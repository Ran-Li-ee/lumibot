import json

from lumibot.components.agents.replay_ui.loader import build_replay_dataset, discover_trace_files, load_agent_trace


def _write_trace(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_agent_trace_extracts_inputs_and_batches(tmp_path):
    trace_path = tmp_path / "agent_runtime" / "traces" / "growth_agent" / "growth.json"
    _write_trace(
        trace_path,
        {
            "agent": "growth_agent",
            "model": "openai/gpt-5.4-mini",
            "request": {
                "base_system_prompt": "Base prompt",
                "user_system_prompt": "Growth system prompt",
                "task_prompt": "Pick the strongest ETF.",
                "context": {"date": "2026-04-07", "universe": ["SPY", "QQQ"]},
                "runtime_context": {
                    "mode": "backtesting",
                    "current_datetime": "2026-04-07T09:30:00-04:00",
                    "strategy_name": "DemoStrategy",
                },
                "tool_surface": [{"name": "market_last_price"}, {"name": "duckdb_query"}],
            },
            "events": [
                {
                    "kind": "tool_call",
                    "tool_name": "market_last_price",
                    "payload": {"symbol": "QQQ"},
                    "timestamp": "2026-04-07T09:30:01-04:00",
                },
                {
                    "kind": "tool_call",
                    "tool_name": "market_last_price",
                    "payload": {"symbol": "SPY"},
                    "timestamp": "2026-04-07T09:30:01-04:00",
                },
                {
                    "kind": "tool_result",
                    "tool_name": "market_last_price",
                    "payload": {"symbol": "QQQ", "price": 110.71},
                    "timestamp": "2026-04-07T09:30:02-04:00",
                },
                {
                    "kind": "tool_result",
                    "tool_name": "market_last_price",
                    "payload": {"symbol": "SPY", "price": 450.5},
                    "timestamp": "2026-04-07T09:30:02-04:00",
                },
                {
                    "kind": "tool_call",
                    "tool_name": "duckdb_query",
                    "payload": {"sql": "SELECT 1"},
                    "timestamp": "2026-04-07T09:30:03-04:00",
                },
                {
                    "kind": "tool_result",
                    "tool_name": "duckdb_query",
                    "payload": {"rows": [{"one": 1}], "row_count": 1},
                    "timestamp": "2026-04-07T09:30:04-04:00",
                },
            ],
            "summary": "RESULT: QQQ is strongest.",
            "warnings": [],
        },
    )

    agent = load_agent_trace(trace_path)

    assert agent.name == "growth_agent"
    assert agent.model == "openai/gpt-5.4-mini"
    assert agent.request["runtime_context"]["current_datetime"] == "2026-04-07T09:30:00-04:00"
    assert len(agent.tool_batches) == 2
    assert [call.tool_name for call in agent.tool_batches[0].calls] == ["market_last_price", "market_last_price"]
    assert agent.tool_batches[0].calls[0].raw_result["price"] == 110.71
    assert agent.tool_batches[1].calls[0].tool_name == "duckdb_query"
    assert "SQL" in agent.tool_batches[1].calls[0].human_explanation


def test_load_agent_trace_accepts_utf8_bom_trace_files(tmp_path):
    trace_path = tmp_path / "agent_runtime" / "traces" / "powershell_agent" / "trace.json"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        json.dumps(
            {
                "agent": "powershell_agent",
                "request": {
                    "runtime_context": {
                        "mode": "backtesting",
                        "current_datetime": "2026-04-07T09:30:00-04:00",
                    },
                },
                "events": [],
                "summary": "RESULT: loaded.",
            }
        ),
        encoding="utf-8-sig",
    )

    agent = load_agent_trace(trace_path)

    assert agent.name == "powershell_agent"
    assert agent.summary == "RESULT: loaded."


def test_load_agent_trace_marks_missing_tool_result(tmp_path):
    trace_path = tmp_path / "agent_runtime" / "traces" / "research" / "missing.json"
    _write_trace(
        trace_path,
        {
            "agent": "research",
            "request": {"runtime_context": {"mode": "backtesting"}},
            "events": [
                {"kind": "tool_call", "tool_name": "market_last_price", "payload": {"symbol": "QQQ"}},
            ],
            "summary": "RESULT: incomplete.",
        },
    )

    agent = load_agent_trace(trace_path)

    assert agent.tool_batches[0].calls[0].raw_result is None
    assert agent.tool_batches[0].calls[0].error == "Tool result unavailable in trace."


def test_load_agent_trace_falls_back_to_flat_tool_calls(tmp_path):
    trace_path = tmp_path / "agent_runtime" / "traces" / "flat_agent" / "flat.json"
    _write_trace(
        trace_path,
        {
            "request": {"runtime_context": {"agent_name": "runtime_flat_agent"}, "model": "openai/gpt-5-mini"},
            "tool_calls": [
                {"tool_name": "market_last_price", "payload": {"symbol": "QQQ"}},
                {"tool_name": "duckdb_query", "arguments": {"sql": "SELECT 1"}},
            ],
            "tool_results": [
                {"tool_name": "market_last_price", "payload": {"symbol": "QQQ", "price": 111.2}},
                {"tool_name": "duckdb_query", "result": {"rows": [{"one": 1}], "row_count": 1}},
            ],
        },
    )

    agent = load_agent_trace(trace_path)

    assert agent.name == "runtime_flat_agent"
    assert agent.model == "openai/gpt-5-mini"
    assert len(agent.tool_batches) == 2
    assert agent.tool_batches[0].calls[0].raw_result["price"] == 111.2
    assert "SQL" in agent.tool_batches[1].calls[0].human_explanation


