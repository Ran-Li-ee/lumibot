# History Summary-Only Tool Results Design

## Purpose

Make the historical market-data tools easier and cheaper for LLM agents to use by sending compact computed summaries instead of full raw historical price rows.

The raw OHLCV data should still be loaded into in-memory DuckDB so agents can run targeted follow-up SQL when needed. The default model-facing payload should focus on metadata, statistics, rankings, availability, and warnings.

This feature is about tool evidence quality and token efficiency. It does not change trading strategy rules, portfolio construction, broker behavior, or risk policy.

## Current Behavior

`market_load_history_table` loads visible historical bars for one symbol into DuckDB and returns table metadata plus `computed_summary`.

`market_load_history_tables_summary` loads multiple symbols and returns cross-symbol summary rows and rankings.

The existing summary already includes:

- Data window: `row_count`, `start`, `end`
- Price: `latest_close`
- Momentum: `return_20`, `return_21`, `return_60`, `return_63`, `return_120`, `return_126`, `return_252`
- Trend: `sma_20`, `sma_50`, `sma_200`
- Relative trend: `price_vs_sma_20`, `price_vs_sma_50`, `price_vs_sma_200`
- Scores: `momentum_composite`, `trend_alignment`
- Range: `high_252`, `low_252`, `distance_to_high_252`, `distance_to_low_252`
- Risk: `max_drawdown_60`, `volatility_20`
- `availability`, `warnings`, and `notes`

The multi-symbol summary already includes rankings:

- `by_return_21`
- `by_return_63`
- `by_return_126`
- `by_momentum_composite`
- `by_trend_alignment`

## Desired Behavior

### Model-Facing Result Shape

`market_load_history_table` should return a compact payload suitable for direct LLM consumption:

- `table_name`
- `columns`
- `row_count`
- `symbol`
- `asset_type`
- `timestep`
- `loaded_at`
- `data_window`
- `computed_summary`
- `available_tables`

It should not include full raw OHLCV rows by default.

The DuckDB table should still contain the raw rows in memory. Agents may still call `duckdb_query` for targeted follow-up analysis if a required statistic is not already covered by the summary.

### Additional Single-Symbol Statistics

Extend `computed_summary` with a small set of high-value metrics:

#### Short-Term Momentum

- `return_5`
- `return_10`

Reason: lets agents see very recent acceleration or weakness without reading raw rows.

#### Volume

- `latest_volume`
- `avg_volume_20`
- `volume_vs_avg_20`

Reason: gives basic liquidity and activity context.

#### Drawdown From Recent Highs

- `drawdown_from_high_20`
- `drawdown_from_high_60`
- `drawdown_from_high_252`

Reason: helps distinguish recent pullbacks from longer-range position near highs.

#### Risk-Adjusted Momentum

- `return_63_over_volatility_20`
- `return_126_over_volatility_20`

Reason: avoids treating high-return, high-volatility assets as automatically better than steadier leaders.

#### Composite Score

- `composite_score`

Reason: provides a compact, factual cross-metric score that agents can use as an input to ranking. This score is advisory evidence, not a mandatory trading rule.

### Additional Cross-Symbol Rankings

Extend `market_load_history_tables_summary.rankings` with:

- `by_composite_score`

The summary rows should also expose any new fields needed to explain this ranking.

## Composite Score Guidance

The score should be simple, deterministic, and explainable.

It should use already-computed summary statistics such as:

- Medium-term momentum, for example `return_63` and `return_126`
- Trend alignment
- Drawdown from recent highs
- Risk-adjusted momentum

The implementation should avoid turning `composite_score` into a hidden strategy. It should be a factual evidence score for relative comparison, not an instruction to trade.

If an input metric is unavailable, the score should use available components and record availability clearly. If too few components are available, `composite_score` should be `null`.

## Prompt Updates

Update the base/tool guidance lightly so agents understand the new default behavior.

The prompt should communicate:

- Use computed summaries from `market_load_history_table` and `market_load_history_tables_summary` as the primary evidence for price history.
- Do not request raw historical rows by default.
- Use `duckdb_query` only for targeted follow-up analysis not already covered by `computed_summary` or rankings.

Avoid adding a long DuckDB SQL tutorial. This feature should reduce dependence on prompt patches.

## Out Of Scope

This feature will not:

- Change trading strategy prompts beyond the minimum summary-first guidance.
- Change order execution rules.
- Change cash-buffer or no-negative-cash behavior.
- Add new agent roles.
- Persist DuckDB tables to disk.
- Disable `duckdb_query`.
- Add complex technical indicators such as RSI, MACD, Bollinger Bands, or custom chart patterns. Existing indicator tools remain responsible for that category.

## Validation Plan

### Unit Tests

Add or update tests for:

- `compute_history_summary` includes the new statistics when sufficient data exists.
- New metrics are `None` or marked unavailable when data is insufficient.
- `build_universe_history_summary` includes `by_composite_score`.
- Summary rows expose the fields required to explain composite ranking.
- Tool descriptions mention summary-first behavior and targeted DuckDB follow-up.

### Integration / Trace Validation

Run a one-day `gpt-5.4-mini` backtest for `growth-execution-test`.

Validate from the generated trace:

- `market_load_history_table` model-facing output contains the new statistics.
- The model-facing output does not include full raw historical rows.
- `market_load_history_tables_summary` includes `by_composite_score`.
- The workflow still completes through growth, decision, and execution agents.
- The decision agent emits valid structured JSON.
- The execution agent either submits the expected order or gives a traceable no-trade explanation.
- `duckdb_query` is not called unless the summary lacks a specific statistic the agent asks for.

### UI Validation

Open the Agent Workflow Replay UI against the generated artifact and confirm:

- Tool call details remain readable.
- Human-readable tool explanations summarize the new statistics.
- The absence of raw rows does not make the tool output confusing.

## Acceptance Criteria

The feature is complete when:

- Historical raw rows remain queryable in DuckDB RAM.
- Default historical tool output sent to LLMs is summary-first and does not include full raw OHLCV rows.
- The added statistics and `by_composite_score` ranking are visible in trace output.
- Existing tests pass.
- A one-day `gpt-5.4-mini` backtest runs successfully and produces usable agent trace output.
