# Real FRED Growth / Inflation Regime Classifier Design

## Purpose

This spec defines Stage 2 of the real macro regime classifier roadmap.

Stage 1 proved that Lumibot can retrieve the required FRED macro data:

```text
GDPC1   - Real Gross Domestic Product
CPIAUCSL - Consumer Price Index for All Urban Consumers
```

Stage 2 now replaces the mock Growth / Inflation quadrant generator with a
deterministic FRED-backed classifier.

The goal is not to redesign the full trading workflow. The goal is to replace
the current fake `macro_regime_classifier` behavior with a real classifier that
keeps the downstream agent workflow stable.

## Relationship To Existing Specs

This spec implements Stage 2 from:

```text
docs/superpowers/specs/2026-08-11-real-macro-regime-classifier-two-stage-roadmap-design.md
```

It depends on Stage 1:

```text
docs/superpowers/specs/2026-08-11-fred-growth-inflation-data-availability-design.md
```

Stage 1 produced a passing validation report:

```text
artifacts/macro_regime_data_availability/stage1-real-validation/
  fred_growth_inflation_data_availability.json
  fred_growth_inflation_data_availability.md
```

The Stage 1 report showed that `GDPC1`, `CPIAUCSL`, and optional comparison
series can be fetched for recent and historical `as_of` dates with enough
history for a five-year trend calculation.

## Current Project Context

The current mock quadrant workflow is implemented in:

```text
lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py
```

The current workflow is:

```text
macro_allocation_agent
  -> macro_regime_classifier tool
  -> equity_basket_agent
  -> commodity_basket_agent
  -> tips_basket_agent
  -> nominal_bond_basket_agent
  -> portfolio_decision_agent
  -> target_portfolio_to_execution_plan tool
  -> execution_agent
  -> execution_plan_execute tool
```

This workflow should remain conceptually intact.

Stage 2 should change only the macro classifier source:

```text
Before:
macro_regime_classifier = deterministic fake date/seed classifier

After:
macro_regime_classifier = deterministic FRED-backed Growth / Inflation classifier
```

The mock strategy must remain available for workflow, trace, and UI testing.

## Core Design Principle

The macro regime should be computed by normal Python code, not by an LLM.

The macro allocation agent should call one tool:

```text
macro_regime_classifier
```

The tool should fetch FRED data, calculate the Growth / Inflation evidence,
classify the quadrant, and return a structured result. The LLM should not write
macro formulas, choose FRED series, or infer the quadrant from prose.

This keeps the system:

- cheaper to run
- easier to test
- less sensitive to model choice
- easier to explain in the replay UI
- less likely to hallucinate macro data or formulas

## Strategy Structure

Stage 2 should not mutate the mock strategy into the real strategy.

Preferred structure:

```text
AITradingTeamMockGrowthInflationQuadrantStrategy
  remains the fake classifier workflow test strategy

AITradingTeamGrowthInflationQuadrantStrategy
  uses the real FRED-backed classifier
```

The implementation may either:

1. create a new strategy file that reuses helpers from the mock strategy, or
2. refactor shared workflow helpers into reusable functions/classes before
   creating the real strategy.

The implementation should choose the smaller safe edit, but must keep the mock
strategy import path and behavior working.

Suggested new file:

```text
lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py
```

Suggested test file:

```text
tests/test_ai_trading_team_growth_inflation_quadrant.py
```

## Classifier Tool

The real strategy should expose a tool named:

```text
macro_regime_classifier
```

The tool name should match the mock tool name so the agent workflow and replay
UI remain easy to compare.

The real tool metadata should distinguish it from the mock tool:

```json
{
  "kind": "fred_macro_regime",
  "mock": false,
  "replay_on_cache": true
}
```

The tool should be available only to `macro_allocation_agent`.

Basket agents, `portfolio_decision_agent`, and `execution_agent` should not
receive this tool.

## Classifier Inputs

The tool should accept a small input surface.

Required:

```text
date
```

Optional:

```text
mode
growth_series_id
inflation_series_id
growth_lag_months
inflation_lag_months
trend_years
```

Default values:

