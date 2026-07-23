import gzip
import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import ClassVar

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
            "preview": (
                f"<{CredentialBearingValue.__module__}."
                f"{CredentialBearingValue.__qualname__} instance>"
            ),
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


def test_descriptor_only_value_does_not_invoke_mutating_repr(tmp_path):
    class MutatingReprValue:
        def __init__(self):
            self.repr_calls = 0

        def __repr__(self):
            self.repr_calls += 1
            return "api_key=descriptor-only-secret"

    value = MutatingReprValue()
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    descriptor = collector.describe_raw_value(value)

    assert value.repr_calls == 0
    assert descriptor["python_type"] == "MutatingReprValue"
    assert descriptor["qualified_type"].endswith(".MutatingReprValue")
    assert descriptor["fidelity"] == "descriptor_only"
    assert "MutatingReprValue instance" in descriptor["preview"]
    assert "0x" not in descriptor["preview"]
    assert "descriptor-only-secret" not in descriptor["preview"]


def test_descriptor_only_value_does_not_access_throwing_shape(tmp_path):
    class ThrowingShapeValue:
        def __init__(self):
            self.shape_accesses = 0

        @property
        def shape(self):
            self.shape_accesses += 1
            raise RuntimeError("shape unavailable")

    value = ThrowingShapeValue()
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    descriptor = collector.describe_raw_value(value)

    assert value.shape_accesses == 0
    assert descriptor["fidelity"] == "descriptor_only"
    assert "ThrowingShapeValue instance" in descriptor["preview"]


def test_descriptor_only_value_does_not_access_unknown_model_dump(tmp_path):
    class ThrowingModelDumpValue:
        def __init__(self):
            self.model_dump_accesses = 0

        @property
        def model_dump(self):
            self.model_dump_accesses += 1
            raise RuntimeError("model dump unavailable")

    value = ThrowingModelDumpValue()
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    descriptor = collector.describe_raw_value(value)

    assert value.model_dump_accesses == 0
    assert descriptor["fidelity"] == "descriptor_only"
    assert "ThrowingModelDumpValue instance" in descriptor["preview"]


def test_descriptor_captures_static_safe_shape_without_invoking_descriptor(tmp_path):
    descriptor_calls = []

    class ThrowingShapeDescriptor:
        def __get__(self, instance, owner):
            descriptor_calls.append((instance, owner))
            raise RuntimeError("shape descriptor must not run")

    class StaticShapeValue:
        shape = (2, 3, None)

    class DescriptorShapeValue:
        shape = ThrowingShapeDescriptor()

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    static_descriptor = collector.describe_raw_value(StaticShapeValue())
    throwing_descriptor = collector.describe_raw_value(DescriptorShapeValue())

    assert static_descriptor["shape"] == [2, 3, None]
    assert "shape" not in throwing_descriptor
    assert descriptor_calls == []


def test_supported_model_uses_guarded_base_serializer(tmp_path):
    class OverriddenModelDump(BaseModel):
        dump_calls: ClassVar[int] = 0
        value: int

        def model_dump(self, *args, **kwargs):
            type(self).dump_calls += 1
            raise RuntimeError("overridden model dump unavailable")

    value = OverriddenModelDump(value=7)
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    descriptor = collector.describe_raw_value(value)

    assert OverriddenModelDump.dump_calls == 0
    assert descriptor["fidelity"] == "semantic_copy"
    assert descriptor["semantic_value"] == {"value": 7}


def test_descriptor_type_identity_bypasses_adversarial_metaclass(tmp_path):
    intercepted_attributes = []

    class AdversarialMeta(type):
        def __getattribute__(cls, name):
            if name in {"__name__", "__module__", "__qualname__"}:
                intercepted_attributes.append(name)
            return super().__getattribute__(name)

    class AdversarialValue(metaclass=AdversarialMeta):
        pass

    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    descriptor = collector.describe_raw_value(AdversarialValue())

    assert intercepted_attributes == []
    assert descriptor["python_type"] == "AdversarialValue"
    assert descriptor["qualified_type"].endswith(".AdversarialValue")
    assert "AdversarialValue instance" in descriptor["preview"]
    assert "0x" not in descriptor["preview"]


def test_descriptor_preview_redacts_complete_safe_preview_before_truncating(tmp_path):
    secret = b"quoted-secret-" + (b"s" * 600)
    value = b"{'api_key': '" + secret + b"'}"
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    descriptor = collector.describe_raw_value(value)

    assert "[REDACTED]" in descriptor["preview"]
    assert secret[:100].decode() not in descriptor["preview"]
    assert len(descriptor["preview"]) <= 500


