# DuckDB Query Reduction History Summary Design

## Summary

Reduce unnecessary LLM-written `duckdb_query` calls by changing two things:

1. Remove general prompt language that nudges agents to use DuckDB whenever historical tables exist.
2. Expand the structured statistics returned by history tools so common single-asset and cross-asset comparisons are
   already available without custom SQL.

`duckdb_query` must remain available as an advanced fallback. The goal is not to forbid SQL. The goal is to make the
stable path easy enough that the LLM only writes SQL when it genuinely needs a custom analysis that the built-in
summaries do not provide.

## Problem

The current system now returns `computed_summary` from `market_load_history_table`, and a one-day backtest confirmed
that every history-load result included the new summary. However, the same backtest still showed two `duckdb_query`
calls from `growth_agent`.

Investigation showed these queries were not caused by a total absence of data. The LLM already had per-symbol
statistics such as latest close, 20/60/120-bar returns, moving averages, range position, volatility, and drawdown.

The queries happened because:

- The agent task asked for a universe-wide relative-strength ranking.
- The available summaries were per-symbol, not a single cross-symbol ranking table.
- The LLM wanted 1M/3M/6M-style windows using 20/63/125 bars, while the current summary exposes 20/60/120.
- The general system context still says: `Use DuckDB for time-series analysis when historical tables are available.`

This means the system still accidentally frames SQL as the default professional path for time-series analysis.

## Goals

- Make `computed_summary` the first-choice source for common history statistics.
- Keep `duckdb_query` available for custom or unsupported analysis.
- Remove generic prompt language that recommends DuckDB as the normal path whenever history tables are loaded.
- Expand single-symbol `computed_summary` with additional common windows and ranking-friendly fields.
- Add a batch universe summary/ranking capability so agents can compare a universe without writing SQL.
- Keep all new statistics factual and strategy-neutral.
- Keep calculation logic local, deterministic, testable, and independent of LLM prompts.
- Update replay UI formatting so the new summary/ranking fields are visible in human-readable form.

## Non-Goals

- Do not disable `duckdb_query`.
- Do not remove DuckDB table loading.
- Do not add a large DuckDB SQL guidance prompt.
- Do not encode buy/sell strategy rules in the statistics.
- Do not make a portfolio optimizer.
- Do not replace agent reasoning with deterministic trading decisions.
- Do not add news, macro, SEC, or fundamental-derived scoring in this feature.
- Do not persist DuckDB tables to disk.

## Current Evidence

The latest one-day growth-execution test produced:

```text
market_load_history_table results: 24
with computed_summary: 24
duckdb_query results: 2
duckdb_query errors: 0
```

The two SQL queries both attempted to create a 12-symbol table with:

```text
symbol, last_close, last_date, ret_1m, ret_3m, ret_6m
```

The second query was a cleaner version of the first and used:

```sql
ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY Date DESC)
```

This indicates the LLM wanted a cross-symbol ranking structure, not merely one missing scalar value.

## Architecture

Keep the existing single-symbol path:

```text
market_load_history_table(symbol)
  -> load visible bars
  -> register DuckDB table
  -> compute_history_summary(frame)
  -> return table metadata + computed_summary + available_tables
```

Add a batch summary path:

```text
market_load_history_tables_summary(symbols)
  -> for each symbol:
       load visible bars through the existing DuckDBQueryLayer history-loading path
       compute/enrich single-symbol summary
  -> assemble universe_summary rows
  -> compute rankings from factual metrics
  -> return universe_summary + rankings + loaded_tables + warnings
```

The batch path should reuse the same calculation module as the single-symbol path. It should not duplicate financial
math in a second place.

## Prompt Behavior

Remove or rewrite generic instructions that suggest DuckDB should be used by default.

Replace:

```text
Use DuckDB for time-series analysis when historical tables are available.
```

With:

```text
Use computed summaries from market_load_history_table or market_load_history_tables_summary first. Use duckdb_query
only when the needed comparison or statistic is not already available in tool results.
```

This instruction may appear in general tool-use guidance, but it must stay short. It must not become a long SQL
tutorial.

The `duckdb_query` tool description should remain accurate and should continue to explain how to query safely. It
should not be removed because SQL is still a legitimate fallback.

## Single-Symbol Summary Expansion

Extend `computed_summary["momentum"]` to include commonly used windows:

```json
{
  "return_20": 0.0479,
  "return_21": 0.0451,
  "return_60": -0.0051,
  "return_63": 0.0137,
  "return_120": 0.0462,
  "return_126": 0.0497,
  "return_252": 0.105
}
```

Keep the existing fields for backward compatibility. Add new fields without renaming existing ones.

Extend `computed_summary["trend"]` with ranking-friendly distance fields:

```json
{
  "sma_20": 467.55,
  "sma_50": 473.3,
  "sma_200": 440.69,
  "price_vs_sma_20": -0.0149,
  "price_vs_sma_50": -0.0268,
  "price_vs_sma_200": 0.0452
}
```

These fields mostly exist today. The implementation should verify they remain stable and available for batch summary
rows.

Add a neutral `scores` group only if it is purely mechanical and transparent:

```json
{
  "scores": {
    "momentum_composite": 0.036,
    "trend_alignment": 2
  }
}
```

Definitions:

- `momentum_composite`: average of available `return_21`, `return_63`, and `return_126`.
- `trend_alignment`: count of available positive `price_vs_sma_20`, `price_vs_sma_50`, and `price_vs_sma_200`.

These are not trading recommendations. They are sortable factual summaries.

If the composite cannot be calculated because all inputs are unavailable, return `null` and mark availability false.

## Batch Universe Summary Tool

Add a new built-in market tool:

```text
market_load_history_tables_summary
```

Suggested Python-facing signature:

```python
def load_history_tables_summary(
    *,
    symbols: list[str],
    length: int = 252,
    timestep: str = "day",
    asset_type: str = "stock",
    table_prefix: str | None = None,
    include_after_hours: bool = True,
) -> dict[str, Any]:
```

The tool should return:

```json
{
  "schema_version": "1.0",
  "symbols": ["SPY", "QQQ", "IWM"],
  "timestep": "day",
  "length": 252,
  "as_of": "2024-09-05T09:30:00-04:00",
  "loaded_tables": [
    {
      "symbol": "SPY",
      "table_name": "spy_hist",
      "row_count": 252,
      "columns": ["Date", "open", "high", "low", "close", "volume"]
    }
  ],
  "universe_summary": [
    {
      "symbol": "SPY",
      "latest_close": 550.95,
      "return_21": 0.055,
      "return_63": 0.043,
      "return_126": 0.081,
      "momentum_composite": 0.06,
      "sma_20": 550.59,
      "sma_50": 549.05,
      "sma_200": 512.51,
      "price_vs_sma_20": 0.0007,
      "price_vs_sma_50": 0.0035,
      "price_vs_sma_200": 0.075,
      "trend_alignment": 3,
      "max_drawdown_60": -0.084,
      "volatility_20": 0.011,
      "distance_to_high_252": -0.025
    }
  ],
  "rankings": {
    "by_return_21": ["SPY", "VNQ", "FXI"],
    "by_return_63": ["VNQ", "GLD", "TLT"],
    "by_return_126": ["GLD", "VNQ", "SPY"],
    "by_momentum_composite": ["VNQ", "GLD", "SPY"],
    "by_trend_alignment": ["VNQ", "GLD", "TLT"]
  },
  "warnings": []
}
```

Rankings should include only symbols with available values for the ranked metric. Sort descending for return,
composite, and trend-alignment metrics. For drawdown, no default ranking is required in the first version because
"less drawdown" can mean different things depending on strategy.

## Tool Descriptions

