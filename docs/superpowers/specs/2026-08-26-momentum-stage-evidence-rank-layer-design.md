# Momentum Stage Evidence Rank Layer Design

## Purpose

Replace the equity-only LLM strategy's current market-evidence layer with a momentum-stage evidence layer.

The goal is to help the strategy select stocks with confirmed but not exhausted strength. In plain language, this feature should move the stock-selection process away from "buy the highest old momentum rank" and toward "find the fish body, not the fish tail."

This is a strategy-evidence feature. It does not change order execution, broker behavior, cadence, news API behavior, stop logic, or the QQQ historical universe resolver.

## Background

The current equity-only LLM strategy uses `market_load_history_tables_summary` as the main evidence tool for `equity_basket_agent`.

The current rank layer already exposes many useful price/volume rankings, organized around five evidence groups:

```text
momentum
trend_quality
risk_adjusted_momentum
breakout_near_high
volume_confirmation
```

That was a useful step forward, but it still mostly answers:

```text
Which stocks are strongest right now?
```

The current research question is different:

```text
Which strong stocks are in a better trend stage, rather than already overextended or stale?
```

The user wants the first version of this new feature to disable the old rank evidence for the equity-only strategy, so the LLM does not remain anchored to previous "strongest rank wins" behavior.

## Design Principle

The model should not receive the old rank system and the new rank system at the same time.

For this feature, the equity-only strategy should receive only the new momentum-stage evidence layer. Old rank code may remain in the codebase for other strategies and backward compatibility, but it must not be the model-facing evidence profile for this strategy.

The new evidence layer should separate:

```text
1. Rankable support signals: useful for top-N ranking.
2. Reference / warning signals: useful for judging whether a ranked candidate is stale, overextended, or short-term overbought.
```

This avoids treating every number as "higher is always better."

## Scope

This feature includes:

1. Add a momentum-stage evidence profile for `market_load_history_tables_summary`.
2. Disable old equity-only model-facing rank groups for the equity-only LLM strategy.
3. Compute the first 11 momentum-stage indicators.
4. Expose rankable indicators as top-10 ranking lists.
5. Expose reference indicators in candidate summaries, not as naive top-rank lists.
6. Update candidate selection so the candidate summary is built from the new stage rankings.
7. Update dynamic equity portfolio construction so it uses the new momentum-stage support structure instead of old `GROUP_WEIGHTS`.
8. Rewrite the equity agent system prompt and task prompt around the new evidence model.
9. Update the tool description so the LLM understands the new evidence profile.
10. Keep news available only as a secondary tie-breaker or catalyst/risk check.
11. Update trace/replay formatting only if required for readable inspection of the new fields.
12. Add focused tests for indicator calculations, sort direction, model-facing output shape, prompt cleanup, and constructor behavior.

## Non-Goals

This feature does not:

1. Add SMH, change the universe source, or change QQQ historical constituent logic.
2. Add fundamental metrics, analyst estimates, valuation metrics, or earnings data.
3. Change weekly/monthly cadence.
4. Change execution tools or order-confirmation behavior.
5. Change stop-loss / trailing-stop behavior.
6. Add a new LLM agent.
7. Add new news providers.
8. Force the LLM to call news every run.
9. Create a black-box "final best stock" score that the LLM must blindly obey.
10. Delete old rank code globally.

## Evidence Profile

Add a new model-facing evidence profile named:

```text
momentum_stage
```

The equity-only strategy should request or receive this profile by default.

The returned payload should make the profile explicit:

```json
{
  "evidence_profile": "momentum_stage"
}
```

Old rank groups such as `momentum`, `trend_quality`, `risk_adjusted_momentum`, `breakout_near_high`, and `volume_confirmation` should not be presented to the equity-only agent when this profile is active.

The old fields may continue to exist for compatibility only if they are not model-facing for this strategy. If compatibility requires preserving `rank_groups` / `rankings` field names, their contents must contain the new momentum-stage groups, not the old groups.

## First 11 Indicators

### 1. `rank_delta_4w`

Purpose: identify stocks whose relative rank has recently improved.

Calculation idea:

```text
rank 4 weeks ago - current rank
```

Positive is better. A stock moving from rank 40 to rank 8 has `+32`, which suggests fresher momentum.

Use as rank: yes.

Sort: higher first.

### 2. `top_decile_age_weeks`

Purpose: estimate whether a stock has been in the leading group for too long.

Calculation idea:

```text
consecutive weeks in the top 10% of the universe
```

Use as rank: no.

Use as reference / warning signal. Very low may mean the trend is unconfirmed. Very high may mean stale momentum. The first version should not assume "higher is always better."

### 3. `extension_ma50_pct`

Purpose: detect whether price is too far above the 50-day moving average.

Calculation:

```text
latest_close / SMA50 - 1
```

Use as rank: no.

Use as reference / warning signal. Moderate extension can confirm strength; extreme extension can indicate overextension.

### 4. `atr_extension_20d`

Purpose: detect overextension while accounting for normal volatility.

