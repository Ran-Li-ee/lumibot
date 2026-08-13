import importlib
import json
import sys
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from lumibot.components.agents.manager import AgentHandle, AgentManager
from lumibot.components.agents.schemas import AgentRunResult, AgentTraceEvent, ToolDefinition


class RecordingAgentManager:
    def __init__(self):
        self.created = []
        self._agents = {}
        self.summaries = {}
        self.tool_calls = {}
        self.planner_results = {}
        self.strategy = None

    def create(self, **kwargs):
        self.created.append(kwargs)
        agent = RecordingAgent(kwargs["name"], self)
        self._agents[kwargs["name"]] = agent
        return agent

    def __getitem__(self, name):
        return self._agents[name]


class RecordingAgent:
    def __init__(self, name, agent_manager):
        self.name = name
        self.agent_manager = agent_manager
        self.calls = []

    def run(self, *, task_prompt, context):
        self.calls.append({"task_prompt": task_prompt, "context": context})
        summary = self.agent_manager.summaries.get(self.name, f"{self.name} summary")
        if self.name in self.agent_manager.planner_results and self.agent_manager.strategy is not None:
            self.agent_manager.strategy._last_target_portfolio_planner_result = self.agent_manager.planner_results[
                self.name
            ]
        return SimpleNamespace(
            summary=summary,
            tool_calls=[
                SimpleNamespace(tool_name=tool_name)
                for tool_name in self.agent_manager.tool_calls.get(self.name, [])
            ],
        )


def make_strategy_with_agent_manager(strategy_class, agent_manager):
    strategy = object.__new__(strategy_class)
    strategy.agents = agent_manager
    agent_manager.strategy = strategy
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


def make_planner_strategy(
    *,
    positions,
    prices,
    cash=0.0,
    portfolio_value=100000.0,
    historical_closes=None,
):
    historical_closes = historical_closes or {}

    def normalize_symbol(symbol):
        if not isinstance(symbol, str):
            symbol = getattr(symbol, "symbol", symbol)
        return str(symbol).upper()

    def get_positions(include_cash_positions=False):
        return list(positions)

    def get_last_price(symbol, quote=None, exchange=None):
        symbol = normalize_symbol(symbol)
        if symbol not in prices:
            return None
        return prices[symbol]

    def get_historical_prices(symbol, length, timestep="day", **kwargs):
        symbol = normalize_symbol(symbol)
        if symbol not in historical_closes:
            return None
        rows = historical_closes[symbol]
        if not rows:
            return None
        if isinstance(rows, dict):
            rows = [rows]
        frame = pd.DataFrame(rows)
        if "date" in frame.columns:
            frame.index = pd.to_datetime(frame["date"])
        return SimpleNamespace(pandas_df=frame)

    return SimpleNamespace(
        get_positions=get_positions,
        get_cash=lambda: cash,
        get_portfolio_value=lambda: portfolio_value,
        get_last_price=get_last_price,
        get_historical_prices=get_historical_prices,
    )


def created_tool_names(created_agent):
    return {getattr(tool, "name", "") for tool in created_agent.get("tools", [])}


def expected_commodity_universe():
    return [
        "GLD",
        "IAU",
        "SLV",
        "CPER",
        "WEAT",
        "CORN",
        "SOYB",
        "CANE",
        "PPLT",
        "PALL",
        "DBB",
        "USO",
        "BNO",
        "UNG",
        "UGA",
        "DBE",
        "DBO",
        "DBA",
        "PDBA",
        "TAGS",
        "TILL",
        "DBC",
        "PDBC",
        "BCI",
        "GSG",
        "COMT",
        "FTGC",
        "CMDY",
    ]


