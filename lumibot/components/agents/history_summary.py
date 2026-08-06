from __future__ import annotations

import math
from numbers import Real
from typing import Any

import pandas as pd

SCHEMA_VERSION = "1.0"


def compute_history_summary(
    frame: pd.DataFrame,
    *,
    symbol: str,
    timestep: str | None,
    as_of: str | None,
) -> dict[str, Any]:
    """Return compact, model-facing statistics for a loaded history table."""
    warnings: list[str] = []
    notes: list[str] = []
    data = _sort_frame(frame)
    close = _numeric_series(data, "close")
    high = _numeric_series(data, "high")
    low = _numeric_series(data, "low")

    if close is None:
        warnings.append(
            "History table has no usable close column; price, momentum, trend, and risk metrics are unavailable."
        )

    latest_close = _last_value(close)
    momentum = {
        "return_20": _period_return(close, 20),
        "return_21": _period_return(close, 21),
        "return_60": _period_return(close, 60),
        "return_63": _period_return(close, 63),
        "return_120": _period_return(close, 120),
        "return_126": _period_return(close, 126),
        "return_252": _period_return(close, 252),
    }
    trend = {
        "sma_20": _sma(close, 20),
        "sma_50": _sma(close, 50),
        "sma_200": _sma(close, 200),
    }
    trend.update(
        {
            "price_vs_sma_20": _relative_to(latest_close, trend["sma_20"]),
            "price_vs_sma_50": _relative_to(latest_close, trend["sma_50"]),
            "price_vs_sma_200": _relative_to(latest_close, trend["sma_200"]),
        }
    )
    scores = {
        "momentum_composite": _mean_available(
            [
                momentum["return_21"],
                momentum["return_63"],
                momentum["return_126"],
            ]
        ),
        "trend_alignment": _trend_alignment(
            [
                trend["price_vs_sma_20"],
                trend["price_vs_sma_50"],
                trend["price_vs_sma_200"],
            ]
        ),
    }
    high_252 = _window_extreme(high if high is not None else close, 252, "max")
    low_252 = _window_extreme(low if low is not None else close, 252, "min")
    max_drawdown_60 = _max_drawdown(close, 60)
    volatility_20 = _volatility(close, 20)
    if volatility_20 is not None and timestep != "day":
        notes.append("volatility_20 is calculated from the last 20 bars and is not annualized as daily volatility.")

    availability = {
        "latest_close": latest_close is not None,
        "return_20": momentum["return_20"] is not None,
        "return_21": momentum["return_21"] is not None,
        "return_60": momentum["return_60"] is not None,
        "return_63": momentum["return_63"] is not None,
        "return_120": momentum["return_120"] is not None,
        "return_126": momentum["return_126"] is not None,
        "return_252": momentum["return_252"] is not None,
        "momentum_composite": scores["momentum_composite"] is not None,
        "trend_alignment": scores["trend_alignment"] is not None,
        "sma_20": trend["sma_20"] is not None,
        "sma_50": trend["sma_50"] is not None,
        "sma_200": trend["sma_200"] is not None,
        "high_252": high_252 is not None,
        "low_252": low_252 is not None,
        "max_drawdown_60": max_drawdown_60 is not None,
        "volatility_20": volatility_20 is not None,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol,
        "timestep": timestep,
        "as_of": as_of,
        "data_window": _data_window(data),
        "price": {
            "latest_close": latest_close,
        },
        "momentum": momentum,
        "trend": trend,
        "scores": scores,
        "range": {
            "high_252": high_252,
            "low_252": low_252,
            "distance_to_high_252": _relative_to(latest_close, high_252),
            "distance_to_low_252": _relative_to(latest_close, low_252),
        },
        "risk": {
            "max_drawdown_60": max_drawdown_60,
            "volatility_20": volatility_20,
        },
        "availability": availability,
        "warnings": warnings,
        "notes": notes,
    }


def build_universe_history_summary(
    history_summaries: dict[str, dict[str, Any]],
    *,
    symbols: list[str],
    timestep: str | None,
    length: int | None,
    as_of: str | None,
    loaded_tables: dict[str, Any] | None,
    warnings: list[str] | None,
) -> dict[str, Any]:
    """Return a compact, model-facing batch summary for a symbol universe."""
    universe_summary = [
        _summary_to_universe_row(history_summaries[symbol])
        for symbol in symbols
        if symbol in history_summaries
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "symbols": symbols,
        "timestep": timestep,
        "length": length,
        "as_of": as_of,
        "loaded_tables": loaded_tables or {},
        "universe_summary": universe_summary,
        "rankings": _rankings(universe_summary),
        "warnings": warnings or [],
    }


