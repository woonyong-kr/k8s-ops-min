"""대화 시스템 프롬프트 빌더 — 대화 정체성/요청 컨텍스트를 엔진에 주입.

노출 텍스트는 domains.ai.messages 카탈로그에서 조회(로케일 번역 가능).
멀티턴 문맥(히스토리)과 도구 프로토콜 안내는 ConversationEngine 이 담당함 —
여기는 "누구와의 어떤 대화인가"만 서술함.
"""

from __future__ import annotations

import json
from typing import Any

from domains.ai.messages import text


def build_system_prompt(evt: Any, locale: str | None = None) -> str:
    """이벤트(AiMessageReceivedBody) → 대화 엔진용 시스템 프롬프트."""
    request_context = json.dumps(evt.context or {}, ensure_ascii=False, sort_keys=True)
    return (
        f"{text('chat.system_prompt', locale)}\n\n"
        f"Agent: {evt.agent}\n"
        f"Conversation: {evt.conversation_id}\n"
        f"Workspace: {evt.workspace_id}\n"
        f"Context: {request_context}"
    )
