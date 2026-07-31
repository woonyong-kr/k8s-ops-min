"""AI 대화 read model 테이블."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.storage.base import (
    Base,
    created_at_column,
    jsonb_column,
    text_column,
    updated_at_column,
)


class AiConversation(Base):
    __tablename__ = "ai_conversations"

    conversation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = text_column()
    user_id: Mapped[str] = text_column()
    title: Mapped[str] = text_column()
    agent: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    context: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class AiConversationMessage(Base):
    __tablename__ = "ai_conversation_messages"

    message_id: Mapped[str] = mapped_column(Text, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("ai_conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[str] = text_column()
    role: Mapped[str] = text_column()
    content: Mapped[str] = text_column()
    agent: Mapped[str] = text_column()
    correlation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)
    created_at: Mapped[Any] = created_at_column()


class AiLlmInvocationMetric(Base):
    __tablename__ = "ai_llm_invocation_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = text_column()
    provider: Mapped[str] = text_column()
    model: Mapped[str] = text_column()
    operation: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    causation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = created_at_column()
