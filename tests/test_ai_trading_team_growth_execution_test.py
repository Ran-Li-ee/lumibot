import importlib
import inspect
import json
import re
import sys
from datetime import datetime
from decimal import Decimal
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
        if isinstance(summary, list):
            summary = summary[min(len(self.calls) - 1, len(summary) - 1)]
        return SimpleNamespace(
            summary=summary,
            tool_calls=[SimpleNamespace(tool_name=tool_name) for tool_name in self.tool_calls.get(self.name, [])],
        )


def load_strategy_module():
    module = importlib.import_module("lumibot.example_strategies.ai_trading_team_growth_execution_test")
    return module, module.AITradingTeamGrowthExecutionTestStrategy


def make_strategy_with_agent_manager(strategy_class, agent_manager):
    strategy = object.__new__(strategy_class)
    strategy.agents = agent_manager
    strategy.parameters = {"universe": ["SPY", "QQQ", "TLT"]}
    strategy.get_datetime = lambda: datetime(2026, 4, 7, 9, 30)
    strategy.get_cash = lambda: 100000.0
    strategy.get_portfolio_value = lambda: 100000.0
    strategy.get_last_price = lambda symbol: 100.0
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


def created_tool_names(created_agent):
    return {getattr(tool, "name", "") for tool in created_agent.get("tools", [])}


def test_parse_execution_plan_extracts_clean_plan_from_result_text():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = (
        '{"decision":{"type":"buy","from":"USD","to":"GLD","reason_brief":"analysis stays out of execution"},'
        '"execution_plan":{"schema_version":1,"intent":"enter_position","orders":[{"sequence":1,'
        '"action":"submit_order","symbol":"GLD","side":"buy","quantity":438,'
        '"quantity_mode":"shares","quantity_source":"account_portfolio_and_market_last_price",'
        '"order_type":"market"}],"constraints":{}}}'
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
                "quantity": 438.0,
                "quantity_mode": "shares",
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
            "quantity": 438,
            "quantity_mode": "shares",
            "order_type": "market"
          },
          {
            "sequence": 1,
            "action": "submit_order",
            "symbol": "VNQ",
            "side": "sell",
            "quantity": 217,
            "quantity_mode": "shares",
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
    assert plan["orders"][0]["quantity"] == 217.0
    assert plan["orders"][1]["quantity"] == 438.0
    assert "cash_buffer_pct" not in plan["orders"][0]
    assert "cash_buffer_pct" not in plan["orders"][1]


def test_parse_execution_plan_strips_legacy_buy_cash_buffer():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = """
    {
      "execution_plan": {
        "schema_version": 1,
        "intent": "enter_position",
        "orders": [
          {
            "sequence": 1,
            "symbol": "GLD",
            "side": "buy",
            "quantity": 9,
            "quantity_mode": "shares",
            "cash_buffer_pct": 0.02,
            "order_type": "market"
          }
        ],
        "constraints": {}
      }
    }
    """

    plan = strategy_module.parse_execution_plan_from_decision_summary(raw_summary)

    assert "cash_buffer_pct" not in plan["orders"][0]


def test_parse_execution_plan_handoff_uses_system_owned_allowlist():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "execution_plan": {
                "schema_version": 1,
                "intent": "enter_position",
                "orders": [
                    {
                        "sequence": 1,
                        "symbol": "GLD",
                        "side": "buy",
                        "quantity": 9,
                        "quantity_mode": "shares",
                        "quantity_source": "sized to retain 2% cash",
                        "order_type": "market",
                        "model_note": "retain a cash reserve",
                    }
                ],
                "constraints": {
                    "cash_reserve_pct": 0.02,
                    "notes": "retain 2% cash",
                    "allow_negative_cash": True,
                    "if_any_order_blocked": "continue",
                },
            }
        }
    )

    plan = strategy_module.parse_execution_plan_from_decision_summary(raw_summary)

    assert "quantity_source" not in plan["orders"][0]
    assert "model_note" not in plan["orders"][0]
    assert plan["constraints"] == {
        "allow_negative_cash": False,
        "if_any_order_blocked": "stop_remaining_orders",
    }


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_parse_execution_plan_rejects_fractional_share_quantities(side):
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "execution_plan": {
                "schema_version": 1,
                "intent": "enter_position" if side == "buy" else "close_position",
                "orders": [
                    {
                        "sequence": 1,
                        "symbol": "GLD",
                        "side": side,
                        "quantity": 1.5,
                        "quantity_mode": "shares",
                        "order_type": "market",
                    }
                ],
            }
        }
    )

    with pytest.raises(ValueError, match="whole-share integer"):
        strategy_module.parse_execution_plan_from_decision_summary(raw_summary)


def test_parse_execution_plan_rejects_sell_after_buy():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "execution_plan": {
                "schema_version": 1,
                "intent": "rotate",
                "orders": [
                    {
                        "sequence": 1,
                        "symbol": "SPY",
                        "side": "buy",
                        "quantity": 10,
                        "quantity_mode": "shares",
                        "order_type": "market",
                    },
                    {
                        "sequence": 2,
                        "symbol": "VNQ",
                        "side": "sell",
                        "quantity": 10,
                        "quantity_mode": "shares",
                        "order_type": "market",
                    },
                ],
            }
        }
    )

    with pytest.raises(ValueError, match="sell orders before the buy order"):
        strategy_module.parse_execution_plan_from_decision_summary(raw_summary)


