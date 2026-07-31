"""Server-owned Timeline transport policy.

Browser surfaces receive this policy in their snapshot frame and must not
invent batch, frame-rate, or retention values locally.
"""

from __future__ import annotations

from datetime import UTC, datetime

from packages.config.settings import env
from packages.contracts.timeline import (
    RealtimePolicy,
    TimelineActivityControlOption,
    TimelineBooleanControl,
    TimelineCapabilityDescriptor,
    TimelineControlOption,
    TimelineControlSurface,
    TimelineCoverageSourceAvailability,
    TimelineFacetControl,
    TimelineLegendControl,
    TimelineLensZoomOption,
    TimelineLiveSessionPolicy,
    TimelinePinsControl,
    TimelineQueryBounds,
    TimelineRangePreset,
    TimelineReconnectPolicy,
)

TIMELINE_MAX_BATCH_EVENTS_ENV = "TIMELINE_MAX_BATCH_EVENTS"
TIMELINE_MAX_FRAMES_PER_SECOND_ENV = "TIMELINE_MAX_FRAMES_PER_SECOND"
TIMELINE_RETENTION_SECONDS_ENV = "TIMELINE_RETENTION_SECONDS"
TIMELINE_MAX_WINDOW_SECONDS_ENV = "TIMELINE_MAX_WINDOW_SECONDS"
TIMELINE_REPLAY_POLL_SECONDS_ENV = "TIMELINE_REPLAY_POLL_SECONDS"
TIMELINE_COVERAGE_CACHE_SECONDS_ENV = "TIMELINE_COVERAGE_CACHE_SECONDS"
TIMELINE_COVERAGE_REFRESH_SECONDS_ENV = "TIMELINE_COVERAGE_REFRESH_SECONDS"
TIMELINE_RECONNECT_MIN_DELAY_MS_ENV = "TIMELINE_RECONNECT_MIN_DELAY_MS"
TIMELINE_RECONNECT_MAX_DELAY_MS_ENV = "TIMELINE_RECONNECT_MAX_DELAY_MS"
TIMELINE_LIVE_SESSION_MAX_AGE_MS_ENV = "TIMELINE_LIVE_SESSION_MAX_AGE_MS"

DEFAULT_MAX_BATCH_EVENTS = 1_000
DEFAULT_MAX_FRAMES_PER_SECOND = 60
DEFAULT_RETENTION_SECONDS = 86_400
DEFAULT_MAX_WINDOW_SECONDS = 2_592_000
DEFAULT_REPLAY_POLL_SECONDS = 1.0
DEFAULT_COVERAGE_CACHE_SECONDS = 2.0
DEFAULT_COVERAGE_REFRESH_SECONDS = 15.0
DEFAULT_RECONNECT_MIN_DELAY_MS = 500
DEFAULT_RECONNECT_MAX_DELAY_MS = 30_000
DEFAULT_LIVE_SESSION_MAX_AGE_MS = 30_000

HOUR_MS = 60 * 60 * 1_000
DAY_MS = 24 * HOUR_MS

# Retained overview buckets are deliberately server-selected.  One hour is the
# fidelity currently guaranteed by retained collection; narrower display bins
# would manufacture precision that the source has not promised.
TIMELINE_OVERVIEW_BUCKET_WIDTHS_MS = (
    HOUR_MS,
    2 * HOUR_MS,
    3 * HOUR_MS,
    6 * HOUR_MS,
    12 * HOUR_MS,
    DAY_MS,
    2 * DAY_MS,
    3 * DAY_MS,
    7 * DAY_MS,
    14 * DAY_MS,
    30 * DAY_MS,
)
TIMELINE_OVERVIEW_MAX_BUCKETS = 256


def timeline_realtime_policy() -> RealtimePolicy:
    """Read and validate one server policy shared by snapshot and SSE adapters."""
    return RealtimePolicy(
        max_batch_events=_positive_int(
            TIMELINE_MAX_BATCH_EVENTS_ENV,
            default=DEFAULT_MAX_BATCH_EVENTS,
            maximum=10_000,
        ),
        max_frames_per_second=_positive_int(
            TIMELINE_MAX_FRAMES_PER_SECOND_ENV,
            default=DEFAULT_MAX_FRAMES_PER_SECOND,
            maximum=60,
        ),
        retention_seconds=_positive_int(
            TIMELINE_RETENTION_SECONDS_ENV,
            default=DEFAULT_RETENTION_SECONDS,
            maximum=31_536_000,
        ),
        resume="cursor",
        hidden_tab="coalesce",
        reconnect=TimelineReconnectPolicy(
            min_delay_ms=_positive_int(
                TIMELINE_RECONNECT_MIN_DELAY_MS_ENV,
                default=DEFAULT_RECONNECT_MIN_DELAY_MS,
                maximum=60_000,
            ),
            max_delay_ms=_positive_int(
                TIMELINE_RECONNECT_MAX_DELAY_MS_ENV,
                default=DEFAULT_RECONNECT_MAX_DELAY_MS,
                maximum=300_000,
            ),
            strategy="full_jitter_exponential",
        ),
        live_session=TimelineLiveSessionPolicy(
            max_age_ms=_positive_int(
                TIMELINE_LIVE_SESSION_MAX_AGE_MS_ENV,
                default=DEFAULT_LIVE_SESSION_MAX_AGE_MS,
                maximum=300_000,
            ),
            strategy="replace_with_snapshot",
        ),
    )


