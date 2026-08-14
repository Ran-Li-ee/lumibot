# Weekly Growth / Inflation Quadrant Run Cadence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the mock Growth / Inflation quadrant strategy's full agent workflow once per observed trading week by default, while keeping daily cadence available for debugging.

**Architecture:** Keep Lumibot's existing `self.sleeptime = "1D"` daily trigger, then add a local strategy-level cadence gate at the top of `on_trading_iteration()`. The cadence gate is deterministic Python state, records skip/run events, and returns before any agent is called on skipped days.

**Tech Stack:** Python, pytest, Lumibot strategy parameters, existing AI agent manager/test fakes, existing benchmark runner.

---

## Files And Responsibilities

- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
  - Add cadence constants and helper functions.
  - Add weekly cadence parameters and state.
  - Gate `on_trading_iteration()` before agent calls.
  - Lightly adjust agent prompts from daily/session wording to scheduled-review wording.

- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
  - Add cadence helper unit tests.
  - Update initialization tests for weekly defaults.
  - Add workflow tests proving skipped days do not call agents.
  - Add prompt wording tests for stale daily-trading phrases.

- Modify: `scripts/run_ai_trading_team_examples_benchmark.py`
  - Add optional CLI arguments for strategy cadence overrides.
  - Pass cadence overrides through `run_backtest(..., parameters=...)`.

- Create: `docs/superpowers/notes/2026-08-14-weekly-quadrant-run-cadence-validation.md`
  - Record unit test and benchmark validation results.

---

### Task 1: Write Failing Cadence Tests

**Files:**
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add cadence helper tests**

Add these tests after `test_initialize_creates_seven_agent_mock_quadrant_workflow`.

```python
def test_weekly_cadence_decision_runs_on_default_monday():
    module, _strategy_class = load_strategy_module()

    decision = module.scheduled_workflow_decision(
        current_date=datetime(2024, 9, 9).date(),
        run_frequency="weekly",
        weekly_run_weekday="MON",
        attempted_week_keys=set(),
    )

    assert decision["should_run"] is True
    assert decision["status"] == "run"
    assert decision["reason"] == "weekly_run_day"
    assert decision["week_key"] == "2024-W37"


def test_weekly_cadence_decision_runs_on_first_observed_day_after_missing_monday():
    module, _strategy_class = load_strategy_module()

    decision = module.scheduled_workflow_decision(
        current_date=datetime(2024, 9, 10).date(),
        run_frequency="weekly",
        weekly_run_weekday="MON",
        attempted_week_keys=set(),
    )

    assert decision["should_run"] is True
    assert decision["status"] == "run"
    assert decision["reason"] == "first_observed_after_preferred_weekday"
    assert decision["week_key"] == "2024-W37"


def test_weekly_cadence_decision_skips_before_preferred_weekday():
    module, _strategy_class = load_strategy_module()

    decision = module.scheduled_workflow_decision(
        current_date=datetime(2024, 9, 9).date(),
        run_frequency="weekly",
        weekly_run_weekday="WED",
        attempted_week_keys=set(),
    )

    assert decision["should_run"] is False
    assert decision["status"] == "skipped"
    assert decision["reason"] == "before_weekly_run_day"
    assert decision["week_key"] == "2024-W37"


def test_weekly_cadence_decision_skips_after_week_attempted():
    module, _strategy_class = load_strategy_module()

    decision = module.scheduled_workflow_decision(
        current_date=datetime(2024, 9, 10).date(),
        run_frequency="weekly",
        weekly_run_weekday="MON",
        attempted_week_keys={"2024-W37"},
    )

    assert decision["should_run"] is False
    assert decision["status"] == "skipped"
    assert decision["reason"] == "weekly_workflow_already_attempted"


def test_daily_cadence_decision_runs_every_day():
    module, _strategy_class = load_strategy_module()

    decision = module.scheduled_workflow_decision(
        current_date=datetime(2024, 9, 10).date(),
        run_frequency="daily",
        weekly_run_weekday="MON",
        attempted_week_keys={"2024-W37"},
    )

    assert decision["should_run"] is True
    assert decision["status"] == "run"
    assert decision["reason"] == "daily_frequency"
```

- [ ] **Step 2: Add invalid cadence input tests**

Add:

```python
def test_weekly_cadence_decision_rejects_invalid_frequency():
    module, _strategy_class = load_strategy_module()

    with pytest.raises(ValueError, match="run_frequency must be 'daily' or 'weekly'"):
        module.scheduled_workflow_decision(
            current_date=datetime(2024, 9, 10).date(),
            run_frequency="hourly",
            weekly_run_weekday="MON",
            attempted_week_keys=set(),
        )


def test_weekly_cadence_decision_rejects_invalid_weekday():
    module, _strategy_class = load_strategy_module()

    with pytest.raises(ValueError, match="weekly_run_weekday must be one of"):
        module.scheduled_workflow_decision(
            current_date=datetime(2024, 9, 10).date(),
            run_frequency="weekly",
            weekly_run_weekday="SUN",
            attempted_week_keys=set(),
        )
```

- [ ] **Step 3: Run helper tests and verify they fail**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_weekly_cadence_decision_runs_on_default_monday tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_weekly_cadence_decision_runs_on_first_observed_day_after_missing_monday tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_weekly_cadence_decision_skips_before_preferred_weekday tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_weekly_cadence_decision_skips_after_week_attempted tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_daily_cadence_decision_runs_every_day tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_weekly_cadence_decision_rejects_invalid_frequency tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_weekly_cadence_decision_rejects_invalid_weekday -q
```

Expected: FAIL with an `AttributeError` similar to:

```text
module 'lumibot.example_strategies.ai_trading_team_mock_growth_inflation_quadrant' has no attribute 'scheduled_workflow_decision'
```

- [ ] **Step 4: Commit failing tests**

```powershell
git add tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "test: define weekly quadrant cadence decisions"
```

---

### Task 2: Implement Cadence Helper And Strategy State

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add cadence constants and helper functions**

In `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`, add this block after `BASKET_AGENT_NAMES`:

```python
WEEKDAY_INDEX_BY_CODE = {
    "MON": 0,
    "TUE": 1,
    "WED": 2,
    "THU": 3,
    "FRI": 4,
}

ALLOWED_RUN_FREQUENCIES = {"daily", "weekly"}


def normalize_run_frequency(value: Any) -> str:
    frequency = str(value or "weekly").strip().lower()
    if frequency not in ALLOWED_RUN_FREQUENCIES:
        raise ValueError("run_frequency must be 'daily' or 'weekly'.")
    return frequency


def normalize_weekly_run_weekday(value: Any) -> str:
    weekday = str(value or "MON").strip().upper()
    if weekday not in WEEKDAY_INDEX_BY_CODE:
        allowed = ", ".join(WEEKDAY_INDEX_BY_CODE)
        raise ValueError(f"weekly_run_weekday must be one of: {allowed}.")
    return weekday


def iso_week_key(value: date_type) -> str:
    iso_year, iso_week, _weekday = value.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def scheduled_workflow_decision(
    *,
    current_date: date_type,
    run_frequency: Any,
    weekly_run_weekday: Any,
    attempted_week_keys: set[str],
) -> dict[str, Any]:
    frequency = normalize_run_frequency(run_frequency)
    preferred_weekday = normalize_weekly_run_weekday(weekly_run_weekday)
    week_key = iso_week_key(current_date)
    base = {
        "date": current_date.isoformat(),
        "run_frequency": frequency,
        "weekly_run_weekday": preferred_weekday,
        "week_key": week_key,
    }

    if frequency == "daily":
        return {**base, "should_run": True, "status": "run", "reason": "daily_frequency"}

    if week_key in attempted_week_keys:
        return {
            **base,
            "should_run": False,
            "status": "skipped",
            "reason": "weekly_workflow_already_attempted",
        }

    preferred_index = WEEKDAY_INDEX_BY_CODE[preferred_weekday]
    current_index = current_date.weekday()
    if current_index == preferred_index:
        return {**base, "should_run": True, "status": "run", "reason": "weekly_run_day"}
    if current_index > preferred_index:
        return {
            **base,
            "should_run": True,
            "status": "run",
            "reason": "first_observed_after_preferred_weekday",
        }
    return {
        **base,
        "should_run": False,
        "status": "skipped",
        "reason": "before_weekly_run_day",
    }
```

- [ ] **Step 2: Update strategy default parameters and initialize state**

In `AITradingTeamMockGrowthInflationQuadrantStrategy.parameters`, add:

```python
"run_frequency": "weekly",
"weekly_run_weekday": "MON",
"weekly_holiday_policy": "first_open_trading_day",
```

In `initialize()`, after mock-regime fields, add:

```python
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
```

- [ ] **Step 3: Update initialization test**

In `test_initialize_creates_seven_agent_mock_quadrant_workflow`, after existing cadence assertions, add:

```python
assert strategy._run_frequency == "weekly"
assert strategy._weekly_run_weekday == "MON"
assert strategy._weekly_holiday_policy == "first_open_trading_day"
assert strategy._scheduled_workflow_attempted_week_keys == set()
assert strategy._scheduled_workflow_events == []
```

- [ ] **Step 4: Run cadence and initialization tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_initialize_creates_seven_agent_mock_quadrant_workflow tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_weekly_cadence_decision_runs_on_default_monday tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_weekly_cadence_decision_runs_on_first_observed_day_after_missing_monday tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_weekly_cadence_decision_skips_before_preferred_weekday tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_weekly_cadence_decision_skips_after_week_attempted tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_daily_cadence_decision_runs_every_day tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_weekly_cadence_decision_rejects_invalid_frequency tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_weekly_cadence_decision_rejects_invalid_weekday -q
```

Expected: PASS.

- [ ] **Step 5: Commit helper implementation**

```powershell
git add lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: add weekly quadrant cadence helper"
```

---

### Task 3: Gate `on_trading_iteration()` Before Agent Calls

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Write skip behavior test**

Add this test near `test_on_trading_iteration_runs_agents_in_expected_order_and_context`:

```python
def test_on_trading_iteration_skips_before_weekly_run_day_without_agent_calls():
    _module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.parameters["weekly_run_weekday"] = "WED"
    strategy.get_datetime = lambda: datetime(2024, 9, 9, 9, 30)
    strategy.initialize()

    strategy.on_trading_iteration()

    assert [agent.name for agent in agent_manager._agents.values() if agent.calls] == []
    assert strategy._scheduled_workflow_events == [
        {
            "date": "2024-09-09",
            "run_frequency": "weekly",
            "weekly_run_weekday": "WED",
            "week_key": "2024-W37",
            "status": "skipped",
            "reason": "before_weekly_run_day",
            "last_weekly_run_date": None,
        }
    ]
```

- [ ] **Step 2: Write repeated-week skip test**

Add:

```python
def test_on_trading_iteration_runs_once_per_observed_week():
    module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    now = {"value": datetime(2024, 9, 9, 9, 30)}
    strategy.get_datetime = lambda: now["value"]
    strategy.initialize()

    macro_report = {
        "agent": "macro_allocation_agent",
        "regime": "growth_up_inflation_down",
        "regime_changed": True,
        "basket_weights": {"equity": 0.50, "commodity": 0.00, "tips": 0.25, "nominal_bond": 0.25},
        "mock": True,
        "reason_brief": "mock",
    }
    basket_reports = {
        "equity_basket_agent": {
            "basket_id": "equity",
            "target_weight": 0.50,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["equity"],
            "selected_symbol": "QQQ",
            "reason_brief": "selected",
        },
        "commodity_basket_agent": {
            "basket_id": "commodity",
            "target_weight": 0.00,
            "status": "inactive",
            "candidate_symbols": module.BASKET_UNIVERSES["commodity"],
            "selected_symbol": None,
            "reason_brief": "inactive",
        },
        "tips_basket_agent": {
            "basket_id": "tips",
            "target_weight": 0.25,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["tips"],
            "selected_symbol": "TIP",
            "reason_brief": "selected",
        },
        "nominal_bond_basket_agent": {
            "basket_id": "nominal_bond",
            "target_weight": 0.25,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["nominal_bond"],
            "selected_symbol": "IEF",
            "reason_brief": "selected",
        },
    }
    planner_plan = {
        "schema_version": 1,
        "intent": "hold",
        "orders": [],
    }
    portfolio_summary = {
        "decision": {"type": "hold", "reason_brief": "already aligned"},
        "target_portfolio": [
            {"basket_id": "equity", "symbol": "QQQ", "target_weight": 0.50},
            {"basket_id": "tips", "symbol": "TIP", "target_weight": 0.25},
            {"basket_id": "nominal_bond", "symbol": "IEF", "target_weight": 0.25},
        ],
        "execution_plan": planner_plan,
    }
    agent_manager.summaries["macro_allocation_agent"] = _json_summary(macro_report)
    for agent_name, report in basket_reports.items():
        agent_manager.summaries[agent_name] = _json_summary(report)
    agent_manager.summaries["portfolio_decision_agent"] = _json_summary(portfolio_summary)
    agent_manager.tool_calls["portfolio_decision_agent"] = ["target_portfolio_to_execution_plan"]
    agent_manager.planner_results["portfolio_decision_agent"] = {"execution_plan": planner_plan}

    strategy.on_trading_iteration()
    now["value"] = datetime(2024, 9, 10, 9, 30)
    strategy.on_trading_iteration()

    assert len(agent_manager["macro_allocation_agent"].calls) == 1
    assert len(agent_manager["portfolio_decision_agent"].calls) == 1
    assert strategy._scheduled_workflow_attempted_week_keys == {"2024-W37"}
    assert strategy._scheduled_workflow_events[-1]["status"] == "skipped"
    assert strategy._scheduled_workflow_events[-1]["reason"] == "weekly_workflow_already_attempted"
    assert strategy._scheduled_workflow_events[-1]["last_weekly_run_date"] == "2024-09-09"
```

- [ ] **Step 3: Run new skip tests and verify they fail**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_on_trading_iteration_skips_before_weekly_run_day_without_agent_calls tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_on_trading_iteration_runs_once_per_observed_week -q
```

Expected: FAIL because `on_trading_iteration()` does not gate agent calls yet.

- [ ] **Step 4: Add cadence event helpers to the strategy**

In `AITradingTeamMockGrowthInflationQuadrantStrategy`, before `on_trading_iteration()`, add:

```python
    def _scheduled_workflow_decision(self, current_date: date_type) -> dict[str, Any]:
        return scheduled_workflow_decision(
            current_date=current_date,
            run_frequency=self._run_frequency,
            weekly_run_weekday=self._weekly_run_weekday,
            attempted_week_keys=self._scheduled_workflow_attempted_week_keys,
        )

    def _record_scheduled_workflow_event(self, event: dict[str, Any]) -> None:
        normalized = dict(event)
        normalized["last_weekly_run_date"] = self._last_scheduled_workflow_run_date
        self._scheduled_workflow_events.append(normalized)

    def _mark_scheduled_workflow_attempted(self, event: dict[str, Any]) -> None:
        if event["run_frequency"] == "weekly":
            self._scheduled_workflow_attempted_week_keys.add(event["week_key"])
        self._last_scheduled_workflow_run_date = event["date"]
        self._record_scheduled_workflow_event(event)
```

- [ ] **Step 5: Gate `on_trading_iteration()`**

At the start of `on_trading_iteration()`, replace:

```python
current_date = self.get_datetime().date().isoformat()
```

with:

```python
current_datetime = self.get_datetime()
current_date_obj = current_datetime.date()
current_date = current_date_obj.isoformat()
cadence_event = self._scheduled_workflow_decision(current_date_obj)
if not cadence_event["should_run"]:
    self._record_scheduled_workflow_event(cadence_event)
    return
self._mark_scheduled_workflow_attempted(cadence_event)
```

This deliberately marks the week as attempted before the macro/agent workflow
runs. If the workflow later blocks, the same week should not repeatedly spend
LLM calls.

- [ ] **Step 6: Run skip and existing workflow tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_on_trading_iteration_skips_before_weekly_run_day_without_agent_calls tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_on_trading_iteration_runs_once_per_observed_week tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_on_trading_iteration_runs_agents_in_expected_order_and_context -q
```

Expected: PASS.

- [ ] **Step 7: Commit cadence gate**

```powershell
git add lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: gate quadrant workflow to weekly cadence"
```

---

### Task 4: Adjust Prompt Wording For Scheduled Reviews

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add prompt wording test**

Add this test near the other prompt tests:

```python
def test_quadrant_prompts_use_scheduled_review_language():
    _module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()

    serialized = "\n".join(
        [
            str(agent["system_prompt"])
            for agent in agent_manager.created
        ]
    ).lower()

    for required_phrase in (
        "scheduled allocation review",
        "scheduled review",
    ):
        assert required_phrase in serialized

    for forbidden_phrase in (
        "daily rebalance",
        "trade every day",
        "refresh the whole portfolio every session",
        "today's macro data changed",
    ):
        assert forbidden_phrase not in serialized
```

- [ ] **Step 2: Run prompt test and verify it fails**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_quadrant_prompts_use_scheduled_review_language -q
```

Expected: FAIL because prompts do not yet contain the scheduled review language.

- [ ] **Step 3: Update macro allocation agent prompt and task**

In `initialize()`, replace the `macro_allocation_agent` `system_prompt` with:

```python
system_prompt=(
    "Macro allocation role: this is the current scheduled allocation review. "
    "Call the mock macro_regime_classifier and return the regime, basket weights, "
    "mock flag, regime_changed, and a compact allocation note. Do not place orders. "
    "Do not decide whether today is a run day; the strategy code owns cadence."
),
```

In `on_trading_iteration()`, replace the macro allocation `task_prompt` with:

```python
task_prompt=(
    "Run the mock macro allocation step for the current scheduled review date and return one "
    "JSON object with regime, basket_weights, mock flag, regime_changed, and reason_brief."
),
```

- [ ] **Step 4: Update basket prompt base strings**

In `basket_agent_system_prompt()`, replace the base string with:

```python
base = (
    f"{basket_id.replace('_', ' ').title()} basket role: stay inside the assigned basket "
    f"({symbols}). For the current scheduled review, select one representative symbol when active, "
    "or report inactive when its target weight is zero. Return basket_id, selected_symbol, status, "
    "and reason_brief. Do not place orders."
)
```

In `basket_agent_task_prompt()`, replace the base string with:

```python
base = (
    "Review only the assigned basket for the current scheduled review and return one JSON object "
    "with basket_id, target_weight, status, candidate_symbols, selected_symbol, and reason_brief. "
    "candidate_symbols must copy the assigned basket_symbols exactly; "
    "do not replace it with a shortlist."
)
```

- [ ] **Step 5: Update portfolio and execution task wording lightly**

In `portfolio_decision_agent` `system_prompt`, change:

```python
"Portfolio decision role: do not redo macro or basket research. Merge the macro allocation report "
```

to:

```python
"Portfolio decision role: this is the current scheduled review. Do not redo macro or basket research. "
"Merge the macro allocation report "
```

In `portfolio_decision_agent` task prompt, change:

```python
"Create target_portfolio from the provided macro and basket reports, then call "
```

to:

```python
"Create target_portfolio for the current scheduled review from the provided macro and basket reports, then call "
```

Do not change execution agent logic. If editing its wording, keep the tool instruction:

```text
Call execution_plan_execute exactly once with the complete execution_plan.
```

- [ ] **Step 6: Run prompt and workflow tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_quadrant_prompts_use_scheduled_review_language tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_on_trading_iteration_runs_agents_in_expected_order_and_context -q
```

