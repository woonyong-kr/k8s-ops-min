from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from collections.abc import Set as AbstractSet
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from packages.contracts.gateway import evidence as gateway_evidence
from packages.contracts.gateway import facets as gateway_facets
from packages.contracts.gateway import limits as gateway_limits
from packages.contracts.gateway import params as gateway_params
from packages.contracts.gateway import routes
from packages.contracts.gitops import ApprovalStatus, WorkflowRunStatus
from packages.security.log_lines import REDACTED_VALUE, redact_log_line
from services.mcp.internal_control import limits as mcp_limits
from services.mcp.internal_control.api_client import ManagementApiClient, ManagementApiError
from services.mcp.internal_control.config import OPSIA_MCP_ENABLE_WRITES_ENV

ToolHandler = Callable[[ManagementApiClient, dict[str, Any]], Awaitable[dict[str, Any]]]

DEFAULT_LIST_CLUSTERS_LIMIT = gateway_limits.CLUSTER_LIST_DEFAULT_LIMIT
MAX_LIST_CLUSTERS_LIMIT = gateway_limits.CLUSTER_LIST_MAX_LIMIT
DEFAULT_LIST_RESOURCES_LIMIT = gateway_limits.INVENTORY_RESOURCE_DEFAULT_LIMIT
MAX_LIST_RESOURCES_LIMIT = gateway_limits.INVENTORY_RESOURCE_MAX_LIMIT
DEFAULT_RELATED_LIMIT = gateway_limits.INVENTORY_RELATED_DEFAULT_LIMIT
MAX_RELATED_LIMIT = gateway_limits.INVENTORY_RELATED_MAX_LIMIT
DEFAULT_EVENT_LIMIT = gateway_limits.INVENTORY_EVENT_DEFAULT_LIMIT
MAX_EVENT_LIMIT = gateway_limits.INVENTORY_EVENT_MAX_LIMIT
DEFAULT_CLUSTER_USAGE_LIMIT = gateway_limits.CLUSTER_USAGE_DEFAULT_LIMIT
MAX_CLUSTER_USAGE_LIMIT = gateway_limits.CLUSTER_USAGE_MAX_LIMIT
DEFAULT_FILTER_FACET_LIMIT = gateway_limits.FILTER_FACET_DEFAULT_LIMIT
MAX_FILTER_FACET_LIMIT = gateway_limits.FILTER_FACET_MAX_LIMIT
DEFAULT_GLOBAL_FILTER_FACET_LIMIT = gateway_limits.GLOBAL_FILTER_FACET_DEFAULT_LIMIT
MAX_GLOBAL_FILTER_FACET_LIMIT = gateway_limits.GLOBAL_FILTER_FACET_MAX_LIMIT
MAX_FILTER_CURSOR_LENGTH = gateway_limits.FILTER_CURSOR_MAX_LENGTH
MAX_FILTER_SEARCH_LENGTH = gateway_limits.FILTER_SEARCH_MAX_LENGTH
MAX_FILTER_VALUE_LIST_LENGTH = gateway_limits.FILTER_VALUE_LIST_MAX_LENGTH
MAX_CLUSTER_ID_LENGTH = gateway_limits.CLUSTER_ID_MAX_LENGTH
MAX_KUBERNETES_NAME_LENGTH = gateway_limits.KUBERNETES_NAME_MAX_LENGTH
MAX_CORRELATION_ID_LENGTH = gateway_limits.CORRELATION_ID_MAX_LENGTH
MAX_INCIDENT_ID_LENGTH = gateway_limits.INCIDENT_ID_MAX_LENGTH
DEFAULT_RESOURCE_METRIC_HISTORY_LIMIT = gateway_limits.RESOURCE_METRIC_HISTORY_DEFAULT_LIMIT
MAX_RESOURCE_METRIC_HISTORY_LIMIT = gateway_limits.RESOURCE_METRIC_HISTORY_MAX_LIMIT
MAX_RESOURCE_METRIC_HISTORY_IDS = gateway_limits.RESOURCE_METRIC_HISTORY_MAX_IDS
MAX_RESOURCE_METRIC_HISTORY_ID_LENGTH = gateway_limits.RESOURCE_METRIC_HISTORY_ID_MAX_LENGTH
MAX_RESOURCE_METRIC_HISTORY_IDS_QUERY_LENGTH = (
    gateway_limits.RESOURCE_METRIC_HISTORY_IDS_MAX_QUERY_LENGTH
)
DEFAULT_RESOURCE_METRIC_HISTORY_RANGE = gateway_limits.RESOURCE_METRIC_HISTORY_DEFAULT_RANGE
RESOURCE_METRIC_HISTORY_RANGES = gateway_limits.RESOURCE_METRIC_HISTORY_RANGES
DEFAULT_RECENT_INCIDENT_LIMIT = gateway_limits.RCA_QUERY_DEFAULT_LIMIT
MAX_QUERY_LIMIT = gateway_limits.RCA_QUERY_MAX_LIMIT
DEFAULT_RCA_RECENT_CHANGE_LIMIT = gateway_limits.RCA_RECENT_CHANGE_DEFAULT_LIMIT
MAX_RCA_RECENT_CHANGE_LIMIT = gateway_limits.RCA_RECENT_CHANGE_MAX_LIMIT
DEFAULT_APPLICATION_LIMIT = gateway_limits.APPLICATION_LIST_DEFAULT_LIMIT
MAX_APPLICATION_LIMIT = gateway_limits.APPLICATION_LIST_MAX_LIMIT
DEFAULT_APPLICATION_DEPLOYMENT_LIMIT = gateway_limits.APPLICATION_DEPLOYMENT_DEFAULT_LIMIT
MAX_APPLICATION_DEPLOYMENT_LIMIT = gateway_limits.APPLICATION_DEPLOYMENT_MAX_LIMIT
DEFAULT_ALERT_EVENT_LIMIT = gateway_limits.ALERT_EVENT_DEFAULT_LIMIT
MAX_ALERT_EVENT_LIMIT = gateway_limits.ALERT_EVENT_MAX_LIMIT
DEFAULT_DEAD_LETTER_LIMIT = gateway_limits.DEAD_LETTER_DEFAULT_LIMIT
MAX_DEAD_LETTER_LIMIT = gateway_limits.DEAD_LETTER_MAX_LIMIT
DEFAULT_RCA_ISSUE_LIMIT = gateway_limits.DASHBOARD_RCA_DEFAULT_LIMIT
MAX_RCA_ISSUE_LIMIT = gateway_limits.DASHBOARD_RCA_MAX_LIMIT
DEFAULT_RESOURCE_ISSUE_LIMIT = gateway_limits.RESOURCE_ISSUE_DEFAULT_LIMIT
DEFAULT_APPLICATION_WORKFLOW_RUN_LIMIT = gateway_limits.APPLICATION_WORKFLOW_RUN_DEFAULT_LIMIT
MAX_APPLICATION_WORKFLOW_RUN_LIMIT = gateway_limits.APPLICATION_WORKFLOW_RUN_MAX_LIMIT
DEFAULT_RELEASE_PLAN_LIMIT = gateway_limits.RELEASE_PLAN_DEFAULT_LIMIT
MAX_RELEASE_PLAN_LIMIT = gateway_limits.RELEASE_PLAN_MAX_LIMIT
DEFAULT_RELEASE_RUN_LIMIT = gateway_limits.RELEASE_RUN_DEFAULT_LIMIT
MAX_RELEASE_RUN_LIMIT = gateway_limits.RELEASE_RUN_MAX_LIMIT
DEFAULT_RELEASE_AUDIT_LIMIT = gateway_limits.RELEASE_AUDIT_DEFAULT_LIMIT
MAX_RELEASE_AUDIT_LIMIT = gateway_limits.RELEASE_AUDIT_MAX_LIMIT
DEFAULT_AUDIT_TIMELINE_LIMIT = gateway_limits.AUDIT_TIMELINE_DEFAULT_LIMIT
MAX_AUDIT_TIMELINE_LIMIT = gateway_limits.AUDIT_TIMELINE_MAX_LIMIT
MAX_PENDING_APPROVAL_APPLICATIONS = mcp_limits.MAX_PENDING_APPROVAL_APPLICATIONS
MAX_GRAPH_NODE_LIMIT = gateway_limits.RESOURCE_GRAPH_MAX_NODE_LIMIT
MAX_GRAPH_EDGE_LIMIT = gateway_limits.RESOURCE_GRAPH_MAX_EDGE_LIMIT
MIN_CHANGE_BUCKET_MS = gateway_limits.CHANGE_TIMELINE_MIN_BUCKET_MS
MAX_CHANGE_BUCKET_MS = gateway_limits.CHANGE_TIMELINE_MAX_BUCKET_MS
MAX_CHANGE_RANGE_MS = gateway_limits.CHANGE_TIMELINE_MAX_RANGE_MS
MAX_CHANGE_BUCKETS = gateway_limits.CHANGE_TIMELINE_MAX_BUCKETS
MAX_EPOCH_MILLISECONDS = gateway_limits.CHANGE_TIMELINE_MAX_EPOCH_MS
MAX_LEGACY_OFFSET = mcp_limits.MAX_LEGACY_OFFSET
MAX_WRITE_PAYLOAD_BYTES = mcp_limits.MAX_WRITE_PAYLOAD_BYTES
LOG_EVIDENCE_SOURCE = gateway_evidence.EVIDENCE_SOURCE_LOGS
PENDING_APPROVAL_STATUS = ApprovalStatus.REQUESTED.value
WAITING_FOR_APPROVAL_STATUS = WorkflowRunStatus.WAITING_FOR_APPROVAL.value
IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
RESPONSE_RULES_KEY = "rules"
RESPONSE_RUNS_KEY = "runs"
RESPONSE_ITEMS_KEY = "items"
RESPONSE_CHANNELS_KEY = "channels"
ALERT_RULE_ID_KEY = "rule_id"
ALERT_CHANNEL_ID_KEY = "channel_id"
ALERT_CHANNEL_LAST_TEST_DETAIL_KEY = "last_test_detail"
ALERT_CHANNEL_ENDPOINT_KEY = "endpoint"
ALERT_CHANNEL_URL_KEY = "url"
ALERT_CHANNEL_WEBHOOK_URL_KEY = "webhook_url"
APPLICATION_ID_KEY = "application_id"
WORKFLOW_RUN_ID_KEY = "workflow_run_id"
METRIC_WIDGET_ID_KEY = "widget_id"
READ_ONLY_TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
}
WRITE_TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
}
SENSITIVE_PROPOSAL_KEY_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "id_token",
        "password",
        "passwd",
        "private_key",
        "refresh_token",
        "secret",
        "ssh_key",
        "token",
    }
)
SENSITIVE_PROPOSAL_EXACT_KEYS = frozenset({"data", "edited_yaml", "stringdata"})
SENSITIVE_PROPOSAL_MARKER_KEYS = frozenset({"key", "name"})
SENSITIVE_PROPOSAL_MARKER_VALUE_KEYS = frozenset({"default", "literal", "value"})
SENSITIVE_READ_EXACT_KEYS = frozenset({"binarydata", "edited_yaml", "raw", "stringdata"})
SECRET_READ_DATA_KEYS = frozenset({"binarydata", "data", "stringdata"})
ALERT_CHANNEL_CONTEXT_KEYS = frozenset({ALERT_CHANNEL_ID_KEY})
ALERT_CHANNEL_ENDPOINT_KEYS = frozenset(
    {ALERT_CHANNEL_ENDPOINT_KEY, ALERT_CHANNEL_URL_KEY, ALERT_CHANNEL_WEBHOOK_URL_KEY}
)
ALERT_CHANNEL_REDACTED_KEYS = ALERT_CHANNEL_ENDPOINT_KEYS | frozenset(
    {ALERT_CHANNEL_LAST_TEST_DETAIL_KEY}
)
DIRECT_EXECUTION_KEYS = frozenset(
    {"confirmation", "direct_execution", "direct_execution_confirmed"}
)
WRITE_METHODS = frozenset({"POST", "PATCH"})
ALLOWED_WRITE_GATEWAY_ROUTES = frozenset(
    {
        ("POST", routes.CLUSTER_METRIC_QUERY_PRESET_RUN_PATH),
        ("POST", routes.ALERT_RULES_PATH),
        ("POST", routes.RCA_RECOVERY_ACTION_SELECT_PATH),
        ("POST", routes.RCA_RECOVERY_ACTION_SELECT_BY_CORRELATION_PATH),
        ("POST", routes.COMMANDS_PATH),
        ("POST", routes.APPROVAL_GRANT_PATH),
        ("POST", routes.APPROVAL_REJECT_PATH),
        ("POST", routes.COMMAND_CANCEL_PATH),
        ("POST", routes.COMMAND_RETRY_PATH),
        ("POST", routes.ALERT_EVENT_ACK_PATH),
        ("POST", routes.ALERT_EVENT_PROMOTE_INCIDENT_PATH),
        ("POST", routes.RESOURCE_MANIFEST_APPROVE_PATH),
        ("POST", routes.RELEASE_PLANS_PATH),
        ("POST", routes.RELEASE_PLAN_START_PATH),
        ("PATCH", routes.ALERT_RULE_PATH),
    }
)
ALLOWED_NON_MUTATING_POST_GATEWAY_ROUTES = frozenset(
    {
        ("POST", routes.RESOURCE_MANIFEST_PREVIEW_PATH),
    }
)


class ToolInputError(ValueError):
    """The model supplied invalid tool arguments."""


@dataclass(frozen=True)
class McpTool:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    annotations: dict[str, Any] | None = None

    def as_protocol_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": deepcopy(self.input_schema),
            "annotations": dict(self.annotations or READ_ONLY_TOOL_ANNOTATIONS),
        }


class ToolRegistry:
    def __init__(self, tools: list[McpTool]) -> None:
        names = [tool.name for tool in tools]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate MCP tool names: {', '.join(duplicates)}")
        self._tools = {tool.name: tool for tool in tools}

    def list_tools(self) -> list[dict[str, Any]]:
        return [self._tools[name].as_protocol_tool() for name in sorted(self._tools)]

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        client: ManagementApiClient,
    ) -> dict[str, Any]:
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise ToolInputError(f"unknown tool: {name}") from exc
        return await tool.handler(client, arguments)


