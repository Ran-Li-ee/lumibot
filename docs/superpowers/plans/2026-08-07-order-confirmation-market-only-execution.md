# Order Confirmation and Market-Only Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit `orders_confirm_order` tool, require execution agents to confirm every submitted order, and make the growth-execution test strategy use market orders only.

**Architecture:** Keep submission and confirmation as separate tools. `orders_submit_order` continues to create/submit orders; the new `orders_confirm_order` refreshes the exact order, retries internally, and returns `confirmed` / `can_continue`. The growth-execution strategy validates market-only execution plans before the trading-enabled execution agent receives them.

**Tech Stack:** Python, Lumibot agent built-in tools, Google ADK function-tool wrapping, pytest, existing replay UI formatter utilities, Yahoo backtesting benchmark runner.

---

## Pre-Flight Notes

The current branch has existing uncommitted changes in:

- `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`
- `tests/test_ai_trading_team_growth_execution_test.py`

These changes are part of the recent prompt/rotation work. Do not revert them. Build on them.

Before starting implementation, run:

```powershell
cd D:\Lumibot
git status --short --branch
```

Expected before implementation:

```text
## dev...origin/dev [ahead 1]
 M lumibot/example_strategies/ai_trading_team_growth_execution_test.py
 M tests/test_ai_trading_team_growth_execution_test.py
```

If extra unrelated files appear, inspect them and avoid adding them to commits for this feature.

## File Structure

Modify:

- `lumibot/components/agents/builtins.py`
  - Add order confirmation helpers.
  - Add `_bind_confirm_order`.
  - Add `BuiltinTools.orders.confirm()`.
  - Enrich `_order_to_dict` with fill and lifecycle fields.

- `tests/test_agent_tool_permissions.py`
  - Add focused tests for `orders_confirm_order`.
  - Add a tool surface test proving explicit `BuiltinTools.orders.confirm()` binds correctly.

- `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`
  - Change allowed order types to `{"market"}`.
  - Reject non-market execution plans before execution.
  - Remove bounded-price order guidance from decision-agent prompt.
  - Add `orders_confirm_order` to execution-agent tools.
  - Update execution-agent prompt and task prompt to require submit-then-confirm after every order.

- `tests/test_ai_trading_team_growth_execution_test.py`
  - Update market-only validation tests.
  - Update prompt tests.
  - Update tool-surface tests.

- `lumibot/components/agents/replay_ui/formatters.py`
  - Add human-readable formatter for `orders_confirm_order`.

- `tests/test_agent_replay_ui_formatters.py`
  - Add formatter tests for confirmed and blocked confirmation results.

Do not modify:

- `lumibot/example_strategies/ai_trading_team_ray_dalio_idea_meritocracy.py`

---

### Task 1: Add `orders_confirm_order` Built-In Tool

**Files:**

- Modify: `tests/test_agent_tool_permissions.py`
- Modify: `lumibot/components/agents/builtins.py`

- [ ] **Step 1: Add failing confirmation-tool tests**

Append this test support code near the existing order-tool tests in `tests/test_agent_tool_permissions.py`, after `test_order_submit_tool_records_memory_event`:

```python
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

    def process_pending_orders(self, strategy=None):
        self.process_pending_calls += 1
        target_strategy = strategy or self.strategy
        if self.process_pending_calls >= target_strategy.fill_after_pending_calls:
            target_strategy.order.status = "fill"
            target_strategy.order.position_filled = True
            target_strategy.order.avg_fill_price = 97.12
            target_strategy.positions = [_ConfirmPosition("USD", 2000.0)]
            target_strategy.cash = 2000.0


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
```

Then append these tests:

```python
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
```

- [ ] **Step 2: Run tests and verify they fail for the expected reason**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py::test_order_confirm_tool_confirms_filled_order_after_processing_pending tests\test_agent_tool_permissions.py::test_builtin_order_tools_expose_explicit_confirm_definition -q
```

Expected:

```text
FAILED ... ImportError or AttributeError for _bind_confirm_order / BuiltinTools.orders.confirm
```

- [ ] **Step 3: Enrich `_order_to_dict`**

In `lumibot/components/agents/builtins.py`, add these helpers immediately above `_order_to_dict`:

```python
def _safe_call(obj: Any, method_name: str, default: Any = None) -> Any:
    method = getattr(obj, method_name, None)
    if not callable(method):
        return default
    try:
        return method()
    except Exception:
        return default


def _order_filled_quantity(order: Any) -> Any:
    transactions = getattr(order, "transactions", None) or []
    if transactions:
        total = 0.0
        for transaction in transactions:
            try:
                total += float(getattr(transaction, "quantity", 0) or 0)
            except Exception:
                return None
        return total
    if _safe_call(order, "is_filled", False):
        quantity = getattr(order, "quantity", None)
        try:
            return float(quantity)
        except Exception:
            return quantity
    return None
```

Then replace `_order_to_dict` with:

```python
def _order_to_dict(order: Any) -> dict[str, Any]:
    asset = getattr(order, "asset", None)
    asset_payload = _asset_to_dict(asset)
    quantity = getattr(order, "quantity", None)
    try:
        quantity = float(quantity)
    except Exception:
        quantity = quantity
    return {
        "identifier": _jsonable(getattr(order, "identifier", None)),
        "status": _jsonable(getattr(order, "status", None)),
        "side": _jsonable(getattr(order, "side", None)),
        "asset": asset_payload,
        "quantity": quantity,
        "filled_quantity": _jsonable(_order_filled_quantity(order)),
        "avg_fill_price": _jsonable(
            getattr(order, "avg_fill_price", None)
            if getattr(order, "avg_fill_price", None) is not None
            else _safe_call(order, "get_fill_price", None)
        ),
        "is_active": _jsonable(_safe_call(order, "is_active", None)),
        "is_filled": _jsonable(_safe_call(order, "is_filled", None)),
        "is_canceled": _jsonable(_safe_call(order, "is_canceled", None)),
        "order_type": _jsonable(getattr(order, "order_type", None)),
        "time_in_force": _jsonable(getattr(order, "time_in_force", None)),
        "limit_price": _jsonable(getattr(order, "limit_price", None)),
        "stop_price": _jsonable(getattr(order, "stop_price", None)),
        "stop_limit_price": _jsonable(getattr(order, "stop_limit_price", None)),
        "trail_price": _jsonable(getattr(order, "trail_price", None)),
        "trail_percent": _jsonable(getattr(order, "trail_percent", None)),
    }
