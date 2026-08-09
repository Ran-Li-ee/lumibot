import importlib
import json
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

from lumibot.components.agents.manager import AgentManager
from lumibot.components.agents.schemas import ToolDefinition


class RecordingAgentManager:
    def __init__(self):
        self.created = []
        self._agents = {}
        self.summaries = {}
        self.tool_calls = {}

    def create(self, **kwargs):
        self.created.append(kwargs)
        agent = RecordingAgent(kwargs["name"], self.summaries, self.tool_calls)
        self._agents[kwargs["name"]] = agent
        return agent

    def __getitem__(self, name):
        return self._agents[name]


class RecordingAgent:
    def __init__(self, name, summaries=None, tool_calls=None):
        self.name = name
        self.summaries = summaries if summaries is not None else {}
        self.tool_calls = tool_calls if tool_calls is not None else {}
        self.calls = []

    def run(self, *, task_prompt, context):
        self.calls.append({"task_prompt": task_prompt, "context": context})
        summary = self.summaries.get(self.name, f"{self.name} summary")
        return SimpleNamespace(
            summary=summary,
            tool_calls=[
                SimpleNamespace(tool_name=tool_name)
                for tool_name in self.tool_calls.get(self.name, [])
            ],
        )


def make_strategy_with_agent_manager(strategy_class, agent_manager):
    strategy = object.__new__(strategy_class)
    strategy.agents = agent_manager
    strategy.parameters = dict(strategy_class.parameters)
    strategy.get_datetime = lambda: datetime(2024, 9, 5, 9, 30)
    strategy.get_cash = lambda: 100000.0
    strategy.get_portfolio_value = lambda: 100000.0
    strategy.get_last_price = lambda symbol: 100.0
    strategy.get_positions = lambda include_cash_positions=False: []
    return strategy


def make_position(symbol, quantity):
    return SimpleNamespace(
        symbol=symbol,
        quantity=quantity,
        asset=SimpleNamespace(symbol=symbol),
    )


def make_planner_strategy(*, positions, prices, cash=0.0, portfolio_value=100000.0):
    def get_positions(include_cash_positions=False):
        return list(positions)

    def get_last_price(symbol, quote=None, exchange=None):
        if not isinstance(symbol, str):
            symbol = getattr(symbol, "symbol", symbol)
        symbol = str(symbol).upper()
        if symbol not in prices:
            return None
        return prices[symbol]

    return SimpleNamespace(
        get_positions=get_positions,
        get_cash=lambda: cash,
        get_portfolio_value=lambda: portfolio_value,
        get_last_price=get_last_price,
    )


def created_tool_names(created_agent):
    return {getattr(tool, "name", "") for tool in created_agent.get("tools", [])}


def load_strategy_module():
    module = importlib.import_module(
        "lumibot.example_strategies.ai_trading_team_mock_growth_inflation_quadrant"
    )
    return module, module.AITradingTeamMockGrowthInflationQuadrantStrategy


def test_examples_benchmark_exposes_mock_quadrant_strategy():
    benchmark = importlib.import_module("scripts.run_ai_trading_team_examples_benchmark")

    assert "mock-growth-inflation-quadrant" in benchmark.STRATEGIES
    assert (
        benchmark.STRATEGIES["mock-growth-inflation-quadrant"].__name__
        == "AITradingTeamMockGrowthInflationQuadrantStrategy"
    )


def test_examples_benchmark_uses_model_specific_key_check(monkeypatch):
    benchmark = importlib.import_module("scripts.run_ai_trading_team_examples_benchmark")
    monkeypatch.setenv("AI_TRADING_TEAM_MODEL", "openai/gpt-5.6-luna")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert benchmark._missing_key_label("openai/gpt-5.6-luna") is None


