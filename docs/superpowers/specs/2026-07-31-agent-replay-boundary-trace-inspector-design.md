# Agent Replay Boundary Trace Inspector

**Last Updated:** 2026-07-31
**Status:** Draft for implementation planning
**Audience:** Lumibot agent-runtime developers, replay-UI developers, and test authors

## Overview

The agent runtime now records detailed `boundary_trace` events for the model-tool
loop. These events describe the transitions between the provider-facing LLM
request/response, LiteLLM, Google ADK, ADK `FunctionTool`, the Lumibot tool
wrapper, and the original local Python tool function.

The Agent Replay UI does not yet expose this information. It currently shows the
agent workflow, input material, normalized tool calls, human-readable tool
outputs, final summaries, tool definitions, and backtest artifacts. This feature
adds a developer-facing inspector that lets a user open a completed backtest run,
select an agent, and inspect the 10 boundary steps around each model/tool
exchange.

The implementation must be staged. Each phase must include focused automated
tests and must pass before the next phase starts.

## Purpose

The goal is to make the saved replay UI answer questions such as:

- What did the model-facing request contain before it was sent to the provider?
- What did the provider response contain when the model requested tools?
- How did LiteLLM and Google ADK represent that response?
- Which local tool call did ADK dispatch, using which call ID?
- What arguments reached the ADK `FunctionTool`, the Lumibot wrapper, and the
  original Python function?
- What did the Python tool return?
- How did the Lumibot wrapper serialize or normalize that result?
- What `FunctionResponse` did ADK receive?
- What model-facing tool result was placed into the next model turn?
- Which payloads are complete, partial, truncated, redacted, not available, or
  stored in sidecar files?

This is a transparency and debugging feature. It must not change trading logic,
agent prompts, tool permissions, tool behavior, order execution, trace recording,
or backtest results.

## Current State

The trace recording layer already persists a `boundary_trace` object in agent
trace files. It records events with fields such as:

- `transition`
- `model_turn_id`
- `tool_batch_id`
- `call_id`
- `status`
- `payload`
- `payload_meta`
- `timestamp`

The stable transition codes are:

- `B01_PROVIDER_TO_LITELLM`
- `B02_LITELLM_TO_ADK`
- `B03_ADK_TO_FUNCTION_TOOL`
- `B04_FUNCTION_TOOL_TO_WRAPPER`
- `B05_WRAPPER_TO_PYTHON_TOOL`
- `B06_PYTHON_TOOL_TO_WRAPPER`
- `B07_WRAPPER_TO_FUNCTION_TOOL`
- `B08_FUNCTION_TOOL_TO_ADK`
- `B09_ADK_TO_LITELLM`
- `B10_LITELLM_TO_PROVIDER`

Some large payloads are stored in compressed sidecar files and referenced by
`payload_meta.sidecar_path`. The UI loader currently accepts traces containing
`boundary_trace`, but it does not expose those events in the public replay data.
The existing normalized `tool_batches` view must remain unchanged.

## Non-Goals

- Do not redesign the existing workflow graph.
- Do not replace the current Tool Calls table.
- Do not expose secrets, authorization headers, API keys, broker credentials, or
  raw unredacted private values.
- Do not attempt to expose hidden model chain-of-thought.
- Do not record external network calls made inside individual tools.
- Do not require rerunning old backtests to view old replay data; old traces
  without `boundary_trace` must still load.
- Do not build a complex visual sequence diagram in the first implementation.
  A table/timeline inspector is enough for this feature.

## Boundary Loop Semantics

Although the boundary codes are named `B01` through `B10`, the chronological
loop around tool use usually starts with a model request:

```text
B09 ADK -> LiteLLM
B10 LiteLLM -> provider
B01 provider -> LiteLLM
B02 LiteLLM -> ADK
B03 ADK -> FunctionTool
B04 FunctionTool -> Lumibot wrapper
B05 Lumibot wrapper -> Python tool
B06 Python tool -> Lumibot wrapper
B07 Lumibot wrapper -> FunctionTool
B08 FunctionTool -> ADK
next B09/B10 includes the tool result in the next model request
```

The UI must avoid implying that every tool call is an isolated linear `B01` to
`B10` sequence. It should group events by `model_turn_id`, then by
`tool_batch_id`, then by `call_id`.

