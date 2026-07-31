"""identity 인가 가드(필터) — Depends 로 라우터/라우트에 선언적으로 적용."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import HTTPException, Request

from packages.contracts.identity import (
    AccessResourceType,
    ResourceAccessRequest,
    ServiceRole,
)
from packages.events.context import set_event_workspace

AGENT_TOKEN_HEADER = "x-agent-token"
AGENT_AUTH_REQUIRED_MESSAGE = "agent authentication required"
ADMIN_AUTH_REQUIRED_MESSAGE = "service admin role required"
RESOURCE_ACCESS_DENIED_MESSAGE = "resource access denied"


def hash_agent_token(token: str) -> str:
    """agent 토큰 → SHA-256 hex. 원문은 저장하지 않고 이 해시만 저장/비교함."""
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class ClusterAgentIdentity:
    """토큰으로 인증된 agent 의 권위 신원 — 요청 body 가 아닌 등록 레지스트리 기준."""

    workspace_id: str
    cluster_id: str


@dataclass(frozen=True)
class ResourceAccessContext:
    db: Any
    current: Any
    request: ResourceAccessRequest


class ResourceAccessFilter(Protocol):
    def authorize(self, context: ResourceAccessContext) -> bool: ...


class StructuredResourceAccessFilter:
    """can_access 저장소 API 기반 리소스 인가 평가."""

    def authorize(self, context: ResourceAccessContext) -> bool:
        can_access = getattr(context.db, "can_access", None)
        if callable(can_access):
            return bool(
                can_access(
                    context.request.user_id,
                    context.request.organization_id,
                    context.request.resource_type,
                    context.request.resource_id,
                    context.request.permission,
                )
            )
        user_has_resource_access = getattr(context.db, "user_has_resource_access", None)
        if callable(user_has_resource_access):
            return bool(
                user_has_resource_access(
                    context.request.user_id,
                    context.request.organization_id,
                    context.request.resource_type,
                    context.request.resource_id,
                    context.request.permission,
                )
            )
        return False


@dataclass(frozen=True)
class ResourceAccessFilterChain:
    filters: tuple[ResourceAccessFilter, ...]

    def authorize(self, context: ResourceAccessContext) -> bool:
        return bool(self.filters) and all(
            access_filter.authorize(context) for access_filter in self.filters
        )


DEFAULT_RESOURCE_ACCESS_FILTER_CHAIN = ResourceAccessFilterChain(
    filters=(StructuredResourceAccessFilter(),)
)


def get_password_auth(request: Request) -> Any:
    return request.app.state.password_auth


async def require_session(request: Request) -> Any:
    """사용자 세션 가드 — 유효 세션 필요(없으면 401)."""
    current = await request.app.state.auth.require_session(request)
    set_event_workspace(getattr(current, "workspace_id", None))
    return current


async def require_admin_session(request: Request) -> Any:
    """서비스 최고 관리자 세션 가드."""
    current = await require_session(request)
    if ServiceRole.SERVICE_ADMIN.value not in current.roles:
        raise HTTPException(status_code=403, detail=ADMIN_AUTH_REQUIRED_MESSAGE)
    return current


def require_resource_access(
    db: Any,
    current: Any,
    workspace_id: str,
    resource_type: str,
    resource_id: str,
    action: str,
    *,
    detail: str = RESOURCE_ACCESS_DENIED_MESSAGE,
    filter_chain: ResourceAccessFilterChain = DEFAULT_RESOURCE_ACCESS_FILTER_CHAIN,
) -> None:
    """사용자 세션이 특정 워크스페이스 리소스에 permission 권한을 가지는지 검사."""
    access_request = ResourceAccessRequest(
        user_id=current.user_id,
        organization_id=workspace_id,
        resource_type=resource_type,
        resource_id=resource_id,
        permission=action,
    )
    context = ResourceAccessContext(db=db, current=current, request=access_request)
    if not filter_chain.authorize(context):
        raise HTTPException(status_code=403, detail=detail)


def require_cluster_access(
    db: Any,
    current: Any,
    workspace_id: str,
    cluster_id: str,
    action: str,
    *,
    detail: str = RESOURCE_ACCESS_DENIED_MESSAGE,
) -> None:
    """cluster 리소스 권한 검사 shortcut — command/debug/approval/dashboard 공통 기준."""
    require_resource_access(
        db,
        current,
        workspace_id,
        AccessResourceType.CLUSTER.value,
        cluster_id,
        action,
        detail=detail,
    )


def resolve_allowed_cluster_ids(
    db: Any,
    current: Any,
    workspace_id: str,
    permission: str,
) -> set[str]:
    """Cluster 목록 인가를 구체적인 ID 집합으로 물질화한다.

    ``accessible_resource_ids``의 legacy 관리자 sentinel인 ``None``을 저장소
    wildcard로 전달하지 않는다. 서비스 관리자일 때만 workspace cluster를
    별도 조회하고, 그 외 ``None``/누락/빈 입력은 항상 빈 집합으로 닫는다.
    """
    user_id = getattr(current, "user_id", None)
    session_workspace_id = getattr(current, "workspace_id", None)
    if (
        not workspace_id
        or workspace_id != session_workspace_id
        or not isinstance(user_id, str)
        or not user_id
    ):
        return set()
    accessible = getattr(db, "accessible_resource_ids", None)
    if not callable(accessible):
        return set()

    cluster_ids = accessible(
        user_id,
        workspace_id,
        AccessResourceType.CLUSTER.value,
        permission,
    )
    if cluster_ids is not None:
        return {
            cluster_id.strip()
            for cluster_id in cluster_ids
            if isinstance(cluster_id, str) and cluster_id.strip()
        }

    roles = tuple(getattr(current, "roles", ()) or ())
    if ServiceRole.SERVICE_ADMIN.value not in roles:
        return set()
    list_cluster_ids = getattr(db, "list_workspace_cluster_ids", None)
    if not callable(list_cluster_ids):
        return set()
    return {
        cluster_id.strip()
        for cluster_id in list_cluster_ids(workspace_id)
        if isinstance(cluster_id, str) and cluster_id.strip()
    }


def resolve_allowed_application_ids(
    db: Any,
    current: Any,
    workspace_id: str,
    permission: str,
) -> set[str]:
    """Application 목록 인가도 concrete ID 집합으로 물질화해 wildcard를 차단한다."""
    user_id = getattr(current, "user_id", None)
    session_workspace_id = getattr(current, "workspace_id", None)
    if (
        not workspace_id
        or workspace_id != session_workspace_id
        or not isinstance(user_id, str)
        or not user_id
    ):
        return set()
    accessible = getattr(db, "accessible_resource_ids", None)
    if not callable(accessible):
        return set()
    application_ids = accessible(
        user_id,
        workspace_id,
        AccessResourceType.APPLICATION.value,
        permission,
    )
    if application_ids is not None:
        return {
            application_id.strip()
            for application_id in application_ids
            if isinstance(application_id, str) and application_id.strip()
        }
    roles = tuple(getattr(current, "roles", ()) or ())
    if ServiceRole.SERVICE_ADMIN.value not in roles:
        return set()
    list_application_ids = getattr(db, "list_workspace_application_ids", None)
    if not callable(list_application_ids):
        return set()
    return {
        application_id.strip()
        for application_id in list_application_ids(workspace_id)
        if isinstance(application_id, str) and application_id.strip()
    }


async def require_cluster_agent(request: Request) -> ClusterAgentIdentity:
    """per-cluster agent 토큰 가드 — fail-closed.

    x-agent-token 을 해시해 등록 레지스트리에서 클러스터를 찾고, 그 클러스터의
    권위 (workspace_id, cluster_id) 를 돌려줌. agent 라우트는 이 값을 쓰고
    요청 body 의 workspace_id/cluster_id 는 신뢰하지 않음(크로스 테넌트 차단).
    토큰 없음/미등록/미인증(해시 불일치)은 모두 401.
    """
    db = request.app.state.db
    token = request.headers.get(AGENT_TOKEN_HEADER, "")
    if token:
        identity = await asyncio.to_thread(
            db.authenticate_cluster_agent,
            hash_agent_token(token),
        )
        if identity is not None:
            authenticated = ClusterAgentIdentity(
                workspace_id=identity["workspace_id"],
                cluster_id=identity["cluster_id"],
            )
            set_event_workspace(authenticated.workspace_id)
            return authenticated
    raise HTTPException(status_code=401, detail=AGENT_AUTH_REQUIRED_MESSAGE)
