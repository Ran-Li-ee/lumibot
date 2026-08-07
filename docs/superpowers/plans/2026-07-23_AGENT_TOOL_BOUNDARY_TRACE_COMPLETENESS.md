# Agent-Tool Boundary Trace Completeness Implementation Plan

Implementation plan for capturing every semantic transition in the model-to-local-tool round trip.

**Last Updated:** 2026-07-23
**Status:** Approved design, ready for implementation
**Audience:** Lumibot agent-runtime developers, replay-UI developers, and test authors

## Overview

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record every semantically meaningful transition between LiteLLM, Google ADK, FunctionTool, the Lumibot tool wrapper, and local Python tools in a versioned, redacted, call-ID-correlated boundary trace.

**Architecture:** Add a per-agent-run `BoundaryTraceCollector` that owns identifiers, immutable boundary events, payload snapshots, redaction, sidecars, and diagnostics. Instrument ADK through composed callbacks and an observed FunctionTool adapter, instrument the Lumibot wrapper around the original callable, and instrument LiteLLM with request-scoped callbacks rather than process-global hooks. Preserve the existing normalized trace and replay cache behavior while adding a backward-compatible top-level `boundary_trace`.

**Tech Stack:** Python 3.10+, dataclasses, `contextvars`, `threading.Lock`, gzip/JSON sidecars, Google ADK 2.x callbacks and FunctionTool, LiteLLM 1.83.x `CustomLogger`, pytest, Ruff.

---

## Scope and Execution Notes

The approved design is:

`docs/superpowers/specs/2026-07-23_AGENT_TOOL_BOUNDARY_TRACE_COMPLETENESS_DESIGN.md`

This plan implements only trace capture and persistence. It does not implement
the later UI presentation.

Run all commands from the repository root. On Windows, use:

```powershell
.\.venv\Scripts\python.exe -m pytest ...
.\.venv\Scripts\python.exe -m ruff check ...
```

Do not use a real provider key until the final manual acceptance task. All
earlier tests must be credential-free.

## File Structure

### New Files

- `lumibot/components/agents/trace_redaction.py`
  - Shared recursive secret redaction used before trace persistence and by the
    replay UI.
- `lumibot/components/agents/boundary_trace.py`
  - Boundary event schema, identifier allocation, context-local call
    correlation, payload snapshots, sidecars, diagnostics, and export.
- `lumibot/components/agents/litellm_trace.py`
  - Request-scoped LiteLLM `CustomLogger` for B10 provider-facing requests and
    B01 provider responses/failures.
- `tests/test_agent_boundary_trace.py`
  - Collector, redaction, sidecar, identifier, and failure-isolation tests.
- `tests/test_agent_runtime_boundary_trace.py`
  - Wrapper, FunctionTool, ADK callback, parallel-call, and multi-turn tests.
- `tests/test_agent_litellm_trace.py`
  - LiteLLM callback compatibility and provider-boundary tests.

### Modified Files

- `lumibot/components/agents/replay_ui/redaction.py`
  - Re-export shared redaction functions so existing UI imports remain valid.
- `lumibot/components/agents/schemas.py`
  - Add call IDs and boundary-trace payload to runtime result structures.
- `lumibot/components/agents/runtime.py`
  - Wire collector into RuntimeRequest, wrapper, FunctionTool, ADK callbacks,
    event normalization, and model resolution.
- `lumibot/components/agents/manager.py`
  - Create the per-run collector, persist boundary traces, preserve partial
    traces on handled failures, and save cache references.
- `tests/test_agent_replay_ui_models.py`
  - Prove shared redaction remains backward compatible.
- `tests/test_agent_replay_ui_loader.py`
  - Prove legacy and boundary-enabled traces both load.
- `tests/backtest/test_agent_runtime_backtest.py`
  - Prove persistence, replay-cache labeling, and unchanged backtest behavior.
- `CHANGELOG.md`
  - Document the added trace capability under the current development version.

---

### Task 1: Move Secret Redaction to a Shared Agent Module

**Files:**
- Create: `lumibot/components/agents/trace_redaction.py`
- Modify: `lumibot/components/agents/replay_ui/redaction.py`
- Modify: `tests/test_agent_replay_ui_models.py`
- Test: `tests/test_agent_boundary_trace.py`

- [ ] **Step 1: Write failing import and persistence-redaction tests**

Create `tests/test_agent_boundary_trace.py` with:

```python
from lumibot.components.agents.trace_redaction import redact_sensitive


def test_trace_redaction_masks_nested_credentials_before_persistence():
    token = "sk-" + ("x" * 24)
    payload = {
        "authorization": "Bearer " + ("y" * 24),
        "nested": {
            "OPENAI_API_KEY": "test-only-secret-value",
            "safe": "QQQ",
        },
        "free_text": f"provider token: {token}",
    }

    redacted = redact_sensitive(payload)

    assert redacted["authorization"] != payload["authorization"]
    assert "y" * 24 not in str(redacted)
    assert "test-only-secret-value" not in str(redacted)
    assert token not in str(redacted)
    assert redacted["nested"]["safe"] == "QQQ"
```

Append to `tests/test_agent_replay_ui_models.py`:

```python
def test_replay_redaction_reexports_shared_implementation():
    from lumibot.components.agents.replay_ui.redaction import redact_sensitive as replay_redact
    from lumibot.components.agents.trace_redaction import redact_sensitive as shared_redact

    assert replay_redact is shared_redact
```

- [ ] **Step 2: Run the tests and verify the new import fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_boundary_trace.py::test_trace_redaction_masks_nested_credentials_before_persistence `
  tests/test_agent_replay_ui_models.py::test_replay_redaction_reexports_shared_implementation -v
```

Expected: FAIL because `lumibot.components.agents.trace_redaction` does not
exist.

- [ ] **Step 3: Move the implementation and leave a compatibility re-export**

Move the existing file:

```powershell
git mv lumibot/components/agents/replay_ui/redaction.py lumibot/components/agents/trace_redaction.py
```

Create `lumibot/components/agents/replay_ui/redaction.py`:

```python
from ..trace_redaction import redact_public_preview, redact_sensitive

__all__ = ["redact_public_preview", "redact_sensitive"]
```

Do not change the regexes or behavior in the moved implementation during this
step.

- [ ] **Step 4: Run shared and existing redaction tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_boundary_trace.py `
  tests/test_agent_replay_ui_models.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add lumibot/components/agents/trace_redaction.py `
  lumibot/components/agents/replay_ui/redaction.py `
  tests/test_agent_boundary_trace.py `
  tests/test_agent_replay_ui_models.py
git commit -m "refactor: share agent trace redaction"
```

---

### Task 2: Add the Boundary Event Schema and Collector

**Files:**
- Create: `lumibot/components/agents/boundary_trace.py`
- Modify: `tests/test_agent_boundary_trace.py`

- [ ] **Step 1: Write failing schema, sequence, and identifier tests**

Append:

```python
from pathlib import Path

from lumibot.components.agents.boundary_trace import (
    BOUNDARY_SCHEMA_VERSION,
    BoundaryTraceCollector,
)


def test_boundary_collector_allocates_stable_turn_batch_and_event_ids(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
        inline_payload_limit=10_000,
    )

    turn_id = collector.start_model_turn()
    batch_id = collector.register_tool_batch(turn_id, ["call_A", "call_B"])
    first = collector.record(
        transition="B03_ADK_TO_FUNCTION_TOOL",
        from_module="google_adk",
        to_module="function_tool",
        model_turn_id=turn_id,
        tool_batch_id=batch_id,
        call_id="call_A",
        payload={"symbol": "QQQ"},
    )
    second = collector.record(
        transition="B03_ADK_TO_FUNCTION_TOOL",
        from_module="google_adk",
        to_module="function_tool",
        model_turn_id=turn_id,
        tool_batch_id=batch_id,
        call_id="call_B",
        payload={"symbol": "SPY"},
    )

    exported = collector.export()

    assert BOUNDARY_SCHEMA_VERSION == 1
    assert turn_id == "run-1:turn:0001"
    assert batch_id == "run-1:turn:0001:batch:0001"
    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert first["span_id"] != second["span_id"]
    assert exported["agent_run_id"] == "run-1"
    assert exported["events"] == [first, second]
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_boundary_trace.py::test_boundary_collector_allocates_stable_turn_batch_and_event_ids -v
```

Expected: FAIL because `boundary_trace.py` does not exist.

- [ ] **Step 3: Implement the minimal event collector**

Create `lumibot/components/agents/boundary_trace.py` with these public
interfaces:

```python
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import itertools
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .trace_redaction import redact_sensitive

BOUNDARY_SCHEMA_VERSION = 1
DEFAULT_INLINE_PAYLOAD_LIMIT = 64_000

_active_tool_call: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "lumibot_boundary_tool_call",
    default=None,
)


def utc_iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def current_tool_call_context() -> dict[str, Any] | None:
    value = _active_tool_call.get()
    return dict(value) if isinstance(value, dict) else None


