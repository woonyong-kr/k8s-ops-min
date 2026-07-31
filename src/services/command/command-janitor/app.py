from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from domains.command.repository import QUEUED_COMMAND_TTL_SECONDS
from domains.target.router import emit_evidence_if_ready
from packages.config.logs import get_logger
from packages.config.settings import env
from packages.runtime.async_db import AsyncDb
from packages.runtime.service import AsyncService
from packages.runtime.worker import HEARTBEAT_PATH
from packages.storage.database import Database, wait_for_database
from packages.storage.retention import RetentionSweepResult, sweep_storage_retention

COMMAND_JANITOR = "command-janitor"
SWEEP_INTERVAL_SECONDS_ENV = "COMMAND_JANITOR_INTERVAL_SECONDS"
QUEUE_TTL_SECONDS_ENV = "COMMAND_QUEUE_TTL_SECONDS"
RETENTION_SWEEP_INTERVAL_SECONDS_ENV = "DB_RETENTION_SWEEP_INTERVAL_SECONDS"
DEFAULT_SWEEP_INTERVAL_SECONDS = "15"
DEFAULT_RETENTION_SWEEP_INTERVAL_SECONDS = "3600"
LOGGER = get_logger(__name__)


async def emit_expired_command_completions(db: Any, service_name: str = COMMAND_JANITOR) -> int:
    """만료 명령을 종결한다 — CommandCompleted 는 같은 트랜잭션에서 outbox 에 적재됨.

    이전에는 상태 커밋 후 NATS 로 직접 발행해, 커밋과 발행 사이 크래시가
    완료 이벤트를 영구 유실시켰다(명령은 이미 terminal 이라 재발견 불가).
    발행은 outbox relay 가 담당하므로 janitor 는 NATS 의존이 없다.
    """
    try:
        queue_ttl_seconds = int(env(QUEUE_TTL_SECONDS_ENV, str(QUEUED_COMMAND_TTL_SECONDS)))
        expired = (
            await db.fail_expired_agent_commands(
                queue_ttl_seconds=queue_ttl_seconds, source=service_name
            )
            or []
        )
    except Exception:
        # rollout 시 schema lock 같은 일시 DB 경합은 다음 주기에 재시도한다.
        LOGGER.exception("expired_command_sweep_failed")
        return 0
    return len(expired)


async def sweep_exhausted_evidence_jobs(async_db: Any, raw_db: Any) -> int:
    """attempt 소진 + lease 만료 evidence 잡을 종결하고 window 집계를 트리거한다.

    lease 상한 도입으로 소진된 잡은 재임대가 불가능해졌으므로, janitor 가
    FAILED 로 닫아야 window 가 failure_policy 에 따라 발행/보류로 진행된다.
    emit_evidence_if_ready 는 outbox 스테이징(DB-only)이라 NATS 연결이 필요
    없다 — events 인자는 source 문자열로만 쓰인다.
    """
    try:
        keys = await async_db.fail_exhausted_evidence_jobs()
    except Exception:
        LOGGER.exception("exhausted_evidence_sweep_failed")
        return 0
    source = SimpleNamespace(source=COMMAND_JANITOR)
    for key in keys:
        try:
            await emit_evidence_if_ready(key, source, raw_db)
        except Exception:
            LOGGER.exception(
                "evidence_emit_after_sweep_failed",
                extra={"context": {"evidence_key": key}},
            )
    return len(keys)


async def sweep_database_retention(db: Any) -> RetentionSweepResult | None:
    try:
        result = await sweep_storage_retention(db)
    except Exception:
        LOGGER.exception("database_retention_sweep_failed")
        return None
    return result


async def log_outbox_backlog(db: Any) -> None:
    """미발행 outbox 적체를 주기 관측한다 — relay 가 죽으면 로그로 드러난다.

    outbox 는 relay 가 발행해야 비워지는데, relay 장애는 발행 지연이라는
    침묵 증상만 남긴다. pending 건수와 최고령 행 나이를 retention 주기마다
    남겨 적체를 조기에 발견 가능하게 한다.
    """
    try:
        pending = int(await db.outbox_pending_count() or 0)
        oldest_age = float(await db.outbox_oldest_age_seconds() or 0)
    except Exception:
        LOGGER.exception("outbox_backlog_probe_failed")
        return
    if pending:
        LOGGER.warning(
            "outbox_backlog",
            extra={
                "context": {
                    "pending": pending,
                    "oldest_age_seconds": int(oldest_age),
                }
            },
        )


async def run() -> None:
    # CommandCompleted 는 fail_expired_agent_commands 트랜잭션이 outbox 에 적재하고
    # relay 가 발행한다 — janitor 는 더 이상 NATS 에 연결하지 않는다(발행 예외로
    # janitor 루프가 죽는 장애 모드도 함께 제거).
    db = Database()
    async_db = AsyncDb(db)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for item in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(item, stopping.set)

    await wait_for_database(db)
    interval = float(env(SWEEP_INTERVAL_SECONDS_ENV, DEFAULT_SWEEP_INTERVAL_SECONDS))
    retention_interval = float(
        env(RETENTION_SWEEP_INTERVAL_SECONDS_ENV, DEFAULT_RETENTION_SWEEP_INTERVAL_SECONDS)
    )
    next_retention_sweep = 0.0
    try:
        while not stopping.is_set():
            Path(HEARTBEAT_PATH).touch()
            count = await emit_expired_command_completions(async_db)
            if count:
                LOGGER.warning("expired_commands_swept", extra={"context": {"count": count}})
            evidence_count = await sweep_exhausted_evidence_jobs(async_db, db)
            if evidence_count:
                LOGGER.warning(
                    "exhausted_evidence_jobs_swept",
                    extra={"context": {"count": evidence_count}},
                )
            loop_time = loop.time()
            if loop_time >= next_retention_sweep:
                retention_result = await sweep_database_retention(async_db)
                next_retention_sweep = loop_time + retention_interval
                await log_outbox_backlog(async_db)
                if retention_result is not None and retention_result.errors:
                    # 부분 실패 관측 — 실패 단계 이름을 남겨 침묵 마비를 조기에 드러낸다.
                    LOGGER.error(
                        "database_retention_steps_failed",
                        extra={"context": {"steps": list(retention_result.errors)}},
                    )
                if retention_result is not None and retention_result.total:
                    LOGGER.warning(
                        "database_retention_swept",
                        extra={"context": retention_result.metrics()},
                    )
            try:
                await asyncio.wait_for(stopping.wait(), timeout=interval)
            except TimeoutError:
                continue
    finally:
        dispose = getattr(db, "dispose", None)
        if dispose is not None:
            dispose()


if __name__ == "__main__":
    AsyncService(COMMAND_JANITOR, run).run()
