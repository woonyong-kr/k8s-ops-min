from __future__ import annotations

import asyncio

from conftest import load_service, run_handler

from domains.rca.events import Evidence, EvidenceBuiltBody

worker = load_service("ai/incident-worker")


class RegistrationStore:
    def __init__(self, role: str) -> None:
        self.role = role
        self.lookups: list[tuple[str, str]] = []

    async def get_cluster_registration(
        self,
        workspace_id: str,
        cluster_id: str,
    ) -> dict[str, object]:
        self.lookups.append((workspace_id, cluster_id))
        return {"settings": {"cluster_role": self.role}}


def evidence() -> Evidence:
    return Evidence(
        workspace_id="default",
        cluster_id="control-plane",
        object_ref="object://evidence/management-snapshot.json",
        kubernetes={
            "resource": {
                "namespace": "management",
                "kind": "ReplicaSet",
                "name": "api-gateway-abc",
            },
            "symptom": "Pod readiness failure",
        },
        metrics={},
        logs=[],
        traces={},
    )


def test_management_registration_is_excluded_without_cluster_id_hardcoding() -> None:
    store = RegistrationStore("management")

    assert asyncio.run(worker.evidence_is_from_management_cluster(evidence(), store)) is True
    assert store.lookups == [("default", "control-plane")]


def test_target_registration_remains_eligible_for_incident_detection() -> None:
    assert (
        asyncio.run(
            worker.evidence_is_from_management_cluster(
                evidence(),
                RegistrationStore("target"),
            )
        )
        is False
    )


def test_management_evidence_stops_before_signal_claim_and_timeline() -> None:
    store = RegistrationStore("management")

    assert run_handler(
        worker.on_evidence_built,
        EvidenceBuiltBody(evidence=evidence()),
        db=store,
    ) == []
