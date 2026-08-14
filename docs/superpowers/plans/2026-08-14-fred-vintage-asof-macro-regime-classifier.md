# FRED Vintage As-Of Macro Regime Classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the real Growth / Inflation quadrant classifier use FRED vintage data that was knowable on the simulated trading date, instead of relying on artificial GDP/CPI lag months as the default behavior.

**Architecture:** Add a new default classifier mode, `fred_ra_vintage_asof`, that resolves an effective FRED vintage date from the strategy's simulated date, asks FRED for point-in-time observations as of that date, and records the exact provenance in the tool result. Keep the existing lagged classifier as `fred_ra_simple_lagged` for legacy comparison.

**Tech Stack:** Python, Lumibot strategy agents, FREDMacroData, pytest, ruff.

---

## File Structure

**Modify:**
- `lumibot/example_strategies/fred_growth_inflation_regime_classifier.py`
  - Add vintage mode constants, as-of policy helpers, vintage evidence generation, and updated tool arguments/description.
  - Preserve legacy lagged mode behavior under `fred_ra_simple_lagged`.
- `lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py`
  - Make the real strategy default to `fred_ra_vintage_asof`.
  - Pass `as_of_policy` to the macro agent context/tool.
  - Update macro agent prompts and canonical validation for vintage evidence.
- `scripts/scan_growth_inflation_regimes.py`
  - Accept/pass the new `mode`, `as_of_policy`, and optional `requested_as_of` fields while preserving legacy scan support.

**Test:**
- `tests/test_ai_trading_team_growth_inflation_quadrant.py`
  - Add classifier, bound tool, prompt, default parameter, and validator coverage.
- `tests/test_growth_inflation_regime_scan.py`
  - Update scan fixtures for the new default output and preserve legacy mode tests.
- `tests/test_fred_macro.py`
  - Keep existing FRED client vintage parameter coverage; add only if a regression appears around `as_of`/`end`.

**Do not modify unless test failures prove it is required:**
- `lumibot/macro/fred.py`
  - Existing `FREDMacroData.get_series(..., as_of=...)` already sends `realtime_start`, `realtime_end`, and `observation_end`.
- Agent replay UI files
  - Existing trace/UI should display the new fields because they are ordinary tool output.

---

### Task 1: Add Failing Tests For Vintage Classifier Defaults

**Files:**
- Modify: `tests/test_ai_trading_team_growth_inflation_quadrant.py`

- [ ] **Step 1: Add helper expectations for vintage evidence**

Add this helper near the existing `_real_macro_evidence` helper so tests can assert the new provenance fields without repeating every numeric detail:

```python
def _assert_vintage_evidence_fields(evidence, *, axis, series_id, as_of):
    assert evidence["axis"] == axis
    assert evidence["series_id"] == series_id
    assert evidence["as_of"] == as_of
    assert "lag_months" not in evidence
    assert "data_cutoff" not in evidence
    assert evidence["latest_realtime_start"] == as_of
    assert evidence["latest_realtime_end"] == as_of
    assert evidence["comparison_realtime_start"] == as_of
    assert evidence["comparison_realtime_end"] == as_of
    assert isinstance(evidence["observation_lag_days"], int)
    assert evidence["observation_lag_days"] >= 0
```

- [ ] **Step 2: Add default vintage classifier test**

Add this test near existing `test_classify_growth_inflation_regime...` tests:

```python
def test_classify_growth_inflation_regime_defaults_to_same_day_fred_vintage():
    module = load_classifier_module()
    fred = FakeFredClient(
        {
            "GDPC1": _payload(
                "GDPC1",
                _quarterly_observations(
                    values=[100 + index for index in range(25)],
                    realtime="2024-09-05",
                ),
            ),
            "CPIAUCSL": _payload(
                "CPIAUCSL",
                _monthly_observations(
                    values=[200 + index for index in range(80)],
                    realtime="2024-09-05",
                ),
            ),
        }
    )

    result = module.classify_growth_inflation_regime(fred, date="2024-09-05")

    assert result["status"] == "passed"
    assert result["mode"] == "fred_ra_vintage_asof"
    assert result["date"] == "2024-09-05"
    assert result["as_of"] == "2024-09-05"
    assert result["requested_as_of"] == "2024-09-05"
    assert result["effective_as_of"] == "2024-09-05"
    assert result["lookahead_clamped"] is False
    assert result["as_of_policy"] == "same_day_vintage"
    assert result["data_quality"]["as_of_policy"] == "same_day_vintage"
    assert result["data_quality"]["requested_as_of"] == "2024-09-05"
    assert result["data_quality"]["effective_as_of"] == "2024-09-05"
    assert result["data_quality"]["lookahead_clamped"] is False
    assert result["data_quality"]["point_in_time_safe"] is True
    assert result["data_quality"]["uses_revised_data"] is False
    _assert_vintage_evidence_fields(
        result["growth_evidence"],
        axis="growth",
        series_id="GDPC1",
        as_of="2024-09-05",
    )
    _assert_vintage_evidence_fields(
        result["inflation_evidence"],
        axis="inflation",
        series_id="CPIAUCSL",
        as_of="2024-09-05",
    )
    assert [call["series_id"] for call in fred.calls] == ["GDPC1", "CPIAUCSL"]
    assert all(call["as_of"] == "2024-09-05" for call in fred.calls)
    assert all(call["end"] == "2024-09-05" for call in fred.calls)
```