def test_examples_benchmark_main_uses_model_specific_key_gate(monkeypatch):
    benchmark = importlib.import_module("scripts.run_ai_trading_team_examples_benchmark")
    monkeypatch.setenv("AI_TRADING_TEAM_MODEL", "openai/gpt-5.6-luna")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_ai_trading_team_examples_benchmark.py",
            "--env-file",
            "missing-ai-trading-team-env-file-for-test.env",
        ],
    )

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        benchmark.main()


def test_examples_benchmark_import_does_not_require_backtesting_stack(monkeypatch):
    sys.modules.pop("scripts.run_ai_trading_team_examples_benchmark", None)
    sys.modules.pop("scripts.run_ai_committee_provider_benchmark", None)
    strategy_modules = (
        "lumibot.example_strategies.ai_trading_team_bill_ackman_concentrated",
        "lumibot.example_strategies.ai_trading_team_bull_bear_large_cap_stocks",
        "lumibot.example_strategies.ai_trading_team_bull_bear_leveraged_etf",
        "lumibot.example_strategies.ai_trading_team_citadel_sector_pods",
        "lumibot.example_strategies.ai_trading_team_growth_execution_test",
        "lumibot.example_strategies.ai_trading_team_mock_growth_inflation_quadrant",
        "lumibot.example_strategies.ai_trading_team_ray_dalio_idea_meritocracy",
        "lumibot.example_strategies.ai_trading_team_warren_buffett_value",
    )
    for module_name in strategy_modules:
        sys.modules.pop(module_name, None)
    real_import = __import__

    def fail_optional_backtesting_import(name, *args, **kwargs):
        if name in {"lumibot.backtesting", "lumibot.entities"}:
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fail_optional_backtesting_import)

    benchmark = importlib.import_module("scripts.run_ai_trading_team_examples_benchmark")

    assert "mock-growth-inflation-quadrant" in benchmark.STRATEGIES
    assert all(module_name not in sys.modules for module_name in strategy_modules)


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


def test_strategy_parameters_are_mock_quadrant_defaults():
    _module, strategy_class = load_strategy_module()

    assert strategy_class.parameters == {
        "basket_universes": _module.BASKET_UNIVERSES,
        "mock_regime_mode": "seeded_random",
        "mock_regime_seed": 42,
    }
    assert strategy_class._execution_agent_base_system_prompt_mode == "execution_minimal"


def test_initialize_creates_seven_agent_mock_quadrant_workflow(monkeypatch):
    _module, strategy_class = load_strategy_module()
    monkeypatch.setenv("AI_TRADING_TEAM_MODEL", "test-model")
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()

    assert [agent["name"] for agent in agent_manager.created] == [
        "macro_allocation_agent",
        "equity_basket_agent",
        "commodity_basket_agent",
        "tips_basket_agent",
        "nominal_bond_basket_agent",
        "portfolio_decision_agent",
        "execution_agent",
    ]
    assert [agent["allow_trading"] for agent in agent_manager.created] == [
        False,
        False,
        False,
        False,
        False,
        False,
        True,
    ]
    assert strategy.sleeptime == "1D"
    assert strategy._mock_regime_mode == "seeded_random"
    assert strategy._mock_regime_seed == 42


def test_agents_receive_distinct_tool_surfaces():
    _module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()

    created = {agent["name"]: agent for agent in agent_manager.created}
    assert created_tool_names(created["macro_allocation_agent"]) == {"macro_regime_classifier"}
    for basket_agent in (
        "equity_basket_agent",
        "commodity_basket_agent",
        "tips_basket_agent",
        "nominal_bond_basket_agent",
    ):
        assert created[basket_agent]["include_builtin_tools"] is False
        assert created_tool_names(created[basket_agent]) == {
            "market_load_history_tables_summary",
            "market_last_price",
        }
    assert created_tool_names(created["portfolio_decision_agent"]) == {
        "target_portfolio_to_execution_plan",
    }
    assert created_tool_names(created["execution_agent"]) == {
        "account_positions",
        "account_portfolio",
        "market_last_price",
        "orders_open_orders",
        "orders_submit_order",
        "orders_confirm_order",
    }
    for non_execution_agent in agent_manager.created[:-1]:
        assert "orders_submit_order" not in created_tool_names(non_execution_agent)
        assert "orders_confirm_order" not in created_tool_names(non_execution_agent)