def test_loader_preserves_event_tool_result_diagnostics(tmp_path):
    trace_path = tmp_path / "agent_runtime" / "traces" / "trader" / "trace.json"
    _write_trace(
        trace_path,
        {
            "agent": "trader",
            "model": "stub",
            "request": {},
            "events": [
                {
                    "kind": "tool_call",
                    "tool_name": "orders_submit_order",
                    "payload": {"symbol": "TIP"},
                },
                {
                    "kind": "tool_result",
                    "tool_name": "orders_submit_order",
                    "payload": {"ok": False},
                    "diagnostics": {"ok": False, "error_type": "ORDER_READINESS_REQUIRED"},
                },
            ],
        },
    )

    replay = load_agent_trace(trace_path)

    call = replay.tool_batches[0].calls[0]
    assert call.diagnostics["error_type"] == "ORDER_READINESS_REQUIRED"


def test_loader_preserves_flat_tool_result_diagnostics(tmp_path):
    trace_path = tmp_path / "agent_runtime" / "traces" / "flat_agent" / "flat.json"
    _write_trace(
        trace_path,
        {
            "agent": "flat_agent",
            "request": {},
            "tool_calls": [{"tool_name": "orders_submit_order", "payload": {"symbol": "TIP"}}],
            "tool_results": [
                {
                    "tool_name": "orders_submit_order",
                    "payload": {"ok": False},
                    "diagnostics": {"ok": False, "error_type": "ORDER_READINESS_REQUIRED"},
                }
            ],
        },
    )

    replay = load_agent_trace(trace_path)

    call = replay.tool_batches[0].calls[0]
    assert call.diagnostics["error_type"] == "ORDER_READINESS_REQUIRED"


def test_discover_trace_files_only_reads_trace_json_files(tmp_path):
    root = tmp_path / "agent_runtime"
    _write_trace(root / "traces" / "growth_agent" / "growth.json", {"agent": "growth_agent"})
    (root / "API.txt").write_text("OPENAI_API_KEY=sk-should-not-be-read", encoding="utf-8")
    (root / "traces" / "growth_agent" / "notes.txt").write_text("not json", encoding="utf-8")

    files = discover_trace_files(root)

    assert files == [root / "traces" / "growth_agent" / "growth.json"]


def test_build_replay_dataset_groups_agents_by_system_run_and_context_dependencies(tmp_path):
    root = tmp_path / "agent_runtime"
    common_runtime = {
        "mode": "backtesting",
        "current_datetime": "2026-04-07T09:30:00-04:00",
        "strategy_name": "DalioDemo",
    }
    _write_trace(
        root / "traces" / "growth_agent" / "growth.json",
        {
            "agent": "growth_agent",
            "request": {
                "context": {"date": "2026-04-07", "universe": ["SPY", "QQQ"]},
                "runtime_context": common_runtime,
                "tool_surface": [],
            },
            "events": [],
            "summary": "Growth summary",
        },
    )
    _write_trace(
        root / "traces" / "trader" / "trader.json",
        {
            "agent": "trader",
            "request": {
                "context": {
                    "growth_summary": "Growth summary",
                    "date": "2026-04-07",
                    "universe": ["SPY", "QQQ"],
                },
                "runtime_context": common_runtime,
                "tool_surface": [],
            },
            "events": [],
            "summary": "Trader summary",
        },
    )

    dataset = build_replay_dataset(root)
    public = dataset.to_public_dict()

    system_runs = public["runs"][0]["system_runs"]
    assert len(system_runs) == 1
    assert [agent["name"] for agent in system_runs[0]["agents"]] == ["growth_agent", "trader"]
    assert system_runs[0]["dependencies"] == [
        {
            "source_label": "context:growth_summary",
            "target_agent": "trader",
            "source_type": "context",
            "source_agent": "growth_agent",
            "source_status": "resolved_summary",
        }
    ]


def test_build_replay_dataset_splits_backtest_runs_by_strategy(tmp_path):
    root = tmp_path / "agent_runtime"
    for strategy_name, current_datetime in (
        ("StrategyA", "2026-04-07T09:30:00-04:00"),
        ("StrategyB", "2026-04-08T09:30:00-04:00"),
    ):
        _write_trace(
            root / "traces" / strategy_name / "trace.json",
            {
                "agent": "trader",
                "request": {
                    "runtime_context": {
                        "mode": "backtesting",
                        "current_datetime": current_datetime,
                        "strategy_name": strategy_name,
                    },
                },
                "events": [],
                "summary": f"{strategy_name} summary",
            },
        )

    public = build_replay_dataset(root).to_public_dict()

    runs = public["runs"]
    assert [run["strategy_name"] for run in runs] == ["StrategyA", "StrategyB"]
    assert [run["label"] for run in runs] == ["agent_runtime", "agent_runtime"]
    assert [len(run["system_runs"]) for run in runs] == [1, 1]
    assert [run["system_runs"][0]["current_datetime"] for run in runs] == [
        "2026-04-07T09:30:00-04:00",
        "2026-04-08T09:30:00-04:00",
    ]


