"""Typed, bounded contracts for agent-sanitized Helm revision artifacts."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from packages.contracts.modeling import StrictModel

HELM_RELEASE_ARTIFACT_READ_ACTION = "helm.release.artifact.read"
HELM_RELEASE_ARTIFACT_READ_CAPABILITY = "helm_release_artifact_read_v1"
HELM_ARTIFACT_MAX_ACTIVE_PER_CLUSTER = 8
HELM_ARTIFACT_CONTENT_MAX_BYTES = 4 * 1024 * 1024

HelmArtifactKind = Literal[
    "manifest",
    "values",
    "manifest_diff",
    "values_diff",
    "notes_diff",
    "hooks_diff",
    "resources_diff",
]
HelmArtifactFormat = Literal["yaml", "unified_diff", "structured"]


class HelmHookDiffItem(StrictModel):
    api_version: str = Field(max_length=253)
    kind: str = Field(min_length=1, max_length=253)
    name: str = Field(min_length=1, max_length=253)
    namespace: str = Field(max_length=253)
    events: tuple[str, ...] = ()
    weight: int = 0
    delete_policies: tuple[str, ...] = ()
    output_log_policies: tuple[str, ...] = ()
    manifest_changed: bool = False


class HelmHooksDiff(StrictModel):
    revision1: int = Field(ge=1)
    revision2: int = Field(ge=1)
    added: tuple[HelmHookDiffItem, ...] = ()
    removed: tuple[HelmHookDiffItem, ...] = ()
    modified: tuple[HelmHookDiffItem, ...] = ()
    unchanged: tuple[HelmHookDiffItem, ...] = ()
    parse_error_count: int = Field(ge=0)

    @model_validator(mode="after")
    def revisions_differ(self) -> Self:
        if self.revision1 == self.revision2:
            raise ValueError("Helm hook diff revisions must differ")
        return self


class HelmRenderedResourceRef(StrictModel):
    api_version: str = Field(max_length=253)
    kind: str = Field(min_length=1, max_length=253)
    name: str = Field(min_length=1, max_length=253)
    namespace: str = Field(max_length=253)


HelmResourceFieldValue = str | int | bool | None


class HelmResourceFieldChange(StrictModel):
    path: str = Field(min_length=1, max_length=2048)
    old_value: HelmResourceFieldValue
    new_value: HelmResourceFieldValue


class HelmRenderedResourceChange(HelmRenderedResourceRef):
    summary: str = Field(max_length=1024)
    field_count: int = Field(ge=1)
    fields: tuple[HelmResourceFieldChange, ...] = ()

    @model_validator(mode="after")
    def retained_fields_do_not_exceed_total(self) -> Self:
        if len(self.fields) > self.field_count:
            raise ValueError("retained Helm resource fields exceed the total field count")
        return self


class HelmResourcesDiff(StrictModel):
    revision1: int = Field(ge=1)
    revision2: int = Field(ge=1)
    added: tuple[HelmRenderedResourceRef, ...] = ()
    removed: tuple[HelmRenderedResourceRef, ...] = ()
    modified: tuple[HelmRenderedResourceChange, ...] = ()
    unchanged: tuple[HelmRenderedResourceRef, ...] = ()
    parse_error_count: int = Field(ge=0)

    @model_validator(mode="after")
    def revisions_differ(self) -> Self:
        if self.revision1 == self.revision2:
            raise ValueError("Helm resource diff revisions must differ")
        return self


class HelmArtifactReadRequest(StrictModel):
    cluster_id: str = Field(min_length=1, max_length=253)
    artifact: HelmArtifactKind
    revision: int = Field(ge=1)
    comparison_revision: int | None = Field(default=None, ge=1)
    all_values: bool = False

    @model_validator(mode="after")
    def comparison_matches_artifact(self) -> Self:
        is_diff = self.artifact.endswith("_diff")
        if is_diff and self.comparison_revision is None:
            raise ValueError("Helm diff artifacts require a comparison revision")
        if not is_diff and self.comparison_revision is not None:
            raise ValueError("single Helm artifacts cannot carry a comparison revision")
        if self.comparison_revision == self.revision:
            raise ValueError("Helm artifact revisions must be distinct")
        if self.artifact not in {"values", "values_diff"} and self.all_values:
            raise ValueError("all_values is valid only for values artifacts")
        return self


class HelmArtifactCommandPayload(HelmArtifactReadRequest):
    namespace: str = Field(min_length=1, max_length=253)
    release_name: str = Field(min_length=1, max_length=253)

    @field_validator("namespace", "release_name")
    @classmethod
    def safe_cli_identity(cls, value: str) -> str:
        normalized = value.strip()
        if normalized.startswith("-") or any(
            character in normalized for character in ("\0", "\r", "\n", "/")
        ):
            raise ValueError("Helm artifact identity is unsafe")
        return normalized


class HelmArtifactResult(StrictModel):
    artifact: HelmArtifactKind
    format: HelmArtifactFormat
    namespace: str = Field(min_length=1, max_length=253)
    release_name: str = Field(min_length=1, max_length=253)
    revision: int = Field(ge=1)
    comparison_revision: int | None = Field(default=None, ge=1)
    all_values: bool = False
    content: str | None = None
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    content_bytes: int | None = Field(
        default=None,
        ge=0,
        le=HELM_ARTIFACT_CONTENT_MAX_BYTES,
    )
    hooks_diff: HelmHooksDiff | None = None
    resources_diff: HelmResourcesDiff | None = None
    projection_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    projection_bytes: int | None = Field(
        default=None,
        ge=0,
        le=HELM_ARTIFACT_CONTENT_MAX_BYTES,
    )
    source_bytes: int = Field(ge=0)
    redaction_applied: Literal[True] = True
    truncated: bool = False

    @model_validator(mode="after")
    def content_metadata_matches(self) -> Self:
        is_diff = self.artifact.endswith("_diff")
        if is_diff != (self.comparison_revision is not None):
            raise ValueError("Helm artifact comparison metadata is inconsistent")
        if self.all_values and self.artifact not in {"values", "values_diff"}:
            raise ValueError("all_values is valid only for values artifacts")

        structured = self.artifact in {"hooks_diff", "resources_diff"}
        if structured != (self.format == "structured"):
            raise ValueError("Helm artifact format is inconsistent")
        if not structured:
            expected_format = "unified_diff" if is_diff else "yaml"
            if self.format != expected_format:
                raise ValueError("Helm text artifact format is inconsistent")
            if self.content is None or self.content_sha256 is None or self.content_bytes is None:
                raise ValueError("Helm text artifact requires content metadata")
            if self.hooks_diff is not None or self.resources_diff is not None:
                raise ValueError("Helm text artifact cannot contain structured diff data")
            if self.projection_sha256 is not None or self.projection_bytes is not None:
                raise ValueError("Helm text artifact cannot contain projection metadata")
            encoded = self.content.encode("utf-8")
            if len(encoded) != self.content_bytes:
                raise ValueError("Helm artifact byte count does not match content")
            return self

        if (
            self.content is not None
            or self.content_sha256 is not None
            or self.content_bytes is not None
        ):
            raise ValueError("Helm structured artifact cannot contain text content")
        if self.projection_sha256 is None or self.projection_bytes is None:
            raise ValueError("Helm structured artifact requires projection metadata")
        if self.artifact == "hooks_diff":
            if self.hooks_diff is None or self.resources_diff is not None:
                raise ValueError("Helm hook artifact requires only a hook diff")
        elif self.resources_diff is None or self.hooks_diff is not None:
            raise ValueError("Helm resource artifact requires only a resource diff")
        return self
