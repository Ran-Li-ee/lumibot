# Nominal Bond Basket Universe Duration Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the mock Growth / Inflation quadrant strategy's nominal bond basket to 16 U.S. nominal Treasury ETFs and make `nominal_bond_basket_agent` choose rank-first, with neutral maturity / duration framing.

**Architecture:** Keep the existing workflow unchanged: macro allocation agent -> four basket agents -> portfolio decision agent -> execution agent. Modify only the nominal bond basket universe, nominal-bond-specific basket prompts, and related tests/verification. Do not add news, FRED, rate-curve, trading, planner, execution, or UI schema behavior in this feature.

**Tech Stack:** Python, pytest, ruff, existing LumiBot agent built-ins, existing `market_load_history_tables_summary` evidence layer, Yahoo-style backtest data path, existing benchmark runner, existing Agent Workflow Replay UI.

---

## Reference Spec

Use this spec as the source of truth:

```text
docs/superpowers/specs/2026-08-14-nominal-bond-basket-universe-duration-prompt-design.md
```

## File Structure

Modify:

```text
lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py
```

Responsibilities:

- Owns `BASKET_UNIVERSES`.
- Owns `basket_agent_tools`.
- Owns `basket_agent_system_prompt`.
- Owns `basket_agent_task_prompt`.
- Creates `nominal_bond_basket_agent` with model-facing tools and prompts.

Modify:

```text
tests/test_ai_trading_team_mock_growth_inflation_quadrant.py
```

Responsibilities:

- Verifies approved basket universes.
- Verifies nominal bond tool permissions remain history/price only.
- Verifies nominal-bond-specific prompt wording.
- Verifies task prompt wording passed through `on_trading_iteration`.
- Keeps existing equity, commodity, TIPS, portfolio, execution, and workflow tests passing.

Create:

```text
docs/superpowers/notes/2026-08-14-nominal-bond-basket-universe-duration-prompt-validation.md
```

Responsibilities:

- Records data-path verification.
- Records unit/lint command results.
- Records one-day benchmark and trace/UI smoke evidence.

No other production files are required.

## Implementation Notes

- Do not change `MOCK_WEIGHT_BY_REGIME`.
- Do not change macro regime classification.
- Do not change portfolio decision, target portfolio planner, execution, or replay UI code.
- Do not give `nominal_bond_basket_agent` `alpaca_news`.
- Do not give `nominal_bond_basket_agent` FRED tools.
- Do not give `nominal_bond_basket_agent` any order or execution tools.
- Do not label any maturity bucket as best, safest, default, or preferred.
- Preserve flat `basket_symbols` context. The duration metadata belongs in the prompt, not as a new context schema.

---

### Task 1: Add Failing Tests For Nominal Bond Universe And Tool Surface

**Files:**

- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add an `expected_nominal_bond_universe` helper**

In `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`, immediately after `expected_tips_universe()`, add:

```python
def expected_nominal_bond_universe():
    return [
        "SGOV",
        "BIL",
        "SHV",
        "SHY",
        "VGSH",
        "SCHO",
        "IEI",
        "IEF",
        "VGIT",
        "SCHR",
        "GOVT",
        "TLH",
        "TLT",
        "VGLT",
        "EDV",
        "ZROZ",
    ]
```

- [ ] **Step 2: Update the basket universe assertion**

In `test_basket_universes_have_expected_symbols`, replace:

```python
        "nominal_bond": ["SHY", "IEF", "TLT", "GOVT", "VGIT"],
```

with:

```python
        "nominal_bond": expected_nominal_bond_universe(),
```

After:

```python
    assert len(module.BASKET_UNIVERSES["tips"]) == 6
```

add:

```python
    assert len(module.BASKET_UNIVERSES["nominal_bond"]) == 16
```

- [ ] **Step 3: Add a direct tool-surface boundary assertion for nominal bonds**

In `test_agents_receive_distinct_tool_surfaces`, after the existing `news_enabled_basket_agent` loop and before portfolio/execution assertions, add:

```python
    nominal_bond_tools = created_tool_names(created["nominal_bond_basket_agent"])
    assert nominal_bond_tools == {
        "market_load_history_tables_summary",
        "market_last_price",
    }
    assert "alpaca_news" not in nominal_bond_tools
    assert "list_fred_series" not in nominal_bond_tools
    assert "get_fred_series" not in nominal_bond_tools
    assert "get_fred_latest" not in nominal_bond_tools
    assert "get_fred_snapshot" not in nominal_bond_tools
    assert "orders_submit_order" not in nominal_bond_tools
    assert "orders_execute_order" not in nominal_bond_tools
    assert "orders_confirm_order" not in nominal_bond_tools
```

This repeats part of the generic loop intentionally so a future accidental tool addition fails with an obvious nominal-bond-specific assertion.

- [ ] **Step 4: Run focused tests and verify expected failure**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_basket_universes_have_expected_symbols tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_agents_receive_distinct_tool_surfaces -q
```

Expected:

```text
FAILED ... test_basket_universes_have_expected_symbols
```

The failure should show the current old nominal bond list:

```text
["SHY", "IEF", "TLT", "GOVT", "VGIT"]
```

The tool-surface test may still pass because the current nominal bond tool surface is already correct. Do not proceed unless the universe failure is for the expected old-list reason.

---

### Task 2: Implement Approved Nominal Bond Universe

**Files:**

- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- Test: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Replace the nominal bond universe**

In `BASKET_UNIVERSES`, replace:

```python
    "nominal_bond": ["SHY", "IEF", "TLT", "GOVT", "VGIT"],
```

with:

```python
    "nominal_bond": [
        "SGOV",
        "BIL",
        "SHV",
        "SHY",
        "VGSH",
        "SCHO",
        "IEI",
        "IEF",
        "VGIT",
        "SCHR",
        "GOVT",
        "TLH",
        "TLT",
        "VGLT",
        "EDV",
        "ZROZ",
    ],
```

- [ ] **Step 2: Confirm nominal bond still receives no news tools**

Inspect `basket_agent_tools` and keep it exactly equivalent to:

```python
def basket_agent_tools(basket_id: str) -> list[ToolDefinition]:
    tools = [
        BuiltinTools.market.load_history_tables_summary(),
        BuiltinTools.market.last_price(),
    ]
    if basket_id in {"commodity", "tips"}:
        tools.append(BuiltinTools.news.alpaca_news())
    return tools
```

Do not add `nominal_bond` to the news-enabled set.

- [ ] **Step 3: Run focused tests and verify pass**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_basket_universes_have_expected_symbols tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_agents_receive_distinct_tool_surfaces -q
```

Expected:

```text
2 passed
```

- [ ] **Step 4: Commit universe change**

Run:

```powershell
git add lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: expand nominal bond basket universe"
```

Expected:

```text
[feature/commodity-basket-universe-expansion <sha>] feat: expand nominal bond basket universe
```

---

### Task 3: Add Failing Tests For Nominal Bond Prompt Behavior

**Files:**

- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add system prompt test for nominal bonds**

In `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`, immediately after `test_tips_basket_prompt_is_rank_first_defensive_and_duration_aware`, add:

```python
def test_nominal_bond_basket_prompt_is_rank_first_and_duration_aware():
    _module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()

    created = {agent["name"]: agent for agent in agent_manager.created}
    prompt = created["nominal_bond_basket_agent"]["system_prompt"].lower()
    for required_phrase in (
        "computed ranking evidence",
        "primary selection evidence",
        "u.s. nominal treasury duration-selection basket",
        "not a corporate-bond or credit-risk basket",
        "cash-like",
        "ultra-short",
        "short-term",
        "intermediate-term",
        "broad-curve",
        "long-term",
        "extended-duration",
        "zero-coupon",
        "maturity / duration metadata",
        "do not select the lowest-volatility symbol by default",
        "do not select the longest-duration symbol by default",
        "not as default safe assets",
        "high interest-rate sensitivity",
    ):
        assert required_phrase in prompt
    for forbidden_phrase in (
        "always choose sgov",
        "always choose bil",
        "always choose shy",
        "always choose tlt",
        "default to sgov",
        "default to bil",
        "default to tlt",
        "default to zroz",
        "long duration is always correct",
        "weak growth automatically means long bonds",
        "capital preservation overrides ranking evidence",
        "news is required",
        "use fred",
        "classify the macro regime",
    ):
        assert forbidden_phrase not in prompt
```