| Input | Default | Reason |
|---|---:|---|
| `mode` | `fred_ra_simple_lagged` | First real Research Affiliates-style mode |
| `growth_series_id` | `GDPC1` | Real GDP |
| `inflation_series_id` | `CPIAUCSL` | CPI inflation |
| `growth_lag_months` | `6` | Literature-aligned conservative GDP lag |
| `inflation_lag_months` | `1` | Literature-aligned conservative CPI lag |
| `trend_years` | `5` | Research Affiliates-style trend comparison |

The strategy may also expose these as parameters so future experiments can
change them without editing code.

## FRED Data Access

The classifier should use the existing Lumibot FRED data layer:

```text
lumibot.macro.FREDMacroData
```

It must not introduce a second FRED client unless the current Lumibot client
proves inadequate.

For each tool call:

```text
FREDMacroData(strategy=strategy)
  -> get_series("GDPC1", as_of=date, start=<history_start>, end=date)
  -> get_series("CPIAUCSL", as_of=date, start=<history_start>, end=date)
```

The tool should rely on `FREDMacroData` point-in-time behavior:

```text
source = "fred_api"
point_in_time_safe = true
uses_revised_data = false
```

If any required series does not satisfy these fields, the classifier must not
silently proceed.

## Lag Handling

Stage 2 should apply explicit conservative data lags after fetching point-in-time
FRED data.

The default mode should be:

```text
fred_ra_simple_lagged
```

For each axis:

```text
axis_data_cutoff = trading_date - axis_lag_months
```

The classifier should fetch point-in-time data as of the actual trading date,
then select the latest observation whose observation date is on or before the
axis data cutoff.

Example:

```text
trading date: 2024-09-05
growth_lag_months: 6
growth data cutoff: 2024-03-05
usable GDPC1 observation: latest observation date <= 2024-03-05

inflation_lag_months: 1
inflation data cutoff: 2024-08-05
usable CPIAUCSL observation: latest observation date <= 2024-08-05
```

This is intentionally conservative. It prevents the first real classifier from
accidentally relying on macro observations that might be too freshly published
or revised for the intended Research Affiliates-style logic.

The output must expose both:

- `as_of`: the simulated trading date
- `data_cutoff`: the lag-adjusted cutoff date used for each axis

## Metric Formula

The first real classifier should use year-over-year percent change.

Growth axis:

```text
growth_metric = GDPC1 latest usable level / GDPC1 level four quarters earlier - 1
growth_trend = average of the last 20 quarterly growth_metric values
```

Inflation axis:

```text
inflation_metric = CPIAUCSL latest usable level / CPIAUCSL level twelve months earlier - 1
inflation_trend = average of the last 60 monthly inflation_metric values
```

The trend window should include the current metric as the final point in the
rolling window.

Minimum raw observations:

| Series | Minimum Raw Observations | Why |
|---|---:|---|
| `GDPC1` | `24` | four-quarter YoY plus 20-quarter trend |
| `CPIAUCSL` | `72` | twelve-month YoY plus 60-month trend |

If there is not enough usable history after lag filtering, the classifier should
return a structured failure instead of producing a quadrant.

## Direction Rules

The first version should use a simple deterministic comparison:

```text
growth_direction = "up" if growth_metric > growth_trend else "down"
inflation_direction = "up" if inflation_metric > inflation_trend else "down"
```

Equality should be treated as `down`.

No confidence bands, dead zones, or continuous scores should be added in this
first real version. Those are future improvements.

The output should still include the exact margins:

```text
growth_margin = growth_metric - growth_trend
inflation_margin = inflation_metric - inflation_trend
```

This lets the UI and later research identify fragile classifications without
changing the first classifier's hard quadrant behavior.

## Regime Mapping

The classifier should produce exactly one of these regimes:

```text
growth_up_inflation_down
growth_up_inflation_up
growth_down_inflation_up
growth_down_inflation_down
```

The direction-to-regime mapping is:

| Growth Direction | Inflation Direction | Regime |
|---|---|---|
| `up` | `down` | `growth_up_inflation_down` |
| `up` | `up` | `growth_up_inflation_up` |
| `down` | `up` | `growth_down_inflation_up` |
| `down` | `down` | `growth_down_inflation_down` |

## Basket Weight Mapping

