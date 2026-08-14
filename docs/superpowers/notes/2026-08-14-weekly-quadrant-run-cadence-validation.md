# Weekly Quadrant Run Cadence Validation

Date: 2026-08-14

## Test Commands

- `D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_tool_permissions.py tests\test_agent_runtime_provider_keys.py tests\test_agent_replay_ui_loader.py tests\test_agent_replay_ui_formatters.py -q`
- `D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py scripts\run_ai_trading_team_examples_benchmark.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py`

## Benchmark Commands

Benchmarks were run with `AI_TRADING_TEAM_MODEL=openai/gpt-5.6-luna` and `--env-file D:\Lumibot\project_notes\API.txt`.

- One-day weekly default:
  `D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-06 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file D:\Lumibot\project_notes\API.txt`
- Multi-day weekly default:
  `D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-13 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file D:\Lumibot\project_notes\API.txt`
- Multi-day daily override:
  `D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-09 --run-frequency daily --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file D:\Lumibot\project_notes\API.txt`
- Matching-window weekly default:
  `D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-09 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file D:\Lumibot\project_notes\API.txt`

## Results

- Focused pytest: `293 passed, 2 warnings in 35.72s`.
- Ruff: `All checks passed!`
- One-day weekly default artifact:
  `C:\Users\Ran\.config\superpowers\worktrees\lumibot\commodity-basket-universe-expansion\artifacts\ai_trading_team_example_benchmarks\20260814_133023_100013\mock-growth-inflation-quadrant`
- Multi-day weekly default artifact:
  `C:\Users\Ran\.config\superpowers\worktrees\lumibot\commodity-basket-universe-expansion\artifacts\ai_trading_team_example_benchmarks\20260814_133203_171584\mock-growth-inflation-quadrant`
- Multi-day daily override artifact:
  `C:\Users\Ran\.config\superpowers\worktrees\lumibot\commodity-basket-universe-expansion\artifacts\ai_trading_team_example_benchmarks\20260814_133443_278018\mock-growth-inflation-quadrant`
- Matching-window weekly default artifact:
  `C:\Users\Ran\.config\superpowers\worktrees\lumibot\commodity-basket-universe-expansion\artifacts\ai_trading_team_example_benchmarks\20260814_133952_562110\mock-growth-inflation-quadrant`

## Cadence Evidence

- One-day weekly default full workflow count: 1 `macro_allocation_agent` trace and 1 `execution_agent` trace.
- Multi-day weekly default full workflow count: 2 `macro_allocation_agent` traces and 2 `execution_agent` traces.
- Multi-day daily override full workflow count: 2 `macro_allocation_agent` traces and 2 `execution_agent` traces.
- Matching-window weekly default full workflow count: 1 `macro_allocation_agent` trace and 1 `execution_agent` trace.
- For the same `2024-09-05` to `2024-09-09` window, weekly default recorded 1 full workflow while daily override recorded 2 full workflows.
- The multi-day weekly default window covered multiple observed trading sessions but recorded only two complete weekly workflows.

## Notes

- Weekly cadence uses strategy-level gating, not Lumibot global `1W` scheduling.
- Skipped days return before agent calls, so skipped days do not create full agent traces.
- The feature does not change macro classification, basket selection, portfolio planning, or execution tools.
- An initial benchmark attempt without `AI_TRADING_TEAM_MODEL=openai/gpt-5.6-luna` failed at provider key validation because the runner default model is `gemini-3.1-flash-lite` while the available API file is OpenAI-oriented. The benchmark was rerun successfully with the explicit OpenAI model environment variable.
