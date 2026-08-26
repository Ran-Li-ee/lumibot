from __future__ import annotations

import math
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


def construct_dynamic_equity_target_portfolio(
    equity_report: dict[str, Any],
    *,
    equity_universe: list[str],
    market_summary: dict[str, Any] | None = None,
    policy: DynamicEquityPortfolioPolicy = DEFAULT_DYNAMIC_EQUITY_POLICY,
) -> dict[str, Any]:
    _validate_policy(policy)
    selected_symbols = _selected_equity_symbols(equity_report, equity_universe, policy)
    scored_candidates = _scored_candidates(selected_symbols, equity_universe, market_summary)
    exposure = 1.0 - policy.cash_buffer_weight
    evidence_profile = (
        str(market_summary.get("evidence_profile") or "").strip().lower()
        if isinstance(market_summary, dict)
        else ""
    )
    weighting_method = (
        "momentum_stage_score_with_volatility_adjustment"
        if evidence_profile == "momentum_stage"
        else "evidence_score_with_volatility_adjustment"
    )

    fallback_used = not any(candidate["usable_score"] for candidate in scored_candidates)
    capped_symbols: list[str] = []
    pruned_symbols: list[str] = []
    diagnostics_warnings: list[str] = []
    if fallback_used:
        selected_candidates = _fallback_candidates(selected_symbols, policy)
        cap_feasible = _cap_feasible(len(selected_candidates), exposure, policy)
        if cap_feasible:
            weights = _equal_weights(len(selected_candidates), exposure)
        else:
            weights = [policy.max_single_weight for _ in selected_candidates]
            capped_symbols = [candidate["symbol"] for candidate in selected_candidates]
        selection_reason = "fallback equal weight from selected symbols"
    else:
        selected_candidates = _select_candidates(scored_candidates, exposure, policy)
        selected_candidates, pruned_symbols, capped_symbols, weights, cap_feasible = _weighted_candidates(
            selected_candidates,
            exposure,
            policy,
        )
        selection_reason = "selected by adjusted evidence score breadth"

    if not cap_feasible:
        diagnostics_warnings.append(
            "max_single_weight is infeasible for selected_count and target exposure; capped best-effort weights used."
        )

    final_weights = {candidate["symbol"]: weight for candidate, weight in zip(selected_candidates, weights)}
    target_portfolio = [
        {
            "basket_id": EQUITY_BASKET_ID,
            "symbol": candidate["symbol"],
            "target_weight": final_weights[candidate["symbol"]],
        }
        for candidate in selected_candidates
    ]

    return {
        "portfolio_mode": "dynamic_equity",
        "selected_count": len(target_portfolio),
        "target_portfolio": target_portfolio,
        "cash_buffer_weight": policy.cash_buffer_weight,
        "weighting_method": weighting_method,
        "diagnostics": {
            "fallback_used": fallback_used,
            "selection_reason": selection_reason,
            "min_positions": policy.min_positions,
            "max_positions": policy.max_positions,
            "max_single_weight": policy.max_single_weight,
            "min_single_weight": policy.min_single_weight,
            "cap_feasible": cap_feasible,
            "capped_symbols": capped_symbols,
            "pruned_symbols": pruned_symbols,
            "warnings": diagnostics_warnings,
            "candidate_scores": _candidate_score_rows(scored_candidates, final_weights),
        },
    }


def latest_market_summary_from_agent_result(agent_result: Any) -> dict[str, Any] | None:
    tool_results = getattr(agent_result, "tool_results", None)
    if not isinstance(tool_results, list):
        return None

    for event in reversed(tool_results):
        if getattr(event, "tool_name", None) != "market_load_history_tables_summary":
            continue
        payload = getattr(event, "payload", None)
        if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
            return payload["result"]
        if isinstance(payload, dict):
            return payload
    return None


