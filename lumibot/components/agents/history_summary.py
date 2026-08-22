from __future__ import annotations

import math
from numbers import Real
from typing import Any

import pandas as pd

SCHEMA_VERSION = "1.0"
RANKING_LIMIT = 10
UNIVERSE_SUMMARY_LIMIT = 15
UNIVERSE_SUMMARY_SELECTION_PRIORITY = [
    "by_composite_score",
    "by_momentum_composite",
    "multi_ranking_overlap",
]
_DETAIL_SELECTION_RANKING_PRIORITY = [
    "by_composite_score",
    "by_momentum_composite",
]


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
    volume = _numeric_series(data, "volume")

    if close is None:
        warnings.append(
            "History table has no usable close column; price, momentum, trend, and risk metrics are unavailable."
        )

    latest_close = _last_value(close)
    momentum = {
        "return_5": _period_return(close, 5),
        "return_10": _period_return(close, 10),
        "return_20": _period_return(close, 20),
        "return_21": _period_return(close, 21),
        "return_60": _period_return(close, 60),
        "return_63": _period_return(close, 63),
        "return_120": _period_return(close, 120),
        "return_126": _period_return(close, 126),
        "return_252": _period_return(close, 252),
        "return_252_ex_skip_21": _period_return_excluding_recent(close, total_window=252, skip_recent=21),
    }
    regression_90 = _linear_regression_log_price(close, 90)
    trend = {
        "sma_20": _sma(close, 20),
        "sma_50": _sma(close, 50),
        "sma_200": _sma(close, 200),
        "linear_regression_slope_90": regression_90["annualized_slope"],
        "linear_regression_r2_90": regression_90["r2"],
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
        "sma_stack_score": _sma_stack_score(
            latest_close,
            trend["sma_20"],
            trend["sma_50"],
            trend["sma_200"],
        ),
    }
    adjusted_slope_90 = None
    if trend["linear_regression_slope_90"] is not None and trend["linear_regression_r2_90"] is not None:
        adjusted_slope_90 = _finite_float(
            trend["linear_regression_slope_90"] * trend["linear_regression_r2_90"]
        )
    scores["adjusted_slope_90"] = adjusted_slope_90
    high_252 = _window_extreme(high if high is not None else close, 252, "max")
    low_252 = _window_extreme(low if low is not None else close, 252, "min")
    max_drawdown_60 = _max_drawdown(close, 60)
    max_drawdown_126 = _max_drawdown_strict(close, 126)
    volatility_20 = _volatility(close, 20)
    volatility_63 = _volatility_strict(close, 63)
    avg_volume_20 = _sma(volume, 20)
    latest_volume = _last_value(volume)
    volume_summary = {
        "latest_volume": latest_volume,
        "avg_volume_20": avg_volume_20,
        "volume_vs_avg_20": _relative_to(latest_volume, avg_volume_20),
        "dollar_volume_20": _finite_float(avg_volume_20 * latest_close)
        if avg_volume_20 is not None and latest_close is not None
        else None,
        "up_volume_ratio_20": _up_volume_ratio(close, volume, 20),
    }
    drawdown_from_high_20 = _drawdown_from_high(high if high is not None else close, close, 20)
    drawdown_from_high_60 = _drawdown_from_high(high if high is not None else close, close, 60)
    drawdown_from_high_252 = _drawdown_from_high(high if high is not None else close, close, 252)
    distance_to_high_63 = _relative_to(
        latest_close,
        _window_extreme_strict(high if high is not None else close, 63, "max"),
    )
    breakout_20_high_score = _breakout_score(high if high is not None else close, close, 20)
    breakout_63_high_score = _breakout_score(high if high is not None else close, close, 63)
    volume_confirmed_momentum = None
    if momentum["return_63"] is not None and volume_summary["up_volume_ratio_20"] is not None:
        volume_confirmed_momentum = _finite_float(
            (momentum["return_63"] + volume_summary["up_volume_ratio_20"]) / 2.0
        )
    scores.update(
        {
            "return_63_over_volatility_20": _ratio(momentum["return_63"], volatility_20),
            "return_126_over_volatility_20": _ratio(momentum["return_126"], volatility_20),
            "return_252_over_volatility_63": _ratio(momentum["return_252"], volatility_63),
            "sharpe_like_63": _sharpe_like_strict(close, 63),
            "calmar_like_126": _ratio(
                momentum["return_126"],
                abs(max_drawdown_126) if max_drawdown_126 is not None else None,
            ),
            "volume_confirmed_momentum": volume_confirmed_momentum,
        }
    )
    scores["composite_score"] = _composite_score(
        momentum_5=momentum["return_5"],
        momentum_10=momentum["return_10"],
        momentum_63=momentum["return_63"],
        momentum_126=momentum["return_126"],
        trend_alignment=scores["trend_alignment"],
        drawdown_from_high_60=drawdown_from_high_60,
        risk_adjusted_63=scores["return_63_over_volatility_20"],
        risk_adjusted_126=scores["return_126_over_volatility_20"],
    )
    if volatility_20 is not None and timestep != "day":
        notes.append("volatility_20 is calculated from the last 20 bars and is not annualized as daily volatility.")

    availability = {
        "latest_close": latest_close is not None,
        "return_5": momentum["return_5"] is not None,
        "return_10": momentum["return_10"] is not None,
        "return_20": momentum["return_20"] is not None,
        "return_21": momentum["return_21"] is not None,
        "return_60": momentum["return_60"] is not None,
        "return_63": momentum["return_63"] is not None,
        "return_120": momentum["return_120"] is not None,
        "return_126": momentum["return_126"] is not None,
        "return_252": momentum["return_252"] is not None,
        "return_252_ex_skip_21": momentum["return_252_ex_skip_21"] is not None,
        "momentum_composite": scores["momentum_composite"] is not None,
        "trend_alignment": scores["trend_alignment"] is not None,
        "sma_stack_score": scores["sma_stack_score"] is not None,
        "adjusted_slope_90": scores["adjusted_slope_90"] is not None,
        "return_63_over_volatility_20": scores["return_63_over_volatility_20"] is not None,
        "return_126_over_volatility_20": scores["return_126_over_volatility_20"] is not None,
        "return_252_over_volatility_63": scores["return_252_over_volatility_63"] is not None,
        "sharpe_like_63": scores["sharpe_like_63"] is not None,
        "calmar_like_126": scores["calmar_like_126"] is not None,
        "volume_confirmed_momentum": scores["volume_confirmed_momentum"] is not None,
        "composite_score": scores["composite_score"] is not None,
        "latest_volume": latest_volume is not None,
        "avg_volume_20": avg_volume_20 is not None,
        "volume_vs_avg_20": volume_summary["volume_vs_avg_20"] is not None,
        "dollar_volume_20": volume_summary["dollar_volume_20"] is not None,
        "up_volume_ratio_20": volume_summary["up_volume_ratio_20"] is not None,
        "sma_20": trend["sma_20"] is not None,
        "sma_50": trend["sma_50"] is not None,
        "sma_200": trend["sma_200"] is not None,
        "linear_regression_slope_90": trend["linear_regression_slope_90"] is not None,
        "linear_regression_r2_90": trend["linear_regression_r2_90"] is not None,
        "high_252": high_252 is not None,
        "low_252": low_252 is not None,
        "distance_to_high_63": distance_to_high_63 is not None,
        "breakout_20_high_score": breakout_20_high_score is not None,
        "breakout_63_high_score": breakout_63_high_score is not None,
        "drawdown_from_high_20": drawdown_from_high_20 is not None,
        "drawdown_from_high_60": drawdown_from_high_60 is not None,
        "drawdown_from_high_252": drawdown_from_high_252 is not None,
        "max_drawdown_60": max_drawdown_60 is not None,
        "max_drawdown_126": max_drawdown_126 is not None,
        "volatility_20": volatility_20 is not None,
        "volatility_63": volatility_63 is not None,
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
        "volume": volume_summary,
        "trend": trend,
        "scores": scores,
        "range": {
            "high_252": high_252,
            "low_252": low_252,
            "distance_to_high_252": _relative_to(latest_close, high_252),
            "distance_to_low_252": _relative_to(latest_close, low_252),
            "distance_to_high_63": distance_to_high_63,
            "breakout_20_high_score": breakout_20_high_score,
            "breakout_63_high_score": breakout_63_high_score,
            "drawdown_from_high_20": drawdown_from_high_20,
            "drawdown_from_high_60": drawdown_from_high_60,
            "drawdown_from_high_252": drawdown_from_high_252,
        },
        "risk": {
            "max_drawdown_60": max_drawdown_60,
            "max_drawdown_126": max_drawdown_126,
            "volatility_20": volatility_20,
            "volatility_63": volatility_63,
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
    all_universe_rows = [
        _summary_to_universe_row(history_summaries[symbol])
        for symbol in symbols
        if symbol in history_summaries
    ]
    full_rankings = _rankings(all_universe_rows)
    rankings = _limit_rankings(full_rankings, RANKING_LIMIT)
    selected_symbols = _select_universe_summary_symbols(rankings, limit=UNIVERSE_SUMMARY_LIMIT)
    if not selected_symbols:
        selected_symbols = [
            str(row["symbol"])
            for row in all_universe_rows
            if row.get("symbol")
        ][:UNIVERSE_SUMMARY_LIMIT]
    rows_by_symbol = {
        str(row["symbol"]): row
        for row in all_universe_rows
        if row.get("symbol")
    }
    universe_summary = [
        rows_by_symbol[symbol]
        for symbol in selected_symbols
        if symbol in rows_by_symbol
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "symbols": symbols,
        "timestep": timestep,
        "length": length,
        "as_of": as_of,
        "ranking_limit": RANKING_LIMIT,
        "rankings": rankings,
        "universe_summary_limit": UNIVERSE_SUMMARY_LIMIT,
        "universe_summary_selection": {
            "mode": "top_rank_union",
            "candidate_count_before_limit": len(_unique_ranked_symbols(rankings)),
            "included_symbols": selected_symbols,
            "priority": UNIVERSE_SUMMARY_SELECTION_PRIORITY,
        },
        "universe_summary": universe_summary,
        "loaded_tables": loaded_tables or {},
        "warnings": warnings or [],
    }


def _summary_to_universe_row(summary: dict[str, Any]) -> dict[str, Any]:
    price = _dict(summary.get("price"))
    momentum = _dict(summary.get("momentum"))
    volume = _dict(summary.get("volume"))
    scores = _dict(summary.get("scores"))
    range_metrics = _dict(summary.get("range"))
    risk = _dict(summary.get("risk"))

    return {
        "symbol": summary.get("symbol"),
        "latest_close": _compact_number(price.get("latest_close")),
        "return_5": _compact_number(momentum.get("return_5")),
        "return_21": _compact_number(momentum.get("return_21")),
        "return_63": _compact_number(momentum.get("return_63")),
        "return_126": _compact_number(momentum.get("return_126")),
        "momentum_composite": _compact_number(scores.get("momentum_composite")),
        "composite_score": _compact_number(scores.get("composite_score")),
        "volume_vs_avg_20": _compact_number(volume.get("volume_vs_avg_20")),
        "trend_alignment": _compact_number(scores.get("trend_alignment")),
        "volatility_20": _compact_number(risk.get("volatility_20")),
        "drawdown_from_high_60": _compact_number(range_metrics.get("drawdown_from_high_60")),
    }


def _rankings(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {
        "by_return_21": _rank_symbols(rows, "return_21"),
        "by_return_63": _rank_symbols(rows, "return_63"),
        "by_return_126": _rank_symbols(rows, "return_126"),
        "by_momentum_composite": _rank_symbols(rows, "momentum_composite"),
        "by_composite_score": _rank_symbols(rows, "composite_score"),
        "by_trend_alignment": _rank_symbols(rows, "trend_alignment"),
    }


def _limit_rankings(rankings: dict[str, list[str]], limit: int) -> dict[str, list[str]]:
    return {
        name: symbols[:limit]
        for name, symbols in rankings.items()
    }


def _select_universe_summary_symbols(rankings: dict[str, list[str]], *, limit: int) -> list[str]:
    candidates = _unique_ranked_symbols(rankings)
    if len(candidates) <= limit:
        return candidates

    selected: list[str] = []
    seen: set[str] = set()

    def add(symbol: str) -> None:
        if len(selected) >= limit or symbol in seen:
            return
        selected.append(symbol)
        seen.add(symbol)

    for ranking_name in _DETAIL_SELECTION_RANKING_PRIORITY:
        for symbol in rankings.get(ranking_name, []):
            add(symbol)

    if len(selected) >= limit:
        return selected

    first_seen_order = {symbol: index for index, symbol in enumerate(candidates)}
    appearance_counts: dict[str, int] = {}
    best_rank: dict[str, int] = {}
    for ranking in rankings.values():
        for index, symbol in enumerate(ranking):
            appearance_counts[symbol] = appearance_counts.get(symbol, 0) + 1
            best_rank[symbol] = min(best_rank.get(symbol, index), index)

    remaining = [symbol for symbol in candidates if symbol not in seen]
    remaining.sort(
        key=lambda symbol: (
            -appearance_counts.get(symbol, 0),
            best_rank.get(symbol, len(candidates)),
            first_seen_order.get(symbol, len(candidates)),
        )
    )
    for symbol in remaining:
        add(symbol)
    return selected


def _unique_ranked_symbols(rankings: dict[str, list[str]]) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for ranking_name in _DETAIL_SELECTION_RANKING_PRIORITY:
        for symbol in rankings.get(ranking_name, []):
            if symbol not in seen:
                symbols.append(symbol)
                seen.add(symbol)
    for ranking in rankings.values():
        for symbol in ranking:
            if symbol not in seen:
                symbols.append(symbol)
                seen.add(symbol)
    return symbols


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


def _compact_number(value: Any, *, digits: int = 6) -> Any:
    if isinstance(value, bool) or not isinstance(value, Real):
        return value
    numeric = float(value)
    if not math.isfinite(numeric):
        return None
    return round(numeric, digits)


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
    return _finite_float(latest / previous - 1.0)


def _sma(series: pd.Series | None, window: int) -> float | None:
    if series is None or len(series) < window:
        return None
    return _finite_float(series.tail(window).mean())


def _relative_to(numerator: float | None, denominator: float | None) -> float | None:
    numerator = _finite_float(numerator)
    denominator = _finite_float(denominator)
    if numerator is None or denominator in (None, 0):
        return None
    return _finite_float(numerator / denominator - 1.0)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    numerator = _finite_float(numerator)
    denominator = _finite_float(denominator)
    if numerator is None or denominator in (None, 0):
        return None
    return _finite_float(numerator / denominator)


def _mean_available(values: list[float | None]) -> float | None:
    available = [_finite_float(value) for value in values]
    available = [value for value in available if value is not None]
    if not available:
        return None
    return _finite_float(sum(available) / len(available))


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


def _window_extreme_strict(series: pd.Series | None, window: int, method: str) -> float | None:
    if series is None or len(series) < window:
        return None
    values = series.tail(window).dropna()
    values = values[values.map(lambda value: _finite_float(value) is not None)]
    if len(values) < window:
        return None
    if method == "max":
        return float(values.max())
    if method == "min":
        return float(values.min())
    raise ValueError(f"Unsupported extreme method: {method}")


def _drawdown_from_high(
    high_series: pd.Series | None,
    close_series: pd.Series | None,
    window: int,
) -> float | None:
    if close_series is None or close_series.empty:
        return None
    latest = _last_value(close_series)
    if latest is None:
        return None
    source = high_series if high_series is not None else close_series
    window_high = _window_extreme(source, window, "max")
    return _relative_to(latest, window_high)


def _composite_score(
    *,
    momentum_5: float | None,
    momentum_10: float | None,
    momentum_63: float | None,
    momentum_126: float | None,
    trend_alignment: int | None,
    drawdown_from_high_60: float | None,
    risk_adjusted_63: float | None,
    risk_adjusted_126: float | None,
) -> float | None:
    components: list[float] = []
    for value in (momentum_5, momentum_10, momentum_63, momentum_126):
        numeric = _finite_float(value)
        if numeric is not None:
            components.append(numeric)
    if trend_alignment is not None:
        trend_numeric = _finite_float(trend_alignment)
        if trend_numeric is not None:
            components.append(trend_numeric / 3.0)
    drawdown = _finite_float(drawdown_from_high_60)
    if drawdown is not None:
        components.append(drawdown)
    for value in (risk_adjusted_63, risk_adjusted_126):
        numeric = _finite_float(value)
        if numeric is not None:
            components.append(numeric / 10.0)
    if len(components) < 3:
        return None
    return _finite_float(sum(components) / len(components))


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


def _max_drawdown_strict(series: pd.Series | None, window: int) -> float | None:
    if series is None or len(series) < window:
        return None
    values = series.tail(window).astype("float64").dropna()
    values = values[values.map(lambda value: _finite_float(value) is not None)]
    if len(values) < window:
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


def _volatility_strict(series: pd.Series | None, window: int) -> float | None:
    if series is None or len(series) <= window:
        return None
    returns = series.pct_change().dropna()
    returns = returns[returns.map(lambda value: _finite_float(value) is not None)].tail(window)
    if len(returns) < window:
        return None
    return _finite_float(returns.std())


def _period_return_excluding_recent(series: pd.Series | None, *, total_window: int, skip_recent: int) -> float | None:
    if series is None or len(series) <= total_window:
        return None
    endpoint_index = -skip_recent - 1
    start_index = -total_window - 1
    endpoint = _finite_float(series.iloc[endpoint_index])
    start = _finite_float(series.iloc[start_index])
    if endpoint is None or start in (None, 0):
        return None
    return _finite_float(endpoint / start - 1.0)


def _sma_stack_score(
    close: float | None,
    sma_20: float | None,
    sma_50: float | None,
    sma_200: float | None,
) -> int | None:
    values = [close, sma_20, sma_50, sma_200]
    if any(_finite_float(value) is None for value in values):
        return None
    score = 0
    if float(close) > float(sma_20):
        score += 1
    if float(sma_20) > float(sma_50):
        score += 1
    if float(sma_50) > float(sma_200):
        score += 1
    if float(close) > float(sma_200):
        score += 1
    return score


def _linear_regression_log_price(series: pd.Series | None, window: int) -> dict[str, float | None]:
    empty_result = {"slope": None, "r2": None, "annualized_slope": None}
    if series is None or len(series) < window:
        return empty_result
    values = pd.Series(
        [_finite_float(value) for value in series.tail(window)],
        dtype="float64",
    ).dropna()
    values = values[values > 0].reset_index(drop=True)
    if len(values) < window:
        return empty_result
    y = values.map(math.log)
    x = pd.Series(range(len(y)), dtype="float64")
    x_mean = x.mean()
    y_mean = y.mean()
    denominator = ((x - x_mean) ** 2).sum()
    if denominator == 0:
        return empty_result
    slope = ((x - x_mean) * (y - y_mean)).sum() / denominator
    fitted = y_mean + slope * (x - x_mean)
    ss_total = ((y - y_mean) ** 2).sum()
    ss_residual = ((y - fitted) ** 2).sum()
    r2 = 1.0 if ss_total == 0 else 1.0 - ss_residual / ss_total
    annualized_slope = math.exp(slope * 252.0) - 1.0
    return {
        "slope": _finite_float(slope),
        "r2": _finite_float(r2),
        "annualized_slope": _finite_float(annualized_slope),
    }


def _sharpe_like(series: pd.Series | None, window: int) -> float | None:
    if series is None or len(series) <= 1:
        return None
    returns = series.pct_change().dropna()
    returns = returns[returns.map(lambda value: _finite_float(value) is not None)].tail(window)
    if len(returns) < 2:
        return None
    volatility = _finite_float(returns.std())
    mean_return = _finite_float(returns.mean())
    if mean_return is None or volatility in (None, 0):
        return None
    return _finite_float(mean_return / volatility)


def _sharpe_like_strict(series: pd.Series | None, window: int) -> float | None:
    if series is None or len(series) <= window:
        return None
    returns = series.pct_change().dropna()
    returns = returns[returns.map(lambda value: _finite_float(value) is not None)].tail(window)
    if len(returns) < window:
        return None
    volatility = _finite_float(returns.std())
    mean_return = _finite_float(returns.mean())
    if mean_return is None or volatility in (None, 0):
        return None
    return _finite_float(mean_return / volatility)


def _breakout_score(high_series: pd.Series | None, close_series: pd.Series | None, window: int) -> float | None:
    if close_series is None or high_series is None or len(close_series) <= window or len(high_series) <= window:
        return None
    latest = _last_value(close_series)
    prior_high = _window_extreme(high_series.iloc[:-1], window, "max")
    return _relative_to(latest, prior_high)


def _up_volume_ratio(close_series: pd.Series | None, volume_series: pd.Series | None, window: int) -> float | None:
    if close_series is None or volume_series is None or len(close_series) <= 1:
        return None
    data = pd.DataFrame({"close": close_series, "volume": volume_series}).dropna().tail(window + 1)
    data = data[
        data["close"].map(lambda value: _finite_float(value) is not None)
        & data["volume"].map(lambda value: _finite_float(value) is not None)
    ].reset_index(drop=True)
    if len(data) <= window:
        return None
    returns = data["close"].pct_change().dropna()
    volumes = data["volume"].iloc[1:]
    if len(returns) < window or len(volumes) < window:
        return None
    total_volume = _finite_float(volumes.sum())
    if total_volume in (None, 0):
        return None
    up_volume = _finite_float(volumes[returns > 0].sum())
    if up_volume is None:
        return None
    return _finite_float(up_volume / total_volume)
