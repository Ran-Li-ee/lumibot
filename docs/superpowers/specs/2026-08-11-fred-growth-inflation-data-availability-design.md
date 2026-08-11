# FRED Growth / Inflation Data Availability Design

## Purpose

This spec defines Stage 1 of the real macro regime classifier roadmap.

The goal is to prove that Lumibot can retrieve the macro data needed for a
future Research Affiliates-style Growth / Inflation quadrant classifier.

This stage does not build the classifier. It only answers:

```text
Can we get the required FRED data, as of a simulated trading date, with enough
history and enough audit metadata to safely build the real classifier later?
```

## Relationship To The Two-Stage Roadmap

This spec implements the detailed design for Stage 1 from:

```text
docs/superpowers/specs/2026-08-11-real-macro-regime-classifier-two-stage-roadmap-design.md
```

The two-stage roadmap is:

```text
Stage 1: prove the required FRED macro data is available and usable
Stage 2: build the real classifier using the verified data
```

Stage 2 must not begin until this Stage 1 validation produces a satisfactory
data availability report.

## Current Project Context

Lumibot already has native FRED / ALFRED support in:

```text
lumibot/macro/fred.py
```

Agents can also receive these built-in macro tools when `FRED_API_KEY` is
configured:

- `list_fred_series`
- `get_fred_series`
- `get_fred_latest`
- `get_fred_snapshot`

The existing FRED implementation is important because it already uses the
official FRED API with `realtime_start` and `realtime_end`, and it reports:

- `source`
- `series_id`
- `as_of`
- `point_in_time_safe`
- `uses_revised_data`
- `observations`

Stage 1 should use this existing Lumibot FRED path. It should not introduce a
separate macro data client unless the current path proves unusable.

## Core Design Principle

Use deterministic local validation, not an LLM.

The Stage 1 validator should be a normal Python command that calls Lumibot's
FRED data layer directly. This keeps the test cheap, repeatable, and easy to
debug.

```text
Python validation script
  -> FREDMacroData
  -> official FRED / ALFRED API
  -> local cache
  -> JSON + Markdown availability report
```

No agent should decide whether data is usable.

## Required Data

Stage 1 must validate two required series.

| Axis | Series ID | Meaning | Expected Frequency | Required |
|---|---|---|---|---|
| Growth | `GDPC1` | Real Gross Domestic Product | Quarterly | Yes |
| Inflation | `CPIAUCSL` | Consumer Price Index for All Urban Consumers | Monthly | Yes |

Stage 1 may optionally inspect secondary series, but they must not be required
for success.

| Use | Series ID | Meaning | Required |
|---|---|---|---|
| Inflation comparison | `PCEPI` | PCE Price Index | No |
| Nominal GDP comparison | `GDP` | Gross Domestic Product | No |

The first implementation should keep the required set small. If `GDPC1` and
`CPIAUCSL` pass, Stage 2 has enough to build the first real classifier.

## Dates To Validate

The validator should test multiple `as_of` dates because one successful current
request does not prove historical backtest usability.

Minimum required dates:

| Label | Date | Purpose |
|---|---|---|
| `recent` | Current local date, or the explicit date passed by CLI | Prove current access works |
| `project_recent_backtest` | `2024-09-05` | Match the recent strategy test window we have used repeatedly |
| `older_backtest` | `2010-01-01` | Prove older backtests can retrieve enough prior macro history |

The implementation plan may add more dates, but it should not remove these
three categories unless a date is impossible for a documented reason.

## Historical Window Requirements

The validator should request enough history before each `as_of` date to support
a future five-year trend calculation.

The future classifier will likely compare current year-over-year growth or
inflation against a rolling five-year average of that same metric. That means
the raw level series must include more than five years of observations.

Minimum sufficiency checks:

| Series | Minimum Non-Null Observations | Reason |
|---|---:|---|
| `GDPC1` | 24 | Quarterly data needs roughly 6 years to calculate year-over-year values plus a 5-year trend |
| `CPIAUCSL` | 72 | Monthly data needs roughly 6 years to calculate year-over-year values plus a 5-year trend |

The validator should also report the raw date span:

- earliest observation date
- latest observation date
- total years covered
- number of non-null observations

The detailed classifier formula will be decided in Stage 2. Stage 1 only needs
to confirm that the data history is deep enough for plausible Stage 2 formulas.

## Point-In-Time Safety Checks

For each required series and `as_of` date, Stage 1 should verify:

1. The response came from Lumibot's official FRED API path.
2. The response marks `point_in_time_safe` as `true`.
3. The response marks `uses_revised_data` as `false`.
4. No observation has `date > as_of`.
5. The raw observations include `realtime_start` and `realtime_end` fields when
   FRED provides them.
6. The report clearly states whether the validation is relying on FRED vintage
   data or revised current data.

The current Lumibot implementation returns:

```text
source = "fred_api"
point_in_time_safe = true
uses_revised_data = false
```

Those fields should be treated as part of the validation evidence, not ignored.

## Publication Lag Expectations

The latest available observation will normally be earlier than the trading date.

Examples:

- Quarterly GDP may lag the trading date by months.
- Monthly CPI may lag the trading date by weeks or a month.

This is not automatically a failure. The validator should distinguish:

```text
good: latest observation is before as_of because macro data is published with lag
bad: latest observation is after as_of, which would imply look-ahead data
bad: latest observation is so old that the classifier would be stale
```

Stage 1 should not decide the final acceptable staleness threshold for trading.
It should report the lag clearly so Stage 2 can choose the policy.

Required lag fields:

- `latest_observation_date`
- `as_of`
- `observation_lag_days`

## API Key And Secret Handling

Stage 1 requires `FRED_API_KEY`.

The validator should support either:

```text
FRED_API_KEY already set in the environment
```

or:

```text
--env-file <path-to-fred-env-file>
```

The validator must never write the API key to reports, logs, exceptions, or
trace output.

If no FRED API key is available, Stage 1 should produce a clear blocked result,
not a misleading data failure:

```json
{
  "status": "blocked",
  "reason": "missing_fred_api_key"
}
```

## Cache Handling

The validator should use a dedicated cache directory for each validation run so
the report can be audited later.

Preferred structure:

```text
artifacts/macro_regime_data_availability/<run_id>/
  fred_cache/
  fred_growth_inflation_data_availability.json
  fred_growth_inflation_data_availability.md
```

The implementation should set:

```text
LUMIBOT_FRED_CACHE_DIR=<artifact_dir>/fred_cache
```

for the validation run unless the user explicitly overrides it.

The report should include the cache path, but not the API key.

## Validator Command

The detailed implementation plan should create a command like:

```powershell
.venv\Scripts\python.exe scripts\validate_fred_growth_inflation_data.py `
  --env-file <path-to-fred-env-file> `
  --as-of 2024-09-05 `
  --as-of 2010-01-01 `
  --include-recent
```

The exact script name can change during implementation, but the command must be
simple enough to run repeatedly from the project root.

## Report Schema

The JSON report should be the source of truth.

Top-level fields:

```json
{
  "schema_version": 1,
  "status": "passed",
  "generated_at": "2026-08-11T00:00:00Z",
  "toolchain": {
    "client": "lumibot.macro.FREDMacroData",
    "uses_official_fred_api": true,
    "cache_dir": "artifacts/macro_regime_data_availability/<run_id>/fred_cache"
  },
  "required_series": ["GDPC1", "CPIAUCSL"],
  "optional_series": ["PCEPI", "GDP"],
  "as_of_results": []
}
```

Each `as_of_results` item should include:

```json
{
  "label": "project_recent_backtest",
  "as_of": "2024-09-05",
  "status": "passed",
  "series": {
    "GDPC1": {
      "status": "passed",
      "available": true,
      "source": "fred_api",
      "point_in_time_safe": true,
      "uses_revised_data": false,
      "observation_count": 80,
      "non_null_observation_count": 80,
      "earliest_observation_date": "2004-10-01",
      "latest_observation_date": "2024-04-01",
      "observation_lag_days": 157,
      "has_enough_history_for_5y_trend": true,
      "future_observation_count": 0,
      "sample_latest_observations": [
        {
          "date": "2024-04-01",
          "value": 23000.0,
          "realtime_start": "2024-09-05",
          "realtime_end": "2024-09-05"
        }
      ],
      "warnings": []
    }
  },
  "warnings": [],
  "errors": []
}
```

The example numeric value above is illustrative. The implementation should
write actual returned values.

## Markdown Report

The Markdown report should translate the JSON into a human-readable review.

It should include:

1. Overall status.
2. Which FRED series were tested.
3. Which `as_of` dates were tested.
4. For each date, whether each series passed.
5. Latest available observation and lag.
6. Whether there is enough history for a five-year trend.
7. Any warnings or blocked conditions.
8. A final recommendation:
   - safe to proceed to Stage 2
   - blocked by missing API key
   - blocked by missing data
   - proceed with documented point-in-time limitation

The Markdown report should not include the full raw time series unless a failure
requires a small diagnostic sample.

## Status Rules

Stage 1 should use clear statuses.

Top-level status:

| Status | Meaning |
|---|---|
| `passed` | All required series pass for all required `as_of` dates |
| `failed` | Required data was reachable, but one or more required checks failed |
| `blocked` | Validation could not run because of missing key, network failure, or unavailable FRED service |

