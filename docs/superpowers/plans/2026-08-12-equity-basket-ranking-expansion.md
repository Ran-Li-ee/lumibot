# Equity Basket Ranking Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the equity basket to 50 US stocks and add transparent top-ten per-metric history rankings for basket agents.

**Architecture:** Keep the existing growth/inflation quadrant workflow unchanged. Extend the existing history summary module to produce top-ten rankings, ranking details, candidate summaries, and coverage metadata; then pass the new `top_n` option through the DuckDB layer and built-in tool wrapper. Update only the mock quadrant strategy's equity universe and basket-agent prompt wording, plus lightweight replay formatting and tests.

**Tech Stack:** Python, pandas, pytest, ruff, existing LumiBot agent built-ins, existing DuckDB history summary layer, existing Agent Replay formatter, existing mock growth/inflation quadrant strategy.

---

## File Structure

- Modify `lumibot/components/agents/history_summary.py`
  - Add `return_252`, risk-adjusted, near-high, and volume-surge fields to compact universe rows.
  - Add top-N support to `build_universe_history_summary`.
  - Add `ranking_details`, `candidate_summary`, and `coverage`.
  - Preserve `universe_summary` only when explicitly requested.

- Modify `lumibot/components/agents/duckdb_tools.py`
  - Add `top_n=10` and `include_full_universe_summary=False` to `load_history_tables_summary`.
  - Pass both values into `build_universe_history_summary`.

- Modify `lumibot/components/agents/builtins.py`
  - Add `top_n` and `include_full_universe_summary` to the `market_load_history_tables_summary` tool wrapper.
  - Update the model-facing description to mention top-ten rankings, ranking details, and separate evidence views.

- Modify `lumibot/components/agents/replay_ui/formatters.py`
  - Update the `market_load_history_tables_summary` explanation so new ranking names and `coverage` are visible.
  - Prefer `candidate_summary` count when `universe_summary` is omitted.

- Modify `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
  - Replace the equity basket with 50 high-liquidity US ordinary stocks.
  - Leave commodity, TIPS, nominal bond baskets unchanged.
  - Update basket-agent prompt text to use ranking evidence without asking for a composite score.

- Modify `tests/test_agent_history_summary.py`
  - Add tests for new rankings, top-N behavior, ranking details, candidate summary, and coverage.
  - Update existing tests that currently expect full `universe_summary` by default.

- Modify `tests/test_agent_replay_ui_formatters.py`
  - Add formatter coverage for `ranking_details`, `candidate_summary`, and `coverage`.

- Modify `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
  - Update basket universe tests for the 50-stock equity basket.
  - Add prompt boundary assertions for basket-agent ranking instructions.

- Create `docs/superpowers/notes/2026-08-12-equity-basket-ranking-expansion-validation.md`
  - Record test commands, one-day backtest command, artifact path, equity data-load outcome, and acceptance status.

---

### Task 1: Add Failing History Summary Output Tests

**Files:**
- Modify: `tests/test_agent_history_summary.py`

- [ ] **Step 1: Add tests for new ranking keys and details**

Insert after `test_build_universe_history_summary_flattens_rows_and_rankings()`:

```python
def test_build_universe_history_summary_adds_expanded_rankings_and_details():
    aaa = compute_history_summary(_frame(260), symbol="AAA", timestep="day", as_of="2024-09-05")
    bbb = compute_history_summary(_frame(260), symbol="BBB", timestep="day", as_of="2024-09-05")
    ccc = compute_history_summary(_frame(260), symbol="CCC", timestep="day", as_of="2024-09-05")

    aaa["momentum"]["return_252"] = 0.10
    bbb["momentum"]["return_252"] = 0.30
    ccc["momentum"]["return_252"] = 0.20

    aaa["scores"]["return_63_over_volatility_20"] = 1.0
    bbb["scores"]["return_63_over_volatility_20"] = 3.0
    ccc["scores"]["return_63_over_volatility_20"] = 2.0

    aaa["scores"]["return_126_over_volatility_20"] = 4.0
    bbb["scores"]["return_126_over_volatility_20"] = 2.0
    ccc["scores"]["return_126_over_volatility_20"] = 3.0

    aaa["range"]["distance_to_high_252"] = -0.20
    bbb["range"]["distance_to_high_252"] = -0.01
    ccc["range"]["distance_to_high_252"] = -0.10

    aaa["volume"]["volume_vs_avg_20"] = 0.05
    bbb["volume"]["volume_vs_avg_20"] = 0.40
    ccc["volume"]["volume_vs_avg_20"] = 0.15

    summary = build_universe_history_summary(
        {"AAA": aaa, "BBB": bbb, "CCC": ccc},
        symbols=["AAA", "BBB", "CCC"],
        timestep="day",
        length=260,
        as_of="2024-09-05",
        loaded_tables={"AAA": "aaa_hist", "BBB": "bbb_hist", "CCC": "ccc_hist"},
        warnings=[],
    )

    assert summary["rankings"]["by_return_252"] == ["BBB", "CCC", "AAA"]
    assert summary["rankings"]["by_risk_adjusted_return_63"] == ["BBB", "CCC", "AAA"]
    assert summary["rankings"]["by_risk_adjusted_return_126"] == ["AAA", "CCC", "BBB"]
    assert summary["rankings"]["by_near_252_high"] == ["BBB", "CCC", "AAA"]
    assert summary["rankings"]["by_volume_surge"] == ["BBB", "CCC", "AAA"]
    assert summary["ranking_details"]["by_return_252"] == [
        {"symbol": "BBB", "value": 0.3},
        {"symbol": "CCC", "value": 0.2},
        {"symbol": "AAA", "value": 0.1},
    ]
```

