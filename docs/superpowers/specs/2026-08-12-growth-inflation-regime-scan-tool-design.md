# Growth / Inflation Regime Scan Tool Design

## Purpose

This spec defines a small no-LLM utility for scanning historical Growth /
Inflation macro regimes over a date range.

The immediate user need is:

```text
Look at the past two years of quadrant classifications, find where the regime
changed, then choose a useful backtest window to verify whether the
Growth / Inflation quadrant strategy changes allocation and trades correctly.
```

This tool is not a trading strategy, not an agent workflow, and not a broker
integration. It is a low-cost research helper that reuses the real deterministic
FRED-backed classifier already built for the quadrant strategy.

## Relationship To Existing Work

This spec builds on:

```text
docs/superpowers/specs/2026-08-11-real-fred-growth-inflation-regime-classifier-design.md
```

The existing real classifier is implemented in:

```text
lumibot/example_strategies/fred_growth_inflation_regime_classifier.py
```

The scan tool should call the existing classifier instead of duplicating its
macro formula.

The scan tool should support the real strategy:

```text
lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py
```

The scan tool should not change the mock strategy or the trading workflow.

## Core Design Principle

The scanner should be deterministic Python code.

It should not use:

- OpenAI
- Google ADK
- LiteLLM
- Lumibot agents
- broker APIs
- order tools
- paper trading

This keeps the scanner cheap, repeatable, easy to test, and safe to run over
hundreds of dates.

## Proposed Command

Add a CLI script:

```text
scripts/scan_growth_inflation_regimes.py
```

Typical use:

```powershell
.venv\Scripts\python.exe scripts\scan_growth_inflation_regimes.py --start 2024-08-01 --end 2026-08-01
```

The command should write artifacts under:

```text
artifacts/macro_regime_scans/<run_id>/
```

where `<run_id>` defaults to a timestamp such as:

```text
20260812_153000
```

## Inputs

Required:

| Argument | Meaning |
|---|---|
| `--start YYYY-MM-DD` | First date to scan |
| `--end YYYY-MM-DD` | Last date to scan |

Optional:

| Argument | Default | Meaning |
|---|---:|---|
| `--calendar` | `trading-days` | Which dates to scan |
| `--window-before` | `5` | Suggested trading days before each transition |
| `--window-after` | `5` | Suggested trading days after each transition |
| `--output-dir` | `artifacts/macro_regime_scans` | Parent artifact directory |
| `--run-id` | timestamp | Artifact subdirectory name |
| `--mode` | classifier default | Classifier mode |
| `--growth-series-id` | classifier default | FRED growth series |
| `--inflation-series-id` | classifier default | FRED inflation series |
| `--growth-lag-months` | classifier default | Growth lag |
| `--inflation-lag-months` | classifier default | Inflation lag |
| `--trend-years` | classifier default | Rolling trend window |

`--calendar` should accept:

```text
trading-days
weekdays
calendar-days
```

Default should be `trading-days` because the strategy trades US-listed ETFs. If
the project cannot access a market calendar in a test environment, it may fall
back to weekdays with a clear warning in the metadata.

## Environment And Credentials

The scanner needs FRED access, so it must be able to use the same FRED
configuration path as the real classifier.

It may support an optional environment file argument if existing project
patterns already support one:

```text
--env-file project_notes/API.txt
```

However, the implementation must not log, print, or commit API keys.

If required FRED credentials are unavailable, the scanner should produce a
clear failure report rather than crashing with an unclear stack trace.

## Date Grid

For each selected scan date:

1. Treat the scan date as the simulated `as_of` date.
2. Call the existing Growth / Inflation classifier.
3. Store one result row.

The scanner should not infer intermediate macro updates itself. It should ask
the classifier for each date, even if quarterly GDP or monthly CPI does not
change every trading day. This keeps the first scanner simple and faithful to
the strategy's daily decision process.

Future versions may add optimization such as caching repeated daily classifier
results when the underlying FRED evidence is unchanged.

## Output Files

The scanner should write three required files and one optional file.

### `daily_regimes.csv`

One row per scanned date.

Required columns:

| Column | Meaning |
|---|---|
| `date` | Scan date |
| `status` | `passed`, `blocked`, or `failed` |
| `regime` | Classified regime, when available |
| `growth_direction` | `up` or `down`, when available |
| `inflation_direction` | `up` or `down`, when available |
| `equity_weight` | Basket weight |
| `commodity_weight` | Basket weight |
| `tips_weight` | Basket weight |
| `nominal_bond_weight` | Basket weight |
| `previous_passed_regime` | Previous available regime |
| `regime_changed` | Whether this date changed from the previous passed regime |
| `growth_metric_value` | Current growth metric |
| `growth_trend_value` | Growth trend value |
| `growth_margin` | Growth metric minus trend |
| `growth_latest_observation_date` | Usable FRED observation date |
| `growth_data_cutoff` | Lag-adjusted growth cutoff |
| `inflation_metric_value` | Current inflation metric |
| `inflation_trend_value` | Inflation trend value |
| `inflation_margin` | Inflation metric minus trend |
| `inflation_latest_observation_date` | Usable FRED observation date |
| `inflation_data_cutoff` | Lag-adjusted inflation cutoff |
| `confidence_level` | Classifier confidence field, if available |
| `reason_brief` | Short human-readable explanation |
| `error_reason` | Failure or blocked reason |

The CSV should preserve failed or blocked dates. It should not silently drop
them.

### `regime_transitions.csv`

One row per detected transition.

A transition occurs when:

```text
current passed regime != previous passed regime
```

