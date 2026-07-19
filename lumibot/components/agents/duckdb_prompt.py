DUCKDB_SQL_GUIDANCE_PROMPT = """\
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
"""
