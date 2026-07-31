from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from packages.config.logs import CONTEXT_KEY, get_logger
from packages.contracts.event_bus.interfaces import EventEnvelope
from packages.storage.engine import (
    DEAD_LETTER_STATUS_OPEN,
    DatabaseConnection,
    compact_error,
)
from packages.storage.retry import async_retry_db_conflict
from packages.storage.schema import (
    EventDeadLetter,
    OutboxModel,
)

DEFAULT_OUTBOX_LEASE_SECONDS = 60
LOGGER = get_logger(__name__)


def event_log_context(evt: EventEnvelope) -> dict[str, object]:
    return {
        "event_id": evt.event_id,
        "subject": evt.subject,
        "source": evt.source,
        "correlation_id": evt.correlation_id,
        "causation_id": evt.causation_id,
        "workspace_id": evt.workspace_id,
    }


class OutboxRepository(DatabaseConnection):
    def stage_events(self, conn: Connection, events: list[EventEnvelope]) -> None:
        """UoW 트랜잭션 안에서 outbox 적재(같은 커넥션). [완전판 3단계]"""
        if not events:
            return
        table = OutboxModel.__table__
        values = [
            {
                "event_id": evt.event_id,
                "subject": evt.subject,
                "source": evt.source,
                "correlation_id": evt.correlation_id,
                "causation_id": evt.causation_id,
                "workspace_id": evt.workspace_id,
                "occurred_at": evt.created_at,
                "payload": evt.payload,
                "schema_version": evt.schema_version,
                "lease_id": None,
                "leased_until": None,
            }
            for evt in events
        ]
        conn.execute(
            pg_insert(table)
            .values(values)
            .on_conflict_do_nothing(index_elements=[table.c.event_id])
        )
        for evt in events:
            LOGGER.info("db_outbox_event_staged", extra={CONTEXT_KEY: event_log_context(evt)})

    async def unsent_events(self, limit: int, source: str | None) -> list[EventEnvelope]:
        table = OutboxModel.__table__
        lease_id = str(uuid.uuid4())
        leased_until = datetime.now(UTC) + timedelta(seconds=DEFAULT_OUTBOX_LEASE_SECONDS)
        available = or_(table.c.lease_id.is_(None), table.c.leased_until < func.now())
        filters = [table.c.sent_at.is_(None), available]
        if source is not None:
            filters.append(table.c.source == source)
        claimable = (
            select(table.c.id)
            .where(*filters)
            .order_by(table.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .cte("claimable_outbox")
        )
        stmt = (
            update(table)
            .where(table.c.id.in_(select(claimable.c.id)))
            .values(lease_id=lease_id, leased_until=leased_until)
            .returning(table)
        )

        async def claim_rows() -> list[dict[str, object]]:
            async with self.async_connection() as conn:
                return list((await conn.execute(stmt)).mappings().all())

        rows = await async_retry_db_conflict(claim_rows)
        return [
            EventEnvelope.from_mapping(
                {
                    "event_id": r["event_id"],
                    "subject": r["subject"],
                    "source": r["source"],
                    "correlation_id": r["correlation_id"],
                    "causation_id": r["causation_id"],
                    "workspace_id": r["workspace_id"],
                    "created_at": r["occurred_at"],
                    "payload": r["payload"],
                    "schema_version": r["schema_version"],
                }
            )
            for r in rows
        ]

    async def mark_events_sent(self, event_ids: list[str]) -> None:
        table = OutboxModel.__table__
        stmt = (
            update(table)
            .where(table.c.event_id.in_(event_ids))
            .values(sent_at=func.now(), lease_id=None, leased_until=None)
        )
        async with self.async_connection() as conn:
            await conn.execute(stmt)
        LOGGER.info(
            "db_outbox_events_sent",
            extra={CONTEXT_KEY: {"event_ids": list(event_ids), "event_count": len(event_ids)}},
        )

    async def mark_events_dead_lettered(
        self, events: list[EventEnvelope], consumer: str, error: str
    ) -> None:
        """비재시도 outbox 이벤트를 DLQ에 남기고 relay 대상에서 제거한다."""
        if not events:
            return
        outbox_table = OutboxModel.__table__
        dead_letter_table = EventDeadLetter.__table__
        compacted_error = compact_error(error)
        async with self.async_connection() as conn:
            for evt in events:
                await conn.execute(
                    pg_insert(dead_letter_table).values(
                        original_event_id=evt.event_id,
                        original_subject=evt.subject,
                        consumer=consumer,
                        correlation_id=evt.correlation_id,
                        attempts=1,
                        error=compacted_error,
                        payload=evt.payload,
                        status=DEAD_LETTER_STATUS_OPEN,
                    )
                )
            await conn.execute(
                update(outbox_table)
                .where(outbox_table.c.event_id.in_([evt.event_id for evt in events]))
                .values(sent_at=func.now(), lease_id=None, leased_until=None)
            )
        for evt in events:
            LOGGER.info(
                "db_outbox_event_dead_lettered",
                extra={
                    CONTEXT_KEY: {
                        **event_log_context(evt),
                        "consumer": consumer,
                    }
                },
            )

    def outbox_pending_count(self) -> int:
        table = OutboxModel.__table__
        statement = select(func.count()).where(table.c.sent_at.is_(None))
        with self.connection() as conn:
            return int(conn.execute(statement).scalar() or 0)

    def outbox_oldest_age_seconds(self) -> float:
        table = OutboxModel.__table__
        oldest_occurred_at = func.min(cast(table.c.occurred_at, TIMESTAMP(timezone=True)))
        statement = select(
            func.coalesce(
                func.extract("epoch", func.now() - oldest_occurred_at),
                0,
            )
        ).where(table.c.sent_at.is_(None))
        with self.connection() as conn:
            return float(conn.execute(statement).scalar() or 0)

    def delete_sent_outbox_older_than(self, cutoff: datetime, *, limit: int = 1000) -> int:
        """발행 완료 outbox 를 보존 기간 이후 배치 삭제한다."""
        table = OutboxModel.__table__
        expired = (
            select(table.c.id)
            .where(table.c.sent_at.is_not(None), table.c.sent_at < cutoff)
            .order_by(table.c.id)
            .limit(limit)
            .cte("expired_outbox")
        )
        statement = table.delete().where(table.c.id.in_(select(expired.c.id))).returning(table.c.id)
        with self.connection() as conn:
            return len(conn.execute(statement).all())

    def dead_letter_unsent_outbox_older_than(self, cutoff: datetime, *, limit: int = 1000) -> int:
        """장기 미발행 outbox 를 DLQ 로 이동한다 — 삭제가 아니라 재처리 가능한 격리.

        relay 가 죽었거나 돌지 않는 환경에서 sent_at IS NULL 행은 발행 대상도
        retention 대상도 아니어서 무한 누적된다. 정상 환경에서는 이 나이까지
        미발행이 존재하지 않으므로 이 sweep 은 no-op 이다. DLQ 행이 남으므로
        원인 복구 후 재발행 판단이 가능하다.
        """
        outbox_table = OutboxModel.__table__
        dead_letter_table = EventDeadLetter.__table__
        stale = (
            select(
                outbox_table.c.id,
                outbox_table.c.event_id,
                outbox_table.c.subject,
                outbox_table.c.correlation_id,
                outbox_table.c.payload,
            )
            .where(
                outbox_table.c.sent_at.is_(None),
                cast(outbox_table.c.occurred_at, TIMESTAMP(timezone=True)) < cutoff,
                # 살아있는 relay 가 방금 lease 한 행은 건드리지 않는다.
                or_(
                    outbox_table.c.lease_id.is_(None),
                    outbox_table.c.leased_until < func.now(),
                ),
            )
            .order_by(outbox_table.c.id)
            .limit(limit)
        )
        with self.connection() as conn:
            rows = conn.execute(stale).mappings().all()
            if not rows:
                return 0
            for row in rows:
                conn.execute(
                    pg_insert(dead_letter_table).values(
                        original_event_id=row["event_id"],
                        original_subject=row["subject"],
                        consumer="outbox-relay:retention",
                        correlation_id=row["correlation_id"],
                        attempts=1,
                        error="unsent outbox exceeded retention window",
                        payload=row["payload"],
                        status=DEAD_LETTER_STATUS_OPEN,
                    )
                )
            conn.execute(
                update(outbox_table)
                .where(outbox_table.c.id.in_([row["id"] for row in rows]))
                .values(sent_at=func.now(), lease_id=None, leased_until=None)
            )
        return len(rows)