def test_prompt_boundaries_are_short_and_role_specific():
    _module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()

    serialized = json.dumps(agent_manager.created, default=str).lower()
    for forbidden_phrase in (
        "prefer doing nothing",
        "avoid overtrading",
        "duckdb",
        "test turnover",
        "limit order",
        "stop loss",
        "cash_buffer_pct",
    ):
        assert forbidden_phrase not in serialized
    assert "call the mock macro_regime_classifier" in serialized
    assert "stay inside the assigned basket" in serialized
    assert "do not redo macro or basket research" in serialized
    assert "call target_portfolio_to_execution_plan" in serialized
    assert "do not manually calculate share quantities" in serialized
    assert "planner tool owns all execution_plan calculations" in serialized
    assert "execute only the provided execution_plan" in serialized


def test_portfolio_decision_prompt_delegates_execution_plan_to_planner_tool():
    _module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()

    created = {agent["name"]: agent for agent in agent_manager.created}
    portfolio_prompt = created["portfolio_decision_agent"]["system_prompt"].lower()
    for required_phrase in (
        "merge the macro allocation report",
        "call target_portfolio_to_execution_plan",
        "do not manually calculate share quantities",
        "planner tool owns all execution_plan calculations",
        "execution_plan must be copied exactly from the planner tool result",
        "do not modify tool-generated quantities",
    ):
        assert required_phrase in portfolio_prompt
    for forbidden_phrase in (
        "before sizing any buy order",
        "call account_positions",
        "call account_portfolio",
        "call market_last_price",
    ):
        assert forbidden_phrase not in portfolio_prompt


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

    assert module.MOCK_WEIGHT_BY_REGIME == {
        "growth_up_inflation_down": {
            "equity": 0.50,
            "commodity": 0.25,
            "tips": 0.00,
            "nominal_bond": 0.25,
        },
        "growth_up_inflation_up": {
            "equity": 0.25,
            "commodity": 0.50,
            "tips": 0.25,
            "nominal_bond": 0.00,
        },
        "growth_down_inflation_up": {
            "equity": 0.00,
            "commodity": 0.25,
            "tips": 0.50,
            "nominal_bond": 0.25,
        },
        "growth_down_inflation_down": {
            "equity": 0.25,
            "commodity": 0.25,
            "tips": 0.00,
            "nominal_bond": 0.50,
        },
    }
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


def test_target_portfolio_to_execution_plan_tool_definition_binds_and_stores_result():
    _module, _strategy_class = load_strategy_module()
    planner_module = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[],
        cash=100000,
        portfolio_value=100000,
        prices={"SPY": 100},
    )

    tool_definition = planner_module.make_target_portfolio_to_execution_plan_tool()
    tool = tool_definition.binder(strategy, None)
    result = tool.function(
        date="2024-09-05",
        target_portfolio=[{"basket_id": "equity", "symbol": "SPY", "target_weight": 0.5}],
    )

    assert tool_definition.name == "target_portfolio_to_execution_plan"
    assert tool.name == "target_portfolio_to_execution_plan"
    assert tool.metadata == {"kind": "portfolio_transition_planner"}
    assert result["execution_plan"]["intent"] == "rebalance"
    assert strategy._last_target_portfolio_planner_result == result


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


def test_parse_execution_plan_defaults_missing_quantity_mode_to_shares_when_quantity_is_explicit():
    module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "execution_plan": {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [
                    {
                        "sequence": 1,
                        "action": "submit_order",
                        "symbol": "TIP",
                        "side": "buy",
                        "quantity": 229,
                        "asset_type": "stock",
                        "order_type": "market",
                        "time_in_force": "day",
                    }
                ],
            }
        }
    )

    plan = module.parse_execution_plan_from_portfolio_summary(raw_summary)

    assert plan["orders"][0]["quantity_mode"] == "shares"
    assert plan["orders"][0]["quantity"] == 229.0


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


