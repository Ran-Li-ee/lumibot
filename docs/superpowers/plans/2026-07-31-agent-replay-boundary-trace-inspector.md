# Agent Replay Boundary Trace Inspector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a developer-facing Agent Replay UI inspector that shows the saved 10-step LLM/tool boundary trace for each selected agent.

**Architecture:** Keep the existing replay UI flow intact and add a parallel boundary-trace data path. The Python loader will normalize `boundary_trace.events` into stable model-turn, batch, and call-span structures; formatters will add human-readable labels and previews; the browser UI will render a collapsible inspector and fetch large sidecar payloads on demand through a safe Flask endpoint.

**Tech Stack:** Python dataclasses, Flask, vanilla JavaScript, static HTML/CSS, pytest, Playwright, ruff.

---

## File Structure

- Modify `lumibot/components/agents/replay_ui/models.py`
  - Add boundary trace dataclasses and expose them from `AgentReplay.to_public_dict()`.
- Modify `lumibot/components/agents/replay_ui/loader.py`
  - Parse `boundary_trace.events`, group by `model_turn_id`, `tool_batch_id`, and `call_id`, and generate deterministic event IDs.
- Create `lumibot/components/agents/replay_ui/boundary_formatters.py`
  - Convert B01-B10 event data into labels, source/target text, metadata badges, and compact previews.
- Modify `lumibot/components/agents/replay_ui/server.py`
  - Add safe sidecar payload endpoint.
- Modify `lumibot/components/agents/replay_ui/static/app.js`
  - Render `LLM <-> Tool Boundary Trace` in the agent detail view and load sidecar payloads on demand.
- Modify `lumibot/components/agents/replay_ui/static/styles.css`
  - Add readable layout styles for boundary events, badges, call spans, and sidecar details.
- Modify tests:
  - `tests/test_agent_replay_ui_loader.py`
  - `tests/test_agent_replay_ui_models.py`
  - `tests/test_agent_replay_ui_static.py`
  - `tests/test_agent_replay_ui_browser.py`
  - Create `tests/test_agent_replay_ui_boundary_formatters.py`
  - Create `tests/test_agent_replay_ui_server.py`.
- Create validation note:
  - `docs/superpowers/notes/2026-07-31-agent-replay-boundary-trace-inspector-validation.md`

Implementation decision for open spec questions:

- Group boundary data in Python, not JavaScript. JavaScript should render, not infer trace semantics.
- Generate deterministic event IDs in the loader from `trace_path`, event index, transition, model turn, tool batch, and call ID.
- Default-open the first model turn and keep deeper call spans collapsed.
- Do not add Tool Calls table cross-links in this first implementation; keep that as a later enhancement.
- Use server-side sidecar tokens keyed by boundary event ID. The browser never sends a filesystem path.

---

## Phase 1 Gate: Replay Data Adapter

### Task 1: Add Boundary Trace Public Models

**Files:**
- Modify: `lumibot/components/agents/replay_ui/models.py`
- Test: `tests/test_agent_replay_ui_models.py`

- [ ] **Step 1: Write model serialization tests**

Add tests like these to `tests/test_agent_replay_ui_models.py`:

```python
from lumibot.components.agents.replay_ui.models import (
    AgentReplay,
    BoundaryEventReplay,
    BoundaryTraceReplay,
)


def test_agent_public_dict_includes_boundary_trace():
    event = BoundaryEventReplay(
        id="event-1",
        transition="B03_ADK_TO_FUNCTION_TOOL",
        model_turn_id="turn-1",
        tool_batch_id="turn-1:batch:0001",
        call_id="call-1",
        status="success",
        payload={"tool_name": "market_last_price"},
        payload_meta={"semantic_completeness": "complete"},
        summary={
            "label": "ADK dispatches FunctionTool",
            "source": "Google ADK",
            "target": "ADK FunctionTool",
            "badges": ["complete"],
            "preview": "market_last_price",
        },
    )
    agent = AgentReplay(
        id="agent-1",
        name="growth_agent",
        model="openai/test",
        trace_path="trace.json",
        boundary_trace=BoundaryTraceReplay(
            available=True,
            schema_version=1,
            events=[event],
            model_turns=[
                {
                    "model_turn_id": "turn-1",
                    "request_response_events": ["event-1"],
                    "tool_batches": [],
                }
            ],
        ),
    )

    public = agent.to_public_dict()

    assert public["boundary_trace"]["available"] is True
    assert public["boundary_trace"]["schema_version"] == 1
    assert public["boundary_trace"]["events"][0]["id"] == "event-1"
    assert public["boundary_trace"]["events"][0]["payload"]["tool_name"] == "market_last_price"
    assert public["boundary_trace"]["model_turns"][0]["model_turn_id"] == "turn-1"


def test_agent_public_dict_uses_empty_boundary_trace_by_default():
    agent = AgentReplay(
        id="agent-1",
        name="legacy_agent",
        model="openai/test",
        trace_path="trace.json",
    )

    public = agent.to_public_dict()

    assert public["boundary_trace"]["available"] is False
    assert public["boundary_trace"]["events"] == []
    assert "does not contain" in public["boundary_trace"]["message"]
```

- [ ] **Step 2: Run the model tests and verify they fail**

Run:

```bash
python -m pytest tests/test_agent_replay_ui_models.py -q
```

Expected: tests fail because `BoundaryEventReplay`, `BoundaryTraceReplay`, and
`AgentReplay.boundary_trace` do not exist.

- [ ] **Step 3: Implement minimal boundary dataclasses**

In `lumibot/components/agents/replay_ui/models.py`, add:

```python
@dataclass
class BoundaryEventReplay:
    id: str
    transition: str
    model_turn_id: str | None = None
    tool_batch_id: str | None = None
    call_id: str | None = None
    status: str | None = None
    timestamp: str | None = None
    payload: Any = None
    payload_meta: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    sidecar: dict[str, Any] | None = None

    def to_public_dict(self) -> dict[str, Any]:
        public = {
            "id": redact_sensitive(self.id),
            "transition": redact_sensitive(self.transition),
            "model_turn_id": redact_sensitive(self.model_turn_id),
            "tool_batch_id": redact_sensitive(self.tool_batch_id),
            "call_id": redact_sensitive(self.call_id),
            "status": redact_sensitive(self.status),
            "timestamp": redact_sensitive(self.timestamp),
            "payload": redact_public_preview(self.payload),
            "payload_meta": redact_sensitive(self.payload_meta),
            "summary": redact_sensitive(self.summary),
        }
        if self.sidecar is not None:
            public["sidecar"] = redact_sensitive(self.sidecar)
        return public


@dataclass
class BoundaryTraceReplay:
    available: bool = False
    schema_version: int | None = None
    events: list[BoundaryEventReplay] = field(default_factory=list)
    model_turns: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[Any] = field(default_factory=list)
    message: str = (
        "This trace does not contain 10-step boundary trace data. "
        "It may have been created before boundary tracing was added."
    )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "schema_version": self.schema_version,
            "events": [event.to_public_dict() for event in self.events],
            "model_turns": redact_sensitive(self.model_turns),
            "diagnostics": redact_sensitive(self.diagnostics),
            "message": redact_sensitive(self.message),
        }
```

