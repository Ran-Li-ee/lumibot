# Execution Plan Execute Stage D Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Stage D `execution_plan_execute` tool so the execution agent can execute one complete strict execution plan with one model-facing tool call.

**Architecture:** Keep portfolio planning unchanged. Add a plan-level built-in order tool that validates one strict `execution_plan`, internally reuses the existing Stage C `orders_execute_order` behavior for each order, stops on the first blocker, and returns a plan-level report with nested order results. Then update execution-agent prompt/tool visibility and replay formatting so the UI remains inspectable.

**Tech Stack:** Python, pytest, ruff, existing LumiBot agent built-ins, existing AgentManager prompt composition, existing Agent Replay formatter, existing benchmark runner.

---

## File Structure

- Modify `lumibot/components/agents/builtins.py`
  - Add `EXECUTION_PLAN_EXECUTE_DESCRIPTION`.
  - Add validation/report helper functions near the Stage C order execution helpers.
  - Add `_bind_execute_plan(strategy, manager)`.
  - Add `_OrderTools.execute_plan()`.
  - Add `BuiltinTools.orders.execute_plan()` to `BuiltinTools.all()`.

- Modify `lumibot/components/agents/manager.py`
  - Add `execution_plan_execute` to execution tool policy text.
  - Treat `execution_plan_execute` as execution/data/order evidence for warnings.

- Modify `lumibot/components/agents/replay_ui/formatters.py`
  - Add a human-readable formatter for `execution_plan_execute`.
  - Register it in `_FORMATTERS`.

- Modify `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
  - Change `execution_agent` visible tools from `BuiltinTools.orders.execute()` to `BuiltinTools.orders.execute_plan()`.
  - Change execution-agent system prompt and task prompt from per-order Stage C wording to one-call Stage D wording.
  - Leave macro, basket, and portfolio decision agents unchanged.

- Modify `tests/test_agent_tool_permissions.py`
  - Add tool definition, metadata, validation, success, blocked, and safety tests.

- Modify `tests/test_agent_manager.py`
  - Add prompt policy tests for `execution_plan_execute`.
  - Add warning/evidence coverage for `execution_plan_execute`.

- Modify `tests/test_agent_runtime_provider_keys.py`
  - Add retry-safety coverage for `execution_plan_execute`.

- Modify `tests/test_agent_replay_ui_formatters.py`
  - Add formatter coverage for completed, hold, invalid, and blocked plan reports.

- Modify `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
  - Update tool-surface and prompt-boundary tests from Stage C to Stage D.
  - Add assertions that non-execution agents do not see `execution_plan_execute`.

- Create `docs/superpowers/notes/2026-08-11-execution-plan-execute-stage-d-validation.md`
  - Record focused test commands, lint command, benchmark commands, artifact paths, and acceptance status.

---

### Task 1: Add Failing Tool Definition Tests

**Files:**
- Modify: `tests/test_agent_tool_permissions.py`

- [ ] **Step 1: Add definition tests beside existing Stage C order tool tests**

Insert after `test_builtin_order_tools_expose_execute_order_definition()`:

```python
def test_builtin_order_tools_expose_execution_plan_execute_definition():
    tool = BuiltinTools.orders.execute_plan()

    assert tool.name == "execution_plan_execute"
    assert "Execute one complete strict execution_plan" in tool.description
    assert "mutates trading state" in tool.description
    assert "does not generate, repair, reorder, optimize, or modify the plan" in tool.description
    assert callable(tool.binder)


def test_builtin_tools_all_includes_execution_plan_execute():
    assert "execution_plan_execute" in {tool.name for tool in BuiltinTools.all()}
```

- [ ] **Step 2: Add bound metadata test**

Insert after `test_bound_execute_order_metadata_marks_mutating_and_replayable()`:

```python
def test_bound_execution_plan_execute_metadata_marks_mutating_and_replayable():
    strategy = _OrderReadinessStrategy()
    manager = AgentManager(strategy)

    definition = BuiltinTools.orders.execute_plan()
    tool = definition.binder(strategy, manager)

    assert definition.metadata["mutates_trading"] is True
    assert tool.name == "execution_plan_execute"
    assert tool.metadata["kind"] == "builtin"
    assert tool.metadata["replay_on_cache"] is True
    assert tool.metadata["mutates_trading"] is True
```

- [ ] **Step 3: Run the new tests and verify they fail**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py::test_builtin_order_tools_expose_execution_plan_execute_definition tests\test_agent_tool_permissions.py::test_builtin_tools_all_includes_execution_plan_execute tests\test_agent_tool_permissions.py::test_bound_execution_plan_execute_metadata_marks_mutating_and_replayable -q
```

Expected:

```text
FAILED ... AttributeError: '_OrderTools' object has no attribute 'execute_plan'
```

---

### Task 2: Add Failing Plan Behavior Tests

**Files:**
- Modify: `tests/test_agent_tool_permissions.py`

- [ ] **Step 1: Add wrapper helper**

Insert after `_wrap_execute_order_tools(strategy)`:

```python
def _wrap_execute_plan_tools(strategy):
    return _wrap_builtin_tools(strategy, [BuiltinTools.orders.execute_plan()])
```

- [ ] **Step 2: Add hold-plan test**

Insert near the Stage C order execution tests:

```python
def test_execution_plan_execute_completes_hold_plan_without_submitting():
    strategy = _OrderReadinessStrategy()
    tool_map = _wrap_execute_plan_tools(strategy)

    result = tool_map["execution_plan_execute"](
        execution_plan={"schema_version": 1, "intent": "hold", "orders": []}
    )

    assert result["plan_status"] == "completed"
    assert result["can_continue"] is True
    assert result["intent"] == "hold"
    assert result["orders_requested"] == 0
    assert result["orders_attempted"] == 0
    assert result["orders_completed"] == 0
    assert result["orders_blocked"] == 0
    assert result["orders_skipped"] == 0
    assert result["order_results"] == []
    assert result["blockers"] == []
    assert strategy.submitted_orders == []
