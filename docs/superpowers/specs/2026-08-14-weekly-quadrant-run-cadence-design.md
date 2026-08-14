# Weekly Growth / Inflation Quadrant Run Cadence Design

Date: 2026-08-14

## 1. Purpose

This spec defines a focused change to run the Growth / Inflation quadrant
strategy on a weekly review cadence instead of running the full agent workflow
on every trading day.

The immediate goal is:

```text
Backtests should evaluate the full macro -> basket -> portfolio -> execution
workflow once per week, not once per trading day.
```

This is a cadence and cost-control change. It should not redesign the macro
classifier, basket universes, portfolio planner, or execution tools.

## 2. Background

The current mock Growth / Inflation quadrant workflow lives in:

```text
lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py
```

The current strategy initializes:

```python
self.sleeptime = "1D"
```

That means Lumibot triggers `on_trading_iteration()` once per trading day in
daily backtests. Each full run can call:

```text
macro_allocation_agent
  -> macro_regime_classifier
  -> equity_basket_agent
  -> commodity_basket_agent
  -> tips_basket_agent
  -> nominal_bond_basket_agent
  -> portfolio_decision_agent
  -> target_portfolio_to_execution_plan
  -> execution_agent
  -> execution_plan_execute
```

This was useful while proving the workflow. It is less appropriate for the
strategy's intended macro allocation behavior because the core regime data does
not update daily.

Official macro release cadence supports this:

- Real GDP is quarterly and released by BEA in advance, second, and third
  estimates. BEA release schedule: https://www.bea.gov/news/schedule
- CPI is monthly and released by BLS, generally at 8:30 AM Eastern on scheduled
  release dates. BLS CPI schedule:
  https://www.bls.gov/schedule/news_release/cpi.htm
- PCE price index is monthly in BEA Personal Income and Outlays. BEA PCE page:
  https://www.bea.gov/data/personal-consumption-expenditures-price-index

Running the full agent stack every trading day often means repeatedly spending
LLM calls on the same macro information. Weekly cadence is a better first
operational default for this strategy stage.

## 3. Current Framework Constraint

Lumibot `sleeptime` currently supports second, minute, hour, and day units. The
existing executor path does not support a native `1W` unit.

Therefore this feature should not attempt to add weekly scheduling to Lumibot's
global scheduler.

Preferred design:

```text
Keep self.sleeptime = "1D"
Add a strategy-level cadence gate at the start of on_trading_iteration()
Only run the full agent workflow on the selected weekly trading day
Return early on non-run trading days
```

This preserves the existing backtest engine behavior and keeps the change local
to the Growth / Inflation quadrant strategy.

## 4. Goals

1. Add weekly run cadence support to the mock Growth / Inflation quadrant
   strategy.
2. Keep daily cadence available for debugging and comparison.
3. Make weekly cadence the intended default for this strategy's normal
   benchmark/backtest use.
4. Ensure non-run trading days do not call any LLM agents or trading tools.
5. Support a simple weekly policy:
   - preferred weekday: Monday by default;
   - if the preferred weekday is not observed in the backtest week, run on the
     first observed trading day after the preferred weekday.
6. Record why a trading day was skipped.
7. Keep trace and replay behavior sane: full agent traces should exist only on
   actual workflow run days.
8. Keep one-day and short-window tests possible by allowing cadence override.
9. Adjust prompts only where cadence language matters.
10. Preserve all current order safety and execution-plan validation behavior.

## 5. Non-Goals

This feature does not:

1. Change the macro regime classifier formula.
2. Change the 50/25/25/0 basket weight map.
3. Change any basket universe.
4. Change basket ranking metrics, news policy, or history-summary behavior.
5. Add stop-loss, take-profit, bracket, OCO, or protective exit orders.
6. Add intraday monitoring between weekly reviews.
7. Add release-date-triggered macro scheduling.
8. Add monthly, quarterly, or event-driven cadence.
9. Add a live broker deployment scheduler.
10. Add persistent restart-proof live cadence state.
11. Change account curve, tearsheet, or replay UI artifact generation.
12. Modify Lumibot's global `sleeptime` parser to support `1W`.

These may become future features, but they should not be bundled into this
first weekly cadence change.

## 6. Cadence Parameters

The strategy should expose explicit cadence parameters.

Suggested parameter shape:

```python
parameters = {
    "run_frequency": "weekly",
    "weekly_run_weekday": "MON",
    "weekly_holiday_policy": "first_open_trading_day",
    ...
}
```

