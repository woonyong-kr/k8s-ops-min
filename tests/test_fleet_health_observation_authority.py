from __future__ import annotations

from domains.dashboard.fleet_router import current_workloads_degraded


def test_new_live_usage_supersedes_stale_inventory_degradation() -> None:
    assert (
        current_workloads_degraded(
            {
                "workloads_degraded": 7,
                "last_seen_at": "2026-07-24T19:43:47Z",
            },
            [{"sampled_at": "2026-07-24T21:00:59Z", "usage": {"pod_running": 46}}],
        )
        == 0
    )


def test_fresh_inventory_degradation_remains_critical_input() -> None:
    assert (
        current_workloads_degraded(
            {
                "workloads_degraded": 2,
                "last_seen_at": "2026-07-24T20:59:30Z",
            },
            [{"sampled_at": "2026-07-24T21:00:00Z", "usage": {"pod_running": 46}}],
        )
        == 2
    )


def test_missing_inventory_timestamp_fails_closed() -> None:
    assert (
        current_workloads_degraded(
            {"workloads_degraded": 1},
            [{"sampled_at": "2026-07-24T21:00:00Z", "usage": {"pod_running": 46}}],
        )
        == 1
    )
