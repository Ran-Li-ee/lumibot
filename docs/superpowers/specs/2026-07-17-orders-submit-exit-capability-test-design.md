# Orders Submit Exit Capability Test Design

## Goal

Create a small experimental AI trading team strategy that tests whether Lumibot's existing `orders_submit_order` tool can reliably perform exit, reduce, and rotate behavior through multi-step tool use, without adding any new exit-position tools.

The purpose is not to improve investment performance. The purpose is to answer a narrower engineering question:

> If the analysis task and execution task are separated into different agents, can a dedicated execution agent use the existing native order tools to move the account toward a structured trading plan?

## Background

The native `orders_submit_order` tool already supports these order sides:

- `buy`
- `sell`
- `buy_to_open`
- `sell_to_close`
- `sell_short`
- `buy_to_cover`

For ordinary stock and ETF long positions, `side="sell"` can reduce or close a position. Therefore many exit-like behaviors can already be represented as one or more calls to `orders_submit_order`:

- reduce a position by selling part of it
- close a position by selling all of it
- rotate from one ETF to another by selling the old ETF and buying the new ETF
- liquidate multiple positions by submitting one sell order per held symbol

The current Ray Dalio idea-meritocracy demo does not isolate this execution problem. Its `trader` agent reads several reports, forms its own conclusion, and also places orders. If that workflow fails to exit or rotate cleanly, it is hard to tell whether the problem was investment reasoning, ambiguous instructions, or low-level order execution.

This experiment removes that ambiguity by splitting the work.

## Non-Goals

This experiment does not:

- add `orders_close_position`
- add `orders_reduce_position`
- add any new broker/order primitive
- modify the original Ray Dalio demo
- optimize investment strategy performance
- test options, shorts, leverage, futures, crypto, or complex multi-asset execution
- merge the existing trace-completeness feature branch into this branch

## Strategy Copy

Create a new example strategy by copying the Ray Dalio idea-meritocracy demo into a separate file.

Recommended file:

```text
lumibot/example_strategies/ai_trading_team_growth_execution_test.py
```

The original file must remain unchanged:

```text
lumibot/example_strategies/ai_trading_team_ray_dalio_idea_meritocracy.py
```

This keeps the author-style demo intact and makes the new file clearly experimental.

## Agent Workflow

The experimental workflow should have three agents:

```text
growth_agent
  -> decision_agent
  -> execution_agent
```

### growth_agent

Purpose:

- Analyze the ETF universe from a growth-regime point of view.
- Explain which ETF looks strongest and whether currently held ETFs should be kept, reduced, or replaced.

Permissions:

- Read-only tools only.
- No order submission, cancellation, or modification tools.

Prompt intent:

```text
Analyze the ETF universe from a growth/regime perspective. Identify the strongest candidate and explain whether the current holding should be kept, reduced, or replaced. Do not place orders.
```

### decision_agent

Purpose:

- Convert the growth report and current account state into a concrete trading plan.
- Do not place orders.
- Do not reintroduce removed inflation, liquidity, debt, or disagreement agents.

Permissions:

- Read-only tools only.
- No order submission, cancellation, or modification tools.

Prompt intent:

```text
Use the growth report, current positions, portfolio state, latest prices, and open orders to produce a concrete trading plan. Do not place orders. Output a clear structured plan for the execution agent.
```

### execution_agent

Purpose:

- Execute the structured trading plan using existing native order tools.
- Do not redo the investment analysis.
- Inspect account state and order state before submitting orders.
- Use only the existing native tools, especially `orders_submit_order`, to perform buys and sells.

Permissions:

- Trading tools enabled.
- Can use:
  - `account_positions`
  - `account_portfolio`
  - `market_last_price`
  - `orders_open_orders`
  - `orders_cancel_order`
  - `orders_modify_order`
  - `orders_submit_order`

Prompt intent:

```text
Execute the provided trading plan. Do not reinterpret the investment thesis. First inspect portfolio, positions, open orders, and latest prices. Then use orders_submit_order as needed to move the account toward the target state.
```

## Removed Agents

The experimental strategy should remove:

- `inflation_agent`
- `debt_liquidity_agent`
- `thoughtful_disagreement`