def test_snapshot_marks_descriptor_preview_redaction(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    event = collector.record(
        transition="B06_PYTHON_TOOL_TO_WRAPPER",
        from_module="python_tool",
        to_module="lumibot_tool_wrapper",
        payload=b"api_key=descriptor-preview-secret",
    )

    assert event["payload"]["preview"] == "b'api_key=[REDACTED]"
    assert event["payload_meta"]["fidelity"] == "descriptor_only"
    assert event["payload_meta"]["redacted"] is True


def test_descriptor_only_container_reports_keys_and_length(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    descriptor = collector.describe_raw_value({"opaque": object()})

    assert descriptor["fidelity"] == "descriptor_only"
    assert descriptor["keys"] == ["opaque"]
    assert descriptor["length"] == 1
    assert "preview" not in descriptor


def test_descriptor_mapping_key_sample_is_bounded(tmp_path):
    class LargeOpaqueMapping(Mapping):
        def __init__(self):
            self.iterated_keys = 0

        def __getitem__(self, key):
            if 0 <= key < 10_000:
                return object()
            raise KeyError(key)

        def __iter__(self):
            for key in range(10_000):
                self.iterated_keys += 1
                yield key

        def __len__(self):
            return 10_000

    value = LargeOpaqueMapping()
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    descriptor = collector.describe_raw_value(value)

    assert descriptor["fidelity"] == "descriptor_only"
    assert descriptor["length"] == 10_000
    assert len(descriptor["keys"]) == 100
    assert value.iterated_keys <= 101


def test_custom_mapping_keys_do_not_invoke_string_hooks(tmp_path):
    class MutatingKey:
        def __init__(self):
            self.string_calls = 0

        def __hash__(self):
            return id(self)

        def __str__(self):
            self.string_calls += 1
            return "private-key-text"

    key = MutatingKey()
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    event = collector.record(
        transition="B06_PYTHON_TOOL_TO_WRAPPER",
        from_module="python_tool",
        to_module="lumibot_tool_wrapper",
        payload={key: 7},
    )

    assert key.string_calls == 0
    assert event["payload"] == {"<MutatingKey>": 7}
    assert event["payload_meta"]["fidelity"] == "descriptor_only"


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
            "preview": (
                f"<{OpaqueValue.__module__}."
                f"{OpaqueValue.__qualname__} instance>"
            ),
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
            "preview": "<builtins.object instance>",
            "python_type": "object",
            "qualified_type": "builtins.object",
        },
        "supported": [1, 2],
    }
    assert event["payload_meta"]["fidelity"] == "descriptor_only"
    assert "0x" not in str(event["payload"])


def test_payload_paths_are_portable_and_machine_directories_are_scrubbed(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    event = collector.record(
        transition="B06_PYTHON_TOOL_TO_WRAPPER",
        from_module="python_tool",
        to_module="lumibot_tool_wrapper",
        payload={
            "windows_path": PureWindowsPath(
                r"C:\Users\private-windows-user\traces\event.json"
            ),
            "posix_path": PurePosixPath(
                "/home/private-posix-user/traces/event.json"
            ),
            "windows_text": (
                r"failed at C:\Users\private-windows-user\traces\event.json"
            ),
            "posix_text": (
                "failed at /home/private-posix-user/traces/event.json"
            ),
        },
    )

    rendered = json.dumps(event, sort_keys=True)
    assert "private-windows-user" not in rendered
    assert "private-posix-user" not in rendered
    assert event["payload"]["windows_path"] == {
        "kind": "absolute_path",
        "name": "event.json",
    }
    assert event["payload"]["posix_path"] == {
        "kind": "absolute_path",
        "name": "event.json",
    }
    assert event["payload"]["windows_text"] == "failed at [ABSOLUTE_PATH]"
    assert event["payload"]["posix_text"] == "failed at [ABSOLUTE_PATH]"


def test_path_scrubbing_preserves_urls_and_relative_paths(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    event = collector.record(
        transition="B06_PYTHON_TOOL_TO_WRAPPER",
        from_module="python_tool",
        to_module="lumibot_tool_wrapper",
        payload={
            "url": "https://example.com/api/v1",
            "relative": "./traces/event.json",
        },
    )

    assert event["payload"] == {
        "url": "https://example.com/api/v1",
        "relative": "./traces/event.json",
    }


def test_diagnostics_scrub_windows_and_posix_absolute_paths(tmp_path):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    collector.add_diagnostic(
        "path_failure",
        OSError(
            "failed at "
            r"C:\Users\private-windows-user\traces\event.json "
            "and /home/private-posix-user/traces/event.json"
        ),
    )

    message = collector.export()["diagnostics"][0]["message"]
    assert "private-windows-user" not in message
    assert "private-posix-user" not in message
    assert message.count("[ABSOLUTE_PATH]") == 2


@pytest.mark.parametrize(
    "message",
    [
        (
            "failed at "
            r"C:\Users\Private User\trace\event.json "
            "and /home/Private User/trace/event.json during trace write"
        ),
        (
            "failed at "
            r'"C:\Users\Private User\trace\event.json" '
            "and '/home/Private User/trace/event.json' during trace write"
        ),
    ],
)
def test_diagnostics_scrub_complete_absolute_paths_containing_spaces(
    tmp_path,
    message,
):
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=tmp_path)

    collector.add_diagnostic("path_failure", OSError(message))

    safe_message = collector.export()["diagnostics"][0]["message"]
    assert "Private User" not in safe_message
    assert r"trace\event.json" not in safe_message
    assert "trace/event.json" not in safe_message
    assert safe_message.count("[ABSOLUTE_PATH]") == 2
    assert "failed at" in safe_message
    assert "during trace write" in safe_message


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


def test_sidecar_failure_diagnostic_scrubs_machine_path(
    monkeypatch,
    tmp_path,
):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
        inline_payload_limit=1,
    )
    monkeypatch.setattr(
        collector,
        "_write_sidecar",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError(
                "replace failed at "
                r"C:\Users\private-windows-user\boundary\payload.json.gz "
                "and /home/private-posix-user/boundary/payload.json.gz"
            )
        ),
    )

    event = collector.record(
        transition="B07_WRAPPER_TO_FUNCTION_TOOL",
        from_module="lumibot_tool_wrapper",
        to_module="function_tool",
        payload={"value": "large"},
    )

    message = collector.export()["diagnostics"][0]["message"]
    assert event["payload_meta"]["truncated"] is True
    assert "private-windows-user" not in message
    assert "private-posix-user" not in message
    assert message.count("[ABSOLUTE_PATH]") == 2


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


