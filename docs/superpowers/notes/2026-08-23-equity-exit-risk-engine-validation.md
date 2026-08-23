# Equity Exit Risk Engine Validation

## Scope

Validated the deterministic equity exit-risk engine feature from `docs/superpowers/specs/2026-08-23-equity-exit-risk-engine-design.md`.

This validation covers:

- Strict `risk_exit` execution plan support.
- Initial stop and trailing stop deterministic plan generation.
- Strategy state integration for scheduled buys, full scheduled sells, and daily risk checks.
- Execution tool compatibility with `risk_exit` sell plans.
- Prompt boundary cleanup so LLM agents do not calculate stop-loss or take-profit levels.
- One short QQQ historical equity-only smoke backtest.

## Test Commands

```powershell
python -m pytest tests/test_equity_trailing_stop_to_execution_plan.py tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_tool_permissions.py -k "equity or trailing_stop or execution_plan_execute or risk_exit" -q
python -m pytest tests/test_dynamic_equity_portfolio_constructor.py tests/test_ai_trading_team_equity_only_llm.py tests/test_equity_trailing_stop_to_execution_plan.py -q
python -m ruff check lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py lumibot/example_strategies/ai_trading_team_equity_only_llm.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py tests/test_equity_trailing_stop_to_execution_plan.py tests/test_ai_trading_team_equity_only_llm.py
python -m ruff check --select F,E9 lumibot/components/agents/builtins.py tests/test_agent_tool_permissions.py
```

## Smoke Backtest

```powershell
$env:AI_TRADING_TEAM_MODEL = "gpt-5.6-luna"
python scripts\run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2024-09-05 --end 2024-09-06 --run-frequency weekly --weekly-run-weekday THU --env-file D:\Lumibot\project_notes\API.txt --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 240
```

## Results

- Focused selected tests: `109 passed, 77 deselected, 1 warning in 15.90s`.
- Full focused strategy tests: `67 passed, 1 warning in 3.33s`.
- Ruff plan scope: `All checks passed!`.
- Ruff syntax/undefined guard for execution-tool files: `All checks passed!`.
- Smoke status: `passed`.
- Smoke wall time: `42442 ms`.
- Smoke artifact root: `C:\Users\Ran\.config\superpowers\worktrees\lumibot\optimize-equity-only-llm\artifacts\ai_trading_team_example_benchmarks\20260823_165249_909988`.
- Smoke strategy artifact: `C:\Users\Ran\.config\superpowers\worktrees\lumibot\optimize-equity-only-llm\artifacts\ai_trading_team_example_benchmarks\20260823_165249_909988\qqq-historical-equity-only-llm`.
- Smoke summary file: `C:\Users\Ran\.config\superpowers\worktrees\lumibot\optimize-equity-only-llm\artifacts\ai_trading_team_example_benchmarks\20260823_165249_909988\summary.json`.
- Exit trigger occurred naturally: `no`. The smoke run started from cash, had no prior equity positions, and `rg -n "risk_exit|exit_checks|initial_stop_pct|trailing_stop_pct|deterministic local exit-risk engine" artifacts\ai_trading_team_example_benchmarks\20260823_165249_909988 -S` found no natural stop trigger evidence.
- Scheduled equity workflow still ran when no exit trigger occurred: `yes`. `settings.json` recorded `agent_equity_basket_agent_calls: 1`, `agent_execution_agent_calls: 1`, and `agent_execution_agent_tool_calls: 1`.
- Smoke trades: five buy orders filled at `2024-09-05 09:30:00-04:00` for `ADBE`, `CTAS`, `FTNT`, `ISRG`, and `KDP`.
- Smoke final positions included USD cash plus `ADBE 33`, `CTAS 95`, `FTNT 251`, `ISRG 39`, and `KDP 514`.

## Notes

- Daily risk exits are close-based checks in this first version, not broker-native intraday stop orders.
- Trigger behavior is covered by deterministic unit tests even though the smoke window did not naturally trigger a stop.
- The benchmark `backtest.log` for the smoke artifact was empty, so prompt visibility was validated through prompt unit tests rather than artifact text search.
- A full-file ruff pass on `lumibot/components/agents/builtins.py` is not currently used as the feature gate because that file has pre-existing style debt unrelated to this feature. The changed execution-tool path was checked with `--select F,E9`.