- [ ] **Step 3: Run the new test and verify it fails**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_growth_inflation_quadrant.py::test_classify_growth_inflation_regime_defaults_to_same_day_fred_vintage -q
```

Expected: FAIL because the current default mode is `fred_ra_simple_lagged` and the output lacks `requested_as_of`, `effective_as_of`, `as_of_policy`, and vintage realtime evidence fields.

---

### Task 2: Implement Vintage Date Policy And Evidence

**Files:**
- Modify: `lumibot/example_strategies/fred_growth_inflation_regime_classifier.py`

- [ ] **Step 1: Add mode and policy constants**

Replace the current default-mode constants with explicit mode names:

```python
VINTAGE_ASOF_MODE = "fred_ra_vintage_asof"
LEGACY_LAGGED_MODE = "fred_ra_simple_lagged"
DEFAULT_MODE = VINTAGE_ASOF_MODE
DEFAULT_AS_OF_POLICY = "same_day_vintage"
SUPPORTED_MODES = {VINTAGE_ASOF_MODE, LEGACY_LAGGED_MODE}
SUPPORTED_AS_OF_POLICIES = {"same_day_vintage", "previous_day_vintage", "explicit"}
```

Keep these existing constants unchanged:

```python
DEFAULT_GROWTH_SERIES_ID = "GDPC1"
DEFAULT_INFLATION_SERIES_ID = "CPIAUCSL"
DEFAULT_GROWTH_LAG_MONTHS = 6
DEFAULT_INFLATION_LAG_MONTHS = 1
DEFAULT_TREND_YEARS = 5
```

- [ ] **Step 2: Add previous-day and as-of resolution helpers**

Add these helpers after `subtract_months`:

```python
def previous_calendar_day(value: date) -> date:
    parsed = parse_date(value)
    return date.fromordinal(parsed.toordinal() - 1)


def resolve_vintage_as_of(
    *,
    trading_date: Any,
    requested_as_of: Any | None = None,
    as_of_policy: str = DEFAULT_AS_OF_POLICY,
    max_as_of: Any | None = None,
) -> dict[str, Any]:
    parsed_trading_date = parse_date(trading_date)
    parsed_max_as_of = parse_date(max_as_of) if max_as_of is not None else parsed_trading_date
    policy = str(as_of_policy or DEFAULT_AS_OF_POLICY).strip()
    if policy not in SUPPORTED_AS_OF_POLICIES:
        raise ValueError(
            f"unsupported as_of_policy {policy!r}; supported policies are "
            f"{sorted(SUPPORTED_AS_OF_POLICIES)!r}."
        )

    if requested_as_of is not None:
        requested = parse_date(requested_as_of)
    elif policy == "previous_day_vintage":
        requested = previous_calendar_day(parsed_trading_date)
    else:
        requested = parsed_trading_date

    effective = min(requested, parsed_max_as_of)
    return {
        "requested_as_of": requested,
        "effective_as_of": effective,
        "lookahead_clamped": requested != effective,
        "as_of_policy": policy,
    }
```

- [ ] **Step 3: Refactor evidence calculation to support explicit cutoffs**

Create an internal helper and route the existing public function through it:

```python
def _calculate_axis_evidence_at_cutoff(
    payload: dict[str, Any],
    *,
    axis: str,
    series_name: str,
    frequency: str,
    trading_date: date,
    data_cutoff: date,
    periods_back: int,
    trend_window_observations: int,
    trend_years: int,
    lag_months: int | None = None,
    evidence_as_of: date | None = None,
) -> dict[str, Any]:
    parsed_trading_date = parse_date(trading_date)
    parsed_data_cutoff = parse_date(data_cutoff)
    periods_back = int(periods_back)
    trend_window_observations = int(trend_window_observations)
    trend_years = int(trend_years)
    if periods_back <= 0:
        raise ValueError("periods_back must be > 0.")
    if trend_window_observations <= 0:
        raise ValueError("trend_window_observations must be > 0.")
    if trend_years <= 0:
        raise ValueError("trend_years must be > 0.")

    observations = _observations_from_payload(payload, parsed_trading_date, parsed_data_cutoff)
    minimum_observations = periods_back + trend_window_observations
    if len(observations) < minimum_observations:
        raise ValueError(
            f"{axis} needs at least {minimum_observations} usable observations, "
            f"got {len(observations)}."
        )

    metrics = []
    for index in range(periods_back, len(observations)):
        comparison = observations[index - periods_back]
        current = observations[index]
        if comparison["value"] == 0:
            raise ValueError(f"{axis} comparison value is zero on {comparison['date'].isoformat()}.")
        metrics.append(
            {
                "observation": current,
                "comparison": comparison,
                "value": current["value"] / comparison["value"] - 1.0,
            }
        )

    trend_metrics = metrics[-trend_window_observations:]
    latest_metric = trend_metrics[-1]
    metric_value = latest_metric["value"]
    trend_value = sum(metric["value"] for metric in trend_metrics) / trend_window_observations
    margin = metric_value - trend_value
    direction = "up" if metric_value > trend_value else "down"
    latest_observation_date = latest_metric["observation"]["date"]

    evidence = {
        "axis": axis,
        "series_id": payload.get("series_id"),
        "series_name": series_name,
        "frequency": frequency,
        "latest_observation_date": latest_observation_date.isoformat(),
        "comparison_observation_date": latest_metric["comparison"]["date"].isoformat(),
        "latest_value": latest_metric["observation"]["value"],
        "comparison_value": latest_metric["comparison"]["value"],
        "metric_name": "year_over_year_change",
        "metric_value": metric_value,
        "trend_years": trend_years,
        "trend_window_observations": trend_window_observations,
        "trend_value": trend_value,
        "margin": margin,
        "direction": direction,
    }
    if lag_months is None:
        parsed_evidence_as_of = parse_date(evidence_as_of or parsed_data_cutoff)
        evidence.update(
            {
                "as_of": parsed_evidence_as_of.isoformat(),
                "latest_realtime_start": latest_metric["observation"].get("realtime_start"),
                "latest_realtime_end": latest_metric["observation"].get("realtime_end"),
                "comparison_realtime_start": latest_metric["comparison"].get("realtime_start"),
                "comparison_realtime_end": latest_metric["comparison"].get("realtime_end"),
                "observation_lag_days": (parsed_evidence_as_of - latest_observation_date).days,
            }
        )
    else:
        evidence.update(
            {
                "lag_months": int(lag_months),
                "data_cutoff": parsed_data_cutoff.isoformat(),
            }
        )
    return evidence
