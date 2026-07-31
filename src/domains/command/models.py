"""command 도메인 테이블 — 에이전트 명령 큐."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from packages.storage.base import (
    Base,
    created_at_column,
    jsonb_column,
    text_column,
    updated_at_column,
)


class AgentCommand(Base):
    """Logical command authority.

    ``command_id`` is deliberately stable for its entire lifetime.  Individual
    agent executions live in :class:`AgentCommandAttempt`; never reuse this
    row's lease when a command is retried.
    """

    __tablename__ = "agent_commands"

    command_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = text_column()
    correlation_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    action: Mapped[str] = text_column()
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    payload: Mapped[dict[str, Any]] = jsonb_column()
    status: Mapped[str] = text_column()
    lease_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    leased_until: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    started_at: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    result: Mapped[dict[str, Any]] = jsonb_column()
    # Receipt event that records the one explicit direct-execution confirmation.
    # It is immutable provenance, not a browser-controlled flag.
    confirmation_event_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    impact_identity: Mapped[str | None] = mapped_column(Text, nullable=True)
    direct_execution: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    active_attempt_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested_at: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    cancel_requested_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_accepted_at: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    cancel_generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    terminal_event_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class AgentCommandAttempt(Base):
    """One leased agent execution for a stable logical command."""

    __tablename__ = "agent_command_attempts"
    __table_args__ = (
        UniqueConstraint("command_id", "attempt_no", name="uq_agent_command_attempt_number"),
        Index(
            "ix_agent_command_attempts_due",
            "workspace_id",
            "cluster_id",
            "status",
            "available_at",
        ),
        Index("ix_agent_command_attempts_command", "workspace_id", "command_id", "attempt_no"),
    )

    attempt_id: Mapped[str] = mapped_column(Text, primary_key=True)
    command_id: Mapped[str] = text_column()
    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = text_column()
    available_at: Mapped[Any] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    lease_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    leased_until: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    started_at: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    result: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class CommandControlAction(Base):
    """Idempotent, append-only actor intent for cancel/retry controls."""

    __tablename__ = "command_control_actions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "command_id",
            "action",
            "idempotency_key",
            name="uq_command_control_action_idempotency",
        ),
        Index("ix_command_control_actions_command", "workspace_id", "command_id", "created_at"),
    )

    control_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = text_column()
    command_id: Mapped[str] = text_column()
    action: Mapped[str] = text_column()
    idempotency_key: Mapped[str] = text_column()
    requested_by: Mapped[str] = text_column()
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str] = text_column()
    event_id: Mapped[str] = text_column()
    audit_event_id: Mapped[str] = text_column()
    attempt_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class CommandOperationEventCursor(Base):
    """Per-workspace command sequence authority for replayable browser events."""

    __tablename__ = "command_operation_event_cursors"

    workspace_id: Mapped[str] = mapped_column(Text, primary_key=True)
    command_id: Mapped[str] = mapped_column(Text, primary_key=True)
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    terminal_sequence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[Any] = updated_at_column()


class CommandOperationEvent(Base):
    """Append-only command event history; Redis only announces these committed rows."""

    __tablename__ = "command_operation_events"
    __table_args__ = (
        Index(
            "ix_command_operation_events_replay",
            "workspace_id",
            "command_id",
            "sequence",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(Text, primary_key=True)
    command_id: Mapped[str] = mapped_column(Text, primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cluster_id: Mapped[str] = text_column()
    kind: Mapped[str] = text_column()
    payload: Mapped[dict[str, Any]] = jsonb_column()
    occurred_at: Mapped[Any] = created_at_column()
