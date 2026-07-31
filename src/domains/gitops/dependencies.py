"""gitops 인가 가드 — GitHub webhook HMAC-SHA256 서명 검증(fail-closed)."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from fastapi import Depends, HTTPException, Request

from packages.config.settings import env
from packages.contracts.identity import DEFAULT_WORKSPACE_ID
from packages.runtime.dependencies import get_db

WEBHOOK_SECRET_ENV = "GITHUB_WEBHOOK_SECRET"
SIGNATURE_HEADER = "x-hub-signature-256"
SIGNATURE_PREFIX = "sha256="


def _candidate_secrets(db: Any) -> list[str]:
    """검증에 허용할 웹훅 시크릿 후보들.

    - 정적 env 시크릿(GITHUB_WEBHOOK_SECRET): 기존 폴러/PAT 흐름(하위호환, 최우선).
    - App(매니페스트로 저장된) 웹훅 시크릿: App 설치가 보내는 웹훅(installation·
      repository·push 등)을 받기 위한 additive 후보. env 만 설정된 배포에선 무영향.
    시크릿 하나만 맞아도 통과하므로 기존 동작을 깨지 않는다.
    """
    candidates: list[str] = []
    env_secret = env(WEBHOOK_SECRET_ENV, "")
    if env_secret:
        candidates.append(env_secret)
    try:
        from domains.scm.github_app_manifest import resolve_webhook_secret

        app_secret = resolve_webhook_secret(db, DEFAULT_WORKSPACE_ID)
    except Exception:  # noqa: BLE001 - App 시크릿 조회 실패는 env 후보로 degrade
        app_secret = ""
    if app_secret and app_secret not in candidates:
        candidates.append(app_secret)
    return candidates


async def verify_github_signature(request: Request, db: Any = Depends(get_db)) -> None:
    """시크릿 미설정 또는 서명 불일치/누락이면 거부. 외부 입력이 파이프라인을 못 열게.

    정적 env 시크릿과 App 저장 시크릿 중 **하나라도** 일치하면 통과한다(둘 다
    HMAC-SHA256, 상수시간 비교). 이는 기존 흐름을 유지한 채 App 설치 웹훅을
    추가로 수용하기 위한 것이다.
    """
    candidates = _candidate_secrets(db)
    if not candidates:
        raise HTTPException(status_code=503, detail="webhook secret not configured")
    body = await request.body()
    supplied = request.headers.get(SIGNATURE_HEADER, "")
    for secret in candidates:
        expected = SIGNATURE_PREFIX + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(supplied, expected):
            return
    raise HTTPException(status_code=401, detail="invalid webhook signature")
