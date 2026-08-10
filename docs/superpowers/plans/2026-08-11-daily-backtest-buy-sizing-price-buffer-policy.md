# Daily Backtest Buy Sizing Price and Buffer Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `target_portfolio_to_execution_plan` use a clearer daily-backtest buy sizing price basis and a default 2% buy sizing buffer so market/day fills can differ from estimates without routinely producing negative cash.

**Architecture:** Keep the fix inside the deterministic portfolio planner tool instead of changing global `YahooDataBacktesting.get_last_price` semantics. Add a local sizing-price helper, expose sizing diagnostics in the tool output, and keep portfolio/execution prompts aligned with the existing division of responsibility.

**Tech Stack:** Python, Lumibot strategy/tool code, pytest, local benchmark runner, existing agent trace/replay infrastructure.

---

## Source Spec

Implement:

`docs/superpowers/specs/2026-08-11-daily-backtest-buy-sizing-price-buffer-policy-design.md`

The key policy is:

```text
sizing_price = previous completed daily close when available
fallback = strategy.get_last_price(symbol)
buy sizing budget = desired buy value * 0.98
quantity = floor(buy sizing budget / sizing_price)
2% buffer is not a hard execution cap
```

---

## File Structure

Modify:

- `lumibot/example_strategies/target_portfolio_to_execution_plan.py`
  - Add `BUY_SIZING_BUFFER_PCT`.
  - Add a structured sizing price helper.
  - Apply the 2% buffer only to buy sizing.
  - Add sizing diagnostics to `current_vs_target` and `cash_projection`.
  - Update the tool description.

- `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
  - Add a concise portfolio decision prompt sentence stating that the planner owns sizing price and buffer.
  - Ensure execution prompt does not mention the buffer.

- `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
  - Extend the planner fake strategy with optional historical close support.
  - Update existing quantity expectations for the new 2% buy buffer.
  - Add tests for previous-close sizing, fallback sizing, diagnostics, and prompt/tool description wording.

No new runtime modules are required.

---

## Task 1: Add Failing Planner Tests for Daily Close Sizing and 2% Buy Buffer

**Files:**

- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Extend imports for pandas**

At the top of `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`, add:

```python
import pandas as pd
```

Keep existing imports unchanged.

- [ ] **Step 2: Extend `make_planner_strategy` with historical daily closes**

Replace the current helper:

```python
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

with:

```python
def make_planner_strategy(
    *,
    positions,
    prices,
    cash=0.0,
    portfolio_value=100000.0,
    historical_closes=None,
):
    historical_closes = historical_closes or {}

    def normalize_symbol(symbol):
        if not isinstance(symbol, str):
            symbol = getattr(symbol, "symbol", symbol)
        return str(symbol).upper()

    def get_positions(include_cash_positions=False):
        return list(positions)

    def get_last_price(symbol, quote=None, exchange=None):
        symbol = normalize_symbol(symbol)
        if symbol not in prices:
            return None
        return prices[symbol]

    def get_historical_prices(symbol, length, timestep="day", **kwargs):
        symbol = normalize_symbol(symbol)
        if symbol not in historical_closes:
            return None
        rows = historical_closes[symbol]
        if not rows:
            return None
        if isinstance(rows, dict):
            rows = [rows]
        frame = pd.DataFrame(rows)
        if "date" in frame.columns:
            frame.index = pd.to_datetime(frame["date"])
        return SimpleNamespace(pandas_df=frame)

    return SimpleNamespace(
        get_positions=get_positions,
        get_cash=lambda: cash,
        get_portfolio_value=lambda: portfolio_value,
        get_last_price=get_last_price,
        get_historical_prices=get_historical_prices,
    )
