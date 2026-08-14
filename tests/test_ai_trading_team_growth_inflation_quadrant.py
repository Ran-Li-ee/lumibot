import importlib
import inspect
import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from lumibot.components.agents.schemas import AgentTraceEvent, ToolDefinition
from lumibot.example_strategies.ai_trading_team_mock_growth_inflation_quadrant import (
    execution_plan_execute_payload,
)


def load_classifier_module():
    return importlib.import_module("lumibot.example_strategies.fred_growth_inflation_regime_classifier")


def load_real_strategy_module():
    module = importlib.import_module("lumibot.example_strategies.ai_trading_team_growth_inflation_quadrant")
    return module, module.AITradingTeamGrowthInflationQuadrantStrategy


def test_examples_benchmark_exposes_real_growth_inflation_quadrant_strategy():
    benchmark = importlib.import_module("scripts.run_ai_trading_team_examples_benchmark")

    assert "growth-inflation-quadrant" in benchmark.STRATEGIES
    assert (
        benchmark.STRATEGIES["growth-inflation-quadrant"].__name__
        == "AITradingTeamGrowthInflationQuadrantStrategy"
    )


def _monthly_observations(start_year=2018, start_month=1, values=None, realtime="2024-09-05"):
    values = list(values or [])
    rows = []
    year = start_year
    month = start_month
    for value in values:
        rows.append(
            {
                "date": f"{year:04d}-{month:02d}-01",
                "value": str(value),
                "realtime_start": realtime,
                "realtime_end": realtime,
            }
        )
        month += 1
        if month == 13:
            month = 1
            year += 1
    return rows


def _quarterly_observations(start_year=2018, start_month=1, values=None, realtime="2024-09-05"):
    values = list(values or [])
    rows = []
    year = start_year
    month = start_month
    for value in values:
        rows.append(
            {
                "date": f"{year:04d}-{month:02d}-01",
                "value": str(value),
                "realtime_start": realtime,
                "realtime_end": realtime,
            }
        )
        month += 3
        if month > 12:
            month -= 12
            year += 1
    return rows


def _payload(series_id, observations, *, source="fred_api", point_in_time_safe=True, uses_revised_data=False):
    return {
        "source": source,
        "series_id": series_id,
        "as_of": "2024-09-05",
        "point_in_time_safe": point_in_time_safe,
        "uses_revised_data": uses_revised_data,
        "observations": observations,
    }


def _contains_key(value, key):
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


class FakeFredClient:
    def __init__(self, payloads):
        self.payloads = dict(payloads)
        self.calls = []

    def get_series(self, series_id, *, start=None, end=None, as_of=None, limit=None):
        self.calls.append(
            {
                "series_id": series_id,
                "start": start,
                "end": end,
                "as_of": as_of,
                "limit": limit,
            }
        )
        value = self.payloads[series_id]
        if isinstance(value, Exception):
            raise value
        return value


