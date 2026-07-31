"""Bootstrap or explicitly reset a database through the complete Alembic history.

The production legacy database is intentionally never adopted in place.  A DBA
creates a new empty database, this module installs the immutable schema that
predates the first revision, and Alembic then applies every revision normally.
No version row is forged and no database URL is printed.  The destructive dev
reset is a separate, strongly-confirmed action; it is never selected implicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from collections.abc import Sequence
from enum import StrEnum

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection

from alembic import command
from packages.config.settings import required_env
from packages.storage.migration import (
    DATABASE_URL_ENV,
    EXPECTED_HEAD_ENV,
    ROOT,
    acquire_schema_lock,
    alembic_config,
    current_revisions,
    release_schema_lock,
    revisions,
)

BASELINE_TARGET_DATABASE_URL_ENV = "BASELINE_TARGET_DATABASE_URL"
BASELINE_CONFIRM_SOURCE_COMMIT_ENV = "BASELINE_CONFIRM_SOURCE_COMMIT"
BASELINE_CONFIRM_EMPTY_TARGET_ENV = "BASELINE_CONFIRM_EMPTY_TARGET"
BASELINE_EMPTY_TARGET_CONFIRMATION = "isolated-empty-database"
BASELINE_CONFIRM_DESTRUCTIVE_RESET_ENV = "BASELINE_CONFIRM_DESTRUCTIVE_RESET"
BASELINE_DESTRUCTIVE_RESET_CONFIRMATION = "destroy-and-rebuild-dev-public-schema"
BASELINE_SOURCE_COMMIT = "017b2485b2c408c2f7e928379ebf6541526d32ab"
BASELINE_SQL_PATH = ROOT / "alembic/baselines/20260708_pre_alembic.sql"
BASELINE_SQL_SHA256 = "080d5c843384923df0c640572465c40fe3b6e8513b97bd9e8815f1fe46118136"


class BaselineDecision(StrEnum):
    """Allowed state for installing the immutable pre-migration schema."""

    READY = "ready"
    BLOCKED_NOT_EMPTY = "blocked_not_empty"


def decide_bootstrap(table_names: Sequence[str]) -> BaselineDecision:
    """Require a genuinely empty target; existing databases are never adopted."""
    if table_names:
        return BaselineDecision.BLOCKED_NOT_EMPTY
    return BaselineDecision.READY


def load_baseline_sql() -> str:
    """Load the pinned schema snapshot after verifying its content digest."""
    sql = BASELINE_SQL_PATH.read_text(encoding="utf-8")
    actual_digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    if actual_digest != BASELINE_SQL_SHA256:
        raise RuntimeError("pre-migration baseline digest mismatch")
    return sql


def _target_table_names(connection: Connection) -> list[str]:
    schema = connection.execute(text("SELECT current_schema()"), {}).scalar_one()
    if schema != "public":
        raise RuntimeError("baseline target must use the public schema")
    return sorted(inspect(connection).get_table_names(schema="public"))


def _validate_operator_confirmation() -> None:
    source_commit = required_env(BASELINE_CONFIRM_SOURCE_COMMIT_ENV)
    if source_commit != BASELINE_SOURCE_COMMIT:
        raise RuntimeError("baseline source commit confirmation mismatch")
    empty_target = required_env(BASELINE_CONFIRM_EMPTY_TARGET_ENV)
    if empty_target != BASELINE_EMPTY_TARGET_CONFIRMATION:
        raise RuntimeError("isolated empty target confirmation mismatch")


def _validate_reset_confirmation() -> None:
    source_commit = required_env(BASELINE_CONFIRM_SOURCE_COMMIT_ENV)
    if source_commit != BASELINE_SOURCE_COMMIT:
        raise RuntimeError("baseline source commit confirmation mismatch")
    confirmation = required_env(BASELINE_CONFIRM_DESTRUCTIVE_RESET_ENV)
    if confirmation != BASELINE_DESTRUCTIVE_RESET_CONFIRMATION:
        raise RuntimeError("destructive dev reset confirmation mismatch")


def _install_pre_migration_schema(connection: Connection) -> None:
    sql = load_baseline_sql()
    driver_connection = connection.connection.driver_connection
    driver_connection.execute(sql)
    connection.commit()


def _replace_public_schema(connection: Connection) -> None:
    """Delete the explicitly selected dev schema without interpolating identifiers."""
    connection.execute(text("DROP SCHEMA public CASCADE"))
    connection.execute(text("CREATE SCHEMA public"))
    connection.commit()


def run_bootstrap() -> str:
    """Create a versioned target without mutating or stamping the legacy DB."""
    _validate_operator_confirmation()
    target_url = required_env(BASELINE_TARGET_DATABASE_URL_ENV)
    expected_head = required_env(EXPECTED_HEAD_ENV)
    config = alembic_config()
    head, _ = revisions(config)
    if expected_head != head:
        raise RuntimeError(
            f"migration image head mismatch; expected={expected_head} image_head={head}"
        )

    sqlalchemy_url = target_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(sqlalchemy_url)
    with engine.connect() as lock_connection:
        acquire_schema_lock(lock_connection)
        try:
            decision = decide_bootstrap(_target_table_names(lock_connection))
            if decision is not BaselineDecision.READY:
                raise RuntimeError(f"baseline refused: {decision.value}")
            _install_pre_migration_schema(lock_connection)

            os.environ[DATABASE_URL_ENV] = target_url
            command.upgrade(config, "head")

            with engine.connect() as verification_connection:
                version_exists, current = current_revisions(verification_connection)
            if not version_exists or current != [head]:
                raise RuntimeError("baseline upgrade did not reach the expected head")
            return head
        finally:
            release_schema_lock(lock_connection)


def run_dev_reset() -> str:
    """Destroy and rebuild one explicitly confirmed dev public schema."""
    _validate_reset_confirmation()
    target_url = required_env(BASELINE_TARGET_DATABASE_URL_ENV)
    expected_head = required_env(EXPECTED_HEAD_ENV)
    config = alembic_config()
    head, _ = revisions(config)
    if expected_head != head:
        raise RuntimeError(
            f"migration image head mismatch; expected={expected_head} image_head={head}"
        )

    sqlalchemy_url = target_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(sqlalchemy_url)
    with engine.connect() as lock_connection:
        acquire_schema_lock(lock_connection)
        try:
            _replace_public_schema(lock_connection)
            if decide_bootstrap(_target_table_names(lock_connection)) is not BaselineDecision.READY:
                raise RuntimeError("destructive dev reset did not produce an empty public schema")
            _install_pre_migration_schema(lock_connection)

            os.environ[DATABASE_URL_ENV] = target_url
            command.upgrade(config, "head")

            with engine.connect() as verification_connection:
                version_exists, current = current_revisions(verification_connection)
            if not version_exists or current != [head]:
                raise RuntimeError("destructive dev reset did not reach the expected head")
            return head
        finally:
            release_schema_lock(lock_connection)


def verify_snapshot() -> tuple[str, str]:
    """Verify immutable assets without opening a database connection."""
    load_baseline_sql()
    head, _ = revisions(alembic_config())
    return BASELINE_SQL_SHA256, head


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap or reset a versioned database")
    parser.add_argument("action", choices=("verify", "bootstrap", "reset-dev"))
    args = parser.parse_args(argv)
    if args.action == "verify":
        digest, head = verify_snapshot()
        print(f"baseline verified: digest={digest} head={head}")
        return 0
    head = run_bootstrap() if args.action == "bootstrap" else run_dev_reset()
    print(f"baseline result: upgraded head={head}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
