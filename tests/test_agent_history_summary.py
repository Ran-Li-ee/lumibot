from __future__ import annotations

import json

import pandas as pd
import pytest

from lumibot.components.agents import history_summary as history_summary_module
from lumibot.components.agents.builtins import BuiltinTools
from lumibot.components.agents.history_summary import (
    _momentum_stage_warning_flags,
    build_universe_history_summary,
    compute_history_summary,
)
from lumibot.components.agents.runtime import _prune_tool_response_for_context_window


def _frame(length: int = 260) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=length, freq="D")
    close = pd.Series(range(100, 100 + length), dtype="float64")
    return pd.DataFrame(
        {
            "Date": dates,
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": range(1000, 1000 + length),
        }
    )


def _trend_frame(length: int = 260, *, start: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=length, freq="D")
    close = pd.Series([start + step * index for index in range(length)], dtype="float64")
    return pd.DataFrame(
        {
            "Date": dates,
            "open": close - 0.25,
            "high": close + 0.75,
            "low": close - 0.75,
            "close": close,
            "volume": [1000 + index * 10 for index in range(length)],
        }
    )


def _stage_frame(
    *,
    length: int = 320,
    start: float = 100.0,
    daily_return: float = 0.001,
    down_every: int | None = None,
    down_return: float = -0.001,
    jump_at: int | None = None,
    jump_return: float = 0.0,
    volume_start: float = 1_000.0,
    up_volume: float = 2_000.0,
    down_volume: float = 900.0,
) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=length, freq="D")
    close_values = [start]
    for index in range(1, length):
        if jump_at == index:
            move = jump_return
        elif down_every is not None and index % down_every == 0:
            move = down_return
        else:
            move = daily_return
        close_values.append(close_values[-1] * (1.0 + move))
    close = pd.Series(close_values, dtype="float64")
    returns = close.pct_change().fillna(0.0)
    volume = pd.Series(
        [
            volume_start
            if index == 0
            else (up_volume if returns.iloc[index] > 0 else down_volume)
            for index in range(length)
        ],
        dtype="float64",
    )
    return pd.DataFrame(
        {
            "Date": dates,
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": volume,
        }
    )


def _stage_frame_from_closes(
    closes: list[float],
    *,
    up_volume: float = 2_000.0,
    down_volume: float = 1_000.0,
) -> pd.DataFrame:
    close = pd.Series(closes, dtype="float64")
    returns = close.pct_change().fillna(0.0)
    volume = pd.Series(
        [
            1_000.0
            if index == 0
            else (up_volume if returns.iloc[index] > 0 else down_volume)
            for index in range(len(close))
        ],
        dtype="float64",
    )
    return pd.DataFrame(
        {
            "Date": pd.date_range("2024-01-01", periods=len(close), freq="D"),
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": volume,
        }
    )


def _breakout_frame() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=80, freq="D")
    close = [100.0] * 79 + [121.0]
    high = [120.0] * 79 + [121.5]
    low = [99.0] * 80
    volume = [1000] * 80
    return pd.DataFrame({"Date": dates, "close": close, "high": high, "low": low, "volume": volume})


def _rankable_summary(
    symbol: str,
    *,
    composite_score: float,
    momentum_composite: float,
    return_21: float,
    return_63: float,
    return_126: float,
    trend_alignment: float,
    return_252: float | None = None,
    return_252_ex_skip_21: float | None = None,
    sma_stack_score: float | None = None,
    adjusted_slope_90: float | None = None,
    linear_regression_r2_90: float | None = None,
    return_252_over_volatility_63: float | None = None,
    sharpe_like_63: float | None = None,
    calmar_like_126: float | None = None,
    distance_to_high_252: float | None = None,
    distance_to_high_63: float | None = None,
    breakout_20_high_score: float | None = None,
    breakout_63_high_score: float | None = None,
    drawdown_from_high_60: float | None = None,
    volume_vs_avg_20: float | None = None,
    dollar_volume_20: float | None = None,
    up_volume_ratio_20: float | None = None,
    volume_confirmed_momentum: float | None = None,
) -> dict:
    return {
        "symbol": symbol,
        "price": {"latest_close": 100.0},
        "momentum": {
            "return_5": 0.0,
            "return_21": return_21,
            "return_63": return_63,
            "return_126": return_126,
            "return_252": return_252,
            "return_252_ex_skip_21": return_252_ex_skip_21,
        },
        "trend": {
            "linear_regression_r2_90": linear_regression_r2_90,
        },
        "scores": {
            "composite_score": composite_score,
            "momentum_composite": momentum_composite,
            "trend_alignment": trend_alignment,
            "sma_stack_score": sma_stack_score,
            "adjusted_slope_90": adjusted_slope_90,
            "return_63_over_volatility_20": return_63 / 0.1,
            "return_126_over_volatility_20": return_126 / 0.1,
            "return_252_over_volatility_63": return_252_over_volatility_63,
            "sharpe_like_63": sharpe_like_63,
            "calmar_like_126": calmar_like_126,
            "volume_confirmed_momentum": volume_confirmed_momentum,
        },
        "volume": {
            "volume_vs_avg_20": volume_vs_avg_20,
            "dollar_volume_20": dollar_volume_20,
            "up_volume_ratio_20": up_volume_ratio_20,
        },
        "risk": {"volatility_20": 0.01},
        "range": {
            "distance_to_high_252": distance_to_high_252,
            "distance_to_high_63": distance_to_high_63,
            "breakout_20_high_score": breakout_20_high_score,
            "breakout_63_high_score": breakout_63_high_score,
            "drawdown_from_high_60": drawdown_from_high_60,
        },
    }


def _stage_rankable_summary(
    symbol: str,
    *,
    return_126: float,
    extension_ma50_pct: float = 0.05,
    atr_extension_20d: float = 1.0,
    positive_day_ratio_3m: float = 0.5,
    max_day_return_share_3m: float = 0.1,
    distance_to_252d_high_pct: float = -0.05,
    recent_vs_intermediate_momentum: float = 0.0,
    up_down_volume_ratio_60d: float | None = 1.5,
    volatility_20: float = 0.01,
) -> dict:
    return {
        "symbol": symbol,
        "price": {"latest_close": 100.0},
        "momentum": {"return_126": return_126},
        "momentum_stage": {
            "extension_ma50_pct": extension_ma50_pct,
            "atr_extension_20d": atr_extension_20d,
            "positive_day_ratio_3m": positive_day_ratio_3m,
            "max_day_return_share_3m": max_day_return_share_3m,
            "distance_to_252d_high_pct": distance_to_252d_high_pct,
            "recent_vs_intermediate_momentum": recent_vs_intermediate_momentum,
            "up_down_volume_ratio_60d": up_down_volume_ratio_60d,
        },
        "risk": {"volatility_20": volatility_20},
    }


def _stage_warning_row(**overrides) -> dict:
    row = {
        "rank_delta_4w": 1,
        "top_decile_age_weeks": 1,
        "extension_ma50_pct": 0.05,
        "atr_extension_20d": 1.0,
        "positive_day_ratio_3m": 0.60,
        "max_day_return_share_3m": 0.10,
        "distance_to_252d_high_pct": -0.02,
        "recent_vs_intermediate_momentum": 0.0,
        "up_down_volume_ratio_60d": 1.5,
        "excess_return_vs_qqq_6m": 0.05,
        "excess_return_vs_spy_6m": 0.06,
        "volatility_20": 0.01,
    }
    row.update(overrides)
    return row


def test_history_tool_descriptions_are_summary_first():
    tool_names = {
        "market_load_history_table",
        "market_load_history_tables_summary",
        "duckdb_query",
    }
    tools = {
        definition.name: definition.binder(object(), object())
        for definition in BuiltinTools.all()
        if definition.name in tool_names
    }

    single = tools["market_load_history_table"].description.lower()
    multi = tools["market_load_history_tables_summary"].description.lower()
    duckdb = tools["duckdb_query"].description.lower()

    assert "targeted single-symbol follow-up" in single
    assert "not the default tool for every symbol" in single
    assert "summary-first" in single
    assert "cross-symbol comparison" in multi
    assert "default tool for multi-symbol" in multi
    assert "evidence_profile" in multi
    assert "legacy" in multi
    assert "momentum_stage" in multi
    assert "benchmark_symbols" in multi
    assert "freshness" in multi
    assert "smoothness" in multi
    assert "near-high" in multi
    assert "benchmark-relative" in multi
    assert "reference fields" in multi
    assert "volume confirmation" in multi
    assert "five evidence groups" not in multi
    assert "top_n" in multi
    assert "candidate_summary_limit" in multi
    assert "ranking_details" in multi
    assert "duckdb sql is only" in multi
    assert "targeted follow-up" in multi
    assert "by_composite_score" not in multi
    assert "targeted follow-up" in duckdb
    assert "computed summaries or rankings are insufficient" in duckdb
    assert "load a table first with market_load_history_table, then analyze it here" not in duckdb


