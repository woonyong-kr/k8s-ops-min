from __future__ import annotations

import asyncio

from benchmarks.event_bus_transport import BenchmarkConfig, comparison, run_mode


def test_inprocess_benchmark_reports_measured_contract() -> None:
    summary = asyncio.run(
        run_mode(
            "inprocess",
            BenchmarkConfig(events=20, rounds=2, payload_bytes=32, timeout_seconds=1),
        )
    )

    assert summary.mode == "inprocess"
    assert summary.events_per_round == 20
    assert summary.measured_rounds == 2
    assert summary.serialized_event_bytes > summary.payload_blob_bytes
    assert summary.median_total_ms > 0
    assert summary.events_per_second > 0


def test_comparison_uses_nats_as_baseline() -> None:
    inprocess = asyncio.run(
        run_mode(
            "inprocess",
            BenchmarkConfig(events=5, rounds=1, payload_bytes=0, timeout_seconds=1),
        )
    )
    nats_like = type(inprocess)(
        **{
            **inprocess.__dict__,
            "mode": "nats",
            "median_total_ms": inprocess.median_total_ms * 4,
            "events_per_second": inprocess.events_per_second / 4,
        }
    )

    result = comparison(inprocess, nats_like)

    assert result == {"batch_time_reduction_percent": 75.0, "throughput_multiplier": 4.0}
