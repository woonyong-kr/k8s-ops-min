"""Transactional repository for user-scoped shell state."""

from __future__ import annotations

import hashlib
from contextlib import nullcontext
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domains.shell_state.models import UserNamespaceScopeRecord, UserUiPreferenceRecord
from domains.shell_state.node_aliases import NodeAliasRepositoryMixin
from packages.storage.engine import DatabaseConnection, iso_or_none


class ShellStateRepository(NodeAliasRepositoryMixin, DatabaseConnection):
    def get_namespace_scope(
        self,
        *,
        workspace_id: str,
        user_id: str,
        cluster_id: str,
    ) -> dict[str, Any] | None:
        table = UserNamespaceScopeRecord.__table__
        with self.connection() as conn:
            row = (
                conn.execute(
                    select(table).where(
                        table.c.workspace_id == workspace_id,
                        table.c.user_id == user_id,
                        table.c.cluster_id == cluster_id,
                    )
                )
                .mappings()
                .first()
            )
        return _serialize_namespace_scope(row)

    def put_namespace_scope(
        self,
        *,
        workspace_id: str,
        user_id: str,
        cluster_id: str,
        namespaces: tuple[str, ...],
        expected_revision: int,
        conn: Any | None = None,
    ) -> dict[str, Any] | None:
        table = UserNamespaceScopeRecord.__table__
        context = nullcontext(conn) if conn is not None else self.connection()
        with context as connection:
            connection.execute(
                select(
                    func.pg_advisory_xact_lock(
                        _lock_key(
                            "namespace",
                            workspace_id,
                            user_id,
                            cluster_id,
                        )
                    )
                )
            )
            existing = (
                connection.execute(
                    select(table)
                    .where(
                        table.c.workspace_id == workspace_id,
                        table.c.user_id == user_id,
                        table.c.cluster_id == cluster_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            current_revision = int(existing["revision"]) if existing is not None else 0
            if current_revision != expected_revision:
                return None
            next_revision = current_revision + 1
            next_generation = (
                int(existing["invalidation_generation"]) + 1 if existing is not None else 1
            )
            if existing is None:
                row = (
                    connection.execute(
                        pg_insert(table)
                        .values(
                            workspace_id=workspace_id,
                            user_id=user_id,
                            cluster_id=cluster_id,
                            namespaces=list(namespaces),
                            revision=next_revision,
                            invalidation_generation=next_generation,
                            updated_at=func.now(),
                        )
                        .returning(table)
                    )
                    .mappings()
                    .one()
                )
            else:
                row = (
                    connection.execute(
                        update(table)
                        .where(
                            table.c.workspace_id == workspace_id,
                            table.c.user_id == user_id,
                            table.c.cluster_id == cluster_id,
                            table.c.revision == expected_revision,
                        )
                        .values(
                            namespaces=list(namespaces),
                            revision=next_revision,
                            invalidation_generation=next_generation,
                            updated_at=func.now(),
                        )
                        .returning(table)
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    return None
        return _serialize_namespace_scope(row)

    def get_ui_preferences(
        self,
        *,
        workspace_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        table = UserUiPreferenceRecord.__table__
        with self.connection() as conn:
            row = (
                conn.execute(
                    select(table).where(
                        table.c.workspace_id == workspace_id,
                        table.c.user_id == user_id,
                    )
                )
                .mappings()
                .first()
            )
        return _serialize_preferences(row)

    def put_ui_preferences(
        self,
        *,
        workspace_id: str,
        user_id: str,
        preferences: dict[str, str],
        expected_revision: int,
        conn: Any | None = None,
    ) -> dict[str, Any] | None:
        table = UserUiPreferenceRecord.__table__
        context = nullcontext(conn) if conn is not None else self.connection()
        with context as connection:
            connection.execute(
                select(
                    func.pg_advisory_xact_lock(
                        _lock_key(
                            "preferences",
                            workspace_id,
                            user_id,
                        )
                    )
                )
            )
            existing = (
                connection.execute(
                    select(table)
                    .where(
                        table.c.workspace_id == workspace_id,
                        table.c.user_id == user_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            current_revision = int(existing["revision"]) if existing is not None else 0
            if current_revision != expected_revision:
                return None
            next_revision = current_revision + 1
            if existing is None:
                row = (
                    connection.execute(
                        pg_insert(table)
                        .values(
                            workspace_id=workspace_id,
                            user_id=user_id,
                            preferences=preferences,
                            revision=next_revision,
                        )
                        .returning(table)
                    )
                    .mappings()
                    .one()
                )
            else:
                row = (
                    connection.execute(
                        update(table)
                        .where(
                            table.c.workspace_id == workspace_id,
                            table.c.user_id == user_id,
                            table.c.revision == expected_revision,
                        )
                        .values(
                            preferences=preferences,
                            revision=next_revision,
                            updated_at=func.now(),
                        )
                        .returning(table)
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    return None
        return _serialize_preferences(row)


def _serialize_namespace_scope(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "cluster_id": str(row["cluster_id"]),
        "namespaces": tuple(sorted(str(item) for item in row["namespaces"])),
        "revision": int(row["revision"]),
        "invalidation_generation": int(row["invalidation_generation"]),
        "updated_at": iso_or_none(row.get("updated_at")),
    }


def _serialize_preferences(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "preferences": dict(row["preferences"]),
        "revision": int(row["revision"]),
        "updated_at": iso_or_none(row.get("updated_at")),
    }


def _lock_key(*parts: str) -> int:
    digest = hashlib.sha256("|".join(parts).encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)
