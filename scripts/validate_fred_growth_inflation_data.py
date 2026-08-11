from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


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


def sanitize_sensitive_text(value: Any) -> str:
    text = str(value)
    text = re.sub(r"(?i)\b(api_key|fred_api_key)=([^&\s]+)", r"\1=<redacted>", text)
    for env_name in ("FRED_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        secret = os.environ.get(env_name)
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


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
    missing_realtime_field_count = sum(
        1 for row in dated_rows if row.get("realtime_start") is None or row.get("realtime_end") is None
    )

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
    if source == "fred_api" and observations and missing_realtime_field_count:
        errors.append(f"{missing_realtime_field_count} observations are missing realtime_start or realtime_end")
    if future_count:
        errors.append(f"{future_count} observations are after as_of")
    if not has_enough_history:
        errors.append(f"only {non_null_count} non-null observations; need {config.minimum_non_null_observations}")
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
        "missing_realtime_field_count": missing_realtime_field_count,
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
    blocked = False
    for series_id, result in series_results.items():
        config = REQUIRED_SERIES.get(series_id) or OPTIONAL_SERIES.get(series_id)
        if config is None:
            warnings.append(f"unknown series {series_id} returned status {result.get('status')}")
            continue
        status = result.get("status")
        message = "; ".join(result.get("errors") or [])
        if config.required and status == "blocked":
            blocked = True
            errors.append(f"required series {series_id} blocked: {message}")
        elif config.required and status != "passed":
            errors.append(f"required series {series_id} {status}: {message}")
        elif not config.required and status != "passed":
            warnings.append(f"optional series {series_id} {status}: {message}")
        warnings.extend(result.get("warnings") or [])

    if blocked:
        status = "blocked"
    else:
        status = "passed" if not errors else "failed"
    return {
        "label": label,
        "as_of": as_of.isoformat(),
        "status": status,
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


def build_blocked_report(*, reason: str, artifact_dir: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "blocked",
        "reason": sanitize_sensitive_text(reason),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "toolchain": {
            "client": "lumibot.macro.FREDMacroData",
            "uses_official_fred_api": True,
            "cache_dir": str(Path(artifact_dir) / "fred_cache"),
        },
        "required_series": list(REQUIRED_SERIES),
        "optional_series": list(OPTIONAL_SERIES),
        "as_of_results": [],
    }


def build_failed_report(*, reason: str, artifact_dir: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "failed",
        "reason": sanitize_sensitive_text(reason),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "toolchain": {
            "client": "lumibot.macro.FREDMacroData",
            "uses_official_fred_api": True,
            "cache_dir": str(Path(artifact_dir) / "fred_cache"),
        },
        "required_series": list(REQUIRED_SERIES),
        "optional_series": list(OPTIONAL_SERIES),
        "as_of_results": [],
    }


def load_env_file(path: Path) -> set[str]:
    loaded: set[str] = set()
    if not path.exists():
        return loaded
    expect_fred_value = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            label = re.sub(r"[^a-z0-9]", "", line.lower())
            if label in {"fred", "fredapikey", "fredkey"}:
                expect_fred_value = True
                continue
            if expect_fred_value and re.fullmatch(r"[A-Za-z0-9]{32}", line):
                loaded.add("FRED_API_KEY")
                if "FRED_API_KEY" not in os.environ:
                    os.environ["FRED_API_KEY"] = line
            expect_fred_value = False
            continue
        expect_fred_value = False
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key != "FRED_API_KEY":
            continue
        loaded.add(key)
        if key not in os.environ:
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
        message = sanitize_sensitive_text(exc)
        status = "blocked" if "FRED_API_KEY is required" in message else "failed"
        return _series_error_result(config=config, status=status, message=message)
    except Exception as exc:
        return _series_error_result(
            config=config,
            status="blocked",
            message=sanitize_sensitive_text(f"{type(exc).__name__}: {exc}"),
        )


def _series_error_result(*, config: SeriesCheckConfig, status: str, message: str) -> dict[str, Any]:
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
        "missing_realtime_field_count": None,
        "sample_latest_observations": [],
        "warnings": [],
        "errors": [message],
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
        "generated_at": datetime.now(timezone.utc).isoformat(),
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


def _recommendation_for_status(status: str) -> str:
    if status == "passed":
        return "safe to proceed to Stage 2"
    if status == "blocked":
        return "blocked by missing key, network, quota, or FRED service availability"
    return "cannot proceed because validation failed"


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
        heading = "Blocked Reason" if report.get("status") == "blocked" else "Failure Reason"
        lines.extend([f"## {heading}", "", f"`{reason}`", ""])

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
                ("recent", date.today()),
                ("project_recent_backtest", date(2024, 9, 5)),
                ("older_backtest", date(2010, 1, 1)),
            ]
        )
    return results


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.env_file is not None:
        load_env_file(args.env_file)

    run_id = args.run_id or _run_id()
    artifact_dir = args.artifact_root / run_id
    cache_dir = artifact_dir / "fred_cache"
    os.environ["LUMIBOT_FRED_CACHE_DIR"] = str(cache_dir)

    try:
        as_of_dates = build_as_of_dates(args.as_of, include_recent=args.include_recent)
    except ValueError as exc:
        report = build_failed_report(reason=str(exc), artifact_dir=str(artifact_dir))
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
        return 1

    if not os.environ.get("FRED_API_KEY"):
        report = build_blocked_report(reason="missing_fred_api_key", artifact_dir=str(artifact_dir))
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
        return 2

    from lumibot.macro import FREDMacroData

    fred = FREDMacroData(cache_dir=cache_dir, min_request_interval_seconds=0.2)
    try:
        report = collect_availability_report(
            fred=fred,
            as_of_dates=as_of_dates,
            artifact_dir=artifact_dir,
            include_optional=args.include_optional,
        )
    except Exception as exc:
        report = build_blocked_report(
            reason=sanitize_sensitive_text(f"{type(exc).__name__}: {exc}"),
            artifact_dir=str(artifact_dir),
        )

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
