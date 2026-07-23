import asyncio
import copy
import gc
import gzip
import inspect
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from threading import Event
from types import SimpleNamespace

import pytest
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from litellm.types.utils import (
    ChatCompletionMessageToolCall,
    Function,
    Message,
    ModelResponse,
)
from pydantic import BaseModel, ConfigDict

from lumibot.components.agents import runtime as agent_runtime
from lumibot.components.agents.boundary_trace import BoundaryTraceCollector
from lumibot.components.agents.runtime import (
    GoogleADKRuntime,
    RuntimeRequest,
    StubAgentRuntime,
    _build_observed_function_tool,
    _normalize_event,
    _wrap_tool_callable,
)
from lumibot.components.agents.schemas import BoundTool
from lumibot.components.agents.tool_context import (
    current_agent_tool_context,
)


def _events(collector, transition):
    return [
        event
        for event in collector.export()["events"]
        if event["transition"] == transition
    ]


def _event_timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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


def _model_response(*, response_id, tool_calls=None, text=None):
    calls = (
        [
            ChatCompletionMessageToolCall(
                id=call_id,
                type="function",
                function=Function(
                    name="market_last_price",
                    arguments=json.dumps({"symbol": symbol}),
                ),
            )
            for call_id, symbol in tool_calls
        ]
        if tool_calls is not None
        else None
    )
    return ModelResponse(
        id=response_id,
        model="openai/test",
        choices=[
            {
                "index": 0,
                "message": Message(
                    role="assistant",
                    content=text,
                    tool_calls=calls,
                ),
                "finish_reason": (
                    "tool_calls" if tool_calls is not None else "stop"
                ),
            }
        ],
        usage={
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    )


async def delayed_price_tool(symbol: str):
    await asyncio.sleep(0.03 if symbol == "QQQ" else 0.005)
    return {
        "symbol": symbol,
        "price": {
            "QQQ": 100.0,
            "SPY": 90.0,
            "IWM": 80.0,
        }[symbol],
    }


def install_scripted_acompletion(monkeypatch):
    from google.adk.models.lite_llm import LiteLLMClient

    responses = iter(
        [
            _model_response(
                response_id="response-turn-1",
                tool_calls=[
                    ("call_A", "QQQ"),
                    ("call_B", "SPY"),
                ],
            ),
            _model_response(
                response_id="response-turn-2",
                tool_calls=[("call_C", "IWM")],
            ),
            _model_response(
                response_id="response-turn-3",
                text="RESULT: complete",
            ),
        ]
    )
    provider_requests = []

    async def scripted_acompletion(
        _client,
        model,
        messages,
        tools,
        **kwargs,
    ):
        callback_counts = {}
        safe_kwargs = {}
        for key, value in kwargs.items():
            if "callback" in key:
                callback_counts[key] = len(
                    value
                    if type(value) in (list, tuple)
                    else [value]
                )
            else:
                safe_kwargs[key] = copy.deepcopy(value)
        provider_requests.append(
            {
                "model": model,
                "messages": copy.deepcopy(messages),
                "tools": copy.deepcopy(tools),
                "kwargs": safe_kwargs,
                "callback_counts": callback_counts,
            }
        )
        return next(responses)

    monkeypatch.setattr(
        LiteLLMClient,
        "acompletion",
        scripted_acompletion,
    )
    return provider_requests


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


def _batch_id(trace, batch):
    batch_ids = list(
        dict.fromkeys(
            event["tool_batch_id"]
            for event in _trace_events(
                trace,
                "B03_ADK_TO_FUNCTION_TOOL",
            )
        )
    )
    return batch_ids[batch - 1]


def calls_in_model_order(trace, *, batch):
    batch_id = _batch_id(trace, batch)
    calls = [
        event
        for event in _trace_events(
            trace,
            "B03_ADK_TO_FUNCTION_TOOL",
        )
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
    batch_id = _batch_id(trace, batch)
    calls = [
        event
        for event in _trace_events(
            trace,
            "B08_FUNCTION_TOOL_TO_ADK",
        )
        if event["tool_batch_id"] == batch_id
    ]
    return [
        event["call_id"]
        for event in sorted(
            calls,
            key=lambda event: event["payload"][
                "completion_sequence"
            ],
        )
    ]


def response_for(trace, call_id):
    event = next(
        event
        for event in _trace_events(
            trace,
            "B08_FUNCTION_TOOL_TO_ADK",
        )
        if event["call_id"] == call_id
    )
    return event["payload"]["function_response"]


def next_provider_request(trace, *, after_call):
    response_event = next(
        event
        for event in _trace_events(
            trace,
            "B08_FUNCTION_TOOL_TO_ADK",
        )
        if event["call_id"] == after_call
    )
    return next(
        event
        for event in _trace_events(
            trace,
            "B10_LITELLM_TO_PROVIDER",
        )
        if event["sequence"] > response_event["sequence"]
        and after_call
        in json.dumps(event["payload"], sort_keys=True)
    )


def _litellm_global_callback_state():
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

    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
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
    assert b04["call_id"] == "call_A"
    assert b04["model_turn_id"] == turn_id
    assert b04["tool_batch_id"] == batch_id
    assert b04["payload"]["call_id_source"] == "provider"
    assert b04["payload"]["model_arguments"]["unknown"] == "removed"
    assert b04["payload"]["wrapper_received_arguments"]["request"] == {
        "symbol": "QQQ"
    }
    assert b04["payload"]["adk_function_call_arguments"] == {
        "request": {"symbol": "QQQ"},
        "unknown": "removed",
    }
    assert b04["payload"]["function_tool_preprocessed_arguments"] == {
        "request": {"symbol": "QQQ"},
        "unknown": "removed",
    }
    assert b04["payload"]["function_tool_filtered_arguments"] == {
        "request": {"symbol": "QQQ"}
    }
    assert b04["payload"]["final_positional_arguments"] == []
    assert b04["payload"]["final_keyword_arguments"]["request"] == {
        "symbol": "QQQ"
    }
    assert b04["payload"][
        "effective_python_arguments_with_defaults"
    ] == {
        "request": {"symbol": "QQQ"},
        "asset_type": "stock",
    }
    assert b04["payload"]["removed_arguments"] == ["unknown"]
    assert b04["payload"]["converted_arguments"] == [
        {
            "name": "request",
            "from_type": "dict",
            "to_type": "SymbolInput",
        }
    ]
    assert b04["payload"]["defaulted_arguments"] == [
        {"name": "asset_type", "value": "stock"}
    ]
    assert b04["payload"]["confirmation_status"] == "not_present"
    assert b04["payload"]["tool_confirmation_present"] is False
    assert b04["payload"]["validation_error"] is None
    assert b04["payload"]["argument_stage_fidelity"][
        "function_tool_preprocessed_arguments"
    ]["source"] == "FunctionTool._preprocess_args return"
    assert collector.call_state(b04["payload"]["observation_id"]) == {}
    b05 = _events(collector, "B05_WRAPPER_TO_PYTHON_TOOL")[0]
    assert b05["payload"]["effective_arguments"]["asset_type"] == "stock"
    assert _event_timestamp(b04["ended_at"]) <= _event_timestamp(
        b05["started_at"]
    )
    call_transitions = [
        event["transition"]
        for event in collector.export()["events"]
        if event["call_id"] == "call_A"
        and event["transition"]
        in {
            "B04_FUNCTION_TOOL_TO_WRAPPER",
            "B05_WRAPPER_TO_PYTHON_TOOL",
            "B06_PYTHON_TOOL_TO_WRAPPER",
            "B07_WRAPPER_TO_FUNCTION_TOOL",
        }
    ]
    assert call_transitions == [
        "B04_FUNCTION_TOOL_TO_WRAPPER",
        "B05_WRAPPER_TO_PYTHON_TOOL",
        "B06_PYTHON_TOOL_TO_WRAPPER",
        "B07_WRAPPER_TO_FUNCTION_TOOL",
    ]


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

    result = asyncio.run(
        observed.run_async(args={}, tool_context=FakeToolContext())
    )

    assert executed == []
    assert "mandatory input parameters" in result["error"]
    b04 = _events(collector, "B04_FUNCTION_TOOL_TO_WRAPPER")[0]
    assert b04["status"] == "blocked"
    assert b04["payload"]["missing_mandatory_arguments"] == ["symbol"]
    assert _events(collector, "B05_WRAPPER_TO_PYTHON_TOOL") == []
    assert collector.call_state(b04["payload"]["observation_id"]) == {}


def test_confirmation_metadata_precedes_confirmed_wrapper_execution(tmp_path):
    class ConfirmedToolContext(FakeToolContext):
        tool_confirmation = SimpleNamespace(confirmed=True)

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(
            name="echo",
            description="echo",
            function=lambda value: value,
        ),
        collector=collector,
        shared_tool_context={},
    )
    observed._require_confirmation = True

    result = asyncio.run(
        observed.run_async(
            args={"value": "received"},
            tool_context=ConfirmedToolContext(),
        )
    )

    assert result == "received"
    b04 = _events(collector, "B04_FUNCTION_TOOL_TO_WRAPPER")[0]
    assert b04["status"] == "success"
    assert b04["payload"]["confirmation_status"] == "confirmed"
    assert b04["payload"]["tool_confirmation_present"] is True
    transitions = [
        event["transition"]
        for event in collector.export()["events"]
        if event["call_id"] == "call_A"
    ]
    assert transitions == [
        "B04_FUNCTION_TOOL_TO_WRAPPER",
        "B05_WRAPPER_TO_PYTHON_TOOL",
        "B06_PYTHON_TOOL_TO_WRAPPER",
        "B07_WRAPPER_TO_FUNCTION_TOOL",
    ]


def test_missing_confirmation_records_blocked_b04_without_b05(tmp_path):
    class ConfirmationToolContext(FakeToolContext):
        def __init__(self):
            self.tool_confirmation = None
            self.requested_confirmation = False

        def request_confirmation(self, **_kwargs):
            self.requested_confirmation = True

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(
            name="echo",
            description="echo",
            function=lambda value: value,
        ),
        collector=collector,
        shared_tool_context={},
    )
    observed._require_confirmation = True
    tool_context = ConfirmationToolContext()

    result = asyncio.run(
        observed.run_async(
            args={"value": "received"},
            tool_context=tool_context,
        )
    )

    assert "requires confirmation" in result["error"]
    assert tool_context.requested_confirmation is True
    b04 = _events(collector, "B04_FUNCTION_TOOL_TO_WRAPPER")[0]
    assert b04["status"] == "blocked"
    assert b04["payload"]["confirmation_status"] == "required_missing"
    assert b04["payload"]["tool_confirmation_present"] is False
    assert b04["payload"]["validation_error"]["type"] == (
        "FunctionToolValidationBlocked"
    )
    assert _events(collector, "B05_WRAPPER_TO_PYTHON_TOOL") == []


def test_missing_required_arguments_exclude_adk_injected_tool_context(tmp_path):
    executed = []

    def tool(symbol: str, tool_context: ToolContext):
        executed.append((symbol, tool_context))
        return {"symbol": symbol}

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(name="tool", description="tool", function=tool),
        collector=collector,
        shared_tool_context={},
    )

    result = asyncio.run(
        observed.run_async(args={}, tool_context=FakeToolContext())
    )

    assert executed == []
    assert "mandatory input parameters" in result["error"]
    b04 = _events(collector, "B04_FUNCTION_TOOL_TO_WRAPPER")[0]
    assert b04["status"] == "blocked"
    assert b04["payload"]["missing_mandatory_arguments"] == ["symbol"]
    assert _events(collector, "B05_WRAPPER_TO_PYTHON_TOOL") == []


def test_preprocessing_error_records_b04_error_and_reraises_unchanged(tmp_path):
    executed = []

    def tool(symbol):
        executed.append(symbol)
        return {"symbol": symbol}

    tool.__annotations__ = {"symbol": "MissingToolAnnotation"}
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(name="tool", description="tool", function=tool),
        collector=collector,
        shared_tool_context={},
    )

    with pytest.raises(NameError) as raised:
        asyncio.run(
            observed.run_async(
                args={"symbol": "QQQ"},
                tool_context=FakeToolContext(),
            )
        )

    assert "MissingToolAnnotation" in str(raised.value)
    assert executed == []
    b04_events = _events(collector, "B04_FUNCTION_TOOL_TO_WRAPPER")
    assert len(b04_events) == 1
    b04 = b04_events[0]
    assert b04["status"] == "error"
    assert b04["error"]["type"] == "NameError"
    assert b04["payload"]["validation_error"]["type"] == "NameError"
    assert b04["payload"]["function_tool_preprocessed_arguments"] is None
    assert b04["payload"]["wrapper_received_arguments"] is None
    assert _events(collector, "B05_WRAPPER_TO_PYTHON_TOOL") == []
    assert collector.call_state(b04["payload"]["observation_id"]) == {}


def test_collector_call_state_is_detached_from_mutable_arguments(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    arguments = {"request": {"symbol": "QQQ"}}

    collector.note_wrapper_arguments("call_A", arguments)
    arguments["request"]["symbol"] = "SPY"
    first_state = collector.call_state("call_A")
    first_state["wrapper_received_arguments"]["request"]["symbol"] = "IWM"

    detached_state = collector.call_state("call_A")
    assert detached_state["wrapper_invoked"] is True
    assert detached_state["wrapper_received_arguments"] == {
        "request": {"symbol": "QQQ"}
    }
    assert detached_state["wrapper_argument_types"] == {
        "request": "dict"
    }


def test_uncopyable_wrapper_arguments_preserve_successful_boundary_state(tmp_path):
    class UncopyableArgument:
        def __deepcopy__(self, _memo):
            raise RuntimeError("argument copy unavailable")

        def __str__(self):
            return "[uncopyable argument]"

        def __repr__(self):
            raise AssertionError("raw repr must not be called")

    received = []

    def tool(value):
        received.append(value)
        return {"ok": True}

    value = UncopyableArgument()
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(name="tool", description="tool", function=tool),
        collector=collector,
        shared_tool_context={},
    )

    result = asyncio.run(
        observed.run_async(
            args={"value": value},
            tool_context=FakeToolContext(),
        )
    )

    assert result == {"ok": True}
    assert received == [value]
    assert _events(collector, "B05_WRAPPER_TO_PYTHON_TOOL")[0]["status"] == "success"
    b04 = _events(collector, "B04_FUNCTION_TOOL_TO_WRAPPER")[0]
    assert b04["status"] == "success"
    safe_value = b04["payload"]["wrapper_received_arguments"]["value"]
    assert safe_value["python_type"] == "UncopyableArgument"
    assert safe_value["preview"].endswith("UncopyableArgument instance>")
    assert "0x" not in safe_value["preview"]
    assert any(
        diagnostic["kind"] == "wrapper_arguments_snapshot_failed"
        for diagnostic in collector.export()["diagnostics"]
    )


