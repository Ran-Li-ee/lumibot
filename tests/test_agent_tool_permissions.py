import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from lumibot.components.agents import AgentManager, AgentRunResult, AgentTraceEvent, BuiltinTools
from lumibot.components.agents.manager import AgentModelCallLimitExceeded
from lumibot.components.agents.schemas import BoundTool, ToolDefinition


class _Vars(dict):
    def get(self, key, default=None):
        return super().get(key, default)

    def set(self, key, value):
        self[key] = value


class _Strategy:
    is_backtesting = True
    parameters = {}
    vars = _Vars()

    def get_datetime(self):
        return datetime(2026, 1, 2, tzinfo=timezone.utc)

    def log_message(self, *args, **kwargs):
        return None


class _OrderReadinessOrder:
    def __init__(
        self,
        *,
        identifier,
        asset,
        quantity,
        side,
        status="new",
        order_type="market",
        time_in_force="day",
        limit_price=None,
        stop_price=None,
        stop_limit_price=None,
        trail_price=None,
        trail_percent=None,
    ):
        self.identifier = identifier
        self.status = status
        self.side = side
        self.asset = asset
        self.quantity = quantity
        self.order_type = order_type
        self.time_in_force = time_in_force
        self.limit_price = limit_price
        self.stop_price = stop_price
        self.stop_limit_price = stop_limit_price
        self.trail_price = trail_price
        self.trail_percent = trail_percent
        self.avg_fill_price = None
        self.transactions = []

    def is_filled(self):
        return str(self.status).lower() in {"fill", "filled", "cash_settled"}

    def is_active(self):
        return not self.is_filled() and not self.is_canceled()

    def is_canceled(self):
        return str(self.status).lower() in {"cancel", "canceled", "cancelled", "error", "expired", "rejected"}

    def get_fill_price(self):
        return self.avg_fill_price


class _OrderReadinessStrategy(_Strategy):
    def __init__(self):
        self.submitted_orders = []
        self.positions = []
        self.open_orders = []
        self.last_prices = {}
        self.cash = 100000.0
        self.portfolio_value = 100000.0
        self.orders_by_identifier = {}
        self.created_order_count = 0
        self.submitted_order_status = "fill"
        self.get_order_calls = []
        self.get_cash_calls = 0
        self.force_negative_cash_after_submit = False

    def get_positions(self, include_cash_positions=True):
        return list(self.positions)

    def get_orders(self):
        return list(self.open_orders)

    def get_cash(self):
        self.get_cash_calls += 1
        if self.force_negative_cash_after_submit and self.get_cash_calls > 1:
            return -1.0
        return self.cash

    def get_portfolio_value(self):
        return self.portfolio_value

    def get_last_price(self, asset, quote=None, exchange=None):
        return self.last_prices.get(getattr(asset, "symbol", None), 100.0)

    def create_order(self, asset, quantity, side, **kwargs):
        self.created_order_count += 1
        return _OrderReadinessOrder(
            identifier=f"test-order-{self.created_order_count}",
            asset=asset,
            quantity=quantity,
            side=side,
            status="new",
            order_type=kwargs.get("order_type", "market"),
            time_in_force=kwargs.get("time_in_force", "day"),
            limit_price=kwargs.get("limit_price"),
            stop_price=kwargs.get("stop_price"),
            stop_limit_price=kwargs.get("stop_limit_price"),
            trail_price=kwargs.get("trail_price"),
            trail_percent=kwargs.get("trail_percent"),
        )

    def submit_order(self, order):
        order.status = self.submitted_order_status
        order.avg_fill_price = self.last_prices.get(getattr(order.asset, "symbol", None), 100.0)
        self.submitted_orders.append(order)
        self.orders_by_identifier[order.identifier] = order
        return order

    def get_order(self, identifier, broker_refresh=True, broker_refresh_ttl_seconds=0.0):
        self.get_order_calls.append(
            {
                "identifier": identifier,
                "broker_refresh": broker_refresh,
                "broker_refresh_ttl_seconds": broker_refresh_ttl_seconds,
            }
        )
        return self.orders_by_identifier.get(identifier)


def _wrap_builtin_tools(strategy, tool_definitions):
    from lumibot.components.agents.runtime import _wrap_tool_callable

    manager = AgentManager(strategy)
    tools = [definition.binder(strategy, manager) for definition in tool_definitions]
    tool_context = {
        "agent_name": "trader",
        "model_call_id": "test-model-call",
        "enforce_order_readiness": True,
        "tool_calls": [],
    }
    return {tool.name: _wrap_tool_callable(tool, tool_context) for tool in tools}


def _fake_asset(symbol, asset_type="stock"):
    return SimpleNamespace(symbol=symbol, asset_type=asset_type)


def _fake_position(symbol, quantity, market_value=None, current_price=None, asset_type="stock"):
    return SimpleNamespace(
        asset=_fake_asset(symbol, asset_type=asset_type),
        quantity=quantity,
        market_value=market_value,
        current_price=current_price,
        avg_fill_price=None,
    )


def _fake_open_order(symbol, quantity=1, side="buy", status="new", identifier="open-order"):
    active = status not in {"filled", "fill", "canceled", "cancelled", "rejected", "expired"}
    return SimpleNamespace(
        identifier=identifier,
        status=status,
        side=side,
        asset=_fake_asset(symbol),
        quantity=quantity,
        order_type="market",
        time_in_force="day",
        is_active=lambda: active,
        is_filled=lambda: False,
        is_canceled=lambda: False,
    )


def _fake_simple_order(symbol, quantity=1, side="buy", status="filled", identifier="simple-order"):
    return SimpleNamespace(
        identifier=identifier,
        status=status,
        side=side,
        asset=_fake_asset(symbol),
        quantity=quantity,
        order_type="market",
        time_in_force="day",
    )


def _wrap_preflight_and_submit_tools(strategy):
    return _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight(), BuiltinTools.orders.submit()])


def _wrap_preflight_and_submit_confirm_tools(strategy):
    return _wrap_builtin_tools(
        strategy,
        [
            BuiltinTools.orders.preflight(),
            BuiltinTools.orders.submit_and_confirm(),
        ],
    )


def _wrap_execute_order_tools(strategy):
    return _wrap_builtin_tools(strategy, [BuiltinTools.orders.execute()])


class _Runtime:
    last_request = None

    def run(self, request):
        from lumibot.components.agents.runtime import _wrap_tool_callable

        type(self).last_request = request
        tool_context = {"agent_name": request.agent_name, "model_call_id": request.model_call_id}
        tool_map = {tool.name: _wrap_tool_callable(tool, tool_context) for tool in request.bound_tools}
        assert "orders_submit_order" not in tool_map
        assert "remember_decision" not in tool_map
        assert "remember_proposal" in tool_map
        assert "remember_risk_note" in tool_map
        memory_result = tool_map["search_memory"](query="AAPL", limit=1)
        notify_result = tool_map["notify_user"](title="Test", message="Backtest dry run")
        events = [
            AgentTraceEvent(kind="tool_call", tool_name="search_memory", payload={"query": "AAPL"}),
            AgentTraceEvent(kind="tool_result", tool_name="search_memory", payload=memory_result),
            AgentTraceEvent(kind="tool_call", tool_name="notify_user", payload={"title": "Test"}),
            AgentTraceEvent(kind="tool_result", tool_name="notify_user", payload=notify_result),
            AgentTraceEvent(kind="text", text="Research completed without trading tools."),
        ]
        return AgentRunResult(summary="Research completed without trading tools.", model=request.model, events=events)


class _LongSummaryRuntime:
    requests = []

    def run(self, request):
        type(self).requests.append(request)
        summary = "x" * 5000
        return AgentRunResult(
            summary=summary,
            model=request.model,
            events=[AgentTraceEvent(kind="text", text=summary)],
        )


class _CaptureRuntime:
    requests = []

    def run(self, request):
        type(self).requests.append(request)
        return AgentRunResult(
            summary="Captured runtime request.",
            model=request.model,
            events=[AgentTraceEvent(kind="text", text="Captured runtime request.")],
        )