```

- [ ] **Step 3: Add a failing test proving previous completed daily close is preferred**

Add this test near the existing `target_portfolio_to_execution_plan` tests:

```python
def test_target_portfolio_to_execution_plan_prefers_previous_completed_daily_close_for_sizing():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[],
        cash=100000,
        portfolio_value=100000,
        prices={"GLD": 229.789993},
        historical_closes={
            "GLD": {"date": "2024-09-04", "close": 230.429993},
        },
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-05",
        target_portfolio=[{"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.25}],
    )

    diagnostics = {row["symbol"]: row for row in result["current_vs_target"]}
    gld = diagnostics["GLD"]
    assert gld["sizing_price"] == pytest.approx(230.429993)
    assert gld["sizing_price_source"] == "previous_completed_daily_close"
    assert gld["sizing_price_datetime"] == "2024-09-04"
    assert gld["buy_sizing_buffer_pct"] == pytest.approx(0.02)
    assert gld["effective_buy_target_value"] == pytest.approx(24500.0)
    assert result["execution_plan"]["orders"][0]["quantity"] == 106
```

Expected under current code: FAIL because `sizing_price`, `sizing_price_source`, and `effective_buy_target_value` do not exist, and quantity is based on `get_last_price`.

- [ ] **Step 4: Add a failing test proving fallback uses `strategy.get_last_price`**

Add:

```python
def test_target_portfolio_to_execution_plan_falls_back_to_last_price_when_daily_close_missing():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[],
        cash=100000,
        portfolio_value=100000,
        prices={"GLD": 229.789993},
        historical_closes={},
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-05",
        target_portfolio=[{"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.25}],
    )

    diagnostics = {row["symbol"]: row for row in result["current_vs_target"]}
    gld = diagnostics["GLD"]
    assert gld["sizing_price"] == pytest.approx(229.789993)
    assert gld["sizing_price_source"] == "strategy_last_price_fallback"
    assert gld["sizing_price_datetime"] is None
    assert result["execution_plan"]["orders"][0]["quantity"] == 106
```

Expected under current code: FAIL because diagnostics do not include the new fields.

- [ ] **Step 5: Update all-cash buy test to expect 2% buy buffer**

In `test_target_portfolio_to_execution_plan_deploys_all_cash_to_targets`, update expected buy quantities:

```python
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
            "quantity": 245,
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
            "quantity": 490,
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
            "quantity": 490,
            "asset_type": "stock",
            "order_type": "market",
            "time_in_force": "day",
        },
    ],
}
assert result["cash_projection"]["cash_after_estimate"] == pytest.approx(2000)
assert result["cash_projection"]["buy_sizing_buffer_pct"] == pytest.approx(0.02)
```

Expected under current code: FAIL because old quantities spend all cash.

- [ ] **Step 6: Run focused tests and confirm failure**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py::test_target_portfolio_to_execution_plan_prefers_previous_completed_daily_close_for_sizing tests/test_ai_trading_team_mock_growth_inflation_quadrant.py::test_target_portfolio_to_execution_plan_falls_back_to_last_price_when_daily_close_missing tests/test_ai_trading_team_mock_growth_inflation_quadrant.py::test_target_portfolio_to_execution_plan_deploys_all_cash_to_targets -q
```

Expected: FAIL. The failures should mention missing diagnostic keys and the old unbuffered quantities.

- [ ] **Step 7: Commit failing tests**

```powershell
git add tests/test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "test: pin daily buy sizing price and buffer policy"
```

---

## Task 2: Implement Sizing Price Helper and Buy Buffer in Planner

**Files:**

- Modify: `lumibot/example_strategies/target_portfolio_to_execution_plan.py`

- [ ] **Step 1: Add imports and constants**

Change the imports at the top:

```python
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
```

Add constants under `TARGET_WEIGHT_TOLERANCE`:

```python
BUY_SIZING_BUFFER_PCT = Decimal("0.02")
SIZING_PRICE_SOURCE_PREVIOUS_CLOSE = "previous_completed_daily_close"
SIZING_PRICE_SOURCE_LAST_PRICE_FALLBACK = "strategy_last_price_fallback"
```

Add the dataclass after `QUOTE_SYMBOLS`:

```python
@dataclass(frozen=True)
class SizingPrice:
    price: Decimal
    source: str
    datetime: str | None = None
```

- [ ] **Step 2: Add dataframe and datetime helpers**

Add after `_float`:

```python
def _bars_dataframe(bars: Any) -> Any | None:
    if bars is None:
        return None
    frame = getattr(bars, "pandas_df", None)
    if frame is None:
        frame = getattr(bars, "df", None)
    if frame is None or not hasattr(frame, "empty") or frame.empty:
        return None
    return frame


def _last_row_datetime_text(frame: Any) -> str | None:
    try:
        row = frame.iloc[-1]
    except Exception:
        return None
    for column in ("date", "Date", "datetime", "Datetime"):
        if column in frame.columns:
            value = row[column]
            return str(value.date() if hasattr(value, "date") else value)
    try:
        index_value = frame.index[-1]
    except Exception:
        return None
    if index_value is None:
        return None
    return str(index_value.date() if hasattr(index_value, "date") else index_value)
```

