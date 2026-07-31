from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, SecretStr, field_validator, model_validator

from packages.config.constants import Command, CommandStatus, Sandbox, Target
from packages.contracts.demo_seed import (
    DEMO_SEED_MARKER_KEY,
    DEMO_SEED_MARKER_RESERVED_MESSAGE,
)
from packages.contracts.evidence_policy import EvidenceProfile
from packages.contracts.gateway.base import StrictModel
from packages.contracts.gitops import (
    DEFAULT_APPLICATION_ID,
    DEFAULT_DEPLOYMENT_BINDING_ID,
    DEFAULT_ENVIRONMENT,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_REPO_BRANCH,
    DEFAULT_REPOSITORY_ID,
    DEFAULT_WATCH_TARGET_ID,
    DEFAULT_WORKFLOW_RUN_ID,
)
from packages.contracts.identity import DEFAULT_WORKSPACE_ID
from packages.contracts.kubernetes_discovery import (
    MAX_DYNAMIC_RESOURCE_NAMESPACES,
    MAX_KUBERNETES_API_VERSION_LENGTH,
    is_kubernetes_dns_label,
)
from packages.contracts.parity import ResourceRef
from packages.contracts.resource_files import ResourceFileResult
from packages.contracts.target import FAST_LANE_PRIORITY_CLASS_NAME

DEFAULT_WEBHOOK_REPLICAS = 2
MIN_WEBHOOK_REPLICAS = 1
MAX_WEBHOOK_REPLICAS = 10
DEFAULT_COMMAND_STATUS: Literal["completed", "failed"] = CommandStatus.COMPLETED
EMPTY_COMMAND_MESSAGE = ""
DEFAULT_TARGET_NAME = "target-cluster"
DEFAULT_TARGET_ENVIRONMENT = "sandbox"
# target cluster static telemetry defaults. Prometheus is intentionally absent:
# its revision-bound integration is the only runtime authority.
DEFAULT_LOKI_BASE_URL = "http://loki-gateway.target.svc"
DEFAULT_TEMPO_BASE_URL = "http://tempo.target.svc:3200"
DEFAULT_OTEL_SERVICE_NAME = "target-cluster-agent"
# Export is opt-in. The agent keeps its outbound management channel, but must not
# infer a direct in-cluster collector endpoint that was not explicitly configured.
DEFAULT_OTEL_TRACES_ENDPOINT = ""
MIN_EVIDENCE_INTERVAL_SECONDS = 1
MAX_EVIDENCE_INTERVAL_SECONDS = 3600
DEFAULT_EVIDENCE_JOB_MAX_ATTEMPTS = 3
MAX_EVIDENCE_JOB_MAX_ATTEMPTS = 10
DEFAULT_AGENT_POLICY_GENERATION = 1
DEFAULT_PROVIDER_INTERVAL_SECONDS = 8
DEFAULT_PROVIDER_MIN_WORKERS = 1
DEFAULT_PROVIDER_MAX_WORKERS = 3
DEFAULT_QUEUE_AGE_TARGET_SECONDS = 15
DEFAULT_AI_AGENT = "operations-chat"
MAX_AI_MESSAGE_LENGTH = 16_000
MAX_METRIC_QUERY_LENGTH = 4_000
MAX_METRIC_DEFINITION_JSON_BYTES = 16_384
MIN_METRIC_RANGE_SECONDS = 60
MAX_METRIC_RANGE_SECONDS = 86_400
MIN_METRIC_STEP_SECONDS = 1
MAX_METRIC_STEP_SECONDS = 3_600

# agent evidence 페이로드 상한 — 무한 크기 수집물이 DB/NATS/LLM 컨텍스트를 압박하지 않도록.
MAX_EVIDENCE_LOG_ENTRIES = 2000
MAX_EVIDENCE_PAYLOAD_BYTES = 1_048_576  # 직렬화 1MiB 상한(초과 시 422)
EVIDENCE_PAYLOAD_TOO_LARGE_MESSAGE = "evidence payload exceeds size limit"
MAX_INVENTORY_RESOURCES = 5000
MAX_INVENTORY_PAYLOAD_BYTES = 16 * 1024 * 1024
INVENTORY_PAYLOAD_TOO_LARGE_MESSAGE = "inventory payload exceeds size limit"
MAX_DEPLOYMENT_REPLICAS = 100
MAX_RESOURCE_MANIFEST_BYTES = 1_048_576


def normalize_control_namespaces(value: str) -> str:
    """Validate and canonicalize a bounded Kubernetes namespace CSV."""

    namespaces = tuple(
        dict.fromkeys(namespace.strip() for namespace in value.split(",") if namespace.strip())
    )
    if len(namespaces) > MAX_DYNAMIC_RESOURCE_NAMESPACES:
        raise ValueError(
            f"control_namespaces supports at most {MAX_DYNAMIC_RESOURCE_NAMESPACES} namespaces"
        )
    if any(not is_kubernetes_dns_label(namespace) for namespace in namespaces):
        raise ValueError("control_namespaces must contain Kubernetes DNS label namespaces")
    return ",".join(namespaces)


class LoginRequest(StrictModel):
    # 우리 서비스 자체 계정 로그인 입력값
    # role 같은 권한 필드는 클라이언트 입력 금지, 서버가 DB/session 기준 결정
    # 로그인은 고정 dev 관리자 식별자(`admin`)와 가입 계정 이메일을 모두 허용한다.
    email: str = Field(
        min_length=1,
        max_length=320,
        pattern=r"^[^@\s]+(?:@[^@\s]+\.[^@\s]+)?$",
    )
    password: str = Field(min_length=8)


