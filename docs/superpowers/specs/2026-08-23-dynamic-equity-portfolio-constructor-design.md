# Dynamic Equity Portfolio Constructor Design

## Purpose

Replace the current fixed "select exactly five stocks and assign equal 20% weights" equity-only portfolio construction rule with a deterministic dynamic portfolio construction layer.

The goal is to let the equity LLM agent identify attractive candidates and explain evidence, while a local deterministic constructor decides:

```text
how many stocks to hold
which eligible candidates become target holdings
how much target weight each selected stock receives
how cash buffer, volatility adjustment, and single-name caps are applied
```

This feature should improve the strategy architecture before further performance tuning. It is not intended to prove outperformance in one implementation pass.

## Background

The current QQQ historical equity-only LLM strategy has a clean workflow:

```text
equity_basket_agent
  -> selected_symbols: exactly five stocks

equity_only_target_portfolio()
  -> converts the five symbols into equal 20% target weights

target_portfolio_to_execution_plan()
  -> converts target weights into sell/buy execution_plan

execution_agent
  -> executes the deterministic execution_plan
```

This has been useful as a baseline, but it is too rigid:

1. Five holdings may be too concentrated when evidence is broad.
2. Five holdings may be too diluted when only a few stocks are clearly strong.
3. Equal weights ignore evidence strength.
4. Equal weights ignore volatility differences.
5. The prompt currently contains hard-coded assumptions such as "choose exactly five" and "downstream planner gives equal target weights".

The existing downstream planner already accepts arbitrary `target_weight` values, so the cleanest integration point is the current `equity_only_target_portfolio()` layer. The feature should replace that fixed equal-weight mapping with a deterministic dynamic constructor while preserving the already-tested planning and execution chain.

## Design Principle

Separate judgment from sizing.

```text
LLM role:
  identify candidates
  explain evidence strength and uncertainty
  optionally use news for close/conflicting/uncertain candidates

Deterministic constructor role:
  calculate selection count
  calculate target weights
  apply volatility adjustment
  apply cash buffer
  apply min/max position constraints
  produce planner-compatible target_portfolio
```

The LLM should not directly assign precise per-symbol weights. It can describe evidence strength, but precise sizing should be performed by local code.

## Literature-Informed Motivation

This design follows several common portfolio construction patterns found in momentum and factor strategies:

1. Cross-sectional momentum strategies often select a top group, top decile, or top quintile rather than a permanently fixed count.
2. Momentum index methodologies such as MSCI and S&P use momentum scores, risk-adjusted momentum, market capitalization, or caps rather than simple equal weight only.
3. Adaptive Asset Allocation-style systems combine momentum selection with risk-aware weighting, often using volatility or risk parity concepts after candidate selection.
4. VAA/PAA-style breadth ideas suggest that the number of strong candidates contains useful information: broad strength can justify broader exposure, while narrow leadership can justify concentration or caution.

The implementation should borrow the general ideas, not attempt to exactly replicate any external index or paper methodology.

## Scope

This feature includes:

1. Add a deterministic dynamic equity portfolio construction layer.
2. Replace fixed five-stock equal weighting in the QQQ historical equity-only strategy.
3. Allow dynamic selected count within a configured range.
4. Use locally computed rank and volatility evidence to produce per-symbol weights.
5. Apply single-position maximum weight.
6. Apply minimum position weight or prune tiny positions.
7. Preserve the existing cash buffer behavior for total target equity exposure.
8. Update equity agent prompts so the agent selects and explains candidates, not exact weights.
9. Update tests around selected-count validation, target portfolio generation, prompt text, and planner compatibility.
10. Record constructor diagnostics in trace-friendly structures so the replay UI can show why a given run selected N holdings and assigned each weight.
11. Run focused tests and a smoke backtest after implementation.

## Non-Goals

This feature does not:

1. Change the QQQ historical universe resolver.
2. Add new equity symbols.
3. Change the five rank evidence groups themselves.
4. Add new news providers.
5. Add fundamental data.
6. Let the LLM directly assign exact per-symbol weights.
7. Rewrite `target_portfolio_to_execution_plan()` or change its execution semantics. The constructor should adapt to the existing planner contract. Only narrowly scoped compatibility fixes are allowed if tests prove they are necessary.
8. Change `execution_agent` order execution logic.
9. Change trailing stop logic.
10. Introduce shorts, leverage, options, margin, or negative cash.
11. Claim performance improvement without a separate backtest comparison.

## Current Problems To Fix

### 1. Fixed Selected Count

