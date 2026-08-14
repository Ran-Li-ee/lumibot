# Real Macro Integration On Quadrant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the real FRED-backed Growth / Inflation quadrant classifier and no-LLM regime scanner to the current latest quadrant workflow without losing the newer basket, execution, weekly cadence, trace, and replay behavior.

**Architecture:** Use the current `feature/commodity-basket-universe-expansion` branch as the source of truth for downstream workflow behavior. Bring the real classifier and scanner from `feature/real-fred-growth-inflation-regime`, then adapt the real strategy to call current downstream helpers extracted from the current mock strategy. Keep `mock-growth-inflation-quadrant` as the workflow test entrypoint and add `growth-inflation-quadrant` as the real macro validation entrypoint.

**Tech Stack:** Python, Lumibot strategies, FREDMacroData, Google ADK agent tools, pytest, benchmark runner, local artifact scanner.

---

## File Map

**Create:**
- `lumibot/example_strategies/fred_growth_inflation_regime_classifier.py` - real FRED-backed macro classifier tool.
- `lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py` - real strategy entrypoint that reuses the current downstream workflow.
- `scripts/scan_growth_inflation_regimes.py` - no-LLM historical regime scanner.
- `tests/test_ai_trading_team_growth_inflation_quadrant.py` - real classifier and real strategy tests adapted to current workflow.
- `tests/test_growth_inflation_regime_scan.py` - scanner unit tests.
- `docs/superpowers/specs/2026-08-12-growth-inflation-regime-scan-tool-design.md` - historical scanner spec brought forward for context.
- `docs/superpowers/plans/2026-08-12-growth-inflation-regime-scan-tool.md` - historical scanner plan brought forward for context.

**Modify:**
- `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py` - extract current downstream helpers while preserving mock behavior.
- `scripts/run_ai_trading_team_examples_benchmark.py` - register `growth-inflation-quadrant`.
- `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py` - lock current helper behavior and benchmark registry expectations.

**Do not replace wholesale:**
- `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- `scripts/run_ai_trading_team_examples_benchmark.py`
- any current basket, execution, trace, or replay files

---

## Task 0: Create The Integration Branch

**Files:**
- No file edits.

- [ ] **Step 1: Verify the current working tree is clean**

Run:

```powershell
git status --short --branch
```

Expected:

```text
## feature/commodity-basket-universe-expansion
```

with no modified files listed.

- [ ] **Step 2: Create the integration branch from the current latest branch**

Run:

```powershell
git switch -c feature/real-macro-integration-on-quadrant
```

Expected:

```text
Switched to a new branch 'feature/real-macro-integration-on-quadrant'
```

If the branch already exists locally, stop and inspect it with:

```powershell
git status --short --branch
git log --oneline --decorate -5
```

Do not delete or reset an existing branch without explicit user approval.

- [ ] **Step 3: Verify the source branch exists**

Run:

```powershell
git branch --list feature/real-fred-growth-inflation-regime
```

Expected:

```text
  feature/real-fred-growth-inflation-regime
```

If it is not listed, verify whether the old worktree exists:

```powershell
Test-Path C:\Users\Ran\.config\superpowers\worktrees\lumibot\real-fred-growth-inflation-regime
```

Expected:

```text
True
```

Use the branch as the preferred source. Use the old worktree only if the branch is unavailable.

---

## Task 1: Bring Forward The Real FRED Macro Classifier

**Files:**
- Create: `lumibot/example_strategies/fred_growth_inflation_regime_classifier.py`
- Create/Modify: `tests/test_ai_trading_team_growth_inflation_quadrant.py`

- [ ] **Step 1: Restore the real classifier file from the real macro branch**

Run:

```powershell
git restore --source feature/real-fred-growth-inflation-regime -- lumibot/example_strategies/fred_growth_inflation_regime_classifier.py
```

Expected:

```text
```

No terminal output is expected on success.

- [ ] **Step 2: Restore the real strategy test file as the starting point**

Run:

```powershell
git restore --source feature/real-fred-growth-inflation-regime -- tests/test_ai_trading_team_growth_inflation_quadrant.py
```

Expected:

```text
```

No terminal output is expected on success.

- [ ] **Step 3: Run classifier-focused tests and observe expected failures from missing real strategy**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_growth_inflation_quadrant.py -q
```

Expected at this stage:

```text
FAILED ... ModuleNotFoundError
```

or failures related to `lumibot.example_strategies.ai_trading_team_growth_inflation_quadrant` not existing yet. Classifier-only tests should either pass or be blocked by collection failures from the missing real strategy. Do not edit classifier logic before inspecting any unexpected classifier-specific failure.

- [ ] **Step 4: Verify the classifier public contract**

Run:

```powershell
rg -n "DEFAULT_MODE|DEFAULT_GROWTH_SERIES_ID|DEFAULT_INFLATION_SERIES_ID|make_real_macro_regime_classifier_tool|classify_growth_inflation_regime" lumibot\example_strategies\fred_growth_inflation_regime_classifier.py
```

Expected output includes:

```text
DEFAULT_MODE = "fred_ra_simple_lagged"
DEFAULT_GROWTH_SERIES_ID = "GDPC1"
DEFAULT_INFLATION_SERIES_ID = "CPIAUCSL"
def classify_growth_inflation_regime(
def make_real_macro_regime_classifier_tool(
```

- [ ] **Step 5: Commit the classifier import**

Run:

```powershell
git add lumibot\example_strategies\fred_growth_inflation_regime_classifier.py tests\test_ai_trading_team_growth_inflation_quadrant.py
git commit -m "feat: add real fred macro regime classifier"
```

Expected:

```text
[feature/real-macro-integration-on-quadrant ...] feat: add real fred macro regime classifier
```

---

## Task 2: Extract Current Downstream Helpers From The Mock Strategy

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

This task is the main safety step. It must preserve the current mock strategy behavior while making the current downstream workflow reusable by the real strategy.

- [ ] **Step 1: Add tests that lock helper reuse without changing behavior**

Modify `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py` by adding tests near the current initialization and workflow tests.

Add:

```python
def test_mock_strategy_exposes_current_downstream_helper_methods():
    _module, strategy_class = load_strategy_module()

    for method_name in (
        "_initialize_growth_inflation_workflow_state",
        "_create_growth_inflation_downstream_agents",
        "_run_growth_inflation_downstream_workflow",
        "_log_growth_inflation_workflow_blocked",
    ):
        assert hasattr(strategy_class, method_name)


def test_mock_strategy_downstream_helper_creates_current_agent_surfaces(monkeypatch):
    _module, strategy_class = load_strategy_module()
    monkeypatch.setenv("AI_TRADING_TEAM_MODEL", "test-model")
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy._create_growth_inflation_downstream_agents("test-model")

    assert [agent["name"] for agent in agent_manager.created] == [
        "equity_basket_agent",
        "commodity_basket_agent",
        "tips_basket_agent",
        "nominal_bond_basket_agent",
        "portfolio_decision_agent",
        "execution_agent",
    ]
    created = {agent["name"]: agent for agent in agent_manager.created}
    assert created_tool_names(created["equity_basket_agent"]) == {
        "market_load_history_tables_summary",
        "market_last_price",
    }
    assert created_tool_names(created["commodity_basket_agent"]) == {
        "market_load_history_tables_summary",
        "market_last_price",
        "alpaca_news",
    }
    assert created_tool_names(created["tips_basket_agent"]) == {
        "market_load_history_tables_summary",
        "market_last_price",
        "alpaca_news",
    }
    assert created_tool_names(created["nominal_bond_basket_agent"]) == {
        "market_load_history_tables_summary",
        "market_last_price",
    }
    assert created_tool_names(created["portfolio_decision_agent"]) == {
        "target_portfolio_to_execution_plan",
    }
    assert created_tool_names(created["execution_agent"]) == {"execution_plan_execute"}
```

- [ ] **Step 2: Run the new helper tests and verify they fail**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py::test_mock_strategy_exposes_current_downstream_helper_methods tests/test_ai_trading_team_mock_growth_inflation_quadrant.py::test_mock_strategy_downstream_helper_creates_current_agent_surfaces -q
```

Expected:

```text
FAILED ... _initialize_growth_inflation_workflow_state
```

or the first missing helper method.

- [ ] **Step 3: Extract current shared workflow state initialization**

Modify `AITradingTeamMockGrowthInflationQuadrantStrategy` in `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`.

Add this method inside the class before `initialize`:

```python
    def _initialize_growth_inflation_workflow_state(self) -> None:
        self._run_frequency = normalize_run_frequency(self.parameters.get("run_frequency", "weekly"))
        self._weekly_run_weekday = normalize_weekly_run_weekday(self.parameters.get("weekly_run_weekday", "MON"))
        self._weekly_holiday_policy = str(
            self.parameters.get("weekly_holiday_policy", "first_open_trading_day")
        ).strip()
        if self._weekly_holiday_policy != "first_open_trading_day":
            raise ValueError("weekly_holiday_policy must be 'first_open_trading_day'.")
        self._scheduled_workflow_attempted_week_keys: set[str] = set()
        self._scheduled_workflow_events: list[dict[str, Any]] = []
        self._last_scheduled_workflow_run_date: str | None = None
        self._last_target_portfolio_planner_result = None
        self._last_execution_plan_error = None
```

Then replace the matching block in `initialize` with:

```python
        self._initialize_growth_inflation_workflow_state()
```

Keep mock-specific fields in `initialize`:

```python
        self._mock_regime_mode = self.parameters.get("mock_regime_mode", "seeded_random")
        self._mock_regime_seed = int(self.parameters.get("mock_regime_seed", 42))
        self._last_mock_regime = None
