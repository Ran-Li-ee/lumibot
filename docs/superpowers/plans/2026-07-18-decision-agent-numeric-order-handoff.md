# Decision Agent Numeric Order Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `AITradingTeamGrowthExecutionTestStrategy` require explicit numeric share quantities in execution plans so `execution_agent` receives mechanical order instructions.

**Architecture:** Keep the change scoped to the growth execution test strategy. Tighten the parser so non-hold orders must use `quantity_mode: "shares"` with a positive `quantity`, update decision prompts to require account tool calls and explicit sizing before JSON output, then verify with unit tests and a one-day backtest trace inspection.

**Tech Stack:** Python, pytest, existing Lumibot agent framework, existing benchmark runner and replay UI loader.

---

## Files

- Modify: `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`
  - Tighten order validation.
  - Preserve optional `quantity_source`.
  - Update decision-agent prompts.
  - Simplify execution-agent sizing language.
- Modify: `tests/test_ai_trading_team_growth_execution_test.py`
  - Update parser expectations.
  - Add semantic quantity rejection tests.
  - Add prompt tests for account tool calls and numeric orders.
- Create: no new runtime modules.

---

### Task 1: Add Tests For Numeric-Only Executable Orders

**Files:**
- Modify: `tests/test_ai_trading_team_growth_execution_test.py`

- [ ] **Step 1: Update parser success fixture to use shares**

Change the JSON in `test_parse_execution_plan_extracts_clean_plan_from_result_text` so the order uses:

```json
"quantity": 438,
"quantity_mode": "shares",
"quantity_source": "account_portfolio_and_market_last_price"
```

Update the expected normalized order to include:

```python
"quantity": 438.0,
"quantity_mode": "shares",
"quantity_source": "account_portfolio_and_market_last_price",
```

- [ ] **Step 2: Update rotate success fixture to use shares**

Change `test_parse_execution_plan_sorts_orders_and_applies_sell_defaults` so the sell and buy orders use:

```json
"quantity": 217,
"quantity_mode": "shares"
```

and

```json
"quantity": 438,
"quantity_mode": "shares"
```

Assert both parsed quantities are numeric:

```python
assert plan["orders"][0]["quantity"] == 217.0
assert plan["orders"][1]["quantity"] == 438.0
```

- [ ] **Step 3: Add semantic quantity rejection test**

Add:

```python
@pytest.mark.parametrize(
    "quantity_mode",
    [
        "current_position",
        "full_position",
        "max_affordable_cash",
        "max_affordable_after_prior_sells",
    ],
)
def test_parse_execution_plan_rejects_semantic_quantity_modes_for_executable_orders(quantity_mode):
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "execution_plan": {
                "schema_version": 1,
                "intent": "rotate",
                "orders": [
                    {
                        "sequence": 1,
                        "symbol": "QQQ",
                        "side": "sell",
                        "quantity": None,
                        "quantity_mode": quantity_mode,
                    }
                ],
                "constraints": {},
            }
        }
    )

    with pytest.raises(ValueError, match="executable orders must use shares quantity_mode"):
        strategy_module.parse_execution_plan_from_decision_summary(raw_summary)
```

- [ ] **Step 4: Update execution context fixture to use numeric shares**

In `test_on_trading_iteration_hands_off_context_in_order`, update the custom decision summary order to:

```json
"quantity": 438,
"quantity_mode": "shares"
```

Assert:

```python
assert execution_context["execution_plan"]["orders"][0]["quantity_mode"] == "shares"
assert execution_context["execution_plan"]["orders"][0]["quantity"] == 438.0
```

- [ ] **Step 5: Add prompt expectations**

Extend `test_decision_prompt_requests_structured_execution_plan` with required phrases:

```python
for prompt_phrase in (
    "before producing json for a non-hold decision, call account_positions and account_portfolio",
    "call market_last_price when sizing buy orders",
    "final executable orders must use quantity_mode: shares",
    "final executable orders must include a positive numeric quantity",
    "do not use full_position, current_position, max_affordable_cash, or max_affordable_after_prior_sells",
    "calculate the share quantity from account tool output",
):
    assert prompt_phrase in decision_prompt_lower
```

Add forbidden phrase assertions:

```python
for misleading_phrase in (
    "using max_affordable_after_prior_sells for the buy quantity_mode",
    "each order must include sequence, symbol, side, and quantity_mode",
):
    assert misleading_phrase not in decision_prompt_lower
```

- [ ] **Step 6: Run targeted tests and confirm red**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py -q
```

Expected: fail until parser and prompts are updated.

---

### Task 2: Tighten Parser And Prompts

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`

- [ ] **Step 1: Keep semantic modes as known-but-not-executable**

Replace `ALLOWED_QUANTITY_MODES` with:

```python
ALLOWED_QUANTITY_MODES = {"shares"}
REJECTED_SEMANTIC_QUANTITY_MODES = {
    "current_position",
    "full_position",
    "max_affordable_cash",
    "max_affordable_after_prior_sells",
}
```

- [ ] **Step 2: Reject non-share executable orders clearly**

In `_normalize_order`, replace unsupported quantity mode handling with:

```python
quantity_mode = str(order["quantity_mode"]).strip().lower()
if quantity_mode in REJECTED_SEMANTIC_QUANTITY_MODES:
    raise ValueError(
        "executable orders must use shares quantity_mode with explicit numeric quantity; "
        f"got semantic quantity_mode: {quantity_mode}"
    )
if quantity_mode not in ALLOWED_QUANTITY_MODES:
    raise ValueError(f"unsupported order quantity_mode: {quantity_mode}")
```

Keep the existing positive finite quantity check, but it now applies to every accepted order because the only accepted mode is `shares`.

- [ ] **Step 3: Preserve quantity source metadata**

Add this field to the returned normalized order:

```python
"quantity_source": order.get("quantity_source"),
```

- [ ] **Step 4: Update decision-agent system prompt**

Replace language that allows optional semantic sizing with language that says:

```text
Before producing JSON for a non-hold decision, call account_positions and account_portfolio.
Call market_last_price when sizing buy orders.
Final executable orders must use quantity_mode: shares and must include a positive numeric quantity.
Do not use full_position, current_position, max_affordable_cash, or max_affordable_after_prior_sells in final executable orders.
For selling all or part of a position, calculate the share quantity from account tool output and write the number.
For buy orders, calculate the share quantity from cash and latest price data and write the number.
```

- [ ] **Step 5: Update decision task prompt**

Mirror the same requirements in the per-run decision task prompt. Remove the sentence that asks the buy order to use `max_affordable_after_prior_sells`.

- [ ] **Step 6: Update execution prompt**

Remove instructions telling execution_agent to honor `max_affordable_after_prior_sells`. Replace with:

```text
Submit the explicit numeric share quantities in execution_plan.orders. Do not compute semantic sizing.
```

- [ ] **Step 7: Run targeted tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py -q
```

Expected: pass.

- [ ] **Step 8: Run focused lint**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check --select F,I lumibot\example_strategies\ai_trading_team_growth_execution_test.py tests\test_ai_trading_team_growth_execution_test.py
```

Expected: pass.

---

### Task 3: One-Day Backtest Trace Verification

**Files:**
- No source edits expected.

- [ ] **Step 1: Run one-day backtest with OpenAI key**

Use the first bare `sk-` or `sk-proj-` key in `project_notes/API.txt`, do not print it, and run:

```powershell
$apiLines = Get-Content project_notes\API.txt
$apiKey = $null
foreach ($line in $apiLines) {
  $trimmed = $line.Trim().Trim('"').Trim("'")
  if ($trimmed.StartsWith('sk-proj-') -or $trimmed.StartsWith('sk-')) { $apiKey = $trimmed; break }
}
if (-not $apiKey) { throw 'No OpenAI sk-/sk-proj- key found in project_notes\API.txt' }
$env:OPENAI_API_KEY = $apiKey
$env:AI_TRADING_TEAM_MODEL = 'openai/gpt-5.4-mini'
$env:GEMINI_API_KEY = 'placeholder-for-openai-runner-check'
Remove-Item Env:\GOOGLE_API_KEY -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy growth-execution-test --start 2024-09-05 --end 2024-09-06 --max-workers 1 --env-file project_notes\API.txt --agent-run-timeout-seconds 900
```

Expected: benchmark exits successfully. A blocked run is acceptable only if the trace proves the decision agent still produced semantic quantity modes, in which case report that the prompt was insufficient rather than claiming success.

- [ ] **Step 2: Inspect latest execution-agent input**

Run:

```powershell
$script = @'
from pathlib import Path
from lumibot.components.agents.replay_ui.loader import build_replay_dataset

roots = sorted(
    Path("artifacts/ai_trading_team_example_benchmarks").glob("*/growth-execution-test/cache/agent_runtime"),
    key=lambda p: p.stat().st_mtime,
    reverse=True,
)
root = roots[0]
ds = build_replay_dataset(root).to_public_dict()
system_runs = [sr for run in ds["runs"] for sr in run["system_runs"]]
execution_agents = [
    agent
    for sr in system_runs
    for agent in sr["agents"]
    if agent["name"] == "execution_agent"
]
print(root)
print("execution_agent_count", len(execution_agents))
if execution_agents:
    context = execution_agents[0]["input"]["context"]
    print(context)
    plan = context["execution_plan"]
    print([(order["symbol"], order["side"], order["quantity_mode"], order["quantity"]) for order in plan["orders"]])
'@
$script | .\.venv\Scripts\python.exe -
```

Expected:

- `execution_agent_count` is at least 1.
- The context has only `date` and `execution_plan`.
- Every order tuple has `quantity_mode == "shares"` and a positive numeric quantity.

- [ ] **Step 3: Run final checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py tests\test_agent_manager.py tests\test_agent_replay_ui_loader.py tests\test_agent_replay_ui_models.py tests\test_agent_replay_ui_static.py -q
.\.venv\Scripts\python.exe -m ruff check --select F,I lumibot\example_strategies\ai_trading_team_growth_execution_test.py tests\test_ai_trading_team_growth_execution_test.py
git diff --check
git status --short --branch
```

Expected:

- Tests pass.
- Focused ruff passes.
- Diff check passes.
- `project_notes/` remains untracked and is not committed.
