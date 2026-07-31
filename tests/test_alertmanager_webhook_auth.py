from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from domains.identity.dependencies import hash_agent_token
from domains.rca.router import (
    ALERTMANAGER_WEBHOOK_TOKEN_ENV,
    STANDARD_SLI_LABELS_INVALID,
    STANDARD_SLI_MEASUREMENT_INVALID,
    WEBHOOK_TOKEN_INVALID,
    alertmanager_webhook,
    require_alertmanager_token,
    validate_alertmanager_sli_labels,
)
from packages.contracts.gateway.requests import AlertmanagerAlert, AlertmanagerWebhookRequest


def webhook_request(authorization: str = "") -> Request:
    headers = [(b"authorization", authorization.encode())] if authorization else []
    return Request({"type": "http", "headers": headers})


class AgentAuthDb:
    def __init__(
        self,
        identity: dict[str, str] | None,
        *,
        expected_token: str = "cluster-agent-token",
    ) -> None:
        self.identity = identity
        self.expected_hash = hash_agent_token(expected_token)
        self.seen_hashes: list[str] = []

    def authenticate_cluster_agent(self, token_hash: str) -> dict[str, str] | None:
        self.seen_hashes.append(token_hash)
        return self.identity if token_hash == self.expected_hash else None


class AlertmanagerLifecycleDb(AgentAuthDb):
    def __init__(self, disposition: str) -> None:
        super().__init__(None)
        self.disposition = disposition
        self.alert_events: list[dict[str, object]] = []
        self.rotations: list[dict[str, object]] = []

    def get_cluster_registration(
        self,
        workspace_id: str,
        cluster_id: str,
    ) -> dict[str, str]:
        return {"workspace_id": workspace_id, "cluster_id": cluster_id}

    def get_evidence_window(self, evidence_key: str) -> dict[str, str]:
        return {
            "evidence_key": evidence_key,
            "event_id": "event-old",
            "correlation_id": "incident-old",
        }

    def get_alertmanager_evidence_disposition(
        self,
        workspace_id: str,
        correlation_id: str,
        event_id: str,
    ) -> str:
        assert workspace_id == "workspace-1"
        assert correlation_id == "incident-old"
        assert event_id == "event-old"
        return self.disposition

    def rotate_alertmanager_evidence_window(
        self,
        **values: object,
    ) -> dict[str, object]:
        self.rotations.append(values)
        return {
            "duplicate": False,
            "event_id": "event-new",
            "correlation_id": "incident-new",
        }

    def upsert_external_alert_event(
        self,
        payload: dict[str, object],
    ) -> dict[str, object]:
        self.alert_events.append(payload)
        return payload


class AlertmanagerOpenIncidentDb(AlertmanagerLifecycleDb):
    def __init__(self) -> None:
        super().__init__("active")
        self.recorded: list[dict[str, object]] = []

    def get_evidence_window(self, _evidence_key: str) -> None:
        return None

    def find_open_alertmanager_incident(
        self,
        workspace_id: str,
        cluster_id: str,
        namespace: str,
        resource_kind: str,
        resource_name: str,
        symptom: str,
    ) -> dict[str, str]:
        assert (
            workspace_id,
            cluster_id,
            namespace,
            resource_kind,
            resource_name,
            symptom,
        ) == (
            "workspace-1",
            "cluster-1",
            "sandbox",
            "Deployment",
            "api-server",
            "admission_failure",
        )
        return {
            "correlation_id": "incident-open",
            "event_id": "event-open",
        }

    def record_evidence_event_once(self, **values: object) -> dict[str, object]:
        self.recorded.append(values)
        envelope = values["event_envelope"]
        return {
            "duplicate": False,
            "event_id": envelope.event_id,
            "correlation_id": envelope.correlation_id,
        }


class AlertmanagerEvents:
    source = "api-gateway"


def authorize(
    request: Request,
    db: object,
    *,
    workspace_id: str = "workspace-1",
    cluster_id: str = "cluster-1",
) -> None:
    asyncio.run(
        require_alertmanager_token(
            request,
            db,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
        )
    )


def test_existing_global_webhook_token_remains_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ALERTMANAGER_WEBHOOK_TOKEN_ENV, "global-webhook-token")
    db = AgentAuthDb(None)

    authorize(webhook_request("Bearer global-webhook-token"), db)

    assert db.seen_hashes == []


