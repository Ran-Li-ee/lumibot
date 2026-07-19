"""Growth-to-execution AI trading team example."""

import json
import math
import os
from decimal import Decimal, InvalidOperation

from lumibot.strategies.strategy import Strategy

ALLOWED_INTENTS = {"hold", "enter_position", "rotate", "reduce_position", "close_position"}
DECISION_TYPE_INTENTS = {
    "hold": "hold",
    "buy": "enter_position",
    "rotate": "rotate",
    "reduce": "reduce_position",
    "close": "close_position",
}
INTENT_ALIASES = {
    "backtest": "enter_position",
    "keep_cash": "hold",
    "maintain_cash": "hold",
}
ALLOWED_ACTIONS = {"submit_order"}
ALLOWED_SIDES = {"buy", "sell"}
ALLOWED_QUANTITY_MODES = {"shares"}
REJECTED_SEMANTIC_QUANTITY_MODES = {
    "current_position",
    "full_position",
    "max_affordable_cash",
    "max_affordable_after_prior_sells",
}
ALLOWED_ORDER_TYPES = {"market", "limit", "stop", "stop_limit", "trailing_stop", "smart_limit"}
REQUIRED_DECISION_ACCOUNT_TOOLS = {"account_positions", "account_portfolio"}
REQUIRED_DECISION_BUY_SIZING_TOOLS = {"market_last_price"}
DEFAULT_BUY_CASH_BUFFER_PCT = 0.02
MINIMUM_BUY_CASH_BUFFER_PCT = 0.01
MAXIMUM_BUY_CASH_BUFFER_PCT = 0.03
SYSTEM_EXECUTION_CONSTRAINTS = {
    "allow_negative_cash": False,
    "if_any_order_blocked": "stop_remaining_orders",
}