Allowed `run_frequency` values:

| Value | Behavior |
|---|---|
| `daily` | Run the full workflow on every trading iteration. This preserves current behavior for debugging. |
| `weekly` | Run the full workflow only once per observed trading week. |

Allowed `weekly_run_weekday` values:

```text
MON, TUE, WED, THU, FRI
```

Initial default:

```text
MON
```

Allowed `weekly_holiday_policy` values:

```text
first_open_trading_day
```

Only this policy is required in v1.

Optional environment overrides may be added if they match existing project
patterns, for example:

```text
AI_TRADING_TEAM_RUN_FREQUENCY
AI_TRADING_TEAM_WEEKLY_RUN_WEEKDAY
```

The implementation plan should decide whether CLI, environment, or parameters
are the smallest safe path for benchmark control.

## 7. Weekly Run Selection Semantics

The cadence gate should reason in trading dates, not calendar timestamps.

For each `on_trading_iteration()`:

```text
current_date = strategy.get_datetime().date()
week_key = ISO year + ISO week number
```

Then:

1. If `run_frequency == "daily"`, run the full workflow.
2. If `run_frequency == "weekly"`:
   - skip if the current `week_key` already has a workflow attempt;
   - run if the current observed weekday is the preferred weekday;
   - also run if the current observed weekday is later than the preferred
     weekday and no earlier preferred weekday iteration was observed in the
     current strategy process.
3. After the strategy attempts the full workflow, record the current `week_key`
   as attempted.

The "preferred weekday or next observed trading day" rule handles:

- Monday market holidays;
- backtest windows that start mid-week;
- short validation windows.

Example:

```text
Backtest dates: Tue, Wed, Thu, Fri
weekly_run_weekday: MON
No Monday iteration exists in this backtest window
Tue runs the weekly workflow because it is the first observed trading day after
the preferred weekday
Wed/Thu/Fri skip
```

This is intentionally scoped to observed strategy iterations. It does not need
to query a separate exchange calendar in v1.

If the workflow attempt is blocked after the cadence gate allows it, v1 should
still mark that week as attempted. This avoids repeatedly spending LLM calls
later in the same week after a blocked weekly review. A later feature can add
retry policy if needed.

## 8. Skip Behavior

On non-run days, the strategy should return before any agent is called.

It should not call:

```text
macro_allocation_agent
basket agents
portfolio_decision_agent
execution_agent
macro_regime_classifier
market_load_history_tables_summary
target_portfolio_to_execution_plan
execution_plan_execute
```

The strategy should record a concise skip event in a place that is easy to test
and inspect.

Suggested internal shape:

```json
{
  "date": "2024-09-06",
  "run_frequency": "weekly",
  "weekly_run_weekday": "MON",
  "week_key": "2024-W36",
  "status": "skipped",
  "reason": "not_weekly_run_day",
  "last_weekly_run_date": "2024-09-05"
}
```

The implementation may store this in an in-memory list such as
`self._weekly_cadence_events` and may also print/log a compact message.

Full agent trace files should not be created for skipped days, because no agent
actually ran.

## 9. Backtest Expectations

For a multi-week backtest:

```text
number of full workflow runs ~= number of observed trading weeks
```

Examples:

| Backtest Window | Expected Weekly Workflow Runs |
|---|---:|
| 2024-09-05 to 2024-09-06 | 1 |
| 2024-09-05 to 2024-09-13 | 2 |
| 2024-09-05 to 2024-10-07 | about 5 |

The exact count depends on Lumibot's start/end date behavior and available
market data, so tests should assert against controlled fake dates where
possible.

For one-day benchmark validation, the strategy must still be runnable. That can
be achieved by:

- using `run_frequency = daily`; or
- relying on the first-observed-trading-day rule.

## 10. Prompt Requirements

Prompt changes should be small, role-specific, and should not add a long
explanation of economic data release schedules.

The prompts should avoid saying or implying:

```text
daily rebalance
today's macro data changed
trade every day
refresh the whole portfolio every session
```

Suggested replacements:

```text
scheduled allocation review
current scheduled review date
weekly review
current review period
```

### Macro Allocation Agent

The macro allocation prompt should say that this is the current scheduled
macro allocation review.

It should not instruct the LLM to decide whether today is a run day. The code
owns cadence gating.

### Basket Agents

Basket prompts should say that the agent selects the best representative symbol
for the basket for the current scheduled review.

They should not describe the task as a daily trade or daily rebalance.

### Portfolio Decision Agent

