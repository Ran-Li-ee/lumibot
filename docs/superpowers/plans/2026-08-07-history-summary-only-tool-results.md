# History Summary-Only Tool Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make historical market-data tools send compact computed summaries to LLMs while keeping raw OHLCV rows queryable in in-memory DuckDB.

**Architecture:** Extend `history_summary.py` with additional deterministic statistics and rankings, keep `duckdb_tools.py` loading raw frames into DuckDB RAM, and adjust tool descriptions / replay formatters so agents and the UI treat computed summaries as primary evidence. Validation combines unit tests, trace inspection, and a one-day `gpt-5.4-mini` backtest.

**Tech Stack:** Python, pandas, DuckDB in-memory connection, pytest, LumiBot agent runtime/tools, Agent Workflow Replay UI formatters.

---

## File Map

- Modify `lumibot/components/agents/history_summary.py`
  - Owns single-symbol `computed_summary` and multi-symbol universe ranking.
  - Add short-term momentum, volume, recent-high drawdowns, risk-adjusted momentum, and composite score.

- Modify `lumibot/components/agents/duckdb_tools.py`
  - Owns loading raw history frames into in-memory DuckDB and returning model-facing metadata.
  - Ensure no full raw rows are returned by `load_history_table`; keep queryability through `duckdb_query`.

- Modify `lumibot/components/agents/builtins.py`
  - Owns tool definitions and model-facing descriptions.
  - Update history tool descriptions to emphasize summary-first and targeted DuckDB follow-up.

- Modify `lumibot/components/agents/manager.py`
  - Owns base system prompt and computed-summary guidance injected by agent manager.
  - Lightly adjust prompt wording from raw-history inspection toward computed summaries.

- Modify `lumibot/components/agents/runtime.py`
  - Owns runtime instruction construction for Google ADK.
  - Keep or adjust summary-first guidance consistently with manager prompt.

- Modify `lumibot/components/agents/replay_ui/formatters.py`
  - Owns human-readable explanations in Agent Workflow Replay UI.
  - Surface new summary fields without making the UI noisy.

- Modify `tests/backtest/test_agent_runtime_backtest.py`
  - Add integration-style tests for summary fields, rankings, and DuckDB queryability.

- Modify `tests/test_agent_replay_ui_formatters.py`
  - Add formatter tests for the new fields and ranking.

- Modify `tests/test_agent_manager.py`
  - Update prompt/instruction tests for summary-first wording.

---

## Task 1: Add Summary Metrics Tests

**Files:**
- Modify: `tests/backtest/test_agent_runtime_backtest.py`
- Test target: `lumibot/components/agents/history_summary.py`

- [ ] **Step 1: Add a direct unit test for new single-symbol summary fields**

Append this test near the existing DuckDB/history summary tests in `tests/backtest/test_agent_runtime_backtest.py`:

```python
def test_compute_history_summary_includes_extended_summary_metrics():
    from lumibot.components.agents.history_summary import compute_history_summary

    index = pd.date_range("2024-01-01", periods=260, freq="D", tz="America/New_York")
    close = pd.Series([100 + i * 0.5 for i in range(260)], index=index)
    frame = pd.DataFrame(
        {
            "Date": index,
            "open": close - 0.2,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": [1_000_000 + i * 1_000 for i in range(260)],
        }
    )

    summary = compute_history_summary(
        frame,
        symbol="SPY",
        timestep="day",
        as_of="2024-09-16T16:00:00-04:00",
    )

    assert summary["momentum"]["return_5"] is not None
    assert summary["momentum"]["return_10"] is not None
    assert summary["volume"]["latest_volume"] == 1_259_000.0
    assert summary["volume"]["avg_volume_20"] is not None
    assert summary["volume"]["volume_vs_avg_20"] is not None
    assert summary["range"]["drawdown_from_high_20"] is not None
    assert summary["range"]["drawdown_from_high_60"] is not None
    assert summary["range"]["drawdown_from_high_252"] is not None
    assert summary["scores"]["return_63_over_volatility_20"] is not None
    assert summary["scores"]["return_126_over_volatility_20"] is not None
    assert summary["scores"]["composite_score"] is not None
    assert summary["availability"]["return_5"] is True
    assert summary["availability"]["latest_volume"] is True
    assert summary["availability"]["drawdown_from_high_20"] is True
    assert summary["availability"]["composite_score"] is True
```