def test_parse_execution_plan_rejects_non_market_order_type():
    strategy_module, _strategy_class = load_strategy_module()
    for order_type in ("limit", "smart_limit", "stop", "stop_limit", "trailing_stop"):
        raw_summary = json.dumps(
            {
                "execution_plan": {
                    "schema_version": 1,
                    "intent": "enter_position",
                    "orders": [
                        {
                            "sequence": 1,
                            "action": "submit_order",
                            "symbol": "SPY",
                            "side": "buy",
                            "quantity": 10,
                            "quantity_mode": "shares",
                            "asset_type": "stock",
                            "order_type": order_type,
                            "time_in_force": "day",
                        }
                    ],
                }
            }
        )

        with pytest.raises(ValueError, match="market-only"):
            strategy_module.parse_execution_plan_from_decision_summary(raw_summary)


def test_parse_execution_plan_rejects_market_order_with_actionable_price_fields():
    strategy_module, _strategy_class = load_strategy_module()
    for field in ("limit_price", "stop_price", "stop_limit_price", "trail_price", "trail_percent"):
        raw_summary = json.dumps(
            {
                "execution_plan": {
                    "schema_version": 1,
                    "intent": "enter_position",
                    "orders": [
                        {
                            "sequence": 1,
                            "action": "submit_order",
                            "symbol": "SPY",
                            "side": "buy",
                            "quantity": 10,
                            "quantity_mode": "shares",
                            "asset_type": "stock",
                            "order_type": "market",
                            field: 95.0,
                            "time_in_force": "day",
                        }
                    ],
                }
            }
        )

        with pytest.raises(ValueError, match=rf"market-only execution_plan must not include {field}"):
            strategy_module.parse_execution_plan_from_decision_summary(raw_summary)


def test_parse_execution_plan_rejects_more_than_one_buy_order():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "execution_plan": {
                "schema_version": 1,
                "intent": "enter_position",
                "orders": [
                    {
                        "sequence": 1,
                        "symbol": "SPY",
                        "side": "buy",
                        "quantity": 1,
                        "quantity_mode": "shares",
                    },
                    {
                        "sequence": 2,
                        "symbol": "QQQ",
                        "side": "buy",
                        "quantity": 1,
                        "quantity_mode": "shares",
                    },
                ],
            }
        }
    )

    with pytest.raises(ValueError, match="at most one buy order"):
        strategy_module.parse_execution_plan_from_decision_summary(raw_summary)


@pytest.mark.parametrize(
    "quantity_mode",
    [
        "current_position",
        "full_position",
        "max_affordable_cash",
        "max_affordable_after_prior_sells",
    ],
)
def test_parse_execution_plan_rejects_semantic_quantity_modes_for_executable_orders(quantity_mode):
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "execution_plan": {
                "schema_version": 1,
                "intent": "rotate",
                "orders": [
                    {
                        "sequence": 1,
                        "symbol": "QQQ",
                        "side": "sell",
                        "quantity": None,
                        "quantity_mode": quantity_mode,
                    }
                ],
                "constraints": {},
            }
        }
    )

    with pytest.raises(ValueError, match="executable orders must use shares quantity_mode"):
        strategy_module.parse_execution_plan_from_decision_summary(raw_summary)


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
    assert "cash_buffer_pct" not in plan["constraints"]


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


def test_parse_execution_plan_derives_intent_from_real_buy_decision_type_when_plan_intent_is_sentence():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "decision": {
                "type": "buy",
                "from": "USD",
                "to": "QQQ",
                "reason_brief": "QQQ is the strongest growth ETF candidate.",
            },
            "execution_plan": {
                "schema_version": "1.0",
                "intent": "deploy cash into the strongest growth ETF candidate using a full-investment stance",
                "orders": [
                    {
                        "sequence": 1,
                        "symbol": "QQQ",
                        "side": "buy",
                        "quantity": 438,
                        "quantity_mode": "shares",
                        "order_type": "market",
                    }
                ],
                "constraints": {
                    "allow_negative_cash": False,
                    "if_any_order_blocked": "stop_remaining_orders",
                },
            },
        }
    )

    plan = strategy_module.parse_execution_plan_from_decision_summary(raw_summary)

    assert plan["intent"] == "enter_position"
    assert plan["orders"][0]["symbol"] == "QQQ"


