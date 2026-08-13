# TIPS Basket Universe News Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the mock growth/inflation quadrant strategy's TIPS basket to six symbols, give `tips_basket_agent` conditional news access, and make its prompts rank-first, defensive, and duration-aware.

**Architecture:** Keep the existing strategy structure unchanged: macro allocation agent -> four basket agents -> portfolio decision agent -> execution agent. Modify only the TIPS basket universe, TIPS basket tool surface, TIPS-specific prompt branches, and related tests/verification. Reuse the existing `market_load_history_tables_summary`, `market_last_price`, and `alpaca_news` built-in tools.

**Tech Stack:** Python, pytest, ruff, existing LumiBot agent built-ins, existing mock growth/inflation quadrant strategy, existing benchmark runner.

---

## Source Spec

Implement:

```text
docs/superpowers/specs/2026-08-13-tips-basket-universe-news-prompt-design.md
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
- Creates `tips_basket_agent` with the model-facing tool surface and prompts.

Modify:

```text
tests/test_ai_trading_team_mock_growth_inflation_quadrant.py
```

Responsibilities:

- Verifies approved basket universes.
- Verifies agent tool permissions.
- Verifies prompt boundaries and TIPS-specific prompt wording.
- Verifies task prompt wording passed through `on_trading_iteration`.

No new production files are required.

## Implementation Notes

- Do not change macro regime classification.
- Do not change basket weights.
- Do not change portfolio decision, planner, execution, or replay UI behavior.
- Do not add FRED, real-yield, breakeven-inflation, or CPI tools.
- Do not make `alpaca_news` mandatory. If credentials are missing, existing unavailable-tool behavior is acceptable.
- Preserve flat `basket_symbols` context. Do not add model-facing hard labels like "short", "full curve", or "long" to context.

---

### Task 1: Add Failing Tests For TIPS Universe And Tool Surface

**Files:**

- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add an `expected_tips_universe` helper**

In `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`, immediately after `expected_commodity_universe()`, add:

```python
def expected_tips_universe():
    return [
        "VTIP",
        "STIP",
        "SCHP",
        "TIP",
        "SPIP",
        "LTPZ",
    ]
```

- [ ] **Step 2: Update the basket universe assertion**

In `test_basket_universes_have_expected_symbols`, replace:

```python
        "tips": ["TIP", "SCHP", "VTIP", "STIP", "LTPZ"],
```

with:

```python
        "tips": expected_tips_universe(),
```

After the existing commodity length assertion:

```python
    assert len(module.BASKET_UNIVERSES["commodity"]) == 28
```

add:

```python
    assert len(module.BASKET_UNIVERSES["tips"]) == 6
```

- [ ] **Step 3: Update the tool surface test to require news for TIPS**

In `test_agents_receive_distinct_tool_surfaces`, replace this loop:

```python
    for basket_agent in (
        "equity_basket_agent",
        "tips_basket_agent",
        "nominal_bond_basket_agent",
    ):
        assert created[basket_agent]["include_builtin_tools"] is False
        assert created_tool_names(created[basket_agent]) == {
            "market_load_history_tables_summary",
            "market_last_price",
        }
    assert created["commodity_basket_agent"]["include_builtin_tools"] is False
    assert created_tool_names(created["commodity_basket_agent"]) == {
        "market_load_history_tables_summary",
        "market_last_price",
        "alpaca_news",
    }
```

with:

```python
    for basket_agent in (
        "equity_basket_agent",
        "nominal_bond_basket_agent",
    ):
        assert created[basket_agent]["include_builtin_tools"] is False
        assert created_tool_names(created[basket_agent]) == {
            "market_load_history_tables_summary",
            "market_last_price",
        }
    for news_enabled_basket_agent in (
        "commodity_basket_agent",
        "tips_basket_agent",
    ):
        assert created[news_enabled_basket_agent]["include_builtin_tools"] is False
        assert created_tool_names(created[news_enabled_basket_agent]) == {
            "market_load_history_tables_summary",
            "market_last_price",
            "alpaca_news",
        }
```

- [ ] **Step 4: Run the focused tests and verify failure**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_basket_universes_have_expected_symbols tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_agents_receive_distinct_tool_surfaces -q
```

Expected:

```text
FAILED ... test_basket_universes_have_expected_symbols
FAILED ... test_agents_receive_distinct_tool_surfaces
```

The failures should show the old five-symbol TIPS list and missing `alpaca_news` on `tips_basket_agent`.

Do not proceed unless these tests fail for the expected reasons.

---

### Task 2: Implement TIPS Universe And Tool Surface

**Files:**

- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- Test: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Update the TIPS universe**

In `BASKET_UNIVERSES`, replace:

```python
    "tips": ["TIP", "SCHP", "VTIP", "STIP", "LTPZ"],
```

