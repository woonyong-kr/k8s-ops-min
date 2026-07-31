"""gitops 이벤트 body."""

from __future__ import annotations

from dataclasses import dataclass, field

from packages.config.constants import RiskLevel, Target
from packages.contracts.event_bus.bodies.base import EventBody, JsonObject
from packages.contracts.event_bus.registry import event
from packages.contracts.event_bus.subjects import EventSubject
from packages.contracts.gitops import (
    DEFAULT_APPLICATION_ID,
    DEFAULT_DEPLOYMENT_BINDING_ID,
    DEFAULT_ENVIRONMENT,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_REPO_BRANCH,
    DEFAULT_REPO_REF,
    DEFAULT_REPOSITORY_ID,
    DEFAULT_WATCH_TARGET_ID,
    DEFAULT_WORKFLOW_RUN_ID,
    ResourceClass,
)
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, ResourceRole


@dataclass(frozen=True)
class Manifest(EventBody):
    """sandbox에 동기화할 배포 사양(값 객체)."""

    app: str
    image: str
    replicas: int
    namespace: str
    manifest_path: str = DEFAULT_MANIFEST_PATH


@dataclass(frozen=True)
class RenderedMetadata(EventBody):
    """렌더된 k8s manifest의 metadata 블록(값 객체)."""

    name: str
    namespace: str


@dataclass(frozen=True)
class RenderedSpec(EventBody):
    """렌더된 k8s manifest의 spec 블록(값 객체)."""

    replicas: int = 0
    image: str = ""


@dataclass(frozen=True)
class RenderedManifest(EventBody):
    """렌더된 Kubernetes manifest(값 객체)."""

    api_version: str = field(metadata={"payload_name": "apiVersion"})
    kind: str
    metadata: RenderedMetadata
    spec: RenderedSpec
    resource_class: str = ResourceClass.APPLICATION.value
    manifest: JsonObject = field(default_factory=dict)
    declared_fields: list[str] = field(default_factory=list)
    managed_fields: list[str] = field(default_factory=list)
    ignored_fields: list[str] = field(default_factory=list)
    last_approved_snapshot: JsonObject = field(default_factory=dict)
    artifact_digest: str = ""


@dataclass(frozen=True)
class Diff(EventBody):
    """원하는 상태와 실제 상태의 차이(값 객체)."""

    resource: str
    namespace: str
    desired_image: str
    actual_image: str
    risk: RiskLevel
    workspace_id: str = DEFAULT_WORKSPACE_ID
    repository_id: str = DEFAULT_REPOSITORY_ID
    watch_target_id: str = DEFAULT_WATCH_TARGET_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    application_id: str = DEFAULT_APPLICATION_ID
    workflow_run_id: str = DEFAULT_WORKFLOW_RUN_ID
    environment: str = DEFAULT_ENVIRONMENT
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    manifest_path: str = DEFAULT_MANIFEST_PATH
    resource_class: str = ResourceClass.APPLICATION.value
    desired_manifest: JsonObject = field(default_factory=dict)
    status: str = "legacy_image_diff"
    has_changes: bool = True
    changes: list[dict[str, object]] = field(default_factory=list)
    basis: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        # wire 호환: 재구성(from_body) 시 str 로 들어온 위험도를 RiskLevel 로 강제함
        if not isinstance(self.risk, RiskLevel):
            object.__setattr__(self, "risk", RiskLevel(self.risk))

    def is_image_only_noop(self) -> bool:
        """이미지 비교만으로 no-op 판정이 가능한 legacy diff인지 확인."""

        return (
            bool(self.desired_image)
            and self.desired_image == self.actual_image
            and not self.desired_manifest
        )


@event(EventSubject.GIT_WEBHOOK_RECEIVED)
@dataclass(frozen=True)
class GitWebhookReceivedBody(EventBody):
    """git.webhook.received — 깃 webhook 입력(gitops 입력)."""

    commit_sha: str
    image: str
    replicas: int
    correlation_id: str | None = None
    workspace_id: str = DEFAULT_WORKSPACE_ID
    repository_id: str = DEFAULT_REPOSITORY_ID
    repo_ref: str = DEFAULT_REPO_REF
    branch: str = DEFAULT_REPO_BRANCH
    watch_target_id: str = DEFAULT_WATCH_TARGET_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    application_id: str = DEFAULT_APPLICATION_ID
    workflow_run_id: str = DEFAULT_WORKFLOW_RUN_ID
    environment: str = DEFAULT_ENVIRONMENT
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    manifest_path: str = DEFAULT_MANIFEST_PATH
    source_type: str = ""
    force: bool = False
    correlation_id: str = ""


