"""Shared Gateway facet axis values."""

from __future__ import annotations

APPLICATION_FILTER_FACET_AXES = (
    "clusters",
    "namespaces",
    "applications",
    "environment",
    "status",
    "pending_promotion",
)

GITOPS_FILTER_FACET_AXES = (
    "clusters",
    "namespaces",
    "applications",
    "environment",
    "approval",
    "change_type",
)

RESOURCE_FILTER_FACET_AXES = ("clusters", "namespaces", "applications")

ISSUE_FILTER_FACET_AXES = (
    "clusters",
    "namespaces",
    "applications",
    "severity",
    "category",
    "status",
    "environment",
)

LABEL_FACET_AXIS = "labels"
APPLICATION_FILTER_CAPABILITY_AXES = (*APPLICATION_FILTER_FACET_AXES, LABEL_FACET_AXIS)
GITOPS_FILTER_CAPABILITY_AXES = (*GITOPS_FILTER_FACET_AXES, LABEL_FACET_AXIS)
ISSUE_FILTER_CAPABILITY_AXES = (*ISSUE_FILTER_FACET_AXES, LABEL_FACET_AXIS)
