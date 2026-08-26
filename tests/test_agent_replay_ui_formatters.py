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


def test_market_load_history_tables_summary_formatter_explains_limited_summary_rows():
    text = explain_tool_result(
        "market_load_history_tables_summary",
        {"symbols": [f"S{i:02d}" for i in range(1, 29)]},
        {
            "ranking_limit": 10,
            "universe_summary_limit": 15,
            "universe_summary": [{"symbol": f"S{i:02d}"} for i in range(1, 16)],
            "rankings": {
                "by_return_63": [f"S{i:02d}" for i in range(1, 11)],
                "by_momentum_composite": [f"S{i:02d}" for i in range(11, 21)],
                "by_composite_score": [f"S{i:02d}" for i in range(1, 11)],
            },
            "universe_summary_selection": {
                "candidate_count_before_limit": 20,
            },
        },
        None,
    )

    assert "15 detailed symbols" in text
    assert "28 requested symbols" in text
    assert "rankings capped at top 10" in text
    assert "20 ranked candidates before the 15-symbol detail limit" in text


def test_market_load_history_tables_summary_formatter_shows_rank_groups_and_candidate_summary():
    explanation = explain_tool_result(
        "market_load_history_tables_summary",
        {"symbols": ["AAA", "BBB", "CCC"], "top_n": 2, "candidate_summary_limit": 2},
        {
            "coverage": {
                "requested_count": 3,
                "loaded_count": 3,
                "failed_count": 0,
                "top_n": 2,
                "candidate_summary_limit": 2,
                "ranking_count": 3,
            },
            "rank_groups": {
                "momentum": ["by_return_63"],
                "trend_quality": ["by_adjusted_slope_90"],
            },
            "ranking_details": {
                "by_return_63": [
                    {"rank": 1, "symbol": "AAA", "value": 0.3},
                    {"rank": 2, "symbol": "BBB", "value": 0.2},
                ],
                "by_adjusted_slope_90": [
                    {"rank": 1, "symbol": "BBB", "value": 0.9},
                ],
            },
            "candidate_summary": [{"symbol": "AAA"}, {"symbol": "BBB"}],
            "warnings": [],
        },
        None,
    )

    assert "3 requested symbols" in explanation
    assert "3 loaded" in explanation
    assert "0 failed" in explanation
    assert "top 2" in explanation
    assert "2 candidate summary rows" in explanation
    assert "momentum" in explanation
    assert "by_return_63: AAA=0.3, BBB=0.2" in explanation
    assert "trend_quality" in explanation


def test_market_load_history_tables_summary_formatter_shows_momentum_stage_profile():
    explanation = explain_tool_result(
        "market_load_history_tables_summary",
        {"symbols": ["AAA", "BBB"], "evidence_profile": "momentum_stage"},
        {
            "evidence_profile": "momentum_stage",
            "coverage": {
                "requested_count": 2,
                "loaded_count": 2,
                "failed_count": 0,
                "top_n": 2,
                "candidate_summary_limit": 2,
                "ranking_count": 2,
            },
            "rank_groups": {
                "freshness": ["by_rank_delta_4w"],
                "relative_strength": ["by_excess_return_vs_qqq_6m"],
            },
            "ranking_details": {
                "by_rank_delta_4w": [{"rank": 1, "symbol": "AAA", "value": 10}],
                "by_excess_return_vs_qqq_6m": [
                    {"rank": 1, "symbol": "BBB", "value": 0.12}
                ],
            },
            "candidate_summary": [
                {"symbol": "AAA", "stage_warning_flags": ["extreme_atr_extension"]},
                {"symbol": "BBB", "stage_warning_flags": []},
            ],
            "benchmark_context": {
                "QQQ": {"return_126": 0.08, "available": True},
                "SPY": {"return_126": 0.04, "available": True},
            },
            "warnings": [],
        },
        None,
    )

    assert "momentum_stage" in explanation
    assert "freshness" in explanation
    assert "by_rank_delta_4w: AAA=10" in explanation
    assert "relative_strength" in explanation
    assert "benchmark 126-bar returns: QQQ=0.08, SPY=0.04" in explanation
    assert "extreme_atr_extension" in explanation


