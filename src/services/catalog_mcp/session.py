"""세션 예산과 감사 로그.

세션 예산 — 도구 호출은 값싸 보이지만 카탈로그 전체를 훑는 수단이 된다.
한 세션이 200회를 넘으면 그건 질문에 답하는 게 아니라 열거하는 것이다.
상한을 넘으면 거부하고, 거부 사실을 남긴다.

감사 로그 — session_id 만 남기면 "에이전트가 읽었다"까지만 안다. 사고가 난
뒤에 답해야 하는 질문은 "누구를 대신해 읽었나"다. principal_sub 를 함께
남기지 않으면 그 질문에 답할 수 없다.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, TextIO

SESSION_CALL_BUDGET = 200


class BudgetExceeded(Exception):
    """세션 호출 예산 초과. HTTP 로 치면 429 다."""

    retry_after_seconds = 3600


@dataclass
class Session:
    """한 MCP 연결의 수명 동안 유지되는 상태."""

    principal_sub: str
    subject_token: str
    session_id: str = field(default_factory=lambda: f"mcp-{uuid.uuid4()}")
    call_budget: int = SESSION_CALL_BUDGET
    calls_made: int = 0

    def charge(self) -> None:
        if self.calls_made >= self.call_budget:
            raise BudgetExceeded(f"session budget {self.call_budget} exhausted")
        self.calls_made += 1

    @property
    def remaining(self) -> int:
        return max(0, self.call_budget - self.calls_made)


class AuditLog:
    """구조화 감사 로그. 기본은 stderr — stdout 은 MCP 프로토콜이 쓴다."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self.records: list[dict[str, Any]] = []

    def record(
        self,
        *,
        session: Session,
        tool: str,
        outcome: str,
        correlation_id: str,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = {
            "ts": time.time(),
            "session_id": session.session_id,
            "principal_sub": session.principal_sub,
            "tool": tool,
            "outcome": outcome,
            "correlation_id": correlation_id,
            "calls_made": session.calls_made,
        }
        if detail:
            entry.update(detail)
        self.records.append(entry)
        print(json.dumps(entry, ensure_ascii=False), file=self._stream, flush=True)
        return entry


def new_correlation_id() -> str:
    return f"cid-{uuid.uuid4()}"
