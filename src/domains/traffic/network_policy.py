"""Bounded Kubernetes NetworkPolicy evaluation over exact observed Pod identities."""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping, Sequence
from typing import Any

from packages.contracts.parity import ResourceRef
from packages.contracts.traffic.control import (
    NetworkPolicyEvaluationCoverage,
    NetworkPolicyEvaluationResponse,
    SelectingNetworkPolicy,
)

MAX_NETWORK_POLICY_EVALUATION_BATCH = 50
MAX_NETWORK_POLICIES_PER_EVALUATION = 500


class NetworkPolicyIdentityChanged(ValueError):
    """The browser identity no longer matches the observed Pod cut."""


class NetworkPolicyObservationUnavailable(RuntimeError):
    """The inventory repository cannot supply the required exact resources."""


def require_bounded_evaluation_batch(requests: Sequence[object]) -> None:
    if len(requests) > MAX_NETWORK_POLICY_EVALUATION_BATCH:
        raise ValueError(
            f"NetworkPolicy evaluation batch exceeds {MAX_NETWORK_POLICY_EVALUATION_BATCH}"
        )


def evaluate_network_policy(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    namespace: str,
    pod_name: str,
    pod_uid: str,
    peer_namespace: str,
    peer_pod_name: str,
    peer_pod_uid: str,
    direction: str,
    port: int,
    protocol: str,
) -> NetworkPolicyEvaluationResponse:
    """Evaluate one flow using Kubernetes' union-of-allow-rules semantics."""

    evaluated = _exact_pod(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespace=namespace,
        name=pod_name,
        uid=pod_uid,
    )
    peer = _exact_pod(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespace=peer_namespace,
        name=peer_pod_name,
        uid=peer_pod_uid,
    )
    policies, cilium_policies, truncated = _network_policies(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
    )
    evaluated_labels = _labels(evaluated)
    selecting: list[SelectingNetworkPolicy] = []
    any_allow = False
    incomplete_reasons: set[str] = set()
    for policy in policies:
        if str(policy.get("namespace") or "") != namespace:
            continue
        spec = _mapping(_raw(policy).get("spec"))
        if not _selector_matches(_mapping(spec.get("podSelector")), evaluated_labels):
            continue
        if not _policy_isolates_direction(spec, direction):
            continue
        allowed = _policy_allows(
            db,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            policy_namespace=namespace,
            spec=spec,
            evaluated=evaluated,
            peer=peer,
            direction=direction,
            port=port,
            protocol=protocol,
        )
        any_allow = any_allow or allowed is True
        if allowed is None:
            incomplete_reasons.add("network_policy_namespace_labels_unavailable")
        selecting.append(
            SelectingNetworkPolicy(
                resource=_resource_ref(policy),
                effect=("allow" if allowed is True else "deny" if allowed is False else "unknown"),
                reason=(
                    "a selecting policy rule admits the exact peer and destination port"
                    if allowed is True
                    else (
                        "the selecting policy has no rule admitting the exact peer and port"
                        if allowed is False
                        else "namespace labels required by a selecting policy were not observed"
                    )
                ),
            )
        )
    for policy in cilium_policies:
        if str(policy.get("namespace") or "") != namespace:
            continue
        spec = _mapping(_raw(policy).get("spec"))
        if not _selector_matches(_mapping(spec.get("endpointSelector")), evaluated_labels):
            continue
        incomplete_reasons.add("cilium_network_policy_rule_unsupported")
        selecting.append(
            SelectingNetworkPolicy(
                resource=_resource_ref(policy),
                effect="unknown",
                reason="Cilium policy selects the exact endpoint but rule evaluation is unavailable",
            )
        )
    selecting.sort(key=lambda item: (item.resource.namespace or "", item.resource.name))
    if truncated:
        incomplete_reasons.add("network_policy_limit_reached")
    verdict = (
        "allowed"
        if any_allow or (not selecting and not incomplete_reasons)
        else "indeterminate"
        if incomplete_reasons
        else "denied"
    )
    coverage = NetworkPolicyEvaluationCoverage(
        state="partial" if incomplete_reasons else "complete",
        evaluated_count=len(policies) + len(cilium_policies),
        returned_count=len(selecting),
        reason_codes=tuple(sorted(incomplete_reasons)),
    )
    return NetworkPolicyEvaluationResponse(
        evaluated_pod=_resource_ref(evaluated),
        peer_pod=_resource_ref(peer),
        direction=direction,
        port=port,
        protocol=protocol,
        selecting_policies=tuple(selecting),
        verdict=verdict,
        coverage=coverage,
    )