def test_parse_execution_plan_rejects_invalid_intent_without_valid_decision_type():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "decision": {
                "type": "invest",
                "from": "USD",
                "to": "QQQ",
                "reason_brief": "Invalid decision type should not normalize intent.",
            },
            "execution_plan": {
                "schema_version": 1,
                "intent": "deploy cash into a fund",
                "orders": [
                    {
                        "sequence": 1,
                        "symbol": "QQQ",
                        "side": "buy",
                        "quantity_mode": "max_affordable_after_prior_sells",
                    }
                ],
                "constraints": {},
            },
        }
    )

    with pytest.raises(ValueError, match="unsupported execution_plan intent"):
        strategy_module.parse_execution_plan_from_decision_summary(raw_summary)


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
            "quantity": 217,
            "quantity_mode": "shares"
          },
          {
            "sequence": 1,
            "symbol": "GLD",
            "side": "buy",
            "quantity": 438,
            "quantity_mode": "shares"
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
    prompt_text += json.dumps(agent_manager.created, default=str)
    for agent in agent_manager._agents.values():
        prompt_text += json.dumps(agent.calls, default=str)

    assert_removed_concepts_absent(prompt_text)


def test_on_trading_iteration_hands_off_context_in_order():
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    for name in ("growth_agent", "decision_agent", "execution_agent"):
        agent_manager._agents[name] = RecordingAgent(name, agent_manager.summaries, agent_manager.tool_calls)
    agent_manager.summaries["decision_agent"] = (
        '{"decision":{"type":"buy","from":"USD","to":"GLD","reason_brief":"keep this out of execution"},'
        '"execution_plan":{"schema_version":1,"intent":"enter_position","orders":[{"sequence":1,'
        '"action":"submit_order","symbol":"GLD","side":"buy","quantity":980,'
        '"quantity_mode":"shares","order_type":"market"}],"constraints":{}}}'
        '\nRESULT: remove me'
    )
    agent_manager.tool_calls["decision_agent"] = ["account_positions", "account_portfolio", "market_last_price"]
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
    assert execution_context["execution_plan"]["orders"][0]["quantity_mode"] == "shares"
    assert execution_context["execution_plan"]["orders"][0]["quantity"] == 980.0
    execution_task_prompt = agent_manager["execution_agent"].calls[0]["task_prompt"]
    serialized_execution_context = json.dumps(execution_context)
    assert "trading_plan" not in serialized_execution_context
    assert "growth_report" not in serialized_execution_context
    for forbidden_buffer_reference in ("cash_buffer_pct", "0.02", "2%"):
        assert forbidden_buffer_reference not in execution_task_prompt
        assert forbidden_buffer_reference not in serialized_execution_context
    assert "RESULT:" not in serialized_execution_context
    assert "reason_brief" not in serialized_execution_context
    assert_removed_concepts_absent(json.dumps([growth_context, decision_context, execution_context]))


def test_non_hold_decision_without_account_tool_evidence_blocks_execution_agent(capsys):
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    for name in ("growth_agent", "decision_agent", "execution_agent"):
        agent_manager._agents[name] = RecordingAgent(name, agent_manager.summaries, agent_manager.tool_calls)
    agent_manager.summaries["decision_agent"] = (
        '{"decision":{"type":"buy","from":"USD","to":"GLD","reason_brief":"numeric but ungrounded"},'
        '"execution_plan":{"schema_version":1,"intent":"enter_position","orders":[{"sequence":1,'
        '"action":"submit_order","symbol":"GLD","side":"buy","quantity":438,'
        '"quantity_mode":"shares","order_type":"market"}],"constraints":{}}}'
    )
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.on_trading_iteration()

    assert agent_manager["growth_agent"].calls
    assert agent_manager["decision_agent"].calls
    assert agent_manager["execution_agent"].calls == []
    assert "decision agent must call account tools" in strategy._last_execution_plan_error
    captured = capsys.readouterr()
    assert "Execution plan blocked" in captured.out


def test_parse_execution_plan_rejects_previous_limit_vnq_plan():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "execution_plan": {
                "schema_version": 1,
                "intent": "enter_position",
                "orders": [
                    {
                        "sequence": 1,
                        "symbol": "VNQ",
                        "side": "buy",
                        "quantity": 1029.0,
                        "quantity_mode": "shares",
                        "order_type": "limit",
                        "limit_price": 95.32,
                    }
                ],
            }
        }
    )

    with pytest.raises(ValueError, match="market-only"):
        strategy_module.parse_execution_plan_from_decision_summary(raw_summary)


@pytest.mark.parametrize("quantity", [97.0, 99.0])
def test_decision_buy_sizing_accepts_inclusive_tolerance_boundaries(quantity):
    strategy_module, _strategy_class = load_strategy_module()
    strategy = SimpleNamespace(
        get_cash=lambda: 100.0,
        get_portfolio_value=lambda: 100.0,
        get_last_price=lambda symbol: 1.0,
    )
    plan = {
        "intent": "enter_position",
        "orders": [
            {
                "sequence": 1,
                "symbol": "SPY",
                "side": "buy",
                "quantity": quantity,
                "order_type": "market",
            }
        ],
        "constraints": {"allow_negative_cash": False},
    }

    strategy_module.validate_decision_buy_sizing(strategy, plan)


@pytest.mark.parametrize(
    ("quantity", "expected_ratio"),
    [
        (992.0, 0.008),
        (966.0, 0.034),
    ],
)
def test_decision_buy_sizing_allows_quantities_outside_former_cash_buffer_tolerance(
    quantity,
    expected_ratio,
):
    strategy_module, _strategy_class = load_strategy_module()
    strategy = SimpleNamespace(
        get_cash=lambda: 100.0,
        get_portfolio_value=lambda: 100.0,
        get_last_price=lambda symbol: 0.1,
    )
    plan = {
        "intent": "enter_position",
        "orders": [
            {
                "sequence": 1,
                "symbol": "SPY",
                "side": "buy",
                "quantity": quantity,
                "order_type": "market",
            }
        ],
        "constraints": {"allow_negative_cash": False},
    }

    strategy_module.validate_decision_buy_sizing(strategy, plan)

    projected_ratio = (100.0 - quantity * 0.1) / 100.0
    assert projected_ratio == pytest.approx(expected_ratio)


@pytest.mark.parametrize("quantity", [425.0, 426.0, 427.0])
def test_decision_buy_sizing_accepts_any_quantity_inside_tolerance_band(quantity):
    strategy_module, _strategy_class = load_strategy_module()
    strategy = SimpleNamespace(
        get_cash=lambda: 100000.0,
        get_portfolio_value=lambda: 100000.0,
        get_last_price=lambda symbol: 229.789993,
    )
    plan = {
        "intent": "enter_position",
        "orders": [
            {
                "sequence": 1,
                "symbol": "GLD",
                "side": "buy",
                "quantity": quantity,
                "order_type": "market",
                "limit_price": None,
                "stop_price": None,
                "stop_limit_price": None,
            }
        ],
        "constraints": {"allow_negative_cash": False},
    }

    strategy_module.validate_decision_buy_sizing(strategy, plan)