def _validate_policy(policy: DynamicEquityPortfolioPolicy) -> None:
    if not isinstance(policy, DynamicEquityPortfolioPolicy):
        raise ValueError("policy must be a DynamicEquityPortfolioPolicy.")
    if policy.min_positions <= 0:
        raise ValueError("policy min_positions must be positive.")
    if policy.max_positions < policy.min_positions:
        raise ValueError("policy max_positions must be at least min_positions.")
    if policy.fallback_positions <= 0:
        raise ValueError("policy fallback_positions must be positive.")
    if policy.fallback_positions > policy.max_positions:
        raise ValueError("policy fallback_positions must be at most max_positions.")
    if not 0.0 <= policy.cash_buffer_weight < 1.0:
        raise ValueError("policy cash_buffer_weight must be in [0, 1).")
    if policy.max_single_weight <= 0.0 or policy.max_single_weight > 1.0:
        raise ValueError("policy max_single_weight must be in (0, 1].")
    if policy.min_single_weight < 0.0 or policy.min_single_weight > policy.max_single_weight:
        raise ValueError("policy min_single_weight must be between 0 and max_single_weight.")
    if not 0.0 <= policy.leading_score_keep_ratio <= 1.0:
        raise ValueError("policy leading_score_keep_ratio must be in [0, 1].")
    if not 0.0 <= policy.minimum_candidate_score <= 1.0:
        raise ValueError("policy minimum_candidate_score must be in [0, 1].")


def _selected_equity_symbols(
    equity_report: dict[str, Any],
    equity_universe: list[str],
    policy: DynamicEquityPortfolioPolicy,
) -> list[str]:
    if not isinstance(equity_report, dict):
        raise ValueError("equity report must be an object.")

    basket_id = str(equity_report.get("basket_id") or "").strip().lower()
    if basket_id != EQUITY_BASKET_ID:
        raise ValueError("basket_id must be 'equity'.")

    status = str(equity_report.get("status") or "").strip().lower()
    if status not in ACTIVE_SELECTION_STATUSES:
        raise ValueError("equity report must be active.")

    raw_symbols = equity_report.get("selected_symbols")
    if not isinstance(raw_symbols, list):
        raise ValueError("selected_symbols must be a list.")

    selected_symbols: list[str] = []
    seen: set[str] = set()
    for symbol in raw_symbols:
        if not isinstance(symbol, str):
            raise ValueError("selected_symbols must contain only strings.")
        clean = symbol.strip().upper()
        if not clean:
            raise ValueError("selected_symbols must contain only non-empty symbols.")
        if clean in seen:
            continue
        seen.add(clean)
        selected_symbols.append(clean)

    if len(selected_symbols) < policy.min_positions:
        raise ValueError(f"selected_symbols must contain at least {policy.min_positions} symbols.")

    allowed = _normalized_universe(equity_universe)
    outside = [symbol for symbol in selected_symbols if symbol not in allowed]
    if outside:
        raise ValueError(f"selected equity symbols must be in equity universe: {', '.join(outside)}")

    return selected_symbols


def _normalized_universe(equity_universe: list[str]) -> set[str]:
    return {str(symbol).strip().upper() for symbol in equity_universe if str(symbol).strip()}


