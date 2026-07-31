"""Bounded W4 activity aggregates over persisted product facts."""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, cast, func, literal, select

from domains.alert.models import AlertEvent
from domains.dashboard.models import RcaTimeline
from domains.gitops.models import WorkflowRun
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gitops import WorkflowRunStatus
from packages.storage.engine import DatabaseConnection


class ActivityOverviewRepository(DatabaseConnection):
    """Aggregate deployments, alerts, and critical incidents without raw-event fan-out."""

    def activity_overview(
        self,
        *,
        workspace_id: str,
        deployment_application_ids: Collection[str],
        alert_cluster_ids: Collection[str],
        incident_cluster_ids: Collection[str],
        from_ms: int,
        to_ms: int,
        bucket_ms: int,
        requested_cluster_ids: Collection[str] = (),
        requested_namespaces: Collection[str] = (),
    ) -> list[JsonObject]:
        deployment_ids = tuple(sorted(set(deployment_application_ids)))
        alert_ids = tuple(sorted(set(alert_cluster_ids)))
        incident_ids = tuple(sorted(set(incident_cluster_ids)))
        cluster_ids = tuple(sorted(set(requested_cluster_ids)))
        namespaces = tuple(sorted(set(requested_namespaces)))
        start = datetime.fromtimestamp(from_ms / 1_000, tz=UTC)
        end = datetime.fromtimestamp(to_ms / 1_000, tz=UTC)
        series: dict[str, dict[int, int]] = {
            "deployments": {},
            "alerts": {},
            "critical": {},
        }
        with self.connection() as conn:
            if deployment_ids:
                run = WorkflowRun.__table__
                series["deployments"] = _series_counts(
                    conn,
                    _bucketed_count_statement(
                        timestamp=run.c.updated_at,
                        from_ms=from_ms,
                        bucket_ms=bucket_ms,
                        predicates=(
                            run.c.workspace_id == workspace_id,
                            run.c.application_id.in_(deployment_ids),
                            *((run.c.cluster_id.in_(cluster_ids),) if cluster_ids else ()),
                            *(
                                (run.c["metadata"]["namespace"].astext.in_(namespaces),)
                                if namespaces
                                else ()
                            ),
                            run.c.status.in_(
                                (
                                    WorkflowRunStatus.FAILED.value,
                                    WorkflowRunStatus.SUCCEEDED.value,
                                )
                            ),
                            run.c.updated_at >= start,
                            run.c.updated_at < end,
                        ),
                    ),
                )

            if alert_ids:
                alert = AlertEvent.__table__
                series["alerts"] = _series_counts(
                    conn,
                    _bucketed_count_statement(
                        timestamp=alert.c.fired_at,
                        from_ms=from_ms,
                        bucket_ms=bucket_ms,
                        predicates=(
                            alert.c.workspace_id == workspace_id,
                            alert.c.subject["cluster"].astext.in_(alert_ids),
                            *(
                                (alert.c.subject["namespace"].astext.in_(namespaces),)
                                if namespaces
                                else ()
                            ),
                            alert.c.fired_at >= start,
                            alert.c.fired_at < end,
                        ),
                    ),
                )

            if incident_ids:
                incident = RcaTimeline.__table__
                series["critical"] = _series_counts(
                    conn,
                    _bucketed_count_statement(
                        timestamp=incident.c.created_at,
                        from_ms=from_ms,
                        bucket_ms=bucket_ms,
                        predicates=(
                            incident.c.workspace_id == workspace_id,
                            incident.c.cluster_id.in_(incident_ids),
                            *(
                                (incident.c.incident_namespace.in_(namespaces),)
                                if namespaces
                                else ()
                            ),
                            incident.c.incident_id.is_not(None),
                            func.lower(incident.c.severity) == "critical",
                            incident.c.created_at >= start,
                            incident.c.created_at < end,
                        ),
                    ),
                )

        buckets = []
        bucket_count = (to_ms - from_ms + bucket_ms - 1) // bucket_ms
        for index in range(bucket_count):
            bucket_from = from_ms + index * bucket_ms
            buckets.append(
                {
                    "from_ms": bucket_from,
                    "to_ms": min(bucket_from + bucket_ms, to_ms),
                    "deployments": series["deployments"].get(index, 0),
                    "alerts": series["alerts"].get(index, 0),
                    "critical": series["critical"].get(index, 0),
                }
            )
        return buckets


def _bucketed_count_statement(
    *,
    timestamp: Any,
    from_ms: int,
    bucket_ms: int,
    predicates: tuple[Any, ...],
) -> Any:
    # PostgreSQL otherwise infers these Python integer binds as ``int4`` in
    # this arithmetic expression.  Current epoch milliseconds already exceed
    # that range, so keep the complete bucket calculation in the bigint domain.
    millis_per_second = literal(1_000, type_=BigInteger())
    window_from_ms = literal(from_ms, type_=BigInteger())
    bucket_width_ms = literal(bucket_ms, type_=BigInteger())
    occurred_ms = func.extract("epoch", timestamp) * millis_per_second
    bucket_index = cast(
        func.floor((occurred_ms - window_from_ms) / bucket_width_ms),
        BigInteger,
    ).label("bucket_index")
    return (
        select(bucket_index, func.count().label("count"))
        .where(*predicates)
        .group_by(bucket_index)
        .order_by(bucket_index)
    )


def _series_counts(conn: Any, statement: Any) -> dict[int, int]:
    return {
        int(row["bucket_index"]): int(row["count"])
        for row in conn.execute(statement).mappings().all()
    }
