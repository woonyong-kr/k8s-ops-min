"""PostgreSQL authority for user-scoped shell state."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, PrimaryKeyConstraint, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.storage.base import Base, created_at_column, updated_at_column


class UserNamespaceScopeRecord(Base):
    __tablename__ = "user_namespace_scopes"
    __table_args__ = (PrimaryKeyConstraint("workspace_id", "user_id", "cluster_id"),)

    workspace_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    cluster_id: Mapped[str] = mapped_column(Text, nullable=False)
    namespaces: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    invalidation_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class UserUiPreferenceRecord(Base):
    __tablename__ = "user_ui_preferences"
    __table_args__ = (PrimaryKeyConstraint("workspace_id", "user_id"),)

    workspace_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    preferences: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class UserNodeAliasRecord(Base):
    __tablename__ = "user_node_aliases"
    __table_args__ = (PrimaryKeyConstraint("workspace_id", "user_id", "cluster_id", "node_name"),)

    workspace_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    cluster_id: Mapped[str] = mapped_column(Text, nullable=False)
    node_name: Mapped[str] = mapped_column(Text, nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()