- [ ] **Step 2: Add task prompt test for nominal bonds**

Immediately after `test_tips_basket_task_prompt_is_rank_first_and_news_secondary`, add:

```python
def test_nominal_bond_basket_task_prompt_is_rank_first_and_duration_aware():
    module, _strategy_class = load_strategy_module()

    prompt = module.basket_agent_task_prompt("nominal_bond").lower()

    for required_phrase in (
        "for nominal bonds",
        "computed ranking evidence first",
        "select one symbol from candidate_symbols",
        "target_weight is positive",
        "ranking evidence",
        "maturity / duration exposure",
        "fits the nominal bond basket role",
        "candidate_symbols must copy the assigned basket_symbols exactly",
    ):
        assert required_phrase in prompt
    for forbidden_phrase in (
        "use news first",
        "use fred",
        "always choose",
        "default to",
        "classify the macro regime",
    ):
        assert forbidden_phrase not in prompt
```

- [ ] **Step 3: Add on-iteration task prompt assertion for nominal bonds**

In `test_on_trading_iteration_runs_agents_in_expected_order_and_context`, after the existing TIPS context/task assertions:

```python
    tips_task = agent_manager["tips_basket_agent"].calls[0]["task_prompt"].lower()
    assert "computed ranking evidence first" in tips_task
    assert "news only" in tips_task
    assert "long-duration candidate" in tips_task
```

add:

```python
    nominal_bond_context = agent_manager["nominal_bond_basket_agent"].calls[0]["context"]
    assert nominal_bond_context["basket_id"] == "nominal_bond"
    assert nominal_bond_context["basket_symbols"] == module.BASKET_UNIVERSES["nominal_bond"]
    assert nominal_bond_context["macro_allocation_report"] == macro_report

    nominal_bond_task = agent_manager["nominal_bond_basket_agent"].calls[0]["task_prompt"].lower()
    assert "computed ranking evidence first" in nominal_bond_task
    assert "maturity / duration exposure" in nominal_bond_task
    assert "fits the nominal bond basket role" in nominal_bond_task
```

- [ ] **Step 4: Run prompt tests and verify expected failure**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_nominal_bond_basket_prompt_is_rank_first_and_duration_aware tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_nominal_bond_basket_task_prompt_is_rank_first_and_duration_aware tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_on_trading_iteration_runs_agents_in_expected_order_and_context -q
```

Expected:

```text
FAILED ... test_nominal_bond_basket_prompt_is_rank_first_and_duration_aware
FAILED ... test_nominal_bond_basket_task_prompt_is_rank_first_and_duration_aware
```

The on-iteration test may also fail until `basket_agent_task_prompt("nominal_bond")` is implemented. The failures should show the current generic nominal bond prompt lacks duration-selection wording.

---

### Task 4: Implement Nominal Bond Prompt Branches

**Files:**

- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- Test: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add nominal bond branch to `basket_agent_system_prompt`**

In `basket_agent_system_prompt`, after the existing `if basket_id == "tips":` branch and before `return base`, add:

```python
    if basket_id == "nominal_bond":
        return (
            base
            + " For nominal bond selection, use computed ranking evidence as the primary selection evidence. "
            "This basket is a U.S. nominal Treasury duration-selection basket, not a corporate-bond or "
            "credit-risk basket. The main decision is maturity / duration exposure: cash-like or ultra-short, "
            "short-term, intermediate-term, broad-curve, long-term, or extended-duration / zero-coupon Treasury "
            "exposure. Maturity / duration metadata: SGOV, BIL, and SHV are cash-like or ultra-short Treasury "
            "exposure; SHY, VGSH, and SCHO are short-term Treasury exposure; IEI, IEF, VGIT, and SCHR are "
            "intermediate-term Treasury exposure; GOVT is broad-curve Treasury exposure; TLH, TLT, and VGLT are "
            "long-term Treasury exposure; EDV and ZROZ are extended-duration or zero-coupon Treasury exposure. "
            "Use the maturity / duration metadata only to understand what each symbol represents. "
            "Do not select the lowest-volatility symbol by default. Do not select the longest-duration symbol by "
            "default. When target_weight is positive, choose the Treasury exposure that best matches the ranking "
            "evidence. Cash-like or short-term exposure may be appropriate when ranking evidence favors low "
            "interest-rate sensitivity. Intermediate or broad-curve exposure may be appropriate when ranking "
            "evidence is balanced. Long or extended-duration exposure should be selected only when ranking evidence "
            "clearly justifies taking high interest-rate sensitivity. Treat long-duration and zero-coupon Treasury "
            "ETFs as high-volatility rate-sensitive positions, not as default safe assets."
        )
