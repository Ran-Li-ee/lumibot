# QQQ Historical Equity-Only LLM Validation

Date: 2026-08-22
Branch: `feature/qqq-historical-constituent-universe`

## Scope

Validate the new `qqq-historical-equity-only-llm` benchmark strategy:

- Uses local point-in-time QQQ N-PORT snapshots as the equity universe.
- Preserves the fixed `equity-only-llm` strategy.
- Registers the new benchmark runner key.
- Produces trace metadata that shows the resolved QQQ snapshot.
- Runs a short paid smoke backtest with `openai/gpt-5.6-luna`.

## Static Verification

Commands:

```powershell
python -m pytest tests/test_qqq_nport_universe.py -m "not apitest" -q
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q
python -m ruff check scripts/run_ai_trading_team_examples_benchmark.py lumibot/example_strategies/ai_trading_team_equity_only_llm.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py tests/test_ai_trading_team_equity_only_llm.py tests/test_qqq_nport_universe.py
```

Results:

- `tests/test_qqq_nport_universe.py`: 30 passed, 1 deselected.
- `tests/test_ai_trading_team_equity_only_llm.py`: 25 passed.
- `ruff`: all checks passed.

Note: pytest emitted the existing `websockets.legacy` deprecation warning. Ruff emitted the existing top-level linter settings deprecation warning.

## QQQ Snapshot Resolver Check

Command used the same resolver path the strategy calls:

```powershell
python - << resolver check for resolve_qqq_snapshot('2024-09-05', mode='strict')
```

Result:

- `as_of`: 2024-09-05
- `selected_report_date`: 2024-06-30
- `selected_filing_date`: 2024-08-28
- `accession_number`: `0001752724-24-196011`
- `holding_count`: 96
- first symbols observed: `CHTR`, `CCEP`, `TTD`, `BKR`, `MDB`, `BKNG`, `ZS`, `FAST`, `AVGO`, `KDP`
- `snapshot_path`: local cache under `AppData\Local\LumiWealth\lumibot\Cache\1.0\universe\qqq_nport\normalized`

The collector CLI `--as-of` check timed out during validation, so the final validation used the actual runtime resolver instead of the collection script.

## Paid Smoke Backtest

Command:

```powershell
$env:AI_TRADING_TEAM_MODEL='openai/gpt-5.6-luna'
python scripts\run_ai_trading_team_examples_benchmark.py `
  --strategy qqq-historical-equity-only-llm `
  --start 2024-09-05 `
  --end 2024-09-12 `
  --env-file D:\Lumibot\project_notes\API.txt `
  --max-workers 1 `
  --agent-run-timeout-seconds 1800
```

Result:

- Status: `passed`
- Artifact root: `artifacts\ai_trading_team_example_benchmarks\20260822_000426_799081`
- Strategy artifact: `artifacts\ai_trading_team_example_benchmarks\20260822_000426_799081\qqq-historical-equity-only-llm`
- Account curve, tearsheet, metrics, stats, trades, and trace files were generated.
- Final positions included USD cash and KDP stock.

Backtest summary:

- total return: 0.91%
- max drawdown: 0.67%
- selected final holding: KDP

Short windows are smoke tests only; these performance figures should not be interpreted as strategy quality.

## Trace Verification

Two weekly system runs were observed.

### 2024-09-05

Equity agent request context included:

- `basket_symbols`: 96 QQQ historical symbols
- `universe_source.type`: `qqq_nport`
- `universe_source.mode`: `strict`
- `universe_source.as_of_date`: `2024-09-05`
- `universe_source.selected_report_date`: `2024-06-30`
- `universe_source.selected_filing_date`: `2024-08-28`
- `universe_source.holding_count`: 96
- `universe_source.snapshot_path`: local normalized QQQ N-PORT snapshot path

Agent behavior:

- `equity_basket_agent`: 1 tool call, `market_load_history_tables_summary`
- selected symbol: CTAS
- warnings: 0
- `execution_agent`: 1 tool call, `execution_plan_execute`
- result: bought 489 CTAS shares
- warnings: 0

### 2024-09-09

Equity agent request context included:

- `basket_symbols`: 96 QQQ historical symbols
- `universe_source.type`: `qqq_nport`
- `universe_source.mode`: `strict`
- `universe_source.as_of_date`: `2024-09-09`
- `universe_source.selected_report_date`: `2024-06-30`
- `universe_source.selected_filing_date`: `2024-08-28`
- `universe_source.holding_count`: 96

Agent behavior:

- `equity_basket_agent`: 1 tool call, `market_load_history_tables_summary`
- selected symbol: KDP
- warnings: 0
- `execution_agent`: 1 tool call, `execution_plan_execute`
- result: sold 489 CTAS shares, then bought 2,658 KDP shares
- warnings: 0

## Notes

- The new benchmark runner key appears in `--help`.
- The fixed `equity-only-llm` key remains present.
- The trace confirms the dynamic QQQ universe is passed as real agent context, not only used internally.
- The strategy successfully executed a same-window rotation from CTAS to KDP.
