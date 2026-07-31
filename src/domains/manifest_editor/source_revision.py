"""Signed handoff for one server-resolved manifest edit source."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from binascii import Error as BinasciiError
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

SOURCE_REVISION_VERSION = 1
SOURCE_REVISION_TTL_SECONDS = 1_800
MINIMUM_SIGNING_KEY_BYTES = 32
MAX_SOURCE_REVISION_TOKEN_BYTES = 8_192
INVALID_SOURCE_REVISION = "manifest source revision is invalid"


@dataclass(frozen=True)
class SourceRevision:
    workspace_id: str
    user_id: str
    resource_id: str
    application_id: str
    repository_ref: str
    branch: str
    binding_manifest_path: str
    resolved_manifest_path: str
    base_sha: str
    source_sha256: str


class SourceRevisionCodec:
    """Authenticate the exact source selected by the server-side Kustomize walk."""

    def __init__(
        self,
        signing_key: str,
        *,
        ttl_seconds: int = SOURCE_REVISION_TTL_SECONDS,
        now: Callable[[], int | float] = time.time,
    ) -> None:
        raw_key = signing_key.encode()
        if len(raw_key) < MINIMUM_SIGNING_KEY_BYTES:
            raise ValueError("source revision signing key must contain at least 32 bytes")
        if ttl_seconds < 1:
            raise ValueError("source revision ttl must be positive")
        self._key = hmac.new(
            raw_key,
            b"opsia:manifest-source-revision:v1",
            hashlib.sha256,
        ).digest()
        self._ttl_seconds = ttl_seconds
        self._now = now

    def encode(self, revision: SourceRevision) -> str:
        payload = {
            "v": SOURCE_REVISION_VERSION,
            **asdict(revision),
            "expires_at": int(self._now()) + self._ttl_seconds,
        }
        body = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        signature = hmac.new(self._key, body, hashlib.sha256).digest()
        token = f"{_encode(body)}.{_encode(signature)}"
        if len(token) > MAX_SOURCE_REVISION_TOKEN_BYTES:
            raise ValueError(INVALID_SOURCE_REVISION)
        return token

    def decode(self, token: str, *, expected: SourceRevision) -> SourceRevision:
        decoded = self.inspect(token)
        if decoded != expected:
            raise ValueError("manifest source revision scope changed")
        return decoded

    def inspect(self, token: str) -> SourceRevision:
        payload = self._verified_payload(token)
        return SourceRevision(
            workspace_id=_required_text(payload, "workspace_id"),
            user_id=_required_text(payload, "user_id"),
            resource_id=_required_text(payload, "resource_id"),
            application_id=_required_text(payload, "application_id"),
            repository_ref=_required_text(payload, "repository_ref"),
            branch=_required_text(payload, "branch"),
            binding_manifest_path=_required_text(payload, "binding_manifest_path"),
            resolved_manifest_path=_required_text(payload, "resolved_manifest_path"),
            base_sha=_required_text(payload, "base_sha"),
            source_sha256=_required_text(payload, "source_sha256"),
        )

    def _verified_payload(self, token: str) -> Mapping[str, Any]:
        try:
            if not token or len(token) > MAX_SOURCE_REVISION_TOKEN_BYTES:
                raise ValueError(INVALID_SOURCE_REVISION)
            body_token, signature_token = token.split(".", 1)
            body = _decode(body_token)
            supplied_signature = _decode(signature_token)
            expected_signature = hmac.new(self._key, body, hashlib.sha256).digest()
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise ValueError(INVALID_SOURCE_REVISION)
            payload = json.loads(body)
            if not isinstance(payload, dict) or payload.get("v") != SOURCE_REVISION_VERSION:
                raise ValueError(INVALID_SOURCE_REVISION)
            expires_at = payload.get("expires_at")
            if isinstance(expires_at, bool) or not isinstance(expires_at, int):
                raise ValueError(INVALID_SOURCE_REVISION)
            if expires_at <= int(self._now()):
                raise ValueError("manifest source revision expired")
            return payload
        except (
            BinasciiError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            KeyError,
        ) as exc:
            raise ValueError(INVALID_SOURCE_REVISION) from exc


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(INVALID_SOURCE_REVISION)
    return value


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