```

- [ ] **Step 4: Extract current downstream agent creation**

Add this method inside the class after the mock macro agent creation block or before `initialize`:

```python
    def _create_growth_inflation_downstream_agents(self, model: str) -> None:
        basket_universes = self.parameters.get("basket_universes", BASKET_UNIVERSES)
        for basket_id, agent_name in BASKET_AGENT_NAMES.items():
            symbols = ", ".join(basket_universes[basket_id])
            self.agents.create(
                name=agent_name,
                model=model,
                allow_trading=False,
                include_builtin_tools=False,
                tools=basket_agent_tools(basket_id),
                system_prompt=basket_agent_system_prompt(basket_id, symbols),
            )

        self.agents.create(
            name="portfolio_decision_agent",
            model=model,
            allow_trading=False,
            include_builtin_tools=False,
            tools=[make_target_portfolio_to_execution_plan_tool()],
            system_prompt=(
                "Portfolio decision role: this is the current scheduled review. Do not redo macro or basket "
                "research. Merge the macro allocation report and basket reports into a target_portfolio, then call "
                f"{TARGET_PORTFOLIO_TO_EXECUTION_PLAN_TOOL_NAME}. Do not "
                "place orders. Do not manually calculate share quantities, cash usage, order side, or order sequence. "
                "The planner tool owns all execution_plan calculations. "
                "The planner tool owns daily backtest buy sizing, including its price basis and buy sizing buffer. "
                "Return only one valid JSON object; do not "
                "include markdown, RESULT text, or prose after the JSON. The top-level fields decision, "
                "target_portfolio, and execution_plan are required. decision must include type and reason_brief. "
                "target_portfolio must list the selected active basket targets as symbols and target weights. "
                "The final execution_plan must be copied exactly from the planner tool result. Do not modify "
                "tool-generated quantities, sides, order_type, time_in_force, or sequence values."
            ),
        )

        self.agents.create(
            name="execution_agent",
            model=model,
            allow_trading=True,
            base_system_prompt_mode=self._execution_agent_base_system_prompt_mode,
            include_builtin_tools=False,
            tools=[BuiltinTools.orders.execute_plan()],
            system_prompt=(
                "Execution role: execute only provided execution_plan. "
                "Call execution_plan_execute exactly once with the complete execution_plan. "
                "Do not manually execute individual orders. "
                "Do not call lower-level order, account, open-order, or price tools when execution_plan_execute "
                "is available. Do not research, change fields, reorder orders, split orders, or repair the plan. "
                "execution_plan_execute returns a concise execution summary for your final answer; "
                "full audit details are recorded in trace/replay for developer inspection. "
                "If the tool returns plan_status=completed, summarize completed orders. If it returns "
                "plan_status=blocked or invalid, summarize where execution stopped and why."
            ),
        )
```

Then replace the duplicated downstream creation block in `initialize` with:

```python
        self._create_growth_inflation_downstream_agents(model)
```

- [ ] **Step 5: Extract current downstream execution workflow**

Add these methods inside the class before `on_trading_iteration`:

```python
    def _log_growth_inflation_workflow_blocked(self, message: str) -> None:
        print(message)

    def _run_growth_inflation_downstream_workflow(
        self,
        current_date: str,
        macro_report: dict[str, Any],
        blocked_prefix: str,
    ) -> None:
        basket_universes = self.parameters.get("basket_universes", BASKET_UNIVERSES)
        try:
            basket_reports_by_id = {}
            basket_weights = _require_dict(macro_report.get("basket_weights", {}), "macro basket_weights")
            for basket_id, agent_name in BASKET_AGENT_NAMES.items():
                basket_result = self.agents[agent_name].run(
                    task_prompt=basket_agent_task_prompt(basket_id),
                    context={
                        "date": current_date,
                        "basket_id": basket_id,
                        "basket_symbols": basket_universes[basket_id],
                        "target_weight": float(basket_weights.get(basket_id, 0.0)),
                        "macro_allocation_report": macro_report,
                    },
                )
                basket_reports_by_id[basket_id] = _parse_json_summary(basket_result.summary, agent_name)

            self._last_target_portfolio_planner_result = None
            portfolio_result = self.agents["portfolio_decision_agent"].run(
                task_prompt=(
                    "Create target_portfolio for the current scheduled review from the provided macro and basket "
                    "reports, then call "
                    f"{TARGET_PORTFOLIO_TO_EXECUTION_PLAN_TOOL_NAME} with date and target_portfolio. "
                    "Return only the strict JSON object with decision, target_portfolio, and the planner tool's "
                    "execution_plan copied exactly."
                ),
                context={
                    "date": current_date,
                    "macro_allocation_report": macro_report,
                    "equity_basket_report": basket_reports_by_id["equity"],
                    "commodity_basket_report": basket_reports_by_id["commodity"],
                    "tips_basket_report": basket_reports_by_id["tips"],
                    "nominal_bond_basket_report": basket_reports_by_id["nominal_bond"],
                },
            )
            execution_plan = parse_execution_plan_from_portfolio_summary(portfolio_result.summary)
            validate_portfolio_decision_tool_evidence(execution_plan, portfolio_result)
            validate_execution_plan_matches_planner_result(self, execution_plan)
            validate_execution_plan_symbols(execution_plan, list(basket_reports_by_id.values()))
            validate_decision_buy_sizing(self, execution_plan)
            validate_execution_plan_cash_safety(self, execution_plan)
        except ValueError as exc:
            self._last_execution_plan_error = str(exc)
            self._log_growth_inflation_workflow_blocked(f"{blocked_prefix}: {exc}")
            return

        self._last_execution_plan_error = None
        if execution_plan["intent"] == "hold" or not execution_plan["orders"]:
            return

        self.agents["execution_agent"].run(
            task_prompt=(
                "Execute the provided execution_plan by calling execution_plan_execute exactly once with the "
                "complete execution_plan. Use the returned concise execution summary to write the final result. "
                "Do not infer missing order details beyond the tool response. Do not call per-order tools."
            ),
            context={
                "date": current_date,
                "execution_plan": execution_plan_execute_payload(execution_plan),
            },
        )