def default_tool_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            McpTool(
                name="list_clusters",
                title="List Clusters",
                description=(
                    "List clusters visible to the authenticated Opsia session by calling "
                    "the existing cluster API."
                ),
                input_schema=_schema(
                    properties={
                        "limit": _integer(
                            "Maximum number of clusters to return.",
                            minimum=1,
                            maximum=MAX_LIST_CLUSTERS_LIMIT,
                            default=DEFAULT_LIST_CLUSTERS_LIMIT,
                        ),
                    }
                ),
                handler=list_clusters,
            ),
            McpTool(
                name="get_fleet_summary",
                title="Get Fleet Summary",
                description=(
                    "Return the existing authorized fleet roll-up summary without "
                    "querying cluster state directly."
                ),
                input_schema=_schema(properties={}),
                handler=get_fleet_summary,
            ),
            McpTool(
                name="list_feature_contracts",
                title="List Feature Contracts",
                description=(
                    "Return the authenticated Gateway feature-contract catalog so AI "
                    "can explain which product capabilities exist without hard-coding them."
                ),
                input_schema=_schema(properties={}),
                handler=list_feature_contracts,
            ),
            McpTool(
                name="get_cluster_summary",
                title="Get Cluster Summary",
                description=(
                    "Return the existing fleet drill-down summary for one authorized cluster."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string(
                            "Cluster id from list_clusters.",
                            max_length=MAX_CLUSTER_ID_LENGTH,
                        ),
                    },
                    required=["cluster_id"],
                ),
                handler=get_cluster_summary,
            ),
            McpTool(
                name="get_cluster_connection_status",
                title="Get Cluster Connection Status",
                description=(
                    "Return one cluster's existing registration, agent heartbeat, and "
                    "connection-stage projection through the target API."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string(
                            "Cluster id from list_clusters.",
                            max_length=MAX_CLUSTER_ID_LENGTH,
                        ),
                    },
                    required=["cluster_id"],
                ),
                handler=get_cluster_connection_status,
            ),
            McpTool(
                name="get_cluster_inventory_summary",
                title="Get Cluster Inventory Summary",
                description=(
                    "Return the existing inventory snapshot metadata and resource counts "
                    "for one authorized cluster."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string(
                            "Cluster id from list_clusters.",
                            max_length=MAX_CLUSTER_ID_LENGTH,
                        ),
                    },
                    required=["cluster_id"],
                ),
                handler=get_cluster_inventory_summary,
            ),
            McpTool(
                name="get_cluster_nodes_summary",
                title="Get Cluster Nodes Summary",
                description=(
                    "Return the authorized node health and usage roll-up for one cluster "
                    "through the existing fleet API."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string(
                            "Cluster id from list_clusters.",
                            max_length=MAX_CLUSTER_ID_LENGTH,
                        ),
                    },
                    required=["cluster_id"],
                ),
                handler=get_cluster_nodes_summary,
            ),
            McpTool(
                name="get_cluster_node_pods_summary",
                title="Get Cluster Node Pods Summary",
                description=(
                    "Return the authorized pod roll-up for one node by calling the existing "
                    "fleet node-pods summary API."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string(
                            "Cluster id from list_clusters.",
                            max_length=MAX_CLUSTER_ID_LENGTH,
                        ),
                        "node_name": _string(
                            "Existing Kubernetes node name from the node summary.",
                            max_length=MAX_KUBERNETES_NAME_LENGTH,
                        ),
                    },
                    required=["cluster_id", "node_name"],
                ),
                handler=get_cluster_node_pods_summary,
            ),
            McpTool(
                name="get_cluster_usage",
                title="Get Cluster Usage",
                description=(
                    "Return persisted usage rollup samples for one authorized cluster through "
                    "the inventory API. MCP does not query Prometheus or the cluster directly."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string(
                            "Cluster id from list_clusters.",
                            max_length=MAX_CLUSTER_ID_LENGTH,
                        ),
                        "limit": _integer(
                            "Maximum number of usage samples to return.",
                            minimum=1,
                            maximum=MAX_CLUSTER_USAGE_LIMIT,
                            default=DEFAULT_CLUSTER_USAGE_LIMIT,
                        ),
                    },
                    required=["cluster_id"],
                ),
                handler=get_cluster_usage,
            ),
            McpTool(
                name="list_resources",
                title="List Resources",
                description=(
                    "List persisted inventory resources for one authorized cluster through "
                    "the inventory API."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string(
                            "Cluster id from list_clusters.",
                            max_length=MAX_CLUSTER_ID_LENGTH,
                        ),
                        "resource_type": _string(
                            "Optional resource type filter, for example pod, workload, service, node, namespace, or event.",
                            max_length=80,
                        ),
                        "namespace": _string(
                            "Optional Kubernetes namespace filter.",
                            max_length=MAX_KUBERNETES_NAME_LENGTH,
                        ),
                        "include_deleted": {
                            "type": "boolean",
                            "description": "Include deleted inventory rows when the API has retained them.",
                            "default": False,
                        },
                        "limit": _integer(
                            "Maximum number of resources to return.",
                            minimum=1,
                            maximum=MAX_LIST_RESOURCES_LIMIT,
                            default=DEFAULT_LIST_RESOURCES_LIMIT,
                        ),
                    },
                    required=["cluster_id"],
                ),
                handler=list_resources,
            ),
            McpTool(
                name="list_cluster_workloads",
                title="List Cluster Workloads",
                description=(
                    "List persisted workload inventory rows for one authorized cluster through "
                    "the existing workload inventory API."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string(
                            "Cluster id from list_clusters.",
                            max_length=MAX_CLUSTER_ID_LENGTH,
                        ),
                        "namespace": _string(
                            "Optional Kubernetes namespace filter.",
                            max_length=MAX_KUBERNETES_NAME_LENGTH,
                        ),
                        "limit": _integer(
                            "Maximum number of workloads to return.",
                            minimum=1,
                            maximum=MAX_LIST_RESOURCES_LIMIT,
                            default=DEFAULT_LIST_RESOURCES_LIMIT,
                        ),
                    },
                    required=["cluster_id"],
                ),
                handler=list_cluster_workloads,
            ),
            McpTool(
                name="list_cluster_services",
                title="List Cluster Services",
                description=(
                    "List persisted service inventory rows for one authorized cluster through "
                    "the existing service inventory API."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string(
                            "Cluster id from list_clusters.",
                            max_length=MAX_CLUSTER_ID_LENGTH,
                        ),
                        "namespace": _string(
                            "Optional Kubernetes namespace filter.",
                            max_length=MAX_KUBERNETES_NAME_LENGTH,
                        ),
                        "limit": _integer(
                            "Maximum number of services to return.",
                            minimum=1,
                            maximum=MAX_LIST_RESOURCES_LIMIT,
                            default=DEFAULT_LIST_RESOURCES_LIMIT,
                        ),
                    },
                    required=["cluster_id"],
                ),
                handler=list_cluster_services,
            ),
            McpTool(
                name="list_cluster_events",
                title="List Cluster Events",
                description=(
                    "List persisted event inventory rows for one authorized cluster through "
                    "the existing event inventory API."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string(
                            "Cluster id from list_clusters.",
                            max_length=MAX_CLUSTER_ID_LENGTH,
                        ),
                        "namespace": _string(
                            "Optional Kubernetes namespace filter.",
                            max_length=MAX_KUBERNETES_NAME_LENGTH,
                        ),
                        "limit": _integer(
                            "Maximum number of events to return.",
                            minimum=1,
                            maximum=MAX_LIST_RESOURCES_LIMIT,
                            default=DEFAULT_LIST_RESOURCES_LIMIT,
                        ),
                    },
                    required=["cluster_id"],
                ),
                handler=list_cluster_events,
            ),
            McpTool(
                name="list_helm_releases",
                title="List Helm Releases",
                description=(
                    "List Helm releases inferred from authorized inventory metadata. "
                    "The Gateway does not decode Helm values or manifests."
                ),
                input_schema=_schema(
                    properties={
                        "clusters": _string(
                            "Optional comma-separated cluster filter.",
                            max_length=MAX_FILTER_VALUE_LIST_LENGTH,
                        ),
                        "namespaces": _string(
                            "Optional comma-separated namespace filter.",
                            max_length=MAX_FILTER_VALUE_LIST_LENGTH,
                        ),
                    }
                ),
                handler=list_helm_releases,
            ),
            McpTool(
                name="get_helm_release",
                title="Get Helm Release",
                description=(
                    "Fetch one Helm release detail inferred from authorized inventory "
                    "metadata without exposing Helm values or rendered manifests."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string(
                            "Cluster id from list_clusters.",
                            max_length=MAX_CLUSTER_ID_LENGTH,
                        ),
                        "namespace": _string(
                            "Existing Helm storage namespace.",
                            max_length=MAX_KUBERNETES_NAME_LENGTH,
                        ),
                        "release_name": _string(
                            "Existing Helm release name.",
                            max_length=MAX_KUBERNETES_NAME_LENGTH,
                        ),
                    },
                    required=["cluster_id", "namespace", "release_name"],
                ),
                handler=get_helm_release,
            ),
            McpTool(
                name="list_global_filter_facets",
                title="List Global Filter Facets",
                description=(
                    "List workspace-wide authorized filter suggestions from the existing "
                    "global facets API so AI can choose real visible scopes only."
                ),
                input_schema=_schema(
                    properties={
                        "query": _string("Optional facet search query.", max_length=200),
                        "clusters": _string(
                            "Optional comma-separated cluster filter.", max_length=2048
                        ),
                        "namespaces": _string(
                            "Optional comma-separated namespace filter.", max_length=2048
                        ),
                        "applications": _string(
                            "Optional comma-separated application filter.",
                            max_length=2048,
                        ),
                        "resource_types": _string(
                            "Optional comma-separated resource type filter.",
                            max_length=2048,
                        ),
                        "labels": _string(
                            "Optional comma-separated label filter.", max_length=2048
                        ),
                        "limit": _integer(
                            "Maximum number of values per facet to return.",
                            minimum=1,
                            maximum=MAX_GLOBAL_FILTER_FACET_LIMIT,
                            default=DEFAULT_GLOBAL_FILTER_FACET_LIMIT,
                        ),
                    }
                ),
                handler=list_global_filter_facets,
            ),
            McpTool(
                name="list_resource_filter_facets",
                title="List Resource Filter Facets",
                description=(
                    "List authorized resource filter values for one facet axis through "
                    "the existing resources facet API."
                ),
                input_schema=_schema(
                    properties={
                        "axis": _enum_string(
                            "Facet axis to list.",
                            gateway_facets.RESOURCE_FILTER_FACET_AXES,
                        ),
                        "selected": _string(
                            "Optional comma-separated selected values for the same axis.",
                            max_length=2048,
                        ),
                        "cursor": _string(
                            "Optional cursor returned by the API.",
                            max_length=MAX_FILTER_CURSOR_LENGTH,
                        ),
                        "limit": _integer(
                            "Maximum number of facet values to return.",
                            minimum=1,
                            maximum=MAX_FILTER_FACET_LIMIT,
                            default=DEFAULT_FILTER_FACET_LIMIT,
                        ),
                    },
                    required=["axis"],
                ),
                handler=list_resource_filter_facets,
            ),
            McpTool(
                name="list_resource_label_facets",
                title="List Resource Label Facets",
                description=(
                    "List authorized resource label selectors through the existing "
                    "resource label-facet API."
                ),
                input_schema=_schema(
                    properties={
                        "clusters": _string(
                            "Optional comma-separated cluster filter.", max_length=2048
                        ),
                        "namespaces": _string(
                            "Optional comma-separated namespace filter.", max_length=2048
                        ),
                        "applications": _string(
                            "Optional comma-separated application filter.",
                            max_length=2048,
                        ),
                        "resource_types": _string(
                            "Optional comma-separated resource type filter.",
                            max_length=2048,
                        ),
                        "health": _string(
                            "Optional comma-separated health filter.", max_length=2048
                        ),
                        "labels": _string(
                            "Optional comma-separated label filter.", max_length=2048
                        ),
                        "query": _string("Optional resource search query.", max_length=200),
                        "include_deleted": _optional_boolean(
                            "Include deleted resources when retained by the Gateway."
                        ),
                        "facet_query": _string(
                            "Optional label facet search query.", max_length=200
                        ),
                        "cursor": _string(
                            "Optional cursor returned by the API.",
                            max_length=MAX_FILTER_CURSOR_LENGTH,
                        ),
                        "limit": _integer(
                            "Maximum number of label facets to return.",
                            minimum=1,
                            maximum=MAX_FILTER_FACET_LIMIT,
                            default=DEFAULT_FILTER_FACET_LIMIT,
                        ),
                    }
                ),
                handler=list_resource_label_facets,
            ),
            McpTool(
                name="get_resource_metrics_history",
                title="Get Resource Metrics History",
                description=(
                    "Fetch persisted metric history for existing resource ids through "
                    "the resources metrics-history API. MCP does not query metrics "
                    "storage or clusters directly."
                ),
                input_schema=_schema(
                    properties={
                        "resource_ids": _string_array(
                            "Existing inventory resource ids returned by Gateway APIs.",
                            max_items=MAX_RESOURCE_METRIC_HISTORY_IDS,
                            max_length=MAX_RESOURCE_METRIC_HISTORY_ID_LENGTH,
                        ),
                        "clusters": _string(
                            "Optional comma-separated cluster filter.", max_length=2048
                        ),
                        "namespaces": _string(
                            "Optional comma-separated namespace filter.", max_length=2048
                        ),
                        "applications": _string(
                            "Optional comma-separated application filter.",
                            max_length=2048,
                        ),
                        "resource_types": _string(
                            "Optional comma-separated resource type filter.",
                            max_length=2048,
                        ),
                        "health": _string(
                            "Optional comma-separated health filter.", max_length=2048
                        ),
                        "labels": _string(
                            "Optional comma-separated label filter.", max_length=2048
                        ),
                        "query": _string("Optional resource search query.", max_length=200),
                        "include_deleted": _optional_boolean(
                            "Include deleted resources when retained by the Gateway."
                        ),
                        "snapshot_revision": _optional_integer(
                            "Optional existing inventory snapshot revision to pin.",
                            minimum=1,
                        ),
                        "time_range": _enum_string(
                            "Metric history window.",
                            RESOURCE_METRIC_HISTORY_RANGES,
                            default=DEFAULT_RESOURCE_METRIC_HISTORY_RANGE,
                        ),
                        "limit": _integer(
                            "Maximum number of samples per series to return.",
                            minimum=1,
                            maximum=MAX_RESOURCE_METRIC_HISTORY_LIMIT,
                            default=DEFAULT_RESOURCE_METRIC_HISTORY_LIMIT,
                        ),
                    },
                    required=["resource_ids"],
                ),
                handler=get_resource_metrics_history,
            ),
            McpTool(
                name="get_resource_detail",
                title="Get Resource Detail",
                description=(
                    "Fetch one persisted inventory resource detail plus related resources "
                    "and Kubernetes events from the existing inventory API."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string("Cluster id from list_clusters.", max_length=512),
                        "resource_type": _string(
                            "Resource type returned by list_resources.", max_length=80
                        ),
                        "kind": _string(
                            "Kubernetes kind, for example Pod or Deployment.", max_length=120
                        ),
                        "name": _string("Kubernetes resource name.", max_length=253),
                        "namespace": _string("Namespace for namespaced resources.", max_length=253),
                        "related_limit": _integer(
                            "Maximum related resources to return.",
                            minimum=1,
                            maximum=MAX_RELATED_LIMIT,
                            default=DEFAULT_RELATED_LIMIT,
                        ),
                        "event_limit": _integer(
                            "Maximum related events to return.",
                            minimum=1,
                            maximum=MAX_EVENT_LIMIT,
                            default=DEFAULT_EVENT_LIMIT,
                        ),
                    },
                    required=["cluster_id", "resource_type", "kind", "name"],
                ),
                handler=get_resource_detail,
            ),
            McpTool(
                name="list_recent_incidents",
                title="List Recent Incidents",
                description=(
                    "List sanitized RCA report summaries visible to the authenticated "
                    "Opsia session."
                ),
                input_schema=_schema(
                    properties={
                        "correlation_id": _string(
                            "Optional incident correlation id.", max_length=255
                        ),
                        "since": _string("Optional ISO-8601 lower bound.", max_length=80),
                        "until": _string("Optional ISO-8601 upper bound.", max_length=80),
                        "limit": _integer(
                            "Maximum number of reports to return.",
                            minimum=1,
                            maximum=MAX_QUERY_LIMIT,
                            default=DEFAULT_RECENT_INCIDENT_LIMIT,
                        ),
                        "offset": _integer(
                            "Offset for legacy pagination.",
                            minimum=0,
                            maximum=MAX_LEGACY_OFFSET,
                            default=0,
                        ),
                        "cursor": _string("Optional cursor returned by the API.", max_length=2048),
                    }
                ),
                handler=list_recent_incidents,
            ),
            McpTool(
                name="get_rca_bundle",
                title="Get RCA Bundle",
                description=(
                    "Fetch the existing remediation bundle for an incident correlation id. "
                    "Gateway RCA permissions and response redaction remain the boundary."
                ),
                input_schema=_schema(
                    properties={
                        "correlation_id": _string(
                            "Existing incident correlation id.",
                            max_length=MAX_CORRELATION_ID_LENGTH,
                        ),
                    },
                    required=["correlation_id"],
                ),
                handler=get_rca_bundle,
            ),
            McpTool(
                name="list_incident_recent_changes",
                title="List Incident Recent Changes",
                description=(
                    "List GitOps changes associated with one existing RCA incident workload "
                    "scope through the RCA recent-changes API."
                ),
                input_schema=_schema(
                    properties={
                        "incident_id": _string(
                            "Existing RCA incident id.",
                            max_length=MAX_INCIDENT_ID_LENGTH,
                        ),
                        "limit": _integer(
                            "Maximum number of recent changes to return.",
                            minimum=1,
                            maximum=MAX_RCA_RECENT_CHANGE_LIMIT,
                            default=DEFAULT_RCA_RECENT_CHANGE_LIMIT,
                        ),
                    },
                    required=["incident_id"],
                ),
                handler=list_incident_recent_changes,
            ),
            McpTool(
                name="list_rca_rules",
                title="List RCA Rules",
                description=(
                    "List the existing RCA rule catalog so AI can explain which symptoms "
                    "and candidate checks are supported without inventing rules."
                ),
                input_schema=_schema(properties={}),
                handler=list_rca_rules,
            ),
            McpTool(
                name="list_dead_letters",
                title="List Dead Letters",
                description=(
                    "List event dead-letter queue entries through the existing Gateway "
                    "admin API. MCP does not inspect the event bus or storage directly."
                ),
                input_schema=_schema(
                    properties={
                        "limit": _integer(
                            "Maximum number of dead-letter entries to return.",
                            minimum=1,
                            maximum=MAX_DEAD_LETTER_LIMIT,
                            default=DEFAULT_DEAD_LETTER_LIMIT,
                        ),
                    }
                ),
                handler=list_dead_letters,
            ),
            McpTool(
                name="list_rca_issues",
                title="List RCA Issues",
                description=(
                    "List the current RCA issue queue through the existing dashboard API "
                    "using the authenticated session's cluster/RCA permissions."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string(
                            "Optional cluster id from list_clusters.",
                            max_length=512,
                        ),
                        "limit": _integer(
                            "Maximum number of RCA issues to return.",
                            minimum=1,
                            maximum=MAX_RCA_ISSUE_LIMIT,
                            default=DEFAULT_RCA_ISSUE_LIMIT,
                        ),
                    }
                ),
                handler=list_rca_issues,
            ),
            McpTool(
                name="list_issue_filter_facets",
                title="List Issue Filter Facets",
                description=(
                    "List authorized issue filter values for one facet axis through "
                    "the existing issue facet API."
                ),
                input_schema=_schema(
                    properties={
                        "axis": _enum_string(
                            "Issue facet axis to list.",
                            gateway_facets.ISSUE_FILTER_FACET_AXES,
                        ),
                        "clusters": _string(
                            "Optional comma-separated cluster filter.", max_length=2048
                        ),
                        "namespaces": _string(
                            "Optional comma-separated namespace filter.", max_length=2048
                        ),
                        "applications": _string(
                            "Optional comma-separated application filter.",
                            max_length=2048,
                        ),
                        "labels": _string(
                            "Optional comma-separated label filter.", max_length=2048
                        ),
                        "severity": _string("Optional issue severity filter.", max_length=120),
                        "status": _string("Optional issue status filter.", max_length=120),
                        "environment": _string(
                            "Optional issue environment filter.", max_length=120
                        ),
                        "query": _string("Optional issue search query.", max_length=200),
                        "facet_query": _string(
                            "Optional issue facet search query.", max_length=200
                        ),
                        "cursor": _string(
                            "Optional cursor returned by the API.",
                            max_length=MAX_FILTER_CURSOR_LENGTH,
                        ),
                        "limit": _integer(
                            "Maximum number of facet values to return.",
                            minimum=1,
                            maximum=MAX_FILTER_FACET_LIMIT,
                            default=DEFAULT_FILTER_FACET_LIMIT,
                        ),
                    },
                    required=["axis"],
                ),
                handler=list_issue_filter_facets,
            ),
            McpTool(
                name="list_issue_label_facets",
                title="List Issue Label Facets",
                description=(
                    "List authorized issue label selectors through the existing issue "
                    "label-facet API."
                ),
                input_schema=_schema(
                    properties={
                        "clusters": _string(
                            "Optional comma-separated cluster filter.", max_length=2048
                        ),
                        "namespaces": _string(
                            "Optional comma-separated namespace filter.", max_length=2048
                        ),
                        "applications": _string(
                            "Optional comma-separated application filter.",
                            max_length=2048,
                        ),
                        "labels": _string(
                            "Optional comma-separated label filter.", max_length=2048
                        ),
                        "severity": _string("Optional issue severity filter.", max_length=120),
                        "status": _string("Optional issue status filter.", max_length=120),
                        "environment": _string(
                            "Optional issue environment filter.", max_length=120
                        ),
                        "query": _string("Optional issue search query.", max_length=200),
                        "facet_query": _string(
                            "Optional issue label facet search query.",
                            max_length=200,
                        ),
                        "cursor": _string(
                            "Optional cursor returned by the API.",
                            max_length=MAX_FILTER_CURSOR_LENGTH,
                        ),
                        "limit": _integer(
                            "Maximum number of label facets to return.",
                            minimum=1,
                            maximum=MAX_FILTER_FACET_LIMIT,
                            default=DEFAULT_FILTER_FACET_LIMIT,
                        ),
                    }
                ),
                handler=list_issue_label_facets,
            ),
            McpTool(
                name="get_rca_incident",
                title="Get RCA Incident",
                description=(
                    "Fetch one RCA incident projection through the existing dashboard API."
                ),
                input_schema=_schema(
                    properties={
                        "incident_id": _string("Existing RCA incident id.", max_length=512),
                        "cluster_id": _string(
                            "Optional cluster id to constrain authorization and lookup.",
                            max_length=512,
                        ),
                    },
                    required=["incident_id"],
                ),
                handler=get_rca_incident,
            ),
            McpTool(
                name="list_resource_issues",
                title="List Resource Issues",
                description=(
                    "List RCA issues attached to one exact resource through the existing "
                    "dashboard API. The Gateway verifies inventory and RCA read access."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string("Cluster id from list_clusters.", max_length=512),
                        "kind": _string("Existing Kubernetes resource kind.", max_length=253),
                        "name": _string("Existing Kubernetes resource name.", max_length=253),
                        "namespace": _string(
                            "Optional namespace for namespaced resources.", max_length=253
                        ),
                        "limit": _integer(
                            "Maximum number of resource issues to return.",
                            minimum=1,
                            maximum=MAX_RCA_ISSUE_LIMIT,
                            default=DEFAULT_RESOURCE_ISSUE_LIMIT,
                        ),
                    },
                    required=["cluster_id", "kind", "name"],
                ),
                handler=list_resource_issues,
            ),
            McpTool(
                name="list_evidence_windows",
                title="List Evidence Windows",
                description=(
                    "List persisted evidence windows visible to the authenticated Opsia "
                    "session so a later get_log_evidence call can use an existing evidence_key."
                ),
                input_schema=_schema(
                    properties={
                        "limit": _integer(
                            "Maximum number of evidence windows to return.",
                            minimum=1,
                            maximum=MAX_QUERY_LIMIT,
                            default=DEFAULT_RECENT_INCIDENT_LIMIT,
                        ),
                        "offset": _integer(
                            "Offset for pagination.",
                            minimum=0,
                            maximum=MAX_LEGACY_OFFSET,
                            default=0,
                        ),
                    }
                ),
                handler=list_evidence_windows,
            ),
            McpTool(
                name="get_log_evidence",
                title="Get Log Evidence",
                description=(
                    "Fetch the logs source from a persisted evidence window. The evidence_key "
                    "must come from existing Opsia evidence or RCA data."
                ),
                input_schema=_schema(
                    properties={
                        "evidence_key": _string("Persisted evidence window key.", max_length=512),
                    },
                    required=["evidence_key"],
                ),
                handler=get_log_evidence,
            ),
            McpTool(
                name="get_command_status",
                title="Get Command Status",
                description=(
                    "Fetch command status and agent result details through the existing "
                    "command status API."
                ),
                input_schema=_schema(
                    properties={
                        "command_id": _string("Existing command id.", max_length=200),
                    },
                    required=["command_id"],
                ),
                handler=get_command_status,
            ),
            McpTool(
                name="list_alert_rules",
                title="List Alert Rules",
                description=(
                    "List existing alert rules through the admin alert-rule API before "
                    "creating or updating a rule."
                ),
                input_schema=_schema(properties={}),
                handler=list_alert_rules,
            ),
            McpTool(
                name="get_alert_rule",
                title="Get Alert Rule",
                description=(
                    "Return one alert rule by filtering the existing alert-rule list API; "
                    "no direct database lookup is used."
                ),
                input_schema=_schema(
                    properties={
                        "rule_id": _string("Existing alert rule id.", max_length=120),
                    },
                    required=["rule_id"],
                ),
                handler=get_alert_rule,
            ),
            McpTool(
                name="list_alert_channels",
                title="List Alert Channels",
                description=(
                    "List existing alert delivery channels through the Gateway admin API "
                    "so alert-rule proposals can reference real configured channels."
                ),
                input_schema=_schema(properties={}),
                handler=list_alert_channels,
            ),
            McpTool(
                name="get_alert_channel",
                title="Get Alert Channel",
                description=(
                    "Fetch one existing alert channel by filtering the Gateway's channel "
                    "list response. MCP does not read alert storage directly."
                ),
                input_schema=_schema(
                    properties={
                        "channel_id": _string("Existing alert channel id.", max_length=120),
                    },
                    required=["channel_id"],
                ),
                handler=get_alert_channel,
            ),
            McpTool(
                name="list_alert_events",
                title="List Alert Events",
                description=(
                    "List existing alert events through the alert-event API before "
                    "acknowledging or promoting an event."
                ),
                input_schema=_schema(
                    properties={
                        "from_time": _string("Optional ISO-8601 lower bound.", max_length=80),
                        "to_time": _string("Optional ISO-8601 upper bound.", max_length=80),
                        "rule_id": _string("Optional existing alert rule id.", max_length=120),
                        "severity": _string("Optional alert event severity.", max_length=40),
                        "status": _string("Optional alert event status.", max_length=40),
                        "limit": _integer(
                            "Maximum number of alert events to return.",
                            minimum=1,
                            maximum=MAX_ALERT_EVENT_LIMIT,
                            default=DEFAULT_ALERT_EVENT_LIMIT,
                        ),
                    }
                ),
                handler=list_alert_events,
            ),
            McpTool(
                name="get_recovery_plan",
                title="Get Recovery Plan",
                description=(
                    "Fetch the existing RCA recovery plan for an incident correlation id "
                    "before requesting one of its actions."
                ),
                input_schema=_schema(
                    properties={
                        "correlation_id": _string(
                            "Existing incident correlation id.",
                            max_length=2048,
                        ),
                    },
                    required=["correlation_id"],
                ),
                handler=get_recovery_plan,
            ),
            McpTool(
                name="list_applications",
                title="List Applications",
                description=(
                    "List applications visible to the authenticated Opsia session through "
                    "the product applications API."
                ),
                input_schema=_schema(
                    properties={
                        "clusters": _string(
                            "Optional comma-separated cluster filter.", max_length=2048
                        ),
                        "namespaces": _string(
                            "Optional comma-separated namespace filter.", max_length=2048
                        ),
                        "applications": _string(
                            "Optional comma-separated application filter.", max_length=2048
                        ),
                        "labels": _string(
                            "Optional comma-separated label filter.", max_length=2048
                        ),
                        "environment": _string(
                            "Optional application environment filter.", max_length=120
                        ),
                        "status": _string("Optional application status filter.", max_length=120),
                        "pending_promotion": _string(
                            "Optional pending promotion filter accepted by the Gateway.",
                            max_length=120,
                        ),
                        "query": _string("Optional application search query.", max_length=200),
                        "limit": _integer(
                            "Maximum number of applications to return.",
                            minimum=1,
                            maximum=MAX_APPLICATION_LIMIT,
                            default=DEFAULT_APPLICATION_LIMIT,
                        ),
                    }
                ),
                handler=list_applications,
            ),
            McpTool(
                name="list_application_filter_facets",
                title="List Application Filter Facets",
                description=(
                    "List authorized application filter values for one facet axis through "
                    "the existing application facet API."
                ),
                input_schema=_schema(
                    properties={
                        "axis": _enum_string(
                            "Application facet axis to list.",
                            gateway_facets.APPLICATION_FILTER_FACET_AXES,
                        ),
                        "clusters": _string(
                            "Optional comma-separated cluster filter.", max_length=2048
                        ),
                        "namespaces": _string(
                            "Optional comma-separated namespace filter.", max_length=2048
                        ),
                        "applications": _string(
                            "Optional comma-separated application filter.",
                            max_length=2048,
                        ),
                        "labels": _string(
                            "Optional comma-separated label filter.", max_length=2048
                        ),
                        "environment": _string(
                            "Optional application environment filter.", max_length=120
                        ),
                        "status": _string("Optional application status filter.", max_length=120),
                        "pending_promotion": _string(
                            "Optional pending promotion filter accepted by the Gateway.",
                            max_length=120,
                        ),
                        "query": _string("Optional application search query.", max_length=200),
                        "facet_query": _string(
                            "Optional application facet search query.",
                            max_length=200,
                        ),
                        "cursor": _string(
                            "Optional cursor returned by the API.",
                            max_length=MAX_FILTER_CURSOR_LENGTH,
                        ),
                        "limit": _integer(
                            "Maximum number of facet values to return.",
                            minimum=1,
                            maximum=MAX_FILTER_FACET_LIMIT,
                            default=DEFAULT_FILTER_FACET_LIMIT,
                        ),
                    },
                    required=["axis"],
                ),
                handler=list_application_filter_facets,
            ),
            McpTool(
                name="list_application_label_facets",
                title="List Application Label Facets",
                description=(
                    "List authorized application label selectors through the existing "
                    "application label-facet API."
                ),
                input_schema=_schema(
                    properties={
                        "clusters": _string(
                            "Optional comma-separated cluster filter.", max_length=2048
                        ),
                        "namespaces": _string(
                            "Optional comma-separated namespace filter.", max_length=2048
                        ),
                        "applications": _string(
                            "Optional comma-separated application filter.",
                            max_length=2048,
                        ),
                        "labels": _string(
                            "Optional comma-separated label filter.", max_length=2048
                        ),
                        "environment": _string(
                            "Optional application environment filter.", max_length=120
                        ),
                        "status": _string("Optional application status filter.", max_length=120),
                        "pending_promotion": _string(
                            "Optional pending promotion filter accepted by the Gateway.",
                            max_length=120,
                        ),
                        "query": _string("Optional application search query.", max_length=200),
                        "facet_query": _string(
                            "Optional application label facet search query.",
                            max_length=200,
                        ),
                        "cursor": _string(
                            "Optional cursor returned by the API.",
                            max_length=MAX_FILTER_CURSOR_LENGTH,
                        ),
                        "limit": _integer(
                            "Maximum number of label facets to return.",
                            minimum=1,
                            maximum=MAX_FILTER_FACET_LIMIT,
                            default=DEFAULT_FILTER_FACET_LIMIT,
                        ),
                    }
                ),
                handler=list_application_label_facets,
            ),
            McpTool(
                name="get_application_detail",
                title="Get Application Detail",
                description=(
                    "Fetch one application detail projection through the existing applications API."
                ),
                input_schema=_schema(
                    properties={
                        "application_id": _string("Existing application id.", max_length=200),
                        "instance": _string("Optional application instance id.", max_length=200),
                        "workload": _string(
                            "Optional workload key within the application.", max_length=128
                        ),
                    },
                    required=["application_id"],
                ),
                handler=get_application_detail,
            ),
            McpTool(
                name="get_application_drift",
                title="Get Application Drift",
                description=(
                    "Return the existing drift projection for one authorized application "
                    "and optional instance."
                ),
                input_schema=_schema(
                    properties={
                        "application_id": _string("Existing application id.", max_length=200),
                        "instance": _string("Optional application instance id.", max_length=200),
                    },
                    required=["application_id"],
                ),
                handler=get_application_drift,
            ),
            McpTool(
                name="list_application_deployments",
                title="List Application Deployments",
                description=(
                    "List deployment history for one authorized application through the "
                    "existing application deployment API."
                ),
                input_schema=_schema(
                    properties={
                        "application_id": _string("Existing application id.", max_length=200),
                        "instance": _string("Optional application instance id.", max_length=200),
                        "limit": _integer(
                            "Maximum number of deployments to return.",
                            minimum=1,
                            maximum=MAX_APPLICATION_DEPLOYMENT_LIMIT,
                            default=DEFAULT_APPLICATION_DEPLOYMENT_LIMIT,
                        ),
                    },
                    required=["application_id"],
                ),
                handler=list_application_deployments,
            ),
            McpTool(
                name="list_audit_timeline",
                title="List Audit Timeline",
                description=(
                    "List an authorized audit timeline for an existing correlation id "
                    "through the audit API."
                ),
                input_schema=_schema(
                    properties={
                        "correlation_id": _string("Existing correlation id.", max_length=2048),
                        "cursor": _string(
                            "Optional cursor returned by the audit API.", max_length=2048
                        ),
                        "limit": _integer(
                            "Maximum number of audit items to return.",
                            minimum=1,
                            maximum=MAX_AUDIT_TIMELINE_LIMIT,
                            default=DEFAULT_AUDIT_TIMELINE_LIMIT,
                        ),
                    },
                    required=["correlation_id"],
                ),
                handler=list_audit_timeline,
            ),
            McpTool(
                name="list_workflow_runs",
                title="List Workflow Runs",
                description=(
                    "List workflow-like runs from the existing application-runs API when "
                    "application_id is supplied, otherwise from release-runs."
                ),
                input_schema=_schema(
                    properties={
                        "application_id": _string(
                            "Optional application id for application workflow runs.",
                            max_length=200,
                        ),
                        "plan_id": _string(
                            "Optional release plan id for release runs.", max_length=160
                        ),
                        "status": _string("Optional release run status filter.", max_length=80),
                        "attention_only": _boolean(
                            "Only release runs needing attention.", default=False
                        ),
                        "active_only": _boolean("Only active release runs.", default=False),
                        "limit": _integer(
                            "Maximum number of runs to return.",
                            minimum=1,
                            maximum=MAX_APPLICATION_WORKFLOW_RUN_LIMIT,
                            default=DEFAULT_RELEASE_RUN_LIMIT,
                        ),
                    }
                ),
                handler=list_workflow_runs,
            ),
            McpTool(
                name="get_workflow_run",
                title="Get Workflow Run",
                description=(
                    "Fetch a release run by id, or filter one application run from the "
                    "existing application-runs API when application_id is supplied."
                ),
                input_schema=_schema(
                    properties={
                        "run_id": _string(
                            "Existing release run id or workflow_run_id.", max_length=200
                        ),
                        "application_id": _string(
                            "Optional application id when run_id is an application workflow_run_id.",
                            max_length=200,
                        ),
                    },
                    required=["run_id"],
                ),
                handler=get_workflow_run,
            ),
            McpTool(
                name="get_release_run_report",
                title="Get Release Run Report",
                description=(
                    "Fetch the existing release-run report projection for one authorized "
                    "release run."
                ),
                input_schema=_schema(
                    properties={
                        "run_id": _string("Existing release run id.", max_length=200),
                    },
                    required=["run_id"],
                ),
                handler=get_release_run_report,
            ),
            McpTool(
                name="list_release_plans",
                title="List Release Plans",
                description=(
                    "List release plans through the existing release-flow API. The "
                    "Gateway enforces workspace and application read permissions."
                ),
                input_schema=_schema(
                    properties={
                        "limit": _integer(
                            "Maximum number of release plans to return.",
                            minimum=1,
                            maximum=MAX_RELEASE_PLAN_LIMIT,
                            default=DEFAULT_RELEASE_PLAN_LIMIT,
                        ),
                    }
                ),
                handler=list_release_plans,
            ),
            McpTool(
                name="get_release_plan",
                title="Get Release Plan",
                description=(
                    "Fetch one release plan through the existing release-flow API before "
                    "creating, starting, or reviewing related runs."
                ),
                input_schema=_schema(
                    properties={
                        "plan_id": _string("Existing release plan id.", max_length=160),
                    },
                    required=["plan_id"],
                ),
                handler=get_release_plan,
            ),
            McpTool(
                name="get_release_run_summary",
                title="Get Release Run Summary",
                description=(
                    "Return the existing release-run summary roll-up, optionally scoped "
                    "to one release plan."
                ),
                input_schema=_schema(
                    properties={
                        "plan_id": _string("Optional existing release plan id.", max_length=160),
                    }
                ),
                handler=get_release_run_summary,
            ),
            McpTool(
                name="list_release_audit",
                title="List Release Audit",
                description=(
                    "List release-flow audit events through the existing release audit "
                    "API. This is read-only and uses Gateway permission checks."
                ),
                input_schema=_schema(
                    properties={
                        "plan_id": _string("Optional existing release plan id.", max_length=160),
                        "run_id": _string("Optional existing release run id.", max_length=200),
                        "event_type": _string("Optional release audit event type.", max_length=120),
                        "limit": _integer(
                            "Maximum number of release audit events to return.",
                            minimum=1,
                            maximum=MAX_RELEASE_AUDIT_LIMIT,
                            default=DEFAULT_RELEASE_AUDIT_LIMIT,
                        ),
                    }
                ),
                handler=list_release_audit,
            ),
            McpTool(
                name="list_pending_approvals",
                title="List Pending Approvals",
                description=(
                    "Find pending approvals from existing application workflow runs or "
                    "waiting release runs. MCP does not query approval tables directly."
                ),
                input_schema=_schema(
                    properties={
                        "application_id": _string(
                            "Optional application id to search application workflow approvals.",
                            max_length=200,
                        ),
                        "limit": _integer(
                            "Maximum number of runs to inspect.",
                            minimum=1,
                            maximum=MAX_APPLICATION_WORKFLOW_RUN_LIMIT,
                            default=DEFAULT_RELEASE_RUN_LIMIT,
                        ),
                    }
                ),
                handler=list_pending_approvals,
            ),
            McpTool(
                name="list_gitops_filter_facets",
                title="List GitOps Filter Facets",
                description=(
                    "List authorized GitOps change filter values for one facet axis "
                    "through the existing GitOps facet API."
                ),
                input_schema=_schema(
                    properties={
                        "axis": _enum_string(
                            "GitOps facet axis to list.",
                            gateway_facets.GITOPS_FILTER_FACET_AXES,
                        ),
                        "clusters": _string(
                            "Optional comma-separated cluster filter.", max_length=2048
                        ),
                        "namespaces": _string(
                            "Optional comma-separated namespace filter.", max_length=2048
                        ),
                        "applications": _string(
                            "Optional comma-separated application filter.",
                            max_length=2048,
                        ),
                        "labels": _string(
                            "Optional comma-separated label filter.", max_length=2048
                        ),
                        "environment": _string(
                            "Optional GitOps environment filter.", max_length=120
                        ),
                        "approval": _string("Optional GitOps approval filter.", max_length=120),
                        "change_type": _string(
                            "Optional GitOps change type filter.", max_length=120
                        ),
                        "query": _string("Optional GitOps search query.", max_length=200),
                        "facet_query": _string(
                            "Optional GitOps facet search query.", max_length=200
                        ),
                        "cursor": _string(
                            "Optional cursor returned by the API.",
                            max_length=MAX_FILTER_CURSOR_LENGTH,
                        ),
                        "limit": _integer(
                            "Maximum number of facet values to return.",
                            minimum=1,
                            maximum=MAX_FILTER_FACET_LIMIT,
                            default=DEFAULT_FILTER_FACET_LIMIT,
                        ),
                    },
                    required=["axis"],
                ),
                handler=list_gitops_filter_facets,
            ),
            McpTool(
                name="get_resource_capabilities",
                title="Get Resource Capabilities",
                description=(
                    "Return the authorized actions currently available for one existing "
                    "inventory resource key."
                ),
                input_schema=_schema(
                    properties={
                        "resource": _string("Existing inventory resource key.", max_length=255),
                    },
                    required=["resource"],
                ),
                handler=get_resource_capabilities,
            ),
            McpTool(
                name="get_resource_graph",
                title="Get Resource Graph",
                description=(
                    "Return the authorized resource graph snapshot from the existing "
                    "filter graph API using only Gateway-supported filters."
                ),
                input_schema=_schema(
                    properties={
                        "clusters": _string(
                            "Optional comma-separated cluster filter.", max_length=2048
                        ),
                        "namespaces": _string(
                            "Optional comma-separated namespace filter.", max_length=2048
                        ),
                        "applications": _string(
                            "Optional comma-separated application filter.",
                            max_length=2048,
                        ),
                        "resource_types": _string(
                            "Optional comma-separated resource type filter.",
                            max_length=2048,
                        ),
                        "health": _string(
                            "Optional comma-separated health filter.", max_length=2048
                        ),
                        "labels": _string(
                            "Optional comma-separated label filter.", max_length=2048
                        ),
                        "query": _string("Optional resource graph search query.", max_length=200),
                        "include_deleted": _optional_boolean(
                            "Include deleted resources when retained by the Gateway."
                        ),
                        "snapshot_revision": _optional_integer(
                            "Optional existing inventory snapshot revision to pin.",
                            minimum=1,
                        ),
                        "max_nodes": _optional_integer(
                            "Maximum graph nodes to return.",
                            minimum=1,
                            maximum=MAX_GRAPH_NODE_LIMIT,
                        ),
                        "max_edges": _optional_integer(
                            "Maximum graph edges to return.",
                            minimum=1,
                            maximum=MAX_GRAPH_EDGE_LIMIT,
                        ),
                    }
                ),
                handler=get_resource_graph,
            ),
            McpTool(
                name="list_recent_changes",
                title="List Recent Changes",
                description=(
                    "List an authorized bounded change timeline through the existing changes API."
                ),
                input_schema=_schema(
                    properties={
                        "from_ms": _integer(
                            "Inclusive epoch-millisecond lower bound.",
                            minimum=0,
                            maximum=MAX_EPOCH_MILLISECONDS,
                        ),
                        "to_ms": _integer(
                            "Exclusive epoch-millisecond upper bound.",
                            minimum=1,
                            maximum=MAX_EPOCH_MILLISECONDS,
                        ),
                        "bucket_ms": _integer(
                            "Timeline bucket size in milliseconds.",
                            minimum=MIN_CHANGE_BUCKET_MS,
                            maximum=MAX_CHANGE_BUCKET_MS,
                        ),
                        "clusters": _string(
                            "Optional comma-separated cluster filter.", max_length=2048
                        ),
                        "namespaces": _string(
                            "Optional comma-separated namespace filter.", max_length=2048
                        ),
                        "applications": _string(
                            "Optional comma-separated application filter.",
                            max_length=2048,
                        ),
                        "resource_types": _string(
                            "Optional comma-separated resource type filter.",
                            max_length=2048,
                        ),
                        "health": _string(
                            "Optional comma-separated health filter.", max_length=2048
                        ),
                        "labels": _string(
                            "Optional comma-separated label filter.", max_length=2048
                        ),
                        "query": _string("Optional resource search query.", max_length=200),
                    },
                    required=["from_ms", "to_ms", "bucket_ms"],
                ),
                handler=list_recent_changes,
            ),
            McpTool(
                name="list_metric_query_presets",
                title="List Metric Query Presets",
                description=(
                    "List existing metric query presets for one authorized cluster before "
                    "running a preset."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string("Cluster id from list_clusters.", max_length=512),
                    },
                    required=["cluster_id"],
                ),
                handler=list_metric_query_presets,
            ),
            McpTool(
                name="list_metric_widgets",
                title="List Metric Widgets",
                description=(
                    "List saved metric widgets for one authorized cluster through the "
                    "existing dashboard API."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string("Cluster id from list_clusters.", max_length=512),
                    },
                    required=["cluster_id"],
                ),
                handler=list_metric_widgets,
            ),
            McpTool(
                name="get_metric_widget",
                title="Get Metric Widget",
                description=(
                    "Fetch one metric widget by filtering the Gateway's metric widget "
                    "list response for the requested cluster."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string("Cluster id from list_clusters.", max_length=512),
                        "widget_id": _string("Existing metric widget id.", max_length=120),
                    },
                    required=["cluster_id", "widget_id"],
                ),
                handler=get_metric_widget,
            ),
            McpTool(
                name="run_metric_query_preset",
                title="Run Metric Query Preset",
                description=(
                    "Dry-run or queue an existing metric preset through the Gateway. The "
                    "Gateway records a read-only agent debug command and returns command_id."
                ),
                input_schema=_schema(
                    properties={
                        "cluster_id": _string("Cluster id from list_clusters.", max_length=512),
                        "preset_id": _string("Existing metric query preset id.", max_length=120),
                        "dry_run": _boolean(
                            "When true, return only a proposal and do not queue the query.",
                            default=True,
                        ),
                        "approval_confirmed": _boolean(
                            "Must be true with dry_run=false after the user has approved the query.",
                            default=False,
                        ),
                    },
                    required=["cluster_id", "preset_id"],
                ),
                handler=run_metric_query_preset,
                annotations=WRITE_TOOL_ANNOTATIONS,
            ),
            McpTool(
                name="create_alert_rule",
                title="Create Alert Rule",
                description=(
                    "Dry-run or submit an alert rule through the existing admin alert-rule "
                    "API. The tool does not bypass Gateway admin-session checks."
                ),
                input_schema=_schema(
                    properties={
                        "payload": _object(
                            "Existing AlertRuleCreateRequest body from the Opsia API contract."
                        ),
                        "dry_run": _boolean(
                            "When true, return only a proposal and do not call the Gateway.",
                            default=True,
                        ),
                        "approval_confirmed": _boolean(
                            "Must be true with dry_run=false after the user has approved the proposal.",
                            default=False,
                        ),
                    },
                    required=["payload"],
                ),
                handler=create_alert_rule,
                annotations=WRITE_TOOL_ANNOTATIONS,
            ),
            McpTool(
                name="request_recovery_action",
                title="Request Recovery Action",
                description=(
                    "Dry-run or submit an existing RCA recovery action selection through "
                    "the Gateway. The selected plan/action must already exist."
                ),
                input_schema=_schema(
                    properties={
                        "plan_id": _string(
                            "Existing recovery plan id. Provide either plan_id or correlation_id.",
                            max_length=2048,
                        ),
                        "correlation_id": _string(
                            "Existing incident correlation id. Provide either correlation_id or plan_id.",
                            max_length=2048,
                        ),
                        "expected_plan_id": _string(
                            "Required with correlation_id so the Gateway can reject stale selections.",
                            max_length=2048,
                        ),
                        "action_id": _string(
                            "Existing recovery action candidate id from the recovery plan.",
                            max_length=2048,
                        ),
                        "reason": _string(
                            "Optional user-visible reason recorded by the existing API.",
                            max_length=500,
                        ),
                        "dry_run": _boolean(
                            "When true, return only a proposal and do not call the Gateway.",
                            default=True,
                        ),
                        "approval_confirmed": _boolean(
                            "Must be true with dry_run=false after the user has approved the proposal.",
                            default=False,
                        ),
                    },
                    required=["action_id"],
                ),
                handler=request_recovery_action,
                annotations=WRITE_TOOL_ANNOTATIONS,
            ),
            McpTool(
                name="create_command_request",
                title="Create Command Request",
                description=(
                    "Dry-run or submit a manual command request through the existing command "
                    "API. MCP refuses direct-execution confirmation flags."
                ),
                input_schema=_schema(
                    properties={
                        "payload": _object(
                            "Existing CommandRequest body from the Opsia API contract."
                        ),
                        "dry_run": _boolean(
                            "When true, return only a proposal and do not call the Gateway.",
                            default=True,
                        ),
                        "approval_confirmed": _boolean(
                            "Must be true with dry_run=false after the user has approved the proposal.",
                            default=False,
                        ),
                    },
                    required=["payload"],
                ),
                handler=create_command_request,
                annotations=WRITE_TOOL_ANNOTATIONS,
            ),
            McpTool(
                name="approve_or_reject_workflow",
                title="Approve Or Reject Workflow",
                description=(
                    "Dry-run or submit an approval grant/reject decision through the existing "
                    "approval API. The Gateway checks approval state and deployment access."
                ),
                input_schema=_schema(
                    properties={
                        "approval_id": _string("Existing approval id.", max_length=2048),
                        "decision": {
                            "type": "string",
                            "description": "Approval decision to send to the Gateway.",
                            "enum": ["grant", "reject"],
                        },
                        "reason": _string(
                            "Optional user-visible reason recorded with the decision.",
                            max_length=500,
                        ),
                        "dry_run": _boolean(
                            "When true, return only a proposal and do not call the Gateway.",
                            default=True,
                        ),
                        "approval_confirmed": _boolean(
                            "Must be true with dry_run=false after the user has approved the proposal.",
                            default=False,
                        ),
                    },
                    required=["approval_id", "decision"],
                ),
                handler=approve_or_reject_workflow,
                annotations=WRITE_TOOL_ANNOTATIONS,
            ),
            McpTool(
                name="update_alert_rule",
                title="Update Alert Rule",
                description=(
                    "Dry-run or submit a partial alert-rule update through the existing "
                    "admin alert-rule PATCH API."
                ),
                input_schema=_schema(
                    properties={
                        "rule_id": _string("Existing alert rule id.", max_length=120),
                        "payload": _object(
                            "Existing AlertRulePatchRequest body from the Opsia API contract."
                        ),
                        "dry_run": _boolean(
                            "When true, return only a proposal and do not call the Gateway.",
                            default=True,
                        ),
                        "approval_confirmed": _boolean(
                            "Must be true with dry_run=false after the user has approved the proposal.",
                            default=False,
                        ),
                    },
                    required=["rule_id", "payload"],
                ),
                handler=update_alert_rule,
                annotations=WRITE_TOOL_ANNOTATIONS,
            ),
            McpTool(
                name="disable_alert_rule",
                title="Disable Alert Rule",
                description=(
                    "Dry-run or disable an alert rule by sending enabled=false through "
                    "the existing alert-rule PATCH API."
                ),
                input_schema=_schema(
                    properties={
                        "rule_id": _string("Existing alert rule id.", max_length=120),
                        "dry_run": _boolean(
                            "When true, return only a proposal and do not call the Gateway.",
                            default=True,
                        ),
                        "approval_confirmed": _boolean(
                            "Must be true with dry_run=false after the user has approved the proposal.",
                            default=False,
                        ),
                    },
                    required=["rule_id"],
                ),
                handler=disable_alert_rule,
                annotations=WRITE_TOOL_ANNOTATIONS,
            ),
            McpTool(
                name="cancel_command_request",
                title="Cancel Command Request",
                description=(
                    "Dry-run or request command cancellation through the existing command "
                    "control API with a caller-supplied idempotency key."
                ),
                input_schema=_schema(
                    properties={
                        "command_id": _string("Existing command id.", max_length=200),
                        "idempotency_key": _string(
                            "Stable key for this cancel request; Gateway uses it for idempotency.",
                            max_length=200,
                        ),
                        "reason": _string("Optional cancellation reason.", max_length=500),
                        "dry_run": _boolean(
                            "When true, return only a proposal and do not call the Gateway.",
                            default=True,
                        ),
                        "approval_confirmed": _boolean(
                            "Must be true with dry_run=false after the user has approved the proposal.",
                            default=False,
                        ),
                    },
                    required=["command_id", "idempotency_key"],
                ),
                handler=cancel_command_request,
                annotations=WRITE_TOOL_ANNOTATIONS,
            ),
            McpTool(
                name="retry_command_request",
                title="Retry Command Request",
                description=(
                    "Dry-run or request command retry through the existing command "
                    "control API with a caller-supplied idempotency key."
                ),
                input_schema=_schema(
                    properties={
                        "command_id": _string("Existing command id.", max_length=200),
                        "idempotency_key": _string(
                            "Stable key for this retry request; Gateway uses it for idempotency.",
                            max_length=200,
                        ),
                        "reason": _string("Optional retry reason.", max_length=500),
                        "dry_run": _boolean(
                            "When true, return only a proposal and do not call the Gateway.",
                            default=True,
                        ),
                        "approval_confirmed": _boolean(
                            "Must be true with dry_run=false after the user has approved the proposal.",
                            default=False,
                        ),
                    },
                    required=["command_id", "idempotency_key"],
                ),
                handler=retry_command_request,
                annotations=WRITE_TOOL_ANNOTATIONS,
            ),
            McpTool(
                name="ack_alert_event",
                title="Ack Alert Event",
                description=(
                    "Dry-run or acknowledge an existing alert event through the Gateway. "
                    "The alert API records actor and state."
                ),
                input_schema=_schema(
                    properties={
                        "event_id": _string("Existing alert event id.", max_length=120),
                        "dry_run": _boolean(
                            "When true, return only a proposal and do not call the Gateway.",
                            default=True,
                        ),
                        "approval_confirmed": _boolean(
                            "Must be true with dry_run=false after the user has approved the proposal.",
                            default=False,
                        ),
                    },
                    required=["event_id"],
                ),
                handler=ack_alert_event,
                annotations=WRITE_TOOL_ANNOTATIONS,
            ),
            McpTool(
                name="promote_alert_incident",
                title="Promote Alert Incident",
                description=(
                    "Dry-run or promote an existing alert event to an incident through "
                    "the Gateway alert API."
                ),
                input_schema=_schema(
                    properties={
                        "event_id": _string("Existing alert event id.", max_length=120),
                        "dry_run": _boolean(
                            "When true, return only a proposal and do not call the Gateway.",
                            default=True,
                        ),
                        "approval_confirmed": _boolean(
                            "Must be true with dry_run=false after the user has approved the proposal.",
                            default=False,
                        ),
                    },
                    required=["event_id"],
                ),
                handler=promote_alert_incident,
                annotations=WRITE_TOOL_ANNOTATIONS,
            ),
            McpTool(
                name="propose_manifest_change",
                title="Propose Manifest Change",
                description=(
                    "Preview a manifest edit through the existing manifest editor, or "
                    "after approval submit it only to the Safe PR workflow."
                ),
                input_schema=_schema(
                    properties={
                        "resource_id": _string("Existing inventory resource key.", max_length=255),
                        "payload": _object(
                            "Existing ResourceManifestPreviewRequest body from the Opsia API contract."
                        ),
                        "reason": _string(
                            "Required audit reason used if the proposal is submitted to Safe PR.",
                            max_length=500,
                        ),
                        "dry_run": _boolean(
                            "When true, call only the non-mutating preview API and return the Safe PR proposal.",
                            default=True,
                        ),
                        "approval_confirmed": _boolean(
                            "Must be true with dry_run=false after the user has approved the preview.",
                            default=False,
                        ),
                    },
                    required=["resource_id", "payload", "reason"],
                ),
                handler=propose_manifest_change,
                annotations=WRITE_TOOL_ANNOTATIONS,
            ),
            McpTool(
                name="create_release_plan",
                title="Create Release Plan",
                description=(
                    "Dry-run or create/update a release plan through the existing release "
                    "plan API. Gateway application permissions and blockers still apply."
                ),
                input_schema=_schema(
                    properties={
                        "payload": _object(
                            "Existing ReleasePlanUpsertRequest body from the Opsia API contract."
                        ),
                        "dry_run": _boolean(
                            "When true, return only a proposal and do not call the Gateway.",
                            default=True,
                        ),
                        "approval_confirmed": _boolean(
                            "Must be true with dry_run=false after the user has approved the proposal.",
                            default=False,
                        ),
                    },
                    required=["payload"],
                ),
                handler=create_release_plan,
                annotations=WRITE_TOOL_ANNOTATIONS,
            ),
            McpTool(
                name="start_release_run",
                title="Start Release Run",
                description=(
                    "Dry-run or start a release run through the existing release-plan "
                    "start API. Gateway blocker and approval-evidence checks still apply."
                ),
                input_schema=_schema(
                    properties={
                        "payload": _object(
                            "Existing ReleasePlanUpsertRequest body, usually with an existing plan_id."
                        ),
                        "dry_run": _boolean(
                            "When true, return only a proposal and do not call the Gateway.",
                            default=True,
                        ),
                        "approval_confirmed": _boolean(
                            "Must be true with dry_run=false after the user has approved the proposal.",
                            default=False,
                        ),
                    },
                    required=["payload"],
                ),
                handler=start_release_run,
                annotations=WRITE_TOOL_ANNOTATIONS,
            ),
        ]
    )