- [ ] **Step 2: Add test for top-ten truncation and candidate summary**

Insert after the previous new test:

```python
def test_build_universe_history_summary_limits_rankings_and_candidate_summary_to_top_n():
    histories = {}
    symbols = []
    loaded_tables = {}
    for index in range(12):
        symbol = f"S{index:02d}"
        symbols.append(symbol)
        loaded_tables[symbol] = f"{symbol.lower()}_hist"
        summary = compute_history_summary(_frame(260), symbol=symbol, timestep="day", as_of=None)
        summary["momentum"]["return_21"] = index / 100.0
        summary["momentum"]["return_63"] = index / 200.0
        summary["momentum"]["return_126"] = index / 300.0
        summary["momentum"]["return_252"] = index / 400.0
        summary["scores"]["momentum_composite"] = index / 500.0
        summary["scores"]["composite_score"] = index / 600.0
        summary["scores"]["trend_alignment"] = index % 4
        summary["scores"]["return_63_over_volatility_20"] = index / 700.0
        summary["scores"]["return_126_over_volatility_20"] = index / 800.0
        summary["range"]["distance_to_high_252"] = -index / 100.0
        summary["volume"]["volume_vs_avg_20"] = index / 900.0
        histories[symbol] = summary

    result = build_universe_history_summary(
        histories,
        symbols=symbols,
        timestep="day",
        length=260,
        as_of=None,
        loaded_tables=loaded_tables,
        warnings=[],
        top_n=10,
    )

    assert result["coverage"] == {
        "requested_count": 12,
        "loaded_count": 12,
        "failed_count": 0,
        "top_n": 10,
    }
    for ranking in result["rankings"].values():
        assert len(ranking) <= 10
    for ranking in result["ranking_details"].values():
        assert len(ranking) <= 10
        assert set(ranking[0]) == {"symbol", "value"}
    assert "universe_summary" not in result
    assert {row["symbol"] for row in result["candidate_summary"]} == {
        symbol
        for ranking in result["rankings"].values()
        for symbol in ranking
    }
```

- [ ] **Step 3: Add test for opt-in full universe summary**

Insert after the previous new test:

```python
def test_build_universe_history_summary_can_include_full_universe_summary_when_requested():
    qqq = compute_history_summary(_frame(260), symbol="QQQ", timestep="day", as_of=None)
    spy = compute_history_summary(_frame(260), symbol="SPY", timestep="day", as_of=None)

    result = build_universe_history_summary(
        {"QQQ": qqq, "SPY": spy},
        symbols=["QQQ", "SPY"],
        timestep="day",
        length=260,
        as_of=None,
        loaded_tables={"QQQ": "qqq_hist", "SPY": "spy_hist"},
        warnings=[],
        top_n=1,
        include_full_universe_summary=True,
    )

    assert len(result["rankings"]["by_return_21"]) == 1
    assert len(result["candidate_summary"]) >= 1
    assert {row["symbol"] for row in result["universe_summary"]} == {"QQQ", "SPY"}
```

