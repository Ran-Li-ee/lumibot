from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

YahooHelper = None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_stats(artifact: Path) -> pd.DataFrame:
    parquet_path = artifact / "stats.parquet"
    csv_path = artifact / "stats.csv"
    if parquet_path.exists():
        stats = pd.read_parquet(parquet_path)
    elif csv_path.exists():
        stats = pd.read_csv(csv_path)
    else:
        raise FileNotFoundError(f"No stats.parquet or stats.csv found in {artifact}")

    stats = stats.copy()
    if "datetime" not in stats.columns:
        stats["datetime"] = stats.index
    if "portfolio_value" not in stats.columns:
        raise ValueError(f"{artifact} stats must include portfolio_value")

    stats.reset_index(drop=True, inplace=True)
    stats["datetime"] = pd.to_datetime(stats["datetime"], utc=True)
    stats["portfolio_value"] = pd.to_numeric(stats["portfolio_value"], errors="coerce")
    stats.dropna(subset=["datetime", "portfolio_value"], inplace=True)
    stats.sort_values("datetime", inplace=True)
    if stats.empty:
        raise ValueError(f"{artifact} stats contains no valid portfolio_value rows")
    return stats


def _rounded(value: float | None) -> float | None:
    if value is None:
        return None
    if not math.isfinite(float(value)):
        return None
    return round(float(value), 12)


def _max_drawdown(values: pd.Series) -> float | None:
    if values.empty:
        return None
    running_max = values.cummax()
    drawdowns = values / running_max - 1.0
    return _rounded(float(drawdowns.min()))


def _annualized_return(stats: pd.DataFrame) -> float | None:
    if len(stats) < 2:
        return None
    start_value = float(stats["portfolio_value"].iloc[0])
    end_value = float(stats["portfolio_value"].iloc[-1])
    if start_value <= 0 or end_value <= 0:
        return None
    days = (stats["datetime"].iloc[-1] - stats["datetime"].iloc[0]).total_seconds() / 86400
    if days <= 0:
        return None
    return _rounded((end_value / start_value) ** (365.25 / days) - 1.0)


def _annualization_factor(stats: pd.DataFrame) -> int:
    if len(stats) < 3:
        return 252
    day_deltas = stats["datetime"].diff().dt.total_seconds().dropna() / 86400
    if day_deltas.empty:
        return 252
    median_days = float(day_deltas.median())
    if median_days >= 20:
        return 12
    if median_days >= 5:
        return 52
    return 252


def _period_returns(stats: pd.DataFrame) -> pd.Series:
    returns = stats["portfolio_value"].astype(float).pct_change().dropna()
    return returns[pd.notna(returns)]


def _volatility(stats: pd.DataFrame) -> float | None:
    returns = _period_returns(stats)
    if len(returns) < 2:
        return None
    return _rounded(float(returns.std(ddof=1) * math.sqrt(_annualization_factor(stats))))


def _sharpe(stats: pd.DataFrame) -> float | None:
    returns = _period_returns(stats)
    if len(returns) < 2:
        return None
    std = float(returns.std(ddof=1))
    if std == 0 or not math.isfinite(std):
        return None
    return _rounded(float(returns.mean() / std * math.sqrt(_annualization_factor(stats))))


def _trade_count(artifact: Path) -> int:
    trades_path = artifact / "trades.csv"
    if not trades_path.exists():
        return 0
    trades = pd.read_csv(trades_path)
    return int(len(trades))


def _yearly_returns(stats: pd.DataFrame) -> dict[str, float]:
    frame = stats.copy()
    frame["year"] = frame["datetime"].dt.year.astype(str)
    returns: dict[str, float] = {}
    previous_year_close: float | None = None
    for year, group in frame.groupby("year", sort=True):
        first = previous_year_close if previous_year_close is not None else float(group["portfolio_value"].iloc[0])
        last = float(group["portfolio_value"].iloc[-1])
        previous_year_close = last
        if first > 0:
            returns[str(year)] = _rounded((last / first) - 1.0) or 0.0
    return returns


