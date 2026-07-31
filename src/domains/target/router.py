"""target 도메인 HTTP 라우터 — target 등록 시 Kubernetes 설치 manifest 생성/적용."""

from __future__ import annotations

import asyncio
import re
import secrets
import shlex
import shutil
import subprocess
import time
from contextlib import suppress
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError

from domains.identity.dependencies import (
    ClusterAgentIdentity,
    hash_agent_token,
    require_admin_session,
    require_cluster_access,
    require_cluster_agent,
    require_session,
)
from domains.integrations.prometheus import update_prometheus_integration
from domains.inventory.ingest import ingest_inventory_snapshot
from domains.inventory.kubernetes_snapshot import kubernetes_evidence_to_inventory_snapshot
from domains.inventory.snapshot_evidence import snapshot_source_summary
from domains.providers.catalog import ProviderCategory, require_available_provider
from domains.rca.events import ClusterEvidenceReceivedBody, compact_cluster_evidence_payload
from domains.target.cluster_visibility import is_blocked_test_cluster
from domains.target.connectivity import (
    AGENT_STATUS_NEVER_CONNECTED,
    AGENT_STATUS_ONLINE,
    cluster_connection_status,
    parse_timestamp,
)
from domains.target.events import (
    ClusterDesiredStateChangedBody,
    EvidenceJobUpdatedBody,
)
from domains.target.evidence_jobs import (
    DEFAULT_EVIDENCE_JOB_LEASE_SECONDS,
    DEFAULT_EVIDENCE_SOURCE_ID,
    DEFAULT_PENDING_EVIDENCE_EVENT_TTL_SECONDS,
    PENDING_EVIDENCE_EVENT_ID_PREFIX,
)
from domains.target.evidence_policy import (
    control_namespace_tuple,
    default_agent_policy,
    enabled_provider_keys,
    evidence_profile_for_registration,
    preserve_server_owned_evidence_queries,
    provider_policy_snapshots,
)
from domains.target.install_manifest import (
    agent_namespace,
    target_install_manifest,
    target_rbac_manifest,
)
from domains.target.management_guard import (
    MANAGEMENT_CLUSTER_ROLE,
    freeze_management_policy,
    is_management_registration,
    is_management_role,
    management_policy_update_is_forbidden,
    management_readonly_detail,
    refresh_management_policy,
)
from domains.target.policy_upgrade import target_desired_components
from domains.target.reconciler import desired_state_version
from domains.target.telemetry_readiness import telemetry_stack_view
from domains.target.uninstall import (
    FINAL_CLEANUP_RESOURCE_REFS,
    UNINSTALL_CLEANUP_RESOURCE_REFS,
    UNINSTALL_COMMAND_REFERENCE,
    queue_agent_uninstall,
)
from packages.config.security import (
    TEST_FIXTURE_ENVIRONMENT,
    test_fixture_purge_enabled,
)
from packages.config.settings import env
from packages.contracts.gateway import limits as gateway_limits
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.policy_merge import merge_agent_policy
from packages.contracts.gateway.requests import (
    AgentPolicy,
    AgentPolicyResponse,
    AgentPolicyStatusRequest,
    AgentReconcileStatusRequest,
    ClusterConnectRequest,
    EvidenceJobResultRequest,
    EvidenceJobScheduleRequest,
    SchedulingPolicy,
    TargetPreflightRequest,
    TargetRegisterRequest,
    normalize_control_namespaces,
)
from packages.contracts.gateway.responses import (
    BootstrapStep,
    ClusterConnectionStatusResponse,
    ClusterConnectResponse,
    ClusterConnectStatusResponse,
    ClusterListResponse,
    ClusterResponse,
    ClusterSummary,
    ClusterUnregisterResponse,
    EvidenceJobPollResponse,
    EvidenceJobResultResponse,
    EvidenceJobScheduleResponse,
    ManagementAccessResponse,
    SchedulingPolicyResponse,
    TargetInstallResponse,
    TargetPreflightResponse,
    TelemetryStackView,
)
from packages.contracts.identity import (
    DEFAULT_WORKSPACE_ID,
    AccessResourceType,
    ClusterRegistrationStatus,
    Permission,
)
from packages.contracts.integrations import PrometheusIntegrationUpdateRequest
from packages.contracts.target import (
    SANDBOX_NAMESPACE,
    TARGET_NAMESPACE,
    TARGET_OTEL_TRACES_ENDPOINT,
    TARGET_RBAC_MANIFEST_VERSION,
)
from packages.events.envelope import event
from packages.runtime.dependencies import (
    get_dashboard_ready_fanout,
    get_db,
    get_events,
    get_operation_events,
    get_timeline_fanout,
)
from packages.security.credentials import (
    CredentialEncryptionError,
    agent_envelope_public_key,
    decrypt_credential,
    encrypt_credential,
    generate_agent_envelope_keypair,
)
from packages.storage.engine import unit_of_work_or_null
from packages.storage.retry import to_thread_db_retry

AGENT_TOKEN_BYTES = 32  # per-cluster agent 토큰 엔트로피(secrets.token_urlsafe)
KUBECTL_NOT_AVAILABLE = "kubectl is not available to api-gateway"
KUBECTL_APPLY_FAILED = "target install apply failed"
KUBECTL_APPLY_TIMEOUT = "target install apply timed out"
KUBECTL_APPLY_TIMEOUT_SECONDS_ENV = "KUBECTL_APPLY_TIMEOUT_SECONDS"
DEFAULT_KUBECTL_APPLY_TIMEOUT_SECONDS = "30"
# 콤마구분 허용 컨텍스트 목록. 설정 시 목록 밖 --context 거부(임의 클러스터 적용 차단).
# 미설정 시 컨텍스트 미지정(현재 kubeconfig)만 허용 — 페이로드로 임의 컨텍스트 지정 불가.
KUBE_CONTEXT_ALLOWLIST_ENV = "KUBE_CONTEXT_ALLOWLIST"
KUBE_CONTEXT_NOT_ALLOWED = "kube context is not in the allowlist"
KUBE_CONTEXT_CONNECTION_FAILED = "kubernetes preflight connection failed"
KUBE_CONTEXT_CONNECTION_TIMEOUT = "kubernetes preflight connection timed out"
DIRECT_APPLY_DEPLOY_PROVIDER = "kube-context"
MANUAL_MANIFEST_DEPLOY_PROVIDER = "manual-manifest"
CONNECT_PROVIDER_HINTS = {
    "aws": "eks",
    "gcp": "gke",
    "azure": "aks",
    "onprem": "onprem",
}
CLUSTER_ACTIVE_NAME_INDEX = "ux_cluster_registrations_workspace_active_name"
CLUSTER_NAME_CONFLICT_CODE = "cluster_name_conflict"
TARGET_PROVIDER_INVALID = "target install provider selection is invalid"
# evidence job 롱폴 튜닝값 — env 미설정 시 기존 기본값과 동일한 기본값이 적용됨(배포 호환)
DEFAULT_EVIDENCE_JOB_POLL_SECONDS_ENV = (
    "EVIDENCE_JOB_POLL_DEFAULT_SECONDS"  # 롱폴 기본 대기 초(기본 10)
)
DEFAULT_EVIDENCE_JOB_POLL_SECONDS = int(env(DEFAULT_EVIDENCE_JOB_POLL_SECONDS_ENV, "10"))
MAX_EVIDENCE_JOB_POLL_SECONDS_ENV = "EVIDENCE_JOB_POLL_MAX_SECONDS"  # 롱폴 최대 대기 초(기본 30)
MAX_EVIDENCE_JOB_POLL_SECONDS = int(env(MAX_EVIDENCE_JOB_POLL_SECONDS_ENV, "30"))
EVIDENCE_JOB_POLL_SLEEP_SECONDS_ENV = (
    "EVIDENCE_JOB_POLL_SLEEP_SECONDS"  # 롱폴 반복 간 대기 초(기본 1)
)
EVIDENCE_JOB_POLL_SLEEP_SECONDS = int(env(EVIDENCE_JOB_POLL_SLEEP_SECONDS_ENV, "1"))
NOT_FOUND_CODE = 404
EVIDENCE_JOB_NOT_FOUND = "evidence job not found"
RELEASE_WORKFLOW_FAILURE_SOURCE_ID = "release-workflow-failure"
TARGET_AGENT_IMAGE_ENV = "TARGET_AGENT_IMAGE"
# 비공개 레지스트리 에이전트 이미지를 아무 클러스터에서나 pull 하기 위한 옵트인
# dockerconfigjson 자격증명. 미설정이면 매니페스트에 pull secret 을 넣지 않는다(회귀 0).
TARGET_AGENT_IMAGE_PULL_SECRET_ENV = "TARGET_AGENT_IMAGE_PULL_SECRET"
TARGET_DEFAULT_CONTROL_NAMESPACES_ENV = "TARGET_DEFAULT_CONTROL_NAMESPACES"
GITOPS_WEBHOOK_IMAGE_ENV = "GITOPS_WEBHOOK_IMAGE"
PUBLIC_MANAGEMENT_BASE_URL_ENV = "PUBLIC_MANAGEMENT_BASE_URL"
PUBLIC_API_BASE_URL_ENV = "PUBLIC_API_BASE_URL"
PUBLIC_BASE_URL_ENV = "PUBLIC_BASE_URL"
OPSIA_ACCESS_MODE_ENV = "OPSIA_ACCESS_MODE"
OPSIA_EXTERNAL_URL_ENV = "OPSIA_EXTERNAL_URL"
SUPPORTED_ACCESS_MODES = frozenset({"portforward", "loadbalancer", "ingress", "nodeport"})
LOCAL_PLACEHOLDER_IMAGES = {"", "service:local", "kubeheal-service:latest"}
# blocked test-cluster 규칙은 domains.target.cluster_visibility.is_blocked_test_cluster로 공유한다
# (list_clusters·등록 거부·checks scope 투영이 단일 predicate 사용).
CLUSTER_ID_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
AGENT_STATUS_NOT_REGISTERED = "not_registered"
AGENT_STATUS_PENDING_INSTALL = ClusterRegistrationStatus.PENDING_INSTALL.value
AGENT_STATUS_INSTALL_FAILED = ClusterRegistrationStatus.INSTALL_FAILED.value
AGENT_STATUS_INSTALL_EXPIRED = ClusterRegistrationStatus.INSTALL_EXPIRED.value
CONCRETE_CLUSTER_PROVIDERS = frozenset({"eks", "gke", "aks", "kind"})
GENERIC_ONPREM_PROVIDERS = frozenset({"existing-k8s", "minikube", "onprem"})
DETECTED_CLUSTER_PROVIDERS = frozenset({"eks", "gke", "aks"})
AGENT_ERROR_STATUSES = frozenset({"error", "failed"})
TARGET_AGENT_IMAGE_NOT_CONFIGURED = "target agent image is not configured"
MANAGEMENT_BASE_URL_NOT_CONFIGURED = "management base URL is not configured"
AGENT_BOOTSTRAP_HTTPS_REQUIRED = "remote agent bootstrap requires an HTTPS management URL"
EXTERNAL_ACCESS_REQUIRED = "external access URL is required to enroll another cluster"
TARGET_REGISTRATION_CONNECT_TIMEOUT_SECONDS_ENV = "TARGET_REGISTRATION_CONNECT_TIMEOUT_SECONDS"
TARGET_REGISTRATION_AUTO_DELETE_EXPIRED_ENV = "TARGET_REGISTRATION_AUTO_DELETE_EXPIRED"
DEFAULT_TARGET_REGISTRATION_CONNECT_TIMEOUT_SECONDS = 1800
CLUSTER_NOT_FOUND = "cluster not found"
TEST_FIXTURE_PURGE_FORBIDDEN_CODE = "test_fixture_purge_forbidden"
TEST_FIXTURE_PURGE_UNSUPPORTED_CODE = "test_fixture_purge_unsupported"
DEFAULT_PROMETHEUS_URL = "http://prometheus.target.svc.cluster.local:9090"
DEFAULT_OTEL_TRACES_URL = TARGET_OTEL_TRACES_ENDPOINT
INSTALL_ARTIFACT_ROOT = Path(__file__).resolve().parents[3]
INSTALL_TELEMETRY_SCRIPTS = {
    "bash": INSTALL_ARTIFACT_ROOT / "scripts" / "install-telemetry.sh",
    "powershell": INSTALL_ARTIFACT_ROOT / "scripts" / "install-telemetry.ps1",
}
INSTALL_TELEMETRY_ASSETS = frozenset(
    {"prometheus.yaml", "loki.yaml", "tempo.yaml", "opentelemetry.yaml", "minio.yaml"}
)

