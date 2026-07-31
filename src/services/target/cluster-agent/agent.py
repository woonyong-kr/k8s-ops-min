from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from commands import (
    AgentCommandRegistry,
    CommandContext,
    CommandResultOutbox,
    CommandResultRecord,
    GitOpsResourceCommandPayload,
    KubernetesApiClient,
    KubernetesCronJobPayload,
    KubernetesGetPayload,
    KubernetesNodeDebugCleanupPayload,
    KubernetesNodeDebugPayload,
    KubernetesNodeDrainPayload,
    KubernetesNodeSchedulingPayload,
    KubernetesPatchPayload,
    KubernetesPodDebugPayload,
    KubernetesScalePayload,
    KubernetesWorkloadRollbackPayload,
    command,
    cronjob_job_body,
    execute_gitops_resource_command,
    rollback_template_from_revision,
    validate_cronjob_resource_ref,
    validate_exact_resource,
    workload_template_sha256,
)
from commands.helm import (
    run_catalog_helm_install,
    run_helm_artifact_query,
    run_helm_release_operation,
    run_helm_values_preview,
    validate_catalog_helm_upgrade_secret,
    validate_helm_release_operation_secret,
    validate_helm_release_secret,
)
from commands.resource_files import ResourceFileExecutor
from commands.service_access import (
    ServiceAccessExecutionError,
    ServiceRequestCancelled,
    execute_service_http_request,
)
from control import (
    AgentControlStore,
    AgentPolicySync,
    DesiredStateReconciler,
    KubernetesArgoObserver,
)
from evidence import EvidenceCollector, EvidenceJobScheduler
from kubernetes_api import (
    kubernetes_api_base_url,
    kubernetes_client,
    kubernetes_headers,
    service_account_token,
)
from live_summary import LiveSummaryPublisher
from node_collector_manager import NodeCollectorManager
from port_forward_stream import KubernetesTcpTargetResolver, PortForwardController
from providers import (
    KubernetesSnapshotProvider,
    LokiLogsProvider,
    MetadataProvider,
    PrometheusMetricsProvider,
    TelemetryProvider,
    TempoTracesProvider,
)
from pydantic import Field, model_validator
from queries import (
    KubernetesSnapshotQuery,
    TelemetryQueryCommandPayload,
    TelemetryQueryDefinition,
    TelemetryQueryRegistry,
    compile_policy_query_definition,
)
from span import configure_tracing
from telemetry_registry import telemetry
from terminal_exec import PodExecController
from traffic_sources import TrafficSourceDetector

import config as agent_config
from config import (
    AGENT_CONTROL_DB_PATH_ENV,
    AGENT_DIRECT_COMMANDS_ENABLED_ENV,
    BOOTSTRAP_MODE_ENV,
    CLUSTER_ROLE_ENV,
    COMMAND_OUTBOX_DB_PATH_ENV,
    COMMAND_OUTBOX_FLUSH_INTERVAL_SECONDS,
    COMMAND_OUTBOX_MAX_ATTEMPTS,
    DEFAULT_AGENT_CONTROL_DB_PATH,
    DEFAULT_AGENT_DIRECT_COMMANDS_ENABLED,
    DEFAULT_BOOTSTRAP_MODE,
    DEFAULT_CLUSTER_ROLE,
    DEFAULT_COMMAND_OUTBOX_DB_PATH,
    DEFAULT_EVIDENCE_FAILURE_POLICY,
    DEFAULT_EVIDENCE_PROVIDER_MAX_WORKERS,
    DEFAULT_EVIDENCE_PROVIDER_WORKERS,
    DEFAULT_NODE_CONTROL_ENABLED,
    DEFAULT_OTEL_SERVICE_NAME,
    DEFAULT_OTEL_TRACES_ENDPOINT,
    DEFAULT_POLICY_SYNC_INTERVAL_SECONDS,
    DEFAULT_RECONCILE_INTERVAL_SECONDS,
    DEFAULT_RECONCILER_MODE,
    EVIDENCE_FAILURE_POLICY_ENV,
    EVIDENCE_PROVIDER_MAX_WORKERS_ENV,
    EVIDENCE_PROVIDER_WORKERS_ENV,
    KUBERNETES_CONFIGMAP_PATCH_ACTION,
    KUBERNETES_DEPLOYMENT_PATCH_ACTION,
    KUBERNETES_DEPLOYMENT_SCALE_ACTION,
    NODE_CONTROL_ENABLED_ENV,
    OTEL_SERVICE_NAME_ENV,
    OTEL_TRACES_ENDPOINT_ENV,
    POLICY_SYNC_INTERVAL_ENV,
    PROMETHEUS_PROBE_MAX_ATTEMPTS,
    QUERY_RUN_ACTION,
    RECONCILE_INTERVAL_ENV,
    RECONCILER_MODE_ARGOCD,
    RECONCILER_MODE_ENV,
)
from config import (
    KUBERNETES_AWAIT_ROLLOUT as CONFIG_KUBERNETES_AWAIT_ROLLOUT,
)
from config import (
    KUBERNETES_ROLLOUT_POLL_INTERVAL_SECONDS as CONFIG_KUBERNETES_ROLLOUT_POLL_INTERVAL_SECONDS,
)
from config import (
    KUBERNETES_ROLLOUT_TIMEOUT_SECONDS as CONFIG_KUBERNETES_ROLLOUT_TIMEOUT_SECONDS,
)
from domains.catalog.install import CatalogHelmInstallPayload
from domains.command.actions import command_action_spec
from domains.rca.test_scenario_adapters import (
    RcaTestCleanupPlan,
    default_test_scenario_adapter_registry,
)
from domains.rca.test_scenario_contract import (
    TestScenarioContractError,
    validate_scenario_adapter_contracts,
)
from domains.rca.test_scenario_kubernetes import (
    RCA_TEST_EXPIRES_AT_ANNOTATION,
    RCA_TEST_RUN_ANNOTATION,
    RCA_TEST_RUN_LABEL,
    rca_test_fixture_owned_by_run,
    validate_rca_test_fixture_target,
)
from domains.rca.test_scenarios import (
    RcaTestScenario,
    test_scenario_by_id,
)
from domains.target.management_guard import MANAGEMENT_CLUSTER_ROLE, MANAGEMENT_READONLY_CODE
from domains.target.uninstall import (
    FINAL_CASCADE_CLUSTER_CLEANUP,
    FINAL_CASCADE_NAMESPACED_CLEANUP,
    FINAL_UNINSTALL_CLUSTER_ROLE,
    PRE_ACK_CLUSTER_CLEANUP,
    PRE_ACK_NAMESPACED_CLEANUP,
    UNINSTALL_CLEANUP_RESOURCE_REFS,
    UNINSTALL_CONTRACT_VERSION,
)
from packages.config.constants import (
    RCA_TEST_COMMAND_ACTIONS,
    Command,
    CommandStatus,
    Sandbox,
    Target,
)
from packages.config.control import (
    CONTROL_NAMESPACE_DENIED_MESSAGE,
    control_namespace_allowed,
)
from packages.config.environments import is_sandbox_environment, normalize_environment
from packages.config.logs import CONTEXT_KEY, get_logger
from packages.config.security import RCA_TEST_RUNS_DISABLED_MESSAGE, rca_test_runs_enabled
from packages.config.settings import env
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.fields import Gateway
from packages.contracts.gateway.requests import (
    DEFAULT_QUEUE_AGE_TARGET_SECONDS,
    AgentPolicy,
    BootstrapPolicy,
    DesiredStatePolicy,
    EvidenceProviderPolicy,
    EvidenceRuntimePolicy,
    StrictModel,
)
from packages.contracts.gitops import supported_kubernetes_resource
from packages.contracts.helm import (
    HELM_RELEASE_ARTIFACT_READ_ACTION,
    HELM_RELEASE_OPERATION_ACTION,
    HELM_VALUES_PREVIEW_ACTION,
    HelmArtifactCommandPayload,
    HelmReleaseOperationCommandPayload,
    HelmValuesPreviewCommandPayload,
)
from packages.contracts.identity import DEFAULT_WORKSPACE_ID
from packages.contracts.integrations import (
    AgentPrometheusIntegrationConfig,
    AgentPrometheusIntegrationEnvelope,
)
from packages.contracts.interfaces import CommandRecord, ManagementPlaneClient
from packages.contracts.resource_files import (
    RESOURCE_FILE_ACTION,
    ResourceFileCommandPayload,
)
from packages.contracts.service_access import (
    SERVICE_HTTP_REQUEST_ACTION,
    ServiceHttpRequestCommandPayload,
)
from packages.contracts.target import (
    TARGET_RBAC_MANIFEST_VERSION,
    TARGET_RBAC_VERSION_ANNOTATION,
)
from packages.contracts.traffic.control import TrafficSourceAgentCommandPayload
from packages.security.credentials import (
    CredentialEncryptionError,
    agent_envelope_context,
    open_agent_payload,
)

LOGGER = get_logger(__name__)
COMMAND_OUTPUT_LIMIT = 2000
SENSITIVE_OUTPUT_MARKERS = (
    "authorization",
    "bearer ",
    "kubeconfig",
    "password",
    "secret",
    "token",
)
RCA_TEST_CLEANUP_INTERVAL_SECONDS = 30
RCA_TEST_CLEANUP_TIMEOUT_SECONDS = 30.0
RCA_TEST_CLEANUP_POLL_SECONDS = 0.25
RCA_TEST_OWNER_CONFLICT_STATUSES = frozenset({409, 422})