def test_agent_allow_trading_false_removes_only_mutating_order_tools(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    strategy = _Strategy()
    manager = AgentManager(strategy)
    monkeypatch.setenv("FRED_API_KEY", "")

    agent = manager.create(name="researcher", model="openai/gpt-5.4-mini", allow_trading=False)
    tool_names = {tool.name for tool in agent._ensure_bound_tools()}

    assert "orders_submit_order" not in tool_names
    assert "orders_submit_and_confirm_order" not in tool_names
    assert "orders_cancel_order" not in tool_names
    assert "orders_modify_order" not in tool_names
    assert "remember_decision" not in tool_names
    assert "remember_proposal" in tool_names
    assert "remember_risk_note" in tool_names
    assert "orders_open_orders" in tool_names
    assert "account_positions" in tool_names
    assert "get_income_statement" in tool_names
    assert "get_indicator" in tool_names
    assert "list_fred_series" not in tool_names
    assert "get_fred_series" not in tool_names
    assert "get_fred_latest" not in tool_names
    assert "get_fred_snapshot" not in tool_names
    assert agent.default_model == "openai/gpt-5.4-mini"


def test_agent_timeout_options_forward_to_runtime_request(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    _CaptureRuntime.requests = []
    strategy = _Strategy()
    strategy.vars = _Vars()
    strategy.is_backtesting = False
    manager = AgentManager(strategy)
    manager.create(
        name="timed",
        system_prompt="Capture timeouts.",
        model="gemini-3.5-flash",
        tools=[],
        include_builtin_tools=False,
        _runtime=_CaptureRuntime(),
        model_request_timeout_seconds=123,
        run_timeout_seconds=456,
    )

    manager["timed"].run(task_prompt="Use defaults.")
    manager["timed"].run(
        task_prompt="Use overrides.",
        model_request_timeout_seconds=7,
        run_timeout_seconds=8,
    )

    assert _CaptureRuntime.requests[0].model_request_timeout_seconds == 123
    assert _CaptureRuntime.requests[0].run_timeout_seconds == 456
    assert _CaptureRuntime.requests[1].model_request_timeout_seconds == 7
    assert _CaptureRuntime.requests[1].run_timeout_seconds == 8


def test_agent_backtest_keeps_fred_tools_when_fred_api_key_is_set(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test-fred-key")
    strategy = _Strategy()
    manager = AgentManager(strategy)

    agent = manager.create(name="researcher_with_fred", model="openai/gpt-5.4-mini", allow_trading=False)
    tool_names = {tool.name for tool in agent._ensure_bound_tools()}

    assert "list_fred_series" in tool_names
    assert "get_fred_series" in tool_names
    assert "get_fred_latest" in tool_names
    assert "get_fred_snapshot" in tool_names


def test_read_only_tool_result_cache_is_shared_across_agent_handles():
    import inspect

    strategy = _Strategy()
    manager = AgentManager(strategy)
    calls = {"count": 0}

    def binder(strategy, manager):
        def cached_tool(symbol: str) -> dict:
            calls["count"] += 1
            return {"ok": True, "symbol": symbol, "count": calls["count"]}

        return BoundTool(
            name="cached_research_tool",
            description="Cached research tool.",
            function=cached_tool,
            source="builtin",
            metadata={"kind": "fundamentals", "cache_scope": "strategy_day"},
        )

    tool_definition = ToolDefinition(name="cached_research_tool", description="Cached research tool.", binder=binder)
    first = manager.create(name="first", tools=[tool_definition], allow_trading=False)
    second = manager.create(name="second", tools=[tool_definition], allow_trading=False)

    first_tool = next(tool for tool in first._ensure_bound_tools() if tool.name == "cached_research_tool")
    second_tool = next(tool for tool in second._ensure_bound_tools() if tool.name == "cached_research_tool")

    assert "symbol" in inspect.signature(first_tool.function).parameters
    assert first_tool.function(symbol="NVDA")["count"] == 1
    cached = second_tool.function(symbol="NVDA")
    assert cached["count"] == 1
    assert cached["_lumibot_tool_cache"]["hit"] is True
    assert calls["count"] == 1


def test_explicit_tool_scope_can_exclude_default_builtin_tools():
    strategy = _Strategy()
    manager = AgentManager(strategy)

    def binder(strategy, manager):
        def research_only_tool(symbol: str) -> dict:
            return {"symbol": symbol}

        return BoundTool(
            name="research_only_tool",
            description="Research-only test tool.",
            function=research_only_tool,
            source="custom",
        )

    tool_definition = ToolDefinition(name="research_only_tool", description="Research-only test tool.", binder=binder)
    agent = manager.create(
        name="scoped_researcher",
        tools=[tool_definition],
        allow_trading=False,
        include_builtin_tools=False,
    )

    tool_names = {tool.name for tool in agent._ensure_bound_tools()}

    assert tool_names == {"research_only_tool"}


def test_get_filings_future_report_date_does_not_trigger_lookahead_warning():
    strategy = _Strategy()
    manager = AgentManager(strategy)
    agent = manager.create(name="researcher", allow_trading=False)
    result = AgentRunResult(
        summary="RESULT: done",
        model="test",
        events=[
            AgentTraceEvent(
                kind="tool_call",
                tool_name="get_filings",
                payload={"symbol": "NVDA", "as_of": "2026-05-21"},
            ),
            AgentTraceEvent(
                kind="tool_result",
                tool_name="get_filings",
                payload={
                    "filings": [
                        {
                            "filing_date": "2026-05-12",
                            "acceptance_datetime": "2026-05-12T20:42:13.000Z",
                            "report_date": "2026-06-24",
                        }
                    ]
                },
            ),
        ],
    )

    warnings = agent._derive_warnings(
        result,
        {"mode": "backtesting", "current_datetime": "2026-05-21T13:30:00+00:00"},
    )

    assert not [warning for warning in warnings if warning["kind"] == "future_timestamp"]


def test_no_tool_warning_is_skipped_when_agent_has_no_tools():
    strategy = _Strategy()
    manager = AgentManager(strategy)
    agent = manager.create(name="debater", tools=[], include_builtin_tools=False, allow_trading=False)
    result = AgentRunResult(
        summary="RESULT: reasoned from context",
        model="test",
        events=[AgentTraceEvent(kind="text", text="RESULT: reasoned from context")],
    )

    warnings = agent._derive_warnings(
        result,
        {"mode": "backtesting", "current_datetime": "2026-05-21T13:30:00+00:00"},
    )

    assert not [warning for warning in warnings if warning["kind"] == "no_tool_calls"]


def test_order_tool_serialization_handles_uuid_identifiers():
    import json
    from uuid import uuid4

    from lumibot.components.agents.builtins import _order_to_dict

    class _Order:
        identifier = uuid4()
        status = "submitted"
        side = "buy"
        asset = None
        quantity = 1
        order_type = "market"
        time_in_force = "day"
        limit_price = None
        stop_price = None

    payload = _order_to_dict(_Order())

    json.dumps(payload)
    assert isinstance(payload["identifier"], str)


def test_order_submit_tool_records_memory_event(monkeypatch, tmp_path):
    import pandas as pd

    from lumibot.components.agents.builtins import _bind_submit_order
    from lumibot.components.agents.runtime import _wrap_tool_callable
    from lumibot.components.memory import MemoryStore

    class _Asset:
        symbol = "TQQQ"
        asset_type = "stock"

    class _Order:
        identifier = "order-123"
        status = "submitted"
        side = "buy"
        asset = _Asset()
        quantity = 10
        order_type = "market"
        time_in_force = "day"
        limit_price = None
        stop_price = None

    class _OrderStrategy(_Strategy):
        def create_order(self, *args, **kwargs):
            return _Order()

        def submit_order(self, order):
            return order

    strategy = _OrderStrategy()
    strategy.memory = MemoryStore(strategy, root_dir=tmp_path)
    monkeypatch.setattr(
        "lumibot.components.agents.builtins.resolve_asset_and_quote",
        lambda *args, **kwargs: (_Asset(), None),
    )

    tool = _bind_submit_order(strategy, manager=None)
    wrapped = _wrap_tool_callable(tool, {"agent_name": "trader", "model_call_id": "call-order-1"})
    result = wrapped(symbol="TQQQ", quantity=10, side="buy")

    assert result["order"]["identifier"] == "order-123"
    events = pd.read_parquet(strategy.memory.export_artifacts(tmp_path, prefix="order_memory")["memory_events"])
    order_events = events[events["event_type"] == "order.submitted"]
    assert len(order_events) == 1
    assert order_events.iloc[0]["agent_name"] == "trader"
    assert order_events.iloc[0]["model_call_id"] == "call-order-1"


class _ConfirmAsset:
    def __init__(self, symbol="VNQ", asset_type="stock"):
        self.symbol = symbol
        self.asset_type = asset_type


class _ConfirmPosition:
    def __init__(self, symbol, quantity):
        self.asset = _ConfirmAsset(symbol)
        self.quantity = quantity
        self.avg_fill_price = None
        self.market_value = None
        self.pnl = None
        self.pnl_percent = None


class _ConfirmOrder:
    def __init__(self, *, status="new", side="sell", quantity=10, symbol="VNQ"):
        self.identifier = "order-123"
        self.status = status
        self.side = side
        self.asset = _ConfirmAsset(symbol)
        self.quantity = quantity
        self.order_type = "market"
        self.time_in_force = "day"
        self.limit_price = None
        self.stop_price = None
        self.avg_fill_price = None
        self.transactions = []
        self.position_filled = status in {"fill", "filled"}

    def is_filled(self):
        return self.position_filled or str(self.status).lower() in {"fill", "filled", "cash_settled"}

    def is_active(self):
        return not self.is_filled() and not self.is_canceled()

    def is_canceled(self):
        return str(self.status).lower() in {"canceled", "cancelled", "error", "expired", "rejected"}

    def get_fill_price(self):
        return self.avg_fill_price


class _ConfirmBroker:
    IS_BACKTESTING_BROKER = True

    def __init__(self, strategy):
        self.strategy = strategy
        self.process_pending_calls = 0
        self._first_iteration = False

    def process_pending_orders(self, strategy=None):
        self.process_pending_calls += 1
        target_strategy = strategy or self.strategy
        if self.process_pending_calls >= target_strategy.fill_after_pending_calls:
            target_strategy.order.status = "fill"
            target_strategy.order.position_filled = True
            target_strategy.order.avg_fill_price = 97.12
            target_strategy.positions = [_ConfirmPosition("USD", 2000.0)]
            target_strategy.cash = 2000.0


class _ConfirmExecutor:
    def __init__(self, strategy, *, cash_after_queue=2000.0):
        self.strategy = strategy
        self.cash_after_queue = cash_after_queue
        self.process_queue_calls = 0
        self.observed_first_iteration_flags = []

    def process_queue(self):
        self.process_queue_calls += 1
        self.observed_first_iteration_flags.append(
            (
                getattr(self.strategy, "_first_iteration", None),
                getattr(self.strategy.broker, "_first_iteration", None),
            )
        )
        if any(flag is True for flag in self.observed_first_iteration_flags[-1]):
            return
        self.strategy.cash = self.cash_after_queue
        self.strategy.positions = [_ConfirmPosition("USD", self.cash_after_queue)]


class _ConfirmStrategy(_Strategy):
    def __init__(self, *, initial_status="new", fill_after_pending_calls=1):
        self.order = _ConfirmOrder(status=initial_status)
        self.fill_after_pending_calls = fill_after_pending_calls
        self.cash = 1000.0
        self.positions = [_ConfirmPosition("USD", 1000.0), _ConfirmPosition("VNQ", 10.0)]
        self.broker = _ConfirmBroker(self)
        self.get_order_calls = []

    def get_order(self, identifier, broker_refresh=True, broker_refresh_ttl_seconds=0.0):
        self.get_order_calls.append(
            {
                "identifier": identifier,
                "broker_refresh": broker_refresh,
                "broker_refresh_ttl_seconds": broker_refresh_ttl_seconds,
            }
        )
        if identifier == self.order.identifier:
            return self.order
        return None

    def get_orders(self, *args, **kwargs):
        return [self.order] if self.order.is_active() else []

    def get_positions(self, include_cash_positions=True):
        return list(self.positions)

    def get_cash(self):
        return self.cash

    def get_portfolio_value(self):
        return self.cash


def test_order_confirm_tool_confirms_filled_order_after_processing_pending():
    from lumibot.components.agents.builtins import _bind_confirm_order

    strategy = _ConfirmStrategy(initial_status="new", fill_after_pending_calls=1)
    tool = _bind_confirm_order(strategy, manager=None)

    result = tool.function(
        identifier="order-123",
        symbol="VNQ",
        side="sell",
        expected_quantity=10,
        position_before_quantity=10,
        cash_before=1000,
    )

    assert result["identifier"] == "order-123"
    assert result["confirmed"] is True
    assert result["can_continue"] is True
    assert result["confirmation_status"] == "filled"
    assert result["attempt_count"] == 1
    assert result["checks"]["order_found"] is True
    assert result["checks"]["order_filled"] is True
    assert result["checks"]["position_moved_as_expected"] is True
    assert result["checks"]["cash_moved_as_expected"] is True
    assert result["order"]["is_filled"] is True
    assert result["order"]["is_active"] is False
    assert result["order"]["avg_fill_price"] == 97.12
    assert strategy.broker.process_pending_calls == 1
    assert strategy.get_order_calls[0]["broker_refresh"] is True


def test_order_confirm_tool_flushes_backtest_executor_queue_before_cash_checks():
    from lumibot.components.agents.builtins import _bind_confirm_order

    strategy = _ConfirmStrategy(initial_status="new", fill_after_pending_calls=1)
    strategy._executor_instance = _ConfirmExecutor(strategy, cash_after_queue=1971.2)

    def fill_order_but_leave_cash_stale(strategy=None):
        target_strategy = strategy or outer_strategy
        target_strategy.broker.process_pending_calls += 1
        target_strategy.order.status = "fill"
        target_strategy.order.position_filled = True
        target_strategy.order.avg_fill_price = 97.12
        target_strategy.positions = [_ConfirmPosition("USD", 1000.0)]

    outer_strategy = strategy
    strategy.broker.process_pending_orders = fill_order_but_leave_cash_stale
    tool = _bind_confirm_order(strategy, manager=None)

    result = tool.function(
        identifier="order-123",
        symbol="VNQ",
        side="sell",
        expected_quantity=10,
        position_before_quantity=10,
        cash_before=1000,
    )

    assert result["confirmed"] is True
    assert result["can_continue"] is True
    assert result["checks"]["position_moved_as_expected"] is True
    assert result["checks"]["cash_moved_as_expected"] is True
    assert result["account_snapshot"]["cash"] == 1971.2
    assert strategy._executor_instance.process_queue_calls == 1


def test_order_confirm_tool_flushes_current_order_events_during_first_iteration():
    from lumibot.components.agents.builtins import _bind_confirm_order

    strategy = _ConfirmStrategy(initial_status="new", fill_after_pending_calls=1)
    strategy._first_iteration = True
    strategy.broker._first_iteration = True
    strategy._executor_instance = _ConfirmExecutor(strategy, cash_after_queue=1971.2)

    def fill_order_but_leave_cash_stale(strategy=None):
        target_strategy = strategy or outer_strategy
        target_strategy.broker.process_pending_calls += 1
        target_strategy.order.status = "fill"
        target_strategy.order.position_filled = True
        target_strategy.order.avg_fill_price = 97.12
        target_strategy.positions = [_ConfirmPosition("USD", 1000.0)]

    outer_strategy = strategy
    strategy.broker.process_pending_orders = fill_order_but_leave_cash_stale
    tool = _bind_confirm_order(strategy, manager=None)

    result = tool.function(
        identifier="order-123",
        symbol="VNQ",
        side="sell",
        expected_quantity=10,
        position_before_quantity=10,
        cash_before=1000,
    )

    assert result["confirmed"] is True
    assert result["can_continue"] is True
    assert result["account_snapshot"]["cash"] == 1971.2
    assert strategy._first_iteration is True
    assert strategy.broker._first_iteration is True
    assert strategy._executor_instance.observed_first_iteration_flags == [(False, False)]


def test_order_confirm_tool_returns_open_after_retries_and_caps_attempts():
    from lumibot.components.agents.builtins import _bind_confirm_order

    strategy = _ConfirmStrategy(initial_status="new", fill_after_pending_calls=99)
    tool = _bind_confirm_order(strategy, manager=None)

    result = tool.function(
        identifier="order-123",
        symbol="VNQ",
        side="sell",
        expected_quantity=10,
        position_before_quantity=10,
        cash_before=1000,
        max_attempts=99,
        wait_seconds=0,
    )

    assert result["confirmed"] is False
    assert result["can_continue"] is False
    assert result["confirmation_status"] == "open_after_retries"
    assert result["attempt_count"] == 5
    assert len(result["attempts"]) == 5
    assert result["checks"]["order_found"] is True
    assert result["checks"]["order_filled"] is False
    assert "remained active" in result["warnings"][0]


def test_order_confirm_tool_returns_terminal_rejected_failure():
    from lumibot.components.agents.builtins import _bind_confirm_order

    strategy = _ConfirmStrategy(initial_status="rejected", fill_after_pending_calls=99)
    tool = _bind_confirm_order(strategy, manager=None)

    result = tool.function(identifier="order-123", symbol="VNQ", side="sell", expected_quantity=10)

    assert result["confirmed"] is False
    assert result["can_continue"] is False
    assert result["confirmation_status"] == "rejected"
    assert result["attempt_count"] == 1
    assert "terminal status" in result["warnings"][0]


def test_order_confirm_tool_returns_partial_fill_blocker_with_payload_shape():
    from lumibot.components.agents.builtins import _bind_confirm_order

    strategy = _ConfirmStrategy(initial_status="partial_fill", fill_after_pending_calls=99)
    tool = _bind_confirm_order(strategy, manager=None)

    result = tool.function(
        identifier="order-123",
        symbol="VNQ",
        side="sell",
        expected_quantity=10,
        position_before_quantity=10,
        cash_before=1000,
    )

    assert result["identifier"] == "order-123"
    assert result["confirmed"] is False
    assert result["can_continue"] is False
    assert result["confirmation_status"] == "partially_filled"
    assert result["attempt_count"] == 1
    assert result["attempts"] == [
        {
            "attempt": 1,
            "processed_pending_orders": True,
            "status": "partially_filled",
            "is_active": True,
            "is_filled": False,
        }
    ]
    assert result["checks"]["order_found"] is True
    assert result["checks"]["order_filled"] is False
    assert result["order"]["identifier"] == "order-123"
    assert result["order"]["status"] == "partial_fill"
    assert result["order"]["side"] == "sell"
    assert result["order"]["asset"] == {
        "symbol": "VNQ",
        "asset_type": "stock",
        "expiration": None,
        "strike": None,
        "right": None,
        "multiplier": None,
    }
    assert result["order"]["quantity"] == 10.0
    assert result["order"]["filled_quantity"] is None
    assert result["order"]["is_active"] is True
    assert result["order"]["is_filled"] is False
    assert result["order"]["is_canceled"] is False
    assert result["account_snapshot"]["cash"] == 1000.0
    assert result["account_snapshot"]["portfolio_value"] == 1000.0
    assert result["account_snapshot"]["positions"][1]["asset"]["symbol"] == "VNQ"
    assert result["account_snapshot"]["positions"][1]["quantity"] == 10.0
    assert "partially filled" in result["warnings"][0]


def test_order_confirm_tool_returns_not_found_for_unknown_identifier():
    from lumibot.components.agents.builtins import _bind_confirm_order

    strategy = _ConfirmStrategy(initial_status="new")
    tool = _bind_confirm_order(strategy, manager=None)

    result = tool.function(identifier="missing-order", symbol="VNQ", side="sell", expected_quantity=10)

    assert result["confirmed"] is False
    assert result["can_continue"] is False
    assert result["confirmation_status"] == "not_found"
    assert result["checks"]["order_found"] is False
    assert "not found" in result["warnings"][0].lower()


def test_builtin_order_tools_expose_explicit_confirm_definition():
    tool = BuiltinTools.orders.confirm()

    assert tool.name == "orders_confirm_order"
    assert "Confirm" in tool.description
    assert callable(tool.binder)


def test_builtin_order_tools_expose_preflight_definition():
    tool = BuiltinTools.orders.preflight()

    assert tool.name == "orders_preflight_check"
    assert "Inspect whether one explicit execution_plan order appears ready" in tool.description
    assert callable(tool.binder)


def test_builtin_order_tools_expose_submit_and_confirm_definition():
    tool = BuiltinTools.orders.submit_and_confirm()

    assert tool.name == "orders_submit_and_confirm_order"
    assert "Submit one explicit execution_plan order and confirm" in tool.description
    assert "orders_preflight_check returns can_submit=true" in tool.description
    assert callable(tool.binder)


def test_builtin_order_tools_expose_execute_order_definition():
    tool = BuiltinTools.orders.execute()

    assert tool.name == "orders_execute_order"
    assert "Execute exactly one explicit execution_plan order" in tool.description
    assert "readiness" in tool.description
    assert "confirms" in tool.description
    assert "execute a full plan" in tool.description
    assert callable(tool.binder)


def test_submit_order_description_mentions_preflight_readiness_path():
    strategy = _Strategy()
    manager = AgentManager(strategy)

    tool = BuiltinTools.orders.submit().binder(strategy, manager)

    assert "inspect readiness" in tool.description
    assert "Prefer orders_preflight_check when available" in tool.description
    assert "ORDER_READINESS_REQUIRED" in tool.description


def test_bound_preflight_description_explains_read_only_contract():
    strategy = _OrderReadinessStrategy()
    manager = AgentManager(strategy)

    tool = BuiltinTools.orders.preflight().binder(strategy, manager)

    assert "cash" in tool.description
    assert "portfolio value" in tool.description
    assert "current position" in tool.description
    assert "open orders" in tool.description
    assert "latest price" in tool.description
    assert "does not submit, cancel, modify, or confirm orders" in tool.description


def test_builtin_tools_all_includes_orders_preflight_check():
    assert "orders_preflight_check" in {tool.name for tool in BuiltinTools.all()}


def test_builtin_tools_all_includes_orders_submit_and_confirm_order():
    assert "orders_submit_and_confirm_order" in {tool.name for tool in BuiltinTools.all()}


def test_builtin_tools_all_includes_orders_execute_order():
    assert "orders_execute_order" in {tool.name for tool in BuiltinTools.all()}


def test_bound_submit_and_confirm_metadata_marks_mutating_and_replayable():
    strategy = _OrderReadinessStrategy()
    manager = AgentManager(strategy)

    definition = BuiltinTools.orders.submit_and_confirm()
    tool = definition.binder(strategy, manager)

    assert definition.metadata["mutates_trading"] is True
    assert tool.metadata["kind"] == "builtin"
    assert tool.metadata["replay_on_cache"] is True
    assert tool.metadata["mutates_trading"] is True


def test_bound_execute_order_metadata_marks_mutating_and_replayable():
    strategy = _OrderReadinessStrategy()
    manager = AgentManager(strategy)

    definition = BuiltinTools.orders.execute()
    tool = definition.binder(strategy, manager)

    assert definition.metadata["mutates_trading"] is True
    assert tool.metadata["kind"] == "builtin"
    assert tool.metadata["replay_on_cache"] is True
    assert tool.metadata["mutates_trading"] is True


def test_orders_preflight_check_ready_buy_returns_structured_snapshot():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 1000.0
    strategy.portfolio_value = 1200.0
    strategy.last_prices = {"SPY": 100.0}
    strategy.positions = [_fake_position("SPY", 2, market_value=200.0, current_price=100.0)]
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight()])

    result = tool_map["orders_preflight_check"](
        sequence=1,
        symbol="SPY",
        quantity=3,
        side="buy",
        asset_type="stock",
        order_type="market",
        time_in_force="day",
    )

    assert result["readiness"] == "ready"
    assert result["can_submit"] is True
    assert result["blockers"] == []
    assert result["account"]["cash"] == 1000.0
    assert result["account"]["portfolio_value"] == 1200.0
    assert result["position"]["quantity"] == 2.0
    assert result["price"]["last_price"] == 100.0
    assert result["estimate"]["estimated_order_value"] == 300.0
    assert result["estimate"]["estimated_cash_after_order"] == 700.0
    assert result["estimate"]["estimated_position_after_order"] == 5.0
    assert result["open_orders"]["same_symbol_count"] == 0
    assert set(result["internal_checks"]) == {
        "account_portfolio",
        "account_positions",
        "orders_open_orders",
        "market_last_price",
    }
    assert strategy.submitted_orders == []


def test_orders_preflight_check_blocks_buy_with_insufficient_cash():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 250.0
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight()])

    result = tool_map["orders_preflight_check"](symbol="SPY", quantity=3, side="buy")

    assert result["readiness"] == "blocked"
    assert result["can_submit"] is False
    assert "INSUFFICIENT_CASH_ESTIMATE" in {blocker["code"] for blocker in result["blockers"]}
    assert result["estimate"]["estimated_order_value"] == 300.0
    assert strategy.submitted_orders == []


