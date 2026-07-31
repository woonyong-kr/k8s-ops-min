"""의존성 기동 대기 — nats·postgres 등이 뜰 때까지 재시도하는 공용 헬퍼."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from packages.config.errors import fail
from packages.config.logs import CONTEXT_KEY, get_logger
from packages.config.settings import env

# 기동 대기 튜닝값 — env 미설정 시 기존 기본값과 동일한 기본값이 적용됨(배포 호환)
DEPENDENCY_RETRY_LIMIT_ENV = "DEPENDENCY_RETRY_LIMIT"  # 의존성 대기 재시도 횟수(기본 60)
DEPENDENCY_RETRY_LIMIT = int(env(DEPENDENCY_RETRY_LIMIT_ENV, "60"))
DEPENDENCY_RETRY_DELAY_SECONDS_ENV = (
    "DEPENDENCY_RETRY_DELAY_SECONDS"  # 의존성 대기 재시도 간격 초(기본 2)
)
DEPENDENCY_RETRY_DELAY_SECONDS = int(env(DEPENDENCY_RETRY_DELAY_SECONDS_ENV, "2"))

LOGGER = get_logger(__name__)


async def retry_dependency(
    attempt: Callable[[], Awaitable[None]],
    *,
    label: str,
    limit: int = DEPENDENCY_RETRY_LIMIT,
    delay: int = DEPENDENCY_RETRY_DELAY_SECONDS,
) -> None:
    """attempt 가 성공할 때까지 limit 회 재시도(간격 delay). 끝내 실패하면 fail 로 종료."""
    for i in range(limit):
        try:
            await attempt()
            return
        except Exception as exc:
            LOGGER.warning(
                "dependency_waiting",
                extra={
                    CONTEXT_KEY: {
                        "dependency": label,
                        "attempt": i + 1,
                        "limit": limit,
                        "exception_type": type(exc).__name__,
                    }
                },
            )
            await asyncio.sleep(delay)
    fail(f"{label} 연결 실패")
