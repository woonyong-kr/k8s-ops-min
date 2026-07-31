from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, cast

from packages.config.errors import require
from packages.config.logs import CONTEXT_KEY, get_logger
from packages.contracts.auth import Actor
from packages.contracts.event_bus.bodies import EventBody
from packages.contracts.event_bus.interfaces import (
    EventEnvelope,
    EventPublisher,
    EventRecorder,
    JsonObject,
)
from packages.contracts.gateway.fields import Gateway
from packages.events.bus import RecordedEventClient
from packages.events.envelope import event
from packages.runtime.async_db import run_sync_with_uow_affinity
from packages.storage.retry import to_thread_db_retry

LOGGER = get_logger(__name__)
TransactionalStage = Callable[[Any, EventEnvelope], None]


@dataclass(frozen=True)
class AcceptedEvent:
    event: EventEnvelope

    def response(self, include_event: bool = False) -> JsonObject:
        data: JsonObject = {
            Gateway.ACCEPTED: True,
            Gateway.EVENT_ID: self.event.event_id,
            Gateway.CORRELATION_ID: self.event.correlation_id,
        }
        if include_event:
            data[Gateway.EVENT] = self.event
        return data


class ApiEventGateway:
    """HTTP/API 입력을 내부 event envelope로 바꾸는 표준 입구."""

    def __init__(self, publisher: EventPublisher, recorder: EventRecorder, source: str) -> None:
        self.events = RecordedEventClient(publisher, recorder)
        self.source = source

    async def accept(
        self,
        subject: str,
        payload: JsonObject,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        actor: Actor | None = None,
        transactional_stage: TransactionalStage | None = None,
    ) -> AcceptedEvent:
        event_payload = dict(payload)
        if actor is not None:
            if not event_payload.get(Gateway.REQUESTED_BY):
                event_payload[Gateway.REQUESTED_BY] = actor.user_id
            if not event_payload.get(Gateway.ACTOR):
                event_payload[Gateway.ACTOR] = actor.to_body()
        durable = await self.accept_via_outbox_if_supported(
            subject,
            event_payload,
            correlation_id,
            causation_id,
            transactional_stage,
        )
        if durable is not None:
            self._log_accepted_event(durable.event, actor=actor, durable=True)
            return durable
        evt = await self.events.emit(
            subject, self.source, event_payload, correlation_id, causation_id
        )
        if transactional_stage is not None:
            raise RuntimeError("transactional event staging requires an outbox-capable recorder")
        self._log_accepted_event(evt, actor=actor, durable=False)
        return AcceptedEvent(evt)

    async def accept_via_outbox_if_supported(
        self,
        subject: str,
        payload: JsonObject,
        correlation_id: str | None,
        causation_id: str | None,
        transactional_stage: TransactionalStage | None,
    ) -> AcceptedEvent | None:
        recorder = self.events.recorder
        unit_of_work = getattr(recorder, "unit_of_work", None)
        stage_events = getattr(recorder, "stage_events", None)
        if not callable(unit_of_work) or not callable(stage_events):
            return None

        evt = event(subject, self.source, payload, correlation_id, causation_id)

        def stage() -> None:
            with unit_of_work() as conn:
                recorder.record_event(evt)
                stage_events(conn, [evt])
                if transactional_stage is not None:
                    transactional_stage(conn, evt)

        await run_sync_with_uow_affinity(stage, thread_runner=to_thread_db_retry)
        return AcceptedEvent(evt)

    async def accept_body(
        self,
        body: EventBody,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        actor: Actor | None = None,
        transactional_stage: TransactionalStage | None = None,
    ) -> AcceptedEvent:
        subject = getattr(body, "__subject__", None)
        require(isinstance(subject, str), f"{body.__class__.__name__} 에 subject 없음", TypeError)
        return await self.accept(
            cast(str, subject),
            _body_payload_with_actor(body, actor),
            correlation_id,
            causation_id,
            actor,
            transactional_stage,
        )

    def _log_accepted_event(
        self,
        evt: EventEnvelope,
        *,
        actor: Actor | None = None,
        durable: bool,
    ) -> None:
        context: JsonObject = {
            "subject": evt.subject,
            "source": evt.source,
            "event_id": evt.event_id,
            "correlation_id": evt.correlation_id,
            "causation_id": evt.causation_id,
            "workspace_id": evt.workspace_id,
            "durable_outbox": durable,
        }
        if actor is not None:
            context["actor_user_id"] = actor.user_id
        requested_by = evt.payload.get(Gateway.REQUESTED_BY)
        if requested_by:
            context[Gateway.REQUESTED_BY] = requested_by
        LOGGER.info("gateway_event_accepted", extra={CONTEXT_KEY: context})


def _body_payload_with_actor(body: EventBody, actor: Actor | None = None) -> JsonObject:
    payload = body.to_body()
    if actor is None:
        return payload

    allowed_keys = _event_body_payload_keys(body)
    if Gateway.REQUESTED_BY in allowed_keys and not payload.get(Gateway.REQUESTED_BY):
        payload[Gateway.REQUESTED_BY] = actor.user_id
    if Gateway.ACTOR in allowed_keys and not payload.get(Gateway.ACTOR):
        payload[Gateway.ACTOR] = actor.to_body()
    return payload


def _event_body_payload_keys(body: EventBody) -> set[str]:
    if not is_dataclass(body):
        return set(body.to_body())
    return {item.metadata.get("payload_name", item.name) for item in fields(body)}