def _stats_from_price_series(prices: pd.Series, *, start_value: float = 100000.0) -> pd.DataFrame:
    if prices.empty:
        raise ValueError("Price series must contain at least one row")
    frame = pd.DataFrame(
        {
            "datetime": pd.to_datetime(prices.index, utc=True),
            "price": pd.to_numeric(prices, errors="coerce").to_numpy(),
        }
    )
    frame.dropna(subset=["datetime", "price"], inplace=True)
    frame.sort_values("datetime", inplace=True)
    if frame.empty:
        raise ValueError("Price series contains no valid prices")
    first_price = float(frame["price"].iloc[0])
    if first_price <= 0:
        raise ValueError("Price series must start with a positive price")
    frame["portfolio_value"] = frame["price"].astype(float) / first_price * float(start_value)
    return frame[["datetime", "portfolio_value"]].reset_index(drop=True)


def artifact_metrics(artifact: str | Path) -> dict[str, Any]:
    root = Path(artifact)
    stats = _read_stats(root)
    result = _read_json(root / "result.json")
    start_value = float(stats["portfolio_value"].iloc[0])
    end_value = float(stats["portfolio_value"].iloc[-1])
    return {
        "artifact": str(root.resolve()),
        "strategy": result.get("strategy", root.name),
        "status": result.get("status"),
        "start_value": start_value,
        "end_value": end_value,
        "total_return": _rounded((end_value / start_value) - 1.0) if start_value else None,
        "cagr": _annualized_return(stats),
        "max_drawdown": _max_drawdown(stats["portfolio_value"].astype(float)),
        "volatility": _volatility(stats),
        "sharpe": _sharpe(stats),
        "yearly_returns": _yearly_returns(stats),
        "trade_count": _trade_count(root),
        "positions": result.get("positions") or [],
    }


def buy_and_hold_metrics(
    symbol: str,
    *,
    start: str,
    end: str,
    start_value: float = 100000.0,
) -> dict[str, Any]:
    symbol = symbol.upper().strip()
    if not symbol:
        raise ValueError("symbol must be non-empty")
    yahoo_helper = _get_yahoo_helper()
    if yahoo_helper is None:
        raise RuntimeError("YahooHelper is unavailable; install optional Yahoo data dependencies to use SPY metrics")

    data = yahoo_helper.get_symbol_data(symbol, interval="1d", auto_adjust=True, caching=True)
    if data is None or data.empty:
        raise ValueError(f"Yahoo data for {symbol} is empty")
    close_column = "Close" if "Close" in data.columns else "close" if "close" in data.columns else None
    if close_column is None:
        raise ValueError(f"Yahoo data for {symbol} must include Close or close column")

    frame = data.copy()
    frame.index = pd.to_datetime(frame.index, utc=True)
    start_date = pd.to_datetime(start).date()
    end_date = pd.to_datetime(end).date()
    frame = frame[(frame.index.date >= start_date) & (frame.index.date <= end_date)]
    if len(frame) < 2:
        raise ValueError(f"Yahoo data for {symbol} must include at least two rows between start and end")

    stats = _stats_from_price_series(frame[close_column], start_value=start_value)
    first_value = float(stats["portfolio_value"].iloc[0])
    last_value = float(stats["portfolio_value"].iloc[-1])
    return {
        "artifact": None,
        "strategy": f"{symbol} buy and hold",
        "symbol": symbol,
        "status": "computed",
        "start_value": first_value,
        "end_value": last_value,
        "total_return": _rounded((last_value / first_value) - 1.0) if first_value else None,
        "cagr": _annualized_return(stats),
        "max_drawdown": _max_drawdown(stats["portfolio_value"].astype(float)),
        "volatility": _volatility(stats),
        "sharpe": _sharpe(stats),
        "yearly_returns": _yearly_returns(stats),
        "trade_count": 1,
        "positions": [symbol],
    }


def _diff(left: Any, right: Any) -> Any:
    if left is None or right is None:
        return None
    if isinstance(left, bool) or isinstance(right, bool):
        return None
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if math.isfinite(float(left)) and math.isfinite(float(right)):
            return _rounded(float(left) - float(right))
    return None


