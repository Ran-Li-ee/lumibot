import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from lumibot.components.agents import runtime as agent_runtime
from lumibot.components.agents.boundary_trace import BoundaryTraceCollector
from lumibot.components.agents.litellm_trace import LiteLLMBoundaryLogger
from lumibot.components.agents.runtime import (
    GoogleADKRuntime,
    RuntimeRequest,
)


def _events(collector, transition):
    return [
        event
        for event in collector.export()["events"]
        if event["transition"] == transition
    ]


def _run(coro):
    return asyncio.run(coro)


def _litellm_global_state():
    import litellm
    from litellm.litellm_core_utils import litellm_logging

    return {
        "callbacks": tuple(litellm.callbacks),
        "input_callback": tuple(litellm.input_callback),
        "success_callback": tuple(litellm.success_callback),
        "failure_callback": tuple(litellm.failure_callback),
        "async_input_callback": tuple(
            litellm._async_input_callback
        ),
        "async_success_callback": tuple(
            litellm._async_success_callback
        ),
        "async_failure_callback": tuple(
            litellm._async_failure_callback
        ),
        "callback_registry": tuple(
            litellm.logging_callback_manager._get_all_callbacks()
        ),
        "custom_logger": litellm_logging.customLogger,
    }


def test_litellm_success_records_provider_adapter_pair_without_secrets(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    logger = LiteLLMBoundaryLogger(collector)
    secret = "test-only-secret-value"
    kwargs = {
        "model": "provider-model",
        "messages": [
            {
                "role": "tool",
                "tool_call_id": "call_A",
                "content": {
                    "price": 1,
                    "api_key": secret,
                    "binary": b"api_key=binary-secret",
                    "path": r"C:\private\secret.txt",
                },
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {"name": "market_last_price"},
            }
        ],
        "temperature": 0.2,
        "stream": False,
        "api_key": secret,
        "headers": {"Authorization": f"Bearer {secret}"},
        "custom_llm_provider": "openai",
        "litellm_call_id": "litellm-call-1",
        "metadata": {
            "lumibot_model_turn_id": turn_id,
            "lumibot_agent_run_id": "run-1",
            "hidden_params": {
                "api_base": "https://private-provider.invalid",
                "api_key": secret,
            },
        },
    }
    response = {
        "id": "response-1",
        "model": "provider-model",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "done"},
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
    }

    _run(
        logger.async_log_success_event(
            kwargs,
            response,
            datetime(2026, 7, 24, tzinfo=timezone.utc),
            datetime(2026, 7, 24, 0, 0, 1, tzinfo=timezone.utc),
        )
    )

    b10 = _events(collector, "B10_LITELLM_TO_PROVIDER")[0]
    b01 = _events(collector, "B01_PROVIDER_TO_LITELLM")[0]
    assert b10["model_turn_id"] == turn_id
    assert b01["model_turn_id"] == turn_id
    assert b10["payload"]["capture_type"] == "provider_adapter_request"
    assert b10["payload"]["boundary_distinction"] == (
        "litellm_provider_adapter_request_not_adk_model_entry"
    )
    assert set(b10["payload"]["request"]) == {
        "model",
        "messages",
        "tools",
        "temperature",
        "metadata",
        "stream",
    }
    assert b10["payload"]["request"]["metadata"] == {
        "lumibot_model_turn_id": turn_id,
        "lumibot_agent_run_id": "run-1",
    }
    assert b10["payload"]["provider_routing"] == {
        "model": "provider-model",
        "custom_llm_provider": "openai",
        "litellm_call_id": "litellm-call-1",
    }
    assert b10["payload"]["provider_attempt"] is None
    assert b10["payload"]["provider_retry_visibility"] == "unavailable"
    assert b10["payload"]["provider_timing_visibility"] == (
        "callback_window_not_per_attempt"
    )
    assert b10["duration_ms"] == 1000.0
    assert b01["payload"]["response"]["id"] == "response-1"
    rendered = str((b10, b01))
    assert secret not in rendered
    assert "Authorization" not in rendered
    assert "private-provider.invalid" not in rendered
    assert r"C:\private\secret.txt" not in rendered
    assert "binary-secret" not in rendered


