"""App 설치 자격증명 해석 — credential_ref ↔ 단명 설치 토큰(캐시).

폴러·scm-worker 가 PR 을 쓸 때, 저장된 PAT 대신 App 설치 토큰을 발급해 쓰기
위한 핵심 로직이다. 이 모듈 자체는 기존 토큰 해석 경로를 건드리지 않는다
(배선은 상위에서 additive 분기로 붙인다). 설치 토큰은 1시간짜리라 프로세스
내 캐시로 재사용해 rate limit·지연을 줄인다.

credential_ref 포맷: ``github-app-installation:{installation_id}``
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx

from domains.scm.github_app import GithubAppClient
from domains.scm.github_app_manifest import resolve_github_app_config

APP_INSTALLATION_REF_PREFIX = "github-app-installation:"

# 만료 이 정도 전이면 새로 발급(clock skew·왕복 여유).
_EXPIRY_SKEW_SECONDS = 60.0
_FALLBACK_TTL_SECONDS = 3000.0  # expires_at 파싱 실패 시 보수적 50분

# 프로세스 내 캐시: installation_id -> (token, expiry_epoch)
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


def is_app_installation_ref(ref: str | None) -> bool:
    return bool(ref) and str(ref).startswith(APP_INSTALLATION_REF_PREFIX)


def make_app_installation_ref(installation_id: str) -> str:
    return f"{APP_INSTALLATION_REF_PREFIX}{installation_id}"


def parse_app_installation_ref(ref: str) -> str:
    if not is_app_installation_ref(ref):
        raise ValueError("not a github app installation ref")
    return ref[len(APP_INSTALLATION_REF_PREFIX) :].strip()


def _parse_expiry(expires_at: str, *, now: float) -> float:
    if not expires_at:
        return now + _FALLBACK_TTL_SECONDS
    try:
        return datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return now + _FALLBACK_TTL_SECONDS


def _cache_get(installation_id: str, now: float) -> str | None:
    hit = _TOKEN_CACHE.get(installation_id)
    if hit and (hit[1] - _EXPIRY_SKEW_SECONDS) > now:
        return hit[0]
    return None


def _cache_put(installation_id: str, token: str, expiry: float) -> None:
    _TOKEN_CACHE[installation_id] = (token, expiry)


def invalidate_installation_token(installation_id: str) -> None:
    """언인스톨·401 등으로 무효화됐을 때 캐시에서 제거."""
    _TOKEN_CACHE.pop(installation_id, None)


async def resolve_installation_token(
    db: Any,
    workspace_id: str,
    installation_id: str,
    *,
    now: float | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """설치 토큰을 캐시에서 재사용하거나 새로 발급해 반환한다.

    App 미구성이면 GithubAppNotConfigured 가 전파된다(상위에서 degrade).
    """
    ts = now if now is not None else time.time()
    cached = _cache_get(installation_id, ts)
    if cached:
        return cached
    cfg = resolve_github_app_config(db, workspace_id)
    minted = await GithubAppClient(cfg).mint_installation_token(installation_id, client=client)
    token = str(minted.get("token") or "")
    expiry = _parse_expiry(str(minted.get("expires_at") or ""), now=ts)
    if token:
        _cache_put(installation_id, token, expiry)
    return token


def resolve_installation_token_sync(
    db: Any,
    workspace_id: str,
    installation_id: str,
    *,
    now: float | None = None,
    client: httpx.Client | None = None,
) -> str:
    """``resolve_installation_token`` 의 동기 버전(폴러 등 동기 경로용).

    같은 프로세스 캐시(_TOKEN_CACHE)를 공유하므로 async(verify) 와 sync(poller) 가
    발급한 토큰을 서로 재사용한다. App 미구성이면 GithubAppNotConfigured 전파.
    """
    ts = now if now is not None else time.time()
    cached = _cache_get(installation_id, ts)
    if cached:
        return cached
    cfg = resolve_github_app_config(db, workspace_id)
    minted = GithubAppClient(cfg).mint_installation_token_sync(installation_id, client=client)
    token = str(minted.get("token") or "")
    expiry = _parse_expiry(str(minted.get("expires_at") or ""), now=ts)
    if token:
        _cache_put(installation_id, token, expiry)
    return token
