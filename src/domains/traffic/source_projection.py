"""Projection of target-agent traffic detector observations into browser descriptors."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from packages.contracts.parity import ClusterScope
from packages.contracts.traffic.control import (
    TRAFFIC_SOURCE_CONNECT_CAPABILITY,
    TRAFFIC_SOURCE_OBSERVER_CAPABILITY,
    TRAFFIC_SOURCE_SELECT_CAPABILITY,
    TrafficClusterSourceCatalog,
    TrafficDetectedCluster,
    TrafficSourceActionDescriptor,
    TrafficSourceDescriptor,
    TrafficSourcesResponse,
)
from packages.contracts.traffic.observations import TrafficScopeCoverage

TRAFFIC_SOURCE_OBSERVATION_UNAVAILABLE = "traffic_source_observation_unavailable"
TRAFFIC_SOURCE_OBSERVATION_INVALID = "traffic_source_observation_invalid"
TRAFFIC_SOURCE_AGENT_STALE = "traffic_source_agent_stale"
TRAFFIC_SOURCE_AGENT_DISCONNECTED = "traffic_source_agent_disconnected"
TRAFFIC_SOURCE_DEPLOY_FORBIDDEN = "traffic_source_deploy_forbidden"
TRAFFIC_SOURCE_CAPABILITY_UNAVAILABLE = "traffic_source_capability_unavailable"
TRAFFIC_SOURCE_DETECTION_ERROR = "traffic_source_detection_error"
TRAFFIC_SOURCE_LIVE_SECONDS = 90
TRAFFIC_SOURCE_STALE_SECONDS = 300


def traffic_sources_response(
    *,
    workspace_id: str,
    cluster_ids: Iterable[str],
    contexts: Mapping[str, Mapping[str, Any]],
    agent_statuses: Mapping[str, Mapping[str, Any]],
    deploy_cluster_ids: set[str],
    now: datetime | None = None,
) -> TrafficSourcesResponse:
    """Return one deterministic catalog per authorized selected cluster."""

    effective_now = now or datetime.now(UTC)
    catalogs = tuple(
        traffic_source_catalog(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            context=contexts.get(cluster_id),
            status=agent_statuses.get(cluster_id),
            deploy_allowed=cluster_id in deploy_cluster_ids,
            now=effective_now,
        )
        for cluster_id in sorted(
            {str(value).strip() for value in cluster_ids if str(value).strip()}
        )
    )
    reason_codes = tuple(sorted({reason for item in catalogs for reason in item.reason_codes}))
    if not catalogs or all(not item.sources for item in catalogs):
        availability = "unavailable"
        reason_codes = reason_codes or (TRAFFIC_SOURCE_OBSERVATION_UNAVAILABLE,)
    elif any(item.reason_codes or item.freshness != "live" for item in catalogs):
        availability = "partial"
        reason_codes = reason_codes or (TRAFFIC_SOURCE_AGENT_STALE,)
    else:
        availability = "available"
    observed = [item.observed_at for item in catalogs if item.observed_at]
    scopes = tuple(item.scope for item in catalogs)
    coverage = TrafficScopeCoverage(
        availability=availability,
        scopes=scopes,
        observed_at=max(observed) if observed else None,
        reason_codes=reason_codes,
    )
    return TrafficSourcesResponse(
        availability=availability,
        coverage=coverage,
        clusters=catalogs,
        reason_codes=reason_codes,
    )


def traffic_source_catalog(
    *,
    workspace_id: str,
    cluster_id: str,
    context: Mapping[str, Any] | None,
    status: Mapping[str, Any] | None,
    deploy_allowed: bool,
    now: datetime,
) -> TrafficClusterSourceCatalog:
    status_details = status.get("details") if isinstance(status, Mapping) else None
    details = status_details if isinstance(status_details, Mapping) else {}
    raw_observation = details.get("traffic_sources")
    observation = raw_observation if isinstance(raw_observation, Mapping) else None
    capabilities = {
        str(value)
        for value in ((status or {}).get("capabilities") or ())
        if isinstance(value, str) and value
    }
    freshness = _source_freshness(status, observation, now)
    scope = ClusterScope(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        freshness=freshness,
    )
    reasons: set[str] = set()
    if observation is None or TRAFFIC_SOURCE_OBSERVER_CAPABILITY not in capabilities:
        reasons.add(TRAFFIC_SOURCE_OBSERVATION_UNAVAILABLE)
        return _empty_catalog(
            scope=scope,
            freshness=freshness,
            capabilities=capabilities,
            deploy_allowed=deploy_allowed,
            reasons=reasons,
        )
    if freshness == "stale":
        reasons.add(TRAFFIC_SOURCE_AGENT_STALE)
    elif freshness == "disconnected":
        reasons.add(TRAFFIC_SOURCE_AGENT_DISCONNECTED)
    if not _inventory_complete(context):
        reasons.add("traffic_source_inventory_partial")

    active_source = _optional_text(observation.get("active_source"))
    observed_at = _optional_text(observation.get("observed_at"))
    raw_sources = observation.get("sources")
    sources: list[TrafficSourceDescriptor] = []
    if isinstance(raw_sources, list):
        for raw_source in raw_sources:
            source = _source_descriptor(
                raw_source,
                active_source=active_source,
                capabilities=capabilities,
                deploy_allowed=deploy_allowed,
            )
            if source is not None:
                sources.append(source)
    sources.sort(key=lambda item: item.key)
    if not sources:
        reasons.add(TRAFFIC_SOURCE_OBSERVATION_INVALID)
    elif any(source.status == "error" for source in sources):
        reasons.add(TRAFFIC_SOURCE_DETECTION_ERROR)
    if active_source and active_source not in {item.key for item in sources}:
        active_source = None
        reasons.add(TRAFFIC_SOURCE_OBSERVATION_INVALID)
    cluster = _cluster_descriptor(observation.get("cluster"))
    revision = _capability_revision(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        active_source=active_source,
        sources=sources,
        capabilities=capabilities,
        deploy_allowed=deploy_allowed,
    )
    return TrafficClusterSourceCatalog(
        scope=scope,
        freshness=freshness,
        observed_at=observed_at,
        active_source=active_source,
        capability_revision=revision,
        cluster=cluster,
        sources=tuple(sources),
        reason_codes=tuple(sorted(reasons)),
    )


def _source_descriptor(
    value: object,
    *,
    active_source: str | None,
    capabilities: set[str],
    deploy_allowed: bool,
) -> TrafficSourceDescriptor | None:
    if not isinstance(value, Mapping):
        return None
    key = _optional_text(value.get("key"))
    label = _optional_text(value.get("label"))
    status = _optional_text(value.get("status"))
    message = _optional_text(value.get("message"))
    if not key or not label or status not in {"available", "not_detected", "error"} or not message:
        return None
    actions: tuple[TrafficSourceActionDescriptor, ...] = ()
    if status == "available":
        action_kind = "connect" if key == active_source else "select"
        capability = (
            TRAFFIC_SOURCE_CONNECT_CAPABILITY
            if action_kind == "connect"
            else TRAFFIC_SOURCE_SELECT_CAPABILITY
        )
        enabled = deploy_allowed and capability in capabilities
        reason = (
            None
            if enabled
            else (
                TRAFFIC_SOURCE_DEPLOY_FORBIDDEN
                if not deploy_allowed
                else TRAFFIC_SOURCE_CAPABILITY_UNAVAILABLE
            )
        )
        action_label = f"{'Connect' if action_kind == 'connect' else 'Use'} {label}"
        actions = (
            TrafficSourceActionDescriptor(
                id=action_kind,
                kind=action_kind,
                label=action_label,
                enabled=enabled,
                reason_code=reason,
            ),
        )
    try:
        return TrafficSourceDescriptor(
            key=key,
            label=label,
            status=status,
            version=_optional_text(value.get("version")),
            native=value.get("native") is True,
            message=message,
            actions=actions,
        )
    except ValueError:
        return None


def _cluster_descriptor(value: object) -> TrafficDetectedCluster | None:
    if not isinstance(value, Mapping):
        return None
    platform = _optional_text(value.get("platform"))
    cni = _optional_text(value.get("cni"))
    if not platform or not cni:
        return None
    try:
        return TrafficDetectedCluster(
            platform=platform,
            cni=cni,
            dataplane_v2=value.get("dataplane_v2") is True,
            kubernetes_version=_optional_text(value.get("kubernetes_version")),
        )
    except ValueError:
        return None


def _empty_catalog(
    *,
    scope: ClusterScope,
    freshness: str,
    capabilities: set[str],
    deploy_allowed: bool,
    reasons: set[str],
) -> TrafficClusterSourceCatalog:
    revision = _capability_revision(
        workspace_id=scope.workspace_id,
        cluster_id=scope.cluster_id,
        active_source=None,
        sources=(),
        capabilities=capabilities,
        deploy_allowed=deploy_allowed,
    )
    return TrafficClusterSourceCatalog(
        scope=scope,
        freshness=freshness,
        capability_revision=revision,
        sources=(),
        reason_codes=tuple(sorted(reasons)),
    )


def _capability_revision(
    *,
    workspace_id: str,
    cluster_id: str,
    active_source: str | None,
    sources: Iterable[TrafficSourceDescriptor],
    capabilities: set[str],
    deploy_allowed: bool,
) -> str:
    document = {
        "workspace_id": workspace_id,
        "cluster_id": cluster_id,
        "active_source": active_source,
        "sources": [source.model_dump(mode="json") for source in sources],
        "capabilities": sorted(capabilities),
        "deploy_allowed": deploy_allowed,
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _source_freshness(
    status: Mapping[str, Any] | None,
    observation: Mapping[str, Any] | None,
    now: datetime,
) -> str:
    if not isinstance(status, Mapping) or str(status.get("status") or "").lower() != "connected":
        return "disconnected"
    timestamps = [
        _parse_time(status.get("last_seen_at")),
        _parse_time((observation or {}).get("observed_at")),
    ]
    observed = min((item for item in timestamps if item is not None), default=None)
    if observed is None:
        return "partial"
    age = max(0.0, (now - observed).total_seconds())
    if age <= TRAFFIC_SOURCE_LIVE_SECONDS:
        return "live"
    if age <= TRAFFIC_SOURCE_STALE_SECONDS:
        return "stale"
    return "disconnected"


def _inventory_complete(context: Mapping[str, Any] | None) -> bool:
    return bool(
        context
        and int(context.get("snapshot_revision") or 0) > 0
        and context.get("resources_complete") is True
        and context.get("labels_complete") is True
    )


def _parse_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _optional_text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None
