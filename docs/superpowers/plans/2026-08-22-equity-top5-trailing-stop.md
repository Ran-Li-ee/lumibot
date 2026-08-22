# Equity Top 5 Allocation And Daily Trailing Stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change the QQQ historical equity-only LLM strategy from one-stock all-in allocation to Top 5 equal-weight allocation, then add a deterministic daily trailing-stop exit check before weekly rebalance.

**Architecture:** Keep LLM responsibility limited to selecting stock symbols. Convert LLM-selected `selected_symbols` into deterministic target portfolio weights, then reuse the existing `target_portfolio_to_execution_plan()` and `execution_plan_execute` path. Add a separate deterministic trailing-stop planner and strategy-local stop state so daily stop checks do not call an LLM.

**Tech Stack:** Python, pytest, Lumibot strategy classes, existing agent runtime/tool system, existing benchmark runner, YahooDataBacktesting.

---

## File Structure

- Modify `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
  - Update equity agent prompts from one winner to exactly five selected symbols.
  - Update `validate_execution_plan_symbols()` so buy orders may target any selected symbol in `selected_symbols`.
- Modify `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`
  - Add Top 5 constants.
  - Convert equity report `selected_symbols` into five equal target weights.
  - Run deterministic trailing-stop check before scheduled weekly/monthly workflow.
  - Pass stop exit plans through the existing execution agent.
- Create `lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py`
  - Build no-op or sell-only execution plans from current positions and strategy-local trailing-stop state.
  - Return compact audit metadata for later UI/trace work.
- Modify `tests/test_ai_trading_team_equity_only_llm.py`
  - Replace one-stock target tests with Top 5 equal-weight tests.
  - Add prompt, validator, and workflow tests.
- Create `tests/test_equity_trailing_stop_to_execution_plan.py`
  - Unit-test stop planner hold/trigger behavior and audit output.
- Modify `scripts/run_ai_trading_team_examples_benchmark.py` only if validation reveals the smoke commands need a new runner argument. The first implementation should avoid runner changes.

---

## Task 1: Update Equity Report Contract Tests To Top 5

**Files:**
- Modify: `tests/test_ai_trading_team_equity_only_llm.py`
- Later implementation target: `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Replace the full-weight selected-symbol test with a Top 5 equal-weight test**

Replace `test_equity_only_target_portfolio_uses_selected_symbol_at_full_weight` with:

```python
def test_equity_only_target_portfolio_uses_five_selected_symbols_at_equal_weights():
    module = load_module()

    result = module.equity_only_target_portfolio(
        {
            "basket_id": "equity",
            "status": "active",
            "selected_symbols": ["orcl", "msft", "nvda", "aapl", "amzn"],
            "reason_brief": "Five strongest equity candidates.",
        },
        equity_universe=["AAPL", "AMZN", "MSFT", "NVDA", "ORCL", "TSLA"],
    )

    assert result == [
        {"basket_id": "equity", "symbol": "ORCL", "target_weight": 0.2},
        {"basket_id": "equity", "symbol": "MSFT", "target_weight": 0.2},
        {"basket_id": "equity", "symbol": "NVDA", "target_weight": 0.2},
        {"basket_id": "equity", "symbol": "AAPL", "target_weight": 0.2},
        {"basket_id": "equity", "symbol": "AMZN", "target_weight": 0.2},
    ]
```

- [ ] **Step 2: Update selected status synonym test**

Replace `test_equity_only_target_portfolio_accepts_selected_status_synonym` with:

```python
def test_equity_only_target_portfolio_accepts_selected_status_synonym_for_top5():
    module = load_module()

    result = module.equity_only_target_portfolio(
        {
            "basket_id": "equity",
            "status": "selected",
            "selected_symbols": ["spy", "orcl", "msft", "aapl", "nvda"],
            "reason_brief": "Five active selections.",
        },
        equity_universe=["SPY", "ORCL", "MSFT", "AAPL", "NVDA", "AMZN"],
    )

    assert result == [
        {"basket_id": "equity", "symbol": "SPY", "target_weight": 0.2},
        {"basket_id": "equity", "symbol": "ORCL", "target_weight": 0.2},
        {"basket_id": "equity", "symbol": "MSFT", "target_weight": 0.2},
        {"basket_id": "equity", "symbol": "AAPL", "target_weight": 0.2},
        {"basket_id": "equity", "symbol": "NVDA", "target_weight": 0.2},
    ]
```

- [ ] **Step 3: Add Top 5 rejection tests**

Add these tests after the existing inactive/non-equity validation tests:

```python
def test_equity_only_target_portfolio_rejects_missing_selected_symbols():
    module = load_module()

    with pytest.raises(ValueError, match="selected_symbols must contain exactly 5 symbols"):
        module.equity_only_target_portfolio(
            {"basket_id": "equity", "status": "active", "selected_symbol": "ORCL"},
            equity_universe=["ORCL", "MSFT", "NVDA", "AAPL", "AMZN"],
        )


def test_equity_only_target_portfolio_rejects_duplicate_selected_symbols():
    module = load_module()

    with pytest.raises(ValueError, match="selected_symbols must be unique"):
        module.equity_only_target_portfolio(
            {
                "basket_id": "equity",
                "status": "active",
                "selected_symbols": ["ORCL", "MSFT", "NVDA", "AAPL", "ORCL"],
            },
            equity_universe=["ORCL", "MSFT", "NVDA", "AAPL", "AMZN"],
        )


def test_equity_only_target_portfolio_rejects_wrong_selected_symbol_count():
    module = load_module()

    with pytest.raises(ValueError, match="selected_symbols must contain exactly 5 symbols"):
        module.equity_only_target_portfolio(
            {
                "basket_id": "equity",
                "status": "active",
                "selected_symbols": ["ORCL", "MSFT", "NVDA", "AAPL"],
            },
            equity_universe=["ORCL", "MSFT", "NVDA", "AAPL", "AMZN"],
        )
```

