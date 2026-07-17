"""Growth-to-execution AI trading team example."""

import os

from lumibot.strategies.strategy import Strategy


class AITradingTeamGrowthExecutionTestStrategy(Strategy):
    parameters = {
        "universe": ["SPY", "QQQ", "IWM", "TLT", "IEF", "TIP", "GLD", "DBC", "VNQ", "UUP", "FXI", "EEM"],
    }

    def initialize(self):
        self.sleeptime = "1D"
        model = os.environ.get("AI_TRADING_TEAM_MODEL", "gemini-3.1-flash-lite")
        self.agents.create(
            name="growth_agent",
            model=model,
            allow_trading=False,
            system_prompt=(
                "Analyze the ETF universe for relative-strength account management. Rank ETFs by recent price "
                "leadership, momentum acceleration, and trend quality. Compare the current holding, if any, against "
                "the strongest candidate. Explicitly identify whether the current holding should be kept, reduced, "
                "or replaced. "
                "Do not assume any ETF is the default holding. Do not favor the current holding merely because it is "
                "already held. Rank the universe from current evidence in this run. Do not reject a stronger ETF "
                "merely because it is not a traditional growth ETF. You cannot place orders, but you must still make "
                "a clear research recommendation, including whether cash should be deployed into the strongest ETF "
                "candidate."
            ),
        )
        self.agents.create(
            name="decision_agent",
            model=model,
            allow_trading=False,
            system_prompt=(
                "Convert growth research plus current account state into a concrete relative-strength account "
                "management plan. Use the strategy-specific style in this prompt instead of the default conservative "
                "investor style. Do not treat no-trade as the default answer. Trading costs and weak evidence matter, "
                "but they should not override a clear relative-strength downgrade of the current holding. You must "
                "choose exactly one plan_type: hold, buy, rotate, reduce, close. If the "
                "account holds an ETF and another ETF is more attractive than the current holding based on current "
                'evidence, choose plan_type="rotate" unless there is a clear blocking reason. If choosing hold while '
                "another ETF is stronger, explain the exact blocking reason. If the account holds only cash or a "
                'cash-like position and the research identifies a strongest ETF candidate, choose plan_type="buy" '
                "unless there is a clear blocking reason. "
                "Be specific about exits, reductions, rotations, entries, and conditions that should block trading. "
                "You cannot place orders, but you must produce an actionable trading plan for the execution agent."
            ),
        )
        self.agents.create(
            name="execution_agent",
            model=model,
            allow_trading=True,
            system_prompt=(
                "Execute the structured plan using native trading tools, especially orders_submit_order. Do not redo "
                "investment analysis. Do not use upstream research to override the trading_plan. Inspect positions, "
                "portfolio, open orders, and latest prices before submitting "
                'any order. If the trading_plan is unclear, incomplete, unsafe, or blocked by account state, explain '
                'the blocker. Otherwise execute the trading_plan. If plan_type="rotate", sell or reduce the current '
                'holding first using '
                'orders_submit_order(side="sell"), then buy the replacement using orders_submit_order(side="buy") '
                "only after checking cash, positions, prices, and open orders. Do not skip the sell leg when the "
                "current holding funds the replacement."
            ),
        )

    def on_trading_iteration(self):
        context = {
            "date": self.get_datetime().date().isoformat(),
            "universe": self.parameters["universe"],
        }
        growth = self.agents["growth_agent"].run(
            task_prompt=(
                "Review the date and universe for relative-strength account management. Rank the strongest ETFs by "
                "recent leadership and trend quality. Compare any current holding against the strongest candidate "
                "and say whether the holding should be kept, reduced, or replaced. Do not assume any ETF is the "
                "default holding. Do not favor the current holding merely because it is already held. Rank the "
                "universe from current evidence in this run. You cannot place orders, but you must still make a clear "
                "research recommendation, including whether cash should be deployed into the strongest ETF candidate."
            ),
            context=context,
        )
        decision = self.agents["decision_agent"].run(
            task_prompt=(
                "Use growth_report and current account state to produce JSON-like text with fields: plan_type, "
                "target_symbol, current_position_assessment, exit_actions, entry_actions, do_not_trade_if. Choose "
                "exactly one plan_type: hold, buy, rotate, reduce, close. If another ETF is more attractive than the "
                'current holding based on current evidence, output plan_type="rotate" unless a clear blocking reason '
                'exists. If the account holds only cash or a cash-like position and growth_report identifies a '
                'strongest ETF candidate, output plan_type="buy" unless a clear blocking reason exists. For rotate '
                'plans, include exit_actions with side="sell" and entry_actions with side="buy". For buy plans, '
                'include entry_actions with side="buy".'
            ),
            context={**context, "growth_report": growth.summary},
        )
        self.agents["execution_agent"].run(
            task_prompt=(
                "Use trading_plan to inspect the account, open orders, positions, and latest prices, then submit only "
                'the orders required by the plan with orders_submit_order. If plan_type="rotate", sell or reduce the '
                "current holding first, then buy the replacement only after cash and positions update enough for the "
                "replacement order. Do not use upstream research to override the trading_plan. If plan_type=\"hold\", "
                "submit no orders and explain why."
            ),
            context={**context, "trading_plan": decision.summary},
        )
