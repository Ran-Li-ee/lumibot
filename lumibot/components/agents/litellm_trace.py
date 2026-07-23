from __future__ import annotations

import math
import threading
from datetime import datetime
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

from .boundary_trace import BoundaryTraceCollector

_MISSING = object()
_MAX_CAPTURE_ITEMS = 200
_MAX_CAPTURE_DEPTH = 8
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
    if storage is None:
        return _MISSING
    try:
        return dict.get(storage, key, _MISSING)
    except BaseException:
        return _MISSING


def _safe_scalar(value: Any) -> Any:
    value_type = type(value)
    if value is None or value_type in (bool, int, str):
        return value
    if value_type is float:
        return value if math.isfinite(value) else None
    return _MISSING


def _omitted_value(value: Any, reason: str = "unsupported") -> dict[str, str]:
    return {
        "capture": f"omitted_{reason}",
        "type": _type_name(value),
    }


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    scalar = _safe_scalar(value)
    if scalar is not _MISSING:
        return scalar
    if depth >= _MAX_CAPTURE_DEPTH:
        return _omitted_value(value, "depth_limit")
    value_type = type(value)
    if value_type in (bytes, bytearray, memoryview):
        return _omitted_value(value, "binary")
    if value_type in (list, tuple):
        iterator = (
            list.__iter__(value)
            if value_type is list
            else tuple.__iter__(value)
        )
        captured = []
        for index, item in enumerate(iterator):
            if index >= _MAX_CAPTURE_ITEMS:
                captured.append(
                    {
                        "capture": "omitted_item_limit",
                        "remaining_count": max(len(value) - index, 0),
                    }
                )
                break
            captured.append(_safe_value(item, depth=depth + 1))
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
                    break
                safe_key = _safe_scalar(key)
                if type(safe_key) not in (str, int):
                    continue
                captured_mapping[str(safe_key)] = _safe_value(
                    item,
                    depth=depth + 1,
                )
        except BaseException:
            return _omitted_value(value)
        return captured_mapping
    return _omitted_value(value)


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


def _safe_provider_request(kwargs: Any) -> dict[str, Any]:
    request: dict[str, Any] = {}
    for key in _ALLOWED_REQUEST_FIELDS:
        value = _request_value(kwargs, key)
        if value is _MISSING:
            continue
        request[key] = (
            _safe_metadata(value)
            if key == "metadata"
            else _safe_value(value)
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


def _safe_usage(value: Any, *, depth: int = 0) -> dict[str, Any]:
    storage = _object_storage(value)
    if storage is None or depth >= _MAX_CAPTURE_DEPTH:
        return {}
    usage: dict[str, Any] = {}
    try:
        items = dict.items(storage)
        for index, (key, item) in enumerate(items):
            if index >= _MAX_CAPTURE_ITEMS or type(key) is not str:
                break
            scalar = _safe_scalar(item)
            if type(scalar) in (int, float) and type(scalar) is not bool:
                usage[key] = scalar
                continue
            nested = _safe_usage(item, depth=depth + 1)
            if nested:
                usage[key] = nested
    except BaseException:
        return {}
    return usage


def _safe_function(value: Any) -> dict[str, Any]:
    function: dict[str, Any] = {}
    for key in _FUNCTION_FIELDS:
        item = _field(value, key)
        if item is not _MISSING:
            function[key] = _safe_value(item)
    return function


def _safe_tool_call(value: Any) -> dict[str, Any]:
    tool_call: dict[str, Any] = {}
    for key in _TOOL_CALL_FIELDS:
        item = _field(value, key)
        if item is not _MISSING:
            tool_call[key] = _safe_value(item)
    function = _field(value, "function")
    if function is not _MISSING:
        tool_call["function"] = _safe_function(function)
    return tool_call


def _safe_tool_calls(value: Any) -> list[dict[str, Any]]:
    if type(value) not in (list, tuple):
        return []
    iterator = (
        list.__iter__(value)
        if type(value) is list
        else tuple.__iter__(value)
    )
    return [
        _safe_tool_call(item)
        for index, item in enumerate(iterator)
        if index < _MAX_CAPTURE_ITEMS
    ]


def _safe_message(value: Any) -> dict[str, Any]:
    message: dict[str, Any] = {}
    for key in _MESSAGE_FIELDS:
        item = _field(value, key)
        if item is not _MISSING:
            message[key] = _safe_value(item)
    for key in ("tool_calls", "function_call"):
        item = _field(value, key)
        if item is _MISSING:
            continue
        message[key] = (
            _safe_tool_calls(item)
            if key == "tool_calls"
            else _safe_function(item)
        )
    return message


def _safe_choice(value: Any) -> dict[str, Any]:
    choice: dict[str, Any] = {}
    for key in _CHOICE_FIELDS:
        item = _field(value, key)
        if item is not _MISSING:
            choice[key] = _safe_value(item)
    for key in ("message", "delta"):
        item = _field(value, key)
        if item is not _MISSING:
            choice[key] = _safe_message(item)
    return choice


def _safe_choices(value: Any) -> list[dict[str, Any]]:
    if type(value) not in (list, tuple):
        return []
    iterator = (
        list.__iter__(value)
        if type(value) is list
        else tuple.__iter__(value)
    )
    return [
        _safe_choice(item)
        for index, item in enumerate(iterator)
        if index < _MAX_CAPTURE_ITEMS
    ]


def _safe_provider_response(response_obj: Any) -> dict[str, Any]:
    response: dict[str, Any] = {}
    for key in _RESPONSE_FIELDS:
        value = _field(response_obj, key)
        if value is not _MISSING:
            response[key] = _safe_value(value)
    choices = _field(response_obj, "choices")
    if choices is not _MISSING:
        response["choices"] = _safe_choices(choices)
    usage = _field(response_obj, "usage")
    if usage is not _MISSING:
        response["usage"] = _safe_usage(usage)
    if response:
        return response
    return _omitted_value(response_obj)


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
) -> dict[str, Any]:
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


