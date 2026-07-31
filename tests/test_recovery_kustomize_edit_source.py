from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import httpx
import pytest
from conftest import ROOT, load_file

from domains.gitops.kustomize_edit_source import resolve_unique_kustomize_edit_source
from domains.gitops.repository_discovery import ManifestRenderValidationError
from domains.gitops.source_patch import (
    ManifestScalarPatchPlan,
    ScalarFieldReplacement,
    canonical_manifest_digest,
    scalar_patch_content,
)
from domains.manifest_editor.validation import ManifestIdentity
from domains.scm.events import SafePrFilePatch, SafePrRequestedBody


class FakeSnapshotClient:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    async def tree_at_revision(
        self,
        repo_ref: str,
        revision: str,
    ) -> tuple[list[dict[str, object]], list[str]]:
        assert repo_ref == "org/repo"
        assert revision == "a" * 40
        return (
            [
                {"type": "blob", "path": path}
                for path in sorted(self.files)
            ],
            [],
        )

    async def content(
        self,
        repo_ref: str,
        revision: str,
        path: str,
    ) -> bytes:
        assert repo_ref == "org/repo"
        assert revision == "a" * 40
        return self.files[path].encode()


def deployment(name: str, replicas: int = 1) -> str:
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  namespace: target
spec:
  replicas: {replicas}
  selector:
    matchLabels:
      app: {name}
  template:
    metadata:
      labels:
        app: {name}
    spec:
      containers:
        - name: {name}
          image: example/{name}:v1
"""


def resolve(files: dict[str, str], *, name: str = "lobby"):
    return asyncio.run(
        resolve_unique_kustomize_edit_source(
            FakeSnapshotClient(files),  # type: ignore[arg-type]
            repo_ref="org/repo",
            revision="a" * 40,
            binding_manifest_path="deploy/overlays/game-server/kustomization.yaml",
            selected_identity=ManifestIdentity(
                "apps/v1",
                "Deployment",
                "target",
                name,
            ),
            protected_field_paths=("spec.replicas",),
        )
    )


def test_resolves_one_reachable_kustomize_resource_file() -> None:
    source = resolve(
        {
            "deploy/overlays/game-server/kustomization.yaml": (
                "resources:\n  - ../../base/lobby.yaml\n"
            ),
            "deploy/base/lobby.yaml": deployment("lobby"),
        }
    )

    assert source is not None
    assert source.path == "deploy/base/lobby.yaml"
    assert source.source_type == "raw-yaml"
    assert source.manifest_sha256.startswith("sha256:")


def test_missing_or_ambiguous_resource_fails_closed() -> None:
    missing = resolve(
        {
            "deploy/overlays/game-server/kustomization.yaml": (
                "resources:\n  - ../../base/api.yaml\n"
            ),
            "deploy/base/api.yaml": deployment("api"),
        }
    )
    ambiguous = resolve(
        {
            "deploy/overlays/game-server/kustomization.yaml": (
                "resources:\n  - ../../base/lobby.yaml\n  - ../../other/lobby.yaml\n"
            ),
            "deploy/base/lobby.yaml": deployment("lobby"),
            "deploy/other/lobby.yaml": deployment("lobby", replicas=2),
        }
    )

    assert missing is None
    assert ambiguous is None


def test_resolves_one_selected_document_in_multi_document_source() -> None:
    multi_document = (
        deployment("lobby")
        + "\n---\n"
        + """\
apiVersion: v1
kind: Service
metadata:
  name: lobby
  namespace: target
spec:
  selector:
    app: lobby
"""
    )
    source = resolve(
        {
            "deploy/overlays/game-server/kustomization.yaml": (
                "resources:\n  - ../../base/lobby.yaml\n"
            ),
            "deploy/base/lobby.yaml": multi_document,
        }
    )

    assert source is not None
    assert source.path == "deploy/base/lobby.yaml"
    assert source.document_identity == ManifestIdentity(
        "apps/v1",
        "Deployment",
        "target",
        "lobby",
    )


def test_duplicate_multi_document_identity_and_anchor_fail_closed() -> None:
    duplicate = deployment("lobby") + "\n---\n" + deployment("lobby", replicas=2)
    list_item = deployment("lobby").replace("\n", "\n    ").rstrip()
    list_wrapper = f"""\
apiVersion: v1
kind: List
items:
  - {list_item}
