# Orders Execute Order Stage C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Stage C `orders_execute_order`, a mutating execution tool that performs preflight, submit, and confirm for exactly one explicit `execution_plan.orders[]` order in one model-facing call.

**Architecture:** Reuse the existing Stage A `orders_preflight_check` behavior and Stage B `orders_submit_and_confirm_order` behavior inside one new built-in order tool. Then shrink the mock growth/inflation quadrant `execution_agent` tool surface to only `orders_execute_order`, update prompts and manager policy so the model stops using the Stage B two-call ceremony, and add replay UI formatting so the compressed tool remains inspectable.

**Tech Stack:** Python, LumiBot agent built-in tools, pytest, existing `AgentManager` prompt/tool policy, Agent Replay UI formatter helpers, `scripts/run_ai_trading_team_examples_benchmark.py`.

---

## File Structure

- Modify `lumibot/components/agents/builtins.py`
  - Add `ORDERS_EXECUTE_ORDER_DESCRIPTION`.
  - Add `_execute_order_blocked_payload`.
  - Add `_execute_order_payload`.
  - Add `_bind_execute_order`.
  - Add `BuiltinTools.orders.execute()`.
  - Register the tool in `BuiltinTools.all()`.
- Modify `lumibot/components/agents/manager.py`
  - Add `orders_execute_order` to execution-tool policy detection.
  - Add policy text explaining that it executes one explicit order end to end.
  - Ensure order/data warning logic treats `orders_execute_order` as a visible order/data execution tool.
- Modify `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
  - Replace Stage B execution-agent tools with `BuiltinTools.orders.execute()`.
  - Update execution-agent system prompt to the Stage C path.
  - Update execution-agent task prompt to the Stage C path.
- Modify `lumibot/components/agents/replay_ui/formatters.py`
  - Add `_orders_execute_order`.
  - Register it in `_FORMATTERS`.
- Modify `tests/test_agent_tool_permissions.py`
  - Add built-in definition tests.
  - Add metadata tests.
  - Add success path tests.
  - Add blocked preflight tests.
  - Add confirmation failure tests.
  - Add negative-cash regression coverage through the wrapper.
- Modify `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
  - Update execution-agent visible tool expectations.
  - Update prompt boundary tests.
  - Update `on_trading_iteration` handoff expectations.
- Modify `tests/test_agent_manager.py`
  - Add manager policy coverage for `orders_execute_order`.
  - Update warning/data-tool behavior if the current warning tests need the new tool name.
- Modify `tests/test_agent_replay_ui_formatters.py`
  - Add formatter tests for successful, preflight-blocked, and confirmation-blocked Stage C results.
- Create `docs/superpowers/notes/2026-08-11-orders-execute-order-stage-c-validation.md`
  - Record focused test commands.
  - Record one-day and two-day benchmark commands.
  - Record artifact paths and observations.

## Shared Commands

Use the main LumiBot virtual environment because this feature worktree does not have its own `.venv`:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_agent_tool_permissions.py -q
```

Use this worktree as the command working directory:

```powershell
cd C:\Users\Ran\.config\superpowers\worktrees\lumibot\mock-growth-inflation-quadrant-skeleton
```

---

## Task 1: Add Stage C Built-In Tool Definition Tests

**Files:**
- Modify: `tests/test_agent_tool_permissions.py`

- [ ] **Step 1: Add failing definition tests near the existing Stage A/B definition tests**

Insert these tests after `test_builtin_order_tools_expose_submit_and_confirm_definition`:

```python
def test_builtin_order_tools_expose_execute_order_definition():
    tool = BuiltinTools.orders.execute()

    assert tool.name == "orders_execute_order"
    assert "Execute exactly one explicit execution_plan order" in tool.description
    assert "readiness" in tool.description
    assert "confirms" in tool.description
    assert "execute a full plan" in tool.description
    assert callable(tool.binder)


def test_builtin_tools_all_includes_orders_execute_order():
    assert "orders_execute_order" in {tool.name for tool in BuiltinTools.all()}


def test_bound_execute_order_metadata_marks_mutating_and_replayable():
    strategy = _OrderReadinessStrategy()
    manager = AgentManager(strategy)

    definition = BuiltinTools.orders.execute()
    tool = definition.binder(strategy, manager)

    assert definition.metadata["mutates_trading"] is True
    assert tool.metadata["kind"] == "builtin"
    assert tool.metadata["replay_on_cache"] is True
    assert tool.metadata["mutates_trading"] is True
```

- [ ] **Step 2: Run the new tests and verify they fail for the missing API**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_agent_tool_permissions.py::test_builtin_order_tools_expose_execute_order_definition tests/test_agent_tool_permissions.py::test_builtin_tools_all_includes_orders_execute_order tests/test_agent_tool_permissions.py::test_bound_execute_order_metadata_marks_mutating_and_replayable -q
```

Expected: fail with an error like:

```text
AttributeError: '_OrderTools' object has no attribute 'execute'
```

- [ ] **Step 3: Commit the failing tests**

