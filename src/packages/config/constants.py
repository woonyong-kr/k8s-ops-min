from __future__ import annotations

from enum import StrEnum
from typing import Final


class Nats:
    DEFAULT_URL: Final[str] = "nats://nats:4222"


class Redis:
    DEFAULT_URL: Final[str] = "redis://redis:6379/0"


class Runtime:
    DEFAULT_SERVICE_NAME: Final[str] = "service"
    DEFAULT_HTTP_PORT: Final[str] = "8000"
    SERVICE_NAME_ENV: Final[str] = "SERVICE_NAME"


class Target:
    DEFAULT_CLUSTER_ID: Final[str] = "default-target-cluster"
    DEFAULT_EVIDENCE_INTERVAL_SECONDS: Final[str] = "30"


class Auth:
    LOCAL_USER_ID: Final[str] = "local-user"
    DEFAULT_SESSION_TTL_SECONDS: Final[str] = "7200"
    SESSION_TTL_ENV: Final[str] = "SESSION_TTL_SECONDS"
    SESSION_COOKIE_NAME: Final[str] = "service_session"
    # 세션 쿠키 httpOnly 설정으로 JS 토큰 접근 차단(XSS 탈취 차단). Secure 는 운영 기본 on,
    # 로컬 http 개발은 COOKIE_SECURE=0. SameSite=lax 로 CSRF 완화.
    COOKIE_SECURE_ENV: Final[str] = "COOKIE_SECURE"
    COOKIE_SAMESITE: Final[str] = "lax"


class GitHub:
    PROVIDER: Final[str] = "github"


class Command:
    DEFAULT_ACTION: Final[str] = "rollout_restart"
    APPLY_MANIFEST_ACTION: Final[str] = "apply_manifest"
    CATALOG_HELM_INSTALL_ACTION: Final[str] = "catalog.helm.install"
    CATALOG_HELM_INSTALL_CAPABILITY: Final[str] = "catalog_helm_install"
    CATALOG_HELM_UPGRADE_CAS_CAPABILITY: Final[str] = "catalog_helm_upgrade_cas.v1"
    DELETE_WORKLOAD_ACTION: Final[str] = "delete_workload"
    RCA_TEST_SCENARIO_INJECT_ACTION: Final[str] = "rca.test.inject"
    RCA_TEST_SCENARIO_CLEANUP_ACTION: Final[str] = "rca.test.cleanup"
    KUBERNETES_DEPLOYMENT_SCALE_ACTION: Final[str] = "k8s.apps.v1.deployments.scale"
    KUBERNETES_STATEFULSET_SCALE_ACTION: Final[str] = "k8s.apps.v1.statefulsets.scale"
    KUBERNETES_STATEFULSET_RESTART_ACTION: Final[str] = "k8s.apps.v1.statefulsets.restart"
    KUBERNETES_DAEMONSET_RESTART_ACTION: Final[str] = "k8s.apps.v1.daemonsets.restart"
    KUBERNETES_NODE_CORDON_ACTION: Final[str] = "k8s.core.v1.nodes.cordon"
    KUBERNETES_NODE_UNCORDON_ACTION: Final[str] = "k8s.core.v1.nodes.uncordon"
    KUBERNETES_NODE_DRAIN_ACTION: Final[str] = "k8s.core.v1.nodes.drain"
    KUBERNETES_POD_DEBUG_ACTION: Final[str] = "k8s.core.v1.pods.debug"
    KUBERNETES_NODE_DEBUG_ACTION: Final[str] = "k8s.core.v1.nodes.debug"
    KUBERNETES_NODE_DEBUG_CLEANUP_ACTION: Final[str] = "k8s.core.v1.nodes.debug.cleanup"
    KUBERNETES_NODE_CONTROL_CAPABILITY: Final[str] = "node_control.v1"
    KUBERNETES_DEBUG_CAPABILITY: Final[str] = "resource_debug.v1"
    KUBERNETES_CRONJOB_TRIGGER_ACTION: Final[str] = "k8s.batch.v1.cronjobs.trigger"
    KUBERNETES_CRONJOB_SUSPEND_ACTION: Final[str] = "k8s.batch.v1.cronjobs.suspend"
    KUBERNETES_CRONJOB_RESUME_ACTION: Final[str] = "k8s.batch.v1.cronjobs.resume"
    KUBERNETES_CRONJOB_CONTROL_CAPABILITY: Final[str] = "cronjob_control.v1"
    KUBERNETES_RESOURCE_DELETE_ACTION: Final[str] = "k8s.resource.delete"
    KUBERNETES_RESOURCE_DELETE_CAPABILITY: Final[str] = "resource_delete.v1"
    KUBERNETES_DEPLOYMENT_ROLLBACK_ACTION: Final[str] = "k8s.apps.v1.deployments.rollback"
    KUBERNETES_STATEFULSET_ROLLBACK_ACTION: Final[str] = "k8s.apps.v1.statefulsets.rollback"
    KUBERNETES_DAEMONSET_ROLLBACK_ACTION: Final[str] = "k8s.apps.v1.daemonsets.rollback"
    KUBERNETES_WORKLOAD_ROLLBACK_CAPABILITY: Final[str] = "workload_rollback.v1"
    GITOPS_RESOURCE_CONTROL_ACTION: Final[str] = "gitops.resource.control"
    GITOPS_RESOURCE_CONTROL_CAPABILITY: Final[str] = "gitops_control.v1"
    TELEMETRY_QUERY_RUN_ACTION: Final[str] = "telemetry.query.run"
    TRAFFIC_SOURCE_SELECT_ACTION: Final[str] = "traffic.source.select"
    TRAFFIC_SOURCE_CONNECT_ACTION: Final[str] = "traffic.source.connect"
    TRAFFIC_SOURCE_OBSERVER_CAPABILITY: Final[str] = "traffic_source_observer.v1"
    TRAFFIC_SOURCE_SELECT_CAPABILITY: Final[str] = "traffic_source_select.v1"
    TRAFFIC_SOURCE_CONNECT_CAPABILITY: Final[str] = "traffic_source_connect.v1"
    CLUSTER_AGENT_UNINSTALL_ACTION: Final[str] = "cluster.agent.uninstall"


