"""플랫폼(런타임) 소유 이벤트 body.

dead_letter.created 는 도메인이 아니라 런타임(DeadLetterSink)이 발행하므로
domains/*/events.py 대신 계약 계층에 둠.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from packages.contracts.event_bus.bodies.base import EventBody, JsonObject
from packages.contracts.event_bus.registry import event
from packages.contracts.event_bus.subjects import EventSubject


@event(EventSubject.DEAD_LETTER_CREATED)
@dataclass(frozen=True)
class DeadLetterCreatedBody(EventBody):
    """dead_letter.created — DLQ 적재 사실 알림(발행자: DeadLetterSink)."""

    dead_letter_id: int
    original_event_id: str
    original_subject: str
    consumer: str
    attempts: int
    error: str
    created_at: str
    status: str
    correlation_id: str
    # 디코드 실패(raw) 경로에서만 원문이 실림.
    payload: JsonObject | None = None


@event(EventSubject.PIPELINE_CONTRACT_FAILED)
@dataclass(frozen=True)
class PipelineContractFailedBody(EventBody):
    """pipeline.contract_failed — consumer 가 계약 위반 이벤트를 거부"""

    contract: str
    reason: str
    consumer: str
    payload: JsonObject
    workspace_id: str
    evidence_ref: str | None = None
    severity: str = "warning"
    diagnostics: JsonObject = field(default_factory=dict)
