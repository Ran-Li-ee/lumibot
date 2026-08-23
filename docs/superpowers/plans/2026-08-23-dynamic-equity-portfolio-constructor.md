# Dynamic Equity Portfolio Constructor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fixed five-stock equal-weight equity-only portfolio construction with a deterministic dynamic constructor that chooses 3-10 holdings and assigns evidence/volatility-adjusted target weights.

**Architecture:** Add a focused constructor module that converts the equity agent's candidate JSON plus the latest `market_load_history_tables_summary` result into planner-compatible `target_portfolio` rows. Keep `target_portfolio_to_execution_plan()` and `execution_agent` behavior unchanged; wire the new constructor at the current `equity_only_target_portfolio()` integration point. Update prompts so the equity agent identifies credible candidates but does not calculate exact weights.

**Tech Stack:** Python 3, pytest, existing Lumibot agent runtime, existing `market_load_history_tables_summary` output, existing `target_portfolio_to_execution_plan` planner.

---

## File Structure

Create:

- `lumibot/example_strategies/dynamic_equity_portfolio_constructor.py`
  - Owns the deterministic policy, score calculation, dynamic count selection, weight normalization, diagnostics, and extraction of the latest summary tool result from an `AgentRunResult`.

- `tests/test_dynamic_equity_portfolio_constructor.py`
  - Focused unit tests for constructor behavior without running LLMs or backtests.

Modify:

- `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`
  - Replace fixed equal-weight `equity_only_target_portfolio()` with a wrapper around the new constructor.
  - Extract latest `market_load_history_tables_summary` from `equity_result`.
  - Store the constructor result on the strategy for trace/debug review.

- `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
  - Remove fixed-five/equal-weight wording from equity prompts.
  - Keep the evidence interpretation policy and conditional news policy.
  - Tell the LLM to return 3-10 credible candidates and avoid exact per-symbol weights.

- `tests/test_ai_trading_team_equity_only_llm.py`
  - Replace fixed 5 / 20% expectations with dynamic range and constraint expectations.
  - Verify prompt changes and strategy wiring.

- `lumibot/components/agents/history_summary.py`
  - Add `volatility_20` to compact `candidate_summary` rows so the constructor can apply real volatility adjustment from the summary tool output.

- `tests/test_agent_history_summary.py`
  - Update compact-row expectations to include `volatility_20`.

Do not modify:

- `lumibot/example_strategies/target_portfolio_to_execution_plan.py`
- `lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py`
- QQQ universe resolver files
- Order execution tools

Preserve unrelated untracked file:

- `project_notes/qqq_historical_5y_deep_dive_20260822_180628.md`

---

## Shared Algorithm For First Implementation

Use this deterministic policy:

```python
DEFAULT_DYNAMIC_EQUITY_POLICY = DynamicEquityPortfolioPolicy(
    min_positions=3,
    max_positions=10,
    fallback_positions=5,
    cash_buffer_weight=0.02,
    max_single_weight=0.30,
    min_single_weight=0.05,
    leading_score_keep_ratio=0.70,
    minimum_candidate_score=0.35,
)
```

Use these evidence group weights:

```python
GROUP_WEIGHTS = {
    "momentum": 0.35,
    "trend_quality": 0.25,
    "risk_adjusted_momentum": 0.20,
    "breakout_near_high": 0.15,
    "volume_confirmation": 0.05,
}
```

Scoring rules:

1. Read candidate rows from `market_summary["candidate_summary"]`.
2. Candidate rows can include:
   - `symbol`
   - `best_rank_by_group`
   - `ranking_count`
   - `best_rank`
   - `volatility_20`
   - `momentum_composite`
   - `drawdown_from_high_60`
3. Convert each group best rank into a 0-1 score:

   ```python
   group_score = 1.0 - ((rank - 1) / max(1, ranking_limit - 1))
   ```

4. Missing groups contribute `0.0`.
5. Evidence score is the weighted sum of available group scores.
6. Add a small repeated-evidence bonus:

   ```python
   evidence_score += min(0.10, ranking_count * 0.01)
   evidence_score = min(1.0, evidence_score)
   ```

7. Volatility adjustment:
   - Collect positive finite `volatility_20` values among scored candidates.
   - Median volatility is neutral.
   - For each candidate:

     ```python
     volatility_penalty = clamp(volatility_20 / median_volatility, 0.75, 1.50)
     adjusted_score = evidence_score / volatility_penalty
     ```

   - If volatility is missing, use `volatility_penalty = 1.0`.

8. Candidate selection:
   - Start from valid `selected_symbols` returned by the LLM, preserving order.
   - Add any high-scoring summary candidates not already included.
   - Sort by adjusted score descending, with LLM order as tie-breaker.
   - Keep candidates with:

     ```python
     adjusted_score >= max(minimum_candidate_score, leading_score * leading_score_keep_ratio)
     ```

   - Enforce `min_positions` and `max_positions`.
   - If evidence is missing, fallback to the first valid `fallback_positions` LLM candidates and equal weights.

9. Weighting:
   - Target total exposure:

     ```python
     target_exposure = 1.0 - cash_buffer_weight
     ```

   - Raw weights are proportional to adjusted scores.
   - Apply `max_single_weight` with iterative redistribution to uncapped holdings.
   - Drop holdings below `min_single_weight` only if doing so still leaves at least `min_positions`.
   - Re-normalize to `target_exposure`.

10. Output:

```python
{
    "portfolio_mode": "dynamic_equity",
    "selected_count": len(target_portfolio),
    "target_portfolio": [
        {"basket_id": "equity", "symbol": "AAPL", "target_weight": 0.24},
    ],
    "cash_buffer_weight": 0.02,
    "weighting_method": "evidence_score_with_volatility_adjustment",
    "diagnostics": {
        "fallback_used": False,
        "selection_reason": "...",
        "candidate_scores": [...],
        "capped_symbols": [],
        "pruned_symbols": [],
    },
}
```

---

### Task 0: Expose Candidate Volatility In History Summary

**Files:**
- Modify: `lumibot/components/agents/history_summary.py`
- Modify: `tests/test_agent_history_summary.py`

- [ ] **Step 1: Update the compact-row test first**

In `tests/test_agent_history_summary.py`, find `test_build_universe_history_summary_candidate_summary_rows_are_compact()` and update the expected row key set so it includes `volatility_20`:

```python
    assert set(first_row) == {
        "symbol",
        "latest_close",
        "return_5",
        "momentum_composite",
        "composite_score",
        "volume_vs_avg_20",
        "drawdown_from_high_60",
        "volatility_20",
        "evidence_groups",
        "ranking_count",
        "best_rank",
        "best_rank_by_group",
    }
    assert "return_21" not in first_row
    assert first_row["volatility_20"] is not None
    assert len(json.dumps(summary["candidate_summary"], sort_keys=True)) < 6_500
```

- [ ] **Step 2: Run the focused history summary test and verify it fails**

Run:

```bash
python -m pytest tests/test_agent_history_summary.py::test_build_universe_history_summary_candidate_summary_rows_are_compact -q
```

Expected: FAIL because `candidate_summary` rows do not yet include `volatility_20`.

- [ ] **Step 3: Add `volatility_20` to compact candidate rows**

In `lumibot/components/agents/history_summary.py`, update `_candidate_summary_row()` so the returned dict contains:

```python
        "volatility_20": _compact_number(row.get("volatility_20")),
```

Place it next to the other compact numeric fields:

```python
    return {
        "symbol": symbol,
        "latest_close": _compact_number(row.get("latest_close")),
        "return_5": _compact_number(row.get("return_5")),
        "momentum_composite": _compact_number(row.get("momentum_composite")),
        "composite_score": _compact_number(row.get("composite_score")),
        "volume_vs_avg_20": _compact_number(row.get("volume_vs_avg_20")),
        "drawdown_from_high_60": _compact_number(row.get("drawdown_from_high_60")),
        "volatility_20": _compact_number(row.get("volatility_20")),
        "evidence_groups": list(best_rank_by_group),
        "ranking_count": ranking_count,
        "best_rank": best_rank,
        "best_rank_by_group": best_rank_by_group,
    }
```

- [ ] **Step 4: Run history summary tests**

Run:

```bash
python -m pytest tests/test_agent_history_summary.py::test_build_universe_history_summary_candidate_summary_rows_are_compact tests/test_agent_history_summary.py::test_build_universe_history_summary_result_is_json_safe -q
```

Expected: PASS.

- [ ] **Step 5: Commit volatility summary support**

```bash
git add lumibot/components/agents/history_summary.py tests/test_agent_history_summary.py
git commit -m "feat: expose candidate volatility in history summary"
```

---

### Task 1: Add Constructor Tests First

**Files:**
- Create: `tests/test_dynamic_equity_portfolio_constructor.py`
- No production files yet.

- [ ] **Step 1: Write failing constructor tests**

Create `tests/test_dynamic_equity_portfolio_constructor.py` with these tests:

```python
import pytest

from lumibot.example_strategies.dynamic_equity_portfolio_constructor import (
    DEFAULT_DYNAMIC_EQUITY_POLICY,
    DynamicEquityPortfolioPolicy,
    construct_dynamic_equity_target_portfolio,
)


def candidate(
    symbol,
    *,
    momentum=1,
    trend_quality=1,
    risk_adjusted_momentum=1,
    breakout_near_high=1,
    volume_confirmation=1,
    ranking_count=5,
    volatility_20=0.02,
):
    groups = {
        "momentum": momentum,
        "trend_quality": trend_quality,
        "risk_adjusted_momentum": risk_adjusted_momentum,
        "breakout_near_high": breakout_near_high,
        "volume_confirmation": volume_confirmation,
    }
    return {
        "symbol": symbol,
        "best_rank_by_group": groups,
        "ranking_count": ranking_count,
        "best_rank": min(groups.values()),
        "volatility_20": volatility_20,
        "momentum_composite": 1.0 / max(1, momentum),
        "drawdown_from_high_60": -0.02,
    }


def summary(rows, *, ranking_limit=10):
    return {
        "ranking_limit": ranking_limit,
        "candidate_summary": rows,
        "rank_groups": {
            "momentum": ["by_momentum_composite"],
            "trend_quality": ["by_adjusted_slope_90"],
            "risk_adjusted_momentum": ["by_return_252_over_volatility_63"],
            "breakout_near_high": ["by_near_252_high"],
            "volume_confirmation": ["by_volume_confirmed_momentum"],
        },
    }


def weights_by_symbol(result):
    return {
        item["symbol"]: item["target_weight"]
        for item in result["target_portfolio"]
    }


def test_constructor_selects_small_leading_cluster_and_preserves_cash_buffer():
    equity_report = {
        "basket_id": "equity",
        "status": "active",
        "selected_symbols": ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"],
    }
    market_summary = summary(
        [
            candidate("AAA", momentum=1, trend_quality=1, risk_adjusted_momentum=1),
            candidate("BBB", momentum=2, trend_quality=2, risk_adjusted_momentum=2),
            candidate("CCC", momentum=3, trend_quality=3, risk_adjusted_momentum=3),
            candidate("DDD", momentum=9, trend_quality=9, risk_adjusted_momentum=9),
            candidate("EEE", momentum=10, trend_quality=10, risk_adjusted_momentum=10),
            candidate("FFF", momentum=10, trend_quality=10, risk_adjusted_momentum=10),
        ]
    )

    result = construct_dynamic_equity_target_portfolio(
        equity_report,
        equity_universe=["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"],
        market_summary=market_summary,
    )

    assert result["portfolio_mode"] == "dynamic_equity"
    assert result["selected_count"] == 3
    assert [row["symbol"] for row in result["target_portfolio"]] == ["AAA", "BBB", "CCC"]
    assert sum(row["target_weight"] for row in result["target_portfolio"]) == pytest.approx(0.98)
    assert result["cash_buffer_weight"] == pytest.approx(0.02)
    assert result["diagnostics"]["fallback_used"] is False


def test_constructor_selects_broader_set_when_scores_are_close():
    equity_report = {
        "basket_id": "equity",
        "status": "active",
        "selected_symbols": ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"],
    }
    market_summary = summary(
        [
            candidate("AAA", momentum=1, trend_quality=2),
            candidate("BBB", momentum=2, trend_quality=1),
            candidate("CCC", momentum=2, trend_quality=3),
            candidate("DDD", momentum=3, trend_quality=2),
            candidate("EEE", momentum=3, trend_quality=3),
            candidate("FFF", momentum=4, trend_quality=3),
            candidate("GGG", momentum=5, trend_quality=4),
            candidate("HHH", momentum=6, trend_quality=4),
        ]
    )

    result = construct_dynamic_equity_target_portfolio(
        equity_report,
        equity_universe=["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"],
        market_summary=market_summary,
    )

    assert 6 <= result["selected_count"] <= 8
    assert sum(row["target_weight"] for row in result["target_portfolio"]) == pytest.approx(0.98)


def test_constructor_downweights_high_volatility_candidate():
    equity_report = {
        "basket_id": "equity",
        "status": "active",
        "selected_symbols": ["CALM", "WILD", "BASE"],
    }
    market_summary = summary(
        [
            candidate("CALM", momentum=1, trend_quality=1, volatility_20=0.01),
            candidate("WILD", momentum=1, trend_quality=1, volatility_20=0.05),
            candidate("BASE", momentum=2, trend_quality=2, volatility_20=0.02),
        ]
    )

    result = construct_dynamic_equity_target_portfolio(
        equity_report,
        equity_universe=["CALM", "WILD", "BASE"],
        market_summary=market_summary,
    )

    weights = weights_by_symbol(result)
    assert weights["CALM"] > weights["WILD"]
    wild_row = next(row for row in result["diagnostics"]["candidate_scores"] if row["symbol"] == "WILD")
    assert wild_row["volatility_penalty"] > 1.0


def test_constructor_enforces_max_single_weight_and_redistributes():
    policy = DynamicEquityPortfolioPolicy(
        min_positions=3,
        max_positions=10,
        fallback_positions=5,
        cash_buffer_weight=0.02,
        max_single_weight=0.30,
        min_single_weight=0.05,
        leading_score_keep_ratio=0.20,
        minimum_candidate_score=0.0,
    )
    equity_report = {
        "basket_id": "equity",
        "status": "active",
        "selected_symbols": ["AAA", "BBB", "CCC"],
    }
    market_summary = summary(
        [
            candidate("AAA", momentum=1, trend_quality=1, ranking_count=20),
            candidate("BBB", momentum=8, trend_quality=8, ranking_count=2),
            candidate("CCC", momentum=9, trend_quality=9, ranking_count=2),
        ]
    )

    result = construct_dynamic_equity_target_portfolio(
        equity_report,
        equity_universe=["AAA", "BBB", "CCC"],
        market_summary=market_summary,
        policy=policy,
    )

    weights = weights_by_symbol(result)
    assert max(weights.values()) <= pytest.approx(0.30)
    assert sum(weights.values()) == pytest.approx(0.98)
    assert "AAA" in result["diagnostics"]["capped_symbols"]


def test_constructor_falls_back_to_equal_weights_when_summary_missing():
    equity_report = {
        "basket_id": "equity",
        "status": "active",
        "selected_symbols": ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"],
    }

    result = construct_dynamic_equity_target_portfolio(
        equity_report,
        equity_universe=["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"],
        market_summary=None,
    )

    assert result["selected_count"] == DEFAULT_DYNAMIC_EQUITY_POLICY.fallback_positions
    assert result["diagnostics"]["fallback_used"] is True
    assert [row["symbol"] for row in result["target_portfolio"]] == ["AAA", "BBB", "CCC", "DDD", "EEE"]
    assert all(row["target_weight"] == pytest.approx(0.196) for row in result["target_portfolio"])


def test_constructor_rejects_outside_universe_symbol():
    equity_report = {
        "basket_id": "equity",
        "status": "active",
        "selected_symbols": ["AAA", "BAD", "CCC"],
    }

    with pytest.raises(ValueError, match="selected equity symbols must be in equity universe: BAD"):
        construct_dynamic_equity_target_portfolio(
            equity_report,
            equity_universe=["AAA", "BBB", "CCC"],
            market_summary=None,
        )
```

- [ ] **Step 2: Run constructor tests and verify they fail**

Run:

```bash
python -m pytest tests/test_dynamic_equity_portfolio_constructor.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'lumibot.example_strategies.dynamic_equity_portfolio_constructor'`.

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/test_dynamic_equity_portfolio_constructor.py
git commit -m "test: cover dynamic equity portfolio constructor"
```

---

### Task 2: Implement Dynamic Constructor Module

**Files:**
- Create: `lumibot/example_strategies/dynamic_equity_portfolio_constructor.py`
- Test: `tests/test_dynamic_equity_portfolio_constructor.py`

- [ ] **Step 1: Add constructor implementation**

Create `lumibot/example_strategies/dynamic_equity_portfolio_constructor.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any

from lumibot.example_strategies.ai_trading_team_equity_only_helpers import (
    ACTIVE_SELECTION_STATUSES,
    EQUITY_BASKET_ID,
)


@dataclass(frozen=True)
class DynamicEquityPortfolioPolicy:
    min_positions: int = 3
    max_positions: int = 10
    fallback_positions: int = 5
    cash_buffer_weight: float = 0.02
    max_single_weight: float = 0.30
    min_single_weight: float = 0.05
    leading_score_keep_ratio: float = 0.70
    minimum_candidate_score: float = 0.35


DEFAULT_DYNAMIC_EQUITY_POLICY = DynamicEquityPortfolioPolicy()

GROUP_WEIGHTS = {
    "momentum": 0.35,
    "trend_quality": 0.25,
    "risk_adjusted_momentum": 0.20,
    "breakout_near_high": 0.15,
    "volume_confirmation": 0.05,
}


def construct_dynamic_equity_target_portfolio(
    equity_report: dict[str, Any],
    *,
    equity_universe: list[str],
    market_summary: dict[str, Any] | None = None,
    policy: DynamicEquityPortfolioPolicy = DEFAULT_DYNAMIC_EQUITY_POLICY,
) -> dict[str, Any]:
    if not isinstance(equity_report, dict):
        raise ValueError("equity report must be an object.")
    _validate_policy(policy)
    selected_symbols = _selected_candidate_symbols(equity_report, equity_universe=equity_universe, policy=policy)
    rows_by_symbol = _candidate_rows_by_symbol(market_summary)
    scored = _score_candidates(selected_symbols, rows_by_symbol, market_summary=market_summary, policy=policy)
    if not scored:
        return _fallback_result(selected_symbols, policy=policy, reason="market_summary_missing_or_unusable")

    final_candidates = _select_final_candidates(scored, policy=policy)
    if not final_candidates:
        return _fallback_result(selected_symbols, policy=policy, reason="no_candidate_passed_dynamic_threshold")

    weights, capped_symbols, pruned_symbols = _target_weights(final_candidates, policy=policy)
    target_portfolio = [
        {"basket_id": EQUITY_BASKET_ID, "symbol": row["symbol"], "target_weight": weights[row["symbol"]]}
        for row in final_candidates
        if row["symbol"] in weights
    ]
    candidate_scores = [
        {
            "symbol": row["symbol"],
            "evidence_score": row["evidence_score"],
            "volatility_penalty": row["volatility_penalty"],
            "adjusted_score": row["adjusted_score"],
            "final_weight": weights.get(row["symbol"], 0.0),
            "selection_source": row["selection_source"],
        }
        for row in final_candidates
    ]
    return {
        "portfolio_mode": "dynamic_equity",
        "selected_count": len(target_portfolio),
        "target_portfolio": target_portfolio,
        "cash_buffer_weight": policy.cash_buffer_weight,
        "weighting_method": "evidence_score_with_volatility_adjustment",
        "diagnostics": {
            "fallback_used": False,
            "selection_reason": "selected by adjusted evidence score breadth",
            "min_positions": policy.min_positions,
            "max_positions": policy.max_positions,
            "max_single_weight": policy.max_single_weight,
            "min_single_weight": policy.min_single_weight,
            "capped_symbols": capped_symbols,
            "pruned_symbols": pruned_symbols,
            "candidate_scores": candidate_scores,
        },
    }


def latest_market_summary_from_agent_result(agent_result: Any) -> dict[str, Any] | None:
    for event in reversed(list(getattr(agent_result, "tool_results", []) or [])):
        if getattr(event, "tool_name", None) != "market_load_history_tables_summary":
            continue
        payload = getattr(event, "payload", None)
        if isinstance(payload, dict):
            result = payload.get("result")
            if isinstance(result, dict):
                return result
            if _looks_like_market_summary(payload):
                return payload
    return None


def _validate_policy(policy: DynamicEquityPortfolioPolicy) -> None:
    if policy.min_positions < 1:
        raise ValueError("min_positions must be at least 1.")
    if policy.max_positions < policy.min_positions:
        raise ValueError("max_positions must be greater than or equal to min_positions.")
    if policy.fallback_positions < 1:
        raise ValueError("fallback_positions must be at least 1.")
    if not 0 <= policy.cash_buffer_weight < 1:
        raise ValueError("cash_buffer_weight must be between 0 and 1.")
    if policy.max_single_weight <= 0 or policy.max_single_weight > 1:
        raise ValueError("max_single_weight must be greater than 0 and no more than 1.")
    if policy.min_single_weight < 0 or policy.min_single_weight > policy.max_single_weight:
        raise ValueError("min_single_weight must be between 0 and max_single_weight.")


def _selected_candidate_symbols(
    equity_report: dict[str, Any],
    *,
    equity_universe: list[str],
    policy: DynamicEquityPortfolioPolicy,
) -> list[str]:
    basket_id = str(equity_report.get("basket_id") or "").strip().lower()
    if basket_id != EQUITY_BASKET_ID:
        raise ValueError("basket_id must be 'equity'.")
    status = str(equity_report.get("status") or "").strip().lower()
    if status not in ACTIVE_SELECTION_STATUSES:
        raise ValueError("equity report must be active.")
    raw_symbols = equity_report.get("selected_symbols")
    if not isinstance(raw_symbols, list):
        raise ValueError(
            f"selected_symbols must contain between {policy.min_positions} and {policy.max_positions} symbols."
        )
    allowed = {str(symbol).strip().upper() for symbol in equity_universe if str(symbol).strip()}
    normalized = []
    seen = set()
    for symbol in raw_symbols:
        if not isinstance(symbol, str):
            raise ValueError("selected_symbols must contain only strings.")
        clean = symbol.strip().upper()
        if not clean:
            raise ValueError("selected_symbols must contain only non-empty symbols.")
        if clean in seen:
            raise ValueError("selected_symbols must be unique.")
        seen.add(clean)
        normalized.append(clean)
    outside = [symbol for symbol in normalized if symbol not in allowed]
    if outside:
        raise ValueError(f"selected equity symbols must be in equity universe: {', '.join(outside)}.")
    if not normalized:
        raise ValueError("selected_symbols must contain at least one symbol.")
    if len(normalized) > policy.max_positions:
        normalized = normalized[: policy.max_positions]
    return normalized


def _candidate_rows_by_symbol(market_summary: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(market_summary, dict):
        return {}
    rows = market_summary.get("candidate_summary")
    if not isinstance(rows, list):
        return {}
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol:
            result[symbol] = row
    return result


def _score_candidates(
    selected_symbols: list[str],
    rows_by_symbol: dict[str, dict[str, Any]],
    *,
    market_summary: dict[str, Any] | None,
    policy: DynamicEquityPortfolioPolicy,
) -> list[dict[str, Any]]:
    candidate_symbols = list(selected_symbols)
    for symbol in rows_by_symbol:
        if symbol not in candidate_symbols:
            candidate_symbols.append(symbol)
    ranking_limit = _positive_float(
        (market_summary or {}).get("ranking_limit")
        or (market_summary or {}).get("coverage", {}).get("top_n")
        or 10
    )
    vols = [
        _positive_float(row.get("volatility_20"))
        for row in rows_by_symbol.values()
        if _positive_float(row.get("volatility_20")) is not None
    ]
    median_vol = median(vols) if vols else None
    llm_order = {symbol: index for index, symbol in enumerate(selected_symbols)}
    scored = []
    for symbol in candidate_symbols:
        row = rows_by_symbol.get(symbol)
        if not row:
            continue
        evidence_score = _evidence_score(row, ranking_limit=ranking_limit)
        if evidence_score <= 0:
            continue
        volatility_penalty = _volatility_penalty(row.get("volatility_20"), median_vol)
        adjusted_score = evidence_score / volatility_penalty
        scored.append(
            {
                "symbol": symbol,
                "evidence_score": round(evidence_score, 6),
                "volatility_penalty": round(volatility_penalty, 6),
                "adjusted_score": round(adjusted_score, 6),
                "llm_order": llm_order.get(symbol, len(llm_order) + 999),
                "selection_source": "llm_and_summary" if symbol in llm_order else "summary_only",
            }
        )
    return sorted(scored, key=lambda row: (-row["adjusted_score"], row["llm_order"], row["symbol"]))


def _evidence_score(row: dict[str, Any], *, ranking_limit: float) -> float:
    best_rank_by_group = row.get("best_rank_by_group")
    if not isinstance(best_rank_by_group, dict):
        return 0.0
    score = 0.0
    for group, weight in GROUP_WEIGHTS.items():
        rank = _positive_float(best_rank_by_group.get(group))
        if rank is None:
            continue
        group_score = 1.0 - ((rank - 1.0) / max(1.0, ranking_limit - 1.0))
        score += weight * max(0.0, min(1.0, group_score))
    ranking_count = _positive_float(row.get("ranking_count")) or 0.0
    return min(1.0, score + min(0.10, ranking_count * 0.01))


def _volatility_penalty(value: Any, median_volatility: float | None) -> float:
    vol = _positive_float(value)
    if vol is None or median_volatility is None or median_volatility <= 0:
        return 1.0
    return max(0.75, min(1.50, vol / median_volatility))


def _select_final_candidates(
    scored: list[dict[str, Any]],
    *,
    policy: DynamicEquityPortfolioPolicy,
) -> list[dict[str, Any]]:
    if not scored:
        return []
    leading_score = scored[0]["adjusted_score"]
    threshold = max(policy.minimum_candidate_score, leading_score * policy.leading_score_keep_ratio)
    selected = [row for row in scored if row["adjusted_score"] >= threshold]
    selected = selected[: policy.max_positions]
    if len(selected) < min(policy.min_positions, len(scored)):
        selected = scored[: min(policy.min_positions, len(scored))]
    return selected


def _target_weights(
    selected: list[dict[str, Any]],
    *,
    policy: DynamicEquityPortfolioPolicy,
) -> tuple[dict[str, float], list[str], list[str]]:
    active = list(selected)
    pruned = []
    capped = []
    weights = _normalize_scores(active, exposure=1.0 - policy.cash_buffer_weight)
    while len(active) > policy.min_positions:
        tiny = [symbol for symbol, weight in weights.items() if weight < policy.min_single_weight]
        if not tiny:
            break
        remove = tiny[0]
        pruned.append(remove)
        active = [row for row in active if row["symbol"] != remove]
        weights = _normalize_scores(active, exposure=1.0 - policy.cash_buffer_weight)
    weights, capped = _cap_and_redistribute(weights, max_weight=policy.max_single_weight, exposure=1.0 - policy.cash_buffer_weight)
    return weights, capped, pruned


def _normalize_scores(selected: list[dict[str, Any]], *, exposure: float) -> dict[str, float]:
    total = sum(max(0.0, row["adjusted_score"]) for row in selected)
    if total <= 0:
        equal = exposure / len(selected)
        return {row["symbol"]: round(equal, 6) for row in selected}
    return {
        row["symbol"]: round(exposure * max(0.0, row["adjusted_score"]) / total, 6)
        for row in selected
    }


def _cap_and_redistribute(
    weights: dict[str, float],
    *,
    max_weight: float,
    exposure: float,
) -> tuple[dict[str, float], list[str]]:
    remaining = dict(weights)
    capped = {}
    capped_symbols = []
    while True:
        over = [symbol for symbol, weight in remaining.items() if weight > max_weight]
        if not over:
            break
        for symbol in over:
            capped[symbol] = max_weight
            capped_symbols.append(symbol)
            remaining.pop(symbol)
        remaining_exposure = exposure - sum(capped.values())
        if not remaining or remaining_exposure <= 0:
            break
        subtotal = sum(remaining.values())
        if subtotal <= 0:
            equal = remaining_exposure / len(remaining)
            remaining = {symbol: equal for symbol in remaining}
        else:
            remaining = {
                symbol: remaining_exposure * weight / subtotal
                for symbol, weight in remaining.items()
            }
    combined = {**capped, **remaining}
    total = sum(combined.values())
    if total > 0:
        combined = {symbol: round(weight * exposure / total, 6) for symbol, weight in combined.items()}
    return combined, sorted(set(capped_symbols))


def _fallback_result(
    selected_symbols: list[str],
    *,
    policy: DynamicEquityPortfolioPolicy,
    reason: str,
) -> dict[str, Any]:
    symbols = selected_symbols[: min(policy.fallback_positions, len(selected_symbols))]
    exposure = 1.0 - policy.cash_buffer_weight
    weight = round(exposure / len(symbols), 6)
    target_portfolio = [
        {"basket_id": EQUITY_BASKET_ID, "symbol": symbol, "target_weight": weight}
        for symbol in symbols
    ]
    return {
        "portfolio_mode": "dynamic_equity",
        "selected_count": len(target_portfolio),
        "target_portfolio": target_portfolio,
        "cash_buffer_weight": policy.cash_buffer_weight,
        "weighting_method": "fallback_equal_weight",
        "diagnostics": {
            "fallback_used": True,
            "selection_reason": reason,
            "capped_symbols": [],
            "pruned_symbols": [],
            "candidate_scores": [],
        },
    }


def _looks_like_market_summary(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("candidate_summary"), list) and isinstance(payload.get("rank_groups"), dict)


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or number != number or number in (float("inf"), float("-inf")):
        return None
    return number
```

- [ ] **Step 2: Run constructor tests**

Run:

```bash
python -m pytest tests/test_dynamic_equity_portfolio_constructor.py -q
```

Expected: PASS.

- [ ] **Step 3: Fix only implementation bugs exposed by these tests**

If a test fails due to a syntax, rounding, or `pytest.approx` issue, fix the implementation or test assertion directly. Do not change the public constructor contract in this task.

- [ ] **Step 4: Commit constructor implementation**

```bash
git add lumibot/example_strategies/dynamic_equity_portfolio_constructor.py tests/test_dynamic_equity_portfolio_constructor.py
git commit -m "feat: add dynamic equity portfolio constructor"
```

---

### Task 3: Wire Constructor Into Equity-Only Strategy

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`
- Modify: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Update existing target portfolio tests to expect dynamic output**

In `tests/test_ai_trading_team_equity_only_llm.py`, replace `test_equity_only_target_portfolio_uses_five_selected_symbols_at_equal_weights` with:

```python
def test_equity_only_target_portfolio_uses_dynamic_constructor_with_fallback_weights():
    module = load_module()

    result = module.equity_only_target_portfolio(
        {
            "basket_id": "equity",
            "status": "active",
            "selected_symbols": ["orcl", "msft", "nvda", "aapl", "amzn", "tsla"],
            "reason_brief": "Six credible equity candidates.",
        },
        equity_universe=["AAPL", "AMZN", "MSFT", "NVDA", "ORCL", "TSLA"],
        market_summary=None,
    )

    assert result["portfolio_mode"] == "dynamic_equity"
    assert result["selected_count"] == 5
    assert result["diagnostics"]["fallback_used"] is True
    assert result["target_portfolio"] == [
        {"basket_id": "equity", "symbol": "ORCL", "target_weight": pytest.approx(0.196)},
        {"basket_id": "equity", "symbol": "MSFT", "target_weight": pytest.approx(0.196)},
        {"basket_id": "equity", "symbol": "NVDA", "target_weight": pytest.approx(0.196)},
        {"basket_id": "equity", "symbol": "AAPL", "target_weight": pytest.approx(0.196)},
        {"basket_id": "equity", "symbol": "AMZN", "target_weight": pytest.approx(0.196)},
    ]
```

Replace `test_equity_only_target_portfolio_accepts_selected_status_synonym_for_top5` with:

```python
def test_equity_only_target_portfolio_accepts_selected_status_synonym_for_dynamic_candidates():
    module = load_module()

    result = module.equity_only_target_portfolio(
        {
            "basket_id": "equity",
            "status": "selected",
            "selected_symbols": ["spy", "orcl", "msft"],
            "reason_brief": "Three active selections.",
        },
        equity_universe=["SPY", "ORCL", "MSFT", "AAPL", "NVDA", "AMZN"],
        market_summary=None,
    )

    assert result["selected_count"] == 3
    assert [row["symbol"] for row in result["target_portfolio"]] == ["SPY", "ORCL", "MSFT"]
    assert sum(row["target_weight"] for row in result["target_portfolio"]) == pytest.approx(0.98)
```

- [ ] **Step 2: Add strategy wiring test**

Add this test near existing workflow tests:

```python
def test_equity_only_workflow_passes_constructor_target_portfolio_to_planner(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamEquityOnlyLLMStrategy)
    strategy.parameters["basket_universes"] = {"equity": ["AAA", "BBB", "CCC", "DDD", "EEE"]}
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "status": "active",
            "candidate_symbols": ["AAA", "BBB", "CCC", "DDD", "EEE"],
            "selected_symbols": ["AAA", "BBB", "CCC"],
            "reason_brief": "Three credible candidates.",
        }
    )

    captured = {}

    def fake_planner(strategy_arg, *, date, target_portfolio):
        captured["target_portfolio"] = target_portfolio
        return {
            "schema_version": "1.0",
            "date": date,
            "target_portfolio": target_portfolio,
            "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []},
            "warnings": [],
        }

    monkeypatch.setattr(module, "target_portfolio_to_execution_plan", fake_planner)

    strategy._run_equity_only_workflow(
        "2024-09-05",
        scheduled_workflow_event={"date": "2024-09-05", "status": "run"},
    )

    assert captured["target_portfolio"]
    assert sum(row["target_weight"] for row in captured["target_portfolio"]) == pytest.approx(0.98)
    assert strategy._last_dynamic_equity_constructor_result["selected_count"] == 3
```

- [ ] **Step 3: Run modified equity tests and verify failure**

Run:

```bash
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_only_target_portfolio_uses_dynamic_constructor_with_fallback_weights tests/test_ai_trading_team_equity_only_llm.py::test_equity_only_target_portfolio_accepts_selected_status_synonym_for_dynamic_candidates tests/test_ai_trading_team_equity_only_llm.py::test_equity_only_workflow_passes_constructor_target_portfolio_to_planner -q
```

Expected: FAIL because `equity_only_target_portfolio()` still returns a list, does not accept `market_summary`, and `_last_dynamic_equity_constructor_result` is not initialized.

- [ ] **Step 4: Import constructor helpers**

In `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`, add imports:

```python
from lumibot.example_strategies.dynamic_equity_portfolio_constructor import (
    construct_dynamic_equity_target_portfolio,
    latest_market_summary_from_agent_result,
)
```

Remove no-longer-needed fixed constants:

```python
EQUITY_ONLY_TARGET_WEIGHT = 1.0
EQUITY_ONLY_SELECTION_COUNT = 5
EQUITY_ONLY_EQUAL_WEIGHT = EQUITY_ONLY_TARGET_WEIGHT / EQUITY_ONLY_SELECTION_COUNT
```

- [ ] **Step 5: Replace `_selected_equity_symbols` validation**

In `ai_trading_team_equity_only_llm.py`, remove `_selected_equity_symbols()` if no other code uses it after the constructor is wired.

If another test still imports it indirectly, keep a compatibility helper:

```python
def _selected_equity_symbols(
    equity_report: dict[str, Any],
    *,
    equity_universe: list[str],
) -> list[str]:
    result = construct_dynamic_equity_target_portfolio(
        equity_report,
        equity_universe=equity_universe,
        market_summary=None,
    )
    return [row["symbol"] for row in result["target_portfolio"]]
```

- [ ] **Step 6: Change `equity_only_target_portfolio()` to return constructor result**

Replace the function with:

```python
def equity_only_target_portfolio(
    equity_report: dict[str, Any],
    *,
    equity_universe: list[str],
    market_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return construct_dynamic_equity_target_portfolio(
        equity_report,
        equity_universe=equity_universe,
        market_summary=market_summary,
    )
```

- [ ] **Step 7: Initialize constructor result state**

In `_initialize_equity_only_workflow_state()`, after `_last_target_portfolio_planner_result = None`, add:

```python
self._last_dynamic_equity_constructor_result = None
```

- [ ] **Step 8: Wire workflow to planner using `target_portfolio` field**

In `_run_equity_only_workflow()`, replace:

```python
target_portfolio = equity_only_target_portfolio(equity_report, equity_universe=equity_universe)
self._last_target_portfolio_planner_result = None
planner_result = target_portfolio_to_execution_plan(
    self,
    date=current_date,
    target_portfolio=target_portfolio,
)
```

with:

```python
market_summary = latest_market_summary_from_agent_result(equity_result)
constructor_result = equity_only_target_portfolio(
    equity_report,
    equity_universe=equity_universe,
    market_summary=market_summary,
)
self._last_dynamic_equity_constructor_result = constructor_result
target_portfolio = constructor_result["target_portfolio"]
self._last_target_portfolio_planner_result = None
planner_result = target_portfolio_to_execution_plan(
    self,
    date=current_date,
    target_portfolio=target_portfolio,
)
```

- [ ] **Step 9: Run focused strategy wiring tests**

Run:

```bash
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_only_target_portfolio_uses_dynamic_constructor_with_fallback_weights tests/test_ai_trading_team_equity_only_llm.py::test_equity_only_target_portfolio_accepts_selected_status_synonym_for_dynamic_candidates tests/test_ai_trading_team_equity_only_llm.py::test_equity_only_workflow_passes_constructor_target_portfolio_to_planner -q
```

Expected: PASS.

- [ ] **Step 10: Commit strategy wiring**

```bash
git add lumibot/example_strategies/ai_trading_team_equity_only_llm.py tests/test_ai_trading_team_equity_only_llm.py
git commit -m "feat: wire dynamic equity constructor into strategy"
```

---

### Task 4: Update Equity Prompts And Prompt Tests

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
- Modify: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Update prompt tests first**

In `tests/test_ai_trading_team_equity_only_llm.py`, update prompt assertions that mention exactly five.

Replace assertions like:

```python
assert "selected_symbols must contain exactly five unique symbols" in task_prompt
```

with:

```python
assert "selected_symbols must contain between 3 and 10 unique symbols" in task_prompt
assert "deterministic constructor" in task_prompt.lower()
assert "do not calculate exact per-symbol target weights" in task_prompt.lower()
```

Add this prompt regression test:

```python
def test_equity_prompts_remove_fixed_five_equal_weight_assumptions():
    helpers = importlib.import_module("lumibot.example_strategies.ai_trading_team_equity_only_helpers")

    prompts = [
        helpers.equity_basket_agent_system_prompt("AAPL, MSFT, NVDA"),
        helpers.qqq_historical_equity_basket_agent_system_prompt("AAPL, MSFT, NVDA"),
        helpers.equity_basket_agent_task_prompt(),
    ]
    joined = " ".join(prompts).lower()

    for forbidden in (
        "choose exactly five",
        "exactly five unique",
        "five selected stocks equal",
        "equal target weights",
        "target_weight must be 1.0",
    ):
        assert forbidden not in joined

    assert "credible candidates" in joined
    assert "between 3 and 10" in joined
    assert "deterministic constructor" in joined
    assert "do not calculate exact per-symbol target weights" in joined
```

- [ ] **Step 2: Run prompt tests and verify they fail**

Run:

```bash
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_prompts_remove_fixed_five_equal_weight_assumptions -q
```

Expected: FAIL because prompt text still says exactly five / equal target weights.

- [ ] **Step 3: Rewrite system prompts**

In `ai_trading_team_equity_only_helpers.py`, replace `equity_basket_agent_system_prompt()` with:

```python
def equity_basket_agent_system_prompt(symbols: str) -> str:
    return (
        f"Equity-only selection role: identify credible stock candidates from the assigned basket_symbols ({symbols}). "
        "You cannot place orders, size trades, or calculate exact per-symbol target weights. "
        "A downstream deterministic constructor will choose the final holding count and target weights from your "
        "candidate evidence plus local rank and volatility data. Use market_load_history_tables_summary first for "
        "multi-symbol comparison. "
        f"{EQUITY_EVIDENCE_INTERPRETATION_POLICY} "
        "Do not invent sector, style, or category labels not provided by tools. "
        "If the leading candidate set is clear from rank evidence, select without news. "
        "Use alpaca_news only as a tie-breaker or risk/catalyst check for leading candidates when rank evidence "
        "is close, conflicting, or uncertain; when used, request news only for leading candidates. "
        "If news is unavailable, continue with rank-only evidence. Return strict JSON only. Do not place orders."
    )
```

Replace `qqq_historical_equity_basket_agent_system_prompt()` with:

```python
def qqq_historical_equity_basket_agent_system_prompt(symbols: str) -> str:
    return (
        f"Equity-only selection role: identify credible stock candidates from the QQQ historical constituent universe "
        f"provided in basket_symbols ({symbols}). "
        "The provided basket_symbols represent the QQQ historical constituent universe available for the current "
        "backtest date. You cannot place orders, size trades, or calculate exact per-symbol target weights. "
        "A downstream deterministic constructor will choose the final holding count and target weights from your "
        "candidate evidence plus local rank and volatility data. Use market_load_history_tables_summary first for "
        "multi-symbol comparison. "
        f"{EQUITY_EVIDENCE_INTERPRETATION_POLICY} "
        "Do not invent sector, style, or category labels not provided by tools. "
        "Do not assume QQQ membership itself makes a stock superior; select from current rank evidence. "
        "Do not choose based on index weight alone. "
        "If the leading candidate set is clear from rank evidence, select without news. "
        "Use alpaca_news only as a tie-breaker or risk/catalyst check for leading candidates when rank evidence "
        "is close, conflicting, or uncertain; when used, request news only for leading candidates. "
        "If news is unavailable, continue with rank-only evidence. Use only symbols in the provided "
        "basket_symbols and do not add symbols outside the provided universe. Return strict JSON only. "
        "Do not place orders."
    )
```

- [ ] **Step 4: Rewrite task prompt**

Replace `equity_basket_agent_task_prompt()` with:

```python
def equity_basket_agent_task_prompt() -> str:
    return (
        "Review only the provided basket_symbols. First call market_load_history_tables_summary with "
        "symbols=basket_symbols, length=252, timestep='day', top_n=10, and candidate_summary_limit=25. "
        "Compare the five rank groups using the Evidence Interpretation Policy from your system prompt. "
        "Return an ordered list of credible candidates, not exact portfolio weights. selected_symbols must contain "
        "between 3 and 10 unique symbols from basket_symbols. Prefer fewer names when evidence leadership is narrow; "
        "include more names when evidence strength is broad or close across several candidates. "
        "Avoid selecting a stock supported by only one evidence group unless the other leading candidates are weaker "
        "or conflicting; explain the exception in reason_brief. "
        "If the leading candidate set is clear from rank evidence, select without news. "
        "If leading candidates are close, conflicting, or uncertain, call alpaca_news for those leading candidates "
        "only. If alpaca_news is unavailable or errors, continue with rank-only evidence. "
        "Return exactly one strict JSON object with basket_id, status, candidate_symbols, selected_symbols, "
        "and reason_brief. Use status='active'. candidate_symbols must copy the assigned basket_symbols exactly; "
        "do not replace it with a shortlist. Do not calculate exact per-symbol target weights; the downstream "
        "deterministic constructor will calculate final selected count and target weights."
    )
```

- [ ] **Step 5: Run prompt tests**

Run:

```bash
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_prompts_remove_fixed_five_equal_weight_assumptions -q
```

Expected: PASS.

- [ ] **Step 6: Run all equity strategy tests and fix updated expectations**

Run:

```bash
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q
```

Expected: PASS after updating any remaining assertions that still expect exactly five or 0.2 weights.

- [ ] **Step 7: Commit prompt update**

```bash
git add lumibot/example_strategies/ai_trading_team_equity_only_helpers.py tests/test_ai_trading_team_equity_only_llm.py
git commit -m "fix: update equity prompts for dynamic construction"
```

---

### Task 5: Add Trace-Friendly Constructor Diagnostics

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`
- Modify: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Add test that constructor diagnostics reach workflow context**

Add this test:

```python
def test_equity_only_workflow_records_dynamic_constructor_result_for_trace(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamEquityOnlyLLMStrategy)
    strategy.parameters["basket_universes"] = {"equity": ["AAA", "BBB", "CCC"]}
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "status": "active",
            "candidate_symbols": ["AAA", "BBB", "CCC"],
            "selected_symbols": ["AAA", "BBB", "CCC"],
            "reason_brief": "Three candidates.",
        }
    )

    monkeypatch.setattr(
        module,
        "target_portfolio_to_execution_plan",
        lambda strategy_arg, *, date, target_portfolio: {
            "schema_version": "1.0",
            "date": date,
            "target_portfolio": target_portfolio,
            "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []},
            "warnings": [],
        },
    )

    strategy._run_equity_only_workflow(
        "2024-09-05",
        scheduled_workflow_event={"date": "2024-09-05", "status": "run"},
    )

    result = strategy._last_dynamic_equity_constructor_result
    assert result["portfolio_mode"] == "dynamic_equity"
    assert "diagnostics" in result
    assert "candidate_scores" in result["diagnostics"]
```

- [ ] **Step 2: Run test and verify pass or targeted failure**

Run:

```bash
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_equity_only_workflow_records_dynamic_constructor_result_for_trace -q
```

Expected: PASS if Task 3 already stores `_last_dynamic_equity_constructor_result`; otherwise FAIL and fix by adding that assignment exactly as specified in Task 3.

- [ ] **Step 3: Include constructor result in planner blocker records**

In `_record_equity_only_planner_blocker()` or the call sites around planner blockers, include:

```python
"dynamic_equity_constructor": self._last_dynamic_equity_constructor_result,
```

only if the event payload already supports arbitrary extra fields. If the helper currently has a narrow schema, do not widen it in this task; storing `_last_dynamic_equity_constructor_result` on strategy is sufficient for trace/debug follow-up.

- [ ] **Step 4: Run equity tests**

Run:

```bash
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit diagnostics**

```bash
git add lumibot/example_strategies/ai_trading_team_equity_only_llm.py tests/test_ai_trading_team_equity_only_llm.py
git commit -m "test: preserve dynamic constructor diagnostics"
```

---

### Task 6: Focused Regression And Smoke Backtest

**Files:**
- No planned code changes.
- Create validation note only after successful verification if useful: `docs/superpowers/notes/2026-08-23-dynamic-equity-portfolio-constructor-validation.md`

- [ ] **Step 1: Run focused unit tests**

Run:

```bash
python -m pytest tests/test_dynamic_equity_portfolio_constructor.py tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_manager.py -q
```

Expected: PASS.

- [ ] **Step 2: Run ruff on touched files**

Run:

```bash
python -m ruff check lumibot/example_strategies/dynamic_equity_portfolio_constructor.py lumibot/example_strategies/ai_trading_team_equity_only_llm.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py tests/test_dynamic_equity_portfolio_constructor.py tests/test_ai_trading_team_equity_only_llm.py
```

Expected: PASS.

- [ ] **Step 3: Run one-day smoke backtest with default development model**

Use the user's local API env file:

```powershell
$env:AI_TRADING_TEAM_MODEL = "gpt-5.6-luna"
python scripts\run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2024-09-05 --end 2024-09-06 --run-frequency weekly --weekly-run-weekday THU --env-file D:\Lumibot\project_notes\API.txt --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 240
```

Expected:

```text
status: passed
```

The smoke run should show:

1. Equity agent prompt no longer says exactly five/equal target weights.
2. Equity agent returns 3-10 selected symbols.
3. Constructor result exists.
4. Planner receives `target_portfolio`.
5. Execution agent receives an execution plan.

- [ ] **Step 4: Inspect smoke artifact for constructor behavior**

Find the newest QQQ historical equity-only artifact and inspect whether it contains agent runtime trace files:

```powershell
$artifact = Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
Write-Host "artifact:" $artifact.FullName
Get-ChildItem $artifact.FullName -Recurse -File |
    Where-Object { $_.Name -like "*agent_runtime*.parquet" -or $_.Name -like "*agent_runtime*.json*" } |
    Select-Object FullName
```

Expected: at least one trace artifact path is printed. If constructor diagnostics are not directly visible in replay yet, confirm `_last_dynamic_equity_constructor_result` was used by planner through test coverage and leave UI formatting for future work.

- [ ] **Step 5: Write validation note**

Create `docs/superpowers/notes/2026-08-23-dynamic-equity-portfolio-constructor-validation.md`:

```markdown
# Dynamic Equity Portfolio Constructor Validation

## Summary

Implemented dynamic equity portfolio construction for the QQQ historical equity-only LLM strategy.

## Verification

- `python -m pytest tests/test_dynamic_equity_portfolio_constructor.py tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_manager.py -q`
- `python -m ruff check lumibot/example_strategies/dynamic_equity_portfolio_constructor.py lumibot/example_strategies/ai_trading_team_equity_only_llm.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py tests/test_dynamic_equity_portfolio_constructor.py tests/test_ai_trading_team_equity_only_llm.py`
- One-day smoke backtest:
  - strategy: `qqq-historical-equity-only-llm`
  - window: `2024-09-05` to `2024-09-06`
  - model: `gpt-5.6-luna`
  - result: record the benchmark status line and the newest artifact path printed by Step 4

## Notes

- The constructor preserves a 2% cash buffer by targeting total equity exposure of 0.98.
- Exact selected count and target weights are deterministic local outputs, not LLM-authored numbers.
- Existing execution planner and execution agent behavior were preserved.
```

- [ ] **Step 6: Commit validation note**

```bash
git add docs/superpowers/notes/2026-08-23-dynamic-equity-portfolio-constructor-validation.md
git commit -m "docs: validate dynamic equity portfolio constructor"
```

---

## Plan Self-Review

Spec coverage:

- Dynamic selected count: Task 1 and Task 2.
- Evidence strength weighting: Task 1 and Task 2.
- Volatility adjustment: Task 1 and Task 2.
- Single-position cap and min weight: Task 1 and Task 2.
- Preserve planner/execution: Task 3 and Task 6.
- Prompt cleanup: Task 4.
- Trace-friendly diagnostics: Task 5.
- Testing and smoke backtest: Task 6.
- Real volatility data path: Task 0.

No placeholders are intentionally left. Runtime-specific paths are handled by PowerShell commands that discover the newest artifact directory.

---

## Execution Options

Plan complete and saved to `docs/superpowers/plans/2026-08-23-dynamic-equity-portfolio-constructor.md`. Two execution options:

**1. Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.
