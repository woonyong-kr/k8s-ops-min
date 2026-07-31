from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from conftest import ROOT, load_file

from domains.gitops.source_patch import (
    DeclaredScalarPatch,
    ManifestScalarPatchPlan,
    ScalarFieldReplacement,
    scalar_patch_content,
)
from domains.manifest_editor.validation import ManifestIdentity
from domains.scm.events import SafePrFilePatch, SafePrRequestedBody

APPROVED_SHA = "a" * 40
CURRENT_SHA = "b" * 40
PR_BASE_SHA = "e" * 40
PR_HEAD_SHA = "d" * 40
SOURCE_PATH = "deploy/k8s/base/game.yaml"
OTHER_SOURCE_PATH = "deploy/k8s/base/other-game.yaml"


@pytest.fixture(scope="module")
def provider_module():
    return load_file(
        Path(ROOT) / "src/services/gitops/scm-worker/github_provider.py",
        "test_safe_pr_structured_base_advance_provider",
    )


def _plan() -> ManifestScalarPatchPlan:
    replacement = ScalarFieldReplacement("spec.replicas", 1, 2)
    return ManifestScalarPatchPlan(
        action_type="replica_scale",
        source_type="raw-yaml",
        source_manifest_sha256="sha256:" + "c" * 64,
        expected_base_sha=APPROVED_SHA,
        manifest_path="deploy/k8s/overlays/game-server",
        replacements=(replacement,),
        rollback_replacements=(
            ScalarFieldReplacement("spec.replicas", 2, 1),
        ),
    )


def _request(plan: ManifestScalarPatchPlan) -> SafePrRequestedBody:
    return SafePrRequestedBody(
        title="Restore lobby capacity",
        body="Restore the approved replica count.",
        provider="github",
        patches=[
            SafePrFilePatch(
                path=".gitops/safe-pr/patches/recovery.yaml",
                content=scalar_patch_content(plan),
            )
        ],
        workflow_run_id="workflow-safe-pr-rebase",
        manifest_path=plan.manifest_path,
        repo_ref="org/repo",
        base_branch="main",
        commit_sha=APPROVED_SHA,
        delivery="pull_request",
    )


def test_branch_name_is_stable_per_approval_and_changes_for_retry(
    provider_module,
) -> None:
    request = _request(_plan())
    first = replace(request, approval_ref="approval-attempt-1")
    redelivery = replace(request, approval_ref="approval-attempt-1")
    retry = replace(request, approval_ref="approval-attempt-2")

    first_branch = provider_module.branch_name(first)

    assert first_branch == provider_module.branch_name(redelivery)
    assert first_branch != provider_module.branch_name(retry)
    assert first_branch.startswith("gitops/workflow-safe-pr-rebase-")
    assert provider_module.branch_name(request) == "gitops/workflow-safe-pr-rebase"


def _declared(
    plan: ManifestScalarPatchPlan,
    *,
    source_path: str = SOURCE_PATH,
) -> DeclaredScalarPatch:
    return DeclaredScalarPatch(
        source_type="raw-yaml",
        source_path=source_path,
        replacements=plan.replacements,
    )


def _deployment(replicas: int, *, release_note: str | None = None) -> str:
    annotation = (
        f"  annotations:\n    demo.opsia.dev/release-note: {release_note}\n"
        if release_note is not None
        else ""
    )
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-server
  namespace: sandbox
{annotation}\
spec:
  replicas: {replicas}
