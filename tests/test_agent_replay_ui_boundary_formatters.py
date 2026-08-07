import pytest

from lumibot.components.agents.replay_ui.boundary_formatters import (
    BOUNDARY_TRANSITION_ORDER,
    BOUNDARY_TRANSITIONS,
    MAX_PREVIEW_CHARS,
    summarize_boundary_event,
)


@pytest.mark.parametrize("transition", BOUNDARY_TRANSITION_ORDER)
def test_formatter_covers_each_boundary_transition(transition):
    summary = summarize_boundary_event(
        {
            "transition": transition,
            "status": "success",
            "payload": {"tool_name": "market_last_price", "args": {"symbol": "SPY"}},
            "payload_meta": {"semantic_completeness": "complete"},
        }
    )

    assert summary["label"]
    assert summary["source"]
    assert summary["target"]
    assert summary["explanation"]
    assert "complete" in summary["badges"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("success", "success"),
        ("error", "error"),
        ("not_available", "not available"),
        ("not_applicable", "not applicable"),
    ],
)
def test_formatter_surfaces_status_badges(status, expected):
    summary = summarize_boundary_event(
        {
            "transition": "B01_PROVIDER_TO_LITELLM",
            "status": status,
            "payload": {},
            "payload_meta": {},
        }
    )

    assert expected in summary["badges"]


def test_formatter_surfaces_payload_fidelity_badges():
    summary = summarize_boundary_event(
        {
            "transition": "B10_LITELLM_TO_PROVIDER",
            "status": "success",
            "payload": {"messages": [{"role": "user", "content": "hello"}]},
            "payload_meta": {
                "semantic_completeness": "partial",
                "redacted": True,
                "truncated": True,
                "pruned": True,
                "sidecar_path": "boundary_payloads/event.json.gz",
            },
        }
    )

    assert "partial" in summary["badges"]
    assert "redacted" in summary["badges"]
    assert "truncated" in summary["badges"]
    assert "pruned" in summary["badges"]
    assert "sidecar" in summary["badges"]


def test_formatter_does_not_fabricate_missing_payload():
    summary = summarize_boundary_event(
        {
            "transition": "B06_PYTHON_TOOL_TO_WRAPPER",
            "status": "not_available",
            "payload": None,
            "payload_meta": {},
        }
    )

    assert "not available" in summary["preview"].lower()


def test_formatter_bounds_long_error_preview():
    summary = summarize_boundary_event(
        {
            "transition": "B06_PYTHON_TOOL_TO_WRAPPER",
            "status": "error",
            "payload": {"error": "x" * (MAX_PREVIEW_CHARS * 2)},
            "payload_meta": {},
        }
    )

    assert len(summary["preview"]) <= MAX_PREVIEW_CHARS
    assert summary["preview"].endswith("...")


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "tool_" + ("x" * (MAX_PREVIEW_CHARS * 2))},
        {"name": "name_" + ("x" * (MAX_PREVIEW_CHARS * 2))},
        {"function": {"name": "function_" + ("x" * (MAX_PREVIEW_CHARS * 2))}},
    ],
)
def test_formatter_bounds_long_name_previews(payload):
    summary = summarize_boundary_event(
        {
            "transition": "B03_ADK_TO_FUNCTION_TOOL",
            "status": "success",
            "payload": payload,
            "payload_meta": {},
        }
    )

    assert len(summary["preview"]) <= MAX_PREVIEW_CHARS
    assert summary["preview"].endswith("...")


def test_provider_boundaries_describe_provider_litellm_snapshots_not_http_internals():
    for transition in ("B01_PROVIDER_TO_LITELLM", "B10_LITELLM_TO_PROVIDER"):
        definition = BOUNDARY_TRANSITIONS[transition]
        text = " ".join(
            [
                definition["label"],
                definition["source"],
                definition["target"],
                definition["explanation"],
            ]
        ).lower()

        assert "provider" in text
        assert "litellm" in text
        assert "http" not in text
        assert "openai server" not in text
