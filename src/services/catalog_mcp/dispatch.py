"""도구 호출 한 번의 전체 경로.

검증 → 예산 → 토큰 교환·호출 → 응답 경계 → 감사 로그. 이 순서가 중요하다.

예산을 검증보다 먼저 깎으면 잘못된 인자를 던지는 것만으로 남의 예산을 태울 수
있다. 감사 로그를 성공 경로에만 남기면 거부당한 시도가 기록되지 않는데,
사고 조사에서 정작 보고 싶은 것이 그 시도다. 그래서 성공·실패 모두 남긴다.

오류는 코드와 correlation_id 만 올린다. 예외 메시지를 그대로 올리면 접속 문자열,
내부 경로, 드라이버 버전이 모델을 거쳐 사용자 화면까지 간다.
"""

from __future__ import annotations

from typing import Any

from .client import CatalogApiClient, UpstreamError
from .server import ToolError, bound_response, validate_arguments
from .session import AuditLog, BudgetExceeded, Session, new_correlation_id


class ToolCallFailed(Exception):
    """모델에게 돌려줄 안전한 실패 표현."""

    def __init__(self, code: str, correlation_id: str, *, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.correlation_id = correlation_id
        self.retry_after = retry_after

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"error": self.code, "correlation_id": self.correlation_id}
        if self.retry_after is not None:
            payload["retry_after_seconds"] = self.retry_after
        return payload


def _items_from(body: dict[str, Any]) -> list[dict[str, Any]]:
    data = body.get("data")
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return []


def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    session: Session,
    client: CatalogApiClient,
    audit: AuditLog,
) -> dict[str, Any]:
    cid = new_correlation_id()

    def fail(code: str, *, retry_after: int | None = None) -> ToolCallFailed:
        audit.record(
            session=session, tool=tool_name, outcome=code, correlation_id=cid
        )
        return ToolCallFailed(code, cid, retry_after=retry_after)

    try:
        validated = validate_arguments(tool_name, arguments)
    except ToolError as exc:
        raise fail(exc.code) from None

    try:
        session.charge()
    except BudgetExceeded:
        raise fail(
            "session_budget_exhausted", retry_after=BudgetExceeded.retry_after_seconds
        ) from None

    try:
        body = client.call(tool_name, validated, session=session)
    except UpstreamError as exc:
        raise fail(exc.code.split(":")[0]) from None

    payload = bound_response(_items_from(body))
    if isinstance(body.get("evidence"), dict):
        payload["evidence"] = body["evidence"]

    audit.record(
        session=session,
        tool=tool_name,
        outcome="ok",
        correlation_id=cid,
        detail={
            "returned_count": payload["returned_count"],
            "truncated": payload["truncated"],
        },
    )
    payload["correlation_id"] = cid
    return payload
