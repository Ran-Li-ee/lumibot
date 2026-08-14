# Execution Plan Model-Facing Summary Audit Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `execution_plan_execute` preserve full execution audit details in trace while returning a compact deterministic `model_facing_summary` to the LLM.

**Architecture:** Keep the current full `execution_plan_execute` result shape intact and add a summary projection inside the execution-plan tool result. Add a runtime projection path that sends the summary to ADK/LLM while the boundary trace continues to record the unpruned raw payload. Update replay formatting and execution-agent wording so developers can inspect both what the model saw and the full audit payload.

**Tech Stack:** Python 3, Lumibot built-in agent tools, Google ADK runtime wrapper, LiteLLM/OpenAI provider path, Flask/static replay UI, pytest, ruff.

---

## File Map

- Modify `lumibot/components/agents/builtins.py`
  - Add deterministic execution-plan model-facing summary helpers.
  - Attach `model_facing_summary` to completed, blocked, invalid, and hold payloads.
  - Keep `order_results`, `final_account_snapshot`, and nested audit fields unchanged.
  - Update `EXECUTION_PLAN_EXECUTE_DESCRIPTION`.

- Modify `lumibot/components/agents/runtime.py`
  - Add a tool-specific response projection helper for `execution_plan_execute`.
  - Make `_prune_tool_response_for_context_window()` return the summary before generic size pruning.

- Modify `lumibot/components/agents/replay_ui/formatters.py`
  - Make the `execution_plan_execute` formatter understand either the full result or a bare model-facing summary.
  - Prefer `final_account` from the summary, falling back to `final_account_snapshot`.

- Modify `lumibot/components/agents/replay_ui/loader.py`
  - Keep existing raw-boundary restoration behavior.
  - Add only the smallest loader change required by tests if the current loader cannot expose model-facing summary plus raw boundary data.

- Modify `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
  - Lightly update execution-agent system/task wording to say the tool returns a concise execution summary and full audit details are in trace/replay.

- Modify tests:
  - `tests/test_agent_tool_permissions.py`
  - `tests/test_agent_runtime_provider_keys.py`
  - `tests/test_agent_replay_ui_formatters.py`
  - `tests/test_agent_replay_ui_loader.py`
  - `tests/test_agent_manager.py`

- Create validation note:
  - `docs/superpowers/notes/2026-08-14-execution-plan-summary-audit-validation.md`

---

### Task 1: Add Execution-Plan Summary Contract Tests

**Files:**
- Modify: `tests/test_agent_tool_permissions.py`

- [ ] **Step 1: Add tests for completed and hold summaries**

Insert these tests after `test_execution_plan_execute_completes_valid_multi_order_plan_in_sequence()`:

```python
def test_execution_plan_execute_completed_result_includes_model_facing_summary():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 100000.0
    strategy.portfolio_value = 100000.0
    strategy.last_prices = {"VGIT": 60.0, "SPY": 100.0}
    strategy.positions = [_fake_position("VGIT", 10, market_value=600.0, current_price=60.0)]
    tool_map = _wrap_execute_plan_tools(strategy)

    result = tool_map["execution_plan_execute"](
        execution_plan={
            "schema_version": 1,
            "intent": "rebalance",
            "orders": [
                {
                    "sequence": 1,
                    "action": "submit_order",
                    "symbol": "VGIT",
                    "side": "sell",
                    "quantity_mode": "shares",
                    "quantity": 10,
                    "asset_type": "stock",
                    "order_type": "market",
                    "time_in_force": "day",
                },
                {
                    "sequence": 2,
                    "action": "submit_order",
                    "symbol": "SPY",
                    "side": "buy",
                    "quantity_mode": "shares",
                    "quantity": 3,
                    "asset_type": "stock",
                    "order_type": "market",
                    "time_in_force": "day",
                },
            ],
        }
    )

    summary = result["model_facing_summary"]

    assert result["order_results"][0]["order_result"]["preflight_result"] is not None
    assert result["order_results"][0]["order_result"]["submit_and_confirm_result"] is not None
    assert summary["schema_version"] == 1
    assert summary["tool_name"] == "execution_plan_execute"
    assert summary["response_type"] == "model_facing_summary"
    assert summary["plan_status"] == "completed"
    assert summary["can_continue"] is True
    assert summary["intent"] == "rebalance"
    assert summary["orders_requested"] == 2
    assert summary["orders_attempted"] == 2
    assert summary["orders_completed"] == 2
    assert summary["orders_blocked"] == 0
    assert summary["orders_skipped"] == 0
    assert [order["sequence"] for order in summary["completed_orders"]] == [1, 2]
    assert [order["symbol"] for order in summary["completed_orders"]] == ["VGIT", "SPY"]
    assert all(order["confirmed"] is True for order in summary["completed_orders"])
    assert all("order_result" not in order for order in summary["completed_orders"])
    assert summary["final_account"]["cash"] == result["final_account_snapshot"]["cash"]
    assert isinstance(summary["final_account"]["positions"], list)
    assert summary["audit_details_available"] is True
    assert "order_results" not in summary


