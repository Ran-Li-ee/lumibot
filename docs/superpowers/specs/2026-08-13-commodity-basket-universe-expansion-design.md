# Commodity Basket Universe Expansion And Rank-First Selection Design

## Purpose

This spec defines a focused expansion of the commodity basket in the mock growth/inflation quadrant strategy.

The immediate goal is to give `commodity_basket_agent` a broader and more opportunity-rich commodity universe while keeping its decision process rank-first: the agent should primarily compare computed price/statistical rankings, and only use news as a secondary tie-breaker when ranking evidence is close, conflicting, or incomplete.

This is not a commodity fundamentals tool spec. USDA, ERS, FRED, inventory, weather, and structured commodity evidence tools are intentionally deferred.

## Background

The current strategy lives in:

```text
lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py
```

The current commodity basket is small:

```python
"commodity": ["GLD", "SLV", "DBC", "PDBC", "GSG"]
```

This makes early system testing simple, but it limits the basket's ability to capture stronger commodity-specific moves. The equity basket work already moved in a rank-first direction, where the agent receives computed rankings instead of raw data-heavy inputs. Commodity selection should follow the same principle.

The user specifically does not want the model to receive explicit category labels such as "single commodity", "broad ETF", "energy", or "agriculture" as primary decision inputs, because those labels can bias the model toward apparently safer diversified ETFs. Classification may exist internally for future evidence routing, but this feature should present the commodity universe as a flat candidate list and let rank evidence lead.

## Scope

This feature includes:

1. Expand `BASKET_UNIVERSES["commodity"]` to the approved 28-symbol candidate list.
2. Keep the commodity basket as a flat symbol list in the model-facing context.
3. Update commodity basket tests that currently expect the old 5-symbol list.
4. Align `commodity_basket_agent` prompt behavior with the equity basket's rank-first selection pattern.
5. Allow `commodity_basket_agent` to use `alpaca_news` as a conditional secondary evidence tool, if configured.
6. Preserve existing history summary/ranking tooling.
7. Verify that all new commodity symbols can be loaded by the current Yahoo daily backtest data path for a representative backtest window.
8. Verify with at least one one-day mock growth/inflation quadrant backtest that the expanded commodity basket does not break macro allocation, basket reports, portfolio decision, execution-plan generation, execution, trace capture, or replay UI discovery.

## Non-Goals

This feature does not:

1. Add USDA NASS, USDA ERS, FRED, weather, inventory, WASDE, EIA, or commodity fundamentals tools.
2. Build historical context summaries for agricultural or energy fundamentals.
3. Add model-facing commodity category labels.
4. Add a new commodity-specific composite score.
5. Change macro regime classification.
6. Change basket target weights.
7. Change portfolio rebalancing or execution-plan mechanics.
8. Expand TIPS or nominal bond baskets.
9. Make performance claims about the expanded universe.

## Approved Commodity Universe

`BASKET_UNIVERSES["commodity"]` must become exactly:

```python
[
    "GLD", "IAU", "SLV", "CPER", "WEAT", "CORN", "SOYB", "CANE",
    "PPLT", "PALL", "DBB",
    "USO", "BNO", "UNG", "UGA", "DBE", "DBO",
    "DBA", "PDBA", "TAGS", "TILL",
    "DBC", "PDBC", "BCI", "GSG", "COMT", "FTGC", "CMDY",
]
```

The ordering is not intended as a preference ranking. It is only a stable, testable configuration order.

The following symbols are intentionally excluded from this iteration:

```text
SGOL, SIVR, BCM
```

`SGOL` and `SIVR` are excluded to reduce near-duplicate gold/silver exposure in the first expansion. `BCM` is excluded because local Yahoo-style data availability checks failed for the representative window used during design discussion.

## Model-Facing Classification Policy

The system may internally know that some symbols represent single commodities, broad commodity strategies, or commodity subsector baskets, but `commodity_basket_agent` must not be given a model-facing category table as part of this feature.

The model-facing input should remain:

```text
basket_id
basket_symbols
target_weight
macro_allocation_report
available tools
tool descriptions
computed rank summaries returned by tools
```

The prompt must avoid language that implies:

```text
broad commodity ETFs are safer defaults
gold is a default defensive choice
energy should be avoided by default
single commodities are too risky by default
diversified commodity funds should be preferred when uncertain
```

The intended behavior is:

```text
Rank evidence first.
News only when rank evidence is close, conflicting, incomplete, or stale.
No category-based default.
```

## Commodity Agent Prompt Requirements

The commodity basket system prompt should be concise and should not try to teach commodity markets in detail.

