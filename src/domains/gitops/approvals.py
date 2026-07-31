"""Stable resource-scoped approval identities for multi-resource workflows."""

from __future__ import annotations


def resource_approval_qualifier(
    namespace: object,
    resource: object,
    artifact_digest: object = "",
) -> str:
    normalized_namespace = str(namespace or "").strip() or "cluster"
    normalized_resource = str(resource or "").strip().casefold() or "resource"
    normalized_artifact = str(artifact_digest or "").strip()
    return "|".join(
        (normalized_namespace, normalized_resource, normalized_artifact)
    )
