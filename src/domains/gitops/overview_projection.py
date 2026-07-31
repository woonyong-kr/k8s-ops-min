"""Canonical GitOps overview projection from immutable inventory and registered bindings."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from packages.contracts.gitops.overview import (
    GitOpsOverviewCoverage,
    GitOpsOverviewKindCount,
    GitOpsOverviewResponse,
    GitOpsOverviewRow,
)
from packages.contracts.parity import CapabilitySet, ClusterScope, ResourceRef

GITOPS_RESOURCE_FAMILIES: dict[tuple[str, str], tuple[str, str]] = {
    ("argoproj.io", "application"): ("argo", "controller"),
    ("argoproj.io", "applicationset"): ("argo", "controller"),
    ("kustomize.toolkit.fluxcd.io", "kustomization"): ("flux", "controller"),
    ("helm.toolkit.fluxcd.io", "helmrelease"): ("flux", "controller"),
    ("source.toolkit.fluxcd.io", "gitrepository"): ("flux", "source"),
    ("source.toolkit.fluxcd.io", "ocirepository"): ("flux", "source"),
    ("source.toolkit.fluxcd.io", "helmrepository"): ("flux", "source"),
    ("source.toolkit.fluxcd.io", "bucket"): ("flux", "source"),
    ("source.toolkit.fluxcd.io", "helmchart"): ("flux", "source"),
}


def project_gitops_overview(
    *,
    workspace_id: str,
    registered_rows: Sequence[Mapping[str, Any]],
    inventory_rows: Sequence[Mapping[str, Any]],
    snapshot_contexts: Mapping[str, Mapping[str, Any]],
    has_more: bool,
) -> GitOpsOverviewResponse:
    controller_items = _controller_items(workspace_id, inventory_rows, snapshot_contexts)
    registered_items = _registered_items(workspace_id, registered_rows, snapshot_contexts)
    items = tuple(
        sorted(
            (*controller_items, *registered_items),
            key=lambda row: (0 if row.authority == "controller" else 1, row.display_name, row.id),
        )
    )
    reasons = _coverage_reasons(snapshot_contexts)
    if has_more:
        reasons.add("result_truncated")
    state = "partial" if reasons else "complete"
    scopes = _scopes(items, snapshot_contexts, workspace_id)
    return GitOpsOverviewResponse(
        workspace_id=workspace_id,
        scopes=scopes,
        items=items,
        kind_counts=_kind_counts(controller_items),
        coverage=GitOpsOverviewCoverage(
            state=state,
            registered_count=len(registered_items),
            controller_count=len(controller_items),
            returned_count=len(items),
            reason_codes=tuple(sorted(reasons)),
        ),
        observed_at=_latest_observed_at(items, snapshot_contexts),
    )


def gitops_resource_family(api_version: str, kind: str) -> tuple[str, str] | None:
    api_group, _version = _split_api_version(api_version)
    return GITOPS_RESOURCE_FAMILIES.get((api_group.casefold(), kind.casefold()))


def _controller_items(
    workspace_id: str,
    rows: Sequence[Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
) -> tuple[GitOpsOverviewRow, ...]:
    projected: dict[tuple[str, str], GitOpsOverviewRow] = {}
    for row in rows:
        family = gitops_resource_family(
            str(row.get("api_version") or ""), str(row.get("kind") or "")
        )
        uid = str(row.get("uid") or "").strip()
        cluster_id = str(row.get("cluster_id") or "").strip()
        name = str(row.get("name") or "").strip()
        resource_version = str(row.get("resource_version") or "").strip()
        if family is None or not uid or not cluster_id or not name or not resource_version:
            continue
        api_group, version = _split_api_version(str(row["api_version"]))
        namespace = _nullable_text(row.get("namespace"))
        context = contexts.get(cluster_id, {})
        scope = ClusterScope(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            namespaces=(namespace,) if namespace else (),
            freshness=_freshness(context),
        )
        resource = ResourceRef(
            api_group=api_group,
            version=version,
            kind=str(row["kind"]),
            namespace=namespace,
            name=name,
            uid=uid,
        )
        provider, role = family
        projected[(cluster_id, uid)] = GitOpsOverviewRow(
            id=f"controller:{cluster_id}:{uid}",
            authority="controller",
            provider=provider,
            role=role,
            display_name=name,
            application_ids=tuple(
                str(value) for value in row.get("application_ids", ()) if str(value).strip()
            ),
            scope=scope,
            resource=resource,
            status=_nullable_text(row.get("status")),
            health=_nullable_text(row.get("health")),
            revision=_summary_revision(row.get("summary")),
            observed_at=_iso_text(row.get("observed_at")),
            labels=_labels(row.get("labels")),
            capabilities=CapabilitySet(
                scope=scope,
                resource=resource,
                revision=resource_version,
                actions=(),
            ),
            partial_reason_codes=tuple(sorted(_context_reasons(context))),
        )
    return tuple(projected.values())


def _registered_items(
    workspace_id: str,
    rows: Sequence[Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
) -> tuple[GitOpsOverviewRow, ...]:
    projected: dict[str, GitOpsOverviewRow] = {}
    for row in rows:
        binding_id = str(row.get("binding_id") or "").strip()
        application_id = str(row.get("application_id") or "").strip()
        cluster_id = str(row.get("cluster_id") or "").strip()
        name = str(row.get("application_name") or row.get("app_name") or "").strip()
        if not binding_id or not application_id or not cluster_id or not name:
            continue
        namespace = _nullable_text(row.get("namespace"))
        context = contexts.get(cluster_id, {})
        projected[binding_id] = GitOpsOverviewRow(
            id=f"registered:{binding_id}",
            authority="registered",
            provider="internal",
            role="controller",
            display_name=name,
            application_ids=(application_id,),
            binding_id=binding_id,
            scope=ClusterScope(
                workspace_id=workspace_id,
                cluster_id=cluster_id,
                namespaces=(namespace,) if namespace else (),
                freshness=_freshness(context),
            ),
            environment=_nullable_text(row.get("environment")),
            status=_nullable_text(row.get("status")),
            revision=_nullable_text(row.get("revision")),
            observed_at=_iso_text(row.get("observed_at")),
            partial_reason_codes=tuple(sorted(_context_reasons(context))),
        )
    return tuple(projected.values())


def _kind_counts(rows: Sequence[GitOpsOverviewRow]) -> tuple[GitOpsOverviewKindCount, ...]:
    counts = Counter(
        (
            row.resource.api_group,
            row.resource.version,
            row.resource.kind,
            row.provider,
            row.role,
            "partial" if row.partial_reason_codes else "exact",
        )
        for row in rows
        if row.resource is not None
    )
    return tuple(
        GitOpsOverviewKindCount(
            api_group=key[0],
            version=key[1],
            kind=key[2],
            provider=key[3],
            role=key[4],
            completeness=key[5],
            count=count,
        )
        for key, count in sorted(counts.items())
    )


def _scopes(
    items: Sequence[GitOpsOverviewRow],
    contexts: Mapping[str, Mapping[str, Any]],
    workspace_id: str,
) -> tuple[ClusterScope, ...]:
    by_cluster: dict[str, set[str]] = {}
    for item in items:
        by_cluster.setdefault(item.scope.cluster_id, set()).update(item.scope.namespaces)
    for cluster_id in contexts:
        by_cluster.setdefault(cluster_id, set())
    return tuple(
        ClusterScope(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            namespaces=tuple(sorted(namespaces)),
            freshness=_freshness(contexts.get(cluster_id, {})),
        )
        for cluster_id, namespaces in sorted(by_cluster.items())
    )


def _coverage_reasons(contexts: Mapping[str, Mapping[str, Any]]) -> set[str]:
    reasons: set[str] = set()
    for context in contexts.values():
        reasons.update(_context_reasons(context))
    return reasons


def _context_reasons(context: Mapping[str, Any]) -> set[str]:
    reasons = {
        str(value) for value in context.get("partial_reason_codes", ()) if str(value).strip()
    }
    if context and not reasons and context.get("resources_complete") is not True:
        reasons.add("snapshot_incomplete")
    if context and not reasons and context.get("labels_complete") is not True:
        reasons.add("labels_incomplete")
    if context and not reasons and not context.get("observed_at"):
        reasons.add("snapshot_unavailable")
    return reasons


def _freshness(context: Mapping[str, Any]) -> str:
    explicit = str(context.get("freshness") or "").strip()
    if explicit in {"live", "stale", "partial", "disconnected"}:
        return explicit
    if not context or not context.get("observed_at"):
        return "disconnected"
    return "partial" if _context_reasons(context) else "live"


def _summary_revision(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for key in (
        "revision",
        "observed_revision",
        "last_applied_revision",
        "last_attempted_revision",
    ):
        result = _nullable_text(value.get(key))
        if result:
            return result
    artifact = value.get("artifact")
    return _nullable_text(artifact.get("revision")) if isinstance(artifact, Mapping) else None


def _latest_observed_at(
    items: Sequence[GitOpsOverviewRow],
    contexts: Mapping[str, Mapping[str, Any]],
) -> str | None:
    values = [item.observed_at for item in items if item.observed_at]
    values.extend(
        value
        for context in contexts.values()
        if (value := _iso_text(context.get("observed_at"))) is not None
    )
    return max(values) if values else None


def _split_api_version(value: str) -> tuple[str, str]:
    api_group, separator, version = value.strip().partition("/")
    return (api_group, version) if separator else ("", api_group)


def _nullable_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _iso_text(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else _nullable_text(value)


def _labels(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): str(label)
        for key, label in value.items()
        if str(key).strip() and str(label).strip()
    }