Calculation idea:

```text
(latest_close - SMA20) / ATR20
```

Use as rank: no.

Use as reference / warning signal. A high value means price may be stretched relative to recent normal movement.

### 5. `positive_day_ratio_3m`

Purpose: identify smoother, more persistent upward movement.

Calculation:

```text
positive-return days over last 63 trading days / available return days
```

Use as rank: yes.

Sort: higher first.

### 6. `max_day_return_share_3m`

Purpose: identify whether recent return came mostly from one large jump.

Calculation idea:

```text
largest positive daily return over last 63 trading days / total positive return over last 63 trading days
```

Use as rank: yes, reverse.

Sort: lower first.

Interpretation: lower is generally smoother. High values warn that recent performance may be driven by one event rather than a durable trend.

### 7. `distance_to_252d_high_pct`

Purpose: capture classic 52-week high momentum.

Calculation:

```text
latest_close / 252-day high - 1
```

Use as rank: yes.

Sort: higher first, because values closer to zero are nearer the high.

### 8. `recent_vs_intermediate_momentum`

Purpose: detect short-term overheat or short-term weakness compared with intermediate momentum.

Calculation idea:

```text
return_21 - return_252_ex_skip_21
```

Use as rank: no.

Use as reference / warning signal. Very high can mean recent move may be overextended. Very low can mean recent weakness against longer momentum.

### 9. `up_down_volume_ratio_60d`

Purpose: check whether upward days have stronger volume support than downward days.

Calculation idea:

```text
sum(volume on positive-return days over 60 days) / sum(volume on negative-return days over 60 days)
```

Use as rank: yes.

Sort: higher first.

### 10. `excess_return_vs_qqq_6m`

Purpose: detect stock-specific strength versus the QQQ benchmark.

Calculation:

```text
stock return_126 - QQQ return_126 over the same date window
```

Use as rank: yes.

Sort: higher first.

QQQ must be loaded as benchmark data only. It must not be added to candidate symbols unless it is already in the strategy universe.

### 11. `excess_return_vs_spy_6m`

Purpose: detect stock-specific strength versus the broad-market benchmark.

Calculation:

```text
stock return_126 - SPY return_126 over the same date window
```

Use as rank: yes.

Sort: higher first.

SPY must be loaded as benchmark data only. It must not be added to candidate symbols unless it is already in the strategy universe.

## Ranking Groups

The new profile should expose rankable indicators in small, clear groups.

Suggested first-version groups:

```text
freshness:
  by_rank_delta_4w

smoothness:
  by_positive_day_ratio_3m
  by_low_max_day_return_share_3m

near_high:
  by_near_252d_high

volume_confirmation:
  by_up_down_volume_ratio_60d

relative_strength:
  by_excess_return_vs_qqq_6m
  by_excess_return_vs_spy_6m
```

The following fields must not be exposed as naive top-rank lists:

```text
top_decile_age_weeks
extension_ma50_pct
atr_extension_20d
recent_vs_intermediate_momentum
```

They should appear in candidate summary rows as reference fields and warning flags.

## Candidate Summary

The candidate summary should be selected from the union of the new stage rankings.

Default limits:

```text
top_n = 10
candidate_summary_limit = 25
```

Candidate summary rows should include:

```text
symbol
latest_close
stage_evidence_groups
stage_ranking_count
stage_best_rank
stage_best_rank_by_group
rank_delta_4w
top_decile_age_weeks
extension_ma50_pct
atr_extension_20d
positive_day_ratio_3m
max_day_return_share_3m
distance_to_252d_high_pct
recent_vs_intermediate_momentum
up_down_volume_ratio_60d
excess_return_vs_qqq_6m
excess_return_vs_spy_6m
stage_warning_flags
```

Possible warning flags:

```text
stale_top_decile
extreme_ma50_extension
extreme_atr_extension
recent_overheat_vs_intermediate
single_day_jump_concentration
benchmark_lag
thin_or_missing_volume_support
insufficient_history
```

Thresholds can be simple in the first version, but they must be explicit and tested. The first implementation should prefer stable behavior over perfect finance theory.

## Dynamic Portfolio Constructor

The existing constructor currently uses old evidence groups and `GROUP_WEIGHTS`.

This feature should update the equity-only construction path to use the momentum-stage evidence profile.

The constructor should compute an auditable candidate score from the new structure. The score should be simple and transparent:

```text
stage_support_score:
  support from rankable groups and repeated appearances

stage_penalty:
  penalty for stale, overextended, short-term overheated, or weak benchmark-relative evidence

adjusted_stage_score:
  stage_support_score - stage_penalty, optionally adjusted by volatility as existing policy already does
```

The constructor should not resurrect old group names or old `composite_score` logic for this strategy.

The output diagnostics should show:

```text
stage_support_score
stage_penalty
adjusted_stage_score
stage_warning_flags
final_weight
```

## Tool Description

The `market_load_history_tables_summary` tool description should describe the active evidence profile clearly enough for the LLM.

