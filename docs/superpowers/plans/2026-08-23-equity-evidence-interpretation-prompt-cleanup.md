# Equity Evidence Interpretation Prompt Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the equity-only selection prompts so the LLM interprets the five rank evidence groups with clear priority, uses news only conditionally, and still emits strict five-symbol JSON.

**Architecture:** Keep the existing equity-only workflow intact: the equity agent calls `market_load_history_tables_summary`, optionally calls `alpaca_news`, returns strict JSON, and the deterministic planner/execution path remains unchanged. Add one shared evidence interpretation policy string and reuse it in both generic and QQQ historical equity system prompts to prevent drift.

**Tech Stack:** Python, pytest, Lumibot example strategy helpers, existing AgentManager/tooling, OpenAI/LiteLLM smoke backtest runner.

---

## File Structure

### Files To Modify

- `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
  - Add a shared `EQUITY_EVIDENCE_INTERPRETATION_POLICY`.
  - Rewrite `equity_basket_agent_system_prompt`.
  - Rewrite `qqq_historical_equity_basket_agent_system_prompt`.
  - Rewrite `equity_basket_agent_task_prompt`.

- `tests/test_ai_trading_team_equity_only_llm.py`
  - Update prompt tests from the old `composite_score` / `leading group` wording to the new evidence policy.
  - Add absence checks for misleading language.
  - Keep existing workflow, strict JSON, conditional news, and no-order/no-sizing assertions.

- `docs/superpowers/notes/2026-08-23-equity-evidence-prompt-cleanup-validation.md`
  - Record focused test results and the one-day smoke backtest artifact path.

### Files Not To Modify

- `lumibot/components/agents/tools/history_summary.py`
  - Indicator/rank calculation is out of scope.

- `lumibot/components/agents/manager.py`
  - Existing general tool policy remains unchanged.

- `lumibot/components/agents/builtins.py`
  - Tool descriptions remain unchanged in this feature.

- `target_portfolio_to_execution_plan` related files
  - Execution planning and sizing are out of scope.

---

## Task 1: Add Failing Prompt Policy Tests

**Files:**
- Modify: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Update the generic equity system prompt test**

Replace the body of `test_equity_agent_system_prompt_is_equity_only_rank_first_and_conditional_news` with:

```python
def test_equity_agent_system_prompt_is_equity_only_rank_first_and_conditional_news():
    module = load_module()
    strategy = make_strategy(module.AITradingTeamEquityOnlyLLMStrategy)

    strategy.initialize()

    equity_agent = created_agent_config(strategy, "equity_basket_agent")
    prompt = equity_agent["system_prompt"].lower()

    for required in (
        "equity-only",
        "choose exactly five stocks",
        "equal target weights",
        "market_load_history_tables_summary",
        "evidence interpretation policy",
        "momentum and trend quality as primary selection evidence",
        "not purely volatility-driven",
        "timing and leadership confirmation",
        "supporting evidence, not as a standalone reason",
        "do not average all ranking groups equally",
        "do not select a stock solely because it leads one ranking list",
        "do not treat any single ranking or combined score as the final answer",
        "alpaca_news",
        "leading candidates",
        "close, conflicting, or uncertain",
        "strict json",
        "do not place orders",
        "cannot place orders or size trades",
    ):
        assert required in prompt

    for forbidden in (
        "quadrant",
        "macro regime",
        "commodity",
        "tips",
        "nominal bond",
        "defensive posture",
        "safest",
        "defensive",
        "cyclical",
        "speculative",
        "leading group",
        "composite_score",
        "duckdb",
        "optimize weights",
    ):
        assert forbidden not in prompt
```

- [ ] **Step 2: Update the equity task prompt test**

In `test_equity_agent_task_prompt_teaches_summary_first_top_n_and_strict_json`, replace the prompt assertions after `task_prompt = equity_call["task_prompt"].lower()` with:

```python
    assert equity_call["context"]["basket_symbols"] == EXPECTED_EQUITY_UNIVERSE
    assert "market_load_history_tables_summary" in task_prompt
    assert "first call" in task_prompt
    assert "length=252" in task_prompt
    assert "timestep='day'" in task_prompt
    assert "top_n=10" in task_prompt
    assert "candidate_summary_limit=25" in task_prompt
    assert "evidence interpretation policy" in task_prompt
    assert "select exactly five unique symbols" in task_prompt
    assert "avoid selecting a stock supported by only one evidence group unless" in task_prompt
    assert "do not assign per-symbol weights" in task_prompt
    assert "alpaca_news" in task_prompt
    assert "close, conflicting, or uncertain" in task_prompt
    assert "leading candidates only" in task_prompt
    assert "candidate_symbols must copy the assigned basket_symbols exactly" in task_prompt
    assert "selected_symbols must contain exactly five unique symbols" in task_prompt
    assert "return exactly one strict json object" in task_prompt

    for forbidden in (
        "leading group",
        "composite_score",
        "defensive",
        "cyclical",
        "speculative",
        "optimize weights",
        "duckdb",
    ):
        assert forbidden not in task_prompt
