# Equity-Only LLM Prompt News Universe Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the clean `equity-only-llm` strategy to a 50-stock equity universe, add conditional news access to the equity agent, and rewrite prompts so the strategy is equity-only, rank-first, and free of old four-quadrant wording.

**Architecture:** Keep the runtime flow unchanged: `equity_basket_agent -> target_portfolio_to_execution_plan -> execution_agent`. The equity agent selects one stock from the assigned universe using ranking summaries first and news only for ambiguous leading candidates. The deterministic planner creates the strict execution plan, and the execution agent remains limited to `execution_plan_execute`.

**Tech Stack:** Python, Lumibot agent framework, `BuiltinTools`, pytest, existing benchmark runner and Agent Replay trace UI.

---

## File Structure

Modify:

- `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
  - Owns the equity universe, equity agent tool list, equity prompts, and validation helpers.
- `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`
  - Owns the strategy wiring, agent creation, per-run task prompt, and handoff to the deterministic planner.
- `lumibot/components/agents/manager.py`
  - Owns shared base prompts. This plan only adjusts one execution-minimal sentence that currently sounds like the execution agent should directly inspect tools it does not receive.
- `tests/test_ai_trading_team_equity_only_llm.py`
  - Owns focused tests for the clean equity-only strategy.
- `tests/test_agent_manager.py`
  - Owns prompt-policy tests for shared agent-manager prompt composition.

Do not modify:

- Four-quadrant strategy files.
- `target_portfolio_to_execution_plan.py`.
- `execution_plan_execute` implementation.
- Benchmark runner key, unless a test proves the existing `equity-only-llm` key is broken.

---

### Task 1: Restore The 50-Stock Equity Universe

**Files:**

- Modify: `tests/test_ai_trading_team_equity_only_llm.py`
- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`

- [ ] **Step 1: Add the expected 50-stock universe test**

In `tests/test_ai_trading_team_equity_only_llm.py`, add this constant after `load_module()`:

```python
EXPECTED_EQUITY_UNIVERSE = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
    "AVGO",
    "AMD",
    "NFLX",
    "ORCL",
    "CRM",
    "ADBE",
    "CSCO",
    "QCOM",
    "TXN",
    "IBM",
    "INTC",
    "NOW",
    "PANW",
    "UNH",
    "JNJ",
    "LLY",
    "MRK",
    "ABBV",
    "TMO",
    "ABT",
    "JPM",
    "BAC",
    "GS",
    "MS",
    "V",
    "MA",
    "WMT",
    "COST",
    "HD",
    "MCD",
    "NKE",
    "SBUX",
    "DIS",
    "XOM",
    "CVX",
    "CAT",
    "GE",
    "HON",
    "BA",
    "DE",
    "PG",
    "KO",
    "PEP",
]
```

Then add this test near the existing equity target-portfolio tests:

```python
def test_equity_universe_contains_50_us_stock_symbols_without_old_etfs():
    helpers = importlib.import_module("lumibot.example_strategies.ai_trading_team_equity_only_helpers")

    universe = helpers.EQUITY_UNIVERSE

    assert universe == EXPECTED_EQUITY_UNIVERSE
    assert len(universe) == 50
    assert len(set(universe)) == 50
    assert all(symbol == symbol.upper() for symbol in universe)
    assert all(symbol.isalpha() for symbol in universe)
    assert not {"SPY", "QQQ", "IWM", "EEM", "FXI"} & set(universe)
```

- [ ] **Step 2: Run the universe test and verify it fails**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_universe_contains_50_us_stock_symbols_without_old_etfs -q
```

Expected: FAIL because current `EQUITY_UNIVERSE` is `["SPY", "QQQ", "IWM", "EEM", "FXI"]`.

- [ ] **Step 3: Replace the current equity universe**

In `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`, replace:

```python
EQUITY_UNIVERSE = ["SPY", "QQQ", "IWM", "EEM", "FXI"]
```

with:

```python
EQUITY_UNIVERSE = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
    "AVGO",
    "AMD",
    "NFLX",
    "ORCL",
    "CRM",
    "ADBE",
    "CSCO",
    "QCOM",
    "TXN",
    "IBM",
    "INTC",
    "NOW",
    "PANW",
    "UNH",
    "JNJ",
    "LLY",
    "MRK",
    "ABBV",
    "TMO",
    "ABT",
    "JPM",
    "BAC",
    "GS",
    "MS",
    "V",
    "MA",
    "WMT",
    "COST",
    "HD",
    "MCD",
    "NKE",
    "SBUX",
    "DIS",
    "XOM",
    "CVX",
    "CAT",
    "GE",
    "HON",
    "BA",
    "DE",
    "PG",
    "KO",
    "PEP",
]
```

- [ ] **Step 4: Run the universe test and verify it passes**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_universe_contains_50_us_stock_symbols_without_old_etfs -q
```

