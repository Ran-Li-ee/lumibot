# Decision Cash Buffer Tolerance and One Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` and TDD.

**Goal:** Accept decision quantities leaving one to three percent cash and give
out-of-band decisions one correction attempt before stopping.

**Architecture:** Replace exact-largest-share validation with a typed
ratio-band validator. Extract decision-plan validation into a helper so the
same path validates initial and retry results. Add one retry call with
decision-only diagnostics. Keep the normalized execution handoff unchanged and
free of buffer information.

**Tech Stack:** Python, pytest, Ruff, existing LumiBot agent workflow and trace
pipeline.

---

### Task 1: Ratio-Band Validator

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`
- Test: `tests/test_ai_trading_team_growth_execution_test.py`

- [x] Add failing tests for in-band 1.91572%, inclusive 1%/3% boundaries, and
  out-of-band values.
- [x] Add a dedicated `DecisionCashBufferToleranceError` carrying structured
  sizing diagnostics.
- [x] Replace exact maximum-quantity comparisons with inclusive ratio checks.
- [x] Preserve whole-share, sell proceeds, holdings, order-type, and
  no-negative-cash behavior.
- [x] Run focused validator tests.

### Task 2: One Decision Retry

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`
- Test: `tests/test_ai_trading_team_growth_execution_test.py`

- [x] Add failing workflow tests for initial pass, retry-pass, retry-fail, and
  no-retry-on-structural-error behavior.
- [x] Extract strict decision parsing and validation into a reusable helper.
- [x] Catch only `DecisionCashBufferToleranceError` from the initial attempt.
- [x] Run the decision agent once more with numerical diagnostic context and a
  correction-only task prompt.
- [x] Validate the retry through the same helper and stop after that attempt.
- [x] Run focused workflow tests.

### Task 3: Isolation and Prompt Tests

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`
- Test: `tests/test_ai_trading_team_growth_execution_test.py`

- [x] Assert retry prompt/context contain the prior ratio, allowed range, and
  correction instruction.
- [x] Assert accepted initial and retry plans hand off only normalized fields.
- [x] Assert execution system prompt, task prompt, and context contain no
  reserve percentages, tolerance labels, previous output, or retry error.
- [x] Run focused prompt and handoff tests.

### Task 4: Verification and Review

- [x] Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py -q
```

- [x] Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check lumibot\example_strategies\ai_trading_team_growth_execution_test.py tests\test_ai_trading_team_growth_execution_test.py
```

- [x] Run `git diff --check`.
- [x] Request independent specification and code-quality reviews.