Run:

```powershell
git add tests/test_agent_tool_permissions.py
git commit -m "test: define orders execute order tool contract"
```

---

## Task 2: Add The `orders_execute_order` Built-In Tool Skeleton

**Files:**
- Modify: `lumibot/components/agents/builtins.py`
- Test: `tests/test_agent_tool_permissions.py`

- [ ] **Step 1: Add the model-facing description near the Stage A/B descriptions**

Insert after `ORDERS_SUBMIT_AND_CONFIRM_ORDER_DESCRIPTION`:

```python
ORDERS_EXECUTE_ORDER_DESCRIPTION = (
    "Execute exactly one explicit execution_plan order end to end. "
    "The tool checks readiness, submits the order if ready, confirms the submitted order, "
    "and returns whether execution can continue. This tool mutates trading state. "
    "It does not perform research, calculate quantities, change order fields, execute multiple orders, "
    "or execute a full plan. If can_continue=false, stop later orders and report the blocker."
)
```

- [ ] **Step 2: Add a minimal blocked payload helper before `_bind_execute_order`**

Insert this helper after `_bind_submit_and_confirm_order` and before `_bind_submit_order`:

```python
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
```

- [ ] **Step 3: Add a minimal `_bind_execute_order` skeleton**

Insert this binder after `_execute_order_blocked_payload`:

```python
def _bind_execute_order(strategy: Any, manager: Any) -> BoundTool:
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
        return _execute_order_blocked_payload(
            sequence=sequence,
            symbol=symbol,
            side=side,
            quantity=quantity,
            asset_type=asset_type,
            order_type=order_type,
            time_in_force=time_in_force,
            blockers=[
                {
                    "code": "ORDERS_EXECUTE_ORDER_NOT_IMPLEMENTED",
                    "message": "orders_execute_order skeleton is registered but execution logic is not implemented.",
                }
            ],
        )

    return BoundTool(
        name="orders_execute_order",
        description=ORDERS_EXECUTE_ORDER_DESCRIPTION,
        function=execute_order,
        metadata={"kind": "builtin", "replay_on_cache": True, "mutates_trading": True},
    )
```

- [ ] **Step 4: Add the order tool factory**

Insert this method in class `_OrderTools` after `submit_and_confirm`:

```python
    def execute(self) -> ToolDefinition:
        return ToolDefinition(
            name="orders_execute_order",
            description=ORDERS_EXECUTE_ORDER_DESCRIPTION,
            binder=_bind_execute_order,
            metadata={"mutates_trading": True},
        )
```

- [ ] **Step 5: Register the built-in in `BuiltinTools.all()`**

Add `self.orders.execute(),` after `self.orders.submit_and_confirm(),`:

```python
            self.orders.preflight(),
            self.orders.submit_and_confirm(),
            self.orders.execute(),
            self.orders.submit(),
```

- [ ] **Step 6: Run the definition tests and verify they pass**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_agent_tool_permissions.py::test_builtin_order_tools_expose_execute_order_definition tests/test_agent_tool_permissions.py::test_builtin_tools_all_includes_orders_execute_order tests/test_agent_tool_permissions.py::test_bound_execute_order_metadata_marks_mutating_and_replayable -q
```

Expected:

```text
3 passed
```

- [ ] **Step 7: Commit the skeleton implementation**

Run:

```powershell
git add lumibot/components/agents/builtins.py tests/test_agent_tool_permissions.py
git commit -m "feat: register orders execute order tool"
```

---

## Task 3: Add Success And Blocked Path Tests For `orders_execute_order`

**Files:**
- Modify: `tests/test_agent_tool_permissions.py`

- [ ] **Step 1: Add a wrapper helper for Stage C tools**

Insert after `_wrap_preflight_and_submit_confirm_tools`:

```python
def _wrap_execute_order_tools(strategy):
    return _wrap_builtin_tools(strategy, [BuiltinTools.orders.execute()])
```

- [ ] **Step 2: Add a success-path test**

Insert after the Stage B success-path test:

```python
def test_orders_execute_order_preflights_submits_and_confirms_one_order():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 1000.0
    strategy.portfolio_value = 1200.0
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_execute_order_tools(strategy)

    result = tool_map["orders_execute_order"](
        sequence=1,
        symbol="SPY",
        quantity=3,
        side="buy",
        asset_type="stock",
        order_type="market",
        time_in_force="day",
    )

    assert result["execution_status"] == "completed"
    assert result["can_continue"] is True
    assert result["blockers"] == []
    assert result["preflight_result"]["readiness"] == "ready"
    assert result["preflight_result"]["can_submit"] is True
    assert result["submit_and_confirm_result"]["submitted"] is True
    assert result["submit_and_confirm_result"]["confirmed"] is True
    assert result["submit_and_confirm_result"]["can_continue"] is True
    assert [step["step"] for step in result["internal_steps"]] == ["preflight", "submit_and_confirm"]
    assert strategy.submitted_orders
    assert strategy.submitted_orders[0].asset.symbol == "SPY"
