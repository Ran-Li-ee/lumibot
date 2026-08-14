# Execution Plan Model-Facing Summary And Audit Details Design

Date: 2026-08-14

## 1. Purpose

This spec defines a focused transparency and token-efficiency improvement for
the `execution_plan_execute` tool.

The current tool correctly records rich execution details, but its raw result
can become large because every planned order embeds:

```text
preflight result
submit result
confirmation result
account snapshot after the order
full nested order objects
```

That full result is valuable for trace replay, debugging, and audit. It is not
the right shape for the LLM to read after execution.

The target structure is:

```text
trace stores full audit_details
LLM receives only model_facing_summary
```

This feature should replace passive context-window truncation with an explicit,
deterministic model-facing summary for `execution_plan_execute`, while
preserving the complete raw execution audit in trace.

## 2. Current Context

The relevant implementation areas are:

```text
lumibot/components/agents/builtins.py
lumibot/components/agents/runtime.py
lumibot/components/agents/replay_ui/formatters.py
lumibot/components/agents/replay_ui/loader.py
lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py
```

The current execution path is:

```text
execution_agent
  -> execution_plan_execute
       -> orders_execute_order, once per plan order
            -> orders_preflight_check
            -> orders_submit_and_confirm_order
                 -> orders_submit_order
                 -> orders_confirm_order
```

The tool currently returns a complete dictionary with:

```text
plan_status
counts
completed_orders
blocked_orders
skipped_orders
order_results
final_account_snapshot
warnings
blockers
summary
```

The biggest field is `order_results`.

In the 2026-08-14 validation benchmark, the full
`execution_plan_execute` semantic result was about 17,365 serialized
characters, and `order_results` alone was about 15,214 characters.

The runtime currently has generic pruning:

```text
if serialized tool response exceeds the configured threshold:
    send an excerpt to the model
    preserve beginning and end
```

This keeps the provider request from overflowing, but it is not semantically
ideal for execution results. The LLM should receive a deliberately designed
execution summary, not an arbitrary excerpt of a large audit object.

## 3. Goals

1. Keep the full `execution_plan_execute` raw execution result available for
   trace, replay, debugging, and audit.
2. Add an explicit `model_facing_summary` for `execution_plan_execute`.
3. Ensure the LLM receives the `model_facing_summary`, not the full raw
   `order_results` audit object.
4. Ensure failed or blocked execution still gives the LLM enough information to
   understand why execution stopped.
5. Keep the replay UI able to show both:
   - what the model saw;
   - the full audit details.
6. Avoid changing trading behavior.
7. Avoid changing basket universes, news tools, macro logic, or portfolio
   construction.
8. Avoid weakening order preflight, confirmation, negative-cash protection, or
   replay safety.

## 4. Non-Goals

This feature does not:

1. Change any symbols, basket definitions, or basket weights.
2. Add, remove, or modify news access for any agent.
3. Change macro regime classification.
4. Change basket-agent selection logic.
5. Change `target_portfolio_to_execution_plan` sizing logic.
6. Change order sequencing.
7. Change order type support.
8. Add limit, stop, bracket, fractional, short, option, margin, or leveraged
   execution.
9. Reduce the amount of data saved in trace.
10. Hide audit details from developers.
11. Build a generic summary system for every tool.

This spec handles only `execution_plan_execute`. Other large tools may adopt
the same pattern later after this implementation is proven.

## 5. Design Principle

The design should separate two payload audiences:

| Audience | Needs | Payload |
|---|---|---|
| LLM / execution agent | Continue/stop decision, order completion status, blockers, warnings, final cash/position summary | `model_facing_summary` |
| Developer / audit / replay UI | Exact preflight, submit, confirm, attempts, order objects, account snapshots | `audit_details` or raw unpruned response |

The LLM should not need to read full nested order objects after execution. The
developer should still be able to inspect them.

## 6. Target Data Flow

The target execution-result flow is:

```text
Local Python tool function returns full execution result
  -> Lumibot trace captures full unpruned response
  -> Lumibot builds or extracts model_facing_summary
  -> ADK FunctionTool receives model-facing response
  -> LLM receives only the concise summary
```

Boundary trace should preserve the distinction:

```text
B06_PYTHON_TOOL_TO_WRAPPER:
  full raw result / audit details

B08_FUNCTION_TOOL_TO_ADK:
  model-facing response
```

