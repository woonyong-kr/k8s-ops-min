"""dashboard-worker — 이벤트 흐름을 프론트 조회용 RCA timeline으로 투영."""

from __future__ import annotations

from domains.dashboard.repository import timeline_update_from_event
from domains.issue_filter.projection import ISSUE_EVIDENCE_KIND, extract_issue_evidence_labels
from packages.config.logs import get_logger
from packages.contracts.event_bus.interfaces import EventEnvelope, JsonObject
from packages.contracts.event_bus.subjects import EventSubject
from packages.contracts.stores import DashboardStore
from packages.runtime.app import App, EventContext

app = App("dashboard-worker")
LOGGER = get_logger(__name__)


@app.on_any
async def on_event(evt: EventEnvelope, ctx: EventContext[DashboardStore]) -> None:
    row = timeline_update_from_event(evt)
    if row is not None:
        await _project_incident_labels(evt, row, ctx.db)
        await ctx.db.upsert_rca_timeline(row)


async def _project_incident_labels(
    evt: EventEnvelope,
    row: JsonObject,
    db: DashboardStore,
) -> None:
    """저장된 원문 evidence에서 최초 incident labels만 fail-closed로 투영한다."""
    if str(evt.subject) != EventSubject.INCIDENT_DETECTED.value:
        return
    target = _issue_target(row)
    if target is None:
        return

    row_workspace_id = str(row.get("workspace_id") or "").strip()
    envelope_workspace_id = str(evt.workspace_id or "").strip()
    if envelope_workspace_id and envelope_workspace_id != row_workspace_id:
        return
    workspace_id = envelope_workspace_id or row_workspace_id
    if not workspace_id:
        return

    try:
        evidence = await db.get_evidence_payload(
            workspace_id,
            str(row["correlation_id"]),
            ISSUE_EVIDENCE_KIND,
        )
    except Exception as exc:
        LOGGER.warning(
            "issue_label_projection_lookup_failed",
            extra={
                "context": {
                    "event_id": evt.event_id,
                    "correlation_id": row.get("correlation_id"),
                    "workspace_id": workspace_id,
                }
            },
            exc_info=exc,
        )
        return
    if not isinstance(evidence, dict):
        return

    projected = extract_issue_evidence_labels(
        evidence,
        cluster_id=target["cluster_id"],
        namespace=target["namespace"],
        resource_kind=target["resource_kind"],
        resource_name=target["resource_name"],
    )
    labels = projected.get("labels")
    if isinstance(labels, dict):
        row["labels"] = labels
    row["labels_complete"] = projected.get("labels_complete") is True


def _issue_target(row: JsonObject) -> dict[str, str | None] | None:
    cluster_id = str(row.get("cluster_id") or "").strip()
    resource_kind = str(row.get("incident_resource_kind") or "").strip()
    resource_name = str(row.get("incident_resource_name") or "").strip()
    if not cluster_id or not resource_kind or not resource_name:
        return None
    namespace_value = row.get("incident_namespace")
    namespace = str(namespace_value).strip() if namespace_value is not None else None
    return {
        "cluster_id": cluster_id,
        "namespace": namespace,
        "resource_kind": resource_kind,
        "resource_name": resource_name,
    }


if __name__ == "__main__":
    app.run()