- [ ] **Step 4: Run focused tests and verify expected failures**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py::test_build_universe_history_summary_adds_expanded_rankings_and_details tests\test_agent_history_summary.py::test_build_universe_history_summary_limits_rankings_and_candidate_summary_to_top_n tests\test_agent_history_summary.py::test_build_universe_history_summary_can_include_full_universe_summary_when_requested -q
```

Expected: failures mentioning missing `by_return_252`, missing `ranking_details`, unsupported `top_n`, or unexpected `universe_summary` behavior.

---

### Task 2: Implement Expanded History Rankings

**Files:**
- Modify: `lumibot/components/agents/history_summary.py`

- [ ] **Step 1: Expand compact universe rows**

Update `_summary_to_universe_row` so it returns these extra fields:

```python
        "return_252": _compact_number(momentum.get("return_252")),
        "return_63_over_volatility_20": _compact_number(scores.get("return_63_over_volatility_20")),
        "return_126_over_volatility_20": _compact_number(scores.get("return_126_over_volatility_20")),
        "distance_to_high_252": _compact_number(range_metrics.get("distance_to_high_252")),
```

The row should continue to include the existing fields:

```python
        "latest_close": _compact_number(price.get("latest_close")),
        "return_5": _compact_number(momentum.get("return_5")),
        "return_21": _compact_number(momentum.get("return_21")),
        "return_63": _compact_number(momentum.get("return_63")),
        "return_126": _compact_number(momentum.get("return_126")),
        "momentum_composite": _compact_number(scores.get("momentum_composite")),
        "composite_score": _compact_number(scores.get("composite_score")),
        "volume_vs_avg_20": _compact_number(volume.get("volume_vs_avg_20")),
        "trend_alignment": _compact_number(scores.get("trend_alignment")),
        "volatility_20": _compact_number(risk.get("volatility_20")),
        "drawdown_from_high_60": _compact_number(range_metrics.get("drawdown_from_high_60")),
```

- [ ] **Step 2: Replace `_rankings` with top-N-aware ranking helpers**

Replace `_rankings` and `_rank_symbols` with:

```python
RANKING_SPECS: tuple[tuple[str, str], ...] = (
    ("by_return_21", "return_21"),
    ("by_return_63", "return_63"),
    ("by_return_126", "return_126"),
    ("by_momentum_composite", "momentum_composite"),
    ("by_composite_score", "composite_score"),
    ("by_trend_alignment", "trend_alignment"),
    ("by_return_252", "return_252"),
    ("by_risk_adjusted_return_63", "return_63_over_volatility_20"),
    ("by_risk_adjusted_return_126", "return_126_over_volatility_20"),
    ("by_near_252_high", "distance_to_high_252"),
    ("by_volume_surge", "volume_vs_avg_20"),
)


def _rankings(rows: list[dict[str, Any]], *, top_n: int) -> dict[str, list[str]]:
    return {
        name: [item["symbol"] for item in _rank_symbol_details(rows, key, top_n=top_n)]
        for name, key in RANKING_SPECS
    }


def _ranking_details(rows: list[dict[str, Any]], *, top_n: int) -> dict[str, list[dict[str, Any]]]:
    return {name: _rank_symbol_details(rows, key, top_n=top_n) for name, key in RANKING_SPECS}


def _rank_symbol_details(rows: list[dict[str, Any]], key: str, *, top_n: int) -> list[dict[str, Any]]:
    rankable = [
        (str(row["symbol"]), float(row[key]))
        for row in rows
        if row.get("symbol") and _is_rankable(row.get(key))
    ]
    ranked = sorted(rankable, key=lambda item: item[1], reverse=True)[:top_n]
    return [{"symbol": symbol, "value": _compact_number(value)} for symbol, value in ranked]
```

- [ ] **Step 3: Add input validation helper**

Add near `_is_rankable`:

```python
def _normalize_top_n(top_n: int | None, row_count: int) -> int:
    if top_n is None:
        top_n = 10
    if isinstance(top_n, bool):
        raise ValueError("top_n must be a positive integer.")
    try:
        normalized = int(top_n)
    except (TypeError, ValueError) as exc:
        raise ValueError("top_n must be a positive integer.") from exc
    if normalized <= 0:
        raise ValueError("top_n must be a positive integer.")
    if row_count <= 0:
        return normalized
    return min(normalized, row_count)