```

- [ ] **Step 4: Implement confirmation helpers and binder**

In `lumibot/components/agents/builtins.py`, add this block after `_bind_open_orders` and before `_bind_cancel_order`:

```python
_ORDER_CONFIRM_MAX_ATTEMPTS = 5
_ORDER_CONFIRM_MAX_WAIT_SECONDS = 5.0
_ORDER_CONFIRM_TERMINAL_FAILURE_STATUSES = {
    "cancel",
    "canceled",
    "cancelled",
    "error",
    "expired",
    "rejected",
}
_ORDER_CONFIRM_PARTIAL_STATUSES = {"partial_fill", "partially_filled"}


def _coerce_confirm_attempts(max_attempts: Any) -> int:
    try:
        attempts = int(max_attempts)
    except Exception:
        attempts = 3
    return min(max(attempts, 1), _ORDER_CONFIRM_MAX_ATTEMPTS)


def _coerce_confirm_wait_seconds(wait_seconds: Any, strategy: Any) -> float:
    if wait_seconds is None:
        wait_seconds = 0.0 if bool(getattr(strategy, "is_backtesting", False)) else 1.0
    try:
        wait = float(wait_seconds)
    except Exception:
        wait = 0.0
    if not math.isfinite(wait):
        wait = 0.0
    return min(max(wait, 0.0), _ORDER_CONFIRM_MAX_WAIT_SECONDS)


def _get_order_for_confirmation(strategy: Any, identifier: str) -> Any:
    get_order = getattr(strategy, "get_order", None)
    if not callable(get_order):
        return None
    try:
        return get_order(identifier, broker_refresh=True, broker_refresh_ttl_seconds=0.0)
    except TypeError:
        try:
            return get_order(identifier, broker_refresh=True)
        except TypeError:
            return get_order(identifier)


def _process_pending_orders_for_confirmation(strategy: Any) -> bool:
    broker = getattr(strategy, "broker", None)
    process_pending_orders = getattr(broker, "process_pending_orders", None)
    if not bool(getattr(strategy, "is_backtesting", False)) or not callable(process_pending_orders):
        return False
    try:
        process_pending_orders(strategy=strategy)
    except TypeError:
        process_pending_orders(strategy)
    return True


def _sleep_for_confirmation(strategy: Any, seconds: float) -> bool:
    if seconds <= 0:
        return False
    sleep = getattr(strategy, "sleep", None)
    if callable(sleep):
        try:
            sleep(seconds)
            return True
        except Exception:
            return False
    return False


def _position_quantity_for_symbol(strategy: Any, symbol: str | None) -> float | None:
    if not symbol:
        return None
    symbol = symbol.strip().upper()
    positions = getattr(strategy, "get_positions", None)
    if not callable(positions):
        return None
    try:
        position_list = positions(include_cash_positions=True)
    except TypeError:
        position_list = positions()
    total = 0.0
    found = False
    for position in position_list or []:
        asset = getattr(position, "asset", None)
        position_symbol = str(getattr(asset, "symbol", "")).strip().upper()
        if position_symbol != symbol:
            continue
        try:
            total += float(getattr(position, "quantity", 0) or 0)
        except Exception:
            return None
        found = True
    return total if found else 0.0


def _positions_snapshot(strategy: Any) -> list[dict[str, Any]]:
    positions = getattr(strategy, "get_positions", None)
    if not callable(positions):
        return []
    try:
        position_list = positions(include_cash_positions=True)
    except TypeError:
        position_list = positions()
    return [_position_to_dict(position) for position in position_list or []]


def _account_snapshot(strategy: Any) -> dict[str, Any]:
    cash = None
    portfolio_value = None
    get_cash = getattr(strategy, "get_cash", None)
    get_portfolio_value = getattr(strategy, "get_portfolio_value", None)
    if callable(get_cash):
        try:
            cash = get_cash()
        except Exception:
            cash = None
    if callable(get_portfolio_value):
        try:
            portfolio_value = get_portfolio_value()
        except Exception:
            portfolio_value = None
    return {
        "cash": _jsonable(cash),
        "portfolio_value": _jsonable(portfolio_value),
        "positions": _positions_snapshot(strategy),
    }


def _order_confirmation_status(order: Any, *, attempts_exhausted: bool = False) -> str:
    if order is None:
        return "not_found"
    raw_status = str(getattr(order, "status", "") or "").strip().lower()
    if _safe_call(order, "is_filled", False):
        if raw_status == "cash_settled":
            return "cash_settled"
        return "filled"
    if raw_status in _ORDER_CONFIRM_PARTIAL_STATUSES:
        return "partially_filled"
    if raw_status in _ORDER_CONFIRM_TERMINAL_FAILURE_STATUSES or _safe_call(order, "is_canceled", False):
        if raw_status in {"cancel", "cancelled"}:
            return "canceled"
        return raw_status or "error"
    if attempts_exhausted and _safe_call(order, "is_active", False):
        return "open_after_retries"
    return raw_status or "unknown"


