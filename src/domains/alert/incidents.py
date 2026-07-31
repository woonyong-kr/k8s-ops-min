"""Convert confirmed RCA incidents into durable in-app alert events."""

from __future__ import annotations

import hashlib
import inspect
import json
from datetime import UTC, datetime
from typing import Any

from domains.rca.events import IncidentDetectedBody, IncidentRecord, IncidentResolvedBody
from packages.contracts.event_bus.interfaces import JsonObject

ALERT_EVENT_SEVERITIES = {
    "critical",
    "high",
    "medium",
    "low",
    "warning",
    "info",
}


async def persist_incident_alert_event(
    db: object,
    evt: IncidentDetectedBody,
) -> JsonObject | None:
    """Persist a confirmed incident through either a sync or async repository."""
    if not evt.detected or evt.incident is None:
        return None
    upsert = getattr(db, "upsert_incident_alert_event", None)
    if not callable(upsert):
        raise RuntimeError("incident alert event repository is unavailable")
    result = upsert(build_incident_alert_event(evt))
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        raise TypeError("incident alert event repository returned an invalid result")
    return result


async def resolve_incident_alert_event(
    db: object,
    evt: IncidentResolvedBody,
) -> int:
    """Close the in-app alert when its incident lifecycle is terminal."""
    resolver = getattr(db, "resolve_incident_alert_events", None)
    if not callable(resolver):
        raise RuntimeError("incident alert event repository is unavailable")
    result = resolver(evt.workspace_id, evt.incident_id)
    if inspect.isawaitable(result):
        result = await result
    return int(result)


def incident_alert_event_id(workspace_id: str, incident_id: str) -> str:
    """Return a replay-safe alert identity for one confirmed incident."""
    identity = f"{workspace_id}|{incident_id}|incident"
    return f"ale-inc-{hashlib.sha256(identity.encode()).hexdigest()[:32]}"


def build_incident_alert_event(
    evt: IncidentDetectedBody,
    *,
    observed_at: datetime | None = None,
) -> JsonObject:
    """Build an in-app firing alert only after the incident was confirmed."""
    incident = evt.incident
    if not evt.detected or incident is None:
        raise ValueError("a confirmed incident is required")

    workspace_id = (evt.workspace_id or incident.workspace_id).strip() or "default"
    fired_at = _incident_timestamp(incident.first_seen_at) or observed_at or datetime.now(UTC)
    subject = _incident_subject(incident)
    summary = (
        incident.summary.strip()
        or f"{subject['kind']} {subject['name']} has {incident.symptom}"
    )[:1000]
    symptom = (incident.symptom.strip() or incident.category or "Incident")[:120]

    return {
        "event_id": incident_alert_event_id(workspace_id, incident.incident_id),
        "workspace_id": workspace_id,
        "rule_id": None,
        "rule_name": symptom,
        "source": "incident",
        "severity": _incident_severity(incident.severity or evt.severity),
        "subject_key": hashlib.sha256(
            json.dumps(
                subject,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest(),
        "subject": subject,
        "fired_at": fired_at,
        "resolved_at": None,
        "status": "firing",
        "observed_value": None,
        "threshold": None,
        "evidence": [
            {
                "type": "incident",
                "metric": symptom,
                "observed_at": fired_at.isoformat(),
                "subject": subject,
                "value": None,
                "summary": summary,
                "link": None,
            }
        ],
        "incident_id": incident.incident_id,
    }


def _incident_subject(incident: IncidentRecord) -> dict[str, str | None]:
    return {
        "cluster": (incident.cluster_id.strip() or "unknown")[:512],
        "namespace": (
            incident.namespace.strip()[:253]
            if isinstance(incident.namespace, str) and incident.namespace.strip()
            else None
        ),
        "kind": (incident.resource_kind.strip() or "Workload")[:253],
        "name": (incident.resource_name.strip() or "unknown")[:253],
    }


def _incident_severity(value: Any) -> str:
    severity = str(value or "warning").strip().lower()
    return severity if severity in ALERT_EVENT_SEVERITIES else "warning"


def _incident_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