```

- [ ] **Step 4: Update `build_universe_history_summary` signature and return shape**

Change the signature to:

```python
def build_universe_history_summary(
    history_summaries: dict[str, dict[str, Any]],
    *,
    symbols: list[str],
    timestep: str | None,
    length: int | None,
    as_of: str | None,
    loaded_tables: dict[str, Any] | None,
    warnings: list[str] | None,
    top_n: int | None = 10,
    include_full_universe_summary: bool = False,
) -> dict[str, Any]:
```

Replace the return block with:

```python
    normalized_top_n = _normalize_top_n(top_n, len(universe_summary))
    rankings = _rankings(universe_summary, top_n=normalized_top_n)
    ranking_details = _ranking_details(universe_summary, top_n=normalized_top_n)
    candidate_symbols = {
        symbol
        for ranking in rankings.values()
        for symbol in ranking
    }
    candidate_summary = [
        row for row in universe_summary if row.get("symbol") in candidate_symbols
    ]
    loaded = loaded_tables or {}
    result = {
        "schema_version": SCHEMA_VERSION,
        "symbols": symbols,
        "timestep": timestep,
        "length": length,
        "as_of": as_of,
        "rankings": rankings,
        "ranking_details": ranking_details,
        "candidate_summary": candidate_summary,
        "coverage": {
            "requested_count": len(symbols),
            "loaded_count": len(universe_summary),
            "failed_count": max(len(symbols) - len(universe_summary), 0),
            "top_n": normalized_top_n,
        },
        "loaded_tables": loaded,
        "warnings": warnings or [],
    }
    if include_full_universe_summary:
        result["universe_summary"] = universe_summary
    return result
```

- [ ] **Step 5: Run focused history summary tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py -q
```

Expected: tests related to old default `universe_summary` expectations may fail and will be updated in Task 3; new ranking tests should pass after minor fixes.

---

### Task 3: Update Existing History Summary Tests For New Default Shape

**Files:**
- Modify: `tests/test_agent_history_summary.py`

- [ ] **Step 1: Update `test_build_universe_history_summary_flattens_rows_and_rankings`**

In this test's `build_universe_history_summary(...)` call, add:

```python
        include_full_universe_summary=True,
```

Keep the existing `rows = {row["symbol"]: row for row in summary["universe_summary"]}` assertions.

Add assertions for new ranking availability:

```python
    assert "by_return_252" in summary["rankings"]
    assert "by_risk_adjusted_return_63" in summary["rankings"]
    assert "by_risk_adjusted_return_126" in summary["rankings"]
    assert "by_near_252_high" in summary["rankings"]
    assert "by_volume_surge" in summary["rankings"]
    assert "ranking_details" in summary
    assert "candidate_summary" in summary
    assert summary["coverage"]["requested_count"] == 2
    assert summary["coverage"]["loaded_count"] == 2
```

- [ ] **Step 2: Update tests that inspect `universe_summary` rows**

For tests that call `build_universe_history_summary` and then inspect `summary["universe_summary"]`, add:

```python
        include_full_universe_summary=True,
```

Known tests likely needing this:

```text
test_build_universe_history_summary_sanitizes_non_finite_row_values
```

- [ ] **Step 3: Update unavailable ranking test**

In `test_build_universe_history_summary_skips_unavailable_ranking_values`, keep existing assertions and add:

```python
    assert summary["rankings"]["by_return_252"] == ["QQQ", "BAD"]
    assert summary["rankings"]["by_risk_adjusted_return_63"]
    assert summary["rankings"]["by_risk_adjusted_return_126"]
    assert summary["rankings"]["by_near_252_high"] == ["QQQ", "SHORT", "BAD"]
    assert "MISSING" not in summary["rankings"]["by_near_252_high"]
```

- [ ] **Step 4: Run history summary tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit history summary changes**

Run:

```powershell
git add lumibot\components\agents\history_summary.py tests\test_agent_history_summary.py
git commit -m "feat: add top-ten basket history rankings"
```

---

### Task 4: Pass Top-N Through DuckDB And Built-In Tool Layers

**Files:**
- Modify: `lumibot/components/agents/duckdb_tools.py`
- Modify: `lumibot/components/agents/builtins.py`
- Modify: `tests/test_agent_history_summary.py`

- [ ] **Step 1: Add failing tool description assertions**

Update `test_history_tool_descriptions_are_summary_first()` in `tests/test_agent_history_summary.py` to add:

```python
    assert "top_n" in multi
    assert "top-ten" in multi or "top ten" in multi
    assert "separate" in multi
```

