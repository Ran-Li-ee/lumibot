# QQQ Historical Equity-Only LLM Strategy Design

## Purpose

Add a new equity-only LLM strategy variant that uses point-in-time QQQ
historical constituent snapshots as its weekly stock universe.

The goal is to compare the existing fixed-50-stock equity-only LLM strategy
against a more realistic, historically changing QQQ constituent universe.
This feature should not replace the existing fixed-universe strategy. It should
create a new strategy so both can be benchmarked side by side.

## Background

The current clean equity-only strategy uses a fixed 50-stock US equity universe.
That strategy is valuable because it already has benchmark results and provides
a stable baseline for future optimization.

Recent work added a QQQ N-PORT universe data layer that can answer:

```text
Given a backtest date, what QQQ holdings universe was available for that date?
```

The collected data currently covers 27 quarterly QQQ snapshots:

```text
report_date range: 2019-09-30 to 2026-03-31
filing_date range: 2019-11-29 to 2026-05-28
```

The new strategy should use this data layer to build a dynamic equity universe.
Although the QQQ holdings snapshots change quarterly, the strategy should still
run weekly because price rankings, trend evidence, relative strength, and news
change more frequently than QQQ holdings disclosures.

## Goals

1. Add a new strategy class:

   ```text
   AITradingTeamQQQHistoricalEquityOnlyLLMStrategy
   ```

2. Keep the existing fixed-50-stock strategy unchanged.
3. Keep the new QQQ historical strategy equity-only.
4. Run the new strategy weekly by default.
5. Resolve the QQQ constituent universe at each scheduled run date using the
   existing QQQ N-PORT resolver.
6. Use `strict` as the default as-of mode to avoid look-ahead bias.
7. Pass the resolved QQQ symbols to the existing equity selection agent flow.
8. Preserve the existing downstream deterministic portfolio planning and
   execution flow.
9. Record QQQ snapshot metadata in agent context and trace so the replay UI can
   explain which holdings snapshot was used.
10. Register a benchmark runner key for the new strategy.
11. Add tests that confirm the new strategy uses dynamic QQQ symbols without
    changing the fixed-50 baseline.

## Non-Goals

This feature does not:

1. Delete or replace `AITradingTeamEquityOnlyLLMStrategy`.
2. Optimize the trading strategy's performance.
3. Add SPY, S&P 500, or all-market historical universe support.
4. Backfill QQQ data before the available N-PORT window.
5. Use today's QQQ holdings to simulate earlier dates.
6. Add a new LLM tool for universe discovery.
7. Change the execution agent, order tools, or target-portfolio planner.
8. Change the news tool behavior beyond prompt wording for this strategy.
9. Commit large downloaded SEC raw or normalized data files.
10. Make claims that the QQQ historical strategy will outperform the fixed
    universe, QQQ, SPY, or any other benchmark.

## Strategy Shape

The new strategy should be a narrow variant of the existing equity-only
strategy:

```text
AITradingTeamGrowthExecutionTestStrategy
  -> AITradingTeamEquityOnlyLLMStrategy
      -> AITradingTeamQQQHistoricalEquityOnlyLLMStrategy
```

This keeps the proven workflow intact:

```text
weekly scheduled run
  -> equity_basket_agent selects one stock
  -> deterministic target_portfolio_to_execution_plan builds execution_plan
  -> execution_agent calls execution_plan_execute
```

Only the equity universe source changes:

```text
fixed 50-stock list
  becomes
QQQ N-PORT as-of snapshot symbols
```

## Run Frequency

The new strategy must default to weekly:

```python
"run_frequency": "weekly"
"weekly_run_weekday": "MON"
"weekly_holiday_policy": "first_open_trading_day"
```

The strategy may keep the same scheduling helper used by
`AITradingTeamEquityOnlyLLMStrategy`.

Weekly is intentional:

1. QQQ holdings snapshots are quarterly.
2. Price/statistical ranks are recalculated from current historical market data.
3. News and momentum evidence can change weekly.
4. Existing fixed-50 benchmark results are weekly, so this keeps comparisons
   easier.

