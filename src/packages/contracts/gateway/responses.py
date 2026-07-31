from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.contracts.ai_conversation import (
    BOUNDED_MESSAGE_HISTORY_REASON,
    MAX_CONVERSATION_MESSAGE_LIMIT,
)
from packages.contracts.cost.observations import (
    CostObservedWorkloadAllocation,
    CostWorkloadAllocation,
    CostWorkloadKind,
)
from packages.contracts.gateway import facets as gateway_facets
from packages.contracts.gateway import limits as gateway_limits
from packages.contracts.gateway.base import StrictModel
from packages.contracts.inventory_provider import ResourceProviderDetail
from packages.contracts.kubernetes_discovery import ApiResourceDiscoveryObservation
from packages.contracts.parity import ClusterScope, ResourceRef
from packages.contracts.resource_access import ResourceAccessDetail

JsonMap = dict[str, Any]
AuditJourneyStage = Literal[
    "alert",
    "evidence",
    "rca",
    "recovery",
    "command",
    "pr",
    "workflow",
    "cluster",
    "ai",
    "notification",
    "system",
    "unknown",
]


class HealthResponse(StrictModel):
    status: str
    service: str | None = None


class AcceptedResponse(StrictModel):
    accepted: bool
    event_id: str
    correlation_id: str
    command_id: str | None = None


class AcceptedEventResponse(AcceptedResponse):
    event: JsonMap


class EventIdAcceptedResponse(StrictModel):
    accepted: bool
    event_id: str


class AuthLogoutCapability(StrictModel):
    action: Literal["end_session", "upstream_identity_required"]
    supported: bool
    reauthentication_expected: bool


class AuthSessionResponse(StrictModel):
    authenticated: Literal[True]
    auth_enabled: Literal[True]
    auth_mode: Literal["password", "trusted_proxy"]
    display_name: str | None = None
    email: str | None = None
    user_id: str = Field(min_length=1)
    groups: list[str]
    roles: list[str] = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    logout: AuthLogoutCapability

    @model_validator(mode="after")
    def validate_logout_semantics(self) -> Self:
        expected = (
            ("end_session", True, False)
            if self.auth_mode == "password"
            else ("upstream_identity_required", False, True)
        )
        actual = (
            self.logout.action,
            self.logout.supported,
            self.logout.reauthentication_expected,
        )
        if actual != expected:
            raise ValueError("logout capability must match the authentication authority")
        return self


class AuthWorkspaceItem(StrictModel):
    workspace_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    slug: str = Field(min_length=1)


class AuthWorkspaceListResponse(StrictModel):
    current_workspace_id: str = Field(min_length=1)
    items: list[AuthWorkspaceItem]


class EmailCheckResponse(StrictModel):
    available: bool
    reason_code: str = ""
    detail: str = ""
    retry_after: int | None = None


class EmailVerificationResponse(StrictModel):
    accepted: bool
    verification_required: bool
    email: str | None = None


class UserApprovalResponse(StrictModel):
    accepted: bool
    user_id: str
    status: str
    role: str
    workspace_id: str


class LogoutResponse(StrictModel):
    authenticated: bool


class AgentCommandPollResponse(StrictModel):
    command: JsonMap | None


class CommandStartedResponse(StrictModel):
    accepted: bool
    correlation_id: str


class CommandHeartbeatResponse(StrictModel):
    accepted: bool
    correlation_id: str
    cancel_requested: bool = False
    cancel_generation: int | None = None


class AgentDebugQueryResponse(StrictModel):
    accepted: bool
    command_id: str
    correlation_id: str


class CommandStatusResponse(StrictModel):
    """브라우저 콘솔이 명령 진행 상태·실제 결과를 조회하는 응답 — 임의 완료 표시 금지 계약."""

    command_id: str
    cluster_id: str
    correlation_id: str
    action: str
    status: str
    result: dict[str, Any] = Field(default_factory=dict)
    completed_at: str | None = None


class SchedulingPolicyResponse(StrictModel):
    accepted: bool = True
    cluster_id: str
    scheduling: JsonMap = Field(default_factory=dict)


class MetricQueryPresetItem(StrictModel):
    preset_id: str
    workspace_id: str
    cluster_id: str
    name: str
    description: str = ""
    source: str
    query: str
    range_seconds: int | None = None
    step_seconds: int | None = None
    unit: str = ""
    metadata: JsonMap = Field(default_factory=dict)
    created_by: str
    created_at: str | None = None
    updated_at: str | None = None


class MetricQueryPresetListResponse(StrictModel):
    items: list[MetricQueryPresetItem] = Field(default_factory=list)


class MetricQueryPresetResponse(StrictModel):
    item: MetricQueryPresetItem


class MetricWidgetItem(StrictModel):
    widget_id: str
    workspace_id: str
    cluster_id: str
    query_preset_id: str
    title: str
    kind: str
    position: JsonMap = Field(default_factory=dict)
    settings: JsonMap = Field(default_factory=dict)
    created_by: str
    created_at: str | None = None
    updated_at: str | None = None


class MetricWidgetListResponse(StrictModel):
    items: list[MetricWidgetItem] = Field(default_factory=list)


class MetricWidgetResponse(StrictModel):
    item: MetricWidgetItem


class RcaTimelineItem(StrictModel):
    workspace_id: str
    correlation_id: str
    cluster_id: str | None = None
    incident_id: str | None = None
    incident_namespace: str | None = None
    incident_resource_kind: str | None = None
    incident_resource_name: str | None = None
    incident_symptom: str | None = None
    evidence_ref: str | None = None
    current_subject: str
    status: str
    root_cause: str | None = None
    confidence: float | None = None
    supporting_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    action_route: str | None = None
    command_id: str | None = None
    pr_url: str | None = None
    error_reason: str | None = None
    updated_at: str | None = None


class RcaTimelineResponse(StrictModel):
    items: list[RcaTimelineItem]


class RcaIssueItem(RcaTimelineItem):
    """Issue queue projection with a conservative source-compatible severity tier.

    The older RCA timeline contract intentionally remains unchanged for rolling
    web deployments.  This sibling projection exposes only the two visual
    severity tiers that the queue can render without browser-side inference.
    """

    issue_severity: Literal["critical", "warning"] | None = None
    severity_availability: Literal["available", "unavailable"]
    severity_reason_code: Literal["source_incomplete", "outside_two_tier_scale"] | None = None
    situation_summary: str | None = None
    recommended_action_summary: str | None = None
    evidence_summary: str | None = None
    evidence_bundle_summary: str | None = None
    recovery_reason_code: str | None = None

    @model_validator(mode="after")
    def validate_severity_projection(self) -> Self:
        if self.severity_availability == "available":
            if self.issue_severity is None or self.severity_reason_code is not None:
                raise ValueError("available issue severity requires a tier without a reason")
        elif self.issue_severity is not None or self.severity_reason_code is None:
            raise ValueError("unavailable issue severity requires a reason without a tier")
        return self


class RecentChangeItem(StrictModel):
    event_id: str
    changed_at: str
    namespace: str
    resource_kind: str
    resource_name: str
    image_before: str | None = None
    image_after: str | None = None
    pr_url: str | None = None
    commit_sha: str
    repository_id: str
    repo_ref: str
    workflow_run_id: str


class RcaIssueLegacyListResponse(StrictModel):
    items: list[RcaIssueItem]


class RcaIssueQueueItem(RcaIssueItem):
    category: str | None = Field(default=None, min_length=1)
    category_availability: Literal["available", "unavailable"]
    category_reason_code: Literal["source_incomplete"] | None = None

    @model_validator(mode="after")
    def validate_category_projection(self) -> Self:
        if self.category_availability == "available":
            if self.category is None or self.category_reason_code is not None:
                raise ValueError("available issue category requires a value without a reason")
        elif self.category is not None or self.category_reason_code is None:
            raise ValueError("unavailable issue category requires a reason without a value")
        return self


class RcaIssueQueueRecentChange(RecentChangeItem):
    incident_id: str = Field(min_length=1)


class RcaIssueQueueFacet(StrictModel):
    value: str = Field(min_length=1)
    count: int = Field(ge=0)


class RcaIssueQueueFacets(StrictModel):
    namespaces: list[RcaIssueQueueFacet] = Field(default_factory=list)
    severities: list[RcaIssueQueueFacet] = Field(default_factory=list)
    categories: list[RcaIssueQueueFacet] = Field(default_factory=list)


class RcaIssueQueueVisibility(StrictModel):
    state: Literal["complete", "partial", "restricted"]
    completeness: Literal["exact", "partial", "unavailable"]
    authorized_cluster_count: int = Field(ge=0)
    requested_namespaces: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_visibility_reason(self) -> Self:
        if self.state != "complete" and not self.reason_codes:
            raise ValueError("incomplete issue visibility requires a reason")
        if self.state == "complete" and self.completeness != "exact":
            raise ValueError("complete issue visibility requires exact completeness")
        if self.state == "restricted" and self.authorized_cluster_count != 0:
            raise ValueError("restricted issue visibility cannot authorize clusters")
        return self


class RcaIssueListResponse(StrictModel):
    items: list[RcaIssueQueueItem]
    total: int = Field(ge=0)
    total_matched: int = Field(ge=0)
    count_completeness: Literal["exact"] = "exact"
    recent_changes: list[RcaIssueQueueRecentChange] = Field(default_factory=list)
    visibility: RcaIssueQueueVisibility
    facets: RcaIssueQueueFacets

    @model_validator(mode="after")
    def validate_queue_counts(self) -> Self:
        if self.total != len(self.items):
            raise ValueError("issue queue total must equal returned items")
        if self.total > self.total_matched:
            raise ValueError("issue queue total cannot exceed matched total")
        returned_incidents = {item.incident_id for item in self.items if item.incident_id}
        if any(change.incident_id not in returned_incidents for change in self.recent_changes):
            raise ValueError("issue queue changes must reference returned incidents")
        return self


class ResourceIssueOnset(StrictModel):
    """Server-owned first-observation projection without a guessed detector phase."""

    first_observed_at: str
    source: Literal["timeline_created_at"] = "timeline_created_at"
    timing_kind: None = None
    timing_availability: Literal["unavailable"] = "unavailable"
    timing_reason_code: Literal["health_transition_evidence_unavailable"] = (
        "health_transition_evidence_unavailable"
    )


class ResourceIssueItem(RcaIssueItem):
    onset: ResourceIssueOnset


class ResourceIssueListResponse(StrictModel):
    """Bounded resource issue list and its independently verified inventory freshness."""

    scope: ClusterScope
    coverage_availability: Literal["available", "partial", "unavailable"]
    observed_at: str | None = None
    reason_codes: tuple[str, ...] = ()
    items: list[ResourceIssueItem]
    limit: int = Field(ge=1, le=100)
    has_more: bool

    @model_validator(mode="after")
    def incomplete_coverage_has_a_reason(self) -> Self:
        if self.coverage_availability != "available" and not self.reason_codes:
            raise ValueError("incomplete resource issue coverage requires a reason")
        return self


ChangeTimelineEventKind = Literal[
    "inventory_event",
    "incident",
    "deployment",
    "gitops_change",
]
ChangeTimelineSeverity = Literal["info", "warning", "critical", "unknown"]


class ChangeTimelineBucket(StrictModel):
    startMs: int = Field(ge=0)
    endMs: int = Field(gt=0)
    total: int = Field(ge=0)
    warnings: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.startMs >= self.endMs or self.warnings > self.total:
            raise ValueError("change timeline bucket is invalid")
        return self


class ChangeTimelineEvent(StrictModel):
    id: str = Field(min_length=1, max_length=512)
    kind: ChangeTimelineEventKind
    occurredMs: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=240)
    severity: ChangeTimelineSeverity


class ChangeTimelineGap(StrictModel):
    from_: int = Field(alias="from", serialization_alias="from", ge=0)
    to: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.from_ >= self.to:
            raise ValueError("change timeline gap is invalid")
        return self


class ChangeTimelineResponse(StrictModel):
    buckets: list[ChangeTimelineBucket] = Field(default_factory=list)
    events: list[ChangeTimelineEvent] = Field(default_factory=list)
    gaps: list[ChangeTimelineGap] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        bucket_ranges = [(item.startMs, item.endMs) for item in self.buckets]
        gap_ranges = [(item.from_, item.to) for item in self.gaps]
        if any(
            current[0] < previous[1]
            for previous, current in zip(bucket_ranges, bucket_ranges[1:], strict=False)
        ):
            raise ValueError("change timeline buckets are not ordered")
        if any(
            current[0] < previous[1]
            for previous, current in zip(gap_ranges, gap_ranges[1:], strict=False)
        ):
            raise ValueError("change timeline gaps are not ordered")
        event_keys = [(item.occurredMs, item.kind, item.id) for item in self.events]
        if event_keys != sorted(event_keys):
            raise ValueError("change timeline events are not ordered")
        return self


class ActivityOverviewBucket(StrictModel):
    from_ms: int = Field(ge=0)
    to_ms: int = Field(gt=0)
    deployments: int = Field(ge=0)
    alerts: int = Field(ge=0)
    critical: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.from_ms >= self.to_ms:
            raise ValueError("activity overview bucket must have positive width")
        return self


class ActivityOverviewResponse(StrictModel):
    from_ms: int = Field(ge=0)
    to_ms: int = Field(gt=0)
    bucket_ms: int = Field(ge=1)
    buckets: list[ActivityOverviewBucket] = Field(min_length=1, max_length=366)

    @model_validator(mode="after")
    def validate_buckets(self) -> Self:
        if self.from_ms >= self.to_ms:
            raise ValueError("activity overview window must have positive width")
        if self.buckets[0].from_ms != self.from_ms or self.buckets[-1].to_ms != self.to_ms:
            raise ValueError("activity overview buckets must cover the requested window")
        if any(
            previous.to_ms != current.from_ms
            for previous, current in zip(self.buckets, self.buckets[1:], strict=False)
        ):
            raise ValueError("activity overview buckets must be contiguous")
        return self


class AuditTimelineItem(StrictModel):
    event_id: str = Field(min_length=1)
    subject: str
    source: str
    created_at: str
    causation_id: str | None = None
    journey_stage: AuditJourneyStage
    payload_summary: JsonMap = Field(default_factory=dict)


class AuditTimelineResponse(StrictModel):
    items: list[AuditTimelineItem] = Field(default_factory=list)
    limit: int
    has_more: bool
    next_cursor: str | None = None


class RecentChangeListResponse(StrictModel):
    incident_id: str
    items: list[RecentChangeItem] = Field(default_factory=list)
    limit: int


class RcaIncidentResponse(StrictModel):
    item: RcaTimelineItem


class EvidenceSourceSummaryItem(StrictModel):
    """저장된 evidence 원문에서 추출한 안전 요약 — raw payload 값은 제외."""

    source: str
    summary: str
    schema_version: int | None = None
    collector: str | None = None
    collector_version: str | None = None
    source_version: str | None = None
    query_version: str | None = None
    collected_at: str | None = None
    evidence_key: str | None = None
    source_id: str | None = None
    agent_id: str | None = None
    window_start: str | None = None


class EvidenceRecordItem(StrictModel):
    """저장된 evidence row 하나 — raw payload 대신 안전 요약만 노출."""

    id: int
    workspace_id: str
    correlation_id: str
    kind: str
    cluster_id: str | None = None
    evidence_ref: str | None = None
    summary: str
    sources: list[EvidenceSourceSummaryItem] = Field(default_factory=list)
    created_at: str | None = None


class EvidenceQueryResponse(StrictModel):
    items: list[EvidenceRecordItem]
    limit: int
    offset: int
    has_more: bool
    next_cursor: str | None = None


class EvidenceWindowSummaryItem(StrictModel):
    """저장된 evidence window 목록 — 원문 payload 없이 source 존재 여부만 노출."""

    evidence_key: str
    workspace_id: str
    cluster_id: str | None = None
    source_id: str | None = None
    window_start: str | None = None
    agent_id: str | None = None
    correlation_id: str | None = None
    sources: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


class EvidenceWindowListResponse(StrictModel):
    items: list[EvidenceWindowSummaryItem]
    limit: int
    offset: int
    has_more: bool


class EvidenceWindowPayloadResponse(StrictModel):
    """저장된 evidence window 원문 조회 — RCA 스키마 확정용 read-only debug 응답."""

    evidence_key: str
    workspace_id: str
    cluster_id: str | None = None
    source: str | None = None
    payload: JsonMap