def test_compute_history_summary_returns_core_groups():
    summary = compute_history_summary(
        _frame(260),
        symbol="QQQ",
        timestep="day",
        as_of="2024-09-05T09:30:00-04:00",
    )

    assert summary["schema_version"] == "1.0"
    assert summary["symbol"] == "QQQ"
    assert summary["timestep"] == "day"
    assert summary["as_of"] == "2024-09-05T09:30:00-04:00"
    assert set(summary) >= {
        "data_window",
        "price",
        "momentum",
        "trend",
        "range",
        "risk",
        "availability",
    }
    assert summary["data_window"]["row_count"] == 260
    assert summary["data_window"]["start"] == "2024-01-01T00:00:00"
    assert summary["data_window"]["end"] == "2024-09-16T00:00:00"


def test_compute_history_summary_calculates_momentum_and_trend():
    frame = _frame(260)
    latest = 359.0

    summary = compute_history_summary(frame, symbol="QQQ", timestep="day", as_of=None)

    assert summary["price"]["latest_close"] == latest
    assert summary["momentum"]["return_20"] == pytest.approx(latest / 339.0 - 1.0)
    assert summary["momentum"]["return_60"] == pytest.approx(latest / 299.0 - 1.0)
    assert summary["momentum"]["return_120"] == pytest.approx(latest / 239.0 - 1.0)
    assert summary["trend"]["sma_20"] == pytest.approx(sum(range(340, 360)) / 20)
    assert summary["trend"]["sma_50"] == pytest.approx(sum(range(310, 360)) / 50)
    assert summary["trend"]["sma_200"] == pytest.approx(sum(range(160, 360)) / 200)
    assert summary["trend"]["price_vs_sma_20"] == pytest.approx(latest / summary["trend"]["sma_20"] - 1.0)


def test_compute_history_summary_adds_ranking_windows_and_scores():
    frame = _frame(260)
    latest = 359.0

    summary = compute_history_summary(frame, symbol="QQQ", timestep="day", as_of=None)

    assert summary["momentum"]["return_21"] == pytest.approx(latest / 338.0 - 1.0)
    assert summary["momentum"]["return_63"] == pytest.approx(latest / 296.0 - 1.0)
    assert summary["momentum"]["return_126"] == pytest.approx(latest / 233.0 - 1.0)
    assert summary["momentum"]["return_252"] == pytest.approx(latest / 107.0 - 1.0)
    assert summary["scores"]["momentum_composite"] == pytest.approx(
        (
            summary["momentum"]["return_21"]
            + summary["momentum"]["return_63"]
            + summary["momentum"]["return_126"]
        )
        / 3
    )
    assert summary["scores"]["trend_alignment"] == 3
    assert summary["availability"]["return_21"] is True
    assert summary["availability"]["return_63"] is True
    assert summary["availability"]["return_126"] is True
    assert summary["availability"]["return_252"] is True
    assert summary["availability"]["momentum_composite"] is True
    assert summary["availability"]["trend_alignment"] is True


def test_compute_history_summary_adds_five_group_momentum_and_trend_quality_metrics():
    frame = _trend_frame(260)
    summary = compute_history_summary(frame, symbol="TREND", timestep="day", as_of=None)

    skip_endpoint = float(frame["close"].iloc[-22])
    skip_start = float(frame["close"].iloc[-253])

    assert summary["momentum"]["return_252_ex_skip_21"] == pytest.approx(skip_endpoint / skip_start - 1.0)
    assert summary["scores"]["sma_stack_score"] == 4
    assert summary["trend"]["linear_regression_slope_90"] is not None
    assert summary["trend"]["linear_regression_r2_90"] > 0.99
    assert summary["scores"]["adjusted_slope_90"] is not None
    assert summary["availability"]["return_252_ex_skip_21"] is True
    assert summary["availability"]["sma_stack_score"] is True
    assert summary["availability"]["adjusted_slope_90"] is True


def test_compute_history_summary_adds_risk_adjusted_momentum_metrics():
    frame = _trend_frame(260)
    summary = compute_history_summary(frame, symbol="RISK", timestep="day", as_of=None)

    assert summary["risk"]["volatility_63"] is not None
    assert summary["risk"]["max_drawdown_126"] == pytest.approx(0.0)
    assert summary["scores"]["return_252_over_volatility_63"] is not None
    assert summary["scores"]["sharpe_like_63"] is not None
    assert summary["scores"]["calmar_like_126"] is None
    assert summary["availability"]["return_252_over_volatility_63"] is True
    assert summary["availability"]["sharpe_like_63"] is True
    assert summary["availability"]["calmar_like_126"] is False


def test_compute_history_summary_adds_breakout_and_volume_confirmation_metrics():
    frame = _breakout_frame()
    frame.loc[60:79, "volume"] = [1000, 1200, 900, 1300, 1100, 1400, 1000, 1500, 1200, 1600] * 2

    summary = compute_history_summary(frame, symbol="BREAK", timestep="day", as_of=None)

    assert summary["range"]["distance_to_high_63"] == pytest.approx(121.0 / 121.5 - 1.0)
    assert summary["range"]["breakout_20_high_score"] == pytest.approx(121.0 / 120.0 - 1.0)
    assert summary["range"]["breakout_63_high_score"] == pytest.approx(121.0 / 120.0 - 1.0)
    assert summary["range"]["drawdown_from_high_60"] == pytest.approx(121.0 / 121.5 - 1.0)
    assert summary["volume"]["dollar_volume_20"] is not None
    assert summary["volume"]["up_volume_ratio_20"] is not None
    assert summary["scores"]["volume_confirmed_momentum"] is not None


