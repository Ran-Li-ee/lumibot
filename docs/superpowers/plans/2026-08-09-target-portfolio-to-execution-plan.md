# Target Portfolio To Execution Plan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic `target_portfolio_to_execution_plan` tool so `portfolio_decision_agent` chooses target portfolio weights while code generates the strict execution plan.

**Architecture:** Create a focused planner module next to the mock quadrant strategy. Register the planner as an ADK/Lumibot tool for `portfolio_decision_agent`, remove direct account/price tools from that agent, and validate that the final summary's `execution_plan` exactly matches the planner tool result before execution.

**Tech Stack:** Python, Lumibot agent `ToolDefinition` / `BoundTool`, pytest, ruff, existing Agent Replay trace pipeline.

---

## File Structure

Create:

- `lumibot/example_strategies/target_portfolio_to_execution_plan.py`
  - Pure deterministic planner.
  - Normalizes target portfolio input.
  - Reads current positions, cash, portfolio value, and prices from the strategy.
  - Produces `current_vs_target`, `cash_projection`, `execution_plan`, and `warnings`.
  - Exposes `make_target_portfolio_to_execution_plan_tool()`.

Modify:

- `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
  - Import the planner tool.
  - Register only `target_portfolio_to_execution_plan` for `portfolio_decision_agent`.
  - Narrow the portfolio decision prompt.
  - Change the portfolio task prompt so the agent calls the planner.
  - Validate parsed `execution_plan` against the stored planner result.
  - Update or replace `validate_portfolio_decision_tool_evidence`.

- `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
  - Add planner unit tests.
  - Update agent tool-surface tests.
  - Update prompt-boundary tests.
  - Add workflow tests proving execution agent receives planner-generated plan.

No UI file changes are required in this feature because scheme A uses a normal ADK tool call that should already appear in Agent Replay.

---

### Task 1: Add Failing Pure Planner Tests

**Files:**
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
- Future implementation target: `lumibot/example_strategies/target_portfolio_to_execution_plan.py`

- [ ] **Step 1: Add fake portfolio helpers near the existing test helpers**

Add these helpers after `make_strategy_with_agent_manager()`:

```python
def make_position(symbol, quantity):
    return SimpleNamespace(
        symbol=symbol,
        quantity=quantity,
        asset=SimpleNamespace(symbol=symbol),
    )


def make_planner_strategy(*, positions, prices, cash=0.0, portfolio_value=100000.0):
    def get_positions(include_cash_positions=False):
        return list(positions)

    def get_last_price(symbol, quote=None, exchange=None):
        if not isinstance(symbol, str):
            symbol = getattr(symbol, "symbol", symbol)
        symbol = str(symbol).upper()
        if symbol not in prices:
            return None
        return prices[symbol]

    return SimpleNamespace(
        get_positions=get_positions,
        get_cash=lambda: cash,
        get_portfolio_value=lambda: portfolio_value,
        get_last_price=get_last_price,
    )
```

- [ ] **Step 2: Add all-cash deployment test**

Append this test:

```python
def test_target_portfolio_to_execution_plan_deploys_all_cash_to_targets():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[],
        cash=100000,
        portfolio_value=100000,
        prices={"SPY": 100, "GLD": 100, "VGIT": 50},
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-05",
        target_portfolio=[
            {"basket_id": "equity", "symbol": "SPY", "target_weight": 0.50},
            {"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.25},
            {"basket_id": "nominal_bond", "symbol": "VGIT", "target_weight": 0.25},
        ],
    )

    assert result["execution_plan"] == {
        "schema_version": 1,
        "intent": "rebalance",
        "orders": [
            {
                "sequence": 1,
                "action": "submit_order",
                "symbol": "GLD",
                "side": "buy",
                "quantity_mode": "shares",
                "quantity": 250,
                "asset_type": "stock",
                "order_type": "market",
                "time_in_force": "day",
            },
            {
                "sequence": 2,
                "action": "submit_order",
                "symbol": "SPY",
                "side": "buy",
                "quantity_mode": "shares",
                "quantity": 500,
                "asset_type": "stock",
                "order_type": "market",
                "time_in_force": "day",
            },
            {
                "sequence": 3,
                "action": "submit_order",
                "symbol": "VGIT",
                "side": "buy",
                "quantity_mode": "shares",
                "quantity": 500,
                "asset_type": "stock",
                "order_type": "market",
                "time_in_force": "day",
            },
        ],
    }
    assert result["cash_projection"]["cash_after_estimate"] == pytest.approx(0)
    assert {row["reason_code"] for row in result["current_vs_target"]} == {"buy_new_target"}
```

- [ ] **Step 3: Add critical full rebalance regression test**

Append this test:

