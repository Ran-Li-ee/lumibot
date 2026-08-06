# History Load Computed Summary Design

## Summary

Enhance the existing `market_load_history_table` tool so that every successful history load returns a structured
`computed_summary` alongside the existing DuckDB table metadata.

The goal is to reduce unnecessary LLM-written DuckDB SQL for common price-history analysis. The LLM should receive
standard, stable statistics such as recent returns, moving averages, range position, volatility, and drawdown directly
from the history-load tool. If these statistics are insufficient for a specific investigation, the LLM can still use
`duckdb_query` as an advanced fallback.

## Problem

The current workflow asks the LLM to load historical bars into DuckDB and then often write SQL to compute basic
statistics. This creates two separate risks:

- SQL syntax or schema mistakes, such as using a column that does not exist.
- Semantic mistakes, where SQL runs successfully but calculates a different metric from what the LLM intended.

A recent example showed the second risk: a query labeled `ret_1m` calculated `MAX(close) / MIN(close in window) - 1`,
which is closer to a window high/low range than a normal one-month return.

## Goals

- Add factual, standardized history statistics to the `market_load_history_table` return payload.
- Keep the existing tool name and existing metadata fields stable.
- Keep `duckdb_query` available for deeper custom analysis.
- Make the statistics easy for the LLM to consume and easy for the replay UI to explain.
- Keep the calculation logic isolated and unit-testable.

## Non-Goals

- Do not remove or disable `duckdb_query`.
- Do not add a new LLM-facing tool in the first version.
- Do not build a multi-asset ranking tool.
- Do not encode a trading strategy or generate buy/sell advice.
- Do not add subjective labels such as `trend_is_strong`.
- Do not change the agent workflow.
- Do not persist DuckDB tables to disk.

## Architecture

Add a dedicated statistics module:

```text
lumibot/components/agents/history_summary.py
```

This module owns the calculation logic. `DuckDBQueryLayer.load_history_table()` remains responsible for loading bars,
registering DuckDB tables, and returning metadata. After it has a visible historical DataFrame, it calls the summary
module and attaches the result to the tool response.

```text
market_load_history_table()
  -> DuckDBQueryLayer.load_history_table()
     -> fetch/normalize visible historical bars
     -> register DuckDB table or visible view
     -> compute_history_summary(...)
     -> return table metadata + computed_summary + available_tables
```

## Public Payload

Existing fields must remain available:

```json
{
  "table_name": "qqq_hist",
  "row_count": 260,
  "columns": ["Date", "open", "high", "low", "close", "volume"],
  "symbol": "QQQ",
  "asset_type": "stock",
  "timestep": "day",
  "loaded_at": "2024-09-05T09:30:00-04:00",
  "available_tables": []
}
```

Add this field:

```json
{
  "computed_summary": {
    "schema_version": "1.0",
    "symbol": "QQQ",
    "timestep": "day",
    "as_of": "2024-09-05T09:30:00-04:00",
    "data_window": {
      "start": "2023-09-01",
      "end": "2024-09-05",
      "row_count": 260
    },
    "price": {
      "latest_close": 458.67
    },
    "momentum": {
      "return_20": 0.0479,
      "return_60": -0.0051,
      "return_120": 0.0462
    },
    "trend": {
      "sma_20": 463.1,
      "sma_50": 469.4,
      "sma_200": 421.8,
      "price_vs_sma_20": -0.0096,
      "price_vs_sma_50": -0.023,
      "price_vs_sma_200": 0.087
    },
    "range": {
      "high_252": 500.0,
      "low_252": 342.0,
      "distance_to_high_252": -0.083,
      "distance_to_low_252": 0.341
    },
    "risk": {
      "volatility_20": 0.22,
      "max_drawdown_60": -0.12
    },
    "availability": {
      "return_20": true,
      "return_60": true,
      "return_120": true,
      "sma_200": true,
      "high_252": true,
      "volatility_20": true,
      "max_drawdown_60": true
    }
  }
}
```

## Calculation Rules

All calculations use only the visible DataFrame returned by the current LumiBot runtime datetime.

### Price

- `latest_close`: latest non-null `close`.

### Momentum

