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
