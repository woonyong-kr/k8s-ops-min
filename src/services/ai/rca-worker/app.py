"""rca-worker — rca.candidates.evaluated -> rca.completed."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from domains.rca.events import (
    RcaAnalysisBlockedBody,
    RcaCandidatesEvaluatedBody,
    RcaCompletedBody,
)
from domains.rca.report_narrative import (
    RCA_NARRATIVE_GENERATED,
    RCA_NARRATIVE_PAYLOAD_KEY,
    RCA_NARRATIVE_STATUS_KEY,
    RCA_NARRATIVE_UNAVAILABLE,
)
from packages.ai.llm import PROVIDER_UNCONFIGURED, build_llm_client, describe_llm_client
from packages.ai.metrics import metered_llm_client
from packages.config.logs import CONTEXT_KEY, get_logger
from packages.config.settings import env
from packages.contracts.event_bus.bodies import EventBody
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.stores import RcaStore
from packages.runtime.app import App, EventContext
from services.ai.agent.pipeline import RcaCompletionPipeline
from services.ai.agent.pipeline.rca_narrative import (
    RcaNarrativeWriter,
    deterministic_rca_narrative,
    evidence_anchored_narrative,
)

app = App("rca-worker")
pipeline = RcaCompletionPipeline()
llm_client = build_llm_client()
narrative_writer = RcaNarrativeWriter()
LOGGER = get_logger(__name__)

RCA_NARRATIVE_TIMEOUT_SECONDS_ENV = "RCA_NARRATIVE_TIMEOUT_SECONDS"
RCA_NARRATIVE_TIMEOUT_SECONDS = float(env(RCA_NARRATIVE_TIMEOUT_SECONDS_ENV, "15"))


@app.on(RcaCandidatesEvaluatedBody)
async def on_candidates_evaluated(
    evt: RcaCandidatesEvaluatedBody,
    ctx: EventContext[RcaStore],
) -> AsyncIterator[EventBody]:
    result = pipeline.complete_body(evt)
    if isinstance(result, RcaCompletedBody):
        # correlation_id is the public lookup key used by the incident detail UI.
        # Skipping persistence because a similar resource report exists leaves the
        # newly emitted incident with no report at all. Incident de-duplication belongs
        # at the incident projection boundary; every emitted correlation must retain
        # its own evidence-backed report.
        report_body = await enriched_report_body(result, ctx)
        await ctx.db.save_rca_report(
            ctx.correlation_id,
            result.workspace_id,
            result.root_cause,
            result.action,
            report_body,
        )
    elif isinstance(result, RcaAnalysisBlockedBody):
        # A blocked analysis is still a durable RCA outcome. Persist its ranked
        # candidates and evidence trail so the issue detail can explain why the
        # cause was not finalized and what evidence is still required.
        report_body = result.to_body()
        report_body["analysis_status"] = "blocked"
        await ctx.db.save_rca_report(
            ctx.correlation_id,
            result.workspace_id,
            "insufficient_evidence",
            "추가 근거 수집 후 RCA 재분석",
            report_body,
        )
    yield result


async def enriched_report_body(
    result: RcaCompletedBody,
    ctx: EventContext[RcaStore],
) -> JsonObject:
    """Best-effort narrative enrichment; deterministic RCA persistence always wins."""
    body = result.to_body()
    body["analysis_status"] = "completed"
    fallback = deterministic_rca_narrative(result)
    if fallback is None:
        body[RCA_NARRATIVE_STATUS_KEY] = RCA_NARRATIVE_UNAVAILABLE
    else:
        body[RCA_NARRATIVE_PAYLOAD_KEY] = fallback
        body[RCA_NARRATIVE_STATUS_KEY] = RCA_NARRATIVE_GENERATED
    try:
        if describe_llm_client(llm_client).get("provider") == PROVIDER_UNCONFIGURED:
            return body
        async with asyncio.timeout(RCA_NARRATIVE_TIMEOUT_SECONDS):
            narrative = await narrative_writer.write(
                result,
                metered_llm_client(
                    llm_client,
                    ctx.db,
                    workspace_id=result.workspace_id,
                    event_id=ctx.event_id,
                    correlation_id=ctx.correlation_id,
                    causation_id=ctx.causation_id,
                ),
            )
    except Exception as exc:
        # Never persist provider errors or model output.  The public status exposes
        # the backend gap without leaking credentials, endpoints, or prompt data.
        LOGGER.warning(
            "rca_narrative_llm_unavailable",
            extra={
                CONTEXT_KEY: {
                    "exception_type": type(exc).__name__,
                    "incident_id": result.incident.incident_id if result.incident else None,
                }
            },
        )
        return body
    body[RCA_NARRATIVE_PAYLOAD_KEY] = evidence_anchored_narrative(
        narrative,
        fallback,
    )
    body[RCA_NARRATIVE_STATUS_KEY] = RCA_NARRATIVE_GENERATED
    return body


if __name__ == "__main__":
    app.run()