Add a field to `AgentReplay`:

```python
boundary_trace: BoundaryTraceReplay = field(default_factory=BoundaryTraceReplay)
```

Add it to `AgentReplay.to_public_dict()`:

```python
"boundary_trace": self.boundary_trace.to_public_dict(),
```

- [ ] **Step 4: Run the model tests and verify they pass**

Run:

```bash
python -m pytest tests/test_agent_replay_ui_models.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Phase 1 model shell**

Run:

```bash
git add lumibot/components/agents/replay_ui/models.py tests/test_agent_replay_ui_models.py
git commit -m "feat: add replay boundary trace models"
```

### Task 2: Parse and Group Boundary Trace Events

**Files:**
- Modify: `lumibot/components/agents/replay_ui/loader.py`
- Test: `tests/test_agent_replay_ui_loader.py`

- [ ] **Step 1: Write loader tests for grouped boundary trace**

Add tests to `tests/test_agent_replay_ui_loader.py`:

```python
def test_loader_groups_boundary_trace_by_turn_batch_and_call_id(tmp_path):
    trace_path = tmp_path / "traces" / "growth_agent" / "trace.json"
    _write_trace(
        trace_path,
        {
            "agent": "growth_agent",
            "model": "openai/test",
            "request": {"context": {}, "runtime_context": {}},
            "events": [],
            "boundary_trace": {
                "schema_version": 1,
                "diagnostics": [],
                "events": [
                    {
                        "transition": "B09_ADK_TO_LITELLM",
                        "model_turn_id": "turn-1",
                        "status": "success",
                        "payload": {"messages": ["request"]},
                        "payload_meta": {"semantic_completeness": "complete"},
                    },
                    {
                        "transition": "B02_LITELLM_TO_ADK",
                        "model_turn_id": "turn-1",
                        "tool_batch_id": "turn-1:batch:0001",
                        "status": "success",
                        "payload": {"function_calls": [{"id": "call-A"}, {"id": "call-B"}]},
                    },
                    {
                        "transition": "B03_ADK_TO_FUNCTION_TOOL",
                        "model_turn_id": "turn-1",
                        "tool_batch_id": "turn-1:batch:0001",
                        "call_id": "call-A",
                        "status": "success",
                        "payload": {"tool_name": "market_last_price", "args": {"symbol": "SPY"}},
                    },
                    {
                        "transition": "B03_ADK_TO_FUNCTION_TOOL",
                        "model_turn_id": "turn-1",
                        "tool_batch_id": "turn-1:batch:0001",
                        "call_id": "call-B",
                        "status": "success",
                        "payload": {"tool_name": "market_last_price", "args": {"symbol": "QQQ"}},
                    },
                ],
            },
        },
    )

    agent = load_agent_trace(trace_path)
    public = agent.to_public_dict()["boundary_trace"]

    assert public["available"] is True
    assert public["schema_version"] == 1
    assert len(public["events"]) == 4
    assert public["model_turns"][0]["model_turn_id"] == "turn-1"
    assert public["model_turns"][0]["request_response_events"]
    batch = public["model_turns"][0]["tool_batches"][0]
    assert batch["tool_batch_id"] == "turn-1:batch:0001"
    assert [call["call_id"] for call in batch["tool_calls"]] == ["call-A", "call-B"]
```

Add a sidecar metadata test:

```python
def test_loader_marks_sidecar_backed_boundary_event_without_inlining_payload(tmp_path):
    trace_path = tmp_path / "traces" / "growth_agent" / "trace.json"
    _write_trace(
        trace_path,
        {
            "agent": "growth_agent",
            "model": "openai/test",
            "request": {"context": {}, "runtime_context": {}},
            "events": [],
            "boundary_trace": {
                "schema_version": 1,
                "events": [
                    {
                        "transition": "B10_LITELLM_TO_PROVIDER",
                        "model_turn_id": "turn-1",
                        "payload": {"preview": "small preview only"},
                        "payload_meta": {
                            "sidecar_path": "boundary_payloads/event.json.gz",
                            "semantic_completeness": "complete",
                        },
                    }
                ],
            },
        },
    )

    public = load_agent_trace(trace_path).to_public_dict()["boundary_trace"]
    event = public["events"][0]

    assert event["payload"] == {"preview": "small preview only"}
    assert event["sidecar"]["available"] is True
    assert event["sidecar"]["event_id"] == event["id"]
    assert "boundary_payloads/event.json.gz" not in str(public)
```

- [ ] **Step 2: Run loader tests and verify they fail**

Run:

```bash
python -m pytest tests/test_agent_replay_ui_loader.py -q
```

Expected: new boundary grouping assertions fail.

- [ ] **Step 3: Implement loader boundary parsing**

In `lumibot/components/agents/replay_ui/loader.py`, update imports:

```python
from .boundary_formatters import summarize_boundary_event
from .models import (
    AgentDependency,
    AgentReplay,
    BoundaryEventReplay,
    BoundaryTraceReplay,
    ReplayDataset,
    ReplayRun,
    SystemRun,
    ToolBatch,
    ToolCallReplay,
)
```

Add helper functions near `load_agent_trace()`:

```python
REQUEST_RESPONSE_TRANSITIONS = {
    "B01_PROVIDER_TO_LITELLM",
    "B02_LITELLM_TO_ADK",
    "B09_ADK_TO_LITELLM",
    "B10_LITELLM_TO_PROVIDER",
}

LOCAL_TOOL_TRANSITIONS = {
    "B03_ADK_TO_FUNCTION_TOOL",
    "B04_FUNCTION_TOOL_TO_WRAPPER",
    "B05_WRAPPER_TO_PYTHON_TOOL",
    "B06_PYTHON_TOOL_TO_WRAPPER",
    "B07_WRAPPER_TO_FUNCTION_TOOL",
    "B08_FUNCTION_TOOL_TO_ADK",
}


def _load_boundary_trace(trace: dict[str, Any], trace_path: Path) -> BoundaryTraceReplay:
    boundary = trace.get("boundary_trace")
    if not isinstance(boundary, dict):
        return BoundaryTraceReplay()

    raw_events = boundary.get("events")
    if not isinstance(raw_events, list):
        raw_events = []

    events: list[BoundaryEventReplay] = []
    for index, raw_event in enumerate(raw_events):
        if not isinstance(raw_event, dict):
            continue
        event = _boundary_event_from_raw(trace_path, index, raw_event)
        events.append(event)

    return BoundaryTraceReplay(
        available=True,
        schema_version=boundary.get("schema_version") if isinstance(boundary.get("schema_version"), int) else None,
        events=events,
        model_turns=_group_boundary_events(events),
        diagnostics=boundary.get("diagnostics") if isinstance(boundary.get("diagnostics"), list) else [],
        message="Boundary trace data is available for this agent run.",
    )


