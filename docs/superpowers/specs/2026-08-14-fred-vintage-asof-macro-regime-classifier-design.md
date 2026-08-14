# FRED Vintage As-Of Macro Regime Classifier Design

## Purpose

Upgrade the real Growth / Inflation quadrant classifier so it uses the FRED
vintage that was knowable on the simulated trading date.

The current real classifier is point-in-time aware at the FRED helper layer, but
the strategy-level classifier still defaults to a conservative artificial lag:

```text
growth_lag_months = 6
inflation_lag_months = 1
```

That lagged mode is simple, but it can be too stale and it can also confuse two
different date concepts:

```text
observation date
  The period the macro value describes.

real-time / vintage date
  The date when that value or revision was known.
```

For GDP, `date = 2025-01-01` means the observation describes the quarter that
starts on 2025-01-01. It does not mean the value was published on 2025-01-01.

The target behavior is:

```text
Backtest date: 2025-07-07
Classifier asks FRED: what GDPC1 and CPIAUCSL values were known as of 2025-07-07?
Classifier computes the quadrant from that vintage only.
```

This mirrors the built-in `alpaca_news` pattern:

```text
Use current simulated time by default.
Clamp future user/model-requested dates.
Return requested and effective dates for auditability.
```

## Current Evidence

The existing `alpaca_news` built-in tool is the model to copy:

```text
lumibot/components/agents/builtins.py
```

It:

- reads `strategy.get_datetime()`;
- defaults omitted `end` to the current simulated datetime;
- clamps future `end` values during backtests;
- returns `requested_end`, `effective_end`, and `lookahead_clamped`;
- documents look-ahead protection in the model-facing tool description;
- has tests that verify the clamp behavior.

The existing FRED helper already supports FRED/ALFRED real-time data:

```text
lumibot/macro/fred.py
```

`FREDMacroData.get_series(..., as_of=...)` sends:

```text
realtime_start = as_of
realtime_end = as_of
```

to the official FRED API.

The bug-prone part is in the real macro classifier:

```text
lumibot/example_strategies/fred_growth_inflation_regime_classifier.py
```

It fetches FRED payloads with `as_of = trading_date`, then applies artificial
`data_cutoff = trading_date - lag_months` later. That means the payload may
contain values that were known on the trading date but were not known at the
artificial cutoff date.

## Design Decision

Create a new default mode:

```text
fred_ra_vintage_asof
```

This mode uses the simulated trading date as the FRED vintage date by default.

Keep the existing lagged mode as a legacy comparison mode:

```text
fred_ra_simple_lagged
```

The real strategy should default to the new vintage mode. The old lagged mode
should remain available for explicit experiments, but it should no longer be the
main strategy default.

## Non-Goals

This work must not:

- change the Research Affiliates-style 50/25/25/0 quadrant weight mapping;
- change basket universes;
- change basket agents, portfolio decision, or execution behavior except for
  fields they must preserve from the macro report;
- add new macro series;
- let the LLM choose macro series or write macro formulas;
- integrate BEA release calendars;
- implement minute-level release-time handling such as 8:30 AM publication
  checks;
- redesign the Growth / Inflation formula;
- remove the legacy lagged mode immediately.

## Date Concepts

The implementation must keep these concepts separate.

### Trading Date

The simulated strategy date:

```text
strategy.get_datetime().date()
```

This is the default macro decision date.

### Requested As-Of

The date requested by the caller or model.

If omitted, it should default to the trading date.

### Effective As-Of

The date actually used for the FRED vintage query after validation and
look-ahead protection.

In backtests:

```text
effective_as_of = min(requested_as_of, current_simulated_date)
```

### Observation Date

The date attached to a FRED observation row.

This describes the period the macro value belongs to. It is not the release
date.

### Real-Time Start / End

The FRED/ALFRED vintage period attached to an observation.

This tells us when a value or revision was known.

## As-Of Policies

The classifier should support a small explicit set of policies.

### same_day_vintage

Default.

Use the current simulated trading date as the FRED vintage date.

```text
trading_date = 2025-07-07
effective_as_of = 2025-07-07
```

This is the best initial default for weekly backtests because the system is not
trying to trade macro releases at minute-level precision.

### previous_day_vintage

Optional conservative policy.

Use the previous calendar day as the FRED vintage date.

