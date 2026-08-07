# Structured Execution Plan Between Decision and Execution Agents

## Purpose

The growth execution test proved that `orders_submit_order` can express exits and rotations, but it also exposed a workflow problem: the decision agent currently sends the execution agent a mixed investment report plus action plan. The execution agent then has too much room to re-interpret the investment thesis, delay the entry leg, or treat the plan as advisory rather than executable.

This design changes the handoff contract between `decision_agent` and `execution_agent`. The decision agent should produce a compact, structured execution plan. The execution agent should follow that plan and only reject or pause for execution-level blockers.

## Problem Statement

The current decision output includes fields such as `current_position_assessment`, long reasoning text, `exit_actions`, `entry_actions`, and `do_not_trade_if`. This is useful for human review, but it is not a clean execution interface.

Observed behavior from the 2024-09-05 to 2024-10-21 backtest:

- `decision_agent` produced six `rotate` decisions.
- `execution_agent` successfully submitted sell orders.
- Several rotations were executed as "sell first, buy later" because the execution agent waited for cash to update before placing the replacement order.
- The execution agent did not redo full research, but the verbose plan gave it enough ambiguity to behave conservatively and defer parts of the plan.

The system needs a clearer separation:

- `decision_agent`: decide what account transition should happen.
- `execution_agent`: transform the account into the requested target state using trading tools.

## Goals

- Make the decision-to-execution handoff machine-readable enough for deterministic validation.
- Preserve a short human-readable decision reason without letting it drive execution.
- Make rotations explicitly express sell and buy legs in one plan.
- Allow execution to compute final affordable buy quantities after sell proceeds are available.
- Prevent execution from redoing investment analysis or overriding the chosen target for non-execution reasons.
- Make trace/UI review easier by showing the exact instruction the execution agent received.

## Non-Goals

- Do not add a new exit tool in this change.
- Do not build a full portfolio optimizer.
- Do not discuss final production risk policy.
- Do not require broker-specific APIs.
- Do not guarantee same-bar/same-day fills in every broker or order type.
- Do not make this a general JSON schema validation framework for all agents yet.

## Proposed Handoff Shape

The decision agent should output JSON-like text with two top-level sections:

```json
{
  "decision": {
    "type": "rotate",
    "from": "VNQ",
    "to": "FXI",
    "reason_brief": "FXI overtook VNQ in the current relative-strength ranking."
  },
  "execution_plan": {
    "mode": "sequential_orders",
    "orders": [
      {
        "sequence": 1,
        "action": "sell",
        "symbol": "VNQ",
        "quantity": 1048,
        "quantity_basis": "current_position",
        "order_type": "market"
      },
      {
        "sequence": 2,
        "action": "buy",
        "symbol": "FXI",
        "quantity": "max_affordable_after_prior_sells",
        "cash_buffer_pct": 2,
        "order_type": "market"
      }
    ],
    "execution_constraints": [
      "Do not create negative cash.",
      "Do not substitute another target symbol.",
      "If sequence 1 cannot execute, do not execute sequence 2."
    ]
  }
}
```

The exact output may remain JSON-like text for now because the agent runtime currently passes summaries as text. The prompt should still demand strict field names and simple values.

## Plan Types

The decision agent should choose exactly one decision type:

- `hold`: no orders.
- `buy`: buy one target from cash.
- `rotate`: sell one current holding and buy one replacement.
- `reduce`: sell part of one current holding.
- `close`: sell all of one current holding.

For this test strategy, the primary cases are `hold`, `buy`, and `rotate`.

## Quantity Rules

The decision agent should be concrete when possible:

- For a full exit, use the current share quantity reported by account tools.
- For a partial reduction, use a numeric share quantity.
- For buys funded by cash or sell proceeds, use a quantity mode instead of pretending to know future cash exactly.

Allowed quantity modes:

- numeric whole shares, for explicit sell/reduce quantities.
- `current_position`, for full exit of the named symbol.
- `max_affordable_cash`, for buying from existing cash.
- `max_affordable_after_prior_sells`, for buying after previous sell legs in the same execution plan.

The execution agent is responsible for converting quantity modes into whole-share order quantities using current price, available cash, and a cash buffer.

## Execution Agent Responsibilities

The execution agent should:

