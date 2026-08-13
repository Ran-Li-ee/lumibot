# Commodity Basket Universe Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the mock growth/inflation quadrant strategy's commodity basket to the approved 28-symbol universe and make `commodity_basket_agent` select rank-first, with optional Alpaca news only as secondary tie-breaker evidence.

**Architecture:** Keep the strategy structure unchanged: macro allocation agent -> four basket agents -> portfolio decision agent -> execution agent. Only the commodity basket universe, commodity basket prompt, commodity basket tool surface, and corresponding tests change. No USDA, ERS, FRED, fundamentals, portfolio planner, execution, or UI schema changes are included.

**Tech Stack:** Python, pytest, Lumibot agent builtins, Yahoo-style backtest data via `yfinance`/`YahooDataBacktesting`, OpenAI model through existing benchmark runner, Agent Workflow Replay UI.

---

## Reference Spec

Use this spec as the source of truth:

```text
docs/superpowers/specs/2026-08-13-commodity-basket-universe-expansion-design.md
```

## Files And Responsibilities

Modify:

```text
lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py
```

Responsibilities:

- Define the approved 28-symbol commodity universe.
- Give only `commodity_basket_agent` conditional `alpaca_news` access.
- Make the commodity basket prompt rank-first and category-neutral.
- Keep other basket prompts and tools narrowly changed only where needed.
- Keep portfolio decision and execution behavior unchanged.

Modify:

```text
tests/test_ai_trading_team_mock_growth_inflation_quadrant.py
```

Responsibilities:

- Assert the expanded commodity universe.
- Assert other baskets remain unchanged.
- Assert commodity tool permissions include `alpaca_news`.
- Assert non-commodity basket tool surfaces remain unchanged.
- Assert commodity prompt is rank-first and has no category-default language.
- Keep existing workflow/context tests passing with the expanded universe.

No new production files are required.

---

## Task 1: Add Failing Tests For Commodity Universe And Tool Surface

**Files:**

- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add expected commodity universe helper near `created_tool_names`**

Insert this helper after `created_tool_names`:

```python
def expected_commodity_universe():
    return [
        "GLD",
        "IAU",
        "SLV",
        "CPER",
        "WEAT",
        "CORN",
        "SOYB",
        "CANE",
        "PPLT",
        "PALL",
        "DBB",
        "USO",
        "BNO",
        "UNG",
        "UGA",
        "DBE",
        "DBO",
        "DBA",
        "PDBA",
        "TAGS",
        "TILL",
        "DBC",
        "PDBC",
        "BCI",
        "GSG",
        "COMT",
        "FTGC",
        "CMDY",
    ]
```

- [ ] **Step 2: Update basket universe test to expect the expanded commodity list**

Replace `test_basket_universes_have_at_least_five_semantically_valid_symbols` with:

```python
def test_basket_universes_have_expected_symbols():
    module, _strategy_class = load_strategy_module()

    assert module.BASKET_UNIVERSES == {
        "equity": ["SPY", "QQQ", "IWM", "EEM", "FXI"],
        "commodity": expected_commodity_universe(),
        "tips": ["TIP", "SCHP", "VTIP", "STIP", "LTPZ"],
        "nominal_bond": ["SHY", "IEF", "TLT", "GOVT", "VGIT"],
    }
    assert len(module.BASKET_UNIVERSES["commodity"]) == 28
    for symbols in module.BASKET_UNIVERSES.values():
        assert len(symbols) >= 5
        assert len(symbols) == len(set(symbols))
```

- [ ] **Step 3: Update tool surface expectations**

Replace the basket-agent loop inside `test_agents_receive_distinct_tool_surfaces` with explicit assertions:

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