def test_compute_history_summary_adds_momentum_stage_metrics_and_availability_keys():
    frame = _stage_frame(daily_return=0.002, up_volume=3_000.0, down_volume=1_000.0)
    summary = compute_history_summary(frame, symbol="STAGE", timestep="day", as_of=None)
    close = frame["close"]
    returns = close.pct_change().dropna()
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - close.shift(1)).abs(),
            (frame["low"] - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    positive_returns_3m = returns.tail(63)[returns.tail(63) > 0]

    assert summary["momentum_stage"]["extension_ma50_pct"] == pytest.approx(
        close.iloc[-1] / close.tail(50).mean() - 1.0
    )
    assert summary["momentum_stage"]["atr_extension_20d"] == pytest.approx(
        (close.iloc[-1] - close.tail(20).mean()) / true_range.tail(20).mean()
    )
    assert summary["momentum_stage"]["positive_day_ratio_3m"] == pytest.approx(1.0)
    assert summary["momentum_stage"]["max_day_return_share_3m"] == pytest.approx(
        positive_returns_3m.max() / positive_returns_3m.sum()
    )
    assert summary["momentum_stage"]["distance_to_252d_high_pct"] == pytest.approx(
        close.iloc[-1] / frame["high"].tail(252).max() - 1.0
    )
    assert summary["momentum_stage"]["recent_vs_intermediate_momentum"] == pytest.approx(
        summary["momentum"]["return_21"] - summary["momentum"]["return_252_ex_skip_21"]
    )
    assert summary["momentum_stage"]["up_down_volume_ratio_60d"] is None
    for key in (
        "extension_ma50_pct",
        "atr_extension_20d",
        "positive_day_ratio_3m",
        "max_day_return_share_3m",
        "distance_to_252d_high_pct",
        "recent_vs_intermediate_momentum",
        "up_down_volume_ratio_60d",
    ):
        assert key in summary["availability"]


def test_compute_history_summary_calculates_up_down_volume_ratio_60d():
    frame = _stage_frame(
        daily_return=0.002,
        down_every=3,
        down_return=-0.001,
        up_volume=3_000.0,
        down_volume=1_000.0,
    )
    summary = compute_history_summary(frame, symbol="VOL", timestep="day", as_of=None)
    returns = frame["close"].pct_change().dropna().tail(60)
    volumes = frame["volume"].iloc[-60:]
    expected = volumes[returns > 0].sum() / volumes[returns < 0].sum()

    assert summary["momentum_stage"]["up_down_volume_ratio_60d"] == pytest.approx(expected)
    assert summary["availability"]["up_down_volume_ratio_60d"] is True


def test_compute_history_summary_ignores_trailing_rows_without_finite_close_for_dependent_metrics():
    frame = _breakout_frame()
    frame.loc[79, "close"] = 119.0
    frame.loc[79, "high"] = 120.0
    frame.loc[79, "volume"] = 1_000.0
    frame.loc[80] = {
        "Date": pd.Timestamp("2024-03-21"),
        "close": float("nan"),
        "high": 500.0,
        "low": 10.0,
        "volume": 1_000_000.0,
    }
    frame.loc[81] = {
        "Date": pd.Timestamp("2024-03-22"),
        "close": float("nan"),
        "high": 600.0,
        "low": 10.0,
        "volume": 2_000_000.0,
    }

    summary = compute_history_summary(frame, symbol="ALIGN", timestep="day", as_of=None)

    assert summary["data_window"]["end"] == "2024-03-20T00:00:00"
    assert summary["price"]["latest_close"] == 119.0
    assert summary["range"]["distance_to_high_63"] == pytest.approx(119.0 / 120.0 - 1.0)
    assert summary["range"]["breakout_20_high_score"] == pytest.approx(119.0 / 120.0 - 1.0)
    assert summary["range"]["breakout_63_high_score"] == pytest.approx(119.0 / 120.0 - 1.0)
    assert summary["volume"]["latest_volume"] == 1_000.0
    assert summary["volume"]["avg_volume_20"] == pytest.approx(1_000.0)
    assert summary["volume"]["dollar_volume_20"] == pytest.approx(119_000.0)


def test_compute_history_summary_new_fixed_window_metrics_require_full_lookbacks():
    summary = compute_history_summary(_trend_frame(63), symbol="SHORT", timestep="day", as_of=None)

    assert summary["risk"]["volatility_63"] is None
    assert summary["scores"]["sharpe_like_63"] is None
    assert summary["range"]["distance_to_high_63"] is not None
    assert summary["range"]["breakout_63_high_score"] is None
    assert summary["volume"]["dollar_volume_20"] is not None
    assert summary["volume"]["up_volume_ratio_20"] is not None

    summary = compute_history_summary(_trend_frame(62), symbol="SHORT", timestep="day", as_of=None)

    assert summary["range"]["distance_to_high_63"] is None

    summary = compute_history_summary(_trend_frame(125), symbol="SHORT", timestep="day", as_of=None)

    assert summary["risk"]["max_drawdown_126"] is None
    assert summary["scores"]["calmar_like_126"] is None
    assert summary["risk"]["max_drawdown_60"] is not None


def test_compute_history_summary_volume_confirmation_requires_full_twenty_returns():
    summary = compute_history_summary(_trend_frame(20), symbol="SHORT", timestep="day", as_of=None)

    assert summary["volume"]["dollar_volume_20"] is not None
    assert summary["volume"]["up_volume_ratio_20"] is None
    assert summary["scores"]["volume_confirmed_momentum"] is None
    assert summary["availability"]["up_volume_ratio_20"] is False
    assert summary["availability"]["volume_confirmed_momentum"] is False

    summary = compute_history_summary(_trend_frame(64), symbol="ENOUGH", timestep="day", as_of=None)

    assert summary["momentum"]["return_63"] is not None
    assert summary["volume"]["up_volume_ratio_20"] is not None
    assert summary["scores"]["volume_confirmed_momentum"] is not None


def test_compute_history_summary_calculates_range_and_drawdown():
    frame = _frame(260)

    summary = compute_history_summary(frame, symbol="QQQ", timestep="day", as_of=None)

    assert summary["range"]["high_252"] == 360.0
    assert summary["range"]["low_252"] == 107.0
    assert summary["range"]["distance_to_high_252"] == pytest.approx(359.0 / 360.0 - 1.0)
    assert summary["range"]["distance_to_low_252"] == pytest.approx(359.0 / 107.0 - 1.0)
    assert summary["risk"]["max_drawdown_60"] == 0.0


def test_compute_history_summary_drawdown_detects_peak_to_trough():
    frame = pd.DataFrame(
        {
            "Date": pd.date_range("2024-01-01", periods=6, freq="D"),
            "close": [100.0, 120.0, 90.0, 110.0, 80.0, 100.0],
        }
    )

    summary = compute_history_summary(frame, symbol="DD", timestep="day", as_of=None)

    assert summary["risk"]["max_drawdown_60"] == pytest.approx(80.0 / 120.0 - 1.0)


def test_compute_history_summary_short_data_marks_unavailable_metrics():
    summary = compute_history_summary(_frame(10), symbol="SHORT", timestep="day", as_of=None)

    assert summary["price"]["latest_close"] == 109.0
    assert summary["momentum"]["return_20"] is None
    assert summary["trend"]["sma_20"] is None
    assert summary["availability"]["return_20"] is False
    assert summary["availability"]["sma_20"] is False
    assert summary["range"]["high_252"] == 110.0
    assert summary["range"]["low_252"] == 99.0


def test_compute_history_summary_new_windows_are_unavailable_when_data_is_short():
    summary = compute_history_summary(_frame(30), symbol="SHORT", timestep="day", as_of=None)

    assert summary["momentum"]["return_21"] is not None
    assert summary["momentum"]["return_63"] is None
    assert summary["momentum"]["return_126"] is None
    assert summary["momentum"]["return_252"] is None
    assert summary["scores"]["momentum_composite"] == summary["momentum"]["return_21"]
    assert summary["availability"]["return_63"] is False
    assert summary["availability"]["return_126"] is False
    assert summary["availability"]["return_252"] is False
    assert summary["availability"]["momentum_composite"] is True


def test_compute_history_summary_missing_close_returns_warnings():
    frame = pd.DataFrame({"Date": pd.date_range("2024-01-01", periods=3), "open": [1, 2, 3]})

    summary = compute_history_summary(frame, symbol="BAD", timestep="day", as_of=None)

    assert summary["data_window"]["row_count"] == 3
    assert summary["price"]["latest_close"] is None
    assert summary["momentum"]["return_20"] is None
    assert summary["availability"]["latest_close"] is False
    assert any("close" in warning.lower() for warning in summary["warnings"])


def test_compute_history_summary_sanitizes_non_finite_source_values():
    frame = pd.DataFrame(
        {
            "Date": pd.date_range("2024-01-01", periods=3, freq="D"),
            "high": [float("inf"), float("-inf"), float("nan")],
            "low": [float("-inf"), float("nan"), float("inf")],
            "close": [float("inf"), float("-inf"), float("nan")],
        }
    )

    summary = compute_history_summary(frame, symbol="BAD", timestep="day", as_of=None)

    json.dumps(summary, allow_nan=False)
    assert summary["price"]["latest_close"] is None
    for key in (
        "return_5",
        "return_10",
        "return_20",
        "return_21",
        "return_60",
        "return_63",
        "return_120",
        "return_126",
        "return_252",
        "return_252_ex_skip_21",
    ):
        assert summary["momentum"][key] is None
    assert summary["trend"] == {
        "sma_20": None,
        "sma_50": None,
        "sma_200": None,
        "price_vs_sma_20": None,
        "price_vs_sma_50": None,
        "price_vs_sma_200": None,
        "linear_regression_slope_90": None,
        "linear_regression_r2_90": None,
    }
    assert summary["range"] == {
        "high_252": None,
        "low_252": None,
        "distance_to_high_252": None,
        "distance_to_low_252": None,
        "distance_to_high_63": None,
        "breakout_20_high_score": None,
        "breakout_63_high_score": None,
        "drawdown_from_high_20": None,
        "drawdown_from_high_60": None,
        "drawdown_from_high_252": None,
    }
    assert summary["risk"] == {
        "max_drawdown_60": None,
        "max_drawdown_126": None,
        "volatility_20": None,
        "volatility_63": None,
    }
    assert summary["scores"] == {
        "momentum_composite": None,
        "trend_alignment": None,
        "sma_stack_score": None,
        "adjusted_slope_90": None,
        "return_63_over_volatility_20": None,
        "return_126_over_volatility_20": None,
        "return_252_over_volatility_63": None,
        "sharpe_like_63": None,
        "calmar_like_126": None,
        "volume_confirmed_momentum": None,
        "composite_score": None,
    }
    assert summary["volume"] == {
        "latest_volume": None,
        "avg_volume_20": None,
        "volume_vs_avg_20": None,
        "dollar_volume_20": None,
        "up_volume_ratio_20": None,
    }
    assert summary["availability"]["latest_close"] is False
    assert summary["availability"]["momentum_composite"] is False
    assert summary["availability"]["trend_alignment"] is False
    assert summary["availability"]["high_252"] is False
    assert summary["availability"]["low_252"] is False
    assert summary["availability"]["max_drawdown_60"] is False
    assert summary["availability"]["volatility_20"] is False
    assert summary["availability"]["return_252_ex_skip_21"] is False
    assert summary["availability"]["sma_stack_score"] is False
    assert summary["availability"]["adjusted_slope_90"] is False
    assert summary["availability"]["return_252_over_volatility_63"] is False
    assert summary["availability"]["sharpe_like_63"] is False
    assert summary["availability"]["calmar_like_126"] is False


def test_compute_history_summary_invalid_trend_comparisons_are_unavailable():
    frame = _frame(260)
    frame.loc[60:258, "close"] = float("inf")

    summary = compute_history_summary(frame, symbol="BAD", timestep="day", as_of=None)

    assert summary["trend"]["price_vs_sma_20"] is None
    assert summary["trend"]["price_vs_sma_50"] is None
    assert summary["trend"]["price_vs_sma_200"] is None
    assert summary["scores"]["trend_alignment"] is None
    assert summary["availability"]["trend_alignment"] is False
    json.dumps(summary, allow_nan=False)


def test_compute_history_summary_sanitizes_overflowed_derived_ratios():
    frame = _frame(260)
    frame["close"] = 1e-308
    frame.loc[8:259, "close"] = 1e308
    frame["high"] = 1e308
    frame["low"] = 1e-308

    summary = compute_history_summary(frame, symbol="WIDE", timestep="day", as_of=None)

    json.dumps(summary, allow_nan=False)
    assert summary["momentum"]["return_252"] is None
    assert summary["range"]["distance_to_low_252"] is None
    assert summary["availability"]["return_252"] is False
    assert summary["range"]["distance_to_high_252"] == 0.0


def test_compute_history_summary_empty_frame_does_not_raise():
    summary = compute_history_summary(pd.DataFrame(), symbol="EMPTY", timestep="day", as_of=None)

    assert summary["data_window"]["row_count"] == 0
    assert summary["price"]["latest_close"] is None
    assert summary["availability"]["latest_close"] is False
    assert summary["warnings"]


def test_compute_history_summary_non_daily_volatility_adds_note():
    frame = _frame(30)

    summary = compute_history_summary(frame, symbol="MINS", timestep="minute", as_of=None)

    assert summary["risk"]["volatility_20"] is not None
    assert any("daily" in note.lower() for note in summary["notes"])


def test_build_universe_history_summary_flattens_rows_and_rankings():
    qqq = compute_history_summary(_frame(260), symbol="QQQ", timestep="day", as_of="2024-09-05")
    spy = compute_history_summary(_frame(260), symbol="SPY", timestep="day", as_of="2024-09-05")
    spy["momentum"]["return_21"] = qqq["momentum"]["return_21"] + 0.10
    spy["momentum"]["return_63"] = qqq["momentum"]["return_63"] + 0.20
    spy["momentum"]["return_126"] = qqq["momentum"]["return_126"] + 0.30
    spy["scores"]["momentum_composite"] = qqq["scores"]["momentum_composite"] + 0.40
    spy["scores"]["trend_alignment"] = qqq["scores"]["trend_alignment"] - 1

    summary = build_universe_history_summary(
        {"QQQ": qqq, "SPY": spy},
        symbols=["QQQ", "SPY"],
        timestep="day",
        length=260,
        as_of="2024-09-05",
        loaded_tables={"QQQ": "history_QQQ", "SPY": "history_SPY"},
        warnings=["kept"],
    )

    assert summary["schema_version"] == "1.0"
    assert summary["symbols"] == ["QQQ", "SPY"]
    assert summary["timestep"] == "day"
    assert summary["length"] == 260
    assert summary["as_of"] == "2024-09-05"
    assert summary["loaded_tables"] == {"QQQ": "history_QQQ", "SPY": "history_SPY"}
    assert summary["warnings"] == ["kept"]

    rows = {row["symbol"]: row for row in summary["universe_summary"]}
    assert rows["QQQ"]["latest_close"] == pytest.approx(qqq["price"]["latest_close"])
    assert rows["QQQ"]["evidence_groups"]
    assert rows["QQQ"]["ranking_count"] > 0
    assert "return_21" not in rows["QQQ"]
    assert rows["QQQ"]["composite_score"] == pytest.approx(qqq["scores"]["composite_score"])
    assert rows["SPY"]["latest_close"] == pytest.approx(spy["price"]["latest_close"])
    assert rows["SPY"]["evidence_groups"]
    assert rows["SPY"]["ranking_count"] > 0
    assert summary["rankings"]["by_return_21"] == ["SPY", "QQQ"]
    assert summary["rankings"]["by_return_63"] == ["SPY", "QQQ"]
    assert summary["rankings"]["by_return_126"] == ["SPY", "QQQ"]
    assert summary["rankings"]["by_momentum_composite"] == ["SPY", "QQQ"]
    assert set(summary["rankings"]["by_composite_score"]) == {"QQQ", "SPY"}
    assert summary["rankings"]["by_trend_alignment"] == ["QQQ", "SPY"]
    assert summary["ranking_details"]["by_return_21"][0]["value"] == pytest.approx(
        spy["momentum"]["return_21"],
        abs=1e-6,
    )


def test_build_universe_history_summary_defaults_to_legacy_evidence_profile():
    qqq = compute_history_summary(_frame(260), symbol="QQQ", timestep="day", as_of=None)
    summary = build_universe_history_summary(
        {"QQQ": qqq},
        symbols=["QQQ"],
        timestep="day",
        length=260,
        as_of=None,
        loaded_tables=None,
        warnings=None,
    )

    assert summary["evidence_profile"] == "legacy"
    assert "by_composite_score" in summary["rankings"]
    assert summary["candidate_summary"][0]["composite_score"] == pytest.approx(
        qqq["scores"]["composite_score"],
        abs=1e-6,
    )


def test_build_universe_history_summary_momentum_stage_shape_excludes_legacy_composites():
    aaa_frame = _stage_frame(daily_return=0.003, up_volume=2_000.0, down_volume=800.0)
    bbb_frame = _stage_frame(daily_return=0.002, up_volume=1_800.0, down_volume=900.0)
    qqq = compute_history_summary(
        _stage_frame(daily_return=0.001, up_volume=1_500.0, down_volume=1_000.0),
        symbol="QQQ",
        timestep="day",
        as_of=None,
    )
    spy = compute_history_summary(
        _stage_frame(daily_return=0.0008, up_volume=1_500.0, down_volume=1_000.0),
        symbol="SPY",
        timestep="day",
        as_of=None,
    )
    history_summaries = {
        "AAA": compute_history_summary(aaa_frame, symbol="AAA", timestep="day", as_of=None),
        "BBB": compute_history_summary(bbb_frame, symbol="BBB", timestep="day", as_of=None),
        "QQQ": qqq,
        "SPY": spy,
    }

    summary = build_universe_history_summary(
        history_summaries,
        symbols=["AAA", "BBB"],
        timestep="day",
        length=320,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        top_n=2,
        candidate_summary_limit=2,
        evidence_profile="momentum_stage",
        history_frames={"AAA": aaa_frame, "BBB": bbb_frame},
        benchmark_summaries={"QQQ": qqq, "SPY": spy},
    )

    assert summary["schema_version"] == "1.0"
    assert summary["evidence_profile"] == "momentum_stage"
    assert summary["rank_groups"] == {
        "freshness": ["by_rank_delta_4w"],
        "smoothness": ["by_positive_day_ratio_3m", "by_low_max_day_return_share_3m"],
        "near_high": ["by_near_252d_high"],
        "volume_confirmation": ["by_up_down_volume_ratio_60d"],
        "relative_strength": ["by_excess_return_vs_qqq_6m", "by_excess_return_vs_spy_6m"],
    }
    assert set(summary) >= {
        "schema_version",
        "evidence_profile",
        "rank_groups",
        "rankings",
        "ranking_details",
        "candidate_summary",
        "benchmark_context",
    }
    assert summary["benchmark_context"]["QQQ"]["return_126"] == pytest.approx(
        qqq["momentum"]["return_126"],
        abs=1e-6,
    )
    assert summary["reference_fields"] == [
        "atr_extension_20d",
        "extension_ma50_pct",
        "recent_vs_intermediate_momentum",
        "top_decile_age_weeks",
    ]
    row = summary["candidate_summary"][0]
    assert set(row) == {
        "symbol",
        "latest_close",
        "stage_evidence_groups",
        "stage_ranking_count",
        "stage_best_rank",
        "stage_best_rank_by_group",
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
        "stage_warning_flags",
        "volatility_20",
    }
    payload = json.dumps(summary, sort_keys=True)
    assert "by_composite_score" not in payload
    assert "by_momentum_composite" not in payload
    assert "momentum_composite" not in payload
    assert "composite_score" not in payload
    assert [row["symbol"] for row in summary["candidate_summary"]] == ["AAA", "BBB"]


def test_build_universe_history_summary_normalizes_momentum_stage_profile_name():
    qqq = compute_history_summary(_stage_frame(daily_return=0.001), symbol="QQQ", timestep="day", as_of=None)
    summary = build_universe_history_summary(
        {"QQQ": qqq},
        symbols=["QQQ"],
        timestep="day",
        length=320,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        evidence_profile=" Momentum_Stage ",
        history_frames={"QQQ": _stage_frame(daily_return=0.001)},
        benchmark_summaries={"QQQ": qqq, "SPY": qqq},
    )

    assert summary["evidence_profile"] == "momentum_stage"


def test_duckdb_history_tables_summary_rejects_unsupported_profile_before_loading():
    from datetime import datetime

    from lumibot.components.agents.duckdb_tools import DuckDBQueryLayer

    class FakeStrategy:
        def __init__(self):
            self.load_calls = []

        def get_datetime(self):
            return datetime(2024, 12, 31)

        def get_historical_prices(self, *args, **kwargs):
            self.load_calls.append((args, kwargs))
            return None

    strategy = FakeStrategy()
    layer = DuckDBQueryLayer(strategy)

    with pytest.raises(ValueError, match="Unsupported evidence_profile"):
        layer.load_history_tables_summary(
            symbols=["AAA"],
            length=320,
            evidence_profile="breakout_only",
        )

    assert strategy.load_calls == []


def test_duckdb_history_tables_summary_rejects_empty_benchmark_symbol():
    from datetime import datetime
    from types import SimpleNamespace

    from lumibot.components.agents.duckdb_tools import DuckDBQueryLayer

    class FakeStrategy:
        def get_datetime(self):
            return datetime(2024, 12, 31)

        def get_historical_prices(self, asset, **kwargs):
            return SimpleNamespace(pandas_df=_stage_frame())

    layer = DuckDBQueryLayer(FakeStrategy())

    with pytest.raises(ValueError, match="benchmark_symbols"):
        layer.load_history_tables_summary(
            symbols=["AAA"],
            length=320,
            evidence_profile="momentum_stage",
            benchmark_symbols=[""],
        )


def test_duckdb_history_tables_summary_momentum_stage_loads_benchmarks_separately():
    from datetime import datetime
    from types import SimpleNamespace

    from lumibot.components.agents.duckdb_tools import DuckDBQueryLayer

    class FakeStrategy:
        def __init__(self):
            self.frames = {
                "AAA": _stage_frame(daily_return=0.003, down_every=8),
                "BBB": _stage_frame(daily_return=0.002, down_every=7),
                "QQQ": _stage_frame(daily_return=0.0015, down_every=6),
                "SPY": _stage_frame(daily_return=0.001, down_every=5),
            }

        def get_datetime(self):
            return datetime(2024, 12, 31)

        def get_historical_prices(self, asset, **kwargs):
            return SimpleNamespace(pandas_df=self.frames[asset.symbol].copy())

    layer = DuckDBQueryLayer(FakeStrategy())

    summary = layer.load_history_tables_summary(
        symbols=["AAA", "BBB"],
        length=320,
        evidence_profile="momentum_stage",
        benchmark_symbols=["QQQ", "SPY"],
        top_n=2,
        candidate_summary_limit=2,
    )

    assert summary["evidence_profile"] == "momentum_stage"
    assert set(summary["benchmark_loaded_tables"]) == {"QQQ", "SPY"}
    assert set(summary["loaded_tables"]) == {"AAA", "BBB"}
    assert not {"QQQ", "SPY"} & set(summary["loaded_tables"])

    ranked_symbols = {
        symbol
        for ranking in summary["rankings"].values()
        for symbol in ranking
    }
    assert not {"QQQ", "SPY"} & ranked_symbols
    assert not {"QQQ", "SPY"} & {row["symbol"] for row in summary["candidate_summary"]}
    assert summary["benchmark_context"]["QQQ"]["available"] is True
    assert summary["benchmark_context"]["SPY"]["available"] is True
    assert summary["benchmark_context"]["QQQ"]["return_126"] is not None
    assert summary["benchmark_context"]["SPY"]["return_126"] is not None


def test_build_universe_history_summary_momentum_stage_low_jump_concentration_ranks_first_and_flags_jump():
    smooth_frame = _stage_frame(daily_return=0.002, up_volume=2_000.0, down_volume=800.0)
    jump_frame = _stage_frame(daily_return=0.002, jump_at=300, jump_return=0.50, up_volume=2_000.0, down_volume=800.0)
    qqq = compute_history_summary(_stage_frame(daily_return=0.001), symbol="QQQ", timestep="day", as_of=None)
    spy = compute_history_summary(_stage_frame(daily_return=0.001), symbol="SPY", timestep="day", as_of=None)
    summaries = {
        "SMOOTH": compute_history_summary(smooth_frame, symbol="SMOOTH", timestep="day", as_of=None),
        "JUMP": compute_history_summary(jump_frame, symbol="JUMP", timestep="day", as_of=None),
    }

    summary = build_universe_history_summary(
        summaries,
        symbols=["SMOOTH", "JUMP"],
        timestep="day",
        length=320,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        top_n=2,
        candidate_summary_limit=2,
        evidence_profile="momentum_stage",
        history_frames={"SMOOTH": smooth_frame, "JUMP": jump_frame},
        benchmark_summaries={"QQQ": qqq, "SPY": spy},
    )

    assert summary["rankings"]["by_low_max_day_return_share_3m"] == ["SMOOTH", "JUMP"]
    rows = {row["symbol"]: row for row in summary["candidate_summary"]}
    assert "single_day_jump_concentration" in rows["JUMP"]["stage_warning_flags"]
    assert rows["JUMP"]["max_day_return_share_3m"] >= 0.35


def test_build_universe_history_summary_momentum_stage_asserts_benchmark_excess_values():
    aaa = _stage_rankable_summary("AAA", return_126=0.25)
    qqq = _stage_rankable_summary("QQQ", return_126=0.10)
    spy = _stage_rankable_summary("SPY", return_126=0.05)

    summary = build_universe_history_summary(
        {"AAA": aaa},
        symbols=["AAA"],
        timestep="day",
        length=320,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        top_n=1,
        candidate_summary_limit=1,
        evidence_profile="momentum_stage",
        benchmark_summaries={"QQQ": qqq, "SPY": spy},
    )

    row = summary["candidate_summary"][0]
    assert row["excess_return_vs_qqq_6m"] == pytest.approx(0.15)
    assert row["excess_return_vs_spy_6m"] == pytest.approx(0.20)


def test_momentum_stage_warning_flags_are_empty_for_clean_row():
    assert _momentum_stage_warning_flags(_stage_warning_row()) == []


@pytest.mark.parametrize(
    ("overrides", "expected_flag"),
    [
        ({"top_decile_age_weeks": 12}, "stale_top_decile"),
        ({"extension_ma50_pct": 0.20}, "extreme_ma50_extension"),
        ({"atr_extension_20d": 3.0}, "extreme_atr_extension"),
        ({"recent_vs_intermediate_momentum": 0.15}, "recent_overheat_vs_intermediate"),
        ({"max_day_return_share_3m": 0.35}, "single_day_jump_concentration"),
        (
            {
                "excess_return_vs_qqq_6m": -0.01,
                "excess_return_vs_spy_6m": -0.02,
            },
            "benchmark_lag",
        ),
        ({"up_down_volume_ratio_60d": 0.99}, "thin_or_missing_volume_support"),
        ({"rank_delta_4w": None}, "insufficient_history"),
    ],
)
def test_momentum_stage_warning_flags_cover_threshold_and_missing_cases(overrides, expected_flag):
    flags = _momentum_stage_warning_flags(_stage_warning_row(**overrides))

    assert expected_flag in flags


def test_build_universe_history_summary_momentum_stage_rank_delta_4w_uses_controlled_universe_ranks():
    frames = {
        "AAA": _stage_frame_from_closes([100.0] * 299 + [160.0] * 21),
        "BBB": _stage_frame_from_closes([100.0] * 194 + [130.0] * 126),
        "CCC": _stage_frame_from_closes([100.0] * 173 + [160.0] * 147),
    }
    summaries = {
        symbol: compute_history_summary(frame, symbol=symbol, timestep="day", as_of=None)
        for symbol, frame in frames.items()
    }

    summary = build_universe_history_summary(
        summaries,
        symbols=["AAA", "BBB", "CCC"],
        timestep="day",
        length=320,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        top_n=3,
        candidate_summary_limit=3,
        evidence_profile="momentum_stage",
        history_frames=frames,
    )

    rows = {row["symbol"]: row for row in summary["candidate_summary"]}
    assert summary["rankings"]["by_rank_delta_4w"] == ["AAA", "BBB", "CCC"]
    assert rows["AAA"]["rank_delta_4w"] == 2
    assert rows["BBB"]["rank_delta_4w"] == 0
    assert rows["CCC"]["rank_delta_4w"] == -2


def test_build_universe_history_summary_momentum_stage_top_decile_age_weeks_uses_weekly_samples():
    frames = {"TOP": _stage_frame_from_closes([100.0] * 305 + [150.0] * 15)}
    for index in range(1, 10):
        frames[f"S{index}"] = _stage_frame_from_closes([100.0] * 194 + [110.0] * 126)
    summaries = {
        symbol: compute_history_summary(frame, symbol=symbol, timestep="day", as_of=None)
        for symbol, frame in frames.items()
    }

    summary = build_universe_history_summary(
        summaries,
        symbols=list(frames),
        timestep="day",
        length=320,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        top_n=10,
        candidate_summary_limit=10,
        evidence_profile="momentum_stage",
        history_frames=frames,
    )

    rows = {row["symbol"]: row for row in summary["candidate_summary"]}
    assert rows["TOP"]["top_decile_age_weeks"] == 3


def test_build_universe_history_summary_momentum_stage_top_decile_age_requires_rankable_universe():
    frames = {
        "TOP1": _stage_frame_from_closes([100.0] * 305 + [150.0] * 15),
        "TOP2": _stage_frame_from_closes([100.0] * 305 + [140.0] * 15),
    }
    for index in range(1, 19):
        frames[f"SHORT{index:02d}"] = _stage_frame_from_closes([100.0] * 100)
    summaries = {
        symbol: compute_history_summary(frame, symbol=symbol, timestep="day", as_of=None)
        for symbol, frame in frames.items()
    }

    summary = build_universe_history_summary(
        summaries,
        symbols=list(frames),
        timestep="day",
        length=320,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        top_n=20,
        candidate_summary_limit=20,
        evidence_profile="momentum_stage",
        history_frames=frames,
    )

    rows = {row["symbol"]: row for row in summary["candidate_summary"]}
    assert rows["TOP1"]["top_decile_age_weeks"] is None
    assert rows["TOP2"]["top_decile_age_weeks"] is None


def test_build_universe_history_summary_momentum_stage_top_decile_age_reuses_weekly_rank_maps(monkeypatch):
    frames = {
        "TOP1": _stage_frame_from_closes([100.0] * 305 + [150.0] * 15),
        "TOP2": _stage_frame_from_closes([100.0] * 310 + [140.0] * 10),
    }
    for index in range(1, 19):
        frames[f"S{index:02d}"] = _stage_frame_from_closes([100.0] * 194 + [110.0] * 126)
    summaries = {
        symbol: compute_history_summary(frame, symbol=symbol, timestep="day", as_of=None)
        for symbol, frame in frames.items()
    }
    calls_by_offset: dict[int, int] = {}
    original = history_summary_module._return_126_by_symbol_as_of_offset

    def counted_return_126_by_symbol_as_of_offset(close_series_by_symbol, symbols, *, offset):
        calls_by_offset[offset] = calls_by_offset.get(offset, 0) + 1
        return original(close_series_by_symbol, symbols, offset=offset)

    monkeypatch.setattr(
        history_summary_module,
        "_return_126_by_symbol_as_of_offset",
        counted_return_126_by_symbol_as_of_offset,
    )

    summary = build_universe_history_summary(
        summaries,
        symbols=list(frames),
        timestep="day",
        length=320,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        top_n=20,
        candidate_summary_limit=20,
        evidence_profile="momentum_stage",
        history_frames=frames,
    )

    rows = {row["symbol"]: row for row in summary["candidate_summary"]}
    assert rows["TOP1"]["top_decile_age_weeks"] == 3
    assert rows["TOP2"]["top_decile_age_weeks"] == 2
    assert calls_by_offset
    assert max(calls_by_offset.values()) == 1


def test_build_universe_history_summary_momentum_stage_covers_all_ranking_directions():
    frames = {
        "AAA": _stage_frame_from_closes([100.0] * 299 + [160.0] * 21),
        "BBB": _stage_frame_from_closes([100.0] * 194 + [130.0] * 126),
        "CCC": _stage_frame_from_closes([100.0] * 173 + [160.0] * 147),
    }
    summaries = {
        "AAA": _stage_rankable_summary(
            "AAA",
            return_126=0.40,
            positive_day_ratio_3m=0.90,
            max_day_return_share_3m=0.20,
            distance_to_252d_high_pct=-0.03,
            up_down_volume_ratio_60d=2.0,
        ),
        "BBB": _stage_rankable_summary(
            "BBB",
            return_126=0.30,
            positive_day_ratio_3m=0.80,
            max_day_return_share_3m=0.10,
            distance_to_252d_high_pct=-0.01,
            up_down_volume_ratio_60d=1.0,
        ),
        "CCC": _stage_rankable_summary(
            "CCC",
            return_126=0.20,
            positive_day_ratio_3m=0.70,
            max_day_return_share_3m=0.05,
            distance_to_252d_high_pct=-0.20,
            up_down_volume_ratio_60d=3.0,
        ),
    }

    summary = build_universe_history_summary(
        summaries,
        symbols=["AAA", "BBB", "CCC"],
        timestep="day",
        length=320,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        top_n=3,
        candidate_summary_limit=3,
        evidence_profile="momentum_stage",
        history_frames=frames,
        benchmark_summaries={
            "QQQ": _stage_rankable_summary("QQQ", return_126=0.10),
            "SPY": _stage_rankable_summary("SPY", return_126=0.05),
        },
    )

    assert summary["rankings"]["by_rank_delta_4w"] == ["AAA", "BBB", "CCC"]
    assert summary["rankings"]["by_positive_day_ratio_3m"] == ["AAA", "BBB", "CCC"]
    assert summary["rankings"]["by_low_max_day_return_share_3m"] == ["CCC", "BBB", "AAA"]
    assert summary["rankings"]["by_near_252d_high"] == ["BBB", "AAA", "CCC"]
    assert summary["rankings"]["by_up_down_volume_ratio_60d"] == ["CCC", "AAA", "BBB"]
    assert summary["rankings"]["by_excess_return_vs_qqq_6m"] == ["AAA", "BBB", "CCC"]
    assert summary["rankings"]["by_excess_return_vs_spy_6m"] == ["AAA", "BBB", "CCC"]


def test_build_universe_history_summary_momentum_stage_candidate_selection_favors_overlap_when_truncated():
    symbols = ["PRI1", "PRI2", "OVR", "VOL", "HIGH"]
    summaries = {
        "PRI1": _stage_rankable_summary(
            "PRI1",
            return_126=0.50,
            positive_day_ratio_3m=0.10,
            max_day_return_share_3m=0.60,
            distance_to_252d_high_pct=-0.50,
            up_down_volume_ratio_60d=0.5,
        ),
        "PRI2": _stage_rankable_summary(
            "PRI2",
            return_126=0.40,
            positive_day_ratio_3m=0.20,
            max_day_return_share_3m=0.50,
            distance_to_252d_high_pct=-0.40,
            up_down_volume_ratio_60d=0.6,
        ),
        "OVR": _stage_rankable_summary(
            "OVR",
            return_126=0.30,
            positive_day_ratio_3m=0.90,
            max_day_return_share_3m=0.05,
            distance_to_252d_high_pct=-0.01,
            up_down_volume_ratio_60d=3.0,
        ),
        "VOL": _stage_rankable_summary(
            "VOL",
            return_126=0.20,
            positive_day_ratio_3m=0.80,
            max_day_return_share_3m=0.10,
            distance_to_252d_high_pct=-0.02,
            up_down_volume_ratio_60d=2.0,
        ),
        "HIGH": _stage_rankable_summary(
            "HIGH",
            return_126=0.10,
            positive_day_ratio_3m=0.70,
            max_day_return_share_3m=0.20,
            distance_to_252d_high_pct=-0.03,
            up_down_volume_ratio_60d=1.5,
        ),
    }

    summary = build_universe_history_summary(
        summaries,
        symbols=symbols,
        timestep="day",
        length=320,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        top_n=3,
        candidate_summary_limit=2,
        evidence_profile="momentum_stage",
        benchmark_summaries={
            "QQQ": _stage_rankable_summary("QQQ", return_126=0.0),
            "SPY": _stage_rankable_summary("SPY", return_126=0.0),
        },
    )

    selected = [row["symbol"] for row in summary["candidate_summary"]]
    assert selected == ["OVR", "VOL"]
    assert "PRI2" not in selected


def test_build_universe_history_summary_returns_five_rank_groups_and_details():
    symbols = ["AAA", "BBB", "CCC"]
    history_summaries = {
        "AAA": _rankable_summary(
            "AAA",
            composite_score=0.1,
            momentum_composite=0.3,
            return_21=0.2,
            return_63=0.4,
            return_126=0.5,
            trend_alignment=3,
            return_252=0.8,
            return_252_ex_skip_21=0.7,
            sma_stack_score=4,
            adjusted_slope_90=0.9,
            linear_regression_r2_90=0.95,
            return_252_over_volatility_63=8.0,
            sharpe_like_63=1.5,
            calmar_like_126=4.0,
            distance_to_high_252=-0.01,
            distance_to_high_63=-0.02,
            breakout_20_high_score=0.03,
            breakout_63_high_score=0.01,
            drawdown_from_high_60=-0.02,
            volume_vs_avg_20=0.4,
            dollar_volume_20=1000000,
            up_volume_ratio_20=0.7,
            volume_confirmed_momentum=0.55,
        ),
        "BBB": _rankable_summary(
            "BBB",
            composite_score=0.2,
            momentum_composite=0.2,
            return_21=0.1,
            return_63=0.2,
            return_126=0.3,
            trend_alignment=2,
            return_252=0.4,
            return_252_ex_skip_21=0.35,
            sma_stack_score=3,
            adjusted_slope_90=0.4,
            linear_regression_r2_90=0.8,
            return_252_over_volatility_63=5.0,
            sharpe_like_63=1.0,
            calmar_like_126=2.0,
            distance_to_high_252=-0.10,
            distance_to_high_63=-0.08,
            breakout_20_high_score=-0.01,
            breakout_63_high_score=-0.02,
            drawdown_from_high_60=-0.08,
            volume_vs_avg_20=0.1,
            dollar_volume_20=500000,
            up_volume_ratio_20=0.6,
            volume_confirmed_momentum=0.3,
        ),
        "CCC": _rankable_summary(
            "CCC",
            composite_score=0.3,
            momentum_composite=0.1,
            return_21=0.05,
            return_63=0.1,
            return_126=0.2,
            trend_alignment=1,
            return_252=0.2,
            return_252_ex_skip_21=0.1,
            sma_stack_score=1,
            adjusted_slope_90=0.1,
            linear_regression_r2_90=0.2,
            return_252_over_volatility_63=2.0,
            sharpe_like_63=0.2,
            calmar_like_126=0.5,
            distance_to_high_252=-0.30,
            distance_to_high_63=-0.20,
            breakout_20_high_score=-0.05,
            breakout_63_high_score=-0.07,
            drawdown_from_high_60=-0.20,
            volume_vs_avg_20=-0.1,
            dollar_volume_20=100000,
            up_volume_ratio_20=0.4,
            volume_confirmed_momentum=0.1,
        ),
    }

    summary = build_universe_history_summary(
        history_summaries,
        symbols=symbols,
        timestep="day",
        length=252,
        as_of="2024-09-05",
        loaded_tables={"AAA": "aaa_hist"},
        warnings=[],
        top_n=2,
        candidate_summary_limit=2,
    )

    assert summary["rank_groups"] == {
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
    assert summary["coverage"] == {
        "requested_count": 3,
        "loaded_count": 3,
        "failed_count": 0,
        "top_n": 2,
        "candidate_summary_limit": 2,
        "ranking_count": len(summary["rankings"]),
    }
    assert summary["rankings"]["by_return_252"] == ["AAA", "BBB"]
    assert summary["rankings"]["by_momentum_composite"] == ["AAA", "BBB"]
    assert summary["rankings"]["by_composite_score"] == ["CCC", "BBB"]
    assert summary["ranking_details"]["by_return_252"][0] == {"rank": 1, "symbol": "AAA", "value": 0.8}
    assert summary["rankings"]["by_near_252_high"] == ["AAA", "BBB"]
    assert len(summary["candidate_summary"]) == 2
    assert summary["candidate_summary"][0]["symbol"] == "AAA"
    assert summary["universe_summary"] == summary["candidate_summary"]
    assert summary["loaded_tables"] == {"AAA": "aaa_hist"}


def test_build_universe_history_summary_honors_top_n_and_candidate_summary_limit():
    symbols = [f"S{i:02d}" for i in range(1, 8)]
    history_summaries = {
        symbol: _rankable_summary(
            symbol,
            composite_score=index,
            momentum_composite=index,
            return_21=index,
            return_63=index,
            return_126=index,
            trend_alignment=index,
            return_252=index,
            return_252_ex_skip_21=index,
            adjusted_slope_90=index,
        )
        for index, symbol in enumerate(symbols, start=1)
    }

    summary = build_universe_history_summary(
        history_summaries,
        symbols=symbols,
        timestep="day",
        length=252,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        top_n=3,
        candidate_summary_limit=4,
    )

    assert summary["ranking_limit"] == 3
    assert summary["universe_summary_limit"] == 4
    assert summary["candidate_summary_limit"] == 4
    assert all(len(ranking) <= 3 for ranking in summary["rankings"].values())
    assert all(len(details) <= 3 for details in summary["ranking_details"].values())
    assert len(summary["candidate_summary"]) <= 4


def test_build_universe_history_summary_pruned_excerpt_includes_candidate_summary():
    symbols = [f"S{i:02d}" for i in range(1, 36)]
    history_summaries = {
        symbol: _rankable_summary(
            symbol,
            composite_score=index,
            momentum_composite=index,
            return_21=index / 100,
            return_63=index / 90,
            return_126=index / 80,
            trend_alignment=index % 4,
            return_252=index / 70,
            return_252_ex_skip_21=index / 75,
            sma_stack_score=index % 5,
            adjusted_slope_90=index / 60,
            linear_regression_r2_90=0.5 + index / 100,
            return_252_over_volatility_63=index / 5,
            sharpe_like_63=index / 10,
            calmar_like_126=index / 8,
            distance_to_high_252=-index / 1000,
            distance_to_high_63=-index / 1200,
            breakout_20_high_score=index / 1000,
            breakout_63_high_score=index / 1100,
            drawdown_from_high_60=-index / 900,
            volume_vs_avg_20=index / 50,
            dollar_volume_20=index * 1_000_000,
            up_volume_ratio_20=0.4 + index / 1000,
            volume_confirmed_momentum=index / 100,
        )
        for index, symbol in enumerate(symbols, start=1)
    }

    summary = build_universe_history_summary(
        history_summaries,
        symbols=symbols,
        timestep="day",
        length=252,
        as_of=None,
        loaded_tables={symbol: f"hist_{symbol}" for symbol in symbols},
        warnings=None,
        top_n=10,
        candidate_summary_limit=25,
    )

    pruned = _prune_tool_response_for_context_window(
        summary,
        tool_name="market_load_history_tables_summary",
        max_chars=6_000,
    )

    assert pruned is not None
    excerpt = pruned["excerpt"]
    assert len(excerpt) <= 6_000
    parsed_excerpt = json.loads(excerpt)
    assert list(parsed_excerpt)[:5] == [
        "schema_version",
        "coverage",
        "rank_groups",
        "ranking_limit",
        "candidate_summary_limit",
    ]
    for essential_key in ("coverage", "rank_groups", "rankings", "ranking_details", "candidate_summary"):
        assert essential_key in parsed_excerpt
    assert parsed_excerpt["candidate_summary"]
    assert parsed_excerpt["coverage"]["loaded_count"] == len(symbols)
    assert parsed_excerpt["rank_groups"] == summary["rank_groups"]
    assert parsed_excerpt["rankings"]
    assert parsed_excerpt["ranking_details"]


def test_market_history_summary_pruning_preserves_error_envelope_fields():
    response = {
        "tool_error": True,
        "error": "DuckDB query failed",
        "error_type": "RuntimeError",
        "arguments": {
            "symbols": [f"S{i:03d}" for i in range(250)],
            "sql": "SELECT * FROM missing_table " * 250,
        },
    }

    pruned = _prune_tool_response_for_context_window(
        response,
        tool_name="market_load_history_tables_summary",
        max_chars=1_000,
    )

    assert pruned is not None
    excerpt = pruned["excerpt"]
    assert "tool_error" in excerpt
    assert "DuckDB query failed" in excerpt
    assert "coverage" not in excerpt
    assert "candidate_summary" not in excerpt


def test_build_universe_history_summary_candidate_summary_rows_are_compact():
    symbols = [f"S{i:02d}" for i in range(1, 31)]
    history_summaries = {
        symbol: _rankable_summary(
            symbol,
            composite_score=index,
            momentum_composite=index,
            return_21=index,
            return_63=index,
            return_126=index,
            trend_alignment=index,
            return_252=index,
            return_252_ex_skip_21=index,
            sma_stack_score=index,
            adjusted_slope_90=index,
            linear_regression_r2_90=index,
            return_252_over_volatility_63=index,
            sharpe_like_63=index,
            calmar_like_126=index,
            distance_to_high_252=index,
            distance_to_high_63=index,
            breakout_20_high_score=index,
            breakout_63_high_score=index,
            drawdown_from_high_60=index,
            volume_vs_avg_20=index,
            dollar_volume_20=index,
            up_volume_ratio_20=index,
            volume_confirmed_momentum=index,
        )
        for index, symbol in enumerate(symbols, start=1)
    }

    summary = build_universe_history_summary(
        history_summaries,
        symbols=symbols,
        timestep="day",
        length=252,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        top_n=10,
        candidate_summary_limit=25,
    )

    first_row = summary["candidate_summary"][0]
    assert set(first_row) == {
        "symbol",
        "latest_close",
        "return_5",
        "momentum_composite",
        "composite_score",
        "volume_vs_avg_20",
        "drawdown_from_high_60",
        "volatility_20",
        "evidence_groups",
        "ranking_count",
        "best_rank",
        "best_rank_by_group",
    }
    assert first_row["volatility_20"] is not None
    assert "return_21" not in first_row
    assert len(json.dumps(summary["candidate_summary"], sort_keys=True)) < 6_500


def test_build_universe_history_summary_prioritizes_repeated_and_priority_ranking_candidates():
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    summary = build_universe_history_summary(
        {
            "AAA": _rankable_summary(
                "AAA",
                composite_score=1.0,
                momentum_composite=0.1,
                return_21=0.1,
                return_63=0.1,
                return_126=0.1,
                trend_alignment=1,
                adjusted_slope_90=0.1,
            ),
            "BBB": _rankable_summary(
                "BBB",
                composite_score=0.1,
                momentum_composite=0.9,
                return_21=0.9,
                return_63=0.9,
                return_126=0.9,
                trend_alignment=3,
                adjusted_slope_90=0.9,
            ),
            "CCC": _rankable_summary(
                "CCC",
                composite_score=0.2,
                momentum_composite=0.8,
                return_21=0.8,
                return_63=0.8,
                return_126=0.8,
                trend_alignment=2,
                adjusted_slope_90=0.8,
            ),
            "DDD": _rankable_summary(
                "DDD",
                composite_score=0.3,
                momentum_composite=0.7,
                return_21=0.7,
                return_63=0.7,
                return_126=0.7,
                trend_alignment=1,
                adjusted_slope_90=0.7,
            ),
            "EEE": _rankable_summary(
                "EEE",
                composite_score=0.4,
                momentum_composite=0.6,
                return_21=0.6,
                return_63=0.6,
                return_126=0.6,
                trend_alignment=1,
                adjusted_slope_90=0.6,
            ),
        },
        symbols=symbols,
        timestep="day",
        length=252,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        top_n=2,
        candidate_summary_limit=2,
    )

    assert [row["symbol"] for row in summary["candidate_summary"]] == ["BBB", "CCC"]


def test_build_universe_history_summary_missing_metric_excludes_only_that_ranking():
    summary = build_universe_history_summary(
        {
            "AAA": _rankable_summary(
                "AAA",
                composite_score=1,
                momentum_composite=1,
                return_21=1,
                return_63=1,
                return_126=1,
                trend_alignment=3,
                return_252=None,
                adjusted_slope_90=1,
            )
        },
        symbols=["AAA"],
        timestep="day",
        length=252,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        top_n=10,
        candidate_summary_limit=25,
    )

    assert summary["rankings"]["by_return_252"] == []
    assert summary["rankings"]["by_adjusted_slope_90"] == ["AAA"]
    assert summary["candidate_summary"][0]["symbol"] == "AAA"


def test_build_universe_history_summary_result_is_json_safe():
    summary = build_universe_history_summary(
        {
            "BAD": _rankable_summary(
                "BAD",
                composite_score=float("nan"),
                momentum_composite=float("inf"),
                return_21=float("-inf"),
                return_63=0.1,
                return_126=0.2,
                trend_alignment=1,
                return_252=0.3,
                adjusted_slope_90=0.4,
            )
        },
        symbols=["BAD", "MISS"],
        timestep="day",
        length=252,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        top_n=10,
        candidate_summary_limit=25,
    )

    json.dumps(summary, allow_nan=False)
    assert summary["coverage"]["failed_count"] == 1


def test_build_universe_history_summary_tie_order_is_independent_of_input_symbol_order():
    symbols = ["AAA", "BBB", "CCC"]
    history_summaries = {
        symbol: _rankable_summary(
            symbol,
            composite_score=1,
            momentum_composite=1,
            return_21=1,
            return_63=1,
            return_126=1,
            trend_alignment=1,
            return_252=1,
            return_252_ex_skip_21=1,
            sma_stack_score=1,
            adjusted_slope_90=1,
            linear_regression_r2_90=1,
            return_252_over_volatility_63=1,
            sharpe_like_63=1,
            calmar_like_126=1,
            distance_to_high_252=1,
            distance_to_high_63=1,
            breakout_20_high_score=1,
            breakout_63_high_score=1,
            drawdown_from_high_60=1,
            volume_vs_avg_20=1,
            dollar_volume_20=1,
            up_volume_ratio_20=1,
            volume_confirmed_momentum=1,
        )
        for symbol in symbols
    }

    forward_summary = build_universe_history_summary(
        history_summaries,
        symbols=symbols,
        timestep="day",
        length=252,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        top_n=3,
        candidate_summary_limit=3,
    )
    reversed_summary = build_universe_history_summary(
        history_summaries,
        symbols=list(reversed(symbols)),
        timestep="day",
        length=252,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        top_n=3,
        candidate_summary_limit=3,
    )

    assert forward_summary["rankings"]["by_return_21"] == symbols
    assert reversed_summary["rankings"]["by_return_21"] == symbols
    assert [row["symbol"] for row in forward_summary["candidate_summary"]] == symbols
    assert [row["symbol"] for row in reversed_summary["candidate_summary"]] == symbols


def test_build_universe_history_summary_limits_rankings_and_detail_rows():
    symbols = [f"S{i:02d}" for i in range(1, 21)]
    history_summaries = {}
    for index, symbol in enumerate(symbols):
        history_summaries[symbol] = _rankable_summary(
            symbol,
            composite_score=20 - index,
            momentum_composite=index + 1,
            return_21=(index % 10) + 1,
            return_63=(20 - index) / 2,
            return_126=index / 3,
            trend_alignment=index % 4,
        )

    summary = build_universe_history_summary(
        history_summaries,
        symbols=symbols,
        timestep="day",
        length=260,
        as_of=None,
        loaded_tables=None,
        warnings=None,
    )

    assert summary["ranking_limit"] == 10
    assert summary["universe_summary_limit"] == 25
    assert summary["symbols"] == symbols
    assert summary["rankings"]["by_composite_score"] == symbols[:10]
    assert summary["rankings"]["by_momentum_composite"] == list(reversed(symbols[-10:]))
    assert all(len(ranking) <= 10 for ranking in summary["rankings"].values())

    detail_symbols = [row["symbol"] for row in summary["universe_summary"]]
    assert len(detail_symbols) == 20
    assert set(detail_symbols) == set(symbols)
    assert summary["universe_summary_selection"] == {
        "mode": "top_rank_union",
        "candidate_count_before_limit": 20,
        "included_symbols": detail_symbols,
        "priority": [
            "by_momentum_composite",
            "by_adjusted_slope_90",
            "by_return_252_over_volatility_63",
            "by_composite_score",
            "multi_ranking_overlap",
        ],
    }


def test_build_universe_history_summary_skips_unavailable_ranking_values():
    qqq = compute_history_summary(_frame(260), symbol="QQQ", timestep="day", as_of=None)
    short = compute_history_summary(_frame(10), symbol="SHORT", timestep="day", as_of=None)
    bad = compute_history_summary(_frame(260), symbol="BAD", timestep="day", as_of=None)
    bad["momentum"]["return_21"] = True
    bad["scores"]["trend_alignment"] = "high"
    missing_symbol = compute_history_summary(_frame(260), symbol="", timestep="day", as_of=None)

    summary = build_universe_history_summary(
        {"QQQ": qqq, "SHORT": short, "BAD": bad, "MISSING": missing_symbol},
        symbols=["QQQ", "SHORT", "BAD", "MISSING"],
        timestep="day",
        length=260,
        as_of=None,
        loaded_tables=None,
        warnings=None,
    )

    assert summary["rankings"]["by_return_21"] == ["QQQ"]
    assert summary["rankings"]["by_return_63"] == ["BAD", "QQQ"]
    assert summary["rankings"]["by_return_126"] == ["BAD", "QQQ"]
    assert summary["rankings"]["by_momentum_composite"] == ["BAD", "QQQ"]
    assert summary["rankings"]["by_trend_alignment"] == ["QQQ"]


def test_build_universe_history_summary_sanitizes_non_finite_row_values():
    bad = {
        "symbol": "BAD",
        "price": {"latest_close": float("inf")},
        "momentum": {
            "return_21": float("nan"),
            "return_63": float("-inf"),
            "return_126": 0.12,
            "return_252": float("inf"),
        },
        "scores": {
            "momentum_composite": float("nan"),
            "trend_alignment": float("inf"),
        },
        "trend": {
            "sma_20": float("inf"),
            "sma_50": float("nan"),
            "sma_200": 200.0,
            "price_vs_sma_20": float("-inf"),
            "price_vs_sma_50": 0.03,
            "price_vs_sma_200": float("nan"),
        },
        "risk": {
            "max_drawdown_60": float("-inf"),
            "volatility_20": float("nan"),
        },
        "range": {
            "distance_to_high_252": float("inf"),
            "distance_to_low_252": float("nan"),
        },
    }

    summary = build_universe_history_summary(
        {"BAD": bad},
        symbols=["BAD"],
        timestep="day",
        length=260,
        as_of=None,
        loaded_tables=None,
        warnings=None,
    )

    row = summary["universe_summary"][0]
    assert row["symbol"] == "BAD"
    assert row["latest_close"] is None
    assert row["evidence_groups"] == ["momentum"]
    assert row["ranking_count"] == 1
    assert row["best_rank"] == 1
    assert summary["rankings"]["by_return_21"] == []
    assert summary["rankings"]["by_return_63"] == []
    assert summary["rankings"]["by_return_126"] == ["BAD"]
    assert summary["rankings"]["by_momentum_composite"] == []
    assert summary["rankings"]["by_composite_score"] == []
    assert summary["rankings"]["by_trend_alignment"] == []
    assert summary["rankings"]["by_adjusted_slope_90"] == []
    json.dumps(summary, allow_nan=False)


def test_compute_history_summary_excludes_non_finite_trend_alignment_from_rankings():
    frame = pd.DataFrame(
        {
            "Date": pd.date_range("2024-01-01", periods=260, freq="D"),
            "close": [float("inf")] * 260,
        }
    )
    history_summary = compute_history_summary(frame, symbol="BAD", timestep="day", as_of=None)

    summary = build_universe_history_summary(
        {"BAD": history_summary},
        symbols=["BAD"],
        timestep="day",
        length=260,
        as_of=None,
        loaded_tables=None,
        warnings=None,
    )

    assert history_summary["scores"]["trend_alignment"] is None
    assert history_summary["availability"]["trend_alignment"] is False
    assert "trend_alignment" not in summary["universe_summary"][0]
    assert summary["rankings"]["by_trend_alignment"] == []
    json.dumps(summary, allow_nan=False)
