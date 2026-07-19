# Decision Agent Numeric Order Handoff Design

## Purpose

The growth execution test showed that the decision agent can decide to rotate, but its plan used semantic sizing such as `quantity_mode: full_position`. The parser blocked those plans before the execution agent could act. This design changes the handoff goal: the decision agent should use account tools before writing its final JSON and should pass explicit share quantities to the execution agent.

## Goals

- Require the decision agent prompt to call account tools before producing non-hold orders.
- Require every executable order handed to the execution agent to use `quantity_mode: "shares"` and a positive numeric `quantity`.
- Keep `execution_agent` mechanical: it receives exact order instructions, not sizing semantics.
- Block execution if a non-hold order uses semantic quantity modes such as `current_position`, `full_position`, `max_affordable_cash`, or `max_affordable_after_prior_sells`.
- Preserve trace clarity: UI should show the decision agent had account tools available/called, and execution-agent input should contain exact numeric orders.

## Non-Goals

- Do not create a new deterministic sizing engine in this pass.
- Do not implement portfolio/risk optimization.
- Do not change the original Dalio demo.
- Do not add new broker/order APIs.
- Do not require attached stop-loss or take-profit orders.

## Design

The decision agent remains responsible for converting the growth report and current account state into a strict execution plan. Before it emits the final JSON, its prompt must tell it to call account state tools, especially `account_positions` and `account_portfolio`, and to call latest price tools when buy sizing is needed.

The strict execution plan contract becomes simpler for the execution agent:

```json
{
  "execution_plan": {
    "schema_version": 1,
    "intent": "rotate",
    "orders": [
      {
        "sequence": 1,
        "action": "submit_order",
        "symbol": "QQQ",
        "side": "sell",
        "quantity_mode": "shares",
        "quantity": 217,
        "order_type": "market",
        "time_in_force": "day",
        "quantity_source": "account_positions"
      },
      {
        "sequence": 2,
        "action": "submit_order",
        "symbol": "GLD",
        "side": "buy",
        "quantity_mode": "shares",
        "quantity": 438,
        "order_type": "market",
        "time_in_force": "day",
        "quantity_source": "account_portfolio_and_market_last_price"
      }
    ],
    "constraints": {
      "allow_negative_cash": false,
      "if_any_order_blocked": "stop_remaining_orders"
    }
  }
}
```

`quantity_source` is optional audit metadata. The execution agent does not need it to submit the order, but it helps the developer see why a number was chosen.

## Validation Rules

- `hold` may have no orders.
- Any non-hold plan must have at least one order.
- Every non-hold order must use `quantity_mode: "shares"`.
- Every non-hold order must include a positive finite `quantity`.
- Legacy semantic modes should be rejected by the parser instead of normalized.
- Execution context should still contain only `date` and `execution_plan`.

## Prompt Requirements

The decision-agent system prompt and task prompt must explicitly say:

- Before producing JSON for a non-hold decision, call `account_positions` and `account_portfolio`.
- For buy sizing, call latest price data as needed.
- Final executable orders must contain exact share quantities.
- Do not use `full_position`, `current_position`, `max_affordable_cash`, or `max_affordable_after_prior_sells` in final executable orders.
- For selling all or half of a position, calculate the share quantity from account tool output and write the number.

## Verification

Unit tests should prove:

- The parser accepts share-based rotate orders with explicit quantities.
- The parser rejects semantic quantity modes for non-hold orders.
- The decision prompt tells the agent to call account tools before non-hold JSON.
- The decision prompt forbids semantic quantity modes in final executable orders.
- The execution-agent context still receives only `date` and a validated `execution_plan`.

Backtest validation should run one day and inspect trace:

- `execution_agent` exists.
- Its context has `execution_plan.orders`.
- Each order has `quantity_mode: "shares"` and numeric `quantity`.
- The context does not contain `growth_report`, `trading_plan`, `RESULT:`, or `reason_brief`.