def test_call_state_uses_safe_fallback_when_detachment_fails(tmp_path):
    class UncopyableArgument:
        def __deepcopy__(self, _memo):
            raise RuntimeError("state copy unavailable")

        def __repr__(self):
            raise AssertionError("raw repr must not be called")

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    collector._call_index["call_A"] = {
        "wrapper_invoked": True,
        "wrapper_received_arguments": {"value": UncopyableArgument()},
    }

    state = collector.call_state("call_A")

    assert state["wrapper_invoked"] is True
    safe_value = state["wrapper_received_arguments"]["value"]
    assert safe_value["python_type"] == "UncopyableArgument"
    assert safe_value["preview"].endswith("UncopyableArgument instance>")
    assert "0x" not in safe_value["preview"]
    assert any(
        diagnostic["kind"] == "call_state_snapshot_failed"
        for diagnostic in collector.export()["diagnostics"]
    )


def test_observed_function_tool_without_collector_is_ordinary_function_tool():
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(
            name="echo",
            description="echo",
            function=lambda value="default": value,
        ),
        collector=None,
        shared_tool_context={},
    )

    assert type(observed) is FunctionTool
    assert (
        asyncio.run(
            observed.run_async(
                args={"value": "received"},
                tool_context=FakeToolContext(),
            )
        )
        == "received"
    )


def test_observed_function_tool_generates_missing_call_id(tmp_path):
    class MissingIdToolContext(FakeToolContext):
        function_call_id = None

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(
            name="echo",
            description="echo",
            function=lambda value: value,
        ),
        collector=collector,
        shared_tool_context={},
    )

    assert (
        asyncio.run(
            observed.run_async(
                args={"value": "received"},
                tool_context=MissingIdToolContext(),
            )
        )
        == "received"
    )

    b04 = _events(collector, "B04_FUNCTION_TOOL_TO_WRAPPER")[0]
    assert b04["call_id"].startswith("generated:function_tool:")
    assert (
        b04["payload"]["call_id_source"]
        == "generated_missing_function_tool_id"
    )


def test_unreadable_provider_call_id_falls_back_without_blocking_tool(tmp_path):
    class UnreadableCallId:
        def __bool__(self):
            return True

        def __str__(self):
            raise RuntimeError("call ID unavailable")

    class UnreadableIdToolContext(FakeToolContext):
        function_call_id = UnreadableCallId()

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(
            name="echo",
            description="echo",
            function=lambda value: value,
        ),
        collector=collector,
        shared_tool_context={},
    )

    result = asyncio.run(
        observed.run_async(
            args={"value": "received"},
            tool_context=UnreadableIdToolContext(),
        )
    )

    assert result == "received"
    b04 = _events(collector, "B04_FUNCTION_TOOL_TO_WRAPPER")[0]
    assert b04["call_id"].startswith("generated:function_tool:")
    assert (
        b04["payload"]["call_id_source"]
        == "generated_missing_function_tool_id"
    )


def test_repeated_provider_call_id_does_not_reuse_wrapper_state(tmp_path):
    executed = []

    def tool(symbol: str):
        executed.append(symbol)
        return {"symbol": symbol}

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(name="tool", description="tool", function=tool),
        collector=collector,
        shared_tool_context={},
    )

    first = asyncio.run(
        observed.run_async(
            args={"symbol": "QQQ"},
            tool_context=FakeToolContext(),
        )
    )
    second = asyncio.run(
        observed.run_async(args={}, tool_context=FakeToolContext())
    )

    assert first == {"symbol": "QQQ"}
    assert "mandatory input parameters" in second["error"]
    assert executed == ["QQQ"]
    b04_events = _events(collector, "B04_FUNCTION_TOOL_TO_WRAPPER")
    assert [event["status"] for event in b04_events] == ["success", "blocked"]
    assert b04_events[1]["payload"]["wrapper_received_arguments"] is None
    assert b04_events[1]["payload"]["missing_mandatory_arguments"] == ["symbol"]


def test_parallel_distinct_call_ids_keep_observations_isolated(tmp_path):
    class YieldingFunctionTool(FunctionTool):
        async def _invoke_callable(self, target, args_to_call):
            await asyncio.sleep(0)
            return await super()._invoke_callable(target, args_to_call)

    class ParallelToolContext(FakeToolContext):
        def __init__(self, call_id):
            self.function_call_id = call_id
            self.tool_confirmation = None

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    turn_id = collector.start_model_turn()
    collector.register_tool_batch(turn_id, ["call_A", "call_B"])
    observed = _build_observed_function_tool(
        YieldingFunctionTool,
        BoundTool(
            name="echo",
            description="echo",
            function=lambda value: {"value": value},
        ),
        collector=collector,
        shared_tool_context={},
    )

    async def run_parallel():
        return await asyncio.gather(
            observed.run_async(
                args={"value": "A"},
                tool_context=ParallelToolContext("call_A"),
            ),
            observed.run_async(
                args={"value": "B"},
                tool_context=ParallelToolContext("call_B"),
            ),
        )

    assert asyncio.run(run_parallel()) == [
        {"value": "A"},
        {"value": "B"},
    ]
    b04_events = _events(collector, "B04_FUNCTION_TOOL_TO_WRAPPER")
    assert {
        event["call_id"]: event["payload"]["model_arguments"]["value"]
        for event in b04_events
    } == {"call_A": "A", "call_B": "B"}
    observation_ids = {
        event["payload"]["observation_id"] for event in b04_events
    }
    assert len(observation_ids) == 2
    assert all(
        collector.call_state(observation_id) == {}
        for observation_id in observation_ids
    )


def test_parallel_duplicate_provider_ids_use_unique_observation_state(tmp_path):
    class YieldingFunctionTool(FunctionTool):
        async def _invoke_callable(self, target, args_to_call):
            await asyncio.sleep(0)
            return await super()._invoke_callable(target, args_to_call)

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    observed = _build_observed_function_tool(
        YieldingFunctionTool,
        BoundTool(
            name="echo",
            description="echo",
            function=lambda value: {"value": value},
        ),
        collector=collector,
        shared_tool_context={},
    )

    async def run_parallel():
        return await asyncio.gather(
            observed.run_async(
                args={"value": "first"},
                tool_context=FakeToolContext(),
            ),
            observed.run_async(
                args={"value": "second"},
                tool_context=FakeToolContext(),
            ),
        )

    assert asyncio.run(run_parallel()) == [
        {"value": "first"},
        {"value": "second"},
    ]
    boundary_events = [
        event
        for event in collector.export()["events"]
        if event["transition"]
        in {
            "B04_FUNCTION_TOOL_TO_WRAPPER",
            "B05_WRAPPER_TO_PYTHON_TOOL",
            "B06_PYTHON_TOOL_TO_WRAPPER",
            "B07_WRAPPER_TO_FUNCTION_TOOL",
        }
    ]
    assert {event["call_id"] for event in boundary_events} == {"call_A"}
    by_observation = {}
    for event in boundary_events:
        observation_id = event["payload"]["observation_id"]
        by_observation.setdefault(observation_id, []).append(event)

    assert len(by_observation) == 2
    assert {
        events[0]["payload"]["model_arguments"]["value"]
        for events in by_observation.values()
    } == {"first", "second"}
    assert all(
        [event["transition"] for event in events]
        == [
            "B04_FUNCTION_TOOL_TO_WRAPPER",
            "B05_WRAPPER_TO_PYTHON_TOOL",
            "B06_PYTHON_TOOL_TO_WRAPPER",
            "B07_WRAPPER_TO_FUNCTION_TOOL",
        ]
        for events in by_observation.values()
    )
    assert all(
        collector.call_state(observation_id) == {}
        for observation_id in by_observation
    )


def test_function_tool_observation_failures_preserve_success_result(
    monkeypatch, tmp_path
):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(
            name="echo",
            description="echo",
            function=lambda value: {"value": value},
        ),
        collector=collector,
        shared_tool_context={},
    )
    monkeypatch.setattr(
        collector,
        "call_state",
        lambda _call_id: (_ for _ in ()).throw(RuntimeError("state failed")),
    )
    monkeypatch.setattr(
        collector,
        "record",
        lambda **_event: (_ for _ in ()).throw(RuntimeError("record failed")),
    )

    result = asyncio.run(
        observed.run_async(
            args={"value": "received"},
            tool_context=FakeToolContext(),
        )
    )

    assert result == {"value": "received"}


def test_pre_invoke_observation_failure_does_not_block_wrapper(
    monkeypatch, tmp_path
):
    executed = []

    def tool(value):
        executed.append(value)
        return {"value": value}

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(name="echo", description="echo", function=tool),
        collector=collector,
        shared_tool_context={},
    )
    monkeypatch.setattr(
        collector,
        "note_wrapper_arguments",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("entry observation failed")
        ),
    )

    result = asyncio.run(
        observed.run_async(
            args={"value": "received"},
            tool_context=FakeToolContext(),
        )
    )

    assert result == {"value": "received"}
    assert executed == ["received"]
    assert _events(collector, "B04_FUNCTION_TOOL_TO_WRAPPER") == []
    assert [
        event["transition"]
        for event in collector.export()["events"]
        if event["call_id"] == "call_A"
    ] == [
        "B05_WRAPPER_TO_PYTHON_TOOL",
        "B06_PYTHON_TOOL_TO_WRAPPER",
        "B07_WRAPPER_TO_FUNCTION_TOOL",
    ]
    assert any(
        diagnostic["kind"] == "pre_invoke_observation_failed"
        for diagnostic in collector.export()["diagnostics"]
    )


def test_b04_payload_observation_failure_preserves_success_result(
    monkeypatch, tmp_path
):
    class BrokenState:
        def get(self, _key, _default=None):
            raise RuntimeError("state payload failed")

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(
            name="echo",
            description="echo",
            function=lambda value: {"value": value},
        ),
        collector=collector,
        shared_tool_context={},
    )
    monkeypatch.setattr(
        collector,
        "call_state",
        lambda _call_id: BrokenState(),
    )

    result = asyncio.run(
        observed.run_async(
            args={"value": "received"},
            tool_context=FakeToolContext(),
        )
    )

    assert result == {"value": "received"}


def test_function_tool_observation_failures_preserve_validation_result(
    monkeypatch, tmp_path
):
    executed = []

    def tool(symbol: str):
        executed.append(symbol)
        return {"symbol": symbol}

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(name="tool", description="tool", function=tool),
        collector=collector,
        shared_tool_context={},
    )
    monkeypatch.setattr(
        collector,
        "call_ids",
        lambda _call_id: (_ for _ in ()).throw(RuntimeError("lookup failed")),
    )

    result = asyncio.run(
        observed.run_async(args={}, tool_context=FakeToolContext())
    )

    assert executed == []
    assert "mandatory input parameters" in result["error"]


def test_normalize_event_preserves_raw_and_function_call_ids():
    event = SimpleNamespace(
        id="event-1",
        invocation_id="invocation-1",
        content=SimpleNamespace(
            parts=[
                SimpleNamespace(thought=True, text="reasoning"),
                SimpleNamespace(thought=False, text="answer"),
                SimpleNamespace(
                    thought=False,
                    text=None,
                    function_call=SimpleNamespace(
                        id="call-1",
                        name="lookup",
                        args={"symbol": "QQQ"},
                    ),
                ),
                SimpleNamespace(
                    thought=False,
                    text=None,
                    function_response=SimpleNamespace(
                        id="call-2",
                        name="lookup",
                        response={
                            "content": [{"text": "tool output"}],
                            "value": 42,
                        },
                    ),
                ),
            ]
        ),
        usage_metadata={"total_token_count": 3},
    )

    normalized = _normalize_event(event)

    assert normalized
    assert {item.event_id for item in normalized} == {"event-1"}
    assert {item.invocation_id for item in normalized} == {"invocation-1"}
    tool_call = next(item for item in normalized if item.kind == "tool_call")
    assert tool_call.call_id == "call-1"
    response_events = [
        item
        for item in normalized
        if item.tool_name == "lookup" and item.kind in {"text", "tool_result"}
    ]
    assert response_events
    assert {item.call_id for item in response_events} == {"call-2"}


def test_before_model_records_post_pruning_request(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
        inline_payload_limit=500_000,
    )
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
                        response={
                            "value": str(index) + ("x" * 10_000)
                        },
                    )
                ],
            )
            for index in range(5)
        ],
        config=types.GenerateContentConfig(),
        model="openai/gpt-5.4-mini",
    )
    runtime = GoogleADKRuntime()
    before_pruning = agent_runtime._adk_object_payload(llm_request)

    prune = runtime._before_model_context_pruning_callback(request)
    capture = runtime._before_model_boundary_callback(request)
    assert prune is not None
    prune(callback_context=None, llm_request=llm_request)
    capture(callback_context=None, llm_request=llm_request)
    assert _events(collector, "B09_ADK_TO_LITELLM") == []
    after_pruning = agent_runtime._adk_object_payload(llm_request)

    llm_request.config.labels = {"adk_agent_name": "agent"}
    runtime._model_entry_boundary_observer(request)(llm_request)
    b09 = _events(collector, "B09_ADK_TO_LITELLM")[0]
    assert b09["model_turn_id"] == collector.active_model_turn()
    assert b09["payload"]["previous_model_turn_id"] is None
    assert b09["payload"]["current_model_turn_id"] == b09["model_turn_id"]
    pruning = b09["payload"]["context_pruning"]
    assert pruning["pruned"] is True
    assert pruning["pruned_tool_results"] == 1
    assert pruning["omitted_function_response_count"] == 1
    assert pruning["omitted_function_response_bytes"] > 0
    assert pruning["reason"] == "older_tool_result_history_limit"
    assert pruning["before"]["request"] == before_pruning
    assert pruning["before"]["function_response_count"] == 5
    assert pruning["after"]["request"] == after_pruning
    assert pruning["after"]["function_response_count"] == 5
    assert pruning["before"]["request_bytes"] > (
        pruning["after"]["request_bytes"]
    )
    assert pruning["notices"]
    assert b09["payload"]["llm_request"]["config"]["labels"] == {
        "adk_agent_name": "agent"
    }
    assert (
        "Older tool result omitted by Lumibot before this model call because "
        "the provider context window would otherwise be exceeded. Use the most "
        "recent visible tool results or call a targeted tool again if this older "
        "detail is still required."
    ) in str(b09["payload"]["llm_request"])


def test_pruning_forensics_uses_boundary_sidecar_when_payload_is_large(
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
        inline_payload_limit=200,
    )
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
                        name=f"tool_{index}",
                        response={"value": "x" * 500},
                    )
                ],
            )
            for index in range(5)
        ],
        config=types.GenerateContentConfig(),
        model=request.model,
    )
    runtime = GoogleADKRuntime()

    runtime._before_model_context_pruning_callback(request)(
        llm_request=llm_request
    )
    runtime._before_model_boundary_callback(request)(
        llm_request=llm_request
    )
    runtime._model_entry_boundary_observer(request)(llm_request)

    b09 = _events(collector, "B09_ADK_TO_LITELLM")[0]
    sidecar_path = b09["payload_meta"]["sidecar_path"]
    assert sidecar_path
    assert (tmp_path / sidecar_path).is_file()
    assert b09["payload_meta"]["compression"] == "gzip"
    assert b09["payload_meta"]["truncated"] is False


