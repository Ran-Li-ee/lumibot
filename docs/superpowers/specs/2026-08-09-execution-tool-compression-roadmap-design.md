# Execution Tool Compression Roadmap Design

Date: 2026-08-09

## 1. Purpose

Define a four-stage roadmap for gradually moving repetitive order-execution
steps out of the LLM-driven `execution_agent` loop and into deterministic
Python tools.

The roadmap is directional. It is not a single implementation scope.

Each stage will later get its own focused design spec, implementation plan,
implementation branch work, tests, backtest validation, and UI review.

The long-term goal is:

```text
LLM reads the execution plan and triggers execution.
Deterministic tools perform repeated mechanical execution steps.
Agent Replay UI still exposes the internal execution details.
```

The roadmap stages are:

```text
A. orders_preflight_check
B. orders_submit_and_confirm_order
C. orders_execute_order
D. execution_plan_execute
```

Stage D is the long-term target. Stages A, B, and C are deliberate stepping
stones that let us reduce LLM round count while keeping the system inspectable
and testable.

## 2. Current Baseline

The mock Growth / Inflation quadrant strategy currently has this execution
flow:

```text
portfolio_decision_agent
  -> receives macro and basket summaries
  -> calls target_portfolio_to_execution_plan
  -> returns target_portfolio and strict execution_plan

execution_agent
  -> receives execution_plan
  -> inspects account and order state
  -> submits each order
  -> confirms each submitted order
  -> writes final execution summary
```

The current execution-agent pattern for one order is usually:

```text
account_portfolio
account_positions
orders_open_orders
market_last_price(symbol)
orders_submit_order
orders_confirm_order
```

This pattern is safe and transparent, but it creates many model turns and
large tool-result context.

Observed two-day mock quadrant benchmark:

```text
Day 1 initial deployment:
  10 execution-agent tool-call rounds plus final summary.

Day 2 rebalance:
  12 execution-agent tool-call rounds plus final summary.
```

The execution was successful:

```text
Day 1:
  buy GLD 108
  buy SPY 90
  buy VGIT 416

Day 2:
  sell VGIT 416
  sell SPY 44
  buy VTIP 511
  buy GLD 105
```

Every submitted order was followed by `orders_confirm_order`, and the second
day completed same-day rotation.

## 3. Problem

The execution agent currently spends too much attention on repetitive
mechanical work:

- Re-checking account state.
- Re-checking positions.
- Re-checking open orders.
- Re-checking the next symbol's price.
- Remembering to submit.
- Remembering to confirm after every submit.
- Waiting for confirmation before proceeding.

These are execution mechanics, not investment reasoning.

The LLM should not be the long-term owner of stable mechanical procedures once
those procedures become clear enough to encode and test.

However, simply hiding the mechanics inside a black-box tool would undermine
the Agent Replay UI goal. The system must save and show the internal execution
steps even when the model sees a higher-level tool.

## 4. Design Principles

This roadmap is governed by these principles.

1. Do not sacrifice trace transparency to save tokens.

   A combined tool may reduce model-facing calls, but internal steps must be
   recorded in trace data and visible in the UI.

2. Move only stable mechanics into tools.

   Deterministic code should own checking, submitting, confirming, sequencing,
   and blocker reporting. It should not own asset selection or target weights.

3. Keep each stage independently shippable.

   Each stage must pass unit tests, short backtests, and replay UI review
   before the next stage starts.

4. Keep prompts and tool lists synchronized.

   If a new higher-level tool becomes the recommended path, the execution
   agent prompt and visible tool list must stop encouraging the older lower
   level path.

5. Prefer narrow tools before large tools.

   A small tool with crisp boundaries is easier to test than a large tool that
   silently absorbs several responsibilities at once.

6. Preserve the current strict execution contract.

   Tools execute explicit `execution_plan.orders`. They do not change symbols,
   sides, quantities, order type, sequence, or investment intent.

7. Keep old functionality available internally.

   Existing low-level logic can remain reusable by Python wrappers even if the
   lower-level tool names are no longer shown to the LLM at later stages.

## 5. Roadmap Status Table

| Stage | Status | Primary Tool | Main Goal | Validation Gate |
|---|---|---|---|---|
| A | planned | `orders_preflight_check` | Package pre-order account, position, open-order, and price checks. | One order preflight appears as one model-facing tool call while internal checks remain traceable. |
| B | planned | `orders_submit_and_confirm_order` | Package submit plus confirmation for one order. | Every submitted order is confirmed inside one model-facing tool call. |
| C | planned | `orders_execute_order` | Execute one explicit order end to end. | One order requires one model-facing execution call with preflight, submit, and confirm internally traced. |
| D | planned | `execution_plan_execute` | Execute an entire strict execution plan. | A multi-order rebalance can be executed by one model-facing plan call with per-order internal trace. |