Current behavior:

```text
selected_symbols must contain exactly five unique symbols
```

Risk:

```text
The strategy cannot adapt when evidence is unusually narrow or broad.
```

Fix:

Allow a configurable range:

```text
min_positions = 3
max_positions = 10
default fallback count = 5
```

### 2. Equal Weight Assumption

Current behavior:

```text
each selected stock receives 20%
```

Risk:

```text
The strongest candidate and the weakest accepted candidate receive identical exposure.
```

Fix:

Generate weights from deterministic evidence scores, then normalize and cap them.

### 3. No Volatility Adjustment

Current behavior:

```text
position size ignores volatility
```

Risk:

```text
A high-momentum but extremely unstable stock can dominate portfolio risk.
```

Fix:

Apply a volatility penalty or inverse-volatility adjustment after evidence scoring.

### 4. No Single-Name Cap In The Constructor

Current behavior:

```text
equal weights avoid large concentration only because selected_count is fixed
```

Risk:

```text
Once dynamic evidence weighting exists, a single stock could receive too much target weight.
```

Fix:

Use a configurable maximum single-position weight:

```text
max_single_weight = 0.30
```

The exact default can be tuned later, but the first implementation should include the guardrail.

### 5. Prompt Conflicts

Current prompt assumptions include:

```text
choose exactly five
downstream deterministic planner gives the five selected stocks equal target weights
target_weight must be 1.0 for the equity basket as a whole
do not assign per-symbol weights
```

Some of these are currently true, but they will become misleading.

Fix:

Rewrite the equity prompts around:

```text
identify credible candidates
explain evidence and uncertainty
do not calculate exact weights
do not place orders
downstream deterministic constructor will calculate target weights
```

## Proposed Workflow

Target workflow:

```text
equity_basket_agent
  -> calls market_load_history_tables_summary
  -> optionally calls alpaca_news for leading candidates
  -> returns candidate/evidence JSON

dynamic_equity_portfolio_constructor
  -> reads candidate/evidence JSON
  -> reads or recomputes required rank and volatility evidence
  -> selects 3-10 final holdings
  -> assigns target weights
  -> emits target_portfolio and diagnostics

target_portfolio_to_execution_plan
  -> receives target_portfolio
  -> generates strict execution_plan

execution_agent
  -> executes the plan
```

## Equity Agent Output Contract

The equity agent should stop being responsible for exact portfolio sizing.

The desired output should remain strict JSON, but shift from final holdings to candidate evidence. A first implementation can still accept `selected_symbols` as an ordered candidate list for backward compatibility, but the semantic meaning should become:

```text
these are the agent's credible candidates, ordered by evidence preference
```

Preferred future shape:

```json
{
  "basket_id": "equity",
  "status": "active",
  "candidate_symbols": ["... all assigned basket symbols ..."],
  "selected_symbols": ["A", "B", "C", "D", "E", "F", "G"],
  "rank_review": {
    "clear_leaders": ["A", "B", "C"],
    "credible_candidates": ["A", "B", "C", "D", "E", "F", "G"],
    "excluded_candidates": [
      {
        "symbol": "X",
        "reason": "strong short-term momentum but weak trend quality"
      }
    ]
  },
  "news_review": {
    "used_news": true,
    "symbols_checked": ["A", "B", "C"],
    "material_risks": []
  },
  "reason_brief": "..."
}
```

Implementation may keep this simpler in stage one, but the prompt should not ask for exact per-symbol weights.

## Constructor Input

The constructor should receive:

```json
{
  "date": "2024-09-05",
  "equity_report": { "...": "strict JSON from equity agent" },
  "basket_symbols": ["..."],
  "policy": {
    "min_positions": 3,
    "max_positions": 10,
    "fallback_positions": 5,
    "cash_buffer_weight": 0.02,
    "max_single_weight": 0.30,
    "min_single_weight": 0.05
  }
}
```

The constructor should not rely only on LLM-transcribed numeric evidence. It should use locally available rank and history summary data when possible.

## Constructor Output

The constructor should produce a planner-compatible target portfolio plus diagnostics:

```json
{
  "portfolio_mode": "dynamic_equity",
  "selected_count": 6,
  "target_portfolio": [
    {"basket_id": "equity", "symbol": "A", "target_weight": 0.24},
    {"basket_id": "equity", "symbol": "B", "target_weight": 0.20},
    {"basket_id": "equity", "symbol": "C", "target_weight": 0.17},
    {"basket_id": "equity", "symbol": "D", "target_weight": 0.15},
    {"basket_id": "equity", "symbol": "E", "target_weight": 0.12},
    {"basket_id": "equity", "symbol": "F", "target_weight": 0.10}
  ],
  "cash_buffer_weight": 0.02,
  "weighting_method": "evidence_score_with_volatility_adjustment",
  "diagnostics": {
    "selection_reason": "six candidates had broad evidence support",
    "max_single_weight": 0.30,
    "min_single_weight": 0.05,
    "capped_symbols": [],
    "pruned_symbols": [],
    "candidate_scores": [
      {
        "symbol": "A",
        "evidence_score": 0.91,
        "volatility_penalty": 0.84,
        "adjusted_score": 0.76,
        "final_weight": 0.24
      }
    ]
  }
}
```

The `target_portfolio` field is the only field required by the existing execution planner. The rest is for trace, UI, debugging, and later strategy review.

## Dynamic Selection Count Rules

The first version should use simple explainable rules.

Suggested defaults:

```text
min_positions = 3
max_positions = 10
fallback_positions = 5
```

Candidate count should be based on deterministic evidence, not LLM prose alone.

Possible rule shape:

1. Build an eligible candidate pool from the union of:
   - equity agent selected symbols
   - symbols with strong repeated appearances across primary rank groups
   - symbols with strong adjusted scores
2. Remove symbols with missing required pricing data.
3. Score each candidate.
4. Keep candidates whose score is close enough to the leading cluster.
5. Enforce `min_positions` and `max_positions`.
6. If scoring is unavailable or inconsistent, fall back to the first valid `fallback_positions` agent-selected symbols and equal weights.

The exact thresholds can be implementation details, but they must be tested and traceable.

## Evidence Score Design

The constructor should convert rank evidence into a normalized score.

The score should reflect the existing five evidence groups:

1. Momentum.
2. Trend quality.
3. Risk-adjusted momentum.
4. Breakout / near-high.
5. Volume confirmation.

Initial score design:

```text
primary evidence:
  momentum
  trend_quality

quality adjustment:
  risk_adjusted_momentum

secondary confirmation:
  breakout_near_high
  volume_confirmation
```

The constructor should not treat all groups as equal votes. This should align with the current Evidence Interpretation Policy already added to the equity agent prompts.

## Volatility Adjustment

After evidence scoring, apply a volatility adjustment.

Simple first version:

```text
adjusted_score = evidence_score / volatility_penalty
```

Where `volatility_penalty` is derived from available history summary volatility fields. The exact implementation can normalize volatility across candidates so the median candidate has a neutral penalty.

Intent:

```text
strong but extremely volatile stocks get reduced weight
strong and smoother stocks get relatively higher weight
```

This should not eliminate high-volatility winners automatically. It should size them more carefully.

## Weight Normalization And Constraints

The constructor should:

1. Convert adjusted scores into raw weights.
2. Normalize total target equity exposure to:

   ```text
   1.0 - cash_buffer_weight
   ```

3. Apply `max_single_weight`.
4. Prune or redistribute positions below `min_single_weight`.
5. Re-normalize after caps/pruning.
6. Ensure final sum does not exceed available target exposure.

Default constraints:

```text
cash_buffer_weight = 0.02
max_single_weight = 0.30
min_single_weight = 0.05
```

These defaults should be parameters, not hard-coded magic throughout the code.

## Prompt Changes

### Remove Or Avoid

Remove prompt wording that says:

1. `choose exactly five`.
2. `the downstream deterministic planner gives the five selected stocks equal target weights`.
3. `selected_symbols must contain exactly five`.
4. `target_weight must be 1.0 for the equity basket as a whole` if it implies fixed basket sizing.
5. `do not assign per-symbol weights` if it is not paired with an explanation that a deterministic constructor will handle weights.

### Add Or Preserve

Prompt should say:

1. The agent identifies credible candidates and explains evidence.
2. The agent does not place orders.
3. The agent does not calculate exact per-symbol target weights.
4. The downstream deterministic constructor calculates selected count and target weights.
5. Candidate symbols must come only from `basket_symbols`.
6. The agent should provide an ordered candidate list of credible names.
7. News remains conditional: only use news for leading candidates when rank evidence is close, conflicting, or uncertain.

### Output Format

The prompt should still require strict JSON.

The first implementation can keep `selected_symbols`, but it should allow a count range:

```text
selected_symbols must contain between 3 and 10 unique symbols from basket_symbols.
```

Future implementations may rename this to `credible_candidate_symbols`, but that is not required for the first integration if it causes too much churn.

