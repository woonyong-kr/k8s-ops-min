from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from domains.alert.incidents import (
    build_incident_alert_event,
    incident_alert_event_id,
    persist_incident_alert_event,
    resolve_incident_alert_event,
)
from domains.alert.repository import AlertRuleRepository
from domains.alert.service import alert_event_response
from domains.rca.events import IncidentDetectedBody, IncidentRecord, IncidentResolvedBody

INCIDENT_SIGNALS = (
    "ImagePullBackOff",
    "ErrImagePull",
    "InvalidImageName",
    "ErrImageNeverPull",
    "CrashLoopBackOff",
    "OOMKilled",
    "FailedScheduling",
    "ProbeFailed",
    "PodNotReady",
    "ServiceEndpointsEmpty",
    "Application5xx",
    "ApplicationTimeout",
    "AlertmanagerFiring",
)


def incident_detected(
    symptom: str = "CrashLoopBackOff",
    *,
    detected: bool = True,
    severity: str = "high",
    incident_id: str = "incident-1",
) -> IncidentDetectedBody:
    incident = (
        IncidentRecord(
            incident_id=incident_id,
            cluster_id="game-server",
            resource_kind="Pod",
            resource_name="demo-game-abc",
            namespace="sandbox",
            symptom=symptom,
            severity=severity,
            first_seen_at="2026-07-23T05:00:00Z",
            summary=f"Pod demo-game-abc has {symptom}",
            category="container_restart",
            workspace_id="workspace-1",
        )
        if detected
        else None
    )
    return IncidentDetectedBody(
        cluster_id="game-server",
        detected=detected,
        reason="incident confirmed" if detected else "no incident",
        workspace_id="workspace-1",
        severity=severity if detected else None,
        incident=incident,
    )


@pytest.mark.parametrize("symptom", INCIDENT_SIGNALS)
def test_every_confirmed_incident_signal_becomes_a_valid_alert(symptom: str) -> None:
    event = build_incident_alert_event(
        incident_detected(symptom),
        observed_at=datetime(2026, 7, 23, 5, 1, tzinfo=UTC),
    )

    assert event["source"] == "incident"
    assert event["status"] == "firing"
    assert event["rule_name"] == symptom
    assert event["incident_id"] == "incident-1"
    assert event["subject"] == {
        "cluster": "game-server",
        "namespace": "sandbox",
        "kind": "Pod",
        "name": "demo-game-abc",
    }
    assert alert_event_response(event).source == "incident"


def test_incident_alert_identity_is_deterministic_for_event_replay() -> None:
    first = build_incident_alert_event(incident_detected())
    replay = build_incident_alert_event(incident_detected())

    assert first["event_id"] == replay["event_id"]
    assert first["event_id"] == incident_alert_event_id("workspace-1", "incident-1")


def test_invalid_incident_severity_falls_back_to_warning() -> None:
    event = build_incident_alert_event(incident_detected(severity="emergency"))

    assert event["severity"] == "warning"


def test_non_incident_cannot_build_an_alert() -> None:
    with pytest.raises(ValueError, match="confirmed incident"):
        build_incident_alert_event(incident_detected(detected=False))


class AsyncIncidentAlertRepository:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    async def upsert_incident_alert_event(
        self,
        payload: dict[str, object],
    ) -> dict[str, object]:
        self.payloads.append(payload)
        return payload

    async def resolve_incident_alert_events(
        self,
        workspace_id: str,
        incident_id: str,
    ) -> int:
        self.payloads.append(
            {
                "workspace_id": workspace_id,
                "incident_id": incident_id,
                "status": "resolved",
            }
        )
        return 1


def test_confirmed_incident_is_persisted_once_through_async_repository() -> None:
    repository = AsyncIncidentAlertRepository()

    persisted = asyncio.run(persist_incident_alert_event(repository, incident_detected()))

    assert persisted is repository.payloads[0]
    assert len(repository.payloads) == 1
    assert repository.payloads[0]["incident_id"] == "incident-1"


def test_non_incident_does_not_touch_repository() -> None:
    repository = AsyncIncidentAlertRepository()

    persisted = asyncio.run(
        persist_incident_alert_event(
            repository,
            incident_detected(detected=False),
        )
    )

    assert persisted is None
    assert repository.payloads == []


def test_incident_resolution_closes_the_linked_alert() -> None:
    repository = AsyncIncidentAlertRepository()
    resolved = IncidentResolvedBody(
        incident_id="incident-1",
        cluster_id="game-server",
        reason="recovery verified",
        evidence_ref="evidence-1",
        recovery_plan_id="plan-1",
        before={},
        after={},
        workspace_id="workspace-1",
    )

    count = asyncio.run(resolve_incident_alert_event(repository, resolved))

    assert count == 1
    assert repository.payloads == [
        {
            "workspace_id": "workspace-1",
            "incident_id": "incident-1",
            "status": "resolved",
        }
    ]


class FakeMappingsResult:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def mappings(self) -> FakeMappingsResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self.row

    def one(self) -> dict[str, Any]:
        if self.row is None:
            raise AssertionError("expected one row")
        return self.row


class FakeConnection:
    def __init__(self, *rows: dict[str, Any] | None) -> None:
        self.rows = list(rows)
        self.executed = 0

    def execute(self, _statement: object) -> FakeMappingsResult:
        self.executed += 1
        return FakeMappingsResult(self.rows.pop(0))


def incident_repository_with(
    *rows: dict[str, Any] | None,
) -> tuple[AlertRuleRepository, FakeConnection]:
    repository = object.__new__(AlertRuleRepository)
    connection = FakeConnection(*rows)

    @contextmanager
    def unit_of_work():
        yield connection

    repository.unit_of_work = unit_of_work  # type: ignore[method-assign]
    return repository, connection


def test_repository_reuses_alertmanager_event_for_same_incident() -> None:
    incident_event = build_incident_alert_event(incident_detected())
    alertmanager_event = {
        **incident_event,
        "event_id": "ale-am-existing",
        "source": "alertmanager",
        "status": "acked",
        "acknowledged_at": datetime(2026, 7, 23, 5, 2, tzinfo=UTC),
        "acknowledged_by": "user-1",
    }
    repository, connection = incident_repository_with(alertmanager_event)

    stored = repository.upsert_incident_alert_event(incident_event)

    assert stored["event_id"] == "ale-am-existing"
    assert stored["status"] == "acked"
    assert connection.executed == 1


def test_repository_inserts_non_alertmanager_incident_once() -> None:
    incident_event = build_incident_alert_event(incident_detected())
    repository, connection = incident_repository_with(None, incident_event)

    stored = repository.upsert_incident_alert_event(incident_event)

    assert stored["event_id"] == incident_event["event_id"]
    assert connection.executed == 2


def test_repository_replay_returns_existing_incident_without_reopening_it() -> None:
    incident_event = build_incident_alert_event(incident_detected())
    acknowledged = {
        **incident_event,
        "status": "acked",
        "acknowledged_at": datetime(2026, 7, 23, 5, 2, tzinfo=UTC),
        "acknowledged_by": "user-1",
    }
    repository, connection = incident_repository_with(None, None, acknowledged)

    stored = repository.upsert_incident_alert_event(incident_event)

    assert stored["status"] == "acked"
    assert stored["acknowledged_by"] == "user-1"
    assert connection.executed == 3
