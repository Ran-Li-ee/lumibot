# Equity Basket Ranking Expansion Design

## Purpose

This spec defines a focused improvement to the growth/inflation quadrant strategy's basket-selection evidence.

The immediate goal is to make `equity_basket_agent` more useful by expanding the equity basket from a small ETF list to a larger US stock universe, while giving the agent transparent, per-metric top-ten rankings instead of forcing it to rely on raw history rows or a hidden all-in-one score.

This is not a strategy-performance optimization spec. It is a data-presentation and agent-evidence improvement spec.

## Background

The current mock growth/inflation quadrant strategy lives in:

```text
lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py
```

The current basket universe is:

```python
BASKET_UNIVERSES = {
    "equity": ["SPY", "QQQ", "IWM", "EEM", "FXI"],
    "commodity": ["GLD", "SLV", "DBC", "PDBC", "GSG"],
    "tips": ["TIP", "SCHP", "VTIP", "STIP", "LTPZ"],
    "nominal_bond": ["SHY", "IEF", "TLT", "GOVT", "VGIT"],
}
```

The current multi-symbol history summary tool is:

```text
market_load_history_tables_summary
```

Its summary and ranking logic is implemented in:

```text
lumibot/components/agents/history_summary.py
```

Current generated rankings are:

```text
by_return_21
by_return_63
by_return_126
by_momentum_composite
by_composite_score
by_trend_alignment
```

These existing rankings must remain available for compatibility in this feature. However, the new design should not promote `composite_score` as the only or official decision answer.

## Scope

This feature includes:

1. Preserve the currently generated rankings.
2. Add five new top-ten rankings:
   - `by_return_252`
   - `by_risk_adjusted_return_63`
   - `by_risk_adjusted_return_126`
   - `by_near_252_high`
   - `by_volume_surge`
3. Limit model-facing rankings to the top 10 symbols per ranking by default.
4. Provide ranking values, not just symbol names, so the agent can see why a symbol ranked highly.
5. Expand the equity basket to 50 high-liquidity US stocks.
6. Keep commodity, TIPS, and nominal bond baskets unchanged.
7. Update basket-agent prompts only where needed to explain the ranking evidence without adding a long prompt.
8. Verify that the one-day backtest can load the expanded equity universe and still reach portfolio decision and execution.

## Non-Goals

This feature does not:

1. Add sector labels, growth/value labels, defensive/cyclical labels, or other preclassification to equity symbols.
2. Add a new composite rank.
3. Remove existing rankings in this iteration.
4. Change macro regime classification.
5. Change basket weights.
6. Change target-portfolio-to-execution-plan behavior.
7. Change execution tools.
8. Add fundamental-data rankings.
9. Optimize for profitability.

## Ranking Design

### Existing Rankings To Preserve

| Ranking | Source metric | Sort rule |
|---|---|---|
| `by_return_21` | `return_21` | Higher first |
| `by_return_63` | `return_63` | Higher first |
| `by_return_126` | `return_126` | Higher first |
| `by_momentum_composite` | `momentum_composite` | Higher first |
| `by_composite_score` | `composite_score` | Higher first |
| `by_trend_alignment` | `trend_alignment` | Higher first |

### New Rankings

| Ranking | Source metric | Sort rule | Meaning |
|---|---|---|---|
| `by_return_252` | `return_252` | Higher first | Long-term momentum |
| `by_risk_adjusted_return_63` | `return_63_over_volatility_20` | Higher first | Medium-term return efficiency |
| `by_risk_adjusted_return_126` | `return_126_over_volatility_20` | Higher first | Longer-term return efficiency |
| `by_near_252_high` | `distance_to_high_252` | Higher first | Closer to the 252-bar high |
| `by_volume_surge` | `volume_vs_avg_20` | Higher first | Recent volume above 20-bar average |

`distance_to_high_252` is produced as `latest_close / high_252 - 1.0`. It is usually negative or zero. Therefore, sorting higher first correctly ranks `-0.01` ahead of `-0.20`, because `-0.01` is closer to the high.

## Model-Facing Output Shape

The tool currently returns:

```text
rankings
universe_summary
loaded_tables
warnings
```

With a 50-symbol equity universe, sending every symbol's full compact summary to the LLM is unnecessary and can increase token use. The default model-facing output should emphasize top-ten candidates.

The updated response should include:

```text
rankings
ranking_details
candidate_summary
coverage
loaded_tables
warnings
```

### `rankings`

`rankings` maps each ranking name to a list of at most `top_n` symbols. The default `top_n` is 10.

Example:

```json
{
  "by_return_252": ["NVDA", "AVGO", "META"]
}
```

### `ranking_details`

`ranking_details` maps each ranking name to at most `top_n` objects containing symbol and metric value.

Example:

```json
{
  "by_return_252": [
    {"symbol": "NVDA", "value": 0.82},
    {"symbol": "AVGO", "value": 0.61}
  ]
}
```

This makes the ranking auditable without requiring the model to inspect all symbol summaries.

### `candidate_summary`

`candidate_summary` contains compact rows only for symbols appearing in at least one top-ten ranking. This is the main per-symbol detail the LLM should use.

This avoids sending all 50 equity rows by default while still giving context for all serious candidates.

### `coverage`

`coverage` summarizes data availability:

```json
{
  "requested_count": 50,
  "loaded_count": 49,
  "failed_count": 1,
  "top_n": 10
}
```

If symbols fail to load, `warnings` should identify them.

### `loaded_tables`

