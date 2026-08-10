# Orders Submit And Confirm Stage B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Stage B `orders_submit_and_confirm_order`, a mutating execution tool that submits one preflight-approved order and confirms that same order before returning.

**Architecture:** Add one native built-in order tool in `lumibot.components.agents.builtins` that internally reuses the existing `orders_submit_order` and `orders_confirm_order` bound functions. Keep `orders_preflight_check` as the separate Stage A readiness gate, then shrink the mock quadrant `execution_agent` tool surface from `preflight + submit + confirm` to `preflight + submit_and_confirm`. Add formatter and tests so Agent Replay remains readable.

**Tech Stack:** Python, LumiBot agent built-in tools, pytest, existing AgentManager tool permission system, replay UI formatter helpers, `scripts/run_ai_trading_team_examples_benchmark.py`.

---

## File Structure

- Modify `lumibot/components/agents/builtins.py`
  - Add `ORDERS_SUBMIT_AND_CONFIRM_ORDER_DESCRIPTION`.
  - Add `_submit_and_confirm_blocked_payload`.
  - Add `_order_identifier_from_submit_result`.
  - Add `_bind_submit_and_confirm_order`.
  - Add `BuiltinTools.orders.submit_and_confirm()`.
  - Register the tool in `BuiltinTools.all()`.
- Modify `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
  - Replace visible execution-agent `orders_submit_order` and `orders_confirm_order` tools with `orders_submit_and_confirm_order`.
  - Update execution-agent system prompt and task prompt to use the Stage B path.
- Modify `lumibot/components/agents/replay_ui/formatters.py`
  - Add a human-readable formatter for `orders_submit_and_confirm_order`.
- Modify `tests/test_agent_tool_permissions.py`
  - Add test helpers that can submit and confirm fake orders.
  - Add built-in definition tests.
  - Add readiness, success, and failure-path tests.
  - Update allow-trading permission tests for the new mutating tool.
- Modify `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
  - Update execution-agent tool surface expectations.
  - Update prompt boundary expectations.
  - Keep non-execution agents unable to access order execution tools.
- Modify `tests/test_agent_replay_ui_formatters.py`
  - Add formatter tests for successful, confirmation-blocked, and submit-blocked combined results.
- Modify `lumibot/components/agents/runtime.py`
  - Add `orders_submit_and_confirm_order` to the mutating-order tool set that disables whole-run retries.
- Modify `lumibot/components/agents/manager.py`
  - Teach the execution tool policy prompt that `orders_submit_and_confirm_order` is an execution tool.
- Modify `tests/test_agent_manager.py`
  - Add prompt-policy coverage for execution agents that only receive the Stage B combined tool.

## Task 1: Expand Test Strategy Support For Combined Submit And Confirm

**Files:**
- Modify: `tests/test_agent_tool_permissions.py`

- [ ] **Step 1: Add a fake order class before `_OrderReadinessStrategy`**

Insert this class after `_Strategy` and before `_OrderReadinessStrategy`:

```python
class _OrderReadinessOrder:
    def __init__(self, *, identifier, symbol, quantity, side, asset_type="stock", status="new", order_type="market", time_in_force="day"):
        self.identifier = identifier
        self.status = status
        self.side = side
        self.asset = SimpleNamespace(symbol=symbol, asset_type=asset_type)
        self.quantity = quantity
        self.order_type = order_type
        self.time_in_force = time_in_force
        self.limit_price = None
        self.stop_price = None
        self.stop_limit_price = None
        self.trail_price = None
        self.trail_percent = None
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
```

- [ ] **Step 2: Expand `_OrderReadinessStrategy.__init__`**

Replace `_OrderReadinessStrategy.__init__` with:

```python
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
```

- [ ] **Step 3: Replace `_OrderReadinessStrategy.create_order`**

Replace the existing `create_order` method with:

```python
    def create_order(self, asset, quantity, side, **kwargs):
        self.created_order_count += 1
        return _OrderReadinessOrder(
            identifier=f"test-order-{self.created_order_count}",
            symbol=getattr(asset, "symbol", None),
            quantity=quantity,
            side=side,
            asset_type=getattr(asset, "asset_type", "stock"),
            status="new",
            order_type=kwargs.get("order_type", "market"),
            time_in_force=kwargs.get("time_in_force", "day"),
        )
```

- [ ] **Step 4: Replace `_OrderReadinessStrategy.submit_order` and add `get_order`**

Replace the existing `submit_order` method with this version and add
`get_order` immediately after it:

```python
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
```

- [ ] **Step 5: Run existing submit/preflight tests to check helper compatibility**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py::test_orders_preflight_check_authorizes_same_exact_submit_order tests\test_agent_tool_permissions.py::test_orders_preflight_check_authorizes_only_one_matching_submit_order tests\test_agent_tool_permissions.py::test_agent_order_tool_allows_buy_that_keeps_cash_positive -q
```

Expected: PASS. If a test fails because the expected status changed from `new`
to `fill`, adjust only that test's status assertion to accept the submitted
order payload still being structurally valid.

- [ ] **Step 6: Commit helper-only compatibility changes**

```powershell
git add tests\test_agent_tool_permissions.py
git commit -m "test: prepare order tool fakes for submit and confirm"
```

## Task 2: Write Failing Tests For `orders_submit_and_confirm_order`

**Files:**
- Modify: `tests/test_agent_tool_permissions.py`

- [ ] **Step 1: Add built-in definition tests after `test_builtin_order_tools_expose_preflight_definition`**

Add:

```python
def test_builtin_order_tools_expose_submit_and_confirm_definition():
    tool = BuiltinTools.orders.submit_and_confirm()

    assert tool.name == "orders_submit_and_confirm_order"
    assert "Submit one explicit execution_plan order and confirm" in tool.description
    assert "orders_preflight_check returns can_submit=true" in tool.description
    assert callable(tool.binder)


