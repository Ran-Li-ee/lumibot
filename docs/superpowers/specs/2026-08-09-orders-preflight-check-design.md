# Orders Preflight Check Design

Date: 2026-08-09

## 1. Purpose

This spec defines Stage A of the execution tool compression roadmap:
`orders_preflight_check`.

The tool packages the checks that the execution agent currently performs
before submitting one explicit order. It is read-only. It does not submit,
confirm, cancel, modify, or sequence orders.

The immediate goal is not to make execution fully automatic in one tool call.
The goal is to reduce repetitive model-facing pre-order tool calls while
preserving the strict execution contract and replay transparency.

## 2. Relationship To The Roadmap

This spec implements only Stage A from
`docs/superpowers/specs/2026-08-09-execution-tool-compression-roadmap-design.md`.

The roadmap stages are:

```text
A. orders_preflight_check
B. orders_submit_and_confirm_order
C. orders_execute_order
D. execution_plan_execute
```

Stage A is deliberately narrow. Stages B, C, and D are deferred until this
stage passes unit tests, benchmark validation, and replay UI inspection.

## 3. Current Baseline

The current execution flow is already stable enough to submit and confirm
orders, but it requires repeated low-level checks before each order.

For each order, the execution agent often does this pattern:

```text
account_portfolio
account_positions
orders_open_orders
market_last_price(symbol)
orders_submit_order
orders_confirm_order
```

In the latest two-day mock growth/inflation quadrant benchmark, this worked,
but it created a lot of model-facing tool traffic:

- Day 1: preflight-style checks before GLD, SPY, and VGIT orders.
- Day 2: repeated preflight-style checks before VGIT, SPY, VTIP, and GLD
  rebalance orders.

Those checks are useful. The problem is that the LLM has to orchestrate them
manually even though they are mechanical.

## 4. Goals

Stage A should:

1. Add one read-only model-facing tool named `orders_preflight_check`.
2. Package account, position, open-order, and latest-price checks for one
   explicit `execution_plan` order.
3. Return a compact readiness report with clear blockers and warnings.
4. Preserve enough structured detail for the replay UI to explain what was
   checked.
5. Let `orders_submit_order` still enforce its existing safety guards.
6. Let a successful preflight satisfy the existing
   `ORDER_READINESS_REQUIRED` gate for the same agent run and symbol.
7. Keep the execution agent focused on executing the provided
   `execution_plan`, not researching or rewriting it.

## 5. Non-Goals

Stage A does not:

- submit an order
- confirm an order
- cancel an order
- modify an order
- execute an entire order lifecycle
- execute an entire `execution_plan`
- decide which asset should be bought or sold
- calculate target portfolio weights
- change basket definitions
- change macro regime classification
- change `target_portfolio_to_execution_plan`
- change the negative-cash policy
- introduce cash-buffer policy changes
- add broker-specific live trading behavior

## 6. Tool Definition

### 6.1 Name

```text
orders_preflight_check
```

### 6.2 Model-Facing Description

```text
Inspect whether one explicit execution_plan order appears ready to submit.
The tool checks current cash, portfolio value, current position in the symbol,
open orders, and latest price. It does not submit, cancel, modify, or confirm
orders. Use it before orders_submit_order when this tool is available.
```

### 6.3 Mutability

The tool is read-only.

It must not call `strategy.submit_order`, `strategy.cancel_order`, or any other
method that changes account or order state.

### 6.4 Intended Agent

The tool is intended for `execution_agent`.

It may exist globally as a built-in tool, but the first strategy-level rollout
should expose it only to trading execution agents that already receive
`orders_submit_order`.

## 7. Input Schema

The first implementation should support the simple stock/ETF market-order path
used by the current mock growth/inflation quadrant strategy.

Required fields:

```json
{
  "symbol": "GLD",
  "side": "buy",
  "quantity": 105
}
```

Optional fields:

```json
{
  "sequence": 1,
  "asset_type": "stock",
  "order_type": "market",
  "time_in_force": "day"
}
```

Field rules:

| Field | Rule |
|---|---|
| `sequence` | Optional integer copied from `execution_plan.orders[].sequence` when available. |
| `symbol` | Required single tradable symbol string. Comma-separated universes are invalid. |
| `side` | Required. Stage A supports `buy` and `sell` for long stock/ETF execution. |
| `quantity` | Required positive number. Zero or negative quantity is blocked. |
| `asset_type` | Optional. Defaults to `stock`. Stage A accepts `stock` and `us_equity`. |
| `order_type` | Optional. Defaults to `market`. Stage A accepts `market`. |
| `time_in_force` | Optional. Defaults to `day`. Stage A accepts `day`. |

Unsupported fields or unsupported order styles should produce a blocker rather
than causing the model to guess.

## 8. Internal Data Collection

The tool should gather the same information the execution agent currently
checks manually:

1. Account snapshot: cash and portfolio value.
2. Position snapshot: current quantity and value for the requested symbol.
3. Open-order snapshot: open orders, with same-symbol orders highlighted.
4. Price snapshot: latest price for the requested symbol.

Implementation should reuse existing lower-level Python helpers or strategy
methods. It should not make nested ADK/model-facing tool calls.

The output must still expose the internal components as structured fields so
the replay UI can show what happened inside the compressed preflight.

## 9. Readiness Rules

The output has a simple top-level readiness contract:

```json
{
  "readiness": "ready",
  "can_submit": true,
  "blockers": [],
  "warnings": []
}
```

`readiness` is either:

- `ready`
- `blocked`

`can_submit` is a boolean mirror of the readiness decision.

### 9.1 Input Blockers

The tool returns `blocked` if:

- `symbol` is missing or is not a single symbol.
- `side` is missing.
- `side` is unsupported for Stage A.
- `quantity` is missing, zero, negative, non-finite, or non-numeric.
- `asset_type` is unsupported.
- `order_type` is unsupported.
- `time_in_force` is unsupported.

Recommended blocker codes:

```text
INVALID_SYMBOL
INVALID_SIDE
INVALID_QUANTITY
UNSUPPORTED_ASSET_TYPE
UNSUPPORTED_ORDER_TYPE
UNSUPPORTED_TIME_IN_FORCE
```

### 9.2 Price Blockers

The tool returns `blocked` if latest price is missing, non-finite, or less than
or equal to zero.

Recommended blocker code:

```text
PRICE_UNAVAILABLE
```

### 9.3 Sell Blockers

For `side="sell"` in Stage A, the tool should verify that the current long
position quantity is at least the requested sell quantity.

If not, it returns:

```text
INSUFFICIENT_POSITION
```

Stage A does not implement short selling preflight.

### 9.4 Buy Blockers

For `side="buy"` in Stage A, the tool should estimate order value as:

```text
estimated_order_value = quantity * last_price
```

If the estimated order value is greater than current cash, the tool returns:

```text
INSUFFICIENT_CASH_ESTIMATE
```

This preflight check is advisory. `orders_submit_order` remains the final
authority and must still reject orders that would violate the negative-cash
guard at submit time.

### 9.5 Open-Order Blockers

If there is an existing open order for the same symbol, Stage A should block
by default:

```text
OPEN_ORDER_CONFLICT
```

This conservative default avoids sending multiple overlapping orders for the
same symbol while the execution compression path is still young.

Open orders for unrelated symbols should be returned in diagnostics but should
not block by default.

## 10. Output Schema

The returned object should be compact enough for the model but explicit enough
for debugging.

Example ready buy:

```json
{
  "sequence": 1,
  "symbol": "GLD",
  "side": "buy",
  "quantity": 105,
  "asset_type": "stock",
  "order_type": "market",
  "time_in_force": "day",
  "readiness": "ready",
  "can_submit": true,
  "blockers": [],
  "warnings": [],
  "account": {
    "cash": 24479.32,
    "portfolio_value": 100000.0
  },
  "position": {
    "symbol": "GLD",
    "quantity": 108,
    "market_value": 25038.0
  },
  "price": {
    "symbol": "GLD",
    "last_price": 231.83
  },
  "open_orders": {
    "count": 0,
    "same_symbol_count": 0,
    "same_symbol_orders": []
  },
  "estimate": {
    "estimated_order_value": 24342.15,
    "estimated_cash_after_order": 137.17,
    "estimated_position_after_order": 213
  },
  "internal_checks": [
    "account_portfolio",
    "account_positions",
    "orders_open_orders",
    "market_last_price"
  ]
}
```

