# Orders Execute Order Stage C Design

Date: 2026-08-11

## 1. Purpose

This spec defines Stage C of the execution tool compression roadmap:
`orders_execute_order`.

Stage C packages one complete explicit order lifecycle into one model-facing
tool call:

```text
orders_execute_order
  -> preflight
  -> submit
  -> confirm
```

The immediate goal is to reduce execution-agent tool calls without weakening
the strict execution contract, order safety checks, confirmation semantics, or
Agent Replay transparency.

Stage C still executes only one order. It does not execute an entire
`execution_plan`. That broader scope remains Stage D.

## 2. Relationship To The Roadmap

This spec implements only Stage C from
`docs/superpowers/specs/2026-08-09-execution-tool-compression-roadmap-design.md`.

The roadmap stages are:

```text
A. orders_preflight_check
B. orders_submit_and_confirm_order
C. orders_execute_order
D. execution_plan_execute
```

Stage A is already implemented on this branch:

```text
execution_agent
  -> orders_preflight_check
  -> orders_submit_order
  -> orders_confirm_order
```

Stage B is already implemented on this branch:

```text
execution_agent
  -> orders_preflight_check
  -> orders_submit_and_confirm_order
```

Stage C changes the preferred execution-agent path to:

```text
execution_agent
  -> orders_execute_order
```

for each order in `execution_plan.orders`, in ascending `sequence` order.

Stage D remains deferred. Stage C must not execute multiple orders in one
model-facing call.

## 3. Current Baseline

The mock Growth / Inflation quadrant strategy currently creates an
`execution_agent` with this Stage B visible tool surface:

```text
orders_preflight_check
orders_submit_and_confirm_order
```

For each order, the execution-agent prompt tells the model to:

1. call `orders_preflight_check` with exact order fields
2. if `can_submit=true`, call `orders_submit_and_confirm_order` with the same
   exact order fields
3. continue only when the combined tool returns `can_continue=true`
4. stop remaining orders when either tool blocks

This flow works and keeps same-day rotation possible. It also keeps Stage A's
readiness gate and Stage B's confirmation behavior visible.

The remaining problem is that every order still requires the LLM to remember a
two-step ceremony. That ceremony is now stable enough to encode as one
deterministic order-execution tool.

## 4. Goals

Stage C should:

1. Add one mutating model-facing tool named `orders_execute_order`.
2. Execute exactly one explicit order from `execution_plan.orders`.
3. Internally perform the same readiness checks as `orders_preflight_check`.
4. Internally submit and confirm the order using the same behavior as
   `orders_submit_and_confirm_order`.
5. Preserve Stage A readiness semantics.
6. Preserve Stage B confirmation and `can_continue` semantics.
7. Preserve negative-cash protection.
8. Return structured nested details for preflight, submit, and confirm.
9. Shrink the mock quadrant `execution_agent` visible tool list to:

   ```text
   orders_execute_order
   ```

10. Update execution-agent prompts so they no longer ask the model to call
    preflight, submit, or confirm tools manually when `orders_execute_order` is
    available.
11. Keep lower-level tools reusable internally by Python code.
12. Keep Agent Replay UI able to explain what happened inside the combined
    order execution.

## 5. Non-Goals

Stage C does not:

- execute more than one order
- execute an entire `execution_plan`
- reorder orders
- skip sequence order
- change symbols, sides, quantities, order type, or time in force
- calculate target weights
- calculate share quantities
- choose replacement assets
- perform investment research
- optimize order slicing
- introduce limit, stop, bracket, fractional, option, short, margin, or
  leveraged order support for the mock strategy
- change macro regime classification
- change basket definitions
- change basket agents
- change `target_portfolio_to_execution_plan`
- change daily buy sizing policy
- change broker-specific live-trading behavior
- hide internal execution steps from trace/UI

## 6. Tool Definition

### 6.1 Name

```text
orders_execute_order
```

### 6.2 Model-Facing Description

Recommended concise description:

```text
Execute exactly one explicit execution_plan order end to end. The tool checks
readiness, submits the order if ready, confirms the submitted order, and
returns whether execution can continue. This tool mutates trading state. It
does not perform research, calculate quantities, change order fields, execute
multiple orders, or execute a full plan. If can_continue=false, stop later
orders and report the blocker.
```

The description is sent to the model, so it should be short but explicit.

It must communicate:

- the tool mutates trading state
- it executes one order only
- it includes readiness checking
- it includes submission and confirmation
- it does not change the order
- `can_continue=false` means stop later orders

### 6.3 Mutability

The tool mutates trading state.

It may submit orders through existing strategy methods, either directly or by
reusing existing bound tool functions. It must be registered with mutating
trading metadata consistent with the existing submit and confirm tools.

### 6.4 Intended Agent

The tool is intended for `execution_agent`.

Research, macro, basket, portfolio-decision, and analysis agents must not
receive it.

## 7. Input Schema

Stage C should mirror the strict single-order fields already emitted inside
`execution_plan.orders`.

Required fields:

```json
{
  "symbol": "GLD",
  "side": "buy",
  "quantity": 105
}
```

Recommended optional fields:

```json
{
  "sequence": 1,
  "asset_type": "stock",
  "order_type": "market",
  "time_in_force": "day",
  "expiration": null,
  "strike": null,
  "right": null,
  "limit_price": null,
  "stop_price": null,
  "stop_limit_price": null,
  "trail_price": null,
  "trail_percent": null,
  "quote_symbol": null,
  "exchange": null,
  "confirmation_max_attempts": 3,
  "confirmation_wait_seconds": null
}
```

Field rules:

| Field | Rule |
|---|---|
| `sequence` | Optional integer copied from `execution_plan.orders[].sequence` when available. |
| `symbol` | Required single tradable symbol string. Comma-separated symbols are invalid. |
| `side` | Required. Stage C must support at least `buy` and `sell` for long stock/ETF execution. |
| `quantity` | Required positive number. Zero, negative, non-finite, or non-numeric values are blocked. |
| `asset_type` | Defaults to `stock`. The mock strategy path supports `stock` and `us_equity`. |
| `order_type` | Defaults to `market`. The mock strategy path supports `market`. |
| `time_in_force` | Defaults to `day`. The mock strategy path supports `day`. |
| `confirmation_max_attempts` | Optional. Defaults to the same practical default used by existing confirmation behavior. |
| `confirmation_wait_seconds` | Optional. Defaults to the same practical default used by existing confirmation behavior. |

Unsupported order styles should return a structured blocker instead of trying
to guess.

## 8. Internal Execution Flow

Stage C should compose existing Stage A and Stage B mechanics rather than
duplicating order safety logic.

Preferred internal flow:

```text
orders_execute_order(args)
  -> run the same preflight logic as orders_preflight_check(args)
  -> if preflight can_submit=false:
       return blocked result with preflight details
  -> run the same submit-and-confirm logic as orders_submit_and_confirm_order(args)
  -> return combined result with preflight and submit-confirm details
```

Implementation may call existing bound tool functions or shared Python
helpers. It must not make nested model calls through ADK. From the LLM's point
of view, this is one tool call.

### 8.1 Readiness Gate

The Stage C implementation must preserve the Stage A readiness gate.

If it reuses `orders_submit_and_confirm_order`, it must ensure the internal
preflight authorizes exactly the same order fields before submit.

If it uses lower-level helpers directly, it must preserve equivalent
protections:

- missing preflight-like readiness blocks submit
- blocked readiness blocks submit
- readiness is exact to symbol, side, quantity, asset type, order type, and
  time in force
- readiness cannot authorize a different order

### 8.2 Submit And Confirm

If preflight is ready, the tool should submit and confirm the same order.

It should reuse Stage B behavior wherever practical:

- same submit field mapping
- same negative-cash guard
- same confirmation retry semantics
- same `can_continue` semantics
- same blocker handling
- same order identifier handling

### 8.3 Stop Behavior

`orders_execute_order` must return `can_continue=false` when:

- input validation blocks the order
- preflight blocks the order
- submit fails
- confirmation fails
- negative-cash protection blocks execution
- the tool cannot determine enough account, position, open-order, or price
  data to safely continue

The execution agent should stop remaining orders when `can_continue=false`.

## 9. Output Contract

The output should be structured, compact, and replay-friendly.

Recommended success shape:

```json
{
  "sequence": 1,
  "symbol": "GLD",
  "side": "buy",
  "quantity": 105,
  "execution_status": "completed",
  "can_continue": true,
  "blockers": [],
  "warnings": [],
  "order": {
    "symbol": "GLD",
    "side": "buy",
    "quantity": 105,
    "asset_type": "stock",
    "order_type": "market",
    "time_in_force": "day"
  },
  "preflight_result": {
    "readiness": "ready",
    "can_submit": true
  },
  "submit_and_confirm_result": {
    "submitted": true,
    "confirmed": true,
    "can_continue": true
  },
  "internal_steps": [
    {
      "step": "preflight",
      "tool": "orders_preflight_check",
      "status": "ready"
    },
    {
      "step": "submit_and_confirm",
      "tool": "orders_submit_and_confirm_order",
      "status": "confirmed"
    }
  ],
  "account_after": {
    "cash": 75642.26,
    "portfolio_value": 100000.0
  }
}
```

Recommended preflight-blocked shape:

```json
{
  "sequence": 1,
  "symbol": "SPY",
  "side": "buy",
  "quantity": 100000,
  "execution_status": "blocked",
  "can_continue": false,
  "blockers": [
    {
      "code": "INSUFFICIENT_CASH_ESTIMATE",
      "message": "Estimated buy value exceeds current cash."
    }
  ],
  "warnings": [],
  "preflight_result": {
    "readiness": "blocked",
    "can_submit": false,
    "blockers": []
  },
  "submit_and_confirm_result": null,
  "internal_steps": [
    {
      "step": "preflight",
      "tool": "orders_preflight_check",
      "status": "blocked"
    }
  ]
}
```

Recommended confirmation-blocked shape:

```json
{
  "sequence": 1,
  "symbol": "GLD",
  "side": "buy",
  "quantity": 105,
  "execution_status": "blocked",
  "can_continue": false,
  "blockers": [
    {
      "code": "CONFIRMATION_FAILED",
      "message": "Submitted order could not be confirmed."
    }
  ],
  "preflight_result": {
    "readiness": "ready",
    "can_submit": true
  },
  "submit_and_confirm_result": {
    "submitted": true,
    "confirmed": false,
    "can_continue": false
  },
  "internal_steps": [
    {
      "step": "preflight",
      "tool": "orders_preflight_check",
      "status": "ready"
    },
    {
      "step": "submit_and_confirm",
      "tool": "orders_submit_and_confirm_order",
      "status": "blocked"
    }
  ]
}
```

### 9.1 Status Vocabulary

Top-level `execution_status` should use a small vocabulary:

```text
completed
blocked
```

If later live trading needs richer states, those should be added in a future
spec. Stage C should keep the first implementation simple.

### 9.2 `can_continue`

`can_continue` means:

```text
It is safe for the execution_agent to proceed to the next execution_plan order.
```

It does not mean the overall portfolio plan is optimal. It only refers to
order-execution flow.

## 10. Tool Visibility

The target Stage C mock quadrant `execution_agent` visible tool list is:

```text
orders_execute_order
```

These tools should normally be hidden from the mock quadrant `execution_agent`
after Stage C:

```text
orders_preflight_check
orders_submit_and_confirm_order
orders_submit_order
orders_confirm_order
account_portfolio
account_positions
orders_open_orders
market_last_price
```

Lower-level tools may remain available:

- to other strategies
- inside Python helper code
- in tests
- in future debug modes

They should not remain visible to the mock quadrant Stage C execution agent
because their presence encourages the older multi-call path.

## 11. Prompt Changes

Prompt changes are part of Stage C, not a follow-up.

### 11.1 Execution-Agent System Prompt

The minimal execution-agent base prompt should remain focused on execution.
It should not add research language or portfolio-decision language.