def test_builtin_tools_all_includes_orders_submit_and_confirm_order():
    assert "orders_submit_and_confirm_order" in {tool.name for tool in BuiltinTools.all()}


def test_bound_submit_and_confirm_metadata_marks_mutating_and_replayable():
    strategy = _OrderReadinessStrategy()
    manager = AgentManager(strategy)

    tool = BuiltinTools.orders.submit_and_confirm().binder(strategy, manager)

    assert tool.metadata["kind"] == "builtin"
    assert tool.metadata["replay_on_cache"] is True
    assert tool.metadata["mutates_trading"] is True
```

- [ ] **Step 2: Add a wrapper helper after `_wrap_preflight_and_submit_tools`**

Find `_wrap_preflight_and_submit_tools` and add:

```python
def _wrap_preflight_and_submit_confirm_tools(strategy):
    return _wrap_builtin_tools(
        strategy,
        [
            BuiltinTools.orders.preflight(),
            BuiltinTools.orders.submit_and_confirm(),
        ],
    )
```

- [ ] **Step 3: Add success-path tests after preflight authorization tests**

Add:

```python
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
```

- [ ] **Step 4: Add readiness failure tests**

Add:

```python
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


def test_orders_submit_and_confirm_order_rejects_different_symbol_side_quantity_and_order_style():
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
    different_symbol = tool_map["orders_submit_and_confirm_order"](symbol="QQQ", quantity=1, side="buy")
    different_side = tool_map["orders_submit_and_confirm_order"](symbol="SPY", quantity=1, side="sell")
    different_quantity = tool_map["orders_submit_and_confirm_order"](symbol="SPY", quantity=2, side="buy")
    different_order_type = tool_map["orders_submit_and_confirm_order"](
        symbol="SPY",
        quantity=1,
        side="buy",
        order_type="limit",
        limit_price=101.0,
    )

    assert preflight_result["can_submit"] is True
    for result in (different_symbol, different_side, different_quantity, different_order_type):
        assert result["submitted"] is False
        assert result["confirmed"] is False
        assert result["can_continue"] is False
        assert result["blockers"][0]["code"] == "ORDER_READINESS_REQUIRED"
    assert strategy.submitted_orders == []
```

- [ ] **Step 5: Add submit and confirmation failure tests**

Add:

```python
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
```

- [ ] **Step 6: Run the new tests and verify they fail for missing tool**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py::test_builtin_order_tools_expose_submit_and_confirm_definition tests\test_agent_tool_permissions.py::test_orders_submit_and_confirm_order_submits_and_confirms_after_matching_preflight tests\test_agent_tool_permissions.py::test_orders_submit_and_confirm_order_requires_preflight_before_submit -q
```

Expected: FAIL with `AttributeError: '_OrderTools' object has no attribute 'submit_and_confirm'`.

- [ ] **Step 7: Commit failing tests**

```powershell
git add tests\test_agent_tool_permissions.py
git commit -m "test: cover submit and confirm order tool"
```

## Task 3: Implement The Combined Submit-And-Confirm Tool

**Files:**
- Modify: `lumibot/components/agents/builtins.py`

- [ ] **Step 1: Add the tool description constant after `ORDERS_PREFLIGHT_CHECK_DESCRIPTION`**

Insert:

```python
ORDERS_SUBMIT_AND_CONFIRM_ORDER_DESCRIPTION = (
    "Submit one explicit execution_plan order and confirm that same submitted order before returning. "
    "Use this after orders_preflight_check returns can_submit=true for the same order. "
    "This tool mutates trading state. It does not perform research, calculate quantities, change order fields, "
    "or execute more than one order. If the result has can_continue=false, stop later orders and report the blocker."
)
```

- [ ] **Step 2: Add helper functions before `_bind_submit_order`**

Insert these helpers immediately before `_bind_submit_order`:

```python
def _order_identifier_from_submit_result(submit_result: dict[str, Any]) -> str | None:
    order = submit_result.get("order") if isinstance(submit_result, dict) else None
    if not isinstance(order, dict):
        return None
    for key in ("identifier", "id", "order_id"):
        value = order.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _submit_and_confirm_blocker_from_exception(exc: Exception) -> dict[str, str]:
    message = str(exc)
    code = "SUBMIT_FAILED"
    if message.startswith("ORDER_READINESS_REQUIRED"):
        code = "ORDER_READINESS_REQUIRED"
    elif message.startswith("NEGATIVE_CASH_NOT_ALLOWED"):
        code = "NEGATIVE_CASH_NOT_ALLOWED"
    elif message.startswith("NEGATIVE_CASH_CHECK_UNAVAILABLE"):
        code = "NEGATIVE_CASH_CHECK_UNAVAILABLE"
    elif "requires" in message.lower():
        code = "INVALID_ORDER_ARGUMENTS"
    return {"code": code, "message": message}


def _submit_and_confirm_blocked_payload(
    *,
    sequence: Any,
    symbol: Any,
    side: Any,
    quantity: Any,
    asset_type: Any,
    order_type: Any,
    time_in_force: Any,
    blockers: list[dict[str, str]],
    warnings: list[str] | None = None,
    submit_result: dict[str, Any] | None = None,
    confirm_result: dict[str, Any] | None = None,
    identifier: str | None = None,
    internal_steps: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "sequence": _jsonable(sequence),
        "symbol": _jsonable(symbol),
        "side": _jsonable(side),
        "quantity": _jsonable(quantity),
        "asset_type": _jsonable(asset_type),
        "order_type": _jsonable(order_type),
        "time_in_force": _jsonable(time_in_force),
        "submitted": submit_result is not None,
        "confirmed": False,
        "can_continue": False,
        "confirmation_status": (
            confirm_result.get("confirmation_status")
            if isinstance(confirm_result, dict)
            else None
        ),
        "identifier": identifier,
        "submit_result": submit_result,
        "confirm_result": confirm_result,
        "internal_steps": internal_steps or [],
        "warnings": list(warnings or []),
        "blockers": blockers,
    }
```

- [ ] **Step 3: Add `_bind_submit_and_confirm_order` before `_bind_submit_order`**

Insert:

```python
def _bind_submit_and_confirm_order(strategy: Any, manager: Any) -> BoundTool:
    submit_tool = _bind_submit_order(strategy, manager)
    confirm_tool = _bind_confirm_order(strategy, manager)

    def submit_and_confirm_order(
        *,
        symbol: str,
        quantity: float,
        side: OrderSideArg,
        sequence: Any = None,
        asset_type: AssetTypeArg = "stock",
        expiration: str | None = None,
        strike: float | None = None,
        right: str | None = None,
        order_type: OrderTypeArg = "market",
        limit_price: float | None = None,
        stop_price: float | None = None,
        stop_limit_price: float | None = None,
        trail_price: float | None = None,
        trail_percent: float | None = None,
        quote_symbol: str | None = None,
        exchange: str | None = None,
        time_in_force: TimeInForceArg = "day",
        confirmation_max_attempts: int = 3,
        confirmation_wait_seconds: float | None = None,
    ) -> dict[str, Any]:
        internal_steps: list[str] = ["orders_submit_order"]
        submit_kwargs = {
            "symbol": symbol,
            "quantity": quantity,
            "side": side,
            "asset_type": asset_type,
            "expiration": expiration,
            "strike": strike,
            "right": right,
            "order_type": order_type,
            "limit_price": limit_price,
            "stop_price": stop_price,
            "stop_limit_price": stop_limit_price,
            "trail_price": trail_price,
            "trail_percent": trail_percent,
            "quote_symbol": quote_symbol,
            "exchange": exchange,
            "time_in_force": time_in_force,
        }
        try:
            submit_result = submit_tool.function(**submit_kwargs)
        except Exception as exc:
            return _submit_and_confirm_blocked_payload(
                sequence=sequence,
                symbol=symbol,
                side=side,
                quantity=quantity,
                asset_type=asset_type,
                order_type=order_type,
                time_in_force=time_in_force,
                blockers=[_submit_and_confirm_blocker_from_exception(exc)],
                submit_result=None,
                confirm_result=None,
                internal_steps=internal_steps,
            )

        identifier = _order_identifier_from_submit_result(submit_result)
        if not identifier:
            return _submit_and_confirm_blocked_payload(
                sequence=sequence,
                symbol=symbol,
                side=side,
                quantity=quantity,
                asset_type=asset_type,
                order_type=order_type,
                time_in_force=time_in_force,
                blockers=[
                    {
                        "code": "ORDER_IDENTIFIER_MISSING",
                        "message": "Order was submitted but no identifier was returned for confirmation.",
                    }
                ],
                submit_result=submit_result,
                confirm_result=None,
                identifier=None,
                internal_steps=internal_steps,
            )

        internal_steps.append("orders_confirm_order")
        confirm_result = confirm_tool.function(
            identifier=identifier,
            symbol=symbol,
            side=side,
            expected_quantity=quantity,
            max_attempts=confirmation_max_attempts,
            wait_seconds=confirmation_wait_seconds,
        )
        warnings = confirm_result.get("warnings") if isinstance(confirm_result.get("warnings"), list) else []
        if confirm_result.get("confirmed") is not True or confirm_result.get("can_continue") is not True:
            return _submit_and_confirm_blocked_payload(
                sequence=sequence,
                symbol=symbol,
                side=side,
                quantity=quantity,
                asset_type=asset_type,
                order_type=order_type,
                time_in_force=time_in_force,
                blockers=[
                    {
                        "code": "CONFIRMATION_FAILED",
                        "message": "Order was submitted but not confirmed. Stop later orders.",
                    }
                ],
                warnings=warnings,
                submit_result=submit_result,
                confirm_result=confirm_result,
                identifier=identifier,
                internal_steps=internal_steps,
            )

        return {
            "sequence": _jsonable(sequence),
            "symbol": _jsonable(symbol),
            "side": _jsonable(side),
            "quantity": _jsonable(quantity),
            "asset_type": _jsonable(asset_type),
            "order_type": _jsonable(order_type),
            "time_in_force": _jsonable(time_in_force),
            "submitted": True,
            "confirmed": True,
            "can_continue": True,
            "confirmation_status": confirm_result.get("confirmation_status"),
            "identifier": identifier,
            "submit_result": submit_result,
            "confirm_result": confirm_result,
            "internal_steps": internal_steps,
            "warnings": warnings,
            "blockers": [],
        }

    return BoundTool(
        name="orders_submit_and_confirm_order",
        description=ORDERS_SUBMIT_AND_CONFIRM_ORDER_DESCRIPTION,
        function=submit_and_confirm_order,
        metadata={"kind": "builtin", "replay_on_cache": True, "mutates_trading": True},
    )
```