```python
def test_target_portfolio_to_execution_plan_handles_full_rebalance_regression_case():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[
            make_position("SPY", 500),
            make_position("GLD", 250),
            make_position("VGIT", 500),
        ],
        cash=0,
        portfolio_value=100000,
        prices={"SPY": 100, "GLD": 100, "VGIT": 50, "TIP": 100},
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-06",
        target_portfolio=[
            {"basket_id": "equity", "symbol": "SPY", "target_weight": 0.25},
            {"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.50},
            {"basket_id": "tips", "symbol": "TIP", "target_weight": 0.25},
        ],
    )

    orders = result["execution_plan"]["orders"]
    assert [(order["side"], order["symbol"], order["quantity"]) for order in orders] == [
        ("sell", "VGIT", 500),
        ("sell", "SPY", 250),
        ("buy", "TIP", 250),
        ("buy", "GLD", 250),
    ]
    assert result["cash_projection"]["cash_after_estimate"] == pytest.approx(0)
    diagnostics = {row["symbol"]: row for row in result["current_vs_target"]}
    assert diagnostics["VGIT"]["reason_code"] == "exit_removed_symbol"
    assert diagnostics["SPY"]["reason_code"] == "reduce_overweight"
    assert diagnostics["GLD"]["reason_code"] == "increase_underweight"
    assert diagnostics["TIP"]["reason_code"] == "buy_new_target"
```

- [ ] **Step 4: Add basket-internal symbol switch test**

Append this test:

```python
def test_target_portfolio_to_execution_plan_handles_basket_internal_symbol_switch():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[
            make_position("SPY", 500),
            make_position("GLD", 250),
            make_position("VGIT", 500),
        ],
        cash=0,
        portfolio_value=100000,
        prices={"SPY": 100, "QQQ": 200, "GLD": 100, "VGIT": 50},
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-06",
        target_portfolio=[
            {"basket_id": "equity", "symbol": "QQQ", "target_weight": 0.50},
            {"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.25},
            {"basket_id": "nominal_bond", "symbol": "VGIT", "target_weight": 0.25},
        ],
    )

    assert [(order["side"], order["symbol"], order["quantity"]) for order in result["execution_plan"]["orders"]] == [
        ("sell", "SPY", 500),
        ("buy", "QQQ", 250),
    ]
```

- [ ] **Step 5: Add drift and hold tests**

Append these tests:

```python
def test_target_portfolio_to_execution_plan_rebalances_price_drift():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[
            make_position("SPY", 570),
            make_position("GLD", 280),
            make_position("VGIT", 300),
        ],
        cash=0,
        portfolio_value=100000,
        prices={"SPY": 100, "GLD": 100, "VGIT": 50},
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-06",
        target_portfolio=[
            {"basket_id": "equity", "symbol": "SPY", "target_weight": 0.50},
            {"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.25},
            {"basket_id": "nominal_bond", "symbol": "VGIT", "target_weight": 0.25},
        ],
    )

    assert [(order["side"], order["symbol"], order["quantity"]) for order in result["execution_plan"]["orders"]] == [
        ("sell", "GLD", 30),
        ("sell", "SPY", 70),
        ("buy", "VGIT", 200),
    ]


def test_target_portfolio_to_execution_plan_holds_when_whole_share_rounding_produces_no_orders():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[
            make_position("SPY", 500),
            make_position("GLD", 250),
            make_position("VGIT", 500),
        ],
        cash=0,
        portfolio_value=100000,
        prices={"SPY": 100, "GLD": 100, "VGIT": 50},
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-06",
        target_portfolio=[
            {"basket_id": "equity", "symbol": "SPY", "target_weight": 0.50},
            {"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.25},
            {"basket_id": "nominal_bond", "symbol": "VGIT", "target_weight": 0.25},
        ],
    )

    assert result["execution_plan"] == {"schema_version": 1, "intent": "hold", "orders": []}
```

- [ ] **Step 6: Add validation tests**

Append these tests:

```python
def test_target_portfolio_to_execution_plan_combines_duplicate_targets_and_rejects_overweight_total():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[],
        cash=100000,
        portfolio_value=100000,
        prices={"SPY": 100, "GLD": 100},
    )

    combined = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-06",
        target_portfolio=[
            {"basket_id": "equity", "symbol": "SPY", "target_weight": 0.25},
            {"basket_id": "equity", "symbol": "SPY", "target_weight": 0.25},
            {"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.25},
        ],
    )
    assert {item["symbol"]: item["target_weight"] for item in combined["target_portfolio"]} == {
        "GLD": 0.25,
        "SPY": 0.50,
    }

    with pytest.raises(ValueError, match="target weights must not exceed 1.0"):
        planner.target_portfolio_to_execution_plan(
            strategy,
            date="2024-09-06",
            target_portfolio=[
                {"symbol": "SPY", "target_weight": 0.75},
                {"symbol": "GLD", "target_weight": 0.50},
            ],
        )


def test_target_portfolio_to_execution_plan_rejects_missing_price():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[],
        cash=100000,
        portfolio_value=100000,
        prices={"SPY": 100},
    )

    with pytest.raises(ValueError, match="missing last price for GLD"):
        planner.target_portfolio_to_execution_plan(
            strategy,
            date="2024-09-06",
            target_portfolio=[
                {"symbol": "SPY", "target_weight": 0.50},
                {"symbol": "GLD", "target_weight": 0.25},
            ],
        )
```

- [ ] **Step 7: Run planner tests and verify they fail for missing module**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'lumibot.example_strategies.target_portfolio_to_execution_plan'
```

- [ ] **Step 8: Commit failing tests**

```powershell
git add tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "test: cover target portfolio transition planning"
```

---

### Task 2: Implement Deterministic Planner Module

**Files:**
- Create: `lumibot/example_strategies/target_portfolio_to_execution_plan.py`
- Test: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Create planner module with normalization and state extraction**

Create `lumibot/example_strategies/target_portfolio_to_execution_plan.py`:

```python
import math
from decimal import Decimal, InvalidOperation
from typing import Any

