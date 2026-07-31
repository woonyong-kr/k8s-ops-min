"""analyze-worker — rca.candidates.planned -> rca.candidates.evaluated."""

from __future__ import annotations

from collections.abc import AsyncIterator

from domains.rca.events import RcaCandidatesPlannedBody
from packages.contracts.event_bus.bodies import EventBody
from packages.runtime.app import App
from services.ai.agent.pipeline import CauseEvaluationPipeline

app = App("analyze-worker")
pipeline = CauseEvaluationPipeline()


@app.on(RcaCandidatesPlannedBody)
async def on_candidates_planned(
    evt: RcaCandidatesPlannedBody,
) -> AsyncIterator[EventBody]:
    yield pipeline.evaluate_body(evt)


if __name__ == "__main__":
    app.run()
