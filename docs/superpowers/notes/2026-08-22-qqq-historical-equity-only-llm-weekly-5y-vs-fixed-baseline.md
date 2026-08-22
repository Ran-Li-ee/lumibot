# QQQ Historical Equity-Only LLM Weekly 5-Year Run

## Purpose

Compare the new `qqq-historical-equity-only-llm` strategy against the fixed-50-stock baseline recorded in:

`docs/superpowers/notes/2026-08-16-equity-only-llm-weekly-5y-baseline.md`

## Run Identity

- Strategy: `qqq-historical-equity-only-llm`
- Branch: `feature/qqq-historical-constituent-universe`
- Model: `openai/gpt-5.6-luna`
- Window: `2021-08-16` to `2026-08-14`
- Run frequency: `weekly`
- Weekly run weekday: `MON`
- Holiday policy: `first_open_trading_day`
- Artifact root: `artifacts/ai_trading_team_example_benchmarks/20260822_100114_919015/qqq-historical-equity-only-llm`
- Runner status: `passed`
- Wall time: `10,199,784 ms` (~170.0 minutes)

## Command

```powershell
$env:AI_TRADING_TEAM_MODEL='openai/gpt-5.6-luna'
python scripts\run_ai_trading_team_examples_benchmark.py `
  --strategy qqq-historical-equity-only-llm `
  --start 2021-08-16 `
  --end 2026-08-14 `
  --run-frequency weekly `
  --weekly-run-weekday MON `
  --max-workers 1 `
  --max-run-attempts 1 `
  --agent-run-timeout-seconds 900 `
  --env-file D:\Lumibot\project_notes\API.txt
```

## Core Comparison

Use `stats.csv` and `qqq_historical_equity_only_llm_tearsheet_metrics.json` as the metric sources. The runner `backtest_result` return/CAGR fields may not match account-value and tearsheet metrics, as already observed in the fixed baseline.

| Metric | QQQ Historical LLM | Fixed-50 Baseline | SPY Benchmark |
|---|---:|---:|---:|
| Start portfolio | `100,000.00` | `100,000.00` | n/a |
| Final portfolio | `252,396.10` | `507,486.68` | n/a |
| Total return | `152%` | `407%` | `86%` |
| CAGR | `20.37%` | `38.44%` | `13.27%` |
| Sharpe | `0.54` | `0.82` | `0.60` |
| Max drawdown | `-65.43%` | `-45.80%` | `-24.49%` |
| Annualized volatility | `72.33%` | `49.88%` | `17.17%` |
| Worst year | `-29.01%` | `-34.55%` | `-18.16%` |
| Best year | `201.34%` | `120.21%` | `26.21%` |
| Worst 3-month return | `-34.94%` | `-26.42%` | `-16.11%` |
| Time to recovery | `21 days` | `180 days` | `426 days` |

## Yearly Returns

| Year | QQQ Historical LLM | SPY Benchmark | Won vs SPY |
|---|---:|---:|---|
| 2021 | `-0.52%` | `6.99%` | No |
| 2022 | `-29.01%` | `-18.16%` | No |
| 2023 | `201.34%` | `26.21%` | Yes |
| 2024 | `-7.62%` | `24.89%` | No |
| 2025 | `48.32%` | `17.72%` | Yes |
| 2026 | `-13.44%` | `14.68%` | No |

## Operational Checks

- `stats.csv` rows: `5016`.
- Equity agent runs: `261`.
- Execution agent runs: `235`.
- Agent run rows: `496`.
- Distinct equity run dates: `261`.
- Agent warnings: `0`.
- Trace records with `tool_error=true` or blocked/invalid plan status: `0`.
- Minimum cash: `0.02757263184875`.
- Final cash: `1,763.903331756775`.
- Negative cash rows: `0`.
- Trade rows: `712`.
- Filled orders: `356`.
- New order records: `356`.
- Trade dates: `235`.
- Max filled orders on one date: `2`.
- Final positions: `1,763.903331756775 USD`, `658 PANW`.

## Agent Behavior

Tool calls:

| Tool | Calls |
|---|---:|
| `market_load_history_tables_summary` | `267` |
| `execution_plan_execute` | `235` |

Top selected symbols by equity agent:

| Symbol | Selections |
|---|---:|
| NVDA | 40 |
| WBD | 19 |
| CEG | 16 |
| PDD | 12 |
| DLTR | 11 |
| VRTX | 11 |
| PLTR | 10 |
| WDC | 10 |
| GILD | 8 |
| ADBE | 7 |
| ROST | 7 |
| META | 7 |

Most-filled trade symbols:

| Symbol | Filled orders |
|---|---:|
| NVDA | 41 |
| WBD | 21 |
| PDD | 18 |
| CEG | 17 |
| DLTR | 15 |
| VRTX | 15 |
| PLTR | 15 |
| WDC | 12 |
| GILD | 10 |
| META | 10 |
| NFLX | 10 |
| TSLA | 9 |

## Important Observations

This run completed successfully and outperformed SPY on total return, but it materially underperformed the fixed-50-stock baseline.

Main differences:

- QQQ historical LLM return was much lower than fixed-50 baseline: `152%` vs `407%`.
- QQQ historical LLM drawdown was much deeper: `-65.43%` vs `-45.80%`.
- QQQ historical LLM Sharpe was lower than both fixed-50 baseline and SPY: `0.54` vs `0.82` and `0.60`.
- The run had a very strong 2023, but poor 2022, 2024, and 2026 relative to SPY.

Operationally, the strategy path worked:

- Weekly scheduling completed the full 5-year window.
- The execution layer completed and confirmed orders.
- No negative cash occurred.
- No agent warnings were recorded.
- No trace-level tool errors or blocked execution plans were found.

But the QQQ historical universe data path exposed symbol-quality problems:

- The runtime logs contained many Yahoo data misses.
- Most frequent problematic symbols observed in logs included `SHOP`, `TRI4EUR`, `CPW`, `STXN`, `MRVLEUR`, and a corrupted `CM?...SA`-like symbol.
- These did not block execution, but they likely add noise, runtime overhead, and potential ranking distortion.

## Preliminary Assessment

This run is useful as a comparison artifact, but it does not yet support replacing the fixed-50 baseline with QQQ historical constituents.

The next likely research question is not whether the LLM can operate the dynamic universe; it can. The question is whether the historical constituent data should be cleaned more aggressively before reaching the ranking tool and LLM.
