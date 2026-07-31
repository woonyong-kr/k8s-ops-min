"""GitOps 변경과 Safe PR 참조를 workload change read model에 투영."""

from __future__ import annotations

from domains.gitops.events import WorkflowRunCompletedBody
from domains.rca_changes.projection import workflow_pr_reference_row, workload_change_row
from domains.scm.events import SafePrCreatedBody
from packages.contracts.stores import RcaChangesStore
from packages.runtime.app import App, EventContext

app = App("change-correlation-worker")


@app.on(WorkflowRunCompletedBody)
async def on_workflow_completed(
    evt: WorkflowRunCompletedBody,
    ctx: EventContext[RcaChangesStore],
) -> None:
    if not ctx.workspace_id or ctx.workspace_id != evt.workspace_id:
        return
    authorities = await ctx.db.get_completed_workload_change_contexts(
        ctx.workspace_id,
        evt.workflow_run_id,
        evt.application_id,
        evt.binding_id,
    )
    for authority in authorities:
        row = workload_change_row(evt, ctx, authority)
        if row is not None:
            await ctx.db.record_workload_change(row)


@app.on(SafePrCreatedBody)
async def on_safe_pr_created(
    evt: SafePrCreatedBody,
    ctx: EventContext[RcaChangesStore],
) -> None:
    if not ctx.workspace_id or ctx.workspace_id != evt.workspace_id:
        return
    authority = await ctx.db.get_workflow_pr_identity_context(
        ctx.workspace_id,
        evt.workflow_run_id,
        evt.application_id,
        evt.binding_id,
    )
    if authority is None:
        return
    row = workflow_pr_reference_row(evt, ctx, authority)
    if row is not None:
        await ctx.db.record_workflow_pr_reference(row)


if __name__ == "__main__":
    app.run()