router = APIRouter()
# per-cluster 토큰 인증 — lease 의 workspace/cluster 는 토큰 identity 에서만 취함.
agent_router = APIRouter()


def allowed_kube_contexts() -> set[str]:
    raw = env(KUBE_CONTEXT_ALLOWLIST_ENV, "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def normalized_management_base_url(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        _port = parts.port
    except ValueError:
        return ""
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.path not in {"", "/", "/api", "/api/"}
    ):
        return ""
    return f"{parts.scheme}://{parts.netloc}/api"


def public_management_base_url() -> str:
    for key in (
        PUBLIC_MANAGEMENT_BASE_URL_ENV,
        PUBLIC_API_BASE_URL_ENV,
        PUBLIC_BASE_URL_ENV,
    ):
        base = normalized_management_base_url(env(key, ""))
        if base:
            return base
    return ""


def agent_image_pull_secret() -> str:
    """옵트인 dockerconfigjson 자격증명(server env). 없으면 빈 문자열(=매니페스트 불변)."""
    return env(TARGET_AGENT_IMAGE_PULL_SECRET_ENV, "").strip()


def management_access_response() -> ManagementAccessResponse:
    mode = env(OPSIA_ACCESS_MODE_ENV, "").strip().lower()
    if mode not in SUPPORTED_ACCESS_MODES:
        mode = "unknown"

    configured_external_url = normalized_management_base_url(
        env(OPSIA_EXTERNAL_URL_ENV, "")
    ).removesuffix("/api")
    agent_server_url = public_management_base_url().removesuffix("/api")
    if configured_external_url:
        return ManagementAccessResponse(
            mode=mode,
            external_url=configured_external_url,
            agent_server_url=agent_server_url or configured_external_url,
            reachability="external",
        )
    if mode == "unknown" and agent_server_url:
        return ManagementAccessResponse(
            mode=mode,
            external_url=agent_server_url,
            agent_server_url=agent_server_url,
            reachability="external",
        )
    return ManagementAccessResponse(
        mode=mode,
        agent_server_url=agent_server_url,
        reachability="self_only",
        limitation_reason="external_url_not_configured",
    )


def target_registration_connect_timeout_seconds() -> int:
    raw = env(
        TARGET_REGISTRATION_CONNECT_TIMEOUT_SECONDS_ENV,
        str(DEFAULT_TARGET_REGISTRATION_CONNECT_TIMEOUT_SECONDS),
    )
    try:
        return max(60, int(raw))
    except ValueError:
        return DEFAULT_TARGET_REGISTRATION_CONNECT_TIMEOUT_SECONDS


def connect_expires_at_from(created_at: datetime, timeout_seconds: int) -> str:
    return (created_at + timedelta(seconds=timeout_seconds)).isoformat()


def shell_quote(value: object) -> str:
    return shlex.quote(str(value))


def provider_config_text(payload: TargetRegisterRequest, key: str, default: str = "") -> str:
    value = payload.provider_config.get(key, default)
    return str(value).strip() if value is not None else ""


def require_provider_config(payload: TargetRegisterRequest, *keys: str) -> dict[str, str]:
    values = {key: provider_config_text(payload, key) for key in keys}
    missing = [key for key, value in values.items() if not value]
    if missing:
        joined = ", ".join(missing)
        raise HTTPException(
            status_code=422,
            detail=f"provider_config is missing required fields: {joined}",
        )
    return values


def normalize_target_provider_defaults(payload: TargetRegisterRequest) -> TargetRegisterRequest:
    updates: dict[str, Any] = {}
    if payload.apply and "deploy_provider" not in payload.model_fields_set:
        updates["deploy_provider"] = DIRECT_APPLY_DEPLOY_PROVIDER

    image = payload.image.strip()
    if image in LOCAL_PLACEHOLDER_IMAGES:
        default_image = env(TARGET_AGENT_IMAGE_ENV, "") or env(GITOPS_WEBHOOK_IMAGE_ENV, "")
        if default_image:
            updates["image"] = default_image

    # 배포 경계에서 확정된 주소가 요청 body보다 우선한다. port-forward 브라우저가 보낸
    # localhost를 외부 agent callback으로 저장하지 않도록 서버 권위값을 사용한다.
    management_base_url = public_management_base_url()
    if not management_base_url:
        management_base_url = normalized_management_base_url(payload.management_base_url)
    if management_base_url:
        updates["management_base_url"] = management_base_url

    if payload.cluster_role != MANAGEMENT_CLUSTER_ROLE and not payload.control_namespaces.strip():
        default_control_namespaces = env(TARGET_DEFAULT_CONTROL_NAMESPACES_ENV, "").strip()
        if default_control_namespaces:
            updates["control_namespaces"] = normalize_control_namespaces(default_control_namespaces)

    if payload.cluster_role == MANAGEMENT_CLUSTER_ROLE:
        updates["install_node_collector"] = False
        updates["install_sample_workload"] = False
        updates["control_namespaces"] = payload.control_namespaces or SANDBOX_NAMESPACE
        for telemetry_field in (
            "loki_base_url",
            "tempo_base_url",
            "otel_traces_endpoint",
        ):
            if telemetry_field not in payload.model_fields_set:
                updates[telemetry_field] = ""

    return payload.model_copy(update=updates) if updates else payload


def slugify_cluster_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "cluster"


def generated_cluster_id(name: str) -> str:
    suffix = f"{secrets.randbelow(10_000):04d}"
    base = slugify_cluster_name(name)[:58].strip("-") or "cluster"
    return f"{base}-{suffix}"


def normalized_cluster_display_name(name: object) -> str:
    return str(name).strip().casefold()


def cluster_name_conflict_detail() -> dict[str, str]:
    return {
        "code": CLUSTER_NAME_CONFLICT_CODE,
        "detail": "같은 워크스페이스에 동일한 이름의 활성 클러스터가 이미 있습니다",
    }


def require_unique_cluster_display_name(db: Any, workspace_id: str, name: str) -> None:
    lister = getattr(db, "list_cluster_registrations", None)
    if not callable(lister):
        return
    normalized = normalized_cluster_display_name(name)
    for registration in lister(workspace_id, limit=500):
        if str(registration.get("status") or "") in {
            ClusterRegistrationStatus.INSTALL_EXPIRED.value,
            ClusterRegistrationStatus.DISCONNECTED.value,
        }:
            continue
        if normalized_cluster_display_name(registration.get("name")) == normalized:
            raise HTTPException(status_code=409, detail=cluster_name_conflict_detail())


def is_cluster_name_integrity_conflict(error: IntegrityError) -> bool:
    original = getattr(error, "orig", None)
    diagnostic = getattr(original, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    return constraint_name == CLUSTER_ACTIVE_NAME_INDEX or CLUSTER_ACTIVE_NAME_INDEX in str(
        original
    )


def resolve_target_cluster_id(payload: TargetRegisterRequest, workspace_id: str, db: Any) -> str:
    explicit = (payload.cluster_id or "").strip()
    if explicit:
        if not CLUSTER_ID_PATTERN.match(explicit):
            raise HTTPException(
                status_code=422,
                detail="cluster_id must use lowercase letters, numbers, and hyphens",
            )
        return explicit
    getter = getattr(db, "get_cluster_registration", None)
    for _ in range(20):
        candidate = generated_cluster_id(payload.name)
        if not callable(getter) or getter(workspace_id, candidate) is None:
            return candidate
    raise HTTPException(status_code=409, detail="cluster_id generation collided")


def reject_test_target(payload: TargetRegisterRequest) -> None:
    if is_blocked_test_cluster(payload.cluster_id or "", payload.name):
        raise HTTPException(status_code=422, detail="test target registrations are not allowed")
    if payload.image.strip() in LOCAL_PLACEHOLDER_IMAGES:
        raise HTTPException(status_code=422, detail="target agent image is not configured")


def require_management_base_url(payload: TargetRegisterRequest) -> None:
    normalized = normalized_management_base_url(payload.management_base_url)
    if not normalized:
        raise HTTPException(status_code=422, detail=MANAGEMENT_BASE_URL_NOT_CONFIGURED)


def require_secure_agent_bootstrap(payload: TargetRegisterRequest) -> None:
    normalized = normalized_management_base_url(payload.management_base_url)
    parsed = urlsplit(normalized)
    hostname = (parsed.hostname or "").casefold()
    local_development_host = hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(
        ".local"
    )
    if parsed.scheme != "https" and not local_development_host:
        raise HTTPException(status_code=422, detail=AGENT_BOOTSTRAP_HTTPS_REQUIRED)


def validate_target_install_providers(payload: TargetRegisterRequest) -> None:
    try:
        require_available_provider(ProviderCategory.CLOUD, payload.cloud_provider)
        require_available_provider(ProviderCategory.DEPLOY, payload.deploy_provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{TARGET_PROVIDER_INVALID}: {exc}") from exc

    if payload.apply and payload.deploy_provider != DIRECT_APPLY_DEPLOY_PROVIDER:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{TARGET_PROVIDER_INVALID}: direct apply requires "
                f"deploy_provider={DIRECT_APPLY_DEPLOY_PROVIDER}"
            ),
        )
    if payload.kube_context and payload.deploy_provider != DIRECT_APPLY_DEPLOY_PROVIDER:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{TARGET_PROVIDER_INVALID}: kube_context requires "
                f"deploy_provider={DIRECT_APPLY_DEPLOY_PROVIDER}"
            ),
        )


def validate_target_bootstrap_config(payload: TargetRegisterRequest) -> None:
    if payload.cloud_provider == "eks":
        require_provider_config(payload, "region", "eks_cluster_name")
    if payload.cloud_provider == "gke":
        values = require_provider_config(
            payload, "project_id", "location_type", "location", "gke_cluster_name"
        )
        if values["location_type"] not in {"region", "zone"}:
            raise HTTPException(
                status_code=422,
                detail="provider_config.location_type must be region or zone",
            )
    if payload.cloud_provider == "aks":
        require_provider_config(payload, "resource_group", "aks_cluster_name")


def target_agent_image_ready(image: str = "") -> bool:
    candidate = image.strip()
    if candidate and candidate not in LOCAL_PLACEHOLDER_IMAGES:
        return True
    return bool(env(TARGET_AGENT_IMAGE_ENV, "") or env(GITOPS_WEBHOOK_IMAGE_ENV, ""))


def target_preflight_provider_checks(
    payload: TargetPreflightRequest,
) -> tuple[bool, list[str], list[str], dict[str, Any], bool | None]:
    errors: list[str] = []
    warnings: list[str] = []
    selected: dict[str, Any] = {}
    provider_errors = 0

    for category, key in (
        (ProviderCategory.CLOUD, payload.cloud_provider),
        (ProviderCategory.DEPLOY, payload.deploy_provider),
    ):
        try:
            selected[category.value] = require_available_provider(category, key).to_body()
        except ValueError as exc:
            errors.append(f"{TARGET_PROVIDER_INVALID}: {exc}")
            provider_errors += 1

    if payload.apply and payload.deploy_provider != DIRECT_APPLY_DEPLOY_PROVIDER:
        errors.append(
            f"{TARGET_PROVIDER_INVALID}: direct apply requires "
            f"deploy_provider={DIRECT_APPLY_DEPLOY_PROVIDER}"
        )
        provider_errors += 1
    if payload.kube_context and payload.deploy_provider != DIRECT_APPLY_DEPLOY_PROVIDER:
        errors.append(
            f"{TARGET_PROVIDER_INVALID}: kube_context requires "
            f"deploy_provider={DIRECT_APPLY_DEPLOY_PROVIDER}"
        )
        provider_errors += 1

    kube_context_allowed: bool | None = None
    allowlist = allowed_kube_contexts()
    if payload.kube_context:
        kube_context_allowed = payload.kube_context in allowlist
        if not kube_context_allowed:
            errors.append(KUBE_CONTEXT_NOT_ALLOWED)
            provider_errors += 1
    elif payload.apply and payload.deploy_provider == DIRECT_APPLY_DEPLOY_PROVIDER and allowlist:
        kube_context_allowed = False
        errors.append(KUBE_CONTEXT_NOT_ALLOWED)
        provider_errors += 1

    if payload.deploy_provider == DIRECT_APPLY_DEPLOY_PROVIDER and not allowlist:
        warnings.append(
            "KUBE_CONTEXT_ALLOWLIST is empty; direct apply can only use the api-gateway "
            "process current kube context"
        )

    if not target_agent_image_ready(payload.image):
        errors.append(TARGET_AGENT_IMAGE_NOT_CONFIGURED)
        provider_errors += 1
    if (
        not normalized_management_base_url(payload.management_base_url)
        and not public_management_base_url()
    ):
        errors.append(MANAGEMENT_BASE_URL_NOT_CONFIGURED)
        provider_errors += 1
    access = management_access_response()
    if (
        payload.cluster_role != MANAGEMENT_CLUSTER_ROLE
        and access.mode != "unknown"
        and access.reachability == "self_only"
    ):
        errors.append(EXTERNAL_ACCESS_REQUIRED)
        provider_errors += 1

    if (
        payload.deploy_provider == DIRECT_APPLY_DEPLOY_PROVIDER
        and payload.apply
        and kube_context_allowed is not False
        and provider_errors == 0
    ):
        connection_error = kube_context_connectivity_error(payload.kube_context)
        if connection_error:
            errors.append(connection_error)
            provider_errors += 1

    return provider_errors == 0, errors, warnings, selected, kube_context_allowed