Example blocked sell:

```json
{
  "sequence": 2,
  "symbol": "VGIT",
  "side": "sell",
  "quantity": 500,
  "asset_type": "stock",
  "order_type": "market",
  "time_in_force": "day",
  "readiness": "blocked",
  "can_submit": false,
  "blockers": [
    {
      "code": "INSUFFICIENT_POSITION",
      "message": "Requested sell quantity 500 exceeds current long position quantity 416."
    }
  ],
  "warnings": [],
  "account": {
    "cash": 137.17,
    "portfolio_value": 100034.29
  },
  "position": {
    "symbol": "VGIT",
    "quantity": 416,
    "market_value": 24194.56
  },
  "price": {
    "symbol": "VGIT",
    "last_price": 58.16
  },
  "open_orders": {
    "count": 0,
    "same_symbol_count": 0,
    "same_symbol_orders": []
  },
  "estimate": {
    "estimated_order_value": 29080.0,
    "estimated_cash_after_order": 29217.17,
    "estimated_position_after_order": -84
  },
  "internal_checks": [
    "account_portfolio",
    "account_positions",
    "orders_open_orders",
    "market_last_price"
  ]
}
```

The exact numeric fields may depend on existing broker/backtest data shape.
The implementation plan should map these examples to the available internal
objects.

## 11. Readiness Guard Compatibility

This is the most important implementation constraint.

`orders_submit_order` currently rejects orders unless the same agent run has
successful calls for:

```text
account_portfolio
account_positions
market_last_price(symbol=<same symbol>)
```

If Stage A hides those lower-level tools from `execution_agent` but does not
teach the readiness guard about `orders_preflight_check`, then submit will fail
with `ORDER_READINESS_REQUIRED`.

Therefore Stage A must do one of these, with option 1 preferred:

1. Update the readiness guard so a successful `orders_preflight_check` for the
   same symbol satisfies account, position, and price readiness.
2. Or record equivalent successful synthetic readiness facts into the current
   agent tool context when `orders_preflight_check` succeeds.

The implementation must not weaken readiness checks globally. A successful
preflight should count only when:

- it happened in the same agent run
- it used the same symbol
- it returned `can_submit=true`
- its account, position, and latest-price checks completed successfully

Blocked preflight results must not satisfy submit readiness.

## 12. Tool Visibility

The target Stage A execution-agent tool list is:

```text
orders_preflight_check
orders_submit_order
orders_confirm_order
```

The lower-level tools should remain implemented and reusable internally:

```text
account_portfolio
account_positions
orders_open_orders
market_last_price
```

But once readiness compatibility is implemented, the mock growth/inflation
quadrant `execution_agent` should not need the lower-level tools directly.

If the first implementation needs a fallback slice, it may temporarily keep the
lower-level tools visible, but the acceptance target is the reduced Stage A tool
surface above.

## 13. Execution-Agent Prompt Changes

The execution-agent prompt should be updated to name the Stage A tool as the
normal pre-order path.

Recommended prompt language:

```text
Before submitting each execution_plan order, call orders_preflight_check for
that exact order when the tool is available. If the preflight result has
can_submit=true, submit that same order with orders_submit_order, then confirm
it with orders_confirm_order before continuing. If preflight returns
can_submit=false, stop execution and report the blocker. Do not use preflight
as investment research and do not change order fields unless the execution_plan
itself is invalid.
```

The prompt should no longer instruct the execution agent to manually call
`account_portfolio`, `account_positions`, `orders_open_orders`, and
`market_last_price` before each order once those tools are hidden.

## 14. Submit Tool Description Compatibility

`orders_submit_order` also has a model-facing description. That description is
sent to the LLM alongside the execution-agent prompt.

