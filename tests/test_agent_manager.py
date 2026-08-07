import pytest

from lumibot.components.agents.manager import AgentHandle, AgentManager
from lumibot.components.agents.runtime import GoogleADKRuntime, RuntimeRequest
from lumibot.components.agents.schemas import (
    AgentRunResult,
    AgentTraceEvent,
    BoundTool,
    ToolDefinition,
)


class DummyVars(dict):
    def get(self, key, default=None):
        return super().get(key, default)

    def set(self, key, value):
        self[key] = value


class DummyStrategy:
    name = "DummyStrategy"
    market = "24/7"

    def __init__(self):
        self.vars = DummyVars()
        self.parameters = {}
        self.is_backtesting = False

    def get_datetime(self):
        return None

    def log_message(self, *args, **kwargs):
        return None


class DummyManager:
    def __init__(self):
        self.strategy = DummyStrategy()

    @staticmethod
    def _with_tool_result_cache(tool):
        return tool


def _bound_tool(name):
    return BoundTool(
        name=name,
        description=f"{name} description",
        function=lambda: None,
    )


def _runtime_request(bound_tools):
    return RuntimeRequest(
        agent_name="research_agent",
        model="test-model",
        system_prompt="Agent-specific objective.",
        task_prompt=None,
        context=None,
        runtime_context=None,
        memory_state=None,
        memory_notes=[],
        bound_tools=bound_tools,
    )


@pytest.mark.parametrize(
    "tool_names",
    [
        ["market_load_history_table"],
        ["duckdb_query"],
        ["market_load_history_table", "duckdb_query"],
        [],
        ["account_positions"],
    ],
)
def test_duckdb_sql_guidance_is_not_appended_to_system_prompt(tool_names):
    handle = AgentHandle(
        manager=DummyManager(),
        name="research_agent",
        system_prompt="Agent-specific objective.",
        default_model="test-model",
        runtime=object(),
    )
    bound_tools = [_bound_tool(name) for name in tool_names]

    prompt = handle._compose_system_prompt({"mode": "backtesting"}, bound_tools)

    assert "DUCKDB SQL GUIDANCE" not in prompt
    assert "When querying DuckDB tables" not in prompt
    assert "Date, not datetime" not in prompt


def test_duckdb_sql_guidance_is_not_appended_with_actual_bound_tools_by_default():
    handle = AgentHandle(
        manager=DummyManager(),
        name="research_agent",
        system_prompt="Agent-specific objective.",
        default_model="test-model",
        tools=[lambda: None],
        include_builtin_tools=False,
        runtime=object(),
    )
    handle._bound_tools = [_bound_tool("duckdb_query")]

    prompt = handle._compose_system_prompt({"mode": "backtesting"})

    assert "DUCKDB SQL GUIDANCE" not in prompt


def test_system_prompt_routes_price_history_policy_before_duckdb_query():
    handle = AgentHandle(
        manager=DummyManager(),
        name="research_agent",
        system_prompt="Agent-specific objective.",
        default_model="test-model",
        runtime=object(),
    )
    bound_tools = [
        _bound_tool("market_load_history_table"),
        _bound_tool("market_load_history_tables_summary"),
        _bound_tool("duckdb_query"),
    ]

    prompt = handle._compose_system_prompt({"mode": "backtesting"}, bound_tools)

    prompt_lower = prompt.lower()
    assert "Use DuckDB for time-series analysis when historical tables are available" not in prompt
    assert "price/history tool policy" in prompt_lower
    assert (
        "market_load_history_tables_summary is the default tool for multi-symbol "
        "price-history comparison"
    ) in prompt_lower
    assert "market_load_history_table is targeted single-symbol follow-up" in prompt_lower
    assert "duckdb_query is targeted follow-up only" in prompt_lower


def test_runtime_instruction_does_not_duplicate_price_history_policy():
    request = _runtime_request(
        [
            _bound_tool("market_load_history_table"),
            _bound_tool("market_load_history_tables_summary"),
            _bound_tool("duckdb_query"),
        ]
    )

    instruction = GoogleADKRuntime()._instruction_for(request)

    assert "Use DuckDB for time-series analysis when historical tables are available" not in instruction
    assert "PRICE/HISTORY TOOL POLICY" not in instruction
    assert "market_load_history_tables_summary is the default tool" not in instruction
    assert "market_load_history_table is targeted single-symbol follow-up" not in instruction
    assert "duckdb_query is targeted follow-up only" not in instruction