For the equity-only strategy, the description should communicate:

```text
This tool returns momentum-stage evidence, not a single best-stock answer.
Rankable lists identify freshness, smoothness, near-high strength, volume support, and benchmark-relative strength.
Reference fields identify stale or overextended candidates and should not be treated as "higher is always better."
Prefer candidates supported by multiple stage evidence groups and not blocked by strong warning flags.
```

It should not encourage DuckDB SQL for normal universe ranking. DuckDB remains available only as a rare follow-up path when the summary is missing or contradictory.

## Equity Agent Prompt Rewrite

The equity agent system prompt and task prompt should be rewritten around the new evidence model.

The new prompt should say, in compact form:

```text
Your job is to select credible equity candidates with confirmed but not exhausted momentum.
Do not pick a stock merely because it has the highest old momentum, return, or composite rank.
Use the momentum-stage evidence profile from market_load_history_tables_summary.
Prefer candidates with several supporting stage rankings: freshness, smoothness, near-high strength, volume confirmation, and benchmark-relative strength.
Use reference fields as risk checks: stale top-decile age, price extension, ATR extension, and recent overheat versus intermediate momentum.
If the evidence is clear, select without news.
Use news only when leading candidates are close, conflicting, or have catalyst/risk uncertainty.
Return strict JSON only.
```

The prompt should remove old wording that anchors the model to:

```text
five rank groups
momentum_composite
composite_score
highest raw momentum
breakout / near-high as standalone leadership proof
```

The prompt should still preserve existing strategy boundaries:

```text
equity_basket_agent cannot place orders
equity_basket_agent cannot size trades
equity_basket_agent should select 3 to 10 symbols
execution_agent handles only execution_plan execution
news is secondary
candidate_symbols must preserve the assigned basket_symbols
```

## News Behavior

News remains available to `equity_basket_agent`, but it is not the primary signal.

Expected behavior:

```text
1. First call market_load_history_tables_summary.
2. Build the leading candidate set from momentum-stage evidence.
3. If leaders are close, conflicting, stale/overextended, or catalyst-sensitive, call alpaca_news for those leading symbols only.
4. If news is unavailable, continue with price/volume stage evidence.
```

The feature should not force news calls every run.

## Backward Compatibility

Old rank calculation can remain available for:

```text
other strategies
tests that intentionally validate legacy behavior
developer comparisons
```

But the equity-only strategy under active development should use the new `momentum_stage` evidence profile by default.

If the implementation adds a parameter such as `evidence_profile`, tests should verify:

```text
equity-only default: momentum_stage
legacy default for unrelated callers: existing behavior, unless intentionally migrated
```

## Trace and Replay Expectations

Trace should record the full tool result as usual.

The replay UI should let the developer see:

```text
evidence_profile
stage rank groups
stage ranking details
candidate stage summary
warning flags
benchmark context
constructor diagnostics
```

If current replay formatting already shows these through raw JSON, no dedicated UI work is required in this feature. If the formatter hides or summarizes away important stage fields, update the formatter just enough to make inspection practical.

## Validation

Unit tests should cover:

1. Each new indicator calculation on deterministic sample price data.
2. Sort direction for every rankable indicator.
3. Reference indicators are present but not exposed as naive rank lists.
4. QQQ and SPY benchmark returns are loaded as benchmark context, not candidate symbols.
5. Candidate summary is selected from new stage rankings.
6. Old `by_composite_score` and `momentum_composite` are absent from the equity-only model-facing evidence profile.
7. The equity agent prompt no longer references old five rank groups or old composite wording.
8. Dynamic constructor uses new stage support/penalty diagnostics instead of old `GROUP_WEIGHTS`.
9. Existing non-equity-only behavior is not unintentionally broken.

Smoke validation should run:

```text
one short backtest
```

The smoke test should verify:

1. `equity_basket_agent` receives `market_load_history_tables_summary`.
2. The tool result contains `evidence_profile = momentum_stage`.
3. The agent's summary references momentum-stage evidence rather than old composite rank.
4. The strategy produces a valid execution plan or a clear, expected no-trade reason.
5. No high-priority runtime errors appear in trace.

## Success Criteria

This feature is successful when:

1. The equity-only strategy no longer shows the old rank evidence profile to the LLM.
2. The tool exposes the 11 first-version momentum-stage indicators.
3. Rankable and reference indicators are clearly separated.
4. Prompt language is rewritten around confirmed-but-not-exhausted momentum.
5. The dynamic constructor uses the new stage evidence structure.
6. Trace/replay can be used to inspect why candidates were selected.
7. Tests prove old composite-score anchoring is removed from this strategy.

## Open Follow-Up Questions

These are intentionally out of scope for the first implementation:

1. Whether SMH should replace or supplement QQQ as the universe source.
2. Whether thresholds for warning flags should be optimized.
3. Whether news should become more systematic after stage evidence is stable.
4. Whether fundamental filters should be added.
5. Whether the strategy should avoid market exposure when all candidates look stale or overextended.