def _confirmation_checks(
    strategy: Any,
    *,
    order: Any,
    symbol: str | None,
    side: str | None,
    expected_quantity: float | None,
    position_before_quantity: float | None,
    cash_before: float | None,
    account_snapshot: dict[str, Any],
) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "order_found": order is not None,
        "order_filled": bool(_safe_call(order, "is_filled", False)) if order is not None else False,
    }
    if order is None:
        return checks

    current_position = _position_quantity_for_symbol(strategy, symbol)
    checks["current_position_quantity"] = _jsonable(current_position)
    if position_before_quantity is not None and current_position is not None and side:
        before_qty = float(position_before_quantity)
        expected_qty = float(expected_quantity or 0)
        side_value = str(side).strip().lower()
        if side_value in {"sell", "sell_to_close", "sell_short", "sell_to_open"}:
            checks["position_moved_as_expected"] = current_position <= before_qty - min(expected_qty, abs(before_qty)) + 1e-9
        elif side_value in {"buy", "buy_to_open", "buy_to_close", "buy_to_cover"}:
            checks["position_moved_as_expected"] = current_position >= before_qty + expected_qty - 1e-9

    current_cash = account_snapshot.get("cash")
    if cash_before is not None and current_cash is not None and side:
        before_cash = float(cash_before)
        cash_value = float(current_cash)
        side_value = str(side).strip().lower()
        if side_value in {"sell", "sell_to_close", "sell_short", "sell_to_open"}:
            checks["cash_moved_as_expected"] = cash_value >= before_cash
        elif side_value in {"buy", "buy_to_open", "buy_to_close", "buy_to_cover"}:
            checks["cash_moved_as_expected"] = cash_value <= before_cash

    return checks


def _confirmation_checks_pass(checks: dict[str, Any]) -> bool:
    for key in ("position_moved_as_expected", "cash_moved_as_expected"):
        if key in checks and checks[key] is False:
            return False
    return bool(checks.get("order_found")) and bool(checks.get("order_filled"))


def _bind_confirm_order(strategy: Any, manager: Any) -> BoundTool:
    def confirm_order(
        *,
        identifier: str,
        symbol: str | None = None,
        side: str | None = None,
        expected_quantity: float | None = None,
        position_before_quantity: float | None = None,
        cash_before: float | None = None,
        max_attempts: int = 3,
        wait_seconds: float | None = None,
    ) -> dict[str, Any]:
        identifier = _require_non_empty_text("identifier", identifier)
        if symbol is not None:
            symbol = _require_single_symbol_text("symbol", symbol)
        if expected_quantity is not None:
            expected_quantity = _require_positive_number("expected_quantity", expected_quantity)
        attempts_limit = _coerce_confirm_attempts(max_attempts)
        wait_value = _coerce_confirm_wait_seconds(wait_seconds, strategy)
        attempts: list[dict[str, Any]] = []
        warnings: list[str] = []
        order = None
        account = _account_snapshot(strategy)
        checks: dict[str, Any] = {"order_found": False, "order_filled": False}
        status = "unknown"

        for attempt_number in range(1, attempts_limit + 1):
            processed_pending = _process_pending_orders_for_confirmation(strategy)
            order = _get_order_for_confirmation(strategy, identifier)
            account = _account_snapshot(strategy)
            status = _order_confirmation_status(
                order,
                attempts_exhausted=attempt_number == attempts_limit,
            )
            order_payload = _order_to_dict(order) if order is not None else None
            checks = _confirmation_checks(
                strategy,
                order=order,
                symbol=symbol,
                side=side,
                expected_quantity=expected_quantity,
                position_before_quantity=position_before_quantity,
                cash_before=cash_before,
                account_snapshot=account,
            )
            attempts.append(
                {
                    "attempt": attempt_number,
                    "processed_pending_orders": processed_pending,
                    "status": status,
                    "is_active": order_payload.get("is_active") if order_payload else None,
                    "is_filled": order_payload.get("is_filled") if order_payload else None,
                }
            )

            if order is None:
                warnings.append(f"Order {identifier} was not found.")
                break
            if status in _ORDER_CONFIRM_TERMINAL_FAILURE_STATUSES or status in {"canceled", "expired", "rejected", "error"}:
                warnings.append(f"Order {identifier} reached terminal status {status}.")
                break
            if status == "partially_filled":
                warnings.append(f"Order {identifier} is partially filled; dependent orders should not continue.")
                break
            if _confirmation_checks_pass(checks):
                return {
                    "identifier": identifier,
                    "confirmed": True,
                    "can_continue": True,
                    "confirmation_status": status,
                    "attempt_count": attempt_number,
                    "order": order_payload,
                    "account_snapshot": account,
                    "checks": checks,
                    "attempts": attempts,
                    "warnings": warnings,
                }
            if attempt_number < attempts_limit:
                _sleep_for_confirmation(strategy, wait_value)

        if order is not None and status not in {"not_found", "partially_filled", "canceled", "expired", "rejected", "error"}:
            status = "open_after_retries" if _safe_call(order, "is_active", False) else status
            if not warnings:
                warnings.append(
                    f"Order {identifier} remained active after {attempts_limit} confirmation attempts. "
                    "Do not submit dependent orders."
                )

        return {
            "identifier": identifier,
            "confirmed": False,
            "can_continue": False,
            "confirmation_status": status,
            "attempt_count": len(attempts),
            "order": _order_to_dict(order) if order is not None else None,
            "account_snapshot": account,
            "checks": checks,
            "attempts": attempts,
            "warnings": warnings,
        }

    return BoundTool(
        name="orders_confirm_order",
        description=(
            "Confirm a previously submitted order by identifier. Use this after every orders_submit_order call "
            "before submitting any later order or writing the final execution summary. The tool refreshes the exact "
            "order, retries internally, and returns whether the order is confirmed filled and whether it is safe to "
            "continue with later orders. This tool does not submit, cancel, or modify orders."
        ),
        function=confirm_order,
        metadata={"kind": "builtin", "replay_on_cache": True},
    )