```

Then change the existing `calculate_axis_evidence(...)` body to validate `lag_months >= 0`, compute `data_cutoff = subtract_months(...)`, and call `_calculate_axis_evidence_at_cutoff(..., lag_months=lag_months)`.

- [ ] **Step 4: Add vintage evidence helper**

Add this helper after `calculate_axis_evidence`:

```python
def calculate_axis_evidence_vintage(
    payload: dict[str, Any],
    *,
    axis: str,
    series_name: str,
    frequency: str,
    trading_date: date,
    as_of: date,
    periods_back: int,
    trend_window_observations: int,
    trend_years: int,
) -> dict[str, Any]:
    return _calculate_axis_evidence_at_cutoff(
        payload,
        axis=axis,
        series_name=series_name,
        frequency=frequency,
        trading_date=trading_date,
        data_cutoff=parse_date(as_of),
        periods_back=periods_back,
        trend_window_observations=trend_window_observations,
        trend_years=trend_years,
        lag_months=None,
        evidence_as_of=parse_date(as_of),
    )
```

- [ ] **Step 5: Split classifier logic by mode**

Update `classify_growth_inflation_regime` signature:

```python
def classify_growth_inflation_regime(
    fred: Any,
    *,
    date: Any,
    mode: str = DEFAULT_MODE,
    growth_series_id: str = DEFAULT_GROWTH_SERIES_ID,
    inflation_series_id: str = DEFAULT_INFLATION_SERIES_ID,
    growth_lag_months: int = DEFAULT_GROWTH_LAG_MONTHS,
    inflation_lag_months: int = DEFAULT_INFLATION_LAG_MONTHS,
    trend_years: int = DEFAULT_TREND_YEARS,
    previous_regime: str | None = None,
    as_of_policy: str = DEFAULT_AS_OF_POLICY,
    requested_as_of: Any | None = None,
    max_as_of: Any | None = None,
) -> dict[str, Any]:
```

Inside the function:

```python
mode_text = str(mode or DEFAULT_MODE).strip()
if mode_text not in SUPPORTED_MODES:
    message = f"Unsupported mode {mode_text!r}; supported modes are {sorted(SUPPORTED_MODES)!r}."
    return _failed_result(date_text, mode_text, "unsupported_mode", [message], **status_series_kwargs)
```

For `mode_text == VINTAGE_ASOF_MODE`, resolve the vintage date and call FRED with the effective vintage:

```python
vintage = resolve_vintage_as_of(
    trading_date=trading_date,
    requested_as_of=requested_as_of,
    as_of_policy=as_of_policy,
    max_as_of=max_as_of or trading_date,
)
effective_as_of = vintage["effective_as_of"]
effective_as_of_text = effective_as_of.isoformat()
growth_payload = fred.get_series(
    growth_series_id,
    start=history_start,
    end=effective_as_of_text,
    as_of=effective_as_of_text,
)
inflation_payload = fred.get_series(
    inflation_series_id,
    start=history_start,
    end=effective_as_of_text,
    as_of=effective_as_of_text,
)
growth_evidence = calculate_axis_evidence_vintage(
    growth_payload,
    axis="growth",
    series_name="Real Gross Domestic Product",
    frequency="quarterly",
    trading_date=trading_date,
    as_of=effective_as_of,
    periods_back=GROWTH_PERIODS_BACK,
    trend_window_observations=trend_years * GROWTH_OBSERVATIONS_PER_YEAR,
    trend_years=trend_years,
)
inflation_evidence = calculate_axis_evidence_vintage(
    inflation_payload,
    axis="inflation",
    series_name="Consumer Price Index for All Urban Consumers",
    frequency="monthly",
    trading_date=trading_date,
    as_of=effective_as_of,
    periods_back=INFLATION_PERIODS_BACK,
    trend_window_observations=trend_years * INFLATION_OBSERVATIONS_PER_YEAR,
    trend_years=trend_years,
)
```

For `mode_text == LEGACY_LAGGED_MODE`, keep the existing lagged fetch/evidence path, but report `mode_text` instead of `mode`.

- [ ] **Step 6: Add vintage fields to successful result**

When returning a passed vintage result, include:

```python
"mode": mode_text,
"date": date_text,
"as_of": effective_as_of_text,
"requested_as_of": vintage["requested_as_of"].isoformat(),
"effective_as_of": effective_as_of_text,
"lookahead_clamped": bool(vintage["lookahead_clamped"]),
"as_of_policy": vintage["as_of_policy"],
"data_quality": {
    "status": "passed",
    "source": "fred_api",
    "point_in_time_safe": True,
    "uses_revised_data": False,
    "required_series": [growth_series_id, inflation_series_id],
    "as_of_policy": vintage["as_of_policy"],
    "requested_as_of": vintage["requested_as_of"].isoformat(),
    "effective_as_of": effective_as_of_text,
    "lookahead_clamped": bool(vintage["lookahead_clamped"]),
    "warnings": [],
    "errors": [],
},
```

When returning a passed legacy result, preserve legacy `as_of=date_text`, evidence `lag_months`, and evidence `data_cutoff`.

- [ ] **Step 7: Run focused classifier tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_growth_inflation_quadrant.py::test_classify_growth_inflation_regime_defaults_to_same_day_fred_vintage -q
```

Expected: PASS.

---

### Task 3: Add Clamp, Previous-Day, And Legacy Coverage

**Files:**
- Modify: `tests/test_ai_trading_team_growth_inflation_quadrant.py`
- Modify: `lumibot/example_strategies/fred_growth_inflation_regime_classifier.py`

- [ ] **Step 1: Add explicit future as-of clamp test**

Add:

```python
def test_classify_growth_inflation_regime_clamps_future_requested_as_of_to_max_as_of():
    module = load_classifier_module()
    fred = FakeFredClient(
        {
            "GDPC1": _payload("GDPC1", _quarterly_observations(values=[100 + i for i in range(25)])),
            "CPIAUCSL": _payload("CPIAUCSL", _monthly_observations(values=[200 + i for i in range(80)])),
        }
    )

    result = module.classify_growth_inflation_regime(
        fred,
        date="2024-09-05",
        requested_as_of="2024-09-10",
        max_as_of="2024-09-05",
    )

    assert result["status"] == "passed"
    assert result["requested_as_of"] == "2024-09-10"
    assert result["effective_as_of"] == "2024-09-05"
    assert result["lookahead_clamped"] is True
    assert all(call["as_of"] == "2024-09-05" for call in fred.calls)
    assert all(call["end"] == "2024-09-05" for call in fred.calls)
```

- [ ] **Step 2: Add previous-day policy test**

Add:

```python
def test_classify_growth_inflation_regime_previous_day_policy_uses_prior_calendar_day():
    module = load_classifier_module()
    fred = FakeFredClient(
        {
            "GDPC1": _payload("GDPC1", _quarterly_observations(values=[100 + i for i in range(25)])),
            "CPIAUCSL": _payload("CPIAUCSL", _monthly_observations(values=[200 + i for i in range(80)])),
        }
    )

    result = module.classify_growth_inflation_regime(
        fred,
        date="2024-09-05",
        as_of_policy="previous_day_vintage",
    )

    assert result["status"] == "passed"
    assert result["requested_as_of"] == "2024-09-04"
    assert result["effective_as_of"] == "2024-09-04"
    assert result["as_of_policy"] == "previous_day_vintage"
    assert all(call["as_of"] == "2024-09-04" for call in fred.calls)
```

- [ ] **Step 3: Update legacy-mode test**

Find the existing test that asserts:

```python
assert result["mode"] == "fred_ra_simple_lagged"
```

Update the call to explicitly request legacy mode:

```python
result = module.classify_growth_inflation_regime(
    fred,
    date="2024-09-05",
    mode="fred_ra_simple_lagged",
)
```

Then keep these legacy assertions:

```python
assert result["mode"] == "fred_ra_simple_lagged"
assert result["as_of"] == "2024-09-05"
assert result["growth_evidence"]["lag_months"] == module.DEFAULT_GROWTH_LAG_MONTHS
assert result["inflation_evidence"]["lag_months"] == module.DEFAULT_INFLATION_LAG_MONTHS
assert "data_cutoff" in result["growth_evidence"]
assert "requested_as_of" not in result
```

- [ ] **Step 4: Run the three classifier tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_growth_inflation_quadrant.py::test_classify_growth_inflation_regime_defaults_to_same_day_fred_vintage tests/test_ai_trading_team_growth_inflation_quadrant.py::test_classify_growth_inflation_regime_clamps_future_requested_as_of_to_max_as_of tests/test_ai_trading_team_growth_inflation_quadrant.py::test_classify_growth_inflation_regime_previous_day_policy_uses_prior_calendar_day -q
```

Expected: PASS.

---

### Task 4: Update Bound Tool Defaults, Arguments, And Tool Description

**Files:**
- Modify: `lumibot/example_strategies/fred_growth_inflation_regime_classifier.py`
- Modify: `tests/test_ai_trading_team_growth_inflation_quadrant.py`

- [ ] **Step 1: Add bound tool test for default strategy date as requested/effective as-of**

Add:

```python
def test_real_macro_tool_defaults_to_strategy_datetime_for_vintage_as_of():
    module = load_classifier_module()
    fred = FakeFredClient(
        {
            "GDPC1": _payload("GDPC1", _quarterly_observations(values=[100 + i for i in range(25)])),
            "CPIAUCSL": _payload("CPIAUCSL", _monthly_observations(values=[200 + i for i in range(80)])),
        }
    )
    tool_definition = module.make_real_macro_regime_classifier_tool(
        fred_factory=lambda _strategy: fred
    )

    class Strategy:
        parameters = {}

        def get_datetime(self):
            return datetime(2024, 9, 5, 9, 30)

    bound_tool = tool_definition.binder(Strategy(), None)
    result = bound_tool.function()

    assert result["mode"] == "fred_ra_vintage_asof"
    assert result["requested_as_of"] == "2024-09-05"
    assert result["effective_as_of"] == "2024-09-05"
    assert result["as_of_policy"] == "same_day_vintage"