class SignupRequest(StrictModel):
    # 가입도 권한 필드 입력 금지. 최초 role/session 정책은 서버 결정
    email: str = Field(min_length=1, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8)
    password_confirm: str = Field(min_length=8)


class ResendEmailVerificationRequest(StrictModel):
    email: str = Field(min_length=1, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8)


class EmailCheckRequest(StrictModel):
    email: str = Field(min_length=1, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class WorkspaceSwitchRequest(StrictModel):
    workspace_id: str = Field(min_length=1, max_length=200)


class GitHubWebhookRequest(StrictModel):
    correlation_id: str | None = Field(default=None, min_length=1, max_length=2048)
    commit_sha: str
    image: str = Field(min_length=1)
    replicas: int = Field(
        default=DEFAULT_WEBHOOK_REPLICAS, ge=MIN_WEBHOOK_REPLICAS, le=MAX_WEBHOOK_REPLICAS
    )
    workspace_id: str = DEFAULT_WORKSPACE_ID
    repository_id: str = DEFAULT_REPOSITORY_ID
    repo_ref: str = Field(min_length=1)
    branch: str = DEFAULT_REPO_BRANCH
    watch_target_id: str = DEFAULT_WATCH_TARGET_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    application_id: str = DEFAULT_APPLICATION_ID
    workflow_run_id: str = DEFAULT_WORKFLOW_RUN_ID
    environment: str = DEFAULT_ENVIRONMENT
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    manifest_path: str = DEFAULT_MANIFEST_PATH
    source_type: str = Field(default="", max_length=40)
    force: bool = False


class ResourceManifestPreviewRequest(StrictModel):
    application_id: str = Field(min_length=1, max_length=200)
    base_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    source_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_revision_token: str | None = Field(default=None, min_length=32, max_length=8192)
    edited_yaml: str = Field(min_length=1, max_length=MAX_RESOURCE_MANIFEST_BYTES)


class ResourceManifestApproveRequest(ResourceManifestPreviewRequest):
    confirmed: Literal[True]
    reason: str = Field(min_length=3, max_length=500)


class ResourceManifestDirectApplyRequest(ResourceManifestPreviewRequest):
    expected_desired_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    confirmation: Literal[True]
    reason: str = Field(min_length=3, max_length=500)


class ResourceDeleteRequest(StrictModel):
    """One exact, preview-pinned destructive resource command."""

    preview_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    confirmation: Literal[True]
    reason: str = Field(min_length=3, max_length=500)
    idempotency_key: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]+$",
    )


class WorkloadRollbackRequest(StrictModel):
    """One preview-pinned rollback against exact workload and revision identities."""

    resource_id: str = Field(min_length=1, max_length=255)
    snapshot_id: str = Field(min_length=1, max_length=255)
    capability_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    workload: ResourceRef
    workload_resource_version: str = Field(min_length=1, max_length=253)
    target_revision: ResourceRef
    target_resource_version: str = Field(min_length=1, max_length=253)
    preview_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    confirmation: Literal[True]
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def validate_revision_pair(self) -> WorkloadRollbackRequest:
        workload_kind = self.workload.kind.casefold()
        expected_revision_kind = {
            "deployment": "replicaset",
            "statefulset": "controllerrevision",
            "daemonset": "controllerrevision",
        }.get(workload_kind)
        if (
            self.workload.api_group != "apps"
            or self.workload.version != "v1"
            or self.workload.namespace is None
            or expected_revision_kind is None
            or self.target_revision.api_group != "apps"
            or self.target_revision.version != "v1"
            or self.target_revision.kind.casefold() != expected_revision_kind
            or self.target_revision.namespace != self.workload.namespace
        ):
            raise ValueError("workload rollback requires an exact apps/v1 revision pair")
        return self


class ResourceManifestCreateDryRunRequest(StrictModel):
    cluster_id: str = Field(min_length=1, max_length=200)
    namespace: str = Field(min_length=1, max_length=253)
    snapshot_id: str = Field(min_length=1, max_length=200)
    edited_yaml: str = Field(min_length=1, max_length=MAX_RESOURCE_MANIFEST_BYTES)
    force: bool = False
    reason: str = Field(min_length=3, max_length=500)


class ResourceManifestCreateRequest(ResourceManifestCreateDryRunRequest):
    desired_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dry_run_command_id: str = Field(min_length=8, max_length=200)
    confirmation: Literal[True]
    force_confirmation: bool = False

    @model_validator(mode="after")
    def validate_force_confirmation(self) -> ResourceManifestCreateRequest:
        if self.force and not self.force_confirmation:
            raise ValueError("force create requires explicit risk confirmation")
        if not self.force and self.force_confirmation:
            raise ValueError("force confirmation is valid only when force is enabled")
        return self


