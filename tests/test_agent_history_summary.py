from __future__ import annotations

import json

import pandas as pd
import pytest

from lumibot.components.agents.builtins import BuiltinTools
from lumibot.components.agents.history_summary import build_universe_history_summary, compute_history_summary


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
) -> dict:
    return {
        "symbol": symbol,
        "price": {"latest_close": 100.0},
        "momentum": {
            "return_5": 0.0,
            "return_21": return_21,
            "return_63": return_63,
            "return_126": return_126,
        },
        "scores": {
            "composite_score": composite_score,
            "momentum_composite": momentum_composite,
            "trend_alignment": trend_alignment,
        },
        "volume": {"volume_vs_avg_20": 0.0},
        "risk": {"volatility_20": 0.01},
        "range": {"drawdown_from_high_60": -0.01},
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
    assert "targeted follow-up" in duckdb
    assert "computed summaries or rankings are insufficient" in duckdb
    assert "load a table first with market_load_history_table, then analyze it here" not in duckdb
    assert "top 10" in multi
    assert "top-ranked candidate subset" in multi


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
    assert rows["QQQ"]["return_21"] == pytest.approx(qqq["momentum"]["return_21"], abs=1e-6)
    assert rows["QQQ"]["return_5"] == pytest.approx(qqq["momentum"]["return_5"], abs=1e-6)
    assert rows["QQQ"]["composite_score"] == pytest.approx(qqq["scores"]["composite_score"], abs=1e-6)
    assert rows["QQQ"]["volume_vs_avg_20"] == pytest.approx(qqq["volume"]["volume_vs_avg_20"], abs=1e-6)
    assert rows["QQQ"]["drawdown_from_high_60"] == pytest.approx(qqq["range"]["drawdown_from_high_60"], abs=1e-6)
    assert "sma_20" not in rows["QQQ"]
    assert rows["SPY"]["latest_close"] == pytest.approx(spy["price"]["latest_close"])
    assert rows["SPY"]["return_63"] == pytest.approx(spy["momentum"]["return_63"], abs=1e-6)
    assert rows["SPY"]["momentum_composite"] == pytest.approx(spy["scores"]["momentum_composite"], abs=1e-6)
    assert rows["SPY"]["trend_alignment"] == pytest.approx(spy["scores"]["trend_alignment"])
    assert summary["rankings"]["by_return_21"] == ["SPY", "QQQ"]
    assert summary["rankings"]["by_return_63"] == ["SPY", "QQQ"]
    assert summary["rankings"]["by_return_126"] == ["SPY", "QQQ"]
    assert summary["rankings"]["by_momentum_composite"] == ["SPY", "QQQ"]
    assert set(summary["rankings"]["by_composite_score"]) == {"QQQ", "SPY"}
    assert summary["rankings"]["by_trend_alignment"] == ["QQQ", "SPY"]


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
    assert summary["universe_summary_limit"] == 15
    assert summary["symbols"] == symbols
    assert summary["rankings"]["by_composite_score"] == symbols[:10]
    assert summary["rankings"]["by_momentum_composite"] == list(reversed(symbols[-10:]))
    assert all(len(ranking) <= 10 for ranking in summary["rankings"].values())

    detail_symbols = [row["symbol"] for row in summary["universe_summary"]]
    assert len(detail_symbols) == 15
    assert detail_symbols == symbols[:10] + list(reversed(symbols[-5:]))
    assert summary["universe_summary_selection"] == {
        "mode": "top_rank_union",
        "candidate_count_before_limit": 20,
        "included_symbols": detail_symbols,
        "priority": [
            "by_composite_score",
            "by_momentum_composite",
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
    assert summary["rankings"]["by_return_63"] == ["QQQ", "BAD"]
    assert summary["rankings"]["by_return_126"] == ["QQQ", "BAD"]
    assert summary["rankings"]["by_momentum_composite"] == ["QQQ", "BAD"]
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
    assert row["return_21"] is None
    assert row["return_63"] is None
    assert row["return_126"] == 0.12
    assert "return_252" not in row
    assert row["momentum_composite"] is None
    assert row["composite_score"] is None
    assert "sma_20" not in row
    assert "sma_50" not in row
    assert "sma_200" not in row
    assert "price_vs_sma_20" not in row
    assert "price_vs_sma_50" not in row
    assert "price_vs_sma_200" not in row
    assert row["trend_alignment"] is None
    assert "max_drawdown_60" not in row
    assert row["volatility_20"] is None
    assert "distance_to_high_252" not in row
    assert "distance_to_low_252" not in row
    assert row["volume_vs_avg_20"] is None
    assert row["drawdown_from_high_60"] is None
    assert summary["rankings"] == {
        "by_return_21": [],
        "by_return_63": [],
        "by_return_126": ["BAD"],
        "by_momentum_composite": [],
        "by_composite_score": [],
        "by_trend_alignment": [],
    }
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
    assert summary["universe_summary"][0]["trend_alignment"] is None
    assert summary["rankings"]["by_trend_alignment"] == []
    json.dumps(summary, allow_nan=False)
