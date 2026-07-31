from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable
from pathlib import Path
from typing import Any

from packages.config.logs import get_logger
from packages.config.settings import env
from packages.runtime.async_db import AsyncDb
from packages.runtime.service import AsyncService
from packages.runtime.worker import HEARTBEAT_PATH
from packages.storage.database import Database, wait_for_database

RCA_TIMELINE_JANITOR = "rca-timeline-janitor"
SWEEP_INTERVAL_SECONDS_ENV = "RCA_TIMELINE_JANITOR_INTERVAL_SECONDS"
EXPIRE_DAYS_ENV = "RCA_OPEN_INCIDENT_EXPIRE_DAYS"
EXPIRE_LIMIT_ENV = "RCA_OPEN_INCIDENT_EXPIRE_LIMIT"
PRE_INCIDENT_RETENTION_HOURS_ENV = "RCA_PRE_INCIDENT_RETENTION_HOURS"
PRE_INCIDENT_RETENTION_LIMIT_ENV = "RCA_PRE_INCIDENT_RETENTION_LIMIT"
EPHEMERAL_RESOLVE_MINUTES_ENV = "RCA_EPHEMERAL_INCIDENT_RESOLVE_MINUTES"
EPHEMERAL_RESOLVE_LIMIT_ENV = "RCA_EPHEMERAL_INCIDENT_RESOLVE_LIMIT"
RECOVERY_VERIFICATION_EXPIRE_LIMIT_ENV = "RECOVERY_VERIFICATION_EXPIRE_LIMIT"
DEFAULT_SWEEP_INTERVAL_SECONDS = "60"
DEFAULT_EXPIRE_DAYS = "3"
DEFAULT_EXPIRE_LIMIT = "500"
DEFAULT_PRE_INCIDENT_RETENTION_HOURS = "24"
DEFAULT_PRE_INCIDENT_RETENTION_LIMIT = "1000"
DEFAULT_EPHEMERAL_RESOLVE_MINUTES = "5"
DEFAULT_EPHEMERAL_RESOLVE_LIMIT = "500"
DEFAULT_RECOVERY_VERIFICATION_EXPIRE_LIMIT = "100"
HEARTBEAT_REFRESH_SECONDS = 30.0
LOGGER = get_logger(__name__)


async def expire_stale_open_incidents(db: Any) -> int:
    expired = await db.expire_stale_open_rca_incidents(
        max_age_days=int(env(EXPIRE_DAYS_ENV, DEFAULT_EXPIRE_DAYS)),
        limit=int(env(EXPIRE_LIMIT_ENV, DEFAULT_EXPIRE_LIMIT)),
    )
    return len(expired or [])


async def delete_stale_pre_incident_timeline(db: Any) -> int:
    return int(
        await db.delete_stale_pre_incident_timeline(
            retention_hours=int(
                env(PRE_INCIDENT_RETENTION_HOURS_ENV, DEFAULT_PRE_INCIDENT_RETENTION_HOURS)
            ),
            limit=int(env(PRE_INCIDENT_RETENTION_LIMIT_ENV, DEFAULT_PRE_INCIDENT_RETENTION_LIMIT)),
        )
    )


async def resolve_recovered_ephemeral_incidents(db: Any) -> int:
    resolved = await db.resolve_recovered_ephemeral_incidents(
        grace_minutes=int(env(EPHEMERAL_RESOLVE_MINUTES_ENV, DEFAULT_EPHEMERAL_RESOLVE_MINUTES)),
        limit=int(env(EPHEMERAL_RESOLVE_LIMIT_ENV, DEFAULT_EPHEMERAL_RESOLVE_LIMIT)),
    )
    rows = resolved or []
    for row in rows:
        workspace_id = str(row.get("workspace_id") or "").strip()
        incident_id = str(row.get("incident_id") or "").strip()
        if not workspace_id or not incident_id:
            continue
        await db.resolve_incident_alert_events(workspace_id, incident_id)
    return len(rows)


async def expire_recovery_verifications(
    db: Any,
    *,
    now: object | None = None,
) -> int:
    expired = await db.expire_recovery_verifications(
        now=now,
        limit=int(
            env(
                RECOVERY_VERIFICATION_EXPIRE_LIMIT_ENV,
                DEFAULT_RECOVERY_VERIFICATION_EXPIRE_LIMIT,
            )
        ),
    )
    return len(expired or [])


def touch_heartbeat() -> None:
    Path(HEARTBEAT_PATH).touch()


async def wait_for_next_sweep(
    stopping: asyncio.Event,
    timeout: float,
    *,
    heartbeat_interval: float = HEARTBEAT_REFRESH_SECONDS,
    touch: Callable[[], None] = touch_heartbeat,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(timeout, 0.0)
    refresh_interval = heartbeat_interval if heartbeat_interval > 0 else HEARTBEAT_REFRESH_SECONDS
    while not stopping.is_set():
        touch()
        remaining = deadline - loop.time()
        if remaining <= 0:
            return
        try:
            await asyncio.wait_for(stopping.wait(), timeout=min(refresh_interval, remaining))
        except TimeoutError:
            continue


async def run() -> None:
    db = Database()
    async_db = AsyncDb(db)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for item in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(item, stopping.set)

    touch_heartbeat()
    await wait_for_database(db)
    interval = float(env(SWEEP_INTERVAL_SECONDS_ENV, DEFAULT_SWEEP_INTERVAL_SECONDS))
    try:
        while not stopping.is_set():
            touch_heartbeat()
            count = await expire_stale_open_incidents(async_db)
            if count:
                LOGGER.warning("stale_open_incidents_expired", extra={"context": {"count": count}})
            verification_expired = await expire_recovery_verifications(async_db)
            if verification_expired:
                LOGGER.warning(
                    "recovery_verifications_expired",
                    extra={"context": {"count": verification_expired}},
                )
            resolved = await resolve_recovered_ephemeral_incidents(async_db)
            if resolved:
                LOGGER.info(
                    "recovered_ephemeral_incidents_resolved",
                    extra={"context": {"count": resolved}},
                )
            deleted = await delete_stale_pre_incident_timeline(async_db)
            if deleted:
                LOGGER.info(
                    "stale_pre_incident_timeline_deleted",
                    extra={"context": {"count": deleted}},
                )
            await wait_for_next_sweep(stopping, interval)
    finally:
        dispose = getattr(db, "dispose", None)
        if dispose is not None:
            dispose()


if __name__ == "__main__":
    AsyncService(RCA_TIMELINE_JANITOR, run).run()
