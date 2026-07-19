# Decision Cash Buffer Tolerance and One Retry

## Purpose

The decision agent should target an approximately two-percent cash reserve, but
whole-share rounding and small arithmetic errors should not block an otherwise
usable order. The existing validator requires the exact largest whole-share
quantity that leaves at least two percent, which rejected a VNQ plan leaving
1.91572 percent cash.

This change introduces an acceptance band and one bounded decision-stage retry.

## Goals

- Keep two percent as the decision agent's initial sizing target.
- Accept a buy plan when projected cash is between one and three percent of
  current portfolio value, inclusive.
- Immediately hand an accepted plan to the execution agent.
- Retry the decision agent once when, and only when, the projected reserve is
  outside the band.
- Give the retry enough numerical feedback to correct the quantity.
- Stop after the retry if it is still invalid.
- Keep all target, tolerance, error, and retry information out of the execution
  agent's prompt and context.
- Preserve hard no-negative-cash and existing order-contract checks.

## Non-Goals

- Do not automatically alter the decision agent's quantity in Python.
- Do not retry malformed JSON, missing-tool evidence, nonexistent positions,
  oversells, invalid order types, or other structural errors.
- Do not allow more than one buy order in this concentrated single-position
  strategy.
- Do not change asset selection or trade direction during validation.
- Do not relax the execution-stage no-negative-cash rule.

## Acceptance Band

The first decision prompt continues to say:

```text
Target a cash reserve of approximately 2% of the current portfolio value.
```

For each buy plan, after simulating preceding eligible market sells:

```text
projected_cash = simulated_cash - quantity * sizing_price
projected_cash_ratio = projected_cash / current_portfolio_value
```

The quantity is accepted when:

```text
0.01 <= projected_cash_ratio <= 0.03
```

Both boundaries are inclusive. The validator no longer requires the quantity
to equal the largest whole-share quantity that preserves at least two percent.

The projected cash must still be non-negative. A portfolio value of zero cannot
support a buy sizing ratio and is rejected.

## Examples

For a 100,000 account and VNQ at 95.32:

```text
1,029 shares -> 1,915.72 cash -> 1.91572% -> accept
```

For the same account:

```text
quantity leaving 0.8% -> retry
quantity leaving 3.4% -> retry
```

## One-Retry Flow

```text
Run decision agent
  -> parse and run structural/tool checks
  -> evaluate decision-stage reserve ratio

If ratio is inside 1%-3%:
  -> normalize handoff
  -> run execution agent

If ratio is outside 1%-3%:
  -> run the same decision agent one more time
  -> include previous decision JSON, calculated projected cash,
     calculated projected percentage, accepted range, cash,
     portfolio value, sizing price, and instruction to return corrected JSON
  -> parse and validate the retry result

If retry passes:
  -> run execution agent

If retry fails:
  -> record the second error and stop
```

No third decision call is allowed in the same trading iteration.

## Typed Error

An out-of-band reserve raises a dedicated decision-sizing exception containing
structured diagnostic fields:

- order sequence;
- symbol;
- quantity;
- sizing price;
- simulated cash before the buy;
- projected cash;
- portfolio value;
- projected ratio;
- minimum and maximum accepted ratios.

The exception remains a `ValueError` subtype so existing blocking behavior and
tests remain compatible.

## Retry Prompt and Context

The retry is explicitly a correction task, not a new investment decision. It
must:

- preserve decision type, symbol, side, order order, and order type unless an
  existing structural validation rule makes the prior output impossible;
- recalculate only the numeric quantity;
- call account and price tools again so the corrected quantity uses current
  evidence;
- target approximately two percent;
- accept one to three percent as the valid result range;
- return the same strict JSON contract.

Retry diagnostic context is sent only to the decision agent. It must never be
copied into `execution_plan`, the execution task prompt, or execution context.

## Execution Isolation

The execution agent receives only the normalized allowlisted plan:

- explicit sequence;
- symbol;
- side;
- integer quantity;
- asset and order fields;
- system-owned execution constraints.

It receives none of:

- `0.02`, `2%`, `1%`, or `3%`;
- reserve target or tolerance fields;
- decision sizing errors;
- previous decision output;
- retry instructions.

## Testing

Automated tests must verify:

- 1.91572 percent is accepted.
- Exactly one percent and exactly three percent are accepted.
- Below one percent and above three percent raise the dedicated sizing error.
- The former 426/427/425 exact-largest-share requirement is removed.
- An in-band initial decision calls the decision agent once and execution agent
  once.
- An out-of-band initial decision triggers exactly one retry.
- A passing retry reaches execution.
- A failing retry stops without a third decision call or execution call.
- Structural errors do not trigger a retry.
- Retry context contains numerical diagnostics.
- Execution prompt and context contain no reserve or retry information.
- Existing sell, position, order-type, whole-share, and negative-cash tests
  remain green.

## Acceptance Criteria

- The previous VNQ 1,029-share example is accepted.
- The decision agent receives at most one correction opportunity per iteration.
- The execution agent remains unaware of all cash-buffer policy.
- Focused pytest, Ruff, and diff checks pass.