```

- [ ] **Step 3: Add successful multi-order test**

```python
def test_execution_plan_execute_completes_valid_multi_order_plan_in_sequence():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 100000.0
    strategy.portfolio_value = 100000.0
    strategy.last_prices = {"VGIT": 60.0, "SPY": 100.0, "GLD": 200.0}
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
                {
                    "sequence": 3,
                    "action": "submit_order",
                    "symbol": "GLD",
                    "side": "buy",
                    "quantity_mode": "shares",
                    "quantity": 2,
                    "asset_type": "stock",
                    "order_type": "market",
                    "time_in_force": "day",
                },
            ],
        }
    )

    assert result["plan_status"] == "completed"
    assert result["can_continue"] is True
    assert result["orders_requested"] == 3
    assert result["orders_attempted"] == 3
    assert result["orders_completed"] == 3
    assert result["orders_blocked"] == 0
    assert result["orders_skipped"] == 0
    assert [item["sequence"] for item in result["order_results"]] == [1, 2, 3]
    assert [order.asset.symbol for order in strategy.submitted_orders] == ["VGIT", "SPY", "GLD"]
    assert [order.side for order in strategy.submitted_orders] == ["sell", "buy", "buy"]
    assert all(item["execution_status"] == "completed" for item in result["order_results"])
    assert all(item["can_continue"] is True for item in result["order_results"])
    assert result["final_account_snapshot"] is not None
```

- [ ] **Step 4: Add invalid-sequence tests**

```python
@pytest.mark.parametrize(
    "orders, expected_message",
    [
        (
            [
                {"sequence": 2, "action": "submit_order", "symbol": "SPY", "side": "buy", "quantity": 1},
                {"sequence": 1, "action": "submit_order", "symbol": "GLD", "side": "buy", "quantity": 1},
            ],
            "ascending sequence order",
        ),
        (
            [
                {"sequence": 1, "action": "submit_order", "symbol": "SPY", "side": "buy", "quantity": 1},
                {"sequence": 1, "action": "submit_order", "symbol": "GLD", "side": "buy", "quantity": 1},
            ],
            "unique sequence",
        ),
        (
            [
                {"sequence": 1, "action": "submit_order", "symbol": "SPY", "side": "buy", "quantity": 1},
                {"sequence": 3, "action": "submit_order", "symbol": "GLD", "side": "buy", "quantity": 1},
            ],
            "1..N",
        ),
    ],
)
def test_execution_plan_execute_rejects_invalid_sequence_before_submit(orders, expected_message):
    strategy = _OrderReadinessStrategy()
    tool_map = _wrap_execute_plan_tools(strategy)

    result = tool_map["execution_plan_execute"](
        execution_plan={"schema_version": 1, "intent": "rebalance", "orders": orders}
    )

    assert result["plan_status"] == "invalid"
    assert result["can_continue"] is False
    assert result["orders_attempted"] == 0
    assert result["orders_completed"] == 0
    assert result["orders_blocked"] == 0
    assert strategy.submitted_orders == []
    assert "INVALID_ORDER_SEQUENCE" in {blocker["code"] for blocker in result["blockers"]}
    assert expected_message in result["blockers"][0]["message"]
```

- [ ] **Step 5: Add invalid field tests**

```python
@pytest.mark.parametrize(
    "plan, blocker_code",
    [
        ({}, "MISSING_EXECUTION_PLAN"),
        ({"schema_version": 2, "intent": "rebalance", "orders": []}, "UNSUPPORTED_PLAN_SCHEMA_VERSION"),
        ({"schema_version": 1, "intent": "trade", "orders": []}, "UNSUPPORTED_PLAN_INTENT"),
        (
            {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [{"sequence": 1, "action": "cancel_order", "symbol": "SPY", "side": "buy", "quantity": 1}],
            },
            "INVALID_ORDER_ACTION",
        ),
        (
            {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [{"sequence": 1, "action": "submit_order", "symbol": "", "side": "buy", "quantity": 1}],
            },
            "INVALID_ORDER_FIELDS",
        ),
        (
            {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [{"sequence": 1, "action": "submit_order", "symbol": "SPY", "side": "buy", "quantity": 0}],
            },
            "INVALID_ORDER_FIELDS",
        ),
        (
            {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [{"sequence": 1, "action": "submit_order", "symbol": "SPY", "side": "buy", "quantity": 1.5}],
            },
            "INVALID_ORDER_FIELDS",
        ),
        (
            {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [
                    {
                        "sequence": 1,
                        "action": "submit_order",
                        "symbol": "SPY",
                        "side": "buy",
                        "quantity": 1,
                        "order_type": "limit",
                    }
                ],
            },
            "INVALID_ORDER_FIELDS",
        ),
    ],
)
def test_execution_plan_execute_rejects_invalid_plan_before_submit(plan, blocker_code):
    strategy = _OrderReadinessStrategy()
    tool_map = _wrap_execute_plan_tools(strategy)

    result = tool_map["execution_plan_execute"](execution_plan=plan)

    assert result["plan_status"] == "invalid"
    assert result["can_continue"] is False
    assert result["orders_attempted"] == 0
    assert strategy.submitted_orders == []
    assert blocker_code in {blocker["code"] for blocker in result["blockers"]}
```

- [ ] **Step 6: Add blocker and skip tests**

```python
def test_execution_plan_execute_stops_after_order_blocker_and_skips_remaining_orders():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 250.0
    strategy.last_prices = {"SPY": 100.0, "GLD": 100.0}
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
                    "quantity": 3,
                    "asset_type": "stock",
                    "order_type": "market",
                    "time_in_force": "day",
                },
                {
                    "sequence": 2,
                    "action": "submit_order",
                    "symbol": "GLD",
                    "side": "buy",
                    "quantity": 1,
                    "asset_type": "stock",
                    "order_type": "market",
                    "time_in_force": "day",
                },
            ],
        }
    )

    assert result["plan_status"] == "blocked"
    assert result["can_continue"] is False
    assert result["orders_requested"] == 2
    assert result["orders_attempted"] == 1
    assert result["orders_completed"] == 0
    assert result["orders_blocked"] == 1
    assert result["orders_skipped"] == 1
    assert result["blocked_orders"][0]["sequence"] == 1
    assert result["skipped_orders"][0]["sequence"] == 2
    assert "INSUFFICIENT_CASH_ESTIMATE" in {
        blocker["code"]
        for blocker in result["blocked_orders"][0]["blockers"]
    }
    assert strategy.submitted_orders == []
