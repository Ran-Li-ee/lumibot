"""Growth-to-execution AI trading team example."""

import json
import math
import os

from lumibot.strategies.strategy import Strategy

ALLOWED_INTENTS = {"hold", "enter_position", "rotate", "reduce_position", "close_position"}
ALLOWED_ACTIONS = {"submit_order"}
ALLOWED_SIDES = {"buy", "sell"}
ALLOWED_QUANTITY_MODES = {
    "shares",
    "current_position",
    "max_affordable_cash",
    "max_affordable_after_prior_sells",
}
ALLOWED_ORDER_TYPES = {"market", "limit", "stop", "stop_limit", "trailing_stop", "smart_limit"}


def _extract_first_json_object(text):
    if not isinstance(text, str):
        raise ValueError("Decision summary must be text.")

    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in decision summary.")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    raise ValueError("Unclosed JSON object in decision summary.")


def _require_dict(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return value


def _normalize_order(order):
    order = _require_dict(order, "order")
    for field in ("sequence", "symbol", "side", "quantity_mode"):
        if field not in order:
            raise ValueError(f"missing required order field: {field}")

    sequence = order["sequence"]
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
        raise ValueError("order sequence must be a positive integer.")

    if not isinstance(order["symbol"], str):
        raise ValueError("order symbol must be a string.")
    symbol = order["symbol"].strip().upper()
    if not symbol:
        raise ValueError("order symbol must be non-empty.")

    side = str(order["side"]).strip().lower()
    if side not in ALLOWED_SIDES:
        raise ValueError(f"unsupported order side: {side}")

    action = str(order.get("action", "submit_order")).strip().lower()
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"unsupported order action: {action}")

    quantity_mode = str(order["quantity_mode"]).strip().lower()
    if quantity_mode not in ALLOWED_QUANTITY_MODES:
        raise ValueError(f"unsupported order quantity_mode: {quantity_mode}")

    quantity = None
    if quantity_mode == "shares":
        if "quantity" not in order or order["quantity"] is None:
            raise ValueError("order quantity is required for shares quantity_mode.")
        try:
            quantity = float(order["quantity"])
        except (TypeError, ValueError) as exc:
            raise ValueError("order quantity must be positive for shares quantity_mode.") from exc
        if not math.isfinite(quantity) or quantity <= 0:
            raise ValueError("order quantity must be positive for shares quantity_mode.")

    order_type = str(order.get("order_type", "market")).strip().lower()
    if order_type not in ALLOWED_ORDER_TYPES:
        raise ValueError(f"unsupported order_type: {order_type}")

    return {
        "sequence": sequence,
        "action": action,
        "symbol": symbol,
        "asset_type": str(order.get("asset_type", "stock")).strip().lower(),
        "side": side,
        "quantity": quantity,
        "quantity_mode": quantity_mode,
        "cash_buffer_pct": order.get("cash_buffer_pct", 2 if side == "buy" else 0),
        "order_type": order_type,
        "limit_price": order.get("limit_price"),
        "stop_price": order.get("stop_price"),
        "stop_limit_price": order.get("stop_limit_price"),
        "trail_price": order.get("trail_price"),
        "trail_percent": order.get("trail_percent"),
        "time_in_force": str(order.get("time_in_force", "day")).strip().lower(),
    }


