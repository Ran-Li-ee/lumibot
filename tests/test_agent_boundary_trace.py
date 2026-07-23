import json

from lumibot.components.agents.boundary_trace import (
    BOUNDARY_SCHEMA_VERSION,
    BoundaryTraceCollector,
)
from lumibot.components.agents.trace_redaction import redact_sensitive


class CredentialBearingValue:
    def __repr__(self):
        return "api_key=custom-object-secret"


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
        "7": "api_key=[REDACTED]",
        "authorization": "[REDACTED]",
    }
    assert json.loads(json.dumps(event["payload"], sort_keys=True)) == event["payload"]
    assert "custom-object-secret" not in str(collector.export())
    assert "mixed-mapping-secret" not in str(collector.export())