@event(EventSubject.GIT_CHANGED)
@dataclass(frozen=True)
class GitChangedBody(EventBody):
    """git.changed — 깃 변경 확정(원시 변경 정보)."""

    commit_sha: str
    image: str
    replicas: int
    workspace_id: str = DEFAULT_WORKSPACE_ID
    repository_id: str = DEFAULT_REPOSITORY_ID
    repo_ref: str = DEFAULT_REPO_REF
    branch: str = DEFAULT_REPO_BRANCH
    watch_target_id: str = DEFAULT_WATCH_TARGET_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    application_id: str = DEFAULT_APPLICATION_ID
    workflow_run_id: str = DEFAULT_WORKFLOW_RUN_ID
    environment: str = DEFAULT_ENVIRONMENT
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    manifest_path: str = DEFAULT_MANIFEST_PATH
    source_type: str = ""


@event(EventSubject.MANIFEST_RENDERED)
@dataclass(frozen=True)
class ManifestRenderedBody(EventBody):
    """manifest.rendered — k8s manifest 렌더 결과."""

    rendered_manifest: RenderedManifest
    workspace_id: str = DEFAULT_WORKSPACE_ID
    repository_id: str = DEFAULT_REPOSITORY_ID
    watch_target_id: str = DEFAULT_WATCH_TARGET_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    application_id: str = DEFAULT_APPLICATION_ID
    workflow_run_id: str = DEFAULT_WORKFLOW_RUN_ID
    environment: str = DEFAULT_ENVIRONMENT
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    commit_sha: str = ""
    manifest_path: str = DEFAULT_MANIFEST_PATH
    repo_ref: str = DEFAULT_REPO_REF
    branch: str = DEFAULT_REPO_BRANCH


@event(EventSubject.MANIFEST_INVALID)
@dataclass(frozen=True)
class ManifestInvalidBody(EventBody):
    """manifest.invalid — repo는 관찰됐지만 배포 가능한 manifest가 아님."""

    workspace_id: str
    repository_id: str
    watch_target_id: str
    binding_id: str
    commit_sha: str
    manifest_path: str
    reason: str
    application_id: str = DEFAULT_APPLICATION_ID
    workflow_run_id: str = DEFAULT_WORKFLOW_RUN_ID
    environment: str = DEFAULT_ENVIRONMENT
    cluster_id: str = Target.DEFAULT_CLUSTER_ID


@event(EventSubject.DESIRED_DIFF_DETECTED)
@dataclass(frozen=True)
class DesiredDesiredDiffDetectedBody(EventBody):
    """desired.diff.detected — 적용해야 할 차이를 감지."""

    diff: Diff


@event(EventSubject.GITOPS_CHANGE_CONTEXT_DETECTED)
@dataclass(frozen=True)
class GitOpsChangeContextDetectedBody(EventBody):
    """gitops.change_context.detected — RCA용 변경 맥락 metadata."""

    metadata: JsonObject
    workspace_id: str = DEFAULT_WORKSPACE_ID
    repository_id: str = DEFAULT_REPOSITORY_ID
    watch_target_id: str = DEFAULT_WATCH_TARGET_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    application_id: str = DEFAULT_APPLICATION_ID
    workflow_run_id: str = DEFAULT_WORKFLOW_RUN_ID
    environment: str = DEFAULT_ENVIRONMENT
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    commit_sha: str = ""
    manifest_path: str = DEFAULT_MANIFEST_PATH
    repo_ref: str = DEFAULT_REPO_REF
    branch: str = DEFAULT_REPO_BRANCH
    resource: str = ""


@event(EventSubject.DIFF_ANALYZED)
@dataclass(frozen=True)
class DiffAnalyzedBody(EventBody):
    """diff.analyzed — diff 위험도 분석 결과."""

    diff: Diff
    safe: bool
    risk: str
    reason: str


