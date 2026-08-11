# Execution Plan Execute Stage D Design

Date: 2026-08-11

## 1. Purpose

This spec defines Stage D of the execution tool compression roadmap:

```text
execution_plan_execute
```

Stage D packages a complete strict `execution_plan` into one model-facing
execution-agent tool call.

The execution agent should move from this Stage C behavior:

```text
for each order:
  call orders_execute_order once
summarize
```

to this Stage D behavior:

```text
call execution_plan_execute once with the complete execution_plan
summarize
```

The core purpose is not to add investment intelligence. It is to reduce
repetitive LLM-driven mechanical execution steps and make same-day rebalance
execution more deterministic, inspectable, and less token-sensitive.

## 2. Context

The current mock growth/inflation quadrant workflow is:

```text
macro_allocation_agent
  -> four basket agents
      -> portfolio_decision_agent
          -> execution_agent
```

The upstream portfolio flow already exists:

```text
macro + basket reports
  -> target_portfolio
  -> target_portfolio_to_execution_plan
  -> strict execution_plan
```

Stage D starts only after the strict `execution_plan` exists.

The most recent Stage C validation showed that:

- `execution_agent` sees only `orders_execute_order`.
- A one-day benchmark completed with three model-facing `orders_execute_order`
  calls.
- A two-day benchmark completed a same-day sell-then-buy rebalance with four
  model-facing `orders_execute_order` calls on the second day.
- Sell orders completed before later buy orders.
- No `NEGATIVE_CASH_NOT_ALLOWED` blocker appeared.
- Final cash was positive.
- Agent traces had no warnings.
- Focused tests passed.

Stage D should build on this proven Stage C building block instead of replacing
the existing readiness, submission, confirmation, or cash-safety logic.

## 3. Stage D Goal

Create one model-facing mutating tool:

```text
execution_plan_execute
```

The tool should:

1. Accept one complete strict `execution_plan`.
2. Validate the plan shape before placing any order.
3. Verify that order sequence is already valid.
4. Execute orders in ascending sequence order.
5. For each order, internally reuse Stage C `orders_execute_order` behavior.
6. Stop immediately if any order returns `can_continue=false`.
7. Return a complete plan-level execution report.
8. Preserve nested order-level and substep-level trace information.

The tool should not generate, repair, reorder, optimize, or reinterpret the
plan.

## 4. Non-Goals

Stage D does not:

- generate the `execution_plan`
- decide basket weights
- choose symbols
- choose target weights
- calculate share quantities
- change order symbols, sides, quantities, order type, or time in force
- reorder investment intent
- silently sort invalid order sequences
- split large orders
- choose limit prices
- implement broker-specific smart routing
- implement tax optimization
- implement fractional-share support
- implement short selling, margin, leverage, options, or new asset permissions
- fix benchmark performance-statistics reporting
- change `target_portfolio_to_execution_plan`
- change basket selection logic

The existing benchmark/reporting issue where `result.json.backtest_result` can
disagree with `stats.csv` is outside this spec. Stage D validation should use
trace, trades, positions, cash, and saved reports as primary evidence, not only
the aggregate return in `result.json`.

## 5. Naming

### 5.1 Model-Facing Tool Name

```text
execution_plan_execute
```

This name intentionally does not start with `orders_` because it operates at a
plan level, not an individual order level.

### 5.2 Python API Shape

The preferred built-in access point is:

```python
BuiltinTools.orders.execute_plan()
```

This keeps the tool near the existing order execution tools while preserving
the model-facing name `execution_plan_execute`.

## 6. Model-Facing Tool Description

The tool description should be concise and explicit:

```text
Execute one complete strict execution_plan in sequence order. This tool
validates the plan, executes each order through readiness checks, submission,
and confirmation, stops on the first blocker, and returns a complete execution
report. It mutates trading state. It does not generate, repair, reorder,
optimize, or modify the plan. Pass the execution_plan exactly as provided by
the upstream planner. If plan_status is blocked or partial, do not call lower
level tools; summarize where execution stopped and why.
```