- [ ] **Step 3: Add previous-close and fallback helpers**

Replace `_get_last_price` with:

```python
def _finite_positive_price(value: Any, label: str) -> Decimal:
    price = _decimal(value, label)
    if price <= 0:
        raise ValueError(f"{label} must be positive.")
    return price


def _get_previous_completed_daily_close(strategy: Any, symbol: str) -> SizingPrice | None:
    get_historical_prices = getattr(strategy, "get_historical_prices", None)
    if get_historical_prices is None:
        return None

    try:
        bars = get_historical_prices(symbol, 1, "day")
    except TypeError:
        try:
            bars = get_historical_prices(symbol, 1, timestep="day")
        except Exception:
            return None
    except Exception:
        return None

    frame = _bars_dataframe(bars)
    if frame is None or "close" not in frame.columns:
        return None

    close_value = frame["close"].iloc[-1]
    price = _finite_positive_price(close_value, f"previous completed daily close for {symbol}")
    return SizingPrice(
        price=price,
        source=SIZING_PRICE_SOURCE_PREVIOUS_CLOSE,
        datetime=_last_row_datetime_text(frame),
    )


def _get_last_price_fallback(strategy: Any, symbol: str) -> SizingPrice:
    raw_price = strategy.get_last_price(symbol)
    if raw_price is None:
        raise ValueError(f"missing sizing price for {symbol}")
    return SizingPrice(
        price=_finite_positive_price(raw_price, f"last price fallback for {symbol}"),
        source=SIZING_PRICE_SOURCE_LAST_PRICE_FALLBACK,
        datetime=None,
    )


def _get_sizing_price(strategy: Any, symbol: str) -> SizingPrice:
    previous_close = _get_previous_completed_daily_close(strategy, symbol)
    if previous_close is not None:
        return previous_close
    return _get_last_price_fallback(strategy, symbol)
```

- [ ] **Step 4: Update diagnostics row function signature and payload**

Change `_diagnostic_row` signature from:

```python
    current_price: Decimal,
```

to:

```python
    sizing_price: SizingPrice,
```

Inside `_diagnostic_row`, replace:

```python
current_value = current_quantity * current_price
```

with:

```python
current_price = sizing_price.price
current_value = current_quantity * current_price
desired_buy_value = max(target_value - current_value, Decimal("0"))
effective_buy_target_value = (
    desired_buy_value * (Decimal("1") - BUY_SIZING_BUFFER_PCT)
    if desired_buy_value > 0
    else Decimal("0")
)
```

Add these fields to the returned dict immediately after `current_price`:

```python
"sizing_price": _float(sizing_price.price),
"sizing_price_source": sizing_price.source,
"sizing_price_datetime": sizing_price.datetime,
```

Add these fields after `target_value`:

```python
"desired_buy_value": _float(desired_buy_value),
"effective_buy_target_value": _float(effective_buy_target_value),
"buy_sizing_buffer_pct": _float(BUY_SIZING_BUFFER_PCT),
```

- [ ] **Step 5: Use sizing price map in planner**

Replace:

```python
prices = {symbol: _get_last_price(strategy, symbol) for symbol in relevant_symbols}
```

with:

```python
sizing_prices = {symbol: _get_sizing_price(strategy, symbol) for symbol in relevant_symbols}
```

Then replace reads of `prices[symbol]` with:

```python
sizing_prices[symbol].price
```

When calling `_diagnostic_row`, pass:

```python
sizing_price=sizing_prices[symbol],
```

- [ ] **Step 6: Apply 2% buffer only to buy candidates**

In the buy loop, replace:

```python
current_price = prices[symbol]
spendable = min(desired_buy_value, projected_cash)
quantity = int(math.floor(spendable / current_price))
```

with:

```python
current_price = sizing_prices[symbol].price
effective_buy_value = desired_buy_value * (Decimal("1") - BUY_SIZING_BUFFER_PCT)
spendable = min(effective_buy_value, projected_cash)
quantity = int(math.floor(spendable / current_price))
```

Keep all sell calculations unbuffered.

- [ ] **Step 7: Add buffer to cash projection**

Add to `cash_projection`:

```python
"buy_sizing_buffer_pct": _float(BUY_SIZING_BUFFER_PCT),
```

- [ ] **Step 8: Update tool description**