def test_market_load_history_tables_summary_formatter_shows_empty_candidate_summary_rows():
    explanation = explain_tool_result(
        "market_load_history_tables_summary",
        {"symbols": ["AAA", "BBB"]},
        {
            "coverage": {
                "requested_count": 2,
                "loaded_count": 0,
                "failed_count": 2,
            },
            "candidate_summary": [],
            "warnings": ["No ranked candidates"],
        },
        None,
    )

    assert "2 requested symbols" in explanation
    assert "0 loaded" in explanation
    assert "2 failed" in explanation
    assert "0 candidate summary rows" in explanation


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


def test_orders_preflight_check_formatter_explains_ready_order():
    text = explain_tool_result(
        "orders_preflight_check",
        {},
        {
            "readiness": "ready",
            "can_submit": True,
            "order": {"symbol": "SPY", "side": "buy", "quantity": 3},
            "account": {"cash": 1000.0},
            "position": {"quantity": 5},
            "price": {"last_price": 100.0},
            "estimate": {"estimated_order_value": 300.0, "estimated_cash_after_order": 700.0},
        },
        None,
    )

    assert "Preflight ready" in text
    assert "buy 3 SPY" in text
    assert "cash 1000.0" in text
    assert "last price 100.0" in text
    assert "estimated value 300.0" in text


def test_orders_preflight_check_formatter_explains_blocked_order():
    text = explain_tool_result(
        "orders_preflight_check",
        {},
        {
            "readiness": "blocked",
            "can_submit": False,
            "order": {"symbol": "VNQ", "side": "sell", "quantity": 20},
            "account": {"cash": 1000.0},
            "position": {"quantity": 5},
            "price": {"last_price": 90.0},
            "estimate": {"estimated_order_value": 1800.0, "estimated_cash_after_order": 1000.0},
            "blockers": [
                {
                    "code": "INSUFFICIENT_POSITION",
                    "message": "Position has only 5 shares available.",
                }
            ],
        },
        None,
    )

    assert "Preflight blocked" in text
    assert "sell 20 VNQ" in text
    assert "INSUFFICIENT_POSITION" in text
    assert "Position has only 5 shares available." in text


def test_orders_preflight_check_formatter_preserves_zero_quantity():
    text = explain_tool_result(
        "orders_preflight_check",
        {"symbol": "SPY", "side": "buy", "quantity": 0},
        {
            "readiness": "blocked",
            "can_submit": False,
            "order": {"symbol": "SPY", "side": "buy", "quantity": 0},
            "account": {"cash": 1000.0},
            "position": {"quantity": 0},
            "price": {"last_price": 100.0},
            "estimate": {"estimated_order_value": None, "estimated_cash_after_order": None},
            "blockers": [{"code": "INVALID_QUANTITY", "message": "quantity must be greater than 0."}],
        },
        None,
    )

    assert "buy 0 SPY" in text
    assert "current quantity 0" in text
    assert "INVALID_QUANTITY" in text


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


def test_orders_submit_and_confirm_order_formatter_explains_success():
    text = explain_tool_result(
        "orders_submit_and_confirm_order",
        {"symbol": "SPY", "side": "buy", "quantity": 2},
        {
            "symbol": "SPY",
            "side": "buy",
            "quantity": 2,
            "submitted": True,
            "confirmed": True,
            "can_continue": True,
            "confirmation_status": "filled",
            "identifier": "order-123",
            "submit_result": {
                "order": {
                    "identifier": "order-123",
                    "status": "fill",
                    "side": "buy",
                    "quantity": 2,
                    "asset": {"symbol": "SPY", "asset_type": "stock"},
                }
            },
            "confirm_result": {"attempt_count": 1, "confirmed": True, "can_continue": True},
            "warnings": [],
            "blockers": [],
        },
        None,
    )

    assert "Submitted and confirmed order order-123" in text
    assert "buy 2 SPY" in text
    assert "filled" in text
    assert "1 attempt" in text
    assert "can_continue=true" in text


