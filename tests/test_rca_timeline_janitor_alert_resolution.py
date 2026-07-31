from __future__ import annotations

import asyncio

from conftest import load_service

janitor = load_service("projection/rca-timeline-janitor")


class JanitorDatabase:
    def __init__(self) -> None:
        self.resolved: list[tuple[str, str]] = []

    async def resolve_recovered_ephemeral_incidents(
        self,
        *,
        grace_minutes: int,
        limit: int,
    ) -> list[dict[str, str]]:
        assert grace_minutes > 0
        assert limit > 0
        return [
            {
                "workspace_id": "workspace-1",
                "incident_id": "incident-1",
            },
            {
                "workspace_id": "workspace-1",
                "incident_id": "incident-2",
            },
        ]

    async def resolve_incident_alert_events(
        self,
        workspace_id: str,
        incident_id: str,
    ) -> int:
        self.resolved.append((workspace_id, incident_id))
        return 1


def test_ephemeral_resolution_closes_alerts_in_the_same_sweep() -> None:
    database = JanitorDatabase()

    count = asyncio.run(janitor.resolve_recovered_ephemeral_incidents(database))

    assert count == 2
    assert database.resolved == [
        ("workspace-1", "incident-1"),
        ("workspace-1", "incident-2"),
    ]
