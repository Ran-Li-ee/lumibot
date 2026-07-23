from pathlib import Path

from lumibot.components.agents.boundary_trace import (
    BOUNDARY_SCHEMA_VERSION,
    BoundaryTraceCollector,
)
from lumibot.components.agents.trace_redaction import redact_sensitive


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