class DecisionCashBufferToleranceError(ValueError):
    def __init__(
        self,
        *,
        sequence,
        symbol,
        quantity,
        sizing_price,
        simulated_cash_before_buy,
        projected_cash,
        portfolio_value,
        projected_ratio,
    ):
        self.sequence = int(sequence)
        self.symbol = str(symbol)
        self.quantity = float(quantity)
        self.sizing_price = float(sizing_price)
        self.simulated_cash_before_buy = float(simulated_cash_before_buy)
        self.projected_cash = float(projected_cash)
        self.portfolio_value = float(portfolio_value)
        self.projected_ratio = float(projected_ratio)
        self.minimum_ratio = MINIMUM_BUY_CASH_BUFFER_PCT
        self.maximum_ratio = MAXIMUM_BUY_CASH_BUFFER_PCT
        self.execution_plan = None
        super().__init__(
            "DECISION_CASH_BUFFER_TOLERANCE: execution_plan buy order "
            f"sequence {self.sequence} for {self.symbol} would leave cash "
            f"{self.projected_cash:.2f} ({self.projected_ratio:.4%} of portfolio value); "
            f"accepted range is {self.minimum_ratio:.0%} to {self.maximum_ratio:.0%}, inclusive."
        )

    def as_diagnostics(self):
        return {
            "sequence": self.sequence,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "sizing_price": self.sizing_price,
            "simulated_cash_before_buy": self.simulated_cash_before_buy,
            "projected_cash": self.projected_cash,
            "portfolio_value": self.portfolio_value,
            "projected_ratio": self.projected_ratio,
            "minimum_ratio": self.minimum_ratio,
            "maximum_ratio": self.maximum_ratio,
        }


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
    if quantity_mode in REJECTED_SEMANTIC_QUANTITY_MODES:
        raise ValueError(
            "executable orders must use shares quantity_mode with explicit numeric quantity; "
            f"got semantic quantity_mode: {quantity_mode}"
        )
    if quantity_mode not in ALLOWED_QUANTITY_MODES:
        raise ValueError(f"unsupported order quantity_mode: {quantity_mode}")

    if "quantity" not in order or order["quantity"] is None:
        raise ValueError("order quantity is required for shares quantity_mode.")
    try:
        quantity = float(order["quantity"])
    except (TypeError, ValueError) as exc:
        raise ValueError("order quantity must be positive for shares quantity_mode.") from exc
    if not math.isfinite(quantity) or quantity <= 0:
        raise ValueError("order quantity must be positive for shares quantity_mode.")
    if not quantity.is_integer():
        raise ValueError("order quantity must be a positive whole-share integer.")

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
    if isinstance(schema_version, bool):
        raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")
    if isinstance(schema_version, (int, float)):
        if schema_version != 1:
            raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")
        schema_version = 1
    elif isinstance(schema_version, str):
        if schema_version.strip() not in {"1", "1.0"}:
            raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")
        schema_version = 1
    else:
        raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")

    raw_intent = str(plan.get("intent") or plan.get("mode") or "").strip().lower()
    intent = INTENT_ALIASES.get(raw_intent, raw_intent)
    if intent not in ALLOWED_INTENTS:
        decision = payload.get("decision", {})
        if isinstance(decision, dict):
            decision_type = str(decision.get("type") or "").strip().lower()
            intent = DECISION_TYPE_INTENTS.get(decision_type, intent)
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
    if sum(order["side"] == "buy" for order in normalized_orders) > 1:
        raise ValueError("execution_plan supports at most one buy order.")
    buy_indexes = [index for index, order in enumerate(normalized_orders) if order["side"] == "buy"]
    if buy_indexes:
        buy_index = buy_indexes[0]
        if any(order["side"] == "sell" for order in normalized_orders[buy_index + 1 :]):
            raise ValueError("execution_plan must place all sell orders before the buy order.")
        buy_order_type = normalized_orders[buy_index]["order_type"]
        if buy_order_type in {"stop", "trailing_stop"}:
            raise ValueError(
                f"buy order_type {buy_order_type!r} does not provide a bounded execution price."
            )

    if "constraints" in plan:
        constraints = plan["constraints"]
    elif "execution_constraints" in plan:
        constraints = plan["execution_constraints"]
    else:
        constraints = {}
    constraints = _require_dict(constraints, "execution_plan constraints")
    normalized_constraints = dict(SYSTEM_EXECUTION_CONSTRAINTS)

    return {
        "schema_version": schema_version,
        "intent": intent,
        "orders": normalized_orders,
        "constraints": normalized_constraints,
    }


def _agent_result_tool_names(result):
    return {event.tool_name for event in getattr(result, "tool_calls", []) if getattr(event, "tool_name", None)}


def _raw_execution_order_price(strategy, order):
    order_type = str(order.get("order_type", "market")).strip().lower()
    price_field = None
    if order_type in {"limit", "smart_limit"}:
        price_field = "limit_price"
    elif order_type == "stop":
        price_field = "stop_price"
    elif order_type == "stop_limit":
        price_field = "stop_limit_price" if order.get("stop_limit_price") is not None else "limit_price"

    if price_field is not None:
        raw_price = order.get(price_field)
        if raw_price is None:
            raise ValueError(f"PRICE_REQUIRED: {order_type} order requires {price_field}.")
        return raw_price, price_field

    raw_price = strategy.get_last_price(order["symbol"])
    if raw_price is None:
        raise ValueError(f"PRICE_REQUIRED: cannot validate execution plan because {order['symbol']} has no last price.")
    return raw_price, None


def _execution_order_price(strategy, order):
    raw_price, price_field = _raw_execution_order_price(strategy, order)
    if price_field is not None:
        price = float(raw_price)
        if not math.isfinite(price) or price <= 0:
            raise ValueError(f"{price_field} must be a positive finite price.")
        return price

    price = float(raw_price)
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"PRICE_REQUIRED: invalid last price for {order['symbol']}: {raw_price!r}.")
    return price


