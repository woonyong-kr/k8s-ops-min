"""Release-flow tables."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.storage.base import (
    Base,
    created_at_column,
    jsonb_column,
    text_column,
    updated_at_column,
)


class ReleasePlan(Base):
    __tablename__ = "release_plans"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    plan_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.workspace_id"))
    name: Mapped[str] = text_column()
    description: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    settings: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class ReleasePlanStep(Base):
    __tablename__ = "release_plan_steps"
    __table_args__ = (
        UniqueConstraint("plan_id", "position"),
        UniqueConstraint("plan_id", "application_id"),
    )

    step_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = text_column()
    plan_id: Mapped[str] = mapped_column(ForeignKey("release_plans.plan_id"))
    application_id: Mapped[str] = text_column()
    name: Mapped[str] = text_column()
    position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    depends_on: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    config: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class ReleaseRun(Base):
    __tablename__ = "release_runs"

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.workspace_id"))
    plan_id: Mapped[str] = text_column()
    plan_name: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    current_wave: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_waves: Mapped[int] = mapped_column(BigInteger, nullable=False)
    started_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    settings: Mapped[dict[str, Any]] = jsonb_column()
    github: Mapped[dict[str, Any]] = jsonb_column()
    rollback: Mapped[dict[str, Any]] = jsonb_column()
    health: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class ReleaseRunStep(Base):
    __tablename__ = "release_run_steps"
    __table_args__ = (UniqueConstraint("run_id", "application_id"),)

    run_step_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = text_column()
    run_id: Mapped[str] = mapped_column(ForeignKey("release_runs.run_id"))
    step_id: Mapped[str] = text_column()
    application_id: Mapped[str] = text_column()
    name: Mapped[str] = text_column()
    wave: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = text_column()
    workflow_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    health: Mapped[dict[str, Any]] = jsonb_column()
    rollback: Mapped[dict[str, Any]] = jsonb_column()
    details: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class ReleaseRunEvent(Base):
    __tablename__ = "release_run_events"

    audit_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = text_column()
    run_id: Mapped[str] = mapped_column(ForeignKey("release_runs.run_id"))
    event_type: Mapped[str] = text_column()
    message: Mapped[str] = text_column()
    actor: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()
