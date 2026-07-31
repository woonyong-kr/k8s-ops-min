from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from conftest import load_service

janitor = load_service("projection/rca-timeline-janitor")


class JanitorDb:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def expire_recovery_verifications(
        self,
        *,
        now: object | None,
        limit: int,
    ) -> list[dict[str, str]]:
        self.calls.append({"now": now, "limit": limit})
        return [{"plan_id": "plan-1"}]


def test_janitor_expires_verification_without_waiting_for_new_evidence(
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 24, 1, 10, tzinfo=UTC)
    db = JanitorDb()
    monkeypatch.setenv("RECOVERY_VERIFICATION_EXPIRE_LIMIT", "7")

    count = asyncio.run(janitor.expire_recovery_verifications(db, now=now))

    assert count == 1
    assert db.calls == [{"now": now, "limit": 7}]
