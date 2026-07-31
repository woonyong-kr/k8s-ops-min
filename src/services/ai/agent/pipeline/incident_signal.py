"""Stable identities for concrete incident signal occurrences."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from domains.rca.events import Evidence, IncidentRecord
from packages.contracts.event_bus.interfaces import JsonObject

SIGNAL_IDENTITY_VERSION = "k8s-container-termination-v1"
ALERTMANAGER_SIGNAL_IDENTITY_VERSION = "alertmanager-firing-v1"
EVIDENCE_SIGNAL_IDENTITY_VERSION = "rca-incident-evidence-v1"


@dataclass(frozen=True)
class IncidentSignalIdentity:
    signal_key: str
    payload: JsonObject


def incident_claim_identity(
    evidence: Evidence,
    incident: IncidentRecord,
) -> IncidentSignalIdentity | None:
    """Return a durable claim identity for every confirmed incident candidate.

    A Kubernetes container termination is the strongest identity and is reused
    when present. Other detected sources use their immutable evidence object
    reference plus the incident target. They still deduplicate redelivery of
    that evidence without guessing a resource UID or merging later evidence.
    """
    termination = incident_termination_identity(evidence, incident)
    if termination is not None:
        return termination
    alertmanager = incident_alertmanager_identity(evidence, incident)
    if alertmanager is not None:
        return alertmanager
    object_ref = text(evidence.object_ref)
    if object_ref is None:
        return None
    identity: JsonObject = {
        "version": EVIDENCE_SIGNAL_IDENTITY_VERSION,
        "workspace_id": evidence.workspace_id,
        "cluster_id": evidence.cluster_id,
        "object_ref": object_ref,
        "namespace": incident.namespace,
        "resource_kind": incident.resource_kind,
        "resource_name": incident.resource_name,
        "symptom": incident.symptom,
        "first_seen_at": incident.first_seen_at,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return IncidentSignalIdentity(
        signal_key=f"{EVIDENCE_SIGNAL_IDENTITY_VERSION}:{hashlib.sha256(encoded).hexdigest()}",
        payload=identity,
    )


def incident_alertmanager_identity(
    evidence: Evidence,
    incident: IncidentRecord,
) -> IncidentSignalIdentity | None:
    """Return one stable identity for an active Alertmanager occurrence.

    Alertmanager repeats a firing notification and the evidence worker can join
    that same occurrence to several adjacent collection windows.  The evidence
    object reference is therefore not an incident identity.  The immutable
    Alertmanager occurrence fields plus tenant, cluster, and target are.
    """

    alertmanager = evidence.metrics.get("alertmanager")
    alerts = alertmanager.get("alerts") if isinstance(alertmanager, dict) else None
    if not isinstance(alerts, list):
        return None

    candidates: list[JsonObject] = []
    for alert in alerts:
        if not isinstance(alert, dict) or text(alert.get("status")) != "firing":
            continue
        fingerprint = text(alert.get("fingerprint"))
        starts_at = text(alert.get("startsAt"))
        labels = alert.get("labels")
        if (
            fingerprint is None
            or starts_at is None
            or not isinstance(labels, dict)
            or not alert_target_matches_incident(labels, incident)
        ):
            continue
        candidates.append(
            {
                "version": ALERTMANAGER_SIGNAL_IDENTITY_VERSION,
                "workspace_id": evidence.workspace_id,
                "cluster_id": evidence.cluster_id,
                "namespace": incident.namespace,
                "resource_kind": incident.resource_kind,
                "resource_name": incident.resource_name,
                "symptom": incident.symptom,
                "fingerprint": fingerprint,
                "starts_at": starts_at,
            }
        )

    # One classified incident must map to one exact alert occurrence.  Ambiguous
    # groups fail closed to the evidence identity instead of merging incidents.
    if len(candidates) != 1:
        return None
    identity = candidates[0]
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return IncidentSignalIdentity(
        signal_key=(
            f"{ALERTMANAGER_SIGNAL_IDENTITY_VERSION}:{hashlib.sha256(encoded).hexdigest()}"
        ),
        payload=identity,
    )


def alert_target_matches_incident(
    labels: dict,
    incident: IncidentRecord,
) -> bool:
    """Match standard Alertmanager target labels to the classified incident."""

    namespace = text(labels.get("opsia_namespace")) or text(labels.get("namespace"))
    resource_kind = text(labels.get("opsia_resource_kind"))
    resource_name = text(labels.get("opsia_resource_name"))
    symptom = text(labels.get("opsia_symptom"))
    if namespace is None or resource_kind is None or resource_name is None:
        return False
    return (
        namespace == incident.namespace
        and resource_kind.casefold() == incident.resource_kind.casefold()
        and resource_name == incident.resource_name
        and (symptom is None or symptom == incident.symptom)
    )


def incident_termination_identity(
    evidence: Evidence,
    incident: IncidentRecord,
) -> IncidentSignalIdentity | None:
    """Return the newest concrete termination that belongs to the incident.

    A claim is deliberately produced only when the snapshot contains enough
    Kubernetes identity to distinguish one process termination from the next.
    Other incident sources remain fail-open and continue through the pipeline.
    """
    candidates: list[tuple[tuple[str, int, str, str], JsonObject]] = []
    pods = evidence.kubernetes.get("pods")
    if not isinstance(pods, list):
        return None

    for pod in pods:
        if not isinstance(pod, dict) or not pod_matches_incident(pod, incident):
            continue
        pod_uid = text(pod.get("uid"))
        if pod_uid is None:
            continue
        containers = pod.get("containers")
        if not isinstance(containers, list):
            continue
        for container in containers:
            if not isinstance(container, dict):
                continue
            container_name = text(container.get("name"))
            reason = text(container.get("last_state_reason"))
            if container_name is None or reason is None:
                continue
            last_state = text(container.get("last_state"))
            if last_state is not None and last_state.lower() != "terminated":
                continue
            restart_count = non_negative_int(container.get("restart_count"))
            finished_at = text(container.get("last_finished_at"))
            # At least one monotonic termination marker is required. Without it,
            # claiming could merge unrelated future failures of the same pod.
            if restart_count is None or (restart_count == 0 and finished_at is None):
                continue
            exit_code = optional_int(container.get("last_exit_code"))
            identity: JsonObject = {
                "version": SIGNAL_IDENTITY_VERSION,
                "workspace_id": evidence.workspace_id,
                "cluster_id": evidence.cluster_id,
                "namespace": text(pod.get("namespace")) or incident.namespace,
                "pod_uid": pod_uid,
                "pod_name": text(pod.get("name")),
                "container_name": container_name,
                "restart_count": restart_count,
                "last_finished_at": finished_at,
                "reason": reason,
                "exit_code": exit_code,
            }
            sort_key = (
                finished_at or "",
                restart_count,
                text(pod.get("name")) or "",
                container_name,
            )
            candidates.append((sort_key, identity))

    if not candidates:
        return None
    identity = max(candidates, key=lambda item: item[0])[1]
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    return IncidentSignalIdentity(
        signal_key=f"{SIGNAL_IDENTITY_VERSION}:{digest}",
        payload=identity,
    )


def pod_matches_incident(pod: JsonObject, incident: IncidentRecord) -> bool:
    if text(pod.get("namespace")) != incident.namespace:
        return False
    resource_kind = incident.resource_kind.casefold()
    if resource_kind == "pod":
        return text(pod.get("name")) == incident.resource_name
    return (text(pod.get("owner_kind")) or "").casefold() == resource_kind and text(
        pod.get("owner_name")
    ) == incident.resource_name


def text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def non_negative_int(value: object) -> int | None:
    parsed = optional_int(value)
    return parsed if parsed is not None and parsed >= 0 else None


def optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
