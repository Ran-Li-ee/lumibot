from __future__ import annotations

import inspect
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import litellm
from litellm.integrations.custom_logger import CustomLogger
from pydantic import BaseModel

from .boundary_trace import BoundaryTraceCollector

_MISSING = object()
_MAX_CAPTURE_ITEMS = 200
_MAX_CAPTURE_DEPTH = 8
_PYDANTIC_EXTRA_DESCRIPTOR = BaseModel.__dict__[
    "__pydantic_extra__"
]
_ALLOWED_REQUEST_FIELDS = (
    "model",
    "messages",
    "tools",
    "functions",
    "function_call",
    "response_format",
    "temperature",
    "max_completion_tokens",
    "max_output_tokens",
    "max_tokens",
    "top_p",
    "top_k",
    "stop",
    "presence_penalty",
    "frequency_penalty",
    "stream_options",
    "seed",
    "n",
    "logprobs",
    "top_logprobs",
    "logit_bias",
    "tool_choice",
    "parallel_tool_calls",
    "reasoning_effort",
    "thinking",
    "verbosity",
    "service_tier",
    "modalities",
    "prediction",
    "audio",
    "web_search_options",
    "store",
    "timeout",
    "request_timeout",
    "max_retries",
    "drop_params",
    "allowed_openai_params",
    "additional_drop_params",
    "context_management",
    "prompt_cache_key",
    "prompt_cache_retention",
    "metadata",
    "stream",
)
_SAFE_METADATA_FIELDS = (
    "lumibot_agent_run_id",
    "lumibot_model_turn_id",
)
_ROUTING_FIELDS = (
    "model",
    "custom_llm_provider",
    "litellm_call_id",
    "litellm_trace_id",
    "call_type",
    "model_group",
    "deployment_id",
    "api_version",
    "base_model",
    "region_name",
)
_RESPONSE_FIELDS = (
    "id",
    "created",
    "model",
    "object",
    "system_fingerprint",
)
_CHOICE_FIELDS = (
    "finish_reason",
    "index",
    "logprobs",
)
_MESSAGE_FIELDS = (
    "role",
    "content",
    "refusal",
    "reasoning_content",
)
_TOOL_CALL_FIELDS = (
    "id",
    "index",
    "type",
)
_FUNCTION_FIELDS = (
    "name",
    "arguments",
)


@dataclass(frozen=True)
class LiteLLMAttemptToken:
    agent_run_id: str
    model_turn_id: str | None
    attempt_index: int
    retry_count: int | None


@dataclass(frozen=True)
class LiteLLMStreamAggregate:
    complete: bool
    fidelity: str
    chunks: tuple[Any, ...]


@dataclass
class _PendingProviderRequest:
    token: LiteLLMAttemptToken
    request: dict[str, Any]
    routing: dict[str, Any]
    cache_hit: bool
    started_at: datetime
    visibility: str
    projection_truncation: dict[str, Any]
    pre_api_seen: bool = False
    proxy_owned: bool = False


@dataclass
class _ProjectionTracker:
    omissions: list[dict[str, Any]]

    def note(
        self,
        *,
        path: str,
        reason: str,
        omitted_count: int,
    ) -> None:
        if omitted_count <= 0:
            return
        omission = {
            "path": path,
            "reason": reason,
            "omitted_count": omitted_count,
        }
        if omission not in self.omissions:
            self.omissions.append(omission)

    def summary(self) -> dict[str, Any]:
        return {
            "truncated": bool(self.omissions),
            "omitted_count": sum(
                omission["omitted_count"]
                for omission in self.omissions
            ),
            "omissions": list(self.omissions),
        }


def _merge_projection_truncation(
    *values: dict[str, Any],
) -> dict[str, Any]:
    tracker = _ProjectionTracker([])
    for value in values:
        omissions = (
            value.get("omissions", [])
            if type(value) is dict
            else []
        )
        for omission in omissions:
            if type(omission) is not dict:
                continue
            tracker.note(
                path=str(omission.get("path", "$")),
                reason=str(omission.get("reason", "unknown")),
                omitted_count=int(
                    omission.get("omitted_count", 0)
                ),
            )
    return tracker.summary()


def supports_dynamic_input_callback() -> bool:
    """Return whether this LiteLLM exposes a request-local input callback."""
    try:
        parameters = inspect.signature(litellm.acompletion).parameters
    except (TypeError, ValueError):
        return False
    return "input_callback" in parameters


def _type_name(value: Any) -> str:
    try:
        name = type.__getattribute__(type(value), "__name__")
    except BaseException:
        return "unknown"
    return name if type(name) is str else "unknown"


def _object_storage(value: Any) -> dict[str, Any] | None:
    value_type = type(value)
    try:
        value_mro = type.__getattribute__(value_type, "__mro__")
    except BaseException:
        value_mro = ()
    if any(candidate is dict for candidate in value_mro):
        return value
    try:
        storage = object.__getattribute__(value, "__dict__")
    except BaseException:
        return None
    return storage if type(storage) is dict else None


