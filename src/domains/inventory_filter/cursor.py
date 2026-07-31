"""Signed, scope-bound cursor codec for inventory filter projections."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from binascii import Error as BinasciiError
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

CURSOR_VERSION = 1
CURSOR_TTL_SECONDS = 600
MINIMUM_CURSOR_SECRET_BYTES = 32
MAX_CURSOR_BYTES = 8192
INVALID_CURSOR = "cursor is invalid"


@dataclass(frozen=True)
class CursorScope:
    workspace_id: str
    user_id: str
    authorization_revision: str
    surface: str
    filter_fingerprint: str
    snapshot_revision: int
    facet_query: str | None


@dataclass(frozen=True)
class DecodedCursor:
    scope: CursorScope
    position: dict[str, Any]


class FilterCursorCodec:
    def __init__(
        self,
        secret: str,
        *,
        ttl_seconds: int = CURSOR_TTL_SECONDS,
        now: Callable[[], int | float] = time.time,
    ) -> None:
        key = secret.encode()
        if len(key) < MINIMUM_CURSOR_SECRET_BYTES:
            raise ValueError("cursor signing key must contain at least 32 bytes")
        if ttl_seconds < 1:
            raise ValueError("cursor ttl must be positive")
        self._key = hmac.new(key, b"opsia:inventory-filter-cursor:v1", hashlib.sha256).digest()
        self._ttl_seconds = ttl_seconds
        self._now = now

    def encode(self, scope: CursorScope, *, position: Mapping[str, Any]) -> str:
        payload = {
            "v": CURSOR_VERSION,
            "workspace_id": scope.workspace_id,
            "user_id": scope.user_id,
            "authorization_revision": scope.authorization_revision,
            "surface": scope.surface,
            "filter_fingerprint": scope.filter_fingerprint,
            "snapshot_revision": scope.snapshot_revision,
            "facet_query": scope.facet_query,
            "position": dict(position),
            "expires_at": int(self._now()) + self._ttl_seconds,
        }
        body = json.dumps(
            payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode()
        signature = hmac.new(self._key, body, hashlib.sha256).digest()
        token = f"{_encode(body)}.{_encode(signature)}"
        if len(token) > MAX_CURSOR_BYTES:
            raise ValueError(INVALID_CURSOR)
        return token

    def decode(self, token: str, *, expected: CursorScope) -> DecodedCursor:
        decoded = self.inspect(token)
        if decoded.scope != expected:
            raise ValueError("cursor scope changed")
        return decoded

    def inspect(self, token: str) -> DecodedCursor:
        """Verify one cursor without accepting it as authorization or current scope."""
        try:
            if not token or len(token) > MAX_CURSOR_BYTES:
                raise ValueError(INVALID_CURSOR)
            body_token, signature_token = token.split(".", 1)
            body = _decode(body_token)
            supplied_signature = _decode(signature_token)
            expected_signature = hmac.new(self._key, body, hashlib.sha256).digest()
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise ValueError(INVALID_CURSOR)
            payload = json.loads(body)
            if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
                raise ValueError(INVALID_CURSOR)
            expires_at = payload.get("expires_at")
            if isinstance(expires_at, bool) or not isinstance(expires_at, int):
                raise ValueError(INVALID_CURSOR)
            if expires_at <= int(self._now()):
                raise ValueError("cursor expired")
            scope = CursorScope(
                workspace_id=_required_text(payload, "workspace_id"),
                user_id=_required_text(payload, "user_id"),
                authorization_revision=_required_text(payload, "authorization_revision"),
                surface=_required_text(payload, "surface"),
                filter_fingerprint=_required_text(payload, "filter_fingerprint"),
                snapshot_revision=_required_int(payload, "snapshot_revision"),
                facet_query=_optional_text(payload, "facet_query"),
            )
            position = payload.get("position")
            if not isinstance(position, dict):
                raise ValueError(INVALID_CURSOR)
            return DecodedCursor(scope=scope, position=dict(position))
        except (
            BinasciiError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            KeyError,
        ) as exc:
            raise ValueError(INVALID_CURSOR) from exc


def authorization_revision(
    *,
    user_id: str,
    workspace_id: str,
    roles: Sequence[str],
    allowed_cluster_ids: Collection[str],
    allowed_application_ids: Collection[str],
) -> str:
    payload = {
        "user_id": user_id,
        "workspace_id": workspace_id,
        "roles": sorted(set(roles)),
        "clusters": sorted(set(allowed_cluster_ids)),
        "applications": sorted(set(allowed_application_ids)),
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(f"{value}{padding}", altchars=b"-_", validate=True)


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or not value:
        raise ValueError(INVALID_CURSOR)
    return value


def _optional_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(INVALID_CURSOR)
    return value


def _required_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(INVALID_CURSOR)
    return value