The real classifier should preserve the current Research Affiliates-style
50/25/25/0 mapping already used by the mock strategy.

| Regime | Equity | Commodity | TIPS | Nominal Bond |
|---|---:|---:|---:|---:|
| `growth_up_inflation_down` | 0.50 | 0.25 | 0.00 | 0.25 |
| `growth_up_inflation_up` | 0.25 | 0.50 | 0.25 | 0.00 |
| `growth_down_inflation_up` | 0.00 | 0.25 | 0.50 | 0.25 |
| `growth_down_inflation_down` | 0.25 | 0.25 | 0.00 | 0.50 |

The real classifier must not optimize or modify these weights in Stage 2.

## Output Contract

The real classifier output should be compatible with the mock classifier's
outer contract, while adding evidence fields.

Required top-level fields for a successful result:

```json
{
  "tool": "macro_regime_classifier",
  "status": "passed",
  "mock": false,
  "mode": "fred_ra_simple_lagged",
  "date": "2024-09-05",
  "as_of": "2024-09-05",
  "regime": "growth_down_inflation_up",
  "growth_direction": "down",
  "inflation_direction": "up",
  "previous_regime": null,
  "regime_changed": false,
  "basket_weights": {
    "equity": 0.0,
    "commodity": 0.25,
    "tips": 0.5,
    "nominal_bond": 0.25
  },
  "growth_evidence": {},
  "inflation_evidence": {},
  "data_quality": {},
  "confidence": {},
  "reason_brief": "..."
}
```

The example regime above is illustrative. Tests should assert formulas using
controlled fake data, not this exact real-world output.

## Evidence Fields

`growth_evidence` should include:

```json
{
  "axis": "growth",
  "series_id": "GDPC1",
  "series_name": "Real Gross Domestic Product",
  "frequency": "quarterly",
  "lag_months": 6,
  "data_cutoff": "2024-03-05",
  "latest_observation_date": "2024-01-01",
  "comparison_observation_date": "2023-01-01",
  "latest_value": 123.45,
  "comparison_value": 120.0,
  "metric_name": "year_over_year_change",
  "metric_value": 0.02875,
  "trend_years": 5,
  "trend_window_observations": 20,
  "trend_value": 0.031,
  "margin": -0.00225,
  "direction": "down"
}
```

`inflation_evidence` should use the same shape with:

```text
series_id = CPIAUCSL
frequency = monthly
lag_months = 1
trend_window_observations = 60
```

## Data Quality Fields

`data_quality` should include enough information for debugging and UI display:

```json
{
  "status": "passed",
  "source": "fred_api",
  "point_in_time_safe": true,
  "uses_revised_data": false,
  "required_series": ["GDPC1", "CPIAUCSL"],
  "warnings": [],
  "errors": []
}
```

The classifier should warn, but not necessarily fail, when:

- a latest usable observation is much older than expected because of the
  explicit lag policy
- metric margins are very close to zero
- optional comparison fields are unavailable

The classifier should fail or block when:

- `FRED_API_KEY` is missing
- required FRED data cannot be retrieved
- required series are not point-in-time safe
- required series use revised data
- usable observations include future dates
- there is not enough usable history after lag filtering

## Confidence Field

The `confidence` field should describe data and classification stability, not
expected investment performance.

Suggested shape:

```json
{
  "level": "high",
  "basis": "data_quality_and_axis_margins",
  "growth_margin": -0.00225,
  "inflation_margin": 0.0041,
  "notes": []
}
```

The first implementation may keep this simple:

- `high` when required data passes and both margins are not near zero
- `medium` when required data passes but at least one margin is near zero
- `low` when the classifier can return evidence but should not trade from it

If the implementation cannot justify a meaningful confidence level without
adding arbitrary thresholds, it may set:

```json
{
  "level": "not_scored",
  "basis": "first deterministic classifier returns margins instead of a score"
}
```

The key requirement is transparency, not pretending to have predictive
confidence.

## Failure And Blocked Results

The classifier should not hallucinate a regime when required data is unavailable.

When it cannot classify because of expected runtime conditions, it should
produce a structured result with `status = "blocked"` or `status = "failed"`.
Expected runtime conditions include missing credentials, unreachable FRED data,
insufficient history, future-dated observations, or non-point-in-time payloads.