```

```python
def test_execution_plan_execute_reports_completed_orders_before_later_blocker():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 1000.0
    strategy.last_prices = {"SPY": 100.0, "GLD": 100.0}
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
                    "quantity": 20,
                    "asset_type": "stock",
                    "order_type": "market",
                    "time_in_force": "day",
                },
            ],
        }
    )

    assert result["plan_status"] == "blocked"
    assert result["orders_completed"] == 1
    assert result["orders_blocked"] == 1
    assert result["completed_orders"][0]["sequence"] == 1
    assert result["blocked_orders"][0]["sequence"] == 2
    assert len(strategy.submitted_orders) == 1
```

- [ ] **Step 7: Add negative-cash regression test**

```python
def test_execution_plan_execute_preserves_negative_cash_guard():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 1000.0
    strategy.last_prices = {"SPY": 100.0}
    strategy.force_negative_cash_after_submit = True
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
                    "quantity": 3,
                    "asset_type": "stock",
                    "order_type": "market",
                    "time_in_force": "day",
                }
            ],
        }
    )

    assert result["plan_status"] == "blocked"
    assert result["orders_attempted"] == 1
    assert result["orders_completed"] == 0
    assert "NEGATIVE_CASH_NOT_ALLOWED" in {
        blocker["code"]
        for blocker in result["blocked_orders"][0]["blockers"]
    }
    assert strategy.submitted_orders == []
```

- [ ] **Step 8: Run the behavior tests and verify they fail**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py -k "execution_plan_execute" -q
```

Expected:

```text
FAILED ... AttributeError: '_OrderTools' object has no attribute 'execute_plan'
```

---

### Task 3: Implement `execution_plan_execute` Built-In Tool

**Files:**
- Modify: `lumibot/components/agents/builtins.py`

- [ ] **Step 1: Add the model-facing description**

Insert after `ORDERS_EXECUTE_ORDER_DESCRIPTION`:

```python
EXECUTION_PLAN_EXECUTE_DESCRIPTION = (
    "Execute one complete strict execution_plan in sequence order. "
    "This tool validates the plan, executes each order through readiness checks, submission, and confirmation, "
    "stops on the first blocker, and returns a complete execution report. "
    "This tool mutates trading state. It does not generate, repair, reorder, optimize, or modify the plan. "
    "Pass the execution_plan exactly as provided by the upstream planner. "
    "If plan_status is blocked or invalid, do not call lower-level tools; summarize where execution stopped and why."
)
```

- [ ] **Step 2: Add helper functions**

Insert after `_bind_execute_order()` and before `_bind_submit_order()`:

```python
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
    return {
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


def _is_positive_whole_share_quantity(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    if not math.isfinite(float(value)):
        return False
    return float(value) > 0 and float(value).is_integer()


def _normalized_execution_plan_order(raw_order: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(raw_order, dict):
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            "Each execution_plan order must be an object.",
        )

    sequence = raw_order.get("sequence")
    symbol = raw_order.get("symbol")
    action = raw_order.get("action")
    side = raw_order.get("side")
    quantity = raw_order.get("quantity")
    quantity_mode = raw_order.get("quantity_mode", "shares")
    asset_type = raw_order.get("asset_type", "stock")
    order_type = raw_order.get("order_type", "market")
    time_in_force = raw_order.get("time_in_force", "day")

    if action != "submit_order":
        return None, _execution_plan_blocker(
            "INVALID_ORDER_ACTION",
            "execution_plan orders must use action='submit_order'.",
            sequence=sequence,
            symbol=symbol,
        )
    if not isinstance(symbol, str) or not symbol.strip():
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            "execution_plan order symbol must be a non-empty string.",
            sequence=sequence,
            symbol=symbol,
        )
    if side not in {"buy", "sell"}:
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            "execution_plan order side must be buy or sell.",
            sequence=sequence,
            symbol=symbol,
        )
    if quantity_mode != "shares":
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            "execution_plan order quantity_mode must be shares.",
            sequence=sequence,
            symbol=symbol,
        )
    if not _is_positive_whole_share_quantity(quantity):
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            "execution_plan order quantity must be a positive whole-share number.",
            sequence=sequence,
            symbol=symbol,
        )
    if asset_type not in {"stock", "us_equity"}:
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            "execution_plan order asset_type must be stock or us_equity.",
            sequence=sequence,
            symbol=symbol,
        )
    if order_type != "market":
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            "execution_plan_execute supports market orders only.",
            sequence=sequence,
            symbol=symbol,
        )
    if time_in_force != "day":
        return None, _execution_plan_blocker(
            "INVALID_ORDER_FIELDS",
            "execution_plan_execute supports day time_in_force only.",
            sequence=sequence,
            symbol=symbol,
        )

    return {
        "sequence": sequence,
        "action": "submit_order",
        "symbol": symbol.strip().upper(),
        "side": side,
        "quantity_mode": "shares",
        "quantity": int(quantity),
        "asset_type": asset_type,
        "order_type": "market",
        "time_in_force": "day",
    }, None
```

- [ ] **Step 3: Add plan validation helper**

Continue below the helper functions:

```python
def _validate_execution_plan_for_execute(
    execution_plan: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(execution_plan, dict) or not execution_plan:
        return None, _execution_plan_invalid_payload(
            _execution_plan_blocker(
                "MISSING_EXECUTION_PLAN",
                "execution_plan must be provided as a non-empty object.",
            )
        )

    schema_version = execution_plan.get("schema_version", 1)
    intent = execution_plan.get("intent")
    raw_orders = execution_plan.get("orders")
    if schema_version != 1:
        return None, _execution_plan_invalid_payload(
            _execution_plan_blocker(
                "UNSUPPORTED_PLAN_SCHEMA_VERSION",
                "execution_plan schema_version must be 1.",
            ),
            intent=intent,
        )
    if intent not in {"rebalance", "hold"}:
        return None, _execution_plan_invalid_payload(
            _execution_plan_blocker(
                "UNSUPPORTED_PLAN_INTENT",
                "execution_plan intent must be rebalance or hold.",
            ),
            intent=intent,
        )
    if not isinstance(raw_orders, list):
        return None, _execution_plan_invalid_payload(
            _execution_plan_blocker(
                "INVALID_EXECUTION_PLAN",
                "execution_plan.orders must be a list.",
            ),
            intent=intent,
        )
    if intent == "hold" and raw_orders:
        return None, _execution_plan_invalid_payload(
            _execution_plan_blocker(
                "INVALID_EXECUTION_PLAN",
                "execution_plan with intent='hold' must not contain orders.",
            ),
            intent=intent,
            orders_requested=len(raw_orders),
        )

    normalized_orders: list[dict[str, Any]] = []
    sequences: list[Any] = []
    for raw_order in raw_orders:
        normalized, blocker = _normalized_execution_plan_order(raw_order)
        if blocker is not None:
            return None, _execution_plan_invalid_payload(
                blocker,
                intent=intent,
                orders_requested=len(raw_orders),
            )
        assert normalized is not None
        normalized_orders.append(normalized)
        sequences.append(normalized["sequence"])

    if any(isinstance(sequence, bool) or not isinstance(sequence, int) for sequence in sequences):
        return None, _execution_plan_invalid_payload(
            _execution_plan_blocker(
                "INVALID_ORDER_SEQUENCE",
                "execution_plan sequence values must be positive integers.",
            ),
            intent=intent,
            orders_requested=len(raw_orders),
        )
    if len(set(sequences)) != len(sequences):
        return None, _execution_plan_invalid_payload(
            _execution_plan_blocker(
                "INVALID_ORDER_SEQUENCE",
                "execution_plan orders must have unique sequence values.",
            ),
            intent=intent,
            orders_requested=len(raw_orders),
        )
    expected_sequences = list(range(1, len(sequences) + 1))
    if sorted(sequences) != expected_sequences:
        return None, _execution_plan_invalid_payload(
            _execution_plan_blocker(
                "INVALID_ORDER_SEQUENCE",
                "execution_plan sequence values must exactly equal 1..N.",
            ),
            intent=intent,
            orders_requested=len(raw_orders),
        )
    if sequences != expected_sequences:
        return None, _execution_plan_invalid_payload(
            _execution_plan_blocker(
                "INVALID_ORDER_SEQUENCE",
                "execution_plan.orders must already be listed in ascending sequence order.",
            ),
            intent=intent,
            orders_requested=len(raw_orders),
        )

    return {
        "schema_version": 1,
        "intent": intent,
        "orders": normalized_orders,
    }, None
```

- [ ] **Step 4: Add report helpers**

```python
def _execution_plan_order_summary(order_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "sequence": order_result.get("sequence"),
        "symbol": order_result.get("symbol"),
        "side": order_result.get("side"),
        "quantity": order_result.get("quantity"),
        "execution_status": order_result.get("execution_status"),
        "can_continue": order_result.get("can_continue"),
        "confirmed": (
            order_result.get("submit_and_confirm_result", {}).get("confirmed")
            if isinstance(order_result.get("submit_and_confirm_result"), dict)
            else None
        ),
        "order_identifier": _orders_execute_order_id(order_result),
        "blockers": list(order_result.get("blockers") or []),
        "warnings": list(order_result.get("warnings") or []),
    }


def _execution_plan_skipped_order(order: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "sequence": order.get("sequence"),
        "symbol": order.get("symbol"),
        "side": order.get("side"),
        "quantity": order.get("quantity"),
        "skip_reason": reason,
    }


def _last_account_snapshot_from_order_results(order_results: list[dict[str, Any]]) -> Any:
    for result in reversed(order_results):
        if isinstance(result, dict) and result.get("account_after") is not None:
            return result.get("account_after")
    return None
```

- [ ] **Step 5: Add binder**

```python
def _bind_execute_plan(strategy: Any, manager: Any) -> BoundTool:
    execute_order_tool = _bind_execute_order(strategy, manager)

    def execution_plan_execute(*, execution_plan: dict[str, Any]) -> dict[str, Any]:
        normalized_plan, invalid_payload = _validate_execution_plan_for_execute(execution_plan)
        if invalid_payload is not None:
            return invalid_payload
        assert normalized_plan is not None

        intent = normalized_plan["intent"]
        orders = list(normalized_plan.get("orders") or [])
        if intent == "hold" or not orders:
            return {
                "schema_version": 1,
                "plan_status": "completed",
                "can_continue": True,
                "intent": intent,
                "orders_requested": len(orders),
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
                "blockers": [],
                "warnings": [],
                "summary": "Execution plan completed with no orders to submit.",
            }

        order_results: list[dict[str, Any]] = []
        completed_orders: list[dict[str, Any]] = []
        blocked_orders: list[dict[str, Any]] = []
        skipped_orders: list[dict[str, Any]] = []
        plan_warnings: list[str] = []

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
                result = _execute_order_blocked_payload(
                    sequence=order.get("sequence"),
                    symbol=order.get("symbol"),
                    side=order.get("side"),
                    quantity=order.get("quantity"),
                    asset_type=order.get("asset_type", "stock"),
                    order_type=order.get("order_type", "market"),
                    time_in_force=order.get("time_in_force", "day"),
                    blockers=[
                        _execution_plan_blocker(
                            "EXECUTION_EXCEPTION",
                            f"Internal order execution raised: {exc}",
                            sequence=order.get("sequence"),
                            symbol=order.get("symbol"),
                        )
                    ],
                )
            order_results.append(result)
            plan_warnings.extend(str(warning) for warning in result.get("warnings") or [])
            summary = _execution_plan_order_summary(result)
            if result.get("can_continue") is True and result.get("execution_status") == "completed":
                completed_orders.append(summary)
                continue

            blocked_orders.append(summary)
            skipped_orders = [
                _execution_plan_skipped_order(
                    skipped,
                    f"stopped_after_sequence_{order.get('sequence')}_blocked",
                )
                for skipped in orders[index + 1 :]
            ]
            return {
                "schema_version": 1,
                "plan_status": "blocked",
                "can_continue": False,
                "intent": intent,
                "orders_requested": len(orders),
                "orders_attempted": len(order_results),
                "orders_completed": len(completed_orders),
                "orders_blocked": len(blocked_orders),
                "orders_skipped": len(skipped_orders),
                "completed_orders": completed_orders,
                "blocked_orders": blocked_orders,
                "skipped_orders": skipped_orders,
                "order_results": order_results,
                "initial_account_snapshot": None,
                "final_account_snapshot": _last_account_snapshot_from_order_results(order_results),
                "blockers": [
                    _execution_plan_blocker(
                        "ORDER_BLOCKED",
                        f"Execution stopped at sequence {order.get('sequence')}.",
                        sequence=order.get("sequence"),
                        symbol=order.get("symbol"),
                    )
                ],
                "warnings": plan_warnings,
                "summary": f"Execution stopped at sequence {order.get('sequence')}.",
            }

        return {
            "schema_version": 1,
            "plan_status": "completed",
            "can_continue": True,
            "intent": intent,
            "orders_requested": len(orders),
            "orders_attempted": len(order_results),
            "orders_completed": len(completed_orders),
            "orders_blocked": 0,
            "orders_skipped": 0,
            "completed_orders": completed_orders,
            "blocked_orders": [],
            "skipped_orders": [],
            "order_results": order_results,
            "initial_account_snapshot": None,
            "final_account_snapshot": _last_account_snapshot_from_order_results(order_results),
            "blockers": [],
            "warnings": plan_warnings,
            "summary": f"All {len(completed_orders)} planned orders were completed and confirmed.",
        }

    return BoundTool(
        name="execution_plan_execute",
        description=EXECUTION_PLAN_EXECUTE_DESCRIPTION,
        function=execution_plan_execute,
        metadata={"kind": "builtin", "replay_on_cache": True, "mutates_trading": True},
    )
```

