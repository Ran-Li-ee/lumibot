from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from scripts.validate_fred_growth_inflation_data import (
    OPTIONAL_SERIES,
    REQUIRED_SERIES,
    SeriesCheckConfig,
    analyze_series_payload,
    build_as_of_dates,
    build_as_of_result,
    build_blocked_report,
    build_top_level_status,
    collect_availability_report,
    load_env_file,
    main,
    parse_args,
    sanitize_sensitive_text,
    write_reports,
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


def test_missing_realtime_fields_fail_fred_api_series_check():
    payload = {
        "source": "fred_api",
        "series_id": "GDPC1",
        "as_of": "2024-09-05",
        "point_in_time_safe": True,
        "uses_revised_data": False,
        "observations": [
            {"date": "2019-01-01", "value": "100"},
            {"date": "2019-04-01", "value": "101", "realtime_start": "2024-09-05"},
            *_observations(2019, 24, realtime="2024-09-05"),
        ],
    }

    result = analyze_series_payload(
        payload,
        config=REQUIRED_SERIES["GDPC1"],
        as_of=date(2024, 9, 5),
    )

    assert result["status"] == "failed"
    assert result["missing_realtime_field_count"] == 2
    assert "2 observations are missing realtime_start or realtime_end" in result["errors"]


def test_missing_api_key_blocked_report_has_no_secret_text():
    report = build_blocked_report(
        reason="missing_fred_api_key",
        artifact_dir="artifacts/macro_regime_data_availability/test-run",
    )

    encoded = json.dumps(report, sort_keys=True)
    assert report["status"] == "blocked"
    assert report["reason"] == "missing_fred_api_key"
    assert "sk-" not in encoded
    assert "from-file" not in encoded


def test_sanitize_sensitive_text_redacts_query_params_and_known_env_values(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "secret-value")

    sanitized = sanitize_sensitive_text(
        "HTTPError: https://api.stlouisfed.org/fred/series?api_key=secret-value&series_id=GDPC1"
    )

    assert "secret-value" not in sanitized
    assert "api_key=<redacted>" in sanitized


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
        config = REQUIRED_SERIES.get(series_id) or OPTIONAL_SERIES[series_id]
        minimum = config.minimum_non_null_observations
        return {
            "source": "fred_api",
            "series_id": series_id,
            "as_of": as_of,
            "point_in_time_safe": True,
            "uses_revised_data": False,
            "observations": _observations(2017, minimum + 2, realtime=as_of),
        }


class FailingFredClient:
    def get_series(self, series_id, *, start=None, end=None, as_of=None, limit=None):
        raise RuntimeError(
            f"request failed for {series_id}: "
            "https://api.stlouisfed.org/fred/series?api_key=secret-value&series_id=GDPC1"
        )


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

    assert loaded == {"FRED_API_KEY"}
    assert os.environ["FRED_API_KEY"] == "existing"
    assert "OPENAI_API_KEY" not in os.environ


def test_load_env_file_reads_fred_heading_with_bare_key_only(monkeypatch, tmp_path):
    env_file = tmp_path / "API.txt"
    fred_key = "a" * 32
    env_file.write_text(
        "\n".join(
            [
                "OpenAI",
                "sk-proj-secret-openai-key",
                "",
                "FRED",
                fred_key,
                "",
                "Alpaca",
                "key: secret-alpaca-key",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    loaded = load_env_file(env_file)

    assert loaded == {"FRED_API_KEY"}
    assert os.environ["FRED_API_KEY"] == fred_key
    assert "OPENAI_API_KEY" not in os.environ


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


def test_exception_report_paths_do_not_leak_api_key_values(monkeypatch, tmp_path):
    monkeypatch.setenv("FRED_API_KEY", "secret-value")
    report = collect_availability_report(
        fred=FailingFredClient(),
        as_of_dates=[("project_recent_backtest", date(2024, 9, 5))],
        artifact_dir=tmp_path,
        include_optional=False,
    )

    paths = write_reports(report, artifact_dir=tmp_path)
    encoded = json.dumps(report, sort_keys=True)
    encoded += paths["json"].read_text(encoding="utf-8")
    encoded += paths["markdown"].read_text(encoding="utf-8")

    assert report["status"] == "blocked"
    assert "secret-value" not in encoded
    assert "api_key=<redacted>" in encoded


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


def test_parse_args_accepts_required_dates_and_include_recent():
    args = parse_args(
        [
            "--as-of",
            "2024-09-05",
            "--as-of",
            "2010-01-01",
            "--include-recent",
            "--env-file",
            "local/fred.env",
        ]
    )

    assert args.as_of == ["2024-09-05", "2010-01-01"]
    assert args.include_recent is True
    assert args.env_file == Path("local/fred.env")


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


def test_build_as_of_dates_defaults_to_all_required_date_categories(monkeypatch):
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

    dates = build_as_of_dates([], include_recent=False)

    assert dates == [
        ("recent", date(2026, 8, 11)),
        ("project_recent_backtest", date(2024, 9, 5)),
        ("older_backtest", date(2010, 1, 1)),
    ]


def test_invalid_cli_date_writes_failed_report_not_blocked(tmp_path, capsys):
    exit_code = main(
        [
            "--artifact-root",
            str(tmp_path),
            "--run-id",
            "bad-date",
            "--as-of",
            "not-a-date",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    report_path = tmp_path / "bad-date" / "fred_growth_inflation_data_availability.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    markdown = (tmp_path / "bad-date" / "fred_growth_inflation_data_availability.md").read_text(encoding="utf-8")

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert report["status"] == "failed"
    assert "invalid --as-of date: not-a-date" in report["reason"]
    assert "## Failure Reason" in markdown
    assert "## Blocked Reason" not in markdown