Replace the current description in `make_target_portfolio_to_execution_plan_tool` with:

```python
description = (
    "Convert a target portfolio into a strict market/day execution_plan. "
    "Use this after choosing target symbols and target weights. The tool reads current positions, "
    "cash, portfolio value, and deterministic sizing prices from the strategy. In daily backtests, "
    "buy sizing uses the previous completed daily close when available and applies a default 2% "
    "buy sizing buffer. The buffer reduces planned buy quantity only; it is not a hard execution "
    "price cap. The tool returns current_vs_target diagnostics and an execution_plan. Do not "
    "manually edit the execution_plan returned by this tool."
)
```

- [ ] **Step 9: Run focused tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py::test_target_portfolio_to_execution_plan_prefers_previous_completed_daily_close_for_sizing tests/test_ai_trading_team_mock_growth_inflation_quadrant.py::test_target_portfolio_to_execution_plan_falls_back_to_last_price_when_daily_close_missing tests/test_ai_trading_team_mock_growth_inflation_quadrant.py::test_target_portfolio_to_execution_plan_deploys_all_cash_to_targets -q
```

Expected: PASS.

- [ ] **Step 10: Commit planner implementation**

```powershell
git add lumibot/example_strategies/target_portfolio_to_execution_plan.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: apply daily buy sizing price buffer"
```

---

## Task 3: Update Planner Quantity Regression Tests

**Files:**

- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Update full rebalance expected buy quantities**

In `test_target_portfolio_to_execution_plan_handles_full_rebalance_regression_case`, update the expected order tuples to:

```python
assert [(order["side"], order["symbol"], order["quantity"]) for order in orders] == [
    ("sell", "VGIT", 500),
    ("sell", "SPY", 250),
    ("buy", "TIP", 245),
    ("buy", "GLD", 245),
]
assert result["cash_projection"]["cash_after_estimate"] == pytest.approx(1000)
```

- [ ] **Step 2: Update basket internal symbol switch expected buy quantity**

In `test_target_portfolio_to_execution_plan_handles_basket_internal_symbol_switch`, update:

```python
assert [
    (order["side"], order["symbol"], order["quantity"])
    for order in result["execution_plan"]["orders"]
] == [
    ("sell", "SPY", 500),
    ("buy", "QQQ", 245),
]
```

- [ ] **Step 3: Update price drift rebalance expected buy quantity**

In `test_target_portfolio_to_execution_plan_rebalances_price_drift`, update:

```python
assert [
    (order["side"], order["symbol"], order["quantity"])
    for order in result["execution_plan"]["orders"]
] == [
    ("sell", "GLD", 30),
    ("sell", "SPY", 70),
    ("buy", "VGIT", 196),
]
```

- [ ] **Step 4: Add test proving buffer does not alter sell quantities**

Add:

```python
def test_target_portfolio_to_execution_plan_buy_buffer_does_not_apply_to_sells():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[make_position("SPY", 500)],
        cash=0,
        portfolio_value=50000,
        prices={"SPY": 100},
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-06",
        target_portfolio=[],
    )

    assert [
        (order["side"], order["symbol"], order["quantity"])
        for order in result["execution_plan"]["orders"]
    ] == [("sell", "SPY", 500)]
    diagnostics = {row["symbol"]: row for row in result["current_vs_target"]}
    assert diagnostics["SPY"]["planned_quantity"] == 500