def expected_tips_universe():
    return [
        "VTIP",
        "STIP",
        "SCHP",
        "TIP",
        "SPIP",
        "LTPZ",
        "TIPS",
    ]


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
        "nominal_bond_basket_agent",
    ):
        assert created[basket_agent]["include_builtin_tools"] is False
        assert created_tool_names(created[basket_agent]) == {
            "market_load_history_tables_summary",
            "market_last_price",
        }
    for news_enabled_basket_agent in (
        "commodity_basket_agent",
        "tips_basket_agent",
    ):
        assert created[news_enabled_basket_agent]["include_builtin_tools"] is False
        assert created_tool_names(created[news_enabled_basket_agent]) == {
            "market_load_history_tables_summary",
            "market_last_price",
            "alpaca_news",
        }
    assert created_tool_names(created["portfolio_decision_agent"]) == {
        "target_portfolio_to_execution_plan",
    }
    assert created_tool_names(created["execution_agent"]) == {"execution_plan_execute"}
    for non_execution_agent in agent_manager.created[:-1]:
        assert "orders_submit_order" not in created_tool_names(non_execution_agent)
        assert "orders_confirm_order" not in created_tool_names(non_execution_agent)
        assert "orders_submit_and_confirm_order" not in created_tool_names(non_execution_agent)
        assert "orders_execute_order" not in created_tool_names(non_execution_agent)
        assert "execution_plan_execute" not in created_tool_names(non_execution_agent)


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
        "first inspect portfolio, positions, open orders, and latest prices",
        "manually call account_portfolio",
        "then confirm it with orders_confirm_order",
        "then call orders_confirm_order",
        "call orders_submit_order with that same order",
        "call orders_preflight_check",
        "orders_preflight_check",
        "orders_submit_and_confirm_order",
        "orders_execute_order",
        "for each order in ascending sequence order",
        "exact order fields",
        "continue only when can_continue=true",
        "preflight returns",
        "combined tool returns",
        "submit-and-confirm",
    ):
        assert forbidden_phrase not in serialized
    assert "call the mock macro_regime_classifier" in serialized
    assert "stay inside the assigned basket" in serialized
    assert "do not redo macro or basket research" in serialized
    assert "call target_portfolio_to_execution_plan" in serialized
    assert "do not manually calculate share quantities" in serialized
    assert "planner tool owns all execution_plan calculations" in serialized
    assert "planner tool owns daily backtest buy sizing" in serialized
    execution_section = serialized.split("execution role:", 1)[-1]
    assert "2% buy sizing buffer" not in execution_section
    assert "execute only provided execution_plan" in serialized
    assert "call execution_plan_execute exactly once with the complete execution_plan" in serialized
    assert "do not manually execute individual orders" in serialized
    assert "do not call lower-level order, account, open-order, or price tools" in serialized
    assert "do not research, change fields, reorder orders, split orders, or repair the plan" in serialized


def test_commodity_basket_prompt_is_rank_first_and_category_neutral():
    _module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()

    created = {agent["name"]: agent for agent in agent_manager.created}
    prompt = created["commodity_basket_agent"]["system_prompt"].lower()
    for required_phrase in (
        "computed ranking evidence",
        "primary selection evidence",
        "clearly stronger",
        "without requiring news",
        "only when",
        "ranking evidence is close",
        "do not prefer broad",
        "ticker-name intuition",
    ):
        assert required_phrase in prompt
    for forbidden_phrase in (
        "default to diversified",
        "prefer broad commodity etf",
        "prefer diversified commodity",
        "broad commodity etfs are safer",
        "gold is a default",
        "avoid energy",
        "single commodities are too risky",
        "choose pdbc when uncertain",
        "choose dbc when uncertain",
    ):
        assert forbidden_phrase not in prompt


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


def test_basket_universes_have_expected_symbols():
    module, _strategy_class = load_strategy_module()

    assert module.BASKET_UNIVERSES == {
        "equity": ["SPY", "QQQ", "IWM", "EEM", "FXI"],
        "commodity": expected_commodity_universe(),
        "tips": expected_tips_universe(),
        "nominal_bond": ["SHY", "IEF", "TLT", "GOVT", "VGIT"],
    }
    assert len(module.BASKET_UNIVERSES["commodity"]) == 28
    assert len(module.BASKET_UNIVERSES["tips"]) == 7
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
    assert tool.metadata == {"kind": "portfolio_transition_planner", "replay_on_cache": True}
    assert result["execution_plan"]["intent"] == "rebalance"
    assert strategy._last_target_portfolio_planner_result == result


