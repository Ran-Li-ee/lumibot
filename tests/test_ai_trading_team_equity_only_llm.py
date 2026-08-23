import importlib
import json
from datetime import date, datetime
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
        tool_results = self.manager.tool_results.get(self.name, [])
        return SimpleNamespace(summary=summary, tool_calls=[], tool_results=tool_results)


class RecordingRunAgentManager:
    def __init__(self):
        self.created = []
        self.summaries = {}
        self.tool_results = {}
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


def qqq_resolution(
    *,
    as_of_date="2024-09-05",
    mode="strict",
    selected_report_date="2024-06-30",
    selected_filing_date="2024-08-28",
    accession_number="0001752724-24-196011",
    symbols=("MSFT", "AAPL", "NVDA", "AMZN", "META"),
    snapshot_path="C:/cache/qqq_nport_2024-06-30.json",
    source_url="https://www.sec.gov/example.xml",
    symbol_repair=None,
):
    return SimpleNamespace(
        as_of_date=date.fromisoformat(as_of_date),
        mode=mode,
        selected_report_date=date.fromisoformat(selected_report_date),
        selected_filing_date=date.fromisoformat(selected_filing_date),
        accession_number=accession_number,
        symbols=tuple(symbols),
        snapshot_path=Path(snapshot_path),
        source_url=source_url,
        symbol_repair=symbol_repair or {
            "applied": False,
            "raw_count": len(symbols),
            "repaired_count": 0,
            "deduped_count": 0,
            "dropped_count": 0,
            "final_count": len(symbols),
            "aliases": [],
        },
    )


def test_equity_only_target_portfolio_uses_dynamic_constructor_with_fallback_weights():
    module = load_module()

    result = module.equity_only_target_portfolio(
        {
            "basket_id": "equity",
            "status": "active",
            "selected_symbols": ["orcl", "msft", "nvda", "aapl", "amzn"],
            "reason_brief": "Five strongest equity candidates.",
        },
        equity_universe=["AAPL", "AMZN", "MSFT", "NVDA", "ORCL", "TSLA"],
    )

    assert result["portfolio_mode"] == "dynamic_equity"
    assert result["selected_count"] == 5
    assert result["diagnostics"]["fallback_used"] is True
    assert result["target_portfolio"] == [
        {"basket_id": "equity", "symbol": "ORCL", "target_weight": 0.196},
        {"basket_id": "equity", "symbol": "MSFT", "target_weight": 0.196},
        {"basket_id": "equity", "symbol": "NVDA", "target_weight": 0.196},
        {"basket_id": "equity", "symbol": "AAPL", "target_weight": 0.196},
        {"basket_id": "equity", "symbol": "AMZN", "target_weight": 0.196},
    ]
    assert sum(row["target_weight"] for row in result["target_portfolio"]) == pytest.approx(0.98)


def test_equity_only_target_portfolio_accepts_selected_status_synonym_for_dynamic_candidates():
    module = load_module()

    result = module.equity_only_target_portfolio(
        {
            "basket_id": "equity",
            "status": "selected",
            "selected_symbols": ["spy", "orcl", "msft", "aapl", "nvda"],
            "reason_brief": "Five active selections.",
        },
        equity_universe=["SPY", "ORCL", "MSFT", "AAPL", "NVDA", "AMZN"],
    )

    assert result["selected_count"] == 5
    assert [row["symbol"] for row in result["target_portfolio"]] == ["SPY", "ORCL", "MSFT", "AAPL", "NVDA"]
    assert sum(row["target_weight"] for row in result["target_portfolio"]) == pytest.approx(0.98)


