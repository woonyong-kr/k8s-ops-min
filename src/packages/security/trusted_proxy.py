"""mTLS 개발 콘솔 프록시의 내부 신원 검증.

브라우저 인증서는 Cloudflare 엣지에서 검증한다. 엣지를 통과한 요청만 접근하는
전용 nginx가 이 모듈의 공유 비밀 헤더를 주입하며, API와 realtime gateway는
동일한 비교 로직으로 프록시 신원을 확인한다. 일반 콘솔은 이 헤더를 제거한다.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass

from packages.config.settings import env

TRUSTED_PROXY_AUTH_SECRET_ENV = "TRUSTED_PROXY_AUTH_SECRET"
TRUSTED_PROXY_AUTH_USER_ID_ENV = "TRUSTED_PROXY_AUTH_USER_ID"
TRUSTED_PROXY_AUTH_WORKSPACE_ID_ENV = "TRUSTED_PROXY_AUTH_WORKSPACE_ID"
TRUSTED_PROXY_AUTH_HEADER = "x-kubeheal-internal-auth"
TRUSTED_PROXY_SESSION_TOKEN = "mtls-dev-console"
MINIMUM_SECRET_LENGTH = 32


@dataclass(frozen=True)
class TrustedProxyIdentity:
    user_id: str
    workspace_id: str


def trusted_proxy_identity(headers: Mapping[str, str]) -> TrustedProxyIdentity | None:
    """올바른 내부 헤더가 있는 경우에만 설정된 프록시 신원을 반환한다."""
    configured_secret = env(TRUSTED_PROXY_AUTH_SECRET_ENV, "").strip()
    if not configured_secret:
        return None
    supplied_secret = headers.get(TRUSTED_PROXY_AUTH_HEADER, "").strip()
    if not supplied_secret or not secrets.compare_digest(configured_secret, supplied_secret):
        return None
    user_id = env(TRUSTED_PROXY_AUTH_USER_ID_ENV, "").strip()
    workspace_id = env(TRUSTED_PROXY_AUTH_WORKSPACE_ID_ENV, "").strip()
    if not user_id or not workspace_id:
        return None
    return TrustedProxyIdentity(user_id=user_id, workspace_id=workspace_id)


def assert_trusted_proxy_config_safe() -> None:
    """부분 설정이나 짧은 공유 비밀이 있으면 gateway 기동을 거부한다."""
    secret = env(TRUSTED_PROXY_AUTH_SECRET_ENV, "").strip()
    user_id = env(TRUSTED_PROXY_AUTH_USER_ID_ENV, "").strip()
    workspace_id = env(TRUSTED_PROXY_AUTH_WORKSPACE_ID_ENV, "").strip()
    configured = (bool(secret), bool(user_id), bool(workspace_id))
    if not any(configured):
        return
    if not all(configured):
        raise RuntimeError("mTLS 개발 프록시 설정은 secret, user, workspace가 모두 필요합니다")
    if len(secret) < MINIMUM_SECRET_LENGTH:
        raise RuntimeError("mTLS 개발 프록시 공유 비밀은 32자 이상이어야 합니다")
