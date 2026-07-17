# Strict Execution Plan Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure the growth execution test strategy passes a validated execution plan object to `execution_agent`, not the raw decision-agent text summary.

**Architecture:** Keep this scoped to `AITradingTeamGrowthExecutionTestStrategy`. Add small parser/validator helpers in the strategy module, update tests first, then update the strategy handoff so invalid decision output blocks execution and valid output is normalized before the trading-enabled agent sees it.

**Tech Stack:** Python, built-in `json`, pytest, existing Lumibot agent manager/test doubles.

---

## Files

- Modify: `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`
  - Add strict parsing and validation helpers.
  - Update decision prompt to request valid JSON only.
  - Update execution-agent context to receive `execution_plan`.
  - Skip execution agent when decision output is invalid.
- Modify: `tests/test_ai_trading_team_growth_execution_test.py`
  - Add parser tests.
  - Add execution context tests for clean strict object.
  - Update previous raw `trading_plan` expectation.
- Create: no production files beyond the scoped strategy module.

---

### Task 1: Add Failing Tests For Strict Handoff

**Files:**
- Modify: `tests/test_ai_trading_team_growth_execution_test.py`

- [ ] **Step 1: Update the recording fake to allow custom summaries**

Replace `RecordingAgent` and `RecordingAgentManager` with versions that can return custom summaries:

```python
class RecordingAgentManager:
    def __init__(self):
        self.created = []
        self._agents = {}
        self.summaries = {}

    def create(self, **kwargs):
        self.created.append(kwargs)
        agent = RecordingAgent(kwargs["name"], self.summaries)
        self._agents[kwargs["name"]] = agent
        return agent

    def __getitem__(self, name):
        return self._agents[name]


class RecordingAgent:
    def __init__(self, name, summaries=None):
        self.name = name
        self.summaries = summaries or {}
        self.calls = []

    def run(self, *, task_prompt, context):
        self.calls.append({"task_prompt": task_prompt, "context": context})
        return SimpleNamespace(summary=self.summaries.get(self.name, f"{self.name} summary"))
```

- [ ] **Step 2: Import the parser helpers in tests through the strategy module**

No new import is needed if tests use `strategy_module` returned by `load_strategy_module()`.

- [ ] **Step 3: Add parser test for mixed JSON plus RESULT**

Add:

```python
def test_parse_execution_plan_extracts_clean_plan_from_result_text():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = (
        '{"decision":{"type":"buy","from":"USD","to":"GLD","reason_brief":"analysis stays out of execution"},'
        '"execution_plan":{"schema_version":1,"intent":"enter_position","orders":[{"sequence":1,'
        '"action":"submit_order","symbol":"GLD","side":"buy","quantity":null,'
        '"quantity_mode":"max_affordable_cash","order_type":"market"}],"constraints":{}}}'
        '\nRESULT: human explanation should not reach execution.'
    )

    plan = strategy_module.parse_execution_plan_from_decision_summary(raw_summary)

    assert plan == {
        "schema_version": 1,
        "intent": "enter_position",
        "orders": [
            {
                "sequence": 1,
                "action": "submit_order",
                "symbol": "GLD",
                "asset_type": "stock",
                "side": "buy",
                "quantity": None,
                "quantity_mode": "max_affordable_cash",
                "cash_buffer_pct": 2,
                "order_type": "market",
                "limit_price": None,
                "stop_price": None,
                "stop_limit_price": None,
                "trail_price": None,
                "trail_percent": None,
                "time_in_force": "day",
            }
        ],
        "constraints": {
            "allow_negative_cash": False,
            "if_any_order_blocked": "stop_remaining_orders",
        },
    }
```

- [ ] **Step 4: Add parser test for sorting sequences and sell defaults**

Add:

```python
def test_parse_execution_plan_sorts_orders_and_applies_sell_defaults():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = """
    {
      "execution_plan": {
        "schema_version": 1,
        "intent": "rotate",
        "orders": [
          {"sequence": 2, "action": "submit_order", "symbol": "GLD", "side": "buy", "quantity": null, "quantity_mode": "max_affordable_after_prior_sells", "order_type": "market"},
          {"sequence": 1, "action": "submit_order", "symbol": "VNQ", "side": "sell", "quantity": null, "quantity_mode": "current_position", "order_type": "market"}
        ],
        "constraints": {"allow_negative_cash": false}
      }
    }
    """

    plan = strategy_module.parse_execution_plan_from_decision_summary(raw_summary)

    assert [order["sequence"] for order in plan["orders"]] == [1, 2]
    assert plan["orders"][0]["side"] == "sell"
    assert plan["orders"][0]["cash_buffer_pct"] == 0
    assert plan["orders"][1]["cash_buffer_pct"] == 2
```

- [ ] **Step 5: Add parser test for invalid output**

Add:

```python
def test_parse_execution_plan_rejects_missing_required_order_fields():
    strategy_module, _strategy_class = load_strategy_module()

    with pytest.raises(ValueError, match="missing required order field: symbol"):
        strategy_module.parse_execution_plan_from_decision_summary(
            '{"execution_plan":{"schema_version":1,"intent":"enter_position","orders":[{"sequence":1,"side":"buy","quantity_mode":"max_affordable_cash"}],"constraints":{}}}'
        )
```

Add `import pytest` near the top of the test file if it is not present.

- [ ] **Step 6: Update execution context test to expect strict execution plan**

Replace the execution context assertions in `test_on_trading_iteration_hands_off_context_in_order` with a valid custom decision summary:

```python
    agent_manager.summaries["decision_agent"] = (
        '{"decision":{"type":"buy","from":"USD","to":"GLD","reason_brief":"keep this out of execution"},'
        '"execution_plan":{"schema_version":1,"intent":"enter_position","orders":[{"sequence":1,'
        '"action":"submit_order","symbol":"GLD","side":"buy","quantity":null,'
        '"quantity_mode":"max_affordable_cash","order_type":"market"}],"constraints":{}}}'
        '\nRESULT: remove me'
    )
```

Then assert:

```python
    assert execution_context["date"] == "2026-04-07"
    assert set(execution_context) == {"date", "execution_plan"}
    assert execution_context["execution_plan"]["orders"][0]["symbol"] == "GLD"
    serialized_execution_context = json.dumps(execution_context)
    assert "trading_plan" not in serialized_execution_context
    assert "growth_report" not in serialized_execution_context
    assert "RESULT:" not in serialized_execution_context
    assert "reason_brief" not in serialized_execution_context
```

- [ ] **Step 7: Add invalid decision blocks execution test**

Add:

```python
def test_invalid_decision_summary_blocks_execution_agent():
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    for name in ("growth_agent", "decision_agent", "execution_agent"):
        agent_manager._agents[name] = RecordingAgent(name, agent_manager.summaries)
    agent_manager.summaries["decision_agent"] = "RESULT: no json here"
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.on_trading_iteration()

    assert agent_manager["growth_agent"].calls
    assert agent_manager["decision_agent"].calls
    assert agent_manager["execution_agent"].calls == []
    assert getattr(strategy, "_last_execution_plan_error") == "No JSON object found in decision summary."
```

- [ ] **Step 8: Add prompt test for pure JSON instruction**

Extend `test_decision_prompt_requests_structured_execution_plan` to assert:

```python
    for strict_phrase in (
        "return only one valid json object",
        "do not include markdown",
        "do not include result text",
        "do not include prose after the json",
    ):
        assert strict_phrase in decision_prompt_lower
```

- [ ] **Step 9: Run tests and confirm red**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py -q
```

Expected: fail because parser helpers do not exist and execution context still passes raw `trading_plan`.

- [ ] **Step 10: Commit failing tests**

Do not commit red tests separately unless the controller explicitly asks. Report red result to controller for implementation step.

---

### Task 2: Implement Strict Parser And Context Handoff

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`

- [ ] **Step 1: Add imports**

At the top:

```python
import json
```

- [ ] **Step 2: Add constants**

After imports:

```python
ALLOWED_INTENTS = {"hold", "enter_position", "rotate", "reduce_position", "close_position"}
ALLOWED_ACTIONS = {"submit_order"}
ALLOWED_SIDES = {"buy", "sell"}
ALLOWED_QUANTITY_MODES = {
    "shares",
    "current_position",
    "max_affordable_cash",
    "max_affordable_after_prior_sells",
}
ALLOWED_ORDER_TYPES = {"market", "limit", "stop", "stop_limit", "trailing_stop", "smart_limit"}
```

- [ ] **Step 3: Add JSON extraction helper**

Add before the strategy class:

```python
def _extract_first_json_object(text):
    if not isinstance(text, str):
        raise ValueError("Decision summary must be text.")
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found in decision summary.")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise ValueError("Unclosed JSON object in decision summary.")
```

- [ ] **Step 4: Add validation helpers**

Add:

```python
def _require_dict(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return value


def _normalize_order(order):
    order = dict(_require_dict(order, "execution_plan order"))
    for field in ("sequence", "symbol", "side", "quantity_mode"):
        if field not in order:
            raise ValueError(f"missing required order field: {field}")
    try:
        sequence = int(order["sequence"])
    except Exception as exc:
        raise ValueError("order sequence must be a positive integer.") from exc
    if sequence <= 0:
        raise ValueError("order sequence must be a positive integer.")
    side = str(order["side"]).strip().lower()
    if side not in ALLOWED_SIDES:
        raise ValueError(f"unsupported order side: {side}")
    action = str(order.get("action") or "submit_order").strip().lower()
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"unsupported order action: {action}")
    quantity_mode = str(order["quantity_mode"]).strip()
    if quantity_mode not in ALLOWED_QUANTITY_MODES:
        raise ValueError(f"unsupported quantity_mode: {quantity_mode}")
    quantity = order.get("quantity")
    if quantity_mode == "shares":
        if quantity is None:
            raise ValueError("quantity is required when quantity_mode is shares.")
        if float(quantity) <= 0:
            raise ValueError("quantity must be positive when quantity_mode is shares.")
    else:
        quantity = None
    order_type = str(order.get("order_type") or "market").strip().lower()
    if order_type not in ALLOWED_ORDER_TYPES:
        raise ValueError(f"unsupported order_type: {order_type}")
    normalized = {
        "sequence": sequence,
        "action": action,
        "symbol": str(order["symbol"]).strip().upper(),
        "asset_type": str(order.get("asset_type") or "stock").strip().lower(),
        "side": side,
        "quantity": quantity,
        "quantity_mode": quantity_mode,
        "cash_buffer_pct": order.get("cash_buffer_pct", 2 if side == "buy" else 0),
        "order_type": order_type,
        "limit_price": order.get("limit_price"),
        "stop_price": order.get("stop_price"),
        "stop_limit_price": order.get("stop_limit_price"),
        "trail_price": order.get("trail_price"),
        "trail_percent": order.get("trail_percent"),
        "time_in_force": str(order.get("time_in_force") or "day").strip().lower(),
    }
    if not normalized["symbol"]:
        raise ValueError("order symbol must be non-empty.")
    return normalized
```

- [ ] **Step 5: Add public parser**

Add:

```python
def parse_execution_plan_from_decision_summary(summary):
    raw_json = _extract_first_json_object(summary)
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Decision summary JSON is invalid: {exc.msg}.") from exc
    payload = _require_dict(payload, "decision summary JSON")
    plan = payload.get("execution_plan", payload)
    plan = dict(_require_dict(plan, "execution_plan"))
    schema_version = int(plan.get("schema_version", 1))
    if schema_version != 1:
        raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")
    intent = str(plan.get("intent") or plan.get("mode") or "").strip()
    if intent == "backtest":
        intent = "enter_position"
    if not intent:
        raise ValueError("execution_plan intent is required.")
    if intent not in ALLOWED_INTENTS:
        raise ValueError(f"unsupported execution_plan intent: {intent}")
    orders = plan.get("orders") or []
    if not isinstance(orders, list):
        raise ValueError("execution_plan orders must be a list.")
    if intent != "hold" and not orders:
        raise ValueError("execution_plan orders are required for non-hold intent.")
    normalized_orders = sorted((_normalize_order(order) for order in orders), key=lambda item: item["sequence"])
    constraints = plan.get("constraints") or plan.get("execution_constraints") or {}
    constraints = dict(_require_dict(constraints, "execution_plan constraints"))
    constraints.setdefault("allow_negative_cash", False)
    constraints.setdefault("if_any_order_blocked", "stop_remaining_orders")
    return {
        "schema_version": schema_version,
        "intent": intent,
        "orders": normalized_orders,
        "constraints": constraints,
    }
```

- [ ] **Step 6: Update decision prompt**

In both decision-agent system prompt and task prompt, add the pure JSON instruction:

```text
Return only one valid JSON object. Do not include markdown, do not include RESULT text, comments, or prose after the JSON.
```

Also ask for strict fields:

```text
execution_plan must include schema_version, intent, orders, and constraints. Each order must include sequence, action, symbol, side, quantity, quantity_mode, order_type, and time_in_force.
```

- [ ] **Step 7: Update execution prompt**

Replace `trading_plan` references with `execution_plan` references. The execution agent prompt should say:

```text
Execute only the provided execution_plan object. Do not read or infer investment reasons. Do not add, remove, replace, or reorder orders.
```

- [ ] **Step 8: Update `on_trading_iteration` handoff**