def test_observed_litellm_captures_final_live_adk_request_labels(tmp_path):
    from google.adk.agents.llm_agent import LlmAgent
    from google.adk.agents.run_config import RunConfig
    from google.adk.models.lite_llm import LiteLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import InMemoryRunner

    actual_model_entry_labels = []

    class ProbeLiteLlm(LiteLlm):
        async def generate_content_async(
            self,
            llm_request,
            stream=False,
        ):
            actual_model_entry_labels.append(
                dict(llm_request.config.labels)
            )
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="probe complete")],
                ),
                partial=False,
                turn_complete=True,
            )

    async def run_probe():
        collector = BoundaryTraceCollector(
            agent_run_id="run-1",
            artifact_root=tmp_path,
        )
        request = make_runtime_request(collector)
        runtime = GoogleADKRuntime()
        observed_type = agent_runtime._build_observed_litellm_type(
            ProbeLiteLlm,
            on_model_entry=runtime._model_entry_boundary_observer(
                request
            ),
        )
        agent = LlmAgent(
            name="agent",
            model=observed_type(model="openai/test"),
            instruction="Respond briefly.",
            before_model_callback=runtime._before_model_boundary_callback(
                request
            ),
        )
        runner = InMemoryRunner(agent=agent, app_name="boundary-probe")
        await runner.session_service.create_session(
            app_name=runner.app_name,
            user_id="probe-user",
            session_id="probe-session",
        )
        events = [
            event
            async for event in runner.run_async(
                user_id="probe-user",
                session_id="probe-session",
                new_message=types.Content(
                    role="user",
                    parts=[types.Part(text="probe")],
                ),
                run_config=RunConfig(),
            )
        ]
        return collector, events

    collector, events = asyncio.run(run_probe())

    assert events
    assert actual_model_entry_labels == [{"adk_agent_name": "agent"}]
    b09 = _events(collector, "B09_ADK_TO_LITELLM")[0]
    traced_labels = b09["payload"]["llm_request"]["config"]["labels"]
    assert traced_labels == actual_model_entry_labels[0]
    assert b09["payload"]["capture_point"] == (
        "observed_litellm_generate_content_async_entry"
    )


def test_cerebras_b09_matches_sanitized_underlying_litellm_entry(
    tmp_path,
    monkeypatch,
):
    from google.adk.models import lite_llm as adk_lite_llm
    from google.adk.models.llm_request import LlmRequest

    underlying_requests = []
    prepare_calls = []
    original_litellm_type = adk_lite_llm.LiteLlm
    original_sanitizer = (
        agent_runtime._strip_thought_parts_from_litellm_request
    )

    class ProbeLiteLlm(original_litellm_type):
        async def generate_content_async(
            self,
            llm_request,
            stream=False,
        ):
            underlying_requests.append(
                llm_request.model_dump(mode="json", exclude_none=True)
            )
            if False:
                yield

    def track_sanitizer(llm_request):
        prepare_calls.append(llm_request)
        original_sanitizer(llm_request)

    monkeypatch.setattr(adk_lite_llm, "LiteLlm", ProbeLiteLlm)
    monkeypatch.setattr(
        agent_runtime,
        "_strip_thought_parts_from_litellm_request",
        track_sanitizer,
    )
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    request = make_runtime_request(
        collector,
        model="cerebras/gpt-oss-120b",
    )
    runtime = GoogleADKRuntime()
    model = agent_runtime._resolve_model_for_adk(
        request.model,
        model_entry_observer=runtime._model_entry_boundary_observer(
            request
        ),
    )
    llm_request = LlmRequest(
        model=request.model,
        contents=[
            types.Content(
                role="model",
                parts=[
                    types.Part(text="private reasoning", thought=True),
                    types.Part(text="visible answer"),
                ],
            )
        ],
        config=types.GenerateContentConfig(
            labels={"adk_agent_name": "agent"}
        ),
    )
    runtime._before_model_boundary_callback(request)(
        llm_request=llm_request
    )

    async def run_probe():
        return [
            response
            async for response in model.generate_content_async(llm_request)
        ]

    assert asyncio.run(run_probe()) == []
    assert type(model).__mro__[1] is ProbeLiteLlm
    assert prepare_calls == [llm_request]
    assert len(underlying_requests) == 1
    b09 = _events(collector, "B09_ADK_TO_LITELLM")[0]
    assert b09["payload"]["llm_request"] == underlying_requests[0]
    assert b09["payload"]["llm_request"]["config"]["labels"] == {
        "adk_agent_name": "agent"
    }
    assert "private reasoning" not in str(underlying_requests[0])
    assert "visible answer" in str(underlying_requests[0])