```

Then rewrite the macro run portion of `on_trading_iteration` so the method keeps cadence and macro parsing locally, then delegates downstream work:

```python
        try:
            macro_result = self.agents["macro_allocation_agent"].run(
                task_prompt=(
                    "Run the mock macro allocation step for the current scheduled review date and return one JSON "
                    "object with regime, basket_weights, mock flag, regime_changed, and reason_brief."
                ),
                context={
                    "date": current_date,
                    "mock_regime_mode": self._mock_regime_mode,
                    "mock_regime_seed": self._mock_regime_seed,
                    "basket_universes": basket_universes,
                },
            )
            macro_report = _parse_json_summary(macro_result.summary, "macro_allocation_agent")
        except ValueError as exc:
            self._last_execution_plan_error = str(exc)
            self._log_growth_inflation_workflow_blocked(f"Mock quadrant workflow blocked: {exc}")
            return

        self._run_growth_inflation_downstream_workflow(
            current_date,
            macro_report,
            "Mock quadrant workflow blocked",
        )
```

Keep the macro agent run inside `on_trading_iteration`; only downstream workflow should move.

- [ ] **Step 6: Run focused mock strategy tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected:

```text
passed
```

If existing tests fail because the refactor changed prompt text, restore the exact current prompt wording rather than updating tests to accept weaker behavior.

- [ ] **Step 7: Commit helper extraction**

Run:

```powershell
git add lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "refactor: share current quadrant downstream workflow"
```

Expected:

```text
[feature/real-macro-integration-on-quadrant ...] refactor: share current quadrant downstream workflow
```

---

## Task 3: Add The Real Growth / Inflation Strategy Entrypoint

**Files:**
- Create: `lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py`
- Modify: `tests/test_ai_trading_team_growth_inflation_quadrant.py`

- [ ] **Step 1: Create the real strategy file using current helper calls**

Create `lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py` with this structure:

```python
import os

from lumibot.example_strategies.ai_trading_team_mock_growth_inflation_quadrant import (
    BASKET_UNIVERSES,
    AITradingTeamMockGrowthInflationQuadrantStrategy,
    _parse_json_summary,
    normalize_run_frequency,
    normalize_weekly_run_weekday,
)
from lumibot.example_strategies.fred_growth_inflation_regime_classifier import (
    DEFAULT_GROWTH_LAG_MONTHS,
    DEFAULT_GROWTH_SERIES_ID,
    DEFAULT_INFLATION_LAG_MONTHS,
    DEFAULT_INFLATION_SERIES_ID,
    DEFAULT_MODE,
    DEFAULT_TREND_YEARS,
    make_real_macro_regime_classifier_tool,
)