def test_orders_submit_and_confirm_order_formatter_explains_confirmation_blocker():
    text = explain_tool_result(
        "orders_submit_and_confirm_order",
        {"symbol": "SPY", "side": "buy", "quantity": 2},
        {
            "symbol": "SPY",
            "side": "buy",
            "quantity": 2,
            "submitted": True,
            "confirmed": False,
            "can_continue": False,
            "confirmation_status": "open_after_retries",
            "identifier": "order-123",
            "confirm_result": {"attempt_count": 3, "warnings": ["Order remained active."]},
            "warnings": ["Order remained active."],
            "blockers": [{"code": "CONFIRMATION_FAILED", "message": "Order was submitted but not confirmed."}],
        },
        None,
    )

    assert "Submit-and-confirm blocked" in text
    assert "buy 2 SPY" in text
    assert "open_after_retries" in text
    assert "can_continue=false" in text
    assert "CONFIRMATION_FAILED" in text


def test_orders_submit_and_confirm_order_formatter_explains_submit_blocker():
    text = explain_tool_result(
        "orders_submit_and_confirm_order",
        {"symbol": "SPY", "side": "buy", "quantity": 2},
        {
            "symbol": "SPY",
            "side": "buy",
            "quantity": 2,
            "submitted": False,
            "confirmed": False,
            "can_continue": False,
            "identifier": None,
            "blockers": [{"code": "ORDER_READINESS_REQUIRED", "message": "Missing matching preflight."}],
        },
        None,
    )

    assert "Submit-and-confirm blocked" in text
    assert "buy 2 SPY" in text
    assert "ORDER_READINESS_REQUIRED" in text
    assert "Missing matching preflight" in text


def test_orders_execute_order_formatter_explains_success():
    text = explain_tool_result(
        "orders_execute_order",
        {"symbol": "SPY", "side": "buy", "quantity": 3},
        {
            "sequence": 1,
            "symbol": "SPY",
            "side": "buy",
            "quantity": 3,
            "execution_status": "completed",
            "can_continue": True,
            "preflight_result": {"readiness": "ready", "can_submit": True},
            "submit_and_confirm_result": {
                "submitted": True,
                "confirmed": True,
                "can_continue": True,
                "confirmation_status": "filled",
                "identifier": "order-123",
            },
            "blockers": [],
        },
        None,
    )

    assert "sequence 1" in text
    assert "buy 3 SPY" in text
    assert "Preflight ready" in text
    assert "submitted=true" in text
    assert "confirmed=true" in text
    assert "order-123" in text
    assert "can_continue=true" in text


def test_orders_execute_order_formatter_explains_preflight_blocker_before_submit():
    text = explain_tool_result(
        "orders_execute_order",
        {"symbol": "SPY", "side": "buy", "quantity": 3},
        {
            "sequence": 2,
            "symbol": "SPY",
            "side": "buy",
            "quantity": 3,
            "execution_status": "blocked",
            "can_continue": False,
            "preflight_result": {"readiness": "blocked", "can_submit": False},
            "submit_and_confirm_result": None,
            "blockers": [
                {
                    "code": "INSUFFICIENT_CASH_ESTIMATE",
                    "message": "Estimated cost would exceed available cash.",
                }
            ],
        },
        None,
    )

    assert "blocked before submit" in text
    assert "sequence 2" in text
    assert "buy 3 SPY" in text
    assert "Preflight blocked" in text
    assert "can_continue=false" in text
    assert "INSUFFICIENT_CASH_ESTIMATE" in text
    assert "Estimated cost would exceed available cash." in text


def test_orders_execute_order_formatter_explains_confirmation_blocker_after_submit():
    text = explain_tool_result(
        "orders_execute_order",
        {"symbol": "SPY", "side": "buy", "quantity": 3},
        {
            "symbol": "SPY",
            "side": "buy",
            "quantity": 3,
            "execution_status": "blocked",
            "can_continue": False,
            "preflight_result": {"readiness": "ready", "can_submit": True},
            "submit_and_confirm_result": {
                "submitted": True,
                "confirmed": False,
                "can_continue": False,
                "confirmation_status": "open_after_retries",
                "identifier": "order-123",
                "blockers": [
                    {
                        "code": "CONFIRMATION_FAILED",
                        "message": "Order was submitted but not confirmed.",
                    }
                ],
            },
            "blockers": [
                {
                    "code": "CONFIRMATION_FAILED",
                    "message": "Order was submitted but not confirmed.",
                }
            ],
        },
        None,
    )

    assert "submitted but not confirmed" in text
    assert "buy 3 SPY" in text
    assert "Preflight ready" in text
    assert "open_after_retries" in text
    assert "order-123" in text
    assert "can_continue=false" in text
    assert "CONFIRMATION_FAILED" in text
    assert "Order was submitted but not confirmed." in text


def test_orders_execute_order_formatter_explains_unknown_submit_confirm_after_ready_preflight():
    text = explain_tool_result(
        "orders_execute_order",
        {"symbol": "SPY", "side": "buy", "quantity": 3},
        {
            "symbol": "SPY",
            "side": "buy",
            "quantity": 3,
            "execution_status": "blocked",
            "can_continue": False,
            "preflight_result": {"readiness": "ready", "can_submit": True},
            "submit_and_confirm_result": None,
            "internal_steps": [
                {"step": "preflight", "tool": "orders_preflight_check", "status": "ready"},
                {
                    "step": "submit_and_confirm",
                    "tool": "orders_submit_and_confirm_order",
                    "status": "blocked",
                },
            ],
            "blockers": [
                {
                    "code": "SUBMIT_AND_CONFIRM_FAILED",
                    "message": "orders_submit_and_confirm_order did not return a usable result.",
                }
            ],
        },
        None,
    )

    assert "blocked before submit" not in text
    assert "blocked during submit/confirm" in text
    assert "buy 3 SPY" in text
    assert "Preflight ready" in text
    assert "can_continue=false" in text
    assert "SUBMIT_AND_CONFIRM_FAILED" in text
    assert "orders_submit_and_confirm_order did not return a usable result." in text


def test_execution_plan_execute_formatter_explains_completed_multi_order_plan():
    text = explain_tool_result(
        "execution_plan_execute",
        {
            "execution_plan": {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [
                    {"sequence": 1, "symbol": "VGIT", "side": "sell", "quantity": 406},
                    {"sequence": 2, "symbol": "GLD", "side": "buy", "quantity": 107},
                ],
            }
        },
        {
            "plan_status": "completed",
            "orders_requested": 2,
            "orders_attempted": 2,
            "orders_completed": 2,
            "orders_blocked": 0,
            "orders_skipped": 0,
            "completed_orders": [
                {"sequence": 1, "symbol": "VGIT", "side": "sell", "quantity": 406},
                {"sequence": 2, "symbol": "GLD", "side": "buy", "quantity": 107},
            ],
            "final_account_snapshot": {"cash": 127.45},
        },
        None,
    )

    assert "Execution plan completed" in text
    assert "2 requested" in text
    assert "2 attempted" in text
    assert "2 completed" in text
    assert "sell VGIT 406" in text
    assert "buy GLD 107" in text
    assert "Final cash: 127.45" in text


def test_execution_plan_execute_formatter_explains_model_facing_summary():
    text = explain_tool_result(
        "execution_plan_execute",
        {"execution_plan": {"schema_version": 1, "intent": "rebalance", "orders": []}},
        {
            "schema_version": 1,
            "tool_name": "execution_plan_execute",
            "response_type": "model_facing_summary",
            "plan_status": "completed",
            "orders_requested": 2,
            "orders_attempted": 2,
            "orders_completed": 2,
            "orders_blocked": 0,
            "orders_skipped": 0,
            "completed_orders": [
                {"sequence": 1, "symbol": "VGIT", "side": "sell", "quantity": 406, "confirmed": True},
                {"sequence": 2, "symbol": "GLD", "side": "buy", "quantity": 107, "confirmed": True},
            ],
            "final_account": {"cash": 630.15, "positions": [{"symbol": "GLD", "quantity": 107.0}]},
            "audit_details_available": True,
        },
        None,
    )

    assert "Execution plan completed" in text
    assert "sell VGIT 406" in text
    assert "buy GLD 107" in text
    assert "Final cash: 630.15" in text


