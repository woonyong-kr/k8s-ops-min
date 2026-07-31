"""Authenticated web-shell state shared by the Python gateway and browser adapters."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from packages.contracts.modeling import StrictModel
from packages.contracts.resource_access import (
    KubernetesPolicyRule,
    KubernetesRestrictedResourceType,
    KubernetesSubject,
)

NamespaceScopeMode = Literal["all", "selected"]
ProductThemeSelection = Literal["system", "light", "dark"]
ProductLocale = Literal["en", "ko"]


class ShellFreshness(StrictModel):
    observed_at: str | None = None
    refresh_after_seconds: int = Field(default=30, ge=1, le=300)
    completeness: Literal["exact", "partial", "unavailable"]
    reason_codes: tuple[str, ...] = ()


class NamespaceScopeResponse(StrictModel):
    workspace_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    actives: tuple[str, ...] = ()
    mode: NamespaceScopeMode
    accessible_namespaces: tuple[str, ...] = ()
    accessible_namespace_count: int = Field(ge=0)
    authoritative: Literal[True] = True
    can_clear_namespace: bool
    cache_scoped: Literal[False] = False
    namespace_rescope: Literal["view_filter_and_stream_invalidation"] = (
        "view_filter_and_stream_invalidation"
    )
    revision: int = Field(ge=0)
    invalidation_generation: int = Field(ge=0)
    freshness: ShellFreshness


class NamespaceScopeUpdateRequest(StrictModel):
    cluster_id: str = Field(min_length=1, max_length=255)
    namespaces: tuple[str, ...] = ()
    expected_revision: int = Field(ge=0)

    @field_validator("namespaces")
    @classmethod
    def canonicalize_namespaces(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        canonical = tuple(sorted({value.strip() for value in values if value.strip()}))
        if len(canonical) > 200:
            raise ValueError("namespace scope exceeds the selection limit")
        if any(len(value) > 253 for value in canonical):
            raise ValueError("namespace name exceeds the supported limit")
        return canonical


class NamespaceScopeUpdateResponse(NamespaceScopeResponse):
    event_id: str = Field(min_length=1)
    audit_event_id: str = Field(min_length=1)


class UiPreferences(StrictModel):
    theme: ProductThemeSelection = "system"
    locale: ProductLocale = "en"


class UiPreferencesResponse(StrictModel):
    workspace_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    preferences: UiPreferences
    revision: int = Field(ge=0)
    updated_at: str | None = None


class UiPreferencesUpdateRequest(StrictModel):
    preferences: UiPreferences
    expected_revision: int = Field(ge=0)


class UiPreferencesUpdateResponse(UiPreferencesResponse):
    event_id: str = Field(min_length=1)
    audit_event_id: str = Field(min_length=1)


PermissionKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"),
]
RevisionHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class SettingsAccessDecision(StrictModel):
    permission: PermissionKey
    category: str = Field(min_length=1)
    allowed: bool


class SettingsUnavailableEvidence(StrictModel):
    status: Literal["unavailable"] = "unavailable"
    reason_code: str = Field(min_length=1)
    detail: str = Field(min_length=1)


class SettingsObservedKubernetesRules(StrictModel):
    status: Literal["observed"] = "observed"
    authority: Literal["cluster_agent_service_account"] = "cluster_agent_service_account"
    namespace: str = Field(min_length=1)
    observed_at: str = Field(min_length=1)
    subject: KubernetesSubject
    resource_rules: tuple[KubernetesPolicyRule, ...] = ()
    non_resource_rules: tuple[KubernetesPolicyRule, ...] = ()
    truncated: bool = False


class SettingsObservedRestrictedResourceTypes(StrictModel):
    status: Literal["observed"] = "observed"
    authority: Literal["cluster_agent_service_account"] = "cluster_agent_service_account"
    namespace: str = Field(min_length=1)
    observed_at: str = Field(min_length=1)
    completeness: Literal["exact", "partial"]
    reason_codes: tuple[str, ...] = ()
    items: tuple[KubernetesRestrictedResourceType, ...] = ()


SettingsKubernetesRulesEvidence = Annotated[
    SettingsObservedKubernetesRules | SettingsUnavailableEvidence,
    Field(discriminator="status"),
]
SettingsRestrictedResourceTypesEvidence = Annotated[
    SettingsObservedRestrictedResourceTypes | SettingsUnavailableEvidence,
    Field(discriminator="status"),
]


class SettingsAccessProfileResponse(StrictModel):
    workspace_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    roles: tuple[str, ...]
    authority: Literal["opsia_rbac"] = "opsia_rbac"
    permissions: tuple[SettingsAccessDecision, ...]
    kubernetes_rules: SettingsKubernetesRulesEvidence
    restricted_resource_types: SettingsRestrictedResourceTypesEvidence
    revision: RevisionHash