class BoundaryTraceCollector:
    def __init__(
        self,
        *,
        agent_run_id: str,
        artifact_root: Path | None,
        inline_payload_limit: int = DEFAULT_INLINE_PAYLOAD_LIMIT,
    ) -> None:
        self.agent_run_id = agent_run_id
        self.artifact_root = Path(artifact_root) if artifact_root is not None else None
        self.inline_payload_limit = max(int(inline_payload_limit), 1)
        self._events: list[dict[str, Any]] = []
        self._diagnostics: list[dict[str, Any]] = []
        self._sequence = itertools.count(1)
        self._turn_number = 0
        self._batch_number_by_turn: dict[str, int] = {}
        self._call_index: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start_model_turn(self) -> str:
        with self._lock:
            self._turn_number += 1
            return f"{self.agent_run_id}:turn:{self._turn_number:04d}"

    def register_tool_batch(self, model_turn_id: str, call_ids: list[str]) -> str:
        with self._lock:
            number = self._batch_number_by_turn.get(model_turn_id, 0) + 1
            self._batch_number_by_turn[model_turn_id] = number
            batch_id = f"{model_turn_id}:batch:{number:04d}"
            for index, call_id in enumerate(call_ids, start=1):
                self._call_index[call_id] = {
                    "model_turn_id": model_turn_id,
                    "tool_batch_id": batch_id,
                    "call_sequence": index,
                }
            return batch_id

    def call_ids(self, call_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._call_index.get(call_id) or {})

    @contextlib.contextmanager
    def tool_call_context(self, **context: Any) -> Iterator[None]:
        token = _active_tool_call.set(dict(context))
        try:
            yield
        finally:
            _active_tool_call.reset(token)

    def record(
        self,
        *,
        transition: str,
        from_module: str,
        to_module: str,
        payload: Any,
        status: str = "success",
        model_turn_id: str | None = None,
        tool_batch_id: str | None = None,
        call_id: str | None = None,
        parent_span_id: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        duration_ms: float | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            safe_payload, payload_meta = self.snapshot(payload)
            with self._lock:
                event = {
                    "schema_version": BOUNDARY_SCHEMA_VERSION,
                    "sequence": next(self._sequence),
                    "transition": transition,
                    "from_module": from_module,
                    "to_module": to_module,
                    "status": status,
                    "agent_run_id": self.agent_run_id,
                    "model_turn_id": model_turn_id,
                    "tool_batch_id": tool_batch_id,
                    "call_id": call_id,
                    "span_id": uuid4().hex,
                    "parent_span_id": parent_span_id,
                    "started_at": started_at,
                    "ended_at": ended_at or utc_iso_timestamp(),
                    "duration_ms": duration_ms,
                    "payload": safe_payload,
                    "payload_meta": payload_meta,
                    "error": redact_sensitive(error),
                }
                self._events.append(event)
                return event
        except Exception as exc:
            self.add_diagnostic("record_failed", exc)
            return {}

    def snapshot(self, payload: Any) -> tuple[Any, dict[str, Any]]:
        redacted = redact_sensitive(payload)
        encoded = json.dumps(redacted, sort_keys=True, default=str).encode("utf-8")
        return redacted, {
            "fidelity": "normalized_copy",
            "representation": "json",
            "byte_count": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "redacted": redacted != payload,
            "pruned": False,
            "truncated": False,
            "sidecar_path": None,
        }

    def add_diagnostic(self, kind: str, exc: BaseException | str) -> None:
        message = str(exc)
        with self._lock:
            self._diagnostics.append(
                {
                    "kind": kind,
                    "message": redact_sensitive(message),
                    "timestamp": utc_iso_timestamp(),
                }
            )

    def export(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": BOUNDARY_SCHEMA_VERSION,
                "agent_run_id": self.agent_run_id,
                "capture_scope": "semantic_boundaries",
                "provider_wire_capture": False,
                "events": list(self._events),
                "diagnostics": list(self._diagnostics),
            }
```

This is the starting implementation. Sidecars and descriptor-only payloads are
added in the next task.

- [ ] **Step 4: Run the collector test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_boundary_trace.py::test_boundary_collector_allocates_stable_turn_batch_and_event_ids -v
```

Expected: PASS.

- [ ] **Step 5: Add a context isolation test**

Append:

```python
def test_tool_call_context_restores_previous_value(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    assert collector.current_tool_call() is None
    with collector.tool_call_context(call_id="call_A", tool_name="market_last_price"):
        assert collector.current_tool_call()["call_id"] == "call_A"
    assert collector.current_tool_call() is None
```

Add this method to the collector:

```python
    def current_tool_call(self) -> dict[str, Any] | None:
        return current_tool_call_context()
```

- [ ] **Step 6: Run the complete collector test file**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_boundary_trace.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add lumibot/components/agents/boundary_trace.py tests/test_agent_boundary_trace.py
git commit -m "feat: add agent boundary trace collector"
```

---

### Task 3: Add Safe Raw Descriptors and Compressed Sidecars

**Files:**
- Modify: `lumibot/components/agents/boundary_trace.py`
- Modify: `tests/test_agent_boundary_trace.py`

- [ ] **Step 1: Write failing sidecar, generator, and failure-isolation tests**

Append:

```python
import gzip
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel


class ExampleTraceModel(BaseModel):
    symbol: str
    score: Decimal


def test_large_payload_is_redacted_then_written_to_relative_sidecar(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
        inline_payload_limit=32,
    )

    event = collector.record(
        transition="B07_WRAPPER_TO_FUNCTION_TOOL",
        from_module="lumibot_tool_wrapper",
        to_module="function_tool",
        payload={"OPENAI_API_KEY": "test-only-secret-value", "rows": ["x" * 100]},
    )

    relative = event["payload_meta"]["sidecar_path"]
    assert relative and not Path(relative).is_absolute()
    with gzip.open(tmp_path / relative, "rt", encoding="utf-8") as handle:
        persisted = json.load(handle)
    assert "test-only-secret-value" not in str(persisted)
    assert persisted["rows"] == ["x" * 100]
    assert event["payload_meta"]["truncated"] is False
    persisted_bytes = json.dumps(
        persisted,
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    assert event["payload_meta"]["sha256"] == hashlib.sha256(
        persisted_bytes
    ).hexdigest()


def test_raw_generator_uses_descriptor_without_consuming_it(tmp_path):
    consumed = []

    def values():
        consumed.append("started")
        yield 1

    generator = values()
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    descriptor = collector.describe_raw_value(generator)

    assert consumed == []
    assert descriptor["fidelity"] == "descriptor_only"
    assert descriptor["python_type"] == "generator"


def test_raw_semantic_values_preserve_supported_types(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    values = {
        "scalar": 7,
        "mapping": {"symbol": "QQQ"},
        "sequence": ["QQQ", "SPY"],
        "datetime": datetime(2024, 9, 5, tzinfo=timezone.utc),
        "decimal": Decimal("1.25"),
        "model": ExampleTraceModel(symbol="QQQ", score=Decimal("9.5")),
    }

    descriptor = collector.describe_raw_value(values)

    assert descriptor["fidelity"] == "semantic_copy"
    assert descriptor["semantic_value"] == {
        "scalar": 7,
        "mapping": {"symbol": "QQQ"},
        "sequence": ["QQQ", "SPY"],
        "datetime": "2024-09-05T00:00:00+00:00",
        "decimal": "1.25",
        "model": {"symbol": "QQQ", "score": "9.5"},
    }


def test_sidecar_failure_adds_diagnostic_without_raising(monkeypatch, tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
        inline_payload_limit=1,
    )
    monkeypatch.setattr(collector, "_write_sidecar", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))

    event = collector.record(
        transition="B07_WRAPPER_TO_FUNCTION_TOOL",
        from_module="lumibot_tool_wrapper",
        to_module="function_tool",
        payload={"value": "large"},
    )

    assert event["payload_meta"]["truncated"] is True
    assert collector.export()["diagnostics"][0]["kind"] == "sidecar_write_failed"


def test_redaction_failure_records_no_payload_and_does_not_raise(monkeypatch, tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    def fail_redaction(_value):
        raise ValueError("redaction unavailable")

    monkeypatch.setattr(
        "lumibot.components.agents.boundary_trace.redact_sensitive",
        fail_redaction,
    )

    event = collector.record(
        transition="B07_WRAPPER_TO_FUNCTION_TOOL",
        from_module="lumibot_tool_wrapper",
        to_module="function_tool",
        payload={"authorization": "must-not-persist"},
    )

    exported = collector.export()
    assert event == {}
    assert exported["events"] == []
    assert exported["diagnostics"][0]["kind"] == "record_failed"
    assert "must-not-persist" not in str(exported)
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_boundary_trace.py -v
```

Expected: FAIL because sidecars and raw descriptors are not implemented.

- [ ] **Step 3: Implement descriptor-only inspection**

Add imports:

```python
import collections.abc
import gzip
import math
import os
import tempfile
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID
```

Add:

```python
_DESCRIPTOR_ONLY = object()


def _semantic_trace_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, Enum):
        return _semantic_trace_value(value.value)
    if isinstance(value, dict):
        return {
            str(key): _semantic_trace_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_semantic_trace_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [
            _semantic_trace_value(item)
            for item in sorted(value, key=repr)
        ]
    if isinstance(value, collections.abc.Iterator):
        return _DESCRIPTOR_ONLY
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _semantic_trace_value(model_dump(mode="json"))
    return _DESCRIPTOR_ONLY


    def describe_raw_value(self, value: Any) -> dict[str, Any]:
        value_type = type(value)
        descriptor: dict[str, Any] = {
            "python_type": value_type.__name__,
            "qualified_type": f"{value_type.__module__}.{value_type.__qualname__}",
        }
        semantic_value = _semantic_trace_value(value)
        if semantic_value is not _DESCRIPTOR_ONLY:
            descriptor["fidelity"] = "semantic_copy"
            descriptor["semantic_value"] = semantic_value
            return descriptor
        descriptor["fidelity"] = "descriptor_only"
        if isinstance(value, dict):
            descriptor["keys"] = [str(key) for key in list(value.keys())[:100]]
            descriptor["length"] = len(value)
        elif isinstance(value, (list, tuple, set, frozenset)):
            descriptor["length"] = len(value)
        elif isinstance(value, collections.abc.Iterator):
            descriptor["one_shot"] = True
        shape = getattr(value, "shape", None)
        if isinstance(shape, tuple):
            descriptor["shape"] = list(shape)
        descriptor["preview"] = redact_sensitive(repr(value)[:500])
        return descriptor
```

Do not iterate over an `Iterator` to create the descriptor.

- [ ] **Step 4: Make diagnostics survive redaction failure**

Replace `add_diagnostic()` with:

```python
    def add_diagnostic(self, kind: str, exc: BaseException | str) -> None:
        try:
            safe_message = redact_sensitive(str(exc))
        except Exception:
            safe_message = "[diagnostic message unavailable: redaction failed]"
        with self._lock:
            self._diagnostics.append(
                {
                    "kind": kind,
                    "message": safe_message,
                    "timestamp": utc_iso_timestamp(),
                }
            )
```

This fixed fallback contains no exception or payload text, so a broken
redactor cannot leak the value it failed to sanitize.

- [ ] **Step 5: Implement atomic gzip sidecars and preview fallback**

Add:

```python
    def _write_sidecar(self, *, span_id: str, payload: Any) -> str:
        if self.artifact_root is None:
            raise OSError("artifact root is unavailable")
        relative = Path("boundary_payloads") / self.agent_run_id / f"{span_id}.json.gz"
        target = self.artifact_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{span_id}-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb") as compressed:
                    compressed.write(json.dumps(payload, sort_keys=True, default=str).encode("utf-8"))
            os.replace(temp_name, target)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(temp_name)
            raise
        return relative.as_posix()
```

Refactor `record()` to allocate `span_id` before calling `snapshot()`, and
change `snapshot()` to:

```python
    def snapshot(self, payload: Any, *, span_id: str) -> tuple[Any, dict[str, Any]]:
        normalized = _semantic_trace_value(payload)
        if normalized is _DESCRIPTOR_ONLY:
            normalized = self.describe_raw_value(payload)
        redacted = redact_sensitive(normalized)
        encoded = json.dumps(redacted, sort_keys=True, default=str).encode("utf-8")
        meta = {
            "fidelity": "normalized_copy",
            "representation": "json",
            "byte_count": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "redacted": redacted != normalized,
            "pruned": False,
            "truncated": False,
            "sidecar_path": None,
        }
        if len(encoded) <= self.inline_payload_limit:
            return redacted, meta
        try:
            meta["sidecar_path"] = self._write_sidecar(span_id=span_id, payload=redacted)
            return {"preview": encoded[:500].decode("utf-8", errors="replace")}, meta
        except Exception as exc:
            self.add_diagnostic("sidecar_write_failed", exc)
            meta["truncated"] = True
            return {"preview": encoded[:500].decode("utf-8", errors="replace")}, meta
```

- [ ] **Step 6: Run sidecar and descriptor tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_boundary_trace.py -v
```

Expected: PASS.

- [ ] **Step 7: Run Ruff**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check `
  lumibot/components/agents/boundary_trace.py `
  tests/test_agent_boundary_trace.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add lumibot/components/agents/boundary_trace.py tests/test_agent_boundary_trace.py
git commit -m "feat: persist large boundary trace payloads"
```

---

### Task 4: Extend Runtime Schemas and Trace Persistence

**Files:**
- Modify: `lumibot/components/agents/schemas.py`
- Modify: `lumibot/components/agents/runtime.py`
- Modify: `lumibot/components/agents/manager.py`
- Modify: `tests/backtest/test_agent_runtime_backtest.py`

- [ ] **Step 1: Write a failing trace-persistence test**

Append to `tests/backtest/test_agent_runtime_backtest.py`:

```python
class BoundaryResultRuntime:
    def run(self, request):
        collector = request.boundary_collector
        collector.record(
            transition="B09_ADK_TO_LITELLM",
            from_module="google_adk",
            to_module="litellm",
            payload={"contents": [{"role": "user", "text": "test"}]},
        )
        return AgentRunResult(
            summary="RESULT: done",
            model=request.model,
            events=[_event("text", text="RESULT: done")],
            boundary_trace=collector.export(),
        )


def test_agent_trace_persists_boundary_trace_without_changing_legacy_events(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path))
    strategy = PromptCaptureStrategy()
    handle = strategy.agents.create(
        name="trace_agent",
        system_prompt="test",
        runtime=BoundaryResultRuntime(),
    )
    handle.run(task_prompt="test")

    trace_path = next((tmp_path / "agent_runtime" / "traces" / "trace_agent").glob("*.json"))
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert payload["events"][0]["kind"] == "text"
    assert payload["boundary_trace"]["schema_version"] == 1
    assert payload["boundary_trace"]["agent_run_id"]
    assert list(trace_path.parent.glob("*.tmp")) == []
```

`AgentRunResult` is already imported in this test file.

- [ ] **Step 2: Run the test and verify the schema field is missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/backtest/test_agent_runtime_backtest.py::test_agent_trace_persists_boundary_trace_without_changing_legacy_events -v
```

Expected: FAIL because `AgentRunResult` has no `boundary_trace`.

- [ ] **Step 3: Extend dataclasses**

In `lumibot/components/agents/schemas.py`, add:

```python
@dataclass
class AgentTraceEvent:
    kind: str
    text: str | None = None
    tool_name: str | None = None
    payload: dict[str, Any] | None = None
    timestamp: str | None = None
    call_id: str | None = None
    event_id: str | None = None
    invocation_id: str | None = None


# Add this after first_event_latency_ms in the existing AgentRunResult:
    boundary_trace: dict[str, Any] | None = None
```

Do not redeclare `AgentRunResult`. Add `boundary_trace` after the existing
optional timing fields so existing positional construction remains compatible.

In `RuntimeRequest`, add:

```python
    agent_run_id: str | None = None
    boundary_collector: BoundaryTraceCollector | None = None
```

- [ ] **Step 4: Create one collector per uncached agent run**

Import:

```python
from uuid import uuid4

from .boundary_trace import BoundaryTraceCollector
```

Immediately after the replay-cache early return and before constructing
`RuntimeRequest`, create the run identity and collector:

```python
        agent_run_id = uuid4().hex
        boundary_collector = BoundaryTraceCollector(
            agent_run_id=agent_run_id,
            artifact_root=self._runtime_artifact_dir(),
        )
```

Pass both into `runtime_request_class(...)`:

```python
            agent_run_id=agent_run_id,
            boundary_collector=boundary_collector,
```

Do not create a collector on the cache-hit path; Task 11 labels cached execution
without pretending a new provider/tool exchange occurred.

- [ ] **Step 5: Add boundary trace to manager persistence**

Add to the trace payload in `AgentHandle.run()`:

```python
            "boundary_trace": result.boundary_trace,
```

Preserve the new normalized event fields:

```python
                    "call_id": event.call_id,
                    "event_id": event.event_id,
                    "invocation_id": event.invocation_id,
```

Add the same normalized event fields in `_result_from_cached()`.

Make `_write_trace()` atomic using a temporary file in the destination
directory:

```python
    def _write_trace(self, result: AgentRunResult, trace_payload: dict[str, Any]) -> Path:
        trace_path = self._trace_dir() / (
            f"{result.cache_key or 'live'}-"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}.json"
        )
        normalized = json.dumps(
            _normalize_json(trace_payload),
            indent=2,
            sort_keys=True,
        )
        temp_path = trace_path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            temp_path.write_text(normalized, encoding="utf-8")
            os.replace(temp_path, trace_path)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
        return trace_path
```

The `uuid4` import added in Step 4 serves both the run ID and temporary file.

- [ ] **Step 6: Save a lightweight cache reference**

Do not duplicate the full boundary trace or sidecars inside replay-cache gzip.
After `_write_trace()` returns, save:

```python
                    "boundary_trace_ref": {
                        "status": "available_original_trace",
                        "trace_path": trace_path.as_posix(),
                        "agent_run_id": (result.boundary_trace or {}).get("agent_run_id"),
                    },
```

When loading a legacy cache entry, set:

```python
result.boundary_trace = {
    "schema_version": 1,
    "status": "unavailable_legacy_cache",
    "execution_source": "replay_cache",
    "events": [],
    "diagnostics": [],
}
```

When a cache reference exists, set:

```python
result.boundary_trace = {
    **cached["boundary_trace_ref"],
    "schema_version": 1,
    "execution_source": "replay_cache",
    "events": [],
    "diagnostics": [],
}
```

- [ ] **Step 7: Run persistence and replay-cache tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/backtest/test_agent_runtime_backtest.py::test_agent_trace_persists_boundary_trace_without_changing_legacy_events `
  tests/backtest/test_agent_runtime_backtest.py::test_agent_runtime_stock_backtest_replays_from_cache -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add lumibot/components/agents/schemas.py `
  lumibot/components/agents/runtime.py `
  lumibot/components/agents/manager.py `
  tests/backtest/test_agent_runtime_backtest.py
git commit -m "feat: persist agent boundary traces"
```

---

### Task 5: Capture Wrapper-to-Python and Python-to-Wrapper Boundaries

**Files:**
- Modify: `lumibot/components/agents/runtime.py`
- Modify: `tests/test_agent_runtime_boundary_trace.py`
- Modify: `tests/backtest/test_agent_runtime_backtest.py`

- [ ] **Step 1: Write failing success, default-argument, and exception tests**

Create `tests/test_agent_runtime_boundary_trace.py`:

```python
from datetime import datetime, timezone

from lumibot.components.agents.boundary_trace import BoundaryTraceCollector
from lumibot.components.agents.runtime import RuntimeRequest, _wrap_tool_callable
from lumibot.components.agents.schemas import BoundTool


def _events(collector, transition):
    return [
        event
        for event in collector.export()["events"]
        if event["transition"] == transition
    ]


def make_runtime_request(collector, *, tools=None, model="openai/test"):
    return RuntimeRequest(
        agent_name="agent",
        model=model,
        system_prompt="system",
        task_prompt="task",
        context={},
        runtime_context={"mode": "backtesting"},
        memory_state={},
        memory_notes=[],
        bound_tools=list(tools or []),
        agent_run_id=collector.agent_run_id,
        boundary_collector=collector,
    )


def test_wrapper_records_raw_and_serialized_results_separately(tmp_path):
    def price(symbol: str, asset_type: str = "stock"):
        return {
            "symbol": symbol,
            "asset_type": asset_type,
            "as_of": datetime(2024, 9, 5, tzinfo=timezone.utc),
        }

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    tool = BoundTool(name="market_last_price", description="price", function=price)
    wrapped = _wrap_tool_callable(tool, {}, collector=collector)

    with collector.tool_call_context(
        call_id="call_A",
        model_turn_id="run-1:turn:0001",
        tool_batch_id="run-1:turn:0001:batch:0001",
    ):
        result = wrapped(symbol="QQQ")

    assert result["as_of"] == "2024-09-05T00:00:00+00:00"
    b05 = _events(collector, "B05_WRAPPER_TO_PYTHON_TOOL")[0]
    b06 = _events(collector, "B06_PYTHON_TOOL_TO_WRAPPER")[0]
    b07 = _events(collector, "B07_WRAPPER_TO_FUNCTION_TOOL")[0]
    assert b05["payload"]["effective_arguments"]["asset_type"] == "stock"
    assert b06["payload"]["raw_result"]["python_type"] == "dict"
    assert b06["payload"]["raw_result"]["fidelity"] == "semantic_copy"
    assert b06["payload"]["raw_result"]["semantic_value"]["symbol"] == "QQQ"
    assert b07["payload"]["serialized_result"]["as_of"].startswith("2024-09-05")


def test_wrapper_records_original_exception_and_returns_existing_error_payload(tmp_path):
    def broken(symbol: str):
        raise ValueError(f"bad symbol {symbol}")

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    wrapped = _wrap_tool_callable(
        BoundTool(name="broken", description="broken", function=broken),
        {},
        collector=collector,
    )

    with collector.tool_call_context(call_id="call_A"):
        result = wrapped(symbol="BAD")

    assert result["tool_error"] is True
    failure = _events(collector, "B06_PYTHON_TOOL_TO_WRAPPER")[0]
    assert failure["status"] == "error"
    assert failure["error"]["type"] == "ValueError"
    assert _events(collector, "B07_WRAPPER_TO_FUNCTION_TOOL")[0]["payload"]["serialized_result"] == result


def test_recorder_failure_does_not_change_successful_tool_result(monkeypatch, tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    wrapped = _wrap_tool_callable(
        BoundTool(
            name="price",
            description="price",
            function=lambda symbol: {"symbol": symbol, "price": 100.0},
        ),
        {},
        collector=collector,
    )
    monkeypatch.setattr(
        collector,
        "snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("snapshot failed")
        ),
    )

    with collector.tool_call_context(call_id="call_A"):
        result = wrapped(symbol="QQQ")

    assert result == {"symbol": "QQQ", "price": 100.0}
    assert collector.export()["events"] == []
    assert collector.export()["diagnostics"]
```

- [ ] **Step 2: Run the tests and verify `_wrap_tool_callable` rejects the collector**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_boundary_trace.py -v
```

Expected: FAIL because `_wrap_tool_callable` has no `collector` argument.

- [ ] **Step 3: Refactor the wrapper without changing its return contract**

Change the signature:

```python
def _wrap_tool_callable(
    tool: BoundTool,
    tool_context: dict[str, Any] | None = None,
    *,
    collector: BoundaryTraceCollector | None = None,
):
```

Inside `wrapper`, read `collector.current_tool_call()`, bind defaults without
changing the real invocation, and record B05:

```python
        call_context = collector.current_tool_call() if collector is not None else {}
        call_context = call_context or {}
        started_at = _utc_iso_timestamp()
        started_perf = time.perf_counter()
        effective_arguments = dict(kwargs)
        try:
            signature = inspect.signature(original)
            bound = signature.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            effective_arguments = dict(bound.arguments)
        except (TypeError, ValueError):
            pass
        if collector is not None:
            collector.record(
                transition="B05_WRAPPER_TO_PYTHON_TOOL",
                from_module="lumibot_tool_wrapper",
                to_module="python_tool",
                model_turn_id=call_context.get("model_turn_id"),
                tool_batch_id=call_context.get("tool_batch_id"),
                call_id=call_context.get("call_id"),
                started_at=started_at,
                payload={
                    "tool_name": tool.name,
                    "source": tool.source,
                    "callable_module": getattr(original, "__module__", None),
                    "callable_qualname": getattr(original, "__qualname__", None),
                    "positional_arguments": list(args),
                    "keyword_arguments": dict(kwargs),
                    "effective_arguments": effective_arguments,
                },
            )
```

Replace the combined call/serialization with distinct values:

```python
        try:
            with agent_tool_context(tool_context):
                raw_result = original(*args, **kwargs)
            if collector is not None:
                collector.record(
                    transition="B06_PYTHON_TOOL_TO_WRAPPER",
                    from_module="python_tool",
                    to_module="lumibot_tool_wrapper",
                    status="success",
                    model_turn_id=call_context.get("model_turn_id"),
                    tool_batch_id=call_context.get("tool_batch_id"),
                    call_id=call_context.get("call_id"),
                    started_at=started_at,
                    duration_ms=max((time.perf_counter() - started_perf) * 1000, 0.0),
                    payload={"raw_result": collector.describe_raw_value(raw_result)},
                )
            result = _json_safe_value(raw_result)
        except Exception as exc:
            if collector is not None:
                collector.record(
                    transition="B06_PYTHON_TOOL_TO_WRAPPER",
                    from_module="python_tool",
                    to_module="lumibot_tool_wrapper",
                    status="error",
                    model_turn_id=call_context.get("model_turn_id"),
                    tool_batch_id=call_context.get("tool_batch_id"),
                    call_id=call_context.get("call_id"),
                    started_at=started_at,
                    duration_ms=max((time.perf_counter() - started_perf) * 1000, 0.0),
                    payload={"tool_name": tool.name},
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
            result = _tool_error_payload(tool.name, kwargs, exc)
```

Record B07 immediately before returning:

```python
        if collector is not None:
            collector.record(
                transition="B07_WRAPPER_TO_FUNCTION_TOOL",
                from_module="lumibot_tool_wrapper",
                to_module="function_tool",
                model_turn_id=call_context.get("model_turn_id"),
                tool_batch_id=call_context.get("tool_batch_id"),
                call_id=call_context.get("call_id"),
                payload={"serialized_result": result},
            )
```

- [ ] **Step 4: Run wrapper tests including the existing NaN regression**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_runtime_boundary_trace.py `
  tests/backtest/test_agent_runtime_backtest.py::test_agent_runtime_wrap_tool_callable_sanitizes_nan_payloads -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add lumibot/components/agents/runtime.py `
  tests/test_agent_runtime_boundary_trace.py `
  tests/backtest/test_agent_runtime_backtest.py
git commit -m "feat: trace local agent tool execution"
```

---

### Task 6: Observe FunctionTool Validation and Preserve Call IDs

**Files:**
- Modify: `lumibot/components/agents/boundary_trace.py`
- Modify: `lumibot/components/agents/runtime.py`
- Modify: `lumibot/components/agents/schemas.py`
- Modify: `tests/test_agent_runtime_boundary_trace.py`

- [ ] **Step 1: Write failing FunctionTool tests**

Add credential-free tests using the installed ADK FunctionTool:

```python
import asyncio

from google.adk.tools.function_tool import FunctionTool
from pydantic import BaseModel

from lumibot.components.agents.runtime import _build_observed_function_tool


class SymbolInput(BaseModel):
    symbol: str


class FakeToolContext:
    function_call_id = "call_A"
    tool_confirmation = None

    class Actions:
        skip_summarization = False

    actions = Actions()


def test_observed_function_tool_correlates_validated_wrapper_arguments(tmp_path):
    received = {}

    def tool(request: SymbolInput, asset_type: str = "stock"):
        received["request"] = request
        received["asset_type"] = asset_type
        return {"symbol": request.symbol, "asset_type": asset_type}

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    turn_id = collector.start_model_turn()
    batch_id = collector.register_tool_batch(turn_id, ["call_A"])
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(name="tool", description="tool", function=tool),
        collector=collector,
        shared_tool_context={},
    )

    result = asyncio.run(
        observed.run_async(
            args={"request": {"symbol": "QQQ"}, "unknown": "removed"},
            tool_context=FakeToolContext(),
        )
    )

    assert result == {"symbol": "QQQ", "asset_type": "stock"}
    assert isinstance(received["request"], SymbolInput)
    assert received["request"].symbol == "QQQ"
    assert received["asset_type"] == "stock"
    b04 = _events(collector, "B04_FUNCTION_TOOL_TO_WRAPPER")[0]
    assert b04["model_turn_id"] == turn_id
    assert b04["tool_batch_id"] == batch_id
    assert b04["payload"]["model_arguments"]["unknown"] == "removed"
    assert b04["payload"]["wrapper_received_arguments"]["request"] == {"symbol": "QQQ"}
    assert b04["payload"]["removed_arguments"] == ["unknown"]
    b05 = _events(collector, "B05_WRAPPER_TO_PYTHON_TOOL")[0]
    assert b05["payload"]["effective_arguments"]["asset_type"] == "stock"


def test_missing_required_argument_records_b04_without_local_execution(tmp_path):
    executed = []

    def tool(symbol: str):
        executed.append(symbol)
        return {"symbol": symbol}

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    turn_id = collector.start_model_turn()
    collector.register_tool_batch(turn_id, ["call_A"])
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(name="tool", description="tool", function=tool),
        collector=collector,
        shared_tool_context={},
    )

    result = asyncio.run(observed.run_async(args={}, tool_context=FakeToolContext()))

    assert executed == []
    assert "mandatory input parameters" in result["error"]
    b04 = _events(collector, "B04_FUNCTION_TOOL_TO_WRAPPER")[0]
    assert b04["status"] == "blocked"
    assert b04["payload"]["missing_mandatory_arguments"] == ["symbol"]
    assert _events(collector, "B05_WRAPPER_TO_PYTHON_TOOL") == []
```

- [ ] **Step 2: Run the tests and verify the builder is missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_runtime_boundary_trace.py::test_observed_function_tool_correlates_validated_wrapper_arguments `
  tests/test_agent_runtime_boundary_trace.py::test_missing_required_argument_records_b04_without_local_execution -v
```

Expected: FAIL because `_build_observed_function_tool` does not exist.

- [ ] **Step 3: Add collector call-state methods**

Add thread-safe methods:

```python
    def note_wrapper_arguments(self, call_id: str, arguments: dict[str, Any]) -> None:
        with self._lock:
            state = self._call_index.setdefault(call_id, {})
            state["wrapper_received_arguments"] = arguments
            state["wrapper_invoked"] = True

    def call_state(self, call_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._call_index.get(call_id) or {})
```

In `_wrap_tool_callable`, call `note_wrapper_arguments()` before B05.

- [ ] **Step 4: Implement an observed FunctionTool subclass factory**

Add to `runtime.py`:

```python
def _build_observed_function_tool(
    function_tool_type: type[Any],
    tool: BoundTool,
    *,
    collector: BoundaryTraceCollector | None,
    shared_tool_context: dict[str, Any],
):
    wrapped = _wrap_tool_callable(tool, shared_tool_context, collector=collector)
    if collector is None:
        return function_tool_type(wrapped)

    class ObservedFunctionTool(function_tool_type):
        async def run_async(self, *, args: dict[str, Any], tool_context: Any) -> Any:
            provider_call_id = getattr(tool_context, "function_call_id", None)
            call_id = (
                str(provider_call_id)
                if provider_call_id
                else f"generated:function_tool:{uuid4().hex}"
            )
            call_id_source = (
                "provider"
                if provider_call_id
                else "generated_missing_function_tool_id"
            )
            ids = collector.call_ids(call_id)
            with collector.tool_call_context(
                call_id=call_id,
                tool_name=tool.name,
                model_turn_id=ids.get("model_turn_id"),
                tool_batch_id=ids.get("tool_batch_id"),
            ):
                result = await super().run_async(args=args, tool_context=tool_context)
            state = collector.call_state(call_id)
            wrapper_arguments = state.get("wrapper_received_arguments")
            missing = []
            if not state.get("wrapper_invoked") and isinstance(result, dict):
                message = str(result.get("error") or "")
                if "mandatory input parameters" in message:
                    signature = inspect.signature(wrapped)
                    missing = [
                        name
                        for name, parameter in signature.parameters.items()
                        if parameter.default is inspect.Parameter.empty
                        and name not in args
                    ]
            accepted = set(wrapper_arguments or {})
            collector.record(
                transition="B04_FUNCTION_TOOL_TO_WRAPPER",
                from_module="function_tool",
                to_module="lumibot_tool_wrapper",
                status="success" if state.get("wrapper_invoked") else "blocked",
                model_turn_id=ids.get("model_turn_id"),
                tool_batch_id=ids.get("tool_batch_id"),
                call_id=call_id,
                payload={
                    "tool_name": tool.name,
                    "call_id_source": call_id_source,
                    "model_arguments": dict(args),
                    "wrapper_received_arguments": wrapper_arguments,
                    "removed_arguments": sorted(set(args) - accepted) if wrapper_arguments is not None else [],
                    "missing_mandatory_arguments": missing,
                },
            )
            return result

    return ObservedFunctionTool(wrapped)
```

This observes the authoritative wrapper arguments rather than claiming a
separately reconstructed validation pass is exact.

- [ ] **Step 5: Preserve call IDs in normalized events**

In `_normalize_event`, set:

```python
call_id=str(getattr(function_call, "id", None) or "") or None
```

for tool calls and:

```python
call_id=str(getattr(function_response, "id", None) or "") or None
```

for tool results. Also copy raw ADK event IDs:

```python
event_id=str(getattr(event, "id", None) or "") or None
invocation_id=str(getattr(event, "invocation_id", None) or "") or None
```

onto every normalized event.

- [ ] **Step 6: Replace FunctionTool construction in `_run_async`**

Replace:

```python
tools = [function_tool_type(_wrap_tool_callable(tool, active_tool_context)) for tool in request.bound_tools]
```

with:

```python
tools = [
    _build_observed_function_tool(
        function_tool_type,
        tool,
        collector=request.boundary_collector,
        shared_tool_context=active_tool_context,
    )
    for tool in request.bound_tools
]
```

- [ ] **Step 7: Run FunctionTool and normalized-event tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_runtime_boundary_trace.py `
  tests/backtest/test_agent_runtime_backtest.py::test_agent_runtime_wrap_tool_callable_sanitizes_nan_payloads -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add lumibot/components/agents/boundary_trace.py `
  lumibot/components/agents/runtime.py `
  lumibot/components/agents/schemas.py `
  tests/test_agent_runtime_boundary_trace.py
git commit -m "feat: trace FunctionTool argument boundaries"
```

---

### Task 7: Capture ADK Model Turns, Dispatches, Pruning, and FunctionResponses

**Files:**
- Modify: `lumibot/components/agents/boundary_trace.py`
- Modify: `lumibot/components/agents/runtime.py`
- Modify: `tests/test_agent_runtime_boundary_trace.py`

- [ ] **Step 1: Write a failing callback-order test**

Add fake request/response objects or use `google.genai.types`:

```python
from types import SimpleNamespace

from google.genai import types

from lumibot.components.agents.runtime import GoogleADKRuntime, _normalize_event


def test_before_model_records_post_pruning_request(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    request = make_runtime_request(
        collector,
        model="openai/gpt-5.4-mini",
    )
    llm_request = SimpleNamespace(
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_function_response(
                        name=f"large_tool_{index}",
                        response={"value": str(index) + ("x" * 10_000)},
                    )
                ],
            )
            for index in range(5)
        ],
        config=types.GenerateContentConfig(),
        model="openai/gpt-5.4-mini",
    )
    runtime = GoogleADKRuntime()

    prune = runtime._before_model_context_pruning_callback(request)
    capture = runtime._before_model_boundary_callback(request)
    prune(callback_context=None, llm_request=llm_request)
    capture(callback_context=None, llm_request=llm_request)

    b09 = _events(collector, "B09_ADK_TO_LITELLM")[0]
    assert b09["payload"]["context_pruning"]["pruned"] is True
    assert (
        "Older tool result omitted by Lumibot before this model call because "
        "the provider context window would otherwise be exceeded. Use the most "
        "recent visible tool results or call a targeted tool again if this older "
        "detail is still required."
    ) in str(b09["payload"])
