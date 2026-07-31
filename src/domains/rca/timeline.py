"""Safe Timeline facts produced by confirmed RCA incident claims."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from domains.rca.events import IncidentRecord
from packages.contracts.parity import ClusterScope
from packages.contracts.timeline import TimelineEvent, TimelineIncidentSubject

IssuePresentationSeverity = Literal["critical", "warning"]


def incident_timeline_event(
    *,
    source_event_id: str,
    source_created_at: str,
    correlation_id: str,
    incident: IncidentRecord,
) -> TimelineEvent:
    """Map a claimed incident without exposing its evidence or inventing a UID."""
    source_key = ":".join(("incident", source_event_id, incident.incident_id))
    severity = incident_timeline_severity(incident.severity)
    return TimelineEvent(
        event_id=source_key,
        source="incident",
        source_key=source_key,
        native_id=incident.incident_id,
        activity="warning",
        occurred_at=incident_occurred_at(source_created_at),
        scope=ClusterScope(
            workspace_id=incident.workspace_id,
            cluster_id=incident.cluster_id,
        ),
        subject=TimelineIncidentSubject(
            incident_id=incident.incident_id,
            correlation_id=correlation_id,
        ),
        event_type="incident",
        severity=severity,
        title=f"Incident {incident.incident_id} detected",
        metadata={"status": "detected", "severity": severity},
    )


def incident_timeline_severity(severity: str) -> str:
    """Normalize detector labels to the strict Timeline severity contract."""
    normalized = severity.strip().lower()
    if normalized in {"critical", "high"}:
        return "critical"
    if normalized in {"warning", "medium"}:
        return "warning"
    if normalized in {"info", "low"}:
        return "info"
    return "unknown"


def issue_presentation_severity(
    severity: object,
    *,
    source_complete: bool,
) -> IssuePresentationSeverity | None:
    """Return only a verified tier supported by the Issues queue visual model.

    Detector labels are normalized once in Python through the shared timeline
    severity policy.  Values that map to informational or unknown severity are
    deliberately unavailable instead of being promoted by the browser.
    """
    if not source_complete or not isinstance(severity, str) or not severity.strip():
        return None
    normalized = incident_timeline_severity(severity)
    if normalized in {"critical", "warning"}:
        return normalized
    return None


def incident_occurred_at(source_created_at: str) -> datetime:
    """Use the durable source-envelope timestamp when it is available."""
    normalized = source_created_at.strip()
    if normalized:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)
