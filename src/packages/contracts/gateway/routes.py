from __future__ import annotations

HEALTHZ_PATH = "/healthz"
READYZ_PATH = "/readyz"
AUTH_SESSION_PATH = "/auth/session"
AUTH_SESSION_REFRESH_PATH = "/auth/session/refresh"
AUTH_WORKSPACES_PATH = "/auth/workspaces"
AUTH_WORKSPACE_SWITCH_PATH = "/auth/workspaces/switch"
AUTH_SIGNUP_PATH = "/auth/signup"
AUTH_LOGIN_PATH = "/auth/login"
AUTH_LOGOUT_PATH = "/auth/logout"
AUTH_CHECK_EMAIL_PATH = "/auth/check-email"
AUTH_VERIFY_EMAIL_PATH = "/auth/verify-email"
AUTH_RESEND_VERIFICATION_PATH = "/auth/resend-verification"
AUTH_APPROVE_USER_PATH = "/auth/users/{user_id}/approve"
PROMETHEUS_INTEGRATION_PATH = "/integrations/prometheus"
AGENT_PROMETHEUS_INTEGRATION_PATH = "/agent/integrations/prometheus"
AGENT_PROMETHEUS_INTEGRATION_STATUS_PATH = "/agent/integrations/prometheus/status"
GITHUB_WEBHOOK_PATH = "/github/webhook"
GITHUB_APP_CONFIG_PATH = "/integrations/github/app/config"
GITHUB_APP_INSTALL_URL_PATH = "/integrations/github/app/install-url"
GITHUB_APP_CALLBACK_PATH = "/integrations/github/app/callback"
GITHUB_APP_MANIFEST_PATH = "/integrations/github/app/manifest"
GITHUB_APP_MANIFEST_CALLBACK_PATH = "/integrations/github/app/manifest/callback"
GITHUB_APP_VERIFY_PATH = "/integrations/github/app/installations/{installation_id}/verify"
# 외부 모니터링(Alertmanager/Grafana) 알림 수신 — 인시던트 파이프라인 트리거.
ALERTMANAGER_WEBHOOK_PATH = "/webhooks/alertmanager"
AGENT_CONNECT_PATH = "/agent/connect"
AGENT_EVIDENCE_PATH = "/agent/evidence"
TARGETS_PATH = "/targets"
# 원라인 인스톨러 — agent 토큰 자체가 자격증명(해시 대조)이라 세션 불필요.
INSTALL_MANIFEST_PATH = "/install/{agent_token}"
INSTALL_TELEMETRY_SCRIPT_PATH = "/install/{agent_token}/telemetry/{platform}"
INSTALL_TELEMETRY_ASSET_PATH = "/install/{agent_token}/telemetry/assets/{asset_name}"
TARGET_RBAC_MANIFEST_PATH = "/clusters/{cluster_id}/target-rbac-manifest"
COMMANDS_PATH = "/commands"
COMMAND_STATUS_PATH = "/commands/{command_id}"
COMMAND_EVENTS_PATH = "/commands/{command_id}/events"
COMMAND_CANCEL_PATH = "/commands/{command_id}/cancel"
COMMAND_RETRY_PATH = "/commands/{command_id}/retry"
# 원본 전수 기능 mapping의 생성형 계약 catalog. 제품 UI는 이 경로를 통해
# 기능·스트리밍 여부를 발견하며, 소스 목록을 다시 하드코딩하지 않는다.
FEATURE_CONTRACTS_PATH = "/feature-contracts"
APPROVAL_GRANT_PATH = "/approvals/{approval_id}/grant"
APPROVAL_REJECT_PATH = "/approvals/{approval_id}/reject"
DEAD_LETTERS_PATH = "/dead-letters"
DEAD_LETTER_REPLAY_PATH = "/dead-letters/{dead_letter_id}/replay"
AI_CONVERSATIONS_PATH = "/ai/conversations"
AI_CONVERSATION_PATH = "/ai/conversations/{conversation_id}"
AI_CONVERSATION_MESSAGES_PATH = "/ai/conversations/{conversation_id}/messages"
# Context-bound AI facade. These routes are intentionally separate from the
# asynchronous conversation workflow: every synchronous answer must carry
# materialized, user-authorized evidence.
AI_CHAT_PATH = "/ai/chat"
AI_SUGGESTIONS_PATH = "/ai/suggestions"
AI_RESOURCES_PATH = "/ai/resources/{kind}"
AI_RESOURCE_PATH = "/ai/resources/{kind}/{namespace}/{name}"
# Durable, resource-scoped AI investigations. The browser supplies an exact
# Kubernetes identity, while the gateway re-resolves it before creating a run.
DIAGNOSE_CAPABILITIES_PATH = "/diagnose/capabilities"
DIAGNOSE_CONSENTS_PATH = "/diagnose/consents"
DIAGNOSE_RUNS_PATH = "/diagnose/runs"
DIAGNOSE_RUN_PATH = "/diagnose/runs/{run_id}"
DIAGNOSE_RUN_TURNS_PATH = "/diagnose/runs/{run_id}/turns"
DIAGNOSE_RUN_STOP_PATH = "/diagnose/runs/{run_id}/stop"
DIAGNOSE_RUN_EVENTS_PATH = "/diagnose/runs/{run_id}/events"
DIAGNOSE_HISTORY_PATH = "/diagnose/history"
# Browser log SSE. Multi-cluster identity is a required query parameter; these
# path constants own only the target identity portion.
POD_LOG_STREAM_PATH = "/pods/{namespace}/{name}/logs/stream"
WORKLOAD_LOG_STREAM_PATH = "/workloads/{kind}/{namespace}/{name}/logs/stream"
# Contextual Workload Detail.  API group/version and exact cluster identity are
# query-owned because the upstream path is intentionally cluster agnostic.
WORKLOAD_DETAIL_PATH = "/workloads/{kind}/{namespace}/{name}"
RIGHTSIZING_SCAN_PATH = "/rightsizing/workloads"
# Safe contextual Compare.  Source-compatible kind/apiGroup/a/b are query
# owned; the product adds cluster_id and an exact apiVersion when resolved.
COMPARE_DESCRIPTORS_PATH = "/compare/descriptors"
COMPARE_CANDIDATES_PATH = "/compare/candidates"
COMPARE_RESOURCES_PATH = "/compare/resources"
# 관리 콘솔 — 조직/그룹/멤버/권한 (프론트 콘솔 전용, admin 세션)
ORGS_PATH = "/orgs"
ORG_PATH = "/orgs/{org_id}"
GROUPS_PATH = "/groups"
GROUP_PATH = "/groups/{group_id}"
GROUP_MEMBERS_PATH = "/groups/{group_id}/members"
GROUP_MEMBER_PATH = "/groups/{group_id}/members/{user_id}"
USERS_PATH = "/users"
# 알림 라우팅 룰 — 워크스페이스별 수신 채널(admin 세션)
ALERT_CHANNELS_PATH = "/alert-channels"
ALERT_CHANNEL_PATH = "/alert-channels/{channel_id}"
ALERT_CHANNEL_TEST_PATH = "/alert-channels/test"
# Opsia 소유 알림 규칙. 클러스터 PrometheusRule 경로와 분리한다.
ALERT_RULES_PATH = "/alert-rules"
ALERT_RULE_PATH = "/alert-rules/{rule_id}"
ALERT_EVENTS_PATH = "/alert-events"
ALERT_EVENTS_STREAM_PATH = "/alert-events/stream"
ALERT_EVENT_ACK_PATH = "/alert-events/{event_id}/ack"
ALERT_EVENT_PROMOTE_INCIDENT_PATH = "/alert-events/{event_id}/promote-incident"
ACTIVITY_OVERVIEW_PATH = "/activity/overview"
ACCESS_PATH = "/access"
ACCESS_ITEM_PATH = "/access/{access_id}"
APPLICATIONS_PATH = "/applications"
APPLICATION_CONNECT_PATH = "/applications/connect"
APPLICATION_CONNECT_PREVIEW_PATH = "/applications/connect/preview"
APPLICATION_PATH = "/applications/{application_id}"
APPLICATION_DEPLOYMENTS_PATH = "/applications/{application_id}/deployments"
APPLICATION_DRIFT_PATH = "/applications/{application_id}/drift"
APPLICATION_RUNS_PATH = "/applications/{application_id}/runs"
APPLICATION_FILTER_RESULTS_PATH = "/applications/filter-results"
APPLICATION_FILTER_FACETS_PATH = "/applications/filter-facets"
APPLICATION_LABEL_FACETS_PATH = "/applications/label-facets"
GITOPS_FILTER_RESULTS_PATH = "/gitops/filter-results"
GITOPS_FILTER_FACETS_PATH = "/gitops/filter-facets"
GITOPS_OVERVIEW_PATH = "/gitops/overview"
GITOPS_APPLICATION_DETAIL_PATH = "/gitops/applications/{application_id}"
GITOPS_RESOURCE_TREE_PATH = "/gitops/resources/{kind}/{namespace}/{name}/tree"
GITOPS_RESOURCE_INSIGHTS_PATH = "/gitops/resources/{kind}/{namespace}/{name}/insights"
GITOPS_RESOURCE_ACTION_PATH = "/gitops/resources/{kind}/{namespace}/{name}/actions"
HELM_RELEASES_PATH = "/helm/releases"
HELM_RELEASE_PATH = "/helm/releases/{namespace}/{release_name}"
HELM_RELEASE_ARTIFACT_PATH = "/helm/releases/{namespace}/{release_name}/artifacts"
HELM_RELEASE_UPGRADE_PATH = "/helm/releases/{namespace}/{release_name}/upgrade"
HELM_RELEASE_UPGRADE_STREAM_PATH = "/helm/releases/{namespace}/{release_name}/upgrade-stream"
HELM_RELEASE_ROLLBACK_STREAM_PATH = "/helm/releases/{namespace}/{release_name}/rollback-stream"
HELM_RELEASE_VALUES_PATH = "/helm/releases/{namespace}/{release_name}/values"
HELM_RELEASE_VALUES_PREVIEW_PATH = "/helm/releases/{namespace}/{release_name}/values/preview"
HELM_RELEASE_INSTALL_STREAM_PATH = "/helm/releases/install-stream"
HELM_INSTALL_TARGETS_PATH = "/helm/install-targets"
HELM_RELEASE_UPGRADE_INFO_PATH = "/helm/releases/{namespace}/{release_name}/upgrade-info"
HELM_RELEASE_VERSIONS_PATH = "/helm/releases/{namespace}/{release_name}/versions"
HELM_UPGRADE_CHECK_PATH = "/helm/upgrade-check"
HELM_CHART_SOURCES_PATH = "/helm/chart-sources"
HELM_CHART_SOURCE_PATH = "/helm/chart-sources/{source_id}"
HELM_CHART_SOURCE_VERSIONS_PATH = "/helm/chart-sources/{source_id}/charts/{chart_name}/versions"
HELM_CHARTS_PATH = "/helm/charts"
HELM_CHART_PATH = "/helm/charts/{source_id}/{chart_name}"
HELM_CHART_VERSION_PATH = "/helm/charts/{source_id}/{chart_name}/{version}"
HELM_ARTIFACTHUB_SEARCH_PATH = "/helm/artifacthub/search"
HELM_ARTIFACTHUB_CHART_PATH = "/helm/artifacthub/charts/{repository}/{chart}"
HELM_ARTIFACTHUB_CHART_VERSION_PATH = "/helm/artifacthub/charts/{repository}/{chart}/{version}"
HELM_REPOSITORY_UPDATE_PATH = "/helm/repositories/{name}/update"
CLUSTER_HOME_INSIGHTS_PATH = "/clusters/{cluster_id}/home/insights"
TRAFFIC_FLOWS_PATH = "/traffic/flows"
TRAFFIC_SOURCES_PATH = "/traffic/sources"
TRAFFIC_SOURCE_PATH = "/traffic/source"
TRAFFIC_CONNECT_PATH = "/traffic/connect"
NETWORK_POLICY_EVALUATE_PATH = "/network-policies/evaluate"
COST_OVERVIEW_PATH = "/cost/overview"
COST_NODES_PATH = "/cost/nodes"
CHECKS_OVERVIEW_PATH = "/checks/overview"
CHECKS_DETAIL_PATH = "/checks/{check_id}"
CHECKS_SETTINGS_PATH = "/settings/audit"
RESOURCE_FILE_COMMAND_PATH = "/resource-files/commands"
SCHEDULED_WORKLOAD_RUNS_PATH = "/workloads/scheduled/{kind}/{namespace}/{name}/runs"
SCHEDULED_WORKLOAD_RUN_LOG_STREAM_PATH = (
    "/workloads/scheduled/{kind}/{namespace}/{name}/runs/{run_key}/logs/stream"
)
REPOSITORY_DISCOVERY_PROBE_PATH = "/repositories/discovery/probe"
REPOSITORY_DISCOVERY_BRANCHES_PATH = "/repositories/discovery/branches"
REPOSITORY_DISCOVERY_MANIFESTS_PATH = "/repositories/discovery/manifests"
REPOSITORY_DISCOVERY_VALIDATE_PATH = "/repositories/discovery/validate"
REPOSITORY_CONNECTION_STATUS_PATH = "/repositories/connection-status"
REPOSITORY_DISCONNECT_PATH = "/repositories/disconnect"
REPOSITORIES_PATH = "/repositories"
DIAGNOSTICS_PATH = "/diagnostics"
VERSION_CHECK_PATH = "/version-check"
RELEASE_PLANS_PATH = "/release-plans"
RELEASE_READINESS_PATH = "/release-readiness"
RELEASE_MANIFEST_RENDER_PATH = "/release-plans/render-manifest"
RELEASE_MANIFEST_SAFE_PR_PATH = "/release-plans/render-manifest/safe-pr"
RELEASE_PLAN_DISPATCH_PATH = "/release-plans/dispatch"
RELEASE_PLAN_PREVIEW_PATH = "/release-plans/preview"
RELEASE_PLAN_START_PATH = "/release-plans/start"
RELEASE_PLAN_ARCHIVE_PATH = "/release-plans/{plan_id}/archive"
RELEASE_PLAN_RESTORE_PATH = "/release-plans/{plan_id}/restore"
RELEASE_PLAN_PATH = "/release-plans/{plan_id}"
RELEASE_RUNS_PATH = "/release-runs"
RELEASE_RUN_PATH = "/release-runs/{run_id}"
RELEASE_RUN_SUMMARY_PATH = "/release-runs/summary"
RELEASE_RUN_HANDOFF_PATH = "/release-runs/{run_id}/handoff"
RELEASE_RUN_REPORT_PATH = "/release-runs/{run_id}/report"
RELEASE_RUN_REPORT_EXPORT_PATH = "/release-runs/{run_id}/report/export"
RELEASE_RUN_ADVANCE_PATH = "/release-runs/{run_id}/advance"
RELEASE_RUN_CANCEL_PATH = "/release-runs/{run_id}/cancel"
RELEASE_RUN_PAUSE_PATH = "/release-runs/{run_id}/pause"
RELEASE_RUN_RESUME_PATH = "/release-runs/{run_id}/resume"
RELEASE_RUN_RETRY_PATH = "/release-runs/{run_id}/retry"
RELEASE_RUN_ROLLBACK_PATH = "/release-runs/{run_id}/rollback"
RELEASE_RUN_NOTIFY_PATH = "/release-runs/{run_id}/notify"
RELEASE_AUDIT_PATH = "/release-audit"
RELEASE_AUDIT_EXPORT_PATH = "/release-audit/export"
CATALOG_ITEMS_PATH = "/catalog/items"
CATALOG_ITEM_PATH = "/catalog/items/{item_id}"
CATALOG_ITEM_INSTALLS_PATH = "/catalog/items/{item_id}/installs"
AGENT_COMMAND_POLL_PATH = "/agent/commands/poll"
AGENT_COMMAND_START_PATH = "/agent/commands/{command_id}/start"
AGENT_COMMAND_HEARTBEAT_PATH = "/agent/commands/{command_id}/heartbeat"
AGENT_COMMAND_RESULT_PATH = "/agent/commands/{command_id}/result"
AGENT_EVIDENCE_JOB_SCHEDULE_PATH = "/agent/evidence/jobs"
AGENT_EVIDENCE_JOB_POLL_PATH = "/agent/evidence/jobs/poll"
AGENT_EVIDENCE_JOB_RESULT_PATH = "/agent/evidence/jobs/{job_id}/result"
AGENT_INVENTORY_SNAPSHOTS_PATH = "/agent/inventory/snapshots"
AGENT_POLICY_PATH = "/agent/policy"
AGENT_POLICY_STATUS_PATH = "/agent/policy/status"
AGENT_RECONCILE_STATUS_PATH = "/agent/reconcile/status"
AGENT_DEBUG_QUERY_PATH = "/agent/debug/query"
CLUSTERS_PATH = "/clusters"
CLUSTERS_CONNECT_PATH = "/clusters/connect"
CLUSTER_PATH = "/clusters/{cluster_id}"
CLUSTER_CONNECT_COMMAND_PATH = "/clusters/{cluster_id}/connect-command"
# 콘솔 fleet 화면용 집계 — 워크스페이스 전체 클러스터 health/사용량 롤업(세션 범위).
FLEET_SUMMARY_PATH = "/fleet/summary"
# 콘솔 홈용 단일 workspace SSE — 권한으로 허용된 fleet 전체 payload를 직접 전송.
FLEET_SUMMARY_EVENTS_PATH = "/fleet/events"
# 클러스터 타일 클릭 드릴다운 — 워크로드 health 그룹/경고 이벤트/열린 인시던트/usage 스냅샷.
CLUSTER_SUMMARY_PATH = "/clusters/{cluster_id}/summary"
CLUSTER_HOME_EVENTS_PATH = "/clusters/{cluster_id}/home/events"
CLUSTER_CONNECTION_STATUS_PATH = "/clusters/{cluster_id}/connection-status"
CLUSTER_CONNECTION_PATH = "/clusters/{cluster_id}/connection"
CLUSTER_NODES_SUMMARY_PATH = "/clusters/{cluster_id}/nodes/summary"
CLUSTER_NODE_ALIASES_PATH = "/clusters/{cluster_id}/nodes/aliases"
CLUSTER_NODE_ALIAS_PATH = "/clusters/{cluster_id}/nodes/{node_name}/alias"
CLUSTER_NODE_PODS_SUMMARY_PATH = "/clusters/{cluster_id}/nodes/{node_name}/pods/summary"
CLUSTER_INVENTORY_RESOURCES_PATH = "/clusters/{cluster_id}/inventory/resources"
CLUSTER_INVENTORY_RESOURCE_DETAIL_PATH = "/clusters/{cluster_id}/inventory/resource-detail"
CLUSTER_INVENTORY_SUMMARY_PATH = "/clusters/{cluster_id}/inventory/summary"
CLUSTER_CONFIG_REFERENCES_PATH = "/clusters/{cluster_id}/config-references"
CLUSTER_API_RESOURCES_PATH = "/clusters/{cluster_id}/api-resources"
CLUSTER_INVENTORY_WORKLOADS_PATH = "/clusters/{cluster_id}/inventory/workloads"
CLUSTER_INVENTORY_SERVICES_PATH = "/clusters/{cluster_id}/inventory/services"
CLUSTER_INVENTORY_EVENTS_PATH = "/clusters/{cluster_id}/inventory/events"
# 워크스페이스 범위 Resources 필터 계약 — 기존 단일 클러스터 인벤토리 경로와 분리한다.
RESOURCES_FILTER_FACETS_PATH = "/resources/filter-facets"
FILTERED_RESOURCES_PATH = "/resources"
RESOURCE_LABEL_FACETS_PATH = "/resources/label-facets"
FILTER_FACETS_PATH = "/filter-facets"
RESOURCE_SEARCH_PATH = "/search"
# Exact Kubernetes RBAC reverse projections. Cluster identity remains an explicit
# query parameter because product sessions are workspace scoped, not kubeconfig scoped.
RBAC_SUBJECT_NAMESPACED_PATH = "/rbac/subject/{kind}/{namespace}/{name}"
RBAC_SUBJECT_GLOBAL_PATH = "/rbac/subject/{kind}/{name}"
RBAC_ROLE_PATH = "/rbac/role/{kind}/{namespace}/{name}"
RBAC_NAMESPACE_PATH = "/rbac/namespace/{namespace}"
CLUSTER_NAMESPACE_SCOPE_PATH = "/cluster/namespace-scope"
CLUSTER_NAMESPACE_PATH = "/cluster/namespace"
SETTINGS_PATH = "/settings"
SETTINGS_ACCESS_PATH = "/settings/access"
REFRESH_POLICIES_PATH = "/refresh-policies"
RESOURCES_GRAPH_PATH = "/resources/graph"
TOPOLOGY_PATH = "/topology"
# Resources time scrubber: actual observed changes plus explicit collection gaps.
CHANGES_PATH = "/changes"
# Retained, immutable product Timeline history.  Snapshot is NDJSON so a
# browser/desktop adapter can use the same frame parser as resumable SSE.
TIMELINE_CAPABILITIES_PATH = "/timeline/capabilities"
TIMELINE_PINS_PATH = "/timeline/pins"
TIMELINE_PIN_PATH = "/timeline/pins/{pin_id}"
TIMELINE_OVERVIEW_PATH = "/timeline/overview"
TIMELINE_SNAPSHOTS_PATH = "/timeline/snapshots"
TIMELINE_STREAM_PATH = "/timeline/stream"
# Resources 표의 여러 pod 추세를 한 번에 읽는다. 단건 BQ-065를 클라이언트에서
# fan-out하지 않도록 서버 batch 경계를 별도로 둔다.
RESOURCE_METRICS_HISTORY_PATH = "/metrics/history"
# Typed product metric reads resolve a server-owned subject and PromQL plan,
# then reuse the existing audited target-agent query command path.
SCOPED_RESOURCE_METRICS_QUERY_PATH = "/metrics/query"
# 단일 inventory resource의 실행 가능 액션만 반환한다. 거부/미지원 액션을
# disabled 항목으로 노출하지 않는 BQ-061 capability 경계다.
RESOURCE_CAPABILITIES_PATH = "/capabilities"
RESOURCE_DELETE_PATH = "/resource-deletions/{resource_id}"
RESOURCE_DELETE_PREVIEW_PATH = "/resource-deletions/{resource_id}/cascade-preview"
# Exact core/v1 Service capabilities and one bounded, audited in-cluster GET.
SERVICE_ACCESS_CAPABILITIES_PATH = "/service-access/capabilities"
SERVICE_REQUESTS_PATH = "/service-access/requests"
# GitOps manifest editor. The selected live inventory resource is resolved to an
# exact application/deployment binding; writes are emitted only as Safe PR events.
RESOURCE_MANIFEST_SOURCE_PATH = "/resource-manifests/{resource_id}"
RESOURCE_MANIFEST_PREVIEW_PATH = "/resource-manifests/{resource_id}/preview"
RESOURCE_MANIFEST_APPROVE_PATH = "/resource-manifests/{resource_id}/approve"
RESOURCE_MANIFEST_APPLY_PATH = "/resource-manifests/{resource_id}/apply"
RESOURCE_MANIFEST_CREATE_CAPABILITY_PATH = "/resource-manifests/create/capability"
RESOURCE_MANIFEST_CREATE_DRY_RUN_PATH = "/resource-manifests/create/dry-run"
RESOURCE_MANIFEST_CREATE_PATH = "/resource-manifests/create"
# 워크스페이스 범위 Issues 필터 계약 — mutable RCA timeline projection의 완전성을 명시한다.
ISSUES_FILTER_RESULTS_PATH = "/issues"
ISSUES_FILTER_FACETS_PATH = "/issues/filter-facets"
ISSUES_LABEL_FACETS_PATH = "/issues/label-facets"
# 스냅샷 기반 실측 활용 시계열(usage rollup) — 콘솔 추이 차트용.
CLUSTER_USAGE_PATH = "/clusters/{cluster_id}/usage"
CLUSTER_METRIC_QUERY_PRESETS_PATH = "/clusters/{cluster_id}/metric-query-presets"
CLUSTER_METRIC_QUERY_PRESET_PATH = "/clusters/{cluster_id}/metric-query-presets/{preset_id}"
CLUSTER_METRIC_QUERY_PRESET_RUN_PATH = "/clusters/{cluster_id}/metric-query-presets/{preset_id}/run"
CLUSTER_METRIC_WIDGETS_PATH = "/clusters/{cluster_id}/metric-widgets"
CLUSTER_METRIC_WIDGET_PATH = "/clusters/{cluster_id}/metric-widgets/{widget_id}"
METRICS_VALIDATE_PATH = "/metrics/validate"
CLUSTER_DEPLOYMENT_SCALE_PATH = (
    "/clusters/{cluster_id}/namespaces/{namespace}/deployments/{deployment}/scale"
)
CLUSTER_WORKLOAD_SCALE_PATH = (
    "/clusters/{cluster_id}/namespaces/{namespace}/workloads/{kind}/{workload}/scale"
)
CLUSTER_WORKLOAD_RESTART_PATH = (
    "/clusters/{cluster_id}/namespaces/{namespace}/workloads/{kind}/{workload}/restart"
)
RESOURCE_WORKLOAD_ROLLBACK_PATH = "/resource-rollbacks/{resource_id}"
CLUSTER_NODE_CORDON_PATH = "/clusters/{cluster_id}/nodes/{node}/cordon"
CLUSTER_NODE_UNCORDON_PATH = "/clusters/{cluster_id}/nodes/{node}/uncordon"
CLUSTER_NODE_DRAIN_PATH = "/clusters/{cluster_id}/nodes/{node}/drain"
CLUSTER_POD_DEBUG_PATH = "/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod}/debug"
CLUSTER_NODE_DEBUG_PATH = "/clusters/{cluster_id}/nodes/{node}/debug"
CLUSTER_NODE_DEBUG_CLEANUP_PATH = "/clusters/{cluster_id}/nodes/{node}/debug/cleanup"
CLUSTER_CRONJOB_TRIGGER_PATH = (
    "/clusters/{cluster_id}/namespaces/{namespace}/cronjobs/{cronjob}/trigger"
)
CLUSTER_CRONJOB_SUSPEND_PATH = (
    "/clusters/{cluster_id}/namespaces/{namespace}/cronjobs/{cronjob}/suspend"
)
CLUSTER_CRONJOB_RESUME_PATH = (
    "/clusters/{cluster_id}/namespaces/{namespace}/cronjobs/{cronjob}/resume"
)
CLUSTER_DEPLOYMENT_RESTART_PATH = (
    "/clusters/{cluster_id}/namespaces/{namespace}/deployments/{deployment}/restart"
)
CLUSTER_POLICY_PATH = "/clusters/{cluster_id}/policy"
CLUSTER_SCHEDULING_PROFILES_PATH = "/clusters/{cluster_id}/scheduling-profiles"
PROVIDERS_CATALOG_PATH = "/providers/catalog"
PROVIDERS_CLUSTER_DISCOVERY_PATH = "/providers/cluster-discovery"
PROVIDERS_VALIDATE_PATH = "/providers/validate"
TARGETS_PREFLIGHT_PATH = "/targets/preflight"
DASHBOARD_RCA_TIMELINE_PATH = "/dashboard/rca/timeline"
DASHBOARD_RCA_ISSUES_PATH = "/dashboard/rca/issues"
RESOURCE_RCA_ISSUES_PATH = "/dashboard/resources/issues"
DASHBOARD_RCA_INCIDENT_PATH = "/dashboard/rca/incidents/{incident_id}"
AUDIT_TIMELINE_PATH = "/audit/timeline"
# 범용 조회 API — 세션 워크스페이스 범위의 evidence/RCA report 목록(read-only)
EVIDENCE_QUERY_PATH = "/evidence"
EVIDENCE_WINDOWS_PATH = "/evidence/windows"
EVIDENCE_WINDOW_PATH = "/evidence/windows/{evidence_key}"
RCA_REPORTS_PATH = "/rca-reports"
RCA_RULES_PATH = "/rca/rules"
RCA_RULES_VALIDATE_PATH = "/rca/rules/validate"
RCA_TEST_SCENARIOS_PATH = "/rca/test-scenarios"
RCA_TEST_RUNS_PATH = "/rca/test-runs"
RCA_TEST_RUN_PATH = "/rca/test-runs/{run_id}"
RCA_BUNDLE_PATH = "/rca/bundles/{correlation_id}"
RCA_RECENT_CHANGES_PATH = "/rca/incidents/{incident_id}/recent-changes"
RCA_RECOVERY_PLAN_BY_CORRELATION_PATH = "/rca/recovery-plans/by-correlation/{correlation_id}"
RCA_RECOVERY_ACTION_SELECT_BY_CORRELATION_PATH = (
    "/rca/recovery-plans/by-correlation/{correlation_id}/actions/select"
)
RCA_RECOVERY_ACTION_SELECT_PATH = "/rca/recovery-plans/{plan_id}/actions/{action_id}/select"
RCA_RECOVERY_RETRY_BY_CORRELATION_PATH = (
    "/rca/recovery-plans/by-correlation/{correlation_id}/retry"
)


def agent_command_result_path(command_id: str) -> str:
    return AGENT_COMMAND_RESULT_PATH.format(command_id=command_id)


def agent_command_start_path(command_id: str) -> str:
    return AGENT_COMMAND_START_PATH.format(command_id=command_id)


def agent_command_heartbeat_path(command_id: str) -> str:
    return AGENT_COMMAND_HEARTBEAT_PATH.format(command_id=command_id)


def agent_evidence_job_result_path(job_id: str) -> str:
    return AGENT_EVIDENCE_JOB_RESULT_PATH.format(job_id=job_id)
