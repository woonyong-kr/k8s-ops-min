from __future__ import annotations

from enum import StrEnum

# NATS JetStream 스트림 이름. 전 서비스 이벤트가 이 한 스트림에 적재.
STREAM_NAME = "SERVICE_EVENTS"

# 스트림 보존 한계. 무한 증가/암묵적 드롭을 막는 위생 설정.
# discard 기본값은 old(가득 차면 오래된 것부터 제거). max_age 는 초 단위.
STREAM_MAX_AGE_SECONDS = 7 * 24 * 60 * 60  # 7일
STREAM_MAX_BYTES = 512 * 1024 * 1024  # 512 MiB; 1Gi PVC의 파일시스템 오버헤드 고려
STREAM_DUPLICATE_WINDOW_SECONDS = 24 * 60 * 60  # relay crash/retry 중복 publish 억제


class EventSubject(StrEnum):
    """이벤트 subject(주제).

    네이밍 규칙: "<도메인>.<행동>[.<상세>]" 점(.) 구분 소문자.
    - 과거형(git.changed) = 이미 일어난 사실.
    - 요청형(command.requested) = 처리 요청 신호.
    StrEnum 이라 멤버 자체가 와이어 문자열.
    """

    # --- GitOps 분리 워커: webhook→manifest→diff ---
    GIT_WEBHOOK_RECEIVED = "git.webhook.received"  # 깃 webhook 수신(입구)
    GIT_CHANGED = "git.changed"  # 변경 확정
    MANIFEST_RENDERED = "manifest.rendered"  # k8s manifest 렌더
    MANIFEST_INVALID = "manifest.invalid"  # 배포 가능한 manifest 부재/파싱 실패
    DESIRED_DIFF_DETECTED = "desired.diff.detected"  # 원하는 상태와 차이 감지
    GITOPS_CHANGE_CONTEXT_DETECTED = "gitops.change_context.detected"  # RCA change context
    DIFF_ANALYZED = "diff.analyzed"  # diff 위험도 분석 결과

    # --- 대상 클러스터/에이전트(cluster-agent) ---
    AGENT_CONNECTED = "agent.connected"  # 에이전트 등록
    CLUSTER_EVIDENCE_RECEIVED = "cluster.evidence.received"  # 증거 수신(입구)
    CLUSTER_INVENTORY_SNAPSHOT_RECORDED = (
        "cluster.inventory.snapshot.recorded"  # inventory snapshot 저장
    )
    CLUSTER_DESIRED_STATE_CHANGED = "cluster.desired_state.changed"  # 목표 상태 등록/변경
    CLUSTER_RECONCILE_REQUESTED = "cluster.reconcile.requested"  # 상태 동기화 요청
    CLUSTER_RECONCILE_STARTED = "cluster.reconcile.started"  # 상태 동기화 시작
    CLUSTER_DRIFT_DETECTED = "cluster.drift.detected"  # 목표/실제 상태 차이
    CLUSTER_RECONCILE_COMPLETED = "cluster.reconcile.completed"  # 상태 동기화 판정 완료
    CLUSTER_RECONCILE_FAILED = "cluster.reconcile.failed"  # 상태 동기화 실패
    EVIDENCE_JOB_UPDATED = "evidence.job.updated"  # evidence job update event
    EVIDENCE_JOBS_QUEUED = "evidence.jobs.queued"  # evidence jobs queued event

    # --- 명령 처리(command-worker): 정책→디스패치→에이전트 큐 ---
    COMMAND_REQUESTED = "command.requested"  # 명령 요청
    COMMAND_REJECTED = "command.rejected"  # 정책 위반 거부
    COMMAND_DISPATCHED = "command.dispatched"  # 실행 계획 수립·대상 클러스터로 라우팅
    COMMAND_QUEUED_FOR_AGENT = "command.queued_for_agent"  # 에이전트 큐 적재
    COMMAND_CANCEL_REQUESTED = "command.cancel.requested"  # 실행 취소 의도 기록
    COMMAND_RETRY_REQUESTED = "command.retry.requested"  # 실패 명령의 수동 재시도 의도 기록
    COMMAND_COMPLETED = "command.completed"  # 에이전트 실행 완료

    # --- 원인 분석/안전 PR(rca-worker) ---
    INCIDENT_DETECTED = "incident.detected"  # 장애 플래그 판단 결과
    EVIDENCE_BUILT = "evidence.built"  # 증거 번들 구성
    EVIDENCE_BUNDLE_BUILT = "evidence.bundle.built"  # RCA 판단 근거 묶음 구성
    RCA_CANDIDATES_PLANNED = "rca.candidates.planned"  # RCA 원인 후보 생성
    RCA_CANDIDATES_EVALUATED = "rca.candidates.evaluated"  # RCA 원인 후보 평가
    RCA_COMPLETED = "rca.completed"  # 근본 원인 분석 완료
    RCA_ANALYSIS_BLOCKED = "rca.analysis_blocked"  # RCA 자동 확정 불가
    RCA_FOLLOWUP_REQUIRED = "rca.followup.required"  # RCA 후속 조치 필요
    RCA_RULE_MISSING = "rca.rule_missing"  # RCA rule 매칭 실패
    RCA_BACKLOG_ITEM_CREATED = "rca.backlog.created"  # RCA 개선 backlog 적재
    RCA_AI_FALLBACK_REQUESTED = "rca.ai_fallback.requested"  # AI fallback 분석 요청
    RECOVERY_PLANNED = "recovery.planned"  # 복구 조치 계획 수립
    RECOVERY_SELECTION_REQUESTED = "recovery.selection_requested"  # 사용자 복구 후보 선택 요청
    RECOVERY_ACTION_SELECTED = "recovery.action_selected"  # 복구 후보 선택 완료
    RECOVERY_PR_TRACKED = "recovery.pr.tracked"  # 생성된 Safe PR과 원 RCA 연결
    RECOVERY_PR_MERGED = "recovery.pr.merged"  # 서명된 GitHub webhook으로 merge 확인
    RECOVERY_VERIFICATION_STARTED = (
        "recovery.verification.started"  # exact binding 배포 성공 후 안정화 검증 시작
    )
    RECOVERY_VERIFICATION_UPDATED = (
        "recovery.verification.updated"  # 안정화 창의 최신 판정/근거 저장
    )
    RECOVERY_VERIFICATION_FAILED = (
        "recovery.verification.failed"  # 배포 실패·검증 시간 초과/회귀
    )
    RECOVERY_RETRY_REQUESTED = (
        "recovery.retry.requested"  # 사용자가 실패 단계에 맞는 복구 재시도를 명시적으로 요청
    )
    INCIDENT_RESOLVED = "incident.resolved"  # 복구 검증 완료 후 장애 종결
    SAFE_PR_PATCH_PREPARED = "safe_pr.patch_prepared"  # Safe PR 패치 초안 준비
    DIFF_EXPLAINED = "diff.explained"  # 패치 diff 와 위험 설명
    SAFE_PR_READY_FOR_CREATION = "safe_pr.ready_for_creation"  # 검증된 Safe PR 생성 요청
    ROLLOUT_DIAGNOSED = "rollout.diagnosed"  # 롤아웃 상태 진단
    APPROVAL_RECOMMENDED = "approval.recommended"  # 승인/거절 보조 판단
    RCA_ACTION_REQUIRED = "rca.action_required"  # 자동 진행 불가, 사람 조치 필요
    ALERT_REQUESTED = "alert.requested"  # 알람 전송/사전 배포 게이트 요청
    ALERT_DISPATCHED = "alert.dispatched"  # 알람 전송 완료(log/webhook provider)
    ALERT_REJECTED = "alert.rejected"  # 알람/정책 게이트 차단
    EMAIL_VERIFICATION_REQUESTED = "mail.email_verification.requested"  # 이메일 인증 요청
    EMAIL_VERIFICATION_SENT = "mail.email_verification.sent"  # 이메일 인증 발송 완료
    EMAIL_VERIFICATION_FAILED = "mail.email_verification.failed"  # 이메일 인증 발송 실패
    SAFE_PR_REQUESTED = "safe_pr.requested"  # PR 생성 요청(공통)
    SAFE_PR_CREATED = "safe_pr.created"  # repo-gateway 가 PR 생성 완료
    SAFE_PR_FAILED = "safe_pr.failed"  # repo-gateway 가 PR 생성 실패

    # --- AI conversation API: HTTP 대화 요청 -> agent worker -> 응답 이벤트 ---
    AI_MESSAGE_RECEIVED = "ai.message.received"  # 사용자 메시지 수신
    AI_MESSAGE_RESPONDED = "ai.message.responded"  # agent 응답 생성
    AI_MESSAGE_FAILED = "ai.message.failed"  # agent 응답 실패

    # --- 사용자별 웹 셸 상태 ---
    NAMESPACE_SCOPE_UPDATED = "namespace.scope.updated"
    UI_PREFERENCES_UPDATED = "ui.preferences.updated"
    CHECKS_SETTINGS_UPDATED = "checks.settings.updated"

    # --- Workspace/cluster integration configuration ---
    PROMETHEUS_INTEGRATION_CONFIGURED = "integration.prometheus.configured"

    # --- Helm workspace configuration ---
    HELM_CHART_SOURCE_DELETED = "helm.chart_source.deleted"
    HELM_CHART_SOURCE_REFRESHED = "helm.chart_source.refreshed"

    # --- GitOps 제품 상태(workflow-controller): 이벤트 흐름을 사용자 실행 객체로 투영 ---
    WORKFLOW_CREATED = "workflow.created"  # 앱/바인딩/커밋 기준 실행 객체 생성 요청
    WORKFLOW_RUN_STARTED = "workflow.run.started"  # 실행 객체 시작/재개
    WORKFLOW_STEP_RECORDED = "workflow.step.recorded"  # 단계 상태 기록
    WORKFLOW_RUN_COMPLETED = "workflow.run.completed"  # 실행 성공 종료
    WORKFLOW_RUN_FAILED = "workflow.run.failed"  # 실행 실패 종료
    APPROVAL_REQUESTED = "approval.requested"  # 쓰기 승인 필요
    APPROVAL_GRANTED = "approval.granted"  # 승인 완료 또는 자동 승인
    APPROVAL_REJECTED = "approval.rejected"  # 승인 거절

    # --- 신뢰성(공통): 재시도 소진 시 DLQ ---
    DEAD_LETTER_CREATED = "dead_letter.created"  # 죽은 편지(DLQ) 적재
    PIPELINE_CONTRACT_FAILED = "pipeline.contract_failed"  # 워커 간 이벤트 계약 위반


# 발행 enum 없이 구독자만 있는 예약 프리픽스(예: audit 프로젝터 산출물용).
RESERVED_STREAM_SUBJECTS = ("audit.>",)


def _derived_stream_subjects() -> list[str]:
    """스트림 subject 와일드카드를 EventSubject 에서 자동 파생.

    새 이벤트/도메인을 enum 에 추가하면 "<도메인>.>" 가 자동 포함됨 —
    수동 와일드카드 목록 동기화 불필요.
    """
    prefixes = {value.split(".", 1)[0] + ".>" for value in EventSubject}
    return sorted(prefixes | set(RESERVED_STREAM_SUBJECTS))


STREAM_SUBJECTS = _derived_stream_subjects()