def test_per_cluster_agent_token_authenticates_by_stored_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ALERTMANAGER_WEBHOOK_TOKEN_ENV, raising=False)
    db = AgentAuthDb({"workspace_id": "workspace-1", "cluster_id": "cluster-1"})

    authorize(webhook_request("Bearer cluster-agent-token"), db)

    assert db.seen_hashes == [hash_agent_token("cluster-agent-token")]


def test_agent_token_can_be_used_when_a_different_global_token_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ALERTMANAGER_WEBHOOK_TOKEN_ENV, "global-webhook-token")
    db = AgentAuthDb({"workspace_id": "workspace-1", "cluster_id": "cluster-1"})

    authorize(webhook_request("Bearer cluster-agent-token"), db)

    assert db.seen_hashes == [hash_agent_token("cluster-agent-token")]


@pytest.mark.parametrize(
    ("identity", "workspace_id", "cluster_id"),
    (
        (
            {"workspace_id": "workspace-other", "cluster_id": "cluster-1"},
            "workspace-1",
            "cluster-1",
        ),
        (
            {"workspace_id": "workspace-1", "cluster_id": "cluster-other"},
            "workspace-1",
            "cluster-1",
        ),
    ),
)
def test_agent_token_cannot_cross_query_scope(
    monkeypatch: pytest.MonkeyPatch,
    identity: dict[str, str],
    workspace_id: str,
    cluster_id: str,
) -> None:
    monkeypatch.delenv(ALERTMANAGER_WEBHOOK_TOKEN_ENV, raising=False)
    db = AgentAuthDb(identity)

    with pytest.raises(HTTPException) as exc:
        authorize(
            webhook_request("Bearer cluster-agent-token"),
            db,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
        )

    assert exc.value.status_code == 401
    assert exc.value.detail == WEBHOOK_TOKEN_INVALID


@pytest.mark.parametrize(
    "authorization",
    ("", "cluster-agent-token", "Basic cluster-agent-token", "Bearer invalid-token"),
)
def test_webhook_authentication_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    authorization: str,
) -> None:
    monkeypatch.delenv(ALERTMANAGER_WEBHOOK_TOKEN_ENV, raising=False)
    db = AgentAuthDb(None)

    with pytest.raises(HTTPException) as exc:
        authorize(webhook_request(authorization), db)

    assert exc.value.status_code == 401
    assert exc.value.detail == WEBHOOK_TOKEN_INVALID


def standard_sli_payload(
    labels: dict[str, str],
    *,
    annotations: dict[str, str] | None = None,
) -> AlertmanagerWebhookRequest:
    return AlertmanagerWebhookRequest(
        receiver="kyro-rca",
        status="firing",
        alerts=[
            AlertmanagerAlert(
                status="firing",
                labels={
                    "alertname": "OpsiaSliFailureRatioHigh",
                    **labels,
                },
                annotations=(
                    {
                        "opsia_observed_value": "0.79",
                        "opsia_threshold": "0.2",
                    }
                    if annotations is None
                    else annotations
                ),
                startsAt="2026-07-24T01:00:00Z",
                fingerprint="sli-alert",
            )
        ],
        groupKey="sli-group",
    )


def test_standard_sli_alert_requires_complete_resource_identity() -> None:
    validate_alertmanager_sli_labels(
        standard_sli_payload(
            {
                "opsia_namespace": "sandbox",
                "opsia_resource_kind": "Deployment",
                "opsia_resource_name": "matchmaking-api",
                "opsia_service": "matchmaking",
                "opsia_sli": "admission",
                "opsia_symptom": "admission_failure",
            }
        )
    )


@pytest.mark.parametrize("disposition", ("orphan", "terminal"))
def test_alertmanager_webhook_starts_new_occurrence_for_closed_lineage(
    monkeypatch: pytest.MonkeyPatch,
    disposition: str,
) -> None:
    monkeypatch.setenv(ALERTMANAGER_WEBHOOK_TOKEN_ENV, "global-webhook-token")
    db = AlertmanagerLifecycleDb(disposition)

    response = asyncio.run(
        alertmanager_webhook(
            standard_sli_payload(
                {
                    "opsia_namespace": "sandbox",
                    "opsia_resource_kind": "Deployment",
                    "opsia_resource_name": "api-server",
                    "opsia_service": "api-server",
                    "opsia_sli": "admission",
                    "opsia_symptom": "admission_failure",
                }
            ),
            webhook_request("Bearer global-webhook-token"),
            cluster_id="cluster-1",
            workspace_id="workspace-1",
            events=AlertmanagerEvents(),
            db=db,
        )
    )

    assert response.correlation_id == "incident-new"
    assert len(db.rotations) == 1
    assert db.alert_events[0]["incident_id"] == "incident-new"


