from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from kubernetes_api import (
    kubernetes_api_base_url,
    kubernetes_client,
    kubernetes_headers,
    service_account_token,
)
from queries import MetadataSnapshotQuery
from telemetry_registry import telemetry

from config import KUBERNETES_API_TIMEOUT_SECONDS, TARGET_CLUSTER_ID_ENV
from packages.config.constants import Target
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.target import TARGET_NAMESPACE
from providers.base import ConfigReader
from providers.collection_limits import (
    COLLECTION_LIMITS_KEY,
    attach_collection_limits,
    limit_payload_list,
    limit_payload_size,
)
from providers.kubernetes_utils import (
    K8S_KIND_DEPLOYMENT,
    K8S_RESOURCE_DEPLOYMENTS,
    K8S_RESOURCE_ENDPOINT_SLICES,
    K8S_RESOURCE_PODS,
    K8S_RESOURCE_REPLICASETS,
    K8S_RESOURCE_RESOURCE_QUOTAS,
    K8S_RESOURCE_SERVICES,
    items,
    metadata,
    object_or_empty,
)
from providers.metadata_config_objects import (
    CONFIG_OBJECT_FORBIDDEN,
    CONFIG_OBJECT_NOT_FOUND,
    CONFIG_OBJECT_OK,
    config_object_resource,
    referenced_config_object_refs,
    referenced_config_object_summary,
)
from providers.metadata_endpoint_slices import endpoint_slice_ready_endpoint_snapshots
from providers.metadata_ownership import pods_for_deployment
from providers.metadata_resource_quotas import resource_quota_snapshots
from providers.metadata_service_selectors import service_selector_match_snapshots
from providers.metadata_workload_snapshots import (
    current_workload_detail_snapshot,
    current_workload_snapshots,
    pod_template,
)

CHANGE_CONTEXT_KEY = "change_context"
CURRENT_WORKLOAD_SNAPSHOT_KEY = "current_workload_snapshot"
CURRENT_WORKLOAD_SNAPSHOTS_KEY = "current_workload_snapshots"
ENDPOINT_SLICE_READY_ENDPOINTS_KEY = "endpoint_slice_ready_endpoints"
REFERENCED_CONFIG_OBJECTS_KEY = "referenced_config_objects"
RESOURCE_QUOTAS_KEY = "resource_quotas"
SERVICE_SELECTOR_MATCHES_KEY = "service_selector_matches"
DEFAULT_METADATA_QUERIES = {
    CHANGE_CONTEXT_KEY,
    CURRENT_WORKLOAD_SNAPSHOTS_KEY,
    K8S_RESOURCE_DEPLOYMENTS,
}
DEPLOYMENT_QUERY_PREFIXES = {
    K8S_KIND_DEPLOYMENT.lower(),
    K8S_RESOURCE_DEPLOYMENTS,
}
MAX_CURRENT_WORKLOAD_SNAPSHOTS = 200
MAX_SERVICE_SELECTOR_MATCHES = 200
MAX_ENDPOINT_SLICE_READY_ENDPOINTS = 200
MAX_REFERENCED_CONFIG_OBJECTS = 100
MAX_RESOURCE_QUOTAS = 50
CHANGE_CONTEXT_LIST_LIMITS = {
    CURRENT_WORKLOAD_SNAPSHOTS_KEY: MAX_CURRENT_WORKLOAD_SNAPSHOTS,
    SERVICE_SELECTOR_MATCHES_KEY: MAX_SERVICE_SELECTOR_MATCHES,
    ENDPOINT_SLICE_READY_ENDPOINTS_KEY: MAX_ENDPOINT_SLICE_READY_ENDPOINTS,
    REFERENCED_CONFIG_OBJECTS_KEY: MAX_REFERENCED_CONFIG_OBJECTS,
    RESOURCE_QUOTAS_KEY: MAX_RESOURCE_QUOTAS,
}


@dataclass(frozen=True)
class MetadataQueryTarget:
    """Describe the Deployment scope for one metadata query."""

    namespace: str
    deployment_name: str | None = None


