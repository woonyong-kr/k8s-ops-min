"""Strict, revision-bound Helm release operation contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from packages.contracts.gateway.base import StrictModel
from packages.contracts.parity import ResourceRef

HELM_RELEASE_OPERATION_ACTION = "helm.release.operation"
HELM_RELEASE_OPERATION_CAPABILITY = "helm_release_operation_cas.v1"


class HelmReleaseGuard(StrictModel):
    """Server-observed release identity that the target agent must revalidate."""

    expected_revision: int = Field(ge=1)
    storage: ResourceRef
    storage_resource_version: str = Field(min_length=1, max_length=253)
    chart_name: str = Field(min_length=1, max_length=512)
    chart_version: str = Field(min_length=1, max_length=256)

    def validate_target(self, *, namespace: str, release_name: str) -> None:
        if (
            self.storage.api_group != ""
            or self.storage.version != "v1"
            or self.storage.kind.casefold() != "secret"
            or self.storage.namespace != namespace
            or self.storage.name != f"sh.helm.release.v1.{release_name}.v{self.expected_revision}"
        ):
            raise ValueError("Helm release guard target is invalid")


class HelmReleaseOperationCommandPayload(StrictModel):
    """Agent-only rollback or uninstall payload; browser input never supplies the guard."""

    operation: Literal["rollback", "uninstall"]
    namespace: str = Field(min_length=1, max_length=63, pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    release_name: str = Field(
        min_length=1,
        max_length=53,
        pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$",
    )
    guard: HelmReleaseGuard
    rollback_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def operation_inputs_are_exact(self) -> HelmReleaseOperationCommandPayload:
        self.guard.validate_target(namespace=self.namespace, release_name=self.release_name)
        if self.operation == "rollback":
            if self.rollback_revision is None:
                raise ValueError("rollback revision is required")
            if self.rollback_revision >= self.guard.expected_revision:
                raise ValueError("rollback revision must be older than the observed revision")
        elif self.rollback_revision is not None:
            raise ValueError("rollback revision is only valid for rollback")
        return self


class HelmReleaseRollbackRequest(StrictModel):
    cluster_id: str = Field(min_length=1, max_length=253)
    expected_revision: int = Field(ge=2)
    revision: int = Field(ge=1)
    confirmation: Literal[True]
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def revision_is_older(self) -> HelmReleaseRollbackRequest:
        if self.revision >= self.expected_revision:
            raise ValueError("rollback revision must be older than the observed revision")
        return self


class HelmReleaseUninstallRequest(StrictModel):
    cluster_id: str = Field(min_length=1, max_length=253)
    expected_revision: int = Field(ge=1)
    confirmation: Literal[True]
    reason: str | None = Field(default=None, max_length=500)
