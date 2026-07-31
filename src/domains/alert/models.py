"""alert 도메인 테이블 — 워크스페이스별 알림 채널(라우팅 룰)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from packages.storage.base import (
    Base,
    created_at_column,
    text_column,
    updated_at_column,
)


class AlertChannel(Base):
    """알림 수신 채널 — min_severity 이상의 알림만 이 채널로 발송된다.

    채널이 하나도 없는 워크스페이스는 기존 전역 provider(env)로 폴백 —
    도입 전 배포와 동작이 완전히 동일하다.
    """

    __tablename__ = "alert_channels"
    __table_args__ = (Index("ix_alert_channels_scope", "workspace_id", "enabled"),)

    channel_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = text_column()
    name: Mapped[str] = text_column()
    kind: Mapped[str] = text_column()  # 현재 "webhook" — slack/teams 등 확장 지점
    url: Mapped[str] = text_column()
    min_severity: Mapped[str] = text_column()  # info | warning | critical
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_tested_at: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_test_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class AlertRule(Base):
    """Opsia가 직접 평가하는 알림 규칙. 클러스터 PrometheusRule과 무관하다."""

    __tablename__ = "alert_rules"
    __table_args__ = (
        Index("ix_alert_rules_workspace_enabled", "workspace_id", "enabled"),
        Index("ix_alert_rules_workspace_name", "workspace_id", "name"),
    )

    rule_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = text_column()
    name: Mapped[str] = text_column()
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metric: Mapped[str] = text_column()
    comparator: Mapped[str] = text_column()
    threshold: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    for_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str] = text_column()
    channels: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = text_column()
    last_fired_at: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    occurrence_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class AlertRuleTargetState(Base):
    """규칙·대상별 지속 조건과 활성 사건 상태."""

    __tablename__ = "alert_rule_target_states"
    __table_args__ = (
        Index("ix_alert_rule_target_states_workspace_rule", "workspace_id", "rule_id"),
    )

    rule_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("alert_rules.rule_id", ondelete="CASCADE"),
        primary_key=True,
    )
    subject_key: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = text_column()
    subject: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    condition_since: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    active_event_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_observed_value: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    last_evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    last_evaluated_at: Mapped[Any] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class AlertEvent(Base):
    """울린 뒤 해소돼도 남는 알림 사건 원장."""

    __tablename__ = "alert_events"
    __table_args__ = (
        Index("ix_alert_events_workspace_fired", "workspace_id", "fired_at"),
        Index("ix_alert_events_workspace_status", "workspace_id", "status"),
        Index(
            "uq_alert_events_active_subject",
            "rule_id",
            "subject_key",
            unique=True,
            postgresql_where=text("status in ('firing', 'acked')"),
        ),
    )

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = text_column()
    # 규칙이 삭제돼도 이미 발생한 사건은 감사/알림 이력으로 남긴다.
    rule_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = text_column()
    severity: Mapped[str] = text_column()
    subject_key: Mapped[str] = text_column()
    subject: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fired_at: Mapped[Any] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    resolved_at: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    status: Mapped[str] = text_column()
    observed_value: Mapped[float | None] = mapped_column(Float(precision=53), nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float(precision=53), nullable=True)
    series_identity: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    incident_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledged_at: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    promoted_at: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    promoted_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()