- [ ] **Step 6: Add `_OrderTools.execute_plan()`**

Insert after `_OrderTools.execute()`:

```python
    def execute_plan(self) -> ToolDefinition:
        return ToolDefinition(
            name="execution_plan_execute",
            description=EXECUTION_PLAN_EXECUTE_DESCRIPTION,
            binder=_bind_execute_plan,
            metadata={"mutates_trading": True},
        )
```

- [ ] **Step 7: Add the tool to `BuiltinTools.all()`**

Insert after `self.orders.execute(),`:

```python
            self.orders.execute_plan(),
```

- [ ] **Step 8: Run focused tool tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py -k "execution_plan_execute" -q
```

Expected:

```text
... passed
```

If an existing Stage C helper name differs, adapt the helper call while preserving the same output fields and rerun the same command.

- [ ] **Step 9: Commit tool implementation**

```powershell
git add lumibot\components\agents\builtins.py tests\test_agent_tool_permissions.py
git commit -m "feat: add execution plan execute tool"
```

---

### Task 4: Add AgentManager Execution Policy And Retry Safety

**Files:**
- Modify: `lumibot/components/agents/manager.py`
- Modify: `tests/test_agent_manager.py`
- Modify: `tests/test_agent_runtime_provider_keys.py`

- [ ] **Step 1: Add AgentManager prompt test**

Insert after `test_execution_agent_with_execute_tool_receives_stage_c_execution_policy()`:

```python
def test_execution_agent_with_execute_plan_tool_receives_stage_d_execution_policy():
    def execution_plan_execute():
        return None

    handle = AgentHandle(
        manager=DummyManager(),
        name="execution_agent",
        system_prompt="Execution role.",
        default_model="test-model",
        runtime=object(),
        tools=[execution_plan_execute],
        include_builtin_tools=False,
        base_system_prompt_mode="execution_minimal",
    )

    prompt = handle._compose_system_prompt(
        {"mode": "backtesting"},
        bound_tools=handle._ensure_bound_tools(),
    )
    prompt_lower = prompt.lower()

    assert "execution tool policy" in prompt_lower
    assert "execution_plan_execute executes one complete strict execution_plan" in prompt_lower
    assert "stops on the first blocker" in prompt_lower
    assert "does not research, generate, repair, reorder, optimize, or modify the plan" in prompt_lower
    assert "orders_execute_order executes one explicit" not in prompt_lower
    assert "price/history tool policy" not in prompt_lower
```

- [ ] **Step 2: Add warning evidence test**

Insert near `test_execution_minimal_mode_skips_memory_thesis_warning_for_position_orders()`:

```python
def test_execution_plan_execute_counts_as_order_and_data_evidence_for_warnings():
    result = AgentRunResult(
        summary="Executed plan.",
        model="test-model",
        events=[
            AgentTraceEvent(
                kind="tool_call",
                tool_name="execution_plan_execute",
                payload={"execution_plan": {"schema_version": 1, "intent": "hold", "orders": []}},
            ),
            AgentTraceEvent(
                kind="tool_result",
                tool_name="execution_plan_execute",
                payload={"plan_status": "completed", "can_continue": True},
            ),
        ],
    )
    handle = AgentHandle(
        manager=DummyManager(),
        name="execution_agent",
        system_prompt="Execution prompt.",
        default_model="test-model",
        runtime=object(),
        tools=[],
        include_builtin_tools=False,
        base_system_prompt_mode="execution_minimal",
    )

    warnings = handle._derive_warnings(
        result,
        {"mode": "backtesting", "current_datetime": "2024-09-05T09:30:00-04:00"},
        bound_tools=[],
    )

    assert not [warning for warning in warnings if warning["kind"] == "order_without_data"]
```

- [ ] **Step 3: Add retry-safety test**

In `tests/test_agent_runtime_provider_keys.py`, add a test next to existing mutating-tool retry tests:

```python
def test_execution_plan_execute_metadata_disables_whole_run_retries(monkeypatch):
    from lumibot.components.agents.runtime import GoogleADKRuntime, RuntimeRequest
    from lumibot.components.agents.schemas import BoundTool

    monkeypatch.setenv("LUMIBOT_AGENT_MAX_RUN_ATTEMPTS", "3")
    request = RuntimeRequest(
        agent_name="execution_agent",
        model="openai/gpt-5.6-luna",
        system_prompt="Execution.",
        task_prompt="Execute.",
        context={},
        bound_tools=[
            BoundTool(
                name="execution_plan_execute",
                description="Execute one complete strict execution_plan.",
                function=lambda **_: {},
                metadata={"mutates_trading": True},
            )
        ],
    )

    assert GoogleADKRuntime._max_attempts_for_request(request) == 1
```

- [ ] **Step 4: Run the new tests and verify they fail**

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py::test_execution_agent_with_execute_plan_tool_receives_stage_d_execution_policy tests\test_agent_manager.py::test_execution_plan_execute_counts_as_order_and_data_evidence_for_warnings tests\test_agent_runtime_provider_keys.py::test_execution_plan_execute_metadata_disables_whole_run_retries -q
```

Expected:

```text
FAILED ... execution_plan_execute policy text missing
```

- [ ] **Step 5: Update `_execution_tool_policy_prompt()`**

In `lumibot/components/agents/manager.py`, add `execution_plan_execute` to `execution_tools`:

```python
            "execution_plan_execute",
```

Then add before the Stage C `orders_execute_order` block:

```python
        if "execution_plan_execute" in tool_names:
            lines.append(
                "execution_plan_execute executes one complete strict execution_plan in sequence order. "
                "It validates the plan, executes each order through readiness checks, submission, and confirmation, "
                "and stops on the first blocker. It does not research, generate, repair, reorder, optimize, "
                "or modify the plan."
            )
```

- [ ] **Step 6: Update `_derive_warnings()` evidence detection**

In `_derive_warnings()`, update `used_data_tool`:

```python
            or name == "execution_plan_execute"
```

Update `used_order_tool`:

```python
        used_order_tool = any(name.startswith("orders_") or name == "execution_plan_execute" for name in tool_names)
```

- [ ] **Step 7: Run focused manager/runtime tests**

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py::test_execution_agent_with_execute_plan_tool_receives_stage_d_execution_policy tests\test_agent_manager.py::test_execution_plan_execute_counts_as_order_and_data_evidence_for_warnings tests\test_agent_runtime_provider_keys.py::test_execution_plan_execute_metadata_disables_whole_run_retries -q
```

Expected:

```text
3 passed
```

- [ ] **Step 8: Commit manager and retry safety changes**

```powershell
git add lumibot\components\agents\manager.py tests\test_agent_manager.py tests\test_agent_runtime_provider_keys.py
git commit -m "feat: add execution plan policy and retry safety"
```

---

### Task 5: Add Agent Replay Formatter

**Files:**
- Modify: `lumibot/components/agents/replay_ui/formatters.py`
- Modify: `tests/test_agent_replay_ui_formatters.py`

- [ ] **Step 1: Add formatter tests**

Insert after the existing `orders_execute_order` formatter tests:

```python
def test_execution_plan_execute_formatter_explains_completed_plan():
    text = explain_tool_result(
        "execution_plan_execute",
        {"execution_plan": {"schema_version": 1, "intent": "rebalance", "orders": []}},
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
            "final_account_snapshot": {"cash": 630.15, "portfolio_value": 100500.0},
        },
        None,
    )

    assert "Execution plan completed" in text
    assert "2 requested" in text
    assert "2 completed" in text
    assert "sell VGIT 406" in text
    assert "buy GLD 107" in text
    assert "Final cash: 630.15" in text


def test_execution_plan_execute_formatter_explains_hold_plan():
    text = explain_tool_result(
        "execution_plan_execute",
        {"execution_plan": {"schema_version": 1, "intent": "hold", "orders": []}},
        {
            "plan_status": "completed",
            "intent": "hold",
            "orders_requested": 0,
            "orders_attempted": 0,
            "orders_completed": 0,
            "orders_blocked": 0,
            "orders_skipped": 0,
        },
        None,
    )

    assert "Execution plan completed" in text
    assert "0 requested" in text
    assert "no orders" in text.lower()


def test_execution_plan_execute_formatter_explains_invalid_plan():
    text = explain_tool_result(
        "execution_plan_execute",
        {"execution_plan": {}},
        {
            "plan_status": "invalid",
            "can_continue": False,
            "blockers": [
                {
                    "code": "INVALID_ORDER_SEQUENCE",
                    "message": "execution_plan.orders must already be listed in ascending sequence order.",
                }
            ],
        },
        None,
    )

    assert "Execution plan invalid" in text
    assert "INVALID_ORDER_SEQUENCE" in text
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
    assert "1 skipped" in text
```

- [ ] **Step 2: Run formatter tests and verify they fail**

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_formatters.py -k "execution_plan_execute" -q
```

Expected:

```text
FAILED ... generic formatter text
```

- [ ] **Step 3: Add formatter implementation**

In `formatters.py`, insert after `_orders_execute_order()`:

```python
def _execution_plan_order_phrase(order: dict[str, Any]) -> str:
    sequence = _first_present(order, "sequence")
    symbol = _first_present(order, "symbol")
    side = _first_present(order, "side")
    quantity = _first_present(order, "quantity", "qty", "shares")
    prefix = "" if sequence is None else f"{_text(sequence)} "
    return f"{prefix}{_text(side)} {_text(symbol)} {_text(quantity)}"


def _execution_plan_execute(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    status = _first_present(result, "plan_status", "status")
    requested = _first_present(result, "orders_requested") or 0
    attempted = _first_present(result, "orders_attempted") or 0
    completed = _first_present(result, "orders_completed") or 0
    blocked = _first_present(result, "orders_blocked") or 0
    skipped = _first_present(result, "orders_skipped") or 0
    counts = (
        f"{_text(requested)} requested, {_text(attempted)} attempted, "
        f"{_text(completed)} completed, {_text(blocked)} blocked, {_text(skipped)} skipped."
    )

    if status == "invalid":
        blocker_text = _blocker_text(result)
        return f"Execution plan invalid before submission: {counts} No orders were submitted.{blocker_text}"

    if status == "blocked":
        blocked_orders = result.get("blocked_orders") if isinstance(result.get("blocked_orders"), list) else []
        if blocked_orders:
            first_blocked = _as_dict(blocked_orders[0])
            sequence = _first_present(first_blocked, "sequence")
            order_text = _execution_plan_order_phrase(first_blocked)
            blocker_text = _blocker_text(first_blocked, result)
            return (
                f"Execution plan blocked at sequence {_text(sequence)}: {order_text}. "
                f"{counts}{blocker_text}"
            )
        return f"Execution plan blocked: {counts}{_blocker_text(result)}"

    completed_orders = result.get("completed_orders") if isinstance(result.get("completed_orders"), list) else []
    order_phrases = [_execution_plan_order_phrase(_as_dict(order)) for order in completed_orders[:6]]
    order_text = ""
    if order_phrases:
        order_text = " Completed orders: " + ", ".join(order_phrases) + "."
    elif int(requested or 0) == 0:
        order_text = " No orders were submitted."
    account = _nested_dict(result, "final_account_snapshot")
    cash = _first_present(account, "cash")
    cash_text = "" if cash is None else f" Final cash: {_text(cash)}."
    return f"Execution plan completed: {counts}{order_text}{cash_text}"
```

Register in `_FORMATTERS`:

```python
    "execution_plan_execute": _execution_plan_execute,
```

- [ ] **Step 4: Run formatter tests**

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_formatters.py -k "execution_plan_execute or orders_execute_order" -q
```

Expected:

```text
... passed
```

- [ ] **Step 5: Commit formatter changes**

```powershell
git add lumibot\components\agents\replay_ui\formatters.py tests\test_agent_replay_ui_formatters.py
git commit -m "feat: format execution plan tool results"
```

---

### Task 6: Switch Mock Quadrant Execution Agent To Stage D

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Update tool-surface test expectations**

In `test_agents_receive_distinct_tool_surfaces()`, change:

```python
    assert created_tool_names(created["execution_agent"]) == {"orders_execute_order"}
