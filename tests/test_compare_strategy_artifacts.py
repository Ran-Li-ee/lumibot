import importlib
import subprocess
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

compare = importlib.import_module("scripts.compare_strategy_artifacts")


def _write_artifact(tmp_path, *, name, values, trades):
    root = tmp_path / name
    root.mkdir()
    stats = pd.DataFrame(
        {
            "datetime": pd.date_range("2020-01-01", periods=len(values), freq="D", tz="UTC"),
            "portfolio_value": values,
        }
    )
    stats.to_csv(root / "stats.csv", index=False)
    pd.DataFrame({"symbol": ["SPY"] * trades}).to_csv(root / "trades.csv", index=False)
    (root / "result.json").write_text(
        '{"strategy":"' + name + '","status":"passed","positions":[]}',
        encoding="utf-8",
    )
    return root


def test_compare_named_artifacts_returns_metrics_for_multiple_strategies(tmp_path):
    first = _write_artifact(tmp_path, name="first", values=[100000, 110000], trades=1)
    second = _write_artifact(tmp_path, name="second", values=[100000, 125000], trades=2)

    payload = compare.compare_named_artifacts({"first": first, "second": second}, reference_label="first")

    assert payload["metrics"]["first"]["total_return"] == 0.1
    assert payload["metrics"]["second"]["total_return"] == 0.25
    assert payload["relative_to_first"]["second"]["total_return_difference"] == 0.15


def test_render_multi_markdown_warns_against_return_only_reading(tmp_path):
    first = _write_artifact(tmp_path, name="first", values=[100000, 110000], trades=1)
    payload = compare.compare_named_artifacts({"first": first}, reference_label="first")

    markdown = compare.render_multi_markdown(payload)

    assert "# Strategy Artifact Comparison" in markdown
    assert "| Strategy | Total return | CAGR | Max drawdown | Volatility | Sharpe | Trades |" in markdown
    assert "Do not read the highest-return row as automatically best" in markdown


def test_parse_named_artifacts_rejects_duplicate_labels():
    with pytest.raises(ValueError, match="Duplicate artifact label"):
        compare.parse_named_artifacts(["same=a", "same=b"])


def test_buy_and_hold_metrics_rejects_empty_yahoo_data(monkeypatch):
    monkeypatch.setattr(
        compare,
        "YahooHelper",
        SimpleNamespace(get_symbol_data=lambda symbol, interval="1d", auto_adjust=True, caching=True: None),
    )

    with pytest.raises(ValueError, match="Yahoo data for SPY is empty"):
        compare.buy_and_hold_metrics("SPY", start="2020-05-01", end="2025-05-01")


def test_render_multi_markdown_uses_benchmark_strategy_label(tmp_path):
    first = _write_artifact(tmp_path, name="first", values=[100000, 110000], trades=1)
    payload = compare.compare_named_artifacts(
        {"first": first},
        reference_label="first",
        spy_buy_hold_metrics={
            "strategy": "QQQ buy and hold",
            "total_return": 0.2,
            "cagr": 0.1,
            "max_drawdown": -0.05,
            "volatility": 0.15,
            "sharpe": 0.7,
            "trade_count": 1,
        },
    )

    markdown = compare.render_multi_markdown(payload)

    assert "QQQ buy and hold" in markdown
    assert "SPY buy and hold" not in markdown


def test_cli_help_does_not_require_yahoo_import():
    result = subprocess.run(
        [sys.executable, "scripts/compare_strategy_artifacts.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout
