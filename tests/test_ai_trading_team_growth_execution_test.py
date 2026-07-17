import importlib
import inspect
import json
import re
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

REMOVED_CONCEPTS = (
    "inflation_agent",
    "debt_liquidity_agent",
    "thoughtful_disagreement",
    "inflation report",
    "liquidity report",
    "debt report",
    "disagreement report",
    "three views",
    "four reports",
    "idea meritocracy",
)


class RecordingAgentManager:
    def __init__(self):
        self.created = []
        self._agents = {}
        self.summaries = {}

    def create(self, **kwargs):
        self.created.append(kwargs)
        agent = RecordingAgent(kwargs["name"], self.summaries)
        self._agents[kwargs["name"]] = agent
        return agent

    def __getitem__(self, name):
        return self._agents[name]


class RecordingAgent:
    def __init__(self, name, summaries=None):
        self.name = name
        self.summaries = summaries if summaries is not None else {}
        self.calls = []

    def run(self, *, task_prompt, context):
        self.calls.append({"task_prompt": task_prompt, "context": context})
        return SimpleNamespace(summary=self.summaries.get(self.name, f"{self.name} summary"))


def load_strategy_module():
    module = importlib.import_module("lumibot.example_strategies.ai_trading_team_growth_execution_test")
    return module, module.AITradingTeamGrowthExecutionTestStrategy


def make_strategy_with_agent_manager(strategy_class, agent_manager):
    strategy = object.__new__(strategy_class)
    strategy.agents = agent_manager
    strategy.parameters = {"universe": ["SPY", "QQQ", "TLT"]}
    strategy.get_datetime = lambda: datetime(2026, 4, 7, 9, 30)
    return strategy


def assert_removed_concepts_absent(text):
    lower_text = text.lower()
    for concept in REMOVED_CONCEPTS:
        assert concept not in lower_text


def assert_sequence_side_relationship(prompt_text, sequence, side):
    sequence_pattern = rf"sequence[\"']?\s*[:=]?\s*[\"']?{sequence}[\"']?"
    side_pattern = rf"(?:side|action)[\"']?\s*[:=]?\s*[\"']?{side}[\"']?"
    pattern = rf"({sequence_pattern}.{{0,120}}{side_pattern}|{side_pattern}.{{0,120}}{sequence_pattern})"
    assert re.search(pattern, prompt_text, re.DOTALL), (
        f"expected sequence {sequence} to be structurally associated with {side}"
    )


def test_parse_execution_plan_extracts_clean_plan_from_result_text():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = (
        '{"decision":{"type":"buy","from":"USD","to":"GLD","reason_brief":"analysis stays out of execution"},'
        '"execution_plan":{"schema_version":1,"intent":"enter_position","orders":[{"sequence":1,'
        '"action":"submit_order","symbol":"GLD","side":"buy","quantity":null,'
        '"quantity_mode":"max_affordable_cash","order_type":"market"}],"constraints":{}}}'
        '\nRESULT: human explanation should not reach execution.'
    )

    plan = strategy_module.parse_execution_plan_from_decision_summary(raw_summary)

    assert plan == {
        "schema_version": 1,
        "intent": "enter_position",
        "orders": [
            {
                "sequence": 1,
                "action": "submit_order",
                "symbol": "GLD",
                "asset_type": "stock",
                "side": "buy",
                "quantity": None,
                "quantity_mode": "max_affordable_cash",
                "cash_buffer_pct": 2,
                "order_type": "market",
                "limit_price": None,
                "stop_price": None,
                "stop_limit_price": None,
                "trail_price": None,
                "trail_percent": None,
                "time_in_force": "day",
            }
        ],
        "constraints": {
            "allow_negative_cash": False,
            "if_any_order_blocked": "stop_remaining_orders",
        },
    }


def test_parse_execution_plan_sorts_orders_and_applies_sell_defaults():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = """
    {
      "execution_plan": {
        "schema_version": 1,
        "intent": "rotate",
        "orders": [
          {
            "sequence": 2,
            "action": "submit_order",
            "symbol": "GLD",
            "side": "buy",
            "quantity": null,
            "quantity_mode": "max_affordable_after_prior_sells",
            "order_type": "market"
          },
          {
            "sequence": 1,
            "action": "submit_order",
            "symbol": "VNQ",
            "side": "sell",
            "quantity": null,
            "quantity_mode": "current_position",
            "order_type": "market"
          }
        ],
        "constraints": {"allow_negative_cash": false}
      }
    }
    """

    plan = strategy_module.parse_execution_plan_from_decision_summary(raw_summary)

    assert [order["sequence"] for order in plan["orders"]] == [1, 2]
    assert plan["orders"][0]["side"] == "sell"
    assert plan["orders"][0]["cash_buffer_pct"] == 0
    assert plan["orders"][1]["cash_buffer_pct"] == 2


