# Strict Execution Plan Contract Between Decision and Execution Agents

## Purpose

The previous structured handoff made the decision agent describe `decision` and `execution_plan`, but the execution agent still received the decision agent's raw text summary. That raw summary can include JSON-like content, `RESULT:` prose, and explanatory notes. The execution agent therefore still has to interpret text.

This design makes the handoff strict for the growth execution test strategy: the decision agent may produce a mixed text response, but the strategy must extract and validate a machine-readable execution plan before calling the execution agent. The execution agent should receive only the validated execution plan object plus minimal runtime context, not the investment report.

## Goals

- Ensure `execution_agent` receives a strict execution plan object, not a free-form text report.
- Remove `decision.reason_brief`, `RESULT:`, and explanatory analysis from the execution-agent context.
- Validate the decision output before any trading-enabled agent is called.
- Block execution safely if the decision output cannot be parsed or misses required fields.
- Keep the change scoped to `AITradingTeamGrowthExecutionTestStrategy`.
- Preserve trace/UI visibility: the UI should show a clean `execution_plan` object in execution-agent input.

## Non-Goals

- Do not build a general schema framework for all agents.
- Do not remove the global `RESULT:` rule from the base system prompt.
- Do not make execution fully deterministic code-only.
- Do not add new broker APIs or new order tools.
- Do not implement production-grade risk policy.
- Do not implement attached stop-loss/take-profit order placement in this pass unless the plan explicitly includes supported fields.

## Current Problem

The latest one-day backtest showed this execution-agent context:

```json
{
  "date": "2024-09-05",
  "trading_plan": "{\"decision\":{...},\"execution_plan\":{...}}\nRESULT: I chose ...",
  "universe": ["SPY", "QQQ", "IWM", "TLT", "IEF", "TIP", "GLD", "DBC", "VNQ", "UUP", "FXI", "EEM"]
}
```

This is not strict enough. The execution agent should not parse a natural-language string or read explanatory text. It should receive an already validated object such as:

```json
{
  "date": "2024-09-05",
  "execution_plan": {
    "schema_version": 1,
    "intent": "enter_position",
    "orders": [
      {
        "sequence": 1,
        "action": "submit_order",
        "symbol": "GLD",
        "asset_type": "stock",
        "side": "buy",
        "quantity": null,
        "quantity_mode": "max_affordable_cash",
        "cash_buffer_pct": 2,
        "order_type": "market",
        "limit_price": null,
        "stop_price": null,
        "stop_limit_price": null,
        "trail_price": null,
        "trail_percent": null,
        "time_in_force": "day"
      }
    ],
    "constraints": {
      "allow_negative_cash": false,
      "if_any_order_blocked": "stop_remaining_orders"
    }
  }
}
```

## Strict Execution Plan Schema

The validated execution plan passed to `execution_agent` must be a Python `dict` with:

- `schema_version`: integer, currently `1`.
- `intent`: one of `hold`, `enter_position`, `rotate`, `reduce_position`, `close_position`.
- `orders`: list of order objects.
- `constraints`: dict.

Each order object must include:

- `sequence`: positive integer.
- `action`: currently `submit_order`.
- `symbol`: non-empty string.
- `asset_type`: default `stock` if omitted by the model.
- `side`: `buy` or `sell`.
- `quantity`: positive number or `null`.
- `quantity_mode`: one of `shares`, `current_position`, `max_affordable_cash`, `max_affordable_after_prior_sells`.
- `cash_buffer_pct`: number, default `2` for buy orders if omitted.
- `order_type`: one of `market`, `limit`, `stop`, `stop_limit`, `trailing_stop`, `smart_limit`.
- Optional price fields: `limit_price`, `stop_price`, `stop_limit_price`, `trail_price`, `trail_percent`.
- `time_in_force`: default `day` if omitted.

Validation rules:

- `orders` must be sorted by `sequence` before being passed to execution.
- `hold` may have an empty orders list.
- Non-hold intents must have at least one order.
- `quantity_mode="shares"` requires numeric `quantity`.
- `quantity_mode!="shares"` may use `quantity=null`.
- `side="buy"` defaults `cash_buffer_pct` to `2` when missing.
- `side="sell"` defaults `cash_buffer_pct` to `0` when missing.
- Unsupported `order_type`, `side`, `action`, or `quantity_mode` blocks execution.

## Decision Output Parsing

The decision agent should be prompted to return only a JSON object, but the strategy must not trust prompt compliance.

The strategy should:

1. Take `decision.summary`.
2. Extract the first balanced JSON object from the text.
3. Parse it with `json.loads`.
4. Accept either:
   - a full object containing `execution_plan`, or
   - the `execution_plan` object itself.
5. Validate and normalize the plan.
6. Pass only the normalized `execution_plan` object to `execution_agent`.

If any step fails, the strategy should not call `execution_agent`. Instead it should record or print a concise blocked message for trace/log visibility. This avoids giving a trading-enabled agent malformed instructions to guess from.

## Execution Agent Context

The execution-agent context should include only:

```json
{
  "date": "YYYY-MM-DD",
  "execution_plan": { "...validated object..." }
}
```

It should not include:

- `growth_report`
- `trading_plan` raw text
- `decision.reason_brief`
- `RESULT:` text
- `universe`, unless a later execution-only reason requires it

The execution-agent prompt should say that its only job is to execute the provided `execution_plan.orders`; it should not evaluate why the trade exists.

## Prompt Expectations

The decision-agent prompt should prefer a pure JSON object:

```text
Return only one valid JSON object. Do not include markdown, RESULT text, comments, or prose after the JSON.
```

The runtime may still append or encourage `RESULT:` through the base prompt, so parsing and validation remain the source of truth.

The execution-agent prompt should not mention investment analysis. It should be operational:

- Read `execution_plan.orders`.
- For each order in sequence order, check positions/cash/open orders/latest price.
- Resolve `quantity_mode` to a whole-share quantity.
- Call `orders_submit_order`.
- Report each sequence as submitted or blocked.
- Do not add, remove, replace, or reorder orders.

## Trace/UI Expectations

After this change, the replay UI should show execution-agent input where:

- `execution_plan` is an object, not a string.
- There is no `trading_plan` key.
- There is no `RESULT:` text in execution-agent context.
- There is no `growth_report` or natural-language investment analysis in execution-agent context.

The decision-agent trace may still show the raw mixed output; that is acceptable because parsing happens between decision and execution.

## Validation Plan

Unit tests should verify:

- A mixed decision summary containing JSON plus `RESULT:` is parsed into a clean plan.
- The execution-agent context contains `execution_plan` and does not contain `trading_plan`, `growth_report`, `RESULT:`, or `reason_brief`.
- Invalid decision output blocks execution and does not call `execution_agent`.
- Orders are sorted by `sequence`.
- Defaults are applied for `asset_type`, `cash_buffer_pct`, and `time_in_force`.
- Existing prompt tests still pass or are updated to the strict contract.

Backtest validation should verify:

- A one-day cash-only run completes.
- The execution-agent trace input contains a clean `execution_plan` object.
- The execution-agent still submits the intended order when the plan is valid.

