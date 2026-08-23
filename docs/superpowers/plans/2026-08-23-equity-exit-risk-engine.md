# Equity Exit Risk Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the equity-only LLM strategy's current simple trailing-stop check into a deterministic daily exit-risk engine with initial stop, trailing stop, trace-friendly diagnostics, and clean prompt boundaries.

**Architecture:** Keep exit risk outside LLM judgment. A local module checks current positions each trading day, emits a strict `risk_exit` sell execution_plan when a stop triggers, and otherwise lets the existing weekly/monthly equity selection workflow continue. The existing `execution_agent` executes the generated plan through `execution_plan_execute`; prompts should only describe responsibilities, not contain long stop-policy logic.

**Tech Stack:** Python, pytest, Lumibot strategy classes, existing local agent tooling, existing `execution_plan_execute` path, Yahoo daily backtesting smoke validation.

---

## File Structure

Modify:

- `lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py`
  - Evolve the existing trailing stop module into the deterministic exit-risk engine while preserving the public function name for minimal churn.
  - Add `DEFAULT_INITIAL_STOP_PCT`.
  - Add `entry_price`, `initial_stop_price`, `initial_stop_triggered`, `trailing_stop_price`, `trailing_stop_triggered`, and `trigger_reason` diagnostics.
  - Return `execution_plan.intent = "risk_exit"` when exit orders exist.

- `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`
  - Add strategy parameter `equity_initial_stop_pct`.
  - Pass both initial and trailing stop percentages to the exit-risk engine.
  - Keep daily exit check before scheduled workflow.
  - Seed position state with `entry_price`.
  - Remove full-exit symbols from stop state after scheduled rebalance.

