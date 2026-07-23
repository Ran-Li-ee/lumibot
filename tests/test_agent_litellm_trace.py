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
    global_lists = {
        "callbacks": list(litellm.callbacks),
        "success_callback": list(litellm.success_callback),
        "failure_callback": list(litellm.failure_callback),
    }

    async def invoke():
        response = await litellm.acompletion(
            model="openai/test",
            messages=[{"role": "user", "content": "hello"}],
            mock_response="hello",
            metadata={"lumibot_model_turn_id": turn_id},
            success_callback=[logger.async_log_success_event],
            failure_callback=[logger.log_failure_event],
        )
        for _ in range(100):
            if _events(collector, "B01_PROVIDER_TO_LITELLM"):
                break
            await asyncio.sleep(0.01)
        return response

    response = _run(invoke())

    assert response.choices[0].message.content == "hello"
    assert litellm.callbacks == global_lists["callbacks"]
    assert litellm.success_callback == global_lists["success_callback"]
    assert litellm.failure_callback == global_lists["failure_callback"]
    assert len(_events(collector, "B10_LITELLM_TO_PROVIDER")) == 1
    assert len(_events(collector, "B01_PROVIDER_TO_LITELLM")) == 1


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
    global_lists = {
        "callbacks": list(litellm.callbacks),
        "success_callback": list(litellm.success_callback),
        "failure_callback": list(litellm.failure_callback),
    }

    async def invoke():
        with pytest.raises(litellm.InternalServerError) as raised:
            await litellm.acompletion(
                model="openai/test",
                messages=[{"role": "user", "content": "hello"}],
                mock_response="litellm.InternalServerError",
                metadata={"lumibot_model_turn_id": turn_id},
                success_callback=[logger.async_log_success_event],
                failure_callback=[logger.log_failure_event],
                num_retries=0,
            )
        for _ in range(100):
            if _events(collector, "B01_PROVIDER_TO_LITELLM"):
                break
            await asyncio.sleep(0.01)
        return raised.value

    error = _run(invoke())

    assert type(error).__name__ == "InternalServerError"
    assert litellm.callbacks == global_lists["callbacks"]
    assert litellm.success_callback == global_lists["success_callback"]
    assert litellm.failure_callback == global_lists["failure_callback"]
    b10 = _events(collector, "B10_LITELLM_TO_PROVIDER")
    b01 = _events(collector, "B01_PROVIDER_TO_LITELLM")
    assert len(b10) == len(b01) == 1
    assert b10[0]["status"] == "error"
    assert b01[0]["status"] == "error"
    assert b01[0]["error"]["type"] == "InternalServerError"
    assert "mock internal server error" in b01[0]["error"]["message"]


def test_litellm_mock_stream_records_one_provider_pair(tmp_path):
    import litellm

    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    logger = LiteLLMBoundaryLogger(collector)
    global_lists = {
        "callbacks": list(litellm.callbacks),
        "success_callback": list(litellm.success_callback),
        "failure_callback": list(litellm.failure_callback),
    }

    async def invoke():
        stream = await litellm.acompletion(
            model="openai/test",
            messages=[{"role": "user", "content": "hello"}],
            mock_response="stream hello",
            metadata={"lumibot_model_turn_id": turn_id},
            success_callback=[logger.async_log_success_event],
            failure_callback=[logger.log_failure_event],
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
    assert litellm.callbacks == global_lists["callbacks"]
    assert litellm.success_callback == global_lists["success_callback"]
    assert litellm.failure_callback == global_lists["failure_callback"]
    b10 = _events(collector, "B10_LITELLM_TO_PROVIDER")
    b01 = _events(collector, "B01_PROVIDER_TO_LITELLM")
    assert len(b10) == len(b01) == 1
    assert b10[0]["payload"]["request"]["stream"] is True


def test_observed_litellm_uses_fresh_callbacks_for_repeated_invocations(
    tmp_path,
):
    snapshots = []
    prepared = []
    observed = []

    async def user_success(*_args):
        return None

    def user_failure(*_args):
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
                        "success_callback",
                        "failure_callback",
                        "metadata",
                    }
                }
            )
            for key in (
                "callbacks",
                "success_callback",
                "failure_callback",
            ):
                self._additional_args[key].clear()
            yield llm_request

    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    observed_type = agent_runtime._build_observed_litellm_type(
        ProbeLiteLlm,
        on_model_entry=observed.append,
        prepare_model_entry=prepared.append,
        boundary_collector=collector,
    )
    original_metadata = {"user_metadata": "preserved"}
    original_callbacks = [user_callback]
    original_success = [user_success]
    original_failure = [user_failure]
    model = observed_type(
        callbacks=original_callbacks,
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
        "success_callback": original_success,
        "failure_callback": original_failure,
        "metadata": original_metadata,
    }
    assert len(snapshots) == 2
    for snapshot in snapshots:
        assert snapshot["callbacks"] == [user_callback]
        assert snapshot["success_callback"][0] is user_success
        assert snapshot["failure_callback"][0] is user_failure
        assert len(snapshot["success_callback"]) == 2
        assert len(snapshot["failure_callback"]) == 2
        success_hook = snapshot["success_callback"][1]
        failure_hook = snapshot["failure_callback"][1]
        assert success_hook.__name__ == "async_log_success_event"
        assert failure_hook.__name__ == "log_failure_event"
        assert isinstance(success_hook.__self__, LiteLLMBoundaryLogger)
        assert failure_hook.__self__ is success_hook.__self__
        assert success_hook.__self__.collector is collector
        assert snapshot["metadata"]["user_metadata"] == "preserved"
        assert snapshot["metadata"]["lumibot_agent_run_id"] == "run-1"
        assert snapshot["metadata"]["lumibot_model_turn_id"].startswith(
            "run-1:turn:"
        )


def test_resolved_litellm_preserves_b09_and_records_distinct_b10(
    tmp_path,
):
    import litellm
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types

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

    async def user_success(*_args):
        user_success_calls.append("success")

    def user_failure(*_args):
        raise AssertionError("failure callback should not run")

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
            "success_callback": [user_success],
            "failure_callback": [user_failure],
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
    global_lists = {
        "callbacks": list(litellm.callbacks),
        "success_callback": list(litellm.success_callback),
        "failure_callback": list(litellm.failure_callback),
    }

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
    assert litellm.callbacks == global_lists["callbacks"]
    assert litellm.success_callback == global_lists["success_callback"]
    assert litellm.failure_callback == global_lists["failure_callback"]
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
