# Orders Preflight Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Stage A `orders_preflight_check`, a read-only execution preflight tool that packages account, position, open-order, and price checks before `orders_submit_order`.

**Architecture:** Add one native built-in tool in `lumibot.components.agents.builtins`, expose it to the mock growth/inflation quadrant `execution_agent`, and make successful same-symbol preflight satisfy the existing order readiness guard. Keep `orders_submit_order` and `orders_confirm_order` separate; this stage only compresses pre-order inspection.

**Tech Stack:** Python, LumiBot agent built-in tools, pytest, existing replay UI formatter helpers, `scripts/run_ai_trading_team_examples_benchmark.py`.

---

## File Structure

- Modify `lumibot/components/agents/builtins.py`
  - Add preflight readiness helpers.
  - Add `_bind_preflight_check`.
  - Add `BuiltinTools.orders.preflight()`.
  - Update `orders_submit_order` description to mention the preferred Stage A readiness path.
- Modify `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
  - Give `execution_agent` `orders_preflight_check`, `orders_submit_order`, and `orders_confirm_order`.
  - Remove direct lower-level account/price/open-order tools from that agent's visible tool list.
  - Update the execution-agent system prompt and task prompt to use preflight before submit.
- Modify `lumibot/components/agents/replay_ui/formatters.py`
  - Add a human-readable formatter for `orders_preflight_check`.
- Modify `tests/test_agent_tool_permissions.py`
  - Add preflight unit tests.
  - Add readiness guard compatibility tests.
  - Add built-in tool definition tests.
- Modify `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
  - Update tool surface expectations.
  - Add prompt expectation checks.
- Modify `tests/test_agent_replay_ui_formatters.py`
  - Add formatter tests for ready and blocked preflight results.

## Task 1: Write Failing Tests For The Built-In Preflight Tool

**Files:**
- Modify: `tests/test_agent_tool_permissions.py`

- [ ] **Step 1: Update the fake order-readiness strategy**

In `tests/test_agent_tool_permissions.py`, replace `_OrderReadinessStrategy` with this expanded version:

```python
class _OrderReadinessStrategy(_Strategy):
    def __init__(self):
        self.submitted_orders = []
        self.positions = []
        self.open_orders = []
        self.last_prices = {}
        self.cash = 100000.0
        self.portfolio_value = 100000.0

    def get_positions(self, include_cash_positions=True):
        return list(self.positions)

    def get_orders(self):
        return list(self.open_orders)

    def get_cash(self):
        return self.cash

    def get_portfolio_value(self):
        return self.portfolio_value

    def get_last_price(self, asset, quote=None, exchange=None):
        return self.last_prices.get(getattr(asset, "symbol", None), 100.0)

    def create_order(self, asset, quantity, side, **kwargs):
        return SimpleNamespace(
            identifier="test-order",
            status="new",
            side=side,
            asset=asset,
            quantity=quantity,
            order_type=kwargs.get("order_type", "market"),
            time_in_force=kwargs.get("time_in_force", "day"),
            limit_price=kwargs.get("limit_price"),
            stop_price=kwargs.get("stop_price"),
        )

    def submit_order(self, order):
        self.submitted_orders.append(order)
        return order
```

- [ ] **Step 2: Add helper factories after `_wrap_builtin_tools`**

Add these helpers after `_wrap_builtin_tools`:

```python
def _fake_asset(symbol, asset_type="stock"):
    return SimpleNamespace(symbol=symbol, asset_type=asset_type)


def _fake_position(symbol, quantity, market_value=None, current_price=None):
    return SimpleNamespace(
        asset=_fake_asset(symbol),
        quantity=quantity,
        market_value=market_value,
        current_price=current_price,
        avg_fill_price=None,
    )


def _fake_open_order(symbol, quantity=1, side="buy", status="new", identifier="open-order"):
    return SimpleNamespace(
        identifier=identifier,
        status=status,
        side=side,
        asset=_fake_asset(symbol),
        quantity=quantity,
        order_type="market",
        time_in_force="day",
        is_active=lambda: True,
        is_filled=lambda: False,
        is_canceled=lambda: False,
    )
```

- [ ] **Step 3: Add preflight behavior tests after `test_builtin_order_tools_expose_explicit_confirm_definition`**

Append these tests:

```python
def test_builtin_order_tools_expose_preflight_definition():
    tool = BuiltinTools.orders.preflight()

    assert tool.name == "orders_preflight_check"
    assert "Inspect whether one explicit execution_plan order appears ready" in tool.description
    assert callable(tool.binder)


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
    assert result["internal_checks"] == [
        "account_portfolio",
        "account_positions",
        "orders_open_orders",
        "market_last_price",
    ]
    assert strategy.submitted_orders == []


def test_orders_preflight_check_blocks_buy_with_insufficient_cash():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 250.0
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight()])

    result = tool_map["orders_preflight_check"](symbol="SPY", quantity=3, side="buy")

    assert result["readiness"] == "blocked"
    assert result["can_submit"] is False
    assert result["blockers"][0]["code"] == "INSUFFICIENT_CASH_ESTIMATE"
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
    assert result["blockers"][0]["code"] == "INSUFFICIENT_POSITION"


def test_orders_preflight_check_blocks_invalid_quantity():
    strategy = _OrderReadinessStrategy()
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight()])

    result = tool_map["orders_preflight_check"](symbol="SPY", quantity=0, side="buy")

    assert result["readiness"] == "blocked"
    assert result["can_submit"] is False
    assert result["blockers"][0]["code"] == "INVALID_QUANTITY"


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
    assert result["blockers"][0]["code"] == "UNSUPPORTED_ORDER_TYPE"


def test_orders_preflight_check_blocks_same_symbol_open_order():
    strategy = _OrderReadinessStrategy()
    strategy.open_orders = [_fake_open_order("SPY")]
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight()])

    result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="buy")

    assert result["readiness"] == "blocked"
    assert result["can_submit"] is False
    assert result["blockers"][0]["code"] == "OPEN_ORDER_CONFLICT"
    assert result["open_orders"]["same_symbol_count"] == 1


def test_orders_preflight_check_blocks_when_price_unavailable():
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"SPY": None}
    tool_map = _wrap_builtin_tools(strategy, [BuiltinTools.orders.preflight()])

    result = tool_map["orders_preflight_check"](symbol="SPY", quantity=1, side="buy")

    assert result["readiness"] == "blocked"
    assert result["can_submit"] is False
    assert result["blockers"][0]["code"] == "PRICE_UNAVAILABLE"
```

- [ ] **Step 4: Run tests to verify they fail**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py::test_builtin_order_tools_expose_preflight_definition tests\test_agent_tool_permissions.py::test_orders_preflight_check_ready_buy_returns_structured_snapshot -q
```

Expected: FAIL with `AttributeError: '_OrderTools' object has no attribute 'preflight'`.

## Task 2: Implement `orders_preflight_check` And Readiness Signals

**Files:**
- Modify: `lumibot/components/agents/builtins.py`

- [ ] **Step 1: Add readiness signal helpers after `_has_successful_market_last_price_for_symbol`**

Insert:

```python
def _normalized_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _record_successful_order_readiness(symbol: str, source: str) -> None:
    normalized_symbol = _normalized_symbol(symbol)
    if not normalized_symbol:
        return
    context = current_agent_tool_context()
    readiness = context.setdefault("order_readiness", [])
    if not isinstance(readiness, list):
        return
    readiness.append(
        {
            "symbol": normalized_symbol,
            "source": source,
            "ok": True,
        }
    )


def _has_successful_order_readiness_for_symbol(symbol: str) -> bool:
    normalized_symbol = _normalized_symbol(symbol)
    context = current_agent_tool_context()
    readiness = context.get("order_readiness")
    if not isinstance(readiness, list):
        return False
    for item in readiness:
        if not isinstance(item, dict):
            continue
        if item.get("ok") is not True:
            continue
        if str(item.get("symbol") or "").strip().upper() == normalized_symbol:
            return True
    return False
```

- [ ] **Step 2: Update `_require_agent_order_readiness`**

Replace the body after the `enforce_order_readiness` early return with:

```python
    if _has_successful_order_readiness_for_symbol(symbol):
        return
    missing: list[str] = []
    if not _has_successful_tool_call("account_portfolio"):
        missing.append("account_portfolio")
    if not _has_successful_tool_call("account_positions"):
        missing.append("account_positions")
    if not _has_successful_market_last_price_for_symbol(symbol):
        missing.append(f"market_last_price(symbol={symbol!r})")
    if missing:
        raise ValueError(
            "ORDER_READINESS_REQUIRED: Before submitting an order, inspect readiness in this same agent run. "
            "Prefer orders_preflight_check when available; otherwise call "
            f"{', '.join(missing)} in this same agent run. "
            "Agents must inspect cash, portfolio value, positions, and the latest price for the ordered asset before trading."
        )