RCA_TEST_COMMAND_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        Command.RCA_TEST_SCENARIO_INJECT_ACTION,
        Command.RCA_TEST_SCENARIO_CLEANUP_ACTION,
    }
)


class CommandStatus:
    # Final(타입 미지정) → mypy 가 Literal 로 추론 → Literal 필드(status)에 그대로 대입 가능.
    QUEUED: Final = "queued"
    LEASED: Final = "leased"
    RUNNING: Final = "running"
    CANCEL_REQUESTED: Final = "cancel_requested"
    CANCELLING: Final = "cancelling"
    COMPLETED: Final = "completed"
    FAILED: Final = "failed"
    CANCELLED: Final = "cancelled"


class RiskLevel(StrEnum):
    """diff 위험도 태그(생산자 gitops·소비자 command 공유) — wire 에는 값 문자열 그대로 실림."""

    SANDBOX_ONLY = "sandbox-only"  # sandbox 한정 변경 → 안전 판정 표식
    NON_SANDBOX_NAMESPACE = "non-sandbox-namespace"  # sandbox 밖 네임스페이스 → 검토 필요
    REVIEW_REQUIRED = "review-required"  # 렌더 상태상 사람 검토 필요


class Sandbox:
    NAMESPACE: Final[str] = "sandbox"
    # 기존 소비자 호환용 별칭 — 원본 정의는 RiskLevel 에 있음
    RISK_TAG: Final[RiskLevel] = RiskLevel.SANDBOX_ONLY
    UNSAFE_NAMESPACE_RISK_TAG: Final[RiskLevel] = RiskLevel.NON_SANDBOX_NAMESPACE
    # 변경 없음 판정 사유(생산자 gitops·소비자 command 공유 — 중복 정의 금지)
    NO_DIFF_REASON: Final[str] = "desired and actual images already match"