Expected: PASS.

- [ ] **Step 7: Commit prompt wording**

```powershell
git add lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "docs: align quadrant prompts with scheduled reviews"
```

---

### Task 5: Add Benchmark Cadence Overrides

**Files:**
- Modify: `scripts/run_ai_trading_team_examples_benchmark.py`
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add benchmark parameter helper tests**

Add these tests near existing benchmark runner tests:

```python
def test_examples_benchmark_builds_strategy_parameters_for_cadence_overrides():
    benchmark = importlib.import_module("scripts.run_ai_trading_team_examples_benchmark")
    args = SimpleNamespace(run_frequency="daily", weekly_run_weekday="TUE")

    assert benchmark._strategy_parameters_from_args(args) == {
        "run_frequency": "daily",
        "weekly_run_weekday": "TUE",
    }


def test_examples_benchmark_omits_empty_strategy_parameter_overrides():
    benchmark = importlib.import_module("scripts.run_ai_trading_team_examples_benchmark")
    args = SimpleNamespace(run_frequency=None, weekly_run_weekday=None)

    assert benchmark._strategy_parameters_from_args(args) == {}
```

- [ ] **Step 2: Run benchmark helper tests and verify they fail**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_examples_benchmark_builds_strategy_parameters_for_cadence_overrides tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_examples_benchmark_omits_empty_strategy_parameter_overrides -q
```

Expected: FAIL because `_strategy_parameters_from_args()` does not exist.

- [ ] **Step 3: Add benchmark helper**

In `scripts/run_ai_trading_team_examples_benchmark.py`, after `_parse_date()`, add:

```python
def _strategy_parameters_from_args(args: argparse.Namespace) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    if getattr(args, "run_frequency", None):
        parameters["run_frequency"] = args.run_frequency
    if getattr(args, "weekly_run_weekday", None):
        parameters["weekly_run_weekday"] = args.weekly_run_weekday
    return parameters
```

- [ ] **Step 4: Pass parameters into `run_backtest()`**

In `_run_one_strategy()`, before `try:`, add:

```python
strategy_parameters = _strategy_parameters_from_args(args)
```

Then add this keyword argument to `strategy_class.run_backtest(...)`:

```python
parameters=strategy_parameters,
```

Put it near `budget=args.budget` so benchmark configuration is easy to inspect.

- [ ] **Step 5: Add CLI options**

In `main()`, after `parser.add_argument("--strategy", ...)`, add:

```python
parser.add_argument("--run-frequency", choices=["daily", "weekly"])
parser.add_argument("--weekly-run-weekday", choices=["MON", "TUE", "WED", "THU", "FRI"])
```

Do not set defaults here. If the user omits these flags, the strategy class
defaults should apply.

- [ ] **Step 6: Run benchmark helper tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_examples_benchmark_builds_strategy_parameters_for_cadence_overrides tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_examples_benchmark_omits_empty_strategy_parameter_overrides -q
```

Expected: PASS.

- [ ] **Step 7: Run full targeted pytest file**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit benchmark override support**

```powershell
git add scripts\run_ai_trading_team_examples_benchmark.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: pass quadrant cadence overrides to benchmark"
```

---

### Task 6: Run Validation And Record Results

**Files:**
- Create: `docs/superpowers/notes/2026-08-14-weekly-quadrant-run-cadence-validation.md`

- [ ] **Step 1: Run focused tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_tool_permissions.py tests\test_agent_runtime_provider_keys.py tests\test_agent_replay_ui_loader.py tests\test_agent_replay_ui_formatters.py -q
```

Expected: PASS.

- [ ] **Step 2: Run ruff on touched files**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py scripts\run_ai_trading_team_examples_benchmark.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 3: Run one-day benchmark**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-06 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file project_notes\API.txt
```

