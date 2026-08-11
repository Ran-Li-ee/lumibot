# FRED Growth / Inflation Data Availability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic Stage 1 validator that proves whether Lumibot can retrieve point-in-time FRED `GDPC1` and `CPIAUCSL` data for current and historical `as_of` dates.

**Architecture:** Add one standalone validation script under `scripts/` that calls `lumibot.macro.FREDMacroData` directly, evaluates each required series/date pair, and writes JSON plus Markdown reports under a timestamped directory inside `artifacts/macro_regime_data_availability/`. Add one focused test module with mocked FRED responses so the validator can be developed without network access.

**Tech Stack:** Python standard library, existing `lumibot.macro.FREDMacroData`, pytest, ruff.

---

## File Structure

Create:

- `scripts/validate_fred_growth_inflation_data.py`
  - CLI entry point.
  - Environment-file loading.
  - Required and optional FRED series definitions.
  - Series/date validation.
  - JSON and Markdown report writing.

- `tests/test_fred_growth_inflation_data_availability.py`
  - Unit tests for the validator.
  - Mocked `FREDMacroData` style payloads.
  - No real network calls.
  - No real API keys.

Do not modify:

- `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- `lumibot/components/agents/*`
- Any prompts
- Any trading strategy behavior

---

## Task 1: Add Report Analysis Tests First

**Files:**

- Create: `tests/test_fred_growth_inflation_data_availability.py`
- Later implement: `scripts/validate_fred_growth_inflation_data.py`

- [ ] **Step 1: Write the first failing tests**

Create `tests/test_fred_growth_inflation_data_availability.py` with:

```python
from __future__ import annotations

import json
import os
from datetime import date

from scripts.validate_fred_growth_inflation_data import (
    REQUIRED_SERIES,
    OPTIONAL_SERIES,
    SeriesCheckConfig,
    analyze_series_payload,
    build_as_of_result,
    build_top_level_status,
)


def _observations(start_year: int, count: int, *, realtime: str = "2024-09-05"):
    rows = []
    year = start_year
    month = 1
    for index in range(count):
        rows.append(
            {
                "date": f"{year:04d}-{month:02d}-01",
                "value": str(100 + index),
                "realtime_start": realtime,
                "realtime_end": realtime,
            }
        )
        month += 1
        if month == 13:
            month = 1
            year += 1
    return rows


def test_required_series_constants_are_small_and_explicit():
    assert REQUIRED_SERIES == {
        "GDPC1": SeriesCheckConfig(
            series_id="GDPC1",
            label="Real Gross Domestic Product",
            minimum_non_null_observations=24,
            required=True,
        ),
        "CPIAUCSL": SeriesCheckConfig(
            series_id="CPIAUCSL",
            label="Consumer Price Index for All Urban Consumers",
            minimum_non_null_observations=72,
            required=True,
        ),
    }
    assert set(OPTIONAL_SERIES) == {"PCEPI", "GDP"}


def test_analyze_series_payload_passes_when_required_data_is_safe_and_deep():
    payload = {
        "source": "fred_api",
        "series_id": "CPIAUCSL",
        "as_of": "2024-09-05",
        "point_in_time_safe": True,
        "uses_revised_data": False,
        "observations": _observations(2018, 80, realtime="2024-09-05"),
    }

    result = analyze_series_payload(
        payload,
        config=SeriesCheckConfig(
            series_id="CPIAUCSL",
            label="Consumer Price Index for All Urban Consumers",
            minimum_non_null_observations=72,
            required=True,
        ),
        as_of=date(2024, 9, 5),
    )

    assert result["status"] == "passed"
    assert result["available"] is True
    assert result["source"] == "fred_api"
    assert result["point_in_time_safe"] is True
    assert result["uses_revised_data"] is False
    assert result["non_null_observation_count"] == 80
    assert result["has_enough_history_for_5y_trend"] is True
    assert result["future_observation_count"] == 0
    assert result["latest_observation_date"] == "2024-08-01"
    assert result["observation_lag_days"] == 35
    assert result["sample_latest_observations"][-1]["date"] == "2024-08-01"


def test_build_as_of_result_passes_when_both_required_series_pass():
    as_of_result = build_as_of_result(
        label="project_recent_backtest",
        as_of=date(2024, 9, 5),
        series_results={
            "GDPC1": {"status": "passed", "errors": [], "warnings": []},
            "CPIAUCSL": {"status": "passed", "errors": [], "warnings": []},
            "PCEPI": {"status": "failed", "errors": ["optional unavailable"], "warnings": []},
        },
    )

    assert as_of_result["status"] == "passed"
    assert as_of_result["warnings"] == ["optional series PCEPI failed: optional unavailable"]
    assert as_of_result["errors"] == []


def test_build_top_level_status_passes_when_all_required_dates_pass():
    report = {
        "as_of_results": [
            {"status": "passed", "errors": []},
            {"status": "passed", "errors": []},
        ]
    }

    assert build_top_level_status(report) == "passed"


def test_report_values_are_json_serializable():
    payload = {
        "source": "fred_api",
        "series_id": "GDPC1",
        "as_of": "2024-09-05",
        "point_in_time_safe": True,
        "uses_revised_data": False,
        "observations": _observations(2018, 24, realtime="2024-09-05"),
    }
    result = analyze_series_payload(
        payload,
        config=SeriesCheckConfig(
            series_id="GDPC1",
            label="Real Gross Domestic Product",
            minimum_non_null_observations=24,
            required=True,
        ),
        as_of=date(2024, 9, 5),
    )

    encoded = json.dumps(result, sort_keys=True)
    assert "GDPC1" in encoded
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_fred_growth_inflation_data_availability.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.validate_fred_growth_inflation_data'`.

- [ ] **Step 3: Commit the failing tests**

```powershell
git add tests\test_fred_growth_inflation_data_availability.py
git commit -m "test: define fred macro data availability checks"
```

---

## Task 2: Implement Core Series Analysis

**Files:**

- Create: `scripts/validate_fred_growth_inflation_data.py`
- Test: `tests/test_fred_growth_inflation_data_availability.py`

- [ ] **Step 1: Add minimal implementation for Task 1 tests**

Create `scripts/validate_fred_growth_inflation_data.py` with:

```python
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lumibot.macro import FREDMacroData


ARTIFACT_ROOT = Path("artifacts") / "macro_regime_data_availability"


@dataclass(frozen=True)
class SeriesCheckConfig:
    series_id: str
    label: str
    minimum_non_null_observations: int
    required: bool


REQUIRED_SERIES: dict[str, SeriesCheckConfig] = {
    "GDPC1": SeriesCheckConfig(
        series_id="GDPC1",
        label="Real Gross Domestic Product",
        minimum_non_null_observations=24,
        required=True,
    ),
    "CPIAUCSL": SeriesCheckConfig(
        series_id="CPIAUCSL",
        label="Consumer Price Index for All Urban Consumers",
        minimum_non_null_observations=72,
        required=True,
    ),
}


OPTIONAL_SERIES: dict[str, SeriesCheckConfig] = {
    "PCEPI": SeriesCheckConfig(
        series_id="PCEPI",
        label="Personal Consumption Expenditures Price Index",
        minimum_non_null_observations=72,
        required=False,
    ),
    "GDP": SeriesCheckConfig(
        series_id="GDP",
        label="Gross Domestic Product",
        minimum_non_null_observations=24,
        required=False,
    ),
}


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


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


def _years_between(start: date | None, end: date | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).days / 365.25, 2)


def _sample_latest_observations(rows: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    sample = []
    for row in rows[-limit:]:
        sample.append(
            {
                "date": row.get("date"),
                "value": _safe_float(row.get("value")),
                "realtime_start": row.get("realtime_start"),
                "realtime_end": row.get("realtime_end"),
            }
        )
    return sample


def analyze_series_payload(
    payload: dict[str, Any],
    *,
    config: SeriesCheckConfig,
    as_of: date,
) -> dict[str, Any]:
    observations = list(payload.get("observations") or [])
    dated_rows: list[dict[str, Any]] = []
    future_rows: list[dict[str, Any]] = []
    non_null_count = 0

    for row in observations:
        obs_date = _parse_date(row.get("date"))
        if obs_date is None:
            continue
        normalized = {
            "date": obs_date.isoformat(),
            "value": _safe_float(row.get("value")),
            "realtime_start": row.get("realtime_start"),
            "realtime_end": row.get("realtime_end"),
        }
        if obs_date > as_of:
            future_rows.append(normalized)
            continue
        dated_rows.append(normalized)
        if normalized["value"] is not None:
            non_null_count += 1

    dated_rows.sort(key=lambda item: item["date"])
    earliest_date = _parse_date(dated_rows[0]["date"]) if dated_rows else None
    latest_date = _parse_date(dated_rows[-1]["date"]) if dated_rows else None
    has_enough_history = non_null_count >= config.minimum_non_null_observations
    future_count = len(future_rows)
    source = payload.get("source")
    point_in_time_safe = payload.get("point_in_time_safe") is True
    uses_revised_data = payload.get("uses_revised_data") is True

    errors: list[str] = []
    warnings: list[str] = []
    if not observations:
        errors.append("no observations returned")
    if source != "fred_api":
        errors.append(f"unexpected source: {source!r}")
    if not point_in_time_safe:
        errors.append("point_in_time_safe is not true")
    if uses_revised_data:
        errors.append("uses_revised_data is true")
    if future_count:
        errors.append(f"{future_count} observations are after as_of")
    if not has_enough_history:
        errors.append(
            f"only {non_null_count} non-null observations; need {config.minimum_non_null_observations}"
        )
    if latest_date is None:
        errors.append("no dated observations before or on as_of")

    lag_days = (as_of - latest_date).days if latest_date is not None else None
    if lag_days is not None and lag_days < 0:
        errors.append("latest observation is after as_of")

    status = "passed" if not errors else "failed"
    return {
        "status": status,
        "series_id": config.series_id,
        "label": config.label,
        "required": config.required,
        "available": bool(observations),
        "source": source,
        "point_in_time_safe": point_in_time_safe,
        "uses_revised_data": uses_revised_data,
        "observation_count": len(observations),
        "non_null_observation_count": non_null_count,
        "earliest_observation_date": earliest_date.isoformat() if earliest_date else None,
        "latest_observation_date": latest_date.isoformat() if latest_date else None,
        "observation_lag_days": lag_days,
        "history_years": _years_between(earliest_date, latest_date),
        "has_enough_history_for_5y_trend": has_enough_history,
        "future_observation_count": future_count,
        "sample_latest_observations": _sample_latest_observations(dated_rows),
        "warnings": warnings,
        "errors": errors,
    }


def build_as_of_result(
    *,
    label: str,
    as_of: date,
    series_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    for series_id, result in series_results.items():
        status = result.get("status")
        is_required = REQUIRED_SERIES.get(series_id, OPTIONAL_SERIES.get(series_id)).required
        message = "; ".join(result.get("errors") or [])
        if is_required and status != "passed":
            errors.append(f"required series {series_id} {status}: {message}")
        elif not is_required and status != "passed":
            warnings.append(f"optional series {series_id} {status}: {message}")
        warnings.extend(result.get("warnings") or [])

    return {
        "label": label,
        "as_of": as_of.isoformat(),
        "status": "passed" if not errors else "failed",
        "series": series_results,
        "warnings": warnings,
        "errors": errors,
    }


def build_top_level_status(report: dict[str, Any]) -> str:
    statuses = [item.get("status") for item in report.get("as_of_results", [])]
    if any(status == "blocked" for status in statuses):
        return "blocked"
    if any(status != "passed" for status in statuses):
        return "failed"
    return "passed"
```

- [ ] **Step 2: Run Task 1 tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_fred_growth_inflation_data_availability.py -q
```

Expected: PASS for the tests added in Task 1.

- [ ] **Step 3: Commit core analysis implementation**

```powershell
git add scripts\validate_fred_growth_inflation_data.py tests\test_fred_growth_inflation_data_availability.py
git commit -m "feat: analyze fred macro data availability"
```

---

## Task 3: Add Failure And Blocked Status Tests

**Files:**

- Modify: `tests/test_fred_growth_inflation_data_availability.py`
- Modify: `scripts/validate_fred_growth_inflation_data.py`

- [ ] **Step 1: Add tests for unsafe and insufficient data**

Append to `tests/test_fred_growth_inflation_data_availability.py`:

```python
def test_future_observations_fail_series_check():
    payload = {
        "source": "fred_api",
        "series_id": "GDPC1",
        "as_of": "2024-09-05",
        "point_in_time_safe": True,
        "uses_revised_data": False,
        "observations": [
            *_observations(2018, 24, realtime="2024-09-05"),
            {
                "date": "2024-10-01",
                "value": "999",
                "realtime_start": "2024-09-05",
                "realtime_end": "2024-09-05",
            },
        ],
    }

    result = analyze_series_payload(
        payload,
        config=REQUIRED_SERIES["GDPC1"],
        as_of=date(2024, 9, 5),
    )

    assert result["status"] == "failed"
    assert result["future_observation_count"] == 1
    assert "1 observations are after as_of" in result["errors"]


def test_too_few_required_observations_fails_series_check():
    payload = {
        "source": "fred_api",
        "series_id": "CPIAUCSL",
        "as_of": "2024-09-05",
        "point_in_time_safe": True,
        "uses_revised_data": False,
        "observations": _observations(2024, 5, realtime="2024-09-05"),
    }

    result = analyze_series_payload(
        payload,
        config=REQUIRED_SERIES["CPIAUCSL"],
        as_of=date(2024, 9, 5),
    )

    assert result["status"] == "failed"
    assert result["has_enough_history_for_5y_trend"] is False
    assert "only 5 non-null observations; need 72" in result["errors"]


def test_revised_or_non_fred_api_data_fails_series_check():
    payload = {
        "source": "csv",
        "series_id": "GDPC1",
        "as_of": "2024-09-05",
        "point_in_time_safe": False,
        "uses_revised_data": True,
        "observations": _observations(2018, 24, realtime="2024-09-05"),
    }

    result = analyze_series_payload(
        payload,
        config=REQUIRED_SERIES["GDPC1"],
        as_of=date(2024, 9, 5),
    )

    assert result["status"] == "failed"
    assert "unexpected source: 'csv'" in result["errors"]
    assert "point_in_time_safe is not true" in result["errors"]
    assert "uses_revised_data is true" in result["errors"]
```

- [ ] **Step 2: Run tests and verify failure or pass**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_fred_growth_inflation_data_availability.py -q
```

Expected: PASS if Task 2 implementation already covers these cases. If any test fails, update `analyze_series_payload()` using the exact logic in Task 2.

- [ ] **Step 3: Add blocked result helper test**

First update the existing `scripts.validate_fred_growth_inflation_data` import block in the test file to include `build_blocked_report`.

```python
from scripts.validate_fred_growth_inflation_data import (
    REQUIRED_SERIES,
    OPTIONAL_SERIES,
    SeriesCheckConfig,
    analyze_series_payload,
    build_as_of_result,
    build_blocked_report,
    build_top_level_status,
)
```

Then append this test:

```python

def test_missing_api_key_blocked_report_has_no_secret_text():
    report = build_blocked_report(
        reason="missing_fred_api_key",
        artifact_dir="artifacts/macro_regime_data_availability/test-run",
    )

    encoded = json.dumps(report, sort_keys=True)
    assert report["status"] == "blocked"
    assert report["reason"] == "missing_fred_api_key"
    assert "api_key" not in encoded.lower()
    assert "sk-" not in encoded
```

- [ ] **Step 4: Implement blocked report helper**

Add this function to `scripts/validate_fred_growth_inflation_data.py`:

```python
def build_blocked_report(*, reason: str, artifact_dir: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "blocked",
        "reason": reason,
        "generated_at": datetime.now(UTC).isoformat(),
        "toolchain": {
            "client": "lumibot.macro.FREDMacroData",
            "uses_official_fred_api": True,
            "cache_dir": str(Path(artifact_dir) / "fred_cache"),
        },
        "required_series": list(REQUIRED_SERIES),
        "optional_series": list(OPTIONAL_SERIES),
        "as_of_results": [],
    }
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_fred_growth_inflation_data_availability.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit failure and blocked status coverage**

```powershell
git add scripts\validate_fred_growth_inflation_data.py tests\test_fred_growth_inflation_data_availability.py
git commit -m "test: cover fred macro validation failures"
```

---

## Task 4: Add Fetching, Env File Loading, And Report Building

**Files:**

- Modify: `scripts/validate_fred_growth_inflation_data.py`
- Modify: `tests/test_fred_growth_inflation_data_availability.py`

- [ ] **Step 1: Add tests for env loading and mocked FRED client execution**

First add `Path` to the top import section:

```python
from pathlib import Path
```

Then update the existing `scripts.validate_fred_growth_inflation_data` import block to include `collect_availability_report` and `load_env_file`.

```python
from scripts.validate_fred_growth_inflation_data import (
    REQUIRED_SERIES,
    OPTIONAL_SERIES,
    SeriesCheckConfig,
    analyze_series_payload,
    build_as_of_result,
    build_blocked_report,
    build_top_level_status,
    collect_availability_report,
    load_env_file,
)
```

Then append these tests:

```python

class FakeFredClient:
    def __init__(self):
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
        minimum = REQUIRED_SERIES.get(series_id, OPTIONAL_SERIES.get(series_id)).minimum_non_null_observations
        return {
            "source": "fred_api",
            "series_id": series_id,
            "as_of": as_of,
            "point_in_time_safe": True,
            "uses_revised_data": False,
            "observations": _observations(2017, minimum + 2, realtime=as_of),
        }


def test_load_env_file_reads_key_value_pairs_without_overwriting(monkeypatch, tmp_path):
    env_file = tmp_path / "API.txt"
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                "FRED_API_KEY=from-file",
                "OPENAI_API_KEY=from-file-openai",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FRED_API_KEY", "existing")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    loaded = load_env_file(env_file)

    assert loaded == {"FRED_API_KEY", "OPENAI_API_KEY"}
    assert os.environ["FRED_API_KEY"] == "existing"
    assert os.environ["OPENAI_API_KEY"] == "from-file-openai"


def test_collect_availability_report_uses_fred_client_for_required_and_optional_series(tmp_path):
    client = FakeFredClient()

    report = collect_availability_report(
        fred=client,
        as_of_dates=[("project_recent_backtest", date(2024, 9, 5))],
        artifact_dir=tmp_path,
        include_optional=True,
    )

    assert report["status"] == "passed"
    assert report["toolchain"]["cache_dir"] == str(tmp_path / "fred_cache")
    assert report["required_series"] == ["GDPC1", "CPIAUCSL"]
    assert report["optional_series"] == ["PCEPI", "GDP"]
    assert report["as_of_results"][0]["status"] == "passed"
    assert [call["series_id"] for call in client.calls] == ["GDPC1", "CPIAUCSL", "PCEPI", "GDP"]
    assert all(call["as_of"] == "2024-09-05" for call in client.calls)
```

- [ ] **Step 2: Run tests to see missing functions**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_fred_growth_inflation_data_availability.py -q
```

Expected: FAIL mentioning `collect_availability_report` and `load_env_file` are missing.

- [ ] **Step 3: Implement env loading and report collection**

Add to `scripts/validate_fred_growth_inflation_data.py`:

```python
def load_env_file(path: Path) -> set[str]:
    loaded: set[str] = set()
    if not path.exists():
        return loaded
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            loaded.add(key)
        if key and key not in os.environ:
            os.environ[key] = value
    return loaded


def _history_start_for_as_of(as_of: date, years: int = 25) -> str:
    return date(max(as_of.year - years, 1900), as_of.month, 1).isoformat()


def _fetch_series_result(
    *,
    fred: Any,
    config: SeriesCheckConfig,
    as_of: date,
) -> dict[str, Any]:
    try:
        payload = fred.get_series(
            config.series_id,
            start=_history_start_for_as_of(as_of),
            end=as_of.isoformat(),
            as_of=as_of.isoformat(),
        )
        return analyze_series_payload(payload, config=config, as_of=as_of)
    except ValueError as exc:
        message = str(exc)
        status = "blocked" if "FRED_API_KEY is required" in message else "failed"
        return {
            "status": status,
            "series_id": config.series_id,
            "label": config.label,
            "required": config.required,
            "available": False,
            "source": None,
            "point_in_time_safe": False,
            "uses_revised_data": None,
            "observation_count": 0,
            "non_null_observation_count": 0,
            "earliest_observation_date": None,
            "latest_observation_date": None,
            "observation_lag_days": None,
            "history_years": None,
            "has_enough_history_for_5y_trend": False,
            "future_observation_count": 0,
            "sample_latest_observations": [],
            "warnings": [],
            "errors": [message],
        }
    except Exception as exc:
        return {
            "status": "blocked",
            "series_id": config.series_id,
            "label": config.label,
            "required": config.required,
            "available": False,
            "source": None,
            "point_in_time_safe": False,
            "uses_revised_data": None,
            "observation_count": 0,
            "non_null_observation_count": 0,
            "earliest_observation_date": None,
            "latest_observation_date": None,
            "observation_lag_days": None,
            "history_years": None,
            "has_enough_history_for_5y_trend": False,
            "future_observation_count": 0,
            "sample_latest_observations": [],
            "warnings": [],
            "errors": [f"{type(exc).__name__}: {exc}"],
        }


def collect_availability_report(
    *,
    fred: Any,
    as_of_dates: list[tuple[str, date]],
    artifact_dir: Path,
    include_optional: bool,
) -> dict[str, Any]:
    all_series = dict(REQUIRED_SERIES)
    optional_series = dict(OPTIONAL_SERIES) if include_optional else {}
    all_series.update(optional_series)

    as_of_results = []
    for label, as_of in as_of_dates:
        series_results = {
            series_id: _fetch_series_result(fred=fred, config=config, as_of=as_of)
            for series_id, config in all_series.items()
        }
        as_of_results.append(build_as_of_result(label=label, as_of=as_of, series_results=series_results))

    report = {
        "schema_version": 1,
        "status": "passed",
        "generated_at": datetime.now(UTC).isoformat(),
        "toolchain": {
            "client": "lumibot.macro.FREDMacroData",
            "uses_official_fred_api": True,
            "cache_dir": str(artifact_dir / "fred_cache"),
        },
        "required_series": list(REQUIRED_SERIES),
        "optional_series": list(optional_series),
        "as_of_results": as_of_results,
    }
    report["status"] = build_top_level_status(report)
    return report
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_fred_growth_inflation_data_availability.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit report collection logic**

```powershell
git add scripts\validate_fred_growth_inflation_data.py tests\test_fred_growth_inflation_data_availability.py
git commit -m "feat: collect fred growth inflation availability"
```

---

## Task 5: Add JSON And Markdown Report Writers

**Files:**

- Modify: `scripts/validate_fred_growth_inflation_data.py`
- Modify: `tests/test_fred_growth_inflation_data_availability.py`

- [ ] **Step 1: Add report writer tests**

Update the existing `scripts.validate_fred_growth_inflation_data` import block to include `write_reports`.

```python
from scripts.validate_fred_growth_inflation_data import (
    REQUIRED_SERIES,
    OPTIONAL_SERIES,
    SeriesCheckConfig,
    analyze_series_payload,
    build_as_of_result,
    build_blocked_report,
    build_top_level_status,
    collect_availability_report,
    load_env_file,
    write_reports,
)
```

Then append this test:

```python

def test_write_reports_creates_json_and_markdown_without_secret_text(tmp_path):
    report = {
        "schema_version": 1,
        "status": "passed",
        "generated_at": "2026-08-11T00:00:00+00:00",
        "toolchain": {
            "client": "lumibot.macro.FREDMacroData",
            "uses_official_fred_api": True,
            "cache_dir": str(tmp_path / "fred_cache"),
        },
        "required_series": ["GDPC1", "CPIAUCSL"],
        "optional_series": ["PCEPI", "GDP"],
        "as_of_results": [
            {
                "label": "project_recent_backtest",
                "as_of": "2024-09-05",
                "status": "passed",
                "series": {
                    "GDPC1": {
                        "status": "passed",
                        "latest_observation_date": "2024-04-01",
                        "observation_lag_days": 157,
                        "non_null_observation_count": 24,
                        "has_enough_history_for_5y_trend": True,
                        "errors": [],
                        "warnings": [],
                    },
                    "CPIAUCSL": {
                        "status": "passed",
                        "latest_observation_date": "2024-08-01",
                        "observation_lag_days": 35,
                        "non_null_observation_count": 72,
                        "has_enough_history_for_5y_trend": True,
                        "errors": [],
                        "warnings": [],
                    },
                },
                "warnings": [],
                "errors": [],
            }
        ],
    }

    paths = write_reports(report, artifact_dir=tmp_path)

    assert paths["json"].name == "fred_growth_inflation_data_availability.json"
    assert paths["markdown"].name == "fred_growth_inflation_data_availability.md"
    assert paths["json"].exists()
    assert paths["markdown"].exists()
    markdown = paths["markdown"].read_text(encoding="utf-8")
    encoded = paths["json"].read_text(encoding="utf-8") + markdown
    assert "# FRED Growth / Inflation Data Availability Report" in markdown
    assert "safe to proceed to Stage 2" in markdown
    assert "2024-09-05" in markdown
    assert "GDPC1" in markdown
    assert "CPIAUCSL" in markdown
    assert "sk-" not in encoded
```

- [ ] **Step 2: Run tests to see missing writer**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_fred_growth_inflation_data_availability.py::test_write_reports_creates_json_and_markdown_without_secret_text -q
```

Expected: FAIL because `write_reports` does not exist.

- [ ] **Step 3: Implement Markdown renderer and writers**

Add to `scripts/validate_fred_growth_inflation_data.py`:

```python
def _recommendation_for_status(status: str) -> str:
    if status == "passed":
        return "safe to proceed to Stage 2"
    if status == "blocked":
        return "blocked by missing key, network, quota, or FRED service availability"
    return "blocked by missing, unsafe, future-dated, or insufficient required macro data"


def render_markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# FRED Growth / Inflation Data Availability Report",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Client: `{report.get('toolchain', {}).get('client')}`",
        f"- Cache directory: `{report.get('toolchain', {}).get('cache_dir')}`",
        f"- Required series: `{', '.join(report.get('required_series', []))}`",
        f"- Optional series: `{', '.join(report.get('optional_series', []))}`",
        f"- Recommendation: **{_recommendation_for_status(str(report.get('status')))}**",
        "",
    ]
    reason = report.get("reason")
    if reason:
        lines.extend(["## Blocked Reason", "", f"`{reason}`", ""])

    for as_of_result in report.get("as_of_results", []):
        lines.extend(
            [
                f"## {as_of_result.get('label')} - {as_of_result.get('as_of')}",
                "",
                f"- Status: `{as_of_result.get('status')}`",
                "",
                "| Series | Status | Latest Observation | Lag Days | Non-Null Obs | 5Y History |",
                "|---|---|---:|---:|---:|---|",
            ]
        )
        for series_id, result in as_of_result.get("series", {}).items():
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(series_id),
                        f"`{result.get('status')}`",
                        str(result.get("latest_observation_date")),
                        str(result.get("observation_lag_days")),
                        str(result.get("non_null_observation_count")),
                        str(result.get("has_enough_history_for_5y_trend")),
                    ]
                )
                + " |"
            )
        if as_of_result.get("warnings"):
            lines.extend(["", "Warnings:"])
            lines.extend(f"- {warning}" for warning in as_of_result["warnings"])
        if as_of_result.get("errors"):
            lines.extend(["", "Errors:"])
            lines.extend(f"- {error}" for error in as_of_result["errors"])
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_reports(report: dict[str, Any], *, artifact_dir: Path) -> dict[str, Path]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    json_path = artifact_dir / "fred_growth_inflation_data_availability.json"
    markdown_path = artifact_dir / "fred_growth_inflation_data_availability.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}
```

- [ ] **Step 4: Run full validator test module**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_fred_growth_inflation_data_availability.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit report writers**

```powershell
git add scripts\validate_fred_growth_inflation_data.py tests\test_fred_growth_inflation_data_availability.py
git commit -m "feat: write fred macro availability reports"
```

---

## Task 6: Add CLI Entry Point

**Files:**

- Modify: `scripts/validate_fred_growth_inflation_data.py`
- Modify: `tests/test_fred_growth_inflation_data_availability.py`

- [ ] **Step 1: Add CLI parsing tests**

Update the existing `scripts.validate_fred_growth_inflation_data` import block to include `build_as_of_dates` and `parse_args`.

```python
from scripts.validate_fred_growth_inflation_data import (
    REQUIRED_SERIES,
    OPTIONAL_SERIES,
    SeriesCheckConfig,
    analyze_series_payload,
    build_as_of_result,
    build_as_of_dates,
    build_blocked_report,
    build_top_level_status,
    collect_availability_report,
    load_env_file,
    parse_args,
    write_reports,
)
```

Then append these tests:

```python

def test_parse_args_accepts_required_dates_and_include_recent():
    args = parse_args(
        [
            "--as-of",
            "2024-09-05",
            "--as-of",
            "2010-01-01",
            "--include-recent",
            "--env-file",
            "<path-to-fred-env-file>",
        ]
    )

    assert args.as_of == ["2024-09-05", "2010-01-01"]
    assert args.include_recent is True
    assert args.env_file == Path("<path-to-fred-env-file>")


def test_build_as_of_dates_labels_project_and_older_dates(monkeypatch):
    monkeypatch.setattr(
        "scripts.validate_fred_growth_inflation_data.date",
        type(
            "FakeDate",
            (date,),
            {
                "today": classmethod(lambda cls: date(2026, 8, 11)),
            },
        ),
    )

    dates = build_as_of_dates(["2024-09-05", "2010-01-01"], include_recent=True)

    assert dates == [
        ("recent", date(2026, 8, 11)),
        ("project_recent_backtest", date(2024, 9, 5)),
        ("older_backtest", date(2010, 1, 1)),
    ]
```

- [ ] **Step 2: Run CLI tests to see missing functions**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_fred_growth_inflation_data_availability.py::test_parse_args_accepts_required_dates_and_include_recent tests\test_fred_growth_inflation_data_availability.py::test_build_as_of_dates_labels_project_and_older_dates -q
```

Expected: FAIL because `parse_args` and `build_as_of_dates` do not exist.

- [ ] **Step 3: Implement CLI parsing and main**

Add to `scripts/validate_fred_growth_inflation_data.py`:

```python
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate FRED growth and inflation data availability for the real macro regime classifier."
    )
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--as-of", action="append", default=[])
    parser.add_argument("--include-recent", action="store_true")
    parser.add_argument("--include-optional", action="store_true")
    parser.add_argument("--artifact-root", type=Path, default=ARTIFACT_ROOT)
    parser.add_argument("--run-id", default=None)
    return parser.parse_args(argv)


def build_as_of_dates(raw_dates: list[str], *, include_recent: bool) -> list[tuple[str, date]]:
    results: list[tuple[str, date]] = []
    if include_recent:
        results.append(("recent", date.today()))
    for raw in raw_dates:
        parsed = _parse_date(raw)
        if parsed is None:
            raise ValueError(f"invalid --as-of date: {raw}")
        if parsed == date(2024, 9, 5):
            label = "project_recent_backtest"
        elif parsed == date(2010, 1, 1):
            label = "older_backtest"
        else:
            label = f"as_of_{parsed.isoformat()}"
        results.append((label, parsed))
    if not results:
        results.extend(
            [
                ("project_recent_backtest", date(2024, 9, 5)),
                ("older_backtest", date(2010, 1, 1)),
            ]
        )
    return results


def _run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.env_file is not None:
        load_env_file(args.env_file)

    run_id = args.run_id or _run_id()
    artifact_dir = args.artifact_root / run_id
    cache_dir = artifact_dir / "fred_cache"
    os.environ["LUMIBOT_FRED_CACHE_DIR"] = str(cache_dir)

    if not os.environ.get("FRED_API_KEY"):
        report = build_blocked_report(reason="missing_fred_api_key", artifact_dir=str(artifact_dir))
        paths = write_reports(report, artifact_dir=artifact_dir)
        print(json.dumps({"status": report["status"], "artifact_dir": str(artifact_dir), **{k: str(v) for k, v in paths.items()}}, sort_keys=True))
        return 2

    fred = FREDMacroData(cache_dir=cache_dir, min_request_interval_seconds=0.2)
    try:
        report = collect_availability_report(
            fred=fred,
            as_of_dates=build_as_of_dates(args.as_of, include_recent=args.include_recent),
            artifact_dir=artifact_dir,
            include_optional=args.include_optional,
        )
    except Exception as exc:
        report = build_blocked_report(reason=f"{type(exc).__name__}: {exc}", artifact_dir=str(artifact_dir))

    paths = write_reports(report, artifact_dir=artifact_dir)
    print(
        json.dumps(
            {
                "status": report["status"],
                "artifact_dir": str(artifact_dir),
                "json": str(paths["json"]),
                "markdown": str(paths["markdown"]),
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run full unit tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_fred_growth_inflation_data_availability.py -q
```

Expected: PASS.

- [ ] **Step 5: Run blocked CLI smoke without FRED key**

Run:

```powershell
cmd /c "set FRED_API_KEY=& .venv\Scripts\python.exe scripts\validate_fred_growth_inflation_data.py --run-id missing-key-smoke"
```

Expected: command exits non-zero and prints JSON containing `"status": "blocked"` and an artifact path under `artifacts\macro_regime_data_availability\missing-key-smoke`.

- [ ] **Step 6: Commit CLI entry point**

```powershell
git add scripts\validate_fred_growth_inflation_data.py tests\test_fred_growth_inflation_data_availability.py
git commit -m "feat: add fred macro availability cli"
```

---

## Task 7: Run Static Checks

**Files:**

- Check: `scripts/validate_fred_growth_inflation_data.py`
- Check: `tests/test_fred_growth_inflation_data_availability.py`

- [ ] **Step 1: Run ruff**

Run:

```powershell
.venv\Scripts\python.exe -m ruff check scripts\validate_fred_growth_inflation_data.py tests\test_fred_growth_inflation_data_availability.py
```

Expected: PASS.

- [ ] **Step 2: If ruff fails, apply the exact reported formatting/import fixes**

Use `apply_patch` to fix reported lines only. Do not refactor unrelated code.

- [ ] **Step 3: Re-run unit tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_fred_growth_inflation_data_availability.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit lint fixes if any files changed**

If `git status --short` shows changes in the script or test file:

```powershell
git add scripts\validate_fred_growth_inflation_data.py tests\test_fred_growth_inflation_data_availability.py
git commit -m "chore: clean fred availability validator"
```

If no files changed, skip this commit.

---

## Task 8: Run Real Stage 1 Validation

**Files:**

- Execute: `scripts/validate_fred_growth_inflation_data.py`
- Output: `artifacts/macro_regime_data_availability/stage1-real-validation/fred_growth_inflation_data_availability.json`
- Output: `artifacts/macro_regime_data_availability/stage1-real-validation/fred_growth_inflation_data_availability.md`

- [ ] **Step 1: Run real validation with local API file**

Run:

```powershell
.venv\Scripts\python.exe scripts\validate_fred_growth_inflation_data.py --env-file <path-to-fred-env-file> --include-recent --include-optional --as-of 2024-09-05 --as-of 2010-01-01 --run-id stage1-real-validation
```

Expected:

- The command prints one JSON line.
- The JSON line includes `"artifact_dir": "artifacts\\macro_regime_data_availability\\stage1-real-validation"` or the same path with forward slashes.
- Status is `passed`, `failed`, or `blocked`.
- It does not print any API key.

- [ ] **Step 2: Inspect generated report paths**

```powershell
$artifact = "artifacts\macro_regime_data_availability\stage1-real-validation"
Get-ChildItem $artifact
Get-Content "$artifact\fred_growth_inflation_data_availability.md" -Raw
```

Expected:

- JSON report exists.
- Markdown report exists.
- Markdown report explains whether Stage 2 is safe to proceed.
- Markdown report lists `GDPC1` and `CPIAUCSL`.
- Markdown report lists `2024-09-05` and `2010-01-01`.

- [ ] **Step 3: Verify no API key leaked**

Run:

```powershell
$artifact = "artifacts\macro_regime_data_availability\stage1-real-validation"
Select-String -Path "$artifact\*" -Pattern "sk-","FRED_API_KEY","api_key" -SimpleMatch
```

Expected:

- No OpenAI key text.
- No FRED key value.
- It is acceptable if the literal phrase `FRED_API_KEY` appears only in a blocked reason. If a real key value appears, stop and redact the artifact.

- [ ] **Step 4: Summarize Stage 1 outcome**

Create a short note in the final implementation response:

```text
Stage 1 validation status: passed, failed, or blocked
Artifact directory: artifacts\macro_regime_data_availability\stage1-real-validation
JSON report: artifacts\macro_regime_data_availability\stage1-real-validation\fred_growth_inflation_data_availability.json
Markdown report: artifacts\macro_regime_data_availability\stage1-real-validation\fred_growth_inflation_data_availability.md
Stage 2 recommendation: quote the recommendation line from the Markdown report
```

- [ ] **Step 5: Commit real validation script and tests if not already committed**

If `git status --short` shows only intended code/test changes:

```powershell
git add scripts\validate_fred_growth_inflation_data.py tests\test_fred_growth_inflation_data_availability.py
git commit -m "feat: validate fred growth inflation data availability"
```

Do not commit generated artifacts unless the user explicitly asks to preserve a specific validation report in git.

---

## Task 9: Final Verification And Handoff

**Files:**

- Read: `docs/superpowers/specs/2026-08-11-fred-growth-inflation-data-availability-design.md`
- Read: generated Markdown report from Task 8

- [ ] **Step 1: Run complete verification command set**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_fred_growth_inflation_data_availability.py -q
.venv\Scripts\python.exe -m ruff check scripts\validate_fred_growth_inflation_data.py tests\test_fred_growth_inflation_data_availability.py
```

Expected: both commands PASS.

- [ ] **Step 2: Check git status**

Run:

```powershell
git status --short --branch
```

Expected:

- Only unrelated pre-existing files may remain modified/untracked.
- The new script and test file should be committed or intentionally left staged only if the user requested no commits.

- [ ] **Step 3: Compare implementation against spec**

Confirm:

- `GDPC1` is checked.
- `CPIAUCSL` is checked.
- `PCEPI` and `GDP` are optional.
- `2024-09-05` is checked.
- `2010-01-01` is checked.
- Current/recent date is checked when `--include-recent` is passed.
- JSON report is written.
- Markdown report is written.
- Missing key results in `blocked`.
- No LLM is called.
- No backtest is run.
- No trading strategy files are modified.

- [ ] **Step 4: Final response**

Report:

```text
Implemented Stage 1 FRED data availability validator.
Tests: pytest and ruff command results
Real validation: passed, failed, or blocked
Reports: JSON and Markdown report paths
Recommendation: quote the recommendation line from the Markdown report
```

If real validation is `failed` or `blocked`, explain the concrete blocker before suggesting any classifier implementation.

