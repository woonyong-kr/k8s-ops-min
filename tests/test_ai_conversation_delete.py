from __future__ import annotations

import asyncio
from types import SimpleNamespace

from domains.ai.router import delete_conversations


class StubAiConversationDb:
    def __init__(self) -> None:
        self.delete_all_calls: list[tuple[str, str]] = []

    def delete_ai_conversations(self, workspace_id: str, *, user_id: str) -> int:
        self.delete_all_calls.append((workspace_id, user_id))
        return 3


def test_delete_all_ai_conversations_is_scoped_to_signed_in_user() -> None:
    db = StubAiConversationDb()
    current = SimpleNamespace(workspace_id="workspace-1", user_id="user-1")

    response = asyncio.run(delete_conversations(current=current, db=db))

    assert response.status_code == 204
    assert db.delete_all_calls == [("workspace-1", "user-1")]
