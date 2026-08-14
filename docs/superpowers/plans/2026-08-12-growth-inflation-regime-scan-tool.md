# Growth / Inflation Regime Scan Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a no-LLM CLI scanner that reuses the real FRED-backed Growth / Inflation classifier, records daily regimes, detects regime transitions, and suggests backtest windows.

**Architecture:** Add one focused script module, `scripts/scan_growth_inflation_regimes.py`, with small pure functions for date grids, classifier row flattening, transition detection, Markdown rendering, artifact writing, and CLI orchestration. Tests monkeypatch the classifier/FRED client so the default suite has no network dependency, then a final optional smoke command uses the user's real FRED key.

**Tech Stack:** Python standard library (`argparse`, `csv`, `json`, `datetime`, `pathlib`), existing `lumibot.example_strategies.fred_growth_inflation_regime_classifier`, existing `lumibot.macro.FREDMacroData`, existing `scripts.validate_fred_growth_inflation_data` env/redaction helpers, pytest, ruff.

---

## File Structure

Create:

- `scripts/scan_growth_inflation_regimes.py`
  - CLI entry point.
  - Date grid creation.
  - Classifier invocation loop.
  - Result flattening.
  - Transition detection.
  - CSV / JSON / Markdown artifact writing.
  - Real FRED client setup.

- `tests/test_growth_inflation_regime_scan.py`
  - Unit tests for date grids, row flattening, transition detection, artifact writing, CLI validation, and no-network scan behavior.

Do not modify:

- `lumibot/example_strategies/fred_growth_inflation_regime_classifier.py`
  - Scanner must reuse it; no classifier math changes in this feature.

- `lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py`
  - Scanner is research tooling, not strategy behavior.

- `scripts/run_ai_trading_team_examples_benchmark.py`
  - Scanner should not run backtests.

---

## Task 1: Add Failing Scanner Helper Tests

**Files:**

- Create: `tests/test_growth_inflation_regime_scan.py`
- Later create: `scripts/scan_growth_inflation_regimes.py`

- [ ] **Step 1: Create the test file with helper tests**

Add `tests/test_growth_inflation_regime_scan.py`:

```python
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from scripts import scan_growth_inflation_regimes as scan


def passed_result(
    regime: str,
    *,
    growth_direction: str = "up",
    inflation_direction: str = "down",
    reason_brief: str = "classifier reason",
) -> dict:
    return {
        "tool": "macro_regime_classifier",
        "status": "passed",
        "mock": False,
        "mode": "fred_ra_simple_lagged",
        "date": "2024-09-05",
        "as_of": "2024-09-05",
        "regime": regime,
        "growth_direction": growth_direction,
        "inflation_direction": inflation_direction,
        "basket_weights": {
            "equity": 0.50,
            "commodity": 0.25,
            "tips": 0.00,
            "nominal_bond": 0.25,
        },
        "growth_evidence": {
            "metric_value": 0.031,
            "trend_value": 0.026,
            "margin": 0.005,
            "latest_observation_date": "2024-01-01",
            "data_cutoff": "2024-03-05",
        },
        "inflation_evidence": {
            "metric_value": 0.021,
            "trend_value": 0.024,
            "margin": -0.003,
            "latest_observation_date": "2024-08-01",
            "data_cutoff": "2024-08-05",
        },
        "confidence": {"level": "medium"},
        "reason_brief": reason_brief,
    }


def blocked_result(reason: str = "missing_fred_api_key") -> dict:
    return {
        "tool": "macro_regime_classifier",
        "status": "blocked",
        "mock": False,
        "mode": "fred_ra_simple_lagged",
        "date": "2024-09-06",
        "as_of": "2024-09-06",
        "reason": reason,
        "data_quality": {
            "status": "blocked",
            "errors": ["FRED_API_KEY is required"],
            "warnings": [],
        },
    }


def test_parse_date_argument_accepts_iso_date():
    assert scan.parse_date_argument("2024-09-05", name="--start") == date(2024, 9, 5)


def test_parse_date_argument_rejects_bad_date():
    with pytest.raises(ValueError, match="invalid --start date"):
        scan.parse_date_argument("not-a-date", name="--start")


def test_build_date_grid_calendar_days_is_inclusive():
    dates, warnings = scan.build_date_grid(
        date(2024, 9, 5),
        date(2024, 9, 8),
        calendar_mode="calendar-days",
    )

    assert dates == [
        date(2024, 9, 5),
        date(2024, 9, 6),
        date(2024, 9, 7),
        date(2024, 9, 8),
    ]
    assert warnings == []


def test_build_date_grid_weekdays_skips_weekend():
    dates, warnings = scan.build_date_grid(
        date(2024, 9, 5),
        date(2024, 9, 9),
        calendar_mode="weekdays",
    )

    assert dates == [
        date(2024, 9, 5),
        date(2024, 9, 6),
        date(2024, 9, 9),
    ]
    assert warnings == []


def test_build_date_grid_trading_days_falls_back_to_weekdays(monkeypatch):
    def fail_import(name, *args, **kwargs):
        if name == "lumibot.tools":
            raise RuntimeError("calendar unavailable")
        return original_import(name, *args, **kwargs)

    original_import = __import__
    monkeypatch.setattr("builtins.__import__", fail_import)

    dates, warnings = scan.build_date_grid(
        date(2024, 9, 5),
        date(2024, 9, 9),
        calendar_mode="trading-days",
    )

    assert dates == [
        date(2024, 9, 5),
        date(2024, 9, 6),
        date(2024, 9, 9),
    ]
    assert len(warnings) == 1
    assert "fell back to weekdays" in warnings[0]


def test_build_date_grid_rejects_unknown_calendar_mode():
    with pytest.raises(ValueError, match="invalid calendar mode"):
        scan.build_date_grid(date(2024, 9, 5), date(2024, 9, 6), calendar_mode="moon-days")


def test_flatten_classifier_result_preserves_success_evidence():
    row = scan.flatten_classifier_result(
        scan_date=date(2024, 9, 5),
        result=passed_result("growth_up_inflation_down"),
        previous_passed_regime=None,
    )

    assert row["date"] == "2024-09-05"
    assert row["status"] == "passed"
    assert row["regime"] == "growth_up_inflation_down"
    assert row["growth_direction"] == "up"
    assert row["inflation_direction"] == "down"
    assert row["equity_weight"] == 0.5
    assert row["commodity_weight"] == 0.25
    assert row["tips_weight"] == 0.0
    assert row["nominal_bond_weight"] == 0.25
    assert row["previous_passed_regime"] == ""
    assert row["regime_changed"] is False
    assert row["growth_metric_value"] == 0.031
    assert row["growth_trend_value"] == 0.026
    assert row["growth_margin"] == 0.005
    assert row["growth_latest_observation_date"] == "2024-01-01"
    assert row["growth_data_cutoff"] == "2024-03-05"
    assert row["inflation_metric_value"] == 0.021
    assert row["inflation_trend_value"] == 0.024
    assert row["inflation_margin"] == -0.003
    assert row["inflation_latest_observation_date"] == "2024-08-01"
    assert row["inflation_data_cutoff"] == "2024-08-05"
    assert row["confidence_level"] == "medium"
    assert row["reason_brief"] == "classifier reason"
    assert row["error_reason"] == ""


def test_flatten_classifier_result_records_blocked_row():
    row = scan.flatten_classifier_result(
        scan_date=date(2024, 9, 6),
        result=blocked_result(),
        previous_passed_regime="growth_up_inflation_down",
    )

    assert row["date"] == "2024-09-06"
    assert row["status"] == "blocked"
    assert row["regime"] == ""
    assert row["previous_passed_regime"] == "growth_up_inflation_down"
    assert row["regime_changed"] is False
    assert row["reason_brief"] == ""
    assert row["error_reason"] == "missing_fred_api_key"
```