```

to:

```python
    assert created_tool_names(created["execution_agent"]) == {"execution_plan_execute"}
```

Extend non-execution assertions:

```python
        assert "execution_plan_execute" not in created_tool_names(non_execution_agent)
```

- [ ] **Step 2: Update prompt-boundary test expectations**

In `test_prompt_boundaries_are_short_and_role_specific()`, add forbidden Stage C phrases:

```python
        "orders_execute_order",
        "for each order in ascending sequence order",
        "exact order fields",
        "continue only when can_continue=true",
```

Replace required execution assertions with:

```python
    assert "execute only provided execution_plan" in serialized
    assert "call execution_plan_execute exactly once with the complete execution_plan" in serialized
    assert "do not manually execute individual orders" in serialized
    assert "do not call lower-level order, account, open-order, or price tools" in serialized
    assert "do not research, change fields, reorder orders, split orders, or repair the plan" in serialized
```

- [ ] **Step 3: Update trading-iteration task-prompt test**

Where the test checks `execution_task_prompt`, replace Stage C required phrases with:

```python
    for required_phrase in (
        "Execute the provided execution_plan by calling execution_plan_execute exactly once",
        "complete execution_plan",
        "Summarize the returned plan report",
        "Do not call per-order tools",
    ):
        assert required_phrase in execution_task_prompt
```

Keep forbidden lower-level assertions and add:

```python
    assert "orders_execute_order" not in execution_task_prompt
```

- [ ] **Step 4: Run strategy tests and verify they fail**

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -k "tool_surfaces or prompt_boundaries or trading_iteration" -q
```

Expected:

```text
FAILED ... orders_execute_order still visible
```

- [ ] **Step 5: Update execution-agent creation**

In `ai_trading_team_mock_growth_inflation_quadrant.py`, change:

```python
            tools=[BuiltinTools.orders.execute()],
```

to:

```python
            tools=[BuiltinTools.orders.execute_plan()],
```

Replace the execution-agent `system_prompt` with:

```python
            system_prompt=(
                "Execution role: execute only provided execution_plan. "
                "Call execution_plan_execute exactly once with the complete execution_plan. "
                "Do not manually execute individual orders. Do not call lower-level order, account, "
                "open-order, or price tools when execution_plan_execute is available. "
                "Do not research, change fields, reorder orders, split orders, or repair the plan. "
                "If the tool returns plan_status=completed, summarize completed orders. "
                "If it returns plan_status=blocked or invalid, summarize where execution stopped and why."
            ),
```

- [ ] **Step 6: Update execution-agent run task prompt**

Replace:

```python
            task_prompt=(
                "Execute the provided execution_plan in sequence order. For each order, call orders_execute_order "
                "exactly once with the exact order fields. Stop if any orders_execute_order result returns "
                "can_continue=false."
            ),
```

with:

```python
            task_prompt=(
                "Execute the provided execution_plan by calling execution_plan_execute exactly once with the "
                "complete execution_plan. Summarize the returned plan report. Do not call per-order tools."
            ),
```

- [ ] **Step 7: Run strategy tests**

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected:

```text
... passed
```

- [ ] **Step 8: Commit strategy prompt/tool-surface changes**

```powershell
git add lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: switch mock quadrant execution to plan tool"
```

---

### Task 7: Focused Regression And Lint

**Files:**
- No source edits unless failures reveal a bug.

- [ ] **Step 1: Run focused pytest suite**

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py tests\test_agent_manager.py tests\test_agent_runtime_provider_keys.py tests\test_agent_replay_ui_formatters.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected:

```text
passed
```

- [ ] **Step 2: Run focused ruff check**

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\builtins.py lumibot\components\agents\manager.py lumibot\components\agents\replay_ui\formatters.py lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_tool_permissions.py tests\test_agent_manager.py tests\test_agent_runtime_provider_keys.py tests\test_agent_replay_ui_formatters.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 3: Fix only failures caused by Stage D edits**

If pytest or ruff fails, inspect the failing line, patch the smallest affected code, and rerun the exact failed command before proceeding.

- [ ] **Step 4: Commit fixes if any**

If Step 3 changed files:

```powershell
git add lumibot tests
git commit -m "fix: stabilize execution plan stage d tests"
```

If Step 3 changed no files, do not create an empty commit.

---

### Task 8: One-Day And Two-Day Benchmark Validation

**Files:**
- Create: `docs/superpowers/notes/2026-08-11-execution-plan-execute-stage-d-validation.md`

- [ ] **Step 1: Run one-day benchmark**

Use the current preferred cheaper model:

```powershell
$env:AI_TRADING_TEAM_MODEL = "openai/gpt-5.6-luna"
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-06 --max-workers 1 --max-run-attempts 1
```

Expected:

```text
{"status": "passed", ... "strategy": "mock-growth-inflation-quadrant", ...}
```

- [ ] **Step 2: Inspect one-day execution-agent trace**

Run this helper, replacing `<RUN_ID>` only if the newest artifact is not the run from Step 1:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -c "from pathlib import Path; from lumibot.components.agents.replay_ui.loader import build_replay_dataset; roots=sorted(Path('artifacts/ai_trading_team_example_benchmarks').glob('*/mock-growth-inflation-quadrant/cache/agent_runtime'), key=lambda p: p.stat().st_mtime, reverse=True); root=roots[0]; ds=build_replay_dataset(root); print(root); tool_calls=[]; [tool_calls.extend([call.tool_name for call in agent.tool_calls]) for run in ds.runs for system in run.system_runs for agent in system.agents if agent.name=='execution_agent']; print(tool_calls)"
```

Expected:

```text
['execution_plan_execute']
```

If the exact list contains more than one entry for a single system run, inspect whether multiple trading days were included. For a one-day run with orders, execution agent should make one model-facing `execution_plan_execute` call.

- [ ] **Step 3: Run two-day benchmark**

```powershell
$env:AI_TRADING_TEAM_MODEL = "openai/gpt-5.6-luna"
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-09 --max-workers 1 --max-run-attempts 1
```

Expected:

```text
{"status": "passed", ... "strategy": "mock-growth-inflation-quadrant", ...}
```

- [ ] **Step 4: Inspect two-day same-day rebalance evidence**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -c "from pathlib import Path; import pandas as pd; roots=sorted(Path('artifacts/ai_trading_team_example_benchmarks').glob('*/mock-growth-inflation-quadrant'), key=lambda p: p.stat().st_mtime, reverse=True); root=roots[0]; print(root); trades=list(root.glob('*trades*.csv')); print([str(p) for p in trades]); detail=root/'stats_agent_detail.parquet'; print('detail_exists', detail.exists()); df=pd.read_parquet(detail); print(df[df['agent_name'].eq('execution_agent')][['agent_name','tool_name','is_call_summary']].to_string(index=False))"
```

