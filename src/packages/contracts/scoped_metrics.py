"""Server-owned, scoped Prometheus query contracts for Resources."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from packages.contracts.gateway.base import StrictModel
from packages.contracts.parity import ClusterScope, ResourceRef

MetricCategory = Literal[
    "cpu",
    "memory",
    "network_rx",
    "network_tx",
    "filesystem",
    "restarts",
    "volume_usage",
    "hpa_current_replicas",
    "hpa_desired_replicas",
]
MetricTimeRange = Literal["15m", "1h", "6h", "24h"]
MetricRefreshPolicyKey = Literal["metrics_prometheus", "metrics_pvc"]


class ResourceMetricSubject(StrictModel):
    kind: Literal["resource"]
    resource_id: str = Field(min_length=1, max_length=255)


class PvcMetricSubject(StrictModel):
    kind: Literal["pvc"]
    resource_id: str = Field(min_length=1, max_length=255)


class NamespaceMetricSubject(StrictModel):
    kind: Literal["namespace"]
    namespace: str = Field(
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$",
    )


class ClusterMetricSubject(StrictModel):
    kind: Literal["cluster"]


ScopedMetricSubject = Annotated[
    ResourceMetricSubject | PvcMetricSubject | NamespaceMetricSubject | ClusterMetricSubject,
    Field(discriminator="kind"),
]


class ScopedMetricQueryRequest(StrictModel):
    cluster_id: str = Field(min_length=1, max_length=255)
    subject: ScopedMetricSubject
    categories: tuple[MetricCategory, ...] = Field(min_length=1, max_length=9)
    range: MetricTimeRange = "1h"

    @field_validator("categories")
    @classmethod
    def categories_are_unique(
        cls, categories: tuple[MetricCategory, ...]
    ) -> tuple[MetricCategory, ...]:
        if len(categories) != len(set(categories)):
            raise ValueError("metric categories must be unique")
        return categories

    @model_validator(mode="after")
    def pvc_category_matches_subject(self) -> ScopedMetricQueryRequest:
        is_pvc = self.subject.kind == "pvc"
        has_volume = "volume_usage" in self.categories
        if is_pvc != has_volume:
            raise ValueError("volume_usage is reserved for the PVC metric subject")
        return self


class ScopedMetricQueryReceipt(StrictModel):
    category: MetricCategory
    unit: Literal["cores", "bytes", "bytes_per_second", "count", "ratio"]
    query_name: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$",
    )
    command_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class ScopedMetricCoverage(StrictModel):
    requested: int = Field(ge=1, le=9)
    queued: int = Field(ge=0, le=9)
    unsupported: int = Field(ge=0, le=9)

    @model_validator(mode="after")
    def counts_match(self) -> ScopedMetricCoverage:
        if self.queued + self.unsupported != self.requested:
            raise ValueError("metric coverage counts must match")
        return self


class ScopedMetricQueryResponse(StrictModel):
    availability: Literal["queued", "partial", "unavailable"]
    source: Literal["prometheus"] = "prometheus"
    refresh_policy_key: MetricRefreshPolicyKey
    scope: ClusterScope
    resource: ResourceRef | None = None
    queries: tuple[ScopedMetricQueryReceipt, ...] = ()
    coverage: ScopedMetricCoverage
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def availability_matches_coverage(self) -> ScopedMetricQueryResponse:
        if self.availability == "queued" and (
            self.coverage.queued != self.coverage.requested or self.reason_codes
        ):
            raise ValueError("queued metric response must have complete coverage")
        if self.availability == "partial" and (
            self.coverage.queued == 0 or self.coverage.unsupported == 0
        ):
            raise ValueError("partial metric response requires mixed coverage")
        if self.availability == "unavailable" and (
            self.coverage.queued != 0 or not self.reason_codes
        ):
            raise ValueError("unavailable metric response requires an explicit reason")
        if len(self.queries) != self.coverage.queued:
            raise ValueError("metric receipts must match queued coverage")
        return self
