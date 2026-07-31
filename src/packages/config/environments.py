"""Canonical deployment-environment classification.

Environment aliases are policy inputs, not free-form display labels.  Keep the
normalization and safety tiers in one package-level module so database startup,
release guards, diagnostics, and workers cannot disagree about ``prod``.
"""

from __future__ import annotations

from enum import StrEnum


class EnvironmentClass(StrEnum):
    SANDBOX = "sandbox"
    STAGING = "staging"
    PRODUCTION = "production"
    OTHER = "other"


SANDBOX_ENVIRONMENT = "sandbox"
STAGING_ENVIRONMENT = "staging"
PRODUCTION_ENVIRONMENT_ALIASES = frozenset({"prod", "production"})


def normalize_environment(value: object) -> str:
    """Return the canonical comparison form for one environment label."""
    if value is None:
        return ""
    return str(value).strip().casefold()


def classify_environment(value: object) -> EnvironmentClass:
    normalized = normalize_environment(value)
    if normalized == SANDBOX_ENVIRONMENT:
        return EnvironmentClass.SANDBOX
    if normalized == STAGING_ENVIRONMENT:
        return EnvironmentClass.STAGING
    if normalized in PRODUCTION_ENVIRONMENT_ALIASES:
        return EnvironmentClass.PRODUCTION
    return EnvironmentClass.OTHER


def is_sandbox_environment(value: object) -> bool:
    return classify_environment(value) is EnvironmentClass.SANDBOX


def is_staging_environment(value: object) -> bool:
    return classify_environment(value) is EnvironmentClass.STAGING


def is_production_environment(value: object) -> bool:
    return classify_environment(value) is EnvironmentClass.PRODUCTION


def is_protected_runtime_environment(value: object) -> bool:
    """Return whether startup must default to read-only schema verification."""
    return classify_environment(value) in {
        EnvironmentClass.STAGING,
        EnvironmentClass.PRODUCTION,
    }
