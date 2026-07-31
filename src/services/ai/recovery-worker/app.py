"""recovery-worker — rca.completed -> recovery.planned."""

from __future__ import annotations

from collections.abc import AsyncIterator

from domains.rca.events import RcaCompletedBody
from packages.contracts.event_bus.bodies import EventBody
from packages.runtime.app import App
from services.ai.agent.pipeline import RecoveryPlanningPipeline

app = App("recovery-worker")
pipeline = RecoveryPlanningPipeline()


@app.on(RcaCompletedBody)
async def on_rca_completed(evt: RcaCompletedBody) -> AsyncIterator[EventBody]:
    yield pipeline.plan_body(evt)


if __name__ == "__main__":
    app.run()