class AgentConnectRequest(StrictModel):
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    agent_id: str
    capabilities: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class AgentEvidenceRequest(StrictModel):
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    correlation_id: str | None = None
    agent_id: str | None = None
    source_id: str | None = None
    window_start: str | None = None
    evidence_key: str | None = None
    workflow_run_id: str | None = None
    release_context: dict[str, Any] = Field(default_factory=dict)
    collection_status: dict[str, Any] = Field(default_factory=dict)
    kubernetes: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    logs: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_EVIDENCE_LOG_ENTRIES)
    traces: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _bound_payload_size(self) -> AgentEvidenceRequest:
        # logs 길이는 Field(max_length)로, 전체 수집물 크기는 직렬화 바이트로 상한.
        # (kubernetes/metrics/traces 는 중첩 dict 라 항목 수만으로는 못 막음)
        size = len(
            json.dumps(
                {
                    "kubernetes": self.kubernetes,
                    "metrics": self.metrics,
                    "logs": self.logs,
                    "traces": self.traces,
                    "metadata": self.metadata,
                    "release_context": self.release_context,
                    "collection_status": self.collection_status,
                },
                default=str,
            ).encode()
        )
        if size > MAX_EVIDENCE_PAYLOAD_BYTES:
            raise ValueError(EVIDENCE_PAYLOAD_TOO_LARGE_MESSAGE)
        return self


class RecoveryActionSelectRequest(StrictModel):
    reason: str | None = Field(default=None, max_length=500)


class RecoveryActionSelectByCorrelationRequest(StrictModel):
    expected_plan_id: str = Field(min_length=1, max_length=2048)
    action_id: str | None = Field(default=None, min_length=1, max_length=2048)
    reason: str | None = Field(default=None, max_length=500)


class RecoveryRetryRequest(StrictModel):
    expected_plan_id: str = Field(min_length=1, max_length=2048)
    reason: str | None = Field(default=None, max_length=500)


class RcaTestRunCreateRequest(StrictModel):
    """등록된 RCA 장애 시나리오 실행 요청 — manifest/evidence는 서버 카탈로그 소유."""

    cluster_id: str = Field(min_length=1, max_length=253)
    scenario_id: str = Field(min_length=1, max_length=120)


class InventoryResource(StrictModel):
    resource_type: str = Field(min_length=1, max_length=80)
    api_version: str = Field(default="", max_length=MAX_KUBERNETES_API_VERSION_LENGTH)
    kind: str = Field(default="", max_length=120)
    namespace: str | None = Field(default=None, max_length=253)
    name: str = Field(min_length=1, max_length=253)
    uid: str | None = Field(default=None, max_length=253)
    resource_version: str | None = Field(default=None, max_length=253)
    status: str = Field(default="unknown", max_length=80)
    health: str = Field(default="unknown", max_length=80)
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class InventorySnapshotRequest(StrictModel):
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    agent_id: str
    source: str = Field(default="cluster-agent", min_length=1, max_length=120)
    collected_at: str | None = None
    replace: bool = False
    resources: list[InventoryResource] = Field(
        default_factory=list, max_length=MAX_INVENTORY_RESOURCES
    )
    summary: dict[str, Any] = Field(default_factory=dict)
    health: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _bound_payload_size(self) -> InventorySnapshotRequest:
        # 리소스 raw/annotations는 중첩 구조이므로 항목 수와 직렬화 바이트를 함께 제한한다.
        size = len(
            json.dumps(
                self.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if size > MAX_INVENTORY_PAYLOAD_BYTES:
            raise ValueError(INVENTORY_PAYLOAD_TOO_LARGE_MESSAGE)
        return self


class TargetProviderSelectionRequest(StrictModel):
    cluster_role: Literal["management", "target"] = "target"
    management_base_url: str = ""
    image: str = ""
    apply: bool = False
    kube_context: str | None = None
    cloud_provider: str = "existing-k8s"
    deploy_provider: str = "manual-manifest"
    provider_config: dict[str, Any] = Field(default_factory=dict)


class TargetRegisterRequest(TargetProviderSelectionRequest):
    cluster_id: str | None = Field(default=None, max_length=253)
    name: str = DEFAULT_TARGET_NAME
    environment: str = DEFAULT_TARGET_ENVIRONMENT
    workspace_id: str = DEFAULT_WORKSPACE_ID
    loki_base_url: str = DEFAULT_LOKI_BASE_URL
    tempo_base_url: str = DEFAULT_TEMPO_BASE_URL
    otel_traces_endpoint: str = DEFAULT_OTEL_TRACES_ENDPOINT
    evidence_interval_seconds: int = Field(
        default=int(Target.DEFAULT_EVIDENCE_INTERVAL_SECONDS),
        ge=MIN_EVIDENCE_INTERVAL_SECONDS,
        le=MAX_EVIDENCE_INTERVAL_SECONDS,
    )
    # 제어(쓰기) 허용 네임스페이스 CSV — 빈 값이면 agent 기본(sandbox)만 허용.
    # 설치 manifest ConfigMap 의 CONTROL_ALLOWED_NAMESPACES 로 주입되어 클러스터별로 다르게 줄 수 있다.
    control_namespaces: str = Field(default="", max_length=2_047)
    install_node_collector: bool = True
    install_sample_workload: bool = False
    sample_workload_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$",
    )
    sample_workload_image: str | None = Field(default=None, min_length=1)

    @field_validator("control_namespaces")
    @classmethod
    def validate_control_namespaces(cls, value: str) -> str:
        return normalize_control_namespaces(value)

    @model_validator(mode="after")
    def _sample_workload_requires_explicit_config(self) -> TargetRegisterRequest:
        if self.install_sample_workload and (
            not self.sample_workload_name or not self.sample_workload_image
        ):
            raise ValueError(
                "sample_workload_name and sample_workload_image are required "
                "when install_sample_workload is true"
            )
        return self


class ClusterConnectRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"\S")
    # The lightweight connect flow only needs the user-facing name.  Provider
    # selection is retained as a backwards-compatible hint for older clients.
    provider: Literal["aws", "gcp", "azure", "onprem"] = "aws"
    environment: Literal["development", "staging", "production"] = "development"