Expected: PASS.

- [ ] **Step 5: Commit the universe change**

Run:

```powershell
git add tests/test_ai_trading_team_equity_only_llm.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py
git commit -m "feat: restore equity-only stock universe"
```

---

### Task 2: Add Conditional News Tool And Rewrite Equity Prompts

**Files:**

- Modify: `tests/test_ai_trading_team_equity_only_llm.py`
- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Add helper functions for created-agent assertions**

In `tests/test_ai_trading_team_equity_only_llm.py`, add these helpers after `make_running_strategy()`:

```python
def created_agent_config(strategy, name):
    matches = [agent for agent in strategy.agents.created if agent["name"] == name]
    assert len(matches) == 1
    return matches[0]


def tool_names(agent_config):
    return [tool.name for tool in agent_config.get("tools", [])]
```

- [ ] **Step 2: Add failing tests for equity tool surface and prompt policy**

In `tests/test_ai_trading_team_equity_only_llm.py`, add these tests after `test_initialize_creates_only_equity_and_execution_agents()`:

```python
def test_equity_agent_tool_surface_includes_rank_price_and_news_only():
    module = load_module()
    strategy = make_strategy(module.AITradingTeamEquityOnlyLLMStrategy)

    strategy.initialize()

    equity_agent = created_agent_config(strategy, "equity_basket_agent")
    execution_agent = created_agent_config(strategy, "execution_agent")

    assert equity_agent["include_builtin_tools"] is False
    assert equity_agent["allow_trading"] is False
    assert tool_names(equity_agent) == [
        "market_load_history_tables_summary",
        "market_last_price",
        "alpaca_news",
    ]
    assert execution_agent["include_builtin_tools"] is False
    assert execution_agent["allow_trading"] is True
    assert tool_names(execution_agent) == ["execution_plan_execute"]


def test_equity_agent_system_prompt_is_equity_only_rank_first_and_conditional_news():
    module = load_module()
    strategy = make_strategy(module.AITradingTeamEquityOnlyLLMStrategy)

    strategy.initialize()

    equity_agent = created_agent_config(strategy, "equity_basket_agent")
    prompt = equity_agent["system_prompt"].lower()

    for required in (
        "equity-only",
        "choose exactly one stock",
        "market_load_history_tables_summary",
        "separate evidence",
        "alpaca_news",
        "leading candidates",
        "strict json",
        "do not place orders",
    ):
        assert required in prompt

    for forbidden in (
        "quadrant",
        "macro regime",
        "commodity",
        "tips",
        "nominal bond",
        "defensive posture",
        "duckdb",
    ):
        assert forbidden not in prompt


def test_equity_agent_task_prompt_teaches_summary_first_top_n_and_strict_json(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamEquityOnlyLLMStrategy)
    strategy.parameters["basket_universes"] = {
        **strategy.parameters["basket_universes"],
        "equity": EXPECTED_EQUITY_UNIVERSE,
    }
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": EXPECTED_EQUITY_UNIVERSE,
            "selected_symbol": "ORCL",
            "reason_brief": "ORCL has the strongest setup.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."

    def fake_target_portfolio_to_execution_plan(strategy_arg, *, date, target_portfolio):
        return {
            "schema_version": "1.0",
            "date": date,
            "target_portfolio": target_portfolio,
            "current_vs_target": [],
            "cash_projection": {
                "cash_before": 100000.0,
                "estimated_sell_proceeds": 0.0,
                "estimated_buy_cost": 0.0,
                "cash_after_estimate": 100000.0,
                "buy_sizing_buffer_pct": 0.02,
                "negative_cash_allowed": False,
            },
            "execution_plan": {
                "schema_version": 1,
                "intent": "hold",
                "orders": [],
            },
            "warnings": [],
        }

    monkeypatch.setattr(module, "target_portfolio_to_execution_plan", fake_target_portfolio_to_execution_plan)

    strategy.on_trading_iteration()

    equity_call = strategy.agents["equity_basket_agent"].calls[0]
    task_prompt = equity_call["task_prompt"].lower()

    assert equity_call["context"]["basket_symbols"] == EXPECTED_EQUITY_UNIVERSE
    assert "market_load_history_tables_summary" in task_prompt
    assert "length=252" in task_prompt
    assert "timestep='day'" in task_prompt
    assert "top_n=10" in task_prompt
    assert "alpaca_news" in task_prompt
    assert "close, conflicting, or uncertain" in task_prompt
    assert "candidate_symbols must copy the assigned basket_symbols exactly" in task_prompt
    assert "return exactly one strict json object" in task_prompt
```