def parse_provider_worker_counts(raw_counts: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for part in raw_counts.split(","):
        item = part.strip()
        if not item:
            continue
        provider_key, separator, raw_count = item.partition("=")
        if not separator:
            raise ValueError(f"invalid provider worker setting: {item}")
        counts[provider_key.strip()] = max(1, int(raw_count.strip()))
    return counts


@dataclass(frozen=True)
class KubernetesManifestResource:
    kind: str
    api_version: str
    namespace: str
    name: str
    plural: str
    api_prefix: str
    manifest: JsonObject

    def collection_url(self, base_url: str) -> str:
        return f"{base_url}{self.api_prefix}/namespaces/{self.namespace}/{self.plural}"

    def resource_url(self, base_url: str) -> str:
        return f"{self.collection_url(base_url)}/{self.name}"


@dataclass(frozen=True)
class RcaTestOwnedResource:
    kind: str
    url: str
    uid: str
    resource_version: str


class RcaTestFixtureOwnershipChanged(RuntimeError):
    """cleanup 대상 이름이 다른 run 소유로 바뀐 안전한 경합."""


class ClusterAgentUninstallPayload(StrictModel):
    cluster_id: str
    contract_version: int


class ExactResourceDeleteTarget(StrictModel):
    api_group: str = Field(max_length=253)
    version: str = Field(min_length=1, max_length=80)
    kind: str = Field(min_length=1, max_length=120)
    namespace: str | None = Field(default=None, max_length=253)
    name: str = Field(min_length=1, max_length=253)
    uid: str = Field(min_length=1, max_length=253)
    resource_version: str = Field(min_length=1, max_length=253)
    plural: str = Field(min_length=1, max_length=253, pattern=r"^[a-z0-9.-]+$")


class ResourceDeleteCommandPayload(StrictModel):
    resources: list[ExactResourceDeleteTarget] = Field(min_length=1, max_length=20)
    propagation_policy: str = Field(pattern=r"^Foreground$")
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    cascade: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unique_targets(self) -> ResourceDeleteCommandPayload:
        identities = [
            (
                item.api_group,
                item.version,
                item.kind.casefold(),
                item.namespace,
                item.name,
                item.uid,
            )
            for item in self.resources
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("resource delete targets must be unique")
        return self


def agent_version_from_image(image_ref: str) -> str | None:
    """이미지 참조에서 사람이 읽을 버전 표식(태그 또는 다이제스트 축약)을 뽑는다.

    값을 만들어내지 않는다 — 참조가 비었거나 태그가 없으면 None(미관측)이다.
    """
    ref = image_ref.strip()
    if not ref:
        return None
    if "@sha256:" in ref:
        return ref.rsplit("@sha256:", 1)[1][:12]
    tail = ref.rsplit("/", 1)[-1]
    if ":" in tail:
        return tail.rsplit(":", 1)[1]
    return None


class AgentConfig:
    TARGET_AGENT_SERVICE_NAME = "cluster-agent"

    DEFAULT_MANAGEMENT_BASE_URL = ""
    MANAGEMENT_BASE_URL_ENV = "MANAGEMENT_BASE_URL"
    TARGET_CLUSTER_ID_ENV = "TARGET_CLUSTER_ID"
    WORKSPACE_ID_ENV = "WORKSPACE_ID"
    EVIDENCE_INTERVAL_ENV = "EVIDENCE_INTERVAL_SECONDS"
    AGENT_TOKEN_ENV = "AGENT_TOKEN"
    AGENT_TOKEN_HEADER = "x-agent-token"
    AGENT_ENVELOPE_PRIVATE_KEY_ENV = agent_config.AGENT_ENVELOPE_PRIVATE_KEY_ENV
    # 타이밍 튜닝값은 config 모듈이 단일 원천(env 오버라이드 가능) — 중복 리터럴 금지
    HTTP_TIMEOUT_SECONDS = agent_config.HTTP_TIMEOUT_SECONDS
    COMMAND_POLL_TIMEOUT_SECONDS = agent_config.COMMAND_POLL_TIMEOUT_SECONDS
    COMMAND_HEARTBEAT_INTERVAL_SECONDS = agent_config.COMMAND_HEARTBEAT_INTERVAL_SECONDS
    COMMAND_EXECUTION_DELAY_SECONDS = agent_config.COMMAND_EXECUTION_DELAY_SECONDS
    REGISTER_RETRY_DELAY_SECONDS = agent_config.REGISTER_RETRY_DELAY_SECONDS
    COMMAND_RETRY_DELAY_SECONDS = agent_config.COMMAND_RETRY_DELAY_SECONDS
    TRAFFIC_SOURCE_OBSERVATION_INTERVAL_SECONDS = (
        agent_config.TRAFFIC_SOURCE_OBSERVATION_INTERVAL_SECONDS
    )

    HOSTNAME_ENV = "HOSTNAME"
    DEFAULT_AGENT_ID = "target-agent"
    AGENT_CAPABILITIES = list(agent_config.AGENT_CAPABILITIES)
    EVIDENCE_SOURCE_ID = "cluster-snapshot"
    NODE_COLLECTOR_RECONCILE_INTERVAL_SECONDS = (
        agent_config.NODE_COLLECTOR_RECONCILE_INTERVAL_SECONDS
    )

    COMMAND_COMPLETED_STATUS = CommandStatus.COMPLETED
    COMMAND_FAILED_STATUS = CommandStatus.FAILED
    APPLY_MANIFEST_ACTION = Command.APPLY_MANIFEST_ACTION
    MANIFEST_CREATE_FIELD_MANAGER = "opsia-resource-create"
    ROLLOUT_RESTART_ACTION = Command.DEFAULT_ACTION
    COMMAND_RESULT_MESSAGE = "Kubernetes action processed in sandbox namespace"
    MANIFEST_CREATED_MESSAGE = "Kubernetes manifest created in sandbox namespace"
    MANIFEST_PATCHED_MESSAGE = "Kubernetes manifest patched in sandbox namespace"
    DEPLOYMENT_ROLLOUT_COMPLETED_MESSAGE = "Kubernetes deployment rollout completed"
    # 제어 허용 네임스페이스는 packages.config.control 단일 기준(CONTROL_ALLOWED_NAMESPACES).
    WRITE_NAMESPACE_DENIED_MESSAGE = CONTROL_NAMESPACE_DENIED_MESSAGE
    MISSING_APPROVAL_EVIDENCE_MESSAGE = (
        "write command requires approval_ref, policy_decision_ref, approval_decided_by, "
        "and approval_expires_at"
    )
    INVALID_APPROVAL_EVIDENCE_MESSAGE = "write command approval_expires_at is invalid"
    EXPIRED_APPROVAL_EVIDENCE_MESSAGE = "write command approval_expires_at is expired"
    DIRECT_COMMANDS_DISABLED_MESSAGE = "direct commands are disabled by agent profile"
    KUBERNETES_ROLLOUT_TIMEOUT_SECONDS = CONFIG_KUBERNETES_ROLLOUT_TIMEOUT_SECONDS
    KUBERNETES_ROLLOUT_POLL_INTERVAL_SECONDS = CONFIG_KUBERNETES_ROLLOUT_POLL_INTERVAL_SECONDS
    KUBERNETES_AWAIT_ROLLOUT = CONFIG_KUBERNETES_AWAIT_ROLLOUT


class HttpManagementPlaneClient:
    def __init__(
        self, base_url: str, timeout_seconds: int = AgentConfig.HTTP_TIMEOUT_SECONDS
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout_seconds)
        self.headers = {AgentConfig.AGENT_TOKEN_HEADER: env(AgentConfig.AGENT_TOKEN_ENV, "")}
        self.workspace_id = env(AgentConfig.WORKSPACE_ID_ENV, DEFAULT_WORKSPACE_ID)
        self.agent_envelope_private_key = env(
            AgentConfig.AGENT_ENVELOPE_PRIVATE_KEY_ENV,
            "",
        )

    async def __aenter__(self) -> HttpManagementPlaneClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self.client.aclose()

    async def register_agent(self, cluster_id: str, agent_id: str, capabilities: list[str]) -> None:
        payload: JsonObject = {
            Gateway.CLUSTER_ID: cluster_id,
            Gateway.AGENT_ID: agent_id,
            Gateway.CAPABILITIES: capabilities,
        }
        # 실측 버전 보고 — 설치 ConfigMap 이 주입한 자기 이미지 참조(TARGET_AGENT_IMAGE)
        # 의 태그/다이제스트가 이 프로세스의 유일한 진짜 버전 사실이다. 서버는 agent
        # details.version 을 이미 읽고 있고(save_cluster_agent_status 가 JSONB 병합
        # 저장이라 이후 traffic_sources 보고에도 유지됨), 값이 없으면 지어내지 않는다.
        version = agent_version_from_image(env("TARGET_AGENT_IMAGE", ""))
        if version:
            payload["details"] = {"version": version}
        response = await self.client.post(
            f"{self.base_url}{gateway_routes.AGENT_CONNECT_PATH}",
            json=payload,
            headers=self.headers,
        )
        response.raise_for_status()

    async def report_agent_status(
        self,
        cluster_id: str,
        agent_id: str,
        capabilities: list[str],
        details: JsonObject,
    ) -> None:
        response = await self.client.post(
            f"{self.base_url}{gateway_routes.AGENT_CONNECT_PATH}",
            json={
                Gateway.CLUSTER_ID: cluster_id,
                Gateway.AGENT_ID: agent_id,
                Gateway.CAPABILITIES: capabilities,
                "details": details,
            },
            headers=self.headers,
        )
        response.raise_for_status()

    async def poll_command(
        self, cluster_id: str, workspace_id: str, agent_id: str, timeout_seconds: int
    ) -> CommandRecord | None:
        response = await self.client.get(
            f"{self.base_url}{gateway_routes.AGENT_COMMAND_POLL_PATH}",
            params={
                Gateway.CLUSTER_ID: cluster_id,
                Gateway.WORKSPACE_ID: workspace_id,
                Gateway.AGENT_ID: agent_id,
                "timeout": timeout_seconds,
            },
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json().get(Gateway.COMMAND)

    async def start_command(
        self,
        command_id: str,
        cluster_id: str,
        workspace_id: str,
        lease_id: str,
        agent_id: str,
        attempt_id: str | None = None,
    ) -> None:
        response = await self.client.post(
            f"{self.base_url}{gateway_routes.agent_command_start_path(command_id)}",
            json={
                Gateway.CLUSTER_ID: cluster_id,
                Gateway.WORKSPACE_ID: workspace_id,
                Gateway.AGENT_ID: agent_id,
                Gateway.LEASE_ID: lease_id,
                **({Gateway.ATTEMPT_ID: attempt_id} if attempt_id else {}),
            },
            headers=self.headers,
        )
        response.raise_for_status()

    async def heartbeat_command(
        self,
        command_id: str,
        cluster_id: str,
        workspace_id: str,
        lease_id: str,
        agent_id: str,
        attempt_id: str | None = None,
        observed_cancel_generation: int | None = None,
        progress: JsonObject | None = None,
    ) -> JsonObject:
        response = await self.client.post(
            f"{self.base_url}{gateway_routes.agent_command_heartbeat_path(command_id)}",
            json={
                Gateway.CLUSTER_ID: cluster_id,
                Gateway.WORKSPACE_ID: workspace_id,
                Gateway.AGENT_ID: agent_id,
                Gateway.LEASE_ID: lease_id,
                **({Gateway.ATTEMPT_ID: attempt_id} if attempt_id else {}),
                **(
                    {Gateway.CANCEL_GENERATION: observed_cancel_generation}
                    if observed_cancel_generation is not None
                    else {}
                ),
                **({"progress": progress} if progress is not None else {}),
            },
            headers=self.headers,
        )
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}

    async def complete_command(
        self,
        command_id: str,
        workspace_id: str,
        lease_id: str,
        agent_id: str,
        result: JsonObject,
        attempt_id: str | None = None,
    ) -> None:
        response = await self.client.post(
            f"{self.base_url}{gateway_routes.agent_command_result_path(command_id)}",
            json={
                **result,
                Gateway.WORKSPACE_ID: workspace_id,
                Gateway.AGENT_ID: agent_id,
                Gateway.LEASE_ID: lease_id,
                **({Gateway.ATTEMPT_ID: attempt_id} if attempt_id else {}),
            },
            headers=self.headers,
        )
        response.raise_for_status()

    async def schedule_evidence_jobs(
        self,
        source_id: str,
        window_start: str,
        provider_keys: list[str],
    ) -> JsonObject:
        response = await self.client.post(
            f"{self.base_url}{gateway_routes.AGENT_EVIDENCE_JOB_SCHEDULE_PATH}",
            json={
                Gateway.SOURCE_ID: source_id,
                Gateway.WINDOW_START: window_start,
                Gateway.PROVIDER_KEYS: provider_keys,
            },
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()

    async def poll_evidence_job(
        self,
        provider_key: str,
        agent_id: str,
        timeout_seconds: int,
    ) -> JsonObject | None:
        response = await self.client.get(
            f"{self.base_url}{gateway_routes.AGENT_EVIDENCE_JOB_POLL_PATH}",
            params={
                Gateway.PROVIDER_KEY: provider_key,
                Gateway.AGENT_ID: agent_id,
                "timeout": timeout_seconds,
            },
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json().get(Gateway.JOB)

    async def complete_evidence_job(
        self,
        job_id: str,
        agent_id: str,
        lease_id: str,
        status: str,
        result: JsonObject,
        error: str,
    ) -> JsonObject:
        response = await self.client.post(
            f"{self.base_url}{gateway_routes.agent_evidence_job_result_path(job_id)}",
            json={
                Gateway.AGENT_ID: agent_id,
                Gateway.LEASE_ID: lease_id,
                Gateway.STATUS: status,
                Gateway.RESULT: result,
                Gateway.ERROR: error,
            },
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()

    async def record_inventory_snapshot(self, payload: JsonObject) -> JsonObject:
        response = await self.client.post(
            f"{self.base_url}{gateway_routes.AGENT_INVENTORY_SNAPSHOTS_PATH}",
            json=payload,
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()

    async def fetch_policy(self, cluster_id: str, generation: int) -> JsonObject | None:
        response = await self.client.get(
            f"{self.base_url}{gateway_routes.AGENT_POLICY_PATH}",
            params={Gateway.CLUSTER_ID: cluster_id, "generation": generation},
            headers=self.headers,
        )
        response.raise_for_status()
        policy = response.json().get("policy")
        return policy if isinstance(policy, dict) else None

    async def report_policy_status(self, status: JsonObject) -> None:
        response = await self.client.post(
            f"{self.base_url}{gateway_routes.AGENT_POLICY_STATUS_PATH}",
            json=status,
            headers=self.headers,
        )
        response.raise_for_status()

    async def fetch_prometheus_integration(self, revision: str) -> JsonObject:
        response = await self.client.get(
            f"{self.base_url}{gateway_routes.AGENT_PROMETHEUS_INTEGRATION_PATH}",
            params={"revision": revision},
            headers=self.headers,
        )
        response.raise_for_status()
        envelope = AgentPrometheusIntegrationEnvelope.model_validate(response.json())
        try:
            context = agent_envelope_context(
                self.workspace_id,
                envelope.cluster_id,
                envelope.revision,
                envelope.operation_id,
                envelope.address,
            )
            secret = open_agent_payload(
                envelope.sealed_headers,
                self.agent_envelope_private_key,
                context,
            )
        except CredentialEncryptionError as exc:
            raise RuntimeError("prometheus integration envelope is invalid") from exc
        headers = secret.get("headers")
        if not isinstance(headers, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
        ):
            raise RuntimeError("prometheus integration envelope is invalid")
        return AgentPrometheusIntegrationConfig(
            cluster_id=envelope.cluster_id,
            revision=envelope.revision,
            operation_id=envelope.operation_id,
            address=envelope.address,
            headers=dict(headers),
        ).model_dump()

    async def report_prometheus_integration_status(self, status: JsonObject) -> None:
        response = await self.client.post(
            f"{self.base_url}{gateway_routes.AGENT_PROMETHEUS_INTEGRATION_STATUS_PATH}",
            json=status,
            headers=self.headers,
        )
        response.raise_for_status()

    async def report_reconcile_status(self, status: JsonObject) -> None:
        response = await self.client.post(
            f"{self.base_url}{gateway_routes.AGENT_RECONCILE_STATUS_PATH}",
            json=status,
            headers=self.headers,
        )
        response.raise_for_status()


class PrometheusRuntimeConfigurationError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def prometheus_transport_error_retryable(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 429} or exc.response.status_code >= 500
    return False


class TargetClusterAgent:
    def __init__(
        self,
        client: ManagementPlaneClient | None = None,
        providers: tuple[TelemetryProvider, ...] | None = None,
        telemetry_transport: httpx.AsyncBaseTransport | None = None,
        kubernetes_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = env(
            AgentConfig.MANAGEMENT_BASE_URL_ENV, AgentConfig.DEFAULT_MANAGEMENT_BASE_URL
        ).rstrip("/")
        if not self.base_url:
            raise RuntimeError(f"{AgentConfig.MANAGEMENT_BASE_URL_ENV} is required")
        self.cluster_id = env(AgentConfig.TARGET_CLUSTER_ID_ENV, Target.DEFAULT_CLUSTER_ID)
        self.workspace_id = env(AgentConfig.WORKSPACE_ID_ENV, DEFAULT_WORKSPACE_ID)
        self.agent_id = env(AgentConfig.HOSTNAME_ENV, AgentConfig.DEFAULT_AGENT_ID)
        self.interval = int(
            env(AgentConfig.EVIDENCE_INTERVAL_ENV, Target.DEFAULT_EVIDENCE_INTERVAL_SECONDS)
        )
        self.cluster_role = env(CLUSTER_ROLE_ENV, DEFAULT_CLUSTER_ROLE)
        self.bootstrap_mode = env(BOOTSTRAP_MODE_ENV, DEFAULT_BOOTSTRAP_MODE)
        self.otel_service_name = env(OTEL_SERVICE_NAME_ENV, DEFAULT_OTEL_SERVICE_NAME)
        self.otel_traces_endpoint = env(OTEL_TRACES_ENDPOINT_ENV, DEFAULT_OTEL_TRACES_ENDPOINT)
        self.tracer = configure_tracing(self.otel_service_name, self.otel_traces_endpoint)
        self.evidence_provider_worker_counts = parse_provider_worker_counts(
            env(EVIDENCE_PROVIDER_WORKERS_ENV, DEFAULT_EVIDENCE_PROVIDER_WORKERS)
        )
        self.evidence_provider_max_worker_counts = parse_provider_worker_counts(
            env(EVIDENCE_PROVIDER_MAX_WORKERS_ENV, DEFAULT_EVIDENCE_PROVIDER_MAX_WORKERS)
        )
        self.evidence_failure_policy = env(
            EVIDENCE_FAILURE_POLICY_ENV,
            DEFAULT_EVIDENCE_FAILURE_POLICY,
        )
        self.agent_control_db_path = env(
            AGENT_CONTROL_DB_PATH_ENV,
            DEFAULT_AGENT_CONTROL_DB_PATH,
        )
        self.command_outbox_db_path = env(
            COMMAND_OUTBOX_DB_PATH_ENV,
            DEFAULT_COMMAND_OUTBOX_DB_PATH,
        )
        self.policy_sync_interval_seconds = int(
            env(POLICY_SYNC_INTERVAL_ENV, DEFAULT_POLICY_SYNC_INTERVAL_SECONDS)
        )
        self.reconcile_interval_seconds = int(
            env(RECONCILE_INTERVAL_ENV, DEFAULT_RECONCILE_INTERVAL_SECONDS)
        )
        self.reconciler_mode = env(RECONCILER_MODE_ENV, DEFAULT_RECONCILER_MODE).strip().lower()
        self.direct_commands_enabled = env(
            AGENT_DIRECT_COMMANDS_ENABLED_ENV,
            DEFAULT_AGENT_DIRECT_COMMANDS_ENABLED,
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.node_control_enabled = env(
            NODE_CONTROL_ENABLED_ENV,
            DEFAULT_NODE_CONTROL_ENABLED,
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.client = client
        self.telemetry_transport = telemetry_transport
        self.kubernetes_transport = kubernetes_transport
        self.node_collector = NodeCollectorManager.from_env(kubernetes_transport)
        # realtime live summary — outbound WS 1개(browser fan-out 은 realtime-gateway 책임)
        self.live_summary = LiveSummaryPublisher.from_env(
            cluster_id=self.cluster_id,
            management_base_url=self.base_url,
            kubernetes_transport=kubernetes_transport,
            terminal_controller=PodExecController(),
            port_forward_controller=PortForwardController(
                resolver=KubernetesTcpTargetResolver(kubernetes_transport)
            ),
        )
        if providers is None:
            providers = (
                KubernetesSnapshotProvider(
                    cluster_id=self.cluster_id,
                    transport=kubernetes_transport,
                ),
                LokiLogsProvider.from_config(env),
                TempoTracesProvider.from_config(env),
                MetadataProvider.from_config(env),
            )
        self.query_registry = TelemetryQueryRegistry()
        self.evidence_collector = EvidenceCollector(providers, self.query_registry)
        self.prometheus_integration_status: JsonObject = {
            "state": "unconfigured",
            "revision": None,
            "operation_id": None,
            "error_code": None,
        }
        self.prometheus_probe_attempts: dict[str, int] = {}
        self.control_store = AgentControlStore(self.agent_control_db_path)
        self.command_outbox = CommandResultOutbox(self.command_outbox_db_path)
        self.evidence_scheduler = EvidenceJobScheduler(
            cluster_id=self.cluster_id,
            workspace_id=self.workspace_id,
            agent_id=self.agent_id,
            source_id=AgentConfig.EVIDENCE_SOURCE_ID,
            collector=self.evidence_collector,
            provider_keys=tuple(self.evidence_collector.providers),
            provider_worker_counts=self.evidence_provider_worker_counts,
            interval_seconds=self.interval,
        )
        self.default_policy = self.build_default_policy()
        self.policy_sync = AgentPolicySync(
            cluster_id=self.cluster_id,
            store=self.control_store,
            default_policy=self.default_policy,
            apply_policy=self.apply_policy,
            interval_seconds=self.policy_sync_interval_seconds,
            status_details=self.policy_status_details,
            apply_runtime_configuration=self.apply_runtime_configurations,
        )
        self.reconciler = DesiredStateReconciler(
            cluster_id=self.cluster_id,
            cluster_role=self.cluster_role,
            store=self.control_store,
            interval_seconds=self.reconcile_interval_seconds,
            reconciler_mode=self.reconciler_mode,
            argo_observer=(
                KubernetesArgoObserver(transport=kubernetes_transport)
                if self.reconciler_mode == RECONCILER_MODE_ARGOCD
                else None
            ),
        )
        self.kubernetes = KubernetesApiClient()
        self.traffic_source_detector = TrafficSourceDetector(self.kubernetes)
        self.resource_file_executor = ResourceFileExecutor()
        self.command_registry = AgentCommandRegistry.from_instance(
            self,
            cluster_id=self.cluster_id,
            cluster_role=self.cluster_role,
            kubernetes=self.kubernetes,
            default_handler=self.apply_default_command,
        )

    def close(self) -> None:
        for store in (self.control_store, self.command_outbox):
            with suppress(Exception):
                store.close()

    def build_default_policy(self) -> AgentPolicy:
        providers = {
            provider_key: EvidenceProviderPolicy(
                enabled=(
                    self.cluster_role != MANAGEMENT_CLUSTER_ROLE or provider_key == "kubernetes"
                ),
                interval_seconds=self.interval,
                min_workers=self.evidence_provider_worker_counts.get(provider_key, 1),
                max_workers=self.evidence_provider_max_worker_counts.get(provider_key, 3),
                queue_age_target_seconds=DEFAULT_QUEUE_AGE_TARGET_SECONDS,
            )
            for provider_key in self.evidence_collector.providers
        }
        return AgentPolicy(
            cluster_id=self.cluster_id,
            cluster_role=self.cluster_role,
            evidence=EvidenceRuntimePolicy(
                profile=(
                    "management" if self.cluster_role == MANAGEMENT_CLUSTER_ROLE else "standard"
                ),
                failure_policy=self.evidence_failure_policy,
                providers=providers,
            ),
            bootstrap=BootstrapPolicy(mode=self.bootstrap_mode),
            desired_state=DesiredStatePolicy(),
        )

    def apply_policy(self, policy: AgentPolicy) -> JsonObject:
        if policy.cluster_id != self.cluster_id:
            raise ValueError(f"policy cluster_id does not match agent: {policy.cluster_id}")
        if policy.cluster_role != self.cluster_role:
            raise ValueError(f"policy cluster_role does not match agent: {policy.cluster_role}")

        base_policy = self.control_store.load_policy() or self.default_policy
        enabled_provider_keys: set[str] = set()
        provider_intervals: dict[str, int] = {}
        min_worker_counts: dict[str, int] = {}
        registered_queries: dict[str, list[str]] = {}
        for provider_key in self.evidence_collector.providers:
            provider_policy = policy.evidence.providers.get(
                provider_key,
                base_policy.evidence.providers.get(
                    provider_key,
                    self.default_policy.evidence.providers.get(
                        provider_key,
                        EvidenceProviderPolicy(enabled=False),
                    ),
                ),
            )
            provider_intervals[provider_key] = provider_policy.interval_seconds
            min_worker_counts[provider_key] = provider_policy.min_workers
            registered_queries[provider_key] = self.register_policy_queries(
                provider_key,
                provider_policy.queries,
            )
            if provider_policy.enabled:
                enabled_provider_keys.add(provider_key)

        self.evidence_scheduler.configure_schedule(
            provider_intervals=provider_intervals,
            enabled_provider_keys=enabled_provider_keys,
        )
        self.evidence_scheduler.set_worker_counts(min_worker_counts)
        return {
            "generation": policy.generation,
            "cluster_role": policy.cluster_role,
            "bootstrap_mode": policy.bootstrap.mode,
            "enabled_providers": sorted(enabled_provider_keys),
            "evidence_worker_counts": min_worker_counts,
            "registered_queries": registered_queries,
        }

    async def policy_status_details(self) -> JsonObject:
        return {
            "target_rbac_manifest": await self.target_rbac_manifest_status(),
            "integrations": {"prometheus": dict(self.prometheus_integration_status)},
        }

    async def apply_runtime_configurations(
        self,
        client: ManagementPlaneClient,
        policy: AgentPolicy,
    ) -> JsonObject:
        """Apply revision-bound provider secrets without persisting them in agent policy state."""
        provider_policy = policy.evidence.providers.get("metrics")
        revision = provider_policy.configuration_revision if provider_policy else None
        operation_id = provider_policy.configuration_operation_id if provider_policy else None
        if revision is None or operation_id is None:
            self.evidence_collector.remove_provider("metrics")
            self.evidence_scheduler.unregister_provider("metrics")
            self.prometheus_probe_attempts.clear()
            self.prometheus_integration_status = {
                "state": "unconfigured",
                "revision": None,
                "operation_id": None,
                "error_code": None,
            }
            return {"integrations": {"prometheus": dict(self.prometheus_integration_status)}}

        current = self.prometheus_integration_status
        if current.get("revision") == revision and current.get("operation_id") == operation_id:
            if current.get("state") == "connected":
                return {"integrations": {"prometheus": dict(current)}}
            if current.get("state") == "failed":
                raise RuntimeError(str(current.get("error_code") or "prometheus_probe_failed"))

        try:
            provider = await self.probe_prometheus_runtime_configuration(
                client,
                revision=revision,
                operation_id=operation_id,
            )
        except PrometheusRuntimeConfigurationError as exc:
            attempts = self.prometheus_probe_attempts.get(revision, 0) + 1
            self.prometheus_probe_attempts = {revision: attempts}
            terminal = not exc.retryable or attempts >= PROMETHEUS_PROBE_MAX_ATTEMPTS
            state = "failed" if terminal else "retrying"
            failed = {
                "state": state,
                "revision": revision,
                "operation_id": operation_id,
                "error_code": exc.code,
            }
            try:
                await client.report_prometheus_integration_status(dict(failed))
            except Exception as report_exc:
                raise RuntimeError("prometheus_status_report_failed") from report_exc
            self.prometheus_integration_status = failed
            raise RuntimeError(exc.code) from exc

        previous_provider = self.evidence_collector.providers.get("metrics")
        previous_scheduled = "metrics" in self.evidence_scheduler.provider_keys
        previous_worker_count = self.evidence_scheduler.provider_worker_counts.get("metrics", 0)
        previous_interval = self.evidence_scheduler.provider_intervals.get(
            "metrics", provider_policy.interval_seconds
        )
        previous_enabled = "metrics" in self.evidence_scheduler.enabled_provider_keys

        def rollback_local_provider() -> None:
            if previous_provider is None:
                self.evidence_collector.remove_provider("metrics")
            else:
                self.evidence_collector.replace_provider(previous_provider)
            if previous_scheduled:
                self.evidence_scheduler.register_provider(
                    "metrics",
                    worker_count=previous_worker_count,
                    interval_seconds=previous_interval,
                    enabled=previous_enabled,
                )
            else:
                self.evidence_scheduler.unregister_provider("metrics")

        try:
            self.evidence_collector.replace_provider(provider)
            self.evidence_scheduler.register_provider(
                "metrics",
                worker_count=provider_policy.min_workers,
                interval_seconds=provider_policy.interval_seconds,
                enabled=provider_policy.enabled,
            )
        except Exception:
            rollback_local_provider()
            raise
        try:
            await client.report_prometheus_integration_status(
                {
                    "revision": revision,
                    "operation_id": operation_id,
                    "state": "connected",
                }
            )
        except Exception as exc:
            rollback_local_provider()
            raise RuntimeError("prometheus_status_report_failed") from exc
        self.prometheus_probe_attempts.clear()
        self.prometheus_integration_status = {
            "state": "connected",
            "revision": revision,
            "operation_id": operation_id,
            "error_code": None,
        }
        return {"integrations": {"prometheus": dict(self.prometheus_integration_status)}}

    async def probe_prometheus_runtime_configuration(
        self,
        client: ManagementPlaneClient,
        *,
        revision: str,
        operation_id: str,
    ) -> PrometheusMetricsProvider:
        try:
            raw_config = await client.fetch_prometheus_integration(revision)
        except Exception as exc:
            raise PrometheusRuntimeConfigurationError(
                "prometheus_configuration_fetch_failed",
                retryable=prometheus_transport_error_retryable(exc),
            ) from exc
        try:
            config = AgentPrometheusIntegrationConfig.model_validate(raw_config)
        except ValueError as exc:
            raise PrometheusRuntimeConfigurationError(
                "prometheus_configuration_invalid",
                retryable=False,
            ) from exc
        if (
            config.cluster_id != self.cluster_id
            or config.revision != revision
            or config.operation_id != operation_id
        ):
            raise PrometheusRuntimeConfigurationError(
                "prometheus_configuration_invalid",
                retryable=False,
            )
        resolved_address = await self.resolve_prometheus_destination(config.address)
        provider = PrometheusMetricsProvider(
            config.address,
            headers=config.headers,
            resolved_address=resolved_address,
        )
        try:
            async with httpx.AsyncClient(
                timeout=provider.timeout_seconds,
                transport=self.telemetry_transport,
            ) as probe_client:
                response = await probe_client.get(
                    provider.request_url("/api/v1/query"),
                    params={"query": "up"},
                    headers=provider.request_headers(),
                    extensions=provider.request_extensions(),
                )
                response.raise_for_status()
        except Exception as exc:
            raise PrometheusRuntimeConfigurationError(
                "prometheus_probe_http_error",
                retryable=prometheus_transport_error_retryable(exc),
            ) from exc
        try:
            payload = response.json()
        except Exception as exc:
            raise PrometheusRuntimeConfigurationError(
                "prometheus_probe_invalid_response",
                retryable=True,
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "success"
            or not isinstance(payload.get("data"), dict)
        ):
            raise PrometheusRuntimeConfigurationError(
                "prometheus_probe_invalid_response",
                retryable=True,
            )
        return provider

    async def resolve_prometheus_destination(self, address: str) -> str:
        """Resolve once, reject unsafe answers, and return one DNS-free connect address."""
        parsed = urlsplit(address)
        hostname = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            infos = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM),
            )
        except OSError as exc:
            raise PrometheusRuntimeConfigurationError(
                "prometheus_probe_dns_error",
                retryable=True,
            ) from exc
        resolved_addresses: list[str] = []
        for info in infos:
            try:
                resolved = ipaddress.ip_address(str(info[4][0]))
            except ValueError:
                continue
            if (
                resolved.is_loopback
                or resolved.is_link_local
                or resolved.is_multicast
                or resolved.is_unspecified
            ):
                raise PrometheusRuntimeConfigurationError(
                    "prometheus_destination_denied",
                    retryable=False,
                )
            normalized = str(resolved)
            if normalized not in resolved_addresses:
                resolved_addresses.append(normalized)
        if not resolved_addresses:
            raise PrometheusRuntimeConfigurationError(
                "prometheus_probe_dns_error",
                retryable=True,
            )
        return resolved_addresses[0]

    async def target_rbac_manifest_status(self) -> JsonObject:
        """Observe the administrator-owned role without ever attempting RBAC writes."""

        base_url = kubernetes_api_base_url()
        token = service_account_token()
        result: JsonObject = {
            "status": "admin_apply_required",
            "actual_version": None,
            "expected_version": TARGET_RBAC_MANIFEST_VERSION,
        }
        if self.cluster_role == MANAGEMENT_CLUSTER_ROLE:
            result["status"] = "not_applicable"
            return result
        if not base_url or not token:
            result["probe"] = "kubernetes_api_unavailable"
            return result
        async with kubernetes_client(self.kubernetes_transport) as client:
            response = await client.get(
                f"{base_url}/apis/rbac.authorization.k8s.io/v1/clusterroles/cluster-agent-read",
                headers=kubernetes_headers(token),
            )
        if response.status_code != 200:
            result["probe"] = "forbidden" if response.status_code == 403 else "unavailable"
            return result
        body = response.json()
        metadata = body.get("metadata") if isinstance(body, dict) else None
        annotations = metadata.get("annotations") if isinstance(metadata, dict) else None
        actual = (
            annotations.get(TARGET_RBAC_VERSION_ANNOTATION)
            if isinstance(annotations, dict)
            else None
        )
        if isinstance(actual, str) and actual:
            result["actual_version"] = actual
        if actual == TARGET_RBAC_MANIFEST_VERSION:
            result["status"] = "current"
        return result

    def register_policy_queries(
        self,
        provider_key: str,
        queries: list[JsonObject],
    ) -> list[str]:
        source = telemetry.source_for_provider(provider_key)
        if source is None:
            return []
        definitions: list[TelemetryQueryDefinition] = []
        for query in queries:
            definition = compile_policy_query_definition(
                query,
                source=source,
                cluster_id=self.cluster_id,
            )
            definitions.append(definition)
        self.evidence_collector.replace_queries(source, tuple(definitions))
        return [definition.name for definition in definitions]

    async def run(self) -> None:
        try:
            if self.client is not None:
                await self.run_with_client(self.client)
                return
            async with HttpManagementPlaneClient(self.base_url) as client:
                await self.run_with_client(client)
        finally:
            self.close()

    async def run_with_client(self, client: ManagementPlaneClient) -> None:
        self.policy_sync.apply_stored_or_default()
        await self.register(client)
        await self.reconcile_node_collector_once()
        await asyncio.gather(
            self.policy_sync.run(client),
            self.reconcile_node_collector_forever(),
            self.cleanup_expired_rca_test_fixtures_forever(),
            self.evidence_scheduler.run(client),
            self.reconciler.run(client),
            self.poll_commands(client),
            self.flush_command_results_forever(client),
            self.report_traffic_sources_forever(client),
            self.live_summary.run(),
        )

    async def report_traffic_sources_forever(self, client: ManagementPlaneClient) -> None:
        reporter = getattr(client, "report_agent_status", None)
        if not callable(reporter):
            return
        while True:
            try:
                observation = await self.traffic_source_detector.observe(
                    active_source=self.control_store.load_runtime_setting("traffic.active_source")
                )
                await reporter(
                    self.cluster_id,
                    self.agent_id,
                    self.advertised_capabilities(),
                    {"traffic_sources": observation},
                )
            except Exception as exc:
                LOGGER.warning(
                    "traffic_source_observation_failed",
                    extra={
                        CONTEXT_KEY: {
                            Gateway.CLUSTER_ID: self.cluster_id,
                            Gateway.AGENT_ID: self.agent_id,
                            "exception_type": type(exc).__name__,
                        }
                    },
                )
            await asyncio.sleep(AgentConfig.TRAFFIC_SOURCE_OBSERVATION_INTERVAL_SECONDS)

    async def cleanup_expired_rca_test_fixtures_forever(self) -> None:
        if not rca_test_runs_enabled():
            return
        while True:
            try:
                cleaned = await self.cleanup_expired_rca_test_fixtures_once()
                if cleaned:
                    LOGGER.info(
                        "rca_test_fixtures_expired",
                        extra={CONTEXT_KEY: {"cluster_id": self.cluster_id, "cleaned": cleaned}},
                    )
            except Exception as exc:
                LOGGER.warning(
                    "rca_test_fixture_cleanup_failed",
                    extra={
                        CONTEXT_KEY: {
                            "cluster_id": self.cluster_id,
                            "exception_type": type(exc).__name__,
                        }
                    },
                )
            await asyncio.sleep(RCA_TEST_CLEANUP_INTERVAL_SECONDS)

    async def cleanup_expired_rca_test_fixtures_once(self) -> int:
        if not rca_test_runs_enabled():
            return 0
        base_url = kubernetes_api_base_url()
        token = service_account_token()
        if not base_url or not token or self.cluster_role == MANAGEMENT_CLUSTER_ROLE:
            return 0
        namespace = Sandbox.NAMESPACE
        collection_urls = (
            f"{base_url}/apis/apps/v1/namespaces/{namespace}/deployments",
            f"{base_url}/api/v1/namespaces/{namespace}/services",
        )
        candidates: set[tuple[str, str]] = set()
        cleaned = 0
        async with kubernetes_client(self.kubernetes_transport) as client:
            for collection_url in collection_urls:
                response = await client.get(
                    collection_url,
                    params={"labelSelector": "kubeheal.io/rca-test=true"},
                    headers=kubernetes_headers(token),
                )
                response.raise_for_status()
                body = response.json()
                rows = body.get("items", []) if isinstance(body, dict) else []
                for row in rows:
                    if not isinstance(row, dict) or not rca_test_resource_expired(row):
                        continue
                    metadata = row.get("metadata")
                    meta = metadata if isinstance(metadata, dict) else {}
                    name = str(meta.get("name") or "")
                    annotations = meta.get("annotations")
                    annotation_body = annotations if isinstance(annotations, dict) else {}
                    run_id = str(annotation_body.get(RCA_TEST_RUN_ANNOTATION) or "")
                    if name and run_id:
                        candidates.add((name, run_id))
        for name, run_id in sorted(candidates):
            if await self.cleanup_rca_test_fixture_if_owned(namespace, name, run_id):
                cleaned += 1
        return cleaned

    async def register(self, client: ManagementPlaneClient) -> None:
        while True:
            try:
                await client.register_agent(
                    self.cluster_id,
                    self.agent_id,
                    self.advertised_capabilities(),
                )
                return
            except Exception as exc:
                LOGGER.warning(
                    "agent_waiting_for_management_gateway",
                    extra={
                        CONTEXT_KEY: {
                            Gateway.CLUSTER_ID: self.cluster_id,
                            Gateway.AGENT_ID: self.agent_id,
                            "exception_type": type(exc).__name__,
                        }
                    },
                )
                await asyncio.sleep(AgentConfig.REGISTER_RETRY_DELAY_SECONDS)

    def advertised_capabilities(self) -> list[str]:
        """Advertise executable features only for the current runtime policy."""

        capabilities = list(AgentConfig.AGENT_CAPABILITIES)
        direct_commands_enabled = getattr(self, "direct_commands_enabled", True)
        if not direct_commands_enabled:
            capabilities.remove(Command.KUBERNETES_CRONJOB_CONTROL_CAPABILITY)
            capabilities.remove(Command.KUBERNETES_RESOURCE_DELETE_CAPABILITY)
            capabilities.remove(Command.KUBERNETES_WORKLOAD_ROLLBACK_CAPABILITY)
            capabilities.remove(Command.GITOPS_RESOURCE_CONTROL_CAPABILITY)
            capabilities.remove(Command.KUBERNETES_DEBUG_CAPABILITY)
            capabilities.remove(Command.TRAFFIC_SOURCE_SELECT_CAPABILITY)
            capabilities.remove(Command.TRAFFIC_SOURCE_CONNECT_CAPABILITY)
        if direct_commands_enabled and getattr(self, "node_control_enabled", False):
            capabilities.append(Command.KUBERNETES_NODE_CONTROL_CAPABILITY)
        return capabilities

    async def poll_commands(self, client: ManagementPlaneClient) -> None:
        while True:
            try:
                command = await client.poll_command(
                    self.cluster_id,
                    self.workspace_id,
                    self.agent_id,
                    AgentConfig.COMMAND_POLL_TIMEOUT_SECONDS,
                )
                if command:
                    command_id = command[Gateway.COMMAND_ID]
                    workspace_id = command.get(Gateway.WORKSPACE_ID, self.workspace_id)
                    lease_id = command[Gateway.LEASE_ID]
                    attempt_id = command.get(Gateway.ATTEMPT_ID)
                    action = command[Gateway.ACTION]
                    LOGGER.info(
                        "agent_executing_command",
                        extra={
                            CONTEXT_KEY: {
                                Gateway.CLUSTER_ID: self.cluster_id,
                                Gateway.AGENT_ID: self.agent_id,
                                Gateway.COMMAND_ID: command_id,
                                Gateway.ACTION: action,
                            }
                        },
                    )
                    if attempt_id:
                        await client.start_command(
                            command_id,
                            self.cluster_id,
                            str(workspace_id),
                            lease_id,
                            self.agent_id,
                            str(attempt_id),
                        )
                    else:
                        await client.start_command(
                            command_id,
                            self.cluster_id,
                            str(workspace_id),
                            lease_id,
                            self.agent_id,
                        )
                    result = await self.execute_command_with_heartbeat(
                        client,
                        command,
                        command_id,
                        str(workspace_id),
                        lease_id,
                        str(attempt_id) if attempt_id else None,
                    )
                    self.command_outbox.enqueue_result(
                        command_id=command_id,
                        workspace_id=str(workspace_id),
                        lease_id=lease_id,
                        agent_id=self.agent_id,
                        attempt_id=str(attempt_id) if attempt_id else None,
                        result=result,
                    )
                    await self.flush_command_results_once(client)
            except Exception as exc:
                LOGGER.warning(
                    "command_polling_failed",
                    extra={
                        CONTEXT_KEY: {
                            Gateway.CLUSTER_ID: self.cluster_id,
                            Gateway.AGENT_ID: self.agent_id,
                            "exception_type": type(exc).__name__,
                        }
                    },
                )
                await asyncio.sleep(AgentConfig.COMMAND_RETRY_DELAY_SECONDS)

    async def flush_command_results_forever(self, client: ManagementPlaneClient) -> None:
        while True:
            await self.flush_command_results_once(client)
            await asyncio.sleep(COMMAND_OUTBOX_FLUSH_INTERVAL_SECONDS)

    async def flush_command_results_once(self, client: ManagementPlaneClient) -> bool:
        finalization = self.command_outbox.next_finalization()
        if finalization is not None:
            return await self.retry_agent_uninstall_finalization(finalization)

        record = self.command_outbox.next_result()
        if record is None:
            return False
        try:
            if record.attempt_id.startswith("legacy:"):
                await client.complete_command(
                    record.command_id,
                    record.workspace_id,
                    record.lease_id,
                    record.agent_id,
                    record.result,
                )
            else:
                await client.complete_command(
                    record.command_id,
                    record.workspace_id,
                    record.lease_id,
                    record.agent_id,
                    record.result,
                    record.attempt_id,
                )
            if self.is_agent_uninstall_cleanup_result(record.result):
                self.command_outbox.mark_acknowledged_for_finalization(
                    record.command_id,
                    record.attempt_id,
                )
                return await self.retry_agent_uninstall_finalization(record)
            self.command_outbox.mark_sent(record.command_id, record.attempt_id)
            LOGGER.info(
                "command_result_flushed",
                extra={
                    CONTEXT_KEY: {
                        Gateway.CLUSTER_ID: self.cluster_id,
                        Gateway.AGENT_ID: self.agent_id,
                        Gateway.COMMAND_ID: record.command_id,
                        Gateway.WORKSPACE_ID: record.workspace_id,
                        Gateway.LEASE_ID: record.lease_id,
                        Gateway.STATUS: record.result.get(Gateway.STATUS),
                        Gateway.APPLIED: record.result.get(Gateway.APPLIED),
                        Gateway.RETRYABLE: record.result.get(Gateway.RETRYABLE),
                        "attempt_count": record.attempt_count,
                    }
                },
            )
            return True
        except Exception as exc:
            if self.should_finalize_uninstall_after_lost_ack(record, exc):
                LOGGER.warning(
                    "agent_uninstall_result_ack_response_lost",
                    extra={
                        CONTEXT_KEY: {
                            Gateway.CLUSTER_ID: self.cluster_id,
                            Gateway.AGENT_ID: self.agent_id,
                            Gateway.COMMAND_ID: record.command_id,
                            "attempt_count": record.attempt_count,
                            "status_code": exc.response.status_code,
                        }
                    },
                )
                self.command_outbox.mark_acknowledged_for_finalization(
                    record.command_id,
                    record.attempt_id,
                )
                return await self.retry_agent_uninstall_finalization(record)
            abandoned = self.command_outbox.record_failure(
                record.command_id,
                str(exc),
                COMMAND_OUTBOX_MAX_ATTEMPTS,
                record.attempt_id,
            )
            LOGGER.warning(
                "command_result_flush_failed",
                extra={
                    CONTEXT_KEY: {
                        Gateway.CLUSTER_ID: self.cluster_id,
                        Gateway.AGENT_ID: self.agent_id,
                        Gateway.COMMAND_ID: record.command_id,
                        "attempt_count": record.attempt_count + 1,
                        "abandoned": abandoned,
                        "exception_type": type(exc).__name__,
                    }
                },
            )
            return False

    async def retry_agent_uninstall_finalization(self, record: CommandResultRecord) -> bool:
        """Retry self-removal only after the result ACK is durable locally."""

        try:
            await self.finalize_agent_installation_cleanup()
            self.command_outbox.mark_finalized(record.command_id, record.attempt_id)
            LOGGER.info(
                "agent_uninstall_finalized_after_result_ack",
                extra={
                    CONTEXT_KEY: {
                        Gateway.CLUSTER_ID: self.cluster_id,
                        Gateway.AGENT_ID: self.agent_id,
                        Gateway.COMMAND_ID: record.command_id,
                    }
                },
            )
            return True
        except Exception as exc:
            LOGGER.warning(
                "agent_uninstall_finalization_retry_pending",
                extra={
                    CONTEXT_KEY: {
                        Gateway.CLUSTER_ID: self.cluster_id,
                        Gateway.AGENT_ID: self.agent_id,
                        Gateway.COMMAND_ID: record.command_id,
                        "exception_type": type(exc).__name__,
                    }
                },
            )
            return False

    @staticmethod
    def is_agent_uninstall_cleanup_result(result: JsonObject) -> bool:
        return (
            result.get(Gateway.STATUS) == CommandStatus.COMPLETED
            and result.get("cleanup_completed") is True
            and result.get("cleanup_resources") == list(UNINSTALL_CLEANUP_RESOURCE_REFS)
            and result.get("residual_resources") == []
        )

    def should_finalize_uninstall_after_lost_ack(
        self,
        record: CommandResultRecord,
        exc: Exception,
    ) -> bool:
        """Recognize the narrow replay signal produced by credential revocation.

        A first transport failure is ambiguous: the server may or may not have
        committed the result.  Only a later authenticated replay rejected as
        missing/revoked can close that ambiguity, and only for the exact
        verified uninstall cleanup receipt.
        """

        return (
            record.attempt_count > 0
            and self.is_agent_uninstall_cleanup_result(record.result)
            and isinstance(exc, httpx.HTTPStatusError)
            and exc.response.status_code in {401, 404}
        )

    async def execute_command_with_heartbeat(
        self,
        client: ManagementPlaneClient,
        command: CommandRecord,
        command_id: str,
        workspace_id: str,
        lease_id: str,
        attempt_id: str | None,
    ) -> JsonObject:
        cancel_requested = asyncio.Event()
        heartbeat = asyncio.create_task(
            self.heartbeat_command_until_done(
                client,
                command_id,
                workspace_id,
                lease_id,
                attempt_id,
                cancel_requested,
            )
        )
        try:
            # The server never kills a local process.  Handlers may observe this
            # event at safe checkpoints; already-running side effects finish and
            # report their actual result so completion/cancel races are honest.
            async def report_progress(progress: JsonObject) -> None:
                await client.heartbeat_command(
                    command_id,
                    self.cluster_id,
                    workspace_id,
                    lease_id,
                    self.agent_id,
                    attempt_id,
                    None,
                    progress,
                )

            return await self.execute_command(
                command,
                cancel_requested=cancel_requested,
                operation_progress_reporter=report_progress,
            )
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def heartbeat_command_until_done(
        self,
        client: ManagementPlaneClient,
        command_id: str,
        workspace_id: str,
        lease_id: str,
        attempt_id: str | None,
        cancel_requested: asyncio.Event,
    ) -> None:
        observed_cancel_generation: int | None = None
        while True:
            await asyncio.sleep(AgentConfig.COMMAND_HEARTBEAT_INTERVAL_SECONDS)
            try:
                if attempt_id is None and observed_cancel_generation is None:
                    response = await client.heartbeat_command(
                        command_id,
                        self.cluster_id,
                        workspace_id,
                        lease_id,
                        self.agent_id,
                    )
                else:
                    response = await client.heartbeat_command(
                        command_id,
                        self.cluster_id,
                        workspace_id,
                        lease_id,
                        self.agent_id,
                        attempt_id,
                        observed_cancel_generation,
                    )
                if isinstance(response, dict) and response.get(Gateway.CANCEL_REQUESTED) is True:
                    generation = response.get(Gateway.CANCEL_GENERATION)
                    if isinstance(generation, int) and generation > 0:
                        observed_cancel_generation = generation
                        cancel_requested.set()
            except Exception as exc:
                LOGGER.warning(
                    "command_heartbeat_failed",
                    extra={
                        CONTEXT_KEY: {
                            Gateway.CLUSTER_ID: self.cluster_id,
                            Gateway.AGENT_ID: self.agent_id,
                            Gateway.COMMAND_ID: command_id,
                            "exception_type": type(exc).__name__,
                        }
                    },
                )

    async def reconcile_node_collector_forever(self) -> None:
        while True:
            await self.reconcile_node_collector_once()
            await asyncio.sleep(AgentConfig.NODE_COLLECTOR_RECONCILE_INTERVAL_SECONDS)

    async def reconcile_node_collector_once(self) -> None:
        try:
            applied, message = await self.node_collector.reconcile()
            LOGGER.info(
                "node_collector_reconciled",
                extra={
                    CONTEXT_KEY: {
                        Gateway.CLUSTER_ID: self.cluster_id,
                        Gateway.AGENT_ID: self.agent_id,
                        Gateway.APPLIED: applied,
                        Gateway.MESSAGE: message,
                    }
                },
            )
        except Exception as exc:
            LOGGER.warning(
                "node_collector_reconcile_failed",
                extra={
                    CONTEXT_KEY: {
                        Gateway.CLUSTER_ID: self.cluster_id,
                        Gateway.AGENT_ID: self.agent_id,
                        "exception_type": type(exc).__name__,
                    }
                },
            )

    async def execute_command(
        self,
        command: CommandRecord,
        *,
        cancel_requested: asyncio.Event | None = None,
        operation_progress_reporter: object = None,
    ) -> JsonObject:
        if cancel_requested is not None and cancel_requested.is_set():
            return {
                Gateway.STATUS: CommandStatus.CANCELLED,
                Gateway.CLUSTER_ID: self.cluster_id,
                Gateway.APPLIED: False,
                Gateway.MESSAGE: "command cancelled before a safe execution checkpoint",
                Gateway.RETRYABLE: False,
                Gateway.RESOURCES: [],
                Gateway.STDOUT: "",
                Gateway.STDERR: "",
            }
        action = str(command.get(Gateway.ACTION, ""))
        payload = self.command_payload(command)
        if action in RCA_TEST_COMMAND_ACTIONS and not rca_test_runs_enabled():
            return self.command_result(False, RCA_TEST_RUNS_DISABLED_MESSAGE)
        action_spec = command_action_spec(action)
        if (
            not getattr(self, "direct_commands_enabled", True)
            and action not in {QUERY_RUN_ACTION, Command.CLUSTER_AGENT_UNINSTALL_ACTION}
            and not bool(action_spec and action_spec.read_only)
        ):
            return self.command_result(False, AgentConfig.DIRECT_COMMANDS_DISABLED_MESSAGE)
        if action in {
            Command.KUBERNETES_NODE_CORDON_ACTION,
            Command.KUBERNETES_NODE_UNCORDON_ACTION,
            Command.KUBERNETES_NODE_DRAIN_ACTION,
            Command.KUBERNETES_NODE_DEBUG_ACTION,
            Command.KUBERNETES_NODE_DEBUG_CLEANUP_ACTION,
        } and not getattr(self, "node_control_enabled", False):
            return self.command_result(False, "node control is disabled by agent profile")
        direct_execution = self.direct_execution_requested(command)
        if self.management_write_blocked(action, direct_execution=direct_execution):
            LOGGER.warning(
                "management_agent_ignored_write_command",
                extra={
                    CONTEXT_KEY: {
                        Gateway.CLUSTER_ID: self.cluster_id,
                        Gateway.AGENT_ID: self.agent_id,
                        Gateway.ACTION: action,
                    }
                },
            )
            return self.command_result(False, MANAGEMENT_READONLY_CODE)
        approval_error = self.approval_evidence_error(command)
        if (
            self.write_action_requires_approval(action, command)
            and approval_error
            and not self.approval_exempt_for_environment(action, command)
            and not direct_execution
        ):
            return self.command_result(False, approval_error)
        try:
            return await self.command_registry.execute(
                action,
                payload,
                metadata={
                    Gateway.COMMAND_ID: command.get(Gateway.COMMAND_ID, ""),
                    Gateway.APPROVAL_REF: self.command_metadata_value(
                        command, Gateway.APPROVAL_REF
                    ),
                    Gateway.POLICY_DECISION_REF: self.command_metadata_value(
                        command, Gateway.POLICY_DECISION_REF
                    ),
                    Gateway.APPROVAL_DECIDED_BY: self.command_metadata_value(
                        command, Gateway.APPROVAL_DECIDED_BY
                    ),
                    Gateway.APPROVAL_EXPIRES_AT: self.command_metadata_value(
                        command, Gateway.APPROVAL_EXPIRES_AT
                    ),
                    Gateway.DIRECT_EXECUTION: direct_execution,
                    "cooperative_cancel_requested": cancel_requested,
                    "operation_progress_reporter": operation_progress_reporter,
                },
            )
        except Exception as exc:
            return self.command_result(False, str(exc))

    def write_action_requires_approval(
        self,
        action: str,
        command: CommandRecord,
    ) -> bool:
        if action in {
            AgentConfig.ROLLOUT_RESTART_ACTION,
            Command.KUBERNETES_STATEFULSET_RESTART_ACTION,
            Command.KUBERNETES_DAEMONSET_RESTART_ACTION,
        }:
            return not is_sandbox_environment(self.command_namespace_value(command))
        return action in {
            AgentConfig.APPLY_MANIFEST_ACTION,
            KUBERNETES_DEPLOYMENT_SCALE_ACTION,
            Command.KUBERNETES_STATEFULSET_SCALE_ACTION,
            Command.KUBERNETES_NODE_CORDON_ACTION,
            Command.KUBERNETES_NODE_UNCORDON_ACTION,
            Command.KUBERNETES_NODE_DRAIN_ACTION,
            Command.KUBERNETES_POD_DEBUG_ACTION,
            Command.KUBERNETES_NODE_DEBUG_ACTION,
            Command.KUBERNETES_NODE_DEBUG_CLEANUP_ACTION,
        }

    def direct_execution_requested(self, command: CommandRecord) -> bool:
        return command.get(Gateway.DIRECT_EXECUTION) is True

    def management_write_blocked(self, action: str, *, direct_execution: bool = False) -> bool:
        if direct_execution:
            return False
        if self.cluster_role != MANAGEMENT_CLUSTER_ROLE:
            return False
        return action in {
            AgentConfig.APPLY_MANIFEST_ACTION,
            Command.CATALOG_HELM_INSTALL_ACTION,
            HELM_RELEASE_OPERATION_ACTION,
            AgentConfig.ROLLOUT_RESTART_ACTION,
            KUBERNETES_CONFIGMAP_PATCH_ACTION,
            KUBERNETES_DEPLOYMENT_PATCH_ACTION,
            KUBERNETES_DEPLOYMENT_SCALE_ACTION,
            Command.KUBERNETES_STATEFULSET_SCALE_ACTION,
            Command.KUBERNETES_STATEFULSET_RESTART_ACTION,
            Command.KUBERNETES_DAEMONSET_RESTART_ACTION,
            Command.KUBERNETES_NODE_CORDON_ACTION,
            Command.KUBERNETES_NODE_UNCORDON_ACTION,
            Command.KUBERNETES_NODE_DRAIN_ACTION,
            Command.KUBERNETES_POD_DEBUG_ACTION,
            Command.KUBERNETES_NODE_DEBUG_ACTION,
            Command.KUBERNETES_NODE_DEBUG_CLEANUP_ACTION,
            Command.KUBERNETES_CRONJOB_TRIGGER_ACTION,
            Command.KUBERNETES_CRONJOB_SUSPEND_ACTION,
            Command.KUBERNETES_CRONJOB_RESUME_ACTION,
            Command.KUBERNETES_RESOURCE_DELETE_ACTION,
            Command.KUBERNETES_DEPLOYMENT_ROLLBACK_ACTION,
            Command.KUBERNETES_STATEFULSET_ROLLBACK_ACTION,
            Command.KUBERNETES_DAEMONSET_ROLLBACK_ACTION,
            Command.RCA_TEST_SCENARIO_INJECT_ACTION,
            Command.RCA_TEST_SCENARIO_CLEANUP_ACTION,
            Command.CLUSTER_AGENT_UNINSTALL_ACTION,
        }

    def command_metadata_value(self, command: CommandRecord, field: str) -> str:
        value = command.get(field)
        if isinstance(value, str) and value:
            return value
        payload = command.get(Gateway.PAYLOAD)
        if isinstance(payload, dict):
            nested = payload.get(field)
            if isinstance(nested, str) and nested:
                return nested
        return ""

    def approval_exempt_for_environment(self, action: str, command: CommandRecord) -> bool:
        """sandbox 허용 rule — command-worker 의 COMMAND_AUTO_APPROVE_* 와 대칭.

        지정 액션(기본: deployment scale)이 sandbox 환경 plan 메타데이터를 가지면
        승인 증적 없이 실행을 허용한다. namespace·name-scoped 정책 가드는 그대로 적용됨.
        """
        actions = {
            item.strip()
            for item in env(
                "AGENT_AUTO_APPROVE_ACTIONS",
                ",".join(
                    (
                        KUBERNETES_DEPLOYMENT_SCALE_ACTION,
                        Command.KUBERNETES_STATEFULSET_SCALE_ACTION,
                    )
                ),
            ).split(",")
            if item.strip()
        }
        environments = {
            normalize_environment(item)
            for item in env("AGENT_AUTO_APPROVE_ENVIRONMENTS", "sandbox").split(",")
            if item.strip()
        }
        environment = normalize_environment(self.command_metadata_value(command, "environment"))
        namespace = normalize_environment(self.command_namespace_value(command))
        return (
            action in actions
            and environment in environments
            and bool(namespace)
            and namespace == environment
        )

    def command_namespace_value(self, command: CommandRecord) -> str:
        payload = command.get(Gateway.PAYLOAD)
        if isinstance(payload, dict):
            diff = payload.get("diff")
            if isinstance(diff, dict):
                desired_manifest = diff.get("desired_manifest")
                if isinstance(desired_manifest, dict):
                    metadata = desired_manifest.get("metadata")
                    if isinstance(metadata, dict):
                        namespace = metadata.get(Gateway.NAMESPACE)
                        if isinstance(namespace, str) and namespace:
                            return namespace
                namespace = diff.get(Gateway.NAMESPACE)
                if isinstance(namespace, str) and namespace:
                    return namespace
            namespace = payload.get(Gateway.NAMESPACE)
            if isinstance(namespace, str) and namespace:
                return namespace
        namespace = command.get(Gateway.NAMESPACE)
        if isinstance(namespace, str) and namespace:
            return namespace
        return ""

    def approval_evidence_error(self, command: CommandRecord) -> str:
        required = (
            Gateway.APPROVAL_REF,
            Gateway.POLICY_DECISION_REF,
            Gateway.APPROVAL_DECIDED_BY,
            Gateway.APPROVAL_EXPIRES_AT,
        )
        if not all(self.command_metadata_value(command, field) for field in required):
            return AgentConfig.MISSING_APPROVAL_EVIDENCE_MESSAGE
        expires_at = parse_approval_expires_at(
            self.command_metadata_value(command, Gateway.APPROVAL_EXPIRES_AT)
        )
        if expires_at is None:
            return AgentConfig.INVALID_APPROVAL_EVIDENCE_MESSAGE
        if expires_at <= datetime.now(UTC):
            return AgentConfig.EXPIRED_APPROVAL_EVIDENCE_MESSAGE
        return ""

    @command.handler(QUERY_RUN_ACTION, payload_model=TelemetryQueryCommandPayload)
    async def run_query_command(
        self,
        ctx: CommandContext[TelemetryQueryCommandPayload],
    ) -> JsonObject:
        definition = self.query_definition_from_payload(ctx.payload.definition_payload())
        result = await self.evidence_collector.run_query(definition)
        return ctx.ok(
            "telemetry query executed",
            query=definition.__dict__,
            result=result,
        )

    @command.handler(RESOURCE_FILE_ACTION, payload_model=ResourceFileCommandPayload)
    async def read_resource_file_command(
        self,
        ctx: CommandContext[ResourceFileCommandPayload],
    ) -> JsonObject:
        return await self.resource_file_executor.execute(ctx)

    @command.handler(
        Command.TRAFFIC_SOURCE_SELECT_ACTION,
        payload_model=TrafficSourceAgentCommandPayload,
    )
    async def select_traffic_source_command(
        self,
        ctx: CommandContext[TrafficSourceAgentCommandPayload],
    ) -> JsonObject:
        cancelled = self.traffic_source_cancelled_result(ctx)
        if cancelled is not None:
            return cancelled
        observation = await self.traffic_source_detector.observe(
            active_source=self.control_store.load_runtime_setting("traffic.active_source")
        )
        source = self.observed_traffic_source(observation, ctx.payload.source_key)
        if source is None or source.get("status") != "available":
            return ctx.fail(
                "traffic source is no longer available",
                retryable=False,
                source_key=ctx.payload.source_key,
            )
        cancelled = self.traffic_source_cancelled_result(ctx)
        if cancelled is not None:
            return cancelled
        self.control_store.save_runtime_setting(
            "traffic.active_source",
            ctx.payload.source_key,
        )
        return ctx.ok(
            "traffic source selected",
            applied=True,
            source_key=ctx.payload.source_key,
            invalidated=list(ctx.payload.cache_invalidations),
        )

    @command.handler(
        Command.TRAFFIC_SOURCE_CONNECT_ACTION,
        payload_model=TrafficSourceAgentCommandPayload,
    )
    async def connect_traffic_source_command(
        self,
        ctx: CommandContext[TrafficSourceAgentCommandPayload],
    ) -> JsonObject:
        cancelled = self.traffic_source_cancelled_result(ctx)
        if cancelled is not None:
            return cancelled
        active_source = self.control_store.load_runtime_setting("traffic.active_source")
        if active_source != ctx.payload.source_key:
            return ctx.fail(
                "traffic source selection changed before connect",
                retryable=False,
                source_key=ctx.payload.source_key,
            )
        observation = await self.traffic_source_detector.observe(active_source=active_source)
        source = self.observed_traffic_source(observation, ctx.payload.source_key)
        if source is None or source.get("status") != "available":
            return ctx.fail(
                "traffic source endpoint is unavailable",
                retryable=True,
                source_key=ctx.payload.source_key,
            )
        cancelled = self.traffic_source_cancelled_result(ctx)
        if cancelled is not None:
            return cancelled
        return ctx.ok(
            "traffic source connected",
            source_key=ctx.payload.source_key,
            connection={"state": "connected", "observed_at": observation["observed_at"]},
            invalidated=list(ctx.payload.cache_invalidations),
        )

    @staticmethod
    def observed_traffic_source(
        observation: JsonObject,
        source_key: str,
    ) -> JsonObject | None:
        sources = observation.get("sources")
        if not isinstance(sources, list):
            return None
        return next(
            (
                source
                for source in sources
                if isinstance(source, dict) and source.get("key") == source_key
            ),
            None,
        )

    def traffic_source_cancelled_result(
        self,
        ctx: CommandContext[TrafficSourceAgentCommandPayload],
    ) -> JsonObject | None:
        cancel_requested = ctx.metadata.get("cooperative_cancel_requested")
        if not isinstance(cancel_requested, asyncio.Event) or not cancel_requested.is_set():
            return None
        return {
            Gateway.STATUS: CommandStatus.CANCELLED,
            Gateway.CLUSTER_ID: self.cluster_id,
            Gateway.APPLIED: False,
            Gateway.MESSAGE: "traffic source command cancelled at a safe observation boundary",
            Gateway.RETRYABLE: False,
            Gateway.RESOURCES: [],
            Gateway.STDOUT: "",
            Gateway.STDERR: "",
        }

    @command.handler(
        HELM_RELEASE_ARTIFACT_READ_ACTION,
        payload_model=HelmArtifactCommandPayload,
    )
    async def helm_release_artifact_read_command(
        self,
        ctx: CommandContext[HelmArtifactCommandPayload],
    ) -> JsonObject:
        result = await asyncio.to_thread(run_helm_artifact_query, ctx.payload)
        if not result.succeeded or result.artifact is None:
            return ctx.fail(
                "Helm artifact read failed",
                error_code=result.error_code or "helm_artifact_read_failed",
                retryable=False,
            )
        return ctx.ok(
            "Helm artifact read completed",
            artifact=result.artifact.model_dump(mode="json", exclude_none=True),
        )

    @command.handler(
        HELM_VALUES_PREVIEW_ACTION,
        payload_model=HelmValuesPreviewCommandPayload,
    )
    async def helm_values_preview_command(
        self,
        ctx: CommandContext[HelmValuesPreviewCommandPayload],
    ) -> JsonObject:
        try:
            live_storage = await ctx.kubernetes.get_namespaced_resource(
                api_group="core",
                version="v1",
                namespace=ctx.payload.namespace,
                resource="secrets",
                name=ctx.payload.guard.storage.name,
            )
            validate_helm_release_secret(
                live_storage,
                namespace=ctx.payload.namespace,
                release_name=ctx.payload.release_name,
                guard=ctx.payload.guard,
            )
        except ValueError:
            return ctx.fail(
                "Helm values preview rejected stale release evidence",
                error_code="helm_release_guard_stale",
                retryable=False,
            )
        except Exception:
            return ctx.fail(
                "Helm values preview could not verify release evidence",
                error_code="helm_release_guard_unavailable",
                retryable=False,
            )
        result = await asyncio.to_thread(run_helm_values_preview, ctx.payload)
        if not result.succeeded or result.preview is None:
            return ctx.fail(
                "Helm values preview failed",
                error_code=result.error_code or "helm_values_preview_failed",
                retryable=False,
                returncode=result.returncode,
            )
        return ctx.ok(
            "Helm values preview completed",
            preview=result.preview.model_dump(mode="json", exclude_none=True),
        )

    @command.handler(
        Command.GITOPS_RESOURCE_CONTROL_ACTION,
        payload_model=GitOpsResourceCommandPayload,
    )
    async def gitops_resource_control_command(
        self,
        ctx: CommandContext[GitOpsResourceCommandPayload],
    ) -> JsonObject:
        return await execute_gitops_resource_command(ctx)

    @command.k8s(
        SERVICE_HTTP_REQUEST_ACTION,
        api_group="core",
        version="v1",
        resource="services",
        verb="get",
        scope="service-access",
        payload_model=ServiceHttpRequestCommandPayload,
    )
    async def service_http_request_command(
        self,
        ctx: CommandContext[ServiceHttpRequestCommandPayload],
    ) -> JsonObject:
        try:
            result = await execute_service_http_request(
                ctx,
                transport=getattr(self, "service_http_transport", None),
            )
        except ServiceRequestCancelled:
            return {
                Gateway.STATUS: CommandStatus.CANCELLED,
                Gateway.CLUSTER_ID: self.cluster_id,
                Gateway.APPLIED: False,
                Gateway.MESSAGE: "service request cancelled",
                Gateway.RETRYABLE: False,
                Gateway.RESOURCES: [],
                Gateway.STDOUT: "",
                Gateway.STDERR: "",
            }
        except ServiceAccessExecutionError as error:
            return ctx.fail(
                str(error),
                error_code=error.code,
                retryable=False,
            )
        return ctx.ok(
            "service request completed",
            service_request=result.model_dump(),
        )

    @command.handler(
        Command.CATALOG_HELM_INSTALL_ACTION,
        payload_model=CatalogHelmInstallPayload,
    )
    async def catalog_helm_install_command(
        self,
        ctx: CommandContext[CatalogHelmInstallPayload],
    ) -> JsonObject:
        if ctx.cluster_role == MANAGEMENT_CLUSTER_ROLE and not bool(
            ctx.metadata.get(Gateway.DIRECT_EXECUTION)
        ):
            return ctx.fail(MANAGEMENT_READONLY_CODE)
        if ctx.payload.upgrade_guard is not None:
            guard = ctx.payload.upgrade_guard
            try:
                live_storage = await ctx.kubernetes.get_namespaced_resource(
                    api_group="core",
                    version="v1",
                    namespace=ctx.payload.namespace,
                    resource="secrets",
                    name=guard.storage.name,
                )
                validate_catalog_helm_upgrade_secret(live_storage, ctx.payload)
            except ValueError:
                return ctx.fail(
                    "catalog Helm upgrade guard rejected stale release evidence",
                    error_code="helm_release_guard_stale",
                    retryable=False,
                )
            except Exception:
                return ctx.fail(
                    "catalog Helm upgrade guard could not verify release evidence",
                    error_code="helm_release_guard_unavailable",
                    retryable=False,
                )
        result = await asyncio.to_thread(run_catalog_helm_install, ctx.payload)
        fields = {
            "catalog_item_id": ctx.payload.catalog_item_id,
            "catalog_version": ctx.payload.catalog_version,
            "release_name": ctx.payload.release_name,
            "returncode": result.returncode,
        }
        if not result.succeeded:
            return ctx.fail(
                f"catalog Helm install failed: {result.error_code}",
                error_code=result.error_code,
                **fields,
            )
        return ctx.ok("catalog Helm install completed", applied=True, **fields)

    @command.handler(
        HELM_RELEASE_OPERATION_ACTION,
        payload_model=HelmReleaseOperationCommandPayload,
    )
    async def helm_release_operation_command(
        self,
        ctx: CommandContext[HelmReleaseOperationCommandPayload],
    ) -> JsonObject:
        try:
            live_storage = await ctx.kubernetes.get_namespaced_resource(
                api_group="core",
                version="v1",
                namespace=ctx.payload.namespace,
                resource="secrets",
                name=ctx.payload.guard.storage.name,
            )
            validate_helm_release_operation_secret(live_storage, ctx.payload)
        except ValueError:
            return ctx.fail(
                "Helm release operation rejected stale release evidence",
                error_code="helm_release_guard_stale",
                retryable=False,
            )
        except Exception:
            return ctx.fail(
                "Helm release operation could not verify release evidence",
                error_code="helm_release_guard_unavailable",
                retryable=False,
            )
        result = await asyncio.to_thread(run_helm_release_operation, ctx.payload)
        fields = {
            "operation": ctx.payload.operation,
            "release_name": ctx.payload.release_name,
            "expected_revision": ctx.payload.guard.expected_revision,
            "rollback_revision": ctx.payload.rollback_revision,
            "returncode": result.returncode,
        }
        if not result.succeeded:
            return ctx.fail(
                f"Helm release operation failed: {result.error_code}",
                error_code=result.error_code,
                retryable=False,
                **fields,
            )
        return ctx.ok("Helm release operation completed", applied=True, **fields)

    @command.k8s(
        KUBERNETES_DEPLOYMENT_PATCH_ACTION,
        api_group="apps",
        version="v1",
        resource="deployments",
        verb="patch",
        payload_model=KubernetesPatchPayload,
    )
    async def patch_deployment_command(
        self,
        ctx: CommandContext[KubernetesPatchPayload],
    ) -> JsonObject:
        spec = ctx.kubernetes_spec
        result = await ctx.kubernetes.patch_namespaced_resource(
            api_group=spec.api_group,
            version=spec.version,
            namespace=ctx.payload.namespace,
            resource=spec.resource,
            name=ctx.payload.name,
            body=ctx.payload.patch_body(),
        )
        return ctx.ok("kubernetes deployment patched", applied=True, result=result)

    @command.k8s(
        KUBERNETES_DEPLOYMENT_SCALE_ACTION,
        api_group="apps",
        version="v1",
        resource="deployments",
        verb="patch",
        scope="user-workload",
        payload_model=KubernetesScalePayload,
    )
    async def scale_deployment_command(
        self,
        ctx: CommandContext[KubernetesScalePayload],
    ) -> JsonObject:
        return await self.scale_workload_command(ctx, label="deployment")

    @command.k8s(
        Command.KUBERNETES_STATEFULSET_SCALE_ACTION,
        api_group="apps",
        version="v1",
        resource="statefulsets",
        verb="patch",
        scope="user-workload",
        payload_model=KubernetesScalePayload,
    )
    async def scale_statefulset_command(
        self,
        ctx: CommandContext[KubernetesScalePayload],
    ) -> JsonObject:
        return await self.scale_workload_command(ctx, label="StatefulSet")

    async def scale_workload_command(
        self,
        ctx: CommandContext[KubernetesScalePayload],
        *,
        label: str,
    ) -> JsonObject:
        spec = ctx.kubernetes_spec
        result = await ctx.kubernetes.patch_namespaced_resource(
            api_group=spec.api_group,
            version=spec.version,
            namespace=ctx.payload.namespace,
            resource=spec.resource,
            name=ctx.payload.name,
            body=ctx.payload.patch_body(),
            subresource="scale",
        )
        return ctx.ok(
            f"kubernetes {label} scaled",
            applied=True,
            replicas=ctx.payload.replicas,
            result=result,
        )

    @command.k8s(
        Command.KUBERNETES_STATEFULSET_RESTART_ACTION,
        api_group="apps",
        version="v1",
        resource="statefulsets",
        verb="patch",
        scope="user-workload",
        payload_model=KubernetesGetPayload,
    )
    async def restart_statefulset_command(
        self,
        ctx: CommandContext[KubernetesGetPayload],
    ) -> JsonObject:
        return await self.restart_workload_command(ctx, label="StatefulSet")

    @command.k8s(
        Command.KUBERNETES_DAEMONSET_RESTART_ACTION,
        api_group="apps",
        version="v1",
        resource="daemonsets",
        verb="patch",
        scope="user-workload",
        payload_model=KubernetesGetPayload,
    )
    async def restart_daemonset_command(
        self,
        ctx: CommandContext[KubernetesGetPayload],
    ) -> JsonObject:
        return await self.restart_workload_command(ctx, label="DaemonSet")

    async def restart_workload_command(
        self,
        ctx: CommandContext[KubernetesGetPayload],
        *,
        label: str,
    ) -> JsonObject:
        spec = ctx.kubernetes_spec
        result = await ctx.kubernetes.patch_namespaced_resource(
            api_group=spec.api_group,
            version=spec.version,
            namespace=ctx.payload.namespace,
            resource=spec.resource,
            name=ctx.payload.name,
            body=build_rollout_restart_patch(),
        )
        return ctx.ok(
            f"kubernetes {label} restarted",
            applied=True,
            result=result,
        )

    @command.k8s(
        Command.KUBERNETES_DEPLOYMENT_ROLLBACK_ACTION,
        api_group="apps",
        version="v1",
        resource="deployments",
        verb="patch",
        scope="user-workload",
        payload_model=KubernetesWorkloadRollbackPayload,
    )
    async def rollback_deployment_command(
        self,
        ctx: CommandContext[KubernetesWorkloadRollbackPayload],
    ) -> JsonObject:
        return await self.rollback_workload_command(ctx, revision_resource="replicasets")

    @command.k8s(
        Command.KUBERNETES_STATEFULSET_ROLLBACK_ACTION,
        api_group="apps",
        version="v1",
        resource="statefulsets",
        verb="patch",
        scope="user-workload",
        payload_model=KubernetesWorkloadRollbackPayload,
    )
    async def rollback_statefulset_command(
        self,
        ctx: CommandContext[KubernetesWorkloadRollbackPayload],
    ) -> JsonObject:
        return await self.rollback_workload_command(ctx, revision_resource="controllerrevisions")

    @command.k8s(
        Command.KUBERNETES_DAEMONSET_ROLLBACK_ACTION,
        api_group="apps",
        version="v1",
        resource="daemonsets",
        verb="patch",
        scope="user-workload",
        payload_model=KubernetesWorkloadRollbackPayload,
    )
    async def rollback_daemonset_command(
        self,
        ctx: CommandContext[KubernetesWorkloadRollbackPayload],
    ) -> JsonObject:
        return await self.rollback_workload_command(ctx, revision_resource="controllerrevisions")

    async def rollback_workload_command(
        self,
        ctx: CommandContext[KubernetesWorkloadRollbackPayload],
        *,
        revision_resource: str,
    ) -> JsonObject:
        spec = ctx.kubernetes_spec
        payload = ctx.payload
        workload = await ctx.kubernetes.get_namespaced_resource(
            api_group=spec.api_group,
            version=spec.version,
            namespace=payload.namespace,
            resource=spec.resource,
            name=payload.name,
        )
        validate_exact_resource(
            workload,
            payload.workload_ref,
            payload.workload_resource_version,
        )
        target = await ctx.kubernetes.get_namespaced_resource(
            api_group="apps",
            version="v1",
            namespace=payload.namespace,
            resource=revision_resource,
            name=payload.target_revision_ref.name,
        )
        validate_exact_resource(
            target,
            payload.target_revision_ref,
            payload.target_revision_resource_version,
        )
        template = rollback_template_from_revision(
            target,
            workload=payload.workload_ref,
            expected_revision=payload.target_revision,
        )
        if (
            workload_template_sha256(template) != payload.target_template_sha256
            or template != payload.target_template
        ):
            raise ValueError("selected workload revision template is stale")
        workload_spec = workload.get("spec")
        current_template = (
            workload_spec.get("template") if isinstance(workload_spec, dict) else None
        )
        if (
            isinstance(current_template, dict)
            and workload_template_sha256(current_template) == payload.target_template_sha256
        ):
            raise ValueError("selected workload revision is already current")
        result = await ctx.kubernetes.patch_namespaced_resource(
            api_group=spec.api_group,
            version=spec.version,
            namespace=payload.namespace,
            resource=spec.resource,
            name=payload.name,
            body={
                "metadata": {"resourceVersion": payload.workload_resource_version},
                "spec": {"template": template},
            },
        )
        return ctx.ok(
            "kubernetes workload revision restored",
            applied=True,
            revision=payload.target_revision,
            partial_failure=False,
            result=result,
        )

    @command.k8s(
        Command.KUBERNETES_NODE_CORDON_ACTION,
        api_group="core",
        version="v1",
        resource="nodes",
        verb="patch",
        scope="cluster-workload",
        payload_model=KubernetesNodeSchedulingPayload,
    )
    async def cordon_node_command(
        self,
        ctx: CommandContext[KubernetesNodeSchedulingPayload],
    ) -> JsonObject:
        return await self.set_node_unschedulable(ctx, expected=True)

    @command.k8s(
        Command.KUBERNETES_NODE_UNCORDON_ACTION,
        api_group="core",
        version="v1",
        resource="nodes",
        verb="patch",
        scope="cluster-workload",
        payload_model=KubernetesNodeSchedulingPayload,
    )
    async def uncordon_node_command(
        self,
        ctx: CommandContext[KubernetesNodeSchedulingPayload],
    ) -> JsonObject:
        return await self.set_node_unschedulable(ctx, expected=False)

    async def set_node_unschedulable(
        self,
        ctx: CommandContext[KubernetesNodeSchedulingPayload],
        *,
        expected: bool,
    ) -> JsonObject:
        if ctx.payload.unschedulable is not expected:
            return ctx.fail("node scheduling action does not match requested state")
        spec = ctx.kubernetes_spec
        result = await ctx.kubernetes.patch_cluster_resource(
            api_group=spec.api_group,
            version=spec.version,
            resource=spec.resource,
            name=ctx.payload.name,
            body={"spec": {"unschedulable": expected}},
        )
        state = "cordoned" if expected else "uncordoned"
        return ctx.ok(
            f"kubernetes node {state}",
            applied=True,
            unschedulable=expected,
            result=result,
        )

    @command.k8s(
        Command.KUBERNETES_NODE_DRAIN_ACTION,
        api_group="core",
        version="v1",
        resource="nodes",
        verb="patch",
        scope="resource-maintenance",
        payload_model=KubernetesNodeDrainPayload,
    )
    async def drain_node_command(
        self,
        ctx: CommandContext[KubernetesNodeDrainPayload],
    ) -> JsonObject:
        node = await ctx.kubernetes.get_cluster_resource(
            api_group="core",
            version="v1",
            resource="nodes",
            name=ctx.payload.name,
        )
        validate_exact_resource(node, ctx.payload.node_ref, ctx.payload.node_resource_version)
        cancel_requested = ctx.metadata.get("cooperative_cancel_requested")
        if isinstance(cancel_requested, asyncio.Event) and cancel_requested.is_set():
            return self.resource_maintenance_cancelled("node drain cancelled before cordon")
        pod_page = await ctx.kubernetes.list_cluster_resources(
            api_group="core",
            version="v1",
            resource="pods",
            query={
                "fieldSelector": f"spec.nodeName={ctx.payload.name}",
                "limit": str(ctx.payload.max_pods + 1),
            },
        )
        raw_items = pod_page.get("items")
        items = (
            [item for item in raw_items if isinstance(item, dict)]
            if isinstance(raw_items, list)
            else []
        )
        metadata = pod_page.get("metadata")
        continuation = metadata.get("continue") if isinstance(metadata, dict) else None
        if continuation or len(items) > ctx.payload.max_pods:
            return ctx.fail("node drain pod set exceeds the bounded operation limit")
        eligible: list[JsonObject] = []
        skipped: list[JsonObject] = []
        for pod in items:
            reason = self.node_drain_skip_reason(
                pod,
                force=ctx.payload.force,
                delete_empty_dir_data=ctx.payload.delete_empty_dir_data,
            )
            identity = self.pod_operation_identity(pod)
            if reason is None:
                eligible.append(pod)
            else:
                skipped.append({**identity, "reason": reason})
        await ctx.kubernetes.patch_cluster_resource(
            api_group="core",
            version="v1",
            resource="nodes",
            name=ctx.payload.name,
            body={
                "metadata": {"resourceVersion": ctx.payload.node_resource_version},
                "spec": {"unschedulable": True},
            },
        )
        results: list[JsonObject] = []
        try:
            cancelled_during_eviction = await asyncio.wait_for(
                self.evict_node_pods_until_cancel(ctx, eligible, cancel_requested, results),
                timeout=ctx.payload.timeout_seconds,
            )
        except TimeoutError:
            self.mark_pending_node_drain_results(
                eligible,
                results,
                status="failed",
                error="TimeoutError",
            )
            await self.report_node_drain_progress(ctx, results, total=len(eligible))
            evicted = sum(item.get("status") == "evicted" for item in results)
            failed = sum(item.get("status") == "failed" for item in results)
            return ctx.fail(
                "node drain timed out after cordon",
                applied=True,
                evicted=evicted,
                failed=failed,
                skipped=len(skipped),
                partial_failure=True,
                resources=[*skipped, *results],
            )
        await self.report_node_drain_progress(ctx, results, total=len(eligible))
        evicted = sum(item.get("status") == "evicted" for item in results)
        failed = sum(item.get("status") == "failed" for item in results)
        resources = [*skipped, *results]
        if cancelled_during_eviction:
            return {
                **self.resource_maintenance_cancelled(
                    "node drain cancelled during eviction",
                    applied=True,
                ),
                Gateway.RESOURCES: resources,
                "evicted": evicted,
                "failed": failed,
                "skipped": len(skipped),
                "partial_failure": True,
            }
        if failed:
            return ctx.fail(
                "kubernetes node drain completed with failed evictions",
                applied=True,
                resources=resources,
                evicted=evicted,
                failed=failed,
                skipped=len(skipped),
                partial_failure=True,
            )
        return ctx.ok(
            "kubernetes node drain completed",
            applied=True,
            resources=resources,
            evicted=evicted,
            failed=failed,
            skipped=len(skipped),
            partial_failure=failed > 0,
        )

    async def evict_node_pods_until_cancel(
        self,
        ctx: CommandContext[KubernetesNodeDrainPayload],
        pods: list[JsonObject],
        cancel_requested: object,
        results: list[JsonObject],
    ) -> bool:
        eviction_task = asyncio.create_task(
            self.evict_node_pods(ctx, pods, cancel_requested, results)
        )
        cancel_task: asyncio.Task[bool] | None = None
        try:
            if not isinstance(cancel_requested, asyncio.Event):
                await eviction_task
                return False
            cancel_task = asyncio.create_task(cancel_requested.wait())
            done, _pending = await asyncio.wait(
                {eviction_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if eviction_task in done:
                await eviction_task
                return False
            eviction_task.cancel()
            with suppress(asyncio.CancelledError):
                await eviction_task
            self.mark_pending_node_drain_results(
                pods,
                results,
                status="cancelled",
            )
            return True
        finally:
            if not eviction_task.done():
                eviction_task.cancel()
                with suppress(asyncio.CancelledError):
                    await eviction_task
            if cancel_task is not None and not cancel_task.done():
                cancel_task.cancel()
                with suppress(asyncio.CancelledError):
                    await cancel_task

    async def evict_node_pods(
        self,
        ctx: CommandContext[KubernetesNodeDrainPayload],
        pods: list[JsonObject],
        cancel_requested: object,
        results: list[JsonObject],
    ) -> None:
        semaphore = asyncio.Semaphore(ctx.payload.max_parallel)

        async def evict(pod: JsonObject) -> None:
            identity = self.pod_operation_identity(pod)
            async with semaphore:
                if isinstance(cancel_requested, asyncio.Event) and cancel_requested.is_set():
                    results.append({**identity, "status": "cancelled"})
                    return
                try:
                    await ctx.kubernetes.create_namespaced_subresource(
                        api_group="core",
                        version="v1",
                        namespace=str(identity["namespace"]),
                        resource="pods",
                        name=str(identity["name"]),
                        subresource="eviction",
                        body={
                            "apiVersion": "policy/v1",
                            "kind": "Eviction",
                            "metadata": {
                                "namespace": identity["namespace"],
                                "name": identity["name"],
                            },
                            "deleteOptions": {
                                "preconditions": {
                                    "uid": identity["uid"],
                                    "resourceVersion": identity["resource_version"],
                                }
                            },
                        },
                    )
                    results.append({**identity, "status": "evicted"})
                except Exception as exc:
                    results.append({**identity, "status": "failed", "error": type(exc).__name__})

        await asyncio.gather(*(evict(pod) for pod in pods))
        results.sort(key=lambda item: (str(item.get("namespace")), str(item.get("name"))))

    def mark_pending_node_drain_results(
        self,
        pods: list[JsonObject],
        results: list[JsonObject],
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        completed = {(item.get("namespace"), item.get("name"), item.get("uid")) for item in results}
        for pod in pods:
            identity = self.pod_operation_identity(pod)
            key = (identity.get("namespace"), identity.get("name"), identity.get("uid"))
            if key in completed:
                continue
            result = {**identity, "status": status}
            if error is not None:
                result["error"] = error
            results.append(result)
        results.sort(key=lambda item: (str(item.get("namespace")), str(item.get("name"))))

    async def report_node_drain_progress(
        self,
        ctx: CommandContext[KubernetesNodeDrainPayload],
        results: list[JsonObject],
        *,
        total: int,
    ) -> None:
        reporter = ctx.metadata.get("operation_progress_reporter")
        if not callable(reporter):
            return
        for batch_index, offset in enumerate(range(0, len(results), 64), start=1):
            resources = [
                {
                    "namespace": str(item["namespace"]),
                    "name": str(item["name"]),
                    "uid": str(item["uid"]),
                    "resource_version": str(item["resource_version"]),
                    "status": str(item["status"]),
                    "error_code": (str(item["error"]) if item.get("error") is not None else None),
                }
                for item in results[offset : offset + 64]
            ]
            progress: JsonObject = {
                "progress_id": f"node-drain-batch-{batch_index}",
                "phase": "node_drain_evictions",
                "completed": min(offset + len(resources), total),
                "total": total,
                "resources": resources,
            }
            for attempt in range(3):
                try:
                    await reporter(progress)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0)

    @command.k8s(
        Command.KUBERNETES_POD_DEBUG_ACTION,
        api_group="core",
        version="v1",
        resource="pods",
        verb="patch",
        scope="resource-maintenance",
        payload_model=KubernetesPodDebugPayload,
    )
    async def debug_pod_command(
        self,
        ctx: CommandContext[KubernetesPodDebugPayload],
    ) -> JsonObject:
        pod = await ctx.kubernetes.get_namespaced_resource(
            api_group="core",
            version="v1",
            namespace=ctx.payload.namespace,
            resource="pods",
            name=ctx.payload.name,
        )
        validate_exact_resource(pod, ctx.payload.pod_ref, ctx.payload.pod_resource_version)
        spec = pod.get("spec")
        if not isinstance(spec, dict):
            return ctx.fail("selected Pod spec is unavailable")
        containers = spec.get("containers")
        if not isinstance(containers, list) or ctx.payload.target_container not in {
            str(item.get("name")) for item in containers if isinstance(item, dict)
        }:
            return ctx.fail("selected Pod target container is stale")
        all_lists = (containers, spec.get("initContainers"), spec.get("ephemeralContainers"))
        names = {
            str(item.get("name"))
            for values in all_lists
            if isinstance(values, list)
            for item in values
            if isinstance(item, dict)
        }
        if ctx.payload.container_name in names:
            return ctx.fail("debug container identity already exists")
        existing = spec.get("ephemeralContainers")
        ephemeral = list(existing) if isinstance(existing, list) else []
        ephemeral.append(
            {
                "name": ctx.payload.container_name,
                "image": ctx.payload.image,
                "targetContainerName": ctx.payload.target_container,
                "stdin": True,
                "tty": True,
            }
        )
        result = await ctx.kubernetes.patch_namespaced_resource(
            api_group="core",
            version="v1",
            namespace=ctx.payload.namespace,
            resource="pods",
            name=ctx.payload.name,
            subresource="ephemeralcontainers",
            body={
                "metadata": {"resourceVersion": ctx.payload.pod_resource_version},
                "spec": {"ephemeralContainers": ephemeral},
            },
        )
        return ctx.ok(
            "ephemeral debug container attached",
            applied=True,
            namespace=ctx.payload.namespace,
            pod=ctx.payload.name,
            container_name=ctx.payload.container_name,
            target_container=ctx.payload.target_container,
            terminal={
                "namespace": ctx.payload.namespace,
                "pod": ctx.payload.name,
                "container": ctx.payload.container_name,
            },
            result=result,
        )

    @command.k8s(
        Command.KUBERNETES_NODE_DEBUG_ACTION,
        api_group="core",
        version="v1",
        resource="pods",
        verb="create",
        scope="resource-maintenance",
        payload_model=KubernetesNodeDebugPayload,
    )
    async def debug_node_command(
        self,
        ctx: CommandContext[KubernetesNodeDebugPayload],
    ) -> JsonObject:
        await self.require_exact_debug_node(ctx)
        result = await ctx.kubernetes.create_namespaced_resource(
            api_group="core",
            version="v1",
            namespace=ctx.payload.namespace,
            resource="pods",
            body=self.node_debug_pod_manifest(ctx.payload),
        )
        return ctx.ok(
            "node debug pod created",
            applied=True,
            namespace=ctx.payload.namespace,
            pod=ctx.payload.debug_pod_name,
            container_name="debugger",
            session_id=ctx.payload.session_id,
            terminal={
                "namespace": ctx.payload.namespace,
                "pod": ctx.payload.debug_pod_name,
                "container": "debugger",
            },
            result=result,
        )

    @command.k8s(
        Command.KUBERNETES_NODE_DEBUG_CLEANUP_ACTION,
        api_group="core",
        version="v1",
        resource="pods",
        verb="delete",
        scope="resource-maintenance",
        payload_model=KubernetesNodeDebugCleanupPayload,
    )
    async def cleanup_node_debug_command(
        self,
        ctx: CommandContext[KubernetesNodeDebugCleanupPayload],
    ) -> JsonObject:
        await self.require_exact_debug_node(ctx)
        pod = await ctx.kubernetes.get_namespaced_resource(
            api_group="core",
            version="v1",
            namespace=ctx.payload.namespace,
            resource="pods",
            name=ctx.payload.debug_pod_name,
        )
        metadata = pod.get("metadata")
        labels = metadata.get("labels") if isinstance(metadata, dict) else None
        spec = pod.get("spec")
        if (
            not isinstance(metadata, dict)
            or not isinstance(labels, dict)
            or not isinstance(spec, dict)
            or labels.get("opsia.io/debug-session") != ctx.payload.session_id
            or labels.get("opsia.io/node-uid") != ctx.payload.node_ref.uid
            or spec.get("nodeName") != ctx.payload.name
        ):
            return ctx.fail("node debug pod ownership is stale")
        uid = str(metadata.get("uid") or "")
        resource_version = str(metadata.get("resourceVersion") or "")
        if not uid or not resource_version:
            return ctx.fail("node debug pod identity is incomplete")
        await ctx.kubernetes.delete_namespaced_resource(
            api_group="core",
            version="v1",
            namespace=ctx.payload.namespace,
            resource="pods",
            name=ctx.payload.debug_pod_name,
            preconditions={"uid": uid, "resourceVersion": resource_version},
            propagation_policy="Background",
        )
        return ctx.ok(
            "node debug pod cleaned up",
            applied=True,
            namespace=ctx.payload.namespace,
            pod=ctx.payload.debug_pod_name,
            session_id=ctx.payload.session_id,
        )

    async def require_exact_debug_node(
        self,
        ctx: CommandContext[KubernetesNodeDebugPayload | KubernetesNodeDebugCleanupPayload],
    ) -> JsonObject:
        node = await ctx.kubernetes.get_cluster_resource(
            api_group="core",
            version="v1",
            resource="nodes",
            name=ctx.payload.name,
        )
        validate_exact_resource(node, ctx.payload.node_ref, ctx.payload.node_resource_version)
        return node

    @staticmethod
    def node_debug_pod_manifest(payload: KubernetesNodeDebugPayload) -> JsonObject:
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": payload.debug_pod_name,
                "namespace": payload.namespace,
                "labels": {
                    "opsia.io/debug-session": payload.session_id,
                    "opsia.io/node-uid": payload.node_ref.uid,
                },
            },
            "spec": {
                "nodeName": payload.name,
                "restartPolicy": "Never",
                "hostPID": True,
                "hostNetwork": True,
                "tolerations": [{"operator": "Exists"}],
                "containers": [
                    {
                        "name": "debugger",
                        "image": payload.image,
                        "stdin": True,
                        "tty": True,
                        "securityContext": {"privileged": True},
                        "volumeMounts": [{"name": "host-root", "mountPath": "/host"}],
                    }
                ],
                "volumes": [{"name": "host-root", "hostPath": {"path": "/"}}],
            },
        }

    @staticmethod
    def pod_operation_identity(pod: JsonObject) -> JsonObject:
        metadata = pod.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("drain Pod metadata is unavailable")
        identity = {
            "namespace": str(metadata.get("namespace") or ""),
            "name": str(metadata.get("name") or ""),
            "uid": str(metadata.get("uid") or ""),
            "resource_version": str(metadata.get("resourceVersion") or ""),
        }
        if not all(identity.values()):
            raise ValueError("drain Pod identity is incomplete")
        return identity

    @staticmethod
    def node_drain_skip_reason(
        pod: JsonObject,
        *,
        force: bool,
        delete_empty_dir_data: bool,
    ) -> str | None:
        metadata = pod.get("metadata")
        spec = pod.get("spec")
        status = pod.get("status")
        if not isinstance(metadata, dict) or not isinstance(spec, dict):
            return "identity-incomplete"
        owners = metadata.get("ownerReferences")
        owner_kinds = (
            {str(item.get("kind") or "").casefold() for item in owners if isinstance(item, dict)}
            if isinstance(owners, list)
            else set()
        )
        annotations = metadata.get("annotations")
        if "daemonset" in owner_kinds:
            return "daemonset"
        if isinstance(annotations, dict) and "kubernetes.io/config.mirror" in annotations:
            return "static-pod"
        if not owners and not force:
            return "unmanaged"
        volumes = spec.get("volumes")
        if (
            not delete_empty_dir_data
            and isinstance(volumes, list)
            and any(isinstance(item, dict) and "emptyDir" in item for item in volumes)
        ):
            return "empty-dir"
        if isinstance(status, dict) and status.get("phase") in {"Succeeded", "Failed"}:
            return "terminal"
        return None

    def resource_maintenance_cancelled(
        self,
        message: str,
        *,
        applied: bool = False,
    ) -> JsonObject:
        return {
            Gateway.STATUS: CommandStatus.CANCELLED,
            Gateway.CLUSTER_ID: self.cluster_id,
            Gateway.APPLIED: applied,
            Gateway.MESSAGE: message,
            Gateway.RETRYABLE: False,
            Gateway.RESOURCES: [],
            Gateway.STDOUT: "",
            Gateway.STDERR: "",
        }

    @command.k8s(
        Command.KUBERNETES_CRONJOB_TRIGGER_ACTION,
        api_group="batch",
        version="v1",
        resource="jobs",
        verb="create",
        scope="user-workload",
        payload_model=KubernetesCronJobPayload,
    )
    async def trigger_cronjob_command(
        self,
        ctx: CommandContext[KubernetesCronJobPayload],
    ) -> JsonObject:
        cronjob = await ctx.kubernetes.get_namespaced_resource(
            api_group="batch",
            version="v1",
            namespace=ctx.payload.namespace,
            resource="cronjobs",
            name=ctx.payload.name,
        )
        validate_cronjob_resource_ref(cronjob, ctx.payload.resource_ref)
        result = await ctx.kubernetes.create_namespaced_resource(
            api_group="batch",
            version="v1",
            namespace=ctx.payload.namespace,
            resource="jobs",
            body=cronjob_job_body(
                cronjob,
                namespace=ctx.payload.namespace,
                name=ctx.payload.name,
            ),
        )
        return ctx.ok("CronJob triggered", applied=True, result=result)

    @command.k8s(
        Command.KUBERNETES_CRONJOB_SUSPEND_ACTION,
        api_group="batch",
        version="v1",
        resource="cronjobs",
        verb="patch",
        scope="user-workload",
        payload_model=KubernetesCronJobPayload,
    )
    async def suspend_cronjob_command(
        self,
        ctx: CommandContext[KubernetesCronJobPayload],
    ) -> JsonObject:
        return await self.set_cronjob_suspended(ctx, suspended=True)

    @command.k8s(
        Command.KUBERNETES_CRONJOB_RESUME_ACTION,
        api_group="batch",
        version="v1",
        resource="cronjobs",
        verb="patch",
        scope="user-workload",
        payload_model=KubernetesCronJobPayload,
    )
    async def resume_cronjob_command(
        self,
        ctx: CommandContext[KubernetesCronJobPayload],
    ) -> JsonObject:
        return await self.set_cronjob_suspended(ctx, suspended=False)

    async def set_cronjob_suspended(
        self,
        ctx: CommandContext[KubernetesCronJobPayload],
        *,
        suspended: bool,
    ) -> JsonObject:
        cronjob = await ctx.kubernetes.get_namespaced_resource(
            api_group="batch",
            version="v1",
            namespace=ctx.payload.namespace,
            resource="cronjobs",
            name=ctx.payload.name,
        )
        validate_cronjob_resource_ref(cronjob, ctx.payload.resource_ref)
        result = await ctx.kubernetes.patch_namespaced_resource(
            api_group="batch",
            version="v1",
            namespace=ctx.payload.namespace,
            resource="cronjobs",
            name=ctx.payload.name,
            body={"spec": {"suspend": suspended}},
        )
        state = "suspended" if suspended else "resumed"
        return ctx.ok(f"CronJob {state}", applied=True, result=result)

    @command.k8s(
        KUBERNETES_CONFIGMAP_PATCH_ACTION,
        api_group="core",
        version="v1",
        resource="configmaps",
        verb="patch",
        payload_model=KubernetesPatchPayload,
    )
    async def patch_configmap_command(
        self,
        ctx: CommandContext[KubernetesPatchPayload],
    ) -> JsonObject:
        spec = ctx.kubernetes_spec
        result = await ctx.kubernetes.patch_namespaced_resource(
            api_group=spec.api_group,
            version=spec.version,
            namespace=ctx.payload.namespace,
            resource=spec.resource,
            name=ctx.payload.name,
            body=ctx.payload.patch_body(),
        )
        return ctx.ok("kubernetes configmap patched", applied=True, result=result)

    async def apply_default_command(self, ctx: CommandContext[JsonObject]) -> JsonObject:
        return ctx.fail(f"unsupported action: {ctx.action}")

    @command.handler(
        Command.CLUSTER_AGENT_UNINSTALL_ACTION,
        payload_model=ClusterAgentUninstallPayload,
    )
    async def schedule_agent_uninstall_command(
        self,
        ctx: CommandContext[ClusterAgentUninstallPayload],
    ) -> JsonObject:
        if ctx.cluster_role == MANAGEMENT_CLUSTER_ROLE:
            return ctx.fail(MANAGEMENT_READONLY_CODE)
        if ctx.payload.cluster_id != self.cluster_id:
            return ctx.fail("uninstall cluster_id does not match agent identity")
        if ctx.payload.contract_version != UNINSTALL_CONTRACT_VERSION:
            return ctx.fail("unsupported agent uninstall contract version")
        await self.prepare_agent_installation_cleanup()
        await self.arm_agent_installation_cleanup()
        return ctx.ok(
            "allowlisted agent runtime cleanup accepted with no residual resources",
            applied=True,
            cleanup_completed=True,
            cleanup_resources=list(UNINSTALL_CLEANUP_RESOURCE_REFS),
            residual_resources=[],
        )

    async def prepare_agent_installation_cleanup(self) -> None:
        """Delete exact non-final Opsia resources before reporting completion.

        Any failure propagates into a FAILED command result and leaves the agent
        Deployment running.  The server therefore never revokes registration on
        a merely scheduled or partially applied cleanup.
        """

        for item in PRE_ACK_NAMESPACED_CLEANUP:
            await self.kubernetes.delete_namespaced_resource(
                api_group=item.api_group,
                version=item.version,
                namespace=item.namespace,
                resource=item.resource,
                name=item.name,
            )
        for item in PRE_ACK_CLUSTER_CLEANUP:
            await self.kubernetes.delete_cluster_resource(
                api_group=item.api_group,
                version=item.version,
                resource=item.resource,
                name=item.name,
            )

    async def arm_agent_installation_cleanup(self) -> None:
        """Attach final resources to the uninstall owner without deleting it.

        The result is not yet ACKed at this point, so the owner ClusterRole and
        agent Deployment must remain alive while the durable outbox retries.
        """

        owner = await self.kubernetes.get_cluster_resource(
            api_group=FINAL_UNINSTALL_CLUSTER_ROLE.api_group,
            version=FINAL_UNINSTALL_CLUSTER_ROLE.version,
            resource=FINAL_UNINSTALL_CLUSTER_ROLE.resource,
            name=FINAL_UNINSTALL_CLUSTER_ROLE.name,
        )
        metadata = owner.get("metadata")
        uid = str(metadata.get("uid") or "") if isinstance(metadata, dict) else ""
        if not uid:
            raise RuntimeError("uninstall ClusterRole UID is unavailable")
        owner_reference = {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "name": FINAL_UNINSTALL_CLUSTER_ROLE.name,
            "uid": uid,
            "controller": False,
            "blockOwnerDeletion": False,
        }
        owner_patch = {"metadata": {"ownerReferences": [owner_reference]}}
        for item in FINAL_CASCADE_NAMESPACED_CLEANUP:
            await self.kubernetes.patch_namespaced_resource(
                api_group=item.api_group,
                version=item.version,
                namespace=item.namespace,
                resource=item.resource,
                name=item.name,
                body=owner_patch,
            )
        for item in FINAL_CASCADE_CLUSTER_CLEANUP:
            await self.kubernetes.patch_cluster_resource(
                api_group=item.api_group,
                version=item.version,
                resource=item.resource,
                name=item.name,
                body=owner_patch,
            )

    async def finalize_agent_installation_cleanup(self) -> None:
        """Delete the armed owner only after the management plane ACKs result."""

        result = await self.kubernetes.delete_cluster_resource(
            api_group=FINAL_UNINSTALL_CLUSTER_ROLE.api_group,
            version=FINAL_UNINSTALL_CLUSTER_ROLE.version,
            resource=FINAL_UNINSTALL_CLUSTER_ROLE.resource,
            name=FINAL_UNINSTALL_CLUSTER_ROLE.name,
            propagation_policy="Background",
        )
        if result.get("deleted") is not True and result.get("status_code") != 404:
            raise RuntimeError("uninstall ClusterRole cleanup was not accepted")

    def command_payload(self, command: CommandRecord) -> JsonObject:
        payload = command.get(Gateway.PAYLOAD)
        if not isinstance(payload, dict):
            return dict(command)
        action = str(command.get(Gateway.ACTION, ""))
        if action in {
            AgentConfig.APPLY_MANIFEST_ACTION,
            AgentConfig.ROLLOUT_RESTART_ACTION,
        } and isinstance(payload.get("diff"), dict):
            return payload
        nested_payload = payload.get(Gateway.PAYLOAD)
        return nested_payload if isinstance(nested_payload, dict) else payload

    def query_definition_from_payload(self, payload: JsonObject) -> TelemetryQueryDefinition:
        query_value = payload.get("query")
        query = query_value if isinstance(query_value, dict) else payload
        if not isinstance(query, dict):
            raise ValueError("telemetry query command requires a query object")
        if not isinstance(query.get("query"), str):
            source = query.get("source")
            name = query.get("name")
            if isinstance(source, str) and isinstance(name, str):
                return self.query_registry.get(source, name)
        return TelemetryQueryDefinition.from_mapping(query)

    @command.handler(
        Command.KUBERNETES_RESOURCE_DELETE_ACTION,
        payload_model=ResourceDeleteCommandPayload,
    )
    async def delete_resource_command(
        self,
        ctx: CommandContext[ResourceDeleteCommandPayload],
    ) -> JsonObject:
        results: list[JsonObject] = []
        successes = 0
        cancel_requested = ctx.metadata.get("cooperative_cancel_requested")
        for target in ctx.payload.resources:
            if isinstance(cancel_requested, asyncio.Event) and cancel_requested.is_set():
                return {
                    Gateway.STATUS: CommandStatus.CANCELLED,
                    Gateway.CLUSTER_ID: self.cluster_id,
                    Gateway.APPLIED: successes > 0,
                    Gateway.MESSAGE: "resource delete cancelled at a safe target boundary",
                    Gateway.RETRYABLE: False,
                    Gateway.RESOURCES: results,
                    Gateway.STDOUT: "",
                    Gateway.STDERR: "",
                    "completeness": "partial" if results else "unavailable",
                    "request_fingerprint": ctx.payload.request_fingerprint,
                }
            try:
                current = await self.get_exact_delete_target(target)
                self.require_exact_delete_target(current, target)
                preconditions = {
                    "uid": target.uid,
                    "resourceVersion": target.resource_version,
                }
                if target.namespace is None:
                    await ctx.kubernetes.delete_cluster_resource(
                        api_group=target.api_group,
                        version=target.version,
                        resource=target.plural,
                        name=target.name,
                        preconditions=preconditions,
                        propagation_policy=ctx.payload.propagation_policy,
                    )
                else:
                    await ctx.kubernetes.delete_namespaced_resource(
                        api_group=target.api_group,
                        version=target.version,
                        namespace=target.namespace,
                        resource=target.plural,
                        name=target.name,
                        preconditions=preconditions,
                        propagation_policy=ctx.payload.propagation_policy,
                    )
                successes += 1
                results.append(self.delete_target_result(target, status="deleted"))
            except Exception as exc:
                results.append(
                    self.delete_target_result(
                        target,
                        status="failed",
                        error=str(exc),
                    )
                )
        if successes != len(ctx.payload.resources):
            return ctx.fail(
                f"resource delete failed for {len(ctx.payload.resources) - successes} target(s)",
                applied=successes > 0,
                resources=results,
                completeness="partial" if successes else "unavailable",
                request_fingerprint=ctx.payload.request_fingerprint,
            )
        return ctx.ok(
            f"resource delete completed for {successes} target(s)",
            applied=True,
            resources=results,
            completeness="exact",
            request_fingerprint=ctx.payload.request_fingerprint,
        )

    async def get_exact_delete_target(
        self,
        target: ExactResourceDeleteTarget,
    ) -> JsonObject:
        if target.namespace is None:
            return await self.kubernetes.get_cluster_resource(
                api_group=target.api_group,
                version=target.version,
                resource=target.plural,
                name=target.name,
            )
        return await self.kubernetes.get_namespaced_resource(
            api_group=target.api_group,
            version=target.version,
            namespace=target.namespace,
            resource=target.plural,
            name=target.name,
        )

    @staticmethod
    def require_exact_delete_target(
        current: JsonObject,
        target: ExactResourceDeleteTarget,
    ) -> None:
        metadata = current.get("metadata")
        meta = metadata if isinstance(metadata, dict) else {}
        expected_api_version = (
            f"{target.api_group}/{target.version}" if target.api_group else target.version
        )
        exact = (
            str(current.get("apiVersion") or "") == expected_api_version
            and str(current.get("kind") or "").casefold() == target.kind.casefold()
            and str(meta.get("namespace") or "") == (target.namespace or "")
            and str(meta.get("name") or "") == target.name
            and str(meta.get("uid") or "") == target.uid
            and str(meta.get("resourceVersion") or "") == target.resource_version
        )
        if not exact:
            raise RuntimeError("resource identity changed before delete")

    @staticmethod
    def delete_target_result(
        target: ExactResourceDeleteTarget,
        *,
        status: str,
        error: str | None = None,
    ) -> JsonObject:
        result: JsonObject = {
            "kind": target.kind,
            "namespace": target.namespace,
            "name": target.name,
            "uid": target.uid,
            "status": status,
        }
        if error is not None:
            result["error"] = error
        return result

    @command.handler(AgentConfig.APPLY_MANIFEST_ACTION)
    async def apply_manifest_command(self, ctx: CommandContext[JsonObject]) -> JsonObject:
        diff = ctx.raw_payload.get("diff", {}) if isinstance(ctx.raw_payload, dict) else {}
        namespace = str(diff.get("namespace") or Sandbox.NAMESPACE)
        nested = ctx.raw_payload.get("payload") if isinstance(ctx.raw_payload, dict) else None
        desired_documents = nested.get("desired_documents") if isinstance(nested, dict) else None
        resource_ref = nested.get("resource_ref") if isinstance(nested, dict) else None
        create_mode = nested.get("create_mode") is True if isinstance(nested, dict) else False
        dry_run = nested.get("dry_run") is True if isinstance(nested, dict) else False
        force = nested.get("force") is True if isinstance(nested, dict) else False
        force_confirmation = (
            nested.get("force_confirmation") is True if isinstance(nested, dict) else False
        )
        field_manager = str(nested.get("field_manager") or "") if isinstance(nested, dict) else ""
        desired_sha256 = str(nested.get("desired_sha256") or "") if isinstance(nested, dict) else ""
        if isinstance(desired_documents, list):
            return await self.apply_manifest_documents(
                desired_documents,
                resource_ref if isinstance(resource_ref, dict) else {},
                namespace,
                create_mode=create_mode,
                dry_run=dry_run,
                force=force,
                force_confirmation=force_confirmation,
                field_manager=field_manager,
                desired_sha256=desired_sha256,
                cancel_requested=ctx.metadata.get("cooperative_cancel_requested"),
            )
        desired_manifest = diff.get("desired_manifest")
        if isinstance(desired_manifest, dict) and desired_manifest:
            applied, message, rollout = await self.apply_kubernetes_manifest(
                desired_manifest,
                namespace,
            )
            return self.command_result(
                applied,
                message,
                resource=str(diff.get("resource", "")),
                rollout=rollout,
            )

        deployment = deployment_name_from_resource(str(diff.get("resource", "")))
        image = str(diff.get("desired_image", ""))
        if not deployment or not image:
            return self.command_result(
                False,
                "apply_manifest requires deployment resource and image",
                resource=str(diff.get("resource", "")),
            )
        if not control_namespace_allowed(namespace):
            return self.command_result(
                False,
                AgentConfig.WRITE_NAMESPACE_DENIED_MESSAGE,
                resource=str(diff.get("resource", "")),
            )
        patch = build_apply_manifest_patch(deployment, image)
        applied, message, rollout = await self.patch_deployment(namespace, deployment, patch)
        return self.command_result(
            applied,
            message,
            resource=str(diff.get("resource", "")),
            rollout=rollout,
        )

    async def apply_manifest_documents(
        self,
        desired_documents: list[object],
        resource_ref: JsonObject,
        fallback_namespace: str,
        *,
        create_mode: bool = False,
        dry_run: bool = False,
        force: bool = False,
        force_confirmation: bool = False,
        field_manager: str = "",
        desired_sha256: str = "",
        cancel_requested: object = None,
    ) -> JsonObject:
        if not desired_documents or len(desired_documents) > 100:
            return self.command_result(False, "apply_manifest desired_documents is invalid")
        expected_uid = str(resource_ref.get("uid") or "")
        expected_kind = str(resource_ref.get("kind") or "")
        expected_name = str(resource_ref.get("name") or "")
        expected_namespace = str(resource_ref.get("namespace") or fallback_namespace)
        if create_mode and force and not force_confirmation:
            return self.command_result(False, "force create requires explicit confirmation")
        if create_mode and field_manager != AgentConfig.MANIFEST_CREATE_FIELD_MANAGER:
            return self.command_result(False, "create field manager is invalid")
        if create_mode and not desired_sha256.startswith("sha256:"):
            return self.command_result(False, "create desired manifest hash is invalid")
        if not create_mode and (not expected_uid or not expected_kind or not expected_name):
            return self.command_result(False, "apply_manifest resource identity is incomplete")

        prepared: list[tuple[JsonObject, KubernetesManifestResource, str | None]] = []
        identities: set[tuple[str, str, str, str]] = set()
        selected_count = 0
        try:
            for value in desired_documents:
                if not isinstance(value, dict):
                    raise ValueError("every desired document must be an object")
                manifest = dict(value)
                resource = kubernetes_manifest_resource(manifest, fallback_namespace)
                if not control_namespace_allowed(resource.namespace):
                    raise ValueError(AgentConfig.WRITE_NAMESPACE_DENIED_MESSAGE)
                identity = (
                    resource.api_version,
                    resource.kind.casefold(),
                    resource.namespace,
                    resource.name,
                )
                if identity in identities:
                    raise ValueError("desired_documents contains duplicate resource identities")
                identities.add(identity)
                selected = not create_mode and (
                    resource.kind.casefold() == expected_kind.casefold()
                    and resource.namespace == expected_namespace
                    and resource.name == expected_name
                )
                selected_count += int(selected)
                prepared.append((resource.manifest, resource, expected_uid if selected else None))
        except ValueError as exc:
            return self.command_result(False, str(exc))
        if not create_mode and selected_count != 1:
            return self.command_result(
                False,
                "desired_documents must contain the exact selected resource once",
            )

        resources: list[JsonObject] = []
        successes = 0
        for manifest, resource, document_uid in prepared:
            if isinstance(cancel_requested, asyncio.Event) and cancel_requested.is_set():
                return {
                    Gateway.STATUS: CommandStatus.CANCELLED,
                    Gateway.CLUSTER_ID: self.cluster_id,
                    Gateway.APPLIED: successes > 0 and not dry_run,
                    Gateway.MESSAGE: "manifest create cancelled at a safe document boundary",
                    Gateway.RETRYABLE: False,
                    Gateway.RESOURCES: resources,
                    Gateway.STDOUT: "",
                    Gateway.STDERR: "",
                    "completeness": "partial" if successes else "unavailable",
                    "dry_run": dry_run,
                    "force": force,
                    "desired_sha256": desired_sha256,
                }
            if create_mode:
                succeeded, message, rollout = await self.create_kubernetes_manifest(
                    manifest,
                    resource.namespace,
                    dry_run=dry_run,
                    force=force,
                    field_manager=field_manager,
                )
            else:
                succeeded, message, rollout = await self.apply_kubernetes_manifest(
                    manifest,
                    resource.namespace,
                    expected_uid=document_uid,
                )
            successes += int(succeeded)
            applied = succeeded and not dry_run
            resources.append(
                {
                    "resource": f"{resource.kind}/{resource.name}",
                    "namespace": resource.namespace,
                    "status": (
                        AgentConfig.COMMAND_COMPLETED_STATUS
                        if succeeded
                        else AgentConfig.COMMAND_FAILED_STATUS
                    ),
                    "applied": applied,
                    "retryable": not succeeded,
                    "message": message,
                    "stdout": sanitize_command_output(message if succeeded else ""),
                    "stderr": sanitize_command_output("" if succeeded else message),
                    "rollout": rollout,
                }
            )
        failures = len(resources) - successes
        completeness = "exact" if failures == 0 else "partial" if successes else "unavailable"
        status = (
            AgentConfig.COMMAND_COMPLETED_STATUS
            if failures == 0
            else AgentConfig.COMMAND_FAILED_STATUS
        )
        return {
            Gateway.STATUS: status,
            Gateway.CLUSTER_ID: self.cluster_id,
            Gateway.APPLIED: successes > 0 and not dry_run,
            Gateway.MESSAGE: (
                "all manifest documents applied"
                if failures == 0
                else f"{successes} of {len(resources)} manifest documents applied"
            ),
            Gateway.RETRYABLE: failures > 0,
            Gateway.RESOURCES: resources,
            Gateway.STDOUT: "",
            Gateway.STDERR: "" if failures == 0 else "one or more manifest documents failed",
            "completeness": completeness,
            "dry_run": dry_run,
            "force": force,
            "desired_sha256": desired_sha256,
        }

    async def create_kubernetes_manifest(
        self,
        manifest: JsonObject,
        fallback_namespace: str,
        *,
        dry_run: bool,
        force: bool,
        field_manager: str,
    ) -> tuple[bool, str, JsonObject]:
        base_url = kubernetes_api_base_url()
        token = service_account_token()
        if not base_url or not token:
            return False, "kubernetes api not configured", {}
        try:
            resource = kubernetes_manifest_resource(manifest, fallback_namespace)
        except ValueError as exc:
            return False, str(exc), {}
        if not control_namespace_allowed(resource.namespace):
            return False, AgentConfig.WRITE_NAMESPACE_DENIED_MESSAGE, {}
        query = "fieldValidation=Strict"
        if dry_run:
            query = f"{query}&dryRun=All"
        async with kubernetes_client(self.kubernetes_transport) as client:
            if force:
                response = await client.patch(
                    f"{resource.resource_url(base_url)}?fieldManager={field_manager}&force=true&{query}",
                    json=resource.manifest,
                    headers=kubernetes_headers(token, "application/apply-patch+yaml"),
                )
                operation = "server dry-run apply" if dry_run else "server-side apply"
            else:
                response = await client.post(
                    f"{resource.collection_url(base_url)}?{query}",
                    json=resource.manifest,
                    headers=kubernetes_headers(token, "application/json"),
                )
                operation = "server dry-run create" if dry_run else "create"
        if response.is_error:
            return False, kubernetes_failure_message(operation, response), {}
        return True, f"Kubernetes {operation} accepted", {}

    @command.handler(Command.RCA_TEST_SCENARIO_INJECT_ACTION)
    async def rca_test_scenario_inject_command(
        self,
        ctx: CommandContext[JsonObject],
    ) -> JsonObject:
        run_id, scenario, expires_at = self.rca_test_command_scenario(ctx.payload)
        observed = await self.inject_rca_test_scenario(scenario, run_id, expires_at)
        return {
            **self.command_result(
                True,
                "RCA test fixture applied and actual fault observed",
                resource=f"rca-test/{scenario.scenario_id}",
            ),
            "rca_test": {
                "run_id": run_id,
                "scenario_id": scenario.scenario_id,
                "scenario_version": scenario.version,
                "evidence_sources": list(scenario.evidence_sources),
                **observed,
            },
        }

    @command.handler(Command.RCA_TEST_SCENARIO_CLEANUP_ACTION)
    async def rca_test_scenario_cleanup_command(
        self,
        ctx: CommandContext[JsonObject],
    ) -> JsonObject:
        run_id, scenario_id, scenario_version, namespace, resource_name, cleanup_adapter = (
            self.rca_test_cleanup_command_target(ctx.payload)
        )
        cleaned = await self.cleanup_rca_test_fixture_if_owned(
            namespace,
            resource_name,
            run_id,
            cleanup_adapter=cleanup_adapter,
        )
        message = (
            "RCA test fixture resources deleted and residuals cleared"
            if cleaned
            else "RCA test fixture owner changed; cleanup safely skipped"
        )
        result = self.command_result(
            True,
            message,
            resource=f"rca-test/{scenario_id}",
        )
        if not cleaned:
            result[Gateway.APPLIED] = False
            for resource in result.get(Gateway.RESOURCES, []):
                if isinstance(resource, dict):
                    resource[Gateway.APPLIED] = False
        return {
            **result,
            "rca_test": {
                "run_id": run_id,
                "scenario_id": scenario_id,
                "scenario_version": scenario_version,
                "cleanup_completed": cleaned,
                "cleanup_status": "completed" if cleaned else "skipped",
            },
        }

    def rca_test_cleanup_command_target(
        self,
        payload: JsonObject,
    ) -> tuple[str, str, int, str, str, str]:
        run_id = str(payload.get("run_id") or "")
        scenario_id = str(payload.get("scenario_id") or "")
        namespace = str(payload.get("namespace") or "")
        resource_name = str(payload.get("resource_name") or "")
        cleanup_adapter = str(payload.get("cleanup_adapter") or "kubernetes.manifest_delete")
        try:
            scenario_version = int(payload.get("scenario_version"))
        except (TypeError, ValueError) as exc:
            raise ValueError("RCA test cleanup requires scenario_version") from exc
        if not run_id or not scenario_id:
            raise ValueError("RCA test cleanup requires run_id and scenario_id")
        validate_rca_test_fixture_target(namespace, resource_name)
        default_test_scenario_adapter_registry().cleanup_adapter(cleanup_adapter)
        return (
            run_id,
            scenario_id,
            scenario_version,
            namespace,
            resource_name,
            cleanup_adapter,
        )

    def rca_test_command_scenario(
        self,
        payload: JsonObject,
    ) -> tuple[str, RcaTestScenario, str]:
        run_id = str(payload.get("run_id") or "")
        scenario_id = str(payload.get("scenario_id") or "")
        try:
            requested_version = int(payload.get("scenario_version"))
        except (TypeError, ValueError) as exc:
            raise ValueError("RCA test command requires scenario_version") from exc
        scenario = test_scenario_by_id(scenario_id)
        if not run_id or scenario is None:
            raise ValueError("unknown or unavailable RCA test scenario")
        verification_mode = payload.get("verification_mode") is True
        if scenario.availability == "verification_pending" and not verification_mode:
            raise ValueError("RCA test verification mode is required for pending scenario")
        if scenario.availability not in {"ready", "verification_pending"}:
            raise ValueError("unknown or unavailable RCA test scenario")
        if scenario.version != requested_version:
            raise ValueError("RCA test scenario version changed; create a new run")
        try:
            validate_scenario_adapter_contracts((scenario,))
            adapter = default_test_scenario_adapter_registry().adapter_for(scenario)
            expected_target = adapter.fixture_target(scenario)
        except TestScenarioContractError as exc:
            raise ValueError("RCA test scenario adapter is not available on target agent") from exc
        requested_namespace = str(payload.get("namespace") or "")
        requested_resource_name = str(payload.get("resource_name") or "")
        validate_rca_test_fixture_target(requested_namespace, requested_resource_name)
        if (
            requested_namespace != expected_target.namespace
            or requested_resource_name != expected_target.resource_name
        ):
            raise ValueError("RCA test scenario target changed; create a new run")
        cleanup_adapter = str(payload.get("cleanup_adapter") or "kubernetes.manifest_delete")
        if cleanup_adapter != scenario.cleanup.adapter:
            raise ValueError("RCA test cleanup adapter changed; create a new run")
        expected_root_cause = str(payload.get("expected_root_cause") or "")
        expected_symptom = str(payload.get("expected_symptom") or "")
        if not expected_root_cause or not expected_symptom:
            raise ValueError("RCA test command requires immutable expectations")
        expires_at = str(payload.get("expires_at") or "")
        parsed_expires_at = parse_approval_expires_at(expires_at)
        if parsed_expires_at is None:
            raise ValueError("RCA test inject expires_at is invalid")
        if parsed_expires_at <= datetime.now(UTC):
            raise ValueError("RCA test inject command is expired")
        return run_id, scenario, expires_at

    async def inject_rca_test_scenario(
        self,
        scenario: RcaTestScenario,
        run_id: str,
        expires_at: str,
    ) -> JsonObject:
        await self.ensure_rca_test_fixture_available(scenario, run_id)
        adapter = default_test_scenario_adapter_registry().adapter_for(scenario)
        manifests = adapter.build_trigger(scenario, run_id, expires_at)
        for manifest in manifests:
            applied, message, _rollout = await self.apply_kubernetes_manifest(
                manifest,
                scenario.safety.namespace,
            )
            if not applied:
                raise RuntimeError(message)
        pod_names = await self.wait_for_rca_test_observation(scenario, run_id)
        resource_name = scenario.trigger.params.resource_name
        return {
            "fault_observed": True,
            "namespace": scenario.safety.namespace,
            "resource_kind": "Deployment",
            "resource_name": resource_name,
            "label_selector": f"kubeheal.io/rca-test-run={run_id}",
            "pod_names": pod_names,
        }

    async def ensure_rca_test_fixture_available(
        self,
        scenario: RcaTestScenario,
        run_id: str,
    ) -> None:
        base_url = kubernetes_api_base_url()
        token = service_account_token()
        if not base_url or not token:
            raise RuntimeError("kubernetes api not configured; RCA test run unavailable")
        adapter = default_test_scenario_adapter_registry().adapter_for(scenario)
        name = adapter.fixture_target(scenario).resource_name
        url = f"{base_url}/apis/apps/v1/namespaces/{scenario.safety.namespace}/deployments/{name}"
        async with kubernetes_client(self.kubernetes_transport) as client:
            response = await client.get(url, headers=kubernetes_headers(token))
        if response.status_code == 404:
            return
        response.raise_for_status()
        current = response.json()
        if not isinstance(current, dict):
            raise RuntimeError("invalid Kubernetes fixture response")
        if rca_test_fixture_owned_by_run(current, run_id):
            return
        spec = current.get("spec")
        spec_body = spec if isinstance(spec, dict) else {}
        if int(spec_body.get("replicas") or 0) == 0 or rca_test_fixture_expired(current):
            return
        raise RuntimeError(f"RCA test scenario already has an active run: {scenario.scenario_id}")

    async def wait_for_rca_test_observation(
        self,
        scenario: RcaTestScenario,
        run_id: str,
    ) -> list[str]:
        provider = self.evidence_collector.providers.get("kubernetes")
        if not isinstance(provider, KubernetesSnapshotProvider):
            raise RuntimeError("Kubernetes evidence provider is unavailable")
        query = KubernetesSnapshotQuery(
            "rca_test_fault_observation",
            "Observe the allowlisted RCA test fixture",
            scenario.safety.namespace,
            f"{RCA_TEST_RUN_LABEL}={run_id}",
        )
        adapter = default_test_scenario_adapter_registry().adapter_for(scenario)
        deadline = time.monotonic() + scenario.observe.timeout_seconds
        while True:
            async with httpx.AsyncClient(timeout=provider.timeout_seconds) as client:
                raw = await provider.query(client, query)
            snapshot = provider.normalize_payload(raw, query)
            if adapter.matches_observation(scenario, snapshot, run_id):
                pod_names = rca_test_run_pod_names(snapshot, run_id)
                if pod_names:
                    return pod_names
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"RCA test fault was not observed before timeout: {scenario.scenario_id}"
                )
            await asyncio.sleep(scenario.observe.poll_seconds)

    async def cleanup_rca_test_fixture_if_owned(
        self,
        namespace: str,
        resource_name: str,
        run_id: str,
        *,
        cleanup_adapter: str = "kubernetes.manifest_delete",
    ) -> bool:
        validate_rca_test_fixture_target(namespace, resource_name)
        if not run_id:
            raise ValueError("RCA test cleanup requires run_id")
        base_url = kubernetes_api_base_url()
        token = service_account_token()
        if not base_url or not token:
            raise RuntimeError("kubernetes api not configured; RCA test cleanup unavailable")
        adapter = default_test_scenario_adapter_registry().cleanup_adapter(cleanup_adapter)
        cleanup_plan = adapter.build_cleanup(namespace, resource_name)
        resource_urls = tuple(
            (resource.kind, resource.url(base_url, namespace, resource_name))
            for resource in cleanup_plan.resources
        )
        async with kubernetes_client(self.kubernetes_transport) as client:
            try:
                resources: list[RcaTestOwnedResource] = []
                for kind, url in resource_urls:
                    resource = await self.rca_test_owned_resource(
                        client,
                        kind=kind,
                        url=url,
                        token=token,
                        run_id=run_id,
                    )
                    if resource is not None:
                        resources.append(resource)
                for resource in resources:
                    deleted = await client.request(
                        "DELETE",
                        resource.url,
                        json={
                            "apiVersion": "v1",
                            "kind": "DeleteOptions",
                            "propagationPolicy": cleanup_plan.propagation_policy,
                            "preconditions": {
                                "uid": resource.uid,
                                "resourceVersion": resource.resource_version,
                            },
                        },
                        headers=kubernetes_headers(token, "application/json"),
                    )
                    if deleted.status_code in RCA_TEST_OWNER_CONFLICT_STATUSES:
                        raise RcaTestFixtureOwnershipChanged(
                            f"RCA test {resource.kind} owner changed during delete"
                        )
                    if deleted.status_code != 404:
                        deleted.raise_for_status()

                await self.wait_for_rca_test_fixture_absent(
                    client,
                    base_url=base_url,
                    token=token,
                    namespace=namespace,
                    resource_name=resource_name,
                    run_id=run_id,
                    resources=resources,
                    cleanup_plan=cleanup_plan,
                )
            except RcaTestFixtureOwnershipChanged:
                return False
        return True

    async def rca_test_owned_resource(
        self,
        client: httpx.AsyncClient,
        *,
        kind: str,
        url: str,
        token: str,
        run_id: str,
    ) -> RcaTestOwnedResource | None:
        response = await client.get(url, headers=kubernetes_headers(token))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError(f"invalid RCA test {kind} response")
        if not rca_test_fixture_owned_by_run(body, run_id):
            raise RcaTestFixtureOwnershipChanged(f"RCA test {kind} owner changed")
        metadata = body.get("metadata")
        meta = metadata if isinstance(metadata, dict) else {}
        uid = str(meta.get("uid") or "")
        resource_version = str(meta.get("resourceVersion") or "")
        if not uid or not resource_version:
            raise RuntimeError(f"RCA test {kind} cleanup requires UID and resourceVersion")
        return RcaTestOwnedResource(
            kind=kind,
            url=url,
            uid=uid,
            resource_version=resource_version,
        )

    async def wait_for_rca_test_fixture_absent(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        token: str,
        namespace: str,
        resource_name: str,
        run_id: str,
        resources: list[RcaTestOwnedResource],
        cleanup_plan: RcaTestCleanupPlan,
    ) -> None:
        deadline = time.monotonic() + RCA_TEST_CLEANUP_TIMEOUT_SECONDS
        expected_uids = {resource.kind: resource.uid for resource in resources}
        while True:
            residuals = await self.rca_test_cleanup_residuals(
                client,
                base_url=base_url,
                token=token,
                namespace=namespace,
                resource_name=resource_name,
                run_id=run_id,
                expected_uids=expected_uids,
                cleanup_plan=cleanup_plan,
            )
            if not residuals:
                return
            if time.monotonic() >= deadline:
                joined = ", ".join(sorted(residuals))
                raise TimeoutError(f"RCA test cleanup residuals did not disappear: {joined}")
            await asyncio.sleep(RCA_TEST_CLEANUP_POLL_SECONDS)

    async def rca_test_cleanup_residuals(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        token: str,
        namespace: str,
        resource_name: str,
        run_id: str,
        expected_uids: dict[str, str],
        cleanup_plan: RcaTestCleanupPlan,
    ) -> set[str]:
        headers = kubernetes_headers(token)
        urls = {
            resource.kind: resource.url(base_url, namespace, resource_name)
            for resource in cleanup_plan.resources
        }
        residuals: set[str] = set()
        current_service_uid = ""
        for kind, url in urls.items():
            response = await client.get(url, headers=headers)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise RuntimeError(f"invalid RCA test {kind} residual response")
            metadata = body.get("metadata")
            meta = metadata if isinstance(metadata, dict) else {}
            uid = str(meta.get("uid") or "")
            if kind == "Service":
                current_service_uid = uid
            if uid == expected_uids.get(kind) or rca_test_fixture_owned_by_run(body, run_id):
                residuals.add(kind)
            else:
                raise RcaTestFixtureOwnershipChanged(
                    f"RCA test {kind} owner changed while verifying cleanup"
                )

        pods = await client.get(
            f"{base_url}/api/v1/namespaces/{namespace}/pods",
            params={"labelSelector": f"{RCA_TEST_RUN_LABEL}={run_id}"},
            headers=headers,
        )
        if pods.status_code != 404:
            pods.raise_for_status()
            pod_body = pods.json()
            if isinstance(pod_body, dict) and pod_body.get("items"):
                residuals.add("Pod")

        endpoint_slices = await client.get(
            f"{base_url}/apis/discovery.k8s.io/v1/namespaces/{namespace}/endpointslices",
            params={"labelSelector": f"kubernetes.io/service-name={resource_name}"},
            headers=headers,
        )
        if endpoint_slices.status_code != 404:
            endpoint_slices.raise_for_status()
            slice_body = endpoint_slices.json()
            rows = slice_body.get("items", []) if isinstance(slice_body, dict) else []
            expected_service_uid = expected_uids.get("Service", "")
            for row in rows:
                metadata = row.get("metadata") if isinstance(row, dict) else None
                meta = metadata if isinstance(metadata, dict) else {}
                owners = meta.get("ownerReferences")
                owner_rows = owners if isinstance(owners, list) else []
                if not expected_service_uid or any(
                    isinstance(owner, dict) and owner.get("uid") == expected_service_uid
                    for owner in owner_rows
                ):
                    residuals.add("EndpointSlice")
                    break

        endpoints = await client.get(
            f"{base_url}/api/v1/namespaces/{namespace}/endpoints/{resource_name}",
            headers=headers,
        )
        if endpoints.status_code != 404:
            endpoints.raise_for_status()
            expected_service_uid = expected_uids.get("Service", "")
            if not current_service_uid or current_service_uid == expected_service_uid:
                residuals.add("Endpoints")
        return residuals

    @command.handler(AgentConfig.ROLLOUT_RESTART_ACTION)
    async def rollout_restart_command(self, ctx: CommandContext[JsonObject]) -> JsonObject:
        diff = ctx.raw_payload.get("diff", {}) if isinstance(ctx.raw_payload, dict) else {}
        namespace = str(diff.get("namespace") or Sandbox.NAMESPACE)
        if not control_namespace_allowed(namespace):
            return self.command_result(
                False,
                AgentConfig.WRITE_NAMESPACE_DENIED_MESSAGE,
                resource=str(diff.get("resource", "")),
            )
        deployment = deployment_name_from_resource(str(diff.get("resource", "")))
        if not deployment:
            return self.command_result(
                False,
                "rollout_restart requires deployment resource",
                resource=str(diff.get("resource", "")),
            )
        patch = build_rollout_restart_patch()
        applied, message, rollout = await self.patch_deployment(namespace, deployment, patch)
        return self.command_result(
            applied,
            message,
            resource=str(diff.get("resource", "")),
            rollout=rollout,
        )

    async def apply_kubernetes_manifest(
        self,
        manifest: JsonObject,
        fallback_namespace: str,
        *,
        expected_uid: str | None = None,
    ) -> tuple[bool, str, JsonObject]:
        base_url = kubernetes_api_base_url()
        token = service_account_token()
        if not base_url or not token:
            return False, "kubernetes api not configured; dry-run only", {}

        try:
            resource = kubernetes_manifest_resource(manifest, fallback_namespace)
        except ValueError as exc:
            return False, str(exc), {}
        if not control_namespace_allowed(resource.namespace):
            return False, AgentConfig.WRITE_NAMESPACE_DENIED_MESSAGE, {}

        async with kubernetes_client(self.kubernetes_transport) as client:
            current = await client.get(
                resource.resource_url(base_url), headers=kubernetes_headers(token)
            )
            if current.status_code == 404:
                if expected_uid:
                    return False, "selected resource identity is stale", {}
                created = await client.post(
                    resource.collection_url(base_url),
                    json=resource.manifest,
                    headers=kubernetes_headers(token, "application/json"),
                )
                if created.is_error:
                    return False, kubernetes_failure_message("create", created), {}
                if resource.kind == "Deployment":
                    return await self.deployment_apply_result(
                        client, base_url, token, resource.namespace, resource.name
                    )
                return True, AgentConfig.MANIFEST_CREATED_MESSAGE, {}

            if current.is_error:
                return False, kubernetes_failure_message("get", current), {}
            if expected_uid:
                current_body = current.json()
                current_metadata = (
                    current_body.get("metadata") if isinstance(current_body, dict) else None
                )
                current_uid = (
                    str(current_metadata.get("uid") or "")
                    if isinstance(current_metadata, dict)
                    else ""
                )
                if current_uid != expected_uid:
                    return False, "selected resource identity is stale", {}
            patched = await client.patch(
                resource.resource_url(base_url),
                json=resource.manifest,
                headers=kubernetes_headers(token, "application/merge-patch+json"),
            )
            if patched.is_error:
                return False, kubernetes_failure_message("patch", patched), {}
            if resource.kind == "Deployment":
                return await self.deployment_apply_result(
                    client, base_url, token, resource.namespace, resource.name
                )
        return True, AgentConfig.MANIFEST_PATCHED_MESSAGE, {}

    def command_result(
        self,
        applied: bool,
        message: str,
        *,
        resource: str = "",
        retryable: bool = False,
        stdout: str = "",
        stderr: str = "",
        rollout: JsonObject | None = None,
    ) -> JsonObject:
        status = (
            AgentConfig.COMMAND_COMPLETED_STATUS if applied else AgentConfig.COMMAND_FAILED_STATUS
        )
        sanitized_stdout = sanitize_command_output(stdout or (message if applied else ""))
        sanitized_stderr = sanitize_command_output(stderr or ("" if applied else message))
        resource_status = []
        if resource:
            resource_status.append(
                {
                    "resource": resource,
                    "status": status,
                    "applied": applied,
                    "retryable": retryable,
                    "message": message,
                    "stdout": sanitized_stdout,
                    "stderr": sanitized_stderr,
                }
            )
        return {
            Gateway.STATUS: status,
            Gateway.CLUSTER_ID: self.cluster_id,
            Gateway.APPLIED: applied,
            Gateway.MESSAGE: message,
            Gateway.RETRYABLE: retryable,
            Gateway.RESOURCES: resource_status,
            Gateway.STDOUT: sanitized_stdout,
            Gateway.STDERR: sanitized_stderr,
            "rollout": rollout or {},
        }

    async def patch_deployment(
        self, namespace: str, deployment: str, patch: JsonObject
    ) -> tuple[bool, str, JsonObject]:
        base_url = kubernetes_api_base_url()
        token = service_account_token()
        if not base_url or not token:
            return False, "kubernetes api not configured; dry-run only", {}
        url = f"{base_url}/apis/apps/v1/namespaces/{namespace}/deployments/{deployment}"
        headers = kubernetes_headers(token, "application/strategic-merge-patch+json")
        async with kubernetes_client(self.kubernetes_transport) as client:
            response = await client.patch(url, json=patch, headers=headers)
            if response.is_error:
                return False, kubernetes_failure_message("patch", response), {}
            return await self.deployment_apply_result(
                client, base_url, token, namespace, deployment
            )

    async def deployment_apply_result(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        token: str,
        namespace: str,
        deployment: str,
    ) -> tuple[bool, str, JsonObject]:
        """Deployment apply 성공 후 반환값. KUBERNETES_AWAIT_ROLLOUT 옵트인이면 rollout
        완료를 기다려 ready=True/False 를 실어 promotion gate 가 판정하게 한다. off(기본)
        면 기존처럼 즉시 ready=None(빠른 배포, 회귀 없음). apply 자체는 성공했으므로
        applied 는 항상 True.
        """
        if AgentConfig.KUBERNETES_AWAIT_ROLLOUT:
            _ready, message, status = await self.wait_for_deployment_rollout(
                client, base_url, token, namespace, deployment
            )
            return True, message, status
        return True, AgentConfig.COMMAND_RESULT_MESSAGE, rollout_progress(deployment, waited=False)

    async def wait_for_deployment_rollout(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        token: str,
        namespace: str,
        deployment: str,
    ) -> tuple[bool, str, JsonObject]:
        timeout = AgentConfig.KUBERNETES_ROLLOUT_TIMEOUT_SECONDS
        url = f"{base_url}/apis/apps/v1/namespaces/{namespace}/deployments/{deployment}"
        if timeout <= 0:
            return (
                True,
                AgentConfig.COMMAND_RESULT_MESSAGE,
                {"resource": f"deployment/{deployment}", "ready": None, "waited": False},
            )
        deadline = time.monotonic() + timeout
        last_status: JsonObject = {}
        while True:
            response = await client.get(url, headers=kubernetes_headers(token))
            if not response.is_error:
                body = response.json()
                if isinstance(body, dict):
                    last_status = deployment_rollout_status(body)
                    if last_status.get("ready") is True:
                        return (
                            True,
                            AgentConfig.DEPLOYMENT_ROLLOUT_COMPLETED_MESSAGE,
                            last_status,
                        )
            else:
                last_status = {
                    "resource": f"deployment/{deployment}",
                    "ready": False,
                    "error": kubernetes_failure_message("rollout status", response),
                }
            if time.monotonic() >= deadline:
                return (
                    False,
                    f"deployment rollout not ready before timeout: {deployment}",
                    last_status,
                )
            await asyncio.sleep(AgentConfig.KUBERNETES_ROLLOUT_POLL_INTERVAL_SECONDS)


def deployment_name_from_resource(resource: str) -> str:
    value = resource.strip()
    if "/" not in value:
        return value
    kind, name = value.split("/", 1)
    normalized_kind = kind.lower()
    if normalized_kind in {"deployment", "deployments"}:
        return name
    if normalized_kind in {"replicaset", "replicasets"}:
        return deployment_name_from_replicaset(name)
    if normalized_kind in {"pod", "pods"}:
        return deployment_name_from_pod(name)
    return ""


def rollout_progress(deployment: str, *, waited: bool) -> JsonObject:
    return {
        "resource": f"deployment/{deployment}",
        "ready": None,
        "phase": "progressing",
        "waited": waited,
    }


def deployment_name_from_pod(name: str) -> str:
    match = re.match(r"^(.+)-[a-f0-9]{8,10}-[a-z0-9]{5}$", name.strip())
    return match.group(1) if match else ""


def deployment_name_from_replicaset(name: str) -> str:
    match = re.match(r"^(.+)-[a-f0-9]{8,10}$", name.strip())
    return match.group(1) if match else ""


def build_apply_manifest_patch(deployment: str, image: str) -> JsonObject:
    return {
        "spec": {
            "template": {
                "metadata": {"annotations": {"ops.service/apply-at": str(int(time.time()))}},
                "spec": {"containers": [{"name": deployment, "image": image}]},
            }
        }
    }


def build_rollout_restart_patch() -> JsonObject:
    return {
        "spec": {
            "template": {
                "metadata": {"annotations": {"ops.service/restarted-at": str(int(time.time()))}}
            }
        }
    }


def deployment_rollout_status(body: JsonObject) -> JsonObject:
    metadata = body.get("metadata")
    spec = body.get("spec")
    status = body.get("status")
    metadata_obj = metadata if isinstance(metadata, dict) else {}
    spec_obj = spec if isinstance(spec, dict) else {}
    status_obj = status if isinstance(status, dict) else {}
    name = str(metadata_obj.get("name") or "")
    replicas_value = spec_obj.get("replicas", 1)
    desired = int(1 if replicas_value is None else replicas_value)
    generation = int(metadata_obj.get("generation") or 0)
    observed = int(status_obj.get("observedGeneration") or 0)
    updated = int(status_obj.get("updatedReplicas") or 0)
    ready_replicas = int(status_obj.get("readyReplicas") or 0)
    available = int(status_obj.get("availableReplicas") or 0)
    conditions = status_obj.get("conditions")
    condition_list = conditions if isinstance(conditions, list) else []
    progressing = deployment_condition(condition_list, "Progressing")
    available_condition = deployment_condition(condition_list, "Available")
    ready = desired == 0 or (
        observed >= generation
        and updated >= desired
        and ready_replicas >= desired
        and available >= desired
        and condition_status(progressing) != "False"
        and condition_status(available_condition) != "False"
    )
    return {
        "resource": f"deployment/{name}" if name else "deployment",
        "ready": ready,
        "desired_replicas": desired,
        "updated_replicas": updated,
        "ready_replicas": ready_replicas,
        "available_replicas": available,
        "observed_generation": observed,
        "generation": generation,
        "conditions": condition_list,
    }


def deployment_condition(conditions: list[object], condition_type: str) -> JsonObject:
    for condition in conditions:
        if isinstance(condition, dict) and condition.get("type") == condition_type:
            return dict(condition)
    return {}


def condition_status(condition: JsonObject) -> str:
    return str(condition.get("status") or "")


def kubernetes_failure_message(action: str, response: httpx.Response) -> str:
    detail = response.text.strip()
    if len(detail) > 200:
        detail = f"{detail[:197]}..."
    suffix = f": {detail}" if detail else ""
    return f"kubernetes {action} failed ({response.status_code}){suffix}"


def sanitize_command_output(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    lines = []
    for line in text.splitlines():
        lowered = line.lower()
        if any(marker in lowered for marker in SENSITIVE_OUTPUT_MARKERS):
            lines.append("[redacted]")
            continue
        lines.append(line)
    sanitized = "\n".join(lines)
    if len(sanitized) <= COMMAND_OUTPUT_LIMIT:
        return sanitized
    return f"{sanitized[: COMMAND_OUTPUT_LIMIT - 3]}..."


def parse_approval_expires_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def rca_test_run_pod_names(snapshot: JsonObject, run_id: str) -> list[str]:
    """현재 run label이 보존된 실제 Pod 이름만 안정된 순서로 반환한다."""
    pods = snapshot.get("pods")
    if not isinstance(pods, list):
        return []
    names = {
        str(pod.get("name") or "")
        for pod in pods
        if isinstance(pod, dict)
        and isinstance(pod.get("labels"), dict)
        and pod["labels"].get(RCA_TEST_RUN_LABEL) == run_id
        and str(pod.get("name") or "")
    }
    return sorted(names)


def rca_test_resource_expired(resource: JsonObject, now: datetime | None = None) -> bool:
    metadata = resource.get("metadata")
    meta = metadata if isinstance(metadata, dict) else {}
    annotations = meta.get("annotations")
    annotation_body = annotations if isinstance(annotations, dict) else {}
    expires_at = parse_approval_expires_at(annotation_body.get(RCA_TEST_EXPIRES_AT_ANNOTATION))
    return expires_at is not None and expires_at <= (now or datetime.now(UTC))


def rca_test_fixture_expired(resource: JsonObject, now: datetime | None = None) -> bool:
    spec = resource.get("spec")
    spec_body = spec if isinstance(spec, dict) else {}
    replicas = int(spec_body.get("replicas") or 0)
    return rca_test_resource_expired(resource, now) and replicas > 0


def kubernetes_manifest_resource(
    manifest: JsonObject,
    fallback_namespace: str,
) -> KubernetesManifestResource:
    kind = str(manifest.get("kind", ""))
    api_version = str(manifest.get("apiVersion", ""))
    metadata = manifest.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("manifest metadata must be an object")
    name = str(metadata.get("name", ""))
    namespace = str(metadata.get("namespace") or fallback_namespace)
    if not kind or not api_version or not name:
        raise ValueError("manifest requires apiVersion, kind, and metadata.name")

    api_prefix, plural = kubernetes_resource_api(kind, api_version)
    normalized = {
        **manifest,
        "metadata": {
            **metadata,
            "namespace": namespace,
        },
    }
    return KubernetesManifestResource(
        kind=kind,
        api_version=api_version,
        namespace=namespace,
        name=name,
        plural=plural,
        api_prefix=api_prefix,
        manifest=normalized,
    )


def kubernetes_resource_api(kind: str, api_version: str) -> tuple[str, str]:
    contract = supported_kubernetes_resource(api_version, kind)
    return contract.api_prefix, contract.plural
