"""scm 도메인 repository — Pull Request 영속."""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert

from domains.scm.models import PullRequest
from packages.storage.engine import DatabaseConnection


class PullRequestRepository(DatabaseConnection):
    def save_pull_request(
        self, correlation_id: str, pr_url: str, title: str, body: str, status: str
    ) -> None:
        table = PullRequest.__table__
        statement = pg_insert(table).values(
            correlation_id=correlation_id, pr_url=pr_url, title=title, body=body, status=status
        )
        with self.connection() as conn:
            conn.execute(statement)
