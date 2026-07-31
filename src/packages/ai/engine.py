"""대화 엔진 — 시스템 프롬프트 + 히스토리 + 사용자 메시지 → LLM 도구 호출 루프.

순수 오케스트레이션 계층임: NATS/HTTP/DB 를 모르고, 저장소 접근은 ToolContext.db
로만 흘러듦(이벤트 배선은 서비스 레이어 책임). LLM 응답 프로토콜(엄격 JSON):

    {"type": "final", "content": "..."}                        # 최종 답변
    {"type": "tool_call", "tool": "이름", "arguments": {...}}   # 도구 호출 요청

도구 목록/파라미터 안내문은 레지스트리에서 생성해 시스템 프롬프트에 주입함.
tool_call 이면 레지스트리로 실행 → 결과를 transcript 에 덧붙여 재호출(루프).
루프 상한(max_tool_calls, 기본 4) 초과 시 최종 답변을 강제함.
JSON 파싱 실패는 전체 텍스트를 최종 답변으로 취급(우아한 폴백).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from packages.ai.llm import LlmClient
from packages.ai.tools import ToolContext, ToolRegistry
from packages.security.log_lines import redact_log_line, redact_sensitive_value

DEFAULT_MAX_TOOL_CALLS = 4
MAX_TOOL_CALLS_LIMIT = 8

REPLY_TYPE_FINAL = "final"
REPLY_TYPE_TOOL_CALL = "tool_call"

# 프로토콜 안내문 — 사용자 노출이 아닌 모델 지시문이라 i18n 대상이 아님.
_PROTOCOL_HEADER = (
    "## Response protocol (strict JSON)\n"
    "Reply with exactly one JSON object and nothing else:\n"
    '- Final answer: {"type": "final", "content": "<answer for the user>"}\n'
    '- Tool call:   {"type": "tool_call", "tool": "<tool name>", "arguments": {...}}\n'
    "Tool results are appended to the transcript as [tool:<name>] entries.\n"
)
_FORCE_FINAL_NOTICE = (
    "[system] Tool call budget exhausted. Reply now with "
    '{"type": "final", "content": "..."} using the information gathered so far.'
)


@dataclass(frozen=True)
class EngineResult:
    """엔진 1회 응답 결과 — 최종 답변 + 도구 호출 흔적."""

    content: str
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    raw_length: int = 0


class ConversationEngine:
    """레지스트리 기반 도구 호출 루프를 도는 대화 오케스트레이터."""

    def __init__(
        self,
        llm: LlmClient,
        registry: ToolRegistry,
        *,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.max_tool_calls = _validate_max_tool_calls(max_tool_calls)

    def tools_prompt_section(self) -> str:
        """레지스트리에서 생성한 도구 안내 섹션 — 시스템 프롬프트에 주입됨."""
        rows = [_PROTOCOL_HEADER, "## Available tools"]
        specs = self.registry.tools()
        if not specs:
            rows.append("(no tools registered — always reply with a final answer)")
        for spec in specs:
            rows.append(f"- {spec.name}: {spec.description}")
            for param, schema in spec.parameters.items():
                required = " (required)" if schema.get("required") is True else ""
                rows.append(
                    f"    - {param}: {schema.get('type', 'any')}{required}"
                    f" — {schema.get('description', '')}"
                )
        return "\n".join(rows)

    async def respond(
        self,
        *,
        system_prompt: str,
        history: list[dict[str, Any]],
        user_message: str,
        context: ToolContext,
        llm_timeout_seconds: float | None = None,
    ) -> EngineResult:
        """대화 1턴 처리 — tool_call 루프를 돌고 최종 답변을 반환함."""
        transcript = [f"[{row.get('role', 'user')}] {row.get('content', '')}" for row in history]
        transcript.append(f"[user] {user_message}")
        tool_trace: list[dict[str, Any]] = []
        raw_length = 0

        for _ in range(self.max_tool_calls):
            raw = await self._complete(system_prompt, transcript, llm_timeout_seconds)
            raw_length += len(raw)
            reply = _parse_reply(raw)
            if reply.get("type") != REPLY_TYPE_TOOL_CALL:
                return EngineResult(str(reply.get("content", "")), tool_trace, raw_length)
            trace = await self._run_tool(reply, context)
            tool_trace.append(trace)
            transcript.append(f"[tool:{trace['tool']}] {json.dumps(trace, ensure_ascii=False)}")

        # 도구 예산 소진 — 최종 답변 강제(또다시 tool_call 이면 원문을 답변으로 폴백).
        transcript.append(_FORCE_FINAL_NOTICE)
        raw = await self._complete(system_prompt, transcript, llm_timeout_seconds)
        raw_length += len(raw)
        reply = _parse_reply(raw)
        content = reply.get("content") if reply.get("type") == REPLY_TYPE_FINAL else raw.strip()
        return EngineResult(str(content or ""), tool_trace, raw_length)

    async def _complete(
        self, system_prompt: str, transcript: list[str], timeout: float | None
    ) -> str:
        prompt = (
            f"{system_prompt}\n\n"
            f"{self.tools_prompt_section()}\n\n"
            f"## Transcript\n{chr(10).join(transcript)}"
        )
        if timeout is None:
            return await self.llm.complete(prompt)
        return await asyncio.wait_for(self.llm.complete(prompt), timeout=timeout)

    async def _run_tool(self, reply: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        """도구 1회 실행 — 미등록/인자 오류/핸들러 예외는 오류로 기록해 LLM 에 회신함."""
        tool = str(reply.get("tool", ""))
        raw_arguments = reply.get("arguments")
        safe_tool = str(redact_sensitive_value(tool))
        safe_arguments = redact_sensitive_value(raw_arguments)
        if not isinstance(raw_arguments, dict):
            return {
                "tool": safe_tool,
                "arguments": safe_arguments,
                "ok": False,
                "error": "tool arguments must be an object",
            }
        try:
            result = await self.registry.execute(tool, context, raw_arguments)
            return {
                "tool": safe_tool,
                "arguments": safe_arguments,
                "ok": True,
                "result": redact_sensitive_value(result),
            }
        except Exception as exc:
            return {
                "tool": safe_tool,
                "arguments": safe_arguments,
                "ok": False,
                "error": redact_log_line(str(exc)),
            }


def _parse_reply(raw: str) -> dict[str, Any]:
    """LLM 응답 파싱 — 프로토콜 JSON 이 아니면 전체 텍스트를 최종 답변으로 취급함."""
    try:
        parsed = json.loads(raw.strip())
    except (ValueError, TypeError):
        return {"type": REPLY_TYPE_FINAL, "content": raw.strip()}
    if isinstance(parsed, dict) and parsed.get("type") in {REPLY_TYPE_FINAL, REPLY_TYPE_TOOL_CALL}:
        return parsed
    return {"type": REPLY_TYPE_FINAL, "content": raw.strip()}


def _validate_max_tool_calls(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("max_tool_calls must be an integer")
    if value < 1 or value > MAX_TOOL_CALLS_LIMIT:
        raise ValueError(f"max_tool_calls must be between 1 and {MAX_TOOL_CALLS_LIMIT}")
    return value


__all__ = [
    "DEFAULT_MAX_TOOL_CALLS",
    "MAX_TOOL_CALLS_LIMIT",
    "ConversationEngine",
    "EngineResult",
]