Required columns:

| Column | Meaning |
|---|---|
| `transition_date` | First scan date with the new regime |
| `previous_scan_date` | Previous passed scan date |
| `from_regime` | Previous regime |
| `to_regime` | New regime |
| `from_weights` | Previous basket weights as compact JSON |
| `to_weights` | New basket weights as compact JSON |
| `suggested_start_date` | Suggested backtest start date |
| `suggested_end_date` | Suggested backtest end date |
| `growth_margin` | Margin on transition date |
| `inflation_margin` | Margin on transition date |
| `confidence_level` | Classifier confidence on transition date |
| `reason_brief` | Classifier explanation on transition date |

The suggested window should use the scan date grid:

```text
suggested_start_date = N scan dates before transition_date
suggested_end_date   = N scan dates after transition_date
```

with `N` controlled by `--window-before` and `--window-after`.

If the transition is near the start or end of the scan range, clamp the
suggested window to the available date grid.

### `summary.md`

A human-readable report for the user.

It should include:

- command and parameter summary
- number of scanned dates
- number of passed, blocked, and failed dates
- count of days in each regime
- first and last passed regime
- transition table
- suggested backtest windows
- warnings about missing data or fragile margins
- artifact file paths

This report is the primary file the user reads before choosing a backtest
window.

### `scan_metadata.json`

Machine-readable metadata is optional but recommended.

It should include:

- generated timestamp
- command arguments
- classifier configuration
- calendar mode
- artifact paths
- row counts
- status counts
- transition count
- warning list

## Error Handling

The scanner should distinguish between command-level errors and row-level
classifier problems.

Command-level errors should stop execution:

- invalid date format
- `start > end`
- invalid calendar mode
- output directory cannot be created

Row-level problems should be recorded and scanning should continue:

- classifier returns `blocked`
- classifier returns `failed`
- FRED data missing for one date
- insufficient history for one date
- unexpected classifier exception for one date

If all rows fail, the command should still write a report explaining that no
usable regimes were found.

## Rate, Cache, And Cost Expectations

The tool should make no LLM calls, so token cost should be zero.

It will call FRED repeatedly through the existing project data layer. A two-year
daily scan may involve hundreds of classifier calls. The first run may take
some time, and repeated runs should benefit from existing FRED caching if the
project data layer provides it.

The first implementation should prioritize correctness and transparency over
aggressive request minimization.

If runtime is too slow in practice, future work may add a daily evidence cache
or scan only potential macro publication dates.

## Integration Boundaries

The scanner should use the existing classifier API as much as possible.

It should not:

- duplicate GDP/CPI formula code
- add a second FRED client
- modify basket agents
- modify execution agents
- trigger Lumibot backtests
- write trace files
- place orders

The scanner may add small formatting helpers for CSV, Markdown, and JSON output.

## Testing Requirements

Tests should not require network access.

Use fake classifier results or monkeypatch the classifier function.

Required tests:

1. Builds the correct date grid for weekdays.
2. Builds a valid trading-day grid when market calendar support is available.
3. Records one daily row per scan date.
4. Preserves blocked and failed rows in `daily_regimes.csv`.
5. Detects regime transitions only between passed rows.
6. Does not create a transition from or into a failed row.
7. Calculates suggested transition windows using the scan date grid.
8. Clamps suggested windows near scan boundaries.
9. Writes `daily_regimes.csv`.
10. Writes `regime_transitions.csv`.
11. Writes `summary.md`.
12. Writes or cleanly skips `scan_metadata.json`.
13. Handles a scan with zero transitions.
14. Handles a scan where all rows fail.
15. Validates CLI arguments.

## Verification Commands

Implementation should include targeted tests similar to:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_growth_inflation_regime_scan.py -q
```

```powershell
.venv\Scripts\python.exe -m ruff check scripts\scan_growth_inflation_regimes.py tests\test_growth_inflation_regime_scan.py
```

After unit tests pass, run a real smoke scan with the user's local FRED
configuration:

```powershell
.venv\Scripts\python.exe scripts\scan_growth_inflation_regimes.py --start 2024-09-01 --end 2024-10-15 --run-id smoke-growth-inflation-scan
```

The real smoke scan should produce artifact files and at least one readable
summary, but it does not need to guarantee a regime transition in that short
window.

## Acceptance Criteria

This feature is complete when:

1. A CLI scanner exists.
2. It reuses the real FRED-backed Growth / Inflation classifier.
3. It can scan a date range without using LLMs.
4. It writes `daily_regimes.csv`.
5. It writes `regime_transitions.csv`.
6. It writes `summary.md`.
7. It records failed or blocked dates instead of hiding them.
8. It identifies regime changes between passed rows.
9. It suggests backtest windows around each transition.
10. Unit tests cover date grids, transition detection, failure handling, and
    output writing.
11. A short real-data smoke scan can run when FRED credentials are available.

## Non-Goals

This feature must not:

- run an LLM agent
- run a strategy backtest
- choose basket ETFs
- decide trades
- place orders
- modify prompts
- modify classifier math
- optimize macro formulas
- add new macro series
- add linearized basket weights
- add a replay UI panel

## Future Extensions

After the first scanner works, future specs may consider:

- rendering an HTML timeline of regimes
- adding a simple chart of growth and inflation margins
- ranking transitions by margin strength
- filtering for durable transitions that persist for several scan dates
- detecting near-transition periods where a margin is close to zero
- adding cached classifier evidence to reduce FRED calls
- adding direct buttons from scan output to run selected backtests

Those are deliberately outside this first scanner.