def test_model_entry_records_previous_and_current_turn_ids(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    request = make_runtime_request(collector)
    runtime = GoogleADKRuntime()
    before = runtime._before_model_boundary_callback(request)
    observe = runtime._model_entry_boundary_observer(request)

    for index in range(2):
        llm_request = SimpleNamespace(
            contents=[],
            config=types.GenerateContentConfig(
                labels={"adk_agent_name": "agent"}
            ),
            model=f"model-{index}",
        )
        before(llm_request=llm_request)
        observe(llm_request)

    first, second = _events(collector, "B09_ADK_TO_LITELLM")
    assert first["payload"]["previous_model_turn_id"] is None
    assert second["payload"]["previous_model_turn_id"] == (
        first["model_turn_id"]
    )
    assert second["payload"]["current_model_turn_id"] == (
        second["model_turn_id"]
    )


def test_after_model_assigns_one_batch_and_preserves_exposed_thoughts(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    request = make_runtime_request(collector)
    runtime = GoogleADKRuntime()
    before = runtime._before_model_boundary_callback(request)
    before(
        callback_context=None,
        llm_request=SimpleNamespace(contents=[], model=request.model),
    )
    response = SimpleNamespace(
        content=types.Content(
            role="model",
            parts=[
                types.Part(text="Visible model analysis", thought=True),
                types.Part.from_function_call(
                    name="market_last_price",
                    args={"symbol": "QQQ"},
                ),
                types.Part.from_function_call(
                    name="market_last_price",
                    args={"symbol": "SPY"},
                ),
            ],
        ),
        usage_metadata={"total_token_count": 17},
        finish_reason="tool_calls",
        provider_metadata={"response_id": "response-1"},
    )
    response.content.parts[1].function_call.id = "call_A"
    response.content.parts[2].function_call.id = "call_B"

    runtime._after_model_boundary_callback(request)(
        callback_context=None,
        llm_response=response,
    )

    call_a = collector.call_ids("call_A")
    call_b = collector.call_ids("call_B")
    assert call_a["tool_batch_id"] == call_b["tool_batch_id"]
    assert call_a["call_sequence"] == 1
    assert call_b["call_sequence"] == 2
    assert collector.batch_call_ids(call_a["tool_batch_id"]) == [
        "call_A",
        "call_B",
    ]
    b02 = _events(collector, "B02_LITELLM_TO_ADK")[0]
    assert b02["payload"]["llm_response"]["finish_reason"] == "tool_calls"
    assert b02["payload"]["llm_response"]["usage_metadata"] == {
        "total_token_count": 17
    }
    assert "Visible model analysis" in str(
        b02["payload"]["provider_exposed_thought_parts"]
    )
    assert b02["payload"]["reasoning_visibility"] == (
        "provider_exposed_only"
    )
    assert "hidden" not in str(b02["payload"]).lower()


def test_after_model_marks_generated_fallback_call_id_and_missing_turn(
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
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
    assert b02["model_turn_id"].startswith("run-1:turn:")
    assert call["trace_call_id"].startswith("generated:")
    # Trace correlation must not alter the ADK object that is later finalized.
    assert response.content.parts[0].function_call.id is None
    assert call["call_id_source"] == "generated_missing_provider_id"
    assert call["provider_runtime_call_id"] is None
    assert call["call_instance_id"]
    diagnostic_kinds = {
        item["kind"] for item in collector.export()["diagnostics"]
    }
    assert "generated_missing_active_model_turn" in diagnostic_kinds
    assert "generated_missing_provider_call_id" in diagnostic_kinds


def test_before_tool_records_authoritative_parallel_dispatch(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    batch_id = collector.register_tool_batch(
        turn_id,
        ["call_A", "call_B"],
    )
    runtime = GoogleADKRuntime()
    request = make_runtime_request(collector)
    args = {"symbol": "QQQ", "nested": {"limit": 3}}
    function_call_part = types.Part.from_function_call(
        name="market_last_price",
        args={"symbol": "QQQ"},
    )
    function_call_part.function_call.id = "call_A"
    source_event = SimpleNamespace(
        id="function-call-event-1",
        content=types.Content(
            role="model",
            parts=[function_call_part],
        ),
    )

    def market_last_price(symbol):
        return {"symbol": symbol}

    selected_tool = FunctionTool(market_last_price)
    tool_context = SimpleNamespace(
        function_call_id="call_A",
        invocation_id="invocation-1",
        session=SimpleNamespace(events=[source_event]),
    )

    runtime._before_tool_boundary_callback(request)(
        tool=selected_tool,
        args=args,
        tool_context=tool_context,
    )
    args["nested"]["limit"] = 99

    b03 = _events(collector, "B03_ADK_TO_FUNCTION_TOOL")[0]
    assert b03["adk_invocation_id"] == "invocation-1"
    assert b03["model_turn_id"] == turn_id
    assert b03["tool_batch_id"] == batch_id
    assert b03["call_id"] == "call_A"
    assert b03["payload"]["source_function_call_event_id"] == (
        "function-call-event-1"
    )
    assert b03["payload"]["dispatch_observed_at"]
    assert b03["payload"]["dispatch_timestamp_source"] == (
        "before_tool_callback_entry"
    )
    assert b03["payload"]["call_lookup_status"] == "matched_batch"
    assert b03["payload"]["selected_function_tool"] == {
        "name": "market_last_price",
        "python_type": "FunctionTool",
        "qualified_type": (
            "google.adk.tools.function_tool.FunctionTool"
        ),
    }
    assert b03["payload"]["model_arguments"] == {
        "symbol": "QQQ",
        "nested": {"limit": 3},
    }
    assert b03["payload"]["call_sequence"] == 1
    assert b03["payload"]["batch_call_ids"] == ["call_A", "call_B"]
    # Batch membership is candidate evidence; only overlapping windows prove
    # that these calls actually ran concurrently.
    assert b03["payload"]["parallel_scheduling"] == {
        "status": "batch_candidate_only",
        "google_adk_version": "2.1.0",
        "installed_dispatch_semantics": {
            "verification": "verified_google_adk_2_1_source",
            "mechanism": (
                "asyncio.create_task_per_filtered_function_call"
            ),
        },
        "batch_candidate_count": 2,
        "batch_membership_evidence": "candidate_only",
        "sibling_task_creation_observed": False,
        "completion_order_claim": "not_observed_at_dispatch",
    }


def test_missing_dispatch_id_is_observed_without_mutating_adk_context(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    runtime = GoogleADKRuntime()
    request = make_runtime_request(collector)
    tool_context = SimpleNamespace(function_call_id=None)

    runtime._before_tool_boundary_callback(request)(
        tool=SimpleNamespace(name="echo"),
        args={"value": "ready"},
        tool_context=tool_context,
    )

    b03 = _events(collector, "B03_ADK_TO_FUNCTION_TOOL")[0]
    assert b03["call_id"].startswith("generated:adk_dispatch:")
    # ADK owns function_call_id and tracing remains a pure observer.
    assert tool_context.function_call_id is None
    assert b03["payload"]["call_id_source"] == (
        "generated_missing_adk_dispatch_id"
    )
    assert b03["payload"]["call_lookup_status"] == "generated"
    assert collector.call_ids(b03["call_id"])["model_turn_id"] == turn_id
    assert any(
        item["kind"] == "generated_missing_adk_dispatch_call_id"
        for item in collector.export()["diagnostics"]
    )


def test_before_tool_marks_unmatched_provider_call_id(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    runtime = GoogleADKRuntime()
    request = make_runtime_request(collector)

    runtime._before_tool_boundary_callback(request)(
        tool=SimpleNamespace(name="echo"),
        args={"value": "ready"},
        tool_context=SimpleNamespace(
            function_call_id="provider-call-without-b02",
            invocation_id="invocation-1",
        ),
    )

    b03 = _events(collector, "B03_ADK_TO_FUNCTION_TOOL")[0]
    assert b03["payload"]["call_lookup_status"] == "unmatched"
    assert b03["payload"]["parallel_scheduling"]["status"] == (
        "single_call_no_parallel_schedule"
    )


def test_native_b03_reconstructs_ordered_batch_from_source_event(
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    parts = [
        types.Part.from_function_call(
            name="echo",
            args={"value": value},
        )
        for value in ("A", "B")
    ]
    for part, call_id in zip(parts, ("call_A", "call_B")):
        part.function_call.id = call_id
    source_event = SimpleNamespace(
        id="native-function-call-event",
        content=types.Content(role="model", parts=parts),
    )
    runtime = GoogleADKRuntime()
    request = make_runtime_request(
        collector,
        model="gemini-test",
    )

    runtime._before_tool_boundary_callback(request)(
        tool=SimpleNamespace(name="echo"),
        args={"value": "B"},
        tool_context=SimpleNamespace(
            function_call_id="call_B",
            session=SimpleNamespace(events=[source_event]),
        ),
    )

    b03 = _events(collector, "B03_ADK_TO_FUNCTION_TOOL")[0]
    assert b03["payload"]["batch_call_ids"] == ["call_A", "call_B"]
    assert b03["payload"]["call_sequence"] == 2
    assert b03["payload"]["call_lookup_status"] == (
        "reconstructed_source_event_batch"
    )
    assert b03["payload"]["parallel_scheduling"]["status"] == (
        "batch_candidate_only"
    )


def test_after_tool_composes_capture_with_pruning_and_no_prune(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    runtime = GoogleADKRuntime()
    request = make_runtime_request(
        collector,
        model="openai/gpt-5.4-mini",
    )
    callback = runtime._after_tool_boundary_and_pruning_callback(request)
    tool = SimpleNamespace(name="large_tool")
    large_response = {"value": "x" * 5_000}

    pruned = callback(
        tool=tool,
        args={},
        tool_context=SimpleNamespace(function_call_id="call_A"),
        tool_response=large_response,
    )

    assert pruned["lumibot_tool_result_pruned"] is True
    state = collector.call_state("call_A")
    assert state["function_tool_response"] == large_response
    assert state["model_facing_response"] == pruned
    assert state["tool_response_pruned"] is True

    small_response = {"value": "ready"}
    no_prune = callback(
        tool=SimpleNamespace(name="small_tool"),
        args={},
        tool_context=SimpleNamespace(function_call_id="call_B"),
        tool_response=small_response,
    )

    assert no_prune is None
    assert collector.call_state("call_B")[
        "function_tool_response"
    ] == small_response
    assert collector.call_state("call_B")[
        "model_facing_response"
    ] == small_response
    assert collector.call_state("call_B")[
        "tool_response_pruned"
    ] is False


def test_after_tool_state_is_detached_and_capture_failure_keeps_pruning(
    monkeypatch,
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    runtime = GoogleADKRuntime()
    request = make_runtime_request(
        collector,
        model="openai/gpt-5.4-mini",
    )
    callback = runtime._after_tool_boundary_and_pruning_callback(request)
    original = {"value": {"status": "ready"}}

    assert (
        callback(
            tool=SimpleNamespace(name="small_tool"),
            args={},
            tool_context=SimpleNamespace(function_call_id="call_A"),
            tool_response=original,
        )
        is None
    )
    original["value"]["status"] = "changed"
    detached = collector.call_state("call_A")
    detached["function_tool_response"]["value"]["status"] = "local"
    assert collector.call_state("call_A")[
        "function_tool_response"
    ]["value"]["status"] == "ready"

    monkeypatch.setattr(
        collector,
        "note_function_tool_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("capture failed")
        ),
    )
    pruned = callback(
        tool=SimpleNamespace(name="large_tool"),
        args={},
        tool_context=SimpleNamespace(function_call_id="call_B"),
        tool_response={"value": "x" * 5_000},
    )
    assert pruned["lumibot_tool_result_pruned"] is True


def test_normalize_event_records_scalar_mapping_and_merged_responses(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    batch_id = collector.register_tool_batch(
        turn_id,
        ["call_A", "call_B"],
    )
    collector.record(
        transition="B07_WRAPPER_TO_FUNCTION_TOOL",
        from_module="lumibot_tool_wrapper",
        to_module="function_tool",
        call_id="call_A",
        payload={"serialized_result": "ready"},
    )
    collector.note_function_tool_response(
        "call_A",
        unpruned_response="ready",
        model_facing_response="ready",
        pruned=False,
    )
    collector.note_function_tool_response(
        "call_B",
        unpruned_response={"price": 100},
        model_facing_response={"price": 100},
        pruned=False,
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
                ),
                types.Part.from_function_response(
                    name="price_tool",
                    response={"price": 100},
                ),
            ],
        ),
        usage_metadata=None,
    )
    event.content.parts[0].function_response.id = "call_A"
    event.content.parts[1].function_response.id = "call_B"

    normalized = _normalize_event(event, collector=collector)

    assert {
        item.call_id
        for item in normalized
        if item.kind == "tool_result"
    } == {"call_A", "call_B"}
    b08_events = _events(collector, "B08_FUNCTION_TOOL_TO_ADK")
    assert len(b08_events) == 2
    assert b08_events[0]["model_turn_id"] == turn_id
    assert b08_events[0]["tool_batch_id"] == batch_id
    assert b08_events[0]["adk_invocation_id"] == "invocation-1"
    assert b08_events[0]["payload"]["event_id"] == "event-1"
    assert b08_events[0]["payload"]["response_id"] == "call_A"
    assert b08_events[0]["payload"]["response_id_source"] == "provider"
    assert b08_events[0]["payload"]["function_response"] == {
        "result": "ready"
    }
    assert b08_events[0]["payload"]["function_tool_response"] == "ready"
    assert b08_events[0]["payload"]["completion_sequence"] == 1
    assert b08_events[0]["payload"]["batch_completion_sequence"] == 1
    assert b08_events[0]["payload"]["response_created_at"]
    assert b08_events[0]["payload"]["scalar_wrapping"] == {
        "detected": True,
        "source": "function_tool_scalar_to_adk_response_mapping",
        "function_tool_result_type": "str",
        "adk_function_response_type": "dict",
        "adk_result_value_type": "str",
        "wrapped_key": "result",
    }
    assert b08_events[0]["payload"]["authoritative_next_model_data"] is True
    assert b08_events[0]["payload"]["merged_event"] == {
        "function_response_count": 2,
        "function_response_index": 1,
        "call_ids": ["call_A", "call_B"],
    }


def test_b08_marks_unmatched_nonempty_runtime_id_as_unknown(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    request = make_runtime_request(collector)
    GoogleADKRuntime()._before_tool_boundary_callback(request)(
        tool=SimpleNamespace(name="echo"),
        args={"value": "ready"},
        tool_context=SimpleNamespace(
            function_call_id="runtime-unmatched",
            invocation_id="invocation-1",
        ),
    )
    b03 = _events(collector, "B03_ADK_TO_FUNCTION_TOOL")[0]
    assert b03["payload"]["call_id_source"] == (
        "unknown_nonempty_runtime_id"
    )
    assert b03["payload"]["provider_call_id"] is None
    assert b03["payload"]["provider_runtime_call_id"] == (
        "runtime-unmatched"
    )
    event = SimpleNamespace(
        id="event-unmatched",
        invocation_id="invocation-1",
        content=types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="echo",
                    response={"value": "ready"},
                )
            ],
        ),
        usage_metadata=None,
    )
    event.content.parts[0].function_response.id = "runtime-unmatched"

    _normalize_event(event, collector=collector)

    b08 = _events(collector, "B08_FUNCTION_TOOL_TO_ADK")[0]
    assert b08["payload"]["response_id_source"] == (
        "unknown_nonempty_runtime_id"
    )


def test_b08_record_failure_preserves_state_for_retry(
    monkeypatch,
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    registered = collector.register_tool_calls(
        turn_id,
        [
            {
                "trace_call_id": "call_A",
                "provider_call_id": "call_A",
                "provider_runtime_call_id": "call_A",
                "call_id_source": "provider",
                "tool_name": "echo",
                "call_fingerprint": "large-fingerprint",
            }
        ],
    )
    call_instance_id = registered["calls"][0]["call_instance_id"]
    collector.note_function_tool_response(
        call_instance_id,
        unpruned_response={"value": "x" * 5_000},
        model_facing_response={"value": "x" * 5_000},
        pruned=False,
    )
    event = SimpleNamespace(
        id="event-retry",
        invocation_id="invocation-1",
        content=types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="echo",
                    response={"value": "x" * 5_000},
                )
            ],
        ),
        usage_metadata=None,
    )
    event.content.parts[0].function_response.id = "call_A"
    original_record = collector.record
    fail_b08 = True

    def flaky_record(**boundary):
        if (
            fail_b08
            and boundary["transition"] == "B08_FUNCTION_TOOL_TO_ADK"
        ):
            raise RuntimeError("B08 recorder unavailable")
        return original_record(**boundary)

    monkeypatch.setattr(collector, "record", flaky_record)

    _normalize_event(event, collector=collector)

    preserved = collector.call_state(call_instance_id)
    assert preserved["function_tool_response"]["value"] == "x" * 5_000
    assert preserved["call_fingerprint"] == "large-fingerprint"

    fail_b08 = False
    _normalize_event(event, collector=collector)

    assert len(_events(collector, "B08_FUNCTION_TOOL_TO_ADK")) == 1
    compacted = collector.call_state(call_instance_id)
    assert compacted["call_state_compacted"] is True
    assert "function_tool_response" not in compacted
    assert "model_facing_response" not in compacted
    assert "call_fingerprint" not in compacted


def test_merged_duplicate_b08_failure_reserves_instance_until_event_end(
    monkeypatch,
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    registered = collector.register_tool_calls(
        turn_id,
        [
            {
                "trace_call_id": "duplicate-provider-id",
                "provider_call_id": "duplicate-provider-id",
                "provider_runtime_call_id": "duplicate-provider-id",
                "call_id_source": "provider",
                "tool_name": "echo",
                "call_fingerprint": f"fingerprint-{value}",
            }
            for value in ("A", "B")
        ],
    )
    first_instance_id, second_instance_id = [
        call["call_instance_id"] for call in registered["calls"]
    ]
    for call, value in zip(registered["calls"], ("A", "B")):
        collector.note_function_tool_response(
            call["call_instance_id"],
            unpruned_response={"value": value, "large": value * 5_000},
            model_facing_response={
                "value": value,
                "large": value * 5_000,
            },
            pruned=False,
        )

    def response_event(event_id, values):
        event = SimpleNamespace(
            id=event_id,
            invocation_id="invocation-1",
            content=types.Content(
                role="user",
                parts=[
                    types.Part.from_function_response(
                        name="echo",
                        response={"value": value},
                    )
                    for value in values
                ],
            ),
            usage_metadata=None,
        )
        for part in event.content.parts:
            part.function_response.id = "duplicate-provider-id"
        return event

    original_record = collector.record
    failed_once = False

    def fail_first_b08(**boundary):
        nonlocal failed_once
        if (
            not failed_once
            and boundary["transition"] == "B08_FUNCTION_TOOL_TO_ADK"
        ):
            failed_once = True
            raise RuntimeError("first merged B08 failed")
        return original_record(**boundary)

    monkeypatch.setattr(collector, "record", fail_first_b08)

    normalized = _normalize_event(
        response_event("merged-event", ("A", "B")),
        collector=collector,
    )

    assert [
        item.payload["value"]
        for item in normalized
        if item.kind == "tool_result"
    ] == ["A", "B"]
    first_pass_b08 = _events(
        collector,
        "B08_FUNCTION_TOOL_TO_ADK",
    )
    assert len(first_pass_b08) == 1
    assert first_pass_b08[0]["call_instance_id"] == second_instance_id
    assert first_pass_b08[0]["payload"]["function_response"] == {
        "value": "B"
    }
    first_state = collector.call_state(first_instance_id)
    assert first_state["function_tool_response"]["value"] == "A"
    assert len(first_state["function_tool_response"]["large"]) == 5_000
    assert first_state["call_fingerprint"] == "fingerprint-A"
    assert "function_response_claimed" not in first_state
    second_state = collector.call_state(second_instance_id)
    assert second_state["call_state_compacted"] is True
    assert "function_tool_response" not in second_state

    retried = _normalize_event(
        response_event("retry-event", ("A",)),
        collector=collector,
    )

    assert [
        item.payload["value"]
        for item in retried
        if item.kind == "tool_result"
    ] == ["A"]
    b08_events = _events(collector, "B08_FUNCTION_TOOL_TO_ADK")
    assert len(b08_events) == 2
    assert b08_events[1]["call_instance_id"] == first_instance_id
    assert b08_events[1]["payload"]["function_response"] == {
        "value": "A"
    }
    assert collector.call_state(first_instance_id)[
        "call_state_compacted"
    ] is True


def test_b08_compacts_large_multi_call_state_after_sidecar_record(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
        inline_payload_limit=200,
    )
    turn_id = collector.start_model_turn()
    registered = collector.register_tool_calls(
        turn_id,
        [
            {
                "trace_call_id": call_id,
                "provider_call_id": call_id,
                "provider_runtime_call_id": call_id,
                "call_id_source": "provider",
                "tool_name": "echo",
                "call_fingerprint": f"fingerprint-{index}",
            }
            for index, call_id in enumerate(("call_A", "call_B"), start=1)
        ],
    )
    markers = {
        "call_A": "A" * 5_000,
        "call_B": "B" * 5_000,
    }
    for call in registered["calls"]:
        call_id = call["trace_call_id"]
        collector.note_function_tool_response(
            call["call_instance_id"],
            unpruned_response={"large": markers[call_id]},
            model_facing_response={"large": markers[call_id]},
            pruned=False,
        )
    event = SimpleNamespace(
        id="event-large",
        invocation_id="invocation-1",
        content=types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="echo",
                    response={"large": markers[call_id]},
                )
                for call_id in ("call_A", "call_B")
            ],
        ),
        usage_metadata=None,
    )
    for part, call_id in zip(
        event.content.parts,
        ("call_A", "call_B"),
    ):
        part.function_response.id = call_id

    _normalize_event(event, collector=collector)

    b08_events = _events(collector, "B08_FUNCTION_TOOL_TO_ADK")
    assert len(b08_events) == 2
    for boundary in b08_events:
        sidecar_path = boundary["payload_meta"]["sidecar_path"]
        assert sidecar_path
        with gzip.open(
            tmp_path / sidecar_path,
            mode="rt",
            encoding="utf-8",
        ) as sidecar:
            retained_payload = json.load(sidecar)
        call_id = boundary["call_id"]
        assert retained_payload["function_response"]["large"] == (
            markers[call_id]
        )
        assert retained_payload["function_tool_response"]["large"] == (
            markers[call_id]
        )

    for call in registered["calls"]:
        state = collector.call_state(call["call_instance_id"])
        assert state["call_state_compacted"] is True
        assert "function_tool_response" not in state
        assert "model_facing_response" not in state
        assert "call_fingerprint" not in state
    stats = collector.call_state_stats()
    assert stats["heavy_payload_call_count"] == 0
    assert stats["fingerprint_count"] == 0
    assert stats["pending_claim_count"] == 0
    assert stats["alias_instance_count"] == 0


def test_b08_preserves_parallel_completion_order_separate_from_model_order(
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.register_tool_batch(turn_id, ["call_A", "call_B"])
    runtime = GoogleADKRuntime()
    request = make_runtime_request(collector)
    after_tool = runtime._after_tool_boundary_and_pruning_callback(
        request
    )

    after_tool(
        tool=SimpleNamespace(name="tool_b"),
        args={},
        tool_context=SimpleNamespace(function_call_id="call_B"),
        tool_response={"value": "B"},
    )
    after_tool(
        tool=SimpleNamespace(name="tool_a"),
        args={},
        tool_context=SimpleNamespace(function_call_id="call_A"),
        tool_response={"value": "A"},
    )
    event = SimpleNamespace(
        id="merged-event",
        invocation_id="invocation-1",
        content=types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="tool_a",
                    response={"value": "A"},
                ),
                types.Part.from_function_response(
                    name="tool_b",
                    response={"value": "B"},
                ),
            ],
        ),
        usage_metadata=None,
    )
    event.content.parts[0].function_response.id = "call_A"
    event.content.parts[1].function_response.id = "call_B"

    _normalize_event(event, collector=collector)

    b08_by_call = {
        item["call_id"]: item
        for item in _events(collector, "B08_FUNCTION_TOOL_TO_ADK")
    }
    assert b08_by_call["call_B"]["payload"]["completion_sequence"] == 1
    assert b08_by_call["call_B"]["payload"][
        "batch_completion_sequence"
    ] == 1
    assert b08_by_call["call_A"]["payload"]["completion_sequence"] == 2
    assert b08_by_call["call_A"]["payload"][
        "batch_completion_sequence"
    ] == 2
    assert b08_by_call["call_A"]["payload"]["merged_event"][
        "function_response_index"
    ] == 1
    assert b08_by_call["call_B"]["payload"]["merged_event"][
        "function_response_index"
    ] == 2


