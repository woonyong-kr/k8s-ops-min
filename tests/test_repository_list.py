"""연결 상태 관리 목록 — 전체 저장소를 상태·사유와 함께 반환(라우터 단위)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from domains.applications.router import list_workspace_repositories


class StubRepoListDb:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.calls: list[str] = []

    def list_repositories(self, workspace_id: str):
        self.calls.append(workspace_id)
        return self._rows


def _run(db):
    return asyncio.run(
        list_workspace_repositories(
            current=SimpleNamespace(user_id="u", roles=("user",), workspace_id="ws-1"),
            db=db,
        )
    )


def test_maps_status_and_reason_and_counts() -> None:
    db = StubRepoListDb(
        [
            {"repo_ref": "o/active", "repository_id": "r1", "provider": "github", "default_branch": "main", "status": "active", "application_count": 3},
            {"repo_ref": "o/cred", "repository_id": "r2", "provider": "github", "default_branch": "main", "status": "invalid_credential", "application_count": 1},
            {"repo_ref": "o/gone", "repository_id": "r3", "provider": "github", "default_branch": "main", "status": "source_unreachable", "application_count": 0},
            {"repo_ref": "o/off", "repository_id": "r4", "provider": "github", "default_branch": "main", "status": "disconnected", "application_count": 0},
            {"repo_ref": "o/weird", "repository_id": "r5", "provider": "github", "default_branch": "main", "status": "mystery", "application_count": 0},
        ]
    )
    response = _run(db)
    by_ref = {item.repo_ref: item for item in response.repositories}
    assert by_ref["o/active"].repository_status == "active"
    assert by_ref["o/active"].degraded_reason is None
    assert by_ref["o/active"].application_count == 3
    assert by_ref["o/cred"].degraded_reason == "credential_invalid"
    assert by_ref["o/gone"].repository_status == "source_unreachable"
    assert by_ref["o/off"].degraded_reason == "disconnected"
    # 알 수 없는 상태는 unknown 으로 정규화, 사유 없음.
    assert by_ref["o/weird"].repository_status == "unknown"
    assert by_ref["o/weird"].degraded_reason is None
    assert db.calls == ["ws-1"]


def test_missing_db_method_returns_empty() -> None:
    response = _run(SimpleNamespace())  # list_repositories 없음 → degrade
    assert response.repositories == []
