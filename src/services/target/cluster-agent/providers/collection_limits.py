from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping

from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gateway.requests import MAX_EVIDENCE_PAYLOAD_BYTES

COLLECTION_LIMITS_KEY = "collection_limits"
PROVIDER_PAYLOAD_BYTE_MARGIN = 65_536
MAX_PROVIDER_PAYLOAD_BYTES = MAX_EVIDENCE_PAYLOAD_BYTES - PROVIDER_PAYLOAD_BYTE_MARGIN
MIN_LIMITED_LIST_ITEMS = 0


def limit_payload_list(
    payload: JsonObject,
    key: str,
    max_items: int,
    limits: JsonObject,
    *,
    group_key: Callable[[object], str] | None = None,
) -> None:
    """Limit one list field and record the original size."""
    value = payload.get(key)
    if not isinstance(value, list) or len(value) <= max_items:
        return
    limited = limit_list(value, max_items, group_key=group_key)
    payload[key] = limited
    record_collection_limit(limits, key, len(value), len(limited))


def limit_payload_size(
    payload: JsonObject,
    *,
    list_keys: Iterable[str],
    limits: JsonObject,
    max_bytes: int = MAX_PROVIDER_PAYLOAD_BYTES,
    group_keys: Mapping[str, Callable[[object], str]] | None = None,
) -> None:
    """Keep shrinking large lists until the JSON payload fits the byte budget."""
    keys = tuple(list_keys)
    while payload_size_bytes(payload) > max_bytes:
        candidate = largest_shrinkable_list(payload, keys)
        if candidate is None:
            return
        key, value = candidate
        target_count = max(MIN_LIMITED_LIST_ITEMS, len(value) // 2)
        group_key = group_keys.get(key) if group_keys else None
        limited = limit_list(value, target_count, group_key=group_key)
        payload[key] = limited
        record_collection_limit(
            limits,
            key,
            original_count_for(limits, key, len(value)),
            len(limited),
        )


def limit_list[ItemT](
    items: list[ItemT],
    max_items: int,
    *,
    group_key: Callable[[ItemT], str] | None = None,
) -> list[ItemT]:
    """Return a bounded list, optionally keeping groups fairly represented."""
    if len(items) <= max_items:
        return items
    if group_key is None:
        return items[:max_items]
    return round_robin_groups(items, max_items, group_key)


def round_robin_groups[ItemT](
    items: list[ItemT],
    max_items: int,
    group_key: Callable[[ItemT], str],
) -> list[ItemT]:
    """Select items across groups so one large group does not hide the rest."""
    buckets: OrderedDict[str, list[ItemT]] = OrderedDict()
    for item in items:
        buckets.setdefault(group_key(item), []).append(item)

    selected: list[ItemT] = []
    while buckets and len(selected) < max_items:
        for key in list(buckets):
            bucket = buckets[key]
            if bucket:
                selected.append(bucket.pop(0))
                if len(selected) >= max_items:
                    break
            if not bucket:
                buckets.pop(key, None)
    return selected


def collection_limit(original_count: int, returned_count: int) -> JsonObject:
    """Build the shared collection limit shape."""
    return {
        "truncated": True,
        "original_count": original_count,
        "returned_count": returned_count,
    }


def record_collection_limit(
    limits: JsonObject,
    key: str,
    original_count: int,
    returned_count: int,
) -> None:
    """Record one list limit while keeping the first original count."""
    limits[key] = collection_limit(original_count_for(limits, key, original_count), returned_count)


def original_count_for(limits: JsonObject, key: str, fallback: int) -> int:
    """Return the first known original count for one limited list."""
    existing = limits.get(key)
    if isinstance(existing, dict) and isinstance(existing.get("original_count"), int):
        return existing["original_count"]
    return fallback


def payload_size_bytes(payload: JsonObject) -> int:
    """Return the JSON byte size used for payload budgeting."""
    return len(json.dumps(payload, default=str).encode())


def largest_shrinkable_list(
    payload: JsonObject,
    keys: Iterable[str],
) -> tuple[str, list[object]] | None:
    """Find the largest list by JSON byte size that can still be reduced."""
    candidates: list[tuple[int, str, list[object]]] = []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list) and len(value) > MIN_LIMITED_LIST_ITEMS:
            candidates.append((payload_size_bytes(value), key, value))
    if not candidates:
        return None
    _length, key, value = max(candidates, key=lambda item: item[0])
    return key, value


def attach_collection_limits(payload: JsonObject, limits: JsonObject) -> None:
    """Attach collection limit details to a provider payload."""
    if not limits:
        return
    existing = payload.get(COLLECTION_LIMITS_KEY)
    lists: JsonObject = {}
    if isinstance(existing, dict) and isinstance(existing.get("lists"), dict):
        lists.update(existing["lists"])
    lists.update(limits)
    payload[COLLECTION_LIMITS_KEY] = {
        "truncated": True,
        "lists": lists,
    }
