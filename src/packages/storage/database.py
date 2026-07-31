"""Database 공개 진입점(하위호환) — 합성은 domains/registry 가 담당.

엔진·트랜잭션·헬퍼는 engine.py, 도메인 repo 합성은 domains/registry.py(도메인 zone).
여기서는 그 Database 와 테스트·하위호환용 모듈 헬퍼를 재노출함.
"""

from __future__ import annotations

import asyncio

from domains.registry import Database as Database
from packages.config.environments import is_protected_runtime_environment
from packages.config.retry import retry_dependency
from packages.config.settings import env
from packages.contracts.interfaces import InitializableStore
from packages.storage.engine import (
    ERROR_MESSAGE_LIMIT,
    compact_error,
    iso_or_none,
    row_dict,
    serialize_command,
    serialize_dead_letter,
)

__all__ = [
    "DATABASE_STARTUP_MODE_ENV",
    "ERROR_MESSAGE_LIMIT",
    "Database",
    "compact_error",
    "iso_or_none",
    "row_dict",
    "serialize_command",
    "serialize_dead_letter",
    "wait_for_database",
]

DATABASE_STARTUP_MODE_ENV = "DATABASE_STARTUP_MODE"
DATABASE_STARTUP_INITIALIZE = "initialize"
DATABASE_STARTUP_VERIFY = "verify"


def database_startup_mode() -> str:
    """DB 시작 모드 — 운영 계열은 읽기 전용 schema 검증이 기본이다."""
    app_env = env("APP_ENV", "").strip().lower()
    default = (
        DATABASE_STARTUP_VERIFY
        if is_protected_runtime_environment(app_env)
        else DATABASE_STARTUP_INITIALIZE
    )
    mode = env(DATABASE_STARTUP_MODE_ENV, default).strip().lower()
    if mode not in {DATABASE_STARTUP_INITIALIZE, DATABASE_STARTUP_VERIFY}:
        raise ValueError(
            f"{DATABASE_STARTUP_MODE_ENV} must be "
            f"{DATABASE_STARTUP_INITIALIZE!r} or {DATABASE_STARTUP_VERIFY!r}"
        )
    return mode


async def wait_for_database(db: InitializableStore) -> None:
    mode = database_startup_mode()

    async def attempt() -> None:
        if mode == DATABASE_STARTUP_VERIFY:
            await asyncio.to_thread(db.verify_schema)
            return
        await asyncio.to_thread(db.init)

    await retry_dependency(attempt, label="postgres")
