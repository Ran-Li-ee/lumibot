import inspect
import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from importlib import import_module
from typing import Any, Literal

from .asset_resolution import resolve_asset_and_quote
from .docs_tools import search_lumibot_docs
from .schemas import BoundTool, ToolDefinition
from .tool_context import append_agent_tool_context_list_item, current_agent_tool_context

AssetTypeArg = Literal["stock", "option", "future", "cont_future", "forex", "crypto", "index", "multileg", "us_equity"]
OrderSideArg = Literal[
    "buy",
    "sell",
    "buy_to_open",
    "buy_to_close",
    "sell_to_open",
    "sell_to_close",
    "sell_short",
    "buy_to_cover",
]
OrderTypeArg = Literal["market", "limit", "stop", "stop_limit", "trailing_stop", "smart_limit"]
TimeInForceArg = Literal["day", "gtc", "gtd"]
OptionRightArg = Literal["call", "put"]
MultilegPriceStyleArg = Literal["market", "best", "mid", "fastest"]
NewsSortArg = Literal["asc", "desc"]

ORDERS_PREFLIGHT_CHECK_DESCRIPTION = (
    "Inspect whether one explicit execution_plan order appears ready to submit. "
    "Checks cash, portfolio value, current position, open orders, and latest price; "
    "returns readiness, blockers, warnings, account/position/open-order/price snapshots, and estimates. "
    "This tool is read-only and does not submit, cancel, modify, or confirm orders."
)
ORDERS_SUBMIT_AND_CONFIRM_ORDER_DESCRIPTION = (
    "Submit one explicit execution_plan order and confirm that same submitted order before returning. "
    "Use this after orders_preflight_check returns can_submit=true for the same order. "
    "This tool mutates trading state. It does not perform research, calculate quantities, change order fields, "
    "or execute more than one order. If the result has can_continue=false, stop later orders and report the blocker."
)
ORDERS_EXECUTE_ORDER_DESCRIPTION = (
    "Execute exactly one explicit execution_plan order end to end. "
    "The tool checks readiness, submits the order if ready, confirms the submitted order, "
    "and returns whether execution can continue. This tool mutates trading state. "
    "It does not perform research, calculate quantities, change order fields, execute multiple orders, "
    "or execute a full plan. If can_continue=false, stop later orders and report the blocker."
)
EXECUTION_PLAN_EXECUTE_DESCRIPTION = (
    "Execute one complete strict execution_plan in sequence order. "
    "This tool validates the plan, executes each order through readiness checks, submission, and confirmation, "
    "enforces no-negative-cash behavior for buys, stops on the first blocker, and returns a concise execution "
    "summary to the model while full audit details are recorded in trace/replay. "
    "This tool mutates trading state. It does not generate, repair, reorder, optimize, or modify the plan. "
    "Pass the execution_plan exactly as provided by the upstream planner. "
    "If plan_status is blocked or invalid, do not call lower-level tools; summarize where execution stopped and why."
)

COMMON_INDICATORS = [
    "sma",
    "ema",
    "rsi",
    "macd",
    "bbands",
    "atr",
    "vwap",
    "vwma",
    "roc",
    "stoch",
]


def _agent_memory_context_kwargs() -> dict[str, Any]:
    context = current_agent_tool_context()
    kwargs: dict[str, Any] = {}
    for key in ("agent_name", "model_call_id"):
        value = context.get(key)
        if value:
            kwargs[key] = value
    return kwargs


class _LazyModule:
    """Read-only proxy that imports the target module on first attribute access.

    object.__setattr__ touches the internal slots directly; callers should only
    read attributes through this proxy so module mutation is not hidden here.
    """

    __slots__ = ("_module_name", "_module")

    def __init__(self, module_name: str):
        object.__setattr__(self, "_module_name", module_name)
        object.__setattr__(self, "_module", None)

    def _load(self):
        module = object.__getattribute__(self, "_module")
        if module is None:
            module = import_module(object.__getattribute__(self, "_module_name"))
            object.__setattr__(self, "_module", module)
        return module

    def __getattr__(self, name):
        return getattr(self._load(), name)

    def __setattr__(self, name, value):
        if name in {"_module_name", "_module"}:
            object.__setattr__(self, name, value)
            return
        setattr(self._load(), name, value)

    def __delattr__(self, name):
        if name in {"_module_name", "_module"}:
            object.__delattr__(self, name)
            return
        delattr(self._load(), name)


requests = _LazyModule("requests")


def _requests():
    return requests


def _asset_class():
    from lumibot.entities import Asset

    return Asset


def _parse_datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _coerce_same_timezone(value: datetime, reference: datetime) -> datetime:
    if value.tzinfo is None and reference.tzinfo is not None:
        return value.replace(tzinfo=reference.tzinfo)
    if value.tzinfo is not None and reference.tzinfo is None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    if value.tzinfo is not None and reference.tzinfo is not None:
        return value.astimezone(reference.tzinfo)
    return value


def _coerce_expiration(expiration: Any) -> Any:
    if isinstance(expiration, str) and expiration.strip():
        try:
            return datetime.fromisoformat(expiration).date()
        except ValueError:
            return expiration
    return expiration


def _require_non_empty_text(name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required and must be a non-empty string.")
    return text


def _require_single_symbol_text(name: str, value: Any) -> str:
    text = _require_non_empty_text(name, value)
    if "," in text:
        raise ValueError(
            f"{name} must be one tradable symbol, not a comma-separated list. "
            "Call this tool once per symbol."
        )
    return text


def _require_positive_int(name: str, value: Any) -> int:
    try:
        parsed = int(value)
    except Exception as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than 0.")
    return parsed


def _require_positive_number(name: str, value: Any) -> float:
    try:
        parsed = float(value)
    except Exception as exc:
        raise ValueError(f"{name} must be a positive number.") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{name} must be a finite number greater than 0.")
    return parsed


def _agent_tool_calls_for_current_run() -> list[dict[str, Any]]:
    context = current_agent_tool_context()
    calls = context.get("tool_calls")
    if isinstance(calls, list):
        return [call for call in calls if isinstance(call, dict)]
    return []


def _tool_call_was_successful(call: dict[str, Any]) -> bool:
    return call.get("ok") is not False


def _has_successful_tool_call(tool_name: str) -> bool:
    return any(
        call.get("tool_name") == tool_name and _tool_call_was_successful(call)
        for call in _agent_tool_calls_for_current_run()
    )


def _has_successful_market_last_price_for_symbol(symbol: str) -> bool:
    normalized_symbol = str(symbol or "").strip().upper()
    for call in _agent_tool_calls_for_current_run():
        if call.get("tool_name") != "market_last_price" or not _tool_call_was_successful(call):
            continue
        arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        if str(arguments.get("symbol") or "").strip().upper() == normalized_symbol:
            return True
    return False


def _normalized_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalized_order_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalized_order_quantity(value: Any) -> float | None:
    try:
        parsed = float(value)
    except Exception:
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _order_readiness_signature(
    *,
    symbol: Any,
    side: Any,
    quantity: Any,
    asset_type: Any,
    order_type: Any,
    time_in_force: Any,
) -> dict[str, Any] | None:
    normalized_symbol = _normalized_symbol(symbol)
    normalized_quantity = _normalized_order_quantity(quantity)
    if not normalized_symbol or normalized_quantity is None:
        return None
    return {
        "symbol": normalized_symbol,
        "side": _normalized_order_text(side),
        "quantity": normalized_quantity,
        "asset_type": _normalized_order_text(asset_type),
        "order_type": _normalized_order_text(order_type),
        "time_in_force": _normalized_order_text(time_in_force),
    }


def _record_successful_order_readiness(
    *,
    symbol: str,
    side: str,
    quantity: Any,
    asset_type: str,
    order_type: str,
    time_in_force: str,
    source: str,
) -> None:
    normalized_symbol = _normalized_symbol(symbol)
    signature = _order_readiness_signature(
        symbol=normalized_symbol,
        side=side,
        quantity=quantity,
        asset_type=asset_type,
        order_type=order_type,
        time_in_force=time_in_force,
    )
    if signature is None:
        return
    append_agent_tool_context_list_item(
        "order_readiness",
        {
            **signature,
            "source": source,
            "ok": True,
        },
    )


def _readiness_signature_matches(item: dict[str, Any], signature: dict[str, Any]) -> bool:
    item_quantity = _normalized_order_quantity(item.get("quantity"))
    signature_quantity = _normalized_order_quantity(signature.get("quantity"))
    if item_quantity is None or signature_quantity is None:
        return False
    return (
        _normalized_symbol(item.get("symbol")) == signature["symbol"]
        and _normalized_order_text(item.get("side")) == signature["side"]
        and math.isclose(item_quantity, signature_quantity, rel_tol=0.0, abs_tol=1e-12)
        and _normalized_order_text(item.get("asset_type")) == signature["asset_type"]
        and _normalized_order_text(item.get("order_type")) == signature["order_type"]
        and _normalized_order_text(item.get("time_in_force")) == signature["time_in_force"]
    )


def _has_successful_order_readiness_for_order(
    *,
    symbol: str,
    side: Any,
    quantity: Any,
    asset_type: Any,
    order_type: Any,
    time_in_force: Any,
) -> bool:
    signature = _order_readiness_signature(
        symbol=symbol,
        side=side,
        quantity=quantity,
        asset_type=asset_type,
        order_type=order_type,
        time_in_force=time_in_force,
    )
    if signature is None:
        return False
    context = current_agent_tool_context()
    readiness = context.get("order_readiness")
    if not isinstance(readiness, list):
        return False
    for item in readiness:
        if not isinstance(item, dict):
            continue
        if item.get("ok") is not True:
            continue
        if _readiness_signature_matches(item, signature):
            item["ok"] = False
            item["consumed"] = True
            return True
    return False


def _require_agent_order_readiness(
    symbol: str,
    *,
    side: Any | None = None,
    quantity: Any | None = None,
    asset_type: Any | None = None,
    order_type: Any | None = None,
    time_in_force: Any | None = None,
) -> None:
    context = current_agent_tool_context()
    if not bool(context.get("enforce_order_readiness")):
        return
    if (
        side is not None
        and quantity is not None
        and asset_type is not None
        and order_type is not None
        and time_in_force is not None
        and _has_successful_order_readiness_for_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            asset_type=asset_type,
            order_type=order_type,
            time_in_force=time_in_force,
        )
    ):
        return
    missing: list[str] = []
    if not _has_successful_tool_call("account_portfolio"):
        missing.append("account_portfolio")
    if not _has_successful_tool_call("account_positions"):
        missing.append("account_positions")
    if not _has_successful_market_last_price_for_symbol(symbol):
        missing.append(f"market_last_price(symbol={symbol!r})")
    if missing:
        raise ValueError(
            "ORDER_READINESS_REQUIRED: Before submitting an order, inspect readiness in this same agent run. "
            "Prefer orders_preflight_check when available; otherwise call "
            f"{', '.join(missing)} in this same agent run. "
            "Agents must inspect cash, portfolio value, positions, and the latest price for the ordered asset "
            "before trading."
        )


def _agent_negative_cash_guard_enabled() -> bool:
    value = os.environ.get("LUMIBOT_AGENT_ALLOW_NEGATIVE_CASH", "")
    return value.strip().lower() not in {"1", "true", "yes", "on"}


def _extract_order_cash_check_price(value: Any) -> tuple[float | None, str | None]:
    source = None
    raw_price = value
    if isinstance(value, dict):
        raw_price = (
            value.get("price")
            if value.get("price") is not None
            else value.get("sizing_price")
            if value.get("sizing_price") is not None
            else value.get("last_price")
        )
        source = value.get("source") or value.get("sizing_price_source") or value.get("price_source")
    elif hasattr(value, "price"):
        raw_price = value.price
        source = getattr(value, "source", None)
    price = _finite_positive_price(raw_price)
    return price, str(source) if source is not None else None


def _strategy_order_cash_check_price_snapshot(
    strategy: Any,
    *,
    asset: Any,
    quote: Any = None,
    exchange: str | None = None,
) -> dict[str, Any] | None:
    hook = getattr(strategy, "get_agent_order_cash_check_price", None)
    if not callable(hook):
        return None

    symbol = getattr(asset, "symbol", asset)
    call_attempts = (
        ((), {"asset": asset, "quote": quote, "exchange": exchange}),
        ((asset,), {"quote": quote, "exchange": exchange}),
        ((), {"asset": asset}),
        ((symbol,), {}),
    )
    try:
        signature = inspect.signature(hook)
    except (TypeError, ValueError):
        signature = None

    if signature is not None:
        for args, kwargs in call_attempts:
            try:
                signature.bind(*args, **kwargs)
            except TypeError:
                continue
            value = hook(*args, **kwargs)
            price, source = _extract_order_cash_check_price(value)
            if price is not None:
                return {
                    "last_price": price,
                    "price_source": source or "strategy_order_cash_check_price",
                }
    else:
        for args, kwargs in call_attempts:
            try:
                value = hook(*args, **kwargs)
            except TypeError:
                continue
            price, source = _extract_order_cash_check_price(value)
            if price is not None:
                return {
                    "last_price": price,
                    "price_source": source or "strategy_order_cash_check_price",
                }

    return None


def _estimate_buy_order_cash_requirement(
    strategy: Any,
    *,
    asset: Any,
    quote: Any,
    quantity: float,
    asset_type: str,
    order_type: str,
    limit_price: float | None,
    stop_price: float | None,
    stop_limit_price: float | None,
    exchange: str | None,
) -> float | None:
    if str(asset_type).strip().lower() not in {"stock", "us_equity"}:
        return None

    price: float | None = None
    if order_type in {"limit", "smart_limit"} and limit_price is not None:
        price = float(limit_price)
    elif order_type == "stop_limit":
        price = float(stop_limit_price if stop_limit_price is not None else limit_price)
    elif order_type == "stop" and stop_price is not None:
        price = float(stop_price)
    elif order_type == "market":
        strategy_price = _strategy_order_cash_check_price_snapshot(
            strategy,
            asset=asset,
            quote=quote,
            exchange=exchange,
        )
        if strategy_price is not None:
            price = float(strategy_price["last_price"])
        else:
            raw_price = strategy.get_last_price(asset, quote=quote, exchange=exchange)
            if raw_price is not None:
                price = float(raw_price)
        if price is None:
            raise ValueError(
                "NEGATIVE_CASH_CHECK_UNAVAILABLE: orders_submit_order cannot verify affordability because "
                f"market_last_price for {getattr(asset, 'symbol', asset)!r} returned None."
            )

    if price is None:
        return None
    if not math.isfinite(price) or price <= 0:
        raise ValueError(
            "NEGATIVE_CASH_CHECK_UNAVAILABLE: orders_submit_order cannot verify affordability because "
            f"estimated order price is invalid: {price!r}."
        )
    return quantity * price


def _require_no_negative_cash_after_buy(
    strategy: Any,
    *,
    asset: Any,
    quote: Any,
    quantity: float,
    side: str,
    asset_type: str,
    order_type: str,
    limit_price: float | None,
    stop_price: float | None,
    stop_limit_price: float | None,
    exchange: str | None,
) -> None:
    if not _agent_negative_cash_guard_enabled():
        return
    if str(side).strip().lower() not in {"buy", "buy_to_open", "buy_to_cover"}:
        return
    if not callable(getattr(strategy, "get_cash", None)) or not callable(
        getattr(strategy, "get_last_price", None)
    ):
        return

    requirement = _estimate_buy_order_cash_requirement(
        strategy,
        asset=asset,
        quote=quote,
        quantity=quantity,
        asset_type=asset_type,
        order_type=order_type,
        limit_price=limit_price,
        stop_price=stop_price,
        stop_limit_price=stop_limit_price,
        exchange=exchange,
    )
    if requirement is None:
        return

    cash = float(strategy.get_cash())
    if not math.isfinite(cash):
        raise ValueError("NEGATIVE_CASH_CHECK_UNAVAILABLE: current cash is not finite.")
    projected_cash = cash - requirement
    if projected_cash < 0:
        raise ValueError(
            "NEGATIVE_CASH_NOT_ALLOWED: orders_submit_order rejected the buy order because estimated cost "
            f"{requirement:.2f} would exceed available cash {cash:.2f} and leave cash {projected_cash:.2f}. "
            "Reduce quantity, sell first, or explicitly enable negative cash outside the agent tool guard."
        )


def _asset_to_dict(asset: Any) -> dict[str, Any] | str:
    if asset is None:
        return "None"
    expiration = getattr(asset, "expiration", None)
    if isinstance(expiration, (datetime, date)):
        expiration_value = expiration.strftime("%Y-%m-%d")
    else:
        expiration_value = expiration
    return {
        "symbol": getattr(asset, "symbol", None),
        "asset_type": getattr(asset, "asset_type", None),
        "expiration": expiration_value,
        "strike": getattr(asset, "strike", None),
        "right": getattr(asset, "right", None),
        "multiplier": getattr(asset, "multiplier", None),
    }


def _position_to_dict(position: Any) -> dict[str, Any]:
    asset = getattr(position, "asset", None)
    asset_payload = _asset_to_dict(asset)
    quantity = getattr(position, "quantity", None)
    try:
        quantity = float(quantity)
    except Exception:
        quantity = quantity
    return {
        "asset": asset_payload,
        "quantity": quantity,
        "avg_fill_price": _jsonable(getattr(position, "avg_fill_price", None)),
        "current_price": _jsonable(getattr(position, "current_price", None)),
        "market_value": _jsonable(getattr(position, "market_value", None)),
        "pnl": _jsonable(
            getattr(position, "pnl", None)
            if hasattr(position, "pnl")
            else getattr(position, "unrealized_pnl", None)
        ),
        "pnl_percent": _jsonable(getattr(position, "pnl_percent", None)),
    }


def _safe_call(obj: Any, method_name: str, default: Any = None) -> Any:
    method = getattr(obj, method_name, None)
    if not callable(method):
        return default
    try:
        return method()
    except Exception:
        return default


def _order_filled_quantity(order: Any) -> Any:
    transactions = getattr(order, "transactions", None) or []
    if transactions:
        total = 0.0
        for transaction in transactions:
            try:
                total += float(getattr(transaction, "quantity", 0) or 0)
            except Exception:
                return None
        return total
    if _safe_call(order, "is_filled", False):
        quantity = getattr(order, "quantity", None)
        try:
            return float(quantity)
        except Exception:
            return quantity
    return None


def _order_to_dict(order: Any) -> dict[str, Any]:
    asset = getattr(order, "asset", None)
    asset_payload = _asset_to_dict(asset)
    quantity = getattr(order, "quantity", None)
    try:
        quantity = float(quantity)
    except Exception:
        quantity = quantity
    return {
        "identifier": _jsonable(getattr(order, "identifier", None)),
        "status": _jsonable(getattr(order, "status", None)),
        "side": _jsonable(getattr(order, "side", None)),
        "asset": asset_payload,
        "quantity": quantity,
        "filled_quantity": _jsonable(_order_filled_quantity(order)),
        "avg_fill_price": _jsonable(
            getattr(order, "avg_fill_price", None)
            if getattr(order, "avg_fill_price", None) is not None
            else _safe_call(order, "get_fill_price", None)
        ),
        "is_active": _jsonable(_safe_call(order, "is_active", None)),
        "is_filled": _jsonable(_safe_call(order, "is_filled", None)),
        "is_canceled": _jsonable(_safe_call(order, "is_canceled", None)),
        "order_type": _jsonable(getattr(order, "order_type", None)),
        "time_in_force": _jsonable(getattr(order, "time_in_force", None)),
        "limit_price": _jsonable(getattr(order, "limit_price", None)),
        "stop_price": _jsonable(getattr(order, "stop_price", None)),
        "stop_limit_price": _jsonable(getattr(order, "stop_limit_price", None)),
        "trail_price": _jsonable(getattr(order, "trail_price", None)),
        "trail_percent": _jsonable(getattr(order, "trail_percent", None)),
    }


def _options_helper_for_strategy(strategy: Any) -> Any:
    helper = getattr(strategy, "_agent_options_helper", None)
    if helper is None:
        from lumibot.components.options_helper import OptionsHelper

        helper = OptionsHelper(strategy)
        setattr(strategy, "_agent_options_helper", helper)
    return helper


def _underlying_asset(
    strategy: Any,
    *,
    symbol: str,
    asset_type: Literal["stock", "index"] = "stock",
) -> Any:
    symbol = _require_single_symbol_text("symbol", symbol)
    asset, _ = resolve_asset_and_quote(strategy, symbol=symbol, asset_type=asset_type)
    return asset