- [ ] **Step 4: Add `_OrderTools.submit_and_confirm`**

In class `_OrderTools`, add this method after `preflight()` and before
`submit()`:

```python
    def submit_and_confirm(self) -> ToolDefinition:
        return ToolDefinition(
            name="orders_submit_and_confirm_order",
            description=ORDERS_SUBMIT_AND_CONFIRM_ORDER_DESCRIPTION,
            binder=_bind_submit_and_confirm_order,
            metadata={"mutates_trading": True},
        )
```

- [ ] **Step 5: Register the tool in `_BuiltinTools.all()`**

Find the order tool registrations in `BuiltinTools.all()` and add:

```python
            self.orders.submit_and_confirm(),
```

immediately after:

```python
            self.orders.preflight(),
```

- [ ] **Step 6: Run Stage B tool tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py::test_builtin_order_tools_expose_submit_and_confirm_definition tests\test_agent_tool_permissions.py::test_builtin_tools_all_includes_orders_submit_and_confirm_order tests\test_agent_tool_permissions.py::test_bound_submit_and_confirm_metadata_marks_mutating_and_replayable tests\test_agent_tool_permissions.py::test_orders_submit_and_confirm_order_submits_and_confirms_after_matching_preflight tests\test_agent_tool_permissions.py::test_orders_submit_and_confirm_order_consumes_preflight_token tests\test_agent_tool_permissions.py::test_orders_submit_and_confirm_order_requires_preflight_before_submit tests\test_agent_tool_permissions.py::test_orders_submit_and_confirm_order_rejects_blocked_preflight tests\test_agent_tool_permissions.py::test_orders_submit_and_confirm_order_rejects_different_symbol_side_quantity_and_order_style tests\test_agent_tool_permissions.py::test_orders_submit_and_confirm_order_blocks_negative_cash_before_confirmation tests\test_agent_tool_permissions.py::test_orders_submit_and_confirm_order_returns_blocker_when_confirmation_fails -q
```

Expected: PASS.

- [ ] **Step 7: Commit implementation**

```powershell
git add lumibot\components\agents\builtins.py tests\test_agent_tool_permissions.py
git commit -m "feat: add submit and confirm order tool"
```

## Task 4: Update Mutating Tool Permissions And Retry Behavior

**Files:**
- Modify: `tests/test_agent_tool_permissions.py`
- Modify: `tests/test_agent_runtime_provider_keys.py`
- Modify: `tests/test_agent_manager.py`
- Modify: `lumibot/components/agents/runtime.py`
- Modify: `lumibot/components/agents/manager.py`

- [ ] **Step 1: Update allow-trading permission tests**

In `test_agent_allow_trading_false_removes_only_mutating_order_tools`, add:

```python
    assert "orders_submit_and_confirm_order" not in tool_names
```

In `test_agent_allow_trading_true_keeps_mutating_order_tools`, add:

```python
    assert "orders_submit_and_confirm_order" in tool_names
```

- [ ] **Step 2: Update provider retry test to include the new mutating tool**

In `tests/test_agent_runtime_provider_keys.py`, find
`test_mutating_order_tools_disable_whole_run_retries_by_default`. Add a bound
tool with the new name to the list used by the test:

```python
            BoundTool(name="orders_submit_and_confirm_order", description="submit and confirm", function=lambda: {"ok": True}),
```

If the test parametrizes one tool at a time instead of listing several tools,
add a second test named:

```python
def test_submit_and_confirm_order_tool_disables_whole_run_retries_by_default(monkeypatch):
    ...
```

and mirror the existing `orders_submit_order` assertion with the new tool
name.

- [ ] **Step 3: Run permission/retry tests and verify failure if runtime is not updated**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py::test_agent_allow_trading_false_removes_only_mutating_order_tools tests\test_agent_tool_permissions.py::test_agent_allow_trading_true_keeps_mutating_order_tools tests\test_agent_runtime_provider_keys.py::test_mutating_order_tools_disable_whole_run_retries_by_default -q
```

Expected: permission tests may pass after metadata changes; retry test should
FAIL until runtime recognizes `orders_submit_and_confirm_order` as mutating.

- [ ] **Step 4: Update mutating-order tool set**

In `lumibot/components/agents/runtime.py`, find:

```python
        mutating_order_tools = {"orders_submit_order", "orders_cancel_order", "orders_modify_order"}
```

Replace it with:

```python
        mutating_order_tools = {
            "orders_submit_order",
            "orders_submit_and_confirm_order",
            "orders_cancel_order",
            "orders_modify_order",
        }
```