def _boundary_event_from_raw(trace_path: Path, index: int, raw_event: dict[str, Any]) -> BoundaryEventReplay:
    transition = _first_text(raw_event.get("transition"), "UNKNOWN_BOUNDARY")
    event_id = _boundary_event_id(trace_path, index, raw_event)
    payload_meta = raw_event.get("payload_meta") if isinstance(raw_event.get("payload_meta"), dict) else {}
    sidecar = None
    if isinstance(payload_meta.get("sidecar_path"), str) and payload_meta.get("sidecar_path"):
        sidecar = {
            "available": True,
            "event_id": event_id,
            "byte_count": payload_meta.get("byte_count"),
            "compression": payload_meta.get("compression"),
            "sha256": payload_meta.get("sha256"),
        }
    return BoundaryEventReplay(
        id=event_id,
        transition=transition,
        model_turn_id=_optional_text(raw_event.get("model_turn_id")),
        tool_batch_id=_optional_text(raw_event.get("tool_batch_id")),
        call_id=_optional_text(raw_event.get("call_id")),
        status=_optional_text(raw_event.get("status")),
        timestamp=_optional_text(raw_event.get("timestamp")),
        payload=raw_event.get("payload"),
        payload_meta=payload_meta,
        summary=summarize_boundary_event(raw_event),
        sidecar=sidecar,
    )


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _boundary_event_id(trace_path: Path, index: int, raw_event: dict[str, Any]) -> str:
    basis = "|".join(
        [
            str(trace_path.resolve()),
            str(index),
            str(raw_event.get("transition") or ""),
            str(raw_event.get("model_turn_id") or ""),
            str(raw_event.get("tool_batch_id") or ""),
            str(raw_event.get("call_id") or ""),
        ]
    )
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:24]
```

Add grouping helpers:

```python
def _group_boundary_events(events: list[BoundaryEventReplay]) -> list[dict[str, Any]]:
    turns: dict[str, list[BoundaryEventReplay]] = {}
    for event in events:
        turn_id = event.model_turn_id or "unknown-model-turn"
        turns.setdefault(turn_id, []).append(event)

    grouped_turns = []
    for turn_id, turn_events in turns.items():
        request_response = [
            event.id for event in turn_events if event.transition in REQUEST_RESPONSE_TRANSITIONS
        ]
        grouped_turns.append(
            {
                "model_turn_id": turn_id,
                "request_response_events": request_response,
                "tool_batches": _group_boundary_tool_batches(turn_events),
            }
        )
    return grouped_turns


def _group_boundary_tool_batches(events: list[BoundaryEventReplay]) -> list[dict[str, Any]]:
    batches: dict[str, list[BoundaryEventReplay]] = {}
    for event in events:
        if event.transition not in LOCAL_TOOL_TRANSITIONS and not event.call_id:
            continue
        batch_id = event.tool_batch_id or "unknown-tool-batch"
        batches.setdefault(batch_id, []).append(event)

    return [
        {
            "tool_batch_id": batch_id,
            "tool_calls": _group_boundary_tool_calls(batch_events),
        }
        for batch_id, batch_events in batches.items()
    ]


def _group_boundary_tool_calls(events: list[BoundaryEventReplay]) -> list[dict[str, Any]]:
    calls: dict[str, list[BoundaryEventReplay]] = {}
    for event in events:
        call_id = event.call_id or "unknown-call"
        calls.setdefault(call_id, []).append(event)

    grouped_calls = []
    for call_id, call_events in calls.items():
        tool_name = _boundary_tool_name(call_events)
        grouped_calls.append(
            {
                "call_id": call_id,
                "tool_name": tool_name,
                "events": [event.id for event in call_events],
            }
        )
    return grouped_calls


def _boundary_tool_name(events: list[BoundaryEventReplay]) -> str | None:
    for event in events:
        payload = event.payload
        if isinstance(payload, dict):
            for key in ("tool_name", "name", "function_name"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value
            function = payload.get("function")
            if isinstance(function, dict):
                name = function.get("name")
                if isinstance(name, str) and name:
                    return name
    return None
```

Update `load_agent_trace()` by assigning the boundary trace before the return:

```python
boundary_trace = _load_boundary_trace(trace, trace_path)
```

Then add this keyword argument to the existing `AgentReplay(...)` constructor:

```python
boundary_trace=boundary_trace,
```

- [ ] **Step 4: Run Phase 1 tests**

Run:

```bash
python -m pytest tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_loader.py -q
```

Expected: PASS.

- [ ] **Step 5: Run lint for changed Python files**

Run:

```bash
python -m ruff check lumibot/components/agents/replay_ui/models.py lumibot/components/agents/replay_ui/loader.py tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_loader.py
```

Expected: PASS.

- [ ] **Step 6: Commit Phase 1 adapter**

Run:

```bash
git add lumibot/components/agents/replay_ui/models.py lumibot/components/agents/replay_ui/loader.py tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_loader.py
git commit -m "feat: expose boundary trace in replay dataset"
```

Do not start Phase 2 until this phase's tests pass.

---

## Phase 2 Gate: Boundary Event Formatting

### Task 3: Add Boundary Formatter Module

**Files:**
- Create: `lumibot/components/agents/replay_ui/boundary_formatters.py`
- Test: `tests/test_agent_replay_ui_boundary_formatters.py`

- [ ] **Step 1: Write formatter tests**

Create `tests/test_agent_replay_ui_boundary_formatters.py`:

```python
import pytest

from lumibot.components.agents.replay_ui.boundary_formatters import (
    BOUNDARY_TRANSITION_ORDER,
    summarize_boundary_event,
)


@pytest.mark.parametrize("transition", BOUNDARY_TRANSITION_ORDER)
def test_formatter_covers_each_boundary_transition(transition):
    summary = summarize_boundary_event(
        {
            "transition": transition,
            "status": "success",
            "payload": {"tool_name": "market_last_price", "args": {"symbol": "SPY"}},
            "payload_meta": {"semantic_completeness": "complete"},
        }
    )

    assert summary["label"]
    assert summary["source"]
    assert summary["target"]
    assert summary["explanation"]
    assert "complete" in summary["badges"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("success", "success"),
        ("error", "error"),
        ("not_available", "not available"),
        ("not_applicable", "not applicable"),
    ],
)
def test_formatter_surfaces_status_badges(status, expected):
    summary = summarize_boundary_event(
        {
            "transition": "B01_PROVIDER_TO_LITELLM",
            "status": status,
            "payload": {},
            "payload_meta": {},
        }
    )

    assert expected in summary["badges"]


def test_formatter_surfaces_payload_fidelity_badges():
    summary = summarize_boundary_event(
        {
            "transition": "B10_LITELLM_TO_PROVIDER",
            "status": "success",
            "payload": {"messages": [{"role": "user", "content": "hello"}]},
            "payload_meta": {
                "semantic_completeness": "partial",
                "redacted": True,
                "truncated": True,
                "pruned": True,
                "sidecar_path": "boundary_payloads/event.json.gz",
            },
        }
    )

    assert "partial" in summary["badges"]
    assert "redacted" in summary["badges"]
    assert "truncated" in summary["badges"]
    assert "pruned" in summary["badges"]
    assert "sidecar" in summary["badges"]


def test_formatter_does_not_fabricate_missing_payload():
    summary = summarize_boundary_event(
        {
            "transition": "B06_PYTHON_TOOL_TO_WRAPPER",
            "status": "not_available",
            "payload": None,
            "payload_meta": {},
        }
    )

    assert "not available" in summary["preview"].lower()
