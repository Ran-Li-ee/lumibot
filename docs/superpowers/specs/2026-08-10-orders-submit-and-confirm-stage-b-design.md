# Orders Submit And Confirm Stage B Design

Date: 2026-08-10

## 1. Purpose

This spec defines Stage B of the execution tool compression roadmap:
`orders_submit_and_confirm_order`.

Stage B packages the two mechanical steps that currently happen after
preflight for every order:

```text
orders_submit_order
orders_confirm_order
```

into one model-facing tool call:

```text
orders_submit_and_confirm_order
```

The immediate goal is to reduce execution-agent rounds without reducing
safety, traceability, or the strict execution contract.

Stage B does not execute a whole order lifecycle by itself. The execution
agent still calls `orders_preflight_check` first. If preflight approves the
exact order, the execution agent then calls `orders_submit_and_confirm_order`
for that same order.

## 2. Relationship To The Roadmap

This spec implements only Stage B from
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

Stage B changes the preferred execution-agent path to:

```text
execution_agent
  -> orders_preflight_check
  -> orders_submit_and_confirm_order
```

Stages C and D remain deferred. This spec must not absorb their scope.

## 3. Current Baseline

The mock Growth / Inflation quadrant strategy currently creates an execution
agent with these visible tools:

```text
orders_preflight_check
orders_submit_order
orders_confirm_order
```

The execution-agent prompt says to:

1. call `orders_preflight_check` for each order
2. if `can_submit=true`, call `orders_submit_order`
3. immediately call `orders_confirm_order`
4. continue only if confirmation returns `can_continue=true`

This flow works and keeps same-day rebalance possible, but each order still
requires the model to remember the submit-then-confirm ceremony.

Stage A added important safety mechanics that Stage B must preserve:

- `orders_preflight_check` is read-only.
- A successful preflight records one exact order-readiness token.
- `orders_submit_order` consumes that readiness token.
- A blocked preflight does not authorize submit.
- Submit still performs negative-cash protection.
- `orders_confirm_order` retries internally and returns `can_continue`.

Stage B should reuse these existing mechanics rather than creating parallel
logic.

## 4. Goals

Stage B should:

1. Add one mutating model-facing tool named
   `orders_submit_and_confirm_order`.
2. Submit exactly one explicit order from `execution_plan.orders`.
3. Confirm that same submitted order before returning.
4. Reuse the existing submit and confirm behavior as much as practical.
5. Preserve the Stage A readiness gate.
6. Preserve the negative-cash guard.
7. Preserve confirmation retry and `can_continue` semantics.
8. Return structured submit and confirm details for replay UI inspection.
9. Shrink the mock quadrant execution-agent visible tool list to:

   ```text
   orders_preflight_check
   orders_submit_and_confirm_order
   ```

10. Update execution-agent prompts so they no longer ask the model to call
    `orders_submit_order` and `orders_confirm_order` separately when the
    combined tool is available.

## 5. Non-Goals

Stage B does not:

- perform preflight internally as the normal path
- replace `orders_preflight_check`
- execute more than one order
- execute an entire `execution_plan`
- decide order sequence
- change symbols, sides, quantities, order type, or time in force
- calculate target weights
- calculate share quantities
- perform investment research
- choose a better asset
- add fractional shares
- add broker-specific live order-routing behavior
- introduce limit, stop, or bracket order support for the mock strategy
- change macro regime classification
- change basket selection
- change `target_portfolio_to_execution_plan`

Stage B starts after a strict execution plan already exists and after a
successful preflight has approved the exact order.

## 6. Tool Definition

### 6.1 Name

```text
orders_submit_and_confirm_order
```

### 6.2 Model-Facing Description

Recommended description:

```text
Submit one explicit execution_plan order and confirm that same submitted order
before returning. Use this after orders_preflight_check returns can_submit=true
for the same order. This tool mutates trading state. It does not perform
research, calculate quantities, change order fields, or execute more than one
order. If the result has can_continue=false, stop later orders and report the
blocker.
```

