from __future__ import annotations

from conftest import load_service

from domains.gitops.events import RenderedManifest, RenderedMetadata, RenderedSpec
from domains.gitops.repository import prepare_approved_resource_snapshots


def deployment_rendered(replicas: int) -> RenderedManifest:
    manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "api", "namespace": "sandbox"},
        "spec": {
            "replicas": replicas,
            "template": {
                "spec": {
                    "containers": [{"name": "api", "image": "registry/api:v2"}],
                }
            },
        },
    }
    return RenderedManifest(
        api_version="apps/v1",
        kind="Deployment",
        metadata=RenderedMetadata(name="api", namespace="sandbox"),
        spec=RenderedSpec(replicas=replicas, image="registry/api:v2"),
        manifest=manifest,
        declared_fields=[
            "spec.replicas",
            "spec.template.spec.containers[name=api].image",
        ],
    )


def test_renderer_injects_resource_scoped_approved_snapshot_as_managed_policy() -> None:
    service = load_service("gitops/manifest-render-worker")
    rendered = deployment_rendered(1)
    snapshot = {
        "resource": "deployment/api",
        "namespace": "sandbox",
        "fields": {
            "spec.replicas": 2,
            "spec.template.spec.containers[name=api].image": "registry/api:v1",
        },
    }

    enriched = service.with_approved_snapshot(rendered, snapshot)

    assert enriched.last_approved_snapshot["spec.replicas"] == 2
    assert enriched.managed_fields == sorted(snapshot["fields"])


def test_renderer_rejects_snapshot_for_another_resource() -> None:
    service = load_service("gitops/manifest-render-worker")
    rendered = deployment_rendered(1)

    enriched = service.with_approved_snapshot(
        rendered,
        {
            "resource": "deployment/other",
            "namespace": "sandbox",
            "fields": {"spec.replicas": 9},
        },
    )

    assert enriched.last_approved_snapshot == {}


def approved_command(
    command_id: str,
    *,
    kind: str,
    name: str,
    manifest: dict[str, object],
) -> dict[str, object]:
    return {
        "command_id": command_id,
        "completed_at": None,
        "payload": {
            "diff": {
                "resource": f"{kind.casefold()}/{name}",
                "namespace": "sandbox",
                "workflow_run_id": "workflow-demo",
                "workspace_id": "workspace-demo",
                "binding_id": "binding-demo",
                "cluster_id": "cluster-demo",
                "desired_manifest": manifest,
                "basis": {"artifact_digest": f"digest-{command_id}"},
            }
        },
    }


def test_mixed_demo_kinds_count_as_handled_but_only_supported_kinds_are_snapshotted() -> None:
    run = {
        "workspace_id": "workspace-demo",
        "binding_id": "binding-demo",
        "cluster_id": "cluster-demo",
        "commit_sha": "commit-demo",
    }
    commands = [
        approved_command(
            "command-deployment",
            kind="Deployment",
            name="api-server",
            manifest={
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "api-server", "namespace": "sandbox"},
                "spec": {
                    "replicas": 2,
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "api-server",
                                    "image": "registry.example/api:v2",
                                }
                            ]
                        }
                    },
                },
            },
        ),
        approved_command(
            "command-service",
            kind="Service",
            name="login-gateway-api",
            manifest={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "login-gateway-api", "namespace": "sandbox"},
                "spec": {
                    "selector": {"app": "api-server"},
                    "ports": [{"port": 8081, "targetPort": "http"}],
                },
            },
        ),
        approved_command(
            "command-pdb",
            kind="PodDisruptionBudget",
            name="api-server",
            manifest={
                "apiVersion": "policy/v1",
                "kind": "PodDisruptionBudget",
                "metadata": {"name": "api-server", "namespace": "sandbox"},
                "spec": {"minAvailable": 1},
            },
        ),
        approved_command(
            "command-service-account",
            kind="ServiceAccount",
            name="management-server",
            manifest={
                "apiVersion": "v1",
                "kind": "ServiceAccount",
                "metadata": {"name": "management-server", "namespace": "sandbox"},
            },
        ),
        approved_command(
            "command-role",
            kind="Role",
            name="management-server",
            manifest={
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "Role",
                "metadata": {"name": "management-server", "namespace": "sandbox"},
                "rules": [],
            },
        ),
    ]

    result = prepare_approved_resource_snapshots("workflow-demo", run, commands)

    assert result is not None
    handled, snapshots = result
    assert handled == len(commands)
    assert {(snapshot["resource_kind"], snapshot["resource_name"]) for snapshot in snapshots} == {
        ("deployment", "api-server"),
        ("service", "login-gateway-api"),
    }
    deployment = next(
        snapshot for snapshot in snapshots if snapshot["resource_kind"] == "deployment"
    )
    assert deployment["snapshot"]["fields"]["spec.replicas"] == 2