def parse_execution_plan_from_decision_summary(summary):
    raw_json = _extract_first_json_object(summary)
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Decision summary JSON is invalid: {exc.msg}.") from exc

    payload = _require_dict(payload, "decision summary JSON")
    if "execution_plan" not in payload:
        raise ValueError("decision summary JSON must include execution_plan.")
    plan = _require_dict(payload["execution_plan"], "execution_plan")

    if "schema_version" not in plan:
        raise ValueError("execution_plan schema_version is required.")
    schema_version = plan["schema_version"]
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version != 1:
        raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")

    intent = str(plan.get("intent") or plan.get("mode") or "").strip().lower()
    if intent == "backtest":
        intent = "enter_position"
    if not intent:
        raise ValueError("execution_plan intent is required.")
    if intent not in ALLOWED_INTENTS:
        raise ValueError(f"unsupported execution_plan intent: {intent}")

    if "orders" not in plan:
        raise ValueError("execution_plan orders are required.")
    orders = plan["orders"]
    if not isinstance(orders, list):
        raise ValueError("execution_plan orders must be a list.")
    if intent == "hold" and orders:
        raise ValueError("hold intent cannot include orders.")
    if intent != "hold" and not orders:
        raise ValueError("execution_plan orders are required for non-hold intent.")
    normalized_orders = sorted((_normalize_order(order) for order in orders), key=lambda order: order["sequence"])
    order_sequences = [order["sequence"] for order in normalized_orders]
    if len(order_sequences) != len(set(order_sequences)):
        raise ValueError("duplicate order sequence.")

    if "constraints" in plan:
        constraints = plan["constraints"]
    elif "execution_constraints" in plan:
        constraints = plan["execution_constraints"]
    else:
        constraints = {}
    constraints = _require_dict(constraints, "execution_plan constraints")
    normalized_constraints = {
        **constraints,
        "allow_negative_cash": False,
        "if_any_order_blocked": "stop_remaining_orders",
    }

    return {
        "schema_version": schema_version,
        "intent": intent,
        "orders": normalized_orders,
        "constraints": normalized_constraints,
    }


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
                "produce strict JSON with top-level fields decision and execution_plan. Return only one valid JSON "
                "object. Do not include markdown. Do not include RESULT text. Do not include prose after the JSON. "
                "Choose exactly one "
                "decision.type from: hold, buy, rotate, reduce, close. decision must include decision.type, "
                "decision.from, decision.to, and decision.reason_brief. Required top-level execution_plan fields "
                "are schema_version, intent, and orders (execution_plan.orders); constraints is optional and "
                "defaults apply. Each order must include sequence, symbol, side, and quantity_mode. Optional order "
                "fields include action, quantity, "
                "asset_type, cash_buffer_pct, order_type, time_in_force, limit_price, stop_price, stop_limit_price, "
                "trail_price, and trail_percent. Legacy aliases are optional: mode for intent and "
                "execution_constraints for constraints. Include "
                "max_affordable_after_prior_sells and cash_buffer_pct as constraints or order fields when useful. "
                "If the "
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
                "Execute only the provided execution_plan object using native trading tools, especially "
                "orders_submit_order. Do not read or infer investment reasons. Do not add, remove, replace, or "
                "reorder orders. "
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
                "Use growth_report and current account state to produce strict JSON with exactly two top-level "
                "fields: decision and execution_plan. Return only one valid JSON object. Do not include markdown. "
                "Do not include RESULT text. Do not include prose after the JSON. decision must include "
                "decision.type, decision.from, decision.to, and decision.reason_brief. decision.type must be one of: "
                "hold, buy, rotate, reduce, "
                "close. Required top-level execution_plan fields are schema_version, intent, and orders "
                "(execution_plan.orders); constraints is optional and defaults apply. Each order must include "
                "sequence, symbol, side, and "
                "quantity_mode. Optional order fields include action, quantity, asset_type, cash_buffer_pct, "
                "order_type, time_in_force, limit_price, stop_price, stop_limit_price, trail_price, and "
                "trail_percent. Legacy aliases are optional: mode for intent and execution_constraints for "
                "constraints. Include max_affordable_after_prior_sells and "
                "cash_buffer_pct in constraints or on the relevant order. If another ETF is more attractive "
                'than the current holding based on current evidence, output decision.type="rotate" unless a clear '
                "blocking reason exists. If the account holds only cash or a cash-like position and growth_report "
                'identifies a strongest ETF candidate, output decision.type="buy" unless a clear blocking reason '
                'exists. For rotate decisions, include sequence: 1 with side: "sell" for decision.from and sequence: '
                '2 with side: "buy" for decision.to, using max_affordable_after_prior_sells for the buy quantity_mode.'
            ),
            context={**context, "growth_report": growth.summary},
        )
        try:
            execution_plan = parse_execution_plan_from_decision_summary(decision.summary)
        except ValueError as exc:
            self._last_execution_plan_error = str(exc)
            print(f"Execution plan blocked: {exc}")
            return
        self._last_execution_plan_error = None

        self.agents["execution_agent"].run(
            task_prompt=(
                "Execute only the provided execution_plan object. Do not read or infer investment reasons. Do not "
                "add, remove, replace, or reorder orders. Inspect the account, open orders, positions, and latest "
                "prices, then submit only the orders listed in execution_plan.orders with orders_submit_order. "
                "execution_plan.orders is the authoritative source of truth for execution; decision.reason_brief is "
                "human context only. Do not re-rank, do not substitute symbol, and do not use upstream research to "
                "override the execution_plan. "
                "Execute in sequence order, preserve each sequence number, and report each sequence as submitted or "
                "blocked. Block or pause solely for execution-level blockers. Apply max_affordable_after_prior_sells "
                "and cash_buffer_pct when checking cash and sizing orders."
            ),
            context={"date": context["date"], "execution_plan": execution_plan},
        )