class RcaCandidateScoreItem(StrictModel):
    """원인 후보 1개의 평가 결과 — 카탈로그 메타(제목/출처) + 평가(점수/근거) 병합."""

    candidate_id: str
    title: str | None = None
    source: str | None = None  # rule | ai_fallback
    score: float | None = None
    reason: str | None = None
    supporting_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


class RcaEvidenceRefItem(StrictModel):
    """판단에 실제 사용된 근거 참조 — 어떤 소스에 어떤 쿼리를 던져 얻었는지."""

    source: str
    name: str
    check_id: str | None = None
    summary: str | None = None
    query: str | None = None
    evidence_ref: str | None = None
    schema_version: int | None = None
    source_version: str | None = None
    collector: str | None = None
    collector_version: str | None = None
    query_version: str | None = None
    collected_at: str | None = None
    evidence_key: str | None = None
    source_id: str | None = None
    agent_id: str | None = None
    window_start: str | None = None


class RcaMissingCheckItem(StrictModel):
    """확정에 필요하지만 미충족인 근거 수집 상태."""

    check_id: str
    source: str | None = None
    status: str | None = None
    reason: str | None = None


class RcaNarrativeItem(StrictModel):
    """Evidence-bounded prose generated after deterministic RCA completion."""

    locale: Literal["ko"]
    executive_summary: str
    impact: str
    reasoning: str
    recommended_action: str
    recurrence_prevention: list[str] = Field(min_length=1)
    limitations: list[str] = Field(min_length=1)


class RcaReportSummaryItem(StrictModel):
    """저장된 RCA report 요약 — payload 원문 대신 화이트리스트 필드만 노출(secret 유출 방지)."""

    id: int
    workspace_id: str
    correlation_id: str
    analysis_status: Literal["completed", "blocked"] = "completed"
    root_cause: str
    action: str
    incident_id: str | None = None
    cluster_id: str | None = None
    symptom: str | None = None
    severity: str | None = None
    first_seen_at: str | None = None
    confidence: float | None = None
    reason: str | None = None
    evidence_ref: str | None = None
    supporting_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    evidence_summary: str | None = None
    evidence_bundle_summary: str | None = None
    created_at: str | None = None
    # ── 분석 심화(화이트리스트) — 대상 리소스·부증상·후보 점수·근거 쿼리 트레일 ──
    resource_kind: str | None = None
    resource_name: str | None = None
    namespace: str | None = None
    secondary_symptoms: list[str] = Field(default_factory=list)
    selected_candidate_id: str | None = None
    candidates: list[RcaCandidateScoreItem] = Field(default_factory=list)
    supporting_evidence_refs: list[RcaEvidenceRefItem] = Field(default_factory=list)
    missing_evidence_checks: list[RcaMissingCheckItem] = Field(default_factory=list)
    narrative: RcaNarrativeItem | None = None
    narrative_status: Literal["generated", "unavailable"] = "unavailable"


class RcaReportListResponse(StrictModel):
    items: list[RcaReportSummaryItem]
    limit: int
    offset: int
    has_more: bool
    next_cursor: str | None = None


class RecoveryActionCandidateItem(StrictModel):
    action_id: str
    title: str
    description: str
    route: str
    rank: int
    score: float
    risk_level: str
    blast_radius: str
    approval_required: bool
    prerequisites: list[str] = Field(default_factory=list)
    validation_checks: list[str] = Field(default_factory=list)
    rollback_plan: str
    evidence_refs: list[str] = Field(default_factory=list)
    recommendation_reason: str | None = None
    expected_outcome: str | None = None
    risk_explanation: str | None = None
    rollback_reason: str | None = None


class RecoveryPlanStatusResponse(StrictModel):
    plan_id: str
    correlation_id: str
    incident_id: str
    evidence_ref: str
    status: str
    summary: str
    target: JsonMap = Field(default_factory=dict)
    recommended_action_id: str
    execution_route: str
    selection_required: bool
    selected_action_id: str | None = None
    selected_by: str | None = None
    selected_action: RecoveryActionCandidateItem | None = None
    candidates: list[RecoveryActionCandidateItem] = Field(default_factory=list)
    lifecycle: JsonMap | None = None


class RemediationBundleMeta(StrictModel):
    correlation_id: str
    incident_id: str | None
    cluster_id: str
    workspace_id: str
    created_at: str | None


class RemediationBundleDiagnosis(StrictModel):
    root_cause: str
    confidence: float | None
    supporting_evidence: list[str]
    missing_evidence: list[str]
    supporting_evidence_refs: list[RcaEvidenceRefItem]
    missing_evidence_checks: list[RcaMissingCheckItem]
    selected_candidate_id: str | None


class RemediationBundleActionDraft(StrictModel):
    action_type: str
    namespace: str
    resource_kind: str
    resource_name: str
    reason: str
    risk_level: str
    dry_run: bool
    source_evidence: list[str]
    params: JsonMap


class RemediationBundleRecoveryCandidate(RecoveryActionCandidateItem):
    """Bundle candidate sharing the canonical recovery-plan contract.

    The bundle adds an executable draft while keeping the three evidence and
    validation lists required for its detail surface.  Common recovery copy
    fields are inherited so the two APIs cannot silently drift again.
    """

    draft: RemediationBundleActionDraft
    prerequisites: list[str]
    validation_checks: list[str]
    evidence_refs: list[str]


class RemediationBundleRemediation(StrictModel):
    status: str
    selected_action_id: str | None
    selected_by: str | None
    candidates: list[RemediationBundleRecoveryCandidate]
    evidence_ref: str


class RemediationBundleResponse(StrictModel):
    meta: RemediationBundleMeta
    diagnosis: RemediationBundleDiagnosis
    remediation: RemediationBundleRemediation | None


class RcaTestScenarioExpectedItem(StrictModel):
    root_cause: str
    symptom: str


class RcaTestScenarioSafetyItem(StrictModel):
    namespace: Literal["sandbox"]
    cleanup_required: Literal[True]
    ttl_seconds: int
    management_cluster_allowed: Literal[False]
    resource_name_prefix: Literal["rca-test-"]
    max_concurrent_runs: int


class RcaTestScenarioAdapterItem(StrictModel):
    adapter: Literal[
        "kubernetes.deployment",
        "gitops.fixture",
        "external.fixture",
        "kubernetes.manifest_delete",
        "fixture.reset",
    ]
    params: JsonMap = Field(default_factory=dict)


class RcaTestScenarioObservationItem(StrictModel):
    timeout_seconds: int
    poll_seconds: int
    pod_waiting_reasons: list[str] = Field(default_factory=list)
    pod_terminated_reasons: list[str] = Field(default_factory=list)
    event_reasons: list[str] = Field(default_factory=list)
    event_message_any: list[str] = Field(default_factory=list)
    log_message_any: list[str] = Field(default_factory=list)
    deployment_condition_reasons: list[str] = Field(default_factory=list)
    external_status_any: list[str] = Field(default_factory=list)


class RcaTestScenarioItem(StrictModel):
    scenario_id: str
    version: int
    title: str
    description: str
    execution: Literal["real", "hybrid", "external"]
    availability: Literal[
        "ready",
        "verification_pending",
        "fixture_required",
        "detector_gap",
    ]
    availability_reason: str | None = None
    verification_work_needed: list[str] = Field(default_factory=list)
    fixture_requirements: list[str] = Field(default_factory=list)
    detector_work_needed: list[str] = Field(default_factory=list)
    expected: RcaTestScenarioExpectedItem
    evidence_sources: list[Literal["kubernetes", "metrics", "logs", "traces", "metadata"]]
    safety: RcaTestScenarioSafetyItem
    trigger: RcaTestScenarioAdapterItem
    observe: RcaTestScenarioObservationItem
    cleanup: RcaTestScenarioAdapterItem


class RcaTestScenarioListResponse(StrictModel):
    items: list[RcaTestScenarioItem] = Field(default_factory=list)


class RcaTestRunResponse(StrictModel):
    accepted: bool = True
    run_id: str
    scenario_id: str
    scenario_version: int
    cluster_id: str
    correlation_id: str
    command_id: str
    evidence_key: str
    status: str
    cleanup_at: str
    verification_mode: bool = False
    failure: JsonMap | None = None
    steps: list[JsonMap] = Field(default_factory=list)


class EvidenceJobScheduleResponse(StrictModel):
    accepted: bool
    evidence_key: str
    queued: int
    job_ids: list[str]


class EvidenceJobPollResponse(StrictModel):
    job: JsonMap | None


class EvidenceJobResultResponse(StrictModel):
    accepted: bool
    evidence_key: str | None = None
    event_id: str | None = None
    correlation_id: str | None = None


class InventorySnapshotResponse(StrictModel):
    accepted: bool
    snapshot_id: str
    cluster_id: str
    resource_count: int
    marked_deleted: int = 0
    resource_types: list[str] = Field(default_factory=list)


class InventoryResourceResponse(StrictModel):
    inventory_key: str
    snapshot_id: str
    workspace_id: str
    cluster_id: str
    resource_type: str
    api_version: str
    kind: str
    namespace: str | None = None
    name: str
    uid: str | None = None
    resource_version: str | None = None
    status: str
    health: str
    labels: JsonMap = Field(default_factory=dict)
    annotations: JsonMap = Field(default_factory=dict)
    summary: JsonMap = Field(default_factory=dict)
    observed_at: str | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    deleted_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class InventoryResourceListResponse(StrictModel):
    cluster_id: str
    resource_type: str | None = None
    resources: list[InventoryResourceResponse]


ConfigReferenceKind = Literal["ConfigMap", "Secret"]
ConfigReferenceSource = Literal["env", "env_from", "volume", "volume_mount"]
ConfigReferenceAvailability = Literal["available", "partial", "unavailable"]


class ConfigReferenceWorkload(StrictModel):
    kind: Literal["Deployment"]
    namespace: str = Field(min_length=1, max_length=gateway_limits.KUBERNETES_NAME_MAX_LENGTH)
    name: str = Field(min_length=1, max_length=gateway_limits.KUBERNETES_NAME_MAX_LENGTH)
    uid: str | None = Field(default=None, max_length=gateway_limits.KUBERNETES_NAME_MAX_LENGTH)


class ConfigReferenceUsage(StrictModel):
    workload: ConfigReferenceWorkload
    source: ConfigReferenceSource
    container_name: str | None = Field(
        default=None,
        max_length=gateway_limits.KUBERNETES_NAME_MAX_LENGTH,
    )
    env_name: str | None = Field(default=None, max_length=gateway_limits.KUBERNETES_NAME_MAX_LENGTH)
    key: str | None = Field(default=None, max_length=gateway_limits.KUBERNETES_NAME_MAX_LENGTH)
    prefix: str | None = Field(default=None, max_length=gateway_limits.KUBERNETES_NAME_MAX_LENGTH)
    volume_name: str | None = Field(
        default=None,
        max_length=gateway_limits.KUBERNETES_NAME_MAX_LENGTH,
    )
    mount_path: str | None = Field(
        default=None,
        max_length=gateway_limits.FILTER_VALUE_LIST_MAX_LENGTH,
    )
    read_only: bool | None = None
    optional: bool | None = None


class ConfigReferenceItem(StrictModel):
    kind: ConfigReferenceKind
    namespace: str = Field(min_length=1, max_length=gateway_limits.KUBERNETES_NAME_MAX_LENGTH)
    name: str = Field(min_length=1, max_length=gateway_limits.KUBERNETES_NAME_MAX_LENGTH)
    referenced_by: list[ConfigReferenceUsage] = Field(
        default_factory=list,
        max_length=gateway_limits.INVENTORY_RESOURCE_MAX_LIMIT,
    )


class ConfigReferenceCoverage(StrictModel):
    availability: ConfigReferenceAvailability
    snapshot_id: str | None = Field(default=None, max_length=gateway_limits.CLUSTER_ID_MAX_LENGTH)
    observed_at: str | None = Field(default=None, max_length=80)
    workload_count: int = Field(ge=0)
    projected_reference_count: int = Field(ge=0)
    reason_codes: tuple[str, ...] = Field(
        default=(),
        max_length=gateway_limits.CONFIG_REFERENCE_REASON_CODE_MAX_COUNT,
    )


class ConfigReferenceListResponse(StrictModel):
    cluster_id: str = Field(min_length=1, max_length=gateway_limits.CLUSTER_ID_MAX_LENGTH)
    namespace: str | None = Field(
        default=None,
        max_length=gateway_limits.KUBERNETES_NAME_MAX_LENGTH,
    )
    items: list[ConfigReferenceItem] = Field(
        default_factory=list,
        max_length=gateway_limits.INVENTORY_RESOURCE_MAX_LIMIT,
    )
    coverage: ConfigReferenceCoverage


class InventoryResourceDetailResponse(StrictModel):
    """단일 Kubernetes 리소스 드릴다운 — raw object 없이 실제 read model 관계만 노출."""

    cluster_id: str
    identity: JsonMap
    resource: InventoryResourceResponse
    provider_detail: ResourceProviderDetail | None = None
    access: ResourceAccessDetail | None = None
    related: dict[str, list[InventoryResourceResponse]] = Field(default_factory=dict)
    events: list[InventoryResourceResponse] = Field(default_factory=list)


ResourceCapabilityExecution = Literal["command", "terminal", "resource-files"]
ResourceCapabilityInputType = Literal["boolean", "integer", "string"]
ResourceCapabilityRequestContext = Literal["simple", "exact-resource", "rollback"]
ResourceCapabilityResultIntent = Literal[
    "refresh-resource",
    "resource-summary",
    "terminal-session",
    "resource-files",
]


class ResourceCapabilitySubject(StrictModel):
    """Capability 판정이 묶인 exact inventory resource identity."""

    resource_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    namespace: str | None = None
    name: str = Field(min_length=1)


class ResourceCapabilityInput(StrictModel):
    """Server-owned field definition for one executable resource capability."""

    key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=120)
    type: ResourceCapabilityInputType
    required: bool = True
    minimum: int | None = None
    maximum: int | None = None
    default: bool | int | str | None = None
    prefill_result_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_]*$",
    )

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("capability input minimum must not exceed maximum")
        if self.type == "boolean" and self.default is not None and type(self.default) is not bool:
            raise ValueError("boolean capability input default must be a boolean")
        if self.type == "integer" and self.default is not None and type(self.default) is not int:
            raise ValueError("integer capability input default must be an integer")
        if self.type == "string" and self.default is not None and type(self.default) is not str:
            raise ValueError("string capability input default must be a string")
        if type(self.default) is int:
            if self.minimum is not None and self.default < self.minimum:
                raise ValueError("capability input default is below minimum")
            if self.maximum is not None and self.default > self.maximum:
                raise ValueError("capability input default is above maximum")
        return self


class ResourceActionCapability(StrictModel):
    """A server-owned, immediately executable action for the exact resource."""

    capability_id: str = Field(min_length=1, max_length=160, pattern=r"^[a-z][a-z0-9._-]*$")
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    execution: ResourceCapabilityExecution
    confirmation_required: bool = True
    realtime: bool = True
    input_schema: list[ResourceCapabilityInput] = Field(default_factory=list)
    method: Literal["POST", "WEBSOCKET"] = "POST"
    path: str = Field(min_length=1, pattern=r"^/")
    request_context: ResourceCapabilityRequestContext = "simple"
    result_intent: ResourceCapabilityResultIntent = "refresh-resource"


class ResourceCapabilitiesResponse(StrictModel):
    """권한 없는 버튼을 그리지 않도록 enabled action만 담는 응답."""

    subject: ResourceCapabilitySubject
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    capabilities: list[ResourceActionCapability] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_capabilities(self) -> Self:
        capability_ids = [item.capability_id for item in self.capabilities]
        if capability_ids != sorted(capability_ids):
            raise ValueError("resource capabilities must be sorted")
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("resource capabilities must be unique")
        return self


class ResourceDeletePreviewRef(ResourceRef):
    """Exact observed identity used by the delete confirmation and CAS."""

    resource_version: str = Field(min_length=1)


class ResourceDeletePreviewResponse(StrictModel):
    """Bounded authoritative owner-reference cascade for one exact root."""

    root: ResourceDeletePreviewRef
    dependents: list[ResourceDeletePreviewRef] = Field(default_factory=list, max_length=200)
    revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    truncated: Literal[False] = False
    max_dependents: int = Field(default=200, ge=1, le=200)

    @model_validator(mode="after")
    def validate_distinct_resources(self) -> Self:
        identities = [(item.uid, item.resource_version) for item in [self.root, *self.dependents]]
        if len(identities) != len(set(identities)):
            raise ValueError("delete preview resources must be unique")
        return self