def test_orders_preflight_check_ready_sell_with_sufficient_position():
    strategy = _OrderReadinessStrategy()
    strategy.positions = [_fake_position("VNQ", 10, market_value=800.0, current_price=80.0)]
    strategy.last_prices = {"VNQ": 80.0}
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight()])

    result = tool_map["orders_preflight_check"](symbol="VNQ", quantity=4, side="sell")

    assert result["readiness"] == "ready"
    assert result["can_submit"] is True
    assert result["estimate"]["estimated_cash_after_order"] == 100320.0
    assert result["estimate"]["estimated_position_after_order"] == 6.0


def test_orders_preflight_check_blocks_sell_with_insufficient_position():
    strategy = _OrderReadinessStrategy()
    strategy.positions = [_fake_position("VNQ", 3, market_value=240.0, current_price=80.0)]
    strategy.last_prices = {"VNQ": 80.0}
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight()])

    result = tool_map["orders_preflight_check"](symbol="VNQ", quantity=4, side="sell")

    assert result["readiness"] == "blocked"
    assert result["can_submit"] is False
    assert "INSUFFICIENT_POSITION" in {blocker["code"] for blocker in result["blockers"]}


def test_orders_preflight_check_does_not_use_option_position_for_stock_sell():
    strategy = _OrderReadinessStrategy()
    strategy.positions = [_fake_position("SPY", 10, asset_type="option")]
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight()])

    result = tool_map["orders_preflight_check"](
        symbol="SPY",
        quantity=5,
        side="sell",
        asset_type="stock",
    )

    assert result["readiness"] == "blocked"
    assert result["can_submit"] is False
    assert "INSUFFICIENT_POSITION" in {blocker["code"] for blocker in result["blockers"]}
    assert result["position"]["quantity"] == 0.0