## QQQ Universe Resolution

Use the existing resolver:

```python
resolve_qqq_snapshot(as_of_date, mode="strict", data_dir=None)
```

Default mode:

```text
strict
```

Strict mode means:

```text
choose the latest snapshot where filing_date <= current backtest date
```

This avoids using a QQQ holdings report before it was publicly filed.

Prototype mode may remain configurable for research, but the new strategy
should default to strict.

## Parameters

Add strategy parameters such as:

```python
parameters = {
    "run_frequency": "weekly",
    "weekly_run_weekday": "MON",
    "weekly_holiday_policy": "first_open_trading_day",
    "qqq_universe_mode": "strict",
    "qqq_universe_data_dir": None,
}
```

`qqq_universe_mode` allowed values:

```text
strict
prototype
```

`qqq_universe_data_dir` is optional and should default to the existing LumiBot
cache folder used by `lumibot.tools.universe.qqq_nport`.

## Missing Snapshot Behavior

If no QQQ snapshot is available for a scheduled run date, the strategy should
not fall back to today's QQQ holdings or the fixed 50-stock list.

It should:

1. Record a blocked/skipped workflow event.
2. Log a clear message.
3. Avoid running the equity agent.
4. Avoid placing trades.

Example reason:

```text
qqq_historical_universe_unavailable
```

This behavior is important for early backtest dates before the first available
N-PORT filing.

## Symbol Filtering

The QQQ resolver returns symbols extracted from normalized snapshots.
The strategy should normalize symbols by:

1. stripping whitespace;
2. uppercasing;
3. removing empty values;
4. removing duplicates while preserving first-seen order.

The strategy should not silently add symbols from outside the resolved snapshot.

If later market data loading fails for some symbols, that failure should be
handled by the existing market summary/ranking tool and recorded as warnings.
The strategy should not preemptively remove symbols unless they are empty or
duplicate.

## Agent Context

The equity agent should receive:

```json
{
  "date": "YYYY-MM-DD",
  "basket_id": "equity",
  "basket_symbols": ["..."],
  "target_weight": 1.0,
  "universe_source": {
    "type": "qqq_nport",
    "mode": "strict",
    "as_of_date": "YYYY-MM-DD",
    "selected_report_date": "YYYY-MM-DD",
    "selected_filing_date": "YYYY-MM-DD",
    "accession_number": "...",
    "holding_count": 96,
    "snapshot_path": "...",
    "source_url": "..."
  }
}
```

The `basket_symbols` field remains the operative universe. `universe_source`
is explanatory metadata.

## Prompt Adjustments

The strategy should reuse the existing equity-only agent structure but adjust
the equity prompt for a dynamic QQQ historical universe.

The prompt should say:

1. The agent is selecting from the provided `basket_symbols`.
2. `basket_symbols` represent the QQQ historical constituent universe available
   for the current backtest date.
3. The agent must use only symbols in `basket_symbols`.
4. The agent should use market summary/rank evidence first.
5. The agent should use news only when leading candidates are close,
   conflicting, or uncertain.
6. The agent should not add symbols outside the provided universe.
7. The agent should not assume QQQ membership itself means the safest or best
   choice; it still needs current rank evidence.

The prompt should avoid:

1. saying QQQ constituents are automatically high quality;
2. saying large-cap technology should always be preferred;
3. adding sector/style/safety labels;
4. implying the agent should buy QQQ itself;
5. implying the agent should choose the largest index weight.

## Workflow Trace and Replay Expectations

The trace should allow a developer to answer:

```text
On this backtest date, which QQQ snapshot was used?
Was strict or prototype mode used?
How many symbols were sent to the equity agent?
Which selected symbol did the equity agent choose?
Was the selected symbol in the snapshot universe?
```

At minimum, this should be visible through agent context in the existing replay
UI. A separate UI feature is not required in this spec.

## Benchmark Registration

Add a new runner key:

```text
qqq-historical-equity-only-llm
```

It should map to:

```text
AITradingTeamQQQHistoricalEquityOnlyLLMStrategy
```

The existing key must remain unchanged:

```text
equity-only-llm
```

This enables side-by-side benchmark commands such as:

```text
python scripts\run_ai_trading_team_examples_benchmark.py --strategy equity-only-llm --strategy qqq-historical-equity-only-llm ...
```

## Data Requirements

The new strategy assumes QQQ N-PORT snapshots have already been collected into
the local cache.

The implementation should not automatically download SEC filings during a
backtest. Backtests should be deterministic and should fail/skip clearly if the
local snapshot data is missing.

The collection command remains separate:

```text
python scripts\collect_qqq_nport_universe.py --start-date 2019-10-01 --end-date 2026-08-21 --mode strict --refresh --write-report
```

## Validation Plan

### Unit Tests

Add focused tests that verify:

1. the new strategy resolves QQQ symbols for a date with a local snapshot;
2. strict mode chooses based on `filing_date`;
3. prototype mode chooses based on `report_date` if configured;
4. missing snapshots block the workflow instead of falling back;
5. `basket_symbols` passed to the equity agent equal the resolved QQQ symbols;
6. selected symbols must still be in the resolved QQQ universe;
7. fixed-50 `AITradingTeamEquityOnlyLLMStrategy` parameters remain unchanged;
8. the benchmark runner includes the new key without removing the old key.

### Smoke Backtest

Run a short one-day or one-week smoke backtest after implementation.

Expected evidence:

1. strategy starts;
2. scheduled weekly logic triggers at most once per week;
3. equity agent context contains `universe_source`;
4. `basket_symbols` count is plausible for QQQ, roughly 89-99 in the collected
   data window;
5. no future snapshot is used in strict mode;
6. execution either completes or blocks for normal strategy reasons, not
   because the QQQ universe failed.

### Benchmark Comparison

After smoke tests, run the same historical window used by the existing
fixed-50 baseline:

```text
fixed-50 equity-only LLM
QQQ historical equity-only LLM
QQQ buy-and-hold
SPY buy-and-hold
```

The benchmark comparison is not part of implementation correctness, but it is
the reason for adding this strategy.

## Acceptance Criteria

1. A new QQQ historical equity-only LLM strategy exists.
2. The original fixed-50 equity-only strategy is still available and unchanged.
3. The new strategy defaults to weekly cadence.
4. The new strategy uses `resolve_qqq_snapshot` at each scheduled workflow run.
5. The new strategy defaults to strict point-in-time behavior.
6. Missing QQQ snapshots block/skip cleanly without fallback to today's
   holdings or fixed symbols.
7. Equity agent context includes QQQ snapshot metadata.
8. The selected symbol is validated against the resolved QQQ universe.
9. Benchmark runner supports `qqq-historical-equity-only-llm`.
10. Focused tests and smoke validation pass.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Look-ahead bias from using report dates too early | Default to strict mode using filing dates. |
| No local QQQ data for early dates | Block/skip clearly; do not fallback. |
| Market data missing for old/delisted symbols | Let ranking/history tools report failures and continue with successful symbols. |
| Token use rises from 90-100 symbols | Existing summary tool sends ranked summaries rather than raw history rows. |
| Prompt pushes the model toward largest QQQ weights | Prompt explicitly says not to select based on index weight alone. |
| Existing fixed-50 baseline changes accidentally | Add tests that assert old strategy parameters remain unchanged. |
| Backtest tries to download SEC data live | Keep collection separate from backtesting. |

## Future Work

1. Compare fixed-50 and QQQ-historical strategy performance over the same
   window.
2. Add SPY/S&P 500 historical universe support.
3. Add a generic historical-universe provider interface.
4. Add a replay UI panel for universe-source metadata.
5. Investigate pre-N-PORT QQQ history if longer backtests are needed.
6. Add optional survivorship-bias diagnostics to benchmark reports.