- [ ] **Step 4: Run focused tests and verify they fail**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_basket_universes_have_expected_symbols tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_agents_receive_distinct_tool_surfaces -q
```

Expected:

```text
FAILED ... test_basket_universes_have_expected_symbols
FAILED ... test_agents_receive_distinct_tool_surfaces
```

The first failure should show the old 5-symbol commodity list. The second should show `alpaca_news` is missing from `commodity_basket_agent`.

---

## Task 2: Add Failing Prompt Tests For Rank-First Commodity Behavior

**Files:**

- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add commodity prompt test after `test_prompt_boundaries_are_short_and_role_specific`**

Add:

```python
def test_commodity_basket_prompt_is_rank_first_and_category_neutral():
    _module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()

    created = {agent["name"]: agent for agent in agent_manager.created}
    prompt = created["commodity_basket_agent"]["system_prompt"].lower()
    for required_phrase in (
        "computed ranking evidence",
        "primary selection evidence",
        "clearly stronger",
        "without requiring news",
        "only when",
        "ranking evidence is close",
        "do not prefer broad",
        "ticker-name intuition",
    ):
        assert required_phrase in prompt
    for forbidden_phrase in (
        "default to diversified",
        "prefer broad commodity etf",
        "prefer diversified commodity",
        "broad commodity etfs are safer",
        "gold is a default",
        "avoid energy",
        "single commodities are too risky",
        "choose pdbc when uncertain",
        "choose dbc when uncertain",
    ):
        assert forbidden_phrase not in prompt
```

- [ ] **Step 2: Add basket task prompt test after `test_on_trading_iteration_runs_agents_in_expected_order_and_context`**

Add:

```python
def test_commodity_basket_task_prompt_mentions_rank_first_selection():
    module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()

    macro_report = {
        "basket_weights": {"equity": 0.0, "commodity": 1.0, "tips": 0.0, "nominal_bond": 0.0},
    }
    basket_reports = {
        "equity_basket_agent": {
            "basket_id": "equity",
            "target_weight": 0.0,
            "status": "inactive",
            "candidate_symbols": module.BASKET_UNIVERSES["equity"],
            "selected_symbol": None,
        },
        "commodity_basket_agent": {
            "basket_id": "commodity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["commodity"],
            "selected_symbol": "CPER",
        },
        "tips_basket_agent": {
            "basket_id": "tips",
            "target_weight": 0.0,
            "status": "inactive",
            "candidate_symbols": module.BASKET_UNIVERSES["tips"],
            "selected_symbol": None,
        },
        "nominal_bond_basket_agent": {
            "basket_id": "nominal_bond",
            "target_weight": 0.0,
            "status": "inactive",
            "candidate_symbols": module.BASKET_UNIVERSES["nominal_bond"],
            "selected_symbol": None,
        },
    }
    execution_plan = {"schema_version": 1, "intent": "hold", "orders": []}

    agent_manager.summaries["macro_allocation_agent"] = _json_summary(macro_report)
    for agent_name, report in basket_reports.items():
        agent_manager.summaries[agent_name] = _json_summary(report)
    agent_manager.summaries["portfolio_decision_agent"] = _json_summary({"execution_plan": execution_plan})
    agent_manager.tool_calls["portfolio_decision_agent"] = ["target_portfolio_to_execution_plan"]
    agent_manager.planner_results["portfolio_decision_agent"] = {"execution_plan": execution_plan}

    strategy.on_trading_iteration()

    commodity_task = agent_manager["commodity_basket_agent"].calls[0]["task_prompt"].lower()
    assert "computed ranking evidence" in commodity_task
    assert "news only" in commodity_task
    assert "close, conflicting, or incomplete" in commodity_task
```

- [ ] **Step 3: Run the new prompt tests and verify they fail**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_commodity_basket_prompt_is_rank_first_and_category_neutral tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_commodity_basket_task_prompt_mentions_rank_first_selection -q
```

Expected:

```text
FAILED ... test_commodity_basket_prompt_is_rank_first_and_category_neutral
FAILED ... test_commodity_basket_task_prompt_mentions_rank_first_selection
```

The failures should be missing required rank-first phrases.

---

## Task 3: Implement Commodity Universe Expansion And Tool Surface

**Files:**

- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Replace the commodity universe**

Replace the current `BASKET_UNIVERSES` commodity entry with:

```python
    "commodity": [
        "GLD",
        "IAU",
        "SLV",
        "CPER",
        "WEAT",
        "CORN",
        "SOYB",
        "CANE",
        "PPLT",
        "PALL",
        "DBB",
        "USO",
        "BNO",
        "UNG",
        "UGA",
        "DBE",
        "DBO",
        "DBA",
        "PDBA",
        "TAGS",
        "TILL",
        "DBC",
        "PDBC",
        "BCI",
        "GSG",
        "COMT",
        "FTGC",
        "CMDY",
    ],
```