`loaded_tables` should remain available for every successfully loaded symbol, including symbols not present in `candidate_summary`. This preserves DuckDB follow-up capability for debugging or rare targeted analysis.

## Tool Interface

`market_load_history_tables_summary` should accept an optional `top_n` argument:

```text
top_n: int = 10
```

Rules:

1. `top_n` must be positive.
2. `top_n` should be clamped to the number of successfully summarized symbols.
3. The default behavior should return at most 10 symbols per ranking.
4. Existing callers that omit `top_n` should receive top-ten rankings.

If backward compatibility requires full universe summaries for tests or debugging, implementation may add an explicit opt-in flag such as:

```text
include_full_universe_summary: bool = False
```

The default must remain token-conscious.

## Equity Universe

The equity basket should expand to 50 US-listed, high-liquidity, ordinary stock tickers.

Initial proposed universe:

```text
AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA, AVGO, AMD, NFLX,
ORCL, CRM, ADBE, CSCO, QCOM, TXN, IBM, INTC, NOW, PANW,
UNH, JNJ, LLY, MRK, ABBV, TMO, ABT,
JPM, BAC, GS, MS, V, MA,
WMT, COST, HD, MCD, NKE, SBUX, DIS,
XOM, CVX, CAT, GE, HON, BA, DE,
PG, KO, PEP
```

Validation may replace individual symbols if the current backtest data source cannot reliably load enough history for them. Replacement symbols should follow the same principle: high-liquidity US ordinary stocks with clean ticker symbols.

The equity prompt must not label these stocks as growth, defensive, cyclical, core, speculative, or any similar category.

## Prompt Design

The basket-agent system prompt should stay short.

For basket agents, update the prompt so it says:

1. Use `market_load_history_tables_summary` as the first tool for multi-symbol basket comparison.
2. Treat rankings as separate evidence views, not as a single official answer.
3. Do not invent a new composite score.
4. Prefer symbols that are supported by multiple relevant rankings when the basket is active.
5. If the target weight is zero, report inactive and do not research unnecessarily.

The prompt should not:

1. Tell the equity agent to choose the safest stock.
2. Tell the equity agent to choose the highest return stock blindly.
3. Classify stocks by sector or style.
4. Encourage DuckDB SQL unless summary rankings are insufficient.

## Replay UI And Trace Expectations

No major UI redesign is required.

The existing replay UI should be able to show the raw tool result. If the human-readable formatter for `market_load_history_tables_summary` currently highlights only old rankings, update it enough to mention the new rankings and top-ten behavior.

The trace should make it possible to verify:

1. Which 50 equity symbols were requested.
2. Which symbols loaded successfully.
3. The top-ten entries for each ranking.
4. Which symbol the equity agent selected.

## Testing Requirements

### Unit Tests

Add or update tests for `history_summary.py`:

1. Existing rankings still exist.
2. The five new rankings exist.
3. Every ranking respects `top_n=10` when more than 10 symbols are present.
4. `by_return_252` sorts higher values first.
5. `by_risk_adjusted_return_63` sorts higher values first.
6. `by_risk_adjusted_return_126` sorts higher values first.
7. `by_near_252_high` sorts `-0.01` ahead of `-0.20`.
8. `by_volume_surge` sorts higher `volume_vs_avg_20` first.
9. `candidate_summary` includes the union of symbols appearing in top-ten rankings.
10. `coverage` reports requested, loaded, failed, and top_n counts.

### Tool Definition Tests

Verify that the tool definition and model-facing description mention:

1. `top_n`.
2. Top-ten ranking behavior.
3. Separate rankings rather than a required composite score.

### Strategy Tests

Verify:

1. `BASKET_UNIVERSES["equity"]` contains exactly 50 symbols.
2. Commodity, TIPS, and nominal bond baskets are unchanged.
3. The equity basket list contains no duplicate symbols.
4. The equity basket list contains only simple uppercase tickers.

### Backtest Smoke Test

Run a one-day backtest of the mock growth/inflation quadrant strategy.

Acceptance checks:

1. The run reaches all expected agents.
2. `equity_basket_agent` calls `market_load_history_tables_summary`.
3. The equity tool call requests the expanded equity universe.
4. The tool result includes the new rankings.
5. Rankings are top-ten limited.
6. The system reaches `portfolio_decision_agent`.
7. The system reaches `execution_agent`.
8. No repeated DuckDB-query fallback is required for ordinary equity ranking.

## Risks And Mitigations

| Risk | Mitigation |
|---|---|
| 50 symbols increase data load time | Keep only top-ten model-facing summaries; run smoke test |
| Some stock data is unavailable in the current backtest data source | Replace failing tickers with high-liquidity alternatives |
| Existing composite rankings bias the agent | Preserve them for compatibility, but prompt says rankings are separate evidence views and not an official answer |
| LLM over-focuses on one ranking | Prompt says use multiple relevant rankings |
| Top-ten truncation hides a useful symbol | Keep `loaded_tables` for follow-up; `top_n` can be adjusted later |
| UI formatter misses new ranking names | Update formatter lightly if needed |

## Acceptance Criteria

This feature is complete when:

1. The five new rankings are generated.
2. All rankings are top-ten limited by default.
3. Ranking details expose both symbol and metric value.
4. Candidate summary avoids sending all 50 equity rows by default.
5. Equity basket has 50 valid US stock symbols.
6. Basket-agent prompts reflect the new ranking evidence style.
7. Automated tests cover ranking behavior and universe definition.
8. A one-day backtest confirms the expanded equity basket can run through the full agent workflow.