- [ ] **Step 2: Run the tool description test and verify it fails**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py::test_history_tool_descriptions_are_summary_first -q
```

Expected: FAIL because `top_n` is not in the description yet.

- [ ] **Step 3: Update DuckDB summary layer signature**

In `lumibot/components/agents/duckdb_tools.py`, change:

```python
    def load_history_tables_summary(
        self,
        *,
        symbols: list[str],
        length: int,
        timestep: str = "day",
        asset_type: str = "stock",
        table_prefix: str | None = None,
        include_after_hours: bool = True,
    ) -> dict[str, Any]:
```

to:

```python
    def load_history_tables_summary(
        self,
        *,
        symbols: list[str],
        length: int,
        timestep: str = "day",
        asset_type: str = "stock",
        table_prefix: str | None = None,
        include_after_hours: bool = True,
        top_n: int = 10,
        include_full_universe_summary: bool = False,
    ) -> dict[str, Any]:
```

Pass the new arguments:

```python
        result = build_universe_history_summary(
            summaries,
            symbols=normalized_symbols,
            timestep=timestep,
            length=int(length),
            as_of=as_of,
            loaded_tables=loaded_tables,
            warnings=warnings,
            top_n=top_n,
            include_full_universe_summary=include_full_universe_summary,
        )
```

- [ ] **Step 4: Update built-in tool wrapper signature and validation**

In `_bind_load_history_tables_summary`, change the inner function signature to:

```python
    def load_history_tables_summary(
        *,
        symbols: list[str],
        length: int = 252,
        timestep: str = "day",
        asset_type: AssetTypeArg = "stock",
        table_prefix: str | None = None,
        include_after_hours: bool = True,
        top_n: int = 10,
        include_full_universe_summary: bool = False,
    ) -> dict[str, Any]:
```

After `length = _require_positive_int("length", length)`, add:

```python
        top_n = _require_positive_int("top_n", top_n)
        include_full_universe_summary = bool(include_full_universe_summary)
```

Pass the new arguments into `manager.duckdb.load_history_tables_summary(...)`:

```python
            top_n=top_n,
            include_full_universe_summary=include_full_universe_summary,
```

- [ ] **Step 5: Update the model-facing tool description**

In the `BoundTool` description for `market_load_history_tables_summary`, include:

```python
            "Arguments: symbols, optional length, timestep, asset_type, table_prefix, include_after_hours, "
            "top_n default 10, and include_full_universe_summary default false. "
            "It returns separate top-ten rankings and ranking_details by metric rather than a required single "
            "composite answer. "
            "candidate_summary contains symbols that appear in at least one top ranking, while loaded_tables "
            "keeps successfully loaded DuckDB table names for targeted follow-up. "
```

Keep the existing summary-first and no-lookahead caveats.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py::test_history_tool_descriptions_are_summary_first tests\test_agent_history_summary.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit tool-layer changes**

Run:

```powershell
git add lumibot\components\agents\duckdb_tools.py lumibot\components\agents\builtins.py tests\test_agent_history_summary.py
git commit -m "feat: expose top-n history summary rankings"
```

---

### Task 5: Update Replay Formatter For New Output Shape

**Files:**
- Modify: `tests/test_agent_replay_ui_formatters.py`
- Modify: `lumibot/components/agents/replay_ui/formatters.py`

- [ ] **Step 1: Add failing formatter test for new result shape**

Update `test_market_load_history_tables_summary_formatter_shows_rankings_and_warnings()` input so the raw result contains:

```python
            "coverage": {
                "requested_count": 50,
                "loaded_count": 49,
                "failed_count": 1,
                "top_n": 10,
            },
            "candidate_summary": [
                {"symbol": "NVDA"},
                {"symbol": "AVGO"},
            ],
            "ranking_details": {
                "by_return_252": [
                    {"symbol": "NVDA", "value": 0.82},
                    {"symbol": "AVGO", "value": 0.61},
                ],
                "by_near_252_high": [
                    {"symbol": "AVGO", "value": -0.01},
                    {"symbol": "NVDA", "value": -0.03},
                ],
            },
```

Add assertions:

```python
    assert "49 loaded / 50 requested" in text
    assert "top 10" in text
    assert "2 candidates" in text
    assert "return_252: NVDA 82.00%, AVGO 61.00%" in text
    assert "near_252_high: AVGO -1.00%, NVDA -3.00%" in text
```

- [ ] **Step 2: Run formatter test and verify it fails**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_formatters.py::test_market_load_history_tables_summary_formatter_shows_rankings_and_warnings -q
```