def _option_asset(
    strategy: Any,
    *,
    symbol: str,
    expiration: str,
    strike: float,
    right: OptionRightArg,
) -> Any:
    symbol = _require_single_symbol_text("symbol", symbol)
    expiration_value = _coerce_expiration(_require_non_empty_text("expiration", expiration))
    if not isinstance(expiration_value, date):
        raise ValueError("expiration must use YYYY-MM-DD format.")
    strike_value = _require_positive_number("strike", strike)
    asset, _ = resolve_asset_and_quote(
        strategy,
        symbol=symbol,
        asset_type="option",
        expiration=expiration_value,
        strike=strike_value,
        right=right,
    )
    return asset


def _parse_option_legs(strategy: Any, legs_json: str, *, time_in_force: TimeInForceArg = "day") -> list[Any]:
    raw = _require_non_empty_text("legs_json", legs_json)
    try:
        legs = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"legs_json must be valid JSON: {exc}") from exc
    if not isinstance(legs, list) or len(legs) < 2:
        raise ValueError("legs_json must decode to a list containing at least two option legs.")

    orders: list[Any] = []
    for index, leg in enumerate(legs):
        if not isinstance(leg, dict):
            raise ValueError(f"legs_json item {index} must be a JSON object.")
        try:
            symbol = _require_single_symbol_text("symbol", leg.get("symbol"))
            expiration = _require_non_empty_text("expiration", leg.get("expiration"))
            strike = _require_positive_number("strike", leg.get("strike"))
            right = str(leg.get("right") or "").strip().lower()
            side = str(leg.get("side") or "").strip().lower()
            quantity = _require_positive_number("quantity", leg.get("quantity"))
        except ValueError as exc:
            raise ValueError(f"Invalid option leg at index {index}: {exc}") from exc
        if right not in {"call", "put"}:
            raise ValueError(f"Invalid option leg at index {index}: right must be 'call' or 'put'.")
        if side not in {
            "buy",
            "sell",
            "buy_to_open",
            "buy_to_close",
            "sell_to_open",
            "sell_to_close",
        }:
            raise ValueError(
                f"Invalid option leg at index {index}: side must describe a buy or sell action for an option contract."
            )
        option = _option_asset(
            strategy,
            symbol=symbol,
            expiration=expiration,
            strike=strike,
            right=right,
        )
        orders.append(strategy.create_order(option, quantity, side, time_in_force=time_in_force))
    return orders


def _bind_positions(strategy: Any, manager: Any) -> BoundTool:
    def positions() -> dict[str, Any]:
        return {
            "positions": [_position_to_dict(position) for position in strategy.get_positions(include_cash_positions=True)],
            "as_of": strategy.get_datetime().isoformat(),
        }

    return BoundTool(
        name="account_positions",
        description=(
            "Return current positions as structured data. "
            "Each entry includes exact asset fields, signed quantity, average fill price, current price, market value, and P&L fields when the broker or backtest provides them. "
            "For options, signed quantity is authoritative: quantity > 0 is a long contract and must use sell_to_close to reduce it; quantity < 0 is a short contract and must use buy_to_close to reduce it. "
            "Use expiration, strike, right, signed quantity, and average fill price to reconstruct and manage an existing multi-leg position. Never report the option portfolio as flat while any option entry has nonzero quantity. "
            "Use this before trading to understand current exposure, whether a symbol is already held, and whether the current portfolio is concentrated. "
            "Example: call this before rotating into a new symbol so you can compare it against what is already owned."
        ),
        function=positions,
        metadata={"kind": "builtin"},
    )


def _bind_portfolio(strategy: Any, manager: Any) -> BoundTool:
    def portfolio() -> dict[str, Any]:
        return {
            "cash": strategy.get_cash(),
            "portfolio_value": strategy.get_portfolio_value(),
            "datetime": strategy.get_datetime().isoformat(),
        }

    return BoundTool(
        name="account_portfolio",
        description=(
            "Return current cash and total portfolio value for sizing decisions. "
            "Use this before placing orders when you need to calculate a sensible whole-share quantity or compare a risky asset against a defensive parking asset. "
            "Example: call this before buying TQQQ so you can size a near-fully-invested position intentionally instead of buying one share."
        ),
        function=portfolio,
        metadata={"kind": "builtin"},
    )


def _bind_last_price(strategy: Any, manager: Any) -> BoundTool:
    def last_price(
        *,
        symbol: str,
        asset_type: AssetTypeArg = "stock",
        expiration: str | None = None,
        strike: float | None = None,
        right: str | None = None,
        quote_symbol: str | None = None,
        exchange: str | None = None,
    ) -> dict[str, Any]:
        symbol = _require_single_symbol_text("symbol", symbol)
        asset, quote = resolve_asset_and_quote(
            strategy,
            symbol=symbol,
            asset_type=asset_type,
            expiration=_coerce_expiration(expiration),
            strike=strike,
            right=right,
            quote_symbol=quote_symbol,
        )
        price = strategy.get_last_price(asset, quote=quote, exchange=exchange)
        return {
            "symbol": symbol,
            "asset_type": asset_type,
            "price": float(price) if price is not None else None,
            "datetime": strategy.get_datetime().isoformat(),
        }

    return BoundTool(
        name="market_last_price",
        description=(
            "Get the current last price for one asset. "
            "Arguments: symbol, asset_type, optional expiration/strike/right for derivatives, optional quote_symbol, optional exchange. "
            "Valid asset_type values: stock, option, future, cont_future, forex, crypto, index, multileg, us_equity. "
            "The symbol argument must be one tradable symbol, not a comma-separated universe; call once per symbol when comparing multiple assets. "
            "Use stock for normal equities. Do not pass economic series ids such as DCOILWTICO, FEDFUNDS, or M2SL as market symbols; use macro/FRED tools for those instead. "
            "Example: market_last_price(symbol='SPY', asset_type='stock')."
        ),
        function=last_price,
        metadata={"kind": "builtin", "replay_on_cache": True},
    )


def _bind_options_get_chain(strategy: Any, manager: Any) -> BoundTool:
    def get_chain(
        *,
        symbol: str,
        underlying_asset_type: Literal["stock", "index"] = "stock",
        include_strikes: bool = False,
    ) -> dict[str, Any]:
        underlying = _underlying_asset(strategy, symbol=symbol, asset_type=underlying_asset_type)
        chains = strategy.get_chains(underlying)
        if not chains:
            return {
                "symbol": symbol.upper(),
                "underlying_asset_type": underlying_asset_type,
                "available": False,
                "call_expirations": [],
                "put_expirations": [],
            }

        chain_root = chains.get("Chains", {}) if hasattr(chains, "get") else {}
        call_map = chain_root.get("CALL", {}) if isinstance(chain_root, dict) else {}
        put_map = chain_root.get("PUT", {}) if isinstance(chain_root, dict) else {}

        def side_payload(side_map: Any) -> dict[str, Any]:
            if not isinstance(side_map, dict):
                return {}
            result: dict[str, Any] = {}
            for expiration in sorted(str(value) for value in side_map.keys()):
                strikes = side_map.get(expiration) or []
                normalized_strikes = sorted({float(value) for value in strikes})
                entry: dict[str, Any] = {
                    "strike_count": len(normalized_strikes),
                    "min_strike": normalized_strikes[0] if normalized_strikes else None,
                    "max_strike": normalized_strikes[-1] if normalized_strikes else None,
                }
                if include_strikes:
                    entry["strikes"] = normalized_strikes
                result[expiration] = entry
            return result

        calls = side_payload(call_map)
        puts = side_payload(put_map)
        return {
            "symbol": symbol.upper(),
            "underlying_asset_type": underlying_asset_type,
            "available": True,
            "multiplier": _jsonable(chains.get("Multiplier") if hasattr(chains, "get") else None),
            "exchange": _jsonable(chains.get("Exchange") if hasattr(chains, "get") else None),
            "call_expirations": list(calls.keys()),
            "put_expirations": list(puts.keys()),
            "calls": calls,
            "puts": puts,
            "strikes_included": include_strikes,
            "datetime": strategy.get_datetime().isoformat(),
        }

    return BoundTool(
        name="options_get_chain",
        description=(
            "Retrieve the option chain available for one underlying through LumiBot's configured broker or backtest data source. "
            "Arguments: symbol, optional underlying_asset_type='stock' or 'index', optional include_strikes. "
            "The default compact response lists call and put expirations plus strike counts and ranges. Set include_strikes=true only when you need every strike for every expiration. "
            "Use this before choosing option contracts. Never invent an expiration or strike that is absent from this result. "
            "Example: options_get_chain(symbol='SPY', include_strikes=false)."
        ),
        function=get_chain,
        metadata={"kind": "builtin", "replay_on_cache": True},
    )


def _bind_options_get_strikes(strategy: Any, manager: Any) -> BoundTool:
    def get_strikes(
        *,
        symbol: str,
        expiration: str,
        right: OptionRightArg,
        underlying_asset_type: Literal["stock", "index"] = "stock",
    ) -> dict[str, Any]:
        underlying = _underlying_asset(strategy, symbol=symbol, asset_type=underlying_asset_type)
        expiration_value = _coerce_expiration(_require_non_empty_text("expiration", expiration))
        if not isinstance(expiration_value, date):
            raise ValueError("expiration must use YYYY-MM-DD format.")
        chains = strategy.get_chains(underlying)
        if not chains:
            strikes: list[float] = []
        elif hasattr(chains, "strikes"):
            strikes = chains.strikes(expiration_value, right.upper()) or []
        else:
            strikes = (
                chains.get("Chains", {})
                .get(right.upper(), {})
                .get(expiration_value.isoformat(), [])
            )
        normalized = sorted({float(value) for value in strikes})
        return {
            "symbol": symbol.upper(),
            "expiration": expiration_value.isoformat(),
            "right": right,
            "strikes": normalized,
            "count": len(normalized),
            "datetime": strategy.get_datetime().isoformat(),
        }

    return BoundTool(
        name="options_get_strikes",
        description=(
            "Return every listed strike for one exact underlying, expiration, and option right. "
            "Arguments: symbol, expiration in YYYY-MM-DD, right='call' or 'put', optional underlying_asset_type='stock' or 'index'. "
            "First use options_get_chain to choose a listed expiration, then use this result when selecting exact contracts. "
            "Example: options_get_strikes(symbol='SPY', expiration='2026-09-18', right='put')."
        ),
        function=get_strikes,
        metadata={"kind": "builtin", "replay_on_cache": True},
    )


def _bind_options_get_greeks(strategy: Any, manager: Any) -> BoundTool:
    def get_greeks(
        *,
        symbol: str,
        expiration: str,
        strike: float,
        right: OptionRightArg,
        underlying_price: float | None = None,
        option_price: float | None = None,
        risk_free_rate: float | None = None,
        query_greeks: bool = False,
    ) -> dict[str, Any]:
        option = _option_asset(
            strategy,
            symbol=symbol,
            expiration=expiration,
            strike=strike,
            right=right,
        )
        greeks = strategy.get_greeks(
            option,
            asset_price=option_price,
            underlying_price=underlying_price,
            risk_free_rate=risk_free_rate,
            query_greeks=query_greeks,
        )
        return {
            "asset": _asset_to_dict(option),
            "greeks": _jsonable(greeks),
            "available": greeks is not None,
            "datetime": strategy.get_datetime().isoformat(),
        }

    return BoundTool(
        name="options_get_greeks",
        description=(
            "Get Greeks for one exact listed option contract. "
            "Arguments: symbol, expiration, strike, right, and optional underlying_price, option_price, risk_free_rate, query_greeks. "
            "Use the exact expiration and strike returned by options_get_chain/options_get_strikes. The result proves Greeks only for the exact strike and right named in the result. Never transfer or reuse that delta for a neighboring strike. A null greeks result means the data source cannot value that contract at the current runtime datetime. "
            "Example: options_get_greeks(symbol='SPY', expiration='2026-09-18', strike=650, right='call')."
        ),
        function=get_greeks,
        metadata={"kind": "builtin", "replay_on_cache": True},
    )


def _bind_options_find_strike_for_delta(strategy: Any, manager: Any) -> BoundTool:
    def find_strike_for_delta(
        *,
        symbol: str,
        expiration: str,
        right: OptionRightArg,
        target_delta: float,
        underlying_price: float | None = None,
        underlying_asset_type: Literal["stock", "index"] = "stock",
    ) -> dict[str, Any]:
        target_delta_value = float(target_delta)
        if not math.isfinite(target_delta_value) or abs(target_delta_value) > 1:
            raise ValueError("target_delta must be a finite value from -1 through 1.")
        expiration_value = _coerce_expiration(_require_non_empty_text("expiration", expiration))
        if not isinstance(expiration_value, date):
            raise ValueError("expiration must use YYYY-MM-DD format.")
        underlying = _underlying_asset(strategy, symbol=symbol, asset_type=underlying_asset_type)
        if underlying_price is None:
            underlying_price = strategy.get_last_price(underlying)
        if underlying_price is None:
            raise ValueError(f"No underlying price is available for {symbol.upper()}.")
        chains = strategy.get_chains(underlying)
        strike = _options_helper_for_strategy(strategy).find_strike_for_delta(
            underlying,
            float(underlying_price),
            target_delta_value,
            expiration_value,
            right,
            chains=chains,
        )
        return {
            "symbol": symbol.upper(),
            "expiration": expiration_value.isoformat(),
            "right": right,
            "target_delta": target_delta_value,
            "underlying_price": float(underlying_price),
            "strike": float(strike) if strike is not None else None,
            "available": strike is not None,
            "datetime": strategy.get_datetime().isoformat(),
        }

    return BoundTool(
        name="options_find_strike_for_delta",
        description=(
            "Find the listed strike whose calculated delta is closest to a target for one expiration and right. "
            "Arguments: symbol, expiration, right, target_delta, optional underlying_price, optional underlying_asset_type. "
            "Use positive target delta for calls and negative target delta for puts. First retrieve the chain and choose a listed expiration. "
            "The returned strike is only a search candidate. It does not prove that the contract's current delta equals or is acceptably close to target_delta. Call options_get_greeks on that exact strike, use the exact returned delta, and reject the candidate when it is outside the strategy's permitted range. Never relabel, round, or describe a materially different verified delta as the target delta. "
            "Example: options_find_strike_for_delta(symbol='SPY', expiration='2026-09-18', right='put', target_delta=-0.16)."
        ),
        function=find_strike_for_delta,
        metadata={"kind": "builtin", "replay_on_cache": True},
    )


def _bind_options_evaluate_market(strategy: Any, manager: Any) -> BoundTool:
    def evaluate_market(
        *,
        symbol: str,
        expiration: str,
        strike: float,
        right: OptionRightArg,
        max_spread_pct: float | None = None,
    ) -> dict[str, Any]:
        option = _option_asset(
            strategy,
            symbol=symbol,
            expiration=expiration,
            strike=strike,
            right=right,
        )
        evaluation = _options_helper_for_strategy(strategy).evaluate_option_market(
            option,
            max_spread_pct=max_spread_pct,
        )
        return {
            "asset": _asset_to_dict(option),
            "market": _jsonable(vars(evaluation)),
            "datetime": strategy.get_datetime().isoformat(),
        }

    return BoundTool(
        name="options_evaluate_market",
        description=(
            "Inspect executable quote quality for one exact option contract and return bid, ask, last, spread percentage, suggested buy/sell prices, and data-quality flags. "
            "Arguments: symbol, expiration, strike, right, optional max_spread_pct as a fraction such as 0.20 for 20 percent. "
            "Call this for every proposed leg before submitting a multi-leg order. Do not trade a contract whose response says the market is unavailable or unacceptably wide under your policy. "
            "Example: options_evaluate_market(symbol='SPY', expiration='2026-09-18', strike=650, right='call', max_spread_pct=0.20)."
        ),
        function=evaluate_market,
        metadata={"kind": "builtin", "replay_on_cache": True},
    )


def _bind_options_calculate_multileg_price(strategy: Any, manager: Any) -> BoundTool:
    def calculate_multileg_price(
        *,
        legs_json: str,
        price_style: Literal["best", "mid", "fastest"] = "mid",
    ) -> dict[str, Any]:
        orders = _parse_option_legs(strategy, legs_json)
        net_price = _options_helper_for_strategy(strategy).calculate_multileg_limit_price(orders, price_style)
        if net_price is None:
            return {
                "available": False,
                "price_style": price_style,
                "net_limit_price": None,
                "legs": [_order_to_dict(order) for order in orders],
            }
        net_price = float(net_price)
        order_type = "debit" if net_price > 0 else "credit" if net_price < 0 else "even"
        return {
            "available": True,
            "price_style": price_style,
            "net_limit_price": net_price,
            "order_type": order_type,
            "broker_price": abs(net_price),
            "legs": [_order_to_dict(order) for order in orders],
            "datetime": strategy.get_datetime().isoformat(),
        }

    return BoundTool(
        name="options_calculate_multileg_price",
        description=(
            "Calculate a provider-generic net limit price for two or more exact option legs without submitting them. "
            "Arguments: legs_json and optional price_style='best', 'mid', or 'fastest'. legs_json must be a JSON array; every leg requires symbol, expiration, strike, right, quantity, and side. "
            "Use buy_to_open/sell_to_open when opening and buy_to_close/sell_to_close when closing. A positive net_limit_price is a debit and a negative value is a credit. "
            "For a closing order, a positive account_positions quantity is long and requires sell_to_close; a negative quantity is short and requires buy_to_close. Closing quantity is the absolute value of the position quantity. "
            "When comparing a per-unit multi-leg opening credit with a per-unit closing debit, price one contract per leg here. Use the full absolute position quantities only in the later orders_submit_multileg call. "
            "Independently reconcile the returned net price from the four option midpoint values you just observed. For a defined-risk structure, reject a result that conflicts materially with those leg mids or violates the structure's economic bounds. "
            "Example legs_json: [{\"symbol\":\"SPY\",\"expiration\":\"2026-09-18\",\"strike\":620,\"right\":\"put\",\"quantity\":1,\"side\":\"buy_to_open\"},{\"symbol\":\"SPY\",\"expiration\":\"2026-09-18\",\"strike\":625,\"right\":\"put\",\"quantity\":1,\"side\":\"sell_to_open\"}]."
        ),
        function=calculate_multileg_price,
        metadata={"kind": "builtin", "replay_on_cache": True},
    )


def _bind_load_history(strategy: Any, manager: Any) -> BoundTool:
    def load_history_table(
        *,
        symbol: str,
        length: int,
        timestep: str = "day",
        table_name: str | None = None,
        asset_type: AssetTypeArg = "stock",
        quote_symbol: str | None = None,
        exchange: str | None = None,
        expiration: str | None = None,
        strike: float | None = None,
        right: str | None = None,
        include_after_hours: bool = True,
    ) -> dict[str, Any]:
        symbol = _require_single_symbol_text("symbol", symbol)
        length = _require_positive_int("length", length)
        timestep = _require_non_empty_text("timestep", timestep)
        return manager.duckdb.load_history_table(
            symbol=symbol,
            length=length,
            timestep=timestep,
            table_name=table_name,
            asset_type=asset_type,
            quote_symbol=quote_symbol,
            exchange=exchange,
            expiration=_coerce_expiration(expiration),
            strike=strike,
            right=right,
            include_after_hours=include_after_hours,
        )

    return BoundTool(
        name="market_load_history_table",
        description=(
            "Load visible historical bars into DuckDB and return the table metadata. "
            "Arguments: symbol, length, timestep, optional table_name, asset_type, quote_symbol, exchange, expiration, strike, right, include_after_hours. "
            "Valid asset_type values: stock, option, future, cont_future, forex, crypto, index, multileg, us_equity. "
            "The symbol argument must be the exact tradable symbol, such as XLY or SPY, not a generated table name such as XLY_HIST. "
            "Use stock for normal equities. If asset_type is omitted, stock is assumed. Do not pass economic series ids such as DCOILWTICO, FEDFUNDS, or M2SL as market symbols; use macro/FRED tools for those instead. "
            "Use the exact column names returned in this tool result when querying the loaded DuckDB table. "
            "The available_tables result field lists the currently queryable tables "
            "and the exact columns for each table. "
            "This is a summary-first targeted single-symbol follow-up tool, "
            "not the default tool for every symbol in a universe ranking. "
            "Use it when market_load_history_tables_summary is missing, contradictory, "
            "or insufficient for one symbol. "
            "It returns metadata and computed_summary for model use, "
            "and does not return full raw historical rows by default. "
            "Raw rows remain queryable in DuckDB through the returned table_name. "
            "The computed_summary result field already includes common price, momentum, volume, trend, "
            "range, risk, and composite-score statistics; "
            "read it first before writing SQL for common history analysis. "
            "For cross-symbol comparisons, prefer market_load_history_tables_summary. "
            "Use duckdb_query only when the needed comparison or statistic is not already available in tool results. "
            "History tables loaded by this tool often expose Date as the timestamp column, not datetime; "
            "Do not assume datetime exists unless it is explicitly listed in columns. "
            "Use close for the traded price when that column is listed. "
            "Caveat: this only loads bars visible at the current LumiBot runtime datetime. "
            "Example: market_load_history_table(symbol='TQQQ', length=252, timestep='day', table_name='recent_prices')."
        ),
        function=load_history_table,
        metadata={"kind": "builtin", "replay_on_cache": True},
    )


