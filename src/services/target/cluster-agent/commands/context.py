from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel

from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gateway.fields import Gateway

COMMAND_COMPLETED_STATUS = "completed"
COMMAND_FAILED_STATUS = "failed"

PayloadT = TypeVar("PayloadT")
PayloadModel = type[BaseModel]
KubernetesVerb = Literal["get", "create", "patch", "apply", "delete"]
KubernetesScope = Literal[
    "target-agent",
    "system",
    "user-workload",
    "cluster-workload",
    "service-access",
    "resource-maintenance",
]


class KubernetesClient(Protocol):
    async def get_namespaced_resource(
        self,
        *,
        api_group: str,
        version: str,
        namespace: str,
        resource: str,
        name: str,
        subresource: str | None = None,
    ) -> JsonObject: ...

    async def get_cluster_resource(
        self,
        *,
        api_group: str,
        version: str,
        resource: str,
        name: str,
    ) -> JsonObject: ...

    async def patch_namespaced_resource(
        self,
        *,
        api_group: str,
        version: str,
        namespace: str,
        resource: str,
        name: str,
        body: JsonObject,
        subresource: str | None = None,
    ) -> JsonObject: ...

    async def patch_cluster_resource(
        self,
        *,
        api_group: str,
        version: str,
        resource: str,
        name: str,
        body: JsonObject,
    ) -> JsonObject: ...

    async def create_namespaced_resource(
        self,
        *,
        api_group: str,
        version: str,
        namespace: str,
        resource: str,
        body: JsonObject,
    ) -> JsonObject: ...

    async def create_namespaced_subresource(
        self,
        *,
        api_group: str,
        version: str,
        namespace: str,
        resource: str,
        name: str,
        subresource: str,
        body: JsonObject,
    ) -> JsonObject: ...

    async def list_cluster_resources(
        self,
        *,
        api_group: str,
        version: str,
        resource: str,
        query: Mapping[str, str] | None = None,
    ) -> JsonObject: ...

    async def delete_namespaced_resource(
        self,
        *,
        api_group: str,
        version: str,
        namespace: str,
        resource: str,
        name: str,
        preconditions: JsonObject | None = None,
        propagation_policy: str | None = None,
    ) -> JsonObject: ...

    async def delete_cluster_resource(
        self,
        *,
        api_group: str,
        version: str,
        resource: str,
        name: str,
        preconditions: JsonObject | None = None,
        propagation_policy: str | None = None,
    ) -> JsonObject: ...


@dataclass(frozen=True)
class KubernetesCommandSpec:
    api_group: str
    version: str
    resource: str
    verb: KubernetesVerb
    scope: KubernetesScope = "target-agent"


@dataclass(frozen=True)
class CommandSpec:
    action: str
    payload_model: PayloadModel | None = None
    kubernetes: KubernetesCommandSpec | None = None


class CommandResult:
    @staticmethod
    def completed(
        cluster_id: str,
        message: str,
        *,
        applied: bool = False,
        retryable: bool = False,
        resources: list[JsonObject] | None = None,
        stdout: str = "",
        stderr: str = "",
        **fields: Any,
    ) -> JsonObject:
        return {
            Gateway.STATUS: COMMAND_COMPLETED_STATUS,
            Gateway.CLUSTER_ID: cluster_id,
            Gateway.APPLIED: applied,
            Gateway.MESSAGE: message,
            Gateway.RETRYABLE: retryable,
            Gateway.RESOURCES: resources or [],
            Gateway.STDOUT: stdout,
            Gateway.STDERR: stderr,
            **fields,
        }

    @staticmethod
    def failed(
        cluster_id: str,
        message: str,
        *,
        applied: bool = False,
        retryable: bool = False,
        resources: list[JsonObject] | None = None,
        stdout: str = "",
        stderr: str = "",
        **fields: Any,
    ) -> JsonObject:
        return {
            Gateway.STATUS: COMMAND_FAILED_STATUS,
            Gateway.CLUSTER_ID: cluster_id,
            Gateway.APPLIED: applied,
            Gateway.MESSAGE: message,
            Gateway.RETRYABLE: retryable,
            Gateway.RESOURCES: resources or [],
            Gateway.STDOUT: stdout,
            Gateway.STDERR: stderr,
            **fields,
        }


@dataclass(frozen=True)
class CommandContext[PayloadT]:
    action: str
    cluster_id: str
    cluster_role: str
    payload: PayloadT
    raw_payload: JsonObject
    kubernetes: KubernetesClient
    spec: CommandSpec
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def kubernetes_spec(self) -> KubernetesCommandSpec:
        if self.spec.kubernetes is None:
            raise RuntimeError(f"{self.action} is not a Kubernetes command")
        return self.spec.kubernetes

    def ok(self, message: str, *, applied: bool = False, **fields: Any) -> JsonObject:
        return CommandResult.completed(
            self.cluster_id,
            message,
            applied=applied,
            **fields,
        )

    def fail(self, message: str, *, applied: bool = False, **fields: Any) -> JsonObject:
        return CommandResult.failed(
            self.cluster_id,
            message,
            applied=applied,
            **fields,
        )
