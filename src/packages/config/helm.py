"""Helm read-model query protection budgets."""

from __future__ import annotations

from dataclasses import dataclass

from packages.config.settings import env
from packages.contracts.helm.artifacts import HELM_ARTIFACT_CONTENT_MAX_BYTES

HELM_OWNED_RESOURCE_QUERY_LIMIT_ENV = "HELM_OWNED_RESOURCE_QUERY_LIMIT"
HELM_ARTIFACT_TIMEOUT_SECONDS_ENV = "HELM_ARTIFACT_TIMEOUT_SECONDS"
HELM_ARTIFACT_OUTPUT_MAX_BYTES_ENV = "HELM_ARTIFACT_OUTPUT_MAX_BYTES"
HELM_ARTIFACT_SOURCE_MAX_BYTES_ENV = "HELM_ARTIFACT_SOURCE_MAX_BYTES"


@dataclass(frozen=True)
class HelmReadLimitDefaults:
    """Deployment defaults; operators can tune the bounded ownership scan."""

    owned_resource_query_limit: int = 5_000
    artifact_timeout_seconds: int = 60
    artifact_output_max_bytes: int = 1024 * 1024
    artifact_source_max_bytes: int = HELM_ARTIFACT_CONTENT_MAX_BYTES


HELM_READ_LIMIT_DEFAULTS = HelmReadLimitDefaults()


def helm_owned_resource_query_limit() -> int:
    """Return a positive, bounded result budget for one Helm ownership read."""

    default = HELM_READ_LIMIT_DEFAULTS.owned_resource_query_limit
    try:
        configured = int(env(HELM_OWNED_RESOURCE_QUERY_LIMIT_ENV, str(default)))
    except ValueError:
        return default
    if configured <= 0:
        return default
    return min(configured, 50_000)


@dataclass(frozen=True)
class HelmArtifactLimits:
    timeout_seconds: int
    output_max_bytes: int
    source_max_bytes: int


def helm_artifact_limits() -> HelmArtifactLimits:
    defaults = HELM_READ_LIMIT_DEFAULTS
    return HelmArtifactLimits(
        timeout_seconds=_positive_int(
            HELM_ARTIFACT_TIMEOUT_SECONDS_ENV,
            defaults.artifact_timeout_seconds,
            maximum=300,
        ),
        output_max_bytes=_positive_int(
            HELM_ARTIFACT_OUTPUT_MAX_BYTES_ENV,
            defaults.artifact_output_max_bytes,
            maximum=HELM_ARTIFACT_CONTENT_MAX_BYTES,
        ),
        source_max_bytes=_positive_int(
            HELM_ARTIFACT_SOURCE_MAX_BYTES_ENV,
            defaults.artifact_source_max_bytes,
            maximum=16 * 1024 * 1024,
        ),
    )


def _positive_int(name: str, default: int, *, maximum: int) -> int:
    try:
        configured = int(env(name, str(default)))
    except ValueError:
        return default
    if configured <= 0:
        return default
    return min(configured, maximum)