- [ ] **Step 2: Add an insufficient-data test**

Append this test below the previous one:

```python
def test_compute_history_summary_marks_extended_metrics_unavailable_when_data_is_short():
    from lumibot.components.agents.history_summary import compute_history_summary

    frame = pd.DataFrame(
        {
            "Date": pd.date_range("2024-01-01", periods=4, freq="D", tz="America/New_York"),
            "open": [100.0, 101.0, 102.0, 103.0],
            "high": [101.0, 102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0, 102.0],
            "close": [100.0, 101.0, 102.0, 103.0],
            "volume": [1000, 1100, 1200, 1300],
        }
    )

    summary = compute_history_summary(
        frame,
        symbol="SPY",
        timestep="day",
        as_of="2024-01-04T16:00:00-04:00",
    )

    assert summary["momentum"]["return_5"] is None
    assert summary["momentum"]["return_10"] is None
    assert summary["volume"]["avg_volume_20"] is None
    assert summary["volume"]["volume_vs_avg_20"] is None
    assert summary["scores"]["return_63_over_volatility_20"] is None
    assert summary["scores"]["return_126_over_volatility_20"] is None
    assert summary["scores"]["composite_score"] is None
    assert summary["availability"]["return_5"] is False
    assert summary["availability"]["avg_volume_20"] is False
    assert summary["availability"]["composite_score"] is False
```

- [ ] **Step 3: Run the new tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_compute_history_summary_includes_extended_summary_metrics tests\backtest\test_agent_runtime_backtest.py::test_compute_history_summary_marks_extended_metrics_unavailable_when_data_is_short -q
```

Expected: both tests fail because the new fields do not exist yet.

---

## Task 2: Implement Extended Summary Metrics

**Files:**
- Modify: `lumibot/components/agents/history_summary.py`
- Test: `tests/backtest/test_agent_runtime_backtest.py`

- [ ] **Step 1: Add new metric calculations in `compute_history_summary`**

In `lumibot/components/agents/history_summary.py`, update the top of `compute_history_summary` after `low = _numeric_series(data, "low")`:

```python
    volume = _numeric_series(data, "volume")
```

Replace the `momentum = { ... }` block with:

```python
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
    }
```

After `volatility_20 = _volatility(close, 20)`, add:

```python
    volume_summary = {
        "latest_volume": _last_value(volume),
        "avg_volume_20": _sma(volume, 20),
    }
    volume_summary["volume_vs_avg_20"] = _relative_to(
        volume_summary["latest_volume"],
        volume_summary["avg_volume_20"],
    )
    drawdown_from_high_20 = _drawdown_from_high(close, 20)
    drawdown_from_high_60 = _drawdown_from_high(close, 60)
    drawdown_from_high_252 = _drawdown_from_high(close, 252)
    risk_adjusted = {
        "return_63_over_volatility_20": _ratio(momentum["return_63"], volatility_20),
        "return_126_over_volatility_20": _ratio(momentum["return_126"], volatility_20),
    }
    composite_score = _composite_score(
        momentum_63=momentum["return_63"],
        momentum_126=momentum["return_126"],
        trend_alignment=scores["trend_alignment"],
        drawdown_from_high_60=drawdown_from_high_60,
        risk_adjusted_63=risk_adjusted["return_63_over_volatility_20"],
        risk_adjusted_126=risk_adjusted["return_126_over_volatility_20"],
    )
    scores.update(risk_adjusted)
    scores["composite_score"] = composite_score
```

- [ ] **Step 2: Add availability flags and returned sections**

In the `availability = { ... }` block, add:

```python
        "return_5": momentum["return_5"] is not None,
        "return_10": momentum["return_10"] is not None,
        "latest_volume": volume_summary["latest_volume"] is not None,
        "avg_volume_20": volume_summary["avg_volume_20"] is not None,
        "volume_vs_avg_20": volume_summary["volume_vs_avg_20"] is not None,
        "drawdown_from_high_20": drawdown_from_high_20 is not None,
        "drawdown_from_high_60": drawdown_from_high_60 is not None,
        "drawdown_from_high_252": drawdown_from_high_252 is not None,
        "return_63_over_volatility_20": risk_adjusted["return_63_over_volatility_20"] is not None,
        "return_126_over_volatility_20": risk_adjusted["return_126_over_volatility_20"] is not None,
        "composite_score": composite_score is not None,
```

In the returned dictionary, add `"volume": volume_summary,` after the `"momentum": momentum,` line.

In the `"range": { ... }` block, add:

```python
            "drawdown_from_high_20": drawdown_from_high_20,
            "drawdown_from_high_60": drawdown_from_high_60,
            "drawdown_from_high_252": drawdown_from_high_252,
```

- [ ] **Step 3: Add helper functions**

Append these helpers below `_volatility`:

```python
def _drawdown_from_high(series: pd.Series | None, window: int) -> float | None:
    if series is None or series.empty:
        return None
    values = series.tail(min(window, len(series))).dropna()
    values = values[values.map(lambda value: _finite_float(value) is not None)]
    if values.empty:
        return None
    latest = _finite_float(values.iloc[-1])
    high = _finite_float(values.max())
    return _relative_to(latest, high)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    numerator = _finite_float(numerator)
    denominator = _finite_float(denominator)
    if numerator is None or denominator in (None, 0):
        return None
    return _finite_float(numerator / denominator)


def _composite_score(
    *,
    momentum_63: float | None,
    momentum_126: float | None,
    trend_alignment: int | None,
    drawdown_from_high_60: float | None,
    risk_adjusted_63: float | None,
    risk_adjusted_126: float | None,
) -> float | None:
    components: list[float] = []
    for value in (momentum_63, momentum_126):
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
```

- [ ] **Step 4: Run the new summary tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_compute_history_summary_includes_extended_summary_metrics tests\backtest\test_agent_runtime_backtest.py::test_compute_history_summary_marks_extended_metrics_unavailable_when_data_is_short -q
```

Expected: both tests pass.

---

## Task 3: Add Universe Summary Composite Ranking Tests

**Files:**
- Modify: `tests/backtest/test_agent_runtime_backtest.py`
- Test target: `lumibot/components/agents/history_summary.py`

- [ ] **Step 1: Extend the existing universe summary test**

In `test_duckdb_history_tables_summary_returns_rankings_and_queryable_tables`, after:

```python
    assert "by_momentum_composite" in summary["rankings"]
```

add:

```python
    assert "by_composite_score" in summary["rankings"]
    assert summary["rankings"]["by_composite_score"]
    for row in summary["universe_summary"]:
        assert "composite_score" in row
        assert "return_5" in row
        assert "return_10" in row
        assert "volume_vs_avg_20" in row
        assert "drawdown_from_high_60" in row
        assert "return_63_over_volatility_20" in row
```

- [ ] **Step 2: Run the targeted test and verify it fails before implementation**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_duckdb_history_tables_summary_returns_rankings_and_queryable_tables -q
```

Expected: fail because `by_composite_score` and row fields are not yet exposed in universe rows.

---

## Task 4: Expose New Fields In Universe Rows And Rankings

**Files:**
- Modify: `lumibot/components/agents/history_summary.py`
- Test: `tests/backtest/test_agent_runtime_backtest.py`

- [ ] **Step 1: Update `_summary_to_universe_row`**

In `lumibot/components/agents/history_summary.py`, add:

```python
    volume = _dict(summary.get("volume"))
