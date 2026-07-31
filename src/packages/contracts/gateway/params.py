"""Shared Gateway query parameter names."""

from __future__ import annotations

TIME_RANGE_FROM_QUERY = "from"
TIME_RANGE_TO_QUERY = "to"
LIMIT_QUERY = "limit"
CURSOR_QUERY = "cursor"
CLUSTER_ID_QUERY = "cluster_id"
CLUSTERS_QUERY = "clusters"
NAMESPACE_QUERY = "namespace"
NAMESPACES_QUERY = "namespaces"
APPLICATIONS_QUERY = "applications"
LABELS_QUERY = "labels"
GLOBAL_FILTER_SEARCH_QUERY = "q"
SNAPSHOT_REVISION_QUERY = "snapshot_revision"
FACET_AXIS_QUERY = "axis"
FACET_SELECTED_QUERY = "selected"
FACET_SEARCH_QUERY = "facet_q"

APPLICATIONS_ENVIRONMENT_QUERY = "applications.environment"
APPLICATIONS_STATUS_QUERY = "applications.status"
APPLICATIONS_PENDING_PROMOTION_QUERY = "applications.pendingPromotion"
APPLICATIONS_SEARCH_QUERY = "applications.q"

GITOPS_ENVIRONMENT_QUERY = "gitops.environment"
GITOPS_APPROVAL_QUERY = "gitops.approval"
GITOPS_CHANGE_TYPE_QUERY = "gitops.changeType"
GITOPS_SEARCH_QUERY = "gitops.q"

RESOURCE_TYPES_QUERY = "resources.types"
RESOURCE_HEALTH_QUERY = "resources.health"
RESOURCE_SEARCH_QUERY = "resources.q"
RESOURCE_INCLUDE_DELETED_QUERY = "resources.includeDeleted"
RESOURCE_METRIC_HISTORY_IDS_QUERY = "ids"
RESOURCE_METRIC_HISTORY_RANGE_QUERY = "range"

ISSUES_SEVERITY_QUERY = "issues.severity"
ISSUES_CATEGORY_QUERY = "issues.category"
ISSUES_STATUS_QUERY = "issues.status"
ISSUES_ENVIRONMENT_QUERY = "issues.environment"
ISSUES_SEARCH_QUERY = "issues.q"

CHANGE_BUCKET_QUERY = "bucket"
