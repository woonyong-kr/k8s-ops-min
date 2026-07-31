"""Revision-bound, redacted Helm candidate-values preview contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Self

from pydantic import Field, model_validator

from packages.contracts.helm.artifacts import (
    HELM_ARTIFACT_CONTENT_MAX_BYTES,
    HelmRenderedResourceChange,
    HelmRenderedResourceRef,
)
from packages.contracts.helm.operations import HelmReleaseGuard
from packages.contracts.helm.releases import HelmCandidateValues, HelmReleaseCandidateRequest
from packages.contracts.modeling import StrictModel

HELM_VALUES_PREVIEW_ACTION = "helm.release.values.preview"
HELM_VALUES_PREVIEW_CAPABILITY = "helm_release_values_preview.v1"
HELM_VALUES_PREVIEW_MAX_ACTIVE_PER_CLUSTER = 4


class HelmReleaseValuesPreviewRequest(HelmReleaseCandidateRequest):
    """Browser request. The server resolves every executable chart field."""


class HelmValuesPreviewCommandPayload(HelmCandidateValues):
    """Agent payload bound to the exact server-observed Helm release revision."""

    namespace: str = Field(
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$",
    )
    release_name: str = Field(
        min_length=1,
        max_length=53,
        pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$",
    )
    guard: HelmReleaseGuard

    @model_validator(mode="after")
    def guard_matches_release(self) -> Self:
        self.guard.validate_target(namespace=self.namespace, release_name=self.release_name)
        return self


class HelmValuesPreviewResources(StrictModel):
    """Only redacted resource identities and bounded field changes cross the Agent boundary."""

    added: tuple[HelmRenderedResourceRef, ...] = ()
    removed: tuple[HelmRenderedResourceRef, ...] = ()
    modified: tuple[HelmRenderedResourceChange, ...] = ()
    unchanged: tuple[HelmRenderedResourceRef, ...] = ()
    parse_error_count: int = Field(ge=0)


class HelmValuesPreviewResult(StrictModel):
    namespace: str = Field(min_length=1, max_length=63)
    release_name: str = Field(min_length=1, max_length=53)
    expected_revision: int = Field(ge=1)
    catalog_item_id: str = Field(min_length=1, max_length=120)
    catalog_version: str = Field(min_length=1, max_length=80)
    chart_name: str = Field(min_length=1, max_length=512)
    chart_version: str = Field(min_length=1, max_length=256)
    resources: HelmValuesPreviewResources
    projection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    projection_bytes: int = Field(ge=0, le=HELM_ARTIFACT_CONTENT_MAX_BYTES)
    source_bytes: int = Field(ge=0, le=32 * 1024 * 1024)
    redaction_applied: Literal[True] = True
    truncated: bool = False

    @model_validator(mode="after")
    def projection_metadata_matches(self) -> Self:
        encoded = json.dumps(
            self.resources.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) != self.projection_bytes:
            raise ValueError("Helm values preview byte count does not match projection")
        if hashlib.sha256(encoded).hexdigest() != self.projection_sha256:
            raise ValueError("Helm values preview digest does not match projection")
        return self