def test_decision_buy_sizing_includes_prior_sell_proceeds():
    strategy_module, _strategy_class = load_strategy_module()
    prices = {"VNQ": 100.0, "FXI": 50.0}
    strategy = SimpleNamespace(
        get_cash=lambda: 100.0,
        get_portfolio_value=lambda: 10100.0,
        get_last_price=lambda symbol: prices[str(symbol)],
        get_positions=lambda: [
            SimpleNamespace(asset=SimpleNamespace(symbol="VNQ"), quantity=100.0),
        ],
    )
    plan = {
        "intent": "rotate",
        "orders": [
            {
                "sequence": 1,
                "symbol": "VNQ",
                "side": "sell",
                "quantity": 100.0,
                "order_type": "market",
                "limit_price": None,
                "stop_price": None,
                "stop_limit_price": None,
            },
            {
                "sequence": 2,
                "symbol": "FXI",
                "side": "buy",
                "quantity": 197.0,
                "order_type": "market",
                "limit_price": None,
                "stop_price": None,
                "stop_limit_price": None,
            },
        ],
        "constraints": {"allow_negative_cash": False},
    }

    strategy_module.validate_decision_buy_sizing(strategy, plan)


@pytest.mark.parametrize(
    ("held_quantity", "sell_quantity", "error_code"),
    [
        (0.0, 1.0, "DECISION_SELL_POSITION_REQUIRED"),
        (10.0, 11.0, "DECISION_SELL_EXCEEDS_LONG_POSITION"),
    ],
)
def test_decision_buy_sizing_rejects_nonexistent_or_oversized_sell(
    held_quantity,
    sell_quantity,
    error_code,
):
    strategy_module, _strategy_class = load_strategy_module()
    positions = []
    if held_quantity:
        positions.append(SimpleNamespace(asset=SimpleNamespace(symbol="VNQ"), quantity=held_quantity))
    strategy = SimpleNamespace(
        get_cash=lambda: 100.0,
        get_portfolio_value=lambda: 1100.0,
        get_last_price=lambda symbol: 10.0,
        get_positions=lambda: positions,
    )
    plan = {
        "intent": "close_position",
        "orders": [
            {
                "sequence": 1,
                "symbol": "VNQ",
                "side": "sell",
                "quantity": sell_quantity,
                "order_type": "market",
            }
        ],
        "constraints": {"allow_negative_cash": False},
    }

    with pytest.raises(ValueError, match=error_code):
        strategy_module.validate_decision_buy_sizing(strategy, plan)


def test_parse_execution_plan_rejects_limit_sell_when_proceeds_would_fund_later_buy():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "execution_plan": {
                "schema_version": 1,
                "intent": "rotate",
                "orders": [
                    {
                        "sequence": 1,
                        "symbol": "VNQ",
                        "side": "sell",
                        "quantity": 100.0,
                        "quantity_mode": "shares",
                        "order_type": "limit",
                        "limit_price": 100.0,
                    },
                    {
                        "sequence": 2,
                        "symbol": "FXI",
                        "side": "buy",
                        "quantity": 197.0,
                        "quantity_mode": "shares",
                        "order_type": "market",
                    },
                ],
            }
        }
    )

    with pytest.raises(ValueError, match="market-only"):
        strategy_module.parse_execution_plan_from_decision_summary(raw_summary)


def test_execution_order_price_for_market_order_uses_last_price():
    strategy_module, _strategy_class = load_strategy_module()
    strategy = SimpleNamespace(get_last_price=lambda symbol: 100.0)

    price = strategy_module._execution_order_price(
        strategy,
        {
            "symbol": "SPY",
            "order_type": "market",
            "limit_price": None,
            "stop_price": None,
            "stop_limit_price": None,
        },
    )

    assert price == 100.0


def test_decision_buy_sizing_handles_near_integer_decimal_floor_boundary():
    strategy_module, _strategy_class = load_strategy_module()
    strategy = SimpleNamespace(
        get_cash=lambda: 100.0,
        get_portfolio_value=lambda: 100.0,
        get_last_price=lambda symbol: 0.1,
        get_positions=lambda: [],
    )
    plan = {
        "intent": "enter_position",
        "orders": [
            {
                "sequence": 1,
                "symbol": "SPY",
                "side": "buy",
                "quantity": 980.0,
                "order_type": "market",
            }
        ],
        "constraints": {"allow_negative_cash": False},
    }

    strategy_module.validate_decision_buy_sizing(strategy, plan)


@pytest.mark.parametrize("portfolio_value", [-100.0, 0.0])
def test_decision_sizing_does_not_require_positive_portfolio_for_sell_only_plan(
    portfolio_value,
):
    strategy_module, _strategy_class = load_strategy_module()
    strategy = SimpleNamespace(
        get_cash=lambda: 100.0,
        get_portfolio_value=lambda: portfolio_value,
        get_last_price=lambda symbol: 10.0,
        get_positions=lambda: [
            SimpleNamespace(asset=SimpleNamespace(symbol="SPY"), quantity=10.0),
        ],
    )
    plan = {
        "intent": "close_position",
        "orders": [
            {
                "sequence": 1,
                "symbol": "SPY",
                "side": "sell",
                "quantity": 10.0,
                "order_type": "market",
            }
        ],
        "constraints": {"allow_negative_cash": False},
    }

    strategy_module.validate_decision_buy_sizing(strategy, plan)