def test_execution_plan_execute_hold_result_includes_model_facing_summary():
    strategy = _OrderReadinessStrategy()
    tool_map = _wrap_execute_plan_tools(strategy)

    result = tool_map["execution_plan_execute"](
        execution_plan={"schema_version": 1, "intent": "hold", "orders": []}
    )

    summary = result["model_facing_summary"]

    assert summary["response_type"] == "model_facing_summary"
    assert summary["plan_status"] == "completed"
    assert summary["intent"] == "hold"
    assert summary["orders_requested"] == 0
    assert summary["orders_attempted"] == 0
    assert summary["completed_orders"] == []
    assert summary["blocked_orders"] == []
    assert summary["skipped_orders"] == []
    assert summary["audit_details_available"] is True
    assert "No planned orders were submitted" in summary["summary"]
```

- [ ] **Step 2: Add tests for blocked and invalid summaries**

Insert these tests after `test_execution_plan_execute_reports_completed_orders_before_later_blocker()`:

```python
def test_execution_plan_execute_blocked_result_includes_model_facing_summary_with_root_cause():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 1000.0
    strategy.last_prices = {"SPY": 100.0, "GLD": 100.0, "VGIT": 60.0}
    tool_map = _wrap_execute_plan_tools(strategy)

    result = tool_map["execution_plan_execute"](
        execution_plan={
            "schema_version": 1,
            "intent": "rebalance",
            "orders": [
                {
                    "sequence": 1,
                    "action": "submit_order",
                    "symbol": "SPY",
                    "side": "buy",
                    "quantity_mode": "shares",
                    "quantity": 1,
                    "asset_type": "stock",
                    "order_type": "market",
                    "time_in_force": "day",
                },
                {
                    "sequence": 2,
                    "action": "submit_order",
                    "symbol": "GLD",
                    "side": "buy",
                    "quantity_mode": "shares",
                    "quantity": 20,
                    "asset_type": "stock",
                    "order_type": "market",
                    "time_in_force": "day",
                },
                {
                    "sequence": 3,
                    "action": "submit_order",
                    "symbol": "VGIT",
                    "side": "buy",
                    "quantity_mode": "shares",
                    "quantity": 1,
                    "asset_type": "stock",
                    "order_type": "market",
                    "time_in_force": "day",
                },
            ],
        }
    )

    summary = result["model_facing_summary"]

    assert result["plan_status"] == "blocked"
    assert summary["plan_status"] == "blocked"
    assert summary["can_continue"] is False
    assert summary["orders_requested"] == 3
    assert summary["orders_attempted"] == 2
    assert summary["orders_completed"] == 1
    assert summary["orders_blocked"] == 1
    assert summary["orders_skipped"] == 1
    assert summary["completed_orders"][0]["symbol"] == "SPY"
    assert summary["blocked_orders"][0]["sequence"] == 2
    assert summary["blocked_orders"][0]["symbol"] == "GLD"
    assert summary["skipped_orders"][0]["sequence"] == 3
    assert summary["blockers"][0]["code"] == "ORDER_BLOCKED"
    assert any(
        blocker["code"] == "INSUFFICIENT_CASH_ESTIMATE"
        for blocker in summary["blocked_orders"][0]["blockers"]
    )
    assert summary["audit_details_available"] is True
    assert "order_results" not in summary


def test_execution_plan_execute_invalid_result_includes_model_facing_summary():
    strategy = _OrderReadinessStrategy()
    tool_map = _wrap_execute_plan_tools(strategy)

    result = tool_map["execution_plan_execute"](
        execution_plan={"schema_version": 2, "intent": "rebalance", "orders": []}
    )

    summary = result["model_facing_summary"]

    assert result["plan_status"] == "invalid"
    assert summary["plan_status"] == "invalid"
    assert summary["can_continue"] is False
    assert summary["orders_attempted"] == 0
    assert summary["completed_orders"] == []
    assert summary["blocked_orders"] == []
    assert summary["skipped_orders"] == []
    assert summary["blockers"][0]["code"] == "UNSUPPORTED_PLAN_SCHEMA_VERSION"
    assert summary["audit_details_available"] is True
    assert "No orders were submitted" in summary["summary"]