async def list_clusters(client: ManagementApiClient, arguments: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown(arguments, {"limit"})
    limit = _bounded_int(
        arguments,
        "limit",
        DEFAULT_LIST_CLUSTERS_LIMIT,
        1,
        MAX_LIST_CLUSTERS_LIMIT,
    )
    data = await client.get_json(routes.CLUSTERS_PATH, {"limit": limit})
    return _read_result("list_clusters", routes.CLUSTERS_PATH, data)


async def get_fleet_summary(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, set())
    data = await client.get_json(routes.FLEET_SUMMARY_PATH)
    return _read_result("get_fleet_summary", routes.FLEET_SUMMARY_PATH, data)


async def list_feature_contracts(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, set())
    data = await client.get_json(routes.FEATURE_CONTRACTS_PATH)
    return _read_result("list_feature_contracts", routes.FEATURE_CONTRACTS_PATH, data)


async def get_cluster_summary(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"cluster_id"})
    cluster_id = _required_str(arguments, "cluster_id", max_length=MAX_CLUSTER_ID_LENGTH)
    path = _format_path(routes.CLUSTER_SUMMARY_PATH, cluster_id=cluster_id)
    data = await client.get_json(path)
    return _read_result("get_cluster_summary", path, data)


async def get_cluster_connection_status(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"cluster_id"})
    cluster_id = _required_str(arguments, "cluster_id", max_length=MAX_CLUSTER_ID_LENGTH)
    path = _format_path(routes.CLUSTER_CONNECTION_STATUS_PATH, cluster_id=cluster_id)
    data = await client.get_json(path)
    return _read_result("get_cluster_connection_status", path, data)


