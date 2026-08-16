# Equity-Only LLM Mainline Decision

Date: 2026-08-16

## Decision

The next main development direction is the equity-only LLM selection engine.

The Growth / Inflation four-quadrant work remains valuable as a historical experiment,
but it should not be merged wholesale into `dev` as the active mainline.

## Why

The same five-year comparison window showed:

| Strategy | Interpretation |
|---|---|
| fixed ETF quadrant | most defensive, but return was too low |
| LLM quadrant | improved return, but did not justify the extra allocation complexity |
| equity-only LLM | strongest return, with higher drawdown |
| SPY buy and hold | strong reference benchmark that remains difficult to beat on risk-adjusted terms |

The equity-only LLM result suggests the basket-selection engine is the most promising area to optimize next.

## Important Caveat

The equity-only LLM path is not yet a mature investment system. It had higher drawdown than SPY and should be treated
as a research direction, not as production readiness or investment advice.

## Git Direction

Do not directly merge `feature/equity-only-llm-ablation` into `dev`.

Instead:

1. Keep `feature/equity-only-llm-ablation` as the four-quadrant experiment archive.
2. Start from `dev`.
3. Selectively port the neutral `equity-only-llm` strategy and minimum generic execution/reporting dependencies.
4. Use a later branch for actual equity strategy optimization.
