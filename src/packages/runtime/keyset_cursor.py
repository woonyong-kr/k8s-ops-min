"""Reusable opaque cursor for stable two-column keyset pagination."""

from __future__ import annotations

import base64
import json
from binascii import Error as BinasciiError
from dataclasses import dataclass
from datetime import datetime

CURSOR_VERSION = 1
MAX_KEYSET_CURSOR_LENGTH = 4096
INVALID_KEYSET_CURSOR = "cursor is invalid"


@dataclass(frozen=True)
class KeysetCursor:
    ordered_at: datetime
    tie_breaker: str


def encode_keyset_cursor(
    *,
    scope: str,
    ordered_at: object,
    tie_breaker: str,
) -> str:
    timestamp = ordered_at.isoformat() if hasattr(ordered_at, "isoformat") else str(ordered_at)
    payload = {
        "v": CURSOR_VERSION,
        "scope": scope,
        "ordered_at": timestamp,
        "tie_breaker": tie_breaker,
    }
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if len(token) > MAX_KEYSET_CURSOR_LENGTH:
        raise ValueError(INVALID_KEYSET_CURSOR)
    return token


def decode_keyset_cursor(token: str, *, expected_scope: str) -> KeysetCursor:
    try:
        if not token or len(token) > MAX_KEYSET_CURSOR_LENGTH:
            raise ValueError(INVALID_KEYSET_CURSOR)
        padding = "=" * (-len(token) % 4)
        decoded = base64.b64decode(
            f"{token}{padding}",
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
        payload = json.loads(decoded)
        if (
            not isinstance(payload, dict)
            or payload.get("v") != CURSOR_VERSION
            or payload.get("scope") != expected_scope
        ):
            raise ValueError(INVALID_KEYSET_CURSOR)
        ordered_at = datetime.fromisoformat(str(payload["ordered_at"]).replace("Z", "+00:00"))
        tie_breaker = str(payload["tie_breaker"]).strip()
        if not tie_breaker:
            raise ValueError(INVALID_KEYSET_CURSOR)
    except (
        BinasciiError,
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError(INVALID_KEYSET_CURSOR) from exc
    return KeysetCursor(ordered_at=ordered_at, tie_breaker=tie_breaker)