def test_validate_execution_plan_symbols_accepts_any_top5_selected_symbol():
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
                },
                {
                    "sequence": 2,
                    "action": "submit_order",
                    "symbol": "MSFT",
                    "asset_type": "stock",
                    "side": "buy",
                    "quantity": 5,
                    "quantity_mode": "shares",
                    "order_type": "market",
                    "time_in_force": "day",
                }
            ],
        },
        {
            "basket_id": "equity",
            "status": "selected",
            "selected_symbols": ["SPY", "ORCL", "MSFT", "AAPL", "NVDA"],
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


def test_qqq_historical_equity_strategy_defaults_to_weekly_strict_without_changing_fixed_baseline():
    module = load_module()

    fixed = module.AITradingTeamEquityOnlyLLMStrategy
    qqq = module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy

    assert fixed.parameters["run_frequency"] == "monthly"
    assert fixed.parameters["basket_universes"]["equity"] == EXPECTED_EQUITY_UNIVERSE
    assert "qqq_universe_mode" not in fixed.parameters

    assert qqq.parameters["run_frequency"] == "weekly"
    assert qqq.parameters["weekly_run_weekday"] == "MON"
    assert qqq.parameters["weekly_holiday_policy"] == "first_open_trading_day"
    assert qqq.parameters["qqq_universe_mode"] == "strict"
    assert qqq.parameters["qqq_universe_data_dir"] is None


def test_qqq_symbol_repair_aliases_and_deduplicates_preserving_order():
    qqq_nport = importlib.import_module("lumibot.tools.universe.qqq_nport")

    symbols, repair = qqq_nport.repair_qqq_symbols(
        ["AAPL", "CPW", "CHKP", "MRVLEUR", "MRVL", "TRI4EUR", "STXN", "", "AAPL"]
    )

    assert symbols == ("AAPL", "CHKP", "MRVL", "TRI", "STX")
    assert repair == {
        "applied": True,
        "raw_count": 9,
        "repaired_count": 4,
        "deduped_count": 3,
        "dropped_count": 1,
        "final_count": 5,
        "aliases": [
            {"from": "CPW", "to": "CHKP"},
            {"from": "MRVLEUR", "to": "MRVL"},
            {"from": "TRI4EUR", "to": "TRI"},
            {"from": "STXN", "to": "STX"},
        ],
    }


def test_qqq_symbol_repair_reports_noop_for_clean_symbols():
    qqq_nport = importlib.import_module("lumibot.tools.universe.qqq_nport")

    symbols, repair = qqq_nport.repair_qqq_symbols(["MSFT", "AAPL", "NVDA"])

    assert symbols == ("MSFT", "AAPL", "NVDA")
    assert repair == {
        "applied": False,
        "raw_count": 3,
        "repaired_count": 0,
        "deduped_count": 0,
        "dropped_count": 0,
        "final_count": 3,
        "aliases": [],
    }


def test_resolve_qqq_snapshot_returns_repaired_symbols_and_metadata(tmp_path):
    qqq_nport = importlib.import_module("lumibot.tools.universe.qqq_nport")
    normalized = tmp_path / "normalized"
    normalized.mkdir()
    snapshot = {
        "report_date": "2026-03-31",
        "filing_date": "2026-05-28",
        "accession_number": "0001067839-26-000024",
        "source_url": "https://www.sec.gov/example.xml",
        "holdings": [
            {"symbol": "AAPL"},
            {"symbol": "TRI4EUR"},
            {"symbol": "TRI"},
            {"symbol": "STXN"},
            {"symbol": "CPW"},
        ],
    }
    (normalized / "qqq_nport_2026-03-31_0001067839-26-000024.json").write_text(
        json.dumps(snapshot),
        encoding="utf-8",
    )

    resolution = qqq_nport.resolve_qqq_snapshot(
        "2026-06-01",
        mode="strict",
        data_dir=tmp_path,
    )

    assert resolution.symbols == ("AAPL", "TRI", "STX", "CHKP")
    assert resolution.symbol_repair == {
        "applied": True,
        "raw_count": 5,
        "repaired_count": 3,
        "deduped_count": 1,
        "dropped_count": 0,
        "final_count": 4,
        "aliases": [
            {"from": "TRI4EUR", "to": "TRI"},
            {"from": "STXN", "to": "STX"},
            {"from": "CPW", "to": "CHKP"},
        ],
    }


def test_equity_only_target_portfolio_rejects_inactive_report():
    module = load_module()

    with pytest.raises(ValueError, match="equity report must be active"):
        module.equity_only_target_portfolio(
            {
                "basket_id": "equity",
                "status": "inactive",
                "selected_symbols": ["ORCL", "MSFT", "NVDA", "AAPL", "AMZN"],
            },
            equity_universe=["ORCL", "MSFT", "NVDA", "AAPL", "AMZN"],
        )


def test_equity_only_target_portfolio_rejects_missing_selected_symbols():
    module = load_module()

    with pytest.raises(ValueError, match="selected_symbols must be a list"):
        module.equity_only_target_portfolio(
            {"basket_id": "equity", "status": "active", "selected_symbol": "ORCL"},
            equity_universe=["ORCL", "MSFT", "NVDA", "AAPL", "AMZN"],
        )


def test_equity_only_target_portfolio_deduplicates_selected_symbols_preserving_order():
    module = load_module()

    result = module.equity_only_target_portfolio(
        {
            "basket_id": "equity",
            "status": "active",
            "selected_symbols": ["ORCL", "MSFT", "NVDA", "AAPL", "ORCL"],
        },
        equity_universe=["ORCL", "MSFT", "NVDA", "AAPL", "AMZN"],
    )

    assert [row["symbol"] for row in result["target_portfolio"]] == ["ORCL", "MSFT", "NVDA", "AAPL"]


def test_equity_only_target_portfolio_rejects_too_few_selected_symbols():
    module = load_module()

    with pytest.raises(ValueError, match="selected_symbols must contain at least 3 symbols"):
        module.equity_only_target_portfolio(
            {
                "basket_id": "equity",
                "status": "active",
                "selected_symbols": ["ORCL", "MSFT"],
            },
            equity_universe=["ORCL", "MSFT", "NVDA", "AAPL", "AMZN"],
        )


def test_equity_only_target_portfolio_rejects_symbol_outside_universe():
    module = load_module()

    with pytest.raises(ValueError, match="selected equity symbols must be in equity universe: GLD"):
        module.equity_only_target_portfolio(
            {
                "basket_id": "equity",
                "status": "active",
                "selected_symbols": ["GLD", "ORCL", "MSFT", "NVDA", "AAPL"],
            },
            equity_universe=["ORCL", "MSFT", "NVDA", "AAPL", "AMZN"],
        )


def test_equity_only_target_portfolio_rejects_non_equity_report():
    module = load_module()

    with pytest.raises(ValueError, match="basket_id must be 'equity'"):
        module.equity_only_target_portfolio(
            {
                "basket_id": "commodity",
                "status": "active",
                "selected_symbols": ["GLD", "ORCL", "MSFT", "NVDA", "AAPL"],
            },
            equity_universe=["GLD", "ORCL", "MSFT", "NVDA", "AAPL"],
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


def test_equity_news_tool_description_is_stock_candidate_scoped():
    module = load_module()
    strategy = make_strategy(module.AITradingTeamEquityOnlyLLMStrategy)

    strategy.initialize()

    equity_agent = created_agent_config(strategy, "equity_basket_agent")
    news_tool = next(tool for tool in equity_agent["tools"] if tool.name == "alpaca_news")
    bound_news_tool = news_tool.binder(strategy, strategy.agents)
    descriptions = [
        news_tool.description.lower(),
        bound_news_tool.description.lower(),
    ]

    for description in descriptions:
        for required in (
            "leading stock candidates",
            "basket_symbols",
            "do not broaden",
        ):
            assert required in description

        for forbidden in (
            "spy",
            "qqq",
            "tlt",
            "gld",
            "commodity",
            "commodities",
            "bond",
            "bonds",
        ):
            assert forbidden not in description


def test_equity_agent_system_prompt_is_equity_only_rank_first_and_conditional_news():
    module = load_module()
    strategy = make_strategy(module.AITradingTeamEquityOnlyLLMStrategy)

    strategy.initialize()

    equity_agent = created_agent_config(strategy, "equity_basket_agent")
    prompt = equity_agent["system_prompt"].lower()

    for required in (
        "equity-only",
        "credible stock candidates",
        "deterministic constructor",
        "do not calculate exact per-symbol target weights",
        "market_load_history_tables_summary",
        "evidence interpretation policy",
        "momentum and trend quality as primary selection evidence",
        "not purely volatility-driven",
        "timing and leadership confirmation",
        "supporting evidence, not as a standalone reason",
        "do not average all ranking groups equally",
        "do not select a stock solely because it leads one ranking list",
        "do not treat any single ranking or combined score as the final answer",
        "alpaca_news",
        "leading candidates",
        "close, conflicting, or uncertain",
        "strict json",
        "do not place orders",
        "cannot place orders or size trades",
    ):
        assert required in prompt

    for forbidden in (
        "quadrant",
        "macro regime",
        "commodity",
        "tips",
        "nominal bond",
        "defensive posture",
        "safest",
        "defensive",
        "cyclical",
        "speculative",
        "leading group",
        "composite_score",
        "duckdb",
        "optimize weights",
        "choose exactly five",
        "equal target weights",
    ):
        assert forbidden not in prompt


def test_equity_prompts_remove_fixed_five_equal_weight_assumptions():
    helpers = importlib.import_module("lumibot.example_strategies.ai_trading_team_equity_only_helpers")

    prompts = [
        helpers.equity_basket_agent_system_prompt("AAPL, MSFT, NVDA"),
        helpers.qqq_historical_equity_basket_agent_system_prompt("AAPL, MSFT, NVDA"),
        helpers.equity_basket_agent_task_prompt(),
    ]

    for prompt in prompts:
        lower_prompt = prompt.lower()
        for forbidden in (
            "choose exactly five",
            "exactly five unique",
            "five selected stocks equal",
            "equal target weights",
            "target_weight must be 1.0",
        ):
            assert forbidden not in lower_prompt

        for required in (
            "credible candidates",
            "between 3 and 10",
            "deterministic constructor",
            "do not calculate exact per-symbol target weights",
        ):
            assert required in lower_prompt


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
            "selected_symbols": ["ORCL", "MSFT", "NVDA", "AAPL", "AMZN"],
            "reason_brief": "Five strongest setup names.",
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
    assert "first call" in task_prompt
    assert "length=252" in task_prompt
    assert "timestep='day'" in task_prompt
    assert "top_n=10" in task_prompt
    assert "candidate_summary_limit=25" in task_prompt
    assert "evidence interpretation policy" in task_prompt
    assert "selected_symbols must contain between 3 and 10 unique symbols" in task_prompt
    assert "deterministic constructor" in task_prompt
    assert "do not calculate exact per-symbol target weights" in task_prompt
    assert "avoid selecting a stock supported by only one evidence group unless" in task_prompt
    assert "alpaca_news" in task_prompt
    assert "close, conflicting, or uncertain" in task_prompt
    assert "leading candidates only" in task_prompt
    assert "candidate_symbols must copy the assigned basket_symbols exactly" in task_prompt
    assert "return exactly one strict json object" in task_prompt

    for forbidden in (
        "leading group",
        "composite_score",
        "defensive",
        "cyclical",
        "speculative",
        "optimize weights",
        "duckdb",
        "target_weight",
    ):
        assert forbidden not in task_prompt


def test_qqq_historical_strategy_resolves_snapshot_and_passes_metadata_to_equity_agent(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy)
    strategy.get_datetime = lambda: datetime(2024, 9, 5, 9, 30)
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": ["MSFT", "AAPL", "NVDA", "AMZN", "META"],
            "selected_symbols": ["MSFT", "AAPL", "NVDA", "AMZN", "META"],
            "reason_brief": "Five QQQ constituents have the strongest evidence.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."
    resolver_calls = []

    def fake_resolve_qqq_snapshot(as_of_date, *, mode="strict", data_dir=None):
        resolver_calls.append({"as_of_date": as_of_date, "mode": mode, "data_dir": data_dir})
        return qqq_resolution(symbols=("MSFT", "AAPL", "NVDA", "AMZN", "META"))

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
            "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []},
            "warnings": [],
        }

    monkeypatch.setattr(module, "resolve_qqq_snapshot", fake_resolve_qqq_snapshot)
    monkeypatch.setattr(module, "target_portfolio_to_execution_plan", fake_target_portfolio_to_execution_plan)

    strategy.on_trading_iteration()

    assert resolver_calls == [{"as_of_date": "2024-09-05", "mode": "strict", "data_dir": None}]
    equity_call = strategy.agents["equity_basket_agent"].calls[0]
    assert equity_call["context"]["basket_symbols"] == ["MSFT", "AAPL", "NVDA", "AMZN", "META"]
    assert equity_call["context"]["universe_source"] == {
        "type": "qqq_nport",
        "mode": "strict",
        "as_of_date": "2024-09-05",
        "selected_report_date": "2024-06-30",
        "selected_filing_date": "2024-08-28",
        "accession_number": "0001752724-24-196011",
        "holding_count": 5,
        "snapshot_path": str(Path("C:/cache/qqq_nport_2024-06-30.json")),
        "source_url": "https://www.sec.gov/example.xml",
        "symbol_repair": {
            "applied": False,
            "raw_count": 5,
            "repaired_count": 0,
            "deduped_count": 0,
            "dropped_count": 0,
            "final_count": 5,
            "aliases": [],
        },
    }


def test_qqq_historical_strategy_supports_prototype_mode_data_dir_and_symbol_normalization(
    monkeypatch, tmp_path
):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy)
    strategy.parameters["qqq_universe_mode"] = "prototype"
    strategy.parameters["qqq_universe_data_dir"] = str(tmp_path)
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": ["AAPL", "MSFT", "NVDA", "AMZN", "META"],
            "selected_symbols": ["AAPL", "MSFT", "NVDA", "AMZN", "META"],
            "reason_brief": "Five symbols are selected.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."
    resolver_calls = []

    def fake_resolve_qqq_snapshot(as_of_date, *, mode="strict", data_dir=None):
        resolver_calls.append({"as_of_date": as_of_date, "mode": mode, "data_dir": data_dir})
        return qqq_resolution(
            mode="prototype",
            selected_report_date="2024-09-30",
            selected_filing_date="2024-11-27",
            symbols=(" aapl ", "", "MSFT", "AAPL", "nvda", "AMZN", "META"),
        )

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
            "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []},
            "warnings": [],
        }

    monkeypatch.setattr(module, "resolve_qqq_snapshot", fake_resolve_qqq_snapshot)
    monkeypatch.setattr(module, "target_portfolio_to_execution_plan", fake_target_portfolio_to_execution_plan)

    strategy.on_trading_iteration()

    assert resolver_calls == [
        {"as_of_date": "2024-09-05", "mode": "prototype", "data_dir": str(tmp_path)}
    ]
    equity_call = strategy.agents["equity_basket_agent"].calls[0]
    assert equity_call["context"]["basket_symbols"] == ["AAPL", "MSFT", "NVDA", "AMZN", "META"]
    assert equity_call["context"]["universe_source"]["mode"] == "prototype"
    assert equity_call["context"]["universe_source"]["holding_count"] == 5


