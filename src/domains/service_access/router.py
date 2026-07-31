"""Service access routes backed by the existing durable command session."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from domains.command.events import CommandRequestedBody
from domains.command.repository import AgentCommandCapacityExceeded
from domains.command.router import (
    COMMAND_PRIORITY_HIGH,
    accept_command_with_receipt_stage,
    announce_staged_operation_event,
    command_accepted_response,
    new_command_id,
    publish_accepted_operation,
)
from domains.gitops.events import Diff
from domains.identity.dependencies import require_cluster_access, require_session
from packages.config.constants import RiskLevel
from packages.contracts.auth import Actor
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, Permission
from packages.contracts.parity import ClusterScope, CommandReceipt, ResourceRef
from packages.contracts.service_access import (
    AGENT_PORT_FORWARD_DESKTOP_REASON,
    AGENT_PORT_FORWARD_UNAVAILABLE_REASON,
    POD_SERVICE_REQUEST_UNSUPPORTED_REASON,
    PORT_DISCOVERY_PARTIAL_REASON,
    PORT_FORWARD_AGENT_CAPABILITY,
    PORT_FORWARD_NO_TCP_PORTS_REASON,
    PORT_FORWARD_RESOURCE_UNAVAILABLE_REASON,
    SERVICE_HTTP_REQUEST_ACTION,
    SERVICE_HTTP_REQUEST_AGENT_CAPABILITY,
    SERVICE_REQUEST_AGENT_UNAVAILABLE_REASON,
    SERVICE_REQUEST_MAX_ACTIVE_PER_CLUSTER,
    SERVICE_REQUEST_NO_TCP_PORTS_REASON,
    SERVICE_REQUEST_RESOURCE_UNAVAILABLE_REASON,
    ServiceAccessCapabilities,
    ServiceHttpRequestCommandPayload,
    ServicePort,
    ServiceRequestCreateRequest,
)
from packages.runtime.dependencies import get_db, get_events, get_operation_events

router = APIRouter()

SERVICE_RESOURCE_TYPE = "service"
POD_RESOURCE_TYPE = "pod"
SERVICE_RESOURCE_NOT_FOUND = "service resource not found"
SERVICE_RESOURCE_IDENTITY_CHANGED = "service resource identity changed"
SERVICE_PORT_UNAVAILABLE = "service port is not available"
SERVICE_REQUEST_LIMIT_REACHED = "too many active service requests"
SERVICE_REQUEST_AGENT_UNAVAILABLE = "service request agent is unavailable"
SERVICE_ACCESS_DENIED = "service access denied"


@router.get(
    gateway_routes.SERVICE_ACCESS_CAPABILITIES_PATH,
    response_model=ServiceAccessCapabilities,
)
async def get_service_access_capabilities(
    resource: str = Query(min_length=1, max_length=255),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ServiceAccessCapabilities:
    workspace_id = str(getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID))
    row = _inventory_resource_by_key(db, workspace_id, resource)
    if row is None:
        raise HTTPException(status_code=404, detail=SERVICE_RESOURCE_NOT_FOUND)
    cluster_id = _required_text(row.get("cluster_id"))
    _require_service_access(db, current, workspace_id, cluster_id)
    ports, port_discovery, port_discovery_reason = _resource_ports(row)
    scope, resource_ref = _scope_and_resource(
        workspace_id,
        row,
        freshness="partial" if port_discovery == "partial" else "live",
    )
    resource_type = str(row.get("resource_type") or "").casefold()
    if resource_type == SERVICE_RESOURCE_TYPE:
        availability, reason = _availability(db, workspace_id, cluster_id, row, ports)
    else:
        availability, reason = "unavailable", POD_SERVICE_REQUEST_UNSUPPORTED_REASON
    local_port_forward, local_port_forward_reason = _local_forward_availability(
        db,
        workspace_id,
        cluster_id,
        row,
        ports,
    )
    return ServiceAccessCapabilities(
        scope=scope,
        resource=resource_ref,
        revision=_capability_revision(
            current=current,
            scope=scope,
            resource=resource_ref,
            inventory=row,
            ports=ports,
            availability=availability,
            reason=reason,
            local_port_forward=local_port_forward,
            local_port_forward_reason=local_port_forward_reason,
            port_discovery=port_discovery,
            port_discovery_reason=port_discovery_reason,
        ),
        service_request=availability,
        service_request_reason=reason,
        local_port_forward=local_port_forward,
        local_port_forward_reason=local_port_forward_reason,
        port_discovery=port_discovery,
        port_discovery_reason=port_discovery_reason,
        ports=ports,
    )


@router.post(
    gateway_routes.SERVICE_REQUESTS_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def create_service_request(
    payload: ServiceRequestCreateRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    workspace_id = str(getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID))
    if payload.scope.workspace_id != workspace_id:
        raise HTTPException(status_code=403, detail=SERVICE_ACCESS_DENIED)
    _require_service_access(db, current, workspace_id, payload.scope.cluster_id)
    row = _resolve_exact_service(db, workspace_id, payload)
    ports = _service_ports(row)
    availability, _reason = _availability(
        db,
        workspace_id,
        payload.scope.cluster_id,
        row,
        ports,
    )
    if availability != "available":
        raise HTTPException(status_code=409, detail=SERVICE_REQUEST_AGENT_UNAVAILABLE)
    if payload.port not in {item.port for item in ports}:
        raise HTTPException(status_code=422, detail=SERVICE_PORT_UNAVAILABLE)
    active = await _active_service_requests(
        db,
        workspace_id,
        payload.scope.cluster_id,
    )
    if active >= SERVICE_REQUEST_MAX_ACTIVE_PER_CLUSTER:
        raise HTTPException(
            status_code=429,
            detail=SERVICE_REQUEST_LIMIT_REACHED,
            headers={"Retry-After": "5"},
        )

    command_payload = ServiceHttpRequestCommandPayload(
        resource=payload.resource,
        port=payload.port,
        scheme=payload.scheme,
        path=payload.path,
    )
    command = CommandRequestedBody(
        cluster_id=payload.scope.cluster_id,
        action=SERVICE_HTTP_REQUEST_ACTION,
        namespace=payload.resource.namespace or "",
        reason=payload.reason,
        diff=Diff(
            workspace_id=workspace_id,
            cluster_id=payload.scope.cluster_id,
            resource=f"service/{payload.resource.name}",
            namespace=payload.resource.namespace or "",
            desired_image="",
            actual_image="service-observed",
            risk=RiskLevel.REVIEW_REQUIRED,
            status=SERVICE_HTTP_REQUEST_ACTION,
            basis={
                "resource_uid": payload.resource.uid,
                "port": payload.port,
                "scheme": payload.scheme,
            },
        ),
        command_id=new_command_id(),
        payload=command_payload.model_dump(),
        workspace_id=workspace_id,
        priority=COMMAND_PRIORITY_HIGH,
        requested_by=str(getattr(current, "user_id", "")),
        direct_execution=False,
        direct_execution_confirmed=False,
    )
    try:
        accepted, receipt_event = await accept_command_with_receipt_stage(
            events,
            command,
            actor=Actor(
                str(getattr(current, "user_id", "")),
                tuple(getattr(current, "roles", ()) or ()),
            ),
            max_active_per_action=SERVICE_REQUEST_MAX_ACTIVE_PER_CLUSTER,
        )
    except AgentCommandCapacityExceeded as error:
        raise HTTPException(
            status_code=429,
            detail=SERVICE_REQUEST_LIMIT_REACHED,
            headers={"Retry-After": "5"},
        ) from error
    response = command_accepted_response(command, accepted)
    if not await announce_staged_operation_event(
        operation_events,
        receipt_event,
        workspace_id=workspace_id,
    ):
        await publish_accepted_operation(operation_events, command, response)
    return response


def _inventory_resource_by_key(
    db: Any,
    workspace_id: str,
    inventory_key: str,
) -> Mapping[str, Any] | None:
    reader = getattr(db, "get_inventory_resource_by_key", None)
    if not callable(reader):
        return None
    row = reader(workspace_id=workspace_id, inventory_key=inventory_key.strip())
    return row if isinstance(row, Mapping) else None


def _resolve_exact_service(
    db: Any,
    workspace_id: str,
    payload: ServiceRequestCreateRequest,
) -> Mapping[str, Any]:
    reader = getattr(db, "get_inventory_resource_by_api_version", None)
    if not callable(reader):
        raise HTTPException(status_code=503, detail=SERVICE_RESOURCE_NOT_FOUND)
    row = reader(
        workspace_id=workspace_id,
        cluster_id=payload.scope.cluster_id,
        resource_type=SERVICE_RESOURCE_TYPE,
        api_version="v1",
        kind="Service",
        namespace=payload.resource.namespace,
        name=payload.resource.name,
    )
    if not isinstance(row, Mapping):
        raise HTTPException(status_code=404, detail=SERVICE_RESOURCE_NOT_FOUND)
    _, resolved = _scope_and_resource(workspace_id, row)
    if resolved != payload.resource:
        raise HTTPException(status_code=409, detail=SERVICE_RESOURCE_IDENTITY_CHANGED)
    return row


def _scope_and_resource(
    workspace_id: str,
    row: Mapping[str, Any],
    *,
    freshness: str = "live",
) -> tuple[ClusterScope, ResourceRef]:
    resource_type = str(row.get("resource_type") or "").casefold()
    if resource_type not in {SERVICE_RESOURCE_TYPE, POD_RESOURCE_TYPE}:
        raise HTTPException(status_code=422, detail=SERVICE_RESOURCE_NOT_FOUND)
    api_version = _required_text(row.get("api_version"))
    kind = _required_text(row.get("kind"))
    if api_version != "v1" or kind.casefold() != resource_type:
        raise HTTPException(status_code=422, detail=SERVICE_RESOURCE_NOT_FOUND)
    cluster_id = _required_text(row.get("cluster_id"))
    namespace = _required_text(row.get("namespace"))
    name = _required_text(row.get("name"))
    uid = _required_text(row.get("uid"))
    try:
        resource = ResourceRef(
            api_group="",
            version="v1",
            kind="Pod" if resource_type == POD_RESOURCE_TYPE else "Service",
            namespace=namespace,
            name=name,
            uid=uid,
        )
        scope = ClusterScope(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            namespaces=(namespace,),
            freshness=freshness,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=SERVICE_RESOURCE_NOT_FOUND) from error
    return scope, resource


def _service_ports(row: Mapping[str, Any]) -> tuple[ServicePort, ...]:
    summary = row.get("summary")
    if not isinstance(summary, Mapping):
        return ()
    raw_ports = summary.get("ports")
    if not isinstance(raw_ports, list):
        return ()
    ports: dict[int, ServicePort] = {}
    for item in raw_ports:
        if not isinstance(item, Mapping):
            continue
        raw_port = item.get("port")
        if isinstance(raw_port, bool) or not isinstance(raw_port, int):
            continue
        protocol = str(item.get("protocol") or "TCP").upper()
        if protocol != "TCP" or not 1 <= raw_port <= 65_535:
            continue
        name = _optional_text(item.get("name"))
        app_protocol = _optional_text(item.get("appProtocol") or item.get("app_protocol"))
        try:
            ports[raw_port] = ServicePort(
                container_name=None,
                port=raw_port,
                name=name,
                protocol="TCP",
                app_protocol=app_protocol,
                default_scheme=_default_scheme(raw_port, name, app_protocol),
            )
        except ValueError:
            continue
    return tuple(ports[port] for port in sorted(ports))


def _resource_ports(
    row: Mapping[str, Any],
) -> tuple[tuple[ServicePort, ...], str, str | None]:
    resource_type = str(row.get("resource_type") or "").casefold()
    if resource_type == SERVICE_RESOURCE_TYPE:
        ports = _service_ports(row)
        return (
            (ports, "complete", None)
            if ports
            else ((), "unavailable", PORT_FORWARD_NO_TCP_PORTS_REASON)
        )
    if resource_type != POD_RESOURCE_TYPE:
        raise HTTPException(status_code=422, detail=SERVICE_RESOURCE_NOT_FOUND)
    return _pod_ports(row)


def _pod_ports(
    row: Mapping[str, Any],
) -> tuple[tuple[ServicePort, ...], str, str | None]:
    summary = row.get("summary")
    if not isinstance(summary, Mapping):
        return (), "partial", PORT_DISCOVERY_PARTIAL_REASON
    raw_containers = summary.get("containers")
    complete = summary.get("container_ports_complete") is True
    if not isinstance(raw_containers, list):
        return (), "partial", PORT_DISCOVERY_PARTIAL_REASON
    ports: dict[tuple[str, int, str], ServicePort] = {}
    for container in raw_containers:
        if not isinstance(container, Mapping):
            complete = False
            continue
        container_name = _optional_text(container.get("name"))
        raw_ports = container.get("ports")
        if container_name is None or not isinstance(raw_ports, list):
            complete = False
            continue
        for item in raw_ports:
            if not isinstance(item, Mapping):
                complete = False
                continue
            raw_port = item.get("container_port")
            protocol = str(item.get("protocol") or "TCP").upper()
            if protocol != "TCP":
                continue
            if isinstance(raw_port, bool) or not isinstance(raw_port, int):
                complete = False
                continue
            name = _optional_text(item.get("name"))
            try:
                descriptor = ServicePort(
                    container_name=container_name,
                    port=raw_port,
                    name=name,
                    protocol="TCP",
                    app_protocol=None,
                    default_scheme=_default_scheme(raw_port, name, None),
                )
            except ValueError:
                complete = False
                continue
            identity = (container_name, raw_port, name or "")
            if identity in ports:
                complete = False
                continue
            ports[identity] = descriptor
    ordered = tuple(ports[identity] for identity in sorted(ports))
    if not ordered:
        return (), "unavailable", PORT_FORWARD_NO_TCP_PORTS_REASON
    if not complete:
        return ordered, "partial", PORT_DISCOVERY_PARTIAL_REASON
    return ordered, "complete", None


def _default_scheme(
    port: int,
    name: str | None,
    app_protocol: str | None,
) -> str:
    if (app_protocol or "").casefold() == "https":
        return "https"
    if port in {443, 8443} or "https" in (name or "").casefold():
        return "https"
    return "http"


def _availability(
    db: Any,
    workspace_id: str,
    cluster_id: str,
    row: Mapping[str, Any],
    ports: tuple[ServicePort, ...],
) -> tuple[str, str | None]:
    summary = row.get("summary")
    service_type = str(summary.get("type") or "").casefold() if isinstance(summary, Mapping) else ""
    if row.get("deleted_at") is not None or service_type == "externalname":
        return "unavailable", SERVICE_REQUEST_RESOURCE_UNAVAILABLE_REASON
    if not ports:
        return "unavailable", SERVICE_REQUEST_NO_TCP_PORTS_REASON
    if not _agent_supports(db, workspace_id, cluster_id):
        return "unavailable", SERVICE_REQUEST_AGENT_UNAVAILABLE_REASON
    return "available", None


def _local_forward_availability(
    db: Any,
    workspace_id: str,
    cluster_id: str,
    row: Mapping[str, Any],
    ports: tuple[ServicePort, ...],
) -> tuple[str, str]:
    if not ports:
        return "unavailable", PORT_FORWARD_NO_TCP_PORTS_REASON
    resource_type = str(row.get("resource_type") or "").casefold()
    if resource_type == POD_RESOURCE_TYPE:
        summary = row.get("summary")
        phase = str(summary.get("phase") or "") if isinstance(summary, Mapping) else ""
        if row.get("deleted_at") is not None or phase.casefold() != "running":
            return "unavailable", PORT_FORWARD_RESOURCE_UNAVAILABLE_REASON
    if not _agent_supports_capability(
        db,
        workspace_id,
        cluster_id,
        PORT_FORWARD_AGENT_CAPABILITY,
    ):
        return "unavailable", AGENT_PORT_FORWARD_UNAVAILABLE_REASON
    return "desktop_required", AGENT_PORT_FORWARD_DESKTOP_REASON


def _agent_supports(db: Any, workspace_id: str, cluster_id: str) -> bool:
    return _agent_supports_capability(
        db,
        workspace_id,
        cluster_id,
        SERVICE_HTTP_REQUEST_AGENT_CAPABILITY,
    )


def _agent_supports_capability(
    db: Any,
    workspace_id: str,
    cluster_id: str,
    capability: str,
) -> bool:
    reader = getattr(db, "list_cluster_agent_statuses", None)
    if not callable(reader):
        return False
    statuses = reader(workspace_id, cluster_id)
    return any(
        isinstance(item, Mapping)
        and str(item.get("status") or "").casefold() == "connected"
        and capability in tuple(item.get("capabilities") or ())
        for item in statuses
    )


def _require_service_access(
    db: Any,
    current: Any,
    workspace_id: str,
    cluster_id: str,
) -> None:
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.INVENTORY_READ.value,
        detail=SERVICE_ACCESS_DENIED,
    )
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.POD_EXEC.value,
        detail=SERVICE_ACCESS_DENIED,
    )


async def _active_service_requests(
    db: Any,
    workspace_id: str,
    cluster_id: str,
) -> int:
    counter = getattr(db, "count_active_agent_commands", None)
    if not callable(counter):
        raise HTTPException(status_code=503, detail="service request capacity unavailable")
    value = counter(workspace_id, cluster_id, SERVICE_HTTP_REQUEST_ACTION)
    if inspect.isawaitable(value):
        value = await value
    return max(0, int(value))


def _capability_revision(
    *,
    current: Any,
    scope: ClusterScope,
    resource: ResourceRef,
    inventory: Mapping[str, Any],
    ports: tuple[ServicePort, ...],
    availability: str,
    reason: str | None,
    local_port_forward: str = "unavailable",
    local_port_forward_reason: str = AGENT_PORT_FORWARD_UNAVAILABLE_REASON,
    port_discovery: str = "complete",
    port_discovery_reason: str | None = None,
) -> str:
    value = {
        "actor_id": str(getattr(current, "user_id", "")),
        "roles": sorted(str(role) for role in (getattr(current, "roles", ()) or ())),
        "scope": scope.model_dump(),
        "resource": resource.model_dump(),
        "snapshot_id": str(inventory.get("snapshot_id") or ""),
        "resource_version": str(inventory.get("resource_version") or ""),
        "availability": availability,
        "reason": reason,
        "local_port_forward": local_port_forward,
        "local_port_forward_reason": local_port_forward_reason,
        "port_discovery": port_discovery,
        "port_discovery_reason": port_discovery_reason,
        "ports": [port.model_dump() for port in ports],
    }
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _required_text(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise HTTPException(status_code=409, detail=SERVICE_RESOURCE_NOT_FOUND)
    return normalized


def _optional_text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None
