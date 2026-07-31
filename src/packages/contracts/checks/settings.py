"""Authenticated, revisioned presentation policy for Checks observations."""

from __future__ import annotations

import re

from pydantic import Field, field_validator

from packages.contracts.modeling import StrictModel

CHECKS_SETTINGS_SELECTION_LIMIT = 200
_IDENTIFIER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.:-]{0,251}[A-Za-z0-9])?$")
_NAMESPACE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)


class ChecksSettingsPolicy(StrictModel):
    hidden_check_ids: tuple[str, ...] = Field(
        default=(), max_length=CHECKS_SETTINGS_SELECTION_LIMIT
    )
    hidden_categories: tuple[str, ...] = Field(
        default=(), max_length=CHECKS_SETTINGS_SELECTION_LIMIT
    )
    hidden_namespaces: tuple[str, ...] = Field(
        default=(), max_length=CHECKS_SETTINGS_SELECTION_LIMIT
    )

    @field_validator("hidden_check_ids", "hidden_categories")
    @classmethod
    def canonicalize_identifiers(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        canonical = tuple(sorted(set(values)))
        if any(value.strip() != value or not _IDENTIFIER.fullmatch(value) for value in canonical):
            raise ValueError("checks settings contain an invalid identifier")
        return canonical

    @field_validator("hidden_namespaces")
    @classmethod
    def canonicalize_namespaces(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        canonical = tuple(sorted(set(values)))
        for value in canonical:
            cluster_id, separator, namespace = value.partition("/")
            if (
                separator != "/"
                or "/" in namespace
                or len(namespace) > 253
                or not _IDENTIFIER.fullmatch(cluster_id)
                or not _NAMESPACE.fullmatch(namespace)
            ):
                raise ValueError("hidden namespace must be a cluster/name reference")
        return canonical


class ChecksSettingsResponse(StrictModel):
    workspace_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    policy: ChecksSettingsPolicy
    revision: int = Field(ge=0)
    invalidation_generation: int = Field(ge=0)
    can_edit: bool
    updated_at: str | None = None


class ChecksSettingsUpdateRequest(StrictModel):
    policy: ChecksSettingsPolicy
    expected_revision: int = Field(ge=0)


class ChecksSettingsUpdateResponse(ChecksSettingsResponse):
    event_id: str = Field(min_length=1)
    audit_event_id: str = Field(min_length=1)
