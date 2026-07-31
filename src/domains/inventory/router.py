"""inventory 도메인 HTTP 라우터 — agent 수집 데이터 수신."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from domains.identity.dependencies import (
    ClusterAgentIdentity,
    require_cluster_access,
    require_cluster_agent,
    require_session,
)
from domains.inventory.capabilities import resource_capabilities_response
from domains.inventory.config_references import (
    CONFIG_REFERENCE_DEFAULT_WORKLOAD_LIMIT,
    CONFIG_REFERENCE_MAX_WORKLOAD_LIMIT,
    config_reference_list_response,
)
from domains.inventory.events import InventorySnapshotRecordedBody
from domains.inventory.ingest import ingest_inventory_snapshot
from domains.inventory.provider_detail import provider_detail_projection
from domains.inventory.resource_count_evidence import project_inventory_resource_counts_evidence
from domains.inventory.resource_types import (
    WORKLOAD_RESOURCE_TYPE,
    include_discoverable_zero_counts,
    workload_kind_for_resource_type,
)
from domains.inventory.workload_revisions import workload_revision_history_response
from domains.resource_access.projection import (
    ResourceAccessUnavailable,
    access_snapshot_from_inventory,
    resource_access_projection,
    resource_supports_access_projection,
)
from packages.contracts.gateway import limits as gateway_limits
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.requests import InventorySnapshotRequest
from packages.contracts.gateway.responses import (
    ClusterUsageResponse,
    ClusterUsageSample,
    ConfigReferenceListResponse,
    InventoryResourceDetailResponse,
    InventoryResourceListResponse,
    InventoryResourceResponse,
    InventorySnapshotResponse,
    InventorySummaryResponse,
    KubernetesApiResourcesResponse,
    ResourceCapabilitiesResponse,
    WorkloadRevisionHistoryResponse,
)
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, Permission
from packages.contracts.kubernetes_discovery import (
    ApiResourceDiscoveryObservation,
    canonical_kubernetes_namespaces,
)
from packages.contracts.resource_access import KubernetesAccessUnavailableResponse
from packages.runtime.dependencies import (
    get_dashboard_ready_fanout,
    get_db,
    get_events,
    get_timeline_fanout,
)

router = APIRouter()
INVENTORY_NAMESPACE_SUMMARY_UNAVAILABLE = "inventory namespace summary is unavailable"


@router.post(
    gateway_routes.AGENT_INVENTORY_SNAPSHOTS_PATH,
    response_model=InventorySnapshotResponse,
)
async def record_inventory_snapshot(
    payload: InventorySnapshotRequest,
    identity: ClusterAgentIdentity = Depends(require_cluster_agent),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    timeline_fanout: Any = Depends(get_timeline_fanout),
    dashboard_ready_fanout: Any = Depends(get_dashboard_ready_fanout),
) -> InventorySnapshotResponse:
    if payload.cluster_id != identity.cluster_id:
        raise HTTPException(status_code=403, detail="cluster_id does not match agent identity")

    async def record_snapshot_event(result: dict[str, Any]) -> None:
        # An ignored stale observation is retained only for collection audit; it
        # must not impersonate an accepted inventory state transition downstream.
        if result.get("accepted") is not True:
            return
        await events.accept_body(
            InventorySnapshotRecordedBody(
                workspace_id=identity.workspace_id,
                cluster_id=identity.cluster_id,
                snapshot_id=str(result["snapshot_id"]),
                agent_id=payload.agent_id,
                resource_count=int(result["resource_count"]),
                resource_types=list(result["resource_types"]),
            )
        )

    result = await ingest_inventory_snapshot(
        db=db,
        workspace_id=identity.workspace_id,
        cluster_id=identity.cluster_id,
        agent_id=payload.agent_id,
        payload=payload.model_dump(),
        fanout=timeline_fanout,
        ready_fanout=dashboard_ready_fanout,
        after_persist=record_snapshot_event,
    )
    return InventorySnapshotResponse(**result)


def require_inventory_access(db: Any, current: Any, workspace_id: str, cluster_id: str) -> None:
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.INVENTORY_READ.value,
    )


def inventory_list_response(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    resource_type: str | None,
    namespace: str | None,
    include_deleted: bool,
    limit: int,
) -> InventoryResourceListResponse:
    requested_resource_type = resource_type.strip().casefold() if resource_type else None
    workload_kind = workload_kind_for_resource_type(requested_resource_type)
    if workload_kind is not None:
        resources = db.list_inventory_resources_by_kind(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            resource_type=WORKLOAD_RESOURCE_TYPE,
            kind=workload_kind,
            namespace=namespace,
            include_deleted=include_deleted,
            limit=limit,
        )
        resources = [
            {**resource, "resource_type": requested_resource_type} for resource in resources
        ]
    else:
        resources = db.list_inventory_resources(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            resource_type=requested_resource_type,
            namespace=namespace,
            include_deleted=include_deleted,
            limit=limit,
        )
    return InventoryResourceListResponse(
        cluster_id=cluster_id,
        resource_type=requested_resource_type,
        resources=[
            InventoryResourceResponse(**public_inventory_resource(resource))
            for resource in resources
        ],
    )


def public_inventory_resource(resource: dict[str, Any]) -> dict[str, Any]:
    """Browser inventory list response에서 raw Kubernetes object를 제거한다."""
    item = dict(resource)
    item.pop("raw", None)
    return item


@router.get(
    gateway_routes.CLUSTER_INVENTORY_RESOURCES_PATH,
    response_model=InventoryResourceListResponse,
)
async def list_inventory_resources(
    cluster_id: str,
    resource_type: str | None = None,
    namespace: str | None = None,
    include_deleted: bool = False,
    limit: int = Query(
        default=gateway_limits.INVENTORY_RESOURCE_DEFAULT_LIMIT,
        ge=1,
        le=gateway_limits.INVENTORY_RESOURCE_MAX_LIMIT,
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> InventoryResourceListResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_inventory_access(db, current, workspace_id, cluster_id)
    return inventory_list_response(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type=resource_type,
        namespace=namespace,
        include_deleted=include_deleted,
        limit=limit,
    )


@router.get(
    gateway_routes.CLUSTER_CONFIG_REFERENCES_PATH,
    response_model=ConfigReferenceListResponse,
)
async def list_config_references(
    cluster_id: Annotated[
        str,
        Path(min_length=1, max_length=gateway_limits.CLUSTER_ID_MAX_LENGTH),
    ],
    namespace: str | None = Query(
        default=None,
        max_length=gateway_limits.KUBERNETES_NAME_MAX_LENGTH,
    ),
    limit: int = Query(
        default=CONFIG_REFERENCE_DEFAULT_WORKLOAD_LIMIT,
        ge=1,
        le=CONFIG_REFERENCE_MAX_WORKLOAD_LIMIT,
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ConfigReferenceListResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_inventory_access(db, current, workspace_id, cluster_id)
    return config_reference_list_response(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespace=namespace,
        workload_limit=limit,
    )


@router.get(
    gateway_routes.CLUSTER_INVENTORY_RESOURCE_DETAIL_PATH,
    response_model=InventoryResourceDetailResponse,
)
async def get_inventory_resource_detail(
    cluster_id: str,
    resource_type: str = Query(min_length=1, max_length=80),
    kind: str = Query(min_length=1, max_length=120),
    name: str = Query(min_length=1, max_length=253),
    namespace: str | None = Query(default=None, max_length=253),
    related_limit: int = Query(
        default=gateway_limits.INVENTORY_RELATED_DEFAULT_LIMIT,
        ge=1,
        le=gateway_limits.INVENTORY_RELATED_MAX_LIMIT,
    ),
    event_limit: int = Query(
        default=gateway_limits.INVENTORY_EVENT_DEFAULT_LIMIT,
        ge=1,
        le=gateway_limits.INVENTORY_EVENT_MAX_LIMIT,
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> InventoryResourceDetailResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_inventory_access(db, current, workspace_id, cluster_id)
    requested_resource_type = resource_type.strip().casefold()
    workload_kind = workload_kind_for_resource_type(requested_resource_type)
    if workload_kind is not None and workload_kind.casefold() != kind.strip().casefold():
        raise HTTPException(status_code=404, detail="inventory resource not found")
    stored_resource_type = (
        WORKLOAD_RESOURCE_TYPE if workload_kind is not None else requested_resource_type
    )
    resource = db.get_inventory_resource(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type=stored_resource_type,
        kind=kind,
        namespace=namespace,
        name=name,
    )
    if resource is None:
        raise HTTPException(status_code=404, detail="inventory resource not found")
    public_resource = public_inventory_resource(resource)
    if workload_kind is not None:
        public_resource["resource_type"] = requested_resource_type
    related = {
        group: [InventoryResourceResponse(**public_inventory_resource(item)) for item in items]
        for group, items in db.list_related_inventory_resources(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            resource=resource,
            limit=related_limit,
        ).items()
    }
    events = [
        InventoryResourceResponse(**public_inventory_resource(event))
        for event in db.list_resource_events(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            resource=resource,
            limit=event_limit,
        )
    ]
    access = None
    if resource_supports_access_projection(public_resource):
        snapshot = db.latest_inventory_snapshot(workspace_id, cluster_id)
        try:
            access = resource_access_projection(
                access_snapshot_from_inventory(snapshot),
                public_resource,
            )
        except ResourceAccessUnavailable:
            access_source = None
            snapshot_summary = snapshot.get("summary") if isinstance(snapshot, dict) else None
            source_summary = (
                snapshot_summary.get("summary") if isinstance(snapshot_summary, dict) else None
            )
            if isinstance(source_summary, dict):
                access_source = source_summary.get("resource_access")
            raw_reasons = (
                access_source.get("reason_codes") if isinstance(access_source, dict) else None
            )
            reasons = tuple(
                reason for reason in raw_reasons or () if isinstance(reason, str) and reason
            )
            access = KubernetesAccessUnavailableResponse(
                reason_codes=reasons or ("resource_access_unavailable",),
            )
    return InventoryResourceDetailResponse(
        cluster_id=cluster_id,
        identity={
            "resource_type": requested_resource_type,
            "kind": kind,
            "namespace": namespace,
            "name": name,
        },
        resource=InventoryResourceResponse(**public_resource),
        provider_detail=provider_detail_projection(resource),
        access=access,
        related=related,
        events=events,
    )


@router.get(
    gateway_routes.RESOURCE_CAPABILITIES_PATH,
    response_model=ResourceCapabilitiesResponse,
)
async def get_resource_capabilities(
    resource: str = Query(min_length=1, max_length=255),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ResourceCapabilitiesResponse:
    """권한·지원·안전 정책을 모두 만족하는 resource action만 공개한다."""
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    inventory_resource = db.get_inventory_resource_by_key(
        workspace_id=workspace_id,
        inventory_key=resource,
    )
    if inventory_resource is None:
        raise HTTPException(status_code=404, detail="inventory resource not found")
    cluster_id = str(inventory_resource["cluster_id"])
    require_inventory_access(db, current, workspace_id, cluster_id)
    return resource_capabilities_response(
        db,
        workspace_id=workspace_id,
        current=current,
        resource=inventory_resource,
    )


@router.get(
    gateway_routes.RESOURCE_WORKLOAD_ROLLBACK_PATH,
    response_model=WorkloadRevisionHistoryResponse,
)
async def get_workload_revision_history(
    resource_id: str,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=50),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> WorkloadRevisionHistoryResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    resource = db.get_inventory_resource_by_key(
        workspace_id=workspace_id,
        inventory_key=resource_id,
    )
    if resource is None:
        raise HTTPException(status_code=404, detail="inventory resource not found")
    cluster_id = str(resource["cluster_id"])
    require_inventory_access(db, current, workspace_id, cluster_id)
    return workload_revision_history_response(
        db,
        workspace_id=workspace_id,
        resource=resource,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    gateway_routes.CLUSTER_INVENTORY_WORKLOADS_PATH,
    response_model=InventoryResourceListResponse,
)
async def list_inventory_workloads(
    cluster_id: str,
    namespace: str | None = None,
    limit: int = Query(
        default=gateway_limits.INVENTORY_RESOURCE_DEFAULT_LIMIT,
        ge=1,
        le=gateway_limits.INVENTORY_RESOURCE_MAX_LIMIT,
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> InventoryResourceListResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_inventory_access(db, current, workspace_id, cluster_id)
    return inventory_list_response(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type="workload",
        namespace=namespace,
        include_deleted=False,
        limit=limit,
    )


@router.get(
    gateway_routes.CLUSTER_INVENTORY_SERVICES_PATH,
    response_model=InventoryResourceListResponse,
)
async def list_inventory_services(
    cluster_id: str,
    namespace: str | None = None,
    limit: int = Query(
        default=gateway_limits.INVENTORY_RESOURCE_DEFAULT_LIMIT,
        ge=1,
        le=gateway_limits.INVENTORY_RESOURCE_MAX_LIMIT,
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> InventoryResourceListResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_inventory_access(db, current, workspace_id, cluster_id)
    return inventory_list_response(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type="service",
        namespace=namespace,
        include_deleted=False,
        limit=limit,
    )


@router.get(
    gateway_routes.CLUSTER_INVENTORY_EVENTS_PATH,
    response_model=InventoryResourceListResponse,
)
async def list_inventory_events(
    cluster_id: str,
    namespace: str | None = None,
    limit: int = Query(
        default=gateway_limits.INVENTORY_RESOURCE_DEFAULT_LIMIT,
        ge=1,
        le=gateway_limits.INVENTORY_RESOURCE_MAX_LIMIT,
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> InventoryResourceListResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_inventory_access(db, current, workspace_id, cluster_id)
    return inventory_list_response(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type="event",
        namespace=namespace,
        include_deleted=False,
        limit=limit,
    )


@router.get(gateway_routes.CLUSTER_USAGE_PATH, response_model=ClusterUsageResponse)
async def get_cluster_usage(
    cluster_id: str,
    limit: int = Query(
        default=gateway_limits.CLUSTER_USAGE_DEFAULT_LIMIT,
        ge=1,
        le=gateway_limits.CLUSTER_USAGE_MAX_LIMIT,
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ClusterUsageResponse:
    """스냅샷마다 적재되는 실측 usage 롤업 시계열 — 콘솔 추이 차트용."""
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_inventory_access(db, current, workspace_id, cluster_id)
    samples = db.list_cluster_usage_samples(workspace_id, cluster_id, limit=limit)
    return ClusterUsageResponse(
        cluster_id=cluster_id,
        samples=[ClusterUsageSample(**sample) for sample in samples],
    )


@router.get(
    gateway_routes.CLUSTER_INVENTORY_SUMMARY_PATH,
    response_model=InventorySummaryResponse,
)
async def get_inventory_summary(
    cluster_id: str,
    namespaces: Annotated[str | None, Query(max_length=2_048)] = None,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> InventorySummaryResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_inventory_access(db, current, workspace_id, cluster_id)
    namespace_scope = _inventory_count_namespaces(namespaces)
    namespace_reader = getattr(db, "inventory_namespace_resource_counts", None)
    if not callable(namespace_reader):
        raise HTTPException(status_code=503, detail=INVENTORY_NAMESPACE_SUMMARY_UNAVAILABLE)
    snapshot = db.latest_inventory_snapshot(workspace_id, cluster_id)
    evidence = project_inventory_resource_counts_evidence(
        snapshot,
        namespace_scope=namespace_scope,
    )
    counts = db.inventory_product_resource_counts(
        workspace_id,
        cluster_id,
        namespaces=namespace_scope,
    )
    if evidence.completeness == "observed":
        counts = include_discoverable_zero_counts(counts, snapshot=snapshot)
    namespace_counts = namespace_reader(
        workspace_id,
        cluster_id,
        namespaces=namespace_scope,
    )
    return InventorySummaryResponse(
        cluster_id=cluster_id,
        latest_snapshot=snapshot,
        counts=counts,
        namespaces=namespace_counts,
        counts_evidence=evidence,
    )


def _inventory_count_namespaces(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    values = tuple(item.strip() for item in value.split(","))
    if any(not item for item in values):
        raise HTTPException(status_code=422, detail="inventory count namespace scope is invalid")
    try:
        return canonical_kubernetes_namespaces(values)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="inventory count namespace scope is invalid",
        ) from exc


@router.get(
    gateway_routes.CLUSTER_API_RESOURCES_PATH,
    response_model=KubernetesApiResourcesResponse,
)
async def get_cluster_api_resources(
    cluster_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> KubernetesApiResourcesResponse:
    """Return only the bounded API catalog observed by the authorized cluster agent."""
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_inventory_access(db, current, workspace_id, cluster_id)
    snapshot = db.latest_inventory_snapshot(workspace_id, cluster_id)
    snapshot_id = str(snapshot.get("snapshot_id")) if isinstance(snapshot, dict) else None
    snapshot_summary = snapshot.get("summary") if isinstance(snapshot, dict) else None
    source_summary = snapshot_summary.get("summary") if isinstance(snapshot_summary, dict) else None
    raw_discovery = (
        source_summary.get("api_resource_discovery") if isinstance(source_summary, dict) else None
    )
    if not isinstance(raw_discovery, dict):
        return KubernetesApiResourcesResponse(
            cluster_id=cluster_id,
            snapshot_id=snapshot_id,
            unavailable_reason="api_resource_discovery_not_observed",
        )
    try:
        discovery = ApiResourceDiscoveryObservation.model_validate(raw_discovery)
    except ValueError:
        return KubernetesApiResourcesResponse(
            cluster_id=cluster_id,
            snapshot_id=snapshot_id,
            unavailable_reason="api_resource_discovery_invalid",
        )
    return KubernetesApiResourcesResponse(
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        discovery=discovery,
    )
