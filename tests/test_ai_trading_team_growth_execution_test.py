import importlib
import inspect
import json
import re
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


def assert_sequence_side_relationship(prompt_text, sequence, side):
    sequence_pattern = rf"sequence[\"']?\s*[:=]?\s*[\"']?{sequence}[\"']?"
    side_pattern = rf"(?:side|action)[\"']?\s*[:=]?\s*[\"']?{side}[\"']?"
    pattern = rf"({sequence_pattern}.{{0,120}}{side_pattern}|{side_pattern}.{{0,120}}{sequence_pattern})"
    assert re.search(pattern, prompt_text, re.DOTALL), (
        f"expected sequence {sequence} to be structurally associated with {side}"
    )


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
    assert execution_context["trading_plan"] == "decision_agent summary"
    assert set(execution_context) == {
        "date",
        "universe",
        "trading_plan",
    }
    assert_removed_concepts_absent(json.dumps([growth_context, decision_context, execution_context]))


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
        "execution_plan.mode",
        "execution_plan.orders",
        "execution_plan.execution_constraints",
        "sequence",
        "quantity_basis",
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