def test_orders_preflight_check_blocks_invalid_quantity():
    strategy = _OrderReadinessStrategy()
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight()])

    result = tool_map["orders_preflight_check"](symbol="SPY", quantity=0, side="buy")

    assert result["readiness"] == "blocked"
    assert result["can_submit"] is False
    assert "INVALID_QUANTITY" in {blocker["code"] for blocker in result["blockers"]}


def test_orders_preflight_check_blocks_invalid_side():
    strategy = _OrderReadinessStrategy()
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight()])

    result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="hold")

    assert result["readiness"] == "blocked"
    assert result["can_submit"] is False
    assert "INVALID_SIDE" in {blocker["code"] for blocker in result["blockers"]}


def test_orders_preflight_check_blocks_unsupported_order_type():
    strategy = _OrderReadinessStrategy()
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight()])

    result = tool_map["orders_preflight_check"](
        symbol="SPY",
        quantity=1,
        side="buy",
        order_type="limit",
    )

    assert result["readiness"] == "blocked"
    assert result["can_submit"] is False
    assert "UNSUPPORTED_ORDER_TYPE" in {blocker["code"] for blocker in result["blockers"]}


def test_orders_preflight_check_blocks_same_symbol_open_order():
    strategy = _OrderReadinessStrategy()
    strategy.open_orders = [_fake_open_order("SPY")]
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight()])

    result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="buy")

    assert result["readiness"] == "blocked"
    assert result["can_submit"] is False
    assert "OPEN_ORDER_CONFLICT" in {blocker["code"] for blocker in result["blockers"]}
    assert result["open_orders"]["same_symbol_count"] == 1