If the replay UI exposes both boundary payloads, a developer should be able to
answer:

```text
What did the Python tool really return?
What did the LLM actually see?
```

## 7. Recommended Implementation Approach

Use a tool-specific projection for `execution_plan_execute`.

The preferred implementation is:

1. Keep `execution_plan_execute` returning the full structured result.
2. Add a deterministic summary builder for that full result.
3. Store the summary in a `model_facing_summary` field on the raw result, or
   expose it through a helper the runtime can call.
4. Update runtime tool-response projection so that for
   `tool_name == "execution_plan_execute"`:
   - the unpruned response remains the full result;
   - the model-facing response becomes the summary;
   - the model-facing response is used even if the full response is under the
     generic pruning threshold.

This is better than relying on size-based pruning because the execution summary
should be semantically stable and intentionally shaped.

## 8. Model-Facing Summary Contract

### 8.1 Completed Plan

For a completed plan, the model-facing response should contain:

```json
{
  "schema_version": 1,
  "tool_name": "execution_plan_execute",
  "response_type": "model_facing_summary",
  "plan_status": "completed",
  "can_continue": true,
  "intent": "rebalance",
  "orders_requested": 3,
  "orders_attempted": 3,
  "orders_completed": 3,
  "orders_blocked": 0,
  "orders_skipped": 0,
  "completed_orders": [
    {
      "sequence": 1,
      "symbol": "IAU",
      "side": "buy",
      "quantity": 517,
      "order_type": "market",
      "time_in_force": "day",
      "confirmed": true,
      "confirmation_status": "filled",
      "order_identifier": "bt_1",
      "fill_price": 47.5,
      "filled_quantity": 517.0
    }
  ],
  "blocked_orders": [],
  "skipped_orders": [],
  "final_account": {
    "cash": 2336.91,
    "portfolio_value": 100000.0,
    "positions": [
      {
        "symbol": "USD",
        "asset_type": "forex",
        "quantity": 2336.91
      },
      {
        "symbol": "IAU",
        "asset_type": "stock",
        "quantity": 517.0,
        "avg_fill_price": 47.5
      }
    ]
  },
  "warnings": [],
  "blockers": [],
  "summary": "All 3 planned orders were completed and confirmed.",
  "audit_details_available": true
}
```

The exact numeric formatting may differ, but the meaning must be preserved.

### 8.2 Blocked Plan

For a blocked plan, the model-facing response must include enough detail to
explain stop behavior:

```json
{
  "schema_version": 1,
  "tool_name": "execution_plan_execute",
  "response_type": "model_facing_summary",
  "plan_status": "blocked",
  "can_continue": false,
  "intent": "rebalance",
  "orders_requested": 4,
  "orders_attempted": 2,
  "orders_completed": 1,
  "orders_blocked": 1,
  "orders_skipped": 2,
  "completed_orders": [
    {
      "sequence": 1,
      "symbol": "AAA",
      "side": "sell",
      "quantity": 10,
      "confirmed": true,
      "confirmation_status": "filled"
    }
  ],
  "blocked_orders": [
    {
      "sequence": 2,
      "symbol": "BBB",
      "side": "buy",
      "quantity": 20,
      "execution_status": "blocked",
      "blockers": [
        {
          "code": "NEGATIVE_CASH_NOT_ALLOWED",
          "message": "..."
        }
      ],
      "warnings": []
    }
  ],
  "skipped_orders": [
    {
      "sequence": 3,
      "symbol": "CCC",
      "side": "buy",
      "quantity": 5,
      "skip_reason": "stopped_after_sequence_2_blocked"
    }
  ],
  "final_account": {
    "cash": 1234.56,
    "portfolio_value": 100000.0,
    "positions": []
  },
  "warnings": [],
  "blockers": [
    {
      "code": "ORDER_BLOCKED",
      "message": "Execution stopped at sequence 2.",
      "sequence": 2,
      "symbol": "BBB"
    }
  ],
  "summary": "Execution stopped at sequence 2 because BBB was blocked.",
  "audit_details_available": true
}
```

The blocked summary must not hide root-cause information. It should preserve
blocker codes and messages.

### 8.3 Invalid Or Hold Plan

Invalid and hold plans should also use the summary contract.

For invalid plans:

```text
plan_status = invalid
orders_attempted = 0
blockers includes the validation blocker
summary says no orders were submitted
```

For hold plans:

```text
plan_status = completed
orders_requested = 0
orders_completed = 0
summary says no planned orders were submitted because intent was hold
```

## 9. Full Audit Details Contract

The full audit details should continue to include the current nested structure:

```text
order_results[].order_result.preflight_result
order_results[].order_result.submit_and_confirm_result
order_results[].order_result.account_after
final_account_snapshot
completed_orders
blocked_orders
skipped_orders
warnings
blockers
```

The implementation may either:

1. keep the current full result shape and add `model_facing_summary`; or
2. wrap the current full result under an explicit `audit_details` key.

The preferred first implementation is option 1:

```text
preserve current full result shape
add model_facing_summary
project model_facing_summary to the LLM
```

This avoids breaking existing tests and replay formatters that already expect
the current full result shape.

## 10. Runtime Projection Requirements

The runtime currently records both unpruned and model-facing responses around
tool calls. This feature should use that capability.

For `execution_plan_execute`:

1. The Python tool raw result should be captured unchanged.
2. The model-facing response should be `model_facing_summary`.
3. Generic excerpt pruning should not be the normal model-facing path for this
   tool.
4. If the summary builder fails, runtime may fall back to existing pruning, but
   the trace should include a diagnostic.
5. The model-facing response should be small enough that it does not need
   generic excerpt pruning under normal execution sizes.

The implementation should not replace the global pruning system. It should add
one tool-specific projection path.

## 11. Replay UI Requirements

The replay UI should remain useful for both audiences.

At minimum, after implementation:

1. Tool Calls should show the model-facing summary as the tool output that the
   LLM saw.
2. Boundary Trace or detail panels should still expose the full unpruned raw
   result.
3. The human-readable explanation for `execution_plan_execute` should prefer
   the summary when present.
4. The UI should not lose visibility into:
   - preflight readiness;
   - submit status;
   - confirmation status;
   - fill price;
   - filled quantity;
   - final cash;
   - blockers and warnings.

No new complex graphical UI is required in this feature. If current UI already
shows both model-facing and raw boundary payloads, tests should verify that
behavior rather than adding a new panel.

## 12. Prompt And Tool Description Changes

### 12.1 Execution-Agent Prompt

The mock quadrant execution-agent prompt should be updated only lightly.

It should communicate:

```text
execution_plan_execute returns a concise execution summary.
Use that summary to write the final result.
Do not infer missing order details beyond the tool response.
Full audit details are recorded in trace/replay for developer inspection.
```

It should not add research language, portfolio-decision language, or detailed
audit instructions.

### 12.2 Tool Description

The `execution_plan_execute` tool description should mention:

```text
Returns a concise execution summary to the model while full audit details are
recorded in trace.
```

The description should remain short. It should not become a tutorial.

### 12.3 Other Prompts

This feature should not change:

1. macro allocation prompts;
2. equity basket prompts;
3. commodity basket prompts;
4. TIPS basket prompts;
5. nominal bond basket prompts;
6. portfolio decision prompts, except if a test proves execution-plan wording
   needs to acknowledge summary/audit separation.

## 13. Basket, Symbol, And News Scope

This feature deliberately does not change basket, symbol, or news behavior.

### 13.1 Baskets And Symbols

No basket universe should be changed in this feature.

The feature should work with whatever `execution_plan` is produced by the
existing workflow.

### 13.2 News

No news tool behavior should change.

The summary/audit pattern may later be useful for large news responses, but
this feature should not modify news tools. Keeping the scope to execution
results makes benchmark differences easier to interpret.

### 13.3 Ranking And History Tools

No ranking or history summary behavior should change.

The current `market_load_history_tables_summary` top-rank behavior remains
out of scope.

## 14. Tests

### 14.1 Summary Builder Tests

Add tests proving that a full successful `execution_plan_execute` result
produces a compact `model_facing_summary` with:

1. `plan_status`;
2. order counts;
3. completed order summaries;
4. final cash;
5. final position summaries;
6. warnings and blockers;
7. `audit_details_available=true`.

Add tests proving that blocked and invalid results preserve:

1. blocker codes;
2. blocker messages;
3. blocked sequence;
4. skipped order summaries;
5. completed-before-blocked summaries.