```

after the existing `momentum = _dict(...)` line.

Then update the returned row dictionary to include:

```python
        "return_5": _finite_or_none(momentum.get("return_5")),
        "return_10": _finite_or_none(momentum.get("return_10")),
        "latest_volume": _finite_or_none(volume.get("latest_volume")),
        "avg_volume_20": _finite_or_none(volume.get("avg_volume_20")),
        "volume_vs_avg_20": _finite_or_none(volume.get("volume_vs_avg_20")),
        "drawdown_from_high_20": _finite_or_none(range_metrics.get("drawdown_from_high_20")),
        "drawdown_from_high_60": _finite_or_none(range_metrics.get("drawdown_from_high_60")),
        "drawdown_from_high_252": _finite_or_none(range_metrics.get("drawdown_from_high_252")),
        "return_63_over_volatility_20": _finite_or_none(scores.get("return_63_over_volatility_20")),
        "return_126_over_volatility_20": _finite_or_none(scores.get("return_126_over_volatility_20")),
        "composite_score": _finite_or_none(scores.get("composite_score")),
```

Keep existing fields; do not remove current row fields.

- [ ] **Step 2: Update `_rankings`**

Add this entry to the returned rankings dictionary:

```python
        "by_composite_score": _rank_symbols(rows, "composite_score"),
```

- [ ] **Step 3: Run universe summary tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_duckdb_history_tables_summary_returns_rankings_and_queryable_tables -q
```

Expected: pass.

---

## Task 5: Verify Raw Rows Stay In DuckDB But Not In Tool Return

**Files:**
- Modify: `tests/backtest/test_agent_runtime_backtest.py`
- Test target: `lumibot/components/agents/duckdb_tools.py`

- [ ] **Step 1: Add explicit no-raw-rows assertion to existing table inventory test**

In `test_duckdb_table_inventory_tracks_fresh_and_cached_history_tables`, after the first `load_history_table` call and before `second = ...`, add:

```python
    assert "rows" not in first
    assert "data" not in first
    assert "records" not in first
```

After:

```python
    row_count = strategy.agents.duckdb.query(sql="SELECT COUNT(*) AS count_rows FROM z_history")
```

add this query if not already present:

```python
    close_query = strategy.agents.duckdb.query(sql="SELECT close FROM z_history ORDER BY 1 LIMIT 1")
    assert close_query["row_count"] == 1
    assert "close" in close_query["columns"]
```

If the test does not currently include `row_count` for `z_history`, add:

```python
    row_count = strategy.agents.duckdb.query(sql="SELECT COUNT(*) AS count_rows FROM z_history")
    assert row_count["rows"] == [{"count_rows": 3}]
```

- [ ] **Step 2: Run the targeted DuckDB table test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_duckdb_table_inventory_tracks_fresh_and_cached_history_tables -q
```

Expected: pass if current implementation already avoids raw rows; fail if raw rows are present.

- [ ] **Step 3: If the test fails because raw rows are returned, remove raw rows from `load_history_table` result**

In `lumibot/components/agents/duckdb_tools.py`, keep:

```python
        result = dict(info)
        result["available_tables"] = self._available_table_schemas()
        return result
```

Ensure no code adds keys named `rows`, `data`, `records`, `frame`, or `raw_rows` to `info` or `result`.

- [ ] **Step 4: Re-run the targeted test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_duckdb_table_inventory_tracks_fresh_and_cached_history_tables -q
```

Expected: pass.

---

## Task 6: Update Tool Descriptions And Prompt Guidance Tests

**Files:**
- Modify: `tests/backtest/test_agent_runtime_backtest.py`
- Modify: `tests/test_agent_manager.py`
- Test targets: `lumibot/components/agents/builtins.py`, `lumibot/components/agents/manager.py`, `lumibot/components/agents/runtime.py`

- [ ] **Step 1: Update tool-description test expectations**

In `test_builtin_market_history_and_duckdb_descriptions_include_schema_hints`, add assertions:

```python
    assert "summary-first" in history_tool.description.lower()
    assert "does not return full raw historical rows" in history_tool.description.lower()
    assert "raw rows remain queryable in DuckDB" in history_tool.description
    assert "by_composite_score" in batch_tool.description
```

