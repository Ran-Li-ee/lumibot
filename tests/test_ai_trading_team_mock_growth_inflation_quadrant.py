import importlib
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from lumibot.components.agents.manager import AgentManager
from lumibot.components.agents.schemas import ToolDefinition


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


def test_make_macro_regime_classifier_tool_returns_definition_that_binds_stateful_tool():
    module, _strategy_class = load_strategy_module()
    strategy = SimpleNamespace(
        _last_mock_regime=None,
        _mock_regime_mode="cycle",
        _mock_regime_seed=7,
        get_datetime=lambda: datetime(2024, 9, 5, 9, 30),
    )

    tool_definition = module.make_macro_regime_classifier_tool()
    tool = tool_definition.binder(strategy, None)

    assert isinstance(tool_definition, ToolDefinition)
    assert tool_definition.name == "macro_regime_classifier"
    assert "deterministic mock Growth / Inflation quadrant" in tool_definition.description
    assert tool_definition.metadata == {"kind": "mock_macro", "mock": True}
    assert tool.name == "macro_regime_classifier"
    assert "deterministic mock Growth / Inflation quadrant" in tool.description
    assert tool.metadata == {"kind": "mock_macro", "mock": True}
    assert tool.source == "local"

    first = tool.function()
    second = tool.function(date="2024-09-06")

    assert first["previous_regime"] is None
    assert second["previous_regime"] == first["regime"]
    assert strategy._last_mock_regime == second["regime"]


def test_agent_manager_create_registers_macro_regime_classifier_tool_definition_by_name():
    module, _strategy_class = load_strategy_module()
    strategy = SimpleNamespace(
        parameters={},
        _last_mock_regime=None,
        _mock_regime_mode="cycle",
        _mock_regime_seed=7,
        get_datetime=lambda: datetime(2024, 9, 5, 9, 30),
    )
    manager = AgentManager(strategy)
    tool_definition = module.make_macro_regime_classifier_tool()

    handle = manager.create(
        name="macro_agent",
        system_prompt="Use local macro regime classifier.",
        model="test-model",
        tools=[tool_definition],
        include_builtin_tools=False,
    )
    bound_tools = handle._ensure_bound_tools()

    assert [tool.name for tool in bound_tools] == ["macro_regime_classifier"]
    assert bound_tools[0].function()["tool"] == "macro_regime_classifier"


def test_parse_execution_plan_accepts_multiple_market_buy_orders():
    module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "decision": {"type": "rebalance", "reason_brief": "mock portfolio target"},
            "execution_plan": {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [
                    {
                        "sequence": 2,
                        "action": "submit_order",
                        "symbol": "TIP",
                        "side": "buy",
                        "quantity_mode": "shares",
                        "quantity": 100,
                        "asset_type": "stock",
                        "order_type": "market",
                        "time_in_force": "day",
                    },
                    {
                        "sequence": 1,
                        "action": "submit_order",
                        "symbol": "QQQ",
                        "side": "buy",
                        "quantity_mode": "shares",
                        "quantity": 50,
                        "asset_type": "stock",
                        "order_type": "market",
                        "time_in_force": "day",
                    },
                ],
            },
        }
    )

    plan = module.parse_execution_plan_from_portfolio_summary(raw_summary)

    assert plan["intent"] == "rebalance"
    assert [order["symbol"] for order in plan["orders"]] == ["QQQ", "TIP"]
    assert [order["quantity"] for order in plan["orders"]] == [50.0, 100.0]


@pytest.mark.parametrize(
    "bad_order, message",
    [
        (
            {
                "sequence": 1,
                "symbol": "QQQ",
                "side": "buy",
                "quantity_mode": "full_position",
                "quantity": 1,
                "order_type": "market",
            },
            "semantic quantity_mode",
        ),
        (
            {
                "sequence": 1,
                "symbol": "QQQ",
                "side": "buy",
                "quantity_mode": "shares",
                "quantity": 1.5,
                "order_type": "market",
            },
            "whole-share integer",
        ),
        (
            {
                "sequence": 1,
                "symbol": "QQQ",
                "side": "buy",
                "quantity_mode": "shares",
                "quantity": 1,
                "order_type": "limit",
            },
            "market-only",
        ),
        (
            {
                "sequence": 1,
                "symbol": "QQQ",
                "side": "buy",
                "quantity_mode": "shares",
                "quantity": 1,
                "order_type": "market",
                "limit_price": 10,
            },
            "must not include limit_price",
        ),
    ],
)
def test_parse_execution_plan_rejects_non_strict_orders(bad_order, message):
    module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps({"execution_plan": {"schema_version": 1, "intent": "rebalance", "orders": [bad_order]}})

    with pytest.raises(ValueError, match=message):
        module.parse_execution_plan_from_portfolio_summary(raw_summary)


def test_parse_execution_plan_rejects_sell_after_buy_sequence():
    module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "execution_plan": {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [
                    {
                        "sequence": 1,
                        "symbol": "QQQ",
                        "side": "buy",
                        "quantity_mode": "shares",
                        "quantity": 1,
                        "order_type": "market",
                    },
                    {
                        "sequence": 2,
                        "symbol": "SPY",
                        "side": "sell",
                        "quantity_mode": "shares",
                        "quantity": 1,
                        "order_type": "market",
                    },
                ],
            }
        }
    )

    with pytest.raises(ValueError, match="sell orders before buy orders"):
        module.parse_execution_plan_from_portfolio_summary(raw_summary)


def test_validate_plan_symbols_match_selected_basket_reports():
    module, _strategy_class = load_strategy_module()
    plan = {
        "schema_version": 1,
        "intent": "rebalance",
        "orders": [
            {
                "sequence": 1,
                "symbol": "QQQ",
                "side": "buy",
                "quantity": 10.0,
                "quantity_mode": "shares",
                "order_type": "market",
            },
            {
                "sequence": 2,
                "symbol": "TIP",
                "side": "buy",
                "quantity": 10.0,
                "quantity_mode": "shares",
                "order_type": "market",
            },
        ],
        "constraints": {"allow_negative_cash": False, "if_any_order_blocked": "stop_remaining_orders"},
    }
    basket_reports = [
        {"basket_id": "equity", "status": "active", "selected_symbol": "QQQ"},
        {"basket_id": "tips", "status": "active", "selected_symbol": "TIP"},
        {"basket_id": "commodity", "status": "inactive", "selected_symbol": None},
    ]

    module.validate_execution_plan_symbols(plan, basket_reports)


def test_validate_plan_symbols_rejects_unselected_buy_symbol():
    module, _strategy_class = load_strategy_module()
    plan = {
        "schema_version": 1,
        "intent": "rebalance",
        "orders": [
            {
                "sequence": 1,
                "symbol": "FXI",
                "side": "buy",
                "quantity": 10.0,
                "quantity_mode": "shares",
                "order_type": "market",
            },
        ],
        "constraints": {"allow_negative_cash": False, "if_any_order_blocked": "stop_remaining_orders"},
    }
    basket_reports = [{"basket_id": "equity", "status": "active", "selected_symbol": "QQQ"}]

    with pytest.raises(ValueError, match="not selected by any active basket"):
        module.validate_execution_plan_symbols(plan, basket_reports)
