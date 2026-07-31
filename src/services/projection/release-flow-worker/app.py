"""release-flow-worker — workflow/agent/RCA events -> release-run read model."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from domains.command.events import (
    CommandCompletedBody,
    CommandDispatchedBody,
    CommandQueuedForAgentBody,
    CommandRejectedBody,
    CommandRequestedBody,
)
from domains.gitops.events import (
    ApprovalGrantedBody,
    ApprovalRejectedBody,
    ApprovalRequestedBody,
    WorkflowRunCompletedBody,
    WorkflowRunFailedBody,
    WorkflowRunStartedBody,
    WorkflowStepRecordedBody,
)
from domains.rca.events import (
    ClusterEvidenceReceivedBody,
    EvidenceBuiltBody,
    EvidenceBundleBuiltBody,
    IncidentDetectedBody,
    RcaActionRequiredBody,
    RcaAnalysisBlockedBody,
    RcaCandidatesEvaluatedBody,
    RcaCandidatesPlannedBody,
    RcaCompletedBody,
    RecoveryActionSelectedBody,
    RecoveryPlannedBody,
    RecoverySelectionRequestedBody,
    SafePrPatchPreparedBody,
)
from domains.release_flow.projection import (
    evidence_queued_update,
    release_alert_request,
    release_failure_evidence_request,
    release_workflow_update_from_event,
)
from domains.scm.events import (
    SafePrCreatedBody,
    SafePrFailedBody,
    SafePrReadyForCreationBody,
    SafePrRequestedBody,
)
from domains.target.events import EvidenceJobsQueuedBody, EvidenceJobUpdatedBody
from domains.target.evidence_policy import (
    DEMO_EVIDENCE_PROFILE,
    evidence_provider_queries,
)
from packages.contracts.event_bus.bodies.base import EventBody
from packages.contracts.event_bus.interfaces import EventEnvelope
from packages.contracts.stores import ReleaseFlowStore
from packages.runtime.app import App, EventContext

app = App("release-flow-worker")
RCA_TEST_LOG_RANGE_SECONDS = 120
MAX_RCA_TEST_LOG_PODS = 16
RCA_TEST_LOG_PATTERN = "ERROR|FATAL|panic|error|failed"


@app.on(CommandCompletedBody)
@app.on(CommandRejectedBody)
@app.on(CommandQueuedForAgentBody)
@app.on(CommandDispatchedBody)
@app.on(CommandRequestedBody)
@app.on(EvidenceJobsQueuedBody)
@app.on(SafePrFailedBody)
@app.on(SafePrCreatedBody)
@app.on(SafePrReadyForCreationBody)
@app.on(SafePrRequestedBody)
@app.on(SafePrPatchPreparedBody)
@app.on(RecoveryActionSelectedBody)
@app.on(RecoverySelectionRequestedBody)
@app.on(RecoveryPlannedBody)
@app.on(RcaActionRequiredBody)
@app.on(RcaAnalysisBlockedBody)
@app.on(RcaCompletedBody)
@app.on(RcaCandidatesEvaluatedBody)
@app.on(RcaCandidatesPlannedBody)
@app.on(EvidenceBundleBuiltBody)
@app.on(IncidentDetectedBody)
@app.on(EvidenceBuiltBody)
@app.on(ClusterEvidenceReceivedBody)
@app.on(EvidenceJobUpdatedBody)
@app.on(WorkflowRunFailedBody)
@app.on(WorkflowRunCompletedBody)
@app.on(ApprovalRejectedBody)
@app.on(ApprovalGrantedBody)
@app.on(ApprovalRequestedBody)
@app.on(WorkflowStepRecordedBody)
@app.on(WorkflowRunStartedBody)
async def on_event(
    evt: EventBody | EventEnvelope,
    ctx: EventContext[ReleaseFlowStore],
) -> AsyncIterator[EventBody]:
    envelope = evt if isinstance(evt, EventEnvelope) else envelope_from_body(evt, ctx)
    async for body in project_event(envelope, ctx):
        yield body


def envelope_from_body(evt: EventBody, ctx: EventContext[ReleaseFlowStore]) -> EventEnvelope:
    return EventEnvelope(
        event_id=ctx.event_id,
        subject=ctx.subject,
        source=ctx.source,
        correlation_id=ctx.correlation_id,
        causation_id=ctx.causation_id,
        created_at=ctx.created_at,
        payload=evt.to_body(),
        workspace_id=ctx.workspace_id,
    )


async def project_event(
    evt: EventEnvelope,
    ctx: EventContext[ReleaseFlowStore],
) -> AsyncIterator[EventBody]:
    test_evidence_request = rca_test_evidence_request(evt)
    if test_evidence_request is not None:
        queued = await ctx.db.queue_evidence_jobs(**test_evidence_request)
        if queued:
            yield evidence_jobs_queued_body(test_evidence_request, queued)
        return

    update = release_workflow_update_from_event(evt)
    if update is None:
        return
    projected = await ctx.db.project_release_workflow_event(update)
    alert_request = release_alert_request(update, projected)
    if alert_request is not None:
        yield alert_request
    evidence_request = release_failure_evidence_request(update, projected)
    if projected is None or evidence_request is None:
        return
    queued = await ctx.db.queue_evidence_jobs(**evidence_request)
    if queued:
        await ctx.db.project_release_workflow_event(evidence_queued_update(update, queued))
        yield evidence_jobs_queued_body(evidence_request, queued)
    return


def rca_test_evidence_request(evt: EventEnvelope) -> dict[str, object] | None:
    """실제 fault 관측이 끝난 test command만 기존 evidence job으로 연결한다."""
    if evt.subject != CommandCompletedBody.__subject__:
        return None
    result = evt.payload.get("result")
    result_body = result if isinstance(result, dict) else {}
    test_result = result_body.get("rca_test")
    test_body = test_result if isinstance(test_result, dict) else {}
    if result_body.get("status") != "completed" or test_body.get("fault_observed") is not True:
        return None

    run_id = str(test_body.get("run_id") or "")
    scenario_id = str(test_body.get("scenario_id") or "")
    workspace_id = str(result_body.get("workspace_id") or "")
    cluster_id = str(result_body.get("cluster_id") or "")
    if not all((run_id, scenario_id, workspace_id, cluster_id)):
        return None
    namespace = str(test_body.get("namespace") or "sandbox").strip() or "sandbox"
    resource_kind = str(test_body.get("resource_kind") or "Deployment").strip() or "Deployment"
    resource_name = str(test_body.get("resource_name") or "").strip()
    pod_names = normalized_pod_names(test_body.get("pod_names"))
    release_context = {
        "correlation_id": evt.correlation_id,
        "rca_test_run_id": run_id,
        "scenario_id": scenario_id,
        "namespace": namespace,
        "resource_kind": resource_kind,
        "resource_name": resource_name,
        "label_selector": str(test_body.get("label_selector") or ""),
        "evidence_scope": "rca_test_run",
    }
    if pod_names:
        release_context["pod_names"] = pod_names
    requested_sources = test_body.get("evidence_sources")
    provider_keys = (
        [str(source) for source in requested_sources if str(source)]
        if isinstance(requested_sources, list) and requested_sources
        else ["kubernetes"]
    )
    provider_policies: dict[str, object] = {}
    for provider_key in provider_keys:
        queries: list[dict[str, object]] = [
            dict(query)
            for query in evidence_provider_queries(
                provider_key,
                cluster_id=cluster_id,
                evidence_profile=DEMO_EVIDENCE_PROFILE,
            )
        ]
        if provider_key == "kubernetes":
            queries = [
                {
                    "name": "rca_test_run_snapshot",
                    "description": "RCA test run scoped sandbox snapshot",
                    "query": namespace,
                    "label_selector": str(test_body.get("label_selector") or ""),
                }
            ]
        elif provider_key == "logs":
            queries = rca_test_log_queries(namespace, pod_names)
        elif provider_key == "metadata":
            queries = [
                {
                    "name": "rca_test_metadata_snapshot",
                    "description": "RCA test run scoped metadata snapshot",
                    "query": rca_test_metadata_query(
                        namespace,
                        resource_kind,
                        resource_name,
                    ),
                }
            ]
        provider_policies[provider_key] = {
            "enabled": True,
            "queries": queries,
            "release_context": release_context,
        }
    return {
        "workspace_id": workspace_id,
        "cluster_id": cluster_id,
        "source_id": "rca-test",
        "window_start": run_id,
        "provider_keys": provider_keys,
        "failure_policy": "strict",
        "max_attempts": 3,
        "policy_generation": 0,
        "provider_policies": provider_policies,
    }


def normalized_pod_names(value: object) -> list[str]:
    """관측 결과의 Pod 이름을 순서 보존·중복 제거·상한 적용해 전달한다."""
    if not isinstance(value, list):
        return []
    names = [str(item).strip() for item in value if str(item).strip()]
    return list(dict.fromkeys(names))[:MAX_RCA_TEST_LOG_PODS]


def rca_test_metadata_query(namespace: str, resource_kind: str, resource_name: str) -> str:
    """Build the narrowest metadata query for one RCA test run."""
    normalized_namespace = namespace.strip() or "sandbox"
    normalized_resource_name = resource_name.strip()
    if resource_kind.strip().casefold() == "deployment" and normalized_resource_name:
        return f"deployment/{normalized_namespace}/{normalized_resource_name}"
    return normalized_namespace


def rca_test_log_queries(namespace: str, pod_names: list[str]) -> list[dict[str, object]]:
    """현재 test run에서 실제 관측된 Pod만 조회하는 짧은 Loki 정책을 만든다."""
    namespace_literal = json.dumps(namespace, ensure_ascii=False)
    return [
        {
            "name": f"rca_test_pod_log_{index}",
            "description": "현재 RCA test run Pod 로그",
            "query": (
                f"{{k8s_namespace_name={namespace_literal}, "
                f"k8s_pod_name={json.dumps(pod_name, ensure_ascii=False)}}} "
                f'|~ "{RCA_TEST_LOG_PATTERN}"'
            ),
            "range_seconds": RCA_TEST_LOG_RANGE_SECONDS,
        }
        for index, pod_name in enumerate(pod_names)
    ]


def evidence_jobs_queued_body(
    evidence_request: dict[str, object],
    queued: dict[str, object],
) -> EvidenceJobsQueuedBody:
    release_context = evidence_request.get("release_context")
    return EvidenceJobsQueuedBody(
        workspace_id=str(evidence_request.get("workspace_id") or ""),
        cluster_id=str(evidence_request.get("cluster_id") or ""),
        evidence_key=str(queued.get("evidence_key") or ""),
        source_id=str(evidence_request.get("source_id") or ""),
        window_start=str(evidence_request.get("window_start") or ""),
        provider_keys=[
            str(provider_key) for provider_key in list(evidence_request.get("provider_keys") or [])
        ],
        queued=int(queued.get("queued") or 0),
        job_ids=[str(job_id) for job_id in list(queued.get("job_ids") or [])],
        workflow_run_id=str(evidence_request.get("window_start") or "") or None,
        release_context=dict(release_context) if isinstance(release_context, dict) else {},
    )


if __name__ == "__main__":
    app.run()
