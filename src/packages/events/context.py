"""인증 경계에서 확정한 이벤트 workspace 컨텍스트."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

CURRENT_WORKSPACE_ID: ContextVar[str | None] = ContextVar(
    "current_event_workspace_id",
    default=None,
)
PENDING_WORKSPACE_ID: ContextVar[str | None] = ContextVar(
    "pending_event_workspace_id",
    default=None,
)


def normalized_workspace_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def set_event_workspace(workspace_id: object) -> None:
    """현재 async 요청 컨텍스트에 인증된 workspace를 고정."""
    CURRENT_WORKSPACE_ID.set(normalized_workspace_id(workspace_id))


def current_event_workspace() -> str | None:
    return normalized_workspace_id(CURRENT_WORKSPACE_ID.get())


def stage_event_workspace(workspace_id: object) -> None:
    """worker가 event_causation 진입 직전 소비할 부모 workspace를 준비."""
    PENDING_WORKSPACE_ID.set(normalized_workspace_id(workspace_id))


def pending_event_workspace() -> str | None:
    return normalized_workspace_id(PENDING_WORKSPACE_ID.get())


@contextmanager
def event_workspace(workspace_id: object) -> Iterator[None]:
    token = CURRENT_WORKSPACE_ID.set(normalized_workspace_id(workspace_id))
    try:
        yield
    finally:
        CURRENT_WORKSPACE_ID.reset(token)