```

- [ ] **Step 3: Add a blocked preflight test**

Insert after the success-path test:

```python
def test_orders_execute_order_blocks_before_submit_when_preflight_blocks():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 250.0
    strategy.last_prices = {"SPY": 100.0}
    tool_map = _wrap_execute_order_tools(strategy)

    result = tool_map["orders_execute_order"](symbol="SPY", quantity=3, side="buy")

    assert result["execution_status"] == "blocked"
    assert result["can_continue"] is False
    assert "INSUFFICIENT_CASH_ESTIMATE" in {blocker["code"] for blocker in result["blockers"]}
    assert result["preflight_result"]["readiness"] == "blocked"
    assert result["submit_and_confirm_result"] is None
    assert [step["step"] for step in result["internal_steps"]] == ["preflight"]
    assert strategy.submitted_orders == []
```

- [ ] **Step 4: Add a confirmation failure test**

Insert after the blocked preflight test:

```python
def test_orders_execute_order_returns_blocker_when_confirmation_fails():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 1000.0
    strategy.last_prices = {"SPY": 100.0}
    strategy.submitted_order_status = "new"
    tool_map = _wrap_execute_order_tools(strategy)

    result = tool_map["orders_execute_order"](
        symbol="SPY",
        quantity=3,
        side="buy",
        confirmation_max_attempts=1,
        confirmation_wait_seconds=0,
    )

    assert result["execution_status"] == "blocked"
    assert result["can_continue"] is False
    assert result["preflight_result"]["readiness"] == "ready"
    assert result["submit_and_confirm_result"]["submitted"] is False
    assert "CONFIRMATION_FAILED" in {blocker["code"] for blocker in result["blockers"]}
    assert [step["step"] for step in result["internal_steps"]] == ["preflight", "submit_and_confirm"]
```

- [ ] **Step 5: Add a negative-cash regression test through Stage C**

Insert after the confirmation failure test:

```python
def test_orders_execute_order_preserves_negative_cash_guard():
    strategy = _OrderReadinessStrategy()
    strategy.cash = 1000.0
    strategy.last_prices = {"SPY": 100.0}
    strategy.force_negative_cash_after_submit = True
    tool_map = _wrap_execute_order_tools(strategy)

    result = tool_map["orders_execute_order"](symbol="SPY", quantity=3, side="buy")

    assert result["execution_status"] == "blocked"
    assert result["can_continue"] is False
    assert "NEGATIVE_CASH_NOT_ALLOWED" in {blocker["code"] for blocker in result["blockers"]}
```

- [ ] **Step 6: Run the new behavior tests and verify they fail against the skeleton**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_agent_tool_permissions.py::test_orders_execute_order_preflights_submits_and_confirms_one_order tests/test_agent_tool_permissions.py::test_orders_execute_order_blocks_before_submit_when_preflight_blocks tests/test_agent_tool_permissions.py::test_orders_execute_order_returns_blocker_when_confirmation_fails tests/test_agent_tool_permissions.py::test_orders_execute_order_preserves_negative_cash_guard -q
```

Expected: fail because the skeleton always returns `ORDERS_EXECUTE_ORDER_NOT_IMPLEMENTED`.

- [ ] **Step 7: Commit the failing behavior tests**

Run:

```powershell
git add tests/test_agent_tool_permissions.py
git commit -m "test: cover orders execute order behavior"
```

---

## Task 4: Implement `orders_execute_order` By Reusing Stage A And Stage B

**Files:**
- Modify: `lumibot/components/agents/builtins.py`
- Test: `tests/test_agent_tool_permissions.py`

- [ ] **Step 1: Add a success payload helper after `_execute_order_blocked_payload`**

Insert:

```python
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
        "account_after": (
            submit_and_confirm_result.get("account_after")
            if isinstance(submit_and_confirm_result, dict)
            else None
        ),
    }
```

- [ ] **Step 2: Replace `_bind_execute_order` body with the real wrapper**

Replace the current skeleton `_bind_execute_order` with:

```python
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
            warnings = list(preflight_result.get("warnings") or []) if isinstance(preflight_result, dict) else []
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
        warnings = (
            list(submit_and_confirm_result.get("warnings") or [])
            if isinstance(submit_and_confirm_result, dict)
            else []
        )
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
```

- [ ] **Step 3: Run the Stage C tool tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_agent_tool_permissions.py::test_orders_execute_order_preflights_submits_and_confirms_one_order tests/test_agent_tool_permissions.py::test_orders_execute_order_blocks_before_submit_when_preflight_blocks tests/test_agent_tool_permissions.py::test_orders_execute_order_returns_blocker_when_confirmation_fails tests/test_agent_tool_permissions.py::test_orders_execute_order_preserves_negative_cash_guard -q
```

Expected:

```text
4 passed
```

- [ ] **Step 4: Run the Stage A/B/C focused tool permission tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_agent_tool_permissions.py -q
```

Expected: all tests in `tests/test_agent_tool_permissions.py` pass.

