"""Bounded read-only resource filesystem contracts for the outbound cluster Agent."""

from __future__ import annotations

import base64
import hashlib
import posixpath
from typing import Annotated, Literal, Self

from pydantic import Field, TypeAdapter, field_validator, model_validator

from packages.contracts.modeling import StrictModel
from packages.contracts.parity import ResourceRef

RESOURCE_FILE_ACTION = "resource.files.read"
RESOURCE_FILE_AGENT_CAPABILITY = "resource_files.v1"
RESOURCE_FILES_SOURCE_REVISION = "cf643dfee93a5ae8dfcd3c2a982620b793b2b4cc"

MAX_RESOURCE_FILE_PATH_LENGTH = 1_024
MAX_RESOURCE_FILE_PAGE_SIZE = 100
DEFAULT_RESOURCE_FILE_PAGE_SIZE = 40
MAX_RESOURCE_FILE_LIST_RECORDS = 2_000
MAX_RESOURCE_FILE_LIST_BYTES = 512 * 1_024
MAX_RESOURCE_FILE_CHUNK_BYTES = 64 * 1_024
DEFAULT_RESOURCE_FILE_CHUNK_BYTES = MAX_RESOURCE_FILE_CHUNK_BYTES
MAX_RESOURCE_FILE_TOTAL_BYTES = 512 * 1_024 * 1_024
MAX_RESOURCE_IMAGE_LAYERS = 256
MAX_RESOURCE_IMAGE_FILES = 250_000

ResourceFileCapabilityId = Literal["image.filesystem", "pod.filesystem"]
ResourceFileOperation = Literal[
    "image.metadata",
    "image.list",
    "image.read",
    "pod.list",
    "pod.read",
]
ResourceFileEntryType = Literal["directory", "file", "symlink"]


def normalize_resource_file_path(value: str) -> str:
    """Canonicalize one Linux-container path without resolving host paths."""

    if "\x00" in value or not value.startswith("/"):
        raise ValueError("resource file path must be an absolute POSIX path")
    normalized = posixpath.normpath(value)
    if not normalized.startswith("/") or len(normalized) > MAX_RESOURCE_FILE_PATH_LENGTH:
        raise ValueError("resource file path must be a bounded absolute POSIX path")
    return normalized


class ResourceFileCommandTarget(StrictModel):
    capability_id: ResourceFileCapabilityId
    capability_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    resource_id: str = Field(min_length=1, max_length=255)
    snapshot_id: str = Field(min_length=1, max_length=255)
    resource: ResourceRef
    operation: ResourceFileOperation
    container: str | None = Field(
        default=None,
        min_length=1,
        max_length=253,
        pattern=r"^[A-Za-z0-9](?:[-._A-Za-z0-9]*[A-Za-z0-9])?$",
    )
    artifact_id: str | None = Field(
        default=None,
        pattern=r"^artifact-[0-9a-f]{64}$",
    )
    path: str | None = Field(default=None, max_length=MAX_RESOURCE_FILE_PATH_LENGTH)
    cursor: int | None = Field(default=None, ge=0, le=MAX_RESOURCE_IMAGE_FILES)
    offset: int | None = Field(default=None, ge=0, le=MAX_RESOURCE_FILE_TOTAL_BYTES)
    limit: int | None = Field(default=None, ge=1, le=MAX_RESOURCE_FILE_CHUNK_BYTES)

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str | None) -> str | None:
        return normalize_resource_file_path(value) if value is not None else None

    @model_validator(mode="after")
    def validate_operation_authority(self) -> Self:
        prefix, operation = self.operation.split(".", 1)
        expected_capability = f"{prefix}.filesystem"
        if self.capability_id != expected_capability:
            raise ValueError("resource file capability does not match the requested operation")
        if self.container is None:
            raise ValueError("resource file operations require an exact Pod container")
        if prefix == "pod" and self.artifact_id is not None:
            raise ValueError("Pod operations cannot accept an image artifact handle")
        if operation == "metadata":
            if any(
                value is not None
                for value in (self.artifact_id, self.path, self.cursor, self.offset, self.limit)
            ):
                raise ValueError("image metadata accepts only the exact Pod container target")
            return self
        if operation == "list":
            if self.path is None:
                raise ValueError("directory operations require an absolute POSIX path")
            if prefix == "image" and self.artifact_id is None:
                raise ValueError("image directory operations require an artifact handle")
            if self.offset is not None:
                raise ValueError("directory operations cannot accept a byte offset")
            if self.limit is None or self.limit > MAX_RESOURCE_FILE_PAGE_SIZE:
                raise ValueError("directory page limit exceeds the bounded directory page size")
            return self
        if operation == "read":
            if self.path is None:
                raise ValueError("file operations require an absolute POSIX path")
            if prefix == "image" and self.artifact_id is None:
                raise ValueError("image file operations require an artifact handle")
            if self.cursor is not None:
                raise ValueError("file operations cannot accept a directory cursor")
            if self.offset is None or self.limit is None:
                raise ValueError("file operations require a bounded offset and chunk limit")
            return self
        raise ValueError("unsupported resource file operation")


class ResourceFileCommandRequest(ResourceFileCommandTarget):
    confirmation: Literal[True]
    idempotency_key: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


class ResourceFileCommandPayload(ResourceFileCommandTarget):
    pod_resource_version: str = Field(min_length=1, max_length=253)