```

- [ ] **Step 2: Add bound tool test for explicit future as-of clamp**

Add:

```python
def test_real_macro_tool_clamps_requested_as_of_after_strategy_datetime():
    module = load_classifier_module()
    fred = FakeFredClient(
        {
            "GDPC1": _payload("GDPC1", _quarterly_observations(values=[100 + i for i in range(25)])),
            "CPIAUCSL": _payload("CPIAUCSL", _monthly_observations(values=[200 + i for i in range(80)])),
        }
    )
    tool_definition = module.make_real_macro_regime_classifier_tool(
        fred_factory=lambda _strategy: fred
    )

    class Strategy:
        parameters = {}

        def get_datetime(self):
            return datetime(2024, 9, 5, 9, 30)

    bound_tool = tool_definition.binder(Strategy(), None)
    result = bound_tool.function(requested_as_of="2024-09-07")

    assert result["requested_as_of"] == "2024-09-07"
    assert result["effective_as_of"] == "2024-09-05"
    assert result["lookahead_clamped"] is True
```

- [ ] **Step 3: Update bound tool signature**

Update inner `macro_regime_classifier(...)` signature:

```python
def macro_regime_classifier(
    *,
    date: Any | None = None,
    mode: str | None = None,
    growth_series_id: str | None = None,
    inflation_series_id: str | None = None,
    growth_lag_months: int | None = None,
    inflation_lag_months: int | None = None,
    trend_years: int | None = None,
    as_of_policy: str | None = None,
    requested_as_of: Any | None = None,
) -> dict[str, Any]:
```

Resolve defaults:

```python
default_as_of_policy = configured_default(DEFAULT_AS_OF_POLICY, "as_of_policy")
resolved_as_of_policy = as_of_policy if as_of_policy is not None else default_as_of_policy
```

Pass to classifier:

```python
result = classify_growth_inflation_regime(
    fred,
    date=resolved_date,
    mode=resolved_mode,
    growth_series_id=resolved_growth_series_id,
    inflation_series_id=resolved_inflation_series_id,
    growth_lag_months=resolved_growth_lag_months,
    inflation_lag_months=resolved_inflation_lag_months,
    trend_years=resolved_trend_years,
    previous_regime=getattr(strategy, "_last_real_regime", None),
    as_of_policy=resolved_as_of_policy,
    requested_as_of=requested_as_of,
    max_as_of=strategy_date,
)
```

- [ ] **Step 4: Update tool description**

Replace the tool description text with:

```python
description = (
    "Classify the Growth / Inflation quadrant using FRED point-in-time GDPC1 and CPIAUCSL data. "
    "Default mode fred_ra_vintage_asof uses the strategy's simulated trading date as the FRED "
    "vintage as-of date, so backtests only see macro observations known by that date. "
    "If requested_as_of is after the strategy date, the tool clamps it and reports "
    "requested_as_of, effective_as_of, lookahead_clamped, and as_of_policy. "
    "Legacy mode fred_ra_simple_lagged remains available for comparison. "
    "Do not manually recalculate its output."
)
```

- [ ] **Step 5: Run bound tool tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_growth_inflation_quadrant.py::test_real_macro_tool_defaults_to_strategy_datetime_for_vintage_as_of tests/test_ai_trading_team_growth_inflation_quadrant.py::test_real_macro_tool_clamps_requested_as_of_after_strategy_datetime -q
```

Expected: PASS.

---

### Task 5: Update Real Quadrant Strategy Defaults, Prompts, Context, And Validator

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py`
- Modify: `tests/test_ai_trading_team_growth_inflation_quadrant.py`

- [ ] **Step 1: Add tests for strategy defaults and macro prompt**

Add:

```python
def test_real_quadrant_strategy_defaults_to_vintage_asof_policy():
    module, _strategy_class = load_real_strategy_module()

    assert module.AITradingTeamGrowthInflationQuadrantStrategy.parameters["macro_regime_mode"] == "fred_ra_vintage_asof"
    assert module.AITradingTeamGrowthInflationQuadrantStrategy.parameters["as_of_policy"] == "same_day_vintage"
```

Add or update the prompt serialization test so it asserts:

```python
assert "FRED vintage as-of date" in serialized_prompts
assert "requested_as_of" in serialized_prompts
assert "effective_as_of" in serialized_prompts
assert "lookahead_clamped" in serialized_prompts
assert "Do not classify the macro regime yourself" in serialized_prompts
```

- [ ] **Step 2: Add canonical validator test for vintage payload**

Create a helper:

```python
def _real_macro_vintage_evidence(module, *, axis, series_id, direction, frequency):
    return {
        "axis": axis,
        "series_id": series_id,
        "series_name": (
            "Real Gross Domestic Product"
            if axis == "growth"
            else "Consumer Price Index for All Urban Consumers"
        ),
        "frequency": frequency,
        "as_of": "2024-09-05",
        "latest_realtime_start": "2024-09-05",
        "latest_realtime_end": "2024-09-05",
        "comparison_realtime_start": "2024-09-05",
        "comparison_realtime_end": "2024-09-05",
        "latest_observation_date": "2024-01-01" if axis == "growth" else "2024-08-01",
        "comparison_observation_date": "2023-01-01" if axis == "growth" else "2023-08-01",
        "latest_value": 100.0,
        "comparison_value": 98.0,
        "metric_name": "year_over_year_change",
        "metric_value": 0.02,
        "trend_years": module.DEFAULT_TREND_YEARS,
        "trend_window_observations": 20 if axis == "growth" else 60,
        "trend_value": 0.01,
        "margin": 0.01,
        "direction": direction,
        "observation_lag_days": 248 if axis == "growth" else 35,
    }