class AITradingTeamGrowthInflationQuadrantStrategy(
    AITradingTeamMockGrowthInflationQuadrantStrategy
):
    parameters = {
        "basket_universes": BASKET_UNIVERSES,
        "run_frequency": "weekly",
        "weekly_run_weekday": "MON",
        "weekly_holiday_policy": "first_open_trading_day",
        "macro_regime_mode": DEFAULT_MODE,
        "growth_series_id": DEFAULT_GROWTH_SERIES_ID,
        "inflation_series_id": DEFAULT_INFLATION_SERIES_ID,
        "growth_lag_months": DEFAULT_GROWTH_LAG_MONTHS,
        "inflation_lag_months": DEFAULT_INFLATION_LAG_MONTHS,
        "trend_years": DEFAULT_TREND_YEARS,
    }
    _execution_agent_base_system_prompt_mode = "execution_minimal"

    def initialize(self):
        self.sleeptime = "1D"
        self._macro_regime_mode = self.parameters.get("macro_regime_mode", DEFAULT_MODE)
        self._growth_series_id = self.parameters.get("growth_series_id", DEFAULT_GROWTH_SERIES_ID)
        self._inflation_series_id = self.parameters.get(
            "inflation_series_id",
            DEFAULT_INFLATION_SERIES_ID,
        )
        self._growth_lag_months = int(
            self.parameters.get("growth_lag_months", DEFAULT_GROWTH_LAG_MONTHS)
        )
        self._inflation_lag_months = int(
            self.parameters.get("inflation_lag_months", DEFAULT_INFLATION_LAG_MONTHS)
        )
        self._trend_years = int(self.parameters.get("trend_years", DEFAULT_TREND_YEARS))
        self._last_real_regime = None
        self._last_macro_regime_error = None
        self._initialize_growth_inflation_workflow_state()
        model = os.environ.get("AI_TRADING_TEAM_MODEL", "gemini-3.1-flash-lite")

        self.agents.create(
            name="macro_allocation_agent",
            model=model,
            allow_trading=False,
            include_builtin_tools=False,
            tools=[make_real_macro_regime_classifier_tool()],
            system_prompt=(
                "Macro allocation role: call the real FRED-backed macro_regime_classifier. "
                "Do not classify the macro regime yourself. Preserve the classifier's structured "
                "status, regime, basket_weights, evidence, data_quality, confidence, and reason fields. "
                "If the classifier returns status=blocked or status=failed, return that plainly. "
                "Do not place orders. Do not decide whether today is a run day; the strategy code owns cadence."
            ),
        )
        self._create_growth_inflation_downstream_agents(model)

    def on_trading_iteration(self):
        current_datetime = self.get_datetime()
        current_date_obj = current_datetime.date()
        current_date = current_date_obj.isoformat()
        cadence_event = self._scheduled_workflow_decision(current_date_obj)
        if not cadence_event["should_run"]:
            self._record_scheduled_workflow_event(cadence_event)
            return
        self._mark_scheduled_workflow_attempted(cadence_event)
        basket_universes = self.parameters.get("basket_universes", BASKET_UNIVERSES)

        try:
            macro_result = self.agents["macro_allocation_agent"].run(
                task_prompt=(
                    "Run the real FRED-backed macro allocation step by calling macro_regime_classifier. "
                    "Return one JSON object preserving status, regime, basket_weights, growth_evidence, "
                    "inflation_evidence, data_quality, confidence, and reason fields."
                ),
                context={
                    "date": current_date,
                    "macro_regime_mode": self._macro_regime_mode,
                    "growth_series_id": self._growth_series_id,
                    "inflation_series_id": self._inflation_series_id,
                    "growth_lag_months": self._growth_lag_months,
                    "inflation_lag_months": self._inflation_lag_months,
                    "trend_years": self._trend_years,
                    "basket_universes": basket_universes,
                },
            )
            macro_report = _parse_json_summary(macro_result.summary, "macro_allocation_agent")
        except ValueError as exc:
            self._last_macro_regime_error = {
                "status": "failed",
                "reason": str(exc),
            }
            self._last_execution_plan_error = None
            self._last_target_portfolio_planner_result = None
            self._log_growth_inflation_workflow_blocked(f"Real quadrant macro workflow blocked: {exc}")
            return

        if str(macro_report.get("status") or "").strip().lower() != "passed":
            self._last_macro_regime_error = macro_report
            self._last_execution_plan_error = None
            self._last_target_portfolio_planner_result = None
            return

        self._last_macro_regime_error = None
        self._run_growth_inflation_downstream_workflow(
            current_date,
            macro_report,
            "Real quadrant workflow blocked",
        )
```

- [ ] **Step 2: Adapt restored real strategy tests to current basket tool surfaces**

In `tests/test_ai_trading_team_growth_inflation_quadrant.py`, update expectations so they match the current branch:

```python
    for basket_agent in (
        "equity_basket_agent",
        "nominal_bond_basket_agent",
    ):
        assert created_tool_names(created[basket_agent]) == {
            "market_load_history_tables_summary",
            "market_last_price",
        }
    for news_enabled_basket_agent in (
        "commodity_basket_agent",
        "tips_basket_agent",
    ):
        assert created_tool_names(created[news_enabled_basket_agent]) == {
            "market_load_history_tables_summary",
            "market_last_price",
            "alpaca_news",
        }
```

Also assert weekly defaults:

```python
def test_real_strategy_parameters_include_current_weekly_cadence_defaults():
    _module, strategy_class = load_real_strategy_module()

    assert strategy_class.parameters["run_frequency"] == "weekly"
    assert strategy_class.parameters["weekly_run_weekday"] == "MON"
    assert strategy_class.parameters["weekly_holiday_policy"] == "first_open_trading_day"
```

- [ ] **Step 3: Add a test that the real strategy blocks before downstream on skipped weekly days**

Add:

```python
def test_real_strategy_skips_before_weekly_run_day_without_macro_call():
    _module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.parameters["weekly_run_weekday"] = "WED"
    strategy.initialize()

    strategy.on_trading_iteration()

    assert agent_manager["macro_allocation_agent"].calls == []
    assert strategy._scheduled_workflow_events[-1]["reason"] == "before_weekly_run_day"
```

- [ ] **Step 4: Run real strategy tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_growth_inflation_quadrant.py -q
```

Expected:

```text
passed
```

- [ ] **Step 5: Run mock strategy tests again to prove no downstream regression**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected:

```text
passed
```

- [ ] **Step 6: Commit the real strategy entrypoint**

Run:

```powershell
git add lumibot\example_strategies\ai_trading_team_growth_inflation_quadrant.py tests\test_ai_trading_team_growth_inflation_quadrant.py
git commit -m "feat: add real growth inflation quadrant strategy"
```

Expected:

```text
[feature/real-macro-integration-on-quadrant ...] feat: add real growth inflation quadrant strategy
```

---

## Task 4: Register The Real Strategy In The Benchmark Runner

