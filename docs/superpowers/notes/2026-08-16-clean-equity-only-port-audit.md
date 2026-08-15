# Clean Equity-Only LLM Mainline Port Audit

Date: 2026-08-16

Base branch: `dev`

Source archive branch: `feature/equity-only-llm-ablation`

## Required Mainline Files

| Path | Archive diff vs `dev` | Clean mainline need |
| --- | --- | --- |
| `lumibot/example_strategies/ai_trading_team_growth_inflation_equity_only_llm.py` | Added, 245 lines | Required. Port as the focused equity-only LLM strategy, adjusted to reuse helpers already present on `dev`. |
| `tests/test_ai_trading_team_growth_inflation_equity_only_llm.py` | Added, 251 lines | Required. Port focused strategy tests and keep assertions aligned with `dev` helper behavior. |
| `scripts/compare_growth_inflation_quadrant_runs.py` | Added, 551 lines | Required if the clean port includes ablation comparison artifacts. Port as a standalone comparison utility rather than merging unrelated archive changes. |
| `tests/test_compare_growth_inflation_quadrant_runs.py` | Added, 263 lines | Required with the comparison utility. Port the tests that cover metrics, CLI output, multi-artifact comparison, and SPY buy-and-hold handling. |
| `scripts/run_ai_trading_team_examples_benchmark.py` | Modified, 10 lines | Required narrowly for the `growth-inflation-equity-only-llm` registry entry. Do not automatically port the archive branch's additional `growth-inflation-quadrant-fixed-etf-baseline` entry or monthly frequency unless separately approved. |
| `lumibot/example_strategies/target_portfolio_to_execution_plan.py` | Modified, 399 lines | Dependency context only for this clean port. `dev` already has the planner module and tool; archive changes are broader sizing-price policy and warning behavior that should not be merged wholesale. |
| `lumibot/example_strategies/ai_trading_team_growth_execution_test.py` | Modified, 135 lines | Dependency context only. `dev` already has buy-sizing and cash-safety validators; archive changes add source-aware order-price plumbing that should be reviewed separately if needed. |
| `lumibot/components/agents/builtins.py` | Modified, 142 lines | Dependency context only. Archive changes add execution-plan cash/price guard behavior and helper functions; do not port automatically for an equity-only strategy mainline add. |

## Existing Dev Helpers

- `validate_decision_buy_sizing`: present on `dev` in `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`, imported/used by `ai_trading_team_mock_growth_inflation_quadrant.py`, and covered by `tests/test_ai_trading_team_growth_execution_test.py`.
- `validate_execution_plan_cash_safety`: present on `dev` in `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`, imported/used by `ai_trading_team_mock_growth_inflation_quadrant.py`, and covered by `tests/test_ai_trading_team_growth_execution_test.py`.
- `validate_execution_plan_matches_planner_result`: present on `dev` in `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py` and covered by `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`.
- `target_portfolio_to_execution_plan`: present on `dev` in `lumibot/example_strategies/target_portfolio_to_execution_plan.py`, with `make_target_portfolio_to_execution_plan_tool()` and existing strategy/test references.
- `execution_plan_execute_payload`: present on `dev` in `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py` and used by strategy/tests.
- `basket_agent_system_prompt`: present on `dev` in `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`.
- `basket_agent_task_prompt`: present on `dev` in `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py` and covered by prompt tests.
- `basket_agent_tools`: present on `dev` in `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`.

## Dependency Port Decisions

| Dependency area | Decision | Reason |
| --- | --- | --- |
| Equity-only LLM strategy | Port cleanly | This is the main feature surface. It creates only the equity basket agent plus execution agent, builds a 100% equity target portfolio, calls the deterministic planner, validates the result, and executes via `execution_plan_execute`. |
| Equity-only strategy tests | Port cleanly | They verify full-weight equity target creation, rejected invalid reports, two-agent initialization, planner handoff, execution handoff, and benchmark registration. |
| Benchmark registry | Port narrowly | Add only the `growth-inflation-equity-only-llm` strategy key needed by this feature. Leave fixed ETF baseline and monthly frequency changes out unless a later task explicitly includes them. |
| Comparison utility and tests | Port as standalone support | The new script is useful for comparing fixed ETF, quadrant LLM, equity-only LLM, and SPY buy-and-hold artifacts. It should be added directly rather than merged with unrelated archive branch source changes. |
| Planner module changes | Do not port wholesale | `dev` already includes `target_portfolio_to_execution_plan`. Archive deltas add daily-open/minute-open sizing policies, fallback warnings, sizing provenance, missing-price blockers, and expanded diagnostics. Those are broader execution-safety changes. |
| Growth execution test strategy changes | Do not port wholesale | `dev` already has the buy-sizing and cash-safety validators needed by the equity-only strategy. Archive deltas add source-aware price lookup helpers that couple to the broader planner/builtins changes. |
| Builtin execution tools changes | Do not port wholesale | Archive deltas add cash-check price snapshots and negative-cash preflight helpers for `execute_plan`. They are not required to add the clean equity-only strategy and should remain a separate reviewed dependency if desired. |
| Existing mock quadrant helpers | Reuse from `dev` | `dev` already exposes the basket prompts/tools, execution payload normalization, planner-result validation, symbol validation, and execution validators the equity-only strategy imports. |

## Notes

- The archive branch `feature/equity-only-llm-ablation` contains four-quadrant experiment changes and execution-tool changes beyond the equity-only LLM ablation. It must not be directly merged into the clean mainline branch.
- Later implementation should cherry-pick or manually port only the required files and narrow registry changes listed above.
- If the broader sizing-price, warning, or negative-cash guard behavior is desired, it should be handled as a separate reviewed task with its own tests and source audit.