def timeline_max_window_ms() -> int:
    """Return the server-side retained-history read ceiling in milliseconds."""
    return (
        _positive_int(
            TIMELINE_MAX_WINDOW_SECONDS_ENV,
            default=DEFAULT_MAX_WINDOW_SECONDS,
            maximum=31_536_000,
        )
        * 1_000
    )


def timeline_query_bounds(*, now: datetime | None = None) -> TimelineQueryBounds:
    """Materialize the retained strip's only authoritative time boundary.

    This is intentionally policy metadata, not evidence coverage.  A caller
    still needs the per-source coverage projection before claiming that any
    point inside this window was actually collected.
    """
    observed_now = now or datetime.now(UTC)
    if observed_now.tzinfo is None:
        raise ValueError("timeline server clock must be timezone-aware")
    server_now_ms = max(0, int(observed_now.timestamp() * 1_000))
    retention_ms = timeline_realtime_policy().retention_seconds * 1_000
    return TimelineQueryBounds(
        server_now_ms=server_now_ms,
        earliest_queryable_ms=max(0, server_now_ms - retention_ms),
        max_window_ms=timeline_max_window_ms(),
    )


def timeline_capability_descriptor(
    *, query_bounds: TimelineQueryBounds | None = None
) -> TimelineCapabilityDescriptor:
    """Describe only Timeline sources implemented by this server deployment.

    Retained history is the sole implemented source today.  The descriptor is
    intentionally not derived from a browser preference, and it does not
    weaken the service's source-specific RBAC predicate.
    """
    return TimelineCapabilityDescriptor(
        selected_source_mode="retained",
        available_source_modes=("retained",),
        max_retained_range_ms=timeline_max_window_ms(),
        query_bounds=query_bounds or timeline_query_bounds(),
        namespace_filter_policy="not_required",
        control_surface=timeline_control_surface(),
    )


def timeline_control_surface() -> TimelineControlSurface:
    """Return the complete retained Timeline control vocabulary.

    This is deliberately ordinary server configuration rather than a copy of
    browser constants.  A client receives stable IDs, labels, exact evidence
    selections, and time/lens limits before it renders a toolbar or strip.
    """
    max_window_ms = timeline_max_window_ms()
    ranges = tuple(
        TimelineRangePreset(id=identifier, label=label, duration_ms=duration)
        for identifier, label, duration in (
            ("1h", "1h", HOUR_MS),
            ("6h", "6h", 6 * HOUR_MS),
            ("24h", "24h", DAY_MS),
            ("7d", "7d", 7 * DAY_MS),
            ("30d", "30d", 30 * DAY_MS),
        )
        if duration <= max_window_ms
    )
    lens_rungs = tuple(
        TimelineLensZoomOption(id=identifier, label=label, duration_ms=duration)
        for identifier, label, duration in (
            ("15m", "15m", 15 * 60 * 1_000),
            ("30m", "30m", 30 * 60 * 1_000),
            ("1h", "1h", HOUR_MS),
            ("2h", "2h", 2 * HOUR_MS),
            ("6h", "6h", 6 * HOUR_MS),
            ("12h", "12h", 12 * HOUR_MS),
            ("1d", "1d", DAY_MS),
            ("2d", "2d", 2 * DAY_MS),
            ("7d", "7d", 7 * DAY_MS),
            ("14d", "14d", 14 * DAY_MS),
            ("30d", "30d", 30 * DAY_MS),
        )
        if duration <= max_window_ms
    )
    if not ranges or not lens_rungs:
        raise ValueError("Timeline retained window must be at least one hour")
    return TimelineControlSurface(
        views=(
            TimelineControlOption(id="list", label="List"),
            TimelineControlOption(id="swimlane", label="Swimlane"),
        ),
        groupings=(
            TimelineControlOption(
                id="app",
                label="Application",
                description="Group lanes by server-defined application evidence.",
            ),
            TimelineControlOption(
                id="owner",
                label="Workload",
                description="Group lanes by owning workload evidence.",
            ),
            TimelineControlOption(
                id="flat",
                label="None",
                description="Show one lane for each resource.",
            ),
        ),
        sorts=(
            TimelineControlOption(id="importance", label="Importance"),
            TimelineControlOption(id="recent", label="Recent activity"),
            TimelineControlOption(id="name", label="Name (A→Z)"),
        ),
        activity=(
            TimelineActivityControlOption(
                id="all",
                label="All",
                activity=(),
                problems_activity=("unhealthy", "warning"),
            ),
            TimelineActivityControlOption(
                id="changes",
                label="Changes",
                activity=("change",),
                problems_activity=("unhealthy",),
            ),
            TimelineActivityControlOption(
                id="k8s_events",
                label="K8s Events",
                activity=("k8s_event",),
                problems_activity=("warning",),
            ),
        ),
        deleted=TimelineBooleanControl(
            key="include_deleted",
            label="Show deleted",
            default=True,
        ),
        kinds=TimelineFacetControl(
            key="kinds",
            label="Kinds",
            selection="multi",
            empty_selection="all",
        ),
        time_ranges=ranges,
        default_time_range_id=ranges[0].id,  # type: ignore[arg-type]
        lens_zoom_rungs=lens_rungs,
        default_lens_zoom_rung=_default_lens_zoom_rung(lens_rungs),
        legend=TimelineLegendControl(
            label="Legend",
            availability="available",
            items=(
                TimelineControlOption(id="change", label="Changes"),
                TimelineControlOption(id="k8s_event", label="K8s Events"),
                TimelineControlOption(id="warning", label="Warnings"),
                TimelineControlOption(id="unhealthy", label="Unhealthy"),
            ),
        ),
        pins=TimelinePinsControl(
            label="Pinned lanes",
            availability="available",
            storage="server",
            revision="pin_set",
            subject_kinds=("resource", "application"),
        ),
    )


