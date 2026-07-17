# Structured Execution Plan Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the growth execution test strategy so `decision_agent` hands `execution_agent` a compact structured execution plan instead of a mixed investment report.

**Architecture:** This is a prompt/interface change in the existing experimental strategy, not a new runtime feature. The decision agent will be prompted to emit `decision` and `execution_plan`; the execution agent will be prompted to execute `execution_plan.orders` in sequence and reject only for execution-level blockers.

**Tech Stack:** Python, Lumibot strategy examples, pytest.

---

## File Structure

- Modify `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`
  - Update `decision_agent` system prompt.
  - Update `decision_agent` task prompt.
  - Update `execution_agent` system prompt.
  - Update `execution_agent` task prompt.
  - Keep execution context limited to `date`, `universe`, and `trading_plan`.
- Modify `tests/test_ai_trading_team_growth_execution_test.py`
  - Replace old `exit_actions`/`entry_actions` prompt test with new `decision`/`execution_plan` prompt test.
  - Add assertions that `execution_agent` prompt treats `execution_plan.orders` as authoritative.
  - Preserve existing workflow/context tests.

## Task 1: Update Tests For Structured Handoff

**Files:**
- Modify: `tests/test_ai_trading_team_growth_execution_test.py`

- [ ] **Step 1: Replace old decision prompt field test**

Change `test_decision_prompt_requests_structured_exit_and_entry_plan` to assert the new handoff shape. Add `import re` and use a helper so the rotate order check is structural enough to accept prose or JSON-like prompts such as `{"sequence": 1, "side": "sell"}`:

```python
def assert_sequence_side_relationship(prompt_text, sequence, side):
    sequence_pattern = rf"sequence[\"']?\s*[:=]?\s*[\"']?{sequence}[\"']?"
    side_pattern = rf"(?:side|action)[\"']?\s*[:=]?\s*[\"']?{side}[\"']?"
    pattern = rf"({sequence_pattern}.{{0,120}}{side_pattern}|{side_pattern}.{{0,120}}{sequence_pattern})"
    assert re.search(pattern, prompt_text, re.DOTALL), (
        f"expected sequence {sequence} to be structurally associated with {side}"
    )


def test_decision_prompt_requests_structured_execution_plan():
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    for name in ("growth_agent", "decision_agent", "execution_agent"):
        agent_manager._agents[name] = RecordingAgent(name)
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.on_trading_iteration()

    decision_prompt = agent_manager["decision_agent"].calls[0]["task_prompt"]
    decision_prompt_lower = decision_prompt.lower()
    for field in (
        "decision.type",
        "decision.from",
        "decision.to",
        "decision.reason_brief",
        "execution_plan.mode",
        "execution_plan.orders",
        "execution_plan.execution_constraints",
        "sequence",
        "quantity_basis",
        "max_affordable_after_prior_sells",
        "cash_buffer_pct",
    ):
        assert field in decision_prompt_lower

    assert_sequence_side_relationship(decision_prompt_lower, 1, "sell")
    assert_sequence_side_relationship(decision_prompt_lower, 2, "buy")
    for rotate_plan_fragment in (
        "decision.from",
        "decision.to",
        "max_affordable_after_prior_sells",
    ):
        assert rotate_plan_fragment in decision_prompt_lower

    for old_field in (
        "current_position_assessment",
        "exit_actions",
        "entry_actions",
        "do_not_trade_if",
    ):
        assert old_field not in decision_prompt
```

- [ ] **Step 2: Add execution prompt authority test**

Add this test after the decision prompt test:

```python
def test_execution_prompt_treats_execution_plan_as_authoritative():
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    for name in ("growth_agent", "decision_agent", "execution_agent"):
        agent_manager._agents[name] = RecordingAgent(name)
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()
    strategy.on_trading_iteration()

    execution_agent_created = [
        created for created in agent_manager.created if created["name"] == "execution_agent"
    ]
    assert len(execution_agent_created) == 1

    prompt_text = json.dumps(execution_agent_created[0])
    prompt_text += json.dumps(agent_manager["execution_agent"].calls)
    prompt_text = prompt_text.lower().replace('\\"', '"')

    for required_phrase in (
        "execution_plan.orders",
        "decision.reason_brief",
        "human context",
        "do not re-rank",
        "do not substitute",
        "execution-level blockers",
        "sequence number",
        "max_affordable_after_prior_sells",
        "cash_buffer_pct",
    ):
        assert required_phrase in prompt_text
    for required_pattern in (
        r"(execution_plan(?:\.orders)?.{0,100}(authoritative|source of truth|control|drive)|"
        r"(authoritative|source of truth|control|drive).{0,100}execution_plan(?:\.orders)?)",
        r"sequence.{0,80}(order|ordering|ascending|submitted order|in sequence)",
        r"(do not|must not|never).{0,80}(substitute|replace|swap).{0,80}symbol",
        r"(only|solely).{0,80}(execution-level|execution level).{0,80}(blocker|blockers)",
        r"each.{0,80}sequence.{0,80}(submitted|blocked)",
    ):
        assert re.search(required_pattern, prompt_text, re.DOTALL)
```

- [ ] **Step 3: Keep neutral prompt test focused on neutral framing**

In `test_prompts_frame_strategy_as_neutral_relative_strength_account_management`, keep the neutral strategy framing assertions focused on strategy intent, and remove old execution phrases:

```python
"do not use upstream research to override the trading_plan",
"otherwise execute the trading_plan",
'plan_type="rotate"',
'side="sell"',
'side="buy"',
"sell or reduce the current holding first",
```

Do not add structured handoff details such as `"execution_plan is the authoritative section"` or `'decision.type'` to this neutral framing test. The detailed execution handoff contract belongs in `test_execution_prompt_treats_execution_plan_as_authoritative`; do not duplicate it in the neutral framing test.

- [ ] **Step 4: Run tests to confirm failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py -q
```

Expected: tests fail because prompts still request old fields and do not include new structured execution language.

- [ ] **Step 5: Commit failing tests only**

Do not commit failing tests. Keep them staged/unstaged for Task 2.

## Task 2: Update Strategy Prompts To Emit Structured Execution Plans

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`
- Test: `tests/test_ai_trading_team_growth_execution_test.py`

- [ ] **Step 1: Update decision agent system prompt**

Replace the final part of the decision agent system prompt so it says:

```python
"Output a compact structured handoff for the execution agent. The output must contain exactly two top-level sections: "
"decision and execution_plan. decision must include decision.type, decision.from, decision.to, and decision.reason_brief. "
"execution_plan must include execution_plan.mode, execution_plan.orders, and execution_plan.execution_constraints. "
"execution_plan is the authoritative section for the execution agent; keep decision.reason_brief short and do not bury "
"execution instructions in narrative text. For rotate plans, include a sell order sequence for the current holding and "
"a buy order sequence for the replacement. For buy plans, include one buy order sequence. Use quantity_basis=\"current_position\" "
"for full exits, max_affordable_cash for cash buys, and max_affordable_after_prior_sells for replacement buys after a sell. "
"Use cash_buffer_pct=2 for buy orders unless a clear execution blocker requires a larger buffer. You cannot place orders, "
"but you must produce an actionable structured execution plan."
```

Keep the existing relative-strength decision rules before this replacement.

- [ ] **Step 2: Update decision agent task prompt**

Replace the decision task prompt with:

```python
(
    "Use growth_report and current account state to produce JSON-like text with exactly two top-level sections: "
    "decision and execution_plan. decision must include decision.type, decision.from, decision.to, and "
    "decision.reason_brief. execution_plan must include execution_plan.mode, execution_plan.orders, and "
    "execution_plan.execution_constraints. Choose exactly one decision.type: hold, buy, rotate, reduce, close. "
    "If another ETF is more attractive than the current holding based on current evidence, output decision.type=\"rotate\" "
    "unless a clear blocking reason exists. If the account holds only cash or a cash-like position and growth_report "
    "identifies a strongest ETF candidate, output decision.type=\"buy\" unless a clear blocking reason exists. "
    "For rotate plans, execution_plan.orders must contain sequence 1 sell for decision.from and sequence 2 buy for "
    "decision.to using quantity=\"max_affordable_after_prior_sells\" and cash_buffer_pct=2. For buy plans, include one "
    "buy order with quantity=\"max_affordable_cash\" and cash_buffer_pct=2. For hold plans, execution_plan.orders must "
    "be an empty list. execution_plan is the authoritative section for the execution agent."
)
```

- [ ] **Step 3: Update execution agent system prompt**

Replace the current execution agent system prompt with:

```python
(
    "Execute the structured trading_plan using native trading tools, especially orders_submit_order. "
    "Do not redo investment analysis. Do not re-rank the universe. Do not substitute another symbol. "
    "decision.reason_brief is for human context only; execution_plan is the authoritative section. "
    "Execute execution_plan.orders exactly in sequence. Inspect positions, portfolio, open orders, and latest prices "
    "before each order. For sell orders with quantity_basis=\"current_position\", use the current held quantity of that symbol. "
    "For buy orders with quantity=\"max_affordable_cash\" or quantity=\"max_affordable_after_prior_sells\", calculate a whole-share "
    "quantity from available cash after applying cash_buffer_pct. Only reject or pause for execution-level blockers: missing "
    "position, insufficient cash after buffer, missing latest price, conflicting open orders, malformed plan, or order tool rejection. "
    "Report each sequence as submitted or blocked with the exact blocker. Do not add orders that are not in execution_plan.orders."
)
```

- [ ] **Step 4: Update execution agent task prompt**

Replace the execution task prompt with:

```python
(
    "Use trading_plan to execute execution_plan.orders exactly in sequence. For each sequence, inspect account, open orders, "
    "positions, and latest price, then submit only the requested order with orders_submit_order when no execution-level blocker "
    "exists. decision.reason_brief is for human context only. Do not re-rank the universe, do not substitute another symbol, "
    "and do not use upstream research to override execution_plan. If decision.type=\"hold\", submit no orders. If a sequence "
    "is blocked, report the sequence number, symbol, action, and exact blocker."
)
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py -q
```

Expected: all tests in this file pass.

- [ ] **Step 6: Commit prompt implementation and tests**

Run:

```powershell
git add lumibot/example_strategies/ai_trading_team_growth_execution_test.py tests/test_ai_trading_team_growth_execution_test.py
git commit -m "test: structure decision execution handoff"
```

## Task 3: Verify Integration And Review Branch State

**Files:**
- No source changes expected.
- Verify: `docs/superpowers/specs/2026-07-17-structured-execution-plan-design.md`
- Verify: `docs/superpowers/plans/2026-07-17-structured-execution-plan-handoff.md`
- Verify: `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`
- Verify: `tests/test_ai_trading_team_growth_execution_test.py`

- [ ] **Step 1: Run focused strategy tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run relevant runtime prompt tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_agent_runtime_injects_base_prompt_runtime_context_and_default_summary_log tests\backtest\test_agent_runtime_backtest.py::test_builtin_market_history_and_duckdb_descriptions_include_schema_hints -q
```

Expected: both tests pass.

- [ ] **Step 3: Check diff hygiene**

Run:

```powershell
git diff --check
git status --short --branch
```

Expected: `git diff --check` prints no errors. Status should show this feature branch with only `project_notes/` untracked.

- [ ] **Step 4: Commit plan document**

Run:

```powershell
git add docs/superpowers/plans/2026-07-17-structured-execution-plan-handoff.md
git commit -m "docs: plan structured execution handoff"
```

If the plan was already committed before implementation, skip this step.
