"""Bounded GitOps controller projections from the server-owned inventory read model."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from domains.gitops.overview_projection import gitops_resource_family
from packages.contracts.gitops.detail import (
    GitOpsCondition,
    GitOpsHistoryEntry,
    GitOpsResourceInsights,
    GitOpsResourceTreeResponse,
    GitOpsTreeCoverage,
    GitOpsTreeEdge,
    GitOpsTreeNode,
    GitOpsTreeReasonCode,
)
from packages.contracts.parity import CapabilitySet, ClusterScope, ResourceRef

GITOPS_INVENTORY_QUERY_LIMIT = 1000
GITOPS_TREE_NODE_LIMIT = 500
GITOPS_TREE_EDGE_LIMIT = 1000

ARGO_APPLICATION_IDENTITY = ("argoproj.io/v1alpha1", "application")
FLUX_KUSTOMIZATION_IDENTITY = ("kustomize.toolkit.fluxcd.io/v1", "kustomization")
FLUX_HELM_RELEASE_IDENTITY = ("helm.toolkit.fluxcd.io/v2", "helmrelease")
FLUX_SOURCE_IDENTITIES = frozenset(
    {
        ("source.toolkit.fluxcd.io/v1", "gitrepository"),
        ("source.toolkit.fluxcd.io/v1", "ocirepository"),
        ("source.toolkit.fluxcd.io/v1", "helmrepository"),
        ("source.toolkit.fluxcd.io/v1", "bucket"),
        ("source.toolkit.fluxcd.io/v1", "helmchart"),
    }
)
SUPPORTED_GITOPS_IDENTITIES = frozenset(
    {
        ARGO_APPLICATION_IDENTITY,
        FLUX_KUSTOMIZATION_IDENTITY,
        FLUX_HELM_RELEASE_IDENTITY,
        *FLUX_SOURCE_IDENTITIES,
    }
)


def gitops_resource_tree(
    root: Mapping[str, Any],
    inventory_rows: Iterable[Mapping[str, Any]],
    *,
    snapshot: Mapping[str, Any] | None,
) -> GitOpsResourceTreeResponse:
    """Project one controller tree without additional per-node repository reads."""

    root_ref = inventory_resource_ref(root)
    root_node = _tree_node(root, role="root")
    rows = [row for row in inventory_rows if isinstance(row, Mapping)]
    by_identity = {_identity_key(row): row for row in rows if _has_complete_identity(row)}
    candidates = _declared_candidates(root, rows)
    resolved: list[tuple[Mapping[str, Any], str, str]] = []
    reasons: set[GitOpsTreeReasonCode] = set()
    for identity, role, relationship in candidates:
        row = by_identity.get(identity)
        if row is None:
            reasons.add("unresolved_declared_resource")
            continue
        if not _has_complete_identity(row):
            reasons.add("resource_identity_incomplete")
            continue
        resolved.append((row, role, relationship))

    nodes: list[GitOpsTreeNode] = [root_node]
    edges: list[GitOpsTreeEdge] = []
    seen_uids = {root_ref.uid}
    observed_count = 1 + len(resolved)
    for row, role, relationship in resolved:
        ref = inventory_resource_ref(row)
        if ref.uid in seen_uids:
            continue
        if len(nodes) >= GITOPS_TREE_NODE_LIMIT:
            reasons.add("node_limit_reached")
            continue
        node = _tree_node(row, role=role)
        nodes.append(node)
        seen_uids.add(ref.uid)
        if len(edges) >= GITOPS_TREE_EDGE_LIMIT:
            reasons.add("edge_limit_reached")
            continue
        if relationship == "depends_on":
            edges.append(
                GitOpsTreeEdge(
                    source=root_node.id,
                    target=node.id,
                    relationship="depends_on",
                )
            )
        else:
            edges.append(
                GitOpsTreeEdge(
                    source=node.id if relationship == "source" else root_node.id,
                    target=root_node.id if relationship == "source" else node.id,
                    relationship=relationship,
                )
            )

    snapshot_id = str((snapshot or {}).get("snapshot_id") or "")
    if not snapshot_id or snapshot_id != str(root.get("snapshot_id") or ""):
        reasons.add("snapshot_mismatch")
    summary_envelope = (snapshot or {}).get("summary")
    summary = summary_envelope.get("summary") if isinstance(summary_envelope, Mapping) else None
    if not isinstance(summary, Mapping) or summary.get("resources_complete") is not True:
        reasons.add("snapshot_incomplete")
    if len(rows) >= GITOPS_INVENTORY_QUERY_LIMIT:
        reasons.add("inventory_query_limit")

    scope = _cluster_scope(root, freshness="partial" if reasons else "live")
    return GitOpsResourceTreeResponse(
        scope=scope,
        root=root_ref,
        nodes=tuple(nodes),
        edges=tuple(edges),
        coverage=GitOpsTreeCoverage(
            state="partial" if reasons else "complete",
            reason_codes=tuple(sorted(reasons)),
            observed_count=observed_count,
            returned_count=len(nodes),
        ),
    )


def gitops_resource_insights(
    root: Mapping[str, Any],
    inventory_rows: Iterable[Mapping[str, Any]],
    *,
    writable: bool,
    agent_available: bool,
) -> GitOpsResourceInsights:
    """Return safe controller facts plus a revision-bound common CapabilitySet."""

    ref = inventory_resource_ref(root)
    raw = _mapping(root.get("raw"))
    status = _mapping(raw.get("status"))
    provider = provider_for_inventory_resource(root)
    rows = [row for row in inventory_rows if isinstance(row, Mapping)]
    source_row = _source_row(root, rows)
    source_ref = inventory_resource_ref(source_row) if source_row is not None else None
    actions = _available_actions(root) if writable and agent_available else ()
    revision = _observed_revision(root)
    conditions = _conditions(status)
    resource_version = str(root.get("resource_version") or "").strip()
    if not resource_version:
        raise ValueError("GitOps resource requires an observed resourceVersion")
    capability_revision = _capability_revision(
        root=root,
        actions=actions,
        source=source_row,
    )
    scope = _cluster_scope(root, freshness="live" if agent_available else "disconnected")
    return GitOpsResourceInsights(
        scope=scope,
        resource=ref,
        resource_version=resource_version,
        provider=provider,
        status=_nullable_string(root.get("status")),
        health=_nullable_string(root.get("health")),
        revision=revision,
        source=source_ref,
        conditions=conditions,
        history=_history(status),
        capabilities=CapabilitySet(
            scope=scope,
            resource=ref,
            revision=capability_revision,
            actions=actions,
        ),
    )


def provider_for_inventory_resource(resource: Mapping[str, Any]) -> str:
    family = gitops_resource_family(
        str(resource.get("api_version") or ""),
        str(resource.get("kind") or ""),
    )
    if family is not None:
        return family[0]
    raise ValueError("unsupported GitOps resource identity")


def inventory_resource_ref(resource: Mapping[str, Any]) -> ResourceRef:
    api_version = str(resource.get("api_version") or "").strip()
    if "/" in api_version:
        api_group, version = api_version.split("/", 1)
    else:
        api_group, version = "", api_version
    uid = str(resource.get("uid") or "").strip()
    if not api_version or not uid:
        raise ValueError("GitOps inventory resource identity is incomplete")
    return ResourceRef(
        api_group=api_group,
        version=version,
        kind=str(resource.get("kind") or "").strip(),
        namespace=_nullable_string(resource.get("namespace")),
        name=str(resource.get("name") or "").strip(),
        uid=uid,
    )


def gitops_capability_revision(
    root: Mapping[str, Any],
    inventory_rows: Iterable[Mapping[str, Any]],
    *,
    writable: bool,
    agent_available: bool,
) -> str:
    rows = [row for row in inventory_rows if isinstance(row, Mapping)]
    return _capability_revision(
        root=root,
        actions=_available_actions(root) if writable and agent_available else (),
        source=_source_row(root, rows),
    )


def gitops_source_observation(
    root: Mapping[str, Any], inventory_rows: Iterable[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    return _source_row(root, [row for row in inventory_rows if isinstance(row, Mapping)])


def _available_actions(root: Mapping[str, Any]) -> tuple[str, ...]:
    identity = _api_kind(root)
    raw = _mapping(root.get("raw"))
    metadata = _mapping(raw.get("metadata"))
    annotations = _mapping(metadata.get("annotations"))
    spec = _mapping(raw.get("spec"))
    if identity == ARGO_APPLICATION_IDENTITY:
        actions = ["refresh", "sync"]
        automated = _mapping(_mapping(spec.get("syncPolicy")).get("automated"))
        suspended = "opsia.io/gitops-suspended-prune" in annotations
        if suspended:
            actions.append("resume")
        elif automated:
            actions.append("suspend")
        return tuple(sorted(actions))
    if identity in {FLUX_KUSTOMIZATION_IDENTITY, FLUX_HELM_RELEASE_IDENTITY}:
        actions = ["reconcile", "sync_with_source"]
        actions.append("resume" if spec.get("suspend") is True else "suspend")
        return tuple(sorted(actions))
    if identity in FLUX_SOURCE_IDENTITIES:
        return ("reconcile",)
    return ()


def _declared_candidates(
    root: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
) -> list[tuple[tuple[str, str, str, str], str, str]]:
    raw = _mapping(root.get("raw"))
    status = _mapping(raw.get("status"))
    identity = _api_kind(root)
    candidates: list[tuple[tuple[str, str, str, str], str, str]] = []
    if identity == ARGO_APPLICATION_IDENTITY:
        for item in _mapping_items(status.get("resources")):
            group = str(item.get("group") or "")
            version = str(item.get("version") or "v1")
            candidates.append(
                (
                    (
                        f"{group}/{version}" if group else version,
                        str(item.get("kind") or "").casefold(),
                        str(item.get("namespace") or ""),
                        str(item.get("name") or ""),
                    ),
                    "declared",
                    "owns",
                )
            )
        root_name = str(root.get("name") or "")
        for row in rows:
            if _identity_key(row) == _identity_key(root):
                continue
            labels = _mapping(row.get("labels"))
            annotations = _mapping(row.get("annotations"))
            tracking_id = str(annotations.get("argocd.argoproj.io/tracking-id") or "")
            if labels.get("app.kubernetes.io/instance") == root_name or tracking_id.startswith(
                f"{root_name}:"
            ):
                candidates.append((_identity_key(row), "generated", "owns"))
        return [item for item in candidates if all(item[0])]

    inventory = _mapping(status.get("inventory"))
    for item in _mapping_items(inventory.get("entries")):
        parsed = _parse_flux_inventory_id(str(item.get("id") or ""), str(item.get("v") or ""))
        if parsed is not None:
            candidates.append((parsed, "declared", "owns"))
    source_identity = _source_identity(root)
    if source_identity is not None:
        candidates.append((source_identity, "source", "source"))
    spec = _mapping(raw.get("spec"))
    for dependency in _mapping_items(spec.get("dependsOn")):
        name = str(dependency.get("name") or "")
        namespace = str(dependency.get("namespace") or root.get("namespace") or "")
        if name:
            candidates.append(
                (
                    (
                        str(root.get("api_version") or ""),
                        str(root.get("kind") or "").casefold(),
                        namespace,
                        name,
                    ),
                    "dependency",
                    "depends_on",
                )
            )
    if identity == FLUX_HELM_RELEASE_IDENTITY:
        release_namespace = str(root.get("namespace") or "")
        release_name = str(root.get("name") or "")
        for row in rows:
            if _identity_key(row) == _identity_key(root):
                continue
            labels = _mapping(row.get("labels"))
            if (
                labels.get("helm.toolkit.fluxcd.io/name") == release_name
                and labels.get("helm.toolkit.fluxcd.io/namespace") == release_namespace
            ):
                candidates.append((_identity_key(row), "generated", "owns"))
    return candidates


def _source_row(root: Mapping[str, Any], rows: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    identity = _source_identity(root)
    if identity is None:
        return None
    return next((row for row in rows if _identity_key(row) == identity), None)


def _source_identity(root: Mapping[str, Any]) -> tuple[str, str, str, str] | None:
    raw = _mapping(root.get("raw"))
    spec = _mapping(raw.get("spec"))
    identity = _api_kind(root)
    source_ref: Mapping[str, Any]
    if identity == FLUX_HELM_RELEASE_IDENTITY:
        chart = _mapping(spec.get("chart"))
        chart_spec = _mapping(chart.get("spec"))
        source_ref = _mapping(chart_spec.get("sourceRef"))
    else:
        source_ref = _mapping(spec.get("sourceRef"))
    kind = str(source_ref.get("kind") or "")
    name = str(source_ref.get("name") or "")
    namespace = str(source_ref.get("namespace") or root.get("namespace") or "")
    api_version = _source_api_version(kind)
    if not api_version or not kind or not name or not namespace:
        return None
    return (api_version, kind.casefold(), namespace, name)


def _source_api_version(kind: str) -> str | None:
    return {
        "gitrepository": "source.toolkit.fluxcd.io/v1",
        "ocirepository": "source.toolkit.fluxcd.io/v1",
        "helmrepository": "source.toolkit.fluxcd.io/v1",
        "bucket": "source.toolkit.fluxcd.io/v1",
        "helmchart": "source.toolkit.fluxcd.io/v1",
    }.get(kind.casefold())


def _parse_flux_inventory_id(value: str, version: str) -> tuple[str, str, str, str] | None:
    parts = value.split("_")
    if len(parts) < 4:
        return None
    namespace, kind, group = parts[0], parts[-1], parts[-2]
    name = "_".join(parts[1:-2])
    if not namespace or not name or not kind:
        return None
    api_version = f"{group}/{version or 'v1'}" if group and group != "core" else (version or "v1")
    return (api_version, kind.casefold(), namespace, name)


def _tree_node(resource: Mapping[str, Any], *, role: str) -> GitOpsTreeNode:
    ref = inventory_resource_ref(resource)
    return GitOpsTreeNode(
        id=ref.uid,
        resource=ref,
        role=role,
        status=_nullable_string(resource.get("status")),
        health=_nullable_string(resource.get("health")),
    )


def _conditions(status: Mapping[str, Any]) -> tuple[GitOpsCondition, ...]:
    result: list[GitOpsCondition] = []
    for condition in _mapping_items(status.get("conditions"))[:50]:
        condition_type = str(condition.get("type") or "").strip()
        condition_status = str(condition.get("status") or "").strip()
        if not condition_type or not condition_status:
            continue
        result.append(
            GitOpsCondition(
                type=condition_type,
                status=condition_status,
                reason=_nullable_string(condition.get("reason")),
                message=_bounded_string(condition.get("message"), 1000),
                observed_at=_nullable_string(
                    condition.get("lastTransitionTime") or condition.get("lastUpdateTime")
                ),
            )
        )
    return tuple(result)


def _history(status: Mapping[str, Any]) -> tuple[GitOpsHistoryEntry, ...]:
    result: list[GitOpsHistoryEntry] = []
    for entry in _mapping_items(status.get("history"))[:50]:
        initiated_by = _mapping(entry.get("initiatedBy"))
        entry_id = entry.get("id")
        result.append(
            GitOpsHistoryEntry(
                id=_nullable_string(entry_id),
                revision=_nullable_string(
                    entry.get("revision") or entry.get("chartVersion") or entry.get("appVersion")
                ),
                deployed_at=_nullable_string(
                    entry.get("deployedAt") or entry.get("finishedAt") or entry.get("startedAt")
                ),
                phase=_nullable_string(entry.get("phase") or entry.get("status")),
                message=_bounded_string(entry.get("message"), 1000),
                initiated_by=_nullable_string(
                    initiated_by.get("username") or initiated_by.get("automated")
                ),
            )
        )
    return tuple(result)


def _observed_revision(root: Mapping[str, Any]) -> str | None:
    raw = _mapping(root.get("raw"))
    status = _mapping(raw.get("status"))
    if _api_kind(root) == ARGO_APPLICATION_IDENTITY:
        sync = _mapping(status.get("sync"))
        return _nullable_string(sync.get("revision"))
    artifact = _mapping(status.get("artifact"))
    return _nullable_string(
        status.get("lastAppliedRevision")
        or status.get("lastAttemptedRevision")
        or artifact.get("revision")
    )


def _capability_revision(
    *,
    root: Mapping[str, Any],
    actions: tuple[str, ...],
    source: Mapping[str, Any] | None,
) -> str:
    value = {
        "snapshot_id": root.get("snapshot_id"),
        "uid": root.get("uid"),
        "resource_version": root.get("resource_version"),
        "actions": actions,
        "source_uid": source.get("uid") if source else None,
        "source_resource_version": source.get("resource_version") if source else None,
    }
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


def _cluster_scope(root: Mapping[str, Any], *, freshness: str) -> ClusterScope:
    namespace = _nullable_string(root.get("namespace"))
    return ClusterScope(
        workspace_id=str(root.get("workspace_id") or ""),
        cluster_id=str(root.get("cluster_id") or ""),
        namespaces=(namespace,) if namespace else (),
        freshness=freshness,
    )


def _api_kind(resource: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(resource.get("api_version") or "").strip(),
        str(resource.get("kind") or "").strip().casefold(),
    )


def _identity_key(resource: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(resource.get("api_version") or "").strip(),
        str(resource.get("kind") or "").strip().casefold(),
        str(resource.get("namespace") or ""),
        str(resource.get("name") or ""),
    )


def _has_complete_identity(resource: Mapping[str, Any]) -> bool:
    identity = _identity_key(resource)
    return bool(identity[0] and identity[1] and identity[3] and str(resource.get("uid") or ""))


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_items(value: object) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _nullable_string(value: object) -> str | None:
    result = str(value).strip() if isinstance(value, (str, int, float)) else ""
    return result or None


def _bounded_string(value: object, limit: int) -> str | None:
    result = _nullable_string(value)
    return result[:limit] if result else None
