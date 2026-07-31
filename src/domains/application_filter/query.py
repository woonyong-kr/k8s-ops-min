"""Canonical parsing and fingerprinting for the Applications filter surface."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Literal

from domains.inventory_filter.query import parse_filter_axis_values, parse_resource_filters
from packages.contracts.gateway import facets as gateway_facets

ApplicationFacetAxis = Literal[*gateway_facets.APPLICATION_FILTER_FACET_AXES]


@dataclass(frozen=True)
class ApplicationFilters:
    clusters: tuple[str, ...]
    namespaces: tuple[tuple[str, str], ...]
    applications: tuple[str, ...]
    environments: tuple[str, ...]
    statuses: tuple[str, ...]
    pending_promotion: bool | None
    labels: tuple[tuple[str, str], ...]
    query: str | None


def parse_application_filters(
    *,
    clusters: str | None,
    namespaces: str | None,
    applications: str | None,
    environments: str | None,
    statuses: str | None,
    pending_promotion: str | None,
    labels: str | None,
    query: str | None,
) -> ApplicationFilters:
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
    return ApplicationFilters(
        clusters=common.clusters,
        namespaces=common.namespaces,
        applications=common.applications,
        environments=_application_axis(environments),
        statuses=_application_axis(statuses),
        pending_promotion=_pending_promotion(pending_promotion),
        labels=common.labels,
        query=common.query,
    )


def application_filter_fingerprint(filters: ApplicationFilters) -> str:
    encoded = json.dumps(
        {
            "clusters": filters.clusters,
            "namespaces": filters.namespaces,
            "applications": filters.applications,
            "environments": filters.environments,
            "statuses": filters.statuses,
            "pending_promotion": filters.pending_promotion,
            "labels": filters.labels,
            "query": filters.query,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def without_facet_axis(
    filters: ApplicationFilters,
    axis: ApplicationFacetAxis,
) -> ApplicationFilters:
    attribute = {
        "clusters": "clusters",
        "namespaces": "namespaces",
        "applications": "applications",
        "environment": "environments",
        "status": "statuses",
        "pending_promotion": "pending_promotion",
    }[axis]
    value: tuple[()] | None = None if axis == "pending_promotion" else ()
    return replace(filters, **{attribute: value})


def selected_facet_values(
    filters: ApplicationFilters,
    axis: ApplicationFacetAxis,
) -> tuple[str, ...]:
    if axis == "clusters":
        return filters.clusters
    if axis == "namespaces":
        return tuple(f"{cluster_id}/{namespace}" for cluster_id, namespace in filters.namespaces)
    if axis == "applications":
        return filters.applications
    if axis == "environment":
        return filters.environments
    if axis == "status":
        return filters.statuses
    if filters.pending_promotion is None:
        return ()
    return ("true" if filters.pending_promotion else "false",)


def _application_axis(value: str | None) -> tuple[str, ...]:
    return parse_filter_axis_values(value, casefold=True, field_name="application")


def _pending_promotion(value: str | None) -> bool | None:
    if value is None:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("pending promotion must be canonical true or false")
