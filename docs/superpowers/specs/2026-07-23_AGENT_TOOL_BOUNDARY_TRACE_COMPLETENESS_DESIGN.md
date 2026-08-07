# Agent-Tool Boundary Trace Completeness

Detailed design for recording every semantically meaningful transition between LiteLLM, Google ADK, FunctionTool, the Lumibot tool wrapper, and local Python tools.

**Last Updated:** 2026-07-23
**Status:** Draft for user review
**Audience:** Lumibot agent-runtime developers, replay-UI developers, and test authors

## Overview

Lumibot currently records agent prompts, available tools, normalized tool calls,
normalized tool results, usage, warnings, and final summaries. That is enough to
replay the broad agent workflow, but it is not enough to explain every
transformation between a remote model and a local Python tool.

This design adds a detailed boundary trace for the following closed loop:

```text
OpenAI model response
  -> LiteLLM provider response
  -> Google ADK LlmResponse
  -> ADK FunctionTool dispatch
  -> Lumibot tool wrapper invocation
  -> local Python tool execution
  -> Lumibot result serialization
  -> ADK FunctionResponse construction
  -> Google ADK next-turn LlmRequest
  -> LiteLLM provider-facing request
  -> OpenAI model request
```

The new trace must preserve the values before and after each meaningful
transformation. It must also identify model turns, parallel tool batches, and
individual tool calls without relying on tool names or list position.

This spec deliberately stops at the provider-adapter boundary. It records the
request that LiteLLM prepares for OpenAI and the response LiteLLM receives from
OpenAI, but it does not capture raw HTTP packets, authorization headers, hidden
model reasoning, or OpenAI server internals.

## Purpose

The primary goal is to make agent-tool execution explainable enough that a
developer can answer all of the following from a saved trace:

- What exactly did the model return when it requested a tool?
- How did LiteLLM convert that response for Google ADK?
- Which FunctionTool did ADK schedule, in which model turn and parallel batch?
- What arguments did the model provide?
- What arguments survived ADK validation, type conversion, and filtering?
- What arguments did the Lumibot wrapper pass to the original Python function?
- What did the Python function return before Lumibot serialized it?
- How did serialization, error handling, and context pruning change the result?
- What FunctionResponse did ADK construct, and which call ID did it use?
- What exact model-facing tool result was placed in the next LLM request?
- What provider-facing message did LiteLLM prepare for OpenAI?
- Did the run use a real model/tool exchange or replay cached results?

The resulting trace is a developer observability artifact. It is not intended
to change agent decisions, tool permissions, tool behavior, or trading logic.

## Current State

The current runtime provides useful but incomplete observability:

- `AgentHandle.run()` records prompt material, context, runtime context, model,
  tool surface, normalized ADK events, warnings, usage, timing, and summary.
- `_normalize_event()` converts ADK parts into `tool_call`, `tool_result`,
  `text`, `thinking`, and `usage` events.
- `_wrap_tool_callable()` adapts each bound Lumibot tool into a callable that
  ADK can expose as a FunctionTool.
- The replay loader infers tool batches from event ordering and pairs results to
  calls primarily by tool name.

The current normalized event loses information that exists in the underlying
ADK objects:

- function call ID;
- function response ID;
- model turn number;
- tool batch ID;
- ADK invocation ID and event ID;
- raw provider response;
- provider-facing request;
- arguments after FunctionTool validation and filtering;
- original Python return value before JSON-safe conversion;
- the difference between original, serialized, pruned, and model-facing tool
  results;
- per-boundary timing;
- provider retry/attempt information when exposed.

Tool-name pairing is not sufficient. A model can call the same tool more than
once in one parallel batch with different arguments. Those calls must be paired
with results by call ID.

## Goals

- Record every semantically meaningful boundary in the model-tool loop.
- Preserve before-and-after values for every transformation.
- Use stable identifiers to connect a model request, model response, tool batch,
  tool call, local execution, function response, and next model request.
- Support multiple model turns in one agent run.
- Support several tool calls in one model turn, including repeated calls to the
  same tool.