```

- [ ] **Step 2: Run formatter tests and verify they fail**

Run:

```bash
python -m pytest tests/test_agent_replay_ui_boundary_formatters.py -q
```

Expected: FAIL because the formatter module does not exist.

- [ ] **Step 3: Implement formatter module**

Create `lumibot/components/agents/replay_ui/boundary_formatters.py`:

```python
from __future__ import annotations

from typing import Any

BOUNDARY_TRANSITIONS: dict[str, dict[str, str]] = {
    "B01_PROVIDER_TO_LITELLM": {
        "label": "Provider response",
        "source": "Provider",
        "target": "LiteLLM",
        "explanation": "The remote model provider returned a visible response to LiteLLM.",
    },
    "B02_LITELLM_TO_ADK": {
        "label": "LiteLLM response parsed for ADK",
        "source": "LiteLLM",
        "target": "Google ADK",
        "explanation": "LiteLLM converted the provider response into ADK's response representation.",
    },
    "B03_ADK_TO_FUNCTION_TOOL": {
        "label": "ADK dispatches FunctionTool",
        "source": "Google ADK",
        "target": "ADK FunctionTool",
        "explanation": "ADK selected a FunctionTool for a model-requested tool call.",
    },
    "B04_FUNCTION_TOOL_TO_WRAPPER": {
        "label": "FunctionTool calls Lumibot wrapper",
        "source": "ADK FunctionTool",
        "target": "Lumibot wrapper",
        "explanation": "The ADK FunctionTool passed validated arguments into Lumibot's tool wrapper.",
    },
    "B05_WRAPPER_TO_PYTHON_TOOL": {
        "label": "Wrapper calls Python tool",
        "source": "Lumibot wrapper",
        "target": "Python tool",
        "explanation": "Lumibot invoked the original local Python tool function.",
    },
    "B06_PYTHON_TOOL_TO_WRAPPER": {
        "label": "Python tool returns",
        "source": "Python tool",
        "target": "Lumibot wrapper",
        "explanation": "The local Python tool returned its raw result to the Lumibot wrapper.",
    },
    "B07_WRAPPER_TO_FUNCTION_TOOL": {
        "label": "Wrapper serializes result",
        "source": "Lumibot wrapper",
        "target": "ADK FunctionTool",
        "explanation": "Lumibot converted the tool result into a JSON-safe function response payload.",
    },
    "B08_FUNCTION_TOOL_TO_ADK": {
        "label": "FunctionTool returns to ADK",
        "source": "ADK FunctionTool",
        "target": "Google ADK",
        "explanation": "The ADK FunctionTool returned the function response to the ADK runtime.",
    },
    "B09_ADK_TO_LITELLM": {
        "label": "ADK builds model request",
        "source": "Google ADK",
        "target": "LiteLLM",
        "explanation": "ADK built the next model request, including any model-facing tool results.",
    },
    "B10_LITELLM_TO_PROVIDER": {
        "label": "LiteLLM sends provider request",
        "source": "LiteLLM",
        "target": "Provider",
        "explanation": "LiteLLM translated the ADK request into the provider-facing request shape.",
    },
}

BOUNDARY_TRANSITION_ORDER = tuple(BOUNDARY_TRANSITIONS)


def summarize_boundary_event(event: dict[str, Any]) -> dict[str, Any]:
    transition = str(event.get("transition") or "UNKNOWN_BOUNDARY")
    definition = BOUNDARY_TRANSITIONS.get(
        transition,
        {
            "label": transition,
            "source": "Unknown",
            "target": "Unknown",
            "explanation": "This boundary transition is not recognized by this UI version.",
        },
    )
    payload = event.get("payload")
    payload_meta = event.get("payload_meta") if isinstance(event.get("payload_meta"), dict) else {}
    status = str(event.get("status") or "unknown")
    return {
        "label": definition["label"],
        "source": definition["source"],
        "target": definition["target"],
        "explanation": definition["explanation"],
        "badges": _badges(status, payload_meta),
        "preview": _preview(status, payload),
    }


def _badges(status: str, payload_meta: dict[str, Any]) -> list[str]:
    badges: list[str] = []
    status_badge = status.replace("_", " ")
    if status_badge:
        badges.append(status_badge)
    completeness = payload_meta.get("semantic_completeness")
    if isinstance(completeness, str) and completeness and completeness not in badges:
        badges.append(completeness)
    for key, label in (
        ("redacted", "redacted"),
        ("truncated", "truncated"),
        ("pruned", "pruned"),
    ):
        if payload_meta.get(key) is True:
            badges.append(label)
    if payload_meta.get("sidecar_path"):
        badges.append("sidecar")
    return badges