def _scored_candidates(
    selected_symbols: list[str],
    equity_universe: list[str],
    market_summary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    allowed = _normalized_universe(equity_universe)
    candidates_by_symbol: dict[str, dict[str, Any]] = {}
    for index, symbol in enumerate(selected_symbols):
        candidates_by_symbol[symbol] = _candidate(symbol, "llm_selected", index, index)

    if isinstance(market_summary, dict):
        rows = market_summary.get("candidate_summary")
        ranking_limit = _positive_number(market_summary.get("ranking_limit")) or 1.0
        evidence_profile = str(market_summary.get("evidence_profile") or "").strip().lower()
        if isinstance(rows, list):
            for row_index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                symbol = _clean_symbol(row.get("symbol"))
                if not symbol or symbol not in allowed:
                    continue
                candidate = candidates_by_symbol.get(symbol)
                if candidate is None:
                    candidate = _candidate(symbol, "market_summary", len(selected_symbols) + row_index, row_index)
                    candidates_by_symbol[symbol] = candidate
                else:
                    candidate["summary_order"] = row_index
                    candidate["selection_source"] = "llm_selected+market_summary"
                candidate["row"] = row
                score_payload = _evidence_score_payload(row, ranking_limit, evidence_profile)
                candidate.update(score_payload)
                candidate["volatility_20"] = _positive_number(row.get("volatility_20"))
                candidate["usable_score"] = candidate["evidence_score"] > 0.0

    candidates = list(candidates_by_symbol.values())
    median_volatility = _median_volatility(candidates)
    for candidate in candidates:
        volatility = candidate.get("volatility_20")
        if median_volatility and volatility:
            candidate["volatility_penalty"] = _clamp(volatility / median_volatility, 0.75, 1.50)
        else:
            candidate["volatility_penalty"] = 1.0
        candidate["adjusted_score"] = candidate["evidence_score"] / candidate["volatility_penalty"]

    return sorted(candidates, key=_score_sort_key)


def _candidate(symbol: str, source: str, original_order: int, llm_order: int | None) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "selection_source": source,
        "original_order": original_order,
        "llm_order": llm_order,
        "summary_order": None,
        "row": None,
        "evidence_score": 0.0,
        "volatility_penalty": 1.0,
        "adjusted_score": 0.0,
        "volatility_20": None,
        "stage_support_score": None,
        "stage_penalty": None,
        "adjusted_stage_score": None,
        "stage_warning_flags": [],
        "usable_score": False,
    }


def _clean_symbol(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    symbol = value.strip().upper()
    return symbol or None


def _evidence_score_payload(
    row: dict[str, Any],
    ranking_limit: float,
    evidence_profile: str | None,
) -> dict[str, Any]:
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

    warning_flags = _stage_warning_flags(row.get("stage_warning_flags"))
    penalty = min(0.50, sum(STAGE_WARNING_PENALTIES.get(flag, 0.0) for flag in warning_flags))
    adjusted_stage_score = _clamp(support_score - penalty, 0.0, 1.0)
    return {
        "evidence_score": adjusted_stage_score,
        "stage_support_score": support_score,
        "stage_penalty": penalty,
        "adjusted_stage_score": adjusted_stage_score,
        "stage_warning_flags": warning_flags,
    }


def _stage_warning_flags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(flag) for flag in value]


def _legacy_evidence_score(row: dict[str, Any], ranking_limit: float) -> float:
    best_rank_by_group = row.get("best_rank_by_group")
    if not isinstance(best_rank_by_group, dict):
        best_rank_by_group = {}

    evidence_score = 0.0
    for group_name, group_weight in GROUP_WEIGHTS.items():
        rank = _positive_number(best_rank_by_group.get(group_name))
        if rank is None:
            continue
        group_score = 1.0 - ((rank - 1.0) / max(1.0, ranking_limit - 1.0))
        evidence_score += group_weight * _clamp(group_score, 0.0, 1.0)

    ranking_count = _positive_number(row.get("ranking_count")) or 0.0
    evidence_score += min(0.10, ranking_count * 0.01)
    return min(1.0, evidence_score)


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0.0:
        return None
    return number


def _median_volatility(candidates: list[dict[str, Any]]) -> float | None:
    volatilities = [
        candidate["volatility_20"]
        for candidate in candidates
        if candidate["usable_score"] and candidate.get("volatility_20")
    ]
    if not volatilities:
        return None
    return median(volatilities)


