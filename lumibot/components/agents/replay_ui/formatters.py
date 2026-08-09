"""Deterministic explanations for agent replay tool results."""

from __future__ import annotations

from typing import Any

GENERIC_EXPLANATION = "This tool returned structured data. Open the raw result to inspect details."


def explain_tool_result(
    tool_name: str,
    arguments: dict[str, Any],
    raw_result: Any,
    error: str | None,
) -> str:
    """Return a human-readable explanation for a tool result."""

    args = _as_dict(arguments)
    result = _as_dict(raw_result)

    if error or result.get("tool_error") is True:
        return f"{tool_name} failed: {_error_text(error, result)}"

    formatter = _FORMATTERS.get(tool_name)
    if formatter is None:
        return GENERIC_EXPLANATION

    return formatter(args, raw_result)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _first_present(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value is not None:
            return value
    return None


def _nested_dict(source: dict[str, Any], key: str) -> dict[str, Any]:
    return _as_dict(source.get(key))


def _row_count(raw_result: Any, *collection_keys: str) -> int | None:
    result = _as_dict(raw_result)
    explicit_count = _first_present(result, "row_count", "count", "total_count", "total")
    if isinstance(explicit_count, int):
        return explicit_count

    for key in collection_keys or ("rows", "items", "results", "data"):
        collection = result.get(key)
        if isinstance(collection, list):
            return len(collection)

    if isinstance(raw_result, list):
        return len(raw_result)

    return None


def _rows_label(count: int | None, singular: str = "row", plural: str | None = None) -> str:
    if count is None:
        return f"an unknown number of {plural or singular + 's'}"
    if count == 1:
        return f"1 {singular}"
    return f"{count} {plural or singular + 's'}"


def _error_text(error: str | None, result: dict[str, Any]) -> str:
    failure = error or _first_present(result, "error", "message", "detail", "exception")
    if failure is None:
        return "unknown error"
    if isinstance(failure, dict):
        nested_message = _first_present(failure, "message", "detail", "error")
        if nested_message is not None:
            return str(nested_message)
    return str(failure)


def _text(value: Any, fallback: str = "unknown") -> str:
    if value is None:
        return fallback
    return str(value)


def _number(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return f"{float(value):.2f}"
    return None


def _percent(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return f"{float(value) * 100:.2f}%"
    return None


def _short_list(values: list[Any], label: str) -> str:
    clean_values = [str(value) for value in values if value is not None]
    if not clean_values:
        return ""
    shown = ", ".join(clean_values[:3])
    suffix = "" if len(clean_values) <= 3 else f", and {len(clean_values) - 3} more"
    return f" {label}: {shown}{suffix}."


def _symbol_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    symbols = []
    for value in values:
        if isinstance(value, dict):
            value = _first_present(value, "symbol", "ticker")
        if value is not None:
            symbols.append(str(value))
    return symbols


def _collection(raw_result: Any, *keys: str) -> list[Any]:
    result = _as_dict(raw_result)
    for key in keys:
        value = result.get(key)
        if isinstance(value, list):
            return value
    if isinstance(raw_result, list):
        return raw_result
    return []


def _asset_symbol(source: dict[str, Any]) -> Any:
    asset = _nested_dict(source, "asset")
    return (
        _first_present(source, "symbol", "ticker")
        or _first_present(asset, "symbol", "ticker")
        or source.get("asset")
    )


def _account_portfolio(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    account_value = _first_present(result, "portfolio_value", "equity", "account_value", "value")
    cash = _first_present(result, "cash", "cash_balance")
    buying_power = _first_present(result, "buying_power", "available_funds")
    parts = []
    if account_value is not None:
        parts.append(f"account value {_text(account_value)}")
    if cash is not None:
        parts.append(f"cash {_text(cash)}")
    if buying_power is not None:
        parts.append(f"buying power {_text(buying_power)}")
    if not parts:
        return "Portfolio snapshot returned account-level balances."
    return f"Portfolio snapshot returned {', '.join(parts)}."


def _account_positions(args: dict[str, Any], raw_result: Any) -> str:
    positions = _collection(raw_result, "positions", "items", "rows")
    count = _row_count(raw_result, "positions", "items", "rows")
    symbols = [_asset_symbol(_as_dict(position)) for position in positions]
    return f"Positions snapshot returned {_rows_label(count, 'position')}." + _short_list(symbols, "Symbols")


def _orders_open_orders(args: dict[str, Any], raw_result: Any) -> str:
    orders = _collection(raw_result, "orders", "items", "rows")
    count = _row_count(raw_result, "orders", "items", "rows")
    symbols = [_asset_symbol(_as_dict(order)) for order in orders]
    return f"Open orders lookup returned {_rows_label(count, 'open order')}." + _short_list(symbols, "Symbols")


def _market_last_price(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    symbol = _first_present(result, "symbol", "ticker") or _first_present(args, "symbol", "ticker")
    price = _first_present(result, "price", "last_price", "close", "value")
    currency = _first_present(result, "currency", "currency_code")
    price_text = _text(price)
    if currency is not None:
        price_text = f"{price_text} {_text(currency)}"
    return f"market_last_price reported {_text(symbol)} latest visible price as {price_text}."


def _market_load_history_table(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    symbol = _first_present(result, "symbol", "ticker") or _first_present(args, "symbol", "ticker")
    table = _first_present(result, "table", "table_name") or _first_present(args, "table", "table_name")
    count = _row_count(raw_result, "rows", "data")
    text = (
        f"Loaded market history for {_text(symbol)} into table {_text(table)} "
        f"with {_rows_label(count)}."
    )
    summary_text = _computed_history_summary(result.get("computed_summary"))
    if summary_text:
        text += f" {summary_text}"
    return text


def _computed_history_summary(raw_summary: Any) -> str:
    summary = _as_dict(raw_summary)
    if not summary:
        return ""
    price = _as_dict(summary.get("price"))
    momentum = _as_dict(summary.get("momentum"))
    volume = _as_dict(summary.get("volume"))
    trend = _as_dict(summary.get("trend"))
    range_summary = _as_dict(summary.get("range"))
    risk = _as_dict(summary.get("risk"))
    scores = _as_dict(summary.get("scores"))
    parts = []
    latest_close = _number(price.get("latest_close"))
    if latest_close is not None:
        parts.append(f"latest close {latest_close}")
    for label, key in (
        ("5-bar return", "return_5"),
        ("10-bar return", "return_10"),
        ("20-bar return", "return_20"),
        ("21-bar return", "return_21"),
        ("60-bar return", "return_60"),
        ("63-bar return", "return_63"),
        ("120-bar return", "return_120"),
        ("126-bar return", "return_126"),
        ("252-bar return", "return_252"),
    ):
        value = _percent(momentum.get(key))
        if value is not None:
            parts.append(f"{label} {value}")
    value = _percent(scores.get("momentum_composite"))
    if value is not None:
        parts.append(f"momentum composite {value}")
    trend_alignment = scores.get("trend_alignment")
    if isinstance(trend_alignment, int | float) and not isinstance(trend_alignment, bool):
        parts.append(f"trend alignment {_text(trend_alignment)}")
    value = _number(scores.get("composite_score"))
    if value is not None:
        parts.append(f"composite score {value}")
    for label, key in (
        ("return63/vol20", "return_63_over_volatility_20"),
        ("return126/vol20", "return_126_over_volatility_20"),
    ):
        value = _number(scores.get(key))
        if value is not None:
            parts.append(f"{label} {value}")
    latest_volume = _number(volume.get("latest_volume"))
    if latest_volume is not None:
        parts.append(f"latest volume {latest_volume}")
    value = _number(volume.get("avg_volume_20"))
    if value is not None:
        parts.append(f"avg volume 20 {value}")
    value = _percent(volume.get("volume_vs_avg_20"))
    if value is not None:
        parts.append(f"volume vs avg20 {value}")
    for label, key in (("SMA20", "sma_20"), ("SMA50", "sma_50"), ("SMA200", "sma_200")):
        value = _number(trend.get(key))
        if value is not None:
            parts.append(f"{label} {value}")
    value = _percent(trend.get("price_vs_sma_20"))
    if value is not None:
        parts.append(f"vs SMA20 {value}")
    value = _percent(range_summary.get("distance_to_high_252"))
    if value is not None:
        parts.append(f"from 252-bar high {value}")
    value = _percent(range_summary.get("distance_to_low_252"))
    if value is not None:
        parts.append(f"from 252-bar low {value}")
    for label, key in (
        ("drawdown from 20-bar high", "drawdown_from_high_20"),
        ("drawdown from 60-bar high", "drawdown_from_high_60"),
        ("drawdown from 252-bar high", "drawdown_from_high_252"),
    ):
        value = _percent(range_summary.get(key))
        if value is not None:
            parts.append(f"{label} {value}")
    value = _percent(risk.get("max_drawdown_60"))
    if value is not None:
        parts.append(f"max drawdown 60 {value}")
    value = _percent(risk.get("volatility_20"))
    if value is not None:
        parts.append(f"20-bar volatility {value}")
    if not parts:
        return ""
    return "Computed summary: " + "; ".join(parts) + "."


def _market_load_history_tables_summary(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    count = len(_collection(raw_result, "universe_summary"))
    parts = [f"Loaded market history summaries for {_rows_label(count, 'symbol')}"]
    requested_symbols = args.get("symbols")
    if isinstance(requested_symbols, list):
        parts.append(f"{_rows_label(len(requested_symbols), 'requested symbol')}")

    rankings = _as_dict(result.get("rankings"))
    for label, key in (
        ("return_63", "by_return_63"),
        ("momentum_composite", "by_momentum_composite"),
        ("composite_score", "by_composite_score"),
    ):
        symbols = _symbol_list(rankings.get(key))
        if symbols:
            parts.append(f"{label}: {', '.join(symbols[:3])}")

    warnings = result.get("warnings")
    if isinstance(warnings, list) and warnings:
        parts.append(_rows_label(len(warnings), "warning"))

    return "; ".join(parts) + "."


def _duckdb_query(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    rows = _collection(raw_result, "rows", "data")
    count = _row_count(raw_result, "rows", "data")
    columns = result.get("columns")
    if not isinstance(columns, list) and rows and isinstance(rows[0], dict):
        columns = list(rows[0].keys())
    column_text = ""
    if isinstance(columns, list) and columns:
        column_text = " Columns: " + ", ".join(str(column) for column in columns[:5]) + "."
    sql = _first_present(args, "sql", "query")
    sql_text = "SQL" if sql is not None else "DuckDB query"
    return f"Ran {sql_text} and returned {_rows_label(count)}.{column_text}"


def _get_indicator(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    indicator = _first_present(result, "indicator", "name") or _first_present(args, "indicator", "name")
    symbol = _first_present(result, "symbol", "ticker") or _first_present(args, "symbol", "ticker")
    value = _first_present(result, "value", "latest", "result")
    value_text = "" if value is None else f" with value {_text(value)}"
    return f"Indicator {_text(indicator)} was calculated for {_text(symbol)}{value_text}."


def _get_indicators(args: dict[str, Any], raw_result: Any) -> str:
    indicators = _collection(raw_result, "indicators", "items", "rows", "results")
    count = _row_count(raw_result, "indicators", "items", "rows", "results")
    names = [_first_present(_as_dict(indicator), "indicator", "name") for indicator in indicators]
    return f"Indicator batch returned {_rows_label(count, 'indicator')}." + _short_list(names, "Indicators")


def _alpaca_news(args: dict[str, Any], raw_result: Any) -> str:
    articles = _collection(raw_result, "news", "articles", "items", "rows")
    count = _row_count(raw_result, "news", "articles", "items", "rows")
    symbols = _first_present(args, "symbols", "symbol", "ticker")
    target = "" if symbols is None else f" for {_text(symbols)}"
    headlines = [_first_present(_as_dict(article), "headline", "title") for article in articles]
    return f"Alpaca news returned {_rows_label(count, 'article')}{target}." + _short_list(headlines, "Headlines")


def _get_fred_snapshot(args: dict[str, Any], raw_result: Any) -> str:
    count = _row_count(raw_result, "series", "observations", "rows", "data")
    return f"FRED snapshot returned {_rows_label(count, 'series', 'series')}."


def _get_fred_latest(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    series_id = _first_present(result, "series_id", "series", "id") or _first_present(args, "series_id", "series")
    value = _first_present(result, "value", "latest", "observation")
    date = _first_present(result, "date", "as_of", "timestamp")
    date_text = "" if date is None else f" on {_text(date)}"
    return f"FRED latest value for {_text(series_id)} was {_text(value)}{date_text}."


def _get_filings(args: dict[str, Any], raw_result: Any) -> str:
    count = _row_count(raw_result, "filings", "items", "rows", "results")
    company = _first_present(args, "ticker", "symbol", "cik", "company")
    target = "" if company is None else f" for {_text(company)}"
    return f"SEC filings lookup returned {_rows_label(count, 'filing')}{target}."


def _search_filing(args: dict[str, Any], raw_result: Any) -> str:
    count = _row_count(raw_result, "matches", "items", "rows", "results")
    query = _first_present(args, "query", "term", "text")
    target = "" if query is None else f" for search text {_text(query)}"
    return f"Filing search returned {_rows_label(count, 'match', 'matches')}{target}."


def _orders_preflight_check(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    order = _nested_dict(result, "order")
    account = _nested_dict(result, "account")
    position = _nested_dict(result, "position")
    price = _nested_dict(result, "price")
    estimate = _nested_dict(result, "estimate")
    blockers = result.get("blockers") if isinstance(result.get("blockers"), list) else []

    symbol = _first_present(order, "symbol") or _first_present(result, "symbol") or _first_present(args, "symbol")
    qty = (
        _first_present(order, "qty", "quantity", "shares")
        or _first_present(result, "qty", "quantity", "shares")
        or _first_present(args, "qty", "quantity", "shares")
    )
    side = _first_present(order, "side", "action") or _first_present(result, "side", "action") or _first_present(
        args,
        "side",
        "action",
    )
    readiness = _first_present(result, "readiness", "status")
    if readiness is None:
        readiness = "ready" if result.get("can_submit") is True else "blocked"

    cash = _first_present(account, "cash", "available_cash")
    current_qty = _first_present(position, "quantity", "qty", "shares")
    last_price = _first_present(price, "last", "last_price", "price")
    estimated_value = _first_present(estimate, "estimated_order_value", "order_value", "estimated_value", "value")
    estimated_cash_after = _first_present(
        estimate,
        "estimated_cash_after_order",
        "cash_after",
        "estimated_cash_after",
    )
    order_text = f"{_text(side)} {_text(qty)} {_text(symbol)}"
    summary = (
        f"Preflight {_text(readiness)} for {order_text}: cash {_text(cash)}, "
        f"current quantity {_text(current_qty)}, last price {_text(last_price)}, "
        f"estimated value {_text(estimated_value)}, estimated cash after {_text(estimated_cash_after)}."
    )

    if blockers:
        blocker = _as_dict(blockers[0])
        code = _first_present(blocker, "code", "reason")
        message = _first_present(blocker, "message", "detail")
        summary += f" First blocker: {_text(code)} - {_text(message)}."

    return summary


def _orders_submit_order(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    order = _nested_dict(result, "order") or result
    symbol = _asset_symbol(order) or _asset_symbol(args)
    qty = _first_present(order, "qty", "quantity", "shares") or _first_present(args, "qty", "quantity", "shares")
    side = _first_present(order, "side", "action") or _first_present(args, "side", "action")
    status = _first_present(order, "status", "state")
    order_id = _first_present(order, "identifier", "id", "order_id")
    order_text = f"{_text(side)} {_text(qty)} {_text(symbol)}"
    status_text = "" if status is None else f" Status: {_text(status)}."
    id_text = "" if order_id is None else f" Order id: {_text(order_id)}."
    return f"Order submission returned for {order_text}.{status_text}{id_text}"


def _orders_confirm_order(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    order = _nested_dict(result, "order") or {}
    symbol = _asset_symbol(order) or _first_present(args, "symbol")
    qty = _first_present(order, "qty", "quantity", "shares") or _first_present(
        args,
        "expected_quantity",
        "quantity",
        "shares",
    )
    side = _first_present(order, "side", "action") or _first_present(args, "side", "action")
    order_id = _first_present(result, "identifier", "id", "order_id") or _first_present(
        order,
        "identifier",
        "id",
        "order_id",
    )
    status = _first_present(result, "confirmation_status", "status")
    attempt_count = _first_present(result, "attempt_count", "attempts_count")
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    order_text = f"{_text(side)} {_text(qty)} {_text(symbol)}"
    attempts_text = ""
    if attempt_count is not None:
        attempt_label = "attempt" if attempt_count == 1 else "attempts"
        attempts_text = f" after {_text(attempt_count)} {attempt_label}"
    if result.get("confirmed") is True:
        return (
            f"Confirmed order {_text(order_id)} for {order_text}{attempts_text}. "
            f"Status: {_text(status)}. can_continue=true."
        )
    warning_text = ""
    if warnings:
        warning_text = f" Warning: {_text(warnings[0])}"
    return (
        f"Confirmation blocked for order {_text(order_id)} for {order_text}{attempts_text}. "
        f"Status: {_text(status)}. can_continue=false.{warning_text}"
    )


def _remember_decision(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    key = _first_present(result, "key", "memory_key", "id") or _first_present(args, "key", "memory_key", "id")
    return f"Saved the agent decision for later replay context under {_text(key, 'a memory key')}."


_FORMATTERS = {
    "account_portfolio": _account_portfolio,
    "account_positions": _account_positions,
    "orders_open_orders": _orders_open_orders,
    "market_last_price": _market_last_price,
    "market_load_history_table": _market_load_history_table,
    "market_load_history_tables_summary": _market_load_history_tables_summary,
    "duckdb_query": _duckdb_query,
    "get_indicator": _get_indicator,
    "get_indicators": _get_indicators,
    "alpaca_news": _alpaca_news,
    "get_fred_snapshot": _get_fred_snapshot,
    "get_fred_latest": _get_fred_latest,
    "get_filings": _get_filings,
    "search_filing": _search_filing,
    "orders_preflight_check": _orders_preflight_check,
    "orders_submit_order": _orders_submit_order,
    "orders_confirm_order": _orders_confirm_order,
    "remember_decision": _remember_decision,
}
