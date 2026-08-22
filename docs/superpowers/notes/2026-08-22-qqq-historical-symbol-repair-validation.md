# QQQ Historical Symbol Repair Validation

## Purpose

Validate that known bad QQQ historical symbols are repaired before reaching the equity agent and market summary tool.

This validation covers the small smoke-test scope for the symbol repair feature. It does not judge strategy performance and does not replace a full five-year benchmark rerun.

## Unit Tests

Command:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q
```

Result:

```text
29 passed, 1 warning in 1.49s
```

The warning is the existing `websockets.legacy` deprecation warning from the local Python environment.

## Ruff

Command:

```powershell
python -m ruff check lumibot/tools/universe/qqq_nport.py lumibot/example_strategies/ai_trading_team_equity_only_llm.py tests/test_ai_trading_team_equity_only_llm.py
```

Result:

```text
All checks passed!
```

Ruff also printed the existing project warning that top-level linter settings in `pyproject.toml` are deprecated in favor of `lint` settings.

## Smoke Backtest

Command:

```powershell
$env:AI_TRADING_TEAM_MODEL='openai/gpt-5.6-luna'
python scripts\run_ai_trading_team_examples_benchmark.py `
  --strategy qqq-historical-equity-only-llm `
  --start 2026-03-02 `
  --end 2026-03-09 `
  --run-frequency weekly `
  --weekly-run-weekday MON `
  --max-workers 1 `
  --max-run-attempts 1 `
  --agent-run-timeout-seconds 900 `
  --env-file D:\Lumibot\project_notes\API.txt
```

Result:

```json
{
  "artifact_dir": "C:\\Users\\Ran\\.config\\superpowers\\worktrees\\lumibot\\optimize-equity-only-llm\\artifacts\\ai_trading_team_example_benchmarks\\20260822_134155_968575\\qqq-historical-equity-only-llm",
  "status": "passed",
  "strategy": "qqq-historical-equity-only-llm",
  "wall_ms": 107596
}
```

## Trace Inspection

Artifact:

```text
artifacts\ai_trading_team_example_benchmarks\20260822_134155_968575\qqq-historical-equity-only-llm
```

Trace inspection result:

```text
bad_in_context []
bad_in_tool_call []
expected_in_context ['MRVL', 'STX', 'TRI']
expected_in_tool_call ['MRVL', 'STX', 'TRI']
```

Recorded `universe_source.symbol_repair` metadata:

```json
[
  {
    "aliases": [
      {
        "from": "STXN",
        "to": "STX"
      },
      {
        "from": "TRI4EUR",
        "to": "TRI"
      }
    ],
    "applied": true,
    "deduped_count": 0,
    "dropped_count": 0,
    "final_count": 98,
    "raw_count": 98,
    "repaired_count": 2
  }
]
```

The smoke window exercised `STXN -> STX` and `TRI4EUR -> TRI`. It did not exercise `CPW -> CHKP` or `MRVLEUR -> MRVL` in the selected live snapshot, but those aliases are covered by unit tests.

## Backtest Log Check

Command checked `backtest.log` for these known bad historical symbols:

```text
CPW
MRVLEUR
TRI4EUR
STXN
```

Result:

```text
bad_symbol_log_matches []
```

## Notes

`SHOP` remains a separate follow-up because manual checks showed it has Yahoo daily data, so it is likely a separate loading, cache, or provider issue rather than a known stale symbol alias.

Full five-year QQQ benchmark rerun should wait until this repair and any other high-priority data-quality fixes are complete.
