# Agent Replay Model Turn Flow UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-version Model Turn Replay UI that explains existing LLM/tool boundary trace events by model turn, tool call, and human-readable flow step.

**Architecture:** Extend the replay loader to publish a richer, UI-oriented `model_turn_replay` shape derived only from existing `boundary_trace.events`. Then update the static browser UI to render a model-turn selector, a conceptual flow panel, and a selected-step detail panel while preserving the current boundary trace list and sidecar loading behavior.

**Tech Stack:** Python dataclasses and loader functions, Flask-served static HTML/CSS/JavaScript, pytest, Playwright smoke tests, existing replay sidecar API.

---

## File Structure

- Modify `lumibot/components/agents/replay_ui/loader.py`
  - Add deterministic model-turn replay grouping helpers.
  - Map trace transitions to UI step numbers.
  - Group tool events by `call_instance_id`, falling back to `call_id`, `tool_batch_id`, then event id.
- Modify `lumibot/components/agents/replay_ui/models.py`
  - Expose the new model-turn replay data through public JSON with existing redaction helpers.
- Modify `lumibot/components/agents/replay_ui/static/app.js`
  - Render `Model Turn Replay`.
  - Add a model turn selector and selected-step detail panel.
  - Reuse existing sidecar loader for B09 full-payload inspection.
- Modify `lumibot/components/agents/replay_ui/static/styles.css`
  - Style the flow panel, step buttons, tool-call lanes, and detail panel.
- Modify tests:
  - `tests/test_agent_replay_ui_loader.py`
  - `tests/test_agent_replay_ui_models.py`
  - `tests/test_agent_replay_ui_static.py`
  - `tests/test_agent_replay_ui_browser.py`

No recorder, runtime, prompt, or trading logic should be changed.

---

### Task 1: Publish Model Turn Replay Data From Loader

**Files:**
- Modify: `tests/test_agent_replay_ui_loader.py`
- Modify: `lumibot/components/agents/replay_ui/loader.py`
- Modify: `lumibot/components/agents/replay_ui/models.py`
- Test: `tests/test_agent_replay_ui_loader.py::test_loader_builds_model_turn_replay_steps`
- Test: `tests/test_agent_replay_ui_loader.py::test_loader_groups_tool_calls_by_call_instance_before_call_id`

- [ ] **Step 1: Add failing loader test for model turn replay steps**

Append this test to `tests/test_agent_replay_ui_loader.py`:

```python
def test_loader_builds_model_turn_replay_steps(tmp_path):
    trace_path = tmp_path / "traces" / "execution_agent" / "trace.json"
    _write_trace(
        trace_path,
        {
            "agent": "execution_agent",
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
                        "payload": {"preview": "ADK request"},
                    },
                    {
                        "transition": "B10_LITELLM_TO_PROVIDER",
                        "model_turn_id": "turn-1",
                        "status": "success",
                        "payload_meta": {"semantic_completeness": "partial", "truncated": True},
                    },
                    {
                        "transition": "B01_PROVIDER_TO_LITELLM",
                        "model_turn_id": "turn-1",
                        "status": "success",
                        "payload": {
                            "response": {
                                "choices": [
                                    {
                                        "message": {
                                            "tool_calls": [
                                                {
                                                    "id": "call-submit",
                                                    "type": "function",
                                                    "function": {
                                                        "name": "orders_submit_order",
                                                        "arguments": "{\"symbol\":\"QQQ\"}",
                                                    },
                                                }
                                            ]
                                        }
                                    }
                                ]
                            }
                        },
                    },
                    {
                        "transition": "B02_LITELLM_TO_ADK",
                        "model_turn_id": "turn-1",
                        "tool_batch_id": "turn-1:batch:0001",
                        "status": "success",
                        "payload": {
                            "tool_calls": [
                                {
                                    "call_id": "call-submit",
                                    "name": "orders_submit_order",
                                    "arguments": {"symbol": "QQQ"},
                                    "call_instance_id": "turn-1:batch:0001:call:0001",
                                }
                            ]
                        },
                    },
                    {
                        "transition": "B03_ADK_TO_FUNCTION_TOOL",
                        "model_turn_id": "turn-1",
                        "tool_batch_id": "turn-1:batch:0001",
                        "call_id": "call-submit",
                        "call_instance_id": "turn-1:batch:0001:call:0001",
                        "status": "success",
                        "payload": {"tool_name": "orders_submit_order", "model_arguments": {"symbol": "QQQ"}},
                    },
                    {
                        "transition": "B08_FUNCTION_TOOL_TO_ADK",
                        "model_turn_id": "turn-1",
                        "tool_batch_id": "turn-1:batch:0001",
                        "call_id": "call-submit",
                        "call_instance_id": "turn-1:batch:0001:call:0001",
                        "status": "success",
                        "payload": {"function_name": "orders_submit_order", "model_facing_response": {"ok": True}},
                    },
                ],
            },
        },
    )

    public = load_agent_trace(trace_path).to_public_dict()
    replay = public["boundary_trace"]["model_turn_replay"]

    assert len(replay) == 1
    turn = replay[0]
    assert turn["model_turn_id"] == "turn-1"
    assert [step["ui_step"] for step in turn["model_steps"]] == ["1", "2", "3", "4"]
    assert turn["model_steps"][0]["transition"] == "B09_ADK_TO_LITELLM"
    assert turn["model_steps"][1]["completeness"] == "partial"
    assert len(turn["tool_calls"]) == 1
    call = turn["tool_calls"][0]
    assert call["tool_name"] == "orders_submit_order"
    assert call["call_instance_id"] == "turn-1:batch:0001:call:0001"
    assert [step["ui_step"] for step in call["steps"]] == ["5.1", "10.1"]
    assert call["steps"][0]["event"]["payload"]["model_arguments"]["symbol"] == "QQQ"
```