- [ ] **Step 5: Commit the wrapper implementation**

Run:

```powershell
git add lumibot/components/agents/builtins.py tests/test_agent_tool_permissions.py
git commit -m "feat: execute one order through preflight submit confirm"
```

---

## Task 5: Update AgentManager Execution Policy For Stage C

**Files:**
- Modify: `lumibot/components/agents/manager.py`
- Modify: `tests/test_agent_manager.py`

- [ ] **Step 1: Add a failing manager policy test**

Insert after `test_execution_agent_with_submit_and_confirm_tool_receives_execution_policy`:

```python
def test_execution_agent_with_execute_order_tool_receives_execution_policy():
    def orders_execute_order():
        return {"can_continue": True}

    strategy = _Strategy()
    manager = AgentManager(strategy)
    agent = manager.create(
        name="execution_agent",
        model="test-model",
        allow_trading=True,
        include_builtin_tools=False,
        tools=[orders_execute_order],
        system_prompt="Execute the provided plan.",
    )

    prompt_lower = agent._compose_system_prompt({"mode": "backtesting"}, agent._ensure_bound_tools()).lower()

    assert "execution tool policy" in prompt_lower
    assert "orders_execute_order executes one explicit execution_plan order end to end" in prompt_lower
    assert "does not research" in prompt_lower
    assert "execute multiple orders" in prompt_lower
```

- [ ] **Step 2: Run the manager policy test and verify it fails**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_agent_manager.py::test_execution_agent_with_execute_order_tool_receives_execution_policy -q
```

Expected: fail because `orders_execute_order` is not included in the execution policy set.

- [ ] **Step 3: Update `_execution_tool_policy_prompt`**

In `lumibot/components/agents/manager.py`, add `orders_execute_order` to `execution_tools`:

```python
        execution_tools = {
            "orders_submit_order",
            "orders_submit_and_confirm_order",
            "orders_execute_order",
            "orders_cancel_order",
            "orders_modify_order",
            "orders_open_orders",
        }
```

Add this branch after the Stage B branch:

```python
        if "orders_execute_order" in tool_names:
            lines.append(
                "orders_execute_order executes one explicit execution_plan order end to end: readiness check, "
                "submission, and confirmation. It does not research, calculate quantities, change order fields, "
                "execute multiple orders, or execute a full plan."
            )
```

- [ ] **Step 4: Update visible data warning logic**

In `_build_warnings`, update `used_data_tool` so `orders_execute_order` counts as visible order/data execution evidence:

```python
            or name in {"orders_preflight_check", "orders_execute_order"}
```

This prevents a pure Stage C execution run from being incorrectly warned as an order tool with no visible data path.

- [ ] **Step 5: Run the manager tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_agent_manager.py::test_execution_agent_with_execute_order_tool_receives_execution_policy tests/test_agent_manager.py::test_orders_preflight_counts_as_visible_order_data_for_warnings -q
```

Expected:

```text
2 passed
```

- [ ] **Step 6: Commit manager policy changes**

Run:

```powershell
git add lumibot/components/agents/manager.py tests/test_agent_manager.py
git commit -m "feat: teach agent manager orders execute policy"
```

---

## Task 6: Switch Mock Quadrant Execution Agent To Stage C

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Update tool surface expectation test**

In `test_agent_tool_surfaces_are_role_specific`, replace:

```python
    assert created_tool_names(created["execution_agent"]) == {
        "orders_preflight_check",
        "orders_submit_and_confirm_order",
    }
```

with:

```python
    assert created_tool_names(created["execution_agent"]) == {
        "orders_execute_order",
    }
```

Add this non-execution assertion:

```python
        assert "orders_execute_order" not in created_tool_names(non_execution_agent)
```

- [ ] **Step 2: Update prompt boundary expectations**

In `test_prompt_boundaries_are_short_and_role_specific`, replace the Stage B positive expectations:

```python
    assert "call orders_preflight_check" in serialized
    assert "orders_submit_and_confirm_order" in serialized
    assert "combined tool returns can_continue=true" in serialized
    assert "if preflight returns can_submit=false" in serialized
```

with:

```python
    assert "orders_execute_order" in serialized
    assert "call orders_execute_order exactly once" in serialized
    assert "the tool performs readiness checks, submission, and confirmation internally" in serialized
    assert "can_continue=false" in serialized
```

Add these forbidden phrases to the `forbidden_phrase` tuple:

```python
        "call orders_preflight_check",
        "orders_submit_and_confirm_order",
        "if preflight returns can_submit=false",
        "combined tool returns can_continue=true",
```

- [ ] **Step 3: Update execution task prompt expectation**

In the test that checks `execution_task_prompt`, replace the required phrases:

```python
    for required_phrase in (
        "orders_preflight_check",
        "with the exact order fields",
        "orders_submit_and_confirm_order",
        "blocks continuation",
    ):
        assert required_phrase in execution_task_prompt
```

with:

```python
    for required_phrase in (
        "orders_execute_order",
        "exact order fields",
        "can_continue=false",
    ):
        assert required_phrase in execution_task_prompt
    assert "orders_preflight_check" not in execution_task_prompt
    assert "orders_submit_and_confirm_order" not in execution_task_prompt
```

- [ ] **Step 4: Run the mock quadrant prompt/tool tests and verify they fail**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py::test_agent_tool_surfaces_are_role_specific tests/test_ai_trading_team_mock_growth_inflation_quadrant.py::test_prompt_boundaries_are_short_and_role_specific -q
```

Expected: fail because the strategy still exposes Stage B tools and prompts.

- [ ] **Step 5: Update mock quadrant execution-agent tool list**

In `AITradingTeamMockGrowthInflationQuadrantStrategy.initialize`, replace:

```python
            tools=[
                BuiltinTools.orders.preflight(),
                BuiltinTools.orders.submit_and_confirm(),
            ],
```

with:

```python
            tools=[
                BuiltinTools.orders.execute(),
            ],
```

- [ ] **Step 6: Update execution-agent system prompt**

Replace the current Stage B execution role prompt with:

```python
            system_prompt=(
                "Execution role: execute only the provided execution_plan using the listed order execution tool. "
                "For each order in ascending sequence order, call orders_execute_order exactly once with the exact "
                "order fields. Do not manually preflight, submit, confirm, query account state, query open orders, "
                "or query latest prices when orders_execute_order is available. The tool performs readiness checks, "
                "submission, and confirmation internally. Continue to the next order only when the tool returns "
                "can_continue=true. If it returns can_continue=false, stop remaining orders and report the blocker. "
                "Do not perform investment research, do not change order fields, and do not call lower-level order "
                "tools when orders_execute_order is available."
            ),
```

- [ ] **Step 7: Update execution-agent task prompt**

Replace the `execution_agent.run(...)` task prompt with:

```python
            task_prompt=(
                "Execute the provided execution_plan in sequence order. For each order, call orders_execute_order "
                "exactly once with the exact order fields. Stop if any orders_execute_order result returns "
                "can_continue=false."
            ),
```

- [ ] **Step 8: Run mock quadrant tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: all mock quadrant tests pass.

- [ ] **Step 9: Commit strategy and prompt changes**

Run:

```powershell
git add lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: route mock quadrant execution through execute order"
```

---

## Task 7: Add Replay UI Formatter For Stage C

**Files:**
- Modify: `lumibot/components/agents/replay_ui/formatters.py`
- Modify: `tests/test_agent_replay_ui_formatters.py`

- [ ] **Step 1: Add formatter tests**

Insert near existing order formatter tests:

```python
def test_orders_execute_order_formatter_explains_success():
    text = format_tool_result(
        "orders_execute_order",
        {"sequence": 1, "symbol": "GLD", "side": "buy", "quantity": 106},
        {
            "sequence": 1,
            "symbol": "GLD",
            "side": "buy",
            "quantity": 106,
            "execution_status": "completed",
            "can_continue": True,
            "preflight_result": {
                "readiness": "ready",
                "account": {"cash": 100000.0},
                "price": {"last_price": 229.79},
            },
            "submit_and_confirm_result": {
                "submitted": True,
                "confirmed": True,
                "can_continue": True,
                "identifier": "order-1",
                "confirmation_status": "fill",
                "confirm_result": {"attempt_count": 1},
            },
            "blockers": [],
            "warnings": [],
        },
    )

    assert "Executed order 1 buy 106 GLD" in text
    assert "preflight ready" in text.lower()
    assert "confirmed" in text.lower()
    assert "can_continue=true" in text


def test_orders_execute_order_formatter_explains_preflight_blocker():
    text = format_tool_result(
        "orders_execute_order",
        {"symbol": "SPY", "side": "buy", "quantity": 100000},
        {
            "symbol": "SPY",
            "side": "buy",
            "quantity": 100000,
            "execution_status": "blocked",
            "can_continue": False,
            "preflight_result": {"readiness": "blocked", "can_submit": False},
            "submit_and_confirm_result": None,
            "blockers": [
                {
                    "code": "INSUFFICIENT_CASH_ESTIMATE",
                    "message": "Estimated buy value exceeds current cash.",
                }
            ],
        },
    )

    assert "blocked before submit" in text.lower()
    assert "INSUFFICIENT_CASH_ESTIMATE" in text
    assert "can_continue=false" in text


def test_orders_execute_order_formatter_explains_confirmation_blocker():
    text = format_tool_result(
        "orders_execute_order",
        {"symbol": "GLD", "side": "buy", "quantity": 106},
        {
            "symbol": "GLD",
            "side": "buy",
            "quantity": 106,
            "execution_status": "blocked",
            "can_continue": False,
            "preflight_result": {"readiness": "ready", "can_submit": True},
            "submit_and_confirm_result": {
                "submitted": True,
                "confirmed": False,
                "can_continue": False,
                "identifier": "order-1",
                "confirmation_status": "new",
            },
            "blockers": [
                {
                    "code": "CONFIRMATION_FAILED",
                    "message": "Order was submitted but not confirmed.",
                }
            ],
        },
    )

    assert "submitted but not confirmed" in text.lower()
    assert "CONFIRMATION_FAILED" in text
    assert "can_continue=false" in text