```

- [ ] **Step 3: Add preflight helper functions before `_bind_open_orders`**

Insert these helpers before `_bind_open_orders`:

```python
def _preflight_blocker(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _preflight_blocked_payload(
    *,
    sequence: int | None,
    symbol: Any,
    side: Any,
    quantity: Any,
    asset_type: Any,
    order_type: Any,
    time_in_force: Any,
    blockers: list[dict[str, str]],
    warnings: list[str] | None = None,
    account: dict[str, Any] | None = None,
    position: dict[str, Any] | None = None,
    price: dict[str, Any] | None = None,
    open_orders: dict[str, Any] | None = None,
    estimate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "asset_type": asset_type,
        "order_type": order_type,
        "time_in_force": time_in_force,
        "readiness": "blocked",
        "can_submit": False,
        "blockers": blockers,
        "warnings": list(warnings or []),
        "account": account or {},
        "position": position or {},
        "price": price or {},
        "open_orders": open_orders or {"count": 0, "same_symbol_count": 0, "same_symbol_orders": []},
        "estimate": estimate or {},
        "internal_checks": [
            "account_portfolio",
            "account_positions",
            "orders_open_orders",
            "market_last_price",
        ],
    }


def _position_symbol(position: Any) -> str:
    return _normalized_symbol(getattr(getattr(position, "asset", None), "symbol", None))


def _position_snapshot_for_symbol(strategy: Any, symbol: str) -> dict[str, Any]:
    normalized_symbol = _normalized_symbol(symbol)
    for position in strategy.get_positions(include_cash_positions=True):
        if _position_symbol(position) == normalized_symbol:
            payload = _position_to_dict(position)
            return {
                "symbol": normalized_symbol,
                "quantity": payload.get("quantity") or 0.0,
                "market_value": payload.get("market_value"),
                "current_price": payload.get("current_price"),
                "raw": payload,
            }
    return {
        "symbol": normalized_symbol,
        "quantity": 0.0,
        "market_value": None,
        "current_price": None,
        "raw": None,
    }


def _open_orders_snapshot_for_symbol(strategy: Any, symbol: str) -> dict[str, Any]:
    normalized_symbol = _normalized_symbol(symbol)
    orders = list(strategy.get_orders())
    order_payloads = [_order_to_dict(order) for order in orders]
    same_symbol_orders = [
        order
        for order in order_payloads
        if _normalized_symbol((order.get("asset") or {}).get("symbol") if isinstance(order.get("asset"), dict) else None)
        == normalized_symbol
    ]
    return {
        "count": len(order_payloads),
        "same_symbol_count": len(same_symbol_orders),
        "same_symbol_orders": same_symbol_orders,
        "orders": order_payloads,
    }


def _finite_positive_price(value: Any) -> float | None:
    try:
        price = float(value)
    except Exception:
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return price
```

- [ ] **Step 4: Add `_bind_preflight_check` before `_bind_open_orders`**

Insert:

```python
def _bind_preflight_check(strategy: Any, manager: Any) -> BoundTool:
    def preflight_check(
        *,
        symbol: str,
        side: OrderSideArg,
        quantity: float,
        sequence: int | None = None,
        asset_type: AssetTypeArg = "stock",
        order_type: OrderTypeArg = "market",
        time_in_force: TimeInForceArg = "day",
    ) -> dict[str, Any]:
        blockers: list[dict[str, str]] = []
        warnings: list[str] = []
        raw_symbol = symbol
        raw_side = side
        raw_quantity = quantity
        raw_asset_type = asset_type
        raw_order_type = order_type
        raw_time_in_force = time_in_force

        symbol_text = str(symbol or "").strip().upper()
        if not symbol_text or "," in symbol_text:
            blockers.append(_preflight_blocker("INVALID_SYMBOL", "symbol must be one non-empty tradable symbol."))

        side_text = str(side or "").strip().lower()
        if side_text not in {"buy", "sell"}:
            blockers.append(_preflight_blocker("INVALID_SIDE", "Stage A supports buy and sell for long stock/ETF execution."))

        try:
            quantity_value = float(quantity)
        except Exception:
            quantity_value = math.nan
        if not math.isfinite(quantity_value) or quantity_value <= 0:
            blockers.append(_preflight_blocker("INVALID_QUANTITY", "quantity must be a finite number greater than 0."))

        asset_type_text = str(asset_type or "stock").strip().lower()
        if asset_type_text not in {"stock", "us_equity"}:
            blockers.append(_preflight_blocker("UNSUPPORTED_ASSET_TYPE", "Stage A supports stock and us_equity only."))

        order_type_text = str(order_type or "market").strip().lower()
        if order_type_text != "market":
            blockers.append(_preflight_blocker("UNSUPPORTED_ORDER_TYPE", "Stage A preflight supports market orders only."))

        time_in_force_text = str(time_in_force or "day").strip().lower()
        if time_in_force_text != "day":
            blockers.append(_preflight_blocker("UNSUPPORTED_TIME_IN_FORCE", "Stage A preflight supports day orders only."))

        account = {
            "cash": strategy.get_cash(),
            "portfolio_value": strategy.get_portfolio_value(),
            "datetime": strategy.get_datetime().isoformat(),
        }
        position = _position_snapshot_for_symbol(strategy, symbol_text)
        open_orders = _open_orders_snapshot_for_symbol(strategy, symbol_text)

        price_value = None
        if symbol_text and asset_type_text in {"stock", "us_equity"}:
            asset, quote = resolve_asset_and_quote(strategy, symbol=symbol_text, asset_type=asset_type_text)
            price_value = _finite_positive_price(strategy.get_last_price(asset, quote=quote))
        price = {
            "symbol": symbol_text,
            "asset_type": asset_type_text,
            "last_price": price_value,
            "datetime": strategy.get_datetime().isoformat(),
        }
        if symbol_text and price_value is None:
            blockers.append(_preflight_blocker("PRICE_UNAVAILABLE", f"Latest price for {symbol_text} is unavailable."))

        if open_orders.get("same_symbol_count", 0) > 0:
            blockers.append(_preflight_blocker("OPEN_ORDER_CONFLICT", f"{symbol_text} already has an open order."))

        current_quantity = float(position.get("quantity") or 0.0)
        estimated_order_value = quantity_value * price_value if math.isfinite(quantity_value) and price_value is not None else None
        estimated_cash_after_order = None
        estimated_position_after_order = None
        if estimated_order_value is not None:
            if side_text == "buy":
                estimated_cash_after_order = float(account["cash"]) - estimated_order_value
                estimated_position_after_order = current_quantity + quantity_value
                if estimated_order_value > float(account["cash"]):
                    blockers.append(
                        _preflight_blocker(
                            "INSUFFICIENT_CASH_ESTIMATE",
                            f"Estimated order value {estimated_order_value:.2f} exceeds cash {float(account['cash']):.2f}.",
                        )
                    )
            elif side_text == "sell":
                estimated_cash_after_order = float(account["cash"]) + estimated_order_value
                estimated_position_after_order = current_quantity - quantity_value
                if current_quantity < quantity_value:
                    blockers.append(
                        _preflight_blocker(
                            "INSUFFICIENT_POSITION",
                            f"Requested sell quantity {quantity_value:g} exceeds current long position quantity {current_quantity:g}.",
                        )
                    )

        estimate = {
            "estimated_order_value": estimated_order_value,
            "estimated_cash_after_order": estimated_cash_after_order,
            "estimated_position_after_order": estimated_position_after_order,
        }

        if blockers:
            return _preflight_blocked_payload(
                sequence=sequence,
                symbol=raw_symbol,
                side=raw_side,
                quantity=raw_quantity,
                asset_type=raw_asset_type,
                order_type=raw_order_type,
                time_in_force=raw_time_in_force,
                blockers=blockers,
                warnings=warnings,
                account=account,
                position=position,
                price=price,
                open_orders=open_orders,
                estimate=estimate,
            )

        _record_successful_order_readiness(symbol_text, "orders_preflight_check")
        return {
            "sequence": sequence,
            "symbol": symbol_text,
            "side": side_text,
            "quantity": quantity_value,
            "asset_type": asset_type_text,
            "order_type": order_type_text,
            "time_in_force": time_in_force_text,
            "readiness": "ready",
            "can_submit": True,
            "blockers": [],
            "warnings": warnings,
            "account": account,
            "position": position,
            "price": price,
            "open_orders": open_orders,
            "estimate": estimate,
            "internal_checks": [
                "account_portfolio",
                "account_positions",
                "orders_open_orders",
                "market_last_price",
            ],
        }

    return BoundTool(
        name="orders_preflight_check",
        description=(
            "Inspect whether one explicit execution_plan order appears ready to submit. "
            "The tool checks current cash, portfolio value, current position in the symbol, "
            "open orders, and latest price. It does not submit, cancel, modify, or confirm orders. "
            "Use it before orders_submit_order when this tool is available."
        ),
        function=preflight_check,
        metadata={"kind": "builtin", "replay_on_cache": True},
    )
```

- [ ] **Step 5: Add the tool to `_OrderTools`**

In class `_OrderTools`, add this method before `submit()`:

```python
    def preflight(self) -> ToolDefinition:
        return ToolDefinition(
            name="orders_preflight_check",
            description="Inspect whether one explicit execution_plan order appears ready to submit.",
            binder=_bind_preflight_check,
        )
```

- [ ] **Step 6: Run the preflight behavior tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py::test_builtin_order_tools_expose_preflight_definition tests\test_agent_tool_permissions.py::test_orders_preflight_check_ready_buy_returns_structured_snapshot tests\test_agent_tool_permissions.py::test_orders_preflight_check_blocks_buy_with_insufficient_cash tests\test_agent_tool_permissions.py::test_orders_preflight_check_ready_sell_with_sufficient_position tests\test_agent_tool_permissions.py::test_orders_preflight_check_blocks_sell_with_insufficient_position tests\test_agent_tool_permissions.py::test_orders_preflight_check_blocks_invalid_quantity tests\test_agent_tool_permissions.py::test_orders_preflight_check_blocks_unsupported_order_type tests\test_agent_tool_permissions.py::test_orders_preflight_check_blocks_same_symbol_open_order tests\test_agent_tool_permissions.py::test_orders_preflight_check_blocks_when_price_unavailable -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add lumibot\components\agents\builtins.py tests\test_agent_tool_permissions.py
git commit -m "feat: add orders preflight check tool"
```

## Task 3: Test And Implement Submit Readiness Compatibility

**Files:**
- Modify: `tests/test_agent_tool_permissions.py`
- Modify: `lumibot/components/agents/builtins.py`

- [ ] **Step 1: Add readiness compatibility tests after `test_agent_order_tool_requires_last_price_for_ordered_symbol`**

Append:

```python
def test_agent_order_tool_submits_after_successful_preflight_for_same_symbol():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 1000.0
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_builtin_tools(
        strategy,
        [
            BuiltinTools.orders.preflight(),
            BuiltinTools.orders.submit(),
        ],
    )

    preflight = tool_map["orders_preflight_check"](symbol="SPY", quantity=5, side="buy")
    result = tool_map["orders_submit_order"](
        symbol="SPY",
        quantity=5,
        side="buy",
        asset_type="stock",
        order_type="market",
    )

    assert preflight["can_submit"] is True
    assert "tool_error" not in result
    assert result["order"]["asset"]["symbol"] == "SPY"
    assert len(strategy.submitted_orders) == 1


def test_agent_order_tool_rejects_after_preflight_for_different_symbol():
    strategy = _OrderReadinessStrategy()
    strategy.last_prices = {"QQQ": 100.0, "SPY": 100.0}
    tool_map = _wrap_builtin_tools(
        strategy,
        [
            BuiltinTools.orders.preflight(),
            BuiltinTools.orders.submit(),
        ],
    )

    preflight = tool_map["orders_preflight_check"](symbol="QQQ", quantity=1, side="buy")
    result = tool_map["orders_submit_order"](
        symbol="SPY",
        quantity=1,
        side="buy",
        asset_type="stock",
        order_type="market",
    )

    assert preflight["can_submit"] is True
    assert result["tool_error"] is True
    assert "ORDER_READINESS_REQUIRED" in result["error"]["message"]
    assert strategy.submitted_orders == []


def test_agent_order_tool_rejects_after_blocked_preflight():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 50.0
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_builtin_tools(
        strategy,
        [
            BuiltinTools.orders.preflight(),
            BuiltinTools.orders.submit(),
        ],
    )

    preflight = tool_map["orders_preflight_check"](symbol="SPY", quantity=2, side="buy")
    result = tool_map["orders_submit_order"](
        symbol="SPY",
        quantity=2,
        side="buy",
        asset_type="stock",
        order_type="market",
    )

    assert preflight["can_submit"] is False
    assert result["tool_error"] is True
    assert "ORDER_READINESS_REQUIRED" in result["error"]["message"]
    assert strategy.submitted_orders == []
```

- [ ] **Step 2: Run compatibility tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py::test_agent_order_tool_submits_after_successful_preflight_for_same_symbol tests\test_agent_tool_permissions.py::test_agent_order_tool_rejects_after_preflight_for_different_symbol tests\test_agent_tool_permissions.py::test_agent_order_tool_rejects_after_blocked_preflight -q
```

Expected: PASS if Task 2 recorded readiness correctly. If the first test fails with `ORDER_READINESS_REQUIRED`, inspect `_record_successful_order_readiness()` and `_has_successful_order_readiness_for_symbol()`.

- [ ] **Step 3: Update `orders_submit_order` model-facing description**

In `_bind_submit_order`, replace the sentence:

```python
            "Before using this tool, call account_portfolio, account_positions, and market_last_price for the same symbol in the current agent run; otherwise the order is rejected with ORDER_READINESS_REQUIRED. "
```

with:

```python
            "Before using this tool, inspect readiness in the current agent run. Prefer orders_preflight_check when available. Otherwise call account_portfolio, account_positions, and market_last_price for the same symbol. If readiness has not been inspected, the order is rejected with ORDER_READINESS_REQUIRED. "
```

- [ ] **Step 4: Add description test after `test_builtin_order_tools_expose_preflight_definition`**

Add:

```python
def test_submit_order_description_mentions_preflight_readiness_path():
    strategy = _OrderReadinessStrategy()
    manager = AgentManager(strategy)
    tool = BuiltinTools.orders.submit().binder(strategy, manager)

    description = tool.description
    assert "Prefer orders_preflight_check when available" in description
    assert "ORDER_READINESS_REQUIRED" in description
    assert "inspect readiness" in description
```

- [ ] **Step 5: Run readiness and description tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py::test_submit_order_description_mentions_preflight_readiness_path tests\test_agent_tool_permissions.py::test_agent_order_tool_rejects_when_account_context_was_not_checked tests\test_agent_tool_permissions.py::test_agent_order_tool_submits_after_account_context_was_checked tests\test_agent_tool_permissions.py::test_agent_order_tool_submits_after_successful_preflight_for_same_symbol tests\test_agent_tool_permissions.py::test_agent_order_tool_rejects_after_preflight_for_different_symbol tests\test_agent_tool_permissions.py::test_agent_order_tool_rejects_after_blocked_preflight -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add lumibot\components\agents\builtins.py tests\test_agent_tool_permissions.py
git commit -m "test: cover preflight readiness compatibility"
```

## Task 4: Update Mock Quadrant Execution Agent Tool Surface And Prompts

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Update expected tool surface test**

In `test_agents_receive_distinct_tool_surfaces`, replace the execution-agent expected tool set with:

```python
    assert created_tool_names(created["execution_agent"]) == {
        "orders_preflight_check",
        "orders_submit_order",
        "orders_confirm_order",
    }
```

- [ ] **Step 2: Update prompt boundary test**

In `test_prompt_boundaries_are_short_and_role_specific`, add these positive assertions:

```python
    assert "call orders_preflight_check before each order" in serialized
    assert "if preflight returns can_submit=false" in serialized
```

Also add these forbidden phrases to the `forbidden_phrase` tuple:

```python
        "first inspect portfolio, positions, open orders, and latest prices",
        "manually call account_portfolio",
```

- [ ] **Step 3: Run strategy tests and verify failure**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_agents_receive_distinct_tool_surfaces tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_prompt_boundaries_are_short_and_role_specific -q
```

Expected: FAIL because the strategy still exposes lower-level execution tools and does not mention preflight.

- [ ] **Step 4: Update `execution_agent` tools in `initialize()`**

In `AITradingTeamMockGrowthInflationQuadrantStrategy.initialize()`, replace the current `execution_agent` `tools=[...]` list with:

```python
            tools=[
                BuiltinTools.orders.preflight(),
                BuiltinTools.orders.submit(),
                BuiltinTools.orders.confirm(),
            ],
```

- [ ] **Step 5: Update execution-agent system prompt**

Replace the `execution_agent` `system_prompt` with:

```python
            system_prompt=(
                "Execution role: execute only the provided execution_plan using the listed order tools. "
                "Call orders_preflight_check before each order. If preflight returns can_submit=true, submit "
                "that same order with orders_submit_order, then confirm it with orders_confirm_order before "
                "continuing. If preflight returns can_submit=false, stop execution and report the blocker. "
                "Follow sequence order, do not perform investment research, and do not change order fields."
            ),
```

- [ ] **Step 6: Update execution-agent task prompt in `on_trading_iteration()`**

Find the task prompt text that currently tells the execution agent to inspect account/positions/open orders/latest prices. Replace it with:

```python
                "Execute the provided execution_plan. For each order in sequence, call orders_preflight_check "
                "with the exact order fields. If can_submit=true, call orders_submit_order with that same order, "
                "then call orders_confirm_order for the returned identifier before continuing. If can_submit=false "
                "or confirmation returns can_continue=false, stop remaining orders and report the blocker."
```

- [ ] **Step 7: Run strategy tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_agents_receive_distinct_tool_surfaces tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_prompt_boundaries_are_short_and_role_specific tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_on_trading_iteration_runs_agents_in_expected_order_and_context -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: route mock quadrant execution through preflight"
```

## Task 5: Add Replay UI Formatter For Preflight Results

**Files:**
- Modify: `tests/test_agent_replay_ui_formatters.py`
- Modify: `lumibot/components/agents/replay_ui/formatters.py`

- [ ] **Step 1: Add formatter tests after `test_orders_submit_order_formatter_extracts_nested_order_payload`**

Add:

```python
def test_orders_preflight_check_formatter_explains_ready_order():
    text = explain_tool_result(
        "orders_preflight_check",
        {"symbol": "SPY", "side": "buy", "quantity": 3},
        {
            "symbol": "SPY",
            "side": "buy",
            "quantity": 3,
            "readiness": "ready",
            "can_submit": True,
            "blockers": [],
            "account": {"cash": 1000.0, "portfolio_value": 1200.0},
            "position": {"quantity": 2.0},
            "price": {"last_price": 100.0},
            "estimate": {"estimated_order_value": 300.0, "estimated_cash_after_order": 700.0},
        },
        None,
    )

    assert "Preflight ready" in text
    assert "buy 3 SPY" in text
    assert "cash 1000.0" in text
    assert "last price 100.0" in text
    assert "estimated value 300.0" in text


def test_orders_preflight_check_formatter_explains_blocked_order():
    text = explain_tool_result(
        "orders_preflight_check",
        {"symbol": "VNQ", "side": "sell", "quantity": 20},
        {
            "symbol": "VNQ",
            "side": "sell",
            "quantity": 20,
            "readiness": "blocked",
            "can_submit": False,
            "blockers": [{"code": "INSUFFICIENT_POSITION", "message": "Requested sell quantity exceeds position."}],
            "account": {"cash": 1000.0, "portfolio_value": 1200.0},
            "position": {"quantity": 10.0},
            "price": {"last_price": 80.0},
            "estimate": {"estimated_order_value": 1600.0},
        },
        None,
    )

    assert "Preflight blocked" in text
    assert "sell 20 VNQ" in text
    assert "INSUFFICIENT_POSITION" in text
    assert "Requested sell quantity exceeds position" in text
```

- [ ] **Step 2: Run formatter tests and verify failure**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_formatters.py::test_orders_preflight_check_formatter_explains_ready_order tests\test_agent_replay_ui_formatters.py::test_orders_preflight_check_formatter_explains_blocked_order -q
```

Expected: FAIL because no formatter is registered yet.

- [ ] **Step 3: Add `_orders_preflight_check` formatter before `_orders_submit_order`**

In `lumibot/components/agents/replay_ui/formatters.py`, insert:

```python
def _orders_preflight_check(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    symbol = _first_present(result, "symbol") or _first_present(args, "symbol")
    qty = _first_present(result, "quantity") or _first_present(args, "quantity")
    side = _first_present(result, "side") or _first_present(args, "side")
    readiness = _first_present(result, "readiness")
    blockers = result.get("blockers") if isinstance(result.get("blockers"), list) else []
    account = _nested_dict(result, "account") or {}
    position = _nested_dict(result, "position") or {}
    price = _nested_dict(result, "price") or {}
    estimate = _nested_dict(result, "estimate") or {}
    order_text = f"{_text(side)} {_text(qty)} {_text(symbol)}"
    cash = _first_present(account, "cash")
    current_qty = _first_present(position, "quantity")
    last_price = _first_present(price, "last_price", "price")
    estimated_value = _first_present(estimate, "estimated_order_value")
    cash_after = _first_present(estimate, "estimated_cash_after_order")
    detail = (
        f" cash {_text(cash)}, current quantity {_text(current_qty)}, "
        f"last price {_text(last_price)}, estimated value {_text(estimated_value)}, "
        f"estimated cash after {_text(cash_after)}."
    )
    if result.get("can_submit") is True or readiness == "ready":
        return f"Preflight ready for {order_text}.{detail}"
    blocker_text = ""
    if blockers:
        first = blockers[0] if isinstance(blockers[0], dict) else {"code": blockers[0]}
        code = _first_present(first, "code")
        message = _first_present(first, "message")
        blocker_text = f" Blocker: {_text(code)}"
        if message is not None:
            blocker_text += f" - {_text(message)}"
    return f"Preflight blocked for {order_text}.{detail}{blocker_text}"
```

- [ ] **Step 4: Register the formatter**

Add this entry to `_FORMATTERS` before `orders_submit_order`:

```python
    "orders_preflight_check": _orders_preflight_check,
```

- [ ] **Step 5: Run formatter tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_formatters.py::test_orders_preflight_check_formatter_explains_ready_order tests\test_agent_replay_ui_formatters.py::test_orders_preflight_check_formatter_explains_blocked_order -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add lumibot\components\agents\replay_ui\formatters.py tests\test_agent_replay_ui_formatters.py
git commit -m "feat: explain orders preflight in replay UI"
```

## Task 6: Run Focused Regression Tests

**Files:**
- Verify only.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_replay_ui_formatters.py -q
```

Expected: PASS.

- [ ] **Step 2: Run focused ruff check**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\builtins.py lumibot\components\agents\replay_ui\formatters.py lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_tool_permissions.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_replay_ui_formatters.py
```

Expected: PASS.

- [ ] **Step 3: Commit any small lint fixes**

If Step 2 required formatting or import cleanup, commit only those cleanup changes:

```powershell
git add lumibot\components\agents\builtins.py lumibot\components\agents\replay_ui\formatters.py lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_tool_permissions.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_replay_ui_formatters.py
git commit -m "chore: clean up preflight stage lint"
```

If there were no changes, do not create an empty commit.

## Task 7: Run One-Day And Two-Day Benchmark Validation

**Files:**
- Verify generated artifacts only.

- [ ] **Step 1: Run one-day benchmark**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-05 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file project_notes\API.txt
```

Expected:

- Result status is `passed`.
- The newest artifact under `artifacts\ai_trading_team_example_benchmarks` contains `mock-growth-inflation-quadrant`.

- [ ] **Step 2: Inspect one-day execution trace for Stage A shape**

Run:

```powershell
$latest = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
rg -n "orders_preflight_check|orders_submit_order|orders_confirm_order|ORDER_READINESS_REQUIRED" "$($latest.FullName)\mock-growth-inflation-quadrant" -S
```

Expected:

- `orders_preflight_check` appears before `orders_submit_order`.
- `orders_confirm_order` appears after submitted orders.
- `ORDER_READINESS_REQUIRED` does not appear.

- [ ] **Step 3: Run two-day benchmark**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-06 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file project_notes\API.txt
```

Expected:

- Result status is `passed`.
- If the plan contains rebalance orders, same-day sell/buy sequencing still completes.
- No negative-cash regression appears.

- [ ] **Step 4: Inspect two-day trace for reduced preflight clutter**

Run:

```powershell
$latest = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
rg -n "account_portfolio|account_positions|orders_open_orders|market_last_price|orders_preflight_check|orders_submit_order|orders_confirm_order" "$($latest.FullName)\mock-growth-inflation-quadrant\cache\agent_runtime\traces\execution_agent" -S
```

Expected:

- `orders_preflight_check` appears for each submitted order.
- The execution-agent trace no longer shows separate model-facing `account_portfolio`, `account_positions`, `orders_open_orders`, or `market_last_price` calls.
- `orders_submit_order` and `orders_confirm_order` still appear.

- [ ] **Step 5: Record benchmark notes if needed**

If the benchmark reveals a non-code observation, add a short note to the final implementation report in the assistant response. Do not commit benchmark artifacts unless the user explicitly asks.

## Task 8: Final Verification And Branch State

**Files:**
- Verify only.

- [ ] **Step 1: Run final focused verification**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_replay_ui_formatters.py -q
D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\builtins.py lumibot\components\agents\replay_ui\formatters.py lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_tool_permissions.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_replay_ui_formatters.py
git diff --check
git status --short --branch
```

Expected:

- pytest passes.
- ruff passes.
- `git diff --check` has no output.
- `git status` shows no unexpected unstaged source changes.

- [ ] **Step 2: Summarize implementation**

The final response should include:

- commits created
- tests run
- benchmark runs and artifact IDs
- whether Stage A acceptance criteria passed
- any residual risk, especially around stale preflight price/cash between preflight and submit

## Self-Review Checklist

- Spec coverage:
  - Tool name, description, input, output, readiness rules, prompt changes, submit-description compatibility, UI formatter, tests, and benchmarks all map to tasks above.
- Placeholder scan:
  - This plan contains no unfinished placeholder markers or unspecified implementation slots.
- Type consistency:
  - The plan uses `orders_preflight_check`, `can_submit`, `readiness`, `blockers`, `warnings`, `account`, `position`, `price`, `open_orders`, `estimate`, and `internal_checks` consistently with the spec.
- Scope:
  - This plan implements Stage A only. It does not combine submit/confirm, execute a full order, or execute an entire plan.