class TargetPreflightRequest(TargetProviderSelectionRequest):
    cluster_id: str = Field(default="", max_length=253)
    name: str | None = Field(default=None, max_length=120)
    environment: str | None = Field(default=None, max_length=80)


class CommandRequest(StrictModel):
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    action: str = Command.DEFAULT_ACTION
    namespace: str = Sandbox.NAMESPACE
    reason: str | None = None
    diff: dict[str, Any] | None = None
    approval_ref: str | None = None
    policy_decision_ref: str | None = None
    # 사용자가 대상·영향을 확인했다는 입력. 서버만 이 값을 direct execution
    # 정책으로 승격한다. 아래 legacy 플래그는 실행 권한을 부여하지 않는다.
    confirmation: Literal[True] | None = None
    direct_execution: bool = False
    direct_execution_confirmed: bool = False


class DeploymentScaleRequest(StrictModel):
    replicas: int = Field(ge=0, le=MAX_DEPLOYMENT_REPLICAS)
    reason: str | None = Field(default=None, max_length=500)
    approval_ref: str | None = None
    policy_decision_ref: str | None = None
    confirmation: Literal[True] | None = None
    direct_execution: bool = False
    direct_execution_confirmed: bool = False


class DeploymentRestartRequest(StrictModel):
    reason: str | None = Field(default=None, max_length=500)
    approval_ref: str | None = None
    policy_decision_ref: str | None = None
    confirmation: Literal[True] | None = None
    direct_execution: bool = False
    direct_execution_confirmed: bool = False


class ConfirmedResourceActionRequest(StrictModel):
    """One server-discovered resource action acknowledged by the operator."""

    reason: str | None = Field(default=None, max_length=500)
    confirmation: Literal[True] | None = None
    direct_execution: bool = False
    direct_execution_confirmed: bool = False


class ExactResourceActionRequest(ConfirmedResourceActionRequest):
    """Capability-bound mutation against one exact inventory observation."""

    resource_id: str = Field(min_length=1, max_length=255)
    snapshot_id: str = Field(min_length=1, max_length=255)
    capability_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    resource: ResourceRef


class NodeDrainRequest(ExactResourceActionRequest):
    timeout_seconds: int = Field(default=60, ge=10, le=600)
    max_parallel: int = Field(default=8, ge=1, le=32)
    max_pods: int = Field(default=1000, ge=1, le=5000)
    force: bool = False
    delete_empty_dir_data: bool = False


class PodDebugRequest(ExactResourceActionRequest):
    target_container: str = Field(min_length=1, max_length=253)
    image: str = Field(
        min_length=1,
        max_length=1024,
        pattern=r"^\S+@sha256:[0-9a-f]{64}$",
    )


class NodeDebugRequest(ExactResourceActionRequest):
    namespace: str = Field(min_length=1, max_length=253)
    image: str = Field(
        min_length=1,
        max_length=1024,
        pattern=r"^\S+@sha256:[0-9a-f]{64}$",
    )


class NodeDebugCleanupRequest(ExactResourceActionRequest):
    namespace: str = Field(min_length=1, max_length=253)
    session_id: str = Field(min_length=8, max_length=128, pattern=r"^[a-z0-9-]+$")


class CronJobControlRequest(ConfirmedResourceActionRequest):
    """One capability-bound CronJob mutation against an exact observed UID."""

    resource_id: str = Field(min_length=1, max_length=255)
    snapshot_id: str = Field(min_length=1, max_length=255)
    capability_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    resource: ResourceRef

    @model_validator(mode="after")
    def validate_cronjob_resource(self) -> CronJobControlRequest:
        if (
            self.resource.api_group != "batch"
            or self.resource.version != "v1"
            or self.resource.kind.casefold() != "cronjob"
            or self.resource.namespace is None
        ):
            raise ValueError("CronJob control requires an exact batch/v1 ResourceRef")
        return self


class AgentDebugQueryRequest(StrictModel):
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    query: dict[str, Any]
    reason: str | None = None


class PrometheusQueryDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    source: Literal["prometheus"] = "prometheus"
    query: str = Field(min_length=1, max_length=MAX_METRIC_QUERY_LENGTH)
    range_seconds: int | None = Field(
        default=900,
        ge=MIN_METRIC_RANGE_SECONDS,
        le=MAX_METRIC_RANGE_SECONDS,
    )
    step_seconds: int | None = Field(
        default=30,
        ge=MIN_METRIC_STEP_SECONDS,
        le=MAX_METRIC_STEP_SECONDS,
    )

    @model_validator(mode="after")
    def _validate_bounds(self) -> PrometheusQueryDefinition:
        if (
            self.range_seconds is not None
            and self.step_seconds is not None
            and self.step_seconds > self.range_seconds
        ):
            raise ValueError("step_seconds must be less than or equal to range_seconds")
        return self


