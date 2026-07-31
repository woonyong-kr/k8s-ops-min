"""Strict, transport-neutral timeline contracts.

The model is used by retained-history reads, live subscriptions, and desktop
replay.  A client never supplies an arbitrary workspace: scopes are validated
as one authenticated workspace and then authorized by the adapter.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from packages.contracts.gateway.base import StrictModel
from packages.contracts.parity import ClusterScope, ResourceRef

TimelineActivity = Literal["change", "k8s_event", "warning", "unhealthy"]
TimelineSource = Literal[
    "inventory",
    "incident",
    "application_workflow",
    "kubernetes_event",
    "gitops",
]
TimelineEventType = Literal[
    "add",
    "update",
    "delete",
    "k8s_event",
    "incident",
    "deployment",
    "gitops_change",
]
TimelineSeverity = Literal["info", "warning", "critical", "unknown"]
TimelineGrouping = Literal["app", "owner", "flat"]
TimelineSort = Literal["importance", "recent", "name"]
TimelineReadMode = Literal["live", "frozen"]
TimelineSourceMode = Literal["retained", "local"]
TimelineNamespaceFilterPolicy = Literal["not_required", "required"]
TimelineView = Literal["list", "swimlane"]
TimelineRangeId = Literal["1h", "6h", "24h", "7d", "30d", "custom"]
TimelineLensZoomRung = Literal[
    "15m",
    "30m",
    "1h",
    "2h",
    "6h",
    "12h",
    "1d",
    "2d",
    "7d",
    "14d",
    "30d",
]
TimelineFrameKind = Literal[
    "snapshot",
    "event",
    "coverage",
    "resync_required",
    "end",
    "error",
]


class TimelineWindow(StrictModel):
    from_ms: int = Field(ge=0)
    to_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> TimelineWindow:
        if self.from_ms >= self.to_ms:
            raise ValueError("timeline window must have positive width")
        return self


class TimelineFilters(StrictModel):
    """Source-independent filters shared by list and swimlane representations."""

    activity: tuple[TimelineActivity, ...] = ()
    kinds: tuple[str, ...] = ()
    include_deleted: bool = True
    pinned_only: bool = False
    query: str = Field(default="", max_length=1_000)

    @field_validator("activity")
    @classmethod
    def canonicalize_activity(
        cls, activity: tuple[TimelineActivity, ...]
    ) -> tuple[TimelineActivity, ...]:
        return tuple(sorted(set(activity)))

    @field_validator("kinds")
    @classmethod
    def canonicalize_kinds(cls, kinds: tuple[str, ...]) -> tuple[str, ...]:
        normalized = {kind.strip() for kind in kinds if kind.strip()}
        return tuple(sorted(normalized))


class TimelinePinResourceTarget(StrictModel):
    """One exact, user-selected Kubernetes resource before the server persists it."""

    kind: Literal["resource"] = "resource"
    scope: ClusterScope
    resource: ResourceRef


class TimelinePinApplicationTarget(StrictModel):
    """One stable application identity before the server materializes its snapshot."""

    kind: Literal["application"] = "application"
    application_id: str = Field(min_length=1, max_length=512)


TimelinePinTarget = Annotated[
    TimelinePinResourceTarget | TimelinePinApplicationTarget,
    Field(discriminator="kind"),
]


class TimelineApplicationPinSnapshot(StrictModel):
    """Immutable display facts materialized from the currently readable application."""

    name: str = Field(min_length=1, max_length=512)
    repository_id: str = Field(min_length=1, max_length=512)
    manifest_path: str = Field(min_length=1, max_length=2_048)


class TimelinePinnedResourceSubject(TimelinePinResourceTarget):
    """The exact resource reference at pin creation; it is never rewritten on rename."""


class TimelinePinnedApplicationSubject(TimelinePinApplicationTarget):
    """The stable application identifier plus immutable server-owned display facts."""

    snapshot: TimelineApplicationPinSnapshot


TimelinePinSubject = Annotated[
    TimelinePinnedResourceSubject | TimelinePinnedApplicationSubject,
    Field(discriminator="kind"),
]


class TimelinePin(StrictModel):
    """A persistent user/workspace pin. Hidden pins are omitted, never re-owned or rewritten."""

    pin_id: str = Field(min_length=1, max_length=128)
    subject: TimelinePinSubject
    created_at: datetime


class TimelinePinSet(StrictModel):
    """Visible pins for exactly one authenticated user and workspace, plus its optimistic revision."""

    revision: int = Field(ge=0)
    pins: tuple[TimelinePin, ...] = ()


class TimelinePinUpsertRequest(StrictModel):
    """Idempotent add guarded by the pin-set revision returned by GET."""

    expected_revision: int = Field(ge=0)
    target: TimelinePinTarget


class TimelinePinMutation(StrictModel):
    """PUT/DELETE result; absent DELETE is intentionally idempotent and does not change revision."""

    action: Literal["added", "unchanged", "deleted", "absent"]
    pin_set: TimelinePinSet


class TimelineQuery(StrictModel):
    """A requested timeline identity; freshness is derived by the gateway, not selected by clients."""

    scopes: tuple[ClusterScope, ...] = Field(min_length=1, max_length=100)
    window: TimelineWindow
    filters: TimelineFilters = Field(default_factory=TimelineFilters)
    mode: TimelineReadMode
    grouping: TimelineGrouping = "app"
    sort: TimelineSort = "importance"
    # Presentation selections travel with the query so every server endpoint
    # can validate them against the same capability descriptor.  They are not
    # part of replay identity because they never alter durable evidence.
    view: TimelineView = "swimlane"
    range_id: TimelineRangeId = "custom"
    lens_zoom_rung: TimelineLensZoomRung = "1h"

    @model_validator(mode="after")
    def canonicalize_scopes(self) -> TimelineQuery:
        workspace_ids = {scope.workspace_id for scope in self.scopes}
        if len(workspace_ids) != 1:
            raise ValueError("timeline scopes must use same workspace")
        by_key: dict[tuple[str, str, tuple[str, ...]], ClusterScope] = {}
        for scope in self.scopes:
            key = (scope.workspace_id, scope.cluster_id, scope.namespaces)
            # ``ClusterScope`` remains wire-compatible with common scope inputs,
            # but collection freshness is evidence output.  Gateway adapters
            # replace this deterministic placeholder with server-observed state.
            by_key[key] = scope.model_copy(update={"freshness": "live"})
        self.scopes = tuple(
            by_key[key]
            for key in sorted(
                by_key,
                key=lambda item: (item[1], item[2]),
            )
        )
        return self


class TimelineReconnectPolicy(StrictModel):
    """Server-owned retry budget for one durable Timeline subscription.

    A browser may retry an interrupted SSE response only with this policy.  The
    cursor itself remains opaque, and a client never supplies a refresh or
    reconnect cadence of its own.
    """

    min_delay_ms: int = Field(ge=100, le=60_000)
    max_delay_ms: int = Field(ge=100, le=300_000)
    strategy: Literal["full_jitter_exponential"]

    @model_validator(mode="after")
    def validate_bounds(self) -> TimelineReconnectPolicy:
        if self.min_delay_ms > self.max_delay_ms:
            raise ValueError("timeline reconnect minimum must not exceed maximum")
        return self


class TimelineLiveSessionPolicy(StrictModel):
    """Server-owned maximum lifetime for one moving live-window session.

    A live query must periodically replace both its bounded snapshot window and
    opaque cursor together.  This is not browser polling: the server declares
    when the current session is no longer authoritative for a moving window.
    """

    max_age_ms: int = Field(ge=1_000, le=300_000)
    strategy: Literal["replace_with_snapshot"]


class RealtimePolicy(StrictModel):
    """Server-negotiated limits; clients must not invent refresh budgets."""

    max_batch_events: int = Field(ge=1, le=10_000)
    max_frames_per_second: int = Field(ge=1, le=60)
    retention_seconds: int = Field(ge=1, le=31_536_000)
    resume: Literal["cursor"]
    hidden_tab: Literal["coalesce"]
    reconnect: TimelineReconnectPolicy
    live_session: TimelineLiveSessionPolicy


class TimelineCursor(StrictModel):
    """Opaque, authorization-bound resume position for one timeline query."""

    token: str = Field(min_length=1, max_length=8_192)

    @field_validator("token")
    @classmethod
    def reject_non_opaque_token(cls, token: str) -> str:
        if token != token.strip() or any(character.isspace() for character in token):
            raise ValueError("timeline cursor token must be opaque")
        return token


class TimelineCoverage(StrictModel):
    scope: ClusterScope
    source: TimelineSource
    from_ms: int = Field(ge=0)
    to_ms: int = Field(gt=0)
    reason: Literal["collection_gap", "retention_boundary", "partial_scope"]

    @model_validator(mode="after")
    def validate_bounds(self) -> TimelineCoverage:
        if self.from_ms >= self.to_ms:
            raise ValueError("timeline coverage must have positive width")
        return self


class TimelineControlOption(StrictModel):
    """One stable, server-owned option rendered by a Timeline control."""

    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=500)


def _require_unique_ids(name: str, options: tuple[TimelineControlOption, ...]) -> None:
    if len({option.id for option in options}) != len(options):
        raise ValueError(f"timeline {name} must use unique IDs")


class TimelineActivityControlOption(TimelineControlOption):
    """One source chip and its exact normal/problem evidence selections."""

    activity: tuple[TimelineActivity, ...] = ()
    problems_activity: tuple[TimelineActivity, ...] = ()

    @field_validator("activity", "problems_activity")
    @classmethod
    def canonicalize_activity_selection(
        cls, activity: tuple[TimelineActivity, ...]
    ) -> tuple[TimelineActivity, ...]:
        return tuple(sorted(set(activity)))


class TimelineRangePreset(TimelineControlOption):
    """A live, relative retained-history window selected by its stable ID."""

    duration_ms: int = Field(ge=1_000)


class TimelineLensZoomOption(TimelineControlOption):
    """A named retained-strip lens width; UI derives the actual interval."""

    duration_ms: int = Field(ge=1_000)


class TimelineBooleanControl(StrictModel):
    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    default: bool


class TimelineFacetControl(StrictModel):
    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    selection: Literal["multi"]
    empty_selection: Literal["all"]


class TimelinePinsControl(StrictModel):
    """Pins are available only through the authenticated persistent pin-set API."""

    key: Literal["pins"] = "pins"
    label: str = Field(min_length=1, max_length=200)
    availability: Literal["available", "unavailable"]
    storage: Literal["server"] | None = None
    revision: Literal["pin_set"] | None = None
    subject_kinds: tuple[Literal["resource", "application"], ...] = ()

    @model_validator(mode="after")
    def validate_persistent_pin_capability(self) -> TimelinePinsControl:
        if self.availability == "available":
            if (
                self.storage != "server"
                or self.revision != "pin_set"
                or self.subject_kinds != ("resource", "application")
            ):
                raise ValueError("available timeline pins require the persistent pin-set contract")
        elif self.storage is not None or self.revision is not None or self.subject_kinds:
            raise ValueError("unavailable timeline pins cannot advertise a storage contract")
        return self


class TimelineLegendControl(StrictModel):
    key: Literal["legend"] = "legend"
    label: str = Field(min_length=1, max_length=200)
    availability: Literal["available"]
    items: tuple[TimelineControlOption, ...] = Field(min_length=1)


class TimelineControlSurface(StrictModel):
    """Complete retained Timeline toolbar/strip contract owned by the server.

    IDs and labels are transport data.  Browser consumers discover this shape
    before rendering controls and therefore never need UI-local fallback
    vocabularies for the retained Timeline surface.
    """

    views: tuple[TimelineControlOption, ...] = Field(min_length=1)
    groupings: tuple[TimelineControlOption, ...] = Field(min_length=1)
    sorts: tuple[TimelineControlOption, ...] = Field(min_length=1)
    activity: tuple[TimelineActivityControlOption, ...] = Field(min_length=1)
    deleted: TimelineBooleanControl
    kinds: TimelineFacetControl
    time_ranges: tuple[TimelineRangePreset, ...] = Field(min_length=1)
    default_time_range_id: TimelineRangeId
    custom_time_range_id: Literal["custom"] = "custom"
    lens_zoom_rungs: tuple[TimelineLensZoomOption, ...] = Field(min_length=1)
    default_lens_zoom_rung: TimelineLensZoomRung
    legend: TimelineLegendControl
    pins: TimelinePinsControl

    @model_validator(mode="after")
    def validate_control_ids(self) -> TimelineControlSurface:
        _require_unique_ids("views", self.views)
        _require_unique_ids("groupings", self.groupings)
        _require_unique_ids("sorts", self.sorts)
        _require_unique_ids("activity", self.activity)
        _require_unique_ids("time ranges", self.time_ranges)
        _require_unique_ids("lens zoom rungs", self.lens_zoom_rungs)
        if self.default_time_range_id == "custom" or self.default_time_range_id not in {
            option.id for option in self.time_ranges
        }:
            raise ValueError("timeline default time range must be an available preset")
        if self.default_lens_zoom_rung not in {option.id for option in self.lens_zoom_rungs}:
            raise ValueError("timeline default lens zoom rung must be available")
        return self


class TimelineQueryBounds(StrictModel):
    """One server-observed strip boundary, not a claim of source coverage.

    ``earliest_queryable_ms`` comes from configured retained-history policy.
    It prevents a browser clock from constructing a silently empty range, but
    it does not mean every source collected continuous evidence in that span.
    The separate ``coverage`` contract remains the only representation of
    observed gaps and unavailable sources.
    """

    server_now_ms: int = Field(ge=0)
    earliest_queryable_ms: int = Field(ge=0)
    max_window_ms: int = Field(ge=1_000)

    @model_validator(mode="after")
    def validate_retained_boundary(self) -> TimelineQueryBounds:
        if self.earliest_queryable_ms > self.server_now_ms:
            raise ValueError("timeline earliest queryable bound must not exceed server time")
        return self


class TimelineCapabilityDescriptor(StrictModel):
    """Server-owned Timeline source and scope constraints for a read session.

    This descriptor is informational only: it never grants a source, cluster,
    or namespace.  The Timeline service continues to authorize every request
    before it creates the descriptor and its cursor binding.
    """

    selected_source_mode: TimelineSourceMode
    available_source_modes: tuple[TimelineSourceMode, ...] = Field(min_length=1)
    max_retained_range_ms: int = Field(ge=1_000)
    query_bounds: TimelineQueryBounds
    namespace_filter_policy: TimelineNamespaceFilterPolicy
    control_surface: TimelineControlSurface

    @field_validator("available_source_modes")
    @classmethod
    def require_unique_source_modes(
        cls, source_modes: tuple[TimelineSourceMode, ...]
    ) -> tuple[TimelineSourceMode, ...]:
        if len(source_modes) != len(set(source_modes)):
            raise ValueError("timeline source modes must be unique")
        return source_modes

    @model_validator(mode="after")
    def require_selected_source_mode_to_be_available(self) -> TimelineCapabilityDescriptor:
        if self.selected_source_mode not in self.available_source_modes:
            raise ValueError("selected source mode must be available")
        return self


class TimelineOverviewBucket(StrictModel):
    """One server-selected, half-open retained-history aggregate bucket."""

    from_ms: int = Field(ge=0)
    to_ms: int = Field(gt=0)
    event_count: int = Field(ge=0)
    problem_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> TimelineOverviewBucket:
        if self.from_ms >= self.to_ms:
            raise ValueError("timeline overview bucket must have positive width")
        if self.problem_count > self.event_count:
            raise ValueError("timeline overview problem count cannot exceed event count")
        return self


class TimelineOverviewActivityFacet(StrictModel):
    activity: TimelineActivity
    count: int = Field(ge=0)


class TimelineOverviewKindFacet(StrictModel):
    kind: str = Field(min_length=1, max_length=253)
    count: int = Field(ge=0)


class TimelineOverviewFacets(StrictModel):
    """Axes are computed independently, never by filtering the visible rows twice."""

    activity: tuple[TimelineOverviewActivityFacet, ...]
    kinds: tuple[TimelineOverviewKindFacet, ...]


class TimelineCoverageSourceAvailability(StrictModel):
    """Absence of a gap is not a synthetic claim that another source was covered."""

    source: TimelineSource
    availability: Literal["observed", "unavailable"]


class TimelineOverview(StrictModel):
    """Safe retained-strip aggregate.  It contains no raw ledger event payloads."""

    window: TimelineWindow
    query_bounds: TimelineQueryBounds
    bucket_width_ms: int = Field(ge=1_000)
    buckets: tuple[TimelineOverviewBucket, ...] = Field(min_length=1, max_length=256)
    coverage: tuple[TimelineCoverage, ...] = ()
    coverage_sources: tuple[TimelineCoverageSourceAvailability, ...] = Field(min_length=1)
    facets: TimelineOverviewFacets
    new_evidence_count: int | None = Field(default=None, ge=0)
    pin_set_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_bucket_window(self) -> TimelineOverview:
        if self.buckets[0].from_ms != self.window.from_ms:
            raise ValueError("timeline overview buckets must start at the requested window")
        if self.buckets[-1].to_ms != self.window.to_ms:
            raise ValueError("timeline overview buckets must end at the requested window")
        for previous, current in zip(self.buckets, self.buckets[1:], strict=False):
            if previous.to_ms != current.from_ms:
                raise ValueError("timeline overview buckets must be contiguous")
        if len({item.source for item in self.coverage_sources}) != len(self.coverage_sources):
            raise ValueError("timeline coverage source availability must be unique")
        return self


class TimelineResourceSubject(StrictModel):
    """A subject backed by an inventory record that has a real Kubernetes UID."""

    kind: Literal["resource"] = "resource"
    resource: ResourceRef


class TimelineInventoryLocatorSubject(StrictModel):
    """Inventory evidence without a UID; it must not masquerade as a ResourceRef."""

    kind: Literal["inventory_locator"] = "inventory_locator"
    inventory_key: str = Field(min_length=1, max_length=512)
    api_group: str = ""
    version: str = ""
    resource_kind: str = Field(min_length=1, max_length=253)
    namespace: str | None = Field(default=None, max_length=253)
    name: str = Field(min_length=1, max_length=253)


class TimelineIncidentSubject(StrictModel):
    """An RCA incident can optionally relate to a resource, but is never one itself."""

    kind: Literal["incident"] = "incident"
    incident_id: str = Field(min_length=1, max_length=512)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=512)


class TimelineApplicationWorkflowSubject(StrictModel):
    """A deployment workflow identity independent from an inventory resource UID."""

    kind: Literal["application_workflow"] = "application_workflow"
    application_id: str = Field(min_length=1, max_length=512)
    binding_id: str = Field(min_length=1, max_length=512)
    workflow_run_id: str = Field(min_length=1, max_length=512)


TimelineSubject = Annotated[
    TimelineResourceSubject
    | TimelineInventoryLocatorSubject
    | TimelineIncidentSubject
    | TimelineApplicationWorkflowSubject,
    Field(discriminator="kind"),
]


class TimelineEvent(StrictModel):
    event_id: str = Field(min_length=1, max_length=512)
    source: TimelineSource
    source_key: str = Field(min_length=1, max_length=1_024)
    native_id: str = Field(min_length=1, max_length=1_024)
    activity: TimelineActivity
    occurred_at: datetime
    scope: ClusterScope
    subject: TimelineSubject
    resource: ResourceRef | None = None
    event_type: TimelineEventType
    severity: TimelineSeverity
    title: str = Field(min_length=1, max_length=1_000)
    owner: ResourceRef | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_exact_resource_relation(self) -> TimelineEvent:
        if self.source in {"application_workflow", "gitops"} and not isinstance(
            self.subject, TimelineApplicationWorkflowSubject
        ):
            raise ValueError("application timeline source requires an application workflow subject")
        if isinstance(self.subject, TimelineResourceSubject):
            if self.resource is None:
                raise ValueError("resource subject requires an exact resource relation")
            if self.resource != self.subject.resource:
                raise ValueError("resource subject relation must match its exact resource")
        if isinstance(self.subject, TimelineInventoryLocatorSubject) and self.resource is not None:
            raise ValueError("uid-less inventory subject cannot carry a resource relation")
        return self


class TimelineStreamFrame(StrictModel):
    """One immutable record in NDJSON or SSE replay order."""

    kind: TimelineFrameKind
    cursor: TimelineCursor
    scopes: tuple[ClusterScope, ...] = ()
    policy: RealtimePolicy | None = None
    capabilities: TimelineCapabilityDescriptor | None = None
    pin_set_revision: int | None = Field(default=None, ge=0)
    event: TimelineEvent | None = None
    events: tuple[TimelineEvent, ...] = ()
    coverage: tuple[TimelineCoverage, ...] = ()
    truncated: bool = False
    event_limit: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_shape(self) -> TimelineStreamFrame:
        if self.kind == "snapshot":
            if not self.scopes:
                raise ValueError("snapshot frame requires scopes")
            if self.policy is None:
                raise ValueError("snapshot frame requires policy")
            if self.capabilities is None:
                raise ValueError("snapshot frame requires capabilities")
            if self.event is not None or self.reason is not None:
                raise ValueError(
                    "snapshot frame may only carry scopes, policy, capabilities, events, coverage, and bounds"
                )
            if self.truncated != (self.event_limit is not None):
                raise ValueError("snapshot frame truncation requires its negotiated event limit")
            return self
        if self.kind == "event":
            if self.event is None:
                raise ValueError("event frame requires event")
            if (
                self.scopes
                or self.policy is not None
                or self.capabilities is not None
                or self.events
                or self.coverage
                or self.truncated
                or self.event_limit is not None
                or self.reason is not None
                or self.pin_set_revision is not None
            ):
                raise ValueError("event frame may only carry one event")
            return self
        if self.kind == "coverage":
            if (
                not self.coverage
                or self.scopes
                or self.policy is not None
                or self.capabilities is not None
                or self.event is not None
                or self.events
                or self.truncated
                or self.event_limit is not None
                or self.reason is not None
                or self.pin_set_revision is not None
            ):
                raise ValueError("coverage frame requires coverage only")
            return self
        if self.kind == "resync_required":
            if (
                not self.reason
                or self.scopes
                or self.policy is not None
                or self.capabilities is not None
                or self.event is not None
                or self.events
                or self.coverage
                or self.truncated
                or self.event_limit is not None
                or self.pin_set_revision is not None
            ):
                raise ValueError("resync frame requires reason only")
            return self
        if (
            self.scopes
            or self.policy is not None
            or self.capabilities is not None
            or self.event is not None
            or self.events
            or self.coverage
            or self.truncated
            or self.event_limit is not None
            or self.pin_set_revision is not None
        ):
            raise ValueError("terminal frame must not carry timeline records")
        if self.kind == "error" and not self.reason:
            raise ValueError("error frame requires reason")
        if self.kind == "end" and self.reason is not None:
            raise ValueError("terminal frame must not carry reason")
        return self

    @property
    def is_terminal(self) -> bool:
        return self.kind in {"end", "error", "resync_required"}
