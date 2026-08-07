# History Load Computed Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a structured `computed_summary` to `market_load_history_table` outputs so LLM agents receive standard price-history statistics without needing DuckDB SQL for basic analysis.

**Architecture:** Create a focused `history_summary.py` module that computes factual statistics from the visible history DataFrame. `DuckDBQueryLayer.load_history_table()` calls that module and appends `computed_summary` to its existing return payload. Replay UI formatting renders the summary but does not calculate metrics.

**Tech Stack:** Python, pandas, DuckDB, Lumibot agent built-in tools, pytest, ruff.

---

## File Structure

- Create `lumibot/components/agents/history_summary.py`
  - Pure calculation module.
  - Public function: `compute_history_summary(frame, *, symbol, timestep, as_of) -> dict[str, Any]`.
  - No Strategy, DuckDB, broker, or LLM dependencies.
- Create `tests/test_agent_history_summary.py`
  - Unit tests for deterministic summary calculations and degraded data.
- Modify `lumibot/components/agents/duckdb_tools.py`
  - Import `compute_history_summary`.
  - Attach `computed_summary` to fresh and cached `load_history_table()` results.
- Modify `lumibot/components/agents/builtins.py`
  - Add one short sentence to `market_load_history_table` description.
- Modify `lumibot/components/agents/replay_ui/formatters.py`
  - Enhance `_market_load_history_table()` explanation when `computed_summary` exists.
- Modify `tests/backtest/test_agent_runtime_backtest.py`
  - Add integration assertions for `computed_summary`, cached payloads, and unchanged DuckDB behavior.
  - Update tool description test.
- Modify or add formatter tests where replay UI formatter tests already live.
  - Preferred search command before editing: `rg -n "_market_load_history_table|human_explanation|format" tests lumibot/components/agents/replay_ui -g "*.py"`.

---

### Task 1: Add Unit Tests for History Summary Calculations

**Files:**
- Create: `tests/test_agent_history_summary.py`
- Create later in Task 2: `lumibot/components/agents/history_summary.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_agent_history_summary.py` with:

```python
from __future__ import annotations

import math

import pandas as pd
import pytest

from lumibot.components.agents.history_summary import compute_history_summary


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


def test_compute_history_summary_missing_close_returns_warnings():
    frame = pd.DataFrame({"Date": pd.date_range("2024-01-01", periods=3), "open": [1, 2, 3]})

    summary = compute_history_summary(frame, symbol="BAD", timestep="day", as_of=None)

    assert summary["data_window"]["row_count"] == 3
    assert summary["price"]["latest_close"] is None
    assert summary["momentum"]["return_20"] is None
    assert summary["availability"]["latest_close"] is False
    assert any("close" in warning.lower() for warning in summary["warnings"])


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
```

- [ ] **Step 2: Run tests and verify they fail because the module does not exist**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'lumibot.components.agents.history_summary'`.

- [ ] **Step 3: Commit failing tests**

```powershell
git add tests\test_agent_history_summary.py
git commit -m "test: specify history computed summary metrics"
```

---

### Task 2: Implement the History Summary Module

**Files:**
- Create: `lumibot/components/agents/history_summary.py`
- Test: `tests/test_agent_history_summary.py`

- [ ] **Step 1: Implement calculation module**

Create `lumibot/components/agents/history_summary.py`:

```python
from __future__ import annotations

import math
from typing import Any

import pandas as pd

SCHEMA_VERSION = "1.0"
MOMENTUM_WINDOWS = (20, 60, 120)
SMA_WINDOWS = (20, 50, 200)
RANGE_WINDOW = 252
DRAWDOWN_WINDOW = 60
VOLATILITY_WINDOW = 20


