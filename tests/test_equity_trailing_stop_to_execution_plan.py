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
        if isinstance(price, list):
            return SimpleNamespace(pandas_df=pd.DataFrame(price[-length:]))
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
        "NVDA": {"entry_date": "2024-09-02", "entry_price": 120.0, "peak_close": 150.0},
        "AAPL": {"entry_date": "2024-09-02", "entry_price": 190.0, "peak_close": 210.0},
    }

    result = module.trailing_stop_to_execution_plan(
        strategy,
        date="2024-09-05",
        initial_stop_pct=0.12,
        trailing_stop_pct=0.20,
        position_state=state,
    )

    assert result["policy"] == {
        "initial_stop_pct": 0.12,
        "trailing_stop_pct": 0.2,
        "price_basis": "daily_close",
    }
    assert result["execution_plan"] == {"schema_version": 1, "intent": "hold", "orders": []}
    assert result["exit_checks"] is result["stop_checks"]
    assert result["exit_checks"][0]["symbol"] == "AAPL"
    assert result["exit_checks"][0]["entry_price"] == 190.0
    assert result["exit_checks"][0]["initial_stop_price"] == pytest.approx(167.2)
    assert result["exit_checks"][0]["trailing_stop_price"] == pytest.approx(168.0)
    assert result["exit_checks"][0]["trigger_reason"] is None
    assert result["exit_checks"][1]["symbol"] == "NVDA"
    assert all(check["triggered"] is False for check in result["exit_checks"])
    assert result["updated_position_state"]["AAPL"]["entry_price"] == 190.0
    assert result["updated_position_state"]["AAPL"]["last_check_date"] == "2024-09-05"
    assert result["updated_position_state"]["AAPL"]["last_check_price"] == 200.0


