# Equity-Only LLM Prompt News Universe Cleanup Validation

## Automated Tests

- `python -m pytest tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_manager.py::test_execution_agent_with_execution_plan_execute_tool_receives_stage_d_policy -q`
  - Result after lint follow-up: `18 passed, 1 warning in 4.53s`.
- `python -m ruff check lumibot/example_strategies/ai_trading_team_equity_only_helpers.py lumibot/example_strategies/ai_trading_team_equity_only_llm.py lumibot/components/agents/manager.py tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_manager.py`
  - Result after lint follow-up: `All checks passed!`.
  - Note: ruff still prints the existing config deprecation warning that top-level `select` should move to `lint.select`; that warning is outside this feature's scope.

## Smoke Backtest

- Command: `python scripts\run_ai_trading_team_examples_benchmark.py --strategy equity-only-llm --start 2024-09-05 --end 2024-09-06 --run-frequency daily --max-workers 1 --max-run-attempts 1 --env-file D:\Lumibot\project_notes\API.txt`
- Model: `openai/gpt-5.6-luna`
- Result: process exited `0`; runner printed `{"strategy": "equity-only-llm", "status": "passed"}` and wrote `artifacts\ai_trading_team_example_benchmarks\20260816_133002_919838\summary.json`.
- Environment setup needed before the successful smoke run: installed missing declared runtime dependencies with `python -m pip install "polars>=1.32.3"` and `python -m pip install -r requirements.txt`.

## Benchmark Summary

- `summary.results[0].strategy`: `equity-only-llm`.
- `summary.results[0].status`: `passed`.
- `summary.results[0].artifact_dir`: `C:\Users\Ran\.config\superpowers\worktrees\lumibot\optimize-equity-only-llm\artifacts\ai_trading_team_example_benchmarks\20260816_133002_919838\equity-only-llm`.

## Replay Checks

- Strategy graph: `equity_basket_agent -> execution_agent`.
- Equity symbol count: `50`.
- Equity tools: strategy requests `market_load_history_tables_summary`, `market_last_price`, and `alpaca_news`; runtime available tools include `alpaca_news`.
- Execution tools: `execution_plan_execute`.
- Execution context keys: `date`, `execution_plan`.
- Prompt cleanup: equity prompt contains no `quadrant`, `macro regime`, `commodity`, `TIPS`, or `nominal bond` wording.
- Notable warnings: `none`.

## Follow-Up Validation Fix

The first validation pass exposed pre-existing ruff findings in `lumibot/components/agents/manager.py`: 13 `E501` line-length findings and 1 `B023` loop-variable binding finding. These findings already existed before the Task 3 prompt wording change, but they blocked the feature's touched-file lint command.

Follow-up commit `ff831e98d54126eb451b9b71bfd79b39a638c105` mechanically wrapped the long lines and fixed the MCP closure binding warning without changing prompt text or strategy behavior. Review confirmed the generated MCP tools still bind distinct server/tool names correctly.

## Final Review Hardening

Final review found that the generic `alpaca_news` tool description still contained cross-asset fallback examples such as broad-market, bond, gold, and commodity symbols. Commit `3f419867f8d27d8637d27d65b790ec482d42a59c` keeps the shared news tool behavior unchanged, but gives the equity-only strategy an equity-scoped wrapper description. The equity agent now sees `alpaca_news` as a tool for leading stock candidates from `basket_symbols` only, with explicit "do not broaden" wording.

Additional regression coverage checks both the `ToolDefinition.description` and the bound `BoundTool.description`, because the latter is what the model ultimately receives at runtime.

## Trace Sources

- Equity trace: `artifacts\ai_trading_team_example_benchmarks\20260816_133002_919838\equity-only-llm\cache\agent_runtime\traces\equity_basket_agent\a62958e2b6f952985c34e331454ca6fa370972c869680647b463646fd0ead2d9-20260816053106866446.json`.
- Execution trace: `artifacts\ai_trading_team_example_benchmarks\20260816_133002_919838\equity-only-llm\cache\agent_runtime\traces\execution_agent\9913a642494c9b7e0d47b14b910f4418be7f535cf6774aa80f04b8dc5dea9cb6-20260816053115965171.json`.
