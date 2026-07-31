"""plan-worker — evidence.bundle.built -> rca.candidates.planned."""

from __future__ import annotations

from collections.abc import AsyncIterator

from domains.rca.events import EvidenceBundleBuiltBody, RcaCandidatesPlannedBody
from packages.contracts.event_bus.bodies import EventBody
from packages.contracts.stores import RcaBacklogStore
from packages.runtime.app import App, EventContext
from services.ai.agent.pipeline import CausePlanningPipeline

app = App("plan-worker")
pipeline = CausePlanningPipeline()
BACKLOG_RULE_RESOLVED_REASON = "matching RCA rule is now available"


@app.on(EvidenceBundleBuiltBody)
async def on_evidence_bundle_built(
    evt: EvidenceBundleBuiltBody,
    ctx: EventContext[RcaBacklogStore],
) -> AsyncIterator[EventBody]:
    bodies = pipeline.plan_bodies(evt)
    planned = next((body for body in bodies if isinstance(body, RcaCandidatesPlannedBody)), None)
    if (
        planned is not None
        and planned.rule_missing is None
        and evt.incident is not None
        and ctx.db is not None
    ):
        # 증상에 맞는 RCA rule 이 생기면 과거 missing-rule backlog 를 자동 해소한다.
        await ctx.db.resolve_rca_backlog_item_for_rule(
            evt.evidence.workspace_id,
            evt.incident.symptom,
            BACKLOG_RULE_RESOLVED_REASON,
        )
    for body in bodies:
        yield body


if __name__ == "__main__":
    app.run()
