from __future__ import annotations

import json
import math
from datetime import date as date_type
from typing import Any

from lumibot.components.agents.builtins import BuiltinTools
from lumibot.components.agents.schemas import BoundTool, ToolDefinition

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

EQUITY_ALPACA_NEWS_DESCRIPTION = (
    "Fetch Alpaca/Benzinga news articles for leading stock candidates in the assigned basket_symbols. "
    "This is symbol/date-window retrieval, not keyword search: arguments are optional symbols comma-list, "
    "start, end, limit <= 50, include_content, exclude_contentless, page_token, optional content_max_chars, "
    "and sort. In backtests, only use articles at or before the current simulated datetime; if end is omitted, "
    "LumiBot uses the current simulated datetime, and future end times are clamped to avoid look-ahead bias. "
    "Use only after stage evidence is close, conflicting, or catalyst-sensitive. Query only the leading candidate "
    "stock tickers from basket_symbols. Do not broaden beyond basket_symbols or add unrelated tickers. First scan "
    "with include_content=False to read headlines, summaries, timestamps, URLs, sources, and symbols. If a "
    "story matters, call again for the same or narrower window with include_content=True and usually "
    "exclude_contentless=True to read the full article body. Full content is not truncated unless you set "
    "content_max_chars. Use page_token when next_page_token is returned. Do not trade from one weak or noisy "
    "article."
)

EQUITY_HISTORY_SUMMARY_DESCRIPTION = (
    "Load visible daily equity history for assigned basket_symbols into DuckDB and return an equity-only "
    "momentum-stage cross-symbol summary. This wrapper defaults to evidence_profile='momentum_stage', "
    "benchmark_symbols=['QQQ','SPY'], and length=378. It ranks candidates by freshness, smoothness, "
    "near-high strength, volume confirmation, and benchmark-relative strength, with reference fields for "
    "stale or overextended candidates. Use it first for equity basket selection; use DuckDB SQL only as "
    "targeted follow-up when the summary is missing or contradictory."
)

ALLOWED_INTENTS = {"hold", "rebalance", "risk_exit"}
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
        equity_history_summary_tool(),
        BuiltinTools.market.last_price(),
        equity_alpaca_news_tool(),
    ]


def equity_history_summary_tool() -> ToolDefinition:
    base_tool = BuiltinTools.market.load_history_tables_summary()

    def _bind_equity_history_summary(strategy: Any, manager: Any) -> BoundTool:
        bound = base_tool.binder(strategy, manager)

        def load_history_tables_summary(
            *,
            symbols: list[str],
            length: int = 378,
            timestep: str = "day",
            asset_type: str = "stock",
            table_prefix: str | None = None,
            include_after_hours: bool = True,
            top_n: int = 10,
            candidate_summary_limit: int = 25,
            evidence_profile: str = "momentum_stage",
            benchmark_symbols: list[str] | None = None,
        ) -> dict[str, Any]:
            if benchmark_symbols is None:
                benchmark_symbols = ["QQQ", "SPY"]
            return bound.function(
                symbols=symbols,
                length=length,
                timestep=timestep,
                asset_type=asset_type,
                table_prefix=table_prefix,
                include_after_hours=include_after_hours,
                top_n=top_n,
                candidate_summary_limit=candidate_summary_limit,
                evidence_profile=evidence_profile,
                benchmark_symbols=benchmark_symbols,
            )

        metadata = dict(bound.metadata or {})
        metadata["scope"] = "equity_only"
        return BoundTool(
            name=bound.name,
            description=EQUITY_HISTORY_SUMMARY_DESCRIPTION,
            function=load_history_tables_summary,
            source=bound.source,
            metadata=metadata,
        )

    metadata = dict(base_tool.metadata or {})
    metadata["scope"] = "equity_only"
    return ToolDefinition(
        name=base_tool.name,
        description=EQUITY_HISTORY_SUMMARY_DESCRIPTION,
        binder=_bind_equity_history_summary,
        metadata=metadata,
    )


