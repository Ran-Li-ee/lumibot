# Equity Exit Risk Engine Design

## Purpose

Upgrade the current equity-only LLM strategy's simple trailing stop check into a systematic exit-risk layer.

The goal is to protect individual positions from large downside moves while preserving the strategy's core idea:

```text
LLM selects strong equity candidates
deterministic constructor sizes the target portfolio
deterministic planner creates the execution_plan
execution_agent executes the plan
```

The exit-risk layer should not become another LLM judgment step. It should be a local, deterministic risk engine that checks current holdings each trading day and creates a strict sell execution plan only when clearly defined exit rules are triggered.

## Background

The current QQQ historical equity-only strategy already has a useful shape:

```text
on_trading_iteration()
  -> _run_daily_trailing_stop_check()
  -> if a trailing stop sell is generated, execute it and skip scheduled selection
  -> otherwise, check whether today is the weekly/monthly scheduled run day
  -> if scheduled, run equity_agent
  -> dynamic equity constructor creates target_portfolio
  -> target_portfolio_to_execution_plan creates execution_plan
  -> execution_agent executes execution_plan
```

There is already an implementation seed in:

```text
lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py
```

That module currently does one main thing:

```text
if current_check_price <= holding_period_peak_price * (1 - trailing_stop_pct):
    sell the full position
```

This is directionally correct, but it is too narrow for the strategy we are now building. The next version should explicitly support:

1. Initial loss protection.
2. Moving profit protection through trailing stop logic.
3. Clear daily check behavior.
4. Clean separation from LLM selection and execution prompts.
5. Trace-friendly diagnostics.

## Strategy Fit

This equity-only strategy is closer to a momentum / relative-strength / trend-following portfolio than to a traditional buy-and-hold strategy.

Relevant external practice suggests several useful patterns:

1. CAN SLIM / IBD-style growth trading often uses fixed percentage loss limits around 7%-8%.
2. Momentum crash research often evaluates stop-loss thresholds around 10%-15%.
3. Trend-following systems often use volatility-aware trailing stops such as ATR multiples.
4. Relative-strength rotation systems often sell when a holding loses rank leadership.

For this strategy, the best fit is not a fixed take-profit rule. The system should let winners run and use trailing exits to protect gains.

## Design Principle

Separate selection, sizing, exit rules, and execution.

```text
equity_agent:
  choose and explain attractive candidates

dynamic_equity_portfolio_constructor:
  choose final holdings and target weights

target_portfolio_to_execution_plan:
  convert target weights into orders

equity_exit_risk_engine:
  check existing holdings for deterministic exit triggers

execution_agent:
  execute only the provided execution_plan
```

The LLM should not decide exact stop prices during normal operation. Stop logic should be calculated locally from position state and market data.

## Scope

This feature includes:

1. Replace or evolve `equity_trailing_stop_to_execution_plan.py` into a clearer exit-risk engine.
2. Preserve daily exit checks before scheduled weekly/monthly selection.
3. Add initial loss protection.
4. Preserve moving trailing stop behavior.
5. Add configuration parameters for initial stop and trailing stop.
6. Keep the first implementation percentage-based by default.
7. Make the module ready for ATR-based stops later, without requiring ATR in this first implementation.
8. Store detailed exit diagnostics for trace and replay UI inspection.
9. Update strategy prompts to clarify that exit risk is handled by local deterministic tools, not by LLM discretion.
10. Update tests around no-trigger, initial-stop trigger, trailing-stop trigger, state update, scheduled workflow interaction, and trace-friendly output.
11. Run focused tests and a short smoke backtest.

## Non-Goals

This feature does not:

1. Change equity rank indicators.
2. Change dynamic target portfolio construction.
3. Change QQQ historical universe logic.
4. Add broker-native bracket orders.
5. Add live broker stop orders.
6. Add fixed take-profit orders.
7. Let LLM calculate stop prices.
8. Let execution_agent decide whether to override an exit.
9. Introduce shorting, leverage, options, or margin.
10. Optimize stop parameters for best backtest performance.
11. Promise performance improvement before comparison backtests.

