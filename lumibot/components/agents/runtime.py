from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import importlib
import inspect
import json
import logging
import math
import os
import re
import sys
import threading
import time
import traceback
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from importlib.metadata import version
from types import (
    BuiltinFunctionType,
    BuiltinMethodType,
    ClassMethodDescriptorType,
    FunctionType,
    MethodDescriptorType,
    MethodType,
    SimpleNamespace,
    WrapperDescriptorType,
)
from typing import Any, Callable, Literal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from pydantic import BaseModel

from .boundary_trace import BoundaryTraceCollector
from .litellm_trace import (
    LiteLLMBoundaryLogger,
    LiteLLMStreamAggregate,
    supports_dynamic_input_callback,
)
from .schemas import AgentRunResult, AgentTraceEvent, BoundTool, MCPServer
from .tool_context import agent_tool_context

_GOOGLE_SDK_NOISE_FILTERS_CONFIGURED = False
ClientSession = None
StdioServerParameters = None
stdio_client = None
streamablehttp_client = None
streamablehttp_client_uses_http_client = False


def _ensure_mcp_client_imports():
    global ClientSession, StdioServerParameters, stdio_client
    global streamablehttp_client, streamablehttp_client_uses_http_client
    if ClientSession is None or StdioServerParameters is None:
        from mcp import ClientSession as _ClientSession
        from mcp import StdioServerParameters as _StdioServerParameters

        ClientSession = _ClientSession
        StdioServerParameters = _StdioServerParameters
    if stdio_client is None:
        from mcp.client.stdio import stdio_client as _stdio_client

        stdio_client = _stdio_client
    if streamablehttp_client is None:
        try:
            from mcp.client.streamable_http import streamable_http_client as _streamablehttp_client
            streamablehttp_client_uses_http_client = True
        except ImportError:
            from mcp.client.streamable_http import streamablehttp_client as _streamablehttp_client

        streamablehttp_client = _streamablehttp_client


class _GoogleGenAITypesNoiseFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return "there are non-text parts in the response" not in message


def _configure_google_sdk_noise_filters() -> None:
    global _GOOGLE_SDK_NOISE_FILTERS_CONFIGURED
    if _GOOGLE_SDK_NOISE_FILTERS_CONFIGURED:
        return
    warnings.filterwarnings(
        "ignore",
        message="deprecated",
        category=DeprecationWarning,
        module=r"google\.adk\.runners",
    )
    warnings.filterwarnings(
        "ignore",
        message=r"Inheritance class AiohttpClientSession from ClientSession is discouraged",
        category=DeprecationWarning,
        module=r"google\.genai\._api_client",
    )
    logging.getLogger("google.genai.types").addFilter(_GoogleGenAITypesNoiseFilter())
    logging.getLogger("google_genai.types").addFilter(_GoogleGenAITypesNoiseFilter())
    _GOOGLE_SDK_NOISE_FILTERS_CONFIGURED = True


def _safe_exception_details(
    exc: Exception,
    *,
    include_traceback: bool = False,
) -> dict[str, str]:
    try:
        type_name = type.__getattribute__(type(exc), "__name__")
    except Exception:
        type_name = "Exception"
    try:
        message = str(exc)
    except Exception:
        message = "[exception message unavailable]"
    details = {
        "type": type_name if isinstance(type_name, str) else "Exception",
        "message": message,
    }
    if include_traceback:
        try:
            rendered_traceback = "".join(
                traceback.format_exception(
                    type(exc),
                    exc,
                    exc.__traceback__,
                )
            )
        except Exception:
            rendered_traceback = "[traceback unavailable]"
        details["traceback"] = rendered_traceback
    return details


def _tool_error_payload(tool_name: str, args: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "tool_error": True,
        "tool_name": tool_name,
        "error": _safe_exception_details(exc),
        "arguments": _json_safe_value(dict(args or {})),
    }


def _utc_iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _tool_function_name(value: str) -> str:
    normalized = re.sub(r"[^0-9a-zA-Z_]+", "_", value).strip("_")
    if not normalized:
        normalized = "tool"
    if normalized[0].isdigit():
        normalized = f"tool_{normalized}"
    return normalized


def _safe_callable_metadata(original: Any) -> dict[str, str | None]:
    metadata_target = original
    metadata_getter = object.__getattribute__
    if inspect.ismethod(original):
        try:
            metadata_target = object.__getattribute__(original, "__func__")
        except Exception:
            metadata_target = type(original)
            metadata_getter = type.__getattribute__
    elif inspect.isfunction(original) or inspect.isbuiltin(original):
        metadata_getter = object.__getattribute__
    elif inspect.isclass(original):
        metadata_getter = type.__getattribute__
    else:
        metadata_target = type(original)
        metadata_getter = type.__getattribute__

    values: dict[str, str | None] = {}
    for key in ("__module__", "__qualname__"):
        try:
            value = metadata_getter(metadata_target, key)
        except Exception:
            value = None
        values[key.removeprefix("__").removesuffix("__")] = value if isinstance(value, str) else None
    return values


def _safe_callable_annotations(original: Any) -> dict[str, Any] | None:
    target = original
    if inspect.ismethod(original):
        try:
            target = object.__getattribute__(original, "__func__")
        except Exception:
            return None
    if not (inspect.isfunction(target) or inspect.isbuiltin(target)):
        return None
    try:
        annotations = object.__getattribute__(target, "__annotations__")
    except Exception:
        return None
    return dict(annotations) if isinstance(annotations, dict) else None


def _fallback_callable_signature(original: Any) -> inspect.Signature | None:
    try:
        raw_call = inspect.getattr_static(type(original), "__call__")
        if isinstance(raw_call, (staticmethod, classmethod)):
            call = object.__getattribute__(raw_call, "__func__")
            remove_receiver = isinstance(raw_call, classmethod)
        else:
            call = raw_call
            remove_receiver = True
        signature = inspect.signature(call)
        parameters = list(signature.parameters.values())
        if remove_receiver and parameters:
            signature = signature.replace(parameters=parameters[1:])
        return signature
    except Exception:
        return None


_AsyncCallableClassification = Literal[
    "definite_async",
    "definite_sync",
    "ambiguous",
]
_SAFE_CALLABLE_INSPECTION_TYPES = {
    FunctionType,
    MethodType,
    BuiltinFunctionType,
    BuiltinMethodType,
}
_SAFE_DESCRIPTOR_BINDER_TYPES = {
    FunctionType,
    MethodType,
    BuiltinFunctionType,
    BuiltinMethodType,
    MethodDescriptorType,
    WrapperDescriptorType,
    ClassMethodDescriptorType,
}
_UNRESOLVED_SIGNATURE_TARGET = object()


def _classify_async_callable(
    original: Any,
) -> _AsyncCallableClassification:
    seen: set[int] = set()

    def classify(
        value: Any,
        *,
        inspect_instance_call: bool,
    ) -> _AsyncCallableClassification:
        value_id = id(value)
        if value_id in seen:
            return "ambiguous"
        seen.add(value_id)

        value_type = type(value)
        try:
            if issubclass(value_type, AsyncMock):
                return "definite_async"
        except Exception:
            return "ambiguous"
        if value_type in _SAFE_CALLABLE_INSPECTION_TYPES:
            return (
                "definite_async"
                if inspect.iscoroutinefunction(value)
                else "definite_sync"
            )
        if value_type in {staticmethod, classmethod}:
            return classify(
                object.__getattribute__(value, "__func__"),
                inspect_instance_call=True,
            )
        if value_type is functools.partialmethod:
            return classify(
                object.__getattribute__(value, "func"),
                inspect_instance_call=True,
            )
        if value_type is functools.partial:
            return classify(
                object.__getattribute__(value, "func"),
                inspect_instance_call=True,
            )
        if not inspect_instance_call:
            return "ambiguous"

        raw_call = inspect.getattr_static(value_type, "__call__")
        return classify(
            raw_call,
            inspect_instance_call=False,
        )

    try:
        return classify(original, inspect_instance_call=True)
    except Exception:
        return "ambiguous"


def _probe_ambiguous_call_descriptor(original: Any) -> Any:
    try:
        owner = type(original)
        raw_call = inspect.getattr_static(owner, "__call__")
        descriptor_get = inspect.getattr_static(
            type(raw_call),
            "__get__",
        )
        # Probe only exact Python/C descriptor binders. The bound value is
        # schema-only; execution still invokes the original inside tool context.
        if type(descriptor_get) not in _SAFE_DESCRIPTOR_BINDER_TYPES:
            return _UNRESOLVED_SIGNATURE_TARGET
        bound_target = descriptor_get(
            raw_call,
            original,
            owner,
        )
        if not callable(bound_target):
            return _UNRESOLVED_SIGNATURE_TARGET
        return bound_target
    except Exception:
        return _UNRESOLVED_SIGNATURE_TARGET


def _add_trace_diagnostic(
    collector: BoundaryTraceCollector | None,
    kind: str,
    exc: BaseException | str,
) -> None:
    if collector is None:
        return
    try:
        collector.add_diagnostic(kind, exc)
    except Exception:
        pass


def _record_tool_boundary(
    collector: BoundaryTraceCollector | None,
    **event: Any,
) -> dict[str, Any]:
    if collector is None:
        return {}
    try:
        return collector.record(**event)
    except Exception as exc:
        _add_trace_diagnostic(collector, "record_failed", exc)
        return {}


def _function_response_id_source(
    provider_response_id: str | None,
    state: dict[str, Any],
) -> str:
    if provider_response_id is None:
        return "generated_missing_adk_function_response_id"
    if (
        state.get("call_id_source") == "provider"
        and state.get("provider_call_id") == provider_response_id
    ):
        return "provider"
    if (
        state.get("call_id_source")
        in {
            "adk_generated_missing_provider_id",
            "generated_missing_provider_id",
            "generated_missing_adk_dispatch_id",
            "generated_missing_source_event_call_id",
            "generated_missing_after_tool_id",
        }
        and state.get("provider_call_id") is None
        and state.get("provider_runtime_call_id")
        == provider_response_id
    ):
        return "adk_generated_missing_provider_id"
    return "unknown_nonempty_runtime_id"


def _wrap_tool_callable(
    tool: BoundTool,
    tool_context: dict[str, Any] | None = None,
    *,
    collector: BoundaryTraceCollector | None = None,
    pre_invoke_observer: Callable[..., None] | None = None,
):
    original = tool.function
    callable_metadata = _safe_callable_metadata(original)
    async_classification = _classify_async_callable(original)
    if async_classification == "ambiguous":
        signature_target = _probe_ambiguous_call_descriptor(
            original
        )
        if signature_target is _UNRESOLVED_SIGNATURE_TARGET:
            callable_signature = _fallback_callable_signature(
                original
            )
        else:
            try:
                callable_signature = inspect.signature(
                    signature_target
                )
            except Exception:
                callable_signature = _fallback_callable_signature(
                    original
                )
    else:
        try:
            callable_signature = inspect.signature(original)
        except Exception:
            callable_signature = _fallback_callable_signature(original)

    def begin_invocation(
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        call_context: dict[str, Any] = {}
        if collector is not None:
            try:
                current_context = collector.current_tool_call()
            except Exception as exc:
                _add_trace_diagnostic(collector, "tool_call_context_failed", exc)
            else:
                if isinstance(current_context, dict):
                    call_context = current_context
        wrapper_received_arguments = dict(kwargs)
        effective_arguments = dict(kwargs)
        if callable_signature is not None:
            try:
                bound = callable_signature.bind_partial(*args, **kwargs)
                wrapper_received_arguments = dict(bound.arguments)
                bound.apply_defaults()
                effective_arguments = dict(bound.arguments)
            except (TypeError, ValueError):
                pass

        trace_ids = {
            "adk_invocation_id": call_context.get(
                "adk_invocation_id"
            ),
            "model_turn_id": call_context.get("model_turn_id"),
            "tool_batch_id": call_context.get("tool_batch_id"),
            "call_id": call_context.get("call_id"),
            "call_instance_id": call_context.get(
                "call_instance_id"
            ),
        }
        if pre_invoke_observer is not None:
            try:
                pre_invoke_observer(
                    final_positional_arguments=list(args),
                    final_keyword_arguments=dict(kwargs),
                    wrapper_received_arguments=wrapper_received_arguments,
                    effective_arguments=effective_arguments,
                    function_tool_context=call_context,
                )
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "pre_invoke_observation_failed",
                    exc,
                )
        invocation_started_at = _utc_iso_timestamp()
        invocation_started_monotonic = time.monotonic()
        started_perf = time.perf_counter()
        calling_thread = threading.current_thread()
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        observation_id = call_context.get("observation_id")
        tool_context_identifiers = {
            "agent_name": (
                tool_context.get("agent_name")
                if isinstance(tool_context, dict)
                else None
            ),
            "model_call_id": (
                tool_context.get("model_call_id")
                if isinstance(tool_context, dict)
                else None
            ),
            "adk_invocation_id": call_context.get(
                "adk_invocation_id"
            ),
            "model_turn_id": call_context.get("model_turn_id"),
            "tool_batch_id": call_context.get("tool_batch_id"),
            "call_id": call_context.get("call_id"),
            "call_instance_id": call_context.get(
                "call_instance_id"
            ),
        }
        _record_tool_boundary(
            collector,
            transition="B05_WRAPPER_TO_PYTHON_TOOL",
            from_module="lumibot_tool_wrapper",
            to_module="python_tool",
            started_at=invocation_started_at,
            payload={
                "tool_name": tool.name,
                "source": tool.source,
                "callable_module": callable_metadata["module"],
                "callable_qualname": callable_metadata["qualname"],
                "positional_arguments": list(args),
                "keyword_arguments": dict(kwargs),
                "effective_arguments": effective_arguments,
                "tool_context_identifiers": (
                    tool_context_identifiers
                ),
                "invocation_started_at": invocation_started_at,
                "invocation_started_monotonic": (
                    invocation_started_monotonic
                ),
                "invocation_monotonic_clock": "time.monotonic",
                "calling_thread": {
                    "identifier": calling_thread.ident,
                    "name": calling_thread.name,
                },
                "asyncio_task": (
                    {
                        "identifier": id(current_task),
                        "name": current_task.get_name(),
                    }
                    if current_task is not None
                    else None
                ),
                "observation_id": observation_id,
            },
            **trace_ids,
        )
        return {
            "wrapper_started_at": invocation_started_at,
            "started_perf": started_perf,
            "trace_ids": trace_ids,
            "observation_id": observation_id,
        }

    def finish_invocation(
        state: dict[str, Any],
        kwargs: dict[str, Any],
        *,
        raw_result: Any = None,
        error: Exception | None = None,
        tool_started_at: str | None,
        tool_ended_at: str | None,
        tool_duration_ms: float,
    ) -> Any:
        observation_id = state["observation_id"]
        trace_ids = state["trace_ids"]
        serialization_error: Exception | None = None
        if error is not None:
            if tool_started_at is None:
                tool_started_at = _utc_iso_timestamp()
                tool_ended_at = tool_started_at
            _record_tool_boundary(
                collector,
                transition="B06_PYTHON_TOOL_TO_WRAPPER",
                from_module="python_tool",
                to_module="lumibot_tool_wrapper",
                status="error",
                started_at=tool_started_at,
                ended_at=tool_ended_at,
                duration_ms=tool_duration_ms,
                payload={
                    "tool_name": tool.name,
                    "observation_id": observation_id,
                },
                error=_safe_exception_details(
                    error,
                    include_traceback=True,
                ),
                **trace_ids,
            )
            result = _tool_error_payload(
                tool.name,
                kwargs,
                error,
            )
        else:
            if collector is not None:
                try:
                    raw_description = collector.describe_raw_value(
                        raw_result,
                        include_forensics=True,
                    )
                except Exception as exc:
                    _add_trace_diagnostic(collector, "describe_raw_value_failed", exc)
                else:
                    _record_tool_boundary(
                        collector,
                        transition="B06_PYTHON_TOOL_TO_WRAPPER",
                        from_module="python_tool",
                        to_module="lumibot_tool_wrapper",
                        status="success",
                        started_at=tool_started_at,
                        ended_at=tool_ended_at,
                        duration_ms=tool_duration_ms,
                        payload={
                            "raw_result": raw_description,
                            "observation_id": observation_id,
                        },
                        **trace_ids,
                    )
            try:
                result = _json_safe_value(raw_result)
            except Exception as caught_serialization_error:
                serialization_error = caught_serialization_error
                result = _tool_error_payload(
                    tool.name,
                    kwargs,
                    caught_serialization_error,
                )
        if isinstance(tool_context, dict):
            calls = tool_context.setdefault("tool_calls", [])
            if isinstance(calls, list):
                calls.append(
                    {
                        "tool_name": tool.name,
                        "arguments": _json_safe_value(dict(kwargs or {})),
                        "ok": not (isinstance(result, dict) and result.get("tool_error") is True),
                    }
                )
        wrapper_error_payload = (
            result
            if error is not None or serialization_error is not None
            else None
        )
        if error is not None:
            serialization_changed_type = True
            serialization_changed_shape = True
            serialization_diagnostics = {
                "reason": "wrapper_generated_tool_error_payload",
                "changed_paths": ["$"],
            }
        elif serialization_error is not None:
            serialization_changed_type = True
            serialization_changed_shape = True
            serialization_diagnostics = {
                "reason": "serialization_failed_wrapper_error_payload",
                "changed_paths": ["$"],
                "error": _safe_exception_details(
                    serialization_error
                ),
            }
        else:
            (
                serialization_changed_type,
                serialization_changed_shape,
                changed_paths,
            ) = _serialization_changes(raw_result, result)
            serialization_diagnostics = {
                "reason": (
                    "json_safe_conversion"
                    if changed_paths
                    else "identity"
                ),
                "changed_paths": changed_paths,
            }
        _record_tool_boundary(
            collector,
            transition="B07_WRAPPER_TO_FUNCTION_TOOL",
            from_module="lumibot_tool_wrapper",
            to_module="function_tool",
            started_at=state["wrapper_started_at"],
            duration_ms=max(
                (
                    time.perf_counter()
                    - state["started_perf"]
                )
                * 1000,
                0.0,
            ),
            payload={
                "serialized_result": result,
                "serialization_changed_type": (
                    serialization_changed_type
                ),
                "serialization_changed_shape": (
                    serialization_changed_shape
                ),
                "serialization_diagnostics": (
                    serialization_diagnostics
                ),
                "wrapper_error_payload": wrapper_error_payload,
                "redaction_fidelity": (
                    "collector_snapshot_applied"
                ),
                "storage_fidelity": "event_payload_meta",
                "observation_id": observation_id,
            },
            **trace_ids,
        )
        return result

    def sync_wrapper(*args, **kwargs):
        state = begin_invocation(args, kwargs)
        tool_started_at: str | None = None
        tool_ended_at: str | None = None
        tool_duration_ms = 0.0
        raw_result: Any = None
        error: Exception | None = None
        try:
            with agent_tool_context(tool_context):
                tool_started_at = _utc_iso_timestamp()
                tool_started_perf = time.perf_counter()
                try:
                    raw_result = original(*args, **kwargs)
                finally:
                    tool_ended_perf = time.perf_counter()
                    tool_ended_at = _utc_iso_timestamp()
                    tool_duration_ms = max(
                        (
                            tool_ended_perf
                            - tool_started_perf
                        )
                        * 1000,
                        0.0,
                    )
        except Exception as exc:
            error = exc
        return finish_invocation(
            state,
            kwargs,
            raw_result=raw_result,
            error=error,
            tool_started_at=tool_started_at,
            tool_ended_at=tool_ended_at,
            tool_duration_ms=tool_duration_ms,
        )

    async def async_wrapper(*args, **kwargs):
        state = begin_invocation(args, kwargs)
        tool_started_at: str | None = None
        tool_ended_at: str | None = None
        tool_duration_ms = 0.0
        raw_result: Any = None
        error: Exception | None = None
        try:
            with agent_tool_context(tool_context):
                tool_started_at = _utc_iso_timestamp()
                tool_started_perf = time.perf_counter()
                try:
                    pending_result = original(
                        *args,
                        **kwargs,
                    )
                    raw_result = (
                        await pending_result
                        if inspect.isawaitable(pending_result)
                        else pending_result
                    )
                finally:
                    tool_ended_perf = time.perf_counter()
                    tool_ended_at = _utc_iso_timestamp()
                    tool_duration_ms = max(
                        (
                            tool_ended_perf
                            - tool_started_perf
                        )
                        * 1000,
                        0.0,
                    )
        except Exception as exc:
            error = exc
        return finish_invocation(
            state,
            kwargs,
            raw_result=raw_result,
            error=error,
            tool_started_at=tool_started_at,
            tool_ended_at=tool_ended_at,
            tool_duration_ms=tool_duration_ms,
        )

    wrapper = (
        sync_wrapper
        if async_classification == "definite_sync"
        else async_wrapper
    )
    wrapper.__name__ = _tool_function_name(tool.name)
    wrapper.__qualname__ = wrapper.__name__
    wrapper.__doc__ = tool.description
    if callable_signature is not None:
        wrapper.__signature__ = callable_signature
    annotations = _safe_callable_annotations(original)
    if annotations is not None:
        wrapper.__annotations__ = annotations
    return wrapper


