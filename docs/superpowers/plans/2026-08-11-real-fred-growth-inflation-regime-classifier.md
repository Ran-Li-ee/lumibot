# Real FRED Growth / Inflation Regime Classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Stage 2 real FRED-backed Growth / Inflation quadrant classifier and integrate it into a new strategy while preserving the existing mock quadrant strategy.

**Architecture:** Add a focused deterministic classifier module that owns FRED payload validation, lag handling, year-over-year metric calculation, trend comparison, regime mapping, and tool binding. Add a new strategy module that reuses the existing mock quadrant downstream workflow shape but swaps only the macro classifier source from mock to real FRED-backed output.

**Tech Stack:** Python standard library, existing `lumibot.macro.FREDMacroData`, existing `lumibot.components.agents` tool definitions, pytest, ruff, optional short Lumibot Yahoo backtest through `scripts/run_ai_trading_team_examples_benchmark.py`.

---

## File Structure

Create:

- `lumibot/example_strategies/fred_growth_inflation_regime_classifier.py`
  - Pure classifier constants and math.
  - FRED payload validation.
  - Lag cutoff handling.
  - Structured success / blocked / failed output.
  - `make_real_macro_regime_classifier_tool()`.

- `lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py`
  - New real FRED-backed strategy class.
  - Same downstream basket / portfolio / execution workflow as the mock quadrant strategy.
  - Real macro allocation prompt and task wording.

- `tests/test_ai_trading_team_growth_inflation_quadrant.py`
  - Unit tests for classifier math, status handling, tool binding, strategy integration, and benchmark registry.
  - No network tests by default.

Modify:

- `scripts/run_ai_trading_team_examples_benchmark.py`
  - Add one lazy registry entry for the real strategy:
    `growth-inflation-quadrant`.

Do not modify:

- `lumibot/macro/fred.py`, unless a direct integration bug is proven.
- `lumibot/example_strategies/target_portfolio_to_execution_plan.py`, unless a direct integration bug is proven.
- `lumibot/components/agents/*`, unless a direct tool binding bug is proven.
- Existing mock strategy behavior or import path.

---

## Task 1: Add Pure Classifier Math Tests First

**Files:**

- Create: `tests/test_ai_trading_team_growth_inflation_quadrant.py`
- Later implement: `lumibot/example_strategies/fred_growth_inflation_regime_classifier.py`

- [ ] **Step 1: Write failing tests for lag and metric calculation**

Create `tests/test_ai_trading_team_growth_inflation_quadrant.py` with:

```python
from __future__ import annotations

import importlib
import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from lumibot.components.agents.schemas import ToolDefinition


def load_classifier_module():
    return importlib.import_module("lumibot.example_strategies.fred_growth_inflation_regime_classifier")


def load_real_strategy_module():
    module = importlib.import_module("lumibot.example_strategies.ai_trading_team_growth_inflation_quadrant")
    return module, module.AITradingTeamGrowthInflationQuadrantStrategy


def _monthly_observations(start_year=2018, start_month=1, values=None, realtime="2024-09-05"):
    values = list(values or [])
    rows = []
    year = start_year
    month = start_month
    for value in values:
        rows.append(
            {
                "date": f"{year:04d}-{month:02d}-01",
                "value": str(value),
                "realtime_start": realtime,
                "realtime_end": realtime,
            }
        )
        month += 1
        if month == 13:
            month = 1
            year += 1
    return rows


def _quarterly_observations(start_year=2018, start_month=1, values=None, realtime="2024-09-05"):
    values = list(values or [])
    rows = []
    year = start_year
    month = start_month
    for value in values:
        rows.append(
            {
                "date": f"{year:04d}-{month:02d}-01",
                "value": str(value),
                "realtime_start": realtime,
                "realtime_end": realtime,
            }
        )
        month += 3
        if month > 12:
            month -= 12
            year += 1
    return rows


def _payload(series_id, observations, *, source="fred_api", point_in_time_safe=True, uses_revised_data=False):
    return {
        "source": source,
        "series_id": series_id,
        "as_of": "2024-09-05",
        "point_in_time_safe": point_in_time_safe,
        "uses_revised_data": uses_revised_data,
        "observations": observations,
    }


def test_subtract_months_handles_quarter_and_year_boundaries():
    module = load_classifier_module()

    assert module.subtract_months(date(2024, 9, 5), 6) == date(2024, 3, 5)
    assert module.subtract_months(date(2024, 1, 31), 1) == date(2023, 12, 31)
    assert module.subtract_months(date(2024, 3, 31), 1) == date(2024, 2, 29)


def test_growth_axis_evidence_uses_latest_usable_lagged_quarter():
    module = load_classifier_module()
    values = [100 + index for index in range(24)]
    values[-1] = 140
    payload = _payload("GDPC1", _quarterly_observations(values=values))

    evidence = module.calculate_axis_evidence(
        payload,
        axis="growth",
        series_name="Real Gross Domestic Product",
        frequency="quarterly",
        trading_date=date(2024, 9, 5),
        lag_months=6,
        periods_back=4,
        trend_window_observations=20,
        trend_years=5,
    )

    usable_values = [float(row["value"]) for row in payload["observations"] if row["date"] <= "2024-03-05"]
    metrics = [(usable_values[index] / usable_values[index - 4]) - 1 for index in range(4, len(usable_values))]
    assert evidence["data_cutoff"] == "2024-03-05"
    assert evidence["latest_observation_date"] == "2023-10-01"
    assert evidence["metric_value"] == pytest.approx(metrics[-1])
    assert evidence["trend_value"] == pytest.approx(sum(metrics[-20:]) / 20)
    assert evidence["direction"] == ("up" if metrics[-1] > sum(metrics[-20:]) / 20 else "down")


def test_inflation_axis_evidence_uses_latest_usable_lagged_month():
    module = load_classifier_module()
    values = [100 + index for index in range(84)]
    values[-2] = 210
    payload = _payload("CPIAUCSL", _monthly_observations(values=values))

    evidence = module.calculate_axis_evidence(
        payload,
        axis="inflation",
        series_name="Consumer Price Index for All Urban Consumers",
        frequency="monthly",
        trading_date=date(2024, 9, 5),
        lag_months=1,
        periods_back=12,
        trend_window_observations=60,
        trend_years=5,
    )

    usable_values = [float(row["value"]) for row in payload["observations"] if row["date"] <= "2024-08-05"]
    metrics = [(usable_values[index] / usable_values[index - 12]) - 1 for index in range(12, len(usable_values))]
    assert evidence["data_cutoff"] == "2024-08-05"
    assert evidence["latest_observation_date"] == "2024-08-01"
    assert evidence["metric_value"] == pytest.approx(metrics[-1])
    assert evidence["trend_value"] == pytest.approx(sum(metrics[-60:]) / 60)
    assert evidence["direction"] == ("up" if metrics[-1] > sum(metrics[-60:]) / 60 else "down")


def test_equal_metric_and_trend_classifies_axis_as_down():
    module = load_classifier_module()
    values = [100.0] * 24
    payload = _payload("GDPC1", _quarterly_observations(values=values))

    evidence = module.calculate_axis_evidence(
        payload,
        axis="growth",
        series_name="Real Gross Domestic Product",
        frequency="quarterly",
        trading_date=date(2024, 9, 5),
        lag_months=0,
        periods_back=4,
        trend_window_observations=20,
        trend_years=5,
    )

    assert evidence["metric_value"] == pytest.approx(0.0)
    assert evidence["trend_value"] == pytest.approx(0.0)
    assert evidence["margin"] == pytest.approx(0.0)
    assert evidence["direction"] == "down"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_inflation_quadrant.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'lumibot.example_strategies.fred_growth_inflation_regime_classifier'`.