async def get_cluster_inventory_summary(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"cluster_id"})
    cluster_id = _required_str(arguments, "cluster_id", max_length=MAX_CLUSTER_ID_LENGTH)
    path = _format_path(routes.CLUSTER_INVENTORY_SUMMARY_PATH, cluster_id=cluster_id)
    data = await client.get_json(path)
    return _read_result("get_cluster_inventory_summary", path, data)


async def get_cluster_nodes_summary(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"cluster_id"})
    cluster_id = _required_str(arguments, "cluster_id", max_length=MAX_CLUSTER_ID_LENGTH)
    path = _format_path(routes.CLUSTER_NODES_SUMMARY_PATH, cluster_id=cluster_id)
    data = await client.get_json(path)
    return _read_result("get_cluster_nodes_summary", path, data)


async def get_cluster_node_pods_summary(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"cluster_id", "node_name"})
    cluster_id = _required_str(arguments, "cluster_id", max_length=MAX_CLUSTER_ID_LENGTH)
    node_name = _required_str(arguments, "node_name", max_length=MAX_KUBERNETES_NAME_LENGTH)
    path = _format_path(
        routes.CLUSTER_NODE_PODS_SUMMARY_PATH,
        cluster_id=cluster_id,
        node_name=node_name,
    )
    data = await client.get_json(path)
    return _read_result("get_cluster_node_pods_summary", path, data)


async def get_cluster_usage(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"cluster_id", "limit"})
    cluster_id = _required_str(arguments, "cluster_id", max_length=MAX_CLUSTER_ID_LENGTH)
    path = _format_path(routes.CLUSTER_USAGE_PATH, cluster_id=cluster_id)
    data = await client.get_json(
        path,
        {
            "limit": _bounded_int(
                arguments,
                "limit",
                DEFAULT_CLUSTER_USAGE_LIMIT,
                1,
                MAX_CLUSTER_USAGE_LIMIT,
            ),
        },
    )
    return _read_result("get_cluster_usage", path, data)