- [ ] **Step 2: Update prompt guidance tests**

In `tests/test_agent_manager.py`, update `test_system_prompt_prefers_computed_summaries_before_duckdb_query` to assert:

```python
    assert "Use computed summaries from market_load_history_table or market_load_history_tables_summary as the primary evidence for price history" in prompt
    assert "Do not request raw historical rows by default" in prompt
    assert "Use duckdb_query only for targeted follow-up analysis" in prompt
```

In `test_runtime_instruction_prefers_computed_summaries_before_duckdb_query`, assert the same three strings against `instruction`.

In `test_runtime_instruction_omits_computed_summary_guidance_without_matching_tools`, update the absent-string assertion to:

```python
    assert "Use computed summaries from market_load_history_table or market_load_history_tables_summary as the primary evidence" not in instruction
```

- [ ] **Step 3: Run tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_builtin_market_history_and_duckdb_descriptions_include_schema_hints tests\test_agent_manager.py::test_system_prompt_prefers_computed_summaries_before_duckdb_query tests\test_agent_manager.py::test_runtime_instruction_prefers_computed_summaries_before_duckdb_query tests\test_agent_manager.py::test_runtime_instruction_omits_computed_summary_guidance_without_matching_tools -q
```

Expected: fail until descriptions and guidance are updated.

---

## Task 7: Update Tool Descriptions And Runtime Guidance

**Files:**
- Modify: `lumibot/components/agents/builtins.py`
- Modify: `lumibot/components/agents/manager.py`
- Modify: `lumibot/components/agents/runtime.py`
- Test: `tests/backtest/test_agent_runtime_backtest.py`, `tests/test_agent_manager.py`

- [ ] **Step 1: Update `market_load_history_table` description**

In `lumibot/components/agents/builtins.py`, find the `BoundTool` description for `market_load_history_table`.

Replace the sentence:

```python
            "The computed_summary result field already includes common price, momentum, trend, "
            "range, and risk statistics; "
            "read it first before writing SQL for common history analysis. "
```

with:

```python
            "This is a summary-first tool: it returns metadata and computed_summary for model use, "
            "and does not return full raw historical rows by default. "
            "Raw rows remain queryable in DuckDB through the returned table_name. "
            "The computed_summary result field already includes common price, momentum, volume, trend, "
            "range, risk, and composite-score statistics; "
            "read it first before writing SQL for common history analysis. "
```

- [ ] **Step 2: Update `market_load_history_tables_summary` description**

In the `market_load_history_tables_summary` description, replace:

```python
            "This tool returns factual rankings plus recent returns, moving averages, trend alignment, drawdown, "
            "volatility, and range position for the requested universe. "
```

with:

```python
            "This summary-first tool returns factual rankings, including by_composite_score, plus recent returns, "
            "moving averages, trend alignment, drawdown, volatility, volume context, and range position "
            "for the requested universe. "
```

- [ ] **Step 3: Update manager prompt guidance**

In `lumibot/components/agents/manager.py`, find the computed-summary guidance string added when history and DuckDB tools are available. Replace the guidance lines with:

```python
                        "Use computed summaries from market_load_history_table or "
                        "market_load_history_tables_summary as the primary evidence for price history. "
                        "Do not request raw historical rows by default. "
                        "Use duckdb_query only for targeted follow-up analysis not already covered "
                        "by computed_summary or rankings. "
```

Also in `_base_system_prompt`, replace:

```python
            "Load recent price history for any asset you are considering and inspect it before deciding.",
```

with:

```python
            "Load recent price history for any asset you are considering and use the computed summary before deciding.",
```

- [ ] **Step 4: Update runtime instruction guidance**

In `lumibot/components/agents/runtime.py`, find the matching guidance around `market_load_history_table`, `market_load_history_tables_summary`, and `duckdb_query`.

Replace the current computed-summary guidance with:

```python
                    "- Use computed summaries from market_load_history_table or "
                    "market_load_history_tables_summary as the primary evidence for price history. "
                    "Do not request raw historical rows by default. "
                    "Use duckdb_query only for targeted follow-up analysis not already covered "
                    "by computed_summary or rankings.\n"