```

- [ ] **Step 5: Add `BuiltinTools.orders.confirm()`**

In `_OrderTools`, add this method after `submit()`:

```python
    def confirm(self) -> ToolDefinition:
        return ToolDefinition(
            name="orders_confirm_order",
            description="Confirm a submitted order by identifier before continuing execution.",
            binder=_bind_confirm_order,
        )
```

Do not add `self.orders.confirm()` to `_BuiltinTools.all()` in this task. The tool should be opt-in for strategies that explicitly need it.

- [ ] **Step 6: Run confirmation-tool tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py::test_order_confirm_tool_confirms_filled_order_after_processing_pending tests\test_agent_tool_permissions.py::test_order_confirm_tool_returns_open_after_retries_and_caps_attempts tests\test_agent_tool_permissions.py::test_order_confirm_tool_returns_terminal_rejected_failure tests\test_agent_tool_permissions.py::test_order_confirm_tool_returns_not_found_for_unknown_identifier tests\test_agent_tool_permissions.py::test_builtin_order_tools_expose_explicit_confirm_definition -q
```

Expected:

```text
5 passed
```

- [ ] **Step 7: Commit Task 1**

Run:

```powershell
git add lumibot\components\agents\builtins.py tests\test_agent_tool_permissions.py
git commit -m "feat: add agent order confirmation tool"
```

---

### Task 2: Enforce Market-Only Execution-Plan Validation

**Files:**

- Modify: `tests/test_ai_trading_team_growth_execution_test.py`
- Modify: `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`

- [ ] **Step 1: Add failing market-only parser tests**

In `tests/test_ai_trading_team_growth_execution_test.py`, add these tests near the existing execution-plan parser tests:

```python
def test_parse_execution_plan_rejects_non_market_order_type():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = (
        '{"decision":{"type":"buy","from":"USD","to":"VNQ","reason_brief":"test"},'
        '"execution_plan":{"schema_version":1,"intent":"enter_position","orders":[{"sequence":1,'
        '"action":"submit_order","symbol":"VNQ","side":"buy","quantity":10,'
        '"quantity_mode":"shares","asset_type":"stock","order_type":"limit","limit_price":95.0,'
        '"time_in_force":"day"}],"constraints":{}}}'
    )

    with pytest.raises(ValueError, match="market-only"):
        strategy_module.parse_execution_plan_from_decision_summary(raw_summary)


def test_parse_execution_plan_rejects_market_order_with_actionable_price_fields():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = (
        '{"decision":{"type":"buy","from":"USD","to":"VNQ","reason_brief":"test"},'
        '"execution_plan":{"schema_version":1,"intent":"enter_position","orders":[{"sequence":1,'
        '"action":"submit_order","symbol":"VNQ","side":"buy","quantity":10,'
        '"quantity_mode":"shares","asset_type":"stock","order_type":"market","limit_price":95.0,'
        '"time_in_force":"day"}],"constraints":{}}}'
    )

    with pytest.raises(ValueError, match="market-only execution_plan must not include limit_price"):
        strategy_module.parse_execution_plan_from_decision_summary(raw_summary)
```

- [ ] **Step 2: Update existing non-market tests**

Find tests that currently expect non-market orders to pass. The following names are expected in the current file:

- `test_decision_buy_sizing_uses_bounded_order_price_for_limit_order`
- `test_decision_buy_after_limit_sell_proceeds_is_blocked`
- parametrized cases for `_raw_execution_order_price` using `limit`, `smart_limit`, `stop`, and `stop_limit`

Replace acceptance assertions for non-market order types with rejection assertions. Use this pattern:

```python
with pytest.raises(ValueError, match="market-only"):
    strategy_module.parse_execution_plan_from_decision_summary(raw_summary)
```

For `_raw_execution_order_price` unit coverage, keep only the market-order case:

```python
def test_execution_order_price_for_market_order_uses_last_price():
    strategy_module, _strategy_class = load_strategy_module()
    strategy = SimpleNamespace(get_last_price=lambda symbol: 100.0)

    price = strategy_module._execution_order_price(
        strategy,
        {
            "symbol": "SPY",
            "order_type": "market",
            "limit_price": None,
            "stop_price": None,
            "stop_limit_price": None,
        },
    )

    assert price == 100.0
```

- [ ] **Step 3: Run market-only tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py::test_parse_execution_plan_rejects_non_market_order_type tests\test_ai_trading_team_growth_execution_test.py::test_parse_execution_plan_rejects_market_order_with_actionable_price_fields -q
```

Expected:

```text
FAILED ... did not raise ValueError
```

- [ ] **Step 4: Make order parsing market-only**

In `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`, replace:

```python
ALLOWED_ORDER_TYPES = {"market", "limit", "stop", "stop_limit", "trailing_stop", "smart_limit"}
```

with:

```python
ALLOWED_ORDER_TYPES = {"market"}
MARKET_ONLY_FORBIDDEN_PRICE_FIELDS = (
    "limit_price",
    "stop_price",
    "stop_limit_price",
    "trail_price",
    "trail_percent",
)
```

In `_normalize_order`, replace the current `order_type` validation block:

```python
    order_type = str(order.get("order_type", "market")).strip().lower()
    if order_type not in ALLOWED_ORDER_TYPES:
        raise ValueError(f"unsupported order_type: {order_type}")
