# Mock Growth / Inflation Quadrant Skeleton Design

Date: 2026-08-08

## 1. Purpose

Build the first runnable skeleton of the Growth / Inflation quadrant
agent workflow without implementing real macro regime detection yet.

This feature is an engineering proof of workflow shape. It should show
that LumiBot can run:

```text
mock macro quadrant
  -> four basket agents
  -> portfolio decision agent
  -> execution agent
```

The system should be traceable in the Agent Replay UI and should preserve
the execution boundary already proven by the existing
`growth -> decision -> execution` strategy.

## 2. Non-Goals

This slice must not attempt to solve the full investment strategy.

Out of scope:

- Real Growth / Inflation regime classification.
- Real macro data source selection.
- Real Research Affiliates weight replication.
- Final basket universe design.
- Risk parameters such as drawdown limits, stop losses, or leverage rules.
- New portfolio optimizer tools.
- New basket metadata tools.
- New order execution tools.
- Performance claims.

The only new tool-like capability in this slice is a deterministic mock
`macro_regime_classifier`.

## 3. Relationship To Existing Work

The implementation should copy the existing test strategy rather than
modify it in place.

Source reference:

```text
lumibot/example_strategies/ai_trading_team_growth_execution_test.py
```

Reusable ideas:

- Separate research, decision, and execution responsibilities.
- Strict JSON from the decision stage.
- Dedicated execution agent with order tools.
- Market-only execution.
- Confirm-after-submit order flow.
- Agent Replay UI traceability.
- Prefer compact history summary outputs over raw history rows.

Do not reuse these assumptions:

- One `growth_agent` ranks the whole universe.
- One decision agent chooses a single best ETF.
- Execution agent receives upstream research.
- One undifferentiated ETF universe is enough.

## 4. Strategy Name

Create a new example strategy with a name that makes the mock nature clear.

Recommended class name:

```text
AITradingTeamMockGrowthInflationQuadrantStrategy
```

Recommended file name:

```text
lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py
```

The existing growth-execution test strategy must remain available as a
stable reference.

## 5. Agent Workflow

The target workflow is:

```text
Lumibot on_trading_iteration()
        |
        v
[A1] macro_allocation_agent
        |
        | output: mock quadrant + basket weights
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
                             | output: strict execution_plan
                             v
                       [A7] execution_agent
```

The four basket agents are logically parallel. The first implementation may
run them sequentially in Python, but the trace dependencies should still
show them as independent branches that all depend on
`macro_allocation_agent` and all feed `portfolio_decision_agent`.

## 6. Mock Regime Classifier

### Purpose

`macro_regime_classifier` is a fake regime tool used to test the workflow.
It must not pretend to perform real macro analysis.

It returns:

- current mock quadrant,
- previous mock quadrant if available,
- whether the quadrant changed,
- basket weights,
- short explanation that the result is mock-generated.

### Determinism

Do not use true randomness. Backtests must be reproducible.

The mock classifier should support deterministic behavior from:

```text
date + seed + mode
```

Recommended modes:

- `seeded_random`: the same date and seed always produce the same quadrant.
- `cycle`: dates walk through the four quadrants in a repeatable order,
  useful for testing reallocations and turnovers.

The default should be reproducible and suitable for debugging.

### Output Shape

Example:

```json
{
  "tool": "macro_regime_classifier",
  "mock": true,
  "mode": "seeded_random",
  "seed": 42,
  "date": "2024-09-05",
  "regime": "growth_up_inflation_down",
  "growth_direction": "up",
  "inflation_direction": "down",
  "previous_regime": "growth_up_inflation_up",
  "regime_changed": true,
  "basket_weights": {
    "equity": 0.50,
    "commodity": 0.00,
    "tips": 0.25,
    "nominal_bond": 0.25
  },
  "reason_brief": "Mock classifier selected this regime from deterministic date and seed logic. This is not real macro evidence."
}
```

## 7. Mock Weight Mapping

Each quadrant should assign:

```text
one primary basket: 50%
two supporting baskets: 25% each
one inactive basket: 0%
```

Initial mapping:

| Regime | equity | commodity | tips | nominal_bond |
|---|---:|---:|---:|---:|
| `growth_up_inflation_down` | 0.50 | 0.00 | 0.25 | 0.25 |
| `growth_up_inflation_up` | 0.50 | 0.25 | 0.25 | 0.00 |
| `growth_down_inflation_up` | 0.00 | 0.50 | 0.25 | 0.25 |
| `growth_down_inflation_down` | 0.25 | 0.00 | 0.25 | 0.50 |

This mapping is only for workflow testing. It is not a final allocation
model.

## 8. Basket Universes

Each basket must contain at least five semantically valid instruments so
that basket agents can demonstrate real selection behavior.

Initial test universe:

| Basket | Symbols | Intended Meaning |
|---|---|---|
| equity | `SPY`, `QQQ`, `IWM`, `EEM`, `FXI` | US large cap, US growth/technology, US small cap, emerging markets, China equity |
| commodity | `GLD`, `SLV`, `DBC`, `PDBC`, `GSG` | Gold, silver, diversified commodity exposure, actively managed commodity exposure, broad commodity index exposure |
| tips | `TIP`, `SCHP`, `VTIP`, `STIP`, `LTPZ` | Inflation-protected US bond ETFs with different durations or providers |
| nominal_bond | `SHY`, `IEF`, `TLT`, `GOVT`, `VGIT` | Nominal US Treasury exposure across short, intermediate, long, and broad Treasury durations |

The basket membership should be encoded in the new strategy configuration
for this slice. Do not build a separate `basket_universe_metadata` tool in
this implementation.

## 9. Agent Responsibilities

### A1: macro_allocation_agent

Responsibility:

- Call `macro_regime_classifier`.
- Report the mock quadrant.
- Report target basket weights.
- Report whether the quadrant changed.
- Explain briefly that the result comes from the mock classifier.

Allowed tools:

- `macro_regime_classifier`

Forbidden:

- Market history tools.
- Basket selection.
- Portfolio decision.
- Order tools.

Expected output:

```json
{
  "agent": "macro_allocation_agent",
  "regime": "growth_up_inflation_down",
  "regime_changed": true,
  "basket_weights": {
    "equity": 0.50,
    "commodity": 0.00,
    "tips": 0.25,
    "nominal_bond": 0.25
  },
  "mock": true,
  "reason_brief": "The mock classifier returned this quadrant and weight set."
}
```

### A2-A5: basket agents

Basket agents:

- `equity_basket_agent`
- `commodity_basket_agent`
- `tips_basket_agent`
- `nominal_bond_basket_agent`

Responsibility:

- Study only the assigned basket.
- Use only the basket symbols passed in context.
- If target weight is greater than zero, select one representative symbol.
- If target weight is zero, return inactive status and no selected symbol.
- Provide a short reason for the selection.

Allowed tools:

- `market_load_history_tables_summary`
- `market_last_price`

Forbidden:

- Choosing outside the basket.
- Macro regime classification.
- Portfolio-level weight changes.
- Order tools.
- Raw DuckDB querying by default.

Expected active output:

```json
{
  "basket_id": "equity",
  "target_weight": 0.50,
  "status": "active",
  "candidate_symbols": ["SPY", "QQQ", "IWM", "EEM", "FXI"],
  "selected_symbol": "QQQ",
  "reason_brief": "QQQ is the preferred representative based on the basket evidence."
}
```

Expected inactive output:

```json
{
  "basket_id": "commodity",
  "target_weight": 0.00,
  "status": "inactive",
  "candidate_symbols": ["GLD", "SLV", "DBC", "PDBC", "GSG"],
  "selected_symbol": null,
  "reason_brief": "The basket target weight is zero, so no instrument is selected."
}
```

### A6: portfolio_decision_agent

Responsibility:

- Merge the macro allocation report and four basket reports.
- Build a final target portfolio from non-zero basket weights.
- Use current account state and prices to create strict numeric orders.
- Output a strict `execution_plan` or explicit no-trade plan.
- Do not redo macro or basket research.

Allowed tools:

- `account_positions`
- `account_portfolio`
- `market_last_price`

Forbidden:

- Market history tools.
- DuckDB tools.
- News tools.
- FRED tools.
- Order tools.
- Re-ranking basket symbols.

Expected output:

```json
{
  "decision": {
    "type": "rebalance",
    "reason_brief": "Mock quadrant weights and basket selections imply a target portfolio update."
  },
  "target_portfolio": [
    {
      "basket_id": "equity",
      "symbol": "QQQ",
      "target_weight": 0.50
    },
    {
      "basket_id": "tips",
      "symbol": "TIP",
      "target_weight": 0.25
    },
    {
      "basket_id": "nominal_bond",
      "symbol": "IEF",
      "target_weight": 0.25
    }
  ],
  "execution_plan": {
    "mode": "backtest",
    "orders": [
      {
        "sequence": 1,
        "symbol": "QQQ",
        "side": "buy",
        "quantity": 100,
        "order_type": "market"
      }
    ]
  }
}
```

The exact order list depends on account state. All quantities must be
explicit numbers. No semantic quantities such as `full_position`,
`half_position`, or `max_affordable` are allowed in the final execution
plan.

### A7: execution_agent

Responsibility:

- Receive only the final `execution_plan`.
- Inspect account state, open orders, positions, and latest prices.
- Submit only the explicit orders in the plan.
- Use market orders only.
- After every submit, call `orders_confirm_order`.
- Stop if confirmation says it is unsafe to continue.

Allowed tools:

- `account_positions`
- `account_portfolio`
- `market_last_price`
- `orders_open_orders`
- `orders_submit_order`
- `orders_confirm_order`