def _serialization_changes(
    original: Any,
    serialized: Any,
) -> tuple[bool, bool, list[str]]:
    changed_type = False
    changed_shape = False
    changed_paths: list[str] = []

    def kind(value: Any) -> str:
        value_type = type(value)
        if value_type is dict:
            return "mapping"
        if value_type in (list, tuple):
            return "sequence"
        if value_type in (
            type(None),
            bool,
            str,
            int,
            float,
            datetime,
            date,
            UUID,
        ) or isinstance(value, Enum):
            return "scalar"
        return "opaque"

    def note(path: str) -> None:
        if path not in changed_paths:
            changed_paths.append(path)

    def compare(left: Any, right: Any, path: str) -> None:
        nonlocal changed_type, changed_shape
        left_type = type(left)
        right_type = type(right)
        left_kind = kind(left)
        right_kind = kind(right)
        if left_type is not right_type:
            changed_type = True
            note(path)
        if left_kind != right_kind:
            changed_shape = True
            note(path)
            return
        if left_kind == "mapping":
            left_keys = [str(key) for key in dict.keys(left)]
            right_keys = list(dict.keys(right))
            if (
                left_keys != right_keys
                or len(set(left_keys)) != len(left_keys)
            ):
                changed_shape = True
                note(path)
            for key in dict.keys(left):
                safe_key = str(key)
                if type(key) is not str:
                    changed_type = True
                    note(f"{path}.{safe_key}")
                if safe_key in right:
                    compare(
                        dict.__getitem__(left, key),
                        dict.__getitem__(right, safe_key),
                        f"{path}.{safe_key}",
                    )
            return
        if left_kind == "sequence":
            if len(left) != len(right):
                changed_shape = True
                note(path)
            for index, (left_item, right_item) in enumerate(
                zip(left, right)
            ):
                compare(
                    left_item,
                    right_item,
                    f"{path}[{index}]",
                )

    try:
        compare(original, serialized, "$")
    except Exception:
        return (
            type(original) is not type(serialized),
            kind(original) != kind(serialized),
            ["$"],
        )
    return changed_type, changed_shape, changed_paths


def _build_observed_function_tool(
    function_tool_type: type[Any],
    tool: BoundTool,
    *,
    collector: BoundaryTraceCollector | None,
    shared_tool_context: dict[str, Any],
):
    def build_boundary_payload(
        state: dict[str, Any],
        function_tool_context: dict[str, Any],
        *,
        missing_mandatory_arguments: list[str],
        validation_error: dict[str, Any] | None,
    ) -> dict[str, Any]:
        model_arguments = state.get("model_arguments") or {}
        preprocessed_arguments = state.get(
            "function_tool_preprocessed_arguments"
        )
        wrapper_invoked = state.get("wrapper_invoked") is True
        wrapper_arguments = state.get("wrapper_received_arguments")
        context_parameter = function_tool_context.get(
            "context_parameter"
        )

        filtered_arguments: dict[str, Any] | None = None
        filtered_source = "unavailable_before_wrapper_entry"
        if wrapper_invoked and isinstance(wrapper_arguments, dict):
            filtered_arguments = {
                name: value
                for name, value in wrapper_arguments.items()
                if name != context_parameter
            }
            filtered_source = (
                "authoritative wrapper arguments excluding "
                "ADK-injected context"
            )
        elif isinstance(preprocessed_arguments, dict):
            try:
                valid_parameters = set(
                    inspect.signature(wrapped).parameters
                )
                filtered_arguments = {
                    name: value
                    for name, value in preprocessed_arguments.items()
                    if name in valid_parameters
                    and name != context_parameter
                }
                filtered_source = (
                    "derived with installed FunctionTool signature "
                    "filter semantics; wrapper not invoked"
                )
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "function_tool_filtered_arguments_failed",
                    exc,
                )

        model_types = state.get("model_argument_types") or {}
        preprocessed_types = (
            state.get("preprocessed_argument_types") or {}
        )
        converted_arguments = [
            {
                "name": name,
                "from_type": model_types[name],
                "to_type": preprocessed_types[name],
            }
            for name in model_arguments
            if name in model_types
            and name in preprocessed_types
            and model_types[name] != preprocessed_types[name]
        ]

        effective_arguments = state.get("effective_arguments")
        defaulted_arguments: list[dict[str, Any]] = []
        if (
            isinstance(effective_arguments, dict)
            and isinstance(wrapper_arguments, dict)
        ):
            defaulted_arguments = [
                {"name": name, "value": value}
                for name, value in effective_arguments.items()
                if name not in wrapper_arguments
            ]

        removed_arguments = (
            sorted(set(model_arguments) - set(filtered_arguments))
            if isinstance(filtered_arguments, dict)
            else []
        )
        wrapper_source = (
            "authoritative _wrap_tool_callable entry"
            if wrapper_invoked
            else "unavailable; wrapper not invoked"
        )
        return {
            "tool_name": tool.name,
            "observation_id": function_tool_context.get(
                "observation_id"
            ),
            "call_id_source": function_tool_context.get(
                "call_id_source"
            ),
            "trace_call_id": function_tool_context.get("call_id"),
            "call_instance_id": function_tool_context.get(
                "call_instance_id"
            ),
            "provider_call_id": function_tool_context.get(
                "provider_call_id"
            ),
            "provider_runtime_call_id": (
                function_tool_context.get(
                    "provider_runtime_call_id"
                )
            ),
            "provider_runtime_alias_relation": (
                function_tool_context.get(
                    "provider_runtime_alias_relation"
                )
            ),
            "provider_id_ambiguity": function_tool_context.get(
                "provider_id_ambiguity"
            ),
            "model_arguments": model_arguments,
            "adk_function_call_arguments": model_arguments,
            "function_tool_preprocessed_arguments": (
                preprocessed_arguments
            ),
            "function_tool_filtered_arguments": filtered_arguments,
            "wrapper_received_arguments": wrapper_arguments,
            "final_positional_arguments": state.get(
                "final_positional_arguments"
            ),
            "final_keyword_arguments": state.get(
                "final_keyword_arguments"
            ),
            "effective_python_arguments_with_defaults": (
                effective_arguments
            ),
            "removed_arguments": removed_arguments,
            "converted_arguments": converted_arguments,
            "defaulted_arguments": defaulted_arguments,
            "missing_mandatory_arguments": (
                missing_mandatory_arguments
            ),
            "confirmation_status": function_tool_context.get(
                "confirmation_status",
                "not_present",
            ),
            "tool_confirmation_present": bool(
                function_tool_context.get(
                    "tool_confirmation_present"
                )
            ),
            "validation_error": validation_error,
            "argument_stage_fidelity": {
                "model_arguments": {
                    "fidelity": "semantic_snapshot",
                    "source": "ObservedFunctionTool.run_async args",
                },
                "adk_function_call_arguments": {
                    "fidelity": "same_snapshot_as_model_arguments",
                    "source": "FunctionTool.run_async args",
                },
                "function_tool_preprocessed_arguments": {
                    "fidelity": (
                        "semantic_snapshot"
                        if preprocessed_arguments is not None
                        else "unavailable"
                    ),
                    "source": "FunctionTool._preprocess_args return",
                },
                "function_tool_filtered_arguments": {
                    "fidelity": (
                        "semantic_snapshot"
                        if filtered_arguments is not None
                        else "unavailable"
                    ),
                    "source": filtered_source,
                },
                "wrapper_received_arguments": {
                    "fidelity": (
                        "semantic_snapshot"
                        if wrapper_invoked
                        else "unavailable"
                    ),
                    "source": wrapper_source,
                },
                "effective_python_arguments_with_defaults": {
                    "fidelity": (
                        "semantic_snapshot"
                        if effective_arguments is not None
                        else "unavailable"
                    ),
                    "source": wrapper_source,
                },
            },
        }

    def observe_wrapper_entry(
        *,
        final_positional_arguments: list[Any],
        final_keyword_arguments: dict[str, Any],
        wrapper_received_arguments: dict[str, Any],
        effective_arguments: dict[str, Any],
        function_tool_context: dict[str, Any],
    ) -> None:
        observation_id = function_tool_context.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id:
            return
        collector.note_wrapper_arguments(
            observation_id,
            wrapper_received_arguments,
            final_positional_arguments=final_positional_arguments,
            final_keyword_arguments=final_keyword_arguments,
            effective_arguments=effective_arguments,
        )
        state = collector.call_state(observation_id)
        _record_tool_boundary(
            collector,
            transition="B04_FUNCTION_TOOL_TO_WRAPPER",
            from_module="function_tool",
            to_module="lumibot_tool_wrapper",
            status="success",
            model_turn_id=function_tool_context.get("model_turn_id"),
            tool_batch_id=function_tool_context.get("tool_batch_id"),
            call_id=function_tool_context.get("call_id"),
            call_instance_id=function_tool_context.get(
                "call_instance_id"
            ),
            payload=build_boundary_payload(
                state,
                function_tool_context,
                missing_mandatory_arguments=[],
                validation_error=None,
            ),
        )

    wrapped = _wrap_tool_callable(
        tool,
        shared_tool_context,
        collector=collector,
        pre_invoke_observer=(
            observe_wrapper_entry
            if collector is not None
            else None
        ),
    )
    if collector is None:
        return function_tool_type(wrapped)

    class ObservedFunctionTool(function_tool_type):
        def _preprocess_args(
            self,
            args: dict[str, Any],
        ) -> dict[str, Any]:
            preprocessed = super()._preprocess_args(args)
            try:
                context = collector.current_tool_call() or {}
                observation_id = context.get("observation_id")
                if isinstance(observation_id, str) and observation_id:
                    collector.note_function_tool_preprocessed_arguments(
                        observation_id,
                        preprocessed,
                    )
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "function_tool_preprocess_observation_failed",
                    exc,
                )
            return preprocessed

        def _missing_mandatory_arguments(
            self,
            args: dict[str, Any],
        ) -> list[str]:
            get_mandatory_args = getattr(
                self,
                "_get_mandatory_args",
                None,
            )
            if callable(get_mandatory_args):
                mandatory_arguments = get_mandatory_args()
            else:
                signature = inspect.signature(wrapped)
                mandatory_arguments = [
                    name
                    for name, parameter in signature.parameters.items()
                    if parameter.default is inspect.Parameter.empty
                    and parameter.kind
                    not in (
                        inspect.Parameter.VAR_POSITIONAL,
                        inspect.Parameter.VAR_KEYWORD,
                    )
                ]
            context_parameter = getattr(
                self,
                "_context_param_name",
                "tool_context",
            )
            return [
                name
                for name in mandatory_arguments
                if name != context_parameter and name not in args
            ]

        def _record_uninvoked_boundary(
            self,
            *,
            status: str,
            args: dict[str, Any],
            observation_id: str,
            call_id: str,
            call_id_source: str,
            ids: dict[str, Any],
            confirmation_status: str,
            tool_confirmation_present: bool,
            validation_error: dict[str, Any],
        ) -> None:
            state = collector.call_state(observation_id)
            if state.get("wrapper_invoked"):
                return
            missing: list[str] = []
            if status == "blocked":
                try:
                    missing = self._missing_mandatory_arguments(args)
                except Exception as exc:
                    _add_trace_diagnostic(
                        collector,
                        "function_tool_missing_arguments_failed",
                        exc,
                    )
            _record_tool_boundary(
                collector,
                transition="B04_FUNCTION_TOOL_TO_WRAPPER",
                from_module="function_tool",
                to_module="lumibot_tool_wrapper",
                status=status,
                model_turn_id=ids.get("model_turn_id"),
                tool_batch_id=ids.get("tool_batch_id"),
                call_id=call_id,
                call_instance_id=ids.get("call_instance_id"),
                payload={
                    **build_boundary_payload(
                        state,
                        {
                            "observation_id": observation_id,
                            "call_id_source": call_id_source,
                            "call_id": call_id,
                            "call_instance_id": ids.get(
                                "call_instance_id"
                            ),
                            "provider_call_id": ids.get(
                                "provider_call_id"
                            ),
                            "provider_runtime_call_id": ids.get(
                                "provider_runtime_call_id"
                            ),
                            "provider_runtime_alias_relation": (
                                ids.get(
                                    "provider_runtime_alias_relation"
                                )
                            ),
                            "provider_id_ambiguity": ids.get(
                                "provider_id_ambiguity"
                            ),
                            "confirmation_status": (
                                confirmation_status
                            ),
                            "tool_confirmation_present": (
                                tool_confirmation_present
                            ),
                            "context_parameter": getattr(
                                self,
                                "_context_param_name",
                                "tool_context",
                            ),
                        },
                        missing_mandatory_arguments=missing,
                        validation_error=validation_error,
                    )
                },
                error=(
                    validation_error if status == "error" else None
                ),
            )

        async def run_async(
            self,
            *,
            args: dict[str, Any],
            tool_context: Any,
        ) -> Any:
            ids: dict[str, Any] = {}
            try:
                raw_provider_call_id = getattr(
                    tool_context,
                    "function_call_id",
                    None,
                )
                provider_runtime_call_id = (
                    str(raw_provider_call_id)
                    if raw_provider_call_id
                    else None
                )
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "function_call_id_observation_failed",
                    exc,
                )
                provider_runtime_call_id = None

            observation_id = f"function_tool:{uuid4().hex}"
            call_fingerprint = _tool_call_fingerprint(
                tool.name,
                args,
            )
            try:
                active_call_instance = (
                    collector.active_call_instance()
                )
                if active_call_instance:
                    ids = collector.call_ids(
                        active_call_instance
                    )
                if not ids:
                    ids = collector.claim_tool_call(
                        provider_runtime_call_id=(
                            provider_runtime_call_id
                        ),
                        tool_name=tool.name,
                        call_fingerprint=call_fingerprint,
                        model_turn_id=(
                            collector.active_model_turn()
                        ),
                        stage="function_tool",
                    )
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "function_tool_call_lookup_failed",
                    exc,
                )
            call_id = str(
                ids.get("trace_call_id")
                or provider_runtime_call_id
                or f"generated:function_tool:{uuid4().hex}"
            )
            call_id_source = str(
                ids.get("call_id_source")
                or (
                    "provider"
                    if provider_runtime_call_id
                    else "generated_missing_function_tool_id"
                )
            )

            tool_confirmation = None
            try:
                tool_confirmation = getattr(
                    tool_context,
                    "tool_confirmation",
                    None,
                )
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "tool_confirmation_observation_failed",
                    exc,
                )
            tool_confirmation_present = tool_confirmation is not None
            confirmation_status = "not_present"
            if tool_confirmation_present:
                try:
                    confirmed = getattr(
                        tool_confirmation,
                        "confirmed",
                        None,
                    )
                except Exception:
                    confirmed = None
                if confirmed is True:
                    confirmation_status = "confirmed"
                elif confirmed is False:
                    confirmation_status = "rejected"
                else:
                    confirmation_status = "present"

            try:
                collector.begin_function_tool_observation(
                    observation_id,
                    model_arguments=args,
                )
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "function_tool_observation_start_failed",
                    exc,
                )

            trace_context = None
            try:
                trace_context = collector.tool_call_context(
                    call_id=call_id,
                    call_instance_id=ids.get("call_instance_id"),
                    call_id_source=call_id_source,
                    observation_id=observation_id,
                    tool_name=tool.name,
                    context_parameter=getattr(
                        self,
                        "_context_param_name",
                        "tool_context",
                    ),
                    model_turn_id=ids.get("model_turn_id"),
                    tool_batch_id=ids.get("tool_batch_id"),
                    adk_invocation_id=_safe_optional_string(
                        _safe_attribute(
                            tool_context,
                            "invocation_id",
                        )
                    ),
                    model_arguments=args,
                    provider_call_id=ids.get("provider_call_id"),
                    provider_runtime_call_id=(
                        provider_runtime_call_id
                        or ids.get("provider_runtime_call_id")
                    ),
                    provider_runtime_alias_relation=ids.get(
                        "provider_runtime_alias_relation"
                    ),
                    provider_id_ambiguity=ids.get(
                        "provider_id_ambiguity"
                    ),
                    confirmation_status=confirmation_status,
                    tool_confirmation_present=(
                        tool_confirmation_present
                    ),
                )
                trace_context.__enter__()
            except Exception as exc:
                trace_context = None
                _add_trace_diagnostic(
                    collector,
                    "function_tool_context_failed",
                    exc,
                )

            try:
                try:
                    result = await super().run_async(
                        args=args,
                        tool_context=tool_context,
                    )
                except BaseException as exc:
                    try:
                        self._record_uninvoked_boundary(
                            status="error",
                            args=args,
                            observation_id=observation_id,
                            call_id=call_id,
                            call_id_source=call_id_source,
                            ids=ids,
                            confirmation_status=confirmation_status,
                            tool_confirmation_present=(
                                tool_confirmation_present
                            ),
                            validation_error=_safe_exception_details(
                                exc
                            ),
                        )
                    except Exception as observation_exc:
                        _add_trace_diagnostic(
                            collector,
                            "function_tool_error_observation_failed",
                            observation_exc,
                        )
                    raise

                try:
                    state = collector.call_state(observation_id)
                    if not state.get("wrapper_invoked"):
                        error_message = ""
                        if isinstance(result, dict):
                            error_message = str(
                                result.get("error") or ""
                            )
                        if error_message:
                            blocked_confirmation_status = (
                                "required_missing"
                                if "requires confirmation"
                                in error_message
                                else confirmation_status
                            )
                            self._record_uninvoked_boundary(
                                status="blocked",
                                args=args,
                                observation_id=observation_id,
                                call_id=call_id,
                                call_id_source=call_id_source,
                                ids=ids,
                                confirmation_status=(
                                    blocked_confirmation_status
                                ),
                                tool_confirmation_present=(
                                    tool_confirmation_present
                                ),
                                validation_error={
                                    "type": (
                                        "FunctionToolValidationBlocked"
                                    ),
                                    "message": error_message,
                                },
                            )
                        else:
                            _add_trace_diagnostic(
                                collector,
                                "function_tool_wrapper_entry_unobserved",
                                "FunctionTool returned without a validation "
                                "error or wrapper-entry observation",
                            )
                except Exception as exc:
                    _add_trace_diagnostic(
                        collector,
                        "function_tool_blocked_observation_failed",
                        exc,
                    )
                return result
            finally:
                if trace_context is not None:
                    try:
                        trace_context.__exit__(None, None, None)
                    except Exception as exc:
                        _add_trace_diagnostic(
                            collector,
                            "function_tool_context_exit_failed",
                            exc,
                        )
                try:
                    collector.clear_function_tool_observation(
                        observation_id
                    )
                except Exception as exc:
                    _add_trace_diagnostic(
                        collector,
                        "function_tool_observation_cleanup_failed",
                        exc,
                    )

    return ObservedFunctionTool(wrapped)


