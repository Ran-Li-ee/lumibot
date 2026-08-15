import importlib
import json
from datetime import datetime
from types import SimpleNamespace

import pytest


def load_module():
    return importlib.import_module("lumibot.example_strategies.ai_trading_team_equity_only_llm")


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
