"""고아 없는 상태 설계 — GitHub 수명주기 웹훅 → 저장소 상태 전이 + 다중 시크릿 서명.

순수 파서/적용기와 서명 검증을 스텁으로 고정한다(라이브 GitHub 없이 계약 검증).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac

import pytest
from fastapi import HTTPException

from domains.gitops import dependencies as deps
from domains.gitops.router import (
    apply_github_lifecycle,
    github_lifecycle_intents,
)

# --- 파서(순수) -------------------------------------------------------------


def test_installation_deleted_degrades_installation() -> None:
    intents = github_lifecycle_intents(
        {"action": "deleted", "installation": {"id": 42}}, "installation"
    )
    assert intents == [
        {"kind": "installation", "installation_id": "42", "status": "invalid_credential"}
    ]


def test_installation_created_is_noop() -> None:
    assert github_lifecycle_intents(
        {"action": "created", "installation": {"id": 42}}, "installation"
    ) == []


def test_installation_repositories_removed() -> None:
    intents = github_lifecycle_intents(
        {
            "action": "removed",
            "installation": {"id": 7},
            "repositories_removed": [{"full_name": "octo/repo"}, {"full_name": "octo/other"}],
        },
        "installation_repositories",
    )
    assert [i["repo_ref"] for i in intents] == ["octo/repo", "octo/other"]
    assert all(i["status"] == "invalid_credential" for i in intents)


def test_repository_deleted_is_source_unreachable() -> None:
    intents = github_lifecycle_intents(
        {"action": "deleted", "repository": {"full_name": "octo/repo"}}, "repository"
    )
    assert intents == [
        {"kind": "repo", "repo_ref": "octo/repo", "status": "source_unreachable"}
    ]


def test_repository_renamed_recovers_old_ref() -> None:
    intents = github_lifecycle_intents(
        {
            "action": "renamed",
            "repository": {"full_name": "octo/new-name"},
            "changes": {"repository": {"name": {"from": "old-name"}}},
        },
        "repository",
    )
    refs = [i["repo_ref"] for i in intents]
    assert "octo/old-name" in refs  # 우리가 저장한 이전 ref 를 반드시 포함
    assert all(i["status"] == "source_unreachable" for i in intents)


def test_unknown_event_is_noop() -> None:
    assert github_lifecycle_intents({"action": "deleted"}, "push") == []


# --- 적용기(스텁 DB) --------------------------------------------------------


class StubLifecycleDb:
    def __init__(self, by_credential: dict[str, list[dict]] | None = None) -> None:
        self._by_credential = by_credential or {}
        self.transitions: list[tuple[str, str]] = []
        self.existing_refs = {"octo/repo", "octo/other", "octo/old-name"}

    def list_repositories_by_credential_ref(self, _ws: str, credential_ref: str) -> list[dict]:
        return self._by_credential.get(credential_ref, [])

    def set_repository_connection_status(self, _ws: str, repo_ref: str, status: str):
        if repo_ref not in self.existing_refs:
            return None  # 멱등: 없는 저장소는 무동작
        self.transitions.append((repo_ref, status))
        return {"repo_ref": repo_ref, "status": status}


def test_apply_installation_transitions_bound_repos() -> None:
    db = StubLifecycleDb(
        by_credential={
            "github-app-installation:7": [
                {"repo_ref": "octo/repo"},
                {"repo_ref": "octo/other"},
            ]
        }
    )
    intents = [{"kind": "installation", "installation_id": "7", "status": "invalid_credential"}]
    affected = apply_github_lifecycle(db, intents)
    assert affected == 2
    assert ("octo/repo", "invalid_credential") in db.transitions
    assert ("octo/other", "invalid_credential") in db.transitions


def test_apply_repo_intent_missing_repo_is_idempotent() -> None:
    db = StubLifecycleDb()
    affected = apply_github_lifecycle(
        db, [{"kind": "repo", "repo_ref": "ghost/repo", "status": "source_unreachable"}]
    )
    assert affected == 0
    assert db.transitions == []


# --- 서명(다중 시크릿) ------------------------------------------------------


class FakeRequest:
    def __init__(self, body: bytes, signature: str) -> None:
        self._body = body
        self.headers = {deps.SIGNATURE_HEADER: signature}

    async def body(self) -> bytes:
        return self._body


def _sign(secret: str, body: bytes) -> str:
    return deps.SIGNATURE_PREFIX + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class _NoAppSecretDb:
    pass


def test_env_secret_still_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b'{"hello":"world"}'
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "env-secret")
    req = FakeRequest(body, _sign("env-secret", body))
    # env 시크릿만으로 통과(하위호환) — 예외가 없어야 한다.
    asyncio.run(deps.verify_github_signature(req, db=_NoAppSecretDb()))


def test_app_secret_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b'{"action":"deleted"}'
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "env-secret")

    # App 저장 시크릿을 반환하도록 resolve_webhook_secret 를 대체.
    import domains.scm.github_app_manifest as manifest

    monkeypatch.setattr(manifest, "resolve_webhook_secret", lambda _db, _ws: "app-secret")
    req = FakeRequest(body, _sign("app-secret", body))  # App 시크릿으로 서명
    asyncio.run(deps.verify_github_signature(req, db=_NoAppSecretDb()))


def test_wrong_signature_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b'{"x":1}'
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "env-secret")
    import domains.scm.github_app_manifest as manifest

    monkeypatch.setattr(manifest, "resolve_webhook_secret", lambda _db, _ws: "app-secret")
    req = FakeRequest(body, _sign("attacker", body))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(deps.verify_github_signature(req, db=_NoAppSecretDb()))
    assert exc.value.status_code == 401


def test_no_secret_configured_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b"{}"
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    import domains.scm.github_app_manifest as manifest

    monkeypatch.setattr(manifest, "resolve_webhook_secret", lambda _db, _ws: "")
    req = FakeRequest(body, _sign("whatever", body))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(deps.verify_github_signature(req, db=_NoAppSecretDb()))
    assert exc.value.status_code == 503