```

This literal is the current replacement message produced by
`_prune_request_contents_for_context_window`; keeping it explicit proves B09
captures the post-pruning request that the model actually receives.

- [ ] **Step 2: Write a failing parallel dispatch and FunctionResponse test**

Add a helper that builds one ADK event with two function calls:

```python
def test_adk_callbacks_assign_one_batch_and_preserve_function_response_ids(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    runtime_request = make_runtime_request(collector)
    runtime = GoogleADKRuntime()
    response = SimpleNamespace(
        content=types.Content(
            role="model",
            parts=[
                types.Part(text="Visible model analysis", thought=True),
                types.Part.from_function_call(name="market_last_price", args={"symbol": "QQQ"}),
                types.Part.from_function_call(name="market_last_price", args={"symbol": "SPY"}),
            ],
        ),
        usage_metadata=None,
        finish_reason="tool_calls",
    )
    response.content.parts[0].function_call.id = "call_A"
    response.content.parts[1].function_call.id = "call_B"

    runtime._after_model_boundary_callback(runtime_request)(
        callback_context=None,
        llm_response=response,
    )

    assert collector.call_ids("call_A")["tool_batch_id"] == collector.call_ids("call_B")["tool_batch_id"]
    assert collector.call_ids("call_A")["call_sequence"] == 1
    assert collector.call_ids("call_B")["call_sequence"] == 2
    b02 = _events(collector, "B02_LITELLM_TO_ADK")[0]
    assert "Visible model analysis" in str(b02["payload"])
    assert b02["payload"]["reasoning_visibility"] == "provider_exposed_only"
```

Add these two focused tests alongside it:

```python
def test_after_model_marks_generated_fallback_call_id(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    runtime = GoogleADKRuntime()
    request = make_runtime_request(collector)
    response = SimpleNamespace(
        content=types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="market_last_price",
                    args={"symbol": "IWM"},
                )
            ],
        ),
        usage_metadata=None,
        finish_reason="tool_calls",
    )

    runtime._after_model_boundary_callback(request)(
        callback_context=None,
        llm_response=response,
    )

    b02 = _events(collector, "B02_LITELLM_TO_ADK")[0]
    call = b02["payload"]["tool_calls"][0]
    assert call["call_id"].startswith("generated:")
    assert call["call_id_source"] == "generated_missing_provider_id"