class RecordingAgentManager:
    def __init__(self):
        self.created = []
        self._agents = {}
        self.summaries = {}
        self.tool_calls = {}
        self.tool_results = {}
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
            tool_results=[
                result
                if isinstance(result, AgentTraceEvent)
                else AgentTraceEvent(
                    kind="tool_result",
                    tool_name=result[0],
                    payload=result[1],
                )
                for result in self.agent_manager.tool_results.get(self.name, [])
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


def created_tool_names(created_agent):
    return {getattr(tool, "name", "") for tool in created_agent.get("tools", [])}


def _json_summary(payload):
    return json.dumps(payload, separators=(",", ":"))


def _macro_tool_result(payload):
    return ("macro_regime_classifier", payload)


def _real_macro_evidence(module, *, axis, series_id, direction, lag_months, frequency):
    return {
        "axis": axis,
        "series_id": series_id,
        "series_name": (
            "Real Gross Domestic Product"
            if axis == "growth"
            else "Consumer Price Index for All Urban Consumers"
        ),
        "frequency": frequency,
        "lag_months": lag_months,
        "data_cutoff": "2024-03-05" if axis == "growth" else "2024-08-05",
        "latest_observation_date": "2024-01-01" if axis == "growth" else "2024-08-01",
        "comparison_observation_date": "2023-01-01" if axis == "growth" else "2023-08-01",
        "latest_value": 170.0 if axis == "growth" else 175.0,
        "comparison_value": 120.0 if axis == "growth" else 165.0,
        "metric_name": "year_over_year_change",
        "metric_value": 0.41666666666666674 if axis == "growth" else 0.06060606060606055,
        "trend_years": module.DEFAULT_TREND_YEARS,
        "trend_window_observations": 20 if axis == "growth" else 60,
        "trend_value": 0.12 if axis == "growth" else 0.08,
        "margin": 0.29666666666666675 if axis == "growth" else -0.01939393939393945,
        "direction": direction,
    }


def _assert_vintage_evidence_fields(evidence, *, axis, series_id, as_of):
    assert evidence["axis"] == axis
    assert evidence["series_id"] == series_id
    assert evidence["as_of"] == as_of
    assert "lag_months" not in evidence
    assert "data_cutoff" not in evidence
    assert evidence["latest_realtime_start"] == as_of
    assert evidence["latest_realtime_end"] == as_of
    assert evidence["comparison_realtime_start"] == as_of
    assert evidence["comparison_realtime_end"] == as_of
    assert isinstance(evidence["observation_lag_days"], int)
    assert evidence["observation_lag_days"] >= 0


def _passed_real_macro_report(module, *, regime="growth_up_inflation_down", reason_brief="real macro"):
    return {
        "status": "passed",
        "tool": "macro_regime_classifier",
        "mock": False,
        "mode": module.DEFAULT_MODE,
        "date": "2024-09-05",
        "as_of": "2024-09-05",
        "regime": regime,
        "basket_weights": dict(module.WEIGHT_BY_REGIME[regime]),
        "growth_evidence": _real_macro_evidence(
            module,
            axis="growth",
            series_id=module.DEFAULT_GROWTH_SERIES_ID,
            direction="up",
            lag_months=module.DEFAULT_GROWTH_LAG_MONTHS,
            frequency="quarterly",
        ),
        "inflation_evidence": _real_macro_evidence(
            module,
            axis="inflation",
            series_id=module.DEFAULT_INFLATION_SERIES_ID,
            direction="down",
            lag_months=module.DEFAULT_INFLATION_LAG_MONTHS,
            frequency="monthly",
        ),
        "data_quality": {
            "status": "passed",
            "source": "fred_api",
            "point_in_time_safe": True,
            "uses_revised_data": False,
            "required_series": [module.DEFAULT_GROWTH_SERIES_ID, module.DEFAULT_INFLATION_SERIES_ID],
        },
        "confidence": {"growth_margin": 0.01, "inflation_margin": 0.02},
        "reason_brief": reason_brief,
    }


def _install_hold_downstream_summaries(agent_manager, module):
    basket_reports = {
        "equity_basket_agent": {
            "basket_id": "equity",
            "target_weight": 0.50,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["equity"],
            "selected_symbol": "SPY",
        },
        "commodity_basket_agent": {
            "basket_id": "commodity",
            "target_weight": 0.25,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["commodity"],
            "selected_symbol": "GLD",
        },
        "tips_basket_agent": {
            "basket_id": "tips",
            "target_weight": 0.00,
            "status": "inactive",
            "candidate_symbols": module.BASKET_UNIVERSES["tips"],
            "selected_symbol": None,
        },
        "nominal_bond_basket_agent": {
            "basket_id": "nominal_bond",
            "target_weight": 0.25,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["nominal_bond"],
            "selected_symbol": "IEF",
        },
    }
    planner_plan = {"schema_version": 1, "intent": "hold", "orders": []}
    portfolio_summary = {
        "decision": {"type": "hold", "reason_brief": "hold target"},
        "target_portfolio": [
            {"basket_id": "equity", "symbol": "SPY", "target_weight": 0.50},
            {"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.25},
            {"basket_id": "nominal_bond", "symbol": "IEF", "target_weight": 0.25},
        ],
        "execution_plan": planner_plan,
    }
    for agent_name, report in basket_reports.items():
        agent_manager.summaries[agent_name] = _json_summary(report)
    agent_manager.summaries["portfolio_decision_agent"] = _json_summary(portfolio_summary)
    agent_manager.tool_calls["portfolio_decision_agent"] = ["target_portfolio_to_execution_plan"]
    agent_manager.planner_results["portfolio_decision_agent"] = {"execution_plan": planner_plan}


def _growth_payload_with_latest_direction(direction):
    values = [100 + index for index in range(25)]
    if direction == "up":
        values[-1] = 170
    elif direction == "down":
        values[-1] = 118
    else:
        raise ValueError(f"unsupported growth direction: {direction}")
    return _payload("GDPC1", _quarterly_observations(values=values))


def _inflation_payload_with_latest_direction(direction):
    values = [100 + index for index in range(80)]
    if direction == "up":
        values[-1] = 245
    elif direction == "down":
        values[-1] = 175
    else:
        raise ValueError(f"unsupported inflation direction: {direction}")
    return _payload("CPIAUCSL", _monthly_observations(values=values))


def test_real_strategy_creates_expected_agents_and_tool_surfaces(monkeypatch):
    _module, strategy_class = load_real_strategy_module()
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
    created = {agent["name"]: agent for agent in agent_manager.created}
    macro_tools = created["macro_allocation_agent"]["tools"]

    assert created["macro_allocation_agent"]["allow_trading"] is False
    assert created["macro_allocation_agent"]["include_builtin_tools"] is False
    assert created_tool_names(created["macro_allocation_agent"]) == {"macro_regime_classifier"}
    assert macro_tools[0].metadata["mock"] is False
    assert macro_tools[0].metadata["kind"] == "fred_macro_regime"
    for basket_agent in (
        "equity_basket_agent",
        "nominal_bond_basket_agent",
    ):
        assert created_tool_names(created[basket_agent]) == {
            "market_load_history_tables_summary",
            "market_last_price",
        }
    for news_enabled_basket_agent in (
        "commodity_basket_agent",
        "tips_basket_agent",
    ):
        assert created_tool_names(created[news_enabled_basket_agent]) == {
            "market_load_history_tables_summary",
            "market_last_price",
            "alpaca_news",
        }
    assert created_tool_names(created["portfolio_decision_agent"]) == {
        "target_portfolio_to_execution_plan",
    }
    assert created_tool_names(created["execution_agent"]) == {"execution_plan_execute"}


def test_real_strategy_parameters_include_current_weekly_cadence_defaults():
    _module, strategy_class = load_real_strategy_module()

    assert strategy_class.parameters["run_frequency"] == "weekly"
    assert strategy_class.parameters["weekly_run_weekday"] == "MON"
    assert strategy_class.parameters["weekly_holiday_policy"] == "first_open_trading_day"


def test_real_strategy_prompts_do_not_ask_llm_to_classify_macro_or_use_mock_language():
    _module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()

    created = {agent["name"]: agent for agent in agent_manager.created}
    serialized_prompts = " ".join(agent["system_prompt"] for agent in agent_manager.created).lower()

    assert "real fred-backed macro_regime_classifier" in serialized_prompts
    assert "do not classify the macro regime yourself" in serialized_prompts
    for forbidden_word in ("mock", "seed", "seeded_random", "random", "fake"):
        assert forbidden_word not in serialized_prompts
    for basket_agent in (
        "equity_basket_agent",
        "commodity_basket_agent",
        "tips_basket_agent",
        "nominal_bond_basket_agent",
    ):
        basket_prompt = created[basket_agent]["system_prompt"].lower()
        assert "stay inside the assigned basket" in basket_prompt
        assert "do not reclassify macro conditions" not in basket_prompt
        assert "do not place orders" in basket_prompt
    portfolio_prompt = created["portfolio_decision_agent"]["system_prompt"].lower()
    execution_prompt = created["execution_agent"]["system_prompt"].lower()

    assert "do not redo macro or basket research" in portfolio_prompt
    assert "call target_portfolio_to_execution_plan" in portfolio_prompt
    assert "planner tool owns all execution_plan calculations" in portfolio_prompt
    assert "execute only provided execution_plan" in execution_prompt
    assert "call execution_plan_execute exactly once with the complete execution_plan" in execution_prompt


def test_real_strategy_skips_before_weekly_run_day_without_macro_call():
    _module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.parameters["weekly_run_weekday"] = "FRI"
    strategy.initialize()

    strategy.on_trading_iteration()

    assert agent_manager["macro_allocation_agent"].calls == []
    assert strategy._scheduled_workflow_events[-1]["reason"] == "before_weekly_run_day"


@pytest.mark.parametrize("status", ["blocked", "failed"])
def test_real_strategy_macro_not_passed_blocks_downstream_and_clears_stale_planner(status):
    module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()
    stale_planner_result = {"execution_plan": {"schema_version": 1, "intent": "hold", "orders": []}}
    strategy._last_target_portfolio_planner_result = stale_planner_result
    strategy._last_execution_plan_error = "stale execution error"
    macro_report = {
        "status": status,
        "reason": f"macro {status}",
        "data_quality": {"status": status, "errors": [f"macro {status}"]},
    }
    agent_manager.summaries["macro_allocation_agent"] = _json_summary({"status": "passed"})
    agent_manager.tool_results["macro_allocation_agent"] = [_macro_tool_result(macro_report)]

    strategy.on_trading_iteration()

    assert len(agent_manager["macro_allocation_agent"].calls) == 1
    for agent_name in module.BASKET_AGENT_NAMES.values():
        assert agent_manager[agent_name].calls == []
    assert agent_manager["portfolio_decision_agent"].calls == []
    assert agent_manager["execution_agent"].calls == []
    assert strategy._last_macro_regime_error == macro_report
    assert strategy._last_execution_plan_error is None
    assert strategy._last_target_portfolio_planner_result is None


def test_real_strategy_missing_classifier_tool_result_blocks_downstream_and_clears_stale_planner():
    module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()
    strategy._last_target_portfolio_planner_result = {
        "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []}
    }
    strategy._last_execution_plan_error = "stale execution error"
    agent_manager.summaries["macro_allocation_agent"] = "not-json"

    strategy.on_trading_iteration()

    assert len(agent_manager["macro_allocation_agent"].calls) == 1
    for agent_name in module.BASKET_AGENT_NAMES.values():
        assert agent_manager[agent_name].calls == []
    assert agent_manager["portfolio_decision_agent"].calls == []
    assert agent_manager["execution_agent"].calls == []
    assert strategy._last_macro_regime_error["status"] == "failed"
    assert "macro_regime_classifier" in strategy._last_macro_regime_error["reason"]
    assert "tool result payload" in strategy._last_macro_regime_error["reason"]
    assert strategy._last_execution_plan_error is None
    assert strategy._last_target_portfolio_planner_result is None


def test_real_strategy_passed_macro_without_classifier_tool_evidence_blocks_downstream(capsys):
    module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()
    strategy._last_target_portfolio_planner_result = {
        "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []}
    }
    strategy._last_execution_plan_error = "stale execution error"
    macro_report = {
        "status": "passed",
        "tool": "macro_regime_classifier",
        "mock": False,
        "regime": "growth_up_inflation_down",
        "basket_weights": {"equity": 0.50, "commodity": 0.25, "tips": 0.00, "nominal_bond": 0.25},
        "growth_evidence": {"direction": "up"},
        "inflation_evidence": {"direction": "down"},
        "data_quality": {"status": "passed"},
        "confidence": {"growth_margin": 0.01, "inflation_margin": 0.02},
        "reason_brief": "plausible but fabricated macro report",
    }
    agent_manager.summaries["macro_allocation_agent"] = _json_summary(macro_report)
    agent_manager.tool_calls["macro_allocation_agent"] = ["macro_regime_classifier"]

    strategy.on_trading_iteration()

    assert len(agent_manager["macro_allocation_agent"].calls) == 1
    for agent_name in module.BASKET_AGENT_NAMES.values():
        assert agent_manager[agent_name].calls == []
    assert agent_manager["portfolio_decision_agent"].calls == []
    assert agent_manager["execution_agent"].calls == []
    assert strategy._last_macro_regime_error["status"] == "failed"
    assert "macro_regime_classifier" in strategy._last_macro_regime_error["reason"]
    assert strategy._last_execution_plan_error is None
    assert strategy._last_target_portfolio_planner_result is None
    blocked_log = capsys.readouterr().out
    assert "Real quadrant macro workflow blocked" in blocked_log
    assert "macro_regime_classifier" in blocked_log


def test_real_strategy_trusts_tool_result_when_summary_fabricates_different_macro_report():
    module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()
    tool_report = _passed_real_macro_report(module, reason_brief="authoritative tool result")
    fabricated_summary = dict(tool_report)
    fabricated_summary["regime"] = "growth_down_inflation_up"
    fabricated_summary["basket_weights"] = {
        "equity": 0.00,
        "commodity": 0.25,
        "tips": 0.50,
        "nominal_bond": 0.25,
    }
    fabricated_summary["reason_brief"] = "fabricated summary"
    agent_manager.summaries["macro_allocation_agent"] = _json_summary(fabricated_summary)
    agent_manager.tool_calls["macro_allocation_agent"] = ["macro_regime_classifier"]
    agent_manager.tool_results["macro_allocation_agent"] = [_macro_tool_result(tool_report)]
    _install_hold_downstream_summaries(agent_manager, module)

    strategy.on_trading_iteration()

    assert strategy._last_macro_regime_error is None
    commodity_context = agent_manager["commodity_basket_agent"].calls[0]["context"]
    portfolio_context = agent_manager["portfolio_decision_agent"].calls[0]["context"]
    assert commodity_context["macro_allocation_report"] == tool_report
    assert portfolio_context["macro_allocation_report"] == tool_report
    assert strategy._last_real_regime == tool_report["regime"]


def test_real_strategy_allows_malformed_summary_when_classifier_tool_result_passed():
    module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()
    tool_report = _passed_real_macro_report(module, reason_brief="authoritative despite prose")
    agent_manager.summaries["macro_allocation_agent"] = "RESULT: no JSON here"
    agent_manager.tool_calls["macro_allocation_agent"] = ["macro_regime_classifier"]
    agent_manager.tool_results["macro_allocation_agent"] = [_macro_tool_result(tool_report)]
    _install_hold_downstream_summaries(agent_manager, module)

    strategy.on_trading_iteration()

    assert strategy._last_macro_regime_error is None
    assert agent_manager["portfolio_decision_agent"].calls
    assert agent_manager["portfolio_decision_agent"].calls[0]["context"]["macro_allocation_report"] == tool_report
    assert strategy._last_real_regime == tool_report["regime"]


def test_real_strategy_blocks_classifier_tool_result_without_dict_payload(capsys):
    module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()
    strategy._last_target_portfolio_planner_result = {
        "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []}
    }
    strategy._last_execution_plan_error = "stale execution error"
    agent_manager.summaries["macro_allocation_agent"] = _json_summary({"status": "passed"})
    agent_manager.tool_calls["macro_allocation_agent"] = ["macro_regime_classifier"]
    agent_manager.tool_results["macro_allocation_agent"] = [_macro_tool_result(["not", "a", "dict"])]

    strategy.on_trading_iteration()

    for agent_name in module.BASKET_AGENT_NAMES.values():
        assert agent_manager[agent_name].calls == []
    assert agent_manager["portfolio_decision_agent"].calls == []
    assert agent_manager["execution_agent"].calls == []
    assert strategy._last_macro_regime_error["status"] == "failed"
    assert "tool result payload" in strategy._last_macro_regime_error["reason"]
    assert strategy._last_execution_plan_error is None
    assert strategy._last_target_portfolio_planner_result is None
    blocked_log = capsys.readouterr().out
    assert "Real quadrant macro workflow blocked" in blocked_log
    assert "tool result payload" in blocked_log


@pytest.mark.parametrize(
    ("payload_update", "expected_reason"),
    [
        ({"date": "2024-09-04", "as_of": "2024-09-04"}, "current date"),
        (
            {
                "data_quality": {
                    "status": "passed",
                    "required_series": ["CUSTOM_GROWTH", "CUSTOM_INFLATION"],
                },
            },
            "default parameters",
        ),
    ],
)
def test_real_strategy_blocks_noncanonical_passed_classifier_payloads_before_downstream(
    payload_update,
    expected_reason,
    capsys,
):
    module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()
    existing_regime = "growth_down_inflation_up"
    strategy._last_real_regime = existing_regime
    strategy._last_target_portfolio_planner_result = {
        "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []}
    }
    strategy._last_execution_plan_error = "stale execution error"
    macro_report = _passed_real_macro_report(module, regime="growth_up_inflation_down")
    macro_report.update(payload_update)
    agent_manager.summaries["macro_allocation_agent"] = _json_summary({"status": "passed"})
    agent_manager.tool_calls["macro_allocation_agent"] = ["macro_regime_classifier"]
    agent_manager.tool_results["macro_allocation_agent"] = [_macro_tool_result(macro_report)]
    _install_hold_downstream_summaries(agent_manager, module)

    strategy.on_trading_iteration()

    assert len(agent_manager["macro_allocation_agent"].calls) == 1
    for agent_name in module.BASKET_AGENT_NAMES.values():
        assert agent_manager[agent_name].calls == []
    assert agent_manager["portfolio_decision_agent"].calls == []
    assert agent_manager["execution_agent"].calls == []
    assert strategy._last_real_regime == existing_regime
    assert strategy._last_macro_regime_error["status"] == "failed"
    assert "macro_regime_classifier" in strategy._last_macro_regime_error["reason"]
    assert "non-canonical" in strategy._last_macro_regime_error["reason"]
    assert expected_reason in strategy._last_macro_regime_error["reason"]
    assert strategy._last_execution_plan_error is None
    assert strategy._last_target_portfolio_planner_result is None
    blocked_log = capsys.readouterr().out
    assert "Real quadrant macro workflow blocked" in blocked_log
    assert "non-canonical" in blocked_log


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("missing_regime", "regime"),
        ("missing_basket_weights", "basket_weights"),
        ("incomplete_basket_weights", "basket_weights"),
        ("bad_provenance_flag", "point_in_time_safe"),
    ],
)
def test_real_strategy_blocks_incomplete_passed_classifier_payloads_before_downstream(
    mutation,
    expected_reason,
    capsys,
):
    module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()
    existing_regime = "growth_down_inflation_up"
    strategy._last_real_regime = existing_regime
    strategy._last_target_portfolio_planner_result = {
        "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []}
    }
    strategy._last_execution_plan_error = "stale execution error"
    macro_report = _passed_real_macro_report(module, regime="growth_up_inflation_down")
    if mutation == "missing_regime":
        del macro_report["regime"]
    elif mutation == "missing_basket_weights":
        del macro_report["basket_weights"]
    elif mutation == "incomplete_basket_weights":
        del macro_report["basket_weights"]["nominal_bond"]
    elif mutation == "bad_provenance_flag":
        macro_report["data_quality"]["point_in_time_safe"] = False
    else:
        raise AssertionError(f"unknown mutation: {mutation}")
    agent_manager.summaries["macro_allocation_agent"] = _json_summary({"status": "passed"})
    agent_manager.tool_calls["macro_allocation_agent"] = ["macro_regime_classifier"]
    agent_manager.tool_results["macro_allocation_agent"] = [_macro_tool_result(macro_report)]
    _install_hold_downstream_summaries(agent_manager, module)

    strategy.on_trading_iteration()

    assert len(agent_manager["macro_allocation_agent"].calls) == 1
    for agent_name in module.BASKET_AGENT_NAMES.values():
        assert agent_manager[agent_name].calls == []
    assert agent_manager["portfolio_decision_agent"].calls == []
    assert agent_manager["execution_agent"].calls == []
    assert strategy._last_real_regime == existing_regime
    assert strategy._last_macro_regime_error["status"] == "failed"
    assert "non-canonical" in strategy._last_macro_regime_error["reason"]
    assert expected_reason in strategy._last_macro_regime_error["reason"]
    assert strategy._last_execution_plan_error is None
    assert strategy._last_target_portfolio_planner_result is None
    blocked_log = capsys.readouterr().out
    assert "Real quadrant macro workflow blocked" in blocked_log
    assert expected_reason in blocked_log


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("unknown_regime", "regime"),
        ("extra_basket_key", "basket_weights"),
        ("wrong_regime_weights", "basket_weights"),
        ("missing_evidence_direction", "direction"),
        ("missing_evidence_metric_value", "metric_value"),
    ],
)
def test_real_strategy_blocks_noncanonical_classifier_regime_weights_and_evidence_before_downstream(
    mutation,
    expected_reason,
    capsys,
):
    module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()
    existing_regime = "growth_down_inflation_up"
    strategy._last_real_regime = existing_regime
    strategy._last_target_portfolio_planner_result = {
        "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []}
    }
    strategy._last_execution_plan_error = "stale execution error"
    macro_report = _passed_real_macro_report(module, regime="growth_up_inflation_down")
    if mutation == "unknown_regime":
        macro_report["regime"] = "growth_flat_inflation_down"
    elif mutation == "extra_basket_key":
        macro_report["basket_weights"]["crypto"] = 0.0
    elif mutation == "wrong_regime_weights":
        macro_report["basket_weights"] = {
            "equity": 0.25,
            "commodity": 0.50,
            "tips": 0.25,
            "nominal_bond": 0.00,
        }
    elif mutation == "missing_evidence_direction":
        del macro_report["growth_evidence"]["direction"]
    elif mutation == "missing_evidence_metric_value":
        del macro_report["growth_evidence"]["metric_value"]
    else:
        raise AssertionError(f"unknown mutation: {mutation}")
    agent_manager.summaries["macro_allocation_agent"] = _json_summary({"status": "passed"})
    agent_manager.tool_calls["macro_allocation_agent"] = ["macro_regime_classifier"]
    agent_manager.tool_results["macro_allocation_agent"] = [_macro_tool_result(macro_report)]
    _install_hold_downstream_summaries(agent_manager, module)

    strategy.on_trading_iteration()

    assert len(agent_manager["macro_allocation_agent"].calls) == 1
    for agent_name in module.BASKET_AGENT_NAMES.values():
        assert agent_manager[agent_name].calls == []
    assert agent_manager["portfolio_decision_agent"].calls == []
    assert agent_manager["execution_agent"].calls == []
    assert strategy._last_real_regime == existing_regime
    assert strategy._last_macro_regime_error["status"] == "failed"
    assert "non-canonical" in strategy._last_macro_regime_error["reason"]
    assert expected_reason in strategy._last_macro_regime_error["reason"]
    assert strategy._last_execution_plan_error is None
    assert strategy._last_target_portfolio_planner_result is None
    blocked_log = capsys.readouterr().out
    assert "Real quadrant macro workflow blocked" in blocked_log
    assert expected_reason in blocked_log