@pytest.mark.parametrize(
    "bound_tools",
    [
        [],
        [_bound_tool("account_positions")],
        [_bound_tool("market_load_history_table")],
        [_bound_tool("duckdb_query")],
    ],
)
def test_runtime_instruction_omits_computed_summary_guidance_without_matching_tools(bound_tools):
    instruction = GoogleADKRuntime()._instruction_for(_runtime_request(bound_tools))

    assert "Use DuckDB for time-series analysis when historical tables are available" not in instruction
    assert "Use computed summaries from market_load_history_table" not in instruction
    assert "Use duckdb_query only for targeted follow-up analysis" not in instruction


def test_disabled_duckdb_tool_does_not_trigger_guidance():
    def bind_disabled_tool(strategy, manager):
        return BoundTool(
            name="duckdb_query",
            description="Disabled DuckDB tool.",
            function=lambda: None,
            metadata={"disabled": True},
        )

    handle = AgentHandle(
        manager=DummyManager(),
        name="execution_agent",
        system_prompt="Agent-specific objective.",
        default_model="test-model",
        tools=[
            ToolDefinition(
                name="duckdb_query",
                description="Disabled DuckDB tool.",
                binder=bind_disabled_tool,
            )
        ],
        include_builtin_tools=False,
        runtime=object(),
    )

    prompt = handle._compose_system_prompt({"mode": "backtesting"})

    assert handle._ensure_bound_tools() == []
    assert "DUCKDB SQL GUIDANCE" not in prompt


class CaptureRuntime:
    def __init__(self):
        self.request = None

    def run(self, request):
        self.request = request
        return AgentRunResult(
            summary="Captured.",
            model=request.model,
            events=[AgentTraceEvent(kind="text", text="Captured.")],
        )


@pytest.mark.parametrize(
    "tool_name",
    [
        "duckdb_query",
        "account_positions",
    ],
)
def test_agent_run_reuses_bound_tools_for_prompt_runtime_and_cache(
    monkeypatch,
    tool_name,
):
    strategy = DummyStrategy()
    manager = AgentManager(strategy)
    runtime = CaptureRuntime()

    def tool():
        return None

    tool.__name__ = tool_name
    handle = manager.create(
        name=f"{tool_name}_agent",
        system_prompt="Agent-specific objective.",
        model="test-model",
        tools=[tool],
        include_builtin_tools=False,
        _runtime=runtime,
    )
    captured_cache_payloads = []
    monkeypatch.setattr(
        manager,
        "_record_agent_observability",
        lambda **kwargs: captured_cache_payloads.append(kwargs["cache_payload"]),
    )
    monkeypatch.setattr(handle, "_append_memory", lambda result: None)
    monkeypatch.setattr(handle, "_append_run_artifact_summary", lambda result, context: None)
    monkeypatch.setattr(handle, "_log_run_summary", lambda result, context: None)
    ensure_bound_tools = handle._ensure_bound_tools
    bind_calls = 0

    def counted_bound_tools():
        nonlocal bind_calls
        bind_calls += 1
        return ensure_bound_tools()

    monkeypatch.setattr(handle, "_ensure_bound_tools", counted_bound_tools)

    handle.run(task_prompt="Inspect the available evidence.")

    assert bind_calls == 1
    assert runtime.request.bound_tools[0].name == tool_name
    assert len(captured_cache_payloads) == 1
    effective_prompt = captured_cache_payloads[0]["effective_system_prompt"]
    assert runtime.request.system_prompt == effective_prompt
    assert "DUCKDB SQL GUIDANCE" not in effective_prompt