def test_orders_preflight_check_ignores_closed_same_symbol_orders():
    strategy = _OrderReadinessStrategy()
    strategy.open_orders = [
        _fake_open_order("SPY", status="filled", identifier="filled-helper-order"),
        _fake_simple_order("SPY", status="canceled", identifier="canceled-simple-order"),
        _fake_simple_order("SPY", status="fill", identifier="filled-simple-order"),
    ]
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight()])

    result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="buy")

    assert result["readiness"] == "ready"
    assert result["can_submit"] is True
    assert result["blockers"] == []
    assert result["open_orders"]["count"] == 0
    assert result["open_orders"]["same_symbol_count"] == 0


def test_orders_preflight_check_blocks_when_price_unavailable():
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"SPY": None}
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight()])

    result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="buy")

    assert result["readiness"] == "blocked"
    assert result["can_submit"] is False
    assert "PRICE_UNAVAILABLE" in {blocker["code"] for blocker in result["blockers"]}


def test_orders_preflight_check_blocks_when_account_unavailable_and_does_not_record_readiness():
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"SPY": 100.0}

    def fail_get_cash():
        raise RuntimeError("cash read failed")

    strategy.get_cash = fail_get_cash
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight(), BuiltinTools.orders.submit()])

    result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="buy")
    submit_result = tool_map["orders_submit_order"](symbol="SPY", quantity=1, side="buy")

    assert result["readiness"] == "blocked"
    assert result["can_submit"] is False
    assert "ACCOUNT_UNAVAILABLE" in {blocker["code"] for blocker in result["blockers"]}
    assert submit_result["tool_error"] is True
    assert "ORDER_READINESS_REQUIRED" in submit_result["error"]["message"]
    assert strategy.submitted_orders == []


def test_orders_preflight_check_blocks_when_positions_unavailable_and_does_not_record_readiness():
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"SPY": 100.0}

    def fail_get_positions(include_cash_positions=True):
        raise RuntimeError("positions read failed")

    strategy.get_positions = fail_get_positions
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight(), BuiltinTools.orders.submit()])

    result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="buy")
    submit_result = tool_map["orders_submit_order"](symbol="SPY", quantity=1, side="buy")

    assert result["readiness"] == "blocked"
    assert result["can_submit"] is False
    assert "POSITIONS_UNAVAILABLE" in {blocker["code"] for blocker in result["blockers"]}
    assert submit_result["tool_error"] is True
    assert "ORDER_READINESS_REQUIRED" in submit_result["error"]["message"]
    assert strategy.submitted_orders == []


def test_orders_preflight_check_blocks_when_open_orders_unavailable_and_does_not_record_readiness():
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"SPY": 100.0}

    def fail_get_orders():
        raise RuntimeError("open orders read failed")

    strategy.get_orders = fail_get_orders
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight(), BuiltinTools.orders.submit()])

    result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="buy")
    submit_result = tool_map["orders_submit_order"](symbol="SPY", quantity=1, side="buy")

    assert result["readiness"] == "blocked"
    assert result["can_submit"] is False
    assert "OPEN_ORDERS_UNAVAILABLE" in {blocker["code"] for blocker in result["blockers"]}
    assert submit_result["tool_error"] is True
    assert "ORDER_READINESS_REQUIRED" in submit_result["error"]["message"]
    assert strategy.submitted_orders == []


def test_orders_preflight_check_authorizes_same_exact_submit_order():
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_preflight_and_submit_tools(strategy)

    preflight_result = tool_map["orders_preflight_check"](
        symbol="SPY",
        quantity=1,
        side="buy",
        asset_type="stock",
        order_type="market",
        time_in_force="day",
    )
    submit_result = tool_map["orders_submit_order"](
        symbol="SPY",
        quantity=1.0,
        side="buy",
        asset_type="stock",
        order_type="market",
        time_in_force="day",
    )

    assert preflight_result["can_submit"] is True
    assert "order" in submit_result
    assert submit_result.get("tool_error") is not True
    assert len(strategy.submitted_orders) == 1


def test_orders_preflight_check_authorizes_only_one_matching_submit_order():
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_preflight_and_submit_tools(strategy)

    preflight_result = tool_map["orders_preflight_check"](
        symbol="SPY",
        quantity=1,
        side="buy",
        asset_type="stock",
        order_type="market",
        time_in_force="day",
    )
    first_submit = tool_map["orders_submit_order"](
        symbol="SPY",
        quantity=1,
        side="buy",
        asset_type="stock",
        order_type="market",
        time_in_force="day",
    )
    second_submit = tool_map["orders_submit_order"](
        symbol="SPY",
        quantity=1,
        side="buy",
        asset_type="stock",
        order_type="market",
        time_in_force="day",
    )

    assert preflight_result["can_submit"] is True
    assert "order" in first_submit
    assert second_submit["tool_error"] is True
    assert "ORDER_READINESS_REQUIRED" in second_submit["error"]["message"]
    assert len(strategy.submitted_orders) == 1


def test_orders_preflight_check_does_not_authorize_different_quantity_submit():
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_preflight_and_submit_tools(strategy)

    preflight_result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="buy")
    submit_result = tool_map["orders_submit_order"](symbol="SPY", quantity=2, side="buy")

    assert preflight_result["can_submit"] is True
    assert submit_result["tool_error"] is True
    assert "ORDER_READINESS_REQUIRED" in submit_result["error"]["message"]
    assert strategy.submitted_orders == []