- [ ] **Step 2: Run tests to verify they fail because the script does not exist**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_growth_inflation_regime_scan.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'scripts.scan_growth_inflation_regimes'
```

- [ ] **Step 3: Commit the failing tests**

Run:

```powershell
git add tests\test_growth_inflation_regime_scan.py
git commit -m "test: define growth inflation scanner helpers"
```

---

## Task 2: Implement Date Grid And Result Flattening Helpers

**Files:**

- Create: `scripts/scan_growth_inflation_regimes.py`
- Modify: `tests/test_growth_inflation_regime_scan.py`

- [ ] **Step 1: Add the initial scanner module**

Create `scripts/scan_growth_inflation_regimes.py` with:

```python
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lumibot.example_strategies.fred_growth_inflation_regime_classifier import (  # noqa: E402
    DEFAULT_GROWTH_LAG_MONTHS,
    DEFAULT_GROWTH_SERIES_ID,
    DEFAULT_INFLATION_LAG_MONTHS,
    DEFAULT_INFLATION_SERIES_ID,
    DEFAULT_MODE,
    DEFAULT_TREND_YEARS,
    classify_growth_inflation_regime,
)
from lumibot.macro import FREDMacroData  # noqa: E402
from scripts.validate_fred_growth_inflation_data import (  # noqa: E402
    load_env_file,
    sanitize_sensitive_text,
)

ARTIFACT_ROOT = Path("artifacts") / "macro_regime_scans"
CALENDAR_MODES = {"trading-days", "weekdays", "calendar-days"}
DAILY_COLUMNS = [
    "date",
    "status",
    "regime",
    "growth_direction",
    "inflation_direction",
    "equity_weight",
    "commodity_weight",
    "tips_weight",
    "nominal_bond_weight",
    "previous_passed_regime",
    "regime_changed",
    "growth_metric_value",
    "growth_trend_value",
    "growth_margin",
    "growth_latest_observation_date",
    "growth_data_cutoff",
    "inflation_metric_value",
    "inflation_trend_value",
    "inflation_margin",
    "inflation_latest_observation_date",
    "inflation_data_cutoff",
    "confidence_level",
    "reason_brief",
    "error_reason",
]
TRANSITION_COLUMNS = [
    "transition_date",
    "previous_scan_date",
    "from_regime",
    "to_regime",
    "from_weights",
    "to_weights",
    "suggested_start_date",
    "suggested_end_date",
    "growth_margin",
    "inflation_margin",
    "confidence_level",
    "reason_brief",
]


def parse_date_argument(value: Any, *, name: str) -> date:
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"invalid {name} date: {value}") from exc


def _calendar_days(start: date, end: date) -> list[date]:
    if start > end:
        return []
    current = start
    days: list[date] = []
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _weekdays(start: date, end: date) -> list[date]:
    return [day for day in _calendar_days(start, end) if day.weekday() < 5]


def _trading_days(start: date, end: date) -> tuple[list[date], list[str]]:
    try:
        from lumibot.tools import get_trading_days

        schedule = get_trading_days(
            market="NYSE",
            start_date=start.isoformat(),
            end_date=(end + timedelta(days=1)).isoformat(),
        )
        days = [timestamp.date() for timestamp in schedule.index]
        return days, []
    except Exception as exc:
        message = (
            "trading-days calendar failed; fell back to weekdays: "
            f"{sanitize_sensitive_text(type(exc).__name__ + ': ' + str(exc))}"
        )
        return _weekdays(start, end), [message]


def build_date_grid(start: date, end: date, *, calendar_mode: str) -> tuple[list[date], list[str]]:
    if calendar_mode not in CALENDAR_MODES:
        raise ValueError(f"invalid calendar mode: {calendar_mode}")
    if start > end:
        raise ValueError("--start must be on or before --end")
    if calendar_mode == "calendar-days":
        return _calendar_days(start, end), []
    if calendar_mode == "weekdays":
        return _weekdays(start, end), []
    return _trading_days(start, end)


def _nested_get(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key, "")
    return current if current is not None else ""


def _weight(result: dict[str, Any], key: str) -> float | str:
    weights = result.get("basket_weights")
    if not isinstance(weights, dict):
        return ""
    value = weights.get(key, "")
    return value if value is not None else ""