def equity_alpaca_news_tool() -> ToolDefinition:
    base_tool = BuiltinTools.news.alpaca_news()

    def _bind_equity_alpaca_news(strategy: Any, manager: Any) -> BoundTool:
        bound = base_tool.binder(strategy, manager)
        metadata = dict(bound.metadata or {})
        metadata["scope"] = "equity_only"
        return BoundTool(
            name=bound.name,
            description=EQUITY_ALPACA_NEWS_DESCRIPTION,
            function=bound.function,
            source=bound.source,
            metadata=metadata,
        )

    metadata = dict(base_tool.metadata or {})
    metadata["scope"] = "equity_only"
    return ToolDefinition(
        name=base_tool.name,
        description=EQUITY_ALPACA_NEWS_DESCRIPTION,
        binder=_bind_equity_alpaca_news,
        metadata=metadata,
    )


EQUITY_EVIDENCE_INTERPRETATION_POLICY = (
    "Evidence Interpretation Policy: "
    "Use momentum-stage evidence to find confirmed but not exhausted strength. "
    "Treat rankable groups as support votes, not automatic answers. "
    "Prefer candidates appearing in multiple stage groups: freshness, smoothness, near-high strength, "
    "volume confirmation, and benchmark-relative strength. "
    "Use reference fields as risk checks: stale top-decile age, MA50 extension, ATR extension, "
    "single-day jump concentration, and recent overheat versus intermediate momentum. "
    "Reference fields are not simple ranking fields and higher is not always better. "
    "If stage evidence is clear, select without news. "
    "Use alpaca_news only for leading candidates when evidence is close, conflicting, or catalyst-sensitive."
)


def equity_basket_agent_system_prompt(symbols: str) -> str:
    return (
        f"Equity-only selection role: identify credible stock candidates from the assigned basket_symbols ({symbols}). "
        "Return credible candidates supported by evidence. "
        "Your job is to select candidates with confirmed but not exhausted momentum. "
        "selected_symbols should contain between 3 and 10 unique symbols. "
        "You cannot place orders or size trades. You cannot calculate exact per-symbol target weights, calculate "
        "stops or take profits, or create exit orders. "
        "Existing positions are protected by a deterministic local exit-risk engine. "
        "Do not calculate stop-loss, do not calculate take-profit, and do not create exit orders. "
        "Do not calculate exact per-symbol target weights. The downstream deterministic constructor chooses the "
        "final holding count and target weights from candidate evidence plus local rank/warning/volatility data. "
        "Use market_load_history_tables_summary first for multi-symbol comparison and request "
        "evidence_profile='momentum_stage'. "
        f"{EQUITY_EVIDENCE_INTERPRETATION_POLICY} "
        "Do not invent sector, style, or category labels not provided by tools. "
        "If news is unavailable, continue with stage evidence. Return strict JSON only. Do not place orders."
    )


def qqq_historical_equity_basket_agent_system_prompt(symbols: str) -> str:
    return (
        "Equity-only selection role: identify credible stock candidates from the QQQ historical constituent "
        f"universe provided in basket_symbols ({symbols}). "
        "Return credible candidates supported by evidence. "
        "Your job is to select candidates with confirmed but not exhausted momentum. "
        "The provided basket_symbols represent the QQQ historical constituent universe available for the "
        "current backtest date. selected_symbols should contain between 3 and 10 unique symbols. "
        "You cannot place orders or size trades. You cannot calculate exact per-symbol target weights, calculate "
        "stops or take profits, or create exit orders. "
        "Existing positions are protected by a deterministic local exit-risk engine. "
        "Do not calculate stop-loss, do not calculate take-profit, and do not create exit orders. "
        "Do not calculate exact per-symbol target weights. The downstream deterministic constructor chooses "
        "the final holding count and target weights from candidate evidence plus local rank/warning/volatility "
        "data. Use market_load_history_tables_summary first for multi-symbol comparison and request "
        "evidence_profile='momentum_stage'. "
        f"{EQUITY_EVIDENCE_INTERPRETATION_POLICY} "
        "Do not invent sector, style, or category labels not provided by tools. "
        "QQQ membership is not automatically superior. "
        "Do not choose based on index weight alone. "
        "If news is unavailable, continue with stage evidence. Use only symbols in the provided "
        "basket_symbols and do not add symbols outside the provided universe. Return strict JSON only. "
        "Do not place orders."
    )


