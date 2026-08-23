from __future__ import annotations

import json

import pandas as pd
import pytest

from lumibot.components.agents.builtins import BuiltinTools
from lumibot.components.agents.history_summary import build_universe_history_summary, compute_history_summary
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
    assert "five evidence groups" in multi
    assert "momentum" in multi
    assert "trend quality" in multi
    assert "risk-adjusted momentum" in multi
    assert "breakout / near-high" in multi
    assert "volume confirmation" in multi
    assert "top_n" in multi
    assert "candidate_summary_limit" in multi
    assert "ranking_details" in multi
    assert "summary-first" in multi
    assert "duckdb only as targeted follow-up" in multi
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
