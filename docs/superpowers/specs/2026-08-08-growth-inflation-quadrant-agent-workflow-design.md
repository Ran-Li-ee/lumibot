# Growth / Inflation Quadrant TAA Agent Workflow Design

Date: 2026-08-08

## 1. Purpose

This spec defines the staged development target for a LumiBot-based
Growth / Inflation four-quadrant tactical asset allocation workflow.

The goal is not to build a complete live trading system in one step. The
goal is to keep future development aligned around a clear workflow:

1. Use a rules-backed macro tool to classify the current Growth /
   Inflation quadrant.
2. Convert that quadrant into target basket weights.
3. Let separate basket agents select the best instrument inside each
   active basket.
4. Merge the basket reports into one portfolio-level target and strict
   execution plan.
5. Let a dedicated execution agent submit and confirm orders without
   doing investment research.

The design is based on the Research Affiliates Growth / Inflation
four-quadrant TAA idea archived locally under:

`docs/strategy_research/S10-growth-inflation-four-quadrant-taa/sources/`

This spec is a product and architecture target. It is not financial
advice and does not claim that the resulting strategy will be profitable.

## 2. Scope

### In Scope

- Define the agent workflow that should guide future implementation.
- Define each agent's responsibility.
- Define each agent's input and output contract at a high level.
- Define each agent's allowed tool surface.
- Name new tools that likely need to be developed.
- Define how the workflow should behave when the macro quadrant changes
  versus when it stays the same.
- Define trace/UI expectations so future work remains observable.

### Out of Scope

- Exact macro data formulas for Growth and Inflation.
- Final choice of public macro data sources.
- Final ETF or stock universe.
- Final basket-level selection strategy.
- Risk parameters such as drawdown limits, stop losses, or position caps.
- Live broker-specific behavior.
- Performance optimization.
- UI implementation details.

These topics require separate specs after this workflow is accepted.

## 3. Core Design Principle

The workflow should be agent-visible, tool-driven, and traceable.

Earlier discussions considered deterministic modules outside the agent
workflow. This spec converts those modules into callable tools wherever
practical. A rule can still be deterministic internally, but if an agent
depends on it, the agent should call a named tool so the replay UI can
show:

- what the agent received,
- which tool was called,
- what the tool returned,
- how that output influenced the next agent.

The most important boundary is:

```text
Rules-backed tools calculate hard facts and portfolio mechanics.
Agents explain, select within bounded choices, merge reports, or execute.
Hard validators block unsafe or malformed plans.
```

## 4. High-Level Workflow

```text
Lumibot on_trading_iteration()
        |
        v
[A1] macro_allocation_agent
        |
        | output: current quadrant + basket weights
        |
        +--------------------+--------------------+--------------------+
        |                    |                    |                    |
        v                    v                    v                    v
[A2] equity_basket_agent  [A3] commodity_basket_agent  [A4] tips_basket_agent  [A5] nominal_bond_basket_agent
        |                    |                    |                    |
        +--------------------+--------------------+--------------------+
                             |
                             v
                  [A6] portfolio_decision_agent
                             |
                             | output: target_portfolio + execution_plan
                             v
                       [A7] execution_agent
                             |
                             v
                    Lumibot orders + trace/UI
```

The four basket agents are logically parallel. If LumiBot executes them
sequentially in the first implementation, the trace dependency graph
should still represent them as independent branches that all depend on
`macro_allocation_agent` and all feed `portfolio_decision_agent`.

## 5. Daily Runtime Behavior

The first version should assume the system runs every trading day.

Even if the macro source data is not updated daily, the daily workflow
should remain the same:

1. Run the macro allocation step.
2. If no new macro data exists, use the latest point-in-time-available
   macro state.
3. Compare today's quadrant with the previous saved quadrant.
4. Run basket agents for all four baskets.
5. Merge basket reports into a target portfolio.
6. Decide whether to trade.
7. Execute only if the final execution plan requires orders.

This keeps the trace/UI consistent and avoids a later rewrite when more
timely data sources are introduced.

## 6. Regime-Unchanged vs Regime-Changed Branches

### Regime Unchanged

When today's quadrant equals the previous trading day's quadrant:

```text
Keep the top-level basket weights.
Still run all basket agents.
Check whether each basket's selected instrument should stay the same.
Generate no orders if the target portfolio is effectively unchanged.
```

This is a daily monitoring and basket-maintenance path.

### Regime Changed

When today's quadrant differs from the previous trading day's quadrant:

```text
Treat the run as a top-level portfolio reallocation.
Generate new basket target weights from the new quadrant.
Run all basket agents to choose instruments for the new basket weights.
Exit zero-weight basket exposure.
Reduce overweight baskets.
Increase newly favored baskets.
Generate a full rebalance execution plan.
```

