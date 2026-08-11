# Real Macro Regime Classifier Two-Stage Roadmap

## Purpose

This spec defines the staged path for replacing the current mock Growth / Inflation quadrant generator with a real macro regime classifier.

The current strategy workflow already has the right agent boundary:

```text
macro_allocation_agent
  -> macro_regime_classifier tool
  -> four basket agents
  -> portfolio_decision_agent
  -> execution_agent
```

The goal is not to redesign this workflow. The goal is to replace the internals of `macro_regime_classifier` in a controlled way:

```text
Stage 1: prove the required FRED macro data is available and usable
Stage 2: build the real classifier using the verified data
```

## Background

The current mock quadrant strategy uses a deterministic fake classifier in:

```text
lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py
```

That mock tool returns:

- A Growth / Inflation regime such as `growth_up_inflation_down`.
- The corresponding basket weights.
- A mock flag and a short explanation.

Research Affiliates' Growth / Inflation TAA article motivates the target real classifier:

- Growth axis: real GDP growth compared with a rolling five-year growth trend.
- Inflation axis: inflation rate of change compared with a rolling five-year inflation trend.
- Tactical allocation: overweight the matching quadrant, underweight the diagonal opposite quadrant, and keep the other two neutral.

However, before implementing any classifier math, the project must first verify that the required macro data can be retrieved in the way our backtesting system needs.

## Stage 1: FRED Macro Data Availability

### Objective

Prove that the project can retrieve the required macro data from FRED through the current Lumibot FRED toolchain, including point-in-time `as_of` behavior for backtests.

Stage 1 answers this question:

> Can we reliably get enough real macro history, as of a simulated trading date, to support a future Growth / Inflation quadrant classifier?

### Primary Data

Stage 1 must focus on the minimum required series:

| Axis | FRED Series | Meaning | Expected Frequency |
|---|---|---|---|
| Growth | `GDPC1` | Real Gross Domestic Product | Quarterly |
| Inflation | `CPIAUCSL` | Consumer Price Index for All Urban Consumers | Monthly |

Optional secondary checks may inspect:

| Use | FRED Series | Meaning |
|---|---|---|
| Inflation comparison | `PCEPI` | PCE Price Index |
| Nominal GDP comparison | `GDP` | Gross Domestic Product |

Optional series must not become required for Stage 1 success.

### Required Checks

Stage 1 should verify:

- `GDPC1` can be retrieved.
- `CPIAUCSL` can be retrieved.
- Retrieval works for a specified `as_of` date, not only for today's date.
- Returned observations do not include future observations beyond the `as_of` date.
- Returned observations contain enough history to calculate a rolling five-year trend.
- Returned payloads expose enough metadata for audit and UI display:
  - `series_id`
  - `as_of`
  - observation dates
  - values
  - realtime/vintage fields if provided by FRED
  - `point_in_time_safe`
  - `uses_revised_data`

### Dates To Validate

Stage 1 should test at least:

| Date Type | Example | Purpose |
|---|---|---|
| Recent date | Current or near-current date | Confirm live data availability |
| Current project backtest date | `2024-09-05` | Confirm recent strategy windows can use the data |
| Older backtest date | `2010-01-01` or similar | Confirm longer historical backtests can get sufficient prior history |

The exact date list can be finalized in the Stage 1 detailed spec.

### Stage 1 Output

Stage 1 should produce a local data availability report, not a trading strategy change.

The report should summarize each tested date and series:

```json
{
  "status": "passed",
  "as_of": "2024-09-05",
  "series": {
    "GDPC1": {
      "available": true,
      "observation_count": 80,
      "latest_observation_date": "2024-04-01",
      "earliest_observation_date": "2004-07-01",
      "has_enough_history_for_5y_trend": true,
      "point_in_time_safe": true
    },
    "CPIAUCSL": {
      "available": true,
      "observation_count": 240,
      "latest_observation_date": "2024-08-01",
      "earliest_observation_date": "2004-09-01",
      "has_enough_history_for_5y_trend": true,
      "point_in_time_safe": true
    }
  }
}
```

The exact shape may change in the detailed Stage 1 spec, but it must be human-readable and machine-readable enough to support later development.

### Stage 1 Non-Goals

Stage 1 must not:

- Replace the mock classifier.
- Change agent prompts.
- Change trading behavior.
- Implement the real quadrant classifier.
- Use LLMs to infer macro regimes.
- Treat downloaded current data as proof that historical point-in-time backtests are safe.

## Stage 2: Real Macro Regime Classifier

### Objective

