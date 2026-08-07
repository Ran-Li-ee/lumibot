# Numeric Execution Plan Quantities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the growth execution test handoff give `execution_agent` explicit numeric share quantities instead of semantic quantity modes.

**Architecture:** Keep the change scoped to `AITradingTeamGrowthExecutionTestStrategy`. Tighten parser validation, update decision/execution prompts, update tests, then run a one-day benchmark and inspect trace context.

**Tech Stack:** Python, pytest, existing Lumibot agent trace replay tooling.

---

### Task 1: Parser And Prompt Tests

**Files:**
- Modify: `tests/test_ai_trading_team_growth_execution_test.py`

- [ ] Update parser fixtures so non-hold examples use `quantity_mode: "shares"` and numeric `quantity`.
- [ ] Add rejection tests for `current_position`, `max_affordable_cash`, `max_affordable_after_prior_sells`, and `full_position`.
- [ ] Add prompt assertions that decision agent must call `account_positions`, `account_portfolio`, and `market_last_price` before final JSON.
- [ ] Add prompt assertions that execution-agent orders already contain final numeric quantities.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py -q` and confirm failures before implementation if practical.

### Task 2: Parser And Prompt Implementation

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`

- [ ] Restrict non-hold order validation to `quantity_mode: "shares"`.
- [ ] Require positive finite `quantity` for every non-hold order.
- [ ] Update decision-agent system prompt and task prompt to require account/portfolio/price tool calls before final JSON.
- [ ] Update rotate instructions so sequence 1 sells exact current shares and sequence 2 buys exact calculated shares.
- [ ] Update execution-agent prompt so it submits provided numeric quantities and does not resolve semantic sizing.
- [ ] Run targeted pytest and focused ruff.

### Task 3: One-Day Backtest Verification

**Files:**
- No source edits expected.

- [ ] Load OpenAI API key from `project_notes/API.txt` without printing it.
- [ ] Run `growth-execution-test` for one day.
- [ ] Inspect latest trace with replay loader.
- [ ] Verify execution-agent context contains only `date` and `execution_plan`.
- [ ] Verify all execution orders use `quantity_mode: "shares"` with numeric `quantity`.
- [ ] Report whether execution_agent got the expected input.
