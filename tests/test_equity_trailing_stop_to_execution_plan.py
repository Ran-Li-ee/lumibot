import importlib
from types import SimpleNamespace

import pandas as pd
import pytest


def load_module():
    return importlib.import_module("lumibot.example_strategies.equity_trailing_stop_to_execution_plan")


def make_position(symbol, quantity):
    return SimpleNamespace(asset=SimpleNamespace(symbol=symbol), quantity=quantity)


def make_strategy(*, positions, prices, historical_prices=None):
    strategy = SimpleNamespace()
    strategy.get_positions = lambda include_cash_positions=False: positions
    strategy.get_last_price = lambda symbol, **kwargs: prices[str(getattr(symbol, "symbol", symbol)).upper()]

    def get_historical_prices(symbol, length, timestep="day", **kwargs):
        symbol_text = str(getattr(symbol, "symbol", symbol)).upper()
        price = prices[symbol_text] if historical_prices is None else historical_prices.get(symbol_text)
        if price is None:
            return SimpleNamespace(pandas_df=pd.DataFrame())
        return SimpleNamespace(
            pandas_df=pd.DataFrame(
                [
                    {
                        "Date": "2024-09-05",
                        "open": price,
                        "high": price,
                        "low": price,
                        "close": price,
                        "volume": 1000,
                    }
                ]
            )
        )

    strategy.get_historical_prices = get_historical_prices
    return strategy


def test_trailing_stop_returns_hold_when_no_position_breaches_threshold():
    module = load_module()
    strategy = make_strategy(
        positions=[make_position("NVDA", 10), make_position("AAPL", 5)],
        prices={"NVDA": 125.0, "AAPL": 200.0},
    )
    state = {
        "NVDA": {"entry_date": "2024-09-02", "peak_close": 150.0},
        "AAPL": {"entry_date": "2024-09-02", "peak_close": 210.0},
    }

    result = module.trailing_stop_to_execution_plan(
        strategy,
        date="2024-09-05",
        trailing_stop_pct=0.20,
        position_state=state,
    )

    assert result["execution_plan"] == {"schema_version": 1, "intent": "hold", "orders": []}
    assert result["stop_checks"][0]["symbol"] == "AAPL"
    assert result["stop_checks"][1]["symbol"] == "NVDA"
    assert all(check["triggered"] is False for check in result["stop_checks"])
    assert result["updated_position_state"]["AAPL"]["last_check_date"] == "2024-09-05"
    assert result["updated_position_state"]["AAPL"]["last_check_price"] == 200.0


def test_trailing_stop_generates_full_position_sell_when_threshold_breached():
    module = load_module()
    strategy = make_strategy(positions=[make_position("NVDA", 10)], prices={"NVDA": 118.0})
    state = {"NVDA": {"entry_date": "2024-09-02", "peak_close": 150.0}}

    result = module.trailing_stop_to_execution_plan(
        strategy,
        date="2024-09-05",
        trailing_stop_pct=0.20,
        position_state=state,
    )

    assert result["execution_plan"] == {
        "schema_version": 1,
        "intent": "rebalance",
        "orders": [
            {
                "sequence": 1,
                "action": "submit_order",
                "symbol": "NVDA",
                "side": "sell",
                "quantity_mode": "shares",
                "quantity": 10,
                "asset_type": "stock",
                "order_type": "market",
                "time_in_force": "day",
            }
        ],
    }
    assert result["stop_checks"] == [
        {
            "symbol": "NVDA",
            "quantity": 10.0,
            "holding_start_date": "2024-09-02",
            "previous_peak_close": 150.0,
            "peak_close": 150.0,
            "current_check_price": 118.0,
            "trailing_stop_pct": 0.2,
            "stop_price": 120.0,
            "triggered": True,
            "price_source": "daily_close",
        }
    ]


def test_trailing_stop_updates_peak_when_current_close_sets_new_high():
    module = load_module()
    strategy = make_strategy(positions=[make_position("NVDA", 10)], prices={"NVDA": 155.0})
    state = {"NVDA": {"entry_date": "2024-09-02", "peak_close": 150.0}}

    result = module.trailing_stop_to_execution_plan(
        strategy,
        date="2024-09-05",
        trailing_stop_pct=0.20,
        position_state=state,
    )

    assert result["updated_position_state"]["NVDA"]["entry_date"] == "2024-09-02"
    assert result["updated_position_state"]["NVDA"]["peak_close"] == 155.0
    assert result["updated_position_state"]["NVDA"]["last_check_date"] == "2024-09-05"
    assert result["updated_position_state"]["NVDA"]["last_check_price"] == 155.0
    assert result["execution_plan"]["intent"] == "hold"


def test_trailing_stop_falls_back_to_last_price_when_daily_close_is_unavailable():
    module = load_module()
    strategy = make_strategy(
        positions=[make_position("NVDA", 10)],
        prices={"NVDA": 149.0},
        historical_prices={"NVDA": None},
    )

    result = module.trailing_stop_to_execution_plan(
        strategy,
        date="2024-09-05",
        trailing_stop_pct=0.20,
        position_state={},
    )

    assert result["execution_plan"] == {"schema_version": 1, "intent": "hold", "orders": []}
    assert result["stop_checks"][0]["price_source"] == "last_price_fallback"
    assert result["updated_position_state"]["NVDA"] == {
        "entry_date": "2024-09-05",
        "peak_close": 149.0,
        "last_check_date": "2024-09-05",
        "last_check_price": 149.0,
    }


def test_trailing_stop_rejects_negative_or_zero_trailing_stop_pct():
    module = load_module()
    strategy = make_strategy(positions=[], prices={})

    with pytest.raises(ValueError, match="trailing_stop_pct must be between 0 and 1"):
        module.trailing_stop_to_execution_plan(
            strategy,
            date="2024-09-05",
            trailing_stop_pct=0,
            position_state={},
        )
