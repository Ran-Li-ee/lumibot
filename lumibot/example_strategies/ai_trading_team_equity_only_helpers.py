from __future__ import annotations

import json
import math
from datetime import date as date_type
from typing import Any

from lumibot.components.agents.builtins import BuiltinTools
from lumibot.components.agents.schemas import ToolDefinition

EQUITY_BASKET_ID = "equity"
EQUITY_AGENT_NAME = "equity_basket_agent"
EQUITY_UNIVERSE = [
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
EQUITY_ONLY_BASKET_UNIVERSES = {EQUITY_BASKET_ID: EQUITY_UNIVERSE}

WEEKDAY_INDEX_BY_CODE = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4}

ALLOWED_INTENTS = {"hold", "rebalance"}
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
SYSTEM_EXECUTION_CONSTRAINTS = {
    "allow_negative_cash": False,
    "if_any_order_blocked": "stop_remaining_orders",
}
ACTIVE_SELECTION_STATUSES = {"active", "selected"}


def normalize_weekly_run_weekday(value: Any) -> str:
    if not value:
        return "MON"
    weekday = str(value).strip().upper()
    if weekday not in WEEKDAY_INDEX_BY_CODE:
        allowed = ", ".join(WEEKDAY_INDEX_BY_CODE)
        raise ValueError(f"weekly_run_weekday must be one of: {allowed}.")
    return weekday


def iso_week_key(value: date_type) -> str:
    iso_year, iso_week, _iso_weekday = value.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def equity_basket_agent_tools() -> list[ToolDefinition]:
    return [
        BuiltinTools.market.load_history_tables_summary(),
        BuiltinTools.market.last_price(),
        BuiltinTools.news.alpaca_news(),
    ]


def equity_basket_agent_system_prompt(symbols: str) -> str:
    return (
        f"Equity-only selection role: choose exactly one stock from the assigned basket_symbols ({symbols}). "
        "The selected stock receives target_weight 1.0 through downstream deterministic planning. "
        "You cannot place orders or size trades. Use market_load_history_tables_summary first for multi-symbol "
        "comparison. Treat rankings as separate evidence views; do not invent sector, style, safety, or "
        "cyclicality labels. If one symbol is clearly stronger across relevant rankings, select it without news. "
        "Use alpaca_news only when leading candidates are close, conflicting, or uncertain; when used, request "
        "news only for leading candidates. If news is unavailable, continue with rank-only evidence. "
        "Return strict JSON only. Do not place orders."
    )


def equity_basket_agent_task_prompt() -> str:
    return (
        "Review only the provided basket_symbols. First call market_load_history_tables_summary with "
        "symbols=basket_symbols, length=252, timestep='day', and top_n=10. Compare separate ranking views. "
        "If one symbol is clearly stronger across relevant rankings, select it without news. If leading "
        "candidates are close, conflicting, or uncertain, call alpaca_news for those leading candidates only. "
        "If alpaca_news is unavailable or errors, continue with rank-only evidence. Return exactly one strict "
        "JSON object with basket_id, target_weight, status, candidate_symbols, selected_symbol, and reason_brief. "
        "Use status='active'. candidate_symbols must copy the assigned basket_symbols exactly; do not replace it "
        "with a shortlist. selected_symbol must be one of basket_symbols."
    )


def _extract_first_json_object(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("Agent summary must be text.")

    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in agent summary.")

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

    raise ValueError("Unclosed JSON object in agent summary.")


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return value


def parse_json_summary(summary: str, label: str) -> dict[str, Any]:
    try:
        return json.loads(_extract_first_json_object(summary))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} JSON is invalid: {exc.msg}") from exc


def _normalize_order(order: Any) -> dict[str, Any]:
    order = _require_dict(order, "order")
    for field in ("sequence", "symbol", "side"):
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

    quantity_mode = str(order.get("quantity_mode", "shares")).strip().lower()
    if quantity_mode in REJECTED_SEMANTIC_QUANTITY_MODES:
        raise ValueError(
            "executable orders must use explicit shares quantity_mode; "
            f"got semantic quantity_mode: {quantity_mode}"
        )
    if quantity_mode not in ALLOWED_QUANTITY_MODES:
        raise ValueError(f"unsupported order quantity_mode: {quantity_mode}")

    if "quantity" not in order or order["quantity"] is None:
        raise ValueError("order quantity is required for shares quantity_mode.")
    if isinstance(order["quantity"], bool):
        raise ValueError("order quantity must be a positive whole-share integer.")
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
        raise ValueError(f"unsupported order_type for market-only equity strategy: {order_type}.")
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
        "time_in_force": str(order.get("time_in_force", "day")).strip().lower(),
    }