def _exact_pod(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    namespace: str,
    name: str,
    uid: str,
) -> Mapping[str, Any]:
    reader = getattr(db, "get_inventory_resource_by_api_version", None)
    if not callable(reader):
        raise NetworkPolicyObservationUnavailable("exact Pod reader is unavailable")
    row = reader(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type="pod",
        api_version="v1",
        kind="Pod",
        namespace=namespace,
        name=name,
    )
    if not isinstance(row, Mapping):
        raise NetworkPolicyIdentityChanged("exact Pod was not observed")
    if (
        str(row.get("cluster_id") or "") != cluster_id
        or str(row.get("namespace") or "") != namespace
        or str(row.get("name") or "") != name
        or str(row.get("uid") or "") != uid
        or str(row.get("api_version") or "") != "v1"
        or str(row.get("kind") or "").casefold() != "pod"
    ):
        raise NetworkPolicyIdentityChanged("exact Pod identity changed")
    return row


def _network_policies(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], bool]:
    reader = getattr(db, "list_inventory_resources_by_api_version", None)
    if not callable(reader):
        raise NetworkPolicyObservationUnavailable("NetworkPolicy reader is unavailable")
    rows = reader(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type="networkpolicy",
        api_version="networking.k8s.io/v1",
        kind="NetworkPolicy",
        limit=MAX_NETWORK_POLICIES_PER_EVALUATION + 1,
    )
    observed = [row for row in rows if isinstance(row, Mapping)]
    standard = observed[:MAX_NETWORK_POLICIES_PER_EVALUATION]
    if len(observed) > MAX_NETWORK_POLICIES_PER_EVALUATION:
        return standard, [], True
    remaining = MAX_NETWORK_POLICIES_PER_EVALUATION - len(standard)
    cilium_rows = reader(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type="ciliumnetworkpolicy",
        api_version="cilium.io/v2",
        kind="CiliumNetworkPolicy",
        limit=remaining + 1,
    )
    cilium_observed = [row for row in cilium_rows if isinstance(row, Mapping)]
    return standard, cilium_observed[:remaining], len(cilium_observed) > remaining


def _policy_isolates_direction(spec: Mapping[str, Any], direction: str) -> bool:
    raw_types = spec.get("policyTypes")
    if isinstance(raw_types, list) and raw_types:
        policy_types = {str(value).casefold() for value in raw_types}
    else:
        policy_types = {"ingress"}
        if "egress" in spec:
            policy_types.add("egress")
    return direction.casefold() in policy_types


def _policy_allows(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    policy_namespace: str,
    spec: Mapping[str, Any],
    evaluated: Mapping[str, Any],
    peer: Mapping[str, Any],
    direction: str,
    port: int,
    protocol: str,
) -> bool | None:
    rules = spec.get("ingress" if direction == "ingress" else "egress")
    if not isinstance(rules, list):
        return False
    uncertain = False
    for raw_rule in rules:
        rule = _mapping(raw_rule)
        if not _ports_allow(
            rule.get("ports"),
            destination=evaluated if direction == "ingress" else peer,
            port=port,
            protocol=protocol,
        ):
            continue
        peers = rule.get("from" if direction == "ingress" else "to")
        if peers is None:
            return True
        if not isinstance(peers, list):
            continue
        matches = [
            _peer_matches(
                db,
                workspace_id=workspace_id,
                cluster_id=cluster_id,
                policy_namespace=policy_namespace,
                selector=_mapping(raw_peer),
                peer=peer,
            )
            for raw_peer in peers
        ]
        if any(match is True for match in matches):
            return True
        uncertain = uncertain or any(match is None for match in matches)
    return None if uncertain else False


def _peer_matches(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    policy_namespace: str,
    selector: Mapping[str, Any],
    peer: Mapping[str, Any],
) -> bool | None:
    if not selector:
        return True
    ip_block = selector.get("ipBlock")
    if isinstance(ip_block, Mapping):
        return _ip_block_matches(ip_block, _pod_ip(peer))
    peer_namespace = str(peer.get("namespace") or "")
    namespace_selector = selector.get("namespaceSelector")
    if namespace_selector is None:
        if peer_namespace != policy_namespace:
            return False
    else:
        normalized_namespace_selector = _mapping(namespace_selector)
        if normalized_namespace_selector:
            namespace_labels = _namespace_labels(
                db,
                workspace_id=workspace_id,
                cluster_id=cluster_id,
                namespace=peer_namespace,
            )
            if namespace_labels is None:
                return None
            if not _selector_matches(normalized_namespace_selector, namespace_labels):
                return False
    pod_selector = selector.get("podSelector")
    return pod_selector is None or _selector_matches(
        _mapping(pod_selector),
        _labels(peer),
    )


