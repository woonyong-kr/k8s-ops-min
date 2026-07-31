from datetime import UTC, datetime

from domains.rca.router import (
    alertmanager_alert_event_id,
    build_alertmanager_alert_event,
)
from packages.contracts.gateway.requests import AlertmanagerAlert


def test_alertmanager_firing_becomes_durable_incident_alert() -> None:
    alert = AlertmanagerAlert(
        status="firing",
        labels={
            "alertname": "DemoGameTickOverrun",
            "severity": "warning",
            "namespace": "sandbox",
            "service": "game-server",
            "room": "room-0",
        },
        annotations={"summary": "game tick p95 is over budget"},
        startsAt="2026-07-23T05:00:00Z",
        fingerprint="demo-fingerprint",
    )

    event = build_alertmanager_alert_event(
        "workspace-1",
        "game-server",
        alert,
        incident_id="incident-1",
        observed_at=datetime(2026, 7, 23, 5, tzinfo=UTC),
    )

    assert event["event_id"] == alertmanager_alert_event_id(
        "workspace-1",
        "game-server",
        alert,
    )
    assert event["source"] == "alertmanager"
    assert event["status"] == "firing"
    assert event["severity"] == "warning"
    assert event["subject"] == {
        "cluster": "game-server",
        "namespace": "sandbox",
        "kind": "Service",
        "name": "game-server",
    }
    assert event["incident_id"] == "incident-1"
    assert event["evidence"][0]["summary"] == "game tick p95 is over budget"


def test_alertmanager_resolved_updates_same_event_identity() -> None:
    firing = AlertmanagerAlert(
        status="firing",
        labels={"alertname": "DemoGameJoinStorm", "severity": "warning"},
        startsAt="2026-07-23T05:00:00Z",
        fingerprint="same-alert",
    )
    resolved = AlertmanagerAlert(
        status="resolved",
        labels={"alertname": "DemoGameJoinStorm", "severity": "warning"},
        startsAt="2026-07-23T05:00:00Z",
        endsAt="2026-07-23T05:02:00Z",
        fingerprint="same-alert",
    )

    event = build_alertmanager_alert_event(
        "workspace-1",
        "game-server",
        resolved,
        incident_id=None,
    )

    assert alertmanager_alert_event_id(
        "workspace-1", "game-server", firing
    ) == alertmanager_alert_event_id("workspace-1", "game-server", resolved)
    assert event["status"] == "resolved"
    assert event["resolved_at"] == datetime(2026, 7, 23, 5, 2, tzinfo=UTC)


def test_standard_sli_alert_uses_opsia_workload_identity() -> None:
    alert = AlertmanagerAlert(
        status="firing",
        labels={
            "alertname": "OpsiaSliFailureRatioHigh",
            "severity": "warning",
            "opsia_namespace": "sandbox",
            "opsia_resource_kind": "Deployment",
            "opsia_resource_name": "api-server",
            "opsia_service": "matchmaking",
            "opsia_sli": "admission",
            "opsia_symptom": "admission_failure",
        },
        annotations={
            "opsia_observed_value": "0.79",
            "opsia_threshold": "0.2",
        },
        startsAt="2026-07-24T01:00:00Z",
        fingerprint="standard-sli",
    )

    event = build_alertmanager_alert_event(
        "workspace-1",
        "game-server",
        alert,
        incident_id="incident-1",
    )

    assert event["subject"] == {
        "cluster": "game-server",
        "namespace": "sandbox",
        "kind": "Deployment",
        "name": "api-server",
    }
    assert event["observed_value"] == 0.79
    assert event["threshold"] == 0.2
    assert event["series_identity"] == {
        "namespace": "sandbox",
        "resource_kind": "Deployment",
        "resource_name": "api-server",
        "service": "matchmaking",
        "sli": "admission",
        "symptom": "admission_failure",
    }