```

with:

```python
    order_type = str(order.get("order_type", "market")).strip().lower()
    if order_type not in ALLOWED_ORDER_TYPES:
        raise ValueError(
            "unsupported order_type for daily market-only strategy: "
            f"{order_type}. Use order_type='market'."
        )
    for field in MARKET_ONLY_FORBIDDEN_PRICE_FIELDS:
        if order.get(field) is not None:
            raise ValueError(f"market-only execution_plan must not include {field}.")
```

- [ ] **Step 5: Simplify execution-price helpers**

In the same strategy file, replace `_raw_execution_order_price`, `_execution_order_price`, and `_decision_order_price` with:

```python
def _raw_execution_order_price(strategy, order):
    raw_price = strategy.get_last_price(order["symbol"])
    if raw_price is None:
        raise ValueError(f"PRICE_REQUIRED: cannot validate execution plan because {order['symbol']} has no last price.")
    return raw_price, None


def _execution_order_price(strategy, order):
    raw_price, _price_field = _raw_execution_order_price(strategy, order)
    price = float(raw_price)
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"PRICE_REQUIRED: invalid last price for {order['symbol']}: {raw_price!r}.")
    return price


def _decision_order_price(strategy, order):
    raw_price, _price_field = _raw_execution_order_price(strategy, order)
    label = f"last price for {order['symbol']}"
    price = _decimal_number(raw_price, label)
    if price <= 0:
        raise ValueError(f"PRICE_REQUIRED: invalid last price for {order['symbol']}: {raw_price!r}.")
    return price
```

- [ ] **Step 6: Remove obsolete non-market buy-order guard**

In `parse_execution_plan_from_decision_summary`, remove this block:

```python
        buy_order_type = normalized_orders[buy_index]["order_type"]
        if buy_order_type in {"stop", "trailing_stop"}:
            raise ValueError(
                f"buy order_type {buy_order_type!r} does not provide a bounded execution price."
            )
```

The market-only parser now rejects those order types earlier.

- [ ] **Step 7: Run focused strategy validation tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py::test_parse_execution_plan_rejects_non_market_order_type tests\test_ai_trading_team_growth_execution_test.py::test_parse_execution_plan_rejects_market_order_with_actionable_price_fields tests\test_ai_trading_team_growth_execution_test.py::test_execution_order_price_for_market_order_uses_last_price -q
```

Expected:

```text
3 passed
```

- [ ] **Step 8: Commit Task 2**

Commit only after the focused parser/validation tests pass.

Run:

```powershell
git add lumibot\example_strategies\ai_trading_team_growth_execution_test.py tests\test_ai_trading_team_growth_execution_test.py
git commit -m "feat: enforce market-only growth execution plans"
```

---

### Task 3: Update Decision and Execution Agent Prompts and Tool Surface

**Files:**

- Modify: `tests/test_ai_trading_team_growth_execution_test.py`
- Modify: `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`

- [ ] **Step 1: Update prompt/tool-surface tests first**

In `test_growth_decision_execution_agents_receive_distinct_tool_surfaces`, update the execution agent expected tool set to:

```python
    assert created_tool_names(created["execution_agent"]) == {
        "account_positions",
        "account_portfolio",
        "market_last_price",
        "orders_open_orders",
        "orders_submit_order",
        "orders_confirm_order",
    }
```

In `test_decision_prompt_requests_structured_execution_plan`, update required phrases to include:

```python
        "all executable orders must use order_type market",
        "do not output limit_price, stop_price, stop_limit_price, trail_price, or trail_percent",
        "for buy sizing, call market_last_price and use a conservative market sizing price",
        "return only the final numeric whole-share quantity",
```

Remove these required phrases from that test:

```python
        "for limit buys, use limit_price",
        "for stop_limit buys, use stop_limit_price or the final bounded execution price",
        "buy orders must use market, limit, smart_limit, or stop_limit",
        "do not use stop or trailing_stop for a buy order",
        "optional bounded-price fields include limit_price, stop_price, and stop_limit_price",
```

Add these forbidden phrases to the same test:

```python
        "smart_limit",
        "stop_limit",
        "trailing_stop",
        "limit buys",
        "bounded execution price",
```

In `test_execution_prompt_treats_execution_plan_as_authoritative`, replace the existing rotate-specific required phrases:

```python
        "confirmed sequence",
        "prior sell order is no longer open",
        "cash or buying power has updated",
        "do not submit the dependent buy order",
```

with general confirmation phrases:

```python
        "orders_confirm_order",
        "after every orders_submit_order",
        "returned identifier",
        "can_continue=true",
        "can_continue=false",
        "stop all remaining orders",
        "use only market orders",
```

Keep the existing forbidden checks for research material and cash-buffer material.

- [ ] **Step 2: Run prompt/tool tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py::test_growth_decision_execution_agents_receive_distinct_tool_surfaces tests\test_ai_trading_team_growth_execution_test.py::test_decision_prompt_requests_structured_execution_plan tests\test_ai_trading_team_growth_execution_test.py::test_execution_prompt_treats_execution_plan_as_authoritative -q
```

Expected:

```text
FAILED ... missing orders_confirm_order and market-only prompt phrases
```

- [ ] **Step 3: Add confirmation tool to execution agent**

In `AITradingTeamGrowthExecutionTestStrategy.initialize()`, update the execution-agent `tools` list:

```python
            tools=[
                BuiltinTools.account.positions(),
                BuiltinTools.account.portfolio(),
                BuiltinTools.market.last_price(),
                BuiltinTools.orders.open_orders(),
                BuiltinTools.orders.submit(),
                BuiltinTools.orders.confirm(),
            ],
