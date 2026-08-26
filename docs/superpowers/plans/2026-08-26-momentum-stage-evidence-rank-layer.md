# Momentum Stage Evidence Rank Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the equity-only LLM strategy's old "strongest current rank wins" evidence layer with a momentum-stage evidence layer that highlights confirmed but not exhausted stock strength.

**Architecture:** Keep the existing history-summary and DuckDB tool path, but add an explicit `momentum_stage` evidence profile alongside the legacy profile. The equity-only strategies will request the new profile through an equity-scoped wrapper tool and prompts; other callers keep legacy behavior unless they explicitly request `momentum_stage`.

**Tech Stack:** Python 3, pandas, DuckDB in-memory tables, LumiBot agent tool definitions, existing replay UI formatter, pytest, ruff, `scripts/run_ai_trading_team_examples_benchmark.py`.

---

## Safety Notes

- Work in `C:\Users\Ran\.config\superpowers\worktrees\lumibot\optimize-equity-only-llm`.
- Current branch at plan-writing time: `feature/qqq-historical-constituent-universe`.
- Preserve existing dirty files unless this plan explicitly modifies them:
  - `lumibot/components/agents/builtins.py`
  - `lumibot/components/agents/runtime.py`
  - `tests/test_agent_manager.py`
  - `docs/superpowers/notes/2026-08-26-momentum-stage-indicator-research.md`
  - `project_notes/qqq_historical_5y_deep_dive_20260822_180628.md`
- When committing, stage only files changed by this implementation and inspect `git diff --cached` before each commit.
- Do not run a five-year backtest in this feature. The smoke validation is a one-trading-day or two-trading-day run only.
- Do not print API keys from `project_notes\API.txt`.

---

## File Structure

Modify:

- `lumibot/components/agents/history_summary.py`
  - Add `evidence_profile` dispatch.
  - Keep legacy rank constants and behavior.
  - Add first-version momentum-stage metrics, ranking groups, candidate rows, benchmark context, and warning flags.

- `lumibot/components/agents/duckdb_tools.py`
  - Accept `evidence_profile` and `benchmark_symbols`.
  - Load benchmark symbols separately for `momentum_stage`.
  - Pass registered frames into the universe-summary builder so rank-delta and top-decile-age can be calculated.

- `lumibot/components/agents/builtins.py`
  - Expose the new parameters on `market_load_history_tables_summary`.
  - Update the global tool description so it is profile-aware and does not advertise the old five-group profile as the only path.

- `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
  - Replace the direct built-in history-summary tool with an equity-scoped wrapper that defaults to `evidence_profile="momentum_stage"` and `benchmark_symbols=["QQQ", "SPY"]`.
  - Rewrite the equity evidence policy, system prompts, and task prompt.

- `lumibot/example_strategies/dynamic_equity_portfolio_constructor.py`
  - Add momentum-stage scoring and diagnostics.
  - Preserve the legacy scoring path for legacy market summaries.

- `lumibot/components/agents/replay_ui/formatters.py`
  - Make `market_load_history_tables_summary` explanations readable for `momentum_stage`.

Modify tests:

- `tests/test_agent_history_summary.py`
- `tests/test_agent_manager.py`
- `tests/test_ai_trading_team_equity_only_llm.py`
- `tests/test_dynamic_equity_portfolio_constructor.py`
- `tests/test_agent_replay_ui_formatters.py`

Do not modify:

- `lumibot/example_strategies/target_portfolio_to_execution_plan.py`
- `lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py`
- QQQ N-PORT universe resolver code
- Execution tools
- Cadence logic
- News provider code

---

## Public Contract

The new model-facing summary shape for the equity-only strategies must contain:

```json
{
  "schema_version": "1.0",
  "evidence_profile": "momentum_stage",
  "rank_groups": {
    "freshness": ["by_rank_delta_4w"],
    "smoothness": ["by_positive_day_ratio_3m", "by_low_max_day_return_share_3m"],
    "near_high": ["by_near_252d_high"],
    "volume_confirmation": ["by_up_down_volume_ratio_60d"],
    "relative_strength": ["by_excess_return_vs_qqq_6m", "by_excess_return_vs_spy_6m"]
  },
  "rankings": {},
  "ranking_details": {},
  "candidate_summary": [],
  "benchmark_context": {
    "symbols": ["QQQ", "SPY"],
    "return_126": {}
  }
}
```

Candidate rows in `candidate_summary` must include:

```python
{
    "symbol": "AAA",
    "latest_close": 123.45,
    "stage_evidence_groups": ["freshness", "smoothness"],
    "stage_ranking_count": 3,
    "stage_best_rank": 1,
    "stage_best_rank_by_group": {"freshness": 1, "smoothness": 2},
    "rank_delta_4w": 12,
    "top_decile_age_weeks": 2,
    "extension_ma50_pct": 0.08,
    "atr_extension_20d": 1.4,
    "positive_day_ratio_3m": 0.62,
    "max_day_return_share_3m": 0.16,
    "distance_to_252d_high_pct": -0.02,
    "recent_vs_intermediate_momentum": 0.03,
    "up_down_volume_ratio_60d": 1.35,
    "excess_return_vs_qqq_6m": 0.07,
    "excess_return_vs_spy_6m": 0.11,
    "stage_warning_flags": []
}
```

The legacy profile may still return old fields such as `momentum_composite`, `composite_score`, `evidence_groups`, and `best_rank_by_group`. The `momentum_stage` profile must not expose `by_composite_score`, `by_momentum_composite`, `momentum_composite`, or `composite_score` in model-facing ranking output.

---

## Metric Definitions

Use these first-version calculations:

```python
rank_delta_4w = rank_by_return_126_as_of_21_bars_ago - rank_by_return_126_as_of_latest_bar
top_decile_age_weeks = consecutive weekly samples where rank_by_return_126 <= ceil(candidate_count * 0.10)
extension_ma50_pct = latest_close / sma_50 - 1
atr_extension_20d = (latest_close - sma_20) / atr_20
positive_day_ratio_3m = count(positive daily returns over last 63 returns) / available return count
max_day_return_share_3m = max(positive daily return over last 63 returns) / sum(positive returns over last 63 returns)
distance_to_252d_high_pct = latest_close / 252_day_high - 1
recent_vs_intermediate_momentum = return_21 - return_252_ex_skip_21
up_down_volume_ratio_60d = sum(volume on positive days over last 60 returns) / sum(volume on negative days over last 60 returns)
excess_return_vs_qqq_6m = stock_return_126 - qqq_return_126
excess_return_vs_spy_6m = stock_return_126 - spy_return_126
```

Use these warning thresholds in the first implementation:

```python
MOMENTUM_STAGE_WARNING_THRESHOLDS = {
    "stale_top_decile_weeks": 12,
    "extreme_ma50_extension_pct": 0.20,
    "extreme_atr_extension": 3.0,
    "recent_overheat_vs_intermediate": 0.15,
    "single_day_jump_share": 0.35,
    "weak_up_down_volume_ratio": 1.0,
}
```

Warning logic:

```python
stale_top_decile = top_decile_age_weeks >= 12
extreme_ma50_extension = extension_ma50_pct >= 0.20
extreme_atr_extension = atr_extension_20d >= 3.0
recent_overheat_vs_intermediate = recent_vs_intermediate_momentum >= 0.15
single_day_jump_concentration = max_day_return_share_3m >= 0.35
benchmark_lag = excess_return_vs_qqq_6m < 0 and excess_return_vs_spy_6m < 0
thin_or_missing_volume_support = up_down_volume_ratio_60d is None or up_down_volume_ratio_60d < 1.0
insufficient_history = any required stage metric for ranking or warning is None
```

---

## Task 1: Add Failing History-Summary Tests

**Files:**

- Modify: `tests/test_agent_history_summary.py`

- [ ] **Step 1: Add imports and deterministic helpers**

Append these helpers near the existing `_frame` and `_trend_frame` helpers:

```python
def _stage_frame(
    *,
    start: float,
    early_step: float,
    recent_step: float,
    jump: float = 0.0,
    length: int = 320,
    volume_base: int = 1000,
) -> pd.DataFrame:
    dates = pd.date_range("2023-01-01", periods=length, freq="D")
    close_values = []
    price = float(start)
    switch_index = max(1, length - 63)
    for index in range(length):
        step = early_step if index < switch_index else recent_step
        price += step
        close_values.append(price)
    if jump:
        close_values[-10] += jump
        close_values[-9:] = [value + jump for value in close_values[-9:]]
    close = pd.Series(close_values, dtype="float64")
    volume = pd.Series([volume_base + index * 5 for index in range(length)], dtype="float64")
    return pd.DataFrame(
        {
            "Date": dates,
            "open": close - 0.25,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": volume,
        }
    )
```

- [ ] **Step 2: Add a failing test for per-symbol stage metrics**

Append:

```python
def test_compute_history_summary_adds_momentum_stage_metrics():
    frame = _stage_frame(start=100, early_step=0.15, recent_step=0.45, length=320)
    summary = compute_history_summary(frame, symbol="AAA", timestep="day", as_of="2024-01-01")

    stage = summary["momentum_stage"]

    assert stage["extension_ma50_pct"] == pytest.approx(
        summary["price"]["latest_close"] / summary["trend"]["sma_50"] - 1.0
    )
    assert stage["atr_extension_20d"] is not None
    assert 0.0 <= stage["positive_day_ratio_3m"] <= 1.0
    assert 0.0 <= stage["max_day_return_share_3m"] <= 1.0
    assert stage["distance_to_252d_high_pct"] == summary["range"]["distance_to_high_252"]
    assert stage["recent_vs_intermediate_momentum"] == pytest.approx(
        summary["momentum"]["return_21"] - summary["momentum"]["return_252_ex_skip_21"]
    )
    assert stage["up_down_volume_ratio_60d"] is not None
    for key in (
        "extension_ma50_pct",
        "atr_extension_20d",
        "positive_day_ratio_3m",
        "max_day_return_share_3m",
        "distance_to_252d_high_pct",
        "recent_vs_intermediate_momentum",
        "up_down_volume_ratio_60d",
    ):
        assert summary["availability"][key] is True