- Distinguish call order from completion order for parallel tools.
- Record both successful and failed model/tool exchanges.
- Record whether a payload was redacted, normalized, pruned, truncated, or
  stored in a sidecar artifact.
- Keep the existing trace fields and replay behavior backward compatible.
- Ensure tracing cannot alter a prompt, tool argument, tool result, agent
  summary, order, exception policy, or runtime control flow.
- Make the trace data sufficient for a later UI implementation without
  prescribing that UI in this change.

## Non-Goals

- Do not design or implement the detailed UI presentation.
- Do not capture raw TCP/TLS packets or exact HTTP wire bytes.
- Do not persist authorization headers, API keys, cookies, broker credentials,
  or other secrets.
- Do not attempt to expose hidden model chain-of-thought.
- Do not trace OpenAI server internals, routing, caching internals, or private
  reasoning.
- Do not trace external network calls made inside individual tools, such as a
  FRED, Alpaca, SEC, or market-data HTTP request. That may be a future
  tool-internal observability layer.
- Do not change existing tool descriptions, permissions, prompts, or trading
  behavior.
- Do not replace Google ADK or LiteLLM.
- Do not introduce a new environment variable solely to control tests or
  tracing.

## Terminology

### Agent Run

One invocation of a Lumibot `AgentHandle`. An agent run can contain several
model turns and several tool batches.

### Model Turn

One outbound request to the model followed by one model response. A response may
contain final text, one tool call, or several parallel tool calls.

### Tool Batch

All function calls returned by the model in the same model response. ADK may
execute these calls concurrently.

### Tool Call

One model-requested function invocation, identified by its function call ID.

### Boundary Event

An immutable trace record describing data moving from one module to the next.
A boundary event records the payload visible at that boundary, not a
reconstruction performed later by the UI.

### Provider-Facing Request

The structured request LiteLLM prepares for the selected provider, including
messages, tool declarations, and supported generation parameters after the ADK
request has been translated. This is not guaranteed to be byte-identical to the
final encrypted HTTP request.

### Raw Tool Result

The value returned by the original Python tool before Lumibot applies
JSON-safe conversion. A raw result may not itself be JSON serializable.

### Model-Facing Tool Result

The FunctionResponse content that is actually included in the next model
request after serialization, callbacks, and any context-size pruning.

## Scope Boundary

This implementation covers these modules and transitions:

```text
Provider/OpenAI abstraction
  <-> LiteLLM adapter
  <-> Google ADK LlmRequest/LlmResponse
  <-> ADK FunctionTool
  <-> Lumibot tool wrapper
  <-> original local Python tool
```

It must work for the existing OpenAI-through-LiteLLM path. The data model must
remain provider-neutral so another LiteLLM provider can use the same trace
schema. Native model paths that do not use LiteLLM must mark LiteLLM-specific
transitions as `not_applicable`; they must not fabricate provider events.

## Required Boundary Transitions

Every transition has a stable transition code. These codes are part of the
trace contract and must not be renamed casually.

### B01: Provider Response to LiteLLM

Capture the provider response exposed to LiteLLM before it is converted into an
ADK `LlmResponse`.

Required fields:

- provider model name;
- provider response ID when exposed;
- provider finish reason;
- provider tool-call IDs;
- provider tool names;
- provider tool argument strings or structures;
- visible assistant text;
- provider-exposed reasoning summary or thought parts, if any;
- usage metadata;
- provider attempt number when exposed;
- cache metadata when exposed;
- error type and message for failed attempts.

The trace must explicitly state that this is a provider-adapter snapshot, not a
raw HTTP response.

### B02: LiteLLM Response to Google ADK

Capture the complete ADK `LlmResponse` yielded by LiteLLM.

Required fields:

- model turn ID;
- ADK response content and parts;
- each `FunctionCall.id`;
- each function name;
- each parsed argument object;
- visible text parts;
- thought parts exposed by the provider;
- finish reason;
- usage metadata;
- model version and interaction ID when exposed.

The trace must make it possible to compare B01 and B02 and identify any
provider-to-ADK conversion.