After each stage is implemented and validated, this table should be updated
with:

- implemented commit or branch name
- test commands
- benchmark artifact path
- UI review notes
- lessons that affect later stages

## 6. Stage A: `orders_preflight_check`

### 6.1 Goal

Create one read-only tool that packages the checks usually performed before
submitting one explicit order.

This reduces model-facing tool clutter and gives the execution agent one
human-readable readiness report.

### 6.2 Model-Facing Tool Name

```text
orders_preflight_check
```

### 6.3 Model-Facing Description

Draft description:

```text
Inspect whether one explicit execution_plan order appears ready to submit.
The tool checks current cash, portfolio value, current position in the symbol,
open orders, and latest price. It does not submit, cancel, or modify orders.
Use it before executing an order when this tool is available.
```

### 6.4 Input Scope

Recommended input:

```json
{
  "sequence": 1,
  "symbol": "GLD",
  "side": "buy",
  "quantity": 105,
  "asset_type": "stock",
  "order_type": "market",
  "time_in_force": "day"
}
```

### 6.5 Internal Steps

The tool should internally collect:

```text
account_portfolio
account_positions
orders_open_orders
market_last_price(symbol)
```

The implementation may call underlying Python helpers directly rather than
calling ADK tools through the model-facing tool path.

### 6.6 Output Scope

Recommended output:

```json
{
  "sequence": 1,
  "symbol": "GLD",
  "side": "buy",
  "quantity": 105,
  "readiness": "ready",
  "blockers": [],
  "cash": 24479.32,
  "portfolio_value": 100000.0,
  "current_position_quantity": 108,
  "open_orders": [],
  "last_price": 231.83,
  "estimated_order_value": 24342.15
}
```

If not ready, `readiness` should be `"blocked"` and `blockers` should explain
why.

### 6.7 Non-Goals

Stage A does not:

- submit orders
- confirm orders
- execute an entire order
- execute an entire plan
- replace `orders_submit_order`
- replace `orders_confirm_order`

### 6.8 Tool Visibility

During Stage A, `execution_agent` may see:

```text
orders_preflight_check
orders_submit_order
orders_confirm_order
```

The old account and price tools can remain visible during the first validation
slice if needed for fallback, but the preferred prompt should ask the agent to
use `orders_preflight_check` first.

### 6.9 Execution Prompt Direction

The execution-agent prompt should be adjusted to say:

```text
Before submitting each execution_plan order, use orders_preflight_check when
that tool is available. If the preflight result is ready, submit the order and
confirm it. If the preflight result is blocked, stop and report the blocker.
```

### 6.10 Acceptance Criteria

Stage A passes when:

- `orders_preflight_check` exists as a model-facing tool.
- It returns account, position, open-order, price, and readiness information.
- It has no trading side effects.
- A short benchmark shows the execution agent using it before orders.
- Internal checks are visible in trace or preserved as structured substeps.
- Existing submit and confirm behavior still works.

## 7. Stage B: `orders_submit_and_confirm_order`

### 7.1 Goal

Create one mutating tool that submits one explicit order and confirms it before
returning.

This packages the rule that every `orders_submit_order` call must be followed
by `orders_confirm_order`.

### 7.2 Model-Facing Tool Name

```text
orders_submit_and_confirm_order
```

### 7.3 Model-Facing Description

Draft description:

```text
Submit one explicit execution_plan order and confirm that same order before
returning. The tool returns whether the order was submitted, whether it was
confirmed filled, whether execution can continue, the order identifier, and an
account snapshot after confirmation. It does not perform investment research
or modify the order fields.
```

### 7.4 Input Scope

Recommended input:

```json
{
  "sequence": 1,
  "symbol": "GLD",
  "side": "buy",
  "quantity": 105,
  "asset_type": "stock",
  "order_type": "market",
  "time_in_force": "day",
  "cash_before": 24479.32,
  "position_before_quantity": 108
}
```

The exact required optional context can be decided in the Stage B spec.

### 7.5 Internal Steps

The tool should internally do:

```text
orders_submit_order
orders_confirm_order
```

It should use the returned order identifier from submit as the confirmation
target.

### 7.6 Output Scope

Recommended output:

```json
{
  "sequence": 1,
  "symbol": "GLD",
  "side": "buy",
  "quantity": 105,
  "submitted": true,
  "confirmed": true,
  "confirmation_status": "filled",
  "can_continue": true,
  "order": {
    "identifier": "bt_7",
    "status": "fill",
    "filled_quantity": 105
  },
  "account_snapshot": {
    "cash": 137.17,
    "positions": []
  },
  "blockers": []
}
```

### 7.7 Non-Goals

Stage B does not:

- perform preflight by itself unless explicitly designed in the Stage B spec
- execute more than one order
- execute an entire plan
- decide order sequence
- change any provided order field

### 7.8 Tool Visibility

During Stage B, the preferred execution-agent tool set is:

```text
orders_preflight_check
orders_submit_and_confirm_order
```

The lower-level `orders_submit_order` and `orders_confirm_order` should be
hidden from the LLM once Stage B is stable enough, while remaining reusable
inside Python.

### 7.9 Execution Prompt Direction

The execution-agent prompt should be adjusted to say:

```text
For each execution_plan order, use orders_preflight_check. If ready, use
orders_submit_and_confirm_order. Do not separately call orders_submit_order or
orders_confirm_order when the combined submit-and-confirm tool is available.
Proceed to the next order only when can_continue=true.
```

### 7.10 Acceptance Criteria

Stage B passes when:

- Submit and confirm happen within one model-facing tool call.
- The returned confirmation proves the submitted order reached a terminal
  acceptable state or returns a blocker.
- Failed confirmation returns `can_continue=false`.
- A short rebalance benchmark still completes same-day rotation.
- UI trace can show submit and confirm as internal substeps.

## 8. Stage C: `orders_execute_order`

### 8.1 Goal

Create one tool that executes one explicit order end to end:

```text
preflight
submit
confirm
```

This is the first stage where one order should normally require one
model-facing execution call.

### 8.2 Model-Facing Tool Name

```text
orders_execute_order
```

### 8.3 Model-Facing Description

Draft description:

```text
Execute exactly one explicit order from execution_plan.orders. The tool checks
readiness, submits the order if ready, confirms the submitted order, and
returns the final order status plus whether execution can continue. It must
not change the order symbol, side, quantity, order type, or sequence.
```

### 8.4 Input Scope

Recommended input:

```json
{
  "sequence": 4,
  "symbol": "GLD",
  "side": "buy",
  "quantity": 105,
  "asset_type": "stock",
  "order_type": "market",
  "time_in_force": "day"
}
```

### 8.5 Internal Steps

The tool should internally do:

```text
orders_preflight_check
orders_submit_order
orders_confirm_order
```

or equivalent lower-level Python helper calls.

### 8.6 Output Scope

Recommended output:

```json
{
  "sequence": 4,
  "symbol": "GLD",
  "side": "buy",
  "quantity": 105,
  "status": "filled",
  "can_continue": true,
  "preflight": {},
  "submission": {},
  "confirmation": {},
  "account_after": {},
  "blockers": []
}
```

### 8.7 Non-Goals

Stage C does not:

- execute multiple orders
- reorder a plan
- modify order fields
- decide whether the execution plan is good
- decide target portfolio
- perform investment research

### 8.8 Tool Visibility

During Stage C, the preferred execution-agent tool set is:

```text
orders_execute_order
```

The lower-level tools should normally be hidden from the LLM to prevent it from
continuing the older multi-round flow.

### 8.9 Execution Prompt Direction

The execution-agent prompt should be adjusted to say:

```text
For each execution_plan order, call orders_execute_order exactly once in
ascending sequence order. Do not manually preflight, submit, or confirm with
lower-level tools when orders_execute_order is available. Stop if the tool
returns can_continue=false.
```

### 8.10 Acceptance Criteria

Stage C passes when:

- A multi-order rebalance uses one model-facing `orders_execute_order` call per
  order.
- Each order internally records preflight, submit, and confirm substeps.
- Same-day sell-then-buy rotation still succeeds.
- No negative cash occurs.
- The final execution summary matches actual filled orders.

## 9. Stage D: `execution_plan_execute`

### 9.1 Goal

Create one tool that executes an entire strict execution plan.

At this stage, the execution agent should mostly:

```text
receive execution_plan
call execution_plan_execute
summarize the result
```

### 9.2 Model-Facing Tool Name

```text
execution_plan_execute
```

### 9.3 Model-Facing Description

Draft description:

```text
Execute a complete strict execution_plan in sequence order. The tool validates
the plan, executes each order through readiness checks, submission, and
confirmation, stops on blockers, and returns a complete execution report. It
does not change investment intent or modify order fields.
```

### 9.4 Input Scope

Recommended input:

```json
{
  "execution_plan": {
    "schema_version": 1,
    "intent": "rebalance",
    "orders": [
      {
        "sequence": 1,
        "action": "submit_order",
        "symbol": "VGIT",
        "side": "sell",
        "quantity_mode": "shares",
        "quantity": 416,
        "asset_type": "stock",
        "order_type": "market",
        "time_in_force": "day"
      }
    ]
  }
}
```

### 9.5 Internal Steps

The tool should internally do:

```text
validate execution_plan schema
sort or verify sequence order
for each order:
  orders_execute_order
  stop if can_continue=false
return full execution report
```

The Stage D spec should decide whether to sort sequence numbers or reject
plans that are not already sorted. The safer first behavior is to reject
invalid sequence order and require the planner to generate a valid order
sequence.

### 9.6 Output Scope

Recommended output:

```json
{
  "plan_status": "completed",
  "completed_orders": [],
  "blocked_orders": [],
  "final_account_snapshot": {},
  "order_results": [
    {
      "sequence": 1,
      "symbol": "VGIT",
      "side": "sell",
      "status": "filled",
      "can_continue": true
    }
  ],
  "summary": "All planned orders were filled."
}
```

### 9.7 Non-Goals

Stage D does not:

- generate the execution plan
- change target weights
- choose symbols
- reorder investment intent
- optimize taxes
- slice orders
- choose limit prices
- implement broker-specific smart routing

### 9.8 Tool Visibility

During Stage D, the preferred execution-agent tool set is:

```text
execution_plan_execute
```

Lower-level execution tools should be hidden from the LLM in normal operation
and reserved for internal code or debugging modes.

### 9.9 Execution Prompt Direction

The execution-agent prompt should be adjusted to say:

```text
Call execution_plan_execute once with the provided execution_plan. Do not
manually execute individual orders when this tool is available. If the tool
returns completed, summarize the completed execution. If it returns blocked,
summarize where execution stopped and why.
```

### 9.10 Acceptance Criteria

Stage D passes when:

- A multi-order rebalance normally takes one model-facing execution tool call.
- The tool executes orders strictly in sequence order.
- Sell confirmations occur before later buy orders.
- Any failed order stops remaining orders.
- Internal trace shows plan -> order -> preflight -> submit -> confirm.
- Agent Replay UI can inspect each internal order result.

## 10. Prompt Architecture Requirements

Each stage must update prompts together with tool visibility.

The execution-agent prompt should become shorter and stricter as tools become
higher-level.

### 10.1 General Execution-Agent Rules Across All Stages

These rules should remain true:

- Execute only the provided `execution_plan`.
- Do not perform investment research.
- Do not re-rank candidates.
- Do not substitute symbols.
- Do not change sides, quantities, order type, or sequence.
- Stop on execution blockers.
- Report what was submitted, confirmed, blocked, or skipped.

### 10.2 Stage-Specific Prompt Ownership

The prompt should name the highest-level tool available for the current stage.

Examples:

```text
Stage A:
  Use orders_preflight_check before submitting each order.

Stage B:
  Use orders_submit_and_confirm_order after preflight.

Stage C:
  Use orders_execute_order for each order.

Stage D:
  Use execution_plan_execute once for the whole plan.
```

The prompt should not continue telling the model to call old lower-level tools
once those tools are hidden or no longer preferred.

## 11. Tool List Strategy

The visible execution-agent tool list should shrink over time.

Recommended progression:

```text
Current:
  account_portfolio
  account_positions
  orders_open_orders
  market_last_price
  orders_submit_order
  orders_confirm_order

Stage A:
  orders_preflight_check
  orders_submit_order
  orders_confirm_order

Stage B:
  orders_preflight_check
  orders_submit_and_confirm_order

Stage C:
  orders_execute_order

Stage D:
  execution_plan_execute
```

During validation slices, old tools may remain visible briefly as fallback,
but the stage-specific spec must explicitly decide that. The long-term desired
state is a small tool surface.

## 12. Tool Description Strategy

Every new tool must include a concise model-facing description.

The description must answer:

- What does the tool do?
- What does it not do?
- What fields does the model need to pass?
- Does it have trading side effects?
- What should the model do when `can_continue=false`?

Descriptions should avoid broad investment language. These are execution
tools, not research tools.

## 13. Trace And UI Requirements

Combined tools must preserve internal trace details.

The UI should not collapse a combined tool into an opaque success message.

Desired hierarchy by final stage:

```text
execution_plan_execute
  order sequence 1
    orders_execute_order
      orders_preflight_check
        account_portfolio
        account_positions
        orders_open_orders
        market_last_price
      orders_submit_order
      orders_confirm_order
  order sequence 2
    ...
```

