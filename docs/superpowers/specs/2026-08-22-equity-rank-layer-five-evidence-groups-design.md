# Equity Rank Layer Five Evidence Groups Design

## Purpose

Upgrade the QQQ historical equity-only LLM strategy's market-evidence layer from a small set of simple ranking lists into a structured five-group rank layer.

The goal is to give `equity_basket_agent` clearer, more auditable price and volume evidence when selecting five stocks from the current QQQ historical constituent universe.

This feature is not a news feature, not a fundamental-data feature, and not a new portfolio-construction feature. It focuses only on the model-facing rank evidence that helps the equity agent choose stocks.

## Background

The current QQQ historical equity-only strategy resolves a point-in-time QQQ constituent universe and asks `equity_basket_agent` to choose exactly five stocks. The downstream deterministic planner gives those five stocks equal target weights.

The current market summary tool is:

```text
market_load_history_tables_summary
```

Its ranking logic currently lives in:

```text
lumibot/components/agents/history_summary.py
```

Current code computes many per-symbol history statistics, but only six ranking lists are exposed:

```text
by_return_21
by_return_63
by_return_126
by_momentum_composite
by_composite_score
by_trend_alignment
```

Earlier work designed and partially implemented richer ranking output, including `ranking_details`, `candidate_summary`, and additional ranking lists. Later token-reduction work simplified the model-facing output back to a smaller structure. This spec intentionally revisits the rank layer with a clearer design.

## Design Principle

The model should not receive one hidden "best stock" answer.

Instead, it should receive several separate evidence views:

```text
1. Momentum rank
2. Trend quality rank
3. Risk-adjusted momentum rank
4. Breakout / near-high rank
5. Volume confirmation rank
```

The equity agent should compare these views and choose five stocks that are supported by multiple relevant rankings, while explaining which evidence groups support each selected symbol.

## Scope

This feature includes:

1. Add or restore structured ranking output with ranking values, not only symbol names.
2. Organize ranking lists into five evidence groups.
3. Add selected new indicators required by those groups.
4. Keep rankings top-10 limited by default.
5. Return detailed candidate summaries for a capped union of leading ranked names.
6. Keep raw OHLCV data out of the default model-facing response.
7. Keep loaded DuckDB tables available for rare targeted follow-up.
8. Update the equity agent system prompt and task prompt so the agent understands the five evidence groups.
9. Remove or soften prompt wording that implies `composite_score` is the final answer.
10. Update replay formatter behavior enough for developers to inspect the new rank evidence.
11. Add focused tests for indicators, sort direction, output shape, and prompt expectations.

## Non-Goals

This feature does not:

1. Add or change news behavior.
2. Add fundamental metrics such as earnings, revenue growth, valuation, margins, or analyst estimates.
3. Add stock-pool quality filtering.
4. Change QQQ historical universe resolution.
5. Change weekly cadence.
6. Change Top 5 selection count.
7. Change equal-weight target portfolio construction.
8. Change `execution_agent` or order execution tools.
9. Add sector, style, growth, defensive, core, or speculative labels to symbols.
10. Create a mandatory black-box combined score that the LLM must obey.
11. Optimize parameters through a long backtest sweep.

## Evidence Groups

### 1. Momentum Rank

Purpose: identify stocks whose prices have already shown strength across multiple lookback windows.

This group answers:

```text
Who has been going up?
Is the strength short-term, medium-term, long-term, or persistent across windows?
```

Ranking members:

| Ranking | Metric | Sort Rule | Meaning |
|---|---|---|---|
| `by_return_21` | `return_21` | Higher first | About one month of momentum; catches recent acceleration. |
| `by_return_63` | `return_63` | Higher first | About three months of momentum; useful for weekly swing selection. |
| `by_return_126` | `return_126` | Higher first | About six months of momentum; reduces short-term noise. |
| `by_return_252` | `return_252` | Higher first | About one year of momentum; connects to classic momentum and dual momentum research. |
| `by_return_252_ex_skip_21` | `return_252_ex_skip_21` | Higher first | About one year of momentum excluding the latest month; reduces chasing immediate short-term reversals. |

