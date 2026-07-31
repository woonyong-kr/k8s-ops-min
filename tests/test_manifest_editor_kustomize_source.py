from __future__ import annotations

import asyncio

from domains.manifest_editor.router import resolve_kustomize_edit_source
from domains.manifest_editor.validation import ManifestIdentity


class FakeGitHubClient:
    def __init__(self) -> None:
        self.files = {
            "deploy/k8s/overlays/game-server/kustomization.yaml": b"""
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base
""",
            "deploy/k8s/base/kustomization.yaml": b"""
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - api-server.yaml
  - services.yaml
""",
            "deploy/k8s/base/api-server.yaml": b"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-server
  namespace: sandbox
spec:
  replicas: 1
""",
            "deploy/k8s/base/services.yaml": b"""
apiVersion: v1
kind: Service
metadata:
  name: api-server
  namespace: sandbox
""",
        }

    async def branch_sha(self, repo_ref: str, branch: str) -> str:
        assert repo_ref == "jungle-303-04/demo-game"
        assert branch == "main"
        return "d" * 40

    async def tree_at_revision(self, repo_ref: str, revision: str):
        assert repo_ref == "jungle-303-04/demo-game"
        assert revision == "d" * 40
        return (
            [
                {"type": "blob", "path": path}
                for path in self.files
            ],
            [],
        )

    async def content(self, repo_ref: str, revision: str, path: str) -> bytes:
        assert repo_ref == "jungle-303-04/demo-game"
        assert revision == "d" * 40
        return self.files[path]


def test_kustomize_binding_resolves_exact_editable_resource_file() -> None:
    source = {
        "provider": "github",
        "repo_ref": "jungle-303-04/demo-game",
        "branch": "main",
        "manifest_path": "deploy/k8s/overlays/game-server",
        "source_type": "kustomize",
    }

    resolved = asyncio.run(
        resolve_kustomize_edit_source(
            source,
            ManifestIdentity(
                api_version="apps/v1",
                kind="Deployment",
                namespace="sandbox",
                name="api-server",
            ),
            FakeGitHubClient(),
        ),
    )

    assert resolved["manifest_path"] == "deploy/k8s/base/api-server.yaml"
    assert resolved["source_type"] == "raw-yaml"
    assert resolved["render_source_type"] == "kustomize"