```

- [ ] **Step 3: Add a failing test for `momentum_stage` output shape and old-rank removal**

Append:

```python
def test_build_universe_history_summary_momentum_stage_profile_shape_excludes_legacy_composites():
    frames = {
        "AAA": _stage_frame(start=100, early_step=0.05, recent_step=0.60),
        "BBB": _stage_frame(start=100, early_step=0.30, recent_step=0.20),
        "CCC": _stage_frame(start=100, early_step=0.20, recent_step=0.25, jump=20),
    }
    summaries = {
        symbol: compute_history_summary(frame, symbol=symbol, timestep="day", as_of="2024-01-01")
        for symbol, frame in frames.items()
    }
    qqq = compute_history_summary(
        _stage_frame(start=100, early_step=0.20, recent_step=0.20),
        symbol="QQQ",
        timestep="day",
        as_of="2024-01-01",
    )
    spy = compute_history_summary(
        _stage_frame(start=100, early_step=0.10, recent_step=0.10),
        symbol="SPY",
        timestep="day",
        as_of="2024-01-01",
    )

    summary = build_universe_history_summary(
        summaries,
        symbols=["AAA", "BBB", "CCC"],
        timestep="day",
        length=320,
        as_of="2024-01-01",
        loaded_tables={"AAA": "aaa_hist", "BBB": "bbb_hist", "CCC": "ccc_hist"},
        warnings=[],
        top_n=2,
        candidate_summary_limit=3,
        evidence_profile="momentum_stage",
        history_frames=frames,
        benchmark_summaries={"QQQ": qqq, "SPY": spy},
    )

    assert summary["evidence_profile"] == "momentum_stage"
    assert summary["rank_groups"] == {
        "freshness": ["by_rank_delta_4w"],
        "smoothness": ["by_positive_day_ratio_3m", "by_low_max_day_return_share_3m"],
        "near_high": ["by_near_252d_high"],
        "volume_confirmation": ["by_up_down_volume_ratio_60d"],
        "relative_strength": ["by_excess_return_vs_qqq_6m", "by_excess_return_vs_spy_6m"],
    }
    assert "by_composite_score" not in summary["rankings"]
    assert "by_momentum_composite" not in summary["rankings"]
    assert "QQQ" not in [row["symbol"] for row in summary["candidate_summary"]]
    assert "SPY" not in [row["symbol"] for row in summary["candidate_summary"]]
    assert summary["benchmark_context"]["return_126"]["QQQ"] == pytest.approx(qqq["momentum"]["return_126"])
    assert summary["benchmark_context"]["return_126"]["SPY"] == pytest.approx(spy["momentum"]["return_126"])

    first_row = summary["candidate_summary"][0]
    assert set(first_row) == {
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
    assert "momentum_composite" not in first_row
    assert "composite_score" not in first_row
```

- [ ] **Step 4: Add a failing test for rank directions**

Append:

```python
def test_momentum_stage_rank_directions_put_lower_jump_concentration_first():
    smooth = _stage_frame(start=100, early_step=0.20, recent_step=0.30, jump=0)
    jumpy = _stage_frame(start=100, early_step=0.20, recent_step=0.02, jump=35)
    summaries = {
        "SMTH": compute_history_summary(smooth, symbol="SMTH", timestep="day", as_of=None),
        "JUMP": compute_history_summary(jumpy, symbol="JUMP", timestep="day", as_of=None),
    }

    summary = build_universe_history_summary(
        summaries,
        symbols=["SMTH", "JUMP"],
        timestep="day",
        length=320,
        as_of=None,
        loaded_tables=None,
        warnings=None,
        top_n=2,
        candidate_summary_limit=2,
        evidence_profile="momentum_stage",
        history_frames={"SMTH": smooth, "JUMP": jumpy},
        benchmark_summaries={},
    )

    assert summary["rankings"]["by_low_max_day_return_share_3m"][0] == "SMTH"
    jump_row = next(row for row in summary["candidate_summary"] if row["symbol"] == "JUMP")
    assert "single_day_jump_concentration" in jump_row["stage_warning_flags"]
```

- [ ] **Step 5: Run the new failing tests**

Run:

```powershell
python -m pytest tests/test_agent_history_summary.py::test_compute_history_summary_adds_momentum_stage_metrics tests/test_agent_history_summary.py::test_build_universe_history_summary_momentum_stage_profile_shape_excludes_legacy_composites tests/test_agent_history_summary.py::test_momentum_stage_rank_directions_put_lower_jump_concentration_first -q
```

Expected result before implementation:

```text
FAILED ... KeyError: 'momentum_stage'
FAILED ... TypeError: build_universe_history_summary() got an unexpected keyword argument 'evidence_profile'
```

Do not proceed until these tests fail for the expected reason.

---

## Task 2: Implement Momentum-Stage Metrics And Universe Builder

**Files:**

- Modify: `lumibot/components/agents/history_summary.py`
- Test: `tests/test_agent_history_summary.py`

- [ ] **Step 1: Add profile constants near the existing rank constants**

Add below `DEFAULT_CANDIDATE_SUMMARY_LIMIT`:

```python
LEGACY_EVIDENCE_PROFILE = "legacy"
MOMENTUM_STAGE_EVIDENCE_PROFILE = "momentum_stage"

MOMENTUM_STAGE_RANK_GROUPS = {
    "freshness": ["by_rank_delta_4w"],
    "smoothness": ["by_positive_day_ratio_3m", "by_low_max_day_return_share_3m"],
    "near_high": ["by_near_252d_high"],
    "volume_confirmation": ["by_up_down_volume_ratio_60d"],
    "relative_strength": ["by_excess_return_vs_qqq_6m", "by_excess_return_vs_spy_6m"],
}

MOMENTUM_STAGE_RANKING_METRICS = {
    "by_rank_delta_4w": "rank_delta_4w",
    "by_positive_day_ratio_3m": "positive_day_ratio_3m",
    "by_low_max_day_return_share_3m": "max_day_return_share_3m",
    "by_near_252d_high": "distance_to_252d_high_pct",
    "by_up_down_volume_ratio_60d": "up_down_volume_ratio_60d",
    "by_excess_return_vs_qqq_6m": "excess_return_vs_qqq_6m",
    "by_excess_return_vs_spy_6m": "excess_return_vs_spy_6m",
}

MOMENTUM_STAGE_RANKING_DIRECTIONS = {
    "by_low_max_day_return_share_3m": "asc",
}

MOMENTUM_STAGE_REFERENCE_FIELDS = {
    "top_decile_age_weeks",
    "extension_ma50_pct",
    "atr_extension_20d",
    "recent_vs_intermediate_momentum",
}

MOMENTUM_STAGE_WARNING_THRESHOLDS = {
    "stale_top_decile_weeks": 12,
    "extreme_ma50_extension_pct": 0.20,
    "extreme_atr_extension": 3.0,
    "recent_overheat_vs_intermediate": 0.15,
    "single_day_jump_share": 0.35,
    "weak_up_down_volume_ratio": 1.0,
}

MOMENTUM_STAGE_CANDIDATE_PRIORITY_RANKINGS = [
    "by_rank_delta_4w",
    "by_excess_return_vs_qqq_6m",
    "by_excess_return_vs_spy_6m",
    "by_positive_day_ratio_3m",
]
```

- [ ] **Step 2: Add per-symbol stage metrics inside `compute_history_summary()`**

After `volume_summary` is defined and before `scores.update(...)`, add:

```python
atr_20 = _atr(high, low, close, 20)
stage_metrics = {
    "extension_ma50_pct": _relative_to(latest_close, trend["sma_50"]),
    "atr_extension_20d": _ratio(
        _finite_float(latest_close - trend["sma_20"])
        if latest_close is not None and trend["sma_20"] is not None
        else None,
        atr_20,
    ),
    "positive_day_ratio_3m": _positive_day_ratio(close, 63),
    "max_day_return_share_3m": _max_positive_return_share(close, 63),
    "distance_to_252d_high_pct": _relative_to(latest_close, high_252),
    "recent_vs_intermediate_momentum": _finite_float(
        momentum["return_21"] - momentum["return_252_ex_skip_21"]
    )
    if momentum["return_21"] is not None and momentum["return_252_ex_skip_21"] is not None
    else None,
    "up_down_volume_ratio_60d": _up_down_volume_ratio(close, volume, 60),
}
```

Add these availability keys:

```python
        "extension_ma50_pct": stage_metrics["extension_ma50_pct"] is not None,
        "atr_extension_20d": stage_metrics["atr_extension_20d"] is not None,
        "positive_day_ratio_3m": stage_metrics["positive_day_ratio_3m"] is not None,
        "max_day_return_share_3m": stage_metrics["max_day_return_share_3m"] is not None,
        "distance_to_252d_high_pct": stage_metrics["distance_to_252d_high_pct"] is not None,
        "recent_vs_intermediate_momentum": stage_metrics["recent_vs_intermediate_momentum"] is not None,
        "up_down_volume_ratio_60d": stage_metrics["up_down_volume_ratio_60d"] is not None,
```

Add this field to the returned summary:

```python
        "momentum_stage": stage_metrics,
```

- [ ] **Step 3: Add metric helper functions near `_up_volume_ratio()`**

Add:

```python
def _atr(
    high_series: pd.Series | None,
    low_series: pd.Series | None,
    close_series: pd.Series | None,
    window: int,
) -> float | None:
    if high_series is None or low_series is None or close_series is None:
        return None
    data = pd.DataFrame({"high": high_series, "low": low_series, "close": close_series}).dropna()
    if len(data) <= window:
        return None
    previous_close = data["close"].shift(1)
    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - previous_close).abs(),
            (data["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    values = true_range.dropna().tail(window)
    if len(values) < window:
        return None
    return _finite_float(values.mean())


def _positive_day_ratio(series: pd.Series | None, window: int) -> float | None:
    if series is None or len(series) <= window:
        return None
    returns = series.pct_change().dropna().tail(window)
    returns = returns[returns.map(lambda value: _finite_float(value) is not None)]
    if returns.empty:
        return None
    return _finite_float((returns > 0).sum() / len(returns))


def _max_positive_return_share(series: pd.Series | None, window: int) -> float | None:
    if series is None or len(series) <= window:
        return None
    returns = series.pct_change().dropna().tail(window)
    returns = returns[returns.map(lambda value: _finite_float(value) is not None)]
    positives = returns[returns > 0]
    if positives.empty:
        return None
    total_positive = _finite_float(positives.sum())
    if total_positive in (None, 0):
        return None
    return _finite_float(float(positives.max()) / total_positive)


def _up_down_volume_ratio(
    close_series: pd.Series | None,
    volume_series: pd.Series | None,
    window: int,
) -> float | None:
    if close_series is None or volume_series is None or len(close_series) <= window:
        return None
    data = pd.DataFrame({"close": close_series, "volume": volume_series}).dropna().tail(window + 1)
    data = data[
        data["close"].map(lambda value: _finite_float(value) is not None)
        & data["volume"].map(lambda value: _finite_float(value) is not None)
    ].reset_index(drop=True)
    if len(data) <= window:
        return None
    returns = data["close"].pct_change().dropna()
    volumes = data["volume"].iloc[1:]
    up_volume = _finite_float(volumes[returns > 0].sum())
    down_volume = _finite_float(volumes[returns < 0].sum())
    if up_volume is None or down_volume in (None, 0):
        return None
    return _finite_float(up_volume / down_volume)
```

- [ ] **Step 4: Extend `build_universe_history_summary()` signature**

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
    top_n: int = DEFAULT_RANKING_LIMIT,
    candidate_summary_limit: int = DEFAULT_CANDIDATE_SUMMARY_LIMIT,
    evidence_profile: str = LEGACY_EVIDENCE_PROFILE,
    history_frames: dict[str, pd.DataFrame] | None = None,
    benchmark_summaries: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
```

At the start of the body, after limit normalization, add:

```python
    profile = str(evidence_profile or LEGACY_EVIDENCE_PROFILE).strip().lower()
    if profile == MOMENTUM_STAGE_EVIDENCE_PROFILE:
        return _build_momentum_stage_universe_history_summary(
            history_summaries,
            symbols=symbols,
            timestep=timestep,
            length=length,
            as_of=as_of,
            loaded_tables=loaded_tables,
            warnings=warnings,
            top_n=top_n,
            candidate_summary_limit=candidate_summary_limit,
            history_frames=history_frames or {},
            benchmark_summaries=benchmark_summaries or {},
        )
    if profile != LEGACY_EVIDENCE_PROFILE:
        raise ValueError(f"Unsupported evidence_profile: {evidence_profile!r}")
```

Add `"evidence_profile": LEGACY_EVIDENCE_PROFILE` to the existing legacy return payload.

- [ ] **Step 5: Add the momentum-stage universe builder**

Add these helpers before `_summary_to_universe_row()`:

```python
def _build_momentum_stage_universe_history_summary(
    history_summaries: dict[str, dict[str, Any]],
    *,
    symbols: list[str],
    timestep: str | None,
    length: int | None,
    as_of: str | None,
    loaded_tables: dict[str, Any] | None,
    warnings: list[str] | None,
    top_n: int,
    candidate_summary_limit: int,
    history_frames: dict[str, pd.DataFrame],
    benchmark_summaries: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    all_universe_rows = [
        _summary_to_momentum_stage_row(history_summaries[symbol])
        for symbol in symbols
        if symbol in history_summaries
    ]
    _add_rank_delta_and_top_decile_age(all_universe_rows, history_frames)
    _add_benchmark_excess_returns(all_universe_rows, benchmark_summaries)
    for row in all_universe_rows:
        row["stage_warning_flags"] = _momentum_stage_warning_flags(row)

    full_rankings = _rankings_for_metrics(
        all_universe_rows,
        MOMENTUM_STAGE_RANKING_METRICS,
        directions=MOMENTUM_STAGE_RANKING_DIRECTIONS,
    )
    rankings = _limit_rankings(full_rankings, top_n)
    ranking_details = _ranking_details_for_metrics(all_universe_rows, rankings, MOMENTUM_STAGE_RANKING_METRICS)
    selected_symbols = _select_momentum_stage_candidate_symbols(rankings, limit=candidate_summary_limit)
    if not selected_symbols:
        selected_symbols = [
            str(row["symbol"])
            for row in all_universe_rows
            if row.get("symbol")
        ][:candidate_summary_limit]
    rows_by_symbol = {str(row["symbol"]): row for row in all_universe_rows if row.get("symbol")}
    candidate_summary = [
        _momentum_stage_candidate_summary_row(symbol, rows_by_symbol[symbol], ranking_details)
        for symbol in selected_symbols
        if symbol in rows_by_symbol
    ]
    coverage = {
        "requested_count": len(symbols),
        "loaded_count": len(all_universe_rows),
        "failed_count": max(0, len(symbols) - len(all_universe_rows)),
        "top_n": top_n,
        "candidate_summary_limit": candidate_summary_limit,
        "ranking_count": len(rankings),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_profile": MOMENTUM_STAGE_EVIDENCE_PROFILE,
        "coverage": coverage,
        "rank_groups": MOMENTUM_STAGE_RANK_GROUPS,
        "reference_fields": sorted(MOMENTUM_STAGE_REFERENCE_FIELDS),
        "ranking_limit": top_n,
        "candidate_summary_limit": candidate_summary_limit,
        "rankings": rankings,
        "ranking_details": ranking_details,
        "candidate_summary": candidate_summary,
        "symbols": symbols,
        "timestep": timestep,
        "length": length,
        "as_of": as_of,
        "benchmark_context": _benchmark_context(benchmark_summaries),
        "universe_summary_limit": candidate_summary_limit,
        "universe_summary_selection": {
            "mode": "momentum_stage_top_rank_union",
            "candidate_count_before_limit": len(_unique_ranked_symbols_for_priority(
                rankings,
                MOMENTUM_STAGE_CANDIDATE_PRIORITY_RANKINGS,
            )),
            "included_symbols": selected_symbols,
            "priority": MOMENTUM_STAGE_CANDIDATE_PRIORITY_RANKINGS + ["multi_stage_ranking_overlap"],
        },
        "universe_summary": candidate_summary,
        "loaded_tables": loaded_tables or {},
        "warnings": warnings or [],
    }
```

- [ ] **Step 6: Add stage row, ranking, and warning helpers**

Add these helpers after `_summary_to_universe_row()`:

```python
def _summary_to_momentum_stage_row(summary: dict[str, Any]) -> dict[str, Any]:
    price = _dict(summary.get("price"))
    stage = _dict(summary.get("momentum_stage"))
    risk = _dict(summary.get("risk"))
    return {
        "symbol": summary.get("symbol"),
        "latest_close": _compact_number(price.get("latest_close")),
        "rank_delta_4w": None,
        "top_decile_age_weeks": None,
        "extension_ma50_pct": _compact_number(stage.get("extension_ma50_pct")),
        "atr_extension_20d": _compact_number(stage.get("atr_extension_20d")),
        "positive_day_ratio_3m": _compact_number(stage.get("positive_day_ratio_3m")),
        "max_day_return_share_3m": _compact_number(stage.get("max_day_return_share_3m")),
        "distance_to_252d_high_pct": _compact_number(stage.get("distance_to_252d_high_pct")),
        "recent_vs_intermediate_momentum": _compact_number(stage.get("recent_vs_intermediate_momentum")),
        "up_down_volume_ratio_60d": _compact_number(stage.get("up_down_volume_ratio_60d")),
        "excess_return_vs_qqq_6m": None,
        "excess_return_vs_spy_6m": None,
        "volatility_20": _compact_number(risk.get("volatility_20")),
    }


def _rankings_for_metrics(
    rows: list[dict[str, Any]],
    metric_map: dict[str, str],
    *,
    directions: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    return {
        ranking_name: _rank_symbols(
            rows,
            metric_name,
            direction=(directions or {}).get(ranking_name, "desc"),
        )
        for ranking_name, metric_name in metric_map.items()
    }


def _ranking_details_for_metrics(
    rows: list[dict[str, Any]],
    rankings: dict[str, list[str]],
    metric_map: dict[str, str],
) -> dict[str, list[dict[str, Any]]]:
    rows_by_symbol = {str(row["symbol"]): row for row in rows if row.get("symbol")}
    details: dict[str, list[dict[str, Any]]] = {}
    for ranking_name, symbols in rankings.items():
        metric_name = metric_map[ranking_name]
        entries = []
        for index, symbol in enumerate(symbols, start=1):
            row = rows_by_symbol.get(symbol)
            if row is None:
                continue
            entries.append({"rank": index, "symbol": symbol, "value": _compact_number(row.get(metric_name))})
        details[ranking_name] = entries
    return details
```

Change `_rankings()` to call the generic helper:

```python
def _rankings(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    return _rankings_for_metrics(rows, RANKING_METRICS)
```

Change `_ranking_details()` to call the generic helper:

```python
def _ranking_details(
    rows: list[dict[str, Any]],
    rankings: dict[str, list[str]],
) -> dict[str, list[dict[str, Any]]]:
    return _ranking_details_for_metrics(rows, rankings, RANKING_METRICS)
```

Change `_rank_symbols()` signature and body:

```python
def _rank_symbols(rows: list[dict[str, Any]], key: str, *, direction: str = "desc") -> list[str]:
    rankable = [
        (str(row["symbol"]), float(row[key]))
        for row in rows
        if row.get("symbol") and _is_rankable(row.get(key))
    ]
    if direction == "asc":
        return [symbol for symbol, _ in sorted(rankable, key=lambda item: (item[1], item[0]))]
    return [symbol for symbol, _ in sorted(rankable, key=lambda item: (-item[1], item[0]))]
```

Add these stage-specific helpers:

```python
def _momentum_stage_candidate_summary_row(
    symbol: str,
    row: dict[str, Any],
    ranking_details: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    best_rank_by_group: dict[str, int] = {}
    ranking_count = 0
    best_rank: int | None = None
    for group_name, ranking_names in MOMENTUM_STAGE_RANK_GROUPS.items():
        group_best_rank: int | None = None
        for ranking_name in ranking_names:
            for entry in ranking_details.get(ranking_name, []):
                if entry.get("symbol") != symbol:
                    continue
                rank = entry.get("rank")
                if not isinstance(rank, int):
                    continue
                ranking_count += 1
                best_rank = rank if best_rank is None else min(best_rank, rank)
                group_best_rank = rank if group_best_rank is None else min(group_best_rank, rank)
        if group_best_rank is not None:
            best_rank_by_group[group_name] = group_best_rank
    return {
        "symbol": symbol,
        "latest_close": _compact_number(row.get("latest_close")),
        "stage_evidence_groups": list(best_rank_by_group),
        "stage_ranking_count": ranking_count,
        "stage_best_rank": best_rank,
        "stage_best_rank_by_group": best_rank_by_group,
        "rank_delta_4w": _compact_number(row.get("rank_delta_4w")),
        "top_decile_age_weeks": row.get("top_decile_age_weeks"),
        "extension_ma50_pct": _compact_number(row.get("extension_ma50_pct")),
        "atr_extension_20d": _compact_number(row.get("atr_extension_20d")),
        "positive_day_ratio_3m": _compact_number(row.get("positive_day_ratio_3m")),
        "max_day_return_share_3m": _compact_number(row.get("max_day_return_share_3m")),
        "distance_to_252d_high_pct": _compact_number(row.get("distance_to_252d_high_pct")),
        "recent_vs_intermediate_momentum": _compact_number(row.get("recent_vs_intermediate_momentum")),
        "up_down_volume_ratio_60d": _compact_number(row.get("up_down_volume_ratio_60d")),
        "excess_return_vs_qqq_6m": _compact_number(row.get("excess_return_vs_qqq_6m")),
        "excess_return_vs_spy_6m": _compact_number(row.get("excess_return_vs_spy_6m")),
        "stage_warning_flags": list(row.get("stage_warning_flags") or []),
        "volatility_20": _compact_number(row.get("volatility_20")),
    }


def _select_momentum_stage_candidate_symbols(rankings: dict[str, list[str]], *, limit: int) -> list[str]:
    candidates = _unique_ranked_symbols_for_priority(rankings, MOMENTUM_STAGE_CANDIDATE_PRIORITY_RANKINGS)
    if len(candidates) <= limit:
        return candidates
    appearance_counts: dict[str, int] = {}
    best_rank: dict[str, int] = {}
    priority_best_rank: dict[str, int] = {}
    first_seen_order: dict[str, int] = {}
    official_rankings = {
        ranking_name
        for ranking_names in MOMENTUM_STAGE_RANK_GROUPS.values()
        for ranking_name in ranking_names
    }
    for ranking_name, ranking in rankings.items():
        for index, symbol in enumerate(ranking):
            first_seen_order.setdefault(symbol, len(first_seen_order))
            if ranking_name in official_rankings:
                appearance_counts[symbol] = appearance_counts.get(symbol, 0) + 1
            best_rank[symbol] = min(best_rank.get(symbol, index), index)
            if ranking_name in MOMENTUM_STAGE_CANDIDATE_PRIORITY_RANKINGS:
                priority_best_rank[symbol] = min(priority_best_rank.get(symbol, index), index)
    ordered = list(candidates)
    ordered.sort(
        key=lambda symbol: (
            -appearance_counts.get(symbol, 0),
            priority_best_rank.get(symbol, len(candidates)),
            best_rank.get(symbol, len(candidates)),
            first_seen_order.get(symbol, len(candidates)),
            symbol,
        )
    )
    return ordered[:limit]


def _unique_ranked_symbols_for_priority(rankings: dict[str, list[str]], priority: list[str]) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for ranking_name in priority:
        for symbol in rankings.get(ranking_name, []):
            if symbol not in seen:
                symbols.append(symbol)
                seen.add(symbol)
    for ranking in rankings.values():
        for symbol in ranking:
            if symbol not in seen:
                symbols.append(symbol)
                seen.add(symbol)
    return symbols
```

- [ ] **Step 7: Add rank-delta, top-decile, and benchmark helpers**

Add:

```python
def _add_rank_delta_and_top_decile_age(rows: list[dict[str, Any]], frames: dict[str, pd.DataFrame]) -> None:
    symbols = [str(row["symbol"]) for row in rows if row.get("symbol")]
    latest_ranks = _return_126_ranks_at_offset(frames, symbols, end_offset=0)
    prior_ranks = _return_126_ranks_at_offset(frames, symbols, end_offset=21)
    top_decile_age = _top_decile_age_weeks(frames, symbols)
    for row in rows:
        symbol = str(row.get("symbol") or "")
        latest_rank = latest_ranks.get(symbol)
        prior_rank = prior_ranks.get(symbol)
        if latest_rank is not None and prior_rank is not None:
            row["rank_delta_4w"] = prior_rank - latest_rank
        row["top_decile_age_weeks"] = top_decile_age.get(symbol)


def _return_126_ranks_at_offset(
    frames: dict[str, pd.DataFrame],
    symbols: list[str],
    *,
    end_offset: int,
) -> dict[str, int]:
    values = []
    for symbol in symbols:
        frame = frames.get(symbol)
        if frame is None:
            continue
        close = _numeric_series(_truncate_to_last_finite_close(_sort_frame(frame)), "close")
        value = _period_return_at_offset(close, window=126, end_offset=end_offset)
        if value is not None:
            values.append((symbol, value))
    values.sort(key=lambda item: (-item[1], item[0]))
    return {symbol: index for index, (symbol, _value) in enumerate(values, start=1)}


def _period_return_at_offset(series: pd.Series | None, *, window: int, end_offset: int) -> float | None:
    if series is None:
        return None
    end_index = len(series) - 1 - end_offset
    start_index = end_index - window
    if start_index < 0 or end_index < 0:
        return None
    start = _finite_float(series.iloc[start_index])
    end = _finite_float(series.iloc[end_index])
    if start in (None, 0) or end is None:
        return None
    return _finite_float(end / start - 1.0)


def _top_decile_age_weeks(frames: dict[str, pd.DataFrame], symbols: list[str]) -> dict[str, int | None]:
    ages = {symbol: 0 for symbol in symbols}
    active = set(symbols)
    if not symbols:
        return ages
    for week_index in range(0, 53):
        offset = week_index * 5
        ranks = _return_126_ranks_at_offset(frames, symbols, end_offset=offset)
        if not ranks:
            break
        cutoff = max(1, math.ceil(len(ranks) * 0.10))
        current_top = {symbol for symbol, rank in ranks.items() if rank <= cutoff}
        for symbol in list(active):
            if symbol in current_top:
                ages[symbol] += 1
            else:
                active.remove(symbol)
        if not active:
            break
    return ages
```

Add:

```python
def _add_benchmark_excess_returns(
    rows: list[dict[str, Any]],
    benchmark_summaries: dict[str, dict[str, Any]],
) -> None:
    benchmark_returns = {
        symbol.upper(): _compact_number(_dict(summary.get("momentum")).get("return_126"))
        for symbol, summary in benchmark_summaries.items()
        if isinstance(symbol, str) and isinstance(summary, dict)
    }
    qqq_return = benchmark_returns.get("QQQ")
    spy_return = benchmark_returns.get("SPY")
    for row in rows:
        stock_return = _summary_return_126(row)
        if stock_return is None:
            continue
        if qqq_return is not None:
            row["excess_return_vs_qqq_6m"] = _compact_number(stock_return - qqq_return)
        if spy_return is not None:
            row["excess_return_vs_spy_6m"] = _compact_number(stock_return - spy_return)


def _summary_return_126(row: dict[str, Any]) -> float | None:
    value = row.get("_return_126")
    return _finite_float(value)
```

In `_summary_to_momentum_stage_row()`, include an internal field:

```python
        "_return_126": _compact_number(_dict(summary.get("momentum")).get("return_126")),
```

In `_momentum_stage_candidate_summary_row()`, do not copy `_return_126` into the returned row.

Add:

```python
def _benchmark_context(benchmark_summaries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    returns = {}
    for symbol, summary in benchmark_summaries.items():
        if not isinstance(summary, dict):
            continue
        clean = str(symbol).strip().upper()
        if not clean:
            continue
        returns[clean] = _compact_number(_dict(summary.get("momentum")).get("return_126"))
    return {"symbols": sorted(returns), "return_126": returns}
```

Add:

```python
def _momentum_stage_warning_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    thresholds = MOMENTUM_STAGE_WARNING_THRESHOLDS
    top_age = _finite_float(row.get("top_decile_age_weeks"))
    if top_age is not None and top_age >= thresholds["stale_top_decile_weeks"]:
        flags.append("stale_top_decile")
    ma50_extension = _finite_float(row.get("extension_ma50_pct"))
    if ma50_extension is not None and ma50_extension >= thresholds["extreme_ma50_extension_pct"]:
        flags.append("extreme_ma50_extension")
    atr_extension = _finite_float(row.get("atr_extension_20d"))
    if atr_extension is not None and atr_extension >= thresholds["extreme_atr_extension"]:
        flags.append("extreme_atr_extension")
    recent_vs_intermediate = _finite_float(row.get("recent_vs_intermediate_momentum"))
    if recent_vs_intermediate is not None and recent_vs_intermediate >= thresholds["recent_overheat_vs_intermediate"]:
        flags.append("recent_overheat_vs_intermediate")
    jump_share = _finite_float(row.get("max_day_return_share_3m"))
    if jump_share is not None and jump_share >= thresholds["single_day_jump_share"]:
        flags.append("single_day_jump_concentration")
    qqq_excess = _finite_float(row.get("excess_return_vs_qqq_6m"))
    spy_excess = _finite_float(row.get("excess_return_vs_spy_6m"))
    if qqq_excess is not None and spy_excess is not None and qqq_excess < 0 and spy_excess < 0:
        flags.append("benchmark_lag")
    volume_ratio = _finite_float(row.get("up_down_volume_ratio_60d"))
    if volume_ratio is None or volume_ratio < thresholds["weak_up_down_volume_ratio"]:
        flags.append("thin_or_missing_volume_support")
    required = (
        "rank_delta_4w",
        "positive_day_ratio_3m",
        "max_day_return_share_3m",
        "distance_to_252d_high_pct",
        "up_down_volume_ratio_60d",
    )
    if any(row.get(key) is None for key in required):
        flags.append("insufficient_history")
    return flags
```

- [ ] **Step 8: Run history-summary tests**

Run:

```powershell
python -m pytest tests/test_agent_history_summary.py -q
```

Expected:

```text
passed
```

If legacy tests fail because `build_universe_history_summary()` now returns `evidence_profile`, update those tests to assert the new legacy field while preserving all old ranking expectations.

- [ ] **Step 9: Commit Task 2**

Run:

```powershell
git add lumibot\components\agents\history_summary.py tests\test_agent_history_summary.py
git diff --cached --stat
git commit -m "feat: add momentum stage history summary profile"
```

Expected staged files:

```text
lumibot/components/agents/history_summary.py
tests/test_agent_history_summary.py
```

---

## Task 3: Wire Tool Parameters And Equity-Scoped Defaults

**Files:**

- Modify: `lumibot/components/agents/duckdb_tools.py`
- Modify: `lumibot/components/agents/builtins.py`
- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
- Modify: `tests/test_agent_manager.py`
- Modify: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Add failing built-in tool parameter tests**

In `tests/test_agent_manager.py`, update `test_market_load_history_tables_summary_tool_accepts_rank_limits()`:

```python
    assert "evidence_profile" in signature.parameters
    assert signature.parameters["evidence_profile"].default == "legacy"
    assert "benchmark_symbols" in signature.parameters
    assert signature.parameters["benchmark_symbols"].default is None
```

Update `test_market_load_history_tables_summary_tool_forwards_and_validates_rank_limits()`:

```python
    assert tool.function(
        symbols=["MSFT"],
        top_n=7,
        candidate_summary_limit=11,
        evidence_profile="momentum_stage",
        benchmark_symbols=["QQQ", "SPY"],
    ) == {"ok": True}
    assert captured["top_n"] == 7
    assert captured["candidate_summary_limit"] == 11
    assert captured["evidence_profile"] == "momentum_stage"
    assert captured["benchmark_symbols"] == ["QQQ", "SPY"]
```

Also add:

```python
    with pytest.raises(ValueError, match="benchmark_symbols"):
        tool.function(symbols=["MSFT"], benchmark_symbols=[""])
```

- [ ] **Step 2: Add failing equity-scoped default test**

In `tests/test_ai_trading_team_equity_only_llm.py`, add:

```python
def test_equity_history_summary_tool_defaults_to_momentum_stage_profile():
    helpers = importlib.import_module("lumibot.example_strategies.ai_trading_team_equity_only_helpers")
    captured = {}

    class FakeDuckDB:
        def load_history_tables_summary(self, **kwargs):
            captured.update(kwargs)
            return {"ok": True}

    tool_definition = helpers.equity_basket_agent_tools()[0]
    assert tool_definition.name == "market_load_history_tables_summary"
    tool = tool_definition.binder(DummyStrategy(), SimpleNamespace(duckdb=FakeDuckDB()))

    assert tool.function(symbols=["AAPL", "MSFT"]) == {"ok": True}
    assert captured["evidence_profile"] == "momentum_stage"
    assert captured["benchmark_symbols"] == ["QQQ", "SPY"]
```

If `DummyStrategy` is not available in this test file, add a tiny local class near nearby helper test classes:

```python
class DummyStrategy:
    pass
```

- [ ] **Step 3: Run the new failing tests**

Run:

```powershell
python -m pytest tests/test_agent_manager.py::test_market_load_history_tables_summary_tool_accepts_rank_limits tests/test_agent_manager.py::test_market_load_history_tables_summary_tool_forwards_and_validates_rank_limits tests/test_ai_trading_team_equity_only_llm.py::test_equity_history_summary_tool_defaults_to_momentum_stage_profile -q
```

Expected before implementation:

```text
FAILED ... evidence_profile
FAILED ... benchmark_symbols
```

- [ ] **Step 4: Implement `duckdb_tools.py` parameter forwarding**

Update `DuckDBQueryLayer.load_history_tables_summary()` signature:

```python
        evidence_profile: str = "legacy",
        benchmark_symbols: list[str] | None = None,
```

Add `frames_by_symbol`, `benchmark_summaries`, `benchmark_frames`, and `benchmark_loaded_tables`:

```python
        profile = str(evidence_profile or "legacy").strip().lower()
        benchmark_symbols = [str(symbol).strip().upper() for symbol in (benchmark_symbols or []) if str(symbol).strip()]
        frames_by_symbol: dict[str, pd.DataFrame] = {}
        benchmark_summaries: dict[str, dict[str, Any]] = {}
        benchmark_loaded_tables: dict[str, str] = {}
```

After each candidate table load succeeds:

```python
            table_name_value = str(table_info.get("table_name") or "")
            if table_name_value:
                loaded_tables[symbol] = table_name_value
                if profile == "momentum_stage":
                    frames_by_symbol[symbol] = self._read_registered_table(table_name_value)
            else:
                loaded_tables[symbol] = ""
```

After the candidate loop and before calling `build_universe_history_summary()`, load benchmarks when `profile == "momentum_stage"`:

```python
        if profile == "momentum_stage":
            for benchmark_symbol in benchmark_symbols:
                if not benchmark_symbol:
                    continue
                try:
                    table_info = self.load_history_table(
                        symbol=benchmark_symbol,
                        length=length,
                        timestep=timestep,
                        asset_type=asset_type,
                        include_after_hours=include_after_hours,
                    )
                except Exception as exc:
                    warnings.append(f"{benchmark_symbol}: failed to load benchmark history table: {exc}")
                    continue
                summary = table_info.get("computed_summary")
                if isinstance(summary, dict):
                    benchmark_summaries[benchmark_symbol] = summary
                table_name_value = str(table_info.get("table_name") or "")
                if table_name_value:
                    benchmark_loaded_tables[benchmark_symbol] = table_name_value
```

Update the builder call:

```python
            evidence_profile=profile,
            history_frames=frames_by_symbol,
            benchmark_summaries=benchmark_summaries,
```

After the builder returns, add benchmark table metadata without mixing it into candidate `loaded_tables`:

```python
        if benchmark_loaded_tables:
            result["benchmark_loaded_tables"] = benchmark_loaded_tables
```

- [ ] **Step 5: Implement built-in tool parameters**

In `_bind_load_history_tables_summary()` in `lumibot/components/agents/builtins.py`, update the inner function signature:

```python
        evidence_profile: str = "legacy",
        benchmark_symbols: list[str] | None = None,
```

Validate and forward:

```python
        evidence_profile = _require_non_empty_text("evidence_profile", evidence_profile).strip().lower()
        normalized_benchmark_symbols = None
        if benchmark_symbols is not None:
            if not isinstance(benchmark_symbols, list):
                raise ValueError("benchmark_symbols must be a list of symbols.")
            normalized_benchmark_symbols = [
                _require_single_symbol_text("benchmark_symbols", symbol)
                for symbol in benchmark_symbols
            ]
```

Forward:

```python
            evidence_profile=evidence_profile,
            benchmark_symbols=normalized_benchmark_symbols,
```

Update the description to include:

```text
Arguments also include optional evidence_profile ('legacy' or 'momentum_stage') and optional benchmark_symbols for profiles that need benchmark-relative evidence.
When evidence_profile='momentum_stage', the result returns stage rankings for freshness, smoothness, near-high strength, volume confirmation, and benchmark-relative strength plus reference fields for stale or overextended candidates.
DuckDB SQL is only a targeted follow-up when the summary is missing or contradictory.
```

Remove the sentence that says the tool always returns the old five evidence groups.

- [ ] **Step 6: Add equity-scoped history-summary wrapper**

In `ai_trading_team_equity_only_helpers.py`, add:

```python
EQUITY_HISTORY_SUMMARY_DESCRIPTION = (
    "Load visible historical bars for assigned basket_symbols and return the momentum_stage evidence profile. "
    "Arguments: symbols, optional length, timestep, asset_type, table_prefix, include_after_hours, top_n, "
    "candidate_summary_limit, evidence_profile, and benchmark_symbols. For this equity-only strategy, "
    "evidence_profile defaults to 'momentum_stage' and benchmark_symbols defaults to ['QQQ', 'SPY']. "
    "This tool returns stage evidence, not a single best-stock answer. Rankable lists identify freshness, "
    "smoothness, near-high strength, volume support, and benchmark-relative strength. Reference fields identify "
    "stale or overextended candidates and are not 'higher is always better' fields. Prefer candidates supported "
    "by multiple stage evidence groups and not blocked by strong warning flags. Use DuckDB SQL only as a rare "
    "follow-up when this summary is missing or contradictory."
)
```

Add:

```python
def equity_history_summary_tool() -> ToolDefinition:
    base_tool = BuiltinTools.market.load_history_tables_summary()

    def _bind_equity_history_summary(strategy: Any, manager: Any) -> BoundTool:
        bound = base_tool.binder(strategy, manager)
        metadata = dict(bound.metadata or {})
        metadata["scope"] = "equity_only"

        def market_load_history_tables_summary(
            *,
            symbols: list[str],
            length: int = 378,
            timestep: str = "day",
            asset_type: str = "stock",
            table_prefix: str | None = None,
            include_after_hours: bool = True,
            top_n: int = 10,
            candidate_summary_limit: int = 25,
            evidence_profile: str = "momentum_stage",
            benchmark_symbols: list[str] | None = None,
        ) -> dict[str, Any]:
            return bound.function(
                symbols=symbols,
                length=length,
                timestep=timestep,
                asset_type=asset_type,
                table_prefix=table_prefix,
                include_after_hours=include_after_hours,
                top_n=top_n,
                candidate_summary_limit=candidate_summary_limit,
                evidence_profile=evidence_profile,
                benchmark_symbols=benchmark_symbols or ["QQQ", "SPY"],
            )

        return BoundTool(
            name=bound.name,
            description=EQUITY_HISTORY_SUMMARY_DESCRIPTION,
            function=market_load_history_tables_summary,
            source=bound.source,
            metadata=metadata,
        )

    metadata = dict(base_tool.metadata or {})
    metadata["scope"] = "equity_only"
    return ToolDefinition(
        name=base_tool.name,
        description=EQUITY_HISTORY_SUMMARY_DESCRIPTION,
        binder=_bind_equity_history_summary,
        metadata=metadata,
    )
```

Change `equity_basket_agent_tools()`:

```python
def equity_basket_agent_tools() -> list[ToolDefinition]:
    return [
        equity_history_summary_tool(),
        BuiltinTools.market.last_price(),
        equity_alpaca_news_tool(),
    ]
```

- [ ] **Step 7: Run tool wiring tests**

Run:

```powershell
python -m pytest tests/test_agent_manager.py tests/test_ai_trading_team_equity_only_llm.py::test_equity_history_summary_tool_defaults_to_momentum_stage_profile -q
```

Expected:

```text
passed
```

- [ ] **Step 8: Commit Task 3**

Run:

```powershell
git add lumibot\components\agents\duckdb_tools.py lumibot\components\agents\builtins.py lumibot\example_strategies\ai_trading_team_equity_only_helpers.py tests\test_agent_manager.py tests\test_ai_trading_team_equity_only_llm.py
git diff --cached --stat
git commit -m "feat: wire momentum stage profile through history summary tool"
```

---

## Task 4: Rewrite Equity Evidence Prompts

**Files:**

- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
- Modify: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Add failing prompt cleanup tests**

In `tests/test_ai_trading_team_equity_only_llm.py`, update or add:

```python
def test_equity_prompts_use_momentum_stage_profile_and_remove_old_rank_anchors():
    helpers = importlib.import_module("lumibot.example_strategies.ai_trading_team_equity_only_helpers")
    prompts = [
        helpers.equity_basket_agent_system_prompt("AAPL, MSFT, NVDA"),
        helpers.qqq_historical_equity_basket_agent_system_prompt("AAPL, MSFT, NVDA"),
        helpers.equity_basket_agent_task_prompt(),
    ]
    joined = " ".join(prompts).lower()

    for required in (
        "momentum-stage",
        "confirmed but not exhausted",
        "freshness",
        "smoothness",
        "near-high strength",
        "volume confirmation",
        "benchmark-relative strength",
        "reference fields",
        "stale",
        "overextended",
        "alpaca_news only",
        "return strict json",
    ):
        assert required in joined

    for forbidden in (
        "five rank groups",
        "five evidence groups",
        "momentum_composite",
        "composite_score",
        "highest raw momentum",
        "breakout / near-high evidence as timing and leadership confirmation",
        "compare the five rank groups",
    ):
        assert forbidden not in joined
```

Update the existing `test_equity_agent_task_prompt_teaches_summary_first_top_n_and_strict_json()` expectations:

```python
    assert "length=378" in task_prompt
    assert "evidence_profile='momentum_stage'" in task_prompt
    assert "benchmark_symbols=['qqq', 'spy']" in task_prompt
    assert "stage evidence" in task_prompt
    assert "old momentum" not in task_prompt
    assert "composite_score" not in task_prompt
    assert "momentum_composite" not in task_prompt
```

- [ ] **Step 2: Run failing prompt tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_prompts_use_momentum_stage_profile_and_remove_old_rank_anchors tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_task_prompt_teaches_summary_first_top_n_and_strict_json -q
```

Expected before prompt rewrite:

```text
FAILED ... old prompt still contains five rank groups or length=252
```

- [ ] **Step 3: Replace `EQUITY_EVIDENCE_INTERPRETATION_POLICY`**

Replace the constant with:

```python
EQUITY_EVIDENCE_INTERPRETATION_POLICY = (
    "Evidence Interpretation Policy: "
    "Use momentum-stage evidence to find confirmed but not exhausted strength. "
    "Treat rankable groups as support votes, not as an automatic answer. "
    "Prefer candidates appearing in multiple stage groups: freshness, smoothness, near-high strength, "
    "volume confirmation, and benchmark-relative strength. "
    "Use reference fields as risk checks: stale top-decile age, MA50 extension, ATR extension, "
    "single-day jump concentration, and recent overheat versus intermediate momentum. "
    "Reference fields are not simple ranking fields and higher is not always better. "
    "If stage evidence is clear, select without news. "
    "Use alpaca_news only for leading candidates when evidence is close, conflicting, or catalyst-sensitive."
)
```

- [ ] **Step 4: Rewrite equity system prompts**

Replace the body of `equity_basket_agent_system_prompt()` with:

```python
    return (
        f"Equity-only selection role: identify credible stock candidates from the assigned basket_symbols ({symbols}). "
        "Your job is to select candidates with confirmed but not exhausted momentum. "
        "selected_symbols should contain between 3 and 10 unique symbols. "
        "You cannot place orders or size trades. You cannot calculate exact per-symbol target weights. "
        "Existing positions are protected by a deterministic local exit-risk engine. "
        "Do not calculate stop-loss, do not calculate take-profit, and do not create exit orders. "
        "The downstream deterministic constructor chooses the final holding count and target weights from "
        "candidate evidence plus local rank, warning, and volatility data. "
        "Use market_load_history_tables_summary first and request evidence_profile='momentum_stage'. "
        f"{EQUITY_EVIDENCE_INTERPRETATION_POLICY} "
        "Do not invent sector, style, or category labels not provided by tools. "
        "If news is unavailable, continue with stage evidence. Return strict JSON only. Do not place orders."
    )
```

Replace the QQQ historical prompt body with:

```python
    return (
        "Equity-only selection role: identify credible stock candidates from the QQQ historical constituent "
        f"universe provided in basket_symbols ({symbols}). "
        "The provided basket_symbols represent the QQQ historical constituent universe available for the "
        "current backtest date. Your job is to select candidates with confirmed but not exhausted momentum. "
        "selected_symbols should contain between 3 and 10 unique symbols. "
        "You cannot place orders or size trades. You cannot calculate exact per-symbol target weights. "
        "Existing positions are protected by a deterministic local exit-risk engine. "
        "Do not calculate stop-loss, do not calculate take-profit, and do not create exit orders. "
        "The downstream deterministic constructor chooses the final holding count and target weights from "
        "candidate evidence plus local rank, warning, and volatility data. "
        "Use market_load_history_tables_summary first and request evidence_profile='momentum_stage'. "
        f"{EQUITY_EVIDENCE_INTERPRETATION_POLICY} "
        "Do not invent sector, style, or category labels not provided by tools. "
        "Do not assume QQQ membership itself makes a stock superior; select from current stage evidence. "
        "Do not choose based on index weight alone. "
        "If news is unavailable, continue with stage evidence. Use only symbols in the provided basket_symbols "
        "and do not add symbols outside the provided universe. Return strict JSON only. Do not place orders."
    )
```

- [ ] **Step 5: Rewrite `equity_basket_agent_task_prompt()`**

Replace its body with:

```python
    return (
        "Review only the provided basket_symbols. First call market_load_history_tables_summary with "
        "symbols=basket_symbols, length=378, timestep='day', top_n=10, candidate_summary_limit=25, "
        "evidence_profile='momentum_stage', and benchmark_symbols=['QQQ', 'SPY']. "
        "Build an ordered list of credible candidates from the momentum-stage evidence profile. "
        "selected_symbols must contain between 3 and 10 unique symbols from basket_symbols. "
        "Prefer fewer selected_symbols when evidence is narrow and cleaner, and more when credible evidence is broad. "
        "Prefer candidates supported by multiple stage groups and not blocked by strong warning flags. "
        "Use reference fields to avoid stale, overextended, or single-jump candidates. "
        "Do not calculate exact per-symbol target weights; the deterministic constructor calculates the final "
        "holding count and target weights. "
        "If the leading candidate set is clear from stage evidence, select without news. "
        "If leading candidates are close, conflicting, overextended, stale, or catalyst-sensitive, call alpaca_news "
        "for those leading candidates only. If alpaca_news is unavailable or errors, continue with stage evidence. "
        "Return exactly one strict JSON object with basket_id, status, candidate_symbols, selected_symbols, "
        "and reason_brief. Use status='active'. candidate_symbols must copy the assigned basket_symbols exactly; "
        "do not replace it with a shortlist."
    )
```

- [ ] **Step 6: Run prompt tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q
```

Expected:

```text
passed
```

- [ ] **Step 7: Commit Task 4**

Run:

```powershell
git add lumibot\example_strategies\ai_trading_team_equity_only_helpers.py tests\test_ai_trading_team_equity_only_llm.py
git diff --cached --stat
git commit -m "refactor: rewrite equity prompts for momentum stage evidence"
```

---

## Task 5: Update Dynamic Equity Constructor For Stage Evidence

**Files:**

- Modify: `lumibot/example_strategies/dynamic_equity_portfolio_constructor.py`
- Modify: `tests/test_dynamic_equity_portfolio_constructor.py`

- [ ] **Step 1: Add failing constructor tests**

In `tests/test_dynamic_equity_portfolio_constructor.py`, add helpers:

```python
def stage_candidate(
    symbol,
    *,
    freshness=1,
    smoothness=1,
    near_high=1,
    volume_confirmation=1,
    relative_strength=1,
    stage_ranking_count=5,
    warnings=None,
    volatility_20=0.02,
):
    groups = {
        "freshness": freshness,
        "smoothness": smoothness,
        "near_high": near_high,
        "volume_confirmation": volume_confirmation,
        "relative_strength": relative_strength,
    }
    return {
        "symbol": symbol,
        "stage_best_rank_by_group": groups,
        "stage_ranking_count": stage_ranking_count,
        "stage_best_rank": min(groups.values()),
        "stage_warning_flags": list(warnings or []),
        "volatility_20": volatility_20,
    }


def stage_summary(rows, *, ranking_limit=10):
    return {
        "evidence_profile": "momentum_stage",
        "ranking_limit": ranking_limit,
        "candidate_summary": rows,
        "rank_groups": {
            "freshness": ["by_rank_delta_4w"],
            "smoothness": ["by_positive_day_ratio_3m", "by_low_max_day_return_share_3m"],
            "near_high": ["by_near_252d_high"],
            "volume_confirmation": ["by_up_down_volume_ratio_60d"],
            "relative_strength": ["by_excess_return_vs_qqq_6m", "by_excess_return_vs_spy_6m"],
        },
    }
```

Add:

```python
def test_constructor_uses_momentum_stage_scores_and_diagnostics():
    equity_report = {
        "basket_id": "equity",
        "status": "active",
        "selected_symbols": ["AAA", "BBB", "CCC", "DDD"],
    }
    market_summary = stage_summary(
        [
            stage_candidate("AAA", freshness=1, relative_strength=1),
            stage_candidate("BBB", freshness=2, relative_strength=2, warnings=["extreme_ma50_extension"]),
            stage_candidate("CCC", freshness=4, relative_strength=4),
            stage_candidate("DDD", freshness=8, relative_strength=8),
        ]
    )

    result = construct_dynamic_equity_target_portfolio(
        equity_report,
        equity_universe=["AAA", "BBB", "CCC", "DDD"],
        market_summary=market_summary,
    )

    assert result["weighting_method"] == "momentum_stage_score_with_volatility_adjustment"
    score_rows = {row["symbol"]: row for row in result["diagnostics"]["candidate_scores"]}
    assert score_rows["AAA"]["stage_support_score"] > score_rows["BBB"]["stage_support_score"]
    assert score_rows["BBB"]["stage_penalty"] > 0
    assert score_rows["BBB"]["adjusted_stage_score"] < score_rows["BBB"]["stage_support_score"]
    assert "stage_warning_flags" in score_rows["AAA"]
```

Add:

```python
def test_constructor_prefers_stage_fields_over_legacy_fields_when_profile_is_momentum_stage():
    equity_report = {
        "basket_id": "equity",
        "status": "active",
        "selected_symbols": ["AAA", "BBB", "CCC"],
    }
    row_a = stage_candidate("AAA", freshness=9, relative_strength=9)
    row_a["best_rank_by_group"] = {"momentum": 1, "trend_quality": 1}
    row_b = stage_candidate("BBB", freshness=1, relative_strength=1)
    row_b["best_rank_by_group"] = {"momentum": 9, "trend_quality": 9}
    row_c = stage_candidate("CCC", freshness=3, relative_strength=3)
    market_summary = stage_summary([row_a, row_b, row_c])

    result = construct_dynamic_equity_target_portfolio(
        equity_report,
        equity_universe=["AAA", "BBB", "CCC"],
        market_summary=market_summary,
    )

    assert result["diagnostics"]["candidate_scores"][0]["symbol"] == "BBB"
```

- [ ] **Step 2: Run failing constructor tests**

Run:

```powershell
python -m pytest tests/test_dynamic_equity_portfolio_constructor.py::test_constructor_uses_momentum_stage_scores_and_diagnostics tests/test_dynamic_equity_portfolio_constructor.py::test_constructor_prefers_stage_fields_over_legacy_fields_when_profile_is_momentum_stage -q
```

Expected before implementation:

```text
FAILED ... weighting_method
FAILED ... stage_support_score
```

- [ ] **Step 3: Add stage weights and penalties**

In `dynamic_equity_portfolio_constructor.py`, add below `GROUP_WEIGHTS`:

```python
STAGE_GROUP_WEIGHTS = {
    "freshness": 0.22,
    "smoothness": 0.20,
    "near_high": 0.14,
    "volume_confirmation": 0.14,
    "relative_strength": 0.30,
}

STAGE_WARNING_PENALTIES = {
    "stale_top_decile": 0.12,
    "extreme_ma50_extension": 0.12,
    "extreme_atr_extension": 0.12,
    "recent_overheat_vs_intermediate": 0.08,
    "single_day_jump_concentration": 0.10,
    "benchmark_lag": 0.15,
    "thin_or_missing_volume_support": 0.06,
    "insufficient_history": 0.10,
}
```

- [ ] **Step 4: Replace single float score helper with profile-aware score payload**

Add:

```python
def _evidence_score_payload(row: dict[str, Any], ranking_limit: float, evidence_profile: str | None) -> dict[str, Any]:
    if evidence_profile == "momentum_stage" or isinstance(row.get("stage_best_rank_by_group"), dict):
        return _stage_evidence_score_payload(row, ranking_limit)
    legacy_score = _legacy_evidence_score(row, ranking_limit)
    return {
        "evidence_score": legacy_score,
        "stage_support_score": None,
        "stage_penalty": None,
        "adjusted_stage_score": None,
        "stage_warning_flags": [],
    }


def _stage_evidence_score_payload(row: dict[str, Any], ranking_limit: float) -> dict[str, Any]:
    best_rank_by_group = row.get("stage_best_rank_by_group")
    if not isinstance(best_rank_by_group, dict):
        best_rank_by_group = {}
    support_score = 0.0
    for group_name, group_weight in STAGE_GROUP_WEIGHTS.items():
        rank = _positive_number(best_rank_by_group.get(group_name))
        if rank is None:
            continue
        group_score = 1.0 - ((rank - 1.0) / max(1.0, ranking_limit - 1.0))
        support_score += group_weight * _clamp(group_score, 0.0, 1.0)
    ranking_count = _positive_number(row.get("stage_ranking_count")) or 0.0
    support_score += min(0.10, ranking_count * 0.01)
    warning_flags = [str(flag) for flag in row.get("stage_warning_flags") or []]
    penalty = min(0.50, sum(STAGE_WARNING_PENALTIES.get(flag, 0.0) for flag in warning_flags))
    adjusted_stage_score = _clamp(support_score - penalty, 0.0, 1.0)
    return {
        "evidence_score": adjusted_stage_score,
        "stage_support_score": support_score,
        "stage_penalty": penalty,
        "adjusted_stage_score": adjusted_stage_score,
        "stage_warning_flags": warning_flags,
    }
```

Rename the existing `_evidence_score()` to `_legacy_evidence_score()` and keep its body unchanged.

- [ ] **Step 5: Use the payload in `_scored_candidates()`**

Inside `_scored_candidates()`, before row iteration, add:

```python
        evidence_profile = str(market_summary.get("evidence_profile") or "").strip().lower()
```

Replace:

```python
                candidate["evidence_score"] = _evidence_score(row, ranking_limit)
```

with:

```python
                score_payload = _evidence_score_payload(row, ranking_limit, evidence_profile)
                candidate.update(score_payload)
```

Update `_candidate()` default:

```python
        "stage_support_score": None,
        "stage_penalty": None,
        "adjusted_stage_score": None,
        "stage_warning_flags": [],
```

- [ ] **Step 6: Update weighting method and score rows**

In `construct_dynamic_equity_target_portfolio()`, compute:

```python
    evidence_profile = str(market_summary.get("evidence_profile") or "").strip().lower() if isinstance(market_summary, dict) else ""
    weighting_method = (
        "momentum_stage_score_with_volatility_adjustment"
        if evidence_profile == "momentum_stage"
        else "evidence_score_with_volatility_adjustment"
    )
```

Replace the return field:

```python
        "weighting_method": weighting_method,
```

Update `_candidate_score_rows()` to include:

```python
            "stage_support_score": _round_or_none(candidate.get("stage_support_score")),
            "stage_penalty": _round_or_none(candidate.get("stage_penalty")),
            "adjusted_stage_score": _round_or_none(candidate.get("adjusted_stage_score")),
            "stage_warning_flags": list(candidate.get("stage_warning_flags") or []),
```

Add helper:

```python
def _round_or_none(value: Any) -> float | None:
    number = _positive_or_zero_number(value)
    if number is None:
        return None
    return round(number, 6)


def _positive_or_zero_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0.0:
        return None
    return number
```

- [ ] **Step 7: Run constructor tests**

Run:

```powershell
python -m pytest tests/test_dynamic_equity_portfolio_constructor.py -q
```

Expected:

```text
passed
```

- [ ] **Step 8: Commit Task 5**

Run:

```powershell
git add lumibot\example_strategies\dynamic_equity_portfolio_constructor.py tests\test_dynamic_equity_portfolio_constructor.py
git diff --cached --stat
git commit -m "feat: score dynamic equity portfolios from stage evidence"
```

---

## Task 6: Update Replay Formatter For Stage Evidence

**Files:**

- Modify: `lumibot/components/agents/replay_ui/formatters.py`
- Modify: `tests/test_agent_replay_ui_formatters.py`

- [ ] **Step 1: Add failing formatter test**

Append to `tests/test_agent_replay_ui_formatters.py`:

```python
def test_market_load_history_tables_summary_formatter_shows_momentum_stage_profile():
    explanation = explain_tool_result(
        "market_load_history_tables_summary",
        {"symbols": ["AAA", "BBB"], "evidence_profile": "momentum_stage"},
        {
            "evidence_profile": "momentum_stage",
            "coverage": {
                "requested_count": 2,
                "loaded_count": 2,
                "failed_count": 0,
                "top_n": 2,
                "candidate_summary_limit": 2,
                "ranking_count": 2,
            },
            "rank_groups": {
                "freshness": ["by_rank_delta_4w"],
                "relative_strength": ["by_excess_return_vs_qqq_6m"],
            },
            "ranking_details": {
                "by_rank_delta_4w": [{"rank": 1, "symbol": "AAA", "value": 10}],
                "by_excess_return_vs_qqq_6m": [{"rank": 1, "symbol": "BBB", "value": 0.12}],
            },
            "candidate_summary": [
                {"symbol": "AAA", "stage_warning_flags": ["extreme_atr_extension"]},
                {"symbol": "BBB", "stage_warning_flags": []},
            ],
            "benchmark_context": {"symbols": ["QQQ", "SPY"], "return_126": {"QQQ": 0.08, "SPY": 0.04}},
            "warnings": [],
        },
        None,
    )

    assert "momentum_stage" in explanation
    assert "freshness" in explanation
    assert "by_rank_delta_4w: AAA=10" in explanation
    assert "relative_strength" in explanation
    assert "QQQ" in explanation
    assert "SPY" in explanation
    assert "extreme_atr_extension" in explanation
```

- [ ] **Step 2: Run failing formatter test**

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_formatters.py::test_market_load_history_tables_summary_formatter_shows_momentum_stage_profile -q
```

Expected before implementation:

```text
FAILED ... momentum_stage
```

- [ ] **Step 3: Update `_market_load_history_tables_summary()`**

In `formatters.py`, after `result = _as_dict(raw_result)`, add:

```python
    evidence_profile = result.get("evidence_profile")
    if evidence_profile:
        parts.append(f"evidence profile {evidence_profile}")
```

After the ranking-group section, add benchmark and warning flag summaries:

```python
    benchmark_context = _as_dict(result.get("benchmark_context"))
    benchmark_returns = _as_dict(benchmark_context.get("return_126"))
    if benchmark_returns:
        pairs = [f"{symbol}={value}" for symbol, value in sorted(benchmark_returns.items())]
        parts.append("benchmark 126-bar returns: " + ", ".join(pairs))

    stage_flags: list[str] = []
    for row in candidate_summary:
        row_dict = _as_dict(row)
        symbol = row_dict.get("symbol")
        flags = row_dict.get("stage_warning_flags")
        if symbol and isinstance(flags, list) and flags:
            stage_flags.append(f"{symbol}: {', '.join(str(flag) for flag in flags)}")
    if stage_flags:
        parts.append("stage warning flags: " + " | ".join(stage_flags[:5]))
```

Keep the existing legacy fallback that shows `return_63`, `momentum_composite`, and `composite_score` only when no `rank_groups` / `ranking_details` are available.

- [ ] **Step 4: Run formatter tests**

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_formatters.py -q
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit Task 6**

Run:

```powershell
git add lumibot\components\agents\replay_ui\formatters.py tests\test_agent_replay_ui_formatters.py
git diff --cached --stat
git commit -m "feat: show momentum stage evidence in replay formatter"
```

---

## Task 7: Full Local Regression

**Files:**

- Read/verify only unless failures require fixes:
  - `lumibot/components/agents/history_summary.py`
  - `lumibot/components/agents/duckdb_tools.py`
  - `lumibot/components/agents/builtins.py`
  - `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
  - `lumibot/example_strategies/dynamic_equity_portfolio_constructor.py`
  - `lumibot/components/agents/replay_ui/formatters.py`

- [ ] **Step 1: Run focused pytest suite**

Run:

```powershell
python -m pytest `
  tests/test_agent_history_summary.py `
  tests/test_agent_manager.py `
  tests/test_ai_trading_team_equity_only_llm.py `
  tests/test_dynamic_equity_portfolio_constructor.py `
  tests/test_agent_replay_ui_formatters.py `
  -q
```

Expected:

```text
passed
```

- [ ] **Step 2: Run ruff on changed files**

Run:

```powershell
python -m ruff check `
  lumibot/components/agents/history_summary.py `
  lumibot/components/agents/duckdb_tools.py `
  lumibot/components/agents/builtins.py `
  lumibot/example_strategies/ai_trading_team_equity_only_helpers.py `
  lumibot/example_strategies/dynamic_equity_portfolio_constructor.py `
  lumibot/components/agents/replay_ui/formatters.py `
  tests/test_agent_history_summary.py `
  tests/test_agent_manager.py `
  tests/test_ai_trading_team_equity_only_llm.py `
  tests/test_dynamic_equity_portfolio_constructor.py `
  tests/test_agent_replay_ui_formatters.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 3: Run smoke backtest**

Run:

```powershell
python scripts\run_ai_trading_team_examples_benchmark.py `
  --strategy qqq-historical-equity-only-llm `
  --start 2024-09-05 `
  --end 2024-09-06 `
  --run-frequency weekly `
  --weekly-run-weekday THU `
  --env-file D:\Lumibot\project_notes\API.txt `
  --max-workers 1 `
  --max-run-attempts 1 `
  --agent-run-timeout-seconds 240
```

Expected runner output includes:

```json
{"strategy": "qqq-historical-equity-only-llm", "status": "passed"}
```

If `project_notes\API.txt` is not present in this worktree, use the absolute path shown above and do not open or print the file content.

- [ ] **Step 4: Inspect the newest artifact for stage evidence**

Run:

```powershell
$latest = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
$artifact = Join-Path $latest.FullName "qqq-historical-equity-only-llm"
@"
import json
from pathlib import Path

artifact = Path(r"$artifact")
trace_root = artifact / "cache" / "agent_runtime" / "traces" / "equity_basket_agent"
trace_files = sorted(trace_root.glob("*.json"))
if not trace_files:
    raise SystemExit("No equity_basket_agent trace files found.")
found_summary = False
found_old_rank = False
for path in trace_files:
    payload = json.loads(path.read_text(encoding="utf-8"))
    text = json.dumps(payload, sort_keys=True)
    if '"evidence_profile": "momentum_stage"' in text or '"evidence_profile":"momentum_stage"' in text:
        found_summary = True
    if "by_composite_score" in text or "by_momentum_composite" in text:
        found_old_rank = True
print(json.dumps({"artifact": str(artifact), "found_momentum_stage": found_summary, "found_old_rank": found_old_rank}, indent=2))
if not found_summary:
    raise SystemExit("momentum_stage evidence profile was not found in equity trace.")
if found_old_rank:
    raise SystemExit("old composite ranking names appeared in equity trace.")
"@ | python -
```

Expected:

```json
{
  "found_momentum_stage": true,
  "found_old_rank": false
}
```

- [ ] **Step 5: Inspect the replay UI manually**

Run:

```powershell
python scripts\agent_trace_ui.py
```

Open:

```text
http://127.0.0.1:8765
```

Select the newest `qqq-historical-equity-only-llm` run. Verify:

- `equity_basket_agent` tool call includes `market_load_history_tables_summary`.
- Tool input includes `evidence_profile='momentum_stage'` and `benchmark_symbols=['QQQ', 'SPY']`.
- Tool output includes `rank_groups` with `freshness`, `smoothness`, `near_high`, `volume_confirmation`, and `relative_strength`.
- Candidate rows include `stage_warning_flags`.
- The formatter explanation mentions `momentum_stage`.

- [ ] **Step 6: Create validation note after successful checks**

After Step 1, Step 2, Step 3, and Step 4 pass, run:

```powershell
$latest = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
$artifact = Join-Path $latest.FullName "qqq-historical-equity-only-llm"
$note = @"
# Momentum Stage Evidence Rank Layer Validation

## Commands

- ``python -m pytest tests/test_agent_history_summary.py tests/test_agent_manager.py tests/test_ai_trading_team_equity_only_llm.py tests/test_dynamic_equity_portfolio_constructor.py tests/test_agent_replay_ui_formatters.py -q``
- ``python -m ruff check lumibot/components/agents/history_summary.py lumibot/components/agents/duckdb_tools.py lumibot/components/agents/builtins.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py lumibot/example_strategies/dynamic_equity_portfolio_constructor.py lumibot/components/agents/replay_ui/formatters.py tests/test_agent_history_summary.py tests/test_agent_manager.py tests/test_ai_trading_team_equity_only_llm.py tests/test_dynamic_equity_portfolio_constructor.py tests/test_agent_replay_ui_formatters.py``
- ``python scripts/run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2024-09-05 --end 2024-09-06 --run-frequency weekly --weekly-run-weekday THU --env-file D:\Lumibot\project_notes\API.txt --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 240``

## Results

- Pytest: passed.
- Ruff: passed.
- Smoke backtest: passed.
- Smoke artifact: ``$artifact``

## Trace Checks

- ``evidence_profile="momentum_stage"`` found in equity tool result: yes.
- Old ``by_composite_score`` / ``by_momentum_composite`` exposed to equity agent: no.
- Candidate rows include stage warning flags: yes.
- Benchmark context includes QQQ and SPY return_126: yes.

## Notes

- Momentum-stage evidence is visible in trace/replay.
- This validation did not run a long performance backtest.
"@
Set-Content -Path docs\superpowers\notes\2026-08-26-momentum-stage-evidence-rank-layer-validation.md -Value $note -Encoding UTF8
```

- [ ] **Step 7: Commit validation note and any small fixes**

Run:

```powershell
git add docs\superpowers\notes\2026-08-26-momentum-stage-evidence-rank-layer-validation.md
git diff --cached --stat
git commit -m "docs: validate momentum stage evidence rank layer"
```

If Step 1 or Step 2 required code fixes, include those fixed files in the commit and mention the test failure in the commit body.

---

## Task 8: Final Verification And Branch Hygiene

**Files:**

- Read only:
  - `git status`
  - `git log`

- [ ] **Step 1: Confirm branch and status**

Run:

```powershell
git status --short --branch
```

Expected:

```text
## feature/qqq-historical-constituent-universe
```

Unrelated pre-existing dirty files may still appear. Do not stage or revert them unless they were part of this implementation.

- [ ] **Step 2: Confirm commits created by this plan**

Run:

```powershell
git log --oneline -8
```

Expected recent commit subjects include:

```text
docs: validate momentum stage evidence rank layer
feat: show momentum stage evidence in replay formatter
feat: score dynamic equity portfolios from stage evidence
refactor: rewrite equity prompts for momentum stage evidence
feat: wire momentum stage profile through history summary tool
feat: add momentum stage history summary profile
```

- [ ] **Step 3: Report result to user**

Report:

- Tests run and outcome.
- Smoke backtest artifact path.
- Whether trace/replay confirmed `momentum_stage`.
- Any remaining non-blocking concerns, especially whether the LLM still calls `duckdb_query` despite the new summary.

---

## Self-Review Against Spec

Spec coverage:

- Add `momentum_stage` evidence profile: Task 2.
- Disable old equity-only model-facing rank groups: Tasks 3 and 4.
- Compute the first 11 indicators: Task 2.
- Expose rankable indicators as top-10 lists: Task 2.
- Expose reference indicators only in candidate summaries: Task 2.
- Build candidate summary from new stage rankings: Task 2.
- Update dynamic constructor to use stage support/penalty: Task 5.
- Rewrite equity prompts: Task 4.
- Update tool description: Task 3.
- Keep news as secondary tie-breaker: Task 4.
- Update replay formatting only as needed: Task 6.
- Add tests: Tasks 1, 3, 4, 5, and 6.
- Smoke validation: Task 7.

No-placeholder scan:

- The plan contains concrete file paths, function names, test snippets, implementation snippets, commands, and expected outcomes.
- Follow-up strategy research, SMH universe changes, fundamentals, cadence, execution tools, and stop logic are excluded from this implementation.

Type consistency:

- The public profile value is `momentum_stage` throughout.
- The legacy profile value is `legacy` throughout.
- The equity-scoped wrapper keeps the tool name `market_load_history_tables_summary`.
- The stage candidate fields use `stage_*` names consistently.
- The constructor preserves `evidence_score` for existing weighting flow while adding `stage_support_score`, `stage_penalty`, and `adjusted_stage_score` diagnostics.
