from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Index, Integer, PrimaryKeyConstraint, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from packages.storage.base import (
    Base,
    created_at_column,
    jsonb_column,
    text_column,
    updated_at_column,
)


class EventModel(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_created_at", "created_at"),)

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    subject: Mapped[str] = text_column()
    source: Mapped[str] = text_column()
    correlation_id: Mapped[str] = text_column()
    causation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = jsonb_column()
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[Any] = created_at_column()


class EventProcessing(Base):
    __tablename__ = "event_processing"
    __table_args__ = (PrimaryKeyConstraint("event_id", "consumer"),)

    event_id: Mapped[str] = mapped_column(Text, nullable=False)
    consumer: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = text_column()
    correlation_id: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    processing_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class EventConsumerMetric(Base):
    __tablename__ = "event_consumer_metrics"
    __table_args__ = (PrimaryKeyConstraint("consumer", "subject"),)

    consumer: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    stream: Mapped[str] = text_column()
    pending_events: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ack_pending_events: Mapped[int] = mapped_column(BigInteger, nullable=False)
    redelivered_events: Mapped[int] = mapped_column(BigInteger, nullable=False)
    observed_at: Mapped[Any] = updated_at_column()


class EventDeadLetter(Base):
    __tablename__ = "event_dead_letters"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    original_event_id: Mapped[str] = text_column()
    original_subject: Mapped[str] = text_column()
    consumer: Mapped[str] = text_column()
    correlation_id: Mapped[str] = text_column()
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    error: Mapped[str] = text_column()
    payload: Mapped[dict[str, Any]] = jsonb_column()
    status: Mapped[str] = text_column()
    replayed_at: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True))
    replay_event_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[Any] = created_at_column()


class OutboxModel(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        Index("ix_outbox_claim", "source", "sent_at", "leased_until", "id"),
        Index(
            "ix_outbox_claim_all_sources",
            "sent_at",
            "leased_until",
            "id",
            postgresql_where=text("sent_at IS NULL"),
        ),
        Index("ix_outbox_sent_at", "sent_at", postgresql_where=text("sent_at IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    subject: Mapped[str] = text_column()
    source: Mapped[str] = text_column()
    correlation_id: Mapped[str] = text_column()
    causation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[str] = text_column()
    payload: Mapped[dict[str, Any]] = jsonb_column()
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    lease_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    leased_until: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    sent_at: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


metadata = Base.metadata