### B03: Google ADK Dispatch to FunctionTool

Capture the dispatch decision before each FunctionTool executes.

Required fields:

- ADK invocation ID;
- ADK event ID;
- model turn ID;
- tool batch ID;
- function call ID;
- call sequence within the model response;
- tool name;
- model-supplied arguments;
- scheduled timestamp;
- whether the batch contains more than one call;
- whether ADK scheduled the call concurrently;
- tool lookup status;
- selected FunctionTool identity.

### B04: FunctionTool to Lumibot Wrapper

Capture FunctionTool argument processing and the values passed to the Lumibot
wrapper.

Required fields:

- function call ID;
- original model-supplied arguments;
- arguments after ADK preprocessing;
- arguments removed because they are not accepted by the callable;
- arguments converted to another Python type;
- missing mandatory arguments;
- validation error, if any;
- confirmation status, when relevant;
- final keyword and positional arguments delivered to the wrapper.

The instrumentation must not reimplement ADK validation independently and then
claim the reconstructed result is authoritative. It must observe the values at
the real invocation boundary.

### B05: Lumibot Wrapper to Original Python Tool

Capture the exact local function invocation immediately before the original
tool runs.

Required fields:

- function call ID;
- bound Lumibot tool name;
- source type, such as built-in or MCP;
- original callable module and qualified name when safely available;
- positional arguments;
- keyword arguments;
- effective arguments with Python defaults applied for observability;
- tool-context identifiers available to the wrapper;
- invocation start wall-clock timestamp;
- invocation start monotonic timestamp;
- calling thread or task identifier when safely available.

Default arguments are observability metadata only. Applying defaults for the
trace must not change the actual function call.

### B06: Original Python Tool to Lumibot Wrapper

Capture the original Python return or exception before Lumibot converts it.

Required fields for success:

- function call ID;
- raw Python type;
- safe structural metadata, such as mapping keys, list length, dataframe shape,
  or scalar type;
- raw value when it is safely serializable;
- safe representation preview when it is not serializable;
- content hash when a stable serialized representation can be produced;
- completion wall-clock timestamp;
- elapsed duration;
- success status.

Required fields for failure:

- exception class;
- exception message after redaction;
- redacted traceback;
- completion timestamp;
- elapsed duration;
- failure status.

Tracing must not consume iterators, generators, streams, or one-shot objects in
order to inspect them.

### B07: Lumibot Wrapper to FunctionTool

Capture the exact value returned by the wrapper after Lumibot processing.

Required fields:

- function call ID;
- JSON-safe serialized result;
- whether serialization changed the type or shape;
- serialization diagnostics;
- wrapper-generated tool error payload, if used;
- redaction status;
- payload storage reference when the full payload is stored outside the main
  trace document.

The wrapper must retain the existing behavior of converting exceptions into the
current structured tool-error payload unless a separate approved change alters
that policy.

### B08: FunctionTool to Google ADK

Capture the ADK FunctionResponse produced from the wrapper result.

Required fields:

- function call ID;
- function response ID;
- function name;
- FunctionResponse payload;
- whether ADK wrapped a non-dict result inside a `result` field;
- response event ID;
- response creation timestamp;
- merged parallel-response event ID when applicable;
- response completion order within the batch.

This event must prove that every response ID matches the corresponding function
call ID.

### B09: Google ADK to LiteLLM

Capture each complete `LlmRequest` immediately before LiteLLM handles it.

Required fields:

- model turn ID;
- previous model turn ID;
- system instruction;
- conversation contents and roles;
- assistant function calls included in history;
- function responses included in history;
- function call and response IDs;
- tool declarations;
- generation configuration;
- model name;
- model-facing tool result;
- context-pruning diagnostics;
- payloads before and after Lumibot context pruning;
- request timestamp.

The authoritative B09 request is the request observed after all Lumibot
before-model mutations that are intended to affect the provider request.
Callback ordering must guarantee that the recorder sees the final model-facing
ADK request.

### B10: LiteLLM to Provider/OpenAI

Capture the provider-facing request produced by LiteLLM.

Required fields:

- model turn ID;
- provider model name;
- translated message list;
- translated assistant tool-call messages;
- translated tool-result messages;
- tool-call IDs;
- translated tool declarations;
- response format;
- supported generation parameters;
- timeout;
- prompt-cache metadata that is safe to persist;
- request attempt number when exposed;
- request start timestamp.

The trace must exclude:

- API keys;
- authorization headers;
- cookies;
- full arbitrary headers;
- client objects;
- transport connection details;
- sensitive provider SDK internals.

The trace must label this event `provider_adapter_request`, not
`raw_http_request`.

## Canonical Identifiers

Every boundary event must include identifiers sufficient for exact correlation.

```json
{
  "agent_run_id": "stable ID for one AgentHandle invocation",
  "adk_invocation_id": "ADK invocation ID when available",
  "model_turn_id": "agent_run_id:turn:0001",
  "provider_attempt": 1,
  "tool_batch_id": "model_turn_id:batch:0001",
  "call_id": "provider/ADK function call ID",
  "span_id": "unique boundary event ID",
  "parent_span_id": "preceding logical event ID"
}
```

Rules:

- `agent_run_id` must not depend only on the agent name or simulated date.
- `model_turn_id` increments for every real model request in an agent run.
- `tool_batch_id` is shared by all function calls from one model response.
- `call_id` uses the provider/ADK function call ID when available.
- A deterministic local fallback call ID may be generated only when the
  provider supplies no ID. The trace must mark it as generated.
- Tool calls and responses must be paired by call ID, never only by tool name.
- `span_id` must be unique within the trace.
- Parallel completion order must not overwrite model call order.

## Boundary Event Schema

The trace adds a new top-level object without removing the existing event lists:

```json
{
  "boundary_trace": {
    "schema_version": 1,
    "agent_run_id": "...",
    "capture_scope": "semantic_boundaries",
    "provider_wire_capture": false,
    "events": [],
    "diagnostics": []
  }
}
```

Each event uses this common envelope:

```json
{
  "schema_version": 1,
  "sequence": 17,
  "transition": "B05_WRAPPER_TO_PYTHON_TOOL",
  "from_module": "lumibot_tool_wrapper",
  "to_module": "python_tool",
  "status": "success",
  "agent_run_id": "...",
  "adk_invocation_id": "...",
  "model_turn_id": "...",
  "tool_batch_id": "...",
  "call_id": "call_123",
  "span_id": "...",
  "parent_span_id": "...",
  "started_at": "2026-07-23T01:23:45.123456Z",
  "ended_at": "2026-07-23T01:23:45.234567Z",
  "duration_ms": 111.111,
  "payload": {},
  "payload_meta": {
    "representation": "json",
    "byte_count": 1234,
    "sha256": "...",
    "redacted": false,
    "pruned": false,
    "truncated": false,
    "sidecar_path": null
  },
  "error": null
}
```

Not every field applies to every transition. Non-applicable fields must be
`null` or omitted consistently according to the serializer contract.

## Payload Fidelity

Every payload snapshot must declare its fidelity:

- `framework_exact`: exact data visible at that Python framework boundary after
  safe copying and redaction;
- `normalized_copy`: semantically equivalent JSON-safe conversion;
- `descriptor_only`: type, shape, size, hash, and preview because the object
  cannot safely be copied;
- `not_available`: the boundary does not expose the value;
- `not_applicable`: the active runtime path does not use this boundary.

The trace must never describe a reconstructed or inferred value as
`framework_exact`.

## Large Payload Handling

Historical tables, filings, news, model prompts, and tool results can be large.
Silently dropping or clipping them would undermine this feature.

Required behavior:

- Keep the main trace document reasonably sized.
- Store large full semantic payloads as compressed JSON sidecars under the same
  agent-runtime artifact root.
- Put a redacted preview, byte count, SHA-256 hash, compression type, and
  relative sidecar path in the boundary event.
- Sidecar paths must be relative to the artifact root and portable with the
  backtest artifact folder.
- A sidecar write failure must create a trace diagnostic and a marked truncated
  preview; it must not fail the agent run.
