import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from lumibot.components.agents.schemas import BoundTool, ToolDefinition

TOOL_NAME = "target_portfolio_to_execution_plan"
TARGET_WEIGHT_TOLERANCE = Decimal("0.000001")
BUY_SIZING_BUFFER_PCT = Decimal("0.02")
SIZING_PRICE_SOURCE_PREVIOUS_CLOSE = "previous_completed_daily_close"
SIZING_PRICE_SOURCE_LAST_PRICE_FALLBACK = "strategy_last_price_fallback"
QUOTE_SYMBOLS = {"USD", "CASH"}


@dataclass(frozen=True)
class SizingPrice:
    price: Decimal
    source: str
    datetime: str | None = None


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{label} must be numeric.") from exc
    if not result.is_finite():
        raise ValueError(f"{label} must be finite.")
    return result


def _float(value: Decimal) -> float:
    return float(value)


def _bars_dataframe(bars: Any) -> Any | None:
    if bars is None:
        return None
    frame = getattr(bars, "pandas_df", None)
    if frame is None:
        frame = getattr(bars, "df", None)
    if frame is None or not hasattr(frame, "empty") or frame.empty:
        return None
    return frame


def _last_row_datetime_text(frame: Any) -> str | None:
    try:
        row = frame.iloc[-1]
    except Exception:
        return None
    for column in ("date", "Date", "datetime", "Datetime"):
        if column in frame.columns:
            value = row[column]
            return str(value.date() if hasattr(value, "date") else value)
    try:
        index_value = frame.index[-1]
    except Exception:
        return None
    if index_value is None:
        return None
    return str(index_value.date() if hasattr(index_value, "date") else index_value)


def _symbol(value: Any) -> str:
    symbol = getattr(value, "symbol", value)
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol must be non-empty.")
    return symbol


def _position_symbol(position: Any) -> str:
    if hasattr(position, "symbol"):
        return _symbol(position.symbol)
    if hasattr(position, "asset"):
        return _symbol(position.asset)
    raise ValueError("position is missing symbol/asset.")


def _position_quantity(position: Any) -> Decimal:
    if not hasattr(position, "quantity"):
        raise ValueError("position is missing quantity.")
    return _decimal(position.quantity, "position quantity")


def _get_positions(strategy: Any) -> list[Any]:
    try:
        return list(strategy.get_positions(include_cash_positions=False))
    except TypeError:
        return list(strategy.get_positions())


def _get_cash(strategy: Any) -> Decimal:
    cash = _decimal(strategy.get_cash(), "cash")
    if cash < 0:
        raise ValueError("cash must be non-negative.")
    return cash


def _get_portfolio_value(strategy: Any) -> Decimal:
    value = _decimal(strategy.get_portfolio_value(), "portfolio value")
    if value <= 0:
        raise ValueError("portfolio value must be positive.")
    return value


def _finite_positive_price(value: Any, label: str) -> Decimal:
    price = _decimal(value, label)
    if price <= 0:
        raise ValueError(f"{label} must be positive.")
    return price


def _get_previous_completed_daily_close(strategy: Any, symbol: str) -> SizingPrice | None:
    get_historical_prices = getattr(strategy, "get_historical_prices", None)
    if get_historical_prices is None:
        return None

    try:
        bars = get_historical_prices(symbol, 1, "day")
    except TypeError:
        try:
            bars = get_historical_prices(symbol, 1, timestep="day")
        except Exception:
            return None
    except Exception:
        return None

    frame = _bars_dataframe(bars)
    if frame is None or "close" not in frame.columns:
        return None

    try:
        close_value = frame["close"].iloc[-1]
        price = _finite_positive_price(close_value, f"previous completed daily close for {symbol}")
    except Exception:
        return None
    return SizingPrice(
        price=price,
        source=SIZING_PRICE_SOURCE_PREVIOUS_CLOSE,
        datetime=_last_row_datetime_text(frame),
    )