with:

```python
    "tips": ["VTIP", "STIP", "SCHP", "TIP", "SPIP", "LTPZ"],
```

- [ ] **Step 2: Give TIPS conditional news access**

In `basket_agent_tools`, replace:

```python
    if basket_id == "commodity":
        tools.append(BuiltinTools.news.alpaca_news())
```

with:

```python
    if basket_id in {"commodity", "tips"}:
        tools.append(BuiltinTools.news.alpaca_news())
```

- [ ] **Step 3: Run the focused tests and verify pass**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_basket_universes_have_expected_symbols tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_agents_receive_distinct_tool_surfaces -q
```

Expected:

```text
2 passed
```

- [ ] **Step 4: Commit universe and tool surface changes**

Run:

```powershell
git add lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: expand tips basket tool surface"
```

Expected:

```text
[feature/commodity-basket-universe-expansion <sha>] feat: expand tips basket tool surface
```

---

### Task 3: Add Failing Tests For TIPS Prompt Behavior

**Files:**

- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add system prompt tests for TIPS**

In `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`, immediately after `test_commodity_basket_prompt_is_rank_first_and_category_neutral`, add:

```python
def test_tips_basket_prompt_is_rank_first_defensive_and_duration_aware():
    _module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()

    created = {agent["name"]: agent for agent in agent_manager.created}
    prompt = created["tips_basket_agent"]["system_prompt"].lower()
    for required_phrase in (
        "computed ranking evidence",
        "primary selection evidence",
        "inflation-protected defensive exposure",
        "protects purchasing power",
        "controlling drawdown",
        "interest-rate sensitivity",
        "short-duration tips",
        "full-curve tips",
        "long-duration tips",
        "not as the default safe choice",
        "news only as secondary evidence",
        "generic inflation headlines alone",
    ):
        assert required_phrase in prompt
    for forbidden_phrase in (
        "always choose vtip",
        "always choose stip",
        "default to ltpz",
        "long-duration tips are safest",
        "use news first",
        "keyword search",
        "real yield tool",
        "breakeven inflation tool",
    ):
        assert forbidden_phrase not in prompt
```

- [ ] **Step 2: Add task prompt tests for TIPS**

Immediately after `test_basket_task_prompt_requires_candidate_symbols_to_copy_assigned_universe`, add:

```python
def test_tips_basket_task_prompt_is_rank_first_and_news_secondary():
    module, _strategy_class = load_strategy_module()

    prompt = module.basket_agent_task_prompt("tips").lower()

    for required_phrase in (
        "for tips",
        "computed ranking evidence first",
        "rank evidence clearly favors",
        "select it directly",
        "news only",
        "close, conflicting, incomplete, stale",
        "long-duration candidate",
        "candidate_symbols must copy the assigned basket_symbols exactly",
    ):
        assert required_phrase in prompt
    for forbidden_phrase in (
        "use news first",
        "always choose",
        "keyword search",
    ):
        assert forbidden_phrase not in prompt
```

- [ ] **Step 3: Add an on-iteration task prompt assertion for TIPS**

In `test_on_trading_iteration_runs_agents_in_expected_order_and_context`, after the existing commodity context assertions:

```python
    commodity_context = agent_manager["commodity_basket_agent"].calls[0]["context"]
    assert commodity_context["basket_id"] == "commodity"
    assert commodity_context["basket_symbols"] == module.BASKET_UNIVERSES["commodity"]
    assert commodity_context["target_weight"] == 0.0
    assert commodity_context["macro_allocation_report"] == macro_report
```

add:

```python
    tips_context = agent_manager["tips_basket_agent"].calls[0]["context"]
    assert tips_context["basket_id"] == "tips"
    assert tips_context["basket_symbols"] == module.BASKET_UNIVERSES["tips"]
    assert tips_context["macro_allocation_report"] == macro_report

    tips_task = agent_manager["tips_basket_agent"].calls[0]["task_prompt"].lower()
    assert "computed ranking evidence first" in tips_task
    assert "news only" in tips_task
    assert "long-duration candidate" in tips_task
```

- [ ] **Step 4: Run prompt tests and verify failure**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_tips_basket_prompt_is_rank_first_defensive_and_duration_aware tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_tips_basket_task_prompt_is_rank_first_and_news_secondary tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_on_trading_iteration_runs_agents_in_expected_order_and_context -q
```

Expected:

```text
FAILED ... test_tips_basket_prompt_is_rank_first_defensive_and_duration_aware
FAILED ... test_tips_basket_task_prompt_is_rank_first_and_news_secondary
```

The on-iteration test may also fail until `basket_agent_task_prompt("tips")` is implemented. Do not proceed unless the failure shows the current generic TIPS prompt lacks the new TIPS-specific wording.

