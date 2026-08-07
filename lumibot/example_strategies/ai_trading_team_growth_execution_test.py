"""Growth-to-execution AI trading team example."""

import json
import math
import os
from decimal import Decimal, InvalidOperation

from lumibot.components.agents.builtins import BuiltinTools
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
ALLOWED_ORDER_TYPES = {"market"}
MARKET_ONLY_FORBIDDEN_PRICE_FIELDS = (
    "limit_price",
    "stop_price",
    "stop_limit_price",
    "trail_price",
    "trail_percent",
)
REQUIRED_DECISION_ACCOUNT_TOOLS = {"account_positions", "account_portfolio"}
REQUIRED_DECISION_BUY_SIZING_TOOLS = {"market_last_price"}
SYSTEM_EXECUTION_CONSTRAINTS = {
    "allow_negative_cash": False,
    "if_any_order_blocked": "stop_remaining_orders",
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
        raise ValueError(
            "unsupported order_type for daily market-only strategy: "
            f"{order_type}. Use order_type='market'."
        )
    for field in MARKET_ONLY_FORBIDDEN_PRICE_FIELDS:
        if order.get(field) is not None:
            raise ValueError(f"market-only execution_plan must not include {field}.")

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
    raw_price = strategy.get_last_price(order["symbol"])
    if raw_price is None:
        raise ValueError(f"PRICE_REQUIRED: cannot validate execution plan because {order['symbol']} has no last price.")
    return raw_price, None


def _execution_order_price(strategy, order):
    raw_price, _price_field = _raw_execution_order_price(strategy, order)
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
    raw_price, _price_field = _raw_execution_order_price(strategy, order)
    label = f"last price for {order['symbol']}"
    price = _decimal_number(raw_price, label)
    if price <= 0:
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

    portfolio_value = _decimal_number(strategy.get_portfolio_value(), "portfolio value")
    has_buy_order = any(order["side"] == "buy" for order in execution_plan["orders"])
    if has_buy_order and portfolio_value <= 0:
        raise ValueError("portfolio value must be a finite positive number when sizing a buy order.")

    simulated_cash = _decimal_number(strategy.get_cash(), "current cash")
    holdings = (
        _current_long_holdings(strategy)
        if any(order["side"] == "sell" for order in execution_plan["orders"])
        else {}
    )
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
            simulated_cash += notional
            continue
        if order["side"] != "buy":
            continue

        simulated_cash -= notional


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
    validate_decision_buy_sizing(strategy, execution_plan)
    validate_execution_plan_cash_safety(strategy, execution_plan)
    return execution_plan


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
            include_builtin_tools=False,
            tools=[
                BuiltinTools.account.positions(),
                BuiltinTools.account.portfolio(),
                BuiltinTools.market.load_history_table(),
                BuiltinTools.market.load_history_tables_summary(),
                BuiltinTools.duckdb.query(),
            ],
            system_prompt=(
                "Growth agent role: rank the ETF universe for relative-strength account management. "
                "Use computed summary metrics as default evidence. Identify the strongest candidate and compare "
                "the current ETF holding, if any, against that candidate. State whether the current holding should "
                "be kept, reduced, or replaced. Do not assume any ETF is the default holding. Do not favor the "
                "current holding merely because it is already held. Do not place orders. Do not calculate final "
                "executable share quantities. Your lack of order permission is not a recommendation to hold cash. "
                "Your research recommendation must name the preferred account action: hold, buy, rotate, reduce, "
                "or close. Do not exhaustively load raw history tables when summary evidence is "
                "sufficient. Growth report contract: include a ranked ETF list, strongest candidate, compact "
                "evidence summary, current holding comparison when relevant, research recommendation, and a short "
                "RESULT summary."
            ),
        )
        self.agents.create(
            name="decision_agent",
            model=model,
            allow_trading=False,
            include_builtin_tools=False,
            tools=[
                BuiltinTools.account.positions(),
                BuiltinTools.account.portfolio(),
                BuiltinTools.market.last_price(),
            ],
            system_prompt=(
                "Decision agent role: convert growth_report and current account state into a concrete account "
                "management decision and strict execution_plan. Do not place orders and do not perform broad ETF "
                "research again. Choose exactly one decision.type from hold, buy, rotate, reduce, close. Decision "
                "JSON contract: return only one valid JSON object with top-level fields decision and execution_plan. "
                "Do not include markdown, RESULT text, or prose after the JSON. decision must include "
                "decision.type, decision.from, decision.to, and decision.reason_brief. execution_plan must include "
                "schema_version, intent, and orders (execution_plan.orders). intent must be one of hold, "
                "enter_position, rotate, reduce_position, close_position. Each executable order must include "
                "sequence, symbol, side, quantity_mode, quantity, asset_type, order_type, and time_in_force. "
                'quantity_mode must be exactly "shares" for every executable order. '
                "Optional bounded-price fields include limit_price, stop_price, and stop_limit_price. Use numeric "
                "share quantities; do not use full_position, current_position, max_affordable_cash, or "
                "max_affordable_after_prior_sells. Before a non-hold decision, call account_positions and "
                "account_portfolio. Call market_last_price when sizing buy orders. Sizing and order construction "
                "rules: choose order_type before calculating quantity. For selling all or part of a position, "
                "calculate the share quantity from account tool output. For buy sizing, choose sizing_price based on "
                "order_type. For market buys, use a conservative sizing_price based on available price evidence; it "
                "may be higher than market_last_price in daily backtests. For limit buys, use limit_price. For "
                "stop_limit buys, use stop_limit_price or the final bounded execution price. Use the 98% cash rule "
                "only as an internal sizing rule: maximum buy quantity must be no greater than "
                "floor(0.98 * available_cash_after_prior_sells / sizing_price). Output only the final numeric share "
                "quantity. Do not output "
                "cash_buffer_pct or any buffer field in execution_plan. Buy orders must use market, limit, "
                "smart_limit, or stop_limit. Do not use stop or trailing_stop for a buy order because those order "
                "types do not provide a bounded execution price. Never produce orders that would make cash negative. "
                'For rotate decisions, set sequence: 1 with side: "sell" for decision.from, then set sequence: 2 '
                'with side: "buy" for decision.to.'
            ),
        )
        self.agents.create(
            name="execution_agent",
            model=model,
            allow_trading=True,
            base_system_prompt_mode=self._execution_agent_base_system_prompt_mode,
            include_builtin_tools=False,
            tools=[
                BuiltinTools.account.positions(),
                BuiltinTools.account.portfolio(),
                BuiltinTools.market.last_price(),
                BuiltinTools.orders.open_orders(),
                BuiltinTools.orders.submit(),
            ],
            system_prompt=(
                "Execution agent role: execute only the provided execution_plan object using native execution tools, "
                "especially orders_submit_order. Treat execution_plan.orders as authoritative. Do not read or infer "
                "investment reasons. Do not re-rank candidates, do not substitute symbols, and do not use upstream "
                "research to override the plan. Do not add, remove, replace, or reorder orders. Inspect positions, "
                "portfolio, open orders, and latest prices before submitting orders. Execute orders in ascending "
                "sequence order. Submit the explicit numeric share quantities in execution_plan.orders. Do not "
                "compute semantic sizing. Block or pause only for execution-level blockers. Execution report "
                "contract: report each sequence as submitted or blocked, and finish with a short RESULT summary."
            ),
        )

    def on_trading_iteration(self):
        context = {
            "date": self.get_datetime().date().isoformat(),
            "universe": self.parameters["universe"],
        }
        growth = self.agents["growth_agent"].run(
            task_prompt=(
                "Use the date and universe to rank the ETF universe from current evidence. Prefer compact computed "
                "summaries and rankings for price-history evidence. Identify the strongest candidate, summarize the "
                "main evidence, compare any current ETF holding against the strongest candidate, and make a research "
                "recommendation with a preferred account action. Finish with RESULT."
            ),
            context=context,
        )
        decision = self.agents["decision_agent"].run(
            task_prompt=(
                "Use growth_report and current account state to produce the strict decision JSON. If the account "
                "holds only cash or a cash-like position and growth_report identifies a strongest ETF candidate, "
                "choose buy unless a clear blocking reason exists. If the account holds an ETF, compare the holding "
                "against the strongest candidate and choose rotate only when the candidate is clearly stronger and "
                "the planned sell and buy quantities can be expressed as executable numeric share orders. Return "
                "only the JSON object."
            ),
            context={**context, "growth_report": growth.summary},
        )
        try:
            execution_plan = validate_decision_result(self, decision)
        except ValueError as exc:
            self._last_execution_plan_error = str(exc)
            print(f"Execution plan blocked: {exc}")
            return
        self._last_execution_plan_error = None

        self.agents["execution_agent"].run(
            task_prompt=(
                "Execute only the provided execution_plan object. Inspect account state, open orders, positions, and "
                "latest prices, then submit only execution_plan.orders with orders_submit_order. Preserve sequence "
                "order and report each sequence as submitted or blocked. Block solely for execution-level blockers."
            ),
            context={"date": context["date"], "execution_plan": execution_plan},
        )
