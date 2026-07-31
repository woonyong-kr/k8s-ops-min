"""세션에 bind된 evidence query adapter와 FastAPI dependency."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import Depends

from domains.identity.dependencies import require_session, resolve_allowed_cluster_ids
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.identity import Permission
from packages.runtime.dependencies import get_db


@dataclass(frozen=True)
class AuthorizedEvidenceQuery:
    """기존 query router 저장소 표면에 세션 cluster 경계를 결합한다."""

    db: Any
    current: Any

    def list_evidence_records(
        self,
        workspace_id: str,
        *,
        correlation_id: str | None = None,
        kind: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
        cursor: tuple[datetime, int] | None = None,
    ) -> list[JsonObject]:
        allowed_cluster_ids = self._allowed_cluster_ids(workspace_id)
        if not allowed_cluster_ids:
            return []
        return self.db.list_evidence(
            workspace_id,
            allowed_cluster_ids,
            correlation_id=correlation_id,
            kind=kind,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
            cursor=cursor,
        )

    def list_evidence_windows_for_workspace(
        self,
        workspace_id: str,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[JsonObject]:
        allowed_cluster_ids = self._allowed_cluster_ids(workspace_id)
        if not allowed_cluster_ids:
            return []
        return self.db.list_evidence_windows(
            workspace_id,
            allowed_cluster_ids,
            limit=limit,
            offset=offset,
        )

    def get_evidence_window_payload_for_workspace(
        self,
        workspace_id: str,
        evidence_key: str,
    ) -> JsonObject | None:
        allowed_cluster_ids = self._allowed_cluster_ids(workspace_id)
        if not allowed_cluster_ids:
            return None
        return self.db.get_evidence(
            workspace_id,
            evidence_key,
            allowed_cluster_ids,
        )

    def _allowed_cluster_ids(self, workspace_id: str) -> set[str]:
        session_workspace_id = getattr(self.current, "workspace_id", None)
        if (
            not isinstance(session_workspace_id, str)
            or not session_workspace_id
            or workspace_id != session_workspace_id
        ):
            return set()
        return resolve_allowed_cluster_ids(
            self.db,
            self.current,
            session_workspace_id,
            Permission.EVIDENCE_READ.value,
        )


def get_authorized_evidence_query(
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> AuthorizedEvidenceQuery:
    """RCA query router가 DB dependency 한 줄로 교체할 session-bound adapter."""
    return AuthorizedEvidenceQuery(db=db, current=current)
