"""Safe PR preflight·위험 정책 — 준비/설명/SCM worker가 공유해 다른 consumer 경유 게이트 우회 차단."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Protocol

from domains.scm.events import SafePrFailedBody, SafePrRequestedBody

STAGE_PREPARE = "prepare"
STAGE_DIFF = "diff"
STAGE_SCM = "scm"
RISK_BLOCKED = "blocked"
RISK_REVIEW_REQUIRED = "review_required"
RISK_LOW = "low"

REASON_MISSING_PATCHES = "missing_patches"
REASON_UNSAFE_PATH = "unsafe_repository_path"
REASON_PROVIDER_MISMATCH = "provider_mismatch"
REASON_PROVIDER_ERROR = "provider_error"
CHANGE_DOCUMENT_DIR = ".gitops/safe-pr"

MESSAGE_MISSING_PATCHES = "safe pr requires at least one file patch"
MESSAGE_UNSAFE_PATH = "safe pr contains an unsafe repository path"
MESSAGE_PROVIDER_MISMATCH = "safe pr provider does not match worker provider"
MESSAGE_PROVIDER_ERROR = "safe pr provider failed before PR creation completed"


@dataclass(frozen=True)
class SafePrPolicyResult:
    allowed: bool
    reason_code: str = ""
    message: str = ""
    risk: str = RISK_LOW
    details: dict[str, object] = field(default_factory=dict)

    @classmethod
    def allow(
        cls, *, risk: str = RISK_LOW, details: dict[str, object] | None = None
    ) -> SafePrPolicyResult:
        return cls(allowed=True, risk=risk, details=details or {})

    @classmethod
    def reject(
        cls,
        *,
        reason_code: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> SafePrPolicyResult:
        return cls(
            allowed=False,
            reason_code=reason_code,
            message=message,
            risk=RISK_BLOCKED,
            details=details or {},
        )


class SafePrPreflightPolicy(Protocol):
    def evaluate(self, request: SafePrRequestedBody) -> SafePrPolicyResult: ...


class SafePrDiffPolicy(Protocol):
    def explain(self, request: SafePrRequestedBody) -> SafePrPolicyResult: ...


class DefaultSafePrPreflightPolicy:
    """구체적인 저장소 변경을 만들 수 없는 요청 거부."""

    def evaluate(self, request: SafePrRequestedBody) -> SafePrPolicyResult:
        if not request.patches:
            return SafePrPolicyResult.reject(
                reason_code=REASON_MISSING_PATCHES,
                message=MESSAGE_MISSING_PATCHES,
                details={
                    "workflow_run_id": request.workflow_run_id,
                    "repository_id": request.repository_id,
                },
            )
        try:
            validate_request_paths(request)
        except ValueError as exc:
            return SafePrPolicyResult.reject(
                reason_code=REASON_UNSAFE_PATH,
                message=MESSAGE_UNSAFE_PATH,
                details={"error": str(exc)},
            )
        return SafePrPolicyResult.allow(
            details={
                "patch_count": len(request.patches),
                "paths": [patch.path for patch in request.patches],
                "pr_kind": request.pr_kind,
            }
        )


class DefaultSafePrDiffPolicy:
    """준비된 Safe PR 요청 설명·게이트."""

    def __init__(self, preflight: SafePrPreflightPolicy | None = None) -> None:
        self.preflight = preflight or DefaultSafePrPreflightPolicy()

    def explain(self, request: SafePrRequestedBody) -> SafePrPolicyResult:
        preflight = self.preflight.evaluate(request)
        if not preflight.allowed:
            return preflight
        risk = (
            RISK_REVIEW_REQUIRED
            if request.approval_ref or request.policy_decision_ref
            else RISK_LOW
        )
        return SafePrPolicyResult.allow(
            risk=risk,
            details={
                **preflight.details,
                "pr_kind": request.pr_kind,
                "approval_ref": request.approval_ref,
                "policy_decision_ref": request.policy_decision_ref,
                "manifest_path": request.manifest_path,
            },
        )


def normalize_repo_path(path: str) -> str:
    raw = path.strip()
    if raw.startswith("/") or "\\" in raw:
        raise ValueError(f"unsafe repository path: {path}")
    normalized = str(PurePosixPath(raw))
    parts = PurePosixPath(normalized).parts
    if not normalized or normalized == "." or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"unsafe repository path: {path}")
    return normalized


def validate_request_paths(request: SafePrRequestedBody) -> None:
    normalize_repo_path(f"{CHANGE_DOCUMENT_DIR}/{request.workflow_run_id}.md")
    normalize_repo_path(request.manifest_path)
    for patch in request.patches:
        normalize_repo_path(patch.path)


def safe_pr_failed_body(
    request: SafePrRequestedBody,
    result: SafePrPolicyResult,
    *,
    stage: str,
) -> SafePrFailedBody:
    return SafePrFailedBody(
        provider=request.provider,
        title=request.title,
        reason=result.message,
        workspace_id=request.workspace_id,
        repository_id=request.repository_id,
        binding_id=request.binding_id,
        application_id=request.application_id,
        workflow_run_id=request.workflow_run_id,
        environment=request.environment,
        manifest_path=request.manifest_path,
        repo_ref=request.repo_ref,
        base_branch=request.base_branch,
        commit_sha=request.commit_sha,
        patch_sha256=request.patch_sha256,
        reason_code=result.reason_code,
        stage=stage,
        details={
            **result.details,
            "approval_ref": request.approval_ref,
            "policy_decision_ref": request.policy_decision_ref,
        },
    )
