from lumibot.components.agents.replay_ui.models import (
    AgentDependency,
    AgentReplay,
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