def test_decision_sizing_preserves_decimal_price_precision_at_lower_boundary():
    strategy_module, _strategy_class = load_strategy_module()
    precise_price = Decimal("1.0000000000000000000000000001")
    strategy = SimpleNamespace(
        get_cash=lambda: Decimal("100"),
        get_portfolio_value=lambda: Decimal("100"),
        get_last_price=lambda symbol: precise_price,
    )
    plan = {
        "intent": "enter_position",
        "orders": [
            {
                "sequence": 1,
                "symbol": "SPY",
                "side": "buy",
                "quantity": 99.0,
                "order_type": "market",
            }
        ],
        "constraints": {"allow_negative_cash": False},
    }

    assert strategy_module._decision_order_price(strategy, plan["orders"][0]) == precise_price
    strategy_module.validate_decision_buy_sizing(strategy, plan)


def _decision_summary(
    quantity,
    *,
    decision_type="buy",
    intent="enter_position",
    symbol="GLD",
    side="buy",
    order_type="market",
    reason_brief="ranked first",
):
    order = {
        "sequence": 1,
        "action": "submit_order",
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "quantity_mode": "shares",
        "order_type": order_type,
    }
    if order_type in {"limit", "smart_limit"}:
        order["limit_price"] = 100
    return json.dumps(
        {
            "decision": {
                "type": decision_type,
                "from": "USD",
                "to": symbol,
                "reason_brief": reason_brief,
            },
            "execution_plan": {
                "schema_version": 1,
                "intent": intent,
                "orders": [order],
                "constraints": {},
            },
        },
        separators=(",", ":"),
    )


def _rotate_decision_summary(sell_quantity, buy_quantity, *, reason_brief="rotate to leader"):
    return json.dumps(
        {
            "decision": {
                "type": "rotate",
                "from": "GLD",
                "to": "SPY",
                "reason_brief": reason_brief,
            },
            "execution_plan": {
                "schema_version": 1,
                "intent": "rotate",
                "orders": [
                    {
                        "sequence": 1,
                        "action": "submit_order",
                        "symbol": "GLD",
                        "side": "sell",
                        "quantity": sell_quantity,
                        "quantity_mode": "shares",
                        "order_type": "market",
                    },
                    {
                        "sequence": 2,
                        "action": "submit_order",
                        "symbol": "SPY",
                        "side": "buy",
                        "quantity": buy_quantity,
                        "quantity_mode": "shares",
                        "order_type": "market",
                    },
                ],
                "constraints": {},
            },
        },
        separators=(",", ":"),
    )


def _workflow_strategy(strategy_class, decision_summaries):
    agent_manager = RecordingAgentManager()
    for name in ("growth_agent", "decision_agent", "execution_agent"):
        agent_manager._agents[name] = RecordingAgent(
            name,
            agent_manager.summaries,
            agent_manager.tool_calls,
        )
    agent_manager.summaries["decision_agent"] = decision_summaries
    agent_manager.tool_calls["decision_agent"] = [
        "account_positions",
        "account_portfolio",
        "market_last_price",
    ]
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    return strategy, agent_manager


def test_in_band_initial_decision_runs_once_and_reaches_execution():
    _strategy_module, strategy_class = load_strategy_module()
    strategy, agent_manager = _workflow_strategy(strategy_class, [_decision_summary(980)])

    strategy.on_trading_iteration()

    assert len(agent_manager["decision_agent"].calls) == 1
    assert len(agent_manager["execution_agent"].calls) == 1


def test_former_out_of_band_decision_reaches_execution_without_retry():
    _strategy_module, strategy_class = load_strategy_module()
    strategy, agent_manager = _workflow_strategy(
        strategy_class,
        [_decision_summary(995), _decision_summary(980)],
    )

    strategy.on_trading_iteration()

    assert len(agent_manager["decision_agent"].calls) == 1
    assert len(agent_manager["execution_agent"].calls) == 1


def test_structural_decision_error_does_not_retry():
    _strategy_module, strategy_class = load_strategy_module()
    strategy, agent_manager = _workflow_strategy(
        strategy_class,
        ["RESULT: malformed", _decision_summary(980)],
    )

    strategy.on_trading_iteration()

    assert len(agent_manager["decision_agent"].calls) == 1
    assert agent_manager["execution_agent"].calls == []


