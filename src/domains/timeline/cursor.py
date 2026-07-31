"""Timeline replay cursors built on the shared signed cursor boundary.

The ledger keeps a numeric sequence internally for transactional ordering.  It
is never sent to a browser directly: the public contract only contains the
opaque ``TimelineCursor`` token issued here.
"""

from __future__ import annotations

import hashlib
import json

from domains.inventory_filter.cursor import CursorScope, FilterCursorCodec
from packages.contracts.gateway.base import StrictModel
from packages.contracts.parity import ClusterScope
from packages.contracts.timeline import TimelineCursor, TimelineQuery, TimelineWindow
from packages.contracts.timeline.models import TimelineActivity, TimelineReadMode

TIMELINE_CURSOR_SURFACE = "timeline-replay"
TIMELINE_SEQUENCE_POSITION = "timeline_sequence"


class TimelineEvidenceFilters(StrictModel):
    """The subset of Timeline filters that changes durable evidence membership.

    ``pinned_only`` is server-backed. Its actual membership is held outside
    this public shape, while its pin-set revision is bound below so a resume
    cannot cross a mutation.
    """

    activity: tuple[TimelineActivity, ...] = ()
    kinds: tuple[str, ...] = ()
    include_deleted: bool = True
    pinned_only: bool = False
    search: str = ""


class TimelineReplayIdentity(StrictModel):
    """Canonical server-owned identity for a retained timeline replay.

    The identity deliberately contains only evidence-selection fields. A
    browser may change grouping, ordering, or a not-yet-backed pin preference
    without invalidating the opaque durable cursor or changing its suffix.
    """

    scopes: tuple[ClusterScope, ...]
    window: TimelineWindow
    mode: TimelineReadMode
    filters: TimelineEvidenceFilters
    pin_set_revision: int | None = None

    @classmethod
    def from_query(
        cls,
        query: TimelineQuery,
        *,
        pin_set_revision: int | None = None,
    ) -> TimelineReplayIdentity:
        if query.filters.pinned_only and pin_set_revision is None:
            raise ValueError("pinned timeline replay requires a pin-set revision")
        if pin_set_revision is not None and pin_set_revision < 0:
            raise ValueError("timeline pin-set revision must be non-negative")
        return cls(
            scopes=tuple(scope.model_copy(update={"freshness": "live"}) for scope in query.scopes),
            window=query.window,
            mode=query.mode,
            filters=TimelineEvidenceFilters(
                activity=query.filters.activity,
                kinds=query.filters.kinds,
                include_deleted=query.filters.include_deleted,
                pinned_only=query.filters.pinned_only,
                search=query.filters.query.strip(),
            ),
            pin_set_revision=pin_set_revision if query.filters.pinned_only else None,
        )


class TimelineCursorBinding(StrictModel):
    """Server-resolved authority that must remain unchanged during replay."""

    user_id: str
    authorization_revision: str
    replay_identity: TimelineReplayIdentity
    snapshot_revision: int = 0

    @classmethod
    def from_query(
        cls,
        *,
        user_id: str,
        authorization_revision: str,
        query: TimelineQuery,
        snapshot_revision: int = 0,
        pin_set_revision: int | None = None,
    ) -> TimelineCursorBinding:
        return cls(
            user_id=user_id,
            authorization_revision=authorization_revision,
            replay_identity=TimelineReplayIdentity.from_query(
                query, pin_set_revision=pin_set_revision
            ),
            snapshot_revision=snapshot_revision,
        )

    def as_filter_scope(self) -> CursorScope:
        return CursorScope(
            workspace_id=self.replay_identity.scopes[0].workspace_id,
            user_id=self.user_id,
            authorization_revision=self.authorization_revision,
            surface=TIMELINE_CURSOR_SURFACE,
            filter_fingerprint=timeline_replay_identity_fingerprint(self.replay_identity),
            snapshot_revision=self.snapshot_revision,
            facet_query=None,
        )


class TimelineReplayCursorCodec:
    """Typed facade preventing a caller from exposing a raw ledger sequence."""

    def __init__(self, codec: FilterCursorCodec) -> None:
        self._codec = codec

    def encode(self, binding: TimelineCursorBinding, *, sequence: int) -> TimelineCursor:
        if isinstance(sequence, bool) or sequence < 0:
            raise ValueError("timeline sequence must be non-negative")
        return TimelineCursor(
            token=self._codec.encode(
                binding.as_filter_scope(),
                position={TIMELINE_SEQUENCE_POSITION: sequence},
            )
        )

    def decode(self, cursor: TimelineCursor, *, binding: TimelineCursorBinding) -> int:
        decoded = self._codec.decode(cursor.token, expected=binding.as_filter_scope())
        sequence = decoded.position.get(TIMELINE_SEQUENCE_POSITION)
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("timeline cursor position is invalid")
        return sequence


def timeline_query_fingerprint(query: TimelineQuery) -> str:
    """Compatibility facade for the explicit server replay identity projection."""
    return timeline_replay_identity_fingerprint(TimelineReplayIdentity.from_query(query))


def timeline_replay_identity_fingerprint(identity: TimelineReplayIdentity) -> str:
    """Fingerprint only durable evidence selection, never presentation preferences."""
    payload = {
        "scopes": [
            {
                "workspace_id": scope.workspace_id,
                "cluster_id": scope.cluster_id,
                "namespaces": scope.namespaces,
            }
            for scope in identity.scopes
        ],
        "window": identity.window.model_dump(mode="json"),
        "mode": identity.mode,
        "filters": identity.filters.model_dump(mode="json"),
        "pin_set_revision": identity.pin_set_revision,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