def test_execution_plan_execute_formatter_shows_more_count_for_long_completed_orders():
    text = explain_tool_result(
        "execution_plan_execute",
        {"execution_plan": {"schema_version": 1, "intent": "rebalance", "orders": []}},
        {
            "plan_status": "completed",
            "orders_requested": 8,
            "orders_attempted": 8,
            "orders_completed": 8,
            "orders_blocked": 0,
            "orders_skipped": 0,
            "completed_orders": [
                {"sequence": 1, "symbol": "AAA", "side": "buy", "quantity": 1},
                {"sequence": 2, "symbol": "BBB", "side": "buy", "quantity": 2},
                {"sequence": 3, "symbol": "CCC", "side": "buy", "quantity": 3},
                {"sequence": 4, "symbol": "DDD", "side": "buy", "quantity": 4},
                {"sequence": 5, "symbol": "EEE", "side": "buy", "quantity": 5},
                {"sequence": 6, "symbol": "FFF", "side": "buy", "quantity": 6},
                {"sequence": 7, "symbol": "GGG", "side": "buy", "quantity": 7},
                {"sequence": 8, "symbol": "HHH", "side": "buy", "quantity": 8},
            ],
        },
        None,
    )

    assert "buy AAA 1" in text
    assert "buy FFF 6" in text
    assert "buy GGG 7" not in text
    assert "and 2 more" in text


def test_execution_plan_execute_formatter_explains_hold_plan_without_orders():
    text = explain_tool_result(
        "execution_plan_execute",
        {"execution_plan": {"schema_version": 1, "intent": "hold", "orders": []}},
        {
            "plan_status": "completed",
            "orders_requested": 0,
            "orders_attempted": 0,
            "orders_completed": 0,
            "orders_blocked": 0,
            "orders_skipped": 0,
            "completed_orders": [],
            "final_account_snapshot": {"cash": 1000.0},
        },
        None,
    )

    assert "Execution plan completed" in text
    assert "0 requested" in text
    assert "No orders were submitted" in text


def test_execution_plan_execute_formatter_does_not_claim_unknown_or_missing_status_completed():
    for raw_result in (
        {
            "plan_status": "partially_done",
            "orders_requested": 3,
            "orders_attempted": 2,
            "orders_completed": 1,
            "orders_blocked": 1,
            "orders_skipped": 1,
            "blockers": [{"code": "AMBIGUOUS_STATUS", "message": "Producer returned an unexpected status."}],
        },
        {
            "orders_requested": 1,
            "orders_attempted": 0,
            "orders_completed": 0,
            "orders_blocked": 0,
            "orders_skipped": 1,
        },
    ):
        text = explain_tool_result(
            "execution_plan_execute",
            {"execution_plan": {"schema_version": 1, "intent": "rebalance", "orders": []}},
            raw_result,
            None,
        )

        assert "Execution plan completed" not in text
        assert "unknown status" in text or "missing status" in text
        assert "requested" in text


def test_execution_plan_execute_formatter_explains_invalid_plan_before_submission():
    text = explain_tool_result(
        "execution_plan_execute",
        {"execution_plan": {"schema_version": 1, "intent": "rebalance", "orders": []}},
        {
            "plan_status": "invalid",
            "orders_requested": 2,
            "orders_attempted": 0,
            "orders_completed": 0,
            "orders_blocked": 0,
            "orders_skipped": 2,
            "blockers": [
                {
                    "code": "INVALID_ORDER_SEQUENCE",
                    "message": "execution_plan.orders must already be listed in ascending sequence order.",
                }
            ],
        },
        None,
    )

    assert "Execution plan invalid before submission" in text
    assert "INVALID_ORDER_SEQUENCE" in text
    assert "execution_plan.orders must already be listed in ascending sequence order." in text
    assert "No orders were submitted" in text


def test_execution_plan_execute_formatter_explains_blocked_plan():
    text = explain_tool_result(
        "execution_plan_execute",
        {"execution_plan": {"schema_version": 1, "intent": "rebalance", "orders": []}},
        {
            "plan_status": "blocked",
            "orders_requested": 3,
            "orders_attempted": 2,
            "orders_completed": 1,
            "orders_blocked": 1,
            "orders_skipped": 1,
            "blocked_orders": [
                {
                    "sequence": 2,
                    "symbol": "SPY",
                    "side": "buy",
                    "quantity": 999,
                    "blockers": [
                        {"code": "NEGATIVE_CASH_NOT_ALLOWED", "message": "Cash would become negative."}
                    ],
                }
            ],
        },
        None,
    )

    assert "Execution plan blocked" in text
    assert "sequence 2" in text
    assert "buy SPY 999" in text
    assert "NEGATIVE_CASH_NOT_ALLOWED" in text
    assert "Cash would become negative." in text
    assert "1 skipped" in text


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