def test_qqq_historical_strategy_passes_repair_metadata_to_equity_agent(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy)
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": ["AAPL", "CHKP", "MRVL", "MSFT", "NVDA"],
            "selected_symbols": ["AAPL", "CHKP", "MRVL", "MSFT", "NVDA"],
            "reason_brief": "Five symbols are selected.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."

    def fake_resolve_qqq_snapshot(as_of_date, *, mode="strict", data_dir=None):
        return qqq_resolution(
            symbols=("AAPL", "CHKP", "MRVL", "MSFT", "NVDA"),
            symbol_repair={
                "applied": True,
                "raw_count": 6,
                "repaired_count": 2,
                "deduped_count": 1,
                "dropped_count": 0,
                "final_count": 5,
                "aliases": [
                    {"from": "CPW", "to": "CHKP"},
                    {"from": "MRVLEUR", "to": "MRVL"},
                ],
            },
        )

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
            "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []},
            "warnings": [],
        }

    monkeypatch.setattr(module, "resolve_qqq_snapshot", fake_resolve_qqq_snapshot)
    monkeypatch.setattr(module, "target_portfolio_to_execution_plan", fake_target_portfolio_to_execution_plan)

    strategy.on_trading_iteration()

    equity_context = strategy.agents["equity_basket_agent"].calls[0]["context"]
    assert equity_context["basket_symbols"] == ["AAPL", "CHKP", "MRVL", "MSFT", "NVDA"]
    assert equity_context["universe_source"]["symbol_repair"] == {
        "applied": True,
        "raw_count": 6,
        "repaired_count": 2,
        "deduped_count": 1,
        "dropped_count": 0,
        "final_count": 5,
        "aliases": [
            {"from": "CPW", "to": "CHKP"},
            {"from": "MRVLEUR", "to": "MRVL"},
        ],
    }