The description should be concise because it is sent to the model. It should
make these points explicit:

- this is a mutating trading tool
- it submits and confirms one order
- it requires prior preflight readiness
- it does not modify the order
- `can_continue=false` means stop later orders

### 6.3 Mutability

The tool mutates trading state.

It may create, submit, and confirm an order through existing strategy methods.
It must be registered with mutating-trading metadata consistent with
`orders_submit_order` and `orders_confirm_order`.

### 6.4 Intended Agent

The tool is intended for `execution_agent`.

Other research, macro, basket, and portfolio-decision agents should not receive
it.

## 7. Input Schema

Stage B should mirror the existing `orders_submit_order` input shape for
single-leg orders, with optional confirmation context.

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
| `symbol` | Required single tradable symbol string. |
| `side` | Required. Stage B must support at least `buy` and `sell` for the mock quadrant strategy. |
| `quantity` | Required positive number. |
| `asset_type` | Defaults to `stock`. |
| `order_type` | Defaults to `market` for the mock strategy path. |
| `time_in_force` | Defaults to `day` for the mock strategy path. |
| `confirmation_max_attempts` | Optional. Defaults to the same practical default used by `orders_confirm_order`. |
| `confirmation_wait_seconds` | Optional. Defaults to existing confirmation wait behavior. |

Stage B should accept the same optional order fields as `orders_submit_order`
where practical so the new tool can be a drop-in replacement for submit plus
confirm.

The mock quadrant strategy should continue to generate market/day stock or ETF
orders. Non-market styles are not part of the benchmark acceptance path.

## 8. Internal Flow

The normal internal flow is:

```text
orders_submit_and_confirm_order
  -> validate and normalize input
  -> call existing submit path for the exact order
  -> extract submitted order identifier
  -> call existing confirm path for that identifier
  -> return combined submit and confirm result
```

The implementation may do this by:

1. extracting shared lower-level helpers from `orders_submit_order` and
   `orders_confirm_order`, or
2. binding the existing tools and calling their functions internally.

The implementation plan should choose the least invasive option that avoids
duplicating trading logic.

Stage B should not call model-facing tools through ADK as nested model calls.
The combined tool is one model-facing call that performs internal Python work.

## 9. Readiness And Safety Requirements

### 9.1 Preflight Readiness

`orders_submit_and_confirm_order` must preserve the exact same readiness
requirements as `orders_submit_order`.

That means:

- if no successful same-run preflight exists for the exact order, the combined
  tool must fail before submitting
- if preflight approved a different symbol, side, quantity, asset type, order
  type, or time in force, the combined tool must fail before submitting
- if preflight was blocked, the combined tool must fail before submitting
- a successful preflight should authorize only one matching combined submit
  attempt

The tool should rely on the existing readiness guard and token consumption
where possible. It should not create a second readiness system.

### 9.2 Negative-Cash Guard

For buy-like stock or ETF orders, the combined tool must preserve the existing
negative-cash guard from `orders_submit_order`.

If submit would make cash negative and negative cash is not explicitly enabled,
the combined tool must fail before confirmation and return a clear failure.

### 9.3 Confirmation Gate

If submit succeeds, the combined tool must call the existing confirmation path
for the returned identifier.

It must return `can_continue=true` only when confirmation succeeds according
to the existing `orders_confirm_order` checks.

If confirmation returns `can_continue=false`, the combined tool must return
`can_continue=false` and preserve confirmation warnings.

### 9.4 No Order Mutation

The combined tool must submit exactly the fields supplied by the
execution-agent call.

It must not:

- change quantity
- change side
- change symbol
- change order type
- choose a different time in force
- convert a failed buy into a smaller buy
- convert a failed sell into a hold

If fields are invalid, it should fail clearly rather than repair them.

## 10. Output Schema

The output should be structured enough for the model and replay UI.

