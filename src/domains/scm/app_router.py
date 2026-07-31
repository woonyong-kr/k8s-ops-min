"""GitHub App 연동 엔드포인트.

- config       : App 구성 여부(프론트가 PAT/App 분기 판단)
- install-url  : 설치 리다이렉트 URL(state 로 진행 중 연결과 연동)
- verify       : installation_id 가 실제로 그 레포에 설치됐는지 + 권한 확인
                 (주소 불일치·권한 부족을 등록 시점에 걸러낸다)

App 미구성 시 config 는 configured=False, 그 외는 409 로 degrade 하여
기존 PAT 흐름을 깨지 않는다.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from domains.identity.dependencies import require_session
from domains.scm.github_app import GithubAppClient, GithubAppNotConfigured
from domains.scm.github_app_manifest import (
    APP_CONFIG_PROVIDER,
    APP_CONFIG_SCOPE,
    build_app_manifest,
    convert_manifest_code,
    new_app_action_url,
    resolve_github_app_config,
    store_app_config_from_conversion,
)
from packages.config.settings import env
from packages.contracts.gateway.routes import (
    GITHUB_APP_CALLBACK_PATH,
    GITHUB_APP_CONFIG_PATH,
    GITHUB_APP_INSTALL_URL_PATH,
    GITHUB_APP_MANIFEST_CALLBACK_PATH,
    GITHUB_APP_MANIFEST_PATH,
    GITHUB_APP_VERIFY_PATH,
)
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, ServiceRole
from packages.runtime.dependencies import get_db

router = APIRouter()

# 설치 완료 후 GitHub 이 브라우저를 되돌려보낼 프론트 위저드 URL.
# 프로덕션은 동일 오리진("/"), 로컬 dev 는 http://localhost:5173/ 등으로 지정.
GITHUB_APP_WEB_RETURN_URL_ENV = "GITHUB_APP_WEB_RETURN_URL"


class GithubAppConfigResponse(BaseModel):
    configured: bool
    slug: str | None
    install_available: bool


class InstallUrlResponse(BaseModel):
    url: str


class VerifyRequest(BaseModel):
    repo_ref: str


class VerifyResponse(BaseModel):
    installation_id: str
    matches: bool
    write_capable: bool
    repositories: list[str]
    permissions: dict[str, str]
    repository_selection: str | None


def _normalize(repo_ref: str) -> str:
    text = repo_ref.strip().lower()
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    if text.endswith(".git"):
        text = text[: -len(".git")]
    return text.strip("/")


@router.get(GITHUB_APP_CONFIG_PATH, response_model=GithubAppConfigResponse)
async def github_app_config(
    db: Any = Depends(get_db),
) -> GithubAppConfigResponse:
    cfg = resolve_github_app_config(db, DEFAULT_WORKSPACE_ID)
    return GithubAppConfigResponse(
        configured=cfg.configured,
        slug=cfg.slug or None,
        install_available=bool(cfg.configured and cfg.slug),
    )


class GithubAppUninstallResponse(BaseModel):
    removed: bool
    env_fallback_active: bool


@router.delete(GITHUB_APP_CONFIG_PATH, response_model=GithubAppUninstallResponse)
async def github_app_uninstall(
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> GithubAppUninstallResponse:
    """저장된 GitHub App 구성(개인키·웹훅시크릿 등)을 서버에서 제거한다(운영자 오프보딩).

    등록한 담당자가 이탈하거나 소유권이 바뀔 때, DB 에 남은 App 자격증명을 지워
    더는 그 App 으로 설치 토큰을 발급하지 못하게 한다(관리자 전용).

    참고:
      - GitHub 상의 App/설치 자체 삭제는 GitHub 설정에서 별도로 해야 한다(여기선
        서버 보관본만 제거). 프론트가 그 안내를 함께 노출한다.
      - env(GITHUB_APP_*) 로 구성된 폴백이 남아 있으면 ``env_fallback_active`` 로
        알려, '지운 줄 알았는데 여전히 동작'하는 고아 인식을 막는다.
    """
    if ServiceRole.SERVICE_ADMIN.value not in tuple(getattr(current, "roles", ()) or ()):
        raise HTTPException(status_code=403, detail="service_admin_required")
    deleter = getattr(db, "delete_workspace_credential", None)
    removed = (
        bool(deleter(DEFAULT_WORKSPACE_ID, APP_CONFIG_PROVIDER, APP_CONFIG_SCOPE))
        if callable(deleter)
        else False
    )
    # DB 구성을 지운 뒤에도 env 폴백이 살아있으면 여전히 configured 로 보인다.
    from domains.scm.github_app import load_github_app_config

    env_fallback_active = load_github_app_config().configured
    return GithubAppUninstallResponse(removed=removed, env_fallback_active=env_fallback_active)


@router.get(GITHUB_APP_INSTALL_URL_PATH, response_model=InstallUrlResponse)
async def github_app_install_url(
    state: str | None = None,
    db: Any = Depends(get_db),
    current: Any = Depends(require_session),
) -> InstallUrlResponse:
    cfg = resolve_github_app_config(db, DEFAULT_WORKSPACE_ID)
    try:
        return InstallUrlResponse(url=GithubAppClient(cfg).install_url(state=state))
    except GithubAppNotConfigured as exc:
        raise HTTPException(status_code=409, detail="github_app_not_configured") from exc


class AppManifestResponse(BaseModel):
    action_url: str
    manifest: dict[str, Any]


def _public_base_url(request: Request, explicit: str | None) -> str:
    """GitHub 이 웹훅·콜백을 보낼 공개 백엔드 주소.

    명시값 > PUBLIC_BASE_URL env > 요청 base_url(배포 뒤 자기 도메인) 순.
    배포본은 자기 도메인을 알기에 운영자가 입력할 필요가 없다.
    """
    if explicit and explicit.strip():
        return explicit.strip().rstrip("/")
    configured = env("PUBLIC_BASE_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


@router.get(GITHUB_APP_MANIFEST_PATH, response_model=AppManifestResponse)
async def github_app_manifest(
    request: Request,
    state: str,
    base_url: str | None = None,
    org: str | None = None,
    name: str | None = None,
) -> AppManifestResponse:
    """운영자 1회 등록용 manifest + GitHub 생성 URL.

    프론트가 이 manifest 를 GitHub 새 App 페이지로 폼 POST 하면 값이 미리 채워진다.
    base_url 은 서버가 자기 공개 주소로 자동 채운다(운영자 입력 불필요).
    """
    base = _public_base_url(request, base_url)
    if not base:
        raise HTTPException(status_code=400, detail="public_base_url_unresolved")
    return AppManifestResponse(
        action_url=new_app_action_url(org=org, state=state),
        manifest=build_app_manifest(base_url=base, name=name),
    )


@router.get(GITHUB_APP_MANIFEST_CALLBACK_PATH)
async def github_app_manifest_callback(
    code: str | None = None,
    state: str | None = None,
    db: Any = Depends(get_db),
) -> RedirectResponse:
    """GitHub 이 App 을 만든 뒤 돌려주는 code 를 자격증명으로 교환·저장한다.

    성공하면 App 이 즉시 configured 가 되어(재기동 불필요) 프론트로 결과를 알린다.
    """
    return_base = env(GITHUB_APP_WEB_RETURN_URL_ENV, "/").strip() or "/"
    outcome = "error"
    if code:
        try:
            conversion = await convert_manifest_code(code)
            store_app_config_from_conversion(db, DEFAULT_WORKSPACE_ID, conversion)
            outcome = "created"
        except Exception:  # noqa: BLE001 - 실패는 프론트에 error 로 전달
            outcome = "error"
    params = {"github_app_manifest": outcome}
    if state:
        params["github_app_state"] = state
    sep = "&" if "?" in return_base else "?"
    return RedirectResponse(url=f"{return_base}{sep}{urlencode(params)}", status_code=302)


@router.get(GITHUB_APP_CALLBACK_PATH)
async def github_app_callback(
    installation_id: str | None = None,
    setup_action: str | None = None,
    state: str | None = None,
) -> RedirectResponse:
    """GitHub 설치/승인 후 브라우저 복귀 지점.

    installation_id·state 를 프론트 위저드로 그대로 넘긴다(state 는 프론트가
    자신이 발급·저장한 값과 대조해 CSRF 를 막는다). 위저드가 이어서 verify 를
    호출한다. 자격증명 결속은 verify 통과 후 연결 확정 시점에 이뤄지므로
    콜백에서는 DB 를 건드리지 않는다(부작용 없음).
    """
    return_base = env(GITHUB_APP_WEB_RETURN_URL_ENV, "/").strip() or "/"
    params: dict[str, str] = {}
    if installation_id:
        params["github_app_installation_id"] = installation_id
    if setup_action:
        params["github_app_setup_action"] = setup_action
    if state:
        params["github_app_state"] = state
    if params:
        sep = "&" if "?" in return_base else "?"
        target = f"{return_base}{sep}{urlencode(params)}"
    else:
        target = return_base
    return RedirectResponse(url=target, status_code=302)


@router.post(GITHUB_APP_VERIFY_PATH, response_model=VerifyResponse)
async def github_app_verify_installation(
    installation_id: str,
    payload: VerifyRequest,
    db: Any = Depends(get_db),
    current: Any = Depends(require_session),
) -> VerifyResponse:
    """설치가 그 레포에 실제로 걸려 있고 PR 쓰기 권한이 있는지 등록 시점에 검증한다."""
    cfg = resolve_github_app_config(db, DEFAULT_WORKSPACE_ID)
    if not cfg.configured:
        raise HTTPException(status_code=409, detail="github_app_not_configured")
    client = GithubAppClient(cfg)
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            minted = await client.mint_installation_token(installation_id, client=http)
            repos = await client.list_installation_repositories(
                installation_id, client=http, token=minted["token"]
            )
    except GithubAppNotConfigured as exc:
        raise HTTPException(status_code=409, detail="github_app_not_configured") from exc
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (401, 403):
            raise HTTPException(status_code=409, detail="github_app_installation_invalid") from exc
        if code == 404:
            raise HTTPException(status_code=404, detail="github_app_installation_not_found") from exc
        raise HTTPException(status_code=502, detail="github_app_upstream_error") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="github_app_upstream_error") from exc

    full_names = [str(r.get("full_name", "")) for r in repos if r.get("full_name")]
    target = _normalize(payload.repo_ref)
    matches = any(_normalize(name) == target for name in full_names)
    permissions = {str(k): str(v) for k, v in (minted.get("permissions") or {}).items()}
    # PR 쓰기 = contents:write + pull_requests:write
    write_capable = (
        permissions.get("contents") == "write" and permissions.get("pull_requests") == "write"
    )
    return VerifyResponse(
        installation_id=installation_id,
        matches=matches,
        write_capable=write_capable,
        repositories=full_names,
        permissions=permissions,
        repository_selection=minted.get("repository_selection"),
    )
