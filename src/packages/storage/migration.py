"""Fail-closed Alembic runner for already versioned PostgreSQL databases.

The legacy AWS database was created through ``Database.init()`` and has no
Alembic version marker.  This runner deliberately refuses that state: a
separate catalog/data proof and DBA-approved transition must establish a
baseline first.  It never derives tenancy or migration authority from payload
data and never prints a database URL.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence, Set
from enum import StrEnum
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection

from packages.config.settings import required_env
from packages.storage.engine import SCHEMA_INIT_LOCK_KEY, SCHEMA_INIT_LOCK_NAMESPACE

DATABASE_URL_ENV = "DATABASE_URL"
EXPECTED_HEAD_ENV = "MIGRATION_EXPECTED_HEAD"
ROOT = Path(__file__).resolve().parents[3]


class MigrationDecision(StrEnum):
    """Allowed and blocked states before an online upgrade."""

    BLOCKED_UNVERSIONED = "blocked_unversioned"
    BLOCKED_EMPTY_VERSION = "blocked_empty_version"
    BLOCKED_UNKNOWN_REVISION = "blocked_unknown_revision"
    BLOCKED_MULTIPLE_REVISIONS = "blocked_multiple_revisions"
    UPGRADE = "upgrade"
    CURRENT = "current"


def decide_upgrade(
    *,
    version_table_exists: bool,
    current_revisions: Sequence[str],
    known_revisions: Set[str],
    head_revision: str,
) -> MigrationDecision:
    """Return a deterministic decision without changing schema state."""
    if not version_table_exists:
        return MigrationDecision.BLOCKED_UNVERSIONED
    if not current_revisions:
        return MigrationDecision.BLOCKED_EMPTY_VERSION
    if len(current_revisions) != 1:
        return MigrationDecision.BLOCKED_MULTIPLE_REVISIONS
    current = current_revisions[0]
    if current not in known_revisions:
        return MigrationDecision.BLOCKED_UNKNOWN_REVISION
    if current == head_revision:
        return MigrationDecision.CURRENT
    return MigrationDecision.UPGRADE


def alembic_config() -> Config:
    """Build Alembic configuration without loading logging side effects."""
    config = Config()
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.set_main_option("prepend_sys_path", str(ROOT / "src"))
    return config


def revisions(config: Config) -> tuple[str, frozenset[str]]:
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"migration history must have exactly one head; found={len(heads)}")
    known = frozenset(item.revision for item in script.walk_revisions())
    return heads[0], known


def current_revisions(connection: Connection) -> tuple[bool, list[str]]:
    schema = inspect(connection).default_schema_name
    if not inspect(connection).has_table("alembic_version", schema=schema):
        return False, []
    rows = connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
    return True, sorted(str(item) for item in rows)


def acquire_schema_lock(connection: Connection) -> None:
    acquired = connection.execute(
        text("SELECT pg_try_advisory_lock(:namespace, :key)"),
        {"namespace": SCHEMA_INIT_LOCK_NAMESPACE, "key": SCHEMA_INIT_LOCK_KEY},
    ).scalar_one()
    if not acquired:
        raise RuntimeError("database schema lock is held by another process")


def release_schema_lock(connection: Connection) -> None:
    connection.execute(
        text("SELECT pg_advisory_unlock(:namespace, :key)"),
        {"namespace": SCHEMA_INIT_LOCK_NAMESPACE, "key": SCHEMA_INIT_LOCK_KEY},
    )


def run_upgrade() -> MigrationDecision:
    """Upgrade one versioned database while excluding create-all bootstrap."""
    database_url = required_env(DATABASE_URL_ENV)
    expected_head = required_env(EXPECTED_HEAD_ENV)
    config = alembic_config()
    head, known = revisions(config)
    if expected_head != head:
        raise RuntimeError(
            f"migration image head mismatch; expected={expected_head} image_head={head}"
        )

    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.connect() as lock_connection:
        acquire_schema_lock(lock_connection)
        try:
            version_exists, current = current_revisions(lock_connection)
            decision = decide_upgrade(
                version_table_exists=version_exists,
                current_revisions=current,
                known_revisions=known,
                head_revision=head,
            )
            if decision is MigrationDecision.CURRENT:
                return decision
            if decision is not MigrationDecision.UPGRADE:
                raise RuntimeError(f"migration refused: {decision.value}")

            from alembic import command

            os.environ[DATABASE_URL_ENV] = database_url
            command.upgrade(config, "head")

            with engine.connect() as verification_connection:
                _, after = current_revisions(verification_connection)
            if after != [head]:
                raise RuntimeError("migration completed without the expected head revision")
            return decision
        finally:
            release_schema_lock(lock_connection)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run fail-closed database migrations")
    parser.add_argument("action", choices=("upgrade",))
    args = parser.parse_args(argv)
    if args.action == "upgrade":
        decision = run_upgrade()
        print(f"migration result: {decision.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