def test_normalize_event_records_scalar_wrapper_result_as_adk_mapping(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    collector.record(
        transition="B07_WRAPPER_TO_FUNCTION_TOOL",
        from_module="lumibot_tool_wrapper",
        to_module="function_tool",
        call_id="call_A",
        payload={"serialized_result": "ready"},
    )
    event = SimpleNamespace(
        id="event-1",
        invocation_id="invocation-1",
        content=types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="scalar_tool",
                    response={"result": "ready"},
                )
            ],
        ),
        usage_metadata=None,
    )
    event.content.parts[0].function_response.id = "call_A"

    normalized = _normalize_event(event, collector=collector)

    assert normalized[0].call_id == "call_A"
    b08 = _events(collector, "B08_FUNCTION_TOOL_TO_ADK")[0]
    assert b08["payload"]["function_response"] == {"result": "ready"}
```

The second test distinguishes the scalar B07 serialization from ADK's mapping
form at B08 rather than presenting them as the same value.

- [ ] **Step 3: Run the tests and verify callbacks are missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_runtime_boundary_trace.py -v
```

Expected: FAIL on missing boundary callback methods and collector-aware event
normalization.

- [ ] **Step 4: Implement ADK request/response serialization helpers**

Add private helpers in `runtime.py`:

```python
def _adk_object_payload(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_safe_value(model_dump(mode="json", exclude_none=True))
    return _json_safe_value(value)


def _parts_function_calls(value: Any) -> list[Any]:
    parts = getattr(getattr(value, "content", None), "parts", None) or []
    return [
        part.function_call
        for part in parts
        if getattr(part, "function_call", None) is not None
    ]
```