- [ ] **Step 3: Commit failing tests**

```powershell
git add tests\test_ai_trading_team_growth_inflation_quadrant.py
git commit -m "test: define fred regime classifier math"
```

---

## Task 2: Implement Pure Classifier Math

**Files:**

- Create: `lumibot/example_strategies/fred_growth_inflation_regime_classifier.py`
- Modify: `tests/test_ai_trading_team_growth_inflation_quadrant.py`

- [ ] **Step 1: Add the classifier module skeleton and pure math**

Create `lumibot/example_strategies/fred_growth_inflation_regime_classifier.py` with:

```python
from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from typing import Any

from lumibot.components.agents.schemas import BoundTool, ToolDefinition
from lumibot.macro import FREDMacroData

REGIMES = (
    "growth_up_inflation_down",
    "growth_up_inflation_up",
    "growth_down_inflation_up",
    "growth_down_inflation_down",
)

WEIGHT_BY_REGIME = {
    "growth_up_inflation_down": {
        "equity": 0.50,
        "commodity": 0.25,
        "tips": 0.00,
        "nominal_bond": 0.25,
    },
    "growth_up_inflation_up": {
        "equity": 0.25,
        "commodity": 0.50,
        "tips": 0.25,
        "nominal_bond": 0.00,
    },
    "growth_down_inflation_up": {
        "equity": 0.00,
        "commodity": 0.25,
        "tips": 0.50,
        "nominal_bond": 0.25,
    },
    "growth_down_inflation_down": {
        "equity": 0.25,
        "commodity": 0.25,
        "tips": 0.00,
        "nominal_bond": 0.50,
    },
}

DEFAULT_MODE = "fred_ra_simple_lagged"
DEFAULT_GROWTH_SERIES_ID = "GDPC1"
DEFAULT_INFLATION_SERIES_ID = "CPIAUCSL"
DEFAULT_GROWTH_LAG_MONTHS = 6
DEFAULT_INFLATION_LAG_MONTHS = 1
DEFAULT_TREND_YEARS = 5
GROWTH_PERIODS_BACK = 4
GROWTH_TREND_OBSERVATIONS = 20
INFLATION_PERIODS_BACK = 12
INFLATION_TREND_OBSERVATIONS = 60


def parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value or "").strip()
    if not text:
        raise ValueError("date is required.")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()


def subtract_months(value: date, months: int) -> date:
    month_index = value.month - int(months) - 1
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def history_start_for_date(value: date, years: int = 12) -> str:
    return date(max(value.year - years, 1900), value.month, 1).isoformat()


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == ".":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _sanitize_reason(value: Any) -> str:
    text = str(value)
    text = re.sub(r"api_key=[^&\\s]+", "api_key=<redacted>", text, flags=re.IGNORECASE)
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-<redacted>", text)
    return text


def _observations_from_payload(payload: dict[str, Any], *, trading_date: date, data_cutoff: date) -> list[dict[str, Any]]:
    errors = []
    if payload.get("source") != "fred_api":
        errors.append(f"unexpected source: {payload.get('source')!r}")
    if payload.get("point_in_time_safe") is not True:
        errors.append("point_in_time_safe is not true")
    if payload.get("uses_revised_data") is True:
        errors.append("uses_revised_data is true")
    if errors:
        raise ValueError("; ".join(errors))

    observations = []
    future_count = 0
    for row in payload.get("observations") or []:
        obs_date = parse_date(row.get("date"))
        value = _safe_float(row.get("value"))
        if obs_date > trading_date:
            future_count += 1
            continue
        if obs_date <= data_cutoff and value is not None:
            observations.append(
                {
                    "date": obs_date,
                    "value": value,
                    "realtime_start": row.get("realtime_start"),
                    "realtime_end": row.get("realtime_end"),
                }
            )
    if future_count:
        raise ValueError(f"{future_count} observations are after trading date")
    observations.sort(key=lambda row: row["date"])
    return observations


def calculate_axis_evidence(
    payload: dict[str, Any],
    *,
    axis: str,
    series_name: str,
    frequency: str,
    trading_date: date,
    lag_months: int,
    periods_back: int,
    trend_window_observations: int,
    trend_years: int,
) -> dict[str, Any]:
    data_cutoff = subtract_months(trading_date, lag_months)
    observations = _observations_from_payload(payload, trading_date=trading_date, data_cutoff=data_cutoff)
    minimum = periods_back + trend_window_observations
    if len(observations) < minimum:
        raise ValueError(f"{axis} needs {minimum} usable observations, got {len(observations)}")

    metrics = []
    for index in range(periods_back, len(observations)):
        previous = observations[index - periods_back]
        current = observations[index]
        if previous["value"] == 0:
            raise ValueError(f"{axis} comparison value is zero on {previous['date'].isoformat()}")
        metrics.append(
            {
                "observation": current,
                "comparison": previous,
                "value": (current["value"] / previous["value"]) - 1.0,
            }
        )

    trend_metrics = metrics[-trend_window_observations:]
    latest = trend_metrics[-1]
    trend_value = sum(item["value"] for item in trend_metrics) / len(trend_metrics)
    metric_value = latest["value"]
    margin = metric_value - trend_value
    direction = "up" if metric_value > trend_value else "down"
    return {
        "axis": axis,
        "series_id": str(payload.get("series_id")),
        "series_name": series_name,
        "frequency": frequency,
        "lag_months": int(lag_months),
        "data_cutoff": data_cutoff.isoformat(),
        "latest_observation_date": latest["observation"]["date"].isoformat(),
        "comparison_observation_date": latest["comparison"]["date"].isoformat(),
        "latest_value": latest["observation"]["value"],
        "comparison_value": latest["comparison"]["value"],
        "metric_name": "year_over_year_change",
        "metric_value": metric_value,
        "trend_years": int(trend_years),
        "trend_window_observations": int(trend_window_observations),
        "trend_value": trend_value,
        "margin": margin,
        "direction": direction,
    }
```

- [ ] **Step 2: Run Task 1 tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_inflation_quadrant.py -q
```

Expected: PASS for the four tests from Task 1.

- [ ] **Step 3: Commit pure math implementation**

```powershell
git add lumibot\example_strategies\fred_growth_inflation_regime_classifier.py tests\test_ai_trading_team_growth_inflation_quadrant.py
git commit -m "feat: calculate fred growth inflation evidence"
```

---

## Task 3: Add Regime Classification And Status Tests

**Files:**

- Modify: `lumibot/example_strategies/fred_growth_inflation_regime_classifier.py`
- Modify: `tests/test_ai_trading_team_growth_inflation_quadrant.py`

- [ ] **Step 1: Add fake FRED client and classification tests**

Append to `tests/test_ai_trading_team_growth_inflation_quadrant.py`:

```python
class FakeFredClient:
    def __init__(self, payloads):
        self.payloads = dict(payloads)
        self.calls = []

    def get_series(self, series_id, *, start=None, end=None, as_of=None, limit=None):
        self.calls.append(
            {
                "series_id": series_id,
                "start": start,
                "end": end,
                "as_of": as_of,
                "limit": limit,
            }
        )
        value = self.payloads[series_id]
        if isinstance(value, Exception):
            raise value
        return value


def _growth_payload_with_latest_direction(direction):
    values = [100 + index for index in range(32)]
    values[-3] = 150 if direction == "up" else 124
    return _payload("GDPC1", _quarterly_observations(values=values))


def _inflation_payload_with_latest_direction(direction):
    values = [100 + index for index in range(90)]
    values[-2] = 220 if direction == "up" else 182
    return _payload("CPIAUCSL", _monthly_observations(values=values))