"""


def _encoded_content(content: str) -> dict[str, str]:
    return {
        "encoding": "base64",
        "content": base64.b64encode(content.encode()).decode(),
    }


def _compare_payload(
    *,
    status: str = "ahead",
    merge_base_sha: str = APPROVED_SHA,
    changed_path: str = SOURCE_PATH,
) -> dict[str, object]:
    return {
        "status": status,
        "merge_base_commit": {"sha": merge_base_sha},
        "files": [{"filename": changed_path, "status": "modified"}],
    }


def test_unrelated_descendant_change_creates_pr_from_current_base(provider_module) -> None:
    """A later unrelated commit is retained by branching from the current base."""

    plan = _plan()
    request = _request(plan)
    declared = _declared(plan)
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    written_files: dict[str, str] = {}

    def handler(raw_request: httpx.Request) -> httpx.Response:
        path = raw_request.url.path
        body = (
            json.loads(raw_request.content)
            if raw_request.content
            else None
        )
        calls.append((raw_request.method, path, body))
        if raw_request.method == "GET" and path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": CURRENT_SHA}})
        if raw_request.method == "GET" and path.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if raw_request.method == "GET" and "/compare/" in path:
            assert path.endswith(f"/compare/{APPROVED_SHA}...{CURRENT_SHA}")
            return httpx.Response(200, json=_compare_payload())
        if raw_request.method == "GET" and path.endswith(f"/contents/{SOURCE_PATH}"):
            assert raw_request.url.params["ref"] == CURRENT_SHA
            return httpx.Response(
                200,
                json=_encoded_content(
                    _deployment(1, release_note="unrelated-change"),
                ),
            )
        if raw_request.method == "POST" and path.endswith("/git/refs"):
            return httpx.Response(201, json={"ref": "refs/heads/gitops/workflow-safe-pr-rebase"})
        if raw_request.method == "PUT" and "/contents/" in path:
            assert body is not None
            written_files[path] = base64.b64decode(str(body["content"])).decode()
            return httpx.Response(201, json={"content": {"sha": "blob-sha"}})
        if raw_request.method == "POST" and path.endswith("/pulls"):
            return httpx.Response(
                201,
                json={
                    "html_url": "https://github.test/org/repo/pull/7",
                    "number": 7,
                    "head": {
                        "ref": "gitops/workflow-safe-pr-rebase",
                        "sha": "d" * 40,
                    },
                },
            )
        return httpx.Response(
            404,
            json={"message": f"unexpected {raw_request.method} {path}"},
        )

    class Db:
        def __init__(self) -> None:
            self.saved: list[tuple[object, ...]] = []

        async def save_pull_request(self, *args: object) -> None:
            self.saved.append(args)

    db = Db()
    context = SimpleNamespace(
        db=db,
        event_id="event-1",
        correlation_id="correlation-1",
        causation_id="causation-1",
    )
    provider = provider_module.GithubScmProvider(
        transport=httpx.MockTransport(handler),
    )
    provider.github_token = lambda: "token"
    provider.validate_structured_patch_authority = AsyncMock(return_value={})
    provider.resolve_declared_patches = AsyncMock(return_value=[declared])

    result = asyncio.run(provider.create_pull_request(request, context))

    branch_call = next(
        body
        for method, path, body in calls
        if method == "POST" and path.endswith("/git/refs")
    )
    assert branch_call is not None
    assert branch_call["sha"] == CURRENT_SHA
    assert result.url == "https://github.test/org/repo/pull/7"
    assert db.saved
    patched_source = next(
        content
        for path, content in written_files.items()
        if path.endswith(f"/contents/{SOURCE_PATH}")
    )
    assert "replicas: 2" in patched_source
    assert "replicas: 1" not in patched_source
    assert "demo.opsia.dev/release-note: unrelated-change" in patched_source


def test_advanced_base_with_changed_target_scalar_fails_closed(provider_module) -> None:
    plan = _plan()
    request = _request(plan)
    declared = _declared(plan)

    def handler(raw_request: httpx.Request) -> httpx.Response:
        if "/compare/" in raw_request.url.path:
            return httpx.Response(200, json=_compare_payload())
        if raw_request.url.path.endswith(f"/contents/{SOURCE_PATH}"):
            assert raw_request.url.params["ref"] == CURRENT_SHA
            return httpx.Response(200, json=_encoded_content(_deployment(3)))
        return httpx.Response(404, json={"message": "unexpected request"})

    provider = provider_module.GithubScmProvider()
    provider.resolve_declared_patches = AsyncMock(return_value=[declared])

    async def validate() -> None:
        async with httpx.AsyncClient(
            base_url="https://api.github.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            await provider.validate_structured_base_advance(
                client,
                "org/repo",
                APPROVED_SHA,
                CURRENT_SHA,
                request,
                [plan],
                authority={},
            )

    with pytest.raises(
        RuntimeError,
        match=re.escape(provider_module.STALE_TARGET_MESSAGE),
    ):
        asyncio.run(validate())


def test_advanced_base_already_at_desired_scalar_creates_document_only_change(
    provider_module,
) -> None:
    """A completed target change keeps its audit PR without rewriting the manifest."""

    plan = _plan()
    request = _request(plan)
    declared = _declared(plan)

    def handler(raw_request: httpx.Request) -> httpx.Response:
        if "/compare/" in raw_request.url.path:
            return httpx.Response(200, json=_compare_payload())
        if raw_request.url.path.endswith(f"/contents/{SOURCE_PATH}"):
            assert raw_request.url.params["ref"] == CURRENT_SHA
            return httpx.Response(200, json=_encoded_content(_deployment(2)))
        return httpx.Response(404, json={"message": "unexpected request"})

    provider = provider_module.GithubScmProvider()
    provider.resolve_declared_patches = AsyncMock(return_value=[declared])

    async def validate() -> list[tuple[str, str]]:
        async with httpx.AsyncClient(
            base_url="https://api.github.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            return await provider.validate_structured_base_advance(
                client,
                "org/repo",
                APPROVED_SHA,
                CURRENT_SHA,
                request,
                [plan],
                authority={},
            )

    assert asyncio.run(validate()) == []


def test_advanced_base_preserves_unrelated_documents_in_selected_source(
    provider_module,
) -> None:
    plan = _plan()
    request = _request(plan)
    declared = DeclaredScalarPatch(
        source_type="raw-yaml",
        source_path=SOURCE_PATH,
        replacements=plan.replacements,
        document_identity=ManifestIdentity(
            "apps/v1",
            "Deployment",
            "sandbox",
            "api-server",
        ),
    )
    service = """\