- [ ] **Step 2: Add a helper for basket tools**

Add this function near `BASKET_AGENT_NAMES`:

```python
def basket_agent_tools(basket_id: str) -> list[ToolDefinition]:
    tools = [
        BuiltinTools.market.load_history_tables_summary(),
        BuiltinTools.market.last_price(),
    ]
    if basket_id == "commodity":
        tools.append(BuiltinTools.news.alpaca_news())
    return tools
```

- [ ] **Step 3: Use the helper in `initialize`**

Replace:

```python
                tools=[
                    BuiltinTools.market.load_history_tables_summary(),
                    BuiltinTools.market.last_price(),
                ],
```

with:

```python
                tools=basket_agent_tools(basket_id),
```

- [ ] **Step 4: Run focused tool/universe tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_basket_universes_have_expected_symbols tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_agents_receive_distinct_tool_surfaces -q
```

Expected:

```text
2 passed
```

---

## Task 4: Implement Commodity Rank-First Prompts

**Files:**

- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add basket system prompt helper**

Add this function after `basket_agent_tools`:

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

- [ ] **Step 2: Use the helper in `initialize`**

Replace the current basket `system_prompt=(...)` block with:

```python
                system_prompt=basket_agent_system_prompt(basket_id, symbols),
```

- [ ] **Step 3: Add basket task prompt helper**

Add this function after `basket_agent_system_prompt`:

```python
def basket_agent_task_prompt(basket_id: str) -> str:
    base = (
        "Review only the assigned basket and return one JSON object with basket_id, "
        "target_weight, status, candidate_symbols, selected_symbol, and reason_brief."
    )
    if basket_id != "commodity":
        return base
    return (
        base
        + " For commodity, use computed ranking evidence first. If rank evidence clearly favors one symbol, "
        "select it directly. Use news only when leading candidates are close, conflicting, or incomplete."
    )
```

- [ ] **Step 4: Use the task prompt helper in `on_trading_iteration`**

Replace:

```python
                    task_prompt=(
                        "Review only the assigned basket and return one JSON object with basket_id, "
                        "target_weight, status, candidate_symbols, selected_symbol, and reason_brief."
                    ),
```

with:

```python
                    task_prompt=basket_agent_task_prompt(basket_id),
```

- [ ] **Step 5: Run focused prompt tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_commodity_basket_prompt_is_rank_first_and_category_neutral tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_commodity_basket_task_prompt_mentions_rank_first_selection tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_prompt_boundaries_are_short_and_role_specific -q
```

Expected:

```text
3 passed
```

---

## Task 5: Update Existing Workflow Expectations

**Files:**

- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Search for stale old commodity literals**

Run:

```powershell
rg -n '"GLD", "SLV", "DBC", "PDBC", "GSG"|market_load_history_tables_summary",\s*"market_last_price"|commodity"\]: \[" tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
```

Expected:

```text
No stale exact commodity-list assertions remain.
```

If the `rg` command prints stale old-list assertions, replace those assertions with `expected_commodity_universe()`.

- [ ] **Step 2: Run all mock quadrant unit tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected:

```text
All tests in tests/test_ai_trading_team_mock_growth_inflation_quadrant.py pass.
```

- [ ] **Step 3: Commit tests and strategy changes**

Run:

```powershell
git add lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: expand commodity basket universe"
```

Expected:

```text
[branch ...] feat: expand commodity basket universe
```

---

## Task 6: Validate Yahoo-Style Historical Data Availability

**Files:**

- No code files changed.

- [ ] **Step 1: Run local data availability check**

Run:

```powershell
@'
import yfinance as yf

symbols = [
    "GLD", "IAU", "SLV", "CPER", "WEAT", "CORN", "SOYB", "CANE",
    "PPLT", "PALL", "DBB",
    "USO", "BNO", "UNG", "UGA", "DBE", "DBO",
    "DBA", "PDBA", "TAGS", "TILL",
    "DBC", "PDBC", "BCI", "GSG", "COMT", "FTGC", "CMDY",
]

failures = []
for symbol in symbols:
    df = yf.download(symbol, start="2024-09-01", end="2024-10-31", progress=False, auto_adjust=False, threads=False)
    if df.empty:
        failures.append((symbol, "empty"))
        print(f"{symbol}: FAIL empty")
        continue
    print(f"{symbol}: OK rows={len(df)} first={df.index.min().date()} last={df.index.max().date()}")

if failures:
    raise SystemExit(f"Data availability failures: {failures}")
'@ | D:\Lumibot\.venv\Scripts\python.exe -
```

