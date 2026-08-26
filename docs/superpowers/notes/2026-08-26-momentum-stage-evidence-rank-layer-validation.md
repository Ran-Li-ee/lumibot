# Momentum Stage Evidence Rank Layer Validation

## Commands

- `python -m pytest tests/test_agent_history_summary.py tests/test_agent_manager.py tests/test_ai_trading_team_equity_only_llm.py tests/test_dynamic_equity_portfolio_constructor.py tests/test_agent_replay_ui_formatters.py -q`
- `python -m ruff check lumibot/components/agents/history_summary.py lumibot/components/agents/duckdb_tools.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py lumibot/example_strategies/dynamic_equity_portfolio_constructor.py lumibot/components/agents/replay_ui/formatters.py tests/test_agent_history_summary.py tests/test_ai_trading_team_equity_only_llm.py tests/test_dynamic_equity_portfolio_constructor.py tests/test_agent_replay_ui_formatters.py`
- `git diff --check dc63471f^..HEAD`
- `python scripts/run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2024-09-05 --end 2024-09-06 --run-frequency weekly --weekly-run-weekday THU --env-file D:\Lumibot\project_notes\API.txt --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 240` with `AI_TRADING_TEAM_MODEL=openai/gpt-5.6-luna`
- `python scripts/agent_trace_ui.py --trace-root artifacts\ai_trading_team_example_benchmarks\20260826_223040_846941\qqq-historical-equity-only-llm\cache\agent_runtime`

## Results

- Pytest: `193 passed, 3 warnings`.
- Ruff on feature-owned files: passed.
- Full plan-listed ruff that includes `lumibot/components/agents/builtins.py`: blocked by pre-existing long-line and `B010` issues in that file.
- Diff whitespace check: passed.
- Smoke backtest: passed.
- Smoke artifact: `artifacts\ai_trading_team_example_benchmarks\20260826_223040_846941\qqq-historical-equity-only-llm`.

## Trace Checks

- `evidence_profile="momentum_stage"` found in `equity_basket_agent` trace: yes.
- Old `by_composite_score` / `by_momentum_composite` exposed to equity trace: no.
- Candidate rows include `stage_warning_flags`: yes.
- Benchmark context includes `QQQ`, `SPY`, and `return_126`: yes.
- Replay UI `/api/dataset` includes the newest smoke run and momentum-stage fields when launched with the smoke trace root: yes.

## Notes

- The first smoke attempt failed before running because `AI_TRADING_TEAM_MODEL` was not set and the runner defaulted to `gemini-3.1-flash-lite`.
- Setting `AI_TRADING_TEAM_MODEL=openai/gpt-5.6-luna` made the smoke run use the intended OpenAI model.
- This validation did not run a long performance backtest.