Any general instruction that says "before submitting, inspect account,
positions, open orders, and latest price" should remain conceptually true, but
the Stage C prompt must make clear that `orders_execute_order` performs those
checks internally.

Recommended Stage C execution role text:

```text
Execution role: execute only the provided execution_plan using the listed
order execution tool. For each order in ascending sequence order, call
orders_execute_order exactly once with the exact order fields. Do not manually
preflight, submit, confirm, query account state, query open orders, or query
latest prices when orders_execute_order is available. The tool performs
readiness checks, submission, and confirmation internally. Continue to the next
order only when the tool returns can_continue=true. If it returns
can_continue=false, stop remaining orders and report the blocker. Do not
perform investment research, do not change order fields, and do not call
lower-level order tools when orders_execute_order is available.
```

### 11.2 Execution-Agent Task Prompt

The task prompt used when calling `execution_agent.run(...)` should be updated
from Stage B wording:

```text
For each order, call orders_preflight_check ...
If preflight allows it, call orders_submit_and_confirm_order ...
```

to Stage C wording:

```text
Execute the provided execution_plan in sequence order. For each order, call
orders_execute_order exactly once with the exact order fields. Stop if any
orders_execute_order result returns can_continue=false.
```

### 11.3 Execution Tool Policy Prompt

The manager-level execution tool policy should recognize
`orders_execute_order`.

Recommended policy addition:

```text
orders_execute_order executes one explicit execution_plan order end to end:
readiness check, submission, and confirmation. It does not research, calculate
quantities, change order fields, execute multiple orders, or execute a full
plan.
```

When `orders_execute_order` is visible, the policy should not recommend
manual `orders_preflight_check` or `orders_submit_and_confirm_order` calls for
the same order.

### 11.4 Prompt Tests

Tests should prove that the mock quadrant Stage C prompt:

- mentions `orders_execute_order`
- says to call it once per order
- says to use exact order fields
- says to stop on `can_continue=false`
- does not tell the agent to call `orders_preflight_check`
- does not tell the agent to call `orders_submit_and_confirm_order`
- does not expose lower-level order tools in the visible tool list

## 12. Tool Description Changes

Stage C needs one new model-facing tool description.

Existing descriptions for Stage A and Stage B may remain for other strategies
and internal tests. They should not be modified to claim Stage C behavior.

### 12.1 `orders_execute_order` Description Requirements

The description must say:

- executes exactly one explicit order
- mutates trading state
- performs readiness, submit, and confirm
- does not change order fields
- does not execute a full plan
- `can_continue=false` means stop later orders

### 12.2 Description Length

The description should be concise. The tool schema and prompt should do the
rest. Avoid turning the tool description into a long tutorial.

## 13. Implementation Placement

Stage C should follow the existing built-in order tool pattern in
`lumibot/components/agents/builtins.py`.

Expected implementation areas:

```text
lumibot/components/agents/builtins.py
  - add ORDERS_EXECUTE_ORDER_DESCRIPTION
  - add _bind_execute_order
  - add BuiltinTools.orders.execute()
  - include the new tool in BuiltinTools.all() if appropriate

lumibot/components/agents/manager.py
  - update execution tool policy text
  - update replay/mutating order tool allowlists if needed
  - update cached replay safety handling if needed

lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py
  - expose only orders_execute_order to execution_agent
  - update execution_agent system prompt
  - update execution_agent task prompt

lumibot/components/agents/replay_ui/formatters.py
  - add human-readable formatter for orders_execute_order
```

Exact file boundaries may be adjusted during implementation if current code
has a better local pattern, but the implementation should stay scoped to
Stage C.

## 14. Reuse Strategy

Stage C should not duplicate complex logic from Stages A and B.

Preferred reuse order:

1. Reuse existing preflight binding/helper logic for readiness.
2. Reuse existing submit-and-confirm binding/helper logic for submit and
   confirmation.
3. Preserve existing readiness-token mechanics if reusing Stage B.
4. Add only the wrapper logic needed to combine them into one model-facing
   call.