def test_parse_execution_plan_enforces_safety_constraint_defaults():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = """
    {
      "execution_plan": {
        "schema_version": 1,
        "intent": "hold",
        "orders": [],
        "constraints": {
          "allow_negative_cash": true,
          "if_any_order_blocked": "continue",
          "cash_buffer_pct": 3
        }
      }
    }
    """

    plan = strategy_module.parse_execution_plan_from_decision_summary(raw_summary)

    assert plan["constraints"]["allow_negative_cash"] is False
    assert plan["constraints"]["if_any_order_blocked"] == "stop_remaining_orders"
    assert plan["constraints"]["cash_buffer_pct"] == 3


def test_parse_execution_plan_accepts_uppercase_hold_intent():
    strategy_module, _strategy_class = load_strategy_module()

    plan = strategy_module.parse_execution_plan_from_decision_summary(
        '{"execution_plan":{"schema_version":1,"intent":"HOLD","orders":[]}}'
    )

    assert plan["intent"] == "hold"


def test_parse_execution_plan_normalizes_real_maintain_cash_hold_plan():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "decision": {
                "type": "hold",
                "from": "USD",
                "to": "USD",
                "reason_brief": "maintain cash after risk check",
            },
            "execution_plan": {
                "schema_version": "1.0",
                "intent": "maintain_cash",
                "orders": [],
                "constraints": {
                    "cash_buffer_pct": 1.0,
                    "max_affordable_after_prior_sells": 0,
                },
            },
        }
    )

    plan = strategy_module.parse_execution_plan_from_decision_summary(raw_summary)

    assert plan["schema_version"] == 1
    assert plan["intent"] == "hold"
    assert plan["orders"] == []


def test_parse_execution_plan_accepts_float_one_schema_version():
    strategy_module, _strategy_class = load_strategy_module()

    plan = strategy_module.parse_execution_plan_from_decision_summary(
        '{"execution_plan":{"schema_version":1.0,"intent":"hold","orders":[]}}'
    )

    assert plan["schema_version"] == 1


def test_parse_execution_plan_rejects_direct_root_plan_without_execution_plan_wrapper():
    strategy_module, _strategy_class = load_strategy_module()

    with pytest.raises(ValueError, match="decision summary JSON must include execution_plan"):
        strategy_module.parse_execution_plan_from_decision_summary('{"schema_version":1,"intent":"hold","orders":[]}')


def test_parse_execution_plan_rejects_missing_orders_key():
    strategy_module, _strategy_class = load_strategy_module()

    with pytest.raises(ValueError, match="execution_plan orders are required"):
        strategy_module.parse_execution_plan_from_decision_summary(
            '{"execution_plan":{"schema_version":1,"intent":"hold","constraints":{}}}'
        )


def test_parse_execution_plan_rejects_hold_with_orders():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = """
    {
      "execution_plan": {
        "schema_version": 1,
        "intent": "hold",
        "orders": [
          {
            "sequence": 1,
            "symbol": "GLD",
            "side": "buy",
            "quantity_mode": "max_affordable_cash"
          }
        ],
        "constraints": {}
      }
    }
    """

    with pytest.raises(ValueError, match="hold intent cannot include orders"):
        strategy_module.parse_execution_plan_from_decision_summary(raw_summary)


def test_parse_execution_plan_rejects_duplicate_order_sequences():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = """
    {
      "execution_plan": {
        "schema_version": 1,
        "intent": "rotate",
        "orders": [
          {
            "sequence": 1,
            "symbol": "VNQ",
            "side": "sell",
            "quantity_mode": "current_position"
          },
          {
            "sequence": 1,
            "symbol": "GLD",
            "side": "buy",
            "quantity_mode": "max_affordable_after_prior_sells"
          }
        ],
        "constraints": {}
      }
    }
    """

    with pytest.raises(ValueError, match="duplicate order sequence"):
        strategy_module.parse_execution_plan_from_decision_summary(raw_summary)