Series-level status:

| Status | Meaning |
|---|---|
| `passed` | Series is available, point-in-time safe, has no future observations, and has enough history |
| `warning` | Series is usable but has a notable limitation such as high observation lag |
| `failed` | Series is missing, unsafe, future-dated, or lacks enough history |
| `blocked` | Series could not be checked because of external access failure |

## Pass Criteria

Stage 1 passes only if all of the following are true:

1. `GDPC1` is available for every required `as_of` date.
2. `CPIAUCSL` is available for every required `as_of` date.
3. Both required series use `source = "fred_api"`.
4. Both required series report `point_in_time_safe = true`.
5. Both required series report `uses_revised_data = false`.
6. No required series has observations after the tested `as_of` date.
7. `GDPC1` has at least 24 non-null observations for every required `as_of`.
8. `CPIAUCSL` has at least 72 non-null observations for every required `as_of`.
9. The JSON and Markdown reports are generated successfully.

If optional series fail, the top-level status should remain `passed` if all
required series pass. Optional failures should be recorded as warnings.

## Failure Examples

Stage 1 should fail if:

- `GDPC1` cannot be fetched for `2024-09-05`.
- `CPIAUCSL` returns fewer than 72 non-null observations for an `as_of` date.
- Any observation date is later than `as_of`.
- The result comes from revised CSV or another non-point-in-time source.
- The Lumibot FRED response does not mark the data as point-in-time safe.

Stage 1 should be blocked if:

- `FRED_API_KEY` is missing.
- FRED is unreachable.
- The network is down.
- The API returns authentication or quota errors.

## Testing Requirements

The implementation plan should include both unit tests and one real validation
run.

Unit tests should use mocked FRED responses and should verify:

1. Required series pass with enough historical observations.
2. Missing `FRED_API_KEY` returns `blocked`, not `failed`.
3. A future-dated observation causes failure.
4. Too few observations causes failure.
5. Optional series failures do not fail the whole run.
6. API keys are not written into report files.
7. JSON and Markdown reports are both generated.

Real validation should run once with the user's local `FRED_API_KEY` and produce
artifacts under:

```text
artifacts/macro_regime_data_availability/<run_id>/
```

## Verification Commands

The implementation plan should include commands similar to:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_fred_growth_inflation_data_availability.py -q
```

```powershell
.venv\Scripts\python.exe -m ruff check scripts\validate_fred_growth_inflation_data.py tests\test_fred_growth_inflation_data_availability.py
```

```powershell
.venv\Scripts\python.exe scripts\validate_fred_growth_inflation_data.py --env-file <path-to-fred-env-file> --include-recent --as-of 2024-09-05 --as-of 2010-01-01
```

The final implementation verification should report:

- test status
- lint status
- generated artifact directory
- JSON report path
- Markdown report path
- final Stage 1 recommendation

## Non-Goals

Stage 1 must not:

- Replace `mock_macro_regime_classifier`.
- Create `AITradingTeamGrowthInflationQuadrantStrategy`.
- Modify agent prompts.
- Modify trading behavior.
- Run an LLM.
- Run a backtest.
- Decide the final Growth / Inflation formula.
- Decide the final acceptable macro data lag policy.
- Add many alternative macro indicators.
- Add multi-country macro logic.

## Open Questions Deferred To Stage 2

These questions should not block Stage 1:

1. Whether GDP should use a 6-month lag exactly as in the Research Affiliates
   article.
2. Whether CPI should use a 1-month lag exactly as in the article.
3. Whether the trend is a simple rolling average, exponential average, or another
   smoother.
4. Whether inflation should use CPI, PCE, or a blended signal.
5. Whether the classifier should return hard quadrant labels only or also a
   confidence score.
6. Whether basket weights should remain fixed at 50/25/25/0 or become
   continuous later.

## Acceptance Criteria

This spec is satisfied when:

1. A Stage 1 validation script is specified in the implementation plan.
2. The script checks `GDPC1` and `CPIAUCSL` through Lumibot's existing FRED
   data layer.
3. The script validates current and historical `as_of` behavior.
4. The script writes JSON and Markdown reports.
5. The reports clearly say whether Stage 2 can proceed.
6. No trading strategy behavior changes are included in Stage 1.

## Next Step

After this spec is approved, write the Stage 1 implementation plan.

The implementation plan should focus on:

1. Building the validator script.
2. Adding focused unit tests with mocked FRED responses.
3. Running one real validation using a local env file containing `FRED_API_KEY=<your-fred-api-key>`.
4. Reviewing the generated report before deciding whether Stage 2 can begin.
