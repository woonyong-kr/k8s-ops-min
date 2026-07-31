"""dashboard read model repository.

이 도메인은 이벤트를 새로 발행하지 않음. 이미 흐른 RCA/command/safe_pr 이벤트를
화면에서 바로 읽기 좋은 timeline row로 투영.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection
from datetime import timedelta
from typing import Any

from sqlalchemy import Float, Select, Text, and_, case, cast, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domains.dashboard.models import MetricQueryPreset, MetricWidget, RcaTimeline
from domains.dashboard.ready_stream import DashboardReadySnapshot
from domains.inventory.models import (
    ClusterInventoryResourceRecord,
    ClusterInventorySnapshotRecord,
)
from domains.rca.models import RcaReport
from domains.rca.timeline import issue_presentation_severity
from packages.contracts.event_bus.interfaces import EventEnvelope, JsonObject
from packages.contracts.event_bus.subjects import EventSubject
from packages.storage.engine import DatabaseConnection

Path = tuple[str, ...]

ROOT_CAUSE_PATHS: tuple[Path, ...] = (
    ("root_cause",),
    ("rca_detail", "root_cause"),
    ("diagnostics", "root_cause"),
    ("details", "plan", "candidates", "0", "draft", "params", "root_cause"),
    ("plan", "candidates", "0", "draft", "params", "root_cause"),
    ("selected", "draft", "params", "root_cause"),
)
CONFIDENCE_PATHS: tuple[Path, ...] = (
    ("rca_detail", "confidence"),
    ("confidence",),
    ("details", "plan", "candidates", "0", "draft", "params", "confidence"),
    ("plan", "candidates", "0", "draft", "params", "confidence"),
    ("selected", "draft", "params", "confidence"),
)
SITUATION_SUMMARY_PATHS: tuple[Path, ...] = (
    ("narrative", "executive_summary"),
    ("incident", "summary"),
    ("details", "plan", "summary"),
    ("plan", "summary"),
    ("summary",),
)
RECOMMENDED_ACTION_SUMMARY_PATHS: tuple[Path, ...] = (
    ("narrative", "recommended_action"),
    ("details", "approval_summary", "recommended_candidate", "title"),
    ("details", "plan", "candidates", "0", "title"),
    ("plan", "candidates", "0", "title"),
    ("selected", "title"),
    ("details", "approval_summary", "recommended_candidate", "description"),
    ("details", "plan", "candidates", "0", "description"),
    ("plan", "candidates", "0", "description"),
    ("selected", "description"),
)
EVIDENCE_SUMMARY_PATHS: tuple[Path, ...] = (
    ("rca_detail", "evidence_summary"),
    ("details", "plan", "candidates", "0", "draft", "reason"),
    ("plan", "candidates", "0", "draft", "reason"),
    ("evaluations", "0", "reason"),
)
EVIDENCE_BUNDLE_SUMMARY_PATHS: tuple[Path, ...] = (
    ("rca_detail", "evidence_bundle_summary"),
    ("details", "plan", "summary"),
    ("plan", "summary"),
    ("incident", "summary"),
)

RCA_TIMELINE_STATUS_BY_SUBJECT: dict[str, str] = {
    EventSubject.CLUSTER_EVIDENCE_RECEIVED.value: "evidence_received",
    EventSubject.EVIDENCE_BUILT.value: "evidence_built",
    EventSubject.INCIDENT_DETECTED.value: "incident_detected",
    EventSubject.EVIDENCE_BUNDLE_BUILT.value: "evidence_bundled",
    EventSubject.RCA_RULE_MISSING.value: "rule_missing",
    EventSubject.RCA_BACKLOG_ITEM_CREATED.value: "backlog_created",
    EventSubject.RCA_AI_FALLBACK_REQUESTED.value: "ai_fallback_requested",
    EventSubject.RCA_CANDIDATES_PLANNED.value: "rca_planned",
    EventSubject.RCA_CANDIDATES_EVALUATED.value: "rca_evaluated",
    EventSubject.RCA_ANALYSIS_BLOCKED.value: "analysis_blocked",
    EventSubject.RCA_COMPLETED.value: "rca_completed",
    EventSubject.RCA_FOLLOWUP_REQUIRED.value: "followup_required",
    EventSubject.RCA_ACTION_REQUIRED.value: "action_required",
    EventSubject.RECOVERY_PLANNED.value: "recovery_planned",
    EventSubject.RECOVERY_SELECTION_REQUESTED.value: "selection_required",
    EventSubject.RECOVERY_ACTION_SELECTED.value: "recovery_selected",
    EventSubject.RECOVERY_PR_TRACKED.value: "pr_open",
    EventSubject.RECOVERY_PR_MERGED.value: "deploy_pending",
    EventSubject.RECOVERY_VERIFICATION_STARTED.value: "verification_pending",
    EventSubject.RECOVERY_VERIFICATION_UPDATED.value: "verification_pending",
    EventSubject.RECOVERY_VERIFICATION_FAILED.value: "failed",
    EventSubject.INCIDENT_RESOLVED.value: "incident_resolved",
    EventSubject.APPROVAL_RECOMMENDED.value: "approval_recommended",
    EventSubject.COMMAND_REQUESTED.value: "command_requested",
    EventSubject.COMMAND_DISPATCHED.value: "command_dispatched",
    EventSubject.COMMAND_QUEUED_FOR_AGENT.value: "command_queued",
    EventSubject.COMMAND_COMPLETED.value: "command_completed",
    EventSubject.COMMAND_REJECTED.value: "command_rejected",
    EventSubject.SAFE_PR_REQUESTED.value: "pr_requested",
    EventSubject.SAFE_PR_PATCH_PREPARED.value: "pr_patch_prepared",
    EventSubject.DIFF_EXPLAINED.value: "pr_diff_explained",
    EventSubject.SAFE_PR_READY_FOR_CREATION.value: "pr_ready_for_creation",
    EventSubject.SAFE_PR_CREATED.value: "pr_created",
    EventSubject.SAFE_PR_FAILED.value: "pr_failed",
}

PRE_INCIDENT_STATUSES: tuple[str, ...] = (
    "evidence_received",
    "evidence_built",
)
# 장애가 실제로 탐지된 뒤 조치가 끝나기 전까지만 open으로 계산한다. 새 전처리 상태가
# 추가돼도 자동으로 인시던트가 되지 않도록 양수 allowlist를 유지한다.
OPEN_INCIDENT_STATUSES: tuple[str, ...] = (
    "incident_detected",
    "evidence_bundled",
    "rule_missing",
    "backlog_created",
    "ai_fallback_requested",
    "rca_planned",
    "rca_evaluated",
    "analysis_blocked",
    "rca_completed",
    "followup_required",
    "action_required",
    "recovery_planned",
    "selection_required",
    "recovery_selected",
    "approval_recommended",
    "command_requested",
    "command_dispatched",
    "command_queued",
    "pr_requested",
    "pr_patch_prepared",
    "pr_diff_explained",
    "pr_ready_for_creation",
    "pr_created",
    "pr_open",
    "deploy_pending",
    "verification_pending",
)
DEFAULT_OPEN_INCIDENT_EXPIRE_DAYS = 3
DEFAULT_OPEN_INCIDENT_EXPIRE_LIMIT = 500
DEFAULT_PRE_INCIDENT_RETENTION_HOURS = 24
DEFAULT_PRE_INCIDENT_RETENTION_LIMIT = 1000
DEFAULT_EPHEMERAL_INCIDENT_RESOLVE_MINUTES = 5
DEFAULT_EPHEMERAL_INCIDENT_RESOLVE_LIMIT = 500
TERMINAL_INCIDENT_STATUSES: tuple[str, ...] = (
    "incident_resolved",
    "incident_expired",
)
RECOVERY_PIN_STATUSES: tuple[str, ...] = (
    "recovery_planned",
    "selection_required",
    "approval_recommended",
    "recovery_selected",
    "pr_requested",
    "pr_patch_prepared",
    "pr_diff_explained",
    "pr_ready_for_creation",
    "pr_created",
    "pr_open",
    "deploy_pending",
    "verification_pending",
)
WEAK_REANALYSIS_STATUSES: tuple[str, ...] = (
    "incident_detected",
    "evidence_bundled",
    "rca_planned",
    "rca_evaluated",
    "rca_completed",
    "followup_required",
)
EPHEMERAL_INCIDENT_RESOURCE_KINDS: tuple[str, ...] = ("Pod", "ReplicaSet")
EPHEMERAL_INCIDENT_RESOURCE_KINDS_NORMALIZED = tuple(
    kind.casefold() for kind in EPHEMERAL_INCIDENT_RESOURCE_KINDS
)
MAX_RCA_REPORT_SUMMARY_BATCH = 101
VALID_CONFIDENCE_PATTERN = r"^(?:0(?:\.[0-9]+)?|1(?:\.0+)?)$"


def projected_incident_status(
    existing_status: Any,
    incoming_status: Any,
    newer_event: Any,
) -> Any:
    """Keep an unresolved recovery PIN authoritative during weak re-analysis."""

    return case(
        (existing_status == "incident_resolved", existing_status),
        (
            and_(
                existing_status.in_(RECOVERY_PIN_STATUSES),
                incoming_status.in_(WEAK_REANALYSIS_STATUSES),
            ),
            existing_status,
        ),
        (newer_event, incoming_status),
        else_=existing_status,
    )


def latest_inventory_snapshot_id_for_incident(timeline: Any) -> Any:
    snapshots = ClusterInventorySnapshotRecord.__table__
    return (
        select(snapshots.c.snapshot_id)
        .where(
            snapshots.c.workspace_id == timeline.c.workspace_id,
            snapshots.c.cluster_id == timeline.c.cluster_id,
            snapshots.c.status != "ignored_stale",
            func.coalesce(
                snapshots.c.summary["summary"]["live_inventory"].as_boolean(),
                True,
            ).is_(True),
        )
        # ix_inventory_snapshots_live_scope_latest(ws, cluster, created_at DESC,
        # snapshot_id DESC, partial live)와 정렬을 일치시킨다. collected_at 정렬은
        # 이 correlated probe 를 후보 행마다 클러스터 스냅샷 전체 top-N 정렬로 만들어
        # (live EXPLAIN: 3,507 buffers/probe vs 정렬 일치 4 buffers/0.089ms)
        # rca-timeline-janitor sweep 이 statement timeout 으로 CrashLoop 했다.
        # created_at 기준 'latest' 는 latest_inventory_snapshot·node_summary_read_model
        # 과 동일한 코드베이스 표준 정의다.
        .order_by(snapshots.c.created_at.desc(), snapshots.c.snapshot_id.desc())
        .limit(1)
        .correlate(timeline)
        .scalar_subquery()
    )


def live_unhealthy_ephemeral_resource_exists(timeline: Any) -> Any:
    inventory = ClusterInventoryResourceRecord.__table__
    return (
        select(inventory.c.inventory_key)
        .where(
            inventory.c.workspace_id == timeline.c.workspace_id,
            inventory.c.cluster_id == timeline.c.cluster_id,
            func.lower(inventory.c.kind) == func.lower(timeline.c.incident_resource_kind),
            func.coalesce(inventory.c.namespace, "")
            == func.coalesce(timeline.c.incident_namespace, ""),
            inventory.c.name == timeline.c.incident_resource_name,
            inventory.c.snapshot_id == latest_inventory_snapshot_id_for_incident(timeline),
            inventory.c.deleted_at.is_(None),
            func.lower(inventory.c.health) == "degraded",
        )
        .correlate(timeline)
        .exists()
    )


def incident_is_live_or_non_ephemeral(timeline: Any) -> Any:
    is_ephemeral = func.lower(func.coalesce(timeline.c.incident_resource_kind, "")).in_(
        EPHEMERAL_INCIDENT_RESOURCE_KINDS_NORMALIZED
    )
    return or_(~is_ephemeral, live_unhealthy_ephemeral_resource_exists(timeline))


def _rca_timeline_response_columns(*, include_issue_severity: bool = False) -> tuple[Any, ...]:
    """화면 응답에 필요한 컬럼만 읽어 큰 payload 전송을 피한다."""
    table = RcaTimeline.__table__
    columns: tuple[Any, ...] = (
        table.c.id,
        table.c.workspace_id,
        table.c.correlation_id,
        table.c.cluster_id,
        table.c.incident_id,
        table.c.incident_namespace,
        table.c.incident_resource_kind,
        table.c.incident_resource_name,
        table.c.incident_symptom,
        table.c.evidence_ref,
        table.c.current_subject,
        table.c.status,
        effective_root_cause_column(table),
        effective_confidence_column(table),
        table.c.supporting_evidence,
        table.c.missing_evidence,
        table.c.action_route,
        table.c.command_id,
        table.c.pr_url,
        table.c.error_reason,
        table.c.payload["reason_code"].astext.label("recovery_reason_code"),
        table.c.updated_at,
    )
    if include_issue_severity:
        return (
            *columns,
            table.c.incident_occurrence_id,
            table.c.severity,
            table.c.severity_complete,
            *issue_detail_projection_columns(table),
        )
    return columns


def _payload_text_column(payload_column: Any, paths: tuple[Path, ...]) -> Any:
    values = tuple(func.nullif(payload_column[path].astext, "") for path in paths)
    return func.coalesce(*values)


def effective_root_cause_column(table: Any) -> Any:
    """Return a scalar RCA verdict for both current and historical payload shapes."""
    return func.coalesce(
        table.c.root_cause,
        _payload_text_column(table.c.payload, ROOT_CAUSE_PATHS),
    ).label("root_cause")


def effective_confidence_column(table: Any) -> Any:
    """Read the bounded numeric confidence without transferring the full payload."""
    historical_confidence = _payload_text_column(table.c.payload, CONFIDENCE_PATHS)
    safe_historical_confidence = case(
        (
            historical_confidence.op("~")(VALID_CONFIDENCE_PATTERN),
            cast(historical_confidence, Float),
        ),
        else_=None,
    )
    return func.coalesce(
        table.c.confidence,
        safe_historical_confidence,
    ).label("confidence")


def issue_detail_projection_columns(table: Any) -> tuple[Any, ...]:
    """Project operator copy from JSON scalars while keeping large evidence server-side."""
    return (
        _payload_text_column(table.c.payload, SITUATION_SUMMARY_PATHS).label(
            "situation_summary"
        ),
        _payload_text_column(table.c.payload, RECOMMENDED_ACTION_SUMMARY_PATHS).label(
            "recommended_action_summary"
        ),
        _payload_text_column(table.c.payload, EVIDENCE_SUMMARY_PATHS).label("evidence_summary"),
        _payload_text_column(table.c.payload, EVIDENCE_BUNDLE_SUMMARY_PATHS).label(
            "evidence_bundle_summary"
        ),
    )


def issue_detail_projection(payload: JsonObject) -> JsonObject:
    """Pure companion to the SQL projection, used by replay and contract tests."""
    return {
        "situation_summary": _first_string(payload, *SITUATION_SUMMARY_PATHS),
        "recommended_action_summary": _first_string(
            payload, *RECOMMENDED_ACTION_SUMMARY_PATHS
        ),
        "evidence_summary": _first_string(payload, *EVIDENCE_SUMMARY_PATHS),
        "evidence_bundle_summary": _first_string(payload, *EVIDENCE_BUNDLE_SUMMARY_PATHS),
    }


def latest_rca_issue_report_summaries_statement(
    workspace_id: str,
    correlation_ids: Collection[str],
) -> Select[Any]:
    """Build one bounded latest-report lookup after timeline deduplication."""
    report = RcaReport.__table__
    bounded_ids = tuple(
        dict.fromkeys(
            correlation_id.strip()
            for correlation_id in correlation_ids
            if isinstance(correlation_id, str) and correlation_id.strip()
        )
    )[:MAX_RCA_REPORT_SUMMARY_BATCH]
    summary = func.jsonb_strip_nulls(
        func.jsonb_build_object(
            "executive_summary",
            report.c.payload["narrative"]["executive_summary"].astext,
            "recommended_action",
            report.c.payload["narrative"]["recommended_action"].astext,
            "evidence_summary",
            report.c.payload["rca_detail"]["evidence_summary"].astext,
            "evidence_bundle_summary",
            report.c.payload["rca_detail"]["evidence_bundle_summary"].astext,
            "supporting_evidence",
            report.c.supporting_evidence,
            "missing_evidence",
            report.c.missing_evidence,
        )
    ).label("rca_issue_report_summary")
    return (
        select(
            report.c.correlation_id,
            summary,
        )
        .where(
            report.c.workspace_id == workspace_id,
            report.c.correlation_id.in_(bounded_ids),
        )
        .distinct(report.c.correlation_id)
        .order_by(
            report.c.correlation_id,
            report.c.created_at.desc(),
            report.c.id.desc(),
        )
    )


def fetch_latest_rca_issue_report_summaries(
    conn: Any,
    *,
    workspace_id: str,
    correlation_ids: Collection[str],
) -> dict[str, JsonObject]:
    """Fetch newest operator-safe report prose with one bounded query."""
    bounded_ids = tuple(
        dict.fromkeys(
            correlation_id.strip()
            for correlation_id in correlation_ids
            if isinstance(correlation_id, str) and correlation_id.strip()
        )
    )[:MAX_RCA_REPORT_SUMMARY_BATCH]
    if not bounded_ids:
        return {}
    rows = conn.execute(
        latest_rca_issue_report_summaries_statement(workspace_id, bounded_ids)
    ).mappings()
    summaries: dict[str, JsonObject] = {}
    for row in rows:
        summary = row.get("rca_issue_report_summary")
        if isinstance(summary, dict):
            summaries[str(row["correlation_id"])] = summary
    return summaries


def _apply_latest_rca_issue_report_summaries(
    items: list[JsonObject],
    summaries: dict[str, JsonObject],
) -> None:
    for item in items:
        correlation_id = item.get("correlation_id")
        item["rca_issue_report_summary"] = (
            summaries.get(correlation_id) if isinstance(correlation_id, str) else None
        )


class DashboardRepository(DatabaseConnection):
    table = RcaTimeline.__table__

    def latest_dashboard_ready_snapshot(
        self,
        *,
        workspace_id: str,
        cluster_id: str,
    ) -> DashboardReadySnapshot | None:
        table = ClusterInventorySnapshotRecord.__table__
        statement = (
            select(table.c.snapshot_id, table.c.created_at)
            .where(
                table.c.workspace_id == workspace_id,
                table.c.cluster_id == cluster_id,
                table.c.status != "ignored_stale",
            )
            .order_by(table.c.created_at.desc(), table.c.snapshot_id.desc())
            .limit(1)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return (
            DashboardReadySnapshot(
                snapshot_id=str(row["snapshot_id"]),
                created_at=row["created_at"],
            )
            if row is not None
            else None
        )

    def list_dashboard_ready_snapshots(
        self,
        *,
        workspace_id: str,
        cluster_id: str,
        after: DashboardReadySnapshot,
        limit: int,
    ) -> tuple[DashboardReadySnapshot, ...]:
        effective_limit = max(1, min(int(limit), 100))
        table = ClusterInventorySnapshotRecord.__table__
        statement = (
            select(table.c.snapshot_id, table.c.created_at)
            .where(
                table.c.workspace_id == workspace_id,
                table.c.cluster_id == cluster_id,
                table.c.status != "ignored_stale",
                or_(
                    table.c.created_at > after.created_at,
                    and_(
                        table.c.created_at == after.created_at,
                        table.c.snapshot_id > after.snapshot_id,
                    ),
                ),
            )
            .order_by(table.c.created_at.asc(), table.c.snapshot_id.asc())
            .limit(effective_limit)
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return tuple(
            DashboardReadySnapshot(
                snapshot_id=str(row["snapshot_id"]),
                created_at=row["created_at"],
            )
            for row in rows
        )

    def list_metric_query_presets(self, workspace_id: str, cluster_id: str) -> list[JsonObject]:
        table = MetricQueryPreset.__table__
        statement: Select[Any] = (
            select(table)
            .where(table.c.workspace_id == workspace_id, table.c.cluster_id == cluster_id)
            .order_by(table.c.updated_at.desc(), table.c.name.asc())
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [serialize_metric_query_preset(row) for row in rows]

    def get_metric_query_preset(
        self,
        workspace_id: str,
        cluster_id: str,
        preset_id: str,
    ) -> JsonObject | None:
        table = MetricQueryPreset.__table__
        statement: Select[Any] = select(table).where(
            table.c.workspace_id == workspace_id,
            table.c.cluster_id == cluster_id,
            table.c.preset_id == preset_id,
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return serialize_metric_query_preset(row) if row is not None else None

    def upsert_metric_query_preset(
        self,
        row: JsonObject,
        *,
        conflict_by_name: bool = False,
    ) -> JsonObject:
        table = MetricQueryPreset.__table__
        insert = pg_insert(table).values(**row, updated_at=func.now())
        excluded = insert.excluded
        updates = {
            "description": excluded.description,
            "source": excluded.source,
            "query": excluded.query,
            "range_seconds": excluded.range_seconds,
            "step_seconds": excluded.step_seconds,
            "unit": excluded.unit,
            "created_by": excluded.created_by,
            "metadata": excluded["metadata"],
            "updated_at": func.now(),
        }
        if not conflict_by_name:
            updates["name"] = excluded.name
        statement = insert.on_conflict_do_update(
            index_elements=(
                [table.c.workspace_id, table.c.cluster_id, table.c.name]
                if conflict_by_name
                else [table.c.preset_id]
            ),
            set_=updates,
        ).returning(table)
        with self.connection() as conn:
            saved = conn.execute(statement).mappings().one()
        return serialize_metric_query_preset(saved)

    def delete_metric_query_preset(
        self,
        workspace_id: str,
        cluster_id: str,
        preset_id: str,
    ) -> bool:
        table = MetricQueryPreset.__table__
        statement = (
            delete(table)
            .where(
                table.c.workspace_id == workspace_id,
                table.c.cluster_id == cluster_id,
                table.c.preset_id == preset_id,
            )
            .returning(table.c.preset_id)
        )
        with self.connection() as conn:
            return conn.execute(statement).first() is not None

    def list_metric_widgets(self, workspace_id: str, cluster_id: str) -> list[JsonObject]:
        table = MetricWidget.__table__
        statement: Select[Any] = (
            select(table)
            .where(table.c.workspace_id == workspace_id, table.c.cluster_id == cluster_id)
            .order_by(table.c.updated_at.desc(), table.c.title.asc())
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [serialize_metric_widget(row) for row in rows]

    def get_metric_widget(
        self,
        workspace_id: str,
        cluster_id: str,
        widget_id: str,
    ) -> JsonObject | None:
        table = MetricWidget.__table__
        statement: Select[Any] = select(table).where(
            table.c.workspace_id == workspace_id,
            table.c.cluster_id == cluster_id,
            table.c.widget_id == widget_id,
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return serialize_metric_widget(row) if row is not None else None

    def upsert_metric_widget(
        self,
        row: JsonObject,
        *,
        conflict_by_title: bool = False,
    ) -> JsonObject:
        table = MetricWidget.__table__
        insert = pg_insert(table).values(**row, updated_at=func.now())
        excluded = insert.excluded
        updates = {
            "query_preset_id": excluded.query_preset_id,
            "kind": excluded.kind,
            "position": excluded.position,
            "settings": excluded.settings,
            "created_by": excluded.created_by,
            "updated_at": func.now(),
        }
        if not conflict_by_title:
            updates["title"] = excluded.title
        statement = insert.on_conflict_do_update(
            index_elements=(
                [table.c.workspace_id, table.c.cluster_id, table.c.title]
                if conflict_by_title
                else [table.c.widget_id]
            ),
            set_=updates,
        ).returning(table)
        with self.connection() as conn:
            saved = conn.execute(statement).mappings().one()
        return serialize_metric_widget(saved)

    def delete_metric_widget(
        self,
        workspace_id: str,
        cluster_id: str,
        widget_id: str,
    ) -> bool:
        table = MetricWidget.__table__
        statement = (
            delete(table)
            .where(
                table.c.workspace_id == workspace_id,
                table.c.cluster_id == cluster_id,
                table.c.widget_id == widget_id,
            )
            .returning(table.c.widget_id)
        )
        with self.connection() as conn:
            return conn.execute(statement).first() is not None

    def upsert_rca_timeline(self, row: JsonObject) -> None:
        """correlation 흐름을 장애 발생 회차에 묶어 최신 상태로 갱신."""
        table = RcaTimeline.__table__
        projected = dict(row)
        with self.connection() as conn:
            occurrence_id = _resolve_incident_occurrence_id(conn, projected)
            if occurrence_id is not None:
                projected["incident_occurrence_id"] = occurrence_id
            insert = pg_insert(table).values(**projected, updated_at=func.now())
            preserve_when_missing = (
                "cluster_id",
                "incident_id",
                "incident_namespace",
                "incident_resource_kind",
                "incident_resource_name",
                "incident_symptom",
                "incident_logical_key",
                "incident_occurrence_id",
                "evidence_ref",
                "root_cause",
                "confidence",
                "supporting_evidence",
                "missing_evidence",
                "action_route",
                "command_id",
                "pr_url",
            )
            updates = {
                key: func.coalesce(getattr(insert.excluded, key), getattr(table.c, key))
                for key in preserve_when_missing
            }
            for value_name in (
                "severity",
                "category",
                "environment",
                "application_ids",
                "labels",
            ):
                complete_name = f"{value_name}_complete"
                existing_value = getattr(table.c, value_name)
                incoming_value = getattr(insert.excluded, value_name)
                existing_complete = getattr(table.c, complete_name)
                incoming_complete = getattr(insert.excluded, complete_name)
                updates[value_name] = case(
                    (existing_complete.is_(True), existing_value),
                    (incoming_complete.is_(True), incoming_value),
                    (existing_value.is_(None), incoming_value),
                    else_=existing_value,
                )
                updates[complete_name] = existing_complete | incoming_complete

            newer_event = or_(
                insert.excluded.last_event_at > table.c.last_event_at,
                and_(
                    insert.excluded.last_event_at == table.c.last_event_at,
                    insert.excluded.last_event_id > table.c.last_event_id,
                ),
            )
            updates.update(
                current_subject=case(
                    (newer_event, insert.excluded.current_subject),
                    else_=table.c.current_subject,
                ),
                # Inventory recovery is the incident lifecycle authority. A late RCA
                # completion replay may enrich the verdict, but must not reopen an
                # already recovered issue in the operator queue.
                status=projected_incident_status(
                    table.c.status,
                    insert.excluded.status,
                    newer_event,
                ),
                error_reason=case(
                    (newer_event, insert.excluded.error_reason),
                    else_=table.c.error_reason,
                ),
                last_event_id=case(
                    (newer_event, insert.excluded.last_event_id),
                    else_=table.c.last_event_id,
                ),
                last_event_at=case(
                    (newer_event, insert.excluded.last_event_at),
                    else_=table.c.last_event_at,
                ),
                payload=case((newer_event, insert.excluded.payload), else_=table.c.payload),
                updated_at=case((newer_event, func.now()), else_=table.c.updated_at),
            )
            statement = insert.on_conflict_do_update(
                index_elements=[table.c.workspace_id, table.c.correlation_id],
                set_=updates,
            )
            conn.execute(statement)
            incoming_status = str(projected.get("status") or "")
            if occurrence_id is not None and incoming_status in TERMINAL_INCIDENT_STATUSES:
                conn.execute(
                    table.update()
                    .where(
                        table.c.workspace_id == projected["workspace_id"],
                        table.c.incident_occurrence_id == occurrence_id,
                    )
                    .values(status=incoming_status)
                )

    def list_rca_timeline(
        self,
        workspace_id: str,
        allowed_cluster_ids: set[str] | None,
        limit: int = 50,
    ) -> list[JsonObject]:
        return self._list_rca_timeline_projection(
            workspace_id,
            allowed_cluster_ids,
            limit,
            include_issue_severity=False,
        )

    def list_rca_issues(
        self,
        workspace_id: str,
        allowed_cluster_ids: set[str] | None,
        limit: int = 50,
    ) -> list[JsonObject]:
        """Return the additive Issues queue projection without altering timeline JSON."""
        return self._list_rca_timeline_projection(
            workspace_id,
            allowed_cluster_ids,
            limit,
            include_issue_severity=True,
        )

    def list_resource_issues(
        self,
        workspace_id: str,
        cluster_id: str,
        *,
        namespace: str | None,
        resource_kind: str,
        resource_name: str,
        limit: int = 26,
    ) -> list[JsonObject]:
        """Return an exact, bounded resource projection with server-owned onset evidence."""

        bounded_limit = max(1, min(limit, 101))
        scan_limit = min(max(bounded_limit * 50, bounded_limit), 5000)
        table = RcaTimeline.__table__
        statement: Select[Any] = (
            select(
                *_rca_timeline_response_columns(include_issue_severity=True),
                table.c.created_at,
            )
            .where(
                table.c.workspace_id == workspace_id,
                table.c.cluster_id == cluster_id,
                table.c.incident_id.is_not(None),
                func.lower(table.c.incident_resource_kind) == resource_kind.casefold(),
                func.coalesce(table.c.incident_namespace, "") == (namespace or ""),
                table.c.incident_resource_name == resource_name,
            )
            .order_by(table.c.updated_at.desc(), table.c.id.desc())
            .limit(scan_limit)
        )
        statement = _exclude_non_incident_detection(statement)
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        seen: set[str] = set()
        items: list[JsonObject] = []
        for row in rows:
            item = serialize_timeline_row(row)
            item.update(issue_severity_projection(row))
            key = incident_occurrence_key(item)
            if key in seen:
                continue
            seen.add(key)
            created_at = item.get("created_at")
            if not isinstance(created_at, str) or not created_at:
                continue
            item["onset"] = {
                "first_observed_at": created_at,
                "source": "timeline_created_at",
                "timing_kind": None,
                "timing_availability": "unavailable",
                "timing_reason_code": "health_transition_evidence_unavailable",
            }
            items.append(item)
            if len(items) >= bounded_limit:
                break
        with self.connection() as conn:
            summaries = fetch_latest_rca_issue_report_summaries(
                conn,
                workspace_id=workspace_id,
                correlation_ids=[
                    str(item["correlation_id"])
                    for item in items
                    if item.get("correlation_id")
                ],
            )
        _apply_latest_rca_issue_report_summaries(items, summaries)
        return items

    def _list_rca_timeline_projection(
        self,
        workspace_id: str,
        allowed_cluster_ids: set[str] | None,
        limit: int,
        *,
        include_issue_severity: bool,
    ) -> list[JsonObject]:
        if allowed_cluster_ids == set():
            return []
        bounded_limit = max(1, min(limit, 100))
        # A polling evidence source can produce several correlations for the same
        # still-active symptom. Fetch enough rows to collapse those correlations
        # without starving other incidents from the operator list.
        scan_limit = min(max(bounded_limit * 50, bounded_limit), 5000)
        statement: Select[Any] = (
            select(*_rca_timeline_response_columns(include_issue_severity=include_issue_severity))
            .where(
                RcaTimeline.workspace_id == workspace_id,
                # Command/approval subjects are shared by incident recovery and
                # cluster lifecycle workflows.  Only rows that were attached to
                # an actual incident belong on the operator incident timeline.
                RcaTimeline.incident_id.is_not(None),
            )
            .order_by(RcaTimeline.updated_at.desc())
            .limit(scan_limit)
        )
        statement = _exclude_non_incident_detection(statement)
        statement = _apply_cluster_filter(statement, allowed_cluster_ids)
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        seen: set[str] = set()
        items: list[JsonObject] = []
        for row in rows:
            item = serialize_timeline_row(row)
            if include_issue_severity:
                item.update(issue_severity_projection(row))
            key = incident_occurrence_key(item)
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
            if len(items) >= bounded_limit:
                break
        if include_issue_severity:
            with self.connection() as conn:
                summaries = fetch_latest_rca_issue_report_summaries(
                    conn,
                    workspace_id=workspace_id,
                    correlation_ids=[
                        str(item["correlation_id"])
                        for item in items
                        if item.get("correlation_id")
                    ],
                )
            _apply_latest_rca_issue_report_summaries(items, summaries)
        return items

    def get_rca_timeline_item(
        self,
        workspace_id: str,
        incident_id: str,
        allowed_cluster_ids: set[str] | None,
    ) -> JsonObject | None:
        if allowed_cluster_ids == set():
            return None
        statement: Select[Any] = (
            select(*_rca_timeline_response_columns())
            .where(
                RcaTimeline.workspace_id == workspace_id,
                RcaTimeline.incident_id == incident_id,
            )
            .order_by(RcaTimeline.updated_at.desc())
            .limit(1)
        )
        statement = _exclude_non_incident_detection(statement)
        statement = _apply_cluster_filter(statement, allowed_cluster_ids)
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return serialize_timeline_row(row) if row is not None else None

    def count_open_rca_incidents(
        self,
        workspace_id: str,
        allowed_cluster_ids: set[str] | None = None,
    ) -> dict[str, int]:
        """fleet 롤업용 — 클러스터별 열린 logical incident 수. 빈 허용 집합이면 {}."""
        if allowed_cluster_ids is not None and not allowed_cluster_ids:
            return {}
        table = RcaTimeline.__table__
        occurrence_key = rca_timeline_incident_occurrence_key_expression()
        statement: Select[Any] = select(
            table.c.cluster_id,
            func.count(func.distinct(occurrence_key)).label("open_incidents"),
        ).where(
            table.c.workspace_id == workspace_id,
            table.c.incident_id.is_not(None),
            table.c.cluster_id.is_not(None),
            table.c.incident_logical_key.is_not(None),
            table.c.status.in_(OPEN_INCIDENT_STATUSES),
            incident_is_live_or_non_ephemeral(table),
        )
        statement = _exclude_non_incident_detection(statement)
        statement = _apply_cluster_filter(statement, allowed_cluster_ids)
        statement = statement.group_by(table.c.cluster_id)
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return {str(row["cluster_id"]): int(row["open_incidents"]) for row in rows}

    def list_open_rca_incidents(
        self,
        workspace_id: str,
        cluster_id: str,
        *,
        limit: int = 20,
    ) -> list[JsonObject]:
        """드릴다운용 — 한 클러스터의 열린 logical incident 요약(최신 갱신순)."""
        bounded_limit = max(1, min(limit, 100))
        scan_limit = min(max(bounded_limit * 50, bounded_limit), 5000)
        statement: Select[Any] = (
            select(RcaTimeline.__table__)
            .where(
                RcaTimeline.workspace_id == workspace_id,
                RcaTimeline.cluster_id == cluster_id,
                RcaTimeline.incident_id.is_not(None),
                RcaTimeline.status.in_(OPEN_INCIDENT_STATUSES),
                incident_is_live_or_non_ephemeral(RcaTimeline.__table__),
            )
            .order_by(RcaTimeline.updated_at.desc())
            .limit(scan_limit)
        )
        statement = _exclude_non_incident_detection(statement)
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        seen: set[str] = set()
        items: list[JsonObject] = []
        for row in rows:
            item = serialize_timeline_row(row)
            key = incident_occurrence_key(item)
            if key in seen:
                continue
            seen.add(key)
            items.append(open_incident_summary(item))
            if len(items) >= bounded_limit:
                break
        return items

    def latest_open_incidents_by_resource(
        self,
        workspace_id: str,
        cluster_id: str,
        *,
        resource_kind: str,
        resources: set[tuple[str, str]],
    ) -> dict[tuple[str, str], str]:
        """리소스별 최신 열린 인시던트 correlation — 드릴다운 타일 링크용."""
        if not resources:
            return {}
        bounded = sorted(resources)[:1000]
        filters = [
            and_(
                RcaTimeline.incident_namespace == namespace,
                RcaTimeline.incident_resource_name == name,
            )
            for namespace, name in bounded
        ]
        statement: Select[Any] = (
            select(
                RcaTimeline.incident_namespace,
                RcaTimeline.incident_resource_name,
                RcaTimeline.correlation_id,
            )
            .where(
                RcaTimeline.workspace_id == workspace_id,
                RcaTimeline.cluster_id == cluster_id,
                func.lower(RcaTimeline.incident_resource_kind) == resource_kind.lower(),
                RcaTimeline.incident_id.is_not(None),
                RcaTimeline.status.in_(OPEN_INCIDENT_STATUSES),
                incident_is_live_or_non_ephemeral(RcaTimeline.__table__),
                or_(*filters),
            )
            .order_by(
                RcaTimeline.incident_namespace,
                RcaTimeline.incident_resource_name,
                RcaTimeline.updated_at.desc(),
            )
        )
        statement = _exclude_non_incident_detection(statement)
        latest: dict[tuple[str, str], str] = {}
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        for row in rows:
            key = (str(row["incident_namespace"]), str(row["incident_resource_name"]))
            latest.setdefault(key, str(row["correlation_id"]))
        return latest

    def expire_stale_open_rca_incidents(
        self,
        max_age_days: int = DEFAULT_OPEN_INCIDENT_EXPIRE_DAYS,
        limit: int = DEFAULT_OPEN_INCIDENT_EXPIRE_LIMIT,
    ) -> list[JsonObject]:
        """오래 열린 timeline row 자동 종결 — fleet 수치가 무한 누적되지 않게 한다."""
        bounded_days = max(1, int(max_age_days))
        bounded_limit = max(1, min(int(limit), DEFAULT_OPEN_INCIDENT_EXPIRE_LIMIT))
        table = RcaTimeline.__table__
        candidates = (
            select(table.c.id)
            .where(
                table.c.incident_id.is_not(None),
                table.c.cluster_id.is_not(None),
                table.c.status.in_(OPEN_INCIDENT_STATUSES),
                table.c.updated_at < func.now() - timedelta(days=bounded_days),
            )
            .order_by(table.c.updated_at.asc())
            .limit(bounded_limit)
            .with_for_update(skip_locked=True)
            .cte("stale_open_incidents")
        )
        statement = (
            table.update()
            .where(table.c.id.in_(select(candidates.c.id)))
            .values(
                status="incident_expired",
                error_reason=func.coalesce(
                    table.c.error_reason,
                    f"open incident exceeded {bounded_days} day retention window",
                ),
                updated_at=func.now(),
            )
            .returning(
                table.c.id,
                table.c.workspace_id,
                table.c.cluster_id,
                table.c.incident_id,
                table.c.correlation_id,
                table.c.status,
            )
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [dict(row) for row in rows]

    def delete_stale_pre_incident_timeline(
        self,
        retention_hours: int = DEFAULT_PRE_INCIDENT_RETENTION_HOURS,
        limit: int = DEFAULT_PRE_INCIDENT_RETENTION_LIMIT,
    ) -> int:
        """원본 evidence를 보존하면서 오래된 전처리 projection만 배치 삭제한다."""
        bounded_hours = max(1, int(retention_hours))
        bounded_limit = max(1, min(int(limit), 5000))
        table = RcaTimeline.__table__
        candidates = (
            select(table.c.id)
            .where(
                table.c.status.in_(PRE_INCIDENT_STATUSES),
                table.c.updated_at < func.now() - timedelta(hours=bounded_hours),
            )
            .order_by(table.c.updated_at.asc())
            .limit(bounded_limit)
            .with_for_update(skip_locked=True)
            .cte("stale_pre_incident_timeline")
        )
        statement = (
            delete(table).where(table.c.id.in_(select(candidates.c.id))).returning(table.c.id)
        )
        with self.connection() as conn:
            rows = conn.execute(statement).all()
        return len(rows)

    def resolve_recovered_ephemeral_incidents(
        self,
        grace_minutes: int = DEFAULT_EPHEMERAL_INCIDENT_RESOLVE_MINUTES,
        limit: int = DEFAULT_EPHEMERAL_INCIDENT_RESOLVE_LIMIT,
    ) -> list[JsonObject]:
        """사라졌거나 정상화된 Pod/ReplicaSet 인시던트를 이력을 보존하며 종결한다."""
        bounded_minutes = max(1, int(grace_minutes))
        bounded_limit = max(1, min(int(limit), 5000))
        timeline = RcaTimeline.__table__
        unhealthy_resource_exists = live_unhealthy_ephemeral_resource_exists(timeline)
        candidates = (
            select(timeline.c.id)
            .where(
                timeline.c.incident_id.is_not(None),
                timeline.c.status.in_(OPEN_INCIDENT_STATUSES),
                func.lower(timeline.c.incident_resource_kind).in_(
                    EPHEMERAL_INCIDENT_RESOURCE_KINDS_NORMALIZED
                ),
                timeline.c.updated_at < func.now() - timedelta(minutes=bounded_minutes),
                ~unhealthy_resource_exists,
            )
            .order_by(timeline.c.updated_at.asc())
            .limit(bounded_limit)
            .with_for_update(skip_locked=True)
            .cte("recovered_ephemeral_incidents")
        )
        statement = (
            timeline.update()
            .where(timeline.c.id.in_(select(candidates.c.id)))
            .values(
                status="incident_resolved",
                error_reason=func.coalesce(
                    timeline.c.error_reason,
                    "ephemeral resource is absent or healthy in current inventory",
                ),
                updated_at=func.now(),
            )
            .returning(
                timeline.c.id,
                timeline.c.workspace_id,
                timeline.c.cluster_id,
                timeline.c.incident_id,
                timeline.c.correlation_id,
                timeline.c.status,
            )
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [dict(row) for row in rows]


def open_incident_summary(row: JsonObject) -> JsonObject:
    """timeline row → 드릴다운 인시던트 요약. payload 원문은 그대로 내리지 않는다."""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return {
        "incident_id": row.get("incident_id"),
        "correlation_id": row.get("correlation_id"),
        "namespace": row.get("incident_namespace"),
        "resource_kind": row.get("incident_resource_kind"),
        "resource_name": row.get("incident_resource_name"),
        "symptom": row.get("incident_symptom")
        or _first_string(payload, ("incident", "symptom"), ("symptom",)),
        "root_cause": row.get("root_cause"),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
    }


def serialize_metric_query_preset(row: Any) -> JsonObject:
    item = dict(row)
    item["metadata"] = dict(item.get("metadata") or item.get("metadata_") or {})
    for key in ("created_at", "updated_at"):
        item[key] = _iso_or_none(item.get(key))
    return item


def serialize_metric_widget(row: Any) -> JsonObject:
    item = dict(row)
    item["position"] = dict(item.get("position") or {})
    item["settings"] = dict(item.get("settings") or {})
    for key in ("created_at", "updated_at"):
        item[key] = _iso_or_none(item.get(key))
    return item


def incident_logical_key(row: JsonObject) -> str:
    """같은 실제 장애를 묶는 key — evidence correlation 폭증을 fleet 수치에서 제거."""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    incident = payload.get("incident") if isinstance(payload, dict) else None
    if isinstance(incident, dict):
        parts = (
            row.get("cluster_id"),
            incident.get("namespace"),
            incident.get("resource_kind"),
            incident.get("resource_name"),
            incident.get("symptom"),
        )
        if any(part not in (None, "") for part in parts[1:]):
            return "|".join(str(part or "unknown") for part in parts)
    projected_key = row.get("incident_logical_key")
    if projected_key not in (None, ""):
        return str(projected_key)
    return str(row.get("incident_id") or row.get("correlation_id") or row.get("id"))


def incident_logical_key_from_projection(row: JsonObject) -> str:
    """count 쿼리용 logical key — payload 전체를 읽지 않고 같은 묶음 규칙을 적용."""
    parts = (
        row.get("cluster_id"),
        row.get("incident_namespace"),
        row.get("incident_resource_kind"),
        row.get("incident_resource_name"),
        row.get("incident_symptom"),
    )
    if any(part not in (None, "") for part in parts[1:]):
        return "|".join(str(part or "unknown") for part in parts)
    projected_key = row.get("incident_logical_key")
    if projected_key not in (None, ""):
        return str(projected_key)
    return str(row.get("incident_id") or row.get("correlation_id") or row.get("id"))


def incident_occurrence_key(row: JsonObject) -> str:
    """같은 증상의 한 발생 회차만 묶고, 종결 뒤 재발은 별도 항목으로 유지."""

    occurrence_id = row.get("incident_occurrence_id")
    if occurrence_id not in (None, ""):
        return str(occurrence_id)
    return incident_logical_key(row)


def rca_timeline_logical_incident_key_expression() -> Any:
    """SQL 집계용 logical key — 구버전 correlation key보다 정규화 projection을 우선한다."""
    table = RcaTimeline.__table__
    dimensions = (
        table.c.incident_namespace,
        table.c.incident_resource_kind,
        table.c.incident_resource_name,
        table.c.incident_symptom,
    )
    has_dimensions = or_(*(func.nullif(column, "").is_not(None) for column in dimensions))
    normalized = func.concat_ws(
        "|",
        func.coalesce(func.nullif(table.c.cluster_id, ""), "unknown"),
        *(func.coalesce(func.nullif(column, ""), "unknown") for column in dimensions),
    )
    return case(
        (has_dimensions, normalized),
        else_=func.coalesce(
            func.nullif(table.c.incident_logical_key, ""),
            table.c.incident_id,
            table.c.correlation_id,
            cast(table.c.id, Text),
        ),
    )


def rca_timeline_incident_occurrence_key_expression() -> Any:
    table = RcaTimeline.__table__
    return func.coalesce(
        func.nullif(table.c.incident_occurrence_id, ""),
        rca_timeline_logical_incident_key_expression(),
    )


def incident_occurrence_lock_key(workspace_id: str, logical_key: str) -> int:
    raw = hashlib.sha256(f"{workspace_id}\0{logical_key}".encode()).digest()[:8]
    value = int.from_bytes(raw, byteorder="big", signed=False)
    return value if value < 2**63 else value - 2**64


def _resolve_incident_occurrence_id(conn: Any, row: JsonObject) -> str | None:
    table = RcaTimeline.__table__
    workspace_id = str(row.get("workspace_id") or "").strip()
    correlation_id = str(row.get("correlation_id") or "").strip()
    if not workspace_id or not correlation_id:
        return None

    existing = conn.execute(
        select(table.c.incident_occurrence_id).where(
            table.c.workspace_id == workspace_id,
            table.c.correlation_id == correlation_id,
        )
    ).scalar_one_or_none()
    if existing:
        return str(existing)

    logical_key = str(row.get("incident_logical_key") or "").strip()
    incident_id = str(row.get("incident_id") or "").strip()
    if not logical_key or not incident_id:
        return None

    conn.execute(select(func.pg_advisory_xact_lock(incident_occurrence_lock_key(
        workspace_id,
        logical_key,
    ))))
    active = conn.execute(
        select(table.c.incident_occurrence_id)
        .where(
            table.c.workspace_id == workspace_id,
            table.c.incident_logical_key == logical_key,
            table.c.incident_occurrence_id.is_not(None),
            table.c.status.in_(OPEN_INCIDENT_STATUSES),
        )
        .order_by(table.c.updated_at.desc(), table.c.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return str(active or incident_id or correlation_id)


def timeline_update_from_event(evt: EventEnvelope) -> JsonObject | None:
    status = RCA_TIMELINE_STATUS_BY_SUBJECT.get(str(evt.subject))
    if status is None:
        return None
    # Evidence 저장 상태는 evidence 전용 조회 모델에서 관리한다. 장애가 확인되기 전
    # 샘플까지 incident timeline에 투영하면 정상 수집 주기마다 행이 무한히 늘어난다.
    if status in PRE_INCIDENT_STATUSES:
        return None

    payload = evt.payload if isinstance(evt.payload, dict) else {}
    if _is_non_incident_detection(str(evt.subject), payload):
        return None
    workspace_id = _trusted_workspace_id(evt, payload)
    if workspace_id is None:
        return None

    correlation_id = evt.correlation_id or evt.event_id
    cluster_id = _cluster_id(payload)
    incident_id = _incident_id(payload)
    projection = _incident_projection(payload, cluster_id, incident_id, correlation_id)
    raw_severity = _first_string(payload, ("severity",))
    severity = raw_severity.casefold() if raw_severity is not None else None
    raw_category = _first_string(payload, ("incident", "category"), ("category",))
    category = raw_category.casefold() if raw_category is not None else None
    is_incident_detection = str(evt.subject) == EventSubject.INCIDENT_DETECTED.value
    row: JsonObject = {
        "workspace_id": workspace_id,
        "correlation_id": correlation_id,
        "cluster_id": cluster_id,
        "incident_id": incident_id,
        **projection,
        "severity": severity if is_incident_detection else None,
        "severity_complete": is_incident_detection and severity is not None,
        "category": category if is_incident_detection else None,
        "category_complete": is_incident_detection and category is not None,
        "environment": None,
        "environment_complete": False,
        "application_ids": None,
        "application_ids_complete": False,
        "labels": None,
        "labels_complete": False,
        "evidence_ref": _evidence_ref(payload),
        "current_subject": str(evt.subject),
        "status": status,
        "root_cause": _root_cause(payload),
        "confidence": _confidence(payload),
        "supporting_evidence": _supporting_evidence(payload),
        "missing_evidence": _missing_evidence(payload),
        "action_route": _action_route(str(evt.subject), payload),
        "command_id": _command_id(payload),
        "pr_url": _pr_url(payload),
        "error_reason": _error_reason(payload),
        "last_event_id": evt.event_id,
        "last_event_at": str(evt.created_at),
        "payload": payload,
    }
    return row


def serialize_timeline_row(row: Any) -> JsonObject:
    item = dict(row)
    for key in ("created_at", "updated_at"):
        item[key] = _iso_or_none(item.get(key))
    item["supporting_evidence"] = item.get("supporting_evidence") or []
    item["missing_evidence"] = item.get("missing_evidence") or []
    return item


def issue_severity_projection(row: Any) -> JsonObject:
    """Keep source absence distinct from a verified non-queue severity tier."""
    item = dict(row)
    source_complete = item.get("severity_complete") is True
    tier = issue_presentation_severity(
        item.get("severity"),
        source_complete=source_complete,
    )
    if tier is not None:
        return {
            "issue_severity": tier,
            "severity_availability": "available",
            "severity_reason_code": None,
        }
    return {
        "issue_severity": None,
        "severity_availability": "unavailable",
        "severity_reason_code": (
            "source_incomplete" if not source_complete else "outside_two_tier_scale"
        ),
    }


def _incident_projection(
    payload: JsonObject,
    cluster_id: str | None,
    incident_id: str | None,
    correlation_id: str,
) -> JsonObject:
    """fleet 집계용 작은 projection — 큰 payload JSON 재파싱을 피한다."""
    namespace = _first_string(
        payload,
        ("incident", "namespace"),
        ("namespace",),
        ("plan", "target", "namespace"),
        ("selected", "draft", "params", "namespace"),
    )
    resource_kind = _first_string(
        payload,
        ("incident", "resource_kind"),
        ("resource", "kind"),
        ("kind",),
        ("plan", "target", "resource_kind"),
        ("selected", "draft", "params", "resource_kind"),
    )
    resource_name = _first_string(
        payload,
        ("incident", "resource_name"),
        ("resource", "name"),
        ("name",),
        ("plan", "target", "resource_name"),
        ("selected", "draft", "params", "resource_name"),
    )
    symptom = _first_string(payload, ("incident", "symptom"), ("symptom",))
    parts = (cluster_id, namespace, resource_kind, resource_name, symptom)
    if any(part not in (None, "") for part in parts[1:]):
        logical_key = "|".join(str(part or "unknown") for part in parts)
    else:
        # approval/dispatch 같은 후속 이벤트는 incident 차원을 싣지 않을 수 있다.
        # 이때 correlation fallback을 새 값으로 넣으면 upsert가 앞서 투영한 정규화 key를
        # 덮어써 같은 장애가 여러 건으로 보인다. 최초 incident 이벤트는 차원을 싣는
        # 계약이며, 구형 무차원 row의 조회 fallback은 incident_logical_key()가 담당한다.
        logical_key = None
    return {
        "incident_namespace": namespace,
        "incident_resource_kind": resource_kind,
        "incident_resource_name": resource_name,
        "incident_symptom": symptom,
        "incident_logical_key": logical_key,
    }


def _apply_cluster_filter(
    statement: Select[Any], allowed_cluster_ids: set[str] | None
) -> Select[Any]:
    if allowed_cluster_ids is None:
        return statement
    return statement.where(RcaTimeline.cluster_id.in_(allowed_cluster_ids))


def _exclude_non_incident_detection(statement: Select[Any]) -> Select[Any]:
    """정상 샘플(detected=false)은 RCA timeline/incident 조회에서 제외한다."""
    table = RcaTimeline.__table__
    return statement.where(
        or_(
            table.c.current_subject != EventSubject.INCIDENT_DETECTED.value,
            table.c.payload["detected"].as_boolean().is_(True),
        )
    )


def _is_non_incident_detection(subject: str, payload: JsonObject) -> bool:
    return subject == EventSubject.INCIDENT_DETECTED.value and payload.get("detected") is not True


def _trusted_workspace_id(evt: EventEnvelope, payload: JsonObject) -> str | None:
    """Use authenticated envelope tenancy; payload may only corroborate it."""
    envelope_workspace_id = str(evt.workspace_id or "").strip()
    if not envelope_workspace_id:
        return None
    payload_workspace_id = _first_string(
        payload,
        ("workspace_id",),
        ("evidence", "workspace_id"),
        ("incident", "workspace_id"),
        ("rule_missing", "workspace_id"),
        ("plan", "target", "workspace_id"),
        ("plan", "workspace_id"),
        ("selected", "draft", "params", "workspace_id"),
        ("diff", "workspace_id"),
        ("requested", "workspace_id"),
        ("requested", "diff", "workspace_id"),
        ("result", "workspace_id"),
    )
    if payload_workspace_id and payload_workspace_id != envelope_workspace_id:
        return None
    return envelope_workspace_id


def _cluster_id(payload: JsonObject) -> str | None:
    return _first_string(
        payload,
        ("cluster_id",),
        ("evidence", "cluster_id"),
        ("incident", "cluster_id"),
        ("plan", "target", "cluster_id"),
        ("plan", "cluster_id"),
        ("selected", "draft", "params", "cluster_id"),
        ("diff", "cluster_id"),
        ("requested", "cluster_id"),
        ("requested", "diff", "cluster_id"),
        ("result", "cluster_id"),
    )


def _incident_id(payload: JsonObject) -> str | None:
    return _first_string(
        payload,
        ("incident_id",),
        ("incident", "incident_id"),
        ("evidence_bundle", "incident_id"),
        ("plan", "incident_id"),
        ("rule_missing", "incident_id"),
        ("requested", "incident_id"),
    )


def _evidence_ref(payload: JsonObject) -> str | None:
    return _first_string(
        payload,
        ("evidence_ref",),
        ("evidence", "object_ref"),
        ("plan", "evidence_ref"),
        ("rule_missing", "evidence_ref"),
        ("requested", "evidence_ref"),
    )


def _root_cause(payload: JsonObject) -> str | None:
    return _first_string(payload, *ROOT_CAUSE_PATHS)


def _confidence(payload: JsonObject) -> float | None:
    value = _first_value(payload, *CONFIDENCE_PATHS)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _supporting_evidence(payload: JsonObject) -> list[str] | None:
    return (
        _evidence_reference_list(payload, ("rca_detail", "supporting_evidence_refs"))
        or _first_list(payload, ("rca_detail", "supporting_evidence"))
        or _evaluation_reference_list(payload, "supporting_evidence_refs")
        or _evaluation_list(payload, "supporting_evidence")
    )


def _missing_evidence(payload: JsonObject) -> list[str] | None:
    return (
        _first_list(
            payload,
            ("rca_detail", "missing_evidence"),
            ("evidence_bundle", "missing_evidence"),
            ("rule_missing", "missing_evidence"),
            ("missing_evidence",),
        )
        or _evaluation_list(payload, "missing_evidence")
        or None
    )


def _action_route(subject: str, payload: JsonObject) -> str | None:
    route = _first_string(payload, ("plan", "execution_route"), ("selected", "route"))
    if route:
        return route
    if subject.startswith("command."):
        return "command"
    if subject.startswith("safe_pr.") or subject == EventSubject.DIFF_EXPLAINED.value:
        return "safe_pr"
    return None


def _command_id(payload: JsonObject) -> str | None:
    return _first_string(
        payload, ("command_id",), ("plan", "command_id"), ("requested", "command_id")
    )


def _pr_url(payload: JsonObject) -> str | None:
    return _first_string(payload, ("pr_url",), ("pull_request", "url"))


def _error_reason(payload: JsonObject) -> str | None:
    return _first_string(
        payload,
        ("reason",),
        ("rule_missing", "message"),
        ("error",),
        ("result", "message"),
    )


def _first_string(payload: JsonObject, *paths: Path) -> str | None:
    value = _first_value(payload, *paths)
    if value is None or value == "":
        return None
    return str(value)


def _first_value(payload: JsonObject, *paths: Path) -> Any | None:
    for path in paths:
        value = _value_at(payload, path)
        if value is not None:
            return value
    return None


def _first_list(payload: JsonObject, *paths: Path) -> list[str] | None:
    for path in paths:
        value = _value_at(payload, path)
        if isinstance(value, list):
            items = [str(item) for item in value if item not in (None, "")]
            if items:
                return _dedupe(items)
    return None


def _evaluation_list(payload: JsonObject, field: str) -> list[str] | None:
    evaluations = payload.get("evaluations")
    if not isinstance(evaluations, list):
        return None
    values: list[str] = []
    for item in evaluations:
        if not isinstance(item, dict):
            continue
        raw = item.get(field)
        if isinstance(raw, list):
            values.extend(str(value) for value in raw if value not in (None, ""))
    return _dedupe(values) if values else None


def _evidence_reference_list(payload: JsonObject, path: Path) -> list[str] | None:
    raw = _value_at(payload, path)
    if not isinstance(raw, list):
        return None
    values = [_format_evidence_reference(item) for item in raw if isinstance(item, dict)]
    values = [value for value in values if value]
    return _dedupe(values) if values else None


def _evaluation_reference_list(payload: JsonObject, field: str) -> list[str] | None:
    evaluations = payload.get("evaluations")
    if not isinstance(evaluations, list):
        return None
    values: list[str] = []
    for item in evaluations:
        if not isinstance(item, dict):
            continue
        raw = item.get(field)
        if isinstance(raw, list):
            values.extend(
                value
                for value in (
                    _format_evidence_reference(ref) for ref in raw if isinstance(ref, dict)
                )
                if value
            )
    return _dedupe(values) if values else None


def _format_evidence_reference(item: JsonObject) -> str | None:
    evidence_ref = item.get("evidence_ref")
    source = item.get("source")
    name = item.get("name")
    check_id = item.get("check_id")
    if evidence_ref:
        return str(evidence_ref)
    if source and name:
        return f"{source}:{name}"
    if check_id:
        return str(check_id)
    return None


def _value_at(payload: JsonObject, path: Path) -> Any | None:
    cursor: Any = payload
    for key in path:
        if isinstance(cursor, dict):
            cursor = cursor.get(key)
            continue
        if isinstance(cursor, list) and key.isdigit():
            index = int(key)
            if index >= len(cursor):
                return None
            cursor = cursor[index]
            continue
        if not isinstance(cursor, (dict, list)):
            return None
        return None
    return cursor


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _iso_or_none(value: object) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None
