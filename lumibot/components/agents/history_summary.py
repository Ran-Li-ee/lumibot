from __future__ import annotations

import math
from numbers import Real
from typing import Any

import pandas as pd

SCHEMA_VERSION = "1.0"
DEFAULT_RANKING_LIMIT = 10
DEFAULT_CANDIDATE_SUMMARY_LIMIT = 25
LEGACY_EVIDENCE_PROFILE = "legacy"
MOMENTUM_STAGE_EVIDENCE_PROFILE = "momentum_stage"

RANK_GROUPS = {
    "momentum": [
        "by_return_21",
        "by_return_63",
        "by_return_126",
        "by_return_252",
        "by_return_252_ex_skip_21",
    ],
    "trend_quality": [
        "by_trend_alignment",
        "by_sma_stack_score",
        "by_adjusted_slope_90",
        "by_regression_r2_90",
    ],
    "risk_adjusted_momentum": [
        "by_return_63_over_volatility_20",
        "by_return_126_over_volatility_20",
        "by_return_252_over_volatility_63",
        "by_sharpe_like_63",
        "by_calmar_like_126",
    ],
    "breakout_near_high": [
        "by_near_252_high",
        "by_near_63_high",
        "by_breakout_20_high",
        "by_breakout_63_high",
        "by_drawdown_from_high_60",
    ],
    "volume_confirmation": [
        "by_volume_vs_avg_20",
        "by_dollar_volume_20",
        "by_up_volume_ratio_20",
        "by_volume_confirmed_momentum",
    ],
}

RANKING_METRICS = {
    "by_return_21": "return_21",
    "by_return_63": "return_63",
    "by_return_126": "return_126",
    "by_return_252": "return_252",
    "by_return_252_ex_skip_21": "return_252_ex_skip_21",
    "by_momentum_composite": "momentum_composite",
    "by_composite_score": "composite_score",
    "by_trend_alignment": "trend_alignment",
    "by_sma_stack_score": "sma_stack_score",
    "by_adjusted_slope_90": "adjusted_slope_90",
    "by_regression_r2_90": "linear_regression_r2_90",
    "by_return_63_over_volatility_20": "return_63_over_volatility_20",
    "by_return_126_over_volatility_20": "return_126_over_volatility_20",
    "by_return_252_over_volatility_63": "return_252_over_volatility_63",
    "by_sharpe_like_63": "sharpe_like_63",
    "by_calmar_like_126": "calmar_like_126",
    "by_near_252_high": "distance_to_high_252",
    "by_near_63_high": "distance_to_high_63",
    "by_breakout_20_high": "breakout_20_high_score",
    "by_breakout_63_high": "breakout_63_high_score",
    "by_drawdown_from_high_60": "drawdown_from_high_60",
    "by_volume_vs_avg_20": "volume_vs_avg_20",
    "by_dollar_volume_20": "dollar_volume_20",
    "by_up_volume_ratio_20": "up_volume_ratio_20",
    "by_volume_confirmed_momentum": "volume_confirmed_momentum",
}

CANDIDATE_PRIORITY_RANKINGS = [
    "by_momentum_composite",
    "by_adjusted_slope_90",
    "by_return_252_over_volatility_63",
    "by_composite_score",
]

MOMENTUM_STAGE_RANK_GROUPS = {
    "freshness": ["by_rank_delta_4w"],
    "smoothness": ["by_positive_day_ratio_3m", "by_low_max_day_return_share_3m"],
    "near_high": ["by_near_252d_high"],
    "volume_confirmation": ["by_up_down_volume_ratio_60d"],
    "relative_strength": ["by_excess_return_vs_qqq_6m", "by_excess_return_vs_spy_6m"],
}

MOMENTUM_STAGE_RANKING_METRICS = {
    "by_rank_delta_4w": "rank_delta_4w",
    "by_positive_day_ratio_3m": "positive_day_ratio_3m",
    "by_low_max_day_return_share_3m": "max_day_return_share_3m",
    "by_near_252d_high": "distance_to_252d_high_pct",
    "by_up_down_volume_ratio_60d": "up_down_volume_ratio_60d",
    "by_excess_return_vs_qqq_6m": "excess_return_vs_qqq_6m",
    "by_excess_return_vs_spy_6m": "excess_return_vs_spy_6m",
}

MOMENTUM_STAGE_RANKING_DIRECTIONS = {
    "by_low_max_day_return_share_3m": "asc",
}

MOMENTUM_STAGE_REFERENCE_FIELDS = [
    "top_decile_age_weeks",
    "extension_ma50_pct",
    "atr_extension_20d",
    "recent_vs_intermediate_momentum",
]

