"""diff-analyze-worker — desired.diff.detected → 위험도 분석 → diff.analyzed.

우현 원본 GitOpsSyncWorkflow.handle()의 COMMAND_REQUESTED 직접 발행 블록을
대체. 지금 구조는 diff를 바로 실행 명령으로 보내지 않고 안전 판정 후
safe_pr.requested로 넘겨 repo-gateway가 PR 생성을 맡게 분리.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import yaml

from domains.alert.events import AlertRequestedBody
from domains.command.events import CommandRequestedBody
from domains.gitops.approvals import resource_approval_qualifier
from domains.gitops.diffing import MISSING
from domains.gitops.events import DesiredDesiredDiffDetectedBody, Diff, DiffAnalyzedBody
from domains.gitops.recovery_merge import recovery_merge_authorization
from domains.gitops.repository import derive_approval_id
from domains.scm.events import SafePrFilePatch, SafePrRequestedBody
from packages.config.constants import Command, GitHub, RiskLevel, Sandbox, Target
from packages.contracts.event_bus.bodies import EventBody
from packages.contracts.gitops import ApprovalStatus
from packages.contracts.identity import ResourceRole
from packages.contracts.stores import PolicyDecisionStore
from packages.runtime.app import App, EventContext

app = App("diff-analyze-worker")

SAFE_REASON = "sandbox 한정 변경이라 안전"
UNSAFE_REASON = "프로덕션 영향 가능 — 검토 필요"
NO_ACTIONABLE_CHANGE_REASON = "managed field 기준 적용할 변경 없음"
PR_TITLE = "Apply sandbox manifest"
PRE_DEPLOY_ALERT_SEVERITY = "info"
MANIFEST_PATCH_DESCRIPTION = "rendered Kubernetes manifest"
ROLLBACK_PATCH_DESCRIPTION = "rollback manifest generated from live/previous values"
ROLLBACK_PATCH_DIR = ".gitops/rollback"
POLICY_ROUTE_NOOP = "noop"
POLICY_ROUTE_SAFE_PR = "safe_pr"
POLICY_ROUTE_APPROVAL_REQUIRED = "approval_required"
POLICY_ROUTE_RECOVERY_PR_MERGED = "recovery_pr_merged"
POLICY_DECISION_REF_PREFIX = "policy-decision"
SYSTEM_POLICY_APPROVER = "system-policy"
RECOVERY_PR_MERGE_REASON = "operator-reviewed recovery PR merged"
RECOVERY_PR_SCOPE_REJECTED_REASON = (
    "resource is outside the exact operator-reviewed recovery change"
)


@dataclass(frozen=True)
class PolicyDecision:
    route: str
    safe: bool
    reason: str
    approval_ref: str | None = None
    policy_decision_ref: str | None = None

    def details(self, diff: Diff) -> dict[str, object]:
        return {
            "policy_route": self.route,
            "policy_decision_ref": self.policy_decision_ref,
            "approval_ref": self.approval_ref,
            "safe": self.safe,
            "risk": str(diff.risk),
            "diff": diff.to_body(),
            "rollback_patches": [patch.to_body() for patch in build_rollback_patches(diff)],
        }


def evaluate_safe_pr_policy(diff: Diff) -> PolicyDecision:
    if diff.is_image_only_noop():
        return PolicyDecision(route=POLICY_ROUTE_NOOP, safe=False, reason=Sandbox.NO_DIFF_REASON)
    if not diff.has_changes:
        return PolicyDecision(
            route=POLICY_ROUTE_NOOP,
            safe=False,
            reason=NO_ACTIONABLE_CHANGE_REASON,
        )
    approval_ref = derive_approval_id(diff.workflow_run_id, approval_qualifier(diff))
    if diff.risk == RiskLevel.SANDBOX_ONLY:
        return PolicyDecision(
            route=POLICY_ROUTE_SAFE_PR,
            safe=True,
            reason=SAFE_REASON,
            approval_ref=approval_ref,
            policy_decision_ref=policy_decision_ref(approval_ref, POLICY_ROUTE_SAFE_PR),
        )
    return PolicyDecision(
        route=POLICY_ROUTE_APPROVAL_REQUIRED,
        safe=False,
        reason=UNSAFE_REASON,
        approval_ref=approval_ref,
        policy_decision_ref=policy_decision_ref(approval_ref, POLICY_ROUTE_APPROVAL_REQUIRED),
    )


def policy_decision_ref(approval_ref: str, route: str) -> str:
    return f"{POLICY_DECISION_REF_PREFIX}:{approval_ref}:{route}"


def approval_qualifier(diff: Diff) -> str:
    basis = diff.basis if isinstance(diff.basis, dict) else {}
    return resource_approval_qualifier(
        diff.namespace,
        diff.resource,
        basis.get("artifact_digest"),
    )


async def persist_policy_decision(
    db: PolicyDecisionStore | Any, diff: Diff, decision: PolicyDecision
) -> None:
    if db is None or not decision.approval_ref:
        return
    request = getattr(db, "request_workflow_approval", None)
    resolve = getattr(db, "resolve_workflow_approval", None)
    if request is None:
        return

    approval_payload = {
        "approval_id": decision.approval_ref,
        "workflow_run_id": diff.workflow_run_id,
        "workspace_id": diff.workspace_id,
        "application_id": diff.application_id,
        "binding_id": diff.binding_id,
        "environment": diff.environment,
        "status": (
            ApprovalStatus.NOT_REQUIRED.value
            if decision.route == POLICY_ROUTE_SAFE_PR
            else ApprovalStatus.REQUESTED.value
        ),
        "reason": decision.reason,
        "requested_role": ResourceRole.RELEASE_OPERATOR.value,
        "details": decision.details(diff),
    }
    await request(approval_payload)
    if decision.route == POLICY_ROUTE_SAFE_PR and resolve is not None:
        await resolve(
            {
                **approval_payload,
                "status": ApprovalStatus.GRANTED.value,
                "decided_by": SYSTEM_POLICY_APPROVER,
                "decision": "auto-approved",
            }
        )


def build_safe_pr_request_body(
    diff: Diff, decision: PolicyDecision | None = None
) -> SafePrRequestedBody:
    decision = decision or evaluate_safe_pr_policy(diff)
    summary = (
        f"{diff.resource}: {diff.actual_image} → {diff.desired_image}"
        if diff.desired_image and diff.desired_image != diff.actual_image
        else f"{diff.resource}: apply rendered manifest"
    )
    return SafePrRequestedBody(
        title=PR_TITLE,
        body=safe_pr_body(summary, diff, decision),
        provider=GitHub.PROVIDER,
        workspace_id=diff.workspace_id,
        repository_id=diff.repository_id,
        binding_id=diff.binding_id,
        application_id=diff.application_id,
        workflow_run_id=diff.workflow_run_id,
        environment=diff.environment,
        manifest_path=diff.manifest_path,
        commit_sha=str(diff.basis.get("commit_sha") or ""),
        cluster_id=diff.cluster_id,
        target_namespace=diff.namespace,
        target_resource=diff.resource,
        target_authority="policy_approval",
        patches=build_manifest_patches(diff),
        approval_ref=decision.approval_ref,
        policy_decision_ref=decision.policy_decision_ref,
        next_alert=build_pre_deploy_alert_request_body(diff, decision),
    )


def safe_pr_body(summary: str, diff: Diff, decision: PolicyDecision) -> str:
    rollback_paths = [patch.path for patch in build_rollback_patches(diff)]
    rollback_line = ", ".join(f"`{path}`" for path in rollback_paths) or "없음"
    artifact_digest = str(diff.basis.get("artifact_digest") or "")
    digest_line = f"\n- artifact_digest: `{artifact_digest}`" if artifact_digest else ""
    return (
        f"{summary}\n\n"
        "## GitOps Basis\n\n"
        f"- approval_ref: `{decision.approval_ref or ''}`\n"
        f"- policy_decision_ref: `{decision.policy_decision_ref or ''}`\n"
        f"- diff_status: `{diff.status}`\n"
        f"- diff_basis: `{diff.basis.get('comparison', 'unknown')}`"
        f"{digest_line}\n"
        f"- rollback_patch: {rollback_line}\n"
    )


def build_manifest_patches(diff: Diff) -> list[SafePrFilePatch]:
    if not diff.desired_manifest:
        return []
    patches = [
        SafePrFilePatch(
            path=diff.manifest_path,
            content=yaml.safe_dump(
                diff.desired_manifest,
                sort_keys=False,
                allow_unicode=True,
            ),
            description=MANIFEST_PATCH_DESCRIPTION,
        )
    ]
    patches.extend(build_rollback_patches(diff))
    return patches


def build_rollback_patches(diff: Diff) -> list[SafePrFilePatch]:
    rollback = build_rollback_manifest(diff)
    if not rollback:
        return []
    return [
        SafePrFilePatch(
            path=rollback_patch_path(diff),
            content=yaml.safe_dump(rollback, sort_keys=False, allow_unicode=True),
            description=ROLLBACK_PATCH_DESCRIPTION,
        )
    ]


def rollback_patch_path(diff: Diff) -> str:
    manifest_name = PurePosixPath(diff.manifest_path).name or "manifest.yaml"
    if "." not in manifest_name:
        manifest_name = f"{manifest_name}.yaml"
    resource = diff.resource.replace("/", "-") or "resource"
    return f"{ROLLBACK_PATCH_DIR}/{diff.workflow_run_id}/{resource}-{manifest_name}"


def build_rollback_manifest(diff: Diff) -> dict[str, Any]:
    if not diff.desired_manifest or not diff.changes:
        return {}
    rollback = deepcopy(diff.desired_manifest)
    applied = False
    for change in diff.changes:
        classification = str(change.get("classification", ""))
        if classification == "already_converged":
            continue
        field_path = str(change.get("field_path", ""))
        if not field_path:
            continue
        before = change.get("before", change.get("live", MISSING))
        if apply_rollback_value(rollback, field_path, before):
            applied = True
    return rollback if applied else {}


def apply_rollback_value(manifest: dict[str, Any], field_path: str, value: Any) -> bool:
    parts = split_field_path(field_path)
    if not parts:
        return False
    parent: Any = manifest
    for part in parts[:-1]:
        parent = descend_manifest_path(parent, part)
        if parent is None:
            return False
    return set_manifest_value(parent, parts[-1], value)


def split_field_path(field_path: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    bracket_depth = 0
    for char in field_path:
        if char == "[":
            bracket_depth += 1
        elif char == "]" and bracket_depth:
            bracket_depth -= 1
        if char == "." and bracket_depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def descend_manifest_path(parent: Any, part: str) -> Any:
    list_name, selector = parse_list_selector(part)
    if list_name is None:
        if not isinstance(parent, dict):
            return None
        child = parent.get(part)
        if not isinstance(child, dict | list):
            return None
        return child
    if not isinstance(parent, dict):
        return None
    items = parent.get(list_name)
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and str(item.get(selector[0], "")) == selector[1]:
            return item
    return None


def set_manifest_value(parent: Any, part: str, value: Any) -> bool:
    list_name, selector = parse_list_selector(part)
    if list_name is not None:
        target = descend_manifest_path(parent, part)
        if not isinstance(target, dict):
            return False
        if value == MISSING:
            target.pop(selector[0], None)
        else:
            target[selector[0]] = value
        return True
    if not isinstance(parent, dict):
        return False
    if value == MISSING:
        parent.pop(part, None)
    else:
        parent[part] = value
    return True


def parse_list_selector(part: str) -> tuple[str | None, tuple[str, str]]:
    if "[" not in part or not part.endswith("]"):
        return None, ("", "")
    name, raw_selector = part.split("[", 1)
    key, separator, value = raw_selector[:-1].partition("=")
    if not name or not separator or not key:
        return None, ("", "")
    return name, (key, value)


def build_auto_command_request_body(
    diff: Diff, decision: PolicyDecision | None = None
) -> CommandRequestedBody:
    decision = decision or evaluate_safe_pr_policy(diff)
    return CommandRequestedBody(
        cluster_id=diff.cluster_id or Target.DEFAULT_CLUSTER_ID,
        action=Command.APPLY_MANIFEST_ACTION,
        namespace=diff.namespace,
        reason="safe sandbox gitops apply",
        diff=diff,
        workspace_id=diff.workspace_id,
        application_id=diff.application_id,
        workflow_run_id=diff.workflow_run_id,
        binding_id=diff.binding_id,
        environment=diff.environment,
        approval_ref=decision.approval_ref,
        policy_decision_ref=decision.policy_decision_ref,
    )


def build_pre_deploy_alert_request_body(
    diff: Diff, decision: PolicyDecision | None = None
) -> AlertRequestedBody:
    decision = decision or evaluate_safe_pr_policy(diff)
    return AlertRequestedBody(
        cluster_id=diff.cluster_id or Target.DEFAULT_CLUSTER_ID,
        namespace=diff.namespace,
        severity=PRE_DEPLOY_ALERT_SEVERITY,
        message=f"pre-deploy check passed for {diff.resource}",
        reason="safe sandbox deploy will continue after alert gate",
        next_command=build_auto_command_request_body(diff, decision),
        workspace_id=diff.workspace_id,
        application_id=diff.application_id,
        workflow_run_id=diff.workflow_run_id,
        binding_id=diff.binding_id,
        environment=diff.environment,
    )


@app.on(DesiredDesiredDiffDetectedBody)
async def on_desired_diff(
    evt: DesiredDesiredDiffDetectedBody, ctx: EventContext
) -> AsyncIterator[EventBody]:
    diff = evt.diff
    recovery_authorization = await recovery_merge_authorization(ctx.db, diff)
    if recovery_authorization.tracked and recovery_authorization.request is None:
        yield DiffAnalyzedBody(
            diff=diff,
            safe=False,
            risk=diff.risk,
            reason=RECOVERY_PR_SCOPE_REJECTED_REASON,
        )
        return
    if recovery_authorization.request is not None:
        approval_ref = derive_approval_id(diff.workflow_run_id, approval_qualifier(diff))
        decision = PolicyDecision(
            route=POLICY_ROUTE_RECOVERY_PR_MERGED,
            safe=False,
            reason=RECOVERY_PR_MERGE_REASON,
            approval_ref=approval_ref,
            policy_decision_ref=policy_decision_ref(
                approval_ref,
                POLICY_ROUTE_RECOVERY_PR_MERGED,
            ),
        )
    else:
        decision = evaluate_safe_pr_policy(diff)
    await persist_policy_decision(ctx.db, diff, decision)
    yield DiffAnalyzedBody(diff=diff, safe=decision.safe, risk=diff.risk, reason=decision.reason)
    if decision.route == POLICY_ROUTE_SAFE_PR:
        # PR 생성 성공 뒤에만 alert/apply 흐름이 이어짐.
        yield build_safe_pr_request_body(diff, decision)


if __name__ == "__main__":
    app.run()
