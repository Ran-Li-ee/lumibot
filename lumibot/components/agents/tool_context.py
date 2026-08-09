from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


_CURRENT_AGENT_TOOL_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "lumibot_current_agent_tool_context",
    default=None,
)


def current_agent_tool_context() -> dict[str, Any]:
    context = _CURRENT_AGENT_TOOL_CONTEXT.get()
    return dict(context or {})


def append_agent_tool_context_list_item(key: str, item: Any) -> bool:
    context = _CURRENT_AGENT_TOOL_CONTEXT.get()
    if not isinstance(context, dict):
        return False
    values = context.setdefault(key, [])
    if not isinstance(values, list):
        return False
    values.append(item)
    return True


@contextmanager
def agent_tool_context(context: dict[str, Any] | None) -> Iterator[None]:
    token = _CURRENT_AGENT_TOOL_CONTEXT.set(context if isinstance(context, dict) else {})
    try:
        yield
    finally:
        _CURRENT_AGENT_TOOL_CONTEXT.reset(token)
