"""Shared defense-in-depth sanitization for log lines crossing trust boundaries."""

from __future__ import annotations

import re
from typing import Any


def _normalized_sensitive_key(value: str) -> str:
    return value.casefold().replace("-", "_").replace(".", "_").replace(" ", "_")


REDACTED_VALUE = "[REDACTED]"
REDACTED_JWT = "[REDACTED_JWT]"
REDACTED_PRIVATE_KEY = "[REDACTED_PRIVATE_KEY]"
MAX_LOG_LINE_LENGTH = 4096
TRUNCATED_LOG_LINE_SUFFIX = " [TRUNCATED]"
TRUNCATED_VALUE = "[TRUNCATED]"
TRUNCATED_MAPPING_KEY = "_truncated"
MAX_REDACTED_VALUE_DEPTH = 12
MAX_REDACTED_MAPPING_ITEMS = 100
MAX_REDACTED_SEQUENCE_ITEMS = 100

SENSITIVE_KEYS = (
    "password",
    "passwd",
    "pwd",
    "token",
    "access_token",
    "access-token",
    "refresh_token",
    "refresh-token",
    "id_token",
    "id-token",
    "secret",
    "api_key",
    "api-key",
    "apikey",
    "client_secret",
    "client-secret",
    "credential",
    "credentials",
    "private_key",
    "private-key",
    "ssh_key",
    "ssh-key",
)
SENSITIVE_HEADER_KEYS = (
    "authorization",
    "cookie",
    "set-cookie",
)
SENSITIVE_KEY_PATTERN = "|".join(re.escape(key) for key in SENSITIVE_KEYS)
SENSITIVE_VALUE_KEY_PARTS = frozenset(
    _normalized_sensitive_key(key) for key in (*SENSITIVE_KEYS, *SENSITIVE_HEADER_KEYS)
)
SENSITIVE_KEY_VALUE_RE = re.compile(
    rf"(?i)(\b(?:{SENSITIVE_KEY_PATTERN})\b[\"']?\s*[:=]\s*)"
    rf"(?:\"[^\"]*\"|'[^']*'|[^\s,;{{}}]+)"
)
AUTHORIZATION_RE = re.compile(r"(?i)\b(authorization\s*[:=]\s*(?:bearer|basic)?\s*)[^\s,;]+")
BEARER_TOKEN_RE = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/-]+=*")
COOKIE_RE = re.compile(r"(?i)\b((?:cookie|set-cookie)\s*[:=]\s*)[^\r\n]+")
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
URL_USERINFO_RE = re.compile(r"\b([a-z][a-z0-9+.-]*://)[^/\s:@]+(?::[^/\s@]*)?@")
PRIVATE_KEY_MARKER_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|-----END [A-Z ]*PRIVATE KEY-----",
    re.I,
)


def redact_log_line(line: str) -> str:
    """Hide sensitive values while keeping useful operational context."""
    if PRIVATE_KEY_MARKER_RE.search(line):
        return REDACTED_PRIVATE_KEY

    redacted = URL_USERINFO_RE.sub(r"\1[REDACTED]@", line)
    redacted = AUTHORIZATION_RE.sub(rf"\1{REDACTED_VALUE}", redacted)
    redacted = BEARER_TOKEN_RE.sub(rf"\1{REDACTED_VALUE}", redacted)
    redacted = COOKIE_RE.sub(rf"\1{REDACTED_VALUE}", redacted)
    redacted = JWT_RE.sub(REDACTED_JWT, redacted)
    redacted = AWS_ACCESS_KEY_RE.sub(REDACTED_VALUE, redacted)
    redacted = EMAIL_RE.sub(REDACTED_VALUE, redacted)
    return SENSITIVE_KEY_VALUE_RE.sub(rf"\1{REDACTED_VALUE}", redacted)


def redact_sensitive_value(
    value: Any,
    *,
    max_depth: int = MAX_REDACTED_VALUE_DEPTH,
    max_mapping_items: int = MAX_REDACTED_MAPPING_ITEMS,
    max_sequence_items: int = MAX_REDACTED_SEQUENCE_ITEMS,
) -> Any:
    """Recursively redact JSON-like values before they cross an AI/log boundary."""
    return _redact_sensitive_value(
        value,
        depth=0,
        max_depth=max_depth,
        max_mapping_items=max_mapping_items,
        max_sequence_items=max_sequence_items,
    )


def _redact_sensitive_value(
    value: Any,
    *,
    depth: int,
    max_depth: int,
    max_mapping_items: int,
    max_sequence_items: int,
) -> Any:
    if depth >= max_depth:
        return TRUNCATED_VALUE
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_mapping_items:
                redacted[TRUNCATED_MAPPING_KEY] = True
                break
            key_text = str(key)
            if _is_sensitive_value_key(key_text):
                redacted[key_text] = REDACTED_VALUE
            else:
                redacted[key_text] = _redact_sensitive_value(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_mapping_items=max_mapping_items,
                    max_sequence_items=max_sequence_items,
                )
        return redacted
    if isinstance(value, (list, tuple)):
        redacted_items = [
            _redact_sensitive_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_mapping_items=max_mapping_items,
                max_sequence_items=max_sequence_items,
            )
            for item in value[:max_sequence_items]
        ]
        if len(value) > max_sequence_items:
            redacted_items.append(TRUNCATED_VALUE)
        return tuple(redacted_items) if isinstance(value, tuple) else redacted_items
    if isinstance(value, str):
        return truncate_log_line(redact_log_line(value))[0]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return truncate_log_line(redact_log_line(str(value)))[0]


def truncate_log_line(line: str) -> tuple[str, bool]:
    """Keep a log line inside the browser/evidence contract bound."""
    if len(line) <= MAX_LOG_LINE_LENGTH:
        return line, False
    keep_length = max(0, MAX_LOG_LINE_LENGTH - len(TRUNCATED_LOG_LINE_SUFFIX))
    return f"{line[:keep_length]}{TRUNCATED_LOG_LINE_SUFFIX}", True


def _is_sensitive_value_key(key: str) -> bool:
    normalized = _normalized_sensitive_key(key)
    compact = normalized.replace("_", "")
    return any(
        part in normalized or part.replace("_", "") in compact for part in SENSITIVE_VALUE_KEY_PARTS
    )