def test_qqq_historical_strategy_blocks_without_fallback_when_snapshot_missing(monkeypatch):
    module = load_module()
    qqq_nport = importlib.import_module("lumibot.tools.universe.qqq_nport")
    strategy = make_running_strategy(module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy)

    def fake_resolve_qqq_snapshot(as_of_date, *, mode="strict", data_dir=None):
        raise qqq_nport.NoSnapshotAvailableError("No QQQ snapshot available")

    monkeypatch.setattr(module, "resolve_qqq_snapshot", fake_resolve_qqq_snapshot)

    strategy.on_trading_iteration()

    assert strategy.agents["equity_basket_agent"].calls == []
    assert strategy.agents["execution_agent"].calls == []
    assert strategy._last_execution_plan_error == "No QQQ snapshot available"
    blocked_event = strategy._scheduled_workflow_events[-1]
    assert blocked_event["date"] == "2024-09-05"
    assert blocked_event["run_frequency"] == "weekly"
    assert blocked_event["weekly_run_weekday"] == "MON"
    assert blocked_event["week_key"] == "2024-W36"
    assert blocked_event["month_key"] == "2024-09"
    assert blocked_event["should_run"] is True
    assert blocked_event["reason"] == "qqq_historical_universe_unavailable"
    assert blocked_event["status"] == "blocked"
    assert blocked_event["error"] == "No QQQ snapshot available"
    assert strategy._scheduled_workflow_attempted_week_keys == set()


