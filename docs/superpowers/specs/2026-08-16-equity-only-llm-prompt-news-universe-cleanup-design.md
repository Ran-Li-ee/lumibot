# Equity-Only LLM Prompt, News, And Universe Cleanup Design

## Purpose

This spec cleans up the current `equity-only-llm` strategy so it can become the next long-lived development baseline.

The strategy should be simple:

```text
equity_basket_agent
  -> deterministic target_portfolio_to_execution_plan
  -> execution_agent
```

The equity agent selects one equity symbol. Deterministic planner code turns that selection into a strict execution plan. The execution agent executes that plan mechanically.

This feature focuses on three changes:

1. Restore the equity universe from the temporary 5-symbol list to a larger 50-stock US equity universe.
2. Give the equity agent conditional access to `alpaca_news`.
3. Rewrite the equity prompts so they are clean, equity-only, rank-first, news-aware, and free of old four-quadrant wording.

## Background

The current clean strategy lives in:

```text
lumibot/example_strategies/ai_trading_team_equity_only_llm.py
lumibot/example_strategies/ai_trading_team_equity_only_helpers.py
```

Current agent flow:

```text
equity_basket_agent
  -> target_portfolio_to_execution_plan
  -> execution_agent
```

Current equity universe:

```python
["SPY", "QQQ", "IWM", "EEM", "FXI"]
```

That list was useful as a small smoke-test universe, but it is not the intended long-term equity-only LLM selection universe. Earlier work expanded the equity basket to 50 high-liquidity US ordinary stocks on a four-quadrant experiment branch. That branch should not be merged wholesale because it also contains four-quadrant strategy work. This feature should selectively bring back only the equity-only-relevant parts.

Current equity tools:

```text
market_load_history_tables_summary
market_last_price
```

Current prompt problem:

The prompt is short, but it still reads like a generic basket prompt. It does not clearly encode the intended evidence flow:

```text
rank/statistics first
  -> select directly if evidence clearly favors one symbol
  -> use news only when leading candidates are close, conflicting, or uncertain
```

It also lacks explicit cleanup boundaries:

- no four-quadrant wording;
- no commodity, TIPS, nominal bond, or macro allocation wording;
- no stock pre-classification labels such as core/growth/defensive/cyclical;
- no encouragement to use DuckDB SQL for ordinary selection;
- no direct order placement by the equity agent.

## Goals

1. Keep `AITradingTeamEquityOnlyLLMStrategy` as the clean main strategy for this branch.
2. Expand `EQUITY_UNIVERSE` to 50 high-liquidity US ordinary stocks.
3. Keep the strategy equity-only: no four-quadrant strategy files, macro allocation agent, commodity basket, TIPS basket, or nominal bond basket should return.
4. Add `alpaca_news` to the equity agent tool surface.
5. Make news usage conditional, not mandatory.
6. Rewrite the equity agent system prompt and task prompt around the actual role: choose one stock from the assigned equity universe.
7. Preserve the deterministic planner handoff: the equity agent outputs a selected symbol, not an execution plan.
8. Keep the execution agent mechanical and minimal.
9. Add tests that verify the intended tool surface, universe, prompts, and workflow boundaries.

## Non-Goals

This feature does not:

1. Reintroduce the four-quadrant strategy.
2. Reintroduce basket weights other than 100% equity.
3. Add macro regime classification.
4. Change `target_portfolio_to_execution_plan` sizing or execution-plan generation.
5. Change `execution_plan_execute` behavior.
6. Add fundamental-data tools or rankings.
7. Add sector/style labels to the stock universe.
8. Force the LLM to call news on every run.
9. Optimize profitability.
10. Change the benchmark runner key unless tests reveal the current key is broken.

## Strategy Shape

The target runtime flow remains:

```text
on_trading_iteration()
  -> scheduled cadence check
  -> equity_basket_agent.run()
  -> parse strict JSON equity report
  -> target_portfolio_to_execution_plan()
  -> validate execution plan
  -> execution_agent.run()
  -> execution_plan_execute()
```

The equity agent should never receive trading tools. The execution agent should never receive broad research reports or news tools.

## Equity Universe

Use this 50-symbol US stock universe:

```text
AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA, AVGO, AMD, NFLX,
ORCL, CRM, ADBE, CSCO, QCOM, TXN, IBM, INTC, NOW, PANW,
UNH, JNJ, LLY, MRK, ABBV, TMO, ABT,
JPM, BAC, GS, MS, V, MA,
WMT, COST, HD, MCD, NKE, SBUX, DIS,
XOM, CVX, CAT, GE, HON, BA, DE,
PG, KO, PEP
```

Rules:

1. All symbols must be uppercase simple stock tickers.
2. No ETFs should be in this equity-only stock universe.
3. No duplicate symbols.
4. The universe should not label stocks by sector, style, safety, growth, defensiveness, or cyclicality.
5. If a symbol fails data loading during implementation smoke tests, replace it only with another high-liquidity US ordinary stock.