def _select_candidates(
    candidates: list[dict[str, Any]],
    exposure: float,
    policy: DynamicEquityPortfolioPolicy,
) -> list[dict[str, Any]]:
    if not candidates:
        return []

    leading_score = candidates[0]["adjusted_score"]
    threshold = max(policy.minimum_candidate_score, leading_score * policy.leading_score_keep_ratio)
    selected = [candidate for candidate in candidates if candidate["adjusted_score"] >= threshold]

    if len(selected) < policy.min_positions:
        selected_symbols = {candidate["symbol"] for candidate in selected}
        for candidate in candidates:
            if candidate["symbol"] in selected_symbols:
                continue
            selected.append(candidate)
            selected_symbols.add(candidate["symbol"])
            if len(selected) >= policy.min_positions:
                break

    cap_feasible_count = math.ceil(exposure / policy.max_single_weight)
    required_count = max(policy.min_positions, cap_feasible_count)
    scored_count = sum(1 for candidate in candidates if candidate["usable_score"])
    if scored_count >= required_count:
        selected_symbols = {candidate["symbol"] for candidate in selected}
        for candidate in candidates:
            if len(selected) >= required_count:
                break
            if candidate["symbol"] in selected_symbols:
                continue
            selected.append(candidate)
            selected_symbols.add(candidate["symbol"])

    return selected[: policy.max_positions]


def _fallback_candidates(
    selected_symbols: list[str],
    policy: DynamicEquityPortfolioPolicy,
) -> list[dict[str, Any]]:
    fallback_count = min(policy.fallback_positions, len(selected_symbols), policy.max_positions)
    if fallback_count < policy.min_positions:
        raise ValueError(f"selected_symbols must contain at least {policy.min_positions} symbols.")
    return [
        _candidate(symbol, "llm_selected", index, index)
        for index, symbol in enumerate(selected_symbols[:fallback_count])
    ]


def _weighted_candidates(
    candidates: list[dict[str, Any]],
    exposure: float,
    policy: DynamicEquityPortfolioPolicy,
) -> tuple[list[dict[str, Any]], list[str], list[str], list[float], bool]:
    selected = list(candidates)
    pruned_symbols: list[str] = []
    capped_symbols: list[str] = []
    weights = _score_weights(selected, exposure, policy, capped_symbols)

    while len(selected) > policy.min_positions:
        low_weight_indexes = [
            index
            for index, weight in enumerate(weights)
            if weight < policy.min_single_weight and _can_drop_position(len(selected) - 1, exposure, policy)
        ]
        if not low_weight_indexes:
            break
        prune_index = min(low_weight_indexes, key=lambda index: selected[index]["adjusted_score"])
        pruned_symbols.append(selected[prune_index]["symbol"])
        selected.pop(prune_index)
        capped_symbols = []
        weights = _score_weights(selected, exposure, policy, capped_symbols)

    weights = _round_weights(weights, exposure, selected, policy)
    cap_feasible = _cap_feasible(len(selected), exposure, policy)
    return selected, pruned_symbols, capped_symbols, weights, cap_feasible


def _score_weights(
    candidates: list[dict[str, Any]],
    exposure: float,
    policy: DynamicEquityPortfolioPolicy,
    capped_symbols: list[str],
) -> list[float]:
    if not candidates:
        return []

    scores = [max(candidate["adjusted_score"], 0.0) for candidate in candidates]
    score_total = sum(scores)
    if score_total <= 0.0:
        equal_weight = exposure / len(candidates)
        if not _cap_feasible(len(candidates), exposure, policy):
            capped_symbols.extend(candidate["symbol"] for candidate in candidates)
            equal_weight = policy.max_single_weight
        return [equal_weight for _ in candidates]

    if not _cap_feasible(len(candidates), exposure, policy):
        weights = [min(exposure * score / score_total, policy.max_single_weight) for score in scores]
        target_total = min(exposure, len(candidates) * policy.max_single_weight)
        _redistribute_under_cap_capacity(weights, target_total, policy.max_single_weight)
        capped_symbols.extend(
            candidate["symbol"] for candidate, weight in zip(candidates, weights) if weight >= policy.max_single_weight
        )
        return weights

    weights = [0.0 for _ in candidates]
    remaining_indexes = set(range(len(candidates)))
    remaining_exposure = exposure
    while remaining_indexes:
        remaining_score = sum(scores[index] for index in remaining_indexes)
        if remaining_score <= 0.0:
            proposed = {index: remaining_exposure / len(remaining_indexes) for index in remaining_indexes}
        else:
            proposed = {
                index: remaining_exposure * scores[index] / remaining_score
                for index in remaining_indexes
            }

        newly_capped = [
            index
            for index, weight in proposed.items()
            if weight > policy.max_single_weight
        ]
        if not newly_capped:
            for index, weight in proposed.items():
                weights[index] = weight
            break

        for index in newly_capped:
            weights[index] = policy.max_single_weight
            capped_symbols.append(candidates[index]["symbol"])
            remaining_exposure -= policy.max_single_weight
            remaining_indexes.remove(index)

    return weights