The strategy should inspect this status and stop the trading iteration before
running basket, portfolio, or execution agents.

Unexpected programmer errors may still raise exceptions, but normal data
availability problems should be visible in the tool result and replay UI.

Preferred blocked result shape:

```json
{
  "tool": "macro_regime_classifier",
  "status": "blocked",
  "mock": false,
  "mode": "fred_ra_simple_lagged",
  "date": "2024-09-05",
  "as_of": "2024-09-05",
  "reason": "missing_fred_api_key",
  "data_quality": {
    "status": "blocked",
    "warnings": [],
    "errors": ["FRED_API_KEY is required"]
  }
}
```

If the tool returns `status != "passed"`, the strategy should not run basket,
portfolio, or execution agents for that trading iteration.

## Agent Workflow Integration

The real strategy should keep the same downstream agent structure as the mock
quadrant strategy.

### Macro Allocation Agent

Tool permissions:

```text
macro_regime_classifier only
```

It should not receive generic FRED tools in Stage 2. The classifier tool owns
the FRED calls and formula.

System prompt intent:

```text
Call the real macro_regime_classifier. Return its structured regime, basket
weights, evidence summary, and reason_brief. Do not classify the macro regime
yourself. Do not place orders.
```

Task prompt intent:

```text
Run the real macro allocation step for the current date and return one JSON
object with status, regime, basket_weights, regime_changed, evidence, and
reason_brief.
```

### Basket Agents

Basket agents should remain conceptually unchanged.

Each basket agent receives:

- date
- basket_id
- basket_symbols
- target_weight
- macro_allocation_report

Each basket agent chooses one symbol if the basket is active, or reports
inactive if target weight is zero.

No basket agent should reclassify macro conditions.

### Portfolio Decision Agent

The portfolio decision agent should remain responsible for turning selected
symbols and target weights into a target portfolio, then calling:

```text
target_portfolio_to_execution_plan
```

It should not redo macro classification.

### Execution Agent

The execution agent should remain responsible only for executing the strict
`execution_plan` by calling:

```text
execution_plan_execute
```

Stage 2 should not change execution policy unless required by integration.

## Prompt Requirements

Prompt changes should be small and role-specific.

Required macro prompt changes:

- Replace "mock macro_regime_classifier" language with "real FRED-backed
  macro_regime_classifier".
- Tell the macro allocation agent not to calculate the regime manually.
- Tell the macro allocation agent to preserve the tool result's structured
  fields.
- Tell the macro allocation agent to return blocked/failed status plainly if
  the tool cannot classify.

Do not add long GDP/CPI formula explanations to the agent prompt. The formula
belongs in code and tool description, not in the LLM's reasoning burden.

Tool description should be concise but explicit:

```text
Classify the Growth / Inflation quadrant using FRED point-in-time GDPC1 and
CPIAUCSL data. The tool applies configured lags, compares year-over-year
metrics with five-year rolling trends, and returns basket weights plus evidence.
Do not manually recalculate its output.
```

## Trace And UI Expectations

The existing trace and replay UI should continue to work.

A real classifier run should make it clear that:

- the macro allocation agent called `macro_regime_classifier`
- the tool was real, not mock
- the tool returned FRED evidence
- the selected regime and basket weights came from deterministic tool output

The UI does not need a new panel in Stage 2, but the tool output must be
human-readable enough for the existing Tool Calls view.

The output should avoid huge raw time series. It should return evidence
summaries, not every raw FRED observation.

## Backtest Behavior

The first Stage 2 backtest should be short.

Recommended validation sequence:

1. Unit tests with fake FRED data.
2. Direct tool invocation with real FRED data for one date.
3. One-day backtest of the real strategy.
4. Two-day backtest only after the one-day run has a sane trace.

The first one-day backtest should verify:

- macro allocation uses `mock = false`
- `status = passed`
- basket weights sum to 1.0
- only baskets with target weight above zero proceed to symbol selection
- portfolio decision still calls `target_portfolio_to_execution_plan`
- execution still calls `execution_plan_execute` if orders are needed

