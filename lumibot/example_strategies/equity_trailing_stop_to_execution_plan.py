from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from lumibot.example_strategies.target_portfolio_to_execution_plan import (
    _current_positions_by_symbol,
    _float,
    _order,
)

DEFAULT_TRAILING_STOP_PCT = 0.20


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric.") from exc
    if not result.is_finite():
        raise ValueError(f"{label} must be finite.")
    return result


def _positive_price(value: Any, label: str) -> Decimal:
    price = _decimal(value, label)
    if price <= 0:
        raise ValueError(f"{label} must be positive.")
    return price


def _bars_dataframe(bars: Any) -> Any | None:
    if bars is None:
        return None
    if hasattr(bars, "empty") and hasattr(bars, "columns"):
        return bars
    frame = getattr(bars, "pandas_df", None)
    if frame is None:
        frame = getattr(bars, "df", None)
    if frame is None or not hasattr(frame, "empty") or frame.empty:
        return None
    return frame


def _date_from_value(value: Any) -> date_type | None:
    if value is None:
        return None
    if hasattr(value, "date"):
        try:
            return value.date()
        except (TypeError, ValueError):
            pass
    text = str(value).strip()
    if not text:
        return None
    try:
        return date_type.fromisoformat(text[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return None


def _close_points_from_frame(
    frame: Any,
    *,
    start_date: date_type,
    end_date: date_type,
    symbol: str,
) -> list[tuple[date_type | None, Decimal]]:
    close_column = None
    for candidate in ("close", "Close"):
        if candidate in frame.columns:
            close_column = candidate
            break
    if close_column is None:
        return []

    date_column = None
    for candidate in ("Date", "date", "datetime", "Datetime", "timestamp"):
        if candidate in frame.columns:
            date_column = candidate
            break

    points: list[tuple[date_type | None, Decimal]] = []
    for index, row in frame.iterrows():
        point_date = _date_from_value(row[date_column]) if date_column else _date_from_value(index)
        if point_date is not None and (point_date < start_date or point_date > end_date):
            continue
        points.append((point_date, _positive_price(row[close_column], f"daily close for {symbol}")))
    return points


def _daily_close_window(
    strategy: Any,
    symbol: str,
    *,
    start_date: date_type,
    end_date: date_type,
) -> tuple[list[tuple[date_type | None, Decimal]], str]:
    get_historical_prices = getattr(strategy, "get_historical_prices", None)
    if callable(get_historical_prices):
        # get_historical_prices is length-based in Lumibot. Request enough daily bars
        # to cover calendar gaps, then filter to the actual holding window.
        length = max((end_date - start_date).days + 8, 1)
        try:
            bars = get_historical_prices(symbol, length, timestep="day")
        except TypeError:
            bars = get_historical_prices(symbol, length, "day")
        frame = _bars_dataframe(bars)
        if frame is not None:
            points = _close_points_from_frame(
                frame,
                start_date=start_date,
                end_date=end_date,
                symbol=symbol,
            )
            if points:
                return points, "daily_close_window"

    get_last_price = getattr(strategy, "get_last_price", None)
    if not callable(get_last_price):
        raise ValueError(f"missing trailing stop check price for {symbol}")
    return [(None, _positive_price(get_last_price(symbol), f"last price for {symbol}"))], "last_price_fallback"


def _normalized_state(position_state: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    for raw_symbol, raw_payload in dict(position_state or {}).items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol or not isinstance(raw_payload, dict):
            continue
        entry_date = str(raw_payload.get("entry_date") or "").strip()
        peak_close = raw_payload.get("peak_close")
        if not entry_date or peak_close is None:
            continue
        state[symbol] = {
            "entry_date": entry_date,
            "peak_close": _float(_positive_price(peak_close, f"peak_close for {symbol}")),
        }
    return state


def trailing_stop_to_execution_plan(
    strategy: Any,
    *,
    date: str,
    trailing_stop_pct: float = DEFAULT_TRAILING_STOP_PCT,
    position_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pct = _decimal(trailing_stop_pct, "trailing_stop_pct")
    if pct <= 0 or pct >= 1:
        raise ValueError("trailing_stop_pct must be between 0 and 1.")

    current_positions = _current_positions_by_symbol(strategy)
    state = _normalized_state(position_state)
    end_date = date_type.fromisoformat(date)
    updated_state: dict[str, dict[str, Any]] = {}
    stop_checks: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []

    for symbol in sorted(current_positions):
        quantity = current_positions[symbol]
        previous = state.get(symbol)
        entry_date_text = str(previous.get("entry_date") if previous else date)
        start_date = date_type.fromisoformat(entry_date_text)
        close_points, price_source = _daily_close_window(
            strategy,
            symbol,
            start_date=start_date,
            end_date=end_date,
        )
        check_date, check_price = close_points[-1]
        window_peak = max(price for _, price in close_points)
        previous_peak = (
            _positive_price(previous["peak_close"], f"previous peak close for {symbol}")
            if previous
            else window_peak
        )
        peak_close = max(previous_peak, window_peak)
        stop_price = peak_close * (Decimal("1") - pct)
        triggered = check_price <= stop_price
        planned_quantity = int(quantity)

        stop_checks.append(
            {
                "symbol": symbol,
                "quantity": _float(quantity),
                "holding_start_date": entry_date_text,
                "previous_peak_close": _float(previous_peak),
                "peak_close": _float(peak_close),
                "current_check_price": _float(check_price),
                "trailing_stop_pct": _float(pct),
                "stop_price": _float(stop_price),
                "triggered": triggered,
                "price_source": price_source,
                "history_start_date": start_date.isoformat(),
                "history_end_date": check_date.isoformat() if check_date is not None else date,
                "history_bar_count": len(close_points),
            }
        )

        if triggered and planned_quantity > 0:
            orders.append(
                _order(
                    sequence=len(orders) + 1,
                    symbol=symbol,
                    side="sell",
                    quantity=planned_quantity,
                )
            )
            continue

        updated_state[symbol] = {
            "entry_date": entry_date_text,
            "peak_close": _float(peak_close),
            "last_check_date": date,
            "last_check_price": _float(check_price),
        }

    return {
        "schema_version": "1.0",
        "date": date,
        "trailing_stop_pct": _float(pct),
        "stop_checks": stop_checks,
        "updated_position_state": updated_state,
        "execution_plan": {
            "schema_version": 1,
            "intent": "rebalance" if orders else "hold",
            "orders": orders,
        },
        "warnings": [],
    }