- [ ] **Step 5: Implement model-turn callbacks**

`_before_model_boundary_callback()` must:

- start the next model turn;
- store the active turn on the collector;
- record B09 using the post-pruning `LlmRequest`;
- mark whether pruning notices are present.

Add collector methods:

```python
    def set_active_model_turn(self, model_turn_id: str) -> None:
        with self._lock:
            self._active_model_turn_id = model_turn_id

    def active_model_turn(self) -> str | None:
        with self._lock:
            return getattr(self, "_active_model_turn_id", None)
```

Implement the callbacks:

```python
    def _before_model_boundary_callback(self, request: RuntimeRequest):
        collector = request.boundary_collector
        if collector is None:
            return None

        def _callback(
            *,
            callback_context: Any = None,
            llm_request: Any = None,
            **_kwargs: Any,
        ) -> None:
            model_turn_id = collector.start_model_turn()
            collector.set_active_model_turn(model_turn_id)
            pruned_parts = 0
            for content in getattr(llm_request, "contents", None) or []:
                for part in getattr(content, "parts", None) or []:
                    response = getattr(
                        getattr(part, "function_response", None),
                        "response",
                        None,
                    )
                    if isinstance(response, dict) and response.get(
                        "lumibot_context_pruned"
                    ):
                        pruned_parts += 1
            collector.record(
                transition="B09_ADK_TO_LITELLM",
                from_module="google_adk",
                to_module="litellm",
                model_turn_id=model_turn_id,
                payload={
                    "llm_request": _adk_object_payload(llm_request),
                    "context_pruning": {
                        "pruned": pruned_parts > 0,
                        "pruned_tool_results": pruned_parts,
                    },
                },
            )
            return None

        return _callback

    def _after_model_boundary_callback(self, request: RuntimeRequest):
        collector = request.boundary_collector
        if collector is None:
            return None

        def _callback(
            *,
            callback_context: Any = None,
            llm_response: Any = None,
            **_kwargs: Any,
        ) -> None:
            model_turn_id = collector.active_model_turn()
            function_calls = _parts_function_calls(llm_response)
            call_records = []
            call_ids = []
            for index, function_call in enumerate(function_calls, start=1):
                provider_call_id = getattr(function_call, "id", None)
                call_id = (
                    str(provider_call_id)
                    if provider_call_id
                    else f"generated:{model_turn_id}:{index:04d}:{uuid4().hex}"
                )
                if not provider_call_id:
                    function_call.id = call_id
                call_ids.append(call_id)
                call_records.append(
                    {
                        "call_id": call_id,
                        "call_id_source": (
                            "provider"
                            if provider_call_id
                            else "generated_missing_provider_id"
                        ),
                        "name": getattr(function_call, "name", None),
                        "arguments": _adk_object_payload(
                            getattr(function_call, "args", None)
                        ),
                        "call_sequence": index,
                    }
                )
            batch_id = (
                collector.register_tool_batch(model_turn_id, call_ids)
                if model_turn_id and call_ids
                else None
            )
            collector.record(
                transition="B02_LITELLM_TO_ADK",
                from_module="litellm",
                to_module="google_adk",
                model_turn_id=model_turn_id,
                tool_batch_id=batch_id,
                payload={
                    "llm_response": _adk_object_payload(llm_response),
                    "tool_calls": call_records,
                    "reasoning_visibility": "provider_exposed_only",
                },
            )
            return None

        return _callback
```

The after-model callback:

- read the current model turn;
- record B02 with the complete ADK response;
- collect function call IDs;
- generate marked fallback IDs only for missing IDs;
- register one tool batch for all calls in that response.

Record `reasoning_visibility: "provider_exposed_only"` in B02. Preserve
provider-exposed thought/reasoning parts already present in `LlmResponse`, but
do not claim access to hidden chain-of-thought or provider-internal reasoning.

- [ ] **Step 6: Implement B03 before-tool dispatch capture**

Add:

```python
    def _before_tool_boundary_callback(self, request: RuntimeRequest):
        collector = request.boundary_collector
        if collector is None:
            return None

        def _callback(*, tool: Any, args: dict[str, Any], tool_context: Any, **_kwargs: Any) -> None:
            provider_call_id = getattr(tool_context, "function_call_id", None)
            call_id = (
                str(provider_call_id)
                if provider_call_id
                else f"generated:adk_dispatch:{uuid4().hex}"
            )
            ids = collector.call_ids(call_id)
            collector.record(
                transition="B03_ADK_TO_FUNCTION_TOOL",
                from_module="google_adk",
                to_module="function_tool",
                model_turn_id=ids.get("model_turn_id"),
                tool_batch_id=ids.get("tool_batch_id"),
                call_id=call_id,
                payload={
                    "tool_name": getattr(tool, "name", None),
                    "call_id_source": (
                        "provider"
                        if provider_call_id
                        else "generated_missing_adk_dispatch_id"
                    ),
                    "model_arguments": args,
                    "call_sequence": ids.get("call_sequence"),
                    "parallel_batch": len(collector.batch_call_ids(ids.get("tool_batch_id"))) > 1,
                },
            )
            return None

        return _callback
```

Add `batch_call_ids()` to the collector rather than reading internal maps.

- [ ] **Step 7: Compose pruning and after-tool capture**

Keep current pruning behavior. Add one composite callback that:

1. records the unpruned FunctionTool response;
2. calls `_prune_tool_response_for_context_window`;
3. records before/after pruning metadata in collector pending call state;
4. returns the pruned response only when pruning occurred.

Do not place a capture callback after a pruning callback in an ADK callback
list, because ADK stops callback iteration when a callback returns a replacement
response.

Add this collector method:

```python
    def note_function_tool_response(
        self,
        call_id: str,
        *,
        unpruned_response: Any,
        model_facing_response: Any,
        pruned: bool,
    ) -> None:
        with self._lock:
            state = self._call_index.setdefault(call_id, {})
            state["function_tool_response"] = unpruned_response
            state["model_facing_response"] = model_facing_response
            state["tool_response_pruned"] = pruned
```

Implement the composite callback:

```python
    def _after_tool_boundary_and_pruning_callback(
        self,
        request: RuntimeRequest,
    ):
        collector = request.boundary_collector
        if collector is None:
            return self._after_tool_context_pruning_callback(request)

        def _callback(
            *,
            tool: Any,
            args: dict[str, Any],
            tool_context: Any,
            tool_response: Any,
            **_kwargs: Any,
        ) -> Any | None:
            call_id = str(getattr(tool_context, "function_call_id", None) or "")
            if not call_id:
                call_id = f"generated:after_tool:{uuid4().hex}"
                collector.add_diagnostic(
                    "generated_missing_after_tool_call_id",
                    call_id,
                )
            tool_name = str(getattr(tool, "name", None) or "")
            pruned_response = _prune_tool_response_for_context_window(
                tool_response,
                tool_name=tool_name,
            )
            model_facing_response = (
                pruned_response
                if pruned_response is not None
                else tool_response
            )
            collector.note_function_tool_response(
                call_id,
                unpruned_response=tool_response,
                model_facing_response=model_facing_response,
                pruned=pruned_response is not None,
            )
            return pruned_response

        return _callback
```

Returning `None` preserves the original FunctionTool response; returning the
pruned mapping preserves the existing context-window behavior.

- [ ] **Step 8: Record B08 from the real ADK FunctionResponse event**

Change:

```python
def _normalize_event(
    event: Any,
    *,
    collector: BoundaryTraceCollector | None = None,
) -> list[AgentTraceEvent]:
```

When a `function_response` part appears:

- read `function_response.id`;
- read pending before/after pruning state;
- record B08 with event ID, invocation ID, response ID, function name,
  individual payload, and merged-event metadata;
- preserve call ID on the normalized `tool_result`.

Use `collector.call_state(call_id)` to include
`function_tool_response`, `model_facing_response`, and
`tool_response_pruned` in B08. The event's actual
`function_response.response` remains authoritative for what ADK put into the
next model turn.

- [ ] **Step 9: Wire callbacks in deterministic order**

Build the ADK agent with:

```python
before_model_callbacks = [
    callback
    for callback in (
        self._before_model_context_pruning_callback(request),
        self._before_model_boundary_callback(request),
    )
    if callback is not None
]

agent = LlmAgentType(
    # existing arguments
    before_model_callback=before_model_callbacks or None,
    after_model_callback=self._after_model_boundary_callback(request),
    before_tool_callback=self._before_tool_boundary_callback(request),
    after_tool_callback=self._after_tool_boundary_and_pruning_callback(request),
)
```

Pass the collector to `_normalize_event`.

- [ ] **Step 10: Run all runtime-boundary tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_boundary_trace.py -v
```

Expected: PASS.

- [ ] **Step 11: Commit**

```powershell
git add lumibot/components/agents/boundary_trace.py `
  lumibot/components/agents/runtime.py `
  tests/test_agent_runtime_boundary_trace.py
git commit -m "feat: trace Google ADK model and tool boundaries"
```

---

### Task 8: Capture LiteLLM Provider-Adapter Requests and Responses

**Files:**
- Create: `lumibot/components/agents/litellm_trace.py`
- Modify: `lumibot/components/agents/runtime.py`
- Create: `tests/test_agent_litellm_trace.py`

- [ ] **Step 1: Write failing request-scoping and redaction tests**

Create:

```python
import asyncio

from lumibot.components.agents.boundary_trace import BoundaryTraceCollector
from lumibot.components.agents.litellm_trace import LiteLLMBoundaryLogger