async def list_resources(client: ManagementApiClient, arguments: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown(
        arguments, {"cluster_id", "resource_type", "namespace", "include_deleted", "limit"}
    )
    cluster_id = _required_str(arguments, "cluster_id", max_length=MAX_CLUSTER_ID_LENGTH)
    path = _format_path(routes.CLUSTER_INVENTORY_RESOURCES_PATH, cluster_id=cluster_id)
    data = await client.get_json(
        path,
        {
            "resource_type": _optional_str(arguments, "resource_type", max_length=80),
            "namespace": _optional_str(
                arguments, "namespace", max_length=MAX_KUBERNETES_NAME_LENGTH
            ),
            "include_deleted": _optional_bool(arguments, "include_deleted", default=False),
            "limit": _bounded_int(
                arguments,
                "limit",
                DEFAULT_LIST_RESOURCES_LIMIT,
                1,
                MAX_LIST_RESOURCES_LIMIT,
            ),
        },
    )
    return _read_result("list_resources", path, data)


async def list_cluster_workloads(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    return await _read_cluster_inventory_collection(
        client,
        arguments,
        tool_name="list_cluster_workloads",
        route=routes.CLUSTER_INVENTORY_WORKLOADS_PATH,
    )


async def list_cluster_services(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    return await _read_cluster_inventory_collection(
        client,
        arguments,
        tool_name="list_cluster_services",
        route=routes.CLUSTER_INVENTORY_SERVICES_PATH,
    )


async def list_cluster_events(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    return await _read_cluster_inventory_collection(
        client,
        arguments,
        tool_name="list_cluster_events",
        route=routes.CLUSTER_INVENTORY_EVENTS_PATH,
    )


async def list_helm_releases(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"clusters", "namespaces"})
    data = await client.get_json(
        routes.HELM_RELEASES_PATH,
        {
            gateway_params.CLUSTERS_QUERY: _optional_str(
                arguments,
                "clusters",
                max_length=MAX_FILTER_VALUE_LIST_LENGTH,
            ),
            gateway_params.NAMESPACES_QUERY: _optional_str(
                arguments,
                "namespaces",
                max_length=MAX_FILTER_VALUE_LIST_LENGTH,
            ),
        },
    )
    return _read_result("list_helm_releases", routes.HELM_RELEASES_PATH, data)


async def get_helm_release(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"cluster_id", "namespace", "release_name"})
    cluster_id = _required_str(arguments, "cluster_id", max_length=MAX_CLUSTER_ID_LENGTH)
    namespace = _required_str(arguments, "namespace", max_length=MAX_KUBERNETES_NAME_LENGTH)
    release_name = _required_str(arguments, "release_name", max_length=MAX_KUBERNETES_NAME_LENGTH)
    path = _format_path(
        routes.HELM_RELEASE_PATH,
        namespace=namespace,
        release_name=release_name,
    )
    data = await client.get_json(path, {gateway_params.CLUSTER_ID_QUERY: cluster_id})
    return _read_result("get_helm_release", path, data)


async def _read_cluster_inventory_collection(
    client: ManagementApiClient,
    arguments: dict[str, Any],
    *,
    tool_name: str,
    route: str,
) -> dict[str, Any]:
    _reject_unknown(arguments, {"cluster_id", "namespace", "limit"})
    cluster_id = _required_str(arguments, "cluster_id", max_length=MAX_CLUSTER_ID_LENGTH)
    path = _format_path(route, cluster_id=cluster_id)
    data = await client.get_json(
        path,
        {
            gateway_params.NAMESPACE_QUERY: _optional_str(
                arguments,
                "namespace",
                max_length=MAX_KUBERNETES_NAME_LENGTH,
            ),
            gateway_params.LIMIT_QUERY: _bounded_int(
                arguments,
                "limit",
                DEFAULT_LIST_RESOURCES_LIMIT,
                1,
                MAX_LIST_RESOURCES_LIMIT,
            ),
        },
    )
    return _read_result(tool_name, path, data)


async def list_global_filter_facets(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(
        arguments,
        {"query", "clusters", "namespaces", "applications", "resource_types", "labels", "limit"},
    )
    data = await client.get_json(
        routes.FILTER_FACETS_PATH,
        {
            gateway_params.GLOBAL_FILTER_SEARCH_QUERY: _optional_str(
                arguments,
                "query",
                max_length=MAX_FILTER_SEARCH_LENGTH,
            ),
            gateway_params.CLUSTERS_QUERY: _optional_str(
                arguments,
                "clusters",
                max_length=MAX_FILTER_VALUE_LIST_LENGTH,
            ),
            gateway_params.NAMESPACES_QUERY: _optional_str(
                arguments,
                "namespaces",
                max_length=MAX_FILTER_VALUE_LIST_LENGTH,
            ),
            gateway_params.APPLICATIONS_QUERY: _optional_str(
                arguments,
                "applications",
                max_length=MAX_FILTER_VALUE_LIST_LENGTH,
            ),
            gateway_params.RESOURCE_TYPES_QUERY: _optional_str(
                arguments,
                "resource_types",
                max_length=MAX_FILTER_VALUE_LIST_LENGTH,
            ),
            gateway_params.LABELS_QUERY: _optional_str(
                arguments,
                "labels",
                max_length=MAX_FILTER_VALUE_LIST_LENGTH,
            ),
            gateway_params.LIMIT_QUERY: _bounded_int(
                arguments,
                "limit",
                DEFAULT_GLOBAL_FILTER_FACET_LIMIT,
                1,
                MAX_GLOBAL_FILTER_FACET_LIMIT,
            ),
        },
    )
    return _read_result("list_global_filter_facets", routes.FILTER_FACETS_PATH, data)


async def list_resource_filter_facets(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"axis", "selected", "cursor", "limit"})
    data = await client.get_json(
        routes.RESOURCES_FILTER_FACETS_PATH,
        {
            gateway_params.FACET_AXIS_QUERY: _required_enum(
                arguments,
                "axis",
                gateway_facets.RESOURCE_FILTER_FACET_AXES,
            ),
            gateway_params.FACET_SELECTED_QUERY: _optional_str(
                arguments,
                "selected",
                max_length=2048,
            ),
            **_facet_page_params(arguments),
        },
    )
    return _read_result("list_resource_filter_facets", routes.RESOURCES_FILTER_FACETS_PATH, data)


async def list_resource_label_facets(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, _RESOURCE_LABEL_FACET_ARGUMENTS)
    data = await client.get_json(
        routes.RESOURCE_LABEL_FACETS_PATH,
        {
            **_resource_filter_params(arguments, include_deleted=True),
            **_facet_page_params(arguments),
        },
    )
    return _read_result("list_resource_label_facets", routes.RESOURCE_LABEL_FACETS_PATH, data)


async def get_resource_metrics_history(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, _RESOURCE_METRIC_HISTORY_ARGUMENTS)
    ids_query = ",".join(
        _required_string_list(
            arguments,
            "resource_ids",
            max_items=MAX_RESOURCE_METRIC_HISTORY_IDS,
            item_max_length=MAX_RESOURCE_METRIC_HISTORY_ID_LENGTH,
            forbidden_characters=",",
        )
    )
    if len(ids_query) > MAX_RESOURCE_METRIC_HISTORY_IDS_QUERY_LENGTH:
        raise ToolInputError(
            f"resource_ids must encode to at most {MAX_RESOURCE_METRIC_HISTORY_IDS_QUERY_LENGTH} characters"
        )
    data = await client.get_json(
        routes.RESOURCE_METRICS_HISTORY_PATH,
        {
            gateway_params.RESOURCE_METRIC_HISTORY_IDS_QUERY: ids_query,
            **_resource_filter_params(arguments, include_deleted=True),
            gateway_params.SNAPSHOT_REVISION_QUERY: _optional_bounded_int(
                arguments,
                "snapshot_revision",
                1,
            ),
            gateway_params.RESOURCE_METRIC_HISTORY_RANGE_QUERY: _optional_enum(
                arguments,
                "time_range",
                RESOURCE_METRIC_HISTORY_RANGES,
                default=DEFAULT_RESOURCE_METRIC_HISTORY_RANGE,
            ),
            gateway_params.LIMIT_QUERY: _bounded_int(
                arguments,
                "limit",
                DEFAULT_RESOURCE_METRIC_HISTORY_LIMIT,
                1,
                MAX_RESOURCE_METRIC_HISTORY_LIMIT,
            ),
        },
    )
    return _read_result("get_resource_metrics_history", routes.RESOURCE_METRICS_HISTORY_PATH, data)


async def get_resource_detail(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(
        arguments,
        {
            "cluster_id",
            "resource_type",
            "kind",
            "name",
            "namespace",
            "related_limit",
            "event_limit",
        },
    )
    cluster_id = _required_str(arguments, "cluster_id", max_length=512)
    path = _format_path(routes.CLUSTER_INVENTORY_RESOURCE_DETAIL_PATH, cluster_id=cluster_id)
    data = await client.get_json(
        path,
        {
            "resource_type": _required_str(arguments, "resource_type", max_length=80),
            "kind": _required_str(arguments, "kind", max_length=120),
            "name": _required_str(arguments, "name", max_length=253),
            "namespace": _optional_str(arguments, "namespace", max_length=253),
            "related_limit": _bounded_int(
                arguments,
                "related_limit",
                DEFAULT_RELATED_LIMIT,
                1,
                MAX_RELATED_LIMIT,
            ),
            "event_limit": _bounded_int(
                arguments,
                "event_limit",
                DEFAULT_EVENT_LIMIT,
                1,
                MAX_EVENT_LIMIT,
            ),
        },
    )
    return _read_result("get_resource_detail", path, data)


async def list_recent_incidents(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"correlation_id", "since", "until", "limit", "offset", "cursor"})
    data = await client.get_json(
        routes.RCA_REPORTS_PATH,
        {
            "correlation_id": _optional_str(arguments, "correlation_id", max_length=255),
            "since": _optional_str(arguments, "since", max_length=80),
            "until": _optional_str(arguments, "until", max_length=80),
            "limit": _bounded_int(
                arguments, "limit", DEFAULT_RECENT_INCIDENT_LIMIT, 1, MAX_QUERY_LIMIT
            ),
            "offset": _bounded_int(arguments, "offset", 0, 0, MAX_LEGACY_OFFSET),
            "cursor": _optional_str(arguments, "cursor", max_length=2048),
        },
    )
    return _read_result("list_recent_incidents", routes.RCA_REPORTS_PATH, data)


async def get_rca_bundle(client: ManagementApiClient, arguments: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown(arguments, {"correlation_id"})
    correlation_id = _required_str(
        arguments,
        "correlation_id",
        max_length=MAX_CORRELATION_ID_LENGTH,
    )
    path = _format_path(routes.RCA_BUNDLE_PATH, correlation_id=correlation_id)
    data = await client.get_json(path)
    return _read_result("get_rca_bundle", path, data)


async def list_incident_recent_changes(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"incident_id", "limit"})
    incident_id = _required_str(arguments, "incident_id", max_length=MAX_INCIDENT_ID_LENGTH)
    path = _format_path(routes.RCA_RECENT_CHANGES_PATH, incident_id=incident_id)
    data = await client.get_json(
        path,
        {
            gateway_params.LIMIT_QUERY: _bounded_int(
                arguments,
                "limit",
                DEFAULT_RCA_RECENT_CHANGE_LIMIT,
                1,
                MAX_RCA_RECENT_CHANGE_LIMIT,
            ),
        },
    )
    return _read_result("list_incident_recent_changes", path, data)


async def list_rca_rules(client: ManagementApiClient, arguments: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown(arguments, set())
    data = await client.get_json(routes.RCA_RULES_PATH)
    return _read_result("list_rca_rules", routes.RCA_RULES_PATH, data)


async def list_dead_letters(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"limit"})
    data = await client.get_json(
        routes.DEAD_LETTERS_PATH,
        {
            "limit": _bounded_int(
                arguments,
                "limit",
                DEFAULT_DEAD_LETTER_LIMIT,
                1,
                MAX_DEAD_LETTER_LIMIT,
            ),
        },
    )
    return _read_result("list_dead_letters", routes.DEAD_LETTERS_PATH, data)


async def list_rca_issues(client: ManagementApiClient, arguments: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown(arguments, {"cluster_id", "limit"})
    data = await client.get_json(
        routes.DASHBOARD_RCA_ISSUES_PATH,
        {
            "cluster_id": _optional_str(arguments, "cluster_id", max_length=512),
            "limit": _bounded_int(
                arguments,
                "limit",
                DEFAULT_RCA_ISSUE_LIMIT,
                1,
                MAX_RCA_ISSUE_LIMIT,
            ),
        },
    )
    return _read_result("list_rca_issues", routes.DASHBOARD_RCA_ISSUES_PATH, data)


async def list_issue_filter_facets(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, _ISSUE_FILTER_FACET_ARGUMENTS)
    data = await client.get_json(
        routes.ISSUES_FILTER_FACETS_PATH,
        {
            gateway_params.FACET_AXIS_QUERY: _required_enum(
                arguments,
                "axis",
                gateway_facets.ISSUE_FILTER_FACET_AXES,
            ),
            **_issue_filter_params(arguments),
            **_facet_page_params(arguments),
        },
    )
    return _read_result("list_issue_filter_facets", routes.ISSUES_FILTER_FACETS_PATH, data)


async def list_issue_label_facets(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, _ISSUE_LABEL_FACET_ARGUMENTS)
    data = await client.get_json(
        routes.ISSUES_LABEL_FACETS_PATH,
        {
            **_issue_filter_params(arguments),
            **_facet_page_params(arguments),
        },
    )
    return _read_result("list_issue_label_facets", routes.ISSUES_LABEL_FACETS_PATH, data)


async def get_rca_incident(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"incident_id", "cluster_id"})
    incident_id = _required_str(arguments, "incident_id", max_length=512)
    path = _format_path(routes.DASHBOARD_RCA_INCIDENT_PATH, incident_id=incident_id)
    data = await client.get_json(
        path,
        {"cluster_id": _optional_str(arguments, "cluster_id", max_length=512)},
    )
    return _read_result("get_rca_incident", path, data)


async def list_resource_issues(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"cluster_id", "kind", "name", "namespace", "limit"})
    data = await client.get_json(
        routes.RESOURCE_RCA_ISSUES_PATH,
        {
            "cluster_id": _required_str(arguments, "cluster_id", max_length=512),
            "kind": _required_str(arguments, "kind", max_length=253),
            "name": _required_str(arguments, "name", max_length=253),
            "namespace": _optional_str(arguments, "namespace", max_length=253),
            "limit": _bounded_int(
                arguments,
                "limit",
                DEFAULT_RESOURCE_ISSUE_LIMIT,
                1,
                MAX_RCA_ISSUE_LIMIT,
            ),
        },
    )
    return _read_result("list_resource_issues", routes.RESOURCE_RCA_ISSUES_PATH, data)


async def list_evidence_windows(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"limit", "offset"})
    data = await client.get_json(
        routes.EVIDENCE_WINDOWS_PATH,
        {
            "limit": _bounded_int(
                arguments, "limit", DEFAULT_RECENT_INCIDENT_LIMIT, 1, MAX_QUERY_LIMIT
            ),
            "offset": _bounded_int(arguments, "offset", 0, 0, MAX_LEGACY_OFFSET),
        },
    )
    return _read_result("list_evidence_windows", routes.EVIDENCE_WINDOWS_PATH, data)


async def get_log_evidence(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"evidence_key"})
    evidence_key = _required_str(arguments, "evidence_key", max_length=512)
    path = _format_path(routes.EVIDENCE_WINDOW_PATH, evidence_key=evidence_key)
    try:
        data = await client.get_json(path, {"source": LOG_EVIDENCE_SOURCE})
    except ManagementApiError as exc:
        if exc.status_code != 404:
            raise
        await client.get_json(path)
        data = {
            "evidence_key": evidence_key,
            "source": LOG_EVIDENCE_SOURCE,
            "available": False,
            "payload": None,
        }
    return _read_result("get_log_evidence", path, data)


async def get_command_status(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"command_id"})
    command_id = _required_str(arguments, "command_id", max_length=200)
    path = _format_path(routes.COMMAND_STATUS_PATH, command_id=command_id)
    data = await client.get_json(path)
    return _read_result("get_command_status", path, data)


async def list_alert_rules(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, set())
    data = await client.get_json(routes.ALERT_RULES_PATH)
    return _read_result("list_alert_rules", routes.ALERT_RULES_PATH, data)


async def get_alert_rule(client: ManagementApiClient, arguments: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown(arguments, {"rule_id"})
    rule_id = _required_str(arguments, "rule_id", max_length=120)
    return await _read_list_item(
        client,
        tool_name="get_alert_rule",
        api_path=routes.ALERT_RULES_PATH,
        response_key=RESPONSE_RULES_KEY,
        id_key=ALERT_RULE_ID_KEY,
        expected_id=rule_id,
        item_key="rule",
    )


async def list_alert_channels(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, set())
    data = await client.get_json(routes.ALERT_CHANNELS_PATH)
    return _read_result("list_alert_channels", routes.ALERT_CHANNELS_PATH, data)


async def get_alert_channel(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"channel_id"})
    channel_id = _required_str(arguments, "channel_id", max_length=120)
    return await _read_list_item(
        client,
        tool_name="get_alert_channel",
        api_path=routes.ALERT_CHANNELS_PATH,
        response_key=RESPONSE_CHANNELS_KEY,
        id_key=ALERT_CHANNEL_ID_KEY,
        expected_id=channel_id,
        item_key="channel",
    )


async def list_alert_events(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(
        arguments,
        {"from_time", "to_time", "rule_id", "severity", "status", "limit"},
    )
    data = await client.get_json(
        routes.ALERT_EVENTS_PATH,
        {
            gateway_params.TIME_RANGE_FROM_QUERY: _optional_str(
                arguments,
                "from_time",
                max_length=80,
            ),
            gateway_params.TIME_RANGE_TO_QUERY: _optional_str(
                arguments,
                "to_time",
                max_length=80,
            ),
            "rule_id": _optional_str(arguments, "rule_id", max_length=120),
            "severity": _optional_str(arguments, "severity", max_length=40),
            "status": _optional_str(arguments, "status", max_length=40),
            "limit": _bounded_int(
                arguments,
                "limit",
                DEFAULT_ALERT_EVENT_LIMIT,
                1,
                MAX_ALERT_EVENT_LIMIT,
            ),
        },
    )
    return _read_result("list_alert_events", routes.ALERT_EVENTS_PATH, data)


async def get_recovery_plan(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"correlation_id"})
    correlation_id = _required_str(arguments, "correlation_id", max_length=2048)
    path = _format_path(
        routes.RCA_RECOVERY_PLAN_BY_CORRELATION_PATH,
        correlation_id=correlation_id,
    )
    data = await client.get_json(path)
    return _read_result("get_recovery_plan", path, data)


