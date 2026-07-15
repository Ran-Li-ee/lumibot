# Agent Replay Tool Definition Panel Design

## Goal

Enhance the Agent Workflow Replay UI so a user can click a tool name in the Tool calls section and see the tool definition material that the agent runtime exposed to the LLM for that tool.

This is a debugging and development feature. It is especially useful before adding new agent tools, such as future exit-position tools, because it lets us verify whether the tool name, description, arguments, examples, and guardrail language are clear enough for the LLM to use correctly.

## Current Behavior

The replay UI already shows:

- selected strategy
- selected backtest run
- selected system run
- workflow graph
- selected agent input material
- selected agent tool calls
- selected agent final summary

For tools, the UI currently exposes only:

- available tool count
- available tool names
- actual tool calls by batch
- tool input
- tool output
- human-readable output explanation

The underlying trace files already contain more information under:

```text
request.tool_surface
```

For current traces, each tool surface entry includes:

```text
name
description
source
metadata
```

However, the replay public model currently reduces this to only:

```text
available_tool_names
```

So the UI is losing useful tool definition information that is already present in the trace.

## Important Accuracy Boundary

There are two related but different concepts:

1. **Model-facing tool declaration material**

   This is what helps the LLM decide whether and how to call a tool. In the Google ADK path, Lumibot wraps each `BoundTool` as a callable and sets:

   - wrapper function name from `tool.name`
   - wrapper docstring from `tool.description`
   - wrapper signature from the original callable signature
   - wrapper annotations from the original callable annotations

2. **Replay/tool trace metadata**

   This is extra information useful to us when inspecting traces, such as:

   - `source`
   - `metadata`

   Some metadata may not be directly sent to the LLM provider as part of the callable tool declaration. The UI should label it as replay metadata, not pretend it is necessarily model-facing.

Current traces reliably contain `name`, `description`, `source`, and `metadata`. They may not contain a fully serialized signature or annotations. The first implementation should display all recorded fields and explicitly mark missing model-facing fields as "not recorded in this trace" rather than guessing.

## User Experience

In the selected agent detail view, the Tool calls section should support clicking a tool name.

When the user clicks a tool name, the UI should show a Tool Definition panel below the Tool calls table. The panel should update when another tool name is clicked.

Recommended layout:

```text
Tool calls
┌────────┬──────────────┬──────────────┬────────────────────────┐
│ Batch  │ Tool input   │ Tool output  │ Human explanation      │
├────────┼──────────────┼──────────────┼────────────────────────┤
│ 1      │ account_...  │ ...          │ ...                    │
│ 2      │ orders_...   │ ...          │ ...                    │
└────────┴──────────────┴──────────────┴────────────────────────┘

Tool Definition
orders_submit_order

Model-facing description
Create and submit a LumiBot order...

Recorded function/signature data
Not recorded in this trace.

Replay metadata
source: local
metadata: { "kind": "builtin", "replay_on_cache": true }
```

The panel should not use a browser tooltip. Tool descriptions are too long for tooltips.

## Required Display Content

For the selected tool, display every recorded field relevant to tool understanding:

- `name`
- `description`
- recorded model-facing signature or parameter schema, if present
- recorded annotations, if present
- `source`
- `metadata`

The UI should group these fields so the user can distinguish:

- **Model-facing material**
- **Replay metadata**

For current traces, this likely means:

Model-facing material:

- `name`
- `description`

Replay metadata:

- `source`
- `metadata`

Missing future-facing fields:

- function signature: show "Not recorded in this trace."
- annotations/schema: show "Not recorded in this trace."

## Interaction Rules

- Tool names in Tool calls should be rendered as clickable controls.
- The clickable control text should remain the exact tool name.
- Clicking a tool name selects that tool for the current agent.
- Selecting a different agent clears the selected tool.
- Selecting a different system run clears the selected tool.
- If the selected tool appears multiple times, the panel still shows one definition for the tool, not one per call.
- If a called tool is absent from `tool_surface`, show:

```text
No tool definition was recorded for this tool in the trace.
```

- The panel should preserve line breaks in long descriptions.
- The panel should avoid horizontal overflow for long JSON or long descriptions.

## Data Model Design

Extend the replay public model so each agent exposes the tool surface entries instead of only tool names.

Current public input material:

```json
{
  "available_tool_count": 37,
  "available_tool_names": ["account_positions", "orders_submit_order"]
}
```

Desired public input material:

```json
{
  "available_tool_count": 37,
  "available_tool_names": ["account_positions", "orders_submit_order"],
  "available_tools": [
    {
      "name": "orders_submit_order",
      "description": "Create and submit a LumiBot order...",
      "source": "local",
      "metadata": {
        "kind": "builtin",
        "replay_on_cache": true
      }
    }
  ]
}
```

Sensitive values must still pass through existing redaction helpers.

## Future Trace Improvement

This UI feature should be compatible with a later trace enhancement that records model-facing function signature or parameter schema.

The first implementation does not need to change the runtime tool wrapper or provider request logging. It should, however, render these fields if they appear in future traces:

- `signature`
- `annotations`
- `parameters`
- `schema`
- `input_schema`

This makes the UI future-proof without pretending current traces contain data they do not.

## Non-Goals

This feature does not:

- add exit-position trading tools
- modify how agents choose tools
- modify the prompt sent to the LLM
- intercept or log raw Google ADK provider HTTP requests
- expose secrets or API keys
- replace the existing Tool calls table

## Error Handling

The UI should handle:

- old traces with no `tool_surface`
- tools called but absent from `tool_surface`
- malformed metadata
- non-string descriptions
- very long descriptions
- empty tool call lists

All of these should degrade into readable empty states, not JavaScript errors.

## Testing Requirements

Add or update tests to verify:

- the public replay model includes `available_tools`
- tool surface data is redacted before public exposure
- old traces without `tool_surface` still load
- the browser UI can click a tool name and render the definition panel
- missing definitions render a clear empty-state message
- changing selected agent clears the selected tool definition

Existing replay UI tests should continue to pass.

## Acceptance Criteria

The feature is complete when:

1. In the UI, clicking `orders_submit_order` in Tool calls shows its full recorded description.
2. The panel clearly separates model-facing material from replay metadata.
3. Old traces without tool definition data do not break the UI.
4. No tool definition panel appears before the user selects a tool.
5. Selecting another agent clears the previously selected tool.
6. Tests cover the model and browser behavior.
7. Existing replay UI tests pass.