def test_post_snapshot_storage_failure_removes_orphan_sidecar(monkeypatch, tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
        inline_payload_limit=1,
    )

    def fail_storage(event):
        relative = event["payload_meta"]["sidecar_path"]
        assert relative is not None
        assert (tmp_path / relative).is_file()
        raise RuntimeError("event storage failed")

    monkeypatch.setattr(
        collector,
        "_store_event",
        fail_storage,
        raising=False,
    )

    event = collector.record(
        transition="B07_WRAPPER_TO_FUNCTION_TOOL",
        from_module="lumibot_tool_wrapper",
        to_module="function_tool",
        payload="x" * 100,
    )

    assert event == {}
    assert collector.export()["events"] == []
    assert collector.export()["diagnostics"][0]["kind"] == "record_failed"
    assert list(tmp_path.rglob("*.json.gz")) == []


def test_orphan_cleanup_failure_adds_path_free_diagnostic(monkeypatch, tmp_path):
    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
        inline_payload_limit=1,
    )

    def fail_storage(_event):
        raise RuntimeError("event storage failed")

    def fail_unlink(_path, *args, **kwargs):
        raise OSError(
            r"cannot unlink C:\Users\private-windows-user\boundary\payload.json.gz"
        )

    monkeypatch.setattr(collector, "_store_event", fail_storage, raising=False)
    monkeypatch.setattr(Path, "unlink", fail_unlink)

    event = collector.record(
        transition="B07_WRAPPER_TO_FUNCTION_TOOL",
        from_module="lumibot_tool_wrapper",
        to_module="function_tool",
        payload="x" * 100,
    )

    diagnostics = collector.export()["diagnostics"]
    assert event == {}
    assert [item["kind"] for item in diagnostics] == [
        "sidecar_cleanup_failed",
        "record_failed",
    ]
    assert diagnostics[0]["message"] == "orphan sidecar cleanup failed"
    assert "private-windows-user" not in str(diagnostics)


def test_large_payload_is_canonically_serialized_once(monkeypatch, tmp_path):
    from lumibot.components.agents import boundary_trace

    collector = BoundaryTraceCollector(
        agent_run_id="run-1",
        artifact_root=tmp_path,
        inline_payload_limit=1,
    )
    encoded_values = []
    original_encoder = boundary_trace._canonical_json_bytes

    def capture_encoding(value):
        encoded = original_encoder(value)
        encoded_values.append(encoded)
        return encoded

    monkeypatch.setattr(
        "lumibot.components.agents.boundary_trace._canonical_json_bytes",
        capture_encoding,
    )

    event = collector.record(
        transition="B07_WRAPPER_TO_FUNCTION_TOOL",
        from_module="lumibot_tool_wrapper",
        to_module="function_tool",
        payload={"value": "large"},
    )

    assert len(encoded_values) == 1
    assert event["payload_meta"]["sha256"] == hashlib.sha256(
        encoded_values[0]
    ).hexdigest()


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
        encoded=json.dumps(
            {"value": "persisted"},
            sort_keys=True,
        ).encode("utf-8"),
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
        collector._write_sidecar(
            span_id="span-1",
            encoded=b'{"value": "persisted"}',
        )

    assert list(tmp_path.rglob("*.tmp")) == []
    assert list(tmp_path.rglob("*.json.gz")) == []


def test_write_sidecar_requires_artifact_root():
    collector = BoundaryTraceCollector(agent_run_id="run-1", artifact_root=None)

    with pytest.raises(OSError, match="artifact root is unavailable"):
        collector._write_sidecar(
            span_id="span-1",
            encoded=b'{"value": "persisted"}',
        )


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
