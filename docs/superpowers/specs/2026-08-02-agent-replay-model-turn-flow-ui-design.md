# Agent Replay Model Turn Flow UI v1 Design

## Purpose

Build the first UI layer for inspecting the LLM/tool boundary flow recorded in existing agent traces.

The goal is to let a developer select a completed backtest, choose an agent, choose a model turn, and understand the exact sequence of communication between:

1. OpenAI LLM
2. LiteLLM
3. Google ADK
4. ADK FunctionTool
5. Lumibot tool wrapper
6. Local Python tool function

This is a replay UI for completed trace data. It is not a live streaming monitor.

## Scope

This version only displays information already recorded in trace files. It must not change trace recording behavior, agent runtime behavior, trading behavior, or tool execution behavior.

The UI should make existing boundary trace data easier to understand by grouping it into:

- Agent
- Model turn
- Tool call
- Boundary step

## Current Trace Evidence

The latest verified backtest trace used for design validation is:

```text
artifacts/ai_trading_team_example_benchmarks/20260801_154220_235490/growth-execution-test/cache/agent_runtime
```

The `execution_agent` trace contains:

- 3 model turns
- 5 tool calls
- 42 boundary events

Expected visible behavior for this trace:

- Model Turn 1 shows four tool calls:
  - `account_positions`
  - `account_portfolio`
  - `orders_open_orders`
  - `market_last_price`
- Model Turn 2 shows one tool call:
  - `orders_submit_order`
- Model Turn 3 shows the final model response and no tool calls.

## Conceptual Flow

The UI should present one model turn at a time.

For a tool-calling model turn, the time-ordered conceptual flow is:

```text
1  Google ADK -> LiteLLM
2  LiteLLM -> OpenAI LLM
3  OpenAI LLM -> LiteLLM
4  LiteLLM -> Google ADK

5.x  Google ADK -> ADK FunctionTool
6.x  ADK FunctionTool -> Lumibot tool wrapper
7.x  Lumibot tool wrapper -> Local Python tool function
8.x  Local Python tool function -> Lumibot tool wrapper
9.x  Lumibot tool wrapper -> ADK FunctionTool
10.x ADK FunctionTool -> Google ADK
```

`x` is the tool-call sequence number inside the model turn.

For a final-answer model turn, the flow is:

```text
11 Google ADK -> LiteLLM
12 LiteLLM -> OpenAI LLM
13 OpenAI LLM -> LiteLLM
14 LiteLLM -> Google ADK
```

The UI numbering is for human readability. It must remain linked to the original trace transition names.

## Trace Transition Mapping

The UI should map conceptual steps to recorded transition names:

| UI Step | Trace Transition | Meaning |
|---|---|---|
| 1 / 11 | `B09_ADK_TO_LITELLM` | ADK sends the model request into LiteLLM |
| 2 / 12 | `B10_LITELLM_TO_PROVIDER` | LiteLLM sends the provider-facing request |
| 3 / 13 | `B01_PROVIDER_TO_LITELLM` | Provider/model response returns to LiteLLM |
| 4 / 14 | `B02_LITELLM_TO_ADK` | LiteLLM response returns to ADK |
| 5.x | `B03_ADK_TO_FUNCTION_TOOL` | ADK dispatches a tool call to FunctionTool |
| 6.x | `B04_FUNCTION_TOOL_TO_WRAPPER` | FunctionTool passes arguments to Lumibot wrapper |
| 7.x | `B05_WRAPPER_TO_PYTHON_TOOL` | Lumibot wrapper calls the local Python function |
| 8.x | `B06_PYTHON_TOOL_TO_WRAPPER` | Local Python function returns raw result |
| 9.x | `B07_WRAPPER_TO_FUNCTION_TOOL` | Lumibot wrapper returns serialized result |
| 10.x | `B08_FUNCTION_TOOL_TO_ADK` | FunctionTool returns model-facing response to ADK |

## UI Placement

Add a new collapsible section to the selected agent detail view:

```text
Model Turn Replay
```

It should live near the existing `Input Material`, `Tool Calls`, and `Final Summary` sections. It should not replace those sections in v1.

## Layout

The section should contain:

1. A model turn selector.
2. A visual flow panel.
3. A boundary detail panel.

Recommended layout:

```text
Model Turn Replay
--------------------------------------------------
Model Turn: [turn:0001 v]

[Flow diagram / step list]     [Selected step details]
```

The first version may use a structured step list instead of a fully drawn diagram if that is safer and faster, but it must preserve the conceptual vertical flow:

```text
OpenAI LLM
LiteLLM
Google ADK
ADK FunctionTool
Lumibot tool wrapper
Local Python tool function
```

## Interaction

Clicking a step should update the detail panel.

Each step detail should show:

- UI step number
- Original trace transition
- From module
- To module
- Model turn ID
- Tool batch ID, if present
- Tool call ID, if present
- Call instance ID, if present
- Tool name, if present
- Status
- Error, if present
- Human-readable explanation
- Payload summary
- Full payload or sidecar loader, if available

## Model Turn Grouping

The UI should group events by `model_turn_id`.

Within a model turn:

- Request/response boundary events should be shown once in chronological order.
- Tool boundary events should be grouped by `call_instance_id` when available.
- If `call_instance_id` is unavailable, fallback grouping may use `call_id`, then `tool_batch_id`, then trace sequence order.

The UI should tolerate partial or malformed traces and show a clear warning instead of failing the entire page.

## Tool Call Display

For each tool call, show:

```text
Tool Call 1: account_positions
  5.1 -> 6.1 -> 7.1 -> 8.1 -> 9.1 -> 10.1

Tool Call 2: account_portfolio
  5.2 -> 6.2 -> 7.2 -> 8.2 -> 9.2 -> 10.2
```

The visible tool name should be resolved from the best available source, in this order:

1. Event payload `tool_name`
2. B02 `tool_calls[].name`
3. B03 `payload.tool_name`
4. B08 `payload.function_name`
5. Existing tool call replay records
6. Unknown tool placeholder

## Context Full Text

The most important payload for understanding "what the LLM saw" is `B09_ADK_TO_LITELLM`.

For B09:

- If a sidecar is available, the UI should offer a "Load full payload" action.
- The detail panel should clearly identify this as the ADK-to-LiteLLM request snapshot.
- The UI should surface:
  - `llm_request`
  - `context_pruning`
  - `previous_model_turn_id`
  - `current_model_turn_id`

This is the first version of "Google ADK context full text" display.

## Known Completeness Limits

The UI must communicate trace completeness honestly.

Known limits:

- `B10_LITELLM_TO_PROVIDER` is currently a LiteLLM pre-provider request projection, not raw OpenAI HTTP wire data.
- Some B10 payloads are partial and may show `semantic_completeness=partial`.
- Hidden model reasoning is not available unless the provider exposes it.
- Tool-internal external API calls are not uniformly traced in v1.
- ADK internal state beyond what is placed in the model request is not shown as a separate object.

These are not blockers for v1.

## Out Of Scope

This v1 does not include:

- Recording new trace fields
- Raw OpenAI HTTP request/response capture
- Tool-internal HTTP/API tracing
- Live streaming replay while a backtest is running
- Interactive animation
- Editing prompts or tool calls from the replay UI
- Changing trading behavior, agent prompts, or execution logic

## Error Handling

The UI should handle:

- Missing `boundary_trace`
- Empty model turns
- Boundary events without `model_turn_id`
- Tool events without `call_instance_id`
- Missing sidecar files
- Corrupt sidecar files
- Partial payload metadata

Failures should be displayed as warnings in the relevant section.

## Security And Redaction

The UI must use existing redacted trace payloads and sidecar-loading safeguards.

It must not:

- Read arbitrary filesystem paths from the browser
- Expose API keys
- Bypass existing sidecar path containment checks
- Print hidden local paths beyond existing public metadata rules

## Acceptance Criteria

Using the verified `execution_agent` trace from `20260801_154220_235490`:

1. The selected agent detail shows a `Model Turn Replay` section.
2. The section lists 3 model turns.
3. Model Turn 1 shows four tool calls:
   - `account_positions`
   - `account_portfolio`
   - `orders_open_orders`
   - `market_last_price`
4. Model Turn 2 shows one tool call:
   - `orders_submit_order`
5. Model Turn 3 shows a final model response and no tool calls.
6. Clicking B09 / UI step 1 or 11 can load the full sidecar payload when available.
7. Clicking `orders_submit_order` step 5.1-10.1 shows:
   - model arguments
   - wrapper arguments
   - Python tool raw result
   - model-facing response
8. Partial B10 payloads are labelled as partial instead of presented as raw OpenAI wire truth.
9. Existing `Input Material`, `Tool Calls`, and `Final Summary` sections still work.

## Test Plan

Automated tests should cover:

- Loader grouping by model turn.
- Tool call grouping by call instance.
- Fallback grouping when call instance is missing.
- Public JSON shape for model turn replay data.
- Sidecar metadata exposure without leaking raw paths.
- Static UI rendering for model turn replay.
- Browser-level smoke test that selecting an agent shows the model turn replay section.

Manual validation should run:

```text
python scripts/agent_trace_ui.py
```

Then select the `AITradingTeamGrowthExecutionTestStrategy` backtest containing the 2024-09-05 system run and verify the acceptance criteria above.

## Design Self-Review

- No implementation code is required by this spec.
- Scope is limited to UI display of existing trace data.
- The distinction between human UI step numbers and raw trace transitions is explicit.
- Known trace completeness limits are documented.
- The design avoids adding new recorder behavior in v1.
