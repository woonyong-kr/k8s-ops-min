from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Literal

from packages.contracts.event_bus.interfaces import EventBus
from packages.events.bus import NATS_URL_ENV, NatsEventBus
from packages.events.in_memory import InMemoryEventBus

Mode = Literal["inprocess", "nats"]


@dataclass(frozen=True)
class BenchmarkConfig:
    events: int = 1_000
    rounds: int = 5
    payload_bytes: int = 1_024
    timeout_seconds: float = 10.0

    def validate(self) -> None:
        if self.events < 1:
            raise ValueError("events must be positive")
        if self.rounds < 1:
            raise ValueError("rounds must be positive")
        if self.payload_bytes < 0:
            raise ValueError("payload_bytes must be non-negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True)
class RoundResult:
    publish_ms: float
    consume_ms: float
    total_ms: float


@dataclass(frozen=True)
class BenchmarkSummary:
    mode: Mode
    events_per_round: int
    measured_rounds: int
    payload_blob_bytes: int
    serialized_event_bytes: int
    median_publish_ms: float
    median_consume_ms: float
    median_total_ms: float
    min_total_ms: float
    max_total_ms: float
    events_per_second: float


def _make_bus(mode: Mode) -> EventBus:
    if mode == "inprocess":
        return InMemoryEventBus()
    return NatsEventBus()


async def _consume(subscription: object, count: int, timeout: float) -> None:
    received = 0
    while received < count:
        # 운영 기본값 NATS_MAX_ACK_PENDING=100을 넘겨 fetch하면, 서버는 100건만
        # 보낸 뒤 클라이언트 batch가 채워지기를 기다리므로 timeout이 측정값에 섞인다.
        batch_size = min(100, count - received)
        messages = await subscription.fetch(batch=batch_size, timeout=timeout)  # type: ignore[attr-defined]
        for message in messages:
            await message.ack()
        received += len(messages)


async def _close_subscription(subscription: object) -> None:
    close = getattr(subscription, "close", None)
    if close is not None:
        await close()
        return
    unsubscribe = getattr(subscription, "unsubscribe", None)
    if unsubscribe is not None:
        await unsubscribe()


async def _run_round(
    bus: EventBus,
    subject: str,
    durable: str,
    config: BenchmarkConfig,
) -> tuple[RoundResult, int]:
    subscription = await bus.subscribe(subject, durable)
    payload = {"blob": "x" * config.payload_bytes, "sequence": 0}

    started = time.perf_counter()
    sample_size = 0
    for sequence in range(config.events):
        payload["sequence"] = sequence
        event = await bus.emit(subject, "transport-benchmark", payload)
        if sequence == 0:
            sample_size = len(json.dumps(event.to_dict()).encode())
    published = time.perf_counter()

    await _consume(subscription, config.events, config.timeout_seconds)
    consumed = time.perf_counter()
    await _close_subscription(subscription)

    return (
        RoundResult(
            publish_ms=(published - started) * 1_000,
            consume_ms=(consumed - published) * 1_000,
            total_ms=(consumed - started) * 1_000,
        ),
        sample_size,
    )


async def run_mode(mode: Mode, config: BenchmarkConfig) -> BenchmarkSummary:
    config.validate()
    bus = _make_bus(mode)
    await bus.connect()
    results: list[RoundResult] = []
    serialized_event_bytes = 0
    run_id = uuid.uuid4().hex

    try:
        # 첫 실행의 연결·stream 생성 비용은 전달 비용과 분리한다.
        await _run_round(
            bus,
            f"audit.benchmark.{run_id}.warmup",
            f"benchmark-{run_id}-warmup",
            config,
        )
        for round_number in range(config.rounds):
            result, serialized_event_bytes = await _run_round(
                bus,
                f"audit.benchmark.{run_id}.round-{round_number}",
                f"benchmark-{run_id}-{round_number}",
                config,
            )
            results.append(result)
    finally:
        await bus.close()

    median_total = statistics.median(result.total_ms for result in results)
    return BenchmarkSummary(
        mode=mode,
        events_per_round=config.events,
        measured_rounds=config.rounds,
        payload_blob_bytes=config.payload_bytes,
        serialized_event_bytes=serialized_event_bytes,
        median_publish_ms=round(statistics.median(r.publish_ms for r in results), 3),
        median_consume_ms=round(statistics.median(r.consume_ms for r in results), 3),
        median_total_ms=round(median_total, 3),
        min_total_ms=round(min(r.total_ms for r in results), 3),
        max_total_ms=round(max(r.total_ms for r in results), 3),
        events_per_second=round(config.events / (median_total / 1_000), 1),
    )


def comparison(inprocess: BenchmarkSummary, nats: BenchmarkSummary) -> dict[str, float]:
    return {
        "batch_time_reduction_percent": round(
            (nats.median_total_ms - inprocess.median_total_ms) / nats.median_total_ms * 100,
            2,
        ),
        "throughput_multiplier": round(
            inprocess.events_per_second / nats.events_per_second,
            2,
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare process-local and NATS event transport")
    parser.add_argument("--mode", choices=("inprocess", "nats", "both"), default="inprocess")
    parser.add_argument("--events", type=int, default=1_000)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--payload-bytes", type=int, default=1_024)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    return parser


async def _main() -> None:
    args = _parser().parse_args()
    config = BenchmarkConfig(
        events=args.events,
        rounds=args.rounds,
        payload_bytes=args.payload_bytes,
        timeout_seconds=args.timeout_seconds,
    )
    modes: tuple[Mode, ...] = ("inprocess", "nats") if args.mode == "both" else (args.mode,)
    if "nats" in modes and not os.getenv(NATS_URL_ENV):
        raise SystemExit("NATS_URL is required for nats mode")

    summaries = [await run_mode(mode, config) for mode in modes]
    for summary in summaries:
        print(json.dumps({"benchmark": asdict(summary)}, ensure_ascii=False))
    if len(summaries) == 2:
        print(json.dumps({"comparison": comparison(summaries[0], summaries[1])}))


if __name__ == "__main__":
    asyncio.run(_main())
