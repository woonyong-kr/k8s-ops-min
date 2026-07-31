"""GitHub App 서버 보관본 제거(오프보딩) — 관리자 게이트 + 삭제 위임 검증."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from domains.scm.app_router import github_app_uninstall


class StubAppDb:
    def __init__(self, delete_result: bool = True) -> None:
        self.delete_result = delete_result
        self.delete_calls: list[tuple[str, str, str]] = []

    def delete_workspace_credential(self, workspace_id: str, provider: str, scope: str) -> bool:
        self.delete_calls.append((workspace_id, provider, scope))
        return self.delete_result


def test_non_admin_forbidden() -> None:
    db = StubAppDb()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            github_app_uninstall(
                current=SimpleNamespace(user_id="u1", roles=("user",)),
                db=db,
            )
        )
    assert exc.value.status_code == 403
    assert db.delete_calls == []  # 비관리자는 삭제를 시도조차 하지 않는다


def test_admin_removes_stored_config(monkeypatch: pytest.MonkeyPatch) -> None:
    # env 폴백이 없다고 가정 → env_fallback_active=False
    import domains.scm.github_app as app_mod

    monkeypatch.setattr(
        app_mod,
        "load_github_app_config",
        lambda: SimpleNamespace(configured=False),
    )
    db = StubAppDb(delete_result=True)
    response = asyncio.run(
        github_app_uninstall(
            current=SimpleNamespace(user_id="admin", roles=("service_admin",)),
            db=db,
        )
    )
    assert response.removed is True
    assert response.env_fallback_active is False
    assert db.delete_calls == [("default", "github-app", "platform")]
