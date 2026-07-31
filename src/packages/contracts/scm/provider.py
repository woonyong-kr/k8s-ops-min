"""safe PR 생성 전략 계약."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ScmPullRequestResult:
    """SCM 쓰기 결과.

    Pull Request 전달은 merge webhook을 안전하게 대조할 수 있도록 GitHub가 발급한
    immutable identity와 생성 직후 head SHA를 함께 반환한다. direct commit 전달은
    ``url``만 채울 수 있다.
    """

    url: str
    number: int | None = None
    node_id: str = ""
    head_ref: str = ""
    head_sha: str = ""


class ScmProvider(Protocol):
    """safe PR 생성 outbound 경계 전략 — 성공 시 PR URL 을 반환함.

    request 는 domains.scm.events.SafePrRequestedBody, ctx 는 EventContext 임
    (레이어 규칙상 packages 는 domains 를 import 못 해 구조적 시그니처로 둠).
    """

    async def create_pull_request(self, request: Any, ctx: Any) -> ScmPullRequestResult: ...