- [ ] **Step 2: Add failing fallback grouping test**

Append this test to `tests/test_agent_replay_ui_loader.py`:

```python
def test_loader_groups_tool_calls_by_call_instance_before_call_id(tmp_path):
    trace_path = tmp_path / "traces" / "execution_agent" / "trace.json"
    _write_trace(
        trace_path,
        {
            "agent": "execution_agent",
            "model": "openai/test",
            "request": {"context": {}, "runtime_context": {}},
            "events": [],
            "boundary_trace": {
                "schema_version": 1,
                "events": [
                    {
                        "transition": "B03_ADK_TO_FUNCTION_TOOL",
                        "model_turn_id": "turn-1",
                        "tool_batch_id": "batch-1",
                        "call_id": "provider-call",
                        "call_instance_id": "instance-A",
                        "payload": {"tool_name": "account_positions"},
                    },
                    {
                        "transition": "B03_ADK_TO_FUNCTION_TOOL",
                        "model_turn_id": "turn-1",
                        "tool_batch_id": "batch-1",
                        "call_id": "provider-call",
                        "call_instance_id": "instance-B",
                        "payload": {"tool_name": "account_portfolio"},
                    },
                ],
            },
        },
    )

    public = load_agent_trace(trace_path).to_public_dict()
    calls = public["boundary_trace"]["model_turn_replay"][0]["tool_calls"]

    assert [call["call_instance_id"] for call in calls] == ["instance-A", "instance-B"]
    assert [call["tool_name"] for call in calls] == ["account_positions", "account_portfolio"]
```

- [ ] **Step 3: Run tests and confirm they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_loader.py::test_loader_builds_model_turn_replay_steps tests\test_agent_replay_ui_loader.py::test_loader_groups_tool_calls_by_call_instance_before_call_id -q
```

Expected: both tests fail because `model_turn_replay` does not exist yet.

- [ ] **Step 4: Implement public model field**

In `lumibot/components/agents/replay_ui/models.py`, update `BoundaryTraceReplay`:

```python
@dataclass
class BoundaryTraceReplay:
    available: bool = False
    schema_version: int | None = None
    events: list[BoundaryEventReplay] = field(default_factory=list)
    model_turns: list[dict[str, Any]] = field(default_factory=list)
    model_turn_replay: list[dict[str, Any]] = field(default_factory=list)
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
            "model_turns": [
                public_turn
                for turn in self.model_turns
                if (public_turn := _public_boundary_model_turn(turn))
            ],
            "model_turn_replay": redact_sensitive(self.model_turn_replay),
            "diagnostics": redact_sensitive(self.diagnostics),
            "message": redact_sensitive(self.message),
        }
```

- [ ] **Step 5: Implement loader grouping helpers**

In `lumibot/components/agents/replay_ui/loader.py`, add constants near existing transition sets:

```python
MODEL_STEP_BY_TRANSITION = {
    "B09_ADK_TO_LITELLM": "1",
    "B10_LITELLM_TO_PROVIDER": "2",
    "B01_PROVIDER_TO_LITELLM": "3",
    "B02_LITELLM_TO_ADK": "4",
}
FINAL_STEP_BY_TRANSITION = {
    "B09_ADK_TO_LITELLM": "11",
    "B10_LITELLM_TO_PROVIDER": "12",
    "B01_PROVIDER_TO_LITELLM": "13",
    "B02_LITELLM_TO_ADK": "14",
}
LOCAL_TOOL_STEP_BASE_BY_TRANSITION = {
    "B03_ADK_TO_FUNCTION_TOOL": "5",
    "B04_FUNCTION_TOOL_TO_WRAPPER": "6",
    "B05_WRAPPER_TO_PYTHON_TOOL": "7",
    "B06_PYTHON_TOOL_TO_WRAPPER": "8",
    "B07_WRAPPER_TO_FUNCTION_TOOL": "9",
    "B08_FUNCTION_TOOL_TO_ADK": "10",
}
```

Still in `loader.py`, update `_load_boundary_trace`:

```python
    return BoundaryTraceReplay(
        available=True,
        schema_version=boundary.get("schema_version") if isinstance(boundary.get("schema_version"), int) else None,
        events=events,
        model_turns=_group_boundary_events(events),
        model_turn_replay=_build_model_turn_replay(events),
        diagnostics=boundary.get("diagnostics") if isinstance(boundary.get("diagnostics"), list) else [],
        message="Boundary trace data is available for this agent run.",
    )