def _json_summary(payload):
    return json.dumps(payload, separators=(",", ":"))


def test_on_trading_iteration_runs_macro_four_baskets_portfolio_then_execution():
    module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()

    macro_report = {
        "agent": "macro_allocation_agent",
        "regime": "growth_up_inflation_down",
        "regime_changed": True,
        "basket_weights": {"equity": 0.50, "commodity": 0.00, "tips": 0.25, "nominal_bond": 0.25},
        "mock": True,
        "reason_brief": "mock",
    }
    basket_reports = {
        "equity_basket_agent": {
            "basket_id": "equity",
            "target_weight": 0.50,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["equity"],
            "selected_symbol": "QQQ",
            "reason_brief": "selected",
        },
        "commodity_basket_agent": {
            "basket_id": "commodity",
            "target_weight": 0.00,
            "status": "inactive",
            "candidate_symbols": module.BASKET_UNIVERSES["commodity"],
            "selected_symbol": None,
            "reason_brief": "inactive",
        },
        "tips_basket_agent": {
            "basket_id": "tips",
            "target_weight": 0.25,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["tips"],
            "selected_symbol": "TIP",
            "reason_brief": "selected",
        },
        "nominal_bond_basket_agent": {
            "basket_id": "nominal_bond",
            "target_weight": 0.25,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["nominal_bond"],
            "selected_symbol": "IEF",
            "reason_brief": "selected",
        },
    }
    portfolio_summary = {
        "decision": {"type": "rebalance", "reason_brief": "mock target"},
        "target_portfolio": [
            {"basket_id": "equity", "symbol": "QQQ", "target_weight": 0.50},
            {"basket_id": "tips", "symbol": "TIP", "target_weight": 0.25},
            {"basket_id": "nominal_bond", "symbol": "IEF", "target_weight": 0.25},
        ],
        "execution_plan": {
            "schema_version": 1,
            "intent": "rebalance",
            "orders": [
                {
                    "sequence": 1,
                    "symbol": "QQQ",
                    "side": "buy",
                    "quantity_mode": "shares",
                    "quantity": 100,
                    "order_type": "market",
                },
                {
                    "sequence": 2,
                    "symbol": "TIP",
                    "side": "buy",
                    "quantity_mode": "shares",
                    "quantity": 50,
                    "order_type": "market",
                },
                {
                    "sequence": 3,
                    "symbol": "IEF",
                    "side": "buy",
                    "quantity_mode": "shares",
                    "quantity": 50,
                    "order_type": "market",
                },
            ],
        },
    }

    agent_manager.summaries["macro_allocation_agent"] = _json_summary(macro_report)
    for agent_name, report in basket_reports.items():
        agent_manager.summaries[agent_name] = _json_summary(report)
    agent_manager.summaries["portfolio_decision_agent"] = _json_summary(portfolio_summary)
    agent_manager.tool_calls["portfolio_decision_agent"] = [
        "account_positions",
        "account_portfolio",
        "market_last_price",
    ]

    strategy.on_trading_iteration()

    assert [agent_name for agent_name, agent in agent_manager._agents.items() if agent.calls] == [
        "macro_allocation_agent",
        "equity_basket_agent",
        "commodity_basket_agent",
        "tips_basket_agent",
        "nominal_bond_basket_agent",
        "portfolio_decision_agent",
        "execution_agent",
    ]
    macro_context = agent_manager["macro_allocation_agent"].calls[0]["context"]
    assert macro_context["date"] == "2024-09-05"
    assert macro_context["mock_regime_mode"] == "seeded_random"
    assert macro_context["mock_regime_seed"] == 42

    commodity_context = agent_manager["commodity_basket_agent"].calls[0]["context"]
    assert commodity_context["basket_id"] == "commodity"
    assert commodity_context["basket_symbols"] == module.BASKET_UNIVERSES["commodity"]
    assert commodity_context["target_weight"] == 0.0
    assert commodity_context["macro_allocation_report"] == macro_report

    portfolio_context = agent_manager["portfolio_decision_agent"].calls[0]["context"]
    assert portfolio_context["macro_allocation_report"] == macro_report
    assert portfolio_context["equity_basket_report"] == basket_reports["equity_basket_agent"]
    assert portfolio_context["commodity_basket_report"] == basket_reports["commodity_basket_agent"]
    assert portfolio_context["tips_basket_report"] == basket_reports["tips_basket_agent"]
    assert portfolio_context["nominal_bond_basket_report"] == basket_reports["nominal_bond_basket_agent"]

    execution_context = agent_manager["execution_agent"].calls[0]["context"]
    assert set(execution_context) == {"date", "execution_plan"}
    assert [order["symbol"] for order in execution_context["execution_plan"]["orders"]] == ["QQQ", "TIP", "IEF"]
    assert "macro_allocation_report" not in execution_context
    assert "equity_basket_report" not in execution_context


