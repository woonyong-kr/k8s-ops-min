from __future__ import annotations

import os
from datetime import UTC, datetime


def env(name: str, default: str) -> str:
    return os.getenv(name, default)


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
