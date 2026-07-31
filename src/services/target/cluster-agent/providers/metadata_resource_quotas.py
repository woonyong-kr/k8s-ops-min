from __future__ import annotations

from packages.contracts.event_bus.interfaces import JsonObject
from providers.kubernetes_utils import compact_dict, metadata, object_or_empty, status


def resource_quota_snapshots(resource_quotas: list[JsonObject]) -> list[JsonObject]:
    """Build small ResourceQuota summaries for one namespace."""
    return [resource_quota_snapshot(resource_quota) for resource_quota in resource_quotas]


def resource_quota_snapshot(resource_quota: JsonObject) -> JsonObject:
    """Build one ResourceQuota hard and used summary."""
    meta = metadata(resource_quota)
    quota_status = status(resource_quota)
    return {
        "name": meta.get("name"),
        "namespace": meta.get("namespace"),
        "hard": compact_dict(object_or_empty(quota_status.get("hard"))),
        "used": compact_dict(object_or_empty(quota_status.get("used"))),
    }