def test_build_replay_dataset_splits_same_strategy_by_backtest_run_id(tmp_path):
    root = tmp_path / "agent_runtime"
    for run_id, label, current_datetime in (
        ("baseline", "Prompt baseline", "2026-04-07T09:30:00-04:00"),
        ("macro-v2", "Added macro agent", "2026-04-08T09:30:00-04:00"),
    ):
        _write_trace(
            root / "traces" / run_id / "trace.json",
            {
                "agent": "trader",
                "request": {
                    "runtime_context": {
                        "mode": "backtesting",
                        "current_datetime": current_datetime,
                        "strategy_name": "StrategyA",
                        "backtest_run_id": run_id,
                        "backtest_run_label": label,
                    },
                },
                "events": [],
                "summary": f"{run_id} summary",
            },
        )

    public = build_replay_dataset(root).to_public_dict()

    runs = public["runs"]
    assert [run["strategy_name"] for run in runs] == ["StrategyA", "StrategyA"]
    assert [run["id"] for run in runs] == ["baseline", "macro-v2"]
    assert [run["label"] for run in runs] == ["Prompt baseline", "Added macro agent"]
    assert [run["system_runs"][0]["current_datetime"] for run in runs] == [
        "2026-04-07T09:30:00-04:00",
        "2026-04-08T09:30:00-04:00",
    ]


def test_build_replay_dataset_marks_ambiguous_summary_sources(tmp_path):
    root = tmp_path / "agent_runtime"
    common_runtime = {
        "mode": "backtesting",
        "current_datetime": "2026-04-07T09:30:00-04:00",
        "strategy_name": "AmbiguousWorkflow",
    }
    for agent_name in ("research_agent", "equity_research_agent"):
        _write_trace(
            root / "traces" / agent_name / "trace.json",
            {
                "agent": agent_name,
                "request": {"context": {}, "runtime_context": common_runtime, "tool_surface": []},
                "events": [],
                "summary": "Shared summary",
            },
        )
    _write_trace(
        root / "traces" / "trader" / "trace.json",
        {
            "agent": "trader",
            "request": {
                "context": {"research_summary": "Shared summary"},
                "runtime_context": common_runtime,
                "tool_surface": [],
            },
            "events": [],
            "summary": "Trader summary",
        },
    )

    public = build_replay_dataset(root).to_public_dict()

    dependency = public["runs"][0]["system_runs"][0]["dependencies"][0]
    assert dependency["source_label"] == "context:research_summary"
    assert dependency["target_agent"] == "trader"
    assert dependency["source_type"] == "context"
    assert dependency["source_status"] == "ambiguous_source"
    assert "source_agent" not in dependency


def test_build_replay_dataset_keeps_ambiguous_untimed_runs_separate(tmp_path):
    root = tmp_path / "agent_runtime"
    _write_trace(
        root / "traces" / "agent_a" / "trace.json",
        {
            "agent": "agent_a",
            "request": {"runtime_context": {"mode": "backtesting"}},
            "timing": {"started_at": "2026-04-07T09:30:00-04:00"},
            "events": [],
        },
    )
    _write_trace(
        root / "traces" / "agent_b" / "trace.json",
        {
            "agent": "agent_b",
            "request": {"runtime_context": {"mode": "backtesting"}},
            "timing": {"started_at": "2026-04-07T09:31:00-04:00"},
            "events": [],
        },
    )

    public = build_replay_dataset(root).to_public_dict()

    system_runs = public["runs"][0]["system_runs"]
    assert len(system_runs) == 2
    assert sorted(run["current_datetime"] for run in system_runs) == [
        "2026-04-07T09:30:00-04:00",
        "2026-04-07T09:31:00-04:00",
    ]


def test_build_replay_dataset_surfaces_trace_parse_errors_as_warnings(tmp_path):
    root = tmp_path / "agent_runtime"
    _write_trace(root / "traces" / "growth_agent" / "growth.json", {"agent": "growth_agent"})
    bad_trace = root / "traces" / "broken_agent" / "broken.json"
    bad_trace.parent.mkdir(parents=True, exist_ok=True)
    bad_trace.write_text("{not json", encoding="utf-8")

    dataset = build_replay_dataset(root)

    assert dataset.warnings[0]["kind"] == "trace_parse_error"
    assert dataset.warnings[0]["path"] == str(bad_trace.resolve())
    assert "Expecting property name" in dataset.warnings[0]["message"]

    public = dataset.to_public_dict()
    assert public["warnings"] == [dataset.warnings[0]]
    assert public["runs"][0]["warnings"] == []