def _preview(status: str, payload: Any) -> str:
    if status in {"not_available", "not_applicable"}:
        return status.replace("_", " ")
    if payload is None:
        return "No payload recorded."
    if isinstance(payload, dict):
        for key in ("tool_name", "name", "function_name"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        function = payload.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            return function["name"]
        if isinstance(payload.get("messages"), list):
            return f"{len(payload['messages'])} model message(s)"
        if isinstance(payload.get("tool_calls"), list):
            return f"{len(payload['tool_calls'])} provider tool call(s)"
        if isinstance(payload.get("function_calls"), list):
            return f"{len(payload['function_calls'])} ADK function call(s)"
        if "result" in payload:
            return "Tool result payload"
        if "error" in payload:
            return f"Error payload: {payload.get('error')}"
        keys = ", ".join(str(key) for key in list(payload.keys())[:5])
        return f"Payload keys: {keys}" if keys else "Empty object payload"
    if isinstance(payload, list):
        return f"{len(payload)} item(s)"
    text = str(payload)
    return text[:240] + ("..." if len(text) > 240 else "")
```

- [ ] **Step 4: Run Phase 2 tests**

Run:

```bash
python -m pytest tests/test_agent_replay_ui_boundary_formatters.py tests/test_agent_replay_ui_loader.py -q
```

Expected: PASS.

- [ ] **Step 5: Run lint for changed Python files**

Run:

```bash
python -m ruff check lumibot/components/agents/replay_ui/boundary_formatters.py lumibot/components/agents/replay_ui/loader.py tests/test_agent_replay_ui_boundary_formatters.py tests/test_agent_replay_ui_loader.py
```

Expected: PASS.

- [ ] **Step 6: Commit Phase 2 formatter**

Run:

```bash
git add lumibot/components/agents/replay_ui/boundary_formatters.py lumibot/components/agents/replay_ui/loader.py tests/test_agent_replay_ui_boundary_formatters.py tests/test_agent_replay_ui_loader.py
git commit -m "feat: format boundary trace events for replay"
```

Do not start Phase 3 until this phase's tests pass.

---

## Phase 3 Gate: Agent Detail UI Inspector

### Task 4: Render Boundary Trace Section in Agent Detail

**Files:**
- Modify: `lumibot/components/agents/replay_ui/static/index.html`
- Modify: `lumibot/components/agents/replay_ui/static/app.js`
- Modify: `lumibot/components/agents/replay_ui/static/styles.css`
- Test: `tests/test_agent_replay_ui_static.py`
- Test: `tests/test_agent_replay_ui_browser.py`

- [ ] **Step 1: Write static UI tests**

Add to `tests/test_agent_replay_ui_static.py`:

```python
def test_static_ui_contains_boundary_trace_region():
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'id="boundaryTraceArea"' in html


def test_static_javascript_renders_boundary_trace_inspector():
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert "renderBoundaryTraceArea" in javascript
    assert "LLM <-> Tool Boundary Trace" in javascript
    assert "renderBoundaryModelTurn" in javascript
    assert "renderBoundaryEventRow" in javascript
    assert "This trace does not contain 10-step boundary trace data" in javascript


def test_static_css_defines_boundary_trace_styles:
    css = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")

    assert ".boundary-trace" in css
    assert ".boundary-event" in css
    assert ".boundary-badge" in css
    assert ".boundary-call-span" in css
```

- [ ] **Step 2: Extend browser fixtures with boundary trace data**

In `tests/test_agent_replay_ui_browser.py`, update `_public_agent()` so callers
can override `boundary_trace`. Change the function signature to:

```python
def _public_agent(agent_id: str, boundary_trace: dict | None = None) -> dict:
```

Then add this key to the returned dict:

```python
"boundary_trace": boundary_trace
if boundary_trace is not None
else {
    "available": False,
    "events": [],
    "model_turns": [],
    "message": "This trace does not contain 10-step boundary trace data.",
},
```

Add a browser test:

```python
def test_browser_renders_boundary_trace_for_selected_agent(page):
    agent = _public_agent(
        "growth_agent",
        boundary_trace={
            "available": True,
            "schema_version": 1,
            "message": "Boundary trace data is available for this agent run.",
            "events": [
                {
                    "id": "event-b09",
                    "transition": "B09_ADK_TO_LITELLM",
                    "status": "success",
                    "summary": {
                        "label": "ADK builds model request",
                        "source": "Google ADK",
                        "target": "LiteLLM",
                        "badges": ["success", "complete"],
                        "preview": "1 model message(s)",
                    },
                    "payload": {"messages": [{"role": "user", "content": "hello"}]},
                    "payload_meta": {"semantic_completeness": "complete"},
                },
                {
                    "id": "event-b03",
                    "transition": "B03_ADK_TO_FUNCTION_TOOL",
                    "status": "success",
                    "summary": {
                        "label": "ADK dispatches FunctionTool",
                        "source": "Google ADK",
                        "target": "ADK FunctionTool",
                        "badges": ["success", "complete"],
                        "preview": "market_last_price",
                    },
                    "payload": {"tool_name": "market_last_price", "args": {"symbol": "SPY"}},
                    "payload_meta": {"semantic_completeness": "complete"},
                },
            ],
            "model_turns": [
                {
                    "model_turn_id": "turn-1",
                    "request_response_events": ["event-b09"],
                    "tool_batches": [
                        {
                            "tool_batch_id": "turn-1:batch:0001",
                            "tool_calls": [
                                {
                                    "call_id": "call-1",
                                    "tool_name": "market_last_price",
                                    "events": ["event-b03"],
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )
    dataset = _public_dataset([agent], [])
    page.route("**/api/dataset", lambda route: route.fulfill(json=dataset))

    page.goto("http://127.0.0.1:8765/")
    page.get_by_text("growth_agent").click()

    expect(page.get_by_text("LLM <-> Tool Boundary Trace")).to_be_visible()
    expect(page.get_by_text("Model Turn 1")).to_be_visible()
    expect(page.get_by_text("B09_ADK_TO_LITELLM")).to_be_visible()
    expect(page.get_by_text("B03_ADK_TO_FUNCTION_TOOL")).to_be_visible()
    expect(page.get_by_text("market_last_price")).to_be_visible()
```

Add a legacy fallback browser test:

```python
def test_browser_renders_boundary_trace_legacy_fallback(page):
    dataset = _public_dataset([_public_agent("legacy_agent")], [])
    page.route("**/api/dataset", lambda route: route.fulfill(json=dataset))

    page.goto("http://127.0.0.1:8765/")
    page.get_by_text("legacy_agent").click()

    expect(page.get_by_text("This trace does not contain 10-step boundary trace data")).to_be_visible()
```

- [ ] **Step 3: Run UI tests and verify they fail**

Run:

```bash
python -m pytest tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py -q
```

Expected: FAIL because the boundary region and renderer do not exist.

- [ ] **Step 4: Add the HTML detail region**

In `lumibot/components/agents/replay_ui/static/index.html`, add the new detail
region between `toolArea` and `summaryArea`:

```html
<section id="boundaryTraceArea" class="detail-section" hidden></section>
```

- [ ] **Step 5: Wire the region in JavaScript**

In `lumibot/components/agents/replay_ui/static/app.js`, add
`boundaryTraceArea` to the element lookup:

```javascript
boundaryTraceArea: document.getElementById("boundaryTraceArea"),
```

Update `renderAgentDetail(agent, systemRun)`:

```javascript
function renderAgentDetail(agent, systemRun) {
  setDetailMode("agent");
  renderInputArea(agent);
  renderToolArea(agent);
  renderBoundaryTraceArea(agent);
  renderSummaryArea(agent, systemRun);
}
```

Update `setDetailMode(mode)`:

```javascript
elements.boundaryTraceArea.hidden = showOverview;
```

Update the no-agent overview branch that clears detail areas:

```javascript
elements.boundaryTraceArea.innerHTML = "";
```

- [ ] **Step 6: Add boundary trace renderer**

In `app.js`, add:

```javascript
function renderBoundaryTraceArea(agent) {
  const trace = agent.boundary_trace || {};
  if (!trace.available) {
    elements.boundaryTraceArea.innerHTML = renderCollapsibleSection(
      "LLM <-> Tool Boundary Trace",
      "not available",
      `<div class="empty-state">${escapeHtml(trace.message || "This trace does not contain 10-step boundary trace data. It may have been created before boundary tracing was added.")}</div>`,
      false,
    );
    return;
  }

  const eventsById = boundaryEventsById(trace.events);
  const turns = Array.isArray(trace.model_turns) ? trace.model_turns : [];
  const body = `
    <div class="boundary-trace">
      ${turns.length ? turns.map((turn, index) => renderBoundaryModelTurn(turn, eventsById, index === 0)).join("") : '<div class="empty-state">Boundary trace contains no model turns.</div>'}
    </div>
  `;
  elements.boundaryTraceArea.innerHTML = renderCollapsibleSection(
    "LLM <-> Tool Boundary Trace",
    `${turns.length} model turn${turns.length === 1 ? "" : "s"} | ${Array.isArray(trace.events) ? trace.events.length : 0} event${Array.isArray(trace.events) && trace.events.length === 1 ? "" : "s"}`,
    body,
    true,
  );
}

function boundaryEventsById(events) {
  const map = new Map();
  (Array.isArray(events) ? events : []).forEach((event) => {
    if (event && event.id) {
      map.set(event.id, event);
    }
  });
  return map;
}

function renderBoundaryModelTurn(turn, eventsById, isOpen) {
  const requestEvents = eventIdsToEvents(turn.request_response_events, eventsById);
  const batches = Array.isArray(turn.tool_batches) ? turn.tool_batches : [];
  const body = `
    <div class="boundary-event-group">
      <h4>Model Request / Response</h4>
      ${requestEvents.length ? requestEvents.map(renderBoundaryEventRow).join("") : '<div class="empty-state">No model request/response boundary events recorded.</div>'}
    </div>
    ${batches.map((batch, index) => renderBoundaryToolBatch(batch, eventsById, index === 0)).join("")}
  `;
  return renderCollapsibleSubsection(
    `Model Turn ${modelTurnLabel(turn.model_turn_id)}`,
    turn.model_turn_id || "unknown turn",
    body,
    isOpen,
  );
}

function modelTurnLabel(modelTurnId) {
  const match = String(modelTurnId || "").match(/turn:(\d+)$/);
  return match ? String(Number(match[1])) : String(modelTurnId || "unknown");
}

function renderBoundaryToolBatch(batch, eventsById, isOpen) {
  const calls = Array.isArray(batch.tool_calls) ? batch.tool_calls : [];
  const body = calls.length
    ? calls.map((call) => renderBoundaryToolCall(call, eventsById)).join("")
    : '<div class="empty-state">No tool calls recorded in this boundary batch.</div>';
  return renderCollapsibleSubsection(
    `Tool Batch ${batch.tool_batch_id || "unknown"}`,
    `${calls.length} call${calls.length === 1 ? "" : "s"}`,
    body,
    isOpen,
  );
}

function renderBoundaryToolCall(call, eventsById) {
  const events = eventIdsToEvents(call.events, eventsById);
  return `
    <div class="boundary-call-span">
      <div class="boundary-call-heading">
        <strong>${escapeHtml(call.tool_name || "Unknown tool")}</strong>
        <span>${escapeHtml(call.call_id || "unknown call")}</span>
      </div>
      ${events.length ? events.map(renderBoundaryEventRow).join("") : '<div class="empty-state">No boundary events recorded for this tool call.</div>'}
    </div>
  `;
}

function eventIdsToEvents(ids, eventsById) {
  return (Array.isArray(ids) ? ids : [])
    .map((id) => eventsById.get(id))
    .filter(Boolean);
}

function renderBoundaryEventRow(event) {
  const summary = event.summary || {};
  return `
    <details class="boundary-event">
      <summary>
        <span class="boundary-code">${escapeHtml(event.transition || "UNKNOWN")}</span>
        <span class="boundary-label">${escapeHtml(summary.label || "")}</span>
        <span class="boundary-route">${escapeHtml(summary.source || "?")} -> ${escapeHtml(summary.target || "?")}</span>
        ${renderBoundaryBadges(summary.badges || [])}
      </summary>
      <div class="boundary-event-body">
        <div class="notice">${escapeHtml(summary.explanation || "")}</div>
        <div class="boundary-preview">${escapeHtml(summary.preview || "")}</div>
        <pre>${formatValue({
          id: event.id,
          status: event.status,
          model_turn_id: event.model_turn_id,
          tool_batch_id: event.tool_batch_id,
          call_id: event.call_id,
          payload_meta: event.payload_meta,
          payload: event.payload,
        })}</pre>
        ${renderBoundarySidecarButton(event)}
      </div>
    </details>
  `;
}

function renderBoundaryBadges(badges) {
  return (Array.isArray(badges) ? badges : [])
    .map((badge) => `<span class="boundary-badge">${escapeHtml(badge)}</span>`)
    .join("");
}

function renderBoundarySidecarButton(event) {
  if (!event.sidecar || !event.sidecar.available) {
    return "";
  }
  return `<button class="secondary-button boundary-sidecar-button" type="button" data-boundary-event-id="${escapeHtml(event.sidecar.event_id || event.id)}">Load full sidecar payload</button><pre class="boundary-sidecar-output" hidden></pre>`;
}
```

- [ ] **Step 7: Add styles**

In `styles.css`, add:

```css
.boundary-trace {
  display: grid;
  gap: 12px;
}

.boundary-event-group {
  display: grid;
  gap: 8px;
}

.boundary-call-span {
  border: 1px solid #cbd5d1;
  border-radius: 6px;
  padding: 10px;
  margin: 8px 0;
  background: #fbfcfb;
}

.boundary-call-heading {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.boundary-event {
  border: 1px solid #d6ddd9;
  border-radius: 6px;
  padding: 8px;
  background: #fff;
}

.boundary-event summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}

.boundary-code {
  font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
  font-weight: 700;
}

.boundary-label {
  font-weight: 600;
}

.boundary-route {
  color: #52615a;
}

.boundary-badge {
  border: 1px solid #b8c4be;
  border-radius: 999px;
  padding: 2px 7px;
  font-size: 12px;
  background: #eef4f1;
}

.boundary-event-body {
  display: grid;
  gap: 8px;
  margin-top: 8px;
}

.boundary-preview {
  border-left: 3px solid #78958a;
  padding-left: 8px;
  color: #26312c;
}

.boundary-sidecar-button {
  width: fit-content;
}
```

- [ ] **Step 8: Run Phase 3 tests**

Run:

```bash
python -m pytest tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py -q
```

Expected: PASS.

- [ ] **Step 9: Run lint for changed static-related tests**

Run:

```bash
python -m ruff check tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py
```

Expected: PASS.

- [ ] **Step 10: Commit Phase 3 UI**

Run:

```bash
git add lumibot/components/agents/replay_ui/static/index.html lumibot/components/agents/replay_ui/static/app.js lumibot/components/agents/replay_ui/static/styles.css tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py
git commit -m "feat: render boundary trace inspector"
```

Do not start Phase 4 until this phase's tests pass.

---

## Phase 4 Gate: Sidecar Payload Access

### Task 5: Add Safe Boundary Sidecar Endpoint

**Files:**
- Modify: `lumibot/components/agents/replay_ui/loader.py`
- Modify: `lumibot/components/agents/replay_ui/server.py`
- Modify: `lumibot/components/agents/replay_ui/static/app.js`
- Test: `tests/test_agent_replay_ui_server.py`
- Test: `tests/test_agent_replay_ui_browser.py`

- [ ] **Step 1: Write server tests for sidecar access**

Create `tests/test_agent_replay_ui_server.py`:

```python
import gzip
import json
from pathlib import Path

from lumibot.components.agents.replay_ui.server import create_app


def _write_trace(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_boundary_sidecar_endpoint_returns_known_payload(tmp_path):
    root = tmp_path / "agent_runtime"
    sidecar_path = root / "boundary_payloads" / "payload.json.gz"
    sidecar_path.parent.mkdir(parents=True)
    sidecar_path.write_bytes(gzip.compress(json.dumps({"full": "payload"}).encode("utf-8")))
    trace_path = root / "traces" / "agent" / "trace.json"
    _write_trace(
        trace_path,
        {
            "agent": "agent",
            "model": "openai/test",
            "request": {"context": {}, "runtime_context": {}},
            "events": [],
            "boundary_trace": {
                "schema_version": 1,
                "events": [
                    {
                        "transition": "B09_ADK_TO_LITELLM",
                        "model_turn_id": "turn-1",
                        "payload": {"preview": "small"},
                        "payload_meta": {
                            "sidecar_path": "boundary_payloads/payload.json.gz",
                            "compression": "gzip",
                        },
                    }
                ],
            },
        },
    )
    app = create_app(root)
    dataset = app.test_client().get("/api/dataset").get_json()
    event_id = dataset["runs"][0]["system_runs"][0]["agents"][0]["boundary_trace"]["events"][0]["id"]

    response = app.test_client().get(f"/api/boundary-payload/{event_id}")

    assert response.status_code == 200
    assert response.get_json()["payload"] == {"full": "payload"}


def test_boundary_sidecar_endpoint_rejects_unknown_event(tmp_path):
    app = create_app(tmp_path / "agent_runtime")

    response = app.test_client().get("/api/boundary-payload/not-real")

    assert response.status_code == 404


def test_boundary_sidecar_endpoint_rejects_path_traversal_sidecar(tmp_path):
    root = tmp_path / "agent_runtime"
    trace_path = root / "traces" / "agent" / "trace.json"
    _write_trace(
        trace_path,
        {
            "agent": "agent",
            "model": "openai/test",
            "request": {"context": {}, "runtime_context": {}},
            "events": [],
            "boundary_trace": {
                "schema_version": 1,
                "events": [
                    {
                        "transition": "B09_ADK_TO_LITELLM",
                        "model_turn_id": "turn-1",
                        "payload_meta": {"sidecar_path": "../secret.json"},
                    }
                ],
            },
        },
    )
    app = create_app(root)
    dataset = app.test_client().get("/api/dataset").get_json()
    event_id = dataset["runs"][0]["system_runs"][0]["agents"][0]["boundary_trace"]["events"][0]["id"]

    response = app.test_client().get(f"/api/boundary-payload/{event_id}")

    assert response.status_code == 404
```

- [ ] **Step 2: Run server tests and verify they fail**

Run:

```bash
python -m pytest tests/test_agent_replay_ui_server.py -q
```

Expected: FAIL because `/api/boundary-payload/<event_id>` does not exist.

- [ ] **Step 3: Add sidecar index helper**

In `loader.py`, add:

```python
def boundary_sidecar_index_for_roots(trace_roots: list[str | Path]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for trace_root in trace_roots:
        root = Path(trace_root)
        for trace_path in discover_trace_files(root):
            try:
                with trace_path.open("r", encoding="utf-8-sig") as trace_file:
                    trace = json.load(trace_file)
            except Exception:
                continue
            boundary = trace.get("boundary_trace") if isinstance(trace, dict) else None
            events = boundary.get("events") if isinstance(boundary, dict) else None
            if not isinstance(events, list):
                continue
            for event_index, raw_event in enumerate(events):
                if not isinstance(raw_event, dict):
                    continue
                payload_meta = raw_event.get("payload_meta")
                if not isinstance(payload_meta, dict):
                    continue
                sidecar_path = payload_meta.get("sidecar_path")
                if not isinstance(sidecar_path, str) or not sidecar_path:
                    continue
                event_id = _boundary_event_id(trace_path.resolve(), event_index, raw_event)
                index[event_id] = {
                    "trace_root": root.resolve(),
                    "sidecar_path": sidecar_path,
                    "compression": payload_meta.get("compression"),
                }
    return index


def load_boundary_sidecar_payload(entry: dict[str, Any]) -> Any:
    trace_root = Path(entry["trace_root"]).resolve()
    relative = Path(str(entry["sidecar_path"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise FileNotFoundError("invalid sidecar path")
    target = (trace_root / relative).resolve()
    if not _is_relative_to(target, trace_root):
        raise FileNotFoundError("sidecar path escapes trace root")
    data = target.read_bytes()
    if entry.get("compression") == "gzip" or target.suffix == ".gz":
        data = gzip.decompress(data)
    return json.loads(data.decode("utf-8"))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
```

Add `import gzip` at the top of `loader.py`.

- [ ] **Step 4: Add Flask endpoint**

In `server.py`, import:

```python
from .loader import (
    artifact_token_for_root,
    backtest_artifact_root,
    boundary_sidecar_index_for_roots,
    build_replay_dataset_from_roots,
    find_account_curve_report,
    find_performance_report,
    load_boundary_sidecar_payload,
)
from .redaction import redact_public_preview
```

In `create_app()`, after configuring trace roots:

```python
app.config["BOUNDARY_SIDECARS_BY_EVENT_ID"] = boundary_sidecar_index_for_roots(app.config["TRACE_ROOTS"])
```

Add route:

```python
@app.get("/api/boundary-payload/<event_id>")
def boundary_payload(event_id: str) -> Response:
    index = app.config.get("BOUNDARY_SIDECARS_BY_EVENT_ID") or {}
    entry = index.get(event_id)
    if entry is None:
        abort(404)
    try:
        payload = load_boundary_sidecar_payload(entry)
    except Exception:
        abort(404)
    return jsonify({"event_id": event_id, "payload": redact_public_preview(payload)})
```

- [ ] **Step 5: Add browser-side sidecar loading**

In `app.js`, add one delegated click listener after initial element setup:

```javascript
document.addEventListener("click", (event) => {
  const button = event.target.closest(".boundary-sidecar-button");
  if (!button) {
    return;
  }
  loadBoundarySidecar(button);
});
```

Add:

```javascript
async function loadBoundarySidecar(button) {
  const eventId = button.getAttribute("data-boundary-event-id");
  const output = button.parentElement.querySelector(".boundary-sidecar-output");
  if (!eventId || !output) {
    return;
  }
  button.disabled = true;
  button.textContent = "Loading full sidecar payload...";
  try {
    const response = await fetch(`/api/boundary-payload/${encodeURIComponent(eventId)}`);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    output.hidden = false;
    output.textContent = formatValue(payload.payload);
    button.textContent = "Full sidecar payload loaded";
  } catch (error) {
    output.hidden = false;
    output.textContent = `Unable to load sidecar payload: ${error.message}`;
    button.textContent = "Load full sidecar payload";
    button.disabled = false;
  }
}
```

- [ ] **Step 6: Add browser sidecar test**

Add to `tests/test_agent_replay_ui_browser.py`:

```python
def test_browser_loads_boundary_sidecar_payload(page):
    agent = _public_agent(
        "growth_agent",
        boundary_trace={
            "available": True,
            "events": [
                {
                    "id": "event-sidecar",
                    "transition": "B09_ADK_TO_LITELLM",
                    "status": "success",
                    "summary": {
                        "label": "ADK builds model request",
                        "source": "Google ADK",
                        "target": "LiteLLM",
                        "badges": ["success", "sidecar"],
                        "preview": "sidecar preview",
                    },
                    "payload": {"preview": "small"},
                    "payload_meta": {"sidecar_path": "hidden"},
                    "sidecar": {"available": True, "event_id": "event-sidecar"},
                }
            ],
            "model_turns": [
                {
                    "model_turn_id": "turn-1",
                    "request_response_events": ["event-sidecar"],
                    "tool_batches": [],
                }
            ],
        },
    )
    dataset = _public_dataset([agent], [])
    page.route("**/api/dataset", lambda route: route.fulfill(json=dataset))
    page.route(
        "**/api/boundary-payload/event-sidecar",
        lambda route: route.fulfill(json={"event_id": "event-sidecar", "payload": {"full": "payload"}}),
    )

    page.goto("http://127.0.0.1:8765/")
    page.get_by_text("growth_agent").click()
    page.get_by_text("Load full sidecar payload").click()

    expect(page.get_by_text('"full": "payload"')).to_be_visible()
```

- [ ] **Step 7: Run Phase 4 tests**

Run:

```bash
python -m pytest tests/test_agent_replay_ui_server.py tests/test_agent_replay_ui_browser.py tests/test_agent_replay_ui_loader.py -q
```

Expected: PASS.

- [ ] **Step 8: Run lint for changed files**

Run:

```bash
python -m ruff check lumibot/components/agents/replay_ui/loader.py lumibot/components/agents/replay_ui/server.py tests/test_agent_replay_ui_server.py tests/test_agent_replay_ui_browser.py
```

Expected: PASS.

- [ ] **Step 9: Commit Phase 4 sidecar access**

Run:

```bash
git add lumibot/components/agents/replay_ui/loader.py lumibot/components/agents/replay_ui/server.py lumibot/components/agents/replay_ui/static/app.js tests/test_agent_replay_ui_server.py tests/test_agent_replay_ui_browser.py
git commit -m "feat: load boundary trace sidecar payloads"
```

Do not start Phase 5 until this phase's tests pass.

---

## Phase 5 Gate: Real Backtest Validation

### Task 6: Validate Against Real Boundary Trace Run

**Files:**
- Create: `docs/superpowers/notes/2026-07-31-agent-replay-boundary-trace-inspector-validation.md`
- Test command only: no production files unless validation finds defects.

- [ ] **Step 1: Run the focused automated test suite**

Run:

```bash
python -m pytest tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_loader.py tests/test_agent_replay_ui_boundary_formatters.py tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py tests/test_agent_replay_ui_server.py -q
```

Expected: PASS.

- [ ] **Step 2: Run ruff on changed Python files**

Run:

```bash
python -m ruff check lumibot/components/agents/replay_ui/models.py lumibot/components/agents/replay_ui/loader.py lumibot/components/agents/replay_ui/boundary_formatters.py lumibot/components/agents/replay_ui/server.py tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_loader.py tests/test_agent_replay_ui_boundary_formatters.py tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py tests/test_agent_replay_ui_server.py
```

Expected: PASS.

- [ ] **Step 3: Locate a real trace with boundary data**

Run:

```powershell
$trace = Get-ChildItem D:\Lumibot\artifacts -Recurse -Filter *.json |
  Where-Object {
    try {
      (Get-Content $_.FullName -Raw | ConvertFrom-Json).boundary_trace.events.Count -gt 0
    } catch {
      $false
    }
  } |
  Select-Object -First 1 FullName
$trace
```

Expected: one trace file path is printed. If none is printed, run a one-day
benchmark that generates boundary trace data using the current benchmark runner
and API key workflow already used in this project.

- [ ] **Step 4: Start the replay UI with the containing runtime root**

Derive the trace root from the trace discovered in Step 3 and start the UI:

```powershell
cd D:\Lumibot
$tracePath = (Get-ChildItem D:\Lumibot\artifacts -Recurse -Filter *.json |
  Where-Object {
    try {
      (Get-Content $_.FullName -Raw | ConvertFrom-Json).boundary_trace.events.Count -gt 0
    } catch {
      $false
    }
  } |
  Select-Object -First 1 -ExpandProperty FullName)
$traceRoot = Split-Path (Split-Path (Split-Path $tracePath -Parent) -Parent) -Parent
.\.venv\Scripts\python.exe scripts\agent_trace_ui.py --trace-root $traceRoot
```

Expected: server starts and prints a local URL such as `http://127.0.0.1:8765`.

- [ ] **Step 5: Manually verify UI behavior**

In the browser:

1. Select the strategy.
2. Select the backtest run.
3. Select the system run.
4. Click an agent that has boundary trace data.
5. Open `LLM <-> Tool Boundary Trace`.
6. Confirm at least one model turn is visible.
7. Confirm `B09`, `B10`, `B01`, and `B02` appear when the provider path recorded them.
8. Confirm at least one tool call span shows `B03` through `B08`.
9. Click a sidecar-backed event if one exists.
10. Confirm the full sidecar payload loads or a clear error is shown.

- [ ] **Step 6: Record validation note**

Create `docs/superpowers/notes/2026-07-31-agent-replay-boundary-trace-inspector-validation.md`:

```markdown
# Agent Replay Boundary Trace Inspector Validation

**Date:** 2026-07-31
**Branch:** feature/structured-execution-plan-handoff

## Automated Tests

- `python -m pytest tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_loader.py tests/test_agent_replay_ui_boundary_formatters.py tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py tests/test_agent_replay_ui_server.py -q`: PASS
- `python -m ruff check lumibot/components/agents/replay_ui/models.py lumibot/components/agents/replay_ui/loader.py lumibot/components/agents/replay_ui/boundary_formatters.py lumibot/components/agents/replay_ui/server.py tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_loader.py tests/test_agent_replay_ui_boundary_formatters.py tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py tests/test_agent_replay_ui_server.py`: PASS

## Real Trace Checked

- Trace root: paste the `$traceRoot` value used in Step 4.
- Strategy: paste the selected Strategy value shown in the UI.
- Backtest run: paste the selected Backtest Run value shown in the UI.
- System run: paste the selected System Run value shown in the UI.
- Agent checked: paste the clicked agent name shown in the workflow graph.

## UI Findings

- Model turns visible: yes
- Tool batches visible: yes
- Tool call spans visible: yes
- B01-B10 events visible where recorded: yes
- Payload metadata badges visible: yes
- Sidecar payload loaded or gracefully reported: yes

## Notes

- No trading behavior was changed by this feature.
```

Replace the angle-bracket values with the actual observed values.

- [ ] **Step 7: Commit validation note**

Run:

```bash
git add docs/superpowers/notes/2026-07-31-agent-replay-boundary-trace-inspector-validation.md
git commit -m "docs: validate boundary trace replay inspector"
```

---

## Final Verification Before Completion

- [ ] Run focused tests:

```bash
python -m pytest tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_loader.py tests/test_agent_replay_ui_boundary_formatters.py tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py tests/test_agent_replay_ui_server.py -q
```

- [ ] Run lint:

```bash
python -m ruff check lumibot/components/agents/replay_ui/models.py lumibot/components/agents/replay_ui/loader.py lumibot/components/agents/replay_ui/boundary_formatters.py lumibot/components/agents/replay_ui/server.py tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_loader.py tests/test_agent_replay_ui_boundary_formatters.py tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py tests/test_agent_replay_ui_server.py
```

- [ ] Confirm `git status --short` is clean.
- [ ] Confirm every phase has its own passing test gate before the next phase commit.
- [ ] Confirm the validation note names a real trace root.

