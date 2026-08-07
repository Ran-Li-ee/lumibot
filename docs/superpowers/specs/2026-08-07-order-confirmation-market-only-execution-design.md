# Order Confirmation and Market-Only Execution Design

## Purpose

Add an explicit order-confirmation step to the agent trading workflow and simplify the current
growth-execution test strategy to market orders only.

The immediate problem is not that `orders_submit_order` cannot sell or rotate. It can. The
problem is that the execution agent currently submits an order, immediately checks broad account
or open-order state, and may see stale or still-open state before Lumibot/backtest/broker state has
settled. In recent rotation-window backtests this caused the agent to submit a sell order, then stop
before the dependent buy because it could not confirm updated cash/position state quickly enough.

This design introduces a small, focused confirmation tool and prompt changes that make every
submitted order follow this pattern:

```text
orders_submit_order
  -> orders_confirm_order
  -> continue only if confirmation says can_continue=true
```

For this daily ETF experiment, the strategy should also stop asking the model to choose between
market, limit, smart-limit, stop-limit, and other order types. All execution-plan orders should be
plain market orders. This reduces hanging orders and makes same-day sell-then-buy rotation easier
to test.

## Background From Investigation

### Current Native Order Tools

`lumibot/components/agents/builtins.py` already provides:

- `orders_submit_order`: creates and submits an order, then returns `{"order": ...}`.
- `orders_open_orders`: returns tracked orders from `strategy.get_orders()`.
- `orders_cancel_order`: cancels a tracked order by identifier.
- `orders_modify_order`: modifies tracked order prices.

`orders_submit_order` is a submission tool. It is not a reliable proof that the order has filled or
that positions/cash have already updated.

### Immediate Order Lookup Exists

`Strategy.get_order(identifier, broker_refresh=True, broker_refresh_ttl_seconds=0.0)` already
exists. Its docstring explicitly says to prefer direct lookup after submitting an order because
broad order-list endpoints can lag briefly after submit.

This is exactly the confirmation use case.

### Broad Open-Order Lists Can Lag

`Strategy.get_orders(...)` also documents that broad broker order-list endpoints can lag after a
submit. The current execution-agent prompt asks it to inspect open orders and portfolio after a
sell. That is directionally correct, but too low-level for the model to orchestrate reliably. The
retry/refresh loop should live inside a local tool, not inside the model's reasoning loop.

### Backtesting Processes Pending Orders Separately

Backtesting has `BacktestingBroker.process_pending_orders(strategy)`, which evaluates pending
orders and emits fills. The strategy executor normally calls this after `on_trading_iteration()`.

For same-iteration sell-then-buy rotation, waiting until the executor returns is too late. The new
confirmation tool should, in backtesting mode, use existing broker/strategy APIs to process pending
orders during confirmation attempts.

### Current Growth-Execution Strategy Still Allows Non-Market Orders

`AITradingTeamGrowthExecutionTestStrategy` currently allows these order types:

```python
{"market", "limit", "stop", "stop_limit", "trailing_stop", "smart_limit"}
```

The decision-agent prompt also includes bounded-price logic for `limit`, `smart_limit`, and
`stop_limit`. That made sense while testing whether the system could handle several order forms,
but it is counterproductive for the current daily ETF rotation test.

## Goals

- Add a new native agent tool named `orders_confirm_order`.
- Keep `orders_submit_order` as the only tool that creates/submits orders.
- Make `orders_confirm_order` confirm an existing submitted order by identifier.
- Make `orders_confirm_order` retry internally, so the LLM does not need to call status tools in a
  loop.
- Make the execution agent confirm every submitted order before placing the next order or writing
  its final summary.
- Make the growth-execution test strategy market-only.
- Prevent the decision agent from producing limit, stop, stop-limit, trailing-stop, or smart-limit
  orders in this strategy.
- Keep the change scoped enough that it can be tested with normal unit tests plus a short backtest.
- Improve replay/UI transparency by showing `orders_confirm_order` as a normal tool call with a
  human-readable confirmation result.

## Non-Goals

- Do not create an `orders_submit_and_confirm_order` combined tool in this pass.
- Do not create high-level tools like `orders_close_position`, `orders_reduce_position`, or
  `orders_rotate_position`.
