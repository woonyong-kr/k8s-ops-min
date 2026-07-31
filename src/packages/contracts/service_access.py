"""Typed contracts for audited in-cluster service requests and local forwarding.

The management server never constructs an arbitrary outbound URL and never
binds a workstation port.  A service request is resolved from an exact
``ResourceRef`` and executed by the target Cluster Agent.  A desktop may own
only the loopback listener; target identity, RBAC and transport remain agent
authority.
"""

from __future__ import annotations

import re
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from packages.contracts.modeling import StrictModel
from packages.contracts.parity import ClusterScope, ResourceRef

SERVICE_HTTP_REQUEST_AGENT_CAPABILITY = "service_http_request_v1"
SERVICE_HTTP_REQUEST_ACTION = "service.http.request"
SERVICE_REQUEST_TIMEOUT_SECONDS = 10
SERVICE_REQUEST_MAX_BODY_BYTES = 512 * 1024
SERVICE_REQUEST_MAX_HEADERS = 100
SERVICE_REQUEST_MAX_HEADER_VALUE_BYTES = 4 * 1024
SERVICE_REQUEST_MAX_PATH_LENGTH = 2_048
SERVICE_REQUEST_MAX_ACTIVE_PER_CLUSTER = 16
PORT_FORWARD_AGENT_CAPABILITY = "port_forward_stream_v1"
AGENT_PORT_FORWARD_DESKTOP_REASON = "desktop_agent_port_forward_required"
AGENT_PORT_FORWARD_UNAVAILABLE_REASON = "agent_port_forward_unavailable"
SERVICE_REQUEST_AGENT_UNAVAILABLE_REASON = "service_request_agent_unavailable"
SERVICE_REQUEST_RESOURCE_UNAVAILABLE_REASON = "service_request_resource_unavailable"
SERVICE_REQUEST_NO_TCP_PORTS_REASON = "service_request_no_tcp_ports"
POD_SERVICE_REQUEST_UNSUPPORTED_REASON = "pod_service_request_unsupported"
PORT_DISCOVERY_PARTIAL_REASON = "port_discovery_partial"
PORT_FORWARD_NO_TCP_PORTS_REASON = "port_forward_no_tcp_ports"
PORT_FORWARD_RESOURCE_UNAVAILABLE_REASON = "port_forward_resource_unavailable"
SERVICE_REQUEST_SAFE_RESPONSE_HEADERS = frozenset(
    {
        "cache-control",
        "content-encoding",
        "content-language",
        "content-length",
        "content-type",
        "date",
        "etag",
        "expires",
        "last-modified",
        "server",
        "vary",
        "x-request-id",
    }
)

ServiceRequestScheme = Literal["http", "https"]
ServiceAccessAvailability = Literal["available", "forbidden", "unavailable"]
LocalPortForwardAvailability = Literal["desktop_required", "unavailable"]
PortDiscoveryAvailability = Literal["complete", "partial", "unavailable"]
ListenAddress = Literal["127.0.0.1"]
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")


class ServicePort(StrictModel):
    container_name: str | None = Field(default=None, min_length=1, max_length=253)
    port: int = Field(ge=1, le=65_535)
    name: str | None = Field(default=None, max_length=63)
    protocol: Literal["TCP"] = "TCP"
    app_protocol: str | None = Field(default=None, max_length=120)
    default_scheme: ServiceRequestScheme


class ServiceAccessResolveRequest(StrictModel):
    scope: ClusterScope
    resource: ResourceRef

    @model_validator(mode="after")
    def require_service_identity(self) -> Self:
        _validate_service_identity(self.scope, self.resource)
        return self


class ServiceAccessCapabilities(StrictModel):
    scope: ClusterScope
    resource: ResourceRef
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    service_request: ServiceAccessAvailability
    service_request_reason: str | None = Field(default=None, max_length=240)
    local_port_forward: LocalPortForwardAvailability = "unavailable"
    local_port_forward_reason: str = Field(
        default=AGENT_PORT_FORWARD_UNAVAILABLE_REASON,
        min_length=1,
        max_length=240,
    )
    port_discovery: PortDiscoveryAvailability = "complete"
    port_discovery_reason: str | None = Field(default=None, min_length=1, max_length=240)
    ports: tuple[ServicePort, ...] = ()

    @model_validator(mode="after")
    def availability_has_consistent_reason(self) -> Self:
        if self.service_request == "available" and self.service_request_reason is not None:
            raise ValueError("available service request capability cannot have a reason")
        if self.service_request != "available" and not self.service_request_reason:
            raise ValueError("unavailable service request capability requires a reason")
        if self.local_port_forward == "desktop_required":
            if self.local_port_forward_reason != AGENT_PORT_FORWARD_DESKTOP_REASON:
                raise ValueError("desktop port forward requires the agent transport reason")
            if not self.ports:
                raise ValueError("desktop port forward requires an observed TCP port")
        elif self.local_port_forward_reason == AGENT_PORT_FORWARD_DESKTOP_REASON:
            raise ValueError("unavailable port forward requires an unavailable reason")
        if (self.port_discovery == "complete") != (self.port_discovery_reason is None):
            raise ValueError("port discovery reason is inconsistent")
        values = [(item.container_name or "", item.port, item.name or "") for item in self.ports]
        if values != sorted(values) or len(values) != len(set(values)):
            raise ValueError("service access ports must be unique and sorted")
        if self.resource.kind.casefold() == "service" and any(
            item.container_name is not None for item in self.ports
        ):
            raise ValueError("Service ports cannot carry a container identity")
        if self.resource.kind.casefold() == "pod" and any(
            item.container_name is None for item in self.ports
        ):
            raise ValueError("Pod ports require a container identity")
        if self.resource.kind.casefold() not in {"pod", "service"}:
            raise ValueError("service access requires a Pod or Service resource")
        if self.resource.api_group not in {"", "core"} or self.resource.version != "v1":
            raise ValueError("service access requires a core/v1 resource")
        if self.resource.namespace is None:
            raise ValueError("service access requires a namespaced resource")
        if self.scope.namespaces and self.resource.namespace not in self.scope.namespaces:
            raise ValueError("service access target is outside the selected namespace scope")
        return self