@pytest.mark.parametrize(
    ("regime", "growth_direction", "inflation_direction"),
    [
        ("growth_up_inflation_down", "down", "down"),
        ("growth_up_inflation_down", "up", "up"),
        ("growth_down_inflation_up", "up", "up"),
    ],
)
def test_real_strategy_blocks_evidence_directions_inconsistent_with_regime_before_downstream(
    regime,
    growth_direction,
    inflation_direction,
    capsys,
):
    module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()
    existing_regime = "growth_down_inflation_down"
    strategy._last_real_regime = existing_regime
    strategy._last_target_portfolio_planner_result = {
        "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []}
    }
    strategy._last_execution_plan_error = "stale execution error"
    macro_report = _passed_real_macro_report(module, regime=regime)
    macro_report["growth_evidence"]["direction"] = growth_direction
    macro_report["inflation_evidence"]["direction"] = inflation_direction
    agent_manager.summaries["macro_allocation_agent"] = _json_summary({"status": "passed"})
    agent_manager.tool_calls["macro_allocation_agent"] = ["macro_regime_classifier"]
    agent_manager.tool_results["macro_allocation_agent"] = [_macro_tool_result(macro_report)]
    _install_hold_downstream_summaries(agent_manager, module)

    strategy.on_trading_iteration()

    assert len(agent_manager["macro_allocation_agent"].calls) == 1
    for agent_name in module.BASKET_AGENT_NAMES.values():
        assert agent_manager[agent_name].calls == []
    assert agent_manager["portfolio_decision_agent"].calls == []
    assert agent_manager["execution_agent"].calls == []
    assert strategy._last_real_regime == existing_regime
    assert strategy._last_macro_regime_error["status"] == "failed"
    assert "non-canonical" in strategy._last_macro_regime_error["reason"]
    assert "evidence directions" in strategy._last_macro_regime_error["reason"]
    assert strategy._last_execution_plan_error is None
    assert strategy._last_target_portfolio_planner_result is None
    blocked_log = capsys.readouterr().out
    assert "Real quadrant macro workflow blocked" in blocked_log
    assert "evidence directions" in blocked_log


