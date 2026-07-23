import asyncio
import inspect
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from types import SimpleNamespace

import pytest
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from pydantic import BaseModel, ConfigDict

from lumibot.components.agents import runtime as agent_runtime
from lumibot.components.agents.boundary_trace import BoundaryTraceCollector
from lumibot.components.agents.runtime import (
    GoogleADKRuntime,
    RuntimeRequest,
    _build_observed_function_tool,
    _normalize_event,
    _wrap_tool_callable,
)
from lumibot.components.agents.schemas import BoundTool


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

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
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

    prune = runtime._before_model_context_pruning_callback(request)
    capture = runtime._before_model_boundary_callback(request)
    assert prune is not None
    prune(callback_context=None, llm_request=llm_request)
    capture(callback_context=None, llm_request=llm_request)

    b09 = _events(collector, "B09_ADK_TO_LITELLM")[0]
    assert b09["model_turn_id"] == collector.active_model_turn()
    assert b09["payload"]["context_pruning"] == {
        "pruned": True,
        "pruned_tool_results": 1,
    }
    assert (
        "Older tool result omitted by Lumibot before this model call because "
        "the provider context window would otherwise be exceeded. Use the most "
        "recent visible tool results or call a targeted tool again if this older "
        "detail is still required."
    ) in str(b09["payload"]["llm_request"])


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
    assert call["call_id"].startswith("generated:")
    assert response.content.parts[0].function_call.id == call["call_id"]
    assert call["call_id_source"] == "generated_missing_provider_id"
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

    runtime._before_tool_boundary_callback(request)(
        tool=SimpleNamespace(name="market_last_price"),
        args=args,
        tool_context=SimpleNamespace(function_call_id="call_A"),
    )
    args["nested"]["limit"] = 99

    b03 = _events(collector, "B03_ADK_TO_FUNCTION_TOOL")[0]
    assert b03["model_turn_id"] == turn_id
    assert b03["tool_batch_id"] == batch_id
    assert b03["call_id"] == "call_A"
    assert b03["payload"] == {
        "tool_name": "market_last_price",
        "call_id_source": "provider",
        "model_arguments": {
            "symbol": "QQQ",
            "nested": {"limit": 3},
        },
        "call_sequence": 1,
        "parallel_batch": True,
    }


def test_missing_dispatch_id_is_mutated_for_downstream_consistency(tmp_path):
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
    assert tool_context.function_call_id == b03["call_id"]
    assert b03["payload"]["call_id_source"] == (
        "generated_missing_adk_dispatch_id"
    )
    assert collector.call_ids(b03["call_id"])["model_turn_id"] == turn_id
    assert any(
        item["kind"] == "generated_missing_adk_dispatch_call_id"
        for item in collector.export()["diagnostics"]
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
    assert b08_events[0]["payload"]["function_response"] == {
        "result": "ready"
    }
    assert b08_events[0]["payload"]["function_tool_response"] == "ready"
    assert b08_events[0]["payload"]["authoritative_next_model_data"] is True
    assert b08_events[0]["payload"]["merged_event"] == {
        "function_response_count": 2,
        "function_response_index": 1,
        "call_ids": ["call_A", "call_B"],
    }


def test_generated_call_id_is_consistent_across_b02_b03_b04_and_b08(
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
    call_id = response.content.parts[0].function_call.id
    tool_context = SimpleNamespace(
        function_call_id=call_id,
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
    event.content.parts[0].function_response.id = call_id
    _normalize_event(event, collector=collector)

    for transition in (
        "B02_LITELLM_TO_ADK",
        "B03_ADK_TO_FUNCTION_TOOL",
        "B04_FUNCTION_TOOL_TO_WRAPPER",
        "B08_FUNCTION_TOOL_TO_ADK",
    ):
        assert _events(collector, transition)[0]["call_id"] in {
            None,
            call_id,
        }
    assert _events(collector, "B02_LITELLM_TO_ADK")[0]["payload"][
        "tool_calls"
    ][0]["call_id"] == call_id
    assert {
        _events(collector, transition)[0]["call_id"]
        for transition in (
            "B03_ADK_TO_FUNCTION_TOOL",
            "B04_FUNCTION_TOOL_TO_WRAPPER",
            "B08_FUNCTION_TOOL_TO_ADK",
        )
    } == {call_id}


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

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    tools = [
        BoundTool(name="one", description="one", function=lambda: 1),
        BoundTool(name="two", description="two", function=lambda: 2),
    ]
    request = make_runtime_request(collector, tools=tools, model="gemini-test")
    runtime = GoogleADKRuntime()
    monkeypatch.setattr(agent_runtime, "_build_observed_function_tool", build)
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
    assert _events(collector, "B09_ADK_TO_LITELLM")[0]["payload"][
        "context_pruning"
    ]["pruned"] is True
    assert callable(agent_kwargs["after_model_callback"])
    assert callable(agent_kwargs["before_tool_callback"])
    assert callable(agent_kwargs["after_tool_callback"])


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