```

- [ ] **Step 4: Replace decision-agent system prompt with market-only wording**

Replace the `decision_agent` `system_prompt` string with:

```python
            system_prompt=(
                "Decision agent role: convert growth_report and current account state into a concrete account "
                "management decision and strict execution_plan. Do not place orders and do not perform broad ETF "
                "research again. Choose exactly one decision.type from hold, buy, rotate, reduce, close. Decision "
                "JSON contract: return only one valid JSON object with top-level fields decision and execution_plan. "
                "Do not include markdown, RESULT text, or prose after the JSON. decision must include "
                "decision.type, decision.from, decision.to, and decision.reason_brief. execution_plan must include "
                "schema_version, intent, and orders (execution_plan.orders). intent must be one of hold, "
                "enter_position, rotate, reduce_position, close_position. Each executable order must include "
                "sequence, symbol, side, quantity_mode, quantity, asset_type, order_type, and time_in_force. "
                'quantity_mode must be exactly "shares" for every executable order. '
                'All executable orders must use order_type "market". Do not output limit_price, stop_price, '
                "stop_limit_price, trail_price, or trail_percent. Use numeric whole-share quantities; do not use "
                "full_position, current_position, max_affordable_cash, or max_affordable_after_prior_sells. "
                "Before a non-hold decision, call account_positions and account_portfolio. For buy sizing, call "
                "market_last_price and use a conservative market sizing price based on available price evidence; it "
                "may be higher than market_last_price in daily backtests. Use the 98% cash rule only as an internal "
                "sizing rule: maximum buy quantity must be no greater than "
                "floor(0.98 * available_cash_after_prior_sells / sizing_price). Return only the final numeric "
                "whole-share quantity. Do not output cash_buffer_pct or any buffer field in execution_plan. Never "
                "produce orders that would make cash negative. For selling all or part of a position, calculate the "
                "share quantity from account tool output. "
                'For rotate decisions, set sequence: 1 with side: "sell" for decision.from, then set sequence: 2 '
                'with side: "buy" for decision.to.'
            ),
```

- [ ] **Step 5: Replace execution-agent system prompt with submit-then-confirm wording**

Replace the `execution_agent` `system_prompt` string with:

```python
            system_prompt=(
                "Execution agent role: execute only the provided execution_plan object using native execution tools, "
                "especially orders_submit_order and orders_confirm_order. Treat execution_plan.orders as "
                "authoritative. Do not read or infer investment reasons. Do not re-rank candidates, do not "
                "substitute symbols, and do not use upstream research to override the plan. Do not add, remove, "
                "replace, or reorder orders. Use only market orders. If any execution_plan order is not a market "
                "order, block it as an execution-level blocker. Inspect positions, portfolio, open orders, and "
                "latest prices before submitting orders. Submit orders in ascending sequence order. After every "
                "orders_submit_order call, immediately call orders_confirm_order with the returned identifier, "
                "symbol, side, and expected_quantity. Do not submit any later order and do not write the final "
                "summary until that order is confirmed. If orders_confirm_order returns can_continue=false, stop "
                "all remaining orders and report the confirmation blocker. Submit the explicit numeric share "
                "quantities in execution_plan.orders. Do not compute semantic sizing. Block or pause only for "
                "execution-level blockers. Execution report contract: report each sequence as submitted, confirmed, "
                "or blocked, and finish with a short RESULT summary."
            ),
```

- [ ] **Step 6: Update execution-agent task prompt**

Replace the execution-agent task prompt in `on_trading_iteration()` with:

```python
            task_prompt=(
                "Execute only the provided execution_plan object. Inspect account state, open orders, positions, and "
                "latest prices, then submit only execution_plan.orders with orders_submit_order. After every submit, "
                "confirm that same order with orders_confirm_order before continuing. Preserve sequence order and "
                "report each sequence as submitted, confirmed, or blocked. Block solely for execution-level blockers."
            ),
```

- [ ] **Step 7: Run focused prompt/tool tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py::test_growth_decision_execution_agents_receive_distinct_tool_surfaces tests\test_ai_trading_team_growth_execution_test.py::test_decision_prompt_requests_structured_execution_plan tests\test_ai_trading_team_growth_execution_test.py::test_execution_prompt_treats_execution_plan_as_authoritative tests\test_ai_trading_team_growth_execution_test.py::test_on_trading_iteration_hands_off_context_in_order tests\test_ai_trading_team_growth_execution_test.py::test_execution_handoff_keeps_execution_agent_free_of_buffer_and_retry_material -q
```

Expected:

```text
5 passed
```

- [ ] **Step 8: Run all growth-execution strategy tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py -q
```

Expected:

```text
All tests in this file pass
```

- [ ] **Step 9: Commit Task 3**

Run:

```powershell
git add lumibot\example_strategies\ai_trading_team_growth_execution_test.py tests\test_ai_trading_team_growth_execution_test.py
git commit -m "feat: require market order confirmation in growth execution agent"
```

---

### Task 4: Add Replay UI Formatter for Confirmation Tool

**Files:**

- Modify: `tests/test_agent_replay_ui_formatters.py`
- Modify: `lumibot/components/agents/replay_ui/formatters.py`

- [ ] **Step 1: Add failing formatter tests**

In `tests/test_agent_replay_ui_formatters.py`, add these tests after `test_orders_submit_order_formatter_extracts_nested_order_payload`:

```python
def test_orders_confirm_order_formatter_explains_confirmed_fill():
    text = explain_tool_result(
        "orders_confirm_order",
        {"identifier": "order-123", "symbol": "VNQ", "side": "sell", "expected_quantity": 10},
        {
            "identifier": "order-123",
            "confirmed": True,
            "can_continue": True,
            "confirmation_status": "filled",
            "attempt_count": 2,
            "order": {
                "identifier": "order-123",
                "status": "fill",
                "side": "sell",
                "quantity": 10,
                "asset": {"symbol": "VNQ", "asset_type": "stock"},
            },
            "warnings": [],
        },
        None,
    )

    assert "Confirmed order order-123" in text
    assert "sell 10 VNQ" in text
    assert "2 attempts" in text
    assert "can_continue=true" in text