from lumibot.components.agents.schemas import BoundTool, ToolDefinition

TOOL_NAME = "target_portfolio_to_execution_plan"
TARGET_WEIGHT_TOLERANCE = Decimal("0.000001")
QUOTE_SYMBOLS = {"USD", "CASH"}


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be numeric.") from exc
    if not result.is_finite():
        raise ValueError(f"{label} must be finite.")
    return result


def _float(value: Decimal) -> float:
    return float(value)


def _symbol(value: Any) -> str:
    symbol = getattr(value, "symbol", value)
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol must be non-empty.")
    return symbol


def _position_symbol(position: Any) -> str:
    if hasattr(position, "symbol"):
        return _symbol(position.symbol)
    if hasattr(position, "asset"):
        return _symbol(position.asset)
    raise ValueError("position is missing symbol/asset.")


def _position_quantity(position: Any) -> Decimal:
    if not hasattr(position, "quantity"):
        raise ValueError("position is missing quantity.")
    return _decimal(position.quantity, "position quantity")


def _get_positions(strategy: Any) -> list[Any]:
    try:
        return list(strategy.get_positions(include_cash_positions=False))
    except TypeError:
        return list(strategy.get_positions())


def _get_cash(strategy: Any) -> Decimal:
    return _decimal(strategy.get_cash(), "cash")


def _get_portfolio_value(strategy: Any) -> Decimal:
    value = _decimal(strategy.get_portfolio_value(), "portfolio value")
    if value <= 0:
        raise ValueError("portfolio value must be positive.")
    return value


def _get_last_price(strategy: Any, symbol: str) -> Decimal:
    raw_price = strategy.get_last_price(symbol)
    if raw_price is None:
        raise ValueError(f"missing last price for {symbol}")
    price = _decimal(raw_price, f"last price for {symbol}")
    if price <= 0:
        raise ValueError(f"last price for {symbol} must be positive.")
    return price
```

- [ ] **Step 2: Add target normalization and current position collection**

Append:

```python
def normalize_target_portfolio(target_portfolio: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(target_portfolio, list):
        raise ValueError("target_portfolio must be a list.")

    combined: dict[str, dict[str, Any]] = {}
    for item in target_portfolio:
        if not isinstance(item, dict):
            raise ValueError("target_portfolio item must be an object.")
        symbol = _symbol(item.get("symbol"))
        weight = _decimal(item.get("target_weight"), f"target_weight for {symbol}")
        if weight < 0:
            raise ValueError("target_weight must be non-negative.")
        if weight == 0:
            continue
        basket_id = str(item.get("basket_id") or "").strip()
        if symbol not in combined:
            combined[symbol] = {"symbol": symbol, "basket_id": basket_id or None, "target_weight": weight}
        else:
            combined[symbol]["target_weight"] += weight
            if combined[symbol].get("basket_id") is None and basket_id:
                combined[symbol]["basket_id"] = basket_id

    total_weight = sum((item["target_weight"] for item in combined.values()), Decimal("0"))
    if total_weight > Decimal("1.0") + TARGET_WEIGHT_TOLERANCE:
        raise ValueError("target weights must not exceed 1.0.")

    normalized = sorted(combined.values(), key=lambda item: item["symbol"])
    return [
        {
            "symbol": item["symbol"],
            "basket_id": item.get("basket_id"),
            "target_weight": _float(item["target_weight"]),
        }
        for item in normalized
    ]


def _current_positions_by_symbol(strategy: Any) -> dict[str, Decimal]:
    positions: dict[str, Decimal] = {}
    for position in _get_positions(strategy):
        symbol = _position_symbol(position)
        if symbol in QUOTE_SYMBOLS:
            continue
        quantity = _position_quantity(position)
        if quantity == 0:
            continue
        positions[symbol] = positions.get(symbol, Decimal("0")) + quantity
    return positions
```

- [ ] **Step 3: Add order builders and main planning function**

Append:

```python
def _order(*, sequence: int, symbol: str, side: str, quantity: int) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "action": "submit_order",
        "symbol": symbol,
        "side": side,
        "quantity_mode": "shares",
        "quantity": quantity,
        "asset_type": "stock",
        "order_type": "market",
        "time_in_force": "day",
    }


def _diagnostic_row(
    *,
    symbol: str,
    basket_id: str | None,
    current_quantity: Decimal,
    current_price: Decimal,
    portfolio_value: Decimal,
    target_weight: Decimal,
    planned_side: str | None,
    planned_quantity: int,
    reason_code: str,
) -> dict[str, Any]:
    current_value = current_quantity * current_price
    current_weight = current_value / portfolio_value
    target_value = target_weight * portfolio_value
    return {
        "symbol": symbol,
        "basket_id": basket_id,
        "current_quantity": _float(current_quantity),
        "current_price": _float(current_price),
        "current_value": _float(current_value),
        "current_weight": _float(current_weight),
        "target_weight": _float(target_weight),
        "target_value": _float(target_value),
        "delta_value": _float(target_value - current_value),
        "planned_side": planned_side,
        "planned_quantity": planned_quantity,
        "reason_code": reason_code,
    }