def _none_metrics() -> dict[str, Any]:
    return {
        "price": {"latest_close": None},
        "momentum": {f"return_{window}": None for window in MOMENTUM_WINDOWS},
        "trend": {
            **{f"sma_{window}": None for window in SMA_WINDOWS},
            **{f"price_vs_sma_{window}": None for window in SMA_WINDOWS},
        },
        "range": {
            "high_252": None,
            "low_252": None,
            "distance_to_high_252": None,
            "distance_to_low_252": None,
        },
        "risk": {
            "volatility_20": None,
            "max_drawdown_60": None,
        },
    }


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _date_bounds(frame: pd.DataFrame) -> tuple[str | None, str | None]:
    if frame.empty:
        return None, None
    date_column = next((column for column in frame.columns if "date" in str(column).lower() or "time" in str(column).lower()), None)
    if date_column is None:
        return None, None
    values = pd.to_datetime(frame[date_column], errors="coerce").dropna()
    if values.empty:
        return None, None
    return values.iloc[0].isoformat(), values.iloc[-1].isoformat()


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator - 1.0


def _max_drawdown(closes: pd.Series) -> float | None:
    if closes.empty:
        return None
    running_peak = closes.cummax()
    drawdowns = closes / running_peak - 1.0
    value = _as_float(drawdowns.min())
    return value


def compute_history_summary(
    frame: pd.DataFrame,
    *,
    symbol: str,
    timestep: str,
    as_of: Any,
) -> dict[str, Any]:
    warnings: list[str] = []
    notes: list[str] = []
    metrics = _none_metrics()
    data_start, data_end = _date_bounds(frame)
    row_count = int(len(frame.index)) if isinstance(frame, pd.DataFrame) else 0
    availability: dict[str, bool] = {key: False for key in ("latest_close",)}
    availability.update({f"return_{window}": False for window in MOMENTUM_WINDOWS})
    availability.update({f"sma_{window}": False for window in SMA_WINDOWS})
    availability.update({"high_252": False, "low_252": False, "volatility_20": False, "max_drawdown_60": False})

    summary = {
        "schema_version": SCHEMA_VERSION,
        "symbol": str(symbol),
        "timestep": str(timestep),
        "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else as_of,
        "data_window": {
            "start": data_start,
            "end": data_end,
            "row_count": row_count,
        },
        **metrics,
        "availability": availability,
        "warnings": warnings,
        "notes": notes,
    }

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        warnings.append("No history rows are available for computed_summary.")
        return summary
    if "close" not in frame.columns:
        warnings.append("No close column is available for price-dependent computed_summary metrics.")
        return summary

    closes = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if closes.empty:
        warnings.append("No numeric close values are available for computed_summary.")
        return summary

    latest_close = _as_float(closes.iloc[-1])
    summary["price"]["latest_close"] = latest_close
    summary["availability"]["latest_close"] = latest_close is not None

    for window in MOMENTUM_WINDOWS:
        key = f"return_{window}"
        if len(closes) > window and latest_close is not None:
            summary["momentum"][key] = _safe_ratio(latest_close, _as_float(closes.iloc[-window - 1]))
            summary["availability"][key] = summary["momentum"][key] is not None

    for window in SMA_WINDOWS:
        sma_key = f"sma_{window}"
        ratio_key = f"price_vs_sma_{window}"
        if len(closes) >= window:
            sma = _as_float(closes.tail(window).mean())
            summary["trend"][sma_key] = sma
            summary["trend"][ratio_key] = _safe_ratio(latest_close, sma)
            summary["availability"][sma_key] = sma is not None

    range_frame = frame.tail(RANGE_WINDOW)
    high_source = "high" if "high" in range_frame.columns else "close"
    low_source = "low" if "low" in range_frame.columns else "close"
    highs = pd.to_numeric(range_frame[high_source], errors="coerce").dropna()
    lows = pd.to_numeric(range_frame[low_source], errors="coerce").dropna()
    high_252 = _as_float(highs.max()) if not highs.empty else None
    low_252 = _as_float(lows.min()) if not lows.empty else None
    summary["range"]["high_252"] = high_252
    summary["range"]["low_252"] = low_252
    summary["range"]["distance_to_high_252"] = _safe_ratio(latest_close, high_252)
    summary["range"]["distance_to_low_252"] = _safe_ratio(latest_close, low_252)
    summary["availability"]["high_252"] = high_252 is not None
    summary["availability"]["low_252"] = low_252 is not None

    returns = closes.pct_change().dropna()
    if len(returns) >= VOLATILITY_WINDOW:
        window_returns = returns.tail(VOLATILITY_WINDOW)
        volatility = _as_float(window_returns.std())
        if volatility is not None:
            if str(timestep).lower() in {"day", "days", "d"}:
                volatility *= math.sqrt(252)
            else:
                notes.append("volatility_20 is computed over 20 bars; annualization is standardized only for daily bars.")
            summary["risk"]["volatility_20"] = volatility
            summary["availability"]["volatility_20"] = True

    drawdown_closes = closes.tail(DRAWDOWN_WINDOW)
    max_drawdown = _max_drawdown(drawdown_closes)
    summary["risk"]["max_drawdown_60"] = max_drawdown
    summary["availability"]["max_drawdown_60"] = max_drawdown is not None

    return summary