def test_hold_plan_skips_execution_agent():
    _module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()

    agent_manager.summaries["macro_allocation_agent"] = _json_summary(
        {"basket_weights": {"equity": 0.0, "commodity": 0.0, "tips": 0.0, "nominal_bond": 0.0}}
    )
    for agent_name, basket_id in {
        "equity_basket_agent": "equity",
        "commodity_basket_agent": "commodity",
        "tips_basket_agent": "tips",
        "nominal_bond_basket_agent": "nominal_bond",
    }.items():
        agent_manager.summaries[agent_name] = _json_summary(
            {"basket_id": basket_id, "target_weight": 0.0, "status": "inactive", "selected_symbol": None}
        )
    agent_manager.summaries["portfolio_decision_agent"] = _json_summary(
        {"execution_plan": {"schema_version": 1, "intent": "hold", "orders": []}}
    )

    strategy.on_trading_iteration()

    assert agent_manager["portfolio_decision_agent"].calls
    assert agent_manager["execution_agent"].calls == []


def _seed_active_equity_workflow(agent_manager, module, execution_plan):
    macro_report = {
        "basket_weights": {"equity": 1.0, "commodity": 0.0, "tips": 0.0, "nominal_bond": 0.0},
    }
    basket_reports = {
        "equity_basket_agent": {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["equity"],
            "selected_symbol": "QQQ",
        },
        "commodity_basket_agent": {
            "basket_id": "commodity",
            "target_weight": 0.0,
            "status": "inactive",
            "candidate_symbols": module.BASKET_UNIVERSES["commodity"],
            "selected_symbol": None,
        },
        "tips_basket_agent": {
            "basket_id": "tips",
            "target_weight": 0.0,
            "status": "inactive",
            "candidate_symbols": module.BASKET_UNIVERSES["tips"],
            "selected_symbol": None,
        },
        "nominal_bond_basket_agent": {
            "basket_id": "nominal_bond",
            "target_weight": 0.0,
            "status": "inactive",
            "candidate_symbols": module.BASKET_UNIVERSES["nominal_bond"],
            "selected_symbol": None,
        },
    }

    agent_manager.summaries["macro_allocation_agent"] = _json_summary(macro_report)
    for agent_name, report in basket_reports.items():
        agent_manager.summaries[agent_name] = _json_summary(report)
    agent_manager.summaries["portfolio_decision_agent"] = _json_summary(
        {
            "decision": {"type": "rebalance", "reason_brief": "safety regression"},
            "execution_plan": execution_plan,
        }
    )
    agent_manager.tool_calls["portfolio_decision_agent"] = [
        "account_positions",
        "account_portfolio",
        "market_last_price",
    ]


