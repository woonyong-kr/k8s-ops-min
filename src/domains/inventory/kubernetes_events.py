"""Canonical Timeline facts for fully captured Kubernetes Event observations.

Inventory keeps scoped Event resources for product reads, while Timeline consumes
only a separately captured, complete all-namespace Event fact batch. It never
delegates an Event to the generic inventory change mapper or infers deletion.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from packages.contracts.parity import ClusterScope, ResourceRef
from packages.contracts.timeline import TimelineEvent, TimelineResourceSubject

EVENT_CAPTURE_SUMMARY_KEY = "kubernetes_event_capture"
EVENT_FACTS_SUMMARY_KEY = "kubernetes_event_facts"
EVENT_CAPTURE_REASON_COMPLETE = "complete"
EVENT_CAPTURE_REASON_INVALID_FACT = "invalid_event_fact_contract"


@dataclass(frozen=True)
class KubernetesEventCapture:
    """Collection proof required before Event observations can enter Timeline."""

    complete: bool
    truncated: bool
    reason: str = ""
    observed_at: datetime | None = None
    max_age_seconds: int | None = None
    coverage: Mapping[str, object] = field(default_factory=dict)

    @property
    def authoritative(self) -> bool:
        return (
            self.complete
            and not self.truncated
            and self.reason == EVENT_CAPTURE_REASON_COMPLETE
            and self.observed_at is not None
            and self.max_age_seconds is not None
            and self.max_age_seconds > 0
            and self.coverage.get("scope") == "all_namespaces"
            and self.coverage.get("pagination") == "continue"
            and not self.coverage.get("gap")
        )

    def is_fresh_at(self, observed_at: datetime) -> bool:
        """Require a declared capture freshness window at the server snapshot time."""
        if not self.authoritative or self.observed_at is None or self.max_age_seconds is None:
            return False
        age_seconds = (observed_at - self.observed_at).total_seconds()
        return 0 <= age_seconds <= self.max_age_seconds

    def with_gap(self, reason: str) -> KubernetesEventCapture:
        """Fail closed while preserving the safe coverage evidence for diagnostics."""
        coverage = dict(self.coverage)
        coverage["gap"] = reason
        return replace(self, complete=False, reason=reason, coverage=coverage)

    @classmethod
    def from_snapshot_summary(cls, summary: Mapping[str, object]) -> KubernetesEventCapture:
        """Fail closed unless a dedicated Event collector supplied both proof bits."""
        capture = summary.get(EVENT_CAPTURE_SUMMARY_KEY)
        if not isinstance(capture, Mapping):
            return cls(complete=False, truncated=False)
        complete = capture.get("complete")
        truncated = capture.get("truncated")
        if not isinstance(complete, bool) or not isinstance(truncated, bool):
            return cls(complete=False, truncated=False)
        reason = normalized_text(capture.get("reason"))
        freshness = capture.get("freshness")
        freshness_body = freshness if isinstance(freshness, Mapping) else {}
        observed_at = parse_timestamp(freshness_body.get("observed_at"))
        max_age_seconds = positive_int(freshness_body.get("max_age_seconds"))
        coverage = capture.get("coverage")
        return cls(
            complete=complete,
            truncated=truncated,
            reason=reason,
            observed_at=observed_at,
            max_age_seconds=max_age_seconds,
            coverage=dict(coverage) if isinstance(coverage, Mapping) else {},
        )


@dataclass(frozen=True)
class KubernetesEventObservation:
    """The minimal monotonic Event state safe to retain and compare."""

    uid: str
    api_version: str
    namespace: str | None
    name: str
    event_type: str
    count: int
    last_occurrence_at: datetime


@dataclass(frozen=True)
class KubernetesEventFactBatch:
    """A complete, all-namespace Event capture kept apart from inventory resources."""

    capture: KubernetesEventCapture
    observations: tuple[KubernetesEventObservation, ...]

    @classmethod
    def from_snapshot_summary(cls, summary: Mapping[str, object]) -> KubernetesEventFactBatch:
        """Decode only the dedicated safe fact contract stored on an inventory snapshot."""
        capture = KubernetesEventCapture.from_snapshot_summary(summary)
        facts = summary.get(EVENT_FACTS_SUMMARY_KEY)
        if not capture.authoritative:
            return cls(capture=capture, observations=())
        if not isinstance(facts, list):
            return cls(capture=capture.with_gap(EVENT_CAPTURE_REASON_INVALID_FACT), observations=())
        observations: list[KubernetesEventObservation] = []
        for fact in facts:
            observation = kubernetes_event_fact_observation(fact)
            if observation is None:
                return cls(
                    capture=capture.with_gap(EVENT_CAPTURE_REASON_INVALID_FACT), observations=()
                )
            observations.append(observation)
        expected_count = exact_nonnegative_int(capture.coverage.get("event_count"))
        if expected_count is None or expected_count != len(observations):
            return cls(capture=capture.with_gap(EVENT_CAPTURE_REASON_INVALID_FACT), observations=())
        return cls(capture=capture, observations=tuple(observations))


def kubernetes_event_fact_observation(fact: object) -> KubernetesEventObservation | None:
    """Decode one isolated all-namespace Event fact without reading inventory/raw data."""
    if not isinstance(fact, Mapping):
        return None
    uid = normalized_text(fact.get("uid"))
    name = normalized_text(fact.get("name"))
    count = positive_int(fact.get("count"))
    last_occurrence_at = event_last_occurrence(fact)
    if not uid or not name or count is None or last_occurrence_at is None:
        return None
    return KubernetesEventObservation(
        uid=uid,
        api_version=normalized_text(fact.get("api_version")),
        namespace=nullable_text(fact.get("namespace")),
        name=name,
        event_type=normalized_text(fact.get("type")),
        count=count,
        last_occurrence_at=last_occurrence_at,
    )


def kubernetes_event_fact_timeline_events(
    *,
    workspace_id: str,
    cluster_id: str,
    observed_at: datetime,
    previous: KubernetesEventFactBatch | None,
    current: KubernetesEventFactBatch,
) -> tuple[TimelineEvent, ...]:
    """Append only monotonic observations from one fresh, complete global capture."""
    if not current.capture.is_fresh_at(observed_at):
        return ()
    previous_by_uid = (
        event_observations_by_uid_from_facts(previous.observations)
        if previous is not None and previous.capture.authoritative
        else {}
    )
    current_by_uid = event_observations_by_uid_from_facts(current.observations)
    return tuple(
        kubernetes_event_timeline_event(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            observation=observation,
            capture=current.capture,
        )
        for uid, observation in sorted(current_by_uid.items())
        if event_observation_advanced(previous_by_uid.get(uid), observation)
    )


def event_observations_by_uid_from_facts(
    observations: Sequence[KubernetesEventObservation],
) -> dict[str, KubernetesEventObservation]:
    """Use the newest fact per UID if an API page ever repeats an Event."""
    by_uid: dict[str, KubernetesEventObservation] = {}
    for observation in observations:
        existing = by_uid.get(observation.uid)
        if existing is None or observation_sort_key(observation) > observation_sort_key(existing):
            by_uid[observation.uid] = observation
    return by_uid


def event_observation_advanced(
    previous: KubernetesEventObservation | None,
    current: KubernetesEventObservation,
) -> bool:
    """A count increase or later occurrence is the only allowed Event change."""
    return previous is None or (
        current.count > previous.count or current.last_occurrence_at > previous.last_occurrence_at
    )


def kubernetes_event_timeline_event(
    *,
    workspace_id: str,
    cluster_id: str,
    observation: KubernetesEventObservation,
    capture: KubernetesEventCapture | None = None,
) -> TimelineEvent:
    """Map one complete Event observation without its message, manifest, or log."""
    api_group, version = split_api_version(observation.api_version)
    resource = ResourceRef(
        api_group=api_group,
        version=version,
        kind="Event",
        namespace=observation.namespace,
        name=observation.name,
        uid=observation.uid,
    )
    source_key = kubernetes_event_source_key(observation)
    return TimelineEvent(
        event_id=source_key,
        source="kubernetes_event",
        source_key=source_key,
        native_id=observation.uid,
        activity="k8s_event",
        occurred_at=observation.last_occurrence_at,
        scope=ClusterScope(workspace_id=workspace_id, cluster_id=cluster_id),
        subject=TimelineResourceSubject(resource=resource),
        resource=resource,
        event_type="k8s_event",
        severity="warning" if observation.event_type.casefold() == "warning" else "info",
        title="Kubernetes event observed",
        metadata=kubernetes_event_timeline_metadata(observation, capture),
    )


def kubernetes_event_timeline_metadata(
    observation: KubernetesEventObservation,
    capture: KubernetesEventCapture | None,
) -> dict[str, object]:
    """Expose only safe capture proof beside the immutable Event observation."""
    metadata: dict[str, object] = {
        "count": observation.count,
        "last_occurrence_at": timestamp_wire_value(observation.last_occurrence_at),
        "collection_complete": True,
        "collection_truncated": False,
    }
    if capture is not None and capture.observed_at is not None:
        metadata.update(
            {
                "collection_reason": capture.reason,
                "capture_observed_at": timestamp_wire_value(capture.observed_at),
                "freshness_max_age_seconds": capture.max_age_seconds,
            }
        )
    return metadata


def kubernetes_event_source_key(observation: KubernetesEventObservation) -> str:
    """Canonical idempotency key for exactly one Event count/time observation."""
    return ":".join(
        (
            "kubernetes_event",
            observation.uid,
            str(observation.count),
            timestamp_wire_value(observation.last_occurrence_at),
        )
    )


def event_last_occurrence(summary: Mapping[str, object]) -> datetime | None:
    for key in ("last_occurrence_at", "last_timestamp"):
        value = parse_timestamp(summary.get(key))
        if value is not None:
            return value
    return None


def observation_sort_key(observation: KubernetesEventObservation) -> tuple[int, datetime]:
    return observation.count, observation.last_occurrence_at


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def timestamp_wire_value(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if not isinstance(value, str):
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 1 else None


def exact_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    return None


def normalized_text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def nullable_text(value: object) -> str | None:
    normalized = normalized_text(value)
    return normalized or None


def split_api_version(api_version: str) -> tuple[str, str]:
    if "/" not in api_version:
        return "", api_version
    api_group, version = api_version.rsplit("/", 1)
    return api_group, version