@pytest.mark.parametrize("disposition", ("active", "pending"))
def test_alertmanager_webhook_deduplicates_live_lineage(
    monkeypatch: pytest.MonkeyPatch,
    disposition: str,
) -> None:
    monkeypatch.setenv(ALERTMANAGER_WEBHOOK_TOKEN_ENV, "global-webhook-token")
    db = AlertmanagerLifecycleDb(disposition)

    response = asyncio.run(
        alertmanager_webhook(
            standard_sli_payload(
                {
                    "opsia_namespace": "sandbox",
                    "opsia_resource_kind": "Deployment",
                    "opsia_resource_name": "api-server",
                    "opsia_service": "api-server",
                    "opsia_sli": "admission",
                    "opsia_symptom": "admission_failure",
                }
            ),
            webhook_request("Bearer global-webhook-token"),
            cluster_id="cluster-1",
            workspace_id="workspace-1",
            events=AlertmanagerEvents(),
            db=db,
        )
    )

    assert response.correlation_id == "incident-old"
    assert db.rotations == []
    assert db.alert_events[0]["incident_id"] == "incident-old"


def test_new_alert_start_creates_a_new_attempt_under_the_unresolved_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh Alertmanager startsAt is a new attempt, not the PIN identity itself."""

    monkeypatch.setenv(ALERTMANAGER_WEBHOOK_TOKEN_ENV, "global-webhook-token")
    db = AlertmanagerOpenIncidentDb()

    response = asyncio.run(
        alertmanager_webhook(
            standard_sli_payload(
                {
                    "opsia_namespace": "sandbox",
                    "opsia_resource_kind": "Deployment",
                    "opsia_resource_name": "api-server",
                    "opsia_service": "api-server",
                    "opsia_sli": "admission",
                    "opsia_symptom": "admission_failure",
                }
            ),
            webhook_request("Bearer global-webhook-token"),
            cluster_id="cluster-1",
            workspace_id="workspace-1",
            events=AlertmanagerEvents(),
            db=db,
        )
    )

    recorded_correlation = db.recorded[0]["event_envelope"].correlation_id
    assert recorded_correlation != "incident-open"
    assert response.correlation_id == recorded_correlation
    assert db.alert_events[0]["incident_id"] == recorded_correlation


@pytest.mark.parametrize(
    "missing_label",
    (
        "opsia_namespace",
        "opsia_resource_kind",
        "opsia_resource_name",
        "opsia_service",
        "opsia_sli",
        "opsia_symptom",
    ),
)
def test_standard_sli_alert_rejects_blank_resource_identity(
    missing_label: str,
) -> None:
    labels = {
        "opsia_namespace": "sandbox",
        "opsia_resource_kind": "Deployment",
        "opsia_resource_name": "matchmaking-api",
        "opsia_service": "matchmaking",
        "opsia_sli": "admission",
        "opsia_symptom": "admission_failure",
    }
    labels[missing_label] = ""

    with pytest.raises(HTTPException) as exc:
        validate_alertmanager_sli_labels(standard_sli_payload(labels))

    assert exc.value.status_code == 422
    assert exc.value.detail == STANDARD_SLI_LABELS_INVALID


@pytest.mark.parametrize(
    "annotations",
    (
        {},
        {"opsia_observed_value": "not-a-number", "opsia_threshold": "0.2"},
        {"opsia_observed_value": "0.8", "opsia_threshold": ""},
        {"opsia_observed_value": "nan", "opsia_threshold": "0.2"},
        {"opsia_observed_value": "1.1", "opsia_threshold": "0.2"},
        {"opsia_observed_value": "0.1", "opsia_threshold": "0.2"},
    ),
)
def test_standard_sli_alert_rejects_missing_or_unbounded_measurements(
    annotations: dict[str, str],
) -> None:
    labels = {
        "opsia_namespace": "sandbox",
        "opsia_resource_kind": "Deployment",
        "opsia_resource_name": "matchmaking-api",
        "opsia_service": "matchmaking",
        "opsia_sli": "admission",
        "opsia_symptom": "admission_failure",
    }

    with pytest.raises(HTTPException) as exc:
        validate_alertmanager_sli_labels(
            standard_sli_payload(labels, annotations=annotations)
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == STANDARD_SLI_MEASUREMENT_INVALID