def test_parse_execution_plan_rejects_missing_required_order_fields():
    strategy_module, _strategy_class = load_strategy_module()

    with pytest.raises(ValueError, match="missing required order field: symbol"):
        strategy_module.parse_execution_plan_from_decision_summary(
            '{"execution_plan":{"schema_version":1,"intent":"enter_position","orders":[{"sequence":1,"side":"buy","quantity_mode":"max_affordable_cash"}],"constraints":{}}}'
        )


@pytest.mark.parametrize("symbol", [None, 123])
def test_parse_execution_plan_rejects_non_string_order_symbol(symbol):
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "execution_plan": {
                "schema_version": 1,
                "intent": "enter_position",
                "orders": [
                    {
                        "sequence": 1,
                        "action": "submit_order",
                        "symbol": symbol,
                        "side": "buy",
                        "quantity_mode": "max_affordable_cash",
                        "order_type": "market",
                    }
                ],
                "constraints": {},
            }
        }
    )

    with pytest.raises(ValueError, match="order symbol must be a string"):
        strategy_module.parse_execution_plan_from_decision_summary(raw_summary)


def test_parse_execution_plan_rejects_missing_schema_version():
    strategy_module, _strategy_class = load_strategy_module()

    with pytest.raises(ValueError, match="execution_plan schema_version is required."):
        strategy_module.parse_execution_plan_from_decision_summary(
            '{"execution_plan":{"intent":"hold","orders":[],"constraints":{}}}'
        )


@pytest.mark.parametrize("schema_version", [True, 1.5, "1.5", "v1"])
def test_parse_execution_plan_rejects_non_integer_schema_version(schema_version):
    strategy_module, _strategy_class = load_strategy_module()

    raw_summary = json.dumps(
        {
            "execution_plan": {
                "schema_version": schema_version,
                "intent": "hold",
                "orders": [],
                "constraints": {},
            }
        }
    )

    with pytest.raises(ValueError, match="unsupported execution_plan schema_version"):
        strategy_module.parse_execution_plan_from_decision_summary(raw_summary)


def test_parse_execution_plan_rejects_explicit_null_orders():
    strategy_module, _strategy_class = load_strategy_module()

    with pytest.raises(ValueError, match="execution_plan orders must be a list."):
        strategy_module.parse_execution_plan_from_decision_summary(
            '{"execution_plan":{"schema_version":1,"intent":"hold","orders":null,"constraints":{}}}'
        )


def test_parse_execution_plan_rejects_explicit_non_object_constraints():
    strategy_module, _strategy_class = load_strategy_module()

    with pytest.raises(ValueError, match="execution_plan constraints must be an object."):
        strategy_module.parse_execution_plan_from_decision_summary(
            '{"execution_plan":{"schema_version":1,"intent":"hold","orders":[],"constraints":[]}}'
        )


@pytest.mark.parametrize("quantity", [None, "not-a-number", "NaN", "Infinity"])
def test_parse_execution_plan_rejects_invalid_shares_quantity_with_value_error(quantity):
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "execution_plan": {
                "schema_version": 1,
                "intent": "enter_position",
                "orders": [
                    {
                        "sequence": 1,
                        "action": "submit_order",
                        "symbol": "GLD",
                        "side": "buy",
                        "quantity": quantity,
                        "quantity_mode": "shares",
                        "order_type": "market",
                    }
                ],
                "constraints": {},
            }
        }
    )

    with pytest.raises(ValueError, match="quantity (is required|must be positive)"):
        strategy_module.parse_execution_plan_from_decision_summary(raw_summary)


def test_initialize_creates_three_agent_growth_decision_execution_workflow(monkeypatch):
    _strategy_module, strategy_class = load_strategy_module()
    monkeypatch.setenv("AI_TRADING_TEAM_MODEL", "test-model")
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()

    assert [agent["name"] for agent in agent_manager.created] == [
        "growth_agent",
        "decision_agent",
        "execution_agent",
    ]
    assert [agent["allow_trading"] for agent in agent_manager.created] == [
        False,
        False,
        True,
    ]
    assert strategy.sleeptime == "1D"


def test_strategy_prompts_do_not_reference_removed_workflow_concepts(monkeypatch):
    strategy_module, strategy_class = load_strategy_module()
    monkeypatch.setenv("AI_TRADING_TEAM_MODEL", "test-model")
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()
    strategy.on_trading_iteration()

    prompt_text = inspect.getsource(strategy_module)
    prompt_text += json.dumps(agent_manager.created)
    for agent in agent_manager._agents.values():
        prompt_text += json.dumps(agent.calls)

    assert_removed_concepts_absent(prompt_text)