```

Add helpers below `_boundary_tool_name`:

```python
def _build_model_turn_replay(events: list[BoundaryEventReplay]) -> list[dict[str, Any]]:
    turns: dict[str, list[BoundaryEventReplay]] = {}
    for event in events:
        turn_id = event.model_turn_id or "unknown-model-turn"
        turns.setdefault(turn_id, []).append(event)

    replay = []
    for turn_index, (turn_id, turn_events) in enumerate(turns.items(), start=1):
        request_events = [event for event in turn_events if event.transition in REQUEST_RESPONSE_TRANSITIONS]
        tool_events = [event for event in turn_events if event.transition in LOCAL_TOOL_TRANSITIONS]
        is_final_turn = not tool_events and _turn_has_final_text(request_events)
        replay.append(
            {
                "model_turn_id": turn_id,
                "turn_index": turn_index,
                "kind": "final_answer" if is_final_turn else "tool_calling",
                "model_steps": [
                    _model_step(event, is_final_turn=is_final_turn)
                    for event in request_events
                ],
                "tool_calls": _model_turn_tool_calls(tool_events),
            }
        )
    return replay


def _model_step(event: BoundaryEventReplay, *, is_final_turn: bool) -> dict[str, Any]:
    step_map = FINAL_STEP_BY_TRANSITION if is_final_turn else MODEL_STEP_BY_TRANSITION
    return {
        "ui_step": step_map.get(event.transition, "?"),
        "transition": event.transition,
        "label": _step_label(step_map.get(event.transition, "?"), event.transition),
        "completeness": event.payload_meta.get("semantic_completeness") if isinstance(event.payload_meta, dict) else None,
        "truncated": bool(event.payload_meta.get("truncated")) if isinstance(event.payload_meta, dict) else False,
        "event": event.to_public_dict(),
    }


def _model_turn_tool_calls(events: list[BoundaryEventReplay]) -> list[dict[str, Any]]:
    grouped: dict[str, list[BoundaryEventReplay]] = {}
    for event in events:
        group_key = (
            _call_instance_id(event)
            or event.call_id
            or event.tool_batch_id
            or f"event:{event.id}"
        )
        grouped.setdefault(group_key, []).append(event)

    calls = []
    for call_index, (group_key, call_events) in enumerate(grouped.items(), start=1):
        calls.append(
            {
                "call_key": group_key,
                "call_index": call_index,
                "call_id": _first_non_empty([event.call_id for event in call_events]),
                "call_instance_id": _first_non_empty([_call_instance_id(event) for event in call_events]),
                "tool_batch_id": _first_non_empty([event.tool_batch_id for event in call_events]),
                "tool_name": _boundary_tool_name(call_events) or "Unknown tool",
                "steps": [
                    _tool_step(event, call_index)
                    for event in call_events
                    if event.transition in LOCAL_TOOL_STEP_BASE_BY_TRANSITION
                ],
            }
        )
    return calls


def _tool_step(event: BoundaryEventReplay, call_index: int) -> dict[str, Any]:
    base = LOCAL_TOOL_STEP_BASE_BY_TRANSITION.get(event.transition, "?")
    ui_step = f"{base}.{call_index}" if base != "?" else "?"
    return {
        "ui_step": ui_step,
        "transition": event.transition,
        "label": _step_label(ui_step, event.transition),
        "completeness": event.payload_meta.get("semantic_completeness") if isinstance(event.payload_meta, dict) else None,
        "truncated": bool(event.payload_meta.get("truncated")) if isinstance(event.payload_meta, dict) else False,
        "event": event.to_public_dict(),
    }


def _call_instance_id(event: BoundaryEventReplay) -> str | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    value = payload.get("call_instance_id")
    return value if isinstance(value, str) and value else None