Recommended successful result:

```json
{
  "sequence": 1,
  "symbol": "GLD",
  "side": "buy",
  "quantity": 105,
  "asset_type": "stock",
  "order_type": "market",
  "time_in_force": "day",
  "submitted": true,
  "confirmed": true,
  "can_continue": true,
  "confirmation_status": "fill",
  "identifier": "bt_7",
  "submit_result": {
    "order": {
      "identifier": "bt_7",
      "symbol": "GLD",
      "side": "buy",
      "quantity": 105,
      "status": "fill"
    }
  },
  "confirm_result": {
    "identifier": "bt_7",
    "confirmed": true,
    "can_continue": true,
    "confirmation_status": "fill",
    "attempt_count": 1,
    "account_snapshot": {}
  },
  "internal_steps": [
    "orders_submit_order",
    "orders_confirm_order"
  ],
  "warnings": [],
  "blockers": []
}
```

Recommended submit failure result:

```json
{
  "sequence": 1,
  "symbol": "GLD",
  "side": "buy",
  "quantity": 999999,
  "submitted": false,
  "confirmed": false,
  "can_continue": false,
  "confirmation_status": null,
  "identifier": null,
  "submit_result": null,
  "confirm_result": null,
  "internal_steps": [
    "orders_submit_order"
  ],
  "warnings": [],
  "blockers": [
    {
      "code": "SUBMIT_FAILED",
      "message": "NEGATIVE_CASH_NOT_ALLOWED: ..."
    }
  ]
}
```

Recommended confirmation failure result:

```json
{
  "sequence": 1,
  "symbol": "GLD",
  "side": "buy",
  "quantity": 105,
  "submitted": true,
  "confirmed": false,
  "can_continue": false,
  "confirmation_status": "open_after_retries",
  "identifier": "bt_7",
  "submit_result": {
    "order": {}
  },
  "confirm_result": {
    "identifier": "bt_7",
    "confirmed": false,
    "can_continue": false,
    "warnings": [
      "Order bt_7 remained active after 3 confirmation attempts. Do not submit dependent orders."
    ]
  },
  "internal_steps": [
    "orders_submit_order",
    "orders_confirm_order"
  ],
  "warnings": [
    "Order bt_7 remained active after 3 confirmation attempts. Do not submit dependent orders."
  ],
  "blockers": [
    {
      "code": "CONFIRMATION_FAILED",
      "message": "Order was submitted but not confirmed. Stop later orders."
    }
  ]
}
```

The exact status strings should follow existing LumiBot order payloads and
`orders_confirm_order` output.

## 11. Error Handling

The combined tool should return structured failure data where practical.

It may raise `ValueError` for programming-style invalid input, consistent with
existing built-in tools, but model-facing execution failures should ideally be
returned as structured blockers when possible.

Important cases:

| Case | Expected Behavior |
|---|---|
| Missing readiness | Do not submit. Return or raise `ORDER_READINESS_REQUIRED`. |
| Preflight token mismatch | Do not submit. Return or raise `ORDER_READINESS_REQUIRED`. |
| Invalid quantity | Do not submit. Return or raise clear invalid quantity error. |
| Negative cash | Do not submit. Return or raise `NEGATIVE_CASH_NOT_ALLOWED`. |
| Submit returns no identifier | Treat as submit failure or confirmation blocker. Do not continue. |
| Confirm not found | Return `confirmed=false`, `can_continue=false`. |
| Confirm partial fill | Return `confirmed=false`, `can_continue=false`. |
| Confirm terminal rejection/cancel/error | Return `confirmed=false`, `can_continue=false`. |
| Confirm open after retries | Return `confirmed=false`, `can_continue=false`. |

The execution-agent prompt must tell the model to stop remaining orders if the
combined tool returns `can_continue=false` or a blocker.

## 12. Tool Visibility

The target Stage B mock quadrant `execution_agent` tool list is:

```text
orders_preflight_check
orders_submit_and_confirm_order
```

These should be hidden from the mock quadrant execution agent after Stage B:

```text
orders_submit_order
orders_confirm_order
```

The lower-level tools should remain implemented and available for:

- internal reuse by the combined tool
- older strategies
- debugging
- later migration paths

Stage B should not globally remove `orders_submit_order` or
`orders_confirm_order`.

## 13. Execution-Agent Prompt Changes

The mock quadrant execution-agent system prompt should say:

```text
Execution role: execute only the provided execution_plan using the listed order
tools. For each order in sequence, call orders_preflight_check with the exact
order fields. If preflight returns can_submit=true, call
orders_submit_and_confirm_order with that same order. Continue to the next
order only when the combined tool returns can_continue=true. If preflight
returns can_submit=false or the combined tool returns can_continue=false, stop
remaining orders and report the blocker. Do not perform investment research,
do not change order fields, and do not call lower-level submit or confirm
tools when the combined tool is available.
```

The task prompt should make the same path explicit but should stay shorter:

```text
Execute the provided execution_plan in sequence order. For each order, preflight
the exact order, then submit and confirm it with
orders_submit_and_confirm_order if preflight allows it. Stop if any preflight
or confirmation blocks continuation.
```

Prompt tests should verify there is no leftover instruction requiring:

```text
orders_submit_order
orders_confirm_order
```

as separate model-visible calls in the mock quadrant Stage B prompt.

## 14. Tool Description Compatibility

The new tool description must make it clear that it replaces the separate
submit-then-confirm ceremony when available.

The existing `orders_submit_order` and `orders_confirm_order` descriptions
should not need large changes, because they remain valid for other strategies.

However, if those descriptions are still sent to the mock quadrant execution
agent by mistake, that is a Stage B tool-visibility bug. Tests should catch
this by asserting that the mock quadrant execution-agent tool surface contains
only:

```text
orders_preflight_check
orders_submit_and_confirm_order
```

## 15. Trace And Replay UI Expectations

Stage B must remain inspectable.

The model-facing trace should show:

```text
orders_preflight_check
orders_submit_and_confirm_order
```

The combined tool output must include:

- submitted flag
- confirmed flag
- can_continue flag
- identifier
- submit_result
- confirm_result
- internal_steps
- warnings
- blockers

This is the minimum Stage B transparency requirement.

If the boundary trace infrastructure can record internal substeps without
large new architecture, Stage B should also record submit and confirm as
internal child steps. If that is not practical in this slice, structured
`submit_result` and `confirm_result` in the combined tool output are accepted
as the first Stage B UI surface.

The replay UI should add a human-readable formatter for
`orders_submit_and_confirm_order`.

The formatter should explain:

- the submitted order
- the order identifier
- whether confirmation succeeded
- confirmation status
- attempt count
- whether execution can continue
- first blocker or warning if blocked

No new nested UI component is required for Stage B unless the implementation
plan finds the current formatter insufficient.

## 16. Replay Cache Requirements

`orders_submit_and_confirm_order` is mutating and should be replayable in the
same way as the existing submit and confirm tools.

Its metadata should include:

```json
{
  "kind": "builtin",
  "replay_on_cache": true,
  "mutates_trading": true
}
```

Cache replay must not bypass readiness or negative-cash protection.

If replay re-executes a cached combined tool call, it should:

1. use the current shared replay context
2. require the matching readiness token
3. submit exactly once
4. confirm the submitted order
5. return the same combined output shape

Blocked preflight must not authorize cached combined submit.

## 17. Testing Plan

The implementation plan should write tests before implementation changes.

### 17.1 Built-In Tool Tests

Add tests proving:

1. `BuiltinTools.orders.submit_and_confirm()` exists.
2. `BuiltinTools.all()` includes `orders_submit_and_confirm_order`.
3. The bound tool name is `orders_submit_and_confirm_order`.
4. The description says it submits and confirms one explicit order.
5. The metadata marks it as mutating and replayable.