def test_sequential_batch_reports_candidate_without_confirmed_overlap(
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    collector.register_tool_batch(turn_id, ["call_A", "call_B"])
    runtime = GoogleADKRuntime()
    request = make_runtime_request(collector)
    before_tool = runtime._before_tool_boundary_callback(request)
    after_tool = runtime._after_tool_boundary_and_pruning_callback(
        request
    )

    for call_id, value in (("call_A", "A"), ("call_B", "B")):
        context = SimpleNamespace(function_call_id=call_id)
        before_tool(
            tool=SimpleNamespace(name="echo"),
            args={"value": value},
            tool_context=context,
        )
        after_tool(
            tool=SimpleNamespace(name="echo"),
            args={"value": value},
            tool_context=context,
            tool_response={"value": value},
        )

    event = SimpleNamespace(
        id="sequential-event",
        invocation_id="invocation-1",
        content=types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="echo",
                    response={"value": value},
                )
                for value in ("A", "B")
            ],
        ),
        usage_metadata=None,
    )
    for part, call_id in zip(
        event.content.parts,
        ("call_A", "call_B"),
    ):
        part.function_response.id = call_id
    _normalize_event(event, collector=collector)

    assert {
        item["payload"]["parallel_execution"]["status"]
        for item in _events(collector, "B08_FUNCTION_TOOL_TO_ADK")
    } == {"batch_candidate_only_no_overlap_observed"}


def test_overlapping_async_dispatch_is_confirmed_only_after_completion(
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    collector.register_tool_batch(turn_id, ["call_A", "call_B"])
    runtime = GoogleADKRuntime()
    request = make_runtime_request(collector)
    before_tool = runtime._before_tool_boundary_callback(request)
    after_tool = runtime._after_tool_boundary_and_pruning_callback(
        request
    )

    async def run_overlapping():
        both_started = asyncio.Event()
        started = 0
        lock = asyncio.Lock()

        async def execute(call_id, value):
            nonlocal started
            context = SimpleNamespace(function_call_id=call_id)
            before_tool(
                tool=SimpleNamespace(name="echo"),
                args={"value": value},
                tool_context=context,
            )
            async with lock:
                started += 1
                if started == 2:
                    both_started.set()
            await both_started.wait()
            if value == "A":
                await asyncio.sleep(0.02)
            after_tool(
                tool=SimpleNamespace(name="echo"),
                args={"value": value},
                tool_context=context,
                tool_response={"value": value},
            )

        await asyncio.gather(
            execute("call_A", "A"),
            execute("call_B", "B"),
        )

    asyncio.run(run_overlapping())
    event = SimpleNamespace(
        id="overlap-event",
        invocation_id="invocation-1",
        content=types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="echo",
                    response={"value": value},
                )
                for value in ("A", "B")
            ],
        ),
        usage_metadata=None,
    )
    for part, call_id in zip(
        event.content.parts,
        ("call_A", "call_B"),
    ):
        part.function_response.id = call_id
    _normalize_event(event, collector=collector)

    assert {
        item["payload"]["parallel_execution"]["status"]
        for item in _events(collector, "B08_FUNCTION_TOOL_TO_ADK")
    } == {"confirmed_actual_dispatch_overlap"}
    assert {
        item["payload"]["parallel_execution"]["evidence"]
        for item in _events(collector, "B08_FUNCTION_TOOL_TO_ADK")
    } == {"overlapping_before_tool_to_after_tool_windows"}


def test_duplicate_provider_ids_keep_unique_instances_and_reversed_completion(
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    runtime = GoogleADKRuntime()
    request = make_runtime_request(collector)
    runtime._before_model_boundary_callback(request)(
        llm_request=SimpleNamespace(contents=[], model=request.model),
    )
    response = SimpleNamespace(
        content=types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="echo",
                    args={"value": value},
                )
                for value in ("A", "B")
            ],
        ),
        usage_metadata=None,
        finish_reason="tool_calls",
    )
    for part in response.content.parts:
        part.function_call.id = "duplicate-provider-id"
    runtime._after_model_boundary_callback(request)(
        llm_response=response,
    )

    b02_calls = _events(collector, "B02_LITELLM_TO_ADK")[0][
        "payload"
    ]["tool_calls"]
    assert [item["call_sequence"] for item in b02_calls] == [1, 2]
    assert len(
        {item["call_instance_id"] for item in b02_calls}
    ) == 2

    before_tool = runtime._before_tool_boundary_callback(request)
    after_tool = runtime._after_tool_boundary_and_pruning_callback(
        request
    )

    async def execute(value, delay):
        context = SimpleNamespace(
            function_call_id="duplicate-provider-id"
        )
        before_tool(
            tool=SimpleNamespace(name="echo"),
            args={"value": value},
            tool_context=context,
        )
        await asyncio.sleep(delay)
        after_tool(
            tool=SimpleNamespace(name="echo"),
            args={"value": value},
            tool_context=context,
            tool_response={"value": value},
        )

    async def run_reversed_completion():
        await asyncio.gather(
            execute("A", 0.02),
            execute("B", 0),
        )

    asyncio.run(run_reversed_completion())
    event = SimpleNamespace(
        id="duplicate-event",
        invocation_id="invocation-1",
        content=types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="echo",
                    response={"value": value},
                )
                for value in ("A", "B")
            ],
        ),
        usage_metadata=None,
    )
    for part in event.content.parts:
        part.function_response.id = "duplicate-provider-id"
    _normalize_event(event, collector=collector)

    b03_events = _events(collector, "B03_ADK_TO_FUNCTION_TOOL")
    b08_events = _events(collector, "B08_FUNCTION_TOOL_TO_ADK")
    assert len({item["call_instance_id"] for item in b03_events}) == 2
    assert len({item["call_instance_id"] for item in b08_events}) == 2
    b08_by_value = {
        item["payload"]["function_response"]["value"]: item
        for item in b08_events
    }
    assert b08_by_value["B"]["payload"]["completion_sequence"] == 1
    assert b08_by_value["A"]["payload"]["completion_sequence"] == 2
    assert {
        item["payload"]["provider_id_ambiguity"]["status"]
        for item in b08_events
    } == {"duplicate_provider_runtime_id"}


def test_completion_sequence_is_allocated_before_response_snapshot(
    monkeypatch,
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.register_tool_batch(turn_id, ["call_slow", "call_fast"])
    snapshot_started = Event()
    release_snapshot = Event()
    original_safe_value = collector._safe_observation_value

    def delayed_safe_value(value, **kwargs):
        if value == {"value": "slow"}:
            snapshot_started.set()
            assert release_snapshot.wait(timeout=5)
        return original_safe_value(value, **kwargs)

    monkeypatch.setattr(
        collector,
        "_safe_observation_value",
        delayed_safe_value,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(
            collector.note_function_tool_response,
            "call_slow",
            unpruned_response={"value": "slow"},
            model_facing_response={"value": "slow"},
            pruned=False,
        )
        assert snapshot_started.wait(timeout=5)
        collector.note_function_tool_response(
            "call_fast",
            unpruned_response={"value": "fast"},
            model_facing_response={"value": "fast"},
            pruned=False,
        )
        release_snapshot.set()
        slow.result(timeout=5)

    assert collector.call_state("call_slow")[
        "completion_sequence"
    ] == 1
    assert collector.call_state("call_fast")[
        "completion_sequence"
    ] == 2


def test_reused_provider_call_id_gets_new_turn_completion_state(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    first_turn = collector.start_model_turn()
    collector.register_tool_batch(first_turn, ["call_A"])
    collector.note_function_tool_response(
        "call_A",
        unpruned_response={"turn": 1},
        model_facing_response={"turn": 1},
        pruned=False,
    )
    first_state = collector.call_state("call_A")

    second_turn = collector.start_model_turn()
    collector.register_tool_batch(second_turn, ["call_A"])
    collector.note_function_tool_response(
        "call_A",
        unpruned_response={"turn": 2},
        model_facing_response={"turn": 2},
        pruned=False,
    )
    second_state = collector.call_state("call_A")

    assert first_state["completion_sequence"] == 1
    assert second_state["completion_sequence"] == 2
    assert second_state["batch_completion_sequence"] == 1
    assert second_state["function_tool_response"] == {"turn": 2}


def test_missing_provider_id_reconciles_without_mutating_adk_history(
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    runtime = GoogleADKRuntime()
    request = make_runtime_request(collector)
    runtime._before_model_boundary_callback(request)(
        llm_request=SimpleNamespace(contents=[], model=request.model),
    )
    response = SimpleNamespace(
        content=types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="echo",
                    args={"value": "ready"},
                )
            ],
        ),
        usage_metadata=None,
        finish_reason="tool_calls",
    )
    runtime._after_model_boundary_callback(request)(
        llm_response=response,
    )
    b02_call = _events(collector, "B02_LITELLM_TO_ADK")[0][
        "payload"
    ]["tool_calls"][0]
    trace_call_id = b02_call["trace_call_id"]
    call_instance_id = b02_call["call_instance_id"]
    assert response.content.parts[0].function_call.id is None
    provider_runtime_call_id = "adk-runtime-call-1"
    tool_context = SimpleNamespace(
        function_call_id=provider_runtime_call_id,
        tool_confirmation=None,
    )
    runtime._before_tool_boundary_callback(request)(
        tool=SimpleNamespace(name="echo"),
        args={"value": "ready"},
        tool_context=tool_context,
    )
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(
            name="echo",
            description="echo",
            function=lambda value: value,
        ),
        collector=collector,
        shared_tool_context={},
    )
    result = asyncio.run(
        observed.run_async(
            args={"value": "ready"},
            tool_context=tool_context,
        )
    )
    runtime._after_tool_boundary_and_pruning_callback(request)(
        tool=SimpleNamespace(name="echo"),
        args={"value": "ready"},
        tool_context=tool_context,
        tool_response=result,
    )
    event = SimpleNamespace(
        id="event-1",
        invocation_id="invocation-1",
        content=types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="echo",
                    response={"result": result},
                )
            ],
        ),
        usage_metadata=None,
    )
    event.content.parts[0].function_response.id = (
        provider_runtime_call_id
    )
    _normalize_event(event, collector=collector)

    assert b02_call["provider_runtime_call_id"] is None
    assert b02_call["call_id_source"] == (
        "generated_missing_provider_id"
    )
    for transition in (
        "B03_ADK_TO_FUNCTION_TOOL",
        "B04_FUNCTION_TOOL_TO_WRAPPER",
        "B08_FUNCTION_TOOL_TO_ADK",
    ):
        boundaries = _events(collector, transition)
        assert boundaries, (
            [
                event["transition"]
                for event in collector.export()["events"]
            ],
            collector.export()["diagnostics"],
        )
        boundary = boundaries[0]
        assert boundary["call_id"] == trace_call_id
        assert boundary["call_instance_id"] == call_instance_id
        assert boundary["payload"]["trace_call_id"] == trace_call_id
        assert boundary["payload"]["provider_runtime_call_id"] == (
            provider_runtime_call_id
        )
        assert boundary["payload"]["provider_runtime_alias_relation"] == (
            "adk_runtime_alias_for_trace_call"
        )


def test_live_adk_missing_id_assignment_and_cleanup_are_observation_pure(
    tmp_path,
):
    from google.adk.agents.llm_agent import LlmAgent
    from google.adk.agents.run_config import RunConfig
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import InMemoryRunner

    async def run_probe(collector):
        request_snapshots = []

        class MissingIdModel(BaseLlm):
            async def generate_content_async(
                self,
                llm_request,
                stream=False,
            ):
                request_snapshots.append(
                    agent_runtime._adk_object_payload(llm_request)
                )
                has_function_response = any(
                    getattr(part, "function_response", None)
                    is not None
                    for content in llm_request.contents
                    for part in (content.parts or [])
                )
                if not has_function_response:
                    yield LlmResponse(
                        content=types.Content(
                            role="model",
                            parts=[
                                types.Part.from_function_call(
                                    name="echo",
                                    args={"value": "ready"},
                                )
                            ],
                        ),
                        partial=False,
                        turn_complete=True,
                    )
                    return
                yield LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text="done")],
                    ),
                    partial=False,
                    turn_complete=True,
                )

        runtime = GoogleADKRuntime()
        request = (
            make_runtime_request(collector)
            if collector is not None
            else None
        )
        tool = _build_observed_function_tool(
            FunctionTool,
            BoundTool(
                name="echo",
                description="echo",
                function=lambda value: value,
            ),
            collector=collector,
            shared_tool_context={},
        )
        agent = LlmAgent(
            name="agent",
            model=MissingIdModel(model="missing-id-probe"),
            instruction="Use echo once.",
            tools=[tool],
            after_model_callback=(
                runtime._after_model_boundary_callback(request)
                if request is not None
                else None
            ),
            before_tool_callback=(
                runtime._before_tool_boundary_callback(request)
                if request is not None
                else None
            ),
            after_tool_callback=(
                runtime._after_tool_boundary_and_pruning_callback(
                    request
                )
                if request is not None
                else None
            ),
        )
        runner = InMemoryRunner(
            agent=agent,
            app_name="missing-id-probe",
        )
        await runner.session_service.create_session(
            app_name=runner.app_name,
            user_id="probe-user",
            session_id="probe-session",
        )
        events = [
            event
            async for event in runner.run_async(
                user_id="probe-user",
                session_id="probe-session",
                new_message=types.Content(
                    role="user",
                    parts=[types.Part(text="probe")],
                ),
                run_config=RunConfig(),
            )
        ]
        return request_snapshots, events

    baseline_requests, baseline_events = asyncio.run(run_probe(None))
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    traced_requests, traced_events = asyncio.run(run_probe(collector))
    for event in traced_events:
        _normalize_event(event, collector=collector)

    def event_call_ids(events):
        return [
            function_call.id
            for event in events
            for function_call in event.get_function_calls()
        ]

    def event_response_ids(events):
        return [
            function_response.id
            for event in events
            for function_response in event.get_function_responses()
        ]

    baseline_call_id = event_call_ids(baseline_events)[0]
    traced_call_id = event_call_ids(traced_events)[0]
    assert baseline_call_id.startswith("adk-")
    assert traced_call_id.startswith("adk-")
    assert event_response_ids(baseline_events) == [baseline_call_id]
    assert event_response_ids(traced_events) == [traced_call_id]
    assert len(baseline_requests) == len(traced_requests) == 2
    for snapshots in (baseline_requests, traced_requests):
        second_contents = snapshots[1]["contents"]
        serialized = str(second_contents)
        assert "adk-" not in serialized
        assert "function_call" in serialized
        assert "function_response" in serialized

    b02_call = _events(collector, "B02_LITELLM_TO_ADK")[0][
        "payload"
    ]["tool_calls"][0]
    assert b02_call["trace_call_id"].startswith("generated:")
    assert b02_call["provider_runtime_call_id"] is None
    for transition in (
        "B03_ADK_TO_FUNCTION_TOOL",
        "B04_FUNCTION_TOOL_TO_WRAPPER",
        "B08_FUNCTION_TOOL_TO_ADK",
    ):
        boundaries = _events(collector, transition)
        assert boundaries, (
            [
                event["transition"]
                for event in collector.export()["events"]
            ],
            collector.export()["diagnostics"],
        )
        boundary = boundaries[0]
        assert boundary["call_id"] == b02_call["trace_call_id"]
        assert boundary["payload"]["provider_runtime_call_id"] == (
            traced_call_id
        )
    b03 = _events(collector, "B03_ADK_TO_FUNCTION_TOOL")[0]
    assert b03["payload"]["call_lookup_status"] == (
        "reconciled_generated_fallback"
    )
    b08 = _events(collector, "B08_FUNCTION_TOOL_TO_ADK")[0]
    assert b08["payload"]["response_id_source"] == (
        "adk_generated_missing_provider_id"
    )
    assert b08["payload"]["trace_call_id_source"] == (
        "generated_missing_provider_id"
    )