def _first_non_empty(values: list[str | None]) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _turn_has_final_text(events: list[BoundaryEventReplay]) -> bool:
    for event in events:
        if event.transition not in {"B01_PROVIDER_TO_LITELLM", "B02_LITELLM_TO_ADK"}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if json.dumps(payload, ensure_ascii=False).find('"text"') >= 0 or "RESULT:" in json.dumps(payload, ensure_ascii=False):
            return True
    return False


def _step_label(ui_step: str, transition: str) -> str:
    return f"{ui_step} {transition.replace('_', ' -> ', 1)}"
```

- [ ] **Step 6: Run loader tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_loader.py::test_loader_builds_model_turn_replay_steps tests\test_agent_replay_ui_loader.py::test_loader_groups_tool_calls_by_call_instance_before_call_id -q
```

Expected: both tests pass.

- [ ] **Step 7: Commit Task 1**

Run:

```powershell
git add lumibot\components\agents\replay_ui\loader.py lumibot\components\agents\replay_ui\models.py tests\test_agent_replay_ui_loader.py
git commit -m "feat: build model turn replay data"
```

---

### Task 2: Render Model Turn Replay UI Shell

**Files:**
- Modify: `tests/test_agent_replay_ui_static.py`
- Modify: `lumibot/components/agents/replay_ui/static/app.js`
- Modify: `lumibot/components/agents/replay_ui/static/styles.css`
- Test: `tests/test_agent_replay_ui_static.py::test_static_javascript_renders_model_turn_replay`

- [ ] **Step 1: Add static JavaScript test**

Append this test to `tests/test_agent_replay_ui_static.py`:

```python
def test_static_javascript_renders_model_turn_replay():
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    css = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")

    assert "Model Turn Replay" in javascript
    assert "renderModelTurnReplayArea" in javascript
    assert "renderModelTurnSelector" in javascript
    assert "renderModelTurnFlow" in javascript
    assert "renderBoundaryStepButton" in javascript
    assert "renderSelectedBoundaryStepDetail" in javascript
    assert "model-turn-replay" in css
    assert "boundary-flow-step" in css
    assert "boundary-step-detail" in css
```

- [ ] **Step 2: Run test and confirm it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_static.py::test_static_javascript_renders_model_turn_replay -q
```

Expected: fail because these functions/classes do not exist.

- [ ] **Step 3: Add replay selection state and event handling**

In `lumibot/components/agents/replay_ui/static/app.js`, extend `state` near the top:

```javascript
    selectedModelTurnIdByAgentId: {},
    selectedBoundaryStepIdByAgentId: {},
```

Inside the existing document click handler, after sidecar handling, add:

```javascript
      const stepButton = event.target.closest(".boundary-flow-step");
      if (stepButton) {
        const agentId = stepButton.getAttribute("data-agent-id");
        const eventId = stepButton.getAttribute("data-boundary-event-id");
        if (agentId && eventId) {
          state.selectedBoundaryStepIdByAgentId[agentId] = eventId;
          renderAgentDetail(selectedRun(), selectedSystemRun(selectedRun()), selectedAgent(selectedSystemRun(selectedRun())));
        }
        return;
      }
```

Add a delegated change handler near the click handler:

```javascript
    document.addEventListener("change", (event) => {
      const select = event.target.closest(".model-turn-select");
      if (!select) {
        return;
      }
      const agentId = select.getAttribute("data-agent-id");
      if (!agentId) {
        return;
      }
      state.selectedModelTurnIdByAgentId[agentId] = select.value;
      delete state.selectedBoundaryStepIdByAgentId[agentId];
      renderAgentDetail(selectedRun(), selectedSystemRun(selectedRun()), selectedAgent(selectedSystemRun(selectedRun())));
    });
```

- [ ] **Step 4: Replace boundary area body with new replay section plus legacy list**

In `renderBoundaryTraceArea(agent)`, replace the `body` assignment with:

```javascript
    const body = `
      ${renderModelTurnReplayArea(agent, trace, eventsById, turns)}
      <div class="boundary-legacy-list">
        <h4>Raw Boundary Event List</h4>
        ${turns.length ? turns.map((turn, index) => renderBoundaryModelTurn(turn, eventsById, index === 0)).join("") : '<div class="empty-state">Boundary trace contains no model turns.</div>'}
      </div>
    `;
