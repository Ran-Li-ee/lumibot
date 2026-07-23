import inspect
import time
from datetime import datetime, timezone
from functools import partial

import pytest

from lumibot.components.agents.boundary_trace import BoundaryTraceCollector
from lumibot.components.agents.runtime import RuntimeRequest, _wrap_tool_callable
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