```

- [ ] **Step 3: Update the QQQ historical equity system prompt test**

Replace the body of `test_qqq_historical_equity_agent_prompt_mentions_historical_constituents_without_weight_bias` with:

```python
def test_qqq_historical_equity_agent_prompt_mentions_historical_constituents_without_weight_bias(monkeypatch):
    module = load_module()
    strategy = make_strategy(module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy)

    strategy.initialize()

    equity_agent = created_agent_config(strategy, "equity_basket_agent")
    prompt = equity_agent["system_prompt"].lower()

    for required in (
        "qqq historical constituent universe",
        "provided basket_symbols",
        "current backtest date",
        "current rank evidence",
        "do not invent sector",
        "do not add symbols",
        "index weight",
        "evidence interpretation policy",
        "momentum and trend quality as primary selection evidence",
        "not purely volatility-driven",
        "timing and leadership confirmation",
        "supporting evidence, not as a standalone reason",
        "do not average all ranking groups equally",
        "do not select a stock solely because it leads one ranking list",
        "do not treat any single ranking or combined score as the final answer",
        "alpaca_news",
        "leading candidates",
        "strict json",
    ):
        assert required in prompt

    for forbidden in (
        "automatically high quality",
        "always prefer",
        "buy qqq",
        "safety label",
        "leading group",
        "composite_score",
        "safe or best",
        "safest",
        "defensive",
        "cyclical",
        "speculative",
        "duckdb",
        "optimize weights",
    ):
        assert forbidden not in prompt
```

- [ ] **Step 4: Run the updated prompt tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_system_prompt_is_equity_only_rank_first_and_conditional_news tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_task_prompt_teaches_summary_first_top_n_and_strict_json tests/test_ai_trading_team_equity_only_llm.py::test_qqq_historical_equity_agent_prompt_mentions_historical_constituents_without_weight_bias -q
```

Expected: the tests fail because the current prompts still contain old wording such as `leading group` and `composite_score`, and do not yet contain `Evidence Interpretation Policy`.

---

## Task 2: Add Shared Evidence Interpretation Policy

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`

- [ ] **Step 1: Add the shared policy constant**

Insert this constant immediately above `def equity_basket_agent_system_prompt(symbols: str) -> str:`:

```python
EQUITY_EVIDENCE_INTERPRETATION_POLICY = (
    "Evidence Interpretation Policy: "
    "Use momentum and trend quality as primary selection evidence. "
    "Use risk-adjusted momentum to prefer strength that is not purely volatility-driven. "
    "Use breakout / near-high evidence as timing and leadership confirmation. "
    "Use volume confirmation only as supporting evidence, not as a standalone reason to select a stock. "
    "Prefer candidates that are strong across primary evidence and confirmed by secondary evidence. "
    "Do not average all ranking groups equally. "
    "Do not select a stock solely because it leads one ranking list. "
    "Do not treat any single ranking or combined score as the final answer."
)
```

- [ ] **Step 2: Run the prompt tests and verify they still fail**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_system_prompt_is_equity_only_rank_first_and_conditional_news tests/test_ai_trading_team_equity_only_llm.py::test_qqq_historical_equity_agent_prompt_mentions_historical_constituents_without_weight_bias -q
```

Expected: the tests still fail because the system prompts do not yet include the new constant and still contain removed language.

---

## Task 3: Rewrite Equity System Prompts

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`

- [ ] **Step 1: Replace the generic equity system prompt**

Replace `equity_basket_agent_system_prompt` with:

```python
def equity_basket_agent_system_prompt(symbols: str) -> str:
    return (
        f"Equity-only selection role: choose exactly five stocks from the assigned basket_symbols ({symbols}). "
        "The downstream deterministic planner gives the five selected stocks equal target weights. "
        "You cannot place orders or size trades. Use market_load_history_tables_summary first for multi-symbol "
        "comparison. "
        f"{EQUITY_EVIDENCE_INTERPRETATION_POLICY} "
        "Do not invent sector, style, or category labels not provided by tools. "
        "If the leading candidate set is clear from rank evidence, select without news. "
        "Use alpaca_news only as a tie-breaker or risk/catalyst check for leading candidates when rank evidence "
        "is close, conflicting, or uncertain; when used, request news only for leading candidates. "
        "If news is unavailable, continue with rank-only evidence. Return strict JSON only. Do not place orders."
    )