Model-facing trace can show the high-level tool call. Developer-facing trace
should show internal substeps.

Each stage-specific spec should decide the minimal UI support needed:

- Stage A can likely use existing tool-call panels.
- Stage B may need nested submit/confirm details in a combined result.
- Stage C likely needs order-level nested substep display.
- Stage D likely needs plan-level nested display.

## 14. Testing And Validation Strategy

Each stage gets its own tests and benchmark validation.

Required validation categories:

1. Unit tests for tool behavior.
2. Tool definition tests for model-facing names, descriptions, schemas, and
   visibility.
3. Prompt tests proving the execution-agent prompt references the new stage
   tool and does not contradict the visible tool list.
4. Trace tests proving internal substeps are recorded.
5. Replay UI tests if the stage changes display needs.
6. One-day benchmark for initial deployment.
7. Two-day benchmark for same-day rebalance and rotation.

Each stage must save:

- test commands
- benchmark command
- benchmark artifact path
- short result notes
- known limitations before moving to the next stage

## 15. Gating Rules Between Stages

The team may start a later stage only after the current stage passes:

- unit tests
- relevant prompt/tool-list tests
- short benchmark
- replay UI inspection
- no unexplained order failures
- no negative cash regression
- final execution summary matches actual trades

If a stage exposes a serious regression, the next stage should not start.

Instead, either:

- fix the current stage, or
- explicitly pause that stage and document why a later stage is still safe.

The default rule is sequential progression.

## 16. Non-Goals For This Roadmap

This roadmap does not change:

- macro regime classification
- basket definitions
- basket agent selection logic
- portfolio target generation
- `target_portfolio_to_execution_plan`
- order quantity calculation in the portfolio planner
- broker integrations
- slippage modeling
- tax optimization
- fractional share support
- options, leverage, shorting, or margin policy
- live trading authorization

The roadmap starts after a strict `execution_plan` already exists.

## 17. Relationship To `target_portfolio_to_execution_plan`

`target_portfolio_to_execution_plan` remains upstream of execution.

Its responsibility:

```text
target portfolio -> strict execution_plan
```

The execution compression roadmap starts after this point.

Its responsibility:

```text
strict execution_plan -> submitted and confirmed orders
```

These responsibilities must not merge unless a future design explicitly
chooses to build a broader deterministic portfolio engine.

## 18. Recommended Next Step

After this roadmap spec is reviewed, write a separate Stage A design spec:

```text
orders_preflight_check design
```

That Stage A spec should be scoped only to:

- the `orders_preflight_check` tool
- Stage A tool visibility
- Stage A execution-agent prompt changes
- Stage A trace and UI expectations
- Stage A tests and benchmark validation

It should reference this roadmap but not implement B, C, or D.

## 19. Risks

### 19.1 Hidden Complexity

Risk:

Combined tools could hide important state transitions.

Mitigation:

Require internal substep trace and UI visibility before accepting each stage.

### 19.2 Prompt And Tool List Drift

Risk:

The prompt may tell the model to use a tool that is no longer visible, or the
tool list may expose lower-level tools that encourage old behavior.

Mitigation:

Every stage-specific spec must include prompt tests and tool visibility tests.

### 19.3 Over-Compression Too Early

Risk:

Jumping directly to Stage D could make failures harder to diagnose.

Mitigation:

Proceed sequentially. Validate each stage with one-day and two-day benchmarks.

### 19.4 Tool Starts Making Investment Decisions

Risk:

Higher-level execution tools could drift into deciding what to trade.

Mitigation:

Execution tools only consume explicit order fields or strict execution plans.
They must not choose symbols, weights, or quantities.

### 19.5 Backtest Behavior Differs From Live Broker Behavior

Risk:

Backtests may fill market orders immediately while live brokers may have more
latency, partial fills, or rejections.

Mitigation:

Keep confirmation semantics explicit. Later stage specs can add broker-aware
confirmation states without changing the overall roadmap.

## 20. Review Checklist For Future Stage Specs

Before implementing each stage, its stage-specific spec should answer:

- What exact new tool is added?
- What exact tools are visible to `execution_agent`?
- What exact tool description is sent to the model?
- What exact execution-agent prompt changes are needed?
- What internal substeps are recorded?
- How does the UI display parent and child steps?
- What happens on a blocker?
- What happens when confirmation fails?
- How do we prove old lower-level behavior is not accidentally still used?
- What one-day and two-day benchmark commands verify the stage?

This checklist is part of the roadmap's guardrail. It should be copied or
referenced by each stage-specific design spec.