@pytest.mark.parametrize(
    ("growth_direction", "inflation_direction", "expected_regime", "expected_weights"),
    [
        (
            "up",
            "down",
            "growth_up_inflation_down",
            {"equity": 0.50, "commodity": 0.25, "tips": 0.00, "nominal_bond": 0.25},
        ),
        (
            "up",
            "up",
            "growth_up_inflation_up",
            {"equity": 0.25, "commodity": 0.50, "tips": 0.25, "nominal_bond": 0.00},
        ),
        (
            "down",
            "up",
            "growth_down_inflation_up",
            {"equity": 0.00, "commodity": 0.25, "tips": 0.50, "nominal_bond": 0.25},
        ),
        (
            "down",
            "down",
            "growth_down_inflation_down",
            {"equity": 0.25, "commodity": 0.25, "tips": 0.00, "nominal_bond": 0.50},
        ),
    ],
)
def test_classify_growth_inflation_regime_maps_all_quadrants(
    growth_direction,
    inflation_direction,
    expected_regime,
    expected_weights,
):
    module = load_classifier_module()
    fred = FakeFredClient(
        {
            "GDPC1": _growth_payload_with_latest_direction(growth_direction),
            "CPIAUCSL": _inflation_payload_with_latest_direction(inflation_direction),
        }
    )

    result = module.classify_growth_inflation_regime(
        fred=fred,
        date="2024-09-05",
        previous_regime="growth_up_inflation_down",
    )

    assert result["tool"] == "macro_regime_classifier"
    assert result["status"] == "passed"
    assert result["mock"] is False
    assert result["mode"] == "fred_ra_simple_lagged"
    assert result["regime"] == expected_regime
    assert result["growth_direction"] == growth_direction
    assert result["inflation_direction"] == inflation_direction
    assert result["basket_weights"] == expected_weights
    assert result["previous_regime"] == "growth_up_inflation_down"
    assert result["regime_changed"] == (expected_regime != "growth_up_inflation_down")
    assert result["growth_evidence"]["axis"] == "growth"
    assert result["inflation_evidence"]["axis"] == "inflation"
    assert result["data_quality"]["status"] == "passed"
    assert "Growth" in result["reason_brief"]
    assert "Inflation" in result["reason_brief"]
    assert [call["series_id"] for call in fred.calls] == ["GDPC1", "CPIAUCSL"]


def test_classify_growth_inflation_regime_blocks_missing_fred_key_without_throwing():
    module = load_classifier_module()
    fred = FakeFredClient({"GDPC1": ValueError("FRED_API_KEY is required to fetch FRED macro data.")})

    result = module.classify_growth_inflation_regime(fred=fred, date="2024-09-05")

    assert result["status"] == "blocked"
    assert result["reason"] == "missing_fred_api_key"
    assert result["mock"] is False
    assert result["data_quality"]["status"] == "blocked"
    assert "FRED_API_KEY" in result["data_quality"]["errors"][0]
    assert "regime" not in result


def test_classify_growth_inflation_regime_fails_unsafe_payload_without_throwing():
    module = load_classifier_module()
    fred = FakeFredClient(
        {
            "GDPC1": _payload("GDPC1", _quarterly_observations(values=[100 + index for index in range(32)]), source="csv"),
            "CPIAUCSL": _inflation_payload_with_latest_direction("up"),
        }
    )

    result = module.classify_growth_inflation_regime(fred=fred, date="2024-09-05")

    assert result["status"] == "failed"
    assert result["reason"] == "classification_failed"
    assert result["data_quality"]["status"] == "failed"
    assert "unexpected source" in result["data_quality"]["errors"][0]
```

- [ ] **Step 2: Run tests to verify missing classifier function**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_inflation_quadrant.py -q
```

Expected: FAIL because `classify_growth_inflation_regime()` does not exist.

- [ ] **Step 3: Implement regime classification and structured status**

Append these functions to `lumibot/example_strategies/fred_growth_inflation_regime_classifier.py`:

```python
def regime_from_directions(growth_direction: str, inflation_direction: str) -> str:
    regime = f"growth_{growth_direction}_inflation_{inflation_direction}"
    if regime not in REGIMES:
        raise ValueError(f"unsupported regime directions: {growth_direction}, {inflation_direction}")
    return regime


def _blocked_result(*, date_text: str, reason: str, error: str, mode: str = DEFAULT_MODE) -> dict[str, Any]:
    return {
        "tool": "macro_regime_classifier",
        "status": "blocked",
        "mock": False,
        "mode": mode,
        "date": date_text,
        "as_of": date_text,
        "reason": reason,
        "data_quality": {
            "status": "blocked",
            "source": "fred_api",
            "point_in_time_safe": None,
            "uses_revised_data": None,
            "required_series": [DEFAULT_GROWTH_SERIES_ID, DEFAULT_INFLATION_SERIES_ID],
            "warnings": [],
            "errors": [_sanitize_reason(error)],
        },
    }


def _failed_result(*, date_text: str, error: str, mode: str = DEFAULT_MODE) -> dict[str, Any]:
    return {
        "tool": "macro_regime_classifier",
        "status": "failed",
        "mock": False,
        "mode": mode,
        "date": date_text,
        "as_of": date_text,
        "reason": "classification_failed",
        "data_quality": {
            "status": "failed",
            "source": "fred_api",
            "point_in_time_safe": None,
            "uses_revised_data": None,
            "required_series": [DEFAULT_GROWTH_SERIES_ID, DEFAULT_INFLATION_SERIES_ID],
            "warnings": [],
            "errors": [_sanitize_reason(error)],
        },
    }


def _confidence_from_margins(growth_margin: float, inflation_margin: float) -> dict[str, Any]:
    near_zero_threshold = 0.001
    level = "medium" if abs(growth_margin) < near_zero_threshold or abs(inflation_margin) < near_zero_threshold else "high"
    return {
        "level": level,
        "basis": "data_quality_and_axis_margins",
        "growth_margin": growth_margin,
        "inflation_margin": inflation_margin,
        "notes": [],
    }


def classify_growth_inflation_regime(
    *,
    fred: Any,
    date: str | date,
    previous_regime: str | None = None,
    mode: str = DEFAULT_MODE,
    growth_series_id: str = DEFAULT_GROWTH_SERIES_ID,
    inflation_series_id: str = DEFAULT_INFLATION_SERIES_ID,
    growth_lag_months: int = DEFAULT_GROWTH_LAG_MONTHS,
    inflation_lag_months: int = DEFAULT_INFLATION_LAG_MONTHS,
    trend_years: int = DEFAULT_TREND_YEARS,
) -> dict[str, Any]:
    trading_date = parse_date(date)
    date_text = trading_date.isoformat()
    if mode != DEFAULT_MODE:
        return _failed_result(date_text=date_text, error=f"unsupported macro classifier mode: {mode}", mode=mode)

    try:
        start = history_start_for_date(trading_date)
        growth_payload = fred.get_series(growth_series_id, start=start, end=date_text, as_of=date_text)
        inflation_payload = fred.get_series(inflation_series_id, start=start, end=date_text, as_of=date_text)
        growth_evidence = calculate_axis_evidence(
            growth_payload,
            axis="growth",
            series_name="Real Gross Domestic Product",
            frequency="quarterly",
            trading_date=trading_date,
            lag_months=growth_lag_months,
            periods_back=GROWTH_PERIODS_BACK,
            trend_window_observations=GROWTH_TREND_OBSERVATIONS,
            trend_years=trend_years,
        )
        inflation_evidence = calculate_axis_evidence(
            inflation_payload,
            axis="inflation",
            series_name="Consumer Price Index for All Urban Consumers",
            frequency="monthly",
            trading_date=trading_date,
            lag_months=inflation_lag_months,
            periods_back=INFLATION_PERIODS_BACK,
            trend_window_observations=INFLATION_TREND_OBSERVATIONS,
            trend_years=trend_years,
        )
    except Exception as exc:
        message = _sanitize_reason(exc)
        if "FRED_API_KEY" in message:
            return _blocked_result(date_text=date_text, reason="missing_fred_api_key", error=message, mode=mode)
        return _failed_result(date_text=date_text, error=message, mode=mode)

    regime = regime_from_directions(growth_evidence["direction"], inflation_evidence["direction"])
    return {
        "tool": "macro_regime_classifier",
        "status": "passed",
        "mock": False,
        "mode": mode,
        "date": date_text,
        "as_of": date_text,
        "regime": regime,
        "growth_direction": growth_evidence["direction"],
        "inflation_direction": inflation_evidence["direction"],
        "previous_regime": previous_regime,
        "regime_changed": previous_regime is not None and previous_regime != regime,
        "basket_weights": dict(WEIGHT_BY_REGIME[regime]),
        "growth_evidence": growth_evidence,
        "inflation_evidence": inflation_evidence,
        "data_quality": {
            "status": "passed",
            "source": "fred_api",
            "point_in_time_safe": True,
            "uses_revised_data": False,
            "required_series": [growth_series_id, inflation_series_id],
            "warnings": [],
            "errors": [],
        },
        "confidence": _confidence_from_margins(growth_evidence["margin"], inflation_evidence["margin"]),
        "reason_brief": (
            f"Growth is {growth_evidence['direction']} because YoY real GDP "
            f"{growth_evidence['metric_value']:.4f} is compared with five-year trend "
            f"{growth_evidence['trend_value']:.4f}. Inflation is {inflation_evidence['direction']} "
            f"because YoY CPI {inflation_evidence['metric_value']:.4f} is compared with five-year trend "
            f"{inflation_evidence['trend_value']:.4f}."
        ),
    }
```

