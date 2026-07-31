"""HTTP input contracts for timeline reads.

The browser submits one complete, bounded query.  The gateway replaces its
scope freshness with observed server state before it becomes a ledger read
boundary, so a client cannot claim that a disconnected cluster is live.
"""

from __future__ import annotations

from packages.contracts.gateway.base import StrictModel
from packages.contracts.timeline.models import TimelineCursor, TimelineQuery


class TimelineSnapshotRequest(StrictModel):
    """Request an immutable retained-history snapshot for one authorized query."""

    query: TimelineQuery


class TimelineStreamRequest(StrictModel):
    """Resume an already-snapshotted query through a long-lived SSE response."""

    query: TimelineQuery
    after: TimelineCursor | None = None


class TimelineOverviewRequest(StrictModel):
    """Read a bounded aggregate for the server-owned retained Timeline strip."""

    query: TimelineQuery
