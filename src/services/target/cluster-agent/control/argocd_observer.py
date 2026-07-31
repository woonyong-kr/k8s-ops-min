from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from kubernetes_api import (
    kubernetes_api_base_url,
    kubernetes_client,
    kubernetes_headers,
    service_account_token,
)

from packages.contracts.event_bus.interfaces import JsonObject

ARGO_API_PREFIX = "/apis/argoproj.io/v1alpha1"
APPLICATIONS_PATH = f"{ARGO_API_PREFIX}/applications"
ROLLOUTS_PATH = f"{ARGO_API_PREFIX}/rollouts"
DEFAULT_LIST_LIMIT = 200
UNAVAILABLE_STATUS_CODES = frozenset({403, 404})
FAILED_ROLLOUT_PHASES = frozenset({"degraded", "error", "failed"})
READY_OPERATION_PHASES = frozenset({"", "succeeded"})


class ArgoObserver(Protocol):
    async def snapshot(self) -> JsonObject: ...


class KubernetesArgoObserver:
    """Service-account 인증으로 Argo CR을 GET/list만 수행하는 observer."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> None:
        self.configured_base_url = base_url
        self.configured_token = token
        self.transport = transport
        self.limit = max(1, min(limit, 1000))

    async def snapshot(self) -> JsonObject:
        base_url = self.configured_base_url or kubernetes_api_base_url()
        token = self.configured_token or service_account_token()
        if not base_url or not token:
            return {
                "applications": unavailable_collection(),
                "rollouts": unavailable_collection(),
            }

        async with kubernetes_client(self.transport) as client:
            applications = await self._read_collection(client, base_url, token, APPLICATIONS_PATH)
            rollouts = await self._read_collection(client, base_url, token, ROLLOUTS_PATH)
        return {
            "applications": normalized_collection(applications, normalize_application),
            "rollouts": normalized_collection(rollouts, normalize_rollout),
        }

    async def _read_collection(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        token: str,
        path: str,
    ) -> JsonObject | None:
        response = await client.get(
            f"{base_url}{path}",
            headers=kubernetes_headers(token),
            params={"limit": self.limit},
        )
        if response.status_code in UNAVAILABLE_STATUS_CODES:
            return None
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"items": []}


def unavailable_collection() -> JsonObject:
    return {"available": False, "truncated": False, "items": []}


def normalized_collection(
    payload: JsonObject | None,
    normalizer: Any,
) -> JsonObject:
    if payload is None:
        return unavailable_collection()
    items = payload.get("items")
    rows = (
        [normalizer(item) for item in items if isinstance(item, dict)]
        if isinstance(items, list)
        else []
    )
    metadata = mapping(payload.get("metadata"))
    return {
        "available": True,
        "truncated": bool(text(metadata.get("continue"))),
        "items": rows,
    }


def normalize_application(item: JsonObject) -> JsonObject:
    metadata = mapping(item.get("metadata"))
    spec = mapping(item.get("spec"))
    status = mapping(item.get("status"))
    sync = mapping(status.get("sync"))
    health = mapping(status.get("health"))
    operation = mapping(status.get("operationState"))
    sync_status = text(sync.get("status"))
    health_status = text(health.get("status"))
    operation_phase = text(operation.get("phase"))
    return {
        "namespace": text(metadata.get("namespace")),
        "name": text(metadata.get("name")),
        "sources": application_sources(spec),
        "sync_status": sync_status,
        "health_status": health_status,
        "reconciled_revision": text(sync.get("revision")),
        "operation_phase": operation_phase,
        "post_verification_ready": application_ready(
            sync_status,
            health_status,
            operation_phase,
        ),
    }


def application_sources(spec: Mapping[str, Any]) -> list[JsonObject]:
    candidates: list[Mapping[str, Any]] = []
    source = spec.get("source")
    if isinstance(source, Mapping):
        candidates.append(source)
    sources = spec.get("sources")
    if isinstance(sources, list):
        candidates.extend(item for item in sources if isinstance(item, Mapping))
    return [
        {
            "repo_url": sanitized_repo_url(item.get("repoURL")),
            "target_revision": text(item.get("targetRevision")),
            "path": text(item.get("path")),
        }
        for item in candidates
    ]


def application_ready(sync_status: str, health_status: str, operation_phase: str) -> bool:
    return (
        sync_status.lower() == "synced"
        and health_status.lower() == "healthy"
        and operation_phase.lower() in READY_OPERATION_PHASES
    )


def normalize_rollout(item: JsonObject) -> JsonObject:
    metadata = mapping(item.get("metadata"))
    status = mapping(item.get("status"))
    phase = text(status.get("phase"))
    aborted = bool(status.get("abort"))
    return {
        "namespace": text(metadata.get("namespace")),
        "name": text(metadata.get("name")),
        "phase": phase,
        "stable_revision": text(status.get("stableRS")),
        "current_revision": text(status.get("currentPodHash")),
        "message": text(status.get("message")),
        "aborted": aborted,
        "failed": aborted or phase.lower() in FAILED_ROLLOUT_PHASES,
    }


def mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def sanitized_repo_url(value: object) -> str:
    raw_url = text(value)
    try:
        parsed = urlsplit(raw_url)
        if parsed.username is None:
            return raw_url
        hostname = parsed.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        port = parsed.port
    except ValueError:
        return ""
    netloc = f"{hostname}:{port}" if port is not None else hostname
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