def test_qqq_historical_strategy_retries_same_week_after_missing_snapshot(monkeypatch):
    module = load_module()
    qqq_nport = importlib.import_module("lumibot.tools.universe.qqq_nport")
    strategy = make_running_strategy(module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy)
    current_datetime = datetime(2024, 9, 2, 9, 30)
    strategy.get_datetime = lambda: current_datetime
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": ["MSFT", "AAPL", "NVDA", "AMZN", "META"],
            "selected_symbols": ["MSFT", "AAPL", "NVDA", "AMZN", "META"],
            "reason_brief": "Five QQQ constituents have the strongest evidence.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."
    resolver_calls = []

    def fake_resolve_qqq_snapshot(as_of_date, *, mode="strict", data_dir=None):
        resolver_calls.append(as_of_date)
        if as_of_date == "2024-09-02":
            raise qqq_nport.NoSnapshotAvailableError("No QQQ snapshot available")
        return qqq_resolution(as_of_date="2024-09-03", symbols=("MSFT", "AAPL", "NVDA", "AMZN", "META"))

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
            "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []},
            "warnings": [],
        }

    monkeypatch.setattr(module, "resolve_qqq_snapshot", fake_resolve_qqq_snapshot)
    monkeypatch.setattr(module, "target_portfolio_to_execution_plan", fake_target_portfolio_to_execution_plan)

    strategy.on_trading_iteration()
    assert strategy._scheduled_workflow_attempted_week_keys == set()
    assert strategy.agents["equity_basket_agent"].calls == []
    assert len(strategy._scheduled_workflow_events) == 1

    current_datetime = datetime(2024, 9, 3, 9, 30)
    strategy.on_trading_iteration()

    assert resolver_calls == ["2024-09-02", "2024-09-03"]
    assert strategy.agents["equity_basket_agent"].calls
    assert strategy.agents["equity_basket_agent"].calls[0]["context"]["date"] == "2024-09-03"
    assert len(strategy._scheduled_workflow_events) == 2
    assert strategy._scheduled_workflow_events[0]["reason"] == "qqq_historical_universe_unavailable"
    assert strategy._scheduled_workflow_events[0]["status"] == "blocked"
    assert strategy._scheduled_workflow_events[1]["reason"] == "first_observed_after_preferred_weekday"
    assert strategy._scheduled_workflow_events[1]["status"] == "run"
    assert "2024-W36" in strategy._scheduled_workflow_attempted_week_keys