- [ ] **Step 3: Run the new prompt/tool tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_tool_surface_includes_rank_price_and_news_only tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_system_prompt_is_equity_only_rank_first_and_conditional_news tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_task_prompt_teaches_summary_first_top_n_and_strict_json -q
```

Expected: FAIL because `alpaca_news` is not yet in the equity tool list and the prompt wording is still old.

- [ ] **Step 4: Add `alpaca_news` to the equity agent tool list**

In `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`, replace `equity_basket_agent_tools()` with:

```python
def equity_basket_agent_tools() -> list[ToolDefinition]:
    return [
        BuiltinTools.market.load_history_tables_summary(),
        BuiltinTools.market.last_price(),
        BuiltinTools.news.alpaca_news(),
    ]
```

- [ ] **Step 5: Rewrite the equity agent system prompt**

In `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`, replace `equity_basket_agent_system_prompt()` with:

```python
def equity_basket_agent_system_prompt(symbols: str) -> str:
    return (
        f"Equity-only selection role: choose exactly one stock from the assigned basket_symbols ({symbols}). "
        "The selected stock receives target_weight 1.0 through downstream deterministic planning. "
        "You cannot place orders or size trades. Use market_load_history_tables_summary first for multi-symbol "
        "comparison. Treat rankings as separate evidence views; do not invent sector, style, safety, or "
        "cyclicality labels. If one symbol is clearly stronger across relevant rankings, select it without news. "
        "Use alpaca_news only when leading candidates are close, conflicting, or uncertain; when used, request "
        "news only for leading candidates. If news is unavailable, continue with rank-only evidence. "
        "Return strict JSON only. Do not place orders."
    )
```

- [ ] **Step 6: Rewrite the equity agent task prompt**

In `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`, replace `equity_basket_agent_task_prompt()` with:

```python
def equity_basket_agent_task_prompt() -> str:
    return (
        "Review only the provided basket_symbols. First call market_load_history_tables_summary with "
        "symbols=basket_symbols, length=252, timestep='day', and top_n=10. Compare separate ranking views. "
        "If one symbol is clearly stronger across relevant rankings, select it without news. If leading "
        "candidates are close, conflicting, or uncertain, call alpaca_news for those leading candidates only. "
        "If alpaca_news is unavailable or errors, continue with rank-only evidence. Return exactly one strict "
        "JSON object with basket_id, target_weight, status, candidate_symbols, selected_symbol, and reason_brief. "
        "Use status='active'. candidate_symbols must copy the assigned basket_symbols exactly; do not replace it "
        "with a shortlist. selected_symbol must be one of basket_symbols."
    )
```

- [ ] **Step 7: Remove duplicated equity prompt append text from strategy wiring**

In `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`, replace this `system_prompt` expression:

```python
system_prompt=(
    equity_basket_agent_system_prompt(equity_symbols)
    + " This equity-only strategy allocates the full target portfolio to the selected equity symbol. "
    "Stay inside the configured equity universe. Do not place orders."
),
```

with:

```python
system_prompt=equity_basket_agent_system_prompt(equity_symbols),
```

Then replace this `task_prompt` expression inside `_run_equity_only_workflow()`:

```python
task_prompt=(
    equity_basket_agent_task_prompt()
    + " This equity-only strategy has target_weight 1.0 for the selected equity symbol. "
    "Return only one JSON object. Do not include markdown or extra prose."
),
```

with:

```python
task_prompt=equity_basket_agent_task_prompt(),
```

- [ ] **Step 8: Run the prompt/tool tests and verify they pass**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_tool_surface_includes_rank_price_and_news_only tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_system_prompt_is_equity_only_rank_first_and_conditional_news tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_task_prompt_teaches_summary_first_top_n_and_strict_json -q
```

