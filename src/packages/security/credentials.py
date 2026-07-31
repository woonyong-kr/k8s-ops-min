"""워크스페이스 자격증명 암호화 — 원문 토큰을 DB/응답/로그에 남기지 않는다."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from packages.config.settings import env

CREDENTIAL_ENCRYPTION_KEY_ENV = "CREDENTIAL_ENCRYPTION_KEY"
TOKEN_PREFIX = "fernet:v1:"
DB_CREDENTIAL_REF_PREFIX = "db:"
AGENT_SEALED_PREFIX = "x25519-aesgcm:v1:"
AGENT_ENVELOPE_INFO = b"opsia-agent-envelope-v1"
AGENT_ENVELOPE_KEY_ID_BYTES = 16
AGENT_ENVELOPE_PUBLIC_KEY_BYTES = 32
AGENT_ENVELOPE_NONCE_BYTES = 12


class CredentialEncryptionError(RuntimeError):
    """자격증명 암호화 설정 또는 복호화 실패."""


def credential_ref(provider: str, scope: str) -> str:
    return f"db:{provider}:{scope}"


def parse_credential_ref(ref: str) -> tuple[str, str]:
    if not ref.startswith(DB_CREDENTIAL_REF_PREFIX):
        raise CredentialEncryptionError("unsupported credential_ref format")
    parts = ref.removeprefix(DB_CREDENTIAL_REF_PREFIX).split(":", 1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise CredentialEncryptionError("credential_ref must be db:<provider>:<scope>")
    return parts[0].strip(), parts[1].strip()


def encrypt_credential(value: str) -> str:
    if not value:
        raise CredentialEncryptionError("저장할 자격증명이 비어 있습니다.")
    token = fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return f"{TOKEN_PREFIX}{token}"


def decrypt_credential(value: str) -> str:
    if not value.startswith(TOKEN_PREFIX):
        raise CredentialEncryptionError("지원하지 않는 자격증명 암호문 형식입니다.")
    token = value.removeprefix(TOKEN_PREFIX).encode("ascii")
    try:
        return fernet().decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialEncryptionError("자격증명 복호화에 실패했습니다.") from exc


def generate_agent_envelope_keypair() -> tuple[str, str]:
    """Return base64 public/private X25519 keys for one registered target agent."""
    private_key = X25519PrivateKey.generate()
    public_key = private_key.public_key()
    return _encode_raw_key(public_key.public_bytes_raw()), _encode_raw_key(
        private_key.private_bytes_raw()
    )


def agent_envelope_public_key(private_key: str) -> str:
    return _encode_raw_key(_load_private_key(private_key).public_key().public_bytes_raw())


def agent_envelope_key_id(public_key: str) -> str:
    return _agent_envelope_key_id(_load_public_key(public_key).public_bytes_raw()).decode("ascii")


def agent_envelope_context(
    workspace_id: str,
    cluster_id: str,
    revision: str,
    operation_id: str,
    address: str,
) -> str:
    values = (workspace_id, cluster_id, revision, operation_id, address)
    if any(not value or "\0" in value for value in values):
        raise CredentialEncryptionError("agent payload sealing context is unavailable")
    return "\0".join(("v1", *values))


def seal_agent_payload(payload: dict[str, Any], public_key: str, context: str) -> str:
    if not public_key or not context:
        raise CredentialEncryptionError("agent payload sealing identity is unavailable")
    recipient = _load_public_key(public_key)
    ephemeral_private = X25519PrivateKey.generate()
    ephemeral_public = ephemeral_private.public_key().public_bytes_raw()
    recipient_public = recipient.public_bytes_raw()
    key_id = _agent_envelope_key_id(recipient_public)
    nonce = os.urandom(AGENT_ENVELOPE_NONCE_BYTES)
    plaintext = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode()
    aad = _agent_envelope_aad(context, key_id)
    ciphertext = AESGCM(
        _agent_sealing_key(ephemeral_private.exchange(recipient), context, key_id)
    ).encrypt(
        nonce,
        plaintext,
        aad,
    )
    packed = key_id + ephemeral_public + nonce + ciphertext
    return AGENT_SEALED_PREFIX + base64.urlsafe_b64encode(packed).decode("ascii")


def open_agent_payload(value: str, private_key: str, context: str) -> dict[str, Any]:
    if not value.startswith(AGENT_SEALED_PREFIX) or not private_key or not context:
        raise CredentialEncryptionError("agent payload envelope is unavailable")
    try:
        packed = base64.urlsafe_b64decode(value.removeprefix(AGENT_SEALED_PREFIX).encode("ascii"))
        recipient = _load_private_key(private_key)
        recipient_public = recipient.public_key().public_bytes_raw()
        key_id = packed[:AGENT_ENVELOPE_KEY_ID_BYTES]
        if key_id != _agent_envelope_key_id(recipient_public):
            raise ValueError("agent envelope recipient key does not match")
        public_start = AGENT_ENVELOPE_KEY_ID_BYTES
        nonce_start = public_start + AGENT_ENVELOPE_PUBLIC_KEY_BYTES
        ciphertext_start = nonce_start + AGENT_ENVELOPE_NONCE_BYTES
        if len(packed) <= ciphertext_start:
            raise ValueError("agent envelope is truncated")
        ephemeral = X25519PublicKey.from_public_bytes(packed[public_start:nonce_start])
        shared_secret = recipient.exchange(ephemeral)
        plaintext = AESGCM(_agent_sealing_key(shared_secret, context, key_id)).decrypt(
            packed[nonce_start:ciphertext_start],
            packed[ciphertext_start:],
            _agent_envelope_aad(context, key_id),
        )
        payload = json.loads(plaintext)
    except Exception as exc:
        raise CredentialEncryptionError("agent payload envelope is invalid") from exc
    if not isinstance(payload, dict):
        raise CredentialEncryptionError("agent payload envelope is invalid")
    return payload


def _agent_sealing_key(shared_secret: bytes, context: str, key_id: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=key_id,
        info=AGENT_ENVELOPE_INFO + b"\0" + context.encode(),
    ).derive(shared_secret)


def _agent_envelope_key_id(public_key: bytes) -> bytes:
    return hashlib.sha256(public_key).hexdigest()[:AGENT_ENVELOPE_KEY_ID_BYTES].encode("ascii")


def _agent_envelope_aad(context: str, key_id: bytes) -> bytes:
    return AGENT_ENVELOPE_INFO + b"\0" + context.encode() + b"\0" + key_id


def _encode_raw_key(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _decode_raw_key(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except Exception as exc:
        raise CredentialEncryptionError("agent envelope key is invalid") from exc
    if len(decoded) != AGENT_ENVELOPE_PUBLIC_KEY_BYTES:
        raise CredentialEncryptionError("agent envelope key is invalid")
    return decoded


def _load_public_key(value: str) -> X25519PublicKey:
    try:
        return X25519PublicKey.from_public_bytes(_decode_raw_key(value))
    except Exception as exc:
        raise CredentialEncryptionError("agent envelope public key is invalid") from exc


def _load_private_key(value: str) -> X25519PrivateKey:
    try:
        return X25519PrivateKey.from_private_bytes(_decode_raw_key(value))
    except Exception as exc:
        raise CredentialEncryptionError("agent envelope private key is invalid") from exc


def fernet() -> Fernet:
    configured = env(CREDENTIAL_ENCRYPTION_KEY_ENV, "").strip()
    if not configured:
        raise CredentialEncryptionError(
            f"{CREDENTIAL_ENCRYPTION_KEY_ENV}가 설정되지 않아 자격증명을 저장할 수 없습니다."
        )
    return Fernet(_fernet_key(configured))


def _fernet_key(value: str) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception:
        decoded = b""
    if len(decoded) == 32:
        return value.encode("ascii")
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)