def _bind_load_history_tables_summary(strategy: Any, manager: Any) -> BoundTool:
    def load_history_tables_summary(
        *,
        symbols: list[str],
        length: int = 252,
        timestep: str = "day",
        asset_type: AssetTypeArg = "stock",
        table_prefix: str | None = None,
        include_after_hours: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(symbols, list) or not symbols:
            raise ValueError("symbols must be a non-empty list.")
        symbols = [_require_single_symbol_text("symbols", symbol) for symbol in symbols]
        normalized_symbol_keys = [symbol.upper() for symbol in symbols]
        if len(normalized_symbol_keys) != len(set(normalized_symbol_keys)):
            raise ValueError("duplicate symbols are not allowed.")
        length = _require_positive_int("length", length)
        timestep = _require_non_empty_text("timestep", timestep)
        return manager.duckdb.load_history_tables_summary(
            symbols=symbols,
            length=length,
            timestep=timestep,
            asset_type=asset_type,
            table_prefix=table_prefix,
            include_after_hours=include_after_hours,
        )

    return BoundTool(
        name="market_load_history_tables_summary",
        description=(
            "Load visible historical bars for multiple symbols into DuckDB and return a cross-symbol summary. "
            "Arguments: symbols, optional length, timestep, asset_type, table_prefix, include_after_hours. "
            "This is the default tool for multi-symbol price-history comparison and universe ranking. "
            "Use it before per-symbol raw history tables for common cross-symbol comparison and ranking tasks. "
            "This summary-first tool returns factual rankings, including by_composite_score, plus recent returns, "
            "moving averages, trend alignment, drawdown, volatility, volume context, and range position "
            "for the requested universe. "
            "Each ranking list is capped at the top 10 symbols, and detailed universe_summary rows are limited "
            "to a top-ranked candidate subset of at most 15 symbols while the full requested symbols list remains visible. "
            "Prefer this tool before writing DuckDB SQL for common universe ranking. "
            "Caveat: this only loads bars visible at the current LumiBot runtime datetime. "
            "Example: market_load_history_tables_summary("
            "symbols=['QQQ', 'SPY'], length=252, timestep='day', table_prefix='cmp')."
        ),
        function=load_history_tables_summary,
        metadata={"kind": "builtin", "replay_on_cache": True},
    )


def _bind_duckdb_query(strategy: Any, manager: Any) -> BoundTool:
    def duckdb_query(*, sql: str, limit: int = 200) -> dict[str, Any]:
        sql = _require_non_empty_text("sql", sql)
        limit = _require_positive_int("limit", limit)
        return manager.duckdb.query(sql=sql, limit=limit)

    return BoundTool(
        name="duckdb_query",
        description=(
            "Run a read-only SQL query against tables previously loaded into DuckDB. "
            "Arguments: sql, optional limit. "
            "Use this as targeted follow-up only when computed summaries or rankings are insufficient "
            "for a specific question and a relevant DuckDB table is already available. "
            "Do not treat DuckDB as a required first step in price-history research. "
            "Use exact column names from market_load_history_table or pragma_table_info('table_name'); "
            "do not invent columns. "
            "History tables loaded by market_load_history_table often use Date as the timestamp column; "
            "Do not invent datetime unless the schema explicitly lists it. Use close for prices when listed. "
            "For multi-table queries, alias every table and qualify shared or potentially shared columns such as "
            "sym, Date, close, and return with the table alias, for example q.sym, q.Date, and q.close. "
            "Example join: SELECT q.Date, q.close AS qqq_close, s.close AS spy_close FROM qqq_hist AS q "
            "JOIN spy_hist AS s ON q.Date = s.Date ORDER BY q.Date. "
            "Caveat: only read-only SQL is allowed. "
            "Example: duckdb_query(sql='SELECT AVG(close) AS avg_close FROM recent_prices')."
        ),
        function=duckdb_query,
        metadata={"kind": "builtin"},
    )


def _bind_docs_search(strategy: Any, manager: Any) -> BoundTool:
    def docs_search(*, query: str, max_results: int = 5, limit: int | None = None) -> dict[str, Any]:
        query = _require_non_empty_text("query", query)
        if limit is not None:
            max_results = limit
        max_results = _require_positive_int("max_results", max_results)
        return search_lumibot_docs(query=query, max_results=max_results)

    return BoundTool(
        name="lumibot_docs_search",
        description=(
            "Search LumiBot's local documentation and return the best matching snippets. "
            "Arguments: query, optional max_results or limit. "
            "Use this when you are unsure how a LumiBot tool, asset type, benchmark, or backtesting feature works. "
            "Example: lumibot_docs_search(query='run_backtest benchmark_asset SPY')."
        ),
        function=docs_search,
        metadata={"kind": "builtin"},
    )


ALPACA_NEWS_DESCRIPTION = (
    "Fetch Alpaca/Benzinga news articles using the user's own Alpaca API key. "
    "This is symbol/date-window retrieval, not keyword search: arguments are optional symbols comma-list, "
    "start, end, limit <= 50, include_content, exclude_contentless, page_token, optional content_max_chars, and sort. "
    "In backtests, only use articles at or before the current simulated datetime; if end is omitted, LumiBot uses "
    "the current simulated datetime, and future end times are clamped to avoid look-ahead bias. "
    "Use a two-step workflow: first scan with include_content=False to read headlines, summaries, timestamps, URLs, "
    "sources, and symbols. Use limit=10-20 for focused single-symbol checks, limit=30-50 for broad market or sector scans, "
    "and use page_token when next_page_token is returned to fetch more pages. Do not trade from one weak or noisy article. If a story matters, call "
    "again for the same/narrower window with include_content=True and usually exclude_contentless=True to read the full article body. Full content is "
    "not truncated unless you explicitly set content_max_chars. Use page_token when next_page_token is returned. "
    "If single-stock news is sparse, broaden intelligently: broad market SPY,QQQ,DIA,IWM; tech/AI/semis QQQ,XLK,SMH; "
    "financials/banks XLF,KRE; energy/oil XLE,USO; healthcare/biotech XLV,XBI; industrials XLI; consumer discretionary "
    "XLY; staples XLP; utilities XLU; materials XLB; real estate XLRE; rates/bonds TLT,IEF,SHY; gold/commodities GLD,SLV,DBC."
)


def _bind_alpaca_news(strategy: Any, manager: Any) -> BoundTool:
    def _warn_unavailable() -> None:
        message = (
            "[agents] alpaca_news is not configured and will not be exposed. "
            "Use an Alpaca broker connection or set ALPACA_NEWS_API_KEY and ALPACA_NEWS_API_SECRET."
        )
        if manager is not None:
            warned = getattr(manager, "_warned_unavailable_builtin_tools", None)
            if warned is None:
                warned = set()
                setattr(manager, "_warned_unavailable_builtin_tools", warned)
            if "alpaca_news" in warned:
                return
            warned.add("alpaca_news")
        log_message = getattr(strategy, "log_message", None)
        if callable(log_message):
            try:
                log_message(message, color="yellow")
                return
            except Exception:
                pass
        warning = getattr(manager, "warning", None) if manager is not None else None
        if callable(warning):
            warning(message)

    def _resolve_alpaca_news_headers() -> tuple[dict[str, str] | None, str | None]:
        # Prefer news-only credentials when supplied. They are intentionally
        # separate from generic Alpaca broker env vars so a Tradier/IBKR/etc.
        # strategy can use Alpaca/Benzinga news without changing broker routing.
        api_key = str(os.environ.get("ALPACA_NEWS_API_KEY") or "").strip()
        api_secret = str(os.environ.get("ALPACA_NEWS_API_SECRET") or "").strip()
        if api_key and api_secret:
            return {
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": api_secret,
            }, "byok_alpaca_news_env"

        broker = getattr(strategy, "broker", None)
        if str(getattr(broker, "name", "") or "").lower() == "alpaca":
            oauth_token = str(getattr(broker, "oauth_token", "") or "").strip()
            if oauth_token:
                return {"Authorization": f"Bearer {oauth_token}"}, "alpaca_broker_oauth"

            api_key = str(getattr(broker, "api_key", "") or "").strip()
            api_secret = str(getattr(broker, "api_secret", "") or "").strip()
            if api_key and api_secret:
                return {
                    "APCA-API-KEY-ID": api_key,
                    "APCA-API-SECRET-KEY": api_secret,
                }, "alpaca_broker_api_key"

        return None, None

    def _unavailable_alpaca_news(**kwargs: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "tool_error": True,
            "error": {
                "type": "MissingCredentials",
                "message": "alpaca_news is not configured. Use an Alpaca broker connection or set ALPACA_NEWS_API_KEY and ALPACA_NEWS_API_SECRET.",
            },
            "articles": [],
            "count": 0,
        }

    if _resolve_alpaca_news_headers()[0] is None:
        _warn_unavailable()
        return BoundTool(
            name="alpaca_news",
            description=ALPACA_NEWS_DESCRIPTION,
            function=_unavailable_alpaca_news,
            metadata={
                "kind": "builtin",
                "disabled": True,
                "disabled_reason": "missing Alpaca broker credentials or ALPACA_NEWS_API_KEY / ALPACA_NEWS_API_SECRET",
            },
        )

    def alpaca_news(
        *,
        symbols: str = "",
        start: str = "",
        end: str = "",
        limit: int = 30,
        include_content: bool = False,
        exclude_contentless: bool = False,
        page_token: str = "",
        content_max_chars: int | None = None,
        sort: NewsSortArg = "desc",
    ) -> dict[str, Any]:
        auth_headers, credential_source = _resolve_alpaca_news_headers()
        if not auth_headers:
            return {
                "ok": False,
                "tool_error": True,
                "error": {
                    "type": "MissingCredentials",
                    "message": "Use an Alpaca broker connection or set ALPACA_NEWS_API_KEY and ALPACA_NEWS_API_SECRET to use alpaca_news.",
                },
                "articles": [],
                "count": 0,
            }

        current_dt = strategy.get_datetime()
        if not end:
            end = current_dt.isoformat()
        if not start:
            start = (current_dt - timedelta(days=7)).isoformat()
        limit = max(1, min(int(limit), 50))
        sort = "asc" if str(sort).lower() == "asc" else "desc"
        content_limit: int | None = None
        if content_max_chars is not None:
            try:
                content_limit = int(content_max_chars)
            except Exception as exc:
                raise ValueError("content_max_chars must be an integer when provided.") from exc
            if content_limit <= 0:
                raise ValueError("content_max_chars must be greater than 0 when provided.")

        requested_end = str(end)
        lookahead_clamped = False
        if getattr(strategy, "is_backtesting", False):
            parsed_end = _parse_datetime_value(end)
            if parsed_end is not None:
                comparable_end = _coerce_same_timezone(parsed_end, current_dt)
                if comparable_end > current_dt:
                    end = current_dt.isoformat()
                    lookahead_clamped = True

        params: dict[str, Any] = {
            "start": start,
            "end": end,
            "sort": sort,
            "limit": limit,
            "include_content": bool(include_content),
            "exclude_contentless": bool(exclude_contentless),
        }
        if symbols:
            params["symbols"] = symbols
        if page_token:
            params["page_token"] = page_token

        response = _requests().get(
            "https://data.alpaca.markets/v1beta1/news",
            headers=auth_headers,
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        normalized_articles: list[dict[str, Any]] = []
        content_available_count = 0
        summary_available_count = 0
        for article in payload.get("news", []) or []:
            if not isinstance(article, dict):
                continue
            raw_content = str(article.get("content") or "")
            raw_summary = str(article.get("summary") or "")
            if raw_content:
                content_available_count += 1
            if raw_summary:
                summary_available_count += 1
            normalized: dict[str, Any] = {
                "id": article.get("id"),
                "headline": article.get("headline"),
                "summary": article.get("summary"),
                "author": article.get("author"),
                "source": article.get("source"),
                "created_at": article.get("created_at"),
                "updated_at": article.get("updated_at"),
                "url": article.get("url"),
                "symbols": article.get("symbols") or [],
                "content_available": bool(raw_content),
                "summary_available": bool(raw_summary),
            }
            if include_content and raw_content:
                normalized["content_original_length"] = len(raw_content)
                if content_limit is not None and len(raw_content) > content_limit:
                    normalized["content"] = raw_content[:content_limit]
                    normalized["content_truncated"] = True
                    normalized["content_max_chars"] = content_limit
                else:
                    normalized["content"] = raw_content
                    normalized["content_truncated"] = False
            normalized_articles.append(normalized)

        return {
            "ok": True,
            "provider": "alpaca",
            "source": "benzinga",
            "endpoint": "v1beta1/news",
            "credential_source": credential_source,
            "window_start": start,
            "window_end": end,
            "requested_end": requested_end,
            "effective_end": end,
            "lookahead_clamped": lookahead_clamped,
            "query_symbols": symbols,
            "include_content": bool(include_content),
            "content_included": bool(include_content),
            "count": len(normalized_articles),
            "content_available_count": content_available_count,
            "summary_available_count": summary_available_count,
            "next_page_token": payload.get("next_page_token"),
            "articles": normalized_articles,
        }

    return BoundTool(
        name="alpaca_news",
        description=ALPACA_NEWS_DESCRIPTION,
        function=alpaca_news,
        metadata={"kind": "builtin"},
    )


def _preflight_blocker(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _finite_positive_price(value: Any) -> float | None:
    try:
        price = float(value)
    except Exception:
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return price


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except Exception:
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _preflight_strategy_call(strategy: Any, method_name: str, *args: Any, **kwargs: Any) -> tuple[bool, Any]:
    method = getattr(strategy, method_name, None)
    if not callable(method):
        return False, None
    try:
        return True, method(*args, **kwargs)
    except TypeError:
        if kwargs:
            try:
                return True, method(*args)
            except Exception:
                return False, None
        return False, None
    except Exception:
        return False, None


def _preflight_account_snapshot(strategy: Any) -> dict[str, Any]:
    cash_ok, raw_cash = _preflight_strategy_call(strategy, "get_cash")
    portfolio_ok, raw_portfolio_value = _preflight_strategy_call(strategy, "get_portfolio_value")
    cash = _finite_float(raw_cash) if cash_ok else None
    portfolio_value = _finite_float(raw_portfolio_value) if portfolio_ok else None
    return {
        "cash": _jsonable(cash),
        "portfolio_value": _jsonable(portfolio_value),
        "_available": cash_ok and portfolio_ok and cash is not None and portfolio_value is not None,
    }


def _preflight_positions(strategy: Any) -> tuple[bool, list[Any]]:
    get_positions = getattr(strategy, "get_positions", None)
    if not callable(get_positions):
        return False, []
    try:
        return True, list(get_positions(include_cash_positions=True) or [])
    except TypeError:
        try:
            return True, list(get_positions() or [])
        except Exception:
            return False, []
    except Exception:
        return False, []


_PREFLIGHT_ASSET_TYPE_ALIASES = {
    "us_equity": "stock",
}


def _preflight_normalized_asset_type(value: Any) -> str:
    raw_value = getattr(value, "value", value)
    text = str(raw_value or "").strip().lower()
    return _PREFLIGHT_ASSET_TYPE_ALIASES.get(text, text)


def _preflight_position_matches(position: Any, symbol: str, asset_type: str) -> bool:
    asset = getattr(position, "asset", None)
    return (
        _normalized_symbol(getattr(asset, "symbol", None)) == _normalized_symbol(symbol)
        and _preflight_normalized_asset_type(getattr(asset, "asset_type", None))
        == _preflight_normalized_asset_type(asset_type)
    )


def _preflight_position_snapshot(strategy: Any, symbol: str, asset_type: str) -> dict[str, Any]:
    normalized_symbol = _normalized_symbol(symbol)
    normalized_asset_type = _preflight_normalized_asset_type(asset_type)
    positions_available, positions = _preflight_positions(strategy)
    if not positions_available:
        return {
            "asset": {
                "symbol": normalized_symbol,
                "asset_type": normalized_asset_type,
                "expiration": None,
                "strike": None,
                "right": None,
                "multiplier": None,
            },
            "quantity": None,
            "avg_fill_price": None,
            "current_price": None,
            "market_value": None,
            "pnl": None,
            "pnl_percent": None,
            "_available": False,
        }
    for position in positions:
        if _preflight_position_matches(position, normalized_symbol, normalized_asset_type):
            snapshot = _position_to_dict(position)
            snapshot["_available"] = _finite_float(snapshot.get("quantity")) is not None
            return snapshot
    return {
        "asset": {
            "symbol": normalized_symbol,
            "asset_type": normalized_asset_type,
            "expiration": None,
            "strike": None,
            "right": None,
            "multiplier": None,
        },
        "quantity": 0.0,
        "avg_fill_price": None,
        "current_price": None,
        "market_value": None,
        "pnl": None,
        "pnl_percent": None,
        "_available": True,
    }


def _preflight_open_orders_snapshot(strategy: Any, symbol: str) -> dict[str, Any]:
    orders_ok, orders = _preflight_strategy_call(strategy, "get_orders")
    if not orders_ok:
        return {
            "count": None,
            "same_symbol_count": None,
            "same_symbol_orders": [],
            "orders": [],
            "_available": False,
        }
    order_payloads = [
        _order_to_dict(order)
        for order in orders or []
        if _preflight_order_appears_open(order)
    ]
    normalized_symbol = _normalized_symbol(symbol)
    same_symbol_orders = [
        order
        for order in order_payloads
        if isinstance(order.get("asset"), dict)
        and _normalized_symbol(order["asset"].get("symbol")) == normalized_symbol
    ]
    return {
        "count": len(order_payloads),
        "same_symbol_count": len(same_symbol_orders),
        "same_symbol_orders": same_symbol_orders,
        "orders": order_payloads,
        "_available": True,
    }


_PREFLIGHT_ACTIVE_ORDER_STATUSES = {
    "unprocessed",
    "submitted",
    "open",
    "new",
    "partial_fill",
    "partially_filled",
}


def _preflight_order_appears_open(order: Any) -> bool:
    is_active = _safe_call(order, "is_active", None)
    if is_active is not None:
        return bool(is_active)
    if _safe_call(order, "is_filled", False):
        return False
    if _safe_call(order, "is_canceled", False):
        return False
    status = _normalized_order_text(getattr(order, "status", None))
    return status in _PREFLIGHT_ACTIVE_ORDER_STATUSES


def _preflight_price_snapshot(strategy: Any, symbol: str, asset_type: str) -> dict[str, Any]:
    price = None
    price_source = None
    if symbol and asset_type in {"stock", "us_equity"}:
        asset, quote = resolve_asset_and_quote(strategy, symbol=symbol, asset_type=asset_type)
        strategy_price = _strategy_order_cash_check_price_snapshot(strategy, asset=asset, quote=quote)
        if strategy_price is not None:
            price = strategy_price["last_price"]
            price_source = strategy_price.get("price_source")
        else:
            price_ok, raw_price = _preflight_strategy_call(strategy, "get_last_price", asset, quote=quote)
            if price_ok:
                price = _finite_positive_price(raw_price)
                if price is not None:
                    price_source = "strategy_last_price"
    return {"last_price": price, "price_source": price_source}


def _preflight_blocked_payload(
    *,
    blockers: list[dict[str, str]],
    warnings: list[str],
    account: dict[str, Any],
    position: dict[str, Any],
    price: dict[str, Any],
    open_orders: dict[str, Any],
    estimate: dict[str, Any],
    internal_checks: list[str],
    sequence: Any,
    symbol: str,
    side: str,
    quantity: float | None,
    asset_type: str,
    order_type: str,
    time_in_force: str,
) -> dict[str, Any]:
    return {
        "readiness": "blocked",
        "can_submit": False,
        "blockers": blockers,
        "warnings": warnings,
        "account": account,
        "position": position,
        "price": price,
        "open_orders": open_orders,
        "estimate": estimate,
        "internal_checks": internal_checks,
        "sequence": _jsonable(sequence),
        "order": {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "asset_type": asset_type,
            "order_type": order_type,
            "time_in_force": time_in_force,
        },
    }


def _bind_preflight_check(strategy: Any, manager: Any) -> BoundTool:
    def preflight_check(
        *,
        symbol: str,
        side: str,
        quantity: Any,
        sequence: Any = None,
        asset_type: str = "stock",
        order_type: str = "market",
        time_in_force: str = "day",
    ) -> dict[str, Any]:
        symbol_text = _normalized_symbol(symbol)
        side_text = str(side or "").strip().lower()
        asset_type_text = str(asset_type or "").strip().lower()
        order_type_text = str(order_type or "").strip().lower()
        time_in_force_text = str(time_in_force or "").strip().lower()
        blockers: list[dict[str, str]] = []
        warnings: list[str] = []
        internal_checks = ["account_portfolio", "account_positions", "orders_open_orders", "market_last_price"]

        parsed_quantity: float | None = None
        if not symbol_text or "," in str(symbol or ""):
            blockers.append(_preflight_blocker("INVALID_SYMBOL", "symbol must be one non-empty tradable symbol."))
        if side_text not in {"buy", "sell"}:
            blockers.append(
                _preflight_blocker(
                    "INVALID_SIDE",
                    "orders_preflight_check supports side='buy' or side='sell'.",
                )
            )
        try:
            parsed_quantity = float(quantity)
        except Exception:
            parsed_quantity = None
        if parsed_quantity is None or not math.isfinite(parsed_quantity) or parsed_quantity <= 0:
            blockers.append(_preflight_blocker("INVALID_QUANTITY", "quantity must be a finite number greater than 0."))
        if asset_type_text not in {"stock", "us_equity"}:
            blockers.append(
                _preflight_blocker(
                    "UNSUPPORTED_ASSET_TYPE",
                    "orders_preflight_check supports stock/us_equity orders.",
                )
            )
        if order_type_text != "market":
            blockers.append(
                _preflight_blocker(
                    "UNSUPPORTED_ORDER_TYPE",
                    "orders_preflight_check currently supports market orders only.",
                )
            )
        if time_in_force_text != "day":
            blockers.append(
                _preflight_blocker(
                    "UNSUPPORTED_TIME_IN_FORCE",
                    "orders_preflight_check currently supports time_in_force='day' only.",
                )
            )

        account = _preflight_account_snapshot(strategy)
        position = _preflight_position_snapshot(strategy, symbol_text, asset_type_text)
        open_orders = _preflight_open_orders_snapshot(strategy, symbol_text)
        price = _preflight_price_snapshot(strategy, symbol_text, asset_type_text)
        position_quantity = _finite_float(position.get("quantity"))
        last_price = price["last_price"]
        estimated_order_value = (
            last_price * parsed_quantity
            if last_price is not None and parsed_quantity is not None and parsed_quantity > 0
            else None
        )
        cash_value = _finite_float(account.get("cash"))
        estimated_cash_after_order = None
        estimated_position_after_order = None
        if estimated_order_value is not None and cash_value is not None and side_text in {"buy", "sell"}:
            estimated_cash_after_order = (
                cash_value - estimated_order_value if side_text == "buy" else cash_value + estimated_order_value
            )
        if (
            parsed_quantity is not None
            and math.isfinite(parsed_quantity)
            and position_quantity is not None
            and side_text in {"buy", "sell"}
        ):
            estimated_position_after_order = (
                position_quantity + parsed_quantity if side_text == "buy" else position_quantity - parsed_quantity
            )
        estimate = {
            "estimated_order_value": _jsonable(estimated_order_value),
            "estimated_cash_after_order": _jsonable(estimated_cash_after_order),
            "estimated_position_after_order": _jsonable(estimated_position_after_order),
        }

        if account.get("_available") is not True:
            blockers.append(
                _preflight_blocker(
                    "ACCOUNT_UNAVAILABLE",
                    "Cash and portfolio value must be available as finite numbers.",
                )
            )
        if position.get("_available") is not True:
            blockers.append(
                _preflight_blocker(
                    "POSITIONS_UNAVAILABLE",
                    "Current positions must be available before preflight can approve an order.",
                )
            )
        if open_orders.get("_available") is not True:
            blockers.append(
                _preflight_blocker(
                    "OPEN_ORDERS_UNAVAILABLE",
                    "Open orders must be available before preflight can approve an order.",
                )
            )
        if last_price is None:
            blockers.append(_preflight_blocker("PRICE_UNAVAILABLE", "A positive finite latest price is required."))
        if isinstance(open_orders.get("same_symbol_count"), int) and open_orders["same_symbol_count"] > 0:
            blockers.append(
                _preflight_blocker("OPEN_ORDER_CONFLICT", "There is already an active open order for this symbol.")
            )
        if (
            side_text == "buy"
            and estimated_order_value is not None
            and cash_value is not None
            and estimated_order_value > cash_value
        ):
            blockers.append(_preflight_blocker("INSUFFICIENT_CASH_ESTIMATE", "Estimated buy value exceeds current cash."))
        if (
            side_text == "sell"
            and parsed_quantity is not None
            and position_quantity is not None
            and parsed_quantity > position_quantity
        ):
            blockers.append(_preflight_blocker("INSUFFICIENT_POSITION", "Sell quantity exceeds current long position quantity."))

        payload_kwargs = {
            "blockers": blockers,
            "warnings": warnings,
            "account": account,
            "position": position,
            "price": price,
            "open_orders": open_orders,
            "estimate": estimate,
            "internal_checks": internal_checks,
            "sequence": sequence,
            "symbol": symbol_text,
            "side": side_text,
            "quantity": parsed_quantity,
            "asset_type": asset_type_text,
            "order_type": order_type_text,
            "time_in_force": time_in_force_text,
        }
        if blockers:
            return _preflight_blocked_payload(**payload_kwargs)

        _record_successful_order_readiness(
            symbol=symbol_text,
            side=side_text,
            quantity=parsed_quantity,
            asset_type=asset_type_text,
            order_type=order_type_text,
            time_in_force=time_in_force_text,
            source="orders_preflight_check",
        )
        ready_payload = _preflight_blocked_payload(**payload_kwargs)
        ready_payload["readiness"] = "ready"
        ready_payload["can_submit"] = True
        return ready_payload

    return BoundTool(
        name="orders_preflight_check",
        description=ORDERS_PREFLIGHT_CHECK_DESCRIPTION,
        function=preflight_check,
        metadata={"kind": "builtin", "replay_on_cache": True},
    )


def _bind_open_orders(strategy: Any, manager: Any) -> BoundTool:
    def open_orders() -> dict[str, Any]:
        orders = strategy.get_orders()
        return {
            "orders": [_order_to_dict(order) for order in orders],
            "datetime": strategy.get_datetime().isoformat(),
        }

    return BoundTool(
        name="orders_open_orders",
        description="List the strategy's currently tracked orders, including identifiers, status, side, quantity, and prices.",
        function=open_orders,
        metadata={"kind": "builtin"},
    )


_ORDER_CONFIRM_MAX_ATTEMPTS = 5
_ORDER_CONFIRM_MAX_WAIT_SECONDS = 5.0
_ORDER_CONFIRM_TERMINAL_FAILURE_STATUSES = {
    "cancel",
    "canceled",
    "cancelled",
    "error",
    "expired",
    "rejected",
}
_ORDER_CONFIRM_PARTIAL_STATUSES = {"partial_fill", "partially_filled"}


def _coerce_confirm_attempts(max_attempts: Any) -> int:
    try:
        attempts = int(max_attempts)
    except Exception:
        attempts = 3
    return min(max(attempts, 1), _ORDER_CONFIRM_MAX_ATTEMPTS)


def _coerce_confirm_wait_seconds(wait_seconds: Any, strategy: Any) -> float:
    if wait_seconds is None:
        wait_seconds = 0.0 if bool(getattr(strategy, "is_backtesting", False)) else 1.0
    try:
        wait = float(wait_seconds)
    except Exception:
        wait = 0.0
    if not math.isfinite(wait):
        wait = 0.0
    return min(max(wait, 0.0), _ORDER_CONFIRM_MAX_WAIT_SECONDS)


def _get_order_for_confirmation(strategy: Any, identifier: str) -> Any:
    get_order = getattr(strategy, "get_order", None)
    if not callable(get_order):
        return None
    try:
        return get_order(identifier, broker_refresh=True, broker_refresh_ttl_seconds=0.0)
    except TypeError:
        try:
            return get_order(identifier, broker_refresh=True)
        except TypeError:
            return get_order(identifier)


def _process_pending_orders_for_confirmation(strategy: Any) -> bool:
    broker = getattr(strategy, "broker", None)
    process_pending_orders = getattr(broker, "process_pending_orders", None)
    if not bool(getattr(strategy, "is_backtesting", False)) or not callable(process_pending_orders):
        return False
    try:
        process_pending_orders(strategy=strategy)
    except TypeError:
        process_pending_orders(strategy)
    _flush_strategy_event_queue_for_confirmation(strategy)
    return True


def _flush_strategy_event_queue_for_confirmation(strategy: Any) -> bool:
    executor = getattr(strategy, "__dict__", {}).get("_executor_instance")
    process_queue = getattr(executor, "process_queue", None)
    if not callable(process_queue):
        return False
    broker = getattr(strategy, "broker", None)
    strategy_first_iteration = getattr(strategy, "_first_iteration", None)
    broker_first_iteration = getattr(broker, "_first_iteration", None)
    try:
        if strategy_first_iteration is True:
            setattr(strategy, "_first_iteration", False)
        if broker is not None and broker_first_iteration is True:
            setattr(broker, "_first_iteration", False)
        process_queue()
    except Exception:
        return False
    finally:
        if strategy_first_iteration is True:
            setattr(strategy, "_first_iteration", strategy_first_iteration)
        if broker is not None and broker_first_iteration is True:
            setattr(broker, "_first_iteration", broker_first_iteration)
    return True


def _sleep_for_confirmation(strategy: Any, seconds: float) -> bool:
    if seconds <= 0:
        return False
    sleep = getattr(strategy, "sleep", None)
    if callable(sleep):
        try:
            sleep(seconds)
            return True
        except Exception:
            return False
    return False


def _position_quantity_for_symbol(strategy: Any, symbol: str | None) -> float | None:
    if not symbol:
        return None
    symbol = symbol.strip().upper()
    positions = getattr(strategy, "get_positions", None)
    if not callable(positions):
        return None
    try:
        position_list = positions(include_cash_positions=True)
    except TypeError:
        position_list = positions()
    total = 0.0
    found = False
    for position in position_list or []:
        asset = getattr(position, "asset", None)
        position_symbol = str(getattr(asset, "symbol", "")).strip().upper()
        if position_symbol != symbol:
            continue
        try:
            total += float(getattr(position, "quantity", 0) or 0)
        except Exception:
            return None
        found = True
    return total if found else 0.0


def _positions_snapshot(strategy: Any) -> list[dict[str, Any]]:
    positions = getattr(strategy, "get_positions", None)
    if not callable(positions):
        return []
    try:
        position_list = positions(include_cash_positions=True)
    except TypeError:
        position_list = positions()
    return [_position_to_dict(position) for position in position_list or []]


def _account_snapshot(strategy: Any) -> dict[str, Any]:
    cash = None
    portfolio_value = None
    get_cash = getattr(strategy, "get_cash", None)
    get_portfolio_value = getattr(strategy, "get_portfolio_value", None)
    if callable(get_cash):
        try:
            cash = get_cash()
        except Exception:
            cash = None
    if callable(get_portfolio_value):
        try:
            portfolio_value = get_portfolio_value()
        except Exception:
            portfolio_value = None
    return {
        "cash": _jsonable(cash),
        "portfolio_value": _jsonable(portfolio_value),
        "positions": _positions_snapshot(strategy),
    }


def _order_confirmation_status(order: Any, *, attempts_exhausted: bool = False) -> str:
    if order is None:
        return "not_found"
    raw_status = str(getattr(order, "status", "") or "").strip().lower()
    if _safe_call(order, "is_filled", False):
        if raw_status == "cash_settled":
            return "cash_settled"
        return "filled"
    if raw_status in _ORDER_CONFIRM_PARTIAL_STATUSES:
        return "partially_filled"
    if raw_status in _ORDER_CONFIRM_TERMINAL_FAILURE_STATUSES or _safe_call(order, "is_canceled", False):
        if raw_status in {"cancel", "cancelled"}:
            return "canceled"
        return raw_status or "error"
    if attempts_exhausted and _safe_call(order, "is_active", False):
        return "open_after_retries"
    return raw_status or "unknown"


def _confirmation_checks(
    strategy: Any,
    *,
    order: Any,
    symbol: str | None,
    side: str | None,
    expected_quantity: float | None,
    position_before_quantity: float | None,
    cash_before: float | None,
    account_snapshot: dict[str, Any],
) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "order_found": order is not None,
        "order_filled": bool(_safe_call(order, "is_filled", False)) if order is not None else False,
    }
    if order is None:
        return checks

    current_position = _position_quantity_for_symbol(strategy, symbol)
    checks["current_position_quantity"] = _jsonable(current_position)
    if position_before_quantity is not None and current_position is not None and side:
        before_qty = float(position_before_quantity)
        expected_qty = float(expected_quantity or 0)
        side_value = str(side).strip().lower()
        if side_value in {"sell", "sell_to_close", "sell_short", "sell_to_open"}:
            checks["position_moved_as_expected"] = current_position <= (
                before_qty - min(expected_qty, abs(before_qty)) + 1e-9
            )
        elif side_value in {"buy", "buy_to_open", "buy_to_close", "buy_to_cover"}:
            checks["position_moved_as_expected"] = current_position >= before_qty + expected_qty - 1e-9

    current_cash = account_snapshot.get("cash")
    if cash_before is not None and current_cash is not None and side:
        before_cash = float(cash_before)
        cash_value = float(current_cash)
        side_value = str(side).strip().lower()
        if side_value in {"sell", "sell_to_close", "sell_short", "sell_to_open"}:
            checks["cash_moved_as_expected"] = cash_value > before_cash + 1e-9
        elif side_value in {"buy", "buy_to_open", "buy_to_close", "buy_to_cover"}:
            checks["cash_moved_as_expected"] = cash_value < before_cash - 1e-9

    return checks


def _confirmation_checks_pass(checks: dict[str, Any]) -> bool:
    for key in ("position_moved_as_expected", "cash_moved_as_expected"):
        if key in checks and checks[key] is False:
            return False
    return bool(checks.get("order_found")) and bool(checks.get("order_filled"))


def _bind_confirm_order(strategy: Any, manager: Any) -> BoundTool:
    def confirm_order(
        *,
        identifier: str,
        symbol: str | None = None,
        side: str | None = None,
        expected_quantity: float | None = None,
        position_before_quantity: float | None = None,
        cash_before: float | None = None,
        max_attempts: int = 3,
        wait_seconds: float | None = None,
    ) -> dict[str, Any]:
        identifier = _require_non_empty_text("identifier", identifier)
        if symbol is not None:
            symbol = _require_single_symbol_text("symbol", symbol)
        if expected_quantity is not None:
            expected_quantity = _require_positive_number("expected_quantity", expected_quantity)
        attempts_limit = _coerce_confirm_attempts(max_attempts)
        wait_value = _coerce_confirm_wait_seconds(wait_seconds, strategy)
        attempts: list[dict[str, Any]] = []
        warnings: list[str] = []
        order = None
        account = _account_snapshot(strategy)
        checks: dict[str, Any] = {"order_found": False, "order_filled": False}
        status = "unknown"

        for attempt_number in range(1, attempts_limit + 1):
            processed_pending = _process_pending_orders_for_confirmation(strategy)
            order = _get_order_for_confirmation(strategy, identifier)
            account = _account_snapshot(strategy)
            status = _order_confirmation_status(
                order,
                attempts_exhausted=attempt_number == attempts_limit,
            )
            order_payload = _order_to_dict(order) if order is not None else None
            checks = _confirmation_checks(
                strategy,
                order=order,
                symbol=symbol,
                side=side,
                expected_quantity=expected_quantity,
                position_before_quantity=position_before_quantity,
                cash_before=cash_before,
                account_snapshot=account,
            )
            attempts.append(
                {
                    "attempt": attempt_number,
                    "processed_pending_orders": processed_pending,
                    "status": status,
                    "is_active": order_payload.get("is_active") if order_payload else None,
                    "is_filled": order_payload.get("is_filled") if order_payload else None,
                }
            )

            if order is None:
                warnings.append(f"Order {identifier} was not found.")
                break
            if status in _ORDER_CONFIRM_TERMINAL_FAILURE_STATUSES or status in {
                "canceled",
                "expired",
                "rejected",
                "error",
            }:
                warnings.append(f"Order {identifier} reached terminal status {status}.")
                break
            if status == "partially_filled":
                warnings.append(f"Order {identifier} is partially filled; dependent orders should not continue.")
                break
            if _confirmation_checks_pass(checks):
                return {
                    "identifier": identifier,
                    "confirmed": True,
                    "can_continue": True,
                    "confirmation_status": status,
                    "attempt_count": attempt_number,
                    "order": order_payload,
                    "account_snapshot": account,
                    "checks": checks,
                    "attempts": attempts,
                    "warnings": warnings,
                }
            if attempt_number < attempts_limit:
                _sleep_for_confirmation(strategy, wait_value)

        if order is not None and status not in {
            "not_found",
            "partially_filled",
            "canceled",
            "expired",
            "rejected",
            "error",
        }:
            status = "open_after_retries" if _safe_call(order, "is_active", False) else status
            if not warnings:
                warnings.append(
                    f"Order {identifier} remained active after {attempts_limit} confirmation attempts. "
                    "Do not submit dependent orders."
                )

        return {
            "identifier": identifier,
            "confirmed": False,
            "can_continue": False,
            "confirmation_status": status,
            "attempt_count": len(attempts),
            "order": _order_to_dict(order) if order is not None else None,
            "account_snapshot": account,
            "checks": checks,
            "attempts": attempts,
            "warnings": warnings,
        }

    return BoundTool(
        name="orders_confirm_order",
        description=(
            "Confirm a previously submitted order by identifier. Use this after every orders_submit_order call "
            "before submitting any later order or writing the final execution summary. The tool refreshes the exact "
            "order, retries internally, and returns whether the order is confirmed filled and whether it is safe to "
            "continue with later orders. This tool does not submit, cancel, or modify orders."
        ),
        function=confirm_order,
        metadata={"kind": "builtin", "replay_on_cache": True, "mutates_trading": True},
    )


def _bind_cancel_order(strategy: Any, manager: Any) -> BoundTool:
    def cancel_order(*, identifier: str) -> dict[str, Any]:
        identifier = _require_non_empty_text("identifier", identifier)
        order = strategy.get_order(identifier)
        if order is None:
            raise ValueError(f"Unknown order identifier: {identifier}")
        strategy.cancel_order(order)
        return {"identifier": identifier, "status": getattr(order, "status", None) or "cancel_requested"}

    return BoundTool(
        name="orders_cancel_order",
        description=(
            "Cancel an existing tracked order by identifier. "
            "Arguments: identifier from orders_open_orders. "
            "Example: orders_cancel_order(identifier='bt_1')."
        ),
        function=cancel_order,
        metadata={"kind": "builtin", "replay_on_cache": True},
    )


def _bind_modify_order(strategy: Any, manager: Any) -> BoundTool:
    def modify_order(*, identifier: str, limit_price: float | None = None, stop_price: float | None = None) -> dict[str, Any]:
        identifier = _require_non_empty_text("identifier", identifier)
        order = strategy.get_order(identifier)
        if order is None:
            raise ValueError(f"Unknown order identifier: {identifier}")
        if limit_price is None and stop_price is None:
            raise ValueError("orders_modify_order requires at least one of limit_price or stop_price.")
        strategy.modify_order(order, limit_price=limit_price, stop_price=stop_price)
        return {
            "identifier": identifier,
            "limit_price": limit_price,
            "stop_price": stop_price,
        }

    return BoundTool(
        name="orders_modify_order",
        description=(
            "Modify an existing tracked order. "
            "Arguments: identifier, optional limit_price, optional stop_price. "
            "Example: orders_modify_order(identifier='bt_7', limit_price=101.25)."
        ),
        function=modify_order,
        metadata={"kind": "builtin", "replay_on_cache": True},
    )


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "as_dict"):
        return _jsonable(value.as_dict())
    if hasattr(value, "item"):
        try:
            return _jsonable(value.item())
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)
    return value