Expected: PASS.

- [ ] **Step 9: Run all focused equity-only tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit the equity prompt and news change**

Run:

```powershell
git add tests/test_ai_trading_team_equity_only_llm.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py lumibot/example_strategies/ai_trading_team_equity_only_llm.py
git commit -m "feat: add equity news prompt contract"
```

---

### Task 3: Clean Execution-Minimal Prompt Wording

**Files:**

- Modify: `tests/test_agent_manager.py`
- Modify: `lumibot/components/agents/manager.py`

- [ ] **Step 1: Add a failing assertion for execution-minimal wording**

In `tests/test_agent_manager.py`, update `test_execution_agent_with_execution_plan_execute_tool_receives_stage_d_policy()` by adding these assertions before the existing `assert "price/history tool policy" not in prompt_lower` line:

```python
    assert "before submitting any order, inspect current positions" not in prompt_lower
    assert "use execution tools that are available to perform required execution-level account" in prompt_lower
```

- [ ] **Step 2: Run the execution prompt test and verify it fails**

Run:

```powershell
python -m pytest tests/test_agent_manager.py::test_execution_agent_with_execution_plan_execute_tool_receives_stage_d_policy -q
```

Expected: FAIL because the current base prompt says the execution agent should inspect account/order/price state directly.

- [ ] **Step 3: Replace the conflicting sentence in the execution-minimal base prompt**

In `lumibot/components/agents/manager.py`, inside `_execution_minimal_base_system_prompt()`, replace:

```python
"Before submitting any order, inspect current positions, available cash, portfolio value, open orders, "
"and the latest price for the ordered asset.",
```

with:

```python
"Use execution tools that are available to perform required execution-level account, cash, open-order, "
"and price checks before submission.",
```

- [ ] **Step 4: Run the execution prompt test and verify it passes**

Run:

```powershell
python -m pytest tests/test_agent_manager.py::test_execution_agent_with_execution_plan_execute_tool_receives_stage_d_policy -q
```

Expected: PASS.

- [ ] **Step 5: Run adjacent execution prompt tests**

Run:

```powershell
python -m pytest tests/test_agent_manager.py::test_execution_agent_with_execute_tool_receives_stage_c_execution_policy tests/test_agent_manager.py::test_execution_agent_with_execution_plan_execute_tool_receives_stage_d_policy tests/test_agent_manager.py::test_agent_manager_create_forwards_base_system_prompt_mode -q
```

Expected: PASS.

- [ ] **Step 6: Commit the execution prompt cleanup**

Run:

```powershell
git add tests/test_agent_manager.py lumibot/components/agents/manager.py
git commit -m "fix: clarify execution minimal prompt"
```

---

### Task 4: Focused Regression And Smoke Validation

**Files:**

- Read: `scripts/run_ai_trading_team_examples_benchmark.py`
- Read: latest artifact under `artifacts/ai_trading_team_example_benchmarks/`
- Read through UI: `scripts/agent_trace_ui.py`

