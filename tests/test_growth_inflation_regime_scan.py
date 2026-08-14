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
        "mode": "fred_ra_vintage_asof",
        "date": "2024-09-05",
        "as_of": "2024-09-05",
        "requested_as_of": "2024-09-05",
        "effective_as_of": "2024-09-05",
        "lookahead_clamped": False,
        "as_of_policy": "same_day_vintage",
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
        "data_quality": {
            "as_of_policy": "same_day_vintage",
            "requested_as_of": "2024-09-05",
            "effective_as_of": "2024-09-05",
            "lookahead_clamped": False,
        },
        "confidence": {"level": "medium"},
        "reason_brief": reason_brief,
    }


def blocked_result(reason: str = "missing_fred_api_key") -> dict:
    return {
        "tool": "macro_regime_classifier",
        "status": "blocked",
        "mock": False,
        "mode": "fred_ra_vintage_asof",
        "date": "2024-09-06",
        "as_of": "2024-09-06",
        "requested_as_of": "2024-09-06",
        "effective_as_of": "2024-09-06",
        "lookahead_clamped": False,
        "as_of_policy": "same_day_vintage",
        "reason": reason,
        "data_quality": {
            "status": "blocked",
            "errors": ["FRED_API_KEY is required"],
            "warnings": [],
            "as_of_policy": "same_day_vintage",
            "requested_as_of": "2024-09-06",
            "effective_as_of": "2024-09-06",
            "lookahead_clamped": False,
        },
    }


def test_parse_date_argument_accepts_iso_date():
    assert scan.parse_date_argument("2024-09-05", name="--start") == date(2024, 9, 5)


def test_parse_date_argument_rejects_bad_date():
    with pytest.raises(ValueError, match="invalid --start date"):
        scan.parse_date_argument("not-a-date", name="--start")


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


def test_default_classifier_config_uses_vintage_asof_mode_without_lag_options():
    args = scan.parse_args(
        [
            "--start",
            "2024-09-01",
            "--end",
            "2024-09-01",
        ]
    )

    assert args.mode == "fred_ra_vintage_asof"
    assert args.as_of_policy == "same_day_vintage"
    assert args.requested_as_of is None
    assert scan._classifier_config(args) == {
        "mode": "fred_ra_vintage_asof",
        "as_of_policy": "same_day_vintage",
        "requested_as_of": None,
        "growth_series_id": "GDPC1",
        "inflation_series_id": "CPIAUCSL",
        "trend_years": 5,
    }


def test_explicit_vintage_classifier_config_passes_as_of_policy_and_requested_as_of():
    args = scan.parse_args(
        [
            "--start",
            "2024-09-01",
            "--end",
            "2024-09-01",
            "--mode",
            "fred_ra_vintage_asof",
            "--as-of-policy",
            "explicit",
            "--requested-as-of",
            "2024-08-30",
            "--growth-series-id",
            "CUSTOM_GROWTH",
            "--inflation-series-id",
            "CUSTOM_INFLATION",
            "--trend-years",
            "7",
        ]
    )

    assert scan._classifier_config(args) == {
        "mode": "fred_ra_vintage_asof",
        "as_of_policy": "explicit",
        "requested_as_of": "2024-08-30",
        "growth_series_id": "CUSTOM_GROWTH",
        "inflation_series_id": "CUSTOM_INFLATION",
        "trend_years": 7,
    }


def test_legacy_classifier_config_keeps_lag_options_for_comparison_mode():
    args = scan.parse_args(
        [
            "--start",
            "2024-09-01",
            "--end",
            "2024-09-01",
            "--mode",
            "fred_ra_simple_lagged",
            "--growth-lag-months",
            "8",
            "--inflation-lag-months",
            "2",
        ]
    )

    assert scan._classifier_config(args) == {
        "mode": "fred_ra_simple_lagged",
        "as_of_policy": "same_day_vintage",
        "requested_as_of": None,
        "growth_series_id": "GDPC1",
        "inflation_series_id": "CPIAUCSL",
        "trend_years": 5,
        "growth_lag_months": 8,
        "inflation_lag_months": 2,
    }


