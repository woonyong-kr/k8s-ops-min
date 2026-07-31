"""Transactional persistence for revisioned Checks settings."""

from __future__ import annotations

import hashlib
from contextlib import nullcontext
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domains.checks.models import UserChecksSettingsRecord
from packages.storage.engine import DatabaseConnection, iso_or_none


class ChecksSettingsRepository(DatabaseConnection):
    def get_checks_settings(
        self,
        *,
        workspace_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        table = UserChecksSettingsRecord.__table__
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
        return _serialize(row)

    def put_checks_settings(
        self,
        *,
        workspace_id: str,
        user_id: str,
        policy: dict[str, list[str]],
        expected_revision: int,
        conn: Any | None = None,
    ) -> dict[str, Any] | None:
        table = UserChecksSettingsRecord.__table__
        context = nullcontext(conn) if conn is not None else self.connection()
        with context as connection:
            connection.execute(
                select(
                    func.pg_advisory_xact_lock(_lock_key("checks-settings", workspace_id, user_id))
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
            next_generation = (
                int(existing["invalidation_generation"]) + 1 if existing is not None else 1
            )
            values = {
                "policy": policy,
                "revision": next_revision,
                "invalidation_generation": next_generation,
                "updated_at": func.now(),
            }
            if existing is None:
                row = (
                    connection.execute(
                        pg_insert(table)
                        .values(
                            workspace_id=workspace_id,
                            user_id=user_id,
                            **values,
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
                        .values(**values)
                        .returning(table)
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    return None
        return _serialize(row)


def _serialize(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "policy": dict(row["policy"]),
        "revision": int(row["revision"]),
        "invalidation_generation": int(row["invalidation_generation"]),
        "updated_at": iso_or_none(row.get("updated_at")),
    }


def _lock_key(*parts: str) -> int:
    digest = hashlib.sha256("|".join(parts).encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)