- [ ] **Step 4: Run tests and verify they fail for the current one-stock implementation**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_only_target_portfolio_uses_five_selected_symbols_at_equal_weights tests/test_ai_trading_team_equity_only_llm.py::test_equity_only_target_portfolio_rejects_missing_selected_symbols -q
```

Expected: FAIL. The current implementation requires `selected_symbol` and returns one target at weight `1.0`.

---

## Task 2: Implement Top 5 Target Portfolio Conversion

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`
- Test: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Add Top 5 constants**

In `ai_trading_team_equity_only_llm.py`, replace:

```python
EQUITY_ONLY_TARGET_WEIGHT = 1.0
```

with:

```python
EQUITY_ONLY_TARGET_WEIGHT = 1.0
EQUITY_ONLY_SELECTION_COUNT = 5
EQUITY_ONLY_EQUAL_WEIGHT = EQUITY_ONLY_TARGET_WEIGHT / EQUITY_ONLY_SELECTION_COUNT
```

- [ ] **Step 2: Add selected-symbol validation helper**

Add this helper below `_normalized_symbol_list()`:

```python
def _selected_equity_symbols(
    equity_report: dict[str, Any],
    *,
    equity_universe: list[str],
    expected_count: int = EQUITY_ONLY_SELECTION_COUNT,
) -> list[str]:
    raw_symbols = equity_report.get("selected_symbols")
    if not isinstance(raw_symbols, list):
        raise ValueError(f"selected_symbols must contain exactly {expected_count} symbols.")

    normalized_symbols = []
    for symbol in raw_symbols:
        if not isinstance(symbol, str):
            raise ValueError("selected_symbols must contain only strings.")
        clean = symbol.strip().upper()
        if not clean:
            raise ValueError("selected_symbols must contain only non-empty symbols.")
        normalized_symbols.append(clean)

    if len(normalized_symbols) != expected_count:
        raise ValueError(f"selected_symbols must contain exactly {expected_count} symbols.")
    if len(set(normalized_symbols)) != len(normalized_symbols):
        raise ValueError("selected_symbols must be unique.")

    allowed = _normalized_universe(equity_universe)
    outside = [symbol for symbol in normalized_symbols if symbol not in allowed]
    if outside:
        joined = ", ".join(outside)
        raise ValueError(f"selected equity symbols must be in equity universe: {joined}.")

    return normalized_symbols
```

- [ ] **Step 3: Update `equity_only_target_portfolio()`**

Replace the single `selected_symbol` block with:

```python
    selected_symbols = _selected_equity_symbols(equity_report, equity_universe=equity_universe)

    return [
        {
            "basket_id": EQUITY_BASKET_ID,
            "symbol": symbol,
            "target_weight": EQUITY_ONLY_EQUAL_WEIGHT,
        }
        for symbol in selected_symbols
    ]
```

- [ ] **Step 4: Update symbol-outside-universe test message**

In `test_equity_only_target_portfolio_rejects_symbol_outside_universe`, change the report to:

```python
{
    "basket_id": "equity",
    "status": "active",
    "selected_symbols": ["GLD", "ORCL", "MSFT", "NVDA", "AAPL"],
}
```

and change the match to:

```python
match="selected equity symbols must be in equity universe: GLD"
```

- [ ] **Step 5: Run Top 5 target portfolio tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -k "target_portfolio" -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add lumibot/example_strategies/ai_trading_team_equity_only_llm.py tests/test_ai_trading_team_equity_only_llm.py
git commit -m "feat: convert equity selection to top five targets"
```

---

## Task 3: Update Prompts And Execution Symbol Validation

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
- Modify: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Update fixed-universe equity system prompt**

In `equity_basket_agent_system_prompt()`, replace the returned text with:

```python
    return (
        f"Equity-only selection role: choose exactly five stocks from the assigned basket_symbols ({symbols}). "
        "The downstream deterministic planner gives the five selected stocks equal target weights. "
        "You cannot place orders or size trades. Use market_load_history_tables_summary first for multi-symbol "
        "comparison. Treat rankings as separate evidence views; do not invent sector, style, safety, or "
        "cyclicality labels. Select the five strongest names across the separate ranking views, not merely "
        "the first five symbols from one ranking. If the leading group is clear across relevant rankings, "
        "select it without news. Use alpaca_news only when leading candidates are close, conflicting, or "
        "uncertain; when used, request news only for leading candidates. If news is unavailable, continue "
        "with rank-only evidence. Return strict JSON only. Do not place orders."
    )
```

- [ ] **Step 2: Update QQQ historical equity system prompt**

In `qqq_historical_equity_basket_agent_system_prompt()`, replace the returned text with:

```python
    return (
        f"Equity-only selection role: choose exactly five stocks from the QQQ historical constituent universe "
        f"provided in basket_symbols ({symbols}). "
        "The provided basket_symbols represent the QQQ historical constituent universe available for the "
        "current backtest date. The downstream deterministic planner gives the five selected stocks equal "
        "target weights. You cannot place orders or size trades. Use market_load_history_tables_summary first "
        "for multi-symbol comparison. Treat rankings as separate evidence views; do not invent sector, style, "
        "safety, or cyclicality labels. Do not assume QQQ membership itself makes a stock safe or best; select "
        "from current rank evidence. Do not choose based on index weight alone. Select the five strongest names "
        "across the separate ranking views, not merely the first five symbols from one ranking. If the leading "
        "group is clear across relevant rankings, select it without news. Use alpaca_news only when leading "
        "candidates are close, conflicting, or uncertain; when used, request news only for leading candidates. "
        "If news is unavailable, continue with rank-only evidence. Use only symbols in the provided "
        "basket_symbols and do not add symbols outside the provided universe. Return strict JSON only. "
        "Do not place orders."
    )
```

- [ ] **Step 3: Update task prompt JSON contract**

In `equity_basket_agent_task_prompt()`, replace the returned text with:

```python
    return (
        "Review only the provided basket_symbols. First call market_load_history_tables_summary with "
        "symbols=basket_symbols, length=252, timestep='day', and top_n=10. Compare separate ranking views. "
        "Select exactly five symbols that are strongest across the separate ranking views; do not simply copy "
        "the first five names from one list if other ranking evidence conflicts. If the leading group is clear "
        "across relevant rankings, select it without news. If leading candidates are close, conflicting, or "
        "uncertain, call alpaca_news for those leading candidates only. If alpaca_news is unavailable or errors, "
        "continue with rank-only evidence. Return exactly one strict JSON object with basket_id, target_weight, "
        "status, candidate_symbols, selected_symbols, and reason_brief. Use status='active'. target_weight must "
        "be 1.0 for the equity basket as a whole; do not assign per-symbol weights. candidate_symbols must copy "
        "the assigned basket_symbols exactly; do not replace it with a shortlist. selected_symbols must contain "
        "exactly five unique symbols from basket_symbols."
    )
