from __future__ import annotations

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


def _price_from_daily_close(strategy: Any, symbol: str) -> tuple[Decimal, str]:
    get_historical_prices = getattr(strategy, "get_historical_prices", None)
    if callable(get_historical_prices):
        try:
            bars = get_historical_prices(symbol, 1, timestep="day")
        except TypeError:
            bars = get_historical_prices(symbol, 1, "day")
        frame = getattr(bars, "pandas_df", None)
        if frame is None:
            frame = getattr(bars, "df", None)
        if frame is not None and not getattr(frame, "empty", True) and "close" in frame.columns:
            return _positive_price(frame["close"].iloc[-1], f"daily close for {symbol}"), "daily_close"

    get_last_price = getattr(strategy, "get_last_price", None)
    if not callable(get_last_price):
        raise ValueError(f"missing trailing stop check price for {symbol}")
    return _positive_price(get_last_price(symbol), f"last price for {symbol}"), "last_price_fallback"


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
    updated_state: dict[str, dict[str, Any]] = {}
    stop_checks: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []

    for symbol in sorted(current_positions):
        quantity = current_positions[symbol]
        check_price, price_source = _price_from_daily_close(strategy, symbol)
        previous = state.get(symbol) or {"entry_date": date, "peak_close": _float(check_price)}
        previous_peak = _positive_price(previous["peak_close"], f"previous peak close for {symbol}")
        peak_close = max(previous_peak, check_price)
        stop_price = peak_close * (Decimal("1") - pct)
        triggered = check_price <= stop_price
        planned_quantity = int(quantity)

        stop_checks.append(
            {
                "symbol": symbol,
                "quantity": _float(quantity),
                "holding_start_date": previous["entry_date"],
                "previous_peak_close": _float(previous_peak),
                "peak_close": _float(peak_close),
                "current_check_price": _float(check_price),
                "trailing_stop_pct": _float(pct),
                "stop_price": _float(stop_price),
                "triggered": triggered,
                "price_source": price_source,
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
            "entry_date": previous["entry_date"],
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
