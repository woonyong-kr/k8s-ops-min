"""Bounded half-open reads for dense change-timeline windows."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from packages.contracts.event_bus.interfaces import JsonObject

MAX_CHANGE_PARTITION_REQUESTS = 255


class ChangeEvidencePartitionLimitExceeded(ValueError):
    """The exact response cannot fit the public bounded contract."""


def load_partitioned_change_evidence(
    reader: Callable[..., Mapping[str, Any]],
    *,
    query: Mapping[str, Any],
    from_ms: int,
    to_ms: int,
    leaf_limit: int,
    max_events: int,
    max_observations: int,
    max_requests: int = MAX_CHANGE_PARTITION_REQUESTS,
) -> JsonObject:
    """Split only overflowing leaves and merge exact half-open evidence.

    Parent rows from an overflowing read are discarded. Only complete leaves
    contribute to the result, so event counts and observation coverage remain
    exact while each database read keeps its existing hard limit.
    """
    if (
        from_ms >= to_ms
        or leaf_limit < 1
        or max_events < 1
        or max_observations < 1
        or max_requests < 1
    ):
        raise ValueError("change evidence partition bounds are invalid")

    pending = [(from_ms, to_ms)]
    events: list[JsonObject] = []
    observations: list[JsonObject] = []
    request_count = 0
    while pending:
        if request_count >= max_requests:
            raise ChangeEvidencePartitionLimitExceeded("change evidence partition limit exceeded")
        leaf_from, leaf_to = pending.pop()
        request_count += 1
        evidence = reader(
            **query,
            from_ms=leaf_from,
            to_ms=leaf_to,
            limit=leaf_limit,
        )
        if evidence.get("event_overflow") or evidence.get("observation_overflow"):
            if leaf_to - leaf_from <= 1:
                raise ChangeEvidencePartitionLimitExceeded(
                    "change evidence cannot be partitioned below one millisecond"
                )
            midpoint = leaf_from + (leaf_to - leaf_from) // 2
            # Stack right first so completed leaves are accumulated chronologically.
            pending.append((midpoint, leaf_to))
            pending.append((leaf_from, midpoint))
            continue

        leaf_events = [dict(item) for item in evidence.get("events") or []]
        leaf_observations = [dict(item) for item in evidence.get("observations") or []]
        if len(events) + len(leaf_events) > max_events:
            raise ChangeEvidencePartitionLimitExceeded("change event response limit exceeded")
        if len(observations) + len(leaf_observations) > max_observations:
            raise ChangeEvidencePartitionLimitExceeded("change observation response limit exceeded")
        events.extend(leaf_events)
        observations.extend(leaf_observations)

    events.sort(
        key=lambda event: (
            int(event["occurredMs"]),
            str(event["kind"]),
            str(event["id"]),
        )
    )
    observations.sort(
        key=lambda observation: (
            int(observation["observed_ms"]),
            str(observation["cluster_id"]),
        )
    )
    return {
        "events": events,
        "observations": observations,
        "event_overflow": False,
        "observation_overflow": False,
    }