Replace:

```python
self.agents["execution_agent"].run(
    ...,
    context={**context, "trading_plan": decision.summary},
)
```

with:

```python
try:
    execution_plan = parse_execution_plan_from_decision_summary(decision.summary)
except ValueError as exc:
    self._last_execution_plan_error = str(exc)
    return

self._last_execution_plan_error = None
self.agents["execution_agent"].run(
    task_prompt=(...),
    context={
        "date": context["date"],
        "execution_plan": execution_plan,
    },
)
```

- [ ] **Step 9: Run targeted tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py -q
```

Expected: pass.

- [ ] **Step 10: Run ruff**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check lumibot\example_strategies\ai_trading_team_growth_execution_test.py tests\test_ai_trading_team_growth_execution_test.py
```

Expected: pass.

- [ ] **Step 11: Commit**

```powershell
git add lumibot/example_strategies/ai_trading_team_growth_execution_test.py tests/test_ai_trading_team_growth_execution_test.py
git commit -m "feat: enforce strict execution plan handoff"
```

---

### Task 3: Backtest Validation

**Files:**
- No source edits expected.

- [ ] **Step 1: Run one-day backtest**

Use existing local API setup from `project_notes/API.txt` without committing it. Run the one-day growth execution benchmark:

```powershell
$api = Get-Content -Raw project_notes\API.txt
$env:OPENAI_API_KEY = [regex]::Match($api, 'sk-proj-[A-Za-z0-9_-]+').Value
$env:FRED_API_KEY = [regex]::Match($api, '(?ms)FRED\s*\r?\n([A-Za-z0-9]+)').Groups[1].Value.Trim()
$env:ALPACA_API_KEY = [regex]::Match($api, '(?m)^key:\s*(\S+)').Groups[1].Value.Trim()
$env:ALPACA_API_SECRET = [regex]::Match($api, '(?m)^Secret:\s*(\S+)').Groups[1].Value.Trim()
$env:ALPACA_NEWS_API_KEY = $env:ALPACA_API_KEY
$env:ALPACA_NEWS_API_SECRET = $env:ALPACA_API_SECRET
$env:GOOGLE_API_KEY = 'dummy-google-key-for-openai-model-run'
$env:AI_TRADING_TEAM_MODEL = 'openai/gpt-5.4-mini'
.\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy growth-execution-test --start 2024-09-05 --end 2024-09-06 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800
```

Expected: status `passed`.

- [ ] **Step 2: Inspect execution trace input**

Run this command to inspect the latest growth execution benchmark artifact automatically:

```powershell
.\.venv\Scripts\python.exe -c "import pandas as pd; from pathlib import Path; roots=sorted(Path('artifacts/ai_trading_team_example_benchmarks').glob('*/growth-execution-test'), key=lambda p: p.stat().st_mtime, reverse=True); root=roots[0]; df=pd.read_parquet(root/'stats_agent_detail.parquet'); row=df[(df.agent_name=='execution_agent') & (df.is_call_summary==True)].iloc[0]; print(root); print(row['context_text'])"
```

Expected:

- Contains `execution_plan=`.
- Does not contain `trading_plan=`.
- Does not contain `RESULT:`.
- Does not contain `growth_report`.
- Does not contain `reason_brief`.

- [ ] **Step 3: Verify UI loader sees strict run**

Run this command to verify the replay loader can read the same latest artifact:

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; from lumibot.components.agents.replay_ui.loader import build_replay_dataset; roots=sorted(Path('artifacts/ai_trading_team_example_benchmarks').glob('*/growth-execution-test'), key=lambda p: p.stat().st_mtime, reverse=True); root=roots[0]; ds=build_replay_dataset(root/'cache/agent_runtime'); print(root); print(len(ds.runs)); print([a.name for a in ds.runs[0].system_runs[0].agents])"
```

Expected: one run with `growth_agent`, `decision_agent`, and `execution_agent`.

- [ ] **Step 4: Run final checks**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py -q
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_agent_runtime_injects_base_prompt_runtime_context_and_default_summary_log tests\backtest\test_agent_runtime_backtest.py::test_builtin_market_history_and_duckdb_descriptions_include_schema_hints -q
.\.venv\Scripts\python.exe -m ruff check lumibot\example_strategies\ai_trading_team_growth_execution_test.py tests\test_ai_trading_team_growth_execution_test.py
git diff --check
git status --short --branch
```

Expected:

- Tests pass.
- Ruff passes.
- Diff check passes.
- Only `project_notes/` may remain untracked.