def timeline_overview_bucket_width_ms(window_width_ms: int) -> int:
    """Choose one bounded retained-strip resolution without client input."""
    if window_width_ms < 1:
        raise ValueError("timeline overview window must be positive")
    for width_ms in TIMELINE_OVERVIEW_BUCKET_WIDTHS_MS:
        if (window_width_ms + width_ms - 1) // width_ms <= TIMELINE_OVERVIEW_MAX_BUCKETS:
            return width_ms
    return TIMELINE_OVERVIEW_BUCKET_WIDTHS_MS[-1]


def timeline_coverage_source_availability() -> tuple[TimelineCoverageSourceAvailability, ...]:
    """Describe coverage evidence honestly; unavailable is never a zero-gap claim."""
    return (
        TimelineCoverageSourceAvailability(source="inventory", availability="unavailable"),
        TimelineCoverageSourceAvailability(source="incident", availability="unavailable"),
        TimelineCoverageSourceAvailability(
            source="application_workflow", availability="unavailable"
        ),
        TimelineCoverageSourceAvailability(source="kubernetes_event", availability="observed"),
        TimelineCoverageSourceAvailability(source="gitops", availability="unavailable"),
    )


def timeline_control_selection_is_valid(query: object) -> bool:
    """Validate a parsed query without leaking configuration through errors."""
    from packages.contracts.timeline import TimelineQuery

    if not isinstance(query, TimelineQuery):
        return False
    controls = timeline_control_surface()
    if query.view not in {option.id for option in controls.views}:
        return False
    if query.grouping not in {option.id for option in controls.groupings}:
        return False
    if query.sort not in {option.id for option in controls.sorts}:
        return False
    if query.lens_zoom_rung not in {option.id for option in controls.lens_zoom_rungs}:
        return False
    if query.filters.pinned_only and controls.pins.availability != "available":
        return False
    allowed_activity_selections = {option.activity for option in controls.activity} | {
        option.problems_activity for option in controls.activity
    }
    if query.filters.activity not in allowed_activity_selections:
        return False
    if query.range_id == "custom":
        return True
    selected_range = next(
        (option for option in controls.time_ranges if option.id == query.range_id),
        None,
    )
    return (
        selected_range is not None
        and query.mode == "live"
        and query.window.to_ms - query.window.from_ms == selected_range.duration_ms
    )


def _default_lens_zoom_rung(
    options: tuple[TimelineLensZoomOption, ...],
) -> str:
    return next((option.id for option in options if option.id == "1h"), options[0].id)


def timeline_replay_poll_seconds() -> float:
    """Bound server-side durable replay repair; browsers never poll Timeline."""
    value = float(env(TIMELINE_REPLAY_POLL_SECONDS_ENV, str(DEFAULT_REPLAY_POLL_SECONDS)))
    if not 0.1 <= value <= 60:
        raise ValueError(f"{TIMELINE_REPLAY_POLL_SECONDS_ENV} must be between 0.1 and 60")
    return value


def timeline_coverage_cache_seconds() -> float:
    """Coalesce identical overview/snapshot/SSE coverage reads inside one worker."""
    return _bounded_float(
        TIMELINE_COVERAGE_CACHE_SECONDS_ENV,
        default=DEFAULT_COVERAGE_CACHE_SECONDS,
        minimum=0.1,
        maximum=30.0,
    )


def timeline_coverage_refresh_seconds() -> float:
    """Refresh SSE coverage independently from the one-second ledger repair cadence."""
    return _bounded_float(
        TIMELINE_COVERAGE_REFRESH_SECONDS_ENV,
        default=DEFAULT_COVERAGE_REFRESH_SECONDS,
        minimum=1.0,
        maximum=300.0,
    )


def _bounded_float(name: str, *, default: float, minimum: float, maximum: float) -> float:
    value = float(env(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return value


def _positive_int(name: str, *, default: int, maximum: int) -> int:
    value = int(env(name, str(default)))
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value
