from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import and_, case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domains.identity.models import (
    ClusterRegistration,
    Group,
    GroupMember,
    MemberResourceRole,
    Organization,
    OrganizationMember,
    ResourceAssignment,
    RolePermission,
    UserAccount,
    Workspace,
)
from domains.target.management_guard import MANAGEMENT_CLUSTER_ROLE, TARGET_CLUSTER_ROLE
from packages.config.security import TEST_FIXTURE_ENVIRONMENT, test_fixture_purge_enabled
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.identity import (
    DEFAULT_GROUP_ID,
    DEFAULT_GROUP_NAME,
    DEFAULT_ORGANIZATION_ID,
    DEFAULT_ORGANIZATION_NAME,
    DEFAULT_ROLE_PERMISSION_ROWS,
    DEFAULT_WORKSPACE_ID,
    DEFAULT_WORKSPACE_NAME,
    GLOBAL_ROLE_POLICY_ORGANIZATION_ID,
    RESOURCE_ROLE_PERMISSIONS,
    AccessResourceType,
    AccessStatus,
    ClusterRegistrationStatus,
    GroupRole,
    OrganizationRole,
    Permission,
    ResourceRole,
    ServiceRole,
    UserStatus,
    WorkspaceStatus,
)
from packages.storage.engine import DatabaseConnection, iso_or_none
from packages.storage.retry import sync_retry_db_conflict


def _accessible_resource_ids_statement(
    user_id: str,
    workspace_id: str,
    resource_type: str,
    permission: str,
) -> Any:
    """Resolve resource grants and scoped-policy fallback in one statement.

    A scoped role policy replaces the global role policy when any scoped row
    exists, including an all-disabled policy.  Keeping that decision correlated
    to the member role preserves the existing fail-closed override semantics
    without one existence query plus one permission query per distinct role.
    """
    assignment = ResourceAssignment.__table__
    group_member = GroupMember.__table__
    member_role = MemberResourceRole.__table__
    active_policy = RolePermission.__table__.alias("active_role_policy")
    scoped_policy = RolePermission.__table__.alias("scoped_role_policy")
    scoped_rows_exist = (
        select(scoped_policy.c.id)
        .where(
            scoped_policy.c.organization_id == workspace_id,
            scoped_policy.c.resource_type == resource_type,
            scoped_policy.c.role == member_role.c.role,
        )
        .limit(1)
        .exists()
    )
    policy_scope = case(
        (scoped_rows_exist, workspace_id),
        else_=GLOBAL_ROLE_POLICY_ORGANIZATION_ID,
    )
    return (
        select(assignment.c.resource_id)
        .select_from(
            assignment.join(
                group_member,
                (group_member.c.group_id == assignment.c.group_id)
                & (group_member.c.user_id == user_id)
                & (group_member.c.status == AccessStatus.ACTIVE.value),
            )
            .join(
                member_role,
                (member_role.c.resource_assignment_id == assignment.c.resource_assignment_id)
                & (member_role.c.user_id == user_id)
                & (member_role.c.status == AccessStatus.ACTIVE.value),
            )
            .join(
                active_policy,
                and_(
                    active_policy.c.organization_id == policy_scope,
                    active_policy.c.resource_type == resource_type,
                    active_policy.c.role == member_role.c.role,
                    active_policy.c.permission == permission,
                    active_policy.c.status == AccessStatus.ACTIVE.value,
                ),
            )
        )
        .where(
            assignment.c.organization_id == workspace_id,
            assignment.c.resource_type == resource_type,
            assignment.c.status == AccessStatus.ACTIVE.value,
        )
        .distinct()
    )


