# DuckDB Schema-Aware Query Guidance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give LLM agents a current inventory of loaded DuckDB history tables and explicit SQL join-alias guidance.

**Architecture:** `DuckDBQueryLayer` will derive a small, deterministic model-facing table inventory from its existing in-memory metadata and attach it to every fresh or cached history-load result. The bound `duckdb_query` description will remain static but gain explicit alias/qualification rules and a correct multi-table example.

**Tech Stack:** Python, DuckDB, pandas, pytest, Ruff, existing Lumibot agent tools and trace pipeline.

---

### Task 1: Model-Facing DuckDB Table Inventory

**Files:**
- Modify: `lumibot/components/agents/duckdb_tools.py`
- Test: `tests/backtest/test_agent_runtime_backtest.py`

- [ ] **Step 1: Write failing tests for fresh, cumulative, and cached inventories**

Add a focused test that binds `market_load_history_table`, loads two named
tables, and asserts:

```python
assert first["available_tables"] == [
    {"table_name": "qqq_hist", "columns": first["columns"]}
]
assert second["available_tables"] == [
    {"table_name": "qqq_hist", "columns": first["columns"]},
    {"table_name": "spy_hist", "columns": second["columns"]},
]
assert cached_first["available_tables"] == second["available_tables"]
assert all(not item["table_name"].startswith("source_") for item in second["available_tables"])
```

- [ ] **Step 2: Run the focused test and confirm the expected failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py -k "duckdb_table_inventory" -q
```

Expected: failure because `available_tables` is absent.

- [ ] **Step 3: Add a deterministic inventory helper**

Add a private `DuckDBQueryLayer` helper that:

```python
def _available_table_schemas(self) -> list[dict[str, Any]]:
    schemas = []
    for table_name, meta in self._table_meta.items():
        if meta.get("kind") == "source_frame":
            continue
        schemas.append(
            {
                "table_name": str(table_name),
                "columns": [str(column) for column in meta.get("columns", [])],
            }
        )
    return sorted(schemas, key=lambda item: item["table_name"])
```

Attach a fresh snapshot to the returned copy for both cache hits and newly
loaded tables. Do not store `available_tables` as authoritative cache state.

- [ ] **Step 4: Run the focused test and confirm it passes**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py -k "duckdb_table_inventory" -q
```

Expected: all selected tests pass.

### Task 2: Explicit Multi-Table SQL Guidance

**Files:**
- Modify: `lumibot/components/agents/builtins.py`
- Test: `tests/backtest/test_agent_runtime_backtest.py`

- [ ] **Step 1: Extend the existing description test first**

Update `test_builtin_market_history_and_duckdb_descriptions_include_schema_hints`
to assert that the model-facing description includes:

```python
assert "alias every table" in query_tool.description
assert "q.sym" in query_tool.description
assert "q.Date" in query_tool.description
assert "q.close" in query_tool.description
assert "JOIN spy_hist AS s ON q.Date = s.Date" in query_tool.description
```

- [ ] **Step 2: Run the description test and confirm the expected failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_builtin_market_history_and_duckdb_descriptions_include_schema_hints -q
```

Expected: failure because the alias and qualified-column guidance is absent.

- [ ] **Step 3: Update only the DuckDB-related tool descriptions**

Extend `market_load_history_table` to explain that `available_tables` contains
the current queryable table inventory. Extend `duckdb_query` to require aliases
for every table in a join and qualified references for duplicate or potentially
duplicate fields such as `sym`, `Date`, `close`, and `return`. Include this
compact example:

```sql
SELECT q.Date, q.close AS qqq_close, s.close AS spy_close
FROM qqq_hist AS q
JOIN spy_hist AS s ON q.Date = s.Date
ORDER BY q.Date
```

- [ ] **Step 4: Run the description and inventory tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py -k "duckdb_table_inventory or schema_hints" -q
```

Expected: all selected tests pass.

### Task 3: Focused Regression Verification

**Files:**
- No production edits expected.

- [ ] **Step 1: Run the complete focused agent-runtime test file**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py -q
```

Expected: zero failures.

- [ ] **Step 2: Run focused Ruff checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\duckdb_tools.py lumibot\components\agents\builtins.py tests\backtest\test_agent_runtime_backtest.py
```

Expected: zero errors.

- [ ] **Step 3: Inspect the diff for scope and existing-work preservation**

Run:

```powershell
git diff --check
git diff -- lumibot/components/agents/duckdb_tools.py lumibot/components/agents/builtins.py tests/backtest/test_agent_runtime_backtest.py docs/superpowers/specs/2026-07-19-duckdb-schema-aware-query-guidance-design.md docs/superpowers/plans/2026-07-19-duckdb-schema-aware-query-guidance.md
```

Expected: no whitespace errors; only the approved two-layer DuckDB changes and
their tests/docs appear.

- [ ] **Step 4: Review the acceptance criteria**

Confirm from tests and diff that:

- every history-load result includes a refreshed table inventory;
- internal source tables are hidden;
- query guidance explicitly prevents ambiguous join columns;
- no SQL parser, rewriter, auto-retry, or new schema tool was added.