- [ ] **Step 5: Run permission/retry tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py::test_agent_allow_trading_false_removes_only_mutating_order_tools tests\test_agent_tool_permissions.py::test_agent_allow_trading_true_keeps_mutating_order_tools tests\test_agent_runtime_provider_keys.py::test_mutating_order_tools_disable_whole_run_retries_by_default -q
```

Expected: PASS.

- [ ] **Step 6: Add manager policy test for the combined tool**

In `tests/test_agent_manager.py`, add this test after
`test_execution_agent_with_order_tools_does_not_receive_history_policy`:

```python
def test_execution_agent_with_submit_and_confirm_tool_receives_execution_policy():
    def orders_submit_and_confirm_order():
        return None

    handle = AgentHandle(
        manager=DummyManager(),
        name="execution_agent",
        system_prompt="Execution role.",
        default_model="test-model",
        runtime=object(),
        tools=[orders_submit_and_confirm_order],
        include_builtin_tools=False,
        base_system_prompt_mode="execution_minimal",
    )

    prompt = handle._compose_system_prompt(
        {"mode": "backtesting"},
        bound_tools=handle._ensure_bound_tools(),
    )
    prompt_lower = prompt.lower()

    assert "execution tool policy" in prompt_lower
    assert "orders_submit_and_confirm_order submits and confirms explicit order fields" in prompt_lower
    assert "price/history tool policy" not in prompt_lower
```

- [ ] **Step 7: Run manager policy test and verify failure**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py::test_execution_agent_with_submit_and_confirm_tool_receives_execution_policy -q
```

Expected: FAIL because `orders_submit_and_confirm_order` is not yet included
in `_execution_tool_policy_prompt()`.

- [ ] **Step 8: Update `AgentHandle._execution_tool_policy_prompt`**

In `lumibot/components/agents/manager.py`, find:

```python
        execution_tools = {
            "orders_submit_order",
            "orders_cancel_order",
            "orders_modify_order",
            "orders_open_orders",
        }
```

Replace it with:

```python
        execution_tools = {
            "orders_submit_order",
            "orders_submit_and_confirm_order",
            "orders_cancel_order",
            "orders_modify_order",
            "orders_open_orders",
        }
```

Then after:

```python
        if "orders_submit_order" in tool_names:
            lines.append("orders_submit_order executes explicit order fields from execution_plan.orders.")
```

insert:

```python
        if "orders_submit_and_confirm_order" in tool_names:
            lines.append(
                "orders_submit_and_confirm_order submits and confirms explicit order fields from execution_plan.orders."
            )
```

- [ ] **Step 9: Run manager policy test**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py::test_execution_agent_with_submit_and_confirm_tool_receives_execution_policy -q
```

Expected: PASS.

- [ ] **Step 10: Commit permission, retry, and manager policy behavior**

```powershell
git add lumibot\components\agents\runtime.py lumibot\components\agents\manager.py tests\test_agent_tool_permissions.py tests\test_agent_runtime_provider_keys.py tests\test_agent_manager.py
git commit -m "fix: treat submit and confirm as mutating order tool"
```

## Task 5: Add Replay UI Formatter For The Combined Tool

**Files:**
- Modify: `tests/test_agent_replay_ui_formatters.py`
- Modify: `lumibot/components/agents/replay_ui/formatters.py`

- [ ] **Step 1: Add formatter tests after `test_orders_confirm_order_formatter_explains_blocked_confirmation`**

Add:

```python
def test_orders_submit_and_confirm_order_formatter_explains_success():
    text = explain_tool_result(
        "orders_submit_and_confirm_order",
        {"symbol": "SPY", "side": "buy", "quantity": 2},
        {
            "symbol": "SPY",
            "side": "buy",
            "quantity": 2,
            "submitted": True,
            "confirmed": True,
            "can_continue": True,
            "confirmation_status": "filled",
            "identifier": "order-123",
            "submit_result": {
                "order": {
                    "identifier": "order-123",
                    "status": "fill",
                    "side": "buy",
                    "quantity": 2,
                    "asset": {"symbol": "SPY", "asset_type": "stock"},
                }
            },
            "confirm_result": {"attempt_count": 1, "confirmed": True, "can_continue": True},
            "warnings": [],
            "blockers": [],
        },
        None,
    )

    assert "Submitted and confirmed order order-123" in text
    assert "buy 2 SPY" in text
    assert "filled" in text
    assert "1 attempt" in text
    assert "can_continue=true" in text


def test_orders_submit_and_confirm_order_formatter_explains_confirmation_blocker():
    text = explain_tool_result(
        "orders_submit_and_confirm_order",
        {"symbol": "SPY", "side": "buy", "quantity": 2},
        {
            "symbol": "SPY",
            "side": "buy",
            "quantity": 2,
            "submitted": True,
            "confirmed": False,
            "can_continue": False,
            "confirmation_status": "open_after_retries",
            "identifier": "order-123",
            "confirm_result": {"attempt_count": 3, "warnings": ["Order remained active."]},
            "warnings": ["Order remained active."],
            "blockers": [{"code": "CONFIRMATION_FAILED", "message": "Order was submitted but not confirmed."}],
        },
        None,
    )

    assert "Submit-and-confirm blocked" in text
    assert "buy 2 SPY" in text
    assert "open_after_retries" in text
    assert "can_continue=false" in text
    assert "CONFIRMATION_FAILED" in text


