# Agent Replay Trace Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record richer agent tool transparency data in traces and display it in the Agent Workflow Replay UI.

**Architecture:** Add trace-only payload helpers in `AgentHandle` so replay cache keys stay stable while traces get richer tool surface, availability, and diagnostics fields. Extend replay loader/models to carry the new trace fields, then update the static UI to render them with backward-compatible empty states.

**Tech Stack:** Python dataclasses, Lumibot agent runtime, JSON trace files, Flask/static replay UI, vanilla JavaScript, pytest, ruff.

---

## File Structure

- Modify `lumibot/components/agents/manager.py`
  - Add trace-safe tool surface helpers.
  - Track tools filtered by trading permission.
  - Add tool result diagnostics helper.
  - Store richer request payload in trace files without changing replay cache payload.
- Modify `lumibot/components/agents/replay_ui/models.py`
  - Expose `defaults`, `safety_requirements`, `tool_availability`, and `diagnostics`.
- Modify `lumibot/components/agents/replay_ui/loader.py`
  - Load diagnostics from event-based and flat-list trace formats.
- Modify `lumibot/components/agents/replay_ui/static/app.js`
  - Render tool availability, defaults, safety requirements, and diagnostics.
- Modify `lumibot/components/agents/replay_ui/static/styles.css`
  - Add small styles for diagnostics and availability lists if needed.
- Add or modify tests:
  - `tests/test_agent_trace_tool_surface.py`
  - `tests/test_agent_replay_ui_models.py`
  - `tests/test_agent_replay_ui_loader.py`
  - `tests/test_agent_replay_ui_browser.py`
  - `tests/test_agent_replay_ui_static.py`

---

## Task 1: Trace Rich Tool Surface and Availability

**Files:**
- Create: `tests/test_agent_trace_tool_surface.py`
- Modify: `lumibot/components/agents/manager.py`

- [ ] **Step 1: Write failing tests for trace-safe tool surface helpers**

Create `tests/test_agent_trace_tool_surface.py` with tests that exercise helper behavior directly:

```python
from __future__ import annotations

from lumibot.components.agents.manager import (
    _tool_safety_requirements_for_trace,
    _tool_surface_entry_for_trace,
)
from lumibot.components.agents.schemas import BoundTool


def sample_tool(symbol: str, quantity: int = 1, side: str = "buy") -> dict[str, object]:
    return {"symbol": symbol, "quantity": quantity, "side": side}


def test_tool_surface_entry_records_signature_annotations_defaults_and_metadata():
    tool = BoundTool(
        name="orders_submit_order",
        description="Submit an order.",
        function=sample_tool,
        source="local",
        metadata={"kind": "builtin", "replay_on_cache": True, "mutates_trading": True},
    )

    entry = _tool_surface_entry_for_trace(tool)

    assert entry["name"] == "orders_submit_order"
    assert entry["description"] == "Submit an order."
    assert entry["source"] == "local"
    assert "symbol" in entry["signature"]
    assert entry["annotations"]["symbol"] == "str"
    assert entry["annotations"]["quantity"] == "int"
    assert entry["defaults"]["quantity"] == 1
    assert entry["defaults"]["side"] == "buy"
    assert entry["metadata"]["mutates_trading"] is True
    assert entry["safety_requirements"] == _tool_safety_requirements_for_trace("orders_submit_order")


def test_tool_surface_entry_handles_tools_without_annotations_or_defaults():
    def no_annotations(value):
        return value

    tool = BoundTool(name="plain_tool", description="Plain.", function=no_annotations)

    entry = _tool_surface_entry_for_trace(tool)

    assert entry["name"] == "plain_tool"
    assert "value" in entry["signature"]
    assert entry["annotations"] == {}
    assert entry["defaults"] == {}
    assert entry["safety_requirements"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_agent_trace_tool_surface.py -q
```

Expected: FAIL because `_tool_surface_entry_for_trace` and `_tool_safety_requirements_for_trace` do not exist yet.

- [ ] **Step 3: Implement trace helper functions**

In `lumibot/components/agents/manager.py`, add imports if missing:

```python
import inspect
```

Near `_stable_tool_metadata_for_cache`, add:

```python
def _json_safe_annotation(value: Any) -> str:
    if value is inspect.Signature.empty:
        return ""
    if isinstance(value, type):
        return value.__name__
    text = getattr(value, "__name__", None)
    if isinstance(text, str) and text:
        return text
    return str(value)


def _json_safe_default(value: Any) -> Any:
    if value is inspect.Signature.empty:
        return None
    normalized = _normalize_json(value)
    if isinstance(normalized, (str, int, float, bool)) or normalized is None:
        return normalized
    if isinstance(normalized, (list, dict)):
        return normalized
    return repr(value)


def _tool_safety_requirements_for_trace(tool_name: str) -> list[str]:
    if tool_name != "orders_submit_order":
        return []
    return [
        "Call account_portfolio in the same agent run before submitting an order.",
        "Call account_positions in the same agent run before submitting an order.",
        "Call market_last_price for the same symbol in the same agent run before submitting an order.",
    ]


def _tool_surface_entry_for_trace(tool: BoundTool) -> dict[str, Any]:
    function = tool.function
    try:
        signature = inspect.signature(function)
        signature_text = str(signature)
    except (TypeError, ValueError):
        signature = None
        signature_text = ""

    annotations: dict[str, str] = {}
    defaults: dict[str, Any] = {}
    if signature is not None:
        for name, parameter in signature.parameters.items():
            annotation = _json_safe_annotation(parameter.annotation)
            if annotation:
                annotations[name] = annotation
            if parameter.default is not inspect.Signature.empty:
                defaults[name] = _json_safe_default(parameter.default)
        return_annotation = _json_safe_annotation(signature.return_annotation)
        if return_annotation:
            annotations["return"] = return_annotation

    return {
        "name": tool.name,
        "description": tool.description,
        "source": tool.source,
        "signature": signature_text,
        "annotations": annotations,
        "defaults": defaults,
        "metadata": dict(tool.metadata or {}),
        "safety_requirements": _tool_safety_requirements_for_trace(tool.name),
    }
```

- [ ] **Step 4: Run helper tests**

Run:

```powershell
python -m pytest tests/test_agent_trace_tool_surface.py -q
```

Expected: PASS.

- [ ] **Step 5: Write failing tests for filtered tool availability**

Append to `tests/test_agent_trace_tool_surface.py`:

```python
from lumibot.components.agents.builtins import BuiltinTools


def test_filter_tools_returns_filtered_mutating_tools_with_reasons(fake_strategy):
    manager = fake_strategy.get_agent_manager()
    handle = manager.create_agent(
        "read_only_agent",
        system_prompt="Read only.",
        model="stub-model",
        allow_trading=False,
    )

    available_names = [getattr(tool, "name", "") for tool in handle._tool_inputs]
    filtered_names = [entry["name"] for entry in handle._filtered_tool_inputs]

    assert "orders_submit_order" not in available_names
    assert "orders_submit_order" in filtered_names
    submit_entry = next(entry for entry in handle._filtered_tool_inputs if entry["name"] == "orders_submit_order")
    assert submit_entry["reason"] == "filtered because allow_trading is false"
    assert submit_entry["metadata"]["mutates_trading"] is True
```

Use the existing `fake_strategy` fixture if available in the test suite. If it is not available in this file, import or define the same fixture pattern used in `tests/test_agent_tool_permissions.py`.

- [ ] **Step 6: Run filtered tool test to verify it fails**

Run:

```powershell
python -m pytest tests/test_agent_trace_tool_surface.py::test_filter_tools_returns_filtered_mutating_tools_with_reasons -q
```

Expected: FAIL because `AgentHandle` does not yet store `_filtered_tool_inputs`.

- [ ] **Step 7: Implement filtered tool recording**

In `AgentHandle.__init__`, initialize filtered tracking before filtering:

```python
self._filtered_tool_inputs: list[dict[str, Any]] = []
```

Replace calls to `_filter_tools_for_trading_permission(...)` with a helper that records filtered tools:

```python
builtin_tools = self._filter_tools_for_trading_permission(BuiltinTools.all(), record_filtered=True)
if tools is None:
    self._tool_inputs = builtin_tools
elif include_builtin_tools:
    self._tool_inputs = builtin_tools + self._filter_tools_for_trading_permission(list(tools), record_filtered=True)
else:
    self._tool_inputs = self._filter_tools_for_trading_permission(list(tools), record_filtered=True)
```