def test_execution_handoff_keeps_execution_agent_free_of_buffer_and_retry_material():
    _strategy_module, strategy_class = load_strategy_module()
    strategy, agent_manager = _workflow_strategy(
        strategy_class,
        [_decision_summary(995), _decision_summary(980)],
    )

    strategy.on_trading_iteration()

    execution_call = agent_manager["execution_agent"].calls[0]
    serialized_execution_material = json.dumps(execution_call).lower()
    for forbidden in (
        "0.02",
        "2%",
        "1%",
        "3%",
        "cash reserve",
        "reserve target",
        "tolerance",
        "previous_decision_output",
        "decision_sizing_diagnostics",
        "retry",
        "correction",
    ):
        assert forbidden not in serialized_execution_material
    assert execution_call["context"] == {
        "date": "2026-04-07",
        "execution_plan": {
            "schema_version": 1,
            "intent": "enter_position",
            "orders": [
                {
                    "sequence": 1,
                    "action": "submit_order",
                    "symbol": "GLD",
                    "asset_type": "stock",
                    "side": "buy",
                        "quantity": 995.0,
                    "quantity_mode": "shares",
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
        },
    }


def test_execution_cash_safety_allows_positive_cash_below_decision_buffer():
    strategy_module, _strategy_class = load_strategy_module()
    strategy = SimpleNamespace(
        get_cash=lambda: 100000.0,
        get_portfolio_value=lambda: 100000.0,
        get_last_price=lambda symbol: 229.789993,
    )
    plan = {
        "intent": "enter_position",
        "orders": [
            {
                "sequence": 1,
                "symbol": "GLD",
                "side": "buy",
                "quantity": 434.0,
                "order_type": "market",
                "limit_price": None,
                "stop_price": None,
                "stop_limit_price": None,
            }
        ],
        "constraints": {"allow_negative_cash": False},
    }

    strategy_module.validate_execution_plan_cash_safety(strategy, plan)


def test_parse_execution_plan_rejects_market_order_stop_price_before_cash_safety():
    strategy_module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "execution_plan": {
                "schema_version": 1,
                "intent": "enter_position",
                "orders": [
                    {
                        "sequence": 1,
                        "symbol": "SPY",
                        "side": "buy",
                        "quantity": 11.0,
                        "quantity_mode": "shares",
                        "order_type": "market",
                        "stop_price": 50.0,
                    }
                ],
            }
        }
    )

    with pytest.raises(ValueError, match="market-only execution_plan must not include stop_price"):
        strategy_module.parse_execution_plan_from_decision_summary(raw_summary)


def test_execution_plan_cash_safety_blocks_negative_cash_even_without_buffer():
    strategy_module, _strategy_class = load_strategy_module()
    strategy = SimpleNamespace(
        get_cash=lambda: 1000.0,
        get_portfolio_value=lambda: 1000.0,
        get_last_price=lambda symbol: 100.0,
    )
    plan = {
        "intent": "enter_position",
        "orders": [
            {
                "sequence": 1,
                "symbol": "SPY",
                "side": "buy",
                "quantity": 11.0,
                "order_type": "market",
                "limit_price": None,
                "stop_price": None,
                "stop_limit_price": None,
            }
        ],
        "constraints": {"allow_negative_cash": False},
    }

    with pytest.raises(ValueError, match="NEGATIVE_CASH_NOT_ALLOWED"):
        strategy_module.validate_execution_plan_cash_safety(strategy, plan)


def test_invalid_decision_summary_blocks_execution_agent(capsys):
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    for name in ("growth_agent", "decision_agent", "execution_agent"):
        agent_manager._agents[name] = RecordingAgent(name, agent_manager.summaries, agent_manager.tool_calls)
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

    strategy.initialize()
    strategy.on_trading_iteration()

    decision_agent_created = [
        created for created in agent_manager.created if created["name"] == "decision_agent"
    ][0]
    decision_prompt = json.dumps(decision_agent_created, default=str) + json.dumps(
        agent_manager["decision_agent"].calls,
        default=str,
    )
    decision_prompt_lower = decision_prompt.lower().replace('\\"', '"')
    for field in (
        "decision.type",
        "decision.from",
        "decision.to",
        "decision.reason_brief",
        "schema_version",
        "intent",
        "execution_plan.orders",
        "sequence",
        "quantity_mode",
    ):
        assert field in decision_prompt_lower

    assert_sequence_side_relationship(decision_prompt_lower, 1, "sell")
    assert_sequence_side_relationship(decision_prompt_lower, 2, "buy")
    for rotate_plan_fragment in (
        "decision.from",
        "decision.to",
    ):
        assert rotate_plan_fragment in decision_prompt_lower

    for prompt_phrase in (
        "before a non-hold decision, call account_positions and account_portfolio",
        "for buy sizing, call market_last_price and use a conservative market sizing price",
        "use numeric whole-share quantities",
        "do not use full_position, current_position, max_affordable_cash, or max_affordable_after_prior_sells",
        "calculate the share quantity from account tool output",
        'all executable orders must use order_type "market"',
        "do not output limit_price, stop_price, stop_limit_price, trail_price, or trail_percent",
        "maximum buy quantity must be no greater than floor(0.98 * available_cash_after_prior_sells / sizing_price)",
        "return only the final numeric whole-share quantity",
        "use a conservative market sizing price based on available price evidence",
        "it may be higher than market_last_price in daily backtests",
        "use the 98% cash rule only as an internal sizing rule",
        "do not output cash_buffer_pct or any buffer field in execution_plan",
        "compare the holding against the strongest candidate",
        "choose rotate only when the candidate is clearly stronger",
        "planned sell and buy quantities can be expressed as executable numeric share orders",
        'quantity_mode must be exactly "shares"',
    ):
        assert prompt_phrase in decision_prompt_lower
    for forbidden_cash_buffer_phrase in (
        "target a cash reserve",
        "0.02",
        "2%",
        "1%",
        "3%",
        "unless a larger cash buffer",
        "quantity * sizing_price <= 0.98 * available cash after prior sells",
        "do not assume market_last_price is the actual fill price",
        "do not include cash_buffer_pct in the execution order",
    ):
        assert forbidden_cash_buffer_phrase not in decision_prompt_lower
    for forbidden_bounded_price_phrase in (
        "optional bounded-price fields include",
        "choose order_type before calculating quantity",
        "choose sizing_price based on order_type",
        "for limit buys",
        "for stop_limit buys",
        "buy orders must use market, limit",
        "limit order",
        "stop order",
        "stop-limit order",
        "trailing-stop order",
        "smart_limit",
        "do not use stop or trailing_stop",
        "bounded execution price",
    ):
        assert forbidden_bounded_price_phrase not in decision_prompt_lower

    for strict_phrase in (
        "return only one valid json object",
        "do not include markdown",
        "result text",
        "prose after the json",
    ):
        assert strict_phrase in decision_prompt_lower

    for prompt_phrase in (
        "execution_plan must include schema_version, intent, and orders",
        "intent must be one of hold, enter_position, rotate, reduce_position, close_position",
        "each executable order must include sequence, symbol, side, quantity_mode, quantity, asset_type, order_type, and time_in_force",
    ):
        assert prompt_phrase in decision_prompt_lower

    assert "quantity_basis" not in decision_prompt_lower

    for misleading_phrase in (
        "execution_plan must include schema_version, intent, orders, and constraints",
        "execution_plan must include execution_plan.mode",
        "execution_plan.execution_constraints",
        "must include sequence, symbol, side, and quantity_basis",
        "using max_affordable_after_prior_sells for the buy quantity_basis",
        "using max_affordable_after_prior_sells for the buy quantity_mode",
        "each order must include sequence, symbol, side, and quantity_mode",
    ):
        assert misleading_phrase not in decision_prompt_lower

    for old_field in (
        "current_position_assessment",
        "exit_actions",
        "entry_actions",
        "do_not_trade_if",
    ):
        assert old_field not in decision_prompt