def test_boundary_callbacks_are_fail_open_and_adk_payloads_are_safe(
    monkeypatch,
    tmp_path,
):
    class ArbitraryHook:
        def model_dump(self, **_kwargs):
            raise AssertionError("arbitrary model_dump hook called")

        def __str__(self):
            raise AssertionError("arbitrary string hook called")

    class BrokenJSONModel(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        value: object

    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    runtime = GoogleADKRuntime()
    request = make_runtime_request(collector)
    runtime._before_model_boundary_callback(request)(
        llm_request=SimpleNamespace(contents=[], model=request.model),
    )
    response = SimpleNamespace(
        content=types.Content(
            role="model",
            parts=[types.Part(text="done")],
        ),
        usage_metadata=None,
        finish_reason="stop",
        provider_metadata={
            "api_key": "test-only-secret",
            "local_path": r"C:\private\trace.json",
            "arbitrary": ArbitraryHook(),
            "broken": BrokenJSONModel(value=ArbitraryHook()),
        },
    )

    assert (
        runtime._after_model_boundary_callback(request)(
            llm_response=response
        )
        is None
    )
    serialized = str(_events(collector, "B02_LITELLM_TO_ADK")[0])
    assert "test-only-secret" not in serialized
    assert r"C:\private\trace.json" not in serialized

    monkeypatch.setattr(
        collector,
        "record",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("recorder failed")
        ),
    )
    assert (
        runtime._before_model_boundary_callback(request)(
            llm_request=SimpleNamespace(contents=[])
        )
        is None
    )
    assert (
        runtime._after_model_boundary_callback(request)(
            llm_response=SimpleNamespace(content=None)
        )
        is None
    )
    assert (
        runtime._before_tool_boundary_callback(request)(
            tool=SimpleNamespace(name="echo"),
            args={},
            tool_context=SimpleNamespace(function_call_id="call_A"),
        )
        is None
    )


def test_collector_turn_batch_and_response_state_are_concurrency_safe(
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )

    def observe(index):
        turn_id = collector.start_model_turn()
        collector.set_active_model_turn(turn_id)
        call_id = f"call_{index:02d}"
        batch_id = collector.register_tool_batch(turn_id, [call_id])
        collector.note_function_tool_response(
            call_id,
            unpruned_response={"index": index},
            model_facing_response={"index": index},
            pruned=False,
        )
        return turn_id, batch_id, call_id

    with ThreadPoolExecutor(max_workers=8) as pool:
        observations = list(pool.map(observe, range(24)))

    assert len({item[0] for item in observations}) == 24
    assert collector.active_model_turn() in {
        item[0] for item in observations
    }
    for turn_id, batch_id, call_id in observations:
        assert collector.batch_call_ids(batch_id) == [call_id]
        state = collector.call_state(call_id)
        assert state["model_turn_id"] == turn_id
        assert state["function_tool_response"]["index"] == int(
            call_id.removeprefix("call_")
        )


def test_collector_public_reads_are_deep_detached(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    turn_id = collector.start_model_turn()
    collector.set_active_model_turn(turn_id)
    collector.register_tool_batch(turn_id, ["call_A"])
    collector.note_function_tool_response(
        "call_A",
        unpruned_response={"nested": {"value": "original"}},
        model_facing_response={"nested": {"value": "original"}},
        pruned=False,
    )
    collector.note_pending_model_turn(
        turn_id,
        previous_model_turn_id=None,
        context_pruning={"nested": {"count": 1}},
    )

    call_ids = collector.call_ids("call_A")
    call_state = collector.call_state("call_A")
    pending = collector.pending_model_turn(turn_id)
    batch = collector.batch_call_ids(call_ids["tool_batch_id"])
    batch_instances = collector.batch_call_instances(
        call_ids["tool_batch_id"]
    )
    call_ids["function_tool_response"]["nested"]["value"] = "changed"
    call_state["model_facing_response"]["nested"]["value"] = "changed"
    pending["context_pruning"]["nested"]["count"] = 99
    batch.append("call_B")
    batch_instances[0]["function_tool_response"]["nested"][
        "value"
    ] = "changed"

    assert collector.call_ids("call_A")[
        "function_tool_response"
    ]["nested"]["value"] == "original"
    assert collector.call_state("call_A")[
        "model_facing_response"
    ]["nested"]["value"] == "original"
    assert collector.pending_model_turn(turn_id)[
        "context_pruning"
    ]["nested"]["count"] == 1
    assert collector.batch_call_ids(call_ids["tool_batch_id"]) == [
        "call_A"
    ]
    assert collector.batch_call_instances(
        call_ids["tool_batch_id"]
    )[0]["function_tool_response"]["nested"]["value"] == "original"
    assert collector.active_model_turn() == turn_id


def test_diagnostic_sink_failure_is_fail_open_for_all_boundary_callbacks(
    monkeypatch,
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    runtime = GoogleADKRuntime()
    request = make_runtime_request(
        collector,
        model="openai/gpt-5.4-mini",
    )
    monkeypatch.setattr(
        collector,
        "add_diagnostic",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("diagnostic sink failed")
        ),
    )

    monkeypatch.setattr(
        collector,
        "start_model_turn",
        lambda: (_ for _ in ()).throw(RuntimeError("turn failed")),
    )
    assert (
        runtime._before_model_boundary_callback(request)(
            llm_request=SimpleNamespace(contents=[])
        )
        is None
    )
    monkeypatch.undo()
    monkeypatch.setattr(
        collector,
        "add_diagnostic",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("diagnostic sink failed")
        ),
    )

    response = SimpleNamespace(
        content=types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="echo",
                    args={"value": "ready"},
                )
            ],
        )
    )
    assert (
        runtime._after_model_boundary_callback(request)(
            llm_response=response
        )
        is None
    )
    tool_context = SimpleNamespace(function_call_id=None)
    assert (
        runtime._before_tool_boundary_callback(request)(
            tool=SimpleNamespace(name="echo"),
            args={"value": "ready"},
            tool_context=tool_context,
        )
        is None
    )
    pruned = runtime._after_tool_boundary_and_pruning_callback(request)(
        tool=SimpleNamespace(name="large_tool"),
        args={},
        tool_context=SimpleNamespace(function_call_id=None),
        tool_response={"value": "x" * 5_000},
    )
    assert pruned["lumibot_tool_result_pruned"] is True

    event = SimpleNamespace(
        id="event-1",
        invocation_id="invocation-1",
        content=types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="echo",
                    response={"result": "ready"},
                )
            ],
        ),
        usage_metadata=None,
    )
    normalized = _normalize_event(event, collector=collector)
    assert any(item.kind == "tool_result" for item in normalized)
    assert event.content.parts[0].function_response.id is None


def test_run_async_builds_every_bound_tool_with_request_collector(
    monkeypatch, tmp_path
):
    built = []
    agent_tools = []
    agent_kwargs = {}
    normalize_collectors = []

    def build(function_tool_type, tool, *, collector, shared_tool_context):
        built.append(
            (
                function_tool_type,
                tool,
                collector,
                shared_tool_context,
            )
        )
        return f"built:{tool.name}"

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
            agent_kwargs.update(kwargs)
            agent_tools.extend(kwargs["tools"])

    class FakeSessionService:
        async def create_session(self, **_kwargs):
            return None

    class FakeRunner:
        def __init__(self, *, agent, app_name):
            self.agent = agent
            self.app_name = app_name
            self.session_service = FakeSessionService()

        async def run_async(self, **_kwargs):
            yield SimpleNamespace(
                id="empty-event",
                invocation_id="invocation-1",
                content=None,
                usage_metadata=None,
            )

    class FakeRunConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
        # Keep this wiring assertion inline despite the larger forensic
        # snapshots; sidecar behavior has a dedicated regression above.
        inline_payload_limit=500_000,
    )
    tools = [
        BoundTool(name="one", description="one", function=lambda: 1),
        BoundTool(name="two", description="two", function=lambda: 2),
    ]
    request = make_runtime_request(
        collector,
        tools=tools,
        # This test asserts LiteLLM model-entry wiring, so use that route.
        model="openai/test",
    )
    runtime = GoogleADKRuntime()
    model_entry_observers = []
    monkeypatch.setattr(agent_runtime, "_build_observed_function_tool", build)
    monkeypatch.setattr(
        agent_runtime,
        "_resolve_model_for_adk",
        lambda model, **kwargs: (
            model_entry_observers.append(
                kwargs.get("model_entry_observer")
            )
            or model
        ),
    )
    monkeypatch.setattr(
        runtime,
        "_ensure_adk",
        lambda: (FakeAgent, FakeRunner, FakeTypes, FunctionTool),
    )
    monkeypatch.setattr(
        agent_runtime.importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(RunConfig=FakeRunConfig)
            if name == "google.adk.agents.run_config"
            else pytest.fail(f"unexpected import: {name}")
        ),
    )
    monkeypatch.setattr(
        runtime,
        "_maybe_build_gemini_thinking_planner",
        lambda *_args: None,
    )
    original_normalize_event = agent_runtime._normalize_event

    def observe_normalize_event(event, *, collector=None):
        normalize_collectors.append(collector)
        return original_normalize_event(event, collector=collector)

    monkeypatch.setattr(
        agent_runtime,
        "_normalize_event",
        observe_normalize_event,
    )

    result = asyncio.run(runtime._run_async(request))

    assert result.events == []
    assert agent_tools == ["built:one", "built:two"]
    assert [entry[1] for entry in built] == tools
    assert all(entry[0] is FunctionTool for entry in built)
    assert all(entry[2] is collector for entry in built)
    assert built[0][3] is built[1][3]
    assert built[0][3]["agent_name"] == "agent"
    assert normalize_collectors == [collector]
    before_model_callbacks = agent_kwargs["before_model_callback"]
    assert isinstance(before_model_callbacks, list)
    assert len(before_model_callbacks) == 2
    llm_request = SimpleNamespace(
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_function_response(
                        name=f"tool_{index}",
                        response={"value": "x" * 10_000},
                    )
                ],
            )
            for index in range(5)
        ],
        model=request.model,
    )
    for callback in before_model_callbacks:
        callback(llm_request=llm_request)
    assert _events(collector, "B09_ADK_TO_LITELLM") == []
    assert len(model_entry_observers) == 1
    model_entry_observers[0](llm_request)
    assert _events(collector, "B09_ADK_TO_LITELLM")[0]["payload"][
        "context_pruning"
    ]["pruned"] is True
    assert callable(agent_kwargs["after_model_callback"])
    assert callable(agent_kwargs["before_tool_callback"])
    assert callable(agent_kwargs["after_tool_callback"])


def test_native_gemini_multi_turn_run_has_no_litellm_boundaries_or_pending(
    monkeypatch,
    tmp_path,
):
    agent_kwargs = {}
    model_entry_observers = []

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
            agent_kwargs.update(kwargs)

    class FakeSessionService:
        async def create_session(self, **_kwargs):
            return None

    class FakeRunner:
        def __init__(self, *, agent, app_name):
            self.agent = agent
            self.app_name = app_name
            self.session_service = FakeSessionService()

        async def run_async(self, **_kwargs):
            for index in range(3):
                llm_request = SimpleNamespace(
                    contents=[],
                    model="gemini-test",
                )
                callbacks = (
                    agent_kwargs.get("before_model_callback") or []
                )
                if not isinstance(callbacks, list):
                    callbacks = [callbacks]
                for callback in callbacks:
                    callback(llm_request=llm_request)
                after_model = agent_kwargs.get(
                    "after_model_callback"
                )
                if after_model is not None:
                    after_model(
                        llm_response=SimpleNamespace(
                            content=types.Content(
                                role="model",
                                parts=[
                                    types.Part(text=f"turn {index}")
                                ],
                            )
                        )
                    )
                yield SimpleNamespace(
                    id=f"event-{index}",
                    invocation_id="invocation-1",
                    content=None,
                    usage_metadata=None,
                )

    class FakeRunConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
    )
    request = make_runtime_request(collector, model="gemini-test")
    runtime = GoogleADKRuntime()
    monkeypatch.setattr(
        runtime,
        "_ensure_adk",
        lambda: (FakeAgent, FakeRunner, FakeTypes, FunctionTool),
    )
    monkeypatch.setattr(
        agent_runtime,
        "_resolve_model_for_adk",
        lambda model, **kwargs: (
            model_entry_observers.append(
                kwargs.get("model_entry_observer")
            )
            or model
        ),
    )
    monkeypatch.setattr(
        agent_runtime.importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(RunConfig=FakeRunConfig)
            if name == "google.adk.agents.run_config"
            else pytest.fail(f"unexpected import: {name}")
        ),
    )
    monkeypatch.setattr(
        runtime,
        "_maybe_build_gemini_thinking_planner",
        lambda *_args: None,
    )

    result = asyncio.run(runtime._run_async(request))

    assert len(result.events) == 0
    assert model_entry_observers == [None]
    assert agent_kwargs["after_model_callback"] is None
    assert _events(collector, "B09_ADK_TO_LITELLM") == []
    assert _events(collector, "B02_LITELLM_TO_ADK") == []
    assert collector.pending_model_turn_count() == 0
    assert collector.active_model_turn().endswith(":turn:0003")


def test_wrapper_records_raw_and_serialized_results_separately(tmp_path):
    invocations = []

    def price(symbol: str, asset_type: str = "stock"):
        invocations.append(((symbol,), {}))
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
        result = wrapped("QQQ")

    assert result["as_of"] == "2024-09-05T00:00:00+00:00"
    assert invocations == [(("QQQ",), {})]

    b05 = _events(collector, "B05_WRAPPER_TO_PYTHON_TOOL")[0]
    b06 = _events(collector, "B06_PYTHON_TOOL_TO_WRAPPER")[0]
    b07 = _events(collector, "B07_WRAPPER_TO_FUNCTION_TOOL")[0]

    for event in (b05, b06, b07):
        assert event["call_id"] == "call_A"
        assert event["model_turn_id"] == "run-1:turn:0001"
        assert event["tool_batch_id"] == "run-1:turn:0001:batch:0001"
        assert event["started_at"]
        assert event["ended_at"]

    assert b05["from_module"] == "lumibot_tool_wrapper"
    assert b05["to_module"] == "python_tool"
    assert b05["payload"]["tool_name"] == "market_last_price"
    assert b05["payload"]["source"] == "local"
    assert b05["payload"]["callable_module"] == __name__
    assert b05["payload"]["callable_qualname"].endswith(".price")
    assert b05["payload"]["positional_arguments"] == ["QQQ"]
    assert b05["payload"]["keyword_arguments"] == {}
    assert b05["payload"]["effective_arguments"] == {
        "symbol": "QQQ",
        "asset_type": "stock",
    }

    assert b06["from_module"] == "python_tool"
    assert b06["to_module"] == "lumibot_tool_wrapper"
    assert b06["status"] == "success"
    assert b06["duration_ms"] >= 0
    assert b06["payload"]["raw_result"]["python_type"] == "dict"
    assert b06["payload"]["raw_result"]["fidelity"] == "semantic_copy"
    assert b06["payload"]["raw_result"]["semantic_value"]["symbol"] == "QQQ"
    assert b07["from_module"] == "lumibot_tool_wrapper"
    assert b07["to_module"] == "function_tool"
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

    assert result == {
        "ok": False,
        "tool_error": True,
        "tool_name": "broken",
        "error": {"type": "ValueError", "message": "bad symbol BAD"},
        "arguments": {"symbol": "BAD"},
    }
    failure = _events(collector, "B06_PYTHON_TOOL_TO_WRAPPER")[0]
    assert failure["status"] == "error"
    assert failure["call_id"] == "call_A"
    assert failure["duration_ms"] >= 0
    assert failure["error"] == {
        "type": "ValueError",
        "message": "bad symbol BAD",
    }
    b07 = _events(collector, "B07_WRAPPER_TO_FUNCTION_TOOL")[0]
    assert b07["call_id"] == "call_A"
    assert b07["payload"]["serialized_result"] == result


