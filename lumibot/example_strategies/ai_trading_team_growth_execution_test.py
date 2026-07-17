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
                "produce JSON-like text with top-level fields decision and execution_plan. Choose exactly one "
                "decision.type from: hold, buy, rotate, reduce, close. decision must include decision.type, "
                "decision.from, decision.to, and decision.reason_brief. execution_plan must include "
                "execution_plan.mode, execution_plan.orders, and execution_plan.execution_constraints. Each order in "
                "execution_plan.orders must include sequence, symbol, side, and quantity_basis. Include "
                "max_affordable_after_prior_sells and cash_buffer_pct as constraints or order fields. If the "
                "account holds an ETF and another ETF is more attractive than the current holding based on current "
                'evidence, choose decision.type="rotate" unless there is a clear blocking reason. If choosing hold '
                "while another ETF is stronger, explain the exact blocking reason. If the account holds only cash or a "
                'cash-like position and the research identifies a strongest ETF candidate, choose decision.type="buy" '
                "unless there is a clear blocking reason. "
                'For rotate decisions, set an order with sequence: 1 and side: "sell" for the source holding, then '
                'an order with sequence: 2 and side: "buy" for the destination holding. '
                "You cannot place orders, but you must produce an actionable trading plan for the execution agent."
            ),
        )
        self.agents.create(
            name="execution_agent",
            model=model,
            allow_trading=True,
            system_prompt=(
                "Execute the structured trading_plan using native trading tools, especially orders_submit_order. "
                "Treat execution_plan.orders as the authoritative source of truth that must control and drive "
                "execution. decision.reason_brief is only human context, not permission to change orders. Do not redo "
                "investment analysis, do not re-rank candidates, and do not substitute or replace any symbol. Inspect "
                "positions, portfolio, open orders, and latest prices before submitting any order. Execute "
                "execution_plan.orders in ascending sequence order and preserve the sequence number in each report. "
                "Only execution-level blockers may block or pause execution. For each sequence, report whether it was "
                "submitted or blocked. Honor max_affordable_after_prior_sells and cash_buffer_pct when sizing or "
                "checking affordability."
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
                "Use growth_report and current account state to produce JSON-like text with exactly two top-level "
                "fields: decision and execution_plan. decision must include decision.type, decision.from, "
                "decision.to, and decision.reason_brief. decision.type must be one of: hold, buy, rotate, reduce, "
                "close. execution_plan must include execution_plan.mode, execution_plan.orders, and "
                "execution_plan.execution_constraints. Each order in execution_plan.orders must include sequence, "
                "symbol, side, and quantity_basis. Include max_affordable_after_prior_sells and cash_buffer_pct in "
                "execution_plan.execution_constraints or on the relevant order. If another ETF is more attractive "
                'than the current holding based on current evidence, output decision.type="rotate" unless a clear '
                "blocking reason exists. If the account holds only cash or a cash-like position and growth_report "
                'identifies a strongest ETF candidate, output decision.type="buy" unless a clear blocking reason '
                'exists. For rotate decisions, include sequence: 1 with side: "sell" for decision.from and sequence: '
                '2 with side: "buy" for decision.to, using max_affordable_after_prior_sells for the buy quantity_basis.'
            ),
            context={**context, "growth_report": growth.summary},
        )
        self.agents["execution_agent"].run(
            task_prompt=(
                "Use trading_plan to inspect the account, open orders, positions, and latest prices, then submit only "
                "the orders listed in execution_plan.orders with orders_submit_order. execution_plan.orders is the "
                "authoritative source of truth for execution; decision.reason_brief is human context only. Do not "
                "re-rank, do not substitute symbol, and do not use upstream research to override the trading_plan. "
                "Execute in sequence order, preserve each sequence number, and report each sequence as submitted or "
                "blocked. Block or pause solely for execution-level blockers. Apply max_affordable_after_prior_sells "
                "and cash_buffer_pct when checking cash and sizing orders."
            ),
            context={**context, "trading_plan": decision.summary},
        )