@event(EventSubject.WORKFLOW_CREATED)
@dataclass(frozen=True)
class WorkflowCreatedBody(EventBody):
    """workflow.created — 앱/바인딩/커밋 기준 실행 객체 생성 요청."""

    workspace_id: str = DEFAULT_WORKSPACE_ID
    application_id: str = DEFAULT_APPLICATION_ID
    workflow_run_id: str = DEFAULT_WORKFLOW_RUN_ID
    repository_id: str = DEFAULT_REPOSITORY_ID
    watch_target_id: str = DEFAULT_WATCH_TARGET_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    environment: str = DEFAULT_ENVIRONMENT
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    commit_sha: str = ""
    manifest_path: str = DEFAULT_MANIFEST_PATH


@event(EventSubject.WORKFLOW_RUN_STARTED)
@dataclass(frozen=True)
class WorkflowRunStartedBody(EventBody):
    """workflow.run.started — 사용자에게 보이는 배포 실행 객체 시작."""

    workflow_run_id: str
    application_id: str
    workspace_id: str = DEFAULT_WORKSPACE_ID
    repository_id: str = DEFAULT_REPOSITORY_ID
    watch_target_id: str = DEFAULT_WATCH_TARGET_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    environment: str = DEFAULT_ENVIRONMENT
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    commit_sha: str = ""
    manifest_path: str = DEFAULT_MANIFEST_PATH
    status: str = "started"
    current_step: str = "git"


@event(EventSubject.WORKFLOW_STEP_RECORDED)
@dataclass(frozen=True)
class WorkflowStepRecordedBody(EventBody):
    """workflow.step.recorded — workflow 단계별 상태 기록."""

    workflow_run_id: str
    application_id: str
    step: str
    status: str
    workspace_id: str = DEFAULT_WORKSPACE_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    environment: str = DEFAULT_ENVIRONMENT
    message: str | None = None
    details: dict[str, object] = field(default_factory=dict)


@event(EventSubject.APPROVAL_REQUESTED)
@dataclass(frozen=True)
class ApprovalRequestedBody(EventBody):
    """approval.requested — write-by-approval 게이트 대기."""

    approval_id: str
    workflow_run_id: str
    application_id: str
    reason: str
    workspace_id: str = DEFAULT_WORKSPACE_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    environment: str = DEFAULT_ENVIRONMENT
    requested_role: str = ResourceRole.RELEASE_OPERATOR.value
    details: dict[str, object] = field(default_factory=dict)


@event(EventSubject.APPROVAL_GRANTED)
@dataclass(frozen=True)
class ApprovalGrantedBody(EventBody):
    """approval.granted — 승인 완료 또는 정책상 자동 승인."""

    approval_id: str
    workflow_run_id: str
    application_id: str
    workspace_id: str = DEFAULT_WORKSPACE_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    environment: str = DEFAULT_ENVIRONMENT
    decided_by: str | None = None
    decision: str = "granted"
    details: dict[str, object] = field(default_factory=dict)


@event(EventSubject.APPROVAL_REJECTED)
@dataclass(frozen=True)
class ApprovalRejectedBody(EventBody):
    """approval.rejected — 승인 거절."""

    approval_id: str
    workflow_run_id: str
    application_id: str
    reason: str
    workspace_id: str = DEFAULT_WORKSPACE_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    environment: str = DEFAULT_ENVIRONMENT
    decided_by: str | None = None
    details: dict[str, object] = field(default_factory=dict)


@event(EventSubject.WORKFLOW_RUN_COMPLETED)
@dataclass(frozen=True)
class WorkflowRunCompletedBody(EventBody):
    """workflow.run.completed — 배포 실행 성공 종료."""

    workflow_run_id: str
    application_id: str
    workspace_id: str = DEFAULT_WORKSPACE_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    environment: str = DEFAULT_ENVIRONMENT
    summary: str = "workflow succeeded"
    details: dict[str, object] = field(default_factory=dict)


@event(EventSubject.WORKFLOW_RUN_FAILED)
@dataclass(frozen=True)
class WorkflowRunFailedBody(EventBody):
    """workflow.run.failed — 배포 실행 실패 종료."""

    workflow_run_id: str
    application_id: str
    reason: str
    workspace_id: str = DEFAULT_WORKSPACE_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    environment: str = DEFAULT_ENVIRONMENT
    details: dict[str, object] = field(default_factory=dict)