Today it tells the model to call:

```text
account_portfolio
account_positions
market_last_price
```

before submitting. If Stage A leaves that wording unchanged while hiding those
tools, the LLM will receive contradictory instructions.

Stage A should update the `orders_submit_order` description to accept the new
path:

```text
Before using this tool, inspect readiness in the current agent run. Prefer
orders_preflight_check when available. Otherwise call account_portfolio,
account_positions, and market_last_price for the same symbol. If readiness has
not been inspected, the order is rejected with ORDER_READINESS_REQUIRED.
```

This keeps the old lower-level path valid for other strategies while making
the Stage A path clear for `execution_agent`.

## 15. Trace And Replay UI Expectations

Stage A should remain inspectable.

The model-facing trace should show:

```text
orders_preflight_check
orders_submit_order
orders_confirm_order
```

For `orders_preflight_check`, the tool output should expose enough structured
fields for the existing replay UI to show:

- requested order
- readiness
- blockers
- warnings
- cash
- portfolio value
- current position quantity
- open order count
- same-symbol open orders
- latest price
- estimated order value
- estimated cash after order
- estimated position after order
- internal check names

No special nested UI is required for Stage A if the compact structured result
is readable in the current tool-call panel.

If the existing human-readable formatter does not explain
`orders_preflight_check`, Stage A should add a small formatter case.

## 16. Testing Plan

The implementation plan should include tests before implementation changes.

### 16.1 Unit Tests

Add focused tests for `orders_preflight_check`:

1. Ready buy with enough cash.
2. Blocked buy with insufficient cash.
3. Ready sell with enough current position quantity.
4. Blocked sell with insufficient current position quantity.
5. Blocked zero quantity.
6. Blocked unsupported order type.
7. Blocked same-symbol open order.
8. Price unavailable returns `PRICE_UNAVAILABLE`.
9. The tool has no trading side effects.

### 16.2 Readiness Guard Tests

Add tests proving:

1. `orders_submit_order` still fails when no readiness inspection happened.
2. A successful `orders_preflight_check` for the same symbol allows
   `orders_submit_order` to pass the `ORDER_READINESS_REQUIRED` gate.
3. A successful `orders_preflight_check` for a different symbol does not count.
4. A blocked `orders_preflight_check` does not count.

### 16.3 Tool Definition And Visibility Tests

Add tests proving:

1. The built-in tool registry exposes `orders_preflight_check`.
2. The model-facing description says the tool is read-only.
3. The mock quadrant `execution_agent` receives:

   ```text
   orders_preflight_check
   orders_submit_order
   orders_confirm_order
   ```

4. The mock quadrant `execution_agent` no longer needs direct
   `account_portfolio`, `account_positions`, `orders_open_orders`, or
   `market_last_price` visibility after Stage A is stable.

### 16.4 Prompt Tests

Add tests proving the execution-agent prompt:

1. Tells the agent to call `orders_preflight_check` before
   `orders_submit_order`.
2. Tells the agent to call `orders_confirm_order` after submit.
3. Tells the agent to stop on `can_submit=false`.
4. Does not tell the agent to manually repeat the four lower-level preflight
   tools when the preflight tool is available.

Add tests proving the `orders_submit_order` tool description:

1. Mentions `orders_preflight_check` as the preferred Stage A readiness path.
2. Does not force the old lower-level path as the only valid path.
3. Still explains that submit will reject without readiness inspection.

### 16.5 Formatter/UI Tests

If a formatter is added, test that:

1. A ready preflight result explains the order is ready.
2. A blocked preflight result names the blocker.
3. Important values such as cash, last price, and estimated order value appear
   in the human-readable explanation.

## 17. Benchmark Validation

After unit tests pass, run short benchmark validation.

### 17.1 One-Day Benchmark

Run one day of the mock growth/inflation quadrant strategy.

Expected result:

- `execution_agent` uses `orders_preflight_check` before each submitted order.
- `orders_submit_order` does not fail with `ORDER_READINESS_REQUIRED`.
- Every submitted order is followed by `orders_confirm_order`.
- Replay UI shows preflight, submit, and confirm in order.