def flatten_classifier_result(
    *,
    scan_date: date,
    result: dict[str, Any],
    previous_passed_regime: str | None,
) -> dict[str, Any]:
    status = str(result.get("status") or "failed")
    regime = str(result.get("regime") or "") if status == "passed" else ""
    reason = str(result.get("reason") or "") if status != "passed" else ""
    if not reason and status != "passed":
        errors = _nested_get(result, "data_quality", "errors")
        if isinstance(errors, list) and errors:
            reason = str(errors[0])
    return {
        "date": scan_date.isoformat(),
        "status": status,
        "regime": regime,
        "growth_direction": result.get("growth_direction", "") if status == "passed" else "",
        "inflation_direction": result.get("inflation_direction", "") if status == "passed" else "",
        "equity_weight": _weight(result, "equity") if status == "passed" else "",
        "commodity_weight": _weight(result, "commodity") if status == "passed" else "",
        "tips_weight": _weight(result, "tips") if status == "passed" else "",
        "nominal_bond_weight": _weight(result, "nominal_bond") if status == "passed" else "",
        "previous_passed_regime": previous_passed_regime or "",
        "regime_changed": bool(status == "passed" and previous_passed_regime and previous_passed_regime != regime),
        "growth_metric_value": _nested_get(result, "growth_evidence", "metric_value") if status == "passed" else "",
        "growth_trend_value": _nested_get(result, "growth_evidence", "trend_value") if status == "passed" else "",
        "growth_margin": _nested_get(result, "growth_evidence", "margin") if status == "passed" else "",
        "growth_latest_observation_date": _nested_get(
            result,
            "growth_evidence",
            "latest_observation_date",
        )
        if status == "passed"
        else "",
        "growth_data_cutoff": _nested_get(result, "growth_evidence", "data_cutoff")
        if status == "passed"
        else "",
        "inflation_metric_value": _nested_get(result, "inflation_evidence", "metric_value")
        if status == "passed"
        else "",
        "inflation_trend_value": _nested_get(result, "inflation_evidence", "trend_value")
        if status == "passed"
        else "",
        "inflation_margin": _nested_get(result, "inflation_evidence", "margin") if status == "passed" else "",
        "inflation_latest_observation_date": _nested_get(
            result,
            "inflation_evidence",
            "latest_observation_date",
        )
        if status == "passed"
        else "",
        "inflation_data_cutoff": _nested_get(result, "inflation_evidence", "data_cutoff")
        if status == "passed"
        else "",
        "confidence_level": _nested_get(result, "confidence", "level") if status == "passed" else "",
        "reason_brief": str(result.get("reason_brief") or "") if status == "passed" else "",
        "error_reason": sanitize_sensitive_text(reason),
    }
```

- [ ] **Step 2: Run helper tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_growth_inflation_regime_scan.py -q
```

Expected:

```text
9 passed
```

If the count differs because pytest collected more tests from later tasks, all collected tests must pass.

- [ ] **Step 3: Run ruff on the new script and test**

Run:

```powershell
.venv\Scripts\python.exe -m ruff check scripts\scan_growth_inflation_regimes.py tests\test_growth_inflation_regime_scan.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 4: Commit helper implementation**

Run:

```powershell
git add scripts\scan_growth_inflation_regimes.py tests\test_growth_inflation_regime_scan.py
git commit -m "feat: add regime scan helper functions"
```

---

## Task 3: Add Transition Detection And Scan Loop Tests

**Files:**

- Modify: `tests/test_growth_inflation_regime_scan.py`
- Modify: `scripts/scan_growth_inflation_regimes.py`

- [ ] **Step 1: Add failing tests for scan loop and transitions**

Append to `tests/test_growth_inflation_regime_scan.py`:

```python
class FakeFred:
    pass


def test_scan_regimes_calls_classifier_for_each_date_and_tracks_previous_regime():
    dates = [date(2024, 9, 5), date(2024, 9, 6), date(2024, 9, 9)]
    regimes = [
        "growth_up_inflation_down",
        "growth_up_inflation_down",
        "growth_down_inflation_up",
    ]
    calls = []

    def fake_classifier(fred, **kwargs):
        calls.append({"fred": fred, **kwargs})
        return passed_result(regimes[len(calls) - 1])

    rows = scan.scan_regimes(
        fred=FakeFred(),
        scan_dates=dates,
        classifier=fake_classifier,
        classifier_config={
            "mode": "fred_ra_simple_lagged",
            "growth_series_id": "GDPC1",
            "inflation_series_id": "CPIAUCSL",
            "growth_lag_months": 6,
            "inflation_lag_months": 1,
            "trend_years": 5,
        },
    )

    assert [call["date"] for call in calls] == ["2024-09-05", "2024-09-06", "2024-09-09"]
    assert calls[0]["previous_regime"] is None
    assert calls[1]["previous_regime"] == "growth_up_inflation_down"
    assert calls[2]["previous_regime"] == "growth_up_inflation_down"
    assert [row["regime_changed"] for row in rows] == [False, False, True]


def test_scan_regimes_preserves_blocked_rows_and_does_not_advance_previous_regime():
    dates = [date(2024, 9, 5), date(2024, 9, 6), date(2024, 9, 9)]
    results = [
        passed_result("growth_up_inflation_down"),
        blocked_result(),
        passed_result("growth_down_inflation_up"),
    ]

    def fake_classifier(_fred, **_kwargs):
        return results.pop(0)

    rows = scan.scan_regimes(
        fred=FakeFred(),
        scan_dates=dates,
        classifier=fake_classifier,
        classifier_config={},
    )

    assert [row["status"] for row in rows] == ["passed", "blocked", "passed"]
    assert rows[1]["previous_passed_regime"] == "growth_up_inflation_down"
    assert rows[1]["regime_changed"] is False
    assert rows[2]["previous_passed_regime"] == "growth_up_inflation_down"
    assert rows[2]["regime_changed"] is True


