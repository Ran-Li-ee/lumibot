# Agent Prompt Architecture Design

## Purpose

Redesign the prompt architecture for the agent trading workflow so prompts are modular, non-contradictory, and easier to maintain.

The immediate trigger is that the latest one-day `growth-execution-test` backtest completed successfully, but `growth_agent` still called `market_load_history_table` 24 times. This suggests the effective prompt still contains older broad instructions that encourage raw history loading even though newer guidance says to prefer computed summaries.

This design is about prompt architecture and prompt responsibility boundaries. It is not a strategy redesign, a new risk model, a new tool, or a broker execution change.

## Current Problem

Agents do not see prompts as separate files or separate intentions. Each agent receives an effective prompt assembled from multiple sources:

- Base system prompt
- Strategy-specific system prompt
- Tool priority guidance
- General tool rules
- Tool descriptions
- Task prompt
- Runtime context
- Available tool list and tool definitions

Because these sources were built incrementally, they now contain overlapping and sometimes competing instructions.

Observed examples:

- A broad base instruction says to load recent price history for any asset under consideration.
- Newer guidance says to prefer computed summaries and not request raw historical rows by default.
- Tool descriptions still imply that `market_load_history_table` is the normal setup step before DuckDB analysis.
- Decision and execution agents can receive historical-data guidance they do not need.
- Strategy prompts, output contracts, sizing rules, and tool rules are partially mixed together.

The result is not a hard runtime error, but it increases token use and makes LLM behavior harder to predict.

## Design Principles

### Single Responsibility Per Prompt Layer

Each prompt layer should answer one question:

- Runtime layer: what is universally true about this run?
- Strategy layer: what is this strategy trying to do?
- Tool layer: how should tools be selected?
- Agent layer: what is this agent responsible for?
- Output layer: what shape must this agent output?
- Sizing layer: how should decision-level order quantities be calculated?

### No Cross-Layer Repetition

Rules should appear in the lowest layer that can own them, and should not be repeated elsewhere unless there is a deliberate safety reason.

Examples:

- Look-ahead safety belongs in global runtime rules.
- ETF relative-strength intent belongs in strategy objective.
- `market_load_history_tables_summary` priority belongs in tool use policy.
- Strict JSON schema belongs in decision output contract.
- 98% buy sizing belongs only in decision sizing rules.

### Agent-Specific Prompt Assembly

Agents should not all receive the same prompt modules.

Each agent should receive only the modules it needs to do its job.

### Prefer Removal Over Prompt Patching

This redesign should remove obsolete or misleading instructions instead of adding more counter-instructions.

If a sentence causes agents to overuse a tool, the preferred fix is to delete or rewrite that sentence at the source, not add another sentence later saying "do not overuse it."

## Proposed Prompt Modules

## 1. Global Runtime Rules

This module is sent to all agents, but may have a minimal variant for execution-only agents.

It should contain:

- Runtime context is the ground truth for current account state, time, mode, and positions.
- Tool results outrank model memory.
- Do not invent facts not present in context or tool output.
- Current simulated datetime is a hard wall in backtesting.
- Do not use future data.
- Context pruning is a normal runtime mechanism.
- Pruning is not evidence failure by itself.
- If a pruned result is essential, call a targeted tool again.

It should not contain:

- Default investor style.
- Conservative trading preference.
- Trade/no-trade bias.
- Price-history tool selection.
- DuckDB guidance.
- Order schema.
- Position sizing rules.
- Agent-specific responsibilities.

## 2. Strategy Objective

This module describes the strategy-level objective shared by the workflow.

For `growth-execution-test`, it should contain:

- The strategy is relative-strength ETF account management.
- Rank the given ETF universe using current available evidence.
- Do not assume any ETF is the default holding.
- Do not favor current holdings merely because they are already held.
- If the account holds only cash or cash-like exposure, deploying into the strongest candidate is allowed when evidence supports it.
- If the account holds an ETF, compare that holding against the strongest candidate.

It should not contain:

- Tool call instructions.
- Raw history or DuckDB instructions.
- JSON schema.
- Order fields.
- 98% cash sizing.
- Execution-agent behavior.

## 3. Tool Use Policy

This module defines tool selection priority by tool category.

It should be included only when the agent has relevant tools.

### 3.1 Account Tools

Contains:

- Use `account_positions` to inspect current holdings.
- Use `account_portfolio` to inspect cash and portfolio value.
- Non-hold decision agents must use both before producing an actionable plan.
- Execution agents must inspect current positions and portfolio before submitting orders.

Does not contain:

- Strategy ranking logic.
- Output schema.

### 3.2 Price And History Tools

Contains:

- Use `market_load_history_tables_summary` as the default tool for multi-symbol price-history comparison.
- Use `market_load_history_table` only for targeted single-symbol follow-up when summary data is missing, contradictory, or insufficient.
- Do not load raw history tables for every symbol when summary rankings already answer the task.
- Use `market_last_price` for current/reference price checks, but do not assume it is a guaranteed fill price in daily backtests.

Does not contain:

- DuckDB SQL syntax.
- Strategy-specific trade decisions.
- Order quantity rules.

### 3.3 DuckDB Tools

Contains:

- Use `duckdb_query` only for targeted follow-up analysis not already covered by computed summaries or rankings.
- Do not treat DuckDB as a required step in every research workflow.
- Do not write SQL merely to appear more rigorous.

Does not contain:

- A long SQL tutorial.
- Repeated table schema details that tools already return.
- Strategy decisions.

### 3.4 Execution Tools

Contains:

- Only execution-capable agents may submit, cancel, or modify orders.
- `orders_submit_order` executes explicit order fields from `execution_plan.orders`.
- Execution tools are not research tools.

Does not contain:

- Relative-strength ranking.
- Buy sizing policy.
- Growth-report interpretation.

## 4. Agent Role Prompts

Each agent receives exactly one role prompt.

### 4.1 Growth Agent Role

Contains:

- Rank the ETF universe.
- Use computed summary metrics as default evidence.
- Identify the strongest candidate.
- Compare current ETF holding against the strongest candidate if there is a current ETF holding.
- State whether the holding should be kept, reduced, or replaced.
- Make a research recommendation.
- Do not place orders.
- Do not calculate final executable share quantities.
- Do not exhaustively load raw history tables when summary evidence is sufficient.

Does not contain:

- Decision JSON contract.
- 98% cash sizing.
- Order fields.
- Execution-agent rules.

### 4.2 Decision Agent Role

Contains:

- Convert `growth_report` and current account state into a concrete decision.
- Choose exactly one decision type: `hold`, `buy`, `rotate`, `reduce`, or `close`.
- Produce a strict `execution_plan`.
- Use explicit numeric share quantities.
- Do not place orders.
- Do not perform broad ETF research again.
- Do not use DuckDB or raw history tools by default.

Does not contain:

- Growth ranking instructions.
- Raw history tool priority.
- Execution tool behavior.
- Long investment philosophy.

### 4.3 Execution Agent Role

Contains:

- Execute only the provided `execution_plan`.
- Treat `execution_plan.orders` as authoritative.
- Do not read or infer investment reasons.
- Do not re-rank candidates.
- Do not substitute symbols.
- Do not change order sequence, side, symbol, quantity, or order type.
- Inspect positions, portfolio, open orders, and latest price before submitting orders.
- Submit or block each order for execution-level reasons only.

Does not contain:

- Strategy objective details beyond "execute this strategy's plan."
- Growth report.
- Relative-strength logic.
- Raw history guidance.
- DuckDB guidance.
- 98% sizing rule.

## 5. Output Contracts

Output contracts define response shape. They should be separate from role descriptions.

### 5.1 Growth Report Contract

Contains:

- Ranked ETF list.
- Strongest candidate.
- Evidence summary.
- Current holding comparison when relevant.
- Research recommendation.
- Short `RESULT:` summary.

Does not contain:

- Executable order JSON.
- Share quantity calculations.

### 5.2 Decision JSON Contract

Contains:

```json
{
  "decision": {
    "type": "hold | buy | rotate | reduce | close",
    "from": "symbol_or_cash",
    "to": "symbol_or_cash",
    "reason_brief": "short human reason"
  },
  "execution_plan": {
    "schema_version": 1,
    "intent": "hold | enter_position | rotate | reduce_position | close_position",
    "orders": []
  }
}
```

Order fields:

- `sequence`
- `symbol`
- `side`
- `quantity_mode`
- `quantity`
- `asset_type`
- `order_type`
- `time_in_force`
- Optional bounded-price fields such as `limit_price`, `stop_price`, and `stop_limit_price`

Strict rules:

- Return only one valid JSON object.
- Do not include markdown.
- Do not include `RESULT` text.
- Do not include prose after the JSON.
- `quantity` must be a positive numeric share quantity for executable orders.
- Do not use semantic quantity fields such as `full_position`, `current_position`, `max_affordable_cash`, or `max_affordable_after_prior_sells`.
- Do not output `cash_buffer_pct`.

Does not contain:

- Research methodology.
- Raw history tool instructions.
- Execution reporting style.

### 5.3 Execution Report Contract

Contains:

- For each sequence, report `submitted` or `blocked`.
- If blocked, report the execution-level blocker.
- Finish with a short `RESULT:` sentence.

Does not contain:

- Candidate ranking.
- Investment analysis.
- Recomputed sizing.

## 6. Sizing And Order Construction Rules

This module is sent only to decision agents that create executable plans.

Contains:

- Use numeric whole-share quantities.
- For selling all or part of a position, calculate quantity from account tool output.
- For buy sizing, choose `order_type` first.
- Choose `sizing_price` according to `order_type`.
- For market buys, use a conservative sizing price based on available price evidence; it may be higher than `market_last_price` in daily backtests.
- For limit buys, use `limit_price`.
- For stop-limit buys, use `stop_limit_price` or the final bounded execution price.
- Use about 98% of available cash after any prior sell orders to calculate maximum whole-share buy quantity.
- The 98% rule is internal sizing logic only.
- Do not output `cash_buffer_pct` or buffer fields.
- Never produce an order plan expected to create negative cash.
- For rotate decisions, sell before buying the replacement.

Does not contain:

- ETF ranking.
- Historical-data tool selection.
- Execution-agent reporting.
- Broad risk philosophy.

## Agent Module Matrix

| Module | Growth Agent | Decision Agent | Execution Agent |
|---|---:|---:|---:|
| 1. Global Runtime Rules | Yes | Yes | Minimal |
| 2. Strategy Objective | Yes | Yes | Minimal or No |
| 3.1 Account Tool Policy | Yes | Yes | Yes |
| 3.2 Price/History Tool Policy | Yes | No, unless needed | No |
| 3.3 DuckDB Tool Policy | Yes, if tool enabled | No | No |
| 3.4 Execution Tool Policy | No | No | Yes |
| 4. Agent Role | Growth only | Decision only | Execution only |
| 5. Output Contract | Growth report | Decision JSON | Execution report |
| 6. Sizing Rules | No | Yes | No |

## Required Deletions Or Rewrites

The implementation should remove or rewrite broad instructions that conflict with the new architecture.

### Delete Or Rewrite From Base Prompt

- Broad instruction to "load recent price history for any asset you are considering."
- Broad instruction implying DuckDB analysis is part of the standard evidence stack for every material equity decision.
- Default investor-policy language that conflicts with strategy-specific prompt behavior or bloats non-research agents.
- Execution and sizing details from prompts sent to non-execution or non-decision agents.

### Rewrite Tool Descriptions

`market_load_history_tables_summary` should be described as the default multi-symbol comparison tool.

`market_load_history_table` should be described as targeted single-symbol follow-up, not the default for every symbol.

`duckdb_query` should be described as targeted follow-up after summaries prove insufficient, not a required research step.