- `return_20`: `latest_close / close_20_bars_ago - 1`.
- `return_60`: `latest_close / close_60_bars_ago - 1`.
- `return_120`: `latest_close / close_120_bars_ago - 1`.

If there are not enough rows, return `null` and mark the metric unavailable.

### Trend

- `sma_20`, `sma_50`, `sma_200`: mean of the last N non-null closes.
- `price_vs_sma_N`: `latest_close / sma_N - 1`.

If there are not enough rows or the SMA is zero/null, return `null`.

### Range

- `high_252`: highest `high` over the last 252 rows if `high` exists; otherwise highest `close`.
- `low_252`: lowest `low` over the last 252 rows if `low` exists; otherwise lowest `close`.
- `distance_to_high_252`: `latest_close / high_252 - 1`.
- `distance_to_low_252`: `latest_close / low_252 - 1`.

If fewer than 252 rows exist, compute over available rows and mark availability according to the actual row count.

### Risk

- `volatility_20`: annualized standard deviation of the last 20 close-to-close returns for daily data.
- `max_drawdown_60`: worst peak-to-trough drawdown over the last 60 closes.

For non-daily timesteps, `volatility_20` should still be computed over 20 bars but should include a metadata note that
annualization is only standardized for daily bars in the first version.

## Error Handling

The history-load tool must not fail only because `computed_summary` cannot be fully calculated.

Rules:

- Missing `close`: return `computed_summary` with all price-dependent fields `null` and include a warning.
- Empty frame: return `computed_summary` with `row_count: 0`, unavailable metrics, and no exception.
- Non-numeric prices: coerce to numeric where possible; unavailable metrics remain `null`.
- Calculation errors: capture a concise warning inside `computed_summary["warnings"]` and still return table metadata.

## Tool Description Update

Update the `market_load_history_table` model-facing description to mention that the result includes `computed_summary`
with standard price-history statistics. Keep the description short. It should not become a long prompt.

Suggested wording:

```text
The result includes computed_summary with standard factual statistics such as recent returns, moving averages,
range position, volatility, and drawdown. Use those fields before writing custom DuckDB SQL for basic history analysis.
```

Do not add a new global system prompt for this feature.

## Replay UI

The trace already records tool outputs, so `computed_summary` will be visible in raw tool output automatically.

Add a small formatter enhancement for `market_load_history_table`:

- Include the number of loaded rows and table name as today.
- If `computed_summary` exists, add a short human-readable sentence with:
  - latest close
  - 20/60/120 bar returns when available
  - price vs 20/50/200 SMA when available
  - max drawdown when available

The UI formatter must not compute financial metrics itself. It only renders fields already returned by the tool.

## Testing

### Unit Tests

Add focused tests for `history_summary.py`:

- Correct `latest_close`, `return_20`, `return_60`, and `return_120` on a deterministic DataFrame.
- Correct `sma_20`, `sma_50`, and `price_vs_sma_N`.
- Correct `high_252`, `low_252`, and distance-to-range metrics.
- Correct `max_drawdown_60` on a simple peak-to-trough sequence.
- Data-short cases return `null` for unavailable metrics without raising.
- Missing `close` returns warnings and unavailable price-dependent metrics.

### Integration Tests

Update or add tests around `DuckDBQueryLayer.load_history_table()`:

- Successful history load includes `computed_summary`.
- Cached history load still includes `computed_summary`.
- Existing fields such as `table_name`, `columns`, `row_count`, and `available_tables` remain unchanged.
- `duckdb_query` remains usable against loaded tables.

### Replay UI Tests

Update formatter tests:

- `market_load_history_table` tool output with `computed_summary` produces a human-readable explanation.
- Tool output without `computed_summary` still renders the existing fallback explanation.

## Acceptance Criteria

- `market_load_history_table` returns `computed_summary` for successful history loads.
- The returned summary contains stable top-level groups: `data_window`, `price`, `momentum`, `trend`, `range`, `risk`,
  and `availability`.
- Existing DuckDB table loading and querying behavior continues to work.
- LLM-facing global system prompt remains free of DuckDB SQL guidance.
- Replay UI shows a concise human-readable explanation of the computed summary.
- Focused unit and integration tests pass.