Expected: FAIL because formatter does not yet use `coverage` or `ranking_details`.

- [ ] **Step 3: Update `_market_load_history_tables_summary` formatter**

In `lumibot/components/agents/replay_ui/formatters.py`, update `_market_load_history_tables_summary` so it:

1. Uses `coverage` if present:

```python
    coverage = raw_result.get("coverage") if isinstance(raw_result, dict) else None
    if isinstance(coverage, dict):
        loaded = coverage.get("loaded_count")
        requested = coverage.get("requested_count")
        top_n = coverage.get("top_n")
        if loaded is not None and requested is not None:
            parts.append(f"{loaded} loaded / {requested} requested")
        if top_n is not None:
            parts.append(f"top {top_n}")
```

2. Counts `candidate_summary` before falling back to `universe_summary`:

```python
    candidate_rows = raw_result.get("candidate_summary")
    if isinstance(candidate_rows, list):
        parts.append(f"{len(candidate_rows)} candidates")
    else:
        rows = raw_result.get("universe_summary")
        if isinstance(rows, list):
            parts.append(f"{len(rows)} symbols")
```

3. Prefers `ranking_details` for display:

```python
    ranking_details = raw_result.get("ranking_details")
    if isinstance(ranking_details, dict):
        for key, entries in ranking_details.items():
            if not isinstance(entries, list) or not entries:
                continue
            label = key.removeprefix("by_")
            formatted = []
            for entry in entries[:5]:
                if not isinstance(entry, dict):
                    continue
                symbol = entry.get("symbol")
                value = entry.get("value")
                if symbol:
                    formatted.append(f"{symbol} {_percent(value)}")
            if formatted:
                parts.append(f"{label}: {', '.join(formatted)}")
```

Keep the existing `rankings` fallback for older traces.

- [ ] **Step 4: Run formatter tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_formatters.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit replay formatter changes**

Run:

```powershell
git add lumibot\components\agents\replay_ui\formatters.py tests\test_agent_replay_ui_formatters.py
git commit -m "feat: show expanded history rankings in replay"
```

---

### Task 6: Expand Equity Basket And Update Basket Prompt Tests

**Files:**
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add failing universe test**

Replace `test_basket_universes_have_at_least_five_semantically_valid_symbols()` with:

```python
def test_basket_universes_have_expected_equity_expansion_and_unchanged_defensive_baskets():
    module, _strategy_class = load_strategy_module()

    assert module.BASKET_UNIVERSES["equity"] == [
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "GOOGL",
        "META",
        "TSLA",
        "AVGO",
        "AMD",
        "NFLX",
        "ORCL",
        "CRM",
        "ADBE",
        "CSCO",
        "QCOM",
        "TXN",
        "IBM",
        "INTC",
        "NOW",
        "PANW",
        "UNH",
        "JNJ",
        "LLY",
        "MRK",
        "ABBV",
        "TMO",
        "ABT",
        "JPM",
        "BAC",
        "GS",
        "MS",
        "V",
        "MA",
        "WMT",
        "COST",
        "HD",
        "MCD",
        "NKE",
        "SBUX",
        "DIS",
        "XOM",
        "CVX",
        "CAT",
        "GE",
        "HON",
        "BA",
        "DE",
        "PG",
        "KO",
        "PEP",
    ]
    assert module.BASKET_UNIVERSES["commodity"] == ["GLD", "SLV", "DBC", "PDBC", "GSG"]
    assert module.BASKET_UNIVERSES["tips"] == ["TIP", "SCHP", "VTIP", "STIP", "LTPZ"]
    assert module.BASKET_UNIVERSES["nominal_bond"] == ["SHY", "IEF", "TLT", "GOVT", "VGIT"]
    assert len(module.BASKET_UNIVERSES["equity"]) == 50
    for symbols in module.BASKET_UNIVERSES.values():
        assert len(symbols) == len(set(symbols))
        assert all(symbol.isupper() and symbol.isalpha() for symbol in symbols)
```

- [ ] **Step 2: Add failing basket prompt boundary test**

Insert near existing agent prompt tests:

```python
def test_basket_agent_prompt_uses_separate_rankings_without_style_labels():
    _module, strategy_class = load_strategy_module()
    strategy = strategy_class()
    strategy.initialize()

    prompt = strategy.agents["equity_basket_agent"].system_prompt.lower()

    for required_phrase in (
        "market_load_history_tables_summary",
        "separate evidence views",
        "do not invent a new composite score",
        "supported by multiple relevant rankings",
        "target weight is zero",
    ):
        assert required_phrase in prompt

    for forbidden_phrase in (
        "defensive stock",
        "growth stock",
        "cyclical stock",
        "core stock",
        "safest stock",
        "highest return stock blindly",
    ):
        assert forbidden_phrase not in prompt
```