def test_parse_args_rejects_negative_window(capsys):
    with pytest.raises(SystemExit):
        scan.parse_args(
            [
                "--start",
                "2024-09-01",
                "--end",
                "2024-10-15",
                "--window-before",
                "-1",
            ]
        )

    captured = capsys.readouterr()
    assert "--window-before must be non-negative" in captured.err


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("--growth-lag-months", "-1", "--growth-lag-months must be non-negative"),
        ("--inflation-lag-months", "-1", "--inflation-lag-months must be non-negative"),
        ("--trend-years", "0", "--trend-years must be positive"),
    ],
)
def test_parse_args_rejects_invalid_classifier_numeric_options(option, value, message, capsys):
    with pytest.raises(SystemExit):
        scan.parse_args(
            [
                "--start",
                "2024-09-01",
                "--end",
                "2024-10-15",
                option,
                value,
            ]
        )

    captured = capsys.readouterr()
    assert message in captured.err


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
    def fail_trading_days(_start, _end):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr(scan, "get_nyse_trading_days", fail_trading_days)

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


def test_flatten_classifier_result_marks_passed_regime_change():
    row = scan.flatten_classifier_result(
        scan_date=date(2024, 9, 9),
        result=passed_result("growth_down_inflation_up"),
        previous_passed_regime="growth_up_inflation_down",
    )

    assert row["status"] == "passed"
    assert row["previous_passed_regime"] == "growth_up_inflation_down"
    assert row["regime_changed"] is True


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
            "mode": "fred_ra_vintage_asof",
            "as_of_policy": "same_day_vintage",
            "requested_as_of": None,
            "growth_series_id": "GDPC1",
            "inflation_series_id": "CPIAUCSL",
            "trend_years": 5,
        },
    )

    assert [call["date"] for call in calls] == ["2024-09-05", "2024-09-06", "2024-09-09"]
    assert calls[0]["mode"] == "fred_ra_vintage_asof"
    assert calls[0]["as_of_policy"] == "same_day_vintage"
    assert calls[0]["requested_as_of"] is None
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


@pytest.mark.parametrize("error_type", [TypeError, AttributeError, KeyError])
def test_scan_regimes_reraises_programmer_classifier_errors(error_type):
    def fake_classifier(_fred, **_kwargs):
        raise error_type("classifier contract broke")

    with pytest.raises(error_type, match="classifier contract broke"):
        scan.scan_regimes(
            fred=FakeFred(),
            scan_dates=[date(2024, 9, 5)],
            classifier=fake_classifier,
            classifier_config={},
        )


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


@pytest.mark.parametrize(
    ("window_before", "window_after", "message"),
    [
        (-1, 0, "window_before must be non-negative"),
        (0, -1, "window_after must be non-negative"),
    ],
)
def test_find_transitions_rejects_negative_windows(window_before, window_after, message):
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

    with pytest.raises(ValueError, match=message):
        scan.find_transitions(rows, window_before=window_before, window_after=window_after)


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
            "note": "api_key=fake-secret",
        },
        warnings=["calendar warning", "api_key=fake-secret"],
        artifact_dir=Path("artifacts/macro_regime_scans/test-run"),
    )
    markdown = scan.render_summary_markdown(report)

    assert report["status"] == "blocked"
    assert report["row_count"] == 3
    assert report["status_counts"] == {"blocked": 1, "passed": 2}
    assert report["regime_counts"] == {
        "growth_down_inflation_up": 1,
        "growth_up_inflation_down": 1,
    }
    assert report["transition_count"] == 1
    assert report["first_passed_regime"] == "growth_up_inflation_down"
    assert report["last_passed_regime"] == "growth_down_inflation_up"
    assert report["args_summary"]["note"] == "api_key=<redacted>"
    assert report["warnings"] == ["calendar warning", "api_key=<redacted>"]
    assert "Status counts" in markdown
    assert "blocked" in markdown
    assert "passed" in markdown
    assert "fake-secret" not in markdown


def test_build_scan_report_marks_mixed_passed_failed_as_failed():
    rows = [
        scan.flatten_classifier_result(
            scan_date=date(2024, 9, 5),
            result=passed_result("growth_up_inflation_down"),
            previous_passed_regime=None,
        ),
        scan.flatten_classifier_result(
            scan_date=date(2024, 9, 6),
            result={"status": "failed", "reason": "data unavailable"},
            previous_passed_regime="growth_up_inflation_down",
        ),
    ]

    report = scan.build_scan_report(
        rows=rows,
        transitions=[],
        args_summary={"start": "2024-09-05", "end": "2024-09-06", "calendar": "weekdays"},
        warnings=[],
        artifact_dir=Path("artifacts/macro_regime_scans/mixed-failed"),
    )

    assert report["status"] == "failed"
    assert report["status_counts"] == {"failed": 1, "passed": 1}