- [ ] **Step 4: Run classifier tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_inflation_quadrant.py -q
```

Expected: PASS for pure classifier and status tests.

- [ ] **Step 5: Commit classifier status implementation**

```powershell
git add lumibot\example_strategies\fred_growth_inflation_regime_classifier.py tests\test_ai_trading_team_growth_inflation_quadrant.py
git commit -m "feat: classify fred growth inflation regimes"
```

---

## Task 4: Add Real Macro Tool Binding

**Files:**

- Modify: `lumibot/example_strategies/fred_growth_inflation_regime_classifier.py`
- Modify: `tests/test_ai_trading_team_growth_inflation_quadrant.py`

- [ ] **Step 1: Add tool binding tests**

Append to `tests/test_ai_trading_team_growth_inflation_quadrant.py`:

```python
def test_make_real_macro_regime_classifier_tool_binds_stateful_tool():
    module = load_classifier_module()
    strategy = SimpleNamespace(
        parameters={
            "macro_classifier_mode": "fred_ra_simple_lagged",
            "growth_lag_months": 6,
            "inflation_lag_months": 1,
            "trend_years": 5,
        },
        _last_real_regime=None,
        get_datetime=lambda: datetime(2024, 9, 5, 9, 30),
    )
    fred = FakeFredClient(
        {
            "GDPC1": _growth_payload_with_latest_direction("up"),
            "CPIAUCSL": _inflation_payload_with_latest_direction("down"),
        }
    )

    tool_definition = module.make_real_macro_regime_classifier_tool(fred_factory=lambda strategy: fred)
    tool = tool_definition.binder(strategy, None)
    first = tool.function(date="2024-09-05")
    second = tool.function(date="2024-09-06")

    assert isinstance(tool_definition, ToolDefinition)
    assert tool_definition.name == "macro_regime_classifier"
    assert "FRED point-in-time GDPC1 and CPIAUCSL" in tool_definition.description
    assert tool_definition.metadata == {"kind": "fred_macro_regime", "mock": False, "replay_on_cache": True}
    assert tool.name == "macro_regime_classifier"
    assert tool.metadata == {"kind": "fred_macro_regime", "mock": False, "replay_on_cache": True}
    assert first["previous_regime"] is None
    assert second["previous_regime"] == first["regime"]
    assert strategy._last_real_regime == second["regime"]
```

- [ ] **Step 2: Run focused test to verify missing tool function**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_inflation_quadrant.py::test_make_real_macro_regime_classifier_tool_binds_stateful_tool -q
```

Expected: FAIL because `make_real_macro_regime_classifier_tool()` does not exist.

- [ ] **Step 3: Implement tool definition**

Append to `lumibot/example_strategies/fred_growth_inflation_regime_classifier.py`:

```python
def make_real_macro_regime_classifier_tool(fred_factory: Any | None = None) -> ToolDefinition:
    name = "macro_regime_classifier"
    description = (
        "Classify the Growth / Inflation quadrant using FRED point-in-time GDPC1 and CPIAUCSL data. "
        "The tool applies configured lags, compares year-over-year metrics with five-year rolling trends, "
        "and returns basket weights plus evidence. Do not manually recalculate its output."
    )
    metadata = {"kind": "fred_macro_regime", "mock": False, "replay_on_cache": True}

    def binder(strategy: Any, manager: Any) -> BoundTool:
        def macro_regime_classifier(
            *,
            date: str | None = None,
            mode: str | None = None,
            growth_series_id: str | None = None,
            inflation_series_id: str | None = None,
            growth_lag_months: int | None = None,
            inflation_lag_months: int | None = None,
            trend_years: int | None = None,
        ) -> dict[str, Any]:
            resolved_date = date or strategy.get_datetime().date().isoformat()
            parameters = getattr(strategy, "parameters", {}) or {}
            factory = fred_factory or (lambda bound_strategy: FREDMacroData(strategy=bound_strategy))
            fred = factory(strategy)
            result = classify_growth_inflation_regime(
                fred=fred,
                date=resolved_date,
                previous_regime=getattr(strategy, "_last_real_regime", None),
                mode=mode or parameters.get("macro_classifier_mode", DEFAULT_MODE),
                growth_series_id=growth_series_id or parameters.get("growth_series_id", DEFAULT_GROWTH_SERIES_ID),
                inflation_series_id=inflation_series_id
                or parameters.get("inflation_series_id", DEFAULT_INFLATION_SERIES_ID),
                growth_lag_months=int(
                    growth_lag_months
                    if growth_lag_months is not None
                    else parameters.get("growth_lag_months", DEFAULT_GROWTH_LAG_MONTHS)
                ),
                inflation_lag_months=int(
                    inflation_lag_months
                    if inflation_lag_months is not None
                    else parameters.get("inflation_lag_months", DEFAULT_INFLATION_LAG_MONTHS)
                ),
                trend_years=int(trend_years if trend_years is not None else parameters.get("trend_years", DEFAULT_TREND_YEARS)),
            )
            if result.get("status") == "passed":
                strategy._last_real_regime = result["regime"]
            return result

        return BoundTool(
            name=name,
            description=description,
            function=macro_regime_classifier,
            source="local",
            metadata=metadata,
        )

    return ToolDefinition(name=name, description=description, binder=binder, metadata=metadata)
```

- [ ] **Step 4: Run focused and full tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_inflation_quadrant.py::test_make_real_macro_regime_classifier_tool_binds_stateful_tool -q
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_inflation_quadrant.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit tool binding**

```powershell
git add lumibot\example_strategies\fred_growth_inflation_regime_classifier.py tests\test_ai_trading_team_growth_inflation_quadrant.py
git commit -m "feat: expose fred macro classifier tool"
```

---

## Task 5: Add Real Strategy Integration Tests

**Files:**

- Create: `lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py`
- Modify: `tests/test_ai_trading_team_growth_inflation_quadrant.py`

- [ ] **Step 1: Add strategy fixture helpers**

