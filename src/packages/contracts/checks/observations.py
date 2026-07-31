"""Evidence-first contracts for agent-reported Checks observations.

The gateway never evaluates a cluster directly.  It only projects a validated
observation embedded in a persisted outbound cluster-agent inventory snapshot.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from packages.contracts.gateway.base import StrictModel
from packages.contracts.parity import ClusterScope, ResourceRef

ChecksAvailability = Literal["available", "partial", "unavailable"]
ChecksSeverity = Literal["warning", "danger"]
ChecksVisibilityState = Literal["ok", "limited", "degraded"]
ChecksVisibilityAccess = Literal["allowed", "namespace_limited", "unavailable"]


def _unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    normalized = tuple(value.strip() for value in values)
    if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must contain unique non-empty values")
    return tuple(sorted(normalized))


class ChecksScopeCoverage(StrictModel):
    availability: ChecksAvailability
    scopes: tuple[ClusterScope, ...] = ()
    observed_at: str | None = None
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def incomplete_scope_has_a_reason(self) -> ChecksScopeCoverage:
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("incomplete checks scope coverage requires a reason")
        return self


class AgentChecksFinding(StrictModel):
    finding_id: str = Field(min_length=1, max_length=253)
    check_id: str = Field(min_length=1, max_length=253)
    category: str = Field(min_length=1, max_length=120)
    severity: ChecksSeverity
    message: str = Field(min_length=1, max_length=4_000)
    resource: ResourceRef


class ChecksFinding(AgentChecksFinding):
    cluster_id: str = Field(min_length=1)


class ChecksCatalogEntry(StrictModel):
    check_id: str = Field(min_length=1, max_length=253)
    title: str = Field(min_length=1, max_length=240)
    category: str = Field(min_length=1, max_length=120)
    severity: ChecksSeverity
    description: str = Field(min_length=1, max_length=4_000)
    remediation: str = Field(min_length=1, max_length=4_000)


class AgentChecksVisibility(StrictModel):
    state: ChecksVisibilityState
    namespace_scope: tuple[str, ...] = ()
    core: dict[str, ChecksVisibilityAccess] = Field(min_length=1)
    missing_optional_kinds: tuple[str, ...] = ()

    @field_validator("namespace_scope")
    @classmethod
    def unique_namespaces(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _unique(values, "namespace_scope")

    @field_validator("missing_optional_kinds")
    @classmethod
    def unique_optional_kinds(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _unique(values, "missing_optional_kinds")

    @field_validator("core")
    @classmethod
    def valid_core_kinds(
        cls, values: dict[str, ChecksVisibilityAccess]
    ) -> dict[str, ChecksVisibilityAccess]:
        normalized = {key.strip(): value for key, value in values.items() if key.strip()}
        if len(normalized) != len(values):
            raise ValueError("visibility core kinds must be non-empty")
        return dict(sorted(normalized.items()))


class ChecksVisibility(AgentChecksVisibility):
    cluster_id: str = Field(min_length=1)


class AgentChecksObservation(StrictModel):
    """One atomic cluster-agent evaluation attached to an inventory snapshot."""

    availability: ChecksAvailability
    observed_at: datetime | None = None
    namespaces: tuple[str, ...] = ()
    findings: tuple[AgentChecksFinding, ...] | None = None
    catalog: tuple[ChecksCatalogEntry, ...] | None = None
    visibility: AgentChecksVisibility | None = None
    reason_codes: tuple[str, ...] = ()

    @field_validator("namespaces")
    @classmethod
    def unique_namespaces(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _unique(values, "namespaces")

    @field_validator("reason_codes")
    @classmethod
    def unique_reasons(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _unique(values, "reason_codes")

    @model_validator(mode="after")
    def availability_matches_evidence(self) -> AgentChecksObservation:
        observed = self.availability != "unavailable"
        has_payload = (
            self.observed_at is not None
            and self.findings is not None
            and self.catalog is not None
            and self.visibility is not None
        )
        if observed != has_payload:
            raise ValueError("observed checks availability requires one complete evidence payload")
        if self.observed_at is not None and self.observed_at.utcoffset() is None:
            raise ValueError("observed checks timestamps must include a UTC offset")
        if self.availability == "available" and self.reason_codes:
            raise ValueError("available checks observation cannot carry incomplete reasons")
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("incomplete checks observation requires a reason")
        if self.findings is not None:
            identities = tuple(finding.finding_id for finding in self.findings)
            if len(identities) != len(set(identities)):
                raise ValueError("checks finding identities must be unique")
        if self.catalog is not None:
            identities = tuple(entry.check_id for entry in self.catalog)
            if len(identities) != len(set(identities)):
                raise ValueError("checks catalog identities must be unique")
            catalog_ids = set(identities)
            if any(finding.check_id not in catalog_ids for finding in self.findings or ()):
                raise ValueError("every checks finding must reference the reported catalog")
        if self.visibility is not None and self.visibility.namespace_scope != self.namespaces:
            raise ValueError("checks visibility and evaluation namespaces must match")
        if self.namespaces and any(
            finding.resource.namespace not in self.namespaces for finding in self.findings or ()
        ):
            raise ValueError("namespace-limited findings must stay inside the observed scope")
        return self


class ChecksObservedResultSet(StrictModel):
    availability: Literal["available", "partial"]
    evaluated_at: str = Field(min_length=1)
    checks: tuple[ChecksFinding, ...]
    total_check_count: int = Field(ge=0)
    total_finding_count: int = Field(ge=0)
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def counts_and_reasons_match_evidence(self) -> ChecksObservedResultSet:
        if self.total_finding_count != len(self.checks):
            raise ValueError("checks finding count must match the observed findings")
        if self.availability == "available" and self.reason_codes:
            raise ValueError("available checks results cannot carry incomplete reasons")
        if self.availability == "partial" and not self.reason_codes:
            raise ValueError("partial checks results require a reason")
        return self


class ChecksUnavailableResultSet(StrictModel):
    availability: Literal["unavailable"] = "unavailable"
    evaluated_at: None = None
    checks: None = None
    total_check_count: None = None
    total_finding_count: None = None
    reason_codes: tuple[str, ...] = Field(min_length=1)


ChecksResultSet = ChecksObservedResultSet | ChecksUnavailableResultSet


class ChecksObservedCatalog(StrictModel):
    availability: Literal["available", "partial"]
    entries: tuple[ChecksCatalogEntry, ...]
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def availability_matches_reasons(self) -> ChecksObservedCatalog:
        if self.availability == "available" and self.reason_codes:
            raise ValueError("available checks catalog cannot carry incomplete reasons")
        if self.availability == "partial" and not self.reason_codes:
            raise ValueError("partial checks catalog requires a reason")
        identities = tuple(entry.check_id for entry in self.entries)
        if len(identities) != len(set(identities)):
            raise ValueError("checks catalog identities must be unique")
        return self


class ChecksUnavailableCatalog(StrictModel):
    availability: Literal["unavailable"] = "unavailable"
    entries: None = None
    reason_codes: tuple[str, ...] = Field(min_length=1)


ChecksCatalog = ChecksObservedCatalog | ChecksUnavailableCatalog


class ChecksVisibilitySummary(StrictModel):
    availability: ChecksAvailability
    clusters: tuple[ChecksVisibility, ...] = ()
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def availability_matches_reasons(self) -> ChecksVisibilitySummary:
        if self.availability == "available" and self.reason_codes:
            raise ValueError("available checks visibility cannot carry incomplete reasons")
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("incomplete checks visibility requires a reason")
        identities = tuple(item.cluster_id for item in self.clusters)
        if len(identities) != len(set(identities)):
            raise ValueError("checks visibility clusters must be unique")
        return self


class ChecksOverviewResponse(StrictModel):
    scope_coverage: ChecksScopeCoverage
    result_set: ChecksResultSet
    catalog: ChecksCatalog
    visibility: ChecksVisibilitySummary


class ChecksObservedDetail(StrictModel):
    requested_check_id: str = Field(min_length=1, max_length=253)
    availability: Literal["available", "partial"]
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    effective_severity: ChecksSeverity
    message: str = Field(min_length=1)
    remediation: str = Field(min_length=1)
    affected_resource_count: int = Field(ge=0)
    findings: tuple[ChecksFinding, ...]
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def evidence_is_consistent(self) -> ChecksObservedDetail:
        if self.affected_resource_count != len(self.findings):
            raise ValueError("affected resource count must match findings")
        if self.availability == "available" and self.reason_codes:
            raise ValueError("available checks detail cannot carry incomplete reasons")
        if self.availability == "partial" and not self.reason_codes:
            raise ValueError("partial checks detail requires a reason")
        return self


class ChecksUnavailableDetail(StrictModel):
    requested_check_id: str = Field(min_length=1, max_length=253)
    availability: Literal["unavailable"] = "unavailable"
    title: None = None
    category: None = None
    effective_severity: None = None
    message: None = None
    remediation: None = None
    affected_resource_count: None = None
    findings: None = None
    reason_codes: tuple[str, ...] = Field(min_length=1)


ChecksDetail = ChecksObservedDetail | ChecksUnavailableDetail


class ChecksDetailResponse(StrictModel):
    scope_coverage: ChecksScopeCoverage
    detail: ChecksDetail