```

- [ ] **Step 2: Run formatter tests and verify they fail**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_agent_replay_ui_formatters.py::test_orders_execute_order_formatter_explains_success tests/test_agent_replay_ui_formatters.py::test_orders_execute_order_formatter_explains_preflight_blocker tests/test_agent_replay_ui_formatters.py::test_orders_execute_order_formatter_explains_confirmation_blocker -q
```

Expected: fail because no `orders_execute_order` formatter is registered.

- [ ] **Step 3: Add `_orders_execute_order` formatter**

Insert after `_orders_submit_and_confirm_order`:

```python
def _orders_execute_order(args: dict[str, Any], raw_result: Any) -> str:
    result = _as_dict(raw_result)
    preflight = _nested_dict(result, "preflight_result")
    submit_confirm = _nested_dict(result, "submit_and_confirm_result")
    symbol = _first_present(result, "symbol") or _first_present(args, "symbol")
    qty = _first_present(result, "quantity", "qty", "shares") or _first_present(args, "quantity", "qty", "shares")
    side = _first_present(result, "side", "action") or _first_present(args, "side", "action")
    sequence = _first_present(result, "sequence") or _first_present(args, "sequence")
    order_id = _first_present(submit_confirm, "identifier", "id", "order_id")
    status = _first_present(result, "execution_status", "status")
    preflight_readiness = _first_present(preflight, "readiness")
    blockers = result.get("blockers") if isinstance(result.get("blockers"), list) else []
    order_text = f"{_text(side)} {_text(qty)} {_text(symbol)}"
    sequence_text = "" if sequence is None else f" {_text(sequence)}"

    if result.get("can_continue") is True and status == "completed":
        id_text = "" if order_id is None else f" Order id: {_text(order_id)}."
        return (
            f"Executed order{sequence_text} {order_text}: preflight {_text(preflight_readiness)}, "
            f"submitted and confirmed.{id_text} can_continue=true."
        )

    blocker_text = ""
    if blockers:
        first = _as_dict(blockers[0])
        code = _first_present(first, "code")
        message = _first_present(first, "message")
        blocker_text = f" Blocker: {_text(code)}"
        if message is not None:
            blocker_text += f" - {_text(message)}"

    if submit_confirm:
        return (
            f"Order{sequence_text} {order_text} was submitted but not confirmed. "
            f"Status: {_text(status)}. can_continue=false.{blocker_text}"
        )

    return (
        f"Order{sequence_text} {order_text} was blocked before submit. "
        f"Preflight {_text(preflight_readiness)}. can_continue=false.{blocker_text}"
    )
```

- [ ] **Step 4: Register the formatter**

Add to `_FORMATTERS`:

```python
    "orders_execute_order": _orders_execute_order,
```

- [ ] **Step 5: Run formatter tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_agent_replay_ui_formatters.py::test_orders_execute_order_formatter_explains_success tests/test_agent_replay_ui_formatters.py::test_orders_execute_order_formatter_explains_preflight_blocker tests/test_agent_replay_ui_formatters.py::test_orders_execute_order_formatter_explains_confirmation_blocker -q
```

Expected:

```text
3 passed
```

- [ ] **Step 6: Commit formatter support**

Run:

```powershell
git add lumibot/components/agents/replay_ui/formatters.py tests/test_agent_replay_ui_formatters.py
git commit -m "feat: explain orders execute order in replay ui"
```

---

## Task 8: Add Replay Cache And Mutating Tool Safety Coverage

**Files:**
- Modify: `lumibot/components/agents/manager.py`
- Modify: `tests/test_agent_manager.py`
- Modify: `tests/test_agent_runtime_provider_keys.py`

- [ ] **Step 1: Search existing mutating allowlists**

Run:

```powershell
rg -n "orders_submit_and_confirm_order|mutates_trading|replay_on_cache|orders_confirm_order|orders_submit_order" lumibot tests -S
```

Expected: identify all explicit Stage B mutating tool allowlists and replay cache guard tests.

- [ ] **Step 2: Add `orders_execute_order` to exact mutating name checks**

Where current code enumerates mutating order tool names and includes:

```python
"orders_submit_and_confirm_order"
```

add:

```python
"orders_execute_order"
```

Do not replace Stage B names globally. Stage B remains available for other strategies and internal code.

- [ ] **Step 3: Add a replay safety test beside the Stage B replay safety test**

If `tests/test_agent_runtime_provider_keys.py` has a test fixture for mutating cached tools, add:

```python
def test_orders_execute_order_is_treated_as_mutating_replayable_tool():
    definition = BuiltinTools.orders.execute()

    assert definition.name == "orders_execute_order"
    assert definition.metadata["mutates_trading"] is True