def test_orders_submit_and_confirm_order_formatter_explains_submit_blocker():
    text = explain_tool_result(
        "orders_submit_and_confirm_order",
        {"symbol": "SPY", "side": "buy", "quantity": 2},
        {
            "symbol": "SPY",
            "side": "buy",
            "quantity": 2,
            "submitted": False,
            "confirmed": False,
            "can_continue": False,
            "identifier": None,
            "blockers": [{"code": "ORDER_READINESS_REQUIRED", "message": "Missing matching preflight."}],
        },
        None,
    )

    assert "Submit-and-confirm blocked" in text
    assert "buy 2 SPY" in text
    assert "ORDER_READINESS_REQUIRED" in text
    assert "Missing matching preflight" in text
```

- [ ] **Step 2: Run formatter tests and verify failure**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_formatters.py::test_orders_submit_and_confirm_order_formatter_explains_success tests\test_agent_replay_ui_formatters.py::test_orders_submit_and_confirm_order_formatter_explains_confirmation_blocker tests\test_agent_replay_ui_formatters.py::test_orders_submit_and_confirm_order_formatter_explains_submit_blocker -q
```

Expected: FAIL because no formatter is registered yet.

- [ ] **Step 3: Add formatter function after `_orders_confirm_order`**

In `lumibot/components/agents/replay_ui/formatters.py`, insert:

```python
def _orders_submit_and_confirm_order(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    submit_result = _nested_dict(result, "submit_result")
    submit_order = _nested_dict(submit_result, "order") if submit_result else {}
    symbol = _asset_symbol(submit_order) or _first_present(result, "symbol") or _first_present(args, "symbol")
    qty = (
        _first_present(submit_order, "qty", "quantity", "shares")
        or _first_present(result, "quantity", "qty", "shares")
        or _first_present(args, "quantity", "qty", "shares")
    )
    side = (
        _first_present(submit_order, "side", "action")
        or _first_present(result, "side", "action")
        or _first_present(args, "side", "action")
    )
    order_id = (
        _first_present(result, "identifier", "id", "order_id")
        or _first_present(submit_order, "identifier", "id", "order_id")
    )
    status = _first_present(result, "confirmation_status", "status")
    confirm_result = _nested_dict(result, "confirm_result")
    attempt_count = _first_present(confirm_result, "attempt_count", "attempts_count")
    blockers = result.get("blockers") if isinstance(result.get("blockers"), list) else []
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    order_text = f"{_text(side)} {_text(qty)} {_text(symbol)}"
    attempts_text = ""
    if attempt_count is not None:
        attempt_label = "attempt" if attempt_count == 1 else "attempts"
        attempts_text = f" after {_text(attempt_count)} {attempt_label}"
    if result.get("submitted") is True and result.get("confirmed") is True and result.get("can_continue") is True:
        return (
            f"Submitted and confirmed order {_text(order_id)} for {order_text}{attempts_text}. "
            f"Status: {_text(status)}. can_continue=true."
        )
    blocker_text = ""
    if blockers:
        first = _as_dict(blockers[0])
        code = _first_present(first, "code")
        message = _first_present(first, "message")
        blocker_text = f" Blocker: {_text(code)}"
        if message is not None:
            blocker_text += f" - {_text(message)}"
    elif warnings:
        blocker_text = f" Warning: {_text(warnings[0])}"
    return (
        f"Submit-and-confirm blocked for {order_text}{attempts_text}. "
        f"Status: {_text(status)}. can_continue=false.{blocker_text}"
    )
```

- [ ] **Step 4: Register formatter**

Add to `_FORMATTERS` after `"orders_confirm_order": _orders_confirm_order,`:

```python
    "orders_submit_and_confirm_order": _orders_submit_and_confirm_order,
```

- [ ] **Step 5: Run formatter tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_formatters.py::test_orders_submit_and_confirm_order_formatter_explains_success tests\test_agent_replay_ui_formatters.py::test_orders_submit_and_confirm_order_formatter_explains_confirmation_blocker tests\test_agent_replay_ui_formatters.py::test_orders_submit_and_confirm_order_formatter_explains_submit_blocker -q
```

Expected: PASS.

- [ ] **Step 6: Commit formatter**

```powershell
git add lumibot\components\agents\replay_ui\formatters.py tests\test_agent_replay_ui_formatters.py
git commit -m "feat: explain submit and confirm in replay UI"
```

## Task 6: Route Mock Quadrant Execution Through Stage B Tool

**Files:**
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Update execution-agent tool surface expectation**

In `test_agents_receive_distinct_tool_surfaces`, replace:

```python
    assert created_tool_names(created["execution_agent"]) == {
        "orders_preflight_check",
        "orders_submit_order",
        "orders_confirm_order",
    }
```

with:

```python
    assert created_tool_names(created["execution_agent"]) == {
        "orders_preflight_check",
        "orders_submit_and_confirm_order",
    }
```

- [ ] **Step 2: Update non-execution tool assertions**

In the same test, after the existing assertions that non-execution agents lack
submit and confirm tools, add:

```python
        assert "orders_submit_and_confirm_order" not in created_tool_names(non_execution_agent)