async def list_applications(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(
        arguments,
        {
            "clusters",
            "namespaces",
            "applications",
            "labels",
            "environment",
            "status",
            "pending_promotion",
            "query",
            "limit",
        },
    )
    data = await client.get_json(
        routes.APPLICATIONS_PATH,
        {
            "clusters": _optional_str(
                arguments, "clusters", max_length=MAX_FILTER_VALUE_LIST_LENGTH
            ),
            "namespaces": _optional_str(
                arguments, "namespaces", max_length=MAX_FILTER_VALUE_LIST_LENGTH
            ),
            "applications": _optional_str(
                arguments, "applications", max_length=MAX_FILTER_VALUE_LIST_LENGTH
            ),
            "labels": _optional_str(arguments, "labels", max_length=MAX_FILTER_VALUE_LIST_LENGTH),
            gateway_params.APPLICATIONS_ENVIRONMENT_QUERY: _optional_str(
                arguments,
                "environment",
                max_length=120,
            ),
            gateway_params.APPLICATIONS_STATUS_QUERY: _optional_str(
                arguments,
                "status",
                max_length=120,
            ),
            gateway_params.APPLICATIONS_PENDING_PROMOTION_QUERY: _optional_str(
                arguments,
                "pending_promotion",
                max_length=120,
            ),
            gateway_params.APPLICATIONS_SEARCH_QUERY: _optional_str(
                arguments,
                "query",
                max_length=MAX_FILTER_SEARCH_LENGTH,
            ),
            "limit": _bounded_int(
                arguments,
                "limit",
                DEFAULT_APPLICATION_LIMIT,
                1,
                MAX_APPLICATION_LIMIT,
            ),
        },
    )
    return _read_result("list_applications", routes.APPLICATIONS_PATH, data)


async def list_application_filter_facets(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, _APPLICATION_FILTER_FACET_ARGUMENTS)
    data = await client.get_json(
        routes.APPLICATION_FILTER_FACETS_PATH,
        {
            gateway_params.FACET_AXIS_QUERY: _required_enum(
                arguments,
                "axis",
                gateway_facets.APPLICATION_FILTER_FACET_AXES,
            ),
            **_application_filter_params(arguments),
            **_facet_page_params(arguments),
        },
    )
    return _read_result(
        "list_application_filter_facets",
        routes.APPLICATION_FILTER_FACETS_PATH,
        data,
    )


async def list_application_label_facets(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, _APPLICATION_LABEL_FACET_ARGUMENTS)
    data = await client.get_json(
        routes.APPLICATION_LABEL_FACETS_PATH,
        {
            **_application_filter_params(arguments),
            **_facet_page_params(arguments),
        },
    )
    return _read_result(
        "list_application_label_facets",
        routes.APPLICATION_LABEL_FACETS_PATH,
        data,
    )


async def get_application_detail(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"application_id", "instance", "workload"})
    application_id = _required_str(arguments, "application_id", max_length=200)
    path = _format_path(routes.APPLICATION_PATH, application_id=application_id)
    data = await client.get_json(
        path,
        {
            "instance": _optional_str(arguments, "instance", max_length=200),
            "workload": _optional_str(arguments, "workload", max_length=128),
        },
    )
    return _read_result("get_application_detail", path, data)


async def get_application_drift(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"application_id", "instance"})
    application_id = _required_str(arguments, "application_id", max_length=200)
    path = _format_path(routes.APPLICATION_DRIFT_PATH, application_id=application_id)
    data = await client.get_json(
        path,
        {"instance": _optional_str(arguments, "instance", max_length=200)},
    )
    return _read_result("get_application_drift", path, data)


async def list_application_deployments(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"application_id", "instance", "limit"})
    application_id = _required_str(arguments, "application_id", max_length=200)
    path = _format_path(routes.APPLICATION_DEPLOYMENTS_PATH, application_id=application_id)
    data = await client.get_json(
        path,
        {
            "limit": _bounded_int(
                arguments,
                "limit",
                DEFAULT_APPLICATION_DEPLOYMENT_LIMIT,
                1,
                MAX_APPLICATION_DEPLOYMENT_LIMIT,
            ),
            "instance": _optional_str(arguments, "instance", max_length=200),
        },
    )
    return _read_result("list_application_deployments", path, data)


async def list_audit_timeline(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"correlation_id", "cursor", "limit"})
    data = await client.get_json(
        routes.AUDIT_TIMELINE_PATH,
        {
            "correlation_id": _required_str(arguments, "correlation_id", max_length=2048),
            "cursor": _optional_str(arguments, "cursor", max_length=2048),
            "limit": _bounded_int(
                arguments,
                "limit",
                DEFAULT_AUDIT_TIMELINE_LIMIT,
                1,
                MAX_AUDIT_TIMELINE_LIMIT,
            ),
        },
    )
    return _read_result("list_audit_timeline", routes.AUDIT_TIMELINE_PATH, data)


async def list_workflow_runs(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(
        arguments,
        {"application_id", "plan_id", "status", "attention_only", "active_only", "limit"},
    )
    application_id = _optional_str(arguments, "application_id", max_length=200)
    if application_id is not None:
        limit = _bounded_int(
            arguments,
            "limit",
            DEFAULT_APPLICATION_WORKFLOW_RUN_LIMIT,
            1,
            MAX_APPLICATION_WORKFLOW_RUN_LIMIT,
        )
        path = _format_path(routes.APPLICATION_RUNS_PATH, application_id=application_id)
        data = await client.get_json(path, {"limit": limit})
        return _read_result("list_workflow_runs", path, data)
    limit = _bounded_int(
        arguments,
        "limit",
        DEFAULT_RELEASE_RUN_LIMIT,
        1,
        MAX_RELEASE_RUN_LIMIT,
    )
    data = await client.get_json(
        routes.RELEASE_RUNS_PATH,
        {
            "plan_id": _optional_str(arguments, "plan_id", max_length=160),
            "status": _optional_str(arguments, "status", max_length=80),
            "attention_only": _optional_bool(arguments, "attention_only", default=False),
            "active_only": _optional_bool(arguments, "active_only", default=False),
            "limit": limit,
        },
    )
    return _read_result("list_workflow_runs", routes.RELEASE_RUNS_PATH, data)


async def get_workflow_run(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"run_id", "application_id"})
    run_id = _required_str(arguments, "run_id", max_length=200)
    application_id = _optional_str(arguments, "application_id", max_length=200)
    if application_id is not None:
        path = _format_path(routes.APPLICATION_RUNS_PATH, application_id=application_id)
        return await _read_list_item(
            client,
            tool_name="get_workflow_run",
            api_path=path,
            response_key=RESPONSE_RUNS_KEY,
            id_key=WORKFLOW_RUN_ID_KEY,
            expected_id=run_id,
            item_key="run",
            params={"limit": MAX_APPLICATION_WORKFLOW_RUN_LIMIT},
        )
    path = _format_path(routes.RELEASE_RUN_PATH, run_id=run_id)
    data = await client.get_json(path)
    return _read_result("get_workflow_run", path, data)


async def get_release_run_report(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"run_id"})
    run_id = _required_str(arguments, "run_id", max_length=200)
    path = _format_path(routes.RELEASE_RUN_REPORT_PATH, run_id=run_id)
    data = await client.get_json(path)
    return _read_result("get_release_run_report", path, data)


async def list_release_plans(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"limit"})
    data = await client.get_json(
        routes.RELEASE_PLANS_PATH,
        {
            "limit": _bounded_int(
                arguments,
                "limit",
                DEFAULT_RELEASE_PLAN_LIMIT,
                1,
                MAX_RELEASE_PLAN_LIMIT,
            ),
        },
    )
    return _read_result("list_release_plans", routes.RELEASE_PLANS_PATH, data)


async def get_release_plan(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"plan_id"})
    plan_id = _required_str(arguments, "plan_id", max_length=160)
    path = _format_path(routes.RELEASE_PLAN_PATH, plan_id=plan_id)
    data = await client.get_json(path)
    return _read_result("get_release_plan", path, data)


async def get_release_run_summary(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"plan_id"})
    data = await client.get_json(
        routes.RELEASE_RUN_SUMMARY_PATH,
        {"plan_id": _optional_str(arguments, "plan_id", max_length=160)},
    )
    return _read_result("get_release_run_summary", routes.RELEASE_RUN_SUMMARY_PATH, data)


async def list_release_audit(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"plan_id", "run_id", "event_type", "limit"})
    data = await client.get_json(
        routes.RELEASE_AUDIT_PATH,
        {
            "plan_id": _optional_str(arguments, "plan_id", max_length=160),
            "run_id": _optional_str(arguments, "run_id", max_length=200),
            "event_type": _optional_str(arguments, "event_type", max_length=120),
            "limit": _bounded_int(
                arguments,
                "limit",
                DEFAULT_RELEASE_AUDIT_LIMIT,
                1,
                MAX_RELEASE_AUDIT_LIMIT,
            ),
        },
    )
    return _read_result("list_release_audit", routes.RELEASE_AUDIT_PATH, data)


async def list_pending_approvals(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"application_id", "limit"})
    application_id = _optional_str(arguments, "application_id", max_length=200)
    limit = _bounded_int(
        arguments,
        "limit",
        DEFAULT_RELEASE_RUN_LIMIT,
        1,
        MAX_APPLICATION_WORKFLOW_RUN_LIMIT,
    )
    if application_id is not None:
        path = _format_path(routes.APPLICATION_RUNS_PATH, application_id=application_id)
        data = await client.get_json(path, {"limit": limit})
        runs = _pending_runs(_list_from_response(data, RESPONSE_RUNS_KEY))
        return _read_result(
            "list_pending_approvals",
            path,
            {
                "source": "application_runs",
                "runs": runs,
                "pending_count": len(runs),
            },
        )

    gitops_data = await client.get_json(
        routes.GITOPS_FILTER_RESULTS_PATH,
        {
            gateway_params.GITOPS_APPROVAL_QUERY: PENDING_APPROVAL_STATUS,
            "limit": min(limit, MAX_RELEASE_RUN_LIMIT),
        },
    )
    gitops_items = _list_from_response(gitops_data, RESPONSE_ITEMS_KEY)
    application_runs: list[dict[str, Any]] = []
    for pending_application_id in _unique_strings(
        item.get(APPLICATION_ID_KEY) for item in gitops_items
    )[:MAX_PENDING_APPROVAL_APPLICATIONS]:
        path = _format_path(
            routes.APPLICATION_RUNS_PATH,
            application_id=pending_application_id,
        )
        run_data = await client.get_json(path, {"limit": limit})
        application_runs.extend(_pending_runs(_list_from_response(run_data, RESPONSE_RUNS_KEY)))

    release_data = await client.get_json(
        routes.RELEASE_RUNS_PATH,
        {
            "status": WAITING_FOR_APPROVAL_STATUS,
            "limit": min(limit, MAX_RELEASE_RUN_LIMIT),
        },
    )
    release_runs = _pending_runs(_list_from_response(release_data, RESPONSE_RUNS_KEY))
    return _read_result(
        "list_pending_approvals",
        routes.GITOPS_FILTER_RESULTS_PATH,
        {
            "source": "gitops_filter_application_runs_release_runs",
            "gitops_changes": gitops_items,
            "application_runs": application_runs,
            "release_runs": release_runs,
            "pending_count": len(application_runs) + len(release_runs),
            "gitops_pending_count": len(gitops_items),
            "application_scan_truncated": len(
                _unique_strings(item.get(APPLICATION_ID_KEY) for item in gitops_items)
            )
            > MAX_PENDING_APPROVAL_APPLICATIONS,
        },
    )


async def list_gitops_filter_facets(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, _GITOPS_FILTER_FACET_ARGUMENTS)
    data = await client.get_json(
        routes.GITOPS_FILTER_FACETS_PATH,
        {
            gateway_params.FACET_AXIS_QUERY: _required_enum(
                arguments,
                "axis",
                gateway_facets.GITOPS_FILTER_FACET_AXES,
            ),
            **_gitops_filter_params(arguments),
            **_facet_page_params(arguments),
        },
    )
    return _read_result("list_gitops_filter_facets", routes.GITOPS_FILTER_FACETS_PATH, data)


async def get_resource_capabilities(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"resource"})
    data = await client.get_json(
        routes.RESOURCE_CAPABILITIES_PATH,
        {
            "resource": _required_str(arguments, "resource", max_length=255),
        },
    )
    return _read_result("get_resource_capabilities", routes.RESOURCE_CAPABILITIES_PATH, data)


async def get_resource_graph(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(
        arguments,
        {
            "clusters",
            "namespaces",
            "applications",
            "resource_types",
            "health",
            "labels",
            "query",
            "include_deleted",
            "snapshot_revision",
            "max_nodes",
            "max_edges",
        },
    )
    data = await client.get_json(
        routes.RESOURCES_GRAPH_PATH,
        {
            "clusters": _optional_str(
                arguments, "clusters", max_length=MAX_FILTER_VALUE_LIST_LENGTH
            ),
            "namespaces": _optional_str(
                arguments, "namespaces", max_length=MAX_FILTER_VALUE_LIST_LENGTH
            ),
            "applications": _optional_str(
                arguments, "applications", max_length=MAX_FILTER_VALUE_LIST_LENGTH
            ),
            gateway_params.RESOURCE_TYPES_QUERY: _optional_str(
                arguments,
                "resource_types",
                max_length=MAX_FILTER_VALUE_LIST_LENGTH,
            ),
            gateway_params.RESOURCE_HEALTH_QUERY: _optional_str(
                arguments,
                "health",
                max_length=MAX_FILTER_VALUE_LIST_LENGTH,
            ),
            "labels": _optional_str(arguments, "labels", max_length=MAX_FILTER_VALUE_LIST_LENGTH),
            gateway_params.RESOURCE_SEARCH_QUERY: _optional_str(
                arguments,
                "query",
                max_length=MAX_FILTER_SEARCH_LENGTH,
            ),
            gateway_params.RESOURCE_INCLUDE_DELETED_QUERY: _optional_bool_or_none(
                arguments,
                "include_deleted",
            ),
            "snapshot_revision": _optional_bounded_int(
                arguments,
                "snapshot_revision",
                1,
            ),
            "max_nodes": _optional_bounded_int(
                arguments,
                "max_nodes",
                1,
                MAX_GRAPH_NODE_LIMIT,
            ),
            "max_edges": _optional_bounded_int(
                arguments,
                "max_edges",
                1,
                MAX_GRAPH_EDGE_LIMIT,
            ),
        },
    )
    return _read_result("get_resource_graph", routes.RESOURCES_GRAPH_PATH, data)


async def list_recent_changes(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(
        arguments,
        {
            "from_ms",
            "to_ms",
            "bucket_ms",
            "clusters",
            "namespaces",
            "applications",
            "resource_types",
            "health",
            "labels",
            "query",
        },
    )
    from_ms = _required_bounded_int(
        arguments,
        "from_ms",
        0,
        MAX_EPOCH_MILLISECONDS,
    )
    to_ms = _required_bounded_int(
        arguments,
        "to_ms",
        1,
        MAX_EPOCH_MILLISECONDS,
    )
    bucket_ms = _required_bounded_int(
        arguments,
        "bucket_ms",
        MIN_CHANGE_BUCKET_MS,
        MAX_CHANGE_BUCKET_MS,
    )
    _validate_change_window(from_ms=from_ms, to_ms=to_ms, bucket_ms=bucket_ms)
    data = await client.get_json(
        routes.CHANGES_PATH,
        {
            gateway_params.TIME_RANGE_FROM_QUERY: from_ms,
            gateway_params.TIME_RANGE_TO_QUERY: to_ms,
            gateway_params.CHANGE_BUCKET_QUERY: bucket_ms,
            "clusters": _optional_str(
                arguments, "clusters", max_length=MAX_FILTER_VALUE_LIST_LENGTH
            ),
            "namespaces": _optional_str(
                arguments, "namespaces", max_length=MAX_FILTER_VALUE_LIST_LENGTH
            ),
            "applications": _optional_str(
                arguments, "applications", max_length=MAX_FILTER_VALUE_LIST_LENGTH
            ),
            gateway_params.RESOURCE_TYPES_QUERY: _optional_str(
                arguments,
                "resource_types",
                max_length=MAX_FILTER_VALUE_LIST_LENGTH,
            ),
            gateway_params.RESOURCE_HEALTH_QUERY: _optional_str(
                arguments,
                "health",
                max_length=MAX_FILTER_VALUE_LIST_LENGTH,
            ),
            "labels": _optional_str(arguments, "labels", max_length=MAX_FILTER_VALUE_LIST_LENGTH),
            gateway_params.RESOURCE_SEARCH_QUERY: _optional_str(
                arguments,
                "query",
                max_length=MAX_FILTER_SEARCH_LENGTH,
            ),
        },
    )
    return _read_result("list_recent_changes", routes.CHANGES_PATH, data)


async def list_metric_query_presets(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"cluster_id"})
    cluster_id = _required_str(arguments, "cluster_id", max_length=512)
    path = _format_path(routes.CLUSTER_METRIC_QUERY_PRESETS_PATH, cluster_id=cluster_id)
    data = await client.get_json(path)
    return _read_result("list_metric_query_presets", path, data)


async def list_metric_widgets(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"cluster_id"})
    cluster_id = _required_str(arguments, "cluster_id", max_length=512)
    path = _format_path(routes.CLUSTER_METRIC_WIDGETS_PATH, cluster_id=cluster_id)
    data = await client.get_json(path)
    return _read_result("list_metric_widgets", path, data)


async def get_metric_widget(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"cluster_id", "widget_id"})
    cluster_id = _required_str(arguments, "cluster_id", max_length=512)
    widget_id = _required_str(arguments, "widget_id", max_length=120)
    path = _format_path(routes.CLUSTER_METRIC_WIDGETS_PATH, cluster_id=cluster_id)
    return await _read_list_item(
        client,
        tool_name="get_metric_widget",
        api_path=path,
        response_key=RESPONSE_ITEMS_KEY,
        id_key=METRIC_WIDGET_ID_KEY,
        expected_id=widget_id,
        item_key="widget",
    )


async def run_metric_query_preset(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"cluster_id", "preset_id", "dry_run", "approval_confirmed"})
    cluster_id = _required_str(arguments, "cluster_id", max_length=512)
    preset_id = _required_str(arguments, "preset_id", max_length=120)
    path = _format_path(
        routes.CLUSTER_METRIC_QUERY_PRESET_RUN_PATH,
        cluster_id=cluster_id,
        preset_id=preset_id,
    )
    return await _post_or_propose(
        client,
        arguments,
        tool_name="run_metric_query_preset",
        api_path=path,
        payload={},
        operation_keys=("command_id", "correlation_id"),
        reason=(
            "Running a metric preset is cluster-read-only, but the existing Gateway "
            "queues an agent debug command and records command status. MCP therefore "
            "defaults to dry_run and requires approval_confirmed=true before it queues "
            "the request. The Gateway verifies preset existence and evidence access."
        ),
    )


async def create_alert_rule(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"payload", "dry_run", "approval_confirmed"})
    payload = _required_object(arguments, "payload")
    return await _post_or_propose(
        client,
        arguments,
        tool_name="create_alert_rule",
        api_path=routes.ALERT_RULES_PATH,
        payload=payload,
        operation_keys=("rule_id",),
        reason=(
            "Alert rule creation is a persistent admin operation, so MCP defaults to "
            "dry_run and requires approval_confirmed=true before it submits the "
            "existing alert-rule POST. Gateway admin-session checks still decide "
            "whether the request is allowed."
        ),
    )