def test_build_scan_report_marks_mixed_passed_blocked_as_blocked():
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
    ]

    report = scan.build_scan_report(
        rows=rows,
        transitions=[],
        args_summary={"start": "2024-09-05", "end": "2024-09-06", "calendar": "weekdays"},
        warnings=[],
        artifact_dir=Path("artifacts/macro_regime_scans/mixed-blocked"),
    )

    assert report["status"] == "blocked"
    assert report["status_counts"] == {"blocked": 1, "passed": 1}


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
            "note": "api_key=fake-secret",
        },
        warnings=["api_key=fake-secret"],
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
    metadata_text = paths["metadata_json"].read_text(encoding="utf-8")
    metadata = json.loads(metadata_text)
    markdown = paths["summary_md"].read_text(encoding="utf-8")

    assert daily_rows[0]["date"] == "2024-09-05"
    assert daily_rows[1]["regime_changed"] == "True"
    assert transition_rows[0]["transition_date"] == "2024-09-06"
    assert metadata["transition_count"] == 1
    assert "# Growth / Inflation Regime Scan Report" in markdown
    assert "growth_up_inflation_down -> growth_down_inflation_up" in markdown
    assert "daily_regimes.csv" in markdown
    assert "fake-secret" not in metadata_text
    assert "fake-secret" not in markdown
    assert "api_key=<redacted>" in metadata_text
    assert "api_key=<redacted>" in markdown
    assert "sk-" not in markdown


def test_write_scan_artifacts_redacts_csv_metadata_and_markdown_outputs(tmp_path):
    secret_text = (
        "bad token sk-testFAKE123 api_key=fake-secret token=fake-token "
        "secret=fake-secret password=hunter2 token: colon-secret Bearer bearer-secret"
    )
    rows = [
        scan.flatten_classifier_result(
            scan_date=date(2024, 9, 5),
            result=passed_result("growth_up_inflation_down"),
            previous_passed_regime=None,
        ),
        scan.flatten_classifier_result(
            scan_date=date(2024, 9, 6),
            result=passed_result("growth_down_inflation_up", reason_brief=secret_text),
            previous_passed_regime="growth_up_inflation_down",
        ),
        scan.flatten_classifier_result(
            scan_date=date(2024, 9, 9),
            result={"status": "failed", "reason": secret_text},
            previous_passed_regime="growth_down_inflation_up",
        ),
    ]
    transitions = scan.find_transitions(rows, window_before=1, window_after=1)
    report = scan.build_scan_report(
        rows=rows,
        transitions=transitions,
        args_summary={
            "start": "2024-09-05",
            "end": "2024-09-09",
            "note": secret_text,
            "api_key": "fake-secret",
            "nested": {"token": "sk-testFAKE123"},
        },
        warnings=[secret_text],
        artifact_dir=tmp_path,
    )

    paths = scan.write_scan_artifacts(
        rows=rows,
        transitions=transitions,
        report=report,
        artifact_dir=tmp_path,
    )

    combined_artifacts = "\n".join(
        [
            paths["daily_csv"].read_text(encoding="utf-8"),
            paths["transitions_csv"].read_text(encoding="utf-8"),
            paths["metadata_json"].read_text(encoding="utf-8"),
            paths["summary_md"].read_text(encoding="utf-8"),
        ]
    )

    assert "sk-testFAKE123" not in combined_artifacts
    assert "fake-secret" not in combined_artifacts
    assert "fake-token" not in combined_artifacts
    assert "hunter2" not in combined_artifacts
    assert "colon-secret" not in combined_artifacts
    assert "bearer-secret" not in combined_artifacts
    assert "api_key" in combined_artifacts
    assert "secret" in combined_artifacts
    assert "password" in combined_artifacts
    assert "nested" in combined_artifacts
    assert "token" in combined_artifacts
    assert "<redacted>" in combined_artifacts


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
    artifact_dir = tmp_path / "bad-date-run"

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["artifact_dir"] == str(artifact_dir)
    assert (artifact_dir / "summary.md").exists()
    assert "invalid --start date" in (artifact_dir / "summary.md").read_text(encoding="utf-8")