def test_orders_confirm_order_formatter_explains_blocked_confirmation():
    text = explain_tool_result(
        "orders_confirm_order",
        {"identifier": "order-123", "symbol": "VNQ", "side": "sell", "expected_quantity": 10},
        {
            "identifier": "order-123",
            "confirmed": False,
            "can_continue": False,
            "confirmation_status": "open_after_retries",
            "attempt_count": 3,
            "order": {
                "identifier": "order-123",
                "status": "new",
                "side": "sell",
                "quantity": 10,
                "asset": {"symbol": "VNQ", "asset_type": "stock"},
            },
            "warnings": ["Order remained active after 3 confirmation attempts."],
        },
        None,
    )

    assert "Confirmation blocked" in text
    assert "open_after_retries" in text
    assert "can_continue=false" in text
    assert "Order remained active" in text
```

- [ ] **Step 2: Run formatter tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_formatters.py::test_orders_confirm_order_formatter_explains_confirmed_fill tests\test_agent_replay_ui_formatters.py::test_orders_confirm_order_formatter_explains_blocked_confirmation -q
```

Expected:

```text
FAILED ... generic formatter text
```

- [ ] **Step 3: Add formatter function**

In `lumibot/components/agents/replay_ui/formatters.py`, add this function after `_orders_submit_order`:

```python
def _orders_confirm_order(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    order = _nested_dict(result, "order") or {}
    symbol = _asset_symbol(order) or _first_present(args, "symbol")
    qty = _first_present(order, "qty", "quantity", "shares") or _first_present(
        args,
        "expected_quantity",
        "quantity",
        "shares",
    )
    side = _first_present(order, "side", "action") or _first_present(args, "side", "action")
    order_id = _first_present(result, "identifier", "id", "order_id") or _first_present(
        order,
        "identifier",
        "id",
        "order_id",
    )
    status = _first_present(result, "confirmation_status", "status")
    attempt_count = _first_present(result, "attempt_count", "attempts_count")
    can_continue = bool(result.get("can_continue"))
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    order_text = f"{_text(side)} {_text(qty)} {_text(symbol)}"
    attempts_text = ""
    if attempt_count is not None:
        attempts_text = f" after {_text(attempt_count)} attempts"
    if result.get("confirmed") is True:
        return (
            f"Confirmed order {_text(order_id)} for {order_text}{attempts_text}. "
            f"Status: {_text(status)}. can_continue=true."
        )
    warning_text = ""
    if warnings:
        warning_text = f" Warning: {_text(warnings[0])}"
    return (
        f"Confirmation blocked for order {_text(order_id)} for {order_text}{attempts_text}. "
        f"Status: {_text(status)}. can_continue=false.{warning_text}"
    )
```

Then add it to `_FORMATTERS`:

```python
    "orders_confirm_order": _orders_confirm_order,
```

- [ ] **Step 4: Run formatter tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_formatters.py -q
```

Expected:

```text
All tests in this file pass
```

- [ ] **Step 5: Commit Task 4**

Run:

```powershell
git add lumibot\components\agents\replay_ui\formatters.py tests\test_agent_replay_ui_formatters.py
git commit -m "feat: explain order confirmation in agent replay"
```

---

### Task 5: Run Focused Regression Suite

**Files:**

- No code changes unless a focused test reveals a defect introduced by Tasks 1-4.

- [ ] **Step 1: Run focused test suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py tests\test_ai_trading_team_growth_execution_test.py tests\test_agent_replay_ui_formatters.py -q
```

Expected:

```text
All selected tests pass
```

- [ ] **Step 2: Run lint for changed files**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\builtins.py lumibot\components\agents\replay_ui\formatters.py lumibot\example_strategies\ai_trading_team_growth_execution_test.py tests\test_agent_tool_permissions.py tests\test_ai_trading_team_growth_execution_test.py tests\test_agent_replay_ui_formatters.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 3: Inspect git diff for accidental scope creep**

Run:

```powershell
git diff --stat HEAD
git diff -- lumibot\example_strategies\ai_trading_team_ray_dalio_idea_meritocracy.py
```

Expected:

```text
No diff for ai_trading_team_ray_dalio_idea_meritocracy.py
```

- [ ] **Step 4: Commit any regression fixes**

If Step 1 or Step 2 required a code fix, commit only the fix:

```powershell
git add <changed-files>
git commit -m "fix: stabilize order confirmation regression tests"
```

If no files changed after Task 4, skip this commit.

---

### Task 6: Run One-Day Backtest and Verify Trace Shape

**Files:**

- No source changes unless the backtest exposes an implementation bug.

- [ ] **Step 1: Set model and API environment**

Use the current default development model:

```powershell
cd D:\Lumibot
$env:AI_TRADING_TEAM_MODEL = "openai/gpt-5.6-luna"
$env:OPENAI_API_KEY = (Get-Content project_notes\API.txt | Select-String "sk-" | Select-Object -First 1).Line.Trim()
$env:GOOGLE_API_KEY = "not-used-openai-runner-gate"
```

- [ ] **Step 2: Run a one-day growth-execution backtest**

Run:

```powershell
.\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy growth-execution-test --start 2024-09-05 --end 2024-09-05 --max-workers 1 --max-run-attempts 1
```

Expected:

```text
JSON output with "strategy": "growth-execution-test" and "status": "passed"
```

