# Agent Replay Input Tool Surface Panel Design

## Goal

Move the primary tool-definition inspection entry point from the `Tool Calls` card to the `Input Material` card, specifically the `Available Tool Names` subsection.

This lets a developer inspect the complete tool surface that was available to the LLM for an agent run, including tools that were used and tools that were not used.

## Problem

The current UI makes tool names clickable inside `Tool Calls`, then renders `Tool Definition` at the bottom of the `Tool Calls` card.

That has two problems:

- A user can click a tool and not notice the panel because it appears below all batches.
- `Tool Calls` only represents tools the LLM actually used. It does not show the full set of tools the LLM was allowed to use.

For tool development, the more important question is: "What exactly did the LLM see as available tools before deciding what to call?"

## Desired Behavior

When an agent is selected, `Input Material` should show `Available Tool Names` as clickable tool chips instead of a comma-separated text block.

Clicking any available tool chip should show a `Tool Definition` panel inside the `Input Material` card, directly under the available tool list.

The panel should show the same model-facing data already exposed by the replay dataset:

- tool name
- source
- model-facing description
- recorded function / parameter data
- recorded annotations
- replay metadata

The panel should also show usage evidence for the selected agent run:

- whether this tool was called in this agent run
- how many times it was called
- which tool-call batches contain the tool

Tools that are available but unused should still be clickable. Their panel should say that they were available to the LLM but not called in this agent run.

## Tool Calls Interaction

Tool names inside `Tool Calls` should remain clickable, but they should select the same shared tool definition state as the `Input Material` tool chips.

After clicking a tool name in `Tool Calls`, the UI should reveal the selected tool definition in `Input Material`. The implementation may scroll or focus the input-side panel if doing so is stable in browser tests.

The `Tool Calls` card should no longer be the only place where the tool definition panel appears.

## Visual Rules

Available tools should be compact and readable:

- used tools should be visually distinguishable from unused tools
- used tools may show a small call count, such as `market_last_price x3`
- unused tools should remain visible, not hidden or disabled
- the selected tool should have a clear active state

The UI should remain plain and utilitarian, matching the existing Agent Replay style.

## Edge Cases

If `available_tool_names` is empty, show the existing empty state.

If a tool name exists in `available_tool_names` but no full definition exists in `available_tools`, clicking it should show:

`No tool definition was recorded for this tool in the trace.`

If a tool appears in `Tool Calls` but is missing from `available_tool_names`, clicking it should still show the missing-definition fallback or its recorded definition if present.

When switching agents, system runs, backtest runs, or strategies, the selected tool definition should clear.

## Acceptance Criteria

- `Input Material > Available Tool Names` renders clickable tool chips.
- Clicking an available tool chip displays the `Tool Definition` panel inside `Input Material`.
- The panel shows model-facing tool details from `input_material.available_tools`.
- The panel shows whether the selected tool was used, call count, and batch numbers.
- Available but unused tools are clickable and show `Used in this agent run: No`.
- Clicking a tool inside `Tool Calls` selects the same tool definition shown in `Input Material`.
- The old behavior where the only visible panel is at the bottom of `Tool Calls` is removed or no longer required.
- Browser tests cover clicking a used tool from `Input Material`, clicking an unused tool from `Input Material`, and clicking a tool from `Tool Calls`.
- Existing replay UI tests still pass.
