"""AI 대화 HTTP 라우터."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import ValidationError

from domains.ai.context_facade import (
    AiResourceKind,
    answer_from_context,
    get_ai_resource,
    get_context_chat_llm,
    get_context_mcp_engine,
    list_ai_resources,
    suggestions_for_context,
)
from domains.ai.events import AiMessageReceivedBody
from domains.ai.repository import (
    ROLE_USER,
    STATUS_WAITING,
)
from domains.identity.dependencies import require_session
from packages.contracts.ai_conversation import (
    BOUNDED_MESSAGE_HISTORY_REASON,
    DEFAULT_CONVERSATION_MESSAGE_LIMIT,
    MAX_CONVERSATION_MESSAGE_LIMIT,
)
from packages.contracts.auth import Actor
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.requests import (
    AiAssistantContext,
    AiChatRequest,
    AiConversationCreateRequest,
    AiMessageCreateRequest,
)
from packages.contracts.gateway.responses import (
    AiChatResponse,
    AiConversationAcceptedResponse,
    AiConversationListResponse,
    AiConversationResponse,
    AiResourceSummary,
    AiSuggestionsResponse,
)
from packages.contracts.identity import DEFAULT_WORKSPACE_ID
from packages.runtime.dependencies import get_db, get_events
from packages.runtime.keyset_cursor import (
    INVALID_KEYSET_CURSOR,
    MAX_KEYSET_CURSOR_LENGTH,
    decode_keyset_cursor,
    encode_keyset_cursor,
)
from packages.storage.engine import unit_of_work_or_null
from packages.storage.retry import to_thread_db_retry

router = APIRouter()
DEFAULT_AGENT = "operations-chat"
NOT_FOUND = "conversation not found"
CONTEXT_STRING_FIELDS = (
    "cluster_id",
    "application_id",
    "diff_source",
    "workflow_run_id",
    "approval_id",
    "resource_type",
    "kind",
    "namespace",
    "name",
    "uid",
    "incident_id",
    "correlation_id",
    "symptom",
    "root_cause",
    "locale",
)
MAX_CONTEXT_VALUE_LENGTH = 253
MAX_AI_CONTEXT_QUERY_LENGTH = 16_384


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def title_for(payload: AiConversationCreateRequest) -> str:
    if payload.title:
        return payload.title
    collapsed = " ".join(payload.message.split())
    return collapsed[:80] if collapsed else "AI conversation"


def normalize_context(raw: dict[str, Any] | None) -> dict[str, str]:
    if not raw:
        return {}
    context: dict[str, str] = {}
    for key in CONTEXT_STRING_FIELDS:
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            context[key] = text[:MAX_CONTEXT_VALUE_LENGTH]
    return context


def parse_assistant_context(raw: str) -> AiAssistantContext:
    try:
        decoded = json.loads(raw)
        return AiAssistantContext.model_validate(decoded)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid AI assistant context") from exc


@router.post(
    gateway_routes.AI_CHAT_PATH,
    response_model=AiChatResponse,
    response_model_exclude_none=True,
)
async def chat_with_context(
    payload: AiChatRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    llm: Any | None = Depends(get_context_chat_llm),
    mcp_engine: Any | None = Depends(get_context_mcp_engine),
) -> AiChatResponse:
    """Return only current, authorized inventory facts with explicit evidence links."""
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    return await answer_from_context(
        db,
        current=current,
        workspace_id=workspace_id,
        context=payload.context,
        message=payload.message,
        llm=llm,
        mcp_engine=mcp_engine,
    )


@router.get(gateway_routes.AI_SUGGESTIONS_PATH, response_model=AiSuggestionsResponse)
async def list_context_suggestions(
    context: str = Query(min_length=2, max_length=MAX_AI_CONTEXT_QUERY_LENGTH),
    current: Any = Depends(require_session),
) -> AiSuggestionsResponse:
    del current  # Authentication is the endpoint boundary; suggestions contain no user data.
    return suggestions_for_context(parse_assistant_context(context))


@router.get(gateway_routes.AI_RESOURCES_PATH, response_model=list[AiResourceSummary])
async def list_context_resources(
    kind: AiResourceKind,
    cluster_id: str | None = Query(default=None, min_length=1, max_length=512),
    namespace: str | None = Query(default=None, max_length=253),
    limit: int = Query(default=50, ge=1, le=100),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> list[AiResourceSummary]:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    return list_ai_resources(
        db,
        current=current,
        workspace_id=workspace_id,
        kind=kind,
        cluster_id=cluster_id,
        namespace=namespace,
        limit=limit,
    )


@router.get(gateway_routes.AI_RESOURCE_PATH, response_model=AiResourceSummary)
async def get_context_resource(
    kind: AiResourceKind,
    namespace: str = Path(min_length=1, max_length=253),
    name: str = Path(min_length=1, max_length=253),
    cluster_id: str = Query(min_length=1, max_length=512),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> AiResourceSummary:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    return get_ai_resource(
        db,
        current=current,
        workspace_id=workspace_id,
        kind=kind,
        cluster_id=cluster_id,
        namespace=None if namespace == "_" else namespace,
        name=name,
    )


@router.post(
    gateway_routes.AI_CONVERSATIONS_PATH,
    response_model=AiConversationAcceptedResponse,
)
async def create_conversation(
    payload: AiConversationCreateRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
) -> AiConversationAcceptedResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    agent = payload.agent or DEFAULT_AGENT
    request_context = normalize_context(payload.context)
    conversation_id = new_id("aic")
    message_id = new_id("aim")
    title = title_for(payload)
    # 대화 생성·첫 메시지·이벤트 스테이징을 한 트랜잭션으로 — 부분 실패 시
    # 메시지 없는 대화(고아) 또는 이벤트 없는 메시지가 남지 않음.
    with unit_of_work_or_null(db):
        db.create_ai_conversation(
            {
                "conversation_id": conversation_id,
                "workspace_id": workspace_id,
                "user_id": current.user_id,
                "title": title,
                "agent": agent,
                "status": STATUS_WAITING,
                "context": request_context,
            }
        )
        db.append_ai_message(
            {
                "message_id": message_id,
                "conversation_id": conversation_id,
                "workspace_id": workspace_id,
                "role": ROLE_USER,
                "content": payload.message,
                "agent": agent,
                "metadata": {"source": "http"},
            }
        )
        accepted = await events.accept_body(
            AiMessageReceivedBody(
                conversation_id=conversation_id,
                message_id=message_id,
                content=payload.message,
                agent=agent,
                user_id=current.user_id,
                workspace_id=workspace_id,
                context=request_context,
            ),
            actor=Actor(current.user_id, tuple(current.roles)),
        )
    return AiConversationAcceptedResponse(
        accepted=True,
        conversation_id=conversation_id,
        message_id=message_id,
        event_id=accepted.event.event_id,
        correlation_id=accepted.event.correlation_id,
    )


@router.post(
    gateway_routes.AI_CONVERSATION_MESSAGES_PATH,
    response_model=AiConversationAcceptedResponse,
)
async def append_message(
    conversation_id: str,
    payload: AiMessageCreateRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
) -> AiConversationAcceptedResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    conversation = db.get_ai_conversation(workspace_id, conversation_id, user_id=current.user_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    agent = payload.agent or str(conversation["agent"])
    request_context = normalize_context(payload.context or conversation.get("context") or {})
    message_id = new_id("aim")
    # 메시지 추가·상태 갱신·이벤트 스테이징을 한 트랜잭션으로(부분 실패 고아 방지).
    with unit_of_work_or_null(db):
        db.append_ai_message(
            {
                "message_id": message_id,
                "conversation_id": conversation_id,
                "workspace_id": workspace_id,
                "role": ROLE_USER,
                "content": payload.message,
                "agent": agent,
                "metadata": {"source": "http"},
            }
        )
        db.mark_ai_conversation_status(workspace_id, conversation_id, STATUS_WAITING)
        accepted = await events.accept_body(
            AiMessageReceivedBody(
                conversation_id=conversation_id,
                message_id=message_id,
                content=payload.message,
                agent=agent,
                user_id=current.user_id,
                workspace_id=workspace_id,
                context=request_context,
            ),
            actor=Actor(current.user_id, tuple(current.roles)),
        )
    return AiConversationAcceptedResponse(
        accepted=True,
        conversation_id=conversation_id,
        message_id=message_id,
        event_id=accepted.event.event_id,
        correlation_id=accepted.event.correlation_id,
    )


@router.get(gateway_routes.AI_CONVERSATIONS_PATH, response_model=AiConversationListResponse)
async def list_conversations(
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> AiConversationListResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    return AiConversationListResponse(
        conversations=db.list_ai_conversations(workspace_id, user_id=current.user_id)
    )


@router.get(
    gateway_routes.AI_CONVERSATION_PATH,
    response_model=AiConversationResponse,
)
async def get_conversation(
    conversation_id: str,
    limit: int = Query(
        default=DEFAULT_CONVERSATION_MESSAGE_LIMIT,
        ge=1,
        le=MAX_CONVERSATION_MESSAGE_LIMIT,
    ),
    cursor: str | None = Query(default=None, max_length=MAX_KEYSET_CURSOR_LENGTH),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> AiConversationResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    cursor_scope = _conversation_cursor_scope(
        workspace_id,
        str(current.user_id),
        conversation_id,
    )
    try:
        decoded_cursor = (
            decode_keyset_cursor(cursor, expected_scope=cursor_scope)
            if cursor is not None
            else None
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=INVALID_KEYSET_CURSOR) from exc
    page = await to_thread_db_retry(
        db.get_ai_conversation_page,
        workspace_id,
        conversation_id,
        user_id=current.user_id,
        limit=limit,
        before=(
            (decoded_cursor.ordered_at, decoded_cursor.tie_breaker)
            if decoded_cursor is not None
            else None
        ),
    )
    if page is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    has_more = bool(page["has_more"])
    next_position = page.get("next_position")
    next_cursor = None
    if has_more and isinstance(next_position, dict):
        next_cursor = encode_keyset_cursor(
            scope=cursor_scope,
            ordered_at=next_position["ordered_at"],
            tie_breaker=str(next_position["tie_breaker"]),
        )
    return AiConversationResponse(
        conversation=page["conversation"],
        messages=page["messages"],
        limit=int(page["limit"]),
        has_more=has_more,
        next_cursor=next_cursor,
        messages_completeness="partial" if has_more else "complete",
        partial_reason_codes=[BOUNDED_MESSAGE_HISTORY_REASON] if has_more else [],
    )


def _conversation_cursor_scope(workspace_id: str, user_id: str, conversation_id: str) -> str:
    return f"ai-conversation-messages:{workspace_id}:{user_id}:{conversation_id}"


@router.delete(gateway_routes.AI_CONVERSATION_PATH, status_code=204)
async def delete_conversation(
    conversation_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> Response:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    deleted = db.delete_ai_conversation(workspace_id, conversation_id, user_id=current.user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return Response(status_code=204)


@router.delete(gateway_routes.AI_CONVERSATIONS_PATH, status_code=204)
async def delete_conversations(
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> Response:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    db.delete_ai_conversations(
        workspace_id,
        user_id=current.user_id,
    )
    return Response(status_code=204)