def kube_context_connectivity_error(kube_context: str | None) -> str | None:
    if not shutil.which("kubectl"):
        return KUBECTL_NOT_AVAILABLE
    command = ["kubectl"]
    if kube_context:
        command.extend(["--context", kube_context])
    command.extend(["get", "--raw=/version", "--request-timeout=5s"])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=6,
        )
    except subprocess.TimeoutExpired:
        return KUBE_CONTEXT_CONNECTION_TIMEOUT
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        suffix = f": {detail[0][:160]}" if detail else ""
        return f"{KUBE_CONTEXT_CONNECTION_FAILED}{suffix}"
    return None


def apply_manifest_with_kubectl(manifest: str, kube_context: str | None) -> str:
    # 입력(컨텍스트) 검증을 먼저 — 허용목록이 비어있으면(미설정) 어떤 명시 컨텍스트도
    # 거부(fail-closed), 설정돼 있으면 목록에 든 컨텍스트만 허용. 임의 클러스터 적용 차단.
    allowlist = allowed_kube_contexts()
    if allowlist and not kube_context:
        raise HTTPException(status_code=403, detail=KUBE_CONTEXT_NOT_ALLOWED)
    if kube_context and kube_context not in allowlist:
        raise HTTPException(status_code=403, detail=KUBE_CONTEXT_NOT_ALLOWED)
    if not shutil.which("kubectl"):
        raise HTTPException(status_code=503, detail=KUBECTL_NOT_AVAILABLE)
    command = ["kubectl"]
    if kube_context:
        command.extend(["--context", kube_context])
    command.extend(["apply", "-f", "-"])
    try:
        result = subprocess.run(
            command,
            input=manifest,
            capture_output=True,
            text=True,
            check=False,
            timeout=float(
                env(KUBECTL_APPLY_TIMEOUT_SECONDS_ENV, DEFAULT_KUBECTL_APPLY_TIMEOUT_SECONDS)
            ),
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail=KUBECTL_APPLY_TIMEOUT) from exc
    if result.returncode != 0:
        raise HTTPException(status_code=502, detail=KUBECTL_APPLY_FAILED)
    return result.stdout


def install_command_for(payload: TargetRegisterRequest, agent_token: str) -> str:
    """원라인 설치 명령 — 관측 스택 준비 후 토큰 manifest 를 적용.

    base 는 등록 payload 의 management_base_url(agent 가 접속하는 공개 게이트웨이 주소)
    그대로 사용 — 서버가 임의 호스트를 합성하지 않음.
    """
    base = normalized_management_base_url(payload.management_base_url)
    if not base:
        return ""
    path = gateway_routes.INSTALL_MANIFEST_PATH.format(agent_token=agent_token)
    telemetry_enabled = payload.cluster_role != MANAGEMENT_CLUSTER_ROLE
    return guarded_kubectl_apply_command(
        payload,
        f"{base}{path}",
        telemetry_script_url=(
            telemetry_script_url(base, agent_token, "bash") if telemetry_enabled else ""
        ),
        telemetry_asset_base_url=(
            telemetry_asset_base_url(base, agent_token) if telemetry_enabled else ""
        ),
        telemetry_agent_token=agent_token if telemetry_enabled else "",
        management_api_base_url=base if telemetry_enabled else "",
    )


def telemetry_script_url(base: str, agent_token: str, platform: str) -> str:
    path = gateway_routes.INSTALL_TELEMETRY_SCRIPT_PATH.format(
        agent_token=agent_token,
        platform=platform,
    )
    return f"{base}{path}"


def telemetry_asset_base_url(base: str, agent_token: str) -> str:
    marker = "{asset_name}"
    path = gateway_routes.INSTALL_TELEMETRY_ASSET_PATH.format(
        agent_token=agent_token,
        asset_name=marker,
    )
    return f"{base}{path.removesuffix(marker).rstrip('/')}"


def guarded_kubectl_apply_command(
    payload: TargetRegisterRequest,
    manifest_url: str,
    context: str = "",
    *,
    telemetry_script_url: str = "",
    telemetry_asset_base_url: str = "",
    telemetry_agent_token: str = "",
    management_api_base_url: str = "",
) -> str:
    """관측 스택을 먼저 준비하고 기존 에이전트 소유권을 안전하게 교체한다."""
    kubectl = "kubectl"
    if context:
        kubectl = f"kubectl --context {shell_quote(context)}"
    namespace = agent_namespace(payload)
    expected_cluster_id = payload.cluster_id or ""
    guard = (
        f'existing="$({kubectl} -n {shell_quote(namespace)} get configmap '
        "target-runtime-config --ignore-not-found -o jsonpath='{.data.TARGET_CLUSTER_ID}' "
        '2>/dev/null || true)"; '
        f'if [ -n "$existing" ] && [ "$existing" != {shell_quote(expected_cluster_id)} ]; '
        "then printf 'Kyro agent is already registered as %s; disconnect it before connecting "
        f'{expected_cluster_id}.\\n\' "$existing" >&2; exit 1; fi; '
    )
    target_context = (
        f"target_context={shell_quote(context)}; "
        if context
        else 'target_context="$(kubectl config current-context)"; '
    )
    install_telemetry = (
        (
            'telemetry_script="$(mktemp "${TMPDIR:-/tmp}/kyro-telemetry.XXXXXX")"; '
            "trap 'rm -f \"$telemetry_script\"' EXIT; "
            f"curl -fsSL {shell_quote(telemetry_script_url)} "
            '-o "$telemetry_script" || exit 1; '
            f"{target_context}"
            f'TARGET_CONTEXT="$target_context" TARGET_NAMESPACE={shell_quote(namespace)} '
            f"TARGET_CLUSTER_ID={shell_quote(expected_cluster_id)} "
            f"WORKSPACE_ID={shell_quote(payload.workspace_id)} "
            f"MANAGEMENT_API_BASE_URL={shell_quote(management_api_base_url)} "
            f"ALERTMANAGER_AGENT_TOKEN={shell_quote(telemetry_agent_token)} "
            f"TELEMETRY_ASSET_BASE_URL={shell_quote(telemetry_asset_base_url)} "
            'bash "$telemetry_script" || exit 1; '
        )
        if (
            telemetry_script_url
            and telemetry_asset_base_url
            and telemetry_agent_token
            and management_api_base_url
        )
        else ""
    )
    wait_for_uninstall = (
        (
            'if [ -z "$existing" ]; then '
            "for attempt in $(seq 1 60); do "
            f"if ! {kubectl} get clusterrole cluster-agent-uninstall >/dev/null 2>&1; "
            "then break; fi; sleep 2; done; "
            f"if {kubectl} get clusterrole cluster-agent-uninstall >/dev/null 2>&1; "
            "then printf 'previous Kyro agent uninstall is still running.\\n' >&2; exit 1; fi; "
            "fi; "
        )
        if payload.cluster_role != MANAGEMENT_CLUSTER_ROLE
        else ""
    )
    # The command is pasted into an already-open operator terminal. Keep the
    # fail-closed ownership guard, but contain its `exit 1` in a subshell so a
    # mismatch cannot terminate the interactive shell itself.
    return (
        f"({guard}{install_telemetry}{wait_for_uninstall}"
        f"curl -fsSL {shell_quote(manifest_url)} | {kubectl} apply -f -)"
    )


def kubectl_apply_command(
    payload: TargetRegisterRequest, agent_token: str, context: str = ""
) -> str:
    base = normalized_management_base_url(payload.management_base_url)
    if not base:
        return ""
    path = gateway_routes.INSTALL_MANIFEST_PATH.format(agent_token=agent_token)
    telemetry_enabled = payload.cluster_role != MANAGEMENT_CLUSTER_ROLE
    return guarded_kubectl_apply_command(
        payload,
        f"{base}{path}",
        context,
        telemetry_script_url=(
            telemetry_script_url(base, agent_token, "bash") if telemetry_enabled else ""
        ),
        telemetry_asset_base_url=(
            telemetry_asset_base_url(base, agent_token) if telemetry_enabled else ""
        ),
        telemetry_agent_token=agent_token if telemetry_enabled else "",
        management_api_base_url=base if telemetry_enabled else "",
    )


def bootstrap_command_for(payload: TargetRegisterRequest, agent_token: str) -> str:
    cloud_provider = payload.cloud_provider.strip()
    base_install = install_command_for(payload, agent_token)
    if cloud_provider == "eks":
        if not provider_config_text(payload, "region", "") or not provider_config_text(
            payload, "eks_cluster_name", ""
        ):
            return base_install
        values = require_provider_config(payload, "region", "eks_cluster_name")
        context_alias = provider_config_text(payload, "context_alias", payload.cluster_id or "")
        return (
            "aws eks update-kubeconfig "
            f"--region {shell_quote(values['region'])} "
            f"--name {shell_quote(values['eks_cluster_name'])} "
            f"--alias {shell_quote(context_alias)} "
            f"&& kubectl --context {shell_quote(context_alias)} get nodes "
            f"&& {kubectl_apply_command(payload, agent_token, context_alias)}"
        )
    if cloud_provider == "gke":
        values = require_provider_config(
            payload, "project_id", "location_type", "location", "gke_cluster_name"
        )
        if values["location_type"] not in {"region", "zone"}:
            raise HTTPException(
                status_code=422,
                detail="provider_config.location_type must be region or zone",
            )
        location_flag = "--zone" if values["location_type"] == "zone" else "--region"
        return (
            "gcloud container clusters get-credentials "
            f"{shell_quote(values['gke_cluster_name'])} "
            f"--project {shell_quote(values['project_id'])} "
            f"{location_flag} {shell_quote(values['location'])} "
            f"&& kubectl get nodes "
            f"&& {base_install}"
        )
    if cloud_provider == "aks":
        values = require_provider_config(payload, "resource_group", "aks_cluster_name")
        return (
            "az aks get-credentials "
            f"--resource-group {shell_quote(values['resource_group'])} "
            f"--name {shell_quote(values['aks_cluster_name'])} "
            "--overwrite-existing "
            f"&& kubectl get nodes "
            f"&& {base_install}"
        )
    if cloud_provider == "existing-k8s":
        context = provider_config_text(payload, "context_name", payload.kube_context or "")
        if context:
            return (
                f"kubectl --context {shell_quote(context)} get nodes "
                f"&& {kubectl_apply_command(payload, agent_token, context)}"
            )
        return base_install
    if cloud_provider == "kind":
        name = provider_config_text(
            payload, "kind_cluster_name", payload.name or payload.cluster_id
        )
        context = f"kind-{name.removeprefix('kind-')}"
        return (
            f"kubectl --context {shell_quote(context)} get nodes "
            f"&& {kubectl_apply_command(payload, agent_token, context)}"
        )
    if cloud_provider == "minikube":
        context = provider_config_text(payload, "profile", "minikube")
        return (
            f"kubectl --context {shell_quote(context)} get nodes "
            f"&& {kubectl_apply_command(payload, agent_token, context)}"
        )
    return base_install