Expected:

```text
Every symbol prints OK with non-zero rows.
The process exits with code 0.
```

- [ ] **Step 2: Save validation notes only if a symbol fails**

If any symbol fails, do not proceed to the LLM smoke test. Remove or replace the failing symbol only after discussing the replacement with the user, then update the spec or add a follow-up note explaining the change.

---

## Task 7: Run Broader Regression Tests

**Files:**

- No code files changed.

- [ ] **Step 1: Run agent strategy tests that touch basket/tool behavior**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_growth_execution_test.py tests\test_agent_manager.py tests\test_agent_history_summary.py -q
```

Expected:

```text
All selected tests pass.
```

- [ ] **Step 2: Run ruff on changed files**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
```

Expected:

```text
All checks passed!
```

---

## Task 8: Run One-Day LLM Backtest Smoke Test

**Files:**

- No code files changed.

- [ ] **Step 1: Confirm API file exists without printing secrets**

Run:

```powershell
Test-Path D:\Lumibot\project_notes\API.txt
```

Expected:

```text
True
```

- [ ] **Step 2: Run one-day mock quadrant benchmark**

Run:

```powershell
cd D:\Lumibot
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-05 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file project_notes\API.txt
```

Expected:

```text
The benchmark completes without Python traceback.
The output identifies a new trace/artifact run.
The commodity basket report is produced when the commodity basket is active, or cleanly reports inactive when target_weight is 0.
Portfolio decision and execution-plan generation are not broken by the expanded commodity universe.
```

- [ ] **Step 3: If the randomly selected regime makes commodity inactive, run a deterministic commodity-active day**

Use the existing mock regime behavior to find a commodity-active date from recent smoke-test windows. If the one-day run reports `commodity` target weight `0.0`, run:

```powershell
cd D:\Lumibot
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-06 --end 2024-09-06 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file project_notes\API.txt
```

Expected:

```text
At least one smoke run includes a non-zero commodity target weight.
The commodity agent can process the expanded 28-symbol universe in that active run.
```

---

## Task 9: Verify Replay UI Discovery

**Files:**

- No code files changed.

- [ ] **Step 1: Start the replay UI**

Run:

```powershell
cd D:\Lumibot
D:\Lumibot\.venv\Scripts\python.exe scripts\agent_trace_ui.py
```

Expected:

```text
The server starts and prints a local 127.0.0.1 URL.
```

- [ ] **Step 2: Open the UI and inspect the new run**

In a browser, open the printed URL, then select:

```text
Strategy: AITradingTeamMockGrowthInflationQuadrantStrategy
Backtest run: the newest run from Task 8
System run: the 2024-09-05 or 2024-09-06 system run from Task 8
```

Expected:

```text
The workflow graph displays all expected agents.
commodity_basket_agent appears in the workflow.
Input Material for commodity_basket_agent shows the expanded basket_symbols list.
Available tools for commodity_basket_agent include market_load_history_tables_summary, market_last_price, and alpaca_news.
Tool calls and final summary render without UI errors.
```

Stop the UI server after inspection.

---

## Task 10: Final Verification And Commit State

**Files:**

- No code files changed unless Task 8 or Task 9 revealed a required fix.

- [ ] **Step 1: Check git status**

Run:

```powershell
git status --short --branch
```

Expected:

```text
The feature commit from Task 5 is present.
Only pre-existing unrelated files remain modified/untracked, or the worktree is clean except known project_notes/research artifacts.
```

- [ ] **Step 2: Show the feature commit**

Run:

```powershell
git show --stat --oneline HEAD
```

Expected:

```text
The latest feature commit modifies only:
lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py
tests/test_ai_trading_team_mock_growth_inflation_quadrant.py
```

- [ ] **Step 3: Summarize verification evidence**

In the final implementation report, include:

```text
Unit test command and result
Ruff command and result
Yahoo data availability command and result
One-day backtest command and result
Replay UI inspection result
Any warnings or residual risks
```

Do not claim completion unless these checks were run and their results were read.

