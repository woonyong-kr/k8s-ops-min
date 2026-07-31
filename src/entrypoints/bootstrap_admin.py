"""Upsert the fixed dev administrator after Alembic has established the schema."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "src" / "services" / "gateway" / "api-gateway"
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

from passwords import default_display_name, hash_password  # noqa: E402

from packages.config.settings import env, required_env  # noqa: E402
from packages.storage.database import Database  # noqa: E402
from packages.storage.migration import (  # noqa: E402
    DATABASE_URL_ENV,
    EXPECTED_HEAD_ENV,
    alembic_config,
    current_revisions,
    revisions,
)

DEV_ADMIN_IDENTIFIER = "admin"
DEFAULT_PROJECT_SLUG = "kubernetes-ops"


def verify_versioned_head(database_url: str, expected_head: str) -> None:
    image_head, _ = revisions(alembic_config())
    if expected_head != image_head:
        raise RuntimeError(
            f"admin bootstrap image head mismatch; expected={expected_head} image_head={image_head}"
        )
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.connect() as connection:
        version_exists, current = current_revisions(connection)
    if not version_exists or current != [image_head]:
        raise RuntimeError("admin bootstrap requires the database at the exact Alembic head")


def bootstrap_admin() -> str:
    database_url = required_env(DATABASE_URL_ENV)
    expected_head = required_env(EXPECTED_HEAD_ENV)
    password = required_env("AUTH_PASSWORD")
    if len(password) < 8:
        raise ValueError("AUTH_PASSWORD must contain at least 8 characters")
    verify_versioned_head(database_url, expected_head)

    project_slug = env("PROJECT_SLUG", DEFAULT_PROJECT_SLUG).strip()
    if not project_slug:
        raise ValueError("PROJECT_SLUG must not be empty")
    user_id = "user-" + str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{project_slug}:{DEV_ADMIN_IDENTIFIER}")
    )

    db = Database()
    db.verify_schema()
    db.upsert_admin_account(
        user_id=user_id,
        email=DEV_ADMIN_IDENTIFIER,
        password_hash=hash_password(password),
        display_name=default_display_name(DEV_ADMIN_IDENTIFIER),
    )
    return user_id


def main() -> int:
    bootstrap_admin()
    print(f"admin bootstrap complete: identifier={DEV_ADMIN_IDENTIFIER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
