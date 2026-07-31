"""outbound 게이트웨이 정형.

외부(GitHub/HTTP/...)로 나가는 워커의 공통 모양:
    *.requested  →  외부 호출  →  *.delivered(성공) / *.failed(실패)

deliver() 가 try/except 를 한 곳에 모아, 게이트웨이 핸들러는 "무엇을 호출하고
성공/실패를 어떤 이벤트로 낼지"만 선언.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


async def deliver(
    call: Callable[[], Awaitable[Any]], ok: Callable[[Any], Any], fail: Callable[[Exception], Any]
) -> AsyncIterator[Any]:
    """외부 호출 1회 → 성공이면 ok(결과), 실패면 fail(예외) body 발행."""
    try:
        result = await call()
    except Exception as exc:  # noqa: BLE001 - 외부 호출은 무엇이든 실패 가능
        yield fail(exc)
        return
    yield ok(result)