`return_252_ex_skip_21` should be calculated as:

```text
close[-22] / close[-253] - 1.0
```

when enough data exists. It uses the price from roughly one month ago as the endpoint and the price from roughly one year before that as the start.

### 2. Trend Quality Rank

Purpose: distinguish stable, persistent uptrends from noisy spikes.

This group answers:

```text
Did the stock rise in a smooth and durable way, or did it just jump around?
```

Ranking members:

| Ranking | Metric | Sort Rule | Meaning |
|---|---|---|---|
| `by_trend_alignment` | `trend_alignment` | Higher first | Count of price above SMA20, SMA50, and SMA200. Existing simple trend score. |
| `by_sma_stack_score` | `sma_stack_score` | Higher first | Rewards bullish moving-average order, such as close > SMA20 > SMA50 > SMA200. |
| `by_adjusted_slope_90` | `adjusted_slope_90` | Higher first | Regression slope times R-squared over about 90 bars; inspired by Clenow-style trend strength. |
| `by_regression_r2_90` | `linear_regression_r2_90` | Higher first | Measures how cleanly price follows a trend line. |

`sma_stack_score` should be a simple deterministic score, for example:

```text
+1 if close > SMA20
+1 if SMA20 > SMA50
+1 if SMA50 > SMA200
+1 if close > SMA200
```

The exact scoring should be documented in code and tests.

`adjusted_slope_90` should be based on a log-price linear regression when possible:

```text
annualized_regression_slope_90 * linear_regression_r2_90
```

If the implementation chooses not to annualize in the first version, the field name and description must make that explicit. Tests should verify monotonic behavior rather than relying on a single fragile decimal value.

### 3. Risk-Adjusted Momentum Rank

Purpose: prefer stocks that deliver strong returns per unit of volatility or drawdown.

This group answers:

```text
Is the stock strong in a way that is worth the risk, or is it only strong because it is extremely volatile?
```

Ranking members:

| Ranking | Metric | Sort Rule | Meaning |
|---|---|---|---|
| `by_return_63_over_volatility_20` | `return_63_over_volatility_20` | Higher first | Three-month return efficiency versus recent volatility. |
| `by_return_126_over_volatility_20` | `return_126_over_volatility_20` | Higher first | Six-month return efficiency versus recent volatility. |
| `by_return_252_over_volatility_63` | `return_252_over_volatility_63` | Higher first | One-year return efficiency versus medium-term volatility. |
| `by_sharpe_like_63` | `sharpe_like_63` | Higher first | Mean daily return divided by daily volatility over about 63 bars. |
| `by_calmar_like_126` | `calmar_like_126` | Higher first | 126-day return divided by absolute 126-day max drawdown. |

These metrics are ranking aids, not claims of true annualized Sharpe or institutional risk measures. Names should use `like` when the metric is an approximation.

When volatility or drawdown denominator is missing or zero, the metric should be `None` and unavailable for ranking.

### 4. Breakout / Near-High Rank

Purpose: identify stocks trading near important highs or breaking out of recent ranges.

This group answers:

```text
Is the stock still near leadership territory, or has it already fallen away from its highs?
Is it breaking to a fresh high?
```

Ranking members:

| Ranking | Metric | Sort Rule | Meaning |
|---|---|---|---|
| `by_near_252_high` | `distance_to_high_252` | Higher first | Closer to 52-week high. Usually negative or zero; -0.01 ranks ahead of -0.20. |
| `by_near_63_high` | `distance_to_high_63` | Higher first | Closer to 3-month high. |
| `by_breakout_20_high` | `breakout_20_high_score` | Higher first | Rewards close at or above prior 20-bar high. |
| `by_breakout_63_high` | `breakout_63_high_score` | Higher first | Rewards close at or above prior 63-bar high. |
| `by_drawdown_from_high_60` | `drawdown_from_high_60` | Higher first | Smaller drawdown from recent high is better. |