Append to `tests/test_ai_trading_team_growth_inflation_quadrant.py`:

```python
class RecordingAgentManager:
    def __init__(self):
        self.created = []
        self._agents = {}
        self.summaries = {}
        self.tool_calls = {}
        self.planner_results = {}
        self.strategy = None

    def create(self, **kwargs):
        self.created.append(kwargs)
        agent = RecordingAgent(kwargs["name"], self)
        self._agents[kwargs["name"]] = agent
        return agent

    def __getitem__(self, name):
        return self._agents[name]


class RecordingAgent:
    def __init__(self, name, agent_manager):
        self.name = name
        self.agent_manager = agent_manager
        self.calls = []

    def run(self, *, task_prompt, context):
        self.calls.append({"task_prompt": task_prompt, "context": context})
        summary = self.agent_manager.summaries.get(self.name, f"{self.name} summary")
        if self.name in self.agent_manager.planner_results and self.agent_manager.strategy is not None:
            self.agent_manager.strategy._last_target_portfolio_planner_result = self.agent_manager.planner_results[
                self.name
            ]
        return SimpleNamespace(
            summary=summary,
            tool_calls=[
                SimpleNamespace(tool_name=tool_name)
                for tool_name in self.agent_manager.tool_calls.get(self.name, [])
            ],
        )


def make_strategy_with_agent_manager(strategy_class, agent_manager):
    strategy = object.__new__(strategy_class)
    strategy.agents = agent_manager
    agent_manager.strategy = strategy
    strategy.parameters = dict(strategy_class.parameters)
    strategy.get_datetime = lambda: datetime(2024, 9, 5, 9, 30)
    strategy.get_cash = lambda: 100000.0
    strategy.get_portfolio_value = lambda: 100000.0
    strategy.get_last_price = lambda symbol: 100.0
    strategy.get_positions = lambda include_cash_positions=False: []
    return strategy


def created_tool_names(created_agent):
    return {getattr(tool, "name", "") for tool in created_agent.get("tools", [])}
```

- [ ] **Step 2: Add tests for real strategy agent surfaces and prompts**

Append:

```python
def test_real_strategy_creates_expected_agents_and_tool_surfaces():
    _module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()

    created = {agent["name"]: agent for agent in agent_manager.created}
    assert list(created) == [
        "macro_allocation_agent",
        "equity_basket_agent",
        "commodity_basket_agent",
        "tips_basket_agent",
        "nominal_bond_basket_agent",
        "portfolio_decision_agent",
        "execution_agent",
    ]
    assert created_tool_names(created["macro_allocation_agent"]) == {"macro_regime_classifier"}
    assert created["macro_allocation_agent"]["include_builtin_tools"] is False
    assert created["macro_allocation_agent"]["tools"][0].metadata["mock"] is False
    for basket_agent in (
        "equity_basket_agent",
        "commodity_basket_agent",
        "tips_basket_agent",
        "nominal_bond_basket_agent",
    ):
        assert created_tool_names(created[basket_agent]) == {
            "market_load_history_tables_summary",
            "market_last_price",
        }
    assert created_tool_names(created["portfolio_decision_agent"]) == {"target_portfolio_to_execution_plan"}
    assert created_tool_names(created["execution_agent"]) == {"execution_plan_execute"}


def test_real_strategy_prompts_do_not_ask_llm_to_classify_macro_or_use_mock_language():
    _module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()

    serialized = json.dumps(agent_manager.created, default=str).lower()
    assert "real fred-backed macro_regime_classifier" in serialized
    assert "do not classify the macro regime yourself" in serialized
    assert "do not manually recalculate" in serialized
    assert "mock macro_regime_classifier" not in serialized
    assert "deterministic mock growth" not in serialized
    assert "duckdb" not in serialized
    assert "call target_portfolio_to_execution_plan" in serialized
    assert "call execution_plan_execute exactly once with the complete execution_plan" in serialized
```

- [ ] **Step 3: Run tests to verify real strategy module is missing**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_inflation_quadrant.py::test_real_strategy_creates_expected_agents_and_tool_surfaces tests\test_ai_trading_team_growth_inflation_quadrant.py::test_real_strategy_prompts_do_not_ask_llm_to_classify_macro_or_use_mock_language -q
```

Expected: FAIL because `ai_trading_team_growth_inflation_quadrant.py` does not exist.

- [ ] **Step 4: Create the real strategy module**

Create `lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py` with:

```python
from __future__ import annotations

import os
from typing import Any

from lumibot.components.agents.builtins import BuiltinTools
from lumibot.example_strategies.ai_trading_team_growth_execution_test import (
    AITradingTeamGrowthExecutionTestStrategy,
    validate_decision_buy_sizing,
    validate_execution_plan_cash_safety,
)
from lumibot.example_strategies.ai_trading_team_mock_growth_inflation_quadrant import (
    BASKET_AGENT_NAMES,
    BASKET_UNIVERSES,
    _parse_json_summary,
    _require_dict,
    execution_plan_execute_payload,
    parse_execution_plan_from_portfolio_summary,
    validate_execution_plan_matches_planner_result,
    validate_execution_plan_symbols,
    validate_portfolio_decision_tool_evidence,
)
from lumibot.example_strategies.fred_growth_inflation_regime_classifier import (
    DEFAULT_GROWTH_LAG_MONTHS,
    DEFAULT_GROWTH_SERIES_ID,
    DEFAULT_INFLATION_LAG_MONTHS,
    DEFAULT_INFLATION_SERIES_ID,
    DEFAULT_MODE,
    DEFAULT_TREND_YEARS,
    make_real_macro_regime_classifier_tool,
)
from lumibot.example_strategies.target_portfolio_to_execution_plan import (
    TOOL_NAME as TARGET_PORTFOLIO_TO_EXECUTION_PLAN_TOOL_NAME,
)
from lumibot.example_strategies.target_portfolio_to_execution_plan import (
    make_target_portfolio_to_execution_plan_tool,
)


