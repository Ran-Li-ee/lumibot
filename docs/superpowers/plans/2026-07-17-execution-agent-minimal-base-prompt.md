# Execution Agent Minimal Base Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an execution-only minimal base prompt mode and use it for the growth execution strategy's `execution_agent`.

**Architecture:** Extend `AgentHandle` and `AgentManager.create` with a per-agent `base_system_prompt_mode`. The existing default mode keeps current behavior unchanged, while `execution_minimal` selects a short execution-focused base prompt that is still recorded in trace data.

**Tech Stack:** Python, pytest, ruff, LumiBot agent runtime, existing agent replay traces.

---

## File Structure

- Modify `lumibot/components/agents/manager.py`
  - Add `BaseSystemPromptMode` type alias.
  - Add `base_system_prompt_mode` to `AgentHandle.__init__`.
  - Add execution minimal base prompt helper.
  - Route `_base_system_prompt()` by mode.
  - Add `base_system_prompt_mode` to `AgentManager.create`.
- Modify `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`
  - Create `execution_agent` with `base_system_prompt_mode="execution_minimal"`.
- Modify `tests/test_agent_manager.py` or create it if missing
  - Verify default and execution minimal prompt modes through a lightweight strategy/manager fixture.
- Modify `tests/backtest/test_ai_trading_team_growth_execution_test.py` or create it if missing
  - Verify the strategy registers execution agent with minimal mode.
- Run existing replay UI tests because prompt fields are consumed by the UI.

---

## Task 1: Add Prompt Mode Unit Tests

**Files:**
- Test: `tests/test_agent_manager.py`

- [ ] **Step 1: Inspect existing agent manager tests**

Run:

```powershell
rg -n "AgentManager|AgentHandle|base_system_prompt|_compose_system_prompt|agents.create" tests
```

Expected: Find current tests or confirm there is no focused `tests/test_agent_manager.py`.

- [ ] **Step 2: Write failing tests for default and execution minimal modes**

If `tests/test_agent_manager.py` does not exist, create it. Add tests with this shape, adapting only fixture details required by current constructors:

```python
from lumibot.components.agents.manager import AgentHandle


class DummyVars(dict):
    def get(self, key, default=None):
        return super().get(key, default)

    def set(self, key, value):
        self[key] = value


class DummyStrategy:
    name = "DummyStrategy"
    market = "24/7"

    def __init__(self):
        self.vars = DummyVars()

    def get_datetime(self):
        return None


class DummyManager:
    def __init__(self):
        self.strategy = DummyStrategy()


def test_agent_handle_uses_default_base_prompt_by_default():
    handle = AgentHandle(
        manager=DummyManager(),
        name="decision_agent",
        system_prompt="Decision prompt.",
        default_model="test-model",
        runtime=object(),
    )

    prompt = handle._compose_system_prompt({"mode": "backtesting"})

    assert "DEFAULT INVESTOR POLICY" in prompt
    assert "Prefer no trade over a weak trade" in prompt
    assert "Decision prompt." in prompt


def test_agent_handle_execution_minimal_base_prompt_omits_decision_policy():
    handle = AgentHandle(
        manager=DummyManager(),
        name="execution_agent",
        system_prompt="Execution prompt.",
        default_model="test-model",
        runtime=object(),
        base_system_prompt_mode="execution_minimal",
    )

    prompt = handle._compose_system_prompt({"mode": "backtesting"})

    assert "You are operating as an order execution agent inside LumiBot." in prompt
    assert "Execute only the provided execution_plan." in prompt
    assert "DEFAULT INVESTOR POLICY" not in prompt
    assert "Prefer no trade over a weak trade" not in prompt
    assert "Require a real thesis" not in prompt
    assert "BACKTESTING SAFETY RULES" in prompt
    assert "Execution prompt." in prompt
```

- [ ] **Step 3: Run tests and verify they fail for missing argument**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py -q
```

Expected: The execution minimal test fails because `AgentHandle.__init__()` does not accept `base_system_prompt_mode`.

---

## Task 2: Implement Base Prompt Mode in Agent Manager

**Files:**
- Modify: `lumibot/components/agents/manager.py`
- Test: `tests/test_agent_manager.py`

- [ ] **Step 1: Add type alias near agent config constants**

Add:

```python
BaseSystemPromptMode = Literal["default", "execution_minimal"]
```

If `Literal` is not imported, add it to the existing `typing` import list.

- [ ] **Step 2: Add constructor parameter and property**

In `AgentHandle.__init__`, add:

```python
base_system_prompt_mode: BaseSystemPromptMode = "default",
```

Store it:

```python
if base_system_prompt_mode not in ("default", "execution_minimal"):
    raise ValueError(f"Unsupported base_system_prompt_mode: {base_system_prompt_mode!r}")
self.base_system_prompt_mode = base_system_prompt_mode
```

- [ ] **Step 3: Split default prompt helper**

Rename the current `_base_system_prompt()` body to:

```python
def _default_base_system_prompt(self, runtime_context: dict[str, Any]) -> str:
    ...
```

Keep the existing text unchanged.

- [ ] **Step 4: Add execution minimal prompt helper**

Add:

```python
def _execution_minimal_base_system_prompt(self, runtime_context: dict[str, Any]) -> str:
    mode = runtime_context.get("mode") or "live"
    lines = [
        "You are operating as an order execution agent inside LumiBot.",
        "Use the provided runtime context and tool outputs as the ground truth for the current state of the strategy.",
        "Execute only the provided execution_plan.",
        "Do not perform investment research, do not re-rank candidates, do not substitute symbols, and do not change the plan.",
        "Do not add, remove, replace, or reorder execution_plan.orders.",
        "Before submitting any order, inspect current positions, available cash, portfolio value, open orders, and the latest price for the ordered asset.",
        "Execute execution_plan.orders in ascending sequence order.",
        "When switching from one asset to another, submit the sell or reduce order before the replacement buy order when that is the sequence provided.",
        "Use whole-share quantities unless the tool and asset type explicitly support fractional quantities.",
        "Block or pause only for execution-level blockers such as missing required order fields, insufficient cash after required prior sells, broker/tool rejection, unavailable price data, or invalid order parameters.",
        "Report each order sequence as submitted or blocked.",
        "Finish every run with a short summary sentence starting with RESULT: that explains what execution action you took.",
    ]
    if mode == "backtesting":
        lines.extend(
            [
                "",
                "BACKTESTING SAFETY RULES - READ THIS AS A HARD REQUIREMENT:",
                "Treat the current simulated datetime as a hard wall. Do not use data that would not have been available at or before that datetime.",
                "If a tool has any parameter that controls a time range, date filter, or temporal bound, set it so that no data after the current simulated datetime can be returned.",
                "If a tool response seems to include future timestamps, treat that as suspicious and do not rely on those records.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "LIVE TRADING RULES:",
                "Act on the current visible account, broker, order, and market state.",
            ]
        )
    return "\n".join(lines).strip()
```

- [ ] **Step 5: Recreate `_base_system_prompt()` as dispatcher**

Add:

```python
def _base_system_prompt(self, runtime_context: dict[str, Any]) -> str:
    if self.base_system_prompt_mode == "execution_minimal":
        return self._execution_minimal_base_system_prompt(runtime_context)
    return self._default_base_system_prompt(runtime_context)
```

- [ ] **Step 6: Add `AgentManager.create` parameter**

Add parameter:

```python
base_system_prompt_mode: BaseSystemPromptMode = "default",
```

Pass it into `AgentHandle(...)`.

- [ ] **Step 7: Run prompt mode tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py -q
```

Expected: Tests pass.

---

## Task 3: Use Minimal Base Prompt in Growth Execution Strategy

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`
- Test: `tests/backtest/test_ai_trading_team_growth_execution_test.py`

- [ ] **Step 1: Write failing strategy test**

Inspect existing test file:

```powershell
if (Test-Path tests\backtest\test_ai_trading_team_growth_execution_test.py) { Get-Content tests\backtest\test_ai_trading_team_growth_execution_test.py } else { "missing" }
```

Add this test, adapting only import names if needed:

```python
from lumibot.example_strategies.ai_trading_team_growth_execution_test import (
    AITradingTeamGrowthExecutionTestStrategy,
)


def test_growth_execution_strategy_uses_minimal_base_prompt_for_execution_agent():
    assert (
        AITradingTeamGrowthExecutionTestStrategy._execution_agent_base_system_prompt_mode
        == "execution_minimal"
    )
```

If the strategy does not have such class attribute yet, this test should fail.

- [ ] **Step 2: Run failing strategy test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_ai_trading_team_growth_execution_test.py::test_growth_execution_strategy_uses_minimal_base_prompt_for_execution_agent -q
```

Expected: Fail because the class attribute does not exist.

- [ ] **Step 3: Add class-level mode constant**

In `AITradingTeamGrowthExecutionTestStrategy`, add:

```python
_execution_agent_base_system_prompt_mode = "execution_minimal"
```

- [ ] **Step 4: Pass mode into execution agent creation**

In the `self.agents.create(name="execution_agent", ...)` call, add:

```python
base_system_prompt_mode=self._execution_agent_base_system_prompt_mode,
```

- [ ] **Step 5: Run strategy test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_ai_trading_team_growth_execution_test.py::test_growth_execution_strategy_uses_minimal_base_prompt_for_execution_agent -q
```

Expected: Pass.

---

## Task 4: Verify Trace and UI-Relevant Behavior

**Files:**
- No production files unless verification exposes a bug.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py tests\backtest\test_ai_trading_team_growth_execution_test.py tests\test_agent_replay_ui_loader.py -q
```

Expected: All tests pass.

- [ ] **Step 2: Run ruff**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\manager.py lumibot\example_strategies\ai_trading_team_growth_execution_test.py tests\test_agent_manager.py tests\backtest\test_ai_trading_team_growth_execution_test.py
```

Expected: `All checks passed!`

- [ ] **Step 3: Run one-day benchmark**

Run the existing one-day growth execution benchmark command used previously for 2024-09-05. If there is a helper script, use it. Otherwise inspect benchmark scripts with:

```powershell
rg -n "GrowthExecution|growth-execution|2024-09-05|AITradingTeamGrowthExecutionTestStrategy|benchmark" scripts tests lumibot\example_strategies
```

Expected: A new trace root is created under `artifacts/ai_trading_team_example_benchmarks`.

- [ ] **Step 4: Inspect execution trace prompt**

Run a small Python inspection against the newest growth execution trace root:

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; from lumibot.components.agents.replay_ui.loader import discover_trace_roots, build_replay_dataset_from_roots; roots=discover_trace_roots(Path.cwd()); ds=build_replay_dataset_from_roots(roots).to_public_dict(); matches=[(run, sr, agent) for run in ds['runs'] for sr in run['system_runs'] for agent in sr['agents'] if run.get('strategy_name')=='AITradingTeamGrowthExecutionTestStrategy' and agent['name']=='execution_agent']; agent=matches[-1][2]; prompt=agent['input_material']['base_system_prompt']; print(prompt[:500]); print('DEFAULT INVESTOR POLICY' in prompt); print('Execute only the provided execution_plan.' in prompt)"
```

Expected:

```text
False
True
```

- [ ] **Step 5: Do not commit `project_notes`**

Run:

```powershell
git status --short
```

Expected: `project_notes/` may remain untracked, but it must not be staged or committed.

---

## Self-Review

- Spec coverage: The plan covers per-agent prompt mode, default preservation, strategy use, trace visibility, tests, and one-day verification.
- Placeholder scan: No placeholder implementation steps remain.
- Type consistency: `base_system_prompt_mode` and `execution_minimal` are used consistently across manager, strategy, and tests.