```text
trading_date = 2025-07-07
effective_as_of = 2025-07-06
```

This avoids ambiguity around whether same-day data was published before the
strategy ran. It is not the default because it can be unnecessarily stale for a
weekly system.

### explicit_vintage

Optional developer/testing policy.

Allow the caller to request a specific `as_of` date. During backtests, future
requested dates must be clamped to the current simulated date.

### legacy_lagged

Compatibility policy for the existing behavior.

This should map to the current `fred_ra_simple_lagged` behavior and remain
available for comparison tests. It must be clearly labeled as legacy.

## Tool Contract

The model-facing tool remains:

```text
macro_regime_classifier
```

The LLM-facing concept does not change: the macro allocation agent calls one
tool and receives the regime and basket weights.

### Inputs

The tool should accept:

```text
date: optional date
mode: optional string
growth_series_id: optional string
inflation_series_id: optional string
trend_years: optional integer
as_of_policy: optional string
requested_as_of: optional date
```

For legacy compatibility, the tool may still accept:

```text
growth_lag_months
inflation_lag_months
```

Those fields are meaningful only in legacy lagged mode.

### Output

The output should include:

```json
{
  "tool": "macro_regime_classifier",
  "status": "passed",
  "mock": false,
  "mode": "fred_ra_vintage_asof",
  "date": "2025-07-07",
  "as_of_policy": "same_day_vintage",
  "requested_as_of": "2025-07-07",
  "effective_as_of": "2025-07-07",
  "lookahead_clamped": false,
  "regime": "growth_down_inflation_down",
  "growth_direction": "down",
  "inflation_direction": "down",
  "previous_regime": "growth_up_inflation_down",
  "regime_changed": true,
  "basket_weights": {
    "equity": 0.25,
    "commodity": 0.25,
    "tips": 0.0,
    "nominal_bond": 0.5
  },
  "growth_evidence": {},
  "inflation_evidence": {},
  "data_quality": {},
  "confidence": {},
  "reason_brief": "..."
}
```

### Evidence Fields

Each evidence object should include:

```text
axis
series_id
series_name
frequency
as_of
latest_observation_date
latest_realtime_start
latest_realtime_end
comparison_observation_date
comparison_realtime_start
comparison_realtime_end
latest_value
comparison_value
metric_name
metric_value
trend_years
trend_window_observations
trend_value
margin
direction
observation_lag_days
```

Legacy lag fields may appear only in legacy mode:

```text
lag_months
data_cutoff
```

### Data Quality Fields

`data_quality` should include:

```text
status
source
point_in_time_safe
uses_revised_data
required_series
as_of_policy
requested_as_of
effective_as_of
lookahead_clamped
warnings
errors
```

If the caller requested a future `as_of`, the tool should still return a passed
result when possible, but it must expose:

```text
lookahead_clamped = true
requested_as_of = future request
effective_as_of = current simulated date
```

## Calculation Rules

The vintage mode should keep the existing metric logic:

```text
growth metric = year-over-year change in real GDP
inflation metric = year-over-year change in CPI
direction = latest metric above or below trailing five-year trend
```

The difference is the data input:

```text
Use FRED observations from the effective vintage.
Do not apply artificial lag-month cutoffs in the default vintage mode.
```

The classifier should use only observations whose observation date is at or
before `effective_as_of`. `FREDMacroData` already filters future observation
dates, but the classifier may keep a defensive check.

## Strategy Integration

The real quadrant strategy should default to:

```text
macro_regime_mode = fred_ra_vintage_asof
as_of_policy = same_day_vintage
```

It should stop sending lag-month fields to the macro allocation agent context
unless the strategy is explicitly configured to run legacy lagged mode.

The strategy validator should accept:

```text
macro_report["as_of"] == macro_report["effective_as_of"]
macro_report["effective_as_of"] <= current_date
macro_report["mode"] == configured macro_regime_mode
macro_report["as_of_policy"] == configured as_of_policy
```

It should no longer require:

```text
growth_evidence["lag_months"]
inflation_evidence["lag_months"]
growth_evidence["data_cutoff"]
inflation_evidence["data_cutoff"]
```

for the default vintage mode.

## Prompt Updates

### Macro Allocation Agent System Prompt

Update the macro allocation prompt to say:

```text
Call macro_regime_classifier.
Do not classify the regime yourself.
Do not manually adjust the basket weights.
Preserve requested_as_of, effective_as_of, lookahead_clamped, as_of_policy,
growth_evidence, inflation_evidence, data_quality, confidence, and reason_brief.
If the tool returns blocked or failed, report that plainly and do not continue.
```

Remove language that implies the macro agent should reason about GDP/CPI lags.

### Macro Allocation Agent Task Prompt

Update the task prompt to say:

```text
Run the FRED vintage macro allocation step by calling macro_regime_classifier.
Return one JSON object preserving the tool result fields.
```

Do not ask the model to calculate the quadrant.

### Tool Description

The tool description should explicitly teach the Alpaca-news-style safety
contract:

```text
In backtests, this tool uses the current simulated date as the FRED vintage
date by default. Future requested as_of dates are clamped to avoid look-ahead
bias. The output reports requested_as_of, effective_as_of, and
lookahead_clamped. Do not manually recalculate its output.
```

### Downstream Prompts

Basket agents, portfolio decision agent, and execution agent should not receive
new macro calculation responsibilities.

They may receive the new macro report fields through context, but they should
continue to treat `basket_weights` as authoritative.

## Trace And Replay UI Requirements

The trace should naturally record the full tool input and output.

The output must contain enough fields for the replay UI to show:

```text
requested_as_of
effective_as_of
lookahead_clamped
as_of_policy
latest GDP observation date
latest GDP realtime_start
latest CPI observation date
latest CPI realtime_start
regime
basket weights
```

No replay UI redesign is required in this feature. The existing tool output
panels should be sufficient once the tool returns the fields.

## Backward Compatibility

Existing code that refers to `macro_regime_classifier` should not need a tool
name change.

The mock strategy remains unchanged.

The legacy lagged mode remains callable for comparison, but the real strategy
uses the new vintage mode by default.

Existing docs/specs that describe `fred_ra_simple_lagged` as the main default
should be considered superseded by this spec.

## Error Handling

The tool should return blocked or failed payloads instead of raising for normal
data issues.

Expected blocked or failed cases include:

- missing `FRED_API_KEY`;
- unsupported mode;
- unsupported `as_of_policy`;
- too few usable observations;
- FRED payload not point-in-time safe;
- future observation dates after `effective_as_of`;
- missing realtime fields;
- malformed requested date.

Programmer errors may still raise.

## Testing Requirements

Unit tests should cover:

1. Vintage mode requests FRED with `as_of = current simulated date`.
2. Default omitted requested_as_of resolves to current simulated date.
3. Future requested_as_of is clamped in backtesting and reports
   `lookahead_clamped = true`.
4. Same-day vintage output includes `requested_as_of`, `effective_as_of`,
   `as_of_policy`, and evidence realtime fields.
5. Legacy lagged mode still accepts `growth_lag_months` and
   `inflation_lag_months`.
6. Default real strategy parameters use `fred_ra_vintage_asof` and
   `same_day_vintage`.
7. Real strategy canonical validation accepts vintage evidence without
   `lag_months` or `data_cutoff`.
8. Macro allocation agent prompt and tool description mention vintage as-of
   safety and look-ahead clamping.
9. Mock quadrant strategy tests still pass.
10. A short real-strategy backtest can run and show the new fields in the macro
    tool result trace.

## Validation Commands

The implementation plan should include focused tests such as:

```text
python -m pytest tests/test_fred_macro.py tests/test_ai_trading_team_growth_inflation_quadrant.py
python -m pytest tests/test_fred_growth_inflation_data_availability.py
python -m pytest tests/test_growth_inflation_regime_scan.py
```

It should also include one short benchmark run for the real strategy using the
current default model configuration.

## Acceptance Criteria

The feature is complete when:

1. The real quadrant strategy defaults to FRED vintage same-day as-of mode.
2. The macro classifier no longer defaults to artificial GDP/CPI lag cutoffs.
3. Future requested as-of dates are clamped in backtests.
4. The macro report exposes requested/effective as-of provenance.
5. The trace/UI can show which FRED vintage was used.
6. Existing mock, basket, portfolio decision, execution, and replay behavior is
   not regressed.
7. Focused tests and one short real-strategy backtest pass.