```

- [ ] **Step 2: Run unit tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py -q
```

Expected: PASS.

- [ ] **Step 3: Run lint for new module and tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\history_summary.py tests\test_agent_history_summary.py
```

Expected: PASS.

- [ ] **Step 4: Commit implementation**

```powershell
git add lumibot\components\agents\history_summary.py tests\test_agent_history_summary.py
git commit -m "feat: compute standard history summaries"
```

---

### Task 3: Attach Computed Summary to History Load Results

**Files:**
- Modify: `lumibot/components/agents/duckdb_tools.py`
- Modify: `tests/backtest/test_agent_runtime_backtest.py`

- [ ] **Step 1: Add failing integration assertions**

In `tests/backtest/test_agent_runtime_backtest.py`, update `test_duckdb_table_inventory_tracks_fresh_and_cached_history_tables` after `cached_first` is created:

```python
    for result in (first, second, cached_first):
        computed_summary = result["computed_summary"]
        assert computed_summary["schema_version"] == "1.0"
        assert computed_summary["data_window"]["row_count"] == result["row_count"]
        assert set(computed_summary) >= {
            "data_window",
            "price",
            "momentum",
            "trend",
            "range",
            "risk",
            "availability",
        }
```

Add this assertion near the existing `duckdb_query` integration coverage or inside the same test after cached assertions:

```python
    query = strategy.agents.duckdb.query(sql="SELECT COUNT(*) AS count_rows FROM z_history")
    assert query["rows"][0]["count_rows"] == first["row_count"]
```

- [ ] **Step 2: Run integration test and verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_duckdb_table_inventory_tracks_fresh_and_cached_history_tables -q
```

Expected: FAIL with `KeyError: 'computed_summary'`.

- [ ] **Step 3: Implement attachment in `duckdb_tools.py`**

At the top of `lumibot/components/agents/duckdb_tools.py`, add:

```python
from .history_summary import compute_history_summary
```

In `DuckDBQueryLayer.load_history_table()`, after `info` is built and before `self._history_cache[cache_key] = dict(info)`, add:

```python
        summary_frame = None
        if info.get("kind") == "visible_view":
            try:
                summary_frame = self.connection.execute(
                    f"SELECT * FROM {self._quote_identifier(str(info['table_name']))}"
                ).fetch_df()
            except Exception:
                summary_frame = None
        elif "frame" in locals() and isinstance(frame, pd.DataFrame):
            summary_frame = normalized if "normalized" in locals() else frame
        if isinstance(summary_frame, pd.DataFrame):
            info["computed_summary"] = compute_history_summary(
                summary_frame,
                symbol=symbol,
                timestep=timestep,
                as_of=self.strategy.get_datetime() if hasattr(self.strategy, "get_datetime") else None,
            )
        else:
            info["computed_summary"] = compute_history_summary(
                pd.DataFrame(),
                symbol=symbol,
                timestep=timestep,
                as_of=self.strategy.get_datetime() if hasattr(self.strategy, "get_datetime") else None,
            )
```