The portfolio prompt should remain deterministic-tool-oriented:

```text
merge macro allocation and basket reports into target_portfolio
call target_portfolio_to_execution_plan
copy the planner execution_plan exactly
```

It may refer to the current scheduled review, but should not manually reason
about weekly timing.

### Execution Agent

Execution prompt changes should be minimal or unnecessary.

The execution agent still receives an execution plan and calls
`execution_plan_execute` exactly once. Cadence is not its job.

## 11. Tool Description Requirements

No new tool is required for v1.

Existing tool descriptions should not need large changes. If any model-facing
tool description currently says "daily" in a way that conflicts with weekly
cadence, that wording should be changed to "current review" or "current
strategy iteration".

Do not encode the weekly scheduling policy inside tool descriptions unless the
tool actually makes a scheduling decision.

## 12. Trace And Replay UI Expectations

The existing replay UI should continue to show full agent workflows for days
where the workflow actually ran.

Skipped trading days are not expected to have agent workflow graphs because no
agent calls happen.

This feature should not require a new UI panel.

However, future UI enhancement may show a benchmark-level run calendar with:

```text
ran / skipped
skip reason
weekly run date
```

That enhancement is out of scope for v1.

## 13. Testing Requirements

Unit tests should verify cadence behavior without network or LLM calls.

Recommended tests:

1. Strategy initializes with:

```text
self.sleeptime == "1D"
run_frequency == "weekly"
weekly_run_weekday == "MON"
```

2. Weekly cadence helper returns run on Monday.
3. Weekly cadence helper returns skip after the week already ran.
4. Weekly cadence helper runs on the first observed trading day if Monday was
   not observed.
5. `run_frequency = daily` runs every observed trading day.
6. Non-run days return before any agent manager calls.
7. A short multi-day fake workflow calls agents only on expected weekly dates.
8. Prompt tests verify no role prompt contains stale "daily rebalance" wording.

Benchmark validation:

1. Run a short one-day benchmark to prove the workflow still works.
2. Run a multi-day or multi-week benchmark to prove trace count drops relative
   to daily cadence.
3. Verify skipped dates do not create full agent trace directories.
4. Verify actual run dates still produce normal:
   - workflow traces;
   - account curve artifact;
   - performance report artifact when returns are non-degenerate.

## 14. Acceptance Criteria

This feature is complete when:

1. The mock Growth / Inflation quadrant strategy supports `run_frequency`.
2. Weekly cadence is the intended default for normal strategy use.
3. Daily cadence remains available for debugging.
4. Weekly cadence runs the full workflow at most once per observed ISO week.
5. The first observed trading day of a week runs when the preferred weekday is
   absent from the observed backtest window and the current observed weekday is
   after the preferred weekday.
6. Skipped days do not call LLM agents or trading tools.
7. Skipped days record a concise skip reason.
8. Prompts no longer imply daily rebalance where weekly review is intended.
9. Existing execution safety tests still pass.
10. A one-day benchmark still works.
11. A multi-day weekly-cadence benchmark shows fewer agent workflow traces than
    daily cadence would produce.

## 15. Risks And Mitigations

### Risk: weekly gating hides useful short-window tests

Mitigation: keep `run_frequency = daily` as an explicit override and make
first-observed-trading-day behavior run in one-day windows.

### Risk: Monday holiday handling becomes calendar-heavy

Mitigation: v1 should use observed trading iterations instead of a separate
calendar query. If Monday never appears in the backtest week, the first observed
trading day runs.

### Risk: live restart duplicates a weekly run

Mitigation: explicitly mark restart-proof live cadence state as a non-goal for
v1. If needed later, add durable state after the weekly backtest behavior is
validated.

### Risk: prompts make the model overthink cadence

Mitigation: code owns cadence. Prompts should only use neutral "scheduled
review" wording.

### Risk: benchmark artifacts look sparse

Mitigation: sparse weekly traces are expected. Validation should count workflow
run dates and skip events, not expect one trace per trading day.

## 16. Implementation Notes For The Future Plan

The implementation plan should consider introducing a small helper with a
clear test surface, for example:

```text
should_run_scheduled_workflow(date, state, run_frequency, weekly_run_weekday)
```

The helper should be deterministic and independent from LLMs.

The strategy can then use it at the top of `on_trading_iteration()`:

```text
if not should_run:
    record skip event
    return

run existing full workflow unchanged
```

This keeps the weekly feature as a narrow wrapper around the current proven
workflow rather than a rewrite of the workflow itself.
