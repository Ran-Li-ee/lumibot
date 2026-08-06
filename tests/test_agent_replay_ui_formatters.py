from copy import deepcopy

from lumibot.components.agents.replay_ui.formatters import explain_tool_result


def test_market_last_price_formatter_explains_symbol_and_price():
    text = explain_tool_result(
        "market_last_price",
        {"symbol": "QQQ"},
        {"symbol": "QQQ", "price": 110.71, "currency": "USD"},
        None,
    )

    assert "QQQ" in text
    assert "110.71" in text
    assert "latest visible price" in text


def test_duckdb_query_formatter_summarizes_rows():
    text = explain_tool_result(
        "duckdb_query",
        {"sql": "SELECT AVG(close) AS avg_close FROM qqq_history"},
        {"rows": [{"avg_close": 101.2}], "row_count": 1},
        None,
    )

    assert "SQL" in text
    assert "1 row" in text
    assert "avg_close" in text


def test_market_load_history_table_formatter_includes_computed_summary():
    text = explain_tool_result(
        "market_load_history_table",
        {"symbol": "QQQ"},
        {
            "symbol": "QQQ",
            "table_name": "qqq_hist",
            "row_count": 252,
            "computed_summary": {
                "price": {"latest_close": 458.67},
                "momentum": {"return_20": 0.034, "return_60": -0.021, "return_120": None},
                "trend": {"sma_20": 450.12, "sma_50": 440.5, "price_vs_sma_20": 0.019},
                "range": {"distance_to_high_252": -0.044, "distance_to_low_252": 0.21},
                "risk": {"max_drawdown_60": -0.083, "volatility_20": 0.011},
            },
        },
        None,
    )

    assert "252 rows" in text
    assert "latest close 458.67" in text
    assert "20-bar return 3.40%" in text
    assert "60-bar return -2.10%" in text
    assert "SMA20 450.12" in text
    assert "vs SMA20 1.90%" in text
    assert "from 252-bar high -4.40%" in text
    assert "max drawdown 60 -8.30%" in text


def test_tool_error_formatter_is_visible():
    text = explain_tool_result(
        "market_last_price",
        {"symbol": "QQQ"},
        {"tool_error": True, "error": "No data"},
        "No data",
    )

    assert "failed" in text.lower()
    assert "No data" in text


def test_tool_error_formatter_extracts_nested_error_message():
    text = explain_tool_result(
        "market_last_price",
        {"symbol": "QQQ"},
        {"tool_error": True, "error": {"type": "ValueError", "message": "bad"}},
        None,
    )

    assert text == "market_last_price failed: bad"


def test_account_positions_formatter_extracts_nested_asset_symbols():
    text = explain_tool_result(
        "account_positions",
        {},
        {
            "positions": [
                {"asset": {"symbol": "SPY", "asset_type": "stock"}, "quantity": 2},
                {"asset": {"symbol": "QQQ", "asset_type": "stock"}, "quantity": 1},
            ]
        },
        None,
    )

    assert "2 positions" in text
    assert "SPY" in text
    assert "QQQ" in text
    assert "{'symbol'" not in text


def test_orders_open_orders_formatter_extracts_nested_asset_symbols():
    text = explain_tool_result(
        "orders_open_orders",
        {},
        {
            "orders": [
                {
                    "asset": {"symbol": "SPY", "asset_type": "stock"},
                    "quantity": 2,
                    "side": "buy",
                }
            ]
        },
        None,
    )

    assert "1 open order" in text
    assert "SPY" in text
    assert "{'symbol'" not in text


def test_orders_submit_order_formatter_extracts_nested_order_payload():
    text = explain_tool_result(
        "orders_submit_order",
        {},
        {
            "order": {
                "identifier": "order-123",
                "status": "submitted",
                "side": "buy",
                "quantity": 2,
                "asset": {"symbol": "SPY", "asset_type": "stock"},
            }
        },
        None,
    )

    assert "buy 2 SPY" in text
    assert "submitted" in text
    assert "order-123" in text
    assert "{'symbol'" not in text


def test_explain_tool_result_does_not_mutate_inputs():
    arguments = {"symbol": "SPY", "nested": {"limit": 1}}
    raw_result = {
        "order": {
            "identifier": "order-123",
            "status": "submitted",
            "side": "buy",
            "quantity": 2,
            "asset": {"symbol": "SPY", "asset_type": "stock"},
        }
    }
    original_arguments = deepcopy(arguments)
    original_raw_result = deepcopy(raw_result)

    explain_tool_result("orders_submit_order", arguments, raw_result, None)

    assert arguments == original_arguments
    assert raw_result == original_raw_result


def test_unknown_tool_uses_generic_formatter():
    text = explain_tool_result("custom_tool", {"x": 1}, {"value": 2}, None)

    assert text == "This tool returned structured data. Open the raw result to inspect details."