Update method signature and body:

```python
def _filter_tools_for_trading_permission(self, tools: list[Any], *, record_filtered: bool = False) -> list[Any]:
    if self.allow_trading:
        return list(tools)
    filtered: list[Any] = []
    for tool in tools:
        metadata = dict(getattr(tool, "metadata", {}) or {})
        if bool(metadata.get("mutates_trading")):
            if record_filtered:
                self._filtered_tool_inputs.append(
                    {
                        "name": getattr(tool, "name", ""),
                        "source": getattr(tool, "source", "local"),
                        "metadata": metadata,
                        "reason": "filtered because allow_trading is false",
                    }
                )
            continue
        filtered.append(tool)
    return filtered
```

- [ ] **Step 8: Add trace request payload helper**

In `AgentHandle`, add:

```python
def _trace_request_payload(self, cache_payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(cache_payload)
    bound_tools = self._ensure_bound_tools()
    available = [
        {
            "name": tool.name,
            "source": tool.source,
            "metadata": dict(tool.metadata or {}),
            "reason": "available",
        }
        for tool in bound_tools
    ]
    payload["tool_surface"] = [_tool_surface_entry_for_trace(tool) for tool in bound_tools]
    payload["tool_availability"] = {
        "available": available,
        "filtered": list(self._filtered_tool_inputs),
    }
    return payload
```

In trace construction, replace:

```python
"request": cache_payload,
```

with:

```python
"request": self._trace_request_payload(cache_payload),
```

Do not change the cache key computation or replay cache save payload.

- [ ] **Step 9: Run trace helper and existing permission tests**

Run:

```powershell
python -m pytest tests/test_agent_trace_tool_surface.py tests/test_agent_tool_permissions.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit Task 1**

Run:

```powershell
git add lumibot/components/agents/manager.py tests/test_agent_trace_tool_surface.py
git commit -m "feat: record richer agent tool trace surface"
```

---

## Task 2: Tool Result Diagnostics

**Files:**
- Modify: `lumibot/components/agents/manager.py`
- Modify: `tests/test_agent_trace_tool_surface.py`

- [ ] **Step 1: Write failing tests for diagnostics helper**

Append:

```python
from lumibot.components.agents.manager import _tool_result_diagnostics_for_trace


def test_order_readiness_error_diagnostics_extract_missing_requirements():
    payload = {
        "ok": False,
        "error": {
            "type": "ValueError",
            "message": (
                "ORDER_READINESS_REQUIRED: Before submitting an order, call "
                "account_portfolio, account_positions, market_last_price(symbol='TIP') "
                "in this same agent run."
            ),
        },
    }

    diagnostics = _tool_result_diagnostics_for_trace(payload)

    assert diagnostics["ok"] is False
    assert diagnostics["error_type"] == "ORDER_READINESS_REQUIRED"
    assert diagnostics["missing_requirements"] == [
        "account_portfolio",
        "account_positions",
        "market_last_price(symbol='TIP')",
    ]


