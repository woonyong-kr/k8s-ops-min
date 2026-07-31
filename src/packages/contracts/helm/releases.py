"""Provider-neutral, read-only Helm release observation contracts.

Helm release storage is commonly represented by Secrets or ConfigMaps.  This
boundary intentionally exposes only inventory metadata: it does not decode
release payloads, values, rendered manifests, or credentials.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, model_validator

from packages.contracts.gateway.base import StrictModel
from packages.contracts.helm.sources import HelmChartSource, HelmChartVersion
from packages.contracts.parity import ClusterScope, ResourceRef

HelmAvailability = Literal["available", "partial", "unavailable"]
HELM_UPGRADE_BATCH_MAX_RELEASES = 100


class HelmObservationCoverage(StrictModel):
    """Completeness of the inventory cut used for Helm storage discovery."""

    availability: HelmAvailability
    observed_at: str | None = None
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def unavailable_coverage_has_a_reason(self) -> HelmObservationCoverage:
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("partial or unavailable Helm coverage requires a reason")
        return self


class HelmResourceHealthAvailability(StrictModel):
    """Health is unavailable until inventory ownership correlation is proven."""

    availability: Literal["unavailable"] = "unavailable"
    health: None = None
    reason_code: str = Field(min_length=1)


class HelmResourceHealthObservation(StrictModel):
    """Health rollup derived only from exactly correlated owned resources."""

    availability: Literal["available", "partial"]
    health: str = Field(min_length=1)
    resource_count: int = Field(ge=0)
    observed_at: str | None = None
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def incomplete_health_has_a_reason(self) -> HelmResourceHealthObservation:
        if self.availability == "partial" and not self.reason_codes:
            raise ValueError("partial Helm resource health requires a reason")
        return self


class HelmFeatureAvailability(StrictModel):
    """Explicit absence of an external Helm/provider integration."""

    availability: Literal["unavailable"] = "unavailable"
    reason_code: str = Field(min_length=1)


HelmUpgradeValueType = Literal["string", "integer", "number", "boolean"]
HelmUpgradeScalar = str | int | float | bool | None


class HelmUpgradeInput(StrictModel):
    """One server-declared primitive value accepted by an executable recipe."""

    name: str = Field(min_length=1, max_length=253)
    value_type: HelmUpgradeValueType
    required: bool
    default: HelmUpgradeScalar = None
    allowed_values: tuple[HelmUpgradeScalar, ...] = ()

    @model_validator(mode="after")
    def scalar_types_match(self) -> HelmUpgradeInput:
        values = (self.default, *self.allowed_values)
        if any(
            value is not None and not _upgrade_scalar_matches(self.value_type, value)
            for value in values
        ):
            raise ValueError("Helm upgrade input scalar type is inconsistent")
        return self


class HelmUpgradeTarget(StrictModel):
    """A target backed by the existing digest-pinned catalog Helm executor."""

    item_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    chart_version: str = Field(min_length=1, max_length=80)
    inputs: tuple[HelmUpgradeInput, ...] = ()


class HelmReleaseCommands(StrictModel):
    availability: Literal["available"] = "available"
    actions: tuple[Literal["upgrade", "rollback", "uninstall"], ...] = (
        "upgrade",
        "rollback",
        "uninstall",
    )
    confirmation_required: Literal[True] = True
    realtime: Literal[True] = True
    upgrade_targets: tuple[HelmUpgradeTarget, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def executable_actions_are_exact(self) -> HelmReleaseCommands:
        if self.actions != ("upgrade", "rollback", "uninstall"):
            raise ValueError("Helm release commands must expose the reviewed action set")
        identities = tuple((item.item_id, item.version) for item in self.upgrade_targets)
        if len(set(identities)) != len(identities):
            raise ValueError("Helm upgrade targets must be unique")
        for target in self.upgrade_targets:
            names = tuple(item.name for item in target.inputs)
            if len(set(names)) != len(names):
                raise ValueError("Helm upgrade inputs must be unique")
        return self


class HelmCandidateValues(StrictModel):
    """Bounded server-owned catalog candidate shared by preview and apply."""

    catalog_item_id: str = Field(min_length=1, max_length=120)
    catalog_version: str = Field(min_length=1, max_length=80)
    values: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def values_are_bounded(self) -> HelmCandidateValues:
        if len(self.values) > 100:
            raise ValueError("Helm candidate values exceed the field limit")
        try:
            encoded = json.dumps(self.values, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as error:
            raise ValueError("Helm candidate values must be JSON compatible") from error
        if len(encoded.encode("utf-8")) > 65_536:
            raise ValueError("Helm candidate values exceed the byte limit")
        return self


class HelmReleaseCandidateRequest(HelmCandidateValues):
    cluster_id: str = Field(min_length=1, max_length=253)
    expected_revision: int = Field(ge=1)


class HelmReleaseUpgradeRequest(HelmReleaseCandidateRequest):
    confirmation: Literal[True]
    reason: str | None = Field(default=None, max_length=500)


class HelmInstallTargetsResponse(StrictModel):
    namespace: str = Field(min_length=1, max_length=63)
    targets: tuple[HelmUpgradeTarget, ...] = Field(default=(), max_length=100)


class HelmReleaseInstallRequest(StrictModel):
    cluster_id: str = Field(min_length=1, max_length=253)
    namespace: str = Field(min_length=1, max_length=63)
    application_name: str = Field(min_length=1, max_length=120)
    release_name: str = Field(min_length=1, max_length=120)
    catalog_item_id: str = Field(min_length=1, max_length=120)
    catalog_version: str = Field(min_length=1, max_length=80)
    values: dict[str, Any] = Field(default_factory=dict)
    confirmation: Literal[True]

    @model_validator(mode="after")
    def values_are_bounded(self) -> HelmReleaseInstallRequest:
        HelmReleaseUpgradeRequest(
            cluster_id=self.cluster_id,
            expected_revision=1,
            catalog_item_id=self.catalog_item_id,
            catalog_version=self.catalog_version,
            values=self.values,
            confirmation=True,
        )
        return self


def _upgrade_scalar_matches(value_type: HelmUpgradeValueType, value: object) -> bool:
    if value_type == "string":
        return isinstance(value, str)
    if value_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, bool)


class HelmOwnedResource(StrictModel):
    """One inventory resource with exact standard Helm ownership metadata."""

    resource: ResourceRef
    status: str = Field(min_length=1)
    health: str = Field(min_length=1)
    observed_at: str | None = None


class HelmOwnedResourceObservation(StrictModel):
    """Bounded owned-resource collection from one inventory observation cut."""

    availability: Literal["available", "partial"]
    items: tuple[HelmOwnedResource, ...] = ()
    observed_at: str | None = None
    truncated: bool = False
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def incomplete_resources_have_a_reason(self) -> HelmOwnedResourceObservation:
        if self.availability == "partial" and not self.reason_codes:
            raise ValueError("partial Helm owned resources require a reason")
        if self.truncated and "helm_owned_resources_truncated" not in self.reason_codes:
            raise ValueError("truncated Helm owned resources require the truncation reason")
        return self


class HelmRelease(StrictModel):
    """One release inferred from an observed Helm storage metadata record."""

    scope: ClusterScope
    name: str = Field(min_length=1)
    storage_namespace: str = Field(min_length=1)
    storage: ResourceRef
    storage_resource_version: str | None = Field(default=None, min_length=1, max_length=253)
    chart: str | None = Field(default=None, min_length=1, max_length=512)
    chart_version: str | None = Field(default=None, min_length=1, max_length=256)
    chart_reason_codes: tuple[str, ...] = ()
    app_version: None = None
    status: str | None = None
    revision: int | None = Field(default=None, ge=1)
    observed_at: str | None = None
    resource_health: HelmResourceHealthObservation | HelmResourceHealthAvailability

    @model_validator(mode="after")
    def chart_identity_is_explicit(self) -> HelmRelease:
        if (self.chart is None) != (self.chart_version is None):
            raise ValueError("Helm chart name and version must be observed together")
        if self.chart is None and not self.chart_reason_codes:
            raise ValueError("unavailable Helm chart identity requires a reason")
        if self.chart is not None and self.chart_reason_codes:
            raise ValueError("observed Helm chart identity cannot contain unavailable reasons")
        return self


class HelmReleaseUpgradeInfo(StrictModel):
    """Current-versus-latest comparison from one exact authorized source."""

    availability: HelmAvailability
    chart_name: str | None = Field(default=None, min_length=1, max_length=512)
    current_version: str | None = Field(default=None, min_length=1, max_length=256)
    latest_version: str | None = Field(default=None, min_length=1, max_length=256)
    update_available: bool | None = None
    source: HelmChartSource | None = None
    observed_at: str | None = None
    reason_codes: tuple[str, ...] = ()
    refresh_after_seconds: int = Field(ge=1, le=3600)

    @model_validator(mode="after")
    def availability_is_consistent(self) -> HelmReleaseUpgradeInfo:
        complete = all(
            value is not None
            for value in (
                self.chart_name,
                self.current_version,
                self.latest_version,
                self.update_available,
                self.source,
            )
        )
        if self.availability == "unavailable":
            if complete or not self.reason_codes:
                raise ValueError("unavailable Helm upgrade info requires only explicit reasons")
        elif not complete:
            raise ValueError("available Helm upgrade info requires exact source and versions")
        elif self.availability == "partial" and not self.reason_codes:
            raise ValueError("partial Helm upgrade info requires reasons")
        elif self.availability == "available" and self.reason_codes:
            raise ValueError("available Helm upgrade info cannot contain reasons")
        return self


class HelmReleaseVersionList(StrictModel):
    """Newest-first bounded versions from the same source used for upgrade info."""

    availability: HelmAvailability
    chart_name: str | None = Field(default=None, min_length=1, max_length=512)
    current_version: str | None = Field(default=None, min_length=1, max_length=256)
    source: HelmChartSource | None = None
    versions: tuple[HelmChartVersion, ...] = Field(default=(), max_length=200)
    observed_at: str | None = None
    truncated: bool = False
    reason_codes: tuple[str, ...] = ()
    refresh_after_seconds: int = Field(ge=1, le=3600)

    @model_validator(mode="after")
    def availability_is_consistent(self) -> HelmReleaseVersionList:
        if self.availability == "unavailable":
            if self.versions or self.source is not None or not self.reason_codes:
                raise ValueError("unavailable Helm versions require only explicit reasons")
        elif (
            self.chart_name is None
            or self.current_version is None
            or self.source is None
            or not self.versions
        ):
            raise ValueError("available Helm versions require exact source evidence")
        elif self.availability == "partial" and not self.reason_codes:
            raise ValueError("partial Helm versions require reasons")
        elif self.availability == "available" and self.reason_codes:
            raise ValueError("available Helm versions cannot contain reasons")
        if self.truncated and "helm_chart_versions_truncated" not in self.reason_codes:
            raise ValueError("truncated Helm versions require the truncation reason")
        return self


class HelmReleaseUpgradeBatch(StrictModel):
    """Bounded multi-cluster release upgrade decoration."""

    releases: dict[str, HelmReleaseUpgradeInfo] = Field(
        default_factory=dict,
        max_length=HELM_UPGRADE_BATCH_MAX_RELEASES,
    )
    coverage: HelmObservationCoverage
    truncated: bool = False
    reason_codes: tuple[str, ...] = ()
    refresh_after_seconds: int = Field(ge=1, le=3600)

    @model_validator(mode="after")
    def truncation_is_explicit(self) -> HelmReleaseUpgradeBatch:
        if self.truncated and "helm_upgrade_batch_truncated" not in self.reason_codes:
            raise ValueError("truncated Helm upgrade batch requires a reason")
        if not self.truncated and "helm_upgrade_batch_truncated" in self.reason_codes:
            raise ValueError("Helm upgrade batch truncation reason requires truncation")
        return self


class HelmReleaseHistoryEntry(StrictModel):
    """One inventory-observed storage revision; never a decoded release body."""

    storage: ResourceRef
    revision: int | None = Field(default=None, ge=1)
    status: str | None = None
    observed_at: str | None = None


class HelmReleaseDetail(StrictModel):
    release: HelmRelease
    history: tuple[HelmReleaseHistoryEntry, ...] = ()
    manifest: HelmFeatureAvailability
    values: HelmFeatureAvailability
    owned_resources: HelmOwnedResourceObservation | HelmFeatureAvailability
    commands: HelmFeatureAvailability | HelmReleaseCommands


class HelmReleaseListResponse(StrictModel):
    releases: tuple[HelmRelease, ...] = ()
    coverage: HelmObservationCoverage
    refresh_after_seconds: int = Field(ge=1, le=3600)
    post_mutation_refresh_after_seconds: float = Field(gt=0, le=60)


class HelmReleaseDetailResponse(StrictModel):
    detail: HelmReleaseDetail
    refresh_after_seconds: int = Field(ge=1, le=3600)
    post_mutation_refresh_after_seconds: float = Field(gt=0, le=60)
