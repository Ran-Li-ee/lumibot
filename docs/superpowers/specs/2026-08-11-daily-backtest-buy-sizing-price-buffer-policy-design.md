# Daily Backtest Buy Sizing Price and Buffer Policy

## Status

Design spec. Approved conceptually by the user on 2026-08-11. This document is intended to drive a later implementation plan and implementation pass.

## Background

The mock Growth / Inflation Quadrant strategy currently routes portfolio-level targets through `target_portfolio_to_execution_plan`, which converts target symbols and target weights into a strict `execution_plan`.

During one-day daily backtests, we observed a cash shortfall even though the generated plan estimated non-negative cash. Investigation found a price basis mismatch:

- the planner estimated buy quantities from `strategy.get_last_price(symbol)`;
- in Yahoo daily backtesting, that path returned a prior completed daily bar open price for the relevant symbols;
- market/day fills then executed at the current backtest day's open price.

For example, on the 2024-09-05 daily backtest:

| Symbol | Planner sizing price | Matched historical price | Market fill price | Matched historical price |
| --- | ---: | --- | ---: | --- |
| GLD | 229.789993 | 2024-09-04 open | 232.720001 | 2024-09-05 open |
| SPY | 550.200012 | 2024-09-04 open | 550.890015 | 2024-09-05 open |
| TLT | 97.879997 | 2024-09-04 open | 99.339996 | 2024-09-05 open |

This caused the planner to overestimate affordable share counts. The actual market fills spent more than the planner estimate and produced negative cash.

## Problem

The current planning behavior has two issues:

1. Daily backtest buy sizing uses a stale and unintuitive price basis: prior daily open.
2. Buy sizing spends essentially all estimated available cash, leaving no room for expected mismatch between sizing price and actual market fill price.

This is not an LLM reasoning problem. The LLM already delegates share calculation to `target_portfolio_to_execution_plan`. The fix belongs in that deterministic planner tool, with prompt and tool-description updates to preserve the division of responsibility.

## Goals

1. Use a more reasonable daily backtest sizing price.
2. Apply a small buy sizing buffer so the planner does not intentionally spend 100% of estimated available cash.
3. Keep execution simple: execution agent follows the generated plan and does not recalculate sizing.
4. Preserve market/day order execution.
5. Make the sizing basis and buffer visible in tool results and replay UI diagnostics.

## Non-Goals

This spec does not:

- change the global semantics of `YahooDataBacktesting.get_last_price`;
- add intraday or minute-level data;
- prevent market fills from exceeding the 98% buffered estimate;
- introduce broker-specific live-trading quote logic;
- add advanced volatility-based buffers;
- let the LLM manually calculate share quantities;
- make the execution agent responsible for sizing, cash buffers, or price estimates.

## Design Summary

For buy quantity estimation in `target_portfolio_to_execution_plan`:

```text
sizing_price = previous completed daily close when available
buy sizing budget = buy target amount * 0.98
quantity = floor(buy sizing budget / sizing_price)
```

The 2% buffer is only a sizing-budget reduction. It is not a hard execution price cap.

After the plan is generated:

- the execution plan still contains market/day orders;
- actual fills may cost more than the buffered estimate;
- fills are allowed to exceed the 98% budget if the broker/backtest engine fills the order at a higher price;
- the negative cash guard remains the final deterministic protection against unacceptable cash states.

## Detailed Behavior

### Price Basis

The planner should distinguish between:

- `sizing_price`: price used to estimate share quantity;
- `estimated_fill_price`: optional future concept, not required in this spec;
- `actual_fill_price`: price recorded by the broker/backtest engine after execution.

For this implementation, `sizing_price` should be:

1. previous completed daily close, when it can be obtained safely;
2. `strategy.get_last_price(symbol)` as a documented fallback if previous completed daily close is unavailable;
3. a hard error if neither source can provide a finite positive price.

