from __future__ import annotations

import json

import pandas as pd
import pytest

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
    assert summary["momentum"] == {
        "return_20": None,
        "return_21": None,
        "return_60": None,
        "return_63": None,
        "return_120": None,
        "return_126": None,
        "return_252": None,
    }
    assert summary["trend"] == {
        "sma_20": None,
        "sma_50": None,
        "sma_200": None,
        "price_vs_sma_20": None,
        "price_vs_sma_50": None,
        "price_vs_sma_200": None,
    }
    assert summary["range"] == {
        "high_252": None,
        "low_252": None,
        "distance_to_high_252": None,
        "distance_to_low_252": None,
    }
    assert summary["risk"] == {
        "max_drawdown_60": None,
        "volatility_20": None,
    }
    assert summary["scores"] == {
        "momentum_composite": None,
        "trend_alignment": None,
    }
    assert summary["availability"]["latest_close"] is False
    assert summary["availability"]["momentum_composite"] is False
    assert summary["availability"]["trend_alignment"] is False
    assert summary["availability"]["high_252"] is False
    assert summary["availability"]["low_252"] is False
    assert summary["availability"]["max_drawdown_60"] is False
    assert summary["availability"]["volatility_20"] is False


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

    assert summary["universe_summary"] == [
        {
            "symbol": "QQQ",
            "latest_close": qqq["price"]["latest_close"],
            "return_21": qqq["momentum"]["return_21"],
            "return_63": qqq["momentum"]["return_63"],
            "return_126": qqq["momentum"]["return_126"],
            "return_252": qqq["momentum"]["return_252"],
            "momentum_composite": qqq["scores"]["momentum_composite"],
            "sma_20": qqq["trend"]["sma_20"],
            "sma_50": qqq["trend"]["sma_50"],
            "sma_200": qqq["trend"]["sma_200"],
            "price_vs_sma_20": qqq["trend"]["price_vs_sma_20"],
            "price_vs_sma_50": qqq["trend"]["price_vs_sma_50"],
            "price_vs_sma_200": qqq["trend"]["price_vs_sma_200"],
            "trend_alignment": qqq["scores"]["trend_alignment"],
            "max_drawdown_60": qqq["risk"]["max_drawdown_60"],
            "volatility_20": qqq["risk"]["volatility_20"],
            "distance_to_high_252": qqq["range"]["distance_to_high_252"],
            "distance_to_low_252": qqq["range"]["distance_to_low_252"],
        },
        {
            "symbol": "SPY",
            "latest_close": spy["price"]["latest_close"],
            "return_21": spy["momentum"]["return_21"],
            "return_63": spy["momentum"]["return_63"],
            "return_126": spy["momentum"]["return_126"],
            "return_252": spy["momentum"]["return_252"],
            "momentum_composite": spy["scores"]["momentum_composite"],
            "sma_20": spy["trend"]["sma_20"],
            "sma_50": spy["trend"]["sma_50"],
            "sma_200": spy["trend"]["sma_200"],
            "price_vs_sma_20": spy["trend"]["price_vs_sma_20"],
            "price_vs_sma_50": spy["trend"]["price_vs_sma_50"],
            "price_vs_sma_200": spy["trend"]["price_vs_sma_200"],
            "trend_alignment": spy["scores"]["trend_alignment"],
            "max_drawdown_60": spy["risk"]["max_drawdown_60"],
            "volatility_20": spy["risk"]["volatility_20"],
            "distance_to_high_252": spy["range"]["distance_to_high_252"],
            "distance_to_low_252": spy["range"]["distance_to_low_252"],
        },
    ]
    assert summary["rankings"]["by_return_21"] == ["SPY", "QQQ"]
    assert summary["rankings"]["by_return_63"] == ["SPY", "QQQ"]
    assert summary["rankings"]["by_return_126"] == ["SPY", "QQQ"]
    assert summary["rankings"]["by_momentum_composite"] == ["SPY", "QQQ"]
    assert summary["rankings"]["by_trend_alignment"] == ["QQQ", "SPY"]


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
    assert row == {
        "symbol": "BAD",
        "latest_close": None,
        "return_21": None,
        "return_63": None,
        "return_126": 0.12,
        "return_252": None,
        "momentum_composite": None,
        "sma_20": None,
        "sma_50": None,
        "sma_200": 200.0,
        "price_vs_sma_20": None,
        "price_vs_sma_50": 0.03,
        "price_vs_sma_200": None,
        "trend_alignment": None,
        "max_drawdown_60": None,
        "volatility_20": None,
        "distance_to_high_252": None,
        "distance_to_low_252": None,
    }
    assert summary["rankings"] == {
        "by_return_21": [],
        "by_return_63": [],
        "by_return_126": ["BAD"],
        "by_momentum_composite": [],
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
