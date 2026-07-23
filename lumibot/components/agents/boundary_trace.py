from __future__ import annotations

import collections.abc
import contextlib
import contextvars
import copy
import gzip
import hashlib
import inspect
import itertools
import json
import math
import os
import re
import sys
import tempfile
import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import (
    Path,
    PosixPath,
    PurePath,
    PurePosixPath,
    PureWindowsPath,
    WindowsPath,
)
from typing import Any, Iterator
from uuid import UUID, uuid4

from pydantic import BaseModel

from .trace_redaction import redact_sensitive

BOUNDARY_SCHEMA_VERSION = 1
DEFAULT_INLINE_PAYLOAD_LIMIT = 64_000
_BINARY_PREVIEW_BYTE_LIMIT = 4_096
_DESCRIPTOR_ONLY = object()
_SAFE_PATH_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_-]+")
_SAFE_SPAN_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])[\"']?(?:[A-Za-z]:[\\/]|\\\\)[^\r\n]*",
    re.IGNORECASE,
)
_POSIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_:/\.])[\"']?/[^\r\n]*",
    re.IGNORECASE,
)

_active_tool_call: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "lumibot_boundary_tool_call",
    default=None,
)

_TRUSTED_SHAPE_TYPE_EXPORTS = {
    ("numpy", "ndarray"): ("numpy", "ndarray"),
    ("pandas.core.frame", "DataFrame"): ("pandas", "DataFrame"),
    ("pandas.core.series", "Series"): ("pandas", "Series"),
}
_STANDARD_PATH_TYPES = (
    PosixPath,
    PurePosixPath,
    PureWindowsPath,
    WindowsPath,
)


def utc_iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def current_tool_call_context() -> dict[str, Any] | None:
    value = _active_tool_call.get()
    return dict(value) if isinstance(value, dict) else None


def _hook_free_mro(value_type: type) -> tuple[type, ...]:
    try:
        mro = type.__getattribute__(value_type, "__mro__")
    except Exception:
        return ()
    return mro if type(mro) is tuple else ()


def _type_mro_contains(value_type: type, base_type: type) -> bool:
    return any(candidate is base_type for candidate in _hook_free_mro(value_type))


def _type_is_one_of(value_type: type, candidates: tuple[type, ...]) -> bool:
    return any(value_type is candidate for candidate in candidates)


def _type_is_subclass(value_type: type, base_type: type) -> bool:
    if _type_mro_contains(value_type, base_type):
        return True
    try:
        return issubclass(value_type, base_type)
    except Exception:
        return False


def _is_mapping_type(value_type: type) -> bool:
    return _type_is_subclass(value_type, collections.abc.Mapping)


def _mapping_items(value: Any, value_type: type) -> Any:
    if _type_mro_contains(value_type, dict):
        return dict.items(value)
    return collections.abc.Mapping.items(value)


def _mapping_keys(value: Any, value_type: type) -> Any:
    if _type_mro_contains(value_type, dict):
        return dict.__iter__(value)
    return iter(value)


def _sequence_iterator(value: Any, value_type: type) -> Any:
    for sequence_type in (list, tuple):
        if _type_mro_contains(value_type, sequence_type):
            return sequence_type.__iter__(value)
    raise TypeError("unsupported sequence type")


def _set_iterator(value: Any, value_type: type) -> Any:
    for set_type in (set, frozenset):
        if _type_mro_contains(value_type, set_type):
            return set_type.__iter__(value)
    raise TypeError("unsupported set type")


def _container_length(value: Any, value_type: type) -> int:
    for container_type in (dict, list, tuple, set, frozenset):
        if _type_mro_contains(value_type, container_type):
            return container_type.__len__(value)
    return len(value)