```

- [ ] **Step 5: Run prompt and description tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_builtin_market_history_and_duckdb_descriptions_include_schema_hints tests\test_agent_manager.py::test_system_prompt_prefers_computed_summaries_before_duckdb_query tests\test_agent_manager.py::test_runtime_instruction_prefers_computed_summaries_before_duckdb_query tests\test_agent_manager.py::test_runtime_instruction_omits_computed_summary_guidance_without_matching_tools -q
```

Expected: pass.

---

## Task 8: Update Replay UI Formatter Tests

**Files:**
- Modify: `tests/test_agent_replay_ui_formatters.py`
- Test target: `lumibot/components/agents/replay_ui/formatters.py`

- [ ] **Step 1: Extend single-table formatter test payload**

In `test_market_load_history_table_formatter_includes_computed_summary`, update `computed_summary` to include:

```python
                "volume": {
                    "latest_volume": 1000000,
                    "avg_volume_20": 900000,
                    "volume_vs_avg_20": 0.1111,
                },
```

Add to `momentum`:

```python
                    "return_5": 0.011,
                    "return_10": 0.022,
```

Add to `range`:

```python
                    "drawdown_from_high_20": -0.012,
                    "drawdown_from_high_60": -0.033,
                    "drawdown_from_high_252": -0.044,
```

Add to `scores`:

```python
                    "return_63_over_volatility_20": -1.727,
                    "return_126_over_volatility_20": 4.727,
                    "composite_score": 0.42,
```

Then add assertions:

```python
    assert "5-bar return 1.10%" in text
    assert "10-bar return 2.20%" in text
    assert "volume vs avg20 11.11%" in text
    assert "drawdown from 20-bar high -1.20%" in text
    assert "risk-adjusted 63 -1.73" in text
    assert "composite score 0.42" in text
```

- [ ] **Step 2: Extend multi-symbol formatter test**

In `test_market_load_history_tables_summary_formatter_shows_rankings_and_warnings`, add:

```python
                "by_composite_score": ["SPY", "VNQ", "QQQ"],
```

to the `rankings` dictionary.

Add assertion:

```python
    assert "composite_score: SPY, VNQ, QQQ" in text
```

- [ ] **Step 3: Run formatter tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_formatters.py::test_market_load_history_table_formatter_includes_computed_summary tests\test_agent_replay_ui_formatters.py::test_market_load_history_tables_summary_formatter_shows_rankings_and_warnings -q
```

Expected: fail until formatter is updated.

---

## Task 9: Update Replay UI Formatters

**Files:**
- Modify: `lumibot/components/agents/replay_ui/formatters.py`
- Test: `tests/test_agent_replay_ui_formatters.py`

- [ ] **Step 1: Include volume and new score sections**

In `_computed_history_summary`, add:

```python
    volume = _as_dict(summary.get("volume"))
```

after the `momentum = ...` line.

Add `return_5` and `return_10` to the momentum loop before `return_20`:

```python
        ("5-bar return", "return_5"),
        ("10-bar return", "return_10"),
```

After trend alignment handling, add:

```python
    value = _number(scores.get("composite_score"))
    if value is not None:
        parts.append(f"composite score {value}")
```

After the SMA/trend section, add:

```python
    latest_volume = _number(volume.get("latest_volume"))
    if latest_volume is not None:
        parts.append(f"latest volume {latest_volume}")
    value = _number(volume.get("avg_volume_20"))
    if value is not None:
        parts.append(f"avg volume 20 {value}")
    value = _percent(volume.get("volume_vs_avg_20"))
    if value is not None:
        parts.append(f"volume vs avg20 {value}")