```

- [ ] **Step 2: Add nominal bond branch to `basket_agent_task_prompt`**

In `basket_agent_task_prompt`, after the existing `if basket_id == "tips":` branch and before `return base`, add:

```python
    if basket_id == "nominal_bond":
        return (
            base
            + " For nominal bonds, use computed ranking evidence first. Select one symbol from candidate_symbols "
            "when target_weight is positive. Explain the selected symbol in terms of ranking evidence, maturity / "
            "duration exposure, and why that duration choice fits the nominal bond basket role."
        )
```

- [ ] **Step 3: Run focused prompt tests and verify pass**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_nominal_bond_basket_prompt_is_rank_first_and_duration_aware tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_nominal_bond_basket_task_prompt_is_rank_first_and_duration_aware tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_on_trading_iteration_runs_agents_in_expected_order_and_context -q
```

Expected:

```text
3 passed
```

- [ ] **Step 4: Run prompt boundary test**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_prompt_boundaries_are_short_and_role_specific -q
```

Expected:

```text
1 passed
```

If this fails, adjust only the nominal-bond prompt wording while preserving:

- rank-first evidence;
- neutral duration metadata;
- no lowest-volatility default;
- no longest-duration default;
- no macro-regime classification;
- no news/FRED requirement.

- [ ] **Step 5: Commit prompt changes**

Run:

```powershell
git add lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: add nominal bond duration prompt"
```

Expected:

```text
[feature/commodity-basket-universe-expansion <sha>] feat: add nominal bond duration prompt
```

---

### Task 5: Verify Nominal Bond Historical Data Path

**Files:**

- No production code changes.

- [ ] **Step 1: Verify Yahoo-style daily data for all approved symbols**

Run:

```powershell
@'
import yfinance as yf

symbols = [
    "SGOV",
    "BIL",
    "SHV",
    "SHY",
    "VGSH",
    "SCHO",
    "IEI",
    "IEF",
    "VGIT",
    "SCHR",
    "GOVT",
    "TLH",
    "TLT",
    "VGLT",
    "EDV",
    "ZROZ",
]