def test_agent_order_tool_rejects_after_preflight_for_different_symbol():
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"SPY": 100.0, "QQQ": 200.0}
    tool_map = _wrap_preflight_and_submit_tools(strategy)

    preflight_result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="buy")
    submit_result = tool_map["orders_submit_order"](symbol="QQQ", quantity=1, side="buy")

    assert preflight_result["can_submit"] is True
    assert submit_result["tool_error"] is True
    assert "ORDER_READINESS_REQUIRED" in submit_result["error"]["message"]
    assert strategy.submitted_orders == []


def test_orders_preflight_check_does_not_authorize_different_side_submit():
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_preflight_and_submit_tools(strategy)

    preflight_result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="buy")
    submit_result = tool_map["orders_submit_order"](symbol="SPY", quantity=1, side="sell")

    assert preflight_result["can_submit"] is True
    assert submit_result["tool_error"] is True
    assert "ORDER_READINESS_REQUIRED" in submit_result["error"]["message"]
    assert strategy.submitted_orders == []


def test_orders_preflight_check_does_not_authorize_different_order_type_or_time_in_force_submit():
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_preflight_and_submit_tools(strategy)

    preflight_result = tool_map["orders_preflight_check"](
        symbol="SPY",
        quantity=1,
        side="buy",
        order_type="market",
        time_in_force="day",
    )
    limit_result = tool_map["orders_submit_order"](
        symbol="SPY",
        quantity=1,
        side="buy",
        order_type="limit",
        limit_price=101.0,
        time_in_force="day",
    )
    gtc_result = tool_map["orders_submit_order"](
        symbol="SPY",
        quantity=1,
        side="buy",
        order_type="market",
        time_in_force="gtc",
    )

    assert preflight_result["can_submit"] is True
    assert limit_result["tool_error"] is True
    assert "ORDER_READINESS_REQUIRED" in limit_result["error"]["message"]
    assert gtc_result["tool_error"] is True
    assert "ORDER_READINESS_REQUIRED" in gtc_result["error"]["message"]
    assert strategy.submitted_orders == []


def test_orders_preflight_check_blocked_result_does_not_authorize_submit():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 50.0
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_preflight_and_submit_tools(strategy)

    preflight_result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="buy")
    submit_result = tool_map["orders_submit_order"](symbol="SPY", quantity=1, side="buy")

    assert preflight_result["readiness"] == "blocked"
    assert "INSUFFICIENT_CASH_ESTIMATE" in {blocker["code"] for blocker in preflight_result["blockers"]}
    assert submit_result["tool_error"] is True
    assert "ORDER_READINESS_REQUIRED" in submit_result["error"]["message"]
    assert strategy.submitted_orders == []


def test_orders_submit_and_confirm_order_submits_and_confirms_after_matching_preflight():
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_preflight_and_submit_confirm_tools(strategy)

    preflight_result = tool_map["orders_preflight_check"](
        sequence=1,
        symbol="SPY",
        quantity=2,
        side="buy",
        asset_type="stock",
        order_type="market",
        time_in_force="day",
    )
    result = tool_map["orders_submit_and_confirm_order"](
        sequence=1,
        symbol="SPY",
        quantity=2.0,
        side="buy",
        asset_type="stock",
        order_type="market",
        time_in_force="day",
    )

    assert preflight_result["can_submit"] is True
    assert result["submitted"] is True
    assert result["confirmed"] is True
    assert result["can_continue"] is True
    assert result["identifier"] == "test-order-1"
    assert result["submit_result"]["order"]["identifier"] == "test-order-1"
    assert result["confirm_result"]["identifier"] == "test-order-1"
    assert result["confirm_result"]["confirmed"] is True
    assert result["internal_steps"] == ["orders_submit_order", "orders_confirm_order"]
    assert len(strategy.submitted_orders) == 1
    assert strategy.get_order_calls[0]["identifier"] == "test-order-1"


def test_orders_execute_order_preflights_submits_and_confirms_one_order():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 1000.0
    strategy.portfolio_value = 1200.0
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_execute_order_tools(strategy)

    result = tool_map["orders_execute_order"](
        sequence=1,
        symbol="SPY",
        quantity=3,
        side="buy",
        asset_type="stock",
        order_type="market",
        time_in_force="day",
    )

    assert result["execution_status"] == "completed"
    assert result["can_continue"] is True
    assert result["blockers"] == []
    assert result["preflight_result"]["readiness"] == "ready"
    assert result["preflight_result"]["can_submit"] is True
    assert result["submit_and_confirm_result"]["submitted"] is True
    assert result["submit_and_confirm_result"]["confirmed"] is True
    assert result["submit_and_confirm_result"]["can_continue"] is True
    assert [step["step"] for step in result["internal_steps"]] == ["preflight", "submit_and_confirm"]
    assert len(strategy.submitted_orders) == 1
    assert strategy.submitted_orders[0].asset.symbol == "SPY"


def test_orders_execute_order_blocks_before_submit_when_preflight_blocks():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 250.0
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_execute_order_tools(strategy)

    result = tool_map["orders_execute_order"](symbol="SPY", quantity=3, side="buy")

    assert result["execution_status"] == "blocked"
    assert result["can_continue"] is False
    assert "INSUFFICIENT_CASH_ESTIMATE" in {blocker["code"] for blocker in result["blockers"]}
    assert result["preflight_result"]["readiness"] == "blocked"
    assert result["submit_and_confirm_result"] is None
    assert [step["step"] for step in result["internal_steps"]] == ["preflight"]
    assert strategy.submitted_orders == []


def test_orders_execute_order_returns_blocker_when_confirmation_fails():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 1000.0
    strategy.last_prices = {"SPY": 100.0}
    strategy.submitted_order_status = "new"
    tool_map = _wrap_execute_order_tools(strategy)

    result = tool_map["orders_execute_order"](
        symbol="SPY",
        quantity=3,
        side="buy",
        confirmation_max_attempts=1,
        confirmation_wait_seconds=0,
    )

    assert result["execution_status"] == "blocked"
    assert result["can_continue"] is False
    assert result["preflight_result"]["readiness"] == "ready"
    assert result["submit_and_confirm_result"]["submitted"] is True
    assert result["submit_and_confirm_result"]["confirmed"] is False
    assert "CONFIRMATION_FAILED" in {blocker["code"] for blocker in result["blockers"]}
    assert [step["step"] for step in result["internal_steps"]] == ["preflight", "submit_and_confirm"]
    assert len(strategy.submitted_orders) == 1


def test_orders_execute_order_preserves_negative_cash_guard():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 1000.0
    strategy.last_prices = {"SPY": 100.0}
    strategy.force_negative_cash_after_submit = True
    tool_map = _wrap_execute_order_tools(strategy)

    result = tool_map["orders_execute_order"](symbol="SPY", quantity=3, side="buy")

    assert result["execution_status"] == "blocked"
    assert result["can_continue"] is False
    assert "NEGATIVE_CASH_NOT_ALLOWED" in {blocker["code"] for blocker in result["blockers"]}
    assert strategy.submitted_orders == []


def test_orders_submit_and_confirm_order_consumes_preflight_token():
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_preflight_and_submit_confirm_tools(strategy)

    preflight_result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="buy")
    first_result = tool_map["orders_submit_and_confirm_order"](symbol="SPY", quantity=1, side="buy")
    second_result = tool_map["orders_submit_and_confirm_order"](symbol="SPY", quantity=1, side="buy")

    assert preflight_result["can_submit"] is True
    assert first_result["submitted"] is True
    assert first_result["confirmed"] is True
    assert second_result["submitted"] is False
    assert second_result["confirmed"] is False
    assert second_result["can_continue"] is False
    assert second_result["blockers"][0]["code"] == "ORDER_READINESS_REQUIRED"
    assert len(strategy.submitted_orders) == 1


def test_orders_submit_and_confirm_order_requires_preflight_before_submit():
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_preflight_and_submit_confirm_tools(strategy)

    result = tool_map["orders_submit_and_confirm_order"](symbol="SPY", quantity=1, side="buy")

    assert result["submitted"] is False
    assert result["confirmed"] is False
    assert result["can_continue"] is False
    assert result["blockers"][0]["code"] == "ORDER_READINESS_REQUIRED"
    assert "ORDER_READINESS_REQUIRED" in result["blockers"][0]["message"]
    assert strategy.submitted_orders == []


def test_orders_submit_and_confirm_order_rejects_blocked_preflight():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 50.0
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_preflight_and_submit_confirm_tools(strategy)

    preflight_result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="buy")
    result = tool_map["orders_submit_and_confirm_order"](symbol="SPY", quantity=1, side="buy")

    assert preflight_result["can_submit"] is False
    assert result["submitted"] is False
    assert result["confirmed"] is False
    assert result["can_continue"] is False
    assert result["blockers"][0]["code"] == "ORDER_READINESS_REQUIRED"
    assert strategy.submitted_orders == []


