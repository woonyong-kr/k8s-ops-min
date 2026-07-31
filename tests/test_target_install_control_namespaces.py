"""Target install manifests create every namespace required by namespaced RBAC."""

from __future__ import annotations

import yaml

from domains.target.install_manifest import install_namespaces, target_install_manifest
from packages.contracts.gateway.requests import TargetRegisterRequest


def documents(manifest: str) -> list[dict]:
    return [document for document in yaml.safe_load_all(manifest) if isinstance(document, dict)]


def test_target_install_creates_configured_control_namespaces_before_rbac() -> None:
    payload = TargetRegisterRequest(
        cluster_id="cluster-1",
        cloud_provider="existing-k8s",
        deploy_provider="manual-manifest",
        control_namespaces="sandbox,color-turf",
    )

    manifest = target_install_manifest(payload, "agent-token")
    rendered = documents(manifest)
    namespaces = [
        document["metadata"]["name"] for document in rendered if document.get("kind") == "Namespace"
    ]

    assert install_namespaces(payload) == ("target", "sandbox", "color-turf")
    assert namespaces == ["target", "sandbox", "color-turf"]
    color_turf_namespace_index = next(
        index
        for index, document in enumerate(rendered)
        if document.get("kind") == "Namespace" and document["metadata"]["name"] == "color-turf"
    )
    color_turf_role_indexes = [
        index
        for index, document in enumerate(rendered)
        if document.get("kind") in {"Role", "RoleBinding"}
        and document.get("metadata", {}).get("namespace") == "color-turf"
    ]
    assert color_turf_role_indexes
    assert all(color_turf_namespace_index < index for index in color_turf_role_indexes)


def test_default_target_namespace_set_remains_deduplicated() -> None:
    payload = TargetRegisterRequest(
        cluster_id="cluster-1",
        cloud_provider="existing-k8s",
        deploy_provider="manual-manifest",
    )

    assert install_namespaces(payload) == ("target", "sandbox")


def test_management_install_creates_its_configured_control_namespace() -> None:
    payload = TargetRegisterRequest(
        cluster_id="management-1",
        cluster_role="management",
        control_namespaces="sandbox",
    )

    assert install_namespaces(payload) == ("management", "sandbox")