- Read `execution_plan.orders` in sequence order.
- Inspect account positions, portfolio cash, open orders, and latest price before each order.
- Submit only the orders required by the plan.
- For sell orders with `quantity_basis="current_position"`, use the current held quantity of that symbol.
- For buy orders with `max_affordable_cash` or `max_affordable_after_prior_sells`, calculate a whole-share quantity from available cash after applying `cash_buffer_pct`.
- Report each attempted order with submitted/blocked status.

The execution agent should not:

- Re-rank the universe.
- Use the research summary to override `decision.to`.
- Substitute a different symbol.
- Convert a `rotate` to `hold` because it disagrees with the investment thesis.
- Add extra orders not present in the plan.

## Valid Execution Blockers

The execution agent may refuse or pause execution only for execution-level reasons:

- Position is missing or smaller than requested.
- Cash is insufficient after applying buffer.
- Latest price is unavailable.
- Existing open orders conflict with the requested action.
- The order tool rejects the order.
- The plan is malformed or missing required fields.
- Executing a later sequence would violate a prior sequence dependency.

If blocked, the execution summary should name the exact blocker and the sequence number.

## Same-Run Rotation Behavior

For a `rotate` plan, the target behavior is:

1. Sell or reduce the current holding.
2. Re-check cash, positions, open orders, and latest target price.
3. Buy the replacement if cash is available and no blocker exists.

This design does not require the system to assume a sell order is filled instantly. It does require the execution agent to make the intended full rotation explicit and to attempt the buy leg when the local account state shows cash is available.

If the backtest broker does not reflect sell proceeds until a later cycle, the execution agent should report:

```text
sequence 2 blocked: sell proceeds are not yet available in account cash
```

That is different from silently deciding not to buy.

## Prompt Changes

### Decision Agent

The decision agent prompt should stop asking for long `current_position_assessment`, `exit_actions`, `entry_actions`, and `do_not_trade_if` as the primary interface.

It should ask for:

- `decision.type`
- `decision.from`
- `decision.to`
- `decision.reason_brief`
- `execution_plan.mode`
- `execution_plan.orders`
- `execution_plan.execution_constraints`

The prompt may allow a brief reason, but it should explicitly say that `execution_plan` is the authoritative section for the execution agent.

### Execution Agent

The execution agent prompt should say:

- Execute `execution_plan.orders` exactly in sequence.
- Use `decision.reason_brief` only for human context, not for re-analysis.
- Do not use `growth_report` or upstream research to override the plan.
- Only reject for execution-level blockers.
- For each order sequence, report whether it was submitted or blocked.

The execution agent context should include only:

- date
- universe if still needed for runtime context
- trading_plan / structured execution plan

It should not receive `growth_report`.

## Trace and UI Expectations

The replay UI should make the structured handoff easy to inspect. No UI implementation is required in this spec, but after implementation the trace should show:

- The full decision summary containing `decision` and `execution_plan`.
- The execution agent input containing only the trading plan, not the growth report.
- The tool calls made for each execution sequence.
- Any blocked sequence message.

## Validation Plan

Unit tests should verify:

- The decision prompt requests `decision` and `execution_plan` fields.
- The execution prompt forbids re-analysis and symbol substitution.
- The execution prompt requires sequence-by-sequence execution reporting.
- The execution agent context does not include `growth_report`.

Backtest validation should verify:

- In a one-day cash-only case, decision emits `buy` and execution submits a buy order.
- In a known rotation window, decision emits `rotate` with sell and buy sequences.
- Execution submits the sell leg.
- If cash becomes available in the same run, execution submits the buy leg.
- If cash does not become available in the same run, execution reports the buy sequence as blocked rather than treating the plan as complete.

## Open Questions

1. Should the execution plan prefer `market` orders for clean behavioral tests, or keep allowing `limit` orders to mimic more realistic behavior?
2. Should `cash_buffer_pct` be fixed by prompt, strategy parameter, or execution agent default?
3. Should malformed decision JSON cause a hard execution skip, or should the execution agent attempt best-effort parsing?

For the next implementation pass, the recommended defaults are:

- Use `market` orders in this experimental test strategy to isolate workflow behavior from limit-order fill timing.
- Use `cash_buffer_pct=2` for buys.
- Treat malformed plans as blocked and submit no orders.