class MetricQueryPresetUpsertRequest(PrometheusQueryDefinition):
    preset_id: str | None = Field(default=None, min_length=1, max_length=120)
    unit: str = Field(default="", max_length=40)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_metadata_bound(self) -> MetricQueryPresetUpsertRequest:
        _ensure_metric_json_bound({"metadata": self.metadata})
        return self


class MetricWidgetUpsertRequest(StrictModel):
    widget_id: str | None = Field(default=None, min_length=1, max_length=120)
    query_preset_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=120)
    kind: Literal["line", "area", "bar", "stat", "table", "heatmap"] = "line"
    position: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_json_bounds(self) -> MetricWidgetUpsertRequest:
        _ensure_metric_json_bound({"position": self.position, "settings": self.settings})
        return self


def _ensure_metric_json_bound(value: dict[str, Any]) -> None:
    size = len(json.dumps(value, sort_keys=True, default=str).encode())
    if size > MAX_METRIC_DEFINITION_JSON_BYTES:
        raise ValueError("metric definition JSON exceeds size limit")


class AiConversationCreateRequest(StrictModel):
    message: str = Field(min_length=1, max_length=MAX_AI_MESSAGE_LENGTH)
    title: str | None = Field(default=None, max_length=120)
    agent: str = Field(default=DEFAULT_AI_AGENT, min_length=1, max_length=80)
    context: dict[str, Any] = Field(default_factory=dict)


class AiMessageCreateRequest(StrictModel):
    message: str = Field(min_length=1, max_length=MAX_AI_MESSAGE_LENGTH)
    agent: str | None = Field(default=None, min_length=1, max_length=80)
    context: dict[str, Any] = Field(default_factory=dict)


AiAssistantFilterValue = Annotated[str, Field(min_length=1, max_length=512)]


class AiAssistantFilters(StrictModel):
    """Canonical product filters that are safe to bind to AI evidence queries."""

    clusters: list[AiAssistantFilterValue] = Field(default_factory=list, max_length=50)
    namespaces: list[AiAssistantFilterValue] = Field(default_factory=list, max_length=100)
    applications: list[AiAssistantFilterValue] = Field(default_factory=list, max_length=100)
    labels: list[AiAssistantFilterValue] = Field(default_factory=list, max_length=100)
    resource_types: list[AiAssistantFilterValue] = Field(default_factory=list, max_length=50)
    health: list[AiAssistantFilterValue] = Field(default_factory=list, max_length=50)
    query: str = Field(default="", max_length=253)


class AiAssistantSelection(StrictModel):
    type: Literal["resource"]
    identity: str = Field(min_length=1, max_length=1024)


class AiAssistantContext(StrictModel):
    screen: str = Field(min_length=1, max_length=80)
    filters: AiAssistantFilters
    selection: AiAssistantSelection | None = None
    time: datetime | None = None
    # Opaque handle for a persisted, server-authorized browser log query. Raw
    # log text is never accepted in assistant context.
    log_stream_id: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def require_offset_time(self) -> AiAssistantContext:
        if self.time is not None and self.time.utcoffset() is None:
            raise ValueError("AI context time must include a UTC offset")
        return self


class AiChatRequest(StrictModel):
    context: AiAssistantContext
    message: str = Field(min_length=1, max_length=MAX_AI_MESSAGE_LENGTH)


class ApplicationUpsertRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    repo_ref: str = Field(default="", max_length=240)
    repository_id: str = ""
    default_branch: str = DEFAULT_REPO_BRANCH
    branch: str | None = Field(default=None, max_length=120)
    manifest_path: str = DEFAULT_MANIFEST_PATH
    cluster_id: str | None = Field(default=None, max_length=160)
    namespace: str = Sandbox.NAMESPACE
    environment: str = DEFAULT_ENVIRONMENT
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_internal_seed_provenance(self) -> ApplicationUpsertRequest:
        if DEMO_SEED_MARKER_KEY in self.metadata:
            raise ValueError(DEMO_SEED_MARKER_RESERVED_MESSAGE)
        return self


class ApplicationConnectRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    repo_ref: str = Field(min_length=1, max_length=240)
    token: str | None = Field(default=None, min_length=1, max_length=500)
    # GitHub App 원클릭 연결 완료 시 전달되는 설치 id. 있으면 PAT/public 대신
    # App 설치 참조를 자격증명으로 저장해 폴러/PR 이 단명 토큰을 발급하게 한다.
    installation_id: str | None = Field(default=None, min_length=1, max_length=40)
    # 리소스 소유권 겹침(다른 앱이 이미 관리 중인 리소스)을 사용자가 확인하고
    # 그래도 진행하겠다고 명시하면 True. 기본은 False(겹치면 409 로 차단).
    allow_conflicts: bool = False
    branch: str = Field(default=DEFAULT_REPO_BRANCH, min_length=1, max_length=200)
    manifest_path: str = Field(default=DEFAULT_MANIFEST_PATH, min_length=1, max_length=500)
    source_type: str = Field(default="", max_length=40)
    cluster_id: str = Field(min_length=1, max_length=120)
    namespace: str = Sandbox.NAMESPACE
    environment: str = DEFAULT_ENVIRONMENT
    metadata: dict[str, Any] = Field(default_factory=dict)
    deploy_policy: dict[str, Any] = Field(default_factory=dict)
    access_policy: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_internal_seed_provenance(self) -> ApplicationConnectRequest:
        if any(
            DEMO_SEED_MARKER_KEY in value
            for value in (self.metadata, self.deploy_policy, self.access_policy)
        ):
            raise ValueError(DEMO_SEED_MARKER_RESERVED_MESSAGE)
        return self


