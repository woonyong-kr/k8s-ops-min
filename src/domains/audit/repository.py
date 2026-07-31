"""audit 도메인 repository — 불변 감사 로그."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, case, func, or_, select, union
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domains.audit.models import AuditLog
from domains.rca.models import RcaReport
from domains.target.models import EvidenceWindow
from packages.contracts.event_bus.interfaces import EventEnvelope, JsonObject
from packages.storage.engine import DatabaseConnection

MAX_SUMMARY_STRING_LENGTH = 500
AUDIT_TIMELINE_SUMMARY_FIELDS = (
    "incident_id",
    "cluster_id",
    "namespace",
    "resource_kind",
    "resource_name",
    "symptom",
    "status",
    "action",
    "action_id",
    "candidate_id",
    "selected_candidate_id",
    "command_id",
    "plan_id",
    "workflow_run_id",
    "pr_url",
    "provider",
    "mode",
    "repository_id",
    "binding_id",
    "application_id",
    "environment",
    "commit_sha",
    "patch_sha256",
    "evidence_ref",
    "confidence",
    "reason_code",
    "reason",
    "diagnosis",
    "next_action",
    "summary",
)


def audit_correlation_clusters_statement(
    workspace_id: str,
    correlation_id: str,
) -> Any:
    report = RcaReport.__table__
    evidence = EvidenceWindow.__table__
    return union(
        select(report.c.cluster_id).where(
            report.c.workspace_id == workspace_id,
            report.c.correlation_id == correlation_id,
        ),
        select(evidence.c.cluster_id).where(
            evidence.c.workspace_id == workspace_id,
            evidence.c.correlation_id == correlation_id,
        ),
    ).limit(2)


def audit_log_row(evt: EventEnvelope) -> JsonObject:
    """감사 로그 insert 행 매핑 — 단건·벌크가 같은 매핑을 공유함(단일 출처)."""
    return {
        "event_id": evt.event_id,
        "subject": evt.subject,
        "source": evt.source,
        "correlation_id": evt.correlation_id,
        "causation_id": evt.causation_id,
        "workspace_id": evt.workspace_id,
        "payload": evt.payload,
        "event_created_at": _event_created_at(evt.created_at),
    }


def _event_created_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


class AuditLogRepository(DatabaseConnection):
    def append_audit_logs(self, rows: list[JsonObject]) -> None:
        """감사 로그 벌크 INSERT — executemany 스타일로 한 문장에 적재함."""
        if not rows:
            return
        with self.connection() as conn:
            conn.execute(pg_insert(AuditLog.__table__), rows)

    def append_audit_log(self, evt: EventEnvelope) -> None:
        # 단건도 벌크 경로로 위임해 insert 매핑을 한 곳으로 유지함
        self.append_audit_logs([audit_log_row(evt)])

    def list_audit_timeline(
        self,
        workspace_id: str,
        correlation_id: str,
        authorized_cluster_id: str,
        *,
        cursor: tuple[datetime, int] | None = None,
        limit: int = 51,
    ) -> list[JsonObject]:
        """신뢰 workspace와 correlation의 감사 이벤트를 안정적인 keyset 순서로 조회."""
        table = AuditLog.__table__
        ownership = audit_correlation_clusters_statement(
            workspace_id,
            correlation_id,
        ).cte("audit_correlation_clusters")
        statement = select(
            table.c.id,
            table.c.event_id,
            table.c.subject,
            table.c.source,
            table.c.causation_id,
            table.c.created_at,
            *[
                case(
                    (
                        func.jsonb_typeof(table.c.payload[field]).in_(
                            ("string", "number", "boolean")
                        ),
                        func.left(table.c.payload[field].astext, MAX_SUMMARY_STRING_LENGTH),
                    ),
                    else_=None,
                ).label(field)
                for field in AUDIT_TIMELINE_SUMMARY_FIELDS
            ],
        ).where(
            table.c.workspace_id == workspace_id,
            table.c.correlation_id == correlation_id,
            select(func.count()).select_from(ownership).scalar_subquery() == 1,
            select(ownership.c.cluster_id).limit(1).scalar_subquery() == authorized_cluster_id,
        )
        if cursor is not None:
            created_at, row_id = cursor
            statement = statement.where(
                or_(
                    table.c.created_at > created_at,
                    and_(table.c.created_at == created_at, table.c.id > row_id),
                )
            )
        statement = statement.order_by(table.c.created_at.asc(), table.c.id.asc()).limit(limit)
        with self.connection() as conn:
            return [dict(row) for row in conn.execute(statement).mappings().all()]

    def list_audit_correlation_cluster_ids(
        self,
        workspace_id: str,
        correlation_id: str,
    ) -> list[str | None]:
        """권위 RCA report/evidence에 기록된 distinct cluster를 최대 2개 조회한다."""
        statement = audit_correlation_clusters_statement(workspace_id, correlation_id)
        with self.connection() as conn:
            return list(conn.execute(statement).scalars().all())

    def delete_audit_logs_older_than(self, cutoff: datetime, *, limit: int = 1000) -> int:
        """감사 로그를 보존 기간 이후 배치 삭제한다."""
        table = AuditLog.__table__
        expired = (
            select(table.c.id)
            .where(table.c.created_at < cutoff)
            .order_by(table.c.id)
            .limit(limit)
            .cte("expired_audit_log")
        )
        statement = table.delete().where(table.c.id.in_(select(expired.c.id))).returning(table.c.id)
        with self.connection() as conn:
            return len(conn.execute(statement).all())