def _redistribute_under_cap_capacity(weights: list[float], target_total: float, max_single_weight: float) -> None:
    while True:
        residual = target_total - sum(weights)
        if residual <= 1e-12:
            return

        under_cap_indexes = [
            index
            for index, weight in enumerate(weights)
            if weight < max_single_weight - 1e-12
        ]
        if not under_cap_indexes:
            return

        share = residual / len(under_cap_indexes)
        added = 0.0
        for index in under_cap_indexes:
            capacity = max_single_weight - weights[index]
            increment = min(share, capacity)
            weights[index] += increment
            added += increment
        if added <= 1e-12:
            return


def _can_drop_position(position_count: int, exposure: float, policy: DynamicEquityPortfolioPolicy) -> bool:
    return position_count >= policy.min_positions and position_count * policy.max_single_weight >= exposure


def _cap_feasible(position_count: int, exposure: float, policy: DynamicEquityPortfolioPolicy) -> bool:
    return position_count * policy.max_single_weight >= exposure


def _equal_weights(position_count: int, exposure: float) -> list[float]:
    if position_count <= 0:
        return []
    weights = [round(exposure / position_count, 6) for _ in range(position_count)]
    residual = round(exposure - sum(weights), 6)
    weights[-1] = round(weights[-1] + residual, 6)
    return weights


def _round_weights(
    weights: list[float],
    exposure: float,
    candidates: list[dict[str, Any]],
    policy: DynamicEquityPortfolioPolicy,
) -> list[float]:
    rounded = [round(weight, 6) for weight in weights]
    target_total = exposure if _cap_feasible(len(candidates), exposure, policy) else sum(weights)
    difference = round(target_total - sum(rounded), 6)
    if not rounded or difference == 0.0:
        return rounded

    cap_is_feasible = _cap_feasible(len(candidates), exposure, policy)
    best_index = None
    sorted_indexes = sorted(
        enumerate(rounded),
        key=lambda item: candidates[item[0]]["adjusted_score"],
        reverse=True,
    )
    for index, weight in sorted_indexes:
        adjusted = round(weight + difference, 6)
        if adjusted < 0.0:
            continue
        if cap_is_feasible and adjusted > policy.max_single_weight:
            continue
        best_index = index
        break

    if best_index is None:
        best_index = max(range(len(rounded)), key=lambda index: rounded[index])
    rounded[best_index] = round(rounded[best_index] + difference, 6)
    return rounded


def _candidate_score_rows(
    candidates: list[dict[str, Any]],
    final_weights: dict[str, float],
) -> list[dict[str, Any]]:
    return [
        {
            "symbol": candidate["symbol"],
            "evidence_score": round(candidate["evidence_score"], 6),
            "volatility_penalty": round(candidate["volatility_penalty"], 6),
            "adjusted_score": round(candidate["adjusted_score"], 6),
            "stage_support_score": _round_or_none(candidate.get("stage_support_score")),
            "stage_penalty": _round_or_none(candidate.get("stage_penalty")),
            "adjusted_stage_score": _round_or_none(candidate.get("adjusted_stage_score")),
            "stage_warning_flags": list(candidate.get("stage_warning_flags") or []),
            "final_weight": final_weights.get(candidate["symbol"], 0.0),
            "selection_source": candidate["selection_source"],
        }
        for candidate in candidates
    ]


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


def _score_sort_key(candidate: dict[str, Any]) -> tuple[float, int, int]:
    llm_order = candidate["llm_order"] if candidate["llm_order"] is not None else 1_000_000
    return (-candidate["adjusted_score"], llm_order, candidate["original_order"])


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
