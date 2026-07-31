from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from packages.config.constants import Nats, Runtime
from packages.config.logs import get_logger
from packages.config.retry import retry_dependency
from packages.config.settings import env
from packages.contracts.event_bus.bodies.platform import DeadLetterCreatedBody
from packages.contracts.event_bus.interfaces import (
    EventBus,
    EventClient,
    EventConsumerMetrics,
    EventEnvelope,
    EventPublisher,
    EventRecorder,
    EventSubscription,
    JsonObject,
)
from packages.contracts.event_bus.subjects import (
    STREAM_DUPLICATE_WINDOW_SECONDS,
    STREAM_MAX_AGE_SECONDS,
    STREAM_MAX_BYTES,
    STREAM_NAME,
    STREAM_SUBJECTS,
)
from packages.contracts.interfaces import DeadLetterStore
from packages.events.context import (
    event_workspace,
    pending_event_workspace,
    stage_event_workspace,
)
from packages.events.envelope import event

NATS_URL_ENV = "NATS_URL"
NATS_MSG_ID_HEADER = "Nats-Msg-Id"
# JetStream 컨슈머 재배달 정책(모두 env 오버라이드 가능).
# ack_wait 는 워커 핸들러 타임아웃(runtime/worker.py, 기본 30s)보다 커야
# 처리 중 재배달로 인한 동시 중복 처리가 방지됨.
ACK_WAIT_SECONDS_ENV = "NATS_ACK_WAIT_SECONDS"
DEFAULT_ACK_WAIT_SECONDS = "60"  # 핸들러 타임아웃(30s)의 2배 — 처리 중 재배달 금지 창
MAX_DELIVER_ENV = "NATS_MAX_DELIVER"
DEFAULT_MAX_DELIVER = "-1"  # 재시도/DLQ 상한은 application ledger가 소유
MAX_ACK_PENDING_ENV = "NATS_MAX_ACK_PENDING"
DEFAULT_MAX_ACK_PENDING = "100"  # 컨슈머당 미확인 in-flight 상한(폭주 억제)
DELIVER_POLICY_ENV = "NATS_DELIVER_POLICY"
DEFAULT_DELIVER_POLICY = "all"
LOGGER = get_logger(__name__)
CURRENT_CAUSATION_ID: ContextVar[str | None] = ContextVar(
    "current_event_causation_id", default=None
)


def nats_client() -> Any:
    import nats

    return nats


def nats_not_found_error() -> type[Exception]:
    from nats.js.errors import NotFoundError

    return NotFoundError


def ack_wait_seconds() -> int:
    """JetStream 재배달 대기 창(초) — consumer_config 와 부팅 타이밍 검증이 같은 값을 읽는다."""
    return int(env(ACK_WAIT_SECONDS_ENV, DEFAULT_ACK_WAIT_SECONDS))


def consumer_config() -> Any:
    """pull 컨슈머 재배달 정책 — 재배달 창(ack_wait)·상한(max_deliver)·in-flight 한도 고정."""
    from nats.js.api import ConsumerConfig, DeliverPolicy

    deliver_policy_value = env(DELIVER_POLICY_ENV, DEFAULT_DELIVER_POLICY).strip().lower()
    try:
        deliver_policy = DeliverPolicy(deliver_policy_value)
    except ValueError as exc:
        raise ValueError(f"unsupported NATS deliver policy: {deliver_policy_value}") from exc

    return ConsumerConfig(
        ack_wait=ack_wait_seconds(),
        max_deliver=int(env(MAX_DELIVER_ENV, DEFAULT_MAX_DELIVER)),
        max_ack_pending=int(env(MAX_ACK_PENDING_ENV, DEFAULT_MAX_ACK_PENDING)),
        deliver_policy=deliver_policy,
    )


def event_context(evt: EventEnvelope) -> dict[str, str | None]:
    """로그 식별 필드와 현재 처리 중인 신뢰 workspace 컨텍스트를 결합."""
    stage_event_workspace(getattr(evt, "workspace_id", None))
    return {
        "subject": evt.subject,
        "event_id": evt.event_id,
        "correlation_id": evt.correlation_id,
        "causation_id": evt.causation_id,
        "workspace_id": getattr(evt, "workspace_id", None),
        "source": evt.source,
    }


@contextmanager
def event_causation(causation_id: str) -> Iterator[None]:
    token = CURRENT_CAUSATION_ID.set(causation_id)
    workspace_id = pending_event_workspace()
    stage_event_workspace(None)
    try:
        with event_workspace(workspace_id):
            yield
    finally:
        CURRENT_CAUSATION_ID.reset(token)