def test_growth_prompt_is_research_only_and_summary_first():
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    for name in ("growth_agent", "decision_agent", "execution_agent"):
        agent_manager._agents[name] = RecordingAgent(name)
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()
    strategy.on_trading_iteration()

    growth_agent_created = [
        created for created in agent_manager.created if created["name"] == "growth_agent"
    ][0]
    prompt_text = json.dumps(growth_agent_created, default=str) + json.dumps(
        agent_manager["growth_agent"].calls,
        default=str,
    )
    prompt_lower = prompt_text.lower().replace('\\"', '"')

    for required_phrase in (
        "rank the etf universe",
        "use computed summary metrics as default evidence",
        "identify the strongest candidate",
        "do not place orders",
        "do not calculate final executable share quantities",
        "lack of order permission is not a recommendation to hold cash",
        "preferred account action",
        "do not exhaustively load raw history tables",
    ):
        assert required_phrase in prompt_lower

    for forbidden_phrase in (
        "execution_plan",
        "schema_version",
        "98% cash rule",
        "cash_buffer_pct",
        "orders_submit_order",
    ):
        assert forbidden_phrase not in prompt_lower


def test_growth_decision_execution_agents_receive_distinct_tool_surfaces():
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()

    created = {agent["name"]: agent for agent in agent_manager.created}

    assert created["growth_agent"]["include_builtin_tools"] is False
    assert created_tool_names(created["growth_agent"]) == {
        "account_positions",
        "account_portfolio",
        "market_load_history_table",
        "market_load_history_tables_summary",
        "duckdb_query",
    }

    assert created["decision_agent"]["include_builtin_tools"] is False
    assert created_tool_names(created["decision_agent"]) == {
        "account_positions",
        "account_portfolio",
        "market_last_price",
    }

    assert created["execution_agent"]["include_builtin_tools"] is False
    assert created_tool_names(created["execution_agent"]) == {
        "account_positions",
        "account_portfolio",
        "market_last_price",
        "orders_open_orders",
        "orders_submit_order",
        "orders_confirm_order",
    }
    assert "orders_confirm_order" not in created_tool_names(created["growth_agent"])
    assert "orders_confirm_order" not in created_tool_names(created["decision_agent"])


def test_execution_prompt_treats_execution_plan_as_authoritative():
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    for name in ("growth_agent", "decision_agent", "execution_agent"):
        agent_manager._agents[name] = RecordingAgent(name, agent_manager.summaries, agent_manager.tool_calls)
    agent_manager.summaries["decision_agent"] = (
        '{"decision":{"type":"buy","from":"USD","to":"GLD","reason_brief":"keep this out of execution"},'
        '"execution_plan":{"schema_version":1,"intent":"enter_position","orders":[{"sequence":1,'
        '"action":"submit_order","symbol":"GLD","side":"buy","quantity":980,'
        '"quantity_mode":"shares","order_type":"market"}],"constraints":{}}}'
    )
    agent_manager.tool_calls["decision_agent"] = [
        "account_positions",
        "account_portfolio",
        "market_last_price",
    ]
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()
    strategy.on_trading_iteration()

    execution_agent_created = [
        created for created in agent_manager.created if created["name"] == "execution_agent"
    ]
    assert len(execution_agent_created) == 1

    prompt_text = json.dumps(execution_agent_created[0], default=str)
    prompt_text += json.dumps(agent_manager["execution_agent"].calls, default=str)
    prompt_text = prompt_text.lower().replace('\\"', '"')

    for required_phrase in (
        "execution_plan.orders",
        "execution_plan.orders as authoritative",
        "do not re-rank",
        "do not substitute",
        "do not add, remove, replace, or reorder orders",
        "execution-level blockers",
        "sequence order",
        "submit the explicit numeric share quantities",
        "do not compute semantic sizing",
        "orders_confirm_order",
        "after every orders_submit_order",
        "returned identifier",
        "can_continue=true",
        "can_continue=false",
        "stop all remaining orders",
        "use only market orders",
        "if any execution_plan order is not a market order",
    ):
        assert required_phrase in prompt_text
    for task_prompt_phrase in (
        "execute only the provided execution_plan object",
        "inspect account state, open orders, positions, and latest prices",
        "submit only execution_plan.orders",
        "after every submit, confirm that same order with orders_confirm_order",
        "preserve sequence order",
        "submitted, confirmed, or blocked",
    ):
        assert task_prompt_phrase in prompt_text
    for task3_confirmation_phrase in (
        "confirmed sequence",
        "prior sell order is no longer open",
        "cash or buying power has updated",
        "do not submit the dependent buy order",
    ):
        assert task3_confirmation_phrase not in prompt_text
    for forbidden_buffer_reference in ("cash_buffer_pct", "0.02", "2%"):
        assert forbidden_buffer_reference not in prompt_text
    for forbidden_research_reference in (
        "decision.reason_brief",
        "growth_report",
        "relative-strength account management",
        "market_load_history_tables_summary",
        "market_load_history_table",
        "duckdb_query",
        "98% cash rule",
        "rank the etf universe",
    ):
        assert forbidden_research_reference not in prompt_text
    for required_pattern in (
        r"(execution_plan(?:\.orders)?.{0,100}(authoritative|source of truth|control|drive)|"
        r"(authoritative|source of truth|control|drive).{0,100}execution_plan(?:\.orders)?)",
        r"sequence.{0,80}(order|ordering|ascending|submitted order|in sequence)",
        r"(do not|must not|never).{0,80}(substitute|replace|swap).{0,80}symbol",
        r"(only|solely).{0,80}(execution-level|execution level).{0,80}(blocker|blockers)",
        r"each.{0,80}sequence.{0,80}(submitted|blocked)",
    ):
        assert re.search(required_pattern, prompt_text, re.DOTALL)