```

- [ ] **Step 4: Update `validate_execution_plan_symbols()`**

Replace the single-symbol logic with:

```python
    selected_symbols = equity_report.get("selected_symbols")
    if status not in ACTIVE_SELECTION_STATUSES or not isinstance(selected_symbols, list):
        raise ValueError("equity report must select active symbols before buying.")

    expected_symbols = {
        str(symbol).strip().upper()
        for symbol in selected_symbols
        if isinstance(symbol, str) and str(symbol).strip()
    }
    if not expected_symbols:
        raise ValueError("equity report must select active symbols before buying.")

    for order in execution_plan.get("orders", []):
        order = _require_dict(order, "order")
        if str(order.get("side") or "").strip().lower() != "buy":
            continue
        symbol = str(order.get("symbol") or "").strip().upper()
        if symbol not in expected_symbols:
            raise ValueError(f"execution_plan buy symbol {symbol} does not match selected equity symbols.")
```

- [ ] **Step 5: Update prompt tests**

In `test_equity_agent_system_prompt_is_equity_only_rank_first_and_conditional_news`, replace required phrase `"choose exactly one stock"` with:

```python
"choose exactly five stocks",
"equal target weights",
```

and add `"selected_symbols"` to the task prompt assertions in `test_equity_agent_task_prompt_teaches_summary_first_top_n_and_strict_json`.

- [ ] **Step 6: Update execution validation test**

Replace `test_validate_execution_plan_symbols_accepts_selected_status_synonym` with:

```python
def test_validate_execution_plan_symbols_accepts_any_top5_selected_symbol():
    module = load_module()

    module.validate_execution_plan_symbols(
        {
            "schema_version": 1,
            "intent": "rebalance",
            "orders": [
                {
                    "sequence": 1,
                    "action": "submit_order",
                    "symbol": "SPY",
                    "asset_type": "stock",
                    "side": "buy",
                    "quantity": 10,
                    "quantity_mode": "shares",
                    "order_type": "market",
                    "time_in_force": "day",
                },
                {
                    "sequence": 2,
                    "action": "submit_order",
                    "symbol": "MSFT",
                    "asset_type": "stock",
                    "side": "buy",
                    "quantity": 5,
                    "quantity_mode": "shares",
                    "order_type": "market",
                    "time_in_force": "day",
                },
            ],
        },
        {
            "basket_id": "equity",
            "status": "selected",
            "selected_symbols": ["SPY", "ORCL", "MSFT", "AAPL", "NVDA"],
        },
    )
```

- [ ] **Step 7: Run prompt and validator tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -k "prompt or validate_execution_plan_symbols" -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```powershell
git add lumibot/example_strategies/ai_trading_team_equity_only_helpers.py tests/test_ai_trading_team_equity_only_llm.py
git commit -m "feat: require top five equity agent selections"
```

---

## Task 4: Update Equity Workflow Tests For Top 5 Planner Calls

**Files:**
- Modify: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Update test agent summaries to `selected_symbols`**

In every test summary that currently uses:

```python
"selected_symbol": "AAPL"
```

replace it with five selected symbols that exist in that test's resolved universe, for example:

```python
"selected_symbols": ["AAPL", "MSFT", "NVDA", "AMZN", "META"]
```

For tests with a small fake universe, update the fake universe to contain at least five symbols.

- [ ] **Step 2: Replace full-weight workflow test**

Replace `test_iteration_builds_full_weight_target_and_runs_execution` with:

```python
def test_iteration_builds_top5_equal_weight_targets_and_runs_execution(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamEquityOnlyLLMStrategy)
    strategy.parameters["basket_universes"] = {
        **strategy.parameters["basket_universes"],
        "equity": ["ORCL", "MSFT", "NVDA", "AAPL", "AMZN", "META"],
    }
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": strategy.parameters["basket_universes"]["equity"],
            "selected_symbols": ["ORCL", "MSFT", "NVDA", "AAPL", "AMZN"],
            "reason_brief": "Five strongest setup names.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."
    planner_calls = []

    def fake_target_portfolio_to_execution_plan(strategy_arg, *, date, target_portfolio):
        planner_calls.append(
            {
                "strategy": strategy_arg,
                "date": date,
                "target_portfolio": target_portfolio,
            }
        )
        return {
            "schema_version": "1.0",
            "date": date,
            "target_portfolio": target_portfolio,
            "current_vs_target": [
                {"symbol": "ORCL", "planned_side": "buy", "planned_quantity": 10, "sizing_price": 100.0}
            ],
            "cash_projection": {
                "cash_before": 100000.0,
                "estimated_sell_proceeds": 0.0,
                "estimated_buy_cost": 1000.0,
                "cash_after_estimate": 99000.0,
                "buy_sizing_buffer_pct": 0.02,
                "negative_cash_allowed": False,
            },
            "execution_plan": {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [
                    {
                        "sequence": 1,
                        "action": "submit_order",
                        "symbol": "ORCL",
                        "asset_type": "stock",
                        "side": "buy",
                        "quantity": 10,
                        "quantity_mode": "shares",
                        "order_type": "market",
                        "time_in_force": "day",
                    }
                ],
            },
            "warnings": [],
        }

    monkeypatch.setattr(module, "target_portfolio_to_execution_plan", fake_target_portfolio_to_execution_plan)

    strategy.on_trading_iteration()

    assert planner_calls == [
        {
            "strategy": strategy,
            "date": "2024-09-05",
            "target_portfolio": [
                {"basket_id": "equity", "symbol": "ORCL", "target_weight": 0.2},
                {"basket_id": "equity", "symbol": "MSFT", "target_weight": 0.2},
                {"basket_id": "equity", "symbol": "NVDA", "target_weight": 0.2},
                {"basket_id": "equity", "symbol": "AAPL", "target_weight": 0.2},
                {"basket_id": "equity", "symbol": "AMZN", "target_weight": 0.2},
            ],
        }
    ]
    equity_agent = strategy.agents["equity_basket_agent"]
    assert len(equity_agent.calls) == 1
    assert equity_agent.calls[0]["context"]["target_weight"] == 1.0
    execution_agent = strategy.agents["execution_agent"]
    assert len(execution_agent.calls) == 1
    execution_plan = execution_agent.calls[0]["context"]["execution_plan"]
    assert execution_plan["orders"][0]["symbol"] == "ORCL"