def _get_last_price_fallback(strategy: Any, symbol: str) -> SizingPrice:
    raw_price = strategy.get_last_price(symbol)
    if raw_price is None:
        raise ValueError(f"missing sizing price for {symbol}")
    return SizingPrice(
        price=_finite_positive_price(raw_price, f"last price fallback for {symbol}"),
        source=SIZING_PRICE_SOURCE_LAST_PRICE_FALLBACK,
        datetime=None,
    )


def _get_sizing_price(strategy: Any, symbol: str) -> SizingPrice:
    previous_close = _get_previous_completed_daily_close(strategy, symbol)
    if previous_close is not None:
        return previous_close
    return _get_last_price_fallback(strategy, symbol)


def normalize_target_portfolio(target_portfolio: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(target_portfolio, list):
        raise ValueError("target_portfolio must be a list.")

    combined: dict[str, dict[str, Any]] = {}
    for item in target_portfolio:
        if not isinstance(item, dict):
            raise ValueError("target_portfolio item must be an object.")
        symbol = _symbol(item.get("symbol"))
        weight = _decimal(item.get("target_weight"), f"target_weight for {symbol}")
        if weight < 0:
            raise ValueError("target_weight must be non-negative.")
        if weight == 0:
            continue

        basket_id = str(item.get("basket_id") or "").strip()
        if symbol not in combined:
            combined[symbol] = {
                "symbol": symbol,
                "basket_id": basket_id or None,
                "target_weight": weight,
            }
        else:
            combined[symbol]["target_weight"] += weight
            if combined[symbol].get("basket_id") is None and basket_id:
                combined[symbol]["basket_id"] = basket_id

    total_weight = sum((item["target_weight"] for item in combined.values()), Decimal("0"))
    if total_weight > Decimal("1.0") + TARGET_WEIGHT_TOLERANCE:
        raise ValueError("target weights must not exceed 1.0.")

    return [
        {
            "symbol": item["symbol"],
            "basket_id": item.get("basket_id"),
            "target_weight": _float(item["target_weight"]),
        }
        for item in sorted(combined.values(), key=lambda target: target["symbol"])
    ]


def _current_positions_by_symbol(strategy: Any) -> dict[str, Decimal]:
    positions: dict[str, Decimal] = {}
    for position in _get_positions(strategy):
        symbol = _position_symbol(position)
        if symbol in QUOTE_SYMBOLS:
            continue
        quantity = _position_quantity(position)
        if quantity < 0:
            raise ValueError("negative position quantity is not supported.")
        if quantity == 0:
            continue
        positions[symbol] = positions.get(symbol, Decimal("0")) + quantity
    return {symbol: quantity for symbol, quantity in positions.items() if quantity != 0}


def _order(*, sequence: int, symbol: str, side: str, quantity: int) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "action": "submit_order",
        "symbol": symbol,
        "side": side,
        "quantity_mode": "shares",
        "quantity": quantity,
        "asset_type": "stock",
        "order_type": "market",
        "time_in_force": "day",
    }


def _diagnostic_row(
    *,
    symbol: str,
    basket_id: str | None,
    current_quantity: Decimal,
    sizing_price: SizingPrice,
    portfolio_value: Decimal,
    target_weight: Decimal,
    planned_side: str | None,
    planned_quantity: int,
    reason_code: str,
) -> dict[str, Any]:
    current_price = sizing_price.price
    current_value = current_quantity * current_price
    current_weight = current_value / portfolio_value
    target_value = target_weight * portfolio_value
    desired_buy_value = max(target_value - current_value, Decimal("0"))
    effective_buy_target_value = (
        desired_buy_value * (Decimal("1") - BUY_SIZING_BUFFER_PCT)
        if desired_buy_value > 0
        else Decimal("0")
    )
    return {
        "symbol": symbol,
        "basket_id": basket_id,
        "current_quantity": _float(current_quantity),
        "current_price": _float(current_price),
        "sizing_price": _float(sizing_price.price),
        "sizing_price_source": sizing_price.source,
        "sizing_price_datetime": sizing_price.datetime,
        "current_value": _float(current_value),
        "current_weight": _float(current_weight),
        "target_weight": _float(target_weight),
        "target_value": _float(target_value),
        "desired_buy_value": _float(desired_buy_value),
        "effective_buy_target_value": _float(effective_buy_target_value),
        "buy_sizing_buffer_pct": _float(BUY_SIZING_BUFFER_PCT),
        "delta_value": _float(target_value - current_value),
        "planned_side": planned_side,
        "planned_quantity": planned_quantity,
        "reason_code": reason_code,
    }


