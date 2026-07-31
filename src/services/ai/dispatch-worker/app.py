"""dispatch-worker — recovery.action_selected -> command or PR request."""

from __future__ import annotations

from collections.abc import AsyncIterator

from domains.rca.events import RecoveryActionSelectedBody
from packages.contracts.event_bus.bodies import EventBody
from packages.runtime.app import App, EventContext
from services.ai.agent.recovery.authority import DatabaseGitOpsAuthorityReadPort
from services.ai.agent.recovery.dispatch import RecoveryDispatcher

app = App("dispatch-worker")
dispatcher = RecoveryDispatcher()


@app.on(RecoveryActionSelectedBody)
async def on_recovery_action_selected(
    evt: RecoveryActionSelectedBody,
    ctx: EventContext[object],
) -> AsyncIterator[EventBody]:
    authority = DatabaseGitOpsAuthorityReadPort(ctx.db) if ctx.db is not None else None
    yield await dispatcher.dispatch_body(
        evt,
        authority=authority,
        correlation_id=ctx.correlation_id,
    )


if __name__ == "__main__":
    app.run()
