"""Durable Diagnose run, replay event, and disclosure-consent tables."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, PrimaryKeyConstraint, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from packages.storage.base import (
    Base,
    created_at_column,
    text_column,
    updated_at_column,
)


class DiagnoseRunRecord(Base):
    __tablename__ = "diagnose_runs"
    __table_args__ = (
        Index(
            "ux_diagnose_runs_active_deduplication",
            "workspace_id",
            "deduplication_key",
            unique=True,
            postgresql_where=text("active IS TRUE"),
        ),
        Index(
            "ix_diagnose_runs_history",
            "workspace_id",
            "requested_by",
            "updated_at",
        ),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    requested_by: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_key: Mapped[str] = text_column()
    deduplication_key: Mapped[str] = text_column()
    target: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    agent: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class DiagnoseEventCursorRecord(Base):
    __tablename__ = "diagnose_event_cursors"

    run_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("diagnose_runs.run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    workspace_id: Mapped[str] = text_column()
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    updated_at: Mapped[Any] = updated_at_column()


class DiagnoseEventRecord(Base):
    __tablename__ = "diagnose_events"
    __table_args__ = (
        PrimaryKeyConstraint("run_id", "sequence"),
        Index("ix_diagnose_events_replay", "workspace_id", "run_id", "sequence"),
    )

    run_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("diagnose_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    workspace_id: Mapped[str] = text_column()
    kind: Mapped[str] = text_column()
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[Any] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class DiagnoseConsentRecord(Base):
    __tablename__ = "diagnose_consents"
    __table_args__ = (
        PrimaryKeyConstraint(
            "workspace_id",
            "requested_by",
            "agent_id",
            "disclosure_revision",
            "surface",
        ),
    )

    workspace_id: Mapped[str] = text_column()
    requested_by: Mapped[str] = text_column()
    agent_id: Mapped[str] = text_column()
    disclosure_revision: Mapped[str] = text_column()
    surface: Mapped[str] = text_column()
    granted_at: Mapped[Any] = created_at_column()
