from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lumibot.example_strategies.fred_growth_inflation_regime_classifier import (  # noqa: E402
    DEFAULT_AS_OF_POLICY,
    DEFAULT_MODE,
    LEGACY_LAGGED_MODE,
)
from scripts.validate_fred_growth_inflation_data import load_env_file, sanitize_sensitive_text  # noqa: E402

ARTIFACT_ROOT = Path("artifacts") / "macro_regime_scans"
CALENDAR_MODES = ("calendar-days", "weekdays", "trading-days")
DEFAULT_GROWTH_SERIES_ID = "GDPC1"
DEFAULT_INFLATION_SERIES_ID = "CPIAUCSL"
DEFAULT_GROWTH_LAG_MONTHS = 6
DEFAULT_INFLATION_LAG_MONTHS = 1
DEFAULT_TREND_YEARS = 5
DAILY_COLUMNS = (
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
)
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

__all__ = (
    "ARTIFACT_ROOT",
    "CALENDAR_MODES",
    "DAILY_COLUMNS",
    "DEFAULT_AS_OF_POLICY",
    "DEFAULT_GROWTH_LAG_MONTHS",
    "DEFAULT_GROWTH_SERIES_ID",
    "DEFAULT_INFLATION_LAG_MONTHS",
    "DEFAULT_INFLATION_SERIES_ID",
    "DEFAULT_MODE",
    "DEFAULT_TREND_YEARS",
    "FREDMacroData",
    "LEGACY_LAGGED_MODE",
    "REPO_ROOT",
    "TRANSITION_COLUMNS",
    "build_date_grid",
    "build_scan_report",
    "classify_growth_inflation_regime",
    "flatten_classifier_result",
    "find_transitions",
    "get_nyse_trading_days",
    "load_env_file",
    "main",
    "parse_args",
    "parse_date_argument",
    "redact_for_artifact",
    "render_summary_markdown",
    "sanitize_sensitive_text",
    "scan_regimes",
    "write_scan_artifacts",
)

_REAL_CLASSIFIER: Callable[..., dict[str, Any]] | None = None
_REAL_FRED_MACRO_DATA: Any | None = None
PROGRAMMER_ERROR_TYPES = (TypeError, AttributeError, KeyError)


def classify_growth_inflation_regime(fred: Any, **kwargs: Any) -> dict[str, Any]:
    global _REAL_CLASSIFIER
    if _REAL_CLASSIFIER is None:
        from lumibot.example_strategies.fred_growth_inflation_regime_classifier import (
            classify_growth_inflation_regime as real_classifier,
        )

        _REAL_CLASSIFIER = real_classifier
    return _REAL_CLASSIFIER(fred, **kwargs)


def FREDMacroData(*args: Any, **kwargs: Any) -> Any:
    global _REAL_FRED_MACRO_DATA
    if _REAL_FRED_MACRO_DATA is None:
        from lumibot.macro import FREDMacroData as real_fred_macro_data

        _REAL_FRED_MACRO_DATA = real_fred_macro_data
    return _REAL_FRED_MACRO_DATA(*args, **kwargs)


def _non_negative_int_value(value: Any, *, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed


def _non_negative_int_arg(option_name: str) -> Callable[[str], int]:
    def parse_value(value: str) -> int:
        try:
            return _non_negative_int_value(value, name=option_name)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc

    return parse_value


def _positive_int_value(value: Any, *, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def _positive_int_arg(option_name: str) -> Callable[[str], int]:
    def parse_value(value: str) -> int:
        try:
            return _positive_int_value(value, name=option_name)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc

    return parse_value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan FRED Growth / Inflation regimes over a date range.")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--calendar", choices=sorted(CALENDAR_MODES), default="trading-days")
    parser.add_argument("--window-before", type=_non_negative_int_arg("--window-before"), default=5)
    parser.add_argument("--window-after", type=_non_negative_int_arg("--window-after"), default=5)
    parser.add_argument("--output-dir", type=Path, default=ARTIFACT_ROOT)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--mode", default=DEFAULT_MODE)
    parser.add_argument("--as-of-policy", default=DEFAULT_AS_OF_POLICY)
    parser.add_argument("--requested-as-of", default=None)
    parser.add_argument("--growth-series-id", default=DEFAULT_GROWTH_SERIES_ID)
    parser.add_argument("--inflation-series-id", default=DEFAULT_INFLATION_SERIES_ID)
    parser.add_argument(
        "--growth-lag-months",
        type=_non_negative_int_arg("--growth-lag-months"),
        default=DEFAULT_GROWTH_LAG_MONTHS,
    )
    parser.add_argument(
        "--inflation-lag-months",
        type=_non_negative_int_arg("--inflation-lag-months"),
        default=DEFAULT_INFLATION_LAG_MONTHS,
    )
    parser.add_argument("--trend-years", type=_positive_int_arg("--trend-years"), default=DEFAULT_TREND_YEARS)
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
        "as_of_policy": args.as_of_policy,
        "requested_as_of": args.requested_as_of,
        "growth_series_id": args.growth_series_id,
        "inflation_series_id": args.inflation_series_id,
        "growth_lag_months": args.growth_lag_months,
        "inflation_lag_months": args.inflation_lag_months,
        "trend_years": args.trend_years,
    }