def test_agent_run_reuses_bound_tools_when_replay_cache_hits(monkeypatch):
    strategy = DummyStrategy()
    strategy.is_backtesting = True
    manager = AgentManager(strategy)

    def duckdb_query():
        return None

    handle = manager.create(
        name="cached_duckdb_agent",
        system_prompt="Agent-specific objective.",
        model="test-model",
        tools=[duckdb_query],
        include_builtin_tools=False,
        _runtime=CaptureRuntime(),
    )
    monkeypatch.setattr(
        manager.replay_cache,
        "load",
        lambda cache_key: {
            "summary": "Cached.",
            "model": "test-model",
            "events": [],
            "warnings": [],
        },
    )
    monkeypatch.setattr(manager, "_record_agent_observability", lambda **kwargs: None)
    monkeypatch.setattr(handle, "_append_memory", lambda result: None)
    monkeypatch.setattr(handle, "_append_run_artifact_summary", lambda result, context: None)
    monkeypatch.setattr(handle, "_log_run_summary", lambda result, context: None)
    ensure_bound_tools = handle._ensure_bound_tools
    bind_calls = 0

    def counted_bound_tools():
        nonlocal bind_calls
        bind_calls += 1
        return ensure_bound_tools()

    monkeypatch.setattr(handle, "_ensure_bound_tools", counted_bound_tools)

    result = handle.run(task_prompt="Use cached evidence.")

    assert result.cache_hit is True
    assert bind_calls == 1


def test_agent_handle_uses_default_base_prompt_by_default():
    handle = AgentHandle(
        manager=DummyManager(),
        name="decision_agent",
        system_prompt="Decision prompt.",
        default_model="test-model",
        runtime=object(),
    )

    prompt = handle._compose_system_prompt({"mode": "backtesting"})

    assert "Runtime context is the ground truth" in prompt
    assert "Tool outputs outrank model memory" in prompt
    assert "Decision prompt." in prompt


def test_default_base_prompt_contains_only_global_runtime_rules():
    handle = AgentHandle(
        manager=DummyManager(),
        name="research_agent",
        system_prompt="Research prompt.",
        default_model="test-model",
        runtime=object(),
    )

    prompt = handle._compose_system_prompt({"mode": "backtesting"})
    prompt_lower = prompt.lower()

    for required_phrase in (
        "runtime context is the ground truth",
        "tool outputs outrank model memory",
        "context pruning is a normal runtime mechanism",
        "current simulated datetime",
        "hard wall",
        "do not use future data",
        "Research prompt.",
    ):
        assert required_phrase.lower() in prompt_lower

    for forbidden_phrase in (
        "default investor policy",
        "prefer no trade over a weak trade",
        "load recent price history for any asset",
        "duckdb analysis",
        "do not submit a material equity order until",
        "sec financial/filing tools",
        "position sizing and order execution",
    ):
        assert forbidden_phrase not in prompt_lower


def test_agent_handle_execution_minimal_base_prompt_omits_decision_policy():
    handle = AgentHandle(
        manager=DummyManager(),
        name="execution_agent",
        system_prompt="Execution prompt.",
        default_model="test-model",
        runtime=object(),
        base_system_prompt_mode="execution_minimal",
    )

    prompt = handle._compose_system_prompt({"mode": "backtesting"})

    assert "You are operating as an order execution agent inside LumiBot." in prompt
    assert "Execute only the provided execution_plan." in prompt
    assert "DEFAULT INVESTOR POLICY" not in prompt
    assert "Prefer no trade over a weak trade" not in prompt
    assert "Require a real thesis" not in prompt
    assert "BACKTESTING SAFETY RULES" in prompt
    assert "Execution prompt." in prompt


def test_execution_minimal_base_prompt_contains_no_research_or_history_policy():
    handle = AgentHandle(
        manager=DummyManager(),
        name="execution_agent",
        system_prompt="Execution prompt.",
        default_model="test-model",
        runtime=object(),
        base_system_prompt_mode="execution_minimal",
    )

    prompt = handle._compose_system_prompt({"mode": "backtesting"})
    prompt_lower = prompt.lower()

    for required_phrase in (
        "order execution agent",
        "execute only the provided execution_plan",
        "do not perform investment research",
        "runtime context is the ground truth",
        "current simulated datetime",
        "Execution prompt.",
    ):
        assert required_phrase.lower() in prompt_lower

    for forbidden_phrase in (
        "default investor policy",
        "market_load_history_tables_summary",
        "market_load_history_table",
        "duckdb_query",
        "computed summaries",
        "98% cash rule",
        "cash_buffer_pct",
        "relative-strength",
    ):
        assert forbidden_phrase not in prompt_lower