def test_scan_regimes_records_expected_classifier_exception_as_failed_row():
    def fake_classifier(_fred, **_kwargs):
        raise RuntimeError("network failed with api_key=secret")

    rows = scan.scan_regimes(
        fred=FakeFred(),
        scan_dates=[date(2024, 9, 5)],
        classifier=fake_classifier,
        classifier_config={},
    )

    assert rows[0]["status"] == "failed"
    assert rows[0]["error_reason"] == "RuntimeError: network failed with api_key=<redacted>"


def test_find_transitions_uses_passed_rows_and_clamps_window():
    rows = [
        scan.flatten_classifier_result(
            scan_date=date(2024, 9, 5),
            result=passed_result("growth_up_inflation_down"),
            previous_passed_regime=None,
        ),
        scan.flatten_classifier_result(
            scan_date=date(2024, 9, 6),
            result=blocked_result(),
            previous_passed_regime="growth_up_inflation_down",
        ),
        scan.flatten_classifier_result(
            scan_date=date(2024, 9, 9),
            result=passed_result("growth_down_inflation_up"),
            previous_passed_regime="growth_up_inflation_down",
        ),
    ]

    transitions = scan.find_transitions(rows, window_before=5, window_after=5)

    assert transitions == [
        {
            "transition_date": "2024-09-09",
            "previous_scan_date": "2024-09-05",
            "from_regime": "growth_up_inflation_down",
            "to_regime": "growth_down_inflation_up",
            "from_weights": json.dumps(
                {"equity": 0.5, "commodity": 0.25, "tips": 0.0, "nominal_bond": 0.25},
                sort_keys=True,
                separators=(",", ":"),
            ),
            "to_weights": json.dumps(
                {"equity": 0.5, "commodity": 0.25, "tips": 0.0, "nominal_bond": 0.25},
                sort_keys=True,
                separators=(",", ":"),
            ),
            "suggested_start_date": "2024-09-05",
            "suggested_end_date": "2024-09-09",
            "growth_margin": 0.005,
            "inflation_margin": -0.003,
            "confidence_level": "medium",
            "reason_brief": "classifier reason",
        }
    ]


def test_find_transitions_returns_empty_list_when_no_passed_regime_change():
    rows = [
        scan.flatten_classifier_result(
            scan_date=date(2024, 9, 5),
            result=passed_result("growth_up_inflation_down"),
            previous_passed_regime=None,
        ),
        scan.flatten_classifier_result(
            scan_date=date(2024, 9, 6),
            result=passed_result("growth_up_inflation_down"),
            previous_passed_regime="growth_up_inflation_down",
        ),
    ]

    assert scan.find_transitions(rows, window_before=1, window_after=1) == []
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_growth_inflation_regime_scan.py -q
```

Expected:

```text
AttributeError: module 'scripts.scan_growth_inflation_regimes' has no attribute 'scan_regimes'
```

- [ ] **Step 3: Implement scan loop and transition detection**

Append these functions to `scripts/scan_growth_inflation_regimes.py`:

```python
def _failed_row_for_exception(scan_date: date, exc: Exception, previous_passed_regime: str | None) -> dict[str, Any]:
    result = {
        "status": "failed",
        "reason": sanitize_sensitive_text(f"{type(exc).__name__}: {exc}"),
    }
    return flatten_classifier_result(
        scan_date=scan_date,
        result=result,
        previous_passed_regime=previous_passed_regime,
    )