missing = []
for symbol in symbols:
    df = yf.download(
        symbol,
        start="2024-09-05",
        end="2024-09-11",
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    rows = len(df)
    print(f"{symbol}: {rows} rows")
    if rows == 0:
        missing.append(symbol)

if missing:
    raise SystemExit(f"Missing data for: {missing}")
'@ | D:\Lumibot\.venv\Scripts\python.exe -
```

Expected:

```text
SGOV: <positive rows> rows
BIL: <positive rows> rows
SHV: <positive rows> rows
SHY: <positive rows> rows
VGSH: <positive rows> rows
SCHO: <positive rows> rows
IEI: <positive rows> rows
IEF: <positive rows> rows
VGIT: <positive rows> rows
SCHR: <positive rows> rows
GOVT: <positive rows> rows
TLH: <positive rows> rows
TLT: <positive rows> rows
VGLT: <positive rows> rows
EDV: <positive rows> rows
ZROZ: <positive rows> rows
```

The process must exit with code `0`.

- [ ] **Step 2: Verify history summary tool handles the expanded nominal bond universe**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py -q
```

Expected:

```text
pytest exits with code 0.
```

- [ ] **Step 3: If any ticker fails**

If any symbol has no data, stop implementation and record the exact failed symbol and error. Do not substitute a symbol without updating the spec or getting explicit user approval.

---

### Task 6: Run Focused Regression Tests And Lint

**Files:**

- No source changes expected unless tests reveal a bug.

- [ ] **Step 1: Run the focused mock quadrant strategy tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected:

```text
All tests in tests/test_ai_trading_team_mock_growth_inflation_quadrant.py pass.
```

- [ ] **Step 2: Run related agent tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py tests\test_ai_trading_team_growth_execution_test.py tests\test_agent_manager.py tests\test_agent_replay_ui_formatters.py -q
```

Expected:

```text
pytest exits with code 0.
```

- [ ] **Step 3: Run ruff on changed files**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 4: Commit only if regression fixes were needed**

If Step 1, Step 2, or Step 3 required additional code/test edits, run:

```powershell
git add lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "test: stabilize nominal bond basket coverage"
```

If no additional edits were needed, do not create an empty commit.

---

### Task 7: Run One-Day Benchmark And Trace/UI Smoke Test

**Files:**

- Create: `docs/superpowers/notes/2026-08-14-nominal-bond-basket-universe-duration-prompt-validation.md`

- [ ] **Step 1: Confirm API file exists without printing secrets**

Run:

```powershell
Test-Path D:\Lumibot\project_notes\API.txt
```

Expected:

```text
True
```

- [ ] **Step 2: Run a one-day benchmark where nominal bond is active**

Use `2024-09-10` because the current seeded mock classifier maps it to a regime with nominal bond target weight `0.50`.

Run:

```powershell
cd D:\Lumibot
$env:AI_TRADING_TEAM_MODEL="openai/gpt-5.6-luna"
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-10 --end 2024-09-10 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file project_notes\API.txt
```

Expected:

```text
The benchmark completes without Python traceback.
The output identifies a new mock-growth-inflation-quadrant artifact.
The run status is passed, or any failure is clearly unrelated to nominal bond universe/prompt changes.
```

- [ ] **Step 3: Inspect latest artifact for nominal bond trace evidence**

Run:

```powershell
$latest = Get-ChildItem D:\Lumibot\artifacts\ai_trading_team_example_benchmarks -Directory |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
$artifact = Join-Path $latest.FullName "mock-growth-inflation-quadrant"
$artifact
rg -n '"agent_name": "nominal_bond_basket_agent"|nominal_bond_basket_agent|SGOV|BIL|SHV|VGSH|SCHO|SCHR|TLH|VGLT|EDV|ZROZ|market_load_history_tables_summary' $artifact -g "*.json" -g "*.jsonl"
```

Expected:

```text
nominal_bond_basket_agent appears in the trace.
At least some approved nominal bond universe symbols appear in the nominal bond agent input/context or tool result.
market_load_history_tables_summary appears for the nominal bond agent when target_weight is positive.
```

- [ ] **Step 4: Inspect the nominal bond report**

Search the artifact for the nominal bond report:

```powershell
rg -n 'nominal_bond_basket_report|selected_symbol|target_weight|reason_brief' $artifact -g "*.json" -g "*.jsonl" -C 3
```

Expected:

```text
If nominal bond target_weight is positive, selected_symbol is one of:
SGOV, BIL, SHV, SHY, VGSH, SCHO, IEI, IEF, VGIT, SCHR, GOVT, TLH, TLT, VGLT, EDV, ZROZ.
The reason mentions ranking evidence and/or duration exposure.
```

- [ ] **Step 5: Start replay UI smoke test**

Run:

```powershell
cd D:\Lumibot
D:\Lumibot\.venv\Scripts\python.exe scripts\agent_trace_ui.py
```

Expected:

```text
The server starts and prints a local 127.0.0.1 URL.
```

Open the UI and select:

```text
Strategy: AITradingTeamMockGrowthInflationQuadrantStrategy
Backtest run: the newest run from Step 2
System run: 2024-09-10
```

Expected:

```text
Workflow graph displays all expected agents.
nominal_bond_basket_agent appears in the workflow.
Input Material for nominal_bond_basket_agent shows the expanded nominal bond basket symbols.
Available tools for nominal_bond_basket_agent show market_load_history_tables_summary and market_last_price only.
Tool calls and final summary render without UI errors.
```

Stop the UI server after inspection.

- [ ] **Step 6: Write validation note**

Create `docs/superpowers/notes/2026-08-14-nominal-bond-basket-universe-duration-prompt-validation.md`:

````markdown
# Nominal Bond Basket Universe Duration Prompt Validation

## Commands

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py tests\test_ai_trading_team_growth_execution_test.py tests\test_agent_manager.py tests\test_agent_replay_ui_formatters.py -q
D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
```

## Data Availability

- Symbols checked:
- Command:
- Result:

## Backtest

- Model:
- Date:
- Command:
- Artifact:
- Status:

## Trace Acceptance Check

- [ ] nominal_bond_basket_agent appeared
- [ ] Expanded 16-symbol nominal bond universe appeared
- [ ] nominal_bond_basket_agent used market_load_history_tables_summary when active
- [ ] nominal_bond_basket_agent did not receive alpaca_news
- [ ] nominal_bond_basket_agent did not receive FRED tools
- [ ] nominal_bond_basket_agent did not receive trading tools
- [ ] Selected symbol, if active, came from approved universe
- [ ] Reason mentioned ranking evidence and/or duration exposure
- [ ] Portfolio decision agent reached
- [ ] Execution agent reached
- [ ] Replay UI discovered the run

## Notes

- Data-load warnings:
- Benchmark warnings:
- Trace/UI warnings:
- Follow-up recommendations:
```
````

- [ ] **Step 7: Commit validation note**

Run:

```powershell
git add docs\superpowers\notes\2026-08-14-nominal-bond-basket-universe-duration-prompt-validation.md
git commit -m "docs: validate nominal bond basket expansion"
```

Expected:

```text
[feature/commodity-basket-universe-expansion <sha>] docs: validate nominal bond basket expansion
```

---

## Final Verification

- [ ] **Step 1: Show recent commits**

Run:

```powershell
git log --oneline -8
```

Expected:

```text
Recent commits include:
feat: expand nominal bond basket universe
feat: add nominal bond duration prompt
docs: validate nominal bond basket expansion
```

- [ ] **Step 2: Confirm no unintended files are staged**

Run:

```powershell
git status --short --branch
```

Expected:

```text
## feature/commodity-basket-universe-expansion
```

No source/test files should remain modified. Benchmark artifacts should be ignored or intentionally excluded.

- [ ] **Step 3: Summarize implementation evidence**

Final implementation report must include:

```text
Changed files
Unit test command and result
Related regression test command and result
Ruff command and result
Yahoo-style data availability command and result
One-day benchmark command, model, date, and result
Trace/UI inspection result
Any warnings or residual risks
```

Do not claim completion unless the commands were run and their outputs were read.

## Final Acceptance Checklist

- [ ] `BASKET_UNIVERSES["nominal_bond"]` is exactly the approved 16-symbol list.
- [ ] `nominal_bond_basket_agent` receives `market_load_history_tables_summary`.
- [ ] `nominal_bond_basket_agent` receives `market_last_price`.
- [ ] `nominal_bond_basket_agent` does not receive `alpaca_news`.
- [ ] `nominal_bond_basket_agent` does not receive FRED tools.
- [ ] `nominal_bond_basket_agent` does not receive trading tools.
- [ ] Nominal bond system prompt is rank-first and duration-aware.
- [ ] Nominal bond system prompt includes neutral maturity / duration metadata.
- [ ] Nominal bond system prompt avoids lowest-volatility and longest-duration defaults.
- [ ] Nominal bond task prompt asks for ranking evidence, duration exposure, and fit to basket role.
- [ ] Existing equity, commodity, and TIPS tool surfaces remain unchanged.
- [ ] Existing macro weights remain unchanged.
- [ ] Approved nominal bond symbols load daily history for the representative 2024 window.
- [ ] One-day active-nominal-bond benchmark does not fail because of this feature.
- [ ] Replay UI discovery still works for the new run.