@pytest.mark.parametrize(
    ("axis", "frequency"),
    [
        ("growth", "monthly"),
        ("inflation", "quarterly"),
        ("growth", "daily"),
        ("inflation", "daily"),
    ],
)
def test_real_strategy_blocks_wrong_evidence_frequency_before_downstream(
    axis,
    frequency,
    capsys,
):
    module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()
    existing_regime = "growth_down_inflation_up"
    strategy._last_real_regime = existing_regime
    strategy._last_target_portfolio_planner_result = {
        "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []}
    }
    strategy._last_execution_plan_error = "stale execution error"
    macro_report = _passed_real_macro_report(module, regime="growth_up_inflation_down")
    macro_report[f"{axis}_evidence"]["frequency"] = frequency
    agent_manager.summaries["macro_allocation_agent"] = _json_summary({"status": "passed"})
    agent_manager.tool_calls["macro_allocation_agent"] = ["macro_regime_classifier"]
    agent_manager.tool_results["macro_allocation_agent"] = [_macro_tool_result(macro_report)]
    _install_hold_downstream_summaries(agent_manager, module)

    strategy.on_trading_iteration()

    assert len(agent_manager["macro_allocation_agent"].calls) == 1
    for agent_name in module.BASKET_AGENT_NAMES.values():
        assert agent_manager[agent_name].calls == []
    assert agent_manager["portfolio_decision_agent"].calls == []
    assert agent_manager["execution_agent"].calls == []
    assert strategy._last_real_regime == existing_regime
    assert strategy._last_macro_regime_error["status"] == "failed"
    assert "non-canonical" in strategy._last_macro_regime_error["reason"]
    assert "frequency" in strategy._last_macro_regime_error["reason"]
    assert strategy._last_execution_plan_error is None
    assert strategy._last_target_portfolio_planner_result is None
    blocked_log = capsys.readouterr().out
    assert "Real quadrant macro workflow blocked" in blocked_log
    assert "frequency" in blocked_log


@pytest.mark.parametrize("axis", ["growth", "inflation"])
def test_real_strategy_blocks_wrong_evidence_metric_name_before_downstream(axis, capsys):
    module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()
    existing_regime = "growth_down_inflation_up"
    strategy._last_real_regime = existing_regime
    strategy._last_target_portfolio_planner_result = {
        "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []}
    }
    strategy._last_execution_plan_error = "stale execution error"
    macro_report = _passed_real_macro_report(module, regime="growth_up_inflation_down")
    macro_report[f"{axis}_evidence"]["metric_name"] = "fabricated_metric"
    agent_manager.summaries["macro_allocation_agent"] = _json_summary({"status": "passed"})
    agent_manager.tool_calls["macro_allocation_agent"] = ["macro_regime_classifier"]
    agent_manager.tool_results["macro_allocation_agent"] = [_macro_tool_result(macro_report)]
    _install_hold_downstream_summaries(agent_manager, module)

    strategy.on_trading_iteration()

    assert len(agent_manager["macro_allocation_agent"].calls) == 1
    for agent_name in module.BASKET_AGENT_NAMES.values():
        assert agent_manager[agent_name].calls == []
    assert agent_manager["portfolio_decision_agent"].calls == []
    assert agent_manager["execution_agent"].calls == []
    assert strategy._last_real_regime == existing_regime
    assert strategy._last_macro_regime_error["status"] == "failed"
    assert "non-canonical" in strategy._last_macro_regime_error["reason"]
    assert "metric_name" in strategy._last_macro_regime_error["reason"]
    assert strategy._last_execution_plan_error is None
    assert strategy._last_target_portfolio_planner_result is None
    blocked_log = capsys.readouterr().out
    assert "Real quadrant macro workflow blocked" in blocked_log
    assert "metric_name" in blocked_log