## Phase 1: Replay Data Adapter

### Goal

Expose `boundary_trace` data through the replay UI backend model without
changing the existing normalized tool call behavior.

### Requirements

- Add replay UI model objects for boundary trace data.
- Preserve the raw transition code and stable identifiers:
  `model_turn_id`, `tool_batch_id`, and `call_id`.
- Preserve status and payload metadata:
  `status`, `payload_meta.fidelity`, `payload_meta.semantic_completeness`,
  `payload_meta.redacted`, `payload_meta.truncated`, `payload_meta.pruned`,
  `payload_meta.sidecar_path`, and related omission metadata when present.
- Group events into model turns, tool batches, and tool call spans.
- Keep the existing `tool_batches` output unchanged.
- For traces without `boundary_trace`, expose an empty boundary trace object with
  a clear availability status.
- Do not inline full sidecar payloads into the initial public dataset.

### Testing Gate

Before Phase 2 starts:

- Loader tests must prove that traces with `boundary_trace` expose grouped
  boundary data.
- Loader tests must prove legacy tool batches remain unchanged.
- Loader tests must cover missing `boundary_trace`.
- Loader tests must cover multiple calls to the same tool in one batch and must
  pair by `call_id`, not by tool name.

## Phase 2: Boundary Event Formatting

### Goal

Make every boundary event understandable in the UI without requiring the user to
read raw JSON first.

### Requirements

- Add a formatter that maps each transition code to:
  - a short label;
  - a plain-language explanation;
  - source module;
  - target module;
  - expected payload meaning.
- Display metadata badges for:
  - complete vs partial;
  - redacted;
  - truncated;
  - sidecar-backed;
  - not available;
  - not applicable;
  - error status.
- Generate compact previews for common payload types:
  - model request messages;
  - provider tool call instructions;
  - ADK function calls;
  - FunctionTool arguments;
  - wrapper arguments;
  - Python tool result;
  - serialized model-facing tool result.
- Preserve access to raw JSON preview for developer inspection.
- Never fabricate missing information. If a provider or runtime layer does not
  expose something, the formatter must say that explicitly.

### Testing Gate

Before Phase 3 starts:

- Formatter tests must cover all 10 transition codes.
- Formatter tests must cover success, error, `not_available`, and
  `not_applicable` statuses.
- Formatter tests must cover redacted, truncated, sidecar-backed, and complete
  payload metadata.
- Snapshot or string tests must verify that plain-language explanations are
  stable and not misleading.

## Phase 3: Agent Detail UI Inspector

### Goal

Add a new collapsible section to the selected agent detail page:

```text
LLM <-> Tool Boundary Trace
```

The first version should prioritize clarity and correctness over visual
complexity.

### Requirements

- Keep the existing sections:
  - Input Material
  - Tool Calls
  - Final Summary
- Add the new Boundary Trace section near Tool Calls.
- Show a model-turn list.
- Within each model turn, show:
  - request/response boundary events (`B09`, `B10`, `B01`, `B02`);
  - tool batches;
  - tool calls within each batch;
  - per-call local execution boundaries (`B03` through `B08`).
- Each event row must show:
  - transition code;
  - short label;
  - source -> target;
  - status;
  - payload fidelity badges;
  - compact human-readable preview;
  - an expandable raw JSON preview.
- If no boundary trace exists, show:

```text
This trace does not contain 10-step boundary trace data. It may have been
created before boundary tracing was added.
```

- The section must remain usable when a model turn has no tool calls.
- The section must remain usable when a tool call is missing one or more
  boundary events.

### Testing Gate

Before Phase 4 starts:

- Static UI tests must confirm that the Boundary Trace section renders.
- Browser tests must select a run, select an agent, expand the Boundary Trace
  section, and find expected B-step labels.
- Browser tests must cover the legacy no-boundary-trace fallback.
- Browser tests must confirm existing Input Material, Tool Calls, and Final
  Summary sections still render.

## Phase 4: Sidecar Payload Access

### Goal

Allow the UI to inspect large payloads stored in `boundary_payloads` sidecar
files without loading every large payload into the initial page dataset.

### Requirements