def test_oversized_buy_plan_blocks_before_execution_agent():
    module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.get_cash = lambda: 100.0
    strategy.get_portfolio_value = lambda: 100.0
    strategy.get_last_price = lambda symbol: 100.0
    strategy.initialize()
    _seed_active_equity_workflow(
        agent_manager,
        module,
        {
            "schema_version": 1,
            "intent": "rebalance",
            "orders": [
                {
                    "sequence": 1,
                    "symbol": "QQQ",
                    "side": "buy",
                    "quantity_mode": "shares",
                    "quantity": 1000,
                    "order_type": "market",
                },
            ],
        },
    )

    strategy.on_trading_iteration()

    assert "NEGATIVE_CASH_NOT_ALLOWED" in strategy._last_execution_plan_error
    assert agent_manager["execution_agent"].calls == []


def test_sell_without_position_blocks_before_execution_agent():
    module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.get_positions = lambda include_cash_positions=False: []
    strategy.initialize()
    _seed_active_equity_workflow(
        agent_manager,
        module,
        {
            "schema_version": 1,
            "intent": "rebalance",
            "orders": [
                {
                    "sequence": 1,
                    "symbol": "QQQ",
                    "side": "sell",
                    "quantity_mode": "shares",
                    "quantity": 10,
                    "order_type": "market",
                },
            ],
        },
    )

    strategy.on_trading_iteration()

    assert "DECISION_SELL_POSITION_REQUIRED" in strategy._last_execution_plan_error
    assert agent_manager["execution_agent"].calls == []


def test_target_portfolio_to_execution_plan_deploys_all_cash_to_targets():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[],
        cash=100000,
        portfolio_value=100000,
        prices={"SPY": 100, "GLD": 100, "VGIT": 50},
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-05",
        target_portfolio=[
            {"basket_id": "equity", "symbol": "SPY", "target_weight": 0.50},
            {"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.25},
            {"basket_id": "nominal_bond", "symbol": "VGIT", "target_weight": 0.25},
        ],
    )

    assert result["execution_plan"] == {
        "schema_version": 1,
        "intent": "rebalance",
        "orders": [
            {
                "sequence": 1,
                "action": "submit_order",
                "symbol": "GLD",
                "side": "buy",
                "quantity_mode": "shares",
                "quantity": 250,
                "asset_type": "stock",
                "order_type": "market",
                "time_in_force": "day",
            },
            {
                "sequence": 2,
                "action": "submit_order",
                "symbol": "SPY",
                "side": "buy",
                "quantity_mode": "shares",
                "quantity": 500,
                "asset_type": "stock",
                "order_type": "market",
                "time_in_force": "day",
            },
            {
                "sequence": 3,
                "action": "submit_order",
                "symbol": "VGIT",
                "side": "buy",
                "quantity_mode": "shares",
                "quantity": 500,
                "asset_type": "stock",
                "order_type": "market",
                "time_in_force": "day",
            },
        ],
    }
    assert result["cash_projection"]["cash_after_estimate"] == pytest.approx(0)
    assert {row["reason_code"] for row in result["current_vs_target"]} == {"buy_new_target"}


def test_target_portfolio_to_execution_plan_handles_full_rebalance_regression_case():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[
            make_position("SPY", 500),
            make_position("GLD", 250),
            make_position("VGIT", 500),
        ],
        cash=0,
        portfolio_value=100000,
        prices={"SPY": 100, "GLD": 100, "VGIT": 50, "TIP": 100},
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-06",
        target_portfolio=[
            {"basket_id": "equity", "symbol": "SPY", "target_weight": 0.25},
            {"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.50},
            {"basket_id": "tips", "symbol": "TIP", "target_weight": 0.25},
        ],
    )

    orders = result["execution_plan"]["orders"]
    assert [(order["side"], order["symbol"], order["quantity"]) for order in orders] == [
        ("sell", "VGIT", 500),
        ("sell", "SPY", 250),
        ("buy", "TIP", 250),
        ("buy", "GLD", 250),
    ]
    assert result["cash_projection"]["cash_after_estimate"] == pytest.approx(0)
    diagnostics = {row["symbol"]: row for row in result["current_vs_target"]}
    assert diagnostics["VGIT"]["reason_code"] == "exit_removed_symbol"
    assert diagnostics["SPY"]["reason_code"] == "reduce_overweight"
    assert diagnostics["GLD"]["reason_code"] == "increase_underweight"
    assert diagnostics["TIP"]["reason_code"] == "buy_new_target"


