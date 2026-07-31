"""Durable user/workspace Checks settings authority."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, PrimaryKeyConstraint, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.storage.base import Base, created_at_column, updated_at_column


class UserChecksSettingsRecord(Base):
    __tablename__ = "user_checks_settings"
    __table_args__ = (PrimaryKeyConstraint("workspace_id", "user_id"),)

    workspace_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    invalidation_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()