def normalize_execution_plan(plan: Any) -> dict[str, Any]:
    plan = _require_dict(plan, "execution_plan")
    if "schema_version" not in plan:
        raise ValueError("execution_plan schema_version is required.")
    schema_version = plan["schema_version"]
    if isinstance(schema_version, bool):
        raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")
    if isinstance(schema_version, (int, float)):
        if schema_version != 1:
            raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")
    elif isinstance(schema_version, str):
        if schema_version.strip() not in {"1", "1.0"}:
            raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")
    else:
        raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")

    intent = str(plan.get("intent") or "").strip().lower()
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
    if intent == "rebalance" and not orders:
        raise ValueError("execution_plan orders are required for rebalance intent.")

    normalized_orders = sorted((_normalize_order(order) for order in orders), key=lambda order: order["sequence"])
    order_sequences = [order["sequence"] for order in normalized_orders]
    if len(order_sequences) != len(set(order_sequences)):
        raise ValueError("duplicate order sequence.")

    buy_seen = False
    for order in normalized_orders:
        if order["side"] == "buy":
            buy_seen = True
        elif buy_seen and order["side"] == "sell":
            raise ValueError("execution_plan must place sell orders before buy orders.")

    return {
        "schema_version": 1,
        "intent": intent,
        "orders": normalized_orders,
        "constraints": dict(SYSTEM_EXECUTION_CONSTRAINTS),
    }


def execution_plan_execute_payload(plan: Any) -> dict[str, Any]:
    normalized_plan = normalize_execution_plan(plan)
    strict_orders = []
    for order in normalized_plan["orders"]:
        quantity = order["quantity"]
        if isinstance(quantity, bool) or not float(quantity).is_integer():
            raise ValueError("execution_plan_execute quantity must be a positive whole-share integer.")
        strict_orders.append(
            {
                "sequence": order["sequence"],
                "action": order["action"],
                "symbol": order["symbol"],
                "side": order["side"],
                "quantity_mode": order["quantity_mode"],
                "quantity": int(quantity),
                "asset_type": order["asset_type"],
                "order_type": order["order_type"],
                "time_in_force": order["time_in_force"],
            }
        )

    return {
        "schema_version": normalized_plan["schema_version"],
        "intent": normalized_plan["intent"],
        "orders": strict_orders,
    }


def validate_execution_plan_symbols(execution_plan: dict[str, Any], equity_report: dict[str, Any]) -> None:
    execution_plan = _require_dict(execution_plan, "execution_plan")
    if execution_plan.get("intent") == "hold":
        return

    equity_report = _require_dict(equity_report, "equity_report")
    status = str(equity_report.get("status") or "").strip().lower()
    selected_symbol = equity_report.get("selected_symbol")
    if status not in ACTIVE_SELECTION_STATUSES or not selected_symbol:
        raise ValueError("equity report must select an active symbol before buying.")

    expected_symbol = str(selected_symbol).strip().upper()
    for order in execution_plan.get("orders", []):
        order = _require_dict(order, "order")
        if str(order.get("side") or "").strip().lower() != "buy":
            continue
        symbol = str(order.get("symbol") or "").strip().upper()
        if symbol != expected_symbol:
            raise ValueError(f"execution_plan buy symbol {symbol} does not match selected equity symbol.")


def validate_execution_plan_matches_planner_result(strategy: Any, execution_plan: dict[str, Any]) -> None:
    planner_result = getattr(strategy, "_last_target_portfolio_planner_result", None)
    if not isinstance(planner_result, dict):
        raise ValueError("strategy must successfully call target_portfolio_to_execution_plan before execution.")
    planner_plan = normalize_execution_plan(planner_result.get("execution_plan"))
    execution_plan = normalize_execution_plan(execution_plan)
    if execution_plan != planner_plan:
        raise ValueError("execution_plan differs from target_portfolio_to_execution_plan result.")
