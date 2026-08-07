# Conditional DuckDB SQL Guidance Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` to execute this plan task by task.

**Goal:** Append an independent DuckDB SQL guidance prompt only to agents whose
actual bound tools include history-table loading or DuckDB querying.

**Architecture:** Store the guidance in a dedicated prompt module. Extend
`AgentHandle` prompt composition to accept the final bound-tool collection,
derive a set of tool names, and append the guidance after the strategy-specific
agent prompt when either trigger name is present. Reuse the same composed text
for cache, trace, provider cache key, and runtime request.

**Tech Stack:** Python, pytest, Ruff, existing LumiBot agent manager and trace
pipeline.

---

### Task 1: Independent Guidance Prompt

**Files:**
- Create: `lumibot/components/agents/duckdb_prompt.py`
- Test: `tests/test_agent_manager.py`

- [x] **Step 1: Write a failing prompt-content test**

Import the prompt constant and assert that it contains the approved general
rules, including authoritative schemas, CTE output columns, separated window
and aggregate stages, scalar result shape, qualified aliases, deterministic
ordering, incremental testing, and failure revision.

- [x] **Step 2: Run the focused test and confirm failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py -k "duckdb_sql_guidance_content" -q
```

- [x] **Step 3: Add the independent prompt constant**

Create `DUCKDB_SQL_GUIDANCE_PROMPT` with the exact approved text from the
design spec. Keep it free of strategy names, symbols, and observed-error
counts.

- [x] **Step 4: Run the focused test**

Expected: selected test passes.

### Task 2: Conditional Prompt Composition

**Files:**
- Modify: `lumibot/components/agents/manager.py`
- Test: `tests/test_agent_manager.py`

- [x] **Step 1: Write failing conditional-composition tests**

Cover agents with:

- only `market_load_history_table`;
- only `duckdb_query`;
- both trigger tools;
- unrelated tools only;
- no tools.

Assert inclusion, omission, exactly-once behavior, and ordering after the
agent-specific system prompt.

- [x] **Step 2: Run the tests and confirm expected failures**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py -k "duckdb_sql_guidance" -q
```

- [x] **Step 3: Implement bound-tool-aware composition**

Import the independent prompt. Make prompt composition accept the final
`BoundTool` list and append the guidance only when either trigger name is
present. In `run()`, bind tools once before composing the prompt and reuse that
list for:

- effective prompt construction;
- cache payload tool surface;
- runtime request;
- provider prompt cache key.

Do not alter base prompt selection or tool permissions.

- [x] **Step 4: Run the conditional tests**

Expected: all selected tests pass.

### Task 3: Trace and Runtime Consistency

**Files:**
- Modify if required: `tests/test_agent_manager.py`
- Modify if required: `lumibot/components/agents/manager.py`

- [x] **Step 1: Add a focused runtime/cache consistency test**

Capture a runtime request and cache payload for an agent with a trigger tool.
Assert both use the same effective prompt and that the guidance appears once.
Repeat the omission assertion for an agent without trigger tools.

- [x] **Step 2: Run the focused manager tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py -q
```

- [x] **Step 3: Preserve existing prompt compatibility**

Run the existing agent tool-permission and backtest runtime tests to confirm
that conditional prompt composition does not alter permissions, minimal
execution prompts, or replay behavior.

### Task 4: Verification and Review

**Files:**
- No production edits expected.

- [x] **Step 1: Run focused pytest**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py tests\test_agent_tool_permissions.py tests\backtest\test_agent_runtime_backtest.py -q
```

- [x] **Step 2: Run focused Ruff**

```powershell
.\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\duckdb_prompt.py lumibot\components\agents\manager.py tests\test_agent_manager.py
```

- [x] **Step 3: Inspect scope**

```powershell
git diff --check
git diff -- lumibot/components/agents/duckdb_prompt.py lumibot/components/agents/manager.py tests/test_agent_manager.py docs/superpowers/specs/2026-07-19-duckdb-sql-guidance-prompt-design.md docs/superpowers/plans/2026-07-19-duckdb-sql-guidance-prompt.md
```

- [x] **Step 4: Request independent reviews**

Have one reviewer check specification compliance and another check code
quality, compatibility, and accidental changes to existing user work.