Expected: JSON output contains:

```json
{
  "strategy": "mock-growth-inflation-quadrant",
  "status": "passed"
}
```

This validates that first-observed-trading-day weekly behavior still allows
short one-day testing.

- [ ] **Step 4: Run multi-day weekly benchmark**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-13 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file project_notes\API.txt
```

Expected: JSON output contains:

```json
{
  "strategy": "mock-growth-inflation-quadrant",
  "status": "passed"
}
```

Expected trace behavior:

```text
The artifact's cache/agent_runtime/traces tree contains full agent traces only for weekly run dates.
It should not contain one full workflow per trading day.
```

- [ ] **Step 5: Run multi-day daily override benchmark**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-09 --run-frequency daily --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file project_notes\API.txt
```

Expected: JSON output contains:

```json
{
  "strategy": "mock-growth-inflation-quadrant",
  "status": "passed"
}
```

Expected trace behavior:

```text
Daily override produces more full agent workflow traces than weekly cadence for the same number of observed trading days.
```

- [ ] **Step 6: Create validation note**

Create `docs/superpowers/notes/2026-08-14-weekly-quadrant-run-cadence-validation.md` with this structure. Replace each result bullet with the observed status line, artifact path, or concise failure line from the commands run in Steps 1-5:

```markdown
# Weekly Quadrant Run Cadence Validation

Date: 2026-08-14

## Test Commands

- `D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_tool_permissions.py tests\test_agent_runtime_provider_keys.py tests\test_agent_replay_ui_loader.py tests\test_agent_replay_ui_formatters.py -q`
- `D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py scripts\run_ai_trading_team_examples_benchmark.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py`

## Benchmark Commands

- One-day weekly default:
  `D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-06 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file project_notes\API.txt`
- Multi-day weekly default:
  `D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-13 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file project_notes\API.txt`
- Multi-day daily override:
  `D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-09 --run-frequency daily --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file project_notes\API.txt`

## Results

- Focused pytest:
- Ruff:
- One-day weekly default artifact:
- Multi-day weekly default artifact:
- Multi-day daily override artifact:

## Cadence Evidence

- Weekly default full workflow run dates:
- Weekly default skipped dates or skipped count:
- Daily override full workflow run dates:

## Notes

- Weekly cadence uses strategy-level gating, not Lumibot global `1W` scheduling.
- Skipped days do not call LLM agents.
- The feature does not change macro classification, basket selection, portfolio planning, or execution tools.
```

- [ ] **Step 7: Commit validation note**

```powershell
git add docs\superpowers\notes\2026-08-14-weekly-quadrant-run-cadence-validation.md
git commit -m "docs: validate weekly quadrant cadence"
```

---

### Task 7: Final Review Before Merge Or Next Feature

**Files:**
- No planned file edits unless review finds a concrete issue.

- [ ] **Step 1: Check final git status**

Run:

```powershell
git status --short --branch
```

Expected:

```text
## feature/commodity-basket-universe-expansion
```

with no uncommitted files.

- [ ] **Step 2: Inspect commit list**

Run:

```powershell
git log --oneline -8
```

Expected: recent commits include:

```text
docs: validate weekly quadrant cadence
feat: pass quadrant cadence overrides to benchmark
docs: align quadrant prompts with scheduled reviews
feat: gate quadrant workflow to weekly cadence
feat: add weekly quadrant cadence helper
test: define weekly quadrant cadence decisions
docs: design weekly quadrant run cadence
```

- [ ] **Step 3: Summarize implementation status**

Prepare a short summary for the user:

```text
Weekly cadence is implemented for the mock Growth / Inflation quadrant strategy.
The full workflow now runs at most once per observed ISO week by default.
Daily cadence remains available with --run-frequency daily.
Skipped days return before agent calls and record skip events.
Validation commands and benchmark artifacts are recorded in the validation note.
```