### 17.2 Success Path Tests

Add tests proving:

1. A successful preflight for a matching order allows
   `orders_submit_and_confirm_order`.
2. The combined tool submits the order.
3. The combined tool confirms the same returned identifier.
4. The returned result has:

   ```text
   submitted=true
   confirmed=true
   can_continue=true
   submit_result
   confirm_result
   internal_steps
   ```

5. The readiness token is consumed after successful combined submit.

### 17.3 Readiness Guard Tests

Add tests proving:

1. Calling the combined tool without preflight fails before submit.
2. A blocked preflight does not authorize the combined tool.
3. A preflight for a different symbol does not authorize the combined tool.
4. A preflight for a different side does not authorize the combined tool.
5. A preflight for a different quantity does not authorize the combined tool.
6. A preflight for a different order type or time in force does not authorize
   the combined tool.
7. One successful preflight authorizes only one combined submit-and-confirm
   attempt.

### 17.4 Failure Path Tests

Add tests proving:

1. Negative-cash buy fails before submit or before any confirmation attempt.
2. If submit fails, confirm is not called.
3. If submit returns no usable identifier, the combined result has
   `submitted=true` or `submitted=false` according to actual behavior, but
   `confirmed=false` and `can_continue=false`.
4. If confirmation returns `confirmed=false`, the combined result surfaces the
   confirmation warning and has `can_continue=false`.
5. Partial fill, rejected, canceled, expired, and open-after-retries statuses
   all block continuation.

### 17.5 Strategy Tool Surface Tests

Update mock quadrant tests proving:

1. `execution_agent` receives:

   ```text
   orders_preflight_check
   orders_submit_and_confirm_order
   ```

2. `execution_agent` no longer receives:

   ```text
   orders_submit_order
   orders_confirm_order
   ```

3. Non-execution agents do not receive the combined tool.

### 17.6 Prompt Tests

Update prompt tests proving:

1. The execution-agent prompt tells the model to call preflight first.
2. The execution-agent prompt tells the model to call
   `orders_submit_and_confirm_order` after successful preflight.
3. The execution-agent prompt tells the model to continue only when
   `can_continue=true`.
4. The prompt does not tell the model to separately call `orders_submit_order`
   and `orders_confirm_order`.

### 17.7 Formatter And UI Tests

Add formatter tests proving:

1. A successful combined result explains submitted and confirmed order details.
2. A confirmation-blocked result explains that continuation is blocked.
3. A submit failure result explains the first blocker.

If loader or browser tests depend on explicit submit/confirm names for the
mock quadrant strategy, update them to the Stage B trace shape.

## 18. Benchmark Validation

After tests pass, run short benchmark validation.

### 18.1 One-Day Benchmark

Run one trading day of the mock Growth / Inflation quadrant strategy.

Expected result:

- `execution_agent` uses `orders_preflight_check`.
- `execution_agent` uses `orders_submit_and_confirm_order`.
- `execution_agent` does not use `orders_submit_order` or
  `orders_confirm_order` as separate model-visible tool calls.
- Every submitted order is confirmed inside the combined tool result.
- No unexplained tool warnings or order failures appear.
- Replay UI can explain the combined tool result.

### 18.2 Two-Day Benchmark

Run a two-day benchmark that can exercise rebalance behavior.

Expected result:

- Same-day sell and buy sequencing still works.
- Each order has one preflight call and one combined submit-confirm call.
- Later buy orders happen only after earlier sell confirmations return
  `can_continue=true`.
- No negative-cash regression appears.
- Final execution summary matches actual trades.

### 18.3 Expected Tool-Call Shape

Stage A per-order shape:

```text
orders_preflight_check
orders_submit_order
orders_confirm_order
```

Target Stage B per-order shape:

```text
orders_preflight_check
orders_submit_and_confirm_order
```