## Equity Agent Tool Surface

The equity agent should have exactly the research tools it needs for this strategy:

```text
market_load_history_tables_summary
market_last_price
alpaca_news
```

`market_load_history_tables_summary` remains the default first tool for broad multi-symbol comparison.

`market_last_price` remains available for a targeted current-price check.

`alpaca_news` should be available only to the equity agent in this strategy. It is not a replacement for statistical rankings. It is supporting evidence for ambiguous candidate decisions.

If `alpaca_news` credentials are missing, the runtime may omit the bound tool or expose an unavailable result depending on the existing tool-binding path. Either behavior is acceptable. In both cases, the equity agent should continue with rank-only evidence and mention that briefly in `reason_brief` if it observes the news limitation.

## Evidence Policy

The equity agent should follow this evidence order:

```text
1. Use ranking/statistical summary first.
2. If one symbol is clearly stronger across relevant ranking views, select it without news.
3. If leading candidates are close, rankings conflict, or uncertainty is material, call alpaca_news for only the leading candidates.
4. Use news as a tie-breaker, catalyst check, and material-risk check.
5. Do not sweep news across all 50 symbols.
6. Do not use unsupported memory or market assumptions.
```

This avoids two failure modes:

1. Selecting based only on headlines while ignoring price evidence.
2. Overusing news even when rankings already provide a clear answer.

## Prompt Architecture

### Equity Agent System Prompt

The system prompt should be rewritten as a concise role definition.

It should say:

1. You are the equity selection agent for an equity-only strategy.
2. Your job is to choose exactly one stock from the assigned `basket_symbols`.
3. The selected stock receives the full target equity allocation through downstream deterministic planning.
4. You cannot place orders.
5. Use `market_load_history_tables_summary` first for multi-symbol comparison.
6. Treat rankings as separate evidence views, not as one official answer.
7. Do not invent sector/style classifications.
8. Do not use news unless top candidates are close, conflicting, or uncertain.
9. If news is needed, call `alpaca_news` only for leading candidates.
10. If news is unavailable, continue rank-only.
11. Return strict JSON only.

It should not say:

1. four-quadrant;
2. macro regime;
3. basket allocation;
4. commodity;
5. TIPS;
6. nominal bond;
7. defensive posture;
8. choose safest;
9. choose highest return blindly;
10. use DuckDB SQL for normal selection.

### Equity Agent Task Prompt

The task prompt should be rewritten as the specific per-run instruction.

It should say:

1. Review only the provided `basket_symbols`.
2. Call `market_load_history_tables_summary` with:

```text
symbols = basket_symbols
length = 252
timestep = "day"
top_n = 10
```

3. Compare separate ranking views.
4. Select one symbol from `basket_symbols`.
5. Use `alpaca_news` only when needed for close/conflicting/uncertain leading candidates.
6. Output exactly one JSON object.

Target JSON shape:

```json
{
  "basket_id": "equity",
  "target_weight": 1.0,
  "status": "active",
  "candidate_symbols": ["...copy the assigned basket_symbols exactly..."],
  "selected_symbol": "AAPL",
  "reason_brief": "Short explanation based on rank evidence and, if used, news evidence."
}
```

`candidate_symbols` must copy the assigned `basket_symbols` exactly. It must not be replaced by a shortlist.

`status` should be `"active"` when a symbol is selected. Existing parser compatibility may continue accepting `"selected"`, but the prompt should teach only `"active"` to reduce output variation.

### Execution Agent Prompt Review

The execution agent already uses:

```text
base_system_prompt_mode="execution_minimal"
tools=[BuiltinTools.orders.execute_plan()]
```

This is the right direction.

Implementation should lightly review execution prompts for accidental conflicts, especially any wording that asks the execution agent to inspect account, order, or price tools directly when only `execution_plan_execute` is exposed.

The final execution prompt should keep these principles:

1. Call `execution_plan_execute` exactly once with the complete plan.
2. Do not research.
3. Do not repair, resize, reorder, split, or reinterpret the plan.
4. Do not call lower-level order tools.
5. Use the returned concise summary for the final answer.

This feature should not broaden the execution agent scope.

## Tool Description Expectations

No new tool is required.

Existing tool definitions should already communicate most runtime details:

```text
market_load_history_tables_summary
market_last_price
alpaca_news
execution_plan_execute
```

Implementation should not create a custom news tool unless tests reveal the generic `alpaca_news` description is too broad for this strategy. The preferred first implementation is to reuse `BuiltinTools.news.alpaca_news()` directly.

If a custom wrapper is needed later, it should only shorten or specialize the model-facing description while reusing the original binder.

## Trace And UI Expectations

No UI redesign is required.