### Restrict Historical Guidance

Historical-data priority guidance should not be sent to execution agents.

Decision agents should not receive raw history or DuckDB guidance unless their actual task requires those tools.

## Expected Prompt Assembly

### Growth Agent

```text
Global Runtime Rules
+ Strategy Objective
+ Account Tool Policy
+ Price/History Tool Policy
+ DuckDB Tool Policy if enabled
+ Growth Agent Role
+ Growth Report Contract
+ Runtime Context
+ Relevant Tool Definitions
+ Task Prompt
```

### Decision Agent

```text
Global Runtime Rules
+ Strategy Objective
+ Account Tool Policy
+ Decision Agent Role
+ Decision JSON Contract
+ Sizing And Order Construction Rules
+ Runtime Context
+ Relevant Tool Definitions
+ Task Prompt
```

### Execution Agent

```text
Minimal Global Runtime Rules
+ Account Tool Policy
+ Execution Tool Policy
+ Execution Agent Role
+ Execution Report Contract
+ Runtime Context
+ Relevant Tool Definitions
+ Task Prompt
```

## Out Of Scope

This spec does not:

- Change the ETF universe.
- Add or remove agents.
- Add new tools.
- Remove DuckDB.
- Disable `market_load_history_table`.
- Change broker execution behavior.
- Change no-negative-cash enforcement.
- Change the structured `execution_plan` parser.
- Optimize model choice or model pricing.

## Validation Plan

### Static Prompt Tests

Add or update tests that inspect assembled prompts for each agent.

Growth agent prompt must:

- Include summary-first price-history guidance.
- Include `market_load_history_tables_summary` as default multi-symbol comparison.
- Say raw history tables are targeted follow-up only.
- Not include decision JSON schema.
- Not include 98% buy sizing rules.

Decision agent prompt must:

- Include strict decision JSON contract.
- Include sizing rules.
- Include account tool requirements.
- Not include raw history table priority.
- Not include DuckDB guidance.
- Not include execution tool instructions.

Execution agent prompt must:

- Include execution-only role.
- Include execution report contract.
- Include order tool policy.
- Not include growth report.
- Not include history summary guidance.
- Not include DuckDB guidance.
- Not include 98% sizing rules.
- Not include `cash_buffer_pct`.

### Tool Description Tests

Add or update tests that verify:

- `market_load_history_tables_summary` is described as default for multi-symbol comparison.
- `market_load_history_table` is described as targeted single-symbol follow-up.
- `duckdb_query` is described as targeted follow-up only when summaries are insufficient.

### Integration Trace Test

Run a one-day `growth-execution-test` backtest with `openai/gpt-5.4-mini`.

Validate from trace:

- Workflow completes through growth, decision, and execution agents.
- Decision agent emits valid strict JSON.
- Execution agent either submits the order or blocks for an execution-level reason.
- Execution plan sent to execution agent does not include `cash_buffer_pct`.
- `duckdb_query` is not called unless a targeted follow-up need appears.
- `market_load_history_table` calls are materially reduced from the observed 24-call pattern, ideally to zero or a small targeted count when `market_load_history_tables_summary` already answers the ranking task.

### UI Validation

Open Agent Workflow Replay UI and inspect the generated run.

Confirm:

- Agent prompts are easier to read by module.
- Tool-call flow remains visible.
- Growth agent tool calls show summary-first behavior.
- Decision agent and execution agent are not polluted by irrelevant history/DuckDB guidance.

## Acceptance Criteria

This feature is complete when:

- Prompt modules are implemented with clear ownership boundaries.
- Prompt assembly sends only relevant modules to each agent.
- Static tests prove banned prompt sections are absent from irrelevant agents.
- Tool descriptions match the summary-first architecture.
- One-day `growth-execution-test` backtest completes successfully.
- Generated trace shows no warnings or SQL errors.
- Growth agent no longer defaults to loading raw history tables for every ETF when summary evidence is available.
- Execution agent receives only execution-relevant prompt material and a clean `execution_plan`.

