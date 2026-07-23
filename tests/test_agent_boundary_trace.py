import gzip
import hashlib
import json
import os
from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel

from lumibot.components.agents.boundary_trace import (
    BOUNDARY_SCHEMA_VERSION,
    BoundaryTraceCollector,
)
from lumibot.components.agents.trace_redaction import redact_sensitive


class CredentialBearingValue:
    def __repr__(self):
        return "api_key=custom-object-secret"


class ExampleTraceModel(BaseModel):
    symbol: str
    score: Decimal


def test_redact_sensitive_masks_credentials_and_preserves_safe_values():
    bearer_token = "boundary-bearer-secret"
    api_key = "boundary-openai-secret"
    sk_token = "sk-abcdefghijklmnopqrstuvwx"
    payload = {
        "header_text": f"Authorization: Bearer {bearer_token}",
        "nested": {
            "OPENAI_API_KEY": api_key,
            "symbol": "QQQ",
        },
        "free_text": f"credential={sk_token}",
    }

    redacted = redact_sensitive(payload)
    rendered = repr(redacted)

    assert bearer_token not in rendered
    assert api_key not in rendered
    assert sk_token not in rendered
    assert redacted["header_text"] == "Authorization: [REDACTED]"
    assert redacted["nested"]["OPENAI_API_KEY"] == "[REDACTED]"
    assert redacted["free_text"] == "credential=[REDACTED]"
    assert redacted["nested"]["symbol"] == "QQQ"


def test_boundary_collector_allocates_stable_turn_batch_and_event_ids(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
        inline_payload_limit=10_000,
    )

    turn_id = collector.start_model_turn()
    batch_id = collector.register_tool_batch(turn_id, ["call_A", "call_B"])
    first = collector.record(
        transition="B03_ADK_TO_FUNCTION_TOOL",
        from_module="google_adk",
        to_module="function_tool",
        model_turn_id=turn_id,
        tool_batch_id=batch_id,
        call_id="call_A",
        payload={"symbol": "QQQ"},
    )
    second = collector.record(
        transition="B03_ADK_TO_FUNCTION_TOOL",
        from_module="google_adk",
        to_module="function_tool",
        model_turn_id=turn_id,
        tool_batch_id=batch_id,
        call_id="call_B",
        payload={"symbol": "SPY"},
    )

    exported = collector.export()

    assert BOUNDARY_SCHEMA_VERSION == 1
    assert turn_id == "run-1:turn:0001"
    assert batch_id == "run-1:turn:0001:batch:0001"
    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert first["span_id"] != second["span_id"]
    assert first["payload_meta"]["compression"] is None
    assert exported["agent_run_id"] == "run-1"
    assert exported["events"] == [first, second]