## Testing Requirements

Unit tests should not require network access.

They should use fake FRED clients or fake observations.

Required tests:

1. Calculates quarterly GDP year-over-year metric correctly.
2. Calculates monthly CPI year-over-year metric correctly.
3. Calculates five-year rolling trend correctly for GDP and CPI.
4. Applies growth and inflation lag cutoffs before selecting latest observations.
5. Maps all four direction combinations to the correct regime.
6. Maps all four regimes to the correct basket weights.
7. Equality between metric and trend is classified as `down`.
8. Insufficient GDP history fails or blocks classification.
9. Insufficient CPI history fails or blocks classification.
10. Non-point-in-time or revised FRED payload fails classification.
11. Future-dated observations fail classification.
12. Tool output includes required evidence fields.
13. Tool definition binds with `name = "macro_regime_classifier"` and
    `metadata.mock = false`.
14. Real strategy creates `macro_allocation_agent` with only the real classifier
    tool.
15. Mock strategy still creates the mock classifier and still passes existing
    tests.
16. Macro prompt no longer says "mock" in the real strategy.
17. Downstream basket, portfolio decision, and execution tool permissions remain
    unchanged from the mock quadrant workflow.

Optional real-data smoke test:

```powershell
.venv\Scripts\python.exe scripts\validate_fred_growth_inflation_data.py --env-file <path-to-fred-env-file> --include-optional --run-id stage1-real-validation
```

Then run a direct classifier or short strategy smoke with `FRED_API_KEY`
configured.

## Verification Commands

The implementation plan should include targeted commands similar to:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_inflation_quadrant.py -q
```

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py tests\test_fred_macro.py -q
```

```powershell
.venv\Scripts\python.exe -m ruff check lumibot\example_strategies\ai_trading_team_growth_inflation_quadrant.py tests\test_ai_trading_team_growth_inflation_quadrant.py
```

If a one-day backtest runner is added or reused, the implementation plan should
include the exact command and expected trace location.

## Acceptance Criteria

Stage 2 is complete when:

1. The mock quadrant strategy remains available and its existing tests pass.
2. A real FRED-backed Growth / Inflation quadrant strategy exists.
3. The real strategy exposes a `macro_regime_classifier` tool with
   `mock = false`.
4. The real tool uses `GDPC1` and `CPIAUCSL` through `FREDMacroData`.
5. The real tool applies explicit lags and five-year trend comparisons.
6. The real tool returns regime, directions, basket weights, evidence,
   data-quality details, and a concise reason.
7. The real strategy blocks safely when required macro data is unavailable.
8. Basket, portfolio decision, and execution agent responsibilities remain
   unchanged.
9. Unit tests cover formula, lag, mapping, failure, and integration behavior.
10. At least one real-data smoke or one-day backtest confirms the tool can run
    with the user's local `FRED_API_KEY`.

## Non-Goals

Stage 2 must not:

- Add extra macro indicators beyond `GDPC1` and `CPIAUCSL` as required inputs.
- Optimize basket weights.
- Add linearized regime strength weights.
- Add multi-country macro logic.
- Add LLM-based macro forecasting.
- Ask basket agents to judge macro conditions.
- Change execution-agent order policy.
- Change `target_portfolio_to_execution_plan` unless a real integration bug is
  discovered.
- Store or commit FRED API keys.
- Return full raw FRED time series to the LLM.

## Future Extensions

After Stage 2 is stable, future specs may consider:

- PCE inflation confirmation.
- GDP nowcast or monthly growth proxy support.
- confidence bands around trend comparisons.
- continuous regime strength scores.
- linearized basket weights instead of 50/25/25/0.
- more detailed macro evidence UI panels.
- multi-region regime classifiers.

These are intentionally outside Stage 2.

## Implementation Plan Guidance

The implementation plan should proceed in small steps:

1. Add deterministic classifier math with fake data tests.
2. Add FRED-backed tool binding.
3. Add the real strategy class and prompt changes.
4. Add integration tests comparing mock and real workflows.
5. Run a direct real-data smoke test.
6. Run a short real-strategy backtest only after unit tests pass.

Do not start by running a long backtest. The classifier math and output contract
must be proven first.