If implementation discovers that Stage A/B logic is too tightly bound to
model-facing wrappers, the plan should first extract small internal helper
functions, then build Stage C on top of those helpers.

The implementation should not create a second independent version of:

- cash checks
- position checks
- open-order checks
- price checks
- negative-cash checks
- submit logic
- confirm retry logic

## 15. Replay And Trace Requirements

Stage C must remain inspectable.

At minimum, `orders_execute_order` output must include:

- the exact order fields received
- preflight result
- submit-and-confirm result, if submission was attempted
- blockers and warnings
- `can_continue`
- internal step names

The Agent Replay UI should be able to render a human-readable explanation like:

```text
Executed order 1 buy 106 GLD. Preflight was ready with cash 100000.00 and
latest price 229.79. The order was submitted and confirmed. Execution may
continue.
```

For blocked cases:

```text
Order 2 buy 100000 SPY was blocked before submit because estimated buy value
exceeded current cash. No order was submitted. Execution must stop.
```

### 15.1 Internal Substep Visibility

The first Stage C UI can expose internal substeps through nested JSON and a
human-readable formatter. It does not need a new graphical nested substep UI
unless the implementation naturally supports it.

However, the structured output must be good enough for a later UI enhancement
to show:

```text
orders_execute_order
  preflight
  submit
  confirm
```

### 15.2 Boundary Trace Expectations

The model-facing trace will show one tool call:

```text
orders_execute_order
```

The tool result should carry enough nested detail to explain the internal
preflight, submit, and confirm work.

If existing boundary-trace infrastructure supports internal tool events, the
implementation may record richer internal substeps. That is a bonus, not the
minimum Stage C acceptance gate.

## 16. Replay Cache And Safety

Stage C is a mutating trading tool. Replay behavior must be safe.

If cached replay invokes `orders_execute_order`, it must not submit live or
backtest orders again. It should return cached output through the same replay
safety behavior used for current mutating tools.

Tests should cover that Stage C does not bypass existing replay cache safety
rules.

If replay-cache support requires a mutating-tool allowlist, add
`orders_execute_order` to that allowlist.

## 17. Error And Blocker Handling

Stage C should return structured blockers instead of raising raw exceptions
where practical.

Recommended blocker code families:

```text
INVALID_SYMBOL
INVALID_SIDE
INVALID_QUANTITY
UNSUPPORTED_ASSET_TYPE
UNSUPPORTED_ORDER_TYPE
UNSUPPORTED_TIME_IN_FORCE
ACCOUNT_UNAVAILABLE
POSITIONS_UNAVAILABLE
OPEN_ORDERS_UNAVAILABLE
PRICE_UNAVAILABLE
OPEN_ORDER_CONFLICT
INSUFFICIENT_CASH_ESTIMATE
INSUFFICIENT_POSITION
ORDER_READINESS_REQUIRED
ORDER_SUBMIT_FAILED
NEGATIVE_CASH_NOT_ALLOWED
CONFIRMATION_FAILED
CONFIRMATION_ERROR
```

The tool may reuse existing blocker codes from Stage A and Stage B.

Top-level blockers should be easy for the LLM and UI to understand without
digging through nested JSON.

## 18. Expected Tool-Call Shape

Stage B per-order shape:

```text
orders_preflight_check
orders_submit_and_confirm_order
```

Target Stage C per-order shape:

```text
orders_execute_order
```

If a benchmark produces three orders, Stage C should normally reduce the
execution-agent order tool calls from six to three.

If a benchmark produces seven orders, Stage C should normally reduce the
execution-agent order tool calls from fourteen to seven.

The exact number of model turns may vary by model, but the visible execution
tool name should follow the Stage C path.

## 19. Testing Requirements

Stage C needs tests in several categories.

### 19.1 Tool Definition Tests

Tests should prove:

1. `BuiltinTools.orders.execute()` exists.
2. The bound tool name is `orders_execute_order`.
3. `BuiltinTools.all()` includes `orders_execute_order` if that matches the
   existing built-in tool pattern.
4. The tool description says it executes exactly one explicit order end to
   end.