- [ ] **Step 3: Open replay UI**

Run:

```powershell
.\.venv\Scripts\python.exe scripts\agent_trace_ui.py
```

Expected:

```text
Serving on http://127.0.0.1:8765
```

In the browser:

- Select strategy `AITradingTeamGrowthExecutionTestStrategy`.
- Select the newest one-day backtest run.
- Select the system run for `2024-09-05`.
- Click `execution_agent`.

Expected UI observations:

- `Available Tool Names` includes `orders_confirm_order`.
- If an order was submitted, `Tool Calls` shows `orders_submit_order` followed by `orders_confirm_order`.
- The confirmation tool's human explanation includes `can_continue=true` or a clear blocker.

- [ ] **Step 4: Inspect trace files directly**

Run:

```powershell
rg -n "orders_submit_order|orders_confirm_order|can_continue|confirmation_status" artifacts\ai_trading_team_example_benchmarks -S
```

Expected:

```text
At least one newest-run trace contains orders_confirm_order when orders_submit_order appears.
```

- [ ] **Step 5: Fix and commit if one-day trace is structurally wrong**

If `orders_submit_order` appears without a following `orders_confirm_order`, inspect the execution-agent prompt and tool list first. After fixing, rerun Step 2 and Step 4, then commit:

```powershell
git add <changed-files>
git commit -m "fix: ensure execution agent confirms submitted orders"
```

If the one-day run has no submitted order because the decision agent chose hold, this task is still acceptable as long as the execution-agent available tool list contains `orders_confirm_order` and unit tests pass.

---

### Task 7: Run Rotation-Window Backtest and Check Same-Day Rotation

**Files:**

- No source changes unless the backtest exposes an implementation bug.

- [ ] **Step 1: Run the rotation-prone window**

Run:

```powershell
cd D:\Lumibot
$env:AI_TRADING_TEAM_MODEL = "openai/gpt-5.6-luna"
$env:OPENAI_API_KEY = (Get-Content project_notes\API.txt | Select-String "sk-" | Select-Object -First 1).Line.Trim()
$env:GOOGLE_API_KEY = "not-used-openai-runner-gate"
.\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy growth-execution-test --start 2024-09-11 --end 2024-10-07 --max-workers 1 --max-run-attempts 1
```

Expected:

```text
JSON output with "strategy": "growth-execution-test" and "status": "passed"
```

- [ ] **Step 2: Locate newest growth-execution artifact directory**

Run:

```powershell
Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 3 FullName,LastWriteTime
```

Expected:

```text
The newest directory contains growth-execution-test
```

- [ ] **Step 3: Inspect trades**

Run:

```powershell
$latest = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
$trades = Join-Path $latest.FullName "growth-execution-test\trades.csv"
Import-Csv $trades | Format-Table -AutoSize
```

Expected success case:

```text
At least one rotation day has a sell and a later buy in the same backtest date.
```

Expected acceptable blocked case:

```text
No same-day rotation, but the trace has orders_confirm_order with can_continue=false and a clear blocker.
```

- [ ] **Step 4: Inspect confirmation calls in traces**

Run:

```powershell
rg -n "orders_confirm_order|confirmation_status|can_continue|open_after_retries|filled" "$($latest.FullName)\growth-execution-test" -S
```

Expected:

```text
Confirmation results are present for submitted orders.
```

- [ ] **Step 5: Record validation notes**

Create or update:

```text
docs/superpowers/notes/2026-08-07-order-confirmation-market-only-validation.md
```

Write:

```markdown
# Order Confirmation Market-Only Validation

## One-Day Backtest

- Command:
- Artifact:
- Outcome:
- Did execution_agent receive orders_confirm_order:
- Did any order submit:
- If submitted, was it confirmed:

## Rotation Window Backtest

- Command:
- Artifact:
- Outcome:
- Rotation days:
- Same-day sell+buy achieved:
- Confirmation failures:
- Remaining issues:
```

Fill every bullet with the actual observed value from Steps 1-4.

- [ ] **Step 6: Commit validation notes**

Run:

```powershell
git add docs\superpowers\notes\2026-08-07-order-confirmation-market-only-validation.md
git commit -m "docs: record order confirmation validation results"
```

---

## Final Verification

Run the final focused suite:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py tests\test_ai_trading_team_growth_execution_test.py tests\test_agent_replay_ui_formatters.py -q
```

Run lint:

```powershell
.\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\builtins.py lumibot\components\agents\replay_ui\formatters.py lumibot\example_strategies\ai_trading_team_growth_execution_test.py tests\test_agent_tool_permissions.py tests\test_ai_trading_team_growth_execution_test.py tests\test_agent_replay_ui_formatters.py
```

Inspect branch state:

```powershell
git status --short --branch
git log --oneline -5
```

Expected:

```text
Working tree clean except generated artifacts not tracked by git.
Recent commits include the confirmation tool, market-only strategy, replay formatter, and validation notes.
```

## Acceptance Checklist

- [ ] `orders_confirm_order` exists as a native built-in tool.
- [ ] `orders_confirm_order` confirms exact submitted orders by identifier.
- [ ] Confirmation retries happen inside the tool.
- [ ] Confirmation returns `confirmed`, `can_continue`, attempts, checks, warnings, order details, and account snapshot.
- [ ] `execution_agent` receives `orders_confirm_order`.
- [ ] Execution prompt requires confirmation after every submit.
- [ ] Decision prompt is market-only.
- [ ] Strategy validation blocks non-market execution plans before execution.
- [ ] Replay formatter explains confirmation tool results in human-readable language.
- [ ] Focused tests pass.
- [ ] One-day backtest produces usable trace output.
- [ ] Rotation-window backtest either completes same-day rotations or produces clear confirmation blockers.