**Files:**
- Modify: `scripts/run_ai_trading_team_examples_benchmark.py`
- Modify: `tests/test_ai_trading_team_growth_inflation_quadrant.py`
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add the benchmark registry entry**

Modify `STRATEGIES` in `scripts/run_ai_trading_team_examples_benchmark.py` by adding this mapping immediately after `mock-growth-inflation-quadrant`:

```python
        "growth-inflation-quadrant": (
            "lumibot.example_strategies.ai_trading_team_growth_inflation_quadrant",
            "AITradingTeamGrowthInflationQuadrantStrategy",
        ),
```

Keep `mock-growth-inflation-quadrant`.

- [ ] **Step 2: Expand lazy import test strategy module list**

In `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`, update the `strategy_modules` tuple inside `test_examples_benchmark_import_does_not_require_backtesting_stack` to include:

```python
        "lumibot.example_strategies.ai_trading_team_growth_inflation_quadrant",
```

- [ ] **Step 3: Run benchmark registry tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_growth_inflation_quadrant.py::test_examples_benchmark_exposes_real_growth_inflation_quadrant_strategy tests/test_ai_trading_team_mock_growth_inflation_quadrant.py::test_examples_benchmark_exposes_mock_quadrant_strategy tests/test_ai_trading_team_mock_growth_inflation_quadrant.py::test_examples_benchmark_import_does_not_require_backtesting_stack -q
```

Expected:

```text
passed
```

- [ ] **Step 4: Commit benchmark registration**

Run:

```powershell
git add scripts\run_ai_trading_team_examples_benchmark.py tests\test_ai_trading_team_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: register real quadrant benchmark"
```

Expected:

```text
[feature/real-macro-integration-on-quadrant ...] feat: register real quadrant benchmark
```

---

## Task 5: Bring Forward The No-LLM Regime Scanner

**Files:**
- Create: `scripts/scan_growth_inflation_regimes.py`
- Create: `tests/test_growth_inflation_regime_scan.py`
- Create: `docs/superpowers/specs/2026-08-12-growth-inflation-regime-scan-tool-design.md`
- Create: `docs/superpowers/plans/2026-08-12-growth-inflation-regime-scan-tool.md`

- [ ] **Step 1: Restore scanner files from the real macro branch**

Run:

```powershell
git restore --source feature/real-fred-growth-inflation-regime -- `
  scripts/scan_growth_inflation_regimes.py `
  tests/test_growth_inflation_regime_scan.py `
  docs/superpowers/specs/2026-08-12-growth-inflation-regime-scan-tool-design.md `
  docs/superpowers/plans/2026-08-12-growth-inflation-regime-scan-tool.md
```

Expected:

```text
```

No terminal output is expected on success.

- [ ] **Step 2: Verify scanner dependency already exists**

Run:

```powershell
Test-Path scripts\validate_fred_growth_inflation_data.py
```

Expected:

```text
True
```

If this returns `False`, stop and restore that file from the real macro branch before continuing:

```powershell
git restore --source feature/real-fred-growth-inflation-regime -- scripts/validate_fred_growth_inflation_data.py tests/test_fred_growth_inflation_data_availability.py
```

- [ ] **Step 3: Run scanner tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_growth_inflation_regime_scan.py -q
```

Expected:

```text
passed
```

- [ ] **Step 4: Run FRED availability tests to protect scanner dependency**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_fred_growth_inflation_data_availability.py tests/test_fred_macro.py -q
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit scanner files**

Run:

```powershell
git add scripts\scan_growth_inflation_regimes.py tests\test_growth_inflation_regime_scan.py docs\superpowers\specs\2026-08-12-growth-inflation-regime-scan-tool-design.md docs\superpowers\plans\2026-08-12-growth-inflation-regime-scan-tool.md
git commit -m "feat: add growth inflation regime scanner"
```

Expected:

```text
[feature/real-macro-integration-on-quadrant ...] feat: add growth inflation regime scanner
```

---

## Task 6: Run Focused Integration Test Suite

**Files:**
- No intended file edits unless tests reveal defects.

- [ ] **Step 1: Run focused quadrant and FRED tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest `
  tests/test_ai_trading_team_mock_growth_inflation_quadrant.py `
  tests/test_ai_trading_team_growth_inflation_quadrant.py `
  tests/test_growth_inflation_regime_scan.py `
  tests/test_fred_growth_inflation_data_availability.py `
  tests/test_fred_macro.py `
  -q
```

Expected:

```text
passed
```

- [ ] **Step 2: Run import and formatting checks for touched Python files**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m ruff check `
  lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py `
  lumibot\example_strategies\ai_trading_team_growth_inflation_quadrant.py `
  lumibot\example_strategies\fred_growth_inflation_regime_classifier.py `
  scripts\run_ai_trading_team_examples_benchmark.py `
  scripts\scan_growth_inflation_regimes.py `
  tests\test_ai_trading_team_mock_growth_inflation_quadrant.py `
  tests\test_ai_trading_team_growth_inflation_quadrant.py `
  tests\test_growth_inflation_regime_scan.py
```

Expected:

```text
All checks passed!
```