def test_real_strategy_passed_macro_runs_downstream_with_real_macro_context():
    module, strategy_class = load_real_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()

    macro_report = _passed_real_macro_report(module, regime="growth_up_inflation_down")
    basket_reports = {
        "equity_basket_agent": {
            "basket_id": "equity",
            "target_weight": 0.50,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["equity"],
            "selected_symbol": "SPY",
        },
        "commodity_basket_agent": {
            "basket_id": "commodity",
            "target_weight": 0.25,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["commodity"],
            "selected_symbol": "GLD",
        },
        "tips_basket_agent": {
            "basket_id": "tips",
            "target_weight": 0.00,
            "status": "inactive",
            "candidate_symbols": module.BASKET_UNIVERSES["tips"],
            "selected_symbol": None,
        },
        "nominal_bond_basket_agent": {
            "basket_id": "nominal_bond",
            "target_weight": 0.25,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["nominal_bond"],
            "selected_symbol": "IEF",
        },
    }
    planner_plan = {
        "schema_version": 1,
        "intent": "rebalance",
        "orders": [
            {
                "sequence": 1,
                "symbol": "SPY",
                "side": "buy",
                "quantity_mode": "shares",
                "quantity": 100,
                "order_type": "market",
            },
            {
                "sequence": 2,
                "symbol": "GLD",
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
        "decision": {"type": "rebalance", "reason_brief": "real target"},
        "target_portfolio": [
            {"basket_id": "equity", "symbol": "SPY", "target_weight": 0.50},
            {"basket_id": "commodity", "symbol": "GLD", "target_weight": 0.25},
            {"basket_id": "nominal_bond", "symbol": "IEF", "target_weight": 0.25},
        ],
        "execution_plan": planner_plan,
    }
    agent_manager.summaries["macro_allocation_agent"] = _json_summary(macro_report)
    agent_manager.tool_calls["macro_allocation_agent"] = ["macro_regime_classifier"]
    agent_manager.tool_results["macro_allocation_agent"] = [_macro_tool_result(macro_report)]
    for agent_name, report in basket_reports.items():
        agent_manager.summaries[agent_name] = _json_summary(report)
    agent_manager.summaries["portfolio_decision_agent"] = _json_summary(portfolio_summary)
    agent_manager.tool_calls["portfolio_decision_agent"] = ["target_portfolio_to_execution_plan"]
    agent_manager.planner_results["portfolio_decision_agent"] = {"execution_plan": planner_plan}

    strategy.on_trading_iteration()

    assert strategy._last_macro_regime_error is None
    assert strategy._last_execution_plan_error is None
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
    assert macro_context["macro_regime_mode"] == module.DEFAULT_MODE
    assert macro_context["growth_series_id"] == module.DEFAULT_GROWTH_SERIES_ID
    assert macro_context["inflation_series_id"] == module.DEFAULT_INFLATION_SERIES_ID
    assert macro_context["growth_lag_months"] == module.DEFAULT_GROWTH_LAG_MONTHS
    assert macro_context["inflation_lag_months"] == module.DEFAULT_INFLATION_LAG_MONTHS
    assert macro_context["trend_years"] == module.DEFAULT_TREND_YEARS
    assert macro_context["basket_universes"] == module.BASKET_UNIVERSES

    commodity_context = agent_manager["commodity_basket_agent"].calls[0]["context"]
    assert commodity_context["target_weight"] == 0.25
    assert commodity_context["macro_allocation_report"] == macro_report
    portfolio_context = agent_manager["portfolio_decision_agent"].calls[0]["context"]
    assert portfolio_context["macro_allocation_report"] == macro_report
    assert portfolio_context["equity_basket_report"] == basket_reports["equity_basket_agent"]
    assert portfolio_context["commodity_basket_report"] == basket_reports["commodity_basket_agent"]
    assert portfolio_context["tips_basket_report"] == basket_reports["tips_basket_agent"]
    assert portfolio_context["nominal_bond_basket_report"] == basket_reports["nominal_bond_basket_agent"]
    execution_context = agent_manager["execution_agent"].calls[0]["context"]
    assert execution_context == {
        "date": "2024-09-05",
        "execution_plan": execution_plan_execute_payload(planner_plan),
    }


def test_subtract_months_handles_quarter_and_year_boundaries():
    module = load_classifier_module()

    assert module.subtract_months(date(2024, 9, 5), 6) == date(2024, 3, 5)
    assert module.subtract_months(date(2024, 1, 31), 1) == date(2023, 12, 31)
    assert module.subtract_months(date(2024, 3, 31), 1) == date(2024, 2, 29)


def test_growth_axis_evidence_uses_latest_usable_lagged_quarter():
    module = load_classifier_module()
    values = [100 + index for index in range(26)]
    values[-1] = 1000
    payload = _payload("GDPC1", _quarterly_observations(values=values))

    evidence = module.calculate_axis_evidence(
        payload,
        axis="growth",
        series_name="Real Gross Domestic Product",
        frequency="quarterly",
        trading_date=date(2024, 9, 5),
        lag_months=6,
        periods_back=4,
        trend_window_observations=20,
        trend_years=5,
    )

    expected_usable_rows = [row for row in payload["observations"] if row["date"] <= "2024-03-05"]
    post_cutoff_rows = [row for row in payload["observations"] if row["date"] > "2024-03-05"]
    usable_values = [float(row["value"]) for row in expected_usable_rows]
    metrics = [(usable_values[index] / usable_values[index - 4]) - 1 for index in range(4, len(usable_values))]
    all_values = [float(row["value"]) for row in payload["observations"]]
    unfiltered_metrics = [(all_values[index] / all_values[index - 4]) - 1 for index in range(4, len(all_values))]
    expected_direction = "up" if metrics[-1] > sum(metrics[-20:]) / 20 else "down"
    unfiltered_direction = "up" if unfiltered_metrics[-1] > sum(unfiltered_metrics[-20:]) / 20 else "down"

    assert expected_usable_rows[-1]["date"] == "2024-01-01"
    assert post_cutoff_rows[-1]["date"] == "2024-04-01"
    assert unfiltered_direction != expected_direction
    assert evidence["data_cutoff"] == "2024-03-05"
    assert evidence["latest_observation_date"] == expected_usable_rows[-1]["date"]
    assert evidence["metric_value"] == pytest.approx(metrics[-1])
    assert evidence["trend_value"] == pytest.approx(sum(metrics[-20:]) / 20)
    assert evidence["direction"] == expected_direction


def test_inflation_axis_evidence_uses_latest_usable_lagged_month():
    module = load_classifier_module()
    values = [100 + index for index in range(81)]
    values[-1] = 1000
    payload = _payload("CPIAUCSL", _monthly_observations(values=values))

    evidence = module.calculate_axis_evidence(
        payload,
        axis="inflation",
        series_name="Consumer Price Index for All Urban Consumers",
        frequency="monthly",
        trading_date=date(2024, 9, 5),
        lag_months=1,
        periods_back=12,
        trend_window_observations=60,
        trend_years=5,
    )

    expected_usable_rows = [row for row in payload["observations"] if row["date"] <= "2024-08-05"]
    post_cutoff_rows = [row for row in payload["observations"] if row["date"] > "2024-08-05"]
    usable_values = [float(row["value"]) for row in expected_usable_rows]
    metrics = [(usable_values[index] / usable_values[index - 12]) - 1 for index in range(12, len(usable_values))]
    all_values = [float(row["value"]) for row in payload["observations"]]
    unfiltered_metrics = [
        (all_values[index] / all_values[index - 12]) - 1 for index in range(12, len(all_values))
    ]
    expected_direction = "up" if metrics[-1] > sum(metrics[-60:]) / 60 else "down"
    unfiltered_direction = "up" if unfiltered_metrics[-1] > sum(unfiltered_metrics[-60:]) / 60 else "down"

    assert expected_usable_rows[-1]["date"] == "2024-08-01"
    assert post_cutoff_rows[-1]["date"] == "2024-09-01"
    assert unfiltered_direction != expected_direction
    assert evidence["data_cutoff"] == "2024-08-05"
    assert evidence["latest_observation_date"] == expected_usable_rows[-1]["date"]
    assert evidence["metric_value"] == pytest.approx(metrics[-1])
    assert evidence["trend_value"] == pytest.approx(sum(metrics[-60:]) / 60)
    assert evidence["direction"] == expected_direction


def test_equal_metric_and_trend_classifies_axis_as_down():
    module = load_classifier_module()
    values = [100.0] * 24
    payload = _payload("GDPC1", _quarterly_observations(values=values))

    evidence = module.calculate_axis_evidence(
        payload,
        axis="growth",
        series_name="Real Gross Domestic Product",
        frequency="quarterly",
        trading_date=date(2024, 9, 5),
        lag_months=0,
        periods_back=4,
        trend_window_observations=20,
        trend_years=5,
    )

    assert evidence["metric_value"] == pytest.approx(0.0)
    assert evidence["trend_value"] == pytest.approx(0.0)
    assert evidence["margin"] == pytest.approx(0.0)
    assert evidence["direction"] == "down"


def test_axis_evidence_rejects_missing_uses_revised_data_flag():
    module = load_classifier_module()
    payload = _payload("GDPC1", _quarterly_observations(values=[100 + index for index in range(24)]))
    del payload["uses_revised_data"]

    with pytest.raises(ValueError, match="uses_revised_data"):
        module.calculate_axis_evidence(
            payload,
            axis="growth",
            series_name="Real Gross Domestic Product",
            frequency="quarterly",
            trading_date=date(2024, 9, 5),
            lag_months=0,
            periods_back=4,
            trend_window_observations=20,
            trend_years=5,
        )


@pytest.mark.parametrize("uses_revised_data", [None, "false", "true", True])
def test_axis_evidence_rejects_non_false_uses_revised_data_flags(uses_revised_data):
    module = load_classifier_module()
    payload = _payload(
        "GDPC1",
        _quarterly_observations(values=[100 + index for index in range(24)]),
        uses_revised_data=uses_revised_data,
    )

    with pytest.raises(ValueError, match="uses_revised_data"):
        module.calculate_axis_evidence(
            payload,
            axis="growth",
            series_name="Real Gross Domestic Product",
            frequency="quarterly",
            trading_date=date(2024, 9, 5),
            lag_months=0,
            periods_back=4,
            trend_window_observations=20,
            trend_years=5,
        )


@pytest.mark.parametrize(
    ("parameter", "invalid_value"),
    [
        ("lag_months", -1),
        ("periods_back", 0),
        ("trend_window_observations", 0),
        ("trend_years", 0),
    ],
)
def test_axis_evidence_rejects_invalid_numeric_parameters(parameter, invalid_value):
    module = load_classifier_module()
    payload = _payload("GDPC1", _quarterly_observations(values=[100 + index for index in range(24)]))
    kwargs = {
        "lag_months": 0,
        "periods_back": 4,
        "trend_window_observations": 20,
        "trend_years": 5,
        parameter: invalid_value,
    }

    with pytest.raises(ValueError, match=parameter):
        module.calculate_axis_evidence(
            payload,
            axis="growth",
            series_name="Real Gross Domestic Product",
            frequency="quarterly",
            trading_date=date(2024, 9, 5),
            **kwargs,
        )


def test_classify_growth_inflation_regime_defaults_to_same_day_fred_vintage():
    module = load_classifier_module()
    fred = FakeFredClient(
        {
            "GDPC1": _payload(
                "GDPC1",
                _quarterly_observations(
                    values=[100 + index for index in range(25)],
                    realtime="2024-09-05",
                ),
            ),
            "CPIAUCSL": _payload(
                "CPIAUCSL",
                _monthly_observations(
                    values=[200 + index for index in range(80)],
                    realtime="2024-09-05",
                ),
            ),
        }
    )

    result = module.classify_growth_inflation_regime(fred, date="2024-09-05")

    assert result["status"] == "passed"
    assert result["mode"] == "fred_ra_vintage_asof"
    assert result["date"] == "2024-09-05"
    assert result["as_of"] == "2024-09-05"
    assert result["requested_as_of"] == "2024-09-05"
    assert result["effective_as_of"] == "2024-09-05"
    assert result["lookahead_clamped"] is False
    assert result["as_of_policy"] == "same_day_vintage"
    assert result["data_quality"]["as_of_policy"] == "same_day_vintage"
    assert result["data_quality"]["requested_as_of"] == "2024-09-05"
    assert result["data_quality"]["effective_as_of"] == "2024-09-05"
    assert result["data_quality"]["lookahead_clamped"] is False
    assert result["data_quality"]["point_in_time_safe"] is True
    assert result["data_quality"]["uses_revised_data"] is False
    _assert_vintage_evidence_fields(
        result["growth_evidence"],
        axis="growth",
        series_id="GDPC1",
        as_of="2024-09-05",
    )
    _assert_vintage_evidence_fields(
        result["inflation_evidence"],
        axis="inflation",
        series_id="CPIAUCSL",
        as_of="2024-09-05",
    )
    assert [call["series_id"] for call in fred.calls] == ["GDPC1", "CPIAUCSL"]
    assert all(call["as_of"] == "2024-09-05" for call in fred.calls)
    assert all(call["end"] == "2024-09-05" for call in fred.calls)


def test_classify_growth_inflation_regime_clamps_future_requested_as_of_to_max_as_of():
    module = load_classifier_module()
    fred = FakeFredClient(
        {
            "GDPC1": _payload("GDPC1", _quarterly_observations(values=[100 + i for i in range(25)])),
            "CPIAUCSL": _payload("CPIAUCSL", _monthly_observations(values=[200 + i for i in range(80)])),
        }
    )

    result = module.classify_growth_inflation_regime(
        fred,
        date="2024-09-05",
        requested_as_of="2024-09-10",
        max_as_of="2024-09-05",
    )

    assert result["status"] == "passed"
    assert result["requested_as_of"] == "2024-09-10"
    assert result["effective_as_of"] == "2024-09-05"
    assert result["lookahead_clamped"] is True
    assert all(call["as_of"] == "2024-09-05" for call in fred.calls)
    assert all(call["end"] == "2024-09-05" for call in fred.calls)


def test_classify_growth_inflation_regime_previous_day_policy_uses_prior_calendar_day():
    module = load_classifier_module()
    fred = FakeFredClient(
        {
            "GDPC1": _payload("GDPC1", _quarterly_observations(values=[100 + i for i in range(25)])),
            "CPIAUCSL": _payload("CPIAUCSL", _monthly_observations(values=[200 + i for i in range(80)])),
        }
    )

    result = module.classify_growth_inflation_regime(
        fred,
        date="2024-09-05",
        as_of_policy="previous_day_vintage",
    )

    assert result["status"] == "passed"
    assert result["requested_as_of"] == "2024-09-04"
    assert result["effective_as_of"] == "2024-09-04"
    assert result["as_of_policy"] == "previous_day_vintage"
    assert all(call["as_of"] == "2024-09-04" for call in fred.calls)


@pytest.mark.parametrize(
    ("growth_direction", "inflation_direction", "expected_regime", "expected_weights"),
    [
        (
            "up",
            "down",
            "growth_up_inflation_down",
            {"equity": 0.50, "commodity": 0.25, "tips": 0.00, "nominal_bond": 0.25},
        ),
        (
            "up",
            "up",
            "growth_up_inflation_up",
            {"equity": 0.25, "commodity": 0.50, "tips": 0.25, "nominal_bond": 0.00},
        ),
        (
            "down",
            "up",
            "growth_down_inflation_up",
            {"equity": 0.00, "commodity": 0.25, "tips": 0.50, "nominal_bond": 0.25},
        ),
        (
            "down",
            "down",
            "growth_down_inflation_down",
            {"equity": 0.25, "commodity": 0.25, "tips": 0.00, "nominal_bond": 0.50},
        ),
    ],
)
def test_classify_growth_inflation_regime_maps_all_quadrants(
    growth_direction,
    inflation_direction,
    expected_regime,
    expected_weights,
):
    module = load_classifier_module()
    previous_regime = "growth_up_inflation_down"
    fred = FakeFredClient(
        {
            "GDPC1": _growth_payload_with_latest_direction(growth_direction),
            "CPIAUCSL": _inflation_payload_with_latest_direction(inflation_direction),
        }
    )

    result = module.classify_growth_inflation_regime(
        fred,
        date="2024-09-05",
        mode=module.LEGACY_LAGGED_MODE,
        previous_regime=previous_regime,
    )

    assert result["tool"] == "macro_regime_classifier"
    assert result["status"] == "passed"
    assert result["mock"] is False
    assert result["mode"] == "fred_ra_simple_lagged"
    assert result["date"] == "2024-09-05"
    assert result["as_of"] == "2024-09-05"
    assert result["regime"] == expected_regime
    assert result["growth_direction"] == growth_direction
    assert result["inflation_direction"] == inflation_direction
    assert result["previous_regime"] == previous_regime
    assert result["regime_changed"] == (expected_regime != previous_regime)
    assert result["basket_weights"] == expected_weights
    assert result["growth_evidence"]["axis"] == "growth"
    assert result["growth_evidence"]["direction"] == growth_direction
    assert result["growth_evidence"]["lag_months"] == module.DEFAULT_GROWTH_LAG_MONTHS
    assert "data_cutoff" in result["growth_evidence"]
    assert result["inflation_evidence"]["axis"] == "inflation"
    assert result["inflation_evidence"]["direction"] == inflation_direction
    assert result["inflation_evidence"]["lag_months"] == module.DEFAULT_INFLATION_LAG_MONTHS
    assert "data_cutoff" in result["inflation_evidence"]
    assert "requested_as_of" not in result
    assert result["data_quality"] == {
        "status": "passed",
        "source": "fred_api",
        "point_in_time_safe": True,
        "uses_revised_data": False,
        "required_series": ["GDPC1", "CPIAUCSL"],
        "warnings": [],
        "errors": [],
    }
    assert result["confidence"]["growth_margin"] == pytest.approx(result["growth_evidence"]["margin"])
    assert result["confidence"]["inflation_margin"] == pytest.approx(result["inflation_evidence"]["margin"])
    assert "Growth" in result["reason_brief"]
    assert "Inflation" in result["reason_brief"]
    assert [call["series_id"] for call in fred.calls] == ["GDPC1", "CPIAUCSL"]
    assert fred.calls[0]["start"] == "2012-09-01"
    assert fred.calls[0]["end"] == "2024-09-05"
    assert fred.calls[0]["as_of"] == "2024-09-05"
    assert fred.calls[0]["limit"] is None


def test_make_real_macro_regime_classifier_tool_binds_stateful_tool():
    module = load_classifier_module()
    factory_calls = []

    class FakeStrategy:
        _last_real_regime = None

        def get_datetime(self):
            return datetime(2024, 9, 5, 15, 30)

    custom_growth = _growth_payload_with_latest_direction("down")
    custom_growth["series_id"] = "CUSTOM_GROWTH"
    custom_inflation = _inflation_payload_with_latest_direction("up")
    custom_inflation["series_id"] = "CUSTOM_INFLATION"
    fred = FakeFredClient(
        {
            "GDPC1": _growth_payload_with_latest_direction("up"),
            "CPIAUCSL": _inflation_payload_with_latest_direction("down"),
            "CUSTOM_GROWTH": custom_growth,
            "CUSTOM_INFLATION": custom_inflation,
        }
    )

    def fred_factory(strategy):
        factory_calls.append(strategy)
        return fred

    strategy = FakeStrategy()
    tool_definition = module.make_real_macro_regime_classifier_tool(fred_factory=fred_factory)

    assert isinstance(tool_definition, ToolDefinition)
    assert tool_definition.name == "macro_regime_classifier"
    assert tool_definition.metadata == {"kind": "fred_macro_regime", "mock": False, "replay_on_cache": True}
    bound_tool = tool_definition.binder(strategy, manager=None)
    description = bound_tool.description.lower()

    assert bound_tool.name == "macro_regime_classifier"
    assert "fred" in description
    assert "gdpc1" in description
    assert "cpiaucsl" in description
    assert "lag" in description
    assert "trend" in description
    assert bound_tool.metadata["kind"] == "fred_macro_regime"
    assert bound_tool.metadata["mock"] is False
    assert bound_tool.metadata["replay_on_cache"] is True

    default_result = bound_tool.function()

    assert default_result["tool"] == "macro_regime_classifier"
    assert default_result["status"] == "passed"
    assert default_result["mock"] is False
    assert default_result["date"] == "2024-09-05"
    assert default_result["previous_regime"] is None
    assert strategy._last_real_regime is None

    stateful_result = bound_tool.function(date="2024-09-05")

    assert stateful_result["status"] == "passed"
    assert stateful_result["previous_regime"] is None
    assert strategy._last_real_regime is None

    explicit_result = bound_tool.function(
        date="2024-09-05",
        mode=module.LEGACY_LAGGED_MODE,
        growth_series_id="CUSTOM_GROWTH",
        inflation_series_id="CUSTOM_INFLATION",
        growth_lag_months=3,
        inflation_lag_months=0,
        trend_years=4,
    )

    assert factory_calls == [strategy]
    assert explicit_result["status"] == "passed"
    assert explicit_result["data_quality"]["required_series"] == ["CUSTOM_GROWTH", "CUSTOM_INFLATION"]
    assert explicit_result["growth_evidence"]["lag_months"] == 3
    assert explicit_result["inflation_evidence"]["lag_months"] == 0
    assert explicit_result["growth_evidence"]["trend_years"] == 4
    assert explicit_result["inflation_evidence"]["trend_years"] == 4
    assert explicit_result["growth_evidence"]["trend_window_observations"] == 16
    assert explicit_result["inflation_evidence"]["trend_window_observations"] == 48
    assert [call["series_id"] for call in fred.calls] == [
        "GDPC1",
        "CPIAUCSL",
        "GDPC1",
        "CPIAUCSL",
        "CUSTOM_GROWTH",
        "CUSTOM_INFLATION",
    ]


def test_make_real_macro_regime_classifier_tool_hides_previous_regime_argument():
    module = load_classifier_module()

    class FakeStrategy:
        def get_datetime(self):
            return datetime(2024, 9, 5, 15, 30)

    fred = FakeFredClient(
        {
            "GDPC1": _growth_payload_with_latest_direction("up"),
            "CPIAUCSL": _inflation_payload_with_latest_direction("down"),
        }
    )
    strategy = FakeStrategy()
    bound_tool = module.make_real_macro_regime_classifier_tool(
        fred_factory=lambda _strategy: fred
    ).binder(strategy, manager=None)

    assert "previous_regime" not in inspect.signature(bound_tool.function).parameters
    with pytest.raises(TypeError, match="previous_regime"):
        bound_tool.function(previous_regime="growth_down_inflation_down")


def test_bound_real_macro_regime_classifier_uses_strategy_parameters_as_defaults():
    module = load_classifier_module()

    class FakeStrategy:
        _last_real_regime = None
        parameters = {
            "macro_regime_mode": module.LEGACY_LAGGED_MODE,
            "mode": "unsupported_mode_should_not_win",
            "growth_series_id": "CUSTOM_GROWTH",
            "inflation_series_id": "CUSTOM_INFLATION",
            "growth_lag_months": 3,
            "inflation_lag_months": 0,
            "trend_years": 4,
        }

        def get_datetime(self):
            return datetime(2024, 9, 5, 15, 30)

    custom_growth = _growth_payload_with_latest_direction("down")
    custom_growth["series_id"] = "CUSTOM_GROWTH"
    custom_inflation = _inflation_payload_with_latest_direction("up")
    custom_inflation["series_id"] = "CUSTOM_INFLATION"
    fred = FakeFredClient(
        {
            "CUSTOM_GROWTH": custom_growth,
            "CUSTOM_INFLATION": custom_inflation,
        }
    )
    strategy = FakeStrategy()
    bound_tool = module.make_real_macro_regime_classifier_tool(
        fred_factory=lambda _strategy: fred
    ).binder(strategy, manager=None)

    result = bound_tool.function()

    assert result["status"] == "passed"
    assert result["mode"] == module.LEGACY_LAGGED_MODE
    assert result["date"] == "2024-09-05"
    assert result["previous_regime"] is None
    assert result["data_quality"]["required_series"] == ["CUSTOM_GROWTH", "CUSTOM_INFLATION"]
    assert result["growth_evidence"]["lag_months"] == 3
    assert result["inflation_evidence"]["lag_months"] == 0
    assert result["growth_evidence"]["trend_years"] == 4
    assert result["inflation_evidence"]["trend_years"] == 4
    assert result["growth_evidence"]["trend_window_observations"] == 16
    assert result["inflation_evidence"]["trend_window_observations"] == 48
    assert [call["series_id"] for call in fred.calls] == ["CUSTOM_GROWTH", "CUSTOM_INFLATION"]
    assert strategy._last_real_regime is None


def test_bound_real_macro_regime_classifier_explicit_defaults_do_not_mutate_state():
    module = load_classifier_module()
    existing_regime = "growth_down_inflation_up"

    class FakeStrategy:
        _last_real_regime = existing_regime

        def get_datetime(self):
            return datetime(2024, 9, 5, 15, 30)

    fred = FakeFredClient(
        {
            "GDPC1": _growth_payload_with_latest_direction("up"),
            "CPIAUCSL": _inflation_payload_with_latest_direction("down"),
        }
    )
    strategy = FakeStrategy()
    bound_tool = module.make_real_macro_regime_classifier_tool(
        fred_factory=lambda _strategy: fred
    ).binder(strategy, manager=None)

    result = bound_tool.function(
        date="2024-09-05",
        mode=module.DEFAULT_MODE,
        growth_series_id=module.DEFAULT_GROWTH_SERIES_ID,
        inflation_series_id=module.DEFAULT_INFLATION_SERIES_ID,
        trend_years=module.DEFAULT_TREND_YEARS,
    )

    assert result["status"] == "passed"
    assert result["date"] == "2024-09-05"
    assert result["previous_regime"] == existing_regime
    assert result["regime"] != existing_regime
    assert result["data_quality"]["required_series"] == ["GDPC1", "CPIAUCSL"]
    assert strategy._last_real_regime == existing_regime


def test_bound_real_macro_regime_classifier_failed_or_blocked_results_do_not_mutate_state():
    module = load_classifier_module()
    existing_regime = "growth_up_inflation_down"

    class FakeStrategy:
        _last_real_regime = existing_regime

        def get_datetime(self):
            return datetime(2024, 9, 5, 15, 30)

    cases = [
        (
            FakeFredClient(
                {"GDPC1": ValueError("FRED_API_KEY is required to fetch FRED macro data.")}
            ),
            "blocked",
        ),
        (
            FakeFredClient(
                {
                    "GDPC1": _payload(
                        "GDPC1",
                        _quarterly_observations(values=[100 + index for index in range(25)]),
                        point_in_time_safe=False,
                    ),
                    "CPIAUCSL": _inflation_payload_with_latest_direction("down"),
                }
            ),
            "failed",
        ),
    ]

    for fred, expected_status in cases:
        strategy = FakeStrategy()
        bound_tool = module.make_real_macro_regime_classifier_tool(
            fred_factory=lambda _strategy, fred=fred: fred
        ).binder(strategy, manager=None)

        result = bound_tool.function()

        assert result["status"] == expected_status
        assert strategy._last_real_regime == existing_regime


def test_bound_real_macro_regime_classifier_custom_call_does_not_mutate_state():
    module = load_classifier_module()
    existing_regime = "growth_up_inflation_down"

    class FakeStrategy:
        _last_real_regime = existing_regime

        def get_datetime(self):
            return datetime(2024, 9, 5, 15, 30)

    custom_growth = _growth_payload_with_latest_direction("down")
    custom_growth["series_id"] = "CUSTOM_GROWTH"
    custom_inflation = _inflation_payload_with_latest_direction("up")
    custom_inflation["series_id"] = "CUSTOM_INFLATION"
    fred = FakeFredClient(
        {
            "CUSTOM_GROWTH": custom_growth,
            "CUSTOM_INFLATION": custom_inflation,
        }
    )
    strategy = FakeStrategy()
    bound_tool = module.make_real_macro_regime_classifier_tool(
        fred_factory=lambda _strategy: fred
    ).binder(strategy, manager=None)

    result = bound_tool.function(
        date="2024-09-04",
        mode=module.LEGACY_LAGGED_MODE,
        growth_series_id="CUSTOM_GROWTH",
        inflation_series_id="CUSTOM_INFLATION",
        growth_lag_months=3,
        inflation_lag_months=0,
        trend_years=module.DEFAULT_TREND_YEARS,
    )

    assert result["status"] == "passed"
    assert result["date"] == "2024-09-04"
    assert result["previous_regime"] == existing_regime
    assert result["data_quality"]["required_series"] == ["CUSTOM_GROWTH", "CUSTOM_INFLATION"]
    assert result["growth_evidence"]["lag_months"] == 3
    assert result["inflation_evidence"]["lag_months"] == 0
    assert strategy._last_real_regime == existing_regime


def test_regime_from_directions_rejects_invalid_direction():
    module = load_classifier_module()

    with pytest.raises(ValueError, match="unsupported regime directions"):
        module.regime_from_directions("flat", "up")


def test_classify_growth_inflation_regime_blocks_missing_fred_key_without_throwing():
    module = load_classifier_module()
    fred = FakeFredClient({"GDPC1": ValueError("FRED_API_KEY is required to fetch FRED macro data.")})

    result = module.classify_growth_inflation_regime(
        fred,
        date="2024-09-05",
        mode=module.LEGACY_LAGGED_MODE,
    )

    assert result["tool"] == "macro_regime_classifier"
    assert result["status"] == "blocked"
    assert result["mock"] is False
    assert result["mode"] == "fred_ra_simple_lagged"
    assert result["date"] == "2024-09-05"
    assert result["as_of"] == "2024-09-05"
    assert result["reason"] == "missing_fred_api_key"
    assert result["data_quality"]["status"] == "blocked"
    assert "FRED_API_KEY is required" in result["data_quality"]["errors"][0]
    assert result["data_quality"]["warnings"] == []
    assert "regime" not in result


def test_classify_growth_inflation_regime_reraises_programmer_errors():
    module = load_classifier_module()
    fred = FakeFredClient({"GDPC1": TypeError("programmer bug")})

    with pytest.raises(TypeError, match="programmer bug"):
        module.classify_growth_inflation_regime(fred, date="2024-09-05")


def test_classify_growth_inflation_regime_rejects_unsupported_mode_before_fetching():
    module = load_classifier_module()
    fred = FakeFredClient({})

    result = module.classify_growth_inflation_regime(
        fred,
        date="2024-09-05",
        mode="fred_ra_simpl_lagged",
    )

    assert result["status"] == "failed"
    assert result["reason"] == "unsupported_mode"
    assert result["mode"] == "fred_ra_simpl_lagged"
    assert "Unsupported mode" in result["data_quality"]["errors"][0]
    assert fred.calls == []


@pytest.mark.parametrize(
    ("field_name", "unsafe_value", "expected_error"),
    [
        ("point_in_time_safe", False, "point_in_time_safe is not true"),
        ("uses_revised_data", True, "uses_revised_data is not false"),
    ],
)
def test_classify_growth_inflation_regime_fails_unsafe_payload_without_throwing(
    field_name,
    unsafe_value,
    expected_error,
):
    module = load_classifier_module()
    growth_payload = _growth_payload_with_latest_direction("up")
    growth_payload[field_name] = unsafe_value
    fred = FakeFredClient(
        {
            "GDPC1": growth_payload,
            "CPIAUCSL": _inflation_payload_with_latest_direction("down"),
        }
    )

    result = module.classify_growth_inflation_regime(fred, date="2024-09-05")

    assert result["status"] == "failed"
    assert result["reason"] == "data_validation_failed"
    assert result["mock"] is False
    assert result["data_quality"]["status"] == "failed"
    assert expected_error in result["data_quality"]["errors"][0]
    assert "regime" not in result


def test_classify_growth_inflation_regime_failure_reports_requested_series_ids():
    module = load_classifier_module()
    growth_series_id = "CUSTOM_GROWTH"
    inflation_series_id = "CUSTOM_INFLATION"
    growth_payload = _payload(
        growth_series_id,
        _quarterly_observations(values=[100 + index for index in range(25)]),
        point_in_time_safe=False,
    )
    fred = FakeFredClient(
        {
            growth_series_id: growth_payload,
            inflation_series_id: _payload(
                inflation_series_id,
                _monthly_observations(values=[100 + index for index in range(80)]),
            ),
        }
    )

    result = module.classify_growth_inflation_regime(
        fred,
        date="2024-09-05",
        growth_series_id=growth_series_id,
        inflation_series_id=inflation_series_id,
    )

    assert result["status"] == "failed"
    assert result["reason"] == "data_validation_failed"
    assert result["data_quality"]["required_series"] == [growth_series_id, inflation_series_id]


def test_classify_growth_inflation_regime_blocked_reports_requested_series_ids():
    module = load_classifier_module()
    growth_series_id = "CUSTOM_GROWTH"
    inflation_series_id = "CUSTOM_INFLATION"
    fred = FakeFredClient(
        {
            growth_series_id: ValueError("FRED_API_KEY is required to fetch FRED macro data."),
        }
    )

    result = module.classify_growth_inflation_regime(
        fred,
        date="2024-09-05",
        growth_series_id=growth_series_id,
        inflation_series_id=inflation_series_id,
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "missing_fred_api_key"
    assert result["data_quality"]["required_series"] == [growth_series_id, inflation_series_id]


def test_classify_growth_inflation_regime_outputs_are_json_safe_without_raw_observations():
    module = load_classifier_module()
    success = module.classify_growth_inflation_regime(
        FakeFredClient(
            {
                "GDPC1": _growth_payload_with_latest_direction("up"),
                "CPIAUCSL": _inflation_payload_with_latest_direction("down"),
            }
        ),
        date="2024-09-05",
    )
    blocked = module.classify_growth_inflation_regime(
        FakeFredClient({"GDPC1": ValueError("FRED_API_KEY is required to fetch FRED macro data.")}),
        date="2024-09-05",
    )
    failed = module.classify_growth_inflation_regime(
        FakeFredClient(
            {
                "GDPC1": _payload(
                    "GDPC1",
                    _quarterly_observations(values=[100 + index for index in range(25)]),
                    point_in_time_safe=False,
                ),
                "CPIAUCSL": _inflation_payload_with_latest_direction("down"),
            }
        ),
        date="2024-09-05",
    )

    for result in (success, blocked, failed):
        json.dumps(result)

    assert not _contains_key(success, "observations")


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (
            _payload(
                "GDPC1",
                _quarterly_observations(values=[100 + index for index in range(25)]),
                source="csv",
            ),
            "unexpected source",
        ),
        (
            _payload(
                "GDPC1",
                _quarterly_observations(values=[100 + index for index in range(25)])
                + [
                    {
                        "date": "2024-10-01",
                        "value": "130",
                        "realtime_start": "2024-09-05",
                        "realtime_end": "2024-09-05",
                    }
                ],
            ),
            "after trading date",
        ),
        (
            _payload("GDPC1", _quarterly_observations(values=[100 + index for index in range(8)])),
            "needs at least",
        ),
    ],
)
def test_classify_growth_inflation_regime_fails_validation_branches_without_throwing(
    payload,
    expected_error,
):
    module = load_classifier_module()
    fred = FakeFredClient(
        {
            "GDPC1": payload,
            "CPIAUCSL": _inflation_payload_with_latest_direction("down"),
        }
    )

    result = module.classify_growth_inflation_regime(fred, date="2024-09-05")

    assert result["status"] == "failed"
    assert result["reason"] == "data_validation_failed"
    assert result["data_quality"]["status"] == "failed"
    assert expected_error in result["data_quality"]["errors"][0]
    assert result["data_quality"]["warnings"] == []
    assert "regime" not in result