```

Keep the outer `renderCollapsibleSection("LLM <-> Tool Boundary Trace", ...)` call unchanged.

- [ ] **Step 5: Add Model Turn Replay rendering functions**

Add these functions before `renderBoundaryModelTurn` in `app.js`:

```javascript
  function renderModelTurnReplayArea(agent, trace, eventsById, legacyTurns) {
    const replayTurns = Array.isArray(trace.model_turn_replay)
      ? trace.model_turn_replay.map(boundaryItemOrEmpty)
      : [];
    if (!replayTurns.length) {
      return `
        <section class="model-turn-replay">
          <h4>Model Turn Replay</h4>
          <div class="empty-state">No model turn replay data is available for this trace.</div>
        </section>
      `;
    }

    const selectedTurn = selectedModelTurnReplay(agent, replayTurns);
    const stepEvents = modelTurnReplayStepEvents(selectedTurn);
    const selectedEventId = selectedBoundaryStepId(agent, stepEvents);
    const selectedEvent = stepEvents.find((event) => event.id === selectedEventId) || stepEvents[0] || null;

    return `
      <section class="model-turn-replay">
        <div class="model-turn-replay-header">
          <h4>Model Turn Replay</h4>
          ${renderModelTurnSelector(agent, replayTurns, selectedTurn)}
        </div>
        <div class="model-turn-replay-grid">
          ${renderModelTurnFlow(agent, selectedTurn, selectedEventId)}
          ${renderSelectedBoundaryStepDetail(selectedEvent)}
        </div>
      </section>
    `;
  }

  function selectedModelTurnReplay(agent, replayTurns) {
    const agentId = agent && agent.id ? agent.id : "";
    const selectedId = state.selectedModelTurnIdByAgentId[agentId];
    return replayTurns.find((turn) => turn.model_turn_id === selectedId) || replayTurns[0] || {};
  }

  function modelTurnReplayStepEvents(turn) {
    const events = [];
    (Array.isArray(turn.model_steps) ? turn.model_steps : []).forEach((step) => {
      if (step && step.event) {
        events.push(step.event);
      }
    });
    (Array.isArray(turn.tool_calls) ? turn.tool_calls : []).forEach((call) => {
      (Array.isArray(call.steps) ? call.steps : []).forEach((step) => {
        if (step && step.event) {
          events.push(step.event);
        }
      });
    });
    return events;
  }

  function selectedBoundaryStepId(agent, events) {
    const agentId = agent && agent.id ? agent.id : "";
    const selectedId = state.selectedBoundaryStepIdByAgentId[agentId];
    return events.some((event) => event.id === selectedId) ? selectedId : (events[0] && events[0].id);
  }

  function renderModelTurnSelector(agent, replayTurns, selectedTurn) {
    const agentId = agent && agent.id ? agent.id : "";
    const options = replayTurns.map((turn) => {
      const selected = turn.model_turn_id === selectedTurn.model_turn_id ? "selected" : "";
      return `<option value="${escapeHtml(turn.model_turn_id || "")}" ${selected}>Model Turn ${escapeHtml(String(turn.turn_index || modelTurnLabel(turn.model_turn_id)))}</option>`;
    }).join("");
    return `
      <label class="model-turn-selector">
        <span>Model Turn</span>
        <select class="model-turn-select" data-agent-id="${escapeHtml(agentId)}">
          ${options}
        </select>
      </label>
    `;
  }
