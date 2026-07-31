"""OutboxRelay — outbox 에 적재된 이벤트를 NATS 로 발행(소비 루프와 별도).

저장된 봉투를 같은 event_id 로 발행 → relay 재시도 시 downstream 이 dedup.
source 필터가 있으면 해당 서비스 row 만, 없으면 모든 source row 를 lease 기반으로 relay 함.
"""

from __future__ import annotations

import asyncio
import logging

from packages.config.settings import env
from packages.contracts.event_bus.interfaces import EnvelopePublisher
from packages.contracts.interfaces import OutboxReader
from packages.events.bus import event_context

LOGGER = logging.getLogger(__name__)

# relay 튜닝값 — 대형 evidence backlog 처리 시 DB lock/메모리 피크를 낮추기 위해 작게 잡는다.
DEFAULT_BATCH_ENV = "OUTBOX_RELAY_BATCH"  # 한 번에 발행할 outbox 행 수(기본 10)
DEFAULT_BATCH = int(env(DEFAULT_BATCH_ENV, "10"))
DEFAULT_PUBLISH_TIMEOUT_SECONDS_ENV = (
    "OUTBOX_PUBLISH_TIMEOUT_SECONDS"  # 건당 발행 대기 한도 초(기본 10)
)
DEFAULT_PUBLISH_TIMEOUT_SECONDS = int(env(DEFAULT_PUBLISH_TIMEOUT_SECONDS_ENV, "10"))
NON_RETRYABLE_PUBLISH_ERRORS = frozenset({"MaxPayloadError"})


class OutboxRelay:
    def __init__(
        self,
        store: OutboxReader,
        publisher: EnvelopePublisher,
        source: str | None,
        batch: int = DEFAULT_BATCH,
        publish_timeout_seconds: int = DEFAULT_PUBLISH_TIMEOUT_SECONDS,
    ) -> None:
        self.store = store
        self.publisher = publisher
        self.source = source
        self.consumer_name = f"outbox-relay:{source or 'all'}"
        self.batch = batch
        self.publish_timeout_seconds = publish_timeout_seconds

    async def run_once(self) -> int:
        """미발행 outbox 를 한 배치 발행하고 '발행된 것만' sent 표시. 발행 건수 반환.

        발행 도중 실패해도 finally 로 '이미 발행된 것'만 표시 → 다음 루프가 전체 배치를
        재발행하지 않음(중복 최소화). 미발행 행은 표시 안 돼 다음에 재시도됨.
        """
        rows = await self.store.unsent_events(self.batch, self.source)
        published: list[str] = []
        try:
            for evt in rows:
                try:
                    await asyncio.wait_for(
                        self.publisher.publish_envelope(evt),
                        timeout=self.publish_timeout_seconds,
                    )
                except Exception as exc:
                    if self._is_non_retryable_publish_error(exc):
                        await self.store.mark_events_dead_lettered(
                            [evt],
                            self.consumer_name,
                            str(exc),
                        )
                        LOGGER.error(
                            "outbox_event_dead_lettered",
                            extra={
                                "context": {
                                    **event_context(evt),
                                    "event_id": evt.event_id,
                                    "subject": evt.subject,
                                    "source": evt.source,
                                    "error_type": exc.__class__.__name__,
                                }
                            },
                        )
                        continue
                    raise
                published.append(evt.event_id)
        finally:
            if published:
                await self.store.mark_events_sent(published)
        return len(published)

    def _is_non_retryable_publish_error(self, exc: Exception) -> bool:
        """브로커 정책상 같은 payload로 재시도해도 성공할 수 없는 오류인지 판정한다."""
        return exc.__class__.__name__ in NON_RETRYABLE_PUBLISH_ERRORS
