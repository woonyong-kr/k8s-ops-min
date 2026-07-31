"""Alembic env — 환경변수에서 DB URL 을 읽고 모든 도메인 모델을 등록한 뒤 마이그레이션 실행."""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

# 모든 도메인·인프라 모델을 import 하여 Base.metadata 에 테이블 등록
import domains.ai.models  # noqa: F401
import domains.audit.models  # noqa: F401
import domains.catalog.models  # noqa: F401
import domains.command.models  # noqa: F401
import domains.dashboard.models  # noqa: F401
import domains.gitops.models  # noqa: F401
import domains.identity.models  # noqa: F401
import domains.inventory.models  # noqa: F401
import domains.rca.models  # noqa: F401
import domains.scm.models  # noqa: F401
import domains.shell_state.models  # noqa: F401
import domains.target.models  # noqa: F401
import domains.timeline.models  # noqa: F401
import packages.storage.schema  # noqa: F401
from alembic import context
from packages.storage.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    # psycopg 드라이버 사용: postgresql:// -> postgresql+psycopg://
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — SQL 스크립트만 생성, DB 연결 없음."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — 실제 DB 에 연결하여 마이그레이션 적용."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