def test_qqq_historical_strategy_rejects_selected_symbol_outside_resolved_universe(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy)
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": ["MSFT", "AAPL", "NVDA", "AMZN", "META"],
            "selected_symbols": ["ORCL", "MSFT", "AAPL", "NVDA", "AMZN"],
            "reason_brief": "ORCL was incorrectly selected.",
        }
    )

    def fake_resolve_qqq_snapshot(as_of_date, *, mode="strict", data_dir=None):
        return qqq_resolution(symbols=("MSFT", "AAPL", "NVDA", "AMZN", "META"))

    monkeypatch.setattr(module, "resolve_qqq_snapshot", fake_resolve_qqq_snapshot)

    strategy.on_trading_iteration()

    assert strategy._last_execution_plan_error == "selected equity symbols must be in equity universe: ORCL"
    assert strategy.agents["execution_agent"].calls == []


def test_qqq_historical_equity_agent_prompt_mentions_historical_constituents_without_weight_bias(monkeypatch):
    module = load_module()
    strategy = make_strategy(module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy)

    strategy.initialize()

    equity_agent = created_agent_config(strategy, "equity_basket_agent")
    prompt = equity_agent["system_prompt"].lower()

    for required in (
        "qqq historical constituent universe",
        "provided basket_symbols",
        "current backtest date",
        "current rank evidence",
        "do not invent sector",
        "do not add symbols",
        "index weight",
        "evidence interpretation policy",
        "momentum and trend quality as primary selection evidence",
        "not purely volatility-driven",
        "timing and leadership confirmation",
        "supporting evidence, not as a standalone reason",
        "do not average all ranking groups equally",
        "do not select a stock solely because it leads one ranking list",
        "do not treat any single ranking or combined score as the final answer",
        "alpaca_news",
        "leading candidates",
        "strict json",
        "do not place orders",
        "cannot place orders or size trades",
    ):
        assert required in prompt

    for forbidden in (
        "automatically high quality",
        "always prefer",
        "buy qqq",
        "safety label",
        "leading group",
        "composite_score",
        "safe or best",
        "safest",
        "defensive",
        "cyclical",
        "speculative",
        "duckdb",
        "optimize weights",
    ):
        assert forbidden not in prompt


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


def test_equity_only_workflow_passes_constructor_target_portfolio_to_planner(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamEquityOnlyLLMStrategy)
    strategy.parameters["basket_universes"] = {
        **strategy.parameters["basket_universes"],
        "equity": ["ORCL", "MSFT", "NVDA", "AAPL", "AMZN", "META"],
    }
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": strategy.parameters["basket_universes"]["equity"],
            "selected_symbols": ["ORCL", "MSFT", "NVDA", "AAPL", "AMZN"],
            "reason_brief": "Five strongest setup names.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."
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

    expected_target_portfolio = [
        {"basket_id": "equity", "symbol": "ORCL", "target_weight": 0.196},
        {"basket_id": "equity", "symbol": "MSFT", "target_weight": 0.196},
        {"basket_id": "equity", "symbol": "NVDA", "target_weight": 0.196},
        {"basket_id": "equity", "symbol": "AAPL", "target_weight": 0.196},
        {"basket_id": "equity", "symbol": "AMZN", "target_weight": 0.196},
    ]
    assert planner_calls == [
        {
            "strategy": strategy,
            "date": "2024-09-05",
            "target_portfolio": expected_target_portfolio,
        }
    ]
    assert strategy._last_dynamic_equity_constructor_result["target_portfolio"] == expected_target_portfolio
    assert strategy._last_dynamic_equity_constructor_result["selected_count"] == 5
    assert sum(row["target_weight"] for row in planner_calls[0]["target_portfolio"]) == pytest.approx(0.98)
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


def test_equity_only_workflow_records_dynamic_constructor_result_for_trace(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamEquityOnlyLLMStrategy)
    strategy.parameters["basket_universes"] = {
        **strategy.parameters["basket_universes"],
        "equity": ["ORCL", "MSFT", "NVDA", "AAPL", "AMZN", "META"],
    }
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": strategy.parameters["basket_universes"]["equity"],
            "selected_symbols": ["ORCL", "MSFT", "NVDA", "AAPL", "AMZN"],
            "reason_brief": "Five strongest setup names.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."

    def fake_target_portfolio_to_execution_plan(strategy_arg, *, date, target_portfolio):
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

    constructor_result = strategy._last_dynamic_equity_constructor_result
    assert constructor_result["portfolio_mode"] == "dynamic_equity"
    assert "diagnostics" in constructor_result
    assert "candidate_scores" in constructor_result["diagnostics"]