apiVersion: v1
kind: Service
metadata:
  name: api-server
  namespace: sandbox
spec:
  selector:
    app: api-server
"""
    source = f"{_deployment(1, release_note='unrelated-change')}---\n{service}"

    def handler(raw_request: httpx.Request) -> httpx.Response:
        if "/compare/" in raw_request.url.path:
            return httpx.Response(200, json=_compare_payload())
        if raw_request.url.path.endswith(f"/contents/{SOURCE_PATH}"):
            assert raw_request.url.params["ref"] == CURRENT_SHA
            return httpx.Response(200, json=_encoded_content(source))
        return httpx.Response(404, json={"message": "unexpected request"})

    provider = provider_module.GithubScmProvider()
    provider.resolve_declared_patches = AsyncMock(return_value=[declared])

    async def validate() -> list[tuple[str, str]]:
        async with httpx.AsyncClient(
            base_url="https://api.github.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            return await provider.validate_structured_base_advance(
                client,
                "org/repo",
                APPROVED_SHA,
                CURRENT_SHA,
                request,
                [plan],
                authority={},
            )

    contents = asyncio.run(validate())

    assert contents[0][0] == SOURCE_PATH
    assert "replicas: 2" in contents[0][1]
    assert service in contents[0][1]
    assert "demo.opsia.dev/release-note: unrelated-change" in contents[0][1]


def test_advanced_base_with_changed_declared_source_fails_closed(provider_module) -> None:
    plan = _plan()
    request = _request(plan)
    approved = _declared(plan)
    current = _declared(plan, source_path=OTHER_SOURCE_PATH)

    def handler(raw_request: httpx.Request) -> httpx.Response:
        if "/compare/" in raw_request.url.path:
            return httpx.Response(200, json=_compare_payload())
        return httpx.Response(404, json={"message": "unexpected request"})

    provider = provider_module.GithubScmProvider()
    provider.resolve_declared_patches = AsyncMock(
        side_effect=[[approved], [current]],
    )

    async def validate() -> None:
        async with httpx.AsyncClient(
            base_url="https://api.github.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            await provider.validate_structured_base_advance(
                client,
                "org/repo",
                APPROVED_SHA,
                CURRENT_SHA,
                request,
                [plan],
                authority={},
            )

    with pytest.raises(
        RuntimeError,
        match=re.escape(provider_module.STALE_TARGET_MESSAGE),
    ):
        asyncio.run(validate())


def test_advanced_base_preserves_source_resolution_failure(provider_module) -> None:
    plan = _plan()
    request = _request(plan)
    message = (
        "remediation source patch unsupported: "
        "Kustomize resource source is missing or ambiguous"
    )

    def handler(raw_request: httpx.Request) -> httpx.Response:
        if "/compare/" in raw_request.url.path:
            return httpx.Response(200, json=_compare_payload())
        return httpx.Response(404, json={"message": "unexpected request"})

    provider = provider_module.GithubScmProvider()
    provider.resolve_declared_patches = AsyncMock(
        side_effect=RuntimeError(message),
    )

    async def validate() -> None:
        async with httpx.AsyncClient(
            base_url="https://api.github.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            await provider.validate_structured_base_advance(
                client,
                "org/repo",
                APPROVED_SHA,
                CURRENT_SHA,
                request,
                [plan],
                authority={},
            )

    with pytest.raises(RuntimeError, match=re.escape(message)):
        asyncio.run(validate())


def test_existing_pr_remains_idempotent_after_base_advances(provider_module) -> None:
    """A redelivery reuses an exact open PR even when main advanced afterward."""

    plan = _plan()
    request = _request(plan)
    patched_source = _deployment(2, release_note="preserved")
    change_path = provider_module.change_document_path(request)
    change_content = provider_module.change_document(request)

    def handler(raw_request: httpx.Request) -> httpx.Response:
        path = raw_request.url.path
        if "/compare/" in path:
            assert path.endswith(f"/compare/{CURRENT_SHA}...{PR_HEAD_SHA}")
            return httpx.Response(
                200,
                json={
                    "status": "diverged",
                    "merge_base_commit": {"sha": PR_BASE_SHA},
                    "files": [
                        {"filename": SOURCE_PATH, "status": "modified"},
                        {"filename": change_path, "status": "added"},
                    ],
                },
            )
        if path.endswith(f"/contents/{SOURCE_PATH}"):
            assert raw_request.url.params["ref"] == PR_HEAD_SHA
            return httpx.Response(200, json=_encoded_content(patched_source))
        if path.endswith(f"/contents/{change_path}"):
            assert raw_request.url.params["ref"] == PR_HEAD_SHA
            return httpx.Response(200, json=_encoded_content(change_content))
        return httpx.Response(404, json={"message": "unexpected request"})

    provider = provider_module.GithubScmProvider()
    provider.validate_structured_base_advance = AsyncMock(
        side_effect=[
            [(SOURCE_PATH, patched_source)],
            [(SOURCE_PATH, patched_source)],
        ]
    )

    async def verify() -> bool:
        async with httpx.AsyncClient(
            base_url="https://api.github.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            return await provider.verify_existing_structured_pr(
                client,
                "org/repo",
                request,
                [plan],
                {"head": {"sha": PR_HEAD_SHA}},
                CURRENT_SHA,
                authority={},
            )

    assert asyncio.run(verify()) is True
    assert provider.validate_structured_base_advance.await_count == 2


@pytest.mark.parametrize(
    ("status", "merge_base_sha"),
    [
        ("diverged", APPROVED_SHA),
        ("ahead", "e" * 40),
    ],
    ids=["diverged", "different-merge-base"],
)
def test_non_descendant_base_fails_before_source_resolution(
    provider_module,
    status: str,
    merge_base_sha: str,
) -> None:
    plan = _plan()
    request = _request(plan)

    def handler(raw_request: httpx.Request) -> httpx.Response:
        if "/compare/" in raw_request.url.path:
            return httpx.Response(
                200,
                json=_compare_payload(
                    status=status,
                    merge_base_sha=merge_base_sha,
                ),
            )
        return httpx.Response(404, json={"message": "unexpected request"})

    provider = provider_module.GithubScmProvider()
    provider.resolve_declared_patches = AsyncMock()

    async def validate() -> None:
        async with httpx.AsyncClient(
            base_url="https://api.github.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            await provider.validate_structured_base_advance(
                client,
                "org/repo",
                APPROVED_SHA,
                CURRENT_SHA,
                request,
                [plan],
                authority={},
            )

    with pytest.raises(
        RuntimeError,
        match=re.escape(provider_module.STALE_BASE_MESSAGE),
    ):
        asyncio.run(validate())
    provider.resolve_declared_patches.assert_not_awaited()
