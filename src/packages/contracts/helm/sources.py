"""Workspace-scoped Helm chart source and version-provider contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator

from packages.contracts.gateway.base import StrictModel

HELM_CHART_SOURCE_PAGE_MAX = 100
HELM_CHART_VERSION_PAGE_MAX = 200
HELM_CHART_PROVIDER_MAX_CHARTS = 50_000

HelmChartSourceProvider = Literal["repository", "oci"]
HelmChartSourceStatus = Literal["active", "disabled"]
HelmChartSourceAction = Literal["refresh", "delete"]
HelmChartVersionAvailability = Literal["available", "partial", "unavailable"]


class HelmChartSourceCredentialInput(StrictModel):
    """Write-only credential input; response models never contain this type."""

    kind: Literal["bearer", "basic"]
    token: SecretStr | None = Field(default=None, min_length=1, max_length=16_384)
    username: str | None = Field(default=None, min_length=1, max_length=512)
    password: SecretStr | None = Field(default=None, min_length=1, max_length=16_384)

    @model_validator(mode="after")
    def credential_shape_matches_kind(self) -> HelmChartSourceCredentialInput:
        if self.kind == "bearer":
            if self.token is None or self.username is not None or self.password is not None:
                raise ValueError("bearer credentials require only a token")
        elif self.username is None or self.password is None or self.token is not None:
            raise ValueError("basic credentials require username and password")
        return self


class HelmChartSourceRegisterRequest(StrictModel):
    provider: HelmChartSourceProvider
    name: str = Field(min_length=1, max_length=120)
    reference: str = Field(min_length=1, max_length=2048)
    credential: HelmChartSourceCredentialInput | None = None


class HelmChartSourceDeleteRequest(StrictModel):
    """Optimistic identity copied from an authorized source projection."""

    provider: HelmChartSourceProvider
    name: str = Field(min_length=1, max_length=120)
    reference: str = Field(min_length=1, max_length=2048)

    @field_validator("name", "reference")
    @classmethod
    def strip_identity_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Helm chart source identity must not be blank")
        return normalized


class HelmChartSource(StrictModel):
    """Public source projection; persisted credential references are intentionally absent."""

    source_id: str = Field(min_length=1)
    provider: HelmChartSourceProvider
    name: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    status: HelmChartSourceStatus
    actions: tuple[HelmChartSourceAction, ...] = ()
    credentials_configured: bool
    observed_at: str | None = None


class HelmChartSourcePage(StrictModel):
    items: tuple[HelmChartSource, ...] = ()
    limit: int = Field(ge=1, le=HELM_CHART_SOURCE_PAGE_MAX)
    has_more: bool
    next_cursor: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def pagination_state_is_consistent(self) -> HelmChartSourcePage:
        if self.has_more != bool(self.next_cursor):
            raise ValueError("has_more and next_cursor must be consistent")
        return self


class HelmChartVersion(StrictModel):
    version: str = Field(min_length=1, max_length=256)
    app_version: str | None = Field(default=None, max_length=256)
    deprecated: bool = False


class HelmChartVersionObservation(StrictModel):
    """Bounded result from exactly one repository or OCI provider."""

    source: HelmChartSource
    chart_name: str = Field(min_length=1, max_length=512)
    availability: HelmChartVersionAvailability
    versions: tuple[HelmChartVersion, ...] = Field(
        default=(),
        max_length=HELM_CHART_VERSION_PAGE_MAX,
    )
    observed_at: str | None = None
    truncated: bool = False
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def incomplete_observation_is_explicit(self) -> HelmChartVersionObservation:
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("partial or unavailable Helm version observation requires a reason")
        if self.availability == "unavailable" and self.versions:
            raise ValueError("unavailable Helm version observation cannot contain versions")
        if self.truncated and "helm_chart_versions_truncated" not in self.reason_codes:
            raise ValueError("truncated Helm versions require the truncation reason")
        return self


class HelmChartVersionResolution(StrictModel):
    """Fail-closed source selection result; versions are never combined across sources."""

    availability: HelmChartVersionAvailability
    source: HelmChartSource | None = None
    versions: tuple[HelmChartVersion, ...] = Field(
        default=(),
        max_length=HELM_CHART_VERSION_PAGE_MAX,
    )
    observed_at: str | None = None
    truncated: bool = False
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def resolution_is_consistent(self) -> HelmChartVersionResolution:
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("partial or unavailable Helm version resolution requires a reason")
        if self.availability == "unavailable" and self.versions:
            raise ValueError("unavailable Helm version resolution cannot contain versions")
        if self.truncated and "helm_chart_versions_truncated" not in self.reason_codes:
            raise ValueError("truncated Helm versions require the truncation reason")
        return self


class HelmRepositoryRefreshResult(StrictModel):
    """One real repository index fetch; chart identities remain server-side."""

    source_id: str = Field(min_length=1, max_length=80)
    chart_count: int = Field(ge=0, le=HELM_CHART_PROVIDER_MAX_CHARTS)
    observed_at: str


class HelmRepositoryRefreshAccepted(HelmRepositoryRefreshResult):
    event_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
