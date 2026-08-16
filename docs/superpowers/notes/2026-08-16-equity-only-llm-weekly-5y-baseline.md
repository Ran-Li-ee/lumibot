# Equity-Only LLM Weekly 5-Year Baseline

## Run Identity

- Strategy: `equity-only-llm`
- Branch: `feature/optimize-equity-only-llm`
- Commit: `5d8af6c7df678bd9f7161ba9afc4dd0ad38cfcb8`
- Model: `openai/gpt-5.6-luna`
- Window: `2021-08-16` to `2026-08-14`
- Run frequency: `weekly`
- Weekly run weekday: `MON`
- Holiday policy: `first_open_trading_day`
- Artifact root: `artifacts/ai_trading_team_example_benchmarks/20260816_135419_346451/equity-only-llm`
- Runner status: `passed`
- Wall time: `4,968,060 ms` (~82.8 minutes)

## Command

```powershell
$env:AI_TRADING_TEAM_MODEL='openai/gpt-5.6-luna'
python scripts\run_ai_trading_team_examples_benchmark.py `
  --strategy equity-only-llm `
  --start 2021-08-16 `
  --end 2026-08-14 `
  --run-frequency weekly `
  --weekly-run-weekday MON `
  --max-workers 1 `
  --max-run-attempts 1 `
  --agent-run-timeout-seconds 900 `
  --env-file D:\Lumibot\project_notes\API.txt
```

## Core Results

Use `stats.csv` and `equity_only_llm_tearsheet_metrics.json` as the baseline metric sources.

| Metric | Strategy | SPY Benchmark |
|---|---:|---:|
| Start portfolio | `100,000.00` | n/a |
| Final portfolio | `507,486.68` | n/a |
| Total return | `407%` | `86%` |
| CAGR | `38.44%` | `13.27%` |
| Sharpe | `0.82` | `0.60` |
| Max drawdown | `-45.80%` | `-24.49%` |
| Annualized volatility | `49.88%` | `17.17%` |
| Worst year | `-34.55%` | `-18.16%` |
| Best year | `120.21%` | `26.21%` |
| Worst 3-month return | `-26.42%` | `-16.11%` |
| Time to recovery | `180 days` | `426 days` |

## Operational Checks

- `stats.csv` rows: `5016`.
- Equity agent runs: `261`.
- Distinct equity run dates: `261`.
- Run day distribution: `234` Monday runs, `27` Tuesday runs.
- Tuesday runs are expected because the weekly holiday policy uses the first open trading day when Monday is closed.
- Execution agent runs: `226`.
- Agent warnings: `0`.
- Tool error files with `"tool_error": true`: `0`.
- Actual blocked or invalid execution plans: `0`.
- Minimum cash: `0.192182540835`.
- Final cash: `127.88722038272`.
- No negative cash observed.
- Trade rows: `628`.
- Filled orders: `314`.
- New order records: `314`.
- Non-`new`/`fill` trade statuses: `0`.
- Trade dates: `226`.
- Max filled orders on one date: `2`.
- Final positions: `127.88722038272 USD`, `1332 PANW`.

## Agent Behavior

- Agent run rows: `487`.
- Equity agent tool calls: mostly `market_load_history_tables_summary`.
- News was used once, on `2025-10-27`, after rank evidence needed support.
- News usage remained conditional rather than routine, matching the current prompt architecture.

Top selected symbols by equity agent count:

| Symbol | Selections |
|---|---:|
| NVDA | 62 |
| TSLA | 19 |
| LLY | 18 |
| NFLX | 17 |
| INTC | 16 |
| XOM | 15 |
| GOOGL | 12 |
| CVX | 12 |
| GE | 12 |
| PANW | 10 |

Most-filled trade symbols:

| Symbol | Filled orders |
|---|---:|
| NVDA | 59 |
| TSLA | 24 |
| NFLX | 21 |
| INTC | 20 |
| XOM | 17 |
| LLY | 17 |
| GE | 17 |
| ORCL | 14 |
| CVX | 14 |
| GOOGL | 14 |

## Artifact Files

- Account curve: `equity_only_llm_account_curve.html`
- Tearsheet: `equity_only_llm_tearsheet.html`
- Tearsheet metrics: `equity_only_llm_tearsheet_metrics.json`
- Stats: `stats.csv`
- Trades: `trades.csv`
- Agent detail: `stats_agent_detail.parquet`

## Important Metric-Caution

`summary.json` / `result.json` include `backtest_result.total_return = 6.38111812392387` and `backtest_result.cagr = 0.49256744403618247`.

Those values do not match the final account value in `stats.csv`:

- Start portfolio: `100,000`
- Final portfolio: `507,486.6790905`
- Total return from account value: `4.074866790905`
- CAGR from account value: `0.384561427191628`

The tearsheet metrics agree with the account-value calculation (`Total Return = 4.07`, `CAGR = 38.44%`). For baseline comparisons, use `stats.csv` / tearsheet metrics, not `backtest_result.total_return` from the runner summary.

## Baseline Assessment

This run is usable as an optimization baseline.

Reasons:

- It completed successfully.
- Weekly scheduling behaved as expected.
- No negative cash occurred.
- No actual blocked/invalid execution plans occurred.
- All submitted orders filled.
- Trace, account curve, tearsheet, stats, and trades artifacts were saved.
- Strategy outperformed SPY materially on return and Sharpe over this window.

Main cautions:

- Drawdown and volatility are much higher than SPY.
- The strategy is highly concentrated in momentum winners, especially NVDA in this window.
- The runner summary's `backtest_result` return/CAGR fields appear inconsistent with account-value and tearsheet metrics.
- This is a single-window result and should not be treated as proof of robustness.