```

- [ ] **Step 5: Run all mock quadrant tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit regression test updates**

```powershell
git add tests/test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "test: update planner regressions for buy sizing buffer"
```

---

## Task 4: Align Prompts and Tool Description Expectations

**Files:**

- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add prompt assertion for portfolio decision ownership**

Find the existing test that serializes created agent prompts. It currently asserts phrases such as:

```python
assert "call target_portfolio_to_execution_plan" in serialized
assert "call orders_preflight_check" in serialized
assert "orders_submit_and_confirm_order" in serialized
```

Add:

```python
assert "planner tool owns daily backtest buy sizing" in serialized
assert "2% buy sizing buffer" not in serialized.split("Execution role:", 1)[-1]
```

If the test does not already split safely, use:

```python
execution_section = serialized.split("Execution role:", 1)[-1]
assert "2% buy sizing buffer" not in execution_section
```

- [ ] **Step 2: Update portfolio decision agent prompt**

In `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`, inside the `portfolio_decision_agent` `system_prompt`, add this sentence after the sentence that says the planner tool owns all execution plan calculations:

```python
"The planner tool owns daily backtest buy sizing, including its price basis and buy sizing buffer. "
```

The full relevant prompt segment should read:

```python
"Do not manually calculate share quantities, cash usage, order side, or order sequence. "
"The planner tool owns all execution_plan calculations. "
"The planner tool owns daily backtest buy sizing, including its price basis and buy sizing buffer. "
"Return only one valid JSON object; do not "
```

- [ ] **Step 3: Verify execution prompt still hides buffer policy**

Confirm the `execution_agent` `system_prompt` does not mention:

```text
2%
buffer
sizing price
```

If any of those terms appear in the execution agent prompt, remove that sentence unless it is part of an unrelated phrase in code outside the prompt.

- [ ] **Step 4: Add test for planner tool description**

Add:

```python
def test_target_portfolio_to_execution_plan_tool_description_explains_sizing_policy():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")

    tool_definition = planner.make_target_portfolio_to_execution_plan_tool()

    assert "previous completed daily close" in tool_definition.description
    assert "default 2% buy sizing buffer" in tool_definition.description
    assert "not a hard execution price cap" in tool_definition.description
    assert "Do not manually edit the execution_plan" in tool_definition.description
```

- [ ] **Step 5: Run prompt and description tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit prompt alignment**

```powershell
git add lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "chore: align prompts with planner sizing policy"
```

---

## Task 5: Verify Order Tool Behavior Is Not Changed

**Files:**

- Read only unless tests fail unexpectedly:
  - `lumibot/components/agents/builtins.py`
  - `tests/test_agent_tool_permissions.py`

- [ ] **Step 1: Inspect order tool descriptions for sizing ownership**

Run:

```powershell
rg -n "sizing|buffer|share quantities|quantity|orders_preflight_check|orders_submit_and_confirm_order" lumibot/components/agents/builtins.py tests/test_agent_tool_permissions.py
```

Expected:

- `orders_preflight_check` can mention quantity validation and estimated cost.
- `orders_submit_and_confirm_order` can require a matching preflight.
- Neither tool should claim it calculates target-portfolio share quantities.

- [ ] **Step 2: Run order tool tests**

Run:

```powershell
python -m pytest tests/test_agent_tool_permissions.py::test_orders_preflight_check_ready_buy_returns_structured_snapshot tests/test_agent_tool_permissions.py::test_orders_submit_and_confirm_order_submits_and_confirms_after_matching_preflight tests/test_agent_tool_permissions.py::test_orders_submit_and_confirm_order_blocks_negative_cash_before_confirmation -q
```

Expected: PASS.

- [ ] **Step 3: Commit only if files changed**

If Step 1 required a wording cleanup, run:

```powershell
git add lumibot/components/agents/builtins.py tests/test_agent_tool_permissions.py
git commit -m "chore: keep order tools separate from sizing policy"
```

If no files changed, do not create a commit for this task.

---

## Task 6: Run Focused Regression Suite

**Files:**

- No code changes expected.

- [ ] **Step 1: Run target planner and order tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py tests/test_agent_tool_permissions.py::test_orders_preflight_check_ready_buy_returns_structured_snapshot tests/test_agent_tool_permissions.py::test_orders_submit_and_confirm_order_submits_and_confirms_after_matching_preflight tests/test_agent_tool_permissions.py::test_orders_submit_and_confirm_order_blocks_negative_cash_before_confirmation -q
```

Expected: PASS.

- [ ] **Step 2: Run lint for touched files**

Run:

```powershell
python -m ruff check lumibot/example_strategies/target_portfolio_to_execution_plan.py lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py tests/test_agent_tool_permissions.py
```

Expected: PASS. If `tests/test_agent_tool_permissions.py` was not modified, still include it because this feature relies on order-tool boundaries.

- [ ] **Step 3: Commit lint/test-only fixes if required**

If the previous steps required code formatting or import cleanup:

```powershell
git add lumibot/example_strategies/target_portfolio_to_execution_plan.py lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py tests/test_agent_tool_permissions.py
git commit -m "fix: clean daily sizing buffer implementation"
```

If no files changed, do not create a commit for this task.

---

## Task 7: Run One-Day Backtest Validation

**Files:**

- No source changes expected.
- Generated artifacts may appear under `artifacts/`.

- [ ] **Step 1: Confirm API key environment is available**

Use the existing project convention for loading `project_notes/API.txt` if the benchmark runner does not already load it. Do not print API key values.