```

Then add:

```python
def test_real_macro_report_canonical_accepts_vintage_asof_payload():
    module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()
    payload = {
        "tool": "macro_regime_classifier",
        "status": "passed",
        "mock": False,
        "mode": "fred_ra_vintage_asof",
        "date": "2024-09-05",
        "as_of": "2024-09-05",
        "requested_as_of": "2024-09-05",
        "effective_as_of": "2024-09-05",
        "lookahead_clamped": False,
        "as_of_policy": "same_day_vintage",
        "regime": "growth_up_inflation_down",
        "growth_direction": "up",
        "inflation_direction": "down",
        "previous_regime": None,
        "regime_changed": False,
        "basket_weights": dict(module.WEIGHT_BY_REGIME["growth_up_inflation_down"]),
        "growth_evidence": _real_macro_vintage_evidence(
            module,
            axis="growth",
            series_id=module.DEFAULT_GROWTH_SERIES_ID,
            direction="up",
            frequency="quarterly",
        ),
        "inflation_evidence": _real_macro_vintage_evidence(
            module,
            axis="inflation",
            series_id=module.DEFAULT_INFLATION_SERIES_ID,
            direction="down",
            frequency="monthly",
        ),
        "data_quality": {
            "status": "passed",
            "source": "fred_api",
            "point_in_time_safe": True,
            "uses_revised_data": False,
            "required_series": [module.DEFAULT_GROWTH_SERIES_ID, module.DEFAULT_INFLATION_SERIES_ID],
            "as_of_policy": "same_day_vintage",
            "requested_as_of": "2024-09-05",
            "effective_as_of": "2024-09-05",
            "lookahead_clamped": False,
            "warnings": [],
            "errors": [],
        },
        "confidence": "medium",
        "reason_brief": "Growth up and inflation down.",
    }

    assert strategy._real_macro_report_canonical_error(payload, "2024-09-05") is None
```

- [ ] **Step 3: Update strategy parameters and initialize fields**

Update `parameters`:

```python
"macro_regime_mode": DEFAULT_MODE,
"as_of_policy": DEFAULT_AS_OF_POLICY,
```

Keep `growth_lag_months` and `inflation_lag_months` in parameters only for legacy mode compatibility. In `initialize`, add:

```python
self._as_of_policy = self.parameters.get("as_of_policy", DEFAULT_AS_OF_POLICY)
```

- [ ] **Step 4: Update macro allocation agent system prompt**

Replace the existing macro system prompt with:

```python
system_prompt=(
    "Macro allocation role: call the real FRED-backed macro_regime_classifier. "
    "Do not classify the macro regime yourself. In the default vintage mode, the classifier "
    "uses the simulated trading date as the FRED vintage as-of date so backtests only use "
    "macro data known by that date. Preserve the classifier's structured status, regime, "
    "basket_weights, requested_as_of, effective_as_of, lookahead_clamped, as_of_policy, "
    "growth_evidence, inflation_evidence, data_quality, confidence, and reason fields. "
    "If the classifier returns status=blocked or status=failed, return that plainly. "
    "Do not place orders."
),
```

- [ ] **Step 5: Update macro task prompt and context**

Change the task prompt to:

```python
task_prompt=(
    "Run the real FRED vintage macro allocation step by calling macro_regime_classifier. "
    "Return one JSON object preserving status, regime, basket_weights, requested_as_of, "
    "effective_as_of, lookahead_clamped, as_of_policy, growth_evidence, inflation_evidence, "
    "data_quality, confidence, and reason fields."
),
```

Update context:

```python
context={
    "date": current_date,
    "macro_regime_mode": self._macro_regime_mode,
    "as_of_policy": self._as_of_policy,
    "growth_series_id": self._growth_series_id,
    "inflation_series_id": self._inflation_series_id,
    "trend_years": self._trend_years,
    "basket_universes": basket_universes,
},
```

If `self._macro_regime_mode == LEGACY_LAGGED_MODE`, add `growth_lag_months` and `inflation_lag_months` to context before running the agent.

- [ ] **Step 6: Update canonical report validator**

Replace the current date/as-of check:

```python
if macro_report.get("date") != current_date or macro_report.get("as_of") != current_date:
    return "current date mismatch: date and as_of must equal the current date."
```

With:

```python
if macro_report.get("date") != current_date:
    return "current date mismatch: date must equal the current date."
if self._macro_regime_mode == VINTAGE_ASOF_MODE:
    if macro_report.get("as_of_policy") != self._as_of_policy:
        return "configured/default parameters mismatch: as_of_policy must match the strategy configuration."
    effective_as_of = macro_report.get("effective_as_of")
    if not isinstance(effective_as_of, str) or not effective_as_of.strip():
        return "effective_as_of must be a non-empty string."
    try:
        if parse_date(effective_as_of) > parse_date(current_date):
            return "effective_as_of must not be after the current date."
    except ValueError as exc:
        return f"effective_as_of must be a valid date: {exc}"
    if macro_report.get("as_of") != effective_as_of:
        return "as_of must equal effective_as_of in vintage mode."
    if macro_report.get("requested_as_of") is None:
        return "requested_as_of must be present in vintage mode."
    if not isinstance(macro_report.get("lookahead_clamped"), bool):
        return "lookahead_clamped must be a boolean in vintage mode."