def _json_safe_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return _json_safe_value(value.value)
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _json_safe_value(v) for k, v in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_safe_value(model_dump(mode="json"))
    serializable = _to_serializable_dict(value)
    if serializable is not None:
        return {str(k): _json_safe_value(v) for k, v in serializable.items()}
    float_value = getattr(value, "__float__", None)
    if callable(float_value):
        try:
            coerced = float(value)
        except Exception:
            pass
        else:
            return coerced if math.isfinite(coerced) else None
    return str(value)


def _safe_type_name(value: Any) -> str:
    try:
        name = type.__getattribute__(type(value), "__name__")
    except Exception:
        return "unknown"
    return name if isinstance(name, str) else "unknown"


def _adk_object_payload(value: Any) -> Any:
    """Copy known ADK/Pydantic shapes without invoking arbitrary hooks."""

    try:
        if value is None or type(value) in (bool, str, int):
            return value
        if type(value) is float:
            return value if math.isfinite(value) else None
        if type(value) in (datetime, date):
            return value.isoformat()
        if type(value) is UUID:
            return str(value)
        if isinstance(value, Enum):
            return _adk_object_payload(value.value)
        if type(value) is dict:
            return {
                str(key): _adk_object_payload(item)
                for key, item in dict.items(value)
            }
        if type(value) in (list, tuple):
            return [_adk_object_payload(item) for item in value]
        if isinstance(value, BaseModel):
            try:
                dumped = BaseModel.model_dump(
                    value,
                    mode="json",
                    exclude_none=True,
                )
            except Exception:
                return {
                    "fidelity": "descriptor_only",
                    "python_type": _safe_type_name(value),
                    "serialization_failed": True,
                }
            return _adk_object_payload(dumped)
        if type(value) is SimpleNamespace:
            data = object.__getattribute__(value, "__dict__")
            return {
                str(key): _adk_object_payload(item)
                for key, item in dict.items(data)
            }
    except Exception:
        pass
    return {
        "fidelity": "descriptor_only",
        "python_type": _safe_type_name(value),
    }


def _parts_function_calls(value: Any) -> list[Any]:
    try:
        parts = getattr(getattr(value, "content", None), "parts", None) or []
        return [
            part.function_call
            for part in parts
            if getattr(part, "function_call", None) is not None
        ]
    except Exception:
        return []


def _provider_exposed_thought_parts(value: Any) -> list[Any]:
    try:
        parts = getattr(getattr(value, "content", None), "parts", None) or []
        return [
            _adk_object_payload(part)
            for part in parts
            if getattr(part, "thought", None) is True
        ]
    except Exception:
        return []


def _tool_call_fingerprint(
    tool_name: Any,
    arguments: Any,
) -> str:
    canonical = {
        "name": _safe_optional_string(tool_name),
        "arguments": _adk_object_payload(arguments),
    }
    try:
        encoded = json.dumps(
            canonical,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except Exception:
        encoded = repr(
            {
                "name": canonical["name"],
                "arguments_type": _safe_type_name(arguments),
            }
        ).encode("utf-8", errors="replace")
    return hashlib.sha256(encoded).hexdigest()


def _safe_optional_string(value: Any) -> str | None:
    try:
        return str(value) if value else None
    except Exception:
        return None


def _safe_attribute(value: Any, name: str) -> Any:
    try:
        return getattr(value, name, None)
    except Exception:
        return None


def _safe_object_identity(value: Any) -> dict[str, str | None]:
    metadata = _safe_callable_metadata(value)
    qualified_type = None
    if metadata["module"] and metadata["qualname"]:
        qualified_type = (
            f"{metadata['module']}.{metadata['qualname']}"
        )
    return {
        "name": _safe_optional_string(_safe_attribute(value, "name")),
        "python_type": _safe_type_name(value),
        "qualified_type": qualified_type,
    }


def _source_function_call_event(
    tool_context: Any,
    call_id: str,
) -> Any | None:
    try:
        events = getattr(
            getattr(tool_context, "session", None),
            "events",
            None,
        )
        if not isinstance(events, list):
            return None
        for event in reversed(events):
            parts = (
                getattr(getattr(event, "content", None), "parts", None)
                or []
            )
            for part in parts:
                function_call = getattr(part, "function_call", None)
                if function_call is None:
                    continue
                event_call_id = _safe_optional_string(
                    getattr(function_call, "id", None)
                )
                if event_call_id == call_id:
                    return event
    except Exception:
        return None
    return None


def _installed_google_adk_version() -> str:
    try:
        return version("google-adk")
    except Exception:
        return "unknown"


def _parallel_scheduling_evidence(
    batch_call_ids: list[str],
) -> dict[str, Any]:
    adk_version = _installed_google_adk_version()
    installed_dispatch_semantics = {
        "verification": (
            "verified_google_adk_2_1_source"
            if adk_version == "2.1.0"
            else "not_verified_for_installed_version"
        ),
        "mechanism": (
            "asyncio.create_task_per_filtered_function_call"
            if adk_version == "2.1.0"
            else None
        ),
    }
    if len(batch_call_ids) <= 1:
        return {
            "status": "single_call_no_parallel_schedule",
            "google_adk_version": adk_version,
            "installed_dispatch_semantics": (
                installed_dispatch_semantics
            ),
            "batch_candidate_count": len(batch_call_ids),
            "batch_membership_evidence": "candidate_only",
            "sibling_task_creation_observed": False,
            "completion_order_claim": "not_observed_at_dispatch",
        }
    return {
        "status": "batch_candidate_only",
        "google_adk_version": adk_version,
        "installed_dispatch_semantics": (
            installed_dispatch_semantics
        ),
        "batch_candidate_count": len(batch_call_ids),
        "batch_membership_evidence": "candidate_only",
        "sibling_task_creation_observed": False,
        "completion_order_claim": "not_observed_at_dispatch",
    }


def _parallel_execution_evidence(
    state: dict[str, Any],
    batch_call_ids: list[str],
) -> dict[str, Any]:
    if len(batch_call_ids) <= 1:
        return {
            "status": "single_call_no_parallel_candidate",
            "evidence": "single_batch_member",
            "batch_candidate_count": len(batch_call_ids),
            "overlapping_call_instance_ids": [],
        }
    if state.get("parallel_overlap_confirmed") is True:
        return {
            "status": "confirmed_actual_dispatch_overlap",
            "evidence": (
                "overlapping_before_tool_to_after_tool_windows"
            ),
            "batch_candidate_count": len(batch_call_ids),
            "overlapping_call_instance_ids": state.get(
                "overlapping_call_instance_ids"
            )
            or [],
        }
    return {
        "status": "batch_candidate_only_no_overlap_observed",
        "evidence": "completed_dispatch_windows_did_not_overlap",
        "batch_candidate_count": len(batch_call_ids),
        "overlapping_call_instance_ids": [],
    }


def _scalar_wrapping_metadata(
    function_response: Any,
    state: dict[str, Any],
) -> dict[str, Any]:
    function_tool_result_type = state.get(
        "function_tool_response_type"
    )
    detected = (
        type(function_response) is dict
        and set(function_response) == {"result"}
        and function_tool_result_type not in (None, "dict")
    )
    result_value = (
        dict.get(function_response, "result")
        if type(function_response) is dict
        else None
    )
    return {
        "detected": detected,
        "source": (
            "function_tool_scalar_to_adk_response_mapping"
            if detected
            else "no_scalar_wrapping_observed"
        ),
        "function_tool_result_type": function_tool_result_type,
        "adk_function_response_type": _safe_type_name(
            function_response
        ),
        "adk_result_value_type": (
            _safe_type_name(result_value)
            if detected
            else None
        ),
        "wrapped_key": "result" if detected else None,
    }


def _to_serializable_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if hasattr(value, "items"):
        try:
            return dict(value.items())
        except Exception:
            return None
    data = getattr(value, "__dict__", None)
    if isinstance(data, dict) and data:
        return data
    return None


def _extract_structured_content(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        structured = (
            result.get("structuredContent")
            or result.get("structured_content")
            or result.get("output")
            or result.get("result")
        )
        if isinstance(structured, dict):
            return structured
        content = result.get("content")
        if isinstance(content, Sequence):
            for entry in content:
                if isinstance(entry, dict) and isinstance(entry.get("text"), str):
                    try:
                        parsed = json.loads(entry["text"])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, dict):
                        return parsed
        return result
    structured_content = getattr(result, "structured_content", None)
    if isinstance(structured_content, dict):
        return structured_content
    return {"value": structured_content or result}


def _quiet_backtest_logs_enabled() -> bool:
    return (
        str(os.environ.get("IS_BACKTESTING", "")).strip().lower() == "true"
        and str(os.environ.get("BACKTESTING_QUIET_LOGS", "")).strip().lower() in {"1", "true", "yes", "on"}
    )


@contextlib.contextmanager
def _mcp_errlog_stream():
    if _quiet_backtest_logs_enabled():
        with open(os.devnull, "w", encoding="utf-8") as devnull:
            yield devnull
        return
    yield sys.stderr


def _extract_tool_text(response: Any) -> list[str]:
    if response is None:
        return []
    if isinstance(response, dict):
        content = response.get("content") or response.get("contents")
    else:
        content = getattr(response, "content", None) or getattr(response, "contents", None)
    if not content:
        return []
    chunks: list[str] = []
    for entry in content:
        text_value: str | None = None
        if isinstance(entry, str):
            text_value = entry
        elif isinstance(entry, dict):
            text_value = entry.get("text")
        else:
            text_value = getattr(entry, "text", None)
        if isinstance(text_value, str) and text_value.strip():
            chunks.append(text_value.strip())
    return chunks


def _coerce_usage_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    data = getattr(value, "__dict__", None)
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items()}
    return None