If a benchmark produces seven orders, Stage B should normally reduce the
execution-agent order tool calls from twenty-one to fourteen.

The exact number of model turns may vary by model, but the visible tool names
should follow the Stage B pattern.

## 19. Acceptance Criteria

Stage B is accepted when:

1. `orders_submit_and_confirm_order` exists as a native model-facing built-in
   tool.
2. It submits and confirms exactly one explicit order.
3. It requires matching successful preflight readiness before submit.
4. It consumes readiness so one preflight cannot authorize repeated submits.
5. It preserves negative-cash protection.
6. It returns structured `submit_result` and `confirm_result`.
7. It returns `can_continue=true` only when confirmation succeeds.
8. It returns `can_continue=false` when submit or confirmation blocks
   execution.
9. The mock quadrant execution-agent visible tool list is reduced to:

   ```text
   orders_preflight_check
   orders_submit_and_confirm_order
   ```

10. The mock quadrant execution-agent prompt matches the Stage B tool list.
11. Unit, readiness, failure-path, prompt, strategy, and formatter tests pass.
12. A one-day benchmark shows the combined tool being used successfully.
13. A two-day benchmark shows same-day rebalance still works.
14. Replay UI remains understandable.

## 20. Risks And Mitigations

### 20.1 Readiness Bypass

Risk:

The combined tool might accidentally bypass the Stage A readiness gate.

Mitigation:

Reuse the existing submit path or readiness guard. Add tests proving missing,
blocked, mismatched, and reused preflight all fail before submit.

### 20.2 Duplicated Submit Logic

Risk:

Duplicating `orders_submit_order` logic could diverge from negative-cash
protection or future submit behavior.

Mitigation:

Prefer extracting shared helpers or invoking the existing bound submit function
internally. Avoid copy-pasting large submit logic.

### 20.3 Hidden Confirmation Failure

Risk:

The combined tool might return a friendly top-level success while confirmation
actually failed.

Mitigation:

Top-level `confirmed` and `can_continue` must mirror the confirmation result.
Tests must cover confirmation-blocked paths.

### 20.4 Prompt And Tool Surface Drift

Risk:

The prompt may ask for the new combined tool while the visible tool list still
shows old tools, or vice versa.

Mitigation:

Add prompt and strategy tool-surface tests in the same Stage B change.

### 20.5 Replay Becomes Too Opaque

Risk:

Combining tools could make Agent Replay less useful.

Mitigation:

Return `submit_result`, `confirm_result`, `internal_steps`, warnings, and
blockers. Add a formatter for the combined tool before accepting the stage.

### 20.6 Live Broker Semantics

Risk:

Backtests may fill immediately while live brokers may leave orders pending.

Mitigation:

Stage B keeps the existing confirmation semantics and `can_continue=false`
stop rule. More advanced live-broker behavior remains a future design topic.

## 21. Deferred To Later Stages

Deferred to Stage C:

- one tool that performs preflight, submit, and confirm for one order
- one model-facing call per order

Deferred to Stage D:

- one tool that executes the entire strict execution plan
- plan-level blocker reporting
- plan-level nested replay UI

Deferred beyond this roadmap:

- partial-fill execution strategy
- cash sweeping
- tax-aware order sequencing
- broker-specific smart routing
- portfolio-level execution optimization
- live trading authorization rules

## 22. Implementation Planning Notes

The implementation plan should start with tests.

Recommended implementation order:

1. Add failing tests for the new built-in tool and success path.
2. Add failing readiness and failure-path tests.
3. Add the binder and registry entry.
4. Reuse or extract submit and confirm helpers.
5. Add the replay UI formatter.
6. Update mock quadrant execution-agent tools and prompts.
7. Update prompt/tool-surface tests.
8. Run targeted tests.
9. Run one-day and two-day benchmark validation.
10. Record benchmark artifact IDs and notable trace observations.

The plan should avoid implementing Stage C or Stage D behavior during Stage B.