Expected:

```text
execution_agent rows include execution_plan_execute
```

Then inspect the trace in the Agent Replay UI and verify:

- execution agent visible tools contain only `execution_plan_execute`
- each trading day with orders has one model-facing execution call
- nested `order_results` show every planned order in sequence
- sell orders complete before later buy orders in the nested report
- final cash is non-negative
- no normal benchmark run is blocked by `NEGATIVE_CASH_NOT_ALLOWED`
- Account Curve and Performance Report buttons still work for the artifact if the files exist

- [ ] **Step 5: Create validation note with observed outputs**

Create `docs/superpowers/notes/2026-08-11-execution-plan-execute-stage-d-validation.md` only after Steps 1-4 have produced actual command outputs and artifact paths. The note must contain the observed outputs from this execution session. Include these sections:

```markdown
# Execution Plan Execute Stage D Validation

Date: 2026-08-11

## Focused Tests

- Command: `D:\Lumibot\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py tests\test_agent_manager.py tests\test_agent_runtime_provider_keys.py tests\test_agent_replay_ui_formatters.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py -q`
- Result: record the exact pytest pass/fail line and elapsed time.

## Ruff

- Command: `D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\builtins.py lumibot\components\agents\manager.py lumibot\components\agents\replay_ui\formatters.py lumibot\example_strategies\ai_trading_team_mock_growth_inflation_quadrant.py tests\test_agent_tool_permissions.py tests\test_agent_manager.py tests\test_agent_runtime_provider_keys.py tests\test_agent_replay_ui_formatters.py tests\test_ai_trading_team_mock_growth_inflation_quadrant.py`
- Result: record the exact ruff output.

## One-Day Benchmark

- Command: `$env:AI_TRADING_TEAM_MODEL = "openai/gpt-5.6-luna"; D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-06 --max-workers 1 --max-run-attempts 1`
- Artifact: record the actual artifact path.
- Result: record the JSON status line.
- Execution-agent model-facing tool calls: record the observed list.

## Two-Day Benchmark

- Command: `$env:AI_TRADING_TEAM_MODEL = "openai/gpt-5.6-luna"; D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-09 --max-workers 1 --max-run-attempts 1`
- Artifact: record the actual artifact path.
- Result: record the JSON status line.
- Execution-agent model-facing tool calls: record the observed list grouped by trading day.

## Acceptance Review

- `execution_plan_execute` exists: record yes/no with evidence.
- execution agent visible tools: record the observed set.
- nested order results visible: record yes/no with evidence.
- sell-before-buy confirmation observed: record yes/no with evidence.
- final cash non-negative: record yes/no with final cash.
- normal benchmark has no `NEGATIVE_CASH_NOT_ALLOWED` blocker: record yes/no.
- Agent Replay formatter readable: record yes/no.
- known limitations: record only limitations observed during this execution.
```

- [ ] **Step 6: Commit validation note**

```powershell
git add docs\superpowers\notes\2026-08-11-execution-plan-execute-stage-d-validation.md
git commit -m "docs: validate execution plan stage d"
```

---

### Task 9: Final Review Before Stage D Completion

**Files:**
- No source edits unless review finds a real issue.

- [ ] **Step 1: Run final status check**

```powershell
git status --short
git log --oneline -5
```

Expected:

```text
git status --short has no unintended source changes
recent commits include Stage D implementation and validation
```

- [ ] **Step 2: Review spec acceptance criteria against implementation**

Open:

```powershell
Get-Content docs\superpowers\specs\2026-08-11-execution-plan-execute-stage-d-design.md
Get-Content docs\superpowers\notes\2026-08-11-execution-plan-execute-stage-d-validation.md
```

Check each acceptance item manually:

- native tool exists
- strict plan input
- validation before submission
- invalid sequence rejected
- Stage C internal behavior preserved
- stops after first blocker
- skipped orders reported
- nested results visible
- strategy exposes only `execution_plan_execute`
- prompt no longer mentions Stage C flow
- focused tests pass
- one-day and two-day benchmarks pass

- [ ] **Step 3: Request code review**

Use `superpowers:requesting-code-review` before claiming completion. Ask reviewer to focus on:

- duplicated side effects risk
- plan validation edge cases
- whether `execution_plan_execute` is truly the only execution-agent visible tool
- whether replay formatter gives enough information without hiding nested JSON
- whether benchmark evidence proves same-day rebalance remains functional

- [ ] **Step 4: Address review findings**

For each real finding:

1. Write or update the failing test.
2. Patch the smallest code path.
3. Rerun the failing test.
4. Rerun the focused suite if the touched code affects shared behavior.
5. Commit the fix with a focused message.

- [ ] **Step 5: Stop for user decision**

After review and validation pass, do not merge automatically. Report:

- commits created
- tests run
- benchmark artifacts
- whether Stage D acceptance passed
- any residual risk

Ask whether to merge/sync the feature branch.

---

## Self-Review

Spec coverage:

- Tool definition and metadata: Task 1, Task 3
- Plan validation: Task 2, Task 3
- Sequence rejection instead of sorting: Task 2, Task 3
- Internal Stage C reuse: Task 3
- Stop on blocker and skip later orders: Task 2, Task 3
- Negative-cash preservation: Task 2, Task 3
- AgentManager policy: Task 4
- Retry safety: Task 4
- Replay UI formatting: Task 5
- Strategy tool surface and prompts: Task 6
- Focused tests and lint: Task 7
- One-day and two-day benchmark: Task 8
- Validation note: Task 8
- Final review: Task 9

No intentionally unresolved implementation markers are left in this plan. The validation note is created only after benchmark and test outputs exist, so execution workers must write actual observed values into it.
