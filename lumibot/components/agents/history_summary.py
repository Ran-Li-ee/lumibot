from __future__ import annotations

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
        "return_60": _period_return(close, 60),
        "return_120": _period_return(close, 120),
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
    high_252 = _window_extreme(high if high is not None else close, 252, "max")
    low_252 = _window_extreme(low if low is not None else close, 252, "min")
    volatility_20 = _volatility(close, 20)
    if volatility_20 is not None and timestep != "day":
        notes.append("volatility_20 is calculated from the last 20 bars and is not annualized as daily volatility.")

    availability = {
        "latest_close": latest_close is not None,
        "return_20": momentum["return_20"] is not None,
        "return_60": momentum["return_60"] is not None,
        "return_120": momentum["return_120"] is not None,
        "sma_20": trend["sma_20"] is not None,
        "sma_50": trend["sma_50"] is not None,
        "sma_200": trend["sma_200"] is not None,
        "high_252": high_252 is not None,
        "low_252": low_252 is not None,
        "max_drawdown_60": close is not None and len(close.dropna()) > 0,
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
        "range": {
            "high_252": high_252,
            "low_252": low_252,
            "distance_to_high_252": _relative_to(latest_close, high_252),
            "distance_to_low_252": _relative_to(latest_close, low_252),
        },
        "risk": {
            "max_drawdown_60": _max_drawdown(close, 60),
            "volatility_20": volatility_20,
        },
        "availability": availability,
        "warnings": warnings,
        "notes": notes,
    }


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
    return float(series.iloc[-1])


def _period_return(series: pd.Series | None, window: int) -> float | None:
    if series is None or len(series) <= window:
        return None
    previous = float(series.iloc[-window - 1])
    latest = float(series.iloc[-1])
    if previous == 0:
        return None
    return latest / previous - 1.0


def _sma(series: pd.Series | None, window: int) -> float | None:
    if series is None or len(series) < window:
        return None
    return float(series.tail(window).mean())


def _relative_to(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator - 1.0


def _window_extreme(series: pd.Series | None, window: int, method: str) -> float | None:
    if series is None or series.empty:
        return None
    values = series.tail(min(window, len(series)))
    if method == "max":
        return float(values.max())
    if method == "min":
        return float(values.min())
    raise ValueError(f"Unsupported extreme method: {method}")


def _max_drawdown(series: pd.Series | None, window: int) -> float | None:
    if series is None or series.empty:
        return None
    values = series.tail(min(window, len(series))).astype("float64")
    running_peak = values.cummax()
    drawdowns = values / running_peak - 1.0
    return float(drawdowns.min())


def _volatility(series: pd.Series | None, window: int) -> float | None:
    if series is None or len(series) <= 1:
        return None
    returns = series.pct_change().dropna().tail(window)
    if len(returns) < 2:
        return None
    return float(returns.std())