def _namespace_labels(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    namespace: str,
) -> Mapping[str, str] | None:
    reader = getattr(db, "get_inventory_resource_by_api_version", None)
    if not callable(reader):
        return None
    row = reader(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type="namespace",
        api_version="v1",
        kind="Namespace",
        namespace=None,
        name=namespace,
    )
    return _labels(row) if isinstance(row, Mapping) else None


def _selector_matches(selector: Mapping[str, Any], labels: Mapping[str, str]) -> bool:
    match_labels = _mapping(selector.get("matchLabels"))
    if any(labels.get(str(key)) != str(value) for key, value in match_labels.items()):
        return False
    expressions = selector.get("matchExpressions")
    if not isinstance(expressions, list):
        return True
    for raw_expression in expressions:
        expression = _mapping(raw_expression)
        key = str(expression.get("key") or "")
        operator = str(expression.get("operator") or "")
        values = {str(value) for value in expression.get("values", []) if isinstance(value, str)}
        present = key in labels
        if operator == "In" and (not present or labels[key] not in values):
            return False
        if operator == "NotIn" and (not present or labels[key] in values):
            return False
        if operator == "Exists" and not present:
            return False
        if operator == "DoesNotExist" and present:
            return False
        if operator not in {"In", "NotIn", "Exists", "DoesNotExist"}:
            return False
    return True


def _ports_allow(
    value: object,
    *,
    destination: Mapping[str, Any],
    port: int,
    protocol: str,
) -> bool:
    if value is None:
        return True
    if not isinstance(value, list):
        return False
    normalized_protocol = protocol.upper()
    for raw_port in value:
        rule = _mapping(raw_port)
        if str(rule.get("protocol") or "TCP").upper() != normalized_protocol:
            continue
        policy_port = rule.get("port")
        if policy_port is None:
            return True
        if isinstance(policy_port, int) and not isinstance(policy_port, bool):
            end_port = rule.get("endPort")
            last = (
                end_port
                if isinstance(end_port, int) and not isinstance(end_port, bool)
                else policy_port
            )
            if policy_port <= port <= last:
                return True
        elif isinstance(policy_port, str) and port in _named_ports(
            destination,
            policy_port,
            normalized_protocol,
        ):
            return True
    return False


def _named_ports(
    pod: Mapping[str, Any],
    name: str,
    protocol: str,
) -> set[int]:
    spec = _mapping(_raw(pod).get("spec"))
    containers = spec.get("containers")
    if not isinstance(containers, list):
        return set()
    matches: set[int] = set()
    for raw_container in containers:
        ports = _mapping(raw_container).get("ports")
        if not isinstance(ports, list):
            continue
        for raw_port in ports:
            candidate = _mapping(raw_port)
            number = candidate.get("containerPort")
            if (
                candidate.get("name") == name
                and str(candidate.get("protocol") or "TCP").upper() == protocol
                and isinstance(number, int)
                and not isinstance(number, bool)
            ):
                matches.add(number)
    return matches


def _ip_block_matches(value: Mapping[str, Any], pod_ip: str | None) -> bool:
    cidr = value.get("cidr")
    if not isinstance(cidr, str) or not pod_ip:
        return False
    try:
        address = ipaddress.ip_address(pod_ip)
        network = ipaddress.ip_network(cidr, strict=False)
        exceptions = [
            ipaddress.ip_network(item, strict=False)
            for item in value.get("except", [])
            if isinstance(item, str)
        ]
    except ValueError:
        return False
    return address in network and not any(address in exception for exception in exceptions)


def _pod_ip(pod: Mapping[str, Any]) -> str | None:
    value = _mapping(_raw(pod).get("status")).get("podIP")
    return str(value) if isinstance(value, str) and value else None


def _labels(resource: Mapping[str, Any]) -> Mapping[str, str]:
    labels = resource.get("labels")
    if not isinstance(labels, Mapping):
        labels = _mapping(_raw(resource).get("metadata")).get("labels")
    if not isinstance(labels, Mapping):
        return {}
    return {
        str(key): str(value)
        for key, value in labels.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _resource_ref(resource: Mapping[str, Any]) -> ResourceRef:
    api_version = str(resource.get("api_version") or "")
    api_group, separator, version = api_version.rpartition("/")
    if not separator:
        api_group, version = "", api_version
    return ResourceRef(
        api_group=api_group,
        version=version,
        kind=str(resource.get("kind") or ""),
        namespace=(str(resource["namespace"]) if resource.get("namespace") is not None else None),
        name=str(resource.get("name") or ""),
        uid=str(resource.get("uid") or ""),
    )


def _raw(resource: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(resource.get("raw"))


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