```

- [ ] **Step 6: Add flow and detail functions**

Continue adding these functions:

```javascript
  function renderModelTurnFlow(agent, turn, selectedEventId) {
    const agentId = agent && agent.id ? agent.id : "";
    const modelSteps = Array.isArray(turn.model_steps) ? turn.model_steps : [];
    const toolCalls = Array.isArray(turn.tool_calls) ? turn.tool_calls : [];
    return `
      <div class="model-turn-flow" aria-label="Model turn boundary flow">
        <div class="boundary-flow-lane">
          ${modelSteps.map((step) => renderBoundaryStepButton(agentId, step, selectedEventId)).join("")}
        </div>
        <div class="tool-call-flow-list">
          ${toolCalls.length ? toolCalls.map((call) => renderToolCallFlow(agentId, call, selectedEventId)).join("") : '<div class="empty-state">This model turn did not call tools.</div>'}
        </div>
      </div>
    `;
  }

  function renderToolCallFlow(agentId, call, selectedEventId) {
    const steps = Array.isArray(call.steps) ? call.steps : [];
    return `
      <div class="tool-call-flow">
        <div class="tool-call-flow-title">
          <strong>Tool Call ${escapeHtml(String(call.call_index || ""))}: ${escapeHtml(call.tool_name || "Unknown tool")}</strong>
          <span>${escapeHtml(call.call_id || call.call_instance_id || "")}</span>
        </div>
        <div class="boundary-flow-lane tool-boundary-flow-lane">
          ${steps.map((step) => renderBoundaryStepButton(agentId, step, selectedEventId)).join("")}
        </div>
      </div>
    `;
  }

  function renderBoundaryStepButton(agentId, step, selectedEventId) {
    step = boundaryItemOrEmpty(step);
    const event = boundaryItemOrEmpty(step.event);
    const isSelected = event.id && event.id === selectedEventId;
    const classes = ["boundary-flow-step"];
    if (isSelected) {
      classes.push("selected");
    }
    if (step.truncated || (event.payload_meta && event.payload_meta.truncated)) {
      classes.push("partial");
    }
    return `
      <button type="button" class="${classes.join(" ")}" data-agent-id="${escapeHtml(agentId)}" data-boundary-event-id="${escapeHtml(event.id || "")}">
        <span class="boundary-flow-step-number">${escapeHtml(step.ui_step || "?")}</span>
        <span class="boundary-flow-step-label">${escapeHtml(step.transition || event.transition || "UNKNOWN")}</span>
      </button>
    `;
  }

  function renderSelectedBoundaryStepDetail(event) {
    if (!event) {
      return `<div class="boundary-step-detail"><div class="empty-state">Select a boundary step to inspect details.</div></div>`;
    }
    const summary = boundaryItemOrEmpty(event.summary);
    return `
      <aside class="boundary-step-detail">
        <h4>Selected Step Detail</h4>
        <div class="boundary-step-detail-meta">
          <div><strong>Transition</strong><span>${escapeHtml(event.transition || "UNKNOWN")}</span></div>
          <div><strong>Status</strong><span>${escapeHtml(event.status || "unknown")}</span></div>
          <div><strong>Route</strong><span>${escapeHtml(summary.source || "?")} -> ${escapeHtml(summary.target || "?")}</span></div>
        </div>
        ${summary.explanation ? `<div class="notice">${escapeHtml(summary.explanation)}</div>` : ""}
        <pre>${formatValue({
          id: event.id,
          transition: event.transition,
          status: event.status,
          model_turn_id: event.model_turn_id,
          tool_batch_id: event.tool_batch_id,
          call_id: event.call_id,
          payload_meta: event.payload_meta,
          payload: event.payload,
        })}</pre>
        ${renderBoundarySidecarButton(event)}
      </aside>
    `;
  }
```

- [ ] **Step 7: Add CSS**

Append to `lumibot/components/agents/replay_ui/static/styles.css`:

```css
.model-turn-replay {
  border: 1px solid var(--border);
  padding: 12px;
  margin-bottom: 16px;
  background: var(--panel);
}

.model-turn-replay-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.model-turn-selector {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.model-turn-replay-grid {
  display: grid;
  grid-template-columns: minmax(280px, 0.9fr) minmax(320px, 1.1fr);
  gap: 12px;
}

.model-turn-flow,
.boundary-step-detail {
  border: 1px solid var(--border);
  background: var(--panel-muted);
  padding: 10px;
}

.boundary-flow-lane {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}

.boundary-flow-step {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border);
  background: var(--panel);
  color: var(--text);
  padding: 6px 8px;
  cursor: pointer;
  max-width: 100%;
}

.boundary-flow-step.selected {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-muted);
}

.boundary-flow-step.partial {
  border-style: dashed;
}

.boundary-flow-step-number {
  font-weight: 700;
}

.boundary-flow-step-label {
  font-size: 11px;
  overflow-wrap: anywhere;
}

.tool-call-flow {
  border-top: 1px solid var(--border);
  padding-top: 10px;
  margin-top: 10px;
}

.tool-call-flow-title {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 12px;
}

.boundary-step-detail-meta {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 10px;
}

.boundary-step-detail-meta div {
  border: 1px solid var(--border);
  padding: 6px;
  background: var(--panel);
}

.boundary-step-detail-meta strong,
.boundary-step-detail-meta span {
  display: block;
}