else:
    if macro_report.get("as_of") != current_date:
        return "current date mismatch: legacy as_of must equal the current date."
```

Update `data_quality` checks in vintage mode:

```python
if self._macro_regime_mode == VINTAGE_ASOF_MODE:
    if data_quality.get("as_of_policy") != self._as_of_policy:
        return "data_quality as_of_policy must match the strategy configuration."
    if data_quality.get("effective_as_of") != macro_report.get("effective_as_of"):
        return "data_quality effective_as_of must match macro_report effective_as_of."
    if data_quality.get("requested_as_of") != macro_report.get("requested_as_of"):
        return "data_quality requested_as_of must match macro_report requested_as_of."
    if data_quality.get("lookahead_clamped") != macro_report.get("lookahead_clamped"):
        return "data_quality lookahead_clamped must match macro_report lookahead_clamped."
```

- [ ] **Step 7: Update evidence validator to support mode-specific fields**

Change `_real_macro_evidence_canonical_error` signature:

```python
def _real_macro_evidence_canonical_error(
    self,
    evidence: dict[str, Any],
    *,
    axis: str,
    series_id: str,
    lag_months: int | None = None,
) -> str | None:
```

Inside it:

```python
if self._macro_regime_mode == VINTAGE_ASOF_MODE:
    if "lag_months" in evidence:
        return "must not include lag_months in vintage mode."
    if "data_cutoff" in evidence:
        return "must not include data_cutoff in vintage mode."
    required_string_fields = (
        "series_name",
        "frequency",
        "as_of",
        "latest_realtime_start",
        "latest_realtime_end",
        "comparison_realtime_start",
        "comparison_realtime_end",
        "latest_observation_date",
        "comparison_observation_date",
        "metric_name",
        "direction",
    )
else:
    if evidence.get("lag_months") != lag_months:
        return f"lag_months must be {lag_months}."
    required_string_fields = (
        "series_name",
        "frequency",
        "data_cutoff",
        "latest_observation_date",
        "comparison_observation_date",
        "metric_name",
        "direction",
    )
```

Include `observation_lag_days` in numeric fields only for vintage mode:

```python
required_numeric_fields = [
    "latest_value",
    "comparison_value",
    "metric_value",
    "trend_window_observations",
    "trend_value",
    "margin",
]
if self._macro_regime_mode == VINTAGE_ASOF_MODE:
    required_numeric_fields.append("observation_lag_days")
```

- [ ] **Step 8: Run strategy tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_growth_inflation_quadrant.py -q
```

Expected: PASS.

---

### Task 6: Update Scan Script Fixtures For New Default Mode

**Files:**
- Modify: `scripts/scan_growth_inflation_regimes.py`
- Modify: `tests/test_growth_inflation_regime_scan.py`

- [ ] **Step 1: Add scan test for passing as-of policy**

Find the test with `fake_classifier(fred, **kwargs)` and add expectations:

```python
assert calls[0]["mode"] == "fred_ra_vintage_asof"
assert calls[0]["as_of_policy"] == "same_day_vintage"
assert calls[0]["requested_as_of"] is None
```

If the scan function uses config arguments, update the call in the test to make defaults explicit:

```python
result = run_growth_inflation_regime_scan(
    fred=FakeFred(),
    dates=[date(2024, 9, 5)],
    mode="fred_ra_vintage_asof",
    as_of_policy="same_day_vintage",
    classifier=fake_classifier,
)
```

- [ ] **Step 2: Update default fixture payloads**

Update helper fixtures that currently return:

```python
"mode": "fred_ra_simple_lagged",
"as_of": "2024-09-05",
```

For default non-legacy scan fixtures, use:

```python
"mode": "fred_ra_vintage_asof",
"as_of": "2024-09-05",
"requested_as_of": "2024-09-05",
"effective_as_of": "2024-09-05",
"lookahead_clamped": False,
"as_of_policy": "same_day_vintage",
```

Add matching `data_quality` fields:

```python
"as_of_policy": "same_day_vintage",
"requested_as_of": "2024-09-05",
"effective_as_of": "2024-09-05",
"lookahead_clamped": False,
```

- [ ] **Step 3: Update script argument parser if needed**

If `scripts/scan_growth_inflation_regimes.py` does not already accept mode arguments, add:

```python
parser.add_argument("--mode", default=DEFAULT_MODE)
parser.add_argument("--as-of-policy", default=DEFAULT_AS_OF_POLICY)
parser.add_argument("--requested-as-of", default=None)
```

Pass through:

```python
classifier(
    fred,
    date=scan_date.isoformat(),
    mode=args.mode,
    as_of_policy=args.as_of_policy,
    requested_as_of=args.requested_as_of,
    growth_series_id=args.growth_series_id,
    inflation_series_id=args.inflation_series_id,
    trend_years=args.trend_years,
)
```

Only pass `growth_lag_months` and `inflation_lag_months` when `args.mode == LEGACY_LAGGED_MODE`.

- [ ] **Step 4: Run scan tests**

Run:

```powershell
python -m pytest tests/test_growth_inflation_regime_scan.py -q
```

Expected: PASS.

---

### Task 7: Run Focused Regression Suite And Formatting

**Files:**
- Modify only files from earlier tasks.

- [ ] **Step 1: Run FRED and quadrant focused tests**

Run:

```powershell
python -m pytest tests/test_fred_macro.py tests/test_fred_growth_inflation_data_availability.py tests/test_ai_trading_team_growth_inflation_quadrant.py tests/test_growth_inflation_regime_scan.py -q
```

Expected: PASS.

- [ ] **Step 2: Run mock quadrant regression tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: PASS.

- [ ] **Step 3: Run ruff on touched files**

Run:

```powershell
python -m ruff check lumibot/example_strategies/fred_growth_inflation_regime_classifier.py lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py scripts/scan_growth_inflation_regimes.py tests/test_ai_trading_team_growth_inflation_quadrant.py tests/test_growth_inflation_regime_scan.py
```

Expected: PASS.

- [ ] **Step 4: Commit implementation**

Run:

```powershell
git add lumibot/example_strategies/fred_growth_inflation_regime_classifier.py lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py scripts/scan_growth_inflation_regimes.py tests/test_ai_trading_team_growth_inflation_quadrant.py tests/test_growth_inflation_regime_scan.py
git commit -m "feat: use FRED vintage as-of macro regime classification"
```

Expected: commit succeeds.

---

### Task 8: Run A Short Real-Strategy Smoke Backtest

**Files:**
- Read: `project_notes/API.txt`
- Run: existing benchmark/backtest script used for the current real quadrant strategy.

- [ ] **Step 1: Load local API keys**

Use the existing project API file format. Do not print secrets. In PowerShell, use the project helper script if one exists; otherwise manually set only the needed environment variables from `project_notes/API.txt`.

Expected environment variables before the run:

```powershell
$env:OPENAI_API_KEY
$env:FRED_API_KEY
$env:AI_TRADING_TEAM_MODEL = "gpt-5.6-luna"
```

- [ ] **Step 2: Run one short weekly real-strategy backtest**

Use the existing benchmark runner command for `AITradingTeamGrowthInflationQuadrantStrategy`. Prefer a short window with a known weekday trigger:

```powershell
python scripts\run_ai_trading_team_examples_benchmark.py --strategy growth-inflation-quadrant --start 2025-06-24 --end 2025-07-08 --run-frequency weekly --max-workers 1 --env-file project_notes\API.txt
```

Expected:
- The trace contains `macro_allocation_agent`.
- The `macro_regime_classifier` tool output has `mode: fred_ra_vintage_asof`.
- The tool output has `requested_as_of`, `effective_as_of`, `lookahead_clamped`, and `as_of_policy`.
- `effective_as_of` is not after the simulated system run date.
- The workflow either proceeds to basket agents or blocks only for a genuine external/API/runtime reason.

- [ ] **Step 3: Open Agent Replay UI and inspect the macro tool output**

Run:

```powershell
python scripts\agent_trace_ui.py
```

Expected:
- The new run is selectable by strategy, backtest run, and system run.
- The macro tool output visibly includes FRED vintage provenance fields.
- No legacy-only `growth_lag_months`, `inflation_lag_months`, or evidence `data_cutoff` fields are required in the default vintage path.

- [ ] **Step 4: Record validation notes**

Create:

```text
docs/superpowers/notes/2026-08-14-fred-vintage-asof-macro-regime-classifier-validation.md
```

Include:

```markdown
# FRED Vintage As-Of Macro Regime Classifier Validation

## Test Commands
- `python -m pytest ...`
- `python -m ruff check ...`
- short benchmark command used

## Smoke Backtest
- Strategy:
- Date window:
- Model:
- Backtest run id:
- System run ids inspected:

## Macro Tool Evidence
- mode:
- requested_as_of:
- effective_as_of:
- lookahead_clamped:
- as_of_policy:
- growth latest observation date / realtime:
- inflation latest observation date / realtime:

## Result
- Passed / failed:
- Follow-up issues:
```

- [ ] **Step 5: Commit validation note**

Run:

```powershell
git add docs/superpowers/notes/2026-08-14-fred-vintage-asof-macro-regime-classifier-validation.md
git commit -m "docs: validate FRED vintage macro regime classifier"
```

Expected: commit succeeds if validation note was created.

---

## Final Verification

Run this full verification set before marking the feature complete:

```powershell
python -m pytest tests/test_fred_macro.py tests/test_fred_growth_inflation_data_availability.py tests/test_ai_trading_team_growth_inflation_quadrant.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py tests/test_growth_inflation_regime_scan.py -q
python -m ruff check lumibot/example_strategies/fred_growth_inflation_regime_classifier.py lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py scripts/scan_growth_inflation_regimes.py tests/test_ai_trading_team_growth_inflation_quadrant.py tests/test_growth_inflation_regime_scan.py
```

Feature is complete when:
- The default real strategy mode is `fred_ra_vintage_asof`.
- The default as-of policy is `same_day_vintage`.
- FRED calls use `as_of=effective_as_of` and `end=effective_as_of`.
- Future requested as-of dates are clamped and reported.
- Vintage output records enough provenance to see what the simulated market could have known.
- Legacy lagged mode still works when explicitly requested.
- The macro agent prompt asks the agent to call the tool and preserve the output, not recalculate the quadrant.
- Agent Replay UI can show the new fields through existing tool output rendering.