### `market_load_history_table`

Keep the current description short, but make fallback priority explicit:

```text
The result includes computed_summary with standard factual statistics. Read computed_summary first. Use duckdb_query
only when the needed comparison or statistic is not already available in tool results.
```

### `market_load_history_tables_summary`

Model-facing description:

```text
Load visible historical bars for multiple symbols and return a cross-symbol summary and factual rankings. Use this
when comparing a universe of assets by recent returns, moving averages, trend alignment, drawdown, volatility, or
range position. Prefer this tool before writing DuckDB SQL for common universe ranking.
```

This tool should be available to agents that currently have market history and DuckDB analysis access. It is
read-only.

## Replay UI

The replay UI should support both paths:

- Existing `market_load_history_table` formatter should include the expanded single-symbol fields when present.
- New `market_load_history_tables_summary` formatter should show:
  - number of symbols summarized
  - top ranked symbols by `return_63` and `momentum_composite`
  - concise warning count if any symbols failed or lacked enough data

The UI should not compute rankings itself. It should only render the tool payload.

## Error Handling

Batch summary should be partial-success tolerant.

Rules:

- If one symbol fails to load, include a warning and continue with other symbols.
- If a symbol has insufficient rows for a metric, set that metric to `null`.
- If all symbols fail, return an empty `universe_summary`, empty `rankings`, and warnings; do not raise solely because
  the batch has no successful symbols.
- Do not hide underlying per-symbol table metadata when a summary metric is unavailable.
- Do not call external services beyond what the existing history-load path already calls.

## Testing

### Unit Tests

Add or update tests for `history_summary.py`:

- `return_21`, `return_63`, `return_126`, and `return_252` are calculated correctly.
- Existing `return_20`, `return_60`, and `return_120` remain unchanged.
- `momentum_composite` averages available `return_21`, `return_63`, and `return_126`.
- `trend_alignment` counts positive available SMA distance fields.
- Missing or short data marks new fields unavailable without raising.

### Batch Tool Tests

Add tests for the new batch summary tool:

- Multiple symbols return one `universe_summary` row per successful symbol.
- Rankings sort symbols correctly by `return_63` and `momentum_composite`.
- The tool reuses history loading so `loaded_tables` are queryable by `duckdb_query`.
- Partial symbol failures return warnings and preserve successful rows.
- Empty `symbols` is rejected with a clear validation error.

### Prompt/Description Tests

Update existing agent prompt/tool-description tests:

- Global/effective system prompt no longer contains `Use DuckDB for time-series analysis when historical tables are available`.
- The replacement text says to use computed summaries first and `duckdb_query` only when needed.
- `market_load_history_table` description includes the fallback priority.
- `market_load_history_tables_summary` appears in the relevant market tool set.

### Replay UI Tests

Add formatter tests:

- Expanded single-symbol summary renders the new 21/63/126/252-bar fields when present.
- Batch universe summary renders symbol count and top ranking names.
- Batch summary with warnings shows warning count without dumping raw JSON.

## Acceptance Criteria

- Agents are no longer generally prompted to use DuckDB whenever historical tables exist.
- `market_load_history_table` still works and remains backward-compatible.
- Single-symbol `computed_summary` includes 21/63/126/252-bar windows.
- A new batch universe summary tool can produce cross-symbol factual rankings without custom SQL.
- `duckdb_query` remains available and functional.
- Replay UI can explain both single-symbol and batch summary outputs.
- Focused tests for summary math, batch tool behavior, prompt text, and replay formatting pass.

## Out Of Scope For This Spec

If LLMs still write occasional SQL after this feature, that is acceptable. A future spec can consider deeper approaches,
such as:

- A schema-aware SQL builder.
- A query validator and repair loop.
- More advanced risk-adjusted ranking fields.
- Strategy-specific ranking presets.

Those are intentionally deferred until this lighter-weight approach has been tested in real traces.
