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
import time
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
_SAFE_NUMERIC_USAGE_FIELDS = {
    "budget_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "cached_content_token_count",
    "cached_input_tokens",
    "cached_prompt_tokens",
    "cached_tokens",
    "candidates_token_count",
    "completion_tokens",
    "input_tokens",
    "max_completion_tokens",
    "max_output_tokens",
    "max_tokens",
    "output_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
    "prompt_token_count",
    "prompt_tokens",
    "reasoning_tokens",
    "thoughts_token_count",
    "tool_use_prompt_token_count",
    "total_token_count",
    "total_tokens",
}
class _TraceDescriptor(dict):
    pass


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
        normalized_mapping = (
            _TraceDescriptor()
            if actual_type is _TraceDescriptor
            else {}
        )
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
    redacted = redact_sensitive(value)
    redacted = _restore_safe_numeric_usage_fields(value, redacted)
    with_descriptors = _restore_trace_descriptors(value, redacted)
    return _scrub_trace_paths(_redact_trace_keys(with_descriptors))


def _restore_safe_numeric_usage_fields(source: Any, transformed: Any) -> Any:
    if type(source) in (dict, _TraceDescriptor) and type(transformed) is dict:
        restored = dict(transformed)
        for key, item in dict.items(source):
            transformed_item = restored.get(key)
            if (
                key in _SAFE_NUMERIC_USAGE_FIELDS
                and type(item) in (int, float)
            ):
                restored[key] = item
            elif key in restored:
                restored[key] = _restore_safe_numeric_usage_fields(
                    item,
                    transformed_item,
                )
        return (
            _TraceDescriptor(restored)
            if type(source) is _TraceDescriptor
            else restored
        )
    if type(source) is list and type(transformed) is list:
        return [
            _restore_safe_numeric_usage_fields(source_item, transformed_item)
            for source_item, transformed_item in zip(
                source,
                transformed,
                strict=True,
            )
        ]
    return transformed


def _restore_trace_descriptors(source: Any, transformed: Any) -> Any:
    source_type = type(source)
    if (
        source_type in (dict, _TraceDescriptor)
        and type(transformed) is dict
    ):
        restored = _TraceDescriptor() if source_type is _TraceDescriptor else {}
        for key, item in transformed.items():
            if dict.__contains__(source, key):
                item = _restore_trace_descriptors(
                    dict.__getitem__(source, key),
                    item,
                )
            restored[key] = item
        return restored
    if source_type is list and type(transformed) is list:
        return [
            _restore_trace_descriptors(source_item, transformed_item)
            for source_item, transformed_item in zip(
                source,
                transformed,
                strict=True,
            )
        ]
    if source_type is tuple and type(transformed) is tuple:
        return tuple(
            _restore_trace_descriptors(source_item, transformed_item)
            for source_item, transformed_item in zip(
                source,
                transformed,
                strict=True,
            )
        )
    return transformed


def _redact_trace_keys(value: Any) -> Any:
    value_type = type(value)
    if value_type in (dict, _TraceDescriptor):
        redacted = {
            redact_sensitive(str(key)): _redact_trace_keys(item)
            for key, item in value.items()
        }
        return _TraceDescriptor(redacted) if value_type is _TraceDescriptor else redacted
    if type(value) is list:
        return [_redact_trace_keys(item) for item in value]
    return value


def _scrub_absolute_paths(value: str) -> str:
    scrubbed = _WINDOWS_ABSOLUTE_PATH_RE.sub("[ABSOLUTE_PATH]", value)
    return _POSIX_ABSOLUTE_PATH_RE.sub("[ABSOLUTE_PATH]", scrubbed)


def _scrub_trace_paths(value: Any) -> Any:
    value_type = type(value)
    if value_type in (dict, _TraceDescriptor):
        scrubbed = {
            _scrub_absolute_paths(str(key)): _scrub_trace_paths(item)
            for key, item in value.items()
        }
        return _TraceDescriptor(scrubbed) if value_type is _TraceDescriptor else scrubbed
    if type(value) is list:
        return [_scrub_trace_paths(item) for item in value]
    if type(value) is tuple:
        return tuple(_scrub_trace_paths(item) for item in value)
    if type(value) is str:
        return _scrub_absolute_paths(value)
    return value


def _is_trace_descriptor(value: Any) -> bool:
    return type(value) is _TraceDescriptor


def _bound_descriptor_previews(value: Any) -> Any:
    value_type = type(value)
    if value_type in (dict, _TraceDescriptor):
        is_descriptor = _is_trace_descriptor(value)
        bounded = {
            key: (
                item[:500]
                if is_descriptor
                and key == "preview"
                and type(item) is str
                else _bound_descriptor_previews(item)
            )
            for key, item in value.items()
        }
        return _TraceDescriptor(bounded) if is_descriptor else bounded
    if type(value) is list:
        return [_bound_descriptor_previews(item) for item in value]
    return value