def _classifier_config(args: argparse.Namespace) -> dict[str, Any]:
    config = {
        "mode": args.mode,
        "as_of_policy": args.as_of_policy,
        "requested_as_of": args.requested_as_of,
        "growth_series_id": args.growth_series_id,
        "inflation_series_id": args.inflation_series_id,
        "trend_years": args.trend_years,
    }
    if args.mode == LEGACY_LAGGED_MODE:
        config["growth_lag_months"] = args.growth_lag_months
        config["inflation_lag_months"] = args.inflation_lag_months
    return config


def parse_date_argument(value: Any, *, name: str) -> date:
    text = str(value or "").strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"invalid {name} date: {value}") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"invalid {name} date: {value}")
    return parsed


def _calendar_days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _weekdays(start: date, end: date) -> list[date]:
    return [value for value in _calendar_days(start, end) if value.weekday() < 5]


def get_nyse_trading_days(start: date, end: date) -> list[date]:
    from lumibot.tools import get_trading_days

    schedule = get_trading_days(
        market="NYSE",
        start_date=start.isoformat(),
        end_date=(end + timedelta(days=1)).isoformat(),
    )
    return [timestamp.date() for timestamp in schedule.index]


def _trading_days(start: date, end: date) -> tuple[list[date], list[str]]:
    try:
        return get_nyse_trading_days(start, end), []
    except Exception as exc:
        dates = _weekdays(start, end)
        message = sanitize_sensitive_text(f"{type(exc).__name__}: {exc}")
        warning = f"trading-days calendar failed; fell back to weekdays: {message}"
        return dates, [warning]


def build_date_grid(
    start: date,
    end: date,
    *,
    calendar_mode: str,
) -> tuple[list[date], list[str]]:
    if calendar_mode not in CALENDAR_MODES:
        expected = ", ".join(CALENDAR_MODES)
        raise ValueError(f"invalid calendar mode {calendar_mode!r}; expected one of: {expected}")
    if start > end:
        raise ValueError("start date must be on or before end date")
    if calendar_mode == "calendar-days":
        return _calendar_days(start, end), []
    if calendar_mode == "weekdays":
        return _weekdays(start, end), []
    return _trading_days(start, end)