```

- [ ] **Step 3: Update prompt boundary test expectations**

In `test_prompt_boundaries_are_short_and_role_specific`, add to the
`forbidden_phrase` tuple:

```python
        "then confirm it with orders_confirm_order",
        "then call orders_confirm_order",
        "call orders_submit_order with that same order",
```

Replace the positive Stage A assertion:

```python
    assert "call orders_preflight_check before each order" in serialized
```

with:

```python
    assert "call orders_preflight_check" in serialized
    assert "orders_submit_and_confirm_order" in serialized
    assert "combined tool returns can_continue=true" in serialized
```

- [ ] **Step 4: Update execution context/task tests**

Find the test that asserts execution task prompt includes:

```python
"orders_submit_order"
"orders_confirm_order"
```

Replace those assertions with:

```python
    assert "orders_preflight_check" in execution_task_prompt
    assert "orders_submit_and_confirm_order" in execution_task_prompt
    assert "orders_submit_order" not in execution_task_prompt
    assert "orders_confirm_order" not in execution_task_prompt
```

- [ ] **Step 5: Run strategy tests and verify failure before implementation**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_agents_receive_distinct_tool_surfaces tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_prompt_boundaries_are_short_and_role_specific tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_on_trading_iteration_runs_agents_in_expected_order_and_context -q
```

Expected: FAIL because the strategy still exposes separate submit and confirm
tools and prompts.

- [ ] **Step 6: Update execution-agent tool list in strategy initialization**

In `AITradingTeamMockGrowthInflationQuadrantStrategy.initialize()`, replace:

```python
            tools=[
                BuiltinTools.orders.preflight(),
                BuiltinTools.orders.submit(),
                BuiltinTools.orders.confirm(),
            ],
```

with:

```python
            tools=[
                BuiltinTools.orders.preflight(),
                BuiltinTools.orders.submit_and_confirm(),
            ],
```

- [ ] **Step 7: Update execution-agent system prompt**

Replace the execution-agent `system_prompt` with:

```python
            system_prompt=(
                "Execution role: execute only the provided execution_plan using the listed order tools. "
                "For each order in sequence, call orders_preflight_check with the exact order fields. "
                "If preflight returns can_submit=true, call orders_submit_and_confirm_order with that same order. "
                "Continue to the next order only when the combined tool returns can_continue=true. "
                "If preflight returns can_submit=false or the combined tool returns can_continue=false, stop "
                "remaining orders and report the blocker. Do not perform investment research, do not change "
                "order fields, and do not call lower-level submit or confirm tools when the combined tool is available."
            ),
```

- [ ] **Step 8: Update execution-agent task prompt**

In `on_trading_iteration()`, replace the execution-agent `task_prompt` with:

```python
                "Execute the provided execution_plan in sequence order. For each order, call orders_preflight_check "
                "with the exact order fields. If preflight allows it, call orders_submit_and_confirm_order with "
                "that same order. Stop if any preflight or submit-and-confirm result blocks continuation."
```

- [ ] **Step 9: Run strategy prompt/tool tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_agents_receive_distinct_tool_surfaces tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_prompt_boundaries_are_short_and_role_specific tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_on_trading_iteration_runs_agents_in_expected_order_and_context -q
```

Expected: PASS.

- [ ] **Step 10: Commit strategy route**

```powershell
git add lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: route mock quadrant through submit and confirm tool"
```

## Task 7: Focused Regression Tests And Lint

**Files:**
- Verify only.

- [ ] **Step 1: Run focused pytest suite**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_replay_ui_formatters.py tests\test_agent_runtime_provider_keys.py tests\test_agent_manager.py -q
```

Expected: PASS.

- [ ] **Step 2: Run focused ruff check**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\builtins.py lumibot\components\agents\runtime.py lumibot\components\agents\manager.py lumibot\components\agents\replay_ui\formatters.py lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_tool_permissions.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_replay_ui_formatters.py tests\test_agent_runtime_provider_keys.py tests\test_agent_manager.py
```

Expected: PASS.

- [ ] **Step 3: Commit lint-only fixes if needed**

If Step 2 required import cleanup or formatting changes, commit those changes:

```powershell
git add lumibot\components\agents\builtins.py lumibot\components\agents\runtime.py lumibot\components\agents\manager.py lumibot\components\agents\replay_ui\formatters.py lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_tool_permissions.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_replay_ui_formatters.py tests\test_agent_runtime_provider_keys.py tests\test_agent_manager.py
git commit -m "chore: clean up submit and confirm stage lint"
```

If there are no source changes after lint, do not create an empty commit.

## Task 8: One-Day Benchmark Validation

**Files:**
- Verify generated artifacts only.

- [ ] **Step 1: Run one-day benchmark**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-05 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file project_notes\API.txt
```

Expected:

- Result status is `passed`.
- The newest artifact under `artifacts\ai_trading_team_example_benchmarks`
  contains `mock-growth-inflation-quadrant`.

- [ ] **Step 2: Inspect one-day execution trace for Stage B tool shape**

Run:

```powershell
$latest = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
rg -n "orders_preflight_check|orders_submit_and_confirm_order|orders_submit_order|orders_confirm_order|ORDER_READINESS_REQUIRED|NEGATIVE_CASH_NOT_ALLOWED" "$($latest.FullName)\mock-growth-inflation-quadrant\cache\agent_runtime\traces\execution_agent" -S
```

Expected:

- `orders_preflight_check` appears before each submitted order.
- `orders_submit_and_confirm_order` appears after ready preflight calls.
- `orders_submit_order` and `orders_confirm_order` do not appear as
  model-facing execution-agent tool calls.
- `ORDER_READINESS_REQUIRED` does not appear.
- `NEGATIVE_CASH_NOT_ALLOWED` does not appear.

- [ ] **Step 3: Inspect combined tool output**

Run:

```powershell
$latest = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
rg -n "\"submitted\"|\"confirmed\"|\"can_continue\"|\"submit_result\"|\"confirm_result\"|\"internal_steps\"" "$($latest.FullName)\mock-growth-inflation-quadrant\cache\agent_runtime\traces\execution_agent" -S
```

Expected:

- Combined tool result includes `submitted`, `confirmed`, `can_continue`,
  `submit_result`, `confirm_result`, and `internal_steps`.
- At least one successful order has `submitted=true`, `confirmed=true`, and
  `can_continue=true`.

## Task 9: Two-Day Rebalance Benchmark Validation

**Files:**
- Verify generated artifacts only.

- [ ] **Step 1: Run two-day benchmark**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-06 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file project_notes\API.txt
```

Expected:

- Result status is `passed`.
- If the generated plan includes sells followed by buys, same-day sequencing
  completes.
- No negative-cash regression appears.

- [ ] **Step 2: Inspect two-day execution trace for Stage B shape**

Run:

```powershell
$latest = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
rg -n "orders_preflight_check|orders_submit_and_confirm_order|orders_submit_order|orders_confirm_order|can_continue|CONFIRMATION_FAILED|ORDER_READINESS_REQUIRED|NEGATIVE_CASH_NOT_ALLOWED" "$($latest.FullName)\mock-growth-inflation-quadrant\cache\agent_runtime\traces\execution_agent" -S
```

Expected:

- Each order uses `orders_preflight_check` then
  `orders_submit_and_confirm_order`.
- There are no separate model-facing `orders_submit_order` or
  `orders_confirm_order` calls.
- Later orders only continue after combined tool results have
  `can_continue=true`.
- `ORDER_READINESS_REQUIRED`, `NEGATIVE_CASH_NOT_ALLOWED`, and
  unexplained `CONFIRMATION_FAILED` do not appear.

- [ ] **Step 3: Compare Stage B order-call count**

Run:

```powershell
$latest = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$traceRoot = Join-Path $latest.FullName "mock-growth-inflation-quadrant\cache\agent_runtime\traces\execution_agent"
Write-Host "preflight:" (rg -c "orders_preflight_check" $traceRoot -S | Measure-Object -Sum | Select-Object -ExpandProperty Sum)
Write-Host "submit_and_confirm:" (rg -c "orders_submit_and_confirm_order" $traceRoot -S | Measure-Object -Sum | Select-Object -ExpandProperty Sum)
Write-Host "separate_submit:" (rg -c "orders_submit_order" $traceRoot -S | Measure-Object -Sum | Select-Object -ExpandProperty Sum)
Write-Host "separate_confirm:" (rg -c "orders_confirm_order" $traceRoot -S | Measure-Object -Sum | Select-Object -ExpandProperty Sum)
```

Expected:

- `submit_and_confirm` count is greater than zero.
- Any `orders_submit_order` or `orders_confirm_order` text should appear only
  inside `internal_steps` or nested result data, not as model-facing tool
  names.

## Task 10: Final Verification And Branch Report

**Files:**
- Verify only.

- [ ] **Step 1: Run final focused verification**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_replay_ui_formatters.py tests\test_agent_runtime_provider_keys.py tests\test_agent_manager.py -q
D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\builtins.py lumibot\components\agents\runtime.py lumibot\components\agents\manager.py lumibot\components\agents\replay_ui\formatters.py lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_tool_permissions.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_replay_ui_formatters.py tests\test_agent_runtime_provider_keys.py tests\test_agent_manager.py
git diff --check
git status --short --branch
```

Expected:

- pytest exits 0.
- ruff exits 0.
- `git diff --check` has no output.
- `git status` shows no unexpected unstaged source changes.

- [ ] **Step 2: Summarize implementation**

The final implementation response should include:

- commits created
- tests run
- one-day benchmark artifact ID
- two-day benchmark artifact ID
- whether Stage B tool-call shape matched the expected
  `preflight -> submit_and_confirm` pattern
- whether any blocker appeared
- whether Replay UI formatter can explain combined results
- residual risk around live broker pending orders and partial fills

## Self-Review Checklist

- Spec coverage:
  - Tool definition, input schema, internal flow, readiness guard, negative-cash
    protection, confirmation gate, output schema, tool visibility, prompts,
    Replay UI formatter, replay cache metadata, tests, and benchmarks all map
    to tasks above.
- Placeholder scan:
  - No unfinished draft markers were found during plan self-review.
- Type consistency:
  - The plan uses `orders_submit_and_confirm_order`,
    `confirmation_max_attempts`, `confirmation_wait_seconds`, `submit_result`,
    `confirm_result`, `internal_steps`, `blockers`, `warnings`,
    `submitted`, `confirmed`, and `can_continue` consistently.
- Scope:
  - This plan implements Stage B only. It does not implement Stage C
    `orders_execute_order` or Stage D `execution_plan_execute`.