def test_on_trading_iteration_hands_off_context_in_order():
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    for name in ("growth_agent", "decision_agent", "execution_agent"):
        agent_manager._agents[name] = RecordingAgent(name, agent_manager.summaries)
    agent_manager.summaries["decision_agent"] = (
        '{"decision":{"type":"buy","from":"USD","to":"GLD","reason_brief":"keep this out of execution"},'
        '"execution_plan":{"schema_version":1,"intent":"enter_position","orders":[{"sequence":1,'
        '"action":"submit_order","symbol":"GLD","side":"buy","quantity":null,'
        '"quantity_mode":"max_affordable_cash","order_type":"market"}],"constraints":{}}}'
        '\nRESULT: remove me'
    )
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.on_trading_iteration()

    assert [agent.name for agent in agent_manager._agents.values() if agent.calls] == [
        "growth_agent",
        "decision_agent",
        "execution_agent",
    ]

    growth_context = agent_manager["growth_agent"].calls[0]["context"]
    decision_context = agent_manager["decision_agent"].calls[0]["context"]
    execution_context = agent_manager["execution_agent"].calls[0]["context"]

    assert growth_context == {
        "date": "2026-04-07",
        "universe": ["SPY", "QQQ", "TLT"],
    }
    assert decision_context["date"] == "2026-04-07"
    assert decision_context["universe"] == ["SPY", "QQQ", "TLT"]
    assert decision_context["growth_report"] == "growth_agent summary"
    assert set(decision_context) == {"date", "universe", "growth_report"}

    assert execution_context["date"] == "2026-04-07"
    assert set(execution_context) == {"date", "execution_plan"}
    assert execution_context["execution_plan"]["orders"][0]["symbol"] == "GLD"
    serialized_execution_context = json.dumps(execution_context)
    assert "trading_plan" not in serialized_execution_context
    assert "growth_report" not in serialized_execution_context
    assert "RESULT:" not in serialized_execution_context
    assert "reason_brief" not in serialized_execution_context
    assert_removed_concepts_absent(json.dumps([growth_context, decision_context, execution_context]))


def test_invalid_decision_summary_blocks_execution_agent(capsys):
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    for name in ("growth_agent", "decision_agent", "execution_agent"):
        agent_manager._agents[name] = RecordingAgent(name, agent_manager.summaries)
    agent_manager.summaries["decision_agent"] = "RESULT: no json here"
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.on_trading_iteration()

    assert agent_manager["growth_agent"].calls
    assert agent_manager["decision_agent"].calls
    assert agent_manager["execution_agent"].calls == []
    assert strategy._last_execution_plan_error == "No JSON object found in decision summary."
    captured = capsys.readouterr()
    assert "Execution plan blocked" in captured.out
    assert "No JSON object found in decision summary" in captured.out


def test_decision_prompt_requests_structured_execution_plan():
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    for name in ("growth_agent", "decision_agent", "execution_agent"):
        agent_manager._agents[name] = RecordingAgent(name)
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.on_trading_iteration()

    decision_prompt = agent_manager["decision_agent"].calls[0]["task_prompt"]
    decision_prompt_lower = decision_prompt.lower()
    for field in (
        "decision.type",
        "decision.from",
        "decision.to",
        "decision.reason_brief",
        "schema_version",
        "intent",
        "execution_plan.orders",
        "constraints",
        "sequence",
        "quantity_mode",
        "max_affordable_after_prior_sells",
        "cash_buffer_pct",
    ):
        assert field in decision_prompt_lower

    assert_sequence_side_relationship(decision_prompt_lower, 1, "sell")
    assert_sequence_side_relationship(decision_prompt_lower, 2, "buy")
    for rotate_plan_fragment in (
        "decision.from",
        "decision.to",
        "max_affordable_after_prior_sells",
    ):
        assert rotate_plan_fragment in decision_prompt_lower

    for strict_phrase in (
        "return only one valid json object",
        "do not include markdown",
        "do not include result text",
        "do not include prose after the json",
    ):
        assert strict_phrase in decision_prompt_lower

    for prompt_phrase in (
        "required top-level execution_plan fields are schema_version, intent, and orders",
        "constraints is optional and defaults apply",
        "each order must include sequence, symbol, side, and quantity_mode",
        "optional order fields include action, quantity, asset_type, cash_buffer_pct, order_type, "
        "time_in_force, limit_price, stop_price, stop_limit_price, trail_price, and trail_percent",
        "defaults apply",
        "legacy aliases are optional",
    ):
        assert prompt_phrase in decision_prompt_lower

    assert "quantity_basis" not in decision_prompt_lower

    for misleading_phrase in (
        "execution_plan must include schema_version, intent, orders, and constraints",
        "execution_plan must include execution_plan.mode",
        "execution_plan.execution_constraints",
        "must include sequence, symbol, side, and quantity_basis",
        "using max_affordable_after_prior_sells for the buy quantity_basis",
    ):
        assert misleading_phrase not in decision_prompt_lower

    for old_field in (
        "current_position_assessment",
        "exit_actions",
        "entry_actions",
        "do_not_trade_if",
    ):
        assert old_field not in decision_prompt