def _summary_to_universe_row(summary: dict[str, Any]) -> dict[str, Any]:
    price = _dict(summary.get("price"))
    momentum = _dict(summary.get("momentum"))
    trend = _dict(summary.get("trend"))
    scores = _dict(summary.get("scores"))
    range_metrics = _dict(summary.get("range"))
    risk = _dict(summary.get("risk"))

    return {
        "symbol": summary.get("symbol"),
        "latest_close": _finite_or_none(price.get("latest_close")),
        "return_21": _finite_or_none(momentum.get("return_21")),
        "return_63": _finite_or_none(momentum.get("return_63")),
        "return_126": _finite_or_none(momentum.get("return_126")),
        "return_252": _finite_or_none(momentum.get("return_252")),
        "momentum_composite": _finite_or_none(scores.get("momentum_composite")),
        "sma_20": _finite_or_none(trend.get("sma_20")),
        "sma_50": _finite_or_none(trend.get("sma_50")),
        "sma_200": _finite_or_none(trend.get("sma_200")),
        "price_vs_sma_20": _finite_or_none(trend.get("price_vs_sma_20")),
        "price_vs_sma_50": _finite_or_none(trend.get("price_vs_sma_50")),
        "price_vs_sma_200": _finite_or_none(trend.get("price_vs_sma_200")),
        "trend_alignment": _finite_or_none(scores.get("trend_alignment")),
        "max_drawdown_60": _finite_or_none(risk.get("max_drawdown_60")),
        "volatility_20": _finite_or_none(risk.get("volatility_20")),
        "distance_to_high_252": _finite_or_none(range_metrics.get("distance_to_high_252")),
        "distance_to_low_252": _finite_or_none(range_metrics.get("distance_to_low_252")),
    }


def _rankings(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {
        "by_return_21": _rank_symbols(rows, "return_21"),
        "by_return_63": _rank_symbols(rows, "return_63"),
        "by_return_126": _rank_symbols(rows, "return_126"),
        "by_momentum_composite": _rank_symbols(rows, "momentum_composite"),
        "by_trend_alignment": _rank_symbols(rows, "trend_alignment"),
    }


def _rank_symbols(rows: list[dict[str, Any]], key: str) -> list[str]:
    rankable = [
        (str(row["symbol"]), float(row[key]))
        for row in rows
        if row.get("symbol") and _is_rankable(row.get(key))
    ]
    return [symbol for symbol, _ in sorted(rankable, key=lambda item: item[1], reverse=True)]


def _dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _is_rankable(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, Real):
        return False
    return math.isfinite(float(value))


def _finite_or_none(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, Real):
        return value
    if math.isfinite(float(value)):
        return value
    return None


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    numeric = float(value)
    if math.isfinite(numeric):
        return numeric
    return None


def _sort_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    date_column = _date_column(frame)
    if date_column is None:
        return frame.copy()
    return frame.sort_values(date_column).reset_index(drop=True)


def _date_column(frame: pd.DataFrame) -> str | None:
    for candidate in ("Date", "date", "datetime", "Datetime", "timestamp", "Timestamp"):
        if candidate in frame.columns:
            return candidate
    return None


def _data_window(frame: pd.DataFrame) -> dict[str, Any]:
    date_column = _date_column(frame)
    start = None
    end = None
    if date_column is not None and not frame.empty:
        dates = pd.to_datetime(frame[date_column], errors="coerce").dropna()
        if not dates.empty:
            start = dates.iloc[0].isoformat()
            end = dates.iloc[-1].isoformat()
    return {
        "row_count": int(len(frame)),
        "start": start,
        "end": end,
    }


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series | None:
    if column not in frame.columns:
        return None
    series = pd.to_numeric(frame[column], errors="coerce").dropna()
    if series.empty:
        return None
    return series.astype("float64")


def _last_value(series: pd.Series | None) -> float | None:
    if series is None or series.empty:
        return None
    return _finite_float(series.iloc[-1])


def _period_return(series: pd.Series | None, window: int) -> float | None:
    if series is None or len(series) <= window:
        return None
    previous = _finite_float(series.iloc[-window - 1])
    latest = _finite_float(series.iloc[-1])
    if previous in (None, 0) or latest is None:
        return None
    return latest / previous - 1.0


def _sma(series: pd.Series | None, window: int) -> float | None:
    if series is None or len(series) < window:
        return None
    return _finite_float(series.tail(window).mean())


def _relative_to(numerator: float | None, denominator: float | None) -> float | None:
    numerator = _finite_float(numerator)
    denominator = _finite_float(denominator)
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator - 1.0


def _mean_available(values: list[float | None]) -> float | None:
    available = [_finite_float(value) for value in values]
    available = [value for value in available if value is not None]
    if not available:
        return None
    return sum(available) / len(available)


def _trend_alignment(values: list[float | None]) -> int | None:
    available = [float(value) for value in values if _is_rankable(value)]
    if not available:
        return None
    return sum(1 for value in available if value > 0)


def _window_extreme(series: pd.Series | None, window: int, method: str) -> float | None:
    if series is None or series.empty:
        return None
    values = series.tail(min(window, len(series))).dropna()
    values = values[values.map(lambda value: _finite_float(value) is not None)]
    if values.empty:
        return None
    if method == "max":
        return float(values.max())
    if method == "min":
        return float(values.min())
    raise ValueError(f"Unsupported extreme method: {method}")


def _max_drawdown(series: pd.Series | None, window: int) -> float | None:
    if series is None or series.empty:
        return None
    values = series.tail(min(window, len(series))).astype("float64").dropna()
    values = values[values.map(lambda value: _finite_float(value) is not None)]
    if values.empty:
        return None
    running_peak = values.cummax()
    drawdowns = values / running_peak - 1.0
    return _finite_float(drawdowns.min())


def _volatility(series: pd.Series | None, window: int) -> float | None:
    if series is None or len(series) <= 1:
        return None
    returns = series.pct_change().dropna()
    returns = returns[returns.map(lambda value: _finite_float(value) is not None)].tail(window)
    if len(returns) < 2:
        return None
    return _finite_float(returns.std())