class ServiceRequestCreateRequest(StrictModel):
    scope: ClusterScope
    resource: ResourceRef
    port: int = Field(ge=1, le=65_535)
    scheme: ServiceRequestScheme = "http"
    path: str = Field(default="/", min_length=1, max_length=SERVICE_REQUEST_MAX_PATH_LENGTH)
    confirmation: Literal[True]
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("path")
    @classmethod
    def validate_relative_request_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith("/") or normalized.startswith("//"):
            raise ValueError("service request path must be an absolute-path reference")
        if any(character in normalized for character in ("\r", "\n", "\0")):
            raise ValueError("service request path contains a forbidden character")
        parsed = urlsplit(normalized)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise ValueError("service request path cannot override the service target")
        return normalized

    @model_validator(mode="after")
    def require_service_identity(self) -> Self:
        _validate_service_identity(self.scope, self.resource)
        return self


class ServiceHttpRequestCommandPayload(StrictModel):
    """The only payload sent to a target agent for a bounded service GET."""

    resource: ResourceRef
    port: int = Field(ge=1, le=65_535)
    scheme: ServiceRequestScheme = "http"
    path: str = Field(default="/", min_length=1, max_length=SERVICE_REQUEST_MAX_PATH_LENGTH)

    @field_validator("path")
    @classmethod
    def validate_relative_request_path(cls, value: str) -> str:
        return ServiceRequestCreateRequest.validate_relative_request_path(value)

    @model_validator(mode="after")
    def require_service_identity(self) -> Self:
        _validate_service_ref(self.resource)
        return self


class LocalPortForwardRequest(StrictModel):
    """Validated native request; Python and browser runtimes must not execute it."""

    scope: ClusterScope
    resource: ResourceRef
    capability_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    remote_port: int = Field(ge=1, le=65_535)
    local_port: int | None = Field(default=None, ge=1, le=65_535)
    listen_address: ListenAddress = "127.0.0.1"
    confirmation: Literal[True]

    @model_validator(mode="after")
    def require_namespaced_tcp_target(self) -> Self:
        if self.resource.kind.casefold() not in {"pod", "service"}:
            raise ValueError("local port forward requires a Pod or Service resource")
        if self.resource.api_group not in {"", "core"} or self.resource.version != "v1":
            raise ValueError("local port forward requires a core/v1 resource")
        if self.resource.namespace is None:
            raise ValueError("local port forward requires a namespaced resource")
        if self.scope.cluster_id.strip() == "":
            raise ValueError("local port forward requires a cluster")
        if self.scope.namespaces and self.resource.namespace not in self.scope.namespaces:
            raise ValueError("local port forward target is outside the selected namespace scope")
        return self


class ServiceRequestResult(StrictModel):
    status: int = Field(ge=100, le=599)
    status_text: str = Field(default="", max_length=120)
    duration_ms: int = Field(ge=0)
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    truncated: bool = False
    body_bytes: int = Field(ge=0, le=SERVICE_REQUEST_MAX_BODY_BYTES)
    error: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def bound_headers(self) -> Self:
        if len(self.headers) > SERVICE_REQUEST_MAX_HEADERS:
            raise ValueError("service response contains too many headers")
        if any(
            not name
            or len(name) > 256
            or name.casefold() not in SERVICE_REQUEST_SAFE_RESPONSE_HEADERS
            or any(character in name for character in ("\r", "\n", "\0"))
            for name in self.headers
        ):
            raise ValueError("service response contains an unsafe header")
        if any(
            len(value.encode("utf-8")) > SERVICE_REQUEST_MAX_HEADER_VALUE_BYTES
            or any(character in value for character in ("\r", "\n", "\0"))
            for value in self.headers.values()
        ):
            raise ValueError("service response header value exceeds the byte limit")
        encoded_length = len(self.body.encode("utf-8"))
        if encoded_length > SERVICE_REQUEST_MAX_BODY_BYTES:
            raise ValueError("service response body exceeds the byte limit")
        if self.body_bytes != encoded_length:
            raise ValueError("service response body byte count does not match the body")
        return self


def _validate_service_identity(scope: ClusterScope, resource: ResourceRef) -> None:
    _validate_service_ref(resource)
    if scope.cluster_id.strip() == "":
        raise ValueError("service access requires a cluster")
    if scope.namespaces and resource.namespace not in scope.namespaces:
        raise ValueError("service access target is outside the selected namespace scope")


def _validate_service_ref(resource: ResourceRef) -> None:
    if resource.kind.casefold() != "service":
        raise ValueError("service access requires a Service resource")
    if resource.api_group not in {"", "core"} or resource.version != "v1":
        raise ValueError("service access requires the core/v1 Service identity")
    if resource.namespace is None:
        raise ValueError("service access requires a namespaced Service")
    if not _valid_dns_label(resource.namespace) or not _valid_dns_label(resource.name):
        raise ValueError("service access requires DNS-label namespace and name")


def _valid_dns_label(value: str) -> bool:
    return len(value) <= 63 and _DNS_LABEL.fullmatch(value) is not None
