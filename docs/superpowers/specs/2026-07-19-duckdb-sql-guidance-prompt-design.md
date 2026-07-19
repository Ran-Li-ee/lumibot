# Conditional DuckDB SQL Guidance Prompt

## Purpose

Agents that can load market-history tables or query DuckDB need a compact set
of general SQL construction rules. Those rules should not consume context for
agents that cannot use either capability.

This change adds one independent model-facing prompt and conditionally appends
it to the effective system prompt according to the agent's actual bound tools.

## Goals

- Define DuckDB SQL guidance as an independent prompt constant.
- Append the guidance when an agent has either
  `market_load_history_table` or `duckdb_query`.
- Do not append the guidance when neither tool is bound.
- Preserve this prompt order:
  1. LumiBot base system prompt.
  2. Strategy-specific agent system prompt.
  3. DuckDB SQL guidance, when applicable.
- Record the exact effective prompt through the existing trace and replay
  pipeline.
- Express general relational rules rather than instructions tailored to a
  fixed list of observed errors.

## Non-Goals

- Do not add an SQL parser, validator, rewriter, or automatic retry loop.
- Do not change DuckDB execution behavior.
- Do not change which tools any agent receives.
- Do not add the guidance to agents merely because another agent has DuckDB
  access.
- Do not add a separate replay UI field; the existing effective-system-prompt
  display is authoritative.

## Permission Rule

The condition is evaluated from the final `BoundTool` collection used in the
runtime request:

```text
append guidance if:
  market_load_history_table is bound
  OR
  duckdb_query is bound
```

This uses actual runtime availability, including all normal configuration and
filtering, rather than an earlier requested-tool configuration.

## Prompt Composition

The effective system prompt is composed in this order:

```text
LumiBot base system prompt

USER SYSTEM PROMPT:
Treat this as the strategy-specific trading objective...
<agent-specific system prompt>

DUCKDB SQL GUIDANCE
<guidance body>
```

The guidance is omitted as a complete block when the condition is false.
Task prompts and runtime-context JSON remain separate runtime request fields.

## Guidance Text

```text
DUCKDB SQL GUIDANCE

Use DuckDB only after loading the required data with
market_load_history_table. Treat the returned table_name, columns, and
available_tables as the authoritative database schema. Never infer a table or
column name from the runtime context, prompt wording, or prior runs.

Build SQL as a sequence of explicit relational stages. Each SELECT or CTE
creates a new table whose available columns are exactly the columns selected
by that stage. If a later stage needs a column for filtering, joining,
grouping, or ordering, include that column explicitly in the earlier SELECT.

Keep different kinds of calculation in separate stages:

- Row-level expressions transform individual rows.
- Window functions such as LAG, LEAD, ROW_NUMBER, and moving averages operate
  across ordered rows while preserving row-level output.
- Aggregate functions such as AVG, SUM, MIN, MAX, and COUNT reduce multiple
  rows into grouped results.

When a calculation needs more than one of these stages, compute row-level and
window values in an inner CTE, then aggregate them in an outer query. Do not
place a window function directly inside an aggregate function.

Make the expected result shape explicit. A scalar subquery used as one value
must return exactly one row and one column. LIMIT N returns up to N rows; it
does not select the Nth row. To select one row at an offset, use an explicit
ordering with LIMIT 1 OFFSET N.

For multi-table queries, assign an alias to every table and qualify all
referenced columns with their aliases. This is required for shared or
potentially shared columns such as Date, close, return, and symbol fields.
Use explicit output aliases when combining results from different tables.

Prefer deterministic SQL:

- Specify ORDER BY before using LIMIT, OFFSET, FIRST, LAST, or positional
  comparisons.
- Use NULLIF when a denominator may be zero.
- Preserve timestamp columns through intermediate CTEs when later stages need
  chronological ordering.
- Use clear CTE and output-column names that describe the calculation.

Develop complex queries incrementally. First run the logic successfully
against one loaded table. Inspect its returned columns and row count. Only
then extend the verified pattern to additional tables or symbols. Do not
duplicate an unverified query across the full universe.

Before calling duckdb_query, verify:

- Every referenced table appears in available_tables.
- Every referenced column appears in that table's columns.
- Every outer query uses only columns produced by its input CTEs.
- Each scalar subquery is guaranteed to return one row and one column.
- Window calculations and aggregate calculations occur in separate stages.
- Multi-table columns are qualified with table aliases.
- The query has been tested on one representative table before being expanded.

If DuckDB returns an error, treat the error message as evidence about the
query structure. Identify the failing stage, revise that stage, and test the
smallest corrected query. Do not repeat the same failed SQL or copy it across
additional tables.
```

## Trace Behavior

The existing cache payload and trace request record both the base prompt and
the effective prompt. The effective prompt must contain this guidance exactly
when the permission rule matches. This makes the behavior visible in Agent
Replay without duplicating prompt state.

## Testing

Automated tests must verify:

- An agent with only `market_load_history_table` receives the guidance.
- An agent with only `duckdb_query` receives the guidance.
- An agent with neither tool does not receive the guidance.
- The guidance appears after the agent-specific system prompt.
- It appears exactly once when both tools are bound.
- The runtime request and cache payload receive the same effective prompt.
- Existing minimal execution-agent prompt behavior remains unchanged when
  DuckDB tools are absent.

## Acceptance Criteria

- The conditional behavior is based on actual bound tool names.
- Decision and execution agents without either DuckDB-related tool receive no
  added text.
- The exact model-facing prompt is visible in the trace's effective prompt.
- Focused pytest and Ruff checks pass.