@pytest.mark.parametrize(
    ("submit_kwargs",),
    [
        ({"symbol": "QQQ", "quantity": 1, "side": "buy"},),
        ({"symbol": "SPY", "quantity": 1, "side": "sell"},),
        ({"symbol": "SPY", "quantity": 2, "side": "buy"},),
        ({"symbol": "SPY", "quantity": 1, "side": "buy", "order_type": "limit", "limit_price": 101.0},),
        ({"symbol": "SPY", "quantity": 1, "side": "buy", "time_in_force": "gtc"},),
    ],
)
def test_orders_submit_and_confirm_order_rejects_preflight_mismatches(submit_kwargs):
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"SPY": 100.0, "QQQ": 200.0}
    tool_map = _wrap_preflight_and_submit_confirm_tools(strategy)

    preflight_result = tool_map["orders_preflight_check"](
        symbol="SPY",
        quantity=1,
        side="buy",
        order_type="market",
        time_in_force="day",
    )
    result = tool_map["orders_submit_and_confirm_order"](**submit_kwargs)

    assert preflight_result["can_submit"] is True
    assert result["submitted"] is False
    assert result["confirmed"] is False
    assert result["can_continue"] is False
    assert result["blockers"][0]["code"] == "ORDER_READINESS_REQUIRED"
    assert strategy.submitted_orders == []


def test_orders_submit_and_confirm_order_blocks_negative_cash_before_confirmation():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 100.0
    strategy.last_prices = {"SPY": 80.0}
    tool_map = _wrap_preflight_and_submit_confirm_tools(strategy)

    preflight_result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="buy")
    strategy.cash = 50.0
    result = tool_map["orders_submit_and_confirm_order"](symbol="SPY", quantity=1, side="buy")

    assert preflight_result["can_submit"] is True
    assert result["submitted"] is False
    assert result["confirmed"] is False
    assert result["can_continue"] is False
    assert result["blockers"][0]["code"] == "NEGATIVE_CASH_NOT_ALLOWED"
    assert "NEGATIVE_CASH_NOT_ALLOWED" in result["blockers"][0]["message"]
    assert strategy.submitted_orders == []
    assert strategy.get_order_calls == []


def test_orders_submit_and_confirm_order_returns_blocker_when_confirmation_fails():
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"SPY": 100.0}
    strategy.submitted_order_status = "new"
    tool_map = _wrap_preflight_and_submit_confirm_tools(strategy)

    preflight_result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="buy")
    result = tool_map["orders_submit_and_confirm_order"](
        symbol="SPY",
        quantity=1,
        side="buy",
        confirmation_max_attempts=1,
        confirmation_wait_seconds=0,
    )

    assert preflight_result["can_submit"] is True
    assert result["submitted"] is True
    assert result["confirmed"] is False
    assert result["can_continue"] is False
    assert result["confirmation_status"] == "open_after_retries"
    assert result["blockers"][0]["code"] == "CONFIRMATION_FAILED"
    assert result["confirm_result"]["can_continue"] is False
    assert len(strategy.submitted_orders) == 1


def test_orders_submit_and_confirm_order_returns_blocker_when_confirmation_raises():
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_preflight_and_submit_confirm_tools(strategy)

    def raise_get_order(identifier, broker_refresh=True, broker_refresh_ttl_seconds=0.0):
        raise RuntimeError("broker confirmation unavailable")

    strategy.get_order = raise_get_order

    preflight_result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="buy")
    result = tool_map["orders_submit_and_confirm_order"](symbol="SPY", quantity=1, side="buy")

    assert preflight_result["can_submit"] is True
    assert result["submitted"] is True
    assert result["confirmed"] is False
    assert result["can_continue"] is False
    assert result["identifier"] == "test-order-1"
    assert result["submit_result"]["order"]["identifier"] == "test-order-1"
    assert result["confirm_result"] is None
    assert result["internal_steps"] == ["orders_submit_order", "orders_confirm_order"]
    assert result["blockers"][0]["code"] == "CONFIRMATION_FAILED"
    assert "broker confirmation unavailable" in result["blockers"][0]["message"]
    assert "submitted but not confirmed" in result["blockers"][0]["message"]
    assert "Stop later orders" in result["blockers"][0]["message"]
    assert len(strategy.submitted_orders) == 1


def test_builtin_order_tools_respect_allow_trading_flag():
    strategy = _Strategy()
    manager = AgentManager(strategy)
    confirm_definition = BuiltinTools.orders.confirm()
    confirm_tool = confirm_definition.binder(strategy, manager)

    assert confirm_definition.metadata["mutates_trading"] is True
    assert confirm_tool.metadata["mutates_trading"] is True

    restricted = manager.create(
        name="restricted_confirm",
        tools=[confirm_definition],
        include_builtin_tools=False,
        allow_trading=False,
    )
    permitted = manager.create(
        name="permitted_confirm",
        tools=[confirm_definition],
        include_builtin_tools=False,
        allow_trading=True,
    )

    assert "orders_confirm_order" not in {tool.name for tool in restricted._ensure_bound_tools()}
    assert "orders_confirm_order" in {tool.name for tool in permitted._ensure_bound_tools()}


def test_builtin_indicator_schema_is_gemini_function_declaration_compatible():
    pytest.importorskip("google.adk.tools.function_tool")
    from google.adk.tools.function_tool import FunctionTool

    from lumibot.components.agents.runtime import _wrap_tool_callable

    strategy = _Strategy()
    manager = AgentManager(strategy)
    agent = manager.create(name="researcher", model="gemini-3.1-flash-lite-preview", allow_trading=False)
    indicator_tool = next(tool for tool in agent._ensure_bound_tools() if tool.name == "get_indicator")

    declaration = FunctionTool(_wrap_tool_callable(indicator_tool))._get_declaration().model_dump(exclude_none=True)
    schema_text = str(declaration)
    parameters = declaration.get("parameters") or declaration.get("parameters_json_schema") or {}

    assert "additional_properties" not in schema_text
    assert "parameters_json" in parameters["properties"]


def test_agent_allow_trading_true_keeps_mutating_order_tools():
    strategy = _Strategy()
    manager = AgentManager(strategy)

    agent = manager.create(name="trader", model="openai/gpt-5.5", allow_trading=True)
    tool_names = {tool.name for tool in agent._ensure_bound_tools()}

    assert "orders_submit_order" in tool_names
    assert "orders_submit_and_confirm_order" in tool_names
    assert "orders_cancel_order" in tool_names
    assert "orders_modify_order" in tool_names
    assert "orders_open_orders" in tool_names
    assert agent.default_model == "openai/gpt-5.5"


def test_agent_order_tool_rejects_when_account_context_was_not_checked():
    strategy = _OrderReadinessStrategy()
    tool_map = _wrap_builtin_tools(
        strategy,
        [
            BuiltinTools.account.positions(),
            BuiltinTools.account.portfolio(),
            BuiltinTools.market.last_price(),
            BuiltinTools.orders.submit(),
        ],
    )

    result = tool_map["orders_submit_order"](
        symbol="SPY",
        quantity=1,
        side="buy",
        asset_type="stock",
        order_type="market",
    )

    assert result["tool_error"] is True
    assert result["error"]["type"] == "ValueError"
    assert "ORDER_READINESS_REQUIRED" in result["error"]["message"]
    assert "account_portfolio" in result["error"]["message"]
    assert "account_positions" in result["error"]["message"]
    assert "market_last_price" in result["error"]["message"]
    assert strategy.submitted_orders == []


def test_agent_order_tool_submits_after_account_context_was_checked():
    strategy = _OrderReadinessStrategy()
    tool_map = _wrap_builtin_tools(
        strategy,
        [
            BuiltinTools.account.positions(),
            BuiltinTools.account.portfolio(),
            BuiltinTools.market.last_price(),
            BuiltinTools.orders.submit(),
        ],
    )

    tool_map["account_portfolio"]()
    tool_map["account_positions"]()
    tool_map["market_last_price"](symbol="SPY", asset_type="stock")
    result = tool_map["orders_submit_order"](
        symbol="SPY",
        quantity=1,
        side="buy",
        asset_type="stock",
        order_type="market",
    )

    assert "tool_error" not in result
    assert result["order"]["asset"]["symbol"] == "SPY"
    assert len(strategy.submitted_orders) == 1


