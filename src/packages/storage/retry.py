from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.exc import OperationalError

RETRYABLE_DB_SQLSTATES = {"40P01", "40001", "55P03"}
RETRYABLE_DB_ERROR_MARKERS = (
    "deadlock detected",
    "could not serialize access",
    "lock_not_available",
    "lock timeout",
    "canceling statement due to lock timeout",
)


def retryable_db_conflict(exc: BaseException) -> bool:
    if not isinstance(exc, OperationalError):
        return False
    original = getattr(exc, "orig", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if sqlstate in RETRYABLE_DB_SQLSTATES:
        return True
    text = str(exc).lower()
    return any(marker in text for marker in RETRYABLE_DB_ERROR_MARKERS)


def retry_delay_seconds(attempt: int, base_delay: float) -> float:
    return base_delay * (attempt + 1)


async def async_retry_db_conflict[T](
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.05,
) -> T:
    for attempt in range(attempts):
        try:
            return await operation()
        except OperationalError as exc:
            if not retryable_db_conflict(exc) or attempt == attempts - 1:
                raise
            await asyncio.sleep(retry_delay_seconds(attempt, base_delay))
    raise RuntimeError("unreachable db retry state")


def sync_retry_db_conflict[T](
    operation: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 0.05,
) -> T:
    for attempt in range(attempts):
        try:
            return operation()
        except OperationalError as exc:
            if not retryable_db_conflict(exc) or attempt == attempts - 1:
                raise
            time.sleep(retry_delay_seconds(attempt, base_delay))
    raise RuntimeError("unreachable db retry state")


async def to_thread_db_retry[T](
    func: Callable[..., T],
    *args: Any,
    attempts: int = 3,
    base_delay: float = 0.05,
    **kwargs: Any,
) -> T:
    return await async_retry_db_conflict(
        lambda: asyncio.to_thread(func, *args, **kwargs),
        attempts=attempts,
        base_delay=base_delay,
    )
