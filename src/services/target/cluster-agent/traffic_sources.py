"""Bounded target-cluster traffic source detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from packages.contracts.event_bus.interfaces import JsonObject


@dataclass(frozen=True)
class TrafficDetectorSpec:
    key: str
    label: str
    selector: str
    unavailable_message: str
    available_message: str
    service_name: str | None = None


TRAFFIC_DETECTORS = (
    TrafficDetectorSpec(
        key="caretta",
        label="Caretta",
        selector="app.kubernetes.io/name=caretta",
        unavailable_message="collector workload was not observed",
        available_message="collector workload was observed",
    ),
    TrafficDetectorSpec(
        key="hubble",
        label="Hubble",
        selector="k8s-app=hubble-relay",
        unavailable_message="relay workload was not observed",
        available_message="relay service and running endpoint were observed",
        service_name="hubble-relay",
    ),
    TrafficDetectorSpec(
        key="istio",
        label="Istio",
        selector="app=istiod",
        unavailable_message="control-plane workload was not observed",
        available_message="control-plane workload was observed",
    ),
)

CNI_DAEMONSETS = (
    ("cilium", "cilium"),
    ("calico", "calico-node"),
    ("vpc-cni", "aws-node"),
    ("azure-cni", "azure-cni"),
    ("flannel", "kube-flannel-ds"),
)


class TrafficSourceObservationError(RuntimeError):
    """A source probe failed and must not be projected as an absent workload."""


class TrafficSourceDetector:
    def __init__(self, kubernetes: Any) -> None:
        self.kubernetes = kubernetes

    async def observe(self, *, active_source: str | None) -> JsonObject:
        cluster = await self._cluster_facts()
        sources = [await self._observe_source(spec, cluster["cni"]) for spec in TRAFFIC_DETECTORS]
        available = {
            str(source["key"]) for source in sources if source.get("status") == "available"
        }
        return {
            "schema_version": 1,
            "observed_at": datetime.now(UTC).isoformat(),
            "active_source": active_source if active_source in available else None,
            "cluster": cluster,
            "sources": sources,
        }

    async def _cluster_facts(self) -> JsonObject:
        version = await self._safe_get("/version")
        nodes = await self._safe_get("/api/v1/nodes")
        labels = [
            self._mapping(self._mapping(item).get("metadata")).get("labels")
            for item in self._items(nodes)
        ]
        platform = self._platform(
            [self._mapping(value) for value in labels if isinstance(value, dict)]
        )
        cni = await self._cni()
        return {
            "platform": platform,
            "cni": cni,
            "dataplane_v2": platform == "gke" and cni == "cilium",
            "kubernetes_version": (
                str(version.get("gitVersion"))
                if isinstance(version, dict) and version.get("gitVersion")
                else None
            ),
        }

    async def _cni(self) -> str:
        for cni, daemonset in CNI_DAEMONSETS:
            observed = await self._safe_get(
                f"/apis/apps/v1/namespaces/kube-system/daemonsets/{daemonset}"
            )
            if observed is not None:
                return cni
        return "unknown"

    async def _observe_source(self, spec: TrafficDetectorSpec, cni: object) -> JsonObject:
        query = urlencode({"labelSelector": spec.selector})
        try:
            body = await self._get(f"/api/v1/pods?{query}")
        except TrafficSourceObservationError as error:
            return self._source_status(spec, "error", str(error))
        running = [
            pod
            for pod in self._items(body)
            if self._mapping(pod.get("status")).get("phase") == "Running"
        ]
        if not running:
            return self._source_status(spec, "not_detected", spec.unavailable_message)
        if spec.service_name:
            metadata = self._mapping(running[0].get("metadata"))
            namespace = str(metadata.get("namespace") or "")
            try:
                service = (
                    await self._get(f"/api/v1/namespaces/{namespace}/services/{spec.service_name}")
                    if namespace
                    else None
                )
            except TrafficSourceObservationError as error:
                return self._source_status(spec, "error", str(error))
            if service is None:
                return self._source_status(
                    spec,
                    "error",
                    "running source workload has no observed service endpoint",
                )
        metadata = self._mapping(running[0].get("metadata"))
        labels = self._mapping(metadata.get("labels"))
        version = next(
            (
                str(labels[key])
                for key in (
                    "app.kubernetes.io/version",
                    "version",
                    "k8s-app-version",
                )
                if labels.get(key)
            ),
            None,
        )
        return self._source_status(
            spec,
            "available",
            spec.available_message,
            version=version,
            native=spec.key == "hubble" and cni == "cilium",
        )

    async def _get(self, path: str) -> JsonObject | None:
        try:
            response = await self.kubernetes.request("GET", path, allow_not_found=True)
        except (httpx.HTTPError, OSError) as error:
            raise TrafficSourceObservationError("source observation request failed") from error
        if response.status_code == 404:
            return None
        if response.status_code == 403:
            raise TrafficSourceObservationError("source observation is forbidden by cluster RBAC")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise TrafficSourceObservationError("source observation request failed") from error
        try:
            value = response.json()
        except ValueError as error:
            raise TrafficSourceObservationError("source observation response is invalid") from error
        return value if isinstance(value, dict) else None

    async def _safe_get(self, path: str) -> JsonObject | None:
        try:
            return await self._get(path)
        except TrafficSourceObservationError:
            return None

    @staticmethod
    def _platform(labels: list[dict[str, Any]]) -> str:
        keys = {str(key) for item in labels for key in item}
        if any(key.startswith(("eks.amazonaws.com/", "alpha.eksctl.io/")) for key in keys):
            return "eks"
        if any(key.startswith(("cloud.google.com/", "container.googleapis.com/")) for key in keys):
            return "gke"
        if any(key.startswith(("kubernetes.azure.com/", "agentpool")) for key in keys):
            return "aks"
        return "generic"

    @staticmethod
    def _source_status(
        spec: TrafficDetectorSpec,
        status: str,
        message: str,
        *,
        version: str | None = None,
        native: bool = False,
    ) -> JsonObject:
        return {
            "key": spec.key,
            "label": spec.label,
            "status": status,
            "version": version,
            "native": native,
            "message": message,
        }

    @classmethod
    def _items(cls, value: JsonObject | None) -> list[JsonObject]:
        items = value.get("items") if isinstance(value, dict) else None
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    @staticmethod
    def _mapping(value: object) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}
