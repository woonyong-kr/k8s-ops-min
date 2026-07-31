"""Session-protected repository discovery endpoints for registration UX."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from domains.gitops.repository import derive_repository_id, repository_credential_scope
from domains.gitops.repository_discovery import (
    GitHubRepositoryClient,
    RepositoryDiscoveryError,
    RepositoryDiscoveryService,
    normalize_github_repo_ref,
)
from domains.identity.dependencies import require_session, resolve_allowed_cluster_ids
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.requests import (
    RepositoryManifestDiscoveryRequest,
    RepositoryManifestValidationRequest,
    RepositoryProbeRequest,
)
from packages.contracts.gateway.responses import (
    RepositoryBranchListResponse,
    RepositoryManifestCandidateListResponse,
    RepositoryManifestValidationResponse,
    RepositoryProbeResponse,
)
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, Permission
from packages.runtime.dependencies import get_db
from packages.security.credentials import (
    CredentialEncryptionError,
    credential_ref,
    decrypt_credential,
    encrypt_credential,
)
from packages.storage.engine import unit_of_work_or_null

router = APIRouter()


def discovery_service() -> RepositoryDiscoveryService:
    # Session-scoped discovery must never borrow the process-wide GitHub token.
    return RepositoryDiscoveryService(GitHubRepositoryClient(token=None))


async def wizard_discovery_service(
    db: Any,
    current: Any,
    repo_ref: str,
    fallback: RepositoryDiscoveryService,
    *,
    request_token: str | None = None,
) -> RepositoryDiscoveryService:
    """Use only a one-request token or this workspace/repository's encrypted credential."""
    normalized = normalize_github_repo_ref(repo_ref)
    if request_token is not None:
        return RepositoryDiscoveryService(
            GitHubRepositoryClient(token=request_token),
            render_executor=fallback.render_executor,
        )
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    get_repository = getattr(db, "get_repository_by_ref", None)
    existing_repository = (
        get_repository(workspace_id, normalized) if callable(get_repository) else None
    )
    repository_id = str(
        (existing_repository or {}).get("repository_id")
        or derive_repository_id({"workspace_id": workspace_id, "repo_ref": normalized})
    )
    stored_ref = str((existing_repository or {}).get("credential_ref") or "").strip()
    if stored_ref:
        from domains.scm.github_app_credentials import (
            is_app_installation_ref,
            parse_app_installation_ref,
            resolve_installation_token,
        )

        if is_app_installation_ref(stored_ref):
            installation_id = parse_app_installation_ref(stored_ref)
            try:
                token = await resolve_installation_token(
                    db,
                    str(workspace_id),
                    installation_id,
                )
            except Exception as exc:
                raise RepositoryDiscoveryError(422, "credential_unavailable") from exc
            if not token:
                raise RepositoryDiscoveryError(422, "credential_unavailable")
            return RepositoryDiscoveryService(
                GitHubRepositoryClient(token=token),
                render_executor=fallback.render_executor,
            )
    scope = repository_credential_scope(repository_id)
    get_credential = getattr(db, "get_workspace_credential", None)
    stored = get_credential(workspace_id, "github", scope) if callable(get_credential) else None
    if stored is None:
        return fallback
    try:
        if (
            str(stored.get("workspace_id") or "") != workspace_id
            or str(stored.get("provider") or "") != "github"
            or str(stored.get("scope") or "") != scope
        ):
            raise CredentialEncryptionError("credential scope mismatch")
        token = decrypt_credential(str(stored.get("encrypted_value") or ""))
    except CredentialEncryptionError as exc:
        raise RepositoryDiscoveryError(422, "credential_unavailable") from exc
    return RepositoryDiscoveryService(
        GitHubRepositoryClient(token=token),
        render_executor=fallback.render_executor,
    )


def discovery_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RepositoryDiscoveryError):
        return HTTPException(status_code=exc.status_code, detail=exc.detail)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=502, detail="repository discovery failed")


def require_repository_discovery_access(db: Any, current: Any) -> str:
    """Require a concrete deploy-capable target before repository contents can be read."""

    workspace_id = str(getattr(current, "workspace_id", "") or "")
    allowed_clusters = resolve_allowed_cluster_ids(
        db,
        current,
        workspace_id,
        Permission.DEPLOY_RUN.value,
    )
    if not allowed_clusters:
        raise HTTPException(status_code=403, detail="resource access denied")
    return workspace_id


async def resolve_wizard_token(
    db: Any,
    *,
    request_token: str | None,
    installation_id: str | None,
) -> str | None:
    """위저드 discovery 호출에 쓸 토큰. 사용자 토큰이 있으면 그걸, 없고 App 설치
    id 가 있으면 설치 토큰을 발급해 반환한다(비공개/rate-limit 레포를 인증 조회).

    App 미구성·발급 실패는 None 으로 degrade(무인증 흐름 유지, 크래시 금지).
    """
    if request_token is not None:
        return request_token
    if installation_id:
        from domains.scm.github_app import GithubAppNotConfigured
        from domains.scm.github_app_credentials import resolve_installation_token

        try:
            token = await resolve_installation_token(db, DEFAULT_WORKSPACE_ID, installation_id)
            return token or None
        except GithubAppNotConfigured:
            return None
        except Exception:  # noqa: BLE001 - 네트워크·발급 실패는 무인증 degrade
            return None
    return None