class AITradingTeamGrowthInflationQuadrantStrategy(AITradingTeamGrowthExecutionTestStrategy):
    parameters = {
        "basket_universes": BASKET_UNIVERSES,
        "macro_classifier_mode": DEFAULT_MODE,
        "growth_series_id": DEFAULT_GROWTH_SERIES_ID,
        "inflation_series_id": DEFAULT_INFLATION_SERIES_ID,
        "growth_lag_months": DEFAULT_GROWTH_LAG_MONTHS,
        "inflation_lag_months": DEFAULT_INFLATION_LAG_MONTHS,
        "trend_years": DEFAULT_TREND_YEARS,
    }
    _execution_agent_base_system_prompt_mode = "execution_minimal"

    def initialize(self):
        self.sleeptime = "1D"
        self._last_real_regime = None
        self._last_target_portfolio_planner_result = None
        model = os.environ.get("AI_TRADING_TEAM_MODEL", "gemini-3.1-flash-lite")

        self.agents.create(
            name="macro_allocation_agent",
            model=model,
            allow_trading=False,
            include_builtin_tools=False,
            tools=[make_real_macro_regime_classifier_tool()],
            system_prompt=(
                "Macro allocation role: call the real FRED-backed macro_regime_classifier and return "
                "the tool result's status, regime, basket weights, evidence summary, and reason_brief. "
                "Do not classify the macro regime yourself. Do not manually recalculate tool evidence. "
                "If the tool returns blocked or failed, return that status plainly. Do not place orders."
            ),
        )

        basket_universes = self.parameters.get("basket_universes", BASKET_UNIVERSES)
        for basket_id, agent_name in BASKET_AGENT_NAMES.items():
            symbols = ", ".join(basket_universes[basket_id])
            self.agents.create(
                name=agent_name,
                model=model,
                allow_trading=False,
                include_builtin_tools=False,
                tools=[
                    BuiltinTools.market.load_history_tables_summary(),
                    BuiltinTools.market.last_price(),
                ],
                system_prompt=(
                    f"{basket_id.replace('_', ' ').title()} basket role: stay inside the assigned basket "
                    f"({symbols}). Select one symbol when active, or report inactive when its target weight is "
                    "zero. Return basket_id, selected_symbol, status, and reason_brief. Do not place orders. "
                    "Do not reclassify macro conditions."
                ),
            )

        self.agents.create(
            name="portfolio_decision_agent",
            model=model,
            allow_trading=False,
            include_builtin_tools=False,
            tools=[make_target_portfolio_to_execution_plan_tool()],
            system_prompt=(
                "Portfolio decision role: do not redo macro or basket research. Merge the macro allocation report "
                "and basket reports into a target_portfolio, then call "
                f"{TARGET_PORTFOLIO_TO_EXECUTION_PLAN_TOOL_NAME}. Do not "
                "place orders. Do not manually calculate share quantities, cash usage, order side, or order sequence. "
                "The planner tool owns all execution_plan calculations. "
                "The planner tool owns daily backtest buy sizing, including its price basis and buy sizing buffer. "
                "Return only one valid JSON object; do not "
                "include markdown, RESULT text, or prose after the JSON. The top-level fields decision, "
                "target_portfolio, and execution_plan are required. decision must include type and reason_brief. "
                "target_portfolio must list the selected active basket targets as symbols and target weights. "
                "The final execution_plan must be copied exactly from the planner tool result. Do not modify "
                "tool-generated quantities, sides, order_type, time_in_force, or sequence values."
            ),
        )

        self.agents.create(
            name="execution_agent",
            model=model,
            allow_trading=True,
            base_system_prompt_mode=self._execution_agent_base_system_prompt_mode,
            include_builtin_tools=False,
            tools=[BuiltinTools.orders.execute_plan()],
            system_prompt=(
                "Execution role: execute only provided execution_plan. "
                "Call execution_plan_execute exactly once with the complete execution_plan. "
                "Do not manually execute individual orders. "
                "Do not call lower-level order, account, open-order, or price tools when execution_plan_execute "
                "is available. Do not research, change fields, reorder orders, split orders, or repair the plan. "
                "If the tool returns plan_status=completed, summarize completed orders. If it returns "
                "plan_status=blocked or invalid, summarize where execution stopped and why."
            ),
        )

    def on_trading_iteration(self):
        current_date = self.get_datetime().date().isoformat()
        basket_universes = self.parameters.get("basket_universes", BASKET_UNIVERSES)

        try:
            macro_result = self.agents["macro_allocation_agent"].run(
                task_prompt=(
                    "Run the real FRED-backed macro allocation step and return one JSON object with status, "
                    "regime, basket_weights, mock flag, regime_changed, growth_evidence, inflation_evidence, "
                    "data_quality, confidence, and reason_brief. Do not calculate the regime manually."
                ),
                context={
                    "date": current_date,
                    "macro_classifier_mode": self.parameters.get("macro_classifier_mode", DEFAULT_MODE),
                    "growth_series_id": self.parameters.get("growth_series_id", DEFAULT_GROWTH_SERIES_ID),
                    "inflation_series_id": self.parameters.get("inflation_series_id", DEFAULT_INFLATION_SERIES_ID),
                    "growth_lag_months": self.parameters.get("growth_lag_months", DEFAULT_GROWTH_LAG_MONTHS),
                    "inflation_lag_months": self.parameters.get(
                        "inflation_lag_months", DEFAULT_INFLATION_LAG_MONTHS
                    ),
                    "trend_years": self.parameters.get("trend_years", DEFAULT_TREND_YEARS),
                    "basket_universes": basket_universes,
                },
            )
            macro_report = _parse_json_summary(macro_result.summary, "macro_allocation_agent")
            if macro_report.get("status") != "passed":
                self._last_macro_regime_error = macro_report
                print(f"Real quadrant workflow blocked: macro classifier status {macro_report.get('status')}")
                return

            basket_reports_by_id = {}
            basket_weights = _require_dict(macro_report.get("basket_weights", {}), "macro basket_weights")
            for basket_id, agent_name in BASKET_AGENT_NAMES.items():
                basket_result = self.agents[agent_name].run(
                    task_prompt=(
                        "Review only the assigned basket and return one JSON object with basket_id, "
                        "target_weight, status, candidate_symbols, selected_symbol, and reason_brief."
                    ),
                    context={
                        "date": current_date,
                        "basket_id": basket_id,
                        "basket_symbols": basket_universes[basket_id],
                        "target_weight": float(basket_weights.get(basket_id, 0.0)),
                        "macro_allocation_report": macro_report,
                    },
                )
                basket_reports_by_id[basket_id] = _parse_json_summary(basket_result.summary, agent_name)

            self._last_target_portfolio_planner_result = None
            portfolio_result = self.agents["portfolio_decision_agent"].run(
                task_prompt=(
                    "Create target_portfolio from the provided macro and basket reports, then call "
                    f"{TARGET_PORTFOLIO_TO_EXECUTION_PLAN_TOOL_NAME} with date and target_portfolio. "
                    "Return only the strict JSON object with decision, target_portfolio, and the planner tool's "
                    "execution_plan copied exactly."
                ),
                context={
                    "date": current_date,
                    "macro_allocation_report": macro_report,
                    "equity_basket_report": basket_reports_by_id["equity"],
                    "commodity_basket_report": basket_reports_by_id["commodity"],
                    "tips_basket_report": basket_reports_by_id["tips"],
                    "nominal_bond_basket_report": basket_reports_by_id["nominal_bond"],
                },
            )
            execution_plan = parse_execution_plan_from_portfolio_summary(portfolio_result.summary)
            validate_portfolio_decision_tool_evidence(execution_plan, portfolio_result)
            validate_execution_plan_matches_planner_result(self, execution_plan)
            validate_execution_plan_symbols(execution_plan, list(basket_reports_by_id.values()))
            validate_decision_buy_sizing(self, execution_plan)
            validate_execution_plan_cash_safety(self, execution_plan)
        except ValueError as exc:
            self._last_execution_plan_error = str(exc)
            print(f"Real quadrant workflow blocked: {exc}")
            return

        self._last_macro_regime_error = None
        self._last_execution_plan_error = None
        if execution_plan["intent"] == "hold" or not execution_plan["orders"]:
            return

        self.agents["execution_agent"].run(
            task_prompt=(
                "Execute the provided execution_plan by calling execution_plan_execute exactly once with the "
                "complete execution_plan. Summarize the returned plan report. Do not call per-order tools."
            ),
            context={
                "date": current_date,
                "execution_plan": execution_plan_execute_payload(execution_plan),
            },
        )
```

- [ ] **Step 5: Run focused strategy tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_inflation_quadrant.py::test_real_strategy_creates_expected_agents_and_tool_surfaces tests\test_ai_trading_team_growth_inflation_quadrant.py::test_real_strategy_prompts_do_not_ask_llm_to_classify_macro_or_use_mock_language -q
```

Expected: PASS.

- [ ] **Step 6: Commit real strategy skeleton**

```powershell
git add lumibot\example_strategies\ai_trading_team_growth_inflation_quadrant.py tests\test_ai_trading_team_growth_inflation_quadrant.py
git commit -m "feat: add real growth inflation quadrant strategy"
```

---

## Task 6: Add Workflow And Blocked-Path Integration Tests

**Files:**

- Modify: `tests/test_ai_trading_team_growth_inflation_quadrant.py`
- Modify if needed: `lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py`

- [ ] **Step 1: Add on-trading-iteration blocked macro test**

