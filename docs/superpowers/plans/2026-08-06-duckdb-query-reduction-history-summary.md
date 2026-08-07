# DuckDB Query Reduction History Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce unnecessary LLM-written DuckDB SQL by making computed summaries and cross-symbol rankings the preferred path for common historical analysis.

**Architecture:** Extend the existing `history_summary.py` calculation module, then reuse it from `DuckDBQueryLayer` for both single-symbol history loads and a new batch universe summary tool. Prompt/tool descriptions should position DuckDB SQL as a fallback, and replay UI should render the new fields without doing financial calculations itself.

**Tech Stack:** Python, pandas, DuckDB, existing LumiBot agent built-in tools, pytest, ruff.

---

## File Structure

- Modify `lumibot/components/agents/history_summary.py`
  - Owns all deterministic calculation logic.
  - Add 21/63/126/252-bar returns, `scores`, and helper functions for flattening/ranking summaries.
- Modify `lumibot/components/agents/duckdb_tools.py`
  - Add `load_history_tables_summary(...)`.
  - Reuse existing `load_history_table(...)` for per-symbol loading and table registration.
- Modify `lumibot/components/agents/builtins.py`
  - Add `_bind_load_history_tables_summary(...)`.
  - Expose `BuiltinTools.market.load_history_tables_summary()`.
  - Include the new tool in `BuiltinTools.all()`.
  - Replace generic "Use DuckDB..." prompt guidance with computed-summary-first guidance wherever it is generated.
- Modify `lumibot/components/agents/replay_ui/formatters.py`
  - Render expanded single-symbol summary fields.
  - Add formatter for `market_load_history_tables_summary`.
- Modify tests:
  - `tests/test_agent_history_summary.py`
  - `tests/backtest/test_agent_runtime_backtest.py`
  - `tests/test_agent_replay_ui_formatters.py`
  - `tests/test_agent_manager.py` if prompt composition tests need direct assertions.

---

## Task 1: Extend Single-Symbol History Summary Metrics

**Files:**
- Modify: `tests/test_agent_history_summary.py`
- Modify: `lumibot/components/agents/history_summary.py`

- [ ] **Step 1: Add failing tests for additional momentum windows and scores**

Append these tests to `tests/test_agent_history_summary.py`:

```python
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
```

Append this short-data test:

```python
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
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py -q
```

Expected: FAIL because `return_21`, `return_63`, `return_126`, `return_252`, and `scores` do not exist yet.

- [ ] **Step 3: Implement the metrics in `history_summary.py`**

In `compute_history_summary`, replace the `momentum` dict with:

```python
    momentum = {
        "return_20": _period_return(close, 20),
        "return_21": _period_return(close, 21),
        "return_60": _period_return(close, 60),
        "return_63": _period_return(close, 63),
        "return_120": _period_return(close, 120),
        "return_126": _period_return(close, 126),
        "return_252": _period_return(close, 252),
    }
```

After the `trend.update(...)` block, add:

```python
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
    }
```

In the `availability` dict, add:

```python
        "return_21": momentum["return_21"] is not None,
        "return_63": momentum["return_63"] is not None,
        "return_126": momentum["return_126"] is not None,
        "return_252": momentum["return_252"] is not None,
        "momentum_composite": scores["momentum_composite"] is not None,
        "trend_alignment": scores["trend_alignment"] is not None,
```

In the returned payload, add:

```python
        "scores": scores,
```

Add helper functions near `_relative_to`:

```python
def _mean_available(values: list[float | None]) -> float | None:
    available = [float(value) for value in values if value is not None]
    if not available:
        return None
    return sum(available) / len(available)


def _trend_alignment(values: list[float | None]) -> int | None:
    available = [value for value in values if value is not None]
    if not available:
        return None
    return sum(1 for value in available if value > 0)
```