5. The tool metadata marks it as mutating and replayable like other order
   tools.

### 19.2 Success Path Tests

Tests should prove:

1. A valid buy order runs preflight internally.
2. If preflight is ready, the tool submits the order.
3. The submitted order is confirmed.
4. The result includes `execution_status="completed"`.
5. The result includes `can_continue=true`.
6. The result includes nested `preflight_result`.
7. The result includes nested `submit_and_confirm_result`.
8. The result includes internal step labels.

### 19.3 Blocked Preflight Tests

Tests should prove:

1. Invalid quantity blocks before submit.
2. Insufficient cash blocks before submit.
3. Insufficient position blocks a sell before submit.
4. Open-order conflict blocks before submit.
5. Price unavailable blocks before submit.
6. Blocked results include `can_continue=false`.
7. Blocked results do not submit an order.

### 19.4 Submit And Confirmation Failure Tests

Tests should prove:

1. Submit exceptions return structured blockers.
2. Negative-cash protection still blocks execution.
3. Confirmation failure returns `can_continue=false`.
4. Confirmation exceptions return structured blockers.
5. A failed order stops later execution by prompt contract.

### 19.5 Tool Visibility Tests

Mock quadrant tests should prove:

1. `execution_agent` receives only `orders_execute_order` for Stage C.
2. Non-execution agents do not receive `orders_execute_order`.
3. `execution_agent` no longer receives Stage B lower-level visible tools:

   ```text
   orders_preflight_check
   orders_submit_and_confirm_order
   ```

4. `execution_agent` does not receive the older low-level tools:

   ```text
   orders_submit_order
   orders_confirm_order
   account_portfolio
   account_positions
   orders_open_orders
   market_last_price
   ```

### 19.6 Prompt Tests

Tests should prove:

1. The execution-agent prompt says to call `orders_execute_order`.
2. It says to call it once per order.
3. It says to use exact order fields.
4. It says to stop on `can_continue=false`.
5. It does not instruct the agent to call `orders_preflight_check`.
6. It does not instruct the agent to call
   `orders_submit_and_confirm_order`.
7. It does not mention manual submit/confirm as the normal path.

### 19.7 Formatter And UI Tests

Tests should prove:

1. A successful `orders_execute_order` result has a clear human explanation.
2. A preflight-blocked result explains that no submit was attempted.
3. A confirmation-blocked result explains that submit happened but confirmation
   blocked continuation.
4. The formatter surfaces nested preflight and submit-confirm details.

### 19.8 Replay Cache Safety Tests

Tests should prove:

1. Cached replay of `orders_execute_order` does not submit a fresh order.
2. Cached replay preserves the same output shape.
3. Blocked internal preflight does not authorize a submit during replay.

## 20. Benchmark Validation

Stage C requires benchmark validation before it is accepted.

### 20.1 One-Day Benchmark

Run a one-day mock quadrant benchmark that creates initial deployment orders.

Expected result:

- `execution_agent` receives `orders_execute_order`.
- `execution_agent` does not receive or call Stage B tools.
- Each execution-plan order is executed by one model-facing
  `orders_execute_order` call.
- Each order result includes internal preflight and submit-confirm details.
- Final cash is non-negative.
- Final positions match filled orders.
- Replay UI explains each high-level order execution.

### 20.2 Two-Day Benchmark

Run a two-day mock quadrant benchmark that can exercise rebalance behavior.

Expected result:

- Same-day sell and buy sequencing still works.
- Earlier sell orders complete before later buy orders are attempted.
- Each order has one model-facing `orders_execute_order` call.
- No negative-cash regression appears.
- Any blocker stops remaining orders.
- Final execution summary matches actual trades.

### 20.3 Artifact Notes

After implementation, record:

- benchmark command
- benchmark artifact path
- selected model
- dates tested
- number of execution-agent model-facing order tool calls
- final orders submitted
- final cash and positions
- replay UI observations

These notes may live in a validation note under `docs/superpowers/notes/`.

## 21. Acceptance Criteria

Stage C is accepted when:

1. `orders_execute_order` exists as a native model-facing built-in tool.
2. It executes exactly one explicit order.
3. It internally performs preflight, submit, and confirm.
4. It preserves Stage A readiness behavior.
5. It preserves Stage B confirmation and `can_continue` behavior.
6. It preserves negative-cash protection.
7. It does not change order fields.
8. It returns structured nested `preflight_result`.
9. It returns structured nested `submit_and_confirm_result` when submit is
   attempted.
10. It returns `can_continue=true` only when the order is confirmed enough to
    proceed.
11. It returns `can_continue=false` when preflight, submit, or confirmation
    blocks execution.
12. The mock quadrant execution-agent visible tool list is reduced to:

    ```text
    orders_execute_order
    ```

13. The mock quadrant execution-agent prompt matches the Stage C tool list.
14. Unit, failure-path, prompt, strategy, formatter, and replay-safety tests
    pass.
15. A one-day benchmark shows the Stage C tool being used successfully.
16. A two-day benchmark shows same-day rebalance still works.
17. Replay UI remains understandable.

## 22. Risks And Mitigations

### 22.1 Hidden Execution Details

Risk:

One tool call could hide preflight, submit, and confirm details.

Mitigation:

Require nested structured output and a replay formatter before acceptance.

### 22.2 Duplicated Safety Logic

Risk:

Stage C could duplicate Stage A/B safety checks and drift from them.

Mitigation:

Reuse existing Stage A/B helpers or bound functions. If helper extraction is
needed, do that first and add regression tests.

### 22.3 Readiness Gate Bypass

Risk:

The wrapper might submit without equivalent preflight readiness.

Mitigation:

Test that blocked preflight cases never submit. If Stage B readiness tokens
are reused, test that exact-order matching still holds.

### 22.4 Prompt And Tool Surface Drift

Risk:

The execution prompt may ask for `orders_execute_order`, but the visible tool
list may still show Stage B tools, encouraging old behavior.

Mitigation:

Add prompt/tool-list tests in the same Stage C implementation.

### 22.5 Over-Compression Into Stage D

Risk:

While implementing Stage C, it may be tempting to pass the whole
`execution_plan` and execute all orders.

Mitigation:

Reject plan-level input in Stage C. Implement only one explicit order per
call. Stage D gets its own future spec.

### 22.6 Live Broker Differences

Risk:

Backtests usually confirm quickly. Live brokers may have slower, partial, or
ambiguous fills.

Mitigation:

Reuse existing confirmation semantics now. Defer broker-specific live states
to a later live-trading spec.

## 23. Deferred To Stage D

Stage C deliberately defers:

- `execution_plan_execute`
- validating the whole plan in one tool
- sequencing all orders inside one tool call
- one-call multi-order rebalance execution
- plan-level aggregate report
- plan-level partial completion handling
- plan-level retry strategy
- plan-level UI tree

Stage C should create a clean per-order building block that Stage D can later
compose.

## 24. Implementation Plan Guidance

The implementation plan should proceed in small slices:

1. Add failing tests for `orders_execute_order` definition and success path.
2. Add the built-in tool wrapper using existing Stage A/B behavior.
3. Add blocked preflight and confirmation failure tests.
4. Update mock quadrant tool visibility.
5. Update execution-agent system and task prompts.
6. Add prompt/tool-list tests.
7. Add replay formatter support.
8. Add replay cache safety tests if needed.
9. Run focused tests.
10. Run one-day benchmark.
11. Run two-day benchmark.
12. Record validation notes.

The plan should avoid implementing Stage D behavior during Stage C.

## 25. Spec Self-Review

This spec was reviewed for:

- placeholder language: no unresolved placeholder markers remain
- scope: limited to one explicit order, not a full execution plan
- prompt alignment: Stage C prompt, task, and policy changes are included
- tool description alignment: model-facing description requirements are
  included
- UI/trace alignment: nested details and formatter requirements are included
- safety: readiness, negative cash, confirmation, replay cache, and blocker
  behavior are included
- roadmap consistency: Stage D responsibilities are explicitly deferred