- Non-JSON raw objects use descriptor-only recording unless a safe existing
  serializer is available.
- The main trace and sidecars must use atomic write/rename where practical.

The exact inline-size threshold is an implementation detail and must be a
stable code constant or explicit runtime configuration, not an undocumented
environment variable.

## Redaction and Secret Safety

Boundary tracing substantially increases the amount of persisted data. Secret
safety is therefore a hard requirement.

Before any boundary payload reaches disk:

- apply centralized recursive redaction;
- redact values for known credential keys;
- redact bearer tokens and API-key patterns;
- exclude authorization headers entirely;
- exclude cookies and provider client objects;
- preserve field names where useful but replace sensitive values;
- record that redaction occurred;
- never copy credentials from local secret files into trace metadata.

Redaction must happen before hashing a persisted payload so the trace hash
describes the saved redacted content, not secret content.

The replay UI may apply a second defensive redaction pass, but UI-time redaction
must not be the only protection.

## Model Reasoning Boundary

The recorder may save:

- visible assistant text;
- provider-exposed reasoning summaries;
- ADK thought parts explicitly returned by the provider.

The recorder must not:

- request hidden chain-of-thought solely for tracing;
- claim that tool choices reveal the complete model reasoning;
- synthesize missing reasoning and store it as model output.

When no reasoning text is exposed, the trace should state
`reasoning_visibility: not_exposed`.

## Argument Transformation Requirements

For each tool call, the trace must preserve these distinct snapshots when they
exist:

```text
model_arguments
  -> adk_function_call_arguments
  -> function_tool_preprocessed_arguments
  -> function_tool_filtered_arguments
  -> wrapper_received_arguments
  -> effective_python_arguments_with_defaults
```

The trace must also list transformations:

```json
{
  "removed_arguments": ["unknown_parameter"],
  "converted_arguments": [
    {
      "name": "request",
      "from_type": "dict",
      "to_type": "OrderRequest"
    }
  ],
  "defaulted_arguments": [
    {
      "name": "asset_type",
      "value": "stock"
    }
  ]
}
```

If ADK rejects the call before invoking the wrapper, B04 must record the
validation failure and B05/B06/B07 must be absent with an explicit diagnostic
that local execution did not occur.

## Result Transformation Requirements

For each successful tool call, the trace must preserve these distinct snapshots
when they exist:

```text
raw_python_result
  -> lumibot_json_safe_result
  -> function_tool_result
  -> adk_function_response
  -> pruned_model_facing_result
  -> provider_tool_result_message
```

The trace must record exactly where a change occurred. It must not show the
pruned model-facing result as though it were the original tool return.

For failures, the trace must distinguish:

- original Python exception;
- Lumibot wrapper-generated tool-error payload;
- ADK tool error or validation response;
- provider/model error;
- trace-recorder error.

## Parallel Tool Calls

Parallel calls are first-class behavior.

Required behavior:

- Preserve model call order for all calls in a batch.
- Preserve independent start, end, and duration values.
- Preserve actual completion order.
- Pair every response to its call by call ID.
- Support multiple calls to the same tool in one batch.
- Record a merged ADK function-response event without losing the individual
  response events.
- Do not infer concurrency solely because calls appear adjacent.
- Mark whether concurrency is confirmed by the ADK dispatch path.

A required regression fixture must include at least two parallel calls to the
same tool with different arguments and deliberately reversed completion order.

## Context Pruning

Lumibot can prune older or oversized tool results before a model request.
Tracing must distinguish:

- original serialized tool result;
- result before model-context pruning;
- result after pruning;
- replacement notice inserted by pruning;
- number of characters or bytes removed;
- reason for pruning;
- the final request observed by LiteLLM.

Callback ordering must be deterministic:

1. runtime mutations intended to change the request;
2. context pruning;
3. final ADK-request boundary capture;
4. LiteLLM translation and provider-request capture.

The recorder itself must not trigger additional pruning.

## Caching, Replay, and Retries

### Replay Cache

When an agent result is loaded from the existing replay cache:

- do not fabricate a new provider/model exchange;
- record `execution_source: replay_cache`;
- retain or reference the boundary trace from the original execution when it is
  available;
- clearly mark whether a displayed boundary event is original or replayed;
- do not execute tools again merely to recreate missing boundary details.

Older cache entries without a boundary trace remain valid and must be marked
`boundary_trace_status: unavailable_legacy_cache`.

### Provider Retries

When LiteLLM exposes retry attempts:

- record each attempt separately;
- use the same model turn ID with increasing `provider_attempt`;
- record attempt errors and timing;
- identify the attempt that produced the accepted response.

When retry details are not exposed, record
`provider_retry_visibility: unavailable`; do not infer a count.

### Tool Retries

If the model calls a tool again after an error, the second call is a new call ID
and must be recorded as a separate tool call, even when the name and arguments
are identical.

## Failure Isolation

Boundary tracing must be fail-open with respect to the agent runtime:

- a recorder serialization failure must not change the tool result;
- a sidecar write failure must not prevent a trade or model response;
- a redaction failure must prevent the affected payload from being persisted,
  but must not fail the agent run;
- a recorder callback must return `None` unless the existing callback contract
  requires a transformed value for unrelated runtime behavior;
- recorder diagnostics must be added to the trace when safe;
- tracing must not swallow an exception that the runtime would otherwise raise;
- tracing must not convert a successful tool call into a failed call.

Non-fatal model/tool failures handled by the runtime must still produce a
partial boundary trace. Abrupt process termination, power loss, or forced kill
is not guaranteed to produce a finalized trace.

## Runtime Architecture

### BoundaryTraceCollector

Introduce one collector per agent run. It owns:

- canonical identifiers;
- sequence allocation;
- model-turn and batch counters;
- boundary event accumulation;
- sidecar persistence;
- diagnostics;
- final trace export.

The collector must not be a process-global mutable singleton. Parallel agents
and parallel tool calls must not write into each other's traces.

### ADK Callback Composition

The runtime already uses callbacks for context pruning. The implementation must
compose observability callbacks with existing callbacks rather than replacing
them.

Required callback responsibilities:

- `before_model_callback`: capture the final ADK `LlmRequest` after pruning;
- `after_model_callback`: capture the ADK `LlmResponse`;
- `before_tool_callback`: capture ADK dispatch information and raw model
  arguments;
- `after_tool_callback`: capture the result visible after tool execution and
  any model-context result transformation;
- model/tool error callbacks where needed for failures.

Callback ordering must be covered by tests.

### FunctionTool Observation

ADK's public before-tool callback observes model arguments before all internal
FunctionTool preprocessing. To capture authoritative post-validation arguments,
the implementation must instrument the actual invocation boundary.

Acceptable approaches include:

- a narrow Lumibot-owned FunctionTool subclass that delegates to ADK and records
  before/after values;
- a Lumibot-owned adapter around FunctionTool invocation;
- wrapper-bound observations that compare ADK raw arguments with the arguments
  actually received by the wrapper.

The implementation must not monkey-patch Google ADK process-wide.

### Lumibot Wrapper Observation

Refactor the wrapper conceptually from:

```python
result = json_safe(original(*args, **kwargs))
```

to:

```python
raw_result = original(*args, **kwargs)
serialized_result = json_safe(raw_result)
```

The actual implementation may differ, but it must provide distinct B06 and B07
snapshots while preserving current return and exception behavior.

### LiteLLM Observation

LiteLLM observation must be scoped to the current model instance or request.
It must not depend on unscoped process-global callbacks that can mix concurrent
agent runs.

The selected integration must capture:

- the provider-facing completion input after ADK-to-LiteLLM translation;
- the provider response as exposed to LiteLLM;
- provider attempt metadata when supported;
- correlation metadata back to the agent run and model turn.

If the installed LiteLLM API cannot expose a field reliably, the trace must mark
that field unavailable. The implementation must not depend on unsupported
private internals without a compatibility test that fails clearly after a
dependency upgrade.

## Persistence and Backward Compatibility

