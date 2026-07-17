import importlib
import inspect
import json
import sys
from datetime import datetime
from types import SimpleNamespace

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

    def create(self, **kwargs):
        self.created.append(kwargs)
        agent = RecordingAgent(kwargs["name"])
        self._agents[kwargs["name"]] = agent
        return agent

    def __getitem__(self, name):
        return self._agents[name]


class RecordingAgent:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def run(self, *, task_prompt, context):
        self.calls.append({"task_prompt": task_prompt, "context": context})
        return SimpleNamespace(summary=f"{self.name} summary")


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
        agent_manager._agents[name] = RecordingAgent(name)
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
    assert execution_context["universe"] == ["SPY", "QQQ", "TLT"]
    assert execution_context["growth_report"] == "growth_agent summary"
    assert execution_context["trading_plan"] == "decision_agent summary"
    assert set(execution_context) == {
        "date",
        "universe",
        "growth_report",
        "trading_plan",
    }
    assert_removed_concepts_absent(json.dumps([growth_context, decision_context, execution_context]))


def test_decision_prompt_requests_structured_exit_and_entry_plan():
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    for name in ("growth_agent", "decision_agent", "execution_agent"):
        agent_manager._agents[name] = RecordingAgent(name)
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.on_trading_iteration()

    decision_prompt = agent_manager["decision_agent"].calls[0]["task_prompt"]
    for field in (
        "plan_type",
        "target_symbol",
        "current_position_assessment",
        "exit_actions",
        "entry_actions",
        "do_not_trade_if",
    ):
        assert field in decision_prompt


def test_prompts_frame_strategy_as_relative_strength_rotation_test(monkeypatch):
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
        "relative-strength rotation test",
        "do not assume qqq is the default",
        "materially outperforms the current holding",
        'plan_type="rotate"',
        'side="sell"',
        'side="buy"',
        "sell or reduce the current holding first",
    ):
        assert required_phrase in prompt_text


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