Append:

```python
def test_real_strategy_stops_when_macro_classifier_returns_blocked():
    _module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()
    agent_manager.summaries["macro_allocation_agent"] = json.dumps(
        {
            "tool": "macro_regime_classifier",
            "status": "blocked",
            "mock": False,
            "date": "2024-09-05",
            "as_of": "2024-09-05",
            "reason": "missing_fred_api_key",
            "data_quality": {"status": "blocked", "warnings": [], "errors": ["FRED_API_KEY is required"]},
        }
    )

    strategy.on_trading_iteration()

    assert len(agent_manager._agents["macro_allocation_agent"].calls) == 1
    assert agent_manager._agents["equity_basket_agent"].calls == []
    assert agent_manager._agents["portfolio_decision_agent"].calls == []
    assert agent_manager._agents["execution_agent"].calls == []
    assert strategy._last_macro_regime_error["status"] == "blocked"
```

- [ ] **Step 2: Add passed workflow context test**

Append:

```python
def test_real_strategy_passes_real_macro_report_to_basket_and_portfolio_agents():
    _module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()
    macro_report = {
        "tool": "macro_regime_classifier",
        "status": "passed",
        "mock": False,
        "mode": "fred_ra_simple_lagged",
        "date": "2024-09-05",
        "as_of": "2024-09-05",
        "regime": "growth_up_inflation_down",
        "growth_direction": "up",
        "inflation_direction": "down",
        "previous_regime": None,
        "regime_changed": False,
        "basket_weights": {"equity": 0.5, "commodity": 0.25, "tips": 0.0, "nominal_bond": 0.25},
        "growth_evidence": {"axis": "growth"},
        "inflation_evidence": {"axis": "inflation"},
        "data_quality": {"status": "passed", "warnings": [], "errors": []},
        "confidence": {"level": "high"},
        "reason_brief": "Growth up and inflation down.",
    }
    agent_manager.summaries["macro_allocation_agent"] = json.dumps(macro_report)
    agent_manager.summaries["equity_basket_agent"] = json.dumps(
        {"basket_id": "equity", "target_weight": 0.5, "status": "active", "selected_symbol": "SPY"}
    )
    agent_manager.summaries["commodity_basket_agent"] = json.dumps(
        {"basket_id": "commodity", "target_weight": 0.25, "status": "active", "selected_symbol": "GLD"}
    )
    agent_manager.summaries["tips_basket_agent"] = json.dumps(
        {"basket_id": "tips", "target_weight": 0.0, "status": "inactive", "selected_symbol": None}
    )
    agent_manager.summaries["nominal_bond_basket_agent"] = json.dumps(
        {"basket_id": "nominal_bond", "target_weight": 0.25, "status": "active", "selected_symbol": "IEF"}
    )
    planner_plan = {"schema_version": 1, "intent": "hold", "orders": []}
    agent_manager.planner_results["portfolio_decision_agent"] = planner_plan
    agent_manager.tool_calls["portfolio_decision_agent"] = ["target_portfolio_to_execution_plan"]
    agent_manager.summaries["portfolio_decision_agent"] = json.dumps(
        {
            "decision": {"type": "hold", "reason_brief": "Already aligned."},
            "target_portfolio": [
                {"basket_id": "equity", "symbol": "SPY", "target_weight": 0.5},
                {"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.25},
                {"basket_id": "nominal_bond", "symbol": "IEF", "target_weight": 0.25},
            ],
            "execution_plan": planner_plan,
        }
    )

    strategy.on_trading_iteration()

    equity_context = agent_manager._agents["equity_basket_agent"].calls[0]["context"]
    portfolio_context = agent_manager._agents["portfolio_decision_agent"].calls[0]["context"]
    assert equity_context["target_weight"] == 0.5
    assert equity_context["macro_allocation_report"]["mock"] is False
    assert portfolio_context["macro_allocation_report"]["regime"] == "growth_up_inflation_down"
    assert agent_manager._agents["execution_agent"].calls == []
```

- [ ] **Step 3: Run integration tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_inflation_quadrant.py::test_real_strategy_stops_when_macro_classifier_returns_blocked tests\test_ai_trading_team_growth_inflation_quadrant.py::test_real_strategy_passes_real_macro_report_to_basket_and_portfolio_agents -q
```

Expected: PASS.

- [ ] **Step 4: Run all real strategy tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_inflation_quadrant.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit workflow integration tests**

```powershell
git add lumibot\example_strategies\ai_trading_team_growth_inflation_quadrant.py tests\test_ai_trading_team_growth_inflation_quadrant.py
git commit -m "test: cover real macro quadrant workflow"
```

---

## Task 7: Register The Real Strategy In The Benchmark Runner

**Files:**

- Modify: `scripts/run_ai_trading_team_examples_benchmark.py`
- Modify: `tests/test_ai_trading_team_growth_inflation_quadrant.py`

- [ ] **Step 1: Add registry test**

Append:

```python
def test_examples_benchmark_exposes_real_growth_inflation_quadrant_strategy():
    benchmark = importlib.import_module("scripts.run_ai_trading_team_examples_benchmark")

    assert "growth-inflation-quadrant" in benchmark.STRATEGIES
    assert (
        benchmark.STRATEGIES["growth-inflation-quadrant"].__name__
        == "AITradingTeamGrowthInflationQuadrantStrategy"
    )
```

- [ ] **Step 2: Run registry test to verify failure**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_inflation_quadrant.py::test_examples_benchmark_exposes_real_growth_inflation_quadrant_strategy -q
```

Expected: FAIL because the registry does not include `growth-inflation-quadrant`.

- [ ] **Step 3: Add lazy strategy registry entry**

Modify the existing `STRATEGIES` lazy registry declaration in `scripts/run_ai_trading_team_examples_benchmark.py`.

Insert after the existing `mock-growth-inflation-quadrant` entry:

```python
        "growth-inflation-quadrant": (
            "lumibot.example_strategies.ai_trading_team_growth_inflation_quadrant",
            "AITradingTeamGrowthInflationQuadrantStrategy",
        ),
```

Keep the existing mock registry entry unchanged.

- [ ] **Step 4: Run registry and import tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_inflation_quadrant.py::test_examples_benchmark_exposes_real_growth_inflation_quadrant_strategy tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_examples_benchmark_exposes_mock_quadrant_strategy -q
```

Expected: PASS.

- [ ] **Step 5: Commit benchmark registry**

```powershell
git add scripts\run_ai_trading_team_examples_benchmark.py tests\test_ai_trading_team_growth_inflation_quadrant.py
git commit -m "feat: register real growth inflation quadrant benchmark"
```

---

## Task 8: Run Regression And Static Verification

**Files:**

- Check: `lumibot/example_strategies/fred_growth_inflation_regime_classifier.py`
- Check: `lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py`
- Check: `tests/test_ai_trading_team_growth_inflation_quadrant.py`
- Check: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
- Check: `tests/test_fred_macro.py`

- [ ] **Step 1: Run real strategy test module**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_inflation_quadrant.py -q
```

Expected: PASS.

- [ ] **Step 2: Run mock strategy regression tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: PASS. This proves Stage 2 did not break the mock workflow.