---

### Task 4: Implement TIPS Prompt Branches

**Files:**

- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- Test: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Update `basket_agent_system_prompt`**

Replace the current function:

```python
def basket_agent_system_prompt(basket_id: str, symbols: str) -> str:
    base = (
        f"{basket_id.replace('_', ' ').title()} basket role: stay inside the assigned basket "
        f"({symbols}). Select one symbol when active, or report inactive when its target weight is zero. "
        "Return basket_id, selected_symbol, status, and reason_brief. Do not place orders."
    )
    if basket_id != "commodity":
        return base
    return (
        base
        + " For commodity selection, use computed ranking evidence as the primary selection evidence. "
        "If one symbol is clearly stronger across ranking evidence, select it without requiring news. "
        "Use news only when ranking evidence is close, conflicting, incomplete, or stale. "
        "Do not prefer broad or diversified commodity symbols merely because they look safer. "
        "Do not prefer or avoid symbols based on ticker-name intuition."
    )
```

with:

```python
def basket_agent_system_prompt(basket_id: str, symbols: str) -> str:
    base = (
        f"{basket_id.replace('_', ' ').title()} basket role: stay inside the assigned basket "
        f"({symbols}). Select one symbol when active, or report inactive when its target weight is zero. "
        "Return basket_id, selected_symbol, status, and reason_brief. Do not place orders."
    )
    if basket_id == "commodity":
        return (
            base
            + " For commodity selection, use computed ranking evidence as the primary selection evidence. "
            "If one symbol is clearly stronger across ranking evidence, select it without requiring news. "
            "Use news only when ranking evidence is close, conflicting, incomplete, or stale. "
            "Do not prefer broad or diversified commodity symbols merely because they look safer. "
            "Do not prefer or avoid symbols based on ticker-name intuition."
        )
    if basket_id == "tips":
        return (
            base
            + " For TIPS selection, use computed ranking evidence as the primary selection evidence. "
            "This basket exists to provide inflation-protected defensive exposure, especially when macro allocation "
            "gives TIPS a positive target weight. Choose the TIPS exposure that best protects purchasing power while "
            "controlling drawdown and interest-rate sensitivity. Prefer short-duration TIPS exposure when the evidence "
            "favors stable inflation defense and lower volatility. Use full-curve TIPS exposure when it offers a better "
            "balance of inflation protection, liquidity, and ranking evidence. Treat long-duration TIPS as a "
            "higher-volatility real-rate position, not as the default safe choice. Select long-duration TIPS only when "
            "ranking evidence and supporting context clearly justify taking duration risk. Use news only as secondary "
            "evidence when ranking evidence is close, conflicting, incomplete, stale, or when long-duration TIPS looks "
            "unusually attractive. Do not use generic inflation headlines alone to justify long-duration TIPS."
        )
    return base
```

- [ ] **Step 2: Update `basket_agent_task_prompt`**

Replace the current function:

```python
def basket_agent_task_prompt(basket_id: str) -> str:
    base = (
        "Review only the assigned basket and return one JSON object with basket_id, "
        "target_weight, status, candidate_symbols, selected_symbol, and reason_brief. "
        "candidate_symbols must copy the assigned basket_symbols exactly; "
        "do not replace it with a shortlist."
    )
    if basket_id != "commodity":
        return base
    return (
        base
        + " For commodity, use computed ranking evidence first. If rank evidence clearly favors one symbol, "
        "select it directly. Use news only when leading candidates are close, conflicting, or incomplete."
    )
```

with:

```python
def basket_agent_task_prompt(basket_id: str) -> str:
    base = (
        "Review only the assigned basket and return one JSON object with basket_id, "
        "target_weight, status, candidate_symbols, selected_symbol, and reason_brief. "
        "candidate_symbols must copy the assigned basket_symbols exactly; "
        "do not replace it with a shortlist."
    )
    if basket_id == "commodity":
        return (
            base
            + " For commodity, use computed ranking evidence first. If rank evidence clearly favors one symbol, "
            "select it directly. Use news only when leading candidates are close, conflicting, or incomplete."
        )
    if basket_id == "tips":
        return (
            base
            + " For TIPS, use computed ranking evidence first. If rank evidence clearly favors one defensive TIPS "
            "candidate, select it directly. Use news only when leading candidates are close, conflicting, incomplete, "
            "stale, or when a long-duration candidate requires confirmation."
        )
    return base
```

- [ ] **Step 3: Run focused prompt tests and verify pass**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_tips_basket_prompt_is_rank_first_defensive_and_duration_aware tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_tips_basket_task_prompt_is_rank_first_and_news_secondary tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_on_trading_iteration_runs_agents_in_expected_order_and_context -q
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

