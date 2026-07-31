"""Opsia 알림 규칙 HTTP 계약."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from domains.inventory_filter.query import parse_resource_filters
from packages.contracts.gateway.base import StrictModel

AlertScopeValue = Annotated[str, Field(min_length=1, max_length=512)]
AlertChannelId = Annotated[str, Field(min_length=1, max_length=120)]
AlertMetric = Literal["cpu_pct", "mem_pct", "restart_count", "pod_not_ready"]
AlertComparator = Literal[">", ">=", "<", "<="]
AlertSeverity = Literal["critical", "high", "medium", "low"]
AlertEventSeverity = Literal["critical", "high", "medium", "low", "warning", "info"]
AlertEventStatus = Literal["firing", "resolved", "acked"]


class AlertRuleScope(StrictModel):
    """1층 통합 검색과 동일한 네 축의 canonical 범위."""

    clusters: list[AlertScopeValue] = Field(default_factory=list, max_length=50)
    namespaces: list[AlertScopeValue] = Field(default_factory=list, max_length=100)
    applications: list[AlertScopeValue] = Field(default_factory=list, max_length=100)
    labels: list[AlertScopeValue] = Field(default_factory=list, max_length=24)

    @field_validator("clusters", "namespaces", "applications", "labels")
    @classmethod
    def reject_embedded_filter_delimiter(cls, values: list[str]) -> list[str]:
        if any("," in value for value in values):
            raise ValueError("filter values cannot contain commas")
        return values

    @model_validator(mode="after")
    def canonicalize_with_resource_filters(self) -> AlertRuleScope:
        filters = parse_resource_filters(
            clusters=_join(self.clusters),
            namespaces=_join(self.namespaces),
            applications=_join(self.applications),
            resource_types=None,
            health=None,
            labels=_join(self.labels),
            query=None,
            include_deleted=False,
        )
        self.clusters = list(filters.clusters)
        self.namespaces = [
            f"{cluster_id}/{namespace}" for cluster_id, namespace in filters.namespaces
        ]
        self.applications = list(filters.applications)
        self.labels = [f"{key}={value}" for key, value in filters.labels]
        return self


class AlertRuleCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    scope: AlertRuleScope
    metric: AlertMetric
    comparator: AlertComparator
    threshold: float = Field(ge=0, allow_inf_nan=False)
    for_seconds: int = Field(ge=1, le=86_400)
    severity: AlertSeverity
    channels: list[AlertChannelId] = Field(default_factory=list, max_length=20)
    enabled: bool

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("name cannot be blank")
        return normalized

    @field_validator("channels")
    @classmethod
    def canonicalize_channels(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("channel id cannot be blank")
        return sorted(set(normalized))


class AlertRuleCreatedResponse(StrictModel):
    rule_id: str = Field(min_length=1, max_length=120)


class AlertRulePatchRequest(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    scope: AlertRuleScope | None = None
    metric: AlertMetric | None = None
    comparator: AlertComparator | None = None
    threshold: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    for_seconds: int | None = Field(default=None, ge=1, le=86_400)
    severity: AlertSeverity | None = None
    channels: list[AlertChannelId] | None = Field(default=None, max_length=20)
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("name cannot be blank")
        return normalized

    @field_validator("channels")
    @classmethod
    def canonicalize_optional_channels(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("channel id cannot be blank")
        return sorted(set(normalized))

    @model_validator(mode="after")
    def require_nonempty_nonnull_patch(self) -> AlertRulePatchRequest:
        changes = self.model_dump(exclude_unset=True)
        if not changes:
            raise ValueError("alert rule patch cannot be empty")
        if any(value is None for value in changes.values()):
            raise ValueError("alert rule fields cannot be null")
        return self


class AlertRuleResponse(StrictModel):
    rule_id: str = Field(min_length=1, max_length=120)
    name: str
    scope: AlertRuleScope
    metric: AlertMetric
    comparator: AlertComparator
    threshold: float
    for_seconds: int
    severity: AlertSeverity
    channels: list[str]
    enabled: bool
    last_fired_at: datetime | None = None
    occurrence_count: int = Field(ge=0)
    created_by: str
    created_at: datetime
    updated_at: datetime


class AlertRuleListResponse(StrictModel):
    rules: list[AlertRuleResponse] = Field(default_factory=list)


class AlertEventSubject(StrictModel):
    cluster: str = Field(min_length=1, max_length=512)
    namespace: str | None = Field(default=None, max_length=253)
    kind: str = Field(min_length=1, max_length=253)
    name: str = Field(min_length=1, max_length=253)


class AlertEvidenceItem(StrictModel):
    type: str = Field(min_length=1, max_length=80)
    metric: str | None = Field(default=None, min_length=1, max_length=120)
    observed_at: datetime | None = None
    subject: AlertEventSubject | None = None
    value: float | None = Field(default=None, allow_inf_nan=False)
    summary: str | None = Field(default=None, min_length=1, max_length=1000)
    link: str | None = Field(default=None, pattern=r"^/")

    @model_validator(mode="after")
    def require_material_evidence(self) -> AlertEvidenceItem:
        if all(
            value is None
            for value in (
                self.metric,
                self.observed_at,
                self.subject,
                self.value,
                self.summary,
                self.link,
            )
        ):
            raise ValueError("alert evidence must contain a material fact or reference")
        return self


class AlertEventResponse(StrictModel):
    event_id: str = Field(min_length=1, max_length=120)
    rule_id: str | None = Field(default=None, max_length=120)
    rule_name: str | None = Field(default=None, max_length=120)
    source: Literal["opsia", "alertmanager", "incident"]
    severity: AlertEventSeverity
    subject: AlertEventSubject
    fired_at: datetime
    resolved_at: datetime | None = None
    status: AlertEventStatus
    observed_value: float | None = Field(default=None, allow_inf_nan=False)
    threshold: float | None = Field(default=None, allow_inf_nan=False)
    evidence: list[AlertEvidenceItem] = Field(min_length=1)
    incident_id: str | None = None
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None
    promoted_at: datetime | None = None
    promoted_by: str | None = None

    @model_validator(mode="after")
    def require_opsia_rule_measurement(self) -> AlertEventResponse:
        if self.source == "opsia" and (
            not self.rule_id
            or not self.rule_name
            or self.observed_value is None
            or self.threshold is None
        ):
            raise ValueError("Opsia alert events require a rule and observed threshold evidence")
        return self


class AlertIncidentPromotionResponse(StrictModel):
    incident_id: str = Field(min_length=1, max_length=120)


def _join(values: list[str]) -> str | None:
    return ",".join(values) if values else None
