"""Pure reverse index over one complete cluster-agent RBAC observation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from packages.contracts.kubernetes_discovery import ApiResourceDiscoveryObservation
from packages.contracts.resource_access import (
    KubernetesBindingRef,
    KubernetesBindingRules,
    KubernetesBindingWithSubjects,
    KubernetesExecutionAccessResponse,
    KubernetesInheritedGroup,
    KubernetesNamespaceAccessResponse,
    KubernetesPodRef,
    KubernetesPolicyRule,
    KubernetesRestrictedResourceType,
    KubernetesRoleAccessResponse,
    KubernetesRoleRef,
    KubernetesSubject,
    KubernetesSubjectAccessResponse,
    ResourceAccessDetail,
)

MAX_FLAT_RULES = 500
IMPLICIT_SERVICE_ACCOUNT_GROUPS = (
    "system:authenticated",
    "system:serviceaccounts",
)


class ResourceAccessUnavailable(RuntimeError):
    """The retained RBAC cut is absent, partial, or invalid."""


@dataclass(frozen=True)
class _Binding:
    ref: KubernetesBindingRef
    subjects: tuple[KubernetesSubject, ...]


class _Index:
    def __init__(self, snapshot: Mapping[str, object]) -> None:
        if snapshot.get("completeness") != "exact":
            raise ResourceAccessUnavailable("Kubernetes RBAC observation is incomplete")
        observed_at = snapshot.get("observed_at")
        if not isinstance(observed_at, str) or not observed_at:
            raise ResourceAccessUnavailable("Kubernetes RBAC observation has no timestamp")
        self.observed_at = observed_at
        try:
            self.roles = _roles(snapshot)
            self.bindings = _bindings(snapshot)
            self.service_accounts = _identities(snapshot.get("service_accounts"))
            self.pod_subjects = _pod_subjects(snapshot.get("pod_subjects"))
        except ValueError as exc:
            raise ResourceAccessUnavailable("Kubernetes RBAC observation is invalid") from exc
        self.bindings_by_subject: dict[tuple[str, str, str], list[_Binding]] = defaultdict(list)
        self.bindings_by_role: dict[tuple[str, str, str], list[_Binding]] = defaultdict(list)
        for binding in self.bindings:
            role = binding.ref.role
            self.bindings_by_role[(role.kind, role.namespace, role.name)].append(binding)
            for subject in binding.subjects:
                self.bindings_by_subject[(subject.kind, subject.namespace, subject.name)].append(
                    binding
                )

    def expanded(self, binding: _Binding) -> KubernetesBindingRules:
        role = binding.ref.role
        rules = self.roles.get((role.kind, role.namespace, role.name), ())
        return KubernetesBindingRules(
            binding=binding.ref,
            role=role,
            rules=rules,
            scope_namespace=(
                binding.ref.namespace
                if binding.ref.kind == "RoleBinding" and role.kind == "ClusterRole"
                else ""
            ),
        )


def subject_access_projection(
    snapshot: Mapping[str, object],
    *,
    kind: str,
    namespace: str | None,
    name: str,
) -> KubernetesSubjectAccessResponse:
    index = _Index(snapshot)
    subject = _subject(kind, namespace, name)
    return _subject_access_from_index(index, subject)


def _subject_access_from_index(
    index: _Index,
    subject: KubernetesSubject,
) -> KubernetesSubjectAccessResponse:
    direct_bindings = list(
        index.bindings_by_subject.get((subject.kind, subject.namespace, subject.name), ())
    )
    direct = tuple(index.expanded(binding) for binding in _sorted_bindings(direct_bindings))
    inherited: list[KubernetesInheritedGroup] = []
    inherited_rules: list[KubernetesBindingRules] = []
    if subject.kind == "ServiceAccount":
        groups = (*IMPLICIT_SERVICE_ACCOUNT_GROUPS, f"system:serviceaccounts:{subject.namespace}")
        for group_name in groups:
            bindings = _sorted_bindings(
                index.bindings_by_subject.get(("Group", "", group_name), ())
            )
            if not bindings:
                continue
            expanded = tuple(index.expanded(binding) for binding in bindings)
            inherited.append(KubernetesInheritedGroup(group_name=group_name, bindings=expanded))
            inherited_rules.extend(expanded)
    flat, truncated = _flatten_rules((*direct, *inherited_rules))
    used_by_pods = tuple(
        KubernetesPodRef(namespace=pod_namespace, name=pod_name)
        for _pod_uid, pod_namespace, pod_name, service_account_name in index.pod_subjects
        if subject.kind == "ServiceAccount"
        and pod_namespace == subject.namespace
        and service_account_name == subject.name
    )
    return KubernetesSubjectAccessResponse(
        observed_at=index.observed_at,
        subject=subject,
        direct=direct,
        inherited_from_groups=tuple(inherited),
        flat=flat,
        truncated=truncated,
        used_by_pods=used_by_pods,
    )


def role_access_projection(
    snapshot: Mapping[str, object],
    *,
    kind: str,
    namespace: str | None,
    name: str,
) -> KubernetesRoleAccessResponse:
    index = _Index(snapshot)
    role = _role(kind, namespace, name)
    bindings = tuple(
        KubernetesBindingWithSubjects(binding=binding.ref, subjects=binding.subjects)
        for binding in _sorted_bindings(
            index.bindings_by_role.get((role.kind, role.namespace, role.name), ())
        )
    )
    return KubernetesRoleAccessResponse(
        observed_at=index.observed_at,
        role=role,
        bindings=bindings,
    )


def namespace_access_projection(
    snapshot: Mapping[str, object],
    *,
    namespace: str,
) -> KubernetesNamespaceAccessResponse:
    index = _Index(snapshot)
    if not namespace:
        raise ValueError("namespace is required")
    role_bindings = tuple(
        _binding_with_subjects(binding)
        for binding in _sorted_bindings(
            binding
            for binding in index.bindings
            if binding.ref.kind == "RoleBinding" and binding.ref.namespace == namespace
        )
    )
    cluster_bindings = tuple(
        _binding_with_subjects(binding)
        for binding in _sorted_bindings(
            binding
            for binding in index.bindings
            if binding.ref.kind == "ClusterRoleBinding"
            and any(
                subject.kind == "ServiceAccount" and subject.namespace == namespace
                for subject in binding.subjects
            )
        )
    )
    return KubernetesNamespaceAccessResponse(
        observed_at=index.observed_at,
        namespace=namespace,
        role_bindings=role_bindings,
        cluster_role_bindings_with_local_subject=cluster_bindings,
        service_account_count=sum(1 for item in index.service_accounts if item[0] == namespace),
    )


def resource_access_projection(
    snapshot: Mapping[str, object],
    resource: Mapping[str, object],
) -> ResourceAccessDetail | None:
    kind = str(resource.get("kind") or "")
    namespace_value = resource.get("namespace")
    namespace = str(namespace_value) if namespace_value is not None else None
    name = str(resource.get("name") or "")
    if kind in {"ServiceAccount", "User", "Group"}:
        return subject_access_projection(
            snapshot,
            kind=kind,
            namespace=namespace,
            name=name,
        )
    if kind == "Pod":
        resource_uid = resource.get("uid")
        if not isinstance(resource_uid, str) or not resource_uid or not namespace or not name:
            raise ResourceAccessUnavailable("Pod access identity is incomplete")
        index = _Index(snapshot)
        access_subject = next(
            (
                (pod_uid, service_account_name)
                for pod_uid, pod_namespace, pod_name, service_account_name in index.pod_subjects
                if pod_namespace == namespace and pod_name == name
            ),
            None,
        )
        if access_subject is None or access_subject[0] != resource_uid:
            raise ResourceAccessUnavailable("Pod access identity does not match inventory")
        return _subject_access_from_index(
            index,
            _subject("ServiceAccount", namespace, access_subject[1]),
        )
    if kind in {"Role", "ClusterRole"}:
        return role_access_projection(
            snapshot,
            kind=kind,
            namespace=namespace,
            name=name,
        )
    if kind == "Namespace":
        return namespace_access_projection(snapshot, namespace=name)
    return None


def resource_supports_access_projection(resource: Mapping[str, object]) -> bool:
    kind = str(resource.get("kind") or "")
    if kind in {"ServiceAccount", "User", "Group", "Role", "ClusterRole", "Namespace"}:
        return True
    if kind != "Pod":
        return False
    summary = resource.get("summary")
    return isinstance(summary, Mapping) and bool(summary.get("service_account_name"))


def access_snapshot_from_inventory(snapshot: Mapping[str, object] | None) -> Mapping[str, object]:
    envelope = snapshot.get("summary") if isinstance(snapshot, Mapping) else None
    source = envelope.get("summary") if isinstance(envelope, Mapping) else None
    access = source.get("resource_access") if isinstance(source, Mapping) else None
    if not isinstance(access, Mapping):
        raise ResourceAccessUnavailable("Kubernetes RBAC observation is unavailable")
    return access


def agent_execution_access_projection(
    inventory_snapshot: Mapping[str, object],
    *,
    namespace: str,
) -> KubernetesExecutionAccessResponse:
    """Project the cluster-agent ServiceAccount without impersonating the product user.

    The management server consumes only the retained inventory cut. Kubernetes API
    discovery and RBAC collection stay inside the existing agent telemetry registry.
    """

    if not namespace:
        raise ValueError("namespace is required")
    envelope = inventory_snapshot.get("summary")
    source = envelope.get("summary") if isinstance(envelope, Mapping) else None
    access = source.get("resource_access") if isinstance(source, Mapping) else None
    discovery = source.get("api_resource_discovery") if isinstance(source, Mapping) else None
    if not isinstance(access, Mapping):
        raise ResourceAccessUnavailable("agent RBAC observation is unavailable")
    if not isinstance(discovery, Mapping):
        raise ResourceAccessUnavailable("agent discovery observation is unavailable")

    index = _Index(access)
    subject = _agent_execution_subject(index, inventory_snapshot.get("agent_id"))
    bindings = _subject_bindings(index, subject)
    applicable = tuple(
        index.expanded(binding)
        for binding in bindings
        if binding.ref.kind == "ClusterRoleBinding" or binding.ref.namespace == namespace
    )
    cluster_wide = tuple(
        index.expanded(binding) for binding in bindings if binding.ref.kind == "ClusterRoleBinding"
    )
    visible_rules, truncated = _flatten_rules(applicable)
    resource_rules = tuple(rule for rule in visible_rules if rule.resources)
    non_resource_rules = tuple(rule for rule in visible_rules if rule.non_resource_urls)

    try:
        catalog = ApiResourceDiscoveryObservation.model_validate(discovery)
    except ValueError as exc:
        raise ResourceAccessUnavailable("agent discovery observation is invalid") from exc
    if catalog.completeness == "unavailable":
        raise ResourceAccessUnavailable("agent discovery observation is unavailable")

    all_applicable_rules = tuple(rule for binding in applicable for rule in binding.rules)
    cluster_rules = tuple(rule for binding in cluster_wide for rule in binding.rules)
    restricted = tuple(
        KubernetesRestrictedResourceType(
            api_group=resource.group,
            version=resource.version,
            resource=resource.name,
            kind=resource.kind,
            namespaced=resource.namespaced,
        )
        for resource in catalog.resources
        if not _rules_allow_list(
            all_applicable_rules if resource.namespaced else cluster_rules,
            api_group=resource.group,
            resource=resource.name,
        )
    )
    return KubernetesExecutionAccessResponse(
        observed_at=index.observed_at,
        namespace=namespace,
        subject=subject,
        resource_rules=resource_rules,
        non_resource_rules=non_resource_rules,
        restricted_resource_types=restricted,
        completeness=catalog.completeness,
        reason_codes=tuple(catalog.reason_codes),
        truncated=truncated,
    )


def _agent_execution_subject(index: _Index, agent_id: object) -> KubernetesSubject:
    pod_name = str(agent_id or "")
    candidates = {
        (pod_namespace, service_account_name)
        for _uid, pod_namespace, observed_pod_name, service_account_name in index.pod_subjects
        if observed_pod_name == pod_name
    }
    if len(candidates) != 1:
        raise ResourceAccessUnavailable("agent execution subject is unavailable")
    subject_namespace, subject_name = next(iter(candidates))
    return _subject("ServiceAccount", subject_namespace, subject_name)


def _subject_bindings(index: _Index, subject: KubernetesSubject) -> tuple[_Binding, ...]:
    bindings = list(
        index.bindings_by_subject.get((subject.kind, subject.namespace, subject.name), ())
    )
    if subject.kind == "ServiceAccount":
        for group_name in (
            *IMPLICIT_SERVICE_ACCOUNT_GROUPS,
            f"system:serviceaccounts:{subject.namespace}",
        ):
            bindings.extend(index.bindings_by_subject.get(("Group", "", group_name), ()))
    unique = {
        (binding.ref.kind, binding.ref.namespace, binding.ref.name): binding for binding in bindings
    }
    return _sorted_bindings(unique.values())


def _rules_allow_list(
    rules: tuple[KubernetesPolicyRule, ...],
    *,
    api_group: str,
    resource: str,
) -> bool:
    return any(
        ("list" in rule.verbs or "*" in rule.verbs)
        and (api_group in rule.api_groups or "*" in rule.api_groups)
        and (resource in rule.resources or "*" in rule.resources)
        for rule in rules
    )


def _roles(
    snapshot: Mapping[str, object],
) -> dict[tuple[str, str, str], tuple[KubernetesPolicyRule, ...]]:
    result: dict[tuple[str, str, str], tuple[KubernetesPolicyRule, ...]] = {}
    for key, kind in (("roles", "Role"), ("cluster_roles", "ClusterRole")):
        values = snapshot.get(key)
        if not isinstance(values, list):
            raise ResourceAccessUnavailable(f"Kubernetes RBAC observation has no {key}")
        for raw in values:
            if not isinstance(raw, Mapping):
                raise ResourceAccessUnavailable("Kubernetes RBAC role is invalid")
            role = _role(kind, raw.get("namespace"), str(raw.get("name") or ""))
            rules = raw.get("rules")
            if not isinstance(rules, list):
                raise ResourceAccessUnavailable("Kubernetes RBAC role rules are invalid")
            result[(role.kind, role.namespace, role.name)] = tuple(
                _policy_rule(rule) for rule in rules
            )
    return result


def _bindings(snapshot: Mapping[str, object]) -> tuple[_Binding, ...]:
    result: list[_Binding] = []
    for key, kind in (
        ("role_bindings", "RoleBinding"),
        ("cluster_role_bindings", "ClusterRoleBinding"),
    ):
        values = snapshot.get(key)
        if not isinstance(values, list):
            raise ResourceAccessUnavailable(f"Kubernetes RBAC observation has no {key}")
        for raw in values:
            if not isinstance(raw, Mapping):
                raise ResourceAccessUnavailable("Kubernetes RBAC binding is invalid")
            namespace = str(raw.get("namespace") or "")
            role_raw = raw.get("roleRef")
            if not isinstance(role_raw, Mapping):
                raise ResourceAccessUnavailable("Kubernetes RBAC binding role is invalid")
            role_kind = str(role_raw.get("kind") or "")
            role = _role(
                role_kind,
                namespace if role_kind == "Role" else None,
                str(role_raw.get("name") or ""),
            )
            subjects_raw = raw.get("subjects")
            if not isinstance(subjects_raw, list):
                raise ResourceAccessUnavailable("Kubernetes RBAC binding subjects are invalid")
            subjects = tuple(
                _subject(
                    str(subject.get("kind") or ""),
                    subject.get("namespace"),
                    str(subject.get("name") or ""),
                )
                for subject in subjects_raw
                if isinstance(subject, Mapping)
            )
            if len(subjects) != len(subjects_raw):
                raise ResourceAccessUnavailable("Kubernetes RBAC binding subject is invalid")
            try:
                ref = KubernetesBindingRef(
                    kind=kind,
                    namespace=namespace,
                    name=str(raw.get("name") or ""),
                    role=role,
                )
            except ValueError as exc:
                raise ResourceAccessUnavailable(
                    "Kubernetes RBAC binding identity is invalid"
                ) from exc
            result.append(_Binding(ref=ref, subjects=subjects))
    return tuple(result)


def _subject(kind: str, namespace: object, name: str) -> KubernetesSubject:
    subject_namespace = str(namespace or "")
    if kind == "ServiceAccount" and not subject_namespace:
        raise ValueError("ServiceAccount namespace is required")
    if kind in {"User", "Group"} and subject_namespace:
        raise ValueError(f"{kind} namespace must be empty")
    return KubernetesSubject(kind=kind, namespace=subject_namespace, name=name)


def _role(kind: str, namespace: object, name: str) -> KubernetesRoleRef:
    role_namespace = str(namespace or "")
    if kind == "Role" and not role_namespace:
        raise ValueError("Role namespace is required")
    if kind == "ClusterRole" and role_namespace:
        raise ValueError("ClusterRole namespace must be empty")
    return KubernetesRoleRef(kind=kind, namespace=role_namespace, name=name)


def _policy_rule(value: object) -> KubernetesPolicyRule:
    if not isinstance(value, Mapping):
        raise ResourceAccessUnavailable("Kubernetes RBAC policy rule is invalid")
    return KubernetesPolicyRule(
        verbs=_strings(value.get("verbs")),
        api_groups=_strings(value.get("apiGroups")),
        resources=_strings(value.get("resources")),
        resource_names=_strings(value.get("resourceNames")),
        non_resource_urls=_strings(value.get("nonResourceURLs")),
    )


def _strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ResourceAccessUnavailable("Kubernetes RBAC string collection is invalid")
    return tuple(value)


def _identities(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise ResourceAccessUnavailable("Kubernetes ServiceAccount observation is invalid")
    rows: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ResourceAccessUnavailable("Kubernetes ServiceAccount identity is invalid")
        namespace = str(item.get("namespace") or "")
        name = str(item.get("name") or "")
        if not namespace or not name:
            raise ResourceAccessUnavailable("Kubernetes ServiceAccount identity is incomplete")
        rows.append((namespace, name))
    return tuple(sorted(set(rows)))


def _pod_subjects(value: object) -> tuple[tuple[str, str, str, str], ...]:
    if not isinstance(value, list):
        raise ResourceAccessUnavailable("Kubernetes Pod subject observation is invalid")
    rows: list[tuple[str, str, str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ResourceAccessUnavailable("Kubernetes Pod subject identity is invalid")
        row = (
            str(item.get("uid") or ""),
            str(item.get("namespace") or ""),
            str(item.get("name") or ""),
            str(item.get("service_account_name") or ""),
        )
        if not all(row):
            raise ResourceAccessUnavailable("Kubernetes Pod subject identity is incomplete")
        rows.append(row)
    return tuple(sorted(set(rows)))


def _sorted_bindings(bindings: Any) -> tuple[_Binding, ...]:
    kind_order = {"RoleBinding": 0, "ClusterRoleBinding": 1}
    return tuple(
        sorted(
            bindings,
            key=lambda item: (
                kind_order[item.ref.kind],
                item.ref.namespace,
                item.ref.name,
            ),
        )
    )


def _binding_with_subjects(binding: _Binding) -> KubernetesBindingWithSubjects:
    return KubernetesBindingWithSubjects(binding=binding.ref, subjects=binding.subjects)


def _flatten_rules(
    bindings: tuple[KubernetesBindingRules, ...],
) -> tuple[tuple[KubernetesPolicyRule, ...], bool]:
    rules: list[KubernetesPolicyRule] = []
    signatures: set[tuple[tuple[str, ...], ...]] = set()
    for binding in bindings:
        for rule in binding.rules:
            signature = (
                tuple(sorted(rule.verbs)),
                tuple(sorted(rule.api_groups)),
                tuple(sorted(rule.resources)),
                tuple(sorted(rule.resource_names)),
                tuple(sorted(rule.non_resource_urls)),
            )
            if signature in signatures:
                continue
            signatures.add(signature)
            rules.append(rule)
            if len(rules) >= MAX_FLAT_RULES:
                return tuple(rules), True
    return tuple(rules), False