class RepositoryDisconnectRequest(StrictModel):
    repo_ref: str = Field(min_length=1, max_length=240)


class RepositoryProbeRequest(StrictModel):
    repo_ref: str = Field(min_length=1, max_length=240)
    token: SecretStr | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        json_schema_extra={"writeOnly": True},
    )
    # GitHub App 설치 id — 있으면 서버가 설치 토큰을 발급해 비공개 레포도 읽는다.
    installation_id: str | None = Field(default=None, min_length=1, max_length=40)


class RepositoryManifestDiscoveryRequest(StrictModel):
    repo_ref: str = Field(min_length=1, max_length=240)
    branch: str = Field(default=DEFAULT_REPO_BRANCH, min_length=1, max_length=200)
    installation_id: str | None = Field(default=None, min_length=1, max_length=40)


class RepositoryManifestValidationRequest(StrictModel):
    repo_ref: str = Field(min_length=1, max_length=240)
    branch: str = Field(default=DEFAULT_REPO_BRANCH, min_length=1, max_length=200)
    manifest_path: str = Field(default=DEFAULT_MANIFEST_PATH, min_length=1, max_length=500)
    source_type: str = Field(default="", max_length=40)
    values_path: str | None = Field(default=None, min_length=1, max_length=500)
    installation_id: str | None = Field(default=None, min_length=1, max_length=40)

    @model_validator(mode="after")
    def values_override_requires_helm(self) -> RepositoryManifestValidationRequest:
        if self.values_path is not None and self.source_type.strip().lower() not in {"", "helm"}:
            raise ValueError("values_path is valid only for Helm manifest validation")
        return self


class RepositoryConnectionPreviewRequest(StrictModel):
    """연결 직전 desired(git) vs live(cluster) 프리뷰 요청.

    선택한 매니페스트를 대상 클러스터/네임스페이스에 연결하면 무엇이 생성·변경·유지
    되는지 실제 리컨사일과 동일한 diffing 의미로 미리 계산한다.
    """

    repo_ref: str = Field(min_length=1, max_length=240)
    branch: str = Field(default=DEFAULT_REPO_BRANCH, min_length=1, max_length=200)
    manifest_path: str = Field(default=DEFAULT_MANIFEST_PATH, min_length=1, max_length=500)
    source_type: str = Field(default="", max_length=40)
    values_path: str | None = Field(default=None, min_length=1, max_length=500)
    cluster_id: str = Field(min_length=1, max_length=120)
    namespace: str = Sandbox.NAMESPACE
    installation_id: str | None = Field(default=None, min_length=1, max_length=40)

    @model_validator(mode="after")
    def values_override_requires_helm(self) -> RepositoryConnectionPreviewRequest:
        if self.values_path is not None and self.source_type.strip().lower() not in {"", "helm"}:
            raise ValueError("values_path is valid only for Helm manifest validation")
        return self


class DeploymentBindingUpsertRequest(StrictModel):
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    namespace: str = Sandbox.NAMESPACE
    environment: str = DEFAULT_ENVIRONMENT
    manifest_path: str = DEFAULT_MANIFEST_PATH
    resource_class: str = "application"
    deploy_policy: dict[str, Any] = Field(default_factory=dict)
    access_policy: dict[str, Any] = Field(default_factory=dict)


class ReleasePlanStepRequest(StrictModel):
    step_id: str | None = None
    application_id: str = Field(min_length=1, max_length=160)
    name: str | None = Field(default=None, max_length=120)
    position: int = Field(ge=0, le=200)
    depends_on: list[str] = Field(default_factory=list, max_length=50)
    config: dict[str, Any] = Field(default_factory=dict)


class ReleasePlanUpsertRequest(StrictModel):
    plan_id: str | None = Field(default=None, max_length=160)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    status: Literal["draft", "active", "paused", "archived"] = "draft"
    settings: dict[str, Any] = Field(default_factory=dict)
    steps: list[ReleasePlanStepRequest] = Field(default_factory=list, max_length=200)


class ReleaseManifestRenderRequest(StrictModel):
    plan: ReleasePlanUpsertRequest
    step_index: int = Field(default=0, ge=0, le=199)


class ReleaseManifestSafePrRequest(ReleaseManifestRenderRequest):
    title: str | None = Field(default=None, max_length=180)
    body: str | None = Field(default=None, max_length=4000)


class ReleaseRunActionRequest(StrictModel):
    reason: str | None = Field(default=None, max_length=500)


class ReleasePlanArchiveRequest(StrictModel):
    reason: str | None = Field(default=None, max_length=500)


class ReleasePlanRestoreRequest(StrictModel):
    reason: str | None = Field(default=None, max_length=500)


class DiagnosticsRequest(StrictModel):
    mode: Literal["yaml", "settings", "release_plan"] = "yaml"
    content: str = Field(default="", max_length=1_000_000)
    settings: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class CatalogInstallRequest(StrictModel):
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    namespace: str = Sandbox.NAMESPACE
    application_name: str = Field(min_length=1, max_length=120)
    release_name: str | None = Field(default=None, min_length=1, max_length=120)
    version: str | None = Field(default=None, max_length=80)
    values: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecisionRequest(StrictModel):
    reason: str | None = None