Removing them is not enough by itself. All prompts and context keys must also stop referring to:

- inflation report
- liquidity report
- debt report
- disagreement report
- three views
- four reports
- idea meritocracy

This avoids hidden prompt contradictions and ghost dependencies.

## Trading Plan Shape

The decision agent should produce a structured plan. The first implementation can use text containing JSON-like structure, rather than requiring strict machine parsing.

Target shape:

```json
{
  "plan_type": "hold | rotate | reduce | close | buy",
  "target_symbol": "QQQ",
  "current_position_assessment": "keep | reduce | close | none",
  "exit_actions": [
    {
      "symbol": "SPY",
      "action": "close_position | reduce_position",
      "quantity_basis": "all_current_shares | partial_current_shares",
      "reason": "Short explanation."
    }
  ],
  "entry_actions": [
    {
      "symbol": "QQQ",
      "action": "buy_with_available_cash",
      "cash_allocation_pct": 0.95,
      "reason": "Short explanation."
    }
  ],
  "do_not_trade_if": [
    "current position already matches target",
    "latest price is unavailable",
    "order would exceed available cash"
  ]
}
```

The execution agent should treat this as an instruction document, not as a reason to re-analyze the market.

## Test Window

Run a 10-trading-day backtest.

Recommended initial window:

```text
2026-04-07 to 2026-04-21
```

The exact end date may be adjusted if the backtesting engine treats the end timestamp as exclusive or if market holidays affect the number of iterations. The acceptance requirement is about approximately 10 trading iterations, not those exact calendar dates.

## Evaluation Criteria

The experiment is successful if the traces show the execution agent can correctly use native order tools to perform basic account transitions.

### Required observations

For each execution-agent run, inspect whether it:

- calls `account_positions` before trading
- calls `account_portfolio` before trading
- calls `market_last_price` for ordered symbols
- checks `orders_open_orders` before placing new orders
- uses `orders_submit_order` with `side="sell"` when reducing or closing a long ETF position
- uses `orders_submit_order` with `side="buy"` when entering a new long ETF position
- avoids submitting orders when the current position already matches the plan
- avoids selling more shares than are held
- avoids buying without available cash
- avoids repeated duplicate orders

### Failure signals

The experiment should be considered a failure or partial failure if traces show:

- `ORDER_READINESS_REQUIRED` errors before normal order submission
- old positions remain after the plan clearly requested a close
- the execution agent buys the new symbol but forgets to sell the old symbol in a rotate plan
- the execution agent sells more than the current holding
- the execution agent trades a symbol not present in the plan
- the execution agent redoes investment analysis instead of executing the plan
- repeated orders are submitted for an already satisfied target state

## Trace and UI Expectations

This branch starts from `dev`, not from the trace-completeness feature branch. Therefore the UI may not show the newest trace completeness fields from the separate feature branch.

That is acceptable for this experiment.

The minimum required trace/UI visibility is:

- each agent run is visible
- prompts and context are visible enough to confirm no ghost agents remain
- tool calls are visible enough to inspect `orders_submit_order`
- order payloads are visible enough to see `side`, `symbol`, `quantity`, and order status

If this proves insufficient, the trace-completeness feature branch can be merged later, but it should not be required for the first version of this experiment.

## Expected Outcome

This experiment should answer one question:

> Do we need new dedicated exit tools now, or is the native `orders_submit_order` sufficient when execution is isolated in its own agent?

Decision rule:

- If the execution agent reliably performs hold, close, reduce, and rotate behaviors with native tools, do not build exit tools yet.
- If failures cluster around translating a clear plan into sell orders, then consider adding semantic tools such as `orders_close_position` or `orders_reduce_position`.

## Acceptance Criteria

The spec is implemented when:

1. A new copied experimental strategy exists.
2. The original Ray Dalio strategy is unchanged.
3. The new strategy has only `growth_agent`, `decision_agent`, and `execution_agent`.
4. Prompts and context no longer refer to removed agents.
5. The execution agent has trading permission and native order tools.
6. The decision agent has no trading permission.
7. A 10-trading-day backtest can be run.
8. The resulting traces can be inspected to evaluate native exit/rotation behavior.