```

After existing range high/low lines, add:

```python
    for label, key in (
        ("drawdown from 20-bar high", "drawdown_from_high_20"),
        ("drawdown from 60-bar high", "drawdown_from_high_60"),
        ("drawdown from 252-bar high", "drawdown_from_high_252"),
    ):
        value = _percent(range_summary.get(key))
        if value is not None:
            parts.append(f"{label} {value}")
```

After volatility handling, add:

```python
    value = _number(scores.get("return_63_over_volatility_20"))
    if value is not None:
        parts.append(f"risk-adjusted 63 {value}")
    value = _number(scores.get("return_126_over_volatility_20"))
    if value is not None:
        parts.append(f"risk-adjusted 126 {value}")
```

- [ ] **Step 2: Include composite ranking in table summary formatter**

In `_market_load_history_tables_summary`, update the ranking loop to include:

```python
        ("composite_score", "by_composite_score"),
```

before or after `momentum_composite`.

- [ ] **Step 3: Run formatter tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_formatters.py::test_market_load_history_table_formatter_includes_computed_summary tests\test_agent_replay_ui_formatters.py::test_market_load_history_tables_summary_formatter_shows_rankings_and_warnings -q
```

Expected: pass.

---

## Task 10: Run Focused Test Suite

**Files:**
- No code edits unless failures reveal a task-specific bug.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backtest\test_agent_runtime_backtest.py::test_compute_history_summary_includes_extended_summary_metrics tests\backtest\test_agent_runtime_backtest.py::test_compute_history_summary_marks_extended_metrics_unavailable_when_data_is_short tests\backtest\test_agent_runtime_backtest.py::test_duckdb_history_tables_summary_returns_rankings_and_queryable_tables tests\backtest\test_agent_runtime_backtest.py::test_duckdb_table_inventory_tracks_fresh_and_cached_history_tables tests\backtest\test_agent_runtime_backtest.py::test_builtin_market_history_and_duckdb_descriptions_include_schema_hints tests\test_agent_manager.py::test_system_prompt_prefers_computed_summaries_before_duckdb_query tests\test_agent_manager.py::test_runtime_instruction_prefers_computed_summaries_before_duckdb_query tests\test_agent_manager.py::test_runtime_instruction_omits_computed_summary_guidance_without_matching_tools tests\test_agent_replay_ui_formatters.py::test_market_load_history_table_formatter_includes_computed_summary tests\test_agent_replay_ui_formatters.py::test_market_load_history_tables_summary_formatter_shows_rankings_and_warnings -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run broader relevant suites**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py tests\test_agent_replay_ui_formatters.py tests\backtest\test_agent_runtime_backtest.py -q
```

Expected: pass. If this is too slow, record runtime and run the targeted tests plus a smaller subset covering changed files.

---

## Task 11: Run One-Day GPT-5.4-Mini Backtest

**Files:**
- No code edits unless validation exposes a bug.

- [ ] **Step 1: Run the benchmark with 5.4-mini**

Use the OpenAI key from `project_notes/API.txt` without printing it:

```powershell
$lines = Get-Content -LiteralPath project_notes\API.txt
$openai = ($lines | Where-Object { $_ -match 'sk-' } | Select-Object -First 1).Trim()
if (-not $openai) { throw 'OpenAI API key not found in project_notes/API.txt' }
$env:OPENAI_API_KEY = $openai
$env:GOOGLE_API_KEY = 'not-used-for-openai-model'
$env:AI_TRADING_TEAM_MODEL = 'openai/gpt-5.4-mini'
$env:LUMIBOT_AGENT_MODEL_REQUEST_TIMEOUT_SECONDS = '600'
.\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy growth-execution-test --start 2024-09-05 --end 2024-09-06 --max-workers 1 --agent-run-timeout-seconds 1800
```

Expected: benchmark prints a new artifact directory under `artifacts\ai_trading_team_example_benchmarks\...`.

- [ ] **Step 2: Inspect trace for summary-only behavior**

Replace `<RUN_ID>` with the run directory printed by the command:

```powershell
@'
import json, pathlib
root = pathlib.Path(r'artifacts/ai_trading_team_example_benchmarks/<RUN_ID>/growth-execution-test')
for agent in ['growth_agent', 'decision_agent', 'execution_agent']:
    trace_dir = root / 'cache' / 'agent_runtime' / 'traces' / agent
    files = list(trace_dir.glob('*.json'))
    print('\n====', agent, '====')
    if not files:
        print('no trace')
        continue
    data = json.loads(files[0].read_text(encoding='utf-8'))
    print('model:', data.get('model'))
    print('warnings:', data.get('warnings'))
    print('summary:', (data.get('final_summary') or data.get('summary') or '')[:1200])
    for event in data.get('events') or []:
        if (event.get('event_type') or event.get('kind')) != 'tool_result':
            continue
        if event.get('tool_name') not in {'market_load_history_table', 'market_load_history_tables_summary', 'duckdb_query'}:
            continue
        payload = event.get('payload') or {}
        print('tool_result:', event.get('tool_name'))
        print('has rows/data/records:', any(key in payload for key in ('rows', 'data', 'records')))
        if event.get('tool_name') == 'market_load_history_table':
            summary = payload.get('computed_summary') or {}
            print('summary keys:', sorted(summary.keys()))
            print('score keys:', sorted((summary.get('scores') or {}).keys()))
        if event.get('tool_name') == 'market_load_history_tables_summary':
            print('rankings:', sorted((payload.get('rankings') or {}).keys()))
