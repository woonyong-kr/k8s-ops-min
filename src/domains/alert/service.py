"""Opsia 알림 규칙 생성 서비스."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from domains.alert.schemas import (
    AlertEventResponse,
    AlertIncidentPromotionResponse,
    AlertRuleCreatedResponse,
    AlertRuleCreateRequest,
    AlertRulePatchRequest,
    AlertRuleResponse,
)
from domains.rca.events import Evidence, EvidenceBuiltBody


class AlertChannelNotFoundError(ValueError):
    """규칙이 현재 워크스페이스에 없는 채널을 참조함."""


class AlertRuleNotFoundError(LookupError):
    """현재 워크스페이스에 규칙이 없음."""


class AlertEventNotFoundError(LookupError):
    """현재 워크스페이스에 알림 발생이 없음."""


class AlertEventStateConflictError(ValueError):
    """현재 상태에서는 요청한 발생 처리를 할 수 없음."""


def create_alert_rule_setting(
    db: Any,
    payload: AlertRuleCreateRequest,
    *,
    workspace_id: str,
    actor_id: str,
    id_factory: Callable[[], str] | None = None,
) -> AlertRuleCreatedResponse:
    """Opsia DB에만 저장할 규칙을 만들고 식별자를 반환한다."""
    _require_workspace_channels(db, workspace_id, payload.channels)
    rule_id = (id_factory or _new_rule_id)()
    saved = db.create_alert_rule(
        {
            "rule_id": rule_id,
            "workspace_id": workspace_id,
            "created_by": actor_id,
            **payload.model_dump(),
        }
    )
    return AlertRuleCreatedResponse(rule_id=str(saved.get("rule_id") or rule_id))


def update_alert_rule_setting(
    db: Any,
    rule_id: str,
    payload: AlertRulePatchRequest,
    *,
    workspace_id: str,
) -> AlertRuleResponse:
    changes = payload.model_dump(exclude_unset=True)
    channels = changes.get("channels")
    if isinstance(channels, list):
        _require_workspace_channels(db, workspace_id, channels)
    saved = db.update_alert_rule(workspace_id, rule_id, changes)
    if saved is None:
        raise AlertRuleNotFoundError(rule_id)
    return alert_rule_response(saved)


def alert_rule_response(row: dict[str, Any]) -> AlertRuleResponse:
    fields = AlertRuleResponse.model_fields
    return AlertRuleResponse.model_validate({key: row.get(key) for key in fields})


def alert_event_response(row: dict[str, Any]) -> AlertEventResponse:
    fields = AlertEventResponse.model_fields
    return AlertEventResponse.model_validate({key: row.get(key) for key in fields})


def acknowledge_alert_event_occurrence(
    db: Any,
    event_id: str,
    *,
    workspace_id: str,
    actor_id: str,
) -> AlertEventResponse:
    try:
        saved = db.acknowledge_alert_event(workspace_id, event_id, actor_id)
    except ValueError as exc:
        raise AlertEventStateConflictError(str(exc)) from exc
    if saved is None:
        raise AlertEventNotFoundError(event_id)
    return alert_event_response(saved)


def promote_alert_event_occurrence(
    db: Any,
    event_id: str,
    *,
    workspace_id: str,
    actor_id: str,
    id_factory: Callable[[], str] | None = None,
) -> tuple[AlertIncidentPromotionResponse, EvidenceBuiltBody | None]:
    proposed_incident_id = (id_factory or _new_incident_id)()
    result = db.promote_alert_event(
        workspace_id,
        event_id,
        proposed_incident_id,
        actor_id,
    )
    if result is None:
        raise AlertEventNotFoundError(event_id)
    saved, created = result
    event = alert_event_response(saved)
    incident_id = event.incident_id
    if not incident_id:
        raise RuntimeError("promoted alert event is missing an incident id")
    response = AlertIncidentPromotionResponse(incident_id=incident_id)
    if not created:
        return response, None
    return response, _evidence_body_from_alert_event(
        event,
        workspace_id=workspace_id,
        incident_id=incident_id,
    )


def _evidence_body_from_alert_event(
    event: AlertEventResponse,
    *,
    workspace_id: str,
    incident_id: str,
) -> EvidenceBuiltBody:
    symptom = event.rule_name or "외부 알림"
    evidence = Evidence(
        cluster_id=event.subject.cluster,
        kubernetes={
            "resource": {
                "kind": event.subject.kind,
                "name": event.subject.name,
                "namespace": event.subject.namespace,
            },
            "symptom": symptom,
            "severity": event.severity,
            "category": "external_alert",
            "first_seen_at": event.fired_at.isoformat(),
        },
        metrics={
            "alert_event": {
                "event_id": event.event_id,
                "source": event.source,
                "observed_value": event.observed_value,
                "threshold": event.threshold,
                "evidence": [item.model_dump(mode="json") for item in event.evidence],
            }
        },
        logs=[],
        traces={},
        object_ref=f"alert-event:{event.event_id}",
        workspace_id=workspace_id,
    )
    return EvidenceBuiltBody(
        evidence=evidence,
        correlation_id=incident_id,
        kind="alert_event",
        summary={
            "event_id": event.event_id,
            "rule_name": symptom,
            "severity": event.severity,
            "promoted_by_operator": True,
        },
    )


def _require_workspace_channels(db: Any, workspace_id: str, channel_ids: list[str]) -> None:
    if not channel_ids:
        return
    getter = getattr(db, "get_alert_channel", None)
    if not callable(getter):
        raise RuntimeError("alert channel repository is unavailable")
    for channel_id in channel_ids:
        if getter(workspace_id, channel_id) is None:
            raise AlertChannelNotFoundError(channel_id)


def _new_rule_id() -> str:
    return f"alr-{uuid.uuid4()}"


def _new_incident_id() -> str:
    return f"inc-alert-{uuid.uuid4()}"
