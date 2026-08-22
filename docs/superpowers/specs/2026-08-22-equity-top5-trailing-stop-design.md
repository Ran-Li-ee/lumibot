# Equity Top 5 Allocation And Daily Trailing Stop Design

## Purpose

The current QQQ historical equity-only LLM strategy has shown strong return potential, but it behaves like a single-stock all-in momentum system. The five-year backtest ending 2026-08-14 produced higher total return than SPY, but with much larger drawdown and volatility. The most important strategy-level weakness is concentration risk: the equity agent selects one symbol, and downstream deterministic planning gives that symbol a 100% target weight.

This feature changes the strategy from:

```text
Select 1 stock -> target 100% in that stock
```

to:

```text
Select Top 5 stocks -> target 20% each
```

It also adds a deterministic daily trailing stop check so that already-held stocks can be exited before the next weekly rebalance if they fall too far from their holding-period high.

The goal is not to maximize one backtest result. The goal is to reduce avoidable single-stock crash risk while preserving the strategy's ability to capture strong equity momentum.

## Current Evidence

The recent QQQ historical five-year run showed:

- Strategy total return around +152%, compared with SPY around +86%.
- Strategy max drawdown around -65%, compared with SPY around -24%.
- Strategy annualized volatility around 72%, compared with SPY around 17%.
- Equity agent made 261 weekly decisions.
- Selection changed 122 times.
- The strategy was effectively all-in one stock most of the time.
- `alpaca_news` was not called in that run; the equity agent relied almost entirely on `market_load_history_tables_summary`.

This feature does not attempt to solve all of those issues. It focuses on the most direct concentration and downside-control problems.

## Non-Goals

This feature must not implement:

- LLM-generated portfolio weights.
- ATR-based stop loss.
- Broker-native stop orders.
- Intraday stop checks.
- Same-day replacement after a trailing stop sale.
- Top 3, Top 10, or adaptive Top-N experiments.
- Mandatory news calls.
- Sector caps or factor caps.
- A new benchmark runner framework.
- A full five-year optimization sweep.

Those are possible future improvements, but this feature should remain narrow enough to evaluate cleanly.

## Strategy Behavior

### Weekly Rebalance

On the normal scheduled rebalance day, the strategy should:

1. Resolve the current equity universe.
2. Ask the equity agent to evaluate only the provided `basket_symbols`.
3. Require the equity agent to return exactly five selected symbols.
4. Convert the five selected symbols into a deterministic equal-weight target portfolio:

```json
[
  {"basket_id": "equity", "symbol": "A", "target_weight": 0.2},
  {"basket_id": "equity", "symbol": "B", "target_weight": 0.2},
  {"basket_id": "equity", "symbol": "C", "target_weight": 0.2},
  {"basket_id": "equity", "symbol": "D", "target_weight": 0.2},
  {"basket_id": "equity", "symbol": "E", "target_weight": 0.2}
]
```

5. Pass that target portfolio to the existing deterministic execution-plan generator.
6. Let the execution agent call `execution_plan_execute` exactly once with the generated execution plan.

The LLM must not calculate share quantities, order prices, or cash buffers. Those remain deterministic tool responsibilities.

### Daily Trailing Stop Check

On every trading day, the strategy should run a deterministic trailing stop check before deciding whether to run the weekly LLM rebalance.

The daily check should:

1. Read current non-cash positions.
2. For each held stock, determine its holding start date or the earliest date available for the current open position.
3. Load daily historical prices from the holding start date through the current date.
4. Compute the highest close during the holding period. The first version uses highest close instead of intraday high because it is less noisy for daily backtests and matches the daily-check design.
5. Compare current check price to the trailing stop threshold.
6. If:

```text
current_check_price <= holding_period_peak_price * (1 - trailing_stop_pct)
```

then the position is stop-triggered.

Default:

```text
trailing_stop_pct = 0.20
```

When a stop is triggered, the strategy should create an execution plan that sells the full position for that symbol.

### Stop Sale Cash Handling

If a trailing stop sale happens:

- Sell the stopped symbol.
- Do not buy a replacement on the same day.
- Leave the proceeds as cash.
- Let the next scheduled weekly rebalance redistribute that cash.

