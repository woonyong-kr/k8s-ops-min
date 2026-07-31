"""공용 에러 가드 — require/fail 한 줄, 시스템 메시지 형식 통일."""

from __future__ import annotations

from typing import NoReturn

SYSTEM_PREFIX = "[event-system]"


def fail(message: str, error: type[Exception] = RuntimeError) -> NoReturn:
    raise error(f"{SYSTEM_PREFIX} {message}")


def require(condition: object, message: str, error: type[Exception] = ValueError) -> None:
    if not condition:
        fail(message, error)
