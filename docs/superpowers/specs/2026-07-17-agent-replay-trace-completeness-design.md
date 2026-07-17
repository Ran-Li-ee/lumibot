# Agent Replay Trace Completeness Design

## Goal

Make Agent Replay traces more useful for developing new tools by recording the tool transparency data that is currently available inside the runtime but not preserved in trace files, then expose that recorded data in the Agent Workflow Replay UI.

This feature supports future development of exit-position tools, but it does not add those tools yet.

## Background

The replay UI currently explains what happened during an agent run:

- which agent ran
- what prompt/context material was recorded
- which tools were available by name
- which tools were called
- what each tool returned
- what final summary the agent produced

That is enough for understanding a run at a high level. It is not enough for developing new tools because the developer also needs to inspect the complete tool contract that the runtime exposed to the LLM.

For example, a future exit tool should not only have Python input/output behavior. It also needs a clear model-facing name, description, parameter contract, metadata, and safety guidance. If the LLM misuses a tool, the replay UI should help answer:

- What exactly did the LLM know about this tool?
- What parameters did the runtime expose?
- Was the tool filtered out because trading was disabled?
- Did the tool have safety requirements?
- If the tool failed, was the failure caused by missing prerequisites or by a real runtime error?

## Current Runtime Facts

The Google ADK runtime wraps each `BoundTool` before handing it to the model runtime. The wrapper currently carries:

- tool name from `BoundTool.name`
- docstring from `BoundTool.description`
- Python signature copied from the original callable
- Python annotations copied from the original callable

The trace currently records `request.tool_surface`, but the stored metadata is intentionally reduced for cache stability. In practice, some traces show only lightweight metadata such as:

```json
{
  "kind": "builtin"
}
```

That means the UI may show less information than the runtime actually had.

Trading permission filtering also happens before the agent runs. When `allow_trading` is false, mutating trading tools are removed from the available tool list. Current traces record the final available tool list, but not which tools were filtered out or why.

Tool failures are recorded as tool results, but the UI does not yet show structured diagnostics for failures such as `ORDER_READINESS_REQUIRED`.

## Scope

This feature has two stages.

### Stage 1: Trace Completeness

Record more complete, deterministic, trace-safe information:

- model-facing tool description
- callable signature as a string
- callable annotations as JSON-safe strings
- callable default values as JSON-safe values
- replay/runtime metadata that is useful for tool development
- known safety requirements for built-in order tools
- available tools after permission filtering
- filtered tools with the filtering reason
- structured diagnostics for failed tool calls

This stage should not log secrets, raw provider HTTP requests, API keys, credentials, or unredacted broker payloads.

### Stage 2: UI Display

Expose the newly recorded trace data in the replay UI:

- show richer tool definitions in the existing Tool Definition panel
- show available and filtered tools in the Input Material area
- show structured failure diagnostics in Tool Calls

The UI should still load old traces that do not contain the new fields.

## Non-Goals

This feature does not:

- add exit-position tools
- change trading strategy behavior
- change agent prompts
- change how the LLM chooses tools
- add universe/ETF metadata descriptions
- intercept raw Google ADK HTTP traffic
- store full provider request/response transcripts
- expose secrets or API keys

## Trace Data Design

### Rich Tool Surface

Each trace should store a richer `request.tool_surface` entry for each available tool:

```json
{
  "name": "orders_submit_order",
  "description": "Create and submit a LumiBot order...",
  "source": "local",
  "signature": "(symbol: str, quantity: int, side: str = 'buy')",
  "annotations": {
    "symbol": "str",
    "quantity": "int",
    "side": "str"
  },
  "defaults": {
    "side": "buy"
  },
  "metadata": {
    "kind": "builtin",
    "replay_on_cache": true,
    "mutates_trading": true
  },
  "safety_requirements": [
    "Call account_portfolio in the same agent run before submitting an order.",
    "Call account_positions in the same agent run before submitting an order.",
    "Call market_last_price for the same symbol in the same agent run before submitting an order."
  ]
}
```

