"""Load completed agent traces into replay UI models."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from .boundary_formatters import summarize_boundary_event
from .formatters import explain_tool_result
from .models import (
    AgentDependency,
    AgentReplay,
    BoundaryEventReplay,
    BoundaryTraceReplay,
    ReplayDataset,
    ReplayRun,
    SystemRun,
    ToolBatch,
    ToolCallReplay,
)

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
REQUEST_RESPONSE_TRANSITIONS = {
    "B01_PROVIDER_TO_LITELLM",
    "B02_LITELLM_TO_ADK",
    "B09_ADK_TO_LITELLM",
    "B10_LITELLM_TO_PROVIDER",
}
LOCAL_TOOL_TRANSITIONS = {
    "B03_ADK_TO_FUNCTION_TOOL",
    "B04_FUNCTION_TOOL_TO_WRAPPER",
    "B05_WRAPPER_TO_PYTHON_TOOL",
    "B06_PYTHON_TOOL_TO_WRAPPER",
    "B07_WRAPPER_TO_FUNCTION_TOOL",
    "B08_FUNCTION_TOOL_TO_ADK",
}
MODEL_STEP_BY_TRANSITION = {
    "B09_ADK_TO_LITELLM": "1",
    "B10_LITELLM_TO_PROVIDER": "2",
    "B01_PROVIDER_TO_LITELLM": "3",
    "B02_LITELLM_TO_ADK": "4",
}
FINAL_STEP_BY_TRANSITION = {
    "B09_ADK_TO_LITELLM": "11",
    "B10_LITELLM_TO_PROVIDER": "12",
    "B01_PROVIDER_TO_LITELLM": "13",
    "B02_LITELLM_TO_ADK": "14",
}
LOCAL_TOOL_STEP_BASE_BY_TRANSITION = {
    "B03_ADK_TO_FUNCTION_TOOL": "5",
    "B04_FUNCTION_TOOL_TO_WRAPPER": "6",
    "B05_WRAPPER_TO_PYTHON_TOOL": "7",
    "B06_PYTHON_TOOL_TO_WRAPPER": "8",
    "B07_WRAPPER_TO_FUNCTION_TOOL": "9",
    "B08_FUNCTION_TOOL_TO_ADK": "10",
}


def discover_trace_files(trace_root: str | Path) -> list[Path]:
    """Discover agent trace JSON files without touching unrelated runtime files."""

    root = Path(trace_root).resolve()
    traces_dir = root / "traces"
    if not traces_dir.exists():
        return []

    trace_files = []
    for trace_path in traces_dir.glob("*/*.json"):
        resolved_trace_path = trace_path.resolve()
        if _is_relative_to(resolved_trace_path, root):
            trace_files.append(resolved_trace_path)
    return sorted(trace_files)


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


def boundary_sidecar_index_for_roots(trace_roots: list[str | Path]) -> dict[str, dict[str, Any]]:
    """Build an event-id keyed index for boundary sidecars under configured trace roots."""

    index: dict[str, dict[str, Any]] = {}
    for trace_root in trace_roots:
        root = Path(trace_root)
        for trace_path in discover_trace_files(root):
            try:
                with trace_path.open("r", encoding="utf-8-sig") as trace_file:
                    trace = json.load(trace_file)
            except Exception:
                continue

            boundary = trace.get("boundary_trace") if isinstance(trace, dict) else None
            raw_events = boundary.get("events") if isinstance(boundary, dict) else None
            if not isinstance(raw_events, list):
                continue

            for event_index, raw_event in enumerate(raw_events):
                if not isinstance(raw_event, dict):
                    continue
                payload_meta = raw_event.get("payload_meta")
                if not isinstance(payload_meta, dict):
                    continue
                sidecar_path = payload_meta.get("sidecar_path")
                if not isinstance(sidecar_path, str) or not sidecar_path:
                    continue

                event_id = _boundary_event_id(trace_path, event_index, raw_event)
                index[event_id] = {
                    "trace_root": root.resolve(),
                    "sidecar_path": sidecar_path,
                    "compression": payload_meta.get("compression"),
                }
    return index


def load_boundary_sidecar_payload(entry: dict[str, Any]) -> Any:
    """Load a JSON sidecar after enforcing trace-root-relative path boundaries."""

    trace_root = Path(entry["trace_root"]).resolve()
    relative = Path(str(entry["sidecar_path"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise FileNotFoundError("invalid sidecar path")

    target = (trace_root / relative).resolve()
    if not _is_relative_to(target, trace_root):
        raise FileNotFoundError("sidecar path escapes trace root")

    data = target.read_bytes()
    if entry.get("compression") == "gzip" or target.suffix == ".gz":
        data = gzip.decompress(data)
    return json.loads(data.decode("utf-8"))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


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
            artifacts=backtest_artifacts_for_trace_root(root),
        )
        for (strategy_name, run_id, label), run_systems in _group_system_runs_by_backtest(root, system_runs).items()
    ]
    return ReplayDataset(runs=runs, source_path=str(root.resolve()), warnings=warnings)


def backtest_artifacts_for_trace_root(trace_root: str | Path) -> dict[str, Any]:
    """Return public metadata for backtest-level output files related to a trace root."""

    artifact_root = backtest_artifact_root(trace_root)
    token = artifact_token_for_root(artifact_root)
    account_curve_path = find_account_curve_report(artifact_root)
    report_path = find_performance_report(artifact_root)
    return {
        "account_curve": {
            "available": account_curve_path is not None,
            "url": f"/artifacts/{token}/account-curve" if account_curve_path is not None else None,
            "reason": None
            if account_curve_path is not None
            else "No native account curve HTML report found for this backtest run.",
        },
        "performance_report": {
            "available": report_path is not None,
            "url": f"/artifacts/{token}/performance-report" if report_path is not None else None,
            "reason": None if report_path is not None else "No tearsheet HTML report found for this backtest run.",
        },
    }


def backtest_artifact_root(trace_root: str | Path) -> Path:
    """Map an agent runtime trace root back to the containing backtest output directory."""

    root = Path(trace_root)
    if root.name == "agent_runtime" and root.parent.name == "cache":
        return root.parent.parent
    return root


def artifact_token_for_root(root: str | Path) -> str:
    """Create a stable opaque URL token for a backtest artifact directory."""

    return hashlib.sha1(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]


def find_performance_report(artifact_root: str | Path) -> Path | None:
    """Find the first local HTML performance report for a backtest output directory."""

    root = Path(artifact_root)
    patterns = ("*tearsheet*.html", "*tear_sheet*.html", "*performance*.html", "*report*.html")
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                return path
    return None


def find_account_curve_report(artifact_root: str | Path) -> Path | None:
    """Find the first native Lumibot account curve HTML report for a backtest output directory."""

    root = Path(artifact_root)
    patterns = ("*account_curve*.html", "*trades*.html")
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                return path
    return None


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
    boundary_trace = _load_boundary_trace(trace, trace_path)

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
        boundary_trace=boundary_trace,
    )


def _load_boundary_trace(trace: dict[str, Any], trace_path: Path) -> BoundaryTraceReplay:
    boundary = trace.get("boundary_trace")
    if not isinstance(boundary, dict):
        return BoundaryTraceReplay()

    raw_events = boundary.get("events")
    if not isinstance(raw_events, list):
        raw_events = []

    events: list[BoundaryEventReplay] = []
    for index, raw_event in enumerate(raw_events):
        if not isinstance(raw_event, dict):
            continue
        events.append(_boundary_event_from_raw(trace_path, index, raw_event))

    return BoundaryTraceReplay(
        available=True,
        schema_version=boundary.get("schema_version") if isinstance(boundary.get("schema_version"), int) else None,
        events=events,
        model_turns=_group_boundary_events(events),
        model_turn_replay=_build_model_turn_replay(events),
        diagnostics=boundary.get("diagnostics") if isinstance(boundary.get("diagnostics"), list) else [],
        message="Boundary trace data is available for this agent run.",
    )


def _boundary_event_from_raw(trace_path: Path, index: int, raw_event: dict[str, Any]) -> BoundaryEventReplay:
    transition = _first_text(raw_event.get("transition"), "UNKNOWN_BOUNDARY")
    event_id = _boundary_event_id(trace_path, index, raw_event)
    payload_meta = raw_event.get("payload_meta") if isinstance(raw_event.get("payload_meta"), dict) else {}
    sidecar = None
    if isinstance(payload_meta.get("sidecar_path"), str) and payload_meta.get("sidecar_path"):
        sidecar = {
            "available": True,
            "event_id": event_id,
            "byte_count": payload_meta.get("byte_count"),
            "compression": payload_meta.get("compression"),
            "sha256": payload_meta.get("sha256"),
        }

    return BoundaryEventReplay(
        id=event_id,
        transition=transition,
        model_turn_id=_optional_text(raw_event.get("model_turn_id")),
        tool_batch_id=_optional_text(raw_event.get("tool_batch_id")),
        call_id=_optional_text(raw_event.get("call_id")),
        call_instance_id=_optional_text(raw_event.get("call_instance_id")),
        status=_optional_text(raw_event.get("status")),
        timestamp=_optional_text(raw_event.get("timestamp")),
        payload=raw_event.get("payload"),
        payload_meta=payload_meta,
        summary=summarize_boundary_event(raw_event),
        sidecar=sidecar,
    )


def _boundary_event_id(trace_path: Path, index: int, raw_event: dict[str, Any]) -> str:
    basis = "|".join(
        [
            str(trace_path.resolve()),
            str(index),
            str(raw_event.get("transition") or ""),
            str(raw_event.get("model_turn_id") or ""),
            str(raw_event.get("tool_batch_id") or ""),
            str(raw_event.get("call_id") or ""),
        ]
    )
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:24]


def _group_boundary_events(events: list[BoundaryEventReplay]) -> list[dict[str, Any]]:
    turns: dict[str, list[BoundaryEventReplay]] = {}
    for event in events:
        turn_id = event.model_turn_id or "unknown-model-turn"
        turns.setdefault(turn_id, []).append(event)

    grouped_turns = []
    for turn_id, turn_events in turns.items():
        grouped_turns.append(
            {
                "model_turn_id": turn_id,
                "request_response_events": [
                    event.id for event in turn_events if event.transition in REQUEST_RESPONSE_TRANSITIONS
                ],
                "tool_batches": _group_boundary_tool_batches(turn_events),
            }
        )
    return grouped_turns


def _group_boundary_tool_batches(events: list[BoundaryEventReplay]) -> list[dict[str, Any]]:
    batches: dict[str, list[BoundaryEventReplay]] = {}
    for event in events:
        if event.transition not in LOCAL_TOOL_TRANSITIONS and not event.call_id:
            continue
        batch_id = event.tool_batch_id or "unknown-tool-batch"
        batches.setdefault(batch_id, []).append(event)

    return [
        {
            "tool_batch_id": batch_id,
            "tool_calls": _group_boundary_tool_calls(batch_events),
        }
        for batch_id, batch_events in batches.items()
    ]


def _group_boundary_tool_calls(events: list[BoundaryEventReplay]) -> list[dict[str, Any]]:
    calls: dict[str, list[BoundaryEventReplay]] = {}
    for event in events:
        call_id = event.call_id or f"unknown-call:{event.id}"
        calls.setdefault(call_id, []).append(event)

    grouped_calls = []
    for call_id, call_events in calls.items():
        grouped_calls.append(
            {
                "call_id": call_id,
                "tool_name": _boundary_tool_name(call_events),
                "events": [event.id for event in call_events],
            }
        )
    return grouped_calls


def _boundary_tool_name(events: list[BoundaryEventReplay]) -> str | None:
    for event in events:
        payload = event.payload
        if not isinstance(payload, dict):
            continue
        for key in ("tool_name", "name", "function_name"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        function = payload.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name:
                return name
    return None


def _build_model_turn_replay(events: list[BoundaryEventReplay]) -> list[dict[str, Any]]:
    turns: dict[str, list[BoundaryEventReplay]] = {}
    for event in events:
        turn_id = event.model_turn_id or "unknown-model-turn"
        turns.setdefault(turn_id, []).append(event)

    replay = []
    for turn_index, (turn_id, turn_events) in enumerate(turns.items(), start=1):
        request_events = [event for event in turn_events if event.transition in REQUEST_RESPONSE_TRANSITIONS]
        tool_events = [event for event in turn_events if event.transition in LOCAL_TOOL_TRANSITIONS]
        is_final_turn = not tool_events and _turn_has_final_text(request_events)
        replay.append(
            {
                "model_turn_id": turn_id,
                "turn_index": turn_index,
                "kind": "final_answer" if is_final_turn else "tool_calling",
                "model_steps": [_model_step(event, is_final_turn=is_final_turn) for event in request_events],
                "tool_calls": _model_turn_tool_calls(tool_events, request_events),
            }
        )
    return replay


def _model_step(event: BoundaryEventReplay, *, is_final_turn: bool) -> dict[str, Any]:
    step_map = FINAL_STEP_BY_TRANSITION if is_final_turn else MODEL_STEP_BY_TRANSITION
    ui_step = step_map.get(event.transition, "?")
    return {
        "ui_step": ui_step,
        "transition": event.transition,
        "label": _step_label(ui_step, event.transition),
        "completeness": _payload_completeness(event),
        "truncated": _payload_truncated(event),
        "event": event.to_public_dict(),
    }


def _model_turn_tool_calls(
    events: list[BoundaryEventReplay],
    request_events: list[BoundaryEventReplay],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[BoundaryEventReplay]] = {}
    for event in events:
        group_key = _call_instance_id(event) or event.call_id or f"event:{event.id}"
        grouped.setdefault(group_key, []).append(event)

    tool_names_by_key = _model_tool_names_by_key(request_events)
    calls = []
    for call_index, (group_key, call_events) in enumerate(grouped.items(), start=1):
        calls.append(
            {
                "call_key": group_key,
                "call_index": call_index,
                "call_id": _first_non_empty([event.call_id for event in call_events]),
                "call_instance_id": _first_non_empty([_call_instance_id(event) for event in call_events]),
                "tool_batch_id": _first_non_empty([event.tool_batch_id for event in call_events]),
                "tool_name": _boundary_tool_name(call_events)
                or _request_tool_name(group_key, call_events, tool_names_by_key)
                or "Unknown tool",
                "steps": [
                    _tool_step(event, call_index)
                    for event in call_events
                    if event.transition in LOCAL_TOOL_STEP_BASE_BY_TRANSITION
                ],
            }
        )
    return calls


def _model_tool_names_by_key(events: list[BoundaryEventReplay]) -> dict[str, str]:
    names: dict[str, str] = {}
    for event in events:
        if event.transition != "B02_LITELLM_TO_ADK":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        for tool_call in payload.get("tool_calls", []):
            if not isinstance(tool_call, dict):
                continue
            name = tool_call.get("name") or tool_call.get("tool_name")
            if not isinstance(name, str) or not name:
                continue
            for key in _tool_call_lookup_keys(tool_call):
                names.setdefault(key, name)
    return names


def _tool_call_lookup_keys(tool_call: dict[str, Any]) -> list[str]:
    keys = []
    for raw_key in (tool_call.get("call_instance_id"), tool_call.get("call_id"), tool_call.get("id")):
        if isinstance(raw_key, str) and raw_key:
            keys.append(raw_key)
    return keys


def _request_tool_name(
    group_key: str,
    events: list[BoundaryEventReplay],
    tool_names_by_key: dict[str, str],
) -> str | None:
    keys = [group_key]
    keys.extend(_call_instance_id(event) for event in events)
    keys.extend(event.call_id for event in events)
    for key in keys:
        if isinstance(key, str) and key in tool_names_by_key:
            return tool_names_by_key[key]
    return None


def _tool_step(event: BoundaryEventReplay, call_index: int) -> dict[str, Any]:
    base = LOCAL_TOOL_STEP_BASE_BY_TRANSITION.get(event.transition, "?")
    ui_step = f"{base}.{call_index}" if base != "?" else "?"
    return {
        "ui_step": ui_step,
        "transition": event.transition,
        "label": _step_label(ui_step, event.transition),
        "completeness": _payload_completeness(event),
        "truncated": _payload_truncated(event),
        "event": event.to_public_dict(),
    }


def _payload_completeness(event: BoundaryEventReplay) -> Any:
    return event.payload_meta.get("semantic_completeness") if isinstance(event.payload_meta, dict) else None


def _payload_truncated(event: BoundaryEventReplay) -> bool:
    return bool(event.payload_meta.get("truncated")) if isinstance(event.payload_meta, dict) else False


def _call_instance_id(event: BoundaryEventReplay) -> str | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    value = payload.get("call_instance_id")
    if isinstance(value, str) and value:
        return value
    return event.call_instance_id


def _first_non_empty(values: list[str | None]) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _turn_has_final_text(events: list[BoundaryEventReplay]) -> bool:
    for event in events:
        if event.transition not in {"B01_PROVIDER_TO_LITELLM", "B02_LITELLM_TO_ADK"}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        serialized = json.dumps(payload, ensure_ascii=False)
        if '"text"' in serialized or "RESULT:" in serialized:
            return True
    return False


def _step_label(ui_step: str, transition: str) -> str:
    return f"{ui_step} {transition.replace('_', ' -> ', 1)}"


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


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
            source_agent, source_status = _dependency_source_from_context_value(
                key,
                context.get(key),
                agent,
                agents,
            )
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
    context_key: str,
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
    fallback_source = _dependency_source_from_context_key(context_key, target_agent, agents)
    if fallback_source:
        return fallback_source, "resolved_context_key"
    return None, "unresolved_source"


def _context_value_matches_summary(context_value: Any, summary: Any) -> bool:
    if summary is None:
        return False
    return context_value == summary


def _dependency_source_from_context_key(
    context_key: str,
    target_agent: AgentReplay,
    agents: list[AgentReplay],
) -> str | None:
    if context_key != "execution_plan":
        return None
    source_candidates = [
        agent
        for agent in agents
        if agent.name == "decision_agent" and agent.id != target_agent.id
    ]
    if len(source_candidates) == 1:
        return source_candidates[0].name
    return None


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
