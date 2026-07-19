# Numeric Execution Plan Quantities

## Purpose

The growth execution test currently lets the decision agent express order sizes with semantic quantity modes such as `current_position`, `full_position`, or `max_affordable_after_prior_sells`. That keeps the execution agent responsible for interpreting sizing language. For the next test, the decision agent should call account and market tools first, then output concrete share quantities so the execution agent receives mechanical order instructions.

## Goals

- Require the decision agent to inspect current account state before final JSON, especially `account_positions` and `account_portfolio`.
- Require the decision agent to use market price evidence before calculating buy quantities.
- Pass only explicit share quantities to the execution agent.
- Reject non-hold plans whose orders do not use `quantity_mode: "shares"` with a positive numeric `quantity`.
- Keep execution-agent context limited to `date` and validated `execution_plan`.
- Preserve trace/UI visibility so the user can verify the decision agent tool calls and the numeric execution plan.

## Non-Goals

- Do not build a new order tool.
- Do not make the execution agent deterministic code-only.
- Do not add stop-loss or take-profit behavior in this pass.
- Do not change the original Dalio example.
- Do not solve long-window rotation profitability.

## Contract

For non-hold plans, each execution order must contain:

```json
{
  "sequence": 1,
  "action": "submit_order",
  "symbol": "QQQ",
  "asset_type": "stock",
  "side": "buy",
  "quantity": 217,
  "quantity_mode": "shares",
  "order_type": "market",
  "time_in_force": "day"
}
```

Allowed quantity behavior:

- `hold` may have `orders: []`.
- All non-hold orders must use `quantity_mode: "shares"`.
- `quantity` must be positive and finite.
- Whole-share trading is preferred. Fractional quantities remain technically parseable only if Lumibot/broker supports them later, but prompts must request whole shares for this test.

Disallowed execution-agent input:

- `quantity_mode: "full_position"`
- `quantity_mode: "current_position"`
- `quantity_mode: "max_affordable_cash"`
- `quantity_mode: "max_affordable_after_prior_sells"`
- Natural-language quantity text such as "sell all" or "max affordable"

## Agent Responsibilities

### Decision Agent

The decision agent owns sizing. Before final JSON it should:

- Call `account_positions` to know existing symbols and quantities.
- Call `account_portfolio` to know cash and portfolio value.
- Call `market_last_price` for symbols it needs to sell or buy.
- Calculate whole-share `quantity` values.
- Output only strict JSON.

For a rotation, it should emit:

1. Sell order with the exact current held share quantity.
2. Buy order with the exact estimated affordable share quantity after the sell, using current cash plus expected sell proceeds.

### Execution Agent

The execution agent should:

- Treat `execution_plan.orders` as the only instruction source.
- Submit exactly the numeric quantities provided.
- Not calculate strategy sizing.
- Still check positions/cash/open orders/latest prices for execution blockers.
- Block if a numeric sell quantity exceeds available shares or a numeric buy quantity is obviously unaffordable.

## Validation

Unit tests must verify:

- Parser accepts valid numeric-share buy and rotation plans.
- Parser rejects non-hold orders with semantic quantity modes.
- Parser rejects missing, zero, negative, NaN, and infinite quantities.
- Decision prompt requires account/portfolio/price tool use before final JSON.
- Execution prompt says orders already contain final numeric quantities.

Backtest validation must run one trading day and verify:

- `execution_agent` receives `execution_plan`.
- Every non-hold execution order has `quantity_mode: "shares"` and numeric `quantity`.
- Execution-agent context does not contain analysis text.