def _semantic_trace_value(value: Any) -> Any:
    actual_type = type(value)
    if value is None or actual_type is bool:
        return value
    if _type_mro_contains(actual_type, str):
        return str.encode(value, errors="surrogatepass").decode(
            errors="surrogatepass"
        )
    if _type_mro_contains(actual_type, int):
        return int.__int__(value)
    if _type_mro_contains(actual_type, float):
        normalized_float = float.__float__(value)
        return normalized_float if math.isfinite(normalized_float) else None
    if _type_mro_contains(actual_type, datetime):
        return datetime.isoformat(value)
    if _type_mro_contains(actual_type, date):
        return date.isoformat(value)
    if _type_mro_contains(actual_type, Decimal):
        return Decimal.__str__(value)
    if _type_mro_contains(actual_type, UUID):
        return UUID.__str__(value)
    if _type_is_one_of(actual_type, _STANDARD_PATH_TYPES):
        return _portable_path_value(value)
    if _type_is_subclass(actual_type, Enum):
        try:
            enum_value = object.__getattribute__(value, "_value_")
        except Exception:
            return _DESCRIPTOR_ONLY
        return _semantic_trace_value(enum_value)
    if _type_is_subclass(actual_type, collections.abc.Iterator):
        return _DESCRIPTOR_ONLY
    if _is_mapping_type(actual_type):
        normalized_mapping = {}
        try:
            for key, item in _mapping_items(value, actual_type):
                normalized_item = _semantic_trace_value(item)
                if normalized_item is _DESCRIPTOR_ONLY:
                    return _DESCRIPTOR_ONLY
                normalized_key, key_is_descriptor = _safe_mapping_key(key)
                if key_is_descriptor:
                    return _DESCRIPTOR_ONLY
                normalized_mapping[normalized_key] = normalized_item
        except Exception:
            return _DESCRIPTOR_ONLY
        return normalized_mapping
    if any(
        _type_mro_contains(actual_type, sequence_type)
        for sequence_type in (list, tuple)
    ):
        normalized_sequence = []
        try:
            for item in _sequence_iterator(value, actual_type):
                normalized_item = _semantic_trace_value(item)
                if normalized_item is _DESCRIPTOR_ONLY:
                    return _DESCRIPTOR_ONLY
                normalized_sequence.append(normalized_item)
        except Exception:
            return _DESCRIPTOR_ONLY
        return normalized_sequence
    if any(
        _type_mro_contains(actual_type, set_type)
        for set_type in (set, frozenset)
    ):
        normalized_set = []
        try:
            for item in _set_iterator(value, actual_type):
                normalized_item = _semantic_trace_value(item)
                if normalized_item is _DESCRIPTOR_ONLY:
                    return _DESCRIPTOR_ONLY
                normalized_set.append(normalized_item)
        except Exception:
            return _DESCRIPTOR_ONLY
        return sorted(
            normalized_set,
            key=lambda item: json.dumps(item, sort_keys=True),
        )
    if _type_is_subclass(actual_type, BaseModel):
        try:
            return _semantic_trace_value(
                BaseModel.model_dump(value, mode="json")
            )
        except Exception:
            return _DESCRIPTOR_ONLY
    return _DESCRIPTOR_ONLY


def _redact_trace_value(value: Any) -> Any:
    return _scrub_trace_paths(_redact_trace_keys(redact_sensitive(value)))


def _redact_trace_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            redact_sensitive(str(key)): _redact_trace_keys(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_trace_keys(item) for item in value]
    return value


def _scrub_absolute_paths(value: str) -> str:
    scrubbed = _WINDOWS_ABSOLUTE_PATH_RE.sub("[ABSOLUTE_PATH]", value)
    return _POSIX_ABSOLUTE_PATH_RE.sub("[ABSOLUTE_PATH]", scrubbed)


