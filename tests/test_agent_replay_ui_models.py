from lumibot.components.agents.replay_ui.models import (
    AgentDependency,
    AgentReplay,
    BoundaryEventReplay,
    BoundaryTraceReplay,
    ReplayDataset,
    ReplayRun,
    SystemRun,
    ToolBatch,
    ToolCallReplay,
)
from lumibot.components.agents.replay_ui.redaction import redact_sensitive
from lumibot.components.agents.trace_redaction import (
    redact_public_preview,
)
from lumibot.components.agents.trace_redaction import (
    redact_sensitive as shared_redact_sensitive,
)


def test_replay_ui_redact_sensitive_reexports_shared_function():
    assert redact_sensitive is shared_redact_sensitive


def test_redact_sensitive_masks_secret_shaped_keys_and_values():
    payload = {
        "api_key": "sk-test-secret",
        "nested": {"Authorization": "Bearer abc123", "normal": "visible"},
        "items": [{"token": "plain-token"}, "OPENAI_API_KEY=sk-visible-pattern"],
    }

    redacted = redact_sensitive(payload)

    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["Authorization"] == "[REDACTED]"
    assert redacted["nested"]["normal"] == "visible"
    assert redacted["items"][0]["token"] == "[REDACTED]"
    assert redacted["items"][1] == "OPENAI_API_KEY=[REDACTED]"


def test_redact_sensitive_preserves_safe_token_counts_only():
    api_token = "sk-proj-synthetic-usage-secret"
    bearer_token = "synthetic-usage-bearer-secret"
    payload = {
        "usage": {
            "prompt_tokens": 101,
            "completion_tokens": 23.5,
            "total_tokens": None,
            "cached_tokens": False,
            "cache_write_input_tokens": 7,
            "prompt_tokens_details": {
                "cached_tokens": 80,
                "note": f"credential={api_token}",
            },
            "completion_tokens_details": {
                "reasoning_tokens": 9,
                "api_key": api_token,
            },
            "reasoning_tokens": api_token,
            "input_tokens": b"structured-secret",
        },
        "token": 123456,
        "api_key": b"binary-secret",
        "Authorization": {"header": f"Bearer {bearer_token}"},
    }

    redacted = redact_sensitive(payload)
    rendered = repr(redacted)

    assert redacted["usage"]["prompt_tokens"] == 101
    assert redacted["usage"]["completion_tokens"] == 23.5
    assert redacted["usage"]["total_tokens"] is None
    assert redacted["usage"]["cached_tokens"] is False
    assert redacted["usage"]["cache_write_input_tokens"] == 7
    assert redacted["usage"]["prompt_tokens_details"]["cached_tokens"] == 80
    assert redacted["usage"]["completion_tokens_details"]["reasoning_tokens"] == 9
    assert redacted["usage"]["prompt_tokens_details"]["note"] == "[REDACTED]"
    assert redacted["usage"]["completion_tokens_details"]["api_key"] == "[REDACTED]"
    assert redacted["usage"]["reasoning_tokens"] == "[REDACTED]"
    assert redacted["usage"]["input_tokens"] == "[REDACTED]"
    assert redacted["token"] == "[REDACTED]"
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["Authorization"] == "[REDACTED]"
    assert api_token not in rendered
    assert bearer_token not in rendered


def test_redact_public_preview_preserves_safe_token_counts_only():
    secret = "sk-proj-synthetic-preview-secret"
    payload = {
        "usage": {
            "Prompt_Tokens": 101,
            "TOTAL_TOKENS": None,
            "CACHE_WRITE_INPUT_TOKENS": 7,
            "completion_tokens_details": {
                "Reasoning_Tokens": 9,
                "note": secret,
            },
            "output_tokens": secret,
        },
        "access_token": 123456,
    }

    preview = redact_public_preview(payload)
    rendered = repr(preview)

    assert preview["usage"]["Prompt_Tokens"] == 101
    assert preview["usage"]["TOTAL_TOKENS"] is None
    assert preview["usage"]["CACHE_WRITE_INPUT_TOKENS"] == 7
    assert preview["usage"]["completion_tokens_details"]["Reasoning_Tokens"] == 9
    assert preview["usage"]["completion_tokens_details"]["note"] == "[REDACTED]"
    assert preview["usage"]["output_tokens"] == "[REDACTED]"
    assert preview["access_token"] == "[REDACTED]"
    assert secret not in rendered


