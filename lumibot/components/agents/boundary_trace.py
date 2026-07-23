from __future__ import annotations

import collections.abc
import contextlib
import contextvars
import copy
import gzip
import hashlib
import itertools
import json
import math
import os
import re
import tempfile
import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID, uuid4

from .trace_redaction import redact_sensitive

BOUNDARY_SCHEMA_VERSION = 1
DEFAULT_INLINE_PAYLOAD_LIMIT = 64_000
_DESCRIPTOR_ONLY = object()
_SAFE_PATH_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_-]+")
_SAFE_SPAN_ID_RE = re.compile(r"[A-Za-z0-9_-]+")

_active_tool_call: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "lumibot_boundary_tool_call",
    default=None,
)


def utc_iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def current_tool_call_context() -> dict[str, Any] | None:
    value = _active_tool_call.get()
    return dict(value) if isinstance(value, dict) else None


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
    if isinstance(value, collections.abc.Iterator):
        return _DESCRIPTOR_ONLY
    if isinstance(value, collections.abc.Mapping):
        normalized_mapping = {}
        for key, item in value.items():
            normalized_item = _semantic_trace_value(item)
            if normalized_item is _DESCRIPTOR_ONLY:
                return _DESCRIPTOR_ONLY
            normalized_mapping[str(key)] = normalized_item
        return normalized_mapping
    if isinstance(value, (list, tuple)):
        normalized_sequence = []
        for item in value:
            normalized_item = _semantic_trace_value(item)
            if normalized_item is _DESCRIPTOR_ONLY:
                return _DESCRIPTOR_ONLY
            normalized_sequence.append(normalized_item)
        return normalized_sequence
    if isinstance(value, (set, frozenset)):
        normalized_set = []
        for item in value:
            normalized_item = _semantic_trace_value(item)
            if normalized_item is _DESCRIPTOR_ONLY:
                return _DESCRIPTOR_ONLY
            normalized_set.append(normalized_item)
        return sorted(
            normalized_set,
            key=lambda item: json.dumps(item, sort_keys=True),
        )
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _semantic_trace_value(model_dump(mode="json"))
        except Exception:
            return _DESCRIPTOR_ONLY
    return _DESCRIPTOR_ONLY


def _redact_trace_value(value: Any) -> Any:
    return _redact_trace_keys(redact_sensitive(value))


def _redact_trace_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            redact_sensitive(str(key)): _redact_trace_keys(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_trace_keys(item) for item in value]
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, default=str).encode("utf-8")


def _descriptor_type(value: Any) -> dict[str, Any]:
    value_type = type(value)
    return {
        "fidelity": "descriptor_only",
        "python_type": value_type.__name__,
        "qualified_type": f"{value_type.__module__}.{value_type.__qualname__}",
    }


