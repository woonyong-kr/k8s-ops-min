"""AI 대화 이벤트 계약."""

from __future__ import annotations

from dataclasses import dataclass

from packages.contracts.event_bus.bodies.base import EventBody, JsonObject
from packages.contracts.event_bus.registry import event
from packages.contracts.event_bus.subjects import EventSubject
from packages.contracts.identity import DEFAULT_WORKSPACE_ID


@event(EventSubject.AI_MESSAGE_RECEIVED)
@dataclass(frozen=True)
class AiMessageReceivedBody(EventBody):
    conversation_id: str
    message_id: str
    content: str
    agent: str
    user_id: str
    workspace_id: str = DEFAULT_WORKSPACE_ID
    context: JsonObject | None = None


@event(EventSubject.AI_MESSAGE_RESPONDED)
@dataclass(frozen=True)
class AiMessageRespondedBody(EventBody):
    conversation_id: str
    request_message_id: str
    response_message_id: str
    content: str
    agent: str
    workspace_id: str = DEFAULT_WORKSPACE_ID
    metadata: JsonObject | None = None


@event(EventSubject.AI_MESSAGE_FAILED)
@dataclass(frozen=True)
class AiMessageFailedBody(EventBody):
    conversation_id: str
    request_message_id: str
    reason: str
    agent: str
    workspace_id: str = DEFAULT_WORKSPACE_ID
    metadata: JsonObject | None = None
