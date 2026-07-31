"""ai-diff-worker - safe_pr.patch_prepared -> diff.explained -> safe_pr.ready."""

from __future__ import annotations

from collections.abc import AsyncIterator

from domains.rca.events import DiffExplainedBody, SafePrPatchPreparedBody
from domains.scm.events import SafePrReadyForCreationBody, SafePrRequestedBody
from domains.scm.pipeline import normalize_safe_pr_request
from domains.scm.policy import STAGE_DIFF, DefaultSafePrDiffPolicy, safe_pr_failed_body
from packages.contracts.event_bus.bodies import EventBody
from packages.runtime.app import App

app = App("ai-diff-worker")
DIFF_POLICY = DefaultSafePrDiffPolicy()


def prepared_request(evt: SafePrPatchPreparedBody) -> SafePrRequestedBody:
    if evt.request:
        return SafePrRequestedBody.from_body(evt.request)  # type: ignore[return-value]
    return SafePrRequestedBody(
        title=evt.title,
        body=evt.body,
        provider=evt.provider,
        workspace_id=evt.workspace_id,
        repository_id=evt.repository_id,
        binding_id=evt.binding_id,
        application_id=evt.application_id,
        workflow_run_id=evt.workflow_run_id,
        environment=evt.environment,
        manifest_path=evt.manifest_path,
        pr_kind=evt.pr_kind,
        approval_ref=evt.approval_ref,
        policy_decision_ref=evt.policy_decision_ref,
    )


@app.on(SafePrPatchPreparedBody)
async def on_safe_pr_patch_prepared(evt: SafePrPatchPreparedBody) -> AsyncIterator[EventBody]:
    request = normalize_safe_pr_request(prepared_request(evt))
    assessment = DIFF_POLICY.explain(request)
    summary = (
        f"{evt.title} 패치 초안은 PR 생성 게이트를 통과했습니다."
        if assessment.allowed
        else f"{evt.title} 패치 초안은 PR 생성 게이트에서 차단되었습니다."
    )
    yield DiffExplainedBody(
        summary=summary,
        risk=assessment.risk,
        details={
            "provider": evt.provider,
            "pr_kind": request.pr_kind,
            "patch_keys": sorted(evt.patch.keys()),
            "body_length": len(evt.body),
            "approval_ref": evt.approval_ref,
            "policy_decision_ref": evt.policy_decision_ref,
            **assessment.details,
        },
        workspace_id=evt.workspace_id,
        ready_for_creation=assessment.allowed,
        reason=assessment.message,
    )
    if assessment.allowed:
        yield SafePrReadyForCreationBody(
            request=request,
            summary=summary,
            risk=assessment.risk,
            details={"pr_kind": request.pr_kind, **assessment.details},
            workspace_id=evt.workspace_id,
        )
        return
    yield safe_pr_failed_body(request, assessment, stage=STAGE_DIFF)


if __name__ == "__main__":
    app.run()
