# Equity Rank Layer Five Evidence Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the QQQ historical equity-only strategy's price/volume rank evidence from a small ranking set into five model-facing evidence groups with clearer metrics, capped candidate summaries, prompt alignment, and replay visibility.

**Architecture:** Extend `lumibot/components/agents/history_summary.py` as the single owner of per-symbol metric calculation and universe ranking output. Keep the existing summary-first tool path, add optional `top_n` and `candidate_summary_limit` arguments in `builtins.py`, align equity agent prompts in `ai_trading_team_equity_only_helpers.py`, and update replay formatter text without redesigning the UI.

**Tech Stack:** Python, pandas, pytest, Lumibot agent builtins, existing agent replay formatter, existing benchmark runner.

---

## File Map

- Modify: `lumibot/components/agents/history_summary.py`
  - Add new metric helpers.
  - Add rank groups.
  - Add ranking details with values.
  - Replace fixed constants-only limits with caller-provided `top_n` and `candidate_summary_limit`.
  - Keep backward-compatible `rankings` and `universe_summary` fields where practical.

- Modify: `lumibot/components/agents/builtins.py`
  - Add `top_n` and `candidate_summary_limit` parameters to `market_load_history_tables_summary`.
  - Validate both arguments as positive integers.
  - Update the model-facing tool description to describe five evidence groups and summary-first behavior.

- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
  - Rewrite `equity_basket_agent_system_prompt()`.
  - Rewrite `qqq_historical_equity_basket_agent_system_prompt()`.
  - Rewrite `equity_basket_agent_task_prompt()`.
  - Keep news conditional and scoped; do not expand this feature into news behavior.

- Modify: `lumibot/components/agents/replay_ui/formatters.py`
  - Update `market_load_history_tables_summary` human explanation for `rank_groups`, `ranking_details`, `candidate_summary`, and `coverage`.

- Modify: `tests/test_agent_history_summary.py`
  - Add metric calculation tests.
  - Add ranking output shape tests.
  - Update old expected summary/ranking tests for the new five-group structure.

- Modify: `tests/test_agent_manager.py`
  - Update tool definition tests for the new tool description and arguments.

- Modify: `tests/backtest/test_agent_runtime_backtest.py`
  - Update runtime-level tool schema tests that mention `market_load_history_tables_summary`.

- Modify: `tests/test_ai_trading_team_equity_only_llm.py`
  - Update prompt tests for five evidence groups and the new tool call arguments.

- Modify: `tests/test_agent_replay_ui_formatters.py`
  - Update/add formatter tests for the richer summary output.

- Optional modify only if an existing smoke command needs a new note: `docs/superpowers/notes/<generated-validation-note>.md`
  - Record the short backtest result after implementation.

---

## Task 1: Add Failing Tests for Per-Symbol Metrics

**Files:**
- Modify: `tests/test_agent_history_summary.py`

- [ ] **Step 1: Add helper frames for trend, breakout, drawdown, and volume tests**

Add these helpers near the existing `_frame()` helper:

```python
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
```

- [ ] **Step 2: Add metric test for skip-month momentum and trend-quality metrics**

Append this test:

```python
def test_compute_history_summary_adds_five_group_momentum_and_trend_quality_metrics():
    frame = _trend_frame(260)
    summary = compute_history_summary(frame, symbol="TREND", timestep="day", as_of=None)

    latest = float(frame["close"].iloc[-1])
    skip_endpoint = float(frame["close"].iloc[-22])
    skip_start = float(frame["close"].iloc[-253])

    assert summary["momentum"]["return_252_ex_skip_21"] == pytest.approx(skip_endpoint / skip_start - 1.0)
    assert summary["scores"]["sma_stack_score"] == 4
    assert summary["trend"]["linear_regression_slope_90"] is not None
    assert summary["trend"]["linear_regression_r2_90"] == pytest.approx(1.0, abs=1e-6)
    assert summary["scores"]["adjusted_slope_90"] is not None
    assert summary["availability"]["return_252_ex_skip_21"] is True
    assert summary["availability"]["sma_stack_score"] is True
    assert summary["availability"]["adjusted_slope_90"] is True
```

- [ ] **Step 3: Add metric test for risk-adjusted momentum**

Append this test:

```python
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
```

- [ ] **Step 4: Add metric test for breakout and volume confirmation**

Append this test:

```python
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
```

- [ ] **Step 5: Run metric tests to confirm they fail**

Run:

```powershell
python -m pytest tests/test_agent_history_summary.py::test_compute_history_summary_adds_five_group_momentum_and_trend_quality_metrics tests/test_agent_history_summary.py::test_compute_history_summary_adds_risk_adjusted_momentum_metrics tests/test_agent_history_summary.py::test_compute_history_summary_adds_breakout_and_volume_confirmation_metrics -q
```

Expected: FAIL because fields such as `return_252_ex_skip_21`, `sma_stack_score`, `linear_regression_r2_90`, `volatility_63`, and breakout scores do not exist yet.

---

## Task 2: Implement Per-Symbol Metrics

**Files:**
- Modify: `lumibot/components/agents/history_summary.py`
- Test: `tests/test_agent_history_summary.py`

- [ ] **Step 1: Add math/numeric helpers**

In `history_summary.py`, add helpers after `_volatility()`:

```python
def _period_return_excluding_recent(series: pd.Series | None, *, total_window: int, skip_recent: int) -> float | None:
    if series is None or len(series) <= total_window:
        return None
    endpoint_index = -skip_recent - 1
    start_index = -total_window - 1
    endpoint = _finite_float(series.iloc[endpoint_index])
    start = _finite_float(series.iloc[start_index])
    if endpoint is None or start in (None, 0):
        return None
    return _finite_float(endpoint / start - 1.0)


def _sma_stack_score(close: float | None, sma_20: float | None, sma_50: float | None, sma_200: float | None) -> int | None:
    values = [close, sma_20, sma_50, sma_200]
    if any(_finite_float(value) is None for value in values):
        return None
    score = 0
    if float(close) > float(sma_20):
        score += 1
    if float(sma_20) > float(sma_50):
        score += 1
    if float(sma_50) > float(sma_200):
        score += 1
    if float(close) > float(sma_200):
        score += 1
    return score


def _linear_regression_log_price(series: pd.Series | None, window: int) -> dict[str, float | None]:
    if series is None or len(series) < window:
        return {"slope": None, "r2": None, "annualized_slope": None}
    values = series.tail(window).map(_finite_float).dropna()
    values = values[values > 0]
    if len(values) < window:
        return {"slope": None, "r2": None, "annualized_slope": None}
    y = values.map(math.log)
    x = pd.Series(range(len(y)), dtype="float64")
    x_mean = x.mean()
    y_mean = y.mean()
    denominator = ((x - x_mean) ** 2).sum()
    if denominator == 0:
        return {"slope": None, "r2": None, "annualized_slope": None}
    slope = ((x - x_mean) * (y - y_mean)).sum() / denominator
    fitted = y_mean + slope * (x - x_mean)
    ss_total = ((y - y_mean) ** 2).sum()
    ss_residual = ((y - fitted) ** 2).sum()
    r2 = 1.0 if ss_total == 0 else 1.0 - ss_residual / ss_total
    annualized_slope = math.exp(slope * 252.0) - 1.0
    return {
        "slope": _finite_float(slope),
        "r2": _finite_float(r2),
        "annualized_slope": _finite_float(annualized_slope),
    }


def _sharpe_like(series: pd.Series | None, window: int) -> float | None:
    if series is None or len(series) <= 1:
        return None
    returns = series.pct_change().dropna()
    returns = returns[returns.map(lambda value: _finite_float(value) is not None)].tail(window)
    if len(returns) < 2:
        return None
    volatility = _finite_float(returns.std())
    mean_return = _finite_float(returns.mean())
    if mean_return is None or volatility in (None, 0):
        return None
    return _finite_float(mean_return / volatility)


def _breakout_score(high_series: pd.Series | None, close_series: pd.Series | None, window: int) -> float | None:
    if close_series is None or high_series is None or len(close_series) <= window or len(high_series) <= window:
        return None
    latest = _last_value(close_series)
    prior_high = _window_extreme(high_series.iloc[:-1], window, "max")
    return _relative_to(latest, prior_high)


def _up_volume_ratio(close_series: pd.Series | None, volume_series: pd.Series | None, window: int) -> float | None:
    if close_series is None or volume_series is None or len(close_series) <= 1:
        return None
    data = pd.DataFrame({"close": close_series, "volume": volume_series}).dropna().tail(window + 1)
    if len(data) < 2:
        return None
    returns = data["close"].pct_change().dropna()
    volumes = data["volume"].iloc[1:]
    total_volume = _finite_float(volumes.sum())
    if total_volume in (None, 0):
        return None
    up_volume = _finite_float(volumes[returns > 0].sum())
    if up_volume is None:
        return None
    return _finite_float(up_volume / total_volume)
```

- [ ] **Step 2: Wire new metrics into `compute_history_summary()`**

Modify the existing `momentum`, `trend`, `scores`, `range`, `risk`, `volume`, and `availability` blocks so the returned summary includes:

```python
momentum["return_252_ex_skip_21"] = _period_return_excluding_recent(close, total_window=252, skip_recent=21)

regression_90 = _linear_regression_log_price(close, 90)
trend["linear_regression_slope_90"] = regression_90["annualized_slope"]
trend["linear_regression_r2_90"] = regression_90["r2"]

scores["sma_stack_score"] = _sma_stack_score(
    latest_close,
    trend["sma_20"],
    trend["sma_50"],
    trend["sma_200"],
)
scores["adjusted_slope_90"] = _ratio(
    trend["linear_regression_slope_90"],
    1.0 / trend["linear_regression_r2_90"] if trend["linear_regression_r2_90"] not in (None, 0) else None,
)

volatility_63 = _volatility(close, 63)
max_drawdown_126 = _max_drawdown(close, 126)
scores["return_252_over_volatility_63"] = _ratio(momentum["return_252"], volatility_63)
scores["sharpe_like_63"] = _sharpe_like(close, 63)
scores["calmar_like_126"] = _ratio(momentum["return_126"], abs(max_drawdown_126) if max_drawdown_126 is not None else None)

range_metrics["distance_to_high_63"] = _relative_to(latest_close, _window_extreme(high if high is not None else close, 63, "max"))
range_metrics["breakout_20_high_score"] = _breakout_score(high if high is not None else close, close, 20)
range_metrics["breakout_63_high_score"] = _breakout_score(high if high is not None else close, close, 63)

volume_summary["dollar_volume_20"] = _finite_float(avg_volume_20 * latest_close) if avg_volume_20 is not None and latest_close is not None else None
volume_summary["up_volume_ratio_20"] = _up_volume_ratio(close, volume, 20)
scores["volume_confirmed_momentum"] = _mean_available([momentum["return_63"], volume_summary["up_volume_ratio_20"]])

risk["volatility_63"] = volatility_63
risk["max_drawdown_126"] = max_drawdown_126
```

Implementation note: do not literally introduce a `range_metrics` variable unless it fits the surrounding code. The current code returns the `range` dict inline; either introduce a local dict or add these fields to that returned dict.

- [ ] **Step 3: Use direct multiplication for adjusted slope**

If Step 2's `_ratio()` expression feels awkward during implementation, use this clearer form:

```python
adjusted_slope_90 = None
if trend["linear_regression_slope_90"] is not None and trend["linear_regression_r2_90"] is not None:
    adjusted_slope_90 = _finite_float(
        trend["linear_regression_slope_90"] * trend["linear_regression_r2_90"]
    )
scores["adjusted_slope_90"] = adjusted_slope_90
```

Use this direct multiplication version in the final code.

- [ ] **Step 4: Update sanitization expectations**

Update `test_compute_history_summary_sanitizes_non_finite_source_values()` so expected `scores`, `trend`, `range`, `risk`, and `volume` dictionaries include the new fields with `None` values.

- [ ] **Step 5: Run metric tests**

Run:

```powershell
python -m pytest tests/test_agent_history_summary.py::test_compute_history_summary_adds_five_group_momentum_and_trend_quality_metrics tests/test_agent_history_summary.py::test_compute_history_summary_adds_risk_adjusted_momentum_metrics tests/test_agent_history_summary.py::test_compute_history_summary_adds_breakout_and_volume_confirmation_metrics tests/test_agent_history_summary.py::test_compute_history_summary_sanitizes_non_finite_source_values -q
```

Expected: PASS.

- [ ] **Step 6: Commit metric work**

Run:

```powershell
git add lumibot\components\agents\history_summary.py tests\test_agent_history_summary.py
git commit -m "feat: add equity history evidence metrics"
```

---

## Task 3: Add Failing Tests for Five-Group Universe Ranking Output

**Files:**
- Modify: `tests/test_agent_history_summary.py`

- [ ] **Step 1: Replace `_rankable_summary()` with a richer test helper**

Update `_rankable_summary()` so it can produce rows for all new ranking families:

```python
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
```

- [ ] **Step 2: Add five-group output shape test**

Append:

```python
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

    assert set(summary["rank_groups"]) == {
        "momentum",
        "trend_quality",
        "risk_adjusted_momentum",
        "breakout_near_high",
        "volume_confirmation",
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
    assert summary["ranking_details"]["by_return_252"][0] == {"rank": 1, "symbol": "AAA", "value": 0.8}
    assert summary["rankings"]["by_near_252_high"] == ["AAA", "BBB"]
    assert len(summary["candidate_summary"]) == 2
    assert summary["candidate_summary"][0]["symbol"] == "AAA"
```

- [ ] **Step 3: Add missing-metric ranking isolation test**

Append:

```python
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
```

- [ ] **Step 4: Run new ranking tests to confirm failure**

Run:

```powershell
python -m pytest tests/test_agent_history_summary.py::test_build_universe_history_summary_returns_five_rank_groups_and_details tests/test_agent_history_summary.py::test_build_universe_history_summary_missing_metric_excludes_only_that_ranking -q
```

Expected: FAIL because `build_universe_history_summary()` does not accept `top_n`, does not return `rank_groups`, `ranking_details`, `candidate_summary`, or `coverage`.

---

## Task 4: Implement Five-Group Universe Ranking Output

**Files:**
- Modify: `lumibot/components/agents/history_summary.py`
- Test: `tests/test_agent_history_summary.py`

- [ ] **Step 1: Add ranking group constants**

Near existing constants, replace the old priority constants with:

```python
DEFAULT_RANKING_LIMIT = 10
DEFAULT_CANDIDATE_SUMMARY_LIMIT = 25

RANK_GROUPS = {
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

RANKING_METRICS = {
    "by_return_21": "return_21",
    "by_return_63": "return_63",
    "by_return_126": "return_126",
    "by_return_252": "return_252",
    "by_return_252_ex_skip_21": "return_252_ex_skip_21",
    "by_momentum_composite": "momentum_composite",
    "by_composite_score": "composite_score",
    "by_trend_alignment": "trend_alignment",
    "by_sma_stack_score": "sma_stack_score",
    "by_adjusted_slope_90": "adjusted_slope_90",
    "by_regression_r2_90": "linear_regression_r2_90",
    "by_return_63_over_volatility_20": "return_63_over_volatility_20",
    "by_return_126_over_volatility_20": "return_126_over_volatility_20",
    "by_return_252_over_volatility_63": "return_252_over_volatility_63",
    "by_sharpe_like_63": "sharpe_like_63",
    "by_calmar_like_126": "calmar_like_126",
    "by_near_252_high": "distance_to_high_252",
    "by_near_63_high": "distance_to_high_63",
    "by_breakout_20_high": "breakout_20_high_score",
    "by_breakout_63_high": "breakout_63_high_score",
    "by_drawdown_from_high_60": "drawdown_from_high_60",
    "by_volume_vs_avg_20": "volume_vs_avg_20",
    "by_dollar_volume_20": "dollar_volume_20",
    "by_up_volume_ratio_20": "up_volume_ratio_20",
    "by_volume_confirmed_momentum": "volume_confirmed_momentum",
}

CANDIDATE_PRIORITY_RANKINGS = [
    "by_momentum_composite",
    "by_adjusted_slope_90",
    "by_return_252_over_volatility_63",
    "by_composite_score",
]
```

Keep `by_composite_score` in `RANKING_METRICS` for backward compatibility, but do not include it in `RANK_GROUPS`.

- [ ] **Step 2: Extend `_summary_to_universe_row()`**

Add the new fields to the returned row:

```python
"return_252": _compact_number(momentum.get("return_252")),
"return_252_ex_skip_21": _compact_number(momentum.get("return_252_ex_skip_21")),
"sma_stack_score": _compact_number(scores.get("sma_stack_score")),
"adjusted_slope_90": _compact_number(scores.get("adjusted_slope_90")),
"linear_regression_r2_90": _compact_number(trend.get("linear_regression_r2_90")),
"return_252_over_volatility_63": _compact_number(scores.get("return_252_over_volatility_63")),
"sharpe_like_63": _compact_number(scores.get("sharpe_like_63")),
"calmar_like_126": _compact_number(scores.get("calmar_like_126")),
"distance_to_high_252": _compact_number(range_metrics.get("distance_to_high_252")),
"distance_to_high_63": _compact_number(range_metrics.get("distance_to_high_63")),
"breakout_20_high_score": _compact_number(range_metrics.get("breakout_20_high_score")),
"breakout_63_high_score": _compact_number(range_metrics.get("breakout_63_high_score")),
"dollar_volume_20": _compact_number(volume.get("dollar_volume_20")),
"up_volume_ratio_20": _compact_number(volume.get("up_volume_ratio_20")),
"volume_confirmed_momentum": _compact_number(scores.get("volume_confirmed_momentum")),
```

- [ ] **Step 3: Replace `_rankings()` and add ranking detail helpers**

Replace `_rankings()` with:

```python
def _rankings(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {
        ranking_name: _rank_symbols(rows, metric_name)
        for ranking_name, metric_name in RANKING_METRICS.items()
    }
```

Add:

```python
def _ranking_details(rows: list[dict[str, Any]], rankings: dict[str, list[str]]) -> dict[str, list[dict[str, Any]]]:
    rows_by_symbol = {str(row["symbol"]): row for row in rows if row.get("symbol")}
    details: dict[str, list[dict[str, Any]]] = {}
    for ranking_name, symbols in rankings.items():
        metric_name = RANKING_METRICS[ranking_name]
        entries = []
        for index, symbol in enumerate(symbols, start=1):
            row = rows_by_symbol.get(symbol)
            if row is None:
                continue
            entries.append(
                {
                    "rank": index,
                    "symbol": symbol,
                    "value": _compact_number(row.get(metric_name)),
                }
            )
        details[ranking_name] = entries
    return details
```

- [ ] **Step 4: Update candidate selection to prioritize repeated appearances**

Replace `_DETAIL_SELECTION_RANKING_PRIORITY` usage with a ranking-frequency helper:

```python
def _select_candidate_summary_symbols(rankings: dict[str, list[str]], *, limit: int) -> list[str]:
    candidates = _unique_ranked_symbols(rankings)
    if len(candidates) <= limit:
        return candidates

    appearance_counts: dict[str, int] = {}
    best_rank: dict[str, int] = {}
    first_seen_order: dict[str, int] = {}
    for ranking_name, ranking in rankings.items():
        priority_bonus = 1 if ranking_name in CANDIDATE_PRIORITY_RANKINGS else 0
        for index, symbol in enumerate(ranking):
            first_seen_order.setdefault(symbol, len(first_seen_order))
            appearance_counts[symbol] = appearance_counts.get(symbol, 0) + 1 + priority_bonus
            best_rank[symbol] = min(best_rank.get(symbol, index), index)

    ordered = list(candidates)
    ordered.sort(
        key=lambda symbol: (
            -appearance_counts.get(symbol, 0),
            best_rank.get(symbol, len(candidates)),
            first_seen_order.get(symbol, len(candidates)),
            symbol,
        )
    )
    return ordered[:limit]
```

Update `_unique_ranked_symbols()` to iterate `CANDIDATE_PRIORITY_RANKINGS` first and then every ranking.

- [ ] **Step 5: Update `build_universe_history_summary()` signature and return shape**

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
) -> dict[str, Any]:
```

Inside the function:

```python
top_n = max(1, int(top_n))
candidate_summary_limit = max(1, int(candidate_summary_limit))
full_rankings = _rankings(all_universe_rows)
rankings = _limit_rankings(full_rankings, top_n)
ranking_details = _ranking_details(all_universe_rows, rankings)
selected_symbols = _select_candidate_summary_symbols(rankings, limit=candidate_summary_limit)
```

Return both new and backward-compatible names:

```python
"rank_groups": RANK_GROUPS,
"ranking_limit": top_n,
"rankings": rankings,
"ranking_details": ranking_details,
"candidate_summary_limit": candidate_summary_limit,
"candidate_summary": candidate_summary,
"universe_summary_limit": candidate_summary_limit,
"universe_summary": candidate_summary,
"universe_summary_selection": {
    "mode": "top_rank_union",
    "candidate_count_before_limit": len(_unique_ranked_symbols(rankings)),
    "included_symbols": selected_symbols,
    "priority": CANDIDATE_PRIORITY_RANKINGS + ["multi_ranking_overlap"],
},
"coverage": {
    "requested_count": len(symbols),
    "loaded_count": len(all_universe_rows),
    "failed_count": max(0, len(symbols) - len(all_universe_rows)),
    "top_n": top_n,
    "candidate_summary_limit": candidate_summary_limit,
    "ranking_count": len(rankings),
},
```

- [ ] **Step 6: Update old ranking tests**

In existing tests, replace assumptions of exactly six rankings with assertions that include the old six plus the new rankings. For the non-finite row test, assert selected important ranking entries individually instead of asserting exact full `summary["rankings"]`.

Use this style:

```python
assert summary["rankings"]["by_return_21"] == []
assert summary["rankings"]["by_return_126"] == ["BAD"]
assert summary["rankings"]["by_composite_score"] == []
assert summary["rankings"]["by_adjusted_slope_90"] == []
```

- [ ] **Step 7: Run ranking tests**

Run:

```powershell
python -m pytest tests/test_agent_history_summary.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit ranking output work**

Run:

```powershell
git add lumibot\components\agents\history_summary.py tests\test_agent_history_summary.py
git commit -m "feat: expose five equity rank evidence groups"
```

---

## Task 5: Add Tool Arguments and Update Tool Description

**Files:**
- Modify: `lumibot/components/agents/builtins.py`
- Modify: `tests/test_agent_history_summary.py`
- Modify: `tests/test_agent_manager.py`
- Modify: `tests/backtest/test_agent_runtime_backtest.py`

- [ ] **Step 1: Add failing test for tool description**

In `tests/test_agent_history_summary.py`, update `test_history_tool_descriptions_are_summary_first()` required assertions:

```python
assert "five evidence groups" in multi
assert "momentum" in multi
assert "trend quality" in multi
assert "risk-adjusted momentum" in multi
assert "breakout" in multi
assert "volume confirmation" in multi
assert "candidate_summary_limit" in multi
assert "top_n" in multi
assert "by_composite_score" not in multi
```

- [ ] **Step 2: Add failing manager-level schema test**

In `tests/test_agent_manager.py`, update or add a test near existing `market_load_history_tables_summary` tests:

```python
def test_market_load_history_tables_summary_tool_accepts_rank_limits():
    tools = {
        definition.name: definition.binder(object(), object())
        for definition in BuiltinTools.all()
        if definition.name == "market_load_history_tables_summary"
    }

    function = tools["market_load_history_tables_summary"].function
    signature = inspect.signature(function)

    assert "top_n" in signature.parameters
    assert signature.parameters["top_n"].default == 10
    assert "candidate_summary_limit" in signature.parameters
    assert signature.parameters["candidate_summary_limit"].default == 25
```

If `inspect` is not imported in `tests/test_agent_manager.py`, add `import inspect`.

- [ ] **Step 3: Run the focused tests and confirm failure**

Run:

```powershell
python -m pytest tests/test_agent_history_summary.py::test_history_tool_descriptions_are_summary_first tests/test_agent_manager.py::test_market_load_history_tables_summary_tool_accepts_rank_limits -q
```

Expected: FAIL because the tool does not expose the new arguments or description.

- [ ] **Step 4: Update `_bind_load_history_tables_summary()`**

In `builtins.py`, change the nested function signature:

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
    candidate_summary_limit: int = 25,
) -> dict[str, Any]:
```

Validate:

```python
top_n = _require_positive_int("top_n", top_n)
candidate_summary_limit = _require_positive_int("candidate_summary_limit", candidate_summary_limit)
```

Pass through:

```python
return manager.duckdb.load_history_tables_summary(
    symbols=symbols,
    length=length,
    timestep=timestep,
    asset_type=asset_type,
    table_prefix=table_prefix,
    include_after_hours=include_after_hours,
    top_n=top_n,
    candidate_summary_limit=candidate_summary_limit,
)
```

- [ ] **Step 5: Update the tool description**

Replace the description paragraph for `market_load_history_tables_summary` with:

```python
description=(
    "Load visible historical bars for multiple symbols into DuckDB and return a summary-first cross-symbol "
    "ranking package. Arguments: symbols, optional length, timestep, asset_type, table_prefix, "
    "include_after_hours, top_n default 10, and candidate_summary_limit default 25. "
    "This is the default tool for multi-symbol price-history comparison and universe ranking. "
    "It returns five evidence groups: momentum, trend quality, risk-adjusted momentum, breakout / near-high, "
    "and volume confirmation. Each ranking list is capped by top_n and includes ranking values in "
    "ranking_details. candidate_summary contains a compact top-ranked candidate subset capped by "
    "candidate_summary_limit. Prefer this tool before writing DuckDB SQL for ordinary universe ranking; "
    "use DuckDB only as targeted follow-up when the computed summaries are insufficient. "
    "Caveat: this only loads bars visible at the current LumiBot runtime datetime. "
    "Example: market_load_history_tables_summary(symbols=['MSFT', 'AAPL'], length=252, timestep='day', "
    "top_n=10, candidate_summary_limit=25)."
),
```

- [ ] **Step 6: Update duckdb manager method if needed**

Search for the manager implementation:

```powershell
rg -n "def load_history_tables_summary" lumibot
```

If the manager method does not accept `top_n` and `candidate_summary_limit`, add those keyword parameters and pass them into `build_universe_history_summary()`.

- [ ] **Step 7: Update runtime/backtest tests**

Run:

```powershell
python -m pytest tests/backtest/test_agent_runtime_backtest.py::test_duckdb_history_tables_summary_returns_rankings_and_queryable_tables -q
```

If it fails because old expected output is too narrow, update the assertions to include:

```python
assert "rank_groups" in summary
assert "ranking_details" in summary
assert "candidate_summary" in summary
assert "coverage" in summary
assert "by_return_252" in summary["rankings"]
assert "by_adjusted_slope_90" in summary["rankings"]
assert "by_volume_confirmed_momentum" in summary["rankings"]
```

- [ ] **Step 8: Run focused tool tests**

Run:

```powershell
python -m pytest tests/test_agent_history_summary.py::test_history_tool_descriptions_are_summary_first tests/test_agent_manager.py::test_market_load_history_tables_summary_tool_accepts_rank_limits tests/backtest/test_agent_runtime_backtest.py::test_duckdb_history_tables_summary_returns_rankings_and_queryable_tables -q
```

Expected: PASS.

- [ ] **Step 9: Commit tool interface work**

Run:

```powershell
git add lumibot\components\agents\builtins.py lumibot\components\agents tests\test_agent_history_summary.py tests\test_agent_manager.py tests\backtest\test_agent_runtime_backtest.py
git commit -m "feat: expose rank summary limits in history tool"
```

Before committing, inspect `git diff --stat` and avoid accidentally staging unrelated files under `lumibot\components\agents`.

---

## Task 6: Update Equity Agent Prompts

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
- Modify: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Add failing prompt assertions**

In `test_equity_agent_system_prompt_is_equity_only_rank_first_and_conditional_news()`, add required phrases:

```python
for required in (
    "momentum",
    "trend quality",
    "risk-adjusted momentum",
    "breakout",
    "near-high",
    "volume confirmation",
    "do not treat composite_score as the final answer",
    "do not blindly copy",
):
    assert required in prompt
```

Add forbidden phrase:

```python
assert "safest" not in prompt
```

In `test_equity_agent_task_prompt_teaches_summary_first_top_n_and_strict_json()`, add:

```python
assert "candidate_summary_limit=25" in task_prompt
assert "five rank groups" in task_prompt
assert "which evidence groups support" in task_prompt
assert "do not assign per-symbol weights" in task_prompt
```

- [ ] **Step 2: Run prompt tests to confirm failure**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_system_prompt_is_equity_only_rank_first_and_conditional_news tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_task_prompt_teaches_summary_first_top_n_and_strict_json tests/test_ai_trading_team_equity_only_llm.py::test_qqq_historical_equity_agent_prompt_mentions_historical_constituents_without_weight_bias -q
```

Expected: FAIL because the prompt does not yet mention all five evidence groups and `candidate_summary_limit`.

- [ ] **Step 3: Rewrite `equity_basket_agent_system_prompt()`**

Replace the returned text with:

```python
return (
    f"Equity-only selection role: choose exactly five stocks from the assigned basket_symbols ({symbols}). "
    "The downstream deterministic planner gives the five selected stocks equal target weights. "
    "You cannot place orders or size trades. Use market_load_history_tables_summary first for multi-symbol "
    "comparison. Treat rank groups as separate evidence lenses: momentum, trend quality, risk-adjusted "
    "momentum, breakout / near-high, and volume confirmation. Prefer stocks supported by multiple relevant "
    "evidence groups. Do not blindly copy the first five names from one ranking list. Do not treat "
    "composite_score as the final answer. Do not invent sector, style, safety, or cyclicality labels. "
    "If the leading group is clear across relevant rank evidence, select it without news. Use alpaca_news "
    "only when leading candidates are close, conflicting, or uncertain; when used, request news only for "
    "leading candidates. If news is unavailable, continue with rank-only evidence. Return strict JSON only."
)
```

- [ ] **Step 4: Rewrite `qqq_historical_equity_basket_agent_system_prompt()`**

Replace the returned text with:

```python
return (
    f"Equity-only selection role: choose exactly five stocks from the QQQ historical constituent universe "
    f"provided in basket_symbols ({symbols}). "
    "The provided basket_symbols represent the QQQ historical constituent universe available for the current "
    "backtest date. The downstream deterministic planner gives the five selected stocks equal target weights. "
    "You cannot place orders or size trades. Use market_load_history_tables_summary first for multi-symbol "
    "comparison. Treat rank groups as separate evidence lenses: momentum, trend quality, risk-adjusted "
    "momentum, breakout / near-high, and volume confirmation. Prefer stocks supported by multiple relevant "
    "evidence groups. Do not blindly copy the first five names from one ranking list. Do not treat "
    "composite_score as the final answer. Do not invent sector, style, safety, or cyclicality labels. "
    "Do not assume QQQ membership itself makes a stock safe or best; select from current rank evidence. "
    "Do not choose based on index weight alone. If the leading group is clear across relevant rank evidence, "
    "select it without news. Use alpaca_news only when leading candidates are close, conflicting, or uncertain; "
    "when used, request news only for leading candidates. If news is unavailable, continue with rank-only "
    "evidence. Use only symbols in the provided basket_symbols and do not add symbols outside the provided "
    "universe. Return strict JSON only."
)
```

- [ ] **Step 5: Rewrite `equity_basket_agent_task_prompt()`**

Replace the returned text with:

```python
return (
    "Review only the provided basket_symbols. First call market_load_history_tables_summary with "
    "symbols=basket_symbols, length=252, timestep='day', top_n=10, and candidate_summary_limit=25. "
    "Compare the five rank groups: momentum, trend quality, risk-adjusted momentum, breakout / near-high, "
    "and volume confirmation. Select exactly five unique symbols that are strongest across the separate "
    "evidence groups; do not simply copy the first five names from one list if other evidence conflicts. "
    "In reason_brief, briefly mention which evidence groups support the selected symbols. If a selected "
    "symbol is supported by only one group, explain why it still deserves selection. If the leading group is "
    "clear across relevant rank evidence, select it without news. If leading candidates are close, conflicting, "
    "or uncertain, call alpaca_news for those leading candidates only. If alpaca_news is unavailable or errors, "
    "continue with rank-only evidence. Return exactly one strict JSON object with basket_id, target_weight, "
    "status, candidate_symbols, selected_symbols, and reason_brief. Use status='active'. target_weight must "
    "be 1.0 for the equity basket as a whole; do not assign per-symbol weights. candidate_symbols must copy "
    "the assigned basket_symbols exactly; do not replace it with a shortlist. selected_symbols must contain "
    "exactly five unique symbols from basket_symbols."
)
```

