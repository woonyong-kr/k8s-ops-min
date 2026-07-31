"""Durable, append-only storage for product timeline evidence.

The table deliberately stores source facts rather than a view projection.  A
broker may announce a committed fact later, but it may never become the source
of truth for replay.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from packages.storage.base import (
    Base,
    created_at_column,
    jsonb_column,
    text_column,
    updated_at_column,
)


class TimelineLedgerCursor(Base):
    """Workspace-local sequence and retention authority for immutable evidence."""

    __tablename__ = "timeline_event_cursors"

    workspace_id: Mapped[str] = mapped_column(Text, primary_key=True)
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    retained_from_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="1"
    )
    updated_at: Mapped[Any] = updated_at_column()


class TimelineLedgerEvent(Base):
    """One source-deduplicated fact in replay order; updates and deletes are forbidden."""

    __tablename__ = "timeline_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "source_key", name="uq_timeline_events_source_key"),
        Index("ix_timeline_events_replay", "workspace_id", "sequence"),
        Index(
            "ix_timeline_events_diagnostics",
            "workspace_id",
            "occurred_at",
            "sequence",
        ),
        Index(
            "ix_timeline_events_scope_time",
            "workspace_id",
            "cluster_id",
            "namespace",
            "occurred_at",
            "sequence",
        ),
        Index(
            "ix_timeline_events_inventory_changes",
            "workspace_id",
            "cluster_id",
            "occurred_at",
            "event_id",
            postgresql_where=text(
                "source = 'inventory' "
                "AND activity = 'change' "
                "AND event_type IN ('add', 'update', 'delete')"
            ),
        ),
    )

    workspace_id: Mapped[str] = mapped_column(Text, primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_key: Mapped[str] = text_column()
    event_id: Mapped[str] = text_column()
    source: Mapped[str] = text_column()
    native_id: Mapped[str] = text_column()
    activity: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    namespace: Mapped[str | None] = mapped_column(Text, nullable=True)
    freshness: Mapped[str] = text_column()
    event_type: Mapped[str] = text_column()
    severity: Mapped[str] = text_column()
    title: Mapped[str] = text_column()
    subject: Mapped[dict[str, Any]] = jsonb_column()
    resource: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    owner: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)
    occurred_at: Mapped[Any] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    recorded_at: Mapped[Any] = created_at_column()


class TimelinePinSetRecord(Base):
    """One optimistic-concurrency revision for a user's persistent workspace pins."""

    __tablename__ = "timeline_pin_sets"

    workspace_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class TimelinePinRecord(Base):
    """Immutable pin subject snapshot; authorization hides rows but never rewrites them."""

    __tablename__ = "timeline_pins"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            "subject_key",
            name="uq_timeline_pins_owner_subject",
        ),
        Index("ix_timeline_pins_owner", "workspace_id", "user_id", "created_at", "pin_id"),
    )

    workspace_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    pin_id: Mapped[str] = mapped_column(Text, primary_key=True)
    subject_key: Mapped[str] = text_column()
    subject: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()