Run:

```powershell
Test-Path project_notes/API.txt
```

Expected: `True`.

- [ ] **Step 2: Run one-day mock quadrant benchmark**

Run the current benchmark runner for the mock quadrant strategy on the bug-reproduction date:

```powershell
python scripts/run_ai_trading_team_examples_benchmark.py --env-file project_notes/API.txt --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-05
```

This command uses the runner's supported flags:

- `--strategy mock-growth-inflation-quadrant`
- `--start 2024-09-05`
- `--end 2024-09-05`
- `--env-file project_notes/API.txt`

Expected:

- benchmark completes;
- `portfolio_decision_agent` calls `target_portfolio_to_execution_plan`;
- `execution_agent` uses `orders_preflight_check` and `orders_submit_and_confirm_order`;
- no `NEGATIVE_CASH_NOT_ALLOWED` blocker appears for the 2024-09-05 initial all-cash plan.

- [ ] **Step 3: Inspect trace for sizing diagnostics**

Find the newest benchmark artifact:

```powershell
Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1 FullName
```

Search it:

```powershell
rg -n "target_portfolio_to_execution_plan|sizing_price|previous_completed_daily_close|buy_sizing_buffer_pct|effective_buy_target_value|NEGATIVE_CASH_NOT_ALLOWED" artifacts\ai_trading_team_example_benchmarks -S
```

Expected:

- `sizing_price` appears in the planner tool result;
- `sizing_price_source` is `previous_completed_daily_close` when previous close is available;
- `buy_sizing_buffer_pct` is `0.02`;
- `NEGATIVE_CASH_NOT_ALLOWED` does not appear in the newest one-day run.

- [ ] **Step 4: Inspect trades and cash**

Search newest run trade files:

```powershell
Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Recurse -Filter trades.csv | Sort-Object LastWriteTime -Descending | Select-Object -First 3 FullName
```

Open the newest `trades.csv` and verify:

- market/day fills still occur;
- actual fill prices may exceed planner sizing prices;
- final cash is not negative in the generated account curve or performance artifacts.

- [ ] **Step 5: Save a short validation note**

Create or append:

`project_notes/daily-buy-sizing-buffer-validation.md`

Add:

```markdown
# Daily Buy Sizing Buffer Validation

## Run

- Date: 2024-09-05
- Strategy: mock-growth-inflation-quadrant
- Branch: feature/mock-growth-inflation-quadrant-skeleton

## Expected

- Planner uses previous completed daily close when available.
- Planner applies buy_sizing_buffer_pct = 0.02.
- Execution still uses market/day orders.
- Final cash is non-negative.

## Observed

- Planner sizing diagnostics:
- Orders:
- Final cash:
- Notes:
```

Fill the bullets with the actual artifact path, quantities, prices, and final cash observed in this run.

- [ ] **Step 6: Commit validation note**

```powershell
git add project_notes/daily-buy-sizing-buffer-validation.md
git commit -m "docs: record daily buy sizing buffer validation"
```

---

## Task 8: Final Verification and Handoff

**Files:**

- No source changes expected.

- [ ] **Step 1: Show final branch status**

Run:

```powershell
git status --short --branch
```

Expected: clean working tree on `feature/mock-growth-inflation-quadrant-skeleton`, except ignored runtime files.

- [ ] **Step 2: Show commit summary**

Run:

```powershell
git log --oneline -8
```

Expected: recent commits include test, implementation, prompt alignment, and validation note commits.

- [ ] **Step 3: Re-run the highest-value verification command**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py tests/test_agent_tool_permissions.py::test_orders_preflight_check_ready_buy_returns_structured_snapshot tests/test_agent_tool_permissions.py::test_orders_submit_and_confirm_order_submits_and_confirms_after_matching_preflight tests/test_agent_tool_permissions.py::test_orders_submit_and_confirm_order_blocks_negative_cash_before_confirmation -q
```

Expected: PASS.

- [ ] **Step 4: Prepare final implementation summary**

The final response should include:

- planner now prefers previous completed daily close for daily buy sizing;
- planner applies default 2% buy sizing buffer;
- buffer is not a hard execution cap;
- execution agent remains a pure executor;
- trace now shows sizing diagnostics;
- one-day backtest result path and whether final cash stayed non-negative.

Do not claim the feature is complete unless tests and the one-day backtest were run and their outputs confirmed.