def powershell_install_command_for(payload: TargetRegisterRequest, agent_token: str) -> str:
    base = normalized_management_base_url(payload.management_base_url)
    if not base:
        return ""
    path = gateway_routes.INSTALL_MANIFEST_PATH.format(agent_token=agent_token)
    manifest_url = f"{base}{path}".replace("'", "''")
    namespace = agent_namespace(payload).replace("'", "''")
    expected_cluster_id = (payload.cluster_id or "").replace("'", "''")
    telemetry_block = ""
    uninstall_wait_block = ""
    if payload.cluster_role != MANAGEMENT_CLUSTER_ROLE:
        script_url = telemetry_script_url(base, agent_token, "powershell").replace("'", "''")
        asset_base_url = telemetry_asset_base_url(base, agent_token).replace("'", "''")
        workspace_id = payload.workspace_id.replace("'", "''")
        management_api_base_url = base.replace("'", "''")
        escaped_agent_token = agent_token.replace("'", "''")
        telemetry_block = (
            f"Invoke-WebRequest -UseBasicParsing -Uri '{script_url}' -OutFile $script; "
            f"$kyroAgentToken='{escaped_agent_token}'; "
            f"& $script -TargetContext $context -TargetNamespace '{namespace}' "
            f"-AssetBaseUrl '{asset_base_url}' -ClusterId '{expected_cluster_id}' "
            f"-WorkspaceId '{workspace_id}' "
            f"-ManagementApiBaseUrl '{management_api_base_url}' "
            "-AgentToken $kyroAgentToken; "
        )
        uninstall_wait_block = (
            "if (-not $existing) { for ($attempt=0; $attempt -lt 60; $attempt++) { "
            "$old=(& kubectl get clusterrole cluster-agent-uninstall "
            "--ignore-not-found -o name 2>$null); "
            "if (-not $old) { break }; Start-Sleep -Seconds 2 }; "
            "if ($old) { throw 'previous Kyro agent uninstall is still running' } }; "
        )
    return (
        "$ErrorActionPreference='Stop'; "
        "$existing=''; "
        f"$targetNamespace=(& kubectl get namespace '{namespace}' --ignore-not-found -o name 2>$null); "
        f"if ($targetNamespace) {{ $existing=(& kubectl -n '{namespace}' get configmap "
        "target-runtime-config --ignore-not-found "
        "-o 'jsonpath={.data.TARGET_CLUSTER_ID}' 2>$null) }; "
        f"if ($existing -and $existing -ne '{expected_cluster_id}') "
        f'{{ throw "Kyro agent is already registered as $existing; disconnect it before connecting {expected_cluster_id}." }}; '
        "$context=(& kubectl config current-context).Trim(); "
        "$script=Join-Path ([IO.Path]::GetTempPath()) ('kyro-'+[guid]::NewGuid().ToString()+'.ps1'); "
        "$manifest=Join-Path ([IO.Path]::GetTempPath()) ('kyro-'+[guid]::NewGuid().ToString()+'.yaml'); "
        f"try {{ {telemetry_block}{uninstall_wait_block}"
        f"Invoke-WebRequest -UseBasicParsing -Uri '{manifest_url}' -OutFile $manifest; "
        "& kubectl apply -f $manifest; "
        "if ($LASTEXITCODE -ne 0) { throw 'manifest installation failed' } } "
        "finally { Remove-Item -LiteralPath $script,$manifest -Force -ErrorAction SilentlyContinue; "
        "Remove-Variable kyroAgentToken -ErrorAction SilentlyContinue }"
    )


def bootstrap_steps_for(payload: TargetRegisterRequest, command: str) -> list[BootstrapStep]:
    steps = [
        BootstrapStep(label="kubeconfig 확인", command="kubectl config current-context"),
    ]
    if command:
        steps.append(BootstrapStep(label="target agent 설치", command=command))
    steps.append(BootstrapStep(label="연결 확인", command="kubectl -n target get pods"))
    return steps


def install_response(
    payload: TargetRegisterRequest,
    manifest: str,
    apply_output: str | None,
    agent_token: str,
    *,
    connect_timeout_seconds: int | None = None,
    connect_expires_at: str | None = None,
) -> TargetInstallResponse:
    bootstrap_command = bootstrap_command_for(payload, agent_token)
    applied = apply_output is not None
    return TargetInstallResponse(
        registered=True,
        cluster_id=payload.cluster_id,
        status=(
            ClusterRegistrationStatus.INSTALL_APPLIED.value
            if applied
            else ClusterRegistrationStatus.PENDING_INSTALL.value
        ),
        applied=applied,
        apply_output=apply_output,
        install_manifest=manifest,
        agent_token=agent_token,
        install_command=install_command_for(payload, agent_token),
        bootstrap_command=bootstrap_command,
        powershell_install_command=powershell_install_command_for(payload, agent_token),
        powershell_bootstrap_command=powershell_install_command_for(payload, agent_token),
        bootstrap_steps=bootstrap_steps_for(payload, bootstrap_command),
        connect_timeout_seconds=connect_timeout_seconds,
        connect_expires_at=connect_expires_at,
        connection_stage="awaiting_install" if applied else "token_issued",
        management_access=management_access_response(),
    )


def visible_cluster_agent_statuses(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """활성 agent만 노출하고, 모두 오래됐으면 최근 상태 한 건을 보존한다."""
    online_agents = [
        agent for agent in agents if cluster_connection_status(agent) == AGENT_STATUS_ONLINE
    ]
    return online_agents or agents[:1]


def registration_connect_timeout(registration: dict[str, Any] | None) -> int | None:
    settings = (registration or {}).get("settings") or {}
    value = settings.get("connect_timeout_seconds")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def registration_connect_expires_at(registration: dict[str, Any] | None) -> str | None:
    settings = (registration or {}).get("settings") or {}
    value = settings.get("connect_expires_at")
    return str(value) if value else None


def registration_connection_status(
    registration: dict[str, Any] | None,
    latest_agent: dict[str, Any] | None,
) -> str:
    agent_status = cluster_connection_status(latest_agent)
    if latest_agent is not None:
        return agent_status
    if registration is None:
        return AGENT_STATUS_NEVER_CONNECTED
    status = str(registration.get("status") or "")
    expires_at = parse_timestamp(registration_connect_expires_at(registration))
    if status in {
        ClusterRegistrationStatus.PENDING_INSTALL.value,
        ClusterRegistrationStatus.INSTALL_APPLIED.value,
    }:
        if expires_at is not None and datetime.now(UTC) > expires_at:
            return AGENT_STATUS_INSTALL_EXPIRED
        return AGENT_STATUS_PENDING_INSTALL
    if status == ClusterRegistrationStatus.INSTALL_FAILED.value:
        return AGENT_STATUS_INSTALL_FAILED
    if status == ClusterRegistrationStatus.INSTALL_EXPIRED.value:
        return AGENT_STATUS_INSTALL_EXPIRED
    return agent_status


def resolved_cluster_provider(
    registration: dict[str, Any],
    latest_snapshot: dict[str, Any] | None,
) -> str:
    settings = registration.get("settings") or {}
    selected = str(settings.get("cloud_provider") or "").strip().lower()
    if selected in CONCRETE_CLUSTER_PROVIDERS:
        return selected

    source_summary = snapshot_source_summary(latest_snapshot) or {}
    detected = str(source_summary.get("detected_provider") or "").strip().lower()
    if detected in DETECTED_CLUSTER_PROVIDERS:
        return detected
    provider_config = settings.get("provider_config") or {}
    hinted = str(provider_config.get("provider_hint") or "").strip().lower()
    if hinted in CONCRETE_CLUSTER_PROVIDERS:
        return hinted
    if selected in GENERIC_ONPREM_PROVIDERS:
        return "onprem"
    return "unknown"


def current_connection_snapshot(
    registration: dict[str, Any] | None,
    latest_agent: dict[str, Any],
    latest_snapshot: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, datetime | None]:
    if latest_snapshot is None:
        return None, None
    if str(latest_snapshot.get("agent_id") or "") != str(latest_agent.get("agent_id") or ""):
        return None, None
    snapshot_at = parse_timestamp(latest_snapshot.get("created_at"))
    if snapshot_at is None:
        return None, None
    registration_updated_at = parse_timestamp((registration or {}).get("updated_at"))
    if registration_updated_at is not None and snapshot_at < registration_updated_at:
        return None, None
    return latest_snapshot, snapshot_at


def cluster_connection_stage(
    registration: dict[str, Any] | None,
    latest_agent: dict[str, Any] | None,
    latest_snapshot: dict[str, Any] | None,
) -> str:
    if latest_agent is not None:
        agent_status = str(latest_agent.get("status") or "").strip().lower()
        if agent_status in AGENT_ERROR_STATUSES:
            return "error"
        if cluster_connection_status(latest_agent) != AGENT_STATUS_ONLINE:
            return "error"
        current_snapshot, snapshot_at = current_connection_snapshot(
            registration,
            latest_agent,
            latest_snapshot,
        )
        if current_snapshot is None or snapshot_at is None:
            return "agent_connected"
        last_seen_at = parse_timestamp(latest_agent.get("last_seen_at"))
        return (
            "ready"
            if last_seen_at is not None and last_seen_at > snapshot_at
            else "snapshot_received"
        )

    connection_status = registration_connection_status(registration, None)
    if connection_status == AGENT_STATUS_INSTALL_EXPIRED:
        return "expired"
    if connection_status == AGENT_STATUS_PENDING_INSTALL:
        return "awaiting_install"
    if connection_status == AGENT_STATUS_INSTALL_FAILED:
        return "error"
    return "error"


def require_test_fixture_purge_environment(registration: dict[str, Any]) -> None:
    registration_environment = str(registration.get("environment") or "")
    if test_fixture_purge_enabled() and registration_environment == TEST_FIXTURE_ENVIRONMENT:
        return
    raise HTTPException(
        status_code=403,
        detail={
            "code": TEST_FIXTURE_PURGE_FORBIDDEN_CODE,
            "detail": (
                "테스트 fixture purge는 TEST_FIXTURE_PURGE_ENABLED=1 및 "
                "environment=test에서만 허용됩니다"
            ),
        },
    )


def unregisterable_registration(db: Any, workspace_id: str, cluster_id: str) -> dict[str, Any]:
    registration = db.get_cluster_registration(workspace_id, cluster_id)
    if registration is None:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=CLUSTER_NOT_FOUND)
    if is_management_registration(registration):
        raise HTTPException(status_code=400, detail=management_readonly_detail())
    return registration


def cluster_summary(
    cluster: dict[str, Any],
    latest_agent: dict[str, Any] | None,
    *,
    latest_snapshot: dict[str, Any] | None = None,
) -> ClusterSummary:
    connection_status = registration_connection_status(cluster, latest_agent)
    status = (
        ClusterRegistrationStatus.INSTALL_EXPIRED.value
        if connection_status == AGENT_STATUS_INSTALL_EXPIRED
        else cluster["status"]
    )
    kubernetes_version, namespace_count, crd_discovery_status = cluster_observation_metadata(
        latest_snapshot, latest_agent
    )
    return ClusterSummary(
        workspace_id=cluster["workspace_id"],
        cluster_id=cluster["cluster_id"],
        name=cluster["name"],
        environment=cluster["environment"],
        status=status,
        settings=cluster.get("settings") or {},
        connection_status=connection_status,
        observation_mode=(
            "simulation"
            if (cluster.get("settings") or {}).get("observation_mode") == "simulation"
            else "agent"
        ),
        provider=resolved_cluster_provider(cluster, latest_snapshot),
        connection_stage=cluster_connection_stage(cluster, latest_agent, latest_snapshot),
        last_agent_id=latest_agent.get("agent_id") if latest_agent else None,
        last_agent_seen_at=latest_agent.get("last_seen_at") if latest_agent else None,
        kubernetes_version=kubernetes_version,
        namespace_count=namespace_count,
        crd_discovery_status=crd_discovery_status,
        last_seen_at=latest_agent.get("last_seen_at") if latest_agent else None,
        created_at=cluster.get("created_at"),
        updated_at=cluster.get("updated_at"),
    )