## Exit Rules

### Rule 1: Initial Stop

Initial stop protects against a position that moves badly soon after entry.

Suggested first version:

```text
initial_stop_pct = 0.12
initial_stop_price = entry_price * (1 - initial_stop_pct)

if current_check_price <= initial_stop_price:
    sell full position
```

Rationale:

```text
7%-8% can be too tight for high-growth QQQ constituents.
10%-15% is commonly discussed in momentum stop-loss research.
12% is a practical first default, not a final optimized number.
```

### Rule 2: Trailing Stop

Trailing stop protects profits while letting winners run.

Suggested first version:

```text
trailing_stop_pct = 0.20
trailing_stop_price = peak_close_since_entry * (1 - trailing_stop_pct)

if current_check_price <= trailing_stop_price:
    sell full position
```

Rationale:

```text
This is intentionally looser than a short-term trading stop.
The strategy wants to capture major winners, not sell normal pullbacks too early.
```

### Rule 3: Rank Failure Exit

Rank failure should remain part of the scheduled rebalance path, not the daily exit-risk path.

On scheduled weekly/monthly runs:

```text
equity_agent + constructor choose current best candidates
target_portfolio_to_execution_plan compares current holdings to target holdings
symbols no longer in target_portfolio are reduced or sold
```

The daily exit-risk engine should not call the LLM and should not re-rank the full universe. That would turn a simple daily safety check into an expensive daily selection run.

## Future ATR Rule

ATR-based logic is a likely second-stage improvement, but not required in this first feature.

Future shape:

```text
initial_stop_distance = max(initial_stop_pct, initial_atr_multiplier * atr_pct)
trailing_stop_distance = max(trailing_stop_pct, trailing_atr_multiplier * atr_pct)
```

Possible future defaults:

```text
initial_atr_multiplier = 2.5
trailing_atr_multiplier = 3.0
atr_window = 14 or 20 trading days
```

The first implementation should structure diagnostics so ATR fields can be added later without changing the execution_plan schema.

## Position State

The engine needs per-position state.

Minimum state:

```json
{
  "SYMBOL": {
    "entry_date": "2024-09-05",
    "entry_price": 120.0,
    "peak_close": 145.0,
    "last_check_date": "2024-09-12",
    "last_check_price": 141.0
  }
}
```

When a scheduled buy creates a new position, seed state with:

```text
entry_date = current_date
entry_price = planner sizing price or known fill/sizing estimate
peak_close = entry_price
last_check_date = current_date
last_check_price = entry_price
```

When a position is still held after daily check:

```text
peak_close = max(previous_peak_close, latest_close_in_holding_window)
last_check_date = current_date
last_check_price = current_check_price
```

When a position is sold by exit-risk engine:

```text
remove it from updated_position_state
```

When a scheduled rebalance sells a position:

```text
remove it from updated_position_state
```

When a scheduled rebalance reduces but does not fully exit a position:

```text
keep existing entry_date, entry_price, and peak_close
```

When a scheduled rebalance increases an existing position:

```text
keep existing state in first implementation
```

This avoids complexity around blended entry prices. A later version can track lots if needed.

## Price Source Policy

Daily exit checks should use completed daily close data when possible.

Reason:

```text
The strategy is currently low-frequency.
The daily exit engine is a close-based safety check, not an intraday stop order simulator.
Using daily close avoids pretending that Yahoo daily backtests have true intraday stop execution.
```

First version:

```text
Use latest available daily close within holding window.
Fallback to strategy.get_last_price only if daily close is unavailable.
Record price_source in diagnostics.
```

Important limitation:

```text
This is not the same as a real broker-native intraday stop order.
It is a deterministic daily review that exits on the next available strategy execution.
```

The trace should make this limitation visible.

## Output Contract

The engine should return:

```json
{
  "schema_version": "1.0",
  "date": "2024-09-12",
  "policy": {
    "initial_stop_pct": 0.12,
    "trailing_stop_pct": 0.20,
    "price_basis": "daily_close"
  },
  "exit_checks": [
    {
      "symbol": "ORCL",
      "quantity": 217,
      "entry_date": "2024-09-05",
      "entry_price": 120.0,
      "previous_peak_close": 145.0,
      "peak_close": 145.0,
      "current_check_price": 124.0,
      "initial_stop_price": 105.6,
      "trailing_stop_price": 116.0,
      "initial_stop_triggered": false,
      "trailing_stop_triggered": false,
      "triggered": false,
      "trigger_reason": null,
      "price_source": "daily_close_window",
      "history_bar_count": 5
    }
  ],
  "updated_position_state": {
    "ORCL": {
      "entry_date": "2024-09-05",
      "entry_price": 120.0,
      "peak_close": 145.0,
      "last_check_date": "2024-09-12",
      "last_check_price": 124.0
    }
  },
  "execution_plan": {
    "schema_version": 1,
    "intent": "risk_exit",
    "orders": []
  },
  "warnings": []
}
```

If triggered:

```json
{
  "execution_plan": {
    "schema_version": 1,
    "intent": "risk_exit",
    "orders": [
      {
        "sequence": 1,
        "action": "submit_order",
        "symbol": "ORCL",
        "side": "sell",
        "quantity_mode": "shares",
        "quantity": 217,
        "asset_type": "stock",
        "order_type": "market",
        "time_in_force": "day"
      }
    ]
  }
}
```

The execution_plan schema must remain compatible with `execution_plan_execute`.

## Integration With Current Strategy

The strategy's top-level flow should remain:

```text
on_trading_iteration()
  -> run daily exit-risk check
  -> if exit-risk execution_plan has sell orders:
       execution_agent executes it
       do not run scheduled selection in the same iteration
  -> else:
       maybe run scheduled equity selection
```

Reason:

```text
If a position hits a risk exit, first reduce risk.
Do not immediately let the same iteration re-buy another name just because it is also a scheduled rebalance day.
This keeps daily safety exits mechanically simple and traceable.
```

Future work may allow same-day risk exit followed by scheduled rebalance, but that should be deliberately tested later.

## Prompt Changes

### Equity Agent Prompt

Equity agent should not be asked to manage stop-losses.

It should say, briefly:

```text
Your role is candidate selection and evidence interpretation.
Do not calculate stop-loss, take-profit, or exit orders.
Existing positions are protected by a deterministic local exit-risk engine.
```

### Execution Agent Prompt

Execution agent already has a good contract:

```text
execute only provided execution_plan
call execution_plan_execute exactly once
do not research, repair, or reorder
```

It should be lightly updated only if necessary to recognize:

```text
execution_reason may be scheduled_rebalance or risk_exit
```

It should not receive long stop policy explanations.

### System Prompt

Do not add a long stop-loss explanation to the base system prompt.

If any system-level wording is needed, keep it short:

```text
Portfolio exits may be generated by deterministic local tools. Agents should not override strict execution_plan outputs.
```

Avoid large prompt additions. The exit engine should be code-owned, not prompt-owned.

## Tool And Agent Permissions

No new LLM-facing tool is required for the first implementation.

The exit-risk engine can run directly inside strategy code before scheduled agent workflow.

Reason:

```text
The daily stop check does not need LLM judgment.
Making it an LLM-callable tool would spend tokens and introduce unnecessary variance.
```

The only agent involved after a trigger is:

```text
execution_agent
```

The execution agent receives:

```json
{
  "date": "...",
  "execution_reason": "risk_exit",
  "execution_plan": {...}
}
```

## Trace And UI Requirements

The result should be preserved in strategy state and replay artifacts as much as the current trace system allows.

At minimum, strategy state should keep:

```text
_last_exit_risk_result
_exit_risk_events
_equity_exit_position_state
```

If existing names are kept for compatibility:

```text
_last_trailing_stop_result
_trailing_stop_events
_equity_trailing_stop_position_state
```

the output fields should still use the broader "exit risk" language in diagnostics.

Replay UI should be able to show:

1. Which positions were checked.
2. Current check price.
3. Entry price.
4. Peak price.
5. Initial stop price.
6. Trailing stop price.
7. Trigger status.
8. Trigger reason.
9. Generated sell execution_plan.
10. Price source and warnings.

This feature does not require a new UI surface, but the data should be structured for later display.

## Testing Requirements

### Unit Tests For Exit Engine

Add or update tests around:

1. No positions returns hold.
2. Held position above both stops returns hold and updates peak.
3. Held position below initial stop generates full sell.
4. Held position below trailing stop generates full sell.
5. New high updates `peak_close`.
6. Daily close unavailable falls back to last price with warning.
7. Invalid stop percentages are rejected.
8. Entry price is preserved in updated state.
9. Triggered symbols are removed from updated state.
10. Multiple symbols can trigger in one daily check.

### Strategy Integration Tests

Update or add tests around:

1. Daily risk exit runs before scheduled equity workflow.
2. If risk exit triggers, scheduled equity agent is skipped that iteration.
3. If no risk exit triggers, scheduled workflow runs normally.
4. Scheduled buys seed exit-risk state.
5. Scheduled full exits remove exit-risk state.
6. Execution agent receives `execution_reason = "risk_exit"` for stop-triggered sells.
7. Execution plan remains compatible with `execution_plan_execute`.

### Prompt Tests

Tests should verify:

1. Equity prompt does not ask the LLM to calculate stop-loss or take-profit orders.
2. Equity prompt states that deterministic local tooling handles exit risk.
3. Execution prompt still says to execute exactly one provided execution_plan.
4. Execution prompt does not tell the agent to research or second-guess risk exits.

### Smoke Backtest

Run a short QQQ historical equity-only backtest.

Minimum smoke goal:

```text
strategy initializes
daily exit-risk check runs
scheduled selection still runs when no exit trigger occurs
artifacts are produced
```

If a natural stop trigger does not occur in the smoke window, that is acceptable. Unit tests should verify trigger behavior deterministically.

## Acceptance Criteria

This feature is accepted when:

1. The strategy has a deterministic daily exit-risk layer.
2. The layer supports both initial stop and trailing stop checks.
3. Stop calculations are local-code-owned, not LLM-owned.
4. Exit checks produce trace-friendly diagnostics.
5. Triggered exits create strict market/day sell execution plans.
6. Execution agent executes risk exits through the existing `execution_plan_execute` path.
7. Scheduled equity selection still works when no exit is triggered.
8. Prompts are cleaned so the LLM does not think it owns stop-loss decisions.
9. Focused tests pass.
10. A short smoke backtest passes.

## Risks And Mitigations

| Risk | Mitigation |
|---|---|
| Stops are too tight and sell winners too early | Use loose first defaults and treat parameter tuning as future research. |
| Stops are too loose and do not protect enough | Record diagnostics so backtests can compare trigger timing and drawdowns. |
| Daily close checks are mistaken for broker-native intraday stops | Document `price_basis` and `price_source` explicitly in output. |
| LLM tries to override risk exits | Keep risk exit outside equity_agent and keep execution_agent contract strict. |
| Position state becomes stale after scheduled rebalance | Seed buys and remove full exits during scheduled planner integration. |
| Partial reductions create confusing blended entry prices | Preserve original state for first implementation; lot-level tracking is future work. |
| Same-day stop exit plus scheduled rebalance creates churn | If risk exit triggers, skip scheduled selection for that iteration. |
| Trace grows too large | Keep full structured diagnostics local/trace-facing; keep model-facing execution summary concise. |

## Future Work

Future improvements can include:

1. ATR-based initial stop and trailing stop.
2. Broker-native live stop orders for real trading.
3. Lot-level entry price tracking.
4. Rank failure exit diagnostics in the constructor.
5. Market-regime dependent stop width.
6. Separate rules for mega-cap, high-beta, and volatile growth stocks.
7. Strategy review chart overlays showing stop-trigger dates.
8. Parameter sweep comparing 10%, 12%, 15%, 20%, ATR, and hybrid stops.
9. Same-day risk exit followed by controlled rebalance if tests prove it is beneficial.
