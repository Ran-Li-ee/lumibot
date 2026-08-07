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
                "volume": {
                    "latest_volume": 1000000,
                    "avg_volume_20": 900000,
                    "volume_vs_avg_20": 0.1111,
                },
                "momentum": {
                    "return_5": 0.011,
                    "return_10": 0.022,
                    "return_20": 0.034,
                    "return_21": 0.032,
                    "return_60": -0.021,
                    "return_63": -0.019,
                    "return_120": None,
                    "return_126": 0.052,
                    "return_252": 0.103,
                },
                "trend": {"sma_20": 450.12, "sma_50": 440.5, "price_vs_sma_20": 0.019},
                "range": {
                    "distance_to_high_252": -0.044,
                    "distance_to_low_252": 0.21,
                    "drawdown_from_high_20": -0.012,
                    "drawdown_from_high_60": -0.033,
                    "drawdown_from_high_252": -0.044,
                },
                "risk": {"max_drawdown_60": -0.083, "volatility_20": 0.011},
                "scores": {
                    "momentum_composite": 0.0217,
                    "trend_alignment": 2,
                    "return_63_over_volatility_20": 1.91,
                    "return_126_over_volatility_20": 4.73,
                    "composite_score": 0.42,
                },
            },
        },
        None,
    )

    assert "252 rows" in text
    assert "latest close 458.67" in text
    assert "5-bar return 1.10%" in text
    assert "10-bar return 2.20%" in text
    assert "20-bar return 3.40%" in text
    assert "21-bar return 3.20%" in text
    assert "60-bar return -2.10%" in text
    assert "63-bar return -1.90%" in text
    assert "126-bar return 5.20%" in text
    assert "252-bar return 10.30%" in text
    assert "momentum composite 2.17%" in text
    assert "trend alignment 2" in text
    assert "composite score 0.42" in text
    assert "latest volume 1000000.00" in text
    assert "avg volume 20 900000.00" in text
    assert "volume vs avg20 11.11%" in text
    assert "SMA20 450.12" in text
    assert "vs SMA20 1.90%" in text
    assert "from 252-bar high -4.40%" in text
    assert "drawdown from 20-bar high -1.20%" in text
    assert "drawdown from 60-bar high -3.30%" in text
    assert "drawdown from 252-bar high -4.40%" in text
    assert "max drawdown 60 -8.30%" in text
    assert "return63/vol20 1.91" in text
    assert "return126/vol20 4.73" in text


def test_market_load_history_tables_summary_formatter_shows_rankings_and_warnings():
    text = explain_tool_result(
        "market_load_history_tables_summary",
        {"symbols": ["SPY", "QQQ", "VNQ"]},
        {
            "universe_summary": [
                {"symbol": "SPY"},
                {"symbol": "QQQ"},
                {"symbol": "VNQ"},
            ],
            "rankings": {
                "by_return_63": ["VNQ", "SPY", "QQQ"],
                "by_momentum_composite": ["VNQ", "QQQ", "SPY"],
                "by_composite_score": ["SPY", "VNQ", "QQQ"],
            },
            "warnings": ["QQQ had a partial history window"],
        },
        None,
    )

    assert "3 symbols" in text
    assert "return_63: VNQ, SPY, QQQ" in text
    assert "momentum_composite: VNQ, QQQ, SPY" in text
    assert "composite_score: SPY, VNQ, QQQ" in text
    assert "1 warning" in text


def test_market_load_history_tables_summary_formatter_counts_empty_summary_rows():
    text = explain_tool_result(
        "market_load_history_tables_summary",
        {"symbols": ["SPY", "QQQ"]},
        {
            "universe_summary": [],
            "rankings": {},
            "warnings": ["No history rows were loaded"],
        },
        None,
    )

    assert "2 symbols" not in text
    assert "0 symbols" in text
    assert "1 warning" in text


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


def test_orders_confirm_order_formatter_explains_confirmed_fill():
    text = explain_tool_result(
        "orders_confirm_order",
        {"identifier": "order-123", "symbol": "VNQ", "side": "sell", "expected_quantity": 10},
        {
            "identifier": "order-123",
            "confirmed": True,
            "can_continue": True,
            "confirmation_status": "filled",
            "attempt_count": 2,
            "order": {
                "identifier": "order-123",
                "status": "fill",
                "side": "sell",
                "quantity": 10,
                "asset": {"symbol": "VNQ", "asset_type": "stock"},
            },
            "warnings": [],
        },
        None,
    )

    assert "Confirmed order order-123" in text
    assert "sell 10 VNQ" in text
    assert "2 attempts" in text
    assert "can_continue=true" in text


def test_orders_confirm_order_formatter_singularizes_one_attempt():
    text = explain_tool_result(
        "orders_confirm_order",
        {"identifier": "order-123", "symbol": "VNQ", "side": "sell", "expected_quantity": 10},
        {
            "identifier": "order-123",
            "confirmed": True,
            "can_continue": True,
            "confirmation_status": "filled",
            "attempt_count": 1,
            "order": {
                "identifier": "order-123",
                "status": "fill",
                "side": "sell",
                "quantity": 10,
                "asset": {"symbol": "VNQ", "asset_type": "stock"},
            },
            "warnings": [],
        },
        None,
    )

    assert "after 1 attempt" in text
    assert "after 1 attempts" not in text


def test_orders_confirm_order_formatter_explains_blocked_confirmation():
    text = explain_tool_result(
        "orders_confirm_order",
        {"identifier": "order-123", "symbol": "VNQ", "side": "sell", "expected_quantity": 10},
        {
            "identifier": "order-123",
            "confirmed": False,
            "can_continue": False,
            "confirmation_status": "open_after_retries",
            "attempt_count": 3,
            "order": {
                "identifier": "order-123",
                "status": "new",
                "side": "sell",
                "quantity": 10,
                "asset": {"symbol": "VNQ", "asset_type": "stock"},
            },
            "warnings": ["Order remained active after 3 confirmation attempts."],
        },
        None,
    )

    assert "Confirmation blocked" in text
    assert "open_after_retries" in text
    assert "can_continue=false" in text
    assert "Order remained active" in text


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