def _decimal_number(value, label):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite.") from exc
    if not result.is_finite():
        raise ValueError(f"{label} must be finite.")
    return result


def _decision_order_price(strategy, order):
    raw_price, price_field = _raw_execution_order_price(strategy, order)
    label = price_field or f"last price for {order['symbol']}"
    price = _decimal_number(raw_price, label)
    if price <= 0:
        if price_field is not None:
            raise ValueError(f"{price_field} must be a positive finite price.")
        raise ValueError(f"PRICE_REQUIRED: invalid last price for {order['symbol']}: {raw_price!r}.")
    return price


def _current_long_holdings(strategy):
    holdings = {}
    for position in strategy.get_positions():
        asset = getattr(position, "asset", None)
        symbol = str(getattr(asset, "symbol", "")).strip().upper()
        quantity = _decimal_number(getattr(position, "quantity", 0), f"position quantity for {symbol or 'unknown'}")
        if symbol and quantity > 0:
            holdings[symbol] = holdings.get(symbol, Decimal("0")) + quantity
    return holdings


def validate_decision_buy_sizing(strategy, execution_plan):
    if execution_plan["intent"] == "hold":
        return

    simulated_cash = _decimal_number(strategy.get_cash(), "current cash")
    portfolio_value = _decimal_number(strategy.get_portfolio_value(), "portfolio value")
    has_buy_order = any(order["side"] == "buy" for order in execution_plan["orders"])
    if has_buy_order and portfolio_value <= 0:
        raise ValueError("portfolio value must be a finite positive number when sizing a buy order.")

    minimum_cash_reserve = portfolio_value * Decimal(str(MINIMUM_BUY_CASH_BUFFER_PCT))
    holdings = (
        _current_long_holdings(strategy)
        if any(order["side"] == "sell" for order in execution_plan["orders"])
        else {}
    )
    uncertain_sell_proceeds = Decimal("0")
    orders = sorted(execution_plan["orders"], key=lambda order: order["sequence"])
    for order in orders:
        price = _decision_order_price(strategy, order)
        quantity = _decimal_number(order["quantity"], f"quantity for {order['symbol']}")
        notional = quantity * price
        if order["side"] == "sell":
            held_quantity = holdings.get(order["symbol"], Decimal("0"))
            if held_quantity <= 0:
                raise ValueError(
                    "DECISION_SELL_POSITION_REQUIRED: execution_plan cannot sell "
                    f"{order['symbol']} because no current long position exists."
                )
            if quantity > held_quantity:
                raise ValueError(
                    "DECISION_SELL_EXCEEDS_LONG_POSITION: execution_plan sell order "
                    f"for {order['symbol']} requests {quantity} shares but only {held_quantity} are held."
                )
            holdings[order["symbol"]] = held_quantity - quantity
            if order.get("order_type", "market") == "market":
                simulated_cash += notional
            else:
                uncertain_sell_proceeds += notional
            continue
        if order["side"] != "buy":
            continue

        if (
            uncertain_sell_proceeds > 0
            and simulated_cash - notional < minimum_cash_reserve
            and simulated_cash + uncertain_sell_proceeds - notional >= minimum_cash_reserve
        ):
            raise ValueError(
                "DECISION_BUY_REQUIRES_MARKET_SELL_PROCEEDS: a later buy may use prior sell proceeds "
                "only when those sells are market orders."
            )
        projected_cash = simulated_cash - notional
        projected_ratio = projected_cash / portfolio_value
        if not (
            Decimal(str(MINIMUM_BUY_CASH_BUFFER_PCT))
            <= projected_ratio
            <= Decimal(str(MAXIMUM_BUY_CASH_BUFFER_PCT))
        ):
            raise DecisionCashBufferToleranceError(
                sequence=order["sequence"],
                symbol=order["symbol"],
                quantity=quantity,
                sizing_price=price,
                simulated_cash_before_buy=simulated_cash,
                projected_cash=projected_cash,
                portfolio_value=portfolio_value,
                projected_ratio=projected_ratio,
            )
        simulated_cash = projected_cash