- Add a safe backend endpoint for boundary sidecar payload lookup.
- The endpoint must use an opaque token or event reference, not arbitrary file
  paths supplied by the browser.
- The endpoint must only serve sidecars that belong to configured replay trace
  roots.
- The endpoint must support compressed JSON sidecars.
- The endpoint must apply the same public redaction rules used by the replay
  dataset.
- The UI must show when a full payload is available from sidecar.
- The UI must allow expanding or loading the full sidecar payload on demand.
- If the sidecar file is missing, unreadable, or invalid, the UI must show a
  clear error instead of silently failing.

### Testing Gate

Before Phase 5 starts:

- Server tests must reject arbitrary path traversal attempts.
- Server tests must return valid sidecar payloads for known events.
- Server tests must handle missing sidecars gracefully.
- Browser tests must click a sidecar-backed event and display the loaded full
  payload.
- Browser tests must confirm the initial dataset does not inline large sidecar
  payloads.

## Phase 5: Real Backtest Validation

### Goal

Prove the inspector works on an actual saved backtest run, not only synthetic
fixtures.

### Requirements

- Use an existing real trace with boundary data when available.
- If no suitable real trace is available, run a short one-day benchmark/backtest
  that generates boundary trace data.
- Open the Agent Replay UI.
- Select the strategy, backtest run, system run, and agent.
- Confirm the UI shows:
  - model turns;
  - tool batches;
  - tool calls;
  - all available B-step events;
  - payload metadata badges;
  - sidecar-backed payload access when present;
  - graceful messaging for missing or not-applicable events.
- Record a short validation note in `docs/superpowers/notes/`.

### Testing Gate

This feature is not complete until:

- focused Python tests pass;
- focused browser/static UI tests pass;
- lint checks for changed files pass;
- at least one real replay run is manually or programmatically checked through
  the UI;
- the validation note names the tested artifact or trace root.

## Suggested UI Shape

The initial UI should be text-first:

```text
LLM <-> Tool Boundary Trace
  Model Turn 1
    Model Request / Response
      B09 ADK -> LiteLLM
      B10 LiteLLM -> Provider
      B01 Provider -> LiteLLM
      B02 LiteLLM -> ADK
    Tool Batch 1
      market_last_price / call_abc
        B03 ADK -> FunctionTool
        B04 FunctionTool -> Lumibot Wrapper
        B05 Lumibot Wrapper -> Python Tool
        B06 Python Tool -> Lumibot Wrapper
        B07 Lumibot Wrapper -> FunctionTool
        B08 FunctionTool -> ADK
```

Later visual sequence diagrams can build on this model, but the first feature
should make the data searchable, expandable, and trustworthy.

## Data Safety

- Reuse existing replay redaction utilities.
- Do not expose raw headers, API keys, credentials, or environment variables.
- Treat sidecar paths as internal server-side references.
- Clearly mark redacted payloads so developers understand why values are absent.
- Clearly mark partial payloads so developers do not mistake truncated data for
  complete data.

## Backward Compatibility

- Existing replay pages must continue to work for old traces.
- Existing run, strategy, and system-run selectors must remain unchanged.
- Existing artifact buttons must remain unchanged.
- Existing tool definition and tool call panels must remain unchanged except for
  any optional cross-links to the new Boundary Trace section.

## Open Implementation Questions

These questions should be resolved in the implementation plan:

- Whether sidecar payload lookup should use event IDs generated by the loader or
  deterministic hashes derived from trace path plus event span ID.
- Whether boundary events should be grouped entirely in Python or partially in
  JavaScript.
- Whether the first UI should default-open the first model turn or stay fully
  collapsed.
- Whether tool call rows in the existing Tool Calls table should link directly
  to matching boundary call spans.

## Acceptance Criteria

The feature is accepted when a developer can:

1. Start `scripts/agent_trace_ui.py`.
2. Select a backtest run that contains boundary trace data.
3. Click an agent.
4. Open `LLM <-> Tool Boundary Trace`.
5. See model turns and tool call spans grouped by stable IDs.
6. Inspect the available B01-B10 events for a tool exchange.
7. Understand each boundary in plain language.
8. Open raw JSON previews when needed.
9. Load sidecar-backed payloads safely.
10. Distinguish complete, partial, redacted, unavailable, and not-applicable
    trace data.

