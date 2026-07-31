"""롱폴 웨이크업 — Postgres LISTEN/NOTIFY 로 명령 큐잉 순간 lease 루프를 깨운다.

동기: agent 롱폴이 1초 주기 DB 폴링이라 (a) 명령 전달 지연 평균 0.5초,
(b) 에이전트 수천 대 규모에서 유휴 폴링 쿼리가 DB 부하의 대부분이 된다.
NOTIFY 를 받으면 대기 중인 lease 루프만 즉시 깨어나 재시도한다.

pgbouncer(transaction pooling) 경유 커넥션으로는 LISTEN 이 동작하지 않으므로
알림 전용 직결 URL(COMMAND_NOTIFY_DATABASE_URL)을 따로 받는다. 미설정이거나
리스너가 죽어 있으면 wait() 는 타임아웃까지 잠들 뿐 — 기존 주기 폴링과 완전히
동일하게 동작한다(fail-open 폴백, 알림은 최적화일 뿐 정확성 요건이 아님).
"""

from __future__ import annotations

import asyncio
import contextlib

import psycopg

from packages.config.logs import CONTEXT_KEY, get_logger

LOGGER = get_logger(__name__)

# 큐잉 측(repository)과 리스너가 공유하는 채널 이름.
AGENT_COMMAND_CHANNEL = "agent_command_queued"
COMMAND_NOTIFY_DATABASE_URL_ENV = "COMMAND_NOTIFY_DATABASE_URL"
RECONNECT_DELAY_SECONDS = 5


def wakeup_key(workspace_id: str, cluster_id: str) -> str:
    return f"{workspace_id}/{cluster_id}"


class CommandWakeup:
    """(workspace, cluster) 단위 대기자 레지스트리 + LISTEN 리스너."""

    def __init__(self) -> None:
        self._waiters: dict[str, set[asyncio.Event]] = {}
        self._task: asyncio.Task | None = None

    async def wait(self, workspace_id: str, cluster_id: str, timeout: float) -> None:
        """알림이 오거나 timeout 이 지날 때까지 대기.

        리스너가 없으면 단순 sleep(timeout) 과 동일 — 호출부는 구분할 필요 없음.
        """
        event = asyncio.Event()
        key = wakeup_key(workspace_id, cluster_id)
        self._waiters.setdefault(key, set()).add(event)
        try:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(event.wait(), timeout)
        finally:
            bucket = self._waiters.get(key)
            if bucket is not None:
                bucket.discard(event)
                if not bucket:
                    self._waiters.pop(key, None)

    def notify_local(self, payload: str) -> int:
        """payload("workspace/cluster")에 해당하는 대기자 전원을 깨움. 깨운 수 반환."""
        events = self._waiters.get(payload, set())
        for event in events:
            event.set()
        return len(events)

    @property
    def listening(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, notify_url: str) -> None:
        if self.listening:
            return
        self._task = asyncio.create_task(self._listen_loop(notify_url))

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _listen_loop(self, notify_url: str) -> None:
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    notify_url, autocommit=True
                ) as conn:
                    await conn.execute(f"LISTEN {AGENT_COMMAND_CHANNEL}")
                    LOGGER.info("command_wakeup_listening")
                    async for notification in conn.notifies():
                        self.notify_local(str(notification.payload))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # 리스너 장애는 폴백(주기 폴링)으로 흡수 — 경고 후 재접속만 시도.
                LOGGER.warning(
                    "command_wakeup_listener_failed",
                    extra={CONTEXT_KEY: {"exception_type": type(exc).__name__}},
                )
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)


# 게이트웨이 프로세스 전역 단일 인스턴스 — lifespan 이 start/stop 을 소유한다.
WAKEUP = CommandWakeup()
