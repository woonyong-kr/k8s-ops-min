"""workflow-controller — GitOps 이벤트 흐름을 사용자 실행 객체로 투영.

기존 git-pull/render/diff/command worker chain은 그대로 유지. 이 worker는 같은
이벤트를 관찰해 Application, WorkflowRun, WorkflowRunStep, Approval 상태를 기록하고
콘솔이 읽을 수 있는 workflow.* / approval.* 이벤트를 발행함.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

from domains.command.events import (
    CommandCompletedBody,
    CommandQueuedForAgentBody,
    CommandRejectedBody,
    CommandRequestedBody,
)
from domains.gitops.approvals import resource_approval_qualifier
from domains.gitops.events import (
    ApprovalGrantedBody,
    ApprovalRejectedBody,
    ApprovalRequestedBody,
    DesiredDesiredDiffDetectedBody,
    Diff,
    DiffAnalyzedBody,
    GitChangedBody,
    GitWebhookReceivedBody,
    ManifestInvalidBody,
    ManifestRenderedBody,
    WorkflowCreatedBody,
    WorkflowRunCompletedBody,
    WorkflowRunFailedBody,
    WorkflowRunStartedBody,
    WorkflowStepRecordedBody,
)
from domains.gitops.recovery_merge import recovery_merge_authorization
from domains.gitops.repository import (
    derive_application_id,
    derive_approval_id,
    derive_deployment_binding_id,
    derive_repository_id,
    derive_watch_target_id,
    derive_workflow_run_id,
)
from domains.gitops.timeline import (
    git_changed_timeline_event,
    workflow_run_timeline_event,
    workflow_step_timeline_event,
)
from domains.scm.events import SafePrCreatedBody, SafePrFailedBody
from domains.target.events import ClusterDesiredStateChangedBody
from domains.target.management_guard import is_management_registration
from domains.timeline.repository import TimelineLedgerAppend
from packages.config.constants import Command, CommandStatus, Sandbox, Target
from packages.config.logs import get_logger
from packages.contracts.event_bus.bodies import EventBody
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gitops import (
    DEFAULT_ENVIRONMENT,
    ApprovalStatus,
    DeploymentBindingStatus,
    WorkflowMutation,
    WorkflowRunStatus,
    WorkflowStepName,
    WorkflowStepStatus,
    promotion_gate_from_command_result,
)
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, ResourceRole
from packages.contracts.stores import WorkflowStore
from packages.contracts.timeline import TimelineEvent
from packages.runtime.app import App, EventContext

app = App("workflow-controller")
LOGGER = get_logger(__name__)

MANUAL_APPROVAL_ROLE = ResourceRole.RELEASE_OPERATOR.value
POLICY_DECISION_REF_PREFIX = "policy-decision"
POLICY_ROUTE_SAFE_PR = "safe_pr"
POLICY_ROUTE_RECOVERY_PR_MERGED = "recovery_pr_merged"
RECOVERY_PR_MERGE_APPROVER = "recovery-pr-merge"


def normalize_payload(payload: JsonObject) -> JsonObject:
    repository_id = derive_repository_id(payload)
    watch_target_id = derive_watch_target_id({**payload, "repository_id": repository_id})
    binding_id = derive_deployment_binding_id(
        {**payload, "repository_id": repository_id, "watch_target_id": watch_target_id}
    )
    scoped_payload = {
        **payload,
        "repository_id": repository_id,
        "watch_target_id": watch_target_id,
        "binding_id": binding_id,
    }
    application_id = derive_application_id(scoped_payload)
    enriched = {
        **scoped_payload,
        "application_id": application_id,
        "workflow_run_id": derive_workflow_run_id(
            {**scoped_payload, "application_id": application_id}
        ),
        "workspace_id": str(payload.get("workspace_id", DEFAULT_WORKSPACE_ID)),
        "environment": str(payload.get("environment", DEFAULT_ENVIRONMENT)),
        "cluster_id": str(payload.get("cluster_id", Target.DEFAULT_CLUSTER_ID)),
    }
    return enriched


def application_name_hint(payload: JsonObject) -> str:
    explicit = payload.get("name") or payload.get("app_name")
    if explicit:
        return str(explicit)
    resource = str(payload.get("resource", ""))
    if "/" in resource:
        return resource.split("/", 1)[1]
    repo_ref = str(payload.get("repo_ref", ""))
    if "/" in repo_ref:
        return repo_ref.rsplit("/", 1)[1]
    return ""


def gitops_payload(evt: EventBody) -> JsonObject:
    payload = evt.to_body()
    name = application_name_hint(payload)
    if name:
        payload["name"] = name
    return normalize_payload(payload)


def diff_payload(evt: DesiredDesiredDiffDetectedBody | DiffAnalyzedBody) -> JsonObject:
    diff = evt.diff
    resource_name = diff.resource.split("/", 1)[-1] if "/" in diff.resource else diff.resource
    payload = diff.to_body()
    if resource_name:
        payload["name"] = resource_name
    return normalize_payload(payload)


async def ensure_run(
    ctx: EventContext[WorkflowStore],
    payload: JsonObject,
    status: str,
    current_step: str,
    summary: str,
    metadata: JsonObject | None = None,
) -> JsonObject | None:
    run = normalize_payload(payload)
    saved_application = await ctx.db.upsert_application(
        {
            **run,
            "name": str(run.get("name") or run.get("application_id")),
            "metadata": {
                "repository_id": run.get("repository_id"),
                "manifest_path": run.get("manifest_path"),
            },
        }
    )
    if isinstance(saved_application, dict):
        canonical_application_id = str(
            saved_application.get("application_id") or run["application_id"]
        )
        if canonical_application_id != str(run["application_id"]):
            workflow_seed = {**run, "application_id": canonical_application_id}
            workflow_seed.pop("workflow_run_id", None)
            run = {
                **run,
                "application_id": canonical_application_id,
                "workflow_run_id": derive_workflow_run_id(workflow_seed),
            }
    mutation = await ctx.db.start_workflow_run(
        {
            **run,
            "status": status,
            "current_step": current_step,
            "summary": summary,
            "metadata": metadata or {},
        }
    )
    if not workflow_mutation_applied(mutation):
        return None
    if not await append_workflow_run_timeline(
        ctx,
        run,
        status=status,
        current_step=current_step,
    ):
        return None
    return run


async def transition_run(
    ctx: EventContext[WorkflowStore],
    payload: JsonObject,
    status: str,
    current_step: str,
    summary: str,
    metadata: JsonObject | None = None,
) -> JsonObject | None:
    run = normalize_payload(payload)
    mutation = await ctx.db.update_workflow_run(
        {
            **run,
            "status": status,
            "current_step": current_step,
            "summary": summary,
            "metadata": metadata or {},
        }
    )
    if not workflow_mutation_applied(mutation):
        return None
    if not await append_workflow_run_timeline(
        ctx,
        run,
        status=status,
        current_step=current_step,
    ):
        return None
    return run


async def record_step(
    ctx: EventContext[WorkflowStore],
    payload: JsonObject,
    step: str,
    status: str,
    message: str | None = None,
    details: JsonObject | None = None,
) -> WorkflowStepRecordedBody | None:
    run = normalize_payload(payload)
    saved = await ctx.db.record_workflow_step(
        {
            **run,
            "name": step,
            "status": status,
            "message": message,
            "details": details or {},
        }
    )
    if not workflow_mutation_applied(saved):
        return None
    if not await append_workflow_step_timeline(
        ctx,
        run,
        step=step,
        status=status,
    ):
        return None
    return WorkflowStepRecordedBody(
        workflow_run_id=str(run["workflow_run_id"]),
        application_id=str(run["application_id"]),
        step=step,
        status=status,
        workspace_id=str(run["workspace_id"]),
        binding_id=str(run["binding_id"]),
        environment=str(run["environment"]),
        message=message,
        details=details or {},
    )


def workflow_mutation_applied(result: object) -> bool:
    """Fail closed unless the repository confirms a real guarded write."""
    return isinstance(result, WorkflowMutation) and result.applied


async def append_workflow_run_timeline(
    ctx: EventContext[WorkflowStore],
    run: JsonObject,
    *,
    status: str,
    current_step: str,
) -> bool:
    event = workflow_run_timeline_event(
        source_event_id=ctx.event_id,
        source_created_at=ctx.created_at,
        workspace_id=str(run["workspace_id"]),
        cluster_id=str(run["cluster_id"]),
        application_id=str(run["application_id"]),
        binding_id=str(run["binding_id"]),
        workflow_run_id=str(run["workflow_run_id"]),
        status=status,
        current_step=current_step,
    )
    return await append_workflow_timeline_event(ctx, event)


async def append_workflow_step_timeline(
    ctx: EventContext[WorkflowStore],
    run: JsonObject,
    *,
    step: str,
    status: str,
) -> bool:
    event = workflow_step_timeline_event(
        source_event_id=ctx.event_id,
        source_created_at=ctx.created_at,
        workspace_id=str(run["workspace_id"]),
        cluster_id=str(run["cluster_id"]),
        application_id=str(run["application_id"]),
        binding_id=str(run["binding_id"]),
        workflow_run_id=str(run["workflow_run_id"]),
        step=step,
        status=status,
    )
    return await append_workflow_timeline_event(ctx, event)


async def append_workflow_timeline_event(
    ctx: EventContext[WorkflowStore],
    event: TimelineEvent,
) -> bool:
    """Append in the worker UoW; output bodies follow only a new durable fact."""
    append = await ctx.db.append_timeline_event(event)
    if not isinstance(append, TimelineLedgerAppend):
        raise TypeError("workflow timeline ledger append returned an invalid result")
    return append.inserted


async def registered_git_changed_binding(
    ctx: EventContext[WorkflowStore],
    payload: JsonObject,
) -> JsonObject | None:
    """Return only the active stored binding named by the canonical event."""
    binding = await ctx.db.get_deployment_binding(
        str(payload["workspace_id"]),
        str(payload["binding_id"]),
    )
    if not isinstance(binding, Mapping):
        return None
    expected = {
        "workspace_id": str(payload["workspace_id"]),
        "binding_id": str(payload["binding_id"]),
        "repository_id": str(payload["repository_id"]),
        "cluster_id": str(payload["cluster_id"]),
        "environment": str(payload["environment"]),
        "manifest_path": str(payload.get("manifest_path", "")),
    }
    if not stored_identity_matches(binding, expected):
        return None
    if str(binding.get("status", "")) != DeploymentBindingStatus.ACTIVE.value:
        return None
    return dict(binding)


async def git_changed_target_is_persisted(
    ctx: EventContext[WorkflowStore],
    run: JsonObject,
    binding: JsonObject,
) -> bool:
    """Confirm the three persisted identities before retaining a GitOps fact."""
    application = await ctx.db.get_application(
        str(run["workspace_id"]),
        str(run["application_id"]),
    )
    workflow_run = await ctx.db.get_workflow_run(str(run["workflow_run_id"]))
    if not isinstance(application, Mapping) or not isinstance(workflow_run, Mapping):
        return False
    application_matches = stored_identity_matches(
        application,
        {
            "workspace_id": str(run["workspace_id"]),
            "application_id": str(run["application_id"]),
            "repository_id": str(run["repository_id"]),
        },
    )
    workflow_matches = stored_identity_matches(
        workflow_run,
        {
            "workspace_id": str(run["workspace_id"]),
            "workflow_run_id": str(run["workflow_run_id"]),
            "application_id": str(run["application_id"]),
            "binding_id": str(run["binding_id"]),
            "cluster_id": str(run["cluster_id"]),
            "environment": str(run["environment"]),
            "commit_sha": str(run.get("commit_sha", "")),
        },
    )
    binding_matches_application = str(binding.get("repository_id", "")) == str(
        application.get("repository_id", "")
    ) and (
        str(binding.get("app_name", "")) == str(application.get("name", ""))
        or str(binding.get("manifest_path", "")) == str(application.get("manifest_path", ""))
    )
    return application_matches and workflow_matches and binding_matches_application


async def workflow_run_identity_is_persisted(
    ctx: EventContext[WorkflowStore],
    run: JsonObject,
) -> bool:
    """Accept follow-up workflow facts only for the exact stored run.

    Safe PR creation is also used by the standalone manifest editor. Those events
    intentionally have a workflow-shaped correlation id but no WorkflowRun row, so
    projecting them as application-workflow steps would create orphan records and
    needlessly contend on the workspace Timeline cursor.
    """
    workflow_run = await ctx.db.get_workflow_run(str(run["workflow_run_id"]))
    if not isinstance(workflow_run, Mapping):
        return False
    expected = {
        "workspace_id": str(run["workspace_id"]),
        "workflow_run_id": str(run["workflow_run_id"]),
        "application_id": str(run["application_id"]),
        "binding_id": str(run["binding_id"]),
        "environment": str(run["environment"]),
    }
    commit_sha = str(run.get("commit_sha", ""))
    if commit_sha:
        expected["commit_sha"] = commit_sha
    return stored_identity_matches(workflow_run, expected)


def stored_identity_matches(record: Mapping[str, object], expected: dict[str, str]) -> bool:
    """Require a complete stored identity rather than trusting an event field."""
    return all(str(record.get(field, "")) == value for field, value in expected.items())


async def append_git_changed_timeline(
    ctx: EventContext[WorkflowStore],
    run: JsonObject,
) -> bool:
    event = git_changed_timeline_event(
        source_event_id=ctx.event_id,
        source_created_at=ctx.created_at,
        workspace_id=str(run["workspace_id"]),
        cluster_id=str(run["cluster_id"]),
        application_id=str(run["application_id"]),
        binding_id=str(run["binding_id"]),
        workflow_run_id=str(run["workflow_run_id"]),
    )
    return await append_workflow_timeline_event(ctx, event)


def approval_payload(payload: JsonObject, reason: str, status: str) -> JsonObject:
    run = normalize_payload(payload)
    resource = str(run.get("resource") or "").strip()
    namespace = str(run.get("namespace") or "").strip()
    basis = run.get("basis")
    artifact_digest = (
        str(basis.get("artifact_digest") or "").strip()
        if isinstance(basis, Mapping)
        else ""
    )
    qualifier = resource_approval_qualifier(
        namespace,
        resource,
        artifact_digest,
    )
    approval_id = derive_approval_id(
        str(run["workflow_run_id"]),
        qualifier if resource else None,
    )
    return {
        **run,
        "approval_id": approval_id,
        "status": status,
        "reason": reason,
        "requested_role": MANUAL_APPROVAL_ROLE,
    }


def policy_decision_ref(approval_id: str, route: str) -> str:
    return f"{POLICY_DECISION_REF_PREFIX}:{approval_id}:{route}"


def merged_recovery_command(
    diff: Diff,
    *,
    approval_ref: str,
    decision_ref: str,
) -> CommandRequestedBody:
    return CommandRequestedBody(
        cluster_id=diff.cluster_id,
        action=Command.APPLY_MANIFEST_ACTION,
        namespace=diff.namespace,
        reason="operator-reviewed recovery PR merged",
        diff=diff,
        workspace_id=diff.workspace_id,
        application_id=diff.application_id,
        workflow_run_id=diff.workflow_run_id,
        binding_id=diff.binding_id,
        environment=diff.environment,
        requested_by=RECOVERY_PR_MERGE_APPROVER,
        approval_ref=approval_ref,
        policy_decision_ref=decision_ref,
    )


def command_result_succeeded(result: JsonObject) -> bool:
    return bool(promotion_gate_from_command_result(result)["eligible"])


@app.on(GitWebhookReceivedBody)
async def on_git_webhook(
    evt: GitWebhookReceivedBody, ctx: EventContext[WorkflowStore]
) -> AsyncIterator[EventBody]:
    run = await ensure_run(
        ctx,
        gitops_payload(evt),
        WorkflowRunStatus.STARTED.value,
        WorkflowStepName.GIT.value,
        "git webhook received",
        {"commit_sha": evt.commit_sha, "repo_ref": evt.repo_ref},
    )
    if run is None:
        return
    yield WorkflowCreatedBody(**workflow_created_fields(run))
    yield WorkflowRunStartedBody(
        workflow_run_id=str(run["workflow_run_id"]),
        application_id=str(run["application_id"]),
        workspace_id=str(run["workspace_id"]),
        repository_id=str(run.get("repository_id", "")),
        watch_target_id=str(run.get("watch_target_id", "")),
        binding_id=str(run["binding_id"]),
        environment=str(run["environment"]),
        cluster_id=str(run["cluster_id"]),
        commit_sha=str(run.get("commit_sha", "")),
        manifest_path=str(run.get("manifest_path", "")),
        status=WorkflowRunStatus.STARTED.value,
        current_step=WorkflowStepName.GIT.value,
    )
    step = await record_step(
        ctx,
        run,
        WorkflowStepName.GIT.value,
        WorkflowStepStatus.RUNNING.value,
        "webhook accepted",
        {"commit_sha": evt.commit_sha, "branch": evt.branch},
    )
    if step is not None:
        yield step
    # 글로벌 서비스 — 같은 repo 의 global 바인딩에도 이 변경을 전개한다.
    async for fanout_body in fanout_global_bindings(evt, ctx):
        yield fanout_body


@app.on(GitChangedBody)
async def on_git_changed(
    evt: GitChangedBody, ctx: EventContext[WorkflowStore]
) -> AsyncIterator[EventBody]:
    payload = gitops_payload(evt)
    binding = await registered_git_changed_binding(ctx, payload)
    if binding is None:
        return
    run = await ensure_run(
        ctx,
        payload,
        WorkflowRunStatus.RENDERING.value,
        WorkflowStepName.RENDER.value,
        "git change confirmed; rendering manifest",
        {"commit_sha": evt.commit_sha, "branch": evt.branch},
    )
    if run is None:
        return
    if not await git_changed_target_is_persisted(ctx, run, binding):
        return
    if not await append_git_changed_timeline(ctx, run):
        return
    step = await record_step(
        ctx,
        run,
        WorkflowStepName.GIT.value,
        WorkflowStepStatus.SUCCEEDED.value,
        "git change confirmed",
        {"commit_sha": evt.commit_sha},
    )
    if step is not None:
        yield step


@app.on(ManifestRenderedBody)
async def on_manifest_rendered(
    evt: ManifestRenderedBody, ctx: EventContext[WorkflowStore]
) -> AsyncIterator[EventBody]:
    # manifest.rendered is a resource-level event for a workflow that git.changed
    # already created. A Kustomize source can emit several Deployments, so a
    # Deployment's metadata.name must never be reused as the Application identity.
    run = await transition_run(
        ctx,
        gitops_payload(evt),
        WorkflowRunStatus.DIFFING.value,
        WorkflowStepName.DIFF.value,
        "manifest rendered; calculating desired diff",
        {"kind": evt.rendered_manifest.kind, "resource": evt.rendered_manifest.metadata.name},
    )
    if run is None:
        return
    step = await record_step(
        ctx,
        run,
        WorkflowStepName.RENDER.value,
        WorkflowStepStatus.SUCCEEDED.value,
        "manifest rendered",
        evt.rendered_manifest.to_body(),
    )
    if step is not None:
        yield step


@app.on(ManifestInvalidBody)
async def on_manifest_invalid(
    evt: ManifestInvalidBody, ctx: EventContext[WorkflowStore]
) -> AsyncIterator[EventBody]:
    run = await ensure_run(
        ctx,
        gitops_payload(evt),
        WorkflowRunStatus.FAILED.value,
        WorkflowStepName.RENDER.value,
        "manifest render failed",
        {"reason": evt.reason},
    )
    if run is None:
        return
    step = await record_step(
        ctx,
        run,
        WorkflowStepName.RENDER.value,
        WorkflowStepStatus.FAILED.value,
        evt.reason,
        {"manifest_path": evt.manifest_path},
    )
    if step is not None:
        yield step
    yield WorkflowRunFailedBody(
        workflow_run_id=str(run["workflow_run_id"]),
        application_id=str(run["application_id"]),
        reason=evt.reason,
        workspace_id=str(run["workspace_id"]),
        binding_id=str(run["binding_id"]),
        environment=str(run["environment"]),
        details={"manifest_path": evt.manifest_path},
    )


@app.on(DesiredDesiredDiffDetectedBody)
async def on_diff_detected(
    evt: DesiredDesiredDiffDetectedBody, ctx: EventContext[WorkflowStore]
) -> AsyncIterator[EventBody]:
    run = await transition_run(
        ctx,
        diff_payload(evt),
        WorkflowRunStatus.POLICY_CHECKING.value,
        WorkflowStepName.POLICY.value,
        "desired diff detected; checking policy",
        {"resource": evt.diff.resource},
    )
    if run is None:
        return
    step = await record_step(
        ctx,
        run,
        WorkflowStepName.DIFF.value,
        WorkflowStepStatus.SUCCEEDED.value,
        "desired diff detected",
        evt.diff.to_body(),
    )
    if step is not None:
        yield step


@app.on(DiffAnalyzedBody)
async def on_diff_analyzed(
    evt: DiffAnalyzedBody, ctx: EventContext[WorkflowStore]
) -> AsyncIterator[EventBody]:
    run = diff_payload(evt)
    policy_step = await record_step(
        ctx,
        run,
        WorkflowStepName.POLICY.value,
        WorkflowStepStatus.SUCCEEDED.value,
        evt.reason,
        {"safe": evt.safe, "risk": evt.risk},
    )
    if policy_step is not None:
        yield policy_step

    if evt.diff.is_image_only_noop():
        completed_run = await transition_run(
            ctx,
            run,
            WorkflowRunStatus.SUCCEEDED.value,
            WorkflowStepName.HEALTH.value,
            Sandbox.NO_DIFF_REASON,
            {"resource": evt.diff.resource},
        )
        if completed_run is None:
            return
        apply_step = await record_step(
            ctx,
            run,
            WorkflowStepName.APPLY.value,
            WorkflowStepStatus.SKIPPED.value,
            Sandbox.NO_DIFF_REASON,
            evt.diff.to_body(),
        )
        if apply_step is not None:
            yield apply_step
        yield WorkflowRunCompletedBody(
            workflow_run_id=str(run["workflow_run_id"]),
            application_id=str(run["application_id"]),
            workspace_id=str(run["workspace_id"]),
            binding_id=str(run["binding_id"]),
            environment=str(run["environment"]),
            summary=Sandbox.NO_DIFF_REASON,
            details=evt.diff.to_body(),
        )
        return

    recovery_authorization = await recovery_merge_authorization(ctx.db, evt.diff)
    if recovery_authorization.tracked and recovery_authorization.request is None:
        return
    merged_recovery = recovery_authorization.request
    if merged_recovery is not None:
        approval = approval_payload(
            run,
            evt.reason,
            ApprovalStatus.REQUESTED.value,
        )
        approval_ref = str(approval["approval_id"])
        decision_ref = policy_decision_ref(
            approval_ref,
            POLICY_ROUTE_RECOVERY_PR_MERGED,
        )
        command = merged_recovery_command(
            evt.diff,
            approval_ref=approval_ref,
            decision_ref=decision_ref,
        )
        details = {
            "safe": False,
            "risk": evt.risk,
            "policy_route": POLICY_ROUTE_RECOVERY_PR_MERGED,
            "policy_decision_ref": decision_ref,
            "approval_ref": approval_ref,
            "diff": evt.diff.to_body(),
            "command_requested": command.to_body(),
            "merge_commit_sha": merged_recovery.commit_sha,
        }
        resolver = getattr(ctx.db, "resolve_workflow_approval_if_open", None)
        if not callable(resolver):
            return
        resolved = await resolver(
            approval_ref,
            str(run["workspace_id"]),
            ApprovalStatus.GRANTED.value,
            RECOVERY_PR_MERGE_APPROVER,
            "approved-by-merged-recovery-pr",
            details,
        )
        if not resolved:
            return
        applying_run = await transition_run(
            ctx,
            run,
            WorkflowRunStatus.APPLYING.value,
            WorkflowStepName.APPLY.value,
            "merged recovery PR approved; dispatching exact deployment",
            {"merge_commit_sha": merged_recovery.commit_sha},
        )
        if applying_run is None:
            raise RuntimeError("merged recovery approval could not advance workflow")
        approval_step = await record_step(
            ctx,
            run,
            WorkflowStepName.APPROVAL.value,
            WorkflowStepStatus.SUCCEEDED.value,
            "operator approval reused from exact merged recovery PR",
            details,
        )
        if approval_step is not None:
            yield approval_step
        yield command
        return

    if evt.safe:
        approval = approval_payload(run, evt.reason, ApprovalStatus.NOT_REQUIRED.value)
        approval_ref = str(approval["approval_id"])
        details = {
            "safe": True,
            "risk": evt.risk,
            "policy_route": POLICY_ROUTE_SAFE_PR,
            "policy_decision_ref": policy_decision_ref(approval_ref, POLICY_ROUTE_SAFE_PR),
            "approval_ref": approval_ref,
            "diff": evt.diff.to_body(),
        }
        applying_run = await transition_run(
            ctx,
            run,
            WorkflowRunStatus.APPLYING.value,
            WorkflowStepName.SAFE_PR.value,
            "policy auto-approved; creating safe PR",
            {"risk": evt.risk},
        )
        if applying_run is None:
            return
        approval_step = await record_step(
            ctx,
            run,
            WorkflowStepName.APPROVAL.value,
            WorkflowStepStatus.SKIPPED.value,
            "approval not required by sandbox policy",
            details,
        )
        if approval_step is not None:
            yield approval_step
        return

    approval = approval_payload(run, evt.reason, ApprovalStatus.REQUESTED.value)
    approval_ref = str(approval["approval_id"])
    details = {
        "safe": False,
        "risk": evt.risk,
        "policy_route": "approval_required",
        "policy_decision_ref": policy_decision_ref(approval_ref, "approval_required"),
        "approval_ref": approval_ref,
        "diff": evt.diff.to_body(),
    }
    waiting_run = await transition_run(
        ctx,
        run,
        WorkflowRunStatus.WAITING_FOR_APPROVAL.value,
        WorkflowStepName.APPROVAL.value,
        "approval required before write",
        details,
    )
    if waiting_run is None:
        return
    approval_step = await record_step(
        ctx,
        run,
        WorkflowStepName.APPROVAL.value,
        WorkflowStepStatus.PENDING.value,
        evt.reason,
        details,
    )
    if approval_step is not None:
        yield approval_step
    yield ApprovalRequestedBody(
        approval_id=approval_ref,
        workflow_run_id=str(run["workflow_run_id"]),
        application_id=str(run["application_id"]),
        reason=evt.reason,
        workspace_id=str(run["workspace_id"]),
        binding_id=str(run["binding_id"]),
        environment=str(run["environment"]),
        requested_role=MANUAL_APPROVAL_ROLE,
        details=details,
    )


@app.on(SafePrCreatedBody)
async def on_safe_pr_created(
    evt: SafePrCreatedBody, ctx: EventContext[WorkflowStore]
) -> AsyncIterator[EventBody]:
    run = gitops_payload(evt)
    if not await workflow_run_identity_is_persisted(ctx, run):
        return
    step = await record_step(
        ctx,
        run,
        WorkflowStepName.SAFE_PR.value,
        WorkflowStepStatus.SUCCEEDED.value,
        "safe PR created",
        {"pr_url": evt.pr_url, "provider": evt.provider, "mode": evt.mode},
    )
    if step is not None:
        yield step


@app.on(SafePrFailedBody)
async def on_safe_pr_failed(
    evt: SafePrFailedBody, ctx: EventContext[WorkflowStore]
) -> AsyncIterator[EventBody]:
    run = await transition_run(
        ctx,
        gitops_payload(evt),
        WorkflowRunStatus.FAILED.value,
        WorkflowStepName.SAFE_PR.value,
        evt.reason,
        {"provider": evt.provider, "title": evt.title},
    )
    if run is None:
        return
    step = await record_step(
        ctx,
        run,
        WorkflowStepName.SAFE_PR.value,
        WorkflowStepStatus.FAILED.value,
        evt.reason,
        {"provider": evt.provider, "title": evt.title},
    )
    if step is not None:
        yield step
    yield WorkflowRunFailedBody(
        workflow_run_id=str(run["workflow_run_id"]),
        application_id=str(run["application_id"]),
        reason=evt.reason,
        workspace_id=str(run["workspace_id"]),
        binding_id=str(run["binding_id"]),
        environment=str(run["environment"]),
        details={"provider": evt.provider, "title": evt.title},
    )


@app.on(ApprovalGrantedBody)
async def on_approval_granted(
    evt: ApprovalGrantedBody, ctx: EventContext[WorkflowStore]
) -> AsyncIterator[EventBody]:
    run = normalize_payload(evt.to_body())
    await ctx.db.resolve_workflow_approval(
        {
            **run,
            "approval_id": evt.approval_id,
            "status": ApprovalStatus.GRANTED.value,
            "decided_by": evt.decided_by,
            "decision": evt.decision,
            "details": evt.details,
        }
    )
    applying_run = await transition_run(
        ctx,
        run,
        WorkflowRunStatus.APPLYING.value,
        WorkflowStepName.APPLY.value,
        "approval granted; waiting for command execution",
        evt.details,
    )
    # The run is commit-scoped while approvals are resource-scoped.  The first
    # grant advances WAITING→APPLYING; later grants legitimately see an
    # already-applying run and must still dispatch their own command.
    if applying_run is not None:
        approval_step = await record_step(
            ctx,
            run,
            WorkflowStepName.APPROVAL.value,
            WorkflowStepStatus.SUCCEEDED.value,
            evt.decision,
            evt.details,
        )
        if approval_step is not None:
            yield approval_step
    command_payload = evt.details.get("command_requested")
    if isinstance(command_payload, Mapping):
        yield CommandRequestedBody.from_body(command_payload)


@app.on(ApprovalRejectedBody)
async def on_approval_rejected(
    evt: ApprovalRejectedBody, ctx: EventContext[WorkflowStore]
) -> AsyncIterator[EventBody]:
    run = normalize_payload(evt.to_body())
    await ctx.db.resolve_workflow_approval(
        {
            **run,
            "approval_id": evt.approval_id,
            "status": ApprovalStatus.REJECTED.value,
            "decided_by": evt.decided_by,
            "decision": "rejected",
            "details": evt.details,
        }
    )
    failed_run = await transition_run(
        ctx,
        run,
        WorkflowRunStatus.FAILED.value,
        WorkflowStepName.APPROVAL.value,
        evt.reason,
        evt.details,
    )
    if failed_run is None:
        return
    approval_step = await record_step(
        ctx,
        run,
        WorkflowStepName.APPROVAL.value,
        WorkflowStepStatus.FAILED.value,
        evt.reason,
        evt.details,
    )
    if approval_step is not None:
        yield approval_step
    yield WorkflowRunFailedBody(
        workflow_run_id=str(run["workflow_run_id"]),
        application_id=str(run["application_id"]),
        reason=evt.reason,
        workspace_id=str(run["workspace_id"]),
        binding_id=str(run["binding_id"]),
        environment=str(run["environment"]),
        details=evt.details,
    )


@app.on(CommandQueuedForAgentBody)
async def on_command_queued(
    evt: CommandQueuedForAgentBody, ctx: EventContext[WorkflowStore]
) -> AsyncIterator[EventBody]:
    payload = gitops_payload(evt)
    # Standalone resource-editor commands deliberately use workflow-shaped
    # correlation fields without owning a GitOps WorkflowRun row. Follow-up
    # command facts must never synthesize an application/run from the reduced
    # agent-queue envelope; only an already persisted, identity-matching run
    # may advance.
    if not await workflow_run_identity_is_persisted(ctx, payload):
        return
    await ctx.db.attach_workflow_command(str(payload["workflow_run_id"]), evt.command_id)
    run = await transition_run(
        ctx,
        payload,
        WorkflowRunStatus.ROLLOUT_WAITING.value,
        WorkflowStepName.APPLY.value,
        "command queued for outbound agent",
        {"command_id": evt.command_id},
    )
    if run is None:
        return
    step = await record_step(
        ctx,
        run,
        WorkflowStepName.APPLY.value,
        WorkflowStepStatus.RUNNING.value,
        "command queued for agent",
        {"command_id": evt.command_id, "cluster_id": evt.cluster_id},
    )
    if step is not None:
        yield step


@app.on(CommandRejectedBody)
async def on_command_rejected(
    evt: CommandRejectedBody, ctx: EventContext[WorkflowStore]
) -> AsyncIterator[EventBody]:
    run = normalize_payload(evt.requested)
    failed_run = await transition_run(
        ctx,
        run,
        WorkflowRunStatus.FAILED.value,
        WorkflowStepName.APPLY.value,
        evt.reason,
        {"requested": evt.requested},
    )
    if failed_run is None:
        return
    step = await record_step(
        ctx,
        run,
        WorkflowStepName.APPLY.value,
        WorkflowStepStatus.FAILED.value,
        evt.reason,
        {"requested": evt.requested},
    )
    if step is not None:
        yield step
    yield WorkflowRunFailedBody(
        workflow_run_id=str(run["workflow_run_id"]),
        application_id=str(run["application_id"]),
        reason=evt.reason,
        workspace_id=str(run["workspace_id"]),
        binding_id=str(run["binding_id"]),
        environment=str(run["environment"]),
        details={"requested": evt.requested},
    )


@app.on(CommandCompletedBody)
async def on_command_completed(
    evt: CommandCompletedBody, ctx: EventContext[WorkflowStore]
) -> AsyncIterator[EventBody]:
    identity = await ctx.db.get_workflow_identity_for_command(evt.command_id)
    if identity is None:
        return
    run = normalize_payload(identity)
    progress = await ctx.db.get_workflow_command_progress(str(run["workflow_run_id"]))
    progress_status = workflow_command_progress_status(progress)
    if progress_status == "waiting":
        return
    succeeded = progress_status == "succeeded"
    run_status = (
        WorkflowRunStatus.SUCCEEDED.value if succeeded else WorkflowRunStatus.FAILED.value
    )
    step_status = (
        WorkflowStepStatus.SUCCEEDED.value if succeeded else WorkflowStepStatus.FAILED.value
    )
    rollout_details = aggregate_workflow_rollout_details(progress, evt.command_id)
    message = (
        "all approved resource commands completed"
        if succeeded
        else workflow_command_failure_message(progress, evt.result)
    )
    if succeeded:
        recorded = await ctx.db.record_approved_workflow_snapshots(
            str(run["workflow_run_id"])
        )
        expected = len(workflow_progress_commands(progress))
        if recorded < expected:
            succeeded = False
            run_status = WorkflowRunStatus.FAILED.value
            step_status = WorkflowStepStatus.FAILED.value
            message = "approved resource snapshot persistence failed"
            rollout_details["snapshot_error"] = {
                "expected": expected,
                "recorded": recorded,
            }
    await ctx.db.attach_workflow_command(str(run["workflow_run_id"]), evt.command_id)
    mutation = await ctx.db.update_workflow_run(
        {
            **run,
            "command_id": evt.command_id,
            "status": run_status,
            "current_step": WorkflowStepName.HEALTH.value,
            "summary": message or run_status,
            "metadata": rollout_details,
        }
    )
    if not workflow_mutation_applied(mutation):
        return
    if not await append_workflow_run_timeline(
        ctx,
        run,
        status=run_status,
        current_step=WorkflowStepName.HEALTH.value,
    ):
        return
    apply_step = await record_step(
        ctx,
        run,
        WorkflowStepName.APPLY.value,
        step_status,
        message or run_status,
        rollout_details,
    )
    if apply_step is not None:
        yield apply_step
    if succeeded:
        health_step = await record_step(
            ctx,
            run,
            WorkflowStepName.HEALTH.value,
            WorkflowStepStatus.SUCCEEDED.value,
            "rollout health completed",
            rollout_details,
        )
        if health_step is not None:
            yield health_step
        yield WorkflowRunCompletedBody(
            workflow_run_id=str(run["workflow_run_id"]),
            application_id=str(run["application_id"]),
            workspace_id=str(run["workspace_id"]),
            binding_id=str(run["binding_id"]),
            environment=str(run["environment"]),
            summary=message or "workflow succeeded",
            details=rollout_details,
        )
        return
    yield WorkflowRunFailedBody(
        workflow_run_id=str(run["workflow_run_id"]),
        application_id=str(run["application_id"]),
        reason=message or "command failed",
        workspace_id=str(run["workspace_id"]),
        binding_id=str(run["binding_id"]),
        environment=str(run["environment"]),
        details=rollout_details,
    )


def workflow_progress_approvals(progress: Mapping[str, object]) -> list[Mapping[str, object]]:
    raw = progress.get("approvals")
    return [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []


def workflow_progress_commands(progress: Mapping[str, object]) -> list[Mapping[str, object]]:
    raw = progress.get("commands")
    return [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []


def workflow_command_progress_status(progress: Mapping[str, object]) -> str:
    approvals = workflow_progress_approvals(progress)
    commands = workflow_progress_commands(progress)
    if approvals:
        if any(str(item.get("status") or "") == ApprovalStatus.REJECTED.value for item in approvals):
            return "failed"
        if any(str(item.get("status") or "") != ApprovalStatus.GRANTED.value for item in approvals):
            return "waiting"
        granted_ids = {str(item.get("approval_id") or "") for item in approvals}
        command_refs = {str(item.get("approval_ref") or "") for item in commands}
        if not granted_ids.issubset(command_refs):
            return "waiting"
    if not commands:
        return "waiting"
    terminal_failures = {
        CommandStatus.FAILED,
        CommandStatus.CANCELLED,
    }
    if any(str(item.get("status") or "") in terminal_failures for item in commands):
        return "failed"
    if any(str(item.get("status") or "") != CommandStatus.COMPLETED for item in commands):
        return "waiting"
    if any(
        not command_result_succeeded(
            dict(item.get("result") or {}) if isinstance(item.get("result"), Mapping) else {}
        )
        for item in commands
    ):
        return "failed"
    return "succeeded"


def workflow_command_failure_message(
    progress: Mapping[str, object], fallback_result: Mapping[str, object]
) -> str:
    for item in workflow_progress_commands(progress):
        result = item.get("result")
        if not isinstance(result, Mapping) or command_result_succeeded(dict(result)):
            continue
        return str(result.get("message") or result.get("status") or "command failed")
    return str(
        fallback_result.get("message")
        or fallback_result.get("status")
        or "command failed"
    )


def aggregate_workflow_rollout_details(
    progress: Mapping[str, object], terminal_command_id: str
) -> JsonObject:
    commands = sorted(
        workflow_progress_commands(progress),
        key=lambda item: str(item.get("command_id") or ""),
    )
    command_ids = [str(item.get("command_id") or "") for item in commands]
    resources: list[object] = []
    failed_resources: list[object] = []
    command_results: list[JsonObject] = []
    rollouts: list[JsonObject] = []
    for item in commands:
        result = item.get("result")
        body = dict(result) if isinstance(result, Mapping) else {}
        details = rollout_result_details(str(item.get("command_id") or ""), body)
        resources.extend(details["resources"])
        failed_resources.extend(details["failed_resources"])
        rollout = details["rollout"]
        if isinstance(rollout, Mapping):
            rollouts.append(dict(rollout))
        command_results.append(
            {
                "command_id": str(item.get("command_id") or ""),
                "approval_ref": str(item.get("approval_ref") or ""),
                "status": str(item.get("status") or ""),
                "result": body,
            }
        )
    eligible = bool(commands) and all(
        str(item.get("status") or "") == CommandStatus.COMPLETED
        and isinstance(item.get("result"), Mapping)
        and command_result_succeeded(dict(item["result"]))
        for item in commands
    )
    aggregate_result: JsonObject = {
        "status": CommandStatus.COMPLETED if eligible else CommandStatus.FAILED,
        "applied": eligible,
        "resources": resources,
        "rollout": {
            "ready": eligible and all(item.get("ready") is not False for item in rollouts),
            "resources": rollouts,
        },
        "commands": command_results,
    }
    return {
        "command_id": terminal_command_id,
        "command_ids": command_ids,
        "result": aggregate_result,
        "resources": resources,
        "failed_resources": failed_resources,
        "rollout": aggregate_result["rollout"],
        "commands": command_results,
    }


def rollout_result_details(command_id: str, result: JsonObject) -> JsonObject:
    resources = result.get("resources")
    resource_list = resources if isinstance(resources, list) else []
    failed = [
        item
        for item in resource_list
        if isinstance(item, Mapping)
        and (item.get("applied") is False or str(item.get("status", "")).lower() == "failed")
    ]
    rollout = result.get("rollout")
    details: JsonObject = {
        "command_id": command_id,
        "result": result,
        "resources": resource_list,
        "failed_resources": failed,
        "rollout": rollout if isinstance(rollout, Mapping) else {},
    }
    return details


def workflow_created_fields(payload: JsonObject) -> JsonObject:
    return {
        "workspace_id": str(payload["workspace_id"]),
        "application_id": str(payload["application_id"]),
        "workflow_run_id": str(payload["workflow_run_id"]),
        "repository_id": str(payload.get("repository_id", "")),
        "watch_target_id": str(payload.get("watch_target_id", "")),
        "binding_id": str(payload["binding_id"]),
        "environment": str(payload["environment"]),
        "cluster_id": str(payload["cluster_id"]),
        "commit_sha": str(payload.get("commit_sha", "")),
        "manifest_path": str(payload.get("manifest_path", "")),
    }


# ── 승격 파이프라인 / 글로벌 서비스 ──────────────────────────────
# 두 룰 모두 바인딩의 deploy_policy JSONB 에 선언된다(스키마·이벤트 계약 변경 없음):
#   {"promotes_to_binding_id": "<binding>"}  → run 성공 시 대상 바인딩으로 재진입
#   {"global": true}                          → 같은 repo 웹훅을 이 바인딩에도 fan-out
PROMOTES_TO_KEY = "promotes_to_binding_id"
GLOBAL_BINDING_KEY = "global"
DEFAULT_PROMOTION_REPLICAS = 2
CLUSTER_REGISTERED_REASON = "target registered"


def binding_policy(binding: JsonObject) -> JsonObject:
    return dict(binding.get("deploy_policy") or {})


def binding_source_type(binding: JsonObject) -> str:
    policy = binding_policy(binding)
    return str(policy.get("manifest_source") or policy.get("source_type") or "").strip()


async def promoted_image_and_replicas(
    ctx: EventContext[WorkflowStore], workflow_run_id: str
) -> tuple[str, int]:
    """소스 run 의 diff 단계 상세에서 실제 적용된 이미지·레플리카를 읽는다(합성 금지)."""
    details = await ctx.db.get_workflow_step_details(workflow_run_id, WorkflowStepName.DIFF.value)
    details = details or {}
    image = str(details.get("desired_image") or "")
    basis = details.get("basis") if isinstance(details.get("basis"), Mapping) else {}
    try:
        replicas = int(basis.get("replicas", DEFAULT_PROMOTION_REPLICAS))
    except (TypeError, ValueError):
        replicas = DEFAULT_PROMOTION_REPLICAS
    return image, replicas


def entry_webhook_for_binding(
    *,
    workspace_id: str,
    application_id: str,
    binding: JsonObject,
    commit_sha: str,
    image: str,
    replicas: int,
    repo_ref: str,
    branch: str,
) -> GitWebhookReceivedBody:
    """대상 바인딩으로 표준 파이프라인(렌더→디프→정책→승인→적용)을 재진입시키는 입구 이벤트."""
    return GitWebhookReceivedBody(
        commit_sha=commit_sha,
        image=image,
        replicas=replicas,
        workspace_id=workspace_id,
        repository_id=str(binding["repository_id"]),
        repo_ref=repo_ref,
        branch=branch,
        watch_target_id=str(binding.get("watch_target_id") or ""),
        binding_id=str(binding["binding_id"]),
        application_id=application_id,
        environment=str(binding["environment"]),
        cluster_id=str(binding["cluster_id"]),
        manifest_path=str(binding["manifest_path"]),
        source_type=binding_source_type(binding),
    )


async def run_exists_for(
    ctx: EventContext[WorkflowStore],
    *,
    workspace_id: str,
    application_id: str,
    binding: JsonObject,
    commit_sha: str,
) -> bool:
    run_id = derive_workflow_run_id(
        {
            "workspace_id": workspace_id,
            "application_id": application_id,
            "binding_id": binding["binding_id"],
            "environment": binding["environment"],
            "commit_sha": commit_sha,
        }
    )
    return await ctx.db.get_workflow_run(run_id) is not None


@app.on(WorkflowRunCompletedBody)
async def on_run_completed_promote(
    evt: WorkflowRunCompletedBody, ctx: EventContext[WorkflowStore]
) -> AsyncIterator[EventBody]:
    """승격 룰 — 성공 종결 run 의 바인딩에 promotes_to 가 있으면 같은 commit 으로
    대상 바인딩의 표준 파이프라인을 연다. 대상 run 이 이미 있으면(재전달·순환) no-op."""
    source = await ctx.db.get_deployment_binding(evt.workspace_id, evt.binding_id)
    if not source:
        return
    target_id = str(binding_policy(source).get(PROMOTES_TO_KEY) or "")
    if not target_id or target_id == evt.binding_id:
        return
    target = await ctx.db.get_deployment_binding(evt.workspace_id, target_id)
    if not target:
        LOGGER.warning(
            "promotion_target_missing",
            extra={"context": {"binding_id": evt.binding_id, "target": target_id}},
        )
        return
    run = await ctx.db.get_workflow_run(evt.workflow_run_id)
    commit_sha = str((run or {}).get("commit_sha") or "")
    if not commit_sha:
        return
    if await run_exists_for(
        ctx,
        workspace_id=evt.workspace_id,
        application_id=evt.application_id,
        binding=target,
        commit_sha=commit_sha,
    ):
        return
    application = await ctx.db.get_application(evt.workspace_id, evt.application_id)
    if not application:
        return
    image, replicas = await promoted_image_and_replicas(ctx, evt.workflow_run_id)
    if not image:
        LOGGER.warning(
            "promotion_skipped_no_image",
            extra={"context": {"workflow_run_id": evt.workflow_run_id}},
        )
        return
    LOGGER.info(
        "promotion_triggered",
        extra={
            "context": {
                "from_binding": evt.binding_id,
                "to_binding": target_id,
                "commit_sha": commit_sha,
            }
        },
    )
    yield entry_webhook_for_binding(
        workspace_id=evt.workspace_id,
        application_id=evt.application_id,
        binding=target,
        commit_sha=commit_sha,
        image=image,
        replicas=replicas,
        repo_ref=str(application.get("repo_ref") or ""),
        branch=str(application.get("default_branch") or ""),
    )


async def fanout_global_bindings(
    evt: GitWebhookReceivedBody, ctx: EventContext[WorkflowStore]
) -> AsyncIterator[EventBody]:
    """글로벌 룰 — 웹훅을 같은 repo 의 global 바인딩들로 확장한다.

    자식(global 바인딩 대상) 웹훅은 재확장하지 않아 증폭이 1단으로 끝난다.
    이미 같은 commit 의 run 이 있는 바인딩은 건너뛴다(중복·재전달 수렴).
    """
    source = await ctx.db.get_deployment_binding(evt.workspace_id, evt.binding_id)
    if source is not None and binding_policy(source).get(GLOBAL_BINDING_KEY):
        return
    repository_id = str((source or {}).get("repository_id") or evt.repository_id or "")
    if not repository_id:
        return
    siblings = (
        await ctx.db.list_repository_deployment_bindings(evt.workspace_id, repository_id) or []
    )
    for binding in siblings:
        if str(binding["binding_id"]) == evt.binding_id:
            continue
        if not binding_policy(binding).get(GLOBAL_BINDING_KEY):
            continue
        registration = await ctx.db.get_cluster_registration(
            evt.workspace_id, str(binding["cluster_id"])
        )
        if is_management_registration(registration):
            LOGGER.warning(
                "global_binding_management_skipped",
                extra={"context": {"binding_id": binding["binding_id"]}},
            )
            continue
        if await run_exists_for(
            ctx,
            workspace_id=evt.workspace_id,
            application_id=evt.application_id,
            binding=binding,
            commit_sha=evt.commit_sha,
        ):
            continue
        LOGGER.info(
            "global_binding_fanout",
            extra={
                "context": {
                    "binding_id": binding["binding_id"],
                    "cluster_id": binding["cluster_id"],
                    "commit_sha": evt.commit_sha,
                }
            },
        )
        yield entry_webhook_for_binding(
            workspace_id=evt.workspace_id,
            application_id=evt.application_id,
            binding=binding,
            commit_sha=evt.commit_sha,
            image=evt.image,
            replicas=evt.replicas,
            repo_ref=evt.repo_ref,
            branch=evt.branch,
        )


@app.on(ClusterDesiredStateChangedBody)
async def on_cluster_registered_attach_globals(
    evt: ClusterDesiredStateChangedBody, ctx: EventContext[WorkflowStore]
) -> AsyncIterator[EventBody]:
    """신규 클러스터 합류 — 글로벌 그룹의 바인딩을 자동 생성하고, 템플릿 바인딩의
    최근 성공 run 이 있으면 그 commit·image 로 초기 배포 파이프라인을 연다."""
    if evt.reason != CLUSTER_REGISTERED_REASON:
        return
    registration = await ctx.db.get_cluster_registration(evt.workspace_id, evt.cluster_id)
    if is_management_registration(registration):
        LOGGER.info(
            "global_binding_management_attach_skipped",
            extra={"context": {"cluster_id": evt.cluster_id}},
        )
        return
    bindings = await ctx.db.list_workspace_deployment_bindings(evt.workspace_id) or []
    global_bindings = [b for b in bindings if binding_policy(b).get(GLOBAL_BINDING_KEY)]
    groups: dict[tuple[str, str, str], list[JsonObject]] = {}
    for binding in global_bindings:
        key = (str(binding["repository_id"]), str(binding["app_name"]), str(binding["namespace"]))
        groups.setdefault(key, []).append(binding)
    for members in groups.values():
        if any(str(member["cluster_id"]) == evt.cluster_id for member in members):
            continue
        template = members[0]
        created = await ctx.db.register_deployment_binding(
            {
                "workspace_id": evt.workspace_id,
                "repository_id": template["repository_id"],
                "watch_target_id": template.get("watch_target_id"),
                "cluster_id": evt.cluster_id,
                "namespace": template["namespace"],
                "app_name": template["app_name"],
                "manifest_path": template["manifest_path"],
                "environment": template["environment"],
                "resource_class": template.get("resource_class", "application"),
                "deploy_policy": binding_policy(template),
                "access_policy": dict(template.get("access_policy") or {}),
            }
        )
        LOGGER.info(
            "global_binding_attached",
            extra={
                "context": {
                    "cluster_id": evt.cluster_id,
                    "binding_id": created.get("binding_id"),
                    "app_name": template["app_name"],
                }
            },
        )
        last = await ctx.db.latest_succeeded_run_for_binding(
            evt.workspace_id, str(template["binding_id"])
        )
        if not last:
            continue  # 배포 이력 없음 — 다음 git 변경 때 fan-out 으로 합류
        image, replicas = await promoted_image_and_replicas(ctx, str(last["workflow_run_id"]))
        application = await ctx.db.get_application(evt.workspace_id, str(last["application_id"]))
        if not image or not application:
            continue
        new_binding = await ctx.db.get_deployment_binding(
            evt.workspace_id, str(created["binding_id"])
        )
        if not new_binding:
            continue
        yield entry_webhook_for_binding(
            workspace_id=evt.workspace_id,
            application_id=str(last["application_id"]),
            binding=new_binding,
            commit_sha=str(last["commit_sha"]),
            image=image,
            replicas=replicas,
            repo_ref=str(application.get("repo_ref") or ""),
            branch=str(application.get("default_branch") or ""),
        )


if __name__ == "__main__":
    app.run()
