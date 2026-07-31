"""Pure value helpers shared by release-flow modules."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from packages.config.constants import Sandbox

PLACEHOLDER_EVIDENCE_HOSTS = {
    "127.0.0.1",
    "::1",
    "example.com",
    "example.test",
    "localhost",
}


def release_step_index(plan: dict[str, Any], step: dict[str, Any]) -> int:
    for index, candidate in enumerate(plan.get("steps", [])):
        if candidate is step:
            return index
        if isinstance(candidate, dict) and candidate == step:
            return index
    return -1


def unique_non_empty(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def parse_release_window_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    normalized = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return normalized.astimezone(UTC)


def release_window_bound_label(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def live_https_url_is_valid(value: str) -> bool:
    parsed = urlparse(value.strip())
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return False
    host = (parsed.hostname or "").lower()
    return not (
        host in PLACEHOLDER_EVIDENCE_HOSTS
        or host.endswith(".example.com")
        or host.endswith(".example.test")
        or host.endswith(".localhost")
    )


def release_step_index_in_steps(steps: list[Any], selected: dict[str, Any]) -> int:
    selected_application_id = str(selected.get("application_id") or "")
    selected_position = selected.get("position")
    for index, candidate in enumerate(steps):
        if not isinstance(candidate, dict):
            continue
        if candidate is selected:
            return index
        if (
            selected_application_id
            and str(candidate.get("application_id") or "") == selected_application_id
        ):
            return index
        if selected_position is not None and candidate.get("position") == selected_position:
            return index
    return -1


def step_config(step: dict[str, Any]) -> dict[str, Any]:
    config = step.get("config", {})
    return dict(config) if isinstance(config, dict) else {}


def plan_settings_value(plan: dict[str, Any]) -> dict[str, Any]:
    settings = plan.get("settings", {})
    return dict(settings) if isinstance(settings, dict) else {}


def int_field(values: dict[str, Any], field: str, fallback: int) -> int:
    raw = values.get(field)
    if isinstance(raw, bool):
        return fallback
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw)
    return fallback


def first_environment(settings: dict[str, Any]) -> str:
    order = settings.get("environment_order", [])
    if isinstance(order, list) and order:
        return str(order[0])
    return Sandbox.NAMESPACE
