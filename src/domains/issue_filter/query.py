"""Canonical parsing and fingerprinting for the Issues filter surface."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Literal

from domains.inventory_filter.query import parse_filter_axis_values, parse_resource_filters
from packages.contracts.gateway import facets as gateway_facets

IssueFacetAxis = Literal[*gateway_facets.ISSUE_FILTER_FACET_AXES]


@dataclass(frozen=True)
class IssueFilters:
    clusters: tuple[str, ...]
    namespaces: tuple[tuple[str, str], ...]
    applications: tuple[str, ...]
    severities: tuple[str, ...]
    categories: tuple[str, ...]
    statuses: tuple[str, ...]
    environments: tuple[str, ...]
    labels: tuple[tuple[str, str], ...]
    query: str | None


def parse_issue_filters(
    *,
    clusters: str | None,
    namespaces: str | None,
    applications: str | None,
    severities: str | None,
    statuses: str | None,
    environments: str | None,
    labels: str | None,
    query: str | None,
    categories: str | None = None,
) -> IssueFilters:
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
    return IssueFilters(
        clusters=common.clusters,
        namespaces=common.namespaces,
        applications=common.applications,
        severities=_issue_axis(severities),
        categories=_issue_axis(categories),
        statuses=_issue_axis(statuses),
        environments=_issue_axis(environments),
        labels=common.labels,
        query=common.query,
    )


def issue_filter_fingerprint(filters: IssueFilters) -> str:
    encoded = json.dumps(
        {
            "clusters": filters.clusters,
            "namespaces": filters.namespaces,
            "applications": filters.applications,
            "severities": filters.severities,
            "categories": filters.categories,
            "statuses": filters.statuses,
            "environments": filters.environments,
            "labels": filters.labels,
            "query": filters.query,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def without_facet_axis(filters: IssueFilters, axis: IssueFacetAxis) -> IssueFilters:
    """Remove only the requested axis so a facet can show alternative values."""
    attribute = {
        "clusters": "clusters",
        "namespaces": "namespaces",
        "applications": "applications",
        "severity": "severities",
        "category": "categories",
        "status": "statuses",
        "environment": "environments",
    }[axis]
    return replace(filters, **{attribute: ()})


def selected_facet_values(filters: IssueFilters, axis: IssueFacetAxis) -> tuple[str, ...]:
    if axis == "clusters":
        return filters.clusters
    if axis == "namespaces":
        return tuple(f"{cluster_id}/{namespace}" for cluster_id, namespace in filters.namespaces)
    if axis == "applications":
        return filters.applications
    if axis == "severity":
        return filters.severities
    if axis == "category":
        return filters.categories
    if axis == "status":
        return filters.statuses
    return filters.environments


def _issue_axis(value: str | None) -> tuple[str, ...]:
    return parse_filter_axis_values(value, casefold=True, field_name="issue")