def _field(value: Any, key: str) -> Any:
    storage = _object_storage(value)
    if storage is not None:
        try:
            item = dict.get(storage, key, _MISSING)
        except BaseException:
            return _MISSING
        if item is not _MISSING:
            return item
    try:
        value_mro = type.__getattribute__(
            type(value),
            "__mro__",
        )
    except BaseException:
        return _MISSING
    if BaseModel not in value_mro:
        return _MISSING
    try:
        extra = _PYDANTIC_EXTRA_DESCRIPTOR.__get__(
            value,
            type(value),
        )
    except BaseException:
        return _MISSING
    if type(extra) is not dict:
        return _MISSING
    try:
        return dict.get(extra, key, _MISSING)
    except BaseException:
        return _MISSING


def _safe_scalar(value: Any) -> Any:
    value_type = type(value)
    if value is None or value_type in (bool, int, str):
        return value
    if value_type is float:
        return value if math.isfinite(value) else None
    return _MISSING


def _omitted_value(
    value: Any,
    reason: str = "unsupported",
    *,
    tracker: _ProjectionTracker | None = None,
    path: str = "$",
) -> dict[str, str]:
    if tracker is not None:
        tracker.note(
            path=path,
            reason=reason,
            omitted_count=1,
        )
    return {
        "capture": f"omitted_{reason}",
        "type": _type_name(value),
    }


def _safe_value(
    value: Any,
    *,
    depth: int = 0,
    tracker: _ProjectionTracker | None = None,
    path: str = "$",
) -> Any:
    scalar = _safe_scalar(value)
    if scalar is not _MISSING:
        return scalar
    if depth >= _MAX_CAPTURE_DEPTH:
        if tracker is not None:
            tracker.note(
                path=path,
                reason="depth_limit",
                omitted_count=1,
            )
        return _omitted_value(value, "depth_limit")
    value_type = type(value)
    if value_type in (bytes, bytearray, memoryview):
        return _omitted_value(
            value,
            "binary",
            tracker=tracker,
            path=path,
        )
    if value_type in (list, tuple):
        iterator = (
            list.__iter__(value)
            if value_type is list
            else tuple.__iter__(value)
        )
        captured = []
        for index, item in enumerate(iterator):
            if index >= _MAX_CAPTURE_ITEMS:
                remaining_count = max(len(value) - index, 0)
                captured.append(
                    {
                        "capture": "omitted_item_limit",
                        "remaining_count": remaining_count,
                    }
                )
                if tracker is not None:
                    tracker.note(
                        path=path,
                        reason="item_limit",
                        omitted_count=remaining_count,
                    )
                break
            captured.append(
                _safe_value(
                    item,
                    depth=depth + 1,
                    tracker=tracker,
                    path=f"{path}[{index}]",
                )
            )
        return captured
    storage = _object_storage(value)
    if storage is not None and any(
        candidate is dict
        for candidate in type.__getattribute__(type(value), "__mro__")
    ):
        captured_mapping: dict[str, Any] = {}
        try:
            iterator = dict.items(storage)
            for index, (key, item) in enumerate(iterator):
                if index >= _MAX_CAPTURE_ITEMS:
                    captured_mapping["capture"] = "omitted_item_limit"
                    if tracker is not None:
                        tracker.note(
                            path=path,
                            reason="item_limit",
                            omitted_count=max(
                                len(storage) - index,
                                0,
                            ),
                        )
                    break
                safe_key = _safe_scalar(key)
                if type(safe_key) not in (str, int):
                    continue
                captured_mapping[str(safe_key)] = _safe_value(
                    item,
                    depth=depth + 1,
                    tracker=tracker,
                    path=f"{path}.{safe_key}",
                )
        except BaseException:
            return _omitted_value(
                value,
                tracker=tracker,
                path=path,
            )
        return captured_mapping
    return _omitted_value(value, tracker=tracker, path=path)