def _aggregate_usage_metadata(payloads: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not payloads:
        return None
    if len(payloads) == 1:
        return payloads[0]

    aggregate: dict[str, Any] = dict(payloads[-1])
    additive_keys = {
        "cached_content_token_count",
        "cached_input_tokens",
        "cached_prompt_tokens",
        "cached_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "candidates_token_count",
        "completion_tokens",
        "prompt_token_count",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "prompt_tokens",
        "reasoning_tokens",
        "thoughts_token_count",
        "tool_use_prompt_token_count",
        "total_token_count",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    }
    for key in additive_keys:
        total = 0
        seen = False
        for payload in payloads:
            value = payload.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                total += int(value)
                seen = True
        if seen:
            aggregate[key] = total
    aggregate["aggregated_usage_event_count"] = len(payloads)
    return aggregate


def _normalize_event_impl(
    event: Any,
    *,
    collector: BoundaryTraceCollector | None = None,
    failed_b08_claims: list[str],
) -> list[AgentTraceEvent]:
    normalized: list[AgentTraceEvent] = []
    event_id = str(getattr(event, "id", None) or "") or None
    invocation_id = (
        str(getattr(event, "invocation_id", None) or "") or None
    )
    event_metadata = {
        "event_id": event_id,
        "invocation_id": invocation_id,
    }
    parts = getattr(getattr(event, "content", None), "parts", None) or []
    function_response_entries: list[
        tuple[Any, str, str | None, str]
    ] = []
    for part in parts:
        function_response = getattr(part, "function_response", None)
        if not function_response or not getattr(
            function_response,
            "name",
            None,
        ):
            continue
        tool_name = str(function_response.name)
        provider_response_id = (
            str(getattr(function_response, "id", None) or "") or None
        )
        response_id_source = (
            "unknown_nonempty_runtime_id"
            if provider_response_id is not None
            else "generated_missing_adk_function_response_id"
        )
        if provider_response_id is None and collector is not None:
            _add_trace_diagnostic(
                collector,
                "generated_missing_adk_function_response_call_id",
                "ADK FunctionResponse had no call ID",
            )
        function_response_entries.append(
            (
                function_response,
                tool_name,
                provider_response_id,
                response_id_source,
            )
        )
    merged_call_ids = [
        call_id
        for _, _, call_id, _ in function_response_entries
        if call_id is not None
    ]
    function_response_index = 0
    for part in parts:
        if getattr(part, "thought", None) is True:
            thought_text = getattr(part, "text", None)
            if isinstance(thought_text, str) and thought_text.strip():
                normalized.append(
                    AgentTraceEvent(
                        kind="thinking",
                        text=thought_text.strip(),
                        payload={"source": "model_thought"},
                        **event_metadata,
                    )
                )
                continue

        text = getattr(part, "text", None)
        if isinstance(text, str) and text.strip():
            normalized.append(
                AgentTraceEvent(
                    kind="text",
                    text=text.strip(),
                    **event_metadata,
                )
            )

        function_call = getattr(part, "function_call", None)
        if function_call and getattr(function_call, "name", None):
            payload = _to_serializable_dict(getattr(function_call, "args", None)) or _to_serializable_dict(
                getattr(function_call, "arguments", None)
            )
            normalized.append(
                AgentTraceEvent(
                    kind="tool_call",
                    tool_name=str(function_call.name),
                    payload=payload,
                    call_id=(
                        str(getattr(function_call, "id", None) or "")
                        or None
                    ),
                    **event_metadata,
                )
            )

        function_response = getattr(part, "function_response", None)
        if function_response and getattr(function_response, "name", None):
            (
                _,
                tool_name,
                provider_response_id,
                response_id_source,
            ) = function_response_entries[function_response_index]
            function_response_index += 1
            state: dict[str, Any] = {}
            response = getattr(
                function_response,
                "response",
                None,
            )
            trace_call_id = provider_response_id
            call_instance_id = None
            batch_call_ids: list[str] = []
            trace_call_id_source = response_id_source
            if collector is not None:
                try:
                    state = collector.claim_function_response(
                        provider_runtime_call_id=(
                            provider_response_id
                        ),
                        tool_name=tool_name,
                    )
                    if not state and provider_response_id:
                        state = collector.call_state(
                            provider_response_id
                        )
                    trace_call_id_source = (
                        state.get("call_id_source")
                        or trace_call_id_source
                    )
                except Exception as exc:
                    _add_trace_diagnostic(
                        collector,
                        "function_response_state_lookup_failed",
                        exc,
                    )
                trace_call_id = (
                    state.get("trace_call_id")
                    or provider_response_id
                    or (
                        "generated:adk_function_response:"
                        f"{uuid4().hex}"
                    )
                )
                call_instance_id = state.get("call_instance_id")
                try:
                    batch_call_ids = collector.batch_call_ids(
                        state.get("tool_batch_id")
                    )
                except Exception as exc:
                    _add_trace_diagnostic(
                        collector,
                        "function_response_batch_lookup_failed",
                        exc,
                    )
                response_id_source = _function_response_id_source(
                    provider_response_id,
                    state,
                )
                b08_recorded = False
                try:
                    recorded_event = _record_tool_boundary(
                        collector,
                        transition="B08_FUNCTION_TOOL_TO_ADK",
                        from_module="function_tool",
                        to_module="google_adk",
                        adk_invocation_id=invocation_id,
                        model_turn_id=state.get("model_turn_id"),
                        tool_batch_id=state.get("tool_batch_id"),
                        call_id=trace_call_id,
                        call_instance_id=call_instance_id,
                        payload={
                            "event_id": event_id,
                            "invocation_id": invocation_id,
                            "response_id": (
                                provider_response_id or trace_call_id
                            ),
                            "response_id_source": response_id_source,
                            "trace_call_id": trace_call_id,
                            "trace_call_id_source": (
                                trace_call_id_source
                            ),
                            "call_instance_id": call_instance_id,
                            "provider_call_id": state.get(
                                "provider_call_id"
                            ),
                            "provider_runtime_call_id": (
                                provider_response_id
                                or state.get(
                                    "provider_runtime_call_id"
                                )
                            ),
                            "provider_runtime_alias_relation": (
                                state.get(
                                    "provider_runtime_alias_relation"
                                )
                            ),
                            "provider_id_ambiguity": state.get(
                                "provider_id_ambiguity"
                            ),
                            "function_name": tool_name,
                            "function_response": _adk_object_payload(
                                response
                            ),
                            "authoritative_next_model_data": True,
                            "function_tool_response": state.get(
                                "function_tool_response"
                            ),
                            "model_facing_response": state.get(
                                "model_facing_response"
                            ),
                            "tool_response_pruned": state.get(
                                "tool_response_pruned"
                            ),
                            "completion_sequence": state.get(
                                "completion_sequence"
                            ),
                            "batch_completion_sequence": state.get(
                                "batch_completion_sequence"
                            ),
                            "response_created_at": state.get(
                                "response_created_at"
                            ),
                            "scalar_wrapping": (
                                _scalar_wrapping_metadata(
                                    response,
                                    state,
                                )
                            ),
                            "parallel_execution": (
                                _parallel_execution_evidence(
                                    state,
                                    batch_call_ids,
                                )
                            ),
                            "merged_event": {
                                "function_response_count": len(
                                    function_response_entries
                                ),
                                "function_response_index": (
                                    function_response_index
                                ),
                                "call_ids": merged_call_ids,
                            },
                        },
                    )
                    b08_recorded = bool(recorded_event)
                finally:
                    if call_instance_id:
                        if b08_recorded:
                            try:
                                collector.finalize_call_instance(
                                    call_instance_id,
                                    b08_recorded=True,
                                )
                            except Exception as exc:
                                _add_trace_diagnostic(
                                    collector,
                                    "function_response_finalize_failed",
                                    exc,
                                )
                        else:
                            failed_b08_claims.append(
                                str(call_instance_id)
                            )
            for chunk in _extract_tool_text(response):
                normalized.append(
                    AgentTraceEvent(
                        kind="text",
                        text=chunk,
                        tool_name=tool_name,
                        call_id=trace_call_id,
                        **event_metadata,
                    )
                )
            normalized.append(
                AgentTraceEvent(
                    kind="tool_result",
                    tool_name=tool_name,
                    payload=_extract_structured_content(response or {}),
                    call_id=trace_call_id,
                    **event_metadata,
                )
            )

    usage_payload = _coerce_usage_metadata(getattr(event, "usage_metadata", None))
    if usage_payload:
        normalized.append(
            AgentTraceEvent(
                kind="usage",
                payload=usage_payload,
                **event_metadata,
            )
        )
    return normalized


def _normalize_event(
    event: Any,
    *,
    collector: BoundaryTraceCollector | None = None,
) -> list[AgentTraceEvent]:
    failed_b08_claims: list[str] = []
    try:
        return _normalize_event_impl(
            event,
            collector=collector,
            failed_b08_claims=failed_b08_claims,
        )
    finally:
        if collector is not None:
            for call_instance_id in failed_b08_claims:
                try:
                    collector.finalize_call_instance(
                        call_instance_id,
                        b08_recorded=False,
                    )
                except Exception as exc:
                    _add_trace_diagnostic(
                        collector,
                        "function_response_claim_release_failed",
                        exc,
                    )


@dataclass
class RuntimeRequest:
    agent_name: str
    model: str
    system_prompt: str
    task_prompt: str | None
    context: dict[str, Any] | None
    runtime_context: dict[str, Any] | None
    memory_state: dict[str, Any] | None
    memory_notes: list[dict[str, Any]]
    bound_tools: list[BoundTool]
    model_call_id: str | None = None
    provider_prompt_cache_key: str | None = None
    model_request_timeout_seconds: float | None = None
    run_timeout_seconds: float | None = None
    agent_run_id: str | None = None
    boundary_collector: BoundaryTraceCollector | None = None


_LITELLM_CONFIGURED = False


def _coerce_positive_timeout_seconds(value: Any) -> float | None:
    if value is None:
        return None
    try:
        timeout_seconds = float(value)
    except Exception:
        return None
    return timeout_seconds if timeout_seconds > 0 else None


def _parse_timeout_seconds(value: Any) -> tuple[bool, float | None]:
    if value is None:
        return False, None
    try:
        timeout_seconds = float(value)
    except Exception:
        return False, None
    return True, timeout_seconds if timeout_seconds > 0 else None


# Error classification for AI agent calls.
#
# The taxonomy has five buckets. Scope: AI agent calls only. The rest of
# LumiBot's error handling (strategy_executor, brokers, data sources) is
# unchanged. See `AgentHandle.run()` and `docsrc/agents.rst` for how these
# buckets map to backtest-vs-live behavior.
#
#   "auth"      : missing/invalid API key, permission denied (401, 403)
#   "config"    : bad model id, malformed prompt, context-window exceeded,
#                 invalid payload (400, 404, 422)
#   "billing"   : out of credits, payment required, quota exhausted (402,
#                 429 + "insufficient_quota", 403 + billing/credits msg)
#   "transient" : 5xx, rate-limit bursts, timeouts, connection errors
#   "unknown"   : anything not matched above; treated as transient (safe default)

_ERROR_CLASS_AUTH = (
    "AuthenticationError",
    "PermissionDeniedError",
    "UnauthenticatedError",
    "NotAuthorized",
)
_ERROR_CLASS_CONFIG = (
    "BadRequestError",
    "NotFoundError",
    "UnprocessableEntityError",
    "ContextWindowExceededError",
    "ContentPolicyViolationError",
    "InvalidRequestError",
    "ImportError",
    "ModuleNotFoundError",
)
_ERROR_CLASS_BILLING = (
    "BillingError",
    "InsufficientQuotaError",
    "PaymentRequiredError",
)
_ERROR_CLASS_TRANSIENT = (
    "APIConnectionError",
    "APIResponseValidationError",
    "APITimeoutError",
    "InternalServerError",
    "ServerError",
    "ServiceUnavailableError",
    "Timeout",
    "TimeoutError",
    "OverloadedError",
    "RateLimitError",
    "ServerDisconnectedError",
    "ReadTimeout",
    "ConnectTimeout",
    "ConnectionError",
)

_BILLING_BODY_KEYWORDS = (
    "insufficient_quota",
    "insufficient funds",
    "no credits",
    "no credit",
    "available credits",
    "out of credits",
    "spending limit",
    "monthly spending limit",
    "billing",
    "payment",
    "purchase those",
    "team doesn't have any credits",
    "team does not have any credits",
    "quota exceeded",
    "exceeded your current quota",
)


def _classify_agent_error(exc: BaseException) -> str:
    """Map an exception raised by the AI agent stack to a bucket.

    See the module-level docstring for the taxonomy. Safe default is
    "unknown" so behavior matches "transient" (retry/skip) when we
    cannot tell — failing closed is the wrong choice for AI errors
    because most truly-unknown failures are transient provider issues.
    """
    exc_name = exc.__class__.__name__
    message = str(exc)
    message_lower = message.lower()

    # HTTP status code if the provider SDK attached one.
    status_code = None
    for attr in ("status_code", "http_status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            status_code = value
            break

    # Body/keyword check for billing — takes precedence over auth because
    # providers often return 401/403 for "no credits" when the key itself
    # is valid (e.g. xAI's "team doesn't have any credits yet").
    if any(kw in message_lower for kw in _BILLING_BODY_KEYWORDS):
        return "billing"

    # Explicit class-name matches (litellm + google-genai + openai + anthropic).
    if exc_name in _ERROR_CLASS_AUTH:
        return "auth"
    if exc_name in _ERROR_CLASS_CONFIG:
        return "config"
    if exc_name in _ERROR_CLASS_BILLING:
        return "billing"
    if exc_name in _ERROR_CLASS_TRANSIENT:
        return "transient"

    # HTTP status code based classification as a fallback.
    if status_code is not None:
        if status_code == 402:
            return "billing"
        if status_code in (401, 403):
            # Already checked billing keywords above; if we got here it's auth.
            return "auth"
        if status_code == 404:
            return "config"
        if status_code in (400, 422):
            return "config"
        if status_code == 429:
            # Rate limits are transient; insufficient_quota already caught above.
            return "transient"
        if 500 <= status_code < 600:
            return "transient"

    # Message substring fallback for providers that don't use standard class names.
    lower_exc = exc_name.lower()
    if any(kw in lower_exc for kw in ("auth", "permission", "unauthorized", "apikey")):
        return "auth"
    if "context" in lower_exc and ("length" in lower_exc or "window" in lower_exc):
        return "config"
    if any(kw in message_lower for kw in ("api key", "apikey", "unauthenticated", "permission denied")):
        return "auth"
    if "invalid model" in message_lower or "model not found" in message_lower:
        return "config"
    if "context length" in message_lower or "context_length" in message_lower or "context window" in message_lower:
        return "config"
    if "not json serializable" in message_lower or "not json-serializable" in message_lower:
        return "config"

    return "unknown"


def _configure_litellm_quietly() -> None:
    # LiteLLM's provider-lookup path in get_llm_provider_logic.py prints a
    # red "Provider List: https://docs.litellm.ai/docs/providers" banner to
    # stderr on internal probes (cost/tokenizer lookups for models not in
    # litellm.model_cost). The banner is purely cosmetic: real failures
    # still raise BadRequestError. New model ids (e.g. gpt-5.4-*, grok-4.20)
    # routinely ship before LiteLLM's static registry catches up, so this
    # banner would fire on every agent call for current-generation models.
    # suppress_debug_info mutes the banner without suppressing exceptions.
    global _LITELLM_CONFIGURED
    if _LITELLM_CONFIGURED:
        return
    try:
        import litellm
    except ImportError:
        _LITELLM_CONFIGURED = True
        return
    try:
        litellm.suppress_debug_info = True
    except Exception:
        pass
    # Provider param compatibility: Google ADK's LiteLlm bridge emits
    # OpenAI-shaped params (e.g. max_completion_tokens). Some providers
    # (xAI, Anthropic, a few others) reject unknown params with
    # UnsupportedParamsError. drop_params makes LiteLLM silently drop
    # params the target provider does not accept instead of failing the
    # call. Affects only unknown kwargs; real errors still propagate.
    try:
        litellm.drop_params = True
    except Exception:
        pass
    # Transient-error retry: rate-limit (429), server errors (500/502/503/529),
    # and brief network blips all happen in normal operation. LiteLLM has
    # provider-aware retry logic (exponential backoff, 429 Retry-After
    # awareness). Enable it at the library level so every provider benefits.
    # Does NOT retry 4xx client errors (auth, invalid model, context length).
    try:
        litellm.num_retries = 3
    except Exception:
        pass
    _LITELLM_CONFIGURED = True


def _sync_xai_api_key_alias() -> None:
    """Allow Grok users to provide either the vendor key name or product name.

    LiteLLM's xAI provider reads XAI_API_KEY. LumiBot's older Grok helper also
    accepts GROK_API_KEY, so mirror GROK_API_KEY into XAI_API_KEY for xai/ models
    when the canonical xAI env var is absent.
    """
    if not os.environ.get("XAI_API_KEY") and os.environ.get("GROK_API_KEY"):
        os.environ["XAI_API_KEY"] = os.environ["GROK_API_KEY"]


def _sync_together_api_key_alias() -> None:
    """Allow either Together's SDK key name or LiteLLM's provider key name.

    Together's own examples commonly use TOGETHER_API_KEY, while LiteLLM's
    Together provider reads TOGETHERAI_API_KEY. Mirror in both directions so
    users can set either one.
    """
    if not os.environ.get("TOGETHERAI_API_KEY") and os.environ.get("TOGETHER_API_KEY"):
        os.environ["TOGETHERAI_API_KEY"] = os.environ["TOGETHER_API_KEY"]
    if not os.environ.get("TOGETHER_API_KEY") and os.environ.get("TOGETHERAI_API_KEY"):
        os.environ["TOGETHER_API_KEY"] = os.environ["TOGETHERAI_API_KEY"]


def _provider_prompt_cache_key(request: RuntimeRequest) -> str:
    """Stable provider-routing key for server-side prompt caches.

    This is not LumiBot's replay cache key. It intentionally excludes the
    changing market context so providers can reuse the static prefix
    (system prompt + tool declarations) while still computing each new bar.
    """
    payload = {
        "agent": request.agent_name,
        "model": request.model,
        "system_prompt": request.system_prompt,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "source": tool.source,
                "metadata": tool.metadata,
            }
            for tool in request.bound_tools
        ],
    }
    digest = hashlib.sha256(json.dumps(_json_safe_value(payload), sort_keys=True).encode("utf-8")).hexdigest()
    return f"lumibot:{request.agent_name}:{digest[:32]}"


DEFAULT_MODEL_CONTEXT_LIMIT_TOKENS = 1_000_000
DEFAULT_MODEL_CONTEXT_STRING_LIMIT_CHARS = 20_000

MODEL_CONTEXT_LIMIT_PREFIXES: tuple[tuple[str, int, int], ...] = (
    ("anthropic/claude-", 200_000, DEFAULT_MODEL_CONTEXT_STRING_LIMIT_CHARS),
    ("claude-", 200_000, DEFAULT_MODEL_CONTEXT_STRING_LIMIT_CHARS),
    ("deepseek/deepseek-v4-", 1_048_576, DEFAULT_MODEL_CONTEXT_STRING_LIMIT_CHARS),
    ("gemini-3.1", 1_048_576, DEFAULT_MODEL_CONTEXT_STRING_LIMIT_CHARS),
    ("gemini-2.5", 1_048_576, DEFAULT_MODEL_CONTEXT_STRING_LIMIT_CHARS),
    ("gemini-1.5", 1_048_576, DEFAULT_MODEL_CONTEXT_STRING_LIMIT_CHARS),
    ("openai/gpt-4.1", 1_047_576, DEFAULT_MODEL_CONTEXT_STRING_LIMIT_CHARS),
    ("gpt-4.1", 1_047_576, DEFAULT_MODEL_CONTEXT_STRING_LIMIT_CHARS),
    ("xai/grok-4.20", 2_000_000, DEFAULT_MODEL_CONTEXT_STRING_LIMIT_CHARS),
    ("grok-4.20", 2_000_000, DEFAULT_MODEL_CONTEXT_STRING_LIMIT_CHARS),
)


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _model_context_limit_entry(model: Any) -> tuple[int, int] | None:
    if not isinstance(model, str):
        return None
    lower = model.strip().lower()
    if not lower:
        return None
    for prefix, token_limit, string_limit in MODEL_CONTEXT_LIMIT_PREFIXES:
        if lower.startswith(prefix):
            return token_limit, string_limit
    return (
        _positive_int_env("LUMIBOT_AGENT_DEFAULT_CONTEXT_LIMIT_TOKENS", DEFAULT_MODEL_CONTEXT_LIMIT_TOKENS),
        _positive_int_env("LUMIBOT_AGENT_DEFAULT_CONTEXT_STRING_LIMIT_CHARS", DEFAULT_MODEL_CONTEXT_STRING_LIMIT_CHARS),
    )


def _model_context_limit_tokens(model: Any) -> int | None:
    entry = _model_context_limit_entry(model)
    if entry is None:
        return None
    return entry[0]


def _model_context_string_limit_chars(model: Any) -> int | None:
    entry = _model_context_limit_entry(model)
    if entry is None:
        return None
    return entry[1]