class ResourceFileEntry(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    path: str = Field(min_length=1, max_length=MAX_RESOURCE_FILE_PATH_LENGTH)
    type: ResourceFileEntryType
    size: int = Field(default=0, ge=0, le=MAX_RESOURCE_FILE_TOTAL_BYTES)
    permissions: str | None = Field(default=None, max_length=16)
    modified_at: str | None = Field(default=None, max_length=64)
    link_target: str | None = Field(default=None, max_length=MAX_RESOURCE_FILE_PATH_LENGTH)

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        return normalize_resource_file_path(value)


class ResourceImageMetadataResult(StrictModel):
    operation: Literal["image.metadata"] = "image.metadata"
    image: str = Field(min_length=1, max_length=2_048)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    platform: str | None = Field(default=None, max_length=120)
    total_size: int = Field(ge=0, le=MAX_RESOURCE_FILE_TOTAL_BYTES)
    layer_count: int = Field(ge=0, le=MAX_RESOURCE_IMAGE_LAYERS)
    cached: bool
    artifact_id: str | None = Field(default=None, pattern=r"^artifact-[0-9a-f]{64}$")
    auth_method: Literal["anonymous", "pull-secret", "cached"]


class ResourceFileDirectoryResult(StrictModel):
    operation: Literal["image.list", "pod.list"]
    path: str = Field(min_length=1, max_length=MAX_RESOURCE_FILE_PATH_LENGTH)
    entries: tuple[ResourceFileEntry, ...] = Field(max_length=MAX_RESOURCE_FILE_PAGE_SIZE)
    cursor: int = Field(ge=0, le=MAX_RESOURCE_IMAGE_FILES)
    next_cursor: int | None = Field(default=None, ge=1, le=MAX_RESOURCE_IMAGE_FILES)
    total_entries: int = Field(ge=0, le=MAX_RESOURCE_IMAGE_FILES)
    truncated: bool
    artifact_id: str | None = Field(default=None, pattern=r"^artifact-[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        return normalize_resource_file_path(value)

    @model_validator(mode="after")
    def validate_cursor(self) -> Self:
        if self.next_cursor is not None and self.next_cursor <= self.cursor:
            raise ValueError("next directory cursor must advance")
        if self.operation == "image.list" and self.artifact_id is None:
            raise ValueError("image directory result requires an artifact handle")
        if self.operation == "pod.list" and self.artifact_id is not None:
            raise ValueError("Pod directory result cannot expose an artifact handle")
        return self


class ResourceFileReadResult(StrictModel):
    operation: Literal["image.read", "pod.read"]
    path: str = Field(min_length=1, max_length=MAX_RESOURCE_FILE_PATH_LENGTH)
    data_base64: str = Field(max_length=((MAX_RESOURCE_FILE_CHUNK_BYTES + 2) // 3) * 4)
    offset: int = Field(ge=0, le=MAX_RESOURCE_FILE_TOTAL_BYTES)
    next_offset: int = Field(ge=0, le=MAX_RESOURCE_FILE_TOTAL_BYTES)
    eof: bool
    total_size: int | None = Field(default=None, ge=0, le=MAX_RESOURCE_FILE_TOTAL_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_id: str | None = Field(default=None, pattern=r"^artifact-[0-9a-f]{64}$")
    media_type: str = Field(default="application/octet-stream", max_length=120)
    filename: str = Field(min_length=1, max_length=255)

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        return normalize_resource_file_path(value)

    @model_validator(mode="after")
    def validate_chunk(self) -> Self:
        try:
            decoded = base64.b64decode(self.data_base64, validate=True)
        except ValueError as exc:
            raise ValueError("resource file chunk must be valid base64") from exc
        if len(decoded) > MAX_RESOURCE_FILE_CHUNK_BYTES:
            raise ValueError("resource file chunk exceeds the byte limit")
        if hashlib.sha256(decoded).hexdigest() != self.sha256:
            raise ValueError("resource file chunk checksum mismatch")
        if self.next_offset != self.offset + len(decoded):
            raise ValueError("resource file chunk offset is not contiguous")
        if self.operation == "image.read" and self.artifact_id is None:
            raise ValueError("image file result requires an artifact handle")
        if self.operation == "pod.read" and self.artifact_id is not None:
            raise ValueError("Pod file result cannot expose an artifact handle")
        if self.eof and self.total_size is not None and self.next_offset != self.total_size:
            raise ValueError("terminal resource file chunk must end at total size")
        return self

    @classmethod
    def from_bytes(
        cls,
        *,
        operation: Literal["image.read", "pod.read"],
        path: str,
        offset: int,
        content: bytes,
        eof: bool,
        total_size: int | None,
        artifact_id: str | None = None,
        media_type: str = "application/octet-stream",
        filename: str | None = None,
    ) -> ResourceFileReadResult:
        if len(content) > MAX_RESOURCE_FILE_CHUNK_BYTES:
            raise ValueError("resource file chunk exceeds the byte limit")
        return cls(
            operation=operation,
            path=path,
            data_base64=base64.b64encode(content).decode("ascii"),
            offset=offset,
            next_offset=offset + len(content),
            eof=eof,
            total_size=total_size,
            sha256=hashlib.sha256(content).hexdigest(),
            artifact_id=artifact_id,
            media_type=media_type,
            filename=filename or posixpath.basename(path) or "download",
        )


ResourceFileResult = Annotated[
    ResourceImageMetadataResult | ResourceFileDirectoryResult | ResourceFileReadResult,
    Field(discriminator="operation"),
]
ResourceFileResultAdapter: TypeAdapter[ResourceFileResult] = TypeAdapter(ResourceFileResult)


def parse_resource_file_result(value: object) -> ResourceFileResult:
    return ResourceFileResultAdapter.validate_python(value)