def _bind_list_indicators(strategy: Any, manager: Any) -> BoundTool:
    def list_indicators() -> dict[str, Any]:
        return {
            "ok": True,
            "common_indicators": COMMON_INDICATORS,
            "notes": (
                "Use get_indicator for one current-bar indicator value. "
                "Lumibot slices indicator outputs to the current strategy datetime, so backtests do not see future bars."
            ),
        }

    return BoundTool(
        name="list_indicators",
        description="List common pandas-ta-classic indicator names available through Lumibot's current-bar indicator system.",
        function=list_indicators,
        source="builtin",
        metadata={"kind": "indicator"},
    )


def _bind_get_indicator(strategy: Any, manager: Any) -> BoundTool:
    def get_indicator(
        symbol: str,
        indicator: str,
        timestep: str = "day",
        asset_type: AssetTypeArg = "stock",
        parameters_json: str | None = None,
    ) -> dict[str, Any]:
        asset = _asset_class()(symbol, asset_type=asset_type)
        indicator_name = _require_non_empty_text("indicator", indicator)
        indicator_kwargs: dict[str, Any] = {}
        if parameters_json:
            try:
                parsed = json.loads(parameters_json)
            except json.JSONDecodeError as exc:
                return {
                    "ok": False,
                    "tool_error": True,
                    "error": {
                        "type": "InvalidParametersJson",
                        "message": f"parameters_json must be valid JSON: {exc}",
                    },
                }
            if not isinstance(parsed, dict):
                return {
                    "ok": False,
                    "tool_error": True,
                    "error": {
                        "type": "InvalidParametersJson",
                        "message": "parameters_json must decode to a JSON object.",
                    },
                }
            indicator_kwargs = parsed
        fn = getattr(strategy.indicators, indicator_name)
        value = fn(asset, timestep=timestep, **indicator_kwargs)
        return {
            "ok": True,
            "symbol": symbol.upper(),
            "asset_type": asset_type,
            "indicator": indicator_name,
            "timestep": timestep,
            "datetime": strategy.get_datetime().isoformat() if hasattr(strategy.get_datetime(), "isoformat") else str(strategy.get_datetime()),
            "value": _jsonable(value),
            "no_lookahead": True,
        }

    return BoundTool(
        name="get_indicator",
        description=(
            "Get one technical indicator for the current strategy datetime. "
            "Arguments: symbol, indicator, timestep='day', asset_type='stock', optional parameters_json as a JSON object string. "
            "Examples: get_indicator(symbol='SPY', indicator='rsi', parameters_json='{\"length\": 14}'); "
            "get_indicator(symbol='NVDA', indicator='macd'). "
            "In backtests this returns only the current-bar value and does not expose future bars."
        ),
        function=get_indicator,
        source="builtin",
        metadata={"kind": "indicator"},
    )


