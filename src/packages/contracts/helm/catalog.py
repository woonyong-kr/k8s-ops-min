"""Strict chart catalog projections backed by registered Helm sources."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from packages.contracts.gateway.base import StrictModel
from packages.contracts.helm.releases import HelmUpgradeTarget
from packages.contracts.helm.sources import HelmChartSource, HelmChartVersion

HELM_CHART_CATALOG_PAGE_MAX = 100
HELM_CHART_CATALOG_TOTAL_MAX = 1_000_000
HELM_CHART_VALUES_SCHEMA_MAX_BYTES = 65_536

HelmChartCatalogAvailability = Literal["available", "partial", "unavailable"]


class HelmChartSummary(StrictModel):
    """Secret-free chart metadata from one exact authorized source."""

    source: HelmChartSource
    name: str = Field(min_length=1, max_length=512)
    version: str = Field(min_length=1, max_length=256)
    app_version: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=4096)
    deprecated: bool = False


class HelmChartCatalogObservation(StrictModel):
    """Bounded provider result before multi-source aggregation."""

    source: HelmChartSource
    availability: HelmChartCatalogAvailability
    items: tuple[HelmChartSummary, ...] = Field(
        default=(),
        max_length=HELM_CHART_CATALOG_PAGE_MAX,
    )
    total: int = Field(ge=0, le=HELM_CHART_CATALOG_TOTAL_MAX)
    observed_at: str | None = None
    truncated: bool = False
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def observation_is_consistent(self) -> HelmChartCatalogObservation:
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("partial or unavailable Helm chart catalog requires a reason")
        if self.availability == "unavailable" and (self.items or self.total):
            raise ValueError("unavailable Helm chart catalog cannot contain results")
        if self.total < len(self.items):
            raise ValueError("Helm chart catalog total cannot be smaller than its page")
        if any(item.source.source_id != self.source.source_id for item in self.items):
            raise ValueError("Helm chart catalog item source does not match its observation")
        if self.truncated and "helm_chart_catalog_truncated" not in self.reason_codes:
            raise ValueError("truncated Helm chart catalog requires a reason")
        return self


class HelmChartCatalogPage(StrictModel):
    """Workspace-authorized result across registered repository and OCI sources."""

    availability: HelmChartCatalogAvailability
    items: tuple[HelmChartSummary, ...] = Field(
        default=(),
        max_length=HELM_CHART_CATALOG_PAGE_MAX,
    )
    total: int = Field(ge=0, le=HELM_CHART_CATALOG_TOTAL_MAX)
    limit: int = Field(ge=1, le=HELM_CHART_CATALOG_PAGE_MAX)
    query: str = Field(default="", max_length=200)
    source_id: str | None = Field(default=None, max_length=80)
    provider: Literal["repository", "oci"] | None = None
    all_versions: bool = False
    observed_at: str | None = None
    truncated: bool = False
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def page_is_consistent(self) -> HelmChartCatalogPage:
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("partial or unavailable Helm chart catalog page requires a reason")
        if self.availability == "unavailable" and (self.items or self.total):
            raise ValueError("unavailable Helm chart catalog page cannot contain results")
        if self.total < len(self.items):
            raise ValueError("Helm chart catalog total cannot be smaller than its page")
        if self.truncated and "helm_chart_catalog_truncated" not in self.reason_codes:
            raise ValueError("truncated Helm chart catalog page requires a reason")
        return self


class HelmChartValuesSchemaAvailable(StrictModel):
    availability: Literal["available"] = "available"
    schema_value: dict[str, Any] = Field(alias="schema")

    @model_validator(mode="after")
    def schema_is_bounded(self) -> HelmChartValuesSchemaAvailable:
        encoded = json.dumps(self.schema_value, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > HELM_CHART_VALUES_SCHEMA_MAX_BYTES:
            raise ValueError("Helm chart values schema exceeds the byte limit")
        return self


class HelmChartValuesSchemaUnavailable(StrictModel):
    availability: Literal["unavailable"] = "unavailable"
    schema_value: None = Field(default=None, alias="schema")
    reason_code: str = Field(min_length=1, max_length=120)


HelmChartValuesSchema = Annotated[
    HelmChartValuesSchemaAvailable | HelmChartValuesSchemaUnavailable,
    Field(discriminator="availability"),
]


class HelmChartInstallAvailable(StrictModel):
    availability: Literal["available"] = "available"
    target: HelmUpgradeTarget


class HelmChartInstallUnavailable(StrictModel):
    availability: Literal["unavailable"] = "unavailable"
    target: None = None
    reason_code: str = Field(min_length=1, max_length=120)


HelmChartInstall = Annotated[
    HelmChartInstallAvailable | HelmChartInstallUnavailable,
    Field(discriminator="availability"),
]


class HelmChartDetail(StrictModel):
    """Exact chart identity, version evidence, values schema, and install authority."""

    availability: HelmChartCatalogAvailability
    chart: HelmChartSummary | None = None
    versions: tuple[HelmChartVersion, ...] = Field(default=(), max_length=200)
    values_schema: HelmChartValuesSchema
    install: HelmChartInstall
    observed_at: str | None = None
    truncated: bool = False
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def detail_is_consistent(self) -> HelmChartDetail:
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("partial or unavailable Helm chart detail requires a reason")
        if self.availability == "unavailable" and (self.chart is not None or self.versions):
            raise ValueError("unavailable Helm chart detail cannot contain chart metadata")
        if self.truncated and "helm_chart_versions_truncated" not in self.reason_codes:
            raise ValueError("truncated Helm chart detail requires a version reason")
        return self