def _safe_metadata(value: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in _SAFE_METADATA_FIELDS:
        item = _field(value, key)
        safe_item = _safe_scalar(item)
        if safe_item is not _MISSING and safe_item is not None:
            metadata[key] = safe_item
    return metadata


def _nested_litellm_params(kwargs: Any) -> Any:
    nested = _field(kwargs, "litellm_params")
    return nested if _object_storage(nested) is not None else {}


def _request_value(kwargs: Any, key: str) -> Any:
    value = _field(kwargs, key)
    if value is not _MISSING:
        return value
    optional_params = _field(kwargs, "optional_params")
    value = _field(optional_params, key)
    if value is not _MISSING:
        return value
    if key == "metadata":
        return _field(_nested_litellm_params(kwargs), "metadata")
    return _MISSING


def _safe_provider_request(
    kwargs: Any,
    *,
    tracker: _ProjectionTracker | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {}
    for key in _ALLOWED_REQUEST_FIELDS:
        value = _request_value(kwargs, key)
        if value is _MISSING:
            continue
        request[key] = (
            _safe_metadata(value)
            if key == "metadata"
            else _safe_value(
                value,
                tracker=tracker,
                path=f"$.{key}",
            )
        )
    return request


def _safe_routing(kwargs: Any) -> dict[str, Any]:
    nested = _nested_litellm_params(kwargs)
    routing: dict[str, Any] = {}
    for key in _ROUTING_FIELDS:
        value = _field(kwargs, key)
        if value is _MISSING:
            value = _field(nested, key)
        safe_value = _safe_scalar(value)
        if safe_value is not _MISSING and safe_value is not None:
            routing[key] = safe_value
    return routing


def _safe_usage(
    value: Any,
    *,
    depth: int = 0,
    tracker: _ProjectionTracker | None = None,
    path: str = "$.response.usage",
) -> dict[str, Any]:
    storage = _object_storage(value)
    if storage is None:
        return {}
    if depth >= _MAX_CAPTURE_DEPTH:
        if tracker is not None:
            tracker.note(
                path=path,
                reason="depth_limit",
                omitted_count=1,
            )
        return {}
    usage: dict[str, Any] = {}
    try:
        items = dict.items(storage)
        for index, (key, item) in enumerate(items):
            if index >= _MAX_CAPTURE_ITEMS:
                if tracker is not None:
                    tracker.note(
                        path=path,
                        reason="item_limit",
                        omitted_count=max(
                            len(storage) - index,
                            0,
                        ),
                    )
                break
            if type(key) is not str:
                break
            scalar = _safe_scalar(item)
            if type(scalar) in (int, float) and type(scalar) is not bool:
                usage[key] = scalar
                continue
            nested = _safe_usage(
                item,
                depth=depth + 1,
                tracker=tracker,
                path=f"{path}.{key}",
            )
            if nested:
                usage[key] = nested
    except BaseException:
        return {}
    return usage


def _safe_function(
    value: Any,
    *,
    tracker: _ProjectionTracker | None = None,
    path: str = "$",
) -> dict[str, Any]:
    function: dict[str, Any] = {}
    for key in _FUNCTION_FIELDS:
        item = _field(value, key)
        if item is not _MISSING:
            function[key] = _safe_value(
                item,
                tracker=tracker,
                path=f"{path}.{key}",
            )
    return function


def _safe_tool_call(
    value: Any,
    *,
    tracker: _ProjectionTracker | None = None,
    path: str = "$",
) -> dict[str, Any]:
    tool_call: dict[str, Any] = {}
    for key in _TOOL_CALL_FIELDS:
        item = _field(value, key)
        if item is not _MISSING:
            tool_call[key] = _safe_value(
                item,
                tracker=tracker,
                path=f"{path}.{key}",
            )
    function = _field(value, "function")
    if function is not _MISSING:
        tool_call["function"] = _safe_function(
            function,
            tracker=tracker,
            path=f"{path}.function",
        )
    return tool_call


def _safe_tool_calls(
    value: Any,
    *,
    tracker: _ProjectionTracker | None = None,
    path: str = "$",
) -> list[dict[str, Any]]:
    if type(value) not in (list, tuple):
        return []
    iterator = (
        list.__iter__(value)
        if type(value) is list
        else tuple.__iter__(value)
    )
    captured = []
    for index, item in enumerate(iterator):
        if index >= _MAX_CAPTURE_ITEMS:
            if tracker is not None:
                tracker.note(
                    path=path,
                    reason="item_limit",
                    omitted_count=max(len(value) - index, 0),
                )
            break
        captured.append(
            _safe_tool_call(
                item,
                tracker=tracker,
                path=f"{path}[{index}]",
            )
        )
    return captured


def _safe_message(
    value: Any,
    *,
    tracker: _ProjectionTracker | None = None,
    path: str = "$",
) -> dict[str, Any]:
    message: dict[str, Any] = {}
    for key in _MESSAGE_FIELDS:
        item = _field(value, key)
        if item is not _MISSING:
            message[key] = _safe_value(
                item,
                tracker=tracker,
                path=f"{path}.{key}",
            )
    for key in ("tool_calls", "function_call"):
        item = _field(value, key)
        if item is _MISSING:
            continue
        message[key] = (
            _safe_tool_calls(
                item,
                tracker=tracker,
                path=f"{path}.{key}",
            )
            if key == "tool_calls"
            else _safe_function(
                item,
                tracker=tracker,
                path=f"{path}.{key}",
            )
        )
    return message


def _safe_choice(
    value: Any,
    *,
    tracker: _ProjectionTracker | None = None,
    path: str = "$",
) -> dict[str, Any]:
    choice: dict[str, Any] = {}
    for key in _CHOICE_FIELDS:
        item = _field(value, key)
        if item is not _MISSING:
            choice[key] = _safe_value(
                item,
                tracker=tracker,
                path=f"{path}.{key}",
            )
    for key in ("message", "delta"):
        item = _field(value, key)
        if item is not _MISSING:
            choice[key] = _safe_message(
                item,
                tracker=tracker,
                path=f"{path}.{key}",
            )
    return choice


def _safe_choices(
    value: Any,
    *,
    tracker: _ProjectionTracker | None = None,
    path: str = "$",
) -> list[dict[str, Any]]:
    if type(value) not in (list, tuple):
        return []
    iterator = (
        list.__iter__(value)
        if type(value) is list
        else tuple.__iter__(value)
    )
    captured = []
    for index, item in enumerate(iterator):
        if index >= _MAX_CAPTURE_ITEMS:
            if tracker is not None:
                tracker.note(
                    path=path,
                    reason="item_limit",
                    omitted_count=max(len(value) - index, 0),
                )
            break
        captured.append(
            _safe_choice(
                item,
                tracker=tracker,
                path=f"{path}[{index}]",
            )
        )
    return captured


def _safe_provider_response(
    response_obj: Any,
    *,
    tracker: _ProjectionTracker | None = None,
    path: str = "$.response",
) -> dict[str, Any]:
    if type(response_obj) is LiteLLMStreamAggregate:
        return {
            "object": "lumibot.stream_aggregate",
            "stream_aggregate": {
                "complete": response_obj.complete,
                "fidelity": _safe_scalar(
                    response_obj.fidelity
                ),
                "chunks": [
                    _safe_provider_response(
                        chunk,
                        tracker=tracker,
                        path=(
                            f"{path}.stream_aggregate.chunks"
                            f"[{index}]"
                        ),
                    )
                    for index, chunk in enumerate(
                        tuple.__iter__(response_obj.chunks)
                    )
                ],
            },
        }
    response: dict[str, Any] = {}
    for key in _RESPONSE_FIELDS:
        value = _field(response_obj, key)
        if value is not _MISSING:
            response[key] = _safe_value(
                value,
                tracker=tracker,
                path=f"{path}.{key}",
            )
    choices = _field(response_obj, "choices")
    if choices is not _MISSING:
        response["choices"] = _safe_choices(
            choices,
            tracker=tracker,
            path=f"{path}.choices",
        )
    usage = _field(response_obj, "usage")
    if usage is not _MISSING:
        response["usage"] = _safe_usage(
            usage,
            tracker=tracker,
            path=f"{path}.usage",
        )
    if response:
        return response
    return _omitted_value(
        response_obj,
        tracker=tracker,
        path=path,
    )


def _retry_count(kwargs: Any) -> int | None:
    value = _field(kwargs, "retry_count")
    if value is _MISSING:
        value = _field(_nested_litellm_params(kwargs), "retry_count")
    if type(value) is int and value >= 0:
        return value
    return None


def _provider_attempt_payload(
    kwargs: Any,
    *,
    accepted_response: bool,
    timing_visibility: str,
    retry_count: int | None = None,
) -> dict[str, Any]:
    if retry_count is None:
        retry_count = _retry_count(kwargs)
    return {
        "provider_attempt": (
            retry_count + 1
            if retry_count is not None
            else None
        ),
        "provider_retry_visibility": (
            "exposed" if retry_count is not None else "unavailable"
        ),
        "accepted_response": accepted_response,
        "provider_timing_visibility": timing_visibility,
    }


def _callback_timing(
    start_time: Any,
    end_time: Any,
) -> tuple[dict[str, Any], str]:
    if type(start_time) is not datetime or type(end_time) is not datetime:
        return {}, "unavailable"
    try:
        duration = datetime.__sub__(end_time, start_time)
        return (
            {
                "started_at": datetime.isoformat(start_time),
                "ended_at": datetime.isoformat(end_time),
                "duration_ms": max(
                    duration.total_seconds() * 1000,
                    0.0,
                ),
            },
            "callback_window_not_per_attempt",
        )
    except BaseException:
        return {}, "unavailable"


def _safe_error(error: Any) -> dict[str, str]:
    message = "[error details unavailable]"
    try:
        args = object.__getattribute__(error, "args")
    except BaseException:
        args = ()
    if type(args) is tuple and args and type(args[0]) is str:
        message = args[0]
    return {
        "type": _type_name(error),
        "message": message,
    }


def _failure_error(kwargs: Any, response_obj: Any) -> Any:
    exception = _field(kwargs, "exception")
    if exception is not _MISSING and exception is not None:
        return exception
    return response_obj


def _cache_hit(kwargs: Any) -> bool:
    nested = _nested_litellm_params(kwargs)
    metadata_values = (
        _field(kwargs, "metadata"),
        _field(nested, "metadata"),
    )
    candidates = (
        _field(kwargs, "cache_hit"),
        _field(nested, "cache_hit"),
        *(
            _field(metadata, "cache_hit")
            for metadata in metadata_values
        ),
        *(
            _field(_field(metadata, "cache"), "hit")
            for metadata in metadata_values
        ),
    )
    return any(value is True for value in candidates)


def _response_cache_hit(response_obj: Any) -> bool:
    hidden_params = _field(response_obj, "_hidden_params")
    if hidden_params is _MISSING:
        try:
            private = object.__getattribute__(
                response_obj,
                "__pydantic_private__",
            )
        except BaseException:
            private = None
        hidden_params = _field(private, "_hidden_params")
    return any(
        value is True
        for value in (
            _field(response_obj, "cache_hit"),
            _field(hidden_params, "cache_hit"),
            _field(_field(hidden_params, "cache"), "hit"),
        )
    )


class LiteLLMBoundaryLogger(CustomLogger):
    """Request-local LiteLLM callback that records provider adapter boundaries."""

    def __init__(
        self,
        collector: BoundaryTraceCollector,
        *,
        max_pending_attempts: int = 128,
        max_completed_attempts: int = 512,
    ) -> None:
        super().__init__()
        # LiteLLM 1.83 checks an instance-level __call__ attribute to route
        # async callbacks. Special-method lookup ignores this instance
        # attribute, so the logger remains non-callable and cannot enter
        # LiteLLM's global function-callback compatibility path.
        self.__call__ = self.async_log_success_event
        self.collector = collector
        self._max_pending_attempts = max(int(max_pending_attempts), 1)
        self._max_completed_attempts = max(
            int(max_completed_attempts),
            1,
        )
        self._attempt_sequence = 0
        self._pending_requests: OrderedDict[
            LiteLLMAttemptToken,
            _PendingProviderRequest,
        ] = OrderedDict()
        self._completed_attempts: OrderedDict[
            LiteLLMAttemptToken,
            tuple[str, Any],
        ] = OrderedDict()
        self._state_lock = threading.Lock()

    def _try_diagnostic(self, kind: str, detail: str) -> None:
        try:
            self.collector.add_diagnostic(kind, detail)
        except BaseException:
            return

    def _turn_id(self, kwargs: Any) -> str | None:
        metadata = _field(kwargs, "metadata")
        turn_id = _field(metadata, "lumibot_model_turn_id")
        if turn_id is _MISSING:
            nested = _nested_litellm_params(kwargs)
            metadata = _field(nested, "metadata")
            turn_id = _field(metadata, "lumibot_model_turn_id")
        safe_turn_id = _safe_scalar(turn_id)
        if safe_turn_id is not _MISSING and safe_turn_id is not None:
            return str(safe_turn_id)
        return self.collector.active_model_turn()

    def _new_attempt(
        self,
        model: Any,
        messages: Any,
        kwargs: Any,
        *,
        visibility: str,
        pre_api_seen: bool,
        proxy_owned: bool,
    ) -> LiteLLMAttemptToken:
        captured_kwargs = (
            dict.copy(kwargs)
            if type(kwargs) is dict
            else {}
        )
        if _field(captured_kwargs, "model") is _MISSING:
            captured_kwargs["model"] = model
        if _field(captured_kwargs, "messages") is _MISSING:
            captured_kwargs["messages"] = messages
        turn_id = self._turn_id(captured_kwargs)
        retry_count = _retry_count(captured_kwargs)
        started_at = _field(captured_kwargs, "api_call_start_time")
        if type(started_at) is not datetime:
            started_at = datetime.now()
        projection_tracker = _ProjectionTracker([])
        request = _safe_provider_request(
            captured_kwargs,
            tracker=projection_tracker,
        )
        with self._state_lock:
            self._attempt_sequence += 1
            token = LiteLLMAttemptToken(
                agent_run_id=self.collector.agent_run_id,
                model_turn_id=turn_id,
                attempt_index=self._attempt_sequence,
                retry_count=retry_count,
            )
            self._pending_requests[token] = _PendingProviderRequest(
                token=token,
                request=request,
                routing=_safe_routing(captured_kwargs),
                cache_hit=_cache_hit(captured_kwargs),
                started_at=started_at,
                visibility=visibility,
                projection_truncation=(
                    projection_tracker.summary()
                ),
                pre_api_seen=pre_api_seen,
                proxy_owned=proxy_owned,
            )
            while (
                len(self._pending_requests)
                > self._max_pending_attempts
            ):
                self._pending_requests.popitem(last=False)
        return token

    def begin_attempt(
        self,
        model: Any,
        messages: Any,
        kwargs: Any,
    ) -> LiteLLMAttemptToken | None:
        """Prove that the request-local provider adapter was entered."""
        try:
            return self._new_attempt(
                model,
                messages,
                kwargs,
                visibility="litellm_acompletion_args_pre_adapter",
                pre_api_seen=False,
                proxy_owned=True,
            )
        except BaseException:
            self._try_diagnostic(
                "litellm_boundary_pre_api_callback_failed",
                "LiteLLM provider request capture failed",
            )
            return None

    def capture_acompletion_request(
        self,
        model: Any,
        messages: Any,
        kwargs: Any,
    ) -> LiteLLMAttemptToken | None:
        """Retain a safe projection of request-local acompletion arguments."""
        return self.begin_attempt(model, messages, kwargs)

    def _refine_or_begin_pre_api(
        self,
        model: Any,
        messages: Any,
        kwargs: Any,
    ) -> LiteLLMAttemptToken:
        captured_kwargs = (
            dict.copy(kwargs)
            if type(kwargs) is dict
            else {}
        )
        if _field(captured_kwargs, "model") is _MISSING:
            captured_kwargs["model"] = model
        if _field(captured_kwargs, "messages") is _MISSING:
            captured_kwargs["messages"] = messages
        turn_id = self._turn_id(captured_kwargs)
        retry_count = _retry_count(captured_kwargs)
        projection_tracker = _ProjectionTracker([])
        projected_request = _safe_provider_request(
            captured_kwargs,
            tracker=projection_tracker,
        )
        with self._state_lock:
            for token, pending in reversed(
                self._pending_requests.items()
            ):
                if (
                    token.model_turn_id == turn_id
                    and token.retry_count == retry_count
                    and not pending.pre_api_seen
                ):
                    pending.request = {
                        **pending.request,
                        **projected_request,
                    }
                    pending.projection_truncation = (
                        _merge_projection_truncation(
                            pending.projection_truncation,
                            projection_tracker.summary(),
                        )
                    )
                    pending.routing = {
                        **pending.routing,
                        **_safe_routing(captured_kwargs),
                    }
                    pending.cache_hit = (
                        pending.cache_hit
                        or _cache_hit(captured_kwargs)
                    )
                    pending.visibility = "litellm_pre_api_callback"
                    pending.pre_api_seen = True
                    return token
            call_id = _safe_routing(captured_kwargs).get(
                "litellm_call_id"
            )
            for token, (_outcome, completed_call_id) in reversed(
                self._completed_attempts.items()
            ):
                if (
                    token.model_turn_id == turn_id
                    and token.retry_count == retry_count
                    and (
                        call_id is None
                        or completed_call_id == call_id
                    )
                ):
                    return token
        return self._new_attempt(
            model,
            messages,
            captured_kwargs,
            visibility="litellm_pre_api_callback",
            pre_api_seen=True,
            proxy_owned=False,
        )

    def log_pre_api_call(
        self,
        model: Any,
        messages: Any,
        kwargs: Any,
    ) -> None:
        try:
            self._refine_or_begin_pre_api(model, messages, kwargs)
        except BaseException:
            self._try_diagnostic(
                "litellm_boundary_pre_api_callback_failed",
                "LiteLLM pre-API request capture failed",
            )

    async def async_log_pre_api_call(
        self,
        model: Any,
        messages: Any,
        kwargs: Any,
    ) -> None:
        self.log_pre_api_call(model, messages, kwargs)

    def pending_attempt_count(self) -> int:
        with self._state_lock:
            return len(self._pending_requests)

    def completed_attempt_count(self) -> int:
        with self._state_lock:
            return len(self._completed_attempts)

    def has_completed_attempt(
        self,
        token: LiteLLMAttemptToken | None,
    ) -> bool:
        if token is None:
            return False
        with self._state_lock:
            return token in self._completed_attempts

    def has_pending_attempt(
        self,
        token: LiteLLMAttemptToken | None = None,
        *,
        model_turn_id: str | None = None,
    ) -> bool:
        with self._state_lock:
            if token is not None:
                return token in self._pending_requests
            return any(
                pending_token.model_turn_id == model_turn_id
                for pending_token in self._pending_requests
            )

    def drain_pending_attempts(
        self,
        *,
        model_turn_id: str | None = None,
    ) -> tuple[LiteLLMAttemptToken, ...]:
        with self._state_lock:
            tokens = tuple(
                token
                for token in self._pending_requests
                if (
                    model_turn_id is None
                    or token.model_turn_id == model_turn_id
                )
            )
            for token in tokens:
                self._pending_requests.pop(token, None)
            return tokens

    def _claim_attempt(
        self,
        token: LiteLLMAttemptToken | None,
        outcome: str,
    ) -> _PendingProviderRequest | None:
        if token is None:
            return None
        with self._state_lock:
            pending = self._pending_requests.pop(token, None)
            if pending is None:
                return None
            self._completed_attempts[token] = (
                outcome,
                pending.routing.get("litellm_call_id"),
            )
            while (
                len(self._completed_attempts)
                > self._max_completed_attempts
            ):
                self._completed_attempts.popitem(last=False)
            return pending

    def _callback_token(
        self,
        kwargs: Any,
    ) -> LiteLLMAttemptToken | None:
        turn_id = self._turn_id(kwargs)
        retry_count = _retry_count(kwargs)
        callback_routing = _safe_routing(kwargs)
        callback_call_id = callback_routing.get("litellm_call_id")
        with self._state_lock:
            candidates = [
                (token, pending)
                for token, pending in self._pending_requests.items()
                if (
                    token.model_turn_id == turn_id
                    and not pending.proxy_owned
                    and (
                        retry_count is None
                        or token.retry_count in (None, retry_count)
                    )
                )
            ]
            if callback_call_id is not None:
                for token, pending in reversed(candidates):
                    if (
                        pending.routing.get("litellm_call_id")
                        == callback_call_id
                    ):
                        return token
            return candidates[-1][0] if candidates else None

    def _try_record(self, **event: Any) -> None:
        try:
            self.collector.record(**event)
        except BaseException:
            self._try_diagnostic(
                "litellm_boundary_record_failed",
                "LiteLLM boundary collector failed",
            )

    def _record_attempt(
        self,
        *,
        token: LiteLLMAttemptToken | None,
        outcome: str,
        kwargs: Any,
        response_obj: Any,
        cache_source: Any,
        start_time: Any,
        end_time: Any,
        partial_response_obj: Any = None,
    ) -> bool:
        pending = self._claim_attempt(token, outcome)
        if pending is None:
            return False
        if (
            pending.cache_hit
            or _cache_hit(kwargs)
            or _response_cache_hit(response_obj)
            or _response_cache_hit(cache_source)
        ):
            self._try_diagnostic(
                "litellm_adapter_cache_hit",
                (
                    "LiteLLM callback exposed cache_hit=True; "
                    "provider exchange omitted"
                ),
            )
            return True
        timing_start = (
            pending.started_at
            if type(pending.started_at) is datetime
            else start_time
        )
        if type(end_time) is not datetime:
            end_time = datetime.now()
        timing, timing_visibility = _callback_timing(
            timing_start,
            end_time,
        )
        if timing:
            timing_visibility = "pre_api_to_terminal_not_per_attempt"
        accepted_response = outcome == "success"
        attempt = _provider_attempt_payload(
            kwargs,
            accepted_response=accepted_response,
            timing_visibility=timing_visibility,
            retry_count=pending.token.retry_count,
        )
        routing = {**pending.routing, **_safe_routing(kwargs)}
        request_tracker = _ProjectionTracker([])
        terminal_request = _safe_provider_request(
            kwargs,
            tracker=request_tracker,
        )
        request = (
            {**terminal_request, **pending.request}
            if pending.pre_api_seen
            else {**pending.request, **terminal_request}
        )
        request_truncation = _merge_projection_truncation(
            pending.projection_truncation,
            request_tracker.summary(),
        )
        response_model = _safe_scalar(_field(response_obj, "model"))
        if (
            accepted_response
            and response_model is not _MISSING
            and response_model is not None
        ):
            routing["model"] = response_model
        status = "success" if accepted_response else "error"
        translated_request_available = pending.pre_api_seen
        if translated_request_available:
            capture_type = "provider_adapter_request"
            boundary_distinction = (
                "request_local_litellm_pre_api_semantics_not_raw_http"
            )
            shown_request_stage = "litellm_pre_api_callback"
            translated_request_reason = None
            to_module = "provider_adapter"
        else:
            capture_type = "litellm_acompletion_input_snapshot"
            boundary_distinction = (
                "litellm_input_before_provider_translation_not_raw_http"
            )
            shown_request_stage = (
                "litellm_acompletion_input_before_provider_translation"
            )
            translated_request_reason = (
                "request_local_pre_api_callback_did_not_fire"
                if supports_dynamic_input_callback()
                else (
                    "installed_litellm_does_not_expose_"
                    "request_local_pre_api_callback"
                )
            )
            to_module = "provider_boundary_not_observed"
        self._try_record(
            transition="B10_LITELLM_TO_PROVIDER",
            from_module="litellm",
            to_module=to_module,
            model_turn_id=pending.token.model_turn_id,
            status=status,
            **timing,
            payload={
                "capture_type": capture_type,
                "boundary_distinction": boundary_distinction,
                "translated_provider_request_status": (
                    "available"
                    if translated_request_available
                    else "not_available"
                ),
                "translated_provider_request_fidelity": (
                    "normalized_copy"
                    if translated_request_available
                    else "not_available"
                ),
                "translated_provider_request_reason": (
                    translated_request_reason
                ),
                "shown_request_stage": shown_request_stage,
                "provider_request_visibility": (
                    pending.visibility
                ),
                "request": request,
                "provider_routing": routing,
                "projection_truncation": request_truncation,
                **attempt,
            },
            projection_truncation=request_truncation,
        )
        response_tracker = _ProjectionTracker([])
        response_truncation = response_tracker.summary()
        response_event: dict[str, Any] = {
            "transition": "B01_PROVIDER_TO_LITELLM",
            "from_module": "provider",
            "to_module": "litellm",
            "model_turn_id": pending.token.model_turn_id,
            "status": status,
            **timing,
            "payload": {
                "capture_type": "provider_adapter_response",
                "provider_routing": routing,
                "projection_truncation": response_truncation,
                **attempt,
            },
            "projection_truncation": response_truncation,
        }
        if accepted_response:
            response_event["payload"]["response"] = (
                _safe_provider_response(
                    response_obj,
                    tracker=response_tracker,
                )
            )
        else:
            response_event["error"] = _safe_error(
                _failure_error(kwargs, response_obj)
            )
            if partial_response_obj is not None:
                response_event["payload"]["partial_stream"] = {
                    "complete": False,
                    "response": _safe_provider_response(
                        partial_response_obj,
                        tracker=response_tracker,
                        path="$.partial_stream.response",
                    ),
                }
        response_truncation = response_tracker.summary()
        response_event["payload"]["projection_truncation"] = (
            response_truncation
        )
        response_event["projection_truncation"] = (
            response_truncation
        )
        self._try_record(**response_event)
        return True

    def complete_success(
        self,
        token: LiteLLMAttemptToken | None,
        response_obj: Any,
        *,
        kwargs: Any = None,
        cache_source: Any = None,
        start_time: Any = None,
        end_time: Any = None,
    ) -> bool:
        try:
            return self._record_attempt(
                token=token,
                outcome="success",
                kwargs=kwargs if type(kwargs) is dict else {},
                response_obj=response_obj,
                cache_source=cache_source,
                start_time=start_time,
                end_time=end_time,
            )
        except BaseException:
            self._try_diagnostic(
                "litellm_boundary_success_callback_failed",
                "LiteLLM success tracing failed",
            )
            return False

    def complete_error(
        self,
        token: LiteLLMAttemptToken | None,
        error: BaseException,
        *,
        kwargs: Any = None,
        cache_source: Any = None,
        start_time: Any = None,
        end_time: Any = None,
        partial_response_obj: Any = None,
    ) -> bool:
        try:
            callback_kwargs = (
                dict.copy(kwargs)
                if type(kwargs) is dict
                else {}
            )
            callback_kwargs["exception"] = error
            return self._record_attempt(
                token=token,
                outcome="failure",
                kwargs=callback_kwargs,
                response_obj=error,
                cache_source=cache_source,
                start_time=start_time,
                end_time=end_time,
                partial_response_obj=partial_response_obj,
            )
        except BaseException:
            self._try_diagnostic(
                "litellm_boundary_failure_callback_failed",
                "LiteLLM failure tracing failed",
            )
            return False

    def complete_pending_error(
        self,
        error: BaseException,
        *,
        model_turn_id: str | None,
    ) -> bool:
        try:
            with self._state_lock:
                token = next(
                    (
                        candidate
                        for candidate in reversed(
                            self._pending_requests
                        )
                        if candidate.model_turn_id == model_turn_id
                    ),
                    None,
                )
            return self.complete_error(token, error)
        except BaseException:
            self._try_diagnostic(
                "litellm_boundary_failure_fallback_failed",
                "LiteLLM pending failure tracing failed",
            )
            return False

    def record_async_failure_fallback(
        self,
        error: BaseException,
        *,
        kwargs: Any = None,
        model_turn_id: str | None = None,
    ) -> None:
        """Complete only a proven provider attempt; callback kwargs cannot prove one."""
        del kwargs
        self.complete_pending_error(
            error,
            model_turn_id=model_turn_id,
        )

    async def async_log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        try:
            self.complete_success(
                self._callback_token(kwargs),
                response_obj,
                kwargs=kwargs,
                start_time=start_time,
                end_time=end_time,
            )
        except BaseException:
            self._try_diagnostic(
                "litellm_boundary_success_callback_failed",
                "LiteLLM success callback tracing failed",
            )

    def log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        try:
            self.complete_success(
                self._callback_token(kwargs),
                response_obj,
                kwargs=kwargs,
                start_time=start_time,
                end_time=end_time,
            )
        except BaseException:
            self._try_diagnostic(
                "litellm_boundary_success_callback_failed",
                "LiteLLM success callback tracing failed",
            )

    async def async_log_failure_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        try:
            error = _failure_error(kwargs, response_obj)
            self.complete_error(
                self._callback_token(kwargs),
                error,
                kwargs=kwargs,
                start_time=start_time,
                end_time=end_time,
            )
        except BaseException:
            self._try_diagnostic(
                "litellm_boundary_failure_callback_failed",
                "LiteLLM failure callback tracing failed",
            )

    def log_failure_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        try:
            error = _failure_error(kwargs, response_obj)
            self.complete_error(
                self._callback_token(kwargs),
                error,
                kwargs=kwargs,
                start_time=start_time,
                end_time=end_time,
            )
        except BaseException:
            self._try_diagnostic(
                "litellm_boundary_failure_callback_failed",
                "LiteLLM failure callback tracing failed",
            )