- Keep existing top-level `events`, `tool_calls`, `tool_results`, `request`,
  `summary`, `usage`, `warnings`, and timing fields.
- Add `boundary_trace`; do not replace the current normalized event stream.
- Continue loading legacy traces without `boundary_trace`.
- The replay loader must not require boundary data until the later UI feature
  explicitly consumes it.
- Replay caches written before this feature remain readable.
- New boundary sidecars must live under the selected run's existing
  `agent_runtime` artifact root.
- Persist only relative sidecar paths in trace JSON.
- No external telemetry service is introduced.

## Performance Requirements

- Tracing must add no model calls and no tool calls.
- Tracing must not perform network requests.
- Tracing must avoid deep-copying arbitrarily large or one-shot raw objects.
- Expensive serialization should occur once per payload where practical.
- Hashing and compression may run outside the critical tool-call path when that
  does not risk losing required data.
- Boundary timing must measure the real operation, excluding optional deferred
  sidecar compression where possible.
- The implementation plan must include a benchmark or deterministic test for
  collector overhead on many small parallel tool calls.

No strict latency threshold is set in this spec because filesystem and payload
sizes vary. The implementation must report measured overhead and avoid obvious
per-event quadratic work.

## Security Requirements

- No API key or authorization header may appear in the committed tests or
  generated fixtures.
- Provider fixtures must use synthetic placeholders.
- Trace tests must include secret-like values and prove they are redacted
  before persistence.
- Absolute machine-specific paths must not be persisted in portable trace
  payloads.
- Tool callable metadata may contain module and qualified function names, but
  must not persist arbitrary source-code locals or closures.
- Trace files remain local artifacts and are not uploaded automatically.

## Test Strategy

### Unit Tests

Add deterministic tests for:

- boundary-event schema and identifier generation;
- stable model-turn and tool-batch assignment;
- provider call IDs preserved through B01-B10;
- generated fallback call IDs clearly marked;
- FunctionTool argument filtering;
- required-argument validation failure;
- type conversion before wrapper invocation;
- Python default-value observation without behavior change;
- raw scalar, mapping, list, datetime, Decimal, and model-object results;
- descriptor-only handling for non-serializable and one-shot objects;
- wrapper exception capture and existing tool-error behavior;
- non-dict FunctionTool result wrapping;
- context pruning with before/after payloads;
- model-facing result matching the final next-turn request;
- redaction before disk persistence;
- compressed sidecar persistence and hash validation;
- sidecar failure producing diagnostics without failing the agent;
- recorder failure isolation;
- LiteLLM request/response correlation without real network access;
- legacy trace and cache compatibility.

### Parallel-Call Regression Test

Use a fake model response containing two calls to the same tool:

```text
call_A -> market_last_price(symbol="QQQ")
call_B -> market_last_price(symbol="SPY")
```

Make `call_B` complete before `call_A`. Verify:

- both calls share one tool batch ID;
- call sequence remains A then B;
- completion sequence is B then A;
- each result is paired by call ID;
- merged ADK response retains both individual mappings;
- no code path pairs results by tool name alone.

### Multi-Turn Regression Test

Simulate:

1. model turn 1 requests two tools;
2. both tools return;
3. model turn 2 receives both FunctionResponses and requests one more tool;
4. the final tool returns;
5. model turn 3 returns final text.

Verify that B09 and B10 for turns 2 and 3 contain the correct prior calls and
responses.

### Integration Test

Run the Google ADK runtime against a deterministic fake LiteLLM/provider
adapter. Verify all B01-B10 events without a real API key or external network.

The integration test must compare:

- provider response;
- ADK response;
- dispatch;
- validated wrapper arguments;
- raw tool result;
- serialized result;
- ADK FunctionResponse;
- next ADK request;
- provider-facing next request.

### Backtest Acceptance

After unit and integration tests pass, run one one-day agent backtest using the
normal benchmark runner and an explicitly supplied developer API key outside
the repository.

Verify:

- the run still produces the same agent/tool behavior expected by the strategy;
- a boundary trace exists for each real agent call;
- a multi-call batch is correlated by call ID;
- the final model-facing tool result can be found in the next model request;
- no credential appears in trace JSON or sidecars;
- the existing replay UI still loads the run even though it does not yet
  present the new boundary events.

The real-provider acceptance run is manual and must not be required in normal
credential-free CI.

## Acceptance Criteria

The feature is complete when all of the following are true:

1. New real model executions emit a versioned `boundary_trace`.
2. B01-B10 are recorded when applicable.
3. Every real model request has a stable model turn ID.
4. Every model-produced tool call has a preserved or explicitly generated call
   ID.
5. Every tool response is paired to its call by call ID.
6. Parallel same-name tool calls with reversed completion order are represented
   correctly.
7. Model arguments, validated arguments, wrapper arguments, and effective
   Python arguments are distinguishable.
8. Raw tool result metadata, serialized result, ADK FunctionResponse, pruned
   result, and provider-facing tool message are distinguishable.
9. The final B09 snapshot matches what LiteLLM receives after Lumibot pruning.
10. B10 records the provider-facing semantic request but is not labeled as raw
    HTTP.
11. Provider responses, errors, usage, and exposed retry attempts are
    correlated to the correct model turn.
12. Cached replay never masquerades as a new OpenAI/tool exchange.
13. Legacy traces and replay caches still load.
14. Trace or sidecar failure does not alter agent or trading behavior.
15. Secret-like test values are redacted before persistence.
16. Existing targeted agent-runtime and replay tests continue to pass.
17. A credential-free ADK/LiteLLM integration test proves the complete loop.
18. A manual one-day backtest confirms the trace works with the current OpenAI
    model path.

## Implementation Planning Boundaries

The future implementation plan should decompose this work into independently
reviewable stages:

1. boundary schema, identifiers, collector, redaction, and sidecars;
2. Lumibot wrapper and FunctionTool boundary capture;
3. ADK model-request, model-response, dispatch, and FunctionResponse capture;
4. LiteLLM provider-adapter request/response capture;
5. trace persistence, replay/cache compatibility, and failure paths;
6. deterministic unit/integration tests;
7. one-day real-provider backtest validation.

The plan must use test-driven development for each behavioral slice. It must not
start with UI rendering. UI consumption of `boundary_trace` is a separate
follow-up spec after this trace contract has been validated with real artifacts.

## Risks and Mitigations

### Risk: Trace Volume Becomes Excessive

Mitigation: compressed sidecars, hashes, previews, stable thresholds, and no
duplicate serialization.

### Risk: Traces Persist Secrets

Mitigation: centralized redaction before persistence, excluded headers,
synthetic secret tests, and secondary UI redaction.

### Risk: Instrumentation Changes Runtime Behavior

Mitigation: fail-open collector, non-mutating snapshots, callback-order tests,
and comparison tests against the existing wrapper behavior.

### Risk: Dependency Upgrades Break LiteLLM or ADK Hooks

Mitigation: narrow adapters, compatibility tests, explicit unavailable status,
and no process-wide monkey patches.

### Risk: Parallel Calls Are Mispaired

Mitigation: call-ID-first correlation and a same-name reversed-completion
regression test.

### Risk: "Raw" Labels Overstate Fidelity

Mitigation: explicit fidelity labels and provider-adapter terminology. Only
values observed directly at a framework boundary may be called
`framework_exact`.

### Risk: Non-Serializable Raw Values Are Consumed or Mutated

Mitigation: descriptor-only capture for streams, generators, and one-shot
objects; never iterate solely for tracing.

## Final Design Decisions

- Capture semantic framework boundaries, not raw HTTP bytes.
- Capture all B01-B10 transitions when they apply.
- Keep existing normalized trace fields for backward compatibility.
- Add a versioned `boundary_trace` object and portable compressed sidecars.
- Correlate by model turn, tool batch, and function call ID.
- Preserve distinct argument and result transformation stages.
- Record final post-pruning model input.
- Use scoped adapters and callbacks, never global monkey patches.
- Redact before persistence.
- Do not implement the detailed UI presentation in this feature.