def test_equity_only_workflow_uses_market_summary_tool_result_for_constructor(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamEquityOnlyLLMStrategy)
    strategy.parameters["basket_universes"] = {
        **strategy.parameters["basket_universes"],
        "equity": ["ORCL", "MSFT", "NVDA", "AAPL", "AMZN", "META"],
    }
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": strategy.parameters["basket_universes"]["equity"],
            "selected_symbols": ["ORCL", "MSFT", "NVDA", "AAPL", "AMZN"],
            "reason_brief": "Five strongest setup names from the LLM.",
        }
    )
    strategy.agents.tool_results["equity_basket_agent"] = [
        SimpleNamespace(
            tool_name="market_load_history_tables_summary",
            payload={
                "result": {
                    "ranking_limit": 10,
                    "candidate_summary": [
                        {
                            "symbol": "META",
                            "ranking_count": 5,
                            "volatility_20": 0.12,
                            "best_rank_by_group": {
                                "momentum": 1,
                                "trend_quality": 1,
                                "risk_adjusted_momentum": 1,
                                "breakout_near_high": 1,
                                "volume_confirmation": 1,
                            },
                        },
                        {
                            "symbol": "ORCL",
                            "ranking_count": 5,
                            "volatility_20": 0.13,
                            "best_rank_by_group": {
                                "momentum": 2,
                                "trend_quality": 2,
                                "risk_adjusted_momentum": 2,
                                "breakout_near_high": 2,
                                "volume_confirmation": 2,
                            },
                        },
                        {
                            "symbol": "MSFT",
                            "ranking_count": 5,
                            "volatility_20": 0.14,
                            "best_rank_by_group": {
                                "momentum": 3,
                                "trend_quality": 3,
                                "risk_adjusted_momentum": 3,
                                "breakout_near_high": 3,
                                "volume_confirmation": 3,
                            },
                        },
                        {
                            "symbol": "NVDA",
                            "ranking_count": 5,
                            "volatility_20": 0.15,
                            "best_rank_by_group": {
                                "momentum": 4,
                                "trend_quality": 4,
                                "risk_adjusted_momentum": 4,
                                "breakout_near_high": 4,
                                "volume_confirmation": 4,
                            },
                        },
                    ],
                }
            },
        )
    ]
    strategy.agents.summaries["execution_agent"] = "Executed plan."
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
                    "symbol": "META",
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
                        "symbol": "META",
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

    constructor_result = strategy._last_dynamic_equity_constructor_result
    constructor_symbols = [row["symbol"] for row in constructor_result["target_portfolio"]]
    planner_symbols = [row["symbol"] for row in planner_calls[0]["target_portfolio"]]
    assert constructor_result["diagnostics"]["fallback_used"] is False
    assert "META" in constructor_symbols
    assert "META" in planner_symbols
    assert constructor_symbols == planner_symbols
    assert planner_calls[0]["strategy"] is strategy
    assert planner_calls[0]["date"] == "2024-09-05"


