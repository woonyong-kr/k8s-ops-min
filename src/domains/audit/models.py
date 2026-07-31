"""audit 도메인 테이블."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Index, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from packages.storage.base import (
    Base,
    created_at_column,
    jsonb_column,
    text_column,
)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_created_at", "created_at"),
        Index(
            "ix_audit_log_correlation_id_created_at",
            "correlation_id",
            "created_at",
        ),
        Index(
            "ix_audit_log_workspace_id_correlation_id_created_at",
            "workspace_id",
            "correlation_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = text_column()
    subject: Mapped[str] = text_column()
    source: Mapped[str] = text_column()
    correlation_id: Mapped[str] = text_column()
    causation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = jsonb_column()
    event_created_at: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[Any] = created_at_column()