class LiteLLMBoundaryLogger(CustomLogger):
    """Request-local LiteLLM callback that records provider adapter boundaries."""

    def __init__(self, collector: BoundaryTraceCollector) -> None:
        super().__init__()
        # LiteLLM 1.83 checks an instance-level __call__ attribute to route
        # async callbacks. Special-method lookup ignores this instance
        # attribute, so the logger remains non-callable and cannot enter
        # LiteLLM's global function-callback compatibility path.
        self.__call__ = self.async_log_success_event
        self.collector = collector
        self._seen_callbacks: set[
            tuple[str, str | None, str, str, int | None]
        ] = set()
        self._seen_lock = threading.Lock()

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

    def _callback_identity(
        self,
        kwargs: Any,
        response_obj: Any,
    ) -> str:
        routing = _safe_routing(kwargs)
        call_id = routing.get("litellm_call_id")
        if call_id is not None:
            return f"call:{call_id}"
        response_id = _field(response_obj, "id")
        safe_response_id = _safe_scalar(response_id)
        if (
            safe_response_id is not _MISSING
            and safe_response_id is not None
        ):
            return f"response:{safe_response_id}"
        return f"object:{id(response_obj)}"

    def _claim_callback(
        self,
        outcome: str,
        kwargs: Any,
        response_obj: Any,
        model_turn_id: str | None,
    ) -> bool:
        key = (
            self.collector.agent_run_id,
            model_turn_id,
            outcome,
            self._callback_identity(kwargs, response_obj),
            _retry_count(kwargs),
        )
        with self._seen_lock:
            if key in self._seen_callbacks:
                return False
            self._seen_callbacks.add(key)
        return True

    def _try_record(self, **event: Any) -> None:
        try:
            self.collector.record(**event)
        except BaseException:
            self._try_diagnostic(
                "litellm_boundary_record_failed",
                "LiteLLM boundary collector failed",
            )

    def _record_callback(
        self,
        *,
        outcome: str,
        kwargs: Any,
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        turn_id = self._turn_id(kwargs)
        if not self._claim_callback(
            outcome,
            kwargs,
            response_obj,
            turn_id,
        ):
            return
        if _cache_hit(kwargs):
            self._try_diagnostic(
                "litellm_adapter_cache_hit",
                (
                    "LiteLLM callback exposed cache_hit=True; "
                    "provider exchange omitted"
                ),
            )
            return
        timing, timing_visibility = _callback_timing(
            start_time,
            end_time,
        )
        accepted_response = outcome == "success"
        attempt = _provider_attempt_payload(
            kwargs,
            accepted_response=accepted_response,
            timing_visibility=timing_visibility,
        )
        routing = _safe_routing(kwargs)
        status = "success" if accepted_response else "error"
        self._try_record(
            transition="B10_LITELLM_TO_PROVIDER",
            from_module="litellm",
            to_module="provider",
            model_turn_id=turn_id,
            status=status,
            **timing,
            payload={
                "capture_type": "provider_adapter_request",
                "boundary_distinction": (
                    "litellm_provider_adapter_request_not_adk_model_entry"
                ),
                "request": _safe_provider_request(kwargs),
                "provider_routing": routing,
                **attempt,
            },
        )
        response_event: dict[str, Any] = {
            "transition": "B01_PROVIDER_TO_LITELLM",
            "from_module": "provider",
            "to_module": "litellm",
            "model_turn_id": turn_id,
            "status": status,
            **timing,
            "payload": {
                "capture_type": "provider_adapter_response",
                "provider_routing": routing,
                **attempt,
            },
        }
        if accepted_response:
            response_event["payload"]["response"] = (
                _safe_provider_response(response_obj)
            )
        else:
            response_event["error"] = _safe_error(
                _failure_error(kwargs, response_obj)
            )
        self._try_record(**response_event)

    async def async_log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        try:
            self._record_callback(
                outcome="success",
                kwargs=kwargs,
                response_obj=response_obj,
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
            self._record_callback(
                outcome="success",
                kwargs=kwargs,
                response_obj=response_obj,
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
            self._record_callback(
                outcome="failure",
                kwargs=kwargs,
                response_obj=response_obj,
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
            self._record_callback(
                outcome="failure",
                kwargs=kwargs,
                response_obj=response_obj,
                start_time=start_time,
                end_time=end_time,
            )
        except BaseException:
            self._try_diagnostic(
                "litellm_boundary_failure_callback_failed",
                "LiteLLM failure callback tracing failed",
            )
