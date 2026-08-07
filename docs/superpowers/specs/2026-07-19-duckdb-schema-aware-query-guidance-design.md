# DuckDB Schema-Aware Query Guidance

## Purpose

LLM agents currently receive metadata for the single table returned by each
`market_load_history_table` call, but they do not receive a consolidated view
of all user-queryable DuckDB tables loaded so far. The `duckdb_query` tool also
warns against invented columns, but does not clearly require table aliases and
qualified column references when joins contain duplicate names such as `sym`,
`Date`, or `close`.

This change improves those two model-facing information layers before
considering a larger SQL parser, validator, or automatic query rewriter.

## Goals

- Include a current inventory of all user-queryable history tables in every
  successful `market_load_history_table` result.
- For each table, expose its exact table name and exact column names.
- Keep source/cache implementation tables out of the model-facing inventory.
- Preserve the existing metadata for the table loaded by the current call.
- Expand the `duckdb_query` model-facing description with explicit join and
  qualification rules.
- Add a short correct multi-table SQL example using aliases.
- Preserve read-only SQL enforcement and all current query behavior.

## Non-Goals

- Do not parse, rewrite, repair, or retry SQL automatically.
- Do not add a new schema-inspection tool.
- Do not block ambiguous SQL before DuckDB executes it.
- Do not change table or column names.
- Do not change agent prompts outside the two DuckDB-related tool definitions.
- Do not modify trace or Agent Replay UI schemas in this pass; the enriched tool
  result will already be captured by the existing trace pipeline.

## Model-Facing History Result

After a history table is successfully loaded, its result continues to contain
the existing top-level fields such as:

```json
{
  "table_name": "qqq_hist",
  "columns": ["Date", "open", "high", "low", "close", "volume"],
  "row_count": 252
}
```

It also contains a new `available_tables` list representing every
user-queryable history table currently available to `duckdb_query`:

```json
{
  "available_tables": [
    {
      "table_name": "qqq_hist",
      "columns": ["Date", "open", "high", "low", "close", "volume"]
    },
    {
      "table_name": "spy_hist",
      "columns": ["Date", "open", "high", "low", "close", "volume"]
    }
  ]
}
```

The list is deterministically sorted by `table_name`. Each entry contains only
the fields needed for SQL construction: `table_name` and `columns`. Internal
`source_frame` tables are excluded because agents should query the visible
history views/slices returned by `market_load_history_table`.

Cached history-load results must refresh `available_tables` before being
returned. Otherwise a cached result could omit tables loaded later in the same
agent run.

## DuckDB Query Guidance

The `duckdb_query` tool description must tell the model:

- Use only table and column names returned by `market_load_history_table` or
  discovered with `pragma_table_info`.
- Treat column names as exact; `Date` does not imply `datetime`.
- Assign an alias to every table in a multi-table query.
- Qualify shared or potentially shared fields with the alias, including `sym`,
  `Date`, `close`, and `return`.
- Never select a bare duplicate column name from a join.

The description includes a compact example:

```sql
SELECT q.Date, q.close AS qqq_close, s.close AS spy_close
FROM qqq_hist AS q
JOIN spy_hist AS s ON q.Date = s.Date
ORDER BY q.Date
```

This example is explanatory only. It does not imply that those specific tables
exist in every run.

## Data Flow

1. An agent calls `market_load_history_table`.
2. `DuckDBQueryLayer` registers or refreshes the visible history table.
3. The layer builds a snapshot from its current user-queryable table metadata.
4. The tool result returns the loaded table metadata plus `available_tables`.
5. The LLM uses that result and the static `duckdb_query` rules to construct SQL.
6. DuckDB remains the authority that executes or rejects the SQL.

## Error Handling

- If no user-queryable history table exists, `available_tables` is an empty
  list.
- Table inventory generation must not query the internet or reload market data.
- The inventory is derived from in-memory `_table_meta`.
- Existing DuckDB exceptions remain unchanged and continue through the current
  runtime error-recording path.

## Testing

Automated tests must verify:

- Loading one history table returns one `available_tables` entry with the exact
  table name and columns.
- Loading a second table returns both entries in deterministic order.
- Internal `source_frame` tables are not exposed.
- A cached first-table load performed after loading a second table returns a
  refreshed two-table inventory.
- `duckdb_query` guidance requires aliases and qualified duplicate columns.
- The guidance mentions `sym`, `Date`, and `close` and includes a valid
  multi-table example.
- Existing focused DuckDB/backtest tests remain green.

## Acceptance Criteria

- A trace of `market_load_history_table` visibly contains all currently
  queryable table names and columns.
- The exact model-facing `duckdb_query` description explains how to avoid
  ambiguous references in joins.
- No SQL parser or automatic correction behavior is introduced.
- Targeted pytest and Ruff checks pass.