```

- [ ] **Step 2: Replace the QQQ historical equity system prompt**

Replace `qqq_historical_equity_basket_agent_system_prompt` with:

```python
def qqq_historical_equity_basket_agent_system_prompt(symbols: str) -> str:
    return (
        f"Equity-only selection role: choose exactly five stocks from the QQQ historical constituent universe "
        f"provided in basket_symbols ({symbols}). "
        "The provided basket_symbols represent the QQQ historical constituent universe available for the "
        "current backtest date. The downstream deterministic planner gives the five selected stocks equal "
        "target weights. You cannot place orders or size trades. Use market_load_history_tables_summary first "
        "for multi-symbol comparison. "
        f"{EQUITY_EVIDENCE_INTERPRETATION_POLICY} "
        "Do not invent sector, style, or category labels not provided by tools. "
        "Do not assume QQQ membership itself makes a stock superior; select from current rank evidence. "
        "Do not choose based on index weight alone. "
        "If the leading candidate set is clear from rank evidence, select without news. "
        "Use alpaca_news only as a tie-breaker or risk/catalyst check for leading candidates when rank evidence "
        "is close, conflicting, or uncertain; when used, request news only for leading candidates. "
        "If news is unavailable, continue with rank-only evidence. Use only symbols in the provided "
        "basket_symbols and do not add symbols outside the provided universe. Return strict JSON only. "
        "Do not place orders."
    )
```

- [ ] **Step 3: Run system prompt tests and verify they pass**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_system_prompt_is_equity_only_rank_first_and_conditional_news tests/test_ai_trading_team_equity_only_llm.py::test_qqq_historical_equity_agent_prompt_mentions_historical_constituents_without_weight_bias -q
```

Expected: both tests pass.

---

## Task 4: Rewrite Equity Task Prompt

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`

- [ ] **Step 1: Replace the task prompt**

Replace `equity_basket_agent_task_prompt` with:

```python
def equity_basket_agent_task_prompt() -> str:
    return (
        "Review only the provided basket_symbols. First call market_load_history_tables_summary with "
        "symbols=basket_symbols, length=252, timestep='day', top_n=10, and candidate_summary_limit=25. "
        "Compare the five rank groups using the Evidence Interpretation Policy from your system prompt. "
        "Select exactly five unique symbols from basket_symbols. Avoid selecting a stock supported by only "
        "one evidence group unless the other leading candidates are weaker or conflicting; explain the "
        "exception in reason_brief. "
        "If the leading candidate set is clear from rank evidence, select without news. "
        "If leading candidates are close, conflicting, or uncertain, call alpaca_news for those leading "
        "candidates only. If alpaca_news is unavailable or errors, continue with rank-only evidence. "
        "Return exactly one strict JSON object with basket_id, target_weight, status, candidate_symbols, "
        "selected_symbols, and reason_brief. Use status='active'. target_weight must be 1.0 for the equity "
        "basket as a whole; do not assign per-symbol weights. candidate_symbols must copy the assigned "
        "basket_symbols exactly; do not replace it with a shortlist. selected_symbols must contain exactly "
        "five unique symbols from basket_symbols."
    )
```

- [ ] **Step 2: Run the task prompt test and verify it passes**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_task_prompt_teaches_summary_first_top_n_and_strict_json -q
```

Expected: the task prompt test passes.

---

## Task 5: Run Focused Regression Tests

**Files:**
- No code edits in this task.

- [ ] **Step 1: Run equity-only prompt and workflow tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q
```

Expected: all tests in `tests/test_ai_trading_team_equity_only_llm.py` pass.

- [ ] **Step 2: Run agent manager regression tests**

Run:

```powershell
python -m pytest tests/test_agent_manager.py -q
```

Expected: all tests in `tests/test_agent_manager.py` pass.

- [ ] **Step 3: Run the combined focused regression command**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_manager.py -q
```

Expected: both focused test files pass in one command.

- [ ] **Step 4: Commit prompt and test changes**

Run:

```powershell
git add lumibot/example_strategies/ai_trading_team_equity_only_helpers.py tests/test_ai_trading_team_equity_only_llm.py
git commit -m "fix: clarify equity evidence interpretation prompts"
```

Expected: a commit is created containing only the prompt/test changes.

---

## Task 6: Run One-Day Smoke Backtest

**Files:**
- Create: `docs/superpowers/notes/2026-08-23-equity-evidence-prompt-cleanup-validation.md`

- [ ] **Step 1: Run the QQQ historical equity-only one-day smoke backtest**

Run:

```powershell
$env:AI_TRADING_TEAM_MODEL = "gpt-5.6-luna"
python scripts\run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2024-09-05 --end 2024-09-06 --run-frequency weekly --weekly-run-weekday THU --env-file D:\Lumibot\project_notes\API.txt --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 240
```