## Trace And UI Requirements

The constructor output should be stored in a way that trace replay can inspect.

The replay UI should be able to show, even if not immediately redesigned:

1. Constructor input candidates.
2. Final selected count.
3. Final target weights.
4. Evidence score per selected symbol.
5. Volatility adjustment per selected symbol.
6. Symbols capped by max weight.
7. Symbols pruned by min weight.
8. Cash buffer applied.
9. Fallback path, if any.

This feature does not require a major UI redesign, but the data should be trace-friendly.

## Testing Requirements

### Unit Tests

Add focused tests for the constructor:

1. Selects fewer positions when only a small leading cluster qualifies.
2. Selects more positions when evidence breadth is broad.
3. Applies evidence-score weighting.
4. Applies volatility adjustment.
5. Enforces `max_single_weight`.
6. Enforces `min_single_weight` by pruning or redistribution.
7. Preserves `cash_buffer_weight`.
8. Produces planner-compatible `target_portfolio`.
9. Falls back safely when evidence is missing.
10. Rejects symbols outside the provided universe.

### Existing Strategy Tests

Update tests in:

```text
tests/test_ai_trading_team_equity_only_llm.py
```

Tests should no longer assume:

```text
exactly five selected symbols
all weights are 0.2
```

Tests should verify:

1. Selected symbols can be 3-10.
2. Target weights sum to approximately 0.98 by default.
3. All target weights are positive and within constraints.
4. Output remains compatible with `target_portfolio_to_execution_plan`.
5. Prompt text no longer says fixed five equal weight.

### Smoke Backtest

Run a one-day QQQ historical equity-only smoke backtest.

The smoke test should verify:

1. Equity agent prompt reflects dynamic construction.
2. Equity agent selects a valid candidate list.
3. Constructor emits a target portfolio with dynamic weights.
4. Planner accepts the target portfolio.
5. Execution agent receives an execution plan.
6. Trace contains constructor diagnostics.

### Comparison Backtest

After smoke passes, run the same five-year baseline window used for the current QQQ historical equity-only LLM baseline. This is for analysis, not required for the feature to be considered mechanically complete.

Compare:

```text
fixed five equal weight baseline
dynamic constructor version
SPY benchmark
```

Metrics to inspect:

1. CAGR / total return.
2. Maximum drawdown.
3. Sharpe.
4. Calmar.
5. Turnover.
6. Number of holdings over time.
7. Weight concentration over time.
8. Whether large winners were allowed enough weight.
9. Whether large losers were capped or diversified.

## Acceptance Criteria

This feature is accepted when:

1. Fixed five/equal-weight assumptions are removed from strategy prompts and tests.
2. Equity agent can return 3-10 credible candidates.
3. A deterministic constructor converts candidates into target weights.
4. Constructor output is compatible with the existing execution planner.
5. Final target weights respect cash buffer, min position, and max position constraints.
6. Volatility adjustment is applied and visible in diagnostics.
7. Trace includes enough constructor details for later UI inspection.
8. Focused unit tests pass.
9. A one-day QQQ historical equity-only smoke backtest reaches planning and execution.

## Risks And Mitigations

| Risk | Mitigation |
|---|---|
| Constructor becomes too complex too quickly | Start with simple deterministic scoring and clear defaults. |
| LLM output format becomes unstable | Keep strict JSON and preserve `selected_symbols` as the first contract if possible. |
| LLM tries to assign exact weights | Prompt explicitly says a deterministic constructor handles exact weights. |
| Dynamic weights overfit noisy rank data | Use caps, volatility adjustment, and a fallback path. |
| One stock receives too much weight | Enforce `max_single_weight`. |
| Tiny positions create noise | Enforce `min_single_weight` or prune tiny allocations. |
| More holdings dilute winners | Selection count should depend on evidence breadth, not always max out. |
| Fewer holdings increase drawdown | Cap concentration and inspect five-year drawdown before treating performance as improvement. |
| Trace becomes too verbose | Store detailed diagnostics in structured fields, but keep model-facing summaries concise. |

## Future Work

Not part of the first implementation:

1. Use fundamentals in weight calculation.
2. Use sector/theme constraints.
3. Learn scoring weights from historical performance.
4. Add explicit market-regime dependent concentration.
5. Add model comparison across GPT versions.
6. Add UI-specific visualizations for constructor score-to-weight flow.
7. Add tax-aware or turnover-aware weighting.
8. Add risk budget based on stop-loss distance.
9. Use optimization libraries for covariance-aware sizing.
