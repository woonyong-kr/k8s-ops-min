"""폴러 App 설치 토큰 분기(Slice 3b) — 기존 PAT/vault/public 경로 무회귀 확인.

App 설치 참조면 동기 발급기로 단명 토큰을 얻고, 발급 실패면 빈 토큰으로 degrade
(폴러가 죽지 않음)한다. 발급기는 네트워크 없이 주입/모킹한다.
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import ROOT, load_file


def _load_poller() -> Any:
    return load_file(
        ROOT / "src" / "services" / "gitops" / "github-poll-worker" / "poller.py", "svc_poller"
    )


class _StubDb:
    def get_workspace_credential(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _app_target(module: Any) -> Any:
    return module.GitHubPollTarget(
        workspace_id="workspace-1",
        repository_id="repo-1",
        repo_ref="org/app-repo",
        credential_ref="github-app-installation:99",
        branch="main",
        watch_target_id="watch-1",
        binding_id="binding-1",
        application_id="app-1",
        environment="prod",
        cluster_id="cluster-1",
        manifest_path="deploy.yaml",
    )


def test_app_installation_ref_mints_token(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_poller()
    seen: dict[str, Any] = {}

    def fake_sync(db: Any, workspace_id: str, installation_id: str) -> str:
        seen["args"] = (workspace_id, installation_id)
        return "ghs_installation_token"

    monkeypatch.setattr(module, "resolve_installation_token_sync", fake_sync)
    poller = module.GitHubPoller(db=_StubDb())
    token = poller._github_token(_app_target(module))
    assert token == "ghs_installation_token"
    assert seen["args"] == ("workspace-1", "99")  # 참조에서 설치 id 정확 파싱


def test_app_not_configured_degrades_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_poller()

    def boom(*_a: Any, **_k: Any) -> str:
        raise module.GithubAppNotConfigured("no app")

    monkeypatch.setattr(module, "resolve_installation_token_sync", boom)
    poller = module.GitHubPoller(db=_StubDb())
    # 미구성이면 예외를 삼키고 빈 토큰(폴러 지속) — 예외 전파 금지.
    assert poller._github_token(_app_target(module)) == ""


def test_app_mint_failure_degrades_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_poller()

    def boom(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("network down")

    monkeypatch.setattr(module, "resolve_installation_token_sync", boom)
    poller = module.GitHubPoller(db=_StubDb())
    assert poller._github_token(_app_target(module)) == ""


def test_non_app_ref_still_uses_public_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """무회귀: public 참조는 App 분기를 타지 않고 종전대로 빈 토큰."""
    module = _load_poller()
    called = {"sync": False}

    def spy(*_a: Any, **_k: Any) -> str:
        called["sync"] = True
        return "should-not-be-used"

    monkeypatch.setattr(module, "resolve_installation_token_sync", spy)
    from packages.contracts.gitops import PUBLIC_GITHUB_CREDENTIAL_REF

    target = module.GitHubPollTarget(
        workspace_id="workspace-1",
        repository_id="repo-1",
        repo_ref="org/public",
        credential_ref=PUBLIC_GITHUB_CREDENTIAL_REF,
        branch="main",
        watch_target_id="watch-1",
        binding_id="binding-1",
        application_id="app-1",
        environment="prod",
        cluster_id="cluster-1",
        manifest_path="deploy.yaml",
    )
    poller = module.GitHubPoller(db=_StubDb())
    assert poller._github_token(target) == ""
    assert called["sync"] is False  # App 분기 미진입(무회귀)