class CommandStartRequest(StrictModel):
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    agent_id: str
    lease_id: str
    attempt_id: str | None = Field(default=None, min_length=1, max_length=200)


class NodeDrainProgressResource(StrictModel):
    namespace: str = Field(min_length=1, max_length=63)
    name: str = Field(min_length=1, max_length=253)
    uid: str = Field(min_length=1, max_length=253)
    resource_version: str = Field(min_length=1, max_length=253)
    status: Literal["evicted", "failed", "cancelled"]
    error_code: str | None = Field(default=None, min_length=1, max_length=200)


class NodeDrainProgress(StrictModel):
    progress_id: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    phase: Literal["node_drain_evictions"]
    completed: int = Field(ge=0, le=5_000)
    total: int = Field(ge=0, le=5_000)
    resources: list[NodeDrainProgressResource] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_progress_counts(self) -> NodeDrainProgress:
        if self.completed > self.total:
            raise ValueError("node drain completed count cannot exceed total")
        return self


class CommandHeartbeatRequest(CommandStartRequest):
    attempt_id: str | None = Field(default=None, min_length=1, max_length=200)
    # Agent only acknowledges a generation it actually observed.  The gateway
    # never treats a browser request as an agent cancellation acknowledgement.
    observed_cancel_generation: int | None = Field(default=None, ge=1)
    progress: NodeDrainProgress | None = None


class CommandControlRequest(StrictModel):
    reason: str | None = Field(default=None, max_length=500)


class AlertChannelUpsertRequest(StrictModel):
    """알림 채널 생성/수정 — min_severity 이상의 알림만 이 채널로 발송된다."""

    channel_id: str = ""  # 빈 값이면 서버가 생성(신규)
    name: str = Field(min_length=1)
    kind: Literal["webhook"] = "webhook"
    url: str = Field(min_length=1, max_length=2000)
    min_severity: Literal["info", "warning", "critical"] = "warning"
    enabled: bool = True


class AlertChannelTestRequest(StrictModel):
    channel_id: str = ""
    name: str = Field(default="test", min_length=1, max_length=120)
    kind: Literal["webhook"] = "webhook"
    url: str = Field(min_length=1, max_length=2000)
    min_severity: Literal["info", "warning", "critical"] = "warning"
    severity: Literal["info", "warning", "critical"] = "warning"
    message: str = Field(default="알림 채널 테스트", max_length=500)


class RcaRuleValidateRequest(StrictModel):
    yaml_text: str = Field(min_length=1, max_length=100_000)


class AlertmanagerAlert(StrictModel):
    """Alertmanager webhook payload 의 alert 항목 — 외부 계약이라 필드명 camelCase 유지."""

    model_config = ConfigDict(extra="allow")

    status: str = "firing"
    labels: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    startsAt: str = ""  # noqa: N815 — Alertmanager 계약 필드명
    endsAt: str = ""  # noqa: N815
    fingerprint: str = ""


class AlertmanagerWebhookRequest(StrictModel):
    """Alertmanager v4 webhook — https://prometheus.io/docs/alerting/latest/configuration/#webhook_config"""

    model_config = ConfigDict(extra="allow")

    version: str = "4"
    groupKey: str = ""  # noqa: N815
    status: str = "firing"
    receiver: str = ""
    alerts: list[AlertmanagerAlert] = Field(default_factory=list)


class CommandResultRequest(StrictModel):
    model_config = ConfigDict(extra="allow")

    status: Literal["completed", "failed", "cancelled"] = DEFAULT_COMMAND_STATUS
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    agent_id: str
    lease_id: str
    attempt_id: str | None = Field(default=None, min_length=1, max_length=200)
    applied: bool = False
    message: str = EMPTY_COMMAND_MESSAGE
    retryable: bool = False
    resources: list[dict[str, Any]] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    # cluster.agent.uninstall 전용 완료 증적. 단순 ACK/예약과 구분해 서버가
    # 실제 allowlist 정리 완료에만 등록 토큰을 폐기한다.
    cleanup_completed: bool = False
    cleanup_resources: list[str] = Field(default_factory=list)
    residual_resources: list[str] = Field(default_factory=list)
    resource_file: ResourceFileResult | None = None


class EvidenceJobScheduleRequest(StrictModel):
    source_id: str = "cluster-snapshot"
    window_start: str
    provider_keys: list[str] = Field(min_length=1)
    release_context: dict[str, Any] = Field(default_factory=dict)
    provider_policies: dict[str, dict[str, Any]] = Field(default_factory=dict)


class EvidenceJobResultRequest(StrictModel):
    agent_id: str
    lease_id: str
    status: Literal["completed", "failed"]
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""

    @model_validator(mode="after")
    def _bound_result_size(self) -> EvidenceJobResultRequest:
        size = len(json.dumps({"result": self.result}, default=str).encode())
        if size > MAX_EVIDENCE_PAYLOAD_BYTES:
            raise ValueError(EVIDENCE_PAYLOAD_TOO_LARGE_MESSAGE)
        return self


