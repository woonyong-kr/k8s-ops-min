"""Redaction helpers for release-flow API and audit payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

JsonObject = dict[str, Any]
REDACTED_VALUE = "<redacted>"
SENSITIVE_KEY_PARTS = (
    "authorization",
    "bearer",
    "credential",
    "password",
    "passwd",
    "private_key",
    "secret",
    "token",
    "api_key",
    "apikey",
)


def redact_release_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: JsonObject = {}
        for key, item in value.items():
            key_text = str(key)
            redacted[key_text] = (
                REDACTED_VALUE if is_sensitive_key(key_text) else redact_release_value(item)
            )
        return redacted
    if isinstance(value, list):
        return [redact_release_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_release_value(item) for item in value]
    return value


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_").replace(" ", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)