The initial implementation should prefer a local helper owned by `target_portfolio_to_execution_plan`, rather than changing global `get_last_price` behavior.

The helper should return a small structured object or equivalent fields:

```json
{
  "price": 230.429993,
  "source": "previous_completed_daily_close",
  "datetime": "2024-09-04"
}
```

Allowed `source` values:

- `previous_completed_daily_close`;
- `strategy_last_price_fallback`.

### Buy Buffer

Add a default:

```text
BUY_SIZING_BUFFER_PCT = 0.02
```

For buy candidates, apply:

```text
effective_buy_value = desired_buy_value * (1 - BUY_SIZING_BUFFER_PCT)
spendable = min(effective_buy_value, projected_cash)
quantity = floor(spendable / sizing_price)
```

The buffer applies only to buy orders.

It must not apply to:

- sell quantities;
- current position value calculations;
- target portfolio weights;
- execution agent behavior;
- preflight or submit-and-confirm hard caps;
- actual fill validation.

### Existing Holdings

Current position values should continue to use the planner's chosen price basis consistently for diagnostics. If implementation separates valuation price and buy sizing price later, that must be explicit in the tool result. For this spec, keep the change minimal and use the planner's deterministic price map as the basis for current-vs-target diagnostics.

### Market Orders

The generated orders remain:

```json
{
  "order_type": "market",
  "time_in_force": "day"
}
```

The system should not switch to limit orders to solve this problem.

### Negative Cash

The planner's estimate should avoid negative projected cash under its own sizing assumptions. However, actual fills can still differ. Existing negative-cash checks should remain in place as final safety.

If a market fill is higher than the buffered estimate but cash remains non-negative, that is acceptable and should not be treated as an error.

If a market fill still causes negative cash despite the buffer, that is a real blocker to investigate later. It may require a larger buffer or a more execution-aware estimated fill price, but that is outside this spec.

## Tool Output Requirements

`target_portfolio_to_execution_plan` should expose enough diagnostics for UI inspection.

Each relevant `current_vs_target` row should include these exact additional fields:

```json
{
  "symbol": "GLD",
  "current_quantity": 0,
  "sizing_price": 230.429993,
  "sizing_price_source": "previous_completed_daily_close",
  "sizing_price_datetime": "2024-09-04",
  "current_value": 0,
  "target_weight": 0.25,
  "target_value": 25000,
  "effective_buy_target_value": 24500,
  "buy_sizing_buffer_pct": 0.02,
  "planned_side": "buy",
  "planned_quantity": 106,
  "reason_code": "buy_new_target"
}
```

The output must clearly answer:

- what price was used for sizing;
- where that price came from;
- what raw target value was requested;
- what buffered buy value was used;
- what quantity was generated.

The `cash_projection` object should include:

```json
{
  "cash_before": 100000,
  "estimated_sell_proceeds": 0,
  "estimated_buy_cost": 98765.43,
  "cash_after_estimate": 1234.57,
  "negative_cash_allowed": false,
  "buy_sizing_buffer_pct": 0.02
}
```

## Prompt Requirements

### Portfolio Decision Agent

The portfolio decision agent prompt should continue to say:

- choose target symbols and target weights from macro and basket reports;
- call `target_portfolio_to_execution_plan`;
- do not manually calculate share quantities;
- do not manually calculate cash usage;
- copy the planner tool's `execution_plan` exactly.

It should not contain detailed instructions about how to apply the 2% buffer. That logic belongs in the tool.

A concise addition is acceptable:

```text
The planner tool owns daily backtest buy sizing, including its price basis and buy sizing buffer.
```

### Execution Agent

The execution agent prompt should continue to say:

- execute only the provided `execution_plan`;
- call `orders_preflight_check`;
- then call `orders_submit_and_confirm_order`;
- stop on blockers;
- do not change order fields.

It should not mention the 2% buffer. The execution agent should not reason about sizing policy.

## Tool Description Requirements

