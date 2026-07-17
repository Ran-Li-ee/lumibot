"""Load completed agent traces into replay UI models."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .formatters import explain_tool_result
from .models import AgentDependency, AgentReplay, ReplayDataset, ReplayRun, SystemRun, ToolBatch, ToolCallReplay

MISSING_TOOL_RESULT = "Tool result unavailable in trace."
CONTEXT_METADATA_KEYS = {
    "asset_type",
    "date",
    "datetime",
    "end",
    "end_date",
    "length",
    "start",
    "symbol",
    "symbols",
    "ticker",
    "tickers",
    "time",
    "timestep",
    "universe",
}


def discover_trace_files(trace_root: str | Path) -> list[Path]:
    """Discover agent trace JSON files without touching unrelated runtime files."""

    traces_dir = Path(trace_root) / "traces"
    if not traces_dir.exists():
        return []
    return sorted(traces_dir.glob("*/*.json"))


def build_replay_dataset(trace_root: str | Path) -> ReplayDataset:
    """Build a replay dataset from all trace files under a runtime directory."""

    return _build_replay_dataset_for_root(Path(trace_root))


def build_replay_dataset_from_roots(trace_roots: list[str | Path]) -> ReplayDataset:
    """Build one replay dataset by merging all trace files under multiple runtime directories."""

    datasets = [_build_replay_dataset_for_root(Path(trace_root)) for trace_root in trace_roots]
    runs: list[ReplayRun] = []
    warnings: list[Any] = []
    source_paths: list[str] = []
    for dataset in datasets:
        runs.extend(dataset.runs)
        warnings.extend(dataset.warnings)
        if dataset.source_path:
            source_paths.append(dataset.source_path)

    runs.sort(key=lambda run: (run.strategy_name, run.label, run.id))
    return ReplayDataset(runs=runs, source_path="; ".join(source_paths), warnings=warnings)


def _build_replay_dataset_for_root(root: Path) -> ReplayDataset:
    """Build a replay dataset from one runtime directory."""

    root = Path(root)
    agents: list[AgentReplay] = []
    warnings: list[dict[str, str]] = []
    for trace_path in discover_trace_files(root):
        try:
            agents.append(load_agent_trace(trace_path))
        except Exception as exc:
            warnings.append(
                {
                    "kind": "trace_parse_error",
                    "path": str(trace_path.resolve()),
                    "message": str(exc),
                }
            )

    grouped_agents: dict[tuple[str, str, str], list[AgentReplay]] = {}
    for agent in agents:
        grouped_agents.setdefault(_system_run_key(agent), []).append(agent)

    system_runs = [
        _build_system_run(key, group_agents)
        for key, group_agents in sorted(grouped_agents.items(), key=lambda item: item[0])
    ]
    runs = [
        ReplayRun(
            id=run_id,
            label=label,
            strategy_name=strategy_name,
            system_runs=run_systems,
        )
        for (strategy_name, run_id, label), run_systems in _group_system_runs_by_backtest(root, system_runs).items()
    ]
    return ReplayDataset(runs=runs, source_path=str(root.resolve()), warnings=warnings)


def load_agent_trace(path: str | Path) -> AgentReplay:
    """Load a single agent trace JSON file into an ``AgentReplay``."""

    trace_path = Path(path).resolve()
    with trace_path.open("r", encoding="utf-8-sig") as trace_file:
        raw_trace = json.load(trace_file)

    trace = raw_trace if isinstance(raw_trace, dict) else {}
    request = trace.get("request") if isinstance(trace.get("request"), dict) else {}
    runtime_context = request.get("runtime_context") if isinstance(request.get("runtime_context"), dict) else {}
    name = _first_text(trace.get("agent"), runtime_context.get("agent_name"), trace_path.parent.name)
    model = _first_text(trace.get("model"), request.get("model"), "")

    tool_batches = _tool_batches_from_events(trace.get("events"))
    if not tool_batches:
        tool_batches = _tool_batches_from_flat_lists(trace.get("tool_calls"), trace.get("tool_results"))

    return AgentReplay(
        id=_agent_id(name, trace_path),
        name=name,
        model=model,
        trace_path=str(trace_path),
        request=request,
        tool_batches=tool_batches,
        summary=trace.get("summary"),
        warnings=_warnings(trace.get("warnings")),
        raw_trace=raw_trace,
    )


def _agent_id(name: str, trace_path: Path) -> str:
    digest = hashlib.sha1(str(trace_path).encode("utf-8")).hexdigest()[:10]
    return f"{name}-{digest}"


def _run_id(root: Path) -> str:
    digest = hashlib.sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:10]
    return f"trace-root-{digest}"


def _backtest_run_id(root: Path, strategy_name: str) -> str:
    digest = hashlib.sha1(f"{root.resolve()}|{strategy_name}".encode()).hexdigest()[:10]
    return f"{_run_id(root)}-{digest}"


def _build_system_run(key: tuple[str, str, str], agents: list[AgentReplay]) -> SystemRun:
    ordered_agents = sorted(agents, key=_agent_timing_sort_key)
    mode, current_datetime, _strategy_name = key
    return SystemRun(
        id="|".join(key),
        mode=mode,
        current_datetime=current_datetime,
        agents=ordered_agents,
        dependencies=_context_dependencies(ordered_agents),
        name="|".join(key),
    )


def _group_system_runs_by_backtest(
    root: Path,
    system_runs: list[SystemRun],
) -> dict[tuple[str, str, str], list[SystemRun]]:
    grouped: dict[tuple[str, str, str], list[SystemRun]] = {}
    for system_run in system_runs:
        grouped.setdefault(_system_run_backtest_key(root, system_run), []).append(system_run)
    return dict(sorted(grouped.items(), key=lambda item: item[0]))


def _system_run_strategy(system_run: SystemRun) -> str:
    parts = system_run.id.split("|")
    if len(parts) >= 3 and parts[2]:
        return parts[2]
    return "unknown-strategy"


def _system_run_backtest_key(root: Path, system_run: SystemRun) -> tuple[str, str, str]:
    strategy_name = _system_run_strategy(system_run)
    runtime_context = _system_run_runtime_context(system_run)
    explicit_id = _first_text(runtime_context.get("backtest_run_id"), runtime_context.get("backtest_id"))
    if explicit_id:
        label = _first_text(
            runtime_context.get("backtest_run_label"),
            runtime_context.get("backtest_label"),
            explicit_id,
        )
        return strategy_name, explicit_id, label
    return strategy_name, _backtest_run_id(root, strategy_name), _trace_root_label(root)


def _trace_root_label(root: Path) -> str:
    parts = root.parts
    marker = "ai_trading_team_example_benchmarks"
    if marker in parts:
        index = parts.index(marker)
        if len(parts) > index + 2:
            return f"{parts[index + 1]} / {parts[index + 2]}"
    return root.name or "agent_runtime"


def _system_run_runtime_context(system_run: SystemRun) -> dict[str, Any]:
    for agent in system_run.agents:
        request = agent.request if isinstance(agent.request, dict) else {}
        runtime_context = request.get("runtime_context")
        if isinstance(runtime_context, dict):
            return runtime_context
    return {}


def _system_run_key(agent: AgentReplay) -> tuple[str, str, str]:
    runtime_context = agent.request.get("runtime_context") if isinstance(agent.request, dict) else {}
    if not isinstance(runtime_context, dict):
        runtime_context = {}
    return (
        _text_or_default(runtime_context.get("mode"), "unknown"),
        _runtime_current_datetime(agent, runtime_context),
        _text_or_default(runtime_context.get("strategy_name"), "unknown-strategy"),
    )


def _runtime_current_datetime(agent: AgentReplay, runtime_context: dict[str, Any]) -> str:
    current_datetime = runtime_context.get("current_datetime")
    if isinstance(current_datetime, str) and current_datetime:
        return current_datetime

    raw_trace = agent.raw_trace if isinstance(agent.raw_trace, dict) else {}
    timing = raw_trace.get("timing") if isinstance(raw_trace.get("timing"), dict) else {}
    started_at = timing.get("started_at")
    if isinstance(started_at, str) and started_at:
        return started_at
    ended_at = timing.get("ended_at")
    if isinstance(ended_at, str) and ended_at:
        return ended_at
    return f"trace:{hashlib.sha1(agent.trace_path.encode('utf-8')).hexdigest()[:10]}"


def _text_or_default(value: Any, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _agent_timing_sort_key(agent: AgentReplay) -> tuple[str, str, str]:
    raw_trace = agent.raw_trace if isinstance(agent.raw_trace, dict) else {}
    timing = raw_trace.get("timing") if isinstance(raw_trace.get("timing"), dict) else {}
    return (
        _text_or_default(timing.get("started_at"), ""),
        _text_or_default(timing.get("ended_at"), ""),
        agent.name,
    )


def _context_dependencies(agents: list[AgentReplay]) -> list[AgentDependency]:
    dependencies: list[AgentDependency] = []
    for agent in agents:
        context = agent.request.get("context") if isinstance(agent.request, dict) else {}
        if not isinstance(context, dict):
            continue
        for key in sorted(str(context_key) for context_key in context.keys()):
            if key.lower() in CONTEXT_METADATA_KEYS:
                continue
            source_agent, source_status = _dependency_source_from_context_value(context.get(key), agent, agents)
            dependencies.append(
                AgentDependency(
                    source_label=f"context:{key}",
                    target_agent=agent.name,
                    source_type="context",
                    source_agent=source_agent,
                    source_status=source_status,
                )
            )
    return dependencies


def _dependency_source_from_context_value(
    context_value: Any,
    target_agent: AgentReplay,
    agents: list[AgentReplay],
) -> tuple[str | None, str]:
    matches = [
        agent
        for agent in agents
        if agent.id != target_agent.id and _context_value_matches_summary(context_value, agent.summary)
    ]
    if len(matches) == 1:
        return matches[0].name, "resolved_summary"
    if len(matches) > 1:
        return None, "ambiguous_source"
    return None, "unresolved_source"


def _context_value_matches_summary(context_value: Any, summary: Any) -> bool:
    if summary is None:
        return False
    return context_value == summary


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return ""


def _warnings(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _tool_batches_from_events(events: Any) -> list[ToolBatch]:
    if not isinstance(events, list):
        return []

    batches: list[ToolBatch] = []
    current_calls: list[ToolCallReplay] = []
    all_calls: list[ToolCallReplay] = []
    unpaired_calls: list[ToolCallReplay] = []
    result_boundary_seen = False

    for event in events:
        if not isinstance(event, dict):
            continue

        kind = event.get("kind")
        tool_name = _tool_name(event)
        if tool_name is None:
            continue

        if kind == "tool_call":
            if current_calls and result_boundary_seen:
                _append_batch(batches, current_calls)
                current_calls = []
                result_boundary_seen = False

            call = ToolCallReplay(
                tool_name=tool_name,
                arguments=_arguments_payload(event),
                timestamp=_timestamp(event),
            )
            current_calls.append(call)
            all_calls.append(call)
            unpaired_calls.append(call)
        elif kind == "tool_result":
            call = _pop_matching_call(unpaired_calls, tool_name)
            if call is None:
                continue

            result_boundary_seen = True
            raw_result = _result_payload(event)
            error = _result_error(event, raw_result)
            call.raw_result = raw_result
            call.error = error
            call.human_explanation = explain_tool_result(call.tool_name, call.arguments, call.raw_result, call.error)

    if current_calls:
        _append_batch(batches, current_calls)

    _mark_missing_results(all_calls)
    return batches


def _tool_batches_from_flat_lists(tool_calls: Any, tool_results: Any) -> list[ToolBatch]:
    calls = [call for call in _as_list(tool_calls) if isinstance(call, dict) and _tool_name(call) is not None]
    results = [
        result for result in _as_list(tool_results) if isinstance(result, dict) and _tool_name(result) is not None
    ]
    batches: list[ToolBatch] = []

    for call_item in calls:
        tool_name = _tool_name(call_item)
        if tool_name is None:
            continue

        result_item = _pop_matching_result(results, tool_name)
        raw_result = _result_payload(result_item) if result_item is not None else None
        error = _result_error(result_item, raw_result) if result_item is not None else MISSING_TOOL_RESULT
        replay_call = ToolCallReplay(
            tool_name=tool_name,
            arguments=_arguments_payload(call_item),
            raw_result=raw_result,
            error=error,
            human_explanation=explain_tool_result(tool_name, _arguments_payload(call_item), raw_result, error),
            timestamp=_timestamp(call_item),
        )
        _append_batch(batches, [replay_call])

    return batches


def _append_batch(batches: list[ToolBatch], calls: list[ToolCallReplay]) -> None:
    batches.append(ToolBatch(batch_index=len(batches) + 1, calls=list(calls)))


def _mark_missing_results(calls: list[ToolCallReplay]) -> None:
    for call in calls:
        if call.human_explanation is not None:
            continue
        call.raw_result = None
        call.error = MISSING_TOOL_RESULT
        call.human_explanation = explain_tool_result(call.tool_name, call.arguments, call.raw_result, call.error)


def _pop_matching_call(calls: list[ToolCallReplay], tool_name: str) -> ToolCallReplay | None:
    for index, call in enumerate(calls):
        if call.tool_name == tool_name:
            return calls.pop(index)
    if calls:
        return calls.pop(0)
    return None


def _pop_matching_result(results: list[dict[str, Any]], tool_name: str) -> dict[str, Any] | None:
    for index, result in enumerate(results):
        if _tool_name(result) == tool_name:
            return results.pop(index)
    if results:
        return results.pop(0)
    return None


def _tool_name(item: dict[str, Any]) -> str | None:
    name = item.get("tool_name") or item.get("name") or item.get("tool")
    return name if isinstance(name, str) and name else None


def _arguments_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("payload")
    if not isinstance(payload, dict):
        payload = item.get("arguments")
    if not isinstance(payload, dict):
        payload = item.get("args")
    return payload if isinstance(payload, dict) else {}


def _result_payload(item: dict[str, Any] | None) -> Any:
    if item is None:
        return None
    if "payload" in item:
        return item.get("payload")
    if "result" in item:
        return item.get("result")
    return item.get("raw_result")


def _result_error(item: dict[str, Any] | None, raw_result: Any) -> str | None:
    if item is None:
        return MISSING_TOOL_RESULT

    explicit_error = item.get("error")
    result = raw_result if isinstance(raw_result, dict) else {}
    if result.get("tool_error") is True:
        result_error = result.get("error") or result.get("message") or result.get("detail")
        return str(result_error if result_error is not None else explicit_error or "unknown error")

    if explicit_error is not None:
        return str(explicit_error)
    return None


def _timestamp(item: dict[str, Any]) -> str | None:
    timestamp = item.get("timestamp")
    return timestamp if isinstance(timestamp, str) else None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