def test_execution_agent_uses_minimal_base_system_prompt_mode():
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()

    execution_agent_created = [
        created for created in agent_manager.created if created["name"] == "execution_agent"
    ]
    assert len(execution_agent_created) == 1
    assert strategy_class._execution_agent_base_system_prompt_mode == "execution_minimal"
    assert execution_agent_created[0]["base_system_prompt_mode"] == "execution_minimal"


def test_prompts_frame_strategy_as_neutral_relative_strength_account_management(monkeypatch):
    _strategy_module, strategy_class = load_strategy_module()
    monkeypatch.setenv("AI_TRADING_TEAM_MODEL", "test-model")
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()
    strategy.on_trading_iteration()

    prompt_text = json.dumps(agent_manager.created, default=str)
    for agent in agent_manager._agents.values():
        prompt_text += json.dumps(agent.calls, default=str)
    prompt_text = prompt_text.lower().replace('\\"', '"')

    for required_phrase in (
        "relative-strength account management",
        "do not assume any etf is the default holding",
        "do not favor the current holding merely because it is already held",
        "rank the etf universe",
        "rank the etf universe from current evidence",
        "compare the holding against the strongest candidate",
        "candidate is clearly stronger",
        "do not place orders",
        "research recommendation",
        "strict execution_plan",
        "do not place orders and do not perform broad etf research again",
        "if the account holds only cash or a cash-like position",
    ):
        assert required_phrase in prompt_text

    assert "do not assume qqq is the default" not in prompt_text
    assert "you are read-only" not in prompt_text
    assert "do not treat no-trade as the default answer" not in prompt_text
    assert "trading costs and weak evidence matter, but they should not override" not in prompt_text
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


def test_benchmark_runner_saves_native_backtest_ui_artifacts(tmp_path, monkeypatch):
    import argparse
    import types

    benchmark = importlib.import_module("scripts.run_ai_trading_team_examples_benchmark")
    captured = {}

    class FakeStrategyInstance:
        def get_positions(self, include_cash_positions=False):
            return []

    class FakeStrategy:
        @classmethod
        def run_backtest(cls, **kwargs):
            captured.update(kwargs)
            return {"total_return": 0.01}, FakeStrategyInstance()

    monkeypatch.setitem(benchmark.STRATEGIES, "growth-execution-test", FakeStrategy)
    fake_backtesting = types.ModuleType("lumibot.backtesting")
    fake_backtesting.YahooDataBacktesting = object
    fake_entities = types.ModuleType("lumibot.entities")

    class FakeAsset:
        class AssetType:
            STOCK = "stock"
            FOREX = "forex"

        def __init__(self, symbol, asset_type):
            self.symbol = symbol
            self.asset_type = asset_type

    fake_entities.Asset = FakeAsset
    monkeypatch.setitem(sys.modules, "lumibot.backtesting", fake_backtesting)
    monkeypatch.setitem(sys.modules, "lumibot.entities", fake_entities)
    args = argparse.Namespace(
        start="2024-09-05",
        end="2024-09-06",
        budget=100000,
        agent_run_timeout_seconds=1800,
        max_run_attempts=3,
    )

    payload = benchmark._run_one_strategy("growth-execution-test", args, str(tmp_path))

    run_dir = tmp_path / "growth-execution-test"
    assert payload["status"] == "passed"
    assert captured["analyze_backtest"] is True
    assert captured["show_plot"] is True
    assert captured["save_tearsheet"] is True
    assert captured["show_tearsheet"] is False
    assert captured["plot_file_html"] == str(run_dir / "growth_execution_test_account_curve.html")
    assert captured["tearsheet_file"] == str(run_dir / "growth_execution_test_tearsheet.html")
    assert captured["tearsheet_metrics_file"] == str(run_dir / "growth_execution_test_tearsheet_metrics.json")