def target_portfolio_to_execution_plan(
    strategy: Any,
    *,
    date: str | None = None,
    target_portfolio: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_targets = normalize_target_portfolio(target_portfolio)
    target_by_symbol = {item["symbol"]: _decimal(item["target_weight"], f"target_weight for {item['symbol']}") for item in normalized_targets}
    basket_by_symbol = {item["symbol"]: item.get("basket_id") for item in normalized_targets}

    cash_before = _get_cash(strategy)
    portfolio_value = _get_portfolio_value(strategy)
    current_positions = _current_positions_by_symbol(strategy)
    relevant_symbols = sorted(set(current_positions) | set(target_by_symbol))
    prices = {symbol: _get_last_price(strategy, symbol) for symbol in relevant_symbols}

    diagnostics: list[dict[str, Any]] = []
    sell_candidates: list[tuple[int, str, int, str]] = []
    buy_candidates: list[tuple[int, str, int, str, Decimal]] = []
    warnings: list[str] = []
    estimated_sell_proceeds = Decimal("0")

    for symbol in relevant_symbols:
        current_quantity = current_positions.get(symbol, Decimal("0"))
        current_price = prices[symbol]
        target_weight = target_by_symbol.get(symbol, Decimal("0"))
        current_value = current_quantity * current_price
        target_value = target_weight * portfolio_value
        delta_value = target_value - current_value

        planned_side = None
        planned_quantity = 0
        reason_code = "already_at_target"
        if current_quantity > 0 and target_weight == 0:
            planned_side = "sell"
            planned_quantity = int(current_quantity)
            reason_code = "exit_removed_symbol"
            estimated_sell_proceeds += Decimal(planned_quantity) * current_price
            sell_candidates.append((1, symbol, planned_quantity, reason_code))
        elif delta_value < 0:
            planned_quantity = int(math.floor(abs(delta_value) / current_price))
            if planned_quantity > 0:
                planned_quantity = min(planned_quantity, int(current_quantity))
                planned_side = "sell"
                reason_code = "reduce_overweight"
                estimated_sell_proceeds += Decimal(planned_quantity) * current_price
                sell_candidates.append((2, symbol, planned_quantity, reason_code))
            else:
                reason_code = "rounding_no_sell"
                warnings.append(f"{symbol}: overweight delta is smaller than one share.")
        elif delta_value > 0:
            desired_buy_value = delta_value
            reason_code = "buy_new_target" if current_quantity == 0 else "increase_underweight"
            buy_candidates.append((3 if current_quantity == 0 else 4, symbol, 0, reason_code, desired_buy_value))

        diagnostics.append(
            _diagnostic_row(
                symbol=symbol,
                basket_id=basket_by_symbol.get(symbol),
                current_quantity=current_quantity,
                current_price=current_price,
                portfolio_value=portfolio_value,
                target_weight=target_weight,
                planned_side=planned_side,
                planned_quantity=planned_quantity,
                reason_code=reason_code,
            )
        )

    orders: list[dict[str, Any]] = []
    sequence = 1
    for _group, symbol, quantity, _reason_code in sorted(sell_candidates, key=lambda item: (item[0], item[1])):
        orders.append(_order(sequence=sequence, symbol=symbol, side="sell", quantity=quantity))
        sequence += 1

    projected_cash = cash_before + estimated_sell_proceeds
    estimated_buy_cost = Decimal("0")
    planned_buy_quantities: dict[str, int] = {}
    for _group, symbol, _quantity, reason_code, desired_buy_value in sorted(buy_candidates, key=lambda item: (item[0], item[1])):
        current_price = prices[symbol]
        spendable = min(desired_buy_value, projected_cash)
        quantity = int(math.floor(spendable / current_price))
        if quantity <= 0:
            warnings.append(f"{symbol}: projected cash is insufficient to buy one share.")
            continue
        cost = Decimal(quantity) * current_price
        projected_cash -= cost
        estimated_buy_cost += cost
        planned_buy_quantities[symbol] = quantity
        orders.append(_order(sequence=sequence, symbol=symbol, side="buy", quantity=quantity))
        sequence += 1

    for row in diagnostics:
        if row["symbol"] in planned_buy_quantities:
            row["planned_side"] = "buy"
            row["planned_quantity"] = planned_buy_quantities[row["symbol"]]

    intent = "rebalance" if orders else "hold"
    execution_plan = {"schema_version": 1, "intent": intent, "orders": orders}
    return {
        "schema_version": "1.0",
        "date": date,
        "target_portfolio": normalized_targets,
        "current_vs_target": diagnostics,
        "cash_projection": {
            "cash_before": _float(cash_before),
            "estimated_sell_proceeds": _float(estimated_sell_proceeds),
            "estimated_buy_cost": _float(estimated_buy_cost),
            "cash_after_estimate": _float(projected_cash),
            "negative_cash_allowed": False,
        },
        "execution_plan": execution_plan,
        "warnings": warnings,
    }
```

- [ ] **Step 4: Add tool definition binder**

Append:

```python
def make_target_portfolio_to_execution_plan_tool() -> ToolDefinition:
    description = (
        "Convert a target portfolio into a strict market/day execution_plan. "
        "Use this after choosing target symbols and target weights. The tool reads current positions, "
        "cash, portfolio value, and prices from the strategy, then returns current_vs_target diagnostics "
        "and an execution_plan. Do not manually edit the execution_plan returned by this tool."
    )
    metadata = {"kind": "portfolio_transition_planner"}

    def binder(strategy: Any, manager: Any) -> BoundTool:
        def planner_tool(*, date: str | None = None, target_portfolio: list[dict[str, Any]]) -> dict[str, Any]:
            result = target_portfolio_to_execution_plan(
                strategy,
                date=date,
                target_portfolio=target_portfolio,
            )
            strategy._last_target_portfolio_planner_result = result
            return result

        return BoundTool(
            name=TOOL_NAME,
            description=description,
            function=planner_tool,
            source="local",
            metadata=metadata,
        )

    return ToolDefinition(name=TOOL_NAME, description=description, binder=binder, metadata=metadata)
```

- [ ] **Step 5: Run planner tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected:

```text
planner tests pass
older agent tool-surface and prompt tests may fail until Task 3
```

- [ ] **Step 6: Commit planner module**

```powershell
git add lumibot\example_strategies\target_portfolio_to_execution_plan.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: add target portfolio transition planner"
```

---

### Task 3: Register Planner Tool And Narrow Portfolio Decision Agent

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Update imports**

In `ai_trading_team_mock_growth_inflation_quadrant.py`, add:

```python
from lumibot.example_strategies.target_portfolio_to_execution_plan import (
    TOOL_NAME as TARGET_PORTFOLIO_TO_EXECUTION_PLAN_TOOL_NAME,
)
from lumibot.example_strategies.target_portfolio_to_execution_plan import (
    make_target_portfolio_to_execution_plan_tool,
)
```

- [ ] **Step 2: Initialize planner state**

Inside `AITradingTeamMockGrowthInflationQuadrantStrategy.initialize()`, after `_last_mock_regime` is set, add:

```python
self._last_target_portfolio_planner_result = None
```

- [ ] **Step 3: Replace portfolio decision tools**

Replace the `portfolio_decision_agent` tool list:

```python
tools=[
    BuiltinTools.account.positions(),
    BuiltinTools.account.portfolio(),
    BuiltinTools.market.last_price(),
],
```

with:

```python
tools=[make_target_portfolio_to_execution_plan_tool()],
```

- [ ] **Step 4: Replace portfolio decision system prompt**

Replace the existing `portfolio_decision_agent` `system_prompt` with:

```python
system_prompt=(
    "Portfolio decision role: do not redo macro or basket research. Merge the macro allocation report "
    "and basket reports into a target_portfolio, then call target_portfolio_to_execution_plan. Do not "
    "place orders. Do not manually calculate share quantities, cash usage, order side, or order sequence. "
    "The planner tool owns all execution_plan calculations. Return only one valid JSON object; do not "
    "include markdown, RESULT text, or prose after the JSON. The top-level fields decision, "
    "target_portfolio, and execution_plan are required. decision must include type and reason_brief. "
    "target_portfolio must list the selected active basket targets as symbols and target weights. "
    "The final execution_plan must be copied exactly from the planner tool result. Do not modify "
    "tool-generated quantities, sides, order_type, time_in_force, or sequence values."
),
```

- [ ] **Step 5: Update portfolio decision task prompt**

Replace the portfolio decision `task_prompt` with:

```python
task_prompt=(
    "Create target_portfolio from the provided macro and basket reports, then call "
    "target_portfolio_to_execution_plan with date and target_portfolio. Return only the strict JSON "
    "object with decision, target_portfolio, and the planner tool's execution_plan copied exactly."
),
```

- [ ] **Step 6: Update agent tool-surface test**

In `test_agents_receive_distinct_tool_surfaces`, replace:

```python
assert created_tool_names(created["portfolio_decision_agent"]) == {
    "account_positions",
    "account_portfolio",
    "market_last_price",
}
```

with:

```python
assert created_tool_names(created["portfolio_decision_agent"]) == {
    "target_portfolio_to_execution_plan",
}
```

- [ ] **Step 7: Update prompt boundary test**

In `test_prompt_boundaries_are_short_and_role_specific`, replace the portfolio decision prompt expectations:

```python
assert "before any non-hold plan, call account_positions and account_portfolio" in serialized
assert "before sizing any buy order, call market_last_price" in serialized
```

with:

```python
assert "call target_portfolio_to_execution_plan" in serialized
assert "do not manually calculate share quantities" in serialized
assert "planner tool owns all execution_plan calculations" in serialized
```

- [ ] **Step 8: Replace strict execution plan prompt test**

Rename `test_portfolio_decision_prompt_declares_strict_execution_plan_contract` to:

```python
def test_portfolio_decision_prompt_delegates_execution_plan_to_planner_tool():
```

Replace required phrases with:

```python
for required_phrase in (
    "merge the macro allocation report",
    "call target_portfolio_to_execution_plan",
    "do not manually calculate share quantities",
    "planner tool owns all execution_plan calculations",
    "execution_plan must be copied exactly from the planner tool result",
    "do not modify tool-generated quantities",
):
    assert required_phrase in portfolio_prompt
```

Also assert removed manual-sizing language:

```python
for forbidden_phrase in (
    "before sizing any buy order",
    "call account_positions",
    "call account_portfolio",
    "call market_last_price",
):
    assert forbidden_phrase not in portfolio_prompt
```

- [ ] **Step 9: Run updated surface/prompt tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected:

```text
agent surface and prompt tests pass
workflow tests may still fail until Task 4
```

- [ ] **Step 10: Commit agent registration and prompt changes**

```powershell
git add lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: delegate portfolio execution planning to tool"
```

---

### Task 4: Validate Final Summary Matches Planner Result

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Refactor execution plan normalization helper**

In `ai_trading_team_mock_growth_inflation_quadrant.py`, extract the body of `parse_execution_plan_from_portfolio_summary()` after `plan = _require_dict(...)` into:

```python
def normalize_execution_plan(plan: Any) -> dict[str, Any]:
    plan = _require_dict(plan, "execution_plan")

    if "schema_version" not in plan:
        raise ValueError("execution_plan schema_version is required.")
    schema_version = plan["schema_version"]
    if isinstance(schema_version, bool):
        raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")
    if isinstance(schema_version, (int, float)):
        if schema_version != 1:
            raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")
    elif isinstance(schema_version, str):
        if schema_version.strip() not in {"1", "1.0"}:
            raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")
    else:
        raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")

    intent = str(plan.get("intent") or "").strip().lower()
    if not intent:
        raise ValueError("execution_plan intent is required.")
    if intent not in ALLOWED_INTENTS:
        raise ValueError(f"unsupported execution_plan intent: {intent}")

    if "orders" not in plan:
        raise ValueError("execution_plan orders are required.")
    orders = plan["orders"]
    if not isinstance(orders, list):
        raise ValueError("execution_plan orders must be a list.")
    if intent == "hold" and orders:
        raise ValueError("hold intent cannot include orders.")
    if intent == "rebalance" and not orders:
        raise ValueError("execution_plan orders are required for rebalance intent.")

    normalized_orders = sorted((_normalize_order(order) for order in orders), key=lambda order: order["sequence"])
    order_sequences = [order["sequence"] for order in normalized_orders]
    if len(order_sequences) != len(set(order_sequences)):
        raise ValueError("duplicate order sequence.")

    buy_seen = False
    for order in normalized_orders:
        if order["side"] == "buy":
            buy_seen = True
        elif buy_seen and order["side"] == "sell":
            raise ValueError("execution_plan must place sell orders before buy orders.")

    return {
        "schema_version": 1,
        "intent": intent,
        "orders": normalized_orders,
        "constraints": dict(SYSTEM_EXECUTION_CONSTRAINTS),
    }
```

Then simplify `parse_execution_plan_from_portfolio_summary()` to:

```python
def parse_execution_plan_from_portfolio_summary(summary: str) -> dict[str, Any]:
    raw_json = _extract_first_json_object(summary)
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Portfolio summary JSON is invalid: {exc.msg}.") from exc

    payload = _require_dict(payload, "portfolio summary JSON")
    if "execution_plan" not in payload:
        raise ValueError("portfolio summary JSON must include execution_plan.")
    return normalize_execution_plan(payload["execution_plan"])
```

- [ ] **Step 2: Add planner-result validation helper**

Add:

```python
def validate_execution_plan_matches_planner_result(strategy: Any, execution_plan: dict[str, Any]) -> None:
    planner_result = getattr(strategy, "_last_target_portfolio_planner_result", None)
    if not isinstance(planner_result, dict):
        raise ValueError("portfolio_decision_agent must call target_portfolio_to_execution_plan before execution.")
    planner_plan = normalize_execution_plan(planner_result.get("execution_plan"))
    if execution_plan != planner_plan:
        raise ValueError("portfolio_decision_agent execution_plan differs from target_portfolio_to_execution_plan result.")
```

- [ ] **Step 3: Replace evidence validator behavior**

Replace `validate_portfolio_decision_tool_evidence()` with:

```python
def validate_portfolio_decision_tool_evidence(execution_plan: dict[str, Any], decision_result: Any) -> None:
    tool_names = _agent_result_tool_names(decision_result)
    if TARGET_PORTFOLIO_TO_EXECUTION_PLAN_TOOL_NAME not in tool_names:
        raise ValueError("portfolio_decision_agent must call target_portfolio_to_execution_plan.")
```

This should apply even for a hold plan. A hold plan should come from the planner, not from LLM-only reasoning.

- [ ] **Step 4: Call planner-result validation in workflow**

In `on_trading_iteration()`, after parsing the execution plan, change:

```python
validate_portfolio_decision_tool_evidence(execution_plan, portfolio_result)
validate_execution_plan_symbols(execution_plan, list(basket_reports_by_id.values()))
```

to:

```python
validate_portfolio_decision_tool_evidence(execution_plan, portfolio_result)
validate_execution_plan_matches_planner_result(self, execution_plan)
validate_execution_plan_symbols(execution_plan, list(basket_reports_by_id.values()))
```

- [ ] **Step 5: Add validation tests**

Append:

```python
def test_validate_execution_plan_matches_planner_result_accepts_exact_tool_plan():
    module, _strategy_class = load_strategy_module()
    strategy = SimpleNamespace(
        _last_target_portfolio_planner_result={
            "execution_plan": {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [
                    {
                        "sequence": 1,
                        "action": "submit_order",
                        "symbol": "SPY",
                        "side": "buy",
                        "quantity_mode": "shares",
                        "quantity": 10,
                        "asset_type": "stock",
                        "order_type": "market",
                        "time_in_force": "day",
                    }
                ],
            }
        }
    )
    execution_plan = module.normalize_execution_plan(strategy._last_target_portfolio_planner_result["execution_plan"])

    module.validate_execution_plan_matches_planner_result(strategy, execution_plan)


def test_validate_execution_plan_matches_planner_result_rejects_llm_rewrite():
    module, _strategy_class = load_strategy_module()
    strategy = SimpleNamespace(
        _last_target_portfolio_planner_result={
            "execution_plan": {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [
                    {
                        "sequence": 1,
                        "action": "submit_order",
                        "symbol": "SPY",
                        "side": "buy",
                        "quantity_mode": "shares",
                        "quantity": 10,
                        "asset_type": "stock",
                        "order_type": "market",
                        "time_in_force": "day",
                    }
                ],
            }
        }
    )
    rewritten = module.normalize_execution_plan(
        {
            "schema_version": 1,
            "intent": "rebalance",
            "orders": [
                {
                    "sequence": 1,
                    "action": "submit_order",
                    "symbol": "SPY",
                    "side": "buy",
                    "quantity_mode": "shares",
                    "quantity": 11,
                    "asset_type": "stock",
                    "order_type": "market",
                    "time_in_force": "day",
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="differs from target_portfolio_to_execution_plan"):
        module.validate_execution_plan_matches_planner_result(strategy, rewritten)


def test_validate_portfolio_decision_tool_evidence_requires_planner_tool_even_for_hold():
    module, _strategy_class = load_strategy_module()
    execution_plan = {"schema_version": 1, "intent": "hold", "orders": []}
    decision_result = SimpleNamespace(tool_calls=[])

    with pytest.raises(ValueError, match="must call target_portfolio_to_execution_plan"):
        module.validate_portfolio_decision_tool_evidence(execution_plan, decision_result)
```

- [ ] **Step 6: Run validation tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected:

```text
all unit tests pass
```

- [ ] **Step 7: Commit validation changes**

```powershell
git add lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "test: guard planner-generated execution plans"
```

---

### Task 5: Update Workflow Tests For Planner Tool Path

**Files:**
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add a workflow test where portfolio result uses planner-generated execution plan**

Append:

```python
def test_on_trading_iteration_executes_planner_generated_plan(monkeypatch):
    module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()

    planner_plan = {
        "schema_version": 1,
        "intent": "rebalance",
        "orders": [
            {
                "sequence": 1,
                "action": "submit_order",
                "symbol": "SPY",
                "side": "buy",
                "quantity_mode": "shares",
                "quantity": 10,
                "asset_type": "stock",
                "order_type": "market",
                "time_in_force": "day",
            }
        ],
    }
    strategy._last_target_portfolio_planner_result = {"execution_plan": planner_plan}
    agent_manager.tool_calls["portfolio_decision_agent"] = ["target_portfolio_to_execution_plan"]
    agent_manager.summaries = {
        "macro_allocation_agent": json.dumps(
            {
                "regime": "growth_up_inflation_down",
                "basket_weights": {
                    "equity": 0.50,
                    "commodity": 0.25,
                    "tips": 0.00,
                    "nominal_bond": 0.25,
                },
                "mock": True,
                "regime_changed": False,
                "reason_brief": "mock",
            }
        ),
        "equity_basket_agent": json.dumps(
            {
                "basket_id": "equity",
                "target_weight": 0.50,
                "status": "active",
                "candidate_symbols": ["SPY"],
                "selected_symbol": "SPY",
                "reason_brief": "mock",
            }
        ),
        "commodity_basket_agent": json.dumps(
            {
                "basket_id": "commodity",
                "target_weight": 0.25,
                "status": "inactive",
                "candidate_symbols": ["GLD"],
                "selected_symbol": None,
                "reason_brief": "mock",
            }
        ),
        "tips_basket_agent": json.dumps(
            {
                "basket_id": "tips",
                "target_weight": 0.00,
                "status": "inactive",
                "candidate_symbols": ["TIP"],
                "selected_symbol": None,
                "reason_brief": "mock",
            }
        ),
        "nominal_bond_basket_agent": json.dumps(
            {
                "basket_id": "nominal_bond",
                "target_weight": 0.25,
                "status": "inactive",
                "candidate_symbols": ["VGIT"],
                "selected_symbol": None,
                "reason_brief": "mock",
            }
        ),
        "portfolio_decision_agent": json.dumps(
            {
                "decision": {"type": "target_portfolio", "reason_brief": "mock"},
                "target_portfolio": [{"basket_id": "equity", "symbol": "SPY", "target_weight": 0.50}],
                "execution_plan": planner_plan,
            }
        ),
    }

    strategy.on_trading_iteration()

    execution_agent = agent_manager["execution_agent"]
    assert len(execution_agent.calls) == 1
    assert execution_agent.calls[0]["context"] == {
        "date": "2024-09-05",
        "execution_plan": module.normalize_execution_plan(planner_plan),
    }
```

- [ ] **Step 2: Add a workflow test that blocks rewritten execution plan**

Append:

```python
def test_on_trading_iteration_blocks_when_portfolio_agent_rewrites_planner_plan():
    module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()

    planner_plan = {
        "schema_version": 1,
        "intent": "rebalance",
        "orders": [
            {
                "sequence": 1,
                "action": "submit_order",
                "symbol": "SPY",
                "side": "buy",
                "quantity_mode": "shares",
                "quantity": 10,
                "asset_type": "stock",
                "order_type": "market",
                "time_in_force": "day",
            }
        ],
    }
    rewritten_plan = {
        "schema_version": 1,
        "intent": "rebalance",
        "orders": [
            {
                "sequence": 1,
                "action": "submit_order",
                "symbol": "SPY",
                "side": "buy",
                "quantity_mode": "shares",
                "quantity": 11,
                "asset_type": "stock",
                "order_type": "market",
                "time_in_force": "day",
            }
        ],
    }
    strategy._last_target_portfolio_planner_result = {"execution_plan": planner_plan}
    agent_manager.tool_calls["portfolio_decision_agent"] = ["target_portfolio_to_execution_plan"]
    agent_manager.summaries.update(
        {
            "macro_allocation_agent": json.dumps(
                {
                    "regime": "growth_up_inflation_down",
                    "basket_weights": {
                        "equity": 0.50,
                        "commodity": 0.25,
                        "tips": 0.00,
                        "nominal_bond": 0.25,
                    },
                    "mock": True,
                    "regime_changed": False,
                    "reason_brief": "mock",
                }
            ),
            "equity_basket_agent": json.dumps(
                {
                    "basket_id": "equity",
                    "target_weight": 0.50,
                    "status": "active",
                    "candidate_symbols": ["SPY"],
                    "selected_symbol": "SPY",
                    "reason_brief": "mock",
                }
            ),
            "commodity_basket_agent": json.dumps(
                {
                    "basket_id": "commodity",
                    "target_weight": 0.25,
                    "status": "inactive",
                    "candidate_symbols": ["GLD"],
                    "selected_symbol": None,
                    "reason_brief": "mock",
                }
            ),
            "tips_basket_agent": json.dumps(
                {
                    "basket_id": "tips",
                    "target_weight": 0.00,
                    "status": "inactive",
                    "candidate_symbols": ["TIP"],
                    "selected_symbol": None,
                    "reason_brief": "mock",
                }
            ),
            "nominal_bond_basket_agent": json.dumps(
                {
                    "basket_id": "nominal_bond",
                    "target_weight": 0.25,
                    "status": "inactive",
                    "candidate_symbols": ["VGIT"],
                    "selected_symbol": None,
                    "reason_brief": "mock",
                }
            ),
            "portfolio_decision_agent": json.dumps(
                {
                    "decision": {"type": "target_portfolio", "reason_brief": "mock"},
                    "target_portfolio": [{"basket_id": "equity", "symbol": "SPY", "target_weight": 0.50}],
                    "execution_plan": rewritten_plan,
                }
            ),
        }
    )

    strategy.on_trading_iteration()

    assert "differs from target_portfolio_to_execution_plan" in strategy._last_execution_plan_error
    assert agent_manager["execution_agent"].calls == []
```

- [ ] **Step 3: Run workflow tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 4: Commit workflow tests**

```powershell
git add tests\test_ai_trading_team_mock_growth_inflation_quadrant.py lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "test: route mock quadrant workflow through transition planner"
```

---

### Task 6: End-To-End Verification

**Files:**
- No code edits unless verification exposes a defect.

- [ ] **Step 1: Run targeted pytest**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 2: Run ruff on touched files**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py lumibot\example_strategies\target_portfolio_to_execution_plan.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 3: Run one-day mock quadrant backtest**

Use the OpenAI key from `project_notes/API.txt` through the existing environment-loading method used in this branch. Do not print the key.

Run:

```powershell
$env:AI_TRADING_TEAM_MODEL="openai/gpt-5.6-luna"
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-05 --max-workers 1 --max-run-attempts 1
```

Expected:

```text
mock-growth-inflation-quadrant result status is passed
Agent Replay shows portfolio_decision_agent called target_portfolio_to_execution_plan
execution_agent receives execution_plan only
```

- [ ] **Step 4: Run two-day switching backtest**

Run:

```powershell
$env:AI_TRADING_TEAM_MODEL="openai/gpt-5.6-luna"
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-09 --max-workers 1 --max-run-attempts 1
```

Expected:

```text
result status is passed
second trading day includes planner-generated transition orders when target portfolio changes
critical case includes partial reduction and increase when current-vs-target requires them
```

- [ ] **Step 5: Inspect trace output**

Find the latest artifact root:

```powershell
Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
```

Then inspect trace JSON:

```powershell
rg -n "target_portfolio_to_execution_plan|current_vs_target|cash_projection" artifacts\ai_trading_team_example_benchmarks
```

Expected:

```text
Trace contains target_portfolio_to_execution_plan tool call input and output.
Tool output includes current_vs_target, cash_projection, execution_plan, and warnings.
```

- [ ] **Step 6: Commit only verification-related fixes**

If verification required no code changes, do not create an empty commit.

If verification exposed a defect and it was fixed:

```powershell
git add lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py lumibot\example_strategies\target_portfolio_to_execution_plan.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "fix: stabilize transition planner verification"
```

---

## Self-Review Checklist

- Spec section 3 is covered by Tasks 2, 3, and 4.
- Tool input/output in spec sections 8-10 is covered by Tasks 1 and 2.
- Calculation behavior in spec section 11 is covered by Task 1 tests and Task 2 implementation.
- Prompt changes in spec section 13 are covered by Task 3.
- Planner-result validation in spec section 14 is covered by Task 4.
- Trace/UI compatibility in spec section 15 is covered by Task 6.
- Test scenarios in spec section 16 are covered by Tasks 1 and 5.
- Acceptance criteria in spec section 17 are covered by Tasks 3-6.
