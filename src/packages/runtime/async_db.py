"""sync DB 를 async 로 감싸 호출 방식을 통일.

일반 sync 메서드는 스레드풀(asyncio.to_thread)로 실행함. 다만 worker UoW 안에서는
ContextVar 의 active SQLAlchemy connection 을 재사용하므로 같은 스레드에서 실행함.
워커는 항상 `await ctx.db.x(...)` 한 가지로 호출함.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from packages.storage.engine import has_active_connection


async def run_sync_with_uow_affinity[T](
    function: Callable[..., T],
    *args: Any,
    thread_runner: Callable[..., Awaitable[T]] | None = None,
    **kwargs: Any,
) -> T:
    """Run sync DB work without moving an active SQLAlchemy connection across threads."""
    if has_active_connection():
        return function(*args, **kwargs)
    runner = thread_runner or asyncio.to_thread
    return await runner(function, *args, **kwargs)


class AsyncDb:
    def __init__(self, db: Any) -> None:
        self._db = db

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._db, name)
        if not callable(attr) or inspect.iscoroutinefunction(attr):
            return attr

        async def call(*args: Any, **kwargs: Any) -> Any:
            return await run_sync_with_uow_affinity(attr, *args, **kwargs)

        return call