### 14.2 Runtime Projection Tests

Add tests proving that for `execution_plan_execute`:

1. full raw response is still captured as unpruned response;
2. model-facing response is the summary;
3. model-facing response is used even when the full response is under the
   generic pruning threshold;
4. generic excerpt response is not used when a valid summary exists.

### 14.3 Replay UI Tests

Add or update tests proving:

1. replay loader exposes model-facing summary for tool output;
2. replay detail/boundary data can still access the full audit payload;
3. human-readable formatter explains completed plans from the summary;
4. human-readable formatter explains blocked plans from the summary.

### 14.4 Existing Behavior Regression Tests

Existing tests should still pass for:

1. `orders_preflight_check`;
2. `orders_submit_and_confirm_order`;
3. `orders_execute_order`;
4. `execution_plan_execute`;
5. replay cache safety;
6. mock quadrant execution-agent tool visibility;
7. mock quadrant prompt alignment.

## 15. Benchmark Validation

After implementation, run at least one one-day mock quadrant benchmark.

Acceptance checks:

1. benchmark status is `passed`;
2. `execution_agent` calls `execution_plan_execute`;
3. the boundary trace contains the full raw execution audit;
4. the model-facing tool result is a compact summary, not the 17k-style full
   audit;
5. all planned orders still submit and confirm when the plan is valid;
6. final cash is non-negative;
7. replay UI can discover the run;
8. Account Curve and Performance Report artifact links remain available when
   generated.

If possible, also run or reuse a blocked-plan unit test to prove failed
execution remains diagnosable.

## 16. Risks And Mitigations

| Risk | Mitigation |
|---|---|
| Summary hides failure root cause. | Preserve blocker code, message, sequence, symbol, completed-before-blocked, and skipped orders. |
| Full audit is accidentally removed from trace. | Tests must verify raw unpruned response remains available. |
| UI only shows summary and loses audit details. | Tests must verify full boundary payload remains inspectable. |
| Runtime projection accidentally affects other tools. | Scope projection to `tool_name == "execution_plan_execute"`. |
| Existing formatter expects full result shape. | Keep current full result shape and add summary instead of replacing raw structure. |
| Prompt gets longer than needed. | Keep prompt change to one concise execution-agent sentence/paragraph. |
| Generic pruning still sends excerpt instead of summary. | Runtime projection test must prove summary wins over generic excerpt pruning. |

## 17. Acceptance Criteria

This feature is accepted when:

1. `execution_plan_execute` full raw result still contains complete audit
   details.
2. A compact `model_facing_summary` is generated for completed, blocked,
   invalid, and hold plans.
3. The LLM receives `model_facing_summary` rather than full `order_results`.
4. Boundary trace records both the full raw response and the model-facing
   summary.
5. Replay UI can show the concise model-facing result and still expose the
   full audit payload.
6. Execution-agent prompt and tool description reflect the concise-summary
   behavior.
7. No basket, symbol, news, macro, or portfolio-selection behavior changes.
8. Focused tests pass.
9. A one-day benchmark confirms valid execution still completes.

## 18. Implementation Plan Guidance

The implementation plan should proceed in small steps:

1. Add tests for `model_facing_summary` generation from successful and blocked
   execution-plan results.
2. Implement the summary builder while preserving the current full raw result
   shape.
3. Add runtime projection tests for `execution_plan_execute`.
4. Implement tool-specific projection from full raw result to summary.
5. Update replay formatter/loader behavior if needed.
6. Add or update replay UI tests.
7. Update execution-agent prompt and tool description.
8. Run focused unit tests and ruff.
9. Run one one-day benchmark.
10. Record validation notes.

The plan should avoid modifying unrelated basket, news, ranking, or macro code.

## 19. Spec Self-Review

This spec was reviewed for:

- placeholder language: no unresolved placeholder markers remain;
- scope: limited to `execution_plan_execute` summary/audit separation;
- non-goals: baskets, symbols, news, macro, and portfolio logic are explicitly
  out of scope;
- data flow: raw audit and model-facing summary paths are both defined;
- failure behavior: blocked and invalid summaries must retain root-cause
  information;
- UI behavior: model-facing summary and full audit inspection are both covered;
- testing: summary builder, runtime projection, replay UI, regression, and
  benchmark validation are included.
