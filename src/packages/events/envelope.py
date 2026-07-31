from __future__ import annotations

import uuid

from packages.config.settings import now_iso
from packages.contracts.event_bus.interfaces import EventEnvelope, JsonObject
from packages.events.context import current_event_workspace, normalized_workspace_id


def event(
    subject: str,
    source: str,
    payload: JsonObject,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    workspace_id: str | None = None,
) -> EventEnvelope:
    # correlation_id 가 없으면 payload 가 실어온 값, 그것도 없으면 자기 자신을
    # 흐름 시작점. causation 은 직전 이벤트(없으면 None=뿌리).
    event_id = str(uuid.uuid4())
    correlation = correlation_id or payload.get("correlation_id") or event_id
    return EventEnvelope(
        event_id=event_id,
        subject=subject,
        source=source,
        correlation_id=correlation,
        causation_id=causation_id,
        created_at=now_iso(),
        payload=payload,
        workspace_id=normalized_workspace_id(workspace_id) or current_event_workspace(),
    )