def _bind_get_indicators(strategy: Any, manager: Any) -> BoundTool:
    def get_indicators(
        symbol: str,
        indicators: list[str],
        timestep: str = "day",
        asset_type: AssetTypeArg = "stock",
    ) -> dict[str, Any]:
        results = []
        single = _bind_get_indicator(strategy, manager).function
        for name in indicators:
            try:
                results.append(single(symbol=symbol, indicator=name, timestep=timestep, asset_type=asset_type))
            except Exception as exc:
                results.append({"ok": False, "indicator": name, "error": str(exc)})
        return {"ok": True, "symbol": symbol.upper(), "results": results}

    return BoundTool(
        name="get_indicators",
        description="Get multiple current-bar technical indicators for one symbol. Pass indicators=['rsi', 'macd', 'bbands', ...].",
        function=get_indicators,
        source="builtin",
        metadata={"kind": "indicator"},
    )


def _bind_get_income_statement(strategy: Any, manager: Any) -> BoundTool:
    def get_income_statement(symbol: str, as_of: str | None = None, raw: bool = False) -> dict[str, Any]:
        return strategy.fundamentals.get_income_statement(symbol, as_of=as_of, raw=raw)

    return BoundTool(
        name="get_income_statement",
        description=(
            "Get SEC income statement facts for a US equity, gated to as_of or the current strategy datetime. "
            "Fields are kept within one SEC filing/statement period when possible; mismatched old facts are omitted with warnings."
        ),
        function=get_income_statement,
        source="builtin",
        metadata={"kind": "fundamentals", "cache_scope": "strategy_day"},
    )


def _bind_get_balance_sheet(strategy: Any, manager: Any) -> BoundTool:
    def get_balance_sheet(symbol: str, as_of: str | None = None, raw: bool = False) -> dict[str, Any]:
        return strategy.fundamentals.get_balance_sheet(symbol, as_of=as_of, raw=raw)

    return BoundTool(
        name="get_balance_sheet",
        description=(
            "Get SEC balance sheet facts for a US equity, gated to as_of or the current strategy datetime. "
            "Fields are kept within one SEC filing/statement period when possible; mismatched old facts are omitted with warnings."
        ),
        function=get_balance_sheet,
        source="builtin",
        metadata={"kind": "fundamentals", "cache_scope": "strategy_day"},
    )


def _bind_get_cash_flow(strategy: Any, manager: Any) -> BoundTool:
    def get_cash_flow(symbol: str, as_of: str | None = None, raw: bool = False) -> dict[str, Any]:
        return strategy.fundamentals.get_cash_flow(symbol, as_of=as_of, raw=raw)

    return BoundTool(
        name="get_cash_flow",
        description=(
            "Get SEC cash flow facts for a US equity, gated to as_of or the current strategy datetime. "
            "Fields are kept within one SEC filing/statement period when possible; mismatched old facts are omitted with warnings."
        ),
        function=get_cash_flow,
        source="builtin",
        metadata={"kind": "fundamentals", "cache_scope": "strategy_day"},
    )


def _bind_get_company_facts(strategy: Any, manager: Any) -> BoundTool:
    def get_company_facts(
        symbol: str,
        as_of: str | None = None,
        raw: bool = False,
        max_facts: int | None = 80,
    ) -> dict[str, Any]:
        return strategy.fundamentals.get_company_facts(symbol, as_of=as_of, raw=raw, max_facts=max_facts)

    return BoundTool(
        name="get_company_facts",
        description=(
            "Get compact or raw SEC companyfacts for a US equity, gated to as_of or the current strategy datetime. "
            "Default output is capped to important/latest facts so agent runs stay within context; use max_facts or raw=True only when needed."
        ),
        function=get_company_facts,
        source="builtin",
        metadata={"kind": "fundamentals", "cache_scope": "strategy_day"},
    )


def _bind_get_filings(strategy: Any, manager: Any) -> BoundTool:
    def get_filings(symbol: str, form: str | None = None, as_of: str | None = None, limit: int = 10) -> dict[str, Any]:
        return strategy.fundamentals.get_filings(symbol, form=form, as_of=as_of, limit=limit)

    return BoundTool(
        name="get_filings",
        description=(
            "List SEC filings for a US equity, point-in-time gated by as_of/current strategy datetime. "
            "Use form='10-K' or form='10-Q' when you need annual or quarterly reports."
        ),
        function=get_filings,
        source="builtin",
        metadata={"kind": "filings", "cache_scope": "strategy_day"},
    )


def _bind_search_filing(strategy: Any, manager: Any) -> BoundTool:
    def search_filing(
        symbol: str,
        accession_number: str,
        query: str,
        primary_document: str | None = None,
        max_results: int = 5,
    ) -> dict[str, Any]:
        return strategy.fundamentals.search_filing(
            symbol,
            accession_number=accession_number,
            query=query,
            primary_document=primary_document,
            max_results=max_results,
        )

    return BoundTool(
        name="search_filing",
        description=(
            "Keyword-search a cached SEC filing document and return matching context snippets. "
            "Use after get_filings when you want annual-report details about risks, margins, debt, accounting, "
            "customers, liquidity, guidance, dilution, buybacks, or management commentary."
        ),
        function=search_filing,
        source="builtin",
        metadata={"kind": "filings", "cache_scope": "strategy_day"},
    )


def _bind_get_filing_document(strategy: Any, manager: Any) -> BoundTool:
    def get_filing_document(
        symbol: str,
        accession_number: str,
        primary_document: str | None = None,
        max_chars: int | None = 20000,
    ) -> dict[str, Any]:
        return strategy.fundamentals.get_filing_document(
            symbol,
            accession_number=accession_number,
            primary_document=primary_document,
            max_chars=max_chars,
        )

    return BoundTool(
        name="get_filing_document",
        description=(
            "Read a SEC filing document as text. This can be large, so prefer search_filing first. "
            "Use max_chars to bound context, or set max_chars=None only when you intentionally need the full document."
        ),
        function=get_filing_document,
        source="builtin",
        metadata={"kind": "filings", "cache_scope": "strategy_day"},
    )


def _bind_list_filing_sections(strategy: Any, manager: Any) -> BoundTool:
    def list_filing_sections(
        symbol: str,
        accession_number: str,
        primary_document: str | None = None,
    ) -> dict[str, Any]:
        return strategy.fundamentals.list_filing_sections(
            symbol,
            accession_number=accession_number,
            primary_document=primary_document,
        )

    return BoundTool(
        name="list_filing_sections",
        description=(
            "List detected sections in a SEC filing, such as item_1a risk factors, item_7 MD&A, "
            "item_7a market risk, and item_8 financial statements. Use after get_filings before reading a long report."
        ),
        function=list_filing_sections,
        source="builtin",
        metadata={"kind": "filings", "cache_scope": "strategy_day"},
    )


def _bind_get_filing_section(strategy: Any, manager: Any) -> BoundTool:
    def get_filing_section(
        symbol: str,
        accession_number: str,
        section: str,
        primary_document: str | None = None,
        max_chars: int | None = 12000,
    ) -> dict[str, Any]:
        return strategy.fundamentals.get_filing_section(
            symbol,
            accession_number=accession_number,
            section=section,
            primary_document=primary_document,
            max_chars=max_chars,
        )

    return BoundTool(
        name="get_filing_section",
        description=(
            "Read one sanitized text section from a SEC filing without loading the whole report. "
            "Useful section values include risk_factors, mda, liquidity, results_of_operations, market_risk, "
            "financial_statements, controls, or exact IDs like item_1a and item_7."
        ),
        function=get_filing_section,
        source="builtin",
        metadata={"kind": "filings", "cache_scope": "strategy_day"},
    )


def _disabled_fred_tool_if_needed(strategy: Any, manager: Any, tool_name: str) -> BoundTool | None:
    if not bool(getattr(strategy, "is_backtesting", False)):
        return None
    macro = getattr(strategy, "macro", None)
    api_key = str(getattr(macro, "api_key", "") or os.environ.get("FRED_API_KEY") or "").strip()
    if api_key:
        return None

    message = (
        "[agents] FRED macro tools are not configured for point-in-time backtesting and will not be exposed. "
        "Set FRED_API_KEY to use FRED/ALFRED vintage data in backtests."
    )
    if manager is not None:
        warned = getattr(manager, "_warned_unavailable_builtin_tools", None)
        if warned is None:
            warned = set()
            setattr(manager, "_warned_unavailable_builtin_tools", warned)
        if "fred_macro_tools" not in warned:
            warned.add("fred_macro_tools")
            log_message = getattr(strategy, "log_message", None)
            if callable(log_message):
                try:
                    log_message(message, color="yellow")
                except Exception:
                    warning = getattr(manager, "warning", None)
                    if callable(warning):
                        warning(message)
            else:
                warning = getattr(manager, "warning", None)
                if callable(warning):
                    warning(message)

    def unavailable_fred_tool(**kwargs: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "tool_error": True,
            "error": {
                "type": "MissingCredentials",
                "message": "FRED macro tools require FRED_API_KEY during backtests to avoid revised-data look-ahead bias.",
            },
            "observations": [],
        }

    return BoundTool(
        name=tool_name,
        description="FRED macro tool unavailable in backtests without FRED_API_KEY.",
        function=unavailable_fred_tool,
        source="builtin",
        metadata={
            "kind": "macro",
            "disabled": True,
            "disabled_reason": "missing FRED_API_KEY for point-in-time backtesting",
        },
    )


def _bind_list_fred_series(strategy: Any, manager: Any) -> BoundTool:
    disabled = _disabled_fred_tool_if_needed(strategy, manager, "list_fred_series")
    if disabled is not None:
        return disabled

    def list_fred_series(category: str | None = None) -> dict[str, Any]:
        return strategy.macro.list_series(category=category)

    return BoundTool(
        name="list_fred_series",
        description=(
            "List curated Federal Reserve FRED macro series available to agents, grouped by category. "
            "Use this before requesting rates, inflation, labor, growth, liquidity, credit, or risk data."
        ),
        function=list_fred_series,
        source="builtin",
        metadata={"kind": "macro", "cache_scope": "strategy_day"},
    )