def test_execution_prompt_treats_execution_plan_as_authoritative():
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    for name in ("growth_agent", "decision_agent", "execution_agent"):
        agent_manager._agents[name] = RecordingAgent(name)
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()
    strategy.on_trading_iteration()

    execution_agent_created = [
        created for created in agent_manager.created if created["name"] == "execution_agent"
    ]
    assert len(execution_agent_created) == 1

    prompt_text = json.dumps(execution_agent_created[0])
    prompt_text += json.dumps(agent_manager["execution_agent"].calls)
    prompt_text = prompt_text.lower().replace('\\"', '"')

    for required_phrase in (
        "execution_plan.orders",
        "decision.reason_brief",
        "human context",
        "do not re-rank",
        "do not substitute",
        "execution-level blockers",
        "sequence number",
        "max_affordable_after_prior_sells",
        "cash_buffer_pct",
    ):
        assert required_phrase in prompt_text
    for required_pattern in (
        r"(execution_plan(?:\.orders)?.{0,100}(authoritative|source of truth|control|drive)|"
        r"(authoritative|source of truth|control|drive).{0,100}execution_plan(?:\.orders)?)",
        r"sequence.{0,80}(order|ordering|ascending|submitted order|in sequence)",
        r"(do not|must not|never).{0,80}(substitute|replace|swap).{0,80}symbol",
        r"(only|solely).{0,80}(execution-level|execution level).{0,80}(blocker|blockers)",
        r"each.{0,80}sequence.{0,80}(submitted|blocked)",
    ):
        assert re.search(required_pattern, prompt_text, re.DOTALL)


def test_prompts_frame_strategy_as_neutral_relative_strength_account_management(monkeypatch):
    _strategy_module, strategy_class = load_strategy_module()
    monkeypatch.setenv("AI_TRADING_TEAM_MODEL", "test-model")
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()
    strategy.on_trading_iteration()

    prompt_text = json.dumps(agent_manager.created)
    for agent in agent_manager._agents.values():
        prompt_text += json.dumps(agent.calls)
    prompt_text = prompt_text.lower().replace('\\"', '"')

    for required_phrase in (
        "relative-strength account management",
        "do not assume any etf is the default holding",
        "do not favor the current holding merely because it is already held",
        "rank the universe from current evidence",
        "more attractive than the current holding",
        "you cannot place orders, but you must still make a clear research recommendation",
        "you cannot place orders, but you must produce an actionable trading plan",
        "if the account holds only cash or a cash-like position",
        "do not treat no-trade as the default answer",
        "trading costs and weak evidence matter, but they should not override",
    ):
        assert required_phrase in prompt_text

    assert "do not assume qqq is the default" not in prompt_text
    assert "you are read-only" not in prompt_text
    for forbidden_phrase in (
        "rotation test",
        "execution capability test",
        "exit capability",
        "capability test",
        "testing whether",
    ):
        assert forbidden_phrase not in prompt_text


def test_benchmark_runner_import_does_not_require_backtesting_stack(monkeypatch):
    sys.modules.pop("scripts.run_ai_trading_team_examples_benchmark", None)
    real_import = __import__

    def fail_optional_backtesting_import(name, *args, **kwargs):
        if name in {"lumibot.backtesting", "lumibot.entities"}:
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fail_optional_backtesting_import)

    benchmark = importlib.import_module("scripts.run_ai_trading_team_examples_benchmark")

    assert "growth-execution-test" in benchmark.STRATEGIES


def test_benchmark_runner_registers_growth_execution_test_strategy():
    _strategy_module, strategy_class = load_strategy_module()
    from scripts.run_ai_trading_team_examples_benchmark import STRATEGIES

    assert STRATEGIES["growth-execution-test"] is strategy_class