def test_successful_tool_result_diagnostics_is_ok():
    assert _tool_result_diagnostics_for_trace({"ok": True}) == {"ok": True}
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m pytest tests/test_agent_trace_tool_surface.py::test_order_readiness_error_diagnostics_extract_missing_requirements tests/test_agent_trace_tool_surface.py::test_successful_tool_result_diagnostics_is_ok -q
```

Expected: FAIL because helper does not exist.

- [ ] **Step 3: Implement diagnostics helper**

In `manager.py`, add:

```python
def _error_payload_from_tool_result(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    return error if isinstance(error, dict) else None


def _extract_order_readiness_requirements(message: str) -> list[str]:
    marker = "call "
    end_marker = " in this same agent run"
    if marker not in message:
        return []
    tail = message.split(marker, 1)[1]
    if end_marker in tail:
        tail = tail.split(end_marker, 1)[0]
    return [part.strip().rstrip(".") for part in tail.split(", ") if part.strip()]


def _tool_result_diagnostics_for_trace(payload: Any) -> dict[str, Any]:
    error = _error_payload_from_tool_result(payload)
    if error is None:
        if isinstance(payload, dict) and payload.get("ok") is True:
            return {"ok": True}
        return {}

    message = str(error.get("message") or "")
    diagnostics: dict[str, Any] = {
        "ok": False,
        "error_type": str(error.get("type") or "tool_error"),
        "error_message": message,
    }
    if "ORDER_READINESS_REQUIRED" in message:
        diagnostics["error_type"] = "ORDER_READINESS_REQUIRED"
        diagnostics["missing_requirements"] = _extract_order_readiness_requirements(message)
    return diagnostics
```

- [ ] **Step 4: Store diagnostics on trace tool results and events**

In trace construction list comprehensions, add diagnostics to `tool_results` entries:

```python
"diagnostics": _tool_result_diagnostics_for_trace(event.payload),
```

For `events`, include diagnostics only for tool result events:

```python
"diagnostics": _tool_result_diagnostics_for_trace(event.payload) if event.kind == "tool_result" else {},
```

- [ ] **Step 5: Run diagnostics tests**

Run:

```powershell
python -m pytest tests/test_agent_trace_tool_surface.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

Run:

```powershell
git add lumibot/components/agents/manager.py tests/test_agent_trace_tool_surface.py
git commit -m "feat: record agent tool result diagnostics"
```

---

## Task 3: Replay Model and Loader Exposure

**Files:**
- Modify: `lumibot/components/agents/replay_ui/models.py`
- Modify: `lumibot/components/agents/replay_ui/loader.py`
- Modify: `tests/test_agent_replay_ui_models.py`
- Modify: `tests/test_agent_replay_ui_loader.py`

- [ ] **Step 1: Write failing model tests**

Add or update tests in `tests/test_agent_replay_ui_models.py`:

```python
from lumibot.components.agents.replay_ui.models import AgentReplay, ToolCallReplay


def test_input_material_exposes_tool_availability_and_rich_tool_fields():
    replay = AgentReplay(
        id="agent-1",
        name="trader",
        model="stub",
        trace_path="trace.json",
        request={
            "tool_surface": [
                {
                    "name": "orders_submit_order",
                    "description": "Submit.",
                    "signature": "(symbol: str)",
                    "annotations": {"symbol": "str"},
                    "defaults": {"quantity": 1},
                    "source": "local",
                    "metadata": {"kind": "builtin", "mutates_trading": True},
                    "safety_requirements": ["Call account_portfolio first."],
                }
            ],
            "tool_availability": {
                "available": [{"name": "account_positions", "reason": "available"}],
                "filtered": [{"name": "orders_submit_order", "reason": "filtered because allow_trading is false"}],
            },
        },
    )

    material = replay.input_material()

    tool = material["available_tools"][0]
    assert tool["defaults"] == {"quantity": 1}
    assert tool["safety_requirements"] == ["Call account_portfolio first."]
    assert material["tool_availability"]["filtered"][0]["name"] == "orders_submit_order"


def test_tool_call_replay_public_dict_exposes_diagnostics():
    call = ToolCallReplay(
        tool_name="orders_submit_order",
        diagnostics={"ok": False, "error_type": "ORDER_READINESS_REQUIRED"},
    )

    assert call.to_public_dict()["diagnostics"]["error_type"] == "ORDER_READINESS_REQUIRED"
```

- [ ] **Step 2: Run model tests to verify failure**

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_models.py -q
```

Expected: FAIL because `defaults`, `safety_requirements`, `tool_availability`, or `diagnostics` are not exposed.

- [ ] **Step 3: Extend replay models**

In `_public_tool_surface`, add:

```python
"defaults",
"safety_requirements",
```

Add a helper:

```python
def _public_tool_availability(tool_availability: Any) -> dict[str, Any]:
    if not isinstance(tool_availability, dict):
        return {"available": [], "filtered": []}
    available = tool_availability.get("available")
    filtered = tool_availability.get("filtered")
    return {
        "available": redact_sensitive(available) if isinstance(available, list) else [],
        "filtered": redact_sensitive(filtered) if isinstance(filtered, list) else [],
    }
```

In `ToolCallReplay`, add field:

```python
diagnostics: dict[str, Any] = field(default_factory=dict)
```

In `to_public_dict`, add:

```python
"diagnostics": redact_public_preview(self.diagnostics),
```

In `AgentReplay.input_material`, add:

```python
"tool_availability": _public_tool_availability(self.request.get("tool_availability")),
```

- [ ] **Step 4: Write failing loader tests**

In `tests/test_agent_replay_ui_loader.py`, add a test that builds a small trace dict/file with event diagnostics:

```python
def test_loader_preserves_tool_result_diagnostics(tmp_path):
    trace = {
        "agent": "trader",
        "model": "stub",
        "request": {},
        "events": [
            {"kind": "tool_call", "tool_name": "orders_submit_order", "payload": {"symbol": "TIP"}},
            {
                "kind": "tool_result",
                "tool_name": "orders_submit_order",
                "payload": {"ok": False},
                "diagnostics": {"ok": False, "error_type": "ORDER_READINESS_REQUIRED"},
            },
        ],
    }
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(json.dumps(trace), encoding="utf-8")

    replay = load_agent_trace(trace_path)

    call = replay.tool_batches[0].calls[0]
    assert call.diagnostics["error_type"] == "ORDER_READINESS_REQUIRED"
```

If the existing loader test file uses a different helper for writing traces, follow that local pattern and keep the same assertion.

- [ ] **Step 5: Extend loader**

In `_tool_batches_from_events`, when processing `kind == "tool_result"`, set:

```python
diagnostics = event.get("diagnostics")
call.diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
```

In `_tool_batches_from_flat_lists`, pass:

```python
diagnostics=result_item.get("diagnostics") if isinstance(result_item, dict) and isinstance(result_item.get("diagnostics"), dict) else {},
```

- [ ] **Step 6: Run replay model/loader tests**

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_loader.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

Run:

```powershell
git add lumibot/components/agents/replay_ui/models.py lumibot/components/agents/replay_ui/loader.py tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_loader.py
git commit -m "feat: expose richer agent replay trace data"
```

---

## Task 4: Replay UI Rendering

**Files:**
- Modify: `lumibot/components/agents/replay_ui/static/app.js`
- Modify: `lumibot/components/agents/replay_ui/static/styles.css`
- Modify: `tests/test_agent_replay_ui_browser.py`
- Modify: `tests/test_agent_replay_ui_static.py`

- [ ] **Step 1: Write failing browser/static tests**

Add browser fixture expectations that load a dataset containing:

- `input_material.tool_availability.available`
- `input_material.tool_availability.filtered`
- a selected tool with `defaults`
- a selected tool with `safety_requirements`
- a tool call with `diagnostics.error_type`

The test should assert visible text includes:

```text
Tool Availability
filtered because allow_trading is false
Recorded Defaults
Safety Requirements
ORDER_READINESS_REQUIRED
```

If `tests/test_agent_replay_ui_browser.py` already has a synthetic dataset helper, extend that helper rather than adding a second fixture style.

Add a static test in `tests/test_agent_replay_ui_static.py` to assert the JS source includes these render hooks:

```python
def test_static_ui_includes_trace_completeness_sections():
    app_js = Path("lumibot/components/agents/replay_ui/static/app.js").read_text(encoding="utf-8")

    assert "Tool Availability" in app_js
    assert "Recorded Defaults" in app_js
    assert "Safety Requirements" in app_js
    assert "Failure Diagnostics" in app_js
```

- [ ] **Step 2: Run UI tests to verify failure**

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py -q
```

Expected: FAIL until the UI renders the new sections.

- [ ] **Step 3: Render tool availability in Input Material**

In `app.js`, add:

```javascript
  function renderToolAvailability(input) {
    const availability = input && input.tool_availability ? input.tool_availability : {};
    const available = Array.isArray(availability.available) ? availability.available : [];
    const filtered = Array.isArray(availability.filtered) ? availability.filtered : [];
    if (available.length === 0 && filtered.length === 0) {
      return `<div class="empty-state">No tool availability details were recorded in this trace.</div>`;
    }
    return `
      <div class="tool-availability-grid">
        <div>
          <h5>Available</h5>
          ${renderToolAvailabilityList(available)}
        </div>
        <div>
          <h5>Filtered</h5>
          ${renderToolAvailabilityList(filtered)}
        </div>
      </div>
    `;
  }

  function renderToolAvailabilityList(items) {
    if (!items.length) {
      return `<div class="empty-state">None recorded.</div>`;
    }
    return `
      <ul class="tool-availability-list">
        ${items.map((item) => `
          <li>
            <strong>${escapeHtml(item.name || "unknown_tool")}</strong>
            ${item.reason ? `<span>${escapeHtml(item.reason)}</span>` : ""}
          </li>
        `).join("")}
      </ul>
    `;
  }
```

In `renderInputArea`, after Available Tool Names, add:

```javascript
      ${renderCollapsibleSubsection(
        "Tool Availability",
        "available and filtered tools",
        renderToolAvailability(input),
        false,
      )}
```

- [ ] **Step 4: Render defaults and safety requirements in Tool Definition panel**

In `renderToolDefinitionPanel`, add:

```javascript
    const defaults = definition.defaults === undefined ? "Not recorded in this trace." : definition.defaults;
    const safetyRequirements = Array.isArray(definition.safety_requirements)
      ? definition.safety_requirements
      : "Not recorded in this trace.";
```

Add sections after annotations:

```javascript
        <h5>Recorded Defaults</h5>
        <pre class="tool-definition-pre">${formatValue(defaults)}</pre>
        <h5>Safety Requirements</h5>
        <pre class="tool-definition-pre">${formatValue(safetyRequirements)}</pre>
```

- [ ] **Step 5: Render diagnostics in Tool Calls**

Add:

```javascript
  function renderToolDiagnostics(call) {
    const diagnostics = call && call.diagnostics ? call.diagnostics : null;
    if (!diagnostics || Object.keys(diagnostics).length === 0 || diagnostics.ok === true) {
      return "";
    }
    return `
      <div class="tool-diagnostics">
        <strong>Failure Diagnostics</strong>
        <pre>${formatValue(diagnostics)}</pre>
      </div>
    `;
  }
```

In `renderToolRow`, change the human explanation cell:

```javascript
        <td>
          ${renderToolDiagnostics(call)}
          <pre>${formatValue(call.human_explanation)}</pre>
        </td>
```

- [ ] **Step 6: Add compact styles**

In `styles.css`, add:

```css
.tool-availability-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.tool-availability-list {
  margin: 0;
  padding-left: 18px;
}

.tool-availability-list li {
  margin-bottom: 6px;
}

.tool-availability-list span {
  display: block;
  color: #4b5563;
  font-size: 12px;
}

.tool-diagnostics {
  border: 1px solid #d97706;
  background: #fff7ed;
  padding: 8px;
  margin-bottom: 8px;
}
```

- [ ] **Step 7: Run UI tests**

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 4**

Run:

```powershell
git add lumibot/components/agents/replay_ui/static/app.js lumibot/components/agents/replay_ui/static/styles.css tests/test_agent_replay_ui_browser.py tests/test_agent_replay_ui_static.py
git commit -m "feat: show trace completeness details in replay UI"
```

---

## Task 5: Final Integration Verification

**Files:**
- No required code changes unless verification reveals issues.

- [ ] **Step 1: Run focused test suite**

Run:

```powershell
python -m pytest tests/test_agent_trace_tool_surface.py tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_loader.py tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py -q
```

Expected: PASS.

- [ ] **Step 2: Run ruff on touched files**

Run:

```powershell
python -m ruff check lumibot/components/agents/manager.py lumibot/components/agents/replay_ui tests/test_agent_trace_tool_surface.py tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_loader.py tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py
```

Expected: PASS.

- [ ] **Step 3: Generate or inspect one trace**

If a fast synthetic trace test exists, use it. Otherwise run a minimal existing agent replay test that writes a trace. Then inspect the JSON and confirm it contains:

```json
"signature"
"defaults"
"tool_availability"
"diagnostics"
```

- [ ] **Step 4: Launch local UI smoke check**

Run:

```powershell
python scripts\agent_trace_ui.py
```

Expected:

- server starts on a local URL
- existing traces still load
- selecting an agent still shows Input Material, Tool Calls, and Final Summary
- Tool Definition panel shows the new sections when the selected trace includes them

Stop the server after the smoke check.

- [ ] **Step 5: Commit verification fixes if needed**

If verification required code changes:

```powershell
git add <changed-files>
git commit -m "fix: polish agent replay trace completeness"
```

If no changes were required, do not create an empty commit.

