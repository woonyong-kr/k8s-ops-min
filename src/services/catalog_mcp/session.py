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
BUDGET_WINDOW_SECONDS = 3600


class BudgetExceeded(Exception):
    """세션 호출 예산 초과. HTTP 로 치면 429 다."""

    def __init__(self, message: str, retry_after_seconds: int = BUDGET_WINDOW_SECONDS) -> None:
        super().__init__(message)
        # 창이 열릴 때까지 남은 시간. 상수를 돌려주면 그 시간에 재시도해도
        # 여전히 막혀서 안내가 거짓이 된다.
        self.retry_after_seconds = retry_after_seconds


@dataclass
class Session:
    """한 MCP 연결의 수명 동안 유지되는 상태."""

    principal_sub: str
    subject_token: str
    session_id: str = field(default_factory=lambda: f"mcp-{uuid.uuid4()}")
    call_budget: int = SESSION_CALL_BUDGET
    window_seconds: int = BUDGET_WINDOW_SECONDS
    calls_made: int = 0
    window_started: float = field(default_factory=time.time)

    def charge(self, *, now: float | None = None) -> None:
        """창이 지나면 초기화한다.

        창이 없으면 retry_after 를 돌려주고도 한 시간 뒤에 여전히 막는다.
        거짓 안내를 하느니 창을 갖는 편이 낫다.
        """
        now = time.time() if now is None else now
        if now - self.window_started >= self.window_seconds:
            self.window_started = now
            self.calls_made = 0
        if self.calls_made >= self.call_budget:
            remaining = max(1, int(self.window_started + self.window_seconds - now))
            raise BudgetExceeded(
                f"session budget {self.call_budget}/{self.window_seconds}s exhausted", remaining
            )
        self.calls_made += 1

    @property
    def remaining(self) -> int:
        return max(0, self.call_budget - self.calls_made)


class AuditLog:
    """구조화 감사 로그. 기본은 stderr — stdout 은 MCP 프로토콜이 쓴다."""

    MAX_RETAINED = 500

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stderr
        # 스트림이 원본이고 이 목록은 조회용 사본이다. 상한이 없으면 긴 세션에서
        # 메모리가 무한히 는다. 오래된 것부터 버린다 — 스트림에는 남아 있다.
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
        if len(self.records) > self.MAX_RETAINED:
            del self.records[: len(self.records) - self.MAX_RETAINED]
        print(json.dumps(entry, ensure_ascii=False), file=self._stream, flush=True)
        return entry


def new_correlation_id() -> str:
    return f"cid-{uuid.uuid4()}"