def scan_regimes(
    *,
    fred: Any,
    scan_dates: list[date],
    classifier: Callable[..., dict[str, Any]] = classify_growth_inflation_regime,
    classifier_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    config = dict(classifier_config or {})
    rows: list[dict[str, Any]] = []
    previous_passed_regime: str | None = None
    for scan_date in scan_dates:
        try:
            result = classifier(
                fred,
                date=scan_date.isoformat(),
                previous_regime=previous_passed_regime,
                **config,
            )
            row = flatten_classifier_result(
                scan_date=scan_date,
                result=result,
                previous_passed_regime=previous_passed_regime,
            )
        except Exception as exc:
            row = _failed_row_for_exception(scan_date, exc, previous_passed_regime)
        rows.append(row)
        if row["status"] == "passed" and row["regime"]:
            previous_passed_regime = str(row["regime"])
    return rows


def _weights_from_row(row: dict[str, Any]) -> dict[str, float]:
    weights: dict[str, float] = {}
    for basket, column in (
        ("equity", "equity_weight"),
        ("commodity", "commodity_weight"),
        ("tips", "tips_weight"),
        ("nominal_bond", "nominal_bond_weight"),
    ):
        value = row.get(column)
        weights[basket] = float(value) if value != "" else 0.0
    return weights


def _compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def find_transitions(
    rows: list[dict[str, Any]],
    *,
    window_before: int,
    window_after: int,
) -> list[dict[str, Any]]:
    passed_rows = [row for row in rows if row.get("status") == "passed" and row.get("regime")]
    if len(passed_rows) < 2:
        return []

    date_to_index = {row["date"]: index for index, row in enumerate(rows)}
    transitions: list[dict[str, Any]] = []
    previous_passed = passed_rows[0]
    for current in passed_rows[1:]:
        if current["regime"] == previous_passed["regime"]:
            previous_passed = current
            continue

        current_index = date_to_index[current["date"]]
        start_index = max(0, current_index - int(window_before))
        end_index = min(len(rows) - 1, current_index + int(window_after))
        transitions.append(
            {
                "transition_date": current["date"],
                "previous_scan_date": previous_passed["date"],
                "from_regime": previous_passed["regime"],
                "to_regime": current["regime"],
                "from_weights": _compact_json(_weights_from_row(previous_passed)),
                "to_weights": _compact_json(_weights_from_row(current)),
                "suggested_start_date": rows[start_index]["date"],
                "suggested_end_date": rows[end_index]["date"],
                "growth_margin": current["growth_margin"],
                "inflation_margin": current["inflation_margin"],
                "confidence_level": current["confidence_level"],
                "reason_brief": current["reason_brief"],
            }
        )
        previous_passed = current
    return transitions
```

- [ ] **Step 4: Run transition tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_growth_inflation_regime_scan.py -q
```

Expected:

```text
14 passed
```

- [ ] **Step 5: Run ruff**

Run:

```powershell
.venv\Scripts\python.exe -m ruff check scripts\scan_growth_inflation_regimes.py tests\test_growth_inflation_regime_scan.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 6: Commit scan loop and transition detection**

Run:

```powershell
git add scripts\scan_growth_inflation_regimes.py tests\test_growth_inflation_regime_scan.py
git commit -m "feat: scan macro regimes and detect transitions"
```

---

## Task 4: Add Artifact Writing And Markdown Summary

**Files:**

- Modify: `tests/test_growth_inflation_regime_scan.py`
- Modify: `scripts/scan_growth_inflation_regimes.py`

- [ ] **Step 1: Add failing tests for output files**

Append to `tests/test_growth_inflation_regime_scan.py`:

```python
def test_build_scan_report_counts_statuses_regimes_and_transitions():
    rows = [
        scan.flatten_classifier_result(
            scan_date=date(2024, 9, 5),
            result=passed_result("growth_up_inflation_down"),
            previous_passed_regime=None,
        ),
        scan.flatten_classifier_result(
            scan_date=date(2024, 9, 6),
            result=blocked_result(),
            previous_passed_regime="growth_up_inflation_down",
        ),
        scan.flatten_classifier_result(
            scan_date=date(2024, 9, 9),
            result=passed_result("growth_down_inflation_up"),
            previous_passed_regime="growth_up_inflation_down",
        ),
    ]
    transitions = scan.find_transitions(rows, window_before=1, window_after=1)

    report = scan.build_scan_report(
        rows=rows,
        transitions=transitions,
        args_summary={
            "start": "2024-09-05",
            "end": "2024-09-09",
            "calendar": "weekdays",
        },
        warnings=["calendar warning"],
        artifact_dir=Path("artifacts/macro_regime_scans/test-run"),
    )

    assert report["status"] == "passed"
    assert report["row_count"] == 3
    assert report["status_counts"] == {"blocked": 1, "passed": 2}
    assert report["regime_counts"] == {
        "growth_down_inflation_up": 1,
        "growth_up_inflation_down": 1,
    }
    assert report["transition_count"] == 1
    assert report["first_passed_regime"] == "growth_up_inflation_down"
    assert report["last_passed_regime"] == "growth_down_inflation_up"
    assert report["warnings"] == ["calendar warning"]


def test_write_scan_artifacts_creates_csv_json_and_markdown(tmp_path):
    rows = [
        scan.flatten_classifier_result(
            scan_date=date(2024, 9, 5),
            result=passed_result("growth_up_inflation_down"),
            previous_passed_regime=None,
        ),
        scan.flatten_classifier_result(
            scan_date=date(2024, 9, 6),
            result=passed_result("growth_down_inflation_up"),
            previous_passed_regime="growth_up_inflation_down",
        ),
    ]
    transitions = scan.find_transitions(rows, window_before=1, window_after=1)
    report = scan.build_scan_report(
        rows=rows,
        transitions=transitions,
        args_summary={
            "start": "2024-09-05",
            "end": "2024-09-06",
            "calendar": "weekdays",
            "window_before": 1,
            "window_after": 1,
        },
        warnings=[],
        artifact_dir=tmp_path,
    )

    paths = scan.write_scan_artifacts(
        rows=rows,
        transitions=transitions,
        report=report,
        artifact_dir=tmp_path,
    )

    assert paths["daily_csv"].name == "daily_regimes.csv"
    assert paths["transitions_csv"].name == "regime_transitions.csv"
    assert paths["summary_md"].name == "summary.md"
    assert paths["metadata_json"].name == "scan_metadata.json"
    assert paths["daily_csv"].exists()
    assert paths["transitions_csv"].exists()
    assert paths["summary_md"].exists()
    assert paths["metadata_json"].exists()

    daily_rows = list(csv.DictReader(paths["daily_csv"].open(newline="", encoding="utf-8")))
    transition_rows = list(csv.DictReader(paths["transitions_csv"].open(newline="", encoding="utf-8")))
    metadata = json.loads(paths["metadata_json"].read_text(encoding="utf-8"))
    markdown = paths["summary_md"].read_text(encoding="utf-8")

    assert daily_rows[0]["date"] == "2024-09-05"
    assert daily_rows[1]["regime_changed"] == "True"
    assert transition_rows[0]["transition_date"] == "2024-09-06"
    assert metadata["transition_count"] == 1
    assert "# Growth / Inflation Regime Scan Report" in markdown
    assert "growth_up_inflation_down -> growth_down_inflation_up" in markdown
    assert "daily_regimes.csv" in markdown
    assert "sk-" not in markdown


def test_render_summary_handles_all_failed_scan():
    report = scan.build_scan_report(
        rows=[
            scan.flatten_classifier_result(
                scan_date=date(2024, 9, 5),
                result={"status": "failed", "reason": "data unavailable"},
                previous_passed_regime=None,
            )
        ],
        transitions=[],
        args_summary={"start": "2024-09-05", "end": "2024-09-05", "calendar": "weekdays"},
        warnings=[],
        artifact_dir=Path("artifacts/macro_regime_scans/all-failed"),
    )

    markdown = scan.render_summary_markdown(report)

    assert report["status"] == "failed"
    assert "No passed regime rows were available." in markdown
    assert "No regime transitions were detected." in markdown
```

- [ ] **Step 2: Run tests to verify output tests fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_growth_inflation_regime_scan.py -q
```

Expected:

```text
AttributeError: module 'scripts.scan_growth_inflation_regimes' has no attribute 'build_scan_report'
```

- [ ] **Step 3: Implement report and artifact writing**

Append to `scripts/scan_growth_inflation_regimes.py`:

```python
def build_scan_report(
    *,
    rows: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    args_summary: dict[str, Any],
    warnings: list[str],
    artifact_dir: Path,
) -> dict[str, Any]:
    status_counts = dict(sorted(Counter(row["status"] for row in rows).items()))
    passed_rows = [row for row in rows if row.get("status") == "passed" and row.get("regime")]
    regime_counts = dict(sorted(Counter(row["regime"] for row in passed_rows).items()))
    if not rows:
        status = "failed"
    elif passed_rows:
        status = "passed"
    elif any(row.get("status") == "blocked" for row in rows):
        status = "blocked"
    else:
        status = "failed"
    return {
        "schema_version": 1,
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "arguments": args_summary,
        "artifact_dir": str(artifact_dir),
        "row_count": len(rows),
        "status_counts": status_counts,
        "regime_counts": regime_counts,
        "transition_count": len(transitions),
        "first_passed_regime": passed_rows[0]["regime"] if passed_rows else None,
        "last_passed_regime": passed_rows[-1]["regime"] if passed_rows else None,
        "warnings": [sanitize_sensitive_text(warning) for warning in warnings],
        "transitions": transitions,
        "artifact_paths": {
            "daily_csv": str(artifact_dir / "daily_regimes.csv"),
            "transitions_csv": str(artifact_dir / "regime_transitions.csv"),
            "summary_md": str(artifact_dir / "summary.md"),
            "metadata_json": str(artifact_dir / "scan_metadata.json"),
        },
    }


def render_summary_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Growth / Inflation Regime Scan Report",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Artifact directory: `{report.get('artifact_dir')}`",
        f"- Scanned dates: `{report.get('row_count')}`",
        f"- Status counts: `{report.get('status_counts')}`",
        f"- Regime counts: `{report.get('regime_counts')}`",
        f"- Transition count: `{report.get('transition_count')}`",
        f"- First passed regime: `{report.get('first_passed_regime')}`",
        f"- Last passed regime: `{report.get('last_passed_regime')}`",
        "",
        "## Arguments",
        "",
    ]
    for key, value in sorted((report.get("arguments") or {}).items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    if report.get("warnings"):
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report["warnings"])
        lines.append("")
    lines.extend(["## Regime Transitions", ""])
    transitions = list(report.get("transitions") or [])
    if transitions:
        lines.extend(
            [
                "| Transition Date | From | To | Suggested Window | Confidence |",
                "|---|---|---|---|---|",
            ]
        )
        for item in transitions:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(item.get("transition_date")),
                        str(item.get("from_regime")),
                        str(item.get("to_regime")),
                        f"{item.get('suggested_start_date')} to {item.get('suggested_end_date')}",
                        str(item.get("confidence_level")),
                    ]
                )
                + " |"
            )
            lines.append(
                f"\n{item.get('from_regime')} -> {item.get('to_regime')}: "
                f"{item.get('reason_brief')}"
            )
        lines.append("")
    else:
        lines.extend(["No regime transitions were detected.", ""])
    if report.get("first_passed_regime") is None:
        lines.extend(["No passed regime rows were available.", ""])
    lines.extend(
        [
            "## Artifact Files",
            "",
            f"- `daily_regimes.csv`: `{_nested_get(report, 'artifact_paths', 'daily_csv')}`",
            f"- `regime_transitions.csv`: `{_nested_get(report, 'artifact_paths', 'transitions_csv')}`",
            f"- `summary.md`: `{_nested_get(report, 'artifact_paths', 'summary_md')}`",
            f"- `scan_metadata.json`: `{_nested_get(report, 'artifact_paths', 'metadata_json')}`",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_scan_artifacts(
    *,
    rows: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    report: dict[str, Any],
    artifact_dir: Path,
) -> dict[str, Path]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    daily_csv = artifact_dir / "daily_regimes.csv"
    transitions_csv = artifact_dir / "regime_transitions.csv"
    summary_md = artifact_dir / "summary.md"
    metadata_json = artifact_dir / "scan_metadata.json"
    _write_csv(daily_csv, rows, DAILY_COLUMNS)
    _write_csv(transitions_csv, transitions, TRANSITION_COLUMNS)
    metadata_json.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    summary_md.write_text(render_summary_markdown(report), encoding="utf-8")
    return {
        "daily_csv": daily_csv,
        "transitions_csv": transitions_csv,
        "summary_md": summary_md,
        "metadata_json": metadata_json,
    }
```

- [ ] **Step 4: Run output tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_growth_inflation_regime_scan.py -q
```

Expected:

```text
17 passed
```

- [ ] **Step 5: Run ruff**

Run:

```powershell
.venv\Scripts\python.exe -m ruff check scripts\scan_growth_inflation_regimes.py tests\test_growth_inflation_regime_scan.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 6: Commit artifact writing**

Run:

```powershell
git add scripts\scan_growth_inflation_regimes.py tests\test_growth_inflation_regime_scan.py
git commit -m "feat: write macro regime scan artifacts"
```

---

## Task 5: Add CLI Orchestration Tests And Implementation

**Files:**

- Modify: `tests/test_growth_inflation_regime_scan.py`
- Modify: `scripts/scan_growth_inflation_regimes.py`

- [ ] **Step 1: Add failing CLI tests**

Append to `tests/test_growth_inflation_regime_scan.py`:

```python
def test_parse_args_accepts_scanner_options():
    args = scan.parse_args(
        [
            "--start",
            "2024-09-01",
            "--end",
            "2024-10-15",
            "--calendar",
            "weekdays",
            "--window-before",
            "3",
            "--window-after",
            "4",
            "--output-dir",
            "artifacts/custom",
            "--run-id",
            "unit-run",
            "--env-file",
            "project_notes/API.txt",
            "--growth-lag-months",
            "6",
            "--inflation-lag-months",
            "1",
            "--trend-years",
            "5",
        ]
    )

    assert args.start == "2024-09-01"
    assert args.end == "2024-10-15"
    assert args.calendar == "weekdays"
    assert args.window_before == 3
    assert args.window_after == 4
    assert args.output_dir == Path("artifacts/custom")
    assert args.run_id == "unit-run"
    assert args.env_file == Path("project_notes/API.txt")
    assert args.growth_lag_months == 6
    assert args.inflation_lag_months == 1
    assert args.trend_years == 5


def test_main_writes_failed_report_for_invalid_date(tmp_path, capsys):
    exit_code = scan.main(
        [
            "--start",
            "bad-date",
            "--end",
            "2024-09-05",
            "--output-dir",
            str(tmp_path),
            "--run-id",
            "bad-date-run",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    summary = (tmp_path / "bad-date-run" / "summary.md").read_text(encoding="utf-8")

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["artifact_dir"] == str(tmp_path / "bad-date-run")
    assert "invalid --start date" in summary


def test_main_runs_scan_with_monkeypatched_classifier_and_fred(tmp_path, monkeypatch, capsys):
    class FakeFredClient:
        def __init__(self, *, cache_dir=None):
            self.cache_dir = cache_dir

    results = [
        passed_result("growth_up_inflation_down"),
        passed_result("growth_down_inflation_up"),
    ]

    def fake_classifier(_fred, **_kwargs):
        return results.pop(0)

    monkeypatch.setattr(scan, "FREDMacroData", FakeFredClient)
    monkeypatch.setattr(scan, "classify_growth_inflation_regime", fake_classifier)

    exit_code = scan.main(
        [
            "--start",
            "2024-09-05",
            "--end",
            "2024-09-06",
            "--calendar",
            "weekdays",
            "--output-dir",
            str(tmp_path),
            "--run-id",
            "unit-scan",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    artifact_dir = tmp_path / "unit-scan"

    assert exit_code == 0
    assert payload["status"] == "passed"
    assert payload["artifact_dir"] == str(artifact_dir)
    assert (artifact_dir / "daily_regimes.csv").exists()
    assert (artifact_dir / "regime_transitions.csv").exists()
    assert (artifact_dir / "summary.md").exists()
    assert (artifact_dir / "scan_metadata.json").exists()
```

- [ ] **Step 2: Run CLI tests to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_growth_inflation_regime_scan.py -q
```

Expected:

```text
AttributeError: module 'scripts.scan_growth_inflation_regimes' has no attribute 'parse_args'
```

- [ ] **Step 3: Implement CLI functions**

Append to `scripts/scan_growth_inflation_regimes.py`:

```python
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan FRED Growth / Inflation regimes over a date range.")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--calendar", choices=sorted(CALENDAR_MODES), default="trading-days")
    parser.add_argument("--window-before", type=int, default=5)
    parser.add_argument("--window-after", type=int, default=5)
    parser.add_argument("--output-dir", type=Path, default=ARTIFACT_ROOT)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--mode", default=DEFAULT_MODE)
    parser.add_argument("--growth-series-id", default=DEFAULT_GROWTH_SERIES_ID)
    parser.add_argument("--inflation-series-id", default=DEFAULT_INFLATION_SERIES_ID)
    parser.add_argument("--growth-lag-months", type=int, default=DEFAULT_GROWTH_LAG_MONTHS)
    parser.add_argument("--inflation-lag-months", type=int, default=DEFAULT_INFLATION_LAG_MONTHS)
    parser.add_argument("--trend-years", type=int, default=DEFAULT_TREND_YEARS)
    return parser.parse_args(argv)


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _args_summary(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "start": args.start,
        "end": args.end,
        "calendar": args.calendar,
        "window_before": args.window_before,
        "window_after": args.window_after,
        "mode": args.mode,
        "growth_series_id": args.growth_series_id,
        "inflation_series_id": args.inflation_series_id,
        "growth_lag_months": args.growth_lag_months,
        "inflation_lag_months": args.inflation_lag_months,
        "trend_years": args.trend_years,
    }