class WorkloadRollbackChange(StrictModel):
    path: str = Field(min_length=1, max_length=500)
    before: str = Field(max_length=500)
    after: str = Field(max_length=500)


class WorkloadRollbackCurrent(StrictModel):
    resource: ResourceRef
    resource_version: str = Field(min_length=1, max_length=253)
    template_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class WorkloadRollbackRevision(StrictModel):
    revision: str = Field(min_length=1, max_length=253)
    resource: ResourceRef
    resource_version: str = Field(min_length=1, max_length=253)
    created_at: str | None = None
    template_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    preview_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    changes: list[WorkloadRollbackChange] = Field(default_factory=list, max_length=200)


class WorkloadRevisionHistoryResponse(StrictModel):
    availability: Literal["available", "unavailable"]
    completeness: Literal["exact", "partial"]
    reason: str | None = Field(default=None, max_length=160)
    snapshot_id: str = Field(min_length=1)
    current: WorkloadRollbackCurrent
    revisions: list[WorkloadRollbackRevision] = Field(default_factory=list, max_length=50)
    next_cursor: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if self.availability == "available" and (
            self.completeness != "exact" or self.reason is not None or not self.revisions
        ):
            raise ValueError("available rollback history requires exact revisions without a reason")
        if self.availability == "unavailable" and (
            self.reason is None or self.revisions or self.next_cursor is not None
        ):
            raise ValueError("unavailable rollback history cannot expose revisions")
        revision_ids = [item.revision for item in self.revisions]
        resource_ids = [(item.resource.uid, item.resource_version) for item in self.revisions]
        if len(revision_ids) != len(set(revision_ids)) or len(resource_ids) != len(
            set(resource_ids)
        ):
            raise ValueError("rollback revisions must have unique revision and resource identities")
        return self


class ResourceManifestSourceChoice(StrictModel):
    application_id: str
    application_name: str
    repository_ref: str
    branch: str
    manifest_path: str
    environment: str


class ResourceManifestEditTarget(StrictModel):
    resource_id: str = Field(min_length=1)
    relationship: Literal["self", "owner"]
    kind: str = Field(min_length=1)
    namespace: str | None = None
    name: str = Field(min_length=1)


class ResourceManifestSourceResponse(StrictModel):
    resource_id: str
    status: Literal["available", "ambiguous", "unsupported"]
    choices: list[ResourceManifestSourceChoice] = Field(default_factory=list)
    selected: ResourceManifestSourceChoice | None = None
    base_sha: str | None = None
    source_sha256: str | None = None
    source_revision_token: str | None = None
    content: str | None = None
    reason: str | None = None
    live_yaml: str | None = None
    live_observed_at: str | None = None
    live_reason: str | None = None
    edit_target: ResourceManifestEditTarget | None = None


class ResourceManifestImpact(StrictModel):
    api_version: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    namespace: str | None = None
    name: str = Field(min_length=1)
    selected: bool = False


class ResourceManifestPreviewResponse(StrictModel):
    valid: bool
    changed: bool
    base_sha: str
    source_sha256: str
    desired_sha256: str
    diff: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    apply_availability: Literal["available", "unavailable"] = "unavailable"
    apply_reason_codes: list[str] = Field(default_factory=list)
    impact: list[ResourceManifestImpact] = Field(default_factory=list)


class ResourceManifestCreateCapabilityResource(StrictModel):
    api_version: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    force_supported: bool


class ResourceManifestCreateCapabilityResponse(StrictModel):
    cluster_id: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    snapshot_id: str | None = None
    available: bool
    reason_codes: list[str] = Field(default_factory=list)
    max_documents: int = Field(ge=1)
    max_bytes: int = Field(ge=1)
    resources: list[ResourceManifestCreateCapabilityResource] = Field(default_factory=list)


class ResourceManifestApproveResponse(StrictModel):
    accepted: bool
    event_id: str
    correlation_id: str
    workflow_run_id: str
    approval_id: str
    sync_state: Literal["awaiting_pr_merge"] = "awaiting_pr_merge"


FilterCountCompleteness = Literal["exact", "partial", "unavailable"]
FilterFacetAvailability = Literal["available", "restricted", "unresolved"]
FilterFacetAxis = Literal[*gateway_facets.RESOURCE_FILTER_FACET_AXES]
FilterSurface = Literal["resources", "issues", "applications", "gitops", "checks"]


class ClusterFilterFacetItem(StrictModel):
    axis: Literal["cluster"] = "cluster"
    value: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    name: str | None = None
    provider: str | None = None
    availability: FilterFacetAvailability


class NamespaceFilterFacetItem(StrictModel):
    axis: Literal["namespace"] = "namespace"
    value: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    availability: FilterFacetAvailability


class ApplicationFilterFacetItem(StrictModel):
    axis: Literal["application"] = "application"
    value: str = Field(min_length=1)
    application_id: str = Field(min_length=1)
    name: str | None = None
    environment: str | None = None
    availability: FilterFacetAvailability


class SelectedFilterFacetResolution(StrictModel):
    axis: Literal["cluster", "namespace", "application"]
    value: str = Field(min_length=1)
    status: Literal["resolved", "restricted", "unresolved", "unavailable"]
    display_label: str | None = None


class FilterResultCounts(StrictModel):
    filtered_count: int | None = Field(default=None, ge=0)
    unfiltered_count: int | None = Field(default=None, ge=0)
    filtered_count_completeness: FilterCountCompleteness
    unfiltered_count_completeness: FilterCountCompleteness


class FilterSnapshotMeta(StrictModel):
    snapshot_revision: int = Field(ge=0)
    authorization_revision: str = Field(min_length=1)
    filter_fingerprint: str = Field(min_length=1)
    observed_at: str | None = None
    stale: bool
    partial_reason_codes: list[str] = Field(default_factory=list)


class ResourceFilterFacetPageResponse(StrictModel):
    axis: FilterFacetAxis
    items: list[ClusterFilterFacetItem | NamespaceFilterFacetItem | ApplicationFilterFacetItem] = (
        Field(default_factory=list)
    )
    selected_resolutions: list[SelectedFilterFacetResolution] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool
    snapshot: FilterSnapshotMeta