Expected:
- The benchmark completes without an unhandled exception.
- A new directory appears under `artifacts/ai_trading_team_example_benchmarks`.
- The selected run contains the `qqq-historical-equity-only-llm` strategy artifacts.

- [ ] **Step 2: Find the latest benchmark artifact**

Run:

```powershell
Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 5 FullName, LastWriteTime
```

Expected: the newest artifact directory is the smoke backtest from Step 1.

- [ ] **Step 3: Inspect trace prompt text for the new policy**

Run:

```powershell
$LATEST = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
rg -n "Evidence Interpretation Policy|momentum and trend quality as primary selection evidence|Do not average all ranking groups equally|leading group|composite_score" $LATEST.FullName
```

Expected:
- `Evidence Interpretation Policy` appears in the equity agent prompt trace.
- `momentum and trend quality as primary selection evidence` appears in the equity agent prompt trace.
- `Do not average all ranking groups equally` appears in the equity agent prompt trace.
- `leading group` does not appear.
- `composite_score` does not appear in prompt text. If it appears in tool output data, confirm it is not part of the equity prompt.

- [ ] **Step 4: Inspect trace tool calls for summary-tool wiring**

Run:

```powershell
$LATEST = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
rg -n "market_load_history_tables_summary|top_n|candidate_summary_limit|alpaca_news|selected_symbols" $LATEST.FullName
```

Expected:
- `market_load_history_tables_summary` appears in the equity agent trace.
- `top_n` appears with value `10`.
- `candidate_summary_limit` appears with value `25`.
- `selected_symbols` appears in the equity agent final summary and contains five symbols.
- `alpaca_news` may appear only if the model found the leading candidates close, conflicting, or uncertain.

- [ ] **Step 5: Create the validation note**

Run this command after the focused tests and smoke backtest pass:

```powershell
$LATEST = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
$ARTIFACT = Join-Path $LATEST.FullName "qqq-historical-equity-only-llm"
@"
# Equity Evidence Prompt Cleanup Validation

## Date

2026-08-23

## Branch

feature/qqq-historical-constituent-universe

## Focused Tests

````text
python -m pytest tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_manager.py -q
````

Result: PASS

## Smoke Backtest

````text
python scripts\run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2024-09-05 --end 2024-09-06 --run-frequency weekly --weekly-run-weekday THU --env-file D:\Lumibot\project_notes\API.txt --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 240
````

Artifact:

````text
$ARTIFACT
````

## Prompt Checks

- Evidence Interpretation Policy present: yes
- Momentum and trend quality marked as primary evidence: yes
- Risk-adjusted momentum described as volatility check: yes
- Breakout / near-high described as timing and leadership confirmation: yes
- Volume described as supporting evidence only: yes
- News remains conditional and candidate-limited: yes
- Old `leading group` wording absent from prompts: yes
- Old `composite_score` wording absent from prompts: yes

## Workflow Checks

- Equity agent called `market_load_history_tables_summary`: yes
- `top_n=10`: yes
- `candidate_summary_limit=25`: yes
- Equity agent selected exactly five symbols: yes
- Workflow reached deterministic planning and execution: yes

## Notes

This smoke test validates prompt wiring and workflow continuity only. It is not a performance claim.
"@ | Set-Content -Encoding UTF8 docs\superpowers\notes\2026-08-23-equity-evidence-prompt-cleanup-validation.md
```

- [ ] **Step 6: Commit validation note**

Run:

```powershell
git add docs/superpowers/notes/2026-08-23-equity-evidence-prompt-cleanup-validation.md
git commit -m "docs: validate equity evidence prompt cleanup"
```

Expected: a commit is created containing the validation note.

---

## Self-Review

### Spec Coverage

- System prompt rewrite: covered by Tasks 2 and 3.
- QQQ system prompt rewrite: covered by Task 3.
- Task prompt rewrite: covered by Task 4.
- Evidence Interpretation Policy: covered by Task 2.
- Conditional news behavior: covered by Tasks 1, 3, 4, and 6.
- Strict JSON and five-symbol selection: covered by Tasks 1, 4, and 6.
- No indicator, universe, planner, execution, or news API logic changes: enforced by file boundaries and Task 5 regression tests.
- Smoke backtest: covered by Task 6.

### Placeholder Scan

The plan contains no open implementation placeholders. Runtime artifact paths are discovered with PowerShell commands during validation.

### Type And Name Consistency

- Prompt helper names match existing functions:
  - `equity_basket_agent_system_prompt`
  - `qqq_historical_equity_basket_agent_system_prompt`
  - `equity_basket_agent_task_prompt`
- Test function names match existing tests in `tests/test_ai_trading_team_equity_only_llm.py`.
- Tool names match existing tool names:
  - `market_load_history_tables_summary`
  - `alpaca_news`