def test_initial_stop_generates_full_position_sell_when_entry_loss_breached():
    module = load_module()
    strategy = make_strategy(positions=[make_position("NVDA", 10)], prices={"NVDA": 87.0})
    state = {"NVDA": {"entry_date": "2024-09-02", "entry_price": 100.0, "peak_close": 105.0}}

    result = module.trailing_stop_to_execution_plan(
        strategy,
        date="2024-09-05",
        initial_stop_pct=0.12,
        trailing_stop_pct=0.20,
        position_state=state,
    )

    assert result["execution_plan"] == {
        "schema_version": 1,
        "intent": "risk_exit",
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
    check = result["exit_checks"][0]
    assert check["initial_stop_price"] == pytest.approx(88.0)
    assert check["trailing_stop_price"] == pytest.approx(84.0)
    assert check["initial_stop_triggered"] is True
    assert check["trailing_stop_triggered"] is False
    assert check["trigger_reason"] == "initial_stop"
    assert result["updated_position_state"] == {}


def test_trailing_stop_generates_full_position_sell_when_threshold_breached():
    module = load_module()
    strategy = make_strategy(positions=[make_position("NVDA", 10)], prices={"NVDA": 118.0})
    state = {"NVDA": {"entry_date": "2024-09-02", "entry_price": 100.0, "peak_close": 150.0}}

    result = module.trailing_stop_to_execution_plan(
        strategy,
        date="2024-09-05",
        initial_stop_pct=0.12,
        trailing_stop_pct=0.20,
        position_state=state,
    )

    assert result["execution_plan"]["intent"] == "risk_exit"
    assert result["execution_plan"]["orders"][0]["symbol"] == "NVDA"
    assert result["execution_plan"]["orders"][0]["side"] == "sell"
    assert result["execution_plan"]["orders"][0]["quantity"] == 10
    check = result["exit_checks"][0]
    assert check["entry_price"] == 100.0
    assert check["initial_stop_price"] == pytest.approx(88.0)
    assert check["trailing_stop_price"] == pytest.approx(120.0)
    assert check["initial_stop_triggered"] is False
    assert check["trailing_stop_triggered"] is True
    assert check["trigger_reason"] == "trailing_stop"
    assert result["updated_position_state"] == {}


def test_exit_engine_can_sell_multiple_triggered_positions():
    module = load_module()
    strategy = make_strategy(
        positions=[make_position("NVDA", 10), make_position("AAPL", 5), make_position("MSFT", 3)],
        prices={"NVDA": 118.0, "AAPL": 170.0, "MSFT": 260.0},
    )
    state = {
        "NVDA": {"entry_date": "2024-09-02", "entry_price": 100.0, "peak_close": 150.0},
        "AAPL": {"entry_date": "2024-09-02", "entry_price": 200.0, "peak_close": 210.0},
        "MSFT": {"entry_date": "2024-09-02", "entry_price": 250.0, "peak_close": 270.0},
    }

    result = module.trailing_stop_to_execution_plan(
        strategy,
        date="2024-09-05",
        initial_stop_pct=0.12,
        trailing_stop_pct=0.20,
        position_state=state,
    )

    assert result["execution_plan"]["intent"] == "risk_exit"
    assert sorted(order["symbol"] for order in result["execution_plan"]["orders"]) == ["AAPL", "NVDA"]
    assert result["updated_position_state"] == {
        "MSFT": {
            "entry_date": "2024-09-02",
            "entry_price": 250.0,
            "peak_close": 270.0,
            "last_check_date": "2024-09-05",
            "last_check_price": 260.0,
        }
    }


def test_trailing_stop_uses_highest_close_from_holding_window():
    module = load_module()
    strategy = make_strategy(
        positions=[make_position("NVDA", 10)],
        prices={"NVDA": 118.0},
        historical_prices={
            "NVDA": [
                {"Date": "2024-08-30", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
                {"Date": "2024-09-02", "open": 130.0, "high": 130.0, "low": 130.0, "close": 130.0},
                {"Date": "2024-09-03", "open": 150.0, "high": 150.0, "low": 150.0, "close": 150.0},
                {"Date": "2024-09-04", "open": 140.0, "high": 140.0, "low": 140.0, "close": 140.0},
                {"Date": "2024-09-05", "open": 118.0, "high": 118.0, "low": 118.0, "close": 118.0},
            ],
        },
    )
    state = {"NVDA": {"entry_date": "2024-09-02", "entry_price": 100.0, "peak_close": 130.0}}

    result = module.trailing_stop_to_execution_plan(
        strategy,
        date="2024-09-05",
        initial_stop_pct=0.12,
        trailing_stop_pct=0.20,
        position_state=state,
    )

    check = result["exit_checks"][0]
    assert check["previous_peak_close"] == 130.0
    assert check["peak_close"] == 150.0
    assert check["current_check_price"] == 118.0
    assert check["initial_stop_price"] == pytest.approx(88.0)
    assert check["trailing_stop_price"] == pytest.approx(120.0)
    assert check["initial_stop_triggered"] is False
    assert check["trailing_stop_triggered"] is True
    assert check["trigger_reason"] == "trailing_stop"
    assert check["history_start_date"] == "2024-09-02"
    assert check["history_end_date"] == "2024-09-05"
    assert check["history_bar_count"] == 4


def test_trailing_stop_updates_peak_when_current_close_sets_new_high():
    module = load_module()
    strategy = make_strategy(positions=[make_position("NVDA", 10)], prices={"NVDA": 155.0})
    state = {"NVDA": {"entry_date": "2024-09-02", "entry_price": 100.0, "peak_close": 150.0}}

    result = module.trailing_stop_to_execution_plan(
        strategy,
        date="2024-09-05",
        initial_stop_pct=0.12,
        trailing_stop_pct=0.20,
        position_state=state,
    )

    check = result["exit_checks"][0]
    assert check["entry_price"] == 100.0
    assert check["peak_close"] == 155.0
    assert check["initial_stop_triggered"] is False
    assert check["trailing_stop_triggered"] is False
    assert check["trigger_reason"] is None
    assert result["updated_position_state"]["NVDA"] == {
        "entry_date": "2024-09-02",
        "entry_price": 100.0,
        "peak_close": 155.0,
        "last_check_date": "2024-09-05",
        "last_check_price": 155.0,
    }
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
        initial_stop_pct=0.12,
        trailing_stop_pct=0.20,
        position_state={},
    )

    assert result["execution_plan"] == {"schema_version": 1, "intent": "hold", "orders": []}
    assert result["exit_checks"][0]["price_source"] == "last_price_fallback"
    assert result["exit_checks"][0]["entry_date"] == "2024-09-05"
    assert result["exit_checks"][0]["entry_price"] == 149.0
    assert result["exit_checks"][0]["initial_stop_price"] == pytest.approx(131.12)
    assert result["updated_position_state"]["NVDA"] == {
        "entry_date": "2024-09-05",
        "entry_price": 149.0,
        "peak_close": 149.0,
        "last_check_date": "2024-09-05",
        "last_check_price": 149.0,
    }


@pytest.mark.parametrize(
    ("initial_stop_pct", "trailing_stop_pct", "message"),
    [
        (0, 0.20, "initial_stop_pct must be between 0 and 1"),
        (1, 0.20, "initial_stop_pct must be between 0 and 1"),
        (0.12, 0, "trailing_stop_pct must be between 0 and 1"),
        (0.12, 1, "trailing_stop_pct must be between 0 and 1"),
    ],
)
def test_exit_engine_rejects_invalid_stop_percentages(initial_stop_pct, trailing_stop_pct, message):
    module = load_module()
    strategy = make_strategy(positions=[], prices={})

    with pytest.raises(ValueError, match=message):
        module.trailing_stop_to_execution_plan(
            strategy,
            date="2024-09-05",
            initial_stop_pct=initial_stop_pct,
            trailing_stop_pct=trailing_stop_pct,
            position_state={},
        )