def test_redact_sensitive_masks_bearer_tokens_and_colon_labels():
    payload = {
        "header": "Bearer abc123",
        "message": "password: hunter2",
        "header_text": "Authorization: Bearer abc123",
        "normal": "portfolio tokenization is not a credential",
    }

    redacted = redact_sensitive(payload)

    assert redacted["header"] == "Bearer [REDACTED]"
    assert redacted["message"] == "password: [REDACTED]"
    assert redacted["header_text"] == "Authorization: [REDACTED]"
    assert redacted["normal"] == "portfolio tokenization is not a credential"


def test_redact_sensitive_masks_json_like_quoted_secret_labels():
    payload = {
        "double_quoted": '{"password": "hunter2"}',
        "single_quoted": "{'token': 'abc123'}",
        "header_json": '{"Authorization": "Bearer abc123"}',
    }

    redacted = redact_sensitive(payload)

    assert redacted["double_quoted"] == '{"password": "[REDACTED]"}'
    assert redacted["single_quoted"] == "{'token': '[REDACTED]'}"
    assert redacted["header_json"] == '{"Authorization": "[REDACTED]"}'


def test_redact_sensitive_preserves_punctuation_around_bearer_tokens():
    payload = {
        "jsonish": '{"header": "Bearer abc123"}',
        "sentence": "Authorization: Bearer abc123.",
    }

    redacted = redact_sensitive(payload)

    assert redacted["jsonish"] == '{"header": "Bearer [REDACTED]"}'
    assert redacted["sentence"] == "Authorization: [REDACTED]."


def test_redact_sensitive_masks_dotted_bearer_tokens():
    payload = {
        "jwt": "Bearer header.payload.signature",
        "sentence": "Authorization: Bearer header.payload.signature.",
    }

    redacted = redact_sensitive(payload)

    assert redacted["jwt"] == "Bearer [REDACTED]"
    assert redacted["sentence"] == "Authorization: [REDACTED]."


def test_agent_replay_public_dict_contains_three_detail_areas():
    agent = AgentReplay(
        id="growth-agent-1",
        name="growth_agent",
        model="openai/gpt-5.4-mini",
        trace_path="C:/trace/growth.json",
        request={
            "base_system_prompt": "Base prompt",
            "effective_system_prompt": "Base prompt\n\nUSER SYSTEM PROMPT:\nGrowth system prompt",
            "user_system_prompt": "Growth system prompt",
            "task_prompt": "Pick the strongest ETF.",
            "context": {"date": "2026-04-07", "universe": ["SPY", "QQQ"]},
            "runtime_context": {
                "mode": "backtesting",
                "current_datetime": "2026-04-07T09:30:00-04:00",
            },
            "tool_surface": [{"name": "market_last_price"}, {"name": "duckdb_query"}],
        },
        tool_batches=[
            ToolBatch(
                batch_index=1,
                calls=[
                    ToolCallReplay(
                        tool_name="market_last_price",
                        arguments={"symbol": "QQQ", "api_key": "sk-test-secret"},
                        raw_result={"price": 110.71},
                        error=None,
                        human_explanation="Looked up the latest QQQ price.",
                        timestamp="2026-04-07T09:30:01-04:00",
                    )
                ],
            )
        ],
        summary="RESULT: QQQ is strongest.",
        warnings=[],
        raw_trace={"secret": "sk-test-secret", "agent": "growth_agent"},
    )

    public = agent.to_public_dict()

    assert public["input_material"]["base_system_prompt"] == "Base prompt"
    assert (
        public["input_material"]["effective_system_prompt"]
        == "Base prompt\n\nUSER SYSTEM PROMPT:\nGrowth system prompt"
    )
    assert public["input_material"]["user_system_prompt_heading"] == "USER SYSTEM PROMPT:"
    assert public["input_material"]["priority_explanation"].startswith("Treat this as")
    assert public["input_material"]["agent_system_prompt"] == "Growth system prompt"
    assert public["input_material"]["task_prompt"] == "Pick the strongest ETF."
    assert public["input_material"]["context_keys"] == ["date", "universe"]
    assert public["input_material"]["run_mode"] == "backtesting"
    assert public["input_material"]["current_time"] == "2026-04-07T09:30:00-04:00"
    assert public["input_material"]["available_tool_count"] == 2
    assert public["input_material"]["available_tool_names"] == ["market_last_price", "duckdb_query"]
    assert public["tool_batches"][0]["calls"][0]["arguments"]["api_key"] == "[REDACTED]"
    assert public["tool_batches"][0]["calls"][0]["raw_result"] == {"price": 110.71}
    assert public["summary"] == "RESULT: QQQ is strongest."
    assert "raw_trace" not in public