def test_b06_success_timing_excludes_trace_overhead(monkeypatch, tmp_path):
    tool_times = {}

    def fast_tool():
        tool_times["started_at"] = datetime.now(timezone.utc)
        result = {"price": 100.0}
        tool_times["ended_at"] = datetime.now(timezone.utc)
        return result

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    original_record = collector.record
    original_describe = collector.describe_raw_value

    def delayed_record(**event):
        time.sleep(0.08)
        return original_record(**event)

    def delayed_describe(value):
        time.sleep(0.08)
        return original_describe(value)

    monkeypatch.setattr(collector, "record", delayed_record)
    monkeypatch.setattr(collector, "describe_raw_value", delayed_describe)
    wrapped = _wrap_tool_callable(
        BoundTool(name="fast", description="fast", function=fast_tool),
        collector=collector,
    )

    elapsed_started = time.perf_counter()
    assert wrapped() == {"price": 100.0}
    elapsed_ms = (time.perf_counter() - elapsed_started) * 1000

    b06 = _events(collector, "B06_PYTHON_TOOL_TO_WRAPPER")[0]
    assert elapsed_ms >= 280
    assert b06["duration_ms"] < 50
    assert abs(
        (_event_timestamp(b06["started_at"]) - tool_times["started_at"]).total_seconds()
    ) < 0.05
    assert abs(
        (_event_timestamp(b06["ended_at"]) - tool_times["ended_at"]).total_seconds()
    ) < 0.05


def test_b06_error_timing_excludes_exception_formatting(tmp_path):
    tool_times = {}

    class SlowMessageError(Exception):
        def __str__(self):
            time.sleep(0.1)
            return "slow message"

    def broken_tool():
        tool_times["started_at"] = datetime.now(timezone.utc)
        time.sleep(0.025)
        tool_times["ended_at"] = datetime.now(timezone.utc)
        raise SlowMessageError()

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    wrapped = _wrap_tool_callable(
        BoundTool(name="broken", description="broken", function=broken_tool),
        collector=collector,
    )

    result = wrapped()

    assert result["error"] == {"type": "SlowMessageError", "message": "slow message"}
    b06 = _events(collector, "B06_PYTHON_TOOL_TO_WRAPPER")[0]
    assert 15 <= b06["duration_ms"] < 70
    assert abs(
        (_event_timestamp(b06["started_at"]) - tool_times["started_at"]).total_seconds()
    ) < 0.05
    assert abs(
        (_event_timestamp(b06["ended_at"]) - tool_times["ended_at"]).total_seconds()
    ) < 0.05


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


def test_raised_recorder_does_not_replace_original_tool_error(monkeypatch, tmp_path):
    def broken():
        raise LookupError("tool failed")

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    wrapped = _wrap_tool_callable(
        BoundTool(name="broken", description="broken", function=broken),
        collector=collector,
    )
    monkeypatch.setattr(
        collector,
        "record",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("recorder failed")),
    )

    result = wrapped()

    assert result["tool_error"] is True
    assert result["error"] == {"type": "LookupError", "message": "tool failed"}


def test_wrapper_reads_callable_metadata_without_instance_hooks(tmp_path):
    class CallableTool:
        def __init__(self):
            self.metadata_accesses = []

        def __getattribute__(self, name):
            # Signature inspection is callable behavior; trace metadata must not
            # probe these instance attributes.
            if name in {"__module__", "__qualname__"}:
                object.__getattribute__(self, "metadata_accesses").append(name)
            return object.__getattribute__(self, name)

        def __call__(self, symbol: str):
            return {"symbol": symbol}

    original = CallableTool()
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    wrapped = _wrap_tool_callable(
        BoundTool(name="callable", description="callable", function=original),
        collector=collector,
    )

    assert wrapped(symbol="QQQ") == {"symbol": "QQQ"}
    assert original.metadata_accesses == []
    metadata = _events(collector, "B05_WRAPPER_TO_PYTHON_TOOL")[0]["payload"]
    assert metadata["callable_module"] == __name__
    assert metadata["callable_qualname"].endswith(".CallableTool")


def test_wrapper_preserves_partial_signature_without_collector():
    def quote(symbol: str, asset_type: str = "stock", currency: str = "USD"):
        return {
            "symbol": symbol,
            "asset_type": asset_type,
            "currency": currency,
        }

    original = partial(quote, asset_type="option")
    wrapped = _wrap_tool_callable(
        BoundTool(name="quote", description="quote", function=original)
    )

    assert inspect.signature(wrapped) == inspect.signature(original)
    assert wrapped("QQQ") == {
        "symbol": "QQQ",
        "asset_type": "option",
        "currency": "USD",
    }


def test_wrapper_uses_partial_signature_defaults_for_b05(tmp_path):
    def quote(symbol: str, asset_type: str = "stock", currency: str = "USD"):
        return {"symbol": symbol, "asset_type": asset_type, "currency": currency}

    original = partial(quote, asset_type="option")
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    wrapped = _wrap_tool_callable(
        BoundTool(name="quote", description="quote", function=original),
        collector=collector,
    )

    assert wrapped("QQQ")["asset_type"] == "option"
    b05 = _events(collector, "B05_WRAPPER_TO_PYTHON_TOOL")[0]
    assert b05["payload"]["effective_arguments"] == {
        "symbol": "QQQ",
        "asset_type": "option",
        "currency": "USD",
    }


def test_wrapper_preserves_explicit_instance_signature_without_collector():
    class ExplicitSignatureTool:
        def __init__(self):
            self.__signature__ = inspect.Signature(
                [
                    inspect.Parameter(
                        "symbol",
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        annotation=str,
                    ),
                    inspect.Parameter(
                        "venue",
                        inspect.Parameter.KEYWORD_ONLY,
                        default="lit",
                        annotation=str,
                    ),
                ]
            )

        def __call__(self, symbol, *, venue="lit"):
            return {"symbol": symbol, "venue": venue}

    original = ExplicitSignatureTool()
    wrapped = _wrap_tool_callable(
        BoundTool(name="route", description="route", function=original)
    )

    assert inspect.signature(wrapped) == inspect.signature(original)
    assert wrapped("QQQ") == {"symbol": "QQQ", "venue": "lit"}


def test_wrapper_uses_explicit_instance_signature_defaults_for_b05(tmp_path):
    class ExplicitSignatureTool:
        def __init__(self):
            self.__signature__ = inspect.Signature(
                [
                    inspect.Parameter(
                        "symbol",
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    ),
                    inspect.Parameter(
                        "venue",
                        inspect.Parameter.KEYWORD_ONLY,
                        default="lit",
                    ),
                ]
            )

        def __call__(self, symbol, *, venue="lit"):
            return {"symbol": symbol, "venue": venue}

    original = ExplicitSignatureTool()
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    wrapped = _wrap_tool_callable(
        BoundTool(name="route", description="route", function=original),
        collector=collector,
    )

    assert wrapped("QQQ")["venue"] == "lit"
    b05 = _events(collector, "B05_WRAPPER_TO_PYTHON_TOOL")[0]
    assert b05["payload"]["effective_arguments"] == {
        "symbol": "QQQ",
        "venue": "lit",
    }


def test_wrapper_falls_back_when_explicit_signature_hook_raises(tmp_path):
    class ThrowingSignatureTool:
        def __init__(self):
            self.signature_reads = 0

        def __getattribute__(self, name):
            if name == "__signature__":
                reads = object.__getattribute__(self, "signature_reads")
                object.__setattr__(self, "signature_reads", reads + 1)
                raise RuntimeError("signature unavailable")
            return object.__getattribute__(self, name)

        def __call__(self, symbol="QQQ", *, venue="lit"):
            return {"symbol": symbol, "venue": venue}

    original = ThrowingSignatureTool()
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    wrapped = _wrap_tool_callable(
        BoundTool(name="throwing", description="throwing", function=original),
        collector=collector,
    )

    assert wrapped() == {"symbol": "QQQ", "venue": "lit"}
    assert original.signature_reads == 1
    signature = inspect.signature(wrapped)
    assert list(signature.parameters) == ["symbol", "venue"]
    assert signature.parameters["symbol"].default == "QQQ"
    assert signature.parameters["venue"].default == "lit"
    b05 = _events(collector, "B05_WRAPPER_TO_PYTHON_TOOL")[0]
    assert b05["payload"]["effective_arguments"] == {
        "symbol": "QQQ",
        "venue": "lit",
    }


def test_wrapper_remains_usable_without_collector():
    wrapped = _wrap_tool_callable(
        BoundTool(
            name="echo",
            description="echo",
            function=lambda value="default": value,
        )
    )

    assert wrapped() == "default"
    with pytest.raises(TypeError):
        _wrap_tool_callable(
            BoundTool(name="echo", description="echo", function=lambda: None),
            {},
            None,
        )


def test_sync_parallel_batch_order_and_timing_match_with_and_without_collector(
    tmp_path,
):
    class ParallelToolContext(FakeToolContext):
        def __init__(self, call_id):
            self.function_call_id = call_id

    def exercise(collector):
        completions = []
        windows = {}

        def sync_tool(value: str):
            started = time.perf_counter()
            time.sleep(0.03 if value == "A" else 0.005)
            windows[value] = (started, time.perf_counter())
            completions.append(value)
            return {"value": value}

        if collector is not None:
            turn_id = collector.start_model_turn()
            collector.set_active_model_turn(turn_id)
            collector.register_tool_batch(
                turn_id,
                ["call_A", "call_B"],
            )
        observed = _build_observed_function_tool(
            FunctionTool,
            BoundTool(
                name="sync_tool",
                description="sync",
                function=sync_tool,
            ),
            collector=collector,
            shared_tool_context={},
        )

        async def run_pair():
            return await asyncio.gather(
                observed.run_async(
                    args={"value": "A"},
                    tool_context=ParallelToolContext("call_A"),
                ),
                observed.run_async(
                    args={"value": "B"},
                    tool_context=ParallelToolContext("call_B"),
                ),
            )

        return asyncio.run(run_pair()), completions, windows

    untraced = exercise(None)
    traced = exercise(
        BoundaryTraceCollector(
            agent_run_id="run-sync-parity",
            artifact_root=tmp_path,
        )
    )

    assert traced[:2] == untraced[:2] == (
        [{"value": "A"}, {"value": "B"}],
        ["A", "B"],
    )
    for _, _, windows in (untraced, traced):
        assert windows["B"][0] >= windows["A"][1]


@pytest.mark.parametrize("with_collector", [False, True])
def test_async_tool_is_awaited_by_adk_with_matching_trace_semantics(
    tmp_path,
    with_collector,
    recwarn,
):
    contexts = []

    async def async_tool(symbol: str, venue: str = "lit"):
        contexts.append(current_agent_tool_context())
        await asyncio.sleep(0)
        contexts.append(current_agent_tool_context())
        return {"symbol": symbol, "venue": venue}

    collector = (
        BoundaryTraceCollector(
            agent_run_id="run-async",
            artifact_root=tmp_path,
        )
        if with_collector
        else None
    )
    if collector is not None:
        turn_id = collector.start_model_turn()
        collector.set_active_model_turn(turn_id)
        collector.register_tool_batch(turn_id, ["call_A"])
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(
            name="async_tool",
            description="async",
            function=async_tool,
        ),
        collector=collector,
        shared_tool_context={"marker": "visible"},
    )

    assert inspect.iscoroutinefunction(observed.func)
    result = asyncio.run(
        observed.run_async(
            args={"symbol": "QQQ"},
            tool_context=FakeToolContext(),
        )
    )
    if inspect.isawaitable(result):
        result.close()
    gc.collect()

    assert not inspect.isawaitable(result)
    assert result == {"symbol": "QQQ", "venue": "lit"}
    assert contexts == [
        {"marker": "visible"},
        {"marker": "visible"},
    ]
    assert not [
        warning
        for warning in recwarn
        if "was never awaited" in str(warning.message)
    ]
    if collector is not None:
        assert [
            event["transition"]
            for event in collector.export()["events"]
            if event["call_id"] == "call_A"
            and event["transition"]
            in {
                "B04_FUNCTION_TOOL_TO_WRAPPER",
                "B05_WRAPPER_TO_PYTHON_TOOL",
                "B06_PYTHON_TOOL_TO_WRAPPER",
                "B07_WRAPPER_TO_FUNCTION_TOOL",
            }
        ] == [
            "B04_FUNCTION_TOOL_TO_WRAPPER",
            "B05_WRAPPER_TO_PYTHON_TOOL",
            "B06_PYTHON_TOOL_TO_WRAPPER",
            "B07_WRAPPER_TO_FUNCTION_TOOL",
        ]


@pytest.mark.parametrize("with_collector", [False, True])
def test_async_callable_object_preserves_signature_defaults_and_metadata(
    tmp_path,
    with_collector,
):
    class AsyncPriceTool:
        async def __call__(
            self,
            symbol: str,
            *,
            venue: str = "lit",
        ):
            await asyncio.sleep(0)
            return {"symbol": symbol, "venue": venue}

    collector = (
        BoundaryTraceCollector(
            agent_run_id="run-async-callable",
            artifact_root=tmp_path,
        )
        if with_collector
        else None
    )
    if collector is not None:
        turn_id = collector.start_model_turn()
        collector.set_active_model_turn(turn_id)
        collector.register_tool_batch(turn_id, ["call_A"])
    observed = _build_observed_function_tool(
        FunctionTool,
        BoundTool(
            name="async_price",
            description="async price",
            function=AsyncPriceTool(),
        ),
        collector=collector,
        shared_tool_context={},
    )
    wrapped = observed.func

    assert inspect.iscoroutinefunction(wrapped)
    signature = inspect.signature(wrapped)
    assert list(signature.parameters) == ["symbol", "venue"]
    assert signature.parameters["venue"].default == "lit"
    assert signature.parameters["symbol"].annotation is str
    result = asyncio.run(
        observed.run_async(
            args={"symbol": "QQQ"},
            tool_context=FakeToolContext(),
        )
    )

    assert result == {"symbol": "QQQ", "venue": "lit"}
    if collector is not None:
        b05 = _events(
            collector,
            "B05_WRAPPER_TO_PYTHON_TOOL",
        )[0]
        assert b05["payload"]["callable_module"] == __name__
        assert b05["payload"]["callable_qualname"].endswith(
            "AsyncPriceTool"
        )
        assert b05["payload"]["effective_arguments"] == {
            "symbol": "QQQ",
            "venue": "lit",
        }


