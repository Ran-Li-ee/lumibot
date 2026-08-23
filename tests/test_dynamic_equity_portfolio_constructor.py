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
    assert result["selected_count"] == 4
    assert [row["symbol"] for row in result["target_portfolio"]][:3] == ["AAA", "BBB", "CCC"]
    assert all(
        row["target_weight"] <= DEFAULT_DYNAMIC_EQUITY_POLICY.max_single_weight + 1e-12
        for row in result["target_portfolio"]
    )
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
    policy = DynamicEquityPortfolioPolicy(max_single_weight=0.50)
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
        policy=policy,
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
        "selected_symbols": ["AAA", "BBB", "CCC", "DDD"],
    }
    market_summary = summary(
        [
            candidate("AAA", momentum=1, trend_quality=1, ranking_count=20),
            candidate("BBB", momentum=8, trend_quality=8, ranking_count=2),
            candidate("CCC", momentum=9, trend_quality=9, ranking_count=2),
            candidate("DDD", momentum=9, trend_quality=8, ranking_count=2),
        ]
    )

    result = construct_dynamic_equity_target_portfolio(
        equity_report,
        equity_universe=["AAA", "BBB", "CCC", "DDD"],
        market_summary=market_summary,
        policy=policy,
    )

    weights = weights_by_symbol(result)
    assert max(weights.values()) <= 0.30 + 1e-12
    assert sum(weights.values()) == pytest.approx(0.98)
    assert "AAA" in result["diagnostics"]["capped_symbols"]


def test_constructor_uses_available_cap_capacity_when_scored_selection_is_cap_infeasible():
    policy = DynamicEquityPortfolioPolicy(
        min_positions=3,
        max_positions=3,
        fallback_positions=3,
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
            candidate(
                "BBB",
                momentum=10,
                trend_quality=10,
                risk_adjusted_momentum=10,
                breakout_near_high=10,
                volume_confirmation=10,
                ranking_count=1,
            ),
            candidate(
                "CCC",
                momentum=10,
                trend_quality=10,
                risk_adjusted_momentum=10,
                breakout_near_high=10,
                volume_confirmation=10,
                ranking_count=1,
            ),
        ]
    )

    result = construct_dynamic_equity_target_portfolio(
        equity_report,
        equity_universe=["AAA", "BBB", "CCC"],
        market_summary=market_summary,
        policy=policy,
    )

    weights = [row["target_weight"] for row in result["target_portfolio"]]
    assert result["diagnostics"]["cap_feasible"] is False
    assert result["diagnostics"]["warnings"]
    assert all(weight <= 0.30 + 1e-12 for weight in weights)
    assert sum(weights) == pytest.approx(0.90)


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


def test_constructor_fallback_weights_reconcile_rounding_residual():
    policy = DynamicEquityPortfolioPolicy(
        min_positions=3,
        max_positions=10,
        fallback_positions=3,
        cash_buffer_weight=0.02,
        max_single_weight=0.50,
        min_single_weight=0.05,
    )
    equity_report = {
        "basket_id": "equity",
        "status": "active",
        "selected_symbols": ["AAA", "BBB", "CCC"],
    }

    result = construct_dynamic_equity_target_portfolio(
        equity_report,
        equity_universe=["AAA", "BBB", "CCC"],
        market_summary=None,
        policy=policy,
    )

    weights = [row["target_weight"] for row in result["target_portfolio"]]
    assert sum(weights) == pytest.approx(0.98)
    assert sum(weights) <= 0.98 + 1e-12


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