async def request_recovery_action(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(
        arguments,
        {
            "plan_id",
            "correlation_id",
            "expected_plan_id",
            "action_id",
            "reason",
            "dry_run",
            "approval_confirmed",
        },
    )
    plan_id = _optional_str(arguments, "plan_id", max_length=2048)
    correlation_id = _optional_str(arguments, "correlation_id", max_length=2048)
    if (plan_id is None) == (correlation_id is None):
        raise ToolInputError("provide exactly one of plan_id or correlation_id")
    action_id = _required_str(arguments, "action_id", max_length=2048)
    reason = _optional_str(arguments, "reason", max_length=500)
    if plan_id is not None:
        api_path = _format_path(
            routes.RCA_RECOVERY_ACTION_SELECT_PATH,
            plan_id=plan_id,
            action_id=action_id,
        )
        payload: dict[str, Any] = {}
    else:
        expected_plan_id = _required_str(arguments, "expected_plan_id", max_length=2048)
        api_path = _format_path(
            routes.RCA_RECOVERY_ACTION_SELECT_BY_CORRELATION_PATH,
            correlation_id=correlation_id or "",
        )
        payload = {
            "expected_plan_id": expected_plan_id,
            "action_id": action_id,
        }
    if reason is not None:
        payload["reason"] = reason
    return await _post_or_propose(
        client,
        arguments,
        tool_name="request_recovery_action",
        api_path=api_path,
        payload=payload,
        operation_keys=("event_id", "correlation_id", "command_id"),
        reason=(
            "Recovery action selection can trigger follow-up workflow events, so MCP "
            "defaults to dry_run and requires approval_confirmed=true before it "
            "submits the existing RCA selection POST. The Gateway verifies that the "
            "plan and action already exist for the authenticated workspace."
        ),
    )


async def create_command_request(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"payload", "dry_run", "approval_confirmed"})
    payload = _required_object(arguments, "payload")
    _reject_direct_execution_flags(payload)
    return await _post_or_propose(
        client,
        arguments,
        tool_name="create_command_request",
        api_path=routes.COMMANDS_PATH,
        payload=payload,
        operation_keys=("command_id", "event_id", "correlation_id", "audit_event_id"),
        reason=(
            "Command requests can affect clusters, so MCP defaults to dry_run, "
            "requires approval_confirmed=true for submission, and refuses direct "
            "execution confirmation flags. The existing command API still performs "
            "RBAC, cluster-scope, diff, audit, and policy validation."
        ),
    )


async def approve_or_reject_workflow(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(
        arguments,
        {"approval_id", "decision", "reason", "dry_run", "approval_confirmed"},
    )
    approval_id = _required_str(arguments, "approval_id", max_length=2048)
    decision = _required_str(arguments, "decision", max_length=20)
    if decision not in {"grant", "reject"}:
        raise ToolInputError("decision must be grant or reject")
    template = routes.APPROVAL_GRANT_PATH if decision == "grant" else routes.APPROVAL_REJECT_PATH
    api_path = _format_path(template, approval_id=approval_id)
    payload: dict[str, Any] = {}
    reason = _optional_str(arguments, "reason", max_length=500)
    if reason is not None:
        payload["reason"] = reason
    return await _post_or_propose(
        client,
        arguments,
        tool_name="approve_or_reject_workflow",
        api_path=api_path,
        payload=payload,
        operation_keys=("event_id", "correlation_id", "command_id"),
        reason=(
            "Approval decisions can unblock or stop workflows, so MCP defaults to "
            "dry_run and requires approval_confirmed=true before it submits the "
            "existing approval decision POST. The Gateway checks that the approval "
            "is open and that the authenticated user has deployment access."
        ),
    )


async def update_alert_rule(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"rule_id", "payload", "dry_run", "approval_confirmed"})
    rule_id = _required_str(arguments, "rule_id", max_length=120)
    payload = _required_object(arguments, "payload")
    path = _format_path(routes.ALERT_RULE_PATH, rule_id=rule_id)
    return await _post_or_propose(
        client,
        arguments,
        tool_name="update_alert_rule",
        api_path=path,
        payload=payload,
        operation_keys=("rule_id",),
        reason=(
            "Alert rule updates persist operational policy, so MCP defaults to dry_run "
            "and requires approval_confirmed=true before it submits the existing "
            "alert-rule PATCH. Gateway admin-session and request-model validation "
            "remain authoritative."
        ),
        method="PATCH",
    )


async def disable_alert_rule(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"rule_id", "dry_run", "approval_confirmed"})
    rule_id = _required_str(arguments, "rule_id", max_length=120)
    path = _format_path(routes.ALERT_RULE_PATH, rule_id=rule_id)
    return await _post_or_propose(
        client,
        arguments,
        tool_name="disable_alert_rule",
        api_path=path,
        payload={"enabled": False},
        operation_keys=("rule_id",),
        reason=(
            "Disabling an alert rule is implemented as the existing alert-rule PATCH "
            "with enabled=false, not as deletion. MCP defaults to dry_run and requires "
            "approval_confirmed=true before submission; Gateway admin-session checks "
            "still decide whether the request is allowed."
        ),
        method="PATCH",
    )


