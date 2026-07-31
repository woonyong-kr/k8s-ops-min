"""select-worker — recovery.planned -> recovery.action_selected or selection request."""

from __future__ import annotations

from collections.abc import AsyncIterator

from domains.rca.events import (
    RecoveryActionSelectedBody,
    RecoveryPlannedBody,
    RecoverySelectionRequestedBody,
)
from packages.contracts.event_bus.bodies import EventBody
from packages.contracts.stores import RecoveryPlanStore
from packages.runtime.app import App, EventContext
from services.ai.agent.recovery.select import RecoverySelector

app = App("select-worker")
selector = RecoverySelector()


@app.on(RecoveryPlannedBody)
async def on_recovery_planned(
    evt: RecoveryPlannedBody,
    ctx: EventContext[RecoveryPlanStore],
) -> AsyncIterator[EventBody]:
    body = selector.select_body(evt)
    if ctx.db is not None:
        if isinstance(body, RecoverySelectionRequestedBody):
            await ctx.db.upsert_recovery_plan(
                ctx.correlation_id,
                body.workspace_id,
                body.plan.to_body(),
                status="selection_requested",
            )
        elif isinstance(body, RecoveryActionSelectedBody):
            await ctx.db.upsert_recovery_plan(
                ctx.correlation_id,
                body.workspace_id,
                body.plan.to_body(),
                status="selected",
                selected_action_id=body.selected.action_id,
                selected_by=body.selected_by,
            )
    yield body


if __name__ == "__main__":
    app.run()