- [ ] **Step 6: Run prompt tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_system_prompt_is_equity_only_rank_first_and_conditional_news tests/test_ai_trading_team_equity_only_llm.py::test_equity_agent_task_prompt_teaches_summary_first_top_n_and_strict_json tests/test_ai_trading_team_equity_only_llm.py::test_qqq_historical_equity_agent_prompt_mentions_historical_constituents_without_weight_bias -q
```

Expected: PASS.

- [ ] **Step 7: Commit prompt work**

Run:

```powershell
git add lumibot\example_strategies\ai_trading_team_equity_only_helpers.py tests\test_ai_trading_team_equity_only_llm.py
git commit -m "feat: align equity prompts with five rank groups"
```

---

## Task 7: Update Replay Formatter for Rank Evidence

**Files:**
- Modify: `tests/test_agent_replay_ui_formatters.py`
- Modify: `lumibot/components/agents/replay_ui/formatters.py`

- [ ] **Step 1: Add failing formatter test for new summary shape**

In `tests/test_agent_replay_ui_formatters.py`, append:

```python
def test_market_load_history_tables_summary_formatter_shows_rank_groups_and_candidate_summary():
    explanation = explain_tool_result(
        "market_load_history_tables_summary",
        {"symbols": ["AAA", "BBB", "CCC"], "top_n": 2, "candidate_summary_limit": 2},
        {
            "coverage": {
                "requested_count": 3,
                "loaded_count": 3,
                "failed_count": 0,
                "top_n": 2,
                "candidate_summary_limit": 2,
                "ranking_count": 3,
            },
            "rank_groups": {
                "momentum": ["by_return_63"],
                "trend_quality": ["by_adjusted_slope_90"],
            },
            "ranking_details": {
                "by_return_63": [
                    {"rank": 1, "symbol": "AAA", "value": 0.3},
                    {"rank": 2, "symbol": "BBB", "value": 0.2},
                ],
                "by_adjusted_slope_90": [
                    {"rank": 1, "symbol": "BBB", "value": 0.9},
                ],
            },
            "candidate_summary": [{"symbol": "AAA"}, {"symbol": "BBB"}],
            "warnings": [],
        },
    )

    assert "3 requested symbols" in explanation
    assert "3 loaded" in explanation
    assert "top 2" in explanation
    assert "2 candidate summary rows" in explanation
    assert "momentum" in explanation
    assert "by_return_63: AAA=0.3, BBB=0.2" in explanation
    assert "trend_quality" in explanation
```

- [ ] **Step 2: Run formatter test to confirm failure**

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_formatters.py::test_market_load_history_tables_summary_formatter_shows_rank_groups_and_candidate_summary -q
```

Expected: FAIL because current formatter only shows old `universe_summary` and three old rankings.

- [ ] **Step 3: Update `_market_load_history_tables_summary()` formatter**

Replace the ranking loop section in `formatters.py` with logic like:

```python
coverage = _as_dict(result.get("coverage"))
if coverage:
    loaded = coverage.get("loaded_count")
    requested = coverage.get("requested_count")
    failed = coverage.get("failed_count")
    if isinstance(loaded, int):
        parts.append(f"{loaded} loaded")
    if isinstance(requested, int):
        parts.append(f"{requested} requested symbols")
    if isinstance(failed, int) and failed:
        parts.append(f"{failed} failed")
    if isinstance(coverage.get("top_n"), int):
        parts.append(f"rankings capped at top {coverage['top_n']}")
    if isinstance(coverage.get("candidate_summary_limit"), int):
        parts.append(f"candidate summary limit {coverage['candidate_summary_limit']}")

candidate_summary = _collection(raw_result, "candidate_summary")
if candidate_summary:
    parts.append(f"{_rows_label(len(candidate_summary), 'candidate summary row')}")

rank_groups = _as_dict(result.get("rank_groups"))
ranking_details = _as_dict(result.get("ranking_details"))
if rank_groups:
    for group_name, ranking_names in list(rank_groups.items())[:5]:
        if not isinstance(ranking_names, list):
            continue
        shown_rankings = []
        for ranking_name in ranking_names[:2]:
            entries = ranking_details.get(ranking_name)
            if not isinstance(entries, list) or not entries:
                continue
            pairs = []
            for entry in entries[:3]:
                entry_dict = _as_dict(entry)
                symbol = entry_dict.get("symbol")
                value = entry_dict.get("value")
                if symbol is not None:
                    pairs.append(f"{symbol}={value}")
            if pairs:
                shown_rankings.append(f"{ranking_name}: {', '.join(pairs)}")
        if shown_rankings:
            parts.append(f"{group_name}: " + " | ".join(shown_rankings))
```

Keep fallback behavior for old `rankings` if `ranking_details` is absent.

- [ ] **Step 4: Run formatter tests**

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_formatters.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit formatter work**

Run:

```powershell
git add lumibot\components\agents\replay_ui\formatters.py tests\test_agent_replay_ui_formatters.py
git commit -m "feat: explain five-group rank summaries in replay"
```

---

## Task 8: Run Focused Regression Tests

**Files:**
- No code changes unless tests reveal a bug.

- [ ] **Step 1: Run focused test suite**

Run:

```powershell
python -m pytest tests/test_agent_history_summary.py tests/test_agent_manager.py tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_replay_ui_formatters.py tests/backtest/test_agent_runtime_backtest.py::test_duckdb_history_tables_summary_returns_rankings_and_queryable_tables -q
```

Expected: PASS.

- [ ] **Step 2: Run lint on touched files**

Run:

```powershell
python -m ruff check lumibot/components/agents/history_summary.py lumibot/components/agents/builtins.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py lumibot/components/agents/replay_ui/formatters.py tests/test_agent_history_summary.py tests/test_agent_manager.py tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_replay_ui_formatters.py tests/backtest/test_agent_runtime_backtest.py
```

Expected: PASS.

- [ ] **Step 3: Fix any failures with the smallest local patch**

If a test fails, inspect the assertion and fix the smallest affected file. Do not broaden scope into news behavior, portfolio construction, QQQ universe resolution, or execution tools.

- [ ] **Step 4: Commit regression fixes if any**

If Step 3 changed files, run:

```powershell
git add <changed-files>
git commit -m "fix: stabilize equity rank evidence tests"
```

If no files changed, skip this commit.

---

## Task 9: Run Short Smoke Backtest and Inspect Trace

**Files:**
- Optional create: `docs/superpowers/notes/2026-08-22-equity-rank-layer-five-evidence-groups-validation.md`

- [ ] **Step 1: Confirm API env loader format**

Inspect `project_notes/API.txt` manually or with an existing local loader command used in previous backtests. Do not print API keys to terminal output.

- [ ] **Step 2: Run a short QQQ historical equity-only smoke backtest**

Use the existing benchmark runner command pattern from this repo. Prefer a short one-week or one-day run to verify behavior, not performance.

Run:

```powershell
python scripts\run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2024-09-05 --end 2024-09-06 --model gpt-5.6-luna
```

If this runner uses different flags in the current branch, run:

```powershell
python scripts\run_ai_trading_team_examples_benchmark.py --help
```

Then rerun with the equivalent strategy, start, end, and model arguments.

- [ ] **Step 3: Verify trace contains new tool call arguments and output fields**

Find the newest trace:

```powershell
Get-ChildItem -Recurse artifacts -Filter *.json | Sort-Object LastWriteTime -Descending | Select-Object -First 20 FullName
```

Inspect the `equity_basket_agent` trace JSON for the `market_load_history_tables_summary` call and verify:

```text
arguments.top_n = 10
arguments.candidate_summary_limit = 25
raw_result.rank_groups exists
raw_result.ranking_details exists
raw_result.candidate_summary exists
raw_result.coverage exists
```

- [ ] **Step 4: Verify agent output still selects exactly five symbols**

Inspect the equity agent final summary and verify:

```text
selected_symbols has exactly 5 unique symbols
each selected symbol is in basket_symbols
reason_brief references evidence groups or rank evidence
```

- [ ] **Step 5: Verify downstream planner and execution still run**

Inspect the strategy trace/workflow and verify:

```text
equity_basket_agent -> execution_agent
target portfolio contains 5 equal-weight rows
execution_plan is generated
execution_agent receives execution_plan
```

This verification checks workflow integration only. It does not require profitable output.

- [ ] **Step 6: Write validation note**

Create `docs/superpowers/notes/2026-08-22-equity-rank-layer-five-evidence-groups-validation.md` with:

```markdown
# Equity Rank Layer Five Evidence Groups Validation

## Test Window

- Strategy: qqq-historical-equity-only-llm
- Window: 2024-09-05 to 2024-09-06
- Model: gpt-5.6-luna

## Verification

- market_load_history_tables_summary called with top_n=10 and candidate_summary_limit=25.
- Tool result included rank_groups, ranking_details, candidate_summary, and coverage.
- equity_basket_agent selected exactly five symbols from basket_symbols.
- Downstream target portfolio and execution workflow ran.

## Notes

- This smoke test validates feature wiring, not strategy performance.
```

Fill in the actual artifact path and any warnings observed.

- [ ] **Step 7: Commit validation note**

Run:

```powershell
git add docs\superpowers\notes\2026-08-22-equity-rank-layer-five-evidence-groups-validation.md
git commit -m "docs: validate equity rank evidence groups"
```

---

## Task 10: Final Verification and Branch Hygiene

**Files:**
- No code changes unless verification reveals a bug.

- [ ] **Step 1: Run final focused verification**

Run:

```powershell
python -m pytest tests/test_agent_history_summary.py tests/test_agent_manager.py tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_replay_ui_formatters.py -q
```

Expected: PASS.

- [ ] **Step 2: Check git status**

Run:

```powershell
git status -sb
```

Expected:

```text
## feature/qqq-historical-constituent-universe
```

It is acceptable if the pre-existing untracked `project_notes/qqq_historical_5y_deep_dive_20260822_180628.md` remains. Do not add it unless the user asks.

- [ ] **Step 3: Summarize implemented scope**

Prepare a concise final note with:

```text
- Metrics added
- Rank groups exposed
- Tool schema updated
- Equity prompts updated
- Replay formatter updated
- Tests run
- Smoke backtest artifact path
- Any residual risks
```

---

## Self-Review Checklist

- [ ] Spec coverage: Tasks cover metric calculation, rank groups, ranking values, capped candidate summaries, tool arguments, prompt updates, replay formatter, tests, and smoke backtest.
- [ ] Non-goals protected: No task changes news behavior, fundamentals, stock universe resolution, portfolio weighting, cadence, execution tools, or QQQ membership logic.
- [ ] Completeness scan: This plan contains no unresolved markers or open-ended implementation gaps.
- [ ] Type consistency: `top_n`, `candidate_summary_limit`, `rank_groups`, `rankings`, `ranking_details`, `candidate_summary`, and `coverage` are named consistently across tool, tests, prompts, and formatter.
- [ ] Backward compatibility: `rankings`, `universe_summary`, `universe_summary_limit`, and `loaded_tables` remain available for existing UI/tests where practical.