Keep the existing cache behavior:

```python
        self._history_cache[cache_key] = dict(info)
        result = dict(info)
        result["available_tables"] = self._available_table_schemas()
        return result
```

- [ ] **Step 4: Run integration test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_duckdb_table_inventory_tracks_fresh_and_cached_history_tables -q
```

Expected: PASS.

- [ ] **Step 5: Run focused regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py tests\backtest\test_agent_runtime_backtest.py::test_agent_runtime_replays_market_priming_builtins_on_cache tests\backtest\test_agent_runtime_backtest.py::test_duckdb_table_inventory_tracks_fresh_and_cached_history_tables -q
```

Expected: PASS.

- [ ] **Step 6: Commit integration**

```powershell
git add lumibot\components\agents\duckdb_tools.py tests\backtest\test_agent_runtime_backtest.py
git commit -m "feat: return computed summaries from history loads"
```

---

### Task 4: Update Tool Description Without Reintroducing Global DuckDB Guidance

**Files:**
- Modify: `lumibot/components/agents/builtins.py`
- Modify: `tests/backtest/test_agent_runtime_backtest.py`
- Existing guard tests: `tests/test_agent_manager.py`

- [ ] **Step 1: Update failing description test**

In `test_builtin_market_history_and_duckdb_descriptions_include_schema_hints`, add:

```python
    assert "computed_summary" in history_tool.description
    assert "standard factual statistics" in history_tool.description
```

Keep existing assertions that verify `duckdb_query` still describes schema hints.

- [ ] **Step 2: Run description test and verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_builtin_market_history_and_duckdb_descriptions_include_schema_hints -q
```

Expected: FAIL because `computed_summary` is not in the description yet.

- [ ] **Step 3: Update `market_load_history_table` description**

In `lumibot/components/agents/builtins.py`, inside `_bind_load_history()` description after the `available_tables` sentence, add:

```python
            "The result includes computed_summary with standard factual statistics such as recent returns, "
            "moving averages, range position, volatility, and drawdown; use those fields before writing custom "
            "DuckDB SQL for basic history analysis. "
```

Do not add anything to `AgentHandle._base_system_prompt()` or `_compose_system_prompt()`.

- [ ] **Step 4: Run description and global prompt guard tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_builtin_market_history_and_duckdb_descriptions_include_schema_hints tests\test_agent_manager.py::test_duckdb_sql_guidance_is_not_appended_to_system_prompt tests\test_agent_manager.py::test_duckdb_sql_guidance_is_not_appended_with_actual_bound_tools_by_default -q
```

Expected: PASS.

- [ ] **Step 5: Commit description update**

```powershell
git add lumibot\components\agents\builtins.py tests\backtest\test_agent_runtime_backtest.py
git commit -m "docs: describe history computed summaries"
```

---

### Task 5: Enhance Replay UI Formatter for Computed Summary

**Files:**
- Modify: `lumibot/components/agents/replay_ui/formatters.py`
- Modify or create tests discovered by: `rg -n "_market_load_history_table|format_tool|human_explanation" tests lumibot/components/agents/replay_ui -g "*.py"`

- [ ] **Step 1: Locate formatter tests**

Run:

```powershell
rg -n "_market_load_history_table|market_load_history_table|human_explanation|format_tool" tests lumibot\components\agents\replay_ui -g "*.py"
```

Expected: Identify the existing test file for replay UI formatter behavior. If no formatter-specific test exists, create `tests/test_agent_replay_ui_formatters.py`.

- [ ] **Step 2: Add failing formatter tests**

In the formatter test file, add:

```python
from lumibot.components.agents.replay_ui.formatters import explain_tool_call


def test_market_history_formatter_mentions_computed_summary():
    explanation = explain_tool_call(
        "market_load_history_table",
        {"symbol": "QQQ"},
        {
            "symbol": "QQQ",
            "table_name": "qqq_hist",
            "row_count": 260,
            "computed_summary": {
                "price": {"latest_close": 458.67},
                "momentum": {"return_20": 0.0479, "return_60": -0.0051, "return_120": 0.0462},
                "trend": {
                    "price_vs_sma_20": -0.0096,
                    "price_vs_sma_50": -0.023,
                    "price_vs_sma_200": 0.087,
                },
                "risk": {"max_drawdown_60": -0.12},
            },
        },
    )

    assert "Loaded market history for QQQ into table qqq_hist with 260 rows." in explanation
    assert "latest close 458.67" in explanation
    assert "20-bar return 4.79%" in explanation
    assert "60-bar return -0.51%" in explanation
    assert "120-bar return 4.62%" in explanation
    assert "vs SMA20 -0.96%" in explanation
    assert "max 60-bar drawdown -12.00%" in explanation


def test_market_history_formatter_keeps_fallback_without_computed_summary():
    explanation = explain_tool_call(
        "market_load_history_table",
        {"symbol": "SPY"},
        {"symbol": "SPY", "table_name": "spy_hist", "row_count": 3},
    )

    assert explanation == "Loaded market history for SPY into table spy_hist with 3 rows."
```

If the actual public function is not `explain_tool_call`, inspect `formatters.py` and use the existing exported function.

- [ ] **Step 3: Run formatter tests and verify they fail**

Run the exact formatter test file. Example:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_formatters.py -q
```

Expected: FAIL because computed summary values are not rendered yet.

- [ ] **Step 4: Implement formatter helpers**

In `lumibot/components/agents/replay_ui/formatters.py`, add local helpers near `_market_load_history_table()`:

```python
def _format_percent(value: Any) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return f"{number * 100:.2f}%"


def _computed_summary_sentence(summary: dict[str, Any]) -> str:
    parts: list[str] = []
    price = _as_dict(summary.get("price"))
    momentum = _as_dict(summary.get("momentum"))
    trend = _as_dict(summary.get("trend"))
    risk = _as_dict(summary.get("risk"))
    latest_close = price.get("latest_close")
    if latest_close is not None:
        parts.append(f"latest close {_text(latest_close)}")
    for window in (20, 60, 120):
        formatted = _format_percent(momentum.get(f"return_{window}"))
        if formatted is not None:
            parts.append(f"{window}-bar return {formatted}")
    for window in (20, 50, 200):
        formatted = _format_percent(trend.get(f"price_vs_sma_{window}"))
        if formatted is not None:
            parts.append(f"vs SMA{window} {formatted}")
    drawdown = _format_percent(risk.get("max_drawdown_60"))
    if drawdown is not None:
        parts.append(f"max 60-bar drawdown {drawdown}")
    return " Summary: " + "; ".join(parts) + "." if parts else ""