A one-day smoke run with news credentials configured should make the following visible in Agent Replay:

1. Strategy: `AITradingTeamEquityOnlyLLMStrategy`.
2. Agent graph: `equity_basket_agent -> execution_agent`.
3. `equity_basket_agent` available tools include:

```text
market_load_history_tables_summary
market_last_price
alpaca_news
```

4. `equity_basket_agent` input material shows the rewritten system prompt and task prompt.
5. `equity_basket_agent` context includes the 50-stock `basket_symbols`.
6. Tool calls should show whether `alpaca_news` was used.
7. `execution_agent` receives only `date` and `execution_plan`.
8. `execution_agent` available tools should remain limited to `execution_plan_execute`.

If news credentials are not configured in the local test environment, the smoke test should still verify that the strategy requests the `alpaca_news` tool definition for the equity agent at creation time, while accepting that runtime may omit the unavailable bound tool from the model-facing available-tools list.

## Testing Requirements

### Unit Tests

Update or add tests in:

```text
tests/test_ai_trading_team_equity_only_llm.py
```

Required coverage:

1. `EQUITY_UNIVERSE` contains exactly 50 symbols.
2. The 50 symbols are uppercase simple tickers.
3. The universe contains no duplicate symbols.
4. The universe contains no old ETF symbols such as `SPY`, `QQQ`, `IWM`, `EEM`, or `FXI`.
5. `initialize()` still creates only:

```text
equity_basket_agent
execution_agent
```

6. `equity_basket_agent` tool names are exactly:

```text
market_load_history_tables_summary
market_last_price
alpaca_news
```

7. `execution_agent` tool names remain exactly:

```text
execution_plan_execute
```

8. Equity prompt contains:

```text
equity-only
market_load_history_tables_summary
alpaca_news
strict JSON
```

9. Equity prompt does not contain:

```text
quadrant
macro
commodity
tips
nominal bond
defensive posture
```

10. Equity task prompt teaches:

```text
length=252
timestep='day'
top_n=10
conditional news
candidate_symbols copied exactly
```

11. Execution prompt still tells the agent to call `execution_plan_execute` exactly once and not modify the plan.

### Existing Regression Tests

Run the focused equity-only tests:

```text
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q
```

If prompt-policy tests are located in another file after implementation, include them in the focused test command.

### Smoke Backtest

After implementation, run a one-day smoke backtest using the current benchmark runner and default branch model setting.

Acceptance checks:

1. The run reaches `equity_basket_agent`.
2. The equity agent sees 50 symbols in context.
3. With news credentials configured, the equity agent sees `alpaca_news` in its runtime available tools.
4. The run produces a parseable equity JSON report.
5. The deterministic planner receives the selected symbol.
6. If an order is generated, `execution_agent` receives only the execution plan.
7. No four-quadrant agents appear.

This smoke test does not need to prove that news is used. Conditional non-use of news is acceptable when ranking evidence is clear. If news credentials are unavailable, verify the requested tool definition in unit tests and treat runtime omission as expected behavior.

## Risks And Mitigations

| Risk | Mitigation |
|---|---|
| 50 symbols increase load time | The history summary tool already returns top-ranked summaries instead of forcing raw-data inspection. |
| The LLM overuses news | Prompt says news is conditional and only for leading candidates. |
| The LLM never uses news | That is acceptable when ranking evidence is clear; future traces can guide tighter prompts if needed. |
| News API credentials are absent | Tool should return unavailable or be omitted by runtime; prompt says continue rank-only if unavailable. |
| Old four-quadrant text leaks back in | Add prompt tests forbidding those terms. |
| Execution prompt becomes too broad | Keep execution agent on `execution_plan_execute` only and test its tool surface. |
| The 50-symbol universe includes a data-poor ticker | Replace only if smoke tests show repeated data failure. |

## Acceptance Criteria

This feature is complete when:

1. The clean equity-only strategy uses the 50-stock universe.
2. The equity agent has `alpaca_news` in addition to existing market tools.
3. The equity agent prompt is rewritten around equity-only stock selection.
4. The prompt encodes rank-first, conditional-news behavior.
5. The prompt no longer contains old four-quadrant or multi-basket strategy wording.
6. The execution agent remains narrow and mechanical.
7. Focused unit tests pass.
8. A one-day smoke backtest can run far enough to verify the updated prompt/tool/context surface in trace.

## Implementation Notes

Likely files:

```text
lumibot/example_strategies/ai_trading_team_equity_only_helpers.py
lumibot/example_strategies/ai_trading_team_equity_only_llm.py
tests/test_ai_trading_team_equity_only_llm.py
```

Potentially inspect, but avoid unnecessary changes to:

```text
lumibot/components/agents/manager.py
lumibot/components/agents/builtins.py
```

This is intentionally a cleanup and selective-port feature. It should not pull in archived four-quadrant strategy behavior.