def test_litellm_provider_request_allowlist_is_complete_and_deny_by_default(
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    logger = LiteLLMBoundaryLogger(collector)
    secret = "test-only-secret-value"
    expected_request = {
        "model": "provider-model",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function"}],
        "functions": [{"name": "legacy_function"}],
        "function_call": "auto",
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_completion_tokens": 256,
        "max_output_tokens": 254,
        "max_tokens": 255,
        "top_p": 0.9,
        "top_k": 20,
        "stop": ["END"],
        "presence_penalty": 0.1,
        "frequency_penalty": 0.2,
        "stream": True,
        "stream_options": {"include_usage": True},
        "seed": 7,
        "n": 2,
        "logprobs": True,
        "top_logprobs": 3,
        "logit_bias": {"42": -1},
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        "reasoning_effort": "medium",
        "thinking": {"type": "enabled", "budget_tokens": 128},
        "verbosity": "low",
        "service_tier": "default",
        "modalities": ["text"],
        "prediction": {"type": "content", "content": "prefix"},
        "audio": {"voice": "alloy", "format": "wav"},
        "web_search_options": {"search_context_size": "low"},
        "store": False,
        "timeout": 30,
        "request_timeout": 31,
        "max_retries": 2,
        "prompt_cache_key": "stable-cache-key",
        "prompt_cache_retention": "24h",
        "metadata": {
            "lumibot_model_turn_id": turn_id,
            "lumibot_agent_run_id": "run-1",
        },
    }
    kwargs = {
        **expected_request,
        "api_key": secret,
        "headers": {"Authorization": f"Bearer {secret}"},
        "extra_headers": {"X-Api-Key": secret},
        "default_headers": {"Authorization": secret},
        "api_base": "https://private-provider.invalid",
        "base_url": "https://private-provider.invalid",
        "organization": "private-organization",
        "client": object(),
        "http_client": object(),
        "llm_client": object(),
        "success_callback": [object()],
        "failure_callback": [object()],
        "callbacks": [object()],
        "filesystem_path": r"C:\private\secret.txt",
        "metadata": {
            **expected_request["metadata"],
            "api_key": secret,
            "hidden_params": {
                "api_base": "https://private-provider.invalid",
            },
        },
    }

    _run(
        logger.async_log_success_event(
            kwargs,
            {"id": "response-allowlist", "choices": []},
            None,
            None,
        )
    )

    request = _events(
        collector,
        "B10_LITELLM_TO_PROVIDER",
    )[0]["payload"]["request"]
    assert request == expected_request
    rendered = str(request)
    assert secret not in rendered
    assert "Authorization" not in rendered
    assert "private-provider.invalid" not in rendered
    assert r"C:\private\secret.txt" not in rendered


def test_litellm_exposed_retry_count_records_attempt_and_acceptance(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    logger = LiteLLMBoundaryLogger(collector)
    kwargs = {
        "model": "provider-model",
        "messages": [],
        "litellm_params": {
            "retry_count": 2,
            "metadata": {"lumibot_model_turn_id": turn_id},
        },
    }

    _run(
        logger.async_log_success_event(
            kwargs,
            {"id": "response-3", "choices": []},
            None,
            None,
        )
    )

    b01 = _events(collector, "B01_PROVIDER_TO_LITELLM")[0]
    assert b01["payload"]["provider_attempt"] == 3
    assert b01["payload"]["provider_retry_visibility"] == "exposed"
    assert b01["payload"]["accepted_response"] is True
    assert b01["payload"]["provider_timing_visibility"] == "unavailable"


def test_litellm_pre_api_request_is_flushed_before_success_response(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    logger = LiteLLMBoundaryLogger(collector)
    pre_kwargs = {
        "model": "provider-model",
        "messages": [{"role": "user", "content": "adapter request"}],
        "temperature": 0.3,
        "litellm_call_id": "call-pre-api",
        "metadata": {"lumibot_model_turn_id": turn_id},
    }

    logger.log_pre_api_call(
        "provider-model",
        pre_kwargs["messages"],
        pre_kwargs,
    )
    assert collector.export()["events"] == []

    _run(
        logger.async_log_success_event(
            {
                "model": "provider-model",
                "litellm_call_id": "call-pre-api",
                "metadata": {"lumibot_model_turn_id": turn_id},
            },
            {"id": "response-pre-api", "choices": []},
            None,
            None,
        )
    )

    events = collector.export()["events"]
    assert [event["transition"] for event in events] == [
        "B10_LITELLM_TO_PROVIDER",
        "B01_PROVIDER_TO_LITELLM",
    ]
    assert events[0]["payload"]["request"] == {
        "model": "provider-model",
        "messages": [
            {"role": "user", "content": "adapter request"}
        ],
        "temperature": 0.3,
        "metadata": {"lumibot_model_turn_id": turn_id},
    }


def test_litellm_failure_records_redacted_provider_error(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    logger = LiteLLMBoundaryLogger(collector)

    _run(
        logger.async_log_failure_event(
            {
                "model": "provider-model",
                "messages": [],
                "metadata": {"lumibot_model_turn_id": turn_id},
                "retry_count": 0,
            },
            RuntimeError("provider unavailable api_key=failure-secret"),
            None,
            None,
        )
    )

    b10 = _events(collector, "B10_LITELLM_TO_PROVIDER")[0]
    b01 = _events(collector, "B01_PROVIDER_TO_LITELLM")[0]
    assert b10["status"] == "error"
    assert b01["status"] == "error"
    assert b01["error"] == {
        "type": "RuntimeError",
        "message": "provider unavailable api_key=[REDACTED]",
    }
    assert b01["payload"]["provider_attempt"] == 1
    assert b01["payload"]["accepted_response"] is False


def test_litellm_failure_fallback_deduplicates_late_native_callback(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    logger = LiteLLMBoundaryLogger(collector)
    kwargs = {
        "model": "provider-model",
        "messages": [],
        "metadata": {"lumibot_model_turn_id": turn_id},
        "retry_count": 0,
    }
    error = RuntimeError("provider unavailable")

    logger.log_pre_api_call(
        "provider-model",
        kwargs["messages"],
        kwargs,
    )
    logger.record_async_failure_fallback(
        error,
        kwargs=kwargs,
        model_turn_id=turn_id,
    )
    _run(
        logger.async_log_failure_event(
            kwargs,
            error,
            None,
            None,
        )
    )

    assert len(_events(collector, "B10_LITELLM_TO_PROVIDER")) == 1
    assert len(_events(collector, "B01_PROVIDER_TO_LITELLM")) == 1


def test_litellm_turn_correlation_uses_nested_metadata_then_active_turn(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    nested_turn = collector.start_model_turn()
    active_turn = collector.start_model_turn()
    collector.set_active_model_turn(active_turn)
    logger = LiteLLMBoundaryLogger(collector)

    _run(
        logger.async_log_success_event(
            {
                "model": "first",
                "messages": [],
                "litellm_params": {
                    "metadata": {
                        "lumibot_model_turn_id": nested_turn,
                    }
                },
            },
            {"id": "response-nested", "choices": []},
            None,
            None,
        )
    )
    _run(
        logger.async_log_success_event(
            {"model": "second", "messages": []},
            {"id": "response-active", "choices": []},
            None,
            None,
        )
    )

    b10_events = _events(collector, "B10_LITELLM_TO_PROVIDER")
    assert [event["model_turn_id"] for event in b10_events] == [
        nested_turn,
        active_turn,
    ]


def test_litellm_repeated_stream_success_callback_is_deduplicated(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    logger = LiteLLMBoundaryLogger(collector)
    kwargs = {
        "model": "provider-model",
        "messages": [],
        "stream": True,
        "litellm_call_id": "stream-call-1",
        "metadata": {"lumibot_model_turn_id": turn_id},
    }

    for chunk_id in ("chunk-1", "chunk-2", "chunk-3"):
        _run(
            logger.async_log_success_event(
                kwargs,
                {"id": chunk_id, "choices": []},
                None,
                None,
            )
        )

    assert len(_events(collector, "B10_LITELLM_TO_PROVIDER")) == 1
    assert len(_events(collector, "B01_PROVIDER_TO_LITELLM")) == 1


def test_litellm_reused_call_and_response_ids_are_scoped_to_model_turn(
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    logger = LiteLLMBoundaryLogger(collector)

    for _ in range(2):
        turn_id = collector.start_model_turn()
        collector.set_active_model_turn(turn_id)
        kwargs = {
            "model": "provider-model",
            "messages": [],
            "litellm_call_id": "reused-call",
            "retry_count": 1,
            "metadata": {
                "lumibot_agent_run_id": "run-1",
                "lumibot_model_turn_id": turn_id,
            },
        }
        for _duplicate in range(2):
            _run(
                logger.async_log_success_event(
                    kwargs,
                    {"id": "reused-response", "choices": []},
                    None,
                    None,
                )
            )

    b10 = _events(collector, "B10_LITELLM_TO_PROVIDER")
    b01 = _events(collector, "B01_PROVIDER_TO_LITELLM")
    assert len(b10) == len(b01) == 2
    assert [event["model_turn_id"] for event in b10] == [
        "run-1:turn:0001",
        "run-1:turn:0002",
    ]
    assert all(
        event["payload"]["provider_attempt"] == 2
        for event in b10
    )


@pytest.mark.parametrize(
    "cache_kwargs",
    [
        {"cache_hit": True},
        {"litellm_params": {"cache_hit": True}},
        {
            "litellm_params": {
                "metadata": {"cache_hit": True},
            }
        },
        {
            "litellm_params": {
                "metadata": {"cache": {"hit": True}},
            }
        },
    ],
)
def test_litellm_cache_hit_does_not_emit_false_provider_exchange(
    tmp_path,
    cache_kwargs,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    logger = LiteLLMBoundaryLogger(collector)
    kwargs = {
        "model": "provider-model",
        "messages": [],
        "litellm_call_id": "cached-call",
        "metadata": {"lumibot_model_turn_id": turn_id},
        **cache_kwargs,
    }

    logger.log_pre_api_call(
        "provider-model",
        kwargs["messages"],
        kwargs,
    )
    _run(
        logger.async_log_success_event(
            kwargs,
            {"id": "cached-response", "choices": []},
            None,
            None,
        )
    )

    exported = collector.export()
    assert exported["events"] == []
    assert exported["diagnostics"][-1]["kind"] == (
        "litellm_adapter_cache_hit"
    )
    assert "provider exchange omitted" in (
        exported["diagnostics"][-1]["message"]
    )


def test_litellm_logger_avoids_arbitrary_object_hooks(tmp_path):
    class Explosive:
        def __getattribute__(self, _name):
            raise AssertionError("arbitrary attribute hook was called")

        def __str__(self):
            raise AssertionError("arbitrary string hook was called")

        def model_dump(self, **_kwargs):
            raise AssertionError("arbitrary serialization hook was called")

    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    logger = LiteLLMBoundaryLogger(collector)

    _run(
        logger.async_log_success_event(
            {
                "model": "provider-model",
                "messages": [{"role": "user", "content": Explosive()}],
            },
            Explosive(),
            None,
            None,
        )
    )

    assert len(_events(collector, "B10_LITELLM_TO_PROVIDER")) == 1
    assert len(_events(collector, "B01_PROVIDER_TO_LITELLM")) == 1
    assert "Explosive" in str(collector.export()["events"])


def test_litellm_logger_is_fail_open_when_collector_and_diagnostic_fail():
    class BrokenCollector:
        def active_model_turn(self):
            raise RuntimeError("turn unavailable")

        def record(self, **_kwargs):
            raise RuntimeError("collector unavailable")

        def add_diagnostic(self, *_args):
            raise RuntimeError("diagnostics unavailable")

    logger = LiteLLMBoundaryLogger(BrokenCollector())

    assert (
        _run(
            logger.async_log_success_event(
                {"model": "provider-model", "messages": []},
                {"id": "response-1"},
                None,
                None,
            )
        )
        is None
    )
    assert (
        _run(
            logger.async_log_failure_event(
                {"model": "provider-model", "messages": []},
                RuntimeError("provider unavailable"),
                None,
                None,
            )
        )
        is None
    )
    assert (
        logger.record_async_failure_fallback(
            RuntimeError("provider unavailable"),
            kwargs={"model": "provider-model", "messages": []},
        )
        is None
    )


def test_litellm_loggers_keep_concurrent_collectors_isolated(tmp_path):
    first = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path / "first",
    )
    second = BoundaryTraceCollector(
        agent_run_id="run-2",
        artifact_root=tmp_path / "second",
    )
    first_turn = first.start_model_turn()
    second_turn = second.start_model_turn()
    first_logger = LiteLLMBoundaryLogger(first)
    second_logger = LiteLLMBoundaryLogger(second)

    async def invoke_both():
        await asyncio.gather(
            first_logger.async_log_success_event(
                {
                    "model": "first-model",
                    "messages": [{"role": "user", "content": "first"}],
                    "litellm_call_id": "first-call",
                    "metadata": {
                        "lumibot_model_turn_id": first_turn,
                    },
                },
                {"id": "first-response", "choices": []},
                None,
                None,
            ),
            second_logger.async_log_success_event(
                {
                    "model": "second-model",
                    "messages": [{"role": "user", "content": "second"}],
                    "litellm_call_id": "second-call",
                    "metadata": {
                        "lumibot_model_turn_id": second_turn,
                    },
                },
                {"id": "second-response", "choices": []},
                None,
                None,
            ),
        )

    _run(invoke_both())

    assert "second-model" not in str(first.export())
    assert "first-model" not in str(second.export())
    assert _events(first, "B10_LITELLM_TO_PROVIDER")[0][
        "model_turn_id"
    ] == first_turn
    assert _events(second, "B10_LITELLM_TO_PROVIDER")[0][
        "model_turn_id"
    ] == second_turn


def test_litellm_acompletion_uses_dynamic_logger_without_global_registration(
    tmp_path,
):
    import litellm

    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    logger = LiteLLMBoundaryLogger(collector)
    global_state = _litellm_global_state()
    assert not callable(logger)

    async def invoke():
        response = await litellm.acompletion(
            model="openai/test",
            messages=[{"role": "user", "content": "hello"}],
            mock_response="hello",
            metadata={"lumibot_model_turn_id": turn_id},
            success_callback=[logger],
            failure_callback=[logger],
        )
        for _ in range(100):
            if _events(collector, "B01_PROVIDER_TO_LITELLM"):
                break
            await asyncio.sleep(0.01)
        return response

    response = _run(invoke())

    assert response.choices[0].message.content == "hello"
    assert _litellm_global_state() == global_state
    assert len(_events(collector, "B10_LITELLM_TO_PROVIDER")) == 1
    assert len(_events(collector, "B01_PROVIDER_TO_LITELLM")) == 1


def test_litellm_concurrent_request_loggers_do_not_touch_global_state(
    tmp_path,
):
    import litellm

    first = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path / "first",
    )
    second = BoundaryTraceCollector(
        agent_run_id="run-2",
        artifact_root=tmp_path / "second",
    )
    first_turn = first.start_model_turn()
    second_turn = second.start_model_turn()
    first.set_active_model_turn(first_turn)
    second.set_active_model_turn(second_turn)
    first_logger = LiteLLMBoundaryLogger(first)
    second_logger = LiteLLMBoundaryLogger(second)
    global_state = _litellm_global_state()
    assert not callable(first_logger)
    assert not callable(second_logger)

    async def complete(
        collector,
        logger,
        turn_id,
        response_text,
    ):
        response = await litellm.acompletion(
            model="openai/test",
            messages=[{"role": "user", "content": response_text}],
            mock_response=response_text,
            metadata={"lumibot_model_turn_id": turn_id},
            success_callback=[logger],
            failure_callback=[logger],
        )
        for _ in range(100):
            if _events(collector, "B01_PROVIDER_TO_LITELLM"):
                break
            await asyncio.sleep(0.01)
        return response

    async def invoke_both():
        return await asyncio.gather(
            complete(
                first,
                first_logger,
                first_turn,
                "first response",
            ),
            complete(
                second,
                second_logger,
                second_turn,
                "second response",
            ),
        )

    responses = _run(invoke_both())

    assert [response.choices[0].message.content for response in responses] == [
        "first response",
        "second response",
    ]
    assert _litellm_global_state() == global_state
    assert "second response" not in str(first.export())
    assert "first response" not in str(second.export())
    assert len(_events(first, "B01_PROVIDER_TO_LITELLM")) == 1
    assert len(_events(second, "B01_PROVIDER_TO_LITELLM")) == 1


def test_litellm_mock_failure_preserves_error_and_records_actual_exception(
    tmp_path,
):
    import litellm

    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    logger = LiteLLMBoundaryLogger(collector)
    global_state = _litellm_global_state()

    with pytest.raises(litellm.InternalServerError) as raised:
        litellm.completion(
            model="openai/test",
            messages=[{"role": "user", "content": "hello"}],
            mock_response="litellm.InternalServerError",
            metadata={"lumibot_model_turn_id": turn_id},
            success_callback=[logger],
            failure_callback=[logger],
            num_retries=0,
        )
    error = raised.value

    assert type(error).__name__ == "InternalServerError"
    assert _litellm_global_state() == global_state
    b10 = _events(collector, "B10_LITELLM_TO_PROVIDER")
    b01 = _events(collector, "B01_PROVIDER_TO_LITELLM")
    assert len(b10) == len(b01) == 1
    assert b10[0]["status"] == "error"
    assert b01[0]["status"] == "error"
    assert b01[0]["error"]["type"] == "InternalServerError"
    assert "mock internal server error" in b01[0]["error"]["message"]


def test_observed_litellm_real_async_failure_records_once_without_globals(
    tmp_path,
    monkeypatch,
):
    import litellm
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types

    collector = BoundaryTraceCollector(
        agent_run_id="run-async-failure",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    model = agent_runtime._resolve_model_for_adk(
        "openai/test",
        model_entry_observer=lambda _request: None,
        boundary_collector=collector,
    )
    model._additional_args.update(
        {
            "mock_response": "litellm.InternalServerError",
            "num_retries": 0,
        }
    )
    request = LlmRequest(
        model="openai/test",
        contents=[
            types.Content(
                role="user",
                parts=[types.Part(text="hello")],
            )
        ],
        config=types.GenerateContentConfig(),
    )
    fallback_errors = []
    original_fallback = (
        LiteLLMBoundaryLogger.record_async_failure_fallback
    )

    def observe_fallback(self, error, **kwargs):
        fallback_errors.append(error)
        return original_fallback(self, error, **kwargs)

    monkeypatch.setattr(
        LiteLLMBoundaryLogger,
        "record_async_failure_fallback",
        observe_fallback,
    )
    global_state = _litellm_global_state()

    async def invoke():
        return [
            response
            async for response in model.generate_content_async(request)
        ]

    with pytest.raises(litellm.InternalServerError) as raised:
        _run(invoke())

    assert type(raised.value) is litellm.InternalServerError
    assert fallback_errors == [raised.value]
    assert fallback_errors[0] is raised.value
    assert _litellm_global_state() == global_state
    events = collector.export()["events"]
    assert [event["transition"] for event in events] == [
        "B10_LITELLM_TO_PROVIDER",
        "B01_PROVIDER_TO_LITELLM",
    ]
    assert events[0]["status"] == "error"
    assert events[1]["status"] == "error"
    assert events[1]["error"]["type"] == "InternalServerError"
    assert events[0]["payload"]["provider_request_visibility"] == (
        "litellm_acompletion_args_pre_adapter"
    )
    assert events[0]["payload"]["provider_retry_visibility"] == (
        "unavailable"
    )
    assert events[0]["payload"]["provider_timing_visibility"] == (
        "pre_api_to_terminal_not_per_attempt"
    )
    assert events[0]["duration_ms"] >= 0


def test_observed_litellm_failure_fallback_reraises_same_exception(tmp_path):
    expected = RuntimeError("same exception")

    class FailingLiteLlm:
        def __init__(self, **kwargs):
            self._additional_args = dict(kwargs)

        def model_copy(self, *, deep=False):
            assert deep is False
            clone = object.__new__(type(self))
            clone.__dict__ = dict(self.__dict__)
            return clone

        async def generate_content_async(
            self,
            _llm_request,
            stream=False,
        ):
            assert stream is False
            raise expected
            yield

    collector = BoundaryTraceCollector(
        agent_run_id="run-identity",
        artifact_root=tmp_path,
    )
    collector.set_active_model_turn(collector.start_model_turn())
    observed_type = agent_runtime._build_observed_litellm_type(
        FailingLiteLlm,
        on_model_entry=lambda _request: None,
        boundary_collector=collector,
    )
    model = observed_type(model="provider-model")

    async def invoke():
        return [
            response
            async for response in model.generate_content_async("request")
        ]

    with pytest.raises(RuntimeError) as raised:
        _run(invoke())

    assert raised.value is expected
    assert len(_events(collector, "B10_LITELLM_TO_PROVIDER")) == 1
    b01 = _events(collector, "B01_PROVIDER_TO_LITELLM")
    assert len(b01) == 1
    assert b01[0]["error"]["message"] == "same exception"


def test_litellm_mock_stream_records_one_provider_pair(tmp_path):
    import litellm

    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    logger = LiteLLMBoundaryLogger(collector)
    global_state = _litellm_global_state()

    async def invoke():
        stream = await litellm.acompletion(
            model="openai/test",
            messages=[{"role": "user", "content": "hello"}],
            mock_response="stream hello",
            metadata={"lumibot_model_turn_id": turn_id},
            success_callback=[logger],
            failure_callback=[logger],
            stream=True,
        )
        chunks = [chunk async for chunk in stream]
        for _ in range(100):
            if _events(collector, "B01_PROVIDER_TO_LITELLM"):
                break
            await asyncio.sleep(0.01)
        return chunks

    chunks = _run(invoke())

    assert chunks
    assert _litellm_global_state() == global_state
    b10 = _events(collector, "B10_LITELLM_TO_PROVIDER")
    b01 = _events(collector, "B01_PROVIDER_TO_LITELLM")
    assert len(b10) == len(b01) == 1
    assert b10[0]["payload"]["request"]["stream"] is True


def test_observed_litellm_uses_fresh_callbacks_for_repeated_invocations(
    tmp_path,
    monkeypatch,
):
    snapshots = []
    prepared = []
    observed = []

    async def user_success(*_args):
        return None

    def user_failure(*_args):
        return None

    def user_input(*_args):
        return None

    def user_callback(*_args):
        return None

    class ProbeLiteLlm:
        def __init__(self, **kwargs):
            self._additional_args = dict(kwargs)

        def model_copy(self, *, deep=False):
            assert deep is False
            clone = object.__new__(type(self))
            clone.__dict__ = dict(self.__dict__)
            return clone

        async def generate_content_async(
            self,
            llm_request,
            stream=False,
        ):
            assert stream is False
            snapshots.append(
                {
                    key: list(value) if type(value) is list else dict(value)
                    for key, value in self._additional_args.items()
                    if key in {
                        "callbacks",
                        "input_callback",
                        "success_callback",
                        "failure_callback",
                        "metadata",
                    }
                }
            )
            for key in (
                "callbacks",
                "input_callback",
                "success_callback",
                "failure_callback",
            ):
                self._additional_args[key].clear()
            yield llm_request

    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    monkeypatch.setattr(
        agent_runtime,
        "supports_dynamic_input_callback",
        lambda: True,
    )
    observed_type = agent_runtime._build_observed_litellm_type(
        ProbeLiteLlm,
        on_model_entry=observed.append,
        prepare_model_entry=prepared.append,
        boundary_collector=collector,
    )
    original_metadata = {"user_metadata": "preserved"}
    original_callbacks = [user_callback]
    original_input = [user_input]
    original_success = [user_success]
    original_failure = [user_failure]
    model = observed_type(
        callbacks=original_callbacks,
        input_callback=original_input,
        success_callback=original_success,
        failure_callback=original_failure,
        metadata=original_metadata,
    )

    async def invoke_twice():
        responses = []
        for index in range(2):
            collector.set_active_model_turn(
                collector.start_model_turn()
            )
            responses.extend(
                [
                    response
                    async for response in model.generate_content_async(
                        f"request-{index}"
                    )
                ]
            )
        return responses

    assert _run(invoke_twice()) == ["request-0", "request-1"]
    assert prepared == ["request-0", "request-1"]
    assert observed == ["request-0", "request-1"]
    assert model._additional_args == {
        "callbacks": original_callbacks,
        "input_callback": original_input,
        "success_callback": original_success,
        "failure_callback": original_failure,
        "metadata": original_metadata,
    }
    assert len(snapshots) == 2
    for snapshot in snapshots:
        assert snapshot["callbacks"] == [user_callback]
        assert snapshot["input_callback"][0] is user_input
        assert snapshot["success_callback"][0] is user_success
        assert snapshot["failure_callback"][0] is user_failure
        assert len(snapshot["input_callback"]) == 2
        assert len(snapshot["success_callback"]) == 2
        assert len(snapshot["failure_callback"]) == 2
        input_logger = snapshot["input_callback"][1]
        success_logger = snapshot["success_callback"][1]
        failure_logger = snapshot["failure_callback"][1]
        assert isinstance(success_logger, LiteLLMBoundaryLogger)
        assert input_logger is success_logger
        assert failure_logger is success_logger
        assert success_logger.collector is collector
        assert snapshot["metadata"]["user_metadata"] == "preserved"
        assert snapshot["metadata"]["lumibot_agent_run_id"] == "run-1"
        assert snapshot["metadata"]["lumibot_model_turn_id"].startswith(
            "run-1:turn:"
        )


def test_resolved_litellm_preserves_b09_and_records_distinct_b10(
    tmp_path,
):
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types
    from litellm.integrations.custom_logger import CustomLogger

    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    request = RuntimeRequest(
        agent_name="agent",
        model="openai/test",
        system_prompt="system",
        task_prompt="task",
        context={},
        runtime_context={"mode": "backtesting"},
        memory_state={},
        memory_notes=[],
        bound_tools=[],
        agent_run_id=collector.agent_run_id,
        boundary_collector=collector,
    )
    runtime = GoogleADKRuntime()
    user_success_calls = []

    class UserLogger(CustomLogger):
        def __init__(self):
            super().__init__()
            self.__call__ = self.async_log_success_event

        async def async_log_success_event(self, *_args, **_kwargs):
            user_success_calls.append("success")

    user_logger = UserLogger()

    model = agent_runtime._resolve_model_for_adk(
        request.model,
        model_entry_observer=runtime._model_entry_boundary_observer(
            request
        ),
        boundary_collector=collector,
    )
    model._additional_args.update(
        {
            "mock_response": "hello",
            "success_callback": [user_logger],
            "failure_callback": [user_logger],
            "metadata": {"user_metadata": "preserved"},
        }
    )
    original_additional_args = {
        key: (
            list(value)
            if type(value) is list
            else dict(value)
            if type(value) is dict
            else value
        )
        for key, value in model._additional_args.items()
    }
    global_state = _litellm_global_state()

    async def invoke_twice():
        responses = []
        for _ in range(2):
            llm_request = LlmRequest(
                model=request.model,
                contents=[
                    types.Content(
                        role="user",
                        parts=[types.Part(text="hello")],
                    )
                ],
                config=types.GenerateContentConfig(),
            )
            runtime._before_model_boundary_callback(request)(
                llm_request=llm_request
            )
            responses.extend(
                [
                    response
                    async for response in model.generate_content_async(
                        llm_request
                    )
                ]
            )
        for _ in range(100):
            if len(_events(collector, "B01_PROVIDER_TO_LITELLM")) == 2:
                break
            await asyncio.sleep(0.01)
        return responses

    responses = _run(invoke_twice())

    assert len(responses) == 2
    assert user_success_calls == ["success", "success"]
    assert model._additional_args == original_additional_args
    assert _litellm_global_state() == global_state
    b09_events = _events(collector, "B09_ADK_TO_LITELLM")
    b10_events = _events(collector, "B10_LITELLM_TO_PROVIDER")
    b01_events = _events(collector, "B01_PROVIDER_TO_LITELLM")
    assert len(b09_events) == len(b10_events) == len(b01_events) == 2
    assert [event["model_turn_id"] for event in b09_events] == [
        event["model_turn_id"] for event in b10_events
    ]
    assert all(
        event["payload"]["llm_request"]["model"] == "openai/test"
        for event in b09_events
    )
    assert all(
        event["payload"]["request"]["model"] == "test"
        for event in b10_events
    )
    assert all(
        event["payload"]["boundary_distinction"]
        == "litellm_provider_adapter_request_not_adk_model_entry"
        for event in b10_events
    )


def test_native_gemini_resolver_bypasses_litellm_logger(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )

    resolved = agent_runtime._resolve_model_for_adk(
        "gemini-3.1-flash-lite-preview",
        model_entry_observer=lambda _request: None,
        boundary_collector=collector,
    )

    assert resolved == "gemini-3.1-flash-lite-preview"
    assert _events(collector, "B10_LITELLM_TO_PROVIDER") == []
    assert _events(collector, "B01_PROVIDER_TO_LITELLM") == []


def test_run_async_passes_request_collector_to_model_resolver(
    monkeypatch,
    tmp_path,
):
    resolver_kwargs = []

    class FakeTypes:
        class GenerateContentConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class Part:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class Content:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeSessionService:
        async def create_session(self, **_kwargs):
            return None

    class FakeRunner:
        def __init__(self, *, agent, app_name):
            self.agent = agent
            self.app_name = app_name
            self.session_service = FakeSessionService()

        async def run_async(self, **_kwargs):
            if False:
                yield None

    class FakeRunConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    request = RuntimeRequest(
        agent_name="agent",
        model="openai/test",
        system_prompt="system",
        task_prompt="task",
        context={},
        runtime_context={"mode": "backtesting"},
        memory_state={},
        memory_notes=[],
        bound_tools=[],
        agent_run_id=collector.agent_run_id,
        boundary_collector=collector,
    )
    runtime = GoogleADKRuntime()
    monkeypatch.setattr(
        runtime,
        "_ensure_adk",
        lambda: (FakeAgent, FakeRunner, FakeTypes, object),
    )
    monkeypatch.setattr(
        runtime,
        "_maybe_build_gemini_thinking_planner",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        agent_runtime,
        "_resolve_model_for_adk",
        lambda model, **kwargs: (
            resolver_kwargs.append(kwargs) or model
        ),
    )
    monkeypatch.setattr(
        agent_runtime.importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(RunConfig=FakeRunConfig)
            if name == "google.adk.agents.run_config"
            else None
        ),
    )

    result = _run(runtime._run_async(request))

    assert result.events == []
    assert len(resolver_kwargs) == 1
    assert resolver_kwargs[0]["boundary_collector"] is collector