MOMENTUM_STAGE_WARNING_THRESHOLDS = {
    "stale_top_decile": 12,
    "extreme_ma50_extension": 0.20,
    "extreme_atr_extension": 3.0,
    "recent_overheat_vs_intermediate": 0.15,
    "single_day_jump_concentration": 0.35,
    "thin_or_missing_volume_support": 1.0,
}

MOMENTUM_STAGE_CANDIDATE_PRIORITY_RANKINGS = [
    "by_rank_delta_4w",
    "by_excess_return_vs_qqq_6m",
    "by_excess_return_vs_spy_6m",
    "by_positive_day_ratio_3m",
]

MOMENTUM_STAGE_ROW_FIELDS = [
    "rank_delta_4w",
    "top_decile_age_weeks",
    "extension_ma50_pct",
    "atr_extension_20d",
    "positive_day_ratio_3m",
    "max_day_return_share_3m",
    "distance_to_252d_high_pct",
    "recent_vs_intermediate_momentum",
    "up_down_volume_ratio_60d",
    "excess_return_vs_qqq_6m",
    "excess_return_vs_spy_6m",
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
    data = _truncate_to_last_finite_close(data)
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

    atr_20 = _atr(high, low, close, 20)
    atr_extension_20d = None
    if latest_close is not None and trend["sma_20"] is not None:
        atr_extension_20d = _ratio(latest_close - trend["sma_20"], atr_20)
    momentum_stage = {
        "extension_ma50_pct": _relative_to(latest_close, trend["sma_50"]),
        "atr_extension_20d": atr_extension_20d,
        "positive_day_ratio_3m": _positive_day_ratio(close, 63),
        "max_day_return_share_3m": _max_positive_return_share(close, 63),
        "distance_to_252d_high_pct": _relative_to(latest_close, high_252),
        "recent_vs_intermediate_momentum": _finite_float(
            momentum["return_21"] - momentum["return_252_ex_skip_21"]
        )
        if momentum["return_21"] is not None and momentum["return_252_ex_skip_21"] is not None
        else None,
        "up_down_volume_ratio_60d": _up_down_volume_ratio(close, volume, 60),
    }

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
        "extension_ma50_pct": momentum_stage["extension_ma50_pct"] is not None,
        "atr_extension_20d": momentum_stage["atr_extension_20d"] is not None,
        "positive_day_ratio_3m": momentum_stage["positive_day_ratio_3m"] is not None,
        "max_day_return_share_3m": momentum_stage["max_day_return_share_3m"] is not None,
        "distance_to_252d_high_pct": momentum_stage["distance_to_252d_high_pct"] is not None,
        "recent_vs_intermediate_momentum": momentum_stage["recent_vs_intermediate_momentum"] is not None,
        "up_down_volume_ratio_60d": momentum_stage["up_down_volume_ratio_60d"] is not None,
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
        "momentum_stage": momentum_stage,
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
    top_n: int = DEFAULT_RANKING_LIMIT,
    candidate_summary_limit: int = DEFAULT_CANDIDATE_SUMMARY_LIMIT,
    evidence_profile: str = LEGACY_EVIDENCE_PROFILE,
    history_frames: dict[str, pd.DataFrame] | None = None,
    benchmark_summaries: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a compact, model-facing batch summary for a symbol universe."""
    profile = str(evidence_profile or LEGACY_EVIDENCE_PROFILE).strip().lower()
    if profile == MOMENTUM_STAGE_EVIDENCE_PROFILE:
        return _build_momentum_stage_universe_history_summary(
            history_summaries,
            symbols=symbols,
            timestep=timestep,
            length=length,
            as_of=as_of,
            loaded_tables=loaded_tables,
            warnings=warnings,
            top_n=top_n,
            candidate_summary_limit=candidate_summary_limit,
            history_frames=history_frames,
            benchmark_summaries=benchmark_summaries,
        )
    if profile != LEGACY_EVIDENCE_PROFILE:
        raise ValueError(f"Unsupported evidence_profile: {evidence_profile}")

    top_n = max(1, int(top_n))
    candidate_summary_limit = max(1, int(candidate_summary_limit))
    all_universe_rows = [
        _summary_to_universe_row(history_summaries[symbol])
        for symbol in symbols
        if symbol in history_summaries
    ]
    full_rankings = _rankings(all_universe_rows)
    rankings = _limit_rankings(full_rankings, top_n)
    ranking_details = _ranking_details(all_universe_rows, rankings)
    selected_symbols = _select_candidate_summary_symbols(rankings, limit=candidate_summary_limit)
    if not selected_symbols:
        selected_symbols = [
            str(row["symbol"])
            for row in all_universe_rows
            if row.get("symbol")
        ][:candidate_summary_limit]
    rows_by_symbol = {
        str(row["symbol"]): row
        for row in all_universe_rows
        if row.get("symbol")
    }
    candidate_summary = [
        _candidate_summary_row(symbol, rows_by_symbol[symbol], ranking_details)
        for symbol in selected_symbols
        if symbol in rows_by_symbol
    ]
    coverage = {
        "requested_count": len(symbols),
        "loaded_count": len(all_universe_rows),
        "failed_count": max(0, len(symbols) - len(all_universe_rows)),
        "top_n": top_n,
        "candidate_summary_limit": candidate_summary_limit,
        "ranking_count": len(rankings),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "coverage": coverage,
        "rank_groups": RANK_GROUPS,
        "ranking_limit": top_n,
        "candidate_summary_limit": candidate_summary_limit,
        "rankings": rankings,
        "ranking_details": ranking_details,
        "candidate_summary": candidate_summary,
        "evidence_profile": LEGACY_EVIDENCE_PROFILE,
        "symbols": symbols,
        "timestep": timestep,
        "length": length,
        "as_of": as_of,
        "universe_summary_limit": candidate_summary_limit,
        "universe_summary_selection": {
            "mode": "top_rank_union",
            "candidate_count_before_limit": len(_unique_ranked_symbols(rankings)),
            "included_symbols": selected_symbols,
            "priority": CANDIDATE_PRIORITY_RANKINGS + ["multi_ranking_overlap"],
        },
        "universe_summary": candidate_summary,
        "loaded_tables": loaded_tables or {},
        "warnings": warnings or [],
    }


def _build_momentum_stage_universe_history_summary(
    history_summaries: dict[str, dict[str, Any]],
    *,
    symbols: list[str],
    timestep: str | None,
    length: int | None,
    as_of: str | None,
    loaded_tables: dict[str, Any] | None,
    warnings: list[str] | None,
    top_n: int,
    candidate_summary_limit: int,
    history_frames: dict[str, pd.DataFrame] | None,
    benchmark_summaries: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    top_n = max(1, int(top_n))
    candidate_summary_limit = max(1, int(candidate_summary_limit))
    requested_symbols = [symbol for symbol in symbols if symbol in history_summaries]
    benchmark_summaries = benchmark_summaries or {}
    benchmark_context = _benchmark_context(history_summaries, benchmark_summaries)
    rank_delta_by_symbol = _rank_delta_4w_by_symbol(history_frames or {}, requested_symbols)
    top_decile_age_by_symbol = _top_decile_age_weeks_by_symbol(history_frames or {}, requested_symbols)
    qqq_return_126 = benchmark_context["QQQ"]["return_126"]
    spy_return_126 = benchmark_context["SPY"]["return_126"]

    all_universe_rows = []
    for symbol in requested_symbols:
        row = _summary_to_momentum_stage_universe_row(
            history_summaries[symbol],
            qqq_return_126=qqq_return_126,
            spy_return_126=spy_return_126,
            rank_delta_4w=rank_delta_by_symbol.get(symbol),
            top_decile_age_weeks=top_decile_age_by_symbol.get(symbol),
        )
        all_universe_rows.append(row)

    full_rankings = _rankings(
        all_universe_rows,
        MOMENTUM_STAGE_RANKING_METRICS,
        MOMENTUM_STAGE_RANKING_DIRECTIONS,
    )
    rankings = _limit_rankings(full_rankings, top_n)
    ranking_details = _ranking_details(all_universe_rows, rankings, MOMENTUM_STAGE_RANKING_METRICS)
    selected_symbols = _select_momentum_stage_candidate_summary_symbols(
        rankings,
        limit=candidate_summary_limit,
    )
    if not selected_symbols:
        selected_symbols = [
            str(row["symbol"])
            for row in all_universe_rows
            if row.get("symbol")
        ][:candidate_summary_limit]
    rows_by_symbol = {
        str(row["symbol"]): row
        for row in all_universe_rows
        if row.get("symbol")
    }
    candidate_summary = [
        _momentum_stage_candidate_summary_row(symbol, rows_by_symbol[symbol], ranking_details)
        for symbol in selected_symbols
        if symbol in rows_by_symbol
    ]
    coverage = {
        "requested_count": len(symbols),
        "loaded_count": len(all_universe_rows),
        "failed_count": max(0, len(symbols) - len(all_universe_rows)),
        "top_n": top_n,
        "candidate_summary_limit": candidate_summary_limit,
        "ranking_count": len(rankings),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_profile": MOMENTUM_STAGE_EVIDENCE_PROFILE,
        "coverage": coverage,
        "rank_groups": MOMENTUM_STAGE_RANK_GROUPS,
        "ranking_limit": top_n,
        "candidate_summary_limit": candidate_summary_limit,
        "rankings": rankings,
        "ranking_details": ranking_details,
        "candidate_summary": candidate_summary,
        "benchmark_context": benchmark_context,
        "symbols": symbols,
        "timestep": timestep,
        "length": length,
        "as_of": as_of,
        "universe_summary_limit": candidate_summary_limit,
        "universe_summary_selection": {
            "mode": "top_rank_union",
            "candidate_count_before_limit": len(_unique_ranked_symbols_for_priority(
                rankings,
                MOMENTUM_STAGE_CANDIDATE_PRIORITY_RANKINGS,
            )),
            "included_symbols": selected_symbols,
            "priority": MOMENTUM_STAGE_CANDIDATE_PRIORITY_RANKINGS + ["multi_ranking_overlap"],
        },
        "universe_summary": candidate_summary,
        "loaded_tables": loaded_tables or {},
        "warnings": warnings or [],
    }


def _benchmark_context(
    history_summaries: dict[str, dict[str, Any]],
    benchmark_summaries: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    context: dict[str, dict[str, Any]] = {}
    for symbol in ("QQQ", "SPY"):
        summary = benchmark_summaries.get(symbol) or history_summaries.get(symbol) or {}
        return_126 = _compact_number(_dict(summary.get("momentum")).get("return_126"))
        context[symbol] = {
            "return_126": return_126,
            "available": return_126 is not None,
        }
    return context


def _summary_to_momentum_stage_universe_row(
    summary: dict[str, Any],
    *,
    qqq_return_126: Any,
    spy_return_126: Any,
    rank_delta_4w: int | None,
    top_decile_age_weeks: int | None,
) -> dict[str, Any]:
    price = _dict(summary.get("price"))
    momentum = _dict(summary.get("momentum"))
    momentum_stage = _dict(summary.get("momentum_stage"))
    risk = _dict(summary.get("risk"))
    return_126 = _finite_float(momentum.get("return_126"))
    excess_return_vs_qqq_6m = _return_difference(return_126, qqq_return_126)
    excess_return_vs_spy_6m = _return_difference(return_126, spy_return_126)
    row = {
        "symbol": summary.get("symbol"),
        "latest_close": _compact_number(price.get("latest_close")),
        "rank_delta_4w": _compact_number(rank_delta_4w),
        "top_decile_age_weeks": _compact_number(top_decile_age_weeks),
        "extension_ma50_pct": _compact_number(momentum_stage.get("extension_ma50_pct")),
        "atr_extension_20d": _compact_number(momentum_stage.get("atr_extension_20d")),
        "positive_day_ratio_3m": _compact_number(momentum_stage.get("positive_day_ratio_3m")),
        "max_day_return_share_3m": _compact_number(momentum_stage.get("max_day_return_share_3m")),
        "distance_to_252d_high_pct": _compact_number(momentum_stage.get("distance_to_252d_high_pct")),
        "recent_vs_intermediate_momentum": _compact_number(
            momentum_stage.get("recent_vs_intermediate_momentum")
        ),
        "up_down_volume_ratio_60d": _compact_number(momentum_stage.get("up_down_volume_ratio_60d")),
        "excess_return_vs_qqq_6m": _compact_number(excess_return_vs_qqq_6m),
        "excess_return_vs_spy_6m": _compact_number(excess_return_vs_spy_6m),
        "volatility_20": _compact_number(risk.get("volatility_20")),
    }
    row["stage_warning_flags"] = _momentum_stage_warning_flags(row)
    return row


def _return_difference(left: Any, right: Any) -> float | None:
    left = _finite_float(left)
    right = _finite_float(right)
    if left is None or right is None:
        return None
    return _finite_float(left - right)


def _momentum_stage_warning_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if any(row.get(metric) is None for metric in MOMENTUM_STAGE_ROW_FIELDS):
        flags.append("insufficient_history")
    if _threshold_gte(row.get("top_decile_age_weeks"), "stale_top_decile"):
        flags.append("stale_top_decile")
    if _threshold_gte(row.get("extension_ma50_pct"), "extreme_ma50_extension"):
        flags.append("extreme_ma50_extension")
    if _threshold_gte(row.get("atr_extension_20d"), "extreme_atr_extension"):
        flags.append("extreme_atr_extension")
    if _threshold_gte(row.get("recent_vs_intermediate_momentum"), "recent_overheat_vs_intermediate"):
        flags.append("recent_overheat_vs_intermediate")
    if _threshold_gte(row.get("max_day_return_share_3m"), "single_day_jump_concentration"):
        flags.append("single_day_jump_concentration")
    qqq_excess = _finite_float(row.get("excess_return_vs_qqq_6m"))
    spy_excess = _finite_float(row.get("excess_return_vs_spy_6m"))
    if qqq_excess is not None and spy_excess is not None and qqq_excess < 0 and spy_excess < 0:
        flags.append("benchmark_lag")
    up_down_volume_ratio = _finite_float(row.get("up_down_volume_ratio_60d"))
    if up_down_volume_ratio is None or up_down_volume_ratio < MOMENTUM_STAGE_WARNING_THRESHOLDS[
        "thin_or_missing_volume_support"
    ]:
        flags.append("thin_or_missing_volume_support")
    return flags


def _threshold_gte(value: Any, threshold_name: str) -> bool:
    numeric = _finite_float(value)
    return numeric is not None and numeric >= MOMENTUM_STAGE_WARNING_THRESHOLDS[threshold_name]


def _momentum_stage_candidate_summary_row(
    symbol: str,
    row: dict[str, Any],
    ranking_details: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    best_rank_by_group: dict[str, int] = {}
    ranking_count = 0
    best_rank: int | None = None

    for group_name, ranking_names in MOMENTUM_STAGE_RANK_GROUPS.items():
        group_best_rank: int | None = None
        for ranking_name in ranking_names:
            for entry in ranking_details.get(ranking_name, []):
                if entry.get("symbol") != symbol:
                    continue
                rank = entry.get("rank")
                if not isinstance(rank, int):
                    continue
                ranking_count += 1
                best_rank = rank if best_rank is None else min(best_rank, rank)
                group_best_rank = rank if group_best_rank is None else min(group_best_rank, rank)
        if group_best_rank is not None:
            best_rank_by_group[group_name] = group_best_rank

    return {
        "symbol": symbol,
        "latest_close": _compact_number(row.get("latest_close")),
        "stage_evidence_groups": list(best_rank_by_group),
        "stage_ranking_count": ranking_count,
        "stage_best_rank": best_rank,
        "stage_best_rank_by_group": best_rank_by_group,
        "rank_delta_4w": _compact_number(row.get("rank_delta_4w")),
        "top_decile_age_weeks": _compact_number(row.get("top_decile_age_weeks")),
        "extension_ma50_pct": _compact_number(row.get("extension_ma50_pct")),
        "atr_extension_20d": _compact_number(row.get("atr_extension_20d")),
        "positive_day_ratio_3m": _compact_number(row.get("positive_day_ratio_3m")),
        "max_day_return_share_3m": _compact_number(row.get("max_day_return_share_3m")),
        "distance_to_252d_high_pct": _compact_number(row.get("distance_to_252d_high_pct")),
        "recent_vs_intermediate_momentum": _compact_number(row.get("recent_vs_intermediate_momentum")),
        "up_down_volume_ratio_60d": _compact_number(row.get("up_down_volume_ratio_60d")),
        "excess_return_vs_qqq_6m": _compact_number(row.get("excess_return_vs_qqq_6m")),
        "excess_return_vs_spy_6m": _compact_number(row.get("excess_return_vs_spy_6m")),
        "stage_warning_flags": list(row.get("stage_warning_flags") or []),
        "volatility_20": _compact_number(row.get("volatility_20")),
    }


def _summary_to_universe_row(summary: dict[str, Any]) -> dict[str, Any]:
    price = _dict(summary.get("price"))
    momentum = _dict(summary.get("momentum"))
    volume = _dict(summary.get("volume"))
    trend = _dict(summary.get("trend"))
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
        "return_252": _compact_number(momentum.get("return_252")),
        "return_252_ex_skip_21": _compact_number(momentum.get("return_252_ex_skip_21")),
        "momentum_composite": _compact_number(scores.get("momentum_composite")),
        "composite_score": _compact_number(scores.get("composite_score")),
        "volume_vs_avg_20": _compact_number(volume.get("volume_vs_avg_20")),
        "dollar_volume_20": _compact_number(volume.get("dollar_volume_20")),
        "up_volume_ratio_20": _compact_number(volume.get("up_volume_ratio_20")),
        "trend_alignment": _compact_number(scores.get("trend_alignment")),
        "sma_stack_score": _compact_number(scores.get("sma_stack_score")),
        "adjusted_slope_90": _compact_number(scores.get("adjusted_slope_90")),
        "linear_regression_r2_90": _compact_number(trend.get("linear_regression_r2_90")),
        "return_63_over_volatility_20": _compact_number(scores.get("return_63_over_volatility_20")),
        "return_126_over_volatility_20": _compact_number(scores.get("return_126_over_volatility_20")),
        "return_252_over_volatility_63": _compact_number(scores.get("return_252_over_volatility_63")),
        "sharpe_like_63": _compact_number(scores.get("sharpe_like_63")),
        "calmar_like_126": _compact_number(scores.get("calmar_like_126")),
        "volume_confirmed_momentum": _compact_number(scores.get("volume_confirmed_momentum")),
        "volatility_20": _compact_number(risk.get("volatility_20")),
        "distance_to_high_252": _compact_number(range_metrics.get("distance_to_high_252")),
        "distance_to_high_63": _compact_number(range_metrics.get("distance_to_high_63")),
        "breakout_20_high_score": _compact_number(range_metrics.get("breakout_20_high_score")),
        "breakout_63_high_score": _compact_number(range_metrics.get("breakout_63_high_score")),
        "drawdown_from_high_60": _compact_number(range_metrics.get("drawdown_from_high_60")),
    }


def _rankings(
    rows: list[dict[str, Any]],
    ranking_metrics: dict[str, str] = RANKING_METRICS,
    ranking_directions: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    ranking_directions = ranking_directions or {}
    return {
        ranking_name: _rank_symbols(
            rows,
            metric_name,
            direction=ranking_directions.get(ranking_name, "desc"),
        )
        for ranking_name, metric_name in ranking_metrics.items()
    }


def _limit_rankings(rankings: dict[str, list[str]], limit: int) -> dict[str, list[str]]:
    return {
        name: symbols[:limit]
        for name, symbols in rankings.items()
    }


def _ranking_details(
    rows: list[dict[str, Any]],
    rankings: dict[str, list[str]],
    ranking_metrics: dict[str, str] = RANKING_METRICS,
) -> dict[str, list[dict[str, Any]]]:
    rows_by_symbol = {
        str(row["symbol"]): row
        for row in rows
        if row.get("symbol")
    }
    details: dict[str, list[dict[str, Any]]] = {}
    for ranking_name, symbols in rankings.items():
        metric_name = ranking_metrics[ranking_name]
        entries = []
        for index, symbol in enumerate(symbols, start=1):
            row = rows_by_symbol.get(symbol)
            if row is None:
                continue
            entries.append(
                {
                    "rank": index,
                    "symbol": symbol,
                    "value": _compact_number(row.get(metric_name)),
                }
            )
        details[ranking_name] = entries
    return details


def _candidate_summary_row(
    symbol: str,
    row: dict[str, Any],
    ranking_details: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    best_rank_by_group: dict[str, int] = {}
    ranking_count = 0
    best_rank: int | None = None

    for group_name, ranking_names in RANK_GROUPS.items():
        group_best_rank: int | None = None
        for ranking_name in ranking_names:
            for entry in ranking_details.get(ranking_name, []):
                if entry.get("symbol") != symbol:
                    continue
                rank = entry.get("rank")
                if not isinstance(rank, int):
                    continue
                ranking_count += 1
                best_rank = rank if best_rank is None else min(best_rank, rank)
                group_best_rank = rank if group_best_rank is None else min(group_best_rank, rank)
        if group_best_rank is not None:
            best_rank_by_group[group_name] = group_best_rank

    return {
        "symbol": symbol,
        "latest_close": _compact_number(row.get("latest_close")),
        "return_5": _compact_number(row.get("return_5")),
        "momentum_composite": _compact_number(row.get("momentum_composite")),
        "composite_score": _compact_number(row.get("composite_score")),
        "volume_vs_avg_20": _compact_number(row.get("volume_vs_avg_20")),
        "drawdown_from_high_60": _compact_number(row.get("drawdown_from_high_60")),
        "volatility_20": _compact_number(row.get("volatility_20")),
        "evidence_groups": list(best_rank_by_group),
        "ranking_count": ranking_count,
        "best_rank": best_rank,
        "best_rank_by_group": best_rank_by_group,
    }


def _select_candidate_summary_symbols(rankings: dict[str, list[str]], *, limit: int) -> list[str]:
    candidates = _unique_ranked_symbols(rankings)
    if len(candidates) <= limit:
        return candidates

    appearance_counts: dict[str, int] = {}
    best_rank: dict[str, int] = {}
    priority_best_rank: dict[str, int] = {}
    first_seen_order: dict[str, int] = {}
    official_rankings = {
        ranking_name
        for ranking_names in RANK_GROUPS.values()
        for ranking_name in ranking_names
    }
    for ranking_name, ranking in rankings.items():
        for index, symbol in enumerate(ranking):
            first_seen_order.setdefault(symbol, len(first_seen_order))
            if ranking_name in official_rankings:
                appearance_counts[symbol] = appearance_counts.get(symbol, 0) + 1
            best_rank[symbol] = min(best_rank.get(symbol, index), index)
            if ranking_name in CANDIDATE_PRIORITY_RANKINGS:
                priority_best_rank[symbol] = min(priority_best_rank.get(symbol, index), index)

    ordered = list(candidates)
    ordered.sort(
        key=lambda symbol: (
            -appearance_counts.get(symbol, 0),
            priority_best_rank.get(symbol, len(candidates)),
            best_rank.get(symbol, len(candidates)),
            first_seen_order.get(symbol, len(candidates)),
            symbol,
        )
    )
    return ordered[:limit]


def _select_momentum_stage_candidate_summary_symbols(rankings: dict[str, list[str]], *, limit: int) -> list[str]:
    candidates = _unique_ranked_symbols_for_priority(rankings, MOMENTUM_STAGE_CANDIDATE_PRIORITY_RANKINGS)
    if len(candidates) <= limit:
        return candidates

    overlap_counts: dict[str, int] = {}
    best_rank: dict[str, int] = {}
    best_priority_rank: dict[str, int] = {}
    best_priority_index: dict[str, int] = {}
    official_rankings = {
        ranking_name
        for ranking_names in MOMENTUM_STAGE_RANK_GROUPS.values()
        for ranking_name in ranking_names
    }
    for ranking_name, ranking in rankings.items():
        for index, symbol in enumerate(ranking):
            if ranking_name in official_rankings:
                overlap_counts[symbol] = overlap_counts.get(symbol, 0) + 1
            best_rank[symbol] = min(best_rank.get(symbol, index), index)
            if ranking_name in MOMENTUM_STAGE_CANDIDATE_PRIORITY_RANKINGS:
                priority_index = MOMENTUM_STAGE_CANDIDATE_PRIORITY_RANKINGS.index(ranking_name)
                best_priority_rank[symbol] = min(best_priority_rank.get(symbol, index), index)
                best_priority_index[symbol] = min(best_priority_index.get(symbol, priority_index), priority_index)

    # Candidate truncation is intentionally overlap-led, then priority-aware.
    # This keeps repeated evidence across stage groups from being crowded out by the first priority list.
    ordered = list(candidates)
    ordered.sort(
        key=lambda symbol: (
            -overlap_counts.get(symbol, 0),
            best_priority_rank.get(symbol, len(candidates)),
            best_priority_index.get(symbol, len(MOMENTUM_STAGE_CANDIDATE_PRIORITY_RANKINGS)),
            best_rank.get(symbol, len(candidates)),
            symbol,
        )
    )
    return ordered[:limit]


def _unique_ranked_symbols(rankings: dict[str, list[str]]) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for ranking_name in CANDIDATE_PRIORITY_RANKINGS:
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


def _unique_ranked_symbols_for_priority(rankings: dict[str, list[str]], priority_rankings: list[str]) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for ranking_name in priority_rankings:
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


def _rank_delta_4w_by_symbol(history_frames: dict[str, pd.DataFrame], symbols: list[str]) -> dict[str, int | None]:
    latest_returns = _return_126_by_symbol_as_of_offset(history_frames, symbols, offset=0)
    prior_returns = _return_126_by_symbol_as_of_offset(history_frames, symbols, offset=21)
    latest_ranks = _rank_metric_value_map(latest_returns)
    prior_ranks = _rank_metric_value_map(prior_returns)
    return {
        symbol: prior_ranks[symbol] - latest_ranks[symbol]
        if symbol in prior_ranks and symbol in latest_ranks
        else None
        for symbol in symbols
    }


def _top_decile_age_weeks_by_symbol(
    history_frames: dict[str, pd.DataFrame],
    symbols: list[str],
) -> dict[str, int | None]:
    return {
        symbol: _top_decile_age_weeks(symbol, history_frames, symbols)
        for symbol in symbols
    }


def _top_decile_age_weeks(symbol: str, history_frames: dict[str, pd.DataFrame], symbols: list[str]) -> int | None:
    if symbol not in history_frames or not symbols:
        return None
    top_decile_rank = max(1, math.ceil(len(symbols) * 0.10))
    age_weeks = 0
    offset = 0
    while True:
        returns = _return_126_by_symbol_as_of_offset(history_frames, symbols, offset=offset)
        ranks = _rank_metric_value_map(returns)
        if symbol not in ranks:
            return age_weeks if age_weeks > 0 else None
        if ranks[symbol] > top_decile_rank:
            return age_weeks
        age_weeks += 1
        offset += 5


def _return_126_by_symbol_as_of_offset(
    history_frames: dict[str, pd.DataFrame],
    symbols: list[str],
    *,
    offset: int,
) -> dict[str, float | None]:
    return {
        symbol: _period_return_as_of_offset(
            _numeric_series(_truncate_to_last_finite_close(_sort_frame(frame)), "close"),
            126,
            offset,
        )
        for symbol in symbols
        if (frame := history_frames.get(symbol)) is not None
    }


def _period_return_as_of_offset(series: pd.Series | None, window: int, offset: int) -> float | None:
    if series is None or len(series) <= window + offset:
        return None
    endpoint_index = len(series) - 1 - offset
    start_index = endpoint_index - window
    endpoint = _finite_float(series.iloc[endpoint_index])
    start = _finite_float(series.iloc[start_index])
    if endpoint is None or start in (None, 0):
        return None
    return _finite_float(endpoint / start - 1.0)


def _rank_metric_value_map(values_by_symbol: dict[str, float | None]) -> dict[str, int]:
    rankable = [
        (symbol, float(value))
        for symbol, value in values_by_symbol.items()
        if _is_rankable(value)
    ]
    return {
        symbol: index
        for index, (symbol, _) in enumerate(
            sorted(rankable, key=lambda item: (-item[1], item[0])),
            start=1,
        )
    }


def _rank_symbols(rows: list[dict[str, Any]], key: str, *, direction: str = "desc") -> list[str]:
    rankable = [
        (str(row["symbol"]), float(row[key]))
        for row in rows
        if row.get("symbol") and _is_rankable(row.get(key))
    ]
    if direction == "asc":
        return [symbol for symbol, _ in sorted(rankable, key=lambda item: (item[1], item[0]))]
    return [symbol for symbol, _ in sorted(rankable, key=lambda item: (-item[1], item[0]))]


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


def _truncate_to_last_finite_close(frame: pd.DataFrame) -> pd.DataFrame:
    if "close" not in frame.columns:
        return frame
    close = pd.to_numeric(frame["close"], errors="coerce")
    finite_close = close.map(lambda value: _finite_float(value) is not None)
    if not finite_close.any():
        return frame
    last_position = int(finite_close.to_numpy().nonzero()[0][-1])
    return frame.iloc[: last_position + 1].copy()


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


def _atr(
    high_series: pd.Series | None,
    low_series: pd.Series | None,
    close_series: pd.Series | None,
    window: int,
) -> float | None:
    if high_series is None or low_series is None or close_series is None:
        return None
    data = pd.DataFrame({"high": high_series, "low": low_series, "close": close_series}).dropna()
    data = data[
        data["high"].map(lambda value: _finite_float(value) is not None)
        & data["low"].map(lambda value: _finite_float(value) is not None)
        & data["close"].map(lambda value: _finite_float(value) is not None)
    ].reset_index(drop=True)
    if len(data) < window:
        return None
    previous_close = data["close"].shift(1)
    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - previous_close).abs(),
            (data["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = _finite_float(true_range.tail(window).mean())
    if atr in (None, 0):
        return None
    return atr


def _recent_returns(series: pd.Series | None, window: int) -> pd.Series | None:
    if series is None or len(series) <= 1:
        return None
    returns = series.pct_change().dropna()
    returns = returns[returns.map(lambda value: _finite_float(value) is not None)].tail(window)
    if returns.empty:
        return None
    return returns


def _positive_day_ratio(series: pd.Series | None, window: int) -> float | None:
    returns = _recent_returns(series, window)
    if returns is None:
        return None
    return _finite_float((returns > 0).sum() / len(returns))


def _max_positive_return_share(series: pd.Series | None, window: int) -> float | None:
    returns = _recent_returns(series, window)
    if returns is None:
        return None
    positive_returns = returns[returns > 0]
    if positive_returns.empty:
        return None
    total_positive_return = _finite_float(positive_returns.sum())
    if total_positive_return in (None, 0):
        return None
    return _finite_float(positive_returns.max() / total_positive_return)


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


def _up_down_volume_ratio(close_series: pd.Series | None, volume_series: pd.Series | None, window: int) -> float | None:
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
    up_volume = _finite_float(volumes[returns > 0].sum())
    down_volume = _finite_float(volumes[returns < 0].sum())
    if up_volume is None or down_volume in (None, 0):
        return None
    return _finite_float(up_volume / down_volume)
