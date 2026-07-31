"""Shared fail-closed policy for browser-initiated pod exec."""

from __future__ import annotations

from packages.config.constants import Sandbox
from packages.config.settings import env

POD_EXEC_ALLOWED_NAMESPACES_ENV = "POD_EXEC_ALLOWED_NAMESPACES"
DEFAULT_POD_EXEC_ALLOWED_NAMESPACES = Sandbox.NAMESPACE


def pod_exec_allowed_namespaces() -> frozenset[str]:
    return frozenset(
        namespace
        for item in env(
            POD_EXEC_ALLOWED_NAMESPACES_ENV,
            DEFAULT_POD_EXEC_ALLOWED_NAMESPACES,
        ).split(",")
        if (namespace := item.strip())
    )


def pod_exec_namespace_allowed(namespace: str) -> bool:
    normalized = namespace.strip()
    return bool(normalized) and normalized in pod_exec_allowed_namespaces()