```

If the existing replay safety test is in `tests/test_agent_manager.py`, add a sibling assertion to the existing test instead:

```python
assert "orders_execute_order" in mutating_tool_names
```

- [ ] **Step 4: Run replay and runtime tests**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_agent_manager.py tests/test_agent_runtime_provider_keys.py -q
```

Expected: both files pass.

- [ ] **Step 5: Commit replay safety updates**

Run:

```powershell
git add lumibot/components/agents/manager.py tests/test_agent_manager.py tests/test_agent_runtime_provider_keys.py
git commit -m "fix: treat execute order as mutating replayable tool"
```

---

## Task 9: Run Focused Regression Test Suite

**Files:**
- No code changes expected.

- [ ] **Step 1: Run focused Stage C test suite**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_agent_tool_permissions.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py tests/test_agent_manager.py tests/test_agent_replay_ui_formatters.py tests/test_agent_runtime_provider_keys.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run formatting/lint check for changed files**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot/components/agents/builtins.py lumibot/components/agents/manager.py lumibot/components/agents/replay_ui/formatters.py lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py tests/test_agent_tool_permissions.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py tests/test_agent_manager.py tests/test_agent_replay_ui_formatters.py tests/test_agent_runtime_provider_keys.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 3: Commit lint-only fixes if the previous step required formatting changes**

If files changed during lint correction, run:

```powershell
git add lumibot tests
git commit -m "chore: clean up execute order stage c lint"
```

If no files changed, skip this commit.

---

## Task 10: Run One-Day Benchmark Validation

**Files:**
- Create: `docs/superpowers/notes/2026-08-11-orders-execute-order-stage-c-validation.md`

- [ ] **Step 1: Run a one-day mock quadrant benchmark**

Use the same model defaults currently configured for this worktree. Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe scripts/run_ai_trading_team_examples_benchmark.py --scenario mock-growth-inflation-quadrant --start-date 2024-09-05 --end-date 2024-09-05
```

Expected:

```text
mock-growth-inflation-quadrant ... passed
```

The exact text may include artifact paths and timing information.

- [ ] **Step 2: Inspect the latest artifact for Stage C tool usage**

Find the latest artifact:

```powershell
Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Recurse -Directory |
  Where-Object { $_.FullName -match 'mock-growth-inflation-quadrant' } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 5 FullName,LastWriteTime
```

Then set a PowerShell variable for the latest artifact and inspect execution trace:

```powershell
$latestArtifact = (Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Recurse -Directory |
  Where-Object { $_.FullName -match 'mock-growth-inflation-quadrant$' } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1).FullName
rg -n "orders_execute_order|orders_preflight_check|orders_submit_and_confirm_order|orders_submit_order|orders_confirm_order|NEGATIVE_CASH_NOT_ALLOWED|can_continue" "$latestArtifact\cache\agent_runtime\traces\execution_agent" -S
```

Expected:

- `orders_execute_order` appears as model-facing tool calls.
- `orders_preflight_check` and `orders_submit_and_confirm_order` appear only inside nested result detail or tool descriptions, not as separate model-facing execution-agent tool calls.
- No `orders_submit_order` or `orders_confirm_order` model-facing calls appear.
- No `NEGATIVE_CASH_NOT_ALLOWED` blocker appears.
- Each order result has `can_continue=true`.

- [ ] **Step 3: Create the validation note**

Create `docs/superpowers/notes/2026-08-11-orders-execute-order-stage-c-validation.md` with this content, replacing each "Record ..." instruction with the exact command output before committing:

```markdown
# Orders Execute Order Stage C Validation

Date: 2026-08-11

## Focused Tests

Command:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_agent_tool_permissions.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py tests/test_agent_manager.py tests/test_agent_replay_ui_formatters.py tests/test_agent_runtime_provider_keys.py -q
```

Result:

Record the exact final pytest pass summary from the terminal output.

## Ruff

Command:

```powershell
D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot/components/agents/builtins.py lumibot/components/agents/manager.py lumibot/components/agents/replay_ui/formatters.py lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py tests/test_agent_tool_permissions.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py tests/test_agent_manager.py tests/test_agent_replay_ui_formatters.py tests/test_agent_runtime_provider_keys.py
```

Result:

Record the exact final ruff summary from the terminal output.

## One-Day Benchmark

Command:

```powershell
D:\Lumibot\.venv\Scripts\python.exe scripts/run_ai_trading_team_examples_benchmark.py --scenario mock-growth-inflation-quadrant --start-date 2024-09-05 --end-date 2024-09-05
```

Artifact:

Record the exact latest one-day artifact path.

Observations:

- execution_agent visible tool list contained `orders_execute_order`.
- Each execution-plan order used one model-facing `orders_execute_order` call.
- Stage B tools did not appear as separate execution-agent model-facing calls.
- Final cash was non-negative.
- Final execution summary matched submitted and confirmed orders.
```

The committed note must contain real command output and artifact paths, not instruction text.

- [ ] **Step 4: Commit one-day validation note**

