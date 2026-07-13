from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"
DEFAULT_PUBLIC_TEXT_LIMIT = 4000
DEFAULT_PUBLIC_ITEM_LIMIT = 50
DEFAULT_PUBLIC_DEPTH_LIMIT = 4

_SENSITIVE_LABEL = r"api[_-]?key|authorization|bearer|password|secret|token"
_SENSITIVE_KEY_RE = re.compile(r"(api[_-]?key|authorization|bearer|password|secret|token)", re.IGNORECASE)
_QUOTED_SENSITIVE_PAIR_RE = re.compile(
    rf"(?P<prefix>(?P<key_quote>['\"])[^'\"]*(?:{_SENSITIVE_LABEL})[^'\"]*(?P=key_quote)\s*:\s*)"
    r"(?P<value_quote>['\"])(?P<value>.*?)(?P=value_quote)",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    rf"\b([A-Za-z0-9_]*(?:{_SENSITIVE_LABEL})[A-Za-z0-9_]*\s*=\s*)"
    r"([^\s,;&]+)",
    re.IGNORECASE,
)
_SENSITIVE_COLON_RE = re.compile(
    rf"\b([A-Za-z0-9_ -]*(?:{_SENSITIVE_LABEL})[A-Za-z0-9_ -]*\s*:\s*)"
    r"([^\r\n,;&\"'}\]\)]+)([\"'}\]\)]?)",
    re.IGNORECASE,
)
_BEARER_TOKEN_RE = re.compile(r"\b(Bearer\s+)([^\s,;&\"'}\]\)]+)([\"'}\]\)]?)", re.IGNORECASE)
_SK_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9_-]+")


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED if _SENSITIVE_KEY_RE.search(str(key)) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    if isinstance(value, str):
        return _redact_string(value)
    return value


def redact_public_preview(
    value: Any,
    *,
    max_text: int = DEFAULT_PUBLIC_TEXT_LIMIT,
    max_items: int = DEFAULT_PUBLIC_ITEM_LIMIT,
    max_depth: int = DEFAULT_PUBLIC_DEPTH_LIMIT,
) -> Any:
    if max_depth < 0:
        return "[truncated nested value]"
    if isinstance(value, dict):
        preview: dict[str, Any] = {}
        items = list(value.items())
        for key, item in items[:max_items]:
            key_text = str(key)
            preview[key_text] = REDACTED if _SENSITIVE_KEY_RE.search(key_text) else redact_public_preview(
                item,
                max_text=max_text,
                max_items=max_items,
                max_depth=max_depth - 1,
            )
        omitted = len(items) - max_items
        if omitted > 0:
            preview["__truncated_items__"] = f"{omitted} additional entries omitted"
        return preview
    if isinstance(value, list):
        preview_items = [
            redact_public_preview(
                item,
                max_text=max_text,
                max_items=max_items,
                max_depth=max_depth - 1,
            )
            for item in value[:max_items]
        ]
        omitted = len(value) - max_items
        if omitted > 0:
            preview_items.append(f"[truncated {omitted} additional items]")
        return preview_items
    if isinstance(value, tuple):
        return tuple(
            redact_public_preview(
                item,
                max_text=max_text,
                max_items=max_items,
                max_depth=max_depth - 1,
            )
            for item in value[:max_items]
        )
    if isinstance(value, str):
        if len(value) <= max_text:
            return _redact_string(value)
        omitted = len(value) - max_text
        return f"{_redact_string(value[:max_text])}\n[truncated {omitted} characters]"
    return value


def _redact_string(value: str) -> str:
    redacted = _QUOTED_SENSITIVE_PAIR_RE.sub(
        lambda match: f"{match.group('prefix')}{match.group('value_quote')}{REDACTED}{match.group('value_quote')}",
        value,
    )
    redacted = _SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{REDACTED}", redacted)
    redacted = _SENSITIVE_COLON_RE.sub(_redact_delimited_secret_match, redacted)
    redacted = _BEARER_TOKEN_RE.sub(_redact_bearer_match, redacted)
    return _SK_TOKEN_RE.sub(REDACTED, redacted)


def _redact_delimited_secret_match(match: re.Match[str]) -> str:
    suffix = match.group(3)
    if match.group(2).endswith("."):
        suffix = f".{suffix}"
    return f"{match.group(1)}{REDACTED}{suffix}"


def _redact_bearer_match(match: re.Match[str]) -> str:
    suffix = match.group(3)
    if match.group(2).endswith("."):
        suffix = f".{suffix}"
    return f"{match.group(1)}{REDACTED}{suffix}"
