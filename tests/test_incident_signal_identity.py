from __future__ import annotations

from dataclasses import replace

from domains.rca.events import Evidence, IncidentRecord
from services.ai.agent.pipeline.incident_signal import incident_claim_identity


def incident(
    *,
    workspace_id: str = "workspace-1",
    cluster_id: str = "cluster-1",
    namespace: str = "sandbox",
    resource_kind: str = "Deployment",
    resource_name: str = "api-server",
    symptom: str = "admission_failure",
) -> IncidentRecord:
    return IncidentRecord(
        incident_id="incident-1",
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespace=namespace,
        resource_kind=resource_kind,
        resource_name=resource_name,
        symptom=symptom,
        severity="high",
        first_seen_at="2026-07-24T03:51:10Z",
        summary=f"{resource_kind} {resource_name} has {symptom}",
    )


def alertmanager_evidence(
    *,
    object_ref: str = "object://evidence/window-1.json",
    workspace_id: str = "workspace-1",
    cluster_id: str = "cluster-1",
    fingerprint: str = "alert-fingerprint-1",
    starts_at: str = "2026-07-24T03:51:10Z",
    namespace: str = "sandbox",
    resource_kind: str = "Deployment",
    resource_name: str = "api-server",
    symptom: str = "admission_failure",
) -> Evidence:
    return Evidence(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        object_ref=object_ref,
        kubernetes={
            "resource": {
                "namespace": namespace,
                "kind": resource_kind,
                "name": resource_name,
            },
            "symptom": symptom,
        },
        metrics={
            "alertmanager": {
                "alerts": [
                    {
                        "status": "firing",
                        "fingerprint": fingerprint,
                        "startsAt": starts_at,
                        "labels": {
                            "opsia_namespace": namespace,
                            "opsia_resource_kind": resource_kind,
                            "opsia_resource_name": resource_name,
                            "opsia_symptom": symptom,
                        },
                    }
                ]
            }
        },
        logs=[],
        traces={},
    )


def claim(evidence: Evidence, target: IncidentRecord | None = None):
    identity = incident_claim_identity(evidence, target or incident())
    assert identity is not None
    return identity


def test_same_active_alert_reuses_claim_across_enriched_evidence_windows() -> None:
    first = claim(alertmanager_evidence(object_ref="object://evidence/window-1.json"))
    repeated = claim(alertmanager_evidence(object_ref="object://evidence/window-2.json"))

    assert repeated.signal_key == first.signal_key
    assert repeated.payload == first.payload
    assert repeated.payload["fingerprint"] == "alert-fingerprint-1"
    assert repeated.payload["starts_at"] == "2026-07-24T03:51:10Z"


def test_new_alert_occurrence_with_new_starts_at_gets_new_claim() -> None:
    first = claim(alertmanager_evidence(starts_at="2026-07-24T03:51:10Z"))
    after_resolution = claim(alertmanager_evidence(starts_at="2026-07-24T04:08:00Z"))

    assert after_resolution.signal_key != first.signal_key


def test_alert_claim_is_isolated_by_tenant_cluster_and_target() -> None:
    baseline_evidence = alertmanager_evidence()
    baseline_incident = incident()
    baseline = claim(baseline_evidence, baseline_incident)

    other_tenant = claim(
        replace(baseline_evidence, workspace_id="workspace-2"),
        replace(baseline_incident, workspace_id="workspace-2"),
    )
    other_cluster = claim(
        replace(baseline_evidence, cluster_id="cluster-2"),
        replace(baseline_incident, cluster_id="cluster-2"),
    )
    other_target_evidence = alertmanager_evidence(resource_name="other-api-server")
    other_target = claim(
        other_target_evidence,
        replace(baseline_incident, resource_name="other-api-server"),
    )

    assert all(
        identity.signal_key.startswith("alertmanager-firing-v1:")
        for identity in (baseline, other_tenant, other_cluster, other_target)
    )
    assert (
        len(
            {
                baseline.signal_key,
                other_tenant.signal_key,
                other_cluster.signal_key,
                other_target.signal_key,
            }
        )
        == 4
    )


def test_concrete_container_termination_remains_stronger_than_alert_claim() -> None:
    evidence = replace(
        alertmanager_evidence(),
        kubernetes={
            "pods": [
                {
                    "uid": "pod-uid-1",
                    "name": "api-server-abc",
                    "namespace": "sandbox",
                    "owner_kind": "Deployment",
                    "owner_name": "api-server",
                    "containers": [
                        {
                            "name": "api-server",
                            "last_state": "terminated",
                            "last_state_reason": "Error",
                            "restart_count": 1,
                            "last_finished_at": "2026-07-24T03:51:09Z",
                            "last_exit_code": 1,
                        }
                    ],
                }
            ]
        },
    )

    identity = claim(evidence)

    assert identity.signal_key.startswith("k8s-container-termination-v1:")
    assert identity.payload["pod_uid"] == "pod-uid-1"