def test_research_agent_with_history_tools_receives_summary_first_policy():
    def market_load_history_table():
        return None

    def market_load_history_tables_summary():
        return None

    def duckdb_query():
        return None

    handle = AgentHandle(
        manager=DummyManager(),
        name="growth_agent",
        system_prompt="Growth role.",
        default_model="test-model",
        runtime=object(),
        tools=[market_load_history_table, market_load_history_tables_summary, duckdb_query],
        include_builtin_tools=False,
    )

    prompt = handle._compose_system_prompt(
        {"mode": "backtesting"},
        bound_tools=handle._ensure_bound_tools(),
    )
    prompt_lower = prompt.lower()

    assert "price/history tool policy" in prompt_lower
    assert (
        "market_load_history_tables_summary is the default tool for multi-symbol "
        "price-history comparison"
    ) in prompt_lower
    assert "market_load_history_table is targeted single-symbol follow-up" in prompt_lower
    assert "duckdb_query is targeted follow-up only" in prompt_lower
    assert "do not load raw history tables for every symbol" in prompt_lower


def test_execution_agent_with_order_tools_does_not_receive_history_policy():
    def orders_submit_order():
        return None

    def market_last_price():
        return None

    handle = AgentHandle(
        manager=DummyManager(),
        name="execution_agent",
        system_prompt="Execution role.",
        default_model="test-model",
        runtime=object(),
        tools=[orders_submit_order, market_last_price],
        include_builtin_tools=False,
        base_system_prompt_mode="execution_minimal",
    )

    prompt = handle._compose_system_prompt(
        {"mode": "backtesting"},
        bound_tools=handle._ensure_bound_tools(),
    )
    prompt_lower = prompt.lower()

    assert "execution tool policy" in prompt_lower
    assert "orders_submit_order executes explicit order fields from execution_plan.orders" in prompt_lower
    for forbidden_phrase in (
        "price/history tool policy",
        "market_load_history_tables_summary",
        "market_load_history_table",
        "duckdb_query",
        "computed summaries",
    ):
        assert forbidden_phrase not in prompt_lower


def test_agent_manager_create_forwards_base_system_prompt_mode():
    manager = AgentManager(DummyStrategy())

    handle = manager.create(
        name="execution_agent",
        system_prompt="Execution prompt.",
        default_model="test-model",
        _runtime=object(),
        base_system_prompt_mode="execution_minimal",
    )

    assert handle.base_system_prompt_mode == "execution_minimal"
    assert "DEFAULT INVESTOR POLICY" not in handle._compose_system_prompt({"mode": "backtesting"})


def test_agent_handle_rejects_unknown_base_system_prompt_mode():
    with pytest.raises(ValueError, match="Unsupported base_system_prompt_mode"):
        AgentHandle(
            manager=DummyManager(),
            name="bad_agent",
            system_prompt="Prompt.",
            default_model="test-model",
            runtime=object(),
            base_system_prompt_mode="bad",
        )


def test_execution_minimal_mode_skips_memory_thesis_warning_for_position_orders():
    result = AgentRunResult(
        summary="RESULT: sold current holding.",
        model="test-model",
        events=[
            AgentTraceEvent(kind="tool_call", tool_name="account_portfolio", payload={}),
            AgentTraceEvent(
                kind="tool_call",
                tool_name="orders_submit_order",
                payload={"symbol": "VNQ", "side": "sell", "quantity": 10},
            ),
        ],
    )
    runtime_context = {
        "mode": "backtesting",
        "positions": [{"symbol": "VNQ", "quantity": 10}],
    }
    default_handle = AgentHandle(
        manager=DummyManager(),
        name="decision_agent",
        system_prompt="Prompt.",
        default_model="test-model",
        tools=[],
        runtime=object(),
        include_builtin_tools=False,
    )
    execution_handle = AgentHandle(
        manager=DummyManager(),
        name="execution_agent",
        system_prompt="Prompt.",
        default_model="test-model",
        tools=[],
        runtime=object(),
        include_builtin_tools=False,
        base_system_prompt_mode="execution_minimal",
    )

    default_warnings = default_handle._derive_warnings(result, runtime_context)
    execution_warnings = execution_handle._derive_warnings(result, runtime_context)

    assert any(
        warning["kind"] == "position_order_without_memory_thesis"
        for warning in default_warnings
    )
    assert not [
        warning
        for warning in execution_warnings
        if warning["kind"] == "position_order_without_memory_thesis"
    ]
