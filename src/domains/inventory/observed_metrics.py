"""Shared extraction and freshness rules for observed Kubernetes utilization."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from math import isfinite
from typing import Any

from packages.config.refresh_policies import browser_refresh_policy


def usage_pct(
    usage: Mapping[str, Any],
    pct_keys: tuple[str, ...],
    ratio_keys: tuple[str, ...],
) -> float | None:
    """Return an observed utilization percent, preferring percent over ratio fields."""

    for key in pct_keys:
        value = _float_or_none(usage.get(key))
        if value is not None:
            return round(value, 1)
    for key in ratio_keys:
        value = _float_or_none(usage.get(key))
        if value is not None:
            return round(value * 100.0, 1)
    return None


def inventory_usage_pct(
    evidence: Mapping[str, Any],
    pct_keys: tuple[str, ...],
    ratio_keys: tuple[str, ...],
    *,
    now: datetime | None = None,
) -> float | None:
    """Accept only finite, non-negative utilization backed by a fresh observation."""

    if inventory_metrics_observed_at(evidence, now=now) is None:
        return None
    summary = _summary(evidence)
    value = usage_pct(summary, pct_keys, ratio_keys)
    return value if value is not None and isfinite(value) and value >= 0 else None


def inventory_metrics_observed_at(
    evidence: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> str | None:
    """Return the metric timestamp only while it satisfies the canonical freshness policy."""

    summary = _summary(evidence)
    observed_at = _optional_text(
        evidence.get("metrics_observed_at") or summary.get("metrics_observed_at")
    )
    return observed_at if inventory_metrics_are_fresh(observed_at, now=now) else None


def inventory_metrics_are_fresh(
    observed_at: str | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Apply the canonical Kubernetes-metrics staleness window, failing closed."""

    if observed_at is None:
        return False
    try:
        parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    current = now or datetime.now(UTC)
    stale_after = browser_refresh_policy("metrics_kubernetes").stale_after_seconds
    if stale_after is None:
        return False
    age_seconds = (current - parsed).total_seconds()
    return -stale_after <= age_seconds <= stale_after


def _summary(evidence: Mapping[str, Any]) -> Mapping[str, Any]:
    value = evidence.get("summary")
    return value if isinstance(value, Mapping) else evidence


def _optional_text(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def _float_or_none(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