def store_github_token(
    db: Any,
    workspace_id: str,
    repository_id: str,
    token: str,
) -> str:
    encrypted = encrypt_credential(token)
    scope = repository_credential_scope(repository_id)
    ref = credential_ref("github", scope)
    with unit_of_work_or_null(db):
        locker = getattr(db, "lock_workspace_credential_scope", None)
        if callable(locker):
            locker(workspace_id, "github", scope)
        upsert = getattr(db, "upsert_workspace_credential", None)
        if not callable(upsert):
            raise CredentialEncryptionError("credential store unavailable")
        upsert(
            {
                "workspace_id": workspace_id,
                "provider": "github",
                "scope": scope,
                "encrypted_value": encrypted,
                "metadata": {"credential_ref": ref, "repository_id": repository_id},
            }
        )
    return ref


@router.post(
    gateway_routes.REPOSITORY_DISCOVERY_PROBE_PATH,
    response_model=RepositoryProbeResponse,
)
async def probe_repository(
    payload: RepositoryProbeRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    service: RepositoryDiscoveryService = Depends(discovery_service),
) -> RepositoryProbeResponse:
    workspace_id = require_repository_discovery_access(db, current)
    try:
        normalized = normalize_github_repo_ref(payload.repo_ref)
        token = payload.token.get_secret_value() if payload.token is not None else None
        effective_token = await resolve_wizard_token(
            db, request_token=token, installation_id=payload.installation_id
        )
        scoped_service = await wizard_discovery_service(
            db,
            current,
            normalized,
            service,
            request_token=effective_token,
        )
        response = await scoped_service.probe_repository(
            RepositoryProbeRequest(repo_ref=normalized)
        )
        # 사용자 PAT 만 저장한다(App 설치 토큰은 단명이라 저장 금지).
        if token is not None and response.reachable:
            get_repository = getattr(db, "get_repository_by_ref", None)
            existing_repository = (
                get_repository(workspace_id, normalized) if callable(get_repository) else None
            )
            repository_id = str(
                (existing_repository or {}).get("repository_id")
                or derive_repository_id({"workspace_id": workspace_id, "repo_ref": normalized})
            )
            store_github_token(db, workspace_id, repository_id, token)
        return response
    except CredentialEncryptionError as exc:
        raise HTTPException(status_code=422, detail="credential_unavailable") from exc
    except (RepositoryDiscoveryError, ValueError) as exc:
        raise discovery_http_error(exc) from exc


@router.get(
    gateway_routes.REPOSITORY_DISCOVERY_BRANCHES_PATH,
    response_model=RepositoryBranchListResponse,
)
async def list_repository_branches(
    repo_ref: str = Query(min_length=1, max_length=240),
    installation_id: str | None = Query(default=None, min_length=1, max_length=40),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    service: RepositoryDiscoveryService = Depends(discovery_service),
) -> RepositoryBranchListResponse:
    require_repository_discovery_access(db, current)
    try:
        effective_token = await resolve_wizard_token(
            db, request_token=None, installation_id=installation_id
        )
        scoped_service = await wizard_discovery_service(
            db,
            current,
            repo_ref,
            service,
            request_token=effective_token,
        )
        return await scoped_service.list_branches(repo_ref)
    except (RepositoryDiscoveryError, ValueError) as exc:
        raise discovery_http_error(exc) from exc


@router.post(
    gateway_routes.REPOSITORY_DISCOVERY_MANIFESTS_PATH,
    response_model=RepositoryManifestCandidateListResponse,
)
async def list_repository_manifest_candidates(
    payload: RepositoryManifestDiscoveryRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    service: RepositoryDiscoveryService = Depends(discovery_service),
) -> RepositoryManifestCandidateListResponse:
    require_repository_discovery_access(db, current)
    try:
        effective_token = await resolve_wizard_token(
            db, request_token=None, installation_id=payload.installation_id
        )
        scoped_service = await wizard_discovery_service(
            db,
            current,
            payload.repo_ref,
            service,
            request_token=effective_token,
        )
        return await scoped_service.list_manifest_candidates(payload.repo_ref, payload.branch)
    except (RepositoryDiscoveryError, ValueError) as exc:
        raise discovery_http_error(exc) from exc


@router.post(
    gateway_routes.REPOSITORY_DISCOVERY_VALIDATE_PATH,
    response_model=RepositoryManifestValidationResponse,
)
async def validate_repository_manifest(
    payload: RepositoryManifestValidationRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    service: RepositoryDiscoveryService = Depends(discovery_service),
) -> RepositoryManifestValidationResponse:
    require_repository_discovery_access(db, current)
    try:
        effective_token = await resolve_wizard_token(
            db, request_token=None, installation_id=payload.installation_id
        )
        scoped_service = await wizard_discovery_service(
            db,
            current,
            payload.repo_ref,
            service,
            request_token=effective_token,
        )
        return await scoped_service.validate_manifest(payload)
    except (RepositoryDiscoveryError, ValueError) as exc:
        raise discovery_http_error(exc) from exc
