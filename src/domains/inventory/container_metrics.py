"""Canonical normalization for container metrics carried by agent usage samples."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from packages.contracts.event_bus.interfaces import JsonObject

MAX_CONTAINER_METRIC_SERIES = 64


def container_metric_observations(value: Any) -> list[JsonObject]:
    """Return bounded, unique, finite container observations without filling gaps."""
    if not isinstance(value, list):
        return []
    containers: dict[str, JsonObject] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = _non_empty_text(item.get("name"))
        if name is None or name in containers:
            continue
        cpu_mcores = _non_negative_number(item.get("cpu_mcores"))
        mem_mib = _non_negative_number(item.get("mem_mib", item.get("memory_mib")))
        if cpu_mcores is None and mem_mib is None:
            continue
        containers[name] = {
            "name": name,
            "cpu_mcores": cpu_mcores,
            "mem_mib": mem_mib,
        }
    return [containers[name] for name in sorted(containers)[:MAX_CONTAINER_METRIC_SERIES]]


def _non_negative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _non_empty_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
