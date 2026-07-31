"""ai-fallback-worker — rca.ai_fallback.requested -> LLM 후보 기반 rca.candidates.planned.

rule 미매칭 incident 를 LLM 에게 물어 원인 후보(source="ai_fallback")를 만들고,
plan-worker 와 같은 `rca.candidates.planned` 이벤트로 되돌려 기존
analyze-worker(평가) → rca-worker(확정) 경로를 그대로 태운다.

실패 정책: LLM 미설정(ValueError)·호출 실패·JSON 비정형은 로그만 남기고 아무
이벤트도 내지 않는다 — 확정 root cause 를 지어내지 않으며, rule 개선 backlog 는
plan-worker 가 이미 `rca.backlog.created` 로 남겨 두었다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from domains.rca.events import RcaAiFallbackRequestedBody
from packages.ai.llm import build_llm_client
from packages.ai.metrics import metered_llm_client
from packages.config.logs import CONTEXT_KEY, get_logger
from packages.contracts.event_bus.bodies import EventBody
from packages.runtime.app import App, EventContext
from services.ai.agent.pipeline import AiFallbackPlanner

app = App("ai-fallback-worker")
llm_client = build_llm_client()
planner = AiFallbackPlanner()
LOGGER = get_logger(__name__)


@app.on(RcaAiFallbackRequestedBody)
async def on_ai_fallback_requested(
    evt: RcaAiFallbackRequestedBody,
    ctx: EventContext[object],
) -> AsyncIterator[EventBody]:
    try:
        planned = await planner.plan_body(
            evt,
            metered_llm_client(
                llm_client,
                ctx.db,
                workspace_id=evt.workspace_id,
                event_id=ctx.event_id,
                correlation_id=ctx.correlation_id,
                causation_id=ctx.causation_id,
            ),
        )
    except Exception as exc:
        LOGGER.warning(
            "ai_fallback_llm_failed",
            extra={
                CONTEXT_KEY: {
                    "exception_type": type(exc).__name__,
                    "incident_id": evt.incident.incident_id,
                    "evidence_ref": evt.evidence_ref,
                }
            },
            exc_info=exc,
        )
        return
    if planned is None:
        LOGGER.info(
            "ai_fallback_no_candidates",
            extra={
                CONTEXT_KEY: {
                    "incident_id": evt.incident.incident_id,
                    "evidence_ref": evt.evidence_ref,
                }
            },
        )
        return
    yield planned


if __name__ == "__main__":
    app.run()