def validate_execution_plan_cash_safety(strategy, execution_plan):
    if execution_plan["intent"] == "hold":
        return

    constraints = execution_plan.get("constraints", {})
    allow_negative_cash = bool(constraints.get("allow_negative_cash", False))
    simulated_cash = float(strategy.get_cash())
    if not math.isfinite(simulated_cash):
        raise ValueError("current cash must be finite.")

    orders = sorted(execution_plan["orders"], key=lambda order: order["sequence"])
    for order in orders:
        price = _execution_order_price(strategy, order)
        notional = float(order["quantity"]) * price
        if order["side"] == "sell":
            if order.get("order_type", "market") == "market":
                simulated_cash += notional
            continue
        if order["side"] != "buy":
            continue

        projected_cash = simulated_cash - notional
        if projected_cash < 0 and not allow_negative_cash:
            raise ValueError(
                "NEGATIVE_CASH_NOT_ALLOWED: execution_plan buy order "
                f"sequence {order['sequence']} for {order['symbol']} would leave cash {projected_cash:.2f}."
            )
        simulated_cash = projected_cash


def validate_decision_tool_evidence(execution_plan, decision_result):
    if execution_plan["intent"] == "hold":
        return

    tool_names = _agent_result_tool_names(decision_result)
    missing_account_tools = sorted(REQUIRED_DECISION_ACCOUNT_TOOLS - tool_names)
    if missing_account_tools:
        raise ValueError(
            "decision agent must call account tools before non-hold execution plan; "
            f"missing: {', '.join(missing_account_tools)}"
        )

    has_buy_order = any(order["side"] == "buy" for order in execution_plan["orders"])
    missing_buy_tools = sorted(REQUIRED_DECISION_BUY_SIZING_TOOLS - tool_names) if has_buy_order else []
    if missing_buy_tools:
        raise ValueError(
            "decision agent must call price tools before buy order sizing; "
            f"missing: {', '.join(missing_buy_tools)}"
        )


def validate_decision_result(strategy, decision_result):
    execution_plan = parse_execution_plan_from_decision_summary(decision_result.summary)
    validate_decision_tool_evidence(execution_plan, decision_result)
    try:
        validate_decision_buy_sizing(strategy, execution_plan)
    except DecisionCashBufferToleranceError as exc:
        exc.execution_plan = execution_plan
        raise
    validate_execution_plan_cash_safety(strategy, execution_plan)
    return execution_plan


def _decision_structure_from_summary(summary):
    payload = json.loads(_extract_first_json_object(summary))
    payload = _require_dict(payload, "decision summary")
    decision = _require_dict(payload.get("decision", {}), "decision")
    return {
        "type": str(decision.get("type", "")).strip().lower(),
        "from": str(decision.get("from", "")).strip().upper(),
        "to": str(decision.get("to", "")).strip().upper(),
        "reason_brief": str(decision.get("reason_brief", "")).strip(),
    }


