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
                "Analyze the ETF universe through growth, earnings momentum, risk appetite, and economic expansion. "
                "Discuss which holdings to keep, reduce, or replace. You are read-only; do not place orders."
            ),
        )
        self.agents.create(
            name="decision_agent",
            model=model,
            allow_trading=False,
            system_prompt=(
                "Convert growth research plus current account state into a structured trading plan. "
                "Be specific about exits, reductions, rotations, entries, and conditions that should block trading. "
                "You are read-only; do not place orders."
            ),
        )
        self.agents.create(
            name="execution_agent",
            model=model,
            allow_trading=True,
            system_prompt=(
                "Execute the structured plan using native trading tools, especially orders_submit_order. "
                "Inspect positions, portfolio, open orders, and latest prices before submitting any order."
            ),
        )

    def on_trading_iteration(self):
        context = {
            "date": self.get_datetime().date().isoformat(),
            "universe": self.parameters["universe"],
        }
        growth = self.agents["growth_agent"].run(
            task_prompt=(
                "Review the date and universe. Identify the strongest growth-regime opportunity, which current "
                "exposures should be kept or reduced, and what replacement candidates deserve attention."
            ),
            context=context,
        )
        decision = self.agents["decision_agent"].run(
            task_prompt=(
                "Use growth_report and current account state to produce JSON-like text with fields: plan_type, "
                "target_symbol, current_position_assessment, exit_actions, entry_actions, do_not_trade_if."
            ),
            context={**context, "growth_report": growth.summary},
        )
        self.agents["execution_agent"].run(
            task_prompt=(
                "Use growth_report and trading_plan to inspect the account, open orders, positions, and latest "
                "prices, then submit only the orders required by the plan with orders_submit_order."
            ),
            context={**context, "growth_report": growth.summary, "trading_plan": decision.summary},
        )