def test_async_tool_error_records_await_timing_and_restores_context(
    tmp_path,
):
    seen_contexts = []

    async def broken(symbol: str):
        seen_contexts.append(current_agent_tool_context())
        await asyncio.sleep(0.01)
        seen_contexts.append(current_agent_tool_context())
        raise ValueError(f"bad symbol {symbol}")

    collector = BoundaryTraceCollector(
        agent_run_id="run-async-error",
        artifact_root=tmp_path,
    )
    wrapped = _wrap_tool_callable(
        BoundTool(
            name="broken_async",
            description="broken",
            function=broken,
        ),
        {"marker": "async-context"},
        collector=collector,
    )

    assert inspect.iscoroutinefunction(wrapped)
    with collector.tool_call_context(call_id="call_A"):
        result = asyncio.run(wrapped(symbol="BAD"))

    assert result["tool_error"] is True
    assert result["error"] == {
        "type": "ValueError",
        "message": "bad symbol BAD",
    }
    assert seen_contexts == [
        {"marker": "async-context"},
        {"marker": "async-context"},
    ]
    assert current_agent_tool_context() == {}
    b06 = _events(collector, "B06_PYTHON_TOOL_TO_WRAPPER")[0]
    b07 = _events(collector, "B07_WRAPPER_TO_FUNCTION_TOOL")[0]
    assert b06["status"] == "error"
    assert b06["duration_ms"] > 0
    assert _event_timestamp(b06["started_at"]) < _event_timestamp(
        b06["ended_at"]
    )
    assert _event_timestamp(b06["ended_at"]) <= _event_timestamp(
        b07["ended_at"]
    )
    assert b07["payload"]["serialized_result"] == result


@pytest.mark.parametrize("callable_kind", ["function", "object"])
@pytest.mark.parametrize("raises", [False, True])
def test_stub_runtime_awaits_async_tools_and_exports_trace(
    tmp_path,
    recwarn,
    callable_kind,
    raises,
):
    async def execute():
        await asyncio.sleep(0)
        if raises:
            raise ValueError("async stub failed")
        return {"status": "async complete"}

    if callable_kind == "function":

        async def tool():
            return await execute()

    else:

        class AsyncTool:
            async def __call__(self):
                return await execute()

        tool = AsyncTool()

    collector = BoundaryTraceCollector(
        agent_run_id=f"run-stub-{callable_kind}-{raises}",
        artifact_root=tmp_path,
    )
    request = make_runtime_request(
        collector,
        tools=[
            BoundTool(
                name="async_stub",
                description="async stub",
                function=tool,
            )
        ],
    )

    result = StubAgentRuntime().run(request)
    gc.collect()

    tool_result = next(
        event.payload
        for event in result.events
        if event.kind == "tool_result"
    )
    if raises:
        assert tool_result["tool_error"] is True
        assert tool_result["error"] == {
            "type": "ValueError",
            "message": "async stub failed",
        }
    else:
        assert tool_result == {"status": "async complete"}
    assert result.boundary_trace == collector.export()
    assert [
        event["transition"]
        for event in result.boundary_trace["events"]
    ] == [
        "B05_WRAPPER_TO_PYTHON_TOOL",
        "B06_PYTHON_TOOL_TO_WRAPPER",
        "B07_WRAPPER_TO_FUNCTION_TOOL",
    ]
    assert _trace_events(
        result.boundary_trace,
        "B06_PYTHON_TOOL_TO_WRAPPER",
    )[0]["status"] == ("error" if raises else "success")
    assert not [
        warning
        for warning in recwarn
        if "was never awaited" in str(warning.message)
    ]


def test_stub_runtime_sync_tool_does_not_enter_asyncio(
    monkeypatch,
    tmp_path,
):
    calls = []

    def sync_tool():
        calls.append("sync")
        return {"status": "sync complete"}

    monkeypatch.setattr(
        agent_runtime.asyncio,
        "run",
        lambda *_args, **_kwargs: pytest.fail(
            "sync stub tool entered asyncio.run"
        ),
    )
    collector = BoundaryTraceCollector(
        agent_run_id="run-stub-sync",
        artifact_root=tmp_path,
    )
    result = StubAgentRuntime().run(
        make_runtime_request(
            collector,
            tools=[
                BoundTool(
                    name="sync_stub",
                    description="sync stub",
                    function=sync_tool,
                )
            ],
        )
    )

    assert calls == ["sync"]
    assert next(
        event.payload
        for event in result.events
        if event.kind == "tool_result"
    ) == {"status": "sync complete"}
    assert result.boundary_trace == collector.export()


def test_stub_runtime_closes_async_wrapper_when_sync_runner_fails(
    tmp_path,
    recwarn,
):
    async def async_tool():
        await asyncio.sleep(0)
        return {"status": "unreachable"}

    collector = BoundaryTraceCollector(
        agent_run_id="run-stub-runner-failure",
        artifact_root=tmp_path,
    )
    request = make_runtime_request(
        collector,
        tools=[
            BoundTool(
                name="async_stub",
                description="async stub",
                function=async_tool,
            )
        ],
    )

    async def invoke_from_running_loop():
        with pytest.raises(
            RuntimeError,
            match=r"asyncio\.run\(\) cannot be called",
        ):
            StubAgentRuntime().run(request)

    asyncio.run(invoke_from_running_loop())
    gc.collect()

    assert not [
        warning
        for warning in recwarn
        if "was never awaited" in str(warning.message)
    ]


def test_stub_runtime_scripted_events_remain_authoritative_with_trace(
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-stub-scripted",
        artifact_root=tmp_path,
    )
    runtime = StubAgentRuntime(
        scripted_events=[
            {
                "kind": "text",
                "text": "scripted summary",
                "timestamp": "2026-07-24T00:00:00Z",
            }
        ]
    )

    result = runtime.run(make_runtime_request(collector))

    assert result.summary == "scripted summary"
    assert len(result.events) == 1
    assert result.events[0].text == "scripted summary"
    assert result.events[0].timestamp == "2026-07-24T00:00:00Z"
    assert result.boundary_trace == collector.export()
    assert result.boundary_trace["events"] == []


def test_full_boundary_loop_preserves_turns_batches_and_reversed_parallel_completion(
    monkeypatch,
    tmp_path,
):
    expected_transitions = {
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
    global_callbacks_before = _litellm_global_callback_state()
    created_loggers = []
    logger_type = agent_runtime.LiteLLMBoundaryLogger

    def tracked_logger(*args, **kwargs):
        logger = logger_type(*args, **kwargs)
        created_loggers.append(logger)
        return logger

    monkeypatch.setattr(
        agent_runtime,
        "LiteLLMBoundaryLogger",
        tracked_logger,
    )
    provider_requests = install_scripted_acompletion(monkeypatch)
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
        inline_payload_limit=500_000,
    )
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

    result = runtime.run(request)
    trace = result.boundary_trace

    assert result.summary == "RESULT: complete"
    assert trace is not None
    assert {
        event["transition"] for event in trace["events"]
    } >= expected_transitions
    assert model_turn_ids(trace) == [
        "run-1:turn:0001",
        "run-1:turn:0002",
        "run-1:turn:0003",
    ]
    for transition in (
        "B01_PROVIDER_TO_LITELLM",
        "B02_LITELLM_TO_ADK",
        "B09_ADK_TO_LITELLM",
        "B10_LITELLM_TO_PROVIDER",
    ):
        assert len(_trace_events(trace, transition)) == 3
    assert len(provider_requests) == 3

    assert calls_in_model_order(trace, batch=1) == [
        "call_A",
        "call_B",
    ]
    # B08 may retain ADK's list order. The authoritative forensic order is
    # the completion sequence allocated when each local call finishes.
    assert calls_in_completion_order(trace, batch=1) == [
        "call_B",
        "call_A",
    ]
    assert calls_in_model_order(trace, batch=2) == ["call_C"]
    b02_events = _trace_events(trace, "B02_LITELLM_TO_ADK")
    assert [
        call["provider_call_id"]
        for call in b02_events[0]["payload"]["tool_calls"]
    ] == ["call_A", "call_B"]
    assert [
        call["provider_call_id"]
        for call in b02_events[1]["payload"]["tool_calls"]
    ] == ["call_C"]

    expected_symbols = {
        "call_A": "QQQ",
        "call_B": "SPY",
        "call_C": "IWM",
    }
    call_instances = {}
    for call_id, symbol in expected_symbols.items():
        call_events = [
            event
            for event in trace["events"]
            if event.get("call_id") == call_id
            and event["transition"]
            in {
                "B03_ADK_TO_FUNCTION_TOOL",
                "B04_FUNCTION_TOOL_TO_WRAPPER",
                "B05_WRAPPER_TO_PYTHON_TOOL",
                "B06_PYTHON_TOOL_TO_WRAPPER",
                "B07_WRAPPER_TO_FUNCTION_TOOL",
                "B08_FUNCTION_TOOL_TO_ADK",
            }
        ]
        assert [
            event["transition"] for event in call_events
        ] == [
            "B03_ADK_TO_FUNCTION_TOOL",
            "B04_FUNCTION_TOOL_TO_WRAPPER",
            "B05_WRAPPER_TO_PYTHON_TOOL",
            "B06_PYTHON_TOOL_TO_WRAPPER",
            "B07_WRAPPER_TO_FUNCTION_TOOL",
            "B08_FUNCTION_TOOL_TO_ADK",
        ]
        instance_ids = {
            event["call_instance_id"] for event in call_events
        }
        assert None not in instance_ids
        assert len(instance_ids) == 1
        call_instances[call_id] = instance_ids.pop()

        b03, b04, b05, _b06, b07, b08 = call_events
        assert b03["payload"]["provider_call_id"] == call_id
        assert b03["payload"]["tool_name"] == "market_last_price"
        assert b04["payload"]["model_arguments"] == {
            "symbol": symbol
        }
        assert b04["payload"]["wrapper_received_arguments"] == {
            "symbol": symbol
        }
        assert b04["payload"][
            "effective_python_arguments_with_defaults"
        ] == {"symbol": symbol}
        assert b05["payload"]["effective_arguments"] == {
            "symbol": symbol
        }
        assert b07["payload"]["serialized_result"]["symbol"] == symbol
        assert b08["payload"]["function_response"]["symbol"] == symbol
        assert response_for(trace, call_id)["symbol"] == symbol
    assert len(set(call_instances.values())) == 3

    first_batch_b08 = [
        event
        for event in _trace_events(
            trace,
            "B08_FUNCTION_TOOL_TO_ADK",
        )
        if event["call_id"] in {"call_A", "call_B"}
    ]
    second_request_event = next_provider_request(
        trace,
        after_call="call_A",
    )
    assert second_request_event["sequence"] > max(
        event["sequence"] for event in first_batch_b08
    )
    second_request = second_request_event["payload"]["request"]
    second_request_json = json.dumps(second_request, sort_keys=True)
    second_tool_responses = {
        message["tool_call_id"]: json.loads(message["content"])
        for message in second_request["messages"]
        if message.get("role") == "tool"
    }
    for call_id, symbol in (
        ("call_A", "QQQ"),
        ("call_B", "SPY"),
    ):
        assert call_id in second_request_json
        assert symbol in second_request_json
        assert second_tool_responses[call_id] == response_for(
            trace,
            call_id,
        )

    call_c_b08 = next(
        event
        for event in _trace_events(
            trace,
            "B08_FUNCTION_TOOL_TO_ADK",
        )
        if event["call_id"] == "call_C"
    )
    final_request_event = next_provider_request(
        trace,
        after_call="call_C",
    )
    assert final_request_event["sequence"] > call_c_b08["sequence"]
    final_request_json = json.dumps(
        final_request_event["payload"]["request"],
        sort_keys=True,
    )
    assert "call_C" in final_request_json
    assert "IWM" in final_request_json
    final_tool_responses = {
        message["tool_call_id"]: json.loads(message["content"])
        for message in final_request_event["payload"]["request"][
            "messages"
        ]
        if message.get("role") == "tool"
    }
    assert final_tool_responses["call_C"] == response_for(
        trace,
        "call_C",
    )

    b09_events = _trace_events(trace, "B09_ADK_TO_LITELLM")
    b10_events = _trace_events(trace, "B10_LITELLM_TO_PROVIDER")
    for provider_request, b09, b10 in zip(
        provider_requests,
        b09_events,
        b10_events,
    ):
        assert b09["model_turn_id"] == b10["model_turn_id"]
        assert b09["payload"]["capture_point"] == (
            "observed_litellm_generate_content_async_entry"
        )
        assert "contents" in b09["payload"]["llm_request"]
        assert b10["payload"]["capture_type"] == (
            "provider_adapter_request"
        )
        assert b10["payload"]["boundary_distinction"] == (
            "litellm_provider_adapter_request_not_adk_model_entry"
        )
        assert "messages" in b10["payload"]["request"]
        assert b10["payload"]["request"]["messages"] == (
            provider_request["messages"]
        )
        assert (
            b09["payload"]["llm_request"]
            != b10["payload"]["request"]
        )

    provider_responses = [
        event["payload"]["response"]
        for event in _trace_events(
            trace,
            "B01_PROVIDER_TO_LITELLM",
        )
    ]
    assert [
        response["id"] for response in provider_responses
    ] == [
        "response-turn-1",
        "response-turn-2",
        "response-turn-3",
    ]
    assert [
        response["choices"][0]["finish_reason"]
        for response in provider_responses
    ] == ["tool_calls", "tool_calls", "stop"]
    assert [
        response["usage"] for response in provider_responses
    ] == [
        {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }
    ] * 3

    forbidden_provider_keys = {
        "api_base",
        "api_key",
        "authorization",
        "base_url",
        "token",
    }
    for provider_request in provider_requests:
        assert provider_request["callback_counts"][
            "success_callback"
        ] == 1
        assert provider_request["callback_counts"][
            "failure_callback"
        ] == 1
        assert not (
            forbidden_provider_keys
            & {
                key.lower()
                for key in provider_request["kwargs"]
            }
        )
    assert _litellm_global_callback_state() == (
        global_callbacks_before
    )
    assert len(created_loggers) == 1
    assert created_loggers[0].pending_attempt_count() == 0
    assert collector.pending_model_turn_count() == 0
    assert trace == collector.export()

    events_at_return = copy.deepcopy(trace["events"])
    time.sleep(0.05)
    assert collector.export()["events"] == events_at_return
    assert result.boundary_trace["events"] == events_at_return