If `ruff` is unavailable in the venv, run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m compileall `
  lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py `
  lumibot\example_strategies\ai_trading_team_growth_inflation_quadrant.py `
  lumibot\example_strategies\fred_growth_inflation_regime_classifier.py `
  scripts\run_ai_trading_team_examples_benchmark.py `
  scripts\scan_growth_inflation_regimes.py
```

Expected:

```text
```

No compile errors.

- [ ] **Step 3: Commit any test-driven fixes**

If no file edits were needed, skip this step. If fixes were needed, run:

```powershell
git add `
  lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py `
  lumibot\example_strategies\ai_trading_team_growth_inflation_quadrant.py `
  lumibot\example_strategies\fred_growth_inflation_regime_classifier.py `
  scripts\run_ai_trading_team_examples_benchmark.py `
  scripts\scan_growth_inflation_regimes.py `
  tests\test_ai_trading_team_mock_growth_inflation_quadrant.py `
  tests\test_ai_trading_team_growth_inflation_quadrant.py `
  tests\test_growth_inflation_regime_scan.py
git commit -m "fix: stabilize real macro integration tests"
```

Expected:

```text
[feature/real-macro-integration-on-quadrant ...] fix: stabilize real macro integration tests
```

---

## Task 7: Run No-LLM Scanner Validation

**Files:**
- Runtime artifacts under `artifacts/macro_regime_scans/`.
- No source edits expected.

- [ ] **Step 1: Run a short scanner smoke test**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe scripts\scan_growth_inflation_regimes.py `
  --env-file D:\Lumibot\project_notes\API.txt `
  --start 2025-06-24 `
  --end 2025-07-08 `
  --calendar trading-days `
  --run-id smoke-real-macro-integration
```

Expected stdout JSON includes:

```json
{"status":"passed"}
```

Expected files:

```text
artifacts/macro_regime_scans/smoke-real-macro-integration/daily_regimes.csv
artifacts/macro_regime_scans/smoke-real-macro-integration/regime_transitions.csv
artifacts/macro_regime_scans/smoke-real-macro-integration/summary.md
artifacts/macro_regime_scans/smoke-real-macro-integration/scan_metadata.json
```

- [ ] **Step 2: Verify scanner artifacts do not leak secrets**

Run:

```powershell
rg -n "sk-|FRED_API_KEY=|OPENAI_API_KEY=|api_key=[A-Za-z0-9]" artifacts\macro_regime_scans\smoke-real-macro-integration
```

Expected:

```text
```

No matches.

- [ ] **Step 3: Run a two-year weekly scanner pass**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe scripts\scan_growth_inflation_regimes.py `
  --env-file D:\Lumibot\project_notes\API.txt `
  --start 2024-08-14 `
  --end 2026-08-14 `
  --calendar trading-days `
  --window-before 5 `
  --window-after 5 `
  --run-id two-year-real-macro-integration
```

Expected stdout JSON includes:

```json
{"status":"passed"}
```

Open:

```text
artifacts/macro_regime_scans/two-year-real-macro-integration/summary.md
```

Expected:

```text
Regime Counts
Transitions
```

- [ ] **Step 4: Commit scanner validation note if source changes were needed**

If scanner code required fixes, commit them:

```powershell
git add scripts\scan_growth_inflation_regimes.py tests\test_growth_inflation_regime_scan.py
git commit -m "fix: stabilize real macro regime scanner"
```

If scanner passed without source changes, do not commit runtime artifacts unless the user explicitly asks to version them.

---

## Task 8: Run Paid Real Strategy Smoke Backtests

**Files:**
- Runtime artifacts under `artifacts/ai_trading_team_example_benchmarks/`.
- No source edits expected unless a bug is found.

- [ ] **Step 1: Confirm OpenAI model and key are loaded**

Run:

```powershell
$env:AI_TRADING_TEAM_MODEL='openai/gpt-5.6-luna'
D:\Lumibot\.venv\Scripts\python.exe -c "from pathlib import Path; import os; path = Path(r'D:\Lumibot\project_notes\API.txt'); print('api_file_exists', path.exists()); print('model', os.environ.get('AI_TRADING_TEAM_MODEL'))"
```

Expected:

```text
api_file_exists True
model openai/gpt-5.6-luna
```

Do not print the API key.

- [ ] **Step 2: Run a one-day real strategy backtest**

Run:

```powershell
$env:AI_TRADING_TEAM_MODEL='openai/gpt-5.6-luna'
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py `
  --env-file D:\Lumibot\project_notes\API.txt `
  --strategy growth-inflation-quadrant `
  --start 2025-06-24 `
  --end 2025-06-25 `
  --max-workers 1 `
  --run-frequency daily
```

Expected stdout includes:

```json
{"status":"passed","strategy":"growth-inflation-quadrant"}
```

- [ ] **Step 3: Inspect the one-day trace for real macro evidence**

Use the printed artifact directory and run:

```powershell
$latestBenchmarkRoot = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory | Sort-Object LastWriteTime | Select-Object -Last 1
$realStrategyArtifact = Get-ChildItem $latestBenchmarkRoot.FullName -Directory | Where-Object { $_.Name -eq "growth-inflation-quadrant" } | Select-Object -First 1
rg -n '"mock": false|"kind": "fred_macro_regime"|GDPC1|CPIAUCSL|macro_regime_classifier' $realStrategyArtifact.FullName
```

