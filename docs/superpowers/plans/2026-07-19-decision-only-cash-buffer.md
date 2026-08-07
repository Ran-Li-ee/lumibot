# Decision-Only Approximate Cash Buffer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` and TDD.

**Goal:** Make the approximate two-percent reserve a decision-stage sizing
rule while keeping it completely out of the execution-agent handoff.

**Architecture:** Split the current combined cash-safety validation into a
decision sizing validator and a pure no-negative-cash execution affordability
validator. Normalize execution orders without `cash_buffer_pct`. Update only
the growth-execution test strategy prompts and contract tests.

**Tech Stack:** Python, pytest, Ruff, existing LumiBot strategy and agent trace
pipeline.

---

### Task 1: Remove Buffer Metadata from Handoff

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`
- Test: `tests/test_ai_trading_team_growth_execution_test.py`

- [x] Write failing parser tests asserting normalized buy and sell orders omit
  `cash_buffer_pct`, including legacy input containing that field.
- [x] Run the focused parser tests and confirm failure.
- [x] Remove `cash_buffer_pct` from `_normalize_order()` output while preserving
  only the explicit handoff allowlist. Drop free-form `quantity_source` and
  model-provided constraints/notes that could leak reserve information.
- [x] Run the focused parser tests and confirm success.

### Task 2: Split Decision Sizing from Execution Affordability

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`
- Test: `tests/test_ai_trading_team_growth_execution_test.py`

- [x] Write failing tests for the 100,000 / 229.789993 example: 426 passes, 427
  fails below target, and 425 fails because one more whole share still fits.
- [x] Add a sell-then-buy test proving prior sell proceeds are included.
- [x] Add tests rejecting nonexistent/oversized sells, fractional shares,
  multiple buys, and non-market sells whose proceeds fund a later buy.
- [x] Add order-type price tests proving market orders ignore irrelevant
  stop/limit fields and stop-limit uses its matching price.
- [x] Write an execution-affordability test proving positive cash below two
  percent is allowed and negative cash is rejected.
- [x] Implement a decision-stage approximate-buffer validator using the fixed
  strategy constant, largest-whole-share rule, actual holdings, and
  order-type-aware prices.
- [x] Reduce execution cash safety to no-negative-cash validation only.
- [x] Call both validators before execution-agent handoff.
- [x] Run the focused validation tests.

### Task 3: Isolate Prompts

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`
- Test: `tests/test_ai_trading_team_growth_execution_test.py`

- [x] Write failing prompt tests for the approved approximate-two-percent
  wording and explicit formula.
- [x] Assert decision prompts omit "exactly 2%" and all
  "unless a larger cash buffer" variants.
- [x] Assert execution system/task prompts and execution context contain none
  of `cash_buffer_pct`, `0.02`, or `2%`.
- [x] Update decision prompts with the approved formula.
- [x] Remove all buffer references from execution prompts.
- [x] Run the focused prompt and workflow tests.

### Task 4: Regression Verification and Review

**Files:**
- No production edits expected.

- [x] Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py -q
```

- [x] Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check lumibot\example_strategies\ai_trading_team_growth_execution_test.py tests\test_ai_trading_team_growth_execution_test.py
```

- [x] Run `git diff --check` and inspect only the scoped files.
- [x] Request independent specification and code-quality reviews.