- [ ] **Step 1: Run focused Python tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_manager.py::test_execution_agent_with_execution_plan_execute_tool_receives_stage_d_policy -q
```

Expected: PASS.

- [ ] **Step 2: Run lint on touched Python files**

Run:

```powershell
python -m ruff check lumibot/example_strategies/ai_trading_team_equity_only_helpers.py lumibot/example_strategies/ai_trading_team_equity_only_llm.py lumibot/components/agents/manager.py tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_manager.py
```

Expected: PASS with no lint errors.

- [ ] **Step 3: Run a one-day equity-only smoke backtest**

For Ran's local environment with API keys in `D:\Lumibot\project_notes\API.txt`, run:

```powershell
$env:AI_TRADING_TEAM_MODEL='openai/gpt-5.6-luna'
python scripts\run_ai_trading_team_examples_benchmark.py --strategy equity-only-llm --start 2024-09-05 --end 2024-09-06 --run-frequency daily --max-workers 1 --max-run-attempts 1 --env-file D:\Lumibot\project_notes\API.txt
```

Expected: command prints a JSON object containing:

```json
{
  "strategy": "equity-only-llm",
  "status": "completed"
}
```

If API provider or market-data keys fail, record the exact provider/tool error and still complete Steps 4 and 5 using any generated trace if one exists.

- [ ] **Step 4: Inspect the generated benchmark summary**

Open the latest `summary.json` under:

```text
artifacts/ai_trading_team_example_benchmarks/
```

Verify:

```text
summary.results[0].strategy == "equity-only-llm"
summary.results[0].artifact_dir ends with "equity-only-llm"
```

- [ ] **Step 5: Inspect Agent Replay UI**

Start the UI:

```powershell
python scripts\agent_trace_ui.py
```

Open:

```text
http://127.0.0.1:8765
```

Select the latest `AITradingTeamEquityOnlyLLMStrategy` run and verify:

```text
agent graph: equity_basket_agent -> execution_agent
equity_basket_agent context basket_symbols count: 50
equity_basket_agent requested tool definitions include alpaca_news
equity_basket_agent runtime available tools include alpaca_news when credentials are configured
equity_basket_agent prompt does not mention quadrant, macro regime, commodity, TIPS, or nominal bond
execution_agent context contains date and execution_plan only
execution_agent available tools contain execution_plan_execute only
```

Conditional acceptance:

```text
If news credentials are missing or runtime omits unavailable tools, unit tests must still prove the strategy requested BuiltinTools.news.alpaca_news().
```

- [ ] **Step 6: Commit final validation notes if a small note file is created**

If the implementation worker creates a short validation note, place it at:

```text
docs/superpowers/notes/2026-08-16-equity-only-llm-prompt-news-universe-cleanup-validation.md
```

Use this content:

```markdown
# Equity-Only LLM Prompt News Universe Cleanup Validation

## Automated Tests

- `python -m pytest tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_manager.py::test_execution_agent_with_execution_plan_execute_tool_receives_stage_d_policy -q`
- `python -m ruff check lumibot/example_strategies/ai_trading_team_equity_only_helpers.py lumibot/example_strategies/ai_trading_team_equity_only_llm.py lumibot/components/agents/manager.py tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_manager.py`

## Smoke Backtest

- Command: `python scripts\run_ai_trading_team_examples_benchmark.py --strategy equity-only-llm --start 2024-09-05 --end 2024-09-06 --run-frequency daily --max-workers 1 --max-run-attempts 1 --env-file D:\Lumibot\project_notes\API.txt`
- Model: `openai/gpt-5.6-luna`

## Replay Checks

- Strategy graph: `equity_basket_agent -> execution_agent`.
- Equity symbol count: `50`.
- Equity tools: strategy requests `market_load_history_tables_summary`, `market_last_price`, and `alpaca_news`; runtime available tools include `alpaca_news` when news credentials are configured.
- Execution tools: `execution_plan_execute`.
- Prompt cleanup: equity prompt contains no `quadrant`, `macro regime`, `commodity`, `TIPS`, or `nominal bond` wording.
- Notable warnings: write exact warning text when warnings are present; write `none` when no warnings are present.
```

After filling the six Replay Checks lines with actual observed values, commit:

```powershell
git add docs/superpowers/notes/2026-08-16-equity-only-llm-prompt-news-universe-cleanup-validation.md
git commit -m "docs: record equity-only cleanup validation"
```

If no note file is created, do not make an empty commit.

---

## Final Review Checklist

- [ ] `git status --short --branch` shows the intended feature branch.
- [ ] No four-quadrant strategy files were modified.
- [ ] `EQUITY_UNIVERSE` has exactly 50 US stock symbols.
- [ ] Equity agent has `market_load_history_tables_summary`, `market_last_price`, and `alpaca_news`.
- [ ] Execution agent has only `execution_plan_execute`.
- [ ] Equity prompt is equity-only, rank-first, and conditional-news.
- [ ] Execution prompt no longer suggests direct manual account/order/price inspection when only `execution_plan_execute` is exposed.
- [ ] Focused pytest command passes.
- [ ] Ruff command passes.
- [ ] One-day smoke backtest either completes or produces a concrete provider/tool error that is unrelated to the code contract.

---

## Execution Handoff

Plan complete. Use `superpowers:subagent-driven-development` for implementation. Dispatch one fresh subagent per task, review after each task, and only move to the next task after tests for the current task pass.
