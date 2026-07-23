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

    def current_tool_call(self) -> dict[str, Any] | None:
        return current_tool_call_context()

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