def test_agent_public_dict_includes_boundary_trace():
    event = BoundaryEventReplay(
        id="event-1",
        transition="B03_ADK_TO_FUNCTION_TOOL",
        model_turn_id="turn-1",
        tool_batch_id="turn-1:batch:0001",
        call_id="call-1",
        status="success",
        payload={"tool_name": "market_last_price"},
        payload_meta={"semantic_completeness": "complete"},
        summary={
            "label": "ADK dispatches FunctionTool",
            "source": "Google ADK",
            "target": "ADK FunctionTool",
            "badges": ["complete"],
            "preview": "market_last_price",
        },
    )
    agent = AgentReplay(
        id="agent-1",
        name="growth_agent",
        model="openai/test",
        trace_path="trace.json",
        boundary_trace=BoundaryTraceReplay(
            available=True,
            schema_version=1,
            events=[event],
            model_turns=[
                {
                    "model_turn_id": "turn-1",
                    "request_response_events": ["event-1"],
                    "tool_batches": [],
                }
            ],
        ),
    )

    public = agent.to_public_dict()

    assert public["boundary_trace"]["available"] is True
    assert public["boundary_trace"]["schema_version"] == 1
    assert public["boundary_trace"]["events"][0]["id"] == "event-1"
    assert public["boundary_trace"]["events"][0]["payload"]["tool_name"] == "market_last_price"
    assert public["boundary_trace"]["model_turns"][0]["model_turn_id"] == "turn-1"


def test_boundary_event_public_dict_exposes_only_safe_sidecar_metadata():
    secret = "sk-test-sidecar-secret"
    event = BoundaryEventReplay(
        id="event-sidecar",
        transition="B10_LITELLM_TO_PROVIDER",
        payload={"preview": "small"},
        payload_meta={
            "sidecar_path": "boundary_payloads/private-event.json.gz",
            "byte_count": 123456,
            "compression": "gzip",
            "sha256": "abc123",
            "semantic_completeness": "complete",
        },
        sidecar={
            "available": True,
            "event_id": "event-sidecar",
            "byte_count": 123456,
            "compression": "gzip",
            "sha256": "abc123",
            "sidecar_path": "boundary_payloads/private-event.json.gz",
            "payload": {"document": "A" * 6000, "api_key": secret},
        },
    )

    public = event.to_public_dict()
    rendered = repr(public)

    assert public["sidecar"] == {
        "available": True,
        "event_id": "event-sidecar",
        "byte_count": 123456,
        "compression": "gzip",
        "sha256": "abc123",
    }
    assert "sidecar_path" not in public["payload_meta"]
    assert "boundary_payloads/private-event.json.gz" not in rendered
    assert secret not in rendered
    assert "A" * 1000 not in rendered


def test_boundary_trace_public_dict_keeps_model_turns_metadata_only():
    secret = "sk-test-model-turn-secret"
    trace = BoundaryTraceReplay(
        available=True,
        schema_version=1,
        model_turns=[
            {
                "model_turn_id": "turn-1",
                "request_response_events": ["event-1"],
                "tool_batches": [
                    {
                        "tool_batch_id": "turn-1:batch:0001",
                        "tool_calls": [
                            {
                                "call_id": "call-1",
                                "tool_name": "market_last_price",
                                "events": ["event-2", "event-3"],
                                "payload": "B" * 6000,
                                "password": "hunter2",
                            }
                        ],
                        "raw_result": {"api_key": secret},
                    }
                ],
                "raw_prompt": "C" * 6000,
                "authorization": f"Bearer {secret}",
            }
        ],
    )

    public = trace.to_public_dict()
    rendered = repr(public)

    assert public["model_turns"] == [
        {
            "model_turn_id": "turn-1",
            "request_response_events": ["event-1"],
            "tool_batches": [
                {
                    "tool_batch_id": "turn-1:batch:0001",
                    "tool_calls": [
                        {
                            "call_id": "call-1",
                            "tool_name": "market_last_price",
                            "events": ["event-2", "event-3"],
                        }
                    ],
                }
            ],
        }
    ]
    assert secret not in rendered
    assert "hunter2" not in rendered
    assert "B" * 1000 not in rendered
    assert "C" * 1000 not in rendered