- [ ] **Step 4: Verify Task 1**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py -q
.\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\history_summary.py tests\test_agent_history_summary.py
```

Expected: all tests pass; ruff passes.

- [ ] **Step 5: Commit Task 1**

```powershell
git add lumibot\components\agents\history_summary.py tests\test_agent_history_summary.py
git commit -m "feat: expand history summary ranking metrics"
```

---

## Task 2: Add Batch Summary Assembly Helpers

**Files:**
- Modify: `tests/test_agent_history_summary.py`
- Modify: `lumibot/components/agents/history_summary.py`

- [ ] **Step 1: Add failing tests for row flattening and rankings**

Append to `tests/test_agent_history_summary.py`:

```python
from lumibot.components.agents.history_summary import build_universe_history_summary
```

If the import section already imports only `compute_history_summary`, replace it with:

```python
from lumibot.components.agents.history_summary import (
    build_universe_history_summary,
    compute_history_summary,
)
```

Add this test:

```python
def test_build_universe_history_summary_flattens_rows_and_rankings():
    spy = compute_history_summary(_frame(260), symbol="SPY", timestep="day", as_of="2024-09-05")
    qqq_frame = _frame(260)
    qqq_frame["close"] = qqq_frame["close"] * 1.2
    qqq_frame["high"] = qqq_frame["high"] * 1.2
    qqq_frame["low"] = qqq_frame["low"] * 1.2
    qqq = compute_history_summary(qqq_frame, symbol="QQQ", timestep="day", as_of="2024-09-05")

    payload = build_universe_history_summary(
        summaries=[spy, qqq],
        symbols=["SPY", "QQQ"],
        timestep="day",
        length=252,
        as_of="2024-09-05",
        loaded_tables=[
            {"symbol": "SPY", "table_name": "spy_hist", "row_count": 252, "columns": ["Date", "close"]},
            {"symbol": "QQQ", "table_name": "qqq_hist", "row_count": 252, "columns": ["Date", "close"]},
        ],
        warnings=[],
    )

    assert payload["schema_version"] == "1.0"
    assert payload["symbols"] == ["SPY", "QQQ"]
    assert len(payload["universe_summary"]) == 2
    row = payload["universe_summary"][0]
    assert set(row) >= {
        "symbol",
        "latest_close",
        "return_21",
        "return_63",
        "return_126",
        "momentum_composite",
        "sma_20",
        "price_vs_sma_20",
        "trend_alignment",
        "max_drawdown_60",
        "volatility_20",
        "distance_to_high_252",
    }
    assert payload["rankings"]["by_return_63"]
    assert payload["rankings"]["by_momentum_composite"]
    assert payload["rankings"]["by_trend_alignment"]
```

Add this test for unavailable metrics:

```python
def test_build_universe_history_summary_skips_unavailable_ranking_values():
    short = compute_history_summary(_frame(10), symbol="SHORT", timestep="day", as_of=None)
    full = compute_history_summary(_frame(260), symbol="FULL", timestep="day", as_of=None)

    payload = build_universe_history_summary(
        summaries=[short, full],
        symbols=["SHORT", "FULL"],
        timestep="day",
        length=252,
        as_of=None,
        loaded_tables=[],
        warnings=["demo warning"],
    )

    assert payload["rankings"]["by_return_63"] == ["FULL"]
    assert "SHORT" not in payload["rankings"]["by_return_126"]
    assert payload["warnings"] == ["demo warning"]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py -q