def validate_retry_quantity_only_change(
    initial_summary,
    initial_execution_plan,
    retry_summary,
    retry_execution_plan,
    *,
    allowed_quantity_sequence,
    allowed_quantity_symbol,
):
    changed_fields = []
    initial_decision = _decision_structure_from_summary(initial_summary)
    retry_decision = _decision_structure_from_summary(retry_summary)
    for field in ("type", "from", "to", "reason_brief"):
        if initial_decision[field] != retry_decision[field]:
            changed_fields.append(f"decision.{field}")

    for field in ("schema_version", "intent", "constraints"):
        if initial_execution_plan[field] != retry_execution_plan[field]:
            changed_fields.append(f"execution_plan.{field}")

    initial_orders = initial_execution_plan["orders"]
    retry_orders = retry_execution_plan["orders"]
    if len(initial_orders) != len(retry_orders):
        changed_fields.append("execution_plan.orders.length")
    else:
        for index, (initial_order, retry_order) in enumerate(zip(initial_orders, retry_orders, strict=True)):
            order_fields = set(initial_order) | set(retry_order)
            for field in sorted(order_fields):
                quantity_change_is_allowed = (
                    field == "quantity"
                    and initial_order.get("sequence") == allowed_quantity_sequence
                    and initial_order.get("symbol") == allowed_quantity_symbol
                )
                if quantity_change_is_allowed:
                    continue
                if initial_order.get(field) != retry_order.get(field):
                    changed_fields.append(f"execution_plan.orders[{index}].{field}")

    if changed_fields:
        raise ValueError(
            "DECISION_RETRY_STRUCTURE_CHANGED: correction retry may change only positive whole-share quantity; "
            f"changed immutable fields: {', '.join(changed_fields)}."
        )