- [ ] **Step 3: Run tests and verify failures**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_basket_universes_have_expected_equity_expansion_and_unchanged_defensive_baskets tests\test_ai_trading_team_mock_growth_inflation_quadrant.py::test_basket_agent_prompt_uses_separate_rankings_without_style_labels -q
```

Expected: FAIL because equity still has 5 ETFs and prompt does not yet include the new ranking guidance.

- [ ] **Step 4: Update equity universe**

In `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`, replace:

```python
    "equity": ["SPY", "QQQ", "IWM", "EEM", "FXI"],
```

with:

```python
    "equity": [
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "GOOGL",
        "META",
        "TSLA",
        "AVGO",
        "AMD",
        "NFLX",
        "ORCL",
        "CRM",
        "ADBE",
        "CSCO",
        "QCOM",
        "TXN",
        "IBM",
        "INTC",
        "NOW",
        "PANW",
        "UNH",
        "JNJ",
        "LLY",
        "MRK",
        "ABBV",
        "TMO",
        "ABT",
        "JPM",
        "BAC",
        "GS",
        "MS",
        "V",
        "MA",
        "WMT",
        "COST",
        "HD",
        "MCD",
        "NKE",
        "SBUX",
        "DIS",
        "XOM",
        "CVX",
        "CAT",
        "GE",
        "HON",
        "BA",
        "DE",
        "PG",
        "KO",
        "PEP",
    ],
```

- [ ] **Step 5: Update basket-agent prompt text**

Replace the basket agent `system_prompt` in the loop with:

```python
                system_prompt=(
                    f"{basket_id.replace('_', ' ').title()} basket role: stay inside the assigned basket "
                    f"({symbols}). If target weight is zero, report inactive without unnecessary research. "
                    "When active, use market_load_history_tables_summary first for multi-symbol comparison. "
                    "Treat rankings as separate evidence views, not as one official answer. "
                    "Do not invent a new composite score. Prefer symbols supported by multiple relevant rankings. "
                    "Return basket_id, selected_symbol, status, candidate_symbols, and reason_brief. Do not place orders."
                ),
```

- [ ] **Step 6: Run strategy tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: PASS or only unrelated failures. If prompt tests fail because `strategy.agents[...]` exposes a different attribute name, inspect nearby tests and assert against the locally captured created agent config pattern already used in this file.

- [ ] **Step 7: Commit strategy changes**

Run:

```powershell
git add lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: expand equity basket ranking universe"
```

---

### Task 7: Run Integrated Tests And Lint

**Files:**
- No source files expected.
- Create or update validation note in Task 8.

- [ ] **Step 1: Run focused test suite**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py tests\test_agent_replay_ui_formatters.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: PASS.

- [ ] **Step 2: Run ruff on touched files**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\history_summary.py lumibot\components\agents\duckdb_tools.py lumibot\components\agents\builtins.py lumibot\components\agents\replay_ui\formatters.py lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_history_summary.py tests\test_agent_replay_ui_formatters.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
```

Expected: PASS.

- [ ] **Step 3: Fix any formatting or lint issues**

If ruff reports import ordering or line length issues, make only the minimal formatting changes it requests.

- [ ] **Step 4: Re-run focused tests after lint fixes**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py tests\test_agent_replay_ui_formatters.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit validation-only fixes if any**

If Step 3 changed files, run:

```powershell
git add lumibot tests
git commit -m "test: stabilize equity basket ranking coverage"
```

If Step 3 made no changes, do not create an empty commit.

---

### Task 8: Run One-Day Backtest Smoke Test And Record Validation

**Files:**
- Create: `docs/superpowers/notes/2026-08-12-equity-basket-ranking-expansion-validation.md`

- [ ] **Step 1: Run one-day benchmark with OpenAI GPT 5.6 Luna**

Load API keys from `project_notes\API.txt` using the existing project convention, then run the same modified benchmark runner used for recent strategy smoke tests. Use the mock growth/inflation quadrant strategy for one trading day.

Run:

```powershell
cd D:\Lumibot
$env:AI_TRADING_TEAM_MODEL="openai/gpt-5.6-luna"
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-05 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file project_notes\API.txt
```