def test_agent_order_tool_rejects_buy_that_would_make_cash_negative():
    strategy = _OrderReadinessStrategy()
    strategy.get_cash = lambda: 1000.0
    strategy.get_portfolio_value = lambda: 1000.0
    strategy.get_last_price = lambda asset, quote=None, exchange=None: 100.0
    tool_map = _wrap_builtin_tools(
        strategy,
        [
            BuiltinTools.account.positions(),
            BuiltinTools.account.portfolio(),
            BuiltinTools.market.last_price(),
            BuiltinTools.orders.submit(),
        ],
    )

    tool_map["account_portfolio"]()
    tool_map["account_positions"]()
    tool_map["market_last_price"](symbol="SPY", asset_type="stock")
    result = tool_map["orders_submit_order"](
        symbol="SPY",
        quantity=11,
        side="buy",
        asset_type="stock",
        order_type="market",
    )

    assert result["tool_error"] is True
    assert result["error"]["type"] == "ValueError"
    assert "NEGATIVE_CASH_NOT_ALLOWED" in result["error"]["message"]
    assert strategy.submitted_orders == []


def test_agent_order_tool_allows_buy_that_keeps_cash_positive():
    strategy = _OrderReadinessStrategy()
    strategy.get_cash = lambda: 1000.0
    strategy.get_portfolio_value = lambda: 1000.0
    strategy.get_last_price = lambda asset, quote=None, exchange=None: 100.0
    tool_map = _wrap_builtin_tools(
        strategy,
        [
            BuiltinTools.account.positions(),
            BuiltinTools.account.portfolio(),
            BuiltinTools.market.last_price(),
            BuiltinTools.orders.submit(),
        ],
    )

    tool_map["account_portfolio"]()
    tool_map["account_positions"]()
    tool_map["market_last_price"](symbol="SPY", asset_type="stock")
    result = tool_map["orders_submit_order"](
        symbol="SPY",
        quantity=9,
        side="buy",
        asset_type="stock",
        order_type="market",
    )

    assert "tool_error" not in result
    assert result["order"]["quantity"] == 9.0
    assert len(strategy.submitted_orders) == 1


def test_agent_order_tool_requires_last_price_for_ordered_symbol():
    strategy = _OrderReadinessStrategy()
    tool_map = _wrap_builtin_tools(
        strategy,
        [
            BuiltinTools.account.positions(),
            BuiltinTools.account.portfolio(),
            BuiltinTools.market.last_price(),
            BuiltinTools.orders.submit(),
        ],
    )

    tool_map["account_portfolio"]()
    tool_map["account_positions"]()
    tool_map["market_last_price"](symbol="QQQ", asset_type="stock")
    result = tool_map["orders_submit_order"](
        symbol="SPY",
        quantity=1,
        side="buy",
        asset_type="stock",
        order_type="market",
    )

    assert result["tool_error"] is True
    assert "market_last_price(symbol='SPY')" in result["error"]["message"]
    assert strategy.submitted_orders == []


def test_market_last_price_rejects_comma_separated_symbols():
    strategy = _OrderReadinessStrategy()
    tool_map = _wrap_builtin_tools(
        strategy,
        [
            BuiltinTools.market.last_price(),
        ],
    )

    result = tool_map["market_last_price"](symbol="TQQQ,SQQQ", asset_type="stock")

    assert result["tool_error"] is True
    assert result["error"]["type"] == "ValueError"
    assert "one tradable symbol" in result["error"]["message"]


def test_order_submit_rejects_comma_separated_symbols():
    strategy = _OrderReadinessStrategy()
    tool_map = _wrap_builtin_tools(
        strategy,
        [
            BuiltinTools.account.positions(),
            BuiltinTools.account.portfolio(),
            BuiltinTools.market.last_price(),
            BuiltinTools.orders.submit(),
        ],
    )

    tool_map["account_portfolio"]()
    tool_map["account_positions"]()
    result = tool_map["orders_submit_order"](
        symbol="TQQQ,SQQQ",
        quantity=1,
        side="buy",
        asset_type="stock",
        order_type="market",
    )

    assert result["tool_error"] is True
    assert result["error"]["type"] == "ValueError"
    assert "one tradable symbol" in result["error"]["message"]
    assert strategy.submitted_orders == []


def test_read_only_agent_runtime_can_use_non_trading_tools(tmp_path):
    from lumibot.components.memory import MemoryStore
    from lumibot.components.notifications import NotificationManager

    strategy = _Strategy()
    strategy.is_backtesting = False
    strategy.memory = MemoryStore(strategy, root_dir=tmp_path)
    strategy.notifications = NotificationManager(strategy)
    strategy.notify = lambda title, message, **kwargs: strategy.notifications.notify(title, message, **kwargs)
    manager = AgentManager(strategy)
    runtime = _Runtime()

    agent = manager.create(
        name="researcher_runtime",
        model="openai/gpt-5.4-mini",
        allow_trading=False,
        _runtime=runtime,
    )
    result = agent.run(task_prompt="Research without trading.")

    assert result.summary == "Research completed without trading tools."
    assert _Runtime.last_request.model == "openai/gpt-5.4-mini"
    assert any(event.tool_name == "search_memory" for event in result.tool_calls)
    assert any(event.tool_name == "notify_user" for event in result.tool_calls)
    retrievals = strategy.memory.export_artifacts(tmp_path, prefix="runtime_memory")["memory_retrievals"]
    import pandas as pd

    retrieval_rows = pd.read_parquet(retrievals)
    assert set(retrieval_rows["agent_name"]) == {"researcher_runtime"}
    assert _Runtime.last_request.model_call_id in set(retrieval_rows["model_call_id"])


def test_agent_runtime_memory_notes_are_compacted(monkeypatch, tmp_path):
    from lumibot.components.memory import MemoryStore
    from lumibot.components.notifications import NotificationManager

    monkeypatch.setenv("LUMIBOT_AGENT_MEMORY_NOTE_MAX_CHARS", "500")
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path))
    strategy = _Strategy()
    strategy.vars = _Vars()
    strategy.is_backtesting = False
    strategy.memory = MemoryStore(strategy, root_dir=tmp_path)
    strategy.notifications = NotificationManager(strategy)
    manager = AgentManager(strategy)
    runtime = _LongSummaryRuntime()
    _LongSummaryRuntime.requests = []

    agent = manager.create(
        name="compact_memory",
        model="openai/gpt-5.4-mini",
        allow_trading=False,
        _runtime=runtime,
    )
    agent.run(task_prompt="first long summary")
    state = strategy.vars.get("_agent_runtime_state")
    state["compact_memory"]["runs"][0]["summary"] = "legacy unbounded duplicate"
    strategy.vars.set("_agent_runtime_state", state)
    agent.run(task_prompt="second should receive compact prior summary")

    assert len(_LongSummaryRuntime.requests) == 2
    prior_notes = _LongSummaryRuntime.requests[1].memory_notes
    assert len(prior_notes) == 1
    assert len(prior_notes[0]["summary"]) == 500
    assert prior_notes[0]["summary"].endswith("...")
    # Full summaries are already represented by bounded memory notes and must
    # not be duplicated in the scheduled self.vars runtime metadata.
    runs = strategy.vars.get("_agent_runtime_state")["compact_memory"]["runs"]
    assert all("summary" not in run for run in runs)
    artifact_path = tmp_path / "agent_runtime" / "agent_run_summaries.jsonl"
    artifact_rows = [json.loads(line) for line in artifact_path.read_text().splitlines()]
    migrated = [row for row in artifact_rows if row.get("migrated_from_runtime_state")]
    assert len(migrated) == 1
    assert migrated[0]["summary"] == "legacy unbounded duplicate"


def test_agent_model_call_limit_stops_before_runtime_call(monkeypatch):
    monkeypatch.setenv("LUMIBOT_AGENT_MAX_MODEL_CALLS", "1")
    strategy = _Strategy()
    strategy.vars = _Vars()
    strategy.is_backtesting = False
    manager = AgentManager(strategy)
    runtime = _LongSummaryRuntime()
    _LongSummaryRuntime.requests = []

    agent = manager.create(
        name="limited",
        model="openai/gpt-5.4-mini",
        allow_trading=False,
        _runtime=runtime,
    )

    agent.run(task_prompt="first call is allowed")
    with pytest.raises(AgentModelCallLimitExceeded):
        agent.run(task_prompt="second call is blocked before provider spend")

    assert len(_LongSummaryRuntime.requests) == 1
    assert strategy.parameters["agent_model_calls"] == 1
    assert strategy.parameters["agent_max_model_calls"] == 1
