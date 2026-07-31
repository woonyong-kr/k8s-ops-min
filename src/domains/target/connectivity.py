"""Authoritative cluster-agent liveness semantics shared by read surfaces."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from packages.config.settings import env

AGENT_ONLINE_WINDOW_SECONDS_ENV = "AGENT_ONLINE_WINDOW_SECONDS"
DEFAULT_AGENT_ONLINE_WINDOW_SECONDS = 120
AGENT_STATUS_NEVER_CONNECTED = "never_connected"
AGENT_STATUS_ONLINE = "online"
AGENT_STATUS_STALE = "stale"


def agent_online_window_seconds() -> int:
    """Return the configured liveness window without accepting a zero interval."""
    return max(
        1,
        int(env(AGENT_ONLINE_WINDOW_SECONDS_ENV, str(DEFAULT_AGENT_ONLINE_WINDOW_SECONDS))),
    )


def parse_timestamp(value: object) -> datetime | None:
    """Parse persisted ISO timestamps while treating malformed values as stale."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def cluster_connection_status(agent: Mapping[str, Any] | None) -> str:
    """Classify the latest persisted agent heartbeat at request time."""
    if agent is None:
        return AGENT_STATUS_NEVER_CONNECTED
    last_seen_at = parse_timestamp(agent.get("last_seen_at"))
    if last_seen_at is None:
        return AGENT_STATUS_STALE
    online_window = timedelta(seconds=agent_online_window_seconds())
    return (
        AGENT_STATUS_ONLINE
        if datetime.now(UTC) - last_seen_at <= online_window
        else AGENT_STATUS_STALE
    )
