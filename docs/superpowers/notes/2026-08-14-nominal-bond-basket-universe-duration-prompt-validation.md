# Nominal Bond Basket Universe and Duration Prompt Validation

Date: 2026-08-14

Branch: `feature/commodity-basket-universe-expansion`

## Scope

Validated the nominal bond basket expansion for `AITradingTeamMockGrowthInflationQuadrantStrategy`.

The intended behavior was:

- expand the nominal bond basket to approved U.S. Treasury duration ETFs;
- keep nominal bond tools limited to rank/price evidence, without news or macro-data tools;
- guide the basket agent to reason about Treasury maturity / duration exposure rather than picking the shortest or longest duration by default;
- preserve benchmark trace and replay UI discovery.

## Static Verification

Command:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Result: `62 passed, 1 warning in 7.76s`.

Command:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py tests\test_ai_trading_team_growth_execution_test.py tests\test_agent_manager.py tests\test_agent_replay_ui_formatters.py -q
```

Result: `160 passed, 2 warnings in 18.27s`.

Command:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
```

Result: `All checks passed!`

## Data Availability Check

Checked Yahoo data for:

`SGOV`, `BIL`, `SHV`, `SHY`, `VGSH`, `SCHO`, `IEI`, `IEF`, `VGIT`, `SCHR`, `GOVT`, `TLH`, `TLT`, `VGLT`, `EDV`, `ZROZ`.

Window: `2024-09-05` to `2024-09-11`.

Result: every symbol returned 4 daily rows.

## Benchmark Run

Command:

```powershell
$env:AI_TRADING_TEAM_MODEL='openai/gpt-5.6-luna'
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-10 --end 2024-09-11 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file D:\Lumibot\project_notes\API.txt
```

Result: passed.

Summary file:

`artifacts/ai_trading_team_example_benchmarks/20260814_093625_968519/summary.json`

Benchmark output artifacts included:

- `mock_growth_inflation_quadrant_account_curve.html`
- `mock_growth_inflation_quadrant_tearsheet.html`
- `stats.csv`
- `trades.csv`
- agent runtime traces under `cache/agent_runtime/traces`

Note: an earlier same-day command used identical start and end dates and failed immediately because Lumibot requires `backtesting_end` to be after `backtesting_start`. The successful validation run used `2024-09-10` to `2024-09-11`.

## Nominal Bond Agent Trace Findings

Trace:

`artifacts/ai_trading_team_example_benchmarks/20260814_093625_968519/mock-growth-inflation-quadrant/cache/agent_runtime/traces/nominal_bond_basket_agent/...json`

Allowed tools:

- `market_load_history_tables_summary`
- `market_last_price`

Tools actually called:

- `market_load_history_tables_summary`

The agent did not receive news, FRED, macro-classification, or trading tools.

Context candidate symbols:

`SGOV`, `BIL`, `SHV`, `SHY`, `VGSH`, `SCHO`, `IEI`, `IEF`, `VGIT`, `SCHR`, `GOVT`, `TLH`, `TLT`, `VGLT`, `EDV`, `ZROZ`.

Tool result included top-10 rankings and detailed summaries for the ranking candidate set, not raw 252-day OHLC rows.

Selected nominal bond symbol: `IEI`.

The reason cited:

- `IEI` ranked first by composite score;
- it had positive 5-day, 21-day, 63-day, and 126-day returns;
- it represented intermediate-term Treasury exposure;
- it avoided taking long-duration or zero-coupon rate sensitivity when composite evidence did not justify that extra volatility.

This matches the intended duration-aware, rank-first behavior.

## Full Workflow Findings

All seven agents ran with zero warnings:

- `macro_allocation_agent`
- `equity_basket_agent`
- `commodity_basket_agent`
- `tips_basket_agent`
- `nominal_bond_basket_agent`
- `portfolio_decision_agent`
- `execution_agent`

Basket outputs:

- equity selected `SPY`;
- commodity selected `IAU`;
- nominal bond selected `IEI`;
- TIPS was inactive because its target weight was `0.0`.

Portfolio decision generated three market buy orders:

- buy `IAU`, 517 shares;
- buy `IEI`, 409 shares;
- buy `SPY`, 44 shares.

Execution filled all three orders at `2024-09-10 09:30:00-04:00`.

Final positions included:

- USD cash: `2336.91064453125`;
- `IAU`: 517 shares;
- `IEI`: 409 shares;
- `SPY`: 44 shares.

## Replay UI Smoke

Called the replay UI loader against:

`artifacts/ai_trading_team_example_benchmarks/20260814_093625_968519/mock-growth-inflation-quadrant/cache/agent_runtime`

Result:

- 1 replay run discovered;
- strategy: `AITradingTeamMockGrowthInflationQuadrantStrategy`;
- all seven agents present;
- account curve artifact available;
- performance report artifact available.

## Conclusion

The nominal bond basket expansion and duration-aware prompt behavior met the expected development target in this validation run.

No high-priority regression was found.
