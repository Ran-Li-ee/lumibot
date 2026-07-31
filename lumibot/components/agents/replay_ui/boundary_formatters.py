"""Human-readable summaries for agent boundary trace events."""

from __future__ import annotations

from typing import Any

MAX_PREVIEW_CHARS = 240

BOUNDARY_TRANSITIONS: dict[str, dict[str, str]] = {
    "B01_PROVIDER_TO_LITELLM": {
        "label": "Provider response",
        "source": "Provider",
        "target": "LiteLLM",
        "explanation": "The remote model provider returned a visible response to LiteLLM.",
    },
    "B02_LITELLM_TO_ADK": {
        "label": "LiteLLM response parsed for ADK",
        "source": "LiteLLM",
        "target": "Google ADK",
        "explanation": "LiteLLM converted the provider response into ADK's response representation.",
    },
    "B03_ADK_TO_FUNCTION_TOOL": {
        "label": "ADK dispatches FunctionTool",
        "source": "Google ADK",
        "target": "ADK FunctionTool",
        "explanation": "ADK selected a FunctionTool for a model-requested tool call.",
    },
    "B04_FUNCTION_TOOL_TO_WRAPPER": {
        "label": "FunctionTool calls Lumibot wrapper",
        "source": "ADK FunctionTool",
        "target": "Lumibot wrapper",
        "explanation": "The ADK FunctionTool passed validated arguments into Lumibot's tool wrapper.",
    },
    "B05_WRAPPER_TO_PYTHON_TOOL": {
        "label": "Wrapper calls Python tool",
        "source": "Lumibot wrapper",
        "target": "Python tool",
        "explanation": "Lumibot invoked the original local Python tool function.",
    },
    "B06_PYTHON_TOOL_TO_WRAPPER": {
        "label": "Python tool returns",
        "source": "Python tool",
        "target": "Lumibot wrapper",
        "explanation": "The local Python tool returned its raw result to the Lumibot wrapper.",
    },
    "B07_WRAPPER_TO_FUNCTION_TOOL": {
        "label": "Wrapper serializes result",
        "source": "Lumibot wrapper",
        "target": "ADK FunctionTool",
        "explanation": "Lumibot converted the tool result into a JSON-safe function response payload.",
    },
    "B08_FUNCTION_TOOL_TO_ADK": {
        "label": "FunctionTool returns to ADK",
        "source": "ADK FunctionTool",
        "target": "Google ADK",
        "explanation": "The ADK FunctionTool returned the function response to the ADK runtime.",
    },
    "B09_ADK_TO_LITELLM": {
        "label": "ADK builds model request",
        "source": "Google ADK",
        "target": "LiteLLM",
        "explanation": "ADK built the next model request, including any model-facing tool results.",
    },
    "B10_LITELLM_TO_PROVIDER": {
        "label": "LiteLLM sends provider request",
        "source": "LiteLLM",
        "target": "Provider",
        "explanation": "LiteLLM translated the ADK request into the provider-facing request shape.",
    },
}

BOUNDARY_TRANSITION_ORDER = tuple(BOUNDARY_TRANSITIONS)


def summarize_boundary_event(event: dict[str, Any]) -> dict[str, Any]:
    transition = str(event.get("transition") or "UNKNOWN_BOUNDARY")
    definition = BOUNDARY_TRANSITIONS.get(
        transition,
        {
            "label": transition,
            "source": "Unknown",
            "target": "Unknown",
            "explanation": "This boundary transition is not recognized by this UI version.",
        },
    )
    payload = event.get("payload")
    payload_meta = event.get("payload_meta") if isinstance(event.get("payload_meta"), dict) else {}
    status = str(event.get("status") or "unknown")
    return {
        "label": definition["label"],
        "source": definition["source"],
        "target": definition["target"],
        "explanation": definition["explanation"],
        "badges": _badges(status, payload_meta),
        "preview": _preview(status, payload),
    }


def _badges(status: str, payload_meta: dict[str, Any]) -> list[str]:
    badges: list[str] = []
    status_badge = status.replace("_", " ")
    if status_badge:
        badges.append(status_badge)
    completeness = payload_meta.get("semantic_completeness")
    if isinstance(completeness, str) and completeness and completeness not in badges:
        badges.append(completeness)
    for key, label in (
        ("redacted", "redacted"),
        ("truncated", "truncated"),
        ("pruned", "pruned"),
    ):
        if payload_meta.get(key) is True:
            badges.append(label)
    if payload_meta.get("sidecar_path"):
        badges.append("sidecar")
    return badges


def _preview(status: str, payload: Any) -> str:
    if status in {"not_available", "not_applicable"}:
        return _bounded_preview_text(status.replace("_", " "))
    if payload is None:
        return _bounded_preview_text("Payload not available.")
    if isinstance(payload, dict):
        return _preview_dict(payload)
    if isinstance(payload, list):
        return _bounded_preview_text(f"{len(payload)} item(s)")
    text = str(payload)
    return _bounded_preview_text(text)


def _preview_dict(payload: dict[str, Any]) -> str:
    for key in ("tool_name", "name", "function_name"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return _bounded_preview_text(value)
    function = payload.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str):
        return _bounded_preview_text(function["name"])
    if isinstance(payload.get("messages"), list):
        return _bounded_preview_text(f"{len(payload['messages'])} model message(s)")
    if isinstance(payload.get("tool_calls"), list):
        return _bounded_preview_text(f"{len(payload['tool_calls'])} provider tool call(s)")
    if isinstance(payload.get("function_calls"), list):
        return _bounded_preview_text(f"{len(payload['function_calls'])} ADK function call(s)")
    if "result" in payload:
        return _bounded_preview_text("Tool result payload")
    if "error" in payload:
        return _bounded_preview_text(f"Error payload: {payload.get('error')}")
    keys = ", ".join(str(key) for key in list(payload.keys())[:5])
    return _bounded_preview_text(f"Payload keys: {keys}" if keys else "Empty object payload")


def _bounded_preview_text(value: Any) -> str:
    text = str(value)
    if len(text) <= MAX_PREVIEW_CHARS:
        return text
    return text[: MAX_PREVIEW_CHARS - 3] + "..."