def equity_basket_agent_task_prompt() -> str:
    return (
        "Review only the provided basket_symbols. First call market_load_history_tables_summary with "
        "symbols=basket_symbols, length=378, timestep='day', top_n=10, candidate_summary_limit=25, "
        "evidence_profile='momentum_stage', and benchmark_symbols=['QQQ', 'SPY']. "
        "Build an ordered list of credible candidates from momentum-stage evidence and select confirmed but not "
        "exhausted momentum. Prefer candidates appearing in multiple stage groups: freshness, smoothness, "
        "near-high strength, volume confirmation, and benchmark-relative strength. selected_symbols must contain "
        "between 3 and 10 unique symbols from basket_symbols. Prefer fewer selected_symbols when evidence is "
        "narrow and cleaner, and more when credible evidence is broad. Prefer candidates supported by multiple "
        "stage groups and not blocked by strong warning flags. Use reference fields as risk checks: stale "
        "top-decile age, MA50 extension, ATR extension, single-day jump concentration, and recent overheat "
        "versus intermediate momentum. Reference fields are not simple ranking fields and higher is not always "
        "better; use them to avoid stale, overextended, or single-jump candidates. "
        "Do not calculate exact per-symbol target weights; the deterministic constructor calculates the final "
        "holding count and target weights. "
        "Use alpaca_news only when leading candidates are close, conflicting, overextended, stale, or "
        "catalyst-sensitive; otherwise skip news. If alpaca_news is unavailable or errors, continue with "
        "stage evidence. "
        "Return exactly one strict JSON object with basket_id, status, candidate_symbols, selected_symbols, "
        "and reason_brief. Use status='active'. candidate_symbols must copy the assigned basket_symbols "
        "exactly; do not replace it with a shortlist."
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
    if intent in {"rebalance", "risk_exit"} and not orders:
        raise ValueError("execution_plan orders are required for rebalance or risk_exit intent.")

    normalized_orders = sorted((_normalize_order(order) for order in orders), key=lambda order: order["sequence"])
    order_sequences = [order["sequence"] for order in normalized_orders]
    if len(order_sequences) != len(set(order_sequences)):
        raise ValueError("duplicate order sequence.")
    if intent == "risk_exit" and any(order["side"] != "sell" for order in normalized_orders):
        raise ValueError("risk_exit intent only supports sell orders.")

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
    selected_symbols = equity_report.get("selected_symbols")
    if status not in ACTIVE_SELECTION_STATUSES or not isinstance(selected_symbols, list):
        raise ValueError("equity report must select active symbols before buying.")

    expected_symbols = {
        str(symbol).strip().upper()
        for symbol in selected_symbols
        if isinstance(symbol, str) and str(symbol).strip()
    }
    if not expected_symbols:
        raise ValueError("equity report must select active symbols before buying.")

    for order in execution_plan.get("orders", []):
        order = _require_dict(order, "order")
        if str(order.get("side") or "").strip().lower() != "buy":
            continue
        symbol = str(order.get("symbol") or "").strip().upper()
        if symbol not in expected_symbols:
            raise ValueError(f"execution_plan buy symbol {symbol} does not match selected equity symbols.")


def validate_execution_plan_matches_planner_result(strategy: Any, execution_plan: dict[str, Any]) -> None:
    planner_result = getattr(strategy, "_last_target_portfolio_planner_result", None)
    if not isinstance(planner_result, dict):
        raise ValueError("strategy must successfully call target_portfolio_to_execution_plan before execution.")
    planner_plan = normalize_execution_plan(planner_result.get("execution_plan"))
    execution_plan = normalize_execution_plan(execution_plan)
    if execution_plan != planner_plan:
        raise ValueError("execution_plan differs from target_portfolio_to_execution_plan result.")