def test_equity_only_workflow_validates_execution_symbols_against_constructor_targets(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamEquityOnlyLLMStrategy)
    strategy.parameters["basket_universes"] = {
        **strategy.parameters["basket_universes"],
        "equity": ["ORCL", "MSFT", "NVDA", "AAPL", "AMZN", "META"],
    }
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": strategy.parameters["basket_universes"]["equity"],
            "selected_symbols": ["ORCL", "MSFT", "NVDA", "AAPL", "AMZN"],
            "reason_brief": "Five strongest setup names.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."
    constructor_result = {
        "portfolio_mode": "dynamic_equity",
        "selected_count": 5,
        "target_portfolio": [
            {"basket_id": "equity", "symbol": "ORCL", "target_weight": 0.196},
            {"basket_id": "equity", "symbol": "MSFT", "target_weight": 0.196},
            {"basket_id": "equity", "symbol": "NVDA", "target_weight": 0.196},
            {"basket_id": "equity", "symbol": "AAPL", "target_weight": 0.196},
            {"basket_id": "equity", "symbol": "META", "target_weight": 0.196},
        ],
        "diagnostics": {"fallback_used": False},
    }

    def fake_equity_only_target_portfolio(equity_report, *, equity_universe, market_summary=None):
        assert market_summary is None
        return constructor_result

    def fake_target_portfolio_to_execution_plan(strategy_arg, *, date, target_portfolio):
        assert target_portfolio == constructor_result["target_portfolio"]
        return {
            "schema_version": "1.0",
            "date": date,
            "target_portfolio": target_portfolio,
            "current_vs_target": [
                {
                    "symbol": "META",
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
                        "symbol": "META",
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

    monkeypatch.setattr(module, "equity_only_target_portfolio", fake_equity_only_target_portfolio)
    monkeypatch.setattr(module, "target_portfolio_to_execution_plan", fake_target_portfolio_to_execution_plan)

    strategy.on_trading_iteration()

    assert strategy._last_dynamic_equity_constructor_result == constructor_result
    assert strategy._last_execution_plan_error is None
    execution_agent = strategy.agents["execution_agent"]
    assert len(execution_agent.calls) == 1
    assert execution_agent.calls[0]["context"]["execution_plan"]["orders"][0]["symbol"] == "META"


def test_scheduled_buy_seeds_trailing_stop_state(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamEquityOnlyLLMStrategy)
    strategy.parameters["basket_universes"] = {
        **strategy.parameters["basket_universes"],
        "equity": ["ORCL", "MSFT", "NVDA", "AAPL", "AMZN", "META"],
    }
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": strategy.parameters["basket_universes"]["equity"],
            "selected_symbols": ["ORCL", "MSFT", "NVDA", "AAPL", "AMZN"],
            "reason_brief": "Five strongest setup names.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."

    def fake_target_portfolio_to_execution_plan(strategy_arg, *, date, target_portfolio):
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

    assert strategy._equity_trailing_stop_position_state["ORCL"] == {
        "entry_date": "2024-09-05",
        "peak_close": 100.0,
        "last_check_date": "2024-09-05",
        "last_check_price": 100.0,
    }


def test_trailing_stop_executes_and_skips_weekly_equity_agent(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamEquityOnlyLLMStrategy)
    strategy.parameters["run_frequency"] = "daily"
    strategy._run_frequency = "daily"
    strategy.agents.summaries["execution_agent"] = "Executed trailing stop."

    def fake_trailing_stop_to_execution_plan(strategy_arg, *, date, trailing_stop_pct, position_state):
        assert strategy_arg is strategy
        assert date == "2024-09-05"
        assert trailing_stop_pct == pytest.approx(0.20)
        assert position_state == {}
        return {
            "schema_version": "1.0",
            "date": date,
            "trailing_stop_pct": trailing_stop_pct,
            "stop_checks": [
                {
                    "symbol": "NVDA",
                    "quantity": 10.0,
                    "holding_start_date": "2024-09-02",
                    "previous_peak_close": 150.0,
                    "peak_close": 150.0,
                    "current_check_price": 118.0,
                    "trailing_stop_pct": 0.2,
                    "stop_price": 120.0,
                    "triggered": True,
                    "price_source": "daily_close",
                }
            ],
            "updated_position_state": {},
            "execution_plan": {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [
                    {
                        "sequence": 1,
                        "action": "submit_order",
                        "symbol": "NVDA",
                        "side": "sell",
                        "quantity_mode": "shares",
                        "quantity": 10,
                        "asset_type": "stock",
                        "order_type": "market",
                        "time_in_force": "day",
                    }
                ],
            },
            "warnings": [],
        }

    monkeypatch.setattr(module, "trailing_stop_to_execution_plan", fake_trailing_stop_to_execution_plan)

    strategy.on_trading_iteration()

    assert strategy.agents["equity_basket_agent"].calls == []
    assert len(strategy.agents["execution_agent"].calls) == 1
    execution_call = strategy.agents["execution_agent"].calls[0]
    assert execution_call["context"]["execution_reason"] == "trailing_stop"
    assert execution_call["context"]["execution_plan"]["orders"][0]["symbol"] == "NVDA"
    assert strategy._last_trailing_stop_result["stop_checks"][0]["triggered"] is True


def test_no_trailing_stop_allows_weekly_equity_workflow(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy)
    strategy.get_datetime = lambda: datetime(2024, 9, 9, 9, 30)
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": ["AAPL", "MSFT", "NVDA", "AMZN", "META"],
            "selected_symbols": ["AAPL", "MSFT", "NVDA", "AMZN", "META"],
            "reason_brief": "Five strongest names.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."

    monkeypatch.setattr(
        module,
        "trailing_stop_to_execution_plan",
        lambda strategy_arg, *, date, trailing_stop_pct, position_state: {
            "schema_version": "1.0",
            "date": date,
            "trailing_stop_pct": trailing_stop_pct,
            "stop_checks": [],
            "updated_position_state": {},
            "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []},
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        module,
        "resolve_qqq_snapshot",
        lambda as_of_date, *, mode="strict", data_dir=None: qqq_resolution(
            as_of_date=as_of_date,
            symbols=("AAPL", "MSFT", "NVDA", "AMZN", "META"),
        ),
    )
    monkeypatch.setattr(
        module,
        "target_portfolio_to_execution_plan",
        lambda strategy_arg, *, date, target_portfolio: {
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
            "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []},
            "warnings": [],
        },
    )

    strategy.on_trading_iteration()

    assert len(strategy.agents["equity_basket_agent"].calls) == 1
    assert strategy._last_trailing_stop_result["execution_plan"]["intent"] == "hold"


def test_benchmark_runner_exposes_fixed_and_qqq_historical_equity_only_strategies():
    benchmark = importlib.import_module("scripts.run_ai_trading_team_examples_benchmark")

    assert "equity-only-llm" in benchmark.STRATEGIES
    assert benchmark.STRATEGIES["equity-only-llm"].__name__ == "AITradingTeamEquityOnlyLLMStrategy"
    assert "qqq-historical-equity-only-llm" in benchmark.STRATEGIES
    assert (
        benchmark.STRATEGIES["qqq-historical-equity-only-llm"].__name__
        == "AITradingTeamQQQHistoricalEquityOnlyLLMStrategy"
    )


def test_benchmark_runner_does_not_expose_quadrant_strategies_on_equity_mainline():
    benchmark = importlib.import_module("scripts.run_ai_trading_team_examples_benchmark")

    assert "mock-growth-inflation-quadrant" not in benchmark.STRATEGIES
    assert "growth-inflation-quadrant" not in benchmark.STRATEGIES


def test_equity_only_strategy_does_not_import_quadrant_strategy_module():
    module = load_module()

    source = Path(module.__file__).read_text(encoding="utf-8")

    assert "ai_trading_team_mock_growth_inflation_quadrant" not in source