```

- [ ] **Step 3: Run the new contract tests and confirm failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_agent_tool_permissions.py::test_execution_plan_execute_completed_result_includes_model_facing_summary `
  tests\test_agent_tool_permissions.py::test_execution_plan_execute_hold_result_includes_model_facing_summary `
  tests\test_agent_tool_permissions.py::test_execution_plan_execute_blocked_result_includes_model_facing_summary_with_root_cause `
  tests\test_agent_tool_permissions.py::test_execution_plan_execute_invalid_result_includes_model_facing_summary -q
```

Expected: all four tests fail with `KeyError: 'model_facing_summary'`.

---

### Task 2: Implement Execution-Plan Summary Builder

**Files:**
- Modify: `lumibot/components/agents/builtins.py`

- [ ] **Step 1: Add compact summary helpers**

Insert this code after `_execution_plan_result_item()`:

```python
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


def _execution_plan_model_order_summary(order: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = _execution_plan_order_summary(order, result)
    result = result if isinstance(result, dict) else {}
    submit_and_confirm_result = result.get("submit_and_confirm_result")
    if not isinstance(submit_and_confirm_result, dict):
        submit_and_confirm_result = {}
    confirmation_order = _execution_plan_confirmation_order_payload(result)

    confirmation_status = submit_and_confirm_result.get("confirmation_status")
    if confirmation_status is not None:
        summary["confirmation_status"] = _jsonable(confirmation_status)

    filled_quantity = _first_non_null_value(
        confirmation_order.get("filled_quantity"),
        confirmation_order.get("quantity"),
    )
    if filled_quantity is not None:
        summary["filled_quantity"] = _jsonable(filled_quantity)

    fill_price = _first_non_null_value(
        confirmation_order.get("avg_fill_price"),
        confirmation_order.get("fill_price"),
        confirmation_order.get("average_fill_price"),
    )
    if fill_price is not None:
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
    completed_orders = [
        _execution_plan_model_order_summary(order)
        for order in list(payload.get("completed_orders") or [])
        if isinstance(order, dict)
    ]
    blocked_orders = []
    for order in list(payload.get("blocked_orders") or []):
        if isinstance(order, dict):
            blocked_orders.append(
                {
                    **_execution_plan_model_order_summary(order),
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
```

- [ ] **Step 2: Attach summaries to all execution-plan payload constructors**

Change `_execution_plan_invalid_payload()` from returning the dict directly to:

```python
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
```

Change `_execution_plan_completed_payload()` from returning the dict directly to:

```python
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
```

Change `_execution_plan_blocked_payload()` from returning the dict directly to:

```python
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
        "blockers": [
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
```

- [ ] **Step 3: Run the summary contract tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_agent_tool_permissions.py::test_execution_plan_execute_completed_result_includes_model_facing_summary `
  tests\test_agent_tool_permissions.py::test_execution_plan_execute_hold_result_includes_model_facing_summary `
  tests\test_agent_tool_permissions.py::test_execution_plan_execute_blocked_result_includes_model_facing_summary_with_root_cause `
  tests\test_agent_tool_permissions.py::test_execution_plan_execute_invalid_result_includes_model_facing_summary -q
```

Expected: all four tests pass.

- [ ] **Step 4: Commit Task 1-2 changes**

Run:

```powershell
git add lumibot\components\agents\builtins.py tests\test_agent_tool_permissions.py
git commit -m "feat: add execution plan model-facing summary"
```

---

### Task 3: Add Runtime Projection Tests

**Files:**
- Modify: `tests/test_agent_runtime_provider_keys.py`

- [ ] **Step 1: Add focused projection tests**

Add these tests after `test_execution_plan_execute_mutates_trading_metadata_ignores_retry_env_override()`:

```python
def test_execution_plan_execute_pruning_uses_model_facing_summary_even_when_full_result_is_small():
    from lumibot.components.agents.runtime import _prune_tool_response_for_context_window

    summary = {
        "schema_version": 1,
        "tool_name": "execution_plan_execute",
        "response_type": "model_facing_summary",
        "plan_status": "completed",
        "orders_completed": 1,
        "audit_details_available": True,
    }
    full_result = {
        "schema_version": 1,
        "plan_status": "completed",
        "order_results": [{"order_result": {"preflight_result": {"large": "raw audit"}}}],
        "model_facing_summary": summary,
    }

    projected = _prune_tool_response_for_context_window(
        full_result,
        tool_name="execution_plan_execute",
        max_chars=100_000,
    )

    assert projected == summary
    assert "lumibot_tool_result_pruned" not in projected


def test_execution_plan_execute_pruning_uses_generic_excerpt_when_summary_missing():
    from lumibot.components.agents.runtime import _prune_tool_response_for_context_window

    full_result = {
        "schema_version": 1,
        "plan_status": "completed",
        "order_results": [{"order_result": {"payload": "x" * 5000}}],
    }

    projected = _prune_tool_response_for_context_window(
        full_result,
        tool_name="execution_plan_execute",
        max_chars=100,
    )

    assert projected["lumibot_tool_result_pruned"] is True
    assert projected["tool_name"] == "execution_plan_execute"
    assert "excerpt" in projected
```

- [ ] **Step 2: Run projection tests and confirm failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_agent_runtime_provider_keys.py::test_execution_plan_execute_pruning_uses_model_facing_summary_even_when_full_result_is_small `
  tests\test_agent_runtime_provider_keys.py::test_execution_plan_execute_pruning_uses_generic_excerpt_when_summary_missing -q
```

Expected: first test fails because the function currently returns `None` for small responses; second test passes or fails only if import ordering needs adjustment.

---

### Task 4: Implement Runtime Tool-Specific Projection

**Files:**
- Modify: `lumibot/components/agents/runtime.py`

- [ ] **Step 1: Add projection helper**

Insert this helper immediately before `_prune_tool_response_for_context_window()`:

```python
def _model_facing_summary_for_tool_response(
    tool_response: Any,
    *,
    tool_name: str | None,
) -> Any | None:
    if tool_name != "execution_plan_execute":
        return None
    if not isinstance(tool_response, dict):
        return None
    summary = tool_response.get("model_facing_summary")
    if not isinstance(summary, dict):
        return None
    if summary.get("response_type") != "model_facing_summary":
        return None
    return summary
```

- [ ] **Step 2: Project summary before generic pruning**

Modify `_prune_tool_response_for_context_window()` so the first lines are:

```python
def _prune_tool_response_for_context_window(
    tool_response: Any,
    *,
    tool_name: str | None,
    max_chars: int = 4_000,
) -> Any | None:
    model_facing_summary = _model_facing_summary_for_tool_response(
        tool_response,
        tool_name=tool_name,
    )
    if model_facing_summary is not None:
        return model_facing_summary
    if tool_name == "market_load_history_tables_summary":
        max_chars = max(max_chars, 6_000)
```

Leave the existing size-based pruning block unchanged after that insertion.

- [ ] **Step 3: Run runtime projection tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_agent_runtime_provider_keys.py::test_execution_plan_execute_pruning_uses_model_facing_summary_even_when_full_result_is_small `
  tests\test_agent_runtime_provider_keys.py::test_execution_plan_execute_pruning_uses_generic_excerpt_when_summary_missing -q
```

Expected: both tests pass.

- [ ] **Step 4: Commit runtime projection**

Run:

```powershell
git add lumibot\components\agents\runtime.py tests\test_agent_runtime_provider_keys.py
git commit -m "feat: project execution plan summary to model"
```

---

### Task 5: Update Replay Formatter And Loader Tests

**Files:**
- Modify: `tests/test_agent_replay_ui_formatters.py`
- Modify: `tests/test_agent_replay_ui_loader.py`

- [ ] **Step 1: Add formatter test for bare model-facing summary**

Add this test after `test_execution_plan_execute_formatter_explains_completed_multi_order_plan()`:

```python
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
```

- [ ] **Step 2: Add loader test for summary-as-model-facing plus raw audit boundary**

Add this test after `test_loader_uses_full_boundary_result_when_legacy_tool_result_is_pruned()`:

```python
def test_loader_keeps_execution_plan_model_facing_summary_and_boundary_audit(tmp_path):
    trace_path = tmp_path / "traces" / "execution_agent" / "trace.json"
    summary = {
        "schema_version": 1,
        "tool_name": "execution_plan_execute",
        "response_type": "model_facing_summary",
        "plan_status": "completed",
        "orders_requested": 1,
        "orders_attempted": 1,
        "orders_completed": 1,
        "orders_blocked": 0,
        "orders_skipped": 0,
        "completed_orders": [{"sequence": 1, "symbol": "GLD", "side": "buy", "quantity": 107}],
        "final_account": {"cash": 630.15},
        "blockers": [],
        "warnings": [],
        "audit_details_available": True,
    }
    full_result = {
        "schema_version": 1,
        "plan_status": "completed",
        "orders_requested": 1,
        "orders_attempted": 1,
        "orders_completed": 1,
        "orders_blocked": 0,
        "orders_skipped": 0,
        "completed_orders": [{"sequence": 1, "symbol": "GLD", "side": "buy", "quantity": 107}],
        "blocked_orders": [],
        "skipped_orders": [],
        "order_results": [
            {
                "sequence": 1,
                "symbol": "GLD",
                "side": "buy",
                "quantity": 107,
                "order_result": {
                    "preflight_result": {"readiness": "ready"},
                    "submit_and_confirm_result": {"confirmed": True},
                },
            }
        ],
        "final_account_snapshot": {"cash": 630.15},
        "blockers": [],
        "warnings": [],
        "model_facing_summary": summary,
    }
    _write_trace(
        trace_path,
        {
            "agent": "execution_agent",
            "model": "openai/test",
            "request": {"context": {}, "runtime_context": {}},
            "events": [
                {
                    "kind": "tool_call",
                    "tool_name": "execution_plan_execute",
                    "call_id": "call_A",
                    "payload": {"execution_plan": {"schema_version": 1, "intent": "rebalance", "orders": []}},
                },
                {
                    "kind": "tool_result",
                    "tool_name": "execution_plan_execute",
                    "call_id": "call_A",
                    "payload": summary,
                },
            ],
            "boundary_trace": {
                "schema_version": 1,
                "diagnostics": [],
                "events": [
                    {
                        "transition": "B06_PYTHON_TOOL_TO_WRAPPER",
                        "model_turn_id": "turn-1",
                        "tool_batch_id": "turn-1:batch:0001",
                        "call_id": "call_A",
                        "payload": {
                            "function_name": "execution_plan_execute",
                            "raw_result": {"semantic_value": full_result},
                        },
                    },
                    {
                        "transition": "B08_FUNCTION_TOOL_TO_ADK",
                        "model_turn_id": "turn-1",
                        "tool_batch_id": "turn-1:batch:0001",
                        "call_id": "call_A",
                        "payload": {
                            "function_name": "execution_plan_execute",
                            "function_tool_response": full_result,
                            "model_facing_response": summary,
                        },
                    },
                ],
            },
        },
    )

    agent = load_agent_trace(trace_path)
    call = agent.tool_batches[0].calls[0]

    assert call.raw_result == summary
    assert "Execution plan completed" in call.human_explanation
    assert agent.boundary_trace.events[0].payload["raw_result"]["semantic_value"]["order_results"][0][
        "order_result"
    ]["preflight_result"]["readiness"] == "ready"
    assert agent.boundary_trace.events[1].payload["model_facing_response"] == summary
```

- [ ] **Step 3: Run formatter and loader tests and confirm failure mode**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_agent_replay_ui_formatters.py::test_execution_plan_execute_formatter_explains_model_facing_summary `
  tests\test_agent_replay_ui_loader.py::test_loader_keeps_execution_plan_model_facing_summary_and_boundary_audit -q
```

Expected: formatter test fails if it does not read `final_account`; loader test should pass if current loader already preserves summary output and boundary audit. If loader test fails, the implementation step below includes the required seam.

---

### Task 6: Implement Replay Formatter Support

**Files:**
- Modify: `lumibot/components/agents/replay_ui/formatters.py`
- Modify: `lumibot/components/agents/replay_ui/loader.py` only if Task 5 loader test fails.

- [ ] **Step 1: Update formatter cash fallback**

Inside `_execution_plan_execute()`, replace:

```python
        account = _nested_dict(result, "final_account_snapshot")
```

with:

```python
        account = _nested_dict(result, "final_account") or _nested_dict(result, "final_account_snapshot")
```

No other formatter change is required if the existing status/count/order logic works with the summary contract.

- [ ] **Step 2: Preserve summary result in loader**

If `test_loader_keeps_execution_plan_model_facing_summary_and_boundary_audit` fails because `_restore_pruned_tool_results_from_boundary()` replaces non-pruned summaries with full raw results, change `lumibot/components/agents/replay_ui/loader.py` so restoration only happens for payloads with `lumibot_tool_result_pruned`:

```python
def _is_pruned_tool_result(value: Any) -> bool:
    return isinstance(value, dict) and value.get("lumibot_tool_result_pruned") is True
```

Use `_is_pruned_tool_result(call.raw_result)` at the restoration decision point. Keep current behavior for pruned legacy payloads exactly as covered by `test_loader_uses_full_boundary_result_when_legacy_tool_result_is_pruned()`.

- [ ] **Step 3: Run replay tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_agent_replay_ui_formatters.py::test_execution_plan_execute_formatter_explains_model_facing_summary `
  tests\test_agent_replay_ui_loader.py::test_loader_keeps_execution_plan_model_facing_summary_and_boundary_audit `
  tests\test_agent_replay_ui_loader.py::test_loader_uses_full_boundary_result_when_legacy_tool_result_is_pruned -q
```

Expected: all selected tests pass.

- [ ] **Step 4: Commit replay support**

Run:

```powershell
git add lumibot\components\agents\replay_ui\formatters.py lumibot\components\agents\replay_ui\loader.py tests\test_agent_replay_ui_formatters.py tests\test_agent_replay_ui_loader.py
git commit -m "feat: replay execution plan model-facing summaries"
```

---

### Task 7: Update Tool Description And Execution-Agent Prompt

**Files:**
- Modify: `lumibot/components/agents/builtins.py`
- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `tests/test_agent_tool_permissions.py`
- Modify: `tests/test_agent_manager.py`

- [ ] **Step 1: Update description tests**

In `test_builtin_order_tools_expose_execution_plan_execute_definition()`, add:

```python
    assert "concise execution summary" in tool.description
    assert "full audit details" in tool.description
```

In `tests/test_agent_manager.py`, inside `test_execution_agent_with_execution_plan_execute_tool_receives_stage_d_policy()`, add:

```python
    assert "concise execution summary" in prompt_lower
    assert "full audit details" in prompt_lower
```

- [ ] **Step 2: Run description tests and confirm failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_agent_tool_permissions.py::test_builtin_order_tools_expose_execution_plan_execute_definition `
  tests\test_agent_manager.py::test_execution_agent_with_execution_plan_execute_tool_receives_stage_d_policy -q
```

Expected: both tests fail on missing text.

- [ ] **Step 3: Update built-in tool description**

Change `EXECUTION_PLAN_EXECUTE_DESCRIPTION` to:

```python
EXECUTION_PLAN_EXECUTE_DESCRIPTION = (
    "Execute one complete strict execution_plan in sequence order. "
    "This tool validates the plan, executes each order through readiness checks, submission, and confirmation, "
    "stops on the first blocker, and returns a concise execution summary to the model while full audit details "
    "are recorded in trace/replay. "
    "This tool mutates trading state. It does not generate, repair, reorder, optimize, or modify the plan. "
    "Pass the execution_plan exactly as provided by the upstream planner. "
    "If plan_status is blocked or invalid, do not call lower-level tools; summarize where execution stopped and why."
)
```

- [ ] **Step 4: Update mock quadrant execution-agent wording**

In `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`, update the execution-agent `system_prompt` sentence block to include:

```python
                "execution_plan_execute returns a concise execution summary for your final answer; "
                "full audit details are recorded in trace/replay for developer inspection. "
```

The resulting `system_prompt` should remain one compact paragraph:

```python
            system_prompt=(
                "Execution role: execute only provided execution_plan. "
                "Call execution_plan_execute exactly once with the complete execution_plan. "
                "Do not manually execute individual orders. "
                "Do not call lower-level order, account, open-order, or price tools when execution_plan_execute "
                "is available. Do not research, change fields, reorder orders, split orders, or repair the plan. "
                "execution_plan_execute returns a concise execution summary for your final answer; "
                "full audit details are recorded in trace/replay for developer inspection. "
                "If the tool returns plan_status=completed, summarize completed orders. If it returns "
                "plan_status=blocked or invalid, summarize where execution stopped and why."
            ),
```

Update the execution task prompt near `on_trading_iteration()` to:

```python
            task_prompt=(
                "Execute the provided execution_plan by calling execution_plan_execute exactly once with the "
                "complete execution_plan. Use the returned concise execution summary to write the final result. "
                "Do not infer missing order details beyond the tool response. Do not call per-order tools."
            ),
```

- [ ] **Step 5: Run description and prompt tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_agent_tool_permissions.py::test_builtin_order_tools_expose_execution_plan_execute_definition `
  tests\test_agent_manager.py::test_execution_agent_with_execution_plan_execute_tool_receives_stage_d_policy -q
```

Expected: both tests pass.

- [ ] **Step 6: Commit prompt and description changes**

Run:

```powershell
git add lumibot\components\agents\builtins.py lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_tool_permissions.py tests\test_agent_manager.py
git commit -m "docs: describe execution plan summary audit split"
```

---

### Task 8: Run Focused Regression Suite

**Files:**
- No code edits.

- [ ] **Step 1: Run focused pytest suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_agent_tool_permissions.py `
  tests\test_agent_runtime_provider_keys.py `
  tests\test_agent_replay_ui_formatters.py `
  tests\test_agent_replay_ui_loader.py `
  tests\test_agent_manager.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run focused ruff check**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check `
  lumibot\components\agents\builtins.py `
  lumibot\components\agents\runtime.py `
  lumibot\components\agents\replay_ui\formatters.py `
  lumibot\components\agents\replay_ui\loader.py `
  lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py `
  tests\test_agent_tool_permissions.py `
  tests\test_agent_runtime_provider_keys.py `
  tests\test_agent_replay_ui_formatters.py `
  tests\test_agent_replay_ui_loader.py `
  tests\test_agent_manager.py
```

Expected: `All checks passed!`

- [ ] **Step 3: Commit test-only fixes if Step 1 or Step 2 required code cleanup**

If Steps 1-2 required code cleanup, commit with:

```powershell
git add lumibot tests
git commit -m "test: stabilize execution plan summary projection"
```

If no cleanup was required, do not create an empty commit.

---

### Task 9: Run One-Day Benchmark And Inspect Trace

**Files:**
- Create: `docs/superpowers/notes/2026-08-14-execution-plan-summary-audit-validation.md`

- [ ] **Step 1: Run one-day mock quadrant benchmark**

Use the OpenAI key from `D:\Lumibot\project_notes\API.txt` and the default development model `openai/gpt-5.6-luna`.

Run from this worktree:

```powershell
$env:AI_TRADING_TEAM_MODEL = "openai/gpt-5.6-luna"
.\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py `
  --strategy mock-growth-inflation-quadrant `
  --start 2024-09-10 `
  --end 2024-09-11 `
  --max-workers 1 `
  --max-run-attempts 1 `
  --agent-run-timeout-seconds 1800 `
  --env-file D:\Lumibot\project_notes\API.txt
```

Expected: benchmark exits with status `passed`.

- [ ] **Step 2: Inspect latest artifact for summary/audit separation**

Run:

```powershell
@'
from pathlib import Path
import json

root = Path("artifacts/ai_trading_team_example_benchmarks")
latest = max(root.glob("*/mock-growth-inflation-quadrant"), key=lambda path: path.stat().st_mtime)
trace_paths = sorted(latest.glob("agent_runtime/*/execution_agent/trace.json"))
if not trace_paths:
    raise SystemExit(f"No execution_agent trace found under {latest}")
trace_path = trace_paths[-1]
trace = json.loads(trace_path.read_text(encoding="utf-8"))

tool_results = [event for event in trace.get("events", []) if event.get("kind") == "tool_result"]
execution_results = [
    event.get("payload")
    for event in tool_results
    if event.get("tool_name") == "execution_plan_execute"
]
if not execution_results:
    raise SystemExit("No execution_plan_execute tool_result found")
model_result = execution_results[-1]
print("TRACE", trace_path)
print("MODEL_RESPONSE_TYPE", model_result.get("response_type"))
print("MODEL_HAS_ORDER_RESULTS", "order_results" in model_result)

boundary_events = trace.get("boundary_trace", {}).get("events", [])
b06_values = [
    event.get("payload", {}).get("raw_result", {}).get("semantic_value")
    for event in boundary_events
    if event.get("transition") == "B06_PYTHON_TOOL_TO_WRAPPER"
    and event.get("payload", {}).get("function_name") == "execution_plan_execute"
]
b08_values = [
    event.get("payload", {}).get("model_facing_response")
    for event in boundary_events
    if event.get("transition") == "B08_FUNCTION_TOOL_TO_ADK"
    and event.get("payload", {}).get("function_name") == "execution_plan_execute"
]
raw = b06_values[-1] if b06_values else {}
model_facing = b08_values[-1] if b08_values else {}
print("B06_HAS_ORDER_RESULTS", isinstance(raw, dict) and "order_results" in raw)
print("B06_HAS_SUMMARY", isinstance(raw, dict) and "model_facing_summary" in raw)
print("B08_RESPONSE_TYPE", model_facing.get("response_type") if isinstance(model_facing, dict) else None)
print("B08_HAS_ORDER_RESULTS", isinstance(model_facing, dict) and "order_results" in model_facing)
'@ | .\.venv\Scripts\python.exe -
```

Expected output includes:

```text
MODEL_RESPONSE_TYPE model_facing_summary
MODEL_HAS_ORDER_RESULTS False
B06_HAS_ORDER_RESULTS True
B06_HAS_SUMMARY True
B08_RESPONSE_TYPE model_facing_summary
B08_HAS_ORDER_RESULTS False
```

- [ ] **Step 3: Start replay UI and manually verify discoverability**

Run:

```powershell
.\.venv\Scripts\python.exe scripts\agent_trace_ui.py
```

Expected:

```text
Agent Replay UI available at http://127.0.0.1:8765
```

Open `http://127.0.0.1:8765`, select:

```text
Strategy: AITradingTeamMockGrowthInflationQuadrantStrategy
Backtest run: latest mock-growth-inflation-quadrant benchmark
System run: 2024-09-10
Agent: execution_agent
```

Manual acceptance:

- Tool Calls output for `execution_plan_execute` is compact and does not contain full nested `order_results`.
- Boundary trace/details still expose the full raw execution result with nested preflight, submit, confirm, and account snapshots.
- Human explanation still says whether execution completed, blocked, or invalid.

- [ ] **Step 4: Write validation note**

Run this script to create `docs/superpowers/notes/2026-08-14-execution-plan-summary-audit-validation.md` from the latest benchmark artifact and trace:

```powershell
@'
from pathlib import Path
import json

artifact_root = Path("artifacts/ai_trading_team_example_benchmarks")
artifact = max(artifact_root.glob("*/mock-growth-inflation-quadrant"), key=lambda path: path.stat().st_mtime)
trace_path = sorted(artifact.glob("agent_runtime/*/execution_agent/trace.json"))[-1]
trace = json.loads(trace_path.read_text(encoding="utf-8"))
tool_results = [event for event in trace.get("events", []) if event.get("kind") == "tool_result"]
execution_results = [
    event.get("payload")
    for event in tool_results
    if event.get("tool_name") == "execution_plan_execute"
]
model_result = execution_results[-1] if execution_results else {}
boundary_events = trace.get("boundary_trace", {}).get("events", [])
b06_values = [
    event.get("payload", {}).get("raw_result", {}).get("semantic_value")
    for event in boundary_events
    if event.get("transition") == "B06_PYTHON_TOOL_TO_WRAPPER"
    and event.get("payload", {}).get("function_name") == "execution_plan_execute"
]
b08_values = [
    event.get("payload", {}).get("model_facing_response")
    for event in boundary_events
    if event.get("transition") == "B08_FUNCTION_TOOL_TO_ADK"
    and event.get("payload", {}).get("function_name") == "execution_plan_execute"
]
raw = b06_values[-1] if b06_values else {}
model_facing = b08_values[-1] if b08_values else {}
summary_path = artifact / "summary.json"
benchmark_status = "unknown"
if summary_path.exists():
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    benchmark_status = str(summary.get("status") or summary.get("overall_status") or "unknown")

note = f"""# Execution Plan Summary Audit Validation

Date: 2026-08-14

## Benchmark

- Command: `scripts/run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-10 --end 2024-09-11`
- Model: `openai/gpt-5.6-luna`
- Artifact: `{artifact.as_posix()}`
- Trace: `{trace_path.as_posix()}`
- Status: `{benchmark_status}`

## Trace Checks

- Model-facing `execution_plan_execute` tool result response type: `{model_result.get("response_type") if isinstance(model_result, dict) else None}`
- Model-facing result contains `order_results`: `{isinstance(model_result, dict) and "order_results" in model_result}`
- B06 raw result contains `order_results`: `{isinstance(raw, dict) and "order_results" in raw}`
- B06 raw result contains `model_facing_summary`: `{isinstance(raw, dict) and "model_facing_summary" in raw}`
- B08 model-facing response type: `{model_facing.get("response_type") if isinstance(model_facing, dict) else None}`
- B08 model-facing response contains `order_results`: `{isinstance(model_facing, dict) and "order_results" in model_facing}`

## UI Checks

- Replay UI discovered the latest run: `manual check required`
- Tool Calls showed compact summary: `manual check required`
- Boundary trace exposed full raw audit details: `manual check required`
- Account Curve link state: `manual check required`
- Performance Report link state: `manual check required`

## Notes

`execution_plan_execute` now separates model-facing execution summary from full audit details. Trading behavior, basket universes, macro regime logic, news access, and portfolio construction were not changed by this feature.
"""

out = Path("docs/superpowers/notes/2026-08-14-execution-plan-summary-audit-validation.md")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(note, encoding="utf-8")
print(out)
'@ | .\.venv\Scripts\python.exe -
```

- [ ] **Step 5: Commit validation note**

Run:

```powershell
git add docs\superpowers\notes\2026-08-14-execution-plan-summary-audit-validation.md
git commit -m "test: validate execution plan summary audit trace"
```

---

## Final Verification

- [ ] **Step 1: Check git status**

Run:

```powershell
git status --short
```

Expected: clean working tree, or only intentionally untracked benchmark artifacts that are ignored by git.

- [ ] **Step 2: Show commit log**

Run:

```powershell
git log --oneline -5
```

Expected: commits include:

```text
feat: add execution plan model-facing summary
feat: project execution plan summary to model
feat: replay execution plan model-facing summaries
docs: describe execution plan summary audit split
test: validate execution plan summary audit trace
```

---

## Self-Review Checklist

- Spec coverage:
  - Full raw result preserved: Tasks 1-2 and Task 9.
  - Compact `model_facing_summary` generated for completed, blocked, invalid, hold: Tasks 1-2.
  - LLM receives summary instead of full `order_results`: Tasks 3-4 and Task 9.
  - Boundary trace records raw and model-facing payloads: Tasks 3-5 and Task 9.
  - Replay UI explains summary and keeps audit detail inspectable: Tasks 5-6 and Task 9.
  - Prompt/tool description updated lightly: Task 7.
  - No basket/news/macro/portfolio changes: file map excludes those domains except execution-agent wording.

- Placeholder scan:
  - This plan contains no unresolved placeholder markers or unspecified implementation steps.

- Type consistency:
  - `model_facing_summary` is always a `dict`.
  - The summary uses `response_type == "model_facing_summary"`.
  - Runtime projection only applies to `tool_name == "execution_plan_execute"`.
  - Full audit remains in the existing result shape under `order_results` and `final_account_snapshot`.