Update the `target_portfolio_to_execution_plan` tool description so the LLM knows the tool owns sizing:

```text
Convert a target portfolio into a strict market/day execution_plan. The tool reads current positions, cash,
portfolio value, and deterministic sizing prices from the strategy. In daily backtests, buy sizing uses the
previous completed daily close when available and applies a default 2% buy sizing buffer. The buffer reduces
planned buy quantity only; it is not a hard execution price cap. Do not manually edit the execution_plan.
```

The order tools should not need major description changes. If any wording implies they perform share sizing, remove that implication.

## Trace and UI Requirements

The existing trace pipeline should capture the enhanced tool output automatically because the diagnostics are returned by the tool.

The replay UI does not need a new screen for this spec. It should simply render the new fields wherever tool outputs are already displayed.

Future UI polish can separately make these fields more readable, for example:

- "Sizing price";
- "Sizing price source";
- "Buy buffer";
- "Buffered buy value";
- "Estimated cash after plan".

## Test Requirements

Implementation should add or update unit tests for `target_portfolio_to_execution_plan`.

Required test cases:

1. Initial all-cash buy plan applies the 2% buy buffer.
   - Example: 100,000 portfolio, 50/25/25 target weights, prices 100/100/50.
   - Expected buy values should be approximately 49,000 / 24,500 / 24,500 before whole-share rounding.

2. Buy buffer does not apply to sells.
   - Existing full-exit and reduce-overweight behavior should remain unchanged.

3. Buy buffer does not block valid market fills above the buffered estimate.
   - This can be validated at the planner/unit-test level by ensuring no hard cap is encoded in the execution plan.

4. Tool output includes sizing diagnostics.
   - Assert presence of `sizing_price`, `sizing_price_source`, and `buy_sizing_buffer_pct`.

5. Daily price basis regression.
   - Add a deterministic fake strategy or data object that proves the planner uses previous completed daily close when available, not prior daily open.

6. Existing execution-plan validation still passes.
   - The portfolio decision agent must still be required to call the planner tool.
   - The execution plan must still match the planner tool output exactly.

## Backtest Verification

After implementation, run a one-day daily backtest on the same scenario that exposed the bug:

```text
2024-09-05
AITradingTeamMockGrowthInflationQuadrantStrategy
```

Expected observations:

- portfolio decision agent calls `target_portfolio_to_execution_plan`;
- tool output shows previous completed daily close as sizing price when available;
- tool output shows 2% buy sizing buffer;
- planned quantities are smaller than the old no-buffer quantities;
- execution agent still submits market/day orders through preflight and submit-and-confirm;
- actual fills may exceed the buffered estimate;
- cash should remain non-negative in the observed one-day scenario.

## Risks

### Prior Close Still Cannot Guarantee Non-Negative Cash

A 2% buffer may not cover extreme overnight gaps. This is acceptable for this spec. If later backtests still go negative, we can consider dynamic buffers, estimated open pricing, or larger asset-specific buffers.

### Global Price Semantics Remain Inconsistent

This spec avoids changing `YahooDataBacktesting.get_last_price`. That means other strategies may still see the old behavior. This is intentional to keep the fix scoped to the mock quadrant execution planner.

### Diagnostics May Grow

The additional fields will make tool output larger. The fields are small and useful for debugging, so this is acceptable.

## Acceptance Criteria

The feature is complete when:

1. `target_portfolio_to_execution_plan` uses previous completed daily close for daily backtest buy sizing when available.
2. Buy quantity calculations use an approximately 2% budget buffer.
3. The buffer is not treated as a hard execution cap.
4. Sell logic is unchanged.
5. Portfolio decision and execution prompts preserve the responsibility split:
   - planner tool calculates quantities;
   - execution agent executes exact orders.
6. Tool output exposes sizing price source and buffer diagnostics.
7. Unit tests cover the policy.
8. A one-day validation backtest confirms the original negative-cash scenario is avoided under the new policy.