def test_main_writes_failed_report_for_invalid_calendar_argparse_error(tmp_path, capsys):
    exit_code = scan.main(
        [
            "--start",
            "2024-09-05",
            "--end",
            "2024-09-06",
            "--calendar",
            "moon-days",
            "--output-dir",
            str(tmp_path),
            "--run-id",
            "bad-calendar-run",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    artifact_dir = tmp_path / "bad-calendar-run"
    summary = (artifact_dir / "summary.md").read_text(encoding="utf-8")

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["artifact_dir"] == str(artifact_dir)
    assert (artifact_dir / "daily_regimes.csv").exists()
    assert (artifact_dir / "regime_transitions.csv").exists()
    assert (artifact_dir / "scan_metadata.json").exists()
    assert "invalid choice" in summary
    assert "--calendar" in summary


def test_main_writes_failed_report_for_non_integer_window_argparse_error(tmp_path, capsys):
    exit_code = scan.main(
        [
            "--start",
            "2024-09-05",
            "--end",
            "2024-09-06",
            "--window-before",
            "not-an-int",
            "--output-dir",
            str(tmp_path),
            "--run-id",
            "bad-window-run",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    artifact_dir = tmp_path / "bad-window-run"
    summary = (artifact_dir / "summary.md").read_text(encoding="utf-8")

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["artifact_dir"] == str(artifact_dir)
    assert "--window-before" in summary
    assert "integer" in summary


def test_main_writes_failed_report_for_negative_window_argparse_error(tmp_path, capsys):
    exit_code = scan.main(
        [
            "--start",
            "2024-09-05",
            "--end",
            "2024-09-06",
            "--window-before",
            "-1",
            "--output-dir",
            str(tmp_path),
            "--run-id",
            "negative-window-run",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    artifact_dir = tmp_path / "negative-window-run"
    summary = (artifact_dir / "summary.md").read_text(encoding="utf-8")

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["artifact_dir"] == str(artifact_dir)
    assert "--window-before must be non-negative" in summary


def test_main_writes_failed_report_for_missing_required_args_with_default_run(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(scan, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(scan, "_run_id", lambda: "generated-run")

    exit_code = scan.main(
        [
            "--mode",
            "api_key=fake-secret",
            "--growth-series-id",
            "sk-testFAKE123",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    artifact_dir = tmp_path / "generated-run"
    metadata_text = (artifact_dir / "scan_metadata.json").read_text(encoding="utf-8")
    summary = (artifact_dir / "summary.md").read_text(encoding="utf-8")

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["artifact_dir"] == str(artifact_dir)
    assert "required" in summary
    assert "fake-secret" not in metadata_text
    assert "sk-testFAKE123" not in metadata_text
    assert "<redacted>" in metadata_text


def test_main_redacts_token_secret_password_values_in_parse_failure_metadata(
    tmp_path,
    capsys,
):
    exit_code = scan.main(
        [
            "--mode",
            "token=fake-token",
            "--growth-series-id",
            "secret=fake-secret",
            "--inflation-series-id",
            "password=hunter2",
            "--output-dir",
            str(tmp_path),
            "--run-id",
            "parse-secrets-run",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    artifact_dir = tmp_path / "parse-secrets-run"
    metadata_text = (artifact_dir / "scan_metadata.json").read_text(encoding="utf-8")
    summary = (artifact_dir / "summary.md").read_text(encoding="utf-8")

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["artifact_dir"] == str(artifact_dir)
    assert "required" in summary
    assert "fake-token" not in metadata_text
    assert "fake-secret" not in metadata_text
    assert "hunter2" not in metadata_text
    assert "token=<redacted>" in metadata_text
    assert "secret=<redacted>" in metadata_text
    assert "password=<redacted>" in metadata_text


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("--growth-lag-months", "-1", "--growth-lag-months must be non-negative"),
        ("--inflation-lag-months", "-1", "--inflation-lag-months must be non-negative"),
        ("--trend-years", "0", "--trend-years must be positive"),
    ],
)
def test_main_writes_failed_report_for_invalid_classifier_numeric_option(
    option,
    value,
    message,
    tmp_path,
    capsys,
):
    exit_code = scan.main(
        [
            "--start",
            "2024-09-05",
            "--end",
            "2024-09-06",
            option,
            value,
            "--output-dir",
            str(tmp_path),
            "--run-id",
            "invalid-classifier-number-run",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    artifact_dir = tmp_path / "invalid-classifier-number-run"
    summary = (artifact_dir / "summary.md").read_text(encoding="utf-8")

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["artifact_dir"] == str(artifact_dir)
    assert message in summary


def test_main_writes_top_level_failed_report_for_programmer_classifier_error(
    tmp_path,
    monkeypatch,
    capsys,
):
    class FakeFredClient:
        def __init__(self, *, cache_dir=None):
            self.cache_dir = cache_dir

    def fake_classifier(_fred, **_kwargs):
        raise TypeError("classifier contract broke")

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
            "programmer-error-run",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    artifact_dir = tmp_path / "programmer-error-run"
    summary = (artifact_dir / "summary.md").read_text(encoding="utf-8")
    daily_rows = list(csv.DictReader((artifact_dir / "daily_regimes.csv").open(newline="", encoding="utf-8")))

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["artifact_dir"] == str(artifact_dir)
    assert "TypeError: classifier contract broke" in summary
    assert daily_rows == []


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