Expected: script prints a JSON line containing `"strategy": "mock-growth-inflation-quadrant"` and `"status": "passed"`, or writes a clear failure payload into the artifact directory.

- [ ] **Step 2: Inspect trace for equity tool call**

Find the newest artifact and store it in a variable:

```powershell
$latest = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory | Sort-Object LastWriteTime | Select-Object -Last 1
$artifact = Join-Path $latest.FullName "mock-growth-inflation-quadrant"
$artifact
```

Then search trace JSON:

```powershell
rg -n "equity_basket_agent|market_load_history_tables_summary|by_return_252|by_risk_adjusted_return_63|by_near_252_high|by_volume_surge|candidate_summary|ranking_details" $artifact -g "*.json" -g "*.jsonl"
```

Expected:

```text
market_load_history_tables_summary
by_return_252
by_risk_adjusted_return_63
by_risk_adjusted_return_126
by_near_252_high
by_volume_surge
candidate_summary
ranking_details
```

- [ ] **Step 3: Confirm top-ten limit**

Inspect the equity tool result. Confirm each `rankings` list and `ranking_details` list has length `<= 10`.

Run:

```powershell
$equityTraceRoot = Join-Path $artifact "cache\agent_runtime\traces\equity_basket_agent"
rg -n "ranking_details|by_return_252|candidate_summary|coverage|top_n" $equityTraceRoot -g "*.json" -g "*.jsonl" -C 2
```

Expected: visible top-ten lists, not 50-symbol lists.

- [ ] **Step 4: Confirm workflow completion**

Use trace/UI artifacts to confirm:

```text
macro_allocation_agent ran
equity_basket_agent ran
commodity_basket_agent ran
tips_basket_agent ran
nominal_bond_basket_agent ran
portfolio_decision_agent ran
execution_agent ran
```

If execution is blocked by market data, order sizing, or zero target weights, record the blocker. The ranking feature is accepted if the basket evidence layer worked and the workflow reached portfolio decision; execution-specific blockers should be separated unless caused by this feature.

- [ ] **Step 5: Write validation note**

Create `docs/superpowers/notes/2026-08-12-equity-basket-ranking-expansion-validation.md`:

```markdown
# Equity Basket Ranking Expansion Validation

## Commands

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py tests\test_agent_replay_ui_formatters.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\history_summary.py lumibot\components\agents\duckdb_tools.py lumibot\components\agents\builtins.py lumibot\components\agents\replay_ui\formatters.py lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_history_summary.py tests\test_agent_replay_ui_formatters.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
```

## Backtest

- Model:
- Date:
- Command:
- Artifact:

## Acceptance Check

- [ ] New rankings present
- [ ] Ranking details present
- [ ] Candidate summary present
- [ ] Coverage present
- [ ] Equity universe requested with 50 symbols
- [ ] Rankings limited to top 10
- [ ] Portfolio decision agent reached
- [ ] Execution agent reached
- [ ] No repeated DuckDB fallback for ordinary equity ranking

## Notes

- Data-load failures:
- Execution blockers:
- Follow-up recommendations:
```

- [ ] **Step 6: Commit validation note**

Run:

```powershell
git add docs\superpowers\notes\2026-08-12-equity-basket-ranking-expansion-validation.md
git commit -m "docs: validate equity basket ranking expansion"
```

---

## Final Verification

- [ ] **Step 1: Show commit history for this feature**

Run:

```powershell
git log --oneline -6
```

Expected: commits for plan/spec plus implementation commits.

- [ ] **Step 2: Confirm unrelated files remain untouched**

Run:

```powershell
git status --short --branch
```

Expected: only pre-existing unrelated files may remain, such as:

```text
 M project_notes/Workflow.vsdx
?? docs/strategy_research/
?? project_notes/ra_figures/
?? project_notes/research_affiliates_beware_shocks_extracted.txt
?? project_notes/research_affiliates_beware_shocks_html_text.txt
```

Do not include those files in this feature unless the user explicitly asks.

- [ ] **Step 3: Provide concise result summary to user**

Report:

```text
Implemented:
- Added five top-ten history rankings.
- Expanded equity basket to 50 US stocks.
- Updated basket prompt and replay formatter.
- Verified with focused tests, ruff, and one-day smoke backtest.

Important findings:
- Any ticker data failures.
- Whether equity agent used DuckDB fallback.
- Whether workflow reached execution.
```