def test_target_portfolio_to_execution_plan_handles_basket_internal_symbol_switch():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[
            make_position("SPY", 500),
            make_position("GLD", 250),
            make_position("VGIT", 500),
        ],
        cash=0,
        portfolio_value=100000,
        prices={"SPY": 100, "QQQ": 200, "GLD": 100, "VGIT": 50},
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-06",
        target_portfolio=[
            {"basket_id": "equity", "symbol": "QQQ", "target_weight": 0.50},
            {"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.25},
            {"basket_id": "nominal_bond", "symbol": "VGIT", "target_weight": 0.25},
        ],
    )

    assert [
        (order["side"], order["symbol"], order["quantity"])
        for order in result["execution_plan"]["orders"]
    ] == [
        ("sell", "SPY", 500),
        ("buy", "QQQ", 250),
    ]


def test_target_portfolio_to_execution_plan_rebalances_price_drift():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[
            make_position("SPY", 570),
            make_position("GLD", 280),
            make_position("VGIT", 300),
        ],
        cash=0,
        portfolio_value=100000,
        prices={"SPY": 100, "GLD": 100, "VGIT": 50},
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-06",
        target_portfolio=[
            {"basket_id": "equity", "symbol": "SPY", "target_weight": 0.50},
            {"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.25},
            {"basket_id": "nominal_bond", "symbol": "VGIT", "target_weight": 0.25},
        ],
    )

    assert [
        (order["side"], order["symbol"], order["quantity"])
        for order in result["execution_plan"]["orders"]
    ] == [
        ("sell", "GLD", 30),
        ("sell", "SPY", 70),
        ("buy", "VGIT", 200),
    ]


def test_target_portfolio_to_execution_plan_holds_when_whole_share_rounding_produces_no_orders():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[
            make_position("SPY", 500),
            make_position("GLD", 250),
            make_position("VGIT", 499),
        ],
        cash=0,
        portfolio_value=99950,
        prices={"SPY": 100, "GLD": 100, "VGIT": 50},
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-06",
        target_portfolio=[
            {"basket_id": "equity", "symbol": "SPY", "target_weight": 0.50},
            {"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.25},
            {"basket_id": "nominal_bond", "symbol": "VGIT", "target_weight": 0.25},
        ],
    )

    assert result["execution_plan"] == {"schema_version": 1, "intent": "hold", "orders": []}
    warnings = result["warnings"]
    assert any("SPY" in warning and "smaller than one share" in warning for warning in warnings)
    assert any("GLD" in warning and "smaller than one share" in warning for warning in warnings)
    assert any(
        "VGIT" in warning
        and ("insufficient to buy one share" in warning or "smaller than one share" in warning)
        for warning in warnings
    )
    diagnostics = {row["symbol"]: row for row in result["current_vs_target"]}
    assert diagnostics["VGIT"]["reason_code"] == "rounding_no_buy"
    assert diagnostics["VGIT"]["planned_side"] is None
    assert diagnostics["VGIT"]["planned_quantity"] == 0