def target_portfolio_to_execution_plan(
    strategy: Any,
    *,
    date: str | None = None,
    target_portfolio: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_targets = normalize_target_portfolio(target_portfolio)
    target_by_symbol = {
        item["symbol"]: _decimal(item["target_weight"], f"target_weight for {item['symbol']}")
        for item in normalized_targets
    }
    basket_by_symbol = {item["symbol"]: item.get("basket_id") for item in normalized_targets}

    cash_before = _get_cash(strategy)
    portfolio_value = _get_portfolio_value(strategy)
    current_positions = _current_positions_by_symbol(strategy)
    relevant_symbols = sorted(set(current_positions) | set(target_by_symbol))
    sizing_prices = {symbol: _get_sizing_price(strategy, symbol) for symbol in relevant_symbols}

    diagnostics: list[dict[str, Any]] = []
    sell_candidates: list[tuple[int, str, int]] = []
    buy_candidates: list[tuple[int, str, Decimal, str]] = []
    warnings: list[str] = []
    estimated_sell_proceeds = Decimal("0")

    for symbol in relevant_symbols:
        current_quantity = current_positions.get(symbol, Decimal("0"))
        current_price = sizing_prices[symbol].price
        target_weight = target_by_symbol.get(symbol, Decimal("0"))
        current_value = current_quantity * current_price
        target_value = target_weight * portfolio_value
        delta_value = target_value - current_value

        planned_side = None
        planned_quantity = 0
        reason_code = "already_at_target"

        if current_quantity > 0 and target_weight == 0:
            planned_quantity = int(current_quantity)
            if planned_quantity > 0:
                planned_side = "sell"
                reason_code = "exit_removed_symbol"
                estimated_sell_proceeds += Decimal(planned_quantity) * current_price
                sell_candidates.append((1, symbol, planned_quantity))
                residual_quantity = current_quantity - Decimal(planned_quantity)
                if residual_quantity > 0:
                    warnings.append(f"{symbol}: full exit leaves fractional residual smaller than one share.")
            else:
                reason_code = "rounding_no_sell"
                warnings.append(f"{symbol}: exit quantity is smaller than one share.")
        elif current_value > target_value:
            reason_code = "reduce_overweight"
            planned_quantity = int(math.floor((current_value - target_value) / current_price))
            if planned_quantity > 0:
                planned_quantity = min(planned_quantity, int(current_quantity))
                planned_side = "sell"
                estimated_sell_proceeds += Decimal(planned_quantity) * current_price
                sell_candidates.append((2, symbol, planned_quantity))
            else:
                warnings.append(f"{symbol}: overweight delta is smaller than one share.")
        elif delta_value > 0:
            reason_code = "buy_new_target" if current_quantity == 0 else "increase_underweight"
            buy_group = 3 if current_quantity == 0 else 4
            buy_candidates.append((buy_group, symbol, delta_value, reason_code))

        diagnostics.append(
            _diagnostic_row(
                symbol=symbol,
                basket_id=basket_by_symbol.get(symbol),
                current_quantity=current_quantity,
                sizing_price=sizing_prices[symbol],
                portfolio_value=portfolio_value,
                target_weight=target_weight,
                planned_side=planned_side,
                planned_quantity=planned_quantity,
                reason_code=reason_code,
            )
        )

    orders: list[dict[str, Any]] = []
    sequence = 1
    for _group, symbol, quantity in sorted(sell_candidates, key=lambda item: (item[0], item[1])):
        orders.append(_order(sequence=sequence, symbol=symbol, side="sell", quantity=quantity))
        sequence += 1

    projected_cash = cash_before + estimated_sell_proceeds
    estimated_buy_cost = Decimal("0")
    planned_buy_quantities: dict[str, int] = {}
    diagnostics_by_symbol = {row["symbol"]: row for row in diagnostics}
    for _group, symbol, desired_buy_value, _reason_code in sorted(
        buy_candidates, key=lambda item: (item[0], item[1])
    ):
        current_price = sizing_prices[symbol].price
        effective_buy_value = desired_buy_value * (Decimal("1") - BUY_SIZING_BUFFER_PCT)
        spendable = min(effective_buy_value, projected_cash)
        quantity = int(math.floor(spendable / current_price))
        if quantity <= 0:
            diagnostic = diagnostics_by_symbol[symbol]
            diagnostic["planned_side"] = None
            diagnostic["planned_quantity"] = 0
            if desired_buy_value < current_price:
                diagnostic["reason_code"] = "rounding_no_buy"
                warnings.append(f"{symbol}: underweight delta is smaller than one share.")
            else:
                diagnostic["reason_code"] = "insufficient_cash_no_buy"
                warnings.append(f"{symbol}: projected cash is insufficient to buy one share.")
            continue

        cost = Decimal(quantity) * current_price
        projected_cash -= cost
        estimated_buy_cost += cost
        planned_buy_quantities[symbol] = quantity
        orders.append(_order(sequence=sequence, symbol=symbol, side="buy", quantity=quantity))
        sequence += 1

    for row in diagnostics:
        if row["symbol"] in planned_buy_quantities:
            row["planned_side"] = "buy"
            row["planned_quantity"] = planned_buy_quantities[row["symbol"]]

    intent = "rebalance" if orders else "hold"
    return {
        "schema_version": "1.0",
        "date": date,
        "target_portfolio": normalized_targets,
        "current_vs_target": diagnostics,
        "cash_projection": {
            "cash_before": _float(cash_before),
            "estimated_sell_proceeds": _float(estimated_sell_proceeds),
            "estimated_buy_cost": _float(estimated_buy_cost),
            "cash_after_estimate": _float(projected_cash),
            "buy_sizing_buffer_pct": _float(BUY_SIZING_BUFFER_PCT),
            "negative_cash_allowed": False,
        },
        "execution_plan": {"schema_version": 1, "intent": intent, "orders": orders},
        "warnings": warnings,
    }


def make_target_portfolio_to_execution_plan_tool() -> ToolDefinition:
    description = (
        "Convert a target portfolio into a strict market/day execution_plan. "
        "Use this after choosing target symbols and target weights. The tool reads current positions, "
        "cash, portfolio value, and deterministic sizing prices from the strategy. In daily backtests, "
        "buy sizing uses the previous completed daily close when available and applies a default 2% "
        "buy sizing buffer. The buffer reduces planned buy quantity only; it is not a hard execution "
        "price cap. The tool returns current_vs_target diagnostics and an execution_plan. Do not "
        "manually edit the execution_plan returned by this tool."
    )
    metadata = {"kind": "portfolio_transition_planner", "replay_on_cache": True}

    def binder(strategy: Any, manager: Any) -> BoundTool:
        def planner_tool(*, date: str | None = None, target_portfolio: list[dict[str, Any]]) -> dict[str, Any]:
            result = target_portfolio_to_execution_plan(
                strategy,
                date=date,
                target_portfolio=target_portfolio,
            )
            strategy._last_target_portfolio_planner_result = result
            return result

        return BoundTool(
            name=TOOL_NAME,
            description=description,
            function=planner_tool,
            source="local",
            metadata=metadata,
        )

    return ToolDefinition(name=TOOL_NAME, description=description, binder=binder, metadata=metadata)
