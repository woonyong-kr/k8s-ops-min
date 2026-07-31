from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from packages.config.logs import CONTEXT_KEY, get_logger
from packages.config.settings import env
from packages.contracts.event_bus.interfaces import (
    EventConsumerLagSnapshot,
    EventConsumerMetrics,
    EventEnvelope,
    JsonObject,
)
from packages.contracts.event_bus.processing import (
    CLAIM_BLOCKED,
    TERMINAL_STATUSES,
    EventProcessingStatus,
)
from packages.contracts.interfaces import EventProcessingRecord
from packages.storage.engine import (
    DatabaseConnection,
    compact_error,
    row_dict,
)
from packages.storage.schema import (
    EventConsumerMetric,
    EventModel,
    EventProcessing,
)

# PROCESSING claim 신선도 창 — 이 시간 안의 PROCESSING 은 다른 소비자 인스턴스가
# 실제 처리 중인 것으로 간주해 재클레임 거절(JetStream 재배달과의 동시 중복 처리 방지).
# ack_wait 뒤 재배달이 와도 원 claim 이 이 창을 넘길 때까지는 획득 불가함.
# 반드시 handler_timeout < ack_wait < 이 값 순서를 지켜야 하며, 워커 기동 시
# validate_event_timing_contract(runtime/worker.py)가 이 관계를 강제한다.
PROCESSING_STALE_SECONDS_ENV = "EVENT_PROCESSING_STALE_SECONDS"
PROCESSING_STALE_SECONDS = int(env(PROCESSING_STALE_SECONDS_ENV, "90"))
EVENT_CONSUMER_METRICS_UPSERT_CHUNK = 1_000
LOGGER = get_logger(__name__)


def event_log_context(evt: EventEnvelope) -> JsonObject:
    return {
        "event_id": evt.event_id,
        "subject": evt.subject,
        "source": evt.source,
        "correlation_id": evt.correlation_id,
        "causation_id": evt.causation_id,
    }