def test_target_portfolio_to_execution_plan_warns_instead_of_selling_zero_shares_for_subshare_exit():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[make_position("ABC", 0.5)],
        cash=0,
        portfolio_value=50,
        prices={"ABC": 100},
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-06",
        target_portfolio=[],
    )

    assert result["execution_plan"] == {"schema_version": 1, "intent": "hold", "orders": []}
    assert any("ABC" in warning and "smaller than one share" in warning for warning in result["warnings"])
    diagnostics = {row["symbol"]: row for row in result["current_vs_target"]}
    assert diagnostics["ABC"]["reason_code"] == "rounding_no_sell"
    assert diagnostics["ABC"]["planned_quantity"] == 0


def test_target_portfolio_to_execution_plan_rejects_negative_cash():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[],
        cash=-1,
        portfolio_value=100000,
        prices={},
    )

    with pytest.raises(ValueError, match="cash must be non-negative"):
        planner.target_portfolio_to_execution_plan(
            strategy,
            date="2024-09-06",
            target_portfolio=[],
        )


def test_target_portfolio_to_execution_plan_rejects_short_positions():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[make_position("ABC", -2)],
        cash=1000,
        portfolio_value=100000,
        prices={"ABC": 100},
    )

    with pytest.raises(ValueError, match="negative position quantity is not supported"):
        planner.target_portfolio_to_execution_plan(
            strategy,
            date="2024-09-06",
            target_portfolio=[],
        )


def test_target_portfolio_to_execution_plan_warns_about_fractional_exit_residual():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[make_position("ABC", 1.5)],
        cash=0,
        portfolio_value=150,
        prices={"ABC": 100},
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-06",
        target_portfolio=[],
    )

    assert [(order["side"], order["symbol"], order["quantity"]) for order in result["execution_plan"]["orders"]] == [
        ("sell", "ABC", 1)
    ]
    assert any("ABC" in warning and "fractional residual" in warning for warning in result["warnings"])


def test_target_portfolio_to_execution_plan_marks_no_buy_when_cash_is_insufficient():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[],
        cash=25,
        portfolio_value=100000,
        prices={"ABC": 100},
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-06",
        target_portfolio=[{"symbol": "ABC", "target_weight": 0.01}],
    )

    assert result["execution_plan"] == {"schema_version": 1, "intent": "hold", "orders": []}
    assert any("ABC" in warning and "insufficient to buy one share" in warning for warning in result["warnings"])
    diagnostics = {row["symbol"]: row for row in result["current_vs_target"]}
    assert diagnostics["ABC"]["reason_code"] == "insufficient_cash_no_buy"
    assert diagnostics["ABC"]["planned_side"] is None
    assert diagnostics["ABC"]["planned_quantity"] == 0


def test_target_portfolio_to_execution_plan_combines_duplicate_targets_and_rejects_overweight_total():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[],
        cash=100000,
        portfolio_value=100000,
        prices={"SPY": 100, "GLD": 100},
    )

    combined = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-06",
        target_portfolio=[
            {"basket_id": "equity", "symbol": "SPY", "target_weight": 0.25},
            {"basket_id": "equity", "symbol": "SPY", "target_weight": 0.25},
            {"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.25},
        ],
    )
    assert {item["symbol"]: item["target_weight"] for item in combined["target_portfolio"]} == {
        "GLD": 0.25,
        "SPY": 0.50,
    }

    with pytest.raises(ValueError, match="target weights must not exceed 1.0"):
        planner.target_portfolio_to_execution_plan(
            strategy,
            date="2024-09-06",
            target_portfolio=[
                {"symbol": "SPY", "target_weight": 0.75},
                {"symbol": "GLD", "target_weight": 0.50},
            ],
        )


def test_target_portfolio_to_execution_plan_rejects_missing_price():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[],
        cash=100000,
        portfolio_value=100000,
        prices={"SPY": 100},
    )

    with pytest.raises(ValueError, match="missing last price for GLD"):
        planner.target_portfolio_to_execution_plan(
            strategy,
            date="2024-09-06",
            target_portfolio=[
                {"symbol": "SPY", "target_weight": 0.50},
                {"symbol": "GLD", "target_weight": 0.25},
            ],
        )