If this fails because of an existing forbidden phrase, adjust the TIPS prompt without weakening these required meanings:

- rank-first;
- defensive inflation-protection role;
- duration-aware;
- news-secondary;
- generic inflation headlines are not enough for long-duration TIPS.

- [ ] **Step 5: Commit prompt changes**

Run:

```powershell
git add lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: add tips basket defensive prompt"
```

Expected:

```text
[feature/commodity-basket-universe-expansion <sha>] feat: add tips basket defensive prompt
```

---

### Task 5: Verify Data Path, Test Suite, And One-Day Backtest

**Files:**

- No required code changes.
- Read/verify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- Read/verify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Verify Yahoo-style data availability for the approved TIPS universe**

Run:

```powershell
@'
import yfinance as yf

symbols = ["VTIP", "STIP", "SCHP", "TIP", "SPIP", "LTPZ"]
missing = []
for symbol in symbols:
    df = yf.download(symbol, start="2024-09-05", end="2024-09-10", auto_adjust=True, progress=False, threads=False)
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
VTIP: <positive rows> rows
STIP: <positive rows> rows
SCHP: <positive rows> rows
TIP: <positive rows> rows
SPIP: <positive rows> rows
LTPZ: <positive rows> rows
```

The command must exit with code `0`.

- [ ] **Step 2: Run the focused strategy test file**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected:

```text
pytest exits with code 0, and the summary contains no FAILED or ERROR lines.
```

- [ ] **Step 3: Run related regression tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py tests\test_agent_replay_ui_formatters.py tests\test_ai_trading_team_growth_execution_test.py tests\test_agent_manager.py tests\test_agent_alpaca_news_builtin.py -q
```

Expected:

```text
pytest exits with code 0, and the summary contains no FAILED or ERROR lines.
```

- [ ] **Step 4: Run ruff on changed files and related tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_alpaca_news_builtin.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 5: Run one-day benchmark smoke test**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-05 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file project_notes\API.txt
```

Expected:

```text
"strategy": "mock-growth-inflation-quadrant"
"status": "passed"
```

If the benchmark fails because external API/model/network credentials are unavailable, record the failure payload and verify that:

- unit tests pass;
- data path verification passed;
- the failure is not caused by the TIPS universe, TIPS tool surface, or TIPS prompt changes.

- [ ] **Step 6: Inspect the latest trace for TIPS tools**

Run:

```powershell
$latest = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$traceRoot = Join-Path $latest.FullName "mock-growth-inflation-quadrant\agent_traces"
rg -n '"agent_name": "tips_basket_agent"|"alpaca_news"|"VTIP"|"STIP"|"SCHP"|"TIP"|"SPIP"|"LTPZ"' $traceRoot
```

Expected:

```text
tips_basket_agent appears in the trace.
The TIPS universe symbols appear in the TIPS agent input/context or tool results.
alpaca_news appears in the TIPS agent available tool definitions or tool list.
```

- [ ] **Step 7: Run replay UI smoke command if the benchmark produced a trace**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe scripts\agent_trace_ui.py
```

Expected:

```text
Agent Replay server starts on localhost and the latest mock-growth-inflation-quadrant run is discoverable.
```

Stop the server after verifying discovery.

- [ ] **Step 8: Final git status check**

Run:

```powershell
git status --short --branch
```

Expected:

```text
## feature/commodity-basket-universe-expansion
```

No unstaged or uncommitted files should remain unless the benchmark generated intentionally ignored artifacts.

---

## Final Acceptance Checklist

- [ ] `BASKET_UNIVERSES["tips"]` is exactly `["VTIP", "STIP", "SCHP", "TIP", "SPIP", "LTPZ"]`.
- [ ] `tips_basket_agent` receives `market_load_history_tables_summary`, `market_last_price`, and `alpaca_news`.
- [ ] `commodity_basket_agent` still receives `alpaca_news`.
- [ ] `equity_basket_agent` and `nominal_bond_basket_agent` tool surfaces are unchanged.
- [ ] TIPS system prompt is rank-first, defensive, duration-aware, and news-secondary.
- [ ] TIPS task prompt preserves exact `candidate_symbols` behavior.
- [ ] No macro classification, basket weights, planner, execution, or replay UI logic changed.
- [ ] Approved TIPS symbols load daily history for the representative 2024 window.
- [ ] Focused and related tests pass.
- [ ] Ruff passes for changed files.
- [ ] One-day benchmark passes, or any failure is documented as unrelated to this feature.

## Implementation Order Summary

1. Write failing universe/tool-surface tests.
2. Implement universe/tool-surface changes.
3. Write failing prompt tests.
4. Implement prompt changes.
5. Verify data path, tests, ruff, benchmark, trace, and replay UI discovery.