The description should not contain market-analysis language. This is an
execution tool, not a research or portfolio construction tool.

## 7. Input Contract

The model-facing input should be:

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
        "quantity": 406,
        "asset_type": "stock",
        "order_type": "market",
        "time_in_force": "day"
      },
      {
        "sequence": 2,
        "action": "submit_order",
        "symbol": "SPY",
        "side": "sell",
        "quantity_mode": "shares",
        "quantity": 42,
        "asset_type": "stock",
        "order_type": "market",
        "time_in_force": "day"
      }
    ]
  }
}
```

The Stage D tool should accept only a complete `execution_plan` object. It
should not accept loose top-level order fields like `symbol`, `side`, or
`quantity`.

## 8. Supported Plan Shape

The first Stage D implementation should support the strict plan shape already
generated by the mock quadrant planner:

```json
{
  "schema_version": 1,
  "intent": "rebalance",
  "orders": []
}
```

Supported `intent` values:

- `rebalance`
- `hold`

Supported order fields:

| Field | Requirement |
|---|---|
| `sequence` | Required positive integer. Must be `1..N` with no gaps. |
| `action` | Required. Must be `submit_order`. |
| `symbol` | Required non-empty string. |
| `side` | Required. Must be `buy` or `sell`. |
| `quantity_mode` | Required or defaultable to `shares`. Stage D supports `shares`. |
| `quantity` | Required positive whole-share number. |
| `asset_type` | Required or defaultable to `stock`. Stage D supports `stock` and `us_equity`. |
| `order_type` | Required or defaultable to `market`. Stage D supports `market`. |
| `time_in_force` | Required or defaultable to `day`. Stage D supports `day`. |

If `intent="hold"`, `orders` must be empty. A hold plan should return a
completed no-op report without calling Stage C per-order execution.

## 9. Sequence Policy

Stage D should reject invalid sequence order instead of sorting it.

Reason:

The planner owns order sequencing. For a rebalance, sequence can encode safety
intent such as:

```text
sell first
then buy
```

If `execution_plan_execute` silently sorts or repairs the plan, it becomes a
planner. That would blur responsibilities and make bugs harder to diagnose.

Validation rules:

- `sequence` values must be integers.
- The set of sequence values must exactly equal `1..N`.
- The order list must already appear in ascending sequence order.
- Duplicate sequence values are blockers.
- Missing sequence values are blockers.
- Buy-before-sell ordering is not automatically rejected if the upstream
  planner deliberately emitted it, but existing cash-safety checks may block
  it. Stage D should not impose new portfolio logic beyond strict sequence
  validation.

## 10. Internal Execution Flow

For `intent="rebalance"` with one or more orders:

```text
execution_plan_execute
  validate execution_plan
  initialize plan report
  for each order in listed sequence:
    call internal Stage C order executor with exact order fields
    append order result
    if order_result.can_continue is false:
      mark plan blocked
      mark remaining orders skipped
      return plan report
  mark plan completed
  return plan report