class AITradingTeamGrowthExecutionTestStrategy(Strategy):
    parameters = {
        "universe": ["SPY", "QQQ", "IWM", "TLT", "IEF", "TIP", "GLD", "DBC", "VNQ", "UUP", "FXI", "EEM"],
    }
    _execution_agent_base_system_prompt_mode = "execution_minimal"

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
                "defaults apply. execution_plan.intent must be one of: hold, enter_position, rotate, "
                "reduce_position, close_position; do not write a sentence. Each order must include sequence, symbol, "
                "side, quantity_mode, and quantity. Optional order "
                "fields include action, "
                "asset_type, order_type, time_in_force, limit_price, stop_price, stop_limit_price, "
                "trail_price, and trail_percent. Legacy aliases are optional: mode for intent and "
                "execution_constraints for constraints. Before producing JSON for a non-hold decision, call "
                "account_positions and account_portfolio. Call market_last_price when sizing buy orders. Final "
                "executable orders must use quantity_mode: shares and must include a positive numeric quantity. "
                "Do not use full_position, current_position, max_affordable_cash, or max_affordable_after_prior_sells "
                "in final executable orders. For selling all or part of a position, calculate the share quantity from "
                "account tool output and write the number. For buy orders, calculate the share quantity from cash and "
                "latest price data and write the number. Target a cash reserve of approximately 2% of the current "
                "portfolio value. Calculate the largest whole-share quantity as "
                "floor((available cash after prior sell orders - 0.02 * current portfolio value) / latest price). "
                "The resulting reserve may be slightly higher because orders use whole shares. The final quantity "
                "must already account for this reserve. Do not include cash_buffer_pct in the execution order. "
                "Buy orders must use market, limit, smart_limit, or stop_limit. Do not use stop or trailing_stop "
                "for a buy order because those order types do not provide a bounded execution price. "
                "Never produce orders that would make cash negative. "
                "If the "
                "account holds an ETF and another ETF is more attractive than the current holding based on current "
                'evidence, choose decision.type="rotate" unless there is a clear blocking reason. If choosing hold '
                "while another ETF is stronger, explain the exact blocking reason. If the account holds only cash or a "
                'cash-like position and the research identifies a strongest ETF candidate, choose decision.type="buy" '
                "unless there is a clear blocking reason. "
                'For rotate decisions, set an order with sequence: 1 and side: "sell" for the source holding, then '
                'an order with sequence: 2 and side: "buy" for the destination holding. '
                'Use quantity_mode: "shares" and explicit numeric quantity on every executable order. '
                "You cannot place orders, but you must produce an actionable trading plan for the execution agent."
            ),
        )
        self.agents.create(
            name="execution_agent",
            model=model,
            allow_trading=True,
            base_system_prompt_mode=self._execution_agent_base_system_prompt_mode,
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
                "submitted or blocked. Submit the explicit numeric share quantities in execution_plan.orders. Do not "
                "compute semantic sizing. Check affordability before buy orders."
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
                "(execution_plan.orders); constraints is optional and defaults apply. execution_plan.intent must be "
                "one of: hold, enter_position, rotate, reduce_position, close_position; do not write a sentence. "
                "Each order must include sequence, symbol, side, quantity_mode, and quantity. Optional order fields "
                "include action, asset_type, "
                "order_type, time_in_force, limit_price, stop_price, stop_limit_price, trail_price, and "
                "trail_percent. Legacy aliases are optional: mode for intent and execution_constraints for "
                "constraints. Before producing JSON for a non-hold decision, call account_positions and "
                "account_portfolio. Call market_last_price when sizing buy orders. Final executable orders must use "
                "quantity_mode: shares and must include a positive numeric quantity. Do not use full_position, "
                "current_position, max_affordable_cash, or max_affordable_after_prior_sells in final executable "
                "orders. For selling all or part of a position, calculate the share quantity from account tool "
                "output and write the number. For buy orders, calculate the share quantity from cash and latest "
                "price data and write the number. Target a cash reserve of approximately 2% of the current portfolio "
                "value. Calculate the largest whole-share quantity as "
                "floor((available cash after prior sell orders - 0.02 * current portfolio value) / latest price). "
                "The resulting reserve may be slightly higher because orders use whole shares. The final quantity "
                "must already account for this reserve. Do not include cash_buffer_pct in the execution order. "
                "Buy orders must use market, limit, smart_limit, or stop_limit. Do not use stop or trailing_stop "
                "for a buy order because those order types do not provide a bounded execution price. "
                "Never produce orders that would make cash negative. "
                "If another ETF is more attractive "
                'than the current holding based on current evidence, output decision.type="rotate" unless a clear '
                "blocking reason exists. If the account holds only cash or a cash-like position and growth_report "
                'identifies a strongest ETF candidate, output decision.type="buy" unless a clear blocking reason '
                'exists. For rotate decisions, include sequence: 1 with side: "sell" for decision.from and sequence: '
                '2 with side: "buy" for decision.to. Final executable orders must include a positive numeric '
                'quantity. Use quantity_mode: "shares" and explicit numeric quantity on both orders.'
            ),
            context={**context, "growth_report": growth.summary},
        )
        try:
            execution_plan = validate_decision_result(self, decision)
        except DecisionCashBufferToleranceError as initial_sizing_error:
            initial_decision_summary = decision.summary
            initial_execution_plan = initial_sizing_error.execution_plan
            decision = self.agents["decision_agent"].run(
                task_prompt=(
                    "Correction only: preserve the prior decision type, symbols, sides, order sequence, and order "
                    "types. Recalculate only the numeric buy quantity using current account and price evidence. Call "
                    "account_positions, account_portfolio, and market_last_price again. Target approximately 2% cash; "
                    "the corrected projected cash ratio must be between 1% and 3%, inclusive. Return the same strict "
                    "JSON contract only, without markdown, RESULT text, or prose."
                ),
                context={
                    **context,
                    "growth_report": growth.summary,
                    "previous_decision_output": decision.summary,
                    "decision_sizing_diagnostics": initial_sizing_error.as_diagnostics(),
                },
            )
            try:
                execution_plan = validate_decision_result(self, decision)
                validate_retry_quantity_only_change(
                    initial_decision_summary,
                    initial_execution_plan,
                    decision.summary,
                    execution_plan,
                    allowed_quantity_sequence=initial_sizing_error.sequence,
                    allowed_quantity_symbol=initial_sizing_error.symbol,
                )
            except ValueError as exc:
                self._last_execution_plan_error = str(exc)
                print(f"Execution plan blocked: {exc}")
                return
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
                "blocked. Block or pause solely for execution-level blockers. Submit the explicit numeric share "
                "quantities in execution_plan.orders. Do not compute semantic sizing. Check affordability before "
                "buy orders."
            ),
            context={"date": context["date"], "execution_plan": execution_plan},
        )
