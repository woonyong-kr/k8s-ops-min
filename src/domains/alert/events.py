"""alert-worker 이벤트 body."""

from __future__ import annotations

from dataclasses import dataclass

from domains.command.events import CommandRequestedBody
from packages.contracts.event_bus.bodies.base import EventBody, JsonObject
from packages.contracts.event_bus.registry import event
from packages.contracts.event_bus.subjects import EventSubject
from packages.contracts.gitops import (
    DEFAULT_APPLICATION_ID,
    DEFAULT_DEPLOYMENT_BINDING_ID,
    DEFAULT_ENVIRONMENT,
    DEFAULT_WORKFLOW_RUN_ID,
)
from packages.contracts.identity import DEFAULT_WORKSPACE_ID


@event(EventSubject.ALERT_REQUESTED)
@dataclass(frozen=True)
class AlertRequestedBody(EventBody):
    """alert.requested — 알람 발송, 통과 시 다음 이벤트 연결 요청"""

    cluster_id: str
    namespace: str
    severity: str
    message: str
    reason: str
    next_command: CommandRequestedBody | None = None
    workspace_id: str = DEFAULT_WORKSPACE_ID
    application_id: str = DEFAULT_APPLICATION_ID
    workflow_run_id: str = DEFAULT_WORKFLOW_RUN_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    environment: str = DEFAULT_ENVIRONMENT
    # None은 기존 발행자의 severity 기반 전체 라우팅, 비어 있지 않은 list는
    # 규칙이 선택한 채널만 허용. 빈 list인 규칙 전이는 notifier가 인앱 전용으로 남긴다.
    channel_ids: list[str] | None = None


@event(EventSubject.ALERT_DISPATCHED)
@dataclass(frozen=True)
class AlertDispatchedBody(EventBody):
    """alert.dispatched — 알람 전송 경계 통과"""

    cluster_id: str
    namespace: str
    severity: str
    channel: str
    mode: str
    workspace_id: str = DEFAULT_WORKSPACE_ID
    application_id: str = DEFAULT_APPLICATION_ID
    workflow_run_id: str = DEFAULT_WORKFLOW_RUN_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    environment: str = DEFAULT_ENVIRONMENT


@event(EventSubject.ALERT_REJECTED)
@dataclass(frozen=True)
class AlertRejectedBody(EventBody):
    """alert.rejected — 알람/정책 게이트의 다음 실행 차단"""

    reason: str
    requested: JsonObject