It must say, in effect:

1. Stay inside the assigned commodity basket.
2. If `target_weight` is zero, report inactive and do not select a symbol.
3. When active, use computed ranking evidence as the primary selection evidence.
4. If one symbol is clearly stronger across the ranking evidence, select it without requiring news.
5. Use available news only when the leading candidates are close, the ranking evidence conflicts, or the price/statistical evidence is incomplete.
6. Do not prefer broad/diversified commodity ETFs merely because they look safer.
7. Do not prefer or avoid symbols based on ticker-name intuition.
8. Return the established basket report JSON fields.

The basket task prompt should remain compact, but it should ask the agent to use ranking evidence and conditional external evidence consistently.

## Tool Permission Requirements

`commodity_basket_agent` should have these explicit tools:

```text
market_load_history_tables_summary
market_last_price
alpaca_news
```

`market_load_history_tables_summary` remains the default tool for multi-symbol rank comparison.

`market_last_price` remains available for targeted price checks.

`alpaca_news` should be available as conditional supporting evidence. If Alpaca news credentials are missing, the existing builtin unavailable-tool behavior is acceptable; the agent should still be able to choose from ranking evidence.

No USDA or ERS tools should be added in this feature.

## Expected Agent Workflow

For an active commodity basket:

1. `commodity_basket_agent` receives the flat commodity symbol list and target weight.
2. The agent calls `market_load_history_tables_summary` for the commodity symbols.
3. The agent reviews top-ranked candidates across available ranking metrics.
4. If one symbol is clearly stronger, the agent returns that symbol.
5. If the top candidates are close or evidence conflicts, the agent may call `alpaca_news` for a focused candidate set.
6. The agent returns the existing strict JSON basket report shape.
7. `portfolio_decision_agent` consumes the basket report exactly as before.
8. Existing planner/execution tools generate and execute the actual orders.

For an inactive commodity basket:

1. The agent should report inactive.
2. It should not call news.
3. It may avoid history calls if no selection is needed.

## Basket Report Compatibility

This feature must preserve the established basket report fields used by downstream portfolio logic:

```text
basket_id
target_weight
status
candidate_symbols
selected_symbol
rank_evidence_summary
external_evidence_used
external_evidence_tools
external_evidence_symbols
external_evidence_summary
reason_brief
```

If the current branch does not yet contain this exact report schema, the implementation should update only the commodity-relevant prompt/tests needed for compatibility with the branch's current schema and should not broaden the feature into a full report-schema migration.

## Data Availability Validation

Before running a live LLM smoke test, validate that all approved commodity symbols can return daily Yahoo-style historical data for a representative window.

The validation should check:

```text
symbol
non-empty data
row count
first date
last date
missing/failure reason
```

The implementation should fail fast or document any symbol that does not load. Symbols that fail data availability should not silently remain in the basket.

## Trace And UI Expectations

The existing trace and replay UI should continue to work without schema changes.

A successful backtest trace should show:

1. `commodity_basket_agent` receives the expanded commodity symbol list.
2. `commodity_basket_agent` has the intended tool list.
3. Its tool calls remain readable in the replay UI.
4. Its final summary/report can be consumed by `portfolio_decision_agent`.

No new UI feature is required in this spec.

## Testing Requirements

Unit tests should cover:

1. The commodity universe equals the approved 28-symbol list.
2. The other basket universes are unchanged.
3. `commodity_basket_agent` receives the expanded commodity symbol list in context.
4. `commodity_basket_agent` has the intended tool permissions.
5. The commodity prompt does not contain category-default language such as "prefer broad commodity ETF" or "default to diversified commodity".
6. Existing portfolio decision and execution-plan tests still pass.

Integration/smoke validation should cover:

1. Yahoo-style historical data availability for all 28 commodity symbols.
2. One-day mock growth/inflation quadrant backtest using the current benchmark runner.
3. Trace discovery in the existing Agent Workflow Replay UI.

## Acceptance Criteria

This feature is complete when:

1. `BASKET_UNIVERSES["commodity"]` contains exactly the approved 28 symbols.
2. The commodity agent prompt is rank-first and does not bias toward broad ETFs or defensive defaults.
3. `commodity_basket_agent` can use `alpaca_news` conditionally, while still being able to select from ranking evidence alone.
4. All approved commodity symbols pass local Yahoo-style daily data availability validation for the selected representative window.
5. Relevant unit tests pass.
6. A one-day mock growth/inflation quadrant backtest completes without breaking the agent workflow.
7. The replay UI can discover and display the resulting trace.

