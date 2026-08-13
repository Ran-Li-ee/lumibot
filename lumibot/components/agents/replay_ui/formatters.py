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
    parts = [
        f"Loaded market history summaries for {_rows_label(count, 'symbol')}",
        f"{_rows_label(count, 'detailed symbol')} retained",
    ]
    requested_symbols = args.get("symbols")
    if isinstance(requested_symbols, list):
        parts.append(f"{_rows_label(len(requested_symbols), 'requested symbol')}")
    ranking_limit = result.get("ranking_limit")
    if isinstance(ranking_limit, int):
        parts.append(f"rankings capped at top {ranking_limit}")
    selection = _as_dict(result.get("universe_summary_selection"))
    candidate_count = selection.get("candidate_count_before_limit")
    summary_limit = result.get("universe_summary_limit")
    if isinstance(candidate_count, int) and isinstance(summary_limit, int):
        parts.append(f"{candidate_count} ranked candidates before the {summary_limit}-symbol detail limit")

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


def _orders_submit_and_confirm_order(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    submit_result = _nested_dict(result, "submit_result")
    submit_order = _nested_dict(submit_result, "order") if submit_result else {}
    symbol = _asset_symbol(submit_order) or _first_present(result, "symbol") or _first_present(args, "symbol")
    qty = (
        _first_present(submit_order, "qty", "quantity", "shares")
        or _first_present(result, "quantity", "qty", "shares")
        or _first_present(args, "quantity", "qty", "shares")
    )
    side = (
        _first_present(submit_order, "side", "action")
        or _first_present(result, "side", "action")
        or _first_present(args, "side", "action")
    )
    order_id = (
        _first_present(result, "identifier", "id", "order_id")
        or _first_present(submit_order, "identifier", "id", "order_id")
    )
    status = _first_present(result, "confirmation_status", "status")
    confirm_result = _nested_dict(result, "confirm_result")
    attempt_count = _first_present(confirm_result, "attempt_count", "attempts_count")
    blockers = result.get("blockers") if isinstance(result.get("blockers"), list) else []
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    order_text = f"{_text(side)} {_text(qty)} {_text(symbol)}"
    attempts_text = ""
    if attempt_count is not None:
        attempt_label = "attempt" if attempt_count == 1 else "attempts"
        attempts_text = f" after {_text(attempt_count)} {attempt_label}"
    if result.get("submitted") is True and result.get("confirmed") is True and result.get("can_continue") is True:
        return (
            f"Submitted and confirmed order {_text(order_id)} for {order_text}{attempts_text}. "
            f"Status: {_text(status)}. can_continue=true."
        )
    blocker_text = ""
    if blockers:
        first = _as_dict(blockers[0])
        code = _first_present(first, "code")
        message = _first_present(first, "message")
        blocker_text = f" Blocker: {_text(code)}"
        if message is not None:
            blocker_text += f" - {_text(message)}"
    elif warnings:
        blocker_text = f" Warning: {_text(warnings[0])}"
    return (
        f"Submit-and-confirm blocked for {order_text}{attempts_text}. "
        f"Status: {_text(status)}. can_continue=false.{blocker_text}"
    )


def _order_field(args: dict[str, Any], result: dict[str, Any], *keys: str) -> Any:
    order = _nested_dict(result, "order")
    for source in (order, result, args):
        value = _first_present(source, *keys)
        if value is not None:
            return value
    return None


def _orders_execute_order_id(result: dict[str, Any]) -> Any:
    submit_and_confirm_result = _nested_dict(result, "submit_and_confirm_result")
    submit_result = _nested_dict(submit_and_confirm_result, "submit_result")
    submit_order = _nested_dict(submit_result, "order")
    confirm_result = _nested_dict(submit_and_confirm_result, "confirm_result")
    for source in (result, submit_and_confirm_result, submit_order, confirm_result):
        value = _first_present(source, "identifier", "id", "order_id")
        if value is not None:
            return value
    return None


def _blocker_text(*sources: Any) -> str:
    for source in sources:
        source_dict = _as_dict(source)
        blockers = source_dict.get("blockers") if isinstance(source_dict.get("blockers"), list) else []
        if not blockers:
            continue
        blocker = _as_dict(blockers[0])
        code = _first_present(blocker, "code", "reason")
        message = _first_present(blocker, "message", "detail")
        parts = [str(value) for value in (code, message) if value is not None]
        if parts:
            return f" Blocker: {' - '.join(parts)}."
    return ""


def _orders_execute_order(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    preflight_result = _nested_dict(result, "preflight_result")
    submit_and_confirm_result = _nested_dict(result, "submit_and_confirm_result")

    symbol = _order_field(args, result, "symbol", "ticker")
    qty = _order_field(args, result, "qty", "quantity", "shares")
    side = _order_field(args, result, "side", "action")
    order_text = f"{_text(side)} {_text(qty)} {_text(symbol)}"

    sequence = _first_present(result, "sequence")
    if sequence is None:
        sequence = _first_present(args, "sequence")
    sequence_text = "" if sequence is None else f" sequence {_text(sequence)}"

    readiness = _first_present(preflight_result, "readiness", "status")
    if readiness is None:
        readiness = "ready" if preflight_result.get("can_submit") is True else "blocked"
    preflight_ready = (
        preflight_result.get("can_submit") is True
        or str(readiness).strip().lower() == "ready"
    )
    preflight_text = f"Preflight {_text(readiness)}"

    order_id = _orders_execute_order_id(result)
    order_id_text = "" if order_id is None else f" Order id: {_text(order_id)}."

    submitted = submit_and_confirm_result.get("submitted")
    confirmed = submit_and_confirm_result.get("confirmed")
    status = _first_present(submit_and_confirm_result, "confirmation_status", "status")

    if result.get("can_continue") is True and submitted is True and confirmed is True:
        status_text = "" if status is None else f" Status: {_text(status)}."
        return (
            f"Executed order{sequence_text}: {order_text}. "
            f"{preflight_text}; submitted=true; confirmed=true.{order_id_text}{status_text} "
            "can_continue=true."
        )

    blocker_text = _blocker_text(result, submit_and_confirm_result, preflight_result)
    if submitted is True and confirmed is not True:
        status_text = "" if status is None else f" Status: {_text(status)}."
        return (
            f"Execution submitted but not confirmed for order{sequence_text}: {order_text}. "
            f"{preflight_text}.{order_id_text}{status_text} can_continue=false.{blocker_text}"
        )

    if preflight_ready:
        status_text = "" if status is None else f" Status: {_text(status)}."
        return (
            f"Execution blocked during submit/confirm for order{sequence_text}: {order_text}. "
            f"{preflight_text}.{order_id_text}{status_text} can_continue=false.{blocker_text}"
        )

    return (
        f"Execution blocked before submit for order{sequence_text}: {order_text}. "
        f"{preflight_text}. can_continue=false.{blocker_text}"
    )


def _execution_plan_order_phrase(order: dict[str, Any]) -> str:
    symbol = _first_present(order, "symbol", "ticker")
    side = _first_present(order, "side", "action")
    quantity = _first_present(order, "quantity", "qty", "shares")
    return f"{_text(side)} {_text(symbol)} {_text(quantity)}"


def _execution_plan_execute(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    status = _first_present(result, "plan_status", "status")
    requested = _first_present(result, "orders_requested") or 0
    attempted = _first_present(result, "orders_attempted") or 0
    completed = _first_present(result, "orders_completed") or 0
    blocked = _first_present(result, "orders_blocked") or 0
    skipped = _first_present(result, "orders_skipped") or 0
    counts = (
        f"{_text(requested)} requested, {_text(attempted)} attempted, "
        f"{_text(completed)} completed, {_text(blocked)} blocked, {_text(skipped)} skipped."
    )

    if status == "invalid":
        return (
            "Execution plan invalid before submission: "
            f"{counts} No orders were submitted.{_blocker_text(result)}"
        )

    if status == "blocked":
        blocked_orders = result.get("blocked_orders") if isinstance(result.get("blocked_orders"), list) else []
        if blocked_orders:
            first_blocked = _as_dict(blocked_orders[0])
            sequence = _first_present(first_blocked, "sequence")
            order_text = _execution_plan_order_phrase(first_blocked)
            return (
                f"Execution plan blocked at sequence {_text(sequence)}: {order_text}. "
                f"{counts}{_blocker_text(first_blocked, result)}"
            )
        return f"Execution plan blocked: {counts}{_blocker_text(result)}"

    if status == "completed":
        completed_orders = result.get("completed_orders") if isinstance(result.get("completed_orders"), list) else []
        order_phrases = [_execution_plan_order_phrase(_as_dict(order)) for order in completed_orders[:6]]
        order_text = ""
        if order_phrases:
            more_count = len(completed_orders) - len(order_phrases)
            more_text = "" if more_count <= 0 else f", and {more_count} more"
            order_text = " Completed orders: " + ", ".join(order_phrases) + more_text + "."
        elif requested == 0:
            order_text = " No orders were submitted."

        account = _nested_dict(result, "final_account_snapshot")
        cash = _first_present(account, "cash", "cash_balance")
        cash_text = "" if cash is None else f" Final cash: {_text(cash)}."
        return f"Execution plan completed: {counts}{order_text}{cash_text}"

    if status is None:
        return f"Execution plan returned missing status: {counts}{_blocker_text(result)}"

    return f"Execution plan returned unknown status {_text(status)}: {counts}{_blocker_text(result)}"


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
    "orders_submit_and_confirm_order": _orders_submit_and_confirm_order,
    "orders_execute_order": _orders_execute_order,
    "execution_plan_execute": _execution_plan_execute,
    "remember_decision": _remember_decision,
}