### 17.2 Two-Day Benchmark

Run a two-day benchmark that can produce rebalance behavior.

Expected result:

- Same-day sell and buy sequencing still works.
- Preflight happens before each sell and buy order.
- Confirm still happens after each submit.
- No unexplained order failure appears.
- No negative-cash regression appears.
- The final execution summary matches actual trades.

### 17.3 Expected Tool-Call Shape

Stage A should reduce the number of model-facing preflight tool calls.

Current per-order shape:

```text
account_portfolio
account_positions
orders_open_orders
market_last_price(symbol)
orders_submit_order
orders_confirm_order
```

Target Stage A per-order shape:

```text
orders_preflight_check
orders_submit_order
orders_confirm_order
```

This may not reduce the number of model turns immediately, because the model
may already call several low-level tools in one turn. It should reduce the
number of tool calls, the size of tool-call orchestration, and the chance that
the model forgets one mechanical check.

## 18. Acceptance Criteria

Stage A is accepted when:

1. `orders_preflight_check` exists as a native model-facing built-in tool.
2. It is read-only and has no trading side effects.
3. It returns account, position, open-order, price, estimate, readiness,
   blocker, and warning information.
4. It satisfies the current order readiness gate only when preflight succeeds
   for the same symbol in the same agent run.
5. It does not weaken `orders_submit_order` negative-cash protection.
6. The execution-agent prompt and visible tool list prefer the Stage A path.
7. Unit, readiness guard, tool definition, prompt, and formatter/UI tests pass.
8. A one-day benchmark shows successful preflight-submit-confirm execution.
9. A two-day benchmark shows rebalance execution still works.
10. The replay UI remains understandable without hidden black-box behavior.

## 19. Risks And Mitigations

### 19.1 Readiness Guard Drift

Risk:

`orders_preflight_check` may report ready, but `orders_submit_order` may still
reject because the old readiness guard only recognizes lower-level tools.

Mitigation:

Treat successful same-symbol preflight as an explicit readiness signal and test
that it unlocks submit without lower-level tool visibility.

### 19.2 Hidden Black Box

Risk:

Compression may hide the data that made the order appear ready.

Mitigation:

Return structured `account`, `position`, `open_orders`, `price`, `estimate`,
and `internal_checks` sections. Keep the UI readable.

### 19.3 Stale Preflight Data

Risk:

Price or cash can change between preflight and submit.

Mitigation:

Keep `orders_submit_order` as the final authority. Preflight is a readiness
inspection, not a guarantee that submit must succeed.

### 19.4 Over-Restrictive Open-Order Blocking

Risk:

Blocking same-symbol open orders may be too conservative for future strategies.

Mitigation:

Use the conservative default in Stage A. Revisit in later stages if a strategy
needs more advanced order management.

### 19.5 Prompt And Tool List Mismatch

Risk:

The prompt may tell the model to call old lower-level tools that are no longer
visible.

Mitigation:

Prompt tests must verify the Stage A prompt names `orders_preflight_check` and
does not preserve contradictory manual-preflight instructions.

### 19.6 Submit Tool Description Mismatch

Risk:

The execution-agent prompt may say to use `orders_preflight_check`, while the
`orders_submit_order` description still says only the old lower-level tools can
satisfy readiness.

Mitigation:

Update and test the `orders_submit_order` model-facing description during Stage
A.

## 20. Deferred To Later Stages

Stage A intentionally leaves these for later:

- Stage B: combine submit and confirm.
- Stage C: execute one explicit order end to end.
- Stage D: execute an entire execution plan in one call.
- Nested UI for multi-step execution tools.
- Advanced live-broker order lifecycle handling.
- Partial-fill strategy.
- Retry or waiting behavior inside preflight.
- Any strategy-level change to growth/inflation quadrant allocation.

## 21. Next Step After Review

If this spec is approved, write an implementation plan for Stage A only.

That implementation plan should start with tests, then add the tool, wire the
readiness guard compatibility, update prompt/tool visibility, update formatter
support if needed, and finish with one-day and two-day benchmark validation.
