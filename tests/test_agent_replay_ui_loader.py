import json

from lumibot.components.agents.replay_ui.loader import (
    build_replay_dataset,
    build_replay_dataset_from_roots,
    discover_trace_files,
    load_agent_trace,
)


def _write_trace(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_minimal_system_trace(root, *, strategy_name="DemoStrategy", current_datetime="2026-04-07T09:30:00-04:00"):
    _write_trace(
        root / "traces" / "growth_agent" / "trace.json",
        {
            "agent": "growth_agent",
            "model": "openai/gpt-5-mini",
            "request": {
                "runtime_context": {
                    "mode": "backtesting",
                    "current_datetime": current_datetime,
                    "strategy_name": strategy_name,
                },
            },
            "events": [],
            "summary": "RESULT: done.",
        },
    )


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


def test_loader_accepts_trace_with_boundary_trace_without_changing_legacy_tool_batches(tmp_path):
    legacy_trace_path = tmp_path / "traces" / "growth_agent" / "legacy.json"
    boundary_trace_path = tmp_path / "traces" / "growth_agent" / "boundary.json"
    legacy_payload = {
        "agent": "growth_agent",
        "model": "openai/test",
        "request": {"context": {}, "runtime_context": {}},
        "events": [
            {
                "kind": "tool_call",
                "tool_name": "market_last_price",
                "call_id": "call_A",
                "payload": {"symbol": "QQQ"},
            },
            {
                "kind": "tool_result",
                "tool_name": "market_last_price",
                "call_id": "call_A",
                "payload": {"price": 1},
            },
        ],
        "summary": "done",
    }
    _write_trace(legacy_trace_path, legacy_payload)
    _write_trace(
        boundary_trace_path,
        {
            **legacy_payload,
            "boundary_trace": {
                "schema_version": 1,
                "agent_run_id": "run-1",
                "events": [{"transition": "B03_ADK_TO_FUNCTION_TOOL", "call_id": "call_A"}],
                "diagnostics": [],
            },
        },
    )

    legacy_agent = load_agent_trace(legacy_trace_path)
    boundary_agent = load_agent_trace(boundary_trace_path)

    # Compare every batch and call so boundary metadata cannot duplicate legacy events.
    assert boundary_agent.tool_batches == legacy_agent.tool_batches


def test_loader_groups_boundary_trace_by_turn_batch_and_call_id(tmp_path):
    trace_path = tmp_path / "traces" / "growth_agent" / "trace.json"
    _write_trace(
        trace_path,
        {
            "agent": "growth_agent",
            "model": "openai/test",
            "request": {"context": {}, "runtime_context": {}},
            "events": [],
            "boundary_trace": {
                "schema_version": 1,
                "diagnostics": [],
                "events": [
                    {
                        "transition": "B09_ADK_TO_LITELLM",
                        "model_turn_id": "turn-1",
                        "status": "success",
                        "payload": {"messages": ["request"]},
                        "payload_meta": {"semantic_completeness": "complete"},
                    },
                    {
                        "transition": "B02_LITELLM_TO_ADK",
                        "model_turn_id": "turn-1",
                        "tool_batch_id": "turn-1:batch:0001",
                        "status": "success",
                        "payload": {"function_calls": [{"id": "call-A"}, {"id": "call-B"}]},
                    },
                    {
                        "transition": "B03_ADK_TO_FUNCTION_TOOL",
                        "model_turn_id": "turn-1",
                        "tool_batch_id": "turn-1:batch:0001",
                        "call_id": "call-A",
                        "status": "success",
                        "payload": {"tool_name": "market_last_price", "args": {"symbol": "SPY"}},
                    },
                    {
                        "transition": "B03_ADK_TO_FUNCTION_TOOL",
                        "model_turn_id": "turn-1",
                        "tool_batch_id": "turn-1:batch:0001",
                        "call_id": "call-B",
                        "status": "success",
                        "payload": {"tool_name": "market_last_price", "args": {"symbol": "QQQ"}},
                    },
                    {
                        "transition": "B08_FUNCTION_TOOL_TO_ADK",
                        "model_turn_id": "turn-1",
                        "tool_batch_id": "turn-1:batch:0001",
                        "call_id": "call-A",
                        "status": "success",
                        "payload": {"tool_name": "market_last_price", "result": {"symbol": "SPY"}},
                    },
                    {
                        "transition": "B08_FUNCTION_TOOL_TO_ADK",
                        "model_turn_id": "turn-1",
                        "tool_batch_id": "turn-1:batch:0001",
                        "call_id": "call-B",
                        "status": "success",
                        "payload": {"tool_name": "market_last_price", "result": {"symbol": "QQQ"}},
                    },
                ],
            },
        },
    )

    agent = load_agent_trace(trace_path)
    public = agent.to_public_dict()["boundary_trace"]

    assert public["available"] is True
    assert public["schema_version"] == 1
    assert len(public["events"]) == 6
    assert public["model_turns"][0]["model_turn_id"] == "turn-1"
    assert public["model_turns"][0]["request_response_events"]
    batch = public["model_turns"][0]["tool_batches"][0]
    assert batch["tool_batch_id"] == "turn-1:batch:0001"
    assert [call["call_id"] for call in batch["tool_calls"]] == ["call-A", "call-B"]

    events_by_id = {event["id"]: event for event in public["events"]}
    calls_by_id = {call["call_id"]: call for call in batch["tool_calls"]}
    for call_id, symbol in (("call-A", "SPY"), ("call-B", "QQQ")):
        call_events = [events_by_id[event_id] for event_id in calls_by_id[call_id]["events"]]
        assert [event["call_id"] for event in call_events] == [call_id, call_id]
        assert {event["payload"]["result"]["symbol"] for event in call_events if "result" in event["payload"]} == {
            symbol
        }
        assert all(event["summary"] == {} for event in call_events)


def test_loader_marks_sidecar_backed_boundary_event_without_inlining_payload(tmp_path):
    trace_path = tmp_path / "traces" / "growth_agent" / "trace.json"
    _write_trace(
        trace_path,
        {
            "agent": "growth_agent",
            "model": "openai/test",
            "request": {"context": {}, "runtime_context": {}},
            "events": [],
            "boundary_trace": {
                "schema_version": 1,
                "events": [
                    {
                        "transition": "B10_LITELLM_TO_PROVIDER",
                        "model_turn_id": "turn-1",
                        "payload": {"preview": "small preview only"},
                        "payload_meta": {
                            "sidecar_path": "boundary_payloads/event.json.gz",
                            "semantic_completeness": "complete",
                            "byte_count": 123,
                            "compression": "gzip",
                            "sha256": "abc123",
                        },
                    }
                ],
            },
        },
    )

    public = load_agent_trace(trace_path).to_public_dict()["boundary_trace"]
    event = public["events"][0]

    assert event["payload"] == {"preview": "small preview only"}
    assert event["payload_meta"] == {
        "semantic_completeness": "complete",
        "byte_count": 123,
        "compression": "gzip",
        "sha256": "abc123",
    }
    assert event["sidecar"] == {
        "available": True,
        "event_id": event["id"],
        "byte_count": 123,
        "compression": "gzip",
        "sha256": "abc123",
    }
    assert "boundary_payloads/event.json.gz" not in str(public)


def test_build_replay_dataset_surfaces_backtest_artifact_links(tmp_path):
    backtest_root = tmp_path / "artifacts" / "20260718_101010_000000" / "growth-execution-test"
    runtime_root = backtest_root / "cache" / "agent_runtime"
    _write_minimal_system_trace(runtime_root)
    (backtest_root / "growth_execution_test_account_curve.html").write_text(
        "<html>account curve</html>",
        encoding="utf-8",
    )
    (backtest_root / "DemoStrategy_tearsheet.html").write_text("<html>tearsheet</html>", encoding="utf-8")

    public = build_replay_dataset(runtime_root).to_public_dict()

    artifacts = public["runs"][0]["artifacts"]
    assert artifacts["account_curve"]["available"] is True
    assert artifacts["account_curve"]["url"].startswith("/artifacts/")
    assert artifacts["account_curve"]["url"].endswith("/account-curve")
    assert artifacts["performance_report"]["available"] is True
    assert artifacts["performance_report"]["url"].startswith("/artifacts/")
    assert artifacts["performance_report"]["url"].endswith("/performance-report")


def test_build_replay_dataset_marks_missing_backtest_artifacts_unavailable(tmp_path):
    runtime_root = tmp_path / "cache" / "agent_runtime"
    _write_minimal_system_trace(runtime_root)
    (runtime_root.parent.parent / "stats.csv").write_text(
        "datetime,portfolio_value,cash\n2026-04-07,100000,100000\n",
        encoding="utf-8",
    )

    public = build_replay_dataset(runtime_root).to_public_dict()

    artifacts = public["runs"][0]["artifacts"]
    assert artifacts["account_curve"]["available"] is False
    assert artifacts["account_curve"]["url"] is None
    assert "account curve" in artifacts["account_curve"]["reason"].lower()
    assert artifacts["performance_report"]["available"] is False
    assert artifacts["performance_report"]["url"] is None
    assert "tearsheet" in artifacts["performance_report"]["reason"].lower()


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


def test_build_replay_dataset_resolves_execution_plan_dependency(tmp_path):
    root = tmp_path / "agent_runtime"
    common_runtime = {
        "mode": "backtesting",
        "current_datetime": "2024-09-05T09:30:00-04:00",
        "strategy_name": "GrowthExecutionTest",
    }
    decision_summary = json.dumps(
        {
            "decision": {"type": "buy", "from": "USD", "to": "VNQ"},
            "execution_plan": {
                "schema_version": 1,
                "intent": "enter_position",
                "orders": [
                    {
                        "sequence": 1,
                        "symbol": "VNQ",
                        "side": "buy",
                        "quantity_mode": "max_affordable_after_prior_sells",
                    }
                ],
            },
        }
    )
    execution_plan = {
        "schema_version": 1,
        "intent": "enter_position",
        "orders": [
            {
                "sequence": 1,
                "symbol": "VNQ",
                "side": "buy",
                "quantity_mode": "max_affordable_after_prior_sells",
            }
        ],
    }
    _write_trace(
        root / "traces" / "decision_agent" / "trace.json",
        {
            "agent": "decision_agent",
            "request": {"context": {}, "runtime_context": common_runtime, "tool_surface": []},
            "events": [],
            "summary": decision_summary,
        },
    )
    _write_trace(
        root / "traces" / "execution_agent" / "trace.json",
        {
            "agent": "execution_agent",
            "request": {
                "context": {"execution_plan": execution_plan},
                "runtime_context": common_runtime,
                "tool_surface": [],
            },
            "events": [],
            "summary": "Execution summary",
        },
    )

    public = build_replay_dataset(root).to_public_dict()

    dependencies = public["runs"][0]["system_runs"][0]["dependencies"]
    assert dependencies == [
        {
            "source_label": "context:execution_plan",
            "target_agent": "execution_agent",
            "source_type": "context",
            "source_agent": "decision_agent",
            "source_status": "resolved_context_key",
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


def test_build_replay_dataset_from_roots_merges_multiple_roots(tmp_path):
    first = tmp_path / "first" / "agent_runtime"
    second = tmp_path / "second" / "agent_runtime"
    _write_trace(
        first / "traces" / "one" / "trace.json",
        {
            "agent": "agent_one",
            "request": {
                "runtime_context": {
                    "mode": "backtesting",
                    "current_datetime": "2024-09-05T09:30:00-04:00",
                    "strategy_name": "StrategyOne",
                }
            },
            "events": [],
            "summary": "one",
        },
    )
    _write_trace(
        second / "traces" / "two" / "trace.json",
        {
            "agent": "agent_two",
            "request": {
                "runtime_context": {
                    "mode": "backtesting",
                    "current_datetime": "2024-09-06T09:30:00-04:00",
                    "strategy_name": "StrategyTwo",
                }
            },
            "events": [],
            "summary": "two",
        },
    )

    public = build_replay_dataset_from_roots([first, second]).to_public_dict()

    assert [run["strategy_name"] for run in public["runs"]] == ["StrategyOne", "StrategyTwo"]
    assert "first" in public["source_path"]
    assert "second" in public["source_path"]


def test_benchmark_artifact_trace_root_gets_readable_label(tmp_path):
    root = (
        tmp_path
        / "artifacts"
        / "ai_trading_team_example_benchmarks"
        / "20260717_185016_461584"
        / "growth-execution-test"
        / "cache"
        / "agent_runtime"
    )
    _write_trace(
        root / "traces" / "execution_agent" / "trace.json",
        {
            "agent": "execution_agent",
            "request": {
                "runtime_context": {
                    "mode": "backtesting",
                    "current_datetime": "2024-09-05T09:30:00-04:00",
                    "strategy_name": "AITradingTeamGrowthExecutionTestStrategy",
                }
            },
            "events": [],
            "summary": "execution",
        },
    )

    public = build_replay_dataset(root).to_public_dict()

    assert public["runs"][0]["label"] == "20260717_185016_461584 / growth-execution-test"


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
