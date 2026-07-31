"""Bounded, read-only ArtifactHub discovery contracts."""

from __future__ import annotations

from pydantic import Field, model_validator

from packages.contracts.gateway.base import StrictModel

ARTIFACTHUB_PAGE_MAX = 60
ARTIFACTHUB_VERSION_MAX = 200


class ArtifactHubRepository(StrictModel):
    name: str = Field(min_length=1, max_length=253)
    url: str = Field(min_length=1, max_length=2048)
    official: bool = False
    verified_publisher: bool = False


class ArtifactHubChart(StrictModel):
    package_id: str = Field(min_length=1, max_length=253)
    name: str = Field(min_length=1, max_length=253)
    version: str = Field(min_length=1, max_length=256)
    app_version: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=4096)
    stars: int = Field(default=0, ge=0)
    deprecated: bool = False
    signed: bool = False
    repository: ArtifactHubRepository


class ArtifactHubSearchPage(StrictModel):
    items: tuple[ArtifactHubChart, ...] = Field(default=(), max_length=ARTIFACTHUB_PAGE_MAX)
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=ARTIFACTHUB_PAGE_MAX)
    has_more: bool
    observed_at: str

    @model_validator(mode="after")
    def pagination_is_consistent(self) -> ArtifactHubSearchPage:
        if self.has_more != (self.offset + len(self.items) < self.total):
            raise ValueError("ArtifactHub pagination state is inconsistent")
        return self


class ArtifactHubChartVersion(StrictModel):
    version: str = Field(min_length=1, max_length=256)
    app_version: str | None = Field(default=None, max_length=256)


class ArtifactHubChartDetail(StrictModel):
    chart: ArtifactHubChart
    readme: str | None = Field(default=None, max_length=262_144)
    available_versions: tuple[ArtifactHubChartVersion, ...] = Field(
        default=(),
        max_length=ARTIFACTHUB_VERSION_MAX,
    )
    versions_truncated: bool = False
    observed_at: str