Forbidden:

- Macro classification.
- Basket research.
- Re-ranking symbols.
- Changing investment intent.
- Limit orders.

## 10. Prompt Architecture

This implementation should rewrite prompts for the new workflow instead of
copying old prompts and layering more instructions on top.

The prompt style should be short, role-specific, and boundary-focused.

### Base System Prompt

Include:

- You are operating inside LumiBot.
- Use runtime context and tool outputs as ground truth.
- Do not invent unavailable facts.
- Follow the assigned agent role.
- Do not call tools outside the assigned role.

Do not include:

- General investor philosophy.
- "Prefer doing nothing" style guidance.
- "Avoid overtrading" style guidance.
- DuckDB SQL instructions.
- Hints that force tool use when summaries are available.
- Hints that the purpose is to test turnover.

### Agent-Specific Prompts

Macro prompt:

- Call the mock classifier.
- Report its result.
- Do not choose instruments.

Basket prompts:

- Stay inside the assigned basket.
- Use compact evidence.
- Select one symbol only when target weight is positive.
- Return inactive when target weight is zero.

Portfolio decision prompt:

- Merge upstream reports.
- Respect the macro basket weights.
- Respect basket agent selections.
- Produce strict numeric market-order execution plans.
- Do not redo research.

Execution prompt:

- Execute only the strict execution plan.
- Use market orders.
- Submit and confirm each order before continuing.
- Report submitted, confirmed, or blocked status.

## 11. Runtime Data Flow

One trading iteration should do:

1. Build context:
   - date,
   - mock classifier mode,
   - mock seed,
   - basket universe definitions.
2. Run `macro_allocation_agent`.
3. Run all four basket agents with:
   - date,
   - basket id,
   - basket symbols,
   - target weight,
   - macro allocation report.
4. Run `portfolio_decision_agent` with:
   - macro report,
   - all four basket reports,
   - date.
5. Parse and validate the strict execution plan in Python.
6. Run `execution_agent` only if the plan contains executable orders.
7. Record all agents and dependencies in trace for Agent Replay UI.

## 12. Validation And Guardrails

Python validation should reject:

- missing `execution_plan`,
- non-JSON or unparsable decision output,
- orders without explicit numeric quantity,
- non-market orders,
- symbols outside selected basket outputs,
- execution plans sent to execution agent with broad research reports,
- order tools assigned to any agent except `execution_agent`.

The validation does not need to solve final portfolio optimization.

## 13. Agent Replay UI Expectations

The UI should show:

- 7 agents in the workflow graph.
- `macro_allocation_agent` feeding all four basket agents.
- Four basket agents feeding `portfolio_decision_agent`.
- `portfolio_decision_agent` feeding `execution_agent`.
- The mock regime classifier tool call and output.
- Each basket agent's candidate symbols, target weight, status, and
  selected symbol.
- Portfolio target and strict execution plan.
- Execution order submission and confirmation calls.

If the UI cannot draw the fan-out / fan-in graph, that is a workflow
trace issue to investigate.

## 14. Test Plan

Unit-level tests:

- Mock classifier returns deterministic output for the same date, seed, and
  mode.
- Mock classifier returns weights summing to 1.0.
- Each regime uses the 50 / 25 / 25 / 0 mapping.
- Each basket contains at least five symbols.
- Validation rejects semantic quantities and non-market orders.

Integration tests:

- One daily backtest iteration runs through A1 to A7.
- Execution agent receives only the final execution plan.
- No agent except `execution_agent` has order tools.
- Replay loader can discover the new strategy trace.

Optional but recommended:

- Run a short `cycle` mode backtest over several trading days to force
  quadrant changes and confirm the workflow can produce reallocation
  plans.

## 15. Acceptance Criteria

The feature is complete when:

- A new mock quadrant strategy exists without modifying the existing
  growth-execution test strategy.
- `macro_regime_classifier` exists in mock deterministic form.
- The new strategy can run at least one one-day backtest iteration.
- The replay UI shows all 7 agents.
- Every basket agent receives at least five semantically valid candidate
  symbols.
- Zero-weight baskets return inactive status.
- Non-zero baskets return one selected symbol.
- `portfolio_decision_agent` outputs strict JSON with explicit numeric
  market-order quantities or an explicit no-trade plan.
- `execution_agent` receives only the execution plan and no broad research
  reports.
- Order submission, if attempted, still uses submit-and-confirm behavior.

## 16. Deferred Decisions

Defer these until the skeleton is proven:

- Real macro regime formula.
- Real macro data source.
- More nuanced weight grid than 50 / 25 / 25 / 0.
- Whether basket agents should use momentum, volatility, risk-adjusted
  momentum, drawdown filters, or another selection method.
- Whether basket definitions should move from strategy config into a
  separate metadata tool.
- Whether portfolio decision should eventually become deterministic code
  rather than an agent.