Breakout scores should avoid look-ahead:

```text
prior_high_20 = max(high over bars before the latest bar)
breakout_20_high_score = close[-1] / prior_high_20 - 1.0
```

The same rule applies to the 63-bar breakout score.

### 5. Volume Confirmation Rank

Purpose: identify whether price strength has visible volume participation.

This group answers:

```text
Is the move supported by unusual trading activity, or is it a thin/weak move?
```

Ranking members:

| Ranking | Metric | Sort Rule | Meaning |
|---|---|---|---|
| `by_volume_vs_avg_20` | `volume_vs_avg_20` | Higher first | Latest volume relative to 20-bar average. |
| `by_dollar_volume_20` | `dollar_volume_20` | Higher first | Liquidity context from average close times average volume. |
| `by_up_volume_ratio_20` | `up_volume_ratio_20` | Higher first | Share of recent volume occurring on positive-return days. |
| `by_volume_confirmed_momentum` | `volume_confirmed_momentum` | Higher first | Combines medium momentum with positive volume participation. |

Volume confirmation should not dominate selection by itself. The prompt should describe it as confirmation evidence, especially useful when momentum and trend candidates are close.

`dollar_volume_20` is primarily liquidity context. It may be ranked, but the prompt should not make the agent choose the largest/liquidest mega-cap solely because this field is high.

## Model-Facing Output Shape

The batch history summary should return:

```text
rank_groups
rankings
ranking_details
candidate_summary
coverage
loaded_tables
warnings
```

### `rank_groups`

`rank_groups` maps evidence groups to ranking names:

```json
{
  "momentum": ["by_return_21", "by_return_63", "by_return_126", "by_return_252", "by_return_252_ex_skip_21"],
  "trend_quality": ["by_trend_alignment", "by_sma_stack_score", "by_adjusted_slope_90", "by_regression_r2_90"],
  "risk_adjusted_momentum": ["by_return_63_over_volatility_20", "by_return_126_over_volatility_20"],
  "breakout_near_high": ["by_near_252_high", "by_near_63_high"],
  "volume_confirmation": ["by_volume_vs_avg_20", "by_dollar_volume_20"]
}
```

The exact list should match implemented rankings.

### `rankings`

`rankings` maps each ranking name to at most `top_n` symbols.

Default:

```text
top_n = 10
```

### `ranking_details`

`ranking_details` maps each ranking name to at most `top_n` objects:

```json
{
  "by_return_63": [
    {"rank": 1, "symbol": "NVDA", "value": 0.321},
    {"rank": 2, "symbol": "AVGO", "value": 0.287}
  ]
}
```

The value must be the exact metric value used for sorting after compact rounding.

### `candidate_summary`

`candidate_summary` contains compact rows for the most relevant symbols from the union of top-ranked lists.

Default limit:

```text
candidate_summary_limit = 25
```

Selection priority:

1. Symbols appearing in multiple ranking lists.
2. Symbols ranking highly in momentum, adjusted slope, and risk-adjusted momentum.
3. Symbols that were already selected or currently held, if that information is available in this function later.
4. First-seen ranking order as a deterministic tie-breaker.

The first version does not need to inject current holdings into this tool. The priority item exists as a future-compatible design note.

### `coverage`

`coverage` should include:

```json
{
  "requested_count": 89,
  "loaded_count": 87,
  "failed_count": 2,
  "top_n": 10,
  "candidate_summary_limit": 25,
  "ranking_count": 20
}
```

Warnings should identify symbols that failed to load.

### `loaded_tables`

`loaded_tables` remains available for all successfully loaded symbols so the model can run rare targeted DuckDB follow-up if the summary evidence is insufficient.

Raw historical rows should not be included in the model-facing response by default.

## Tool Interface

`market_load_history_tables_summary` should accept optional arguments:

```text
top_n: int = 10
candidate_summary_limit: int = 25
```

Rules:

1. Both values must be positive integers.
2. Values should be clamped to the number of successfully summarized symbols.
3. Existing callers that omit these arguments should receive top-10 rankings and at most 25 candidate summary rows.
4. The tool description must mention the five evidence groups and the default limits.
5. `top_n` in prompts and the actual tool schema must be consistent.

## Prompt Scope

Prompt changes are in scope for this feature.

The goal is not to add a long prompt. The goal is to align the prompt with the new evidence structure and remove outdated hints that push the model toward a single hidden composite answer.

### Equity Agent System Prompt

Update the QQQ historical equity agent system prompt so it says:

1. Select exactly five stocks from the provided QQQ historical `basket_symbols`.
2. Use `market_load_history_tables_summary` first for multi-symbol comparison.
3. Treat rank groups as separate evidence lenses:
   - momentum;
   - trend quality;
   - risk-adjusted momentum;
   - breakout / near-high;
   - volume confirmation.
4. Prefer stocks supported by multiple relevant evidence groups.
5. Do not blindly copy the first five names from one ranking list.
6. Do not treat `composite_score` as the final answer.
7. Do not invent sector, style, safety, or cyclicality labels.
8. Do not place orders or size trades.
9. Return strict JSON only.

### Equity Agent Task Prompt

Update the task prompt so it says:

1. First call `market_load_history_tables_summary` with:

   ```text
   symbols=basket_symbols
   length=252
   timestep='day'
   top_n=10
   candidate_summary_limit=25
   ```

2. Compare evidence across the five rank groups.
3. Select exactly five unique symbols.
4. In `reason_brief`, briefly mention which evidence groups support the selected symbols.
5. If a selected symbol is supported by only one group, explain why it still deserves selection.
6. Continue with rank-only evidence if news is unavailable or not needed. This feature does not require news.
7. Return exactly one strict JSON object with:

   ```text
   basket_id
   target_weight
   status
   candidate_symbols
   selected_symbols
   reason_brief
   ```

### Prompt Text To Avoid

The updated prompt should not:

1. Say or imply that `composite_score` is the official answer.
2. Tell the model to choose the safest stock.
3. Tell the model to choose only the highest return stock.
4. Encourage DuckDB SQL for ordinary ranking decisions.
5. Encourage broad news searches.
6. Add labels such as aggressive, defensive, cyclical, or core.
7. Ask the model to optimize portfolio weights.

## Formula and Missing Data Rules

All metrics must be deterministic and use only data visible to the current backtest runtime.

General rules:

1. If not enough history exists, the metric is `None`.
2. Non-finite values must be sanitized to `None`.
3. Metrics with `None` must be excluded from their ranking list.
4. Sort direction must be explicit and covered by tests.
5. Rank tie-breakers must be deterministic, preferably by symbol or stable input order after sorting by value.
6. No metric should use future bars.

## Replay UI and Trace Expectations

No large UI redesign is required.

The existing agent replay UI should be able to show:

1. `rank_groups`.
2. `ranking_details`.
3. `candidate_summary`.
4. `coverage`.

The human-readable formatter for `market_load_history_tables_summary` should be updated enough to make clear:

1. how many symbols were requested and loaded;
2. top-N ranking limit;
3. candidate summary limit;
4. which evidence groups are present;
5. top ranking names with values for each group.

This is developer-facing output. It should favor clarity over polished product copy.

## Testing Requirements

### Unit Tests: Metric Calculation

Add or update tests for:

1. `return_252`.
2. `return_252_ex_skip_21`.
3. `sma_stack_score`.
4. `linear_regression_slope_90`.
5. `linear_regression_r2_90`.
6. `adjusted_slope_90`.
7. `return_252_over_volatility_63`.
8. `sharpe_like_63`.
9. `calmar_like_126`.
10. `distance_to_high_63`.
11. `breakout_20_high_score`.
12. `breakout_63_high_score`.
13. `dollar_volume_20`.
14. `up_volume_ratio_20`.
15. `volume_confirmed_momentum`.