def _nested_get(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _weight(result: dict[str, Any], key: str) -> Any:
    return _nested_get(result, "basket_weights", key)


def _first_data_quality_error(result: dict[str, Any]) -> Any:
    errors = _nested_get(result, "data_quality", "errors")
    if isinstance(errors, (list, tuple)):
        return errors[0] if errors else ""
    return errors or ""


def flatten_classifier_result(
    scan_date: date,
    result: dict[str, Any],
    previous_passed_regime: str | None,
) -> dict[str, Any]:
    row: dict[str, Any] = {column: "" for column in DAILY_COLUMNS}
    status = str(result.get("status") or "")
    is_passed = status == "passed"
    previous_regime = previous_passed_regime or ""

    row.update(
        {
            "date": scan_date.isoformat(),
            "status": status,
            "previous_passed_regime": previous_regime,
            "regime_changed": False,
        }
    )

    if not is_passed:
        reason = result.get("reason") or _first_data_quality_error(result)
        row["error_reason"] = sanitize_sensitive_text(reason) if reason else ""
        return row

    regime = str(result.get("regime") or "")
    row.update(
        {
            "regime": regime,
            "growth_direction": result.get("growth_direction") or "",
            "inflation_direction": result.get("inflation_direction") or "",
            "equity_weight": _weight(result, "equity"),
            "commodity_weight": _weight(result, "commodity"),
            "tips_weight": _weight(result, "tips"),
            "nominal_bond_weight": _weight(result, "nominal_bond"),
            "regime_changed": previous_passed_regime is not None and previous_passed_regime != regime,
            "growth_metric_value": _nested_get(result, "growth_evidence", "metric_value"),
            "growth_trend_value": _nested_get(result, "growth_evidence", "trend_value"),
            "growth_margin": _nested_get(result, "growth_evidence", "margin"),
            "growth_latest_observation_date": _nested_get(
                result,
                "growth_evidence",
                "latest_observation_date",
            ),
            "growth_data_cutoff": _nested_get(result, "growth_evidence", "data_cutoff"),
            "inflation_metric_value": _nested_get(result, "inflation_evidence", "metric_value"),
            "inflation_trend_value": _nested_get(result, "inflation_evidence", "trend_value"),
            "inflation_margin": _nested_get(result, "inflation_evidence", "margin"),
            "inflation_latest_observation_date": _nested_get(
                result,
                "inflation_evidence",
                "latest_observation_date",
            ),
            "inflation_data_cutoff": _nested_get(result, "inflation_evidence", "data_cutoff"),
            "confidence_level": _nested_get(result, "confidence", "level") or "",
            "reason_brief": result.get("reason_brief") or "",
        }
    )
    return row


def _failed_row_for_exception(
    scan_date: date,
    exc: Exception,
    previous_passed_regime: str | None,
) -> dict[str, Any]:
    return flatten_classifier_result(
        scan_date=scan_date,
        result={
            "status": "failed",
            "reason": f"{type(exc).__name__}: {exc}",
        },
        previous_passed_regime=previous_passed_regime,
    )


def scan_regimes(
    fred: FREDMacroData,
    scan_dates: list[date],
    classifier: Callable[..., dict[str, Any]] | None = None,
    classifier_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    config = dict(classifier_config or {})
    active_classifier = classifier or classify_growth_inflation_regime
    rows: list[dict[str, Any]] = []
    previous_passed_regime: str | None = None

    for scan_date in scan_dates:
        try:
            result = active_classifier(
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
        except PROGRAMMER_ERROR_TYPES:
            raise
        except Exception as exc:
            row = _failed_row_for_exception(
                scan_date=scan_date,
                exc=exc,
                previous_passed_regime=previous_passed_regime,
            )

        rows.append(row)
        if row["status"] == "passed" and row["regime"]:
            previous_passed_regime = row["regime"]

    return rows


def _weights_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "equity": row.get("equity_weight"),
        "commodity": row.get("commodity_weight"),
        "tips": row.get("tips_weight"),
        "nominal_bond": row.get("nominal_bond_weight"),
    }


def _compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def find_transitions(
    rows: list[dict[str, Any]],
    window_before: int,
    window_after: int,
) -> list[dict[str, Any]]:
    window_before = _non_negative_int_value(window_before, name="window_before")
    window_after = _non_negative_int_value(window_after, name="window_after")
    transitions: list[dict[str, Any]] = []
    previous_passed_row: dict[str, Any] | None = None

    for index, row in enumerate(rows):
        regime = row.get("regime") or ""
        if row.get("status") != "passed" or not regime:
            continue

        if previous_passed_row is not None and previous_passed_row["regime"] != regime:
            start_index = max(0, index - window_before)
            end_index = min(len(rows) - 1, index + window_after)
            transitions.append(
                {
                    "transition_date": row["date"],
                    "previous_scan_date": previous_passed_row["date"],
                    "from_regime": previous_passed_row["regime"],
                    "to_regime": regime,
                    "from_weights": _compact_json(_weights_from_row(previous_passed_row)),
                    "to_weights": _compact_json(_weights_from_row(row)),
                    "suggested_start_date": rows[start_index]["date"],
                    "suggested_end_date": rows[end_index]["date"],
                    "growth_margin": row.get("growth_margin"),
                    "inflation_margin": row.get("inflation_margin"),
                    "confidence_level": row.get("confidence_level"),
                    "reason_brief": row.get("reason_brief"),
                }
            )

        previous_passed_row = row

    return transitions


def _sorted_counts(values: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "")
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


_REDACTED_VALUE = "<redacted>"
_SECRET_TOKEN_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]+")
_SENSITIVE_PAIR_PATTERN = re.compile(
    r"\b(api[_-]?key|fred[_-]?api[_-]?key|token|secret|password)(\s*[:=]\s*)([^\s,;]+)",
    re.IGNORECASE,
)
_BEARER_TOKEN_PATTERN = re.compile(r"\bbearer\s+[^\s,;]+", re.IGNORECASE)
_SENSITIVE_KEY_PARTS = ("api_key", "token", "secret", "password")


def _redact_text(value: Any) -> str:
    text = sanitize_sensitive_text(value)
    text = _SENSITIVE_PAIR_PATTERN.sub(r"\1\2" + _REDACTED_VALUE, text)
    text = _BEARER_TOKEN_PATTERN.sub("Bearer " + _REDACTED_VALUE, text)
    return _SECRET_TOKEN_PATTERN.sub(_REDACTED_VALUE, text)


def _is_sensitive_key(value: Any) -> bool:
    text = str(value).lower()
    return any(part in text for part in _SENSITIVE_KEY_PARTS)


def redact_for_artifact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _REDACTED_VALUE if _is_sensitive_key(key) else redact_for_artifact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_for_artifact(item) for item in value]
    if isinstance(value, tuple):
        return [redact_for_artifact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def build_scan_report(
    rows: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    args_summary: dict[str, Any],
    warnings: list[str],
    artifact_dir: Path,
) -> dict[str, Any]:
    passed_rows = [row for row in rows if row.get("status") == "passed"]
    passed_regime_rows = [row for row in passed_rows if row.get("regime")]
    has_failed = any(row.get("status") == "failed" for row in rows)
    has_blocked = any(row.get("status") == "blocked" for row in rows)

    if has_failed:
        status = "failed"
    elif has_blocked:
        status = "blocked"
    elif passed_rows:
        status = "passed"
    else:
        status = "failed"

    artifact_dir = Path(artifact_dir)
    return {
        "status": status,
        "row_count": len(rows),
        "status_counts": _sorted_counts([row.get("status") for row in rows]),
        "regime_counts": _sorted_counts([row.get("regime") for row in passed_regime_rows]),
        "transition_count": len(transitions),
        "first_passed_regime": passed_regime_rows[0]["regime"] if passed_regime_rows else "",
        "last_passed_regime": passed_regime_rows[-1]["regime"] if passed_regime_rows else "",
        "args_summary": redact_for_artifact(args_summary),
        "warnings": redact_for_artifact(warnings),
        "artifact_dir": str(artifact_dir),
        "artifact_paths": {
            "daily_csv": str(artifact_dir / "daily_regimes.csv"),
            "transitions_csv": str(artifact_dir / "regime_transitions.csv"),
            "summary_md": str(artifact_dir / "summary.md"),
            "metadata_json": str(artifact_dir / "scan_metadata.json"),
        },
        "transitions": redact_for_artifact(transitions),
    }


def _markdown_value(value: Any) -> str:
    text = _redact_text(value)
    return text.replace("\n", " ").replace("|", "\\|")


def render_summary_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Growth / Inflation Regime Scan Report",
        "",
        "## Status",
        f"- Status: `{_markdown_value(report.get('status', ''))}`",
        f"- Daily rows: `{report.get('row_count', 0)}`",
        f"- Status counts: `{_markdown_value(report.get('status_counts', {}))}`",
        f"- Transitions: `{report.get('transition_count', 0)}`",
        f"- First passed regime: `{_markdown_value(report.get('first_passed_regime') or 'n/a')}`",
        f"- Last passed regime: `{_markdown_value(report.get('last_passed_regime') or 'n/a')}`",
        "",
    ]

    if not report.get("first_passed_regime"):
        lines.extend(["No passed regime rows were available.", ""])

    lines.extend(["## Arguments"])
    args_summary = report.get("args_summary") or {}
    if args_summary:
        for key, value in sorted(args_summary.items()):
            lines.append(f"- `{_markdown_value(key)}`: `{_markdown_value(value)}`")
    else:
        lines.append("- None")
    lines.append("")

    lines.extend(["## Warnings"])
    warnings = report.get("warnings") or []
    if warnings:
        for warning in warnings:
            lines.append(f"- {_markdown_value(warning)}")
    else:
        lines.append("- None")
    lines.append("")

    lines.extend(["## Regime Counts"])
    regime_counts = report.get("regime_counts") or {}
    if regime_counts:
        for regime, count in regime_counts.items():
            lines.append(f"- `{_markdown_value(regime)}`: `{count}`")
    else:
        lines.append("- None")
    lines.append("")

    lines.extend(["## Transitions"])
    transitions = report.get("transitions") or []
    if transitions:
        lines.extend(
            [
                "| Transition date | Regime change | Suggested window | Confidence |",
                "| --- | --- | --- | --- |",
            ]
        )
        for transition in transitions:
            regime_change = (
                f"{_markdown_value(transition.get('from_regime', ''))} -> "
                f"{_markdown_value(transition.get('to_regime', ''))}"
            )
            suggested_window = (
                f"{_markdown_value(transition.get('suggested_start_date', ''))} to "
                f"{_markdown_value(transition.get('suggested_end_date', ''))}"
            )
            lines.append(
                "| "
                f"{_markdown_value(transition.get('transition_date', ''))} | "
                f"{regime_change} | "
                f"{suggested_window} | "
                f"{_markdown_value(transition.get('confidence_level', ''))} |"
            )
    else:
        lines.append("No regime transitions were detected.")
    lines.append("")

    lines.extend(["## Artifacts"])
    artifact_paths = report.get("artifact_paths") or {}
    for key in ("daily_csv", "transitions_csv", "summary_md", "metadata_json"):
        if key in artifact_paths:
            lines.append(f"- `{key}`: `{_markdown_value(artifact_paths[key])}`")
    if not artifact_paths:
        lines.append("- None")

    return "\n".join(lines).rstrip() + "\n"


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: tuple[str, ...] | list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_scan_artifacts(
    rows: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    report: dict[str, Any],
    artifact_dir: Path,
) -> dict[str, Path]:
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "daily_csv": artifact_dir / "daily_regimes.csv",
        "transitions_csv": artifact_dir / "regime_transitions.csv",
        "summary_md": artifact_dir / "summary.md",
        "metadata_json": artifact_dir / "scan_metadata.json",
    }
    safe_rows = redact_for_artifact(rows)
    safe_transitions = redact_for_artifact(transitions)
    safe_report = redact_for_artifact(report)

    _write_csv(paths["daily_csv"], safe_rows, DAILY_COLUMNS)
    _write_csv(paths["transitions_csv"], safe_transitions, TRANSITION_COLUMNS)
    paths["summary_md"].write_text(render_summary_markdown(safe_report), encoding="utf-8")
    paths["metadata_json"].write_text(
        json.dumps(safe_report, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    return paths


def _failure_artifacts(
    artifact_dir: Path,
    args_summary: dict[str, Any],
    reason: str,
    status: str = "failed",
) -> dict[str, Path]:
    safe_reason = _redact_text(reason)
    report = build_scan_report(
        rows=[],
        transitions=[],
        args_summary=args_summary,
        warnings=[safe_reason],
        artifact_dir=artifact_dir,
    )
    report["status"] = status
    report["reason"] = safe_reason
    return write_scan_artifacts(
        rows=[],
        transitions=[],
        report=report,
        artifact_dir=artifact_dir,
    )


def _stdout_warning(captured_stdout: str) -> str:
    text = captured_stdout.strip()
    if not text:
        return ""
    return f"runtime stdout redirected away from CLI JSON: {_redact_text(text)}"


def _payload_for_paths(
    *,
    status: str,
    artifact_dir: Path,
    paths: dict[str, Path],
    transition_count: int,
) -> dict[str, Any]:
    return {
        "status": status,
        "artifact_dir": str(artifact_dir),
        "daily_csv": str(paths["daily_csv"]),
        "transitions_csv": str(paths["transitions_csv"]),
        "summary": str(paths["summary_md"]),
        "metadata": str(paths["metadata_json"]),
        "transition_count": transition_count,
    }


def _print_json_payload(payload: dict[str, Any]) -> None:
    print(json.dumps(redact_for_artifact(payload), sort_keys=True, separators=(",", ":")))


def _argv_list(argv: list[str] | None) -> list[str]:
    source = sys.argv[1:] if argv is None else argv
    return [str(item) for item in source]


def _argv_option_value(argv: list[str], option_name: str) -> str | None:
    prefix = f"{option_name}="
    for index, item in enumerate(argv):
        if item.startswith(prefix):
            return item[len(prefix) :]
        if item == option_name and index + 1 < len(argv):
            next_item = argv[index + 1]
            if not next_item.startswith("--"):
                return next_item
    return None


def _failure_artifact_dir_from_argv(argv: list[str]) -> Path:
    output_dir = Path(_argv_option_value(argv, "--output-dir") or ARTIFACT_ROOT)
    run_id = _argv_option_value(argv, "--run-id") or _run_id()
    return output_dir / run_id


def _parse_failure_args_summary(argv: list[str]) -> dict[str, Any]:
    return {
        "parse_failed": True,
        "argv": argv,
    }


def _system_exit_code(exc: SystemExit) -> int:
    if isinstance(exc.code, int):
        return exc.code
    if exc.code is None:
        return 0
    return 1


def _argparse_failure_reason(stderr_text: str, exit_code: int) -> str:
    reason = stderr_text.strip() or f"argument parsing failed with exit code {exit_code}"
    return _redact_text(reason)


def main(argv: list[str] | None = None) -> int:
    raw_argv = _argv_list(argv)
    parse_stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(parse_stderr):
            args = parse_args(raw_argv)
    except SystemExit as exc:
        exit_code = _system_exit_code(exc)
        if exit_code == 0:
            return 0
        artifact_dir = _failure_artifact_dir_from_argv(raw_argv)
        paths = _failure_artifacts(
            artifact_dir=artifact_dir,
            args_summary=_parse_failure_args_summary(raw_argv),
            reason=_argparse_failure_reason(parse_stderr.getvalue(), exit_code),
        )
        _print_json_payload(
            _payload_for_paths(
                status="failed",
                artifact_dir=artifact_dir,
                paths=paths,
                transition_count=0,
            )
        )
        return 1

    run_id = args.run_id or _run_id()
    artifact_dir = args.output_dir / run_id
    cache_dir = artifact_dir / "fred_cache"
    args_summary = _args_summary(args)
    captured_stdout = io.StringIO()
    warnings: list[str] = []
    grid_warnings: list[str] = []

    try:
        with contextlib.redirect_stdout(captured_stdout):
            if args.env_file:
                load_env_file(args.env_file)
            os.environ["LUMIBOT_FRED_CACHE_DIR"] = str(cache_dir)
            start = parse_date_argument(args.start, name="--start")
            end = parse_date_argument(args.end, name="--end")
            scan_dates, grid_warnings = build_date_grid(
                start,
                end,
                calendar_mode=args.calendar,
            )
            fred = FREDMacroData(cache_dir=cache_dir)
            rows = scan_regimes(
                fred=fred,
                scan_dates=scan_dates,
                classifier=classify_growth_inflation_regime,
                classifier_config=_classifier_config(args),
            )
            transitions = find_transitions(
                rows,
                window_before=args.window_before,
                window_after=args.window_after,
            )
    except ValueError as exc:
        reason = str(exc)
        stdout_warning = _stdout_warning(captured_stdout.getvalue())
        if stdout_warning:
            reason = f"{reason}; {stdout_warning}"
        paths = _failure_artifacts(
            artifact_dir=artifact_dir,
            args_summary=args_summary,
            reason=reason,
        )
        _print_json_payload(
            _payload_for_paths(
                status="failed",
                artifact_dir=artifact_dir,
                paths=paths,
                transition_count=0,
            )
        )
        return 1
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        stdout_warning = _stdout_warning(captured_stdout.getvalue())
        if stdout_warning:
            reason = f"{reason}; {stdout_warning}"
        paths = _failure_artifacts(
            artifact_dir=artifact_dir,
            args_summary=args_summary,
            reason=reason,
        )
        _print_json_payload(
            _payload_for_paths(
                status="failed",
                artifact_dir=artifact_dir,
                paths=paths,
                transition_count=0,
            )
        )
        return 1

    warnings.extend(grid_warnings)
    stdout_warning = _stdout_warning(captured_stdout.getvalue())
    if stdout_warning:
        warnings.append(stdout_warning)

    report = build_scan_report(
        rows=rows,
        transitions=transitions,
        args_summary=args_summary,
        warnings=warnings,
        artifact_dir=artifact_dir,
    )
    paths = write_scan_artifacts(
        rows=rows,
        transitions=transitions,
        report=report,
        artifact_dir=artifact_dir,
    )
    _print_json_payload(
        _payload_for_paths(
            status=report["status"],
            artifact_dir=artifact_dir,
            paths=paths,
            transition_count=report["transition_count"],
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