def cluster_observation_metadata(
    latest_snapshot: dict[str, Any] | None,
    latest_agent: dict[str, Any] | None,
) -> tuple[str | None, int | None, str | None]:
    source = snapshot_source_summary(latest_snapshot)
    if source is None:
        return None, None, None
    nodes = source.get("nodes")
    node_items = nodes if isinstance(nodes, list) else []
    versions = {
        str(node.get("version") or "").strip() for node in node_items if isinstance(node, dict)
    }
    versions.discard("")
    details = (latest_agent or {}).get("details")
    traffic_sources = details.get("traffic_sources") if isinstance(details, dict) else None
    traffic_cluster = traffic_sources.get("cluster") if isinstance(traffic_sources, dict) else None
    observed_server_version = (
        str(traffic_cluster.get("kubernetes_version") or "").strip()
        if isinstance(traffic_cluster, dict)
        else ""
    )
    kubernetes_version = observed_server_version or (
        next(iter(versions)) if len(versions) == 1 else None
    )
    namespaces = source.get("namespaces")
    # namespace 스코프 수집(control_namespaces)은 resources_complete=False 로 보고되므로
    # "완전 수집"을 요구하면 실제 관측값이 영원히 null 로 남는다. 관측된 namespace 목록이
    # 있으면 그 수를 그대로 제공한다(관측 범위 내 실측 — last-known-good).
    namespace_count = (
        len({value for value in namespaces if isinstance(value, str) and value})
        if isinstance(namespaces, list)
        else None
    )
    discovery = source.get("api_resource_discovery")
    discovery_status = discovery.get("completeness") if isinstance(discovery, dict) else None
    crd_discovery_status = (
        str(discovery_status) if discovery_status in {"exact", "partial", "unavailable"} else None
    )
    return kubernetes_version, namespace_count, crd_discovery_status


def complete_inventory_snapshot(latest_snapshot: dict[str, Any] | None) -> bool:
    source = snapshot_source_summary(latest_snapshot)
    if source is None or source.get("resources_complete") is not True:
        return False
    limits = source.get("collection_limits")
    return not isinstance(limits, dict) or limits.get("truncated") is not True


def enrich_cluster_inventory_counts(
    db: Any,
    workspace_id: str,
    summary: ClusterSummary,
    latest_snapshot: dict[str, Any] | None,
    resource_counts: list[dict[str, Any]] | None = None,
) -> None:
    # 과거에는 "완전 수집" snapshot 만 카운트를 채웠지만, namespace 스코프 에이전트는
    # resources_complete=False 를 보고하므로 실측 카운트가 영원히 null 로 남아 화면이
    # 비었다. snapshot 이 하나라도 있으면 그 안의 실측 행 수를 그대로 제공한다
    # (관측 범위 내 사실 — 지어내는 값 아님, last-known-good).
    if not isinstance(latest_snapshot, dict) or not latest_snapshot:
        return
    count_reader = getattr(db, "inventory_resource_counts", None)
    if not callable(count_reader):
        return
    rows = (
        count_reader(workspace_id, summary.cluster_id)
        if resource_counts is None
        else resource_counts
    )
    counts = inventory_counts(rows)
    summary.node_count = counts.get("node", 0)
    summary.server_count = summary.node_count
    summary.pod_count = counts.get("pod", 0)
    summary.namespace_count = counts.get("namespace", summary.namespace_count)


def touch_agent_seen(
    db: Any,
    identity: ClusterAgentIdentity,
    agent_id: str | None,
    *,
    status: str = "connected",
) -> None:
    if not agent_id:
        return
    # heartbeat 는 best-effort — 저장소가 메서드를 제공하지 않으면 폴링을 막지 않고 건너뜀.
    saver = getattr(db, "save_cluster_agent_status", None)
    if saver is None:
        return
    saver(
        workspace_id=identity.workspace_id,
        cluster_id=identity.cluster_id,
        agent_id=agent_id,
        capabilities=None,
        status=status,
        details={"heartbeat_source": "agent_api"},
    )


def inventory_counts(counts: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for row in counts:
        resource_type = str(row.get("resource_type") or "")
        totals[resource_type] = totals.get(resource_type, 0) + int(row.get("count") or 0)
    return totals


def target_register_payload_from_settings(settings: dict[str, Any]) -> TargetRegisterRequest:
    allowed = set(TargetRegisterRequest.model_fields)
    return TargetRegisterRequest(
        **{key: value for key, value in settings.items() if key in allowed}
    )


def require_target_registration_preflight(
    payload: TargetRegisterRequest,
    workspace_id: str,
    db: Any,
) -> None:
    getter = getattr(db, "get_cluster_registration", None)
    if callable(getter) and getter(workspace_id, payload.cluster_id or "") is not None:
        raise HTTPException(status_code=409, detail="cluster_id is already registered")
    allowed = set(TargetPreflightRequest.model_fields)
    preflight_payload = TargetPreflightRequest(
        **{key: value for key, value in payload.model_dump().items() if key in allowed}
    )
    provider_ready, errors, _warnings, _selected, _kube_context_allowed = (
        target_preflight_provider_checks(preflight_payload)
    )
    if not provider_ready or errors:
        raise HTTPException(
            status_code=422, detail={"message": "target preflight failed", "errors": errors}
        )


# require_admin_session 이 세션을 검증 → base router 에 둠.
# (라우터 단위 require_session + require_admin_session = 이중 검증/레이트리밋 2배 회피)
@router.post(gateway_routes.TARGETS_PREFLIGHT_PATH, response_model=TargetPreflightResponse)
async def target_registration_preflight(
    payload: TargetPreflightRequest,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> TargetPreflightResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    cluster_id = payload.cluster_id.strip()
    errors: list[str] = []
    warnings: list[str] = []

    if not cluster_id:
        errors.append("cluster_id is required")
    elif not CLUSTER_ID_PATTERN.match(cluster_id):
        errors.append("cluster_id must use lowercase letters, numbers, and hyphens")
    if is_blocked_test_cluster(cluster_id):
        errors.append("test target registrations are not allowed")

    provider_ready, provider_errors, provider_warnings, selected, kube_context_allowed = (
        target_preflight_provider_checks(payload)
    )
    errors.extend(provider_errors)
    warnings.extend(provider_warnings)

    existing = None
    getter = getattr(db, "get_cluster_registration", None)
    if cluster_id and callable(getter):
        existing = getter(workspace_id, cluster_id)
    duplicate_cluster_id = existing is not None
    if duplicate_cluster_id:
        errors.append("cluster_id is already registered")

    agents: list[dict[str, Any]] = []
    lister = getattr(db, "list_cluster_agent_statuses", None)
    if cluster_id and callable(lister):
        agents = visible_cluster_agent_statuses(lister(workspace_id, cluster_id))
    latest_agent = agents[0] if agents else None
    connection_status = (
        cluster_connection_status(latest_agent)
        if duplicate_cluster_id or latest_agent
        else AGENT_STATUS_NOT_REGISTERED
    )

    return TargetPreflightResponse(
        valid=not errors,
        duplicate_cluster_id=duplicate_cluster_id,
        provider_ready=provider_ready,
        agent_install_status=connection_status,
        connection_status=connection_status,
        kube_context_allowed=kube_context_allowed,
        errors=errors,
        warnings=warnings,
        selected=selected,
        last_agent_id=latest_agent.get("agent_id") if latest_agent else None,
        last_seen_at=latest_agent.get("last_seen_at") if latest_agent else None,
        management_access=management_access_response(),
    )


@router.post(gateway_routes.TARGETS_PATH, response_model=TargetInstallResponse)
async def register_target(
    payload: TargetRegisterRequest,
    current: Any = Depends(require_admin_session),  # kubectl apply 실행 → admin 만
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
) -> TargetInstallResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    cluster_id = resolve_target_cluster_id(payload, workspace_id, db)
    scoped_payload = normalize_target_provider_defaults(payload).model_copy(
        update={"workspace_id": workspace_id, "cluster_id": cluster_id}
    )
    reject_test_target(scoped_payload)
    require_management_base_url(scoped_payload)
    access = management_access_response()
    if (
        scoped_payload.cluster_role != MANAGEMENT_CLUSTER_ROLE
        and access.mode != "unknown"
        and access.reachability == "self_only"
    ):
        raise HTTPException(status_code=422, detail=EXTERNAL_ACCESS_REQUIRED)
    require_secure_agent_bootstrap(scoped_payload)
    require_target_registration_preflight(scoped_payload, workspace_id, db)
    validate_target_install_providers(scoped_payload)
    validate_target_bootstrap_config(scoped_payload)
    components = target_desired_components(scoped_payload)
    version = desired_state_version(components)

    # 클러스터별 agent 토큰 생성 — 원문은 이 클러스터 secret 에만 주입, 해시만 레지스트리에 저장.
    # 전역 AGENT_TOKEN 신뢰를 제거(토큰 1개로 전 워크스페이스 접근하던 구멍 차단). 재등록 시 회전.
    agent_token = secrets.token_urlsafe(AGENT_TOKEN_BYTES)
    agent_envelope_public_key, agent_envelope_private_key = generate_agent_envelope_keypair()
    encrypted_agent_envelope_private_key = encrypt_credential(agent_envelope_private_key)
    manifest = target_install_manifest(
        scoped_payload,
        agent_token,
        agent_envelope_private_key,
        image_pull_secret=agent_image_pull_secret(),
    )
    status_updater = getattr(db, "update_cluster_registration_status", None)
    if scoped_payload.apply and not callable(status_updater):
        raise HTTPException(
            status_code=503, detail="cluster registration status update unavailable"
        )
    connect_timeout_seconds = target_registration_connect_timeout_seconds()
    created_at = datetime.now(UTC)
    connect_expires_at = connect_expires_at_from(created_at, connect_timeout_seconds)
    registration_settings = scoped_payload.model_dump(exclude={"apply", "kube_context"})
    registration_settings["connect_timeout_seconds"] = connect_timeout_seconds
    registration_settings["connect_expires_at"] = connect_expires_at

    # 클러스터 등록·정책·desired-state·이벤트 스테이징을 한 트랜잭션으로 —
    # 부분 실패 시 정책/desired-state 없는 반쪽 등록(고아)이 남지 않음.
    with unit_of_work_or_null(db) as registration_connection:
        # 동시 등록은 같은 클러스터 advisory lock 아래에서 재확인해 후행 upsert가
        # 먼저 commit된 agent key를 회전시키거나 두 agent를 apply하지 못하게 한다.
        registration_lock = getattr(db, "lock_cluster_policy_for_update", None)
        if callable(registration_lock):
            registration_lock(
                workspace_id,
                scoped_payload.cluster_id,
                conn=registration_connection,
            )
        registration_getter = getattr(db, "get_cluster_registration", None)
        if (
            callable(registration_getter)
            and registration_getter(workspace_id, scoped_payload.cluster_id) is not None
        ):
            raise HTTPException(status_code=409, detail="cluster_id is already registered")
        try:
            db.register_target_cluster(
                {
                    "workspace_id": workspace_id,
                    "user_id": current.user_id,
                    "cluster_id": scoped_payload.cluster_id,
                    "name": scoped_payload.name,
                    "environment": scoped_payload.environment,
                    "status": ClusterRegistrationStatus.PENDING_INSTALL.value,
                    "agent_token_hash": hash_agent_token(agent_token),
                    "agent_envelope_public_key": agent_envelope_public_key,
                    "agent_envelope_private_key_encrypted": (encrypted_agent_envelope_private_key),
                    "settings": registration_settings,
                }
            )
        except IntegrityError as exc:
            if is_cluster_name_integrity_conflict(exc):
                raise HTTPException(
                    status_code=409,
                    detail=cluster_name_conflict_detail(),
                ) from exc
            raise
        if db.get_cluster_policy(workspace_id, scoped_payload.cluster_id) is None:
            policy = default_agent_policy(
                cluster_id=scoped_payload.cluster_id,
                cluster_role=scoped_payload.cluster_role,
                interval_seconds=scoped_payload.evidence_interval_seconds,
                evidence_profile=evidence_profile_for_registration(
                    cluster_role=scoped_payload.cluster_role,
                    environment=scoped_payload.environment,
                    install_sample_workload=scoped_payload.install_sample_workload,
                ),
                # control_namespaces 가 실제 수집 범위에 반영되게 정책 쿼리로 컴파일.
                control_namespaces=control_namespace_tuple(scoped_payload.control_namespaces),
                bootstrap_mode=(
                    "management"
                    if scoped_payload.cluster_role == MANAGEMENT_CLUSTER_ROLE
                    else "target"
                ),
            )
            if scoped_payload.cluster_role == MANAGEMENT_CLUSTER_ROLE:
                policy = freeze_management_policy(policy)
            db.upsert_cluster_policy(workspace_id, scoped_payload.cluster_id, policy.model_dump())
        db.upsert_target_desired_states(
            workspace_id,
            scoped_payload.cluster_id,
            [component.to_body() for component in components],
            current.user_id,
        )
        await events.accept_body(
            ClusterDesiredStateChangedBody(
                workspace_id=workspace_id,
                cluster_id=scoped_payload.cluster_id,
                desired_state_version=version,
                components=components,
                reason="target registered",
                requested_by=current.user_id,
            )
        )

    # 외부 bootstrap은 pending 등록 commit 뒤에만 실행한다. kubectl apply는 같은
    # manifest 재시도에 멱등이며, 부분 실패해도 저장된 per-cluster key를 유지한다.
    apply_output: str | None = None
    if scoped_payload.apply:
        try:
            apply_output = apply_manifest_with_kubectl(manifest, scoped_payload.kube_context)
        except Exception:
            with unit_of_work_or_null(db):
                status_updater(
                    workspace_id,
                    scoped_payload.cluster_id,
                    ClusterRegistrationStatus.INSTALL_FAILED.value,
                )
            raise
        with unit_of_work_or_null(db):
            status_updater(
                workspace_id,
                scoped_payload.cluster_id,
                ClusterRegistrationStatus.INSTALL_APPLIED.value,
            )
    return install_response(
        scoped_payload,
        manifest,
        apply_output,
        agent_token,
        connect_timeout_seconds=connect_timeout_seconds,
        connect_expires_at=connect_expires_at,
    )


@router.post(
    gateway_routes.CLUSTERS_CONNECT_PATH,
    response_model=ClusterConnectResponse,
)
async def connect_cluster(
    payload: ClusterConnectRequest,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> ClusterConnectResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    display_name = payload.name.strip()
    require_unique_cluster_display_name(db, workspace_id, display_name)
    try:
        receipt = await register_target(
            TargetRegisterRequest(
                name=display_name,
                environment=payload.environment,
                cloud_provider="existing-k8s",
                deploy_provider=MANUAL_MANIFEST_DEPLOY_PROVIDER,
                provider_config={"provider_hint": CONNECT_PROVIDER_HINTS[payload.provider]},
                otel_traces_endpoint=DEFAULT_OTEL_TRACES_URL,
            ),
            current=current,
            db=db,
            events=events,
        )
    except IntegrityError as exc:
        if is_cluster_name_integrity_conflict(exc):
            raise HTTPException(
                status_code=409,
                detail=cluster_name_conflict_detail(),
            ) from exc
        raise
    try:
        if not receipt.install_command or not receipt.connect_expires_at:
            raise HTTPException(status_code=503, detail="cluster install command is unavailable")
        await update_prometheus_integration(
            PrometheusIntegrationUpdateRequest(
                cluster_id=receipt.cluster_id,
                prometheus_url=DEFAULT_PROMETHEUS_URL,
                headers={},
            ),
            current=current,
            db=db,
            events=events,
            operation_events=operation_events,
        )
    except Exception:
        # No install command has been returned yet, so this newly-created registration
        # cannot have a live agent. Revoke its token and active-name claim to make an
        # immediate retry safe while retaining the audit row.
        unregister = getattr(db, "unregister_target_cluster", None)
        if callable(unregister):
            with suppress(Exception):
                unregister(workspace_id, receipt.cluster_id)
        raise
    return ClusterConnectResponse(
        cluster_id=receipt.cluster_id,
        install_command=receipt.install_command,
        powershell_install_command=receipt.powershell_install_command,
        expires_at=receipt.connect_expires_at,
    )


@router.post(
    gateway_routes.CLUSTER_CONNECT_COMMAND_PATH,
    response_model=ClusterConnectResponse,
)
async def reissue_cluster_connect_command(
    cluster_id: str,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> ClusterConnectResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    registration = unregisterable_registration(db, workspace_id, cluster_id)
    status = str(registration.get("status") or "")
    if status not in {
        ClusterRegistrationStatus.PENDING_INSTALL.value,
        ClusterRegistrationStatus.INSTALL_APPLIED.value,
        ClusterRegistrationStatus.INSTALL_FAILED.value,
        ClusterRegistrationStatus.INSTALL_EXPIRED.value,
    }:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cluster_already_connected",
                "detail": "이미 연결된 클러스터의 설치 명령은 다시 발급할 수 없습니다",
            },
        )
    agents = visible_cluster_agent_statuses(
        db.list_cluster_agent_statuses(workspace_id, cluster_id)
    )
    if agents and cluster_connection_status(agents[0]) == AGENT_STATUS_ONLINE:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cluster_already_connected",
                "detail": "에이전트가 이미 연결되어 설치 명령을 다시 발급하지 않았습니다",
            },
        )

    payload = normalize_target_provider_defaults(
        target_register_payload_from_settings(registration.get("settings") or {})
    )
    require_secure_agent_bootstrap(payload)
    agent_token = secrets.token_urlsafe(AGENT_TOKEN_BYTES)
    agent_envelope_public_key, agent_envelope_private_key = generate_agent_envelope_keypair()
    encrypted_agent_envelope_private_key = encrypt_credential(agent_envelope_private_key)
    timeout_seconds = target_registration_connect_timeout_seconds()
    expires_at = connect_expires_at_from(datetime.now(UTC), timeout_seconds)
    settings = payload.model_dump(exclude={"apply", "kube_context"})
    settings["connect_timeout_seconds"] = timeout_seconds
    settings["connect_expires_at"] = expires_at
    rotate = getattr(db, "reissue_target_cluster_install", None)
    if not callable(rotate):
        raise HTTPException(
            status_code=503, detail="cluster install command rotation is unavailable"
        )
    with unit_of_work_or_null(db):
        rotated = rotate(
            workspace_id,
            cluster_id,
            agent_token_hash=hash_agent_token(agent_token),
            agent_envelope_public_key=agent_envelope_public_key,
            agent_envelope_private_key_encrypted=encrypted_agent_envelope_private_key,
            settings=settings,
        )
        if not rotated:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "cluster_connect_state_changed",
                    "detail": "연결 상태가 바뀌어 설치 명령을 다시 발급하지 않았습니다",
                },
            )
    return ClusterConnectResponse(
        cluster_id=cluster_id,
        install_command=install_command_for(payload, agent_token),
        powershell_install_command=powershell_install_command_for(payload, agent_token),
        expires_at=expires_at,
    )