def test_target_portfolio_to_execution_plan_tool_description_explains_sizing_policy():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")

    tool_definition = planner.make_target_portfolio_to_execution_plan_tool()

    assert "previous completed daily close" in tool_definition.description
    assert "default 2% buy sizing buffer" in tool_definition.description
    assert "not a hard execution price cap" in tool_definition.description
    assert "Do not manually edit the execution_plan" in tool_definition.description


def test_target_portfolio_to_execution_plan_replays_cache_side_effect():
    planner_module = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[],
        cash=100000,
        portfolio_value=100000,
        prices={"GLD": 100},
    )
    strategy.vars = SimpleNamespace(get=lambda key, default=None: default, set=lambda key, value: None)
    strategy._last_target_portfolio_planner_result = None
    tool = planner_module.make_target_portfolio_to_execution_plan_tool().binder(strategy, SimpleNamespace())
    handle = AgentHandle(
        manager=SimpleNamespace(strategy=strategy),
        name="portfolio_decision_agent",
        system_prompt="test",
        default_model="test-model",
        tools=[],
        include_builtin_tools=False,
        runtime=object(),
    )
    cached_result = AgentRunResult(
        summary="cached",
        model="test-model",
        cache_hit=True,
        cache_key="cache-key",
        events=[
            AgentTraceEvent(
                kind="tool_call",
                tool_name="target_portfolio_to_execution_plan",
                payload={
                    "date": "2024-09-05",
                    "target_portfolio": [{"symbol": "GLD", "target_weight": 1.0}],
                },
            )
        ],
    )

    handle._replay_cached_side_effects(cached_result, [tool])

    assert strategy._last_target_portfolio_planner_result["execution_plan"] == {
        "schema_version": 1,
        "intent": "rebalance",
        "orders": [
            {
                "sequence": 1,
                "action": "submit_order",
                "symbol": "GLD",
                "side": "buy",
                "quantity_mode": "shares",
                "quantity": 980,
                "asset_type": "stock",
                "order_type": "market",
                "time_in_force": "day",
            }
        ],
    }


def test_validate_execution_plan_matches_planner_result_accepts_exact_tool_plan():
    module, _strategy_class = load_strategy_module()
    strategy = SimpleNamespace(
        _last_target_portfolio_planner_result={
            "execution_plan": {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [
                    {
                        "sequence": 1,
                        "action": "submit_order",
                        "symbol": "SPY",
                        "side": "buy",
                        "quantity_mode": "shares",
                        "quantity": 10,
                        "asset_type": "stock",
                        "order_type": "market",
                        "time_in_force": "day",
                    }
                ],
            }
        }
    )
    execution_plan = module.normalize_execution_plan(strategy._last_target_portfolio_planner_result["execution_plan"])

    module.validate_execution_plan_matches_planner_result(strategy, execution_plan)


def test_validate_execution_plan_matches_planner_result_accepts_equivalent_unnormalized_plan():
    module, _strategy_class = load_strategy_module()
    strategy = SimpleNamespace(
        _last_target_portfolio_planner_result={
            "execution_plan": {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [
                    {
                        "sequence": 1,
                        "action": "submit_order",
                        "symbol": "SPY",
                        "side": "buy",
                        "quantity_mode": "shares",
                        "quantity": 10,
                        "asset_type": "stock",
                        "order_type": "market",
                        "time_in_force": "day",
                    }
                ],
            }
        }
    )
    execution_plan = {
        "schema_version": "1.0",
        "intent": "REBALANCE",
        "orders": [
            {
                "sequence": 1,
                "action": "SUBMIT_ORDER",
                "symbol": " spy ",
                "side": "BUY",
                "quantity": 10,
                "order_type": "MARKET",
            }
        ],
    }

    module.validate_execution_plan_matches_planner_result(strategy, execution_plan)