async def cancel_command_request(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    return await _command_control_request(
        client,
        arguments,
        tool_name="cancel_command_request",
        route_template=routes.COMMAND_CANCEL_PATH,
        action_label="cancel",
    )


async def retry_command_request(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    return await _command_control_request(
        client,
        arguments,
        tool_name="retry_command_request",
        route_template=routes.COMMAND_RETRY_PATH,
        action_label="retry",
    )


async def _command_control_request(
    client: ManagementApiClient,
    arguments: dict[str, Any],
    *,
    tool_name: str,
    route_template: str,
    action_label: str,
) -> dict[str, Any]:
    _reject_unknown(
        arguments,
        {"command_id", "idempotency_key", "reason", "dry_run", "approval_confirmed"},
    )
    command_id = _required_str(arguments, "command_id", max_length=200)
    idempotency_key = _idempotency_key(arguments)
    reason = _optional_str(arguments, "reason", max_length=500)
    payload: dict[str, Any] = {}
    if reason is not None:
        payload["reason"] = reason
    path = _format_path(route_template, command_id=command_id)
    return await _post_or_propose(
        client,
        arguments,
        tool_name=tool_name,
        api_path=path,
        payload=payload,
        operation_keys=(
            "command_id",
            "event_id",
            "audit_event_id",
            "correlation_id",
            "attempt_id",
        ),
        reason=(
            f"Command {action_label} changes command lifecycle state, so MCP defaults "
            "to dry_run, requires approval_confirmed=true before submission, and "
            "sends the caller-supplied Idempotency-Key to the existing command control "
            "API. The Gateway re-checks deployment access, command state, and agent "
            "capabilities."
        ),
        headers={IDEMPOTENCY_KEY_HEADER: idempotency_key},
    )


async def ack_alert_event(client: ManagementApiClient, arguments: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown(arguments, {"event_id", "dry_run", "approval_confirmed"})
    event_id = _required_str(arguments, "event_id", max_length=120)
    path = _format_path(routes.ALERT_EVENT_ACK_PATH, event_id=event_id)
    return await _post_or_propose(
        client,
        arguments,
        tool_name="ack_alert_event",
        api_path=path,
        payload={},
        operation_keys=("event_id", "incident_id"),
        reason=(
            "Acknowledging an alert event changes alert lifecycle state, so MCP "
            "defaults to dry_run and requires approval_confirmed=true before it "
            "submits the existing alert-event ack POST. The Gateway records the "
            "authenticated actor and rejects invalid state transitions."
        ),
    )


async def promote_alert_incident(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"event_id", "dry_run", "approval_confirmed"})
    event_id = _required_str(arguments, "event_id", max_length=120)
    path = _format_path(routes.ALERT_EVENT_PROMOTE_INCIDENT_PATH, event_id=event_id)
    return await _post_or_propose(
        client,
        arguments,
        tool_name="promote_alert_incident",
        api_path=path,
        payload={},
        operation_keys=("incident_id", "event_id", "correlation_id"),
        reason=(
            "Promoting an alert event creates an incident linkage through the existing "
            "alert API, so MCP defaults to dry_run and requires approval_confirmed=true "
            "before submission. The Gateway checks event existence and records the "
            "authenticated actor."
        ),
    )


async def propose_manifest_change(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(
        arguments,
        {"resource_id", "payload", "reason", "dry_run", "approval_confirmed"},
    )
    resource_id = _required_str(arguments, "resource_id", max_length=255)
    payload = _required_object(arguments, "payload")
    reason_text = _required_str(arguments, "reason", max_length=500)
    if len(reason_text) < 3:
        raise ToolInputError("reason must be at least 3 characters")
    preview_path = _format_path(routes.RESOURCE_MANIFEST_PREVIEW_PATH, resource_id=resource_id)
    approve_path = _format_path(routes.RESOURCE_MANIFEST_APPROVE_PATH, resource_id=resource_id)
    _assert_allowed_gateway_route("POST", preview_path, ALLOWED_NON_MUTATING_POST_GATEWAY_ROUTES)
    _assert_allowed_gateway_route("POST", approve_path, ALLOWED_WRITE_GATEWAY_ROUTES)
    approve_payload = {**payload, "confirmed": True, "reason": reason_text}
    _validate_json_payload(approve_payload, "payload")
    proposal = _write_proposal("POST", approve_path, approve_payload)
    dry_run = _optional_bool(arguments, "dry_run", default=True)
    if dry_run:
        preview = _redact_manifest_preview_data(await client.post_json(preview_path, payload))
        return {
            "tool": "propose_manifest_change",
            "data": _redact_read_response_value(preview),
            "safety": {
                "mutating": False,
                "dry_run": True,
                "proposal": proposal,
                "approval_required": True,
                "operation_id": None,
                "api_path": preview_path,
                "reason": (
                    "The dry run calls only the existing manifest preview API to obtain "
                    "Gateway validation and diff data. Submitting the same approved "
                    "change would call the Safe PR approve API, which creates a PR "
                    "workflow rather than applying directly to the cluster."
                ),
            },
        }
    if not _optional_bool(arguments, "approval_confirmed", default=False):
        raise ToolInputError("approval_confirmed must be true when dry_run is false")
    if not client.settings.writes_enabled:
        raise ToolInputError(
            f"{OPSIA_MCP_ENABLE_WRITES_ENV}=true is required before MCP write tools can submit"
        )
    raw_data = await client.post_json(approve_path, approve_payload)
    operation_id = _operation_id_from_response(
        raw_data,
        ("event_id", "correlation_id", "workflow_run_id", "approval_id"),
    )
    data = _redact_read_response_value(raw_data)
    return {
        "tool": "propose_manifest_change",
        "data": data,
        "safety": {
            "mutating": True,
            "dry_run": False,
            "proposal": proposal,
            "approval_required": False,
            "operation_id": operation_id,
            "api_path": approve_path,
            "reason": (
                "The approved submission uses the existing manifest approve API. That "
                "API emits a Safe PR workflow and does not apply the manifest directly "
                "to the cluster."
            ),
        },
    }


async def create_release_plan(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"payload", "dry_run", "approval_confirmed"})
    payload = _required_object(arguments, "payload")
    return await _post_or_propose(
        client,
        arguments,
        tool_name="create_release_plan",
        api_path=routes.RELEASE_PLANS_PATH,
        payload=payload,
        operation_keys=("plan_id", "plan.plan_id"),
        reason=(
            "Release plan creation is persistent deployment configuration, so MCP "
            "defaults to dry_run and requires approval_confirmed=true before it "
            "submits the existing release-plan POST. Gateway application manage "
            "permissions and plan validation remain authoritative."
        ),
    )


async def start_release_run(
    client: ManagementApiClient, arguments: dict[str, Any]
) -> dict[str, Any]:
    _reject_unknown(arguments, {"payload", "dry_run", "approval_confirmed"})
    payload = _required_object(arguments, "payload")
    return await _post_or_propose(
        client,
        arguments,
        tool_name="start_release_run",
        api_path=routes.RELEASE_PLAN_START_PATH,
        payload=payload,
        operation_keys=("run_id", "run.run_id"),
        reason=(
            "Starting a release can dispatch deployment work, so MCP defaults to "
            "dry_run and requires approval_confirmed=true before it submits the "
            "existing release-plan start POST. Gateway blocker checks, application "
            "permissions, and production approval-evidence checks still apply."
        ),
    )


_RESOURCE_FILTER_ARGUMENTS = frozenset(
    {
        "clusters",
        "namespaces",
        "applications",
        "resource_types",
        "health",
        "labels",
        "query",
        "include_deleted",
    }
)
_FACET_PAGE_ARGUMENTS = frozenset({"facet_query", "cursor", "limit"})
_RESOURCE_LABEL_FACET_ARGUMENTS = _RESOURCE_FILTER_ARGUMENTS | _FACET_PAGE_ARGUMENTS
_RESOURCE_METRIC_HISTORY_ARGUMENTS = _RESOURCE_FILTER_ARGUMENTS | frozenset(
    {"resource_ids", "snapshot_revision", "time_range", "limit"}
)
_APPLICATION_FILTER_ARGUMENTS = frozenset(
    {
        "clusters",
        "namespaces",
        "applications",
        "labels",
        "environment",
        "status",
        "pending_promotion",
        "query",
    }
)
_APPLICATION_FILTER_FACET_ARGUMENTS = (
    _APPLICATION_FILTER_ARGUMENTS | _FACET_PAGE_ARGUMENTS | frozenset({"axis"})
)
_APPLICATION_LABEL_FACET_ARGUMENTS = _APPLICATION_FILTER_ARGUMENTS | _FACET_PAGE_ARGUMENTS
_GITOPS_FILTER_ARGUMENTS = frozenset(
    {
        "clusters",
        "namespaces",
        "applications",
        "labels",
        "environment",
        "approval",
        "change_type",
        "query",
    }
)
_GITOPS_FILTER_FACET_ARGUMENTS = (
    _GITOPS_FILTER_ARGUMENTS | _FACET_PAGE_ARGUMENTS | frozenset({"axis"})
)
_ISSUE_FILTER_ARGUMENTS = frozenset(
    {
        "clusters",
        "namespaces",
        "applications",
        "labels",
        "severity",
        "status",
        "environment",
        "query",
    }
)
_ISSUE_FILTER_FACET_ARGUMENTS = (
    _ISSUE_FILTER_ARGUMENTS | _FACET_PAGE_ARGUMENTS | frozenset({"axis"})
)
_ISSUE_LABEL_FACET_ARGUMENTS = _ISSUE_FILTER_ARGUMENTS | _FACET_PAGE_ARGUMENTS


def _resource_filter_params(
    arguments: dict[str, Any],
    *,
    include_deleted: bool,
) -> dict[str, Any]:
    params = {
        gateway_params.CLUSTERS_QUERY: _optional_str(
            arguments,
            "clusters",
            max_length=MAX_FILTER_VALUE_LIST_LENGTH,
        ),
        gateway_params.NAMESPACES_QUERY: _optional_str(
            arguments,
            "namespaces",
            max_length=MAX_FILTER_VALUE_LIST_LENGTH,
        ),
        gateway_params.APPLICATIONS_QUERY: _optional_str(
            arguments,
            "applications",
            max_length=MAX_FILTER_VALUE_LIST_LENGTH,
        ),
        gateway_params.RESOURCE_TYPES_QUERY: _optional_str(
            arguments,
            "resource_types",
            max_length=MAX_FILTER_VALUE_LIST_LENGTH,
        ),
        gateway_params.RESOURCE_HEALTH_QUERY: _optional_str(
            arguments,
            "health",
            max_length=MAX_FILTER_VALUE_LIST_LENGTH,
        ),
        gateway_params.LABELS_QUERY: _optional_str(
            arguments, "labels", max_length=MAX_FILTER_VALUE_LIST_LENGTH
        ),
        gateway_params.RESOURCE_SEARCH_QUERY: _optional_str(
            arguments,
            "query",
            max_length=MAX_FILTER_SEARCH_LENGTH,
        ),
    }
    if include_deleted:
        params[gateway_params.RESOURCE_INCLUDE_DELETED_QUERY] = _optional_bool_or_none(
            arguments,
            "include_deleted",
        )
    return params


def _application_filter_params(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        gateway_params.CLUSTERS_QUERY: _optional_str(
            arguments, "clusters", max_length=MAX_FILTER_VALUE_LIST_LENGTH
        ),
        gateway_params.NAMESPACES_QUERY: _optional_str(
            arguments,
            "namespaces",
            max_length=MAX_FILTER_VALUE_LIST_LENGTH,
        ),
        gateway_params.APPLICATIONS_QUERY: _optional_str(
            arguments,
            "applications",
            max_length=MAX_FILTER_VALUE_LIST_LENGTH,
        ),
        gateway_params.LABELS_QUERY: _optional_str(
            arguments, "labels", max_length=MAX_FILTER_VALUE_LIST_LENGTH
        ),
        gateway_params.APPLICATIONS_ENVIRONMENT_QUERY: _optional_str(
            arguments,
            "environment",
            max_length=120,
        ),
        gateway_params.APPLICATIONS_STATUS_QUERY: _optional_str(
            arguments,
            "status",
            max_length=120,
        ),
        gateway_params.APPLICATIONS_PENDING_PROMOTION_QUERY: _optional_str(
            arguments,
            "pending_promotion",
            max_length=120,
        ),
        gateway_params.APPLICATIONS_SEARCH_QUERY: _optional_str(
            arguments,
            "query",
            max_length=MAX_FILTER_SEARCH_LENGTH,
        ),
    }


def _gitops_filter_params(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        gateway_params.CLUSTERS_QUERY: _optional_str(
            arguments, "clusters", max_length=MAX_FILTER_VALUE_LIST_LENGTH
        ),
        gateway_params.NAMESPACES_QUERY: _optional_str(
            arguments,
            "namespaces",
            max_length=MAX_FILTER_VALUE_LIST_LENGTH,
        ),
        gateway_params.APPLICATIONS_QUERY: _optional_str(
            arguments,
            "applications",
            max_length=MAX_FILTER_VALUE_LIST_LENGTH,
        ),
        gateway_params.LABELS_QUERY: _optional_str(
            arguments, "labels", max_length=MAX_FILTER_VALUE_LIST_LENGTH
        ),
        gateway_params.GITOPS_ENVIRONMENT_QUERY: _optional_str(
            arguments,
            "environment",
            max_length=120,
        ),
        gateway_params.GITOPS_APPROVAL_QUERY: _optional_str(
            arguments,
            "approval",
            max_length=120,
        ),
        gateway_params.GITOPS_CHANGE_TYPE_QUERY: _optional_str(
            arguments,
            "change_type",
            max_length=120,
        ),
        gateway_params.GITOPS_SEARCH_QUERY: _optional_str(
            arguments,
            "query",
            max_length=MAX_FILTER_SEARCH_LENGTH,
        ),
    }


def _issue_filter_params(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        gateway_params.CLUSTERS_QUERY: _optional_str(
            arguments, "clusters", max_length=MAX_FILTER_VALUE_LIST_LENGTH
        ),
        gateway_params.NAMESPACES_QUERY: _optional_str(
            arguments,
            "namespaces",
            max_length=MAX_FILTER_VALUE_LIST_LENGTH,
        ),
        gateway_params.APPLICATIONS_QUERY: _optional_str(
            arguments,
            "applications",
            max_length=MAX_FILTER_VALUE_LIST_LENGTH,
        ),
        gateway_params.LABELS_QUERY: _optional_str(
            arguments, "labels", max_length=MAX_FILTER_VALUE_LIST_LENGTH
        ),
        gateway_params.ISSUES_SEVERITY_QUERY: _optional_str(
            arguments,
            "severity",
            max_length=120,
        ),
        gateway_params.ISSUES_STATUS_QUERY: _optional_str(
            arguments,
            "status",
            max_length=120,
        ),
        gateway_params.ISSUES_ENVIRONMENT_QUERY: _optional_str(
            arguments,
            "environment",
            max_length=120,
        ),
        gateway_params.ISSUES_SEARCH_QUERY: _optional_str(
            arguments, "query", max_length=MAX_FILTER_SEARCH_LENGTH
        ),
    }


def _facet_page_params(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        gateway_params.FACET_SEARCH_QUERY: _optional_str(
            arguments,
            "facet_query",
            max_length=MAX_FILTER_SEARCH_LENGTH,
        ),
        gateway_params.CURSOR_QUERY: _optional_str(
            arguments,
            "cursor",
            max_length=MAX_FILTER_CURSOR_LENGTH,
        ),
        gateway_params.LIMIT_QUERY: _bounded_int(
            arguments,
            "limit",
            DEFAULT_FILTER_FACET_LIMIT,
            1,
            MAX_FILTER_FACET_LIMIT,
        ),
    }


def _read_result(tool_name: str, api_path: str, data: Any) -> dict[str, Any]:
    return {
        "tool": tool_name,
        "data": _redact_read_response_value(data),
        "safety": {
            "mutating": False,
            "dry_run": None,
            "proposal": None,
            "approval_required": False,
            "operation_id": None,
            "api_path": api_path,
            "reason": (
                "This read-only MCP tool only issues an authenticated GET request to the "
                "existing Opsia API Gateway. There is no mutation to preview, approve, "
                "or track as an operation."
            ),
        },
    }


async def _read_list_item(
    client: ManagementApiClient,
    *,
    tool_name: str,
    api_path: str,
    response_key: str,
    id_key: str,
    expected_id: str,
    item_key: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = await client.get_json(api_path, params)
    item = _find_mapping_by_key(
        _list_from_response(data, response_key),
        id_key,
        expected_id,
    )
    return _read_result(
        tool_name,
        api_path,
        {
            item_key: item,
            "available": item is not None,
        },
    )


def _assert_allowed_gateway_route(
    method: str,
    api_path: str,
    allowed_routes: AbstractSet[tuple[str, str]],
) -> None:
    if not _gateway_route_is_allowed(method, api_path, allowed_routes):
        raise ToolInputError(f"{method} {api_path} is not permitted for this MCP tool")


def _gateway_route_is_allowed(
    method: str,
    api_path: str,
    allowed_routes: AbstractSet[tuple[str, str]],
) -> bool:
    return any(
        allowed_method == method and _route_template_matches(allowed_template, api_path)
        for allowed_method, allowed_template in allowed_routes
    )


def _route_template_matches(template: str, api_path: str) -> bool:
    template_segments = _path_segments(template)
    path_segments = _path_segments(api_path)
    if len(template_segments) != len(path_segments):
        return False
    for template_segment, path_segment in zip(template_segments, path_segments, strict=True):
        if _is_route_parameter(template_segment):
            if not path_segment:
                return False
            continue
        if template_segment != path_segment:
            return False
    return True


def _path_segments(path: str) -> tuple[str, ...]:
    return tuple(segment for segment in path.strip("/").split("/") if segment)


def _is_route_parameter(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}") and len(segment) > 2


async def _post_or_propose(
    client: ManagementApiClient,
    arguments: dict[str, Any],
    *,
    tool_name: str,
    api_path: str,
    payload: dict[str, Any],
    operation_keys: tuple[str, ...],
    reason: str,
    method: str = "POST",
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    if method not in WRITE_METHODS:
        raise ToolInputError(f"unsupported MCP write method: {method}")
    _assert_allowed_gateway_route(method, api_path, ALLOWED_WRITE_GATEWAY_ROUTES)
    _validate_json_payload(payload, "payload")
    dry_run = _optional_bool(arguments, "dry_run", default=True)
    approval_confirmed = _optional_bool(arguments, "approval_confirmed", default=False)
    proposal = _write_proposal(method, api_path, payload, headers=headers)
    if dry_run:
        return {
            "tool": tool_name,
            "data": None,
            "safety": {
                "mutating": False,
                "dry_run": True,
                "proposal": proposal,
                "approval_required": True,
                "operation_id": None,
                "api_path": api_path,
                "reason": reason,
            },
        }
    if not approval_confirmed:
        raise ToolInputError("approval_confirmed must be true when dry_run is false")
    if not client.settings.writes_enabled:
        raise ToolInputError(
            f"{OPSIA_MCP_ENABLE_WRITES_ENV}=true is required before MCP write tools can submit"
        )
    if method == "POST":
        raw_data = await client.post_json(api_path, payload, headers=headers)
    else:
        raw_data = await client.patch_json(api_path, payload, headers=headers)
    operation_id = _operation_id_from_response(
        raw_data,
        operation_keys,
    )
    data = _redact_read_response_value(raw_data)
    return {
        "tool": tool_name,
        "data": data,
        "safety": {
            "mutating": True,
            "dry_run": False,
            "proposal": proposal,
            "approval_required": False,
            "operation_id": operation_id,
            "api_path": api_path,
            "reason": reason,
        },
    }


def _write_proposal(
    method: str,
    api_path: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    redacted_body = _redact_proposal_value(payload)
    proposal: dict[str, Any] = {
        "method": method,
        "api_path": api_path,
        "body": redacted_body,
        "body_redacted": redacted_body != payload,
        "uses_existing_gateway_api": True,
    }
    if headers:
        proposal["headers"] = _redact_proposal_headers(headers)
    return proposal


def _redact_proposal_headers(headers: dict[str, str]) -> dict[str, str]:
    return {key: REDACTED_VALUE for key in headers}


def _redact_manifest_preview_data(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    redacted = deepcopy(data)
    if isinstance(redacted.get("diff"), str) and redacted["diff"]:
        redacted["diff"] = REDACTED_VALUE
        redacted["diff_redacted"] = True
    return _redact_response_strings(redacted)


def _redact_response_strings(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_response_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_response_strings(item) for item in value]
    if isinstance(value, str):
        return redact_log_line(value)
    return deepcopy(value)


def _redact_read_response_value(value: Any) -> Any:
    if isinstance(value, dict):
        secret_context = _mapping_describes_secret(value)
        alert_channel_context = _mapping_describes_alert_channel(value)
        has_sensitive_marker = _has_sensitive_proposal_marker(value)
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized_key = _normalized_proposal_identifier(key_text)
            if (
                _is_sensitive_read_key(normalized_key)
                or (secret_context and normalized_key in SECRET_READ_DATA_KEYS)
                or (alert_channel_context and normalized_key in ALERT_CHANNEL_REDACTED_KEYS)
                or (has_sensitive_marker and _is_sensitive_marker_value_key(key_text))
            ):
                redacted[key_text] = REDACTED_VALUE
            else:
                redacted[key_text] = _redact_read_response_value(item)
        return redacted
    if isinstance(value, list):
        return [_redact_read_response_value(item) for item in value]
    if isinstance(value, str):
        return redact_log_line(value)
    return deepcopy(value)


def _is_sensitive_read_key(normalized_key: str) -> bool:
    return normalized_key in SENSITIVE_READ_EXACT_KEYS or _contains_sensitive_proposal_part(
        normalized_key
    )


def _mapping_describes_secret(value: dict[Any, Any]) -> bool:
    kind = value.get("kind") or value.get("resource_kind")
    if isinstance(kind, str) and kind.strip().casefold() == "secret":
        return True
    resource_type = value.get("resource_type") or value.get("type")
    return isinstance(resource_type, str) and resource_type.strip().casefold() == "secret"


def _mapping_describes_alert_channel(value: dict[Any, Any]) -> bool:
    normalized_keys = {_normalized_proposal_identifier(str(key)) for key in value}
    return ALERT_CHANNEL_CONTEXT_KEYS.issubset(normalized_keys) and bool(
        normalized_keys & ALERT_CHANNEL_ENDPOINT_KEYS
    )


def _operation_id_from_response(
    data: Any,
    keys: tuple[str, ...],
) -> str | None:
    if isinstance(data, dict):
        for key in keys:
            value = _response_value(data, key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
    return None


def _response_value(data: dict[str, Any], key: str) -> Any:
    current: Any = data
    for part in key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _list_from_response(data: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    values = data.get(key)
    if not isinstance(values, list):
        return []
    return [dict(item) for item in values if isinstance(item, dict)]


def _find_mapping_by_key(
    values: list[dict[str, Any]],
    key: str,
    expected: str,
) -> dict[str, Any] | None:
    for value in values:
        if str(value.get(key) or "") == expected:
            return value
    return None


def _unique_strings(values: Any) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        unique.append(text)
        seen.add(text)
    return unique


def _pending_runs(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [value for value in values if _has_pending_approval(value)]


def _validate_change_window(*, from_ms: int, to_ms: int, bucket_ms: int) -> None:
    width = to_ms - from_ms
    if width <= 0:
        raise ToolInputError("to_ms must be greater than from_ms")
    if width > MAX_CHANGE_RANGE_MS:
        raise ToolInputError("change timeline range must be 24 hours or less")
    if bucket_ms > width:
        raise ToolInputError("bucket_ms must not be greater than the requested range")
    if (width + bucket_ms - 1) // bucket_ms > MAX_CHANGE_BUCKETS:
        raise ToolInputError("change timeline bucket count must be 1440 or less")


def _has_pending_approval(value: dict[str, Any]) -> bool:
    if str(value.get("approval_status") or "") == PENDING_APPROVAL_STATUS:
        return True
    if str(value.get("status") or value.get("derived_status") or "") == WAITING_FOR_APPROVAL_STATUS:
        return True
    approvals = value.get("approvals")
    if isinstance(approvals, list) and any(
        isinstance(item, dict) and str(item.get("status") or "") == PENDING_APPROVAL_STATUS
        for item in approvals
    ):
        return True
    steps = value.get("steps")
    return isinstance(steps, list) and any(
        isinstance(item, dict) and str(item.get("status") or "") == WAITING_FOR_APPROVAL_STATUS
        for item in steps
    )


def _idempotency_key(arguments: dict[str, Any]) -> str:
    value = _required_str(arguments, "idempotency_key", max_length=200)
    if len(value) < 8:
        raise ToolInputError("idempotency_key must be at least 8 characters")
    return value


def _redact_proposal_value(value: Any) -> Any:
    if isinstance(value, dict):
        has_sensitive_marker = _has_sensitive_proposal_marker(value)
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_proposal_key(key_text) or (
                has_sensitive_marker and _is_sensitive_marker_value_key(key_text)
            ):
                redacted[key_text] = REDACTED_VALUE
            else:
                redacted[key_text] = _redact_proposal_value(item)
        return redacted
    if isinstance(value, list):
        return [_redact_proposal_value(item) for item in value]
    if isinstance(value, str):
        return redact_log_line(value)
    return deepcopy(value)


def _is_sensitive_proposal_key(key: str) -> bool:
    normalized = _normalized_proposal_identifier(key)
    if normalized in SENSITIVE_PROPOSAL_EXACT_KEYS:
        return True
    return _contains_sensitive_proposal_part(normalized)


def _has_sensitive_proposal_marker(value: dict[Any, Any]) -> bool:
    for key, item in value.items():
        if (
            _normalized_proposal_identifier(str(key)) in SENSITIVE_PROPOSAL_MARKER_KEYS
            and isinstance(item, str)
            and _contains_sensitive_proposal_part(_normalized_proposal_identifier(item))
        ):
            return True
    return False


def _is_sensitive_marker_value_key(key: str) -> bool:
    return _normalized_proposal_identifier(key) in SENSITIVE_PROPOSAL_MARKER_VALUE_KEYS


def _contains_sensitive_proposal_part(value: str) -> bool:
    compact = value.replace("_", "")
    return any(
        part in value or part.replace("_", "") in compact for part in SENSITIVE_PROPOSAL_KEY_PARTS
    )


def _normalized_proposal_identifier(value: str) -> str:
    return value.casefold().replace("-", "_").replace(".", "_").replace(" ", "_")


def _schema(
    *,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _string(description: str, *, max_length: int, default: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "string",
        "description": description,
        "minLength": 1,
        "maxLength": max_length,
    }
    if default is not None:
        schema["default"] = default
    return schema


def _object(description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "additionalProperties": True,
    }


def _boolean(description: str, *, default: bool) -> dict[str, Any]:
    return {
        "type": "boolean",
        "description": description,
        "default": default,
    }


def _optional_boolean(description: str) -> dict[str, Any]:
    return {
        "type": "boolean",
        "description": description,
    }


def _integer(
    description: str,
    *,
    minimum: int,
    maximum: int | None = None,
    default: int | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "integer",
        "description": description,
        "minimum": minimum,
    }
    if maximum is not None:
        schema["maximum"] = maximum
    if default is not None:
        schema["default"] = default
    return schema


def _optional_integer(
    description: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> dict[str, Any]:
    return _integer(description, minimum=minimum, maximum=maximum)


def _enum_string(
    description: str,
    values: tuple[str, ...],
    *,
    default: str | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "string",
        "description": description,
        "enum": list(values),
    }
    if default is not None:
        schema["default"] = default
    return schema


def _string_array(
    description: str,
    *,
    max_items: int,
    max_length: int,
) -> dict[str, Any]:
    return {
        "type": "array",
        "description": description,
        "minItems": 1,
        "maxItems": max_items,
        "items": _string("Existing value from the Opsia API.", max_length=max_length),
    }


def _reject_unknown(arguments: dict[str, Any], allowed: AbstractSet[str]) -> None:
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise ToolInputError(f"unknown arguments: {', '.join(unknown)}")


def _required_str(arguments: dict[str, Any], name: str, *, max_length: int) -> str:
    value = _optional_str(arguments, name, max_length=max_length)
    if value is None:
        raise ToolInputError(f"{name} is required")
    return value


def _required_enum(arguments: dict[str, Any], name: str, values: tuple[str, ...]) -> str:
    value = _required_str(arguments, name, max_length=_max_enum_length(values))
    if value not in values:
        raise ToolInputError(f"{name} must be one of: {', '.join(values)}")
    return value


def _optional_enum(
    arguments: dict[str, Any],
    name: str,
    values: tuple[str, ...],
    *,
    default: str,
) -> str:
    value = _optional_str(arguments, name, max_length=_max_enum_length(values))
    if value is None:
        return default
    if value not in values:
        raise ToolInputError(f"{name} must be one of: {', '.join(values)}")
    return value


def _max_enum_length(values: tuple[str, ...]) -> int:
    if not values:
        raise ToolInputError("enum values must not be empty")
    return max(len(value) for value in values)


def _required_object(arguments: dict[str, Any], name: str) -> dict[str, Any]:
    value = arguments.get(name)
    if not isinstance(value, dict):
        raise ToolInputError(f"{name} must be an object")
    return _validate_json_payload(value, name)


def _required_string_list(
    arguments: dict[str, Any],
    name: str,
    *,
    max_items: int,
    item_max_length: int,
    forbidden_characters: str = "",
) -> tuple[str, ...]:
    value = arguments.get(name)
    if not isinstance(value, list):
        raise ToolInputError(f"{name} must be an array")
    if not value or len(value) > max_items:
        raise ToolInputError(f"{name} must contain between 1 and {max_items} values")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ToolInputError(f"{name} values must be strings")
        text = item.strip()
        if not text:
            raise ToolInputError(f"{name} values must not be empty")
        if _has_control_character(text):
            raise ToolInputError(f"{name} values contain unsafe control characters")
        if len(text) > item_max_length:
            raise ToolInputError(f"{name} values must be at most {item_max_length} characters")
        if any(character in text for character in forbidden_characters):
            raise ToolInputError(f"{name} values contain unsupported separator characters")
        if text in seen:
            raise ToolInputError(f"{name} values must be unique")
        normalized.append(text)
        seen.add(text)
    return tuple(normalized)


def _optional_str(arguments: dict[str, Any], name: str, *, max_length: int) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolInputError(f"{name} must be a string")
    text = value.strip()
    if not text:
        return None
    if _has_control_character(text):
        raise ToolInputError(f"{name} contains unsafe control characters")
    if len(text) > max_length:
        raise ToolInputError(f"{name} must be at most {max_length} characters")
    return text


def _bounded_int(
    arguments: dict[str, Any],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolInputError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ToolInputError(f"{name} must be between {minimum} and {maximum}")
    return value


def _required_bounded_int(
    arguments: dict[str, Any],
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    if name not in arguments:
        raise ToolInputError(f"{name} is required")
    return _bounded_int(arguments, name, minimum, minimum, maximum)


def _optional_bounded_int(
    arguments: dict[str, Any],
    name: str,
    minimum: int,
    maximum: int | None = None,
) -> int | None:
    if name not in arguments or arguments[name] is None:
        return None
    value = arguments[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolInputError(f"{name} must be an integer")
    if value < minimum:
        raise ToolInputError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ToolInputError(f"{name} must be between {minimum} and {maximum}")
    return value


def _optional_bool(arguments: dict[str, Any], name: str, *, default: bool) -> bool:
    value = arguments.get(name, default)
    if not isinstance(value, bool):
        raise ToolInputError(f"{name} must be a boolean")
    return value


def _optional_bool_or_none(arguments: dict[str, Any], name: str) -> bool | None:
    if name not in arguments or arguments[name] is None:
        return None
    return _optional_bool(arguments, name, default=False)


def _validate_json_payload(value: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ToolInputError(f"{name} must be a finite JSON object") from exc
    if len(encoded) > MAX_WRITE_PAYLOAD_BYTES:
        raise ToolInputError(
            f"{name} must be at most {MAX_WRITE_PAYLOAD_BYTES} bytes when encoded as JSON"
        )
    return deepcopy(value)


def _reject_direct_execution_flags(payload: dict[str, Any]) -> None:
    if _contains_direct_execution_flag(payload):
        raise ToolInputError(
            "create_command_request cannot set direct execution confirmation flags"
        )


def _contains_direct_execution_flag(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            str(key) in DIRECT_EXECUTION_KEYS or _contains_direct_execution_flag(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_direct_execution_flag(item) for item in value)
    return False


def _format_path(template: str, **values: str) -> str:
    escaped = {key: quote(value, safe="") for key, value in values.items()}
    return template.format(**escaped)


def _has_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def dumps_tool_result(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, sort_keys=True)
