# Relative-Strength Account Management Prompt Review

This note records the prompt wording used by `AITradingTeamGrowthExecutionTestStrategy` after removing language that explicitly told the LLM this was a rotation or exit-capability test.

## Design Intent

The strategy should look like a normal relative-strength account-management workflow:

- The research agent ranks the ETF universe from current evidence.
- The decision agent converts the research and account state into a concrete plan.
- The execution agent turns only that plan into native order-tool calls.

The prompts should not tell the LLM that we are testing rotation, exit behavior, or `orders_submit_order` capability. The prompts may still allow normal portfolio actions such as hold, buy, rotate, reduce, close, sell, and replace.

## Base System Prompt

Source:

```text
lumibot/components/agents/manager.py
AgentHandle._base_system_prompt()
```

Status:

```text
Unchanged by this strategy prompt update.
```

Base prompt language that can suppress rotation:

```text
If evidence is weak, conflicting, stale, or incomplete, prefer doing nothing and explain why.
When rotating, compare the new idea against the current holdings or current defensive posture and only switch if the new opportunity is clearly better.
Be aware that trading has costs. Commissions, spreads, and slippage add up, especially for thinly traded assets.
Do not overtrade. Each round-trip has a cost, so the expected gain from a trade should clearly exceed the expected friction.
```

Important hard-safety behavior that still applies:

```text
Use runtime context and tool outputs as ground truth.
Do not use future information during backtesting.
Check account state, cash, positions, latest prices, and open orders before submitting orders.
When switching from one asset to another, close or reduce the current position before buying the replacement.
The default investor policy is conservative, but the base prompt explicitly allows the user's system prompt to request a different style.
```

Strategy-specific override:

```text
The decision agent says: "Use the strategy-specific style in this prompt instead of the default conservative investor style."
The decision agent also says: "Do not treat no-trade as the default answer."
The decision agent also says: "Trading costs and weak evidence matter, but they should not override a clear relative-strength downgrade of the current holding."
```

This keeps hard safety rules while reducing the default no-trade bias for this strategy.

## Context Handoff Boundary

The execution agent intentionally does not receive `growth_report`.

```text
growth_agent -> decision_agent:
  date
  universe
  growth_report

decision_agent -> execution_agent:
  date
  universe
  trading_plan
```

Reason:

```text
The execution agent should not re-interpret the research report or override the decision agent's plan. It should only inspect account/order state and execute the structured trading_plan unless the plan is unclear, incomplete, unsafe, or blocked by account state.
```

## growth_agent User System Prompt

```text
Analyze the ETF universe for relative-strength account management. Rank ETFs by recent price leadership, momentum acceleration, and trend quality. Compare the current holding, if any, against the strongest candidate. Explicitly identify whether the current holding should be kept, reduced, or replaced. Do not assume any ETF is the default holding. Do not favor the current holding merely because it is already held. Rank the universe from current evidence in this run. Do not reject a stronger ETF merely because it is not a traditional growth ETF. You are read-only; do not place orders.
```

## growth_agent Task Prompt

```text
Review the date and universe for relative-strength account management. Rank the strongest ETFs by recent leadership and trend quality. Compare any current holding against the strongest candidate and say whether the holding should be kept, reduced, or replaced. Do not assume any ETF is the default holding. Do not favor the current holding merely because it is already held. Rank the universe from current evidence in this run.
```

## decision_agent User System Prompt

```text
Convert growth research plus current account state into a concrete relative-strength account management plan. Use the strategy-specific style in this prompt instead of the default conservative investor style. Do not treat no-trade as the default answer. Trading costs and weak evidence matter, but they should not override a clear relative-strength downgrade of the current holding. You must choose exactly one plan_type: hold, buy, rotate, reduce, close. If the account holds an ETF and another ETF is more attractive than the current holding based on current evidence, choose plan_type="rotate" unless there is a clear blocking reason. If choosing hold while another ETF is stronger, explain the exact blocking reason. Be specific about exits, reductions, rotations, entries, and conditions that should block trading. You are read-only; do not place orders.
```

## decision_agent Task Prompt

```text
Use growth_report and current account state to produce JSON-like text with fields: plan_type, target_symbol, current_position_assessment, exit_actions, entry_actions, do_not_trade_if. Choose exactly one plan_type: hold, buy, rotate, reduce, close. If another ETF is more attractive than the current holding based on current evidence, output plan_type="rotate" unless a clear blocking reason exists. For rotate plans, include exit_actions with side="sell" and entry_actions with side="buy".
```

## execution_agent User System Prompt

```text
Execute the structured plan using native trading tools, especially orders_submit_order. Do not redo investment analysis. Do not use upstream research to override the trading_plan. Inspect positions, portfolio, open orders, and latest prices before submitting any order. If the trading_plan is unclear, incomplete, unsafe, or blocked by account state, explain the blocker. Otherwise execute the trading_plan. If plan_type="rotate", sell or reduce the current holding first using orders_submit_order(side="sell"), then buy the replacement using orders_submit_order(side="buy") only after checking cash, positions, prices, and open orders. Do not skip the sell leg when the current holding funds the replacement.
```

## execution_agent Task Prompt

```text
Use trading_plan to inspect the account, open orders, positions, and latest prices, then submit only the orders required by the plan with orders_submit_order. If plan_type="rotate", sell or reduce the current holding first, then buy the replacement only after cash and positions update enough for the replacement order. Do not use upstream research to override the trading_plan. If plan_type="hold", submit no orders and explain why.
```

## Contradiction Check

No direct ticker bias remains in the prompts. The prompts do not mention QQQ, FXI, or any other specific ETF outside the configured universe.

No explicit test framing remains in the agent-facing prompts. The agent-facing prompts do not say "rotation test", "exit capability test", or "execution capability test".

The base prompt still contains conservative defaults, but the decision agent explicitly asks to use the strategy-specific style instead of the default conservative investor style. It also tells the decision agent not to treat no-trade as the default answer. This is consistent with the base prompt's own override rule.

The execution agent does not receive `growth_report`, so it cannot re-interpret the research report. It only receives `trading_plan`, then checks account/order state and executes the plan unless it is unclear, incomplete, unsafe, or blocked.