def _get_yahoo_helper():
    global YahooHelper
    if YahooHelper is not None:
        return YahooHelper
    try:
        from lumibot.tools import YahooHelper as imported_yahoo_helper
    except ModuleNotFoundError as exc:
        if exc.name in {"yfinance", "yahooquery"}:
            return None
        raise
    YahooHelper = imported_yahoo_helper
    return YahooHelper


def compare_artifacts(baseline_artifact: str | Path, candidate_artifact: str | Path) -> dict[str, Any]:
    baseline = artifact_metrics(baseline_artifact)
    candidate = artifact_metrics(candidate_artifact)
    return {
        "baseline": baseline,
        "candidate": candidate,
        "comparison": {
            "total_return_difference": _diff(candidate["total_return"], baseline["total_return"]),
            "cagr_difference": _diff(candidate["cagr"], baseline["cagr"]),
            "max_drawdown_difference": _diff(candidate["max_drawdown"], baseline["max_drawdown"]),
            "volatility_difference": _diff(candidate["volatility"], baseline["volatility"]),
            "sharpe_difference": _diff(candidate["sharpe"], baseline["sharpe"]),
            "trade_count_difference": _diff(candidate["trade_count"], baseline["trade_count"]),
        },
    }


def compare_named_artifacts(
    artifacts: dict[str, str | Path],
    *,
    reference_label: str = "equity_only_llm",
    spy_buy_hold_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metrics = {label: artifact_metrics(path) for label, path in artifacts.items()}
    relative_key = f"relative_to_{reference_label}"
    relative: dict[str, dict[str, Any]] = {}
    reference = metrics.get(reference_label)
    if reference is not None:
        for label, row in metrics.items():
            relative[label] = {
                "total_return_difference": _diff(row["total_return"], reference["total_return"]),
                "cagr_difference": _diff(row["cagr"], reference["cagr"]),
                "max_drawdown_difference": _diff(row["max_drawdown"], reference["max_drawdown"]),
                "volatility_difference": _diff(row["volatility"], reference["volatility"]),
                "sharpe_difference": _diff(row["sharpe"], reference["sharpe"]),
                "trade_count_difference": _diff(row["trade_count"], reference["trade_count"]),
            }

    payload: dict[str, Any] = {
        "strategies": list(artifacts),
        "reference_label": reference_label,
        "metrics": metrics,
        relative_key: relative,
    }
    if spy_buy_hold_metrics is not None:
        payload["spy_buy_and_hold"] = spy_buy_hold_metrics
    return payload


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def _num(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f}"


def _metric_row(label: str, left: str, right: str, difference: str) -> str:
    return f"| {label} | {left} | {right} | {difference} |"


def _multi_metric_row(label: str, metrics: dict[str, Any]) -> str:
    return (
        f"| {label} | {_pct(metrics.get('total_return'))} | {_pct(metrics.get('cagr'))} | "
        f"{_pct(metrics.get('max_drawdown'))} | {_pct(metrics.get('volatility'))} | "
        f"{_num(metrics.get('sharpe'))} | {metrics.get('trade_count', 'n/a')} |"
    )


def render_markdown(payload: dict[str, Any]) -> str:
    baseline = payload["baseline"]
    candidate = payload["candidate"]
    comparison = payload["comparison"]
    return "\n".join(
        [
            "# Strategy Artifact Comparison",
            "",
            "| Metric | Baseline | Candidate | Difference |",
            "|---|---:|---:|---:|",
            _metric_row(
                "Total return",
                _pct(baseline["total_return"]),
                _pct(candidate["total_return"]),
                _pct(comparison["total_return_difference"]),
            ),
            _metric_row(
                "CAGR",
                _pct(baseline["cagr"]),
                _pct(candidate["cagr"]),
                _pct(comparison["cagr_difference"]),
            ),
            _metric_row(
                "Max drawdown",
                _pct(baseline["max_drawdown"]),
                _pct(candidate["max_drawdown"]),
                _pct(comparison["max_drawdown_difference"]),
            ),
            _metric_row(
                "Volatility",
                _pct(baseline["volatility"]),
                _pct(candidate["volatility"]),
                _pct(comparison["volatility_difference"]),
            ),
            _metric_row(
                "Sharpe",
                _num(baseline["sharpe"]),
                _num(candidate["sharpe"]),
                _num(comparison["sharpe_difference"]),
            ),
            _metric_row(
                "Trade count",
                str(baseline["trade_count"]),
                str(candidate["trade_count"]),
                str(comparison["trade_count_difference"]),
            ),
            "",
            "Do not read the highest-return row as automatically best; compare return, drawdown, "
            "volatility, Sharpe, and turnover together.",
            "",
        ]
    )


def render_multi_markdown(payload: dict[str, Any]) -> str:
    rows = [_multi_metric_row(label, payload["metrics"][label]) for label in payload["strategies"]]
    if payload.get("spy_buy_and_hold") is not None:
        benchmark = payload["spy_buy_and_hold"]
        rows.append(_multi_metric_row(str(benchmark.get("strategy") or "buy and hold"), benchmark))

    return "\n".join(
        [
            "# Strategy Artifact Comparison",
            "",
            "| Strategy | Total return | CAGR | Max drawdown | Volatility | Sharpe | Trades |",
            "|---|---:|---:|---:|---:|---:|---:|",
            *rows,
            "",
            (
                "Do not read the highest-return row as automatically best; compare return, drawdown, "
                "volatility, Sharpe, turnover, and strategy behavior together."
            ),
            "",
        ]
    )


def parse_named_artifacts(values: list[str]) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Named artifact must be in label=path form: {value}")
        label, path = value.split("=", 1)
        label = label.strip()
        path = path.strip()
        if not label or not path:
            raise ValueError(f"Named artifact must include non-empty label and path: {value}")
        if label in artifacts:
            raise ValueError(f"Duplicate artifact label: {label}")
        artifacts[label] = Path(path)
    return artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-artifact")
    parser.add_argument("--candidate-artifact")
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        help="Named artifact in label=path form. When provided, writes the multi-strategy report.",
    )
    parser.add_argument("--reference-label", default="equity_only_llm")
    parser.add_argument("--spy-symbol", default=None)
    parser.add_argument("--spy-start", default=None)
    parser.add_argument("--spy-end", default=None)
    args = parser.parse_args(argv)

    try:
        named_artifacts = parse_named_artifacts(args.artifact)
    except ValueError as exc:
        parser.error(str(exc))
    if named_artifacts:
        spy_metrics = None
        if args.spy_symbol:
            if not args.spy_start or not args.spy_end:
                parser.error("--spy-start and --spy-end are required when --spy-symbol is provided")
            spy_metrics = buy_and_hold_metrics(args.spy_symbol, start=args.spy_start, end=args.spy_end)
        payload = compare_named_artifacts(
            named_artifacts,
            reference_label=args.reference_label,
            spy_buy_hold_metrics=spy_metrics,
        )
        output_prefix = Path(args.output_prefix)
        output_prefix.parent.mkdir(parents=True, exist_ok=True)
        output_prefix.with_suffix(".json").write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        output_prefix.with_suffix(".md").write_text(render_multi_markdown(payload), encoding="utf-8")
        print(
            json.dumps(
                {
                    "json": str(output_prefix.with_suffix(".json")),
                    "markdown": str(output_prefix.with_suffix(".md")),
                },
                sort_keys=True,
            )
        )
        return 0

    if not args.baseline_artifact or not args.candidate_artifact:
        parser.error("--baseline-artifact and --candidate-artifact are required unless --artifact is provided")

    payload = compare_artifacts(args.baseline_artifact, args.candidate_artifact)
    output_prefix = Path(args.output_prefix)
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    output_prefix.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    output_prefix.with_suffix(".md").write_text(render_markdown(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(output_prefix.with_suffix(".json")),
                "markdown": str(output_prefix.with_suffix(".md")),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