"""
    anchored = deployment("lobby").replace(
        "replicas: 1",
        "replicas: &replicas 1",
    )

    assert (
        resolve(
            {
                "deploy/overlays/game-server/kustomization.yaml": (
                    "resources:\n  - ../../base/lobby.yaml\n"
                ),
                "deploy/base/lobby.yaml": duplicate,
            }
        )
        is None
    )
    assert (
        resolve(
            {
                "deploy/overlays/game-server/kustomization.yaml": (
                    "resources:\n  - ../../base/lobby.yaml\n"
                ),
                "deploy/base/lobby.yaml": (
                    deployment("lobby")
                    + "\n---\n"
                    + list_wrapper
                ),
            }
        )
        is None
    )
    assert (
        resolve(
            {
                "deploy/overlays/game-server/kustomization.yaml": (
                    "resources:\n  - ../../base/lobby.yaml\n"
                ),
                "deploy/base/lobby.yaml": list_wrapper,
            }
        )
        is None
    )
    assert (
        resolve(
            {
                "deploy/overlays/game-server/kustomization.yaml": (
                    "resources:\n  - ../../base/lobby.yaml\n"
                ),
                "deploy/base/lobby.yaml": anchored,
            }
        )
        is None
    )


def test_kustomize_field_ownership_is_not_bypassed_by_equal_raw_value() -> None:
    assert (
        resolve(
            {
                "deploy/overlays/game-server/kustomization.yaml": (
                    "resources:\n"
                    "  - ../../base/lobby.yaml\n"
                    "replicas:\n"
                    "  - name: lobby\n"
                    "    count: 1\n"
                ),
                "deploy/base/lobby.yaml": deployment("lobby"),
            }
        )
        is None
    )


def test_unrelated_overlay_patches_do_not_block_exact_replica_owner() -> None:
    source = resolve(
        {
            "deploy/overlays/game-server/kustomization.yaml": (
                "resources:\n"
                "  - ../../base\n"
                "images:\n"
                "  - name: example/api-server\n"
                "    newTag: v2\n"
                "patches:\n"
                "  - path: game-room-node-selector.yaml\n"
                "    target:\n"
                "      kind: Deployment\n"
                "      name: game-room-0\n"
                "  - path: gateway-config.yaml\n"
                "    target:\n"
                "      kind: ConfigMap\n"
                "      name: gateway\n"
            ),
            "deploy/overlays/game-server/game-room-node-selector.yaml": (
                "apiVersion: apps/v1\n"
                "kind: Deployment\n"
                "metadata:\n"
                "  name: game-room-0\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      nodeSelector:\n"
                "        room: zero\n"
            ),
            "deploy/overlays/game-server/gateway-config.yaml": (
                "apiVersion: v1\n"
                "kind: ConfigMap\n"
                "metadata:\n"
                "  name: gateway\n"
                "data:\n"
                "  mode: demo\n"
            ),
            "deploy/base/kustomization.yaml": (
                "resources:\n"
                "  - api-server.yaml\n"
                "  - game-room-0.yaml\n"
            ),
            "deploy/base/api-server.yaml": deployment("api-server"),
            "deploy/base/game-room-0.yaml": deployment("game-room-0"),
        },
        name="api-server",
    )

    assert source is not None
    assert source.path == "deploy/base/api-server.yaml"


def test_same_target_overlay_replica_patch_blocks_base_edit() -> None:
    source = resolve(
        {
            "deploy/overlays/game-server/kustomization.yaml": (
                "resources:\n"
                "  - ../../base/api-server.yaml\n"
                "patches:\n"
                "  - path: api-server-replicas.yaml\n"
                "    target:\n"
                "      kind: Deployment\n"
                "      name: api-server\n"
            ),
            "deploy/overlays/game-server/api-server-replicas.yaml": (
                "apiVersion: apps/v1\n"
                "kind: Deployment\n"
                "metadata:\n"
                "  name: api-server\n"
                "spec:\n"
                "  replicas: 1\n"
            ),
            "deploy/base/api-server.yaml": deployment("api-server"),
        },
        name="api-server",
    )

    assert source is None


@pytest.mark.parametrize(
    "extra_files,kustomization",
    [
        (
            {"deploy/overlays/game-server/transformer.yaml": "apiVersion: builtin\nkind: PatchTransformer\n"},
            "transformers:\n  - transformer.yaml\n",
        ),
        (
            {"deploy/overlays/game-server/replica.json": "- op: replace\n  path: /spec\n  value: {}\n"},
            (
                "patchesJson6902:\n"
                "  - path: replica.json\n"
                "    target:\n"
                "      kind: Deployment\n"
                "      name: api-server\n"
            ),
        ),
        (
            {
                "deploy/overlays/game-server/replica.json": (
                    "- op: move\n"
                    "  from: /spec/replicas\n"
                    "  path: /metadata/annotations/oldReplicas\n"
                )
            },
            (
                "patchesJson6902:\n"
                "  - path: replica.json\n"
                "    target:\n"
                "      kind: Deployment\n"
                "      name: api-server\n"
            ),
        ),
    ],
)
def test_custom_or_ancestor_patch_ownership_blocks_replica_edit(
    extra_files: dict[str, str],
    kustomization: str,
) -> None:
    source = resolve(
        {
            "deploy/overlays/game-server/kustomization.yaml": (
                "resources:\n  - ../../base/api-server.yaml\n" + kustomization
            ),
            "deploy/base/api-server.yaml": deployment("api-server"),
            **extra_files,
        },
        name="api-server",
    )

    assert source is None


def test_remote_kustomize_reference_is_rejected() -> None:
    with pytest.raises(ManifestRenderValidationError):
        resolve(
            {
                "deploy/overlays/game-server/kustomization.yaml": (
                    "resources:\n"
                    "  - github.com/example/remote//deploy?ref=v1\n"
                ),
            }
        )


def test_scm_materializes_unique_kustomize_source_without_repository_contract() -> None:
    provider_module = load_file(
        Path(ROOT) / "src/services/gitops/scm-worker/github_provider.py",
        "test_recovery_kustomize_github_provider",
    )
    base_sha = "a" * 40
    raw = (
        deployment("lobby")
        + "\n---\n"
        + """\
apiVersion: v1
kind: Service
metadata:
  name: lobby
  namespace: target
spec:
  selector:
    app: lobby
"""
    )
    desired = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "lobby", "namespace": "target"},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": "lobby"}},
            "template": {
                "metadata": {"labels": {"app": "lobby"}},
                "spec": {
                    "containers": [
                        {"name": "lobby", "image": "example/lobby:v1"}
                    ]
                },
            },
        },
    }
    plan = ManifestScalarPatchPlan(
        action_type="replica_scale",
        source_type="raw-yaml",
        source_manifest_sha256=canonical_manifest_digest(desired),
        expected_base_sha=base_sha,
        manifest_path="deploy/overlays/game-server/kustomization.yaml",
        replacements=(ScalarFieldReplacement("spec.replicas", 1, 2),),
        rollback_replacements=(ScalarFieldReplacement("spec.replicas", 2, 1),),
    )
    request = SafePrRequestedBody(
        title="scale lobby",
        body="scale",
        provider="github",
        patches=[
            SafePrFilePatch(
                path=".gitops/safe-pr/patches/recovery.yaml",
                content=scalar_patch_content(plan),
            )
        ],
        manifest_path=plan.manifest_path,
        repo_ref="org/repo",
        base_branch="dev",
        commit_sha=base_sha,
    )
    files = {
        "deploy/overlays/game-server/kustomization.yaml": (
            "resources:\n  - ../../base/lobby.yaml\n"
        ),
        "deploy/base/lobby.yaml": raw,
    }

    def response(request_value: httpx.Request) -> httpx.Response:
        path = request_value.url.path
        if path.endswith("/contents/.remediation.yaml"):
            return httpx.Response(404, json={"message": "not found"})
        if f"/git/commits/{base_sha}" in path:
            return httpx.Response(200, json={"tree": {"sha": "tree-1"}})
        if path.endswith("/git/trees/tree-1"):
            return httpx.Response(
                200,
                json={
                    "truncated": False,
                    "tree": [
                        {"type": "blob", "path": file_path}
                        for file_path in sorted(files)
                    ],
                },
            )
        marker = "/contents/"
        if marker in path:
            file_path = path.split(marker, 1)[1]
            content = files[file_path]
            return httpx.Response(
                200,
                json={
                    "type": "file",
                    "size": len(content.encode()),
                    "encoding": "base64",
                    "content": base64.b64encode(content.encode()).decode(),
                },
            )
        return httpx.Response(404, json={"message": path})

    async def materialize() -> list[tuple[str, str]]:
        provider = provider_module.GithubScmProvider()
        async with httpx.AsyncClient(
            base_url="https://api.github.test",
            transport=httpx.MockTransport(response),
        ) as client:
            return await provider.materialize_patch_contents(
                client,
                "org/repo",
                base_sha,
                request,
                [plan],
                authority={
                    "provenance": {
                        "source_type": "kustomize",
                        "manifest_path": request.manifest_path,
                    },
                    "desired_manifest": desired,
                },
            )

    contents = asyncio.run(materialize())

    assert contents[0][0] == "deploy/base/lobby.yaml"
    assert "replicas: 2" in contents[0][1]
    assert "replicas: 1" not in contents[0][1]
    assert "kind: Service" in contents[0][1]
    assert "selector:\n    app: lobby" in contents[0][1]


def test_scm_validates_recovery_against_exact_resource_diff_not_workflow_step() -> None:
    provider_module = load_file(
        Path(ROOT) / "src/services/gitops/scm-worker/github_provider.py",
        "test_resource_scoped_recovery_github_provider",
    )
    desired = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "api-server", "namespace": "sandbox"},
        "spec": {"replicas": 1},
    }
    digest = canonical_manifest_digest(desired)
    plan = ManifestScalarPatchPlan(
        action_type="replica_scale",
        source_type="raw-yaml",
        source_manifest_sha256="sha256:" + "b" * 64,
        expected_base_sha="a" * 40,
        manifest_path="deploy/k8s",
        replacements=(ScalarFieldReplacement("spec.replicas", 1, 2),),
        rollback_replacements=(ScalarFieldReplacement("spec.replicas", 2, 1),),
    )
    request = SafePrRequestedBody(
        title="restore api capacity",
        body="restore",
        provider="github",
        patches=[
            SafePrFilePatch(
                path=".gitops/safe-pr/patches/recovery.yaml",
                content=scalar_patch_content(plan),
            )
        ],
        workspace_id="workspace-1",
        repository_id="repo-1",
        binding_id="binding-1",
        application_id="app-1",
        workflow_run_id="workflow-1",
        environment="sandbox",
        manifest_path=plan.manifest_path,
        repo_ref="org/repo",
        base_branch="dev",
        commit_sha=plan.expected_base_sha,
        cluster_id="cluster-1",
        target_namespace="sandbox",
        target_resource="Deployment/api-server",
        target_authority="completed_workload_change",
    )

    class Db:
        async def get_workflow_run(self, workflow_run_id: str):
            return {
                "workflow_run_id": workflow_run_id,
                "workspace_id": "workspace-1",
                "application_id": "app-1",
                "binding_id": "binding-1",
                "environment": "sandbox",
                "commit_sha": "a" * 40,
            }

        async def get_workflow_step_details(self, *args: object):
            raise AssertionError("resource-scoped recovery must not read singleton diff")

        async def get_completed_workload_resource_diff(self, *args: object):
            assert args == (
                "workspace-1",
                "workflow-1",
                "binding-1",
                "cluster-1",
                "sandbox",
                "Deployment",
                "api-server",
            )
            diff = {
                "workspace_id": "workspace-1",
                "repository_id": "repo-1",
                "binding_id": "binding-1",
                "application_id": "app-1",
                "workflow_run_id": "workflow-1",
                "environment": "sandbox",
                "cluster_id": "cluster-1",
                "commit_sha": "a" * 40,
                "manifest_path": "deploy/k8s",
                "namespace": "sandbox",
                "resource": "Deployment/api-server",
                "desired_manifest": desired,
                "basis": {
                    "artifact_digest": digest,
                    "old_desired_source": "last_approved_snapshot",
                },
                "changes": [
                    {
                        "field_path": "spec.replicas",
                        "old_desired": 2,
                        "new_desired": 1,
                    }
                ],
            }
            return {
                "workspace_id": "workspace-1",
                "workflow_run_id": "workflow-1",
                "binding_id": "binding-1",
                "cluster_id": "cluster-1",
                "namespace": "sandbox",
                "resource_kind": "deployment",
                "resource_name": "api-server",
                "repository_id": "repo-1",
                "manifest_path": "deploy/k8s",
                "commit_sha": "a" * 40,
                "diff_details": diff,
            }

        async def get_manifest_artifact_provenance(self, *args: object):
            assert args[-2:] == ("Deployment/api-server", digest)
            return {
                "workspace_id": "workspace-1",
                "repository_id": "repo-1",
                "binding_id": "binding-1",
                "commit_sha": "a" * 40,
                "manifest_path": "deploy/k8s",
                "artifact_digest": digest,
                "source_manifest_sha256": "sha256:" + "b" * 64,
                "repo_ref": "org/repo",
                "branch": "dev",
            }

    db = Db()
    context = type("Context", (), {"db": db})()
    authority = asyncio.run(
        provider_module.GithubScmProvider().validate_structured_patch_authority(
            request,
            [plan],
            context,
        )
    )

    assert authority["desired_manifest"]["metadata"]["name"] == "api-server"

    async def missing_resource(*args: object):
        return None

    db.get_completed_workload_resource_diff = missing_resource  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="workflow authority"):
        asyncio.run(
            provider_module.GithubScmProvider().validate_structured_patch_authority(
                request,
                [plan],
                context,
            )
        )