Tests should include insufficient-data cases and non-finite sanitization.

### Unit Tests: Ranking Output

Add or update tests for:

1. All existing rankings remain available unless deliberately replaced by a documented equivalent.
2. New rankings appear under the correct evidence group.
3. Each ranking list is limited to `top_n`.
4. `ranking_details` includes rank, symbol, and value.
5. `candidate_summary` is capped at `candidate_summary_limit`.
6. Candidate summary prioritizes repeated appearances across rankings.
7. `coverage` reports requested, loaded, failed, top_n, candidate_summary_limit, and ranking_count.
8. Missing metric values exclude symbols from only the affected ranking, not from the whole summary.

### Tool Definition Tests

Verify model-facing tool descriptions mention:

1. `top_n`.
2. `candidate_summary_limit`.
3. five evidence groups.
4. top-N ranking behavior.
5. summary-first behavior.
6. DuckDB as targeted follow-up only.

### Prompt Tests

Verify the QQQ historical equity agent prompt:

1. mentions the five evidence groups;
2. says `market_load_history_tables_summary` should be used first;
3. says not to blindly copy one ranking list;
4. says not to treat `composite_score` as the final answer;
5. requires exactly five selected symbols;
6. does not ask for portfolio weighting;
7. does not add sector/style/safety labels.

### Replay Formatter Tests

Verify the formatter for `market_load_history_tables_summary` displays:

1. coverage;
2. evidence groups;
3. ranking names;
4. ranking values;
5. candidate summary count.

### Smoke Backtest

Run a short QQQ historical equity-only smoke backtest.

The smoke test should verify:

1. `equity_basket_agent` calls `market_load_history_tables_summary`.
2. The call uses `top_n=10` and `candidate_summary_limit=25`.
3. The tool result contains `rank_groups`, `ranking_details`, `candidate_summary`, and `coverage`.
4. The equity agent selects exactly five symbols.
5. The selected symbols are in `basket_symbols`.
6. The downstream deterministic planner still generates a valid execution plan.
7. No repeated DuckDB query fallback is required for ordinary ranking.

The smoke test is an implementation-correctness check, not a performance claim.

## Acceptance Criteria

This feature is complete when:

1. The history summary tool exposes ranking evidence in five groups.
2. Rankings include values and not just symbol lists.
3. Candidate summaries are capped and token-conscious.
4. Raw history rows remain out of default model-facing output.
5. Tool arguments and prompts agree on `top_n` and `candidate_summary_limit`.
6. Equity agent prompts are updated and cleaned of old composite-score bias.
7. Tests cover metric calculation, ranking output, tool descriptions, prompts, and formatter behavior.
8. A short backtest confirms the workflow still runs through equity selection and execution planning.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Too many rankings overwhelm the LLM | Group rankings into five named evidence groups and cap each at top 10. |
| Candidate summary grows too large | Cap at 25 symbols and prioritize repeated ranking appearances. |
| Composite score keeps biasing the model | Keep it as one evidence view only; prompt says it is not the final answer. |
| Volume metrics favor only mega-cap liquidity | Treat volume as confirmation and context, not standalone selection proof. |
| Regression metrics are fragile on short histories | Return `None` when data is insufficient; add explicit tests. |
| New output shape breaks replay UI | Update formatter and tests for both old and new fields where practical. |
| Prompt becomes too long | Use concise group-based instructions and avoid duplicating formula details in the prompt. |

## Future Work

Not part of this feature:

1. Add news/catalyst confirmation after rank evidence is close or conflicting.
2. Add fundamentals and earnings-growth evidence.
3. Test Top 10 instead of Top 5.
4. Add sector or theme concentration controls.
5. Add model-independent scoring experiments.
6. Compare five-year performance before and after rank-layer changes.
7. Explore dynamic candidate-summary limits based on universe size.