def _classifier_config(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "mode": args.mode,
        "growth_series_id": args.growth_series_id,
        "inflation_series_id": args.inflation_series_id,
        "growth_lag_months": args.growth_lag_months,
        "inflation_lag_months": args.inflation_lag_months,
        "trend_years": args.trend_years,
    }


def _failure_artifacts(
    *,
    artifact_dir: Path,
    args_summary: dict[str, Any],
    reason: str,
    status: str = "failed",
) -> dict[str, Path]:
    row = {column: "" for column in DAILY_COLUMNS}
    row["status"] = status
    row["error_reason"] = sanitize_sensitive_text(reason)
    report = build_scan_report(
        rows=[row],
        transitions=[],
        args_summary=args_summary,
        warnings=[sanitize_sensitive_text(reason)],
        artifact_dir=artifact_dir,
    )
    report["status"] = status
    return write_scan_artifacts(rows=[row], transitions=[], report=report, artifact_dir=artifact_dir)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_id = args.run_id or _run_id()
    artifact_dir = args.output_dir / run_id
    args_summary = _args_summary(args)
    if args.env_file is not None:
        load_env_file(args.env_file)
    os.environ["LUMIBOT_FRED_CACHE_DIR"] = str(artifact_dir / "fred_cache")

    try:
        start = parse_date_argument(args.start, name="--start")
        end = parse_date_argument(args.end, name="--end")
        scan_dates, warnings = build_date_grid(start, end, calendar_mode=args.calendar)
        if not scan_dates:
            raise ValueError("date grid is empty")
    except ValueError as exc:
        paths = _failure_artifacts(
            artifact_dir=artifact_dir,
            args_summary=args_summary,
            reason=str(exc),
            status="failed",
        )
        print(
            json.dumps(
                {
                    "status": "failed",
                    "artifact_dir": str(artifact_dir),
                    "summary": str(paths["summary_md"]),
                    "metadata": str(paths["metadata_json"]),
                },
                sort_keys=True,
            )
        )
        return 1

    fred = FREDMacroData(cache_dir=artifact_dir / "fred_cache")
    rows = scan_regimes(
        fred=fred,
        scan_dates=scan_dates,
        classifier=classify_growth_inflation_regime,
        classifier_config=_classifier_config(args),
    )
    transitions = find_transitions(rows, window_before=args.window_before, window_after=args.window_after)
    report = build_scan_report(
        rows=rows,
        transitions=transitions,
        args_summary=args_summary,
        warnings=warnings,
        artifact_dir=artifact_dir,
    )
    paths = write_scan_artifacts(rows=rows, transitions=transitions, report=report, artifact_dir=artifact_dir)
    print(
        json.dumps(
            {
                "status": report["status"],
                "artifact_dir": str(artifact_dir),
                "daily_csv": str(paths["daily_csv"]),
                "transitions_csv": str(paths["transitions_csv"]),
                "summary": str(paths["summary_md"]),
                "metadata": str(paths["metadata_json"]),
                "transition_count": report["transition_count"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run CLI tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_growth_inflation_regime_scan.py -q
```

Expected:

```text
20 passed
```

- [ ] **Step 5: Run focused existing classifier tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_inflation_quadrant.py tests\test_fred_growth_inflation_data_availability.py -q
```

Expected:

```text
all tests passed
```

- [ ] **Step 6: Run ruff**

Run:

```powershell
.venv\Scripts\python.exe -m ruff check scripts\scan_growth_inflation_regimes.py tests\test_growth_inflation_regime_scan.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 7: Commit CLI orchestration**

Run:

```powershell
git add scripts\scan_growth_inflation_regimes.py tests\test_growth_inflation_regime_scan.py
git commit -m "feat: add growth inflation regime scan cli"
```

---

## Task 6: Real FRED Smoke Scan

**Files:**

- No code changes expected.
- Read generated files under `artifacts/macro_regime_scans/smoke-growth-inflation-scan/`.

- [ ] **Step 1: Run unit verification before the paid/network smoke**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_growth_inflation_regime_scan.py tests\test_ai_trading_team_growth_inflation_quadrant.py tests\test_fred_growth_inflation_data_availability.py -q
```

Expected:

```text
all tests passed
```

- [ ] **Step 2: Run ruff verification**

Run:

```powershell
.venv\Scripts\python.exe -m ruff check scripts\scan_growth_inflation_regimes.py tests\test_growth_inflation_regime_scan.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 3: Run a short real FRED smoke scan**

Use the user's local API file if available:

```powershell
.venv\Scripts\python.exe scripts\scan_growth_inflation_regimes.py --start 2024-09-01 --end 2024-10-15 --calendar weekdays --run-id smoke-growth-inflation-scan --env-file project_notes\API.txt
```

Expected stdout shape:

```json
{
  "artifact_dir": "artifacts\\macro_regime_scans\\smoke-growth-inflation-scan",
  "daily_csv": "artifacts\\macro_regime_scans\\smoke-growth-inflation-scan\\daily_regimes.csv",
  "metadata": "artifacts\\macro_regime_scans\\smoke-growth-inflation-scan\\scan_metadata.json",
  "status": "passed",
  "summary": "artifacts\\macro_regime_scans\\smoke-growth-inflation-scan\\summary.md",
  "transition_count": 0,
  "transitions_csv": "artifacts\\macro_regime_scans\\smoke-growth-inflation-scan\\regime_transitions.csv"
}
```

`transition_count` may be any nonnegative integer. The smoke scan only proves the scanner can run with real FRED data and produce readable artifacts.

- [ ] **Step 4: Inspect generated artifacts**

Run:

```powershell
Get-Content artifacts\macro_regime_scans\smoke-growth-inflation-scan\summary.md
Import-Csv artifacts\macro_regime_scans\smoke-growth-inflation-scan\daily_regimes.csv | Select-Object -First 5
Import-Csv artifacts\macro_regime_scans\smoke-growth-inflation-scan\regime_transitions.csv | Select-Object -First 5
```

Expected:

- `summary.md` contains `# Growth / Inflation Regime Scan Report`.
- `daily_regimes.csv` has one row per weekday scan date.
- No API key appears in any output.
- If no transitions exist in the short window, summary says no regime transitions were detected.

- [ ] **Step 5: Do not commit generated artifacts unless explicitly requested**

Run:

```powershell
git status --short
```

Expected:

```text
no tracked source changes from generated artifacts
```

If generated artifacts appear as untracked files and are not ignored, leave them uncommitted and mention them in the task handoff.

---

## Task 7: Final Regression And Branch Handoff

**Files:**

- Possibly none.
- If final smoke reveals a small bug, fix only scanner-related files.

- [ ] **Step 1: Run final focused test suite**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_growth_inflation_regime_scan.py tests\test_ai_trading_team_growth_inflation_quadrant.py tests\test_fred_growth_inflation_data_availability.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected:

```text
all tests passed
```

- [ ] **Step 2: Run final ruff check**

Run:

```powershell
.venv\Scripts\python.exe -m ruff check scripts\scan_growth_inflation_regimes.py tests\test_growth_inflation_regime_scan.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 3: Confirm git status**

Run:

```powershell
git status --short --branch
```

Expected:

```text
## feature/real-fred-growth-inflation-regime
```

or only ignored/untracked generated artifacts under `artifacts/`.

- [ ] **Step 4: If code changed after the last commit, commit final fixes**

Only if `git status --short` shows tracked source/test changes:

```powershell
git add scripts\scan_growth_inflation_regimes.py tests\test_growth_inflation_regime_scan.py
git commit -m "fix: harden growth inflation regime scanner"
```

- [ ] **Step 5: Report implementation outcome**

In the final handoff, include:

- scanner command
- generated artifact directory from real smoke scan
- whether any transitions were found in the smoke window
- test commands run
- ruff result
- any uncommitted generated artifacts

---

## Acceptance Checklist

- [ ] `scripts/scan_growth_inflation_regimes.py` exists.
- [ ] The scanner imports and calls `classify_growth_inflation_regime`.
- [ ] The scanner does not duplicate GDP/CPI classifier math.
- [ ] The scanner makes no LLM, ADK, LiteLLM, broker, order, or backtest calls.
- [ ] `daily_regimes.csv` is written.
- [ ] `regime_transitions.csv` is written.
- [ ] `summary.md` is written.
- [ ] `scan_metadata.json` is written.
- [ ] Failed and blocked rows remain visible in `daily_regimes.csv`.
- [ ] Transitions are detected only between passed rows.
- [ ] Suggested windows are clamped to the scanned date grid.
- [ ] Unit tests run without network access.
- [ ] A short real FRED smoke scan works when `FRED_API_KEY` is available.
