"""Bounded Kubernetes RBAC projections shared by gateway and browser contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from packages.contracts.modeling import StrictModel


class KubernetesSubject(StrictModel):
    kind: Literal["ServiceAccount", "User", "Group"]
    namespace: str = ""
    name: str = Field(min_length=1)


class KubernetesRoleRef(StrictModel):
    kind: Literal["Role", "ClusterRole"]
    namespace: str = ""
    name: str = Field(min_length=1)


class KubernetesBindingRef(StrictModel):
    kind: Literal["RoleBinding", "ClusterRoleBinding"]
    namespace: str = ""
    name: str = Field(min_length=1)
    role: KubernetesRoleRef


class KubernetesPolicyRule(StrictModel):
    verbs: tuple[str, ...] = ()
    api_groups: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    resource_names: tuple[str, ...] = ()
    non_resource_urls: tuple[str, ...] = ()


class KubernetesRestrictedResourceType(StrictModel):
    api_group: str = ""
    version: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    namespaced: bool
    reason_code: Literal["list_permission_not_observed"] = "list_permission_not_observed"


class KubernetesExecutionAccessResponse(StrictModel):
    """Namespace-scoped execution authority derived from one agent observation."""

    observed_at: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    subject: KubernetesSubject
    resource_rules: tuple[KubernetesPolicyRule, ...] = ()
    non_resource_rules: tuple[KubernetesPolicyRule, ...] = ()
    restricted_resource_types: tuple[KubernetesRestrictedResourceType, ...] = ()
    completeness: Literal["exact", "partial"]
    reason_codes: tuple[str, ...] = ()
    truncated: bool = False


class KubernetesBindingRules(StrictModel):
    binding: KubernetesBindingRef
    role: KubernetesRoleRef
    rules: tuple[KubernetesPolicyRule, ...] = ()
    scope_namespace: str = ""


class KubernetesInheritedGroup(StrictModel):
    group_name: str = Field(min_length=1)
    bindings: tuple[KubernetesBindingRules, ...] = ()


class KubernetesPodRef(StrictModel):
    namespace: str = Field(min_length=1)
    name: str = Field(min_length=1)


class KubernetesSubjectAccessResponse(StrictModel):
    type: Literal["subject"] = "subject"
    observed_at: str
    subject: KubernetesSubject
    direct: tuple[KubernetesBindingRules, ...] = ()
    inherited_from_groups: tuple[KubernetesInheritedGroup, ...] = ()
    flat: tuple[KubernetesPolicyRule, ...] = ()
    truncated: bool = False
    used_by_pods: tuple[KubernetesPodRef, ...] = ()


class KubernetesBindingWithSubjects(StrictModel):
    binding: KubernetesBindingRef
    subjects: tuple[KubernetesSubject, ...] = ()


class KubernetesRoleAccessResponse(StrictModel):
    type: Literal["role"] = "role"
    observed_at: str
    role: KubernetesRoleRef
    bindings: tuple[KubernetesBindingWithSubjects, ...] = ()


class KubernetesNamespaceAccessResponse(StrictModel):
    type: Literal["namespace"] = "namespace"
    observed_at: str
    namespace: str = Field(min_length=1)
    role_bindings: tuple[KubernetesBindingWithSubjects, ...] = ()
    cluster_role_bindings_with_local_subject: tuple[KubernetesBindingWithSubjects, ...] = ()
    service_account_count: int = Field(ge=0)


class KubernetesAccessUnavailableResponse(StrictModel):
    type: Literal["unavailable"] = "unavailable"
    reason_codes: tuple[str, ...] = Field(min_length=1)


ResourceAccessDetail = Annotated[
    KubernetesSubjectAccessResponse
    | KubernetesRoleAccessResponse
    | KubernetesNamespaceAccessResponse
    | KubernetesAccessUnavailableResponse,
    Field(discriminator="type"),
]