class IdentityAccessRepository(DatabaseConnection):
    """identity·조직·그룹·리소스 접근 저장소."""

    user_table = UserAccount.__table__
    workspace_table = Workspace.__table__
    organization_table = Organization.__table__
    organization_member_table = OrganizationMember.__table__
    group_table = Group.__table__
    group_member_table = GroupMember.__table__
    resource_assignment_table = ResourceAssignment.__table__
    member_resource_role_table = MemberResourceRole.__table__
    role_permission_table = RolePermission.__table__
    cluster_table = ClusterRegistration.__table__

    @staticmethod
    def required_tables() -> set[str]:
        return {
            UserAccount.__tablename__,
            Workspace.__tablename__,
            Organization.__tablename__,
            OrganizationMember.__tablename__,
            Group.__tablename__,
            GroupMember.__tablename__,
            ResourceAssignment.__tablename__,
            MemberResourceRole.__tablename__,
            RolePermission.__tablename__,
            ClusterRegistration.__tablename__,
        }

    def ensure_default_workspace(self) -> JsonObject:
        table = Workspace.__table__
        statement = self._workspace_upsert(DEFAULT_WORKSPACE_ID, DEFAULT_WORKSPACE_NAME).returning(
            table.c.workspace_id,
            table.c.name,
            table.c.slug,
            table.c.status,
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return (
            dict(row)
            if row is not None
            else {
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "name": DEFAULT_WORKSPACE_NAME,
                "slug": DEFAULT_WORKSPACE_ID,
                "status": WorkspaceStatus.ACTIVE.value,
            }
        )

    def ensure_default_organization(self) -> JsonObject:
        table = Organization.__table__
        statement = self._organization_upsert(
            DEFAULT_ORGANIZATION_ID,
            DEFAULT_ORGANIZATION_NAME,
        ).returning(
            table.c.organization_id,
            table.c.name,
            table.c.slug,
            table.c.status,
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
            conn.execute(
                self._group_upsert(DEFAULT_GROUP_ID, DEFAULT_ORGANIZATION_ID, DEFAULT_GROUP_NAME)
            )
        return (
            dict(row)
            if row is not None
            else {
                "organization_id": DEFAULT_ORGANIZATION_ID,
                "name": DEFAULT_ORGANIZATION_NAME,
                "slug": DEFAULT_ORGANIZATION_ID,
                "status": AccessStatus.ACTIVE.value,
            }
        )

    def ensure_default_role_permissions(self) -> list[JsonObject]:
        table = RolePermission.__table__
        rows: list[JsonObject] = []
        managed_resource_types = {
            resource_type
            for _scope, resource_type, _role, _permission, _status in DEFAULT_ROLE_PERMISSION_ROWS
        }
        new_roles = set(RESOURCE_ROLE_PERMISSIONS)
        with self.connection() as conn:
            if managed_resource_types:
                conn.execute(
                    table.update()
                    .where(
                        table.c.organization_id == GLOBAL_ROLE_POLICY_ORGANIZATION_ID,
                        table.c.resource_type.in_(managed_resource_types),
                        table.c.role.notin_(new_roles),
                        table.c.status == AccessStatus.ACTIVE.value,
                    )
                    .values(status=AccessStatus.DISABLED.value, updated_at=func.now())
                )
            for (
                organization_id,
                resource_type,
                role,
                permission,
                status,
            ) in DEFAULT_ROLE_PERMISSION_ROWS:
                row = (
                    conn.execute(
                        self._role_permission_upsert(
                            organization_id,
                            resource_type,
                            role,
                            permission,
                            status,
                        ).returning(table)
                    )
                    .mappings()
                    .first()
                )
                if row is not None:
                    rows.append(dict(row))
        return rows

    def register_target_cluster(self, payload: JsonObject) -> JsonObject:
        user_id = str(payload["user_id"])
        workspace_id = str(payload.get("workspace_id") or DEFAULT_WORKSPACE_ID)
        organization_id = str(payload.get("organization_id") or workspace_id)
        group_id = self._default_group_id(organization_id)
        cluster_id = str(payload["cluster_id"])
        assignment_id = self._resource_assignment_id(
            organization_id,
            AccessResourceType.CLUSTER.value,
            cluster_id,
        )
        with self.connection() as conn:
            conn.execute(self._user_upsert(user_id))
            conn.execute(self._workspace_upsert(workspace_id, workspace_id))
            conn.execute(self._organization_upsert(organization_id, organization_id))
            conn.execute(
                self._organization_member_upsert(
                    organization_id,
                    user_id,
                    OrganizationRole.OWNER.value,
                )
            )
            conn.execute(self._group_upsert(group_id, organization_id, DEFAULT_GROUP_NAME))
            conn.execute(self._group_member_upsert(group_id, user_id, GroupRole.MANAGER.value))
            conn.execute(
                self._resource_assignment_upsert(
                    assignment_id,
                    organization_id,
                    group_id,
                    AccessResourceType.CLUSTER.value,
                    cluster_id,
                )
            )
            conn.execute(
                self._member_resource_role_upsert(
                    assignment_id,
                    user_id,
                    ResourceRole.CLUSTER_STEWARD.value,
                )
            )
            conn.execute(self._cluster_upsert(payload))
        return payload

    def update_cluster_registration_status(
        self,
        workspace_id: str,
        cluster_id: str,
        status: str,
    ) -> None:
        table = ClusterRegistration.__table__
        statement = (
            update(table)
            .where(table.c.workspace_id == workspace_id, table.c.cluster_id == cluster_id)
            .values(status=status, updated_at=func.now())
        )
        with self.connection() as conn:
            conn.execute(statement)

    def mark_cluster_registration_connected(self, workspace_id: str, cluster_id: str) -> bool:
        """Promote an enrolled agent without cancelling a pending uninstall."""

        table = ClusterRegistration.__table__
        statement = (
            update(table)
            .where(
                table.c.workspace_id == workspace_id,
                table.c.cluster_id == cluster_id,
                table.c.status.in_(
                    (
                        ClusterRegistrationStatus.PENDING_INSTALL.value,
                        ClusterRegistrationStatus.INSTALL_APPLIED.value,
                        ClusterRegistrationStatus.INSTALL_FAILED.value,
                        ClusterRegistrationStatus.REGISTERED.value,
                    )
                ),
            )
            .values(status=ClusterRegistrationStatus.REGISTERED.value, updated_at=func.now())
            .returning(table.c.cluster_id)
        )
        with self.connection() as conn:
            return conn.execute(statement).first() is not None

    def reissue_target_cluster_install(
        self,
        workspace_id: str,
        cluster_id: str,
        *,
        agent_token_hash: str,
        agent_envelope_public_key: str,
        agent_envelope_private_key_encrypted: str,
        settings: JsonObject,
    ) -> bool:
        """만료/대기 등록의 설치 자격증명을 원자적으로 회전한다."""
        table = ClusterRegistration.__table__
        statement = (
            update(table)
            .where(
                table.c.workspace_id == workspace_id,
                table.c.cluster_id == cluster_id,
                table.c.status.in_(
                    (
                        ClusterRegistrationStatus.PENDING_INSTALL.value,
                        ClusterRegistrationStatus.INSTALL_APPLIED.value,
                        ClusterRegistrationStatus.INSTALL_FAILED.value,
                        ClusterRegistrationStatus.INSTALL_EXPIRED.value,
                    )
                ),
            )
            .values(
                status=ClusterRegistrationStatus.PENDING_INSTALL.value,
                agent_token_hash=agent_token_hash,
                agent_envelope_public_key=agent_envelope_public_key,
                agent_envelope_private_key_encrypted=agent_envelope_private_key_encrypted,
                settings=settings,
                updated_at=func.now(),
            )
            .returning(table.c.cluster_id)
        )
        with self.connection() as conn:
            return conn.execute(statement).first() is not None

    def unregister_target_cluster(self, workspace_id: str, cluster_id: str) -> bool:
        """target 등록 해제 — 감사/권한 이력은 남기고 agent 토큰만 폐기한다."""
        table = ClusterRegistration.__table__
        statement = (
            update(table)
            .where(table.c.workspace_id == workspace_id, table.c.cluster_id == cluster_id)
            .values(
                status=ClusterRegistrationStatus.DISCONNECTED.value,
                agent_token_hash=None,
                agent_envelope_public_key=None,
                agent_envelope_private_key_encrypted=None,
                updated_at=func.now(),
            )
            .returning(table.c.cluster_id)
        )
        with self.connection() as conn:
            return conn.execute(statement).first() is not None

    def purge_test_target_cluster_registration(self, workspace_id: str, cluster_id: str) -> bool:
        """테스트 fixture registration과 전용 접근 할당을 물리 삭제한다."""
        if not test_fixture_purge_enabled():
            return False

        cluster = self.cluster_table
        assignment = self.resource_assignment_table
        member_role = self.member_resource_role_table
        cluster_role = func.coalesce(
            cluster.c.settings["cluster_role"].astext,
            TARGET_CLUSTER_ROLE,
        )
        delete_registration = (
            delete(cluster)
            .where(
                cluster.c.workspace_id == workspace_id,
                cluster.c.cluster_id == cluster_id,
                cluster.c.environment == TEST_FIXTURE_ENVIRONMENT,
                cluster_role != MANAGEMENT_CLUSTER_ROLE,
            )
            .returning(cluster.c.cluster_id)
        )
        assignment_filter = (
            assignment.c.organization_id == workspace_id,
            assignment.c.resource_type == AccessResourceType.CLUSTER.value,
            assignment.c.resource_id == cluster_id,
        )
        assignment_ids = select(assignment.c.resource_assignment_id).where(*assignment_filter)

        with self.connection() as conn:
            if conn.execute(delete_registration).first() is None:
                return False
            conn.execute(
                delete(member_role).where(member_role.c.resource_assignment_id.in_(assignment_ids))
            )
            conn.execute(delete(assignment).where(*assignment_filter))
        return True

    def get_user_by_email(self, email: str) -> JsonObject | None:
        table = UserAccount.__table__
        statement = (
            select(
                table.c.user_id,
                table.c.email,
                table.c.password_hash,
                table.c.display_name,
                table.c.status,
                table.c.role,
            )
            .where(table.c.email == email)
            .limit(1)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def get_user_by_id(self, user_id: str) -> JsonObject | None:
        table = UserAccount.__table__
        statement = (
            select(
                table.c.user_id,
                table.c.email,
                table.c.password_hash,
                table.c.display_name,
                table.c.status,
                table.c.role,
            )
            .where(table.c.user_id == user_id)
            .limit(1)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def list_active_group_ids_for_user(self, user_id: str, workspace_id: str) -> list[str]:
        """Return persistent RBAC group identities for one exact workspace subject."""

        group = self.group_table
        member = self.group_member_table
        statement = (
            select(group.c.group_id)
            .select_from(member.join(group, member.c.group_id == group.c.group_id))
            .where(
                member.c.user_id == user_id,
                member.c.status == AccessStatus.ACTIVE.value,
                group.c.organization_id == workspace_id,
                group.c.status == AccessStatus.ACTIVE.value,
            )
            .order_by(group.c.group_id)
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [str(row["group_id"]) for row in rows]

    def create_user(
        self,
        user_id: str,
        email: str,
        password_hash: str,
        display_name: str,
        status: str,
        role: str,
    ) -> JsonObject | None:
        table = UserAccount.__table__
        service_role = (
            ServiceRole.SERVICE_ADMIN.value
            if role == ServiceRole.SERVICE_ADMIN.value
            else ServiceRole.USER.value
        )
        statement = (
            pg_insert(table)
            .values(
                user_id=user_id,
                email=email,
                password_hash=password_hash,
                display_name=display_name,
                status=status,
                role=service_role,
            )
            .on_conflict_do_nothing(index_elements=[table.c.email])
            .returning(
                table.c.user_id,
                table.c.email,
                table.c.password_hash,
                table.c.display_name,
                table.c.status,
                table.c.role,
            )
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def upsert_admin_account(
        self,
        user_id: str,
        email: str,
        password_hash: str,
        display_name: str,
    ) -> JsonObject | None:
        table = UserAccount.__table__
        statement = self._admin_user_upsert(
            user_id,
            email,
            password_hash,
            display_name,
        ).returning(
            table.c.user_id,
            table.c.email,
            table.c.password_hash,
            table.c.display_name,
            table.c.status,
            table.c.role,
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
            conn.execute(self._workspace_upsert(DEFAULT_WORKSPACE_ID, DEFAULT_WORKSPACE_NAME))
            conn.execute(
                self._organization_upsert(DEFAULT_ORGANIZATION_ID, DEFAULT_ORGANIZATION_NAME)
            )
            conn.execute(
                self._organization_member_upsert(
                    DEFAULT_ORGANIZATION_ID,
                    user_id,
                    OrganizationRole.OWNER.value,
                )
            )
            conn.execute(
                self._group_upsert(
                    DEFAULT_GROUP_ID,
                    DEFAULT_ORGANIZATION_ID,
                    DEFAULT_GROUP_NAME,
                )
            )
            conn.execute(
                self._group_member_upsert(DEFAULT_GROUP_ID, user_id, GroupRole.MANAGER.value)
            )
        return dict(row) if row is not None else None

    def complete_email_verification(self, user_id: str) -> JsonObject | None:
        table = UserAccount.__table__
        with self.connection() as conn:
            user = (
                conn.execute(select(table).where(table.c.user_id == user_id).limit(1))
                .mappings()
                .first()
            )
            if user is None:
                return None
            if user["status"] != UserStatus.PENDING_EMAIL_VERIFICATION.value:
                return dict(user)
            if self._has_service_admin(conn):
                status = UserStatus.PENDING_APPROVAL.value
                role = ServiceRole.USER.value
            else:
                status = UserStatus.ACTIVE.value
                role = ServiceRole.SERVICE_ADMIN.value
            row = (
                conn.execute(
                    table.update()
                    .where(table.c.user_id == user_id)
                    .values(status=status, role=role, updated_at=func.now())
                    .returning(
                        table.c.user_id,
                        table.c.email,
                        table.c.password_hash,
                        table.c.display_name,
                        table.c.status,
                        table.c.role,
                    )
                )
                .mappings()
                .first()
            )
            if status == UserStatus.ACTIVE.value:
                conn.execute(self._workspace_upsert(DEFAULT_WORKSPACE_ID, DEFAULT_WORKSPACE_NAME))
                conn.execute(
                    self._organization_upsert(DEFAULT_ORGANIZATION_ID, DEFAULT_ORGANIZATION_NAME)
                )
                conn.execute(
                    self._organization_member_upsert(
                        DEFAULT_ORGANIZATION_ID,
                        user_id,
                        OrganizationRole.OWNER.value,
                    )
                )
                conn.execute(
                    self._group_upsert(
                        DEFAULT_GROUP_ID,
                        DEFAULT_ORGANIZATION_ID,
                        DEFAULT_GROUP_NAME,
                    )
                )
                conn.execute(
                    self._group_member_upsert(DEFAULT_GROUP_ID, user_id, GroupRole.MANAGER.value)
                )
        if row is None:
            return None
        result = dict(row)
        result["workspace_id"] = DEFAULT_WORKSPACE_ID
        return result

    def approve_user(self, user_id: str, workspace_id: str) -> JsonObject | None:
        table = UserAccount.__table__
        organization_id = workspace_id or DEFAULT_ORGANIZATION_ID
        group_id = self._default_group_id(organization_id)
        with self.connection() as conn:
            user = (
                conn.execute(select(table).where(table.c.user_id == user_id).limit(1))
                .mappings()
                .first()
            )
            if user is None or user["status"] != UserStatus.PENDING_APPROVAL.value:
                return None
            row = (
                conn.execute(
                    table.update()
                    .where(table.c.user_id == user_id)
                    .values(
                        status=UserStatus.ACTIVE.value,
                        role=ServiceRole.USER.value,
                        updated_at=func.now(),
                    )
                    .returning(
                        table.c.user_id,
                        table.c.email,
                        table.c.password_hash,
                        table.c.display_name,
                        table.c.status,
                        table.c.role,
                    )
                )
                .mappings()
                .first()
            )
            conn.execute(self._workspace_upsert(organization_id, organization_id))
            conn.execute(self._organization_upsert(organization_id, organization_id))
            conn.execute(
                self._organization_member_upsert(
                    organization_id,
                    user_id,
                    OrganizationRole.MEMBER.value,
                )
            )
            conn.execute(self._group_upsert(group_id, organization_id, DEFAULT_GROUP_NAME))
            conn.execute(self._group_member_upsert(group_id, user_id, GroupRole.MEMBER.value))
        if row is None:
            return None
        result = dict(row)
        result["workspace_id"] = organization_id
        return result

    def get_default_workspace_id_for_user(self, user_id: str) -> str | None:
        organization_member = OrganizationMember.__table__
        user_table = UserAccount.__table__
        with self.connection() as conn:
            service_role = conn.execute(
                select(user_table.c.role)
                .where(
                    user_table.c.user_id == user_id,
                    user_table.c.status == UserStatus.ACTIVE.value,
                )
                .limit(1)
            ).scalar_one_or_none()
            if service_role == ServiceRole.SERVICE_ADMIN.value:
                return DEFAULT_WORKSPACE_ID
            value = conn.execute(
                select(organization_member.c.organization_id)
                .where(
                    organization_member.c.user_id == user_id,
                    organization_member.c.status == AccessStatus.ACTIVE.value,
                )
                .order_by(organization_member.c.created_at)
                .limit(1)
            ).scalar_one_or_none()
        return str(value) if value is not None else None

    def list_authorized_workspaces(
        self,
        user_id: str,
        *,
        service_admin: bool,
    ) -> list[JsonObject]:
        """List active workspaces granted by persistent membership or service authority."""

        workspace = self.workspace_table
        organization = self.organization_table
        member = self.organization_member_table
        statement = select(
            workspace.c.workspace_id,
            workspace.c.name,
            workspace.c.slug,
        ).where(workspace.c.status == WorkspaceStatus.ACTIVE.value)
        if not service_admin:
            statement = statement.select_from(
                workspace.join(
                    organization,
                    organization.c.organization_id == workspace.c.workspace_id,
                ).join(
                    member,
                    member.c.organization_id == organization.c.organization_id,
                )
            ).where(
                organization.c.status == AccessStatus.ACTIVE.value,
                member.c.user_id == user_id,
                member.c.status == AccessStatus.ACTIVE.value,
            )
        statement = statement.order_by(func.lower(workspace.c.name), workspace.c.workspace_id)
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [dict(row) for row in rows]

    def grant_resource_access(self, payload: JsonObject) -> JsonObject:
        user_id = str(payload.get("subject_id") or payload.get("user_id"))
        organization_id = str(
            payload.get("organization_id") or payload.get("workspace_id") or DEFAULT_ORGANIZATION_ID
        )
        resource_type = str(payload["resource_type"])
        resource_id = str(payload["resource_id"])
        role = ResourceRole(str(payload.get("role") or ResourceRole.OBSERVER.value)).value
        group_id = str(payload.get("group_id") or self._default_group_id(organization_id))
        assignment_id = self._resource_assignment_id(organization_id, resource_type, resource_id)
        with self.connection() as conn:
            conn.execute(self._user_upsert(user_id))
            conn.execute(self._workspace_upsert(organization_id, organization_id))
            conn.execute(self._organization_upsert(organization_id, organization_id))
            conn.execute(
                self._organization_member_upsert(
                    organization_id,
                    user_id,
                    OrganizationRole.MEMBER.value,
                )
            )
            conn.execute(self._group_upsert(group_id, organization_id, DEFAULT_GROUP_NAME))
            conn.execute(self._group_member_upsert(group_id, user_id, GroupRole.MEMBER.value))
            conn.execute(
                self._resource_assignment_upsert(
                    assignment_id,
                    organization_id,
                    group_id,
                    resource_type,
                    resource_id,
                )
            )
            conn.execute(self._member_resource_role_upsert(assignment_id, user_id, role))
        return {
            **payload,
            "organization_id": organization_id,
            "workspace_id": organization_id,
            "subject_id": user_id,
            "role": role,
        }

    def is_service_admin(self, user_id: str) -> bool:
        def lookup() -> bool:
            table = UserAccount.__table__
            statement = (
                select(table.c.user_id)
                .where(
                    table.c.user_id == user_id,
                    table.c.role == ServiceRole.SERVICE_ADMIN.value,
                    table.c.status == UserStatus.ACTIVE.value,
                )
                .limit(1)
            )
            with self.connection() as conn:
                return conn.execute(statement).first() is not None

        return sync_retry_db_conflict(lookup)

    def get_organization_member(self, organization_id: str, user_id: str) -> JsonObject | None:
        table = OrganizationMember.__table__
        statement = (
            select(table)
            .where(
                table.c.organization_id == organization_id,
                table.c.user_id == user_id,
                table.c.status == AccessStatus.ACTIVE.value,
            )
            .limit(1)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def get_group_member(self, group_id: str, user_id: str) -> JsonObject | None:
        table = GroupMember.__table__
        statement = (
            select(table)
            .where(
                table.c.group_id == group_id,
                table.c.user_id == user_id,
                table.c.status == AccessStatus.ACTIVE.value,
            )
            .limit(1)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def get_resource_assignment_for_org(
        self,
        organization_id: str,
        resource_type: str,
        resource_id: str,
    ) -> JsonObject | None:
        table = ResourceAssignment.__table__
        statement = (
            select(table)
            .where(
                table.c.organization_id == organization_id,
                table.c.resource_type == resource_type,
                table.c.resource_id == resource_id,
                table.c.status == AccessStatus.ACTIVE.value,
            )
            .limit(1)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def get_member_resource_role(
        self,
        resource_assignment_id: str,
        user_id: str,
    ) -> JsonObject | None:
        table = MemberResourceRole.__table__
        statement = (
            select(table)
            .where(
                table.c.resource_assignment_id == resource_assignment_id,
                table.c.user_id == user_id,
                table.c.status == AccessStatus.ACTIVE.value,
            )
            .limit(1)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def get_role_permissions(
        self,
        resource_type: str,
        role: str,
        organization_id: str | None = None,
    ) -> set[str]:
        role = ResourceRole(role).value
        organization_scope = self._role_policy_scope(
            organization_id or GLOBAL_ROLE_POLICY_ORGANIZATION_ID
        )
        table = RolePermission.__table__
        with self.connection() as conn:
            scope = organization_scope
            scoped_rows_exist = conn.execute(
                select(table.c.id)
                .where(
                    table.c.organization_id == organization_scope,
                    table.c.resource_type == resource_type,
                    table.c.role == role,
                )
                .limit(1)
            ).first()
            if scoped_rows_exist is None:
                scope = GLOBAL_ROLE_POLICY_ORGANIZATION_ID
            rows = conn.execute(
                select(table.c.permission).where(
                    table.c.organization_id == scope,
                    table.c.resource_type == resource_type,
                    table.c.role == role,
                    table.c.status == AccessStatus.ACTIVE.value,
                )
            ).scalars()
            return {str(row) for row in rows}

    def role_has_permission(
        self,
        resource_type: str,
        role: str,
        permission: str,
        organization_id: str | None = None,
    ) -> bool:
        return Permission(permission).value in self.get_role_permissions(
            resource_type,
            role,
            organization_id,
        )

    def can_access(
        self,
        user_id: str,
        organization_id: str,
        resource_type: str,
        resource_id: str,
        permission: str,
    ) -> bool:
        if self.is_service_admin(user_id):
            return True
        if self.get_organization_member(organization_id, user_id) is None:
            return False
        assignment = self.get_resource_assignment_for_org(
            organization_id,
            resource_type,
            resource_id,
        )
        if assignment is None:
            return False
        if self.get_group_member(str(assignment["group_id"]), user_id) is None:
            return False
        member_role = self.get_member_resource_role(
            str(assignment["resource_assignment_id"]),
            user_id,
        )
        if member_role is None:
            return False
        return self.role_has_permission(
            resource_type,
            str(member_role["role"]),
            permission,
            organization_id,
        )

    def effective_permissions_for_resource(
        self,
        user_id: str,
        organization_id: str,
        resource_type: str,
        resource_id: str,
    ) -> set[str]:
        """Resolve one resource's effective product permissions without N permission queries."""
        if self.is_service_admin(user_id):
            return {permission.value for permission in Permission}
        if self.get_organization_member(organization_id, user_id) is None:
            return set()
        assignment = self.get_resource_assignment_for_org(
            organization_id,
            resource_type,
            resource_id,
        )
        if assignment is None:
            return set()
        if self.get_group_member(str(assignment["group_id"]), user_id) is None:
            return set()
        member_role = self.get_member_resource_role(
            str(assignment["resource_assignment_id"]),
            user_id,
        )
        if member_role is None:
            return set()
        return self.get_role_permissions(
            resource_type,
            str(member_role["role"]),
            organization_id,
        )

    def user_has_resource_access(
        self,
        user_id: str,
        workspace_id: str,
        resource_type: str,
        resource_id: str,
        action: str,
    ) -> bool:
        return self.can_access(user_id, workspace_id, resource_type, resource_id, action)

    def accessible_resource_ids(
        self,
        user_id: str,
        workspace_id: str,
        resource_type: str,
        action: str,
    ) -> set[str] | None:
        def lookup() -> set[str] | None:
            if self.is_service_admin(user_id):
                return None
            permission = Permission(action).value
            statement = _accessible_resource_ids_statement(
                user_id,
                workspace_id,
                resource_type,
                permission,
            )
            with self.connection() as conn:
                candidates = list(conn.execute(statement).mappings())
            return {str(row["resource_id"]) for row in candidates}

        return sync_retry_db_conflict(lookup)

    def authenticate_cluster_agent(self, token_hash: str) -> JsonObject | None:
        if not token_hash:
            return None
        table = ClusterRegistration.__table__
        statement = (
            select(table.c.workspace_id, table.c.cluster_id)
            .where(
                table.c.agent_token_hash == token_hash,
                table.c.status.in_(
                    (
                        ClusterRegistrationStatus.PENDING_INSTALL.value,
                        ClusterRegistrationStatus.INSTALL_APPLIED.value,
                        ClusterRegistrationStatus.INSTALL_FAILED.value,
                        ClusterRegistrationStatus.REGISTERED.value,
                        ClusterRegistrationStatus.UNINSTALL_REQUESTED.value,
                    )
                ),
            )
            .limit(1)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def list_cluster_registrations(
        self,
        workspace_id: str,
        *,
        cluster_ids: set[str] | None = None,
        limit: int = 100,
    ) -> list[JsonObject]:
        if cluster_ids is not None and not cluster_ids:
            return []
        table = ClusterRegistration.__table__
        statement = (
            select(
                table.c.workspace_id,
                table.c.cluster_id,
                table.c.name,
                table.c.environment,
                table.c.status,
                table.c.settings,
                table.c.created_at,
                table.c.updated_at,
            )
            .where(
                table.c.workspace_id == workspace_id,
                table.c.status != ClusterRegistrationStatus.DISCONNECTED.value,
            )
            .order_by(table.c.environment, table.c.name, table.c.cluster_id)
            .limit(max(1, min(limit, 500)))
        )
        if cluster_ids is not None:
            statement = statement.where(table.c.cluster_id.in_(cluster_ids))
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [self._serialize_cluster_registration(row) for row in rows]

    def get_cluster_registration(self, workspace_id: str, cluster_id: str) -> JsonObject | None:
        table = ClusterRegistration.__table__
        statement = (
            select(
                table.c.workspace_id,
                table.c.cluster_id,
                table.c.name,
                table.c.environment,
                table.c.status,
                table.c.settings,
                table.c.created_at,
                table.c.updated_at,
            )
            .where(table.c.workspace_id == workspace_id, table.c.cluster_id == cluster_id)
            .limit(1)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return self._serialize_cluster_registration(row) if row else None

    def get_cluster_registration_install_credentials(
        self, workspace_id: str, cluster_id: str
    ) -> JsonObject | None:
        """Return the private enrollment material for the one-time installer only.

        Keep these encrypted key fields out of the general registration reader so
        ordinary cluster/detail call sites cannot accidentally serialize them.
        """
        table = ClusterRegistration.__table__
        statement = (
            select(
                table.c.workspace_id,
                table.c.cluster_id,
                table.c.settings,
                table.c.agent_envelope_public_key,
                table.c.agent_envelope_private_key_encrypted,
            )
            .where(table.c.workspace_id == workspace_id, table.c.cluster_id == cluster_id)
            .limit(1)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    @staticmethod
    def _serialize_cluster_registration(row: JsonObject) -> JsonObject:
        item = dict(row)
        item["created_at"] = iso_or_none(item.get("created_at"))
        item["updated_at"] = iso_or_none(item.get("updated_at"))
        return item

    # --- 관리 콘솔 조회/편집 API (조직·그룹·멤버·권한) ---

    def list_organizations(self) -> list[JsonObject]:
        org = self.organization_table
        member = self.organization_member_table
        group = self.group_table
        member_count = (
            select(func.count())
            .where(
                member.c.organization_id == org.c.organization_id,
                member.c.status == AccessStatus.ACTIVE.value,
            )
            .scalar_subquery()
        )
        group_count = (
            select(func.count())
            .where(
                group.c.organization_id == org.c.organization_id,
                group.c.status == AccessStatus.ACTIVE.value,
            )
            .scalar_subquery()
        )
        statement = (
            select(
                org.c.organization_id,
                org.c.name,
                org.c.slug,
                org.c.created_at,
                member_count.label("member_count"),
                group_count.label("group_count"),
            )
            .where(org.c.status == AccessStatus.ACTIVE.value)
            .order_by(org.c.created_at)
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [
            {
                "org_id": r["organization_id"],
                "name": r["name"],
                "description": r["slug"],
                "member_count": int(r["member_count"]),
                "group_count": int(r["group_count"]),
                "created_at": iso_or_none(r["created_at"]),
            }
            for r in rows
        ]

    def create_organization(self, name: str, description: str) -> JsonObject:
        organization_id = f"org-{uuid.uuid4().hex[:12]}"
        with self.connection() as conn:
            conn.execute(self._organization_upsert(organization_id, name))
        return {
            "org_id": organization_id,
            "name": name,
            "description": description,
            "member_count": 0,
            "group_count": 0,
            "created_at": None,
        }

    def delete_organization(self, organization_id: str) -> bool:
        group = self.group_table
        org = self.organization_table
        has_group = (
            select(group.c.group_id)
            .where(
                group.c.organization_id == organization_id,
                group.c.status == AccessStatus.ACTIVE.value,
            )
            .limit(1)
        )
        with self.connection() as conn:
            if conn.execute(has_group).first() is not None:
                return False
            conn.execute(
                org.update()
                .where(org.c.organization_id == organization_id)
                .values(status=AccessStatus.DISABLED.value, updated_at=func.now())
            )
        return True

    def list_groups(self, organization_id: str | None = None) -> list[JsonObject]:
        group = self.group_table
        member = self.group_member_table
        member_count = (
            select(func.count())
            .where(
                member.c.group_id == group.c.group_id,
                member.c.status == AccessStatus.ACTIVE.value,
            )
            .scalar_subquery()
        )
        statement = (
            select(
                group.c.group_id,
                group.c.organization_id,
                group.c.name,
                member_count.label("member_count"),
            )
            .where(group.c.status == AccessStatus.ACTIVE.value)
            .order_by(group.c.name)
        )
        if organization_id:
            statement = statement.where(group.c.organization_id == organization_id)
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [
            {
                "group_id": r["group_id"],
                "org_id": r["organization_id"],
                "name": r["name"],
                "member_count": int(r["member_count"]),
            }
            for r in rows
        ]

    def create_group(self, organization_id: str, name: str) -> JsonObject:
        group_id = f"grp-{uuid.uuid4().hex[:12]}"
        with self.connection() as conn:
            conn.execute(self._group_upsert(group_id, organization_id, name))
        return {"group_id": group_id, "org_id": organization_id, "name": name, "member_count": 0}

    def list_group_members(self, group_id: str) -> list[JsonObject]:
        member = self.group_member_table
        user = self.user_table
        statement = (
            select(user.c.user_id, user.c.email)
            .select_from(member.join(user, member.c.user_id == user.c.user_id))
            .where(
                member.c.group_id == group_id,
                member.c.status == AccessStatus.ACTIVE.value,
            )
            .order_by(user.c.email)
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [{"user_id": r["user_id"], "email": r["email"] or r["user_id"]} for r in rows]

    def add_group_member(self, group_id: str, user_id: str) -> None:
        with self.connection() as conn:
            conn.execute(self._group_member_upsert(group_id, user_id, GroupRole.MEMBER.value))

    def remove_group_member(self, group_id: str, user_id: str) -> None:
        member = self.group_member_table
        with self.connection() as conn:
            conn.execute(
                member.update()
                .where(member.c.group_id == group_id, member.c.user_id == user_id)
                .values(status=AccessStatus.DISABLED.value, updated_at=func.now())
            )

    def is_last_active_service_admin(self, user_id: str) -> bool:
        table = self.user_table
        statement = select(table.c.user_id).where(
            table.c.role == ServiceRole.SERVICE_ADMIN.value,
            table.c.status == UserStatus.ACTIVE.value,
        )
        with self.connection() as conn:
            rows = [str(row[0]) for row in conn.execute(statement).all()]
        return rows == [user_id]

    def list_users(self, status: str | None = None) -> list[JsonObject]:
        user = self.user_table
        member = self.group_member_table
        statement = select(
            user.c.user_id, user.c.email, user.c.role, user.c.status, user.c.created_at
        ).order_by(user.c.created_at)
        if status:
            statement = statement.where(user.c.status == status)
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
            group_rows = (
                conn.execute(
                    select(member.c.user_id, member.c.group_id).where(
                        member.c.status == AccessStatus.ACTIVE.value
                    )
                )
                .mappings()
                .all()
            )
        groups_by_user: dict[str, list[str]] = {}
        for g in group_rows:
            groups_by_user.setdefault(g["user_id"], []).append(g["group_id"])
        return [
            {
                "user_id": r["user_id"],
                "email": r["email"] or r["user_id"],
                "role": r["role"],
                "status": r["status"],
                "groups": groups_by_user.get(r["user_id"], []),
                "created_at": iso_or_none(r["created_at"]),
            }
            for r in rows
        ]

    def list_access_grants(
        self,
        organization_id: str,
        resource_id: str | None = None,
    ) -> list[JsonObject]:
        assignment = self.resource_assignment_table
        role = self.member_resource_role_table
        user = self.user_table
        statement = (
            select(
                role.c.id,
                role.c.user_id,
                role.c.role,
                role.c.created_at,
                assignment.c.resource_type,
                assignment.c.resource_id,
                user.c.email,
            )
            .select_from(
                role.join(
                    assignment,
                    role.c.resource_assignment_id == assignment.c.resource_assignment_id,
                ).join(user, role.c.user_id == user.c.user_id, isouter=True)
            )
            .where(
                role.c.status == AccessStatus.ACTIVE.value,
                assignment.c.organization_id == organization_id,
            )
            .order_by(role.c.created_at)
        )
        if resource_id:
            statement = statement.where(assignment.c.resource_id == resource_id)
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [
            {
                "access_id": str(r["id"]),
                "subject_id": str(r["user_id"]),
                "subject_type": "user",
                "subject_label": r["email"] or r["user_id"],
                "resource_type": r["resource_type"],
                "resource_id": r["resource_id"],
                "role": r["role"],
                "granted_at": iso_or_none(r["created_at"]),
            }
            for r in rows
        ]

    def revoke_access(self, access_id: str) -> None:
        role = self.member_resource_role_table
        try:
            row_id = int(access_id)
        except ValueError:
            return
        with self.connection() as conn:
            conn.execute(
                role.update()
                .where(role.c.id == row_id)
                .values(status=AccessStatus.DISABLED.value, updated_at=func.now())
            )

    @staticmethod
    def _user_upsert(user_id: str) -> Any:
        table = UserAccount.__table__
        insert = pg_insert(table).values(
            user_id=user_id,
            email=None,
            password_hash=None,
            display_name=user_id,
            status=UserStatus.ACTIVE.value,
            role=ServiceRole.USER.value,
        )
        return insert.on_conflict_do_update(
            index_elements=[table.c.user_id],
            set_={"updated_at": func.now()},
        )

    @staticmethod
    def _admin_user_upsert(
        user_id: str,
        email: str,
        password_hash: str,
        display_name: str,
    ) -> Any:
        table = UserAccount.__table__
        insert = pg_insert(table).values(
            user_id=user_id,
            email=email,
            password_hash=password_hash,
            display_name=display_name,
            status=UserStatus.ACTIVE.value,
            role=ServiceRole.SERVICE_ADMIN.value,
        )
        return insert.on_conflict_do_update(
            index_elements=[table.c.email],
            set_={
                "password_hash": insert.excluded.password_hash,
                "display_name": insert.excluded.display_name,
                "status": UserStatus.ACTIVE.value,
                "role": ServiceRole.SERVICE_ADMIN.value,
                "updated_at": func.now(),
            },
        )

    @staticmethod
    def _workspace_upsert(workspace_id: str, name: str) -> Any:
        table = Workspace.__table__
        insert = pg_insert(table).values(
            workspace_id=workspace_id,
            name=name,
            slug=workspace_id,
            status=WorkspaceStatus.ACTIVE.value,
        )
        return insert.on_conflict_do_update(
            index_elements=[table.c.workspace_id],
            set_={
                "name": insert.excluded.name,
                "status": WorkspaceStatus.ACTIVE.value,
                "updated_at": func.now(),
            },
        )

    @staticmethod
    def _organization_upsert(organization_id: str, name: str) -> Any:
        table = Organization.__table__
        insert = pg_insert(table).values(
            organization_id=organization_id,
            name=name,
            slug=organization_id,
            status=AccessStatus.ACTIVE.value,
        )
        return insert.on_conflict_do_update(
            index_elements=[table.c.organization_id],
            set_={
                "name": insert.excluded.name,
                "status": AccessStatus.ACTIVE.value,
                "updated_at": func.now(),
            },
        )

    @staticmethod
    def _organization_member_upsert(organization_id: str, user_id: str, role: str) -> Any:
        table = OrganizationMember.__table__
        insert = pg_insert(table).values(
            organization_id=organization_id,
            user_id=user_id,
            role=role,
            status=AccessStatus.ACTIVE.value,
        )
        return insert.on_conflict_do_update(
            index_elements=[table.c.organization_id, table.c.user_id],
            set_={
                "role": case(
                    (table.c.role == OrganizationRole.OWNER.value, table.c.role),
                    (insert.excluded.role == OrganizationRole.OWNER.value, insert.excluded.role),
                    else_=insert.excluded.role,
                ),
                "status": AccessStatus.ACTIVE.value,
                "updated_at": func.now(),
            },
        )

    @staticmethod
    def _group_upsert(group_id: str, organization_id: str, name: str) -> Any:
        table = Group.__table__
        insert = pg_insert(table).values(
            group_id=group_id,
            organization_id=organization_id,
            name=name,
            slug=group_id,
            status=AccessStatus.ACTIVE.value,
        )
        return insert.on_conflict_do_update(
            index_elements=[table.c.group_id],
            set_={
                "name": insert.excluded.name,
                "status": AccessStatus.ACTIVE.value,
                "updated_at": func.now(),
            },
        )

    @staticmethod
    def _group_member_upsert(group_id: str, user_id: str, role: str) -> Any:
        table = GroupMember.__table__
        insert = pg_insert(table).values(
            group_id=group_id,
            user_id=user_id,
            role=role,
            status=AccessStatus.ACTIVE.value,
        )
        return insert.on_conflict_do_update(
            index_elements=[table.c.group_id, table.c.user_id],
            set_={
                "role": case(
                    (table.c.role == GroupRole.MANAGER.value, table.c.role),
                    (insert.excluded.role == GroupRole.MANAGER.value, insert.excluded.role),
                    else_=insert.excluded.role,
                ),
                "status": AccessStatus.ACTIVE.value,
                "updated_at": func.now(),
            },
        )

    @staticmethod
    def _resource_assignment_upsert(
        resource_assignment_id: str,
        organization_id: str,
        group_id: str,
        resource_type: str,
        resource_id: str,
    ) -> Any:
        table = ResourceAssignment.__table__
        insert = pg_insert(table).values(
            resource_assignment_id=resource_assignment_id,
            organization_id=organization_id,
            group_id=group_id,
            resource_type=resource_type,
            resource_id=resource_id,
            status=AccessStatus.ACTIVE.value,
        )
        return insert.on_conflict_do_update(
            index_elements=[table.c.resource_assignment_id],
            set_={
                "group_id": insert.excluded.group_id,
                "status": AccessStatus.ACTIVE.value,
                "updated_at": func.now(),
            },
        )

    @staticmethod
    def _member_resource_role_upsert(
        resource_assignment_id: str,
        user_id: str,
        role: str,
    ) -> Any:
        table = MemberResourceRole.__table__
        insert = pg_insert(table).values(
            resource_assignment_id=resource_assignment_id,
            user_id=user_id,
            role=ResourceRole(role).value,
            status=AccessStatus.ACTIVE.value,
        )
        return insert.on_conflict_do_update(
            index_elements=[table.c.resource_assignment_id, table.c.user_id],
            set_={
                "role": case(
                    (table.c.role == ResourceRole.CLUSTER_STEWARD.value, table.c.role),
                    (
                        insert.excluded.role == ResourceRole.CLUSTER_STEWARD.value,
                        insert.excluded.role,
                    ),
                    else_=insert.excluded.role,
                ),
                "status": AccessStatus.ACTIVE.value,
                "updated_at": func.now(),
            },
        )

    @staticmethod
    def _role_permission_upsert(
        organization_id: str,
        resource_type: str,
        role: str,
        permission: str,
        status: str,
    ) -> Any:
        table = RolePermission.__table__
        insert = pg_insert(table).values(
            organization_id=organization_id,
            resource_type=resource_type,
            role=ResourceRole(role).value,
            permission=Permission(permission).value,
            status=status,
        )
        return insert.on_conflict_do_update(
            index_elements=[
                table.c.organization_id,
                table.c.resource_type,
                table.c.role,
                table.c.permission,
            ],
            set_={
                "status": insert.excluded.status,
                "updated_at": func.now(),
            },
        )

    @staticmethod
    def _cluster_upsert(payload: JsonObject) -> Any:
        table = ClusterRegistration.__table__
        workspace_id = str(payload.get("workspace_id") or DEFAULT_WORKSPACE_ID)
        insert = pg_insert(table).values(
            workspace_id=workspace_id,
            cluster_id=str(payload["cluster_id"]),
            name=str(payload.get("name") or payload["cluster_id"]),
            environment=str(payload.get("environment") or "default"),
            status=str(payload.get("status") or ClusterRegistrationStatus.REGISTERED.value),
            agent_token_hash=payload.get("agent_token_hash"),
            agent_envelope_public_key=payload.get("agent_envelope_public_key"),
            agent_envelope_private_key_encrypted=payload.get(
                "agent_envelope_private_key_encrypted"
            ),
            settings=payload.get("settings") or {},
        )
        return insert.on_conflict_do_update(
            index_elements=[table.c.workspace_id, table.c.cluster_id],
            set_={
                "name": insert.excluded.name,
                "environment": insert.excluded.environment,
                "status": str(payload.get("status") or ClusterRegistrationStatus.REGISTERED.value),
                "agent_token_hash": insert.excluded.agent_token_hash,
                "agent_envelope_public_key": insert.excluded.agent_envelope_public_key,
                "agent_envelope_private_key_encrypted": (
                    insert.excluded.agent_envelope_private_key_encrypted
                ),
                "settings": insert.excluded.settings,
                "updated_at": func.now(),
            },
        )

    @staticmethod
    def _role_policy_scope(organization_id: str) -> str:
        return organization_id or GLOBAL_ROLE_POLICY_ORGANIZATION_ID

    @staticmethod
    def _default_group_id(organization_id: str) -> str:
        if organization_id == DEFAULT_ORGANIZATION_ID:
            return DEFAULT_GROUP_ID
        digest = hashlib.sha256(organization_id.encode()).hexdigest()[:24]
        return f"group-{digest}"

    @staticmethod
    def _resource_assignment_id(
        organization_id: str,
        resource_type: str,
        resource_id: str,
    ) -> str:
        raw = "|".join([organization_id, resource_type, resource_id])
        digest = hashlib.sha256(raw.encode()).hexdigest()[:32]
        return f"resource-assignment-{digest}"

    @staticmethod
    def _has_service_admin(conn: Any) -> bool:
        table = UserAccount.__table__
        statement = (
            select(table.c.user_id)
            .where(
                table.c.role == ServiceRole.SERVICE_ADMIN.value,
                table.c.status == UserStatus.ACTIVE.value,
            )
            .limit(1)
        )
        return conn.execute(statement).first() is not None


WorkspaceAccessRepository = IdentityAccessRepository
