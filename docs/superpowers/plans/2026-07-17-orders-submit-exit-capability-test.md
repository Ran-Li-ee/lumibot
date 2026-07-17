# Orders Submit Exit Capability Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven development for implementation changes. Do not modify `project_notes/`. Do not modify the original Ray Dalio demo except to read it for reference.

**Goal:** Add a copied experimental Lumibot AI trading team strategy that tests whether the native `orders_submit_order` tool can handle exits, reductions, and rotations when execution is isolated in a dedicated execution agent.

**Architecture:** Add one new example strategy with a three-agent workflow: `growth_agent -> decision_agent -> execution_agent`. Register it in the benchmark runner so a 10-trading-day backtest can be launched by strategy name. Add tests that verify agent topology, permissions, prompt hygiene, context handoff, and runner registration without requiring live LLM calls.

**Tech Stack:** Python, Lumibot strategy examples, existing agent runtime API, pytest, ruff.

---

## Task 1: Add Tests for the Experimental Strategy

**Files:**
- Create `tests/test_ai_trading_team_growth_execution_test.py`

**TDD first:**
Write tests before implementation. The tests should initially fail because the new strategy file and benchmark registration do not exist.

**Required tests:**
1. Import `AITradingTeamGrowthExecutionTestStrategy` from `lumibot.example_strategies.ai_trading_team_growth_execution_test`.
2. Verify `initialize()` creates exactly these agents in this order:
   - `growth_agent`
   - `decision_agent`
   - `execution_agent`
3. Verify permissions:
   - `growth_agent`: `allow_trading=False`
   - `decision_agent`: `allow_trading=False`
   - `execution_agent`: `allow_trading=True`
4. Verify the system prompts and task prompts do not mention removed workflow concepts:
   - `inflation_agent`
   - `debt_liquidity_agent`
   - `thoughtful_disagreement`
   - `inflation report`
   - `liquidity report`
   - `debt report`
   - `disagreement report`
   - `three views`
   - `four reports`
   - `idea meritocracy`
5. Verify `on_trading_iteration()` calls agents in this order:
   - `growth_agent`
   - `decision_agent`
   - `execution_agent`
6. Verify context handoff:
   - base context includes `date` and `universe`
   - decision context includes `growth_report`
   - execution context includes `growth_report` and `trading_plan`
   - no context key reintroduces removed agents
7. Verify the benchmark runner exposes the new strategy key:
   - `growth-execution-test`

**Implementation hint:**
Avoid constructing a real Lumibot broker or calling a real LLM in tests. Use `object.__new__(StrategyClass)` and attach fake `agents`, `parameters`, and `get_datetime()` methods. A fake agent can record `task_prompt` and `context`, then return an object with a `summary` string.

---

## Task 2: Implement the New Experimental Strategy

**Files:**
- Create `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`

**Requirements:**
1. Do not edit `lumibot/example_strategies/ai_trading_team_ray_dalio_idea_meritocracy.py`.
2. Define `AITradingTeamGrowthExecutionTestStrategy`.
3. Use the same broad ETF universe shape as the Ray Dalio demo unless the surrounding code requires a narrower list.
4. In `initialize()`:
   - set `self.sleeptime = "1D"`
   - read model from `AI_TRADING_TEAM_MODEL`, with the same style of fallback as existing examples
   - create only `growth_agent`, `decision_agent`, and `execution_agent`
   - make `growth_agent` and `decision_agent` read-only
   - make `execution_agent` trading-enabled
5. Prompt intent:
   - `growth_agent`: analyze the universe from a growth/regime perspective and discuss keep/reduce/replace, but do not trade
   - `decision_agent`: convert growth report plus current account state into a structured trading plan, but do not trade
   - `execution_agent`: execute the structured plan using existing native order tools, especially `orders_submit_order`; inspect positions, portfolio, open orders, and latest prices before submitting orders
6. In `on_trading_iteration()`:
   - build a small context containing `date` and `universe`
   - run `growth_agent`
   - pass `growth_report` to `decision_agent`
   - pass `growth_report` and `trading_plan` to `execution_agent`
7. The decision plan prompt should request JSON-like text with fields:
   - `plan_type`
   - `target_symbol`
   - `current_position_assessment`
   - `exit_actions`
   - `entry_actions`
   - `do_not_trade_if`

**Prompt hygiene:**
The new strategy must not contain references to the removed Ray Dalio multi-report workflow.

---

## Task 3: Register the Strategy in the Benchmark Runner

**Files:**
- Update `scripts/run_ai_trading_team_examples_benchmark.py`

**Requirements:**
1. Import `AITradingTeamGrowthExecutionTestStrategy`.
2. Add strategy key:
   - `growth-execution-test`
3. Keep existing strategy keys unchanged.
4. Do not change benchmark behavior for existing strategies.

---

## Task 4: Verify Locally

**Commands:**

```powershell
cd D:\Lumibot
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py
.\.venv\Scripts\python.exe -m ruff check lumibot\example_strategies\ai_trading_team_growth_execution_test.py scripts\run_ai_trading_team_examples_benchmark.py tests\test_ai_trading_team_growth_execution_test.py
```

**Backtest command after unit tests pass:**

Run the new strategy over the recommended window:

```powershell
cd D:\Lumibot
.\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy growth-execution-test --start 2026-04-07 --end 2026-04-21 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 900 --env-file does-not-exist.env
```

If API keys are not available in the environment, report that unit tests passed and the real LLM backtest is blocked by missing credentials. Do not commit API keys or `project_notes/`.

---

## Task 5: Commit and Push the Feature Branch

After tests pass:

```powershell
git status --short
git add docs\superpowers\plans\2026-07-17-orders-submit-exit-capability-test.md lumibot\example_strategies\ai_trading_team_growth_execution_test.py scripts\run_ai_trading_team_examples_benchmark.py tests\test_ai_trading_team_growth_execution_test.py
git commit -m "test: add growth execution agent strategy"
git push origin feature/orders-submit-exit-capability-test
```

Do not add `project_notes/`.