def _contains_redacted_descriptor(value: Any) -> bool:
    if type(value) in (dict, _TraceDescriptor):
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
    return _TraceDescriptor(
        {
            "fidelity": "descriptor_only",
            "python_type": python_type,
            "qualified_type": qualified_type,
        }
    )


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
        self._batch_completion_number: dict[str, int] = {}
        self._call_index: dict[str, dict[str, Any]] = {}
        self._call_alias_index: dict[str, list[str]] = {}
        self._batch_call_instances: dict[str, list[str]] = {}
        self._observation_index: dict[str, dict[str, Any]] = {}
        self._pending_model_turns: dict[str, dict[str, Any]] = {}
        self._active_model_turn_id: str | None = None
        self._active_model_turn_context: contextvars.ContextVar[
            str | None
        ] = contextvars.ContextVar(
            f"lumibot_boundary_model_turn_{id(self)}",
            default=None,
        )
        self._active_call_instance_context: contextvars.ContextVar[
            str | None
        ] = contextvars.ContextVar(
            f"lumibot_boundary_call_instance_{id(self)}",
            default=None,
        )
        self._completion_number = 0
        self._lock = threading.Lock()

    def start_model_turn(self) -> str:
        with self._lock:
            self._turn_number += 1
            return f"{self.agent_run_id}:turn:{self._turn_number:04d}"

    def set_active_model_turn(self, model_turn_id: str) -> None:
        self._active_model_turn_context.set(model_turn_id)
        with self._lock:
            self._active_model_turn_id = model_turn_id

    def active_model_turn(self) -> str | None:
        contextual_turn = self._active_model_turn_context.get()
        if contextual_turn is not None:
            return contextual_turn
        with self._lock:
            return self._active_model_turn_id

    def note_pending_model_turn(
        self,
        model_turn_id: str,
        *,
        previous_model_turn_id: str | None,
        context_pruning: dict[str, Any],
        adk_invocation_id: str | None = None,
    ) -> None:
        safe_pruning = self._safe_observation_value(
            context_pruning,
            snapshot_diagnostic="model_turn_pruning_snapshot_failed",
            normalization_diagnostic=(
                "model_turn_pruning_normalization_failed"
            ),
        )
        with self._lock:
            self._pending_model_turns[model_turn_id] = {
                "model_turn_id": model_turn_id,
                "previous_model_turn_id": previous_model_turn_id,
                "adk_invocation_id": adk_invocation_id,
                "context_pruning": (
                    safe_pruning
                    if type(safe_pruning) is dict
                    else {}
                ),
            }

    def pending_model_turn(
        self,
        model_turn_id: str,
    ) -> dict[str, Any]:
        snapshot_error: Exception | None = None
        with self._lock:
            try:
                state = copy.deepcopy(
                    self._pending_model_turns.get(model_turn_id)
                    or {}
                )
            except Exception as exc:
                snapshot_error = exc
                state = {}
        if snapshot_error is not None:
            self._try_add_diagnostic(
                "pending_model_turn_locked_snapshot_failed",
                snapshot_error,
            )
        return self._detached_state(
            state,
            snapshot_diagnostic="pending_model_turn_snapshot_failed",
            normalization_diagnostic=(
                "pending_model_turn_normalization_failed"
            ),
        )

    def take_pending_model_turn(
        self,
        model_turn_id: str,
    ) -> dict[str, Any]:
        snapshot_error: Exception | None = None
        with self._lock:
            pending = self._pending_model_turns.pop(
                model_turn_id,
                None,
            ) or {}
            try:
                state = copy.deepcopy(pending)
            except Exception as exc:
                snapshot_error = exc
                state = {}
        if snapshot_error is not None:
            self._try_add_diagnostic(
                "pending_model_turn_take_locked_snapshot_failed",
                snapshot_error,
            )
        return self._detached_state(
            state,
            snapshot_diagnostic=(
                "pending_model_turn_take_snapshot_failed"
            ),
            normalization_diagnostic=(
                "pending_model_turn_take_normalization_failed"
            ),
        )

    def pending_model_turn_count(self) -> int:
        with self._lock:
            return len(self._pending_model_turns)

    def _append_call_alias_locked(
        self,
        alias: str | None,
        call_instance_id: str,
    ) -> None:
        if not isinstance(alias, str) or not alias:
            return
        instances = self._call_alias_index.setdefault(alias, [])
        if call_instance_id not in instances:
            instances.append(call_instance_id)

    def _instance_ids_for_identifier_locked(
        self,
        identifier: str,
    ) -> list[str]:
        if identifier in self._call_index:
            return [identifier]
        return list(self._call_alias_index.get(identifier) or [])

    def register_tool_calls(
        self,
        model_turn_id: str,
        calls: list[dict[str, Any]],
    ) -> dict[str, Any]:
        duplicate_provider_ids: list[str] = []
        snapshot_error: Exception | None = None
        with self._lock:
            number = self._batch_number_by_turn.get(
                model_turn_id,
                0,
            ) + 1
            self._batch_number_by_turn[model_turn_id] = number
            batch_id = f"{model_turn_id}:batch:{number:04d}"
            call_instance_ids: list[str] = []
            for index, call in enumerate(calls, start=1):
                call_instance_id = (
                    f"{batch_id}:call:{index:04d}"
                )
                trace_call_id = str(
                    call.get("trace_call_id")
                    or call.get("call_id")
                    or call_instance_id
                )
                provider_call_id = call.get("provider_call_id")
                provider_runtime_call_id = call.get(
                    "provider_runtime_call_id"
                )
                state = {
                    "model_turn_id": model_turn_id,
                    "tool_batch_id": batch_id,
                    "call_sequence": index,
                    "call_instance_id": call_instance_id,
                    "trace_call_id": trace_call_id,
                    "call_id_source": call.get(
                        "call_id_source",
                        "provider",
                    ),
                    "provider_call_id": provider_call_id,
                    "provider_runtime_call_id": (
                        provider_runtime_call_id
                    ),
                    "provider_runtime_alias_relation": (
                        call.get(
                            "provider_runtime_alias_relation"
                        )
                    ),
                    "tool_name": call.get("tool_name"),
                    "call_fingerprint": call.get(
                        "call_fingerprint"
                    ),
                }
                self._call_index[call_instance_id] = state
                self._append_call_alias_locked(
                    trace_call_id,
                    call_instance_id,
                )
                self._append_call_alias_locked(
                    (
                        str(provider_call_id)
                        if provider_call_id
                        else None
                    ),
                    call_instance_id,
                )
                self._append_call_alias_locked(
                    (
                        str(provider_runtime_call_id)
                        if provider_runtime_call_id
                        else None
                    ),
                    call_instance_id,
                )
                call_instance_ids.append(call_instance_id)
            self._batch_call_instances[batch_id] = call_instance_ids
            provider_id_counts: dict[str, int] = {}
            for call_instance_id in call_instance_ids:
                provider_call_id = self._call_index[
                    call_instance_id
                ].get("provider_call_id")
                if provider_call_id:
                    provider_id_counts[str(provider_call_id)] = (
                        provider_id_counts.get(
                            str(provider_call_id),
                            0,
                        )
                        + 1
                    )
            duplicate_provider_ids = [
                provider_call_id
                for provider_call_id, count in (
                    provider_id_counts.items()
                )
                if count > 1
            ]
            for provider_call_id in duplicate_provider_ids:
                ambiguity = {
                    "status": "duplicate_provider_runtime_id",
                    "provider_runtime_call_id": provider_call_id,
                    "candidate_count": provider_id_counts[
                        provider_call_id
                    ],
                    "claiming": (
                        "name_canonical_args_fingerprint_then_"
                        "batch_sequence"
                    ),
                }
                for call_instance_id in call_instance_ids:
                    state = self._call_index[call_instance_id]
                    if (
                        state.get("provider_call_id")
                        == provider_call_id
                    ):
                        state["provider_id_ambiguity"] = ambiguity
            try:
                result = copy.deepcopy(
                    {
                        "tool_batch_id": batch_id,
                        "calls": [
                            self._call_index[call_instance_id]
                            for call_instance_id in call_instance_ids
                        ],
                    }
                )
            except Exception as exc:
                snapshot_error = exc
                result = {
                    "tool_batch_id": batch_id,
                    "calls": [],
                }
        for provider_call_id in duplicate_provider_ids:
            self._try_add_diagnostic(
                "duplicate_provider_call_id",
                provider_call_id,
            )
        if snapshot_error is not None:
            self._try_add_diagnostic(
                "tool_batch_locked_snapshot_failed",
                snapshot_error,
            )
        return self._detached_state(
            result,
            snapshot_diagnostic="tool_batch_snapshot_failed",
            normalization_diagnostic=(
                "tool_batch_normalization_failed"
            ),
        )

    def register_tool_batch(self, model_turn_id: str, call_ids: list[str]) -> str:
        registered = self.register_tool_calls(
            model_turn_id,
            [
                {
                    "trace_call_id": call_id,
                    "provider_call_id": call_id,
                    "call_id_source": "provider",
                }
                for call_id in call_ids
            ],
        )
        return str(registered["tool_batch_id"])

    def batch_call_ids(self, tool_batch_id: str | None) -> list[str]:
        if not tool_batch_id:
            return []
        with self._lock:
            calls = [
                (
                    (
                        self._call_index.get(call_instance_id)
                        or {}
                    ).get("call_sequence"),
                    (
                        self._call_index.get(call_instance_id)
                        or {}
                    ).get("trace_call_id"),
                )
                for call_instance_id in self._batch_call_instances.get(
                    tool_batch_id,
                    [],
                )
            ]
        return [
            str(trace_call_id)
            for _, trace_call_id in sorted(
                calls,
                key=lambda item: (
                    item[0]
                    if isinstance(item[0], int)
                    else sys.maxsize
                ),
            )
            if trace_call_id
        ]

    def batch_call_instances(
        self,
        tool_batch_id: str | None,
    ) -> list[dict[str, Any]]:
        if not tool_batch_id:
            return []
        snapshot_error: Exception | None = None
        with self._lock:
            try:
                states = copy.deepcopy(
                    [
                        self._call_index.get(call_instance_id)
                        or {}
                        for call_instance_id in (
                            self._batch_call_instances.get(
                                tool_batch_id,
                                [],
                            )
                        )
                    ]
                )
            except Exception as exc:
                snapshot_error = exc
                states = []
        if snapshot_error is not None:
            self._try_add_diagnostic(
                "batch_call_instances_locked_snapshot_failed",
                snapshot_error,
            )
        detached = self._safe_observation_value(
            states,
            snapshot_diagnostic=(
                "batch_call_instances_snapshot_failed"
            ),
            normalization_diagnostic=(
                "batch_call_instances_normalization_failed"
            ),
        )
        return detached if type(detached) is list else []

    def _state_for_identifier(
        self,
        identifier: str,
    ) -> dict[str, Any]:
        snapshot_error: Exception | None = None
        with self._lock:
            instance_ids = self._instance_ids_for_identifier_locked(
                identifier
            )
            try:
                state = (
                    copy.deepcopy(
                        self._call_index.get(instance_ids[-1])
                        or {}
                    )
                    if instance_ids
                    else {}
                )
            except Exception as exc:
                snapshot_error = exc
                state = (
                    dict(
                        self._call_index.get(instance_ids[-1])
                        or {}
                    )
                    if instance_ids
                    else {}
                )
            if len(instance_ids) > 1:
                state["call_id_lookup_ambiguity"] = {
                    "status": "multiple_call_instances",
                    "call_instance_ids": list(instance_ids),
                }
        if snapshot_error is not None:
            self._try_add_diagnostic(
                "call_identifier_locked_snapshot_failed",
                snapshot_error,
            )
        return state

    def call_ids(self, call_id: str) -> dict[str, Any]:
        state = self._state_for_identifier(call_id)
        return self._detached_state(
            state,
            snapshot_diagnostic="call_ids_snapshot_failed",
            normalization_diagnostic="call_ids_normalization_failed",
        )

    def note_call_id_source(self, call_id: str, source: str) -> None:
        with self._lock:
            instance_ids = self._instance_ids_for_identifier_locked(
                call_id
            )
            if not instance_ids:
                return
            self._call_index[instance_ids[-1]][
                "call_id_source"
            ] = source

    def set_active_call_instance(
        self,
        call_instance_id: str | None,
    ) -> None:
        self._active_call_instance_context.set(call_instance_id)

    def active_call_instance(self) -> str | None:
        return self._active_call_instance_context.get()

    def claim_tool_call(
        self,
        *,
        provider_runtime_call_id: str | None,
        tool_name: str | None,
        call_fingerprint: str | None,
        model_turn_id: str | None,
        stage: str,
    ) -> dict[str, Any]:
        claim_key = f"{stage}_claimed"
        ambiguity: dict[str, Any] | None = None
        snapshot_error: Exception | None = None
        with self._lock:
            candidates: list[dict[str, Any]] = []
            if provider_runtime_call_id:
                candidates = [
                    self._call_index[call_instance_id]
                    for call_instance_id in (
                        self._instance_ids_for_identifier_locked(
                            provider_runtime_call_id
                        )
                    )
                    if call_instance_id in self._call_index
                ]
            if model_turn_id:
                candidates = [
                    state
                    for state in candidates
                    if state.get("model_turn_id") == model_turn_id
                ]
            available = [
                state
                for state in candidates
                if not state.get(claim_key)
            ]
            fingerprint_matches = [
                state
                for state in available
                if call_fingerprint
                and state.get("call_fingerprint")
                == call_fingerprint
            ]
            if fingerprint_matches:
                available = fingerprint_matches
            if not available:
                available = [
                    state
                    for state in self._call_index.values()
                    if not state.get(claim_key)
                    and (
                        not model_turn_id
                        or state.get("model_turn_id")
                        == model_turn_id
                    )
                    and (
                        not tool_name
                        or not state.get("tool_name")
                        or state.get("tool_name") == tool_name
                    )
                    and (
                        not call_fingerprint
                        or state.get("call_fingerprint")
                        == call_fingerprint
                    )
                ]
            available.sort(
                key=lambda state: (
                    str(state.get("tool_batch_id") or ""),
                    state.get("call_sequence")
                    if isinstance(
                        state.get("call_sequence"),
                        int,
                    )
                    else sys.maxsize,
                )
            )
            if not available:
                return {}
            state = available[0]
            state[claim_key] = True
            trace_call_id = state.get("trace_call_id")
            if provider_runtime_call_id:
                state["provider_runtime_call_id"] = (
                    provider_runtime_call_id
                )
                relation = (
                    "same_as_trace_call_id"
                    if provider_runtime_call_id == trace_call_id
                    else "adk_runtime_alias_for_trace_call"
                )
                state["provider_runtime_alias_relation"] = relation
                self._append_call_alias_locked(
                    provider_runtime_call_id,
                    str(state["call_instance_id"]),
                )
            duplicate_candidates = [
                candidate
                for candidate in self._call_index.values()
                if provider_runtime_call_id
                and candidate.get("model_turn_id")
                == state.get("model_turn_id")
                and (
                    candidate.get("provider_call_id")
                    == provider_runtime_call_id
                    or candidate.get("provider_runtime_call_id")
                    == provider_runtime_call_id
                )
            ]
            if len(duplicate_candidates) > 1:
                ambiguity = {
                    "status": "duplicate_provider_runtime_id",
                    "provider_runtime_call_id": (
                        provider_runtime_call_id
                    ),
                    "candidate_count": len(duplicate_candidates),
                    "claiming": (
                        "name_canonical_args_fingerprint_then_"
                        "batch_sequence"
                    ),
                }
                for candidate in duplicate_candidates:
                    candidate["provider_id_ambiguity"] = ambiguity
            try:
                result = copy.deepcopy(state)
            except Exception as exc:
                snapshot_error = exc
                result = {}
        if ambiguity is not None:
            self._try_add_diagnostic(
                "duplicate_provider_runtime_call_id",
                str(provider_runtime_call_id),
            )
        if snapshot_error is not None:
            self._try_add_diagnostic(
                "tool_call_claim_locked_snapshot_failed",
                snapshot_error,
            )
        return self._detached_state(
            result,
            snapshot_diagnostic="tool_call_claim_snapshot_failed",
            normalization_diagnostic=(
                "tool_call_claim_normalization_failed"
            ),
        )

    def claim_function_response(
        self,
        *,
        provider_runtime_call_id: str | None,
        tool_name: str | None,
    ) -> dict[str, Any]:
        snapshot_error: Exception | None = None
        with self._lock:
            candidates = [
                self._call_index[call_instance_id]
                for call_instance_id in (
                    self._instance_ids_for_identifier_locked(
                        provider_runtime_call_id
                    )
                    if provider_runtime_call_id
                    else []
                )
                if call_instance_id in self._call_index
            ]
            if not candidates and provider_runtime_call_id is None:
                candidates = list(self._call_index.values())
            available = [
                state
                for state in candidates
                if not state.get("function_response_claimed")
                and (
                    not tool_name
                    or not state.get("tool_name")
                    or state.get("tool_name") == tool_name
                )
            ]
            available.sort(
                key=lambda state: (
                    str(state.get("tool_batch_id") or ""),
                    state.get("call_sequence")
                    if isinstance(
                        state.get("call_sequence"),
                        int,
                    )
                    else sys.maxsize,
                )
            )
            if not available:
                return {}
            state = available[0]
            state["function_response_claimed"] = True
            try:
                result = copy.deepcopy(state)
            except Exception as exc:
                snapshot_error = exc
                result = {}
        if snapshot_error is not None:
            self._try_add_diagnostic(
                "function_response_claim_locked_snapshot_failed",
                snapshot_error,
            )
        return self._detached_state(
            result,
            snapshot_diagnostic=(
                "function_response_claim_snapshot_failed"
            ),
            normalization_diagnostic=(
                "function_response_claim_normalization_failed"
            ),
        )

    def finalize_call_instance(
        self,
        call_instance_id: str,
        *,
        b08_recorded: bool,
    ) -> bool:
        finalized_at = utc_iso_timestamp()
        with self._lock:
            state = self._call_index.get(call_instance_id)
            if state is None:
                return False
            if not b08_recorded:
                state.pop("function_response_claimed", None)
                return False

            retained_keys = {
                "model_turn_id",
                "tool_batch_id",
                "call_sequence",
                "call_instance_id",
                "trace_call_id",
                "call_id_source",
                "provider_call_id",
                "provider_runtime_call_id",
                "provider_runtime_alias_relation",
                "provider_id_ambiguity",
                "tool_name",
                "dispatch_observed_at",
                "dispatch_completed_at",
                "completion_sequence",
                "batch_completion_sequence",
                "response_created_at",
                "tool_response_pruned",
                "function_tool_response_type",
                "model_facing_response_type",
                "parallel_overlap_confirmed",
                "overlapping_call_instance_ids",
            }
            compacted = {
                key: value
                for key, value in state.items()
                if key in retained_keys
            }
            compacted["b08_recorded"] = True
            compacted["call_state_compacted"] = True
            compacted["finalized_at"] = finalized_at
            self._call_index[call_instance_id] = compacted

            for alias, instance_ids in list(
                self._call_alias_index.items()
            ):
                remaining = [
                    instance_id
                    for instance_id in instance_ids
                    if instance_id != call_instance_id
                ]
                if remaining:
                    self._call_alias_index[alias] = remaining
                else:
                    self._call_alias_index.pop(alias, None)

            for observation_id, observation in list(
                self._observation_index.items()
            ):
                if observation.get("call_instance_id") == call_instance_id:
                    self._observation_index.pop(observation_id, None)

            batch_id = compacted.get("tool_batch_id")
            if isinstance(batch_id, str) and all(
                (
                    self._call_index.get(instance_id)
                    or {}
                ).get("call_state_compacted")
                for instance_id in self._batch_call_instances.get(
                    batch_id,
                    [],
                )
            ):
                self._batch_completion_number.pop(batch_id, None)
            return True

    def call_state_stats(self) -> dict[str, int]:
        heavy_keys = {
            "function_tool_response",
            "model_facing_response",
            "wrapper_received_arguments",
            "wrapper_argument_types",
            "final_positional_arguments",
            "final_keyword_arguments",
            "effective_arguments",
            "raw_arguments",
            "raw_response",
        }
        claim_keys = {
            "dispatch_claimed",
            "function_tool_claimed",
            "after_tool_claimed",
            "function_response_claimed",
        }
        with self._lock:
            states = list(self._call_index.values())
            return {
                "call_instance_count": len(states),
                "alias_instance_count": sum(
                    len(instance_ids)
                    for instance_ids in self._call_alias_index.values()
                ),
                "fingerprint_count": sum(
                    "call_fingerprint" in state for state in states
                ),
                "pending_claim_count": sum(
                    any(key in state for key in claim_keys)
                    for state in states
                ),
                "heavy_payload_call_count": sum(
                    any(key in state for key in heavy_keys)
                    for state in states
                ),
                "observation_count": len(self._observation_index),
            }

    def note_dispatch_started(
        self,
        call_instance_id: str,
        *,
        dispatch_observed_at: str,
        monotonic_started: float | None = None,
    ) -> None:
        with self._lock:
            state = self._call_index.get(call_instance_id)
            if state is None:
                return
            state["dispatch_observed_at"] = dispatch_observed_at
            state["_dispatch_started_monotonic"] = (
                monotonic_started
                if monotonic_started is not None
                else time.perf_counter()
            )

    def note_function_tool_response(
        self,
        call_id: str,
        *,
        unpruned_response: Any,
        model_facing_response: Any,
        pruned: bool,
        response_created_at: str | None = None,
    ) -> None:
        completed_monotonic = time.perf_counter()
        with self._lock:
            instance_ids = self._instance_ids_for_identifier_locked(
                call_id
            )
            if not instance_ids:
                return
            state = self._call_index[instance_ids[-1]]
            if "completion_sequence" not in state:
                self._completion_number += 1
                state["completion_sequence"] = self._completion_number
                state["response_created_at"] = (
                    response_created_at or utc_iso_timestamp()
                )
                batch_id = state.get("tool_batch_id")
                if isinstance(batch_id, str) and batch_id:
                    batch_completion = (
                        self._batch_completion_number.get(batch_id, 0)
                        + 1
                    )
                    self._batch_completion_number[batch_id] = (
                        batch_completion
                    )
                    state["batch_completion_sequence"] = (
                        batch_completion
                    )
                state["_dispatch_completed_monotonic"] = (
                    completed_monotonic
                )
                state["dispatch_completed_at"] = (
                    response_created_at or utc_iso_timestamp()
                )
                started = state.get("_dispatch_started_monotonic")
                for sibling_instance_id in (
                    self._batch_call_instances.get(batch_id, [])
                    if isinstance(batch_id, str)
                    else []
                ):
                    if sibling_instance_id == state.get(
                        "call_instance_id"
                    ):
                        continue
                    sibling = self._call_index.get(
                        sibling_instance_id
                    )
                    if sibling is None:
                        continue
                    sibling_started = sibling.get(
                        "_dispatch_started_monotonic"
                    )
                    sibling_completed = sibling.get(
                        "_dispatch_completed_monotonic"
                    )
                    if not isinstance(started, (int, float)) or (
                        not isinstance(
                            sibling_started,
                            (int, float),
                        )
                    ):
                        continue
                    if (
                        started < (
                            sibling_completed
                            if isinstance(
                                sibling_completed,
                                (int, float),
                            )
                            else completed_monotonic
                        )
                        and sibling_started < completed_monotonic
                    ):
                        state["parallel_overlap_confirmed"] = True
                        sibling["parallel_overlap_confirmed"] = True
                        state_overlaps = state.setdefault(
                            "overlapping_call_instance_ids",
                            [],
                        )
                        if sibling_instance_id not in state_overlaps:
                            state_overlaps.append(sibling_instance_id)
                        sibling_overlaps = sibling.setdefault(
                            "overlapping_call_instance_ids",
                            [],
                        )
                        state_instance_id = str(
                            state.get("call_instance_id")
                        )
                        if state_instance_id not in sibling_overlaps:
                            sibling_overlaps.append(
                                state_instance_id
                            )
        safe_unpruned = self._safe_observation_value(
            unpruned_response,
            snapshot_diagnostic="function_tool_response_snapshot_failed",
            normalization_diagnostic=(
                "function_tool_response_normalization_failed"
            ),
        )
        safe_model_facing = self._safe_observation_value(
            model_facing_response,
            snapshot_diagnostic="model_facing_response_snapshot_failed",
            normalization_diagnostic=(
                "model_facing_response_normalization_failed"
            ),
        )
        unpruned_type = _descriptor_type(unpruned_response)[
            "python_type"
        ]
        model_facing_type = _descriptor_type(model_facing_response)[
            "python_type"
        ]
        with self._lock:
            instance_ids = self._instance_ids_for_identifier_locked(
                call_id
            )
            if not instance_ids:
                return
            state = self._call_index[instance_ids[-1]]
            state["function_tool_response"] = safe_unpruned
            state["model_facing_response"] = safe_model_facing
            state["tool_response_pruned"] = bool(pruned)
            state["function_tool_response_type"] = unpruned_type
            state["model_facing_response_type"] = model_facing_type

    def _try_add_diagnostic(
        self,
        kind: str,
        exc: BaseException | str,
    ) -> None:
        try:
            self.add_diagnostic(kind, exc)
        except Exception:
            return

    def _safe_observation_value(
        self,
        value: Any,
        *,
        snapshot_diagnostic: str,
        normalization_diagnostic: str,
    ) -> Any:
        try:
            detached_value = copy.deepcopy(value)
        except Exception as exc:
            self._try_add_diagnostic(snapshot_diagnostic, exc)
            detached_value = value
        try:
            safe_value, _ = self._snapshot_trace_value(
                detached_value,
                nested=False,
            )
            return safe_value
        except Exception as exc:
            self._try_add_diagnostic(normalization_diagnostic, exc)
            return {}

    def _detached_state(
        self,
        state: dict[str, Any],
        *,
        snapshot_diagnostic: str,
        normalization_diagnostic: str,
    ) -> dict[str, Any]:
        try:
            return copy.deepcopy(state)
        except Exception as exc:
            self._try_add_diagnostic(snapshot_diagnostic, exc)
        try:
            safe_state, _ = self._snapshot_trace_value(
                state,
                nested=False,
            )
            return safe_state if type(safe_state) is dict else {}
        except Exception as exc:
            self._try_add_diagnostic(normalization_diagnostic, exc)
            return {}

    @staticmethod
    def _argument_type_names(arguments: dict[str, Any]) -> dict[str, str]:
        return {
            name: _descriptor_type(value)["python_type"]
            for name, value in dict.items(arguments)
            if type(name) is str
        }

    def begin_function_tool_observation(
        self,
        observation_id: str,
        *,
        model_arguments: dict[str, Any],
    ) -> None:
        safe_arguments = self._safe_observation_value(
            model_arguments,
            snapshot_diagnostic="model_arguments_snapshot_failed",
            normalization_diagnostic="model_arguments_normalization_failed",
        )
        argument_types = self._argument_type_names(model_arguments)
        with self._lock:
            self._observation_index[observation_id] = {
                "wrapper_invoked": False,
                "model_arguments": (
                    safe_arguments
                    if type(safe_arguments) is dict
                    else {}
                ),
                "model_argument_types": argument_types,
            }

    def note_function_tool_preprocessed_arguments(
        self,
        observation_id: str,
        arguments: dict[str, Any],
    ) -> None:
        safe_arguments = self._safe_observation_value(
            arguments,
            snapshot_diagnostic="preprocessed_arguments_snapshot_failed",
            normalization_diagnostic=(
                "preprocessed_arguments_normalization_failed"
            ),
        )
        argument_types = self._argument_type_names(arguments)
        with self._lock:
            state = self._observation_index.setdefault(
                observation_id,
                {},
            )
            state["function_tool_preprocessed_arguments"] = (
                safe_arguments
                if type(safe_arguments) is dict
                else {}
            )
            state["preprocessed_argument_types"] = argument_types

    def note_wrapper_arguments(
        self,
        observation_id: str,
        arguments: dict[str, Any],
        *,
        final_positional_arguments: list[Any] | None = None,
        final_keyword_arguments: dict[str, Any] | None = None,
        effective_arguments: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            state = self._observation_index.setdefault(
                observation_id,
                {},
            )
            state["wrapper_invoked"] = True
            state["wrapper_received_arguments"] = None

        safe_arguments = self._safe_observation_value(
            arguments,
            snapshot_diagnostic="wrapper_arguments_snapshot_failed",
            normalization_diagnostic="wrapper_arguments_normalization_failed",
        )
        safe_positional = self._safe_observation_value(
            list(final_positional_arguments or []),
            snapshot_diagnostic="positional_arguments_snapshot_failed",
            normalization_diagnostic="positional_arguments_normalization_failed",
        )
        safe_keyword = self._safe_observation_value(
            dict(final_keyword_arguments or {}),
            snapshot_diagnostic="keyword_arguments_snapshot_failed",
            normalization_diagnostic="keyword_arguments_normalization_failed",
        )
        safe_effective = self._safe_observation_value(
            dict(effective_arguments or {}),
            snapshot_diagnostic="effective_arguments_snapshot_failed",
            normalization_diagnostic="effective_arguments_normalization_failed",
        )
        argument_types = self._argument_type_names(arguments)

        with self._lock:
            state = self._observation_index.setdefault(
                observation_id,
                {},
            )
            state["wrapper_invoked"] = True
            state["wrapper_received_arguments"] = (
                safe_arguments if type(safe_arguments) is dict else {}
            )
            state["wrapper_argument_types"] = argument_types
            state["final_positional_arguments"] = (
                safe_positional if type(safe_positional) is list else []
            )
            state["final_keyword_arguments"] = (
                safe_keyword if type(safe_keyword) is dict else {}
            )
            state["effective_arguments"] = (
                safe_effective if type(safe_effective) is dict else {}
            )

    def call_state(self, observation_id: str) -> dict[str, Any]:
        snapshot_error: Exception | None = None
        with self._lock:
            observation_state = self._observation_index.get(
                observation_id
            )
            try:
                state = (
                    copy.deepcopy(observation_state)
                    if observation_state is not None
                    else None
                )
            except Exception as exc:
                snapshot_error = exc
                state = {}
        if snapshot_error is not None:
            self._try_add_diagnostic(
                "call_state_locked_snapshot_failed",
                snapshot_error,
            )
        if state is None:
            state = self._state_for_identifier(observation_id)
        return self._detached_state(
            state,
            snapshot_diagnostic="call_state_snapshot_failed",
            normalization_diagnostic="call_state_normalization_failed",
        )

    def clear_function_tool_observation(
        self,
        observation_id: str,
    ) -> None:
        with self._lock:
            self._observation_index.pop(observation_id, None)

    def clear_wrapper_call_state(self, call_id: str) -> None:
        with self._lock:
            state = self._call_index.get(call_id)
            if state is None:
                return
            state.pop("wrapper_received_arguments", None)
            state.pop("wrapper_invoked", None)

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
        call_instance_id: str | None = None,
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
                    "call_instance_id": call_instance_id,
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
            descriptor["preview"] = "[binary content omitted]"
            descriptor["redacted"] = True
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
                descriptor = _TraceDescriptor(
                    {
                        **identity,
                        "fidelity": "semantic_copy",
                        "semantic_value": semantic_value,
                    }
                )
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
            normalized_mapping = (
                _TraceDescriptor()
                if actual_type is _TraceDescriptor
                else {}
            )
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
