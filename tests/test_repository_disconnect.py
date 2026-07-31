"""고아 없는 상태 설계 — 저장소 연결 해제/상태 파생 라우터 단위 검증(스텁 DB).

DB SQL 은 라이브에서 검증하되, 여기서는 라우터 계약을 스텁으로 고정한다:
  - 미등록 저장소 해제 → 404
  - 관리 권한 있는 해제 → disconnected + db.disconnect_repository 정확 호출
  - 상태 파생: source_unreachable/disconnected → degraded_reason 매핑
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from domains.applications.router import (
    disconnect_repository_connection,
    get_repository_connection_status,
)
from packages.contracts.gateway.requests import RepositoryDisconnectRequest


class StubDisconnectDb:
    def __init__(self, repository: dict[str, object] | None) -> None:
        self._repository = repository
        self.disconnect_calls: list[tuple[str, str]] = []

    def get_repository_by_ref(self, _workspace_id: str, _repo_ref: str):
        return self._repository

    # service_admin 세션은 애플리케이션 접근 검사를 우회하므로 목록은 쓰이지 않지만
    # 방어적으로 제공한다.
    def list_repository_applications(self, _workspace_id: str, _repository_id: str):
        return []

    def disconnect_repository(self, workspace_id: str, repo_ref: str):
        self.disconnect_calls.append((workspace_id, repo_ref))
        if self._repository is None:
            return None
        return {
            **self._repository,
            "status": "disconnected",
            "cascade": {
                "repository": 1,
                "watch_targets": 2,
                "bindings": 2,
                "applications": 2,
            },
            "credential_dropped": True,
        }


def _admin() -> SimpleNamespace:
    return SimpleNamespace(user_id="admin-1", roles=("service_admin",), workspace_id="ws-1")


def test_disconnect_unregistered_returns_404() -> None:
    db = StubDisconnectDb(repository=None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            disconnect_repository_connection(
                RepositoryDisconnectRequest(repo_ref="octo/repo"),
                current=_admin(),
                db=db,
            )
        )
    assert exc.value.status_code == 404
    assert db.disconnect_calls == []  # 미등록이면 DB 를 건드리지 않는다


def test_disconnect_registered_returns_disconnected_and_cascades() -> None:
    db = StubDisconnectDb(
        repository={"repository_id": "repo-1", "repo_ref": "octo/repo", "status": "active"}
    )
    response = asyncio.run(
        disconnect_repository_connection(
            RepositoryDisconnectRequest(repo_ref="octo/repo"),
            current=_admin(),
            db=db,
        )
    )
    assert response.repository_status == "disconnected"
    assert response.connection_stage == "error"
    assert response.terminal is True
    assert response.degraded_reason == "disconnected"
    assert response.repository_id == "repo-1"
    assert db.disconnect_calls == [("ws-1", "octo/repo")]


@pytest.mark.parametrize(
    ("stored_status", "expected_status", "expected_reason"),
    [
        ("active", "active", None),
        ("invalid_credential", "invalid_credential", "credential_invalid"),
        ("source_unreachable", "source_unreachable", "source_unreachable"),
        ("disconnected", "disconnected", "disconnected"),
        ("disabled", "disabled", "disabled"),
        ("weird-unknown", "unknown", None),
    ],
)
def test_status_derivation_maps_reason(
    stored_status: str, expected_status: str, expected_reason: str | None
) -> None:
    db = StubDisconnectDb(
        repository={"repository_id": "repo-1", "repo_ref": "octo/repo", "status": stored_status}
    )
    response = asyncio.run(
        get_repository_connection_status(
            repo_ref="octo/repo",
            current=_admin(),
            db=db,
        )
    )
    assert response.repository_status == expected_status
    assert response.degraded_reason == expected_reason
    assert response.connection_stage == ("ready" if expected_status == "active" else "error")