- `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
  - Allow `risk_exit` in strict execution_plan normalization.
  - Add short prompt wording that local deterministic tooling handles exits.

- `tests/test_equity_trailing_stop_to_execution_plan.py`
  - Update existing tests from the older trailing-stop-only schema to the new exit-risk schema.
  - Add initial-stop and multiple-trigger tests.

- `tests/test_ai_trading_team_equity_only_llm.py`
  - Update strategy integration tests to expect initial stop parameter/state.
  - Add tests for scheduled full-exit state removal and prompt cleanup.

Create:

- `docs/superpowers/notes/2026-08-23-equity-exit-risk-engine-validation.md`
  - Record test commands, smoke backtest command, artifact path, and observed behavior.

Do not modify:

- `lumibot/example_strategies/dynamic_equity_portfolio_constructor.py`
- `lumibot/example_strategies/target_portfolio_to_execution_plan.py`
- QQQ historical universe resolver files
- Any untracked `project_notes/*` files unless explicitly requested

---

### Task 1: Allow Strict `risk_exit` Execution Plans

**Files:**

- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
- Test: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Add failing tests for risk_exit normalization**

Append this test near the other execution plan normalization tests in `tests/test_ai_trading_team_equity_only_llm.py`:

```python
def test_normalize_execution_plan_accepts_risk_exit_sell_plan():
    helpers = importlib.import_module("lumibot.example_strategies.ai_trading_team_equity_only_helpers")

    result = helpers.normalize_execution_plan(
        {
            "schema_version": 1,
            "intent": "risk_exit",
            "orders": [
                {
                    "sequence": 1,
                    "action": "submit_order",
                    "symbol": "NVDA",
                    "asset_type": "stock",
                    "side": "sell",
                    "quantity": 10,
                    "quantity_mode": "shares",
                    "order_type": "market",
                    "time_in_force": "day",
                }
            ],
        }
    )

    assert result["intent"] == "risk_exit"
    assert result["orders"][0]["symbol"] == "NVDA"
    assert result["orders"][0]["side"] == "sell"
    assert result["constraints"]["allow_negative_cash"] is False
```

Append this regression test near `test_validate_execution_plan_symbols_accepts_any_top5_selected_symbol`:

```python
def test_validate_execution_plan_symbols_ignores_risk_exit_sells():
    module = load_module()

    module.validate_execution_plan_symbols(
        {
            "schema_version": 1,
            "intent": "risk_exit",
            "orders": [
                {
                    "sequence": 1,
                    "action": "submit_order",
                    "symbol": "NVDA",
                    "asset_type": "stock",
                    "side": "sell",
                    "quantity": 10,
                    "quantity_mode": "shares",
                    "order_type": "market",
                    "time_in_force": "day",
                }
            ],
        },
        {
            "basket_id": "equity",
            "status": "active",
            "selected_symbols": ["AAPL", "MSFT", "ORCL"],
        },
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_normalize_execution_plan_accepts_risk_exit_sell_plan tests/test_ai_trading_team_equity_only_llm.py::test_validate_execution_plan_symbols_ignores_risk_exit_sells -q
```

Expected:

```text
FAIL
unsupported execution_plan intent: risk_exit
```

- [ ] **Step 3: Add `risk_exit` to allowed intents**

In `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`, change:

```python
ALLOWED_INTENTS = {"hold", "rebalance"}
```

to:

```python
ALLOWED_INTENTS = {"hold", "rebalance", "risk_exit"}
```

Then change the intent/order validation block inside `normalize_execution_plan()` from:

```python
if intent == "hold" and orders:
    raise ValueError("hold intent cannot include orders.")
if intent == "rebalance" and not orders:
    raise ValueError("execution_plan orders are required for rebalance intent.")
```

to:

```python
if intent == "hold" and orders:
    raise ValueError("hold intent cannot include orders.")
if intent in {"rebalance", "risk_exit"} and not orders:
    raise ValueError("execution_plan orders are required for rebalance or risk_exit intent.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_normalize_execution_plan_accepts_risk_exit_sell_plan tests/test_ai_trading_team_equity_only_llm.py::test_validate_execution_plan_symbols_ignores_risk_exit_sells -q
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit**

Run:

```powershell
git add lumibot/example_strategies/ai_trading_team_equity_only_helpers.py tests/test_ai_trading_team_equity_only_llm.py
git commit -m "feat: allow equity risk exit plans"
```

---

### Task 2: Upgrade Exit Engine Unit Contract

**Files:**

- Modify: `tests/test_equity_trailing_stop_to_execution_plan.py`
- Modify: `lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py`

- [ ] **Step 1: Replace existing test expectations with exit-risk schema**

In `tests/test_equity_trailing_stop_to_execution_plan.py`, update the state in `test_trailing_stop_returns_hold_when_no_position_breaches_threshold()` to include `entry_price`:

```python
state = {
    "NVDA": {"entry_date": "2024-09-02", "entry_price": 120.0, "peak_close": 150.0},
    "AAPL": {"entry_date": "2024-09-02", "entry_price": 190.0, "peak_close": 210.0},
}
```

Update the call to include `initial_stop_pct`:

```python
result = module.trailing_stop_to_execution_plan(
    strategy,
    date="2024-09-05",
    initial_stop_pct=0.12,
    trailing_stop_pct=0.20,
    position_state=state,
)
```

Add these assertions after the existing hold assertions:

```python
assert result["policy"] == {
    "initial_stop_pct": 0.12,
    "trailing_stop_pct": 0.2,
    "price_basis": "daily_close",
}
assert result["exit_checks"][0]["symbol"] == "AAPL"
assert result["exit_checks"][0]["entry_price"] == 190.0
assert result["exit_checks"][0]["initial_stop_price"] == pytest.approx(167.2)
assert result["exit_checks"][0]["trailing_stop_price"] == pytest.approx(168.0)
assert result["exit_checks"][0]["trigger_reason"] is None
assert result["updated_position_state"]["AAPL"]["entry_price"] == 190.0
```

Keep backward-compatible `stop_checks` assertions only if the implementation chooses to alias `stop_checks = exit_checks`. The preferred assertion is `exit_checks`.

- [ ] **Step 2: Add failing initial stop test**

Append:

```python
def test_initial_stop_generates_full_position_sell_when_entry_loss_breached():
    module = load_module()
    strategy = make_strategy(positions=[make_position("NVDA", 10)], prices={"NVDA": 87.0})
    state = {"NVDA": {"entry_date": "2024-09-02", "entry_price": 100.0, "peak_close": 105.0}}

    result = module.trailing_stop_to_execution_plan(
        strategy,
        date="2024-09-05",
        initial_stop_pct=0.12,
        trailing_stop_pct=0.20,
        position_state=state,
    )

    assert result["execution_plan"]["intent"] == "risk_exit"
    assert result["execution_plan"]["orders"] == [
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
    ]
    check = result["exit_checks"][0]
    assert check["initial_stop_price"] == 88.0
    assert check["trailing_stop_price"] == 84.0
    assert check["initial_stop_triggered"] is True
    assert check["trailing_stop_triggered"] is False
    assert check["triggered"] is True
    assert check["trigger_reason"] == "initial_stop"
    assert result["updated_position_state"] == {}
```

- [ ] **Step 3: Add failing trailing stop schema test**

Replace `test_trailing_stop_generates_full_position_sell_when_threshold_breached()` expected result with:

```python
def test_trailing_stop_generates_full_position_sell_when_threshold_breached():
    module = load_module()
    strategy = make_strategy(positions=[make_position("NVDA", 10)], prices={"NVDA": 118.0})
    state = {"NVDA": {"entry_date": "2024-09-02", "entry_price": 100.0, "peak_close": 150.0}}

    result = module.trailing_stop_to_execution_plan(
        strategy,
        date="2024-09-05",
        initial_stop_pct=0.12,
        trailing_stop_pct=0.20,
        position_state=state,
    )

    assert result["execution_plan"]["intent"] == "risk_exit"
    assert result["execution_plan"]["orders"][0]["symbol"] == "NVDA"
    assert result["execution_plan"]["orders"][0]["side"] == "sell"
    check = result["exit_checks"][0]
    assert check["entry_price"] == 100.0
    assert check["initial_stop_price"] == 88.0
    assert check["trailing_stop_price"] == 120.0
    assert check["initial_stop_triggered"] is False
    assert check["trailing_stop_triggered"] is True
    assert check["trigger_reason"] == "trailing_stop"
    assert result["updated_position_state"] == {}
```

- [ ] **Step 4: Add failing multiple trigger test**

Append:

```python
def test_exit_engine_can_sell_multiple_triggered_positions():
    module = load_module()
    strategy = make_strategy(
        positions=[make_position("NVDA", 10), make_position("AAPL", 5), make_position("MSFT", 3)],
        prices={"NVDA": 118.0, "AAPL": 170.0, "MSFT": 260.0},
    )
    state = {
        "NVDA": {"entry_date": "2024-09-02", "entry_price": 100.0, "peak_close": 150.0},
        "AAPL": {"entry_date": "2024-09-02", "entry_price": 200.0, "peak_close": 210.0},
        "MSFT": {"entry_date": "2024-09-02", "entry_price": 250.0, "peak_close": 270.0},
    }

    result = module.trailing_stop_to_execution_plan(
        strategy,
        date="2024-09-05",
        initial_stop_pct=0.12,
        trailing_stop_pct=0.20,
        position_state=state,
    )

    assert result["execution_plan"]["intent"] == "risk_exit"
    assert [order["symbol"] for order in result["execution_plan"]["orders"]] == ["AAPL", "NVDA"]
    assert result["updated_position_state"] == {
        "MSFT": {
            "entry_date": "2024-09-02",
            "entry_price": 250.0,
            "peak_close": 270.0,
            "last_check_date": "2024-09-05",
            "last_check_price": 260.0,
        }
    }
```

- [ ] **Step 5: Update invalid percentage test**

Replace `test_trailing_stop_rejects_negative_or_zero_trailing_stop_pct()` with:

```python
@pytest.mark.parametrize(
    ("initial_stop_pct", "trailing_stop_pct", "message"),
    [
        (0, 0.20, "initial_stop_pct must be between 0 and 1"),
        (1, 0.20, "initial_stop_pct must be between 0 and 1"),
        (0.12, 0, "trailing_stop_pct must be between 0 and 1"),
        (0.12, 1, "trailing_stop_pct must be between 0 and 1"),
    ],
)
def test_exit_engine_rejects_invalid_stop_percentages(initial_stop_pct, trailing_stop_pct, message):
    module = load_module()
    strategy = make_strategy(positions=[], prices={})

    with pytest.raises(ValueError, match=message):
        module.trailing_stop_to_execution_plan(
            strategy,
            date="2024-09-05",
            initial_stop_pct=initial_stop_pct,
            trailing_stop_pct=trailing_stop_pct,
            position_state={},
        )
```

- [ ] **Step 6: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_equity_trailing_stop_to_execution_plan.py -q
```

Expected:

```text
FAIL
TypeError: trailing_stop_to_execution_plan() got an unexpected keyword argument 'initial_stop_pct'
```

- [ ] **Step 7: Implement exit-risk schema and initial stop**

Modify `lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py`.

Add this constant below `DEFAULT_TRAILING_STOP_PCT`:

```python
DEFAULT_INITIAL_STOP_PCT = 0.12
```

Update `_normalized_state()` to preserve `entry_price`:

```python
def _normalized_state(position_state: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    for raw_symbol, raw_payload in dict(position_state or {}).items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol or not isinstance(raw_payload, dict):
            continue
        entry_date = str(raw_payload.get("entry_date") or "").strip()
        peak_close = raw_payload.get("peak_close")
        entry_price = raw_payload.get("entry_price", peak_close)
        if not entry_date or peak_close is None or entry_price is None:
            continue
        state[symbol] = {
            "entry_date": entry_date,
            "entry_price": _float(_positive_price(entry_price, f"entry_price for {symbol}")),
            "peak_close": _float(_positive_price(peak_close, f"peak_close for {symbol}")),
        }
    return state
```

Change the public function signature to:

```python
def trailing_stop_to_execution_plan(
    strategy: Any,
    *,
    date: str,
    initial_stop_pct: float = DEFAULT_INITIAL_STOP_PCT,
    trailing_stop_pct: float = DEFAULT_TRAILING_STOP_PCT,
    position_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
```

Inside the function, validate both percentages:

```python
initial_pct = _decimal(initial_stop_pct, "initial_stop_pct")
trailing_pct = _decimal(trailing_stop_pct, "trailing_stop_pct")
if initial_pct <= 0 or initial_pct >= 1:
    raise ValueError("initial_stop_pct must be between 0 and 1.")
if trailing_pct <= 0 or trailing_pct >= 1:
    raise ValueError("trailing_stop_pct must be between 0 and 1.")
```

Replace the per-symbol stop calculation block with:

```python
previous = state.get(symbol)
entry_date_text = str(previous.get("entry_date") if previous else date)
start_date = date_type.fromisoformat(entry_date_text)
close_points, price_source = _daily_close_window(
    strategy,
    symbol,
    start_date=start_date,
    end_date=end_date,
)
check_date, check_price = close_points[-1]
window_peak = max(price for _, price in close_points)
previous_peak = (
    _positive_price(previous["peak_close"], f"previous peak close for {symbol}")
    if previous
    else window_peak
)
entry_price = (
    _positive_price(previous["entry_price"], f"entry_price for {symbol}")
    if previous and previous.get("entry_price") is not None
    else check_price
)
peak_close = max(previous_peak, window_peak)
initial_stop_price = entry_price * (Decimal("1") - initial_pct)
trailing_stop_price = peak_close * (Decimal("1") - trailing_pct)
initial_stop_triggered = check_price <= initial_stop_price
trailing_stop_triggered = check_price <= trailing_stop_price
triggered = initial_stop_triggered or trailing_stop_triggered
trigger_reason = None
if initial_stop_triggered:
    trigger_reason = "initial_stop"
elif trailing_stop_triggered:
    trigger_reason = "trailing_stop"
planned_quantity = int(quantity)
```

Build each check with:

```python
exit_checks.append(
    {
        "symbol": symbol,
        "quantity": _float(quantity),
        "entry_date": entry_date_text,
        "entry_price": _float(entry_price),
        "previous_peak_close": _float(previous_peak),
        "peak_close": _float(peak_close),
        "current_check_price": _float(check_price),
        "initial_stop_pct": _float(initial_pct),
        "trailing_stop_pct": _float(trailing_pct),
        "initial_stop_price": _float(initial_stop_price),
        "trailing_stop_price": _float(trailing_stop_price),
        "initial_stop_triggered": initial_stop_triggered,
        "trailing_stop_triggered": trailing_stop_triggered,
        "triggered": triggered,
        "trigger_reason": trigger_reason,
        "price_source": price_source,
        "history_start_date": start_date.isoformat(),
        "history_end_date": check_date.isoformat() if check_date is not None else date,
        "history_bar_count": len(close_points),
    }
)
```

When not triggered, preserve state with entry price:

```python
updated_state[symbol] = {
    "entry_date": entry_date_text,
    "entry_price": _float(entry_price),
    "peak_close": _float(peak_close),
    "last_check_date": date,
    "last_check_price": _float(check_price),
}
```

Return:

```python
intent = "risk_exit" if orders else "hold"
return {
    "schema_version": "1.0",
    "date": date,
    "policy": {
        "initial_stop_pct": _float(initial_pct),
        "trailing_stop_pct": _float(trailing_pct),
        "price_basis": "daily_close",
    },
    "initial_stop_pct": _float(initial_pct),
    "trailing_stop_pct": _float(trailing_pct),
    "exit_checks": exit_checks,
    "stop_checks": exit_checks,
    "updated_position_state": updated_state,
    "execution_plan": {
        "schema_version": 1,
        "intent": intent,
        "orders": orders,
    },
    "warnings": [],
}
```

Keep `stop_checks` as a backward-compatible alias for now.

- [ ] **Step 8: Run unit tests**

Run:

```powershell
python -m pytest tests/test_equity_trailing_stop_to_execution_plan.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 9: Commit**

Run:

```powershell
git add lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py tests/test_equity_trailing_stop_to_execution_plan.py
git commit -m "feat: add equity initial and trailing exit risk checks"
```

---

### Task 3: Integrate Exit-Risk Policy Into Strategy State

**Files:**

- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`
- Test: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Add failing strategy tests for initial stop parameter and seeded entry price**

Update `test_scheduled_buy_seeds_trailing_stop_state()` expected state from:

```python
assert strategy._equity_trailing_stop_position_state["ORCL"] == {
    "entry_date": "2024-09-05",
    "peak_close": 100.0,
    "last_check_date": "2024-09-05",
    "last_check_price": 100.0,
}
```

to:

```python
assert strategy._equity_trailing_stop_position_state["ORCL"] == {
    "entry_date": "2024-09-05",
    "entry_price": 100.0,
    "peak_close": 100.0,
    "last_check_date": "2024-09-05",
    "last_check_price": 100.0,
}
```

- [ ] **Step 2: Update risk exit integration test fake signature**

In `test_trailing_stop_executes_and_skips_weekly_equity_agent()`, change:

```python
def fake_trailing_stop_to_execution_plan(strategy_arg, *, date, trailing_stop_pct, position_state):
```

to:

```python
def fake_trailing_stop_to_execution_plan(
    strategy_arg,
    *,
    date,
    initial_stop_pct,
    trailing_stop_pct,
    position_state,
):
```

Add:

```python
assert initial_stop_pct == pytest.approx(0.12)
```

Return a `risk_exit` intent and policy-shaped payload:

```python
"policy": {
    "initial_stop_pct": initial_stop_pct,
    "trailing_stop_pct": trailing_stop_pct,
    "price_basis": "daily_close",
},
"exit_checks": [
    {
        "symbol": "NVDA",
        "quantity": 10.0,
        "entry_date": "2024-09-02",
        "entry_price": 100.0,
        "previous_peak_close": 150.0,
        "peak_close": 150.0,
        "current_check_price": 118.0,
        "initial_stop_price": 88.0,
        "trailing_stop_price": 120.0,
        "initial_stop_triggered": False,
        "trailing_stop_triggered": True,
        "triggered": True,
        "trigger_reason": "trailing_stop",
        "price_source": "daily_close",
    }
],
"stop_checks": [
    {
        "symbol": "NVDA",
        "triggered": True,
        "trigger_reason": "trailing_stop",
    }
],
"execution_plan": {
    "schema_version": 1,
    "intent": "risk_exit",
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
```

Update the assertion:

```python
assert execution_call["context"]["execution_reason"] == "risk_exit"
```

- [ ] **Step 3: Update no-risk-exit fake signature**

In `test_no_trailing_stop_allows_weekly_equity_workflow()`, change the lambda signature to:

```python
lambda strategy_arg, *, date, initial_stop_pct, trailing_stop_pct, position_state: {
    "schema_version": "1.0",
    "date": date,
    "policy": {
        "initial_stop_pct": initial_stop_pct,
        "trailing_stop_pct": trailing_stop_pct,
        "price_basis": "daily_close",
    },
    "exit_checks": [],
    "stop_checks": [],
    "updated_position_state": {},
    "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []},
    "warnings": [],
}
```

- [ ] **Step 4: Add failing test for scheduled full exit clearing state**

Append:

```python
def test_scheduled_full_exit_removes_trailing_stop_state(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamEquityOnlyLLMStrategy)
    strategy.parameters["run_frequency"] = "daily"
    strategy._run_frequency = "daily"
    strategy._equity_trailing_stop_position_state = {
        "ORCL": {
            "entry_date": "2024-09-01",
            "entry_price": 100.0,
            "peak_close": 125.0,
            "last_check_date": "2024-09-04",
            "last_check_price": 120.0,
        }
    }
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "status": "active",
            "candidate_symbols": ["MSFT", "NVDA", "AAPL"],
            "selected_symbols": ["MSFT", "NVDA", "AAPL"],
            "reason_brief": "New target excludes ORCL.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."

    monkeypatch.setattr(
        module,
        "trailing_stop_to_execution_plan",
        lambda strategy_arg, *, date, initial_stop_pct, trailing_stop_pct, position_state: {
            "schema_version": "1.0",
            "date": date,
            "policy": {
                "initial_stop_pct": initial_stop_pct,
                "trailing_stop_pct": trailing_stop_pct,
                "price_basis": "daily_close",
            },
            "exit_checks": [],
            "stop_checks": [],
            "updated_position_state": dict(position_state),
            "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []},
            "warnings": [],
        },
    )

    def fake_target_portfolio_to_execution_plan(strategy_arg, *, date, target_portfolio):
        return {
            "schema_version": "1.0",
            "date": date,
            "target_portfolio": target_portfolio,
            "current_vs_target": [
                {"symbol": "ORCL", "planned_side": "sell", "planned_quantity": 10, "sizing_price": 100.0}
            ],
            "cash_projection": {
                "cash_before": 0.0,
                "estimated_sell_proceeds": 1000.0,
                "estimated_buy_cost": 0.0,
                "cash_after_estimate": 1000.0,
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
                        "side": "sell",
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

    assert "ORCL" not in strategy._equity_trailing_stop_position_state
```

- [ ] **Step 5: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_scheduled_buy_seeds_trailing_stop_state tests/test_ai_trading_team_equity_only_llm.py::test_trailing_stop_executes_and_skips_weekly_equity_agent tests/test_ai_trading_team_equity_only_llm.py::test_no_trailing_stop_allows_weekly_equity_workflow tests/test_ai_trading_team_equity_only_llm.py::test_scheduled_full_exit_removes_trailing_stop_state -q
```

Expected:

```text
FAIL
```

Existing code does not pass `initial_stop_pct`, still uses `execution_reason="trailing_stop"`, and does not clear scheduled full exits from stop state.

- [ ] **Step 6: Implement strategy integration**

In `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`, update imports:

```python
from lumibot.example_strategies.equity_trailing_stop_to_execution_plan import (
    DEFAULT_INITIAL_STOP_PCT,
    DEFAULT_TRAILING_STOP_PCT,
    trailing_stop_to_execution_plan,
)
```

In `_initialize_equity_only_workflow_state()`, add:

```python
self._equity_initial_stop_pct = float(
    self.parameters.get("equity_initial_stop_pct", DEFAULT_INITIAL_STOP_PCT)
)
```

In `_run_daily_trailing_stop_check()`, change the call to:

```python
result = trailing_stop_to_execution_plan(
    self,
    date=current_date,
    initial_stop_pct=self._equity_initial_stop_pct,
    trailing_stop_pct=self._equity_trailing_stop_pct,
    position_state=self._equity_trailing_stop_position_state,
)
```

Change the execution reason from:

```python
reason="trailing_stop",
```

to:

```python
reason="risk_exit",
```

In `_seed_trailing_stop_state_from_scheduled_plan()`, add entry price:

```python
self._equity_trailing_stop_position_state[symbol] = {
    "entry_date": current_date,
    "entry_price": sizing_price,
    "peak_close": sizing_price,
    "last_check_date": current_date,
    "last_check_price": sizing_price,
}
```

At the end of `_seed_trailing_stop_state_from_scheduled_plan()`, remove fully exited symbols:

```python
for order in execution_plan.get("orders", []):
    if not isinstance(order, dict):
        continue
    if str(order.get("side") or "").strip().lower() != "sell":
        continue
    symbol = str(order.get("symbol") or "").strip().upper()
    if not symbol:
        continue
    planned_quantity = order.get("quantity")
    current_quantity = None
    for row in planner_result.get("current_vs_target", []):
        if not isinstance(row, dict):
            continue
        if str(row.get("symbol") or "").strip().upper() == symbol:
            current_quantity = row.get("current_quantity")
            break
    try:
        is_full_exit = current_quantity is not None and float(planned_quantity) >= float(current_quantity)
    except (TypeError, ValueError):
        is_full_exit = False
    if is_full_exit:
        self._equity_trailing_stop_position_state.pop(symbol, None)
```

This code relies on `current_vs_target.current_quantity`, which is already produced by `target_portfolio_to_execution_plan()`.

- [ ] **Step 7: Run focused integration tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_scheduled_buy_seeds_trailing_stop_state tests/test_ai_trading_team_equity_only_llm.py::test_trailing_stop_executes_and_skips_weekly_equity_agent tests/test_ai_trading_team_equity_only_llm.py::test_no_trailing_stop_allows_weekly_equity_workflow tests/test_ai_trading_team_equity_only_llm.py::test_scheduled_full_exit_removes_trailing_stop_state -q
```

Expected:

```text
4 passed
```

- [ ] **Step 8: Commit**

Run:

```powershell
git add lumibot/example_strategies/ai_trading_team_equity_only_llm.py tests/test_ai_trading_team_equity_only_llm.py
git commit -m "feat: integrate equity exit risk state"
```

---

### Task 4: Clean Prompt Boundaries For Exit Risk

**Files:**

- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`
- Test: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Add failing prompt assertions**

In `test_equity_agent_system_prompt_is_equity_only_rank_first_and_conditional_news()`, add required strings:

```python
for required in (
    "existing positions are protected by a deterministic local exit-risk engine",
    "do not calculate stop-loss",
    "do not calculate take-profit",
    "do not create exit orders",
):
    assert required in prompt
```

Add forbidden strings:

```python
for forbidden in (
    "set stop-loss",
    "set take-profit",
    "calculate stop price",
):
    assert forbidden not in prompt
```

Append:

```python
def test_execution_agent_prompt_handles_risk_exit_without_second_guessing():
    module = load_module()
    strategy = make_strategy(module.AITradingTeamEquityOnlyLLMStrategy)

    strategy.initialize()

    execution_agent = created_agent_config(strategy, "execution_agent")
    prompt = execution_agent["system_prompt"].lower()

    assert "scheduled_rebalance or risk_exit" in prompt
    assert "execute only provided execution_plan" in prompt
    assert "do not research" in prompt
    assert "do not" in prompt and "second-guess" in prompt
    assert "calculate stop" not in prompt
    assert "take-profit" not in prompt
```

- [ ] **Step 2: Run prompt tests to verify failure**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_system_prompt_is_equity_only_rank_first_and_conditional_news tests/test_ai_trading_team_equity_only_llm.py::test_execution_agent_prompt_handles_risk_exit_without_second_guessing -q
```

Expected:

```text
FAIL
```

- [ ] **Step 3: Update equity prompts**

In `equity_basket_agent_system_prompt()` and `qqq_historical_equity_basket_agent_system_prompt()` in `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`, add this concise sentence near the existing role/sizing boundary text:

```python
"Existing positions are protected by a deterministic local exit-risk engine. "
"Do not calculate stop-loss, do not calculate take-profit, and do not create exit orders. "
```

Do not add a long stop policy section.

- [ ] **Step 4: Update execution prompt**

In `AITradingTeamEquityOnlyLLMStrategy.initialize()` in `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`, update the execution agent system prompt by changing:

```python
"If the tool returns plan_status=completed, summarize completed orders. If it returns "
```

to:

```python
"The execution_reason may be scheduled_rebalance or risk_exit; in both cases, execute the provided "
"plan and do not second-guess why the plan exists. "
"If the tool returns plan_status=completed, summarize completed orders. If it returns "
```

- [ ] **Step 5: Run prompt tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_system_prompt_is_equity_only_rank_first_and_conditional_news tests/test_ai_trading_team_equity_only_llm.py::test_execution_agent_prompt_handles_risk_exit_without_second_guessing -q
```

Expected:

```text
2 passed
```

- [ ] **Step 6: Commit**

Run:

```powershell
git add lumibot/example_strategies/ai_trading_team_equity_only_helpers.py lumibot/example_strategies/ai_trading_team_equity_only_llm.py tests/test_ai_trading_team_equity_only_llm.py
git commit -m "fix: clarify equity exit risk prompt boundaries"
```

---

### Task 5: Regression Suite And Smoke Backtest

**Files:**

- Create: `docs/superpowers/notes/2026-08-23-equity-exit-risk-engine-validation.md`

- [ ] **Step 1: Run focused unit and integration suite**

Run:

```powershell
python -m pytest tests/test_equity_trailing_stop_to_execution_plan.py tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_tool_permissions.py -k "equity or trailing_stop or execution_plan_execute or risk_exit" -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 2: Run full focused strategy tests**

Run:

```powershell
python -m pytest tests/test_dynamic_equity_portfolio_constructor.py tests/test_ai_trading_team_equity_only_llm.py tests/test_equity_trailing_stop_to_execution_plan.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 3: Run lint**

Run:

```powershell
python -m ruff check lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py lumibot/example_strategies/ai_trading_team_equity_only_llm.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py tests/test_equity_trailing_stop_to_execution_plan.py tests/test_ai_trading_team_equity_only_llm.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 4: Run short QQQ historical smoke backtest**

Use the OpenAI key from the user's normalized env file and the current default model convention.

Run:

```powershell
$env:AI_TRADING_TEAM_MODEL = "gpt-5.6-luna"
python scripts\run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2024-09-05 --end 2024-09-06 --run-frequency weekly --weekly-run-weekday THU --env-file D:\Lumibot\project_notes\API.txt --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 240
```

Expected:

```text
Benchmark completes with status passed.
Artifacts are written under artifacts/ai_trading_team_example_benchmarks/<run_id>/qqq-historical-equity-only-llm.
```

If no natural stop trigger occurs, that is acceptable. Confirm from artifacts or trace that the strategy initialized and scheduled workflow still ran.

- [ ] **Step 5: Inspect latest artifact for exit-risk fields**

Find latest artifact:

```powershell
Get-ChildItem artifacts\ai_trading_team_example_benchmarks | Sort-Object LastWriteTime | Select-Object -Last 1
```

Search for risk-exit fields:

```powershell
rg -n "risk_exit|exit_checks|initial_stop_pct|trailing_stop_pct|deterministic local exit-risk engine" artifacts\ai_trading_team_example_benchmarks -S
```

Expected:

```text
Prompt text includes deterministic local exit-risk engine.
If trace captures strategy context/state for the run, exit-risk fields appear.
No execution_agent prompt asks the model to calculate stops.
```

- [ ] **Step 6: Write validation note**

Create `docs/superpowers/notes/2026-08-23-equity-exit-risk-engine-validation.md` with:

```markdown
# Equity Exit Risk Engine Validation

## Scope

Validated the deterministic equity exit-risk engine feature from `docs/superpowers/specs/2026-08-23-equity-exit-risk-engine-design.md`.

## Test Commands

```powershell
python -m pytest tests/test_equity_trailing_stop_to_execution_plan.py tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_tool_permissions.py -k "equity or trailing_stop or execution_plan_execute or risk_exit" -q
python -m pytest tests/test_dynamic_equity_portfolio_constructor.py tests/test_ai_trading_team_equity_only_llm.py tests/test_equity_trailing_stop_to_execution_plan.py -q
python -m ruff check lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py lumibot/example_strategies/ai_trading_team_equity_only_llm.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py tests/test_equity_trailing_stop_to_execution_plan.py tests/test_ai_trading_team_equity_only_llm.py
```

## Smoke Backtest

```powershell
$env:AI_TRADING_TEAM_MODEL = "gpt-5.6-luna"
python scripts\run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2024-09-05 --end 2024-09-06 --run-frequency weekly --weekly-run-weekday THU --env-file D:\Lumibot\project_notes\API.txt --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 240
```

## Results

- Focused tests: paste the final pytest summary line, for example `42 passed in 8.12s`.
- Ruff: paste the final ruff summary line, for example `All checks passed!`.
- Smoke artifact: paste the concrete artifact directory path from the completed smoke run.
- Exit trigger occurred naturally: write `yes` or `no` and cite the searched artifact evidence.
- Scheduled equity workflow still ran when no exit trigger occurred: write `yes` or `no` and cite the searched artifact evidence.

## Notes

- Daily risk exits are close-based checks in this first version, not broker-native intraday stop orders.
- Trigger behavior is covered by deterministic unit tests even if the smoke window does not naturally trigger a stop.
```

Do not commit this note until every result bullet above contains the actual observed run output or artifact evidence.

- [ ] **Step 7: Commit validation note**

Run:

```powershell
git add docs/superpowers/notes/2026-08-23-equity-exit-risk-engine-validation.md
git commit -m "docs: validate equity exit risk engine"
```

---

### Task 6: Final Review Before Completion

**Files:**

- Review: all changed files

- [ ] **Step 1: Check worktree status**

Run:

```powershell
git status --short --branch
```

Expected:

```text
Only known unrelated untracked files may remain, especially project_notes/qqq_historical_5y_deep_dive_20260822_180628.md.
No implementation files should be unstaged.
```

- [ ] **Step 2: Review final diff against feature base**

Run:

```powershell
git log --oneline --max-count 8
git diff --stat HEAD~5..HEAD
```

Expected:

```text
Commits are small and scoped to risk_exit plan support, exit-risk engine, strategy integration, prompt cleanup, and validation docs.
```

- [ ] **Step 3: Run final focused verification**

Run:

```powershell
python -m pytest tests/test_dynamic_equity_portfolio_constructor.py tests/test_ai_trading_team_equity_only_llm.py tests/test_equity_trailing_stop_to_execution_plan.py tests/test_agent_tool_permissions.py -q
python -m ruff check lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py lumibot/example_strategies/ai_trading_team_equity_only_llm.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py tests/test_equity_trailing_stop_to_execution_plan.py tests/test_ai_trading_team_equity_only_llm.py
```

Expected:

```text
pytest passes.
ruff passes.
```

- [ ] **Step 4: Request code review**

Use the code review process before declaring completion. Ask the reviewer to focus on:

```text
1. Whether risk_exit plans remain compatible with execution_plan_execute.
2. Whether initial/trailing stop diagnostics are sufficient for trace/UI.
3. Whether strategy state is updated correctly after scheduled buys and sells.
4. Whether prompt changes are concise and do not invite LLM stop-price calculation.
5. Whether close-based stop semantics are clearly documented and tested.
```

- [ ] **Step 5: Address review feedback when the reviewer reports a defect**

If review finds issues, fix them with tests and commit:

```powershell
git add <changed-files>
git commit -m "fix: address equity exit risk review"
```

- [ ] **Step 6: Final response**

Report:

```text
Implementation completed.
Tests run and results.
Smoke backtest artifact path.
Whether natural stop trigger occurred.
Known limitation: first version is daily close based, not broker-native intraday stop.
```