@media (max-width: 900px) {
  .model-turn-replay-grid {
    grid-template-columns: 1fr;
  }

  .boundary-step-detail-meta {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 8: Run static test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_static.py::test_static_javascript_renders_model_turn_replay -q
```

Expected: pass.

- [ ] **Step 9: Commit Task 2**

Run:

```powershell
git add lumibot\components\agents\replay_ui\static\app.js lumibot\components\agents\replay_ui\static\styles.css tests\test_agent_replay_ui_static.py
git commit -m "feat: render model turn replay shell"
```

---

### Task 3: Browser Smoke Test For Model Turn Replay

**Files:**
- Modify: `tests/test_agent_replay_ui_browser.py`
- Test: `tests/test_agent_replay_ui_browser.py::test_browser_shows_model_turn_replay_for_boundary_trace`

- [ ] **Step 1: Add browser test dataset with boundary trace**

Append this helper to `tests/test_agent_replay_ui_browser.py`:

```python
def _boundary_trace_for_model_turn_replay() -> dict:
    return {
        "available": True,
        "schema_version": 1,
        "events": [
            {
                "id": "event-b09",
                "transition": "B09_ADK_TO_LITELLM",
                "model_turn_id": "turn-1",
                "status": "success",
                "payload": {"preview": "ADK request"},
                "payload_meta": {"semantic_completeness": "complete"},
                "summary": {"label": "ADK request", "source": "Google ADK", "target": "LiteLLM", "explanation": "Request sent."},
            },
            {
                "id": "event-b01",
                "transition": "B01_PROVIDER_TO_LITELLM",
                "model_turn_id": "turn-1",
                "status": "success",
                "payload": {"response": {"tool_calls": [{"name": "orders_submit_order"}]}},
                "payload_meta": {"semantic_completeness": "complete"},
                "summary": {"label": "Model tool call", "source": "OpenAI LLM", "target": "LiteLLM", "explanation": "Model requested tool."},
            },
            {
                "id": "event-b03",
                "transition": "B03_ADK_TO_FUNCTION_TOOL",
                "model_turn_id": "turn-1",
                "tool_batch_id": "batch-1",
                "call_id": "call-1",
                "status": "success",
                "payload": {"tool_name": "orders_submit_order", "model_arguments": {"symbol": "QQQ"}},
                "payload_meta": {"semantic_completeness": "complete"},
                "summary": {"label": "Dispatch tool", "source": "Google ADK", "target": "ADK FunctionTool", "explanation": "Dispatch."},
            },
        ],
        "model_turns": [],
        "model_turn_replay": [
            {
                "model_turn_id": "turn-1",
                "turn_index": 1,
                "kind": "tool_calling",
                "model_steps": [
                    {"ui_step": "1", "transition": "B09_ADK_TO_LITELLM", "event": {"id": "event-b09", "transition": "B09_ADK_TO_LITELLM", "status": "success", "payload": {"preview": "ADK request"}, "payload_meta": {"semantic_completeness": "complete"}, "summary": {"label": "ADK request", "source": "Google ADK", "target": "LiteLLM", "explanation": "Request sent."}}},
                    {"ui_step": "3", "transition": "B01_PROVIDER_TO_LITELLM", "event": {"id": "event-b01", "transition": "B01_PROVIDER_TO_LITELLM", "status": "success", "payload": {"response": {"tool_calls": [{"name": "orders_submit_order"}]}}, "payload_meta": {"semantic_completeness": "complete"}, "summary": {"label": "Model tool call", "source": "OpenAI LLM", "target": "LiteLLM", "explanation": "Model requested tool."}}},
                ],
                "tool_calls": [
                    {
                        "call_key": "call-1",
                        "call_index": 1,
                        "call_id": "call-1",
                        "call_instance_id": "instance-1",
                        "tool_batch_id": "batch-1",
                        "tool_name": "orders_submit_order",
                        "steps": [
                            {"ui_step": "5.1", "transition": "B03_ADK_TO_FUNCTION_TOOL", "event": {"id": "event-b03", "transition": "B03_ADK_TO_FUNCTION_TOOL", "status": "success", "payload": {"tool_name": "orders_submit_order", "model_arguments": {"symbol": "QQQ"}}, "payload_meta": {"semantic_completeness": "complete"}, "summary": {"label": "Dispatch tool", "source": "Google ADK", "target": "ADK FunctionTool", "explanation": "Dispatch."}}},
                        ],
                    }
                ],
            }
        ],
        "diagnostics": [],
        "message": "Boundary trace data is available.",
    }
```

- [ ] **Step 2: Add browser smoke test**

Append:

```python
def test_browser_shows_model_turn_replay_for_boundary_trace(page):
    agent = _public_agent("execution_agent", boundary_trace=_boundary_trace_for_model_turn_replay())
    dataset = _public_dataset([agent], [])
    page.route("**/api/replay-data", lambda route: route.fulfill(json=dataset))
    page.goto("http://127.0.0.1:8765")

    page.get_by_role("button", name=re.compile("execution_agent")).click()

    expect(page.get_by_text("Model Turn Replay")).to_be_visible()
    expect(page.locator(".model-turn-select")).to_be_visible()
    expect(page.get_by_text("Tool Call 1: orders_submit_order")).to_be_visible()
    expect(page.get_by_text("B03_ADK_TO_FUNCTION_TOOL")).to_be_visible()

    page.get_by_role("button", name=re.compile("5\\.1")).click()
    expect(page.get_by_text("Selected Step Detail")).to_be_visible()
    expect(page.get_by_text("QQQ")).to_be_visible()
```

If this file uses a different Playwright fixture pattern for `page`, adapt the test to the existing server fixture in the file without changing the assertions.

- [ ] **Step 3: Run browser test and confirm failure or fixture mismatch**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_browser.py::test_browser_shows_model_turn_replay_for_boundary_trace -q
```

Expected before Task 2 implementation: fail. Expected after Task 2: pass. This browser file already uses `page.route("**/api/dataset", ...)` and `page.goto(replay_url)`, so keep that pattern.

- [ ] **Step 4: Commit Task 3**

Run:

```powershell
git add tests\test_agent_replay_ui_browser.py
git commit -m "test: cover model turn replay browser view"
```

---

### Task 4: Validate Against Real Trace And Polish Existing Tests

**Files:**
- Verify:
  - `tests/test_agent_replay_ui_loader.py`
  - `tests/test_agent_replay_ui_static.py`
  - `tests/test_agent_replay_ui_browser.py`
  - `lumibot/components/agents/replay_ui/static/app.js`
  - `lumibot/components/agents/replay_ui/static/styles.css`

- [ ] **Step 1: Run focused automated tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_loader.py tests\test_agent_replay_ui_models.py tests\test_agent_replay_ui_static.py tests\test_agent_replay_ui_browser.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run ruff on touched files**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\replay_ui\loader.py lumibot\components\agents\replay_ui\models.py tests\test_agent_replay_ui_loader.py tests\test_agent_replay_ui_models.py tests\test_agent_replay_ui_static.py tests\test_agent_replay_ui_browser.py
```

Expected: `All checks passed!`

- [ ] **Step 3: Start local UI for manual validation**

Run:

```powershell
cd D:\Lumibot
.\.venv\Scripts\python.exe scripts\agent_trace_ui.py
```

Open:

```text
http://127.0.0.1:8765
```

Select:

```text
Strategy: AITradingTeamGrowthExecutionTestStrategy
Backtest Run: 20260801_154220_235490 / growth-execution-test
System Run: backtesting|2024-09-05T09:30:00-04:00|AITradingTeamGrowthExecutionTestStrategy
Agent: execution_agent
```

Expected manual result:

- `Model Turn Replay` is visible.
- The selector lists 3 model turns.
- Model Turn 1 shows:
  - `account_positions`
  - `account_portfolio`
  - `orders_open_orders`
  - `market_last_price`
- Model Turn 2 shows:
  - `orders_submit_order`
- Model Turn 3 shows no tool calls and shows final response steps.
- Selecting step `1` or `11` shows a sidecar load button when B09 sidecar metadata is present.
- Selecting `5.1` for `orders_submit_order` shows QQQ, buy, 217, and limit price 458.67 somewhere in the detail payload.

- [ ] **Step 4: Fix only validation failures**

If any expectation fails, make the smallest change in the responsible file:

- Loader grouping wrong: fix `loader.py` helper.
- Missing public JSON field: fix `models.py`.
- UI rendering wrong: fix `app.js`.
- Layout/readability issue: fix `styles.css`.
- Test fixture wrong: fix only the test setup.

Then rerun the failing command from Steps 1-3.

- [ ] **Step 5: Commit validation polish**

If Step 4 changed files, run:

```powershell
git add lumibot\components\agents\replay_ui tests
git commit -m "fix: polish model turn replay validation"
```

If no files changed, skip this commit.

---

## Final Verification

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_loader.py tests\test_agent_replay_ui_models.py tests\test_agent_replay_ui_static.py tests\test_agent_replay_ui_browser.py -q
.\.venv\Scripts\python.exe -m ruff check lumibot\components\agents\replay_ui\loader.py lumibot\components\agents\replay_ui\models.py tests\test_agent_replay_ui_loader.py tests\test_agent_replay_ui_models.py tests\test_agent_replay_ui_static.py tests\test_agent_replay_ui_browser.py
```

Expected:

- pytest passes.
- ruff passes.
- Manual UI validation against `20260801_154220_235490` confirms the acceptance criteria.

---

## Self-Review

Spec coverage:

- Agent/model-turn/tool-call/boundary-step grouping: Task 1.
- Human UI step numbers and raw transition mapping: Task 1 and Task 2.
- Model turn selector: Task 2.
- Selected step detail panel: Task 2.
- B09 sidecar visibility: Task 2 and Task 4.
- Existing sections preserved: Task 2 changes only `boundaryTraceArea`; Task 4 verifies existing tests.
- Known completeness limits: Task 2 displays partial/truncated badges through existing metadata; Task 4 verifies B10 remains labelled partial.

Placeholder scan:

- No TBD/TODO placeholders.
- All planned tests include concrete assertions.
- All commands include expected results.

Type consistency:

- Loader emits `boundary_trace.model_turn_replay`.
- Model exposes `model_turn_replay` through public dict.
- JavaScript reads `trace.model_turn_replay`.
- Tool calls use `call_instance_id`, `call_id`, `tool_batch_id`, `tool_name`, and `steps`.