'@ | .\.venv\Scripts\python.exe -
```

Expected:

- `market_load_history_table` payloads do not include `rows`, `data`, or `records`.
- `computed_summary.scores` includes `composite_score`.
- `market_load_history_tables_summary.rankings` includes `by_composite_score`.
- `duckdb_query` is absent, or if present it is traceable to a targeted missing statistic.

- [ ] **Step 3: Inspect trades**

Run:

```powershell
Get-Content -LiteralPath artifacts\ai_trading_team_example_benchmarks\<RUN_ID>\growth-execution-test\trades.csv
```

Expected: either a valid order lifecycle appears, or the agent trace contains a clear no-trade explanation. There should be no tool/runtime error.

---

## Task 12: Final Verification And Handoff Notes

**Files:**
- Modify only if final verification exposes missing docs or broken tests.

- [ ] **Step 1: Run git diff summary**

Run:

```powershell
git diff --stat
git diff -- lumibot\components\agents\history_summary.py lumibot\components\agents\duckdb_tools.py lumibot\components\agents\builtins.py lumibot\components\agents\manager.py lumibot\components\agents\runtime.py lumibot\components\agents\replay_ui\formatters.py
```

Expected: changes are scoped to summary metrics, prompt/tool descriptions, and UI formatter text.

- [ ] **Step 2: Run final targeted tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py tests\test_agent_replay_ui_formatters.py tests\backtest\test_agent_runtime_backtest.py -q
```

Expected: pass, or record any unrelated failures with evidence.

- [ ] **Step 3: Record the benchmark artifact path**

Create a short note in the final response with:

- New artifact directory.
- Whether `duckdb_query` was called.
- Whether raw rows were absent from model-facing history payloads.
- Whether the workflow reached execution.
- Whether an order was submitted.

Do not print API keys.

---

## Self-Review

Spec coverage:

- Summary-only model-facing behavior: Tasks 5 and 11.
- Additional summary metrics: Tasks 1 and 2.
- Cross-symbol composite ranking: Tasks 3 and 4.
- DuckDB RAM queryability: Task 5.
- Prompt/tool guidance: Tasks 6 and 7.
- UI readability: Tasks 8 and 9.
- Unit and integration validation: Tasks 10 and 11.

Placeholder scan:

- No `TODO`, `TBD`, or intentionally vague implementation steps remain.

Type consistency:

- New fields are placed in existing `momentum`, `volume`, `range`, and `scores` sections.
- `by_composite_score` follows existing ranking key naming.
- Tests use existing helper names and file paths.