```

- [ ] **Step 3: Run the equity-only test module**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

Run:

```powershell
git add tests/test_ai_trading_team_equity_only_llm.py
git commit -m "test: update equity workflow for top five targets"
```

---

## Task 5: Add Deterministic Trailing Stop Planner Tests

**Files:**
- Create: `tests/test_equity_trailing_stop_to_execution_plan.py`
- Later implementation target: `lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py`

- [ ] **Step 1: Create the test file**

Create `tests/test_equity_trailing_stop_to_execution_plan.py` with:

```python
from types import SimpleNamespace

import pandas as pd
import pytest


def load_module():
    import importlib

    return importlib.import_module("lumibot.example_strategies.equity_trailing_stop_to_execution_plan")


def make_position(symbol, quantity):
    return SimpleNamespace(asset=SimpleNamespace(symbol=symbol), quantity=quantity)


def make_strategy(*, positions, prices):
    strategy = SimpleNamespace()
    strategy.get_positions = lambda include_cash_positions=False: positions
    strategy.get_last_price = lambda symbol, **kwargs: prices[str(getattr(symbol, "symbol", symbol)).upper()]
    strategy.get_historical_prices = lambda symbol, length, timestep="day", **kwargs: SimpleNamespace(
        pandas_df=pd.DataFrame(
            [{"Date": "2024-09-05", "open": prices[str(symbol).upper()], "high": prices[str(symbol).upper()], "low": prices[str(symbol).upper()], "close": prices[str(symbol).upper()], "volume": 1000}]
        )
    )
    return strategy


def test_trailing_stop_returns_hold_when_no_position_breaches_threshold():
    module = load_module()
    strategy = make_strategy(
        positions=[make_position("NVDA", 10), make_position("AAPL", 5)],
        prices={"NVDA": 125.0, "AAPL": 200.0},
    )
    state = {
        "NVDA": {"entry_date": "2024-09-02", "peak_close": 150.0},
        "AAPL": {"entry_date": "2024-09-02", "peak_close": 210.0},
    }

    result = module.trailing_stop_to_execution_plan(
        strategy,
        date="2024-09-05",
        trailing_stop_pct=0.20,
        position_state=state,
    )

    assert result["execution_plan"] == {"schema_version": 1, "intent": "hold", "orders": []}
    assert result["stop_checks"][0]["symbol"] == "AAPL"
    assert result["stop_checks"][1]["symbol"] == "NVDA"
    assert all(check["triggered"] is False for check in result["stop_checks"])


def test_trailing_stop_generates_full_position_sell_when_threshold_breached():
    module = load_module()
    strategy = make_strategy(positions=[make_position("NVDA", 10)], prices={"NVDA": 118.0})
    state = {"NVDA": {"entry_date": "2024-09-02", "peak_close": 150.0}}

    result = module.trailing_stop_to_execution_plan(
        strategy,
        date="2024-09-05",
        trailing_stop_pct=0.20,
        position_state=state,
    )

    assert result["execution_plan"] == {
        "schema_version": 1,
        "intent": "rebalance",
        "orders": [
            {
                "sequence": 1,
                "action": "submit_order",
                "symbol": "NVDA",
                "side": "sell",
                "quantity_mode": "shares",
                "quantity": 10,
                "asset_type": "stock",
                "order_type": "market",
                "time_in_force": "day",
            }
        ],
    }
    assert result["stop_checks"] == [
        {
            "symbol": "NVDA",
            "quantity": 10.0,
            "holding_start_date": "2024-09-02",
            "previous_peak_close": 150.0,
            "peak_close": 150.0,
            "current_check_price": 118.0,
            "trailing_stop_pct": 0.2,
            "stop_price": 120.0,
            "triggered": True,
            "price_source": "daily_close",
        }
    ]


def test_trailing_stop_updates_peak_when_current_close_sets_new_high():
    module = load_module()
    strategy = make_strategy(positions=[make_position("NVDA", 10)], prices={"NVDA": 155.0})
    state = {"NVDA": {"entry_date": "2024-09-02", "peak_close": 150.0}}

    result = module.trailing_stop_to_execution_plan(
        strategy,
        date="2024-09-05",
        trailing_stop_pct=0.20,
        position_state=state,
    )

    assert result["updated_position_state"]["NVDA"]["entry_date"] == "2024-09-02"
    assert result["updated_position_state"]["NVDA"]["peak_close"] == 155.0
    assert result["execution_plan"]["intent"] == "hold"


def test_trailing_stop_rejects_negative_or_zero_trailing_stop_pct():
    module = load_module()
    strategy = make_strategy(positions=[], prices={})

    with pytest.raises(ValueError, match="trailing_stop_pct must be between 0 and 1"):
        module.trailing_stop_to_execution_plan(
            strategy,
            date="2024-09-05",
            trailing_stop_pct=0,
            position_state={},
        )
```

- [ ] **Step 2: Run tests and verify they fail because the module is missing**

Run:

```powershell
python -m pytest tests/test_equity_trailing_stop_to_execution_plan.py -q
```

Expected: FAIL with `ModuleNotFoundError`.

---

## Task 6: Implement Deterministic Trailing Stop Planner

**Files:**
- Create: `lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py`
- Test: `tests/test_equity_trailing_stop_to_execution_plan.py`

- [ ] **Step 1: Create planner module**

Create `lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py` with:

```python
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from lumibot.example_strategies.target_portfolio_to_execution_plan import (
    _current_positions_by_symbol,
    _float,
    _order,
)


DEFAULT_TRAILING_STOP_PCT = 0.20


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{label} must be numeric.") from exc
    if not result.is_finite():
        raise ValueError(f"{label} must be finite.")
    return result


def _price_from_daily_close(strategy: Any, symbol: str) -> tuple[Decimal, str]:
    get_historical_prices = getattr(strategy, "get_historical_prices", None)
    if callable(get_historical_prices):
        try:
            bars = get_historical_prices(symbol, 1, timestep="day")
        except TypeError:
            bars = get_historical_prices(symbol, 1, "day")
        frame = getattr(bars, "pandas_df", None)
        if frame is None:
            frame = getattr(bars, "df", None)
        if frame is not None and not frame.empty and "close" in frame.columns:
            return _positive_price(frame["close"].iloc[-1], f"daily close for {symbol}"), "daily_close"

    get_last_price = getattr(strategy, "get_last_price", None)
    if not callable(get_last_price):
        raise ValueError(f"missing trailing stop check price for {symbol}")
    return _positive_price(get_last_price(symbol), f"last price for {symbol}"), "last_price_fallback"


def _positive_price(value: Any, label: str) -> Decimal:
    price = _decimal(value, label)
    if price <= 0:
        raise ValueError(f"{label} must be positive.")
    return price


def _normalized_state(position_state: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    for raw_symbol, raw_payload in dict(position_state or {}).items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol or not isinstance(raw_payload, dict):
            continue
        entry_date = str(raw_payload.get("entry_date") or "").strip()
        peak_close = raw_payload.get("peak_close")
        if not entry_date or peak_close is None:
            continue
        state[symbol] = {"entry_date": entry_date, "peak_close": _float(_positive_price(peak_close, "peak_close"))}
    return state


def trailing_stop_to_execution_plan(
    strategy: Any,
    *,
    date: str,
    trailing_stop_pct: float = DEFAULT_TRAILING_STOP_PCT,
    position_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pct = _decimal(trailing_stop_pct, "trailing_stop_pct")
    if pct <= 0 or pct >= 1:
        raise ValueError("trailing_stop_pct must be between 0 and 1.")

    current_positions = _current_positions_by_symbol(strategy)
    state = _normalized_state(position_state)
    updated_state: dict[str, dict[str, Any]] = {}
    stop_checks: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []

    for symbol in sorted(current_positions):
        quantity = current_positions[symbol]
        check_price, price_source = _price_from_daily_close(strategy, symbol)
        previous = state.get(symbol) or {"entry_date": date, "peak_close": _float(check_price)}
        previous_peak = _positive_price(previous["peak_close"], f"previous peak close for {symbol}")
        peak_close = max(previous_peak, check_price)
        stop_price = peak_close * (Decimal("1") - pct)
        triggered = check_price <= stop_price
        planned_quantity = int(quantity)

        stop_checks.append(
            {
                "symbol": symbol,
                "quantity": _float(quantity),
                "holding_start_date": previous["entry_date"],
                "previous_peak_close": _float(previous_peak),
                "peak_close": _float(peak_close),
                "current_check_price": _float(check_price),
                "trailing_stop_pct": _float(pct),
                "stop_price": _float(stop_price),
                "triggered": triggered,
                "price_source": price_source,
            }
        )

        if triggered and planned_quantity > 0:
            orders.append(
                _order(
                    sequence=len(orders) + 1,
                    symbol=symbol,
                    side="sell",
                    quantity=planned_quantity,
                )
            )
            continue

        updated_state[symbol] = {
            "entry_date": previous["entry_date"],
            "peak_close": _float(peak_close),
            "last_check_date": date,
            "last_check_price": _float(check_price),
        }

    return {
        "schema_version": "1.0",
        "date": date,
        "trailing_stop_pct": _float(pct),
        "stop_checks": stop_checks,
        "updated_position_state": updated_state,
        "execution_plan": {
            "schema_version": 1,
            "intent": "rebalance" if orders else "hold",
            "orders": orders,
        },
        "warnings": [],
    }
```

- [ ] **Step 2: Run trailing stop tests**

Run:

```powershell
python -m pytest tests/test_equity_trailing_stop_to_execution_plan.py -q
```

Expected: PASS.

- [ ] **Step 3: Commit**

Run:

```powershell
git add lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py tests/test_equity_trailing_stop_to_execution_plan.py
git commit -m "feat: add deterministic equity trailing stop planner"
```

---

## Task 7: Integrate Daily Stop Check Before Scheduled Workflow

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`
- Modify: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Add failing integration test for stop-priority behavior**

Add this test to `tests/test_ai_trading_team_equity_only_llm.py`:

```python
def test_trailing_stop_executes_and_skips_weekly_equity_agent(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy)
    strategy.get_datetime = lambda: datetime(2024, 9, 9, 9, 30)
    strategy.agents.summaries["execution_agent"] = "Stop sale executed."

    def fake_trailing_stop_to_execution_plan(strategy_arg, *, date, trailing_stop_pct, position_state):
        return {
            "schema_version": "1.0",
            "date": date,
            "trailing_stop_pct": trailing_stop_pct,
            "stop_checks": [
                {
                    "symbol": "NVDA",
                    "quantity": 10.0,
                    "holding_start_date": "2024-09-03",
                    "previous_peak_close": 150.0,
                    "peak_close": 150.0,
                    "current_check_price": 118.0,
                    "trailing_stop_pct": trailing_stop_pct,
                    "stop_price": 120.0,
                    "triggered": True,
                    "price_source": "daily_close",
                }
            ],
            "updated_position_state": {},
            "execution_plan": {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [
                    {
                        "sequence": 1,
                        "action": "submit_order",
                        "symbol": "NVDA",
                        "side": "sell",
                        "quantity_mode": "shares",
                        "quantity": 10,
                        "asset_type": "stock",
                        "order_type": "market",
                        "time_in_force": "day",
                    }
                ],
            },
            "warnings": [],
        }

    monkeypatch.setattr(module, "trailing_stop_to_execution_plan", fake_trailing_stop_to_execution_plan)

    strategy.on_trading_iteration()

    assert strategy.agents["equity_basket_agent"].calls == []
    assert len(strategy.agents["execution_agent"].calls) == 1
    execution_context = strategy.agents["execution_agent"].calls[0]["context"]
    assert execution_context["execution_plan"]["orders"][0]["symbol"] == "NVDA"
    assert strategy._last_trailing_stop_result["stop_checks"][0]["triggered"] is True
```

- [ ] **Step 2: Import planner in strategy**

At the top of `ai_trading_team_equity_only_llm.py`, add:

```python
from lumibot.example_strategies.equity_trailing_stop_to_execution_plan import (
    DEFAULT_TRAILING_STOP_PCT,
    trailing_stop_to_execution_plan,
)
```

- [ ] **Step 3: Initialize stop state**

In `_initialize_equity_only_workflow_state()`, add:

```python
        self._equity_trailing_stop_pct = float(
            self.parameters.get("equity_trailing_stop_pct", DEFAULT_TRAILING_STOP_PCT)
        )
        self._equity_trailing_stop_position_state: dict[str, dict[str, Any]] = {}
        self._trailing_stop_events: list[dict[str, Any]] = []
        self._last_trailing_stop_result = None
```

- [ ] **Step 4: Add stop execution helper**

Add this method to `AITradingTeamEquityOnlyLLMStrategy` before `_run_equity_only_workflow()`:

```python
    def _execute_plan_with_execution_agent(
        self,
        *,
        current_date: str,
        execution_plan: dict[str, Any],
        reason: str,
    ) -> None:
        self.agents["execution_agent"].run(
            task_prompt=(
                "Execute the provided execution_plan by calling execution_plan_execute exactly once with the "
                "complete execution_plan. Use the returned concise execution summary to write the final result. "
                "Do not infer missing order details beyond the tool response. Do not call per-order tools. "
                f"This execution_plan reason is {reason}."
            ),
            context={
                "date": current_date,
                "execution_reason": reason,
                "execution_plan": execution_plan_execute_payload(execution_plan),
            },
        )

    def _run_daily_trailing_stop_check(self, current_date: str) -> bool:
        result = trailing_stop_to_execution_plan(
            self,
            date=current_date,
            trailing_stop_pct=self._equity_trailing_stop_pct,
            position_state=self._equity_trailing_stop_position_state,
        )
        self._last_trailing_stop_result = result
        self._trailing_stop_events.append(result)
        self._equity_trailing_stop_position_state = dict(result.get("updated_position_state") or {})

        execution_plan = normalize_execution_plan(result.get("execution_plan"))
        if execution_plan["intent"] == "hold" or not execution_plan["orders"]:
            return False

        self._last_target_portfolio_planner_result = {
            "execution_plan": execution_plan,
            "trailing_stop_result": result,
        }
        self._execute_plan_with_execution_agent(
            current_date=current_date,
            execution_plan=execution_plan,
            reason="trailing_stop",
        )
        return True
```

- [ ] **Step 5: Replace direct execution-agent call in weekly workflow**

In `_run_equity_only_workflow()`, replace the final `self.agents["execution_agent"].run(...)` block with:

```python
        self._execute_plan_with_execution_agent(
            current_date=current_date,
            execution_plan=execution_plan,
            reason="scheduled_rebalance",
        )
```

- [ ] **Step 6: Run stop check before cadence decision**

In `on_trading_iteration()`, replace:

```python
        cadence_event = self._scheduled_workflow_decision(current_date_obj)
```

with:

```python
        if self._run_daily_trailing_stop_check(current_date):
            return
        cadence_event = self._scheduled_workflow_decision(current_date_obj)
```

- [ ] **Step 7: Run integration test**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_trailing_stop_executes_and_skips_weekly_equity_agent -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```powershell
git add lumibot/example_strategies/ai_trading_team_equity_only_llm.py tests/test_ai_trading_team_equity_only_llm.py
git commit -m "feat: run daily equity trailing stop before rebalance"
```

---

## Task 8: Ensure Scheduled Workflow Still Runs When No Stop Triggers

**Files:**
- Modify: `tests/test_ai_trading_team_equity_only_llm.py`
- Modify if needed: `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Add no-stop weekly workflow test**

Add:

```python
def test_no_trailing_stop_allows_weekly_equity_workflow(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy)
    strategy.get_datetime = lambda: datetime(2024, 9, 9, 9, 30)
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": ["AAPL", "MSFT", "NVDA", "AMZN", "META"],
            "selected_symbols": ["AAPL", "MSFT", "NVDA", "AMZN", "META"],
            "reason_brief": "Five strongest names.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."

    monkeypatch.setattr(
        module,
        "trailing_stop_to_execution_plan",
        lambda strategy_arg, *, date, trailing_stop_pct, position_state: {
            "schema_version": "1.0",
            "date": date,
            "trailing_stop_pct": trailing_stop_pct,
            "stop_checks": [],
            "updated_position_state": {},
            "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []},
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        module,
        "resolve_qqq_snapshot",
        lambda as_of_date, *, mode="strict", data_dir=None: qqq_resolution(
            as_of_date=as_of_date,
            symbols=("AAPL", "MSFT", "NVDA", "AMZN", "META"),
        ),
    )
    monkeypatch.setattr(
        module,
        "target_portfolio_to_execution_plan",
        lambda strategy_arg, *, date, target_portfolio: {
            "schema_version": "1.0",
            "date": date,
            "target_portfolio": target_portfolio,
            "current_vs_target": [],
            "cash_projection": {
                "cash_before": 100000.0,
                "estimated_sell_proceeds": 0.0,
                "estimated_buy_cost": 0.0,
                "cash_after_estimate": 100000.0,
                "buy_sizing_buffer_pct": 0.02,
                "negative_cash_allowed": False,
            },
            "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []},
            "warnings": [],
        },
    )

    strategy.on_trading_iteration()

    assert len(strategy.agents["equity_basket_agent"].calls) == 1
    assert strategy._last_trailing_stop_result["execution_plan"]["intent"] == "hold"
```

- [ ] **Step 2: Run the no-stop test**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_no_trailing_stop_allows_weekly_equity_workflow -q
```

Expected: PASS.

- [ ] **Step 3: Run all equity-only and trailing-stop tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py tests/test_equity_trailing_stop_to_execution_plan.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

Run:

```powershell
git add tests/test_ai_trading_team_equity_only_llm.py lumibot/example_strategies/ai_trading_team_equity_only_llm.py
git commit -m "test: preserve weekly workflow after stop checks"
```

---

## Task 9: Focused Lint And Broader Regression Tests

**Files:**
- Verify only.

- [ ] **Step 1: Run focused ruff**

Run:

```powershell
python -m ruff check lumibot/example_strategies/ai_trading_team_equity_only_llm.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py tests/test_ai_trading_team_equity_only_llm.py tests/test_equity_trailing_stop_to_execution_plan.py
```

Expected: PASS. If ruff reports import ordering or line-length issues, fix the exact files and rerun this command.

- [ ] **Step 2: Run adjacent planner tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py tests/test_equity_trailing_stop_to_execution_plan.py tests/test_agent_tool_permissions.py -k "execution_plan_execute or equity_only or trailing_stop" -q
```

Expected: PASS.

- [ ] **Step 3: Commit lint or regression fixes if any were needed**

If Step 1 or Step 2 required fixes, run:

```powershell
git add lumibot/example_strategies tests
git commit -m "fix: stabilize equity top5 stop tests"
```

If no files changed, do not create an empty commit.

---

## Task 10: Short Smoke Backtest

**Files:**
- Verify generated artifacts under the newest `qqq-historical-equity-only-llm` directory inside `artifacts/ai_trading_team_example_benchmarks`.

- [ ] **Step 1: Run a one-month smoke backtest**

Use OpenAI GPT-5.6 Luna if `project_notes/API.txt` is configured and `.env.local` is already updated. Otherwise set `AI_TRADING_TEAM_MODEL` in the same shell before running.

Run:

```powershell
$env:AI_TRADING_TEAM_MODEL="openai/gpt-5.6-luna"
python scripts/run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2024-09-05 --end 2024-10-07 --run-frequency weekly --weekly-run-weekday MON --max-workers 1 --agent-run-timeout-seconds 1800 --max-run-attempts 3 --env-file project_notes/API.txt
```

Expected:
- Command exits successfully.
- Output JSON shows `"strategy": "qqq-historical-equity-only-llm"` and `"status": "passed"`.
- A new artifact directory is printed.

- [ ] **Step 2: Inspect generated target portfolios**

Run this command to inspect the newest QQQ historical equity-only artifact:

```powershell
python - <<'PY'
from pathlib import Path
import json

benchmark_root = Path("artifacts/ai_trading_team_example_benchmarks")
roots = sorted(
    benchmark_root.glob("*/qqq-historical-equity-only-llm"),
    key=lambda path: path.stat().st_mtime,
    reverse=True,
)
if not roots:
    raise SystemExit("No qqq-historical-equity-only-llm artifacts found.")
root = roots[0]
print(root.resolve())
result = json.loads((root / "result.json").read_text(encoding="utf-8"))
print(result["status"])
for path in sorted(root.glob("agent_runtime/traces/*/equity_basket_agent*.json"))[:3]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(path.name)
    print(payload.get("summary", "")[:500])
PY
```

Expected:
- Status prints `passed`.
- Equity agent summaries include `selected_symbols`.
- The selected symbol list contains five symbols.

- [ ] **Step 3: Open replay UI to visually inspect one run**

Run:

```powershell
python scripts/agent_trace_ui.py
```

Open `http://127.0.0.1:8765`, select the new `qqq-historical-equity-only-llm` run, and verify:
- Workflow still shows `equity_basket_agent -> execution_agent`.
- Equity agent output contains five selected symbols.
- Execution plan includes multiple orders when the portfolio is not already at target.

- [ ] **Step 4: Record smoke result**

Create or update a note:

```powershell
New-Item -ItemType Directory -Force docs/superpowers/notes | Out-Null
```

Then generate `docs/superpowers/notes/2026-08-22-equity-top5-trailing-stop-smoke.md` from the newest artifact:

```powershell
python - <<'PY'
from pathlib import Path
import json

benchmark_root = Path("artifacts/ai_trading_team_example_benchmarks")
roots = sorted(
    benchmark_root.glob("*/qqq-historical-equity-only-llm"),
    key=lambda path: path.stat().st_mtime,
    reverse=True,
)
if not roots:
    raise SystemExit("No qqq-historical-equity-only-llm artifacts found.")
root = roots[0]
result = json.loads((root / "result.json").read_text(encoding="utf-8"))
text_blobs = []
for path in root.rglob("*.json"):
    try:
        text_blobs.append(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        continue
combined = "\n".join(text_blobs).lower()
negative_cash_count = combined.count("negative_cash")
stop_trigger_count = combined.count('"triggered": true')
summary = [
    "# Equity Top 5 Trailing Stop Smoke",
    "",
    "## Command",
    "",
    "`python scripts/run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2024-09-05 --end 2024-10-07 --run-frequency weekly --weekly-run-weekday MON --max-workers 1 --agent-run-timeout-seconds 1800 --max-run-attempts 3 --env-file project_notes/API.txt`",
    "",
    "## Result",
    "",
    f"- Status: {result.get('status')}",
    f"- Artifact: `{root.resolve()}`",
    "- Equity agent selected_symbols count: verify in replay UI and trace summaries.",
    f"- Negative cash markers: {negative_cash_count}",
    f"- Stop-triggered exits: {stop_trigger_count}",
    "- Notes: smoke run completed; use replay UI for qualitative inspection.",
    "",
]
note = Path("docs/superpowers/notes/2026-08-22-equity-top5-trailing-stop-smoke.md")
note.write_text("\n".join(summary), encoding="utf-8")
print(note.resolve())
PY
```

- [ ] **Step 5: Commit smoke note**

Run:

```powershell
git add docs/superpowers/notes/2026-08-22-equity-top5-trailing-stop-smoke.md
git commit -m "docs: record equity top5 stop smoke"
```

---

## Task 11: Five-Year Comparison Validation

**Files:**
- Verify generated artifacts.
- Create/modify: `docs/superpowers/notes/2026-08-22-equity-top5-trailing-stop-5y-comparison.md`

- [ ] **Step 1: Run the five-year comparison window**

Run:

```powershell
$env:AI_TRADING_TEAM_MODEL="openai/gpt-5.6-luna"
python scripts/run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2021-08-16 --end 2026-08-14 --run-frequency weekly --weekly-run-weekday MON --max-workers 1 --agent-run-timeout-seconds 1800 --max-run-attempts 3 --env-file project_notes/API.txt
```

Expected: The run may take a long time, but should finish with `"status": "passed"`.

- [ ] **Step 2: Compare with the existing baseline note**

Use the previous baseline note:

```text
docs/superpowers/notes/2026-08-16-equity-only-llm-weekly-5y-baseline.md
```

Run this command, paste the old baseline artifact path when prompted, and let the script use the newest candidate artifact:

```powershell
$oldBaselineArtifact = Read-Host "Paste previous single-stock baseline artifact directory"
$newArtifact = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory |
  Sort-Object LastWriteTime -Descending |
  ForEach-Object { Join-Path $_.FullName "qqq-historical-equity-only-llm" } |
  Where-Object { Test-Path $_ } |
  Select-Object -First 1
python scripts\compare_strategy_artifacts.py --baseline-artifact "$oldBaselineArtifact" --candidate-artifact "$newArtifact"
```

Expected: The comparison prints total return, CAGR, max drawdown, volatility, Sharpe, and trade count.

- [ ] **Step 3: Count stop-triggered exits from traces or logs**

Run:

```powershell
$newArtifact = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory |
  Sort-Object LastWriteTime -Descending |
  ForEach-Object { Join-Path $_.FullName "qqq-historical-equity-only-llm" } |
  Where-Object { Test-Path $_ } |
  Select-Object -First 1
$env:NEW_EQUITY_TOP5_ARTIFACT = "$newArtifact"
python - <<'PY'
from pathlib import Path
import json
import os

root = Path(os.environ["NEW_EQUITY_TOP5_ARTIFACT"])
count = 0
for path in root.rglob("*.json"):
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    if "trailing_stop" in text and '"triggered": true' in text.lower():
        count += text.lower().count('"triggered": true')
print(count)
PY
```

Expected: Prints the number of triggered stop checks discoverable from artifacts. Zero is acceptable if the market window never breached the threshold; crashes or missing artifacts are not acceptable.

- [ ] **Step 4: Write comparison note**

Generate `docs/superpowers/notes/2026-08-22-equity-top5-trailing-stop-5y-comparison.md`:

```powershell
$oldBaselineArtifact = Read-Host "Paste previous single-stock baseline artifact directory"
$newArtifact = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory |
  Sort-Object LastWriteTime -Descending |
  ForEach-Object { Join-Path $_.FullName "qqq-historical-equity-only-llm" } |
  Where-Object { Test-Path $_ } |
  Select-Object -First 1
$env:OLD_EQUITY_BASELINE_ARTIFACT = "$oldBaselineArtifact"
$env:NEW_EQUITY_TOP5_ARTIFACT = "$newArtifact"
python - <<'PY'
from pathlib import Path
import os

baseline = Path(os.environ["OLD_EQUITY_BASELINE_ARTIFACT"])
candidate = Path(os.environ["NEW_EQUITY_TOP5_ARTIFACT"])
note = Path("docs/superpowers/notes/2026-08-22-equity-top5-trailing-stop-5y-comparison.md")
lines = [
    "# Equity Top 5 Trailing Stop Five-Year Comparison",
    "",
    "## Window",
    "",
    "- Start: 2021-08-16",
    "- End: 2026-08-14",
    "- Cadence: weekly Monday",
    "- Model: openai/gpt-5.6-luna",
    "",
    "## Baseline",
    "",
    "- Previous run: QQQ historical equity-only LLM, single selected symbol at 100% target weight.",
    "- Baseline note: `docs/superpowers/notes/2026-08-16-equity-only-llm-weekly-5y-baseline.md`",
    f"- Baseline artifact: `{baseline.resolve()}`",
    "",
    "## Candidate",
    "",
    "- New run: QQQ historical equity-only LLM, Top 5 equal weight, daily deterministic trailing stop.",
    f"- Candidate artifact: `{candidate.resolve()}`",
    "",
    "## Metrics",
    "",
    "Paste the output from `scripts/compare_strategy_artifacts.py` below this paragraph after reviewing it.",
    "",
    "## Assessment",
    "",
    "- Concentration check: inspect target portfolios and trade logs for five-symbol allocation.",
    "- Drawdown check: compare max drawdown against the baseline output.",
    "- Return tradeoff: compare total return and CAGR against the baseline output.",
    "- Stop behavior: count stop-triggered exits from trace or log artifacts.",
    "",
]
note.write_text("\n".join(lines), encoding="utf-8")
print(note.resolve())
PY
```

- [ ] **Step 5: Commit comparison note**

Run:

```powershell
git add docs/superpowers/notes/2026-08-22-equity-top5-trailing-stop-5y-comparison.md
git commit -m "docs: compare equity top5 trailing stop run"
```

---

## Self-Review Checklist

- [ ] Spec coverage: Top 5 selected symbols, equal-weight deterministic target conversion, stop-before-weekly behavior, stop-sell cash handling, prompt changes, execution prompt compatibility, trace/audit discoverability, unit tests, smoke backtest, and five-year comparison are each mapped to tasks.
- [ ] Placeholder scan: The plan contains no deferred-work markers or vague "add tests" instructions. Runtime artifact paths are discovered by commands or requested with `Read-Host`.
- [ ] Type consistency: `selected_symbols` is consistently a list of uppercase symbols; `execution_plan` keeps existing schema version `1`; stop planner returns `stop_checks`, `updated_position_state`, and an existing-compatible `execution_plan`.
- [ ] Risk check: The plan does not give LLMs share sizing, order price calculation, trailing stop validity decisions, or same-day stop replacement decisions.
- [ ] Verification check: The plan requires focused unit tests before code, ruff, adjacent execution-plan regression tests, a one-month smoke backtest, UI inspection, and a five-year comparison.