The exact signature and annotation strings may differ from this example. The requirement is that they are recorded in a deterministic, human-readable, JSON-safe way.

### Cache Stability

Replay cache keys should not become noisy because of trace-only diagnostics. If the existing cache payload needs to stay minimal, the implementation should build a separate trace request payload from the cache payload and store the richer version only in the trace file.

### Tool Availability

The trace should include tool availability information under `request.tool_availability`:

```json
{
  "available": [
    {
      "name": "account_positions",
      "source": "local",
      "metadata": {
        "kind": "builtin"
      },
      "reason": "available"
    }
  ],
  "filtered": [
    {
      "name": "orders_submit_order",
      "metadata": {
        "mutates_trading": true
      },
      "reason": "filtered because allow_trading is false"
    }
  ]
}
```

This helps distinguish two very different cases:

- The LLM did not use a tool that was available.
- The LLM could not use a tool because the runtime filtered it out.

### Tool Failure Diagnostics

Tool result events should include a `diagnostics` object when useful:

```json
{
  "ok": false,
  "error_type": "ORDER_READINESS_REQUIRED",
  "error_message": "ORDER_READINESS_REQUIRED: before submitting an order, call account_portfolio...",
  "missing_requirements": [
    "account_portfolio",
    "account_positions",
    "market_last_price(symbol='TIP')"
  ]
}
```

For successful calls, `diagnostics` may be omitted or recorded as:

```json
{
  "ok": true
}
```

The UI should handle either shape.

## UI Design

### Input Material

The Input Material card should keep the existing available tool names, but add a developer-focused availability section:

```text
Tool Availability

Available
- account_positions
- market_last_price
- orders_open_orders

Filtered
- orders_submit_order
  reason: filtered because allow_trading is false
```

This section should be compact and collapsible with the rest of Input Material.

### Tool Definition Panel

The Tool Definition panel should display every recorded field relevant to tool development:

- name
- source
- whether it was used in this agent run
- call count and batches
- model-facing description
- signature
- annotations
- defaults
- safety requirements
- metadata

The panel should clearly label missing data as not recorded in this trace rather than guessing.

### Tool Calls

When a tool result contains diagnostics, the Tool Calls section should show the diagnostics in human-readable form. For example:

```text
Failure diagnostics
ORDER_READINESS_REQUIRED
Missing requirements:
- account_portfolio
- account_positions
- market_last_price(symbol='TIP')
```

This should appear alongside the existing human-readable explanation, not replace the raw tool output.

## Backward Compatibility

Old traces must still load.

If a trace lacks:

- `signature`
- `annotations`
- `defaults`
- `safety_requirements`
- `tool_availability`
- `diagnostics`

then the UI should show clear empty states and avoid JavaScript errors.

## Redaction and Safety

All new trace fields must pass through the same JSON normalization and redaction path used by existing traces.

The implementation must not record:

- API keys
- broker credentials
- account secrets
- raw authentication headers
- unredacted environment variables

## Testing Requirements

Add or update tests to verify:

1. Trace request payloads include rich tool surface fields.
2. Tool metadata useful for development is preserved in trace-safe form.
3. Mutating trading tools filtered by `allow_trading=False` are recorded with a reason.
4. Order readiness failures produce structured diagnostics.
5. Old traces without the new fields still load.
6. Replay public models expose the new fields.
7. The browser UI renders signature, defaults, safety requirements, metadata, tool availability, and failure diagnostics.
8. Existing replay UI behavior still passes.

## Acceptance Criteria

The feature is complete when:

1. A new trace records richer tool surface data for available tools.
2. A new trace records filtered trading tools and the reason they were filtered.
3. A tool failure caused by order readiness requirements records structured diagnostics.
4. The UI displays the new tool surface fields in the Tool Definition panel.
5. The UI displays available/filtered tool availability in Input Material.
6. The UI displays tool failure diagnostics in Tool Calls.
7. Old traces remain readable.
8. Automated tests cover the new trace and UI behavior.

