import asyncio
import inspect
import time
from datetime import datetime, timezone
from functools import partial
from types import SimpleNamespace

import pytest
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext
from pydantic import BaseModel

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
    assert b04["payload"]["removed_arguments"] == ["unknown"]
    b05 = _events(collector, "B05_WRAPPER_TO_PYTHON_TOOL")[0]
    assert b05["payload"]["effective_arguments"]["asset_type"] == "stock"


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


def test_collector_call_state_is_detached_from_mutable_arguments(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    arguments = {"request": {"symbol": "QQQ"}}

    collector.note_wrapper_arguments("call_A", arguments)
    arguments["request"]["symbol"] = "SPY"
    first_state = collector.call_state("call_A")
    first_state["wrapper_received_arguments"]["request"]["symbol"] = "IWM"

    assert collector.call_state("call_A") == {
        "wrapper_invoked": True,
        "wrapper_received_arguments": {"request": {"symbol": "QQQ"}},
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


def test_run_async_builds_every_bound_tool_with_request_collector(
    monkeypatch, tmp_path
):
    built = []
    agent_tools = []

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
            if False:
                yield None

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

    result = asyncio.run(runtime._run_async(request))

    assert result.events == []
    assert agent_tools == ["built:one", "built:two"]
    assert [entry[1] for entry in built] == tools
    assert all(entry[0] is FunctionTool for entry in built)
    assert all(entry[2] is collector for entry in built)
    assert built[0][3] is built[1][3]
    assert built[0][3]["agent_name"] == "agent"


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