def installer_registration_by_token(agent_token: str, db: Any) -> dict[str, Any]:
    """Resolve one installer-scoped registration without exposing token validity."""

    identity = db.authenticate_cluster_agent(hash_agent_token(agent_token))
    if identity is None:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail="install link not found")
    private_registration_getter = getattr(db, "get_cluster_registration_install_credentials", None)
    if callable(private_registration_getter):
        registration = private_registration_getter(identity["workspace_id"], identity["cluster_id"])
    else:
        # Narrow compatibility path for non-persistent test doubles. Production
        # repositories always use the secret-bearing installer-only reader above.
        registration = db.get_cluster_registration(identity["workspace_id"], identity["cluster_id"])
    if registration is None:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail="install link not found")
    return registration


@router.get(gateway_routes.INSTALL_TELEMETRY_SCRIPT_PATH, include_in_schema=False)
async def install_telemetry_script_by_token(
    agent_token: str,
    platform: str,
    db: Any = Depends(get_db),
) -> PlainTextResponse:
    installer_registration_by_token(agent_token, db)
    path = INSTALL_TELEMETRY_SCRIPTS.get(platform)
    if path is None or not path.is_file():
        raise HTTPException(status_code=NOT_FOUND_CODE, detail="install link not found")
    media_type = "text/x-powershell" if platform == "powershell" else "text/x-shellscript"
    return PlainTextResponse(
        path.read_text(encoding="utf-8"),
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
    )


@router.get(gateway_routes.INSTALL_TELEMETRY_ASSET_PATH, include_in_schema=False)
async def install_telemetry_asset_by_token(
    agent_token: str,
    asset_name: str,
    db: Any = Depends(get_db),
) -> PlainTextResponse:
    installer_registration_by_token(agent_token, db)
    if asset_name not in INSTALL_TELEMETRY_ASSETS:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail="install link not found")
    path = INSTALL_ARTIFACT_ROOT / "deploy" / "target" / asset_name
    if not path.is_file():
        raise HTTPException(status_code=NOT_FOUND_CODE, detail="install link not found")
    return PlainTextResponse(
        path.read_text(encoding="utf-8"),
        media_type="text/yaml",
        headers={"Cache-Control": "no-store"},
    )


@router.get(gateway_routes.INSTALL_MANIFEST_PATH, include_in_schema=True)
async def install_manifest_by_token(
    agent_token: str,
    db: Any = Depends(get_db),
) -> PlainTextResponse:
    """원라인 인스톨러가 사용할 Agent manifest를 토큰 범위로 렌더한다."""

    registration = installer_registration_by_token(agent_token, db)
    try:
        agent_envelope_private_key = decrypt_credential(
            str(registration.get("agent_envelope_private_key_encrypted") or "")
        )
        if agent_envelope_public_key(agent_envelope_private_key) != str(
            registration.get("agent_envelope_public_key") or ""
        ):
            raise CredentialEncryptionError("agent envelope keypair does not match")
    except CredentialEncryptionError as exc:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail="install link not found") from exc
    payload = target_register_payload_from_settings(registration.get("settings") or {})
    manifest = target_install_manifest(
        payload,
        agent_token,
        agent_envelope_private_key,
        image_pull_secret=agent_image_pull_secret(),
    )
    return PlainTextResponse(
        manifest,
        media_type="text/yaml",
        headers={"Cache-Control": "no-store"},
    )