Build a real `macro_regime_classifier` that uses the verified FRED data from Stage 1 to classify the current Growth / Inflation quadrant.

Stage 2 answers this question:

> Given verified FRED macro data, can the system deterministically classify the macro regime and return the same kind of structured output currently provided by the mock classifier?

### Proposed Data Logic

The first real version should use:

```text
Growth: GDPC1
Inflation: CPIAUCSL
```

The likely first classifier formula is:

```text
growth_metric = year-over-year real GDP growth
growth_trend = rolling five-year average of the growth metric

inflation_metric = year-over-year CPI inflation
inflation_trend = rolling five-year average of the inflation metric
```

Then:

```text
growth_up = growth_metric > growth_trend
growth_down = growth_metric <= growth_trend

inflation_up = inflation_metric > inflation_trend
inflation_down = inflation_metric <= inflation_trend
```

The detailed Stage 2 spec must decide the exact formula after Stage 1 proves the data shape and frequency.

### Literature-Aligned Lag Handling

Research Affiliates used lags to account for delayed and revised macro data:

| Axis | Default Literature-Aligned Lag |
|---|---|
| Growth | 6 months |
| Inflation | 1 month |

Stage 2 should treat these as explicit parameters, not hidden assumptions.

The detailed Stage 2 spec should decide whether the default mode is:

```text
fred_ra_simple_lagged
```

or whether a no-extra-lag mode is also exposed for later experimentation.

### Regime To Basket Weight Mapping

Stage 2 should preserve the current Research Affiliates-style basket mapping:

| Regime | Equity | Commodity | TIPS | Nominal Bond |
|---|---:|---:|---:|---:|
| `growth_up_inflation_down` | 0.50 | 0.25 | 0.00 | 0.25 |
| `growth_up_inflation_up` | 0.25 | 0.50 | 0.25 | 0.00 |
| `growth_down_inflation_up` | 0.00 | 0.25 | 0.50 | 0.25 |
| `growth_down_inflation_down` | 0.25 | 0.25 | 0.00 | 0.50 |

### Output Compatibility

The real classifier should keep the same outer contract as the mock classifier so the agent workflow remains stable.

Expected top-level fields:

- `tool`
- `mock`
- `mode`
- `date`
- `as_of`
- `regime`
- `growth_direction`
- `inflation_direction`
- `previous_regime`
- `regime_changed`
- `basket_weights`
- `reason_brief`

The real classifier should add evidence fields:

- `growth_evidence`
- `inflation_evidence`
- `data_quality`
- `confidence`

### Strategy Integration

Stage 2 should not destroy the existing mock strategy.

Preferred structure:

```text
AITradingTeamMockGrowthInflationQuadrantStrategy
  remains available for workflow and UI testing

AITradingTeamGrowthInflationQuadrantStrategy
  uses the real FRED-backed macro_regime_classifier
```

The two strategies may share the same downstream basket, portfolio decision, and execution flow.

### Stage 2 Non-Goals

Stage 2 must not:

- Add many alternative macro indicators.
- Add multi-country GDP logic.
- Add LLM-based macro forecasting.
- Optimize the basket weights beyond 50/25/25/0.
- Add linearized regime strength scores.
- Change basket contents unless required by integration.

Those topics are future research and development tracks.

## Dependency Between Stages

Stage 2 must not begin until Stage 1 has produced a satisfactory data availability report.

Minimum Stage 1 pass criteria:

- `GDPC1` and `CPIAUCSL` are retrievable.
- They are retrievable for at least one relevant historical backtest `as_of` date.
- They provide at least five years of usable prior history for that date.
- The returned FRED tool data is point-in-time safe or the limitation is explicitly documented.

If Stage 1 fails, the project should keep using the mock classifier and discuss alternative data sources or fallback macro definitions before writing classifier logic.

## Future Extensions

After Stage 2 is stable, future work may consider:

- `PCEPI` as a secondary inflation confirmation series.
- Regime confidence bands instead of hard greater-than comparisons.
- Linearized basket weights instead of 50/25/25/0.
- More frequent growth proxies such as PMI, payrolls, unemployment, credit spreads, or market-implied growth signals.
- Multi-region macro classifiers for non-US-heavy asset universes.
- UI panels that explain macro evidence in plain language.

These extensions should not be included in the first real classifier implementation.

## Next Step

The next spec should be:

```text
FRED Growth / Inflation Data Availability Spec
```

That detailed Stage 1 spec should define:

- The validation script or command.
- The exact dates to test.
- The exact pass/fail criteria.
- The report file location and schema.
- The verification commands.

Only after Stage 1 passes should we write the Stage 2 real classifier spec.
