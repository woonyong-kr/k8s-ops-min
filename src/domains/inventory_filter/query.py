"""Canonical parsing and fingerprinting for workspace-wide inventory filters."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from packages.contracts.gateway import limits as gateway_limits

MAX_AXIS_VALUES = gateway_limits.FILTER_AXIS_MAX_VALUES
MAX_LABEL_SELECTORS = gateway_limits.FILTER_LABEL_SELECTOR_MAX_VALUES
MAX_TOKEN_LENGTH = gateway_limits.FILTER_AXIS_VALUE_MAX_LENGTH
MAX_LABEL_VALUE_LENGTH = gateway_limits.FILTER_LABEL_VALUE_MAX_LENGTH
MAX_QUERY_LENGTH = gateway_limits.FILTER_SEARCH_MAX_LENGTH
MAX_KUBERNETES_LABEL_NAME_LENGTH = gateway_limits.KUBERNETES_LABEL_NAME_MAX_LENGTH
MAX_KUBERNETES_LABEL_PREFIX_LENGTH = gateway_limits.KUBERNETES_LABEL_PREFIX_MAX_LENGTH
LABEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[-A-Za-z0-9_.]*[A-Za-z0-9])?$")
DNS_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")


@dataclass(frozen=True)
class ResourceFilters:
    clusters: tuple[str, ...]
    namespaces: tuple[tuple[str, str], ...]
    applications: tuple[str, ...]
    resource_types: tuple[str, ...]
    health: tuple[str, ...]
    labels: tuple[tuple[str, str], ...]
    query: str | None
    include_deleted: bool


def parse_resource_filters(
    *,
    clusters: str | None,
    namespaces: str | None,
    applications: str | None,
    resource_types: str | None,
    health: str | None,
    labels: str | None,
    query: str | None,
    include_deleted: bool,
) -> ResourceFilters:
    parsed_query = query.strip() if query is not None else None
    if parsed_query == "":
        parsed_query = None
    if parsed_query is not None and _has_control_character(parsed_query):
        raise ValueError("query contains unsafe control characters")
    if parsed_query is not None and len(parsed_query) > MAX_QUERY_LENGTH:
        raise ValueError("query is too long")
    return ResourceFilters(
        clusters=_axis(clusters),
        namespaces=_namespaces(namespaces),
        applications=_axis(applications),
        resource_types=tuple(value.casefold() for value in _axis(resource_types)),
        health=tuple(value.casefold() for value in _axis(health)),
        labels=_labels(labels),
        query=parsed_query,
        include_deleted=include_deleted,
    )


def filter_fingerprint(filters: ResourceFilters) -> str:
    payload = {
        "clusters": filters.clusters,
        "namespaces": filters.namespaces,
        "applications": filters.applications,
        "resource_types": filters.resource_types,
        "health": filters.health,
        "labels": filters.labels,
        "query": filters.query,
        "include_deleted": filters.include_deleted,
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def parse_facet_values(axis: str, value: str | None) -> tuple[str, ...]:
    if axis == "clusters":
        return _axis(value)
    if axis == "applications":
        return _axis(value)
    if axis == "namespaces":
        return tuple(f"{cluster_id}/{namespace}" for cluster_id, namespace in _namespaces(value))
    raise ValueError("facet axis is invalid")


def parse_filter_axis_values(
    value: str | None,
    *,
    casefold: bool = False,
    field_name: str = "filter",
    max_values: int = MAX_AXIS_VALUES,
    max_length: int = MAX_TOKEN_LENGTH,
) -> tuple[str, ...]:
    values = _tokens(value, limit=max_values, field_name=field_name)
    if any(len(item) > max_length for item in values):
        raise ValueError(f"{field_name} filter value is too long")
    normalized = [item.casefold() if casefold else item for item in values]
    return tuple(sorted(set(normalized)))


def _tokens(value: str | None, *, limit: int, field_name: str = "filter") -> list[str]:
    if value is None:
        return []
    raw = value.split(",")
    if not raw or len(raw) > limit:
        raise ValueError(f"too many {field_name} filter values")
    tokens = [item.strip() for item in raw]
    if any(not item for item in tokens):
        raise ValueError(f"{field_name} filter values cannot be empty")
    if any(_has_control_character(item) for item in tokens):
        raise ValueError(f"{field_name} filter values contain unsafe control characters")
    if any(len(item) > MAX_LABEL_VALUE_LENGTH for item in tokens):
        raise ValueError(f"{field_name} filter value is too long")
    return tokens


def _axis(value: str | None) -> tuple[str, ...]:
    return parse_filter_axis_values(value)


def _namespaces(value: str | None) -> tuple[tuple[str, str], ...]:
    parsed: set[tuple[str, str]] = set()
    for token in _tokens(value, limit=MAX_AXIS_VALUES):
        cluster_id, separator, namespace = token.rpartition("/")
        if not separator or not cluster_id or not namespace:
            raise ValueError("namespace must use <cluster-id>/<namespace>")
        if len(cluster_id) > MAX_TOKEN_LENGTH or len(namespace) > MAX_TOKEN_LENGTH:
            raise ValueError("namespace reference is too long")
        parsed.add((cluster_id, namespace))
    return tuple(sorted(parsed))


def _labels(value: str | None) -> tuple[tuple[str, str], ...]:
    parsed: set[tuple[str, str]] = set()
    for token in _tokens(value, limit=MAX_LABEL_SELECTORS):
        key, separator, label_value = token.partition("=")
        if not separator or not key:
            raise ValueError("label must use key=value")
        if not _valid_label_key(key) or not _valid_label_value(label_value):
            raise ValueError("label selector is invalid")
        if len(label_value) > MAX_KUBERNETES_LABEL_NAME_LENGTH:
            raise ValueError("label selector is too long")
        parsed.add((key, label_value))
    return tuple(sorted(parsed))


def _valid_label_key(key: str) -> bool:
    prefix, separator, name = key.rpartition("/")
    if not separator:
        name = key
        prefix = ""
    if not name or len(name) > MAX_KUBERNETES_LABEL_NAME_LENGTH:
        return False
    if LABEL_NAME_PATTERN.fullmatch(name) is None:
        return False
    if not prefix:
        return True
    if len(prefix) > MAX_KUBERNETES_LABEL_PREFIX_LENGTH:
        return False
    return all(
        len(part) <= 63 and DNS_LABEL_PATTERN.fullmatch(part) is not None
        for part in prefix.split(".")
    )


def _valid_label_value(value: str) -> bool:
    if not value:
        return True
    return (
        len(value) <= MAX_KUBERNETES_LABEL_NAME_LENGTH
        and LABEL_NAME_PATTERN.fullmatch(value) is not None
    )


def _has_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)
