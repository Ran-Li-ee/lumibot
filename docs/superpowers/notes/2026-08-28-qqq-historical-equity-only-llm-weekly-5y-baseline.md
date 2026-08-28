# QQQ Historical Equity-Only LLM Weekly 5Y Baseline

## Purpose

Record the current QQQ historical equity-only LLM strategy as the milestone baseline for future optimization.

This note records the run result, git baseline reference, reproduction command, local artifact paths, known warnings, and interpretation limits.

## Git Reference

Branch at archive time:

    feature/qqq-historical-constituent-universe

Baseline tag:

    baseline/equity-only-qqq-weekly-5y-20260828

Baseline commit:

    71aa540fbf57b413817ec437ffffa3ac39787343

Archive branch:

    archive/equity-only-qqq-weekly-5y-20260828

The tag and archive branch point to the behavior baseline. Later cleanup or optimization commits should be compared against this tag.

## Benchmark Setup

    Strategy: qqq-historical-equity-only-llm
    Window: 2021-08-16 to 2026-08-14
    Cadence: weekly
    Weekly run weekday: MON
    Model: openai/gpt-5.6-luna
    Status: passed

## Reproduction Command

    cd C:\Users\Ran\.config\superpowers\worktrees\lumibot\optimize-equity-only-llm
    $env:AI_TRADING_TEAM_MODEL='openai/gpt-5.6-luna'
    python scripts\run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2021-08-16 --end 2026-08-14 --run-frequency weekly --weekly-run-weekday MON --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 900 --env-file D:\Lumibot\project_notes\API.txt

## Result Metrics

| Metric | Value |
|---|---:|
| Total return | 471.98% |
| CAGR | 41.82% |
| Max drawdown | 19.17% |
| Sharpe | 1.53 |
| Volatility | 24.86% |
| ROMAD | 2.181 |

## Final Positions

The completed benchmark summary reported these final positions:

    USD cash-like position: 7096.21850585926
    FTNT: 299 shares
    PANW: 132 shares
    DXCM: 564 shares
    AMD: 98 shares
    CRWD: 218 shares

## Local Artifacts

Run directory:

    C:/Users/Ran/.config/superpowers/worktrees/lumibot/optimize-equity-only-llm/artifacts/ai_trading_team_example_benchmarks/20260827_181522_001118/qqq-historical-equity-only-llm

Summary file:

    C:/Users/Ran/.config/superpowers/worktrees/lumibot/optimize-equity-only-llm/artifacts/ai_trading_team_example_benchmarks/20260827_181522_001118/summary.json

Account curve:

    C:/Users/Ran/.config/superpowers/worktrees/lumibot/optimize-equity-only-llm/artifacts/ai_trading_team_example_benchmarks/20260827_181522_001118/qqq-historical-equity-only-llm/qqq_historical_equity_only_llm_account_curve.html

Tearsheet:

    C:/Users/Ran/.config/superpowers/worktrees/lumibot/optimize-equity-only-llm/artifacts/ai_trading_team_example_benchmarks/20260827_181522_001118/qqq-historical-equity-only-llm/qqq_historical_equity_only_llm_tearsheet.html

Tearsheet metrics:

    C:/Users/Ran/.config/superpowers/worktrees/lumibot/optimize-equity-only-llm/artifacts/ai_trading_team_example_benchmarks/20260827_181522_001118/qqq-historical-equity-only-llm/qqq_historical_equity_only_llm_tearsheet_metrics.json

Trades:

    C:/Users/Ran/.config/superpowers/worktrees/lumibot/optimize-equity-only-llm/artifacts/ai_trading_team_example_benchmarks/20260827_181522_001118/qqq-historical-equity-only-llm/trades.csv

Stats:

    C:/Users/Ran/.config/superpowers/worktrees/lumibot/optimize-equity-only-llm/artifacts/ai_trading_team_example_benchmarks/20260827_181522_001118/qqq-historical-equity-only-llm/stats.csv

Backtest log:

    C:/Users/Ran/.config/superpowers/worktrees/lumibot/optimize-equity-only-llm/artifacts/ai_trading_team_example_benchmarks/20260827_181522_001118/qqq-historical-equity-only-llm/backtest.log

## Trace Health

The milestone investigation found:

    Agent summary rows: 545
    Agent runtime summary file: cache/agent_runtime/agent_run_summaries.jsonl
    First system run: 2021-08-16T09:30:00-04:00
    Last system run: 2026-08-10T09:30:00-04:00
    Fatal runner failure: none observed
    Negative cash blocker: none observed in this milestone summary

Known warning:

    2024-10-07 equity_basket_agent alpaca_news ReadTimeout from data.alpaca.markets with a 20 second read timeout.

The warning is recorded as a known issue. It does not invalidate the baseline, but future news-tool hardening should consider retries, caching, or fallback behavior.

## Interpretation

This run is the current optimization baseline.

The result is important because the strategy roughly kept pace with SPY before 2026 and clearly separated above SPY after September 2025. The result also suggests that the equity-only LLM route deserves further work even though the strategy still has rough edges.

## Limitations

1. This is still a backtest, not live or paper trading proof.
2. The result may include model stochasticity.
3. One completed run is not enough to prove robustness.
4. News API timeout behavior still needs future hardening.
5. Later strategy changes should be compared against this baseline using the same window, cadence, model family, and artifact review process.

## Next Optimization Direction

Use this baseline to compare future equity-only LLM improvements, especially changes to:

1. Evidence ranking.
2. Candidate selection.
3. News usage.
4. Position sizing.
5. Exit logic.
6. Model choice and prompt architecture.