```

Expected: FAIL because `build_universe_history_summary` does not exist.

- [ ] **Step 3: Implement batch helper functions**

Add to `history_summary.py` after `compute_history_summary`:

```python
def build_universe_history_summary(
    *,
    summaries: list[dict[str, Any]],
    symbols: list[str],
    timestep: str | None,
    length: int,
    as_of: str | None,
    loaded_tables: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    rows = [_summary_to_universe_row(summary) for summary in summaries]
    return {
        "schema_version": SCHEMA_VERSION,
        "symbols": list(symbols),
        "timestep": timestep,
        "length": int(length),
        "as_of": as_of,
        "loaded_tables": loaded_tables,
        "universe_summary": rows,
        "rankings": _rankings(rows),
        "warnings": list(warnings),
    }
```

Add helpers:

```python
def _summary_to_universe_row(summary: dict[str, Any]) -> dict[str, Any]:
    price = _dict(summary.get("price"))
    momentum = _dict(summary.get("momentum"))
    trend = _dict(summary.get("trend"))
    range_summary = _dict(summary.get("range"))
    risk = _dict(summary.get("risk"))
    scores = _dict(summary.get("scores"))
    return {
        "symbol": summary.get("symbol"),
        "latest_close": price.get("latest_close"),
        "return_21": momentum.get("return_21"),
        "return_63": momentum.get("return_63"),
        "return_126": momentum.get("return_126"),
        "return_252": momentum.get("return_252"),
        "momentum_composite": scores.get("momentum_composite"),
        "sma_20": trend.get("sma_20"),
        "sma_50": trend.get("sma_50"),
        "sma_200": trend.get("sma_200"),
        "price_vs_sma_20": trend.get("price_vs_sma_20"),
        "price_vs_sma_50": trend.get("price_vs_sma_50"),
        "price_vs_sma_200": trend.get("price_vs_sma_200"),
        "trend_alignment": scores.get("trend_alignment"),
        "max_drawdown_60": risk.get("max_drawdown_60"),
        "volatility_20": risk.get("volatility_20"),
        "distance_to_high_252": range_summary.get("distance_to_high_252"),
        "distance_to_low_252": range_summary.get("distance_to_low_252"),
    }
```

```python
def _rankings(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {
        "by_return_21": _rank_symbols(rows, "return_21"),
        "by_return_63": _rank_symbols(rows, "return_63"),
        "by_return_126": _rank_symbols(rows, "return_126"),
        "by_momentum_composite": _rank_symbols(rows, "momentum_composite"),
        "by_trend_alignment": _rank_symbols(rows, "trend_alignment"),
    }


def _rank_symbols(rows: list[dict[str, Any]], key: str) -> list[str]:
    ranked = [
        row
        for row in rows
        if row.get("symbol") is not None and isinstance(row.get(key), int | float) and not isinstance(row.get(key), bool)
    ]
    ranked.sort(key=lambda row: float(row[key]), reverse=True)
    return [str(row["symbol"]) for row in ranked]


def _dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}
```

If ruff flags the `_rank_symbols` comprehension line length, split the condition into a small helper:

```python
def _is_rankable(row: dict[str, Any], key: str) -> bool:
    value = row.get(key)
    return row.get("symbol") is not None and isinstance(value, int | float) and not isinstance(value, bool)
```

- [ ] **Step 4: Verify Task 2**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py -q
.\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\history_summary.py tests\test_agent_history_summary.py
```

Expected: all tests pass; ruff passes.

- [ ] **Step 5: Commit Task 2**

```powershell
git add lumibot\components\agents\history_summary.py tests\test_agent_history_summary.py
git commit -m "feat: build universe history rankings"
```

---

## Task 3: Add DuckDB Batch Summary Method And Built-In Tool

**Files:**
- Modify: `tests/backtest/test_agent_runtime_backtest.py`
- Modify: `lumibot/components/agents/duckdb_tools.py`
- Modify: `lumibot/components/agents/builtins.py`

- [ ] **Step 1: Add failing integration test for DuckDB layer batch summary**

Append near `test_duckdb_table_inventory_tracks_fresh_and_cached_history_tables` in `tests/backtest/test_agent_runtime_backtest.py`:

```python
@pytest.mark.usefixtures("disable_datasource_override")
def test_duckdb_history_tables_summary_returns_rankings_and_queryable_tables(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path / "cache"))
    pandas_data = _build_stock_pandas_data()
    first_data = next(iter(pandas_data.values()))
    second_asset = Asset("AGST2", Asset.AssetType.STOCK)
    second_frame = first_data.df.copy()
    second_frame["close"] = second_frame["close"] * 1.2
    second_frame["high"] = second_frame["high"] * 1.2
    second_frame["low"] = second_frame["low"] * 1.2
    pandas_data[second_asset] = Data(second_asset, second_frame, timestep="minute")
    _, strategy = PromptCaptureStrategy.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=datetime(2025, 1, 6),
        backtesting_end=datetime(2025, 1, 7),
        pandas_data=pandas_data,
        benchmark_asset=None,
        analyze_backtest=False,
        show_plot=False,
        save_tearsheet=False,
        show_tearsheet=False,
        show_indicators=False,
        save_logfile=False,
        show_progress_bar=False,
        quiet_logs=True,
    )

    payload = strategy.agents.duckdb.load_history_tables_summary(
        symbols=["AGST", "AGST2"],
        length=30,
        timestep="minute",
        table_prefix="cmp",
    )

    assert payload["schema_version"] == "1.0"
    assert payload["symbols"] == ["AGST", "AGST2"]
    assert len(payload["loaded_tables"]) == 2
    assert len(payload["universe_summary"]) == 2
    assert set(payload["rankings"]) >= {"by_return_21", "by_return_63", "by_momentum_composite"}
    assert all(table["table_name"].startswith("cmp_") for table in payload["loaded_tables"])
    query = strategy.agents.duckdb.query(sql="SELECT COUNT(*) AS count_rows FROM cmp_agst")
    assert query["rows"][0]["count_rows"] == 30
```

Add a validation test:

```python
def test_builtin_market_history_tables_summary_rejects_empty_symbols():
    from lumibot.components.agents import BuiltinTools

    _, strategy = PromptCaptureStrategy.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=datetime(2025, 1, 6),
        backtesting_end=datetime(2025, 1, 7),
        pandas_data=_build_stock_pandas_data(),
        benchmark_asset=None,
        analyze_backtest=False,
        show_plot=False,
        save_tearsheet=False,
        show_tearsheet=False,
        show_indicators=False,
        save_logfile=False,
        show_progress_bar=False,
        quiet_logs=True,
    )
    tool = BuiltinTools.market.load_history_tables_summary().binder(strategy, strategy.agents)

    with pytest.raises(ValueError, match="symbols"):
        tool.function(symbols=[])
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_duckdb_history_tables_summary_returns_rankings_and_queryable_tables tests\backtest\test_agent_runtime_backtest.py::test_builtin_market_history_tables_summary_rejects_empty_symbols -q
```

Expected: FAIL because `load_history_tables_summary` does not exist.

- [ ] **Step 3: Implement `DuckDBQueryLayer.load_history_tables_summary`**

In `duckdb_tools.py`, update imports:

```python
from .history_summary import build_universe_history_summary, compute_history_summary
```

Add this method inside `DuckDBQueryLayer` after `load_history_table`:

```python
    def load_history_tables_summary(
        self,
        *,
        symbols: list[str],
        length: int = 252,
        timestep: str = "day",
        asset_type: str = "stock",
        table_prefix: str | None = None,
        include_after_hours: bool = True,
    ) -> dict[str, Any]:
        if not symbols:
            raise ValueError("symbols must contain at least one symbol.")
        summaries: list[dict[str, Any]] = []
        loaded_tables: list[dict[str, Any]] = []
        warnings: list[str] = []
        for symbol in symbols:
            try:
                table_name = None
                if table_prefix:
                    table_name = f"{self._slugify(table_prefix)}_{self._slugify(str(symbol))}"
                info = self.load_history_table(
                    symbol=str(symbol),
                    length=length,
                    timestep=timestep,
                    table_name=table_name,
                    asset_type=asset_type,
                    include_after_hours=include_after_hours,
                )
            except Exception as exc:
                warnings.append(f"{symbol}: {exc}")
                continue
            summary = info.get("computed_summary")
            if isinstance(summary, dict):
                summaries.append(summary)
            else:
                warnings.append(f"{symbol}: history loaded but computed_summary was unavailable")
            loaded_tables.append(
                {
                    "symbol": info.get("symbol", symbol),
                    "table_name": info.get("table_name"),
                    "row_count": info.get("row_count"),
                    "columns": info.get("columns", []),
                }
            )
        as_of = self.strategy.get_datetime().isoformat() if hasattr(self.strategy, "get_datetime") else None
        payload = build_universe_history_summary(
            summaries=summaries,
            symbols=[str(symbol) for symbol in symbols],
            timestep=timestep,
            length=length,
            as_of=as_of,
            loaded_tables=loaded_tables,
            warnings=warnings,
        )
        payload["available_tables"] = self._available_table_schemas()
        return payload
```

- [ ] **Step 4: Add built-in tool binder**

In `builtins.py`, add a binder after `_bind_load_history`:

```python
def _bind_load_history_tables_summary(strategy: Any, manager: Any) -> BoundTool:
    def load_history_tables_summary(
        *,
        symbols: list[str],
        length: int = 252,
        timestep: str = "day",
        asset_type: AssetTypeArg = "stock",
        table_prefix: str | None = None,
        include_after_hours: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(symbols, list) or not symbols:
            raise ValueError("symbols must be a non-empty list of symbol strings.")
        clean_symbols = [_require_single_symbol_text("symbols", symbol) for symbol in symbols]
        length = _require_positive_int("length", length)
        timestep = _require_non_empty_text("timestep", timestep)
        return manager.duckdb.load_history_tables_summary(
            symbols=clean_symbols,
            length=length,
            timestep=timestep,
            asset_type=asset_type,
            table_prefix=table_prefix,
            include_after_hours=include_after_hours,
        )

    return BoundTool(
        name="market_load_history_tables_summary",
        description=(
            "Load visible historical bars for multiple symbols and return a cross-symbol summary and factual "
            "rankings. Use this when comparing a universe of assets by recent returns, moving averages, trend "
            "alignment, drawdown, volatility, or range position. Prefer this tool before writing DuckDB SQL for "
            "common universe ranking."
        ),
        function=load_history_tables_summary,
        metadata={"kind": "builtin", "replay_on_cache": True},
    )
```

In `_MarketTools`, add:

```python
    def load_history_tables_summary(self) -> ToolDefinition:
        return ToolDefinition(
            name="market_load_history_tables_summary",
            description="Load visible historical bars for multiple symbols and return factual rankings.",
            binder=_bind_load_history_tables_summary,
        )
```

In `_BuiltinTools.all()`, add the new tool immediately after `self.market.load_history_table()`:

```python
            self.market.load_history_tables_summary(),
```

- [ ] **Step 5: Verify Task 3**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_duckdb_history_tables_summary_returns_rankings_and_queryable_tables tests\backtest\test_agent_runtime_backtest.py::test_builtin_market_history_tables_summary_rejects_empty_symbols -q
.\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\duckdb_tools.py lumibot\components\agents\builtins.py
```

If full-file ruff on `builtins.py` reports unrelated pre-existing line-length issues, rerun:

```powershell
.\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\duckdb_tools.py
```

Then inspect the `builtins.py` diff manually and ensure newly added lines are below 120 characters.

- [ ] **Step 6: Commit Task 3**

```powershell
git add lumibot\components\agents\duckdb_tools.py lumibot\components\agents\builtins.py tests\backtest\test_agent_runtime_backtest.py
git commit -m "feat: add market universe history summary tool"
```

---

## Task 4: Update Prompt And Tool Description Priority

**Files:**
- Modify: `tests/test_agent_manager.py`
- Modify: `tests/backtest/test_agent_runtime_backtest.py`
- Modify: `lumibot/components/agents/manager.py` or the file that generates the general tool-use guidance
- Modify: `lumibot/components/agents/builtins.py`

- [ ] **Step 1: Add failing prompt priority assertions**

In `tests/test_agent_manager.py`, add:

```python
def test_system_prompt_prefers_computed_summaries_before_duckdb_query():
    handle = AgentHandle(
        manager=DummyManager(),
        name="research_agent",
        system_prompt="Agent-specific objective.",
        default_model="test-model",
        runtime=object(),
    )
    bound_tools = [
        _bound_tool("market_load_history_table"),
        _bound_tool("market_load_history_tables_summary"),
        _bound_tool("duckdb_query"),
    ]

    prompt = handle._compose_system_prompt({"mode": "backtesting"}, bound_tools)

    assert "Use DuckDB for time-series analysis when historical tables are available" not in prompt
    assert "Use computed summaries from market_load_history_table or market_load_history_tables_summary first" in prompt
    assert "Use duckdb_query only when the needed comparison or statistic is not already available" in prompt
```

In `tests/backtest/test_agent_runtime_backtest.py`, update the tool description test to include:

```python
    batch_tool = BuiltinTools.market.load_history_tables_summary().binder(strategy, strategy.agents)
    assert "computed_summary" in history_tool.description
    assert "Use duckdb_query only when the needed comparison or statistic is not already available" in history_tool.description
    assert "market_load_history_tables_summary" in [tool.name for tool in BuiltinTools.all()]
    assert "cross-symbol summary" in batch_tool.description
    assert "Prefer this tool before writing DuckDB SQL" in batch_tool.description
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py::test_system_prompt_prefers_computed_summaries_before_duckdb_query tests\backtest\test_agent_runtime_backtest.py::test_builtin_market_history_and_duckdb_descriptions_include_schema_hints -q
```

Expected: FAIL until prompt/guidance strings are updated.

- [ ] **Step 3: Replace generic DuckDB guidance**

Find the code that emits:

```text
Use DuckDB for time-series analysis when historical tables are available.
```

Replace it with:

```text
Use computed summaries from market_load_history_table or market_load_history_tables_summary first. Use duckdb_query only when the needed comparison or statistic is not already available in tool results.
```

Keep the replacement as a short general rule. Do not add SQL examples here.

- [ ] **Step 4: Update `market_load_history_table` description**

In `_bind_load_history`, change the relevant description sentence to include:

```text
The result includes computed_summary with standard factual statistics. Read computed_summary first. Use duckdb_query only when the needed comparison or statistic is not already available in tool results.
```

Do not remove existing schema-safety text about exact column names and `Date`.

- [ ] **Step 5: Verify Task 4**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py tests\backtest\test_agent_runtime_backtest.py::test_builtin_market_history_and_duckdb_descriptions_include_schema_hints -q
```

Expected: prompt and tool-description tests pass.

- [ ] **Step 6: Commit Task 4**

```powershell
git add lumibot\components\agents\manager.py lumibot\components\agents\builtins.py tests\test_agent_manager.py tests\backtest\test_agent_runtime_backtest.py
git commit -m "docs: prefer computed summaries before duckdb queries"
```

If the guidance lives somewhere other than `manager.py`, stage that actual file instead of `manager.py`.

---

## Task 5: Update Replay UI Formatters

**Files:**
- Modify: `tests/test_agent_replay_ui_formatters.py`
- Modify: `lumibot/components/agents/replay_ui/formatters.py`

- [ ] **Step 1: Add failing formatter tests**

Update `test_market_load_history_table_formatter_includes_computed_summary` to include new fields:

```python
                "momentum": {
                    "return_20": 0.034,
                    "return_21": 0.032,
                    "return_60": -0.021,
                    "return_63": -0.019,
                    "return_120": None,
                    "return_126": 0.052,
                    "return_252": 0.103,
                },
                "scores": {"momentum_composite": 0.0217, "trend_alignment": 2},
```

Add assertions:

```python
    assert "21-bar return 3.20%" in text
    assert "63-bar return -1.90%" in text
    assert "126-bar return 5.20%" in text
    assert "252-bar return 10.30%" in text
    assert "momentum composite 2.17%" in text
    assert "trend alignment 2" in text
```

Add a new batch formatter test:

```python
def test_market_load_history_tables_summary_formatter_shows_rankings_and_warnings():
    text = explain_tool_result(
        "market_load_history_tables_summary",
        {"symbols": ["SPY", "QQQ", "VNQ"]},
        {
            "symbols": ["SPY", "QQQ", "VNQ"],
            "universe_summary": [{"symbol": "SPY"}, {"symbol": "QQQ"}, {"symbol": "VNQ"}],
            "rankings": {
                "by_return_63": ["VNQ", "SPY", "QQQ"],
                "by_momentum_composite": ["VNQ", "QQQ", "SPY"],
            },
            "warnings": ["QQQ: demo warning"],
        },
        None,
    )

    assert "3 symbols" in text
    assert "return_63: VNQ, SPY, QQQ" in text
    assert "momentum_composite: VNQ, QQQ, SPY" in text
    assert "1 warning" in text
```

- [ ] **Step 2: Run formatter tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_formatters.py -q
```

Expected: FAIL until formatter renders new fields and new tool.

- [ ] **Step 3: Update `_computed_history_summary`**

In `formatters.py`, expand the momentum loop:

```python
    for label, key in (
        ("20-bar return", "return_20"),
        ("21-bar return", "return_21"),
        ("60-bar return", "return_60"),
        ("63-bar return", "return_63"),
        ("120-bar return", "return_120"),
        ("126-bar return", "return_126"),
        ("252-bar return", "return_252"),
    ):
```

After risk fields, add score rendering:

```python
    scores = _as_dict(summary.get("scores"))
    value = _percent(scores.get("momentum_composite"))
    if value is not None:
        parts.append(f"momentum composite {value}")
    alignment = scores.get("trend_alignment")
    if isinstance(alignment, int | float) and not isinstance(alignment, bool):
        parts.append(f"trend alignment {_text(alignment)}")
```

- [ ] **Step 4: Add batch formatter**

Add:

```python
def _market_load_history_tables_summary(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    rows = _collection(raw_result, "universe_summary")
    count = len(rows) if rows else len(result.get("symbols", [])) if isinstance(result.get("symbols"), list) else None
    rankings = _as_dict(result.get("rankings"))
    parts = [f"Loaded universe history summary for {_rows_label(count, 'symbol')}."]
    for label, key in (("return_63", "by_return_63"), ("momentum_composite", "by_momentum_composite")):
        symbols = rankings.get(key)
        if isinstance(symbols, list) and symbols:
            parts.append(f"Top {label}: {', '.join(str(symbol) for symbol in symbols[:5])}.")
    warnings = result.get("warnings")
    if isinstance(warnings, list) and warnings:
        parts.append(f"{_rows_label(len(warnings), 'warning').capitalize()} recorded.")
    return " ".join(parts)
```

Register it in `_FORMATTERS`:

```python
    "market_load_history_tables_summary": _market_load_history_tables_summary,
```

- [ ] **Step 5: Verify Task 5**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_formatters.py -q
.\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\replay_ui\formatters.py tests\test_agent_replay_ui_formatters.py
```

Expected: all formatter tests pass; ruff passes.

- [ ] **Step 6: Commit Task 5**

```powershell
git add lumibot\components\agents\replay_ui\formatters.py tests\test_agent_replay_ui_formatters.py
git commit -m "feat: show universe history summaries in replay UI"
```

---

## Task 6: Focused Verification And One-Day Backtest Smoke Test

**Files:**
- No production code changes expected.
- Optional note file if the run needs a short investigation record.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py tests\test_agent_replay_ui_formatters.py tests\test_agent_manager.py tests\backtest\test_agent_runtime_backtest.py::test_duckdb_table_inventory_tracks_fresh_and_cached_history_tables tests\backtest\test_agent_runtime_backtest.py::test_duckdb_history_tables_summary_returns_rankings_and_queryable_tables tests\backtest\test_agent_runtime_backtest.py::test_builtin_market_history_and_duckdb_descriptions_include_schema_hints -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run targeted ruff checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\history_summary.py lumibot\components\agents\duckdb_tools.py lumibot\components\agents\replay_ui\formatters.py tests\test_agent_history_summary.py tests\test_agent_replay_ui_formatters.py
```

Expected: all checks pass.

- [ ] **Step 3: Run one-day OpenAI backtest**

Do not print API keys. Use this PowerShell command:

```powershell
$lines = Get-Content project_notes\API.txt
$openai = ($lines | Where-Object { $_ -match 'sk-' } | Select-Object -First 1).Trim()
if ($openai -match '=') { $openai = ($openai -split '=',2)[1].Trim().Trim('"').Trim("'") }
$env:OPENAI_API_KEY = $openai
$env:GOOGLE_API_KEY = 'dummy-key-for-runner-provider-check'
$env:AI_TRADING_TEAM_MODEL = 'openai/gpt-5.4-mini'
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy growth-execution-test --start 2024-09-05 --end 2024-09-06 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800
```

Expected:

- Benchmark status is `passed`.
- A new artifact directory is printed under `artifacts\ai_trading_team_example_benchmarks\...`.

- [ ] **Step 4: Inspect the trace for new behavior**

Replace `<RUN_ID>` with the new run id printed by the benchmark:

```powershell
@'
import json
from pathlib import Path

root = Path(r"artifacts/ai_trading_team_example_benchmarks/<RUN_ID>/growth-execution-test/cache/agent_runtime/traces")
batch_calls = 0
history_calls = 0
duckdb_calls = 0
for path in root.rglob("*.json"):
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data.get("tool_results", []):
        name = item.get("tool_name")
        if name == "market_load_history_tables_summary":
            batch_calls += 1
        elif name == "market_load_history_table":
            history_calls += 1
        elif name == "duckdb_query":
            duckdb_calls += 1
print({"batch_summary_calls": batch_calls, "history_table_calls": history_calls, "duckdb_query_calls": duckdb_calls})
'@ | .\.venv\Scripts\python.exe -
```

Expected:

- `market_load_history_tables_summary` is available in tool definitions.
- It is acceptable if the LLM still chooses not to call the new tool in the first smoke run.
- If the LLM writes SQL, record whether it did so after batch summary was available and whether the SQL requested a metric not in the new summary payload.

- [ ] **Step 5: Optional UI check**

Start the replay UI:

```powershell
.\.venv\Scripts\python.exe scripts\agent_trace_ui.py
```

Open the local URL it prints and select the new benchmark run. Verify that:

- `market_load_history_table` outputs show expanded summary fields when present.
- `market_load_history_tables_summary` outputs show ranking text if the tool was called.

- [ ] **Step 6: Commit any verification notes only if created**

If a verification note was written, commit only that note:

```powershell
git add docs\superpowers\notes\<note-file>.md
git commit -m "docs: record duckdb query reduction smoke test"
```

Do not commit generated benchmark artifacts unless the user explicitly asks to save them.

---

## Self-Review Checklist

- Spec coverage:
  - Prompt de-emphasis is covered in Task 4.
  - Single-symbol 21/63/126/252 windows are covered in Task 1.
  - Cross-symbol ranking tool is covered in Tasks 2 and 3.
  - Replay UI support is covered in Task 5.
  - Real trace validation is covered in Task 6.
- Placeholder scan:
  - No unresolved markers or unspecified implementation steps.
- Type consistency:
  - New tool name is consistently `market_load_history_tables_summary`.
  - Batch payload keys are consistently `universe_summary`, `rankings`, `loaded_tables`, and `warnings`.
  - New score keys are consistently `momentum_composite` and `trend_alignment`.
