from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from domains.rca.repository import RcaRepository


class _Rows:
    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, Any]]:
        return []


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    def execute(self, statement: Any) -> _Rows:
        self.statements.append(statement)
        return _Rows()


@pytest.mark.parametrize(
    ("method_name", "extra_identity"),
    [
        (
            "find_open_recovery_plan_for_pull_request_base_identity",
            {},
        ),
        (
            "find_open_recovery_plan_for_pull_request",
            {"head_sha": "a" * 40},
        ),
    ],
)
def test_recovery_pr_repository_identity_is_case_insensitive(
    method_name: str,
    extra_identity: dict[str, str],
) -> None:
    repository = object.__new__(RcaRepository)
    connection = _RecordingConnection()

    @contextmanager
    def recording_connection():
        yield connection

    repository.connection = recording_connection  # type: ignore[method-assign]

    method = getattr(repository, method_name)
    method(
        pr_url="https://github.com/Jungle-303-04/demo-game/pull/18",
        repo_ref="Jungle-303-04/demo-game",
        base_branch="main",
        pr_number=18,
        pr_node_id="PR_kwDOExample",
        head_ref="gitops/workflow-example",
        **extra_identity,
    )

    assert len(connection.statements) == 1
    compiled = str(
        connection.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()
    assert compiled.count("lower(") == 1
    assert "jungle-303-04/demo-game" in compiled