def test_litellm_success_callback_records_b10_and_b01_without_secrets(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    logger = LiteLLMBoundaryLogger(collector)
    kwargs = {
        "model": "openai/test",
        "messages": [{"role": "tool", "tool_call_id": "call_A", "content": "{\"price\": 1}"}],
        "tools": [{"type": "function", "function": {"name": "market_last_price"}}],
        "api_key": "test-only-secret-value",
        "metadata": {"lumibot_model_turn_id": turn_id},
    }
    response = {
        "id": "response-1",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "done"},
            }
        ],
    }

    asyncio.run(logger.async_log_success_event(kwargs, response, None, None))

    b10 = [
        event for event in collector.export()["events"]
        if event["transition"] == "B10_LITELLM_TO_PROVIDER"
    ][0]
    b01 = [
        event for event in collector.export()["events"]
        if event["transition"] == "B01_PROVIDER_TO_LITELLM"
    ][0]
    assert b10["model_turn_id"] == turn_id
    assert b01["model_turn_id"] == turn_id
    assert "test-only-secret-value" not in str(b10)
    assert "api_key" not in str(b10["payload"]).lower()
    assert b10["payload"]["provider_attempt"] is None
    assert b10["payload"]["provider_retry_visibility"] == "unavailable"


def test_litellm_exposed_retry_count_records_attempt_and_acceptance(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    turn_id = collector.start_model_turn()
    logger = LiteLLMBoundaryLogger(collector)
    kwargs = {
        "model": "openai/test",
        "messages": [],
        "retry_count": 2,
        "metadata": {"lumibot_model_turn_id": turn_id},
    }

    asyncio.run(
        logger.async_log_success_event(
            kwargs,
            {"id": "response-3", "choices": []},
            None,
            None,
        )
    )

    b01 = [
        event
        for event in collector.export()["events"]
        if event["transition"] == "B01_PROVIDER_TO_LITELLM"
    ][0]
    assert b01["payload"]["provider_attempt"] == 3
    assert b01["payload"]["provider_retry_visibility"] == "exposed"
    assert b01["payload"]["accepted_response"] is True


def test_litellm_failure_callback_records_attempt_error(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    logger = LiteLLMBoundaryLogger(collector)

    asyncio.run(
        logger.async_log_failure_event(
            {"model": "openai/test", "messages": [], "metadata": {"lumibot_model_turn_id": turn_id}},
            RuntimeError("provider unavailable"),
            None,
            None,
        )
    )

    events = collector.export()["events"]
    assert events[-1]["transition"] == "B01_PROVIDER_TO_LITELLM"
    assert events[-1]["status"] == "error"
```

- [ ] **Step 2: Run and verify the module is missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_litellm_trace.py -v
```

Expected: FAIL because `litellm_trace.py` does not exist.

- [ ] **Step 3: Implement the request-scoped LiteLLM logger**

Create:

```python
from __future__ import annotations

from datetime import datetime
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

from .boundary_trace import BoundaryTraceCollector


def _safe_provider_request(kwargs: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "model",
        "messages",
        "tools",
        "response_format",
        "temperature",
        "max_completion_tokens",
        "top_p",
        "stop",
        "timeout",
        "prompt_cache_key",
        "prompt_cache_retention",
        "metadata",
        "stream",
    }
    return {key: value for key, value in kwargs.items() if key in allowed}


def _provider_attempt_payload(
    kwargs: dict[str, Any],
    *,
    accepted_response: bool,
) -> dict[str, Any]:
    retry_count = kwargs.get("retry_count")
    if retry_count is None and isinstance(kwargs.get("litellm_params"), dict):
        retry_count = kwargs["litellm_params"].get("retry_count")
    if isinstance(retry_count, int) and retry_count >= 0:
        return {
            "provider_attempt": retry_count + 1,
            "provider_retry_visibility": "exposed",
            "accepted_response": accepted_response,
        }
    return {
        "provider_attempt": None,
        "provider_retry_visibility": "unavailable",
        "accepted_response": accepted_response,
    }


def _callback_timing(start_time: Any, end_time: Any) -> dict[str, Any]:
    if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
        return {}
    return {
        "started_at": start_time.isoformat(),
        "ended_at": end_time.isoformat(),
        "duration_ms": max((end_time - start_time).total_seconds() * 1000, 0.0),
    }


class LiteLLMBoundaryLogger(CustomLogger):
    def __init__(self, collector: BoundaryTraceCollector) -> None:
        super().__init__()
        self.collector = collector

    def _turn_id(self, kwargs: dict[str, Any]) -> str | None:
        metadata = kwargs.get("metadata")
        if isinstance(metadata, dict) and metadata.get("lumibot_model_turn_id"):
            return str(metadata["lumibot_model_turn_id"])
        return self.collector.active_model_turn()

    async def async_log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        turn_id = self._turn_id(kwargs)
        self.collector.record(
            transition="B10_LITELLM_TO_PROVIDER",
            from_module="litellm",
            to_module="provider",
            model_turn_id=turn_id,
            **_callback_timing(start_time, end_time),
            payload={
                "capture_type": "provider_adapter_request",
                "request": _safe_provider_request(kwargs),
                **_provider_attempt_payload(
                    kwargs,
                    accepted_response=True,
                ),
            },
        )
        self.collector.record(
            transition="B01_PROVIDER_TO_LITELLM",
            from_module="provider",
            to_module="litellm",
            model_turn_id=turn_id,
            **_callback_timing(start_time, end_time),
            payload={
                "capture_type": "provider_adapter_response",
                "response": response_obj,
                **_provider_attempt_payload(
                    kwargs,
                    accepted_response=True,
                ),
            },
        )

    async def async_log_failure_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        turn_id = self._turn_id(kwargs)
        self.collector.record(
            transition="B10_LITELLM_TO_PROVIDER",
            from_module="litellm",
            to_module="provider",
            model_turn_id=turn_id,
            status="error",
            **_callback_timing(start_time, end_time),
            payload={
                "capture_type": "provider_adapter_request",
                "request": _safe_provider_request(kwargs),
                **_provider_attempt_payload(
                    kwargs,
                    accepted_response=False,
                ),
            },
        )
        self.collector.record(
            transition="B01_PROVIDER_TO_LITELLM",
            from_module="provider",
            to_module="litellm",
            model_turn_id=turn_id,
            status="error",
            **_callback_timing(start_time, end_time),
            payload={
                "capture_type": "provider_adapter_response",
                **_provider_attempt_payload(
                    kwargs,
                    accepted_response=False,
                ),
            },
            error={"type": type(response_obj).__name__, "message": str(response_obj)},
        )
```

Keep this as a request-scoped `CustomLogger`; do not append it to
`litellm.callbacks`, `litellm.success_callback`, or
`litellm.failure_callback`.

- [ ] **Step 4: Run logger unit tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_litellm_trace.py -v
```

Expected: PASS.

- [ ] **Step 5: Add an actual LiteLLM callback compatibility test**

Use LiteLLM's credential-free mock response:

```python
def test_litellm_dynamic_callbacks_fire_without_global_registration(tmp_path):
    import litellm

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    logger = LiteLLMBoundaryLogger(collector)
    before = list(litellm.callbacks)

    asyncio.run(
        litellm.acompletion(
            model="openai/test",
            messages=[{"role": "user", "content": "hello"}],
            mock_response="hello",
            metadata={"lumibot_model_turn_id": turn_id},
            success_callback=[logger],
            failure_callback=[logger],
        )
    )

    assert litellm.callbacks == before
    transitions = [event["transition"] for event in collector.export()["events"]]
    assert "B10_LITELLM_TO_PROVIDER" in transitions
    assert "B01_PROVIDER_TO_LITELLM" in transitions
```

- [ ] **Step 6: Add an observed LiteLLM model wrapper**

In `runtime.py`, replace the local provider class selection with a model type
that injects request-scoped callbacks before delegating:

```python
class ObservedLiteLlm(LiteLlm):
    def __init__(self, *, boundary_collector: BoundaryTraceCollector | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._boundary_collector = boundary_collector
        self._boundary_logger = (
            LiteLLMBoundaryLogger(boundary_collector)
            if boundary_collector is not None
            else None
        )

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        if self._boundary_logger is not None:
            metadata = dict(self._additional_args.get("metadata") or {})
            metadata["lumibot_agent_run_id"] = self._boundary_collector.agent_run_id
            metadata["lumibot_model_turn_id"] = self._boundary_collector.active_model_turn()
            self._additional_args["metadata"] = metadata
            success_callbacks = list(
                self._additional_args.get("success_callback") or []
            )
            failure_callbacks = list(
                self._additional_args.get("failure_callback") or []
            )
            if self._boundary_logger not in success_callbacks:
                success_callbacks.append(self._boundary_logger)
            if self._boundary_logger not in failure_callbacks:
                failure_callbacks.append(self._boundary_logger)
            self._additional_args["success_callback"] = success_callbacks
            self._additional_args["failure_callback"] = failure_callbacks
        async for response in super().generate_content_async(llm_request, stream=stream):
            yield response
```

Preserve the Cerebras thought-stripping behavior by subclassing
`ObservedLiteLlm` for Cerebras rather than maintaining two unrelated
implementations.

Extend `_resolve_model_for_adk()` with:

```python
boundary_collector: BoundaryTraceCollector | None = None
```

and pass `request.boundary_collector` from `_run_async`.

- [ ] **Step 7: Run provider routing and LiteLLM tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_litellm_trace.py `
  tests/test_agent_runtime_provider_keys.py `
  tests/test_agent_runtime_errors.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add lumibot/components/agents/litellm_trace.py `
  lumibot/components/agents/runtime.py `
  tests/test_agent_litellm_trace.py
git commit -m "feat: trace LiteLLM provider boundaries"
```

---

### Task 9: Build a Credential-Free Full-Loop Integration Test

**Files:**
- Modify: `tests/test_agent_runtime_boundary_trace.py`
- Modify: `lumibot/components/agents/runtime.py`

- [ ] **Step 1: Add a three-turn scripted provider fixture**

Add a scripted `acompletion` replacement that:

1. returns two same-name tool calls (`call_A`, `call_B`);
2. returns one additional call (`call_C`) after receiving both results;
3. returns final text after receiving `call_C`;
4. invokes the request-scoped success callback with the same request and
   response objects it returns.

Use `litellm.types.utils.ModelResponse` and
`ChatCompletionMessageToolCall` from the installed LiteLLM package so the
normal ADK bridge performs the real conversion.

The fixture must deliberately delay the QQQ tool longer than the SPY tool so
completion order is B then A while call order remains A then B.

Add these concrete test helpers near the top of
`tests/test_agent_runtime_boundary_trace.py`:

```python
import json
import time
from datetime import datetime, timezone

import litellm
from litellm.types.utils import ModelResponse


def _model_response(*, tool_calls=None, text=None):
    message = {"role": "assistant", "content": text}
    if tool_calls is not None:
        message["tool_calls"] = [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "market_last_price",
                    "arguments": json.dumps({"symbol": symbol}),
                },
            }
            for call_id, symbol in tool_calls
        ]
    return ModelResponse(
        model="openai/test",
        choices=[
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )


def delayed_price_tool(symbol: str):
    time.sleep(0.03 if symbol == "QQQ" else 0.005)
    return {"symbol": symbol, "price": 100.0 if symbol == "QQQ" else 90.0}


def install_scripted_acompletion(monkeypatch):
    responses = iter(
        [
            _model_response(tool_calls=[("call_A", "QQQ"), ("call_B", "SPY")]),
            _model_response(tool_calls=[("call_C", "IWM")]),
            _model_response(text="RESULT: complete"),
        ]
    )

    async def scripted_acompletion(**kwargs):
        response = next(responses)
        now = datetime.now(timezone.utc)
        for callback in kwargs.get("success_callback") or []:
            await callback.async_log_success_event(kwargs, response, now, now)
        return response

    monkeypatch.setattr(litellm, "acompletion", scripted_acompletion)


def _trace_events(trace, transition):
    return [
        event
        for event in trace["events"]
        if event["transition"] == transition
    ]


def model_turn_ids(trace):
    return list(
        dict.fromkeys(
            event["model_turn_id"]
            for event in trace["events"]
            if event.get("model_turn_id")
        )
    )


def calls_in_model_order(trace, *, batch):
    batch_ids = list(
        dict.fromkeys(
            event["tool_batch_id"]
            for event in _trace_events(trace, "B03_ADK_TO_FUNCTION_TOOL")
        )
    )
    batch_id = batch_ids[batch - 1]
    calls = [
        event
        for event in _trace_events(trace, "B03_ADK_TO_FUNCTION_TOOL")
        if event["tool_batch_id"] == batch_id
    ]
    return [
        event["call_id"]
        for event in sorted(
            calls,
            key=lambda event: event["payload"]["call_sequence"],
        )
    ]


def calls_in_completion_order(trace, *, batch):
    batch_ids = list(
        dict.fromkeys(
            event["tool_batch_id"]
            for event in _trace_events(trace, "B03_ADK_TO_FUNCTION_TOOL")
        )
    )
    batch_id = batch_ids[batch - 1]
    return [
        event["call_id"]
        for event in _trace_events(trace, "B08_FUNCTION_TOOL_TO_ADK")
        if event["tool_batch_id"] == batch_id
    ]


def response_for(trace, call_id):
    event = next(
        event
        for event in _trace_events(trace, "B08_FUNCTION_TOOL_TO_ADK")
        if event["call_id"] == call_id
    )
    return event["payload"]["function_response"]


def next_provider_request(trace, *, after_call):
    response_event = next(
        event
        for event in _trace_events(trace, "B08_FUNCTION_TOOL_TO_ADK")
        if event["call_id"] == after_call
    )
    return next(
        event["payload"]
        for event in _trace_events(trace, "B10_LITELLM_TO_PROVIDER")
        if event["sequence"] > response_event["sequence"]
        and after_call in json.dumps(event["payload"], sort_keys=True)
    )
```

- [ ] **Step 2: Write the failing full-loop assertion**

```python
def test_full_boundary_loop_preserves_turns_batches_and_reversed_parallel_completion(
    monkeypatch,
    tmp_path,
):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    runtime = GoogleADKRuntime()
    request = make_runtime_request(
        collector,
        tools=[
            BoundTool(
                name="market_last_price",
                description="price",
                function=delayed_price_tool,
            )
        ],
    )
    install_scripted_acompletion(monkeypatch)

    result = runtime.run(request)
    trace = result.boundary_trace

    assert result.summary == "RESULT: complete"
    assert {event["transition"] for event in trace["events"]} >= {
        "B01_PROVIDER_TO_LITELLM",
        "B02_LITELLM_TO_ADK",
        "B03_ADK_TO_FUNCTION_TOOL",
        "B04_FUNCTION_TOOL_TO_WRAPPER",
        "B05_WRAPPER_TO_PYTHON_TOOL",
        "B06_PYTHON_TOOL_TO_WRAPPER",
        "B07_WRAPPER_TO_FUNCTION_TOOL",
        "B08_FUNCTION_TOOL_TO_ADK",
        "B09_ADK_TO_LITELLM",
        "B10_LITELLM_TO_PROVIDER",
    }
    assert model_turn_ids(trace) == [
        "run-1:turn:0001",
        "run-1:turn:0002",
        "run-1:turn:0003",
    ]
    assert calls_in_model_order(trace, batch=1) == ["call_A", "call_B"]
    assert calls_in_completion_order(trace, batch=1) == ["call_B", "call_A"]
    assert response_for(trace, "call_A")["symbol"] == "QQQ"
    assert response_for(trace, "call_B")["symbol"] == "SPY"
    assert "call_A" in json.dumps(
        next_provider_request(trace, after_call="call_A"),
        sort_keys=True,
    )
```

The helpers pair events by call ID and event sequence; they must never infer
correlation from the repeated tool name.

- [ ] **Step 3: Run the integration test and inspect failures by boundary**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_runtime_boundary_trace.py::test_full_boundary_loop_preserves_turns_batches_and_reversed_parallel_completion -vv
```

Expected before final wiring: FAIL naming one or more missing boundary events.

- [ ] **Step 4: Complete runtime result export**

Before returning `AgentRunResult` from `GoogleADKRuntime._run_async`, set:

```python
boundary_trace=(
    request.boundary_collector.export()
    if request.boundary_collector is not None
    else None
),
```

Ensure B02 uses transition name `B02_LITELLM_TO_ADK`, and every transition name
exactly matches the approved constants used by tests.

- [ ] **Step 5: Run the full-loop test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_runtime_boundary_trace.py::test_full_boundary_loop_preserves_turns_batches_and_reversed_parallel_completion -vv
```

Expected: PASS.

- [ ] **Step 6: Run all new boundary suites**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_boundary_trace.py `
  tests/test_agent_runtime_boundary_trace.py `
  tests/test_agent_litellm_trace.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add lumibot/components/agents/runtime.py `
  tests/test_agent_runtime_boundary_trace.py
git commit -m "test: verify complete agent tool boundary loop"
```

---

### Task 10: Persist Partial Traces on Handled Failures

**Files:**
- Modify: `lumibot/components/agents/manager.py`
- Modify: `tests/backtest/test_agent_runtime_backtest.py`

- [ ] **Step 1: Write a failing handled-provider-error test**

Add a runtime that records B09 and then raises a transient error. Verify the
manager's existing safe no-op result still writes a trace:

```python
class BoundaryFailingRuntime:
    def __init__(self, error):
        self.error = error

    def run(self, request):
        collector = request.boundary_collector
        collector.record(
            transition="B09_ADK_TO_LITELLM",
            from_module="google_adk",
            to_module="litellm",
            model_turn_id=collector.start_model_turn(),
            payload={"model": request.model, "contents": []},
        )
        raise self.error


def test_handled_agent_failure_persists_partial_boundary_trace(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path))
    runtime = BoundaryFailingRuntime(error=TimeoutError("provider timeout"))
    strategy = PromptCaptureStrategy()
    handle = strategy.agents.create(
        name="failure_agent",
        system_prompt="test",
        runtime=runtime,
    )

    result = handle.run(task_prompt="test")

    assert "Skipped this iteration" in result.summary
    trace_path = next((tmp_path / "agent_runtime" / "traces" / "failure_agent").glob("*.json"))
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["boundary_trace"]["events"]
    assert trace["boundary_trace"]["diagnostics"]
    assert trace["boundary_trace"]["events"][0]["transition"] == "B09_ADK_TO_LITELLM"


def test_trace_write_failure_does_not_change_successful_agent_result(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path))
    strategy = PromptCaptureStrategy()
    handle = strategy.agents.create(
        name="trace_write_failure_agent",
        system_prompt="test",
        runtime=BoundaryResultRuntime(),
    )
    monkeypatch.setattr(
        handle,
        "_write_trace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("trace disk unavailable")
        ),
    )

    result = handle.run(task_prompt="test")

    assert result.summary == "RESULT: done"
    assert result.payload["trace_path"] is None
    assert result.payload["trace_write_error"] is True
    assert result.boundary_trace["diagnostics"][-1]["kind"] == "trace_write_failed"
```

- [ ] **Step 2: Run and verify no failure trace is currently written**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/backtest/test_agent_runtime_backtest.py::test_handled_agent_failure_persists_partial_boundary_trace `
  tests/backtest/test_agent_runtime_backtest.py::test_trace_write_failure_does_not_change_successful_agent_result -v
```

Expected: FAIL because the handled-error branch returns before normal trace
persistence and normal trace-write errors currently escape.

- [ ] **Step 3: Extract one trace-payload builder**

Add an `AgentHandle._build_trace_payload(...)` helper containing the current
normal trace fields and `boundary_trace`. Use it in success and handled-failure
paths.

The handled-failure path must:

- add a recorder diagnostic with the runtime error;
- finalize the collector;
- write the partial trace;
- put `trace_path` into `result.payload`;
- never save the failed result into replay cache;
- retain the current backtest auth/config/billing re-raise behavior.

Wrap the normal `_write_trace()` call too:

```python
        try:
            trace_path = self._write_trace(result, trace_payload)
        except OSError as exc:
            boundary_collector.add_diagnostic("trace_write_failed", exc)
            result.boundary_trace = boundary_collector.export()
            result.payload = {
                "trace_path": None,
                "warnings": result.warnings,
                "trace_write_error": True,
            }
            trace_path = None
```

Save replay cache only when `trace_path is not None`, because a cache reference
must never point to a trace that was not persisted. The successful agent
summary, tool result, and trading behavior remain unchanged.

- [ ] **Step 4: Run failure and error-classification tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/backtest/test_agent_runtime_backtest.py::test_handled_agent_failure_persists_partial_boundary_trace `
  tests/backtest/test_agent_runtime_backtest.py::test_trace_write_failure_does_not_change_successful_agent_result `
  tests/test_agent_runtime_errors.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add lumibot/components/agents/manager.py `
  tests/backtest/test_agent_runtime_backtest.py
git commit -m "fix: preserve partial agent boundary traces"
```

---

### Task 11: Verify Legacy Replay UI and Cache Compatibility

**Files:**
- Modify: `tests/test_agent_replay_ui_loader.py`
- Modify: `tests/backtest/test_agent_runtime_backtest.py`

- [ ] **Step 1: Add boundary-enabled and legacy loader fixtures**

Add:

```python
def test_loader_accepts_trace_with_boundary_trace_without_changing_legacy_tool_batches(tmp_path):
    trace_path = tmp_path / "traces" / "growth_agent" / "trace.json"
    _write_trace(
        trace_path,
        {
            "agent": "growth_agent",
            "model": "openai/test",
            "request": {"context": {}, "runtime_context": {}},
            "events": [
                {"kind": "tool_call", "tool_name": "market_last_price", "call_id": "call_A", "payload": {"symbol": "QQQ"}},
                {"kind": "tool_result", "tool_name": "market_last_price", "call_id": "call_A", "payload": {"price": 1}},
            ],
            "boundary_trace": {
                "schema_version": 1,
                "agent_run_id": "run-1",
                "events": [{"transition": "B03_ADK_TO_FUNCTION_TOOL", "call_id": "call_A"}],
                "diagnostics": [],
            },
            "summary": "done",
        },
    )

    agent = load_agent_trace(trace_path)

    assert agent.tool_batches[0].calls[0].tool_name == "market_last_price"
    assert agent.tool_batches[0].calls[0].raw_result == {"price": 1}
```

Keep the existing legacy trace tests unchanged.

- [ ] **Step 2: Run loader and replay-cache tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_replay_ui_loader.py `
  tests/test_agent_replay_ui_models.py `
  tests/backtest/test_agent_runtime_backtest.py::test_agent_runtime_stock_backtest_replays_from_cache -v
```

Expected: PASS without adding boundary rendering to the UI.

- [ ] **Step 3: Add a cache-source assertion**

Extend the replay-cache backtest test to assert:

```python
assert second_result.boundary_trace["execution_source"] == "replay_cache"
assert second_result.boundary_trace["status"] in {
    "available_original_trace",
    "unavailable_legacy_cache",
}
```

- [ ] **Step 4: Run targeted compatibility tests again**

Run the same command from Step 2.

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add tests/test_agent_replay_ui_loader.py `
  tests/backtest/test_agent_runtime_backtest.py
git commit -m "test: preserve legacy agent replay compatibility"
```

---

### Task 12: Measure Collector Overhead and Finalize Documentation

**Files:**
- Modify: `tests/test_agent_boundary_trace.py`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add a deterministic many-call overhead regression**

Do not assert a machine-specific millisecond ceiling. Assert linear event count
and prevent accidental quadratic payload growth:

```python
def test_collector_handles_many_small_parallel_events_without_duplicate_growth(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
        inline_payload_limit=10_000,
    )
    turn_id = collector.start_model_turn()
    call_ids = [f"call_{index:04d}" for index in range(500)]
    batch_id = collector.register_tool_batch(turn_id, call_ids)

    for call_id in call_ids:
        collector.record(
            transition="B03_ADK_TO_FUNCTION_TOOL",
            from_module="google_adk",
            to_module="function_tool",
            model_turn_id=turn_id,
            tool_batch_id=batch_id,
            call_id=call_id,
            payload={"value": 1},
        )

    exported = collector.export()
    assert len(exported["events"]) == 500
    assert sum(event["payload_meta"]["byte_count"] for event in exported["events"]) < 20_000
```

- [ ] **Step 2: Run the collector suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_boundary_trace.py -v
```

Expected: PASS.

- [ ] **Step 3: Update the changelog**

Under `## 4.5.75 - 2026-07-13` and its `### Added` section, add:

```markdown
- **AI-agent traces now preserve the complete model-to-tool semantic boundary.**
  Versioned, redacted boundary events correlate LiteLLM provider requests and
  responses, ADK model turns, parallel FunctionTool calls, validated wrapper
  arguments, raw local results, serialized FunctionResponses, context pruning,
  retries when exposed, and replay-cache provenance without capturing secrets
  or raw HTTP traffic.
```

- [ ] **Step 4: Run Ruff on all changed Python files**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check `
  lumibot/components/agents/trace_redaction.py `
  lumibot/components/agents/boundary_trace.py `
  lumibot/components/agents/litellm_trace.py `
  lumibot/components/agents/runtime.py `
  lumibot/components/agents/manager.py `
  lumibot/components/agents/schemas.py `
  lumibot/components/agents/replay_ui/redaction.py `
  tests/test_agent_boundary_trace.py `
  tests/test_agent_runtime_boundary_trace.py `
  tests/test_agent_litellm_trace.py `
  tests/test_agent_replay_ui_models.py `
  tests/test_agent_replay_ui_loader.py `
  tests/backtest/test_agent_runtime_backtest.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add tests/test_agent_boundary_trace.py CHANGELOG.md
git commit -m "docs: document complete agent boundary tracing"
```

---

### Task 13: Run the Complete Automated Verification

**Files:**
- No source changes expected.

- [ ] **Step 1: Run all focused boundary and replay suites**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_boundary_trace.py `
  tests/test_agent_runtime_boundary_trace.py `
  tests/test_agent_litellm_trace.py `
  tests/test_agent_replay_ui_models.py `
  tests/test_agent_replay_ui_loader.py `
  tests/test_agent_runtime_provider_keys.py `
  tests/test_agent_runtime_errors.py `
  tests/backtest/test_agent_runtime_backtest.py -v
```

Expected: PASS.

- [ ] **Step 2: Run the complete agent test subset**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent*.py `
  tests/backtest/test_agent_runtime_backtest.py -v
```

Expected: PASS.

- [ ] **Step 3: Run Ruff and diff hygiene**

```powershell
.\.venv\Scripts\python.exe -m ruff check `
  lumibot/components/agents `
  tests/test_agent_boundary_trace.py `
  tests/test_agent_runtime_boundary_trace.py `
  tests/test_agent_litellm_trace.py
git diff --check
git status --short
```

Expected:

- Ruff exits 0.
- `git diff --check` exits 0.
- `git status --short` contains no unexpected files.

- [ ] **Step 4: Review the final diff against the spec**

Verify explicitly:

- B01-B10 all have automated assertions.
- no test pairs same-name calls by name;
- no process-global LiteLLM callback is registered;
- no authorization header or API key is persisted;
- raw HTTP is not claimed;
- cache hits are labeled and do not execute tools;
- existing normalized trace fields remain;
- no UI rendering was added.

- [ ] **Step 5: Commit any test-only corrections**

Only when Step 1-4 required corrections:

```powershell
git add lumibot/components/agents/trace_redaction.py `
  lumibot/components/agents/boundary_trace.py `
  lumibot/components/agents/litellm_trace.py `
  lumibot/components/agents/runtime.py `
  lumibot/components/agents/manager.py `
  lumibot/components/agents/schemas.py `
  lumibot/components/agents/replay_ui/redaction.py `
  tests/test_agent_boundary_trace.py `
  tests/test_agent_runtime_boundary_trace.py `
  tests/test_agent_litellm_trace.py `
  tests/test_agent_replay_ui_models.py `
  tests/test_agent_replay_ui_loader.py `
  tests/backtest/test_agent_runtime_backtest.py
git commit -m "test: complete agent boundary trace verification"
```

Before committing, unstage any listed path that did not need a correction with
`git restore --staged path/to/file`. Do not include unrelated changes and do
not create an empty commit.

---

### Task 14: Run One-Day Real OpenAI Backtest Acceptance

**Files:**
- Generated local artifacts only; do not commit credentials or artifact output.

- [ ] **Step 1: Confirm the key is supplied outside tracked files**

Load `OPENAI_API_KEY` into the current process from the developer's untracked
secret source. Do not print it and do not add the secret source path to docs or
Git.

Verify only presence:

```powershell
if (-not $env:OPENAI_API_KEY) { throw "OPENAI_API_KEY is not set" }
```

- [ ] **Step 2: Run the existing one-day benchmark command**

Use the repository's provider benchmark runner because it accepts an explicit
OpenAI model and validates the matching provider key. Capture the directory
list before execution so the acceptance script can identify exactly the new
run rather than guessing from a timestamp:

```powershell
$benchmarkRoot = Join-Path (Get-Location) "artifacts\ai_trading_team_provider_benchmarks"
$beforeRuns = @(
  if (Test-Path -LiteralPath $benchmarkRoot) {
    Get-ChildItem -LiteralPath $benchmarkRoot -Directory |
      Select-Object -ExpandProperty FullName
  }
)
$env:LUMIBOT_ALLOW_PAID_AI_TRADING_TEAM_BACKTEST = "1"
.\.venv\Scripts\python.exe scripts\run_ai_trading_team_provider_benchmark.py `
  --models openai/gpt-5.4-mini `
  --start 2024-09-05 `
  --end 2024-09-06 `
  --budget 100000 `
  --max-run-attempts 1 `
  --agent-run-timeout-seconds 1800
$runRoot = Get-ChildItem -LiteralPath $benchmarkRoot -Directory |
  Where-Object { $_.FullName -notin $beforeRuns } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if ($null -eq $runRoot) { throw "The benchmark did not create a new run directory." }
$providerRoot = Join-Path $runRoot.FullName "openai_gpt-5.4-mini"
$traceRoot = Join-Path $providerRoot "cache\agent_runtime"
if (-not (Test-Path -LiteralPath $traceRoot)) {
  throw "Agent trace root was not generated: $traceRoot"
}
```

Expected: the benchmark exits 0, reports `status: passed`, and `$traceRoot`
points to the new run's `agent_runtime` directory. Do not change strategy
prompts or trading behavior for this acceptance.

- [ ] **Step 3: Validate the generated trace artifact**

Use a short read-only Python command against the generated trace root:

```powershell
@'
import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path

from lumibot.components.agents.replay_ui.loader import build_replay_dataset

trace_root = Path(sys.argv[1])
secret_pattern = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
trace_paths = sorted((trace_root / "traces").glob("*/*.json"))
assert trace_paths, f"No trace files found under {trace_root}"
all_events = []
sidecars = set()
for path in trace_paths:
    payload = json.loads(path.read_text(encoding="utf-8"))
    boundary = payload.get("boundary_trace") or {}
    events = boundary.get("events", [])
    all_events.extend(events)
    transitions = {event.get("transition") for event in events}
    print(path.name, boundary.get("agent_run_id"), sorted(transitions))
    assert {"B01_PROVIDER_TO_LITELLM", "B02_LITELLM_TO_ADK", "B09_ADK_TO_LITELLM", "B10_LITELLM_TO_PROVIDER"} <= transitions
    text = path.read_text(encoding="utf-8")
    assert secret_pattern.search(text) is None
    for event in events:
        relative = (event.get("payload_meta") or {}).get("sidecar_path")
        if relative:
            sidecar = (trace_root / relative).resolve()
            assert sidecar.is_relative_to(trace_root.resolve())
            assert sidecar.is_file()
            sidecars.add(sidecar)

transitions = {event.get("transition") for event in all_events}
assert {
    "B03_ADK_TO_FUNCTION_TOOL",
    "B04_FUNCTION_TOOL_TO_WRAPPER",
    "B05_WRAPPER_TO_PYTHON_TOOL",
    "B06_PYTHON_TOOL_TO_WRAPPER",
    "B07_WRAPPER_TO_FUNCTION_TOOL",
    "B08_FUNCTION_TOOL_TO_ADK",
} <= transitions
b03_call_ids = {
    (event.get("agent_run_id"), event.get("call_id"))
    for event in all_events
    if event.get("transition") == "B03_ADK_TO_FUNCTION_TOOL"
}
b08_events = [
    event
    for event in all_events
    if event.get("transition") == "B08_FUNCTION_TOOL_TO_ADK"
]
assert all(
    (event.get("agent_run_id"), event.get("call_id")) in b03_call_ids
    for event in b08_events
)
for response_event in b08_events:
    call_id = response_event.get("call_id")
    assert any(
        event.get("transition") == "B10_LITELLM_TO_PROVIDER"
        and event.get("agent_run_id") == response_event.get("agent_run_id")
        and event.get("sequence", 0) > response_event.get("sequence", 0)
        and call_id in json.dumps(event.get("payload"), sort_keys=True)
        for event in all_events
    )
batch_sizes = Counter(
    event.get("tool_batch_id")
    for event in all_events
    if event.get("transition") == "B03_ADK_TO_FUNCTION_TOOL"
    and event.get("tool_batch_id")
)
assert any(size > 1 for size in batch_sizes.values())
for sidecar in sidecars:
    with gzip.open(sidecar, "rt", encoding="utf-8") as handle:
        assert secret_pattern.search(handle.read()) is None
dataset = build_replay_dataset(trace_root)
assert dataset.runs, "Replay UI loader did not discover the new run"
assert not dataset.warnings, dataset.warnings
print(
    f"Replay loader discovered {len(dataset.runs)} backtest run(s); "
    f"{len(sidecars)} sidecar(s); transition counts: "
    f"{Counter(event.get('transition') for event in all_events)}"
)
'@ | .\.venv\Scripts\python.exe - $traceRoot
```

Expected: every trace lists the four model/provider transitions, B03-B08 form
a call-ID-correlated tool round trip, at least one parallel batch is present,
all sidecars stay inside the artifact root, and the replay loader discovers the
run without warnings.

- [ ] **Step 4: Validate a real tool round trip**

Review the printed transition counts and one complete call ID in the trace.
Confirm the B03 model arguments, B04 validated arguments, B05 effective Python
arguments, B06 raw semantic result, B07 serialized result, B08 ADK
FunctionResponse, and later B10 provider request match the same call. The
automated assertions in Step 3 are the gate; this review checks readability.

- [ ] **Step 5: Record verification results without secrets**

Report:

- benchmark run ID;
- strategy and simulated date;
- agent count;
- model-turn count;
- tool-batch count;
- B01-B10 transition counts;
- sidecar count;
- redaction diagnostic count;
- test and Ruff results.

Do not commit the generated trace unless the user explicitly requests a
sanitized fixture.

---

## Completion Gate

Implementation is ready for code review only when:

- Tasks 1-13 pass without a provider key;
- Task 14 proves the OpenAI/LiteLLM path with a one-day run;
- all B01-B10 events are present when applicable;
- call-ID correlation survives parallel same-name reversed completion;
- redaction occurs before persistence;
- legacy replay and cache tests pass;
- tracing failures remain fail-open;
- the worktree contains no credential, machine-specific path, generated
  backtest artifact, or unrelated change.
