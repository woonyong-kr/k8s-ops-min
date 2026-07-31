"""Validated GitOps overview filters shared by router and repository."""

from __future__ import annotations

from dataclasses import dataclass

from domains.inventory_filter.query import parse_resource_filters


@dataclass(frozen=True)
class GitOpsOverviewFilters:
    clusters: tuple[str, ...]
    namespaces: tuple[tuple[str, str], ...]
    applications: tuple[str, ...]
    providers: tuple[str, ...]
    kinds: tuple[str, ...]
    labels: tuple[tuple[str, str], ...]
    query: str | None


def parse_gitops_overview_filters(
    *,
    clusters: str | None,
    namespaces: str | None,
    applications: str | None,
    providers: str | None,
    kinds: str | None,
    labels: str | None,
    query: str | None,
) -> GitOpsOverviewFilters:
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
    provider_values = _axis(providers)
    if any(value not in {"argo", "flux", "internal"} for value in provider_values):
        raise ValueError("GitOps provider is invalid")
    return GitOpsOverviewFilters(
        clusters=common.clusters,
        namespaces=common.namespaces,
        applications=common.applications,
        providers=provider_values,
        kinds=tuple(value.casefold() for value in _axis(kinds)),
        labels=common.labels,
        query=common.query,
    )


def _axis(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    values = [item.strip() for item in value.split(",")]
    if not values or len(values) > 100 or any(not item or len(item) > 253 for item in values):
        raise ValueError("GitOps filter values are invalid")
    return tuple(sorted(set(values)))
