"""scm 도메인 테이블."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from packages.storage.base import Base, created_at_column, text_column


class PullRequest(Base):
    __tablename__ = "pull_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    correlation_id: Mapped[str] = text_column()
    pr_url: Mapped[str] = text_column()
    title: Mapped[str] = text_column()
    body: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    created_at: Mapped[Any] = created_at_column()