def _bind_get_fred_series(strategy: Any, manager: Any) -> BoundTool:
    disabled = _disabled_fred_tool_if_needed(strategy, manager, "get_fred_series")
    if disabled is not None:
        return disabled

    def get_fred_series(
        series_id: str,
        start: str | None = None,
        end: str | None = None,
        as_of: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return strategy.macro.get_series(series_id, start=start, end=end, as_of=as_of, limit=limit)

    return BoundTool(
        name="get_fred_series",
        description=(
            "Get a FRED macro time series. In backtests, as_of defaults to the strategy datetime. "
            "Requires FRED_API_KEY and requests vintage data using realtime_start/realtime_end "
            "so backtests do not accidentally use future revisions."
        ),
        function=get_fred_series,
        source="builtin",
        metadata={"kind": "macro", "cache_scope": "strategy_day"},
    )


def _bind_get_fred_latest(strategy: Any, manager: Any) -> BoundTool:
    disabled = _disabled_fred_tool_if_needed(strategy, manager, "get_fred_latest")
    if disabled is not None:
        return disabled

    def get_fred_latest(series_id: str, as_of: str | None = None) -> dict[str, Any]:
        return strategy.macro.get_latest(series_id, as_of=as_of)

    return BoundTool(
        name="get_fred_latest",
        description=(
            "Get the latest FRED macro observation available as of the strategy datetime or explicit as_of date."
        ),
        function=get_fred_latest,
        source="builtin",
        metadata={"kind": "macro", "cache_scope": "strategy_day"},
    )


def _bind_get_fred_snapshot(strategy: Any, manager: Any) -> BoundTool:
    disabled = _disabled_fred_tool_if_needed(strategy, manager, "get_fred_snapshot")
    if disabled is not None:
        return disabled

    def get_fred_snapshot(series_ids: list[str] | str, as_of: str | None = None) -> dict[str, Any]:
        return strategy.macro.get_snapshot(series_ids, as_of=as_of)

    return BoundTool(
        name="get_fred_snapshot",
        description=(
            "Get latest available values for several FRED macro series as of the strategy datetime. "
            "Pass a list or comma-separated string such as FEDFUNDS,DGS10,CPIAUCSL,UNRATE."
        ),
        function=get_fred_snapshot,
        source="builtin",
        metadata={"kind": "macro", "cache_scope": "strategy_day"},
    )


def _bind_notify_user(strategy: Any, manager: Any) -> BoundTool:
    def notify_user(title: str, message: str, severity: str = "info", enabled: bool | None = None) -> dict[str, Any]:
        results = strategy.notify(title, message, severity=severity, enabled=enabled)
        return {"ok": all(result.ok for result in results), "results": [_jsonable(result.__dict__) for result in results]}

    return BoundTool(
        name="notify_user",
        description=(
            "Send a user notification through configured Lumibot notification providers. "
            "Backtests keep notifications disabled by default unless enabled=True is passed or notifications are configured as enabled."
        ),
        function=notify_user,
        source="builtin",
        metadata={"kind": "notification"},
    )


def _bind_memory_remember(strategy: Any, manager: Any) -> BoundTool:
    def remember(text: str, kind: str = "memory", tags: list[str] | None = None) -> dict[str, Any]:
        return strategy.memory.remember(text, kind=kind, tags=tags, **_agent_memory_context_kwargs())

    return BoundTool(name="remember", description="Store a local Lumibot agent memory or note.", function=remember, source="builtin", metadata={"kind": "memory"})


def _bind_memory_search(strategy: Any, manager: Any) -> BoundTool:
    def search_memory(
        query: str,
        limit: int = 10,
        kind: str | None = None,
        symbol: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        return strategy.memory.search(
            query,
            limit=limit,
            kind=kind,
            symbol=symbol,
            status=status,
            **_agent_memory_context_kwargs(),
        )

    return BoundTool(
        name="search_memory",
        description=(
            "Search local Lumibot agent memories, lessons, decisions, and theses. "
            "Use symbol/status filters when checking an open thesis for a held position."
        ),
        function=search_memory,
        source="builtin",
        metadata={"kind": "memory"},
    )


def _bind_remember_decision(strategy: Any, manager: Any) -> BoundTool:
    def remember_decision(text: str, symbol: str | None = None, action: str | None = None) -> dict[str, Any]:
        return strategy.memory.remember_decision(text, symbol=symbol, action=action, **_agent_memory_context_kwargs())

    return BoundTool(
        name="remember_decision",
        description=(
            "Record an actual AI trading decision in the local decision journal. "
            "Use this for the final trading agent's executed or intentional decision, not for research proposals."
        ),
        function=remember_decision,
        source="builtin",
        metadata={"kind": "memory", "mutates_trading": True},
    )


def _bind_remember_proposal(strategy: Any, manager: Any) -> BoundTool:
    def remember_proposal(
        text: str,
        symbol: str | None = None,
        action: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        return strategy.memory.remember_proposal(
            text,
            symbol=symbol,
            action=action,
            tags=tags,
            **_agent_memory_context_kwargs(),
        )

    return BoundTool(
        name="remember_proposal",
        description="Record a research proposal or non-final trade idea without marking it as an executed trading decision.",
        function=remember_proposal,
        source="builtin",
        metadata={"kind": "memory"},
    )


def _bind_remember_risk_note(strategy: Any, manager: Any) -> BoundTool:
    def remember_risk_note(
        text: str,
        symbol: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        return strategy.memory.remember_risk_note(
            text,
            symbol=symbol,
            tags=tags,
            **_agent_memory_context_kwargs(),
        )

    return BoundTool(
        name="remember_risk_note",
        description="Record a compact risk note or bear-case memory without marking it as an executed trading decision.",
        function=remember_risk_note,
        source="builtin",
        metadata={"kind": "memory"},
    )


def _bind_remember_lesson(strategy: Any, manager: Any) -> BoundTool:
    def remember_lesson(text: str, symbol: str | None = None) -> dict[str, Any]:
        return strategy.memory.remember_lesson(text, symbol=symbol, **_agent_memory_context_kwargs())

    return BoundTool(name="remember_lesson", description="Record a compact trading lesson for future agent runs.", function=remember_lesson, source="builtin", metadata={"kind": "memory"})


def _bind_open_thesis(strategy: Any, manager: Any) -> BoundTool:
    def open_thesis(text: str, symbol: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
        return strategy.memory.open_thesis(text, symbol=symbol, tags=tags, **_agent_memory_context_kwargs())

    return BoundTool(name="open_thesis", description="Open a hedge-fund-style investment thesis in local Lumibot memory.", function=open_thesis, source="builtin", metadata={"kind": "memory"})


def _bind_update_thesis(strategy: Any, manager: Any) -> BoundTool:
    def update_thesis(thesis_id: str, text: str) -> dict[str, Any]:
        return strategy.memory.update_thesis(thesis_id, text, **_agent_memory_context_kwargs())

    return BoundTool(name="update_thesis", description="Append an update to an open investment thesis.", function=update_thesis, source="builtin", metadata={"kind": "memory"})


def _bind_close_thesis(strategy: Any, manager: Any) -> BoundTool:
    def close_thesis(thesis_id: str, text: str) -> dict[str, Any]:
        return strategy.memory.close_thesis(thesis_id, text, **_agent_memory_context_kwargs())

    return BoundTool(name="close_thesis", description="Close an investment thesis and record its outcome/reflection.", function=close_thesis, source="builtin", metadata={"kind": "memory"})


def _order_identifier_from_submit_result(submit_result: dict[str, Any]) -> str | None:
    order = submit_result.get("order") if isinstance(submit_result, dict) else None
    if not isinstance(order, dict):
        return None
    for key in ("identifier", "id", "order_id"):
        value = order.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _submit_and_confirm_blocker_from_exception(exc: Exception) -> dict[str, str]:
    message = str(exc)
    code = "SUBMIT_FAILED"
    if message.startswith("ORDER_READINESS_REQUIRED"):
        code = "ORDER_READINESS_REQUIRED"
    elif message.startswith("NEGATIVE_CASH_NOT_ALLOWED"):
        code = "NEGATIVE_CASH_NOT_ALLOWED"
    elif message.startswith("NEGATIVE_CASH_CHECK_UNAVAILABLE"):
        code = "NEGATIVE_CASH_CHECK_UNAVAILABLE"
    elif "requires" in message.lower():
        code = "INVALID_ORDER_ARGUMENTS"
    return {"code": code, "message": message}


def _submit_and_confirm_blocked_payload(
    *,
    sequence: Any,
    symbol: Any,
    side: Any,
    quantity: Any,
    asset_type: Any,
    order_type: Any,
    time_in_force: Any,
    blockers: list[dict[str, str]],
    warnings: list[str] | None = None,
    submit_result: dict[str, Any] | None = None,
    confirm_result: dict[str, Any] | None = None,
    identifier: str | None = None,
    internal_steps: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "sequence": _jsonable(sequence),
        "symbol": _jsonable(symbol),
        "side": _jsonable(side),
        "quantity": _jsonable(quantity),
        "asset_type": _jsonable(asset_type),
        "order_type": _jsonable(order_type),
        "time_in_force": _jsonable(time_in_force),
        "submitted": submit_result is not None,
        "confirmed": False,
        "can_continue": False,
        "confirmation_status": (
            confirm_result.get("confirmation_status")
            if isinstance(confirm_result, dict)
            else None
        ),
        "identifier": identifier,
        "submit_result": submit_result,
        "confirm_result": confirm_result,
        "internal_steps": internal_steps or [],
        "warnings": list(warnings or []),
        "blockers": blockers,
    }


def _bind_submit_and_confirm_order(strategy: Any, manager: Any) -> BoundTool:
    submit_tool = _bind_submit_order(strategy, manager)
    confirm_tool = _bind_confirm_order(strategy, manager)

    def submit_and_confirm_order(
        *,
        symbol: str,
        quantity: float,
        side: OrderSideArg,
        asset_type: AssetTypeArg = "stock",
        expiration: str | None = None,
        strike: float | None = None,
        right: str | None = None,
        order_type: OrderTypeArg = "market",
        limit_price: float | None = None,
        stop_price: float | None = None,
        stop_limit_price: float | None = None,
        trail_price: float | None = None,
        trail_percent: float | None = None,
        quote_symbol: str | None = None,
        exchange: str | None = None,
        time_in_force: TimeInForceArg = "day",
        sequence: Any = None,
        confirmation_max_attempts: int = 3,
        confirmation_wait_seconds: float | None = None,
    ) -> dict[str, Any]:
        submit_kwargs = {
            "symbol": symbol,
            "quantity": quantity,
            "side": side,
            "asset_type": asset_type,
            "expiration": expiration,
            "strike": strike,
            "right": right,
            "order_type": order_type,
            "limit_price": limit_price,
            "stop_price": stop_price,
            "stop_limit_price": stop_limit_price,
            "trail_price": trail_price,
            "trail_percent": trail_percent,
            "quote_symbol": quote_symbol,
            "exchange": exchange,
            "time_in_force": time_in_force,
        }
        internal_steps = ["orders_submit_order"]
        try:
            submit_result = submit_tool.function(**submit_kwargs)
        except Exception as exc:
            return _submit_and_confirm_blocked_payload(
                sequence=sequence,
                symbol=symbol,
                side=side,
                quantity=quantity,
                asset_type=asset_type,
                order_type=order_type,
                time_in_force=time_in_force,
                blockers=[_submit_and_confirm_blocker_from_exception(exc)],
                internal_steps=internal_steps,
            )

        identifier = _order_identifier_from_submit_result(submit_result)
        if identifier is None:
            return _submit_and_confirm_blocked_payload(
                sequence=sequence,
                symbol=symbol,
                side=side,
                quantity=quantity,
                asset_type=asset_type,
                order_type=order_type,
                time_in_force=time_in_force,
                blockers=[
                    {
                        "code": "ORDER_IDENTIFIER_MISSING",
                        "message": "Order was submitted but no identifier was returned. Stop later orders.",
                    }
                ],
                submit_result=submit_result,
                internal_steps=internal_steps,
            )

        internal_steps.append("orders_confirm_order")
        try:
            confirm_result = confirm_tool.function(
                identifier=identifier,
                symbol=symbol,
                side=side,
                expected_quantity=quantity,
                max_attempts=confirmation_max_attempts,
                wait_seconds=confirmation_wait_seconds,
            )
        except Exception as exc:
            return _submit_and_confirm_blocked_payload(
                sequence=sequence,
                symbol=symbol,
                side=side,
                quantity=quantity,
                asset_type=asset_type,
                order_type=order_type,
                time_in_force=time_in_force,
                blockers=[
                    {
                        "code": "CONFIRMATION_FAILED",
                        "message": (
                            "Order was submitted but not confirmed. Stop later orders. "
                            f"Confirmation failed with error: {exc}"
                        ),
                    }
                ],
                submit_result=submit_result,
                confirm_result=None,
                identifier=identifier,
                internal_steps=internal_steps,
            )
        warnings = list(confirm_result.get("warnings") or []) if isinstance(confirm_result, dict) else []
        if (
            not isinstance(confirm_result, dict)
            or confirm_result.get("confirmed") is not True
            or confirm_result.get("can_continue") is not True
        ):
            return _submit_and_confirm_blocked_payload(
                sequence=sequence,
                symbol=symbol,
                side=side,
                quantity=quantity,
                asset_type=asset_type,
                order_type=order_type,
                time_in_force=time_in_force,
                blockers=[
                    {
                        "code": "CONFIRMATION_FAILED",
                        "message": "Order was submitted but not confirmed. Stop later orders.",
                    }
                ],
                warnings=warnings,
                submit_result=submit_result,
                confirm_result=confirm_result,
                identifier=identifier,
                internal_steps=internal_steps,
            )

        return {
            "sequence": _jsonable(sequence),
            "symbol": _jsonable(symbol),
            "side": _jsonable(side),
            "quantity": _jsonable(quantity),
            "asset_type": _jsonable(asset_type),
            "order_type": _jsonable(order_type),
            "time_in_force": _jsonable(time_in_force),
            "submitted": True,
            "confirmed": True,
            "can_continue": True,
            "confirmation_status": confirm_result.get("confirmation_status"),
            "identifier": identifier,
            "submit_result": submit_result,
            "confirm_result": confirm_result,
            "internal_steps": internal_steps,
            "warnings": warnings,
            "blockers": [],
        }

    return BoundTool(
        name="orders_submit_and_confirm_order",
        description=ORDERS_SUBMIT_AND_CONFIRM_ORDER_DESCRIPTION,
        function=submit_and_confirm_order,
        metadata={"kind": "builtin", "replay_on_cache": True, "mutates_trading": True},
    )


def _execute_order_blocked_payload(
    *,
    sequence: Any = None,
    symbol: Any = None,
    side: Any = None,
    quantity: Any = None,
    asset_type: Any = "stock",
    order_type: Any = "market",
    time_in_force: Any = "day",
    blockers: list[dict[str, str]] | None = None,
    warnings: list[str] | None = None,
    preflight_result: Any = None,
    submit_and_confirm_result: Any = None,
    internal_steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "sequence": _jsonable(sequence),
        "symbol": _jsonable(symbol),
        "side": _jsonable(side),
        "quantity": _jsonable(quantity),
        "asset_type": _jsonable(asset_type),
        "order_type": _jsonable(order_type),
        "time_in_force": _jsonable(time_in_force),
        "execution_status": "blocked",
        "can_continue": False,
        "blockers": list(blockers or []),
        "warnings": list(warnings or []),
        "order": {
            "symbol": _jsonable(symbol),
            "side": _jsonable(side),
            "quantity": _jsonable(quantity),
            "asset_type": _jsonable(asset_type),
            "order_type": _jsonable(order_type),
            "time_in_force": _jsonable(time_in_force),
        },
        "preflight_result": preflight_result,
        "submit_and_confirm_result": submit_and_confirm_result,
        "internal_steps": list(internal_steps or []),
    }


def _execute_order_payload(
    *,
    sequence: Any = None,
    symbol: Any = None,
    side: Any = None,
    quantity: Any = None,
    asset_type: Any = "stock",
    order_type: Any = "market",
    time_in_force: Any = "day",
    preflight_result: Any = None,
    submit_and_confirm_result: Any = None,
    internal_steps: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    confirm_result = (
        submit_and_confirm_result.get("confirm_result")
        if isinstance(submit_and_confirm_result, dict)
        else None
    )
    account_after = (
        confirm_result.get("account_snapshot")
        if isinstance(confirm_result, dict)
        else None
    )
    return {
        "sequence": _jsonable(sequence),
        "symbol": _jsonable(symbol),
        "side": _jsonable(side),
        "quantity": _jsonable(quantity),
        "asset_type": _jsonable(asset_type),
        "order_type": _jsonable(order_type),
        "time_in_force": _jsonable(time_in_force),
        "execution_status": "completed",
        "can_continue": True,
        "blockers": [],
        "warnings": list(warnings or []),
        "order": {
            "symbol": _jsonable(symbol),
            "side": _jsonable(side),
            "quantity": _jsonable(quantity),
            "asset_type": _jsonable(asset_type),
            "order_type": _jsonable(order_type),
            "time_in_force": _jsonable(time_in_force),
        },
        "preflight_result": preflight_result,
        "submit_and_confirm_result": submit_and_confirm_result,
        "internal_steps": list(internal_steps or []),
        "account_after": account_after,
    }


def _bind_execute_order(strategy: Any, manager: Any) -> BoundTool:
    preflight_tool = _bind_preflight_check(strategy, manager)
    submit_and_confirm_tool = _bind_submit_and_confirm_order(strategy, manager)

    def execute_order(
        *,
        symbol: str,
        quantity: float,
        side: OrderSideArg,
        asset_type: AssetTypeArg = "stock",
        expiration: str | None = None,
        strike: float | None = None,
        right: str | None = None,
        order_type: OrderTypeArg = "market",
        limit_price: float | None = None,
        stop_price: float | None = None,
        stop_limit_price: float | None = None,
        trail_price: float | None = None,
        trail_percent: float | None = None,
        quote_symbol: str | None = None,
        exchange: str | None = None,
        time_in_force: TimeInForceArg = "day",
        sequence: Any = None,
        confirmation_max_attempts: int = 3,
        confirmation_wait_seconds: float | None = None,
    ) -> dict[str, Any]:
        internal_steps: list[dict[str, Any]] = []
        try:
            preflight_result = preflight_tool.function(
                sequence=sequence,
                symbol=symbol,
                side=side,
                quantity=quantity,
                asset_type=asset_type,
                order_type=order_type,
                time_in_force=time_in_force,
            )
        except Exception as exc:
            return _execute_order_blocked_payload(
                sequence=sequence,
                symbol=symbol,
                side=side,
                quantity=quantity,
                asset_type=asset_type,
                order_type=order_type,
                time_in_force=time_in_force,
                blockers=[_submit_and_confirm_blocker_from_exception(exc)],
                internal_steps=[
                    {
                        "step": "preflight",
                        "tool": "orders_preflight_check",
                        "status": "blocked",
                    }
                ],
            )

        preflight_ready = isinstance(preflight_result, dict) and preflight_result.get("can_submit") is True
        preflight_warnings = list(preflight_result.get("warnings") or []) if isinstance(preflight_result, dict) else []
        internal_steps.append(
            {
                "step": "preflight",
                "tool": "orders_preflight_check",
                "status": "ready" if preflight_ready else "blocked",
            }
        )
        if not preflight_ready:
            blockers = (
                list(preflight_result.get("blockers") or [])
                if isinstance(preflight_result, dict)
                else [
                    {
                        "code": "PREFLIGHT_FAILED",
                        "message": "orders_preflight_check did not return a ready result.",
                    }
                ]
            )
            return _execute_order_blocked_payload(
                sequence=sequence,
                symbol=symbol,
                side=side,
                quantity=quantity,
                asset_type=asset_type,
                order_type=order_type,
                time_in_force=time_in_force,
                blockers=blockers,
                warnings=preflight_warnings,
                preflight_result=preflight_result,
                internal_steps=internal_steps,
            )

        try:
            submit_and_confirm_result = submit_and_confirm_tool.function(
                symbol=symbol,
                quantity=quantity,
                side=side,
                asset_type=asset_type,
                expiration=expiration,
                strike=strike,
                right=right,
                order_type=order_type,
                limit_price=limit_price,
                stop_price=stop_price,
                stop_limit_price=stop_limit_price,
                trail_price=trail_price,
                trail_percent=trail_percent,
                quote_symbol=quote_symbol,
                exchange=exchange,
                time_in_force=time_in_force,
                sequence=sequence,
                confirmation_max_attempts=confirmation_max_attempts,
                confirmation_wait_seconds=confirmation_wait_seconds,
            )
        except Exception as exc:
            internal_steps.append(
                {
                    "step": "submit_and_confirm",
                    "tool": "orders_submit_and_confirm_order",
                    "status": "blocked",
                }
            )
            return _execute_order_blocked_payload(
                sequence=sequence,
                symbol=symbol,
                side=side,
                quantity=quantity,
                asset_type=asset_type,
                order_type=order_type,
                time_in_force=time_in_force,
                blockers=[_submit_and_confirm_blocker_from_exception(exc)],
                warnings=preflight_warnings,
                preflight_result=preflight_result,
                submit_and_confirm_result=None,
                internal_steps=internal_steps,
            )

        submit_ready = (
            isinstance(submit_and_confirm_result, dict)
            and submit_and_confirm_result.get("can_continue") is True
            and submit_and_confirm_result.get("confirmed") is True
        )
        internal_steps.append(
            {
                "step": "submit_and_confirm",
                "tool": "orders_submit_and_confirm_order",
                "status": "confirmed" if submit_ready else "blocked",
            }
        )
        submit_warnings = (
            list(submit_and_confirm_result.get("warnings") or [])
            if isinstance(submit_and_confirm_result, dict)
            else []
        )
        warnings = preflight_warnings + submit_warnings
        if not submit_ready:
            blockers = (
                list(submit_and_confirm_result.get("blockers") or [])
                if isinstance(submit_and_confirm_result, dict)
                else [
                    {
                        "code": "SUBMIT_AND_CONFIRM_FAILED",
                        "message": "orders_submit_and_confirm_order did not confirm the order.",
                    }
                ]
            )
            return _execute_order_blocked_payload(
                sequence=sequence,
                symbol=symbol,
                side=side,
                quantity=quantity,
                asset_type=asset_type,
                order_type=order_type,
                time_in_force=time_in_force,
                blockers=blockers,
                warnings=warnings,
                preflight_result=preflight_result,
                submit_and_confirm_result=submit_and_confirm_result,
                internal_steps=internal_steps,
            )

        return _execute_order_payload(
            sequence=sequence,
            symbol=symbol,
            side=side,
            quantity=quantity,
            asset_type=asset_type,
            order_type=order_type,
            time_in_force=time_in_force,
            preflight_result=preflight_result,
            submit_and_confirm_result=submit_and_confirm_result,
            internal_steps=internal_steps,
            warnings=warnings,
        )

    return BoundTool(
        name="orders_execute_order",
        description=ORDERS_EXECUTE_ORDER_DESCRIPTION,
        function=execute_order,
        metadata={"kind": "builtin", "replay_on_cache": True, "mutates_trading": True},
    )


def _execution_plan_blocker(
    code: str,
    message: str,
    *,
    sequence: Any = None,
    symbol: Any = None,
) -> dict[str, Any]:
    blocker: dict[str, Any] = {"code": code, "message": message}
    if sequence is not None:
        blocker["sequence"] = _jsonable(sequence)
    if symbol is not None:
        blocker["symbol"] = _jsonable(symbol)
    return blocker


def _execution_plan_invalid_payload(
    blocker: dict[str, Any],
    *,
    intent: Any = None,
    orders_requested: int = 0,
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "plan_status": "invalid",
        "can_continue": False,
        "intent": _jsonable(intent),
        "orders_requested": orders_requested,
        "orders_attempted": 0,
        "orders_completed": 0,
        "orders_blocked": 0,
        "orders_skipped": 0,
        "completed_orders": [],
        "blocked_orders": [],
        "skipped_orders": [],
        "order_results": [],
        "initial_account_snapshot": None,
        "final_account_snapshot": None,
        "blockers": [blocker],
        "warnings": [],
        "summary": "No orders were submitted because the execution plan was invalid.",
    }
    return _with_execution_plan_model_facing_summary(payload)


def _execution_plan_order_summary(order: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, Any]:
    result = result if isinstance(result, dict) else {}
    submit_and_confirm_result = result.get("submit_and_confirm_result")
    if not isinstance(submit_and_confirm_result, dict):
        submit_and_confirm_result = {}
    identifier = submit_and_confirm_result.get("identifier")
    summary = {
        "sequence": _jsonable(order.get("sequence")),
        "symbol": _jsonable(order.get("symbol")),
        "side": _jsonable(order.get("side")),
        "quantity": _jsonable(order.get("quantity")),
        "asset_type": _jsonable(order.get("asset_type")),
        "order_type": _jsonable(order.get("order_type")),
        "time_in_force": _jsonable(order.get("time_in_force")),
        "execution_status": _jsonable(result.get("execution_status")),
        "confirmed": submit_and_confirm_result.get("confirmed") is True,
    }
    if identifier is not None:
        summary["order_identifier"] = _jsonable(identifier)
    return summary


def _execution_plan_blocked_order_summary(order: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    summary = _execution_plan_order_summary(order, result)
    summary["execution_status"] = "blocked"
    summary["can_continue"] = False
    summary["blockers"] = list(result.get("blockers") or [])
    summary["warnings"] = list(result.get("warnings") or [])
    return summary


def _execution_plan_skipped_orders(
    orders: list[dict[str, Any]],
    *,
    stopped_sequence: Any,
) -> list[dict[str, Any]]:
    return [
        {
            "sequence": _jsonable(order.get("sequence")),
            "symbol": _jsonable(order.get("symbol")),
            "side": _jsonable(order.get("side")),
            "quantity": _jsonable(order.get("quantity")),
            "asset_type": _jsonable(order.get("asset_type")),
            "order_type": _jsonable(order.get("order_type")),
            "time_in_force": _jsonable(order.get("time_in_force")),
            "execution_status": "skipped",
            "skip_reason": f"stopped_after_sequence_{stopped_sequence}_blocked",
        }
        for order in orders
    ]


def _execution_plan_result_item(order: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "sequence": _jsonable(order.get("sequence")),
        "symbol": _jsonable(order.get("symbol")),
        "side": _jsonable(order.get("side")),
        "quantity": _jsonable(order.get("quantity")),
        "execution_status": _jsonable(result.get("execution_status")),
        "can_continue": result.get("can_continue") is True,
        "blockers": list(result.get("blockers") or []),
        "warnings": list(result.get("warnings") or []),
        "order_result": result,
    }


def _first_non_null_value(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _execution_plan_confirmation_order_payload(result: dict[str, Any]) -> dict[str, Any]:
    submit_and_confirm_result = result.get("submit_and_confirm_result")
    if not isinstance(submit_and_confirm_result, dict):
        return {}
    confirm_result = submit_and_confirm_result.get("confirm_result")
    if isinstance(confirm_result, dict):
        confirm_order = confirm_result.get("order")
        if isinstance(confirm_order, dict):
            return confirm_order
    submit_result = submit_and_confirm_result.get("submit_result")
    if isinstance(submit_result, dict):
        submit_order = submit_result.get("order")
        if isinstance(submit_order, dict):
            return submit_order
    return {}


def _execution_plan_model_order_summary(
    order: dict[str, Any],
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = _execution_plan_order_summary(order, result)
    result = result if isinstance(result, dict) else {}
    submit_and_confirm_result = result.get("submit_and_confirm_result")
    if not isinstance(submit_and_confirm_result, dict):
        submit_and_confirm_result = {}
    confirmation_order = _execution_plan_confirmation_order_payload(result)

    confirmation_status = submit_and_confirm_result.get("confirmation_status")
    if confirmation_status is not None:
        summary["confirmation_status"] = _jsonable(confirmation_status)

    confirmed_or_filled = (
        submit_and_confirm_result.get("confirmed") is True
        or confirmation_status == "filled"
        or confirmation_order.get("is_filled") is True
    )
    explicit_filled_quantity = confirmation_order.get("filled_quantity")
    filled_quantity = explicit_filled_quantity
    if filled_quantity is None and confirmed_or_filled:
        filled_quantity = confirmation_order.get("quantity")
    if filled_quantity is not None:
        summary["filled_quantity"] = _jsonable(filled_quantity)

    fill_price = _first_non_null_value(
        confirmation_order.get("avg_fill_price"),
        confirmation_order.get("fill_price"),
        confirmation_order.get("average_fill_price"),
    )
    if fill_price is not None and (confirmed_or_filled or explicit_filled_quantity is not None):
        summary["fill_price"] = _jsonable(fill_price)

    return summary


def _execution_plan_model_position_summary(position: Any) -> dict[str, Any]:
    if not isinstance(position, dict):
        return {"symbol": _jsonable(position)}
    summary: dict[str, Any] = {}
    for source_key, target_key in (
        ("symbol", "symbol"),
        ("asset_type", "asset_type"),
        ("quantity", "quantity"),
        ("avg_fill_price", "avg_fill_price"),
        ("current_price", "current_price"),
        ("market_value", "market_value"),
    ):
        value = position.get(source_key)
        if value is not None:
            summary[target_key] = _jsonable(value)
    asset = position.get("asset")
    if "symbol" not in summary and isinstance(asset, dict):
        symbol = asset.get("symbol")
        if symbol is not None:
            summary["symbol"] = _jsonable(symbol)
        asset_type = asset.get("asset_type")
        if asset_type is not None:
            summary.setdefault("asset_type", _jsonable(asset_type))
    return summary


def _execution_plan_model_account_summary(account: Any) -> dict[str, Any] | None:
    if not isinstance(account, dict):
        return None
    summary: dict[str, Any] = {}
    for source_key, target_key in (
        ("cash", "cash"),
        ("cash_balance", "cash"),
        ("portfolio_value", "portfolio_value"),
        ("account_value", "portfolio_value"),
    ):
        value = account.get(source_key)
        if value is not None and target_key not in summary:
            summary[target_key] = _jsonable(value)
    positions = account.get("positions")
    if isinstance(positions, list):
        summary["positions"] = [_execution_plan_model_position_summary(position) for position in positions]
    return summary


def _execution_plan_model_facing_summary(payload: dict[str, Any]) -> dict[str, Any]:
    result_by_sequence = {
        item.get("sequence"): item.get("order_result")
        for item in list(payload.get("order_results") or [])
        if isinstance(item, dict) and isinstance(item.get("order_result"), dict)
    }
    completed_orders = [
        _execution_plan_model_order_summary(order, result_by_sequence.get(order.get("sequence")))
        for order in list(payload.get("completed_orders") or [])
        if isinstance(order, dict)
    ]
    blocked_orders = []
    for order in list(payload.get("blocked_orders") or []):
        if isinstance(order, dict):
            blocked_orders.append(
                {
                    **_execution_plan_model_order_summary(
                        order,
                        result_by_sequence.get(order.get("sequence")),
                    ),
                    "execution_status": _jsonable(order.get("execution_status") or "blocked"),
                    "can_continue": order.get("can_continue") is True,
                    "blockers": list(order.get("blockers") or []),
                    "warnings": list(order.get("warnings") or []),
                }
            )
    skipped_orders = [
        {
            "sequence": _jsonable(order.get("sequence")),
            "symbol": _jsonable(order.get("symbol")),
            "side": _jsonable(order.get("side")),
            "quantity": _jsonable(order.get("quantity")),
            "asset_type": _jsonable(order.get("asset_type")),
            "order_type": _jsonable(order.get("order_type")),
            "time_in_force": _jsonable(order.get("time_in_force")),
            "execution_status": _jsonable(order.get("execution_status") or "skipped"),
            "skip_reason": _jsonable(order.get("skip_reason")),
        }
        for order in list(payload.get("skipped_orders") or [])
        if isinstance(order, dict)
    ]
    final_account = _execution_plan_model_account_summary(payload.get("final_account_snapshot"))
    return {
        "schema_version": 1,
        "tool_name": "execution_plan_execute",
        "response_type": "model_facing_summary",
        "plan_status": _jsonable(payload.get("plan_status")),
        "can_continue": payload.get("can_continue") is True,
        "intent": _jsonable(payload.get("intent")),
        "orders_requested": int(payload.get("orders_requested") or 0),
        "orders_attempted": int(payload.get("orders_attempted") or 0),
        "orders_completed": int(payload.get("orders_completed") or 0),
        "orders_blocked": int(payload.get("orders_blocked") or 0),
        "orders_skipped": int(payload.get("orders_skipped") or 0),
        "completed_orders": completed_orders,
        "blocked_orders": blocked_orders,
        "skipped_orders": skipped_orders,
        "final_account": final_account,
        "warnings": list(payload.get("warnings") or []),
        "blockers": list(payload.get("blockers") or []),
        "summary": _jsonable(payload.get("summary")),
        "audit_details_available": True,
    }


def _with_execution_plan_model_facing_summary(payload: dict[str, Any]) -> dict[str, Any]:
    payload["model_facing_summary"] = _execution_plan_model_facing_summary(payload)
    return payload


def _execution_plan_final_account_snapshot(
    strategy: Any,
    order_results: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for item in reversed(order_results):
        result = item.get("order_result")
        if isinstance(result, dict):
            account_after = result.get("account_after")
            if isinstance(account_after, dict):
                return account_after
            preflight_result = result.get("preflight_result")
            if isinstance(preflight_result, dict):
                account = preflight_result.get("account")
                if isinstance(account, dict):
                    return account
    try:
        return _account_snapshot(strategy)
    except Exception:
        return None


def _execution_plan_completed_payload(
    *,
    strategy: Any,
    intent: str,
    orders_requested: int,
    completed_orders: list[dict[str, Any]],
    order_results: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    if orders_requested == 0:
        summary = "No planned orders were submitted because the execution plan intent was hold."
    else:
        summary = f"All {orders_requested} planned orders were completed and confirmed."
    payload = {
        "schema_version": 1,
        "plan_status": "completed",
        "can_continue": True,
        "intent": intent,
        "orders_requested": orders_requested,
        "orders_attempted": len(order_results),
        "orders_completed": len(completed_orders),
        "orders_blocked": 0,
        "orders_skipped": 0,
        "completed_orders": completed_orders,
        "blocked_orders": [],
        "skipped_orders": [],
        "order_results": order_results,
        "initial_account_snapshot": None,
        "final_account_snapshot": _execution_plan_final_account_snapshot(strategy, order_results),
        "blockers": [],
        "warnings": warnings,
        "summary": summary,
    }
    return _with_execution_plan_model_facing_summary(payload)


def _execution_plan_blocked_payload(
    *,
    strategy: Any,
    intent: str,
    orders_requested: int,
    completed_orders: list[dict[str, Any]],
    blocked_orders: list[dict[str, Any]],
    skipped_orders: list[dict[str, Any]],
    order_results: list[dict[str, Any]],
    warnings: list[str],
    stopped_sequence: Any,
    stopped_symbol: Any,
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "plan_status": "blocked",
        "can_continue": False,
        "intent": intent,
        "orders_requested": orders_requested,
        "orders_attempted": len(order_results),
        "orders_completed": len(completed_orders),
        "orders_blocked": len(blocked_orders),
        "orders_skipped": len(skipped_orders),
        "completed_orders": completed_orders,
        "blocked_orders": blocked_orders,
        "skipped_orders": skipped_orders,
        "order_results": order_results,
        "initial_account_snapshot": None,
        "final_account_snapshot": _execution_plan_final_account_snapshot(strategy, order_results),
        "blockers": list(blocked_orders[-1].get("blockers") or [])
        if blocked_orders
        else [
            _execution_plan_blocker(
                "ORDER_BLOCKED",
                f"Execution stopped at sequence {stopped_sequence}.",
                sequence=stopped_sequence,
                symbol=stopped_symbol,
            )
        ],
        "warnings": warnings,
        "summary": f"Execution stopped at sequence {stopped_sequence} because {stopped_symbol} was blocked.",
    }
    return _with_execution_plan_model_facing_summary(payload)


def _execution_plan_exception_result(order: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "sequence": _jsonable(order.get("sequence")),
        "symbol": _jsonable(order.get("symbol")),
        "side": _jsonable(order.get("side")),
        "quantity": _jsonable(order.get("quantity")),
        "asset_type": _jsonable(order.get("asset_type")),
        "order_type": _jsonable(order.get("order_type")),
        "time_in_force": _jsonable(order.get("time_in_force")),
        "execution_status": "blocked",
        "can_continue": False,
        "blockers": [
            _execution_plan_blocker(
                "EXECUTION_EXCEPTION",
                str(exc),
                sequence=order.get("sequence"),
                symbol=order.get("symbol"),
            )
        ],
        "warnings": [],
        "order": {
            "symbol": _jsonable(order.get("symbol")),
            "side": _jsonable(order.get("side")),
            "quantity": _jsonable(order.get("quantity")),
            "asset_type": _jsonable(order.get("asset_type")),
            "order_type": _jsonable(order.get("order_type")),
            "time_in_force": _jsonable(order.get("time_in_force")),
        },
        "preflight_result": None,
        "submit_and_confirm_result": None,
        "internal_steps": [],
    }


def _execution_plan_cash_value(strategy: Any) -> float | None:
    get_cash = getattr(strategy, "get_cash", None)
    if not callable(get_cash):
        return None
    try:
        cash = float(get_cash())
    except Exception:
        return None
    if not math.isfinite(cash):
        return None
    return cash


def _execution_plan_negative_cash_result_if_needed(
    strategy: Any,
    order: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any] | None:
    if not _agent_negative_cash_guard_enabled():
        return None
    if str(order.get("side")).strip().lower() != "buy":
        return None
    cash = _execution_plan_cash_value(strategy)
    if cash is None or cash >= 0:
        return None

    blocker = _execution_plan_blocker(
        "NEGATIVE_CASH_INVARIANT_VIOLATION",
        f"Cash became negative after sequence {order.get('sequence')} buy for {order.get('symbol')}: {cash:.2f}.",
        sequence=order.get("sequence"),
        symbol=order.get("symbol"),
    )
    guarded = dict(result)
    guarded["execution_status"] = "blocked"
    guarded["can_continue"] = False
    guarded["blockers"] = list(guarded.get("blockers") or []) + [blocker]
    guarded["warnings"] = list(guarded.get("warnings") or []) + [
        "NEGATIVE_CASH_INVARIANT_VIOLATION: execution stopped because cash became negative after a buy."
    ]
    guarded["account_after"] = {
        **(guarded.get("account_after") if isinstance(guarded.get("account_after"), dict) else {}),
        "cash": cash,
    }
    return guarded


def _validate_execution_plan_sequence(orders: list[Any]) -> dict[str, Any] | None:
    sequences: list[int] = []
    for index, order in enumerate(orders):
        if not isinstance(order, dict):
            return _execution_plan_blocker(
                "INVALID_ORDER_FIELDS",
                "execution_plan.orders entries must be objects.",
            )
        sequence = order.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            return _execution_plan_blocker(
                "INVALID_ORDER_SEQUENCE",
                "execution_plan.orders sequence values must be integers.",
                sequence=sequence,
                symbol=order.get("symbol"),
            )
        if sequence <= 0:
            return _execution_plan_blocker(
                "INVALID_ORDER_SEQUENCE",
                "execution_plan.orders sequence values must be positive integers.",
                sequence=sequence,
                symbol=order.get("symbol"),
            )
        sequences.append(sequence)
        if sequence != index + 1 and sorted(sequences) == list(range(1, len(sequences) + 1)):
            return _execution_plan_blocker(
                "INVALID_ORDER_SEQUENCE",
                "execution_plan.orders must already be listed in ascending sequence order.",
                sequence=sequence,
                symbol=order.get("symbol"),
            )

    if len(set(sequences)) != len(sequences):
        return _execution_plan_blocker(
            "INVALID_ORDER_SEQUENCE",
            "execution_plan.orders must have unique sequence values.",
        )
    expected = list(range(1, len(orders) + 1))
    if sequences != expected:
        if sorted(sequences) == expected:
            return _execution_plan_blocker(
                "INVALID_ORDER_SEQUENCE",
                "execution_plan.orders must already be listed in ascending sequence order.",
            )
        return _execution_plan_blocker(
            "INVALID_ORDER_SEQUENCE",
            "execution_plan.orders sequence values must exactly cover 1..N with no gaps.",
        )
    return None


def _validate_execution_plan_order_fields(order: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    required_fields = {
        "sequence",
        "action",
        "symbol",
        "side",
        "quantity_mode",
        "quantity",
        "asset_type",
        "order_type",
        "time_in_force",
    }
    unsupported_fields = sorted(str(field) for field in set(order) - required_fields)
    if unsupported_fields:
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            f"execution_plan order contains unsupported fields: {', '.join(unsupported_fields)}.",
            sequence=order.get("sequence"),
            symbol=order.get("symbol"),
        )
    missing_fields = sorted(required_fields - set(order))
    if missing_fields:
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            f"execution_plan order is missing required fields: {', '.join(missing_fields)}.",
            sequence=order.get("sequence"),
            symbol=order.get("symbol"),
        )
    if order.get("action") != "submit_order":
        return None, _execution_plan_blocker(
            "INVALID_ORDER_ACTION",
            "execution_plan orders must use action='submit_order'.",
            sequence=order.get("sequence"),
            symbol=order.get("symbol"),
        )

    symbol = order.get("symbol")
    if not isinstance(symbol, str) or not symbol or symbol != symbol.strip():
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            "execution_plan order symbol must be a non-empty string with no surrounding whitespace.",
            sequence=order.get("sequence"),
            symbol=symbol,
        )
    if "," in symbol:
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            "execution_plan order symbol must be one tradable symbol.",
            sequence=order.get("sequence"),
            symbol=symbol,
        )

    side = order.get("side")
    if side not in {"buy", "sell"}:
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            "execution_plan order side must be buy or sell.",
            sequence=order.get("sequence"),
            symbol=symbol,
        )

    if order.get("quantity_mode") != "shares":
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            "execution_plan order quantity_mode must be shares.",
            sequence=order.get("sequence"),
            symbol=symbol,
        )

    raw_quantity = order.get("quantity")
    if isinstance(raw_quantity, bool) or not isinstance(raw_quantity, int) or raw_quantity <= 0:
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            "execution_plan order quantity must be a positive whole-share integer.",
            sequence=order.get("sequence"),
            symbol=symbol,
        )

    asset_type = order.get("asset_type")
    if asset_type not in {"stock", "us_equity"}:
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            "execution_plan order asset_type must be stock or us_equity.",
            sequence=order.get("sequence"),
            symbol=symbol,
        )

    if order.get("order_type") != "market":
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            "execution_plan order order_type must be market.",
            sequence=order.get("sequence"),
            symbol=symbol,
        )

    if order.get("time_in_force") != "day":
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            "execution_plan order time_in_force must be day.",
            sequence=order.get("sequence"),
            symbol=symbol,
        )

    return (
        {
            "sequence": order["sequence"],
            "action": order["action"],
            "symbol": order["symbol"],
            "side": order["side"],
            "quantity_mode": order["quantity_mode"],
            "quantity": raw_quantity,
            "asset_type": order["asset_type"],
            "order_type": order["order_type"],
            "time_in_force": order["time_in_force"],
        },
        None,
    )


def _validate_execution_plan(
    execution_plan: Any,
) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None, int]:
    if not isinstance(execution_plan, dict) or not {"schema_version", "intent", "orders"}.issubset(execution_plan):
        return (
            None,
            [],
            _execution_plan_blocker(
                "MISSING_EXECUTION_PLAN",
                "execution_plan must be one complete object with schema_version, intent, and orders.",
            ),
            0,
        )

    supported_top_level_fields = {"schema_version", "intent", "orders"}
    unsupported_top_level_fields = sorted(str(field) for field in set(execution_plan) - supported_top_level_fields)
    if unsupported_top_level_fields:
        return (
            _jsonable(execution_plan.get("intent")),
            [],
            _execution_plan_blocker(
                "INVALID_EXECUTION_PLAN",
                f"execution_plan contains unsupported top-level fields: {', '.join(unsupported_top_level_fields)}.",
            ),
            0,
        )

    intent = execution_plan.get("intent")
    raw_orders = execution_plan.get("orders")
    orders_requested = len(raw_orders) if isinstance(raw_orders, list) else 0
    if execution_plan.get("schema_version") != 1:
        return (
            _jsonable(intent),
            [],
            _execution_plan_blocker(
                "UNSUPPORTED_PLAN_SCHEMA_VERSION",
                "execution_plan.schema_version must be 1.",
            ),
            orders_requested,
        )
    if intent not in {"rebalance", "hold"}:
        return (
            _jsonable(intent),
            [],
            _execution_plan_blocker(
                "UNSUPPORTED_PLAN_INTENT",
                "execution_plan.intent must be rebalance or hold.",
            ),
            orders_requested,
        )
    if not isinstance(raw_orders, list):
        return (
            intent,
            [],
            _execution_plan_blocker(
                "INVALID_EXECUTION_PLAN",
                "execution_plan.orders must be a list.",
            ),
            orders_requested,
        )
    if intent == "hold" and raw_orders:
        return (
            intent,
            [],
            _execution_plan_blocker(
                "INVALID_EXECUTION_PLAN",
                "execution_plan.orders must be empty when intent is hold.",
            ),
            len(raw_orders),
        )

    sequence_blocker = _validate_execution_plan_sequence(raw_orders)
    if sequence_blocker is not None:
        return intent, [], sequence_blocker, len(raw_orders)

    normalized_orders: list[dict[str, Any]] = []
    for order in raw_orders:
        normalized_order, blocker = _validate_execution_plan_order_fields(order)
        if blocker is not None:
            return intent, [], blocker, len(raw_orders)
        if normalized_order is not None:
            normalized_orders.append(normalized_order)
    return intent, normalized_orders, None, len(raw_orders)


def _bind_execute_plan(strategy: Any, manager: Any) -> BoundTool:
    execute_order_tool = _bind_execute_order(strategy, manager)

    def execute_plan(execution_plan: dict[str, Any] | None = None) -> dict[str, Any]:
        intent, orders, blocker, orders_requested = _validate_execution_plan(execution_plan)
        if blocker is not None:
            return _execution_plan_invalid_payload(
                blocker,
                intent=intent,
                orders_requested=orders_requested,
            )
        if intent == "hold":
            return _execution_plan_completed_payload(
                strategy=strategy,
                intent="hold",
                orders_requested=0,
                completed_orders=[],
                order_results=[],
                warnings=[],
            )

        completed_orders: list[dict[str, Any]] = []
        blocked_orders: list[dict[str, Any]] = []
        order_results: list[dict[str, Any]] = []
        warnings: list[str] = []
        for index, order in enumerate(orders):
            try:
                result = execute_order_tool.function(
                    sequence=order["sequence"],
                    symbol=order["symbol"],
                    quantity=order["quantity"],
                    side=order["side"],
                    asset_type=order["asset_type"],
                    order_type=order["order_type"],
                    time_in_force=order["time_in_force"],
                )
            except Exception as exc:
                result = _execution_plan_exception_result(order, exc)

            if isinstance(result, dict):
                result_warnings = [str(warning) for warning in list(result.get("warnings") or [])]
            else:
                result = _execution_plan_exception_result(
                    order,
                    RuntimeError("orders_execute_order did not return a structured result."),
                )
                result_warnings = []
            invariant_result = _execution_plan_negative_cash_result_if_needed(strategy, order, result)
            if invariant_result is not None:
                result = invariant_result
                result_warnings = [str(warning) for warning in list(result.get("warnings") or [])]
            warnings.extend(result_warnings)
            order_results.append(_execution_plan_result_item(order, result))

            execution_completed = (
                result.get("execution_status") == "completed"
                and result.get("can_continue") is True
            )
            if execution_completed:
                completed_orders.append(_execution_plan_order_summary(order, result))
                continue

            blocked_orders.append(_execution_plan_blocked_order_summary(order, result))
            skipped_orders = _execution_plan_skipped_orders(
                orders[index + 1 :],
                stopped_sequence=order["sequence"],
            )
            return _execution_plan_blocked_payload(
                strategy=strategy,
                intent=str(intent),
                orders_requested=orders_requested,
                completed_orders=completed_orders,
                blocked_orders=blocked_orders,
                skipped_orders=skipped_orders,
                order_results=order_results,
                warnings=warnings,
                stopped_sequence=order["sequence"],
                stopped_symbol=order["symbol"],
            )

        return _execution_plan_completed_payload(
            strategy=strategy,
            intent=str(intent),
            orders_requested=orders_requested,
            completed_orders=completed_orders,
            order_results=order_results,
            warnings=warnings,
        )

    return BoundTool(
        name="execution_plan_execute",
        description=EXECUTION_PLAN_EXECUTE_DESCRIPTION,
        function=execute_plan,
        metadata={"kind": "builtin", "replay_on_cache": True, "mutates_trading": True},
    )


def _bind_submit_order(strategy: Any, manager: Any) -> BoundTool:
    def submit_order(
        *,
        symbol: str,
        quantity: float,
        side: OrderSideArg,
        asset_type: AssetTypeArg = "stock",
        expiration: str | None = None,
        strike: float | None = None,
        right: str | None = None,
        order_type: OrderTypeArg = "market",
        limit_price: float | None = None,
        stop_price: float | None = None,
        stop_limit_price: float | None = None,
        trail_price: float | None = None,
        trail_percent: float | None = None,
        quote_symbol: str | None = None,
        exchange: str | None = None,
        time_in_force: TimeInForceArg = "day",
    ) -> dict[str, Any]:
        symbol = _require_single_symbol_text("symbol", symbol)
        quantity = _require_positive_number("quantity", quantity)
        _require_agent_order_readiness(
            symbol,
            side=side,
            quantity=quantity,
            asset_type=asset_type,
            order_type=order_type,
            time_in_force=time_in_force,
        )
        if order_type == "limit" and limit_price is None:
            raise ValueError("orders_submit_order with order_type='limit' requires limit_price.")
        if order_type in {"stop", "stop_limit"} and stop_price is None:
            raise ValueError(f"orders_submit_order with order_type={order_type!r} requires stop_price.")
        if order_type == "stop_limit" and stop_limit_price is None and limit_price is None:
            raise ValueError("orders_submit_order with order_type='stop_limit' requires stop_limit_price or limit_price.")
        if order_type == "trailing_stop" and trail_price is None and trail_percent is None:
            raise ValueError("orders_submit_order with order_type='trailing_stop' requires trail_price or trail_percent.")
        asset, quote = resolve_asset_and_quote(
            strategy,
            symbol=symbol,
            asset_type=asset_type,
            expiration=_coerce_expiration(expiration),
            strike=strike,
            right=right,
            quote_symbol=quote_symbol,
        )
        _require_no_negative_cash_after_buy(
            strategy,
            asset=asset,
            quote=quote,
            quantity=quantity,
            side=side,
            asset_type=asset_type,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            stop_limit_price=stop_limit_price,
            exchange=exchange,
        )
        created = strategy.create_order(
            asset,
            quantity,
            side,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            stop_limit_price=stop_limit_price,
            trail_price=trail_price,
            trail_percent=trail_percent,
            exchange=exchange,
            quote=quote,
            time_in_force=time_in_force,
        )
        submitted = strategy.submit_order(created)
        order_payload = _order_to_dict(submitted)
        memory = getattr(strategy, "memory", None)
        if memory is not None and hasattr(memory, "record_order_submitted"):
            try:
                memory.record_order_submitted(
                    order=submitted,
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    order_type=order_type,
                    asset_type=asset_type,
                    limit_price=limit_price,
                    stop_price=stop_price,
                    stop_limit_price=stop_limit_price,
                    trail_price=trail_price,
                    trail_percent=trail_percent,
                    quote_symbol=quote_symbol,
                    exchange=exchange,
                    time_in_force=time_in_force,
                    order_payload=order_payload,
                    **_agent_memory_context_kwargs(),
                )
            except Exception:
                pass
        return {"order": order_payload}

    return BoundTool(
        name="orders_submit_order",
        description=(
            "Create and submit a LumiBot order. "
            "Arguments: symbol, quantity, side, optional asset_type, expiration, strike, right, order_type, limit_price, stop_price, stop_limit_price, trail_price, trail_percent, quote_symbol, exchange, time_in_force. "
            "Valid asset_type values: stock, option, future, cont_future, forex, crypto, index, multileg, us_equity. "
            "Use stock for normal equities. "
            "Before using this tool, inspect readiness in the current agent run. "
            "Prefer orders_preflight_check when available. "
            "Otherwise call account_portfolio, account_positions, and market_last_price for the same symbol. "
            "If readiness has not been inspected, the order is rejected with ORDER_READINESS_REQUIRED. "
            "Valid side values: buy, sell, buy_to_open, buy_to_close, sell_to_open, sell_to_close, sell_short, buy_to_cover. "
            "Valid order_type values: market, limit, stop, stop_limit, trailing_stop, smart_limit. "
            "Valid time_in_force values: day, gtc, gtd. "
            "For stock/us_equity buy-like orders, this tool estimates affordability and rejects orders that would "
            "make cash negative unless LUMIBOT_AGENT_ALLOW_NEGATIVE_CASH is explicitly enabled. "
            "Caveats: limit orders require limit_price; stop and stop_limit orders require stop_price; trailing_stop requires trail_price or trail_percent; smart_limit uses LumiBot's built-in smart-limit behavior. "
            "Example: orders_submit_order(symbol='SPY', quantity=100, side='buy', asset_type='stock', order_type='market')."
        ),
        function=submit_order,
        metadata={"kind": "builtin", "replay_on_cache": True},
    )


def _bind_submit_multileg_order(strategy: Any, manager: Any) -> BoundTool:
    def submit_multileg(
        *,
        legs_json: str,
        price_style: MultilegPriceStyleArg = "mid",
        net_limit_price: float | None = None,
        time_in_force: TimeInForceArg = "day",
    ) -> dict[str, Any]:
        orders = _parse_option_legs(strategy, legs_json, time_in_force=time_in_force)
        symbols = sorted({str(getattr(order.asset, "symbol", "")).upper() for order in orders})
        for symbol in symbols:
            _require_agent_order_readiness(symbol)

        submit_kwargs: dict[str, Any] = {
            "is_multileg": True,
            "duration": time_in_force,
        }
        resolved_net_price: float | None = None
        if price_style == "market":
            if net_limit_price is not None:
                raise ValueError("net_limit_price cannot be used when price_style='market'.")
            submit_kwargs["order_type"] = "market"
        else:
            if net_limit_price is None:
                calculated = _options_helper_for_strategy(strategy).calculate_multileg_limit_price(orders, price_style)
                if calculated is None:
                    raise ValueError(
                        "Unable to calculate a multi-leg limit price from the current quotes. Evaluate every leg or use price_style='market' only if your trading policy permits it."
                    )
                resolved_net_price = float(calculated)
            else:
                resolved_net_price = float(net_limit_price)
                if not math.isfinite(resolved_net_price):
                    raise ValueError("net_limit_price must be finite.")
            submit_kwargs["order_type"] = (
                "debit" if resolved_net_price > 0 else "credit" if resolved_net_price < 0 else "even"
            )
            submit_kwargs["price"] = abs(resolved_net_price)

        submitted = strategy.submit_order(orders, **submit_kwargs)
        submitted_orders = submitted if isinstance(submitted, list) else [submitted]
        return {
            "submitted": [_order_to_dict(order) for order in submitted_orders if order is not None],
            "legs": [_order_to_dict(order) for order in orders],
            "price_style": price_style,
            "net_limit_price": resolved_net_price,
            "order_type": submit_kwargs["order_type"],
            "time_in_force": time_in_force,
            "datetime": strategy.get_datetime().isoformat(),
        }

    return BoundTool(
        name="orders_submit_multileg",
        description=(
            "Create and submit one atomic multi-leg option order from exact contracts selected by the agent. This is generic and does not choose a strategy or its legs. "
            "Arguments: legs_json, optional price_style='market', 'best', 'mid', or 'fastest', optional signed net_limit_price, optional time_in_force. legs_json must be a JSON array with at least two legs; each leg requires symbol, expiration, strike, right, quantity, and side. "
            "Before submitting, call account_portfolio, account_positions, market_last_price for each underlying symbol, retrieve the chain, and evaluate every exact leg. "
            "Opening sides are buy_to_open and sell_to_open. Closing sides are buy_to_close and sell_to_close. Use matching quantities when the intended position requires matched contracts. "
            "When closing existing positions, map signed account quantities exactly: positive long quantity -> sell_to_close; negative short quantity -> buy_to_close. Reversing that mapping increases exposure instead of closing it. "
            "Every proposed closing leg must reduce the corresponding exact position quantity toward zero. Do not use the same closing side for positive and negative position quantities. "
            "Current nonzero option positions remain open until a later account_positions result shows zero quantity. A submitted or filled order result is not itself proof that positions are flat, and a final response must not claim submission unless this tool returned submitted orders. "
            "Before opening more option exposure, compare the proposed legs with all current option positions and pending orders. Do not add another structure when the strategy policy permits only one open structure. "
            "For limit execution, a positive signed net_limit_price is a debit and a negative value is a credit. If omitted, LumiBot calculates the selected best/mid/fastest price. "
            "The agent must validate that signed price against its exact leg quotes and strategy economics before submission. For equal-width credit spreads, credit must be positive and strictly less than the wing width. "
            "Use price_style='market' only when the strategy policy explicitly accepts market execution. The tool returns the submitted child orders and pricing classification."
        ),
        function=submit_multileg,
        metadata={"kind": "builtin", "replay_on_cache": True},
    )


class _AccountTools:
    def positions(self) -> ToolDefinition:
        return ToolDefinition(
            name="account_positions",
            description="Return current positions with asset fields and quantity.",
            binder=_bind_positions,
        )

    def portfolio(self) -> ToolDefinition:
        return ToolDefinition(
            name="account_portfolio",
            description="Return current cash and portfolio value for sizing decisions.",
            binder=_bind_portfolio,
        )


class _MarketTools:
    def last_price(self) -> ToolDefinition:
        return ToolDefinition(
            name="market_last_price",
            description="Get the current last price for one asset.",
            binder=_bind_last_price,
        )

    def load_history_table(self) -> ToolDefinition:
        return ToolDefinition(
            name="market_load_history_table",
            description="Load visible historical bars into DuckDB.",
            binder=_bind_load_history,
        )

    def load_history_tables_summary(self) -> ToolDefinition:
        return ToolDefinition(
            name="market_load_history_tables_summary",
            description="Load visible historical bars for multiple symbols and summarize the universe.",
            binder=_bind_load_history_tables_summary,
        )


class _OptionsTools:
    def get_chain(self) -> ToolDefinition:
        return ToolDefinition(name="options_get_chain", description="Retrieve an underlying's available option chain.", binder=_bind_options_get_chain)

    def get_strikes(self) -> ToolDefinition:
        return ToolDefinition(name="options_get_strikes", description="List strikes for one expiration and option right.", binder=_bind_options_get_strikes)

    def get_greeks(self) -> ToolDefinition:
        return ToolDefinition(name="options_get_greeks", description="Get Greeks for one exact option contract.", binder=_bind_options_get_greeks)

    def find_strike_for_delta(self) -> ToolDefinition:
        return ToolDefinition(name="options_find_strike_for_delta", description="Find a listed option strike closest to a target delta.", binder=_bind_options_find_strike_for_delta)

    def evaluate_market(self) -> ToolDefinition:
        return ToolDefinition(name="options_evaluate_market", description="Evaluate quote quality for one exact option contract.", binder=_bind_options_evaluate_market)

    def calculate_multileg_price(self) -> ToolDefinition:
        return ToolDefinition(name="options_calculate_multileg_price", description="Calculate a signed net price for exact option legs.", binder=_bind_options_calculate_multileg_price)


class _DuckDBTools:
    def query(self) -> ToolDefinition:
        return ToolDefinition(
            name="duckdb_query",
            description="Run a read-only SQL query against loaded DuckDB tables.",
            binder=_bind_duckdb_query,
        )


class _DocsTools:
    def search(self) -> ToolDefinition:
        return ToolDefinition(
            name="lumibot_docs_search",
            description="Search LumiBot's local documentation before guessing about tool or backtesting behavior.",
            binder=_bind_docs_search,
        )


class _NewsTools:
    def alpaca_news(self) -> ToolDefinition:
        return ToolDefinition(
            name="alpaca_news",
            description=ALPACA_NEWS_DESCRIPTION,
            binder=_bind_alpaca_news,
        )


class _IndicatorTools:
    def list_indicators(self) -> ToolDefinition:
        return ToolDefinition(name="list_indicators", description="List common technical indicators.", binder=_bind_list_indicators)

    def get_indicator(self) -> ToolDefinition:
        return ToolDefinition(name="get_indicator", description="Get one current-bar technical indicator.", binder=_bind_get_indicator)

    def get_indicators(self) -> ToolDefinition:
        return ToolDefinition(name="get_indicators", description="Get multiple current-bar technical indicators.", binder=_bind_get_indicators)


class _FundamentalTools:
    def income_statement(self) -> ToolDefinition:
        return ToolDefinition(name="get_income_statement", description="Get SEC income statement facts.", binder=_bind_get_income_statement)

    def balance_sheet(self) -> ToolDefinition:
        return ToolDefinition(name="get_balance_sheet", description="Get SEC balance sheet facts.", binder=_bind_get_balance_sheet)

    def cash_flow(self) -> ToolDefinition:
        return ToolDefinition(name="get_cash_flow", description="Get SEC cash flow facts.", binder=_bind_get_cash_flow)

    def company_facts(self) -> ToolDefinition:
        return ToolDefinition(name="get_company_facts", description="Get SEC companyfacts.", binder=_bind_get_company_facts)

    def filings(self) -> ToolDefinition:
        return ToolDefinition(name="get_filings", description="List SEC filings.", binder=_bind_get_filings)

    def search_filing(self) -> ToolDefinition:
        return ToolDefinition(name="search_filing", description="Search a SEC filing.", binder=_bind_search_filing)

    def filing_document(self) -> ToolDefinition:
        return ToolDefinition(name="get_filing_document", description="Read a SEC filing document.", binder=_bind_get_filing_document)

    def list_filing_sections(self) -> ToolDefinition:
        return ToolDefinition(name="list_filing_sections", description="List SEC filing sections.", binder=_bind_list_filing_sections)

    def filing_section(self) -> ToolDefinition:
        return ToolDefinition(name="get_filing_section", description="Read one SEC filing section.", binder=_bind_get_filing_section)


class _MacroTools:
    def list_fred_series(self) -> ToolDefinition:
        return ToolDefinition(name="list_fred_series", description="List curated FRED macro series.", binder=_bind_list_fred_series)

    def get_fred_series(self) -> ToolDefinition:
        return ToolDefinition(name="get_fred_series", description="Get a FRED macro time series.", binder=_bind_get_fred_series)

    def get_fred_latest(self) -> ToolDefinition:
        return ToolDefinition(name="get_fred_latest", description="Get the latest FRED macro observation.", binder=_bind_get_fred_latest)

    def get_fred_snapshot(self) -> ToolDefinition:
        return ToolDefinition(name="get_fred_snapshot", description="Get a multi-series FRED macro snapshot.", binder=_bind_get_fred_snapshot)


class _NotificationTools:
    def notify_user(self) -> ToolDefinition:
        return ToolDefinition(name="notify_user", description="Send a user notification.", binder=_bind_notify_user)


class _MemoryTools:
    def remember(self) -> ToolDefinition:
        return ToolDefinition(name="remember", description="Store a local memory.", binder=_bind_memory_remember)

    def search(self) -> ToolDefinition:
        return ToolDefinition(name="search_memory", description="Search local memories.", binder=_bind_memory_search)

    def remember_decision(self) -> ToolDefinition:
        return ToolDefinition(
            name="remember_decision",
            description="Record an actual trading decision.",
            binder=_bind_remember_decision,
            metadata={"mutates_trading": True},
        )

    def remember_proposal(self) -> ToolDefinition:
        return ToolDefinition(name="remember_proposal", description="Record a non-final trade proposal.", binder=_bind_remember_proposal)

    def remember_risk_note(self) -> ToolDefinition:
        return ToolDefinition(name="remember_risk_note", description="Record a compact risk note.", binder=_bind_remember_risk_note)

    def remember_lesson(self) -> ToolDefinition:
        return ToolDefinition(name="remember_lesson", description="Record a compact lesson.", binder=_bind_remember_lesson)

    def open_thesis(self) -> ToolDefinition:
        return ToolDefinition(name="open_thesis", description="Open an investment thesis.", binder=_bind_open_thesis)

    def update_thesis(self) -> ToolDefinition:
        return ToolDefinition(name="update_thesis", description="Update an investment thesis.", binder=_bind_update_thesis)

    def close_thesis(self) -> ToolDefinition:
        return ToolDefinition(name="close_thesis", description="Close an investment thesis.", binder=_bind_close_thesis)


class _OrderTools:
    def preflight(self) -> ToolDefinition:
        return ToolDefinition(
            name="orders_preflight_check",
            description=ORDERS_PREFLIGHT_CHECK_DESCRIPTION,
            binder=_bind_preflight_check,
        )

    def submit_and_confirm(self) -> ToolDefinition:
        return ToolDefinition(
            name="orders_submit_and_confirm_order",
            description=ORDERS_SUBMIT_AND_CONFIRM_ORDER_DESCRIPTION,
            binder=_bind_submit_and_confirm_order,
            metadata={"mutates_trading": True},
        )

    def execute(self) -> ToolDefinition:
        return ToolDefinition(
            name="orders_execute_order",
            description=ORDERS_EXECUTE_ORDER_DESCRIPTION,
            binder=_bind_execute_order,
            metadata={"mutates_trading": True},
        )

    def execute_plan(self) -> ToolDefinition:
        return ToolDefinition(
            name="execution_plan_execute",
            description=EXECUTION_PLAN_EXECUTE_DESCRIPTION,
            binder=_bind_execute_plan,
            metadata={"mutates_trading": True},
        )

    def submit(self) -> ToolDefinition:
        return ToolDefinition(
            name="orders_submit_order",
            description="Submit an order with explicit side/type/time_in_force.",
            binder=_bind_submit_order,
            metadata={"mutates_trading": True},
        )

    def confirm(self) -> ToolDefinition:
        return ToolDefinition(
            name="orders_confirm_order",
            description="Confirm a submitted order by identifier before continuing execution.",
            binder=_bind_confirm_order,
            metadata={"mutates_trading": True},
        )

    def cancel(self) -> ToolDefinition:
        return ToolDefinition(
            name="orders_cancel_order",
            description="Cancel a tracked order by identifier.",
            binder=_bind_cancel_order,
            metadata={"mutates_trading": True},
        )

    def submit_multileg(self) -> ToolDefinition:
        return ToolDefinition(
            name="orders_submit_multileg",
            description="Submit one atomic multi-leg option order from exact agent-selected contracts.",
            binder=_bind_submit_multileg_order,
            metadata={"mutates_trading": True},
        )

    def open_orders(self) -> ToolDefinition:
        return ToolDefinition(name="orders_open_orders", description="List tracked orders and their identifiers.", binder=_bind_open_orders)

    def modify(self) -> ToolDefinition:
        return ToolDefinition(
            name="orders_modify_order",
            description="Modify a tracked order by identifier.",
            binder=_bind_modify_order,
            metadata={"mutates_trading": True},
        )


class _BuiltinTools:
    account = _AccountTools()
    market = _MarketTools()
    options = _OptionsTools()
    duckdb = _DuckDBTools()
    docs = _DocsTools()
    news = _NewsTools()
    indicators = _IndicatorTools()
    fundamentals = _FundamentalTools()
    macro = _MacroTools()
    notifications = _NotificationTools()
    memory = _MemoryTools()
    orders = _OrderTools()

    def all(self) -> list[ToolDefinition]:
        """Return all built-in tools. Used as the default when tools=None in agent creation."""
        return [
            self.account.positions(),
            self.account.portfolio(),
            self.market.last_price(),
            self.market.load_history_table(),
            self.market.load_history_tables_summary(),
            self.options.get_chain(),
            self.options.get_strikes(),
            self.options.get_greeks(),
            self.options.find_strike_for_delta(),
            self.options.evaluate_market(),
            self.options.calculate_multileg_price(),
            self.duckdb.query(),
            self.docs.search(),
            self.news.alpaca_news(),
            self.indicators.list_indicators(),
            self.indicators.get_indicator(),
            self.indicators.get_indicators(),
            self.fundamentals.income_statement(),
            self.fundamentals.balance_sheet(),
            self.fundamentals.cash_flow(),
            self.fundamentals.company_facts(),
            self.fundamentals.filings(),
            self.fundamentals.search_filing(),
            self.fundamentals.filing_document(),
            self.fundamentals.list_filing_sections(),
            self.fundamentals.filing_section(),
            self.macro.list_fred_series(),
            self.macro.get_fred_series(),
            self.macro.get_fred_latest(),
            self.macro.get_fred_snapshot(),
            self.notifications.notify_user(),
            self.memory.remember(),
            self.memory.search(),
            self.memory.remember_proposal(),
            self.memory.remember_risk_note(),
            self.memory.remember_decision(),
            self.memory.remember_lesson(),
            self.memory.open_thesis(),
            self.memory.update_thesis(),
            self.memory.close_thesis(),
            self.orders.preflight(),
            self.orders.submit_and_confirm(),
            self.orders.execute(),
            self.orders.execute_plan(),
            self.orders.submit(),
            self.orders.confirm(),
            self.orders.submit_multileg(),
            self.orders.cancel(),
            self.orders.open_orders(),
            self.orders.modify(),
        ]


BuiltinTools = _BuiltinTools()