def _truncate_preserving_edges(text: str, max_chars: int, *, label: str) -> str:
    if len(text) <= max_chars:
        return text
    head_chars = max(max_chars // 2, 0)
    tail_chars = max(max_chars - head_chars, 0)
    omitted = len(text) - head_chars - tail_chars
    notice = (
        f"\n\n[Lumibot input context pruned {omitted} characters from {label} "
        "to stay within the receiving model context window. Beginning and end "
        "are preserved.]\n\n"
    )
    if len(notice) >= max_chars:
        notice = f"\n[Pruned {omitted} chars from {label}.]\n"
    available = max(max_chars - len(notice), 0)
    head_chars = available // 2
    tail_chars = available - head_chars
    return f"{text[:head_chars]}{notice}{text[-tail_chars:] if tail_chars else ''}"


def _truncate_preserving_start(text: str, max_chars: int, *, label: str) -> str:
    if len(text) <= max_chars:
        return text
    notice = (
        f"\n\n[Lumibot input context pruned {len(text) - max_chars} trailing characters from {label} "
        "to stay within the receiving model context window. Beginning is preserved.]\n\n"
    )
    if len(notice) >= max_chars:
        notice = f"\n[Pruned trailing chars from {label}.]\n"
    available = max(max_chars - len(notice), 0)
    return f"{text[:available]}{notice}"


def _prune_large_context_strings(value: Any, *, max_string_chars: int, path: str = "context") -> tuple[Any, int]:
    if isinstance(value, str):
        if len(value) <= max_string_chars:
            return value, 0
        return _truncate_preserving_edges(value, max_string_chars, label=path), 1
    if isinstance(value, dict):
        pruned = 0
        output: dict[str, Any] = {}
        for key, item in value.items():
            child, child_pruned = _prune_large_context_strings(
                item,
                max_string_chars=max_string_chars,
                path=f"{path}.{key}",
            )
            output[str(key)] = child
            pruned += child_pruned
        return output, pruned
    if isinstance(value, list):
        pruned = 0
        output = []
        for idx, item in enumerate(value):
            child, child_pruned = _prune_large_context_strings(
                item,
                max_string_chars=max_string_chars,
                path=f"{path}[{idx}]",
            )
            output.append(child)
            pruned += child_pruned
        return output, pruned
    return value, 0


def _serialized_content_length(value: Any) -> int:
    try:
        if hasattr(value, "model_dump"):
            return len(json.dumps(_json_safe_value(value.model_dump(mode="json")), sort_keys=True, default=str))
        return len(json.dumps(_json_safe_value(value), sort_keys=True, default=str))
    except Exception:
        return len(repr(value))


def _request_contents_length(contents: list[Any]) -> int:
    return sum(_serialized_content_length(content) for content in contents)


def _part_has_function_response(part: Any) -> bool:
    return getattr(part, "function_response", None) is not None


def _function_response_payload_length(part: Any) -> int:
    function_response = getattr(part, "function_response", None)
    if function_response is None:
        return 0
    return _serialized_content_length(getattr(function_response, "response", None))


def _function_response_payload_is_pruned(part: Any) -> bool:
    function_response = getattr(part, "function_response", None)
    if function_response is None:
        return False
    response = getattr(function_response, "response", None)
    if isinstance(response, dict):
        return response.get("lumibot_context_pruned") is True
    return False


def _replace_function_response_payload(part: Any, message: str) -> bool:
    function_response = getattr(part, "function_response", None)
    if function_response is None:
        return False
    original_response = getattr(function_response, "response", None)
    original_chars = _serialized_content_length(original_response)
    replacement = {
        "lumibot_context_pruned": True,
        "message": message,
    }
    if _serialized_content_length(replacement) >= original_chars:
        return False
    try:
        function_response.response = replacement
        return True
    except Exception:
        return False


def _model_facing_summary_for_tool_response(
    tool_response: Any,
    *,
    tool_name: str | None,
) -> Any | None:
    if tool_name != "execution_plan_execute":
        return None
    if not isinstance(tool_response, dict):
        return None
    summary = tool_response.get("model_facing_summary")
    if not isinstance(summary, dict):
        return None
    if summary.get("response_type") != "model_facing_summary":
        return None
    return summary


def _is_pruned_tool_response_envelope(tool_response: Any) -> bool:
    return (
        isinstance(tool_response, dict)
        and tool_response.get("lumibot_tool_result_pruned") is True
    )


def _market_history_tables_summary_excerpt(
    tool_response: Any,
    *,
    max_chars: int,
) -> str | None:
    if not isinstance(tool_response, dict):
        return None
    result = _json_safe_value(tool_response)
    if not isinstance(result, dict):
        return None

    candidate_rows = result.get("candidate_summary")
    ranking_details = result.get("ranking_details")
    rankings = result.get("rankings")
    candidate_count = len(candidate_rows) if isinstance(candidate_rows, list) else None
    detail_limit = 3
    candidate_limit = min(candidate_count or 0, 10)
    use_ranking_metadata = False

    def limited_rankings() -> Any:
        if not use_ranking_metadata or not isinstance(rankings, dict):
            return rankings
        return {
            str(name): {
                "count": len(symbols) if isinstance(symbols, list) else None,
                "top_symbols": symbols[:3] if isinstance(symbols, list) else symbols,
            }
            for name, symbols in rankings.items()
        }

    def limited_ranking_details() -> Any:
        if not isinstance(ranking_details, dict):
            return ranking_details
        return {
            str(name): entries[:detail_limit] if isinstance(entries, list) else entries
            for name, entries in ranking_details.items()
        }

    def payload() -> dict[str, Any]:
        excerpt_payload: dict[str, Any] = {
            "schema_version": result.get("schema_version"),
            "coverage": result.get("coverage"),
            "rank_groups": result.get("rank_groups"),
            "ranking_limit": result.get("ranking_limit"),
            "candidate_summary_limit": result.get("candidate_summary_limit"),
            "rankings": limited_rankings(),
            "ranking_details": limited_ranking_details(),
            "candidate_summary": (
                candidate_rows[:candidate_limit]
                if isinstance(candidate_rows, list)
                else candidate_rows
            ),
        }
        if isinstance(ranking_details, dict):
            excerpt_payload["ranking_details_excerpt"] = {
                "included_per_ranking": detail_limit,
                "ranking_count": len(ranking_details),
                "truncated": any(
                    isinstance(entries, list) and len(entries) > detail_limit
                    for entries in ranking_details.values()
                ),
            }
        if isinstance(candidate_rows, list):
            excerpt_payload["candidate_summary_excerpt"] = {
                "included": candidate_limit,
                "available": len(candidate_rows),
                "truncated": len(candidate_rows) > candidate_limit,
            }
        if use_ranking_metadata and isinstance(rankings, dict):
            excerpt_payload["rankings_excerpt"] = {
                "mode": "top_symbol_metadata",
                "ranking_count": len(rankings),
                "truncated": True,
            }
        return excerpt_payload

    while True:
        serialized = json.dumps(payload(), sort_keys=False, separators=(",", ":"), default=str)
        if len(serialized) <= max_chars:
            return serialized
        if detail_limit > 1:
            detail_limit -= 1
            continue
        if candidate_limit > 1:
            candidate_limit = max(1, candidate_limit // 2)
            continue
        if not use_ranking_metadata:
            use_ranking_metadata = True
            continue
        return _truncate_preserving_start(
            serialized,
            max_chars,
            label="tool_response.market_load_history_tables_summary",
        )


def _prune_tool_response_for_context_window(
    tool_response: Any,
    *,
    tool_name: str | None,
    max_chars: int = 4_000,
) -> Any | None:
    model_facing_summary = _model_facing_summary_for_tool_response(
        tool_response,
        tool_name=tool_name,
    )
    if model_facing_summary is not None:
        return model_facing_summary
    if tool_name == "market_load_history_tables_summary":
        max_chars = max(max_chars, 6_000)
    response_chars = _serialized_content_length(tool_response)
    if response_chars <= max_chars:
        return None
    preserve_summary_order = tool_name == "market_load_history_tables_summary"
    market_history_excerpt = (
        _market_history_tables_summary_excerpt(tool_response, max_chars=max_chars)
        if preserve_summary_order
        else None
    )
    serialized = json.dumps(
        _json_safe_value(tool_response),
        sort_keys=not preserve_summary_order,
        default=str,
    )
    if market_history_excerpt is not None:
        excerpt = market_history_excerpt
    elif preserve_summary_order:
        excerpt = _truncate_preserving_start(
            serialized,
            max_chars,
            label=f"tool_response.{tool_name or 'unknown'}",
        )
    else:
        excerpt = _truncate_preserving_edges(
            serialized,
            max_chars,
            label=f"tool_response.{tool_name or 'unknown'}",
        )
    return {
        "lumibot_tool_result_pruned": True,
        "tool_name": tool_name,
        "original_chars": response_chars,
        "excerpt": excerpt,
        "message": (
            "Tool response was shortened by Lumibot before sending it back to this model "
            "because the provider context window would otherwise be exceeded. Call a targeted tool "
            "again if more detail is required."
        ),
    }


def _prune_request_contents_for_context_window(
    contents: list[Any],
    *,
    context_limit_tokens: int,
    reserve_ratio: float = 3.0,
    preserve_recent_tool_results: int = 4,
    always_prune_older_tool_results: bool = False,
) -> dict[str, Any] | None:
    """Trim oversized historical tool results before provider context failure.

    This is input-side context pruning. It leaves the Lumibot system prompt,
    task, tool declarations, function-call sequence, and most recent tool
    results intact. Only older tool-result payloads are replaced, and only when
    the serialized request is already larger than a conservative provider-window
    budget.
    """
    if not contents:
        return None

    # The registry stores provider limits in tokens while this guard only has a
    # cheap serialized-character estimate. Use a conservative character budget
    # instead of pruning at a tiny percentage of the true token window.
    max_chars = int(context_limit_tokens * reserve_ratio)
    before_chars = _request_contents_length(contents)

    tool_response_parts: list[Any] = []
    for content in contents:
        for part in getattr(content, "parts", None) or []:
            if _part_has_function_response(part):
                tool_response_parts.append(part)

    should_prune_for_size = before_chars > max_chars
    should_prune_for_history = (
        always_prune_older_tool_results
        and len(tool_response_parts) > preserve_recent_tool_results
    )
    if not should_prune_for_size and not should_prune_for_history:
        return None

    if len(tool_response_parts) <= preserve_recent_tool_results:
        return None

    pruned = 0
    replacement_message = (
        "Older tool result omitted by Lumibot before this model call because "
        "the provider context window would otherwise be exceeded. Use the most "
        "recent visible tool results or call a targeted tool again if this older "
        "detail is still required."
    )
    candidates = [
        part
        for part in tool_response_parts[: -preserve_recent_tool_results]
        if not _function_response_payload_is_pruned(part)
    ]
    candidates.sort(key=_function_response_payload_length, reverse=True)
    for part in candidates:
        if should_prune_for_size and _request_contents_length(contents) <= max_chars:
            break
        if _replace_function_response_payload(part, replacement_message):
            pruned += 1

    after_chars = _request_contents_length(contents)
    if pruned <= 0:
        return None
    return {
        "type": "provider_context_pruning",
        "pruned_tool_results": pruned,
        "before_chars": before_chars,
        "after_chars": after_chars,
        "max_chars": max_chars,
        "reasons": [
            reason
            for reason, applies in (
                ("provider_context_window", should_prune_for_size),
                (
                    "older_tool_result_history_limit",
                    should_prune_for_history,
                ),
            )
            if applies
        ],
        "notice": replacement_message,
    }


def _json_payload_byte_count(value: Any) -> int:
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
    except Exception:
        return 0


def _request_pruning_snapshot(llm_request: Any) -> dict[str, Any]:
    request_payload = _adk_object_payload(llm_request)
    contents = getattr(llm_request, "contents", None)
    contents_payload = _adk_object_payload(contents or [])
    content_count = 0
    part_count = 0
    function_response_count = 0
    function_response_bytes = 0
    notices: list[str] = []
    try:
        content_count = len(contents or [])
        for content in contents or []:
            parts = getattr(content, "parts", None) or []
            part_count += len(parts)
            for part in parts:
                function_response = getattr(
                    part,
                    "function_response",
                    None,
                )
                if function_response is None:
                    continue
                function_response_count += 1
                response = getattr(
                    function_response,
                    "response",
                    None,
                )
                function_response_bytes += _json_payload_byte_count(
                    _adk_object_payload(response)
                )
                if (
                    isinstance(response, dict)
                    and response.get("lumibot_context_pruned")
                    and isinstance(response.get("message"), str)
                ):
                    notices.append(response["message"])
    except Exception:
        pass
    return {
        "request": request_payload,
        "contents": contents_payload,
        "content_count": content_count,
        "part_count": part_count,
        "function_response_count": function_response_count,
        "function_response_bytes": function_response_bytes,
        "request_bytes": _json_payload_byte_count(request_payload),
        "contents_bytes": _json_payload_byte_count(contents_payload),
        "notices": notices,
    }


def _copy_content_without_thought_parts(content: Any) -> tuple[Any, bool]:
    parts = getattr(content, "parts", None)
    if not parts:
        return content, False
    filtered_parts = [part for part in parts if not getattr(part, "thought", False)]
    if len(filtered_parts) == len(parts):
        return content, False
    if hasattr(content, "model_copy"):
        return content.model_copy(update={"parts": filtered_parts}), True
    try:
        return type(content)(role=getattr(content, "role", None), parts=filtered_parts), True
    except Exception:
        content.parts = filtered_parts
        return content, True


def _strip_thought_parts_from_litellm_request(llm_request: Any) -> None:
    contents = getattr(llm_request, "contents", None)
    if not contents:
        return
    updated = []
    changed = False
    for content in contents:
        clean_content, content_changed = _copy_content_without_thought_parts(content)
        updated.append(clean_content)
        changed = changed or content_changed
    if changed:
        llm_request.contents = updated


def _is_native_gemini_model(model: Any) -> bool:
    if not isinstance(model, str):
        return False
    lower = model.strip().lower()
    return lower.startswith("gemini-") or lower.startswith("models/gemini")


def _record_native_litellm_boundaries_not_applicable(
    collector: BoundaryTraceCollector | None,
) -> None:
    payload = {
        "fidelity": "not_applicable",
        "reason": "native_model_path_does_not_use_litellm",
        "runtime_path": "native_google_adk_gemini",
    }
    for transition, from_module, to_module in (
        ("B01_PROVIDER_TO_LITELLM", "provider", "litellm"),
        ("B02_LITELLM_TO_ADK", "litellm", "google_adk"),
        ("B09_ADK_TO_LITELLM", "google_adk", "litellm"),
        ("B10_LITELLM_TO_PROVIDER", "litellm", "provider"),
    ):
        _record_tool_boundary(
            collector,
            transition=transition,
            from_module=from_module,
            to_module=to_module,
            status="not_applicable",
            payload=payload,
            payload_fidelity="not_applicable",
        )


def _record_litellm_boundaries_not_available(
    collector: BoundaryTraceCollector | None,
    *,
    model_turn_id: str | None,
    reason: str,
) -> None:
    payload = {
        "fidelity": "not_available",
        "reason": reason,
        "runtime_path": "litellm_observation_unavailable",
    }
    for transition, from_module, to_module in (
        (
            "B10_LITELLM_TO_PROVIDER",
            "litellm",
            "provider_boundary_not_observed",
        ),
        (
            "B01_PROVIDER_TO_LITELLM",
            "provider_boundary_not_observed",
            "litellm",
        ),
    ):
        _record_tool_boundary(
            collector,
            transition=transition,
            from_module=from_module,
            to_module=to_module,
            status="not_available",
            model_turn_id=model_turn_id,
            payload=payload,
            payload_fidelity="not_available",
        )


def _build_observed_litellm_type(
    base_type: type[Any],
    *,
    on_model_entry: Callable[[Any], None],
    prepare_model_entry: Callable[[Any], None] | None = None,
    boundary_collector: BoundaryTraceCollector | None = None,
) -> type[Any]:
    """Build a request-local LiteLLM adapter with extensible entry hooks."""

    boundary_logger = (
        LiteLLMBoundaryLogger(boundary_collector)
        if boundary_collector is not None
        else None
    )
    dynamic_input_supported = supports_dynamic_input_callback()

    class RequestLocalLiteLLMStream:
        def __init__(
            self,
            delegate: Any,
            *,
            owner: Any,
            token: Any,
            capture_kwargs: dict[str, Any],
        ) -> None:
            self._delegate = delegate
            self._owner = owner
            self._token = token
            self._capture_kwargs = capture_kwargs
            self._iterator = None
            self._chunks: list[Any] = []
            self._terminal = False
            self._owner.register_stream(self)

        def __aiter__(self):
            return self

        def _aggregate_response(
            self,
            *,
            complete: bool,
        ) -> Any:
            chunks = list(self._chunks)
            try:
                import litellm

                response = litellm.stream_chunk_builder(
                    chunks=chunks
                )
            except BaseException as exc:
                _add_trace_diagnostic(
                    boundary_collector,
                    "litellm_stream_aggregate_failed",
                    exc,
                )
                return LiteLLMStreamAggregate(
                    complete=complete,
                    fidelity="stream_chunk_builder_raises",
                    chunks=tuple(chunks),
                )
            if response is not None:
                return response
            _add_trace_diagnostic(
                boundary_collector,
                "litellm_stream_aggregate_failed",
                "LiteLLM stream_chunk_builder returned None",
            )
            return LiteLLMStreamAggregate(
                complete=complete,
                fidelity="stream_chunk_builder_none",
                chunks=tuple(chunks),
            )

        def _release(self) -> None:
            self._chunks.clear()
            self._unregister()

        def buffered_chunk_count(self) -> int:
            return len(self._chunks)

        def _complete_success(self) -> None:
            if self._terminal:
                return
            self._terminal = True
            try:
                response = self._aggregate_response(
                    complete=True
                )
                boundary_logger.complete_success(
                    self._token,
                    response,
                    kwargs=self._capture_kwargs,
                    cache_source=self._delegate,
                )
            finally:
                self._release()

        def _complete_error(self, error: BaseException) -> None:
            if self._terminal:
                return
            self._terminal = True
            try:
                partial_response = (
                    self._aggregate_response(complete=False)
                    if self._chunks
                    else None
                )
                boundary_logger.complete_error(
                    self._token,
                    error,
                    kwargs=self._capture_kwargs,
                    cache_source=self._delegate,
                    partial_response_obj=partial_response,
                )
            finally:
                self._release()

        def _unregister(self) -> None:
            owner = self._owner
            if owner is None:
                return
            self._owner = None
            owner.unregister_stream(self)

        async def __anext__(self):
            if self._terminal:
                raise StopAsyncIteration
            try:
                if self._iterator is None:
                    self._iterator = self._delegate.__aiter__()
                chunk = await self._iterator.__anext__()
            except StopAsyncIteration:
                self._complete_success()
                raise
            except BaseException as exc:
                self._complete_error(exc)
                raise
            try:
                self._chunks.append(chunk)
            except BaseException as exc:
                _add_trace_diagnostic(
                    boundary_collector,
                    "litellm_stream_chunk_capture_failed",
                    exc,
                )
            return chunk

        async def _aclose(self) -> None:
            if self._terminal:
                return
            try:
                close = (
                    getattr(self._iterator, "aclose", None)
                    if self._iterator is not None
                    else None
                )
                if close is None:
                    close = getattr(self._delegate, "aclose", None)
                if close is not None:
                    await close()
            except BaseException as exc:
                self._complete_error(exc)
                raise
            self._complete_error(
                asyncio.CancelledError(
                    "LiteLLM response stream closed before exhaustion"
                )
            )

        async def aclose(self) -> None:
            await self._aclose()

        async def __aenter__(self):
            try:
                enter = getattr(self._delegate, "__aenter__", None)
                if enter is not None:
                    await enter()
            except BaseException as exc:
                self._complete_error(exc)
                raise
            return self

        async def __aexit__(
            self,
            exc_type: Any,
            exc: BaseException | None,
            traceback: Any,
        ) -> Any:
            try:
                exit_context = getattr(
                    self._delegate,
                    "__aexit__",
                    None,
                )
                result = (
                    await exit_context(exc_type, exc, traceback)
                    if exit_context is not None
                    else None
                )
            except BaseException as exit_error:
                self._complete_error(exit_error)
                raise
            self._complete_error(
                exc
                if exc is not None
                else asyncio.CancelledError(
                    "LiteLLM response stream context exited "
                    "before exhaustion"
                )
            )
            return result

        def __getattr__(self, name: str) -> Any:
            return getattr(self._delegate, name)

    def _is_async_stream(value: Any) -> bool:
        try:
            value_type = type(value)
            type.__getattribute__(value_type, "__aiter__")
        except BaseException:
            return False
        return True

    class RequestLocalLiteLLMClient:
        def __init__(self, delegate: Any) -> None:
            self._delegate = delegate
            self._active_streams: set[
                RequestLocalLiteLLMStream
            ] = set()

        def register_stream(
            self,
            stream: RequestLocalLiteLLMStream,
        ) -> None:
            self._active_streams.add(stream)

        def unregister_stream(
            self,
            stream: RequestLocalLiteLLMStream,
        ) -> None:
            self._active_streams.discard(stream)

        def active_stream_count(self) -> int:
            return len(self._active_streams)

        async def close_active_streams(self) -> None:
            errors: list[BaseException] = []
            for stream in tuple(self._active_streams):
                try:
                    await stream._aclose()
                except BaseException as exc:
                    errors.append(exc)
            if errors:
                raise errors[0]

        async def acompletion(
            self,
            model: Any,
            messages: Any,
            tools: Any,
            **kwargs: Any,
        ) -> Any:
            kwargs = _sanitize_litellm_completion_args_for_model(
                model=model,
                tools=tools,
                kwargs=kwargs,
            )
            capture_kwargs = {
                "model": model,
                "messages": messages,
                "tools": tools,
                **kwargs,
            }
            token = boundary_logger.begin_attempt(
                model,
                messages,
                capture_kwargs,
            )
            try:
                response = await self._delegate.acompletion(
                    model=model,
                    messages=messages,
                    tools=tools,
                    **kwargs,
                )
            except BaseException as exc:
                boundary_logger.complete_error(
                    token,
                    exc,
                    kwargs=capture_kwargs,
                )
                raise
            if (
                kwargs.get("stream") is True
                and _is_async_stream(response)
            ):
                return RequestLocalLiteLLMStream(
                    response,
                    owner=self,
                    token=token,
                    capture_kwargs=capture_kwargs,
                )
            boundary_logger.complete_success(
                token,
                response,
                kwargs=capture_kwargs,
            )
            return response

    class ObservedLiteLlm(base_type):
        async def generate_content_async(
            self,
            llm_request: Any,
            stream: bool = False,
        ):
            if prepare_model_entry is not None:
                prepare_model_entry(llm_request)
            try:
                on_model_entry(llm_request)
            except Exception:
                pass
            invocation_model = self
            invocation_args: dict[str, Any] | None = None
            request_local_client: RequestLocalLiteLLMClient | None = (
                None
            )
            invocation_turn_id = (
                boundary_collector.active_model_turn()
                if boundary_collector is not None
                else None
            )
            if boundary_logger is not None:
                try:
                    invocation_args = dict(self._additional_args)
                    callback_keys = [
                        "callbacks",
                        "success_callback",
                        "failure_callback",
                    ]
                    if dynamic_input_supported:
                        callback_keys.append("input_callback")
                    for callback_key in callback_keys:
                        existing = invocation_args.get(callback_key)
                        if existing is None:
                            callbacks = []
                        elif type(existing) in (list, tuple):
                            callbacks = list(existing)
                        else:
                            callbacks = [existing]
                        if callback_key in (
                            "input_callback",
                            "success_callback",
                            "failure_callback",
                        ):
                            callbacks.append(boundary_logger)
                        if callbacks or callback_key in invocation_args:
                            invocation_args[callback_key] = callbacks
                    metadata_value = invocation_args.get("metadata")
                    metadata = (
                        dict(metadata_value)
                        if type(metadata_value) is dict
                        else {}
                    )
                    metadata["lumibot_agent_run_id"] = (
                        boundary_collector.agent_run_id
                    )
                    metadata["lumibot_model_turn_id"] = (
                        invocation_turn_id
                    )
                    invocation_args["metadata"] = metadata
                    invocation_model = self.model_copy(deep=False)
                    invocation_model._additional_args = invocation_args
                    invocation_client = getattr(
                        invocation_model,
                        "llm_client",
                        None,
                    )
                    if invocation_client is not None:
                        request_local_client = (
                            RequestLocalLiteLLMClient(
                                invocation_client
                            )
                        )
                        invocation_model.llm_client = (
                            request_local_client
                        )
                except Exception as exc:
                    _add_trace_diagnostic(
                        boundary_collector,
                        "litellm_callback_composition_failed",
                        exc,
                    )
                    _record_litellm_boundaries_not_available(
                        boundary_collector,
                        model_turn_id=invocation_turn_id,
                        reason="litellm_callback_composition_failed",
                    )
                    invocation_model = self
            terminal_error: BaseException | None = None
            try:
                async for response in base_type.generate_content_async(
                    invocation_model,
                    llm_request,
                    stream=stream,
                ):
                    yield response
            except BaseException as exc:
                if not isinstance(exc, GeneratorExit):
                    terminal_error = exc
                raise
            finally:
                try:
                    if request_local_client is not None:
                        try:
                            await request_local_client.close_active_streams()
                        except BaseException as cleanup_error:
                            if terminal_error is None:
                                raise
                            _add_trace_diagnostic(
                                boundary_collector,
                                "litellm_stream_cleanup_failed",
                                cleanup_error,
                            )
                finally:
                    if (
                        boundary_logger is not None
                        and terminal_error is not None
                    ):
                        boundary_logger.complete_pending_error(
                            terminal_error,
                            model_turn_id=invocation_turn_id,
                        )

    ObservedLiteLlm.__name__ = f"Observed{base_type.__name__}"
    ObservedLiteLlm.__qualname__ = ObservedLiteLlm.__name__
    return ObservedLiteLlm


def _resolve_model_for_adk(
    model: Any,
    *,
    prompt_cache_key: str | None = None,
    model_request_timeout_seconds: float | None = None,
    model_entry_observer: Callable[[Any], None] | None = None,
    boundary_collector: BoundaryTraceCollector | None = None,
) -> Any:
    # Native Gemini IDs take ADK's fast path as plain strings. Any other
    # provider prefix (e.g. "openai/...", "xai/...", "anthropic/...") is
    # routed through google.adk.models.lite_llm.LiteLlm which normalizes
    # tool-call shapes and auth across ~100 providers via LiteLLM.
    if not isinstance(model, str):
        return model
    lower = model.strip().lower()
    if _is_native_gemini_model(model):
        return model
    if lower.startswith("xai/"):
        _sync_xai_api_key_alias()
    if lower.startswith("together_ai/"):
        _sync_together_api_key_alias()
    _configure_litellm_quietly()
    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as exc:
        raise ImportError(
            f"Agent model '{model}' requires the 'litellm' package. "
            "Install it with: pip install 'google-adk[extensions]' litellm"
        ) from exc

    class CerebrasLiteLlm(LiteLlm):
        async def generate_content_async(self, llm_request: Any, stream: bool = False):
            # ADK 2 preserves provider reasoning as Gemini thought parts and
            # LiteLLM serializes those back to `messages.*.reasoning_content`.
            # Cerebras rejects that nonstandard message field, so strip thought
            # parts before request conversion while preserving normal text and
            # tool-call history.
            _strip_thought_parts_from_litellm_request(llm_request)
            async for response in super().generate_content_async(llm_request, stream=stream):
                yield response

    kwargs: dict[str, Any] = {}
    resolved_timeout_seconds = _coerce_positive_timeout_seconds(model_request_timeout_seconds)
    if resolved_timeout_seconds is not None:
        # google.adk.models.lite_llm.LiteLlm forwards additional args to
        # LiteLLM's acompletion call. LiteLLM accepts timeout in seconds.
        kwargs["timeout"] = resolved_timeout_seconds
    if prompt_cache_key:
        if lower.startswith("openai/"):
            # OpenAI prompt caching is automatic for long shared prefixes. The
            # key improves routing stability and 24h is the documented maximum
            # extended retention value.
            kwargs["prompt_cache_key"] = prompt_cache_key
            kwargs["prompt_cache_retention"] = "24h"
        elif lower.startswith("xai/"):
            # xAI recommends x-grok-conv-id for Chat Completions cache routing.
            kwargs["headers"] = {"x-grok-conv-id": prompt_cache_key}
    is_cerebras_model = lower.startswith("cerebras/")
    model_type = CerebrasLiteLlm if is_cerebras_model else LiteLlm
    if (
        model_entry_observer is not None
        or boundary_collector is not None
    ):
        model_type = _build_observed_litellm_type(
            LiteLlm,
            on_model_entry=model_entry_observer or (lambda _request: None),
            prepare_model_entry=(
                _strip_thought_parts_from_litellm_request
                if is_cerebras_model
                else None
            ),
            boundary_collector=boundary_collector,
        )
    return model_type(model=model, **kwargs)


def _supports_explicit_temperature_for_adk_model(model: Any) -> bool:
    """Return True only for ADK-native models known to accept temperature.

    ADK's LiteLlm bridge forwards GenerateContentConfig fields to provider APIs.
    OpenAI GPT-5/reasoning-class models reject custom temperature values and only
    allow the provider default. Passing temperature=0.0 therefore breaks those
    models before the agent can run. Keep deterministic temperature only on the
    Gemini-native path; let LiteLLM providers use their provider defaults unless
    a future explicit per-provider compatibility layer is added.
    """
    return _is_native_gemini_model(model)


def _sanitize_litellm_completion_args_for_model(
    *,
    model: Any,
    tools: Any,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Return provider-compatible LiteLLM completion kwargs for known model quirks."""

    if not isinstance(model, str):
        return kwargs
    model_name = model.strip().lower()
    if "/" in model_name:
        model_name = model_name.rsplit("/", 1)[-1]
    if model_name != "gpt-5.6-luna":
        return kwargs
    sanitized = dict(kwargs)
    if tools:
        sanitized["reasoning_effort"] = "none"
    else:
        sanitized.pop("reasoning_effort", None)
    return sanitized


class GoogleADKRuntime:
    def __init__(self, mcp_servers: list[MCPServer] | None = None) -> None:
        self.mcp_servers = mcp_servers or []
        self._llm_agent_type: type[Any] | None = None
        self._runner_type: type[Any] | None = None
        self._genai_types: Any = None
        self._function_tool_type: Any = None
        self._google_genai_types: Any = None

    def _ensure_adk(self) -> tuple[type[Any], type[Any], Any, Any]:
        _configure_google_sdk_noise_filters()
        if (
            self._llm_agent_type is not None
            and self._runner_type is not None
            and self._genai_types is not None
            and self._function_tool_type is not None
        ):
            return self._llm_agent_type, self._runner_type, self._genai_types, self._function_tool_type

        llm_agent_module = importlib.import_module("google.adk.agents.llm_agent")
        runners_module = importlib.import_module("google.adk.runners")
        function_tool_module = importlib.import_module("google.adk.tools.function_tool")
        from google.genai import types as google_genai_types

        self._llm_agent_type = llm_agent_module.LlmAgent
        self._runner_type = runners_module.InMemoryRunner
        self._function_tool_type = function_tool_module.FunctionTool
        self._genai_types = google_genai_types
        self._google_genai_types = google_genai_types
        return self._llm_agent_type, self._runner_type, self._genai_types, self._function_tool_type

    @staticmethod
    def _maybe_build_gemini_thinking_planner(model: Any, genai_types: Any) -> Any | None:
        """Gemini 3 thought text only shows up when ADK thinking is enabled via
        BuiltInPlanner, not GenerateContentConfig.

        BotSpot already uses this path successfully. LumiBot originally tried to
        set `ThinkingConfig(include_thoughts=True)` inside
        `GenerateContentConfig`, which still yielded thought token counts but not
        explicit thought parts in normalized events. This helper mirrors the
        BotSpot pattern so real thought parts can flow through `_normalize_event`.
        """
        if not isinstance(model, str):
            return None
        lower_model = model.strip().lower()
        if not lower_model.startswith("gemini-3"):
            return None
        thinking_config_type = getattr(genai_types, "ThinkingConfig", None)
        if thinking_config_type is None:
            return None
        try:
            planners_module = importlib.import_module("google.adk.planners")
        except ImportError:
            return None
        planner_type = getattr(planners_module, "BuiltInPlanner", None)
        if planner_type is None:
            return None
        try:
            thinking_config = thinking_config_type(include_thoughts=True)
            return planner_type(thinking_config=thinking_config)
        except Exception:
            return None

    def _instruction_for(self, request: RuntimeRequest) -> str:
        lines = [request.system_prompt.strip()]
        lines.append("")
        lines.append("General rules:")
        lines.append("- Use tools for structured data and trading actions.")
        if request.bound_tools:
            available_tool_names = {tool.name for tool in request.bound_tools}
            tool_names = ", ".join(sorted(available_tool_names))
            lines.append(f"- Available tool names for this run: {tool_names}.")
            lines.append("- Only call tool names that appear in the available tool list for this run.")
        lines.append("- Return a short final summary after you finish using tools.")
        return "\n".join(lines).strip()

    def _before_model_context_pruning_callback(self, request: RuntimeRequest):
        context_limit = _model_context_limit_tokens(request.model)
        collector = request.boundary_collector
        trace_litellm_entry = (
            collector is not None
            and not _is_native_gemini_model(request.model)
        )
        if not context_limit and not trace_litellm_entry:
            return None

        def _callback(*args: Any, callback_context: Any = None, llm_request: Any = None, **_kwargs: Any) -> None:
            if llm_request is None and len(args) >= 2:
                llm_request = args[1]
            contents = getattr(llm_request, "contents", None)
            before_snapshot: dict[str, Any] = {}
            if trace_litellm_entry:
                try:
                    before_snapshot = _request_pruning_snapshot(
                        llm_request
                    )
                except Exception as exc:
                    _add_trace_diagnostic(
                        collector,
                        "pre_pruning_request_capture_failed",
                        exc,
                    )
            pruning = None
            if isinstance(contents, list) and context_limit:
                pruning = _prune_request_contents_for_context_window(
                    contents,
                    context_limit_tokens=context_limit,
                    always_prune_older_tool_results=True,
                )
            if pruning:
                logging.getLogger(__name__).warning(
                    "Pruned %s older tool result payload(s) for model=%s before provider context window overflow "
                    "(request chars %s -> %s, budget %s).",
                    pruning["pruned_tool_results"],
                    request.model,
                    pruning["before_chars"],
                    pruning["after_chars"],
                    pruning["max_chars"],
                )
            if trace_litellm_entry:
                try:
                    after_snapshot = _request_pruning_snapshot(
                        llm_request
                    )
                    previous_model_turn_id = (
                        collector.active_model_turn()
                    )
                    model_turn_id = collector.start_model_turn()
                    collector.set_active_model_turn(model_turn_id)
                    reasons = (
                        list(pruning.get("reasons") or [])
                        if pruning
                        else []
                    )
                    notices = list(
                        dict.fromkeys(
                            [
                                *(
                                    [pruning.get("notice")]
                                    if pruning
                                    and pruning.get("notice")
                                    else []
                                ),
                                *(
                                    after_snapshot.get("notices")
                                    or []
                                ),
                            ]
                        )
                    )
                    context_pruning = {
                        "pruned": bool(pruning),
                        "pruned_tool_results": (
                            int(
                                pruning.get(
                                    "pruned_tool_results",
                                    0,
                                )
                            )
                            if pruning
                            else 0
                        ),
                        "omitted_function_response_count": (
                            int(
                                pruning.get(
                                    "pruned_tool_results",
                                    0,
                                )
                            )
                            if pruning
                            else 0
                        ),
                        "omitted_function_response_bytes": max(
                            int(
                                before_snapshot.get(
                                    "function_response_bytes",
                                    0,
                                )
                            )
                            - int(
                                after_snapshot.get(
                                    "function_response_bytes",
                                    0,
                                )
                            ),
                            0,
                        ),
                        "reason": (
                            reasons[0]
                            if len(reasons) == 1
                            else (
                                "multiple_pruning_reasons"
                                if reasons
                                else "not_pruned"
                            )
                        ),
                        "reasons": reasons,
                        "notices": notices,
                        "before": before_snapshot,
                        "after": after_snapshot,
                        "max_chars": (
                            pruning.get("max_chars")
                            if pruning
                            else (
                                int(context_limit * 3.0)
                                if context_limit
                                else None
                            )
                        ),
                    }
                    collector.note_pending_model_turn(
                        model_turn_id,
                        previous_model_turn_id=(
                            previous_model_turn_id
                        ),
                        context_pruning=context_pruning,
                        adk_invocation_id=_safe_optional_string(
                            _safe_attribute(
                                callback_context,
                                "invocation_id",
                            )
                        ),
                    )
                except Exception as exc:
                    _add_trace_diagnostic(
                        collector,
                        "pruning_forensics_capture_failed",
                        exc,
                    )
            return None

        return _callback

    def _before_model_boundary_callback(self, request: RuntimeRequest):
        collector = request.boundary_collector
        if collector is None:
            return None

        def _callback(
            *args: Any,
            callback_context: Any = None,
            llm_request: Any = None,
            **_kwargs: Any,
        ) -> None:
            if llm_request is None and len(args) >= 2:
                llm_request = args[1]
            try:
                active_model_turn_id = collector.active_model_turn()
                pending = (
                    collector.pending_model_turn(
                        active_model_turn_id
                    )
                    if active_model_turn_id
                    else {}
                )
                if pending:
                    collector.note_pending_model_turn(
                        active_model_turn_id,
                        previous_model_turn_id=pending.get(
                            "previous_model_turn_id"
                        ),
                        context_pruning=pending.get(
                            "context_pruning"
                        )
                        or {},
                        adk_invocation_id=(
                            _safe_optional_string(
                                _safe_attribute(
                                    callback_context,
                                    "invocation_id",
                                )
                            )
                            or pending.get("adk_invocation_id")
                        ),
                    )
                    return None
                previous_model_turn_id = active_model_turn_id
                model_turn_id = collector.start_model_turn()
                collector.set_active_model_turn(model_turn_id)
                pruned_parts = 0
                for content in (
                    getattr(llm_request, "contents", None) or []
                ):
                    for part in getattr(content, "parts", None) or []:
                        response = getattr(
                            getattr(
                                part,
                                "function_response",
                                None,
                            ),
                            "response",
                            None,
                        )
                        if isinstance(response, dict) and response.get(
                            "lumibot_context_pruned"
                        ):
                            pruned_parts += 1
                if not _is_native_gemini_model(request.model):
                    collector.note_pending_model_turn(
                        model_turn_id,
                        previous_model_turn_id=previous_model_turn_id,
                        context_pruning={
                            "pruned": pruned_parts > 0,
                            "pruned_tool_results": pruned_parts,
                            "omitted_function_response_count": (
                                pruned_parts
                            ),
                            "omitted_function_response_bytes": 0,
                            "reason": (
                                "previous_pruning_notice_observed"
                                if pruned_parts
                                else "not_pruned"
                            ),
                            "reasons": [],
                            "notices": [],
                        },
                        adk_invocation_id=_safe_optional_string(
                            getattr(
                                callback_context,
                                "invocation_id",
                                None,
                            )
                        ),
                    )
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "before_model_turn_observation_failed",
                    exc,
                )
            return None

        return _callback

    def _model_entry_boundary_observer(
        self,
        request: RuntimeRequest,
    ) -> Callable[[Any], None]:
        collector = request.boundary_collector

        def _observe(llm_request: Any) -> None:
            if collector is None:
                return
            try:
                model_turn_id = collector.active_model_turn()
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "model_entry_active_turn_lookup_failed",
                    exc,
                )
                model_turn_id = None
            if not model_turn_id:
                try:
                    model_turn_id = collector.start_model_turn()
                    collector.set_active_model_turn(model_turn_id)
                    collector.note_pending_model_turn(
                        model_turn_id,
                        previous_model_turn_id=None,
                        context_pruning={
                            "pruned": False,
                            "pruned_tool_results": 0,
                        },
                    )
                    _add_trace_diagnostic(
                        collector,
                        "generated_missing_active_model_turn",
                        model_turn_id,
                    )
                except Exception as exc:
                    _add_trace_diagnostic(
                        collector,
                        "model_entry_turn_generation_failed",
                        exc,
                    )
                    return
            try:
                pending = collector.take_pending_model_turn(model_turn_id)
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "pending_model_turn_lookup_failed",
                    exc,
                )
                pending = {}
            _record_tool_boundary(
                collector,
                transition="B09_ADK_TO_LITELLM",
                from_module="google_adk",
                to_module="litellm",
                adk_invocation_id=pending.get("adk_invocation_id"),
                model_turn_id=model_turn_id,
                payload={
                    "llm_request": _adk_object_payload(llm_request),
                    "context_pruning": pending.get(
                        "context_pruning"
                    )
                    or {
                        "pruned": False,
                        "pruned_tool_results": 0,
                    },
                    "previous_model_turn_id": pending.get(
                        "previous_model_turn_id"
                    ),
                    "current_model_turn_id": model_turn_id,
                    "capture_point": (
                        "observed_litellm_"
                        "generate_content_async_entry"
                    ),
                },
            )

        return _observe

    def _after_model_boundary_callback(self, request: RuntimeRequest):
        collector = request.boundary_collector
        if collector is None or _is_native_gemini_model(request.model):
            return None

        def _callback(
            *args: Any,
            callback_context: Any = None,
            llm_response: Any = None,
            **_kwargs: Any,
        ) -> None:
            if llm_response is None and len(args) >= 2:
                llm_response = args[1]
            try:
                model_turn_id = collector.active_model_turn()
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "active_model_turn_lookup_failed",
                    exc,
                )
                model_turn_id = None
            if not model_turn_id:
                try:
                    model_turn_id = collector.start_model_turn()
                    collector.set_active_model_turn(model_turn_id)
                    _add_trace_diagnostic(
                        collector,
                        "generated_missing_active_model_turn",
                        model_turn_id,
                    )
                except Exception as exc:
                    _add_trace_diagnostic(
                        collector,
                        "active_model_turn_generation_failed",
                        exc,
                    )
                    model_turn_id = None

            call_records: list[dict[str, Any]] = []
            call_ids: list[str] = []
            try:
                function_calls = _parts_function_calls(llm_response)
                for index, function_call in enumerate(
                    function_calls,
                    start=1,
                ):
                    try:
                        raw_call_id = getattr(
                            function_call,
                            "id",
                            None,
                        )
                        provider_call_id = (
                            str(raw_call_id)
                            if raw_call_id
                            else None
                        )
                    except Exception as exc:
                        _add_trace_diagnostic(
                            collector,
                            "provider_call_id_observation_failed",
                            exc,
                        )
                        provider_call_id = None
                    if provider_call_id:
                        trace_call_id = provider_call_id
                        call_id_source = "provider"
                    else:
                        turn_segment = model_turn_id or "missing_turn"
                        trace_call_id = (
                            f"generated:{turn_segment}:{index:04d}:"
                            f"{uuid4().hex}"
                        )
                        call_id_source = (
                            "generated_missing_provider_id"
                        )
                        _add_trace_diagnostic(
                            collector,
                            "generated_missing_provider_call_id",
                            trace_call_id,
                        )
                    function_name = getattr(
                        function_call,
                        "name",
                        None,
                    )
                    function_arguments = getattr(
                        function_call,
                        "args",
                        None,
                    )
                    call_ids.append(trace_call_id)
                    call_records.append(
                        {
                            "call_id": trace_call_id,
                            "trace_call_id": trace_call_id,
                            "provider_call_id": provider_call_id,
                            "provider_runtime_call_id": (
                                provider_call_id
                            ),
                            "provider_runtime_alias_relation": (
                                "same_as_trace_call_id"
                                if provider_call_id
                                else None
                            ),
                            "call_id_source": call_id_source,
                            "name": function_name,
                            "arguments": _adk_object_payload(
                                function_arguments
                            ),
                            "call_fingerprint": (
                                _tool_call_fingerprint(
                                    function_name,
                                    function_arguments,
                                )
                            ),
                            "call_sequence": index,
                        }
                    )
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "after_model_tool_call_capture_failed",
                    exc,
                )

            batch_id = None
            if model_turn_id and call_ids:
                try:
                    registered_batch = collector.register_tool_calls(
                        model_turn_id,
                        [
                            {
                                "trace_call_id": call_record[
                                    "trace_call_id"
                                ],
                                "provider_call_id": call_record[
                                    "provider_call_id"
                                ],
                                "provider_runtime_call_id": (
                                    call_record[
                                        "provider_runtime_call_id"
                                    ]
                                ),
                                "provider_runtime_alias_relation": (
                                    call_record[
                                        "provider_runtime_alias_relation"
                                    ]
                                ),
                                "call_id_source": call_record[
                                    "call_id_source"
                                ],
                                "tool_name": call_record["name"],
                                "call_fingerprint": call_record[
                                    "call_fingerprint"
                                ],
                            }
                            for call_record in call_records
                        ],
                    )
                    batch_id = registered_batch["tool_batch_id"]
                    for call_record, registered_call in zip(
                        call_records,
                        registered_batch["calls"],
                    ):
                        call_record.update(
                            {
                                "call_instance_id": registered_call[
                                    "call_instance_id"
                                ],
                                "tool_batch_id": batch_id,
                            }
                        )
                except Exception as exc:
                    _add_trace_diagnostic(
                        collector,
                        "tool_batch_registration_failed",
                        exc,
                    )
            _record_tool_boundary(
                collector,
                transition="B02_LITELLM_TO_ADK",
                from_module="litellm",
                to_module="google_adk",
                model_turn_id=model_turn_id,
                tool_batch_id=batch_id,
                payload={
                    "llm_response": _adk_object_payload(llm_response),
                    "provider_exposed_thought_parts": (
                        _provider_exposed_thought_parts(llm_response)
                    ),
                    "tool_calls": call_records,
                    "reasoning_visibility": (
                        "provider_exposed_only"
                    ),
                },
            )
            return None

        return _callback

    def _before_tool_boundary_callback(self, request: RuntimeRequest):
        collector = request.boundary_collector
        if collector is None:
            return None

        def _callback(
            *positional: Any,
            tool: Any = None,
            args: dict[str, Any] | None = None,
            tool_context: Any = None,
            **_kwargs: Any,
        ) -> None:
            dispatch_observed_at = _utc_iso_timestamp()
            dispatch_started_monotonic = time.perf_counter()
            if tool is None and positional:
                tool = positional[0]
            if args is None and len(positional) >= 2:
                args = positional[1]
            if tool_context is None and len(positional) >= 3:
                tool_context = positional[2]
            model_arguments = args if isinstance(args, dict) else {}
            try:
                raw_call_id = getattr(
                    tool_context,
                    "function_call_id",
                    None,
                )
                provider_call_id = (
                    str(raw_call_id) if raw_call_id else None
                )
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "adk_dispatch_call_id_observation_failed",
                    exc,
                )
                provider_call_id = None

            tool_name = _safe_optional_string(
                _safe_attribute(tool, "name")
            )
            call_fingerprint = _tool_call_fingerprint(
                tool_name,
                model_arguments,
            )
            source_function_call_event = (
                _source_function_call_event(
                    tool_context,
                    provider_call_id,
                )
                if provider_call_id
                else None
            )
            try:
                model_turn_id = collector.active_model_turn()
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "adk_dispatch_active_turn_lookup_failed",
                    exc,
                )
                model_turn_id = None
            try:
                ids = collector.claim_tool_call(
                    provider_runtime_call_id=provider_call_id,
                    tool_name=tool_name,
                    call_fingerprint=call_fingerprint,
                    model_turn_id=model_turn_id,
                    stage="dispatch",
                )
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "adk_dispatch_call_lookup_failed",
                    exc,
                )
                ids = {}
            if ids:
                call_id_source = (
                    ids.get("call_id_source")
                    or (
                        "provider"
                        if provider_call_id
                        else "generated_missing_adk_dispatch_id"
                    )
                )
                call_lookup_status = (
                    "reconciled_generated_fallback"
                    if (
                        provider_call_id
                        and ids.get("trace_call_id")
                        != provider_call_id
                    )
                    else "matched_batch"
                )
            else:
                trace_call_id = (
                    provider_call_id
                    or f"generated:adk_dispatch:{uuid4().hex}"
                )
                call_id_source = (
                    "unknown_nonempty_runtime_id"
                    if provider_call_id
                    else "generated_missing_adk_dispatch_id"
                )
                call_lookup_status = (
                    "unmatched" if provider_call_id else "generated"
                )
                if not provider_call_id:
                    _add_trace_diagnostic(
                        collector,
                        "generated_missing_adk_dispatch_call_id",
                        trace_call_id,
                    )
                try:
                    if not model_turn_id:
                        model_turn_id = collector.start_model_turn()
                        collector.set_active_model_turn(model_turn_id)
                        _add_trace_diagnostic(
                            collector,
                            "generated_missing_active_model_turn",
                            model_turn_id,
                        )
                    source_call_records = []
                    for index, source_call in enumerate(
                        _parts_function_calls(
                            source_function_call_event
                        ),
                        start=1,
                    ):
                        source_provider_call_id = (
                            _safe_optional_string(
                                _safe_attribute(
                                    source_call,
                                    "id",
                                )
                            )
                        )
                        source_trace_call_id = (
                            source_provider_call_id
                            or (
                                "generated:source_function_call:"
                                f"{index:04d}:{uuid4().hex}"
                            )
                        )
                        source_name = _safe_optional_string(
                            _safe_attribute(source_call, "name")
                        )
                        source_arguments = _safe_attribute(
                            source_call,
                            "args",
                        )
                        source_call_id_source = (
                            "adk_generated_missing_provider_id"
                            if (
                                source_provider_call_id
                                and source_provider_call_id.startswith(
                                    "adk-"
                                )
                            )
                            else (
                                "provider"
                                if source_provider_call_id
                                else (
                                    "generated_missing_"
                                    "source_event_call_id"
                                )
                            )
                        )
                        source_call_records.append(
                            {
                                "trace_call_id": (
                                    source_trace_call_id
                                ),
                                "provider_call_id": (
                                    source_provider_call_id
                                    if source_call_id_source
                                    == "provider"
                                    else None
                                ),
                                "provider_runtime_call_id": (
                                    source_provider_call_id
                                ),
                                "provider_runtime_alias_relation": (
                                    (
                                        "same_as_trace_call_id"
                                        if source_call_id_source
                                        == "provider"
                                        else (
                                            "adk_runtime_id_without_"
                                            "provider_id"
                                        )
                                    )
                                    if source_provider_call_id
                                    else None
                                ),
                                "call_id_source": (
                                    source_call_id_source
                                ),
                                "tool_name": source_name,
                                "call_fingerprint": (
                                    _tool_call_fingerprint(
                                        source_name,
                                        source_arguments,
                                    )
                                ),
                            }
                        )
                    registration_calls = (
                        source_call_records
                        if source_call_records
                        else [
                            {
                                "trace_call_id": trace_call_id,
                                "provider_call_id": None,
                                "provider_runtime_call_id": (
                                    provider_call_id
                                ),
                                "provider_runtime_alias_relation": (
                                    "runtime_id_without_provider_provenance"
                                    if provider_call_id
                                    else None
                                ),
                                "call_id_source": call_id_source,
                                "tool_name": tool_name,
                                "call_fingerprint": (
                                    call_fingerprint
                                ),
                            }
                        ]
                    )
                    registered = collector.register_tool_calls(
                        model_turn_id,
                        registration_calls,
                    )
                    call_instance_id = registered["calls"][0][
                        "call_instance_id"
                    ]
                    ids = collector.claim_tool_call(
                        provider_runtime_call_id=(
                            provider_call_id
                        ),
                        tool_name=tool_name,
                        call_fingerprint=call_fingerprint,
                        model_turn_id=model_turn_id,
                        stage="dispatch",
                    )
                    if not ids:
                        ids = collector.call_ids(
                            call_instance_id
                        )
                    call_id_source = (
                        ids.get("call_id_source")
                        or call_id_source
                    )
                    if source_call_records:
                        call_lookup_status = (
                            "reconstructed_source_event_batch"
                        )
                except Exception as exc:
                    _add_trace_diagnostic(
                        collector,
                        "adk_dispatch_fallback_registration_failed",
                        exc,
                    )
                    ids = {}
            trace_call_id = str(
                ids.get("trace_call_id")
                or provider_call_id
                or f"generated:adk_dispatch:{uuid4().hex}"
            )
            call_instance_id = _safe_optional_string(
                ids.get("call_instance_id")
            )
            if call_instance_id:
                try:
                    collector.set_active_call_instance(
                        call_instance_id
                    )
                    collector.note_dispatch_started(
                        call_instance_id,
                        dispatch_observed_at=dispatch_observed_at,
                        monotonic_started=(
                            dispatch_started_monotonic
                        ),
                    )
                except Exception as exc:
                    _add_trace_diagnostic(
                        collector,
                        "adk_dispatch_window_capture_failed",
                        exc,
                    )
            try:
                batch_call_ids = collector.batch_call_ids(
                    ids.get("tool_batch_id")
                )
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "adk_dispatch_batch_lookup_failed",
                    exc,
                )
                batch_call_ids = []
            parallel_batch = len(batch_call_ids) > 1
            adk_invocation_id = _safe_optional_string(
                _safe_attribute(tool_context, "invocation_id")
            )

            _record_tool_boundary(
                collector,
                transition="B03_ADK_TO_FUNCTION_TOOL",
                from_module="google_adk",
                to_module="function_tool",
                started_at=dispatch_observed_at,
                adk_invocation_id=adk_invocation_id,
                model_turn_id=ids.get("model_turn_id"),
                tool_batch_id=ids.get("tool_batch_id"),
                call_id=trace_call_id,
                call_instance_id=call_instance_id,
                payload={
                    "tool_name": tool_name,
                    "selected_function_tool": (
                        _safe_object_identity(tool)
                    ),
                    "trace_call_id": trace_call_id,
                    "call_instance_id": call_instance_id,
                    "provider_call_id": ids.get(
                        "provider_call_id"
                    ),
                    "provider_runtime_call_id": (
                        provider_call_id
                        or ids.get("provider_runtime_call_id")
                    ),
                    "provider_runtime_alias_relation": ids.get(
                        "provider_runtime_alias_relation"
                    ),
                    "provider_id_ambiguity": ids.get(
                        "provider_id_ambiguity"
                    ),
                    "call_id_source": call_id_source,
                    "call_lookup_status": call_lookup_status,
                    "model_arguments": model_arguments,
                    "call_sequence": ids.get("call_sequence"),
                    "parallel_batch": parallel_batch,
                    "batch_call_ids": batch_call_ids,
                    "source_function_call_event_id": (
                        _safe_optional_string(
                            _safe_attribute(
                                source_function_call_event,
                                "id",
                            )
                        )
                    ),
                    "dispatch_observed_at": dispatch_observed_at,
                    "dispatch_timestamp_source": (
                        "before_tool_callback_entry"
                    ),
                    "parallel_scheduling": (
                        _parallel_scheduling_evidence(
                            batch_call_ids
                        )
                    ),
                },
            )
            return None

        return _callback

    def _after_tool_context_pruning_callback(self, request: RuntimeRequest):
        if _model_context_limit_tokens(request.model) is None:
            return None

        def _callback(
            *args: Any,
            tool: Any = None,
            tool_response: Any = None,
            **_kwargs: Any,
        ) -> Any | None:
            if tool is None and len(args) >= 1:
                tool = args[0]
            if tool_response is None and len(args) >= 4:
                tool_response = args[3]
            tool_name = str(getattr(tool, "name", None) or "")
            pruned = _prune_tool_response_for_context_window(tool_response, tool_name=tool_name)
            if _is_pruned_tool_response_envelope(pruned):
                logging.getLogger(__name__).warning(
                    "Pruned oversized tool response for model=%s tool=%s original_chars=%s.",
                    request.model,
                    tool_name,
                    pruned["original_chars"],
                )
            return pruned

        return _callback

    def _after_tool_boundary_and_pruning_callback(
        self,
        request: RuntimeRequest,
    ):
        collector = request.boundary_collector
        pruning_callback = self._after_tool_context_pruning_callback(
            request
        )
        if collector is None:
            return pruning_callback

        def _callback(
            *positional: Any,
            tool: Any = None,
            args: dict[str, Any] | None = None,
            tool_context: Any = None,
            tool_response: Any = None,
            **_kwargs: Any,
        ) -> Any | None:
            response_created_at = _utc_iso_timestamp()
            if tool is None and positional:
                tool = positional[0]
            if args is None and len(positional) >= 2:
                args = positional[1]
            if tool_context is None and len(positional) >= 3:
                tool_context = positional[2]
            if tool_response is None and len(positional) >= 4:
                tool_response = positional[3]
            try:
                raw_call_id = getattr(
                    tool_context,
                    "function_call_id",
                    None,
                )
                provider_runtime_call_id = (
                    str(raw_call_id) if raw_call_id else None
                )
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "after_tool_call_id_observation_failed",
                    exc,
                )
                provider_runtime_call_id = None
            tool_name = _safe_optional_string(
                _safe_attribute(tool, "name")
            )
            call_fingerprint = _tool_call_fingerprint(
                tool_name,
                args if isinstance(args, dict) else {},
            )
            ids: dict[str, Any] = {}
            try:
                active_call_instance = (
                    collector.active_call_instance()
                )
                if active_call_instance:
                    ids = collector.call_ids(
                        active_call_instance
                    )
                if not ids:
                    ids = collector.claim_tool_call(
                        provider_runtime_call_id=(
                            provider_runtime_call_id
                        ),
                        tool_name=tool_name,
                        call_fingerprint=call_fingerprint,
                        model_turn_id=(
                            collector.active_model_turn()
                        ),
                        stage="after_tool",
                    )
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "after_tool_call_lookup_failed",
                    exc,
                )
            if not ids:
                trace_call_id = (
                    provider_runtime_call_id
                    or f"generated:after_tool:{uuid4().hex}"
                )
                call_id_source = (
                    "provider"
                    if provider_runtime_call_id
                    else "generated_missing_after_tool_id"
                )
                try:
                    model_turn_id = collector.active_model_turn()
                    if not model_turn_id:
                        model_turn_id = collector.start_model_turn()
                        collector.set_active_model_turn(model_turn_id)
                    registered = collector.register_tool_calls(
                        model_turn_id,
                        [
                            {
                                "trace_call_id": trace_call_id,
                                "provider_call_id": (
                                    provider_runtime_call_id
                                ),
                                "provider_runtime_call_id": (
                                    provider_runtime_call_id
                                ),
                                "provider_runtime_alias_relation": (
                                    "same_as_trace_call_id"
                                    if provider_runtime_call_id
                                    else None
                                ),
                                "call_id_source": call_id_source,
                                "tool_name": tool_name,
                                "call_fingerprint": (
                                    call_fingerprint
                                ),
                            }
                        ],
                    )
                    ids = collector.call_ids(
                        registered["calls"][0][
                            "call_instance_id"
                        ]
                    )
                except Exception as exc:
                    _add_trace_diagnostic(
                        collector,
                        "after_tool_fallback_registration_failed",
                        exc,
                    )
            call_id = str(
                ids.get("call_instance_id")
                or ids.get("trace_call_id")
                or provider_runtime_call_id
                or f"generated:after_tool:{uuid4().hex}"
            )
            if provider_runtime_call_id is None:
                _add_trace_diagnostic(
                    collector,
                    "generated_missing_after_tool_call_id",
                    str(ids.get("trace_call_id") or call_id),
                )

            try:
                collector.note_function_tool_response(
                    call_id,
                    unpruned_response=tool_response,
                    model_facing_response=tool_response,
                    pruned=False,
                    response_created_at=response_created_at,
                )
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "unpruned_function_tool_response_capture_failed",
                    exc,
                )

            pruned_response = None
            if pruning_callback is not None:
                pruned_response = pruning_callback(
                    tool=tool,
                    args=args,
                    tool_context=tool_context,
                    tool_response=tool_response,
                )
            model_facing_response = (
                pruned_response
                if pruned_response is not None
                else tool_response
            )
            try:
                collector.note_function_tool_response(
                    call_id,
                    unpruned_response=tool_response,
                    model_facing_response=model_facing_response,
                    pruned=pruned_response is not None,
                    response_created_at=response_created_at,
                )
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "function_tool_response_state_capture_failed",
                    exc,
                )
            try:
                collector.set_active_call_instance(None)
            except Exception as exc:
                _add_trace_diagnostic(
                    collector,
                    "after_tool_call_context_cleanup_failed",
                    exc,
                )
            return pruned_response

        return _callback

    def _build_user_text(self, request: RuntimeRequest) -> str:
        sections: list[str] = []
        tool_names = {tool.name for tool in request.bound_tools}
        if request.runtime_context:
            runtime_context = json.dumps(
                _json_safe_value(request.runtime_context),
                sort_keys=True,
                default=str,
            )
            sections.append(
                f"Runtime Context JSON:\n{runtime_context}"
            )
        if request.bound_tools:
            sections.append(
                "Available Tools JSON:\n"
                f"{json.dumps(sorted(tool_names), sort_keys=True, default=str)}"
            )
        if request.memory_state:
            sections.append(
                "Lumibot Memory State JSON:\n"
                f"{json.dumps(_json_safe_value(request.memory_state), sort_keys=True, default=str)}"
            )
        if request.memory_notes:
            memory_notes = _json_safe_value(request.memory_notes[-5:])
            context_string_limit = _model_context_string_limit_chars(request.model)
            if context_string_limit:
                memory_notes, memory_pruned = _prune_large_context_strings(
                    memory_notes,
                    max_string_chars=max(context_string_limit // 2, 1),
                    path="memory_notes",
                )
                if memory_pruned:
                    sections.append(f"Lumibot Context Notice:\nPruned {memory_pruned} oversized memory string(s).")
            sections.append(
                "Persistent Memory JSON:\n"
                f"{json.dumps(memory_notes, sort_keys=True, default=str)}"
            )
        if request.task_prompt:
            sections.append(f"Task:\n{request.task_prompt.strip()}")
        else:
            required_categories = [
                "account_positions or account_portfolio",
                "market_last_price or market_load_history_table",
                "duckdb_query after loading a price table",
                "get_indicator or get_indicators",
            ]
            if "alpaca_news" in tool_names:
                required_categories.append("alpaca_news")
            fred_tools = sorted(
                name
                for name in tool_names
                if name.startswith("get_fred_")
                or name == "list_fred_series"
            )
            if fred_tools:
                required_categories.append(" or ".join(fred_tools))
            required_categories.extend(
                [
                    "get_income_statement, get_balance_sheet, get_cash_flow, or get_company_facts",
                    "get_filings, search_filing, or get_filing_document",
                ]
            )
            sections.append(
                "Task:\n"
                "Do your normal job for the current market state. Before making a trading decision, use the available "
                "tools to review account/portfolio state, current market prices, recent price history, technical "
                "indicators, relevant news when configured, macro/FRED data when configured, and SEC financial/filing "
                "evidence for relevant single-stock candidates. Specifically, include calls from these available "
                f"categories: {'; '.join(required_categories)}. "
                "In backtests, date-bound every external data request to the current simulated datetime and do not use "
                "future information."
            )
        if request.context:
            context_payload = _json_safe_value(request.context)
            context_string_limit = _model_context_string_limit_chars(request.model)
            if context_string_limit:
                context_payload, context_pruned = _prune_large_context_strings(
                    context_payload,
                    max_string_chars=context_string_limit,
                    path="context",
                )
                if context_pruned:
                    sections.append(f"Lumibot Context Notice:\nPruned {context_pruned} oversized context string(s).")
            sections.append(f"User Context JSON:\n{json.dumps(context_payload, sort_keys=True, default=str)}")
        return "\n\n".join(sections)

    async def _run_async(self, request: RuntimeRequest) -> AgentRunResult:
        started_at = _utc_iso_timestamp()
        started_perf = time.perf_counter()
        first_event_at: str | None = None
        first_event_perf: float | None = None
        LlmAgentType, InMemoryRunnerType, genai_types, function_tool_type = self._ensure_adk()
        run_config_module = importlib.import_module("google.adk.agents.run_config")
        tool_name_map = {_tool_function_name(tool.name): tool.name for tool in request.bound_tools}
        active_tool_context = {
            "agent_name": request.agent_name,
            "model_call_id": request.model_call_id,
            "enforce_order_readiness": True,
            "tool_calls": [],
        }
        tools = [
            _build_observed_function_tool(
                function_tool_type,
                tool,
                collector=request.boundary_collector,
                shared_tool_context=active_tool_context,
            )
            for tool in request.bound_tools
        ]
        config_kwargs = self._generate_content_config_kwargs_for_request(request, genai_types)
        model_request_timeout_seconds = self._model_request_timeout_seconds_for_request(request)
        run_timeout_seconds = self._run_timeout_seconds_for_request(request)
        model_timeout_label = (
            f"{model_request_timeout_seconds:g}s" if model_request_timeout_seconds is not None else "disabled"
        )
        run_timeout_label = f"{run_timeout_seconds:g}s" if run_timeout_seconds is not None else "disabled"
        try:
            sys.stderr.write(
                f"[lumibot.agents] starting agent '{request.agent_name}' "
                f"(model={request.model!r}, model_request_timeout={model_timeout_label}, "
                f"run_timeout={run_timeout_label}).\n"
            )
            sys.stderr.flush()
        except Exception:
            pass
        planner = self._maybe_build_gemini_thinking_planner(request.model, genai_types)
        uses_litellm_boundaries = not _is_native_gemini_model(
            request.model
        )
        if not uses_litellm_boundaries:
            _record_native_litellm_boundaries_not_applicable(
                request.boundary_collector
            )
        before_model_callbacks = [
            callback
            for callback in (
                self._before_model_context_pruning_callback(request),
                self._before_model_boundary_callback(request),
            )
            if callback is not None
        ]
        agent = LlmAgentType(
            name=request.agent_name,
            model=_resolve_model_for_adk(
                request.model,
                prompt_cache_key=request.provider_prompt_cache_key or _provider_prompt_cache_key(request),
                model_request_timeout_seconds=model_request_timeout_seconds,
                model_entry_observer=(
                    self._model_entry_boundary_observer(request)
                    if (
                        request.boundary_collector is not None
                        and uses_litellm_boundaries
                    )
                    else None
                ),
                boundary_collector=(
                    request.boundary_collector
                    if uses_litellm_boundaries
                    else None
                ),
            ),
            instruction=self._instruction_for(request),
            tools=tools,
            generate_content_config=genai_types.GenerateContentConfig(**config_kwargs),
            planner=planner,
            before_model_callback=before_model_callbacks or None,
            after_model_callback=(
                self._after_model_boundary_callback(request)
                if uses_litellm_boundaries
                else None
            ),
            before_tool_callback=self._before_tool_boundary_callback(request),
            after_tool_callback=(
                self._after_tool_boundary_and_pruning_callback(request)
            ),
        )
        runner = InMemoryRunnerType(agent=agent, app_name="lumibot-agents")
        session_id = str(uuid4())
        user_id = "lumibot-user"
        await runner.session_service.create_session(
            app_name=runner.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        content = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=self._build_user_text(request))],
        )
        events: list[AgentTraceEvent] = []
        run_config = run_config_module.RunConfig(max_llm_calls=sys.maxsize - 1)
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=content,
            run_config=run_config,
        ):
            normalized_events = _normalize_event(
                event,
                collector=request.boundary_collector,
            )
            if normalized_events and first_event_perf is None:
                first_event_perf = time.perf_counter()
                first_event_at = _utc_iso_timestamp()
                try:
                    sys.stderr.write(
                        f"[lumibot.agents] first ADK event for agent '{request.agent_name}' "
                        f"(model={request.model!r}) after "
                        f"{max(int((first_event_perf - started_perf) * 1000), 0)}ms.\n"
                    )
                    sys.stderr.flush()
                except Exception:
                    pass
            timestamp = _utc_iso_timestamp()
            for normalized_event in normalized_events:
                normalized_event.timestamp = timestamp
            events.extend(normalized_events)
        for event in events:
            if event.tool_name:
                event.tool_name = tool_name_map.get(event.tool_name, event.tool_name)
        summary = None
        text_chunks = [event.text for event in events if event.kind == "text" and event.text]
        if text_chunks:
            summary = text_chunks[-1]
        usage = _aggregate_usage_metadata(
            [event.payload for event in events if event.kind == "usage" and isinstance(event.payload, dict)]
        )
        ended_at = _utc_iso_timestamp()
        ended_perf = time.perf_counter()
        return AgentRunResult(
            summary=summary,
            model=request.model,
            events=events,
            usage=usage,
            started_at=started_at,
            first_event_at=first_event_at,
            ended_at=ended_at,
            latency_ms=max(int((ended_perf - started_perf) * 1000), 0),
            first_event_latency_ms=(
                max(int((first_event_perf - started_perf) * 1000), 0)
                if first_event_perf is not None
                else None
            ),
        )

    # Transient-error retry policy for the full agent call. Covers both the
    # Gemini-native path (google-genai exceptions) and the LiteLlm path
    # (network/timeouts below LiteLLM's own retry layer). LiteLLM already
    # retries individual HTTP calls 3x; this outer retry handles whole-run
    # failures like session setup errors, ADK runner glitches, and anything
    # else that bubbles up.
    #
    # Retry schedule: 10 attempts with per-step backoff capped at 60s so no
    # single wait exceeds one minute. Total budget ~= 5 minutes across all
    # attempts. Prefer more small tries over a few long ones — most cloud
    # provider 5xx storms clear within seconds or low-minutes, and a 5-min
    # budget covers the common case without leaving a live bot frozen for
    # 10 minutes on a single call. If the provider is still down after
    # this budget, the strategy-level safety net (in manager.py's
    # AgentHandle.run) catches the failure and skips this iteration so
    # the strategy stays alive and retries on the next bar.
    _MAX_RUN_ATTEMPTS = 10
    _DEFAULT_RUN_TIMEOUT_SECONDS = 1800.0
    _DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS = 600.0
    _RETRY_BACKOFF_SECONDS = (2.0, 3.0, 5.0, 10.0, 20.0, 30.0, 45.0, 60.0, 60.0, 60.0)

    @staticmethod
    def _model_request_timeout_seconds_for_request(request: RuntimeRequest) -> float | None:
        if request.model_request_timeout_seconds is not None:
            return _coerce_positive_timeout_seconds(request.model_request_timeout_seconds)
        raw = os.environ.get("LUMIBOT_AGENT_MODEL_REQUEST_TIMEOUT_SECONDS")
        if raw is not None:
            parsed, timeout_seconds = _parse_timeout_seconds(raw)
            if parsed:
                return timeout_seconds
        # This is the provider/model HTTP request timeout, not the full agent
        # run timeout. Multi-tool agents can still run longer via the outer
        # run timeout; one wedged model request should not.
        return GoogleADKRuntime._DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS

    @staticmethod
    def _generate_content_config_kwargs_for_request(request: RuntimeRequest, genai_types: Any) -> dict[str, Any]:
        config_kwargs: dict[str, Any] = {
            "max_output_tokens": 65535,
        }
        request_timeout_seconds = GoogleADKRuntime._model_request_timeout_seconds_for_request(request)
        if _is_native_gemini_model(request.model) and request_timeout_seconds is not None:
            timeout_millis = max(int(request_timeout_seconds * 1000), 1)
            http_options_type = getattr(genai_types, "HttpOptions", None)
            if http_options_type is not None:
                config_kwargs["http_options"] = http_options_type(timeout=timeout_millis)
        return config_kwargs

    @staticmethod
    def _max_attempts_for_request(request: RuntimeRequest) -> int:
        mutating_order_tools = {
            "orders_submit_order",
            "orders_submit_and_confirm_order",
            "orders_cancel_order",
            "orders_modify_order",
        }
        has_mutating_trading_tool = any(
            bool(getattr(tool, "metadata", {}).get("mutates_trading"))
            or tool.name in mutating_order_tools
            for tool in request.bound_tools
        )
        if has_mutating_trading_tool:
            # Retrying the whole agent run after a broker-side effect can duplicate orders.
            # Research-only agents keep the larger retry budget; trading agents fail fast
            # and let the next scheduled/bar iteration re-evaluate from current broker state.
            return 1
        raw = os.environ.get("LUMIBOT_AGENT_MAX_RUN_ATTEMPTS")
        if raw:
            try:
                return max(int(raw), 1)
            except Exception:
                pass
        mode = ""
        if isinstance(request.runtime_context, dict):
            mode = str(request.runtime_context.get("mode") or "").strip().lower()
        # Backtests can multiply spend quickly because one strategy run may call
        # the model hundreds of times. Keep provider retries conservative unless
        # the user explicitly opts into a higher retry budget.
        if mode == "backtesting":
            return 2
        return GoogleADKRuntime._MAX_RUN_ATTEMPTS

    @staticmethod
    def _run_timeout_seconds_for_request(request: RuntimeRequest) -> float | None:
        if request.run_timeout_seconds is not None:
            return _coerce_positive_timeout_seconds(request.run_timeout_seconds)
        raw = os.environ.get("LUMIBOT_AGENT_RUN_TIMEOUT_SECONDS")
        if raw is not None:
            parsed, timeout_seconds = _parse_timeout_seconds(raw)
            if parsed:
                return timeout_seconds
        # Agentic trading/research runs can legitimately spend many minutes
        # across model calls and tool calls. Keep the default high enough for
        # realistic multi-tool agents while still preventing indefinite hangs.
        return GoogleADKRuntime._DEFAULT_RUN_TIMEOUT_SECONDS

    @staticmethod
    def _is_non_retryable(exc: BaseException) -> bool:
        # Use the shared classifier: only transient and unknown errors retry.
        # auth / config / billing surface immediately so we don't waste ~5
        # minutes of retry budget on a wrong API key.
        return _classify_agent_error(exc) not in ("transient", "unknown")

    async def _run_async_with_timeout(self, request: RuntimeRequest, timeout_seconds: float) -> AgentRunResult:
        try:
            return await asyncio.wait_for(self._run_async(request), timeout=timeout_seconds)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise TimeoutError(
                f"Agent run exceeded {timeout_seconds:g}s timeout "
                f"(model={request.model!r}, agent={request.agent_name!r})."
            ) from exc

    def run(self, request: RuntimeRequest) -> AgentRunResult:
        import time as _time

        last_exc: BaseException | None = None
        max_attempts = self._max_attempts_for_request(request)
        timeout_seconds = self._run_timeout_seconds_for_request(request)
        for attempt in range(1, max_attempts + 1):
            try:
                if timeout_seconds is None:
                    result = asyncio.run(self._run_async(request))
                else:
                    result = asyncio.run(self._run_async_with_timeout(request, timeout_seconds))
                if result.boundary_trace is None and request.boundary_collector is not None:
                    result.boundary_trace = request.boundary_collector.export()
                return result
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:  # noqa: BLE001 - intentional broad catch for retry
                last_exc = exc
                if self._is_non_retryable(exc):
                    raise
                if attempt >= max_attempts:
                    break
                delay = self._RETRY_BACKOFF_SECONDS[min(attempt - 1, len(self._RETRY_BACKOFF_SECONDS) - 1)]
                try:
                    sys.stderr.write(
                        f"[lumibot.agents] transient error on attempt {attempt}/{max_attempts} "
                        f"for model={request.model!r}: {exc.__class__.__name__}: {str(exc)[:240]}. "
                        f"Retrying in {delay:.0f}s...\n"
                    )
                    sys.stderr.flush()
                except Exception:
                    pass
                _time.sleep(delay)
        assert last_exc is not None
        raise last_exc


class StubAgentRuntime:
    def __init__(self, scripted_events: list[dict[str, Any]] | None = None) -> None:
        self.scripted_events = scripted_events or []

    @staticmethod
    def _resolve_tool_result(tool_result: Any) -> Any:
        if not inspect.isawaitable(tool_result):
            return tool_result
        try:
            return asyncio.run(tool_result)
        except BaseException:
            close = getattr(tool_result, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            raise

    def run(self, request: RuntimeRequest) -> AgentRunResult:
        if self.scripted_events:
            events = [
                AgentTraceEvent(
                    kind=event["kind"],
                    text=event.get("text"),
                    tool_name=event.get("tool_name"),
                    payload=event.get("payload"),
                    timestamp=event.get("timestamp") or _utc_iso_timestamp(),
                )
                for event in self.scripted_events
            ]
            summary = next((event.text for event in reversed(events) if event.kind == "text" and event.text), None)
            boundary_trace = (
                request.boundary_collector.export()
                if request.boundary_collector is not None
                else None
            )
            return AgentRunResult(
                summary=summary,
                model=request.model,
                events=events,
                boundary_trace=boundary_trace,
            )

        events: list[AgentTraceEvent] = []
        if request.context is not None:
            events.append(
                AgentTraceEvent(
                    kind="thinking",
                    text="Stub runtime inspected the provided context.",
                    timestamp=_utc_iso_timestamp(),
                )
            )
        if request.bound_tools:
            first_tool = request.bound_tools[0]
            tool_context = {
                "agent_name": request.agent_name,
                "model_call_id": request.model_call_id,
                "enforce_order_readiness": True,
                "tool_calls": [],
            }
            if callable(first_tool.function):
                wrapped_tool = _wrap_tool_callable(
                    first_tool,
                    tool_context,
                    collector=request.boundary_collector,
                )
                tool_result = self._resolve_tool_result(wrapped_tool())
            else:
                tool_result = None
            events.append(
                AgentTraceEvent(
                    kind="tool_call",
                    tool_name=first_tool.name,
                    payload={},
                    timestamp=_utc_iso_timestamp(),
                )
            )
            payload = tool_result if isinstance(tool_result, dict) else {"value": tool_result}
            events.append(
                AgentTraceEvent(
                    kind="tool_result",
                    tool_name=first_tool.name,
                    payload=payload,
                    timestamp=_utc_iso_timestamp(),
                )
            )
        summary = "Stub agent completed run."
        events.append(
            AgentTraceEvent(
                kind="text",
                text=summary,
                timestamp=_utc_iso_timestamp(),
            )
        )
        boundary_trace = (
            request.boundary_collector.export()
            if request.boundary_collector is not None
            else None
        )
        return AgentRunResult(
            summary=summary,
            model=request.model,
            events=events,
            boundary_trace=boundary_trace,
        )


def list_mcp_tools(server: MCPServer) -> list[dict[str, Any]]:
    return _run_mcp_sync(_list_mcp_tools_async, server)


def call_mcp_tool(server: MCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _run_mcp_sync(_call_mcp_tool_async, server, name, arguments)


def _mcp_headers(server: MCPServer) -> dict[str, str]:
    headers = {"Accept": "application/json, text/event-stream"}
    if server.headers:
        headers.update(server.headers)
    if server.auth_token_env:
        import os

        token = os.environ.get(server.auth_token_env)
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def _jsonable(value: Any) -> Any:
    value = _json_safe_value(value)
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


async def _with_mcp_session(server: MCPServer, callback):
    _ensure_mcp_client_imports()
    transport = (server.transport or "http").lower().replace("-", "_")
    if transport == "stdio":
        parameters = StdioServerParameters(
            command=str(server.command),
            args=list(server.args or []),
            env=dict(server.env) if server.env else None,
            cwd=server.cwd,
        )
        with _mcp_errlog_stream() as errlog:
            async with stdio_client(parameters, errlog=errlog) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    return await callback(session)

    headers = _mcp_headers(server)
    timeout = server.timeout_seconds
    sse_timeout = server.sse_read_timeout_seconds
    if streamablehttp_client_uses_http_client:
        import httpx
        from mcp.shared._httpx_utils import create_mcp_http_client

        http_timeout = httpx.Timeout(timeout, read=sse_timeout)
        async with create_mcp_http_client(headers=headers, timeout=http_timeout) as http_client:
            async with streamablehttp_client(
                str(server.url),
                http_client=http_client,
                terminate_on_close=server.terminate_on_close,
            ) as (read_stream, write_stream, _get_session_id):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    return await callback(session)
    else:
        async with streamablehttp_client(
            str(server.url),
            headers=headers,
            timeout=timeout,
            sse_read_timeout=sse_timeout,
            terminate_on_close=server.terminate_on_close,
        ) as (read_stream, write_stream, _get_session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await callback(session)


def _run_mcp_sync(async_fn, *args):
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        from anyio import run as anyio_run

        return anyio_run(async_fn, *args)
    from anyio.from_thread import start_blocking_portal

    with start_blocking_portal() as portal:
        return portal.call(async_fn, *args)


async def _list_mcp_tools_async(server: MCPServer) -> list[dict[str, Any]]:
    transport = (server.transport or "http").lower().replace("-", "_")
    async def callback(session: ClientSession) -> list[dict[str, Any]]:
        result = await session.list_tools()
        tools = getattr(result, "tools", None) or []
        normalized: list[dict[str, Any]] = []
        for tool in tools:
            dumped = _jsonable(tool)
            if isinstance(dumped, dict):
                normalized.append(dumped)
        return normalized

    if transport == "http":
        return await _legacy_http_list_tools(server)
    return await _with_mcp_session(server, callback)


async def _call_mcp_tool_async(server: MCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    transport = (server.transport or "http").lower().replace("-", "_")
    async def callback(session: ClientSession) -> dict[str, Any]:
        result = await session.call_tool(name, arguments or {})
        dumped = _jsonable(result)
        if not isinstance(dumped, dict):
            raise RuntimeError(f"{name} returned unexpected payload: {dumped!r}")
        if dumped.get("isError") is True:
            raise RuntimeError(f"{name} failed: {dumped}")
        return dumped

    if transport == "http":
        return await _legacy_http_call_tool(server, name, arguments)
    return await _with_mcp_session(server, callback)


async def _legacy_http_list_tools(server: MCPServer) -> list[dict[str, Any]]:
    import httpx

    payload = {
        "jsonrpc": "2.0",
        "id": "tools-list",
        "method": "tools/list",
        "params": {},
    }
    async with httpx.AsyncClient(timeout=server.timeout_seconds) as client:
        response = await client.post(str(server.url), json=payload, headers=_mcp_headers(server))
        response.raise_for_status()
        data = response.json()
    result = data.get("result") or {}
    tools = result.get("tools") or []
    return tools if isinstance(tools, list) else []


async def _legacy_http_call_tool(server: MCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    import httpx

    payload = {
        "jsonrpc": "2.0",
        "id": f"{name}-call",
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    async with httpx.AsyncClient(timeout=server.timeout_seconds) as client:
        response = await client.post(str(server.url), json=payload, headers=_mcp_headers(server))
        response.raise_for_status()
        data = response.json()
    if "error" in data:
        raise RuntimeError(f"{name} failed: {data['error']}")
    result = data.get("result") or {}
    if not isinstance(result, dict):
        raise RuntimeError(f"{name} returned unexpected payload: {result!r}")
    return result