This keeps stop logic separate from selection logic and avoids spending LLM tokens on unscheduled replacement decisions.

### Same-Day Interaction Between Stop Check And Weekly Rebalance

If a day is both:

- a weekly rebalance day, and
- a day where one or more trailing stops trigger,

the first version should prioritize the stop event:

```text
Run stop check -> if any stop orders are generated, execute stop orders and skip weekly rebalance for that day.
```

Rationale: a stop trigger means the risk-control layer found a position whose trend has broken. Skipping same-day rebalance avoids selling a stopped stock and immediately buying another stock without a fresh full weekly decision after the cash state updates.

The next trading week can rebalance normally.

## Agent Output Contract

### Equity Agent Summary

The equity agent should return strict JSON with a new primary field:

```json
{
  "basket_id": "equity",
  "target_weight": 1.0,
  "status": "active",
  "candidate_symbols": ["... all assigned basket symbols ..."],
  "selected_symbols": ["A", "B", "C", "D", "E"],
  "reason_brief": "Short explanation of why these five are the strongest current choices."
}
```

Rules:

- `selected_symbols` must contain exactly five symbols.
- Symbols must be uppercase.
- Symbols must be unique.
- Every selected symbol must exist in `basket_symbols`.
- `candidate_symbols` must continue to copy the assigned `basket_symbols` exactly, not a shortlist.
- The equity agent may mention close alternatives in `reason_brief`, but alternatives must not be treated as selected.
- The old `selected_symbol` field may be tolerated for backward compatibility in existing tests or older traces, but the QQQ historical Top 5 strategy must require `selected_symbols`.

### Target Portfolio Conversion

The target portfolio builder should convert `selected_symbols` into equal weights:

```text
weight_per_symbol = 1.0 / len(selected_symbols)
```

For this feature, `len(selected_symbols)` must be exactly 5, so each target weight is `0.2`.

The builder should reject:

- Missing `selected_symbols`.
- Fewer than five symbols.
- More than five symbols.
- Duplicate symbols.
- Symbols outside the resolved universe.
- Inactive equity reports.
- Non-equity basket reports.

## Prompt Changes

### Equity Agent System Prompt

The equity agent prompt should be updated so it no longer says the selected stock receives target weight `1.0`.

It should say:

- The agent selects exactly five stocks.
- The downstream deterministic planner gives the selected stocks equal weights.
- The agent must not size trades.
- The agent must not rank by gut feeling.
- It should use `market_load_history_tables_summary` first.
- It may use `alpaca_news` when leading candidates are close, conflicting, or uncertain.

The prompt should not imply that selecting one winner is enough.

### Equity Agent Task Prompt

The task prompt should:

- Ask for exactly five selected symbols.
- Keep the requirement to use `market_load_history_tables_summary` first.
- Keep `top_n=10` unless implementation reveals the Top 5 selection needs a larger summary.
- Explain that `selected_symbols` should reflect the five strongest names across separate ranking views, not merely the first five listed in one ranking.
- Require strict JSON.

### Execution Agent Prompt

The execution agent prompt should remain mostly unchanged:

- Execute only the provided `execution_plan`.
- Call `execution_plan_execute` exactly once.
- Do not recalculate portfolio logic.

It may need one small wording addition:

- Execution plans may now represent either scheduled rebalance orders or trailing-stop exit orders.

The execution agent should not be asked to decide whether a stop is valid.

## Tool And Deterministic Logic Changes

### Existing Deterministic Planner

The existing `target_portfolio_to_execution_plan()` should continue to handle scheduled rebalance plans. It already accepts a list of target portfolio items, so Top 5 equal weight should mostly exercise existing multi-target behavior.

The implementation should verify that:

- Existing sell-down logic works when reducing a symbol from 100% to 20%.
- Existing buy logic works for multiple symbols.
- Existing cash buffer logic still prevents negative cash.
- Existing order ordering sells before buys when cash is needed.

### New Trailing Stop Planner

Add a deterministic trailing stop planner with a clear interface, such as:

```python
trailing_stop_to_execution_plan(
    strategy,
    *,
    date: str,
    trailing_stop_pct: float,
) -> dict[str, Any]
```

The returned object should follow the same execution-plan schema already accepted by `execution_plan_execute`.

If no stop is triggered, it should return a hold plan or a compact result that the strategy can interpret as no-op.

The stop planner should include audit metadata, for example:

```json
{
  "stop_checks": [
    {
      "symbol": "NVDA",
      "quantity": 100,
      "holding_start_date": "2025-01-06",
      "peak_close": 150.0,
      "current_check_price": 118.0,
      "trailing_stop_pct": 0.2,
      "stop_price": 120.0,
      "triggered": true
    }
  ]
}
```

Trace/UI should be able to show this information later, even if the first implementation stores it only in strategy events or execution-plan metadata.

## Trace And UI Expectations

The existing agent workflow replay UI should remain compatible.

For weekly rebalance runs, the trace should show:

- Equity agent input context.
- Equity agent Top 5 output.
- Generated target portfolio with five 20% weights.
- Execution agent order execution.

For daily stop checks, there may be no LLM agent trace if no LLM is called. The implementation should still record enough local strategy event data to answer:

- Did stop check run on this date?
- Which positions were checked?
- What peak/current/threshold values were used?
- Was any stop triggered?
- What order was generated?

The first version does not need to build a new UI panel for stop checks. It only needs to store the data in a discoverable artifact or trace-adjacent event structure so future UI work can surface it.

## Backtesting Validation

Validation should happen in stages:

### Stage 1: Unit Tests

Add tests for:

- Equity report with exactly five selected symbols becomes five equal-weight target portfolio items.
- Duplicate selected symbols are rejected.
- Selected symbols outside the universe are rejected.
- Single-symbol legacy output is not accepted by the Top 5 QQQ historical strategy path.
- Trailing stop planner returns hold/no-op when no position breaches threshold.
- Trailing stop planner returns sell orders when a position breaches threshold.
- Stop-triggered sell orders use full current position quantity.

### Stage 2: Short Smoke Backtest

Run a one-month or two-month QQQ historical smoke backtest.

Expected:

- Weekly equity agent decisions still run on schedule.
- Weekly target portfolio contains five symbols.
- Execution plans can contain multiple buy/sell orders.
- Daily stop checks run between weekly rebalances.
- No negative cash.
- No crash from mixed scheduled rebalance and stop-check logic.

### Stage 3: Five-Year Comparison Backtest

After smoke test passes, run the same five-year window used for the prior QQQ historical comparison:

```text
2021-08-16 to 2026-08-14
```

Compare:

1. Previous QQQ historical all-in LLM run.
2. New Top 5 equal-weight run without evaluating stop impact separately if stop is included from the start.
3. If practical, a second run with trailing stop disabled to isolate the effect of Top 5 diversification.

Minimum metrics:

- Total return.
- CAGR.
- Max drawdown.
- Sharpe.
- Volatility.
- RoMaD / Calmar-style return-over-drawdown.
- Number of trades.
- Number of stop-triggered exits.
- Cash drag from stopped positions waiting for next rebalance.

## Success Criteria

This feature is successful if:

- The strategy no longer all-ins a single selected stock during scheduled rebalance.
- Scheduled target portfolio contains exactly five equal-weight symbols.
- Daily trailing stop checks run without LLM calls.
- Stop-triggered exits sell the full stopped position and leave proceeds in cash.
- Execution agent still receives deterministic execution plans only.
- Existing QQQ historical universe handling remains compatible.
- Five-year comparison shows materially lower max drawdown than the all-in run, or the result clearly explains why it did not.

The feature is not required to beat SPY or maximize return in the first implementation.

## Open Follow-Up Questions

These are intentionally left for later experiments, not for this feature:

- Whether Top 5 should become Top 10.
- Whether equal weights should become volatility-adjusted weights.
- Whether trailing stop should use highest high instead of highest close.
- Whether stop threshold should be 15%, 20%, 25%, or ATR-based.
- Whether stopped cash should be redeployed immediately into the next-ranked symbol.
- Whether news should be required when a selected Top 5 name has abnormal event risk.