@telemetry.source(
    source="metadata",
    evidence_key="metadata",
    query_type=MetadataSnapshotQuery,
    empty_payload=dict,
)
class MetadataProvider:
    """Collect change context metadata for RCA.
    It builds the metadata evidence bucket.
    """

    span_name = "metadata.collect"
    query_count_attribute = "metadata.query_count"
    result_count_attribute = "metadata.result_count"
    timeout_seconds = KUBERNETES_API_TIMEOUT_SECONDS
    failure_message = "metadata collection failed"
    queries: tuple[MetadataSnapshotQuery, ...] = ()

    def __init__(
        self,
        *,
        cluster_id: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Store the target cluster id and an optional HTTP transport."""
        self.cluster_id = cluster_id
        self.transport = transport

    @classmethod
    def from_config(cls, read_config: ConfigReader) -> MetadataProvider:
        """Create the provider from agent config values."""
        return cls(cluster_id=read_config(TARGET_CLUSTER_ID_ENV, Target.DEFAULT_CLUSTER_ID))

    async def query(
        self,
        _client: httpx.AsyncClient,
        telemetry_query: MetadataSnapshotQuery,
    ) -> JsonObject:
        """Read current Deployment metadata from Kubernetes."""
        base_url = kubernetes_api_base_url()
        token = service_account_token()
        target = metadata_query_target(telemetry_query)

        if not base_url or not token:
            return {
                "cluster_id": self.cluster_id,
                "collected_at": datetime.now(UTC).isoformat(),
                CHANGE_CONTEXT_KEY: empty_change_context(),
            }

        headers = kubernetes_headers(token)

        async with kubernetes_client(self.transport) as client:
            if target.deployment_name:
                deployment = await self.get_json(
                    client,
                    base_url,
                    headers,
                    namespaced_apps_path(
                        target.namespace,
                        K8S_RESOURCE_DEPLOYMENTS,
                        target.deployment_name,
                    ),
                    allow_not_found=True,
                )
                if deployment:
                    replicasets = await self.get_json(
                        client,
                        base_url,
                        headers,
                        namespaced_apps_path(target.namespace, K8S_RESOURCE_REPLICASETS),
                    )
                    pod_list = await self.get_json(
                        client,
                        base_url,
                        headers,
                        namespaced_core_path(target.namespace, K8S_RESOURCE_PODS),
                    )
                    service_list = await self.get_json(
                        client,
                        base_url,
                        headers,
                        namespaced_core_path(target.namespace, K8S_RESOURCE_SERVICES),
                    )
                    resource_quota_list = await self.get_json(
                        client,
                        base_url,
                        headers,
                        namespaced_core_path(
                            target.namespace,
                            K8S_RESOURCE_RESOURCE_QUOTAS,
                        ),
                        allow_empty_list=True,
                    )
                    endpoint_slice_list = await self.get_json(
                        client,
                        base_url,
                        headers,
                        namespaced_discovery_path(
                            target.namespace,
                            K8S_RESOURCE_ENDPOINT_SLICES,
                        ),
                        allow_empty_list=True,
                    )
                    referenced_config_objects = await self.get_config_object_summaries(
                        client,
                        base_url,
                        headers,
                        deployment,
                        target.namespace,
                    )
                    change_context = specific_workload_change_context(
                        deployment,
                        items(replicasets),
                        items(pod_list),
                        items(service_list),
                        items(endpoint_slice_list),
                        items(resource_quota_list),
                        referenced_config_objects,
                    )
                else:
                    change_context = empty_change_context()
            else:
                deployments = await self.get_json(
                    client,
                    base_url,
                    headers,
                    namespaced_apps_path(target.namespace, K8S_RESOURCE_DEPLOYMENTS),
                )
                deployment_items = items(deployments)
                replicasets: JsonObject = {"items": []}
                snapshots: list[JsonObject] = []
                if deployment_items:
                    replicasets = await self.get_json(
                        client,
                        base_url,
                        headers,
                        namespaced_apps_path(target.namespace, K8S_RESOURCE_REPLICASETS),
                    )
                pods = await self.get_json(
                    client,
                    base_url,
                    headers,
                    namespaced_core_path(target.namespace, K8S_RESOURCE_PODS),
                )
                services = await self.get_json(
                    client,
                    base_url,
                    headers,
                    namespaced_core_path(target.namespace, K8S_RESOURCE_SERVICES),
                )
                resource_quotas = await self.get_json(
                    client,
                    base_url,
                    headers,
                    namespaced_core_path(
                        target.namespace,
                        K8S_RESOURCE_RESOURCE_QUOTAS,
                    ),
                    allow_empty_list=True,
                )
                endpoint_slices = await self.get_json(
                    client,
                    base_url,
                    headers,
                    namespaced_discovery_path(
                        target.namespace,
                        K8S_RESOURCE_ENDPOINT_SLICES,
                    ),
                    allow_empty_list=True,
                )
                if deployment_items:
                    snapshots = current_workload_snapshots(
                        deployment_items,
                        items(replicasets),
                        items(pods),
                    )
                change_context = {
                    CURRENT_WORKLOAD_SNAPSHOTS_KEY: snapshots,
                    SERVICE_SELECTOR_MATCHES_KEY: service_selector_match_snapshots(
                        items(services),
                        items(pods),
                    ),
                    ENDPOINT_SLICE_READY_ENDPOINTS_KEY: (
                        endpoint_slice_ready_endpoint_snapshots(
                            items(endpoint_slices),
                        )
                    ),
                    RESOURCE_QUOTAS_KEY: resource_quota_snapshots(
                        items(resource_quotas),
                    ),
                }

        return {
            "cluster_id": self.cluster_id,
            "collected_at": datetime.now(UTC).isoformat(),
            CHANGE_CONTEXT_KEY: change_context,
        }

    async def get_json(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        path: str,
        *,
        allow_not_found: bool = False,
        allow_empty_list: bool = False,
    ) -> JsonObject:
        """Call one Kubernetes API path and return a JSON object."""
        response = await client.get(f"{base_url}{path}", headers=headers)
        if allow_not_found and response.status_code == httpx.codes.NOT_FOUND:
            return {}
        if allow_empty_list and response.status_code in {
            httpx.codes.FORBIDDEN,
            httpx.codes.NOT_FOUND,
        }:
            return {"items": []}
        response.raise_for_status()
        payload = response.json()

        return payload if isinstance(payload, dict) else {"items": []}

    async def get_optional_json(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        path: str,
    ) -> tuple[JsonObject, str]:
        """Call one optional Kubernetes API path."""
        response = await client.get(f"{base_url}{path}", headers=headers)
        if response.status_code == httpx.codes.FORBIDDEN:
            return {}, CONFIG_OBJECT_FORBIDDEN
        if response.status_code == httpx.codes.NOT_FOUND:
            return {}, CONFIG_OBJECT_NOT_FOUND
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}, CONFIG_OBJECT_OK

    async def get_config_object_summaries(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        deployment: JsonObject,
        namespace: str,
    ) -> list[JsonObject]:
        """Read safe metadata for referenced ConfigMaps and Secrets."""
        summaries: list[JsonObject] = []
        for reference in referenced_config_object_refs(deployment, namespace):
            kind = str(reference.get("kind") or "")
            resource = config_object_resource(kind)
            payload, access = await self.get_optional_json(
                client,
                base_url,
                headers,
                namespaced_core_path(
                    namespace,
                    resource,
                    str(reference.get("name") or ""),
                ),
            )
            summaries.append(referenced_config_object_summary(reference, payload, access))
        return summaries

    def empty_results(self) -> JsonObject:
        """Create an empty metadata evidence bucket."""
        return {
            CHANGE_CONTEXT_KEY: empty_change_context(),
        }

    def append_result(
        self,
        results: JsonObject,
        telemetry_query: MetadataSnapshotQuery,
        payload: JsonObject,
    ) -> None:
        """Normalize one metadata result and merge it into the bucket."""
        change_context = object_or_empty(results.get(CHANGE_CONTEXT_KEY))
        merge_change_context(
            change_context,
            self.normalize_payload(payload, telemetry_query),
        )
        results[CHANGE_CONTEXT_KEY] = change_context or empty_change_context()

    def build_response(self, results: JsonObject) -> JsonObject:
        """Return the finished metadata evidence bucket."""
        change_context = object_or_empty(results.get(CHANGE_CONTEXT_KEY))
        results[CHANGE_CONTEXT_KEY] = (
            limit_change_context(change_context) if change_context else empty_change_context()
        )
        return results

    def normalize_payload(
        self,
        payload: JsonObject,
        _telemetry_query: MetadataSnapshotQuery,
    ) -> JsonObject:
        """Turn raw metadata data into the change context shape."""
        change_context = payload.get(CHANGE_CONTEXT_KEY, {})

        if not isinstance(change_context, dict):
            return empty_change_context()

        snapshots = change_context.get(CURRENT_WORKLOAD_SNAPSHOTS_KEY)
        snapshot = change_context.get(CURRENT_WORKLOAD_SNAPSHOT_KEY)
        endpoint_slices = change_context.get(ENDPOINT_SLICE_READY_ENDPOINTS_KEY)
        referenced_config_objects = change_context.get(REFERENCED_CONFIG_OBJECTS_KEY)
        resource_quotas = change_context.get(RESOURCE_QUOTAS_KEY)
        service_matches = change_context.get(SERVICE_SELECTOR_MATCHES_KEY)
        normalized: JsonObject = {}

        if isinstance(snapshots, list):
            normalized[CURRENT_WORKLOAD_SNAPSHOTS_KEY] = [
                item for item in snapshots if isinstance(item, dict)
            ]

        if isinstance(snapshot, dict) and snapshot:
            normalized[CURRENT_WORKLOAD_SNAPSHOT_KEY] = snapshot

        if isinstance(service_matches, list):
            normalized[SERVICE_SELECTOR_MATCHES_KEY] = [
                item for item in service_matches if isinstance(item, dict)
            ]

        if isinstance(endpoint_slices, list):
            normalized[ENDPOINT_SLICE_READY_ENDPOINTS_KEY] = [
                item for item in endpoint_slices if isinstance(item, dict)
            ]

        if isinstance(referenced_config_objects, list):
            normalized[REFERENCED_CONFIG_OBJECTS_KEY] = [
                item for item in referenced_config_objects if isinstance(item, dict)
            ]

        if isinstance(resource_quotas, list):
            normalized[RESOURCE_QUOTAS_KEY] = [
                item for item in resource_quotas if isinstance(item, dict)
            ]

        return limit_change_context(normalized) or empty_change_context()


def metadata_query_target(telemetry_query: MetadataSnapshotQuery) -> MetadataQueryTarget:
    """Turn a metadata query string into a Deployment scope."""
    query = telemetry_query.query.strip()
    if not query or query in DEFAULT_METADATA_QUERIES:
        return MetadataQueryTarget(namespace=TARGET_NAMESPACE)

    parts = [part.strip() for part in query.split("/") if part.strip()]
    if len(parts) == 2 and parts[0].lower() in DEPLOYMENT_QUERY_PREFIXES:
        return MetadataQueryTarget(namespace=TARGET_NAMESPACE, deployment_name=parts[1])
    if len(parts) == 3 and parts[0].lower() in DEPLOYMENT_QUERY_PREFIXES:
        return MetadataQueryTarget(namespace=parts[1], deployment_name=parts[2])
    if len(parts) == 2:
        return MetadataQueryTarget(namespace=parts[0], deployment_name=parts[1])
    if len(parts) == 1:
        return MetadataQueryTarget(namespace=parts[0])

    return MetadataQueryTarget(namespace=TARGET_NAMESPACE)


def namespaced_apps_path(
    namespace: str,
    resource: str,
    name: str | None = None,
) -> str:
    """Build a Kubernetes apps/v1 namespaced API path."""
    path = f"/apis/apps/v1/namespaces/{path_part(namespace)}/{path_part(resource)}"
    if name:
        path = f"{path}/{path_part(name)}"
    return path


def namespaced_core_path(
    namespace: str,
    resource: str,
    name: str | None = None,
) -> str:
    """Build a Kubernetes core/v1 namespaced API path."""
    path = f"/api/v1/namespaces/{path_part(namespace)}/{path_part(resource)}"
    if name:
        path = f"{path}/{path_part(name)}"
    return path


def namespaced_discovery_path(
    namespace: str,
    resource: str,
    name: str | None = None,
) -> str:
    """Build a Kubernetes discovery.k8s.io/v1 namespaced API path."""
    path = f"/apis/discovery.k8s.io/v1/namespaces/{path_part(namespace)}/{path_part(resource)}"
    if name:
        path = f"{path}/{path_part(name)}"
    return path


def path_part(value: str) -> str:
    """Escape one value for a Kubernetes API path."""
    return quote(value, safe="")


def specific_workload_change_context(
    deployment: JsonObject,
    replicasets: list[JsonObject],
    pods: list[JsonObject],
    services: list[JsonObject],
    endpoint_slices: list[JsonObject],
    resource_quotas: list[JsonObject],
    referenced_config_objects: list[JsonObject],
) -> JsonObject:
    """Build a change context for one Deployment."""
    if not deployment:
        return empty_change_context()
    target_pods = pods_for_deployment(deployment, replicasets, pods)
    target_labels = object_or_empty(metadata(pod_template(deployment)).get("labels"))
    service_matches = service_selector_match_snapshots(
        services,
        pods,
        target_labels=target_labels,
        target_pods=target_pods,
    )
    return {
        CURRENT_WORKLOAD_SNAPSHOT_KEY: current_workload_detail_snapshot(
            deployment,
            replicasets,
            pods,
        ),
        SERVICE_SELECTOR_MATCHES_KEY: service_matches,
        ENDPOINT_SLICE_READY_ENDPOINTS_KEY: endpoint_slice_ready_endpoint_snapshots(
            endpoint_slices,
            service_matches=service_matches,
        ),
        REFERENCED_CONFIG_OBJECTS_KEY: referenced_config_objects,
        RESOURCE_QUOTAS_KEY: resource_quota_snapshots(resource_quotas),
    }


def merge_change_context(target: JsonObject, source: JsonObject) -> None:
    """Merge one normalized change context into another."""
    for key in CHANGE_CONTEXT_LIST_LIMITS:
        merge_unique_context_list(target, source, key)

    snapshot = source.get(CURRENT_WORKLOAD_SNAPSHOT_KEY)
    if isinstance(snapshot, dict) and snapshot:
        if target.get(CURRENT_WORKLOAD_SNAPSHOTS_KEY) == []:
            target.pop(CURRENT_WORKLOAD_SNAPSHOTS_KEY, None)
        target[CURRENT_WORKLOAD_SNAPSHOT_KEY] = snapshot

    collection_limits = source.get(COLLECTION_LIMITS_KEY)
    if isinstance(collection_limits, dict) and collection_limits:
        target[COLLECTION_LIMITS_KEY] = collection_limits


def merge_unique_context_list(target: JsonObject, source: JsonObject, key: str) -> None:
    """Append one metadata list while retaining order and resource uniqueness."""

    incoming = source.get(key)
    if not isinstance(incoming, list):
        return
    existing = target.get(key)
    merged = (
        [item for item in existing if isinstance(item, dict)]
        if isinstance(existing, list)
        else []
    )
    seen = {change_context_item_identity(key, item) for item in merged}
    for item in incoming:
        if not isinstance(item, dict):
            continue
        identity = change_context_item_identity(key, item)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(item)
    target[key] = merged


def change_context_item_identity(key: str, item: JsonObject) -> tuple[str, ...]:
    """Return a stable resource identity for one normalized metadata list item."""

    nested_key = {
        CURRENT_WORKLOAD_SNAPSHOTS_KEY: "workload",
        SERVICE_SELECTOR_MATCHES_KEY: "service",
        ENDPOINT_SLICE_READY_ENDPOINTS_KEY: "endpoint_slice",
    }.get(key)
    identity = object_or_empty(item.get(nested_key)) if nested_key else item
    namespace = str(identity.get("namespace") or "")
    name = str(identity.get("name") or "")
    if namespace and name:
        return (
            key,
            str(identity.get("kind") or ""),
            namespace,
            name,
            str(identity.get("uid") or ""),
        )
    return (key, json.dumps(item, sort_keys=True, separators=(",", ":"), default=str))


def empty_change_context() -> JsonObject:
    """Build the default change context shape."""
    return {
        CURRENT_WORKLOAD_SNAPSHOTS_KEY: [],
    }


def limit_change_context(change_context: JsonObject) -> JsonObject:
    """Limit large metadata lists and record what was truncated."""
    limits: JsonObject = {}
    for key, max_items in CHANGE_CONTEXT_LIST_LIMITS.items():
        limit_payload_list(change_context, key, max_items, limits)
    limit_payload_size(change_context, list_keys=CHANGE_CONTEXT_LIST_LIMITS, limits=limits)
    attach_collection_limits(change_context, limits)
    return change_context