- [ ] **Step 3: Run FRED macro regression tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_fred_macro.py -q
```

Expected: PASS.

- [ ] **Step 4: Run ruff**

Run:

```powershell
.venv\Scripts\python.exe -m ruff check lumibot\example_strategies\fred_growth_inflation_regime_classifier.py lumibot\example_strategies\ai_trading_team_growth_inflation_quadrant.py tests\test_ai_trading_team_growth_inflation_quadrant.py scripts\run_ai_trading_team_examples_benchmark.py
```

Expected: PASS.

- [ ] **Step 5: Fix only reported lint or test failures**

If ruff or pytest fails, inspect the exact output and patch only the reported lines. Do not refactor the mock strategy, planner tool, execution tool, or agent framework during this task.

- [ ] **Step 6: Commit verification fixes if files changed**

If `git status --short` shows changes in the Stage 2 files after fixes:

```powershell
git add lumibot\example_strategies\fred_growth_inflation_regime_classifier.py lumibot\example_strategies\ai_trading_team_growth_inflation_quadrant.py tests\test_ai_trading_team_growth_inflation_quadrant.py scripts\run_ai_trading_team_examples_benchmark.py
git commit -m "chore: verify real macro classifier integration"
```

If no files changed, skip this commit.

---

## Task 9: Run Direct Real FRED Classifier Smoke

**Files:**

- Execute: `lumibot/example_strategies/fred_growth_inflation_regime_classifier.py`
- Output: terminal JSON summary only

- [ ] **Step 1: Confirm `FRED_API_KEY` is available without printing it**

Run:

```powershell
.venv\Scripts\python.exe -c "import os; raise SystemExit(0 if os.environ.get('FRED_API_KEY') else 2)"
```

Expected: exit code `0`.

If the exit code is `2`, load a local untracked secret source in the shell before continuing. Do not write or commit credentials.

- [ ] **Step 2: Run one direct classifier smoke**

Run:

```powershell
.venv\Scripts\python.exe -c "import json; from lumibot.macro import FREDMacroData; from lumibot.example_strategies.fred_growth_inflation_regime_classifier import classify_growth_inflation_regime; result = classify_growth_inflation_regime(fred=FREDMacroData(), date='2024-09-05'); print(json.dumps({k: result.get(k) for k in ('status','mock','mode','regime','growth_direction','inflation_direction','basket_weights','reason')}, indent=2, sort_keys=True))"
```

Expected:

- JSON prints to terminal.
- `status` is `passed`, `blocked`, or `failed`.
- If `status` is `passed`, `mock` is `false`, `regime` is one of the four supported regimes, and `basket_weights` sums to `1.0`.
- If `status` is `blocked` or `failed`, the printed `reason` explains the blocker without exposing an API key.

- [ ] **Step 3: If smoke is blocked or failed, stop before backtest**

If the direct smoke does not return `status = passed`, do not run the LLM backtest. Capture the printed JSON in the final response and explain the blocker.

- [ ] **Step 4: If smoke passes, commit no files**

This task should not create code changes. If `git status --short` changes because of cache or artifacts, verify those generated paths are ignored or leave them uncommitted.

---

## Task 10: Run One-Day Real Strategy Backtest

**Files:**

- Execute: `scripts/run_ai_trading_team_examples_benchmark.py`
- Output: a timestamped run directory under `artifacts/ai_trading_team_example_benchmarks`, containing a `growth_inflation_quadrant` strategy subdirectory.

- [ ] **Step 1: Confirm provider and FRED keys are available without printing them**

Run:

```powershell
.venv\Scripts\python.exe -c "import os; missing=[]; model=os.environ.get('AI_TRADING_TEAM_MODEL',''); missing.append('FRED_API_KEY') if not os.environ.get('FRED_API_KEY') else None; missing.append('OPENAI_API_KEY') if model.startswith('openai/') and not os.environ.get('OPENAI_API_KEY') else None; print('missing=' + ','.join(missing)); raise SystemExit(1 if missing else 0)"
```

Expected: `missing=` and exit code `0`.

- [ ] **Step 2: Run a one-day benchmark backtest**

Run:

```powershell
.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy growth-inflation-quadrant --start 2024-09-05 --end 2024-09-06 --max-workers 1 --agent-run-timeout-seconds 1800
```

Expected:

- The command prints a JSON result line for `growth-inflation-quadrant`.
- `status` is `passed`.
- The final summary JSON path is printed.
- A strategy artifact directory exists under the newest timestamped `artifacts\ai_trading_team_example_benchmarks` run directory.

- [ ] **Step 3: Inspect benchmark result**

Run:

```powershell
$summary = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Recurse -Filter summary.json |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $summary) { throw "No benchmark summary.json found." }
Get-Content $summary.FullName -Raw
```

Expected:

- The summary contains one result.
- The result strategy is `growth-inflation-quadrant`.
- `status` is `passed`.
- It contains artifact paths for stats/trades/settings/logfile when generated.

- [ ] **Step 4: Inspect replay UI if traces were generated**

Run:

```powershell
.venv\Scripts\python.exe scripts\agent_trace_ui.py
```

Expected:

- The UI can still list historical runs.
- The new strategy appears as `AITradingTeamGrowthInflationQuadrantStrategy` if a trace was generated.
- The macro allocation tool call shows `mock: false`.
- The macro allocation tool output includes `growth_evidence`, `inflation_evidence`, `data_quality`, and `basket_weights`.

- [ ] **Step 5: Do not commit generated artifacts**

Run:

```powershell
git status --short --branch
```

Expected:

- Code/test changes are committed.
- Generated artifacts are ignored or left uncommitted.
- Existing unrelated local files may remain.

---

## Task 11: Final Verification And Handoff

**Files:**

- Read: `docs/superpowers/specs/2026-08-11-real-fred-growth-inflation-regime-classifier-design.md`
- Check: all files created or modified by this plan

- [ ] **Step 1: Run full targeted verification command set**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py tests\test_fred_macro.py -q
.venv\Scripts\python.exe -m ruff check lumibot\example_strategies\fred_growth_inflation_regime_classifier.py lumibot\example_strategies\ai_trading_team_growth_inflation_quadrant.py tests\test_ai_trading_team_growth_inflation_quadrant.py scripts\run_ai_trading_team_examples_benchmark.py
```

Expected: both commands PASS.

- [ ] **Step 2: Compare implementation against spec**

Confirm:

- Mock quadrant strategy still exists and imports.
- Real quadrant strategy exists and imports.
- Real tool name is `macro_regime_classifier`.
- Real tool metadata has `mock = False`.
- Real tool uses `FREDMacroData`.
- Required series are `GDPC1` and `CPIAUCSL`.
- Growth lag default is 6 months.
- Inflation lag default is 1 month.
- Growth uses four-quarter year-over-year change.
- Inflation uses twelve-month year-over-year change.
- Trend comparison uses 20 quarterly metrics and 60 monthly metrics.
- Equality is classified as `down`.
- Basket weights match the 50/25/25/0 mapping.
- Tool output includes `growth_evidence`, `inflation_evidence`, `data_quality`, `confidence`, and `reason_brief`.
- The strategy stops before basket agents if macro status is not `passed`.
- Portfolio decision still delegates execution planning to `target_portfolio_to_execution_plan`.
- Execution still delegates to `execution_plan_execute`.
- Full raw FRED time series are not returned to the LLM.

- [ ] **Step 3: Check git status**

Run:

```powershell
git status --short --branch
```

Expected:

- Stage 2 code/test/registry changes are committed.
- No generated artifacts are staged.
- Only known unrelated local files may remain.

- [ ] **Step 4: Final response**

Report these concrete fields in the final response:

- Whether Stage 2 real FRED Growth / Inflation classifier implementation was completed.
- The exact pytest command that was run and whether it passed.
- The exact ruff command that was run and whether it passed.
- The direct FRED smoke result: `passed`, `blocked`, or `failed`, with the printed reason.
- The one-day backtest result: `passed`, `blocked`, or `failed`, with the artifact path if present.
- Important generated trace or report paths.
- Git status: branch ahead/behind and any unrelated dirty files.

If either direct FRED smoke or one-day backtest is blocked, do not claim Stage 2 trading integration is complete. Say exactly which lower-level deterministic pieces passed and what blocked the runtime validation.