Run:

```powershell
git add docs/superpowers/notes/2026-08-11-orders-execute-order-stage-c-validation.md
git commit -m "docs: record execute order one day validation"
```

---

## Task 11: Run Two-Day Rebalance Benchmark Validation

**Files:**
- Modify: `docs/superpowers/notes/2026-08-11-orders-execute-order-stage-c-validation.md`

- [ ] **Step 1: Run a two-day benchmark that can exercise rebalance**

Run:

```powershell
D:\Lumibot\.venv\Scripts\python.exe scripts/run_ai_trading_team_examples_benchmark.py --scenario mock-growth-inflation-quadrant --start-date 2024-09-05 --end-date 2024-09-06
```

Expected:

```text
mock-growth-inflation-quadrant ... passed
```

- [ ] **Step 2: Inspect the two-day trace**

Find the latest artifact and inspect execution trace:

```powershell
Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Recurse -Directory |
  Where-Object { $_.FullName -match 'mock-growth-inflation-quadrant' } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 5 FullName,LastWriteTime
```

Then set a PowerShell variable for the latest artifact and inspect execution trace:

```powershell
$latestArtifact = (Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Recurse -Directory |
  Where-Object { $_.FullName -match 'mock-growth-inflation-quadrant$' } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1).FullName
rg -n "orders_execute_order|side.*sell|side.*buy|can_continue|NEGATIVE_CASH_NOT_ALLOWED|execution_status" "$latestArtifact\cache\agent_runtime\traces\execution_agent" -S
```

Expected:

- Same-day sell orders appear before later buy orders when the plan requires rotation.
- Every submitted order has an `orders_execute_order` result.
- Each successful result has `can_continue=true`.
- No negative-cash blocker appears.
- Final summary matches actual filled orders.

- [ ] **Step 3: Add two-day validation section**

Append this section to `docs/superpowers/notes/2026-08-11-orders-execute-order-stage-c-validation.md` with real values:

```markdown
## Two-Day Benchmark

Command:

```powershell
D:\Lumibot\.venv\Scripts\python.exe scripts/run_ai_trading_team_examples_benchmark.py --scenario mock-growth-inflation-quadrant --start-date 2024-09-05 --end-date 2024-09-06
```

Artifact:

Record the exact latest two-day artifact path.

Observations:

- Same-day rebalance completed through `orders_execute_order`.
- Sell orders completed before later buy orders when sequence required it.
- Each execution-plan order used one model-facing `orders_execute_order` call.
- No negative-cash regression appeared.
- Replay UI remained readable for successful and blocked order paths.
```

The committed note must contain real command output and artifact paths, not instruction text.

- [ ] **Step 4: Commit two-day validation note**

Run:

```powershell
git add docs/superpowers/notes/2026-08-11-orders-execute-order-stage-c-validation.md
git commit -m "docs: record execute order two day validation"
```

---

## Task 12: Final Review Before Stage C Acceptance

**Files:**
- Review only unless fixes are discovered.

- [ ] **Step 1: Verify git history shows focused commits**

Run:

```powershell
git log --oneline -8
```

Expected: recent commits correspond to tests, tool implementation, prompt/tool surface, formatter, safety, and validation notes.

- [ ] **Step 2: Verify worktree cleanliness**

Run:

```powershell
git status --short --branch
```

Expected:

```text
## feature/mock-growth-inflation-quadrant-skeleton
```

No modified or untracked files should remain, except benchmark artifacts that are intentionally ignored by git.

- [ ] **Step 3: Summarize Stage C acceptance state**

Prepare a concise final summary with:

- `orders_execute_order` implemented.
- Mock quadrant execution-agent tool surface reduced to one tool.
- Focused tests passed.
- One-day benchmark passed with artifact path.
- Two-day benchmark passed with artifact path.
- Known limitations remain Stage D scope: full-plan execution is not implemented.

Do not merge branches or push to GitHub in this task.

---

## Plan Self-Review

Spec coverage:

- Tool definition: Tasks 1 and 2.
- Tool behavior and reuse of Stage A/B: Tasks 3 and 4.
- Manager policy and prompt/tool synchronization: Tasks 5 and 6.
- Replay UI formatting: Task 7.
- Replay/mutating safety: Task 8.
- Focused tests: Task 9.
- One-day benchmark validation: Task 10.
- Two-day rebalance validation: Task 11.
- Stage C acceptance review: Task 12.
- Stage D deferral: preserved because no task accepts a full `execution_plan` in a single model-facing call.

Placeholder scan:

- The plan contains no unresolved implementation marker. Validation-note tasks explicitly require real command output and artifact paths before commit.

Type consistency:

- The new tool name is consistently `orders_execute_order`.
- The new factory method is consistently `BuiltinTools.orders.execute()`.
- The top-level result uses `execution_status`, `can_continue`, `preflight_result`, `submit_and_confirm_result`, and `internal_steps`.
- The mock quadrant execution path remains one order at a time and does not implement `execution_plan_execute`.