class EvidenceProviderPolicy(StrictModel):
    enabled: bool = True
    interval_seconds: int = Field(default=DEFAULT_PROVIDER_INTERVAL_SECONDS, ge=1)
    min_workers: int = Field(default=DEFAULT_PROVIDER_MIN_WORKERS, ge=0)
    max_workers: int = Field(default=DEFAULT_PROVIDER_MAX_WORKERS, ge=0)
    queue_age_target_seconds: int = Field(default=DEFAULT_QUEUE_AGE_TARGET_SECONDS, ge=1)
    queries: list[dict[str, Any]] = Field(default_factory=list)
    # Opaque management-plane revision only. Provider secrets never enter the
    # durable policy or the agent's on-disk policy cache.
    configuration_revision: str | None = Field(default=None, min_length=1, max_length=120)
    configuration_operation_id: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def require_complete_configuration_identity(self) -> EvidenceProviderPolicy:
        if (self.configuration_revision is None) != (self.configuration_operation_id is None):
            raise ValueError(
                "provider configuration revision and operation identity must be paired"
            )
        return self


class EvidenceRuntimePolicy(StrictModel):
    profile: EvidenceProfile = "standard"
    failure_policy: Literal["allow_partial", "strict"] = "allow_partial"
    max_attempts: int = Field(
        default=DEFAULT_EVIDENCE_JOB_MAX_ATTEMPTS,
        ge=1,
        le=MAX_EVIDENCE_JOB_MAX_ATTEMPTS,
    )
    providers: dict[str, EvidenceProviderPolicy] = Field(default_factory=dict)


class DesiredResource(StrictModel):
    resource_id: str
    scope: Literal["target-agent", "system", "user-workload"] = "target-agent"
    kind: Literal["ConfigMap", "Deployment"]
    namespace: str
    name: str
    action: Literal["observe", "apply"] = "observe"
    state: dict[str, Any] = Field(default_factory=dict)


class BootstrapPolicy(StrictModel):
    mode: Literal["management", "target"] = "target"
    resources: list[DesiredResource] = Field(default_factory=list)


class DesiredStatePolicy(StrictModel):
    resources: list[DesiredResource] = Field(default_factory=list)


class SchedulingSelector(StrictModel):
    namespaces: list[str] = Field(default_factory=list, max_length=50)
    labels: dict[str, str] = Field(default_factory=dict)
    workload_names: list[str] = Field(default_factory=list, max_length=100)


class SchedulingToleration(StrictModel):
    key: str = Field(min_length=1, max_length=120)
    operator: Literal["Exists", "Equal"] = "Equal"
    value: str = Field(default="", max_length=120)
    effect: Literal["NoSchedule", "PreferNoSchedule", "NoExecute"] = "NoSchedule"


class SchedulingProfile(StrictModel):
    profile_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    enabled: bool = True
    description: str = Field(default="", max_length=500)
    selector: SchedulingSelector = Field(default_factory=SchedulingSelector)
    priority_class_name: str = Field(default=FAST_LANE_PRIORITY_CLASS_NAME, max_length=120)
    priority_value: int = Field(default=100_000, ge=0, le=1_000_000_000)
    preemption_policy: Literal["PreemptLowerPriority", "Never"] = "PreemptLowerPriority"
    placement_mode: Literal["preferred", "required"] = "preferred"
    node_selector: dict[str, str] = Field(default_factory=dict)
    preferred_node_labels: dict[str, str] = Field(default_factory=dict)
    tolerations: list[SchedulingToleration] = Field(default_factory=list, max_length=20)
    pre_pull_images: list[str] = Field(default_factory=list, max_length=50)
    termination_grace_period_seconds: int | None = Field(default=None, ge=0, le=300)
    scheduler_name: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def require_explicit_selector(self) -> SchedulingProfile:
        if not self.enabled:
            return self
        if self.selector.namespaces or self.selector.labels or self.selector.workload_names:
            return self
        raise ValueError("enabled scheduling profile requires at least one selector")


class SchedulingPolicy(StrictModel):
    profiles: list[SchedulingProfile] = Field(default_factory=list, max_length=50)


class AgentPolicy(StrictModel):
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    generation: int = Field(default=DEFAULT_AGENT_POLICY_GENERATION, ge=1)
    cluster_role: Literal["management", "target"] = "target"
    evidence: EvidenceRuntimePolicy = Field(default_factory=EvidenceRuntimePolicy)
    bootstrap: BootstrapPolicy = Field(default_factory=BootstrapPolicy)
    desired_state: DesiredStatePolicy = Field(default_factory=DesiredStatePolicy)
    scheduling: SchedulingPolicy = Field(default_factory=SchedulingPolicy)


class AgentPolicyResponse(StrictModel):
    policy: AgentPolicy | None = None


class AgentPolicyStatusRequest(StrictModel):
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    generation: int = Field(default=DEFAULT_AGENT_POLICY_GENERATION, ge=1)
    status: Literal["applied", "failed", "unchanged"] = "applied"
    message: str = EMPTY_COMMAND_MESSAGE
    details: dict[str, Any] = Field(default_factory=dict)


class ProviderSelectionRequest(StrictModel):
    source_provider: str | None = None
    deploy_provider: str | None = None
    cloud_provider: str | None = None
    secret_provider: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    credential_refs: dict[str, str] = Field(default_factory=dict)


class AgentReconcileStatusRequest(StrictModel):
    cluster_id: str = Target.DEFAULT_CLUSTER_ID
    generation: int = Field(default=DEFAULT_AGENT_POLICY_GENERATION, ge=1)
    status: Literal["applied", "failed", "unchanged"] = "unchanged"
    message: str = EMPTY_COMMAND_MESSAGE
    details: dict[str, Any] = Field(default_factory=dict)