```

The internal Stage C call should reuse existing Python helper behavior rather
than making nested LLM/ADK model-facing calls.

The implementation may call the bound `orders_execute_order` function
internally, or extract a shared helper if that reduces duplication. Either way,
the semantics must remain equivalent to Stage C:

```text
preflight
submit
confirm
negative-cash guard
can_continue handling
account_after snapshot
structured blockers
```

## 11. Output Contract

Successful completed plan:

```json
{
  "schema_version": 1,
  "plan_status": "completed",
  "can_continue": true,
  "intent": "rebalance",
  "orders_requested": 4,
  "orders_attempted": 4,
  "orders_completed": 4,
  "orders_blocked": 0,
  "orders_skipped": 0,
  "completed_orders": [
    {
      "sequence": 1,
      "symbol": "VGIT",
      "side": "sell",
      "quantity": 406,
      "execution_status": "completed",
      "confirmed": true,
      "order_identifier": "bt_4"
    }
  ],
  "blocked_orders": [],
  "skipped_orders": [],
  "order_results": [
    {
      "sequence": 1,
      "symbol": "VGIT",
      "side": "sell",
      "quantity": 406,
      "execution_status": "completed",
      "can_continue": true,
      "order_result": {}
    }
  ],
  "final_account_snapshot": {},
  "blockers": [],
  "warnings": [],
  "summary": "All 4 planned orders were completed and confirmed."
}
```

Blocked plan:

```json
{
  "schema_version": 1,
  "plan_status": "blocked",
  "can_continue": false,
  "intent": "rebalance",
  "orders_requested": 4,
  "orders_attempted": 2,
  "orders_completed": 1,
  "orders_blocked": 1,
  "orders_skipped": 2,
  "completed_orders": [],
  "blocked_orders": [
    {
      "sequence": 2,
      "symbol": "SPY",
      "side": "buy",
      "quantity": 999,
      "execution_status": "blocked",
      "blockers": [
        {
          "code": "NEGATIVE_CASH_NOT_ALLOWED",
          "message": "..."
        }
      ]
    }
  ],
  "skipped_orders": [
    {
      "sequence": 3,
      "symbol": "GLD",
      "side": "buy",
      "quantity": 10,
      "skip_reason": "stopped_after_sequence_2_blocked"
    }
  ],
  "order_results": [],
  "final_account_snapshot": {},
  "blockers": [
    {
      "code": "ORDER_BLOCKED",
      "message": "Execution stopped at sequence 2."
    }
  ],
  "warnings": [],
  "summary": "Execution stopped at sequence 2 because SPY buy was blocked."
}
```

Invalid plan:

```json
{
  "schema_version": 1,
  "plan_status": "invalid",
  "can_continue": false,
  "orders_requested": 0,
  "orders_attempted": 0,
  "orders_completed": 0,
  "orders_blocked": 0,
  "orders_skipped": 0,
  "completed_orders": [],
  "blocked_orders": [],
  "skipped_orders": [],
  "order_results": [],
  "final_account_snapshot": {},
  "blockers": [
    {
      "code": "INVALID_EXECUTION_PLAN",
      "message": "execution_plan.orders must already be sorted by sequence."
    }
  ],
  "warnings": [],
  "summary": "No orders were submitted because the execution plan was invalid."
}
```

## 12. Plan Status Values

Use these plan-level statuses:

| Status | Meaning |
|---|---|
| `completed` | All requested orders completed, or a hold plan had no orders. |
| `blocked` | At least one order was attempted and blocked; later orders were skipped. |
| `invalid` | Plan validation failed before any order was submitted. |

Avoid a separate `partial` status in the first Stage D implementation.
If some orders completed and a later order blocked, use:

```text
plan_status = "blocked"
orders_completed > 0
orders_skipped > 0
```

This is easier for the execution agent to summarize and keeps the first
implementation narrower.

## 13. Blocker Codes

Stage D should preserve Stage A/C blocker codes where they originate.

Plan-level blockers should use these additional codes:

| Code | Meaning |
|---|---|
| `MISSING_EXECUTION_PLAN` | No execution_plan object was provided. |
| `INVALID_EXECUTION_PLAN` | The top-level plan shape is invalid. |
| `UNSUPPORTED_PLAN_SCHEMA_VERSION` | schema_version is not supported. |
| `UNSUPPORTED_PLAN_INTENT` | intent is not `rebalance` or `hold`. |
| `INVALID_ORDER_SEQUENCE` | sequence values are missing, duplicated, non-integer, or out of order. |
| `INVALID_ORDER_ACTION` | action is not `submit_order`. |
| `INVALID_ORDER_FIELDS` | order field validation failed before execution. |
| `ORDER_BLOCKED` | A Stage C order returned can_continue=false. |

Each blocker should include:

```json
{
  "code": "INVALID_ORDER_SEQUENCE",
  "message": "execution_plan.orders must be listed in ascending sequence order.",
  "sequence": 2,
  "symbol": "SPY"
}
```

`sequence` and `symbol` may be omitted when the blocker is top-level.

## 14. Error Handling

The tool should prefer structured blocker payloads over raw exceptions.

Unexpected Python exceptions from internal order execution should be caught and
returned as:

```json
{
  "plan_status": "blocked",
  "can_continue": false,
  "blockers": [
    {
      "code": "EXECUTION_EXCEPTION",
      "message": "..."
    }
  ]
}
```

If an exception happens before any order is submitted, `orders_attempted` should
be `0`.

If an exception happens after earlier orders completed, the report should show:

- completed prior orders
- blocked current order if identifiable
- skipped later orders
- final account snapshot if available

## 15. Account Snapshot Policy

The Stage D report should include:

- `initial_account_snapshot` if cheaply available before execution
- `final_account_snapshot` after completion or blocking
- per-order `account_after` inside each nested order result when Stage C
  returned it

The first implementation may omit `initial_account_snapshot` if extracting it
would duplicate Stage A checks, but it should include `final_account_snapshot`
whenever Stage C provides one.

## 16. Replay And Trace Requirements

Stage D must not become an opaque one-line result.

Developer-facing trace should preserve this hierarchy:

```text
execution_plan_execute
  sequence 1
    orders_execute_order
      orders_preflight_check
      orders_submit_and_confirm_order
        orders_submit_order
        orders_confirm_order
  sequence 2
    ...
