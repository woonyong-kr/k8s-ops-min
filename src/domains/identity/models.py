"""identity 도메인 테이블 — 서비스·조직·그룹·리소스 접근."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from packages.contracts.identity import GLOBAL_ROLE_POLICY_ORGANIZATION_ID
from packages.storage.base import (
    Base,
    created_at_column,
    jsonb_column,
    text_column,
    updated_at_column,
)


class UserAccount(Base):
    __tablename__ = "user_accounts"
    __table_args__ = (UniqueConstraint("email"),)
    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    role: Mapped[str] = text_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (UniqueConstraint("slug"),)
    organization_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = text_column()
    slug: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("organization_id", "user_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.organization_id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.user_id"))
    role: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class Group(Base):
    __tablename__ = "groups"
    __table_args__ = (UniqueConstraint("organization_id", "slug"),)
    group_id: Mapped[str] = mapped_column(Text, primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.organization_id"))
    name: Mapped[str] = text_column()
    slug: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.group_id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.user_id"))
    role: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class ResourceAssignment(Base):
    __tablename__ = "resource_assignments"
    __table_args__ = (
        UniqueConstraint("group_id", "resource_type", "resource_id"),
        UniqueConstraint("organization_id", "resource_type", "resource_id"),
    )
    resource_assignment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.organization_id"))
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.group_id"))
    resource_type: Mapped[str] = text_column()
    resource_id: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class MemberResourceRole(Base):
    __tablename__ = "member_resource_roles"
    __table_args__ = (UniqueConstraint("resource_assignment_id", "user_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resource_assignment_id: Mapped[str] = mapped_column(
        ForeignKey("resource_assignments.resource_assignment_id")
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.user_id"))
    role: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("organization_id", "resource_type", "role", "permission"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[str] = mapped_column(
        Text, nullable=False, default=GLOBAL_ROLE_POLICY_ORGANIZATION_ID
    )
    resource_type: Mapped[str] = text_column()
    role: Mapped[str] = text_column()
    permission: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("slug"),)
    workspace_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = text_column()
    slug: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class ClusterRegistration(Base):
    __tablename__ = "cluster_registrations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.workspace_id"))
    cluster_id: Mapped[str] = text_column()
    name: Mapped[str] = text_column()
    environment: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    agent_token_hash: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    agent_envelope_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_envelope_private_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    settings: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()
    __table_args__ = (
        UniqueConstraint("workspace_id", "cluster_id"),
        Index(
            "ux_cluster_registrations_workspace_active_name",
            workspace_id,
            func.lower(func.btrim(name)),
            unique=True,
            postgresql_where=text("status not in ('install_expired', 'disconnected')"),
        ),
    )