def test_validate_execution_plan_matches_planner_result_rejects_llm_rewrite():
    module, _strategy_class = load_strategy_module()
    strategy = SimpleNamespace(
        _last_target_portfolio_planner_result={
            "execution_plan": {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [
                    {
                        "sequence": 1,
                        "action": "submit_order",
                        "symbol": "SPY",
                        "side": "buy",
                        "quantity_mode": "shares",
                        "quantity": 10,
                        "asset_type": "stock",
                        "order_type": "market",
                        "time_in_force": "day",
                    }
                ],
            }
        }
    )
    rewritten = module.normalize_execution_plan(
        {
            "schema_version": 1,
            "intent": "rebalance",
            "orders": [
                {
                    "sequence": 1,
                    "action": "submit_order",
                    "symbol": "SPY",
                    "side": "buy",
                    "quantity_mode": "shares",
                    "quantity": 11,
                    "asset_type": "stock",
                    "order_type": "market",
                    "time_in_force": "day",
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="differs from target_portfolio_to_execution_plan"):
        module.validate_execution_plan_matches_planner_result(strategy, rewritten)


def test_validate_portfolio_decision_tool_evidence_requires_planner_tool_even_for_hold():
    module, _strategy_class = load_strategy_module()
    execution_plan = {"schema_version": 1, "intent": "hold", "orders": []}
    decision_result = SimpleNamespace(tool_calls=[])

    with pytest.raises(ValueError, match="must call target_portfolio_to_execution_plan"):
        module.validate_portfolio_decision_tool_evidence(execution_plan, decision_result)


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


def test_on_trading_iteration_runs_agents_in_expected_order_and_context():
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
    planner_plan = {
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
    }
    portfolio_summary = {
        "decision": {"type": "rebalance", "reason_brief": "mock target"},
        "target_portfolio": [
            {"basket_id": "equity", "symbol": "QQQ", "target_weight": 0.50},
            {"basket_id": "tips", "symbol": "TIP", "target_weight": 0.25},
            {"basket_id": "nominal_bond", "symbol": "IEF", "target_weight": 0.25},
        ],
        "execution_plan": planner_plan,
    }

    agent_manager.summaries["macro_allocation_agent"] = _json_summary(macro_report)
    for agent_name, report in basket_reports.items():
        agent_manager.summaries[agent_name] = _json_summary(report)
    agent_manager.summaries["portfolio_decision_agent"] = _json_summary(portfolio_summary)
    agent_manager.tool_calls["portfolio_decision_agent"] = ["target_portfolio_to_execution_plan"]
    agent_manager.planner_results["portfolio_decision_agent"] = {"execution_plan": planner_plan}

    assert agent_manager.tool_calls["portfolio_decision_agent"] == ["target_portfolio_to_execution_plan"]
    assert json.loads(agent_manager.summaries["portfolio_decision_agent"])["execution_plan"] == planner_plan

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

    assert len(agent_manager["execution_agent"].calls) == 1
    execution_task_prompt = agent_manager["execution_agent"].calls[0]["task_prompt"]
    for required_phrase in (
        "Execute the provided execution_plan by calling execution_plan_execute exactly once",
        "complete execution_plan",
        "Summarize the returned plan report",
        "Do not call per-order tools",
    ):
        assert required_phrase in execution_task_prompt
    assert "orders_execute_order" not in execution_task_prompt
    assert "orders_preflight_check" not in execution_task_prompt
    assert "orders_submit_order" not in execution_task_prompt
    assert "orders_confirm_order" not in execution_task_prompt
    assert "orders_submit_and_confirm_order" not in execution_task_prompt
    execution_context = agent_manager["execution_agent"].calls[0]["context"]
    assert execution_context == {
        "date": "2024-09-05",
        "execution_plan": module.execution_plan_execute_payload(planner_plan),
    }
    assert "constraints" not in execution_context["execution_plan"]
    assert all(isinstance(order["quantity"], int) for order in execution_context["execution_plan"]["orders"])
    assert [order["symbol"] for order in execution_context["execution_plan"]["orders"]] == ["QQQ", "TIP", "IEF"]
    from lumibot.components.agents.builtins import _validate_execution_plan

    _intent, _orders, blocker, _orders_requested = _validate_execution_plan(
        execution_context["execution_plan"]
    )
    assert blocker is None


def test_commodity_basket_task_prompt_mentions_rank_first_selection():
    module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()

    macro_report = {
        "basket_weights": {"equity": 0.0, "commodity": 1.0, "tips": 0.0, "nominal_bond": 0.0},
    }
    basket_reports = {
        "equity_basket_agent": {
            "basket_id": "equity",
            "target_weight": 0.0,
            "status": "inactive",
            "candidate_symbols": module.BASKET_UNIVERSES["equity"],
            "selected_symbol": None,
        },
        "commodity_basket_agent": {
            "basket_id": "commodity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["commodity"],
            "selected_symbol": "CPER",
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
    execution_plan = {"schema_version": 1, "intent": "hold", "orders": []}

    agent_manager.summaries["macro_allocation_agent"] = _json_summary(macro_report)
    for agent_name, report in basket_reports.items():
        agent_manager.summaries[agent_name] = _json_summary(report)
    agent_manager.summaries["portfolio_decision_agent"] = _json_summary({"execution_plan": execution_plan})
    agent_manager.tool_calls["portfolio_decision_agent"] = ["target_portfolio_to_execution_plan"]
    agent_manager.planner_results["portfolio_decision_agent"] = {"execution_plan": execution_plan}

    strategy.on_trading_iteration()

    commodity_task = agent_manager["commodity_basket_agent"].calls[0]["task_prompt"].lower()
    assert "computed ranking evidence" in commodity_task
    assert "news only" in commodity_task
    assert "close, conflicting, or incomplete" in commodity_task


def test_basket_task_prompt_requires_candidate_symbols_to_copy_assigned_universe():
    module, _strategy_class = load_strategy_module()

    prompt = module.basket_agent_task_prompt("equity").lower()

    assert (
        "candidate_symbols must copy the assigned basket_symbols exactly; "
        "do not replace it with a shortlist"
    ) in prompt


def test_on_trading_iteration_blocks_when_portfolio_agent_rewrites_planner_plan():
    module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()

    planner_plan = {
        "schema_version": 1,
        "intent": "rebalance",
        "orders": [
            {
                "sequence": 1,
                "symbol": "QQQ",
                "side": "buy",
                "quantity_mode": "shares",
                "quantity": 10,
                "order_type": "market",
            },
        ],
    }
    rewritten_plan = {
        "schema_version": 1,
        "intent": "rebalance",
        "orders": [
            {
                "sequence": 1,
                "symbol": "QQQ",
                "side": "buy",
                "quantity_mode": "shares",
                "quantity": 11,
                "order_type": "market",
            },
        ],
    }
    _seed_active_equity_workflow(agent_manager, module, rewritten_plan)
    agent_manager.planner_results["portfolio_decision_agent"] = {"execution_plan": planner_plan}

    strategy.on_trading_iteration()

    assert "differs from target_portfolio_to_execution_plan" in strategy._last_execution_plan_error
    assert agent_manager["execution_agent"].calls == []


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
    agent_manager.tool_calls["portfolio_decision_agent"] = ["target_portfolio_to_execution_plan"]
    agent_manager.planner_results["portfolio_decision_agent"] = {
        "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []}
    }

    strategy.on_trading_iteration()

    assert agent_manager["portfolio_decision_agent"].calls
    assert agent_manager["execution_agent"].calls == []
    assert strategy._last_execution_plan_error is None


def test_on_trading_iteration_rejects_stale_planner_result_without_fresh_successful_call():
    module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()
    execution_plan = {
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
        ],
    }
    strategy._last_target_portfolio_planner_result = {"execution_plan": execution_plan}
    _seed_active_equity_workflow(agent_manager, module, execution_plan)

    strategy.on_trading_iteration()

    assert "successfully call target_portfolio_to_execution_plan" in strategy._last_execution_plan_error
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
    agent_manager.tool_calls["portfolio_decision_agent"] = ["target_portfolio_to_execution_plan"]


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
    agent_manager.planner_results["portfolio_decision_agent"] = {
        "execution_plan": json.loads(agent_manager.summaries["portfolio_decision_agent"])["execution_plan"]
    }

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
    agent_manager.planner_results["portfolio_decision_agent"] = {
        "execution_plan": json.loads(agent_manager.summaries["portfolio_decision_agent"])["execution_plan"]
    }

    strategy.on_trading_iteration()

    assert "DECISION_SELL_POSITION_REQUIRED" in strategy._last_execution_plan_error
    assert agent_manager["execution_agent"].calls == []


def test_target_portfolio_to_execution_plan_applies_buy_sizing_buffer_to_new_targets():
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
                "quantity": 245,
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
                "quantity": 490,
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
                "quantity": 490,
                "asset_type": "stock",
                "order_type": "market",
                "time_in_force": "day",
            },
        ],
    }
    assert result["cash_projection"]["cash_after_estimate"] == pytest.approx(2000)
    assert result["cash_projection"]["buy_sizing_buffer_pct"] == pytest.approx(0.02)
    assert {row["reason_code"] for row in result["current_vs_target"]} == {"buy_new_target"}


def test_target_portfolio_to_execution_plan_prefers_previous_completed_daily_close_for_sizing():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[],
        cash=100000,
        portfolio_value=100000,
        prices={"GLD": 229.789993},
        historical_closes={
            "GLD": {"date": "2024-09-04", "close": 230.429993},
        },
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-05",
        target_portfolio=[{"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.25}],
    )

    diagnostics = {row["symbol"]: row for row in result["current_vs_target"]}
    gld = diagnostics["GLD"]
    assert gld["sizing_price"] == pytest.approx(230.429993)
    assert gld["sizing_price_source"] == "previous_completed_daily_close"
    assert gld["sizing_price_datetime"] == "2024-09-04"
    assert gld["buy_sizing_buffer_pct"] == pytest.approx(0.02)
    assert gld["effective_buy_target_value"] == pytest.approx(24500.0)
    assert result["execution_plan"]["orders"][0]["quantity"] == 106


def test_target_portfolio_to_execution_plan_falls_back_to_last_price_when_daily_close_missing():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[],
        cash=100000,
        portfolio_value=100000,
        prices={"GLD": 229.789993},
        historical_closes={},
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-05",
        target_portfolio=[{"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.25}],
    )

    diagnostics = {row["symbol"]: row for row in result["current_vs_target"]}
    gld = diagnostics["GLD"]
    assert gld["sizing_price"] == pytest.approx(229.789993)
    assert gld["sizing_price_source"] == "strategy_last_price_fallback"
    assert gld["sizing_price_datetime"] is None
    assert result["execution_plan"]["orders"][0]["quantity"] == 106


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
        ("buy", "TIP", 245),
        ("buy", "GLD", 245),
    ]
    assert result["cash_projection"]["cash_after_estimate"] == pytest.approx(1000)
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
        ("buy", "QQQ", 245),
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
        ("buy", "VGIT", 196),
    ]


def test_target_portfolio_to_execution_plan_buy_buffer_does_not_apply_to_sells():
    planner = importlib.import_module("lumibot.example_strategies.target_portfolio_to_execution_plan")
    strategy = make_planner_strategy(
        positions=[make_position("SPY", 500)],
        cash=0,
        portfolio_value=50000,
        prices={"SPY": 100},
    )

    result = planner.target_portfolio_to_execution_plan(
        strategy,
        date="2024-09-06",
        target_portfolio=[],
    )

    assert [
        (order["side"], order["symbol"], order["quantity"])
        for order in result["execution_plan"]["orders"]
    ] == [("sell", "SPY", 500)]
    diagnostics = {row["symbol"]: row for row in result["current_vs_target"]}
    assert diagnostics["SPY"]["planned_quantity"] == 500


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

    with pytest.raises(ValueError, match="missing sizing price for GLD"):
        planner.target_portfolio_to_execution_plan(
            strategy,
            date="2024-09-06",
            target_portfolio=[
                {"symbol": "SPY", "target_weight": 0.50},
                {"symbol": "GLD", "target_weight": 0.25},
            ],
        )
