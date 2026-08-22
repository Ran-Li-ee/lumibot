# Equity Top 5 Trailing Stop Smoke

## Command

```powershell
$env:AI_TRADING_TEAM_MODEL='openai/gpt-5.6-luna'
python scripts/run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2024-09-05 --end 2024-10-07 --run-frequency weekly --weekly-run-weekday MON --max-workers 1 --agent-run-timeout-seconds 1800 --max-run-attempts 3 --env-file D:\Lumibot\project_notes\API.txt
```

## Result

- Status: `passed`
- Strategy: `qqq-historical-equity-only-llm`
- Model: `openai/gpt-5.6-luna`
- Window: `2024-09-05` to `2024-10-07`
- Artifact: `C:\Users\Ran\.config\superpowers\worktrees\lumibot\optimize-equity-only-llm\artifacts\ai_trading_team_example_benchmarks\20260822_153911_384962\qqq-historical-equity-only-llm`
- Wall time: `95362 ms`

## Checks

- Equity agent summaries contained `selected_symbols` lists with five symbols.
- Trade log showed five initial buy fills on `2024-09-05`: `AAPL`, `CCEP`, `CTAS`, `ISRG`, and `KDP`.
- Final positions were diversified across five stocks plus cash.
- `negative_cash` markers found in text artifacts: `0`.
- Stop-triggered markers found in text artifacts: `0`.
- `selected_symbols` markers found in text artifacts: `95`.
- `execution_reason` markers found in text artifacts: `25`.

## Notes

No trailing stop sale occurred in this short smoke window, so this run verifies the Top 5 allocation path and execution compatibility, but not a live stop-trigger event inside a real backtest.