class EventRepository(DatabaseConnection):
    def record_event(self, evt: EventEnvelope) -> None:
        table = EventModel.__table__
        statement = (
            pg_insert(table)
            .values(
                event_id=evt.event_id,
                subject=evt.subject,
                source=evt.source,
                correlation_id=evt.correlation_id,
                causation_id=evt.causation_id,
                payload=evt.payload,
                schema_version=evt.schema_version,
            )
            .on_conflict_do_nothing(index_elements=[table.c.event_id])
        )
        with self.connection() as conn:
            conn.execute(statement)
        LOGGER.info("db_event_recorded", extra={CONTEXT_KEY: event_log_context(evt)})

    def begin_event_processing(self, evt: EventEnvelope, consumer: str) -> EventProcessingRecord:
        table = EventProcessing.__table__
        with self.connection() as conn:
            row = self.claim_event_processing(conn, table, evt, consumer)
            if row:
                LOGGER.info(
                    "db_event_processing_claimed",
                    extra={
                        CONTEXT_KEY: {
                            **event_log_context(evt),
                            "consumer": consumer,
                            "status": row["status"],
                            "attempts": row["attempts"],
                        }
                    },
                )
                return EventProcessingRecord(status=row["status"], attempts=row["attempts"])

            existing = self.get_event_processing(conn, table, evt, consumer)
            if existing:
                status = str(existing["status"])
                if status == EventProcessingStatus.PROCESSING:
                    # 신선한 PROCESSING 이 claim 을 거절 = 다른 인스턴스가 처리 중
                    # → 종결도 획득도 아닌 미획득 신호로 치환(워커가 nak 로 미룸)
                    status = CLAIM_BLOCKED
                return EventProcessingRecord(status=status, attempts=existing["attempts"])
            return EventProcessingRecord(status="unknown", attempts=0)

    def claim_event_processing(
        self, conn: Connection, table: Any, evt: EventEnvelope, consumer: str
    ) -> JsonObject | None:
        """단일 원자 UPSERT 로 처리권 claim + attempt 누적.

        재클레임 허용 조건: 종결(PROCESSED/DEAD_LETTERED) 아님 그리고
        (PROCESSING 아님 또는 updated_at 이 신선도 창을 넘김 = 죽은 claim).
        신선한 PROCESSING 이면 행을 반환하지 않음(동시 중복 처리 차단).
        """
        insert = pg_insert(table).values(
            event_id=evt.event_id,
            consumer=consumer,
            subject=evt.subject,
            correlation_id=evt.correlation_id,
            status=EventProcessingStatus.PROCESSING,
            attempts=1,
            last_error=None,
            updated_at=func.now(),
        )
        statement = insert.on_conflict_do_update(
            index_elements=[table.c.event_id, table.c.consumer],
            set_={
                "attempts": table.c.attempts + 1,
                "status": EventProcessingStatus.PROCESSING,
                "last_error": None,
                "updated_at": func.now(),
            },
            where=and_(
                table.c.status.not_in(TERMINAL_STATUSES),
                or_(
                    table.c.status != EventProcessingStatus.PROCESSING,
                    table.c.updated_at
                    < func.now() - text(f"interval '{int(PROCESSING_STALE_SECONDS)} seconds'"),
                ),
            ),
        ).returning(table.c.status, table.c.attempts)
        row = conn.execute(statement).mappings().first()
        return row_dict(row) if row else None

    def get_event_processing(
        self, conn: Connection, table: Any, evt: EventEnvelope, consumer: str
    ) -> JsonObject | None:
        statement = select(table.c.status, table.c.attempts).where(
            table.c.event_id == evt.event_id, table.c.consumer == consumer
        )
        row = conn.execute(statement).mappings().first()
        return row_dict(row) if row else None

    def finish_event_processing(
        self, evt: EventEnvelope, consumer: str, duration_ms: int | None = None
    ) -> None:
        table = EventProcessing.__table__
        values: JsonObject = {
            "status": EventProcessingStatus.PROCESSED,
            "last_error": None,
            "updated_at": func.now(),
        }
        if duration_ms is not None:
            values["processing_duration_ms"] = max(0, int(duration_ms))
        statement = (
            update(table)
            .where(table.c.event_id == evt.event_id, table.c.consumer == consumer)
            .values(**values)
        )
        with self.connection() as conn:
            conn.execute(statement)
        LOGGER.info("db_event_recorded", extra={CONTEXT_KEY: event_log_context(evt)})
        LOGGER.info(
            "db_event_processing_finished",
            extra={
                CONTEXT_KEY: {
                    **event_log_context(evt),
                    "consumer": consumer,
                    "status": EventProcessingStatus.PROCESSED,
                    "processing_duration_ms": duration_ms,
                }
            },
        )

    def fail_event_processing(
        self,
        evt: EventEnvelope,
        consumer: str,
        error: str,
        status: str,
        duration_ms: int | None = None,
    ) -> None:
        table = EventProcessing.__table__
        values: JsonObject = {
            "status": status,
            "last_error": compact_error(error),
            "updated_at": func.now(),
        }
        if duration_ms is not None:
            values["processing_duration_ms"] = max(0, int(duration_ms))
        statement = (
            update(table)
            .where(table.c.event_id == evt.event_id, table.c.consumer == consumer)
            .values(**values)
        )
        with self.connection() as conn:
            conn.execute(statement)
        LOGGER.info(
            "db_event_processing_failed",
            extra={
                CONTEXT_KEY: {
                    **event_log_context(evt),
                    "consumer": consumer,
                    "status": status,
                    "processing_duration_ms": duration_ms,
                }
            },
        )

    def event_processing_status_counts(self) -> dict[str, int]:
        table = EventProcessing.__table__
        statement = select(table.c.status, func.count().label("count")).group_by(table.c.status)
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return {row["status"]: int(row["count"]) for row in rows}

    def event_processing_duration_avg_ms_by_consumer(self) -> dict[str, float]:
        table = EventProcessing.__table__
        statement = (
            select(table.c.consumer, func.avg(table.c.processing_duration_ms).label("duration"))
            .where(table.c.processing_duration_ms.is_not(None))
            .group_by(table.c.consumer)
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return {str(row["consumer"]): float(row["duration"] or 0) for row in rows}

    def event_processing_duration_max_ms_by_consumer(self) -> dict[str, int]:
        table = EventProcessing.__table__
        statement = (
            select(table.c.consumer, func.max(table.c.processing_duration_ms).label("duration"))
            .where(table.c.processing_duration_ms.is_not(None))
            .group_by(table.c.consumer)
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return {str(row["consumer"]): int(row["duration"] or 0) for row in rows}

    def record_event_consumer_metrics(self, sample: EventConsumerMetrics) -> None:
        self.record_event_consumer_metrics_batch((sample,))

    def record_event_consumer_metrics_batch(
        self,
        samples: Sequence[EventConsumerMetrics],
    ) -> None:
        """Upsert a metrics observation batch in one transaction.

        One current row is retained per consumer/subject identity. Duplicate identities in
        an input batch are collapsed to the last observation before issuing PostgreSQL's
        multi-row upsert, avoiding a cardinality violation inside one statement.
        """
        latest_by_identity = {(sample.durable, sample.subject): sample for sample in samples}
        if not latest_by_identity:
            return

        table = EventConsumerMetric.__table__
        values = [
            {
                "consumer": sample.durable,
                "subject": sample.subject,
                "stream": sample.stream,
                "pending_events": max(0, int(sample.pending)),
                "ack_pending_events": max(0, int(sample.ack_pending)),
                "redelivered_events": max(0, int(sample.redelivered)),
                "observed_at": func.now(),
            }
            for _identity, sample in sorted(latest_by_identity.items())
        ]
        with self.connection() as conn:
            for offset in range(0, len(values), EVENT_CONSUMER_METRICS_UPSERT_CHUNK):
                insert = pg_insert(table).values(
                    values[offset : offset + EVENT_CONSUMER_METRICS_UPSERT_CHUNK]
                )
                conn.execute(
                    insert.on_conflict_do_update(
                        index_elements=[table.c.consumer, table.c.subject],
                        set_={
                            "stream": insert.excluded.stream,
                            "pending_events": insert.excluded.pending_events,
                            "ack_pending_events": insert.excluded.ack_pending_events,
                            "redelivered_events": insert.excluded.redelivered_events,
                            "observed_at": func.now(),
                        },
                    )
                )

    def event_consumer_pending_by_consumer_subject(self) -> dict[tuple[str, str], int]:
        return self._event_consumer_metric_values("pending_events")

    def event_consumer_ack_pending_by_consumer_subject(self) -> dict[tuple[str, str], int]:
        return self._event_consumer_metric_values("ack_pending_events")

    def event_consumer_redelivered_by_consumer_subject(self) -> dict[tuple[str, str], int]:
        return self._event_consumer_metric_values("redelivered_events")

    def event_consumer_lag_snapshot(self, *, limit: int) -> EventConsumerLagSnapshot:
        """Read actual backlog rows without confusing table cardinality with lag.

        Counts and the bounded backlog-first sample are read inside the same transaction.
        Idle consumer/subject metrics remain represented by ``metric_count`` but do not
        consume the lag sample budget or degrade diagnostics completeness.
        """
        if limit < 1:
            raise ValueError("consumer lag snapshot limit must be positive")

        table = EventConsumerMetric.__table__
        lagging = or_(
            table.c.pending_events > 0,
            table.c.ack_pending_events > 0,
            table.c.redelivered_events > 0,
        )
        counts = select(
            func.count().label("metric_count"),
            func.count().filter(lagging).label("lagging_count"),
        )
        samples = (
            select(
                table.c.consumer,
                table.c.subject,
                table.c.stream,
                table.c.pending_events,
                table.c.ack_pending_events,
                table.c.redelivered_events,
            )
            .where(lagging)
            .order_by(
                table.c.pending_events.desc(),
                table.c.ack_pending_events.desc(),
                table.c.redelivered_events.desc(),
                table.c.consumer,
                table.c.subject,
            )
            .limit(limit)
        )
        with self.connection() as conn:
            count_row = conn.execute(counts).mappings().one()
            rows = conn.execute(samples).mappings().all()

        return EventConsumerLagSnapshot(
            samples=tuple(
                EventConsumerMetrics(
                    stream=str(row["stream"]),
                    subject=str(row["subject"]),
                    durable=str(row["consumer"]),
                    pending=max(0, int(row["pending_events"] or 0)),
                    ack_pending=max(0, int(row["ack_pending_events"] or 0)),
                    redelivered=max(0, int(row["redelivered_events"] or 0)),
                )
                for row in rows
            ),
            metric_count=max(0, int(count_row["metric_count"] or 0)),
            lagging_count=max(0, int(count_row["lagging_count"] or 0)),
        )

    def _event_consumer_metric_values(self, column_name: str) -> dict[tuple[str, str], int]:
        table = EventConsumerMetric.__table__
        column = getattr(table.c, column_name)
        statement = select(table.c.consumer, table.c.subject, column)
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return {
            (str(row["consumer"]), str(row["subject"])): int(row[column_name] or 0) for row in rows
        }

    def delete_events_older_than(self, cutoff: datetime, *, limit: int = 1000) -> int:
        """이벤트 원장을 보존 기간 이후 배치 삭제한다."""
        table = EventModel.__table__
        expired = (
            select(table.c.event_id)
            .where(table.c.created_at < cutoff)
            .order_by(table.c.created_at, table.c.event_id)
            .limit(limit)
            .cte("expired_events")
        )
        statement = (
            table.delete()
            .where(table.c.event_id.in_(select(expired.c.event_id)))
            .returning(table.c.event_id)
        )
        with self.connection() as conn:
            return len(conn.execute(statement).all())