def _scrub_trace_paths(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _scrub_absolute_paths(str(key)): _scrub_trace_paths(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub_trace_paths(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub_trace_paths(item) for item in value)
    if isinstance(value, str):
        return _scrub_absolute_paths(value)
    return value


def _is_trace_descriptor(value: Any) -> bool:
    if type(value) is not dict:
        return False
    fidelity = dict.get(value, "fidelity")
    return (
        type(fidelity) is str
        and fidelity in ("semantic_copy", "descriptor_only")
        and type(dict.get(value, "python_type")) is str
        and type(dict.get(value, "qualified_type")) is str
    )


def _bound_descriptor_previews(value: Any) -> Any:
    if type(value) is dict:
        is_descriptor = _is_trace_descriptor(value)
        return {
            key: (
                item[:500]
                if is_descriptor
                and key == "preview"
                and type(item) is str
                else _bound_descriptor_previews(item)
            )
            for key, item in value.items()
        }
    if type(value) is list:
        return [_bound_descriptor_previews(item) for item in value]
    return value


def _contains_redacted_descriptor(value: Any) -> bool:
    if type(value) is dict:
        if _is_trace_descriptor(value) and value.get("redacted") is True:
            return True
        return any(_contains_redacted_descriptor(item) for item in value.values())
    if type(value) is list:
        return any(_contains_redacted_descriptor(item) for item in value)
    return False


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, default=str).encode("utf-8")


def _hook_free_type_identity(
    value_type: type,
) -> tuple[str, str, str] | None:
    try:
        python_type = type.__getattribute__(value_type, "__name__")
        module = type.__getattribute__(value_type, "__module__")
        qualname = type.__getattribute__(value_type, "__qualname__")
        if not all(
            type(item) is str
            for item in (python_type, module, qualname)
        ):
            raise TypeError("type identity is not string-valued")
        return python_type, module, qualname
    except Exception:
        return None


def _descriptor_type(value: Any) -> dict[str, Any]:
    identity = _hook_free_type_identity(type(value))
    if identity is None:
        python_type = "unknown"
        qualified_type = "unknown"
    else:
        python_type, module, qualname = identity
        qualified_type = f"{module}.{qualname}"
    return {
        "fidelity": "descriptor_only",
        "python_type": python_type,
        "qualified_type": qualified_type,
    }


def _type_based_preview(descriptor: dict[str, Any]) -> str:
    return f"<{descriptor['qualified_type']} instance>"


def _safe_static_shape(value: Any) -> list[int | None] | None:
    try:
        shape = inspect.getattr_static(value, "shape")
    except Exception:
        return None
    return _safe_shape_tuple(shape)


def _safe_shape_tuple(shape: Any) -> list[int | None] | None:
    if type(shape) is not tuple or not all(
        dimension is None or type(dimension) is int
        for dimension in shape
    ):
        return None
    return list(shape)


def _is_trusted_shape_type(value_type: type) -> bool:
    identity = _hook_free_type_identity(value_type)
    if identity is None:
        return False
    python_type, module, qualname = identity
    export = _TRUSTED_SHAPE_TYPE_EXPORTS.get(
        (module, qualname)
    )
    if export is None or python_type != qualname:
        return False
    module_name, export_name = export
    loaded_module = sys.modules.get(module_name)
    if loaded_module is None:
        return False
    return vars(loaded_module).get(export_name) is value_type


def _safe_shape(value: Any, value_type: type) -> list[int | None] | None:
    static_shape = _safe_static_shape(value)
    if static_shape is not None:
        return static_shape
    if not _is_trusted_shape_type(value_type):
        return None
    try:
        return _safe_shape_tuple(
            object.__getattribute__(value, "shape")
        )
    except Exception:
        return None


def _safe_mapping_key(value: Any) -> tuple[str, bool]:
    actual_type = type(value)
    if _type_is_one_of(
        actual_type,
        (str, int, float, bool, type(None), Decimal, UUID),
    ):
        return str(value), False
    descriptor = _descriptor_type(value)
    return f"<{descriptor['python_type']}>", True


def _portable_path_value(value: PurePath) -> dict[str, Any]:
    if value.is_absolute():
        return {
            "kind": "absolute_path",
            "name": value.name or None,
        }
    return {
        "kind": "relative_path",
        "value": value.as_posix(),
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
                return self._store_event(event)
        except Exception as exc:
            if sidecar_path is not None:
                self._remove_sidecar(sidecar_path)
            self.add_diagnostic("record_failed", exc)
            return {}

    def _store_event(self, event: dict[str, Any]) -> dict[str, Any]:
        stored_event = copy.deepcopy(event)
        returned_event = copy.deepcopy(event)
        self._events.append(stored_event)
        return returned_event

    def _descriptor_only_value(
        self,
        value: Any,
        *,
        include_safe_preview: bool,
    ) -> dict[str, Any]:
        actual_type = type(value)
        descriptor = _descriptor_type(value)
        if _type_is_subclass(actual_type, collections.abc.Iterator):
            descriptor["one_shot"] = True
            return descriptor
        shape = _safe_shape(value, actual_type)
        if shape is not None:
            descriptor["shape"] = shape
        if _is_mapping_type(actual_type):
            try:
                descriptor["keys"] = [
                    _safe_mapping_key(key)[0]
                    for key in itertools.islice(
                        _mapping_keys(value, actual_type),
                        100,
                    )
                ]
            except Exception:
                pass
            try:
                descriptor["length"] = _container_length(value, actual_type)
            except Exception:
                pass
            return descriptor
        if any(
            _type_mro_contains(actual_type, container_type)
            for container_type in (list, tuple, set, frozenset)
        ):
            try:
                descriptor["length"] = _container_length(value, actual_type)
            except Exception:
                pass
            return descriptor
        if include_safe_preview and _type_is_one_of(
            actual_type,
            (bytes, bytearray),
        ):
            descriptor["length"] = len(value)
            if actual_type is bytes:
                preview_value = bytes.__getitem__(
                    value,
                    slice(0, _BINARY_PREVIEW_BYTE_LIMIT),
                )
            else:
                preview_value = bytearray.__getitem__(
                    value,
                    slice(0, _BINARY_PREVIEW_BYTE_LIMIT),
                )
            descriptor["preview"] = repr(preview_value)
        elif include_safe_preview:
            descriptor["preview"] = _type_based_preview(descriptor)
        return descriptor

    def describe_raw_value(self, value: Any) -> dict[str, Any]:
        fallback = _descriptor_type(value)
        fallback["preview"] = _type_based_preview(fallback)
        try:
            identity = {
                "python_type": fallback["python_type"],
                "qualified_type": fallback["qualified_type"],
            }
            semantic_value = _semantic_trace_value(value)
            if semantic_value is not _DESCRIPTOR_ONLY:
                descriptor = {
                    **identity,
                    "fidelity": "semantic_copy",
                    "semantic_value": semantic_value,
                }
            else:
                descriptor = self._descriptor_only_value(
                    value,
                    include_safe_preview=True,
                )
            sanitized = _redact_trace_value(descriptor)
            if sanitized != descriptor:
                sanitized["redacted"] = True
            return _bound_descriptor_previews(sanitized)
        except Exception:
            return fallback

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

    def _write_sidecar(self, *, span_id: str, encoded: bytes) -> str:
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
                    compressed.write(encoded)
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
            self.add_diagnostic(
                "sidecar_cleanup_failed",
                "orphan sidecar cleanup failed",
            )

    def _snapshot_trace_value(
        self,
        value: Any,
        *,
        nested: bool,
    ) -> tuple[Any, bool]:
        actual_type = type(value)
        if value is None or actual_type is bool:
            return value, False
        if _type_mro_contains(actual_type, str):
            normalized_string = str.encode(
                value,
                errors="surrogatepass",
            ).decode(errors="surrogatepass")
            return normalized_string, False
        if _type_mro_contains(actual_type, int):
            return int.__int__(value), False
        if _type_mro_contains(actual_type, float):
            normalized_float = float.__float__(value)
            return (
                normalized_float if math.isfinite(normalized_float) else None
            ), False
        if _type_mro_contains(actual_type, datetime):
            return datetime.isoformat(value), False
        if _type_mro_contains(actual_type, date):
            return date.isoformat(value), False
        if _type_mro_contains(actual_type, Decimal):
            return Decimal.__str__(value), False
        if _type_mro_contains(actual_type, UUID):
            return UUID.__str__(value), False
        if _type_is_one_of(actual_type, _STANDARD_PATH_TYPES):
            return _portable_path_value(value), False
        if _type_is_subclass(actual_type, Enum):
            try:
                enum_value = object.__getattribute__(value, "_value_")
            except Exception:
                enum_value = _DESCRIPTOR_ONLY
            if enum_value is not _DESCRIPTOR_ONLY:
                return self._snapshot_trace_value(
                    enum_value,
                    nested=nested,
                )
        if _type_is_subclass(actual_type, collections.abc.Iterator):
            descriptor = _descriptor_type(value)
            descriptor["one_shot"] = True
            return descriptor, True
        if _is_mapping_type(actual_type):
            normalized_mapping = {}
            descriptor_only = False
            try:
                for key, item in _mapping_items(value, actual_type):
                    (
                        normalized_item,
                        item_is_descriptor,
                    ) = self._snapshot_trace_value(
                        item,
                        nested=True,
                    )
                    normalized_key, key_is_descriptor = _safe_mapping_key(key)
                    normalized_mapping[normalized_key] = normalized_item
                    descriptor_only = (
                        descriptor_only
                        or item_is_descriptor
                        or key_is_descriptor
                    )
                return normalized_mapping, descriptor_only
            except Exception:
                pass
        if any(
            _type_mro_contains(actual_type, sequence_type)
            for sequence_type in (list, tuple)
        ):
            normalized_sequence = []
            descriptor_only = False
            try:
                for item in _sequence_iterator(value, actual_type):
                    (
                        normalized_item,
                        item_is_descriptor,
                    ) = self._snapshot_trace_value(
                        item,
                        nested=True,
                    )
                    normalized_sequence.append(normalized_item)
                    descriptor_only = descriptor_only or item_is_descriptor
                return normalized_sequence, descriptor_only
            except Exception:
                pass
        if any(
            _type_mro_contains(actual_type, set_type)
            for set_type in (set, frozenset)
        ):
            normalized_set = []
            descriptor_only = False
            try:
                for item in _set_iterator(value, actual_type):
                    (
                        normalized_item,
                        item_is_descriptor,
                    ) = self._snapshot_trace_value(
                        item,
                        nested=True,
                    )
                    normalized_set.append(normalized_item)
                    descriptor_only = descriptor_only or item_is_descriptor
                return (
                    sorted(normalized_set, key=_canonical_json_bytes),
                    descriptor_only,
                )
            except Exception:
                pass

        if _type_is_subclass(actual_type, BaseModel):
            try:
                return self._snapshot_trace_value(
                    BaseModel.model_dump(value, mode="json"),
                    nested=nested,
                )
            except Exception:
                pass
        if nested:
            return (
                self._descriptor_only_value(
                    value,
                    include_safe_preview=True,
                ),
                True,
            )
        return (
            self._descriptor_only_value(
                value,
                include_safe_preview=True,
            ),
            True,
        )

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
        has_redacted_descriptor = _contains_redacted_descriptor(normalized)
        sanitized = _redact_trace_value(normalized)
        was_redacted = sanitized != normalized or has_redacted_descriptor
        redacted = _bound_descriptor_previews(sanitized)
        encoded = _canonical_json_bytes(redacted)
        meta = {
            "fidelity": (
                "descriptor_only" if descriptor_only else "normalized_copy"
            ),
            "representation": "json",
            "byte_count": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "redacted": was_redacted,
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
                encoded=encoded,
            )
            meta["compression"] = "gzip"
            return preview, meta
        except Exception as exc:
            self.add_diagnostic("sidecar_write_failed", exc)
            meta["truncated"] = True
            return preview, meta

    def add_diagnostic(self, kind: str, exc: BaseException | str) -> None:
        try:
            safe_message = _scrub_absolute_paths(
                redact_sensitive(str(exc))
            )
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