- Do not build a deterministic code-only execution engine yet.
- Do not solve live-broker cash settlement, margin, shorting, or T+1 settlement rules.
- Do not add limit-order management, cancel/replace loops, or market-making behavior.
- Do not redesign the strategy's investment logic.
- Do not modify the original Ray Dalio demo strategy.

## Design Decision

Use a separate confirmation tool:

```text
orders_submit_order(...)
orders_confirm_order(identifier=..., symbol=..., side=..., expected_quantity=...)
```

This keeps tool responsibility clean:

- `orders_submit_order`: create and submit an order.
- `orders_confirm_order`: verify what happened to that submitted order.
- `orders_open_orders`: inspect broad current order state when needed.
- `account_positions` / `account_portfolio`: inspect current account state when needed.

The confirmation tool should not submit, cancel, modify, or replace orders.

## New Tool: `orders_confirm_order`

### Model-Facing Description

Proposed model-facing description:

```text
Confirm a previously submitted order by identifier. Use this after every
orders_submit_order call before submitting any later order or writing the final execution summary.
The tool refreshes the exact order, retries internally, and returns whether the order is confirmed
filled and whether it is safe to continue with later orders. This tool does not submit, cancel, or
modify orders.
```

### Function Signature

Recommended first-version signature:

```python
def confirm_order(
    identifier: str,
    symbol: str | None = None,
    side: str | None = None,
    expected_quantity: float | None = None,
    position_before_quantity: float | None = None,
    cash_before: float | None = None,
    max_attempts: int = 3,
    wait_seconds: float | None = None,
) -> dict[str, Any]:
    ...
```

### Argument Meaning

- `identifier`: required order identifier returned by `orders_submit_order`.
- `symbol`: optional but strongly recommended; used to include symbol-specific position checks.
- `side`: optional but strongly recommended; expected `buy`, `sell`, `buy_to_open`,
  `sell_to_close`, `sell_short`, or `buy_to_cover`.
- `expected_quantity`: optional but strongly recommended; intended submitted quantity.
- `position_before_quantity`: optional; lets the tool check whether the position moved in the
  expected direction.
- `cash_before`: optional; lets the tool report whether cash moved in the expected direction.
- `max_attempts`: optional, capped by implementation to prevent long waits.
- `wait_seconds`: optional; if omitted, the tool chooses a safe mode-specific default.

The model should normally pass `identifier`, `symbol`, `side`, and `expected_quantity`. The
before-state fields are useful when the execution agent has already inspected positions and
portfolio, but confirmation must not fail solely because the model omits them.

### Default Retry Policy

Recommended defaults:

- Backtesting: `max_attempts=3`, `wait_seconds=0`.
- Paper/live: `max_attempts=3`, `wait_seconds=1`.
- Implementation should cap `max_attempts` to a small maximum, such as 5, even if the model passes
  a larger number.
- Implementation should cap `wait_seconds` to a small maximum, such as 5 seconds, even if the model
  passes a larger number.

The tool should handle retries internally. The LLM should see one tool call and one compact result,
not a repeated sequence of status-checking tool calls.

### Confirmation Algorithm

For each attempt:

1. If running in backtesting and the broker exposes `process_pending_orders(strategy)`, call it or
   otherwise use the existing Lumibot mechanism that processes pending orders without advancing to
   the next trading day.
2. Refresh the exact order with `strategy.get_order(identifier, broker_refresh=True)`.
3. Read current open orders with `strategy.get_orders(...)` or the existing open-order helper.
4. Read current portfolio/cash and positions.
5. Determine order lifecycle status:
   - found or missing
   - active/open/new/submitted/partially filled
   - filled/cash-settled/assigned/exercised
   - canceled/rejected/error/expired
6. If the order is fully filled and optional expected-state checks pass, return confirmed.
7. If the order is terminal but not filled, return not confirmed with a terminal failure reason.
8. If the order is still active and attempts remain, wait using the mode-specific retry policy.
9. If attempts are exhausted, return not confirmed with `open_after_retries`.

### Confirmation Result Shape

Return a compact structured payload:

```json
{
  "identifier": "bt_123",
  "confirmed": true,
  "can_continue": true,
  "confirmation_status": "filled",
  "attempt_count": 2,
  "order": {
    "identifier": "bt_123",
    "status": "fill",
    "side": "sell",
    "asset": {"symbol": "VNQ", "asset_type": "stock"},
    "quantity": 1017,
    "filled_quantity": 1017,
    "avg_fill_price": 97.12,
    "is_active": false,
    "is_filled": true,
    "order_type": "market",
    "time_in_force": "day"
  },
  "account_snapshot": {
    "cash": 98842.04,
    "portfolio_value": 100231.55,
    "positions": [
      {"symbol": "USD", "quantity": 98842.04},
      {"symbol": "VNQ", "quantity": 0}
    ]
  },
  "checks": {
    "order_found": true,
    "order_filled": true,
    "position_moved_as_expected": true,
    "cash_moved_as_expected": true
  },
  "attempts": [
    {"attempt": 1, "status": "new", "is_active": true, "is_filled": false},
    {"attempt": 2, "status": "fill", "is_active": false, "is_filled": true}
  ],
  "warnings": []
}
```

For a blocked order:

```json
{
  "identifier": "bt_124",
  "confirmed": false,
  "can_continue": false,
  "confirmation_status": "open_after_retries",
  "attempt_count": 3,
  "order": {"identifier": "bt_124", "status": "new", "is_active": true},
  "checks": {
    "order_found": true,
    "order_filled": false
  },
  "warnings": [
    "Order remained active after 3 confirmation attempts. Do not submit dependent orders."
  ]
}
```

### Confirmation States

Use explicit string states:

- `filled`
- `cash_settled`
- `partially_filled`
- `open_after_retries`
- `rejected`
- `canceled`
- `expired`
- `error`
- `not_found`
- `unknown`

First version behavior:

- Full fills: `confirmed=true`, `can_continue=true`.
- Partial fills: `confirmed=false`, `can_continue=false`.
- Still open after retries: `confirmed=false`, `can_continue=false`.
- Rejected/canceled/error/expired/not found: `confirmed=false`, `can_continue=false`.

Partial-fill continuation can be designed later. For the current ETF daily market-order strategy,
the safe first behavior is to stop dependent orders when a fill is incomplete.

## Tool Registration

Add a new `_bind_confirm_order(strategy, manager)` binder near the existing order binders in
`lumibot/components/agents/builtins.py`.

Add a new method on `_OrderTools`:

```python
def confirm(self) -> ToolDefinition:
    return ToolDefinition(
        name="orders_confirm_order",
        description="Confirm a submitted order by identifier before continuing execution.",
        binder=_bind_confirm_order,
    )
```

The tool should be available only when explicitly added to an agent's tool list. Do not silently add
it to all agents unless existing `BuiltinTools.all()` policy already requires it. The
growth-execution strategy should explicitly include it for `execution_agent`.

Metadata guidance:

- It does not submit/cancel/modify orders.
- It may refresh broker state and, in backtesting, may process pending orders.
- Treat it as an order-management confirmation tool, not as a trade-placing tool.

If the permission system has a strict mutating/read-only boundary, implementation should choose the
least surprising option: make it available only to trading-enabled execution agents.

## Prompt Changes

### Decision Agent

Decision agent should produce only market-order execution plans for this strategy.

Remove or rewrite all prompt language that asks it to choose among:

- `limit`
- `stop`
- `stop_limit`
- `trailing_stop`
- `smart_limit`

The decision agent should output:

```json
{
  "sequence": 1,
  "action": "submit_order",
  "symbol": "VNQ",
  "asset_type": "stock",
  "side": "sell",
  "quantity_mode": "shares",
  "quantity": 1017,
  "order_type": "market",
  "time_in_force": "day"
}
```

For buy sizing:

- The decision agent must call `market_last_price` before sizing buy orders.
- The decision agent should use current available cash after prior sells and a conservative market
  sizing price.
- It should output final numeric whole-share quantities.
- It should not output `cash_buffer_pct` or any buffer field.
- It must not intentionally produce orders that would make cash negative.

For rotations:

- Sequence 1: market sell the current symbol.
- Sequence 2: market buy the target symbol.
- The decision agent should not include any wording that requires the execution agent to analyze the
  investment case.

### Execution Agent

Execution agent should be told:

- Execute only the validated `execution_plan.orders`.
- Do not infer investment reasons.
- Do not re-rank symbols.
- Do not change quantities, symbols, sides, sequence, or order types.
- Submit orders in ascending sequence order.
- Every `orders_submit_order` call must be followed by `orders_confirm_order` using the returned
  identifier.
- Continue to the next sequence only if `orders_confirm_order` returns `can_continue=true`.
- If confirmation fails, stop remaining orders and report the blocker.
- Use only market orders. If a non-market order appears in the execution plan, block it as an
  execution-plan validation error rather than trying to repair it.

Recommended core wording:

```text
After every orders_submit_order call, immediately call orders_confirm_order with the returned
identifier, symbol, side, and expected_quantity. Do not submit any later order and do not write the
final summary until that order is confirmed. If orders_confirm_order returns can_continue=false,
stop all remaining orders and report the confirmation blocker.
```

This rule should be general. It should not mention rotations specifically. Same-day rotation should
work as a natural result of confirming each order before continuing.

## Strategy Execution-Plan Validation

Update `AITradingTeamGrowthExecutionTestStrategy` validation to enforce market-only execution:

- `ALLOWED_ORDER_TYPES = {"market"}` for this strategy.
- Parsing should reject or block any order whose `order_type` is not `market`.
- Non-market price fields may be parsed as optional fields for compatibility, but any non-market
  order type should fail validation.
- Market orders should not include actionable limit/stop/stop-limit/trailing fields.
- Existing validation that simulates sell-before-buy cash should remain.
- Negative-cash guard should remain.

The validation layer is important because prompt-only restrictions are not enough. If the decision
agent outputs a limit order anyway, execution should never receive it.

## Existing Tests That Must Change

Several existing tests in `tests/test_ai_trading_team_growth_execution_test.py` currently assert
non-market behavior. They must be updated because the strategy is becoming market-only.

Expected changes:

- Replace tests that expect limit/smart-limit/stop-limit acceptance with tests that assert rejection.
- Update prompt tests to assert that decision prompt requires `order_type: market`.
- Update prompt tests to assert that decision prompt no longer contains bounded-price order
  instructions.
- Update execution-agent tool-surface tests to include `orders_confirm_order`.
- Update execution prompt tests to require the submit-then-confirm rule.
- Add execution handoff tests showing no investment report is passed to execution agent.
- Add tests proving `cash_buffer_pct`, `0.02`, and `2%` remain absent from execution-agent input.

## UI and Trace Behavior

The replay UI should require minimal new concepts because `orders_confirm_order` is just another
tool call.

Expected trace shape for a two-order rotation:

```text
execution_agent model turn N:
  tool call 1: orders_submit_order(sell ...)
  tool call 2: orders_confirm_order(identifier from sell ...)
  tool call 3: orders_submit_order(buy ...)
  tool call 4: orders_confirm_order(identifier from buy ...)
```

The boundary-flow UI should show each confirmation tool call as its own path through:

```text
Google ADK -> ADK FunctionTool -> Lumibot Tool Package Layer -> Local Python Tool Function
          <- ADK FunctionTool <- Lumibot Tool Package Layer <- Local Python Tool Function
```

The existing tool-call table should display a human-readable explanation such as:

```text
Confirmed order bt_123 for SELL 1017 VNQ. Status fill. Position moved as expected.
can_continue=true.
```

If confirmation fails:

```text
Order bt_124 remained open after 3 attempts. can_continue=false. Later orders should not be submitted.
```

## Backtest Validation Scenario

Use the same rotation-prone window that previously exposed the issue:

```text
2024-09-11 to 2024-10-07
```

Expected outcome:

- When the decision agent produces a rotate plan, the execution agent submits the sell.
- The execution agent calls `orders_confirm_order` for the sell.
- If the sell confirms filled, the execution agent proceeds to the buy in the same system run.
- The trade CSV should show same-day sell and buy for at least one successful rotation, assuming
  market data permits fills.
- If a rotation still does not complete, the trace should show an explicit confirmation failure
  reason from `orders_confirm_order`.

## Unit Test Plan

### Built-in Tool Tests

Add tests around `_bind_confirm_order` using fake strategy/order objects:

- Filled order returns `confirmed=true` and `can_continue=true`.
- New/open/submitted order retries and returns `open_after_retries`.
- Partially filled order returns `confirmed=false` and `can_continue=false`.
- Rejected/canceled/error/expired order returns terminal failure.
- Unknown identifier returns `not_found`.
- Attempts are capped even if the model passes a large `max_attempts`.
- Returned payload includes attempts, order payload, checks, warnings, and account snapshot.

### Strategy Tests

Add/update tests for:

- Decision prompt is market-only.
- Execution prompt requires submit then confirm after every order.
- Execution agent receives `orders_confirm_order`.
- Non-market execution-plan order types are blocked before execution.
- Valid market rotate plan still reaches execution.
- Execution context remains strict: only `date` and validated `execution_plan`, not growth report or
  decision prose.

### UI Formatter Tests

Add a formatter case for `orders_confirm_order`:

- Confirmed fill renders a concise success explanation.
- Open-after-retries renders a clear blocker explanation.
- Rejected/canceled renders terminal failure wording.

## Integration Test Plan

1. Run focused unit tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_tool_permissions.py tests\test_ai_trading_team_growth_execution_test.py tests\test_agent_replay_ui_formatters.py -q
```

2. Run a one-day backtest:

```powershell
.\.venv\Scripts\python.exe scripts\run_ai_trading_team_example_benchmarks.py --strategy growth-execution-test --start 2024-09-05 --end 2024-09-05
```

3. Confirm one-day replay UI shows:

- `execution_agent` has `orders_submit_order`.
- `execution_agent` has `orders_confirm_order`.
- Any submitted order is followed by a confirmation call.

4. Run the rotation-prone window:

```powershell
.\.venv\Scripts\python.exe scripts\run_ai_trading_team_example_benchmarks.py --strategy growth-execution-test --start 2024-09-11 --end 2024-10-07
```

5. Inspect:

- trace UI
- trades CSV
- account curve
- performance report if generated

## Risks and Mitigations

### Risk: Confirmation Tool Accidentally Advances Backtest Time

Mitigation: confirmation should process pending orders without advancing to the next trading day.
Implementation must inspect the exact broker methods before coding and add tests that confirm
same-system-run behavior.

### Risk: Live Brokers Report Order Status Slowly

Mitigation: cap retries and return a clear blocked result. Do not let the agent hang indefinitely.

### Risk: Filled Order Status Uses Broker-Specific Strings

Mitigation: rely on `Order.is_filled()` and `Order.is_active()` when possible, and normalize known
terminal status strings in one helper.

### Risk: Market Orders Increase Slippage

Mitigation: acceptable for this experiment. The goal is workflow reliability for daily ETF tests,
not execution-price optimization. More nuanced order types can be reintroduced later after
confirmation and execution control are stable.

### Risk: Prompt Rule Still Gets Ignored

Mitigation: prompt guidance is not the only guard. Strategy validation blocks non-market orders and
execution-agent tests assert the confirm-after-submit rule. A future phase may move from
LLM-followed execution to deterministic plan execution if needed.

### Risk: Partial Fill Handling Is Too Conservative

Mitigation: first version stops on partial fills. That is acceptable for stock/ETF daily backtests.
Partial-fill continuation can be designed separately when testing live/paper broker behavior.

## Acceptance Criteria

- `orders_confirm_order` exists as a native built-in tool.
- The tool confirms exact submitted orders by identifier using direct order lookup.
- The tool retries internally and returns `confirmed`, `can_continue`, attempts, checks, warnings,
  order details, and account snapshot.
- `AITradingTeamGrowthExecutionTestStrategy` gives `orders_confirm_order` to `execution_agent`.
- The execution-agent prompt requires confirmation after every submit.
- The decision-agent prompt and strategy validation are market-only.
- Non-market execution plans are blocked before the execution agent is called.
- Existing focused tests pass after updates.
- A one-day backtest produces usable trace output.
- The rotation-prone backtest either completes same-day rotations or clearly shows why confirmation
  blocked continuation.

## Future Work

- Deterministic code-level execution of validated execution plans.
- A higher-level `orders_execute_plan` tool that performs submit-confirm sequencing without asking
  the LLM to orchestrate each order.
- Broker-specific confirmation policies for real paper/live accounts.
- Partial-fill-aware continuation logic.
- Reintroducing non-market orders once confirmation and cancel/replace handling are mature.