```

Update `_market_load_history_table()`:

```python
def _market_load_history_table(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    symbol = _first_present(result, "symbol", "ticker") or _first_present(args, "symbol", "ticker")
    table = _first_present(result, "table", "table_name") or _first_present(args, "table", "table_name")
    count = _row_count(raw_result, "rows", "data")
    base = (
        f"Loaded market history for {_text(symbol)} into table {_text(table)} "
        f"with {_rows_label(count)}."
    )
    summary = _as_dict(result.get("computed_summary"))
    return base + _computed_summary_sentence(summary)
```

- [ ] **Step 5: Run formatter tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_formatters.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit UI formatter update**

```powershell
git add lumibot\components\agents\replay_ui\formatters.py tests\test_agent_replay_ui_formatters.py
git commit -m "feat: explain history computed summaries in replay UI"
```

---

### Task 6: Run End-to-End Focused Verification

**Files:**
- No code changes expected.

- [ ] **Step 1: Run focused test suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py tests\test_agent_manager.py tests\backtest\test_agent_runtime_backtest.py::test_duckdb_table_inventory_tracks_fresh_and_cached_history_tables tests\backtest\test_agent_runtime_backtest.py::test_builtin_market_history_and_duckdb_descriptions_include_schema_hints tests\test_agent_replay_ui_formatters.py -q
```

Expected: PASS.

- [ ] **Step 2: Run lint for touched files**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\history_summary.py lumibot\components\agents\duckdb_tools.py lumibot\components\agents\builtins.py lumibot\components\agents\replay_ui\formatters.py tests\test_agent_history_summary.py tests\backtest\test_agent_runtime_backtest.py tests\test_agent_replay_ui_formatters.py
```

Expected: PASS for touched files. If existing unrelated lint failures appear in files touched by previous work, record them and rerun with a narrower `--select F401,F841` only after confirming no new unused imports or variables were introduced.

- [ ] **Step 3: Search for forbidden global DuckDB guidance**

Run:

```powershell
rg -n "DUCKDB SQL GUIDANCE|duckdb_prompt|When querying DuckDB tables|Date, not datetime" lumibot tests -g "*.py"
```

Expected: No production code hits for global prompt guidance. Hits in tests may only be negative assertions or tool-description-specific assertions.

- [ ] **Step 4: Optional one-day backtest smoke**

Only run if API keys are available locally and the user wants an LLM smoke test:

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe - <<'PY'
import os
from datetime import datetime
from pathlib import Path

api_path = Path("project_notes/API.txt")
key = next((line.strip() for line in api_path.read_text(encoding="utf-8").splitlines() if line.strip().startswith("sk-")), None)
if not key:
    raise RuntimeError("OpenAI key not found")
os.environ["OPENAI_API_KEY"] = key
os.environ["AI_TRADING_TEAM_MODEL"] = "openai/gpt-5.4-mini"
os.environ["LUMIBOT_AGENT_TRACE_DETAIL"] = "1"
os.environ["LUMIBOT_AGENT_BOUNDARY_TRACE"] = "1"

from lumibot.backtesting import YahooDataBacktesting
from lumibot.example_strategies.ai_trading_team_growth_execution_test import AITradingTeamGrowthExecutionTestStrategy

_, strategy = AITradingTeamGrowthExecutionTestStrategy.run_backtest(
    datasource_class=YahooDataBacktesting,
    backtesting_start=datetime(2024, 9, 5),
    backtesting_end=datetime(2024, 9, 6),
    benchmark_asset="SPY",
    analyze_backtest=False,
    show_plot=False,
    save_tearsheet=False,
    show_tearsheet=False,
    show_indicators=False,
    save_logfile=False,
    quiet_logs=True,
)
print("last_execution_plan_error=", getattr(strategy, "_last_execution_plan_error", None))
PY
```

Expected: Backtest completes. Latest `market_load_history_table` trace outputs contain `computed_summary`.

- [ ] **Step 5: Commit verification note only if code changed during verification**

If no code changed, do not commit. If verification fixes were needed:

```powershell
git add <changed-files>
git commit -m "fix: stabilize history computed summary verification"
```

---

## Self-Review Notes

- Spec coverage: Tasks cover summary module, tool integration, tool description, replay UI, tests, and global prompt guard.
- Scope boundaries: No new LLM-facing tool, no DuckDB persistence, no strategy workflow change.
- Type consistency: Plan uses `computed_summary`, `schema_version`, `data_window`, `price`, `momentum`, `trend`, `range`, `risk`, and `availability` consistently with the spec.
- Known caution: The example implementation in Task 3 uses `locals()` to avoid touching the broader history-load control flow. During execution, prefer a cleaner local variable if the implementer can keep the diff small.

