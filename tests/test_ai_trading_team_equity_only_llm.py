import importlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_module():
    return importlib.import_module("lumibot.example_strategies.ai_trading_team_equity_only_llm")


EXPECTED_EQUITY_UNIVERSE = [
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


class RecordingAgentManager:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(name=kwargs["name"])


class RecordingRunAgent:
    def __init__(self, name, manager):
        self.name = name
        self.manager = manager
        self.calls = []

    def run(self, *, task_prompt, context):
        self.calls.append({"task_prompt": task_prompt, "context": context})
        summary = self.manager.summaries[self.name]
        return SimpleNamespace(summary=summary, tool_calls=[], tool_results=[])


class RecordingRunAgentManager:
    def __init__(self):
        self.created = []
        self.summaries = {}
        self._agents = {}

    def create(self, **kwargs):
        self.created.append(kwargs)
        agent = RecordingRunAgent(kwargs["name"], self)
        self._agents[kwargs["name"]] = agent
        return agent

    def __getitem__(self, name):
        return self._agents[name]


def make_strategy(strategy_class):
    strategy = object.__new__(strategy_class)
    strategy.agents = RecordingAgentManager()
    strategy.parameters = dict(strategy_class.parameters)
    strategy.get_datetime = lambda: datetime(2024, 9, 5, 9, 30)
    strategy.get_cash = lambda: 100000.0
    strategy.get_portfolio_value = lambda: 100000.0
    strategy.get_last_price = lambda asset, **kwargs: 100.0
    strategy.get_positions = lambda: []
    return strategy


def make_running_strategy(strategy_class):
    strategy = object.__new__(strategy_class)
    strategy.agents = RecordingRunAgentManager()
    strategy.parameters = dict(strategy_class.parameters)
    strategy.get_datetime = lambda: datetime(2024, 9, 5, 9, 30)
    strategy.get_cash = lambda: 100000.0
    strategy.get_portfolio_value = lambda: 100000.0
    strategy.get_last_price = lambda asset, **kwargs: 100.0
    strategy.get_positions = lambda: []
    strategy._initialize_equity_only_workflow_state()
    strategy.agents.create(name="equity_basket_agent")
    strategy.agents.create(name="execution_agent")
    return strategy


def created_agent_config(strategy, name):
    matches = [agent for agent in strategy.agents.created if agent["name"] == name]
    assert len(matches) == 1
    return matches[0]


def tool_names(agent_config):
    return [tool.name for tool in agent_config.get("tools", [])]


def test_equity_only_target_portfolio_uses_selected_symbol_at_full_weight():
    module = load_module()

    result = module.equity_only_target_portfolio(
        {
            "basket_id": "equity",
            "status": "active",
            "selected_symbol": "orcl",
            "reason_brief": "Strongest equity candidate.",
        },
        equity_universe=["SPY", "ORCL", "MSFT"],
    )

    assert result == [{"basket_id": "equity", "symbol": "ORCL", "target_weight": 1.0}]


def test_equity_only_target_portfolio_accepts_selected_status_synonym():
    module = load_module()

    result = module.equity_only_target_portfolio(
        {
            "basket_id": "equity",
            "status": "selected",
            "selected_symbol": "spy",
            "reason_brief": "SPY is selected from the equity basket.",
        },
        equity_universe=["SPY", "ORCL", "MSFT"],
    )

    assert result == [{"basket_id": "equity", "symbol": "SPY", "target_weight": 1.0}]


def test_validate_execution_plan_symbols_accepts_selected_status_synonym():
    module = load_module()

    module.validate_execution_plan_symbols(
        {
            "schema_version": 1,
            "intent": "rebalance",
            "orders": [
                {
                    "sequence": 1,
                    "action": "submit_order",
                    "symbol": "SPY",
                    "asset_type": "stock",
                    "side": "buy",
                    "quantity": 10,
                    "quantity_mode": "shares",
                    "order_type": "market",
                    "time_in_force": "day",
                }
            ],
        },
        {
            "basket_id": "equity",
            "status": "selected",
            "selected_symbol": "SPY",
        },
    )


def test_equity_universe_contains_50_us_stock_symbols_without_old_etfs():
    helpers = importlib.import_module("lumibot.example_strategies.ai_trading_team_equity_only_helpers")

    universe = helpers.EQUITY_UNIVERSE

    assert universe == EXPECTED_EQUITY_UNIVERSE
    assert len(universe) == 50
    assert len(set(universe)) == 50
    assert all(symbol == symbol.upper() for symbol in universe)
    assert all(symbol.isalpha() for symbol in universe)
    assert not {"SPY", "QQQ", "IWM", "EEM", "FXI"} & set(universe)


def test_equity_only_target_portfolio_rejects_inactive_report():
    module = load_module()

    with pytest.raises(ValueError, match="equity report must be active"):
        module.equity_only_target_portfolio(
            {"basket_id": "equity", "status": "inactive", "selected_symbol": "ORCL"},
            equity_universe=["SPY", "ORCL"],
        )


def test_equity_only_target_portfolio_rejects_symbol_outside_universe():
    module = load_module()

    with pytest.raises(ValueError, match="selected equity symbol must be in equity universe"):
        module.equity_only_target_portfolio(
            {"basket_id": "equity", "status": "active", "selected_symbol": "GLD"},
            equity_universe=["SPY", "ORCL"],
        )


def test_equity_only_target_portfolio_rejects_non_equity_report():
    module = load_module()

    with pytest.raises(ValueError, match="basket_id must be 'equity'"):
        module.equity_only_target_portfolio(
            {"basket_id": "commodity", "status": "active", "selected_symbol": "GLD"},
            equity_universe=["SPY", "ORCL"],
        )


def test_initialize_creates_only_equity_and_execution_agents():
    module = load_module()
    strategy = make_strategy(module.AITradingTeamEquityOnlyLLMStrategy)

    strategy.initialize()

    created_names = [agent["name"] for agent in strategy.agents.created]
    assert created_names == ["equity_basket_agent", "execution_agent"]
    assert "macro_allocation_agent" not in created_names
    assert "portfolio_decision_agent" not in created_names
    assert "commodity_basket_agent" not in created_names
    assert "tips_basket_agent" not in created_names
    assert "nominal_bond_basket_agent" not in created_names
    assert strategy._run_frequency == "monthly"
    equity_prompt = strategy.agents.created[0]["system_prompt"].lower()
    assert "quadrant" not in equity_prompt
    assert "commodity" not in equity_prompt
    assert "tips" not in equity_prompt
    assert "nominal bond" not in equity_prompt


def test_equity_agent_tool_surface_includes_rank_price_and_news_only():
    module = load_module()
    strategy = make_strategy(module.AITradingTeamEquityOnlyLLMStrategy)

    strategy.initialize()

    equity_agent = created_agent_config(strategy, "equity_basket_agent")
    execution_agent = created_agent_config(strategy, "execution_agent")

    assert equity_agent["include_builtin_tools"] is False
    assert equity_agent["allow_trading"] is False
    assert tool_names(equity_agent) == [
        "market_load_history_tables_summary",
        "market_last_price",
        "alpaca_news",
    ]
    assert execution_agent["include_builtin_tools"] is False
    assert execution_agent["allow_trading"] is True
    assert tool_names(execution_agent) == ["execution_plan_execute"]


def test_equity_agent_system_prompt_is_equity_only_rank_first_and_conditional_news():
    module = load_module()
    strategy = make_strategy(module.AITradingTeamEquityOnlyLLMStrategy)

    strategy.initialize()

    equity_agent = created_agent_config(strategy, "equity_basket_agent")
    prompt = equity_agent["system_prompt"].lower()

    for required in (
        "equity-only",
        "choose exactly one stock",
        "market_load_history_tables_summary",
        "separate evidence",
        "alpaca_news",
        "leading candidates",
        "strict json",
        "do not place orders",
    ):
        assert required in prompt

    for forbidden in (
        "quadrant",
        "macro regime",
        "commodity",
        "tips",
        "nominal bond",
        "defensive posture",
        "duckdb",
    ):
        assert forbidden not in prompt


def test_equity_agent_task_prompt_teaches_summary_first_top_n_and_strict_json(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamEquityOnlyLLMStrategy)
    strategy.parameters["basket_universes"] = {
        **strategy.parameters["basket_universes"],
        "equity": EXPECTED_EQUITY_UNIVERSE,
    }
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": EXPECTED_EQUITY_UNIVERSE,
            "selected_symbol": "ORCL",
            "reason_brief": "ORCL has the strongest setup.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."

    def fake_target_portfolio_to_execution_plan(strategy_arg, *, date, target_portfolio):
        return {
            "schema_version": "1.0",
            "date": date,
            "target_portfolio": target_portfolio,
            "current_vs_target": [],
            "cash_projection": {
                "cash_before": 100000.0,
                "estimated_sell_proceeds": 0.0,
                "estimated_buy_cost": 0.0,
                "cash_after_estimate": 100000.0,
                "buy_sizing_buffer_pct": 0.02,
                "negative_cash_allowed": False,
            },
            "execution_plan": {
                "schema_version": 1,
                "intent": "hold",
                "orders": [],
            },
            "warnings": [],
        }

    monkeypatch.setattr(module, "target_portfolio_to_execution_plan", fake_target_portfolio_to_execution_plan)

    strategy.on_trading_iteration()

    equity_call = strategy.agents["equity_basket_agent"].calls[0]
    task_prompt = equity_call["task_prompt"].lower()

    assert equity_call["context"]["basket_symbols"] == EXPECTED_EQUITY_UNIVERSE
    assert "market_load_history_tables_summary" in task_prompt
    assert "length=252" in task_prompt
    assert "timestep='day'" in task_prompt
    assert "top_n=10" in task_prompt
    assert "alpaca_news" in task_prompt
    assert "close, conflicting, or uncertain" in task_prompt
    assert "candidate_symbols must copy the assigned basket_symbols exactly" in task_prompt
    assert "return exactly one strict json object" in task_prompt


def test_equity_only_order_cash_check_price_uses_planner_sizing_policy(monkeypatch):
    module = load_module()
    strategy = make_strategy(module.AITradingTeamEquityOnlyLLMStrategy)
    captured = {}

    def fake_order_cash_check_price(strategy_arg, asset, **kwargs):
        captured["strategy"] = strategy_arg
        captured["asset"] = asset
        captured["kwargs"] = kwargs
        return {
            "price": 32.78,
            "source": "yahoo_daily_open",
            "datetime": "2024-09-30T09:30:00-04:00",
            "granularity": "1D",
            "field": "open",
            "warning": None,
        }

    monkeypatch.setattr(module, "target_portfolio_order_cash_check_price", fake_order_cash_check_price)
    asset = SimpleNamespace(symbol="FXI")

    result = strategy.get_agent_order_cash_check_price(asset, order={"symbol": "FXI"})

    assert result["price"] == pytest.approx(32.78)
    assert result["source"] == "yahoo_daily_open"
    assert captured == {
        "strategy": strategy,
        "asset": asset,
        "kwargs": {"order": {"symbol": "FXI"}},
    }


def test_monthly_cadence_runs_once_per_month():
    module = load_module()
    strategy = make_strategy(module.AITradingTeamEquityOnlyLLMStrategy)

    strategy.initialize()

    first = strategy._scheduled_workflow_decision(datetime(2024, 9, 5).date())
    strategy._mark_scheduled_workflow_attempted(first)
    second = strategy._scheduled_workflow_decision(datetime(2024, 9, 9).date())
    third = strategy._scheduled_workflow_decision(datetime(2024, 10, 1).date())

    assert first["should_run"] is True
    assert first["reason"] == "monthly_first_observed_trading_day"
    assert second["should_run"] is False
    assert second["reason"] == "monthly_workflow_already_attempted"
    assert third["should_run"] is True


def test_iteration_builds_full_weight_target_and_runs_execution(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamEquityOnlyLLMStrategy)
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": strategy.parameters["basket_universes"]["equity"],
            "selected_symbol": "ORCL",
            "reason_brief": "ORCL has the strongest setup.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."
    strategy.parameters["basket_universes"] = {
        **strategy.parameters["basket_universes"],
        "equity": ["ORCL", "MSFT"],
    }
    planner_calls = []

    def fake_target_portfolio_to_execution_plan(strategy_arg, *, date, target_portfolio):
        planner_calls.append(
            {
                "strategy": strategy_arg,
                "date": date,
                "target_portfolio": target_portfolio,
            }
        )
        return {
            "schema_version": "1.0",
            "date": date,
            "target_portfolio": target_portfolio,
            "current_vs_target": [
                {
                    "symbol": "ORCL",
                    "planned_side": "buy",
                    "planned_quantity": 10,
                    "sizing_price": 100.0,
                }
            ],
            "cash_projection": {
                "cash_before": 100000.0,
                "estimated_sell_proceeds": 0.0,
                "estimated_buy_cost": 1000.0,
                "cash_after_estimate": 99000.0,
                "buy_sizing_buffer_pct": 0.02,
                "negative_cash_allowed": False,
            },
            "execution_plan": {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [
                    {
                        "sequence": 1,
                        "action": "submit_order",
                        "symbol": "ORCL",
                        "asset_type": "stock",
                        "side": "buy",
                        "quantity": 10,
                        "quantity_mode": "shares",
                        "order_type": "market",
                        "time_in_force": "day",
                    }
                ],
            },
            "warnings": [],
        }

    monkeypatch.setattr(module, "target_portfolio_to_execution_plan", fake_target_portfolio_to_execution_plan)

    strategy.on_trading_iteration()

    assert planner_calls == [
        {
            "strategy": strategy,
            "date": "2024-09-05",
            "target_portfolio": [{"basket_id": "equity", "symbol": "ORCL", "target_weight": 1.0}],
        }
    ]
    equity_agent = strategy.agents["equity_basket_agent"]
    assert len(equity_agent.calls) == 1
    equity_context = equity_agent.calls[0]["context"]
    assert equity_context["target_weight"] == 1.0
    assert equity_context["basket_id"] == "equity"
    assert "macro_allocation_report" not in equity_context
    execution_agent = strategy.agents["execution_agent"]
    assert len(execution_agent.calls) == 1
    execution_plan = execution_agent.calls[0]["context"]["execution_plan"]
    assert execution_plan["orders"][0]["symbol"] == "ORCL"


def test_benchmark_runner_exposes_neutral_equity_only_strategy():
    benchmark = importlib.import_module("scripts.run_ai_trading_team_examples_benchmark")

    assert "equity-only-llm" in benchmark.STRATEGIES
    assert benchmark.STRATEGIES["equity-only-llm"].__name__ == "AITradingTeamEquityOnlyLLMStrategy"


def test_benchmark_runner_does_not_expose_quadrant_strategies_on_equity_mainline():
    benchmark = importlib.import_module("scripts.run_ai_trading_team_examples_benchmark")

    assert "mock-growth-inflation-quadrant" not in benchmark.STRATEGIES
    assert "growth-inflation-quadrant" not in benchmark.STRATEGIES


def test_equity_only_strategy_does_not_import_quadrant_strategy_module():
    module = load_module()

    source = Path(module.__file__).read_text(encoding="utf-8")

    assert "ai_trading_team_mock_growth_inflation_quadrant" not in source
