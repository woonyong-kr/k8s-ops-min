"""Canonical parsing and fingerprinting for the GitOps filter surface."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Literal

from domains.inventory_filter.query import parse_filter_axis_values, parse_resource_filters
from packages.contracts.gateway import facets as gateway_facets

GitOpsFacetAxis = Literal[*gateway_facets.GITOPS_FILTER_FACET_AXES]


@dataclass(frozen=True)
class GitOpsFilters:
    clusters: tuple[str, ...]
    namespaces: tuple[tuple[str, str], ...]
    applications: tuple[str, ...]
    environments: tuple[str, ...]
    approvals: tuple[str, ...]
    change_types: tuple[str, ...]
    labels: tuple[tuple[str, str], ...]
    query: str | None


def parse_gitops_filters(
    *,
    clusters: str | None,
    namespaces: str | None,
    applications: str | None,
    environments: str | None,
    approvals: str | None,
    change_types: str | None,
    labels: str | None,
    query: str | None,
) -> GitOpsFilters:
    common = parse_resource_filters(
        clusters=clusters,
        namespaces=namespaces,
        applications=applications,
        resource_types=None,
        health=None,
        labels=labels,
        query=query,
        include_deleted=False,
    )
    return GitOpsFilters(
        clusters=common.clusters,
        namespaces=common.namespaces,
        applications=common.applications,
        environments=_axis(environments),
        approvals=_axis(approvals),
        change_types=_axis(change_types),
        labels=common.labels,
        query=common.query,
    )


def gitops_filter_fingerprint(filters: GitOpsFilters) -> str:
    encoded = json.dumps(
        {
            "clusters": filters.clusters,
            "namespaces": filters.namespaces,
            "applications": filters.applications,
            "environments": filters.environments,
            "approvals": filters.approvals,
            "change_types": filters.change_types,
            "labels": filters.labels,
            "query": filters.query,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def without_facet_axis(filters: GitOpsFilters, axis: GitOpsFacetAxis) -> GitOpsFilters:
    attribute = {
        "clusters": "clusters",
        "namespaces": "namespaces",
        "applications": "applications",
        "environment": "environments",
        "approval": "approvals",
        "change_type": "change_types",
    }[axis]
    return replace(filters, **{attribute: ()})


def selected_facet_values(filters: GitOpsFilters, axis: GitOpsFacetAxis) -> tuple[str, ...]:
    if axis == "clusters":
        return filters.clusters
    if axis == "namespaces":
        return tuple(f"{cluster_id}/{namespace}" for cluster_id, namespace in filters.namespaces)
    if axis == "applications":
        return filters.applications
    if axis == "environment":
        return filters.environments
    if axis == "approval":
        return filters.approvals
    return filters.change_types


def _axis(value: str | None) -> tuple[str, ...]:
    return parse_filter_axis_values(value, casefold=True, field_name="GitOps")