class GlobalClusterFacetItem(StrictModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    count: int | None = Field(default=None, ge=0)
    count_completeness: FilterCountCompleteness


class GlobalNamespaceFacetItem(GlobalClusterFacetItem):
    cluster_id: str = Field(min_length=1)


class GlobalApplicationFacetItem(GlobalClusterFacetItem):
    pass


class GlobalLabelFacetItem(StrictModel):
    key: str = Field(min_length=1)
    value: str
    count: int | None = Field(default=None, ge=0)
    count_completeness: FilterCountCompleteness


class GlobalResourceFacetItem(GlobalClusterFacetItem):
    kind: str = Field(min_length=1)


class GlobalResourceTypeFacetItem(GlobalClusterFacetItem):
    pass


class GlobalFilterFacetsResponse(StrictModel):
    clusters: list[GlobalClusterFacetItem] = Field(default_factory=list)
    namespaces: list[GlobalNamespaceFacetItem] = Field(default_factory=list)
    applications: list[GlobalApplicationFacetItem] = Field(default_factory=list)
    resource_types: list[GlobalResourceTypeFacetItem] = Field(default_factory=list)
    labels: list[GlobalLabelFacetItem] = Field(default_factory=list)
    resources: list[GlobalResourceFacetItem] = Field(default_factory=list)


class ResourceIdentitySearchHit(StrictModel):
    id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    resource: ResourceRef
    matched_fields: list[
        Literal["name", "kind", "namespace", "api_version", "resource_type", "uid"]
    ] = Field(default_factory=list)
    observed_at: str | None = None


class ResourceIdentitySearchResponse(StrictModel):
    scopes: list[ClusterScope] = Field(default_factory=list)
    hits: list[ResourceIdentitySearchHit] = Field(default_factory=list)
    total: int = Field(ge=0)
    total_completeness: FilterCountCompleteness
    snapshot: FilterSnapshotMeta


class InventoryResourceClusterIdentity(StrictModel):
    cluster_id: str = Field(min_length=1)
    name: str | None = None
    provider: str | None = None


class ResourceTableMetricEvidence(StrictModel):
    resource_uid: str | None = Field(default=None, min_length=1)
    source_snapshot_id: str = Field(min_length=1)
    observed_at: str | None = Field(default=None, min_length=1)
    measurement_window: str | None = Field(default=None, min_length=1, max_length=64)
    cpu_mcores: float | None = Field(default=None, ge=0)
    memory_mib: float | None = Field(default=None, ge=0)
    completeness: FilterCountCompleteness
    reason_codes: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("reason_codes")
    @classmethod
    def validate_reason_codes(cls, values: list[str]) -> list[str]:
        if values != sorted(set(values)) or any(not value for value in values):
            raise ValueError("resource table metric reasons must be unique and ordered")
        return values

    def validate_evidence(self, required: tuple[float | int | str | None, ...]) -> Self:
        if self.completeness == "exact" and (
            any(value is None for value in required) or self.reason_codes
        ):
            raise ValueError("exact resource table metrics require complete evidence")
        if self.completeness == "partial" and not self.reason_codes:
            raise ValueError("partial resource table metrics require reason codes")
        if self.completeness == "unavailable" and (
            self.cpu_mcores is not None or self.memory_mib is not None or not self.reason_codes
        ):
            raise ValueError("unavailable resource table metrics cannot expose usage")
        return self


class ResourceTablePodMetrics(ResourceTableMetricEvidence):
    kind: Literal["pod"] = "pod"
    cpu_request_mcores: float | None = Field(default=None, gt=0)
    cpu_limit_mcores: float | None = Field(default=None, gt=0)
    memory_request_mib: float | None = Field(default=None, gt=0)
    memory_limit_mib: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_pod_evidence(self) -> Self:
        return self.validate_evidence(
            (
                self.resource_uid,
                self.observed_at,
                self.measurement_window,
                self.cpu_mcores,
                self.memory_mib,
                self.cpu_request_mcores,
                self.cpu_limit_mcores,
                self.memory_request_mib,
                self.memory_limit_mib,
            )
        )


class ResourceTableNodeMetrics(ResourceTableMetricEvidence):
    kind: Literal["node"] = "node"
    cpu_allocatable_mcores: float | None = Field(default=None, ge=0)
    memory_allocatable_mib: float | None = Field(default=None, ge=0)
    pod_count: int | None = Field(default=None, ge=0)
    pod_allocatable: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_node_evidence(self) -> Self:
        return self.validate_evidence(
            (
                self.resource_uid,
                self.observed_at,
                self.measurement_window,
                self.cpu_mcores,
                self.memory_mib,
                self.cpu_allocatable_mcores,
                self.memory_allocatable_mib,
                self.pod_count,
                self.pod_allocatable,
            )
        )


class FilteredInventoryResourceItem(StrictModel):
    resource: InventoryResourceResponse
    cluster: InventoryResourceClusterIdentity
    application_ids: list[str] = Field(default_factory=list)
    application_binding_completeness: FilterCountCompleteness
    metrics: ResourceTablePodMetrics | ResourceTableNodeMetrics | None = None

    @model_validator(mode="after")
    def validate_metric_identity(self) -> Self:
        metrics = self.metrics
        if metrics is None:
            return self
        if metrics.kind != self.resource.resource_type:
            raise ValueError("resource table metrics must match resource type")
        if metrics.source_snapshot_id != self.resource.snapshot_id:
            raise ValueError("resource table metrics must match source snapshot")
        if metrics.resource_uid != self.resource.uid:
            raise ValueError("resource table metrics must match resource uid")
        return self


class FilteredInventoryResourceListResponse(StrictModel):
    items: list[FilteredInventoryResourceItem] = Field(default_factory=list, max_length=200)
    next_cursor: str | None = None
    has_more: bool
    counts: FilterResultCounts
    snapshot: FilterSnapshotMeta


class ResourceMetricHistoryPoint(StrictModel):
    observed_at: str = Field(min_length=1)
    cpu_mcores: float | None = Field(default=None, ge=0)
    mem_mib: float | None = Field(default=None, ge=0)


class ResourceMetricContainerObservation(StrictModel):
    name: str = Field(min_length=1, max_length=253)
    cpu_mcores: float | None = Field(default=None, ge=0)
    mem_mib: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_observed_metric(self) -> Self:
        if self.cpu_mcores is None and self.mem_mib is None:
            raise ValueError("container metric observation requires CPU or memory")
        return self


class ResourceMetricContainerHistorySeries(StrictModel):
    name: str = Field(min_length=1, max_length=253)
    points: list[ResourceMetricHistoryPoint] = Field(default_factory=list)
    completeness: FilterCountCompleteness
    partial_reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_history(self) -> Self:
        observed_at = [point.observed_at for point in self.points]
        if observed_at != sorted(observed_at) or len(set(observed_at)) != len(observed_at):
            raise ValueError("container metric history points must be unique and ordered")
        if self.completeness == "unavailable" and self.points:
            raise ValueError("unavailable container history cannot expose points")
        if self.completeness == "exact" and (not self.points or self.partial_reason_codes):
            raise ValueError("exact container history requires points without reasons")
        return self


class ResourceMetricCurrentObservation(StrictModel):
    observed_at: str = Field(min_length=1)
    measurement_window: str = Field(min_length=1, max_length=64)
    cpu_mcores: float | None = Field(default=None, ge=0)
    mem_mib: float | None = Field(default=None, ge=0)
    containers: list[ResourceMetricContainerObservation] = Field(
        default_factory=list,
        max_length=64,
    )
    container_metrics_complete: bool = False

    @model_validator(mode="after")
    def require_observed_metric(self) -> Self:
        if self.cpu_mcores is None and self.mem_mib is None:
            raise ValueError("current metric observation requires CPU or memory")
        names = [container.name for container in self.containers]
        if names != sorted(names) or len(set(names)) != len(names):
            raise ValueError("container metric observations must be unique and ordered")
        if self.container_metrics_complete and not self.containers:
            raise ValueError("complete container metrics require an observed container")
        return self


class ResourceMetricHistorySeries(StrictModel):
    resource_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    resource_type: Literal["pod", "node"]
    namespace: str | None = Field(default=None, min_length=1)
    name: str = Field(min_length=1)
    points: list[ResourceMetricHistoryPoint] = Field(default_factory=list)
    current_observation: ResourceMetricCurrentObservation | None = None
    container_series: list[ResourceMetricContainerHistorySeries] = Field(
        default_factory=list,
        max_length=64,
    )
    container_history_completeness: FilterCountCompleteness = "unavailable"
    container_history_reason_codes: list[str] = Field(default_factory=list)
    has_sparkline_points: bool
    completeness: FilterCountCompleteness
    partial_reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_metric_history(self) -> Self:
        if self.resource_type == "pod" and self.namespace is None:
            raise ValueError("pod metric history requires a namespace")
        if self.resource_type == "node" and self.namespace is not None:
            raise ValueError("node metric history must be cluster scoped")
        if self.resource_type == "node" and (
            self.container_series
            or self.container_history_completeness != "unavailable"
            or self.container_history_reason_codes != ["container_metrics_not_applicable"]
            or (
                self.current_observation is not None
                and (
                    self.current_observation.containers
                    or self.current_observation.container_metrics_complete
                )
            )
        ):
            raise ValueError("node current metrics cannot expose Pod containers")
        container_names = [item.name for item in self.container_series]
        if container_names != sorted(container_names) or len(set(container_names)) != len(
            container_names
        ):
            raise ValueError("container metric history series must be unique and ordered")
        if self.container_history_completeness == "unavailable" and self.container_series:
            raise ValueError("unavailable container metric history cannot expose series")
        if self.container_history_completeness == "exact" and (
            not self.container_series
            or self.container_history_reason_codes
            or any(item.completeness != "exact" for item in self.container_series)
        ):
            raise ValueError("exact container metric history requires exact series")
        observed_at = [point.observed_at for point in self.points]
        if observed_at != sorted(observed_at) or len(set(observed_at)) != len(observed_at):
            raise ValueError("resource metric history points must be unique and ordered")
        has_cpu = any(point.cpu_mcores is not None for point in self.points)
        if self.has_sparkline_points != has_cpu:
            raise ValueError("sparkline availability must reflect measured CPU points")
        if self.completeness == "unavailable" and has_cpu:
            raise ValueError("unavailable metric history cannot carry measured CPU points")
        if self.completeness == "exact" and (
            not self.points or any(point.cpu_mcores is None for point in self.points)
        ):
            raise ValueError("exact metric history requires CPU data at every returned point")
        if self.completeness == "exact" and self.partial_reason_codes:
            raise ValueError("exact metric history cannot carry partial reasons")
        return self


class ResourceMetricsHistoryResponse(StrictModel):
    refresh_policy_key: Literal[
        "metrics_kubernetes",
        "metrics_prometheus",
        "metrics_pvc",
        "metrics_rightsizing",
    ]
    series: list[ResourceMetricHistorySeries] = Field(default_factory=list)
    completeness: FilterCountCompleteness
    partial_reason_codes: list[str] = Field(default_factory=list)
    snapshot: FilterSnapshotMeta

    @model_validator(mode="after")
    def validate_metric_series(self) -> Self:
        resource_ids = [item.resource_id for item in self.series]
        if len(set(resource_ids)) != len(resource_ids):
            raise ValueError("resource metric history identities must be unique")
        if self.completeness == "exact" and (
            any(item.completeness != "exact" for item in self.series) or self.partial_reason_codes
        ):
            raise ValueError("exact metric history response cannot contain incomplete series")
        return self


GraphRelationKind = Literal["owns", "runs_on", "selects", "routes_to"]
GraphRelationPlane = Literal[
    "ownership",
    "placement",
    "network_configured",
    "network_effective",
]
GraphEvidenceType = Literal[
    "owner_reference",
    "node_assignment",
    "selector_match",
    "service_name_label",
]
GraphRelationCompleteness = Literal["exact", "partial", "unavailable"]
GraphNodeCategory = Literal[
    "workload",
    "pod",
    "node",
    "service",
    "endpoint",
    "event",
    "other",
]


class ResourceGraphDrilldownIdentity(StrictModel):
    version: Literal["v1"] = "v1"
    cluster_id: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    # Legacy/custom inventory rows may not declare an API version. The remaining v1 identity
    # tuple still resolves the existing detail route without turning valid rows into a 500.
    api_version: str
    kind: str = Field(min_length=1)
    namespace: str | None = None
    name: str = Field(min_length=1)
    uid: str | None = None


class ResourceGraphNode(StrictModel):
    node_id: str = Field(min_length=1)
    category: GraphNodeCategory
    identity: ResourceGraphDrilldownIdentity
    status: str
    health: str
    observed_at: str | None = None
    deleted_at: str | None = None
    application_ids: list[str] = Field(default_factory=list)
    application_binding_completeness: FilterCountCompleteness


class ResourceGraphEdgeEvidence(StrictModel):
    type: GraphEvidenceType
    authority: Literal["authoritative", "derived"]
    observed_at: str | None = None


class ResourceGraphEdge(StrictModel):
    edge_id: str = Field(min_length=1)
    from_node_id: str = Field(min_length=1)
    to_node_id: str = Field(min_length=1)
    kind: GraphRelationKind
    plane: GraphRelationPlane
    direction: Literal["directed"] = "directed"
    state: Literal["active", "historical"] = "active"
    evidence: ResourceGraphEdgeEvidence

    @model_validator(mode="after")
    def validate_relation_semantics(self) -> Self:
        expected = {
            "owns": ("ownership", "owner_reference", "authoritative"),
            "runs_on": ("placement", "node_assignment", "authoritative"),
            "selects": (None, "selector_match", "derived"),
            "routes_to": ("network_effective", "service_name_label", "authoritative"),
        }[self.kind]
        expected_plane, expected_evidence, expected_authority = expected
        if expected_plane is not None and self.plane != expected_plane:
            raise ValueError("graph relation plane does not match its kind")
        if self.kind == "selects" and self.plane not in {"ownership", "network_configured"}:
            raise ValueError("selector relation plane is invalid")
        if self.evidence.type != expected_evidence or self.evidence.authority != expected_authority:
            raise ValueError("graph relation evidence does not match its kind")
        return self


class ResourceGraphSnapshotResponse(StrictModel):
    graph_revision: str = Field(min_length=1)
    cluster_projection_revision: int = Field(ge=0)
    cluster: InventoryResourceClusterIdentity
    nodes: list[ResourceGraphNode] = Field(default_factory=list)
    edges: list[ResourceGraphEdge] = Field(default_factory=list)
    root_node_ids: list[str] = Field(default_factory=list)
    counts: FilterResultCounts
    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    omitted_node_count: int = Field(ge=0)
    omitted_edge_count: int = Field(ge=0)
    node_limit: int = Field(ge=1)
    edge_limit: int = Field(ge=1)
    truncated: bool
    relation_completeness: GraphRelationCompleteness
    partial_reason_codes: list[str] = Field(default_factory=list)
    snapshot: FilterSnapshotMeta

    @model_validator(mode="after")
    def validate_graph_integrity(self) -> Self:
        node_ids = [node.node_id for node in self.nodes]
        edge_ids = [edge.edge_id for edge in self.edges]
        known_nodes = set(node_ids)
        if len(known_nodes) != len(node_ids) or len(set(edge_ids)) != len(edge_ids):
            raise ValueError("graph identities must be unique")
        if self.node_count != len(self.nodes) or self.edge_count != len(self.edges):
            raise ValueError("graph counts must match returned records")
        if self.node_count > self.node_limit or self.edge_count > self.edge_limit:
            raise ValueError("graph records must honor response limits")
        if any(node.identity.cluster_id != self.cluster.cluster_id for node in self.nodes):
            raise ValueError("graph nodes must belong to the selected cluster")
        if any(
            edge.from_node_id not in known_nodes or edge.to_node_id not in known_nodes
            for edge in self.edges
        ):
            raise ValueError("graph edges must reference returned nodes")
        if not set(self.root_node_ids).issubset(known_nodes):
            raise ValueError("graph roots must reference returned nodes")
        if (self.omitted_node_count or self.omitted_edge_count) and not self.truncated:
            raise ValueError("omitted graph records require truncated=true")
        if self.truncated and self.relation_completeness == "exact":
            raise ValueError("truncated graph relations cannot be exact")
        if self.relation_completeness == "exact" and self.partial_reason_codes:
            raise ValueError("exact graph relations cannot carry partial reasons")
        return self


RelationsTopologyEdgeType = Literal["owns", "runs_on", "selects", "routes_to"]
TopologyAvailability = Literal["available", "unavailable"]


class RelationsTopologyResponse(ResourceGraphSnapshotResponse):
    """Evidence-backed Resources relationship graph for the /topology surface.

    The projection deliberately inherits the full resource-graph evidence model.
    It must never turn an unavailable inventory projection into guessed nodes or
    edges merely to keep the canvas populated.
    """

    view: Literal["relations"] = "relations"
    availability: TopologyAvailability
    refresh_after_seconds: int = Field(ge=1, le=60)

    @model_validator(mode="after")
    def validate_topology_availability(self) -> Self:
        unavailable = self.availability == "unavailable"
        if unavailable and (
            self.relation_completeness != "unavailable"
            or self.nodes
            or self.edges
            or self.root_node_ids
            or self.node_count != 0
            or self.edge_count != 0
            or self.counts.filtered_count is not None
            or self.counts.unfiltered_count is not None
            or self.counts.filtered_count_completeness != "unavailable"
            or self.counts.unfiltered_count_completeness != "unavailable"
        ):
            raise ValueError("unavailable topology must not claim graph evidence")
        if not unavailable and self.relation_completeness == "unavailable":
            raise ValueError("available topology requires relationship evidence state")
        return self


class PhysicalTopologyServer(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    cpu_pct: float | None = Field(default=None, ge=0)
    mem_pct: float | None = Field(default=None, ge=0)
    status: str
    matched_pod_count: int | None = Field(default=None, ge=0)
    total_pod_count: int | None = Field(default=None, ge=0)
    matched_pod_count_completeness: FilterCountCompleteness
    total_pod_count_completeness: FilterCountCompleteness

    @model_validator(mode="after")
    def validate_pod_counts(self) -> Self:
        pairs = (
            (self.matched_pod_count, self.matched_pod_count_completeness),
            (self.total_pod_count, self.total_pod_count_completeness),
        )
        if any((value is None) != (completeness == "unavailable") for value, completeness in pairs):
            raise ValueError("physical topology pod counts must match completeness")
        return self


class PhysicalTopologyPod(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    server_id: str | None = None
    # requests 대비 사용률이다. requests 근거가 projection에 없으면 0이 아니라 null이다.
    usage_pct: float | None = Field(default=None, ge=0)
    cpu_mcores: float | None = Field(default=None, ge=0)
    cpu_request_mcores: float | None = Field(default=None, gt=0)
    cpu_limit_mcores: float | None = Field(default=None, gt=0)
    mem_mib: float | None = Field(default=None, ge=0)
    mem_request_mib: float | None = Field(default=None, gt=0)
    mem_limit_mib: float | None = Field(default=None, gt=0)
    phase: str
    health: str
    restarts: int = Field(ge=0)
    matches_filter: bool

    @model_validator(mode="after")
    def validate_request_relative_usage(self) -> Self:
        requests_missing = self.cpu_request_mcores is None or self.mem_request_mib is None
        measurements_missing = self.cpu_mcores is None and self.mem_mib is None
        if self.usage_pct is not None and (requests_missing or measurements_missing):
            raise ValueError("physical topology usage requires complete request evidence")
        return self


class PhysicalTopologyResponse(StrictModel):
    view: Literal["physical"] = "physical"
    cluster_projection_revision: int = Field(ge=0)
    cluster: InventoryResourceClusterIdentity
    servers: list[PhysicalTopologyServer] = Field(default_factory=list)
    pods: list[PhysicalTopologyPod] = Field(default_factory=list)
    truncated: dict[str, int] = Field(default_factory=dict)
    unassigned_truncated_count: int = Field(ge=0)
    counts: FilterResultCounts
    projection_completeness: GraphRelationCompleteness
    metrics_completeness: GraphRelationCompleteness
    metrics_observed_at: str | None = None
    partial_reason_codes: list[str] = Field(default_factory=list)
    snapshot: FilterSnapshotMeta

    @model_validator(mode="after")
    def validate_physical_topology(self) -> Self:
        server_ids = [server.id for server in self.servers]
        pod_ids = [pod.id for pod in self.pods]
        known_servers = set(server_ids)
        if len(known_servers) != len(server_ids) or len(set(pod_ids)) != len(pod_ids):
            raise ValueError("physical topology identities must be unique")
        if any(
            pod.server_id is not None and pod.server_id not in known_servers for pod in self.pods
        ):
            raise ValueError("physical topology pods must reference returned servers")
        if not set(self.truncated).issubset(known_servers):
            raise ValueError("physical topology truncation must reference returned servers")
        if any(count <= 0 for count in self.truncated.values()):
            raise ValueError("physical topology truncation counts must be positive")
        if self.projection_completeness == "exact" and self.partial_reason_codes:
            raise ValueError("exact physical topology cannot carry partial reasons")
        return self


class LabelSelector(StrictModel):
    key: str = Field(min_length=1)
    value: str
    selector: str = Field(min_length=2)


class LabelFacetItem(LabelSelector):
    match_count: int | None = Field(default=None, ge=0)
    count_completeness: FilterCountCompleteness


class SelectedLabelResolution(LabelSelector):
    status: Literal["resolved", "zero", "restricted", "unavailable"]


class LabelFacetPageResponse(StrictModel):
    surface: FilterSurface
    items: list[LabelFacetItem] = Field(default_factory=list)
    selected_resolutions: list[SelectedLabelResolution] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool
    counts: FilterResultCounts
    snapshot: FilterSnapshotMeta


ApplicationFilterAxis = Literal[*gateway_facets.APPLICATION_FILTER_FACET_AXES]
ApplicationFilterAvailability = Literal["available", "partial", "unavailable"]


class ApplicationFilterItem(StrictModel):
    """Applications 목록 전용 provider-neutral DTO."""

    application_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    repository_ids: list[str] = Field(default_factory=list)
    cluster_ids: list[str] = Field(default_factory=list)
    namespace_refs: list[str] = Field(default_factory=list)
    environments: list[str] = Field(default_factory=list)
    lifecycle_status: str = Field(min_length=1)
    pending_promotion: bool
    binding_count: int = Field(ge=0)
    updated_at: str
    binding_completeness: FilterCountCompleteness
    label_projection_completeness: FilterCountCompleteness


class ApplicationSurfaceFilterFacetItem(StrictModel):
    """Applications surface facet; common catalog ApplicationFilterFacetItem과 별도다."""

    axis: ApplicationFilterAxis
    value: str = Field(min_length=1)
    label: str = Field(min_length=1)
    match_count: int | None = Field(default=None, ge=0)
    count_completeness: FilterCountCompleteness
    availability: ApplicationFilterAvailability

    @model_validator(mode="after")
    def validate_count_availability(self) -> Self:
        if self.availability == "unavailable" and self.match_count is not None:
            raise ValueError("unavailable application facet cannot expose a match count")
        if self.count_completeness == "unavailable" and self.match_count is not None:
            raise ValueError("unavailable application facet count must be null")
        return self


class ApplicationFilterCapability(StrictModel):
    axis: Literal[*gateway_facets.APPLICATION_FILTER_CAPABILITY_AXES]
    availability: ApplicationFilterAvailability
    reason_code: str | None = None
    source_semantics: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_reason(self) -> Self:
        if self.availability != "available" and not self.reason_code:
            raise ValueError("partial or unavailable application capability requires a reason")
        return self


class ApplicationSelectedFacetResolution(StrictModel):
    axis: Literal[
        "cluster",
        "namespace",
        "application",
        "environment",
        "status",
        "pending_promotion",
    ]
    value: str = Field(min_length=1)
    status: Literal["resolved", "zero", "restricted", "unavailable"]
    display_label: str | None = None


class ApplicationFilterResultsResponse(StrictModel):
    items: list[ApplicationFilterItem] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool
    counts: FilterResultCounts
    snapshot: FilterSnapshotMeta
    facets: list[ApplicationSurfaceFilterFacetItem] = Field(default_factory=list)
    capabilities: list[ApplicationFilterCapability] = Field(default_factory=list)
    selected_labels: list[SelectedLabelResolution] = Field(default_factory=list)


class ApplicationFilterFacetPageResponse(StrictModel):
    surface: Literal["applications"] = "applications"
    axis: ApplicationFilterAxis
    items: list[ApplicationSurfaceFilterFacetItem] = Field(default_factory=list)
    selected_resolutions: list[ApplicationSelectedFacetResolution] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool
    counts: FilterResultCounts
    snapshot: FilterSnapshotMeta
    capabilities: list[ApplicationFilterCapability] = Field(default_factory=list)


class ApplicationLabelFacetPageResponse(StrictModel):
    surface: Literal["applications"] = "applications"
    items: list[LabelFacetItem] = Field(default_factory=list)
    selected_resolutions: list[SelectedLabelResolution] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool
    counts: FilterResultCounts
    snapshot: FilterSnapshotMeta
    capabilities: list[ApplicationFilterCapability] = Field(default_factory=list)


GitOpsFilterAxis = Literal[*gateway_facets.GITOPS_FILTER_FACET_AXES]
GitOpsFilterAvailability = Literal["available", "partial", "unavailable"]


class GitOpsFilterItem(StrictModel):
    """Git provider와 저장 payload를 노출하지 않는 변경·승인 목록 DTO."""

    change_id: str = Field(min_length=1)
    application_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    binding_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    status: str = Field(min_length=1)
    current_step: str = Field(min_length=1)
    approval_status: str = Field(min_length=1)
    change_type: str | None = None
    summary: str | None = None
    updated_at: str
    change_type_completeness: FilterCountCompleteness
    label_projection_completeness: FilterCountCompleteness


class GitOpsFilterFacetItem(StrictModel):
    axis: GitOpsFilterAxis
    value: str = Field(min_length=1)
    label: str = Field(min_length=1)
    match_count: int | None = Field(default=None, ge=0)
    count_completeness: FilterCountCompleteness
    availability: GitOpsFilterAvailability

    @model_validator(mode="after")
    def validate_count_availability(self) -> Self:
        if self.availability == "unavailable" and self.match_count is not None:
            raise ValueError("unavailable GitOps facet cannot expose a match count")
        if self.count_completeness == "unavailable" and self.match_count is not None:
            raise ValueError("unavailable GitOps facet count must be null")
        return self


class GitOpsFilterCapability(StrictModel):
    axis: Literal[*gateway_facets.GITOPS_FILTER_CAPABILITY_AXES]
    availability: GitOpsFilterAvailability
    reason_code: str | None = None
    source_semantics: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_reason(self) -> Self:
        if self.availability != "available" and not self.reason_code:
            raise ValueError("partial or unavailable GitOps capability requires a reason")
        return self


class GitOpsSelectedFacetResolution(StrictModel):
    axis: Literal[
        "cluster",
        "namespace",
        "application",
        "environment",
        "approval",
        "change_type",
    ]
    value: str = Field(min_length=1)
    status: Literal["resolved", "zero", "restricted", "unavailable"]
    display_label: str | None = None


class GitOpsFilterResultsResponse(StrictModel):
    items: list[GitOpsFilterItem] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool
    counts: FilterResultCounts
    snapshot: FilterSnapshotMeta
    facets: list[GitOpsFilterFacetItem] = Field(default_factory=list)
    capabilities: list[GitOpsFilterCapability] = Field(default_factory=list)
    selected_labels: list[SelectedLabelResolution] = Field(default_factory=list)


class GitOpsFilterFacetPageResponse(StrictModel):
    surface: Literal["gitops"] = "gitops"
    axis: GitOpsFilterAxis
    items: list[GitOpsFilterFacetItem] = Field(default_factory=list)
    selected_resolutions: list[GitOpsSelectedFacetResolution] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool
    counts: FilterResultCounts
    snapshot: FilterSnapshotMeta
    capabilities: list[GitOpsFilterCapability] = Field(default_factory=list)


IssueFilterAxis = Literal[*gateway_facets.ISSUE_FILTER_FACET_AXES]
IssueFilterAvailability = Literal["available", "partial", "unavailable"]


class IssueFilterItem(StrictModel):
    """Issues 목록 전용 DTO — 저장 payload와 detail identity를 의도적으로 분리한다."""

    issue_id: str = Field(min_length=1)
    detail_id: str | None = None
    correlation_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    namespace: str | None = None
    resource_kind: str | None = None
    resource_name: str | None = None
    symptom: str | None = None
    severity: str | None = None
    category: str | None = None
    category_completeness: FilterCountCompleteness = "unavailable"
    issue_state: Literal["open", "resolved", "unknown"]
    current_subject: str = Field(min_length=1)
    pipeline_status: str = Field(min_length=1)
    environment: str | None = None
    environment_completeness: FilterCountCompleteness
    application_ids: list[str] = Field(default_factory=list)
    application_binding_completeness: FilterCountCompleteness
    label_projection_completeness: FilterCountCompleteness
    root_cause: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    updated_at: str


class IssueFilterFacetItem(StrictModel):
    axis: IssueFilterAxis
    value: str = Field(min_length=1)
    label: str = Field(min_length=1)
    match_count: int | None = Field(default=None, ge=0)
    count_completeness: FilterCountCompleteness
    availability: IssueFilterAvailability

    @model_validator(mode="after")
    def validate_count_availability(self) -> Self:
        if self.availability == "unavailable" and self.match_count is not None:
            raise ValueError("unavailable issue facet cannot expose a match count")
        if self.count_completeness == "unavailable" and self.match_count is not None:
            raise ValueError("unavailable issue facet count must be null")
        return self


class IssueFilterCapability(StrictModel):
    axis: Literal[*gateway_facets.ISSUE_FILTER_CAPABILITY_AXES]
    availability: IssueFilterAvailability
    reason_code: str | None = None
    source_semantics: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_reason(self) -> Self:
        if self.availability != "available" and not self.reason_code:
            raise ValueError("partial or unavailable issue capability requires a reason")
        return self


class IssueSelectedFacetResolution(StrictModel):
    axis: Literal[
        "cluster",
        "namespace",
        "application",
        "severity",
        "category",
        "status",
        "environment",
    ]
    value: str = Field(min_length=1)
    status: Literal["resolved", "zero", "restricted", "unavailable"]
    display_label: str | None = None


class IssueFilterResultsResponse(StrictModel):
    items: list[IssueFilterItem] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool
    counts: FilterResultCounts
    snapshot: FilterSnapshotMeta
    facets: list[IssueFilterFacetItem] = Field(default_factory=list)
    capabilities: list[IssueFilterCapability] = Field(default_factory=list)
    selected_labels: list[SelectedLabelResolution] = Field(default_factory=list)


class IssueFilterFacetPageResponse(StrictModel):
    surface: Literal["issues"] = "issues"
    axis: IssueFilterAxis
    items: list[IssueFilterFacetItem] = Field(default_factory=list)
    selected_resolutions: list[IssueSelectedFacetResolution] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool
    counts: FilterResultCounts
    snapshot: FilterSnapshotMeta
    capabilities: list[IssueFilterCapability] = Field(default_factory=list)


class IssueLabelFacetPageResponse(StrictModel):
    surface: Literal["issues"] = "issues"
    items: list[LabelFacetItem] = Field(default_factory=list)
    selected_resolutions: list[SelectedLabelResolution] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool
    counts: FilterResultCounts
    snapshot: FilterSnapshotMeta
    capabilities: list[IssueFilterCapability] = Field(default_factory=list)


class ClusterUsageSample(StrictModel):
    sampled_at: str | None = None
    usage: JsonMap = Field(default_factory=dict)


class ClusterUsageResponse(StrictModel):
    cluster_id: str
    samples: list[ClusterUsageSample] = Field(default_factory=list)


class InventoryResourceCountForbidden(StrictModel):
    namespace: str | None = None
    api_group: str = ""
    version: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    namespaced: bool
    reason_code: Literal["list_permission_not_observed"] = "list_permission_not_observed"


class InventoryResourceCountsEvidence(StrictModel):
    completeness: Literal["observed", "partial", "unavailable"]
    observed_at: str | None = None
    namespace_scope: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    forbidden: tuple[InventoryResourceCountForbidden, ...] = ()

    @field_validator("namespace_scope")
    @classmethod
    def canonicalize_namespace_scope(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)) or tuple(sorted(values)) != values:
            raise ValueError("inventory count namespace scope must be sorted and unique")
        return values

    @model_validator(mode="after")
    def validate_evidence_state(self) -> Self:
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("inventory count reason codes must be unique")
        if self.completeness == "observed":
            if self.observed_at is None or self.reason_codes:
                raise ValueError("observed inventory counts require timestamp and no reasons")
        elif not self.reason_codes:
            raise ValueError("incomplete inventory counts require reasons")
        if self.completeness == "unavailable" and (self.observed_at is not None or self.forbidden):
            raise ValueError("unavailable inventory counts cannot carry observed evidence")
        return self


class InventoryResourceCount(StrictModel):
    resource_type: str = Field(min_length=1, max_length=120)
    health: str = Field(min_length=1, max_length=80)
    count: int = Field(ge=0)


class InventoryNamespaceSummary(StrictModel):
    namespace: str = Field(min_length=1, max_length=253)
    total: int = Field(ge=0)
    counts: list[InventoryResourceCount] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        if self.total != sum(item.count for item in self.counts):
            raise ValueError("inventory namespace total must equal its resource counts")
        return self


class InventorySummaryResponse(StrictModel):
    cluster_id: str
    latest_snapshot: JsonMap | None = None
    counts: list[JsonMap] = Field(default_factory=list)
    namespaces: list[InventoryNamespaceSummary] = Field(default_factory=list)
    counts_evidence: InventoryResourceCountsEvidence


class KubernetesApiResourcesResponse(StrictModel):
    cluster_id: str
    snapshot_id: str | None = None
    discovery: ApiResourceDiscoveryObservation | None = None
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if self.discovery is None and not self.unavailable_reason:
            raise ValueError("unavailable API resources require a reason")
        if self.discovery is not None and self.unavailable_reason is not None:
            raise ValueError("available API resources cannot include an unavailable reason")
        return self


class FleetClusterSummaryItem(StrictModel):
    """fleet 화면 클러스터 1개 롤업 — health 는 healthy|warning|critical|stale|unknown."""

    cluster_id: str
    name: str
    health: str
    pods_running: int = 0
    pods_total: int = 0
    nodes_ready: int = 0
    nodes_total: int = 0
    open_incidents: int = 0
    restarts_recent: int = 0
    # 실측 활용률(%) — usage 롤업 우선, 최신 inventory node 실측 대체; 둘 다 없으면 None.
    cpu_pct: float | None = None
    mem_pct: float | None = None
    last_seen_at: str | None = None


class FleetTotals(StrictModel):
    """Fleet summary totals visible within the caller's authorization scope.

    ``dead_letters`` is platform-global rather than workspace-scoped, so it is
    observed only for service administrators. ``None`` means the value is not
    authorized/observed; it must never be rendered as a synthetic zero.
    """

    clusters: int = 0
    healthy: int = 0
    warning: int = 0
    critical: int = 0
    stale: int = 0
    unknown: int = 0
    open_incidents: int = 0
    pending_approvals: int = 0
    running_workflows: int = 0
    dead_letters: int | None = None


class FleetSummaryResponse(StrictModel):
    clusters: list[FleetClusterSummaryItem] = Field(default_factory=list)
    totals: FleetTotals = Field(default_factory=FleetTotals)


class FleetSummaryStreamFrame(StrictModel):
    """Latest-state fleet projection sent over one authenticated workspace SSE."""

    cursor: str = Field(min_length=1, max_length=8192)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    refresh_after_ms: int = Field(ge=5_000, le=10_000)
    summary: FleetSummaryResponse


class ClusterWorkloadHealthItem(StrictModel):
    """드릴다운 워크로드 1개 — ready 는 "ready/desired" 문자열(inventory status)."""

    name: str
    kind: str
    namespace: str | None = None
    health: str
    ready: str = ""
    restarts: int = 0


class ClusterWarningEventItem(StrictModel):
    namespace: str | None = None
    name: str
    reason: str | None = None
    message: str | None = None
    involved_kind: str | None = None
    involved_name: str | None = None
    count: int = 0
    last_seen_at: str | None = None


class ClusterOpenIncidentItem(StrictModel):
    incident_id: str
    correlation_id: str
    symptom: str | None = None
    root_cause: str | None = None
    namespace: str | None = None
    resource_kind: str | None = None
    resource_name: str | None = None
    status: str
    created_at: str | None = None


class ClusterUsageSnapshot(StrictModel):
    """최신 usage 롤업 1건 — agent 가 관측한 값만(없으면 None/0)."""

    sampled_at: str | None = None
    pods_running: int = 0
    pods_total: int = 0
    nodes_ready: int = 0
    nodes_total: int = 0
    restart_total: int = 0
    cpu_pct: float | None = None
    mem_pct: float | None = None


class ClusterSummaryDetailResponse(StrictModel):
    cluster_id: str
    name: str
    health: str
    # health 값("healthy"/"degraded"/"unknown") → 워크로드 목록 그룹.
    workloads: dict[str, list[ClusterWorkloadHealthItem]] = Field(default_factory=dict)
    warning_events: list[ClusterWarningEventItem] = Field(default_factory=list)
    open_incidents: list[ClusterOpenIncidentItem] = Field(default_factory=list)
    usage: ClusterUsageSnapshot | None = None


HomeInsightAvailability = Literal["available", "partial", "unavailable"]


class HomeInsightCoverage(StrictModel):
    availability: HomeInsightAvailability
    observed_at: str | None = None
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def incomplete_coverage_has_a_reason(self) -> Self:
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("incomplete Home insight coverage requires a reason")
        return self


class HomeCustomResourceCount(StrictModel):
    api_group: str = Field(min_length=1)
    version: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    count: int = Field(ge=1)


class HomeCustomResourceSummary(StrictModel):
    coverage: HomeInsightCoverage
    items: tuple[HomeCustomResourceCount, ...] = Field(default=(), max_length=20)
    total_kinds: int | None = Field(default=None, ge=0)
    total_resources: int | None = Field(default=None, ge=0)
    has_more: bool = False

    @model_validator(mode="after")
    def counts_match_coverage(self) -> Self:
        unavailable = self.coverage.availability == "unavailable"
        if unavailable and (
            self.items or self.total_kinds is not None or self.total_resources is not None
        ):
            raise ValueError("unavailable custom resource coverage cannot expose counts")
        if not unavailable and (self.total_kinds is None or self.total_resources is None):
            raise ValueError("observed custom resource coverage requires totals")
        if self.total_kinds is not None and self.total_kinds < len(self.items):
            raise ValueError("custom resource total kinds cannot be smaller than items")
        if self.total_resources is not None and self.total_resources < sum(
            item.count for item in self.items
        ):
            raise ValueError("custom resource total cannot be smaller than visible counts")
        if self.has_more != (self.total_kinds is not None and self.total_kinds > len(self.items)):
            raise ValueError("custom resource has_more must match the bounded result")
        return self


class HomeHelmSummary(StrictModel):
    coverage: HomeInsightCoverage
    release_count: int | None = Field(default=None, ge=0)
    status_counts: dict[str, int] = Field(default_factory=dict, max_length=20)

    @model_validator(mode="after")
    def release_counts_match_coverage(self) -> Self:
        if self.coverage.availability == "unavailable":
            if self.release_count is not None or self.status_counts:
                raise ValueError("unavailable Helm coverage cannot expose release counts")
            return self
        if self.release_count is None:
            raise ValueError("observed Helm coverage requires a release count")
        if any(
            not status.strip() or len(status) > 120 or count < 1
            for status, count in self.status_counts.items()
        ):
            raise ValueError("Helm status counts must be positive and named")
        if sum(self.status_counts.values()) > self.release_count:
            raise ValueError("Helm status counts cannot exceed the release count")
        return self


HomeCertificateExpiryStatus = Literal["valid", "expiring", "expired"]


class HomeCertificateExpiryItem(StrictModel):
    secret: ResourceRef
    source_certificate: ResourceRef
    not_after: str = Field(min_length=1)
    status: HomeCertificateExpiryStatus
    seconds_remaining: int
    observed_at: str | None = None


class HomeCertificateExpirySummary(StrictModel):
    coverage: HomeInsightCoverage
    items: tuple[HomeCertificateExpiryItem, ...] = Field(default=(), max_length=20)
    tls_secret_count: int | None = Field(default=None, ge=0)
    observed_expiry_count: int | None = Field(default=None, ge=0)
    expiring_count: int | None = Field(default=None, ge=0)
    expired_count: int | None = Field(default=None, ge=0)
    earliest_expiry: str | None = None
    warning_before_seconds: int = Field(ge=1, le=315_360_000)
    has_more: bool = False

    @model_validator(mode="after")
    def counts_match_coverage(self) -> Self:
        counts = (
            self.tls_secret_count,
            self.observed_expiry_count,
            self.expiring_count,
            self.expired_count,
        )
        unavailable = self.coverage.availability == "unavailable"
        if unavailable:
            if any(value is not None for value in counts) or self.items or self.earliest_expiry:
                raise ValueError("unavailable certificate coverage cannot expose observations")
            if self.has_more:
                raise ValueError("unavailable certificate coverage cannot be truncated")
            return self
        if any(value is None for value in counts):
            raise ValueError("observed certificate coverage requires counts")
        assert self.tls_secret_count is not None
        assert self.observed_expiry_count is not None
        assert self.expiring_count is not None
        assert self.expired_count is not None
        if self.observed_expiry_count > self.tls_secret_count:
            raise ValueError("observed certificate expiries cannot exceed TLS Secrets")
        if self.expiring_count + self.expired_count > self.observed_expiry_count:
            raise ValueError("certificate health counts cannot exceed observations")
        if self.observed_expiry_count == 0 or self.earliest_expiry is None:
            raise ValueError("observed certificate coverage requires an earliest expiry")
        if self.has_more != self.observed_expiry_count > len(self.items):
            raise ValueError("certificate has_more must match the bounded result")
        if len({item.secret.uid for item in self.items}) != len(self.items):
            raise ValueError("certificate summary Secret identities must be unique")
        return self


HomeRelationCompleteness = Literal["exact", "partial", "unavailable"]


class HomeTopologyPreviewSummary(StrictModel):
    coverage: HomeInsightCoverage
    node_count: int | None = Field(default=None, ge=0)
    edge_count: int | None = Field(default=None, ge=0)
    omitted_node_count: int | None = Field(default=None, ge=0)
    omitted_edge_count: int | None = Field(default=None, ge=0)
    relation_completeness: HomeRelationCompleteness

    @model_validator(mode="after")
    def counts_match_coverage(self) -> Self:
        counts = (
            self.node_count,
            self.edge_count,
            self.omitted_node_count,
            self.omitted_edge_count,
        )
        unavailable = self.coverage.availability == "unavailable"
        if unavailable and (
            any(value is not None for value in counts)
            or self.relation_completeness != "unavailable"
        ):
            raise ValueError("unavailable Home topology cannot expose graph counts")
        if not unavailable and (
            any(value is None for value in counts) or self.relation_completeness == "unavailable"
        ):
            raise ValueError("observed Home topology requires graph counts")
        if self.coverage.availability == "available" and self.relation_completeness != "exact":
            raise ValueError("available Home topology requires exact relation evidence")
        if self.coverage.availability == "partial" and self.relation_completeness != "partial":
            raise ValueError("partial Home topology requires partial relation evidence")
        return self


class HomeProviderAvailabilitySummary(StrictModel):
    coverage: HomeInsightCoverage


class HomeExploreSummary(StrictModel):
    traffic: HomeProviderAvailabilitySummary
    cost: HomeProviderAvailabilitySummary


class HomeNetworkPolicyCoverageSummary(StrictModel):
    coverage: HomeInsightCoverage
    total_policies: int | None = Field(default=None, ge=0)
    covered_workloads: int | None = Field(default=None, ge=0)
    total_workloads: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def counts_match_coverage(self) -> Self:
        counts = (self.total_policies, self.covered_workloads, self.total_workloads)
        unavailable = self.coverage.availability == "unavailable"
        if unavailable and any(value is not None for value in counts):
            raise ValueError("unavailable NetworkPolicy coverage cannot expose counts")
        if not unavailable and any(value is None for value in counts):
            raise ValueError("observed NetworkPolicy coverage requires counts")
        if (
            self.covered_workloads is not None
            and self.total_workloads is not None
            and self.covered_workloads > self.total_workloads
        ):
            raise ValueError("covered workloads cannot exceed total workloads")
        return self


class HomeGitOpsControllerSummary(StrictModel):
    coverage: HomeInsightCoverage
    controller_count: int | None = Field(default=None, ge=0)
    provider_counts: dict[str, int] = Field(default_factory=dict, max_length=20)
    health_counts: dict[str, int] = Field(default_factory=dict, max_length=40)

    @model_validator(mode="after")
    def counts_match_coverage(self) -> Self:
        unavailable = self.coverage.availability == "unavailable"
        if unavailable and (
            self.controller_count is not None or self.provider_counts or self.health_counts
        ):
            raise ValueError("unavailable GitOps coverage cannot expose controller counts")
        if not unavailable and self.controller_count is None:
            raise ValueError("observed GitOps coverage requires a controller count")
        for counts in (self.provider_counts, self.health_counts):
            if any(not key.strip() or count < 1 for key, count in counts.items()):
                raise ValueError("GitOps controller counts must be positive and named")
            if self.controller_count is not None and sum(counts.values()) > self.controller_count:
                raise ValueError("GitOps grouped counts cannot exceed controller count")
        return self


class HomeAuditFindingSummary(StrictModel):
    coverage: HomeInsightCoverage
    total_check_count: int | None = Field(default=None, ge=0)
    total_finding_count: int | None = Field(default=None, ge=0)
    severity_counts: dict[Literal["warning", "danger"], int] = Field(
        default_factory=dict,
        max_length=2,
    )

    @model_validator(mode="after")
    def counts_match_coverage(self) -> Self:
        unavailable = self.coverage.availability == "unavailable"
        if unavailable and (
            self.total_check_count is not None
            or self.total_finding_count is not None
            or self.severity_counts
        ):
            raise ValueError("unavailable audit coverage cannot expose finding counts")
        if not unavailable and (self.total_check_count is None or self.total_finding_count is None):
            raise ValueError("observed audit coverage requires counts")
        if any(count < 1 for count in self.severity_counts.values()):
            raise ValueError("audit severity counts must be positive")
        if (
            self.total_finding_count is not None
            and sum(self.severity_counts.values()) != self.total_finding_count
        ):
            raise ValueError("audit severity counts must match total findings")
        return self


class HomePostureSummary(StrictModel):
    network_policy: HomeNetworkPolicyCoverageSummary
    gitops: HomeGitOpsControllerSummary
    audit: HomeAuditFindingSummary


class HomeInsightsResponse(StrictModel):
    cluster_id: str = Field(min_length=1)
    topology: HomeTopologyPreviewSummary
    explore: HomeExploreSummary
    posture: HomePostureSummary
    custom_resources: HomeCustomResourceSummary
    helm: HomeHelmSummary
    certificate_expiry: HomeCertificateExpirySummary
    refresh_after_seconds: int = Field(ge=1, le=3600)


class NodeSummaryItem(StrictModel):
    name: str
    ready: bool
    health: str
    kubernetes_version: str | None = None
    pods_running: int = 0
    pods_capacity: int = 0
    cpu_pct: float | None = None
    mem_pct: float | None = None
    # 마지막 실측 시각과 신선도 — freshness 창(metrics_kubernetes.stale_after)을 넘긴
    # '실측'은 버리지 않고 마지막 값 + stale=true 로 정직하게 노출한다(합성 금지).
    # metrics-server 원천 타임스탬프 granularity(15~60s)가 창(20s)보다 클 수 있어,
    # null 로 지우면 실제 관측이 있는데도 '관측 안 됨'으로 오표시된다.
    metrics_observed_at: str | None = None
    metrics_stale: bool = False
    restarts_recent: int = 0
    conditions: list[str] = Field(default_factory=list)


class ClusterNodesSummaryResponse(StrictModel):
    cluster_id: str
    nodes: list[NodeSummaryItem] = Field(default_factory=list)


class PodSummaryItem(StrictModel):
    name: str
    namespace: str
    phase: str
    health: str
    ready: str = "0/0"
    restarts: int = 0
    owner_kind: str | None = None
    owner_name: str | None = None
    cpu_mcores: float | None = None
    mem_mib: float | None = None
    incident_correlation_id: str | None = None


class NodePodsSummaryResponse(StrictModel):
    cluster_id: str
    node_name: str
    pods: list[PodSummaryItem] = Field(default_factory=list)


class BootstrapStep(StrictModel):
    label: str
    command: str


class ManagementAccessResponse(StrictModel):
    mode: Literal["portforward", "loadbalancer", "ingress", "nodeport", "unknown"] = "unknown"
    external_url: str | None = None
    agent_server_url: str = ""
    reachability: Literal["external", "self_only"] = "self_only"
    limitation_reason: Literal["external_url_not_configured"] | None = None


class TargetInstallResponse(StrictModel):
    registered: bool
    cluster_id: str
    status: str
    applied: bool
    apply_output: str | None
    install_manifest: str
    # 이 클러스터의 per-cluster agent 토큰(원문). 등록 관리자에게 1회 반환 —
    # agent 배포 secret 주입 및 agent 인증(x-agent-token)에 사용. 서버는 해시만 저장.
    agent_token: str
    # 원라인 설치 명령 — curl <base>/install/<token> | kubectl apply -f -
    install_command: str = ""
    # provider별 설치 명령. 새 UI는 이 값을 우선 사용하고 없으면 install_command로 fallback.
    bootstrap_command: str = ""
    powershell_install_command: str = ""
    powershell_bootstrap_command: str = ""
    bootstrap_steps: list[BootstrapStep] = Field(default_factory=list)
    connect_timeout_seconds: int | None = None
    connect_expires_at: str | None = None
    connection_stage: str | None = None
    management_access: ManagementAccessResponse | None = None


class ClusterAgentStatus(StrictModel):
    workspace_id: str
    cluster_id: str
    agent_id: str
    status: str
    capabilities: list[str] = Field(default_factory=list)
    details: JsonMap = Field(default_factory=dict)
    last_seen_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ClusterSummary(StrictModel):
    workspace_id: str
    cluster_id: str
    name: str
    environment: str
    status: str
    settings: JsonMap = Field(default_factory=dict)
    connection_status: str
    observation_mode: Literal["agent", "simulation"] = "agent"
    provider: str | None = None
    connection_stage: str | None = None
    last_agent_id: str | None = None
    last_agent_seen_at: str | None = None
    node_count: int | None = Field(default=None, ge=0)
    pod_count: int | None = Field(default=None, ge=0)
    namespace_count: int | None = Field(default=None, ge=0)
    kubernetes_version: str | None = None
    crd_discovery_status: Literal["exact", "partial", "unavailable"] | None = None
    incident_count: int | None = Field(default=None, ge=0)
    # VP-015 / BQ-069 product fields. These stay nullable until the backing
    # inventory or incident source proves a value; unknown is never reported
    # as zero.
    server_count: int | None = Field(default=None, ge=0)
    app_count: int | None = Field(default=None, ge=0)
    open_incidents: int | None = Field(default=None, ge=0)
    last_seen_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ClusterListResponse(StrictModel):
    clusters: list[ClusterSummary]


class ClusterResponse(StrictModel):
    cluster: ClusterSummary
    agents: list[ClusterAgentStatus] = Field(default_factory=list)


class TelemetryStackComponent(StrictModel):
    """관측 스택 구성요소 하나의 실측 상태(present/ready)."""

    key: str
    label: str
    present: bool = False
    ready: bool = False


class TelemetryStackView(StrictModel):
    """관측 스택(minio·prometheus·loki·tempo·otel) 준비도 — 실측 파드 health 기반.

    ready_count/total 로 "합류 진행"을 정직하게 표기한다(타이머로 채우지 않음).
    스택 설치가 시작되지 않았으면 서버는 이 필드를 None 으로 둔다(진행바 미표시).
    """

    ready_count: int = Field(ge=0)
    total: int = Field(ge=1)
    complete: bool = False
    components: list[TelemetryStackComponent] = Field(default_factory=list)


class ClusterConnectionStatusResponse(StrictModel):
    cluster_id: str
    connection_status: str
    # 관측 스택 합류 진행(선택) — 에이전트-우선 연결 후 후속 세팅 상태를 실측으로 노출.
    telemetry_stack: TelemetryStackView | None = None
    connection_stage: (
        Literal[
            "awaiting_install",
            "agent_connected",
            "snapshot_received",
            "ready",
            "expired",
            "error",
        ]
        | None
    ) = None
    refresh_after_seconds: float | None = Field(default=None, ge=0.25, le=30)
    last_agent_id: str | None = None
    last_seen_at: str | None = None
    agents: list[ClusterAgentStatus] = Field(default_factory=list)
    connect_timeout_seconds: int | None = None
    connect_expires_at: str | None = None

    @model_validator(mode="after")
    def validate_polling_semantics(self) -> Self:
        terminal = self.connection_stage in {"ready", "expired", "error"}
        if terminal and self.refresh_after_seconds is not None:
            raise ValueError("terminal cluster connection stages cannot request polling")
        if (
            self.connection_stage is not None
            and not terminal
            and self.refresh_after_seconds is None
        ):
            raise ValueError("non-terminal cluster connection stages require server polling")
        return self


class RepositoryConnectionStatusResponse(StrictModel):
    repo_ref: str = Field(min_length=1, max_length=240)
    repository_id: str | None = Field(default=None, min_length=1, max_length=160)
    repository_status: Literal[
        "unregistered",
        "active",
        "invalid_credential",
        "disabled",
        "source_unreachable",
        "disconnected",
        "unknown",
    ]
    connection_stage: Literal["awaiting_validation", "ready", "error"]
    terminal: bool
    refresh_after_seconds: float | None = Field(default=None, ge=0.25, le=30)
    # 비정상/해제 사유를 UI 에 그대로 노출하기 위한 부가 정보(선택). 상태 판정에는
    # 영향을 주지 않으며, 없으면 종전 응답과 동일하다(하위호환).
    degraded_reason: (
        Literal[
            "credential_invalid",
            "source_unreachable",
            "permission_revoked",
            "disconnected",
            "disabled",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_registration_semantics(self) -> Self:
        expected = {
            "unregistered": ("awaiting_validation", False, False),
            "active": ("ready", True, True),
            "invalid_credential": ("error", True, True),
            "disabled": ("error", True, True),
            "source_unreachable": ("error", True, True),
            "disconnected": ("error", True, True),
            "unknown": ("error", True, True),
        }[self.repository_status]
        actual = (
            self.connection_stage,
            self.terminal,
            self.refresh_after_seconds is None,
        )
        if actual != expected:
            raise ValueError("repository connection stage must match persisted status")
        if (self.repository_status == "unregistered") != (self.repository_id is None):
            raise ValueError("only an unregistered repository can omit repository_id")
        return self


class RepositoryListItem(StrictModel):
    repo_ref: str = Field(min_length=1, max_length=240)
    repository_id: str = Field(min_length=1, max_length=160)
    provider: str = ""
    default_branch: str = ""
    repository_status: Literal[
        "active",
        "invalid_credential",
        "disabled",
        "source_unreachable",
        "disconnected",
        "unknown",
    ]
    degraded_reason: (
        Literal[
            "credential_invalid",
            "source_unreachable",
            "permission_revoked",
            "disconnected",
            "disabled",
        ]
        | None
    ) = None
    application_count: int = Field(default=0, ge=0)
    updated_at: str | None = None


class RepositoryListResponse(StrictModel):
    repositories: list[RepositoryListItem] = Field(default_factory=list)


class ClusterConnectResponse(StrictModel):
    cluster_id: str
    install_command: str = Field(min_length=1)
    powershell_install_command: str = Field(min_length=1)
    expires_at: str


class ClusterConnectStatusResponse(StrictModel):
    status: Literal["waiting", "connected", "expired", "failed"]
    stage: str | None = None
    agent_version: str | None = None
    connected_at: str | None = None
    failure_reason: str | None = None


class ClusterUnregisterResponse(StrictModel):
    cluster_id: str
    status: Literal["uninstalling", "cleanup_required", "disconnected", "purged"]
    stage: Literal[
        "agent_cleanup_queued",
        "agent_cleanup_pending",
        "registration_revoked",
        "purged",
    ]
    command_id: str | None = None
    command_status_path: str | None = None
    uninstall_command: str | None = None
    cleanup_verified: bool = False
    resources: list[str] = Field(default_factory=list)
    residual_resources: list[str] = Field(default_factory=list)
    failure_reason: str | None = None


class AlertChannelResponse(StrictModel):
    channel_id: str
    workspace_id: str
    name: str
    kind: str
    url: str
    min_severity: str
    enabled: bool
    last_tested_at: str | None = None
    last_test_status: str | None = None
    last_test_detail: str | None = None
    last_test_status_code: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


class AlertChannelListResponse(StrictModel):
    channels: list[AlertChannelResponse] = Field(default_factory=list)


class ValidationErrorItem(StrictModel):
    code: str
    detail: str
    line: int | None = None


class AlertChannelTestResponse(StrictModel):
    valid: bool
    delivered: bool = False
    code: str | None = None
    detail: str = ""
    status_code: int | None = None
    channel: AlertChannelResponse | None = None


class RcaRuleValidateResponse(StrictModel):
    valid: bool
    errors: list[ValidationErrorItem] = Field(default_factory=list)
    matched_symptom: str | None = None
    candidates_count: int = 0


class RcaRuleCandidateItem(StrictModel):
    candidate_id: str
    title: str
    expected_evidence: list[str] = Field(default_factory=list)
    signals_count: int = 0


class RcaRuleCatalogItem(StrictModel):
    rule_id: str
    symptoms: list[str] = Field(default_factory=list)
    required_sources: list[str] = Field(default_factory=list)
    candidates: list[RcaRuleCandidateItem] = Field(default_factory=list)


class RcaRuleCatalogResponse(StrictModel):
    items: list[RcaRuleCatalogItem] = Field(default_factory=list)
    rules_count: int = 0
    candidates_count: int = 0


class DeadLettersResponse(StrictModel):
    dead_letters: list[JsonMap]


class DeadLetterReplayResponse(StrictModel):
    accepted: bool
    dead_letter_id: int
    replay_event: JsonMap


class AiConversationAcceptedResponse(StrictModel):
    accepted: bool
    conversation_id: str
    message_id: str
    event_id: str
    correlation_id: str


class AiConversationResponse(StrictModel):
    conversation: JsonMap
    messages: list[JsonMap]
    limit: int = Field(ge=1, le=MAX_CONVERSATION_MESSAGE_LIMIT)
    has_more: bool
    next_cursor: str | None = None
    messages_completeness: Literal["complete", "partial"]
    partial_reason_codes: list[str]

    @model_validator(mode="after")
    def validate_message_page(self) -> Self:
        if self.has_more:
            if (
                self.next_cursor is None
                or self.messages_completeness != "partial"
                or BOUNDED_MESSAGE_HISTORY_REASON not in self.partial_reason_codes
            ):
                raise ValueError("partial message page requires cursor and bounded history reason")
        elif (
            self.next_cursor is not None
            or self.messages_completeness != "complete"
            or self.partial_reason_codes
        ):
            raise ValueError("complete message page cannot carry partial pagination state")
        return self


class AiConversationListResponse(StrictModel):
    conversations: list[JsonMap]


AI_NO_DATA_ANSWER = "그 데이터가 없습니다."


class AiEvidenceLink(StrictModel):
    type: Literal["inventory-resource", "log-stream"]
    id: str = Field(min_length=1, max_length=255)
    label: str = Field(min_length=1, max_length=512)
    link: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def validate_internal_link(self) -> Self:
        # Product-internal absolute paths only. Protocol-relative links, URL
        # schemes, fragments and control characters must never reach an anchor.
        if (
            not self.link.startswith("/")
            or self.link.startswith("//")
            or "#" in self.link
            or any(character.isspace() or ord(character) < 32 for character in self.link)
        ):
            raise ValueError("AI evidence link must be a safe product-internal path")
        return self


class AiChatAction(StrictModel):
    """Human-confirmed action proposal; this response never executes it."""

    type: Literal["create_alert_rule"]
    payload: JsonMap
    rationale: str = Field(min_length=1, max_length=1000)


class AiChatResponse(StrictModel):
    answer: str = Field(min_length=1, max_length=4000)
    evidence: list[AiEvidenceLink] = Field(default_factory=list, max_length=20)
    action: AiChatAction | None = None
    # Capability/identity questions are answered by the configured model from
    # the product capability contract, not from cluster evidence. Keeping this
    # explicit prevents an arbitrary evidence-free operational claim from
    # passing the response boundary.
    # "clarification" marks a follow-up question for the allowlisted alert
    # action (e.g. missing metric/threshold) so the client can carry the
    # pending request across turns against this stateless endpoint.
    answer_kind: Literal["capability", "clarification"] | None = None

    @model_validator(mode="after")
    def require_evidence_or_canonical_no_data(self) -> Self:
        if self.answer_kind == "capability" and (self.evidence or self.action is not None):
            raise ValueError("AI capability answers cannot carry operational evidence or actions")
        if self.answer_kind == "clarification" and self.action is not None:
            raise ValueError("AI clarification answers cannot carry actions")
        if (
            not self.evidence
            and self.action is None
            and self.answer_kind is None
            and self.answer != AI_NO_DATA_ANSWER
        ):
            raise ValueError("AI answer without evidence must use the canonical no-data answer")
        identities = [(item.type, item.id) for item in self.evidence]
        if len(set(identities)) != len(identities):
            raise ValueError("AI evidence entries must be unique")
        return self


class AiSuggestion(StrictModel):
    id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=240)
    prompt: str = Field(min_length=1, max_length=1000)


class AiSuggestionsResponse(StrictModel):
    suggestions: list[AiSuggestion] = Field(default_factory=list, max_length=12)


class AiResourceSummary(StrictModel):
    """Token-bounded inventory evidence; raw Kubernetes metadata is excluded."""

    id: str = Field(min_length=1, max_length=255)
    cluster_id: str = Field(min_length=1, max_length=512)
    resource_type: str = Field(min_length=1, max_length=80)
    kind: str = Field(min_length=1, max_length=120)
    namespace: str | None = Field(default=None, max_length=253)
    name: str = Field(min_length=1, max_length=253)
    status: str = Field(min_length=1, max_length=80)
    health: str = Field(min_length=1, max_length=80)
    observed_at: str | None = None
    link: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def validate_internal_link(self) -> Self:
        AiEvidenceLink(
            type="inventory-resource",
            id=self.id,
            label=self.name,
            link=self.link,
        )
        return self


ApplicationProjectionCompleteness = Literal["exact", "partial", "unavailable"]
ApplicationProjectionAvailability = Literal["available", "unavailable"]
ApplicationHealthStatus = Literal["healthy", "degraded", "unknown"]
ApplicationDeploymentStatus = Literal["succeeded", "failed", "running", "pending", "unknown"]
ApplicationBatchRuntimeStatus = Literal[
    "running",
    "failed",
    "succeeded",
    "suspended",
    "unknown",
]
ApplicationTopologyEdgeType = Literal["owns", "runs_on", "selects", "routes_to"]
ApplicationTopologyAuthority = Literal["authoritative", "derived"]
ApplicationHistoryEntryType = Literal["delivery", "incident"]
ApplicationSourceConflict = Literal["aligned", "conflict", "unknown"]
ApplicationDriftStatus = Literal["in_sync", "drifted", "unknown"]
ApplicationActivityType = Literal["deployment", "incident", "change"]
ApplicationDriftScalar = str | int | float | bool | None


class ApplicationHealthSummary(StrictModel):
    status: ApplicationHealthStatus
    ready_pods: int | None = Field(default=None, ge=0)
    total_pods: int | None = Field(default=None, ge=0)
    restarts: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_pod_counts(self) -> Self:
        if (
            self.ready_pods is not None
            and self.total_pods is not None
            and self.ready_pods > self.total_pods
        ):
            raise ValueError("ready pod count cannot exceed total pod count")
        return self


class ApplicationRuntimeReadiness(StrictModel):
    completeness: ApplicationProjectionCompleteness
    status: ApplicationHealthStatus
    ready_pods: int | None = Field(default=None, ge=0)
    total_pods: int | None = Field(default=None, ge=0)
    restarts: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_runtime_readiness(self) -> Self:
        if (
            self.ready_pods is not None
            and self.total_pods is not None
            and self.ready_pods > self.total_pods
        ):
            raise ValueError("ready pod count cannot exceed total pod count")
        if self.completeness == "unavailable" and (
            self.status != "unknown"
            or self.ready_pods is not None
            or self.total_pods is not None
            or self.restarts is not None
        ):
            raise ValueError("unavailable runtime readiness must not claim runtime evidence")
        return self


class ApplicationDeliveryState(StrictModel):
    availability: ApplicationProjectionAvailability
    status: ApplicationDeploymentStatus | None = None
    workflow_run_id: str | None = None
    observed_at: str | None = None

    @model_validator(mode="after")
    def validate_delivery_state(self) -> Self:
        if self.availability == "unavailable" and any(
            value is not None for value in (self.status, self.workflow_run_id, self.observed_at)
        ):
            raise ValueError("unavailable delivery state must not claim delivery evidence")
        if self.availability == "available" and (
            self.status is None or self.workflow_run_id is None
        ):
            raise ValueError("available delivery state requires an observed workflow run")
        return self


class ApplicationBatchRuntime(StrictModel):
    availability: ApplicationProjectionAvailability
    completeness: ApplicationProjectionCompleteness
    status: ApplicationBatchRuntimeStatus | None = None
    active_runs: int | None = Field(default=None, ge=0)
    failed_runs: int | None = Field(default=None, ge=0)
    succeeded_runs: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_batch_runtime(self) -> Self:
        counters = (self.active_runs, self.failed_runs, self.succeeded_runs)
        if self.availability == "unavailable" and (
            self.completeness != "unavailable"
            or self.status is not None
            or any(value is not None for value in counters)
        ):
            raise ValueError("unavailable batch runtime must not claim batch evidence")
        if self.availability == "available" and (
            self.completeness == "unavailable" or self.status is None
        ):
            raise ValueError("available batch runtime requires an observed batch state")
        return self


class ApplicationCurrentDeployment(StrictModel):
    version: str | None = None
    image: str | None = None
    image_digest: str | None = None
    git_sha: str | None = None
    deployed_at: str | None = None
    deployed_by: str | None = None


class ApplicationResourceKindCount(StrictModel):
    kind: str = Field(min_length=1)
    count: int = Field(ge=0)


class ApplicationProductCard(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    environments: list[str] = Field(default_factory=list)
    lifecycle_status: str = Field(min_length=1)
    repository_ref: str | None = None
    default_branch: str | None = None
    manifest_path: str | None = None
    health: ApplicationHealthSummary
    runtime_readiness: ApplicationRuntimeReadiness
    current_deployment: ApplicationCurrentDeployment | None = None
    delivery: ApplicationDeliveryState
    batch_runtime: ApplicationBatchRuntime
    has_drift: bool | None = None
    drift_summary: str | None = None
    resource_counts: list[ApplicationResourceKindCount] | None = None
    resource_counts_completeness: ApplicationProjectionCompleteness
    open_incidents: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_card_semantics(self) -> Self:
        if self.has_drift is not True and self.drift_summary is not None:
            raise ValueError("drift summary requires confirmed drift")
        if self.has_drift is True and not self.drift_summary:
            raise ValueError("confirmed drift requires a summary")
        if self.resource_counts_completeness == "unavailable" and self.resource_counts is not None:
            raise ValueError("unavailable resource counts must be null")
        if self.resource_counts_completeness != "unavailable" and self.resource_counts is None:
            raise ValueError("available resource counts must be an array")
        if self.resource_counts is not None:
            kinds = [item.kind for item in self.resource_counts]
            if kinds != sorted(set(kinds)):
                raise ValueError("resource count kinds must be unique and sorted")
        return self


class ApplicationEndpointSummary(StrictModel):
    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)


class ApplicationRecentIncident(StrictModel):
    id: str = Field(min_length=1)
    title: str | None = None
    status: str = Field(min_length=1)
    started_at: str | None = None


class ApplicationRecentActivity(StrictModel):
    id: str = Field(min_length=1)
    type: ApplicationActivityType
    summary: str | None = None
    occurred_at: str | None = None


class ApplicationTopologyNode(StrictModel):
    id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    namespace: str | None = None
    name: str = Field(min_length=1)
    status: str = Field(min_length=1)
    health: str = Field(min_length=1)
    observed_at: str | None = None


class ApplicationTopologyEdge(StrictModel):
    id: str = Field(min_length=1)
    from_id: str = Field(min_length=1)
    to_id: str = Field(min_length=1)
    type: ApplicationTopologyEdgeType
    evidence_type: str = Field(min_length=1)
    authority: ApplicationTopologyAuthority
    observed_at: str | None = None


class ApplicationTopology(StrictModel):
    availability: ApplicationProjectionAvailability
    completeness: ApplicationProjectionCompleteness
    observed_at: str | None = None
    nodes: list[ApplicationTopologyNode] | None = Field(default=None, max_length=200)
    edges: list[ApplicationTopologyEdge] | None = Field(default=None, max_length=1000)
    partial_reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_topology(self) -> Self:
        if self.availability == "unavailable" and (
            self.completeness != "unavailable"
            or self.observed_at is not None
            or self.nodes is not None
            or self.edges is not None
            or self.partial_reason_codes
        ):
            raise ValueError("unavailable topology must not claim topology evidence")
        if self.availability == "available" and (
            self.completeness == "unavailable" or self.nodes is None or self.edges is None
        ):
            raise ValueError("available topology requires node and edge collections")
        if self.completeness == "exact" and self.partial_reason_codes:
            raise ValueError("exact topology cannot carry partial reasons")
        if self.completeness == "partial" and not self.partial_reason_codes:
            raise ValueError("partial topology requires source reasons")
        nodes = self.nodes or []
        node_ids = {node.id for node in nodes}
        if len(node_ids) != len(nodes):
            raise ValueError("topology node identities must be unique")
        edges = self.edges or []
        if len({edge.id for edge in edges}) != len(edges):
            raise ValueError("topology edge identities must be unique")
        if any(edge.from_id not in node_ids or edge.to_id not in node_ids for edge in edges):
            raise ValueError("topology edges must reference returned nodes")
        return self


class ApplicationHistoryEntry(StrictModel):
    id: str = Field(min_length=1)
    type: ApplicationHistoryEntryType
    status: str = Field(min_length=1)
    summary: str | None = None
    occurred_at: str | None = None
    workflow_run_id: str | None = None
    gitops_change_id: str | None = None

    @model_validator(mode="after")
    def validate_history_reference(self) -> Self:
        if self.type == "delivery" and self.workflow_run_id is None:
            raise ValueError("delivery history requires a workflow run anchor")
        if self.type == "incident" and (
            self.workflow_run_id is not None or self.gitops_change_id is not None
        ):
            raise ValueError("incident history cannot claim deployment anchors")
        return self


class ApplicationHistory(StrictModel):
    availability: ApplicationProjectionAvailability
    completeness: ApplicationProjectionCompleteness
    entries: list[ApplicationHistoryEntry] | None = Field(default=None, max_length=6)
    partial_reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_history(self) -> Self:
        if self.availability == "unavailable" and (
            self.completeness != "unavailable"
            or self.entries is not None
            or self.partial_reason_codes
        ):
            raise ValueError("unavailable history must not claim history evidence")
        if self.availability == "available" and (
            self.completeness == "unavailable" or self.entries is None
        ):
            raise ValueError("available history requires entry collection")
        if self.completeness == "exact" and self.partial_reason_codes:
            raise ValueError("exact history cannot carry partial reasons")
        if self.completeness == "partial" and not self.partial_reason_codes:
            raise ValueError("partial history requires source reasons")
        entries = self.entries or []
        if len({entry.id for entry in entries}) != len(entries):
            raise ValueError("history entry identities must be unique")
        return self


class ApplicationSourceEvidence(StrictModel):
    availability: ApplicationProjectionAvailability
    completeness: ApplicationProjectionCompleteness
    conflict: ApplicationSourceConflict | None = None
    repository_ref: str | None = None
    default_branch: str | None = None
    manifest_path: str | None = None
    partial_reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source_evidence(self) -> Self:
        values = (self.conflict, self.repository_ref, self.default_branch, self.manifest_path)
        if self.availability == "unavailable" and (
            self.completeness != "unavailable"
            or any(value is not None for value in values)
            or self.partial_reason_codes
        ):
            raise ValueError("unavailable source must not claim source evidence")
        if self.availability == "available" and (
            self.completeness == "unavailable"
            or self.conflict is None
            or self.repository_ref is None
        ):
            raise ValueError("available source requires repository provenance")
        if self.completeness == "exact" and (
            self.default_branch is None or self.manifest_path is None or self.partial_reason_codes
        ):
            raise ValueError("exact source requires branch, manifest, and no partial reasons")
        if self.completeness == "partial" and not self.partial_reason_codes:
            raise ValueError("partial source requires source reasons")
        return self


class ApplicationClusterScope(StrictModel):
    """Wire-safe scope evidence for an authorized application instance.

    This stays in the gateway response module so importing the public HTTP
    response catalog never creates a cycle through the command parity models.
    Its JSON shape is intentionally compatible with the shared cluster scope.
    """

    workspace_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    namespaces: tuple[str, ...] = ()
    freshness: Literal["live", "stale", "partial", "disconnected"] = "live"

    @field_validator("namespaces")
    @classmethod
    def canonicalize_namespaces(cls, namespaces: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({namespace.strip() for namespace in namespaces if namespace.strip()}))


class ApplicationInstanceScope(StrictModel):
    """One immutable deployment binding the caller may select in detail."""

    id: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    status: str = Field(min_length=1)
    scope: ApplicationClusterScope


class ApplicationWorkloadScopeItem(StrictModel):
    """One direct, currently observed workload a caller may select.

    ``key`` is an opaque inventory identity.  It is intentionally not a
    browser-assembled ``kind/namespace/name`` string: the selected deployment
    binding already fixes the cluster, and the immutable resource reference
    keeps same-name recreation visible to consumers.
    """

    key: str = Field(min_length=1, max_length=128)
    resource: ResourceRef
    scope: ApplicationClusterScope
    observed_at: str | None = None


class ApplicationWorkloadScope(StrictModel):
    """Authorized app/workload choices backed by rendered-manifest evidence."""

    availability: ApplicationProjectionAvailability
    completeness: ApplicationProjectionCompleteness
    application_scope_available: bool
    selected_workload_key: str | None = None
    workloads: list[ApplicationWorkloadScopeItem] = Field(default_factory=list, max_length=200)
    partial_reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_workload_scope(self) -> Self:
        if self.availability == "unavailable" and (
            self.completeness != "unavailable"
            or self.application_scope_available
            or self.selected_workload_key is not None
            or self.workloads
            or self.partial_reason_codes
        ):
            raise ValueError("unavailable workload scope must not claim workload evidence")
        if self.availability == "available" and self.completeness == "unavailable":
            raise ValueError("available workload scope requires a completeness value")
        keys = [item.key for item in self.workloads]
        if len(keys) != len(set(keys)):
            raise ValueError("workload scope identities must be unique")
        if self.selected_workload_key is not None and self.selected_workload_key not in keys:
            raise ValueError("selected workload must be among server-authorized workloads")
        if not self.application_scope_available and not (
            self.completeness == "exact"
            and len(self.workloads) == 1
            and self.selected_workload_key == self.workloads[0].key
        ):
            raise ValueError("only one exact workload may hide application scope")
        if self.completeness == "exact" and self.partial_reason_codes:
            raise ValueError("exact workload scope cannot carry partial reasons")
        if self.completeness == "partial" and not self.partial_reason_codes:
            raise ValueError("partial workload scope requires source reasons")
        return self


class ApplicationUnavailableEvidence(StrictModel):
    """A deliberately unavailable workload channel with its server reason."""

    availability: Literal["unavailable"] = "unavailable"
    reason_codes: list[str] = Field(min_length=1, max_length=20)

    @field_validator("reason_codes")
    @classmethod
    def unique_reason_codes(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("unavailable evidence reasons must be unique")
        if any(not value.strip() for value in values):
            raise ValueError("unavailable evidence reasons must be non-empty")
        return values


class ApplicationWorkloadDetail(StrictModel):
    """Only evidence that is genuinely scoped to the selected workload."""

    workload: ApplicationWorkloadScopeItem
    runtime_readiness: ApplicationRuntimeReadiness
    resource_counts: list[ApplicationResourceKindCount] | None = None
    resource_counts_completeness: ApplicationProjectionCompleteness
    topology: ApplicationTopology
    history: ApplicationUnavailableEvidence
    cost: CostWorkloadAllocation
    actions: ApplicationUnavailableEvidence

    @model_validator(mode="after")
    def validate_workload_detail(self) -> Self:
        if self.resource_counts_completeness == "unavailable" and self.resource_counts is not None:
            raise ValueError("unavailable workload resource counts must be null")
        if self.resource_counts_completeness != "unavailable" and self.resource_counts is None:
            raise ValueError("available workload resource counts must be an array")
        if isinstance(self.cost, CostObservedWorkloadAllocation) and (
            self.workload.resource.kind not in get_args(CostWorkloadKind)
        ):
            raise ValueError("observed cost requires a supported workload kind")
        return self


class ApplicationDetailScope(StrictModel):
    """Server-authorized environment and instance choices for one application."""

    availability: ApplicationProjectionAvailability
    completeness: ApplicationProjectionCompleteness
    selected_instance_id: str | None = None
    instances: list[ApplicationInstanceScope] = Field(default_factory=list, max_length=500)
    partial_reason_codes: list[str] = Field(default_factory=list)
    selected_scope: Literal["application", "workload"] = "application"
    workload_scope: ApplicationWorkloadScope

    @model_validator(mode="after")
    def validate_instance_scope(self) -> Self:
        if self.availability == "unavailable" and (
            self.completeness != "unavailable"
            or self.selected_instance_id is not None
            or self.instances
            or self.partial_reason_codes
        ):
            raise ValueError("unavailable instance scope must not claim scope evidence")
        if self.availability == "available" and (
            self.completeness == "unavailable" or not self.instances
        ):
            raise ValueError("available instance scope requires selectable instances")
        instance_ids = [instance.id for instance in self.instances]
        if len(instance_ids) != len(set(instance_ids)):
            raise ValueError("instance scope identities must be unique")
        if self.availability == "available" and self.selected_instance_id not in instance_ids:
            raise ValueError("available instance scope requires a selected allowed instance")
        if self.completeness == "exact" and self.partial_reason_codes:
            raise ValueError("exact instance scope cannot carry partial reasons")
        if self.completeness == "partial" and not self.partial_reason_codes:
            raise ValueError("partial instance scope requires source reasons")
        if self.selected_scope == "workload" and self.workload_scope.selected_workload_key is None:
            raise ValueError("workload selection requires a selected workload")
        if (
            self.selected_scope == "application"
            and self.workload_scope.availability == "available"
            and not self.workload_scope.application_scope_available
        ):
            raise ValueError("application selection is not available for one exact workload")
        return self


class ApplicationProductDetail(ApplicationProductCard):
    endpoints: list[ApplicationEndpointSummary] | None = None
    endpoints_completeness: ApplicationProjectionCompleteness
    recent_incidents: list[ApplicationRecentIncident] = Field(default_factory=list, max_length=3)
    recent_activity: list[ApplicationRecentActivity] = Field(default_factory=list, max_length=3)
    topology: ApplicationTopology
    history: ApplicationHistory
    source: ApplicationSourceEvidence
    scope: ApplicationDetailScope
    workload: ApplicationWorkloadDetail | None = None

    @model_validator(mode="after")
    def validate_detail_semantics(self) -> Self:
        if self.endpoints_completeness == "unavailable" and self.endpoints is not None:
            raise ValueError("unavailable endpoints must be null")
        if self.endpoints_completeness != "unavailable" and self.endpoints is None:
            raise ValueError("available endpoints must be an array")
        selected_workload = self.scope.selected_scope == "workload"
        if selected_workload != (self.workload is not None):
            raise ValueError("workload detail must match the selected scope")
        if self.workload is not None and (
            self.workload.workload.key != self.scope.workload_scope.selected_workload_key
        ):
            raise ValueError("workload detail must match the selected workload")
        return self


class ApplicationProductListResponse(StrictModel):
    applications: list[ApplicationProductCard] = Field(default_factory=list)


class ApplicationProductDetailResponse(StrictModel):
    application: ApplicationProductDetail


class ApplicationDeploymentHistoryItem(StrictModel):
    id: str = Field(min_length=1)
    environment: str | None = None
    cluster_id: str = Field(min_length=1)
    git_sha: str | None = None
    version: str | None = None
    deployed_at: str | None = None
    deployed_by: str | None = None
    status: ApplicationDeploymentStatus
    gitops_change_id: str | None = None


class ApplicationDeploymentHistoryResponse(StrictModel):
    deployments: list[ApplicationDeploymentHistoryItem] = Field(default_factory=list)


class ApplicationDriftDifference(StrictModel):
    resource: str = Field(min_length=1)
    field_path: str = Field(min_length=1)
    old_value: ApplicationDriftScalar = None
    new_value: ApplicationDriftScalar = None
    value_redacted: bool
    changed_by: str | None = None
    changed_at: str | None = None


class ApplicationDriftResponse(StrictModel):
    status: ApplicationDriftStatus
    summary: str | None = None
    differences: list[ApplicationDriftDifference] = Field(default_factory=list)
    observed_at: str | None = None

    @model_validator(mode="after")
    def validate_drift_semantics(self) -> Self:
        if self.status == "drifted" and (not self.differences or not self.summary):
            raise ValueError("drifted response requires differences and a summary")
        if self.status != "drifted" and (self.differences or self.summary is not None):
            raise ValueError("non-drift response cannot carry differences or summary")
        if any(
            item.value_redacted is False and item.old_value is None and item.new_value is None
            for item in self.differences
        ):
            raise ValueError("empty drift values must be marked redacted")
        return self


class ApplicationResponse(StrictModel):
    application: JsonMap


class ApplicationListResponse(StrictModel):
    applications: list[JsonMap]


class RepositoryProbeResponse(StrictModel):
    repo_ref: str
    normalized_repo_ref: str
    valid: bool
    reachable: bool
    default_branch: str | None = None
    private: bool | None = None
    html_url: str | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class RepositoryBranchItem(StrictModel):
    name: str
    protected: bool = False
    default: bool = False


class RepositoryBranchListResponse(StrictModel):
    repo_ref: str
    default_branch: str | None = None
    branches: list[RepositoryBranchItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RepositoryManifestCandidate(StrictModel):
    path: str
    source_type: str
    display_name: str
    reason: str = ""


class RepositoryManifestCandidateListResponse(StrictModel):
    repo_ref: str
    branch: str
    candidates: list[RepositoryManifestCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RepoManifestFile(StrictModel):
    path: str
    kinds: list[str] = Field(default_factory=list)


class RepoManifestFileListResponse(StrictModel):
    repo: str
    branch: str
    manifests: list[RepoManifestFile] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RepositoryManifestResource(StrictModel):
    api_version: str = ""
    kind: str
    namespace: str | None = None
    name: str


class RepositoryManifestValidationResponse(StrictModel):
    repo_ref: str
    branch: str
    manifest_path: str
    valid: bool
    status: str
    validation_mode: str
    resource_count: int = 0
    resources: list[RepositoryManifestResource] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class RepositoryConnectionPreviewFieldChange(StrictModel):
    """관리필드 단위 변경 한 건(연결 시 desired 로 수렴)."""

    field_path: str
    classification: str
    before: str
    after: str


class RepositoryConnectionPreviewResource(StrictModel):
    """연결하면 이 리소스가 어떻게 되는지 — create/update/in_sync/conflict."""

    api_version: str = ""
    kind: str
    namespace: str | None = None
    name: str
    # create=클러스터에 없음(생성) · update=있으나 변경 · in_sync=있고 일치 유지 ·
    # conflict=다른 활성 앱이 이미 소유(연결 시 상호 덮어쓰기 위험)
    change: Literal["create", "update", "in_sync", "conflict"]
    live_observed: bool = False
    status: str = ""
    field_changes: list[RepositoryConnectionPreviewFieldChange] = Field(default_factory=list)
    # conflict 인 경우 이미 소유 중인 앱 식별자
    owned_by: str | None = None


class RepositoryConnectionPreviewResponse(StrictModel):
    """연결 전 desired vs live 프리뷰 결과."""

    repo_ref: str
    branch: str
    manifest_path: str
    cluster_id: str
    namespace: str
    revision: str = ""
    valid: bool = False
    # 하나라도 live 관측이 있었는지 — 전부 미관측이면 클러스터가 비었거나 관측 전.
    live_observed: bool = False
    create_count: int = 0
    update_count: int = 0
    in_sync_count: int = 0
    conflict_count: int = 0
    resources: list[RepositoryConnectionPreviewResource] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class DeploymentBindingResponse(StrictModel):
    deployment: JsonMap


class DeploymentBindingListResponse(StrictModel):
    deployments: list[JsonMap]


class PromotionGateResponse(StrictModel):
    eligible: bool
    command_status: str
    command_completed: bool
    applied: bool | None = None
    applied_not_false: bool
    failed_resources: list[JsonMap] = Field(default_factory=list)
    failed_resource_count: int = 0
    rollout_ready: bool | None = None
    rollout_ready_not_false: bool


class WorkflowRunItemResponse(BaseModel):
    """기존 동적 run payload를 보존하면서 promotion gate만 구조화한다."""

    model_config = ConfigDict(extra="allow")

    promotion_gate: PromotionGateResponse | None = None


class WorkflowRunListResponse(StrictModel):
    runs: list[WorkflowRunItemResponse]


class ReleasePlanResponse(StrictModel):
    plan: JsonMap


class ReleasePlanListResponse(StrictModel):
    plans: list[JsonMap]


class ReleasePlanPreviewResponse(StrictModel):
    preview: JsonMap


class GeneratedManifestFile(StrictModel):
    path: str
    content: str
    action: str = "upsert"
    description: str = ""


class GeneratedManifestResource(StrictModel):
    api_version: str = ""
    kind: str
    namespace: str = ""
    name: str


class ReleaseReadinessResponse(StrictModel):
    ready: bool
    mode: str
    summary: str
    checks: list[JsonMap] = Field(default_factory=list)
    impact: JsonMap = Field(default_factory=dict)
    next_actions: list[JsonMap] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReleaseRunResponse(StrictModel):
    run: JsonMap


class ReleaseRunAlertResponse(StrictModel):
    accepted: bool
    event: JsonMap | None = None
    run: JsonMap


class ReleaseRunHandoffResponse(StrictModel):
    handoff: JsonMap


class ReleaseRunReportResponse(StrictModel):
    report: JsonMap


class ReleaseRunListResponse(StrictModel):
    runs: list[JsonMap]


class ReleaseRunSummaryResponse(StrictModel):
    total_runs: int
    status_breakdown: dict[str, int] = Field(default_factory=dict)
    plan_breakdown: dict[str, int] = Field(default_factory=dict)
    active_runs: int = 0
    succeeded_runs: int = 0
    cancelled_runs: int = 0
    attention_required_runs: int = 0
    failed_runs: int = 0
    paused_runs: int = 0
    rollback_requested_runs: int = 0
    waiting_for_approval_runs: int = 0
    live_runs: int = 0
    unhealthy_runs: int = 0
    verification_failed_runs: int = 0
    verification_pending_timeout_runs: int = 0
    policy_override_runs: int = 0
    policy_override_breakdown: dict[str, int] = Field(default_factory=dict)
    active_change_freeze_runs: int = 0
    change_freeze_override_runs: int = 0
    stale_runs: int = 0
    last_run_status: str | None = None
    recent_runs: list[JsonMap] = Field(default_factory=list)


class ReleaseAuditListResponse(StrictModel):
    events: list[JsonMap] = Field(default_factory=list)


class ReleasePlanDispatchResponse(StrictModel):
    accepted: bool
    wave: int
    events: list[JsonMap]
    blockers: list[str] = Field(default_factory=list)
    run: JsonMap | None = None


class DiagnosticItem(StrictModel):
    source: str
    severity: str
    message: str
    code: str
    line: int = 1
    column: int = 1
    end_line: int = 1
    end_column: int = 2
    path: str | None = None
    action: str | None = None


class DiagnosticsResponse(StrictModel):
    diagnostics: list[DiagnosticItem]


class ReleaseManifestRenderResponse(StrictModel):
    manifest: str
    files: list[GeneratedManifestFile] = Field(default_factory=list)
    resources: list[GeneratedManifestResource] = Field(default_factory=list)
    resource_count: int = 0
    diagnostics: list[DiagnosticItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: str = ""


class ReleaseManifestSafePrResponse(ReleaseManifestRenderResponse):
    accepted: bool
    event_id: str
    correlation_id: str
    workflow_run_id: str = ""
    application_id: str = ""
    repo_ref: str = ""
    base_branch: str = ""
    manifest_path: str = ""
    commit_sha: str = ""
    patch_sha256: str = ""


class CatalogItemListResponse(StrictModel):
    items: list[JsonMap]


class CatalogItemResponse(StrictModel):
    item: JsonMap


class CatalogInstallAcceptedResponse(StrictModel):
    accepted: bool
    command_id: str
    correlation_id: str
    status: str


class ProviderCatalogResponse(StrictModel):
    providers: dict[str, list[JsonMap]]


class ClusterImportCandidate(StrictModel):
    cluster_id: str
    name: str
    source: str
    cloud_provider: str
    deploy_provider: str
    kube_context: str | None = None
    external_handle: str | None = None
    console_url: str | None = None
    direct_apply_available: bool = False
    labels: JsonMap = Field(default_factory=dict)


class ClusterRegistrationFlow(StrictModel):
    cloud_provider: str
    label: str
    status: str
    description: str
    deploy_providers: list[JsonMap] = Field(default_factory=list)
    default_deploy_provider: str
    supports_import: bool = False
    unavailable_reason: str | None = None
    import_candidates: list[ClusterImportCandidate] = Field(default_factory=list)


class ProviderClusterDiscoveryResponse(StrictModel):
    default_cloud_provider: str = "existing-k8s"
    default_deploy_provider: str = "manual-manifest"
    flows: list[ClusterRegistrationFlow] = Field(default_factory=list)
    import_candidates: list[ClusterImportCandidate] = Field(default_factory=list)


class ProviderValidationResponse(StrictModel):
    valid: bool
    errors: list[str]
    warnings: list[str]
    selected: dict[str, JsonMap]


class TargetPreflightResponse(StrictModel):
    valid: bool
    duplicate_cluster_id: bool
    provider_ready: bool
    agent_install_status: str
    connection_status: str
    kube_context_allowed: bool | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    selected: dict[str, JsonMap] = Field(default_factory=dict)
    last_agent_id: str | None = None
    last_seen_at: str | None = None
    management_access: ManagementAccessResponse | None = None