@router.get(gateway_routes.TARGET_RBAC_MANIFEST_PATH, include_in_schema=True)
async def target_rbac_manifest_for_admin(
    cluster_id: str,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> PlainTextResponse:
    """Return an RBAC-only artifact that requires an external cluster administrator."""

    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    registration = db.get_cluster_registration(workspace_id, cluster_id)
    if registration is None:
        raise HTTPException(status_code=404, detail=CLUSTER_NOT_FOUND)
    if is_management_registration(registration):
        raise HTTPException(status_code=400, detail=management_readonly_detail())
    payload = target_register_payload_from_settings(registration.get("settings") or {})
    return PlainTextResponse(
        target_rbac_manifest(payload),
        media_type="text/yaml",
        headers={
            "Cache-Control": "no-store",
            "X-Target-RBAC-Manifest-Version": TARGET_RBAC_MANIFEST_VERSION,
        },
    )


@router.get(gateway_routes.CLUSTERS_PATH, response_model=ClusterListResponse)
async def list_clusters(
    limit: int = Query(
        default=gateway_limits.CLUSTER_LIST_DEFAULT_LIMIT,
        ge=1,
        le=gateway_limits.CLUSTER_LIST_MAX_LIMIT,
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ClusterListResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    accessible_cluster_ids = await asyncio.to_thread(
        db.accessible_resource_ids,
        current.user_id,
        workspace_id,
        AccessResourceType.CLUSTER.value,
        Permission.CLUSTER_READ.value,
    )
    clusters = await asyncio.to_thread(
        db.list_cluster_registrations,
        workspace_id,
        cluster_ids=accessible_cluster_ids,
        limit=limit,
    )
    cluster_ids = {cluster["cluster_id"] for cluster in clusters}
    snapshot_getter = getattr(db, "latest_inventory_snapshots", None)
    bulk_count_reader = getattr(db, "inventory_resource_counts_by_cluster", None)
    latest_agents, latest_snapshots = await asyncio.gather(
        asyncio.to_thread(db.latest_cluster_agent_statuses, workspace_id, cluster_ids),
        asyncio.to_thread(snapshot_getter, workspace_id, cluster_ids)
        if callable(snapshot_getter)
        else asyncio.sleep(0, result={}),
    )
    summaries = [
        cluster_summary(
            cluster,
            latest_agents.get(cluster["cluster_id"]),
            latest_snapshot=latest_snapshots.get(cluster["cluster_id"]),
        )
        for cluster in clusters
        if not is_blocked_test_cluster(cluster["cluster_id"], str(cluster["name"]))
    ]
    count_cluster_ids = {
        summary.cluster_id
        for summary in summaries
        if isinstance(latest_snapshots.get(summary.cluster_id), dict)
        and latest_snapshots.get(summary.cluster_id)
    }
    resource_counts = (
        await asyncio.to_thread(bulk_count_reader, workspace_id, count_cluster_ids)
        if callable(bulk_count_reader) and count_cluster_ids
        else None
    )
    for summary in summaries:
        if resource_counts is None:
            enrich_cluster_inventory_counts(
                db,
                workspace_id,
                summary,
                latest_snapshots.get(summary.cluster_id),
            )
        else:
            enrich_cluster_inventory_counts(
                db,
                workspace_id,
                summary,
                latest_snapshots.get(summary.cluster_id),
                resource_counts.get(summary.cluster_id, []),
            )
        # Cluster discovery is a latency-sensitive identity path. Exact incident
        # aggregation belongs to the dedicated fleet/issues APIs, so unknown
        # counts remain nullable here instead of blocking or fabricating zero.
    return ClusterListResponse(clusters=summaries)


@router.get(gateway_routes.CLUSTER_PATH, response_model=ClusterResponse)
async def get_cluster(
    cluster_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ClusterResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.CLUSTER_READ.value,
    )
    cluster = db.get_cluster_registration(workspace_id, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=404, detail=CLUSTER_NOT_FOUND)
    agents = visible_cluster_agent_statuses(
        db.list_cluster_agent_statuses(workspace_id, cluster_id)
    )
    latest_agent = agents[0] if agents else None
    snapshot_getter = getattr(db, "latest_inventory_snapshot", None)
    latest_snapshot = (
        snapshot_getter(workspace_id, cluster_id) if callable(snapshot_getter) else None
    )
    summary = cluster_summary(cluster, latest_agent, latest_snapshot=latest_snapshot)
    enrich_cluster_inventory_counts(db, workspace_id, summary, latest_snapshot)
    return ClusterResponse(cluster=summary, agents=agents)


@router.get(
    gateway_routes.CLUSTER_CONNECTION_STATUS_PATH,
    response_model=ClusterConnectionStatusResponse,
)
async def get_cluster_connection_status(
    cluster_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ClusterConnectionStatusResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.CLUSTER_READ.value,
    )
    registration_getter = getattr(db, "get_cluster_registration", None)
    registration = (
        registration_getter(workspace_id, cluster_id) if callable(registration_getter) else None
    )
    agents = visible_cluster_agent_statuses(
        db.list_cluster_agent_statuses(workspace_id, cluster_id)
    )
    latest_agent = agents[0] if agents else None
    snapshot_getter = getattr(db, "latest_inventory_snapshot", None)
    latest_snapshot = (
        snapshot_getter(workspace_id, cluster_id) if callable(snapshot_getter) else None
    )
    connection_status = registration_connection_status(registration, latest_agent)
    connection_stage = cluster_connection_stage(registration, latest_agent, latest_snapshot)
    telemetry_stack = _cluster_telemetry_stack(db, workspace_id, cluster_id, connection_status)
    return ClusterConnectionStatusResponse(
        cluster_id=cluster_id,
        connection_status=connection_status,
        connection_stage=connection_stage,
        telemetry_stack=telemetry_stack,
        refresh_after_seconds=connection_refresh_after_seconds(connection_stage),
        last_agent_id=latest_agent.get("agent_id") if latest_agent else None,
        last_seen_at=latest_agent.get("last_seen_at") if latest_agent else None,
        agents=agents,
        connect_timeout_seconds=registration_connect_timeout(registration),
        connect_expires_at=registration_connect_expires_at(registration),
    )


def _cluster_telemetry_stack(
    db: Any,
    workspace_id: str,
    cluster_id: str,
    connection_status: str,
) -> TelemetryStackView | None:
    """온라인 클러스터의 target 네임스페이스 워크로드에서 관측 스택 준비도를 실측한다.

    에이전트가 온라인이 아니거나 스택 관측이 0이면 None(진행바 미표시)."""
    if connection_status != AGENT_STATUS_ONLINE:
        return None
    lister = getattr(db, "list_inventory_resources", None)
    if not callable(lister):
        return None
    try:
        workloads = lister(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            resource_type="workload",
            namespace=TARGET_NAMESPACE,
            limit=200,
        )
    except Exception:  # noqa: BLE001 - 진행바 산출 실패는 연결 상태 응답을 막지 않는다.
        return None
    view = telemetry_stack_view(workloads if isinstance(workloads, list) else [])
    return TelemetryStackView.model_validate(view) if view else None


def connection_refresh_after_seconds(connection_stage: str | None) -> float | None:
    """Return the server-owned connect polling policy for the current stage."""

    if connection_stage in {"ready", "expired", "error"}:
        return None
    return 0.5


@router.get(
    gateway_routes.CLUSTER_CONNECTION_PATH,
    response_model=ClusterConnectStatusResponse,
)
async def get_cluster_connection(
    cluster_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ClusterConnectStatusResponse:
    connection = await get_cluster_connection_status(
        cluster_id,
        current=current,
        db=db,
    )
    stage = connection.connection_stage
    # 연결 판정은 rich stage 계약을 정본으로 사용한다. 위저드는 stage=="ready"
    # (에이전트 online + 인벤토리 스냅샷 적재 + 후속 heartbeat)에서만 완료로 넘어가고,
    # install_failed/error 는 무한 대기로 뭉개지 않고 명시적 failed 로 표면화한다.
    if stage == "ready":
        status = "connected"
    elif stage == "expired" or connection.connection_status == AGENT_STATUS_INSTALL_EXPIRED:
        status = "expired"
    elif stage == "error":
        status = "failed"
    else:
        status = "waiting"
    failure_reason: str | None = None
    if status == "failed":
        if connection.connection_status == AGENT_STATUS_INSTALL_FAILED:
            failure_reason = "install_failed"
        else:
            failure_reason = "agent_error"
    agent_version = next(
        (
            str(agent.details["version"])
            for agent in connection.agents
            if agent.details.get("version")
        ),
        None,
    )
    connected_at = connection.last_seen_at if status == "connected" else None
    return ClusterConnectStatusResponse(
        status=status,
        stage=stage,
        agent_version=agent_version,
        connected_at=connected_at,
        failure_reason=failure_reason,
    )


@router.put(gateway_routes.CLUSTER_POLICY_PATH)
async def update_cluster_policy(
    cluster_id: str,
    payload: AgentPolicy,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    if payload.cluster_id != cluster_id:
        raise HTTPException(
            status_code=409,
            detail="cluster_id does not match policy payload",
        )
    existing = db.get_cluster_policy(workspace_id, cluster_id)
    base_policy = (
        AgentPolicy.model_validate(existing)
        if existing
        else default_agent_policy(cluster_id=cluster_id)
    )
    registration_getter = getattr(db, "get_cluster_registration", None)
    registration = (
        registration_getter(workspace_id, cluster_id) if callable(registration_getter) else None
    )
    management_cluster = is_management_registration(registration) or is_management_role(
        base_policy.cluster_role
    )
    if management_cluster:
        if management_policy_update_is_forbidden(payload):
            raise HTTPException(status_code=400, detail=management_readonly_detail())
        base_policy = freeze_management_policy(base_policy)
    merged_policy = merge_agent_policy(base_policy, payload)
    if management_cluster:
        merged_policy = freeze_management_policy(merged_policy)
    elif registration:
        raw_settings = registration.get("settings")
        settings = raw_settings if isinstance(raw_settings, dict) else {}
        cluster_role = str(settings.get("cluster_role") or merged_policy.cluster_role)
        merged_policy = preserve_server_owned_evidence_queries(
            merged_policy,
            cluster_id=cluster_id,
            evidence_profile=evidence_profile_for_registration(
                cluster_role=cluster_role,
                environment=str(settings.get("environment") or ""),
                install_sample_workload=bool(settings.get("install_sample_workload")),
            ),
            control_namespaces=control_namespace_tuple(
                str(settings.get("control_namespaces") or "")
            ),
        )
    try:
        stored = db.upsert_cluster_policy(workspace_id, cluster_id, merged_policy.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"accepted": True, "policy": stored}


def cluster_policy_base(
    db: Any,
    workspace_id: str,
    cluster_id: str,
) -> tuple[AgentPolicy, dict[str, Any] | None, bool]:
    existing = db.get_cluster_policy(workspace_id, cluster_id)
    base_policy = (
        AgentPolicy.model_validate(existing)
        if existing
        else default_agent_policy(cluster_id=cluster_id)
    )
    registration_getter = getattr(db, "get_cluster_registration", None)
    registration = (
        registration_getter(workspace_id, cluster_id) if callable(registration_getter) else None
    )
    management_cluster = is_management_registration(registration) or is_management_role(
        base_policy.cluster_role
    )
    return base_policy, registration, management_cluster


@router.get(
    gateway_routes.CLUSTER_SCHEDULING_PROFILES_PATH,
    response_model=SchedulingPolicyResponse,
)
async def get_cluster_scheduling_profiles(
    cluster_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> SchedulingPolicyResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.CLUSTER_READ.value,
    )
    base_policy, _registration, _management_cluster = cluster_policy_base(
        db,
        workspace_id,
        cluster_id,
    )
    return SchedulingPolicyResponse(
        cluster_id=cluster_id,
        scheduling=base_policy.scheduling.model_dump(),
    )


@router.put(
    gateway_routes.CLUSTER_SCHEDULING_PROFILES_PATH,
    response_model=SchedulingPolicyResponse,
)
async def update_cluster_scheduling_profiles(
    cluster_id: str,
    payload: SchedulingPolicy,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> SchedulingPolicyResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    base_policy, _registration, management_cluster = cluster_policy_base(
        db,
        workspace_id,
        cluster_id,
    )
    if management_cluster:
        raise HTTPException(status_code=400, detail=management_readonly_detail())
    merged_policy = base_policy.model_copy(
        update={
            "generation": base_policy.generation + 1,
            "scheduling": payload,
        }
    )
    try:
        stored = db.upsert_cluster_policy(workspace_id, cluster_id, merged_policy.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    stored_policy = AgentPolicy.model_validate(stored)
    return SchedulingPolicyResponse(
        cluster_id=cluster_id,
        scheduling=stored_policy.scheduling.model_dump(),
    )


@router.delete(
    gateway_routes.CLUSTER_PATH,
    response_model=ClusterUnregisterResponse,
    status_code=202,
)
async def unregister_cluster(
    cluster_id: str,
    purge: bool = False,
    manual_cleanup_attested: bool = False,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> ClusterUnregisterResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    cleanup_resources = list(UNINSTALL_CLEANUP_RESOURCE_REFS)
    pending_residuals = list(FINAL_CLEANUP_RESOURCE_REFS)
    if purge:
        # 테스트 fixture 물리 삭제만 별도 UoW로 묶고 운영 soft-delete 경로는 그대로 둔다.
        with unit_of_work_or_null(db):
            registration = unregisterable_registration(db, workspace_id, cluster_id)
            require_test_fixture_purge_environment(registration)
            purge_registration = getattr(db, "purge_test_target_cluster_registration", None)
            if not callable(purge_registration):
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": TEST_FIXTURE_PURGE_UNSUPPORTED_CODE,
                        "detail": "테스트 fixture purge를 처리할 수 없습니다",
                    },
                )
            if not purge_registration(workspace_id, cluster_id):
                raise HTTPException(status_code=NOT_FOUND_CODE, detail=CLUSTER_NOT_FOUND)
        return ClusterUnregisterResponse(
            cluster_id=cluster_id,
            status="purged",
            stage="purged",
            cleanup_verified=True,
        )

    unregisterable_registration(db, workspace_id, cluster_id)
    if manual_cleanup_attested:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "manual_cleanup_attestation_unsupported",
                "detail": "등록 해제는 인증된 에이전트의 잔여 리소스 0 완료 증적이 필요합니다",
            },
        )
    agents = visible_cluster_agent_statuses(
        db.list_cluster_agent_statuses(workspace_id, cluster_id)
    )
    snapshot_getter = getattr(db, "latest_inventory_snapshot", None)
    latest_snapshot = (
        snapshot_getter(workspace_id, cluster_id) if callable(snapshot_getter) else None
    )
    if not agents and callable(snapshot_getter) and latest_snapshot is None:
        unregister = getattr(db, "unregister_target_cluster", None)
        if not callable(unregister):
            raise HTTPException(
                status_code=500,
                detail="cluster registration cleanup is unavailable",
            )
        with unit_of_work_or_null(db):
            if not unregister(workspace_id, cluster_id):
                raise HTTPException(status_code=NOT_FOUND_CODE, detail=CLUSTER_NOT_FOUND)
        return ClusterUnregisterResponse(
            cluster_id=cluster_id,
            status="disconnected",
            stage="registration_revoked",
            cleanup_verified=True,
            resources=cleanup_resources,
            residual_resources=[],
        )
    latest_agent = agents[0] if agents else None
    online = cluster_connection_status(latest_agent) == AGENT_STATUS_ONLINE
    status_updater = getattr(db, "update_cluster_registration_status", None)
    if callable(status_updater):
        status_updater(
            workspace_id,
            cluster_id,
            ClusterRegistrationStatus.UNINSTALL_REQUESTED.value,
        )
    queue = getattr(db, "queue_agent_command", None)
    if not callable(queue):
        return ClusterUnregisterResponse(
            cluster_id=cluster_id,
            status="cleanup_required",
            stage="agent_cleanup_pending",
            uninstall_command=UNINSTALL_COMMAND_REFERENCE,
            resources=cleanup_resources,
            residual_resources=pending_residuals,
            failure_reason="agent cleanup queue is unavailable",
        )
    try:
        queued = queue_agent_uninstall(
            db,
            cluster_id=cluster_id,
            workspace_id=workspace_id,
            requested_by=str(current.user_id),
        )
    except Exception as exc:
        return ClusterUnregisterResponse(
            cluster_id=cluster_id,
            status="cleanup_required",
            stage="agent_cleanup_pending",
            uninstall_command=UNINSTALL_COMMAND_REFERENCE,
            resources=cleanup_resources,
            residual_resources=pending_residuals,
            failure_reason=f"agent cleanup queue failed: {type(exc).__name__}",
        )
    if not queued.inserted:
        return ClusterUnregisterResponse(
            cluster_id=cluster_id,
            status="cleanup_required",
            stage="agent_cleanup_pending",
            command_id=queued.command_id,
            command_status_path=gateway_routes.COMMAND_STATUS_PATH.format(
                command_id=queued.command_id
            ),
            uninstall_command=UNINSTALL_COMMAND_REFERENCE,
            resources=cleanup_resources,
            residual_resources=pending_residuals,
            failure_reason="agent cleanup command is already pending",
        )
    return ClusterUnregisterResponse(
        cluster_id=cluster_id,
        status="uninstalling" if online else "cleanup_required",
        stage="agent_cleanup_queued" if online else "agent_cleanup_pending",
        command_id=queued.command_id,
        command_status_path=gateway_routes.COMMAND_STATUS_PATH.format(command_id=queued.command_id),
        uninstall_command=UNINSTALL_COMMAND_REFERENCE,
        resources=cleanup_resources,
        residual_resources=pending_residuals,
        failure_reason=None if online else "agent is offline; cleanup waits for agent reconnect",
    )


@agent_router.get(
    gateway_routes.AGENT_POLICY_PATH,
    response_model=AgentPolicyResponse,
)
async def agent_policy(
    cluster_id: str,
    generation: int = 0,
    identity: ClusterAgentIdentity = Depends(require_cluster_agent),
    db: Any = Depends(get_db),
) -> AgentPolicyResponse:
    if cluster_id != identity.cluster_id:
        raise HTTPException(status_code=403, detail="cluster_id does not match agent identity")
    stored = db.get_cluster_policy(identity.workspace_id, identity.cluster_id)
    if stored is None:
        return AgentPolicyResponse(policy=None)
    policy = AgentPolicy.model_validate(stored)
    registration_getter = getattr(db, "get_cluster_registration", None)
    registration = (
        registration_getter(identity.workspace_id, identity.cluster_id)
        if callable(registration_getter)
        else None
    )
    if is_management_registration(registration) or is_management_role(policy.cluster_role):
        refreshed = refresh_management_policy(policy)
        if refreshed.generation > policy.generation:
            try:
                stored = db.upsert_cluster_policy(
                    identity.workspace_id,
                    identity.cluster_id,
                    refreshed.model_dump(mode="json"),
                )
            except ValueError:
                stored = db.get_cluster_policy(identity.workspace_id, identity.cluster_id)
                if stored is None:
                    return AgentPolicyResponse(policy=None)
            policy = AgentPolicy.model_validate(stored)
    if policy.generation <= generation:
        return AgentPolicyResponse(policy=None)
    return AgentPolicyResponse(policy=policy)


@agent_router.post(gateway_routes.AGENT_POLICY_STATUS_PATH)
async def agent_policy_status(
    payload: AgentPolicyStatusRequest,
    identity: ClusterAgentIdentity = Depends(require_cluster_agent),
    db: Any = Depends(get_db),
) -> dict[str, bool]:
    status = payload.model_copy(update={"cluster_id": identity.cluster_id}).model_dump()
    db.save_agent_policy_status(identity.workspace_id, status)
    return {"accepted": True}


@agent_router.post(gateway_routes.AGENT_RECONCILE_STATUS_PATH)
async def agent_reconcile_status(
    payload: AgentReconcileStatusRequest,
    identity: ClusterAgentIdentity = Depends(require_cluster_agent),
    db: Any = Depends(get_db),
) -> dict[str, bool]:
    status = payload.model_copy(update={"cluster_id": identity.cluster_id}).model_dump()
    db.save_agent_reconcile_status(identity.workspace_id, status)
    return {"accepted": True}


@agent_router.post(
    gateway_routes.AGENT_EVIDENCE_JOB_SCHEDULE_PATH,
    response_model=EvidenceJobScheduleResponse,
)
async def schedule_evidence_jobs(
    payload: EvidenceJobScheduleRequest,
    identity: ClusterAgentIdentity = Depends(require_cluster_agent),
    db: Any = Depends(get_db),
) -> EvidenceJobScheduleResponse:
    stored_policy = await db_call(
        db.get_cluster_policy,
        identity.workspace_id,
        identity.cluster_id,
    )
    policy = (
        AgentPolicy.model_validate(stored_policy)
        if stored_policy
        else default_agent_policy(cluster_id=identity.cluster_id)
    )
    provider_keys = enabled_provider_keys(policy, payload.provider_keys)
    queued = await db_call(
        db.queue_evidence_jobs,
        workspace_id=identity.workspace_id,
        cluster_id=identity.cluster_id,
        source_id=payload.source_id,
        window_start=payload.window_start,
        provider_keys=provider_keys,
        failure_policy=policy.evidence.failure_policy,
        max_attempts=policy.evidence.max_attempts,
        policy_generation=policy.generation,
        provider_policies=provider_policy_snapshots(policy, provider_keys),
    )
    return EvidenceJobScheduleResponse(**queued)


async def lease_next_evidence_job(
    db: Any,
    cluster_id: str,
    workspace_id: str,
    provider_key: str,
    agent_id: str,
    timeout: int,
) -> dict[str, Any] | None:
    deadline = time.time() + min(timeout, MAX_EVIDENCE_JOB_POLL_SECONDS)
    while time.time() < deadline:
        row = await db.lease_evidence_job(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            provider_key=provider_key,
            agent_id=agent_id,
            lease_seconds=DEFAULT_EVIDENCE_JOB_LEASE_SECONDS,
        )
        if row:
            return row
        await asyncio.sleep(EVIDENCE_JOB_POLL_SLEEP_SECONDS)
    return None


@agent_router.get(
    gateway_routes.AGENT_EVIDENCE_JOB_POLL_PATH,
    response_model=EvidenceJobPollResponse,
)
async def poll_evidence_job(
    provider_key: str,
    agent_id: str = "target-agent",
    timeout: int = DEFAULT_EVIDENCE_JOB_POLL_SECONDS,
    identity: ClusterAgentIdentity = Depends(require_cluster_agent),
    db: Any = Depends(get_db),
) -> EvidenceJobPollResponse:
    await db_call(touch_agent_seen, db, identity, agent_id)
    job = await lease_next_evidence_job(
        db,
        identity.cluster_id,
        identity.workspace_id,
        provider_key,
        agent_id,
        timeout,
    )
    return EvidenceJobPollResponse(job=job)


async def emit_evidence_if_ready(
    evidence_key: str,
    events: Any,
    db: Any,
) -> EvidenceJobResultResponse | None:
    existing = await db_call(db.get_evidence_window, evidence_key)
    if existing:
        if str(existing["event_id"]).startswith(PENDING_EVIDENCE_EVENT_ID_PREFIX):
            if not await release_stale_pending_evidence_window(db, evidence_key):
                return None
        else:
            return EvidenceJobResultResponse(
                accepted=True,
                evidence_key=evidence_key,
                event_id=existing["event_id"],
                correlation_id=existing["correlation_id"],
            )

    payload = await db_call(db.evidence_payload_if_ready, evidence_key)
    if payload is None:
        return None

    evidence_body = ClusterEvidenceReceivedBody(**complete_evidence_payload(payload))
    correlation_id = payload.get("correlation_id")
    event_envelope = event(
        evidence_body.__subject__,
        getattr(events, "source", "api-gateway"),
        compact_cluster_evidence_payload(
            evidence_body,
            correlation_id if isinstance(correlation_id, str) else None,
        ),
        correlation_id if isinstance(correlation_id, str) else None,
    )
    recorded = await db_call(
        db.record_evidence_event_once,
        evidence_key=evidence_key,
        workspace_id=evidence_body.workspace_id,
        cluster_id=evidence_body.cluster_id,
        source_id=evidence_body.source_id or DEFAULT_EVIDENCE_SOURCE_ID,
        window_start=evidence_body.window_start or evidence_key,
        agent_id=evidence_body.agent_id,
        event_envelope=event_envelope,
        payload=evidence_body.to_body(),
    )
    if str(recorded["event_id"]).startswith(PENDING_EVIDENCE_EVENT_ID_PREFIX):
        return None
    return EvidenceJobResultResponse(
        accepted=True,
        evidence_key=evidence_key,
        event_id=recorded["event_id"],
        correlation_id=recorded["correlation_id"],
    )


def complete_evidence_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed_fields = {item.name for item in fields(ClusterEvidenceReceivedBody)}
    return {
        **{key: value for key, value in payload.items() if key in allowed_fields},
        "kubernetes": payload.get("kubernetes")
        if isinstance(payload.get("kubernetes"), dict)
        else {},
        "metrics": payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {},
        "logs": payload.get("logs") if isinstance(payload.get("logs"), list) else [],
        "traces": payload.get("traces") if isinstance(payload.get("traces"), dict) else {},
    }


@agent_router.post(
    gateway_routes.AGENT_EVIDENCE_JOB_RESULT_PATH,
    response_model=EvidenceJobResultResponse,
)
async def evidence_job_result(
    job_id: str,
    payload: EvidenceJobResultRequest,
    identity: ClusterAgentIdentity = Depends(require_cluster_agent),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    timeline_fanout: Any = Depends(get_timeline_fanout),
    dashboard_ready_fanout: Any = Depends(get_dashboard_ready_fanout),
) -> EvidenceJobResultResponse:
    result = await db_call(
        db.complete_evidence_job,
        workspace_id=identity.workspace_id,
        cluster_id=identity.cluster_id,
        job_id=job_id,
        lease_id=payload.lease_id,
        agent_id=payload.agent_id,
        status=payload.status,
        result=payload.result,
        error=payload.error,
    )
    if result is None:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=EVIDENCE_JOB_NOT_FOUND)

    await db_call(touch_agent_seen, db, identity, payload.agent_id)
    source_id = str(result.get("source_id") or "")
    if source_id == RELEASE_WORKFLOW_FAILURE_SOURCE_ID:
        await events.accept_body(
            EvidenceJobUpdatedBody(
                provider_key=str(result.get("provider_key") or ""),
                status=str(result.get("status") or payload.status),
                evidence_key=str(result["evidence_key"]),
                workspace_id=identity.workspace_id,
                cluster_id=identity.cluster_id,
                source_id=source_id,
                window_start=str(result.get("window_start") or "") or None,
                evidence_emitted=False,
                collection_status={
                    "job_id": job_id,
                    "reported_status": payload.status,
                    "stored_status": str(result.get("status") or payload.status),
                },
            )
        )
    kubernetes = payload.result.get("kubernetes")
    if payload.status == "completed" and isinstance(kubernetes, dict):
        await ingest_inventory_snapshot(
            db=db,
            workspace_id=identity.workspace_id,
            cluster_id=identity.cluster_id,
            agent_id=payload.agent_id,
            payload=kubernetes_evidence_to_inventory_snapshot(
                kubernetes,
                cluster_id=identity.cluster_id,
                agent_id=payload.agent_id,
            ),
            fanout=timeline_fanout,
            ready_fanout=dashboard_ready_fanout,
        )

    evidence_key = str(result["evidence_key"])
    emitted = await emit_evidence_if_ready(evidence_key, events, db)
    if emitted:
        return emitted
    return EvidenceJobResultResponse(accepted=True, evidence_key=evidence_key)


async def db_call(func: Any, *args: Any, **kwargs: Any) -> Any:
    return await to_thread_db_retry(func, *args, **kwargs)


async def release_stale_pending_evidence_window(db: Any, evidence_key: str) -> bool:
    return bool(
        await db_call(
            db.release_stale_pending_evidence_window,
            evidence_key,
            DEFAULT_PENDING_EVIDENCE_EVENT_TTL_SECONDS,
        )
    )


router.include_router(agent_router)
