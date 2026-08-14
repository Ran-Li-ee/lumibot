import calendar
import math
import re
from datetime import date, datetime
from typing import Any, Callable

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
GROWTH_OBSERVATIONS_PER_YEAR = 4
GROWTH_TREND_OBSERVATIONS = DEFAULT_TREND_YEARS * GROWTH_OBSERVATIONS_PER_YEAR
INFLATION_PERIODS_BACK = 12
INFLATION_OBSERVATIONS_PER_YEAR = 12
INFLATION_TREND_OBSERVATIONS = DEFAULT_TREND_YEARS * INFLATION_OBSERVATIONS_PER_YEAR
PROGRAMMER_ERROR_TYPES = (TypeError, AttributeError, KeyError)
EXPECTED_RUNTIME_DATA_ERROR_TYPES = (ValueError, ArithmeticError, RuntimeError, OSError)


def parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value or "").strip()
    if not text:
        raise ValueError("date is required.")

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(f"date must be ISO or YYYY-MM-DD, got {value!r}.") from exc


def subtract_months(value: date, months: int) -> date:
    parsed = parse_date(value)
    month_index = parsed.year * 12 + parsed.month - 1 - int(months)
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(parsed.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def history_start_for_date(value: date, years: int = 12) -> str:
    parsed = parse_date(value)
    return date(parsed.year - int(years), parsed.month, 1).isoformat()


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text or text == ".":
        return None

    try:
        number = float(text)
    except ValueError:
        return None

    if not math.isfinite(number):
        return None
    return number


def _sanitize_reason(value: Any) -> str:
    text = str(value)
    text = re.sub(r"(?i)(api[_-]?key\s*=\s*)[^&\s]+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(api[_-]?key\s*:\s*)[^,\s]+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(api[_-]?key\s+)[A-Za-z0-9_.-]{12,}", r"\1<redacted>", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9_.-]{12,}", r"\1<redacted>", text)
    return re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-<redacted>", text)


def _observations_from_payload(
    payload: dict[str, Any],
    trading_date: date,
    data_cutoff: date,
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("FRED payload must be an object.")

    errors = []
    if payload.get("source") != "fred_api":
        errors.append(f"unexpected source: {payload.get('source')!r}")
    if payload.get("point_in_time_safe") is not True:
        errors.append("point_in_time_safe is not true")
    if payload.get("uses_revised_data") is not False:
        errors.append("uses_revised_data is not false")
    if errors:
        raise ValueError(_sanitize_reason("; ".join(errors)))

    parsed_trading_date = parse_date(trading_date)
    parsed_data_cutoff = parse_date(data_cutoff)
    rows = payload.get("observations") or []
    if not isinstance(rows, list):
        raise ValueError("FRED observations must be a list.")

    observations = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("FRED observation must be an object.")

        obs_date = parse_date(row.get("date"))
        if obs_date > parsed_trading_date:
            message = (
                f"FRED observation date {obs_date.isoformat()} is after trading date "
                f"{parsed_trading_date.isoformat()}."
            )
            raise ValueError(message)

        value = _safe_float(row.get("value"))
        if obs_date <= parsed_data_cutoff and value is not None:
            observations.append(
                {
                    "date": obs_date,
                    "value": value,
                    "realtime_start": row.get("realtime_start"),
                    "realtime_end": row.get("realtime_end"),
                }
            )

    observations.sort(key=lambda observation: observation["date"])
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
    parsed_trading_date = parse_date(trading_date)
    lag_months = int(lag_months)
    periods_back = int(periods_back)
    trend_window_observations = int(trend_window_observations)
    trend_years = int(trend_years)
    if lag_months < 0:
        raise ValueError("lag_months must be >= 0.")
    if periods_back <= 0:
        raise ValueError("periods_back must be > 0.")
    if trend_window_observations <= 0:
        raise ValueError("trend_window_observations must be > 0.")
    if trend_years <= 0:
        raise ValueError("trend_years must be > 0.")

    data_cutoff = subtract_months(parsed_trading_date, lag_months)
    observations = _observations_from_payload(payload, parsed_trading_date, data_cutoff)
    minimum_observations = periods_back + trend_window_observations
    if len(observations) < minimum_observations:
        message = (
            f"{axis} needs at least {minimum_observations} usable observations, "
            f"got {len(observations)}."
        )
        raise ValueError(message)

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

    return {
        "axis": axis,
        "series_id": payload.get("series_id"),
        "series_name": series_name,
        "frequency": frequency,
        "lag_months": lag_months,
        "data_cutoff": data_cutoff.isoformat(),
        "latest_observation_date": latest_metric["observation"]["date"].isoformat(),
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


def regime_from_directions(growth_direction: str, inflation_direction: str) -> str:
    regime_by_directions = {
        ("up", "down"): "growth_up_inflation_down",
        ("up", "up"): "growth_up_inflation_up",
        ("down", "up"): "growth_down_inflation_up",
        ("down", "down"): "growth_down_inflation_down",
    }
    try:
        return regime_by_directions[(growth_direction, inflation_direction)]
    except KeyError as exc:
        raise ValueError(
            f"unsupported regime directions: growth={growth_direction!r}, "
            f"inflation={inflation_direction!r}."
        ) from exc


def _status_date_text(value: Any) -> str:
    try:
        return parse_date(value).isoformat()
    except ValueError:
        text = _sanitize_reason(value).strip()
        return text or "unknown"


def _message_list(messages: Any) -> list[str]:
    if messages is None:
        return []
    if isinstance(messages, (list, tuple)):
        values = messages
    else:
        values = [messages]
    return [_sanitize_reason(value) for value in values if str(value).strip()]


def _status_result(
    date: Any,
    mode: str,
    status: str,
    reason: str,
    errors: Any,
    warnings: Any = None,
    *,
    growth_series_id: str = DEFAULT_GROWTH_SERIES_ID,
    inflation_series_id: str = DEFAULT_INFLATION_SERIES_ID,
) -> dict[str, Any]:
    date_text = _status_date_text(date)
    return {
        "tool": "macro_regime_classifier",
        "status": status,
        "mock": False,
        "mode": str(mode),
        "date": date_text,
        "as_of": date_text,
        "reason": str(reason),
        "data_quality": {
            "status": status,
            "source": "fred_api",
            "point_in_time_safe": None,
            "uses_revised_data": None,
            "required_series": [growth_series_id, inflation_series_id],
            "warnings": _message_list(warnings),
            "errors": _message_list(errors),
        },
    }


def _blocked_result(
    date: Any,
    mode: str,
    reason: str,
    errors: Any,
    warnings: Any = None,
    *,
    growth_series_id: str = DEFAULT_GROWTH_SERIES_ID,
    inflation_series_id: str = DEFAULT_INFLATION_SERIES_ID,
) -> dict[str, Any]:
    return _status_result(
        date,
        mode,
        "blocked",
        reason,
        errors,
        warnings,
        growth_series_id=growth_series_id,
        inflation_series_id=inflation_series_id,
    )


def _failed_result(
    date: Any,
    mode: str,
    reason: str,
    errors: Any,
    warnings: Any = None,
    *,
    growth_series_id: str = DEFAULT_GROWTH_SERIES_ID,
    inflation_series_id: str = DEFAULT_INFLATION_SERIES_ID,
) -> dict[str, Any]:
    return _status_result(
        date,
        mode,
        "failed",
        reason,
        errors,
        warnings,
        growth_series_id=growth_series_id,
        inflation_series_id=inflation_series_id,
    )


def _confidence_from_margins(
    growth_evidence: dict[str, Any],
    inflation_evidence: dict[str, Any],
) -> dict[str, Any]:
    growth_margin = _safe_float(growth_evidence.get("margin"))
    inflation_margin = _safe_float(inflation_evidence.get("margin"))
    if growth_margin is None or inflation_margin is None:
        return {
            "level": "not_scored",
            "basis": "axis margins were unavailable",
            "growth_margin": growth_margin,
            "inflation_margin": inflation_margin,
        }

    smallest_margin = min(abs(growth_margin), abs(inflation_margin))
    if smallest_margin < 0.001:
        level = "low"
    elif smallest_margin < 0.005:
        level = "medium"
    else:
        level = "high"

    return {
        "level": level,
        "basis": "absolute axis margin thresholds: low < 0.001, medium < 0.005, high otherwise",
        "growth_margin": growth_margin,
        "inflation_margin": inflation_margin,
    }


def _classification_failure_reason(exc: Exception) -> str:
    if isinstance(exc, (ValueError, ArithmeticError)):
        return "data_validation_failed"
    return "classification_failed"


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
) -> dict[str, Any]:
    status_series_kwargs = {
        "growth_series_id": growth_series_id,
        "inflation_series_id": inflation_series_id,
    }
    try:
        trading_date = parse_date(date)
    except ValueError as exc:
        return _failed_result(date, mode, "data_validation_failed", [exc], **status_series_kwargs)

    date_text = trading_date.isoformat()
    if mode != DEFAULT_MODE:
        message = f"Unsupported mode {mode!r}; supported mode is {DEFAULT_MODE!r}."
        return _failed_result(date_text, mode, "unsupported_mode", [message], **status_series_kwargs)

    try:
        trend_years = int(trend_years)
        history_start = history_start_for_date(trading_date)
        growth_payload = fred.get_series(
            growth_series_id,
            start=history_start,
            end=date_text,
            as_of=date_text,
        )
        inflation_payload = fred.get_series(
            inflation_series_id,
            start=history_start,
            end=date_text,
            as_of=date_text,
        )
        growth_evidence = calculate_axis_evidence(
            growth_payload,
            axis="growth",
            series_name="Real Gross Domestic Product",
            frequency="quarterly",
            trading_date=trading_date,
            lag_months=growth_lag_months,
            periods_back=GROWTH_PERIODS_BACK,
            trend_window_observations=trend_years * GROWTH_OBSERVATIONS_PER_YEAR,
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
            trend_window_observations=trend_years * INFLATION_OBSERVATIONS_PER_YEAR,
            trend_years=trend_years,
        )
        regime = regime_from_directions(growth_evidence["direction"], inflation_evidence["direction"])
    except PROGRAMMER_ERROR_TYPES:
        raise
    except EXPECTED_RUNTIME_DATA_ERROR_TYPES as exc:
        message = _sanitize_reason(exc)
        if "FRED_API_KEY" in message:
            return _blocked_result(date_text, mode, "missing_fred_api_key", [message], **status_series_kwargs)
        return _failed_result(
            date_text,
            mode,
            _classification_failure_reason(exc),
            [message],
            **status_series_kwargs,
        )

    return {
        "tool": "macro_regime_classifier",
        "status": "passed",
        "mock": False,
        "mode": str(mode),
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
        "confidence": _confidence_from_margins(growth_evidence, inflation_evidence),
        "reason_brief": (
            f"Growth {growth_evidence['direction']}: YoY {growth_evidence['metric_value']:.4f} "
            f"vs trend {growth_evidence['trend_value']:.4f}. "
            f"Inflation {inflation_evidence['direction']}: YoY "
            f"{inflation_evidence['metric_value']:.4f} vs trend "
            f"{inflation_evidence['trend_value']:.4f}. Regime {regime}."
        ),
    }


def make_real_macro_regime_classifier_tool(
    fred_factory: Callable[[Any], Any] | None = None,
) -> ToolDefinition:
    name = "macro_regime_classifier"
    description = (
        "Classify the Growth / Inflation quadrant using FRED point-in-time GDPC1 and CPIAUCSL data. "
        "The tool applies configured lags, compares year-over-year metrics with five-year rolling trends, "
        "and returns regime, basket weights, and evidence. "
        "Do not manually recalculate its output."
    )
    metadata = {"kind": "fred_macro_regime", "mock": False, "replay_on_cache": True}

    def binder(strategy: Any, manager: Any) -> BoundTool:
        parameters = getattr(strategy, "parameters", {}) or {}
        fred = fred_factory(strategy) if fred_factory is not None else FREDMacroData(strategy=strategy)

        def configured_default(default: Any, *keys: str) -> Any:
            for key in keys:
                if key in parameters:
                    return parameters[key]
            return default

        default_mode = configured_default(DEFAULT_MODE, "macro_regime_mode", "mode")
        default_growth_series_id = configured_default(DEFAULT_GROWTH_SERIES_ID, "growth_series_id")
        default_inflation_series_id = configured_default(
            DEFAULT_INFLATION_SERIES_ID,
            "inflation_series_id",
        )
        default_growth_lag_months = configured_default(
            DEFAULT_GROWTH_LAG_MONTHS,
            "growth_lag_months",
        )
        default_inflation_lag_months = configured_default(
            DEFAULT_INFLATION_LAG_MONTHS,
            "inflation_lag_months",
        )
        default_trend_years = configured_default(DEFAULT_TREND_YEARS, "trend_years")

        def macro_regime_classifier(
            *,
            date: Any | None = None,
            mode: str | None = None,
            growth_series_id: str | None = None,
            inflation_series_id: str | None = None,
            growth_lag_months: int | None = None,
            inflation_lag_months: int | None = None,
            trend_years: int | None = None,
        ) -> dict[str, Any]:
            strategy_date = strategy.get_datetime().date().isoformat()
            resolved_date = date if date is not None else strategy_date
            resolved_mode = mode if mode is not None else default_mode
            resolved_growth_series_id = (
                growth_series_id if growth_series_id is not None else default_growth_series_id
            )
            resolved_inflation_series_id = (
                inflation_series_id
                if inflation_series_id is not None
                else default_inflation_series_id
            )
            resolved_growth_lag_months = (
                growth_lag_months if growth_lag_months is not None else default_growth_lag_months
            )
            resolved_inflation_lag_months = (
                inflation_lag_months
                if inflation_lag_months is not None
                else default_inflation_lag_months
            )
            resolved_trend_years = trend_years if trend_years is not None else default_trend_years

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
            )
            return result

        return BoundTool(
            name=name,
            description=description,
            function=macro_regime_classifier,
            source="local",
            metadata=metadata,
        )

    return ToolDefinition(name=name, description=description, binder=binder, metadata=metadata)