class NatsEventBus(EventBus):
    def __init__(self) -> None:
        self.url = env(NATS_URL_ENV, Nats.DEFAULT_URL)
        self.nc: Any | None = None
        self.js: Any | None = None

    async def connect(self) -> None:
        nats = nats_client()

        async def attempt() -> None:
            connection = await nats.connect(
                self.url, name=env(Runtime.SERVICE_NAME_ENV, Runtime.DEFAULT_SERVICE_NAME)
            )
            self.nc = connection
            self.js = connection.jetstream()
            await self.ensure_stream()

        await retry_dependency(attempt, label="nats")

    async def ensure_stream(self) -> None:
        assert self.js is not None
        not_found = nats_not_found_error()
        try:
            info = await self.js.stream_info(STREAM_NAME)
            subjects = sorted(set(info.config.subjects or []) | set(STREAM_SUBJECTS))
            await self.js.update_stream(
                name=STREAM_NAME,
                subjects=subjects,
                storage="file",
                max_age=STREAM_MAX_AGE_SECONDS,
                max_bytes=STREAM_MAX_BYTES,
                duplicate_window=STREAM_DUPLICATE_WINDOW_SECONDS,
            )
        except not_found:
            await self.js.add_stream(
                name=STREAM_NAME,
                subjects=STREAM_SUBJECTS,
                storage="file",
                max_age=STREAM_MAX_AGE_SECONDS,
                max_bytes=STREAM_MAX_BYTES,
                duplicate_window=STREAM_DUPLICATE_WINDOW_SECONDS,
            )

    async def emit(
        self,
        subject: str,
        source: str,
        payload: JsonObject,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> EventEnvelope:
        assert self.js is not None
        evt = event(subject, source, payload, correlation_id, causation_id)
        await self.js.publish(
            subject,
            json.dumps(evt.to_dict()).encode(),
            headers={NATS_MSG_ID_HEADER: evt.event_id},
        )
        LOGGER.info("emitted", extra={"context": event_context(evt)})
        return evt

    async def publish_envelope(self, evt: EventEnvelope) -> EventEnvelope:
        assert self.js is not None
        await self.js.publish(
            evt.subject,
            json.dumps(evt.to_dict()).encode(),
            headers={NATS_MSG_ID_HEADER: evt.event_id},
        )
        LOGGER.info("relayed", extra={"context": event_context(evt)})
        return evt

    async def subscribe(self, subject: str, durable: str) -> EventSubscription:
        assert self.js is not None
        return await self.js.pull_subscribe(
            subject, durable=durable, stream=STREAM_NAME, config=consumer_config()
        )

    async def consumer_metrics(self, subject: str, durable: str) -> EventConsumerMetrics:
        assert self.js is not None
        info = await self.js.consumer_info(STREAM_NAME, durable)
        return EventConsumerMetrics(
            stream=STREAM_NAME,
            subject=subject,
            durable=durable,
            pending=int(getattr(info, "num_pending", 0) or 0),
            ack_pending=int(getattr(info, "num_ack_pending", 0) or 0),
            redelivered=int(getattr(info, "num_redelivered", 0) or 0),
        )

    async def close(self) -> None:
        if self.nc:
            await self.nc.drain()


class RecordedEventClient:
    def __init__(self, publisher: EventPublisher, recorder: EventRecorder) -> None:
        self.publisher = publisher
        self.recorder = recorder

    async def emit(
        self,
        subject: str,
        source: str,
        payload: JsonObject,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> EventEnvelope:
        evt = await self.publisher.emit(
            subject, source, payload, correlation_id, causation_id or CURRENT_CAUSATION_ID.get()
        )
        self.recorder.record_event(evt)
        return evt


class DeadLetterSink:
    def __init__(self, events: EventClient, store: DeadLetterStore, source: str) -> None:
        self.events = events
        self.store = store
        self.source = source

    async def capture(
        self, evt: EventEnvelope, consumer: str, error: Exception, attempts: int
    ) -> EventEnvelope:
        dead_letter = self.store.record_dead_letter(evt, consumer, str(error), attempts)
        body = DeadLetterCreatedBody.from_body(dead_letter)
        with event_workspace(evt.workspace_id):
            return await self.events.emit(
                body.__subject__,
                self.source,
                body.to_body(),
                evt.correlation_id,
                evt.event_id,
            )

    async def capture_raw(self, raw: bytes, consumer: str, error: Exception) -> EventEnvelope:
        dead_letter = self.store.record_raw_dead_letter(raw, consumer, str(error))
        body = DeadLetterCreatedBody.from_body(dead_letter)
        return await self.events.emit(
            body.__subject__,
            self.source,
            body.to_body(),
            dead_letter["correlation_id"],
            dead_letter["original_event_id"],
        )
