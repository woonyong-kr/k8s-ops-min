"""alert 도메인 HTTP 라우터 — 워크스페이스별 알림 채널(라우팅 룰) 관리(admin 세션)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from domains.alert.delivery import post_alert_webhook
from domains.alert.events import AlertRequestedBody
from domains.alert.schemas import (
    AlertEventResponse,
    AlertEventSeverity,
    AlertEventStatus,
    AlertIncidentPromotionResponse,
    AlertRuleCreatedResponse,
    AlertRuleCreateRequest,
    AlertRuleListResponse,
    AlertRulePatchRequest,
    AlertRuleResponse,
)
from domains.alert.service import (
    AlertChannelNotFoundError,
    AlertEventNotFoundError,
    AlertEventStateConflictError,
    AlertRuleNotFoundError,
    acknowledge_alert_event_occurrence,
    alert_event_response,
    alert_rule_response,
    create_alert_rule_setting,
    promote_alert_event_occurrence,
    update_alert_rule_setting,
)
from domains.identity.dependencies import require_admin_session
from packages.contracts.auth import Actor
from packages.contracts.gateway import limits as gateway_limits
from packages.contracts.gateway import params as gateway_params
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.requests import AlertChannelTestRequest, AlertChannelUpsertRequest
from packages.contracts.gateway.responses import (
    AlertChannelListResponse,
    AlertChannelResponse,
    AlertChannelTestResponse,
)
from packages.contracts.identity import DEFAULT_WORKSPACE_ID
from packages.runtime.dependencies import get_db, get_events
from packages.security.outbound_url import UnsafeOutboundUrlError, validate_outbound_url_syntax
from packages.storage.engine import unit_of_work_or_null
from packages.storage.retry import to_thread_db_retry

router = APIRouter()
NOT_FOUND_CODE = 404
CHANNEL_NOT_FOUND = "alert channel not found"
UNSAFE_WEBHOOK_URL_CODE = "unsafe_webhook_url"
UNSAFE_WEBHOOK_URL_DETAIL = "안전하지 않은 웹훅 URL입니다."
ALERT_CHANNEL_NOT_FOUND_CODE = "alert_channel_not_found"
ALERT_CHANNEL_NOT_FOUND_DETAIL = "선택한 알림 채널을 찾을 수 없습니다."
ALERT_RULE_NOT_FOUND = "alert rule not found"
ALERT_EVENT_NOT_FOUND = "alert event not found"


@router.post(
    gateway_routes.ALERT_RULES_PATH,
    response_model=AlertRuleCreatedResponse,
    status_code=201,
)
async def create_alert_rule(
    payload: AlertRuleCreateRequest,
    response: Response,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> AlertRuleCreatedResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    try:
        created = create_alert_rule_setting(
            db,
            payload,
            workspace_id=workspace_id,
            actor_id=str(current.user_id),
        )
    except AlertChannelNotFoundError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": ALERT_CHANNEL_NOT_FOUND_CODE,
                "detail": ALERT_CHANNEL_NOT_FOUND_DETAIL,
            },
        ) from exc
    response.headers["Location"] = gateway_routes.ALERT_RULE_PATH.format(rule_id=created.rule_id)
    return created


@router.get(gateway_routes.ALERT_RULES_PATH, response_model=AlertRuleListResponse)
async def list_alert_rules(
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> AlertRuleListResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    return AlertRuleListResponse(
        rules=[alert_rule_response(row) for row in db.list_alert_rules(workspace_id)]
    )


@router.patch(gateway_routes.ALERT_RULE_PATH, response_model=AlertRuleResponse)
async def update_alert_rule(
    rule_id: str,
    payload: AlertRulePatchRequest,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> AlertRuleResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    try:
        return update_alert_rule_setting(
            db,
            rule_id,
            payload,
            workspace_id=workspace_id,
        )
    except AlertChannelNotFoundError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": ALERT_CHANNEL_NOT_FOUND_CODE,
                "detail": ALERT_CHANNEL_NOT_FOUND_DETAIL,
            },
        ) from exc
    except AlertRuleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=ALERT_RULE_NOT_FOUND) from exc


@router.delete(gateway_routes.ALERT_RULE_PATH, status_code=204, response_model=None)
async def delete_alert_rule(
    rule_id: str,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> None:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    if not db.delete_alert_rule(workspace_id, rule_id):
        raise HTTPException(status_code=404, detail=ALERT_RULE_NOT_FOUND)


@router.get(gateway_routes.ALERT_EVENTS_PATH, response_model=list[AlertEventResponse])
async def list_alert_events(
    from_time: datetime | None = Query(
        default=None,
        alias=gateway_params.TIME_RANGE_FROM_QUERY,
    ),
    to_time: datetime | None = Query(
        default=None,
        alias=gateway_params.TIME_RANGE_TO_QUERY,
    ),
    rule_id: str | None = Query(default=None, min_length=1, max_length=120),
    severity: AlertEventSeverity | None = None,
    status: AlertEventStatus | None = None,
    limit: int = Query(
        default=gateway_limits.ALERT_EVENT_DEFAULT_LIMIT,
        ge=1,
        le=gateway_limits.ALERT_EVENT_MAX_LIMIT,
    ),
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> list[AlertEventResponse]:
    if from_time is not None and to_time is not None and from_time > to_time:
        raise HTTPException(status_code=422, detail="from must not be after to")
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    rows = db.list_alert_events(
        workspace_id,
        from_time=from_time,
        to_time=to_time,
        rule_id=rule_id,
        severity=severity,
        status=status,
        limit=limit,
    )
    return [alert_event_response(row) for row in rows]


@router.get(gateway_routes.ALERT_EVENTS_STREAM_PATH)
async def stream_alert_events(
    request: Request,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> StreamingResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    return StreamingResponse(
        _alert_event_stream(db, workspace_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _alert_event_stream(
    db: Any,
    workspace_id: str,
    request: Request,
) -> AsyncIterator[str]:
    """Keep the browser current while PostgreSQL remains the durable source."""
    # The browser first loads the durable list, then opens this stream. Replaying
    # the bounded current set closes the race between those two requests.
    seen: set[tuple[str, str]] = set()
    yield "event: ready\ndata: {}\n\n"
    heartbeat = 0
    while not await request.is_disconnected():
        await asyncio.sleep(1)
        rows = await to_thread_db_retry(
            db.list_alert_events,
            workspace_id,
            limit=gateway_limits.ALERT_EVENT_MAX_LIMIT,
        )
        signatures = {_alert_event_signature(row) for row in rows}
        changed = [row for row in reversed(rows) if _alert_event_signature(row) not in seen]
        for row in changed:
            event = alert_event_response(row)
            payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
            yield f"id: {event.event_id}\nevent: alert\ndata: {payload}\n\n"
        seen = signatures
        heartbeat += 1
        if heartbeat >= 15:
            heartbeat = 0
            yield ": keep-alive\n\n"


def _alert_event_signature(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("event_id") or ""), str(row.get("updated_at") or "")


@router.post(gateway_routes.ALERT_EVENT_ACK_PATH, response_model=AlertEventResponse)
async def acknowledge_alert_event(
    event_id: str,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> AlertEventResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    try:
        return acknowledge_alert_event_occurrence(
            db,
            event_id,
            workspace_id=workspace_id,
            actor_id=str(current.user_id),
        )
    except AlertEventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=ALERT_EVENT_NOT_FOUND) from exc
    except AlertEventStateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    gateway_routes.ALERT_EVENT_PROMOTE_INCIDENT_PATH,
    response_model=AlertIncidentPromotionResponse,
)
async def promote_alert_event_to_incident(
    event_id: str,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
) -> AlertIncidentPromotionResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    try:
        with unit_of_work_or_null(db):
            response, body = promote_alert_event_occurrence(
                db,
                event_id,
                workspace_id=workspace_id,
                actor_id=str(current.user_id),
            )
            if body is not None:
                await events.accept_body(
                    body,
                    correlation_id=response.incident_id,
                    actor=Actor(str(current.user_id), tuple(current.roles)),
                )
    except AlertEventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=ALERT_EVENT_NOT_FOUND) from exc
    return response


@router.get(gateway_routes.ALERT_CHANNELS_PATH, response_model=AlertChannelListResponse)
async def list_alert_channels(
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> AlertChannelListResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    channels = db.list_alert_channels(workspace_id)
    return AlertChannelListResponse(
        channels=[AlertChannelResponse(**channel) for channel in channels]
    )


@router.post(gateway_routes.ALERT_CHANNELS_PATH, response_model=AlertChannelResponse)
async def upsert_alert_channel(
    payload: AlertChannelUpsertRequest,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> AlertChannelResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    try:
        validate_outbound_url_syntax(payload.url)
    except UnsafeOutboundUrlError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": UNSAFE_WEBHOOK_URL_CODE, "detail": UNSAFE_WEBHOOK_URL_DETAIL},
        ) from exc
    require_alert_channel_activation_test(payload, workspace_id, db)
    try:
        saved = db.upsert_alert_channel(
            {
                **payload.model_dump(exclude={"channel_id"}),
                **({"channel_id": payload.channel_id} if payload.channel_id else {}),
                "workspace_id": workspace_id,
            }
        )
    except LookupError as exc:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=CHANNEL_NOT_FOUND) from exc
    return AlertChannelResponse(**saved)


def require_alert_channel_activation_test(
    payload: AlertChannelUpsertRequest,
    workspace_id: str,
    db: Any,
) -> None:
    if not payload.enabled:
        return
    if not payload.channel_id:
        raise HTTPException(
            status_code=409,
            detail="save the alert channel disabled, test delivery, then enable it",
        )
    getter = getattr(db, "get_alert_channel", None)
    existing = getter(workspace_id, payload.channel_id) if callable(getter) else None
    if existing is None:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=CHANNEL_NOT_FOUND)
    if str(existing.get("url") or "") != payload.url:
        raise HTTPException(
            status_code=409,
            detail="test the updated webhook URL before enabling the alert channel",
        )
    if str(existing.get("last_test_status") or "").lower() != "passed":
        raise HTTPException(
            status_code=409,
            detail="a successful alert channel delivery test is required before enabling it",
        )


@router.post(gateway_routes.ALERT_CHANNEL_TEST_PATH, response_model=AlertChannelTestResponse)
async def test_alert_channel(
    payload: AlertChannelTestRequest,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> AlertChannelTestResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    alert = AlertRequestedBody(
        cluster_id="validation",
        namespace="validation",
        severity=payload.severity,
        message=payload.message,
        reason="alert channel validation",
        workspace_id=workspace_id,
    )
    result = await post_alert_webhook(payload.url, alert)
    if result.delivered:
        channel = record_channel_test_result(
            db,
            workspace_id,
            payload.channel_id,
            status="passed",
            detail="테스트 알림을 전송했습니다.",
            status_code=result.status_code,
        )
        return AlertChannelTestResponse(
            valid=True,
            delivered=True,
            detail="테스트 알림을 전송했습니다.",
            status_code=result.status_code,
            channel=channel,
        )
    if result.error == UNSAFE_WEBHOOK_URL_CODE:
        code = UNSAFE_WEBHOOK_URL_CODE
        detail = UNSAFE_WEBHOOK_URL_DETAIL
    else:
        code = "timeout" if result.error == "timeout" else "delivery_failed"
        detail = "테스트 알림 전송에 실패했습니다."
    channel = record_channel_test_result(
        db,
        workspace_id,
        payload.channel_id,
        status="failed",
        detail=detail,
        status_code=result.status_code,
    )
    return AlertChannelTestResponse(
        valid=False,
        delivered=False,
        code=code,
        detail=detail,
        status_code=result.status_code,
        channel=channel,
    )


def record_channel_test_result(
    db: Any,
    workspace_id: str,
    channel_id: str,
    *,
    status: str,
    detail: str,
    status_code: int | None,
) -> AlertChannelResponse | None:
    if not channel_id:
        return None
    recorder = getattr(db, "record_alert_channel_test", None)
    if not callable(recorder):
        return None
    try:
        row = recorder(
            workspace_id,
            channel_id,
            status=status,
            detail=detail,
            status_code=status_code,
        )
    except LookupError as exc:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=CHANNEL_NOT_FOUND) from exc
    return AlertChannelResponse(**row)


@router.delete(gateway_routes.ALERT_CHANNEL_PATH, status_code=204, response_model=None)
async def delete_alert_channel(
    channel_id: str,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> None:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    if not db.delete_alert_channel(workspace_id, channel_id):
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=CHANNEL_NOT_FOUND)