def _safe_agent_run_segment(agent_run_id: str) -> str:
    raw_value = str(agent_run_id)
    sanitized = _SAFE_PATH_SEGMENT_RE.sub("-", raw_value).strip("-_")[:48]
    digest = hashlib.sha256(
        raw_value.encode("utf-8", errors="surrogatepass")
    ).hexdigest()[:16]
    return f"{sanitized or 'run'}-{digest}"


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
        adk_invocation_id: str | None = None,
        model_turn_id: str | None = None,
        tool_batch_id: str | None = None,
        call_id: str | None = None,
        parent_span_id: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        duration_ms: float | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sidecar_path: str | None = None
        try:
            normalized_error, _ = self._snapshot_trace_value(error, nested=False)
            safe_error = _redact_trace_value(normalized_error)
            span_id = uuid4().hex
            safe_payload, payload_meta = self.snapshot(payload, span_id=span_id)
            sidecar_path = payload_meta.get("sidecar_path")
            with self._lock:
                event = {
                    "schema_version": BOUNDARY_SCHEMA_VERSION,
                    "sequence": next(self._sequence),
                    "transition": transition,
                    "from_module": from_module,
                    "to_module": to_module,
                    "status": status,
                    "agent_run_id": self.agent_run_id,
                    "adk_invocation_id": adk_invocation_id,
                    "model_turn_id": model_turn_id,
                    "tool_batch_id": tool_batch_id,
                    "call_id": call_id,
                    "span_id": span_id,
                    "parent_span_id": parent_span_id,
                    "started_at": started_at,
                    "ended_at": ended_at or utc_iso_timestamp(),
                    "duration_ms": duration_ms,
                    "payload": safe_payload,
                    "payload_meta": payload_meta,
                    "error": safe_error,
                }
                stored_event = copy.deepcopy(event)
                returned_event = copy.deepcopy(event)
                self._events.append(stored_event)
                return returned_event
        except Exception as exc:
            if sidecar_path is not None:
                self._remove_sidecar(sidecar_path)
            self.add_diagnostic("record_failed", exc)
            return {}

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
        if isinstance(value, collections.abc.Iterator):
            descriptor["one_shot"] = True
            return descriptor
        elif isinstance(value, collections.abc.Mapping):
            descriptor["keys"] = [str(key) for key in list(value.keys())[:100]]
            descriptor["length"] = len(value)
            return descriptor
        elif isinstance(value, (list, tuple, set, frozenset)):
            descriptor["length"] = len(value)
            return descriptor
        shape = getattr(value, "shape", None)
        if isinstance(shape, tuple):
            descriptor["shape"] = list(shape)
        descriptor["preview"] = redact_sensitive(repr(value))[:500]
        return descriptor

    def _resolve_sidecar_target(self, relative: Path) -> Path:
        if self.artifact_root is None:
            raise OSError("artifact root is unavailable")
        if relative.is_absolute() or ".." in relative.parts:
            raise OSError("sidecar path must be relative and contained")

        artifact_root = self.artifact_root.resolve()
        boundary_root = (artifact_root / "boundary_payloads").resolve()
        target = (artifact_root / relative).resolve()
        try:
            boundary_root.relative_to(artifact_root)
            target.relative_to(boundary_root)
        except ValueError as exc:
            raise OSError("sidecar path escapes artifact root") from exc
        return target

    def _write_sidecar(self, *, span_id: str, payload: Any) -> str:
        if _SAFE_SPAN_ID_RE.fullmatch(span_id) is None:
            raise OSError("span id is not a safe path component")
        relative = (
            Path("boundary_payloads")
            / _safe_agent_run_segment(self.agent_run_id)
            / f"{span_id}.json.gz"
        )
        target = self._resolve_sidecar_target(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{span_id}-",
            suffix=".tmp",
            dir=target.parent,
        )
        try:
            with os.fdopen(fd, "wb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb") as compressed:
                    compressed.write(_canonical_json_bytes(payload))
            os.replace(temp_name, target)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(temp_name)
            raise
        return relative.as_posix()

    def _remove_sidecar(self, relative_path: str) -> None:
        try:
            target = self._resolve_sidecar_target(Path(relative_path))
            target.unlink(missing_ok=True)
        except OSError:
            return

    def _snapshot_trace_value(
        self,
        value: Any,
        *,
        nested: bool,
    ) -> tuple[Any, bool]:
        if value is None or isinstance(value, (bool, str, int)):
            return value, False
        if isinstance(value, float):
            return (value if math.isfinite(value) else None), False
        if isinstance(value, (datetime, date)):
            return value.isoformat(), False
        if isinstance(value, (Decimal, UUID)):
            return str(value), False
        if isinstance(value, Enum):
            return self._snapshot_trace_value(value.value, nested=nested)
        if isinstance(value, collections.abc.Iterator):
            descriptor = _descriptor_type(value)
            descriptor["one_shot"] = True
            return descriptor, True
        if isinstance(value, collections.abc.Mapping):
            normalized_mapping = {}
            descriptor_only = False
            for key, item in value.items():
                normalized_item, item_is_descriptor = self._snapshot_trace_value(
                    item,
                    nested=True,
                )
                normalized_mapping[str(key)] = normalized_item
                descriptor_only = descriptor_only or item_is_descriptor
            return normalized_mapping, descriptor_only
        if isinstance(value, (list, tuple)):
            normalized_sequence = []
            descriptor_only = False
            for item in value:
                normalized_item, item_is_descriptor = self._snapshot_trace_value(
                    item,
                    nested=True,
                )
                normalized_sequence.append(normalized_item)
                descriptor_only = descriptor_only or item_is_descriptor
            return normalized_sequence, descriptor_only
        if isinstance(value, (set, frozenset)):
            normalized_set = []
            descriptor_only = False
            for item in value:
                normalized_item, item_is_descriptor = self._snapshot_trace_value(
                    item,
                    nested=True,
                )
                normalized_set.append(normalized_item)
                descriptor_only = descriptor_only or item_is_descriptor
            return (
                sorted(normalized_set, key=_canonical_json_bytes),
                descriptor_only,
            )

        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                return self._snapshot_trace_value(
                    model_dump(mode="json"),
                    nested=nested,
                )
            except Exception:
                pass
        if nested:
            return _descriptor_type(value), True
        return self.describe_raw_value(value), True

    def snapshot(
        self,
        payload: Any,
        *,
        span_id: str,
    ) -> tuple[Any, dict[str, Any]]:
        normalized, descriptor_only = self._snapshot_trace_value(
            payload,
            nested=False,
        )
        redacted = _redact_trace_value(normalized)
        encoded = _canonical_json_bytes(redacted)
        meta = {
            "fidelity": (
                "descriptor_only" if descriptor_only else "normalized_copy"
            ),
            "representation": "json",
            "byte_count": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "redacted": redacted != normalized,
            "pruned": False,
            "truncated": False,
            "sidecar_path": None,
            "compression": None,
        }
        if len(encoded) <= self.inline_payload_limit:
            return redacted, meta
        preview = {"preview": encoded[:500].decode("utf-8", errors="replace")}
        try:
            meta["sidecar_path"] = self._write_sidecar(
                span_id=span_id,
                payload=redacted,
            )
            meta["compression"] = "gzip"
            return preview, meta
        except Exception as exc:
            self.add_diagnostic("sidecar_write_failed", exc)
            meta["truncated"] = True
            return preview, meta

    def add_diagnostic(self, kind: str, exc: BaseException | str) -> None:
        try:
            safe_message = redact_sensitive(str(exc))
        except Exception:
            safe_message = "[diagnostic message unavailable: redaction failed]"
        try:
            with self._lock:
                self._diagnostics.append(
                    {
                        "kind": kind,
                        "message": safe_message,
                        "timestamp": utc_iso_timestamp(),
                    }
                )
        except Exception:
            return

    def export(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": BOUNDARY_SCHEMA_VERSION,
                "agent_run_id": self.agent_run_id,
                "capture_scope": "semantic_boundaries",
                "provider_wire_capture": False,
                "events": copy.deepcopy(self._events),
                "diagnostics": copy.deepcopy(self._diagnostics),
            }