Expected:

```text
"mock": false
"kind": "fred_macro_regime"
GDPC1
CPIAUCSL
macro_regime_classifier
```

Also verify no mock wording appears in the real run trace:

```powershell
$latestBenchmarkRoot = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory | Sort-Object LastWriteTime | Select-Object -Last 1
$realStrategyArtifact = Get-ChildItem $latestBenchmarkRoot.FullName -Directory | Where-Object { $_.Name -eq "growth-inflation-quadrant" } | Select-Object -First 1
rg -n "seeded_random|Mock classifier|fake macro|mock macro" $realStrategyArtifact.FullName
```

Expected:

```text
```

No matches in the real strategy artifact directory.

- [ ] **Step 4: Run a short weekly real strategy backtest**

Run:

```powershell
$env:AI_TRADING_TEAM_MODEL='openai/gpt-5.6-luna'
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py `
  --env-file D:\Lumibot\project_notes\API.txt `
  --strategy growth-inflation-quadrant `
  --start 2025-06-24 `
  --end 2025-07-08 `
  --max-workers 1 `
  --run-frequency weekly `
  --weekly-run-weekday MON
```

Expected stdout includes:

```json
{"status":"passed","strategy":"growth-inflation-quadrant"}
```

- [ ] **Step 5: Verify weekly cadence behavior in artifacts**

Use the printed artifact directory and run:

```powershell
$latestBenchmarkRoot = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory | Sort-Object LastWriteTime | Select-Object -Last 1
$realStrategyArtifact = Get-ChildItem $latestBenchmarkRoot.FullName -Directory | Where-Object { $_.Name -eq "growth-inflation-quadrant" } | Select-Object -First 1
rg -n "weekly_run_day|first_observed_after_preferred_weekday|weekly_workflow_already_attempted|macro_allocation_agent" $realStrategyArtifact.FullName
```

Expected:

```text
weekly_run_day
macro_allocation_agent
```

The trace should show the full agent workflow only on scheduled weekly review days.

- [ ] **Step 6: Commit source fixes if backtests revealed bugs**

If source edits were needed, run focused tests again:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest `
  tests/test_ai_trading_team_mock_growth_inflation_quadrant.py `
  tests/test_ai_trading_team_growth_inflation_quadrant.py `
  tests/test_growth_inflation_regime_scan.py `
  -q
```

Then commit:

```powershell
git add `
  lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py `
  lumibot\example_strategies\ai_trading_team_growth_inflation_quadrant.py `
  lumibot\example_strategies\fred_growth_inflation_regime_classifier.py `
  scripts\run_ai_trading_team_examples_benchmark.py `
  scripts\scan_growth_inflation_regimes.py `
  tests\test_ai_trading_team_mock_growth_inflation_quadrant.py `
  tests\test_ai_trading_team_growth_inflation_quadrant.py `
  tests\test_growth_inflation_regime_scan.py
git commit -m "fix: stabilize real macro quadrant backtest"
```

If no source edits were needed, do not commit runtime artifacts.

---

## Task 9: Final Verification And Handoff

**Files:**
- No intended source edits.

- [ ] **Step 1: Run final status and log summary**

Run:

```powershell
git status --short --branch
git log --oneline --decorate -8
```

Expected:

```text
## feature/real-macro-integration-on-quadrant
```

No uncommitted source files. Runtime artifact files may exist only if they are ignored by git.

- [ ] **Step 2: Run final focused verification suite**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest `
  tests/test_ai_trading_team_mock_growth_inflation_quadrant.py `
  tests/test_ai_trading_team_growth_inflation_quadrant.py `
  tests/test_growth_inflation_regime_scan.py `
  tests/test_fred_growth_inflation_data_availability.py `
  tests/test_fred_macro.py `
  -q
```

Expected:

```text
passed
```

- [ ] **Step 3: Summarize validation artifacts**

Record in the final response:

```text
Scanner smoke artifact:
Scanner two-year artifact:
One-day real backtest artifact:
Short weekly real backtest artifact:
Focused tests:
Known residual risks:
```

Known residual risks should mention that annual weekly performance validation is future work and that scanner-selected windows should guide the annual run.

- [ ] **Step 4: Do not merge automatically**

Stop after the integration branch is validated. Ask the user whether to:

```text
1. keep developing on feature/real-macro-integration-on-quadrant
2. open a PR into the user's dev branch
3. merge locally into dev and push
```

Do not merge to `dev` without explicit user approval.

---

## Self-Review Checklist

- The plan keeps current latest branch behavior authoritative.
- The plan migrates the real classifier.
- The plan migrates the scanner as a mandatory validation gate.
- The plan avoids blindly replacing the current mock strategy.
- The plan verifies real strategy weekly cadence.
- The plan verifies benchmark registry contains both mock and real strategy names.
- The plan runs cheap tests before paid LLM backtests.
- The plan checks trace artifacts for `mock=false` and FRED evidence.
- The plan stops before merging.
