# Decision-Only Approximate Cash Buffer

## Purpose

The growth-execution test currently sends `cash_buffer_pct` to the execution
agent and treats the two-percent reserve as an execution-time hard threshold.
This defeats the reserve's purpose: the reserve is intended to influence
decision-stage whole-share sizing and absorb ordinary market-price movement,
not to make the execution agent reject an otherwise affordable order.

This change makes the approximate two-percent reserve a decision-stage sizing
rule only.

## Goals

- Tell the decision agent to target an approximately two-percent cash reserve
  when calculating buy quantities.
- Remove wording such as "unless a larger cash buffer is needed."
- Give the decision agent a concrete whole-share formula.
- Verify decision-stage quantities deterministically before handoff.
- Remove `cash_buffer_pct` from the normalized execution order.
- Ensure the execution agent never receives or is instructed about the
  two-percent reserve.
- Keep a hard no-negative-cash check before execution.

## Non-Goals

- Do not let the execution agent resize orders.
- Do not automatically repair a bad decision-agent quantity.
- Do not change the selected symbol, side, order type, or order sequence.
- Do not introduce fractional shares.
- Do not change the general LumiBot base prompt for unrelated agents.

## Decision Prompt Rule

Both the decision agent's persistent system prompt and per-run task prompt must
convey this rule without exceptions:

```text
When calculating a buy quantity, target a cash reserve of approximately 2% of
the current portfolio value.

Use the latest available price and choose the largest whole-share quantity
that is expected to leave at least approximately 2% of the portfolio value in
cash.

Calculate the quantity as:

floor(
  (available cash after prior sell orders - 0.02 * current portfolio value)
  / latest price
)

The resulting cash reserve may be slightly higher than 2% because orders use
whole-share quantities. The final quantity must already account for this
reserve. Do not include cash_buffer_pct in the execution order.
```

The wording must not contain:

- "exactly 2%";
- "unless a larger cash buffer is needed";
- any instruction to put `cash_buffer_pct` in the output order.

## Approximate Two-Percent Definition

For a buy order evaluated at the latest available price:

1. Projected cash after all preceding sell orders and this buy must be at least
   `0.02 * current portfolio value`.
2. Adding one more whole share at the same price would make projected cash fall
   below that target.

This defines the largest whole-share quantity that respects the target. The
actual reserve may therefore be slightly above two percent by less than one
share price.

Example:

```text
cash = 100,000
portfolio value = 100,000
price = 229.789993
target reserve = 2,000
quantity = floor((100,000 - 2,000) / 229.789993) = 426
projected cash = 2,109.46
```

Quantity 426 passes. Quantity 427 leaves less than the target. Quantity 425 is
not the largest valid whole-share quantity.

## Handoff Contract

The normalized execution plan sent to the execution agent contains explicit
numeric quantities but no cash-buffer field:

```json
{
  "schema_version": 1,
  "intent": "enter_position",
  "orders": [
    {
      "sequence": 1,
      "symbol": "GLD",
      "side": "buy",
      "quantity_mode": "shares",
      "quantity": 426,
      "order_type": "market",
      "time_in_force": "day"
    }
  ]
}
```

If a model emits a legacy `cash_buffer_pct` field, parsing may accept the input
for compatibility but must remove the field from the normalized plan before
handoff.

The handoff uses an allowlist rather than preserving arbitrary model fields.
Untrusted descriptive fields such as `quantity_source`, free-form constraints,
notes, or alternate reserve names are not forwarded. The only normalized
constraints are the system-owned no-negative-cash and stop-on-block settings.

This concentrated single-position test strategy supports at most one buy order
per plan. A rotation may contain one or more sells followed by that one buy.
All share quantities must be positive whole numbers.

Buy orders may use market orders, where the reserve absorbs ordinary movement,
or order types with a bounded execution price: limit, smart-limit, and
stop-limit. Stop and trailing-stop buys are rejected because their eventual
execution price can exceed the sizing price without a defined upper bound.
All rotation sells must precede the buy in sequence order.

## Validation Responsibilities

### Decision-stage sizing validation

Before invoking the execution agent:

- simulate sell proceeds in sequence order;
- reject sell quantities that exceed the strategy's current long holding;
- require any sell whose proceeds fund a later buy to be a market order;
- evaluate each buy at the relevant explicit or latest price;
- require non-negative cash;
- require the approximate two-percent target;
- require the largest valid whole-share quantity.

An invalid quantity blocks handoff and records a clear decision-plan error.
Order prices are selected according to order type; irrelevant price fields
must not affect affordability calculations.

### Execution affordability validation

The execution boundary checks only:

- projected cash is not negative;
- required prices and order fields are valid;
- normal broker/tool execution constraints.

Falling below two percent after a subsequent price movement is not itself an
execution blocker as long as projected cash remains non-negative.

## Prompt Isolation

The execution agent's system prompt and task prompt must not contain:

- `cash_buffer_pct`;
- `0.02`;
- `2%`;
- instructions to preserve or check a cash reserve.

The execution agent receives only normalized explicit orders.

## Testing

Automated tests must verify:

- decision prompts contain "approximately 2%" and the whole-share formula;
- decision prompts contain no conditional larger-buffer language;
- a 100,000 cash / 100,000 portfolio / 229.789993 price plan accepts 426 shares;
- the same plan rejects 427 shares as below target;
- the same plan rejects 425 shares as not the largest valid quantity;
- normalized orders omit `cash_buffer_pct`, including legacy input;
- execution prompts and execution context contain no buffer field or wording;
- execution affordability allows a positive-cash order even when projected
  cash is below two percent;
- execution affordability still rejects negative cash;
- sell-then-buy rotation uses simulated sell proceeds before evaluating the
  decision-stage target.
- all normalized share quantities are whole numbers;
- nonexistent or oversized sells are rejected;
- a plan with more than one buy is rejected by this single-position strategy;
- market-order validation ignores irrelevant limit/stop fields;
- arbitrary model-provided metadata cannot leak reserve information into the
  execution context.

## Acceptance Criteria

- The decision agent alone receives the approximate two-percent sizing rule.
- The execution agent receives an explicit quantity and no buffer information.
- The decision-stage validator accepts the correct largest whole-share result.
- The execution-stage safety check blocks only negative cash, not a breached
  two-percent reserve.
- Focused pytest and Ruff checks pass.