def test_tool_call_context_restores_previous_value(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    assert collector.current_tool_call() is None
    with collector.tool_call_context(call_id="call_A", tool_name="market_last_price"):
        assert collector.current_tool_call()["call_id"] == "call_A"
    assert collector.current_tool_call() is None


def test_record_return_is_detached_from_collector_state(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    event = collector.record(
        transition="B03_ADK_TO_FUNCTION_TOOL",
        from_module="google_adk",
        to_module="function_tool",
        payload={"nested": {"symbol": "QQQ"}},
    )

    event["payload"]["nested"]["symbol"] = "SPY"
    event["payload"]["nested"]["authorization"] = "injected-record-secret"
    event["payload_meta"]["sha256"] = "tampered"

    persisted = collector.export()["events"][0]
    assert persisted["payload"]["nested"] == {"symbol": "QQQ"}
    assert persisted["payload_meta"]["sha256"] != "tampered"
    assert "injected-record-secret" not in str(persisted)


def test_export_returns_deeply_detached_events_and_diagnostics(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    collector.record(
        transition="B03_ADK_TO_FUNCTION_TOOL",
        from_module="google_adk",
        to_module="function_tool",
        payload={"rows": [{"symbol": "QQQ"}]},
    )
    collector.add_diagnostic("test_diagnostic", "safe message")

    exported = collector.export()
    exported["events"][0]["payload"]["rows"][0]["symbol"] = "SPY"
    exported["diagnostics"][0]["message"] = "injected-export-secret"
    exported["events"].append({"payload": "injected-event"})
    exported["diagnostics"].append({"message": "injected-diagnostic"})

    persisted = collector.export()
    assert persisted["events"][0]["payload"]["rows"] == [{"symbol": "QQQ"}]
    assert persisted["diagnostics"][0]["message"] == "safe message"
    assert len(persisted["events"]) == 1
    assert len(persisted["diagnostics"]) == 1
    assert "injected" not in str(persisted)


def test_record_includes_adk_invocation_id(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    event = collector.record(
        transition="B03_ADK_TO_FUNCTION_TOOL",
        from_module="google_adk",
        to_module="function_tool",
        adk_invocation_id="adk-invocation-1",
        payload={"symbol": "QQQ"},
    )

    assert event["adk_invocation_id"] == "adk-invocation-1"
    assert collector.export()["events"][0]["adk_invocation_id"] == "adk-invocation-1"


def test_redaction_failure_is_fail_open_even_for_diagnostics(monkeypatch, tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    def fail_redaction(_value):
        raise ValueError("redaction unavailable")

    monkeypatch.setattr(
        "lumibot.components.agents.boundary_trace.redact_sensitive",
        fail_redaction,
    )

    event = collector.record(
        transition="B03_ADK_TO_FUNCTION_TOOL",
        from_module="google_adk",
        to_module="function_tool",
        payload={"authorization": "must-not-persist"},
    )

    exported = collector.export()
    assert event == {}
    assert exported["events"] == []
    assert exported["diagnostics"] == [
        {
            "kind": "record_failed",
            "message": "[diagnostic message unavailable: redaction failed]",
            "timestamp": exported["diagnostics"][0]["timestamp"],
        }
    ]
    assert "must-not-persist" not in str(exported)


def test_snapshot_normalizes_arbitrary_payloads_before_redaction(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    event = collector.record(
        transition="B03_ADK_TO_FUNCTION_TOOL",
        from_module="google_adk",
        to_module="function_tool",
        payload={
            "symbols": {"SPY", "QQQ"},
            7: CredentialBearingValue(),
            "authorization": "Bearer mixed-mapping-secret",
        },
    )

    assert event["payload"] == {
        "symbols": ["QQQ", "SPY"],
        "7": {
            "fidelity": "descriptor_only",
            "python_type": "CredentialBearingValue",
            "qualified_type": (
                f"{CredentialBearingValue.__module__}."
                f"{CredentialBearingValue.__qualname__}"
            ),
        },
        "authorization": "[REDACTED]",
    }
    assert event["payload_meta"]["fidelity"] == "descriptor_only"
    assert json.loads(json.dumps(event["payload"], sort_keys=True)) == event["payload"]
    assert "custom-object-secret" not in str(collector.export())
    assert "mixed-mapping-secret" not in str(collector.export())


def test_large_payload_is_redacted_then_written_to_relative_sidecar(tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
        inline_payload_limit=32,
    )

    event = collector.record(
        transition="B07_WRAPPER_TO_FUNCTION_TOOL",
        from_module="lumibot_tool_wrapper",
        to_module="function_tool",
        payload={
            "OPENAI_API_KEY": "test-only-secret-value",
            "rows": ["x" * 100],
        },
    )

    relative = event["payload_meta"]["sidecar_path"]
    assert relative and not Path(relative).is_absolute()
    relative_path = Path(relative)
    assert relative_path.parts[0] == "boundary_payloads"
    assert relative_path.name == f"{event['span_id']}.json.gz"
    with gzip.open(tmp_path / relative, "rb") as handle:
        persisted_bytes = handle.read()
    persisted = json.loads(persisted_bytes)
    assert "test-only-secret-value" not in str(persisted)
    assert persisted["rows"] == ["x" * 100]
    assert event["payload_meta"]["truncated"] is False
    assert event["payload_meta"]["compression"] == "gzip"
    canonical_bytes = json.dumps(
        persisted,
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    assert persisted_bytes == canonical_bytes
    assert event["payload_meta"]["byte_count"] == len(canonical_bytes)
    assert event["payload_meta"]["sha256"] == hashlib.sha256(
        canonical_bytes
    ).hexdigest()


def test_raw_generator_uses_descriptor_without_consuming_it(tmp_path):
    consumed = []

    def values():
        consumed.append("started")
        yield 1

    generator = values()
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    descriptor = collector.describe_raw_value(generator)

    assert consumed == []
    assert descriptor["fidelity"] == "descriptor_only"
    assert descriptor["python_type"] == "generator"
    assert descriptor["qualified_type"] == "builtins.generator"
    assert descriptor["one_shot"] is True


def test_raw_iterator_descriptor_never_calls_consuming_repr(tmp_path):
    class ConsumingReprIterator(Iterator):
        def __init__(self):
            self.position = 0

        def __next__(self):
            self.position += 1
            return self.position

        def __repr__(self):
            return f"next={next(self)}"

    iterator = ConsumingReprIterator()
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    descriptor = collector.describe_raw_value(iterator)

    assert iterator.position == 0
    assert descriptor == {
        "python_type": "ConsumingReprIterator",
        "qualified_type": (
            f"{ConsumingReprIterator.__module__}."
            f"{ConsumingReprIterator.__qualname__}"
        ),
        "fidelity": "descriptor_only",
        "one_shot": True,
    }


def test_raw_semantic_values_preserve_supported_types(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    values = {
        "scalar": 7,
        "mapping": {"symbol": "QQQ"},
        "sequence": ["QQQ", "SPY"],
        "datetime": datetime(2024, 9, 5, tzinfo=timezone.utc),
        "decimal": Decimal("1.25"),
        "model": ExampleTraceModel(symbol="QQQ", score=Decimal("9.5")),
    }

    descriptor = collector.describe_raw_value(values)

    assert descriptor["python_type"] == "dict"
    assert descriptor["qualified_type"] == "builtins.dict"
    assert descriptor["fidelity"] == "semantic_copy"
    assert descriptor["semantic_value"] == {
        "scalar": 7,
        "mapping": {"symbol": "QQQ"},
        "sequence": ["QQQ", "SPY"],
        "datetime": "2024-09-05T00:00:00+00:00",
        "decimal": "1.25",
        "model": {"symbol": "QQQ", "score": "9.5"},
    }


def test_descriptor_only_value_reports_safe_bounded_metadata(tmp_path):
    class ShapedOpaqueValue:
        shape = (2, 3)

        def __repr__(self):
            return "api_key=descriptor-only-secret " + ("x" * 600)

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    descriptor = collector.describe_raw_value(ShapedOpaqueValue())

    assert descriptor["python_type"] == "ShapedOpaqueValue"
    assert descriptor["qualified_type"].endswith(".ShapedOpaqueValue")
    assert descriptor["fidelity"] == "descriptor_only"
    assert descriptor["shape"] == [2, 3]
    assert len(descriptor["preview"]) <= 500
    assert "descriptor-only-secret" not in descriptor["preview"]


def test_descriptor_preview_redacts_complete_repr_before_truncating(tmp_path):
    secret = "quoted-secret-" + ("s" * 600)

    class LongQuotedSecret:
        def __repr__(self):
            return f"{{'api_key': '{secret}'}}"

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    descriptor = collector.describe_raw_value(LongQuotedSecret())

    assert descriptor["preview"] == "{'api_key': '[REDACTED]'}"
    assert secret[:100] not in descriptor["preview"]
    assert len(descriptor["preview"]) <= 500


def test_descriptor_only_container_reports_keys_and_length(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    descriptor = collector.describe_raw_value({"opaque": object()})

    assert descriptor["fidelity"] == "descriptor_only"
    assert descriptor["keys"] == ["opaque"]
    assert descriptor["length"] == 1
    assert "preview" not in descriptor


def test_record_uses_nested_descriptor_without_opaque_repr(tmp_path):
    repr_calls = []

    class OpaqueValue:
        def __repr__(self):
            repr_calls.append(True)
            return "user-repr-secret at 0xDEADBEEF"

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    event = collector.record(
        transition="B03_ADK_TO_FUNCTION_TOOL",
        from_module="google_adk",
        to_module="function_tool",
        payload={
            "opaque": OpaqueValue(),
            "supported": {"symbols": ["QQQ", "SPY"]},
        },
    )

    assert repr_calls == []
    assert event["payload"] == {
        "opaque": {
            "fidelity": "descriptor_only",
            "python_type": "OpaqueValue",
            "qualified_type": (
                f"{OpaqueValue.__module__}.{OpaqueValue.__qualname__}"
            ),
        },
        "supported": {"symbols": ["QQQ", "SPY"]},
    }
    assert event["payload_meta"]["fidelity"] == "descriptor_only"
    assert "0xDEADBEEF" not in str(event)
    assert "user-repr-secret" not in str(event)


def test_record_uses_safe_nested_descriptor_for_plain_object(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    event = collector.record(
        transition="B03_ADK_TO_FUNCTION_TOOL",
        from_module="google_adk",
        to_module="function_tool",
        payload={"opaque": object(), "supported": [1, 2]},
    )

    assert event["payload"] == {
        "opaque": {
            "fidelity": "descriptor_only",
            "python_type": "object",
            "qualified_type": "builtins.object",
        },
        "supported": [1, 2],
    }
    assert event["payload_meta"]["fidelity"] == "descriptor_only"
    assert "0x" not in str(event["payload"])


def test_sidecar_failure_adds_diagnostic_without_raising(monkeypatch, tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
        inline_payload_limit=1,
    )
    monkeypatch.setattr(
        collector,
        "_write_sidecar",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    event = collector.record(
        transition="B07_WRAPPER_TO_FUNCTION_TOOL",
        from_module="lumibot_tool_wrapper",
        to_module="function_tool",
        payload={"value": "large"},
    )

    assert event["payload"] == {"preview": '{"value": "large"}'}
    assert event["payload_meta"]["sidecar_path"] is None
    assert event["payload_meta"]["truncated"] is True
    assert event["payload_meta"]["compression"] is None
    assert collector.export()["diagnostics"][0]["kind"] == "sidecar_write_failed"


def test_redaction_failure_records_no_payload_and_does_not_raise(monkeypatch, tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    def fail_redaction(_value):
        raise ValueError("redaction unavailable")

    monkeypatch.setattr(
        "lumibot.components.agents.boundary_trace.redact_sensitive",
        fail_redaction,
    )

    event = collector.record(
        transition="B07_WRAPPER_TO_FUNCTION_TOOL",
        from_module="lumibot_tool_wrapper",
        to_module="function_tool",
        payload={"authorization": "must-not-persist"},
    )

    exported = collector.export()
    assert event == {}
    assert exported["events"] == []
    assert len(exported["diagnostics"]) == 1
    assert exported["diagnostics"][0]["kind"] == "record_failed"
    assert "must-not-persist" not in str(exported)


def test_later_redaction_failure_leaves_no_orphan_sidecar(monkeypatch, tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
        inline_payload_limit=1,
    )
    calls = 0

    def fail_after_first_redaction(value):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise ValueError("redaction unavailable")
        return value

    monkeypatch.setattr(
        "lumibot.components.agents.boundary_trace.redact_sensitive",
        fail_after_first_redaction,
    )

    event = collector.record(
        transition="B07_WRAPPER_TO_FUNCTION_TOOL",
        from_module="lumibot_tool_wrapper",
        to_module="function_tool",
        payload="x" * 100,
        error={"message": "later redaction"},
    )

    assert event == {}
    assert collector.export()["events"] == []
    assert collector.export()["diagnostics"][0]["kind"] == "record_failed"
    assert list(tmp_path.rglob("*.json.gz")) == []


@pytest.mark.parametrize(
    "agent_run_id",
    [
        "../escape",
        r"..\escape",
        r"C:\outside\run",
    ],
)
def test_sidecar_path_sanitizes_agent_run_id_and_stays_in_root(
    monkeypatch,
    tmp_path,
    agent_run_id,
):
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    boundary_root = (artifact_root / "boundary_payloads").resolve()
    real_mkdir = Path.mkdir

    def guarded_mkdir(path, *args, **kwargs):
        resolved = path.resolve()
        try:
            resolved.relative_to(boundary_root)
        except ValueError as exc:
            raise AssertionError(f"attempted outside write: {resolved}") from exc
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", guarded_mkdir)
    collector = BoundaryTraceCollector(
        agent_run_id=agent_run_id,
        artifact_root=artifact_root,
        inline_payload_limit=1,
    )

    event = collector.record(
        transition="B07_WRAPPER_TO_FUNCTION_TOOL",
        from_module="lumibot_tool_wrapper",
        to_module="function_tool",
        payload={"value": "large"},
    )

    relative = Path(event["payload_meta"]["sidecar_path"])
    target = (artifact_root / relative).resolve()
    assert not relative.is_absolute()
    assert len(relative.parts) == 3
    assert relative.parts[0] == "boundary_payloads"
    assert ".." not in relative.parts
    assert "\\" not in relative.parts[1]
    assert ":" not in relative.parts[1]
    assert target.is_relative_to(boundary_root)
    assert target.is_file()


def test_write_sidecar_uses_atomic_replace(monkeypatch, tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    replaced = {}
    real_replace = os.replace

    def capture_replace(source, target):
        source_path = Path(source)
        target_path = Path(target)
        assert source_path.parent == target_path.parent
        assert source_path != target_path
        with gzip.open(source_path, "rt", encoding="utf-8") as handle:
            assert json.load(handle) == {"value": "persisted"}
        replaced["source"] = source_path
        replaced["target"] = target_path
        real_replace(source, target)

    monkeypatch.setattr(
        "lumibot.components.agents.boundary_trace.os.replace",
        capture_replace,
    )

    relative = collector._write_sidecar(
        span_id="span-1",
        payload={"value": "persisted"},
    )

    relative_path = Path(relative)
    assert relative_path.parts[0] == "boundary_payloads"
    assert relative_path.name == "span-1.json.gz"
    assert replaced["target"] == tmp_path / relative
    assert not replaced["source"].exists()
    assert replaced["target"].is_file()


def test_write_sidecar_cleans_temporary_file_on_failure(monkeypatch, tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    def fail_replace(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr(
        "lumibot.components.agents.boundary_trace.os.replace",
        fail_replace,
    )

    with pytest.raises(OSError, match="replace failed"):
        collector._write_sidecar(span_id="span-1", payload={"value": "persisted"})

    assert list(tmp_path.rglob("*.tmp")) == []
    assert list(tmp_path.rglob("*.json.gz")) == []


def test_write_sidecar_requires_artifact_root():
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=None)

    with pytest.raises(OSError, match="artifact root is unavailable"):
        collector._write_sidecar(span_id="span-1", payload={"value": "persisted"})


def test_record_allocates_span_before_snapshot(monkeypatch, tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)
    snapshot_span_ids = []

    def capture_snapshot(payload, *, span_id):
        snapshot_span_ids.append(span_id)
        return payload, {"truncated": False}

    monkeypatch.setattr(collector, "snapshot", capture_snapshot)

    event = collector.record(
        transition="B07_WRAPPER_TO_FUNCTION_TOOL",
        from_module="lumibot_tool_wrapper",
        to_module="function_tool",
        payload={"value": "safe"},
    )

    assert snapshot_span_ids == [event["span_id"]]