This is a portfolio reconstruction path, not merely a "which ETF is
strongest today" path.

## 7. Agents

### A1: macro_allocation_agent

Responsibility:

- Call the macro regime tool.
- Report the current Growth / Inflation quadrant.
- Report whether the quadrant changed versus the previous saved state.
- Report four basket target weights.
- Explain the tool output briefly.
- Do not choose basket instruments.
- Do not create orders.
- Do not trade.

Primary input:

```json
{
  "date": "YYYY-MM-DD",
  "previous_regime": "growth_up_inflation_down",
  "previous_basket_weights": {
    "equity": 0.50,
    "commodity": 0.25,
    "tips": 0.0,
    "nominal_bond": 0.25
  }
}
```

Expected output:

```json
{
  "agent": "macro_allocation_agent",
  "regime": "growth_up_inflation_down",
  "previous_regime": "growth_up_inflation_up",
  "regime_changed": true,
  "basket_weights": {
    "equity": 0.50,
    "commodity": 0.25,
    "tips": 0.0,
    "nominal_bond": 0.25
  },
  "reason_brief": "The macro_regime_classifier tool reports growth improving and inflation cooling."
}
```

Allowed tools:

- `macro_regime_classifier` (new): returns quadrant, previous quadrant,
  regime change flag, basket weights, and macro evidence.
- `account_positions` (optional): read current holdings for context.
- `account_portfolio` (optional): read current portfolio value for
  context.

Forbidden tools:

- Order tools.
- Basket selection tools.
- Raw market history tools unless a later spec explicitly adds them.

### A2: equity_basket_agent

Responsibility:

- Study only the equity basket.
- Select one instrument to represent the equity basket when its target
  weight is greater than zero.
- Output inactive status when equity target weight is zero.
- Do not decide total account weights.
- Do not create an execution plan.
- Do not trade.

Candidate examples:

- `SPY`
- `QQQ`
- `IWM`

Allowed tools:

- `basket_universe_metadata` (new): returns candidate instruments and
  descriptions for the requested basket.
- `market_load_history_tables_summary`: preferred tool for comparing
  candidate price-history summaries.
- `market_last_price`: targeted price check.
- `account_positions`: inspect current exposure.
- `account_portfolio`: inspect account size and cash.
- `duckdb_query` only as targeted follow-up when summaries are
  insufficient.

Expected output:

```json
{
  "basket_id": "equity",
  "target_weight": 0.50,
  "selected_symbol": "QQQ",
  "action": "use_selected_symbol",
  "reason_brief": "QQQ is the preferred equity basket expression based on the available basket evidence."
}
```

### A3: commodity_basket_agent

Responsibility:

- Study only the commodity basket.
- Select one instrument to represent the commodity basket when its
  target weight is greater than zero.
- Output inactive status when commodity target weight is zero.
- Do not decide total account weights.
- Do not create an execution plan.
- Do not trade.

Candidate examples:

- `GLD`
- `DBC`

Allowed tools:

- `basket_universe_metadata`
- `market_load_history_tables_summary`
- `market_last_price`
- `account_positions`
- `account_portfolio`
- `duckdb_query` only as targeted follow-up.

Expected output:

```json
{
  "basket_id": "commodity",
  "target_weight": 0.25,
  "selected_symbol": "GLD",
  "action": "use_selected_symbol",
  "reason_brief": "GLD is the preferred commodity basket expression based on the available basket evidence."
}
```

### A4: tips_basket_agent

Responsibility:

- Study only the TIPS / inflation-protected bond basket.
- Select one instrument when target weight is greater than zero.
- Return inactive status when target weight is zero.
- Do not decide total account weights.
- Do not create an execution plan.
- Do not trade.

Candidate examples:

- `TIP`

Allowed tools:

- `basket_universe_metadata`
- `market_load_history_tables_summary`
- `market_last_price`
- `account_positions`
- `account_portfolio`
- `duckdb_query` only as targeted follow-up.

Expected zero-weight output:

```json
{
  "basket_id": "tips",
  "target_weight": 0.0,
  "selected_symbol": null,
  "action": "inactive_basket",
  "reason_brief": "The current macro allocation assigns zero weight to the TIPS basket."
}
```

### A5: nominal_bond_basket_agent

Responsibility:

- Study only the nominal bond basket.
- Select one instrument to represent nominal bond exposure when target
  weight is greater than zero.
- Output inactive status when target weight is zero.
- Do not decide total account weights.
- Do not create an execution plan.
- Do not trade.

Candidate examples:

- `IEF`
- `TLT`

Allowed tools:

- `basket_universe_metadata`
- `market_load_history_tables_summary`
- `market_last_price`
- `account_positions`
- `account_portfolio`
- `duckdb_query` only as targeted follow-up.

Expected output:

```json
{
  "basket_id": "nominal_bond",
  "target_weight": 0.25,
  "selected_symbol": "IEF",
  "action": "use_selected_symbol",
  "reason_brief": "IEF is the preferred nominal bond basket expression based on the available basket evidence."
}
```

### A6: portfolio_decision_agent

Responsibility:

- Merge the macro allocation report and four basket reports.
- Build the final target portfolio.
- Convert the target portfolio into a strict execution plan.
- Use account and price tools for sizing.
- Validate the plan before sending it to execution.
- Do not redo macro classification.
- Do not redo broad basket research.
- Do not submit orders.

Primary input:

```json
{
  "macro_allocation_report": {},
  "equity_basket_report": {},
  "commodity_basket_report": {},
  "tips_basket_report": {},
  "nominal_bond_basket_report": {},
  "date": "YYYY-MM-DD"
}
```

Expected output:

```json
{
  "target_portfolio": {
    "QQQ": 0.50,
    "GLD": 0.25,
    "IEF": 0.25
  },
  "execution_plan": {
    "schema_version": 1,
    "intent": "rebalance",
    "orders": [
      {
        "sequence": 1,
        "symbol": "OLD_SYMBOL",
        "side": "sell",
        "quantity_mode": "shares",
        "quantity": 100,
        "asset_type": "stock",
        "order_type": "market",
        "time_in_force": "day"
      },
      {
        "sequence": 2,
        "symbol": "QQQ",
        "side": "buy",
        "quantity_mode": "shares",
        "quantity": 50,
        "asset_type": "stock",
        "order_type": "market",
        "time_in_force": "day"
      }
    ]
  }
}
```

Allowed tools:

- `account_positions`
- `account_portfolio`
- `market_last_price`
- `target_portfolio_diff` (new): compares current positions with target
  weights and returns buy/sell differences.
- `execution_plan_validator` (new): blocks malformed or unsafe plans
  before execution.

Forbidden tools:

- Macro tools.
- FRED tools.
- News tools.
- Basket research tools.
- Market history tools.
- Order submission or confirmation tools.

### A7: execution_agent

Responsibility:

- Execute only the provided `execution_plan`.
- Do not read or infer investment reasons.
- Do not re-rank candidates.
- Do not substitute symbols.
- Do not add, remove, replace, or reorder orders.
- Use market orders only in the first version.
- Submit orders in sequence order.
- After every `orders_submit_order`, call `orders_confirm_order`.
- Stop remaining orders if confirmation says it is not safe to continue.
- Report execution status.

Primary input:

```json
{
  "date": "YYYY-MM-DD",
  "execution_plan": {}
}
```

Allowed tools:

- `account_positions`
- `account_portfolio`
- `market_last_price`
- `orders_open_orders`
- `orders_submit_order`
- `orders_confirm_order`

Forbidden tools:

- Macro tools.
- FRED tools.
- News tools.
- Market history tools.
- DuckDB tools.
- Basket metadata tools.

Expected output:

```json
{
  "execution_status": "completed",
  "orders": [
    {
      "sequence": 1,
      "symbol": "OLD_SYMBOL",
      "status": "confirmed"
    },
    {
      "sequence": 2,
      "symbol": "QQQ",
      "status": "confirmed"
    }
  ],
  "result": "Rebalance execution completed."
}
```

## 8. New Tools

### macro_regime_classifier

Purpose:

- Encapsulate the Growth / Inflation quadrant calculation.
- Return a point-in-time-safe macro classification.
- Return basket weights derived from the quadrant.

The tool should be deterministic internally. The agent may explain its
output, but the agent should not invent the quadrant.

Output:

```json
{
  "date": "YYYY-MM-DD",
  "regime": "growth_up_inflation_down",
  "growth_direction": "up",
  "inflation_direction": "down",
  "previous_regime": "growth_up_inflation_up",
  "regime_changed": true,
  "basket_weights": {
    "equity": 0.50,
    "commodity": 0.25,
    "tips": 0.0,
    "nominal_bond": 0.25
  },
  "evidence": {
    "growth_signal": {},
    "inflation_signal": {}
  }
}
```

### basket_universe_metadata

Purpose:

- Return each basket's candidate instruments.
- Return plain-language descriptions of what each candidate represents.
- Make basket membership explicit instead of relying on model memory.

Output:

```json
{
  "basket_id": "equity",
  "candidates": [
    {
      "symbol": "SPY",
      "description": "Broad US large-cap equity ETF"
    },
    {
      "symbol": "QQQ",
      "description": "Nasdaq-100 growth-heavy equity ETF"
    }
  ]
}
```

### target_portfolio_diff

Purpose:

- Compare current positions against target portfolio weights.
- Return sell candidates before buy candidates.
- Reduce portfolio math burden on the LLM.

Output:

```json
{
  "current_weights": {},
  "target_weights": {},
  "diffs": [],
  "suggested_order_skeleton": []
}
```

### execution_plan_validator

Purpose:

- Validate the final `execution_plan` before `execution_agent` receives
  it.
- Block negative cash, non-numeric quantities, unsupported order types,
  wrong order order, and missing required fields.

Output:

```json
{
  "valid": true,
  "blockers": [],
  "normalized_execution_plan": {}
}
```

## 9. Trace and UI Expectations

Future implementation should make this workflow visible in the existing
Agent Replay UI:

- The workflow graph should show A1 through A7.
- The four basket agents should appear as parallel branches.
- Each agent's input material should show upstream reports.
- Each tool definition should be available from the UI.
- The `macro_regime_classifier` result should show:
  - current regime,
  - previous regime,
  - regime changed flag,
  - basket weights,
  - macro evidence.
- Basket agent outputs should show:
  - target weight,
  - candidate universe,
  - selected symbol,
  - inactive basket status if target weight is zero.
- `portfolio_decision_agent` should show:
  - final target portfolio,
  - strict execution plan,
  - validator result.
- `execution_agent` should show:
  - submitted orders,
  - confirmation calls,
  - confirmed or blocked status.

The UI is part of the development feedback loop. If a step cannot be
seen in trace, it should be treated as a design weakness.

## 10. First Implementation Slice

The first implementation should not attempt the complete final system.

Recommended staged slice:

1. Add static or simplified `macro_regime_classifier` using hardcoded or
   fixture-backed macro outputs.
2. Add `basket_universe_metadata` with a small ETF universe:
   - equity: `SPY`, `QQQ`, `IWM`
   - commodity: `GLD`, `DBC`
   - tips: `TIP`
   - nominal bond: `IEF`, `TLT`
3. Create the A1 to A7 agent workflow using existing LumiBot agent
   patterns.
4. Let basket agents use existing history summary tools.
5. Let `portfolio_decision_agent` output strict JSON.
6. Reuse the existing market-only `execution_agent` and
   `orders_confirm_order` flow.
7. Verify that the replay UI shows the whole graph and every report.

This allows the workflow to be tested before solving the harder macro
data problem.

## 11. Anti-Drift Rules

Future implementation should not drift into these patterns:

- Do not let the LLM freely classify the macro quadrant without calling
  `macro_regime_classifier`.
- Do not hide regime classification as invisible background code if an
  agent depends on it.
- Do not give order tools to macro or basket agents.
- Do not let `execution_agent` receive broad research reports.
- Do not let basket agents choose instruments outside their assigned
  basket unless a future spec explicitly allows it.
- Do not let `portfolio_decision_agent` redo macro research or basket
  research.
- Do not send raw history rows to the model by default.
- Do not use limit orders in the first version.
- Do not allow negative cash in the first version.
- Do not claim a step is working unless the trace/UI can prove it.

## 12. Open Decisions for Later Specs

The following decisions are intentionally deferred:

- Exact Growth signal formula.
- Exact Inflation signal formula.
- Whether to use GDP, industrial production, CLI, LEI, inflation nowcasts,
  CPI, PCE, market-implied inflation, or a blend.
- How to handle macro data publication lag and revisions.
- Whether basket agents use pure momentum, risk-adjusted momentum,
  drawdown filters, volatility filters, or LLM summaries.
- Whether a zero-weight basket agent should still do full research or
  return quickly.
- Whether target weights should be exactly Research Affiliates'
  `50/25/25/0` mapping or later generalized.
- Rebalance tolerance bands.
- Tax-aware behavior.
- Broker-specific live execution behavior.

These are deferred to keep this spec focused on the workflow shape.

## 13. Acceptance Criteria

The workflow design is considered implemented only when:

- A strategy can run one daily backtest iteration through A1 to A7.
- The replay UI shows the macro agent, four basket agents,
  portfolio decision agent, and execution agent.
- The macro agent calls `macro_regime_classifier`.
- Each basket agent receives only its basket universe.
- The portfolio decision agent receives all basket reports and outputs a
  strict execution plan or an explicit hold/no-trade plan.
- The execution agent receives only the final execution plan and no broad
  research context.
- If orders are submitted, every submit is followed by confirmation.
- If the regime changes, the trace clearly distinguishes this as a
  portfolio reallocation run.
- If the regime does not change, the trace still shows daily basket
  maintenance.

## 14. Self-Review Notes

- The spec intentionally keeps macro formulas out of scope.
- The spec treats deterministic calculations as tools when agents depend
  on them.
- The execution boundary remains consistent with the existing
  market-only, confirm-after-submit execution design.
- The four basket agents are logically parallel even if the first
  implementation runs them sequentially.
- No agent except `execution_agent` has order submission or confirmation
  permission.
