import importlib

import pytest


def load_strategy_module():
    module = importlib.import_module(
        "lumibot.example_strategies.ai_trading_team_mock_growth_inflation_quadrant"
    )
    return module, module.AITradingTeamMockGrowthInflationQuadrantStrategy


def test_regimes_are_exact_growth_inflation_quadrants():
    module, _strategy_class = load_strategy_module()

    assert module.REGIMES == (
        "growth_up_inflation_down",
        "growth_up_inflation_up",
        "growth_down_inflation_up",
        "growth_down_inflation_down",
    )


def test_strategy_initially_subclasses_growth_execution_test_strategy():
    _module, strategy_class = load_strategy_module()
    from lumibot.example_strategies.ai_trading_team_growth_execution_test import (
        AITradingTeamGrowthExecutionTestStrategy,
    )

    assert issubclass(strategy_class, AITradingTeamGrowthExecutionTestStrategy)


def test_strategy_parameters_copy_growth_execution_defaults():
    _module, strategy_class = load_strategy_module()
    from lumibot.example_strategies.ai_trading_team_growth_execution_test import (
        AITradingTeamGrowthExecutionTestStrategy,
    )

    assert strategy_class.parameters == dict(AITradingTeamGrowthExecutionTestStrategy.parameters)
    assert strategy_class.parameters is not AITradingTeamGrowthExecutionTestStrategy.parameters
    assert "universe" in strategy_class.parameters


def test_basket_universes_have_at_least_five_semantically_valid_symbols():
    module, _strategy_class = load_strategy_module()

    assert module.BASKET_UNIVERSES == {
        "equity": ["SPY", "QQQ", "IWM", "EEM", "FXI"],
        "commodity": ["GLD", "SLV", "DBC", "PDBC", "GSG"],
        "tips": ["TIP", "SCHP", "VTIP", "STIP", "LTPZ"],
        "nominal_bond": ["SHY", "IEF", "TLT", "GOVT", "VGIT"],
    }
    for symbols in module.BASKET_UNIVERSES.values():
        assert len(symbols) >= 5
        assert len(symbols) == len(set(symbols))


def test_mock_weight_mapping_uses_50_25_25_0_and_sums_to_one():
    module, _strategy_class = load_strategy_module()

    assert set(module.MOCK_WEIGHT_BY_REGIME) == set(module.REGIMES)
    for regime, weights in module.MOCK_WEIGHT_BY_REGIME.items():
        assert set(weights) == set(module.BASKET_UNIVERSES)
        assert sorted(weights.values()) == [0.0, 0.25, 0.25, 0.5], regime
        assert sum(weights.values()) == pytest.approx(1.0)


def test_mock_regime_classifier_seeded_random_is_reproducible():
    module, _strategy_class = load_strategy_module()

    first = module.mock_macro_regime_classifier(date="2024-09-05", seed=42, mode="seeded_random")
    second = module.mock_macro_regime_classifier(date="2024-09-05", seed=42, mode="seeded_random")
    different_seed = module.mock_macro_regime_classifier(date="2024-09-05", seed=43, mode="seeded_random")

    assert first == second
    assert first["mock"] is True
    assert first["mode"] == "seeded_random"
    assert first["seed"] == 42
    assert first["date"] == "2024-09-05"
    assert first["regime"] in module.REGIMES
    assert first["basket_weights"] == module.MOCK_WEIGHT_BY_REGIME[first["regime"]]
    assert first["reason_brief"].startswith("Mock classifier")
    assert different_seed["regime"] in module.REGIMES


def test_mock_regime_classifier_cycle_mode_walks_quadrants_by_date():
    module, _strategy_class = load_strategy_module()

    seen = [
        module.mock_macro_regime_classifier(date=f"2024-09-0{day}", seed=7, mode="cycle")["regime"]
        for day in range(2, 6)
    ]

    assert len(set(seen)) == 4
    assert all(regime in module.REGIMES for regime in seen)


def test_mock_regime_classifier_reports_previous_regime_and_change_flag():
    module, _strategy_class = load_strategy_module()

    result = module.mock_macro_regime_classifier(
        date="2024-09-05",
        seed=42,
        mode="seeded_random",
        previous_regime="growth_up_inflation_down",
    )

    assert result["previous_regime"] == "growth_up_inflation_down"
    assert result["regime_changed"] == (result["regime"] != "growth_up_inflation_down")