```

The model-facing trace will show one `execution_plan_execute` call.

The tool result should include enough nested structured data for Agent Replay
UI to show:

- plan status
- count of requested/attempted/completed/blocked/skipped orders
- each order in sequence order
- each order's final status
- each order's blocker if blocked
- internal substeps for each attempted order
- final account snapshot

Large nested details may still be pruned before sending back to the LLM, but
the saved replay trace should retain the full raw result when the runtime
already supports preserving full payloads. If current trace mechanics only
store the pruned model-facing payload, the implementation plan should add the
smallest practical improvement needed to keep Stage D inspectable.

## 17. Replay UI Formatter

Add a formatter for `execution_plan_execute`.

Minimum human explanation:

```text
Execution plan completed: 4 requested, 4 attempted, 4 completed, 0 blocked, 0 skipped.
Completed orders: 1 sell VGIT 406, 2 sell SPY 42, 3 buy VTIP 501, 4 buy GLD 107.
Final cash: 630.15 USD.
```

Blocked example:

```text
Execution plan blocked at sequence 2: buy SPY 999 failed with NEGATIVE_CASH_NOT_ALLOWED.
Completed before block: 1 order. Skipped after block: 2 orders.
```

Invalid example:

```text
Execution plan invalid before submission: INVALID_ORDER_SEQUENCE.
No orders were submitted.
```

The formatter does not need a new visual tree in the first implementation if a
clear text summary and structured JSON remain available. A richer plan-level UI
tree may be deferred unless the first UI is too hard to inspect.

## 18. Tool Visibility

For the mock growth/inflation quadrant Stage D strategy path, the execution
agent should receive only:

```text
execution_plan_execute
```

These should not be visible to that execution agent during normal Stage D
operation:

```text
orders_preflight_check
orders_submit_order
orders_confirm_order
orders_submit_and_confirm_order
orders_execute_order
account_portfolio
account_positions
orders_open_orders
market_last_price
```

Those lower-level tools should remain available in Python and tests because
`execution_plan_execute` composes them internally.

## 19. AgentManager Policy

`AgentManager._execution_tool_policy_prompt()` should understand
`execution_plan_execute`.

Suggested policy text:

```text
execution_plan_execute executes one complete strict execution_plan in sequence
order. It validates the plan, executes each order through readiness checks,
submission, and confirmation, and stops on the first blocker. It does not
research, generate, repair, reorder, optimize, or modify the plan.
```

The execution tool policy should also treat `execution_plan_execute` as:

- an execution tool
- a visible data/order evidence tool for warnings
- a mutating trading tool

## 20. Execution Agent Prompt Changes

The mock quadrant execution-agent system prompt should be shortened from Stage
C wording to Stage D wording.

Current Stage C direction:

```text
For each order in ascending sequence order, call orders_execute_order exactly
once with exact order fields.
```

Target Stage D direction:

```text
Execution role: execute only the provided execution_plan. Call
execution_plan_execute exactly once with the complete execution_plan. Do not
manually execute individual orders. Do not call lower-level order, account,
open-order, or price tools when execution_plan_execute is available. Do not
research, change fields, reorder orders, split orders, or repair the plan. If
the tool returns plan_status=completed, summarize completed orders. If it
returns plan_status=blocked or invalid, summarize where execution stopped and
why.
```

The execution-agent task prompt should similarly say:

```text
Execute the provided execution_plan by calling execution_plan_execute exactly
once with the complete plan. Summarize the returned plan report. Do not call
per-order tools.
```

## 21. Prompt Anti-Patterns To Remove

The Stage D prompt must not contain:

- `orders_execute_order`
- `orders_preflight_check`
- `orders_submit_order`
- `orders_confirm_order`
- `orders_submit_and_confirm_order`
- `for each order, call ...`
- `manually preflight`
- `manually submit`
- `manually confirm`
- `query account`
- `query latest prices`
- `calculate quantity`
- `2% buy sizing buffer`
- investment research instructions

The execution agent should see execution as one deterministic tool call plus
summary.

## 22. Portfolio Decision Agent Prompt

The portfolio decision agent should not change its responsibility.

It should still:

1. merge macro and basket reports
2. create `target_portfolio`
3. call `target_portfolio_to_execution_plan`
4. copy the tool-generated `execution_plan` exactly

It should not be told about `execution_plan_execute`.

Reason:

`execution_plan_execute` belongs to the execution agent. If the portfolio
decision agent sees execution-stage instructions, it may start reasoning about
execution mechanics again, which would undo the boundary we worked to create.

## 23. Tool Definition And Schema Tests

Tests should prove:

1. `BuiltinTools.orders.execute_plan()` exists.
2. Its model-facing name is `execution_plan_execute`.
3. Its description says it executes a complete strict `execution_plan`.
4. Its description says it mutates trading state.
5. Its description says it does not generate, repair, reorder, optimize, or
   modify the plan.
6. It binds to a callable tool.
7. Bound tool metadata includes:

```json
{
  "kind": "builtin",
  "replay_on_cache": true,
  "mutates_trading": true
}
```

8. `BuiltinTools.all()` includes `execution_plan_execute`.

## 24. Tool Behavior Tests

Add unit tests for:

1. Completed hold plan with no orders.
2. Completed rebalance plan with multiple valid orders.
3. Plan-level validation failure before any order is submitted.
4. Duplicate sequence numbers block before any order is submitted.
5. Missing sequence number blocks before any order is submitted.
6. Out-of-order list blocks before any order is submitted.
7. Unsupported intent blocks before any order is submitted.
8. Unsupported action blocks before any order is submitted.
9. Unsupported order type blocks before any order is submitted.
10. Invalid quantity blocks before any order is submitted.
11. A Stage C preflight blocker stops the plan and skips later orders.
12. A Stage C confirmation blocker stops the plan and skips later orders.
13. Negative cash protection still blocks.
14. Earlier completed orders remain reported when a later order blocks.
15. Remaining orders are reported as skipped after the first blocker.

The tests should not rely only on mocks that always succeed. They should use
existing order fake strategy helpers where possible so the test exercises real
Stage C behavior.

## 25. Strategy Tool-Surface Tests

Update mock quadrant strategy tests so:

1. `execution_agent` visible tools equal:

```text
execution_plan_execute
```

2. `execution_agent` does not see:

```text
orders_execute_order
orders_submit_and_confirm_order
orders_preflight_check
orders_submit_order
orders_confirm_order
account_portfolio
account_positions
orders_open_orders
market_last_price
```

3. Non-execution agents do not see `execution_plan_execute`.
4. The prompt boundary test requires `execution_plan_execute`.
5. The prompt boundary test rejects Stage C phrases like
   `orders_execute_order exactly once`.

## 26. AgentManager Tests

Add tests so manager policy:

1. Includes Stage D policy text when `execution_plan_execute` is visible.
2. Does not incorrectly warn that an execution run lacks visible data/order
   evidence when `execution_plan_execute` is the only execution tool.
3. Treats `execution_plan_execute` as mutating trading metadata if the runtime
   receives it.

## 27. Replay Formatter Tests

Add formatter tests for:

1. Completed multi-order plan.
2. Hold/no-op plan.
3. Invalid plan before submission.
4. Blocked plan after one completed order.
5. Confirmation blocker after submit.
6. Final account cash/positions included when available.

The formatter should produce readable text without requiring the user to parse
raw nested JSON first.

## 28. Runtime Safety Tests

Stage C added metadata-based protection against whole-agent retries for
mutating trading tools.

Stage D must preserve this.

Tests should prove:

```text
LUMIBOT_AGENT_MAX_RUN_ATTEMPTS=3
execution_plan_execute metadata mutates_trading=true
=> GoogleADKRuntime._max_attempts_for_request(...) == 1
```

This matters because a whole-agent retry of `execution_plan_execute` could
duplicate an entire plan, not just one order.

## 29. Replay Cache Safety

`execution_plan_execute` mutates trading state. Replay/cached execution must
not re-submit orders just because a cached response is replayed.

The implementation should follow the same replay-cache safety pattern used for
Stage C mutating tools:

- bind tool with `replay_on_cache=true` only if the replay mechanism returns
  the cached output without calling the Python function again
- add a regression test proving cache replay does not execute the plan again

If existing replay cache tests already cover metadata generically, add a
Stage D-specific assertion so future edits do not accidentally remove the
protection.

## 30. Benchmark Validation

Stage D requires both one-day and two-day benchmark validation.

Recommended commands:

```powershell
$env:AI_TRADING_TEAM_MODEL = "openai/gpt-5.6-luna"
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-06 --max-workers 1 --max-run-attempts 1
```

```powershell
$env:AI_TRADING_TEAM_MODEL = "openai/gpt-5.6-luna"
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-09 --max-workers 1 --max-run-attempts 1
```

Validation should inspect:

- benchmark status is `passed`
- execution agent visible tools contain only `execution_plan_execute`
- one-day execution agent makes one model-facing `execution_plan_execute` call
- two-day execution agent makes one model-facing `execution_plan_execute` call
  per trading day with orders
- nested report contains one result per planned order
- sell confirmations occur before later buy orders
- all fills in `trades.csv` match the plan report
- final cash is non-negative
- no `NEGATIVE_CASH_NOT_ALLOWED` blocker appears in the normal benchmark
- no unexplained agent warnings appear
- account curve and tearsheet files are saved

Do not rely on `result.json.backtest_result.total_return` as the sole
performance check because a separate reporting issue can make that aggregate
metric disagree with `stats.csv`.

## 31. Validation Note

After implementation, create:

```text
docs/superpowers/notes/2026-08-11-execution-plan-execute-stage-d-validation.md
```

The note should record:

- test commands and results
- focused lint command and result
- one-day benchmark command/result/artifact path
- two-day benchmark command/result/artifact path
- execution-agent model-facing tool call counts
- final positions/cash
- any warnings or known limitations
- whether Stage D acceptance criteria passed

## 32. Acceptance Criteria

Stage D is accepted when:

1. `execution_plan_execute` exists as a native model-facing built-in tool.
2. It accepts a complete strict `execution_plan`.
3. It rejects loose order fields as the primary API shape.
4. It validates the plan before any order submission.
5. It rejects invalid sequence order instead of silently sorting.
6. It executes orders in listed ascending sequence order.
7. It internally reuses Stage C `orders_execute_order` behavior or equivalent
   shared helpers.
8. It preserves Stage A readiness behavior.
9. It preserves Stage B confirmation behavior.
10. It preserves Stage C per-order `can_continue` behavior.
11. It preserves negative-cash protection.
12. It stops after the first blocker.
13. It marks later orders as skipped after a blocker.
14. It returns a structured plan-level report.
15. It returns nested order-level results.
16. It returns enough internal substep data for replay inspection.
17. The mock quadrant execution-agent visible tool list is reduced to:

```text
execution_plan_execute
```

18. The mock quadrant execution-agent prompt matches the Stage D tool list.
19. The execution-agent prompt no longer mentions Stage C lower-level flow.
20. Unit, failure-path, prompt, strategy, formatter, runtime safety, and replay
    safety tests pass.
21. A one-day benchmark shows one model-facing execution tool call for the
    whole plan.
22. A two-day benchmark shows same-day rebalance still works.
23. The final execution summary matches actual filled orders.
24. No negative cash regression appears.
25. Replay UI remains understandable.

## 33. Risks And Mitigations

### 33.1 Duplicating An Entire Plan

Risk:

Whole-agent retries or replay mistakes could duplicate multiple orders.

Mitigation:

Mark `execution_plan_execute` with `mutates_trading=true`; test that retry env
overrides do not raise attempts above `1`; test replay cache does not call the
tool again.

### 33.2 Hiding Too Much Detail

Risk:

One model-facing call could make it hard to see which order failed.

Mitigation:

Require nested `order_results`, `completed_orders`, `blocked_orders`,
`skipped_orders`, and a replay formatter.

### 33.3 Silent Plan Repair

Risk:

If the tool sorts, repairs, or rewrites the plan, bugs in the upstream planner
become hidden.

Mitigation:

Reject invalid order sequence and invalid fields before submission.

### 33.4 Confusing Prompt Drift

Risk:

The execution prompt could still tell the agent to call Stage C tools even
though those tools are hidden.

Mitigation:

Add prompt tests that require `execution_plan_execute` and reject Stage C tool
names.

### 33.5 Partial Completion Ambiguity

Risk:

If order 1 fills and order 2 blocks, it may be unclear whether the plan
"failed" or "partially succeeded."

Mitigation:

Use `plan_status="blocked"` plus explicit counts and completed/skipped order
lists. Do not introduce a separate `partial` status in the first version.

### 33.6 Stage Boundary Creep

Risk:

The plan-level tool could tempt us to merge portfolio planning and execution.

Mitigation:

Keep `target_portfolio_to_execution_plan` upstream and unchanged. Stage D starts
only after the strict `execution_plan` exists.

## 34. Implementation Plan Guidance

The implementation plan should proceed in small slices:

1. Add failing tool definition tests.
2. Add failing plan validation tests.
3. Add failing success-path and blocked-path behavior tests.
4. Implement `execution_plan_execute` built-in wrapper using Stage C behavior.
5. Add runtime/replay safety tests.
6. Add AgentManager policy updates and tests.
7. Switch mock quadrant execution-agent tool surface and prompts to Stage D.
8. Add strategy prompt/tool-surface tests.
9. Add replay formatter and tests.
10. Run focused regression tests.
11. Run focused lint.
12. Run one-day benchmark.
13. Run two-day benchmark.
14. Record validation note.
15. Request final code review before merge.

The plan should not change macro classification, basket selection, target
portfolio generation, or performance-statistics reporting.

## 35. Spec Self-Review

This spec was reviewed for:

- unresolved markers: none found
- scope: limited to executing an existing strict `execution_plan`
- roadmap alignment: it implements Stage D and preserves A/B/C boundaries
- prompt alignment: execution-agent prompt, task prompt, and forbidden phrases
  are specified
- tool description alignment: model-facing description and non-goals are
  explicit
- UI/trace alignment: nested plan/order/substep inspection is required
- safety: validation, blocker handling, retry safety, replay safety, and
  negative-cash protection are included
- benchmark alignment: one-day and two-day validation requirements are included
- known limitation handling: benchmark performance-statistics reporting is
  explicitly out of scope
