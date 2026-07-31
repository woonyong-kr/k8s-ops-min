"""관리 콘솔 라우터 — 조직·그룹·멤버·리소스 권한 조회/편집(admin 세션).

프론트 웹 콘솔(docs/fd) 전용 경계. 기존 identity 저장소 메서드에 HTTP 표면만 부여.
쓰기는 admin 세션 필요, workspace 는 세션 신원에서 유도(클라이언트 입력 금지).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import Field

from domains.identity.dependencies import require_admin_session
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.base import StrictModel
from packages.runtime.dependencies import get_db

router = APIRouter()
HTTP_CONFLICT = 409


class OrgCreateRequest(StrictModel):
    name: str = Field(min_length=3, max_length=40)
    description: str = Field(default="", max_length=200)


class GroupCreateRequest(StrictModel):
    org_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)


class AccessGrantRequest(StrictModel):
    subject_type: str = Field(pattern=r"^(user|group)$")
    subject_id: str = Field(min_length=1)
    subject_label: str | None = None
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    role: str = Field(min_length=1)


class OrgListResponse(StrictModel):
    orgs: list[dict[str, Any]]


class OrgResponse(StrictModel):
    org_id: str
    name: str
    description: str
    member_count: int
    group_count: int
    created_at: str | None = None


class GroupListResponse(StrictModel):
    groups: list[dict[str, Any]]


class GroupResponse(StrictModel):
    group_id: str
    org_id: str
    name: str
    member_count: int


class GroupMembersResponse(StrictModel):
    members: list[dict[str, Any]]


class UserListResponse(StrictModel):
    users: list[dict[str, Any]]


class AccessListResponse(StrictModel):
    grants: list[dict[str, Any]]


class AccessGrantResponse(StrictModel):
    access_id: str
    subject_id: str | None = None
    subject_type: str
    subject_label: str
    resource_type: str
    resource_id: str
    role: str
    granted_at: str | None = None


class AcceptedResponse(StrictModel):
    accepted: bool = True


@router.get(gateway_routes.ORGS_PATH, response_model=OrgListResponse)
async def list_orgs(
    _current: Any = Depends(require_admin_session), db: Any = Depends(get_db)
) -> OrgListResponse:
    return OrgListResponse(orgs=db.list_organizations())


@router.post(gateway_routes.ORGS_PATH, response_model=OrgResponse)
async def create_org(
    payload: OrgCreateRequest,
    _current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> OrgResponse:
    return OrgResponse(**db.create_organization(payload.name, payload.description))


@router.delete(gateway_routes.ORG_PATH, status_code=204)
async def delete_org(
    org_id: str,
    response: Response,
    _current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> Response:
    if not db.delete_organization(org_id):
        raise HTTPException(status_code=HTTP_CONFLICT, detail="groups_exist")
    response.status_code = 204
    return response


@router.get(gateway_routes.GROUPS_PATH, response_model=GroupListResponse)
async def list_groups(
    org_id: str | None = None,
    _current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> GroupListResponse:
    return GroupListResponse(groups=db.list_groups(org_id))


@router.post(gateway_routes.GROUPS_PATH, response_model=GroupResponse)
async def create_group(
    payload: GroupCreateRequest,
    _current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> GroupResponse:
    return GroupResponse(**db.create_group(payload.org_id, payload.name))


@router.get(gateway_routes.GROUP_MEMBERS_PATH, response_model=GroupMembersResponse)
async def list_group_members(
    group_id: str,
    _current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> GroupMembersResponse:
    return GroupMembersResponse(members=db.list_group_members(group_id))


@router.put(gateway_routes.GROUP_MEMBER_PATH, response_model=AcceptedResponse)
async def add_group_member(
    group_id: str,
    user_id: str,
    _current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> AcceptedResponse:
    db.add_group_member(group_id, user_id)
    return AcceptedResponse()


@router.delete(gateway_routes.GROUP_MEMBER_PATH, status_code=204)
async def remove_group_member(
    group_id: str,
    user_id: str,
    response: Response,
    _current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> Response:
    checker = getattr(db, "is_last_active_service_admin", None)
    if callable(checker) and checker(user_id):
        raise HTTPException(
            status_code=400,
            detail={"code": "last_admin", "detail": "마지막 관리자는 제거할 수 없습니다."},
        )
    db.remove_group_member(group_id, user_id)
    response.status_code = 204
    return response


@router.get(gateway_routes.USERS_PATH, response_model=UserListResponse)
async def list_users(
    status: str | None = None,
    _current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> UserListResponse:
    return UserListResponse(users=db.list_users(status))


@router.get(gateway_routes.ACCESS_PATH, response_model=AccessListResponse)
async def list_access(
    resource_id: str | None = None,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> AccessListResponse:
    return AccessListResponse(
        grants=db.list_access_grants(
            organization_id=current.workspace_id,
            resource_id=resource_id,
        )
    )


@router.post(gateway_routes.ACCESS_PATH, response_model=AccessGrantResponse)
async def grant_access(
    payload: AccessGrantRequest,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> AccessGrantResponse:
    workspace_id = current.workspace_id
    db.grant_resource_access(
        {
            "subject_id": payload.subject_id,
            "resource_type": payload.resource_type,
            "resource_id": payload.resource_id,
            "role": payload.role,
            "organization_id": workspace_id,
        }
    )
    # 방금 부여한 grant 를 조회로 되돌려줌(access_id 확정).
    # subject_id 까지 비교해야 같은 resource+role 의 다른 사용자 grant 와 재매칭되지 않음.
    grants = db.list_access_grants(
        organization_id=workspace_id,
        resource_id=payload.resource_id,
    )
    matches = [
        g
        for g in grants
        if g["resource_id"] == payload.resource_id
        and g["role"] == payload.role
        and g.get("subject_id") == payload.subject_id
    ]
    # created_at 오름차순 조회이므로 마지막 항목이 방금 부여한 grant.
    match = matches[-1] if matches else None
    if match is None:
        return AccessGrantResponse(
            access_id="pending",
            subject_id=payload.subject_id,
            subject_type=payload.subject_type,
            subject_label=payload.subject_label or payload.subject_id,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            role=payload.role,
        )
    return AccessGrantResponse(**{**match, "subject_type": payload.subject_type})


@router.delete(gateway_routes.ACCESS_ITEM_PATH, status_code=204)
async def revoke_access(
    access_id: str,
    response: Response,
    _current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> Response:
    db.revoke_access(access_id)
    response.status_code = 204
    return response