def test_agent_public_dict_uses_empty_boundary_trace_by_default():
    agent = AgentReplay(
        id="agent-1",
        name="legacy_agent",
        model="openai/test",
        trace_path="trace.json",
    )

    public = agent.to_public_dict()

    assert public["boundary_trace"]["available"] is False
    assert public["boundary_trace"]["events"] == []
    assert "does not contain" in public["boundary_trace"]["message"]


def test_agent_replay_input_material_redacts_runtime_context_fields():
    agent = AgentReplay(
        id="growth-agent-1",
        name="growth_agent",
        model="openai/gpt-5.4-mini",
        trace_path="C:/trace/growth.json",
        request={
            "runtime_context": {
                "mode": "password: hunter2",
                "current_datetime": "Authorization: Bearer abc123",
            },
        },
    )

    public = agent.to_public_dict()

    assert public["input_material"]["run_mode"] == "password: [REDACTED]"
    assert public["input_material"]["current_time"] == "Authorization: [REDACTED]"


def test_tool_call_public_dict_limits_oversized_raw_results():
    call = ToolCallReplay(
        tool_name="get_filing_document",
        arguments={"symbol": "SPY"},
        raw_result={"document": "A" * 6000, "password": "hunter2"},
    )

    public = call.to_public_dict()

    assert len(public["raw_result"]["document"]) < 5000
    assert "[truncated" in public["raw_result"]["document"]
    assert public["raw_result"]["password"] == "[REDACTED]"


def test_public_dict_redacts_secret_shaped_paths():
    agent = AgentReplay(
        id="growth-agent-1",
        name="growth_agent",
        model="openai/gpt-5.4-mini",
        trace_path="C:/tmp/sk-test-secret/growth.json",
    )
    dataset = ReplayDataset(
        runs=[ReplayRun(id="run-1", label="agent_runtime", system_runs=[])],
        source_path="C:/tmp/sk-source-secret/agent_runtime",
    )

    agent_public = agent.to_public_dict()
    dataset_public = dataset.to_public_dict()

    assert agent_public["trace_path"] == "C:/tmp/[REDACTED]/growth.json"
    assert dataset_public["source_path"] == "C:/tmp/[REDACTED]/agent_runtime"


def test_replay_run_public_dict_contains_planned_system_run_shape():
    agent = AgentReplay(id="agent-1", name="trader", model="", trace_path="trace.json")
    run = ReplayRun(
        id="run-1",
        label="agent_runtime",
        strategy_name="Demo",
        system_runs=[
            SystemRun(
                id="backtesting|2026-04-07T09:30:00-04:00|Demo",
                current_datetime="2026-04-07T09:30:00-04:00",
                mode="backtesting",
                agents=[agent],
                dependencies=[AgentDependency(source_label="context:growth_summary", target_agent="trader")],
            )
        ],
    )

    public = run.to_public_dict()

    assert public["label"] == "agent_runtime"
    assert public["strategy_name"] == "Demo"
    assert "systems" not in public
    assert "agents" not in public
    assert public["system_runs"][0]["current_datetime"] == "2026-04-07T09:30:00-04:00"
    assert public["system_runs"][0]["mode"] == "backtesting"
    assert public["system_runs"][0]["agents"][0]["name"] == "trader"
    assert public["system_runs"][0]["dependencies"] == [
        {
            "source_label": "context:growth_summary",
            "target_agent": "trader",
            "source_type": "context",
            "source_status": "unresolved_source",
        }
    ]
