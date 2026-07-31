"""Repository mixin for user-scoped Kubernetes node display aliases."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domains.shell_state.models import UserNodeAliasRecord
from packages.storage.engine import iso_or_none


class NodeAliasRepositoryMixin:
    def list_node_aliases(
        self,
        *,
        workspace_id: str,
        user_id: str,
        cluster_id: str,
    ) -> list[dict[str, Any]]:
        table = UserNodeAliasRecord.__table__
        statement = (
            select(table)
            .where(
                table.c.workspace_id == workspace_id,
                table.c.user_id == user_id,
                table.c.cluster_id == cluster_id,
            )
            .order_by(table.c.node_name)
        )
        with self.connection() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(statement).mappings().all()
        return [_serialize_node_alias(row) for row in rows]

    def put_node_alias(
        self,
        *,
        workspace_id: str,
        user_id: str,
        cluster_id: str,
        node_name: str,
        alias: str,
    ) -> dict[str, Any]:
        table = UserNodeAliasRecord.__table__
        insert = pg_insert(table).values(
            workspace_id=workspace_id,
            user_id=user_id,
            cluster_id=cluster_id,
            node_name=node_name,
            alias=alias,
            revision=1,
            updated_at=func.now(),
        )
        statement = insert.on_conflict_do_update(
            index_elements=(table.c.workspace_id, table.c.user_id, table.c.cluster_id, table.c.node_name),
            set_={
                "alias": insert.excluded.alias,
                "revision": table.c.revision + 1,
                "updated_at": func.now(),
            },
        ).returning(table)
        with self.connection() as conn:  # type: ignore[attr-defined]
            row = conn.execute(statement).mappings().one()
        return _serialize_node_alias(row)

    def delete_node_alias(
        self,
        *,
        workspace_id: str,
        user_id: str,
        cluster_id: str,
        node_name: str,
    ) -> bool:
        table = UserNodeAliasRecord.__table__
        statement = (
            delete(table)
            .where(
                table.c.workspace_id == workspace_id,
                table.c.user_id == user_id,
                table.c.cluster_id == cluster_id,
                table.c.node_name == node_name,
            )
            .returning(table.c.node_name)
        )
        with self.connection() as conn:  # type: ignore[attr-defined]
            row = conn.execute(statement).first()
        return row is not None


def _serialize_node_alias(row: Any) -> dict[str, Any]:
    return {
        "cluster_id": str(row["cluster_id"]),
        "node_name": str(row["node_name"]),
        "alias": str(row["alias"]),
        "revision": int(row["revision"]),
        "updated_at": iso_or_none(row.get("updated_at")),
    }
