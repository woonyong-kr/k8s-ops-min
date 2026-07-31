"""Pure bucketing and gap projection for the change timeline contract."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from packages.contracts.event_bus.interfaces import JsonObject

WARNING_SEVERITIES = frozenset({"warning", "critical"})


def build_change_timeline(
    *,
    from_ms: int,
    to_ms: int,
    bucket_ms: int,
    events: Sequence[Mapping[str, Any]],
    observations: Sequence[Mapping[str, Any]],
    required_cluster_ids: Collection[str],
) -> JsonObject:
    """Return exact event counts and explicit no-observation windows.

    An empty event bucket is not automatically a gap. A bucket is a gap only
    when at least one required cluster has no persisted inventory observation
    in that half-open bucket.
    """

    ordered_events = sorted(
        (dict(event) for event in events),
        key=lambda event: (
            int(event["occurredMs"]),
            str(event["kind"]),
            str(event["id"]),
        ),
    )
    event_counts: dict[int, int] = defaultdict(int)
    warning_counts: dict[int, int] = defaultdict(int)
    for event in ordered_events:
        occurred_ms = int(event["occurredMs"])
        if not from_ms <= occurred_ms < to_ms:
            continue
        index = (occurred_ms - from_ms) // bucket_ms
        event_counts[index] += 1
        if str(event["severity"]) in WARNING_SEVERITIES:
            warning_counts[index] += 1

    observed_by_bucket: dict[int, set[str]] = defaultdict(set)
    for observation in observations:
        observed_ms = int(observation["observed_ms"])
        if not from_ms <= observed_ms < to_ms:
            continue
        index = (observed_ms - from_ms) // bucket_ms
        observed_by_bucket[index].add(str(observation["cluster_id"]))

    required = {str(cluster_id) for cluster_id in required_cluster_ids}
    buckets: list[JsonObject] = []
    missing_intervals: list[tuple[int, int]] = []
    index = 0
    start = from_ms
    while start < to_ms:
        end = min(start + bucket_ms, to_ms)
        buckets.append(
            {
                "startMs": start,
                "endMs": end,
                "total": event_counts[index],
                "warnings": warning_counts[index],
            }
        )
        if required and not required.issubset(observed_by_bucket.get(index, set())):
            missing_intervals.append((start, end))
        start = end
        index += 1

    gaps = _merge_intervals(missing_intervals)
    return {
        "buckets": buckets,
        "events": [
            event for event in ordered_events if from_ms <= int(event["occurredMs"]) < to_ms
        ],
        "gaps": [{"from": start, "to": end} for start, end in gaps],
    }


def _merge_intervals(intervals: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in intervals:
        if merged and merged[-1][1] == start:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged
