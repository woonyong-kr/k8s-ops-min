// ── 데모 서피스: 배포 · 이슈 · 타임라인 · 점검 · 비용 · 설정 (Master Spec 5.7~5.10) ──
// 원칙: 모든 숫자는 실제 백엔드 계약(어댑터 훅) 파생 — 관측 안 된 값은 채우지 않는다(no backfill).
// 시각은 공용 부품(KpiValue/MiniBars/RankList/MiniTimeline)과 셸 토큰만 사용. 제품 이식 시 D5 공용 표로 수렴한다.
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Rocket, Package, AlertTriangle, Bell, Clock, ShieldCheck, Coins,
  Building2, Globe, Check, Sparkle, Sparkles, Palette, RefreshCw, Lock, Pin,
  ChevronRight, MapPin, ShieldAlert, ArrowLeft, ArrowRight, ExternalLink, CircleAlert, CircleCheck,
  Lightbulb, Trash2,
} from "lucide-react";
import { UI, BLUE, HP, TINT, INSET, MONO, TYPE, SOFT, DUR, PRESENT_SCALE, RADIUS, SPACE, inkA, blueA, critA } from "./devpreview/theme";
import { GithubIcon } from "./devpreview/brandIcons";
import { useCostOverview } from "./devpreview/costFeed";
import { useChecksOverview } from "./devpreview/checksFeed";
import {
  RCA_DETAIL_REFRESH_MS,
  useEvidenceWindowPayload,
  useEvidenceObjectReferences,
  useIncidentRecentChanges,
  useLatestRcaReport,
  useRcaIssueDetails,
  useRecoveryAudit,
  useRemediationBundle,
  useRecoveryPlan,
  parseEvidenceObjectReference,
  type RcaIssueAttemptDetail,
  type RcaIssueDetailView,
} from "./devpreview/rcaDetailFeed";
import type { RcaReport } from "./api/evidence-schemas";
import type { RecoveryActionAccepted, RecoveryActionCandidate, RecoveryPlan } from "./api/recovery-schemas";
import type { RemediationBundleActionDraft } from "./api/rca-bundle-schemas";
import { isApiError } from "./api/client";
import { retryRecovery, selectRecoveryAction } from "./api/recovery";
import type { AiRecoveryHandoff, AiRecoveryPreview, AiRecoveryPreviewLine } from "./features/ai-assistant/aiRecoveryHandoff";
import { isActiveRcaIssue } from "./devpreview/rcaIssuesFeed";
import { sessionInitial, type SessionView } from "./devpreview/sessionFeed";
import {
  deleteAllStoredAiConversations,
  deleteStoredAiConversation,
  useAiConversations,
  useConversationDetail,
} from "./devpreview/aiFeed";
import {
  useAlertRules,
  useAlertChannels,
  type AlertEventsFeed,
  type AlertEventView,
} from "./devpreview/alertsFeed";
import { alertEventPresentation, alertSeverityTone, type AlertEventIcon, type AlertPresentationTone } from "./devpreview/alertEventPresentation";
import {
  useApplicationRuns,
  useHelmReleases,
  type ApplicationsFeed,
  type ApplicationRunView,
} from "./devpreview/deployFeed";
import { ProgressNodeRail, type ProgressNode } from "./devpreview/ProgressNodeRail";
import { DeployDetailHost, type DeployDetailTarget } from "./devpreview/DeployDetailPanel";
import { DetailDrawer, DetailDrawerTabs } from "./devpreview/DetailDrawer";
import { isActiveRunStatus, runEffectiveStatus, useReleaseActions, useReleaseFlow } from "./devpreview/releaseFlowFeed";
import { useChangeTimeline } from "./devpreview/changeTimelineFeed";
import { useTimelineBoard } from "./devpreview/timelineFeed";
import { useUiPreferences, useRefreshPolicies, useSettingsAccess } from "./devpreview/settingsFeed";
import { MiniTimeline } from "./devpreview/widgets";
import { operationalMessageLabel, statusLabel } from "./devpreview/statusLabel";
import { RepositoryConnections } from "./devpreview/RepositoryConnections";
import { RepositoryStatusList } from "./devpreview/RepositoryStatusList";
import { SegmentedControl } from "./devpreview/SegmentedControl";
import { groupApplicationsByRepository } from "./devpreview/repositoryRegistry";
import { selectScenarioRuns } from "./devpreview/scenarioGateSelection";
import { currentRecoveryAttemptPrUrl, recoveryDisplayedStep, recoveryProgressPercent, recoveryProgressState, withCreatedPullRequest, type RecoveryProgressOverride, type RecoveryProgressState } from "./devpreview/recoveryProgress";
import { issueAnalysisState } from "./devpreview/issueAnalysisState";
import { canOpenRecoveryPlan, canStartRecoveryReview } from "./devpreview/recoveryAccess";
import { pullRequestReference } from "./devpreview/pullRequestReference";
import { isSafePrRoute, recoveryRouteLabel } from "./devpreview/recoveryRoute";
import {
  evidencePreviewLines,
  evidenceReferenceMatches,
  mergeEvidenceReferences,
  missingEvidencePresentations,
  rcaSummaryPresentation,
} from "./devpreview/rcaPresentation";

// ── 상대 시간 포맷 — 서버 타임스탬프(ISO 또는 epoch ms)를 사람이 읽는 근사치로 ──
function fromNow(input: string | number | null): string {
  if (input === null) return "—";
  const ms = typeof input === "number" ? input : Date.parse(input);
  if (!Number.isFinite(ms)) return "—";
  const diff = Date.now() - ms;
  if (diff < 0) return "방금";
  const min = Math.floor(diff / 60000);
  if (min < 1) return "방금";
  if (min < 60) return `${min}분 전`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}시간 전`;
  const day = Math.floor(hr / 24);
  return `${day}일 전`;
}

// ── 상태 라벨 — 공용 statusLabel(신규 헬퍼) 우선, 이 서피스에서만 쓰는 소수 토큰은
//    로컬 보강(헬퍼 파일은 동시 편집 금지라 여기서 덧댄다). 매핑에 없으면 원문 유지. ──
const LOCAL_STATUS_KO: Record<string, string> = {
  firing: "발생 중",
  live: "실시간",
  stale: "지연",
  partial: "부분",
  trusted_proxy: "신뢰 프록시",
  service_admin: "서비스 관리자",
  applications: "애플리케이션 목록",
  changes: "변경 이력",
  cost_nodes: "비용·노드",
  cost_summary: "비용 요약",
  cost_trend: "비용 추이",
  dashboard: "대시보드",
  gitops_counts: "GitOps 집계",
  gitops_rows: "GitOps 항목",
  helm_detail: "Helm 상세",
  helm_list: "Helm 목록",
  issues_audit: "이슈 감사",
  metrics_kubernetes: "Kubernetes 메트릭",
  metrics_prometheus: "Prometheus 메트릭",
  metrics_pvc: "PVC 메트릭",
  metrics_rightsizing: "리소스 최적화 메트릭",
  port_sessions: "포트 세션",
  resource_list: "리소스 목록",
  resource_list_slow: "느린 리소스 목록",
  evidence_received: "증거 수신",
  evidence_collected: "증거 수집 완료",
  evidence_built: "증거 정규화 완료",
  evidence_bundled: "증거 묶음 생성",
  incident_detected: "장애 감지",
  rule_missing: "분석 규칙 확인 필요",
  backlog_created: "분석 대기",
  ai_fallback_requested: "AI 보완 분석 중",
  rca_planned: "RCA 계획됨",
  rca_in_progress: "RCA 분석 중",
  rca_evaluated: "원인 후보 평가 완료",
  rca_completed: "원인 분석 완료",
  followup_required: "추가 확인 필요",
  action_required: "복구 검토 필요",
  recovery_planned: "복구 계획 생성",
  selection_required: "복구 선택 필요",
  recovery_selected: "복구 조치 선택됨",
  command_requested: "복구 요청됨",
  command_dispatched: "복구 실행 중",
  command_queued: "복구 실행 대기",
  command_completed: "복구 실행 완료",
  command_rejected: "복구 명령 거부됨",
  pr_requested: "복구 PR 요청됨",
  pr_created: "복구 PR 생성됨",
  pr_failed: "복구 PR 생성 실패",
  pr_open: "복구 PR 검토 대기",
  deploy_pending: "복구 배포 중",
  verification_pending: "안정화 검증 중",
  failed: "복구 실패",
  incident_resolved: "복구 완료",
};
function koLabel(raw: string | null | undefined): string {
  const key = raw?.trim().toLowerCase();
  return (key ? LOCAL_STATUS_KO[key] : undefined) ?? statusLabel(raw);
}
// ── reason code 한글화 — 백엔드가 준 원시 스네이크 코드(:cluster 등 콜론 접미사 포함)를
//    사용자 친화 한글 문구로. 매핑에 없으면 일반 안내로 폴백하고, 원시 코드는 호출부에서
//    작은 부가표기로만 노출한다(코드 나열 대신 정돈된 안내). ──
const REASON_KO: Record<string, string> = {
  checks_observation_unavailable: "점검 관측 데이터가 아직 없습니다",
  checks_definition_unavailable: "점검 정의(카탈로그)가 아직 없습니다",
  checks_observation_stale: "점검 관측 데이터가 오래되었습니다",
  checks_observation_partial: "점검 관측이 부분적으로만 수집되었습니다",
  checks_observation_clock_skew: "점검 관측 시각에 편차가 있습니다",
  checks_namespace_scope_partial: "일부 네임스페이스만 점검 범위에 포함되었습니다",
  checks_catalog_conflict: "점검 카탈로그 정의가 충돌합니다",
  application_bindings_incomplete: "애플리케이션 바인딩이 아직 완료되지 않았습니다",
  cost_observation_unavailable: "비용 관측 데이터가 아직 없습니다",
  cost_observation_not_integrated: "비용 관측이 아직 연동되지 않았습니다",
  node_pricing_observation_not_integrated: "노드 단가 관측이 아직 연동되지 않았습니다",
};
function reasonLabel(code: string): string {
  const prefix = code.split(":")[0];
  return REASON_KO[code] ?? REASON_KO[prefix] ?? "관측 데이터가 아직 없습니다";
}
// 정돈된 honest 안내 — 원시 코드 프리픽스로 중복 제거해 한글 한 줄씩, 원시 코드는 작은 표기로만.
function ReasonNotes({ codes }: { codes: string[] }) {
  const seen = new Set<string>();
  const rows: string[] = [];
  for (const c of codes) { const k = c.split(":")[0]; if (!seen.has(k)) { seen.add(k); rows.push(k); } }
  if (rows.length === 0) return null;
  return (
    <ul style={{ display: "flex", flexDirection: "column", gap: 5, margin: "8px 0 0", padding: 0, listStyle: "none" }}>
      {rows.map((k) => (
        <li key={k} style={{ display: "flex", alignItems: "baseline", gap: 8, fontSize: TYPE.caption, color: UI.ink2 }}>
          <span style={{ width: 4, height: 4, borderRadius: 999, background: HP.warn, flexShrink: 0, transform: "translateY(-2px)" }} />
          {/* M27/M28: 원시 reason code는 사용자에게 노출하지 않는다 — 한글 honest 라벨만 표기. */}
          <span style={{ flex: 1, minWidth: 0 }}>{reasonLabel(k)}</span>
        </li>
      ))}
    </ul>
  );
}

// ── 공통 프레임: 전역 내비게이션이 화면 이름을 이미 소유하므로 본문 제목은 반복하지 않는다.
// 탭과 주 액션만 하나의 상단 도구막대에 두고, 제목은 main의 접근 가능한 이름으로 유지한다. ──
function Page({ title, icon: _icon, action, navigation, tabs, tab, onTab, ensureVerticalScroll = false, children }: {
  title: string; icon: React.ComponentType<{ size?: number; style?: React.CSSProperties }>;
  action?: React.ReactNode; navigation?: React.ReactNode;
  tabs?: string[]; tab?: string; onTab?: (t: string) => void;
  ensureVerticalScroll?: boolean; children: React.ReactNode;
}) {
  return (
    <main aria-label={title} style={{ minWidth: 0, minHeight: ensureVerticalScroll ? `calc(100vh / ${PRESENT_SCALE})` : undefined, boxSizing: "border-box", display: "flex", flexDirection: "column", gap: SPACE.card, padding: "12px 18px 40px" }}>
      {(navigation || tabs || action) && (
        <div data-surface-toolbar="true" style={{ minHeight: 34, display: "flex", alignItems: "center", gap: 10 }}>
          {navigation ?? (tabs && (
            <div role="tablist" aria-label={`${title} 보기`} style={{ display: "flex", gap: 2, background: inkA(0.05), borderRadius: 9, padding: 2, width: "fit-content" }}>
              {tabs.map((t) => (
                <button type="button" role="tab" key={t} className="product-focusable product-control" aria-selected={tab === t} onClick={() => onTab?.(t)}
                  style={{ position: "relative", border: "none", background: "transparent", borderRadius: 7, padding: "5px 16px", fontSize: TYPE.label, fontWeight: 600, color: tab === t ? UI.ink : UI.ink3, cursor: "pointer" }}>
                  {tab === t && <motion.span layoutId={`ptab-${title}`} transition={SOFT} style={{ position: "absolute", inset: 0, background: UI.card, borderRadius: 7, boxShadow: `0 1px 4px ${inkA(0.14)}` }} />}
                  <span style={{ position: "relative" }}>{t}</span>
                </button>
              ))}
            </div>
          ))}
          {action && <span style={{ marginLeft: "auto" }}>{action}</span>}
        </div>
      )}
      {children}
    </main>
  );
}

const Card = ({ children, pad = SPACE.card }: { children: React.ReactNode; pad?: number }) => (
  <div style={{ background: UI.card, border: `1px solid ${UI.line}`, borderRadius: RADIUS.card, padding: pad, minWidth: 0 }}>{children}</div>
);
const SettingsRow = ({ icon: I, title, sub, right }: { icon: React.ComponentType<{ size?: number; style?: React.CSSProperties }>; title: string; sub: string; right: React.ReactNode }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "13px 15px", borderBottom: `1px solid ${UI.line2}` }}>
    <span style={{ width: 32, height: 32, borderRadius: 9, background: inkA(0.05), display: "grid", placeItems: "center", flexShrink: 0 }}><I size={16} style={{ color: UI.ink2 }} /></span>
    <span style={{ minWidth: 0, flex: 1 }}>
      <span style={{ display: "block", fontSize: TYPE.body, fontWeight: 600, color: UI.heading }}>{title}</span>
      <span style={{ display: "block", fontSize: TYPE.caption, color: UI.ink3, marginTop: 1 }}>{sub}</span>
    </span>
    {right}
  </div>
);
const Pill = ({ tone, label }: { tone: "ok" | "warn" | "crit" | "info"; label: string }) => (
  <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: TYPE.caption, fontWeight: 600, borderRadius: 999, padding: "3px 9px", whiteSpace: "nowrap",
    color: tone === "ok" ? TINT.ok.fg : tone === "warn" ? TINT.warn.fg : tone === "crit" ? TINT.crit.fg : TINT.blue.fg,
    background: tone === "ok" ? TINT.ok.bg : tone === "warn" ? TINT.warn.bg : tone === "crit" ? critA(0.09) : blueA(0.08),
    border: `1px solid ${tone === "ok" ? TINT.ok.bd : tone === "warn" ? TINT.warn.bd : tone === "crit" ? critA(0.3) : blueA(0.25)}` }}>
    <span className={tone !== "ok" ? "pulsedot" : undefined} style={{ width: 5, height: 5, borderRadius: 999, background: tone === "info" ? BLUE : HP[tone] }} />{label}
  </span>
);
// 간이 표 행 — 제품에서는 D5 공용 표가 오너(여기서는 같은 타이포·헤어라인 문법만 재현)
function THead({ cols }: { cols: [string, string][] }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: cols.map(([, w]) => w).join(" "), gap: 12, padding: "8px 14px", borderBottom: `1px solid ${UI.line}`, background: UI.bg2 }}>
      {cols.map(([l]) => <span key={l} style={{ fontSize: TYPE.caption, fontWeight: 600, letterSpacing: "0.05em", color: UI.ink3 }}>{l}</span>)}
    </div>
  );
}
function TRow({ cols, cells, onClick, i = 0 }: { cols: [string, string][]; cells: React.ReactNode[]; onClick?: () => void; i?: number }) {
  return (
    <motion.button initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SOFT, delay: Math.min(i, 8) * 0.04 }}
      onClick={onClick} disabled={!onClick} className={onClick ? "rrow" : undefined}
      style={{ display: "grid", gridTemplateColumns: cols.map(([, w]) => w).join(" "), gap: 12, alignItems: "center", width: "100%", textAlign: "left", border: "none", background: "transparent", borderBottom: `1px solid ${UI.line2}`, padding: "10px 14px", cursor: onClick ? "pointer" : "default" }}>
      {cells.map((c, j) => <span key={j} style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: TYPE.label, color: UI.ink }}>{c}</span>)}
    </motion.button>
  );
}
// 서피스 요약 칩 — 홈·지도 상태 요약 줄과 같은 칩 문법(제품 P2에서 공용 컴포넌트로 수렴)
const segStyle: React.CSSProperties = { display: "flex", alignItems: "center", gap: 5, fontSize: TYPE.label, fontWeight: 600, color: UI.ink2, background: UI.card, border: `1px solid ${UI.line}`, borderRadius: 999, padding: "5px 11px", whiteSpace: "nowrap" };
const numStyle: React.CSSProperties = { fontWeight: 700, color: UI.ink, fontVariantNumeric: "tabular-nums" };
function ChipRow({ chips }: { chips: { label: string; value: React.ReactNode; warn?: boolean; crit?: boolean }[] }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      {chips.map((c) => (
        <span key={c.label} style={{ ...segStyle, ...(c.crit ? { borderColor: TINT.crit.bd, background: TINT.crit.bg, color: TINT.crit.fg } : c.warn ? { borderColor: TINT.warn.bd, background: TINT.warn.bg, color: TINT.warn.fg } : {}) }}>
          {c.label} <b style={numStyle}>{c.value}</b>
        </span>
      ))}
    </div>
  );
}

const Mono = ({ children, dim }: { children: React.ReactNode; dim?: boolean }) => (
  <span style={{ fontSize: TYPE.label, color: dim ? UI.ink3 : UI.ink, fontVariantNumeric: "tabular-nums" }}>{children}</span>
);


// ── 배포 /deploy — 탭: 애플리케이션 | GitOps | 워크플로우 | Helm 릴리스 (5.7) ──
// UI-PHASE2-001: 실 GET /api/applications(애플리케이션·GitOps·워크플로우 실행) +
// GET /api/helm/releases. 애플리케이션 jsonMap은 방어적으로 읽고, 없는 필드는
// 정직한 gap으로, Helm은 커버리지 unavailable + reason code를 그대로 렌더한다.
// 읽기 전용 — 여기서 어떤 배포/동기화 변형(mutation)도 발생시키지 않는다.
function healthPill(status: string | null): React.ReactNode {
  if (status === "healthy" || status === "ready") return <Pill tone="ok" label={koLabel(status)} />;
  if (status === "degraded" || status === "warning") return <Pill tone="warn" label={koLabel(status)} />;
  if (status === "critical" || status === "failed" || status === "unhealthy") return <Pill tone="crit" label={koLabel(status)} />;
  return <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>{status ? koLabel(status) : "관측 안 됨"}</span>;
}
function deliveryPill(status: string | null): React.ReactNode {
  if (status === null) return <Mono dim>—</Mono>;
  if (status === "succeeded" || status === "synced" || status === "healthy") return <Pill tone="ok" label={koLabel(status)} />;
  if (status === "failed" || status === "degraded" || status === "error") return <Pill tone="crit" label={koLabel(status)} />;
  if (status === "pending" || status === "progressing" || status === "running") return <Pill tone="info" label={koLabel(status)} />;
  return <Pill tone="warn" label={koLabel(status)} />;
}
const emptyRow = (msg: string) => <div style={{ padding: "14px 15px", fontSize: TYPE.label, color: UI.ink3 }}>{msg}</div>;

function detailString(details: Record<string, unknown>, key: string): string | null {
  const value = details[key];
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function detailEvidence(details: Record<string, unknown>, needles: string[]): string | null {
  const normalizedNeedles = needles.map((needle) => needle.toLowerCase());
  const visit = (value: unknown): string | null => {
    if (typeof value === "string") {
      const normalized = value.toLowerCase();
      return normalizedNeedles.some((needle) => normalized.includes(needle)) ? value : null;
    }
    if (Array.isArray(value)) {
      for (const item of value) {
        const match = visit(item);
        if (match) return match;
      }
      return null;
    }
    if (typeof value === "object" && value !== null) {
      for (const item of Object.values(value)) {
        const match = visit(item);
        if (match) return match;
      }
    }
    return null;
  };
  return visit(details);
}

const WORKFLOW_STEP_KO: Record<string, string> = {
  git: "Git 변경",
  render: "매니페스트 렌더링",
  diff: "변경 비교",
  policy: "정책 검토",
  approval: "승인",
  apply: "클러스터 적용",
  health: "상태 확인",
  safe_pr: "복구 PR",
};

function workflowStepLabel(name: string): string {
  const normalized = name.trim().toLowerCase();
  if (normalized === "") return "단계 이름 미관측";
  return WORKFLOW_STEP_KO[normalized] ?? name;
}

function isCompletedWorkflowStep(status: string | null): boolean {
  const normalized = status?.trim().toLowerCase() ?? "";
  return ["succeeded", "completed", "ready"].includes(normalized);
}

function isFailedWorkflowStep(status: string | null): boolean {
  const normalized = status?.trim().toLowerCase() ?? "";
  return ["failed", "error", "degraded", "blocked"].includes(normalized);
}

function WorkflowEvidencePanel({ runs, repositoryRef, status, onRefresh, onOpenRef, onOpenIssues, onAskAi }: {
  runs: ApplicationRunView[];
  repositoryRef: string | null;
  status: "loading" | "ready" | "unavailable";
  onRefresh: () => void;
  onOpenRef: (kind: string, name: string) => void;
  onOpenIssues: () => void;
  onAskAi: () => void;
}) {
  const selection = selectScenarioRuns(runs, repositoryRef);
  const scopedRuns = selection.runs;
  const latest = scopedRuns[0] ?? null;
  const failureStep = latest?.steps.find((step) =>
    detailEvidence(step.details, ["imagepullbackoff", "errimagepull", "image_pull_back_off"]) !== null
    || step.message?.toLowerCase().includes("imagepullbackoff") === true
    || step.message?.toLowerCase().includes("errimagepull") === true) ?? null;
  const failureResource = failureStep
    ? detailString(failureStep.details, "pod_name")
      ?? detailString(failureStep.details, "resource_name")
      ?? detailString(failureStep.details, "name")
    : null;
  const explicitCurrentIndex = latest?.currentStep
    ? latest.steps.findIndex((step) => step.name.toLowerCase() === latest.currentStep?.toLowerCase())
    : -1;
  const firstIncompleteIndex = latest?.steps.findIndex((step) => !isCompletedWorkflowStep(step.status)) ?? -1;
  const currentIndex = explicitCurrentIndex >= 0 ? explicitCurrentIndex : firstIncompleteIndex;
  const progressSteps: ProgressNode[] = latest?.steps.map((step, index) => {
    const failed = isFailedWorkflowStep(step.status)
      || (index === currentIndex && isFailedWorkflowStep(latest.status));
    const active = index === currentIndex && !isCompletedWorkflowStep(step.status) && !failed;
    const isFailureStep = step === failureStep;
    const waitingForApproval = active && latest.status === "waiting_for_approval";
    const prUrl = step.name === "safe_pr" ? detailString(step.details, "pr_url") : null;
    return {
      id: `${step.name || "unnamed"}-${step.updatedAt ?? index}`,
      label: workflowStepLabel(step.name),
      state: isCompletedWorkflowStep(step.status) ? "complete"
        : failed ? "failed"
          : active ? "active"
            : "pending",
      statusLabel: koLabel(step.status),
      description: step.message ? operationalMessageLabel(step.message) : null,
      observedAt: step.updatedAt ? fromNow(step.updatedAt) : null,
      activity: waitingForApproval ? "waiting" : active ? "running" : undefined,
      tone: waitingForApproval ? "warning" : "info",
      href: prUrl,
      actionLabel: isFailureStep ? (failureResource ? "파드 상세" : "AI 분석")
        : waitingForApproval ? "이슈/RCA"
          : null,
      onAction: isFailureStep ? (failureResource ? () => onOpenRef("Pod", failureResource) : onAskAi)
        : waitingForApproval ? onOpenIssues
          : null,
    };
  }) ?? [];

  return (
    <Card pad={16}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: TYPE.section, fontWeight: 700, color: UI.heading }}>워크플로 진행</div>
          <div style={{ marginTop: 4, fontSize: TYPE.caption, color: UI.ink3 }}>관측된 실행 단계와 현재 위치</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {latest && deliveryPill(latest.status)}
          <button className="product-focusable product-control" onClick={onRefresh} aria-label="워크플로 실행 새로고침" style={{ width: 30, height: 30, borderRadius: 8, border: `1px solid ${UI.line}`, background: UI.card, color: UI.ink2, cursor: "pointer", display: "grid", placeItems: "center" }}><RefreshCw size={14} /></button>
        </div>
      </div>
      {latest ? (
        <div style={{ display: "grid", gap: 14, marginTop: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0, flexWrap: "wrap", borderTop: `1px solid ${UI.line2}`, paddingTop: 12 }}>
            <span title={latest.workflowRunId} style={{ maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: UI.ink2, fontFamily: MONO, fontSize: TYPE.caption }}>
              {latest.workflowRunId}
            </span>
            <span aria-hidden="true" style={{ color: UI.line }}>·</span>
            <span style={{ color: UI.ink2, fontSize: TYPE.caption }}>{latest.applicationName}</span>
            {latest.repositoryRef ? <span style={{ color: UI.ink2, fontFamily: MONO, fontSize: TYPE.caption }}>{latest.repositoryRef}</span> : null}
            {latest.commitSha ? (
              <span title={latest.commitSha} style={{ color: UI.ink2, fontFamily: MONO, fontSize: TYPE.caption }}>
                commit {latest.commitSha.slice(0, 12)}
              </span>
            ) : null}
            {latest.updatedAt ? <span style={{ marginLeft: "auto", color: UI.ink2, fontSize: TYPE.caption }}>{fromNow(latest.updatedAt)}</span> : null}
          </div>
          {progressSteps.length > 0 ? (
            <ProgressNodeRail steps={progressSteps} ariaLabel="워크플로 진행 단계" />
          ) : (
            <div style={{ fontSize: TYPE.label, color: UI.ink3 }}>단계 데이터 관측 안 됨</div>
          )}
        </div>
      ) : (
        <div style={{ marginTop: 14, borderTop: `1px solid ${UI.line2}`, paddingTop: 14, fontSize: TYPE.label, color: UI.ink3 }}>
          {status === "loading" ? "불러오는 중…" : status === "unavailable" ? "워크플로 실행을 불러오지 못했습니다." : "관측된 워크플로 실행 없음"}
        </div>
      )}
    </Card>
  );
}
export function DeploySurface({ applicationsFeed, onRefreshApplications, pendingRepos = [], repositoryFilter = null, applicationDetailId = null, onApplicationDetailClose, onOpenRef, onOpenIssues, onAskAi, onAddRepo, topInset = 57, leftInset = 208, rightInset = 0 }: {
  applicationsFeed: ApplicationsFeed;
  onRefreshApplications: () => void;
  pendingRepos?: string[];
  repositoryFilter?: string | null;
  applicationDetailId?: string | null;
  onApplicationDetailClose?: () => void;
  onOpenRef: (kind: string, name: string) => void; onOpenIssues: () => void; onAskAi: () => void; onAddRepo: () => void;
  /** 상세 패널 겹침 방지용 크롬 인셋 — unified DetailOverlay와 같은 계약. */
  topInset?: number; leftInset?: number; rightInset?: number;
}) {
  const [tab, setTab] = useState(repositoryFilter ? "GitOps" : "워크플로우");
  // 행 클릭 → 상세 패널(읽기 전용). 한 번에 하나만 연다 — 전역 레이어 계약(70/71).
  const [detail, setDetail] = useState<DeployDetailTarget | null>(null);
  const [selectedRepository, setSelectedRepository] = useState<string | null>(repositoryFilter);
  const [expandedRepositories, setExpandedRepositories] = useState<string[]>(repositoryFilter ? [repositoryFilter] : []);
  useEffect(() => {
    if (!repositoryFilter) return;
    const timer = window.setTimeout(() => {
      setSelectedRepository(repositoryFilter);
      setExpandedRepositories((current) =>
        current.some((repositoryRef) => repositoryRef.toLowerCase() === repositoryFilter.toLowerCase())
          ? current
          : [...current, repositoryFilter],
      );
      setTab("GitOps");
    }, 0);
    return () => window.clearTimeout(timer);
  }, [repositoryFilter]);
  const [repositoryRefreshKey, setRepositoryRefreshKey] = useState(0);
  const appsFeed = applicationsFeed;
  const refreshRepositoryData = () => {
    setRepositoryRefreshKey((key) => key + 1);
    onRefreshApplications();
  };
  const workflowFeed = useApplicationRuns(appsFeed.items, repositoryRefreshKey);
  const helm = useHelmReleases();
  // 릴리스 탭 — 탭이 열려 있을 때만 조회한다(진행 중 런 관측 시 5초 폴링).
  const releaseFlow = useReleaseFlow(tab === "릴리스");
  const releaseActions = useReleaseActions(releaseFlow.refresh);
  const apps = appsFeed.items;
  useEffect(() => {
    if (applicationDetailId === null) return;
    const application = apps.find((candidate) => candidate.id === applicationDetailId);
    if (!application) return;
    const timer = window.setTimeout(() => {
      setTab("애플리케이션");
      setDetail({
        kind: "application",
        applicationId: application.id,
        name: application.name,
      });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [applicationDetailId, apps]);
  const repositoryGroups = useMemo(() => groupApplicationsByRepository(apps), [apps]);
  const connectedRepositoryKeys = useMemo(
    () => new Set(repositoryGroups.map((group) => group.repositoryRef.toLowerCase())),
    [repositoryGroups],
  );
  const pendingOnly = pendingRepos.filter((repositoryRef) => !connectedRepositoryKeys.has(repositoryRef.toLowerCase()));
  const workflowStatus = appsFeed.status === "unavailable"
    ? "unavailable"
    : appsFeed.status === "loading"
      ? "loading"
      : workflowFeed.status;
  const visibleWorkflowRuns = workflowFeed.items.filter((run) =>
    !run.workflowRunId.startsWith("workflow-connect-validation-")
    && (
      selectedRepository === null
      || run.repositoryRef?.toLowerCase() === selectedRepository.toLowerCase()
    ));
  const appCols: [string, string][] = [["앱", "minmax(140px,1.4fr)"], ["환경", "minmax(80px,0.8fr)"], ["저장소", "minmax(150px,1.4fr)"], ["헬스", "minmax(110px,0.9fr)"], ["배포", "minmax(90px,0.8fr)"], ["브랜치", "minmax(70px,0.6fr)"]];
  const wfCols: [string, string][] = [["앱", "minmax(140px,1.2fr)"], ["워크플로우 실행", "minmax(200px,1.8fr)"], ["상태", "minmax(90px,0.8fr)"], ["관측 시각", "minmax(80px,0.7fr)"]];
  const helmCols: [string, string][] = [["릴리스", "minmax(120px,1.1fr)"], ["차트", "minmax(150px,1.4fr)"], ["차트 버전", "minmax(80px,0.8fr)"], ["네임스페이스", "minmax(90px,0.9fr)"], ["리비전", "56px"], ["상태", "minmax(90px,0.8fr)"]];
  const releaseRunCols: [string, string][] = [["런 / 플랜", "minmax(180px,1.5fr)"], ["웨이브", "minmax(70px,0.6fr)"], ["상태", "minmax(100px,0.8fr)"], ["시작", "minmax(80px,0.7fr)"], ["시작자", "minmax(90px,0.7fr)"]];
  const releasePlanCols: [string, string][] = [["플랜", "minmax(180px,1.5fr)"], ["단계", "minmax(60px,0.5fr)"], ["상태", "minmax(100px,0.8fr)"], ["최근 런", "minmax(110px,0.9fr)"], ["수정", "minmax(80px,0.7fr)"]];
  const loading = appsFeed.status === "loading";
  return (
    <>
    <Page title="배포" icon={Rocket} ensureVerticalScroll
      navigation={
        <SegmentedControl
          active={tab}
          ariaLabel="배포 보기"
          indicatorId="ptab-배포"
          items={[
            { value: "애플리케이션", label: "애플리케이션" },
            { value: "GitOps", label: "GitOps" },
            { value: "워크플로우", label: "워크플로우" },
            { value: "Helm 릴리스", label: "Helm 릴리스" },
            { value: "릴리스", label: "릴리스" },
          ]}
          onChange={setTab}
        />
      }
      action={tab === "GitOps"
        ? <button className="product-focusable product-action" onClick={onAddRepo} style={{ display: "flex", alignItems: "center", gap: 6, border: "none", background: BLUE, color: UI.card, borderRadius: 9, padding: "6px 13px", fontSize: TYPE.label, fontWeight: 600, cursor: "pointer" }}>+ 저장소 연결</button>
        : null}>
      <ChipRow chips={[
        { label: "앱", value: appsFeed.status === "ready" ? apps.length : "—" },
        { label: "배포 대기", value: appsFeed.status === "ready" ? apps.filter((a) => a.deliveryStatus === "pending").length : "—" },
        { label: "저장소", value: appsFeed.status === "ready" ? repositoryGroups.length + pendingOnly.length : "—" },
        { label: "Helm", value: helm.status === "ready" && helm.coverageAvailability === "available" ? helm.items.length : "—", warn: helm.status === "ready" && helm.coverageAvailability === "unavailable" },
      ]} />
      {appsFeed.stale && (
        <span style={{ fontSize: TYPE.caption, color: TINT.warn.fg }}>
          애플리케이션 · 최근 관측값 표시 중 · 재조회 대기
        </span>
      )}
      {tab === "애플리케이션" && (
        <Card pad={0}>
          <THead cols={appCols} />
          {loading ? emptyRow("불러오는 중…")
            : appsFeed.status === "unavailable" ? emptyRow("애플리케이션을 불러오지 못했습니다.")
            : apps.length === 0 ? emptyRow("관측된 애플리케이션 없음")
            : apps.map((a, i) => (
              <TRow key={a.id} cols={appCols} i={i}
                onClick={() => setDetail({ kind: "application", applicationId: a.id, name: a.name })} cells={[
                <span key="n" style={{ display: "flex", alignItems: "center", gap: 8 }}><Package size={13} style={{ color: BLUE, flexShrink: 0 }} /><Mono>{a.name}</Mono></span>,
                <span key="e" style={{ fontSize: TYPE.label, color: UI.ink2 }}>{a.environments.length ? a.environments.join(", ") : "—"}</span>,
                <Mono key="r" dim>{a.repositoryRef ?? "—"}</Mono>,
                healthPill(a.healthStatus),
                deliveryPill(a.deliveryStatus),
                <Mono key="b" dim>{a.defaultBranch ?? "—"}</Mono>,
              ]} />
            ))}
        </Card>
      )}
      {tab === "GitOps" && (
        <Card pad={10}>
          {loading ? emptyRow("불러오는 중…")
            : appsFeed.status === "unavailable" ? emptyRow("GitOps 바인딩을 불러오지 못했습니다.")
            : <>
              <RepositoryConnections
                groups={repositoryGroups}
                expandedRepositories={expandedRepositories}
                onOpenRepository={(repositoryRef) => {
                  const repositoryKey = repositoryRef.toLowerCase();
                  const isOpen = expandedRepositories.some((current) => current.toLowerCase() === repositoryKey);
                  setExpandedRepositories((current) =>
                    isOpen
                      ? current.filter((expandedRepository) => expandedRepository.toLowerCase() !== repositoryKey)
                      : [...current, repositoryRef],
                  );
                  setSelectedRepository(isOpen ? null : repositoryRef);
                }}
                onDisconnected={refreshRepositoryData}
              />
              {pendingOnly.map((repositoryRef) => (
                <div key={repositoryRef} style={{ display: "flex", alignItems: "center", gap: 9, padding: "9px 8px", color: UI.ink3 }}>
                  <GithubIcon size={15} style={{ flexShrink: 0 }} />
                  <Mono>{repositoryRef}</Mono>
                  <span style={{ marginLeft: "auto" }}><Pill tone="info" label="연결 중" /></span>
                </div>
              ))}
              {/* 연결 상태 관리 — degraded/disconnected 포함 전체 저장소 상태 + 해제. */}
              <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid #eef0f2" }}>
                <div style={{ marginBottom: 6, color: "#6b7280", fontSize: 11, fontWeight: 700 }}>전체 연결 상태</div>
                <RepositoryStatusList
                  key={repositoryRefreshKey}
                  onChanged={refreshRepositoryData}
                />
              </div>
            </>}
        </Card>
      )}
      {tab === "워크플로우" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <WorkflowEvidencePanel runs={workflowFeed.items} repositoryRef={selectedRepository} status={workflowStatus}
            onRefresh={refreshRepositoryData}
            onOpenRef={onOpenRef} onOpenIssues={onOpenIssues} onAskAi={onAskAi} />
          <Card pad={0}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "13px 15px", borderBottom: `1px solid ${UI.line2}` }}>
              <span style={{ color: UI.heading, fontSize: TYPE.body, fontWeight: 700 }}>실행 기록</span>
              <span style={{ color: UI.ink3, fontFamily: MONO, fontSize: TYPE.caption }}>
                {workflowStatus === "ready" ? `${visibleWorkflowRuns.length}건` : "—"}
              </span>
            </div>
            <THead cols={wfCols} />
            {workflowStatus === "loading" ? emptyRow("불러오는 중…")
              : workflowStatus === "unavailable" ? emptyRow("워크플로우 실행을 불러오지 못했습니다.")
              : visibleWorkflowRuns.length === 0 ? emptyRow("관측된 워크플로 실행 없음")
              : visibleWorkflowRuns.map((run, i) => {
                return (
                <TRow key={run.workflowRunId} cols={wfCols} i={i}
                  onClick={() => setDetail({ kind: "run", workflowRunId: run.workflowRunId })} cells={[
                  <span key="n" style={{ display: "flex", alignItems: "center", gap: 8 }}><Rocket size={13} style={{ color: BLUE, flexShrink: 0 }} /><Mono>{run.applicationName}</Mono></span>,
                  <Mono key="w" dim>{run.workflowRunId}</Mono>,
                  deliveryPill(run.status),
                  <Mono key="t" dim>{fromNow(run.updatedAt ?? run.createdAt)}</Mono>,
                ]} />
                );
              })}
          </Card>
        </div>
      )}
      {tab === "Helm 릴리스" && (
        <Card pad={0}>
          <THead cols={helmCols} />
          {helm.status === "loading" ? emptyRow("불러오는 중…")
            : helm.status === "unavailable" ? emptyRow("Helm 릴리스를 불러오지 못했습니다.")
            : helm.items.length === 0 ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "14px 15px" }}>
                <span style={{ fontSize: TYPE.body, fontWeight: 600, color: UI.ink2 }}>Helm 릴리스 관측 안 됨</span>
                <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>현재 스코프에서 Helm 저장소 관측이 완결되지 않았습니다.</span>
                <ReasonNotes codes={helm.reasonCodes} />
              </div>
            )
            : helm.items.map((h, i) => (
              <TRow key={`${h.clusterId}/${h.storageNamespace}/${h.name}`} cols={helmCols} i={i}
                onClick={() => setDetail({ kind: "helm", identity: { clusterId: h.clusterId, storageNamespace: h.storageNamespace, name: h.name }, displayNamespace: h.namespace })} cells={[
                <span key="n" style={{ display: "flex", alignItems: "center", gap: 8 }}><Package size={13} style={{ color: BLUE, flexShrink: 0 }} /><Mono>{h.name}</Mono></span>,
                <Mono key="c" dim>{h.chart ?? "—"}</Mono>, <Mono key="v">{h.chartVersion ?? "—"}</Mono>,
                <Mono key="ns" dim>{h.namespace}</Mono>, <Mono key="rv">{h.revision ?? "—"}</Mono>,
                deliveryPill(h.status),
              ]} />
            ))}
        </Card>
      )}
      {tab === "릴리스" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {releaseFlow.activeRuns.length > 0 && (
            <Card pad={0}>
              <div style={{ padding: "10px 14px", borderBottom: `1px solid ${UI.line2}`, background: UI.bg2, fontSize: TYPE.caption, fontWeight: 700, color: UI.ink2 }}>진행 중 릴리스 런</div>
              <THead cols={releaseRunCols} />
              {releaseFlow.activeRuns.map((run, i) => (
                <TRow key={run.run_id} cols={releaseRunCols} i={i}
                  onClick={() => setDetail({ kind: "releaseRun", runId: run.run_id })} cells={[
                  <span key="r"><Mono>{run.run_id}</Mono> <Mono dim>· {run.plan_name}</Mono></span>,
                  <Mono key="w">{run.current_wave}/{run.total_waves}</Mono>,
                  deliveryPill(runEffectiveStatus(run)),
                  <Mono key="t" dim>{fromNow(run.created_at ?? null)}</Mono>,
                  <Mono key="a" dim>{typeof run.started_by === "string" && run.started_by !== "" ? run.started_by : "—"}</Mono>,
                ]} />
              ))}
            </Card>
          )}
          <Card pad={0}>
            <THead cols={releasePlanCols} />
            {releaseFlow.status === "loading" ? emptyRow("불러오는 중…")
              : releaseFlow.status === "unavailable" ? emptyRow("릴리스 플랜을 불러오지 못했습니다.")
              : releaseFlow.plans.length === 0 ? emptyRow("관측된 릴리스 플랜 없음")
              : releaseFlow.plans.map((plan, i) => {
                const planKey = plan.plan_id ?? plan.name;
                const latestRun = releaseFlow.runs.find((run) => run.plan_id === (plan.plan_id ?? "")) ?? null;
                return (
                  <TRow key={planKey} cols={releasePlanCols} i={i}
                    onClick={() => setDetail({ kind: "releasePlan", planKey })} cells={[
                    <Mono key="n">{plan.name}</Mono>,
                    <Mono key="s" dim>{plan.steps.length}</Mono>,
                    deliveryPill(plan.status),
                    latestRun === null ? <Mono key="lr" dim>—</Mono> : deliveryPill(runEffectiveStatus(latestRun)),
                    <Mono key="u" dim>{fromNow(plan.updated_at ?? null)}</Mono>,
                  ]} />
                );
              })}
          </Card>
          {releaseFlow.runs.filter((run) => !isActiveRunStatus(runEffectiveStatus(run))).length > 0 && (
            <Card pad={0}>
              <div style={{ padding: "10px 14px", borderBottom: `1px solid ${UI.line2}`, background: UI.bg2, fontSize: TYPE.caption, fontWeight: 700, color: UI.ink2 }}>런 이력</div>
              <THead cols={releaseRunCols} />
              {releaseFlow.runs.filter((run) => !isActiveRunStatus(runEffectiveStatus(run))).slice(0, 10).map((run, i) => (
                <TRow key={run.run_id} cols={releaseRunCols} i={i}
                  onClick={() => setDetail({ kind: "releaseRun", runId: run.run_id })} cells={[
                  <span key="r"><Mono>{run.run_id}</Mono> <Mono dim>· {run.plan_name}</Mono></span>,
                  <Mono key="w">{run.current_wave}/{run.total_waves}</Mono>,
                  deliveryPill(runEffectiveStatus(run)),
                  <Mono key="t" dim>{fromNow(run.created_at ?? null)}</Mono>,
                  <Mono key="a" dim>{typeof run.started_by === "string" && run.started_by !== "" ? run.started_by : "—"}</Mono>,
                ]} />
              ))}
            </Card>
          )}
        </div>
      )}
    </Page>
    {detail !== null && (
      <DeployDetailHost target={detail} runs={workflowFeed.items}
        releasePlans={releaseFlow.plans} releaseRuns={releaseFlow.runs} releaseActions={releaseActions}
        onClose={() => {
          const closingControlledApplication =
            detail.kind === "application" && detail.applicationId === applicationDetailId;
          setDetail(null);
          if (closingControlledApplication) onApplicationDetailClose?.();
        }}
        topInset={topInset} leftInset={leftInset} rightInset={rightInset} />
    )}
    </>
  );
}

// ── RCA 상세 — 실 계약(GET /api/dashboard/rca/issues 항목의 관측 RCA 필드 +
// GET /api/rca/recovery-plans/by-correlation) 파생. 원인/확신도/증거/복구 후보는
// 서버가 준 값만 렌더하고, 없으면 정직한 "관측 안 됨"으로 둔다(no backfill).
// 실제 복구 실행 경로(capability/CSRF)는 이 데모에 배선되어 있지 않으므로 실행
// 컨트롤은 비활성으로 두고 가짜 성공을 만들지 않는다.
const RECOVERY_STEP_LABELS = ["승인", "정책", "실행", "검증", "완료"] as const;
const ISSUE_DETAIL_TYPE = {
  sectionTitle: TYPE.section,
  itemTitle: TYPE.body,
  body: TYPE.body,
  label: TYPE.label,
} as const;

function RecoveryPlanProgress({ progress, prUrl = null }: { progress: RecoveryProgressState; prUrl?: string | null }) {
  const activeColor = progress.tone === "failed" ? HP.crit
    : progress.tone === "completed" ? HP.ok
      : progress.tone === "approval" ? HP.warn
        : BLUE;
  const displayedStep = recoveryDisplayedStep(progress);
  const progressPercent = recoveryProgressPercent(progress);
  const prReference = prUrl ? pullRequestReference(prUrl) : null;
  return (
    <section aria-live="polite" style={{ display: "grid", gap: SPACE.stack, border: `1px solid ${UI.line}`, borderRadius: RADIUS.card, background: UI.card, padding: SPACE.card, boxShadow: `0 6px 16px -10px ${inkA(0.26)}, 0 1px 3px ${inkA(0.06)}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div style={{ minWidth: 0, flex: 1, display: "grid", gap: 3 }}>
          <strong style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: ISSUE_DETAIL_TYPE.sectionTitle, fontWeight: 700, color: UI.heading }}>{progress.label}</strong>
          <span style={{ fontSize: ISSUE_DETAIL_TYPE.label, color: UI.ink3 }}>복구 진행 상태</span>
        </div>
        <span style={{ flexShrink: 0, fontSize: ISSUE_DETAIL_TYPE.itemTitle, fontVariantNumeric: "tabular-nums", color: progress.tone === "failed" ? HP.crit : UI.ink2 }}>
          {progress.tone === "failed" ? "중단" : `${displayedStep}/5`}
        </span>
      </div>
      <div aria-label="복구 진행률" style={{ height: 5, overflow: "hidden", borderRadius: 999, background: HP.pending }}>
        <motion.div
          initial={false}
          animate={{ width: `${progressPercent}%` }}
          transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1] }}
          style={{ height: "100%", borderRadius: 999, background: activeColor }}
        />
      </div>
      <ol aria-label="복구 진행 단계" style={{ display: "grid", gridTemplateColumns: "repeat(5, minmax(0, 1fr))", gap: 5, margin: 0, padding: "11px 0 0", borderTop: `1px dashed ${UI.line}`, listStyle: "none" }}>
        {RECOVERY_STEP_LABELS.map((label, index) => {
          const completed = progress.phase === "completed" || index < progress.step;
          const active = progress.phase !== "waiting" && index === Math.min(progress.step, 4);
          const markerColor = active ? activeColor : completed ? HP.ok : HP.pending;
          return (
            <li key={label} style={{ minWidth: 0, display: "grid", justifyItems: "center", gap: 5, textAlign: "center" }}>
              <motion.span aria-hidden="true" initial={false} animate={{ scale: active ? 1.06 : 1 }} transition={{ duration: DUR.micro }}
                style={{ width: 22, height: 22, display: "grid", placeItems: "center", borderRadius: 6, border: `1px solid ${active ? markerColor : UI.line}`, background: active ? markerColor : completed ? TINT.ok.bg : UI.card, color: active ? UI.card : completed ? TINT.ok.fg : UI.ink3, fontSize: TYPE.caption, fontWeight: 600 }}>
                {completed ? <Check size={12} /> : index + 1}
              </motion.span>
              <span title={label} style={{ width: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: TYPE.caption, color: active ? UI.ink : UI.ink3, fontWeight: active ? 600 : 500 }}>{label}</span>
            </li>
          );
        })}
      </ol>
      {progress.latestEvent && (
        <span title={progress.latestEvent.subject} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: TYPE.caption, color: UI.ink3 }}>
          최근 기록 · {koLabel(progress.latestEvent.subject)} · {fromNow(progress.latestEvent.created_at)}
        </span>
      )}
      {prUrl && prReference && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap", paddingTop: 10, borderTop: `1px dashed ${UI.line}` }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: UI.ink2, fontSize: TYPE.label }}>
            <CircleCheck size={14} color={HP.ok} />
            {progress.phase === "completed" ? "복구에 사용된 PR" : "복구 PR 생성 완료"}
          </span>
          <a
            className="product-focusable"
            href={prUrl}
            rel="noopener noreferrer"
            target="_blank"
            title={`${prReference.label} 열기`}
            style={{ width: "fit-content", display: "inline-flex", alignItems: "center", gap: 5, color: BLUE, fontSize: TYPE.label, fontWeight: 600, textDecoration: "none" }}
          >
            {prReference.label} <ExternalLink size={13} />
          </a>
        </div>
      )}
    </section>
  );
}

const RECOVERY_CHECK_LABELS: Record<string, string> = {
  failure_ratio_below_threshold: "접속 실패율 정상화",
  request_rate_near_baseline: "장애 전과 동일한 요청 부하",
  desired_replicas_restored: "승인된 replica 수 복원",
  ready_replicas_restored: "복구 Pod Ready",
  updated_replicas_restored: "복구 버전 반영",
  available_replicas_restored: "복구 Pod 가용",
  unavailable_replicas_zero: "비가용 Pod 없음",
  deployment_generation_observed: "Deployment 최신 세대 반영",
  protected_workloads_present: "기존 실행 workload 유지",
  protected_workloads_healthy: "기존 실행 workload 정상",
  protected_workloads_uninterrupted: "기존 실행 workload 무중단",
  protected_active_session_series_present: "활성 세션 근거 연속 수집",
  protected_active_sessions_maintained: "활성 세션 수 무중단 유지",
  alertmanager_resolved: "원래 알림 해소",
  alertmanager_no_refire: "검증 중 알림 재발 없음",
};

function recoveryLifecycleObject(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function RecoveryLifecycleEvidence({ lifecycle }: { lifecycle?: Record<string, unknown> | null }) {
  if (!lifecycle) return null;
  const verification = recoveryLifecycleObject(lifecycle.verification);
  const before = recoveryLifecycleObject(verification.before);
  const after = recoveryLifecycleObject(verification.after);
  const deployment = recoveryLifecycleObject(after.deployment);
  const checks = recoveryLifecycleObject(after.checks);
  const failure = recoveryLifecycleObject(lifecycle.failure);
  const reason = typeof failure.reason === "string" && failure.reason.trim()
    ? failure.reason
    : typeof verification.last_reason === "string" && verification.last_reason.trim()
      ? verification.last_reason
      : null;
  const checkItems = Object.entries(checks)
    .filter(([, value]) => typeof value === "boolean" || value === null)
    .map(([key, value]) => ({
      key,
      label: RECOVERY_CHECK_LABELS[key] ?? key,
      value,
    }));
  const beforeFailure = typeof before.failure_ratio === "number" ? before.failure_ratio : null;
  const afterFailure = typeof after.failure_ratio === "number" ? after.failure_ratio : null;
  const beforeRate = typeof before.request_rate === "number" ? before.request_rate : null;
  const afterRate = typeof after.request_rate === "number" ? after.request_rate : null;
  const readyReplicas = typeof deployment.ready_replicas === "number" ? deployment.ready_replicas : null;
  const protectedWorkloads = Array.isArray(after.protected_workloads)
    ? after.protected_workloads.length
    : null;
  const activeSessionCount = Array.isArray(after.protected_active_sessions)
    ? after.protected_active_sessions.reduce((total, item) => {
        const value = recoveryLifecycleObject(item).value;
        return total + (typeof value === "number" ? value : 0);
      }, 0)
    : null;
  return (
    <section aria-label="복구 안정화 검증" style={{ display: "grid", gap: 11, border: `1px solid ${UI.line}`, borderRadius: RADIUS.card, background: UI.card, padding: SPACE.card }}>
      <div style={{ display: "grid", gap: 3 }}>
        <strong style={{ fontSize: ISSUE_DETAIL_TYPE.sectionTitle, color: UI.heading }}>복구 안정화 검증</strong>
        <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>서버에 저장된 배포 후 before/after 근거입니다.</span>
      </div>
      {reason && (
        <div role={lifecycle.phase === "failed" ? "alert" : undefined} style={{ borderRadius: 8, background: lifecycle.phase === "failed" ? TINT.crit.bg : UI.bg2, color: lifecycle.phase === "failed" ? TINT.crit.fg : UI.ink2, padding: 10, fontSize: TYPE.caption, lineHeight: 1.5 }}>
          {reason}
        </div>
      )}
      <dl style={{ display: "grid", gridTemplateColumns: "108px minmax(0, 1fr)", gap: "7px 10px", margin: 0, fontSize: TYPE.caption }}>
        {(beforeFailure !== null || afterFailure !== null) && <><dt style={{ color: UI.ink3 }}>접속 실패율</dt><dd style={{ margin: 0, color: UI.ink2 }}>{beforeFailure === null ? "미확인" : `${(beforeFailure * 100).toFixed(1)}%`} → {afterFailure === null ? "검증 대기" : `${(afterFailure * 100).toFixed(1)}%`}</dd></>}
        {(beforeRate !== null || afterRate !== null) && <><dt style={{ color: UI.ink3 }}>동일 부하 확인</dt><dd style={{ margin: 0, color: UI.ink2 }}>{beforeRate === null ? "미확인" : beforeRate.toFixed(1)} → {afterRate === null ? "검증 대기" : afterRate.toFixed(1)} req/s</dd></>}
        {readyReplicas !== null && <><dt style={{ color: UI.ink3 }}>Ready replica</dt><dd style={{ margin: 0, color: UI.ink2 }}>{readyReplicas}</dd></>}
        {protectedWorkloads !== null && <><dt style={{ color: UI.ink3 }}>기존 workload</dt><dd style={{ margin: 0, color: UI.ink2 }}>{protectedWorkloads}개 연속 관측</dd></>}
        {activeSessionCount !== null && <><dt style={{ color: UI.ink3 }}>활성 세션</dt><dd style={{ margin: 0, color: UI.ink2 }}>{activeSessionCount}개 이상 유지</dd></>}
      </dl>
      {checkItems.length > 0 && (
        <ul style={{ display: "grid", gap: 6, margin: 0, padding: 0, listStyle: "none" }}>
          {checkItems.map((item) => (
            <li key={item.key} style={{ display: "flex", alignItems: "center", gap: 7, fontSize: TYPE.caption, color: item.value === false ? TINT.crit.fg : item.value === true ? TINT.ok.fg : UI.ink3 }}>
              {item.value === true ? <CircleCheck size={13} /> : item.value === false ? <CircleAlert size={13} /> : <Clock size={13} />}
              <span>{item.label}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function RecoveryRetryControl({
  visible,
  pending,
  error,
  onRetry,
}: {
  visible: boolean;
  pending: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  if (!visible) return null;
  return (
    <section style={{ display: "grid", gap: 9, border: `1px solid ${TINT.warn.bd}`, borderRadius: RADIUS.card, background: TINT.warn.bg, padding: SPACE.card }}>
      <div style={{ display: "grid", gap: 3 }}>
        <strong style={{ fontSize: ISSUE_DETAIL_TYPE.itemTitle, color: TINT.warn.fg }}>실패한 단계 다시 시도</strong>
        <span style={{ fontSize: TYPE.caption, color: UI.ink2, lineHeight: 1.5 }}>서버가 저장한 현재 시도를 기준으로 PR 생성·배포·검증 중 실패한 단계만 안전하게 다시 실행합니다.</span>
      </div>
      {error && <div role="alert" style={{ fontSize: TYPE.caption, color: TINT.crit.fg, lineHeight: 1.5 }}>{error}</div>}
      <button
        type="button"
        className="product-focusable product-control"
        disabled={pending}
        onClick={onRetry}
        style={{ justifySelf: "end", display: "inline-flex", alignItems: "center", gap: 6, border: `1px solid ${TINT.warn.bd}`, borderRadius: 8, background: UI.card, color: pending ? UI.ink3 : TINT.warn.fg, padding: "7px 11px", fontSize: TYPE.caption, fontWeight: 600, cursor: pending ? "wait" : "pointer" }}
      >
        <RefreshCw size={13} className={pending ? "spin" : undefined} />
        {pending ? "재시도 접수 중…" : "실패 단계 재시도"}
      </button>
    </section>
  );
}

function RecoveryCandidateDetails({
  candidate,
  recommended = false,
  selected,
  pending,
  onSelect,
  onOpenTarget,
  showAction = true,
}: {
  candidate: RecoveryActionCandidate;
  recommended?: boolean;
  selected: boolean;
  pending: boolean;
  onSelect: () => void;
  onOpenTarget?: (() => void) | null;
  showAction?: boolean;
}) {
  const blocked = candidate.executable === false;
  return (
    <div style={{ display: "grid", gap: 16 }}>
      {blocked && (
        <div role="status" style={{ display: "flex", alignItems: "flex-start", gap: 7, border: `1px solid ${TINT.warn.bd}`, borderRadius: 8, background: TINT.warn.bg, color: TINT.warn.fg, padding: 11, fontSize: TYPE.caption, lineHeight: 1.45 }}>
          <CircleAlert size={14} style={{ flexShrink: 0, marginTop: 1 }} />
          {candidate.blocked_reason || "현재 클러스터 제어 정책에서는 이 복구를 실행할 수 없습니다."}
        </div>
      )}
      <dl style={{ display: "grid", gridTemplateColumns: "72px minmax(0, 1fr)", alignItems: "center", gap: "8px 10px", margin: 0, paddingBottom: 14 }}>
        <dt style={{ fontSize: TYPE.caption, color: UI.ink2 }}>조치 위험도</dt>
        <dd style={{ margin: 0, fontSize: TYPE.caption, lineHeight: 1.5, color: candidate.risk_level ? UI.ink : UI.ink3 }}>{candidate.risk_level || "미확인"}</dd>
        <dt style={{ fontSize: TYPE.caption, color: UI.ink2 }}>영향 범위</dt>
        <dd style={{ minWidth: 0, maxWidth: "100%", margin: 0 }}>
          {onOpenTarget ? (
            <button
              type="button"
              className="product-focusable product-control"
              title={`${candidate.blast_radius || "대상 리소스"} 상세 열기`}
              onClick={onOpenTarget}
              style={{ maxWidth: "100%", border: "none", borderRadius: 7, background: TINT.gray.bg, color: UI.ink2, padding: "2px 7px", fontSize: TYPE.caption, fontWeight: 400, lineHeight: 1.35, cursor: "pointer", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", transition: `background ${DUR.micro}s ease, color ${DUR.micro}s ease` }}
              onMouseEnter={(event) => { event.currentTarget.style.background = TINT.gray.bd; event.currentTarget.style.color = UI.ink; }}
              onMouseLeave={(event) => { event.currentTarget.style.background = TINT.gray.bg; event.currentTarget.style.color = UI.ink2; }}
              onFocus={(event) => { event.currentTarget.style.background = TINT.gray.bd; event.currentTarget.style.color = UI.ink; }}
              onBlur={(event) => { event.currentTarget.style.background = TINT.gray.bg; event.currentTarget.style.color = UI.ink2; }}
            >
              {candidate.blast_radius || "미확인"}
            </button>
          ) : (
            <span title={candidate.blast_radius} style={{ display: "block", width: "fit-content", maxWidth: "100%", borderRadius: 7, background: TINT.gray.bg, color: candidate.blast_radius ? UI.ink2 : UI.ink3, padding: "2px 7px", fontSize: TYPE.caption, lineHeight: 1.35, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{candidate.blast_radius || "미확인"}</span>
          )}
        </dd>
      </dl>
      {candidate.recommendation_reason && <RecoveryCandidateSection title="추천 이유" text={candidate.recommendation_reason} />}
      {candidate.risk_explanation && <RecoveryCandidateSection title="위험도 설명" text={candidate.risk_explanation} />}
      <RecoveryCandidateSection title="실행할 조치" text={candidate.description || "조치 설명이 없습니다."} />
      {recommended
        ? <RecommendedRecoveryChecks candidate={candidate} />
        : <RecoveryCandidateSupplement candidate={candidate} />}
      {showAction && <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 7, paddingTop: 2 }}>
        <button
          type="button"
          className={selected ? undefined : "product-focusable product-action"}
          disabled={selected || pending || blocked}
          onClick={onSelect}
          style={{ border: selected ? `1px solid ${TINT.ok.bd}` : "none", borderRadius: 8, background: selected ? TINT.ok.bg : pending || blocked ? UI.bg2 : BLUE, color: selected ? TINT.ok.fg : pending || blocked ? UI.ink3 : UI.card, padding: "7px 13px", fontSize: TYPE.label, fontWeight: 600, cursor: selected || pending || blocked ? "not-allowed" : "pointer", boxShadow: selected || pending || blocked ? "none" : `0 2px 6px ${blueA(0.2)}` }}
        >
          {selected ? "선택됨" : pending ? "선택 중…" : blocked ? "권한 정책상 실행 불가" : "검토하기"}
        </button>
      </div>}
    </div>
  );
}

function recoveryActionModeSummary(route: string): string {
  if (route === "auto") return "자동 복구 요청을 바로 제출합니다.";
  if (isSafePrRoute(route)) return "복구 PR 생성을 바로 요청합니다.";
  return "선택한 복구 절차로 바로 진행합니다.";
}

function recoveryActionRouteLabel(route: string): string {
  return recoveryRouteLabel(route);
}

function recoveryActionRouteSentence(route: string): string {
  if (route === "auto") return "안전 조건을 충족하면 자동 복구로 실행됩니다.";
  if (isSafePrRoute(route)) return "GitOps 변경 PR을 생성한 뒤 검토와 병합을 거쳐 적용됩니다.";
  return "정해진 복구 절차에 따라 실행됩니다.";
}

function recoveryAiPrompt({
  candidate,
  cluster,
  namespace,
  resourceKind,
  resourceName,
  symptom,
  rootCause,
}: {
  candidate: RecoveryActionCandidate;
  cluster: string;
  namespace: string;
  resourceKind: string;
  resourceName: string;
  symptom: string;
  rootCause: string | null | undefined;
}): string {
  const lines = [
    "🔎 다음 복구 플랜을 현재 운영 근거에 따라 검토해 주세요.",
    "",
    "🚨 장애 정보",
    `- 대상: ${cluster} / ${namespace} / ${resourceKind} / ${resourceName}`,
    `- 증상: ${symptom}`,
    `- 판단된 원인: ${rootCause?.trim() || "미확인"}`,
    "",
    "🛠️ 선택한 복구 조치",
    `- 조치: ${candidate.title}`,
    `- 처리 경로: ${recoveryActionRouteLabel(candidate.route)}`,
    `- 처리 방식: ${recoveryActionRouteSentence(candidate.route)}`,
    `- 위험도: ${candidate.risk_level || "미확인"}`,
    `- 영향 범위: ${candidate.blast_radius || "미확인"}`,
    `- 조치 내용: ${candidate.description || "미확인"}`,
  ];
  if (candidate.recommendation_reason) lines.push(`- 추천 이유: ${candidate.recommendation_reason}`);
  lines.push("", "✅ 안전 조건");
  if (candidate.validation_checks.length > 0) {
    candidate.validation_checks.slice(0, 4).forEach((check) => lines.push(`- 성공 조건: ${check}`));
  } else {
    lines.push("- 성공 조건: 미확인");
  }
  lines.push(
    `- 실패 시 복원: ${candidate.rollback_plan || "미확인"}`,
    "",
    "📋 검토 요청",
    "1. 실행 전 추가로 확인할 사항",
    "2. 예상 영향과 중단 기준",
    "3. 성공 여부를 확인할 방법",
    "4. 운영자 판단이 필요한 사항",
    "",
    "확인되지 않은 사실은 추정하지 말고, AI는 검토만 수행하며 복구 조치를 직접 실행하지 마세요.",
  );
  return lines.join("\n");
}

function recoveryAiDisplayPrompt({
  candidate,
  resourceKind,
  resourceName,
  symptom,
  rootCause,
}: {
  candidate: RecoveryActionCandidate;
  resourceKind: string;
  resourceName: string;
  symptom: string;
  rootCause: string | null | undefined;
}): string {
  const target = `${resourceKind} ${resourceName}`;
  const cause = rootCause?.trim() || "아직 확정되지 않은 원인";
  const actionDescription = candidate.description?.trim()
    || `${candidate.title} 조치를 적용합니다.`;
  const impact = candidate.blast_radius?.trim() || "확인되지 않은 범위";
  const risk = candidate.risk_level?.trim() || "미확인";

  return [
    "🔎 복구 플랜 검토",
    "",
    `현재 ${target}에서 ${symptom} 증상이 발생했습니다. 수집된 운영 근거를 종합한 원인은 ${cause}입니다.`,
    "",
    "🛠️ 선택한 복구 조치",
    `${candidate.title}을 선택했습니다. ${actionDescription}`,
    `영향 범위는 ${impact}, 조치 위험도는 ${risk}입니다. ${recoveryActionRouteSentence(candidate.route)}`,
    "",
    "✅ 확인해 주세요",
    "- 현재 상태에서 안전하게 실행할 수 있는지",
    "- 성공 조건과 작업 중단 기준이 충분한지",
    "- 실패 시 복원 계획에 빠진 내용은 없는지",
    "",
    "확인되지 않은 내용은 추정하지 말고, 추가 확인이 필요한 항목을 알려주세요.",
  ].join("\n");
}

function RecommendedRecoveryChecks({ candidate }: { candidate: RecoveryActionCandidate }) {
  return (
    <>
      {candidate.validation_checks.length > 0 && <RecoveryCandidateList title="성공 조건" items={candidate.validation_checks} />}
      <RecoveryRollbackSection candidate={candidate} />
    </>
  );
}

function RecoveryCandidateSupplement({ candidate }: { candidate: RecoveryActionCandidate }) {
  return (
    <>
      {candidate.expected_outcome && <RecoveryCandidateSection title="기대 효과" text={candidate.expected_outcome} />}
      {candidate.validation_checks.length > 0 && <RecoveryCandidateList title="성공 조건" items={candidate.validation_checks} />}
      <RecoveryRollbackSection candidate={candidate} />
    </>
  );
}

function RecoveryRollbackSection({ candidate }: { candidate: RecoveryActionCandidate }) {
  if (!candidate.rollback_plan) return null;
  return (
    <section style={{ display: "grid", gap: 7, paddingTop: 14, borderTop: `1px dashed ${UI.line}` }}>
      <h4 style={{ margin: 0, fontSize: ISSUE_DETAIL_TYPE.itemTitle, fontWeight: 600, color: UI.heading }}>실패 시 복원</h4>
      {candidate.rollback_reason && (
        <div style={{ display: "grid", gridTemplateColumns: "64px minmax(0, 1fr)", gap: 8, alignItems: "start" }}>
          <span style={{ fontSize: TYPE.caption, lineHeight: 1.55, color: UI.ink3 }}>복원 조건</span>
          <p style={{ margin: 0, fontSize: ISSUE_DETAIL_TYPE.body, lineHeight: 1.55, color: UI.ink2 }}>{candidate.rollback_reason}</p>
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "64px minmax(0, 1fr)", gap: 8, alignItems: "start" }}>
        <span style={{ fontSize: TYPE.caption, lineHeight: 1.55, color: UI.ink3 }}>복원 방법</span>
        <p style={{ margin: 0, fontSize: ISSUE_DETAIL_TYPE.body, lineHeight: 1.55, color: UI.ink2 }}>{candidate.rollback_plan}</p>
      </div>
    </section>
  );
}

function RecoveryCandidateSection({ title, text }: { title: string; text: string }) {
  return (
    <section style={{ display: "grid", gap: 7, paddingTop: 14, borderTop: `1px dashed ${UI.line}` }}>
      <h4 style={{ margin: 0, fontSize: ISSUE_DETAIL_TYPE.itemTitle, fontWeight: 600, color: UI.heading }}>{title}</h4>
      <p style={{ margin: 0, fontSize: ISSUE_DETAIL_TYPE.body, lineHeight: 1.6, color: UI.ink2 }}>{text}</p>
    </section>
  );
}

function RecoveryCandidateList({ title, items, divider = true }: { title: string; items: readonly string[]; divider?: boolean }) {
  return (
    <section style={{ display: "grid", gap: 7, paddingTop: divider ? 14 : 0, borderTop: divider ? `1px dashed ${UI.line}` : "none" }}>
      <h4 style={{ margin: 0, fontSize: ISSUE_DETAIL_TYPE.itemTitle, fontWeight: 600, color: UI.heading }}>{title}</h4>
      <ul style={{ display: "grid", gap: 5, margin: 0, padding: 0, listStyle: "none" }}>
        {items.map((item, index) => <li key={`${item}-${index}`} style={{ display: "grid", gridTemplateColumns: "14px minmax(0, 1fr)", gap: 5, fontSize: ISSUE_DETAIL_TYPE.body, lineHeight: 1.5, color: UI.ink2 }}><Check size={12} style={{ marginTop: 2, color: TINT.ok.fg }} /><span>{item}</span></li>)}
      </ul>
    </section>
  );
}

function RecoveryAlternativeCandidate({
  candidate,
  selected,
  pending,
  onSelect,
  onOpenTarget,
}: {
  candidate: RecoveryActionCandidate;
  selected: boolean;
  pending: boolean;
  onSelect: () => void;
  onOpenTarget?: (() => void) | null;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ borderTop: `1px dashed ${UI.line}`, background: open ? UI.bg2 : UI.card }}>
      <button type="button" className="product-focusable product-control" aria-expanded={open} onClick={() => setOpen((current) => !current)}
        style={{ width: "100%", minWidth: 0, display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto auto", alignItems: "center", gap: 10, border: "none", background: "transparent", padding: 12, textAlign: "left", cursor: "pointer" }}>
        <span style={{ minWidth: 0, display: "flex", alignItems: "center", gap: 7 }}>
          <Lightbulb size={14} style={{ flexShrink: 0, color: UI.ink3 }} />
          <strong title={candidate.title} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: ISSUE_DETAIL_TYPE.itemTitle, fontWeight: 600, color: UI.heading }}>{candidate.title}</strong>
        </span>
        <span style={{ fontSize: TYPE.caption, color: UI.ink2, fontVariantNumeric: "tabular-nums" }}>{Math.round(candidate.score * 100)}%</span>
        <Sparkle size={14} style={{ color: UI.ink3, transform: open ? "rotate(45deg)" : "none", transition: `transform ${DUR.micro}s ease` }} />
      </button>
      {open && <div style={{ padding: "0 12px 14px" }}><RecoveryCandidateDetails candidate={candidate} selected={selected} pending={pending} onSelect={onSelect} onOpenTarget={onOpenTarget} /></div>}
    </div>
  );
}

function RecoveryPlanPanel({
  plan,
  selectedActionId,
  pendingActionId,
  selectionError,
  onSelect,
  onOpenTarget,
}: {
  plan: RecoveryPlan;
  selectedActionId: string | null;
  pendingActionId: string | null;
  selectionError: string | null;
  onSelect: (actionId: string) => void;
  onOpenTarget?: (() => void) | null;
}) {
  const recommended = plan.candidates.find((candidate) => candidate.action_id === plan.recommended_action_id) ?? plan.candidates[0] ?? null;
  const alternatives = plan.candidates.filter((candidate) => candidate.action_id !== recommended?.action_id);
  return (
    <div style={{ display: "grid", gap: 16 }}>
      {selectionError && <div role="alert" style={{ display: "flex", alignItems: "flex-start", gap: 7, border: `1px solid ${TINT.crit.bd}`, borderRadius: 8, background: TINT.crit.bg, color: TINT.crit.fg, padding: 11, fontSize: TYPE.caption, lineHeight: 1.45 }}><CircleAlert size={14} style={{ flexShrink: 0, marginTop: 1 }} />{selectionError}</div>}
      {recommended ? (
        <RcaCardSection title="권장 복구 조치">
          <div style={{ display: "grid", gap: 13, padding: 15 }}>
            <div style={{ minWidth: 0, display: "flex", alignItems: "center", gap: 8 }}>
              <Lightbulb size={15} style={{ flexShrink: 0, color: UI.ink3 }} />
              <strong title={recommended.title} style={{ minWidth: 0, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: ISSUE_DETAIL_TYPE.itemTitle, color: UI.heading }}>{recommended.title}</strong>
              <span style={{ border: `1px solid ${TINT.blue.bd}`, borderRadius: 999, background: TINT.blue.bg, color: TINT.blue.fg, padding: "2px 7px", fontSize: TYPE.caption, fontWeight: 600 }}>권장</span>
              <span style={{ fontSize: ISSUE_DETAIL_TYPE.label, color: UI.ink2, fontVariantNumeric: "tabular-nums" }}>{Math.round(recommended.score * 100)}%</span>
            </div>
            <RecoveryCandidateDetails
              candidate={recommended}
              recommended
              selected={selectedActionId === recommended.action_id}
              pending={pendingActionId === recommended.action_id}
              onSelect={() => onSelect(recommended.action_id)}
              onOpenTarget={onOpenTarget}
            />
          </div>
        </RcaCardSection>
      ) : <div style={{ fontSize: TYPE.label, color: UI.ink3 }}>관측된 복구 후보 없음</div>}
      {alternatives.length > 0 && (
        <section style={{ display: "grid", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
            <h3 style={{ margin: 0, fontSize: ISSUE_DETAIL_TYPE.itemTitle, fontWeight: 600, color: UI.heading }}>다른 복구 후보</h3>
            <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>{alternatives.length}개</span>
          </div>
          <div style={{ overflow: "hidden", borderBottom: `1px dashed ${UI.line}` }}>
            {alternatives.map((candidate) => (
              <RecoveryAlternativeCandidate
                key={candidate.action_id}
                candidate={candidate}
                selected={selectedActionId === candidate.action_id}
                pending={pendingActionId === candidate.action_id}
                onSelect={() => onSelect(candidate.action_id)}
                onOpenTarget={onOpenTarget}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function recoveryDraftLabel(key: string): string {
  const labels: Record<string, string> = {
    secret_name: "Secret",
    restore_version: "복원 버전",
    strategy: "적용 방식",
    max_unavailable: "최대 중단 Pod",
    repository: "저장소",
    base_branch: "기준 브랜치",
    image: "컨테이너 이미지",
    replicas: "복제본",
    patch: "변경 패치",
    manifest: "매니페스트",
  };
  return labels[key] ?? key.split("_").join(" ");
}

function recoveryDraftValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "미지정";
  if (typeof value === "boolean") return value ? "예" : "아니요";
  if (value === "previous") return "이전 정상 버전";
  if (value === "rolling") return "순차 적용";
  if (typeof value === "string" || typeof value === "number") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return "표시할 수 없는 값";
  }
}

function recoveryDraftActionLabel(actionType: string): string {
  if (actionType === "restore_secret") return "Secret 이전 버전 복원";
  if (actionType === "restart_workload") return "워크로드 순차 재시작";
  if (actionType === "create_safe_pr") return "GitOps 복구 PR 생성";
  return actionType;
}

function recoveryAiPreview(
  candidate: RecoveryActionCandidate,
  draft: RemediationBundleActionDraft | null,
): AiRecoveryPreview | undefined {
  const patch = [draft?.params.patch, draft?.params.manifest, draft?.params.diff]
    .find((value): value is string => typeof value === "string" && value.trim() !== "");
  if (!draft || patch === undefined) return undefined;

  const lines: AiRecoveryPreviewLine[] = patch.split(/\r?\n/u).map((content) => {
    if (content.startsWith("+") && !content.startsWith("+++")) {
      return { kind: "add", content: content.slice(1) };
    }
    if (content.startsWith("-") && !content.startsWith("---")) {
      return { kind: "remove", content: content.slice(1) };
    }
    return { kind: "context", content };
  });
  const resourceKind = draft.resource_kind || "리소스";
  const resourceName = draft.resource_name || candidate.blast_radius || "대상";
  return {
    title: recoveryDraftActionLabel(draft.action_type),
    fileName: `${draft.namespace || "namespace"}/${resourceKind.toLowerCase()}/${resourceName}`,
    lines,
  };
}

function RecoveryDraftPreview({
  draft,
  status,
}: {
  draft: RemediationBundleActionDraft | null;
  status: "idle" | "loading" | "ready" | "unavailable";
}) {
  const params = draft ? Object.entries(draft.params) : [];
  const visibleParams = params.filter(([key]) => key !== "secret_name");
  const secretName = draft?.params.secret_name;
  return (
    <RcaCardSection title="변경사항 미리보기">
      <div style={{ display: "grid", gap: 14, padding: 15 }}>
        {status === "loading" ? (
          <span style={{ fontSize: ISSUE_DETAIL_TYPE.body, color: UI.ink3 }}>변경 초안을 불러오는 중…</span>
        ) : draft ? (
          <>
            <dl style={{ display: "grid", gridTemplateColumns: "82px minmax(0, 1fr)", gap: "8px 10px", margin: 0 }}>
              <dt style={{ fontSize: TYPE.caption, color: UI.ink3 }}>조치 유형</dt>
              <dd style={{ margin: 0, fontSize: TYPE.caption, color: UI.ink2 }}>{recoveryDraftActionLabel(draft.action_type)}</dd>
              <dt style={{ fontSize: TYPE.caption, color: UI.ink3 }}>대상</dt>
              <dd style={{ minWidth: 0, margin: 0, fontSize: TYPE.caption, color: UI.ink2 }}>
                {typeof secretName === "string" ? `Secret · ${secretName}` : `${draft.namespace} · ${draft.resource_kind} · ${draft.resource_name}`}
              </dd>
              <dt style={{ fontSize: TYPE.caption, color: UI.ink3 }}>사전 검증</dt>
              <dd style={{ margin: 0, fontSize: TYPE.caption, color: UI.ink2 }}>{draft.dry_run ? "Dry run 적용" : "실행 경로에서 검증"}</dd>
            </dl>
            {visibleParams.length > 0 ? (
              <div style={{ display: "grid", gap: 8, paddingTop: 4 }}>
                {visibleParams.map(([key, value], index) => {
                  const formatted = recoveryDraftValue(value);
                  return (
                    <motion.div
                      key={key}
                      initial={{ opacity: 0, x: 10 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: Math.min(index * 0.05, 0.2), duration: 0.2 }}
                      style={{ display: "grid", gridTemplateColumns: "96px minmax(0, 1fr)", gap: 12, alignItems: "center", minHeight: 42, padding: "9px 11px", borderRadius: 8, background: UI.bg2 }}
                    >
                      <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>{recoveryDraftLabel(key)}</span>
                      <div style={{ minWidth: 0, display: "grid", gridTemplateColumns: "minmax(0, auto) 16px minmax(0, 1fr)", alignItems: "center", gap: 7 }}>
                        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: TYPE.caption, color: UI.ink3 }}>
                          {key === "restore_version" ? "현재 적용 버전" : "현재 설정"}
                        </span>
                        <ArrowRight size={13} style={{ color: UI.ink3 }} />
                        <code style={{ minWidth: 0, overflowWrap: "anywhere", whiteSpace: formatted.includes("\n") ? "pre-wrap" : "normal", fontFamily: MONO, fontSize: TYPE.caption, lineHeight: 1.5, color: UI.ink }}>{formatted}</code>
                      </div>
                    </motion.div>
                  );
                })}
              </div>
            ) : (
              <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>이 조치에는 별도 매니페스트 파라미터가 없습니다.</span>
            )}
          </>
        ) : (
          <span style={{ fontSize: ISSUE_DETAIL_TYPE.body, lineHeight: 1.55, color: UI.ink3 }}>
            서버에서 변경 초안을 제공하지 않아 매니페스트 변경사항을 표시할 수 없습니다.
          </span>
        )}
      </div>
    </RcaCardSection>
  );
}

function RecoveryReveal({ index, children }: { index: number; children: React.ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.045, duration: 0.2, ease: "easeOut" }}
    >
      {children}
    </motion.div>
  );
}

function RecoveryConfirmation({
  candidate,
  draft,
  bundleStatus,
  progress,
  lifecycle,
  prUrl,
  retryPending,
  retryError,
  pending,
  selected,
  onBack,
  onConfirm,
  onRetry,
  onAskAi,
  onOpenTarget,
}: {
  candidate: RecoveryActionCandidate;
  draft: RemediationBundleActionDraft | null;
  bundleStatus: "idle" | "loading" | "ready" | "unavailable";
  progress: RecoveryProgressState;
  lifecycle?: Record<string, unknown> | null;
  prUrl?: string | null;
  retryPending: boolean;
  retryError: string | null;
  pending: boolean;
  selected: boolean;
  onBack: () => void;
  onConfirm: () => void;
  onRetry: () => void;
  onAskAi: () => void;
  onOpenTarget?: (() => void) | null;
}) {
  const reviewEnabled = canStartRecoveryReview({ selected, pending });
  return (
    <motion.div
      key="recovery-confirmation"
      initial={{ opacity: 0, x: 32 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 24 }}
      transition={{ duration: DUR.fade, ease: "easeOut" }}
      style={{ display: "grid", gap: 16 }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button type="button" className="product-focusable product-control" aria-label="복구 후보로 돌아가기" onClick={onBack}
          style={{ width: 30, height: 30, display: "grid", placeItems: "center", flexShrink: 0, border: `1px solid ${UI.line}`, borderRadius: 8, background: UI.card, color: UI.ink2, padding: 0, cursor: "pointer" }}>
          <ArrowLeft size={15} />
        </button>
        <div style={{ minWidth: 0, display: "grid", gap: 2 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
            <h2 style={{ margin: 0, fontSize: ISSUE_DETAIL_TYPE.sectionTitle, fontWeight: 700, color: UI.heading }}>복구 조치 최종 확인</h2>
            <span style={{ border: `1px solid ${TINT.blue.bd}`, borderRadius: 999, background: TINT.blue.bg, color: TINT.blue.fg, padding: "2px 7px", fontSize: TYPE.caption, fontWeight: 600 }}>2단계</span>
          </div>
        </div>
      </div>

      <RecoveryReveal index={0}><RecoveryPlanProgress progress={progress} prUrl={prUrl} /></RecoveryReveal>
      <RecoveryReveal index={1}><RecoveryLifecycleEvidence lifecycle={lifecycle} /></RecoveryReveal>
      <RecoveryReveal index={2}>
        <RecoveryRetryControl
          visible={progress.phase === "failed"}
          pending={retryPending}
          error={retryError}
          onRetry={onRetry}
        />
      </RecoveryReveal>

      <RecoveryReveal index={3}><RcaCardSection title="선택한 복구 조치">
        <div style={{ display: "grid", gap: 14, padding: 15 }}>
          <div style={{ minWidth: 0, display: "flex", alignItems: "center", gap: 8 }}>
            <Lightbulb size={15} style={{ flexShrink: 0, color: UI.ink3 }} />
            <strong style={{ minWidth: 0, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: ISSUE_DETAIL_TYPE.itemTitle, color: UI.heading }}>{candidate.title}</strong>
            <span style={{ fontSize: TYPE.caption, color: UI.ink2, fontVariantNumeric: "tabular-nums" }}>{Math.round(candidate.score * 100)}%</span>
          </div>
          <p style={{ margin: 0, fontSize: ISSUE_DETAIL_TYPE.body, lineHeight: 1.6, color: UI.ink2 }}>{candidate.description || "조치 설명이 없습니다."}</p>
          <dl style={{ display: "grid", gridTemplateColumns: "72px minmax(0, 1fr)", gap: "8px 10px", margin: 0, paddingTop: 12, borderTop: `1px dashed ${UI.line}` }}>
            <dt style={{ fontSize: TYPE.caption, color: UI.ink3 }}>조치 위험도</dt>
            <dd style={{ margin: 0, fontSize: TYPE.caption, color: UI.ink2 }}>{candidate.risk_level || "미확인"}</dd>
            <dt style={{ fontSize: TYPE.caption, color: UI.ink3 }}>영향 범위</dt>
            <dd style={{ minWidth: 0, margin: 0 }}>
              {onOpenTarget ? (
                <button type="button" className="product-focusable product-control" onClick={onOpenTarget}
                  style={{ maxWidth: "100%", border: "none", borderRadius: 7, background: TINT.gray.bg, color: UI.ink2, padding: "2px 7px", fontSize: TYPE.caption, cursor: "pointer", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {candidate.blast_radius || "미확인"}
                </button>
              ) : <span style={{ fontSize: TYPE.caption, color: UI.ink2 }}>{candidate.blast_radius || "미확인"}</span>}
            </dd>
          </dl>
        </div>
      </RcaCardSection></RecoveryReveal>

      <RecoveryReveal index={2}><RecoveryDraftPreview draft={draft} status={bundleStatus} /></RecoveryReveal>

      <RecoveryReveal index={3}><RcaCardSection title="검토 정보">
        <div style={{ display: "grid", gap: 12, padding: 15 }}>
          {candidate.validation_checks.length > 0 && <RecoveryCandidateList title="성공 조건" items={candidate.validation_checks} divider={false} />}
          <RecoveryRollbackSection candidate={candidate} />
        </div>
      </RcaCardSection></RecoveryReveal>

      <RecoveryReveal index={4}><RcaCardSection title="처리 방식">
        <div style={{ display: "grid", gap: 12, padding: 15 }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 8 }}>
            <button type="button" className={selected ? undefined : "product-focusable product-control"} disabled={selected || pending} onClick={onConfirm}
              style={{ minWidth: 0, minHeight: 64, display: "grid", gap: 4, alignContent: "center", border: selected ? `1px solid ${TINT.ok.bd}` : `1px solid ${UI.line}`, borderRadius: 8, background: selected ? TINT.ok.bg : pending ? UI.bg2 : UI.card, color: selected ? TINT.ok.fg : pending ? UI.ink3 : UI.heading, padding: "9px 11px", textAlign: "left", cursor: selected || pending ? "not-allowed" : "pointer", boxShadow: "none" }}>
              <strong style={{ fontSize: TYPE.label, fontWeight: 600 }}>{selected ? "요청됨" : pending ? "요청 중…" : "직접 진행"}</strong>
              <span style={{ fontSize: TYPE.caption, fontWeight: 400, lineHeight: 1.4, color: selected ? TINT.ok.fg : UI.ink3 }}>{recoveryActionModeSummary(candidate.route)}</span>
            </button>
            <button type="button" className={reviewEnabled ? "product-focusable product-action" : undefined}
              disabled={!reviewEnabled} onClick={onAskAi}
              style={{ minWidth: 0, minHeight: 64, display: "grid", gap: 4, alignContent: "center", border: "none", borderRadius: 8, background: reviewEnabled ? BLUE : UI.bg2, color: reviewEnabled ? UI.card : UI.ink3, padding: "9px 11px", textAlign: "left", cursor: reviewEnabled ? "pointer" : "not-allowed", boxShadow: reviewEnabled ? `0 2px 6px ${blueA(0.2)}` : "none" }}>
              <strong style={{ display: "flex", alignItems: "center", gap: 5, fontSize: TYPE.label, fontWeight: 600 }}><Sparkles size={14} />{selected ? "이미 요청됨" : pending ? "요청 중…" : "AI와 진행하기"}</strong>
              <span style={{ fontSize: TYPE.caption, fontWeight: 400, lineHeight: 1.4, color: reviewEnabled ? UI.card : UI.ink3, opacity: 0.82 }}>{selected ? "실행 결과와 정책 판단을 진행 상태에서 확인해 주세요." : "AI가 안전 조건을 검토한 뒤 같은 복구 절차로 이어집니다."}</span>
            </button>
          </div>
        </div>
      </RcaCardSection></RecoveryReveal>
    </motion.div>
  );
}

function RcaCardSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ flexShrink: 0, overflow: "hidden", border: `1px solid ${UI.line}`, borderRadius: RADIUS.card, background: UI.card }}>
      <h2 style={{ margin: 0, padding: "12px 15px", borderBottom: `1px solid ${UI.line}`, background: UI.bg2, fontSize: ISSUE_DETAIL_TYPE.sectionTitle, fontWeight: 700, color: UI.heading }}>{title}</h2>
      {children}
    </section>
  );
}

function reportRiskLabel(severity: string | null | undefined): string {
  const normalized = severity?.trim().toLowerCase();
  if (normalized === "critical" || normalized === "high") return "위험";
  if (normalized === "warning" || normalized === "medium") return "보통";
  if (normalized === "info" || normalized === "low") return "낮음";
  return "미확인";
}

function reportTimeLabel(value: string | null | undefined): string {
  if (!value) return "미확인";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "미확인";
  return new Intl.DateTimeFormat("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(parsed);
}

function reportElapsedLabel(startValue: string | null | undefined, endValue: string | null | undefined): string | null {
  if (!startValue || !endValue) return null;
  const start = new Date(startValue).getTime();
  const end = new Date(endValue).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;
  const minutes = Math.floor((end - start) / 60_000);
  if (minutes < 1) return "1분 미만";
  if (minutes < 60) return `${minutes}분`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  if (hours < 24) return remainingMinutes > 0 ? `${hours}시간 ${remainingMinutes}분` : `${hours}시간`;
  const days = Math.floor(hours / 24);
  const remainingHours = hours % 24;
  return remainingHours > 0 ? `${days}일 ${remainingHours}시간` : `${days}일`;
}

function rcaDisplayLabel(value: string): string {
  return value.replace(/_/g, " ").replace(/\./g, " · ");
}

function evidenceSourceLabel(source: string): string {
  const normalized = source.trim().toLowerCase();
  if (normalized === "kubernetes" || normalized === "k8s") return "Kubernetes";
  if (normalized === "logs" || normalized === "loki") return "로그";
  if (normalized === "metrics" || normalized === "prometheus") return "메트릭";
  if (normalized === "traces" || normalized === "tempo") return "트레이스";
  return rcaDisplayLabel(source);
}

function evidenceNameLabel(name: string): string {
  const labels: Record<string, string> = {
    pod_restart_state: "Pod 재시작 상태(pod restart state)",
    database_authentication_errors: "데이터베이스 인증 오류(database authentication errors)",
    container_restart_rate: "컨테이너 재시작률(container restart rate)",
  };
  return labels[name] ?? rcaDisplayLabel(name);
}

function evidencePayloadSource(source: string): string {
  const normalized = source.trim().toLowerCase();
  if (normalized === "k8s") return "kubernetes";
  if (normalized === "loki") return "logs";
  if (normalized === "prometheus") return "metrics";
  if (normalized === "tempo") return "traces";
  return normalized;
}

type EvidenceReference = RcaReport["supporting_evidence_refs"][number];
type MissingEvidenceCheck = RcaReport["missing_evidence_checks"][number];

function evidenceReferenceLabel(value: string, references: readonly EvidenceReference[]): string {
  const evidence = references.find((reference) => evidenceReferenceMatches(value, reference));
  const pointer = parseEvidenceObjectReference(value);
  return evidence
    ? `${evidenceSourceLabel(evidence.source)} · ${evidenceNameLabel(evidence.name)}`
    : pointer
      ? `${evidenceSourceLabel(pointer.source)} · ${evidenceNameLabel(pointer.name)}`
    : rcaDisplayLabel(value.replace(":", " · "));
}

function evidenceDetailAnchor(index: number): string {
  return `rca-evidence-detail-${index}`;
}

function ReportNumberedSection({ number, title, sectionRef, children }: { number: string; title: string; sectionRef?: React.RefObject<HTMLElement | null>; children: React.ReactNode }) {
  return (
    <section ref={sectionRef} style={{ display: "grid", gridTemplateColumns: "42px minmax(0, 1fr)", gap: 12, padding: "17px 0", borderTop: `1px dashed ${UI.line}` }}>
      <span aria-hidden="true" style={{ display: "grid", gridTemplateColumns: "auto 1px", gap: 9, alignSelf: "stretch", color: UI.ink2, fontSize: TYPE.body, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
        <span>{number}</span><span style={{ width: 1, height: "100%", background: inkA(0.28) }} />
      </span>
      <div style={{ minWidth: 0, display: "grid", gap: 11 }}>
        <h3 style={{ margin: 0, fontSize: TYPE.body, fontWeight: 600, color: UI.heading }}>{title}</h3>
        {children}
      </div>
    </section>
  );
}

export function CandidateEvidenceTokens({ label, items, tone, references = [], missingChecks = [], onEvidenceSelect }: {
  label: string;
  items: readonly string[];
  tone: "ok" | "warn";
  references?: readonly EvidenceReference[];
  missingChecks?: readonly MissingEvidenceCheck[];
  onEvidenceSelect?: (item: string) => void;
}) {
  if (items.length === 0) return null;
  const entries = tone === "warn"
    ? missingEvidencePresentations(items, missingChecks).map((presentation) => ({
      item: presentation.item,
      missingPresentation: presentation,
    }))
    : items.map((item) => ({ item, missingPresentation: null }));
  return (
    <div style={{ display: "grid", gap: 4 }}>
      <span style={{ display: "flex", alignItems: "center", gap: 5, fontSize: TYPE.caption, fontWeight: 600, color: tone === "ok" ? TINT.ok.fg : TINT.warn.fg }}>
        {tone === "warn" && <ShieldAlert size={13} />}{label}
      </span>
      <span style={{ display: "grid", justifyItems: "start", gap: 3, fontSize: TYPE.caption, color: UI.ink2, lineHeight: 1.5 }}>
        {entries.map(({ item, missingPresentation }, index) => {
          const linked = tone === "ok" && references.some((reference) => evidenceReferenceMatches(item, reference));
          const content = missingPresentation?.message
            ?? evidenceReferenceLabel(item, references);
          return linked ? (
            <button key={`${item}-${index}`} className="product-focusable" type="button" title="해당 근거 상세로 이동" onClick={() => onEvidenceSelect?.(item)}
              onMouseEnter={(event) => { event.currentTarget.style.color = UI.ink; }} onMouseLeave={(event) => { event.currentTarget.style.color = UI.ink2; }}
              onFocus={(event) => { event.currentTarget.style.color = UI.ink; }} onBlur={(event) => { event.currentTarget.style.color = UI.ink2; }}
              style={{ border: "none", borderRadius: 4, background: "transparent", color: UI.ink2, padding: 0, font: "inherit", cursor: "pointer", textDecoration: "underline", textUnderlineOffset: 3, transition: `color ${DUR.micro}s ease` }}>{content}</button>
          ) : (
            <span key={`${item}-${index}`} style={{ display: "grid", gap: 1 }}>
              <span>{content}</span>
              {missingPresentation?.metadata && (
                <span style={{ fontSize: 10, color: UI.ink3 }}>{missingPresentation.metadata}</span>
              )}
            </span>
          );
        })}
      </span>
    </div>
  );
}

function EvidenceWindowPreview({ evidenceKey, source, open }: { evidenceKey: string | null; source: string; open: boolean }) {
  const payloadSource = evidencePayloadSource(source);
  const feed = useEvidenceWindowPayload(evidenceKey, payloadSource, open);
  if (!evidenceKey) return <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>원본 근거 참조가 없습니다.</span>;
  if (feed.status === "loading") return <span style={{ fontSize: TYPE.caption, color: UI.ink2 }}>근거 원문을 불러오는 중…</span>;
  if (feed.status === "unavailable") return <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: TYPE.caption, color: TINT.crit.fg }}><CircleAlert size={13} />근거 원문을 불러오지 못했습니다.</span>;
  if (feed.status !== "ready" || !feed.evidence) return null;
  const lines = evidencePreviewLines(payloadSource, feed.evidence.payload);
  return lines.length > 0 ? (
    <ul style={{ display: "grid", gap: 6, margin: 0, padding: 0, listStyle: "none" }}>
      {lines.map((line, index) => <li key={`${line}-${index}`} style={{ display: "grid", gridTemplateColumns: "10px minmax(0, 1fr)", gap: 4, borderRadius: 6, background: UI.card, padding: "7px 8px", fontFamily: source === "logs" || source === "loki" ? MONO : undefined, fontSize: TYPE.caption, color: UI.ink2, lineHeight: 1.5, overflowWrap: "anywhere" }}><span aria-hidden="true">-</span><span>{line}</span></li>)}
    </ul>
  ) : <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>표시할 근거 표본이 없습니다.</span>;
}

function EvidenceDetailItem({ evidence, id, highlighted = false, onOpenChange }: { evidence: EvidenceReference; id?: string; highlighted?: boolean; onOpenChange?: (open: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const collectedAt = reportTimeLabel(evidence.collected_at ?? evidence.window_start);
  return (
    <details id={id} open={open} onToggle={(event) => { const nextOpen = event.currentTarget.open; setOpen(nextOpen); onOpenChange?.(nextOpen); }} style={{ borderTop: `1px dashed ${UI.line}`, borderRadius: highlighted ? 8 : 0, background: highlighted ? UI.bg2 : "transparent", scrollMarginTop: 16, transition: `background ${DUR.fade}s ease` }}>
      <summary style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: 9, alignItems: "center", padding: "11px 4px", cursor: "pointer", listStyle: "none" }}>
        <span style={{ minWidth: 0, display: "grid", gap: 3 }}>
          <strong title={evidence.name} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: TYPE.label, color: UI.ink2 }}>{evidenceSourceLabel(evidence.source)} · {evidenceNameLabel(evidence.name)}</strong>
          <span style={{ fontSize: TYPE.caption, color: UI.ink2, lineHeight: 1.45 }}>{evidence.summary || "수집된 근거"}</span>
        </span>
        <ChevronRight size={15} style={{ color: UI.ink2, transform: open ? "rotate(90deg)" : "none", transition: `transform ${DUR.micro}s ease` }} />
      </summary>
      <div style={{ display: "grid", gap: 10, padding: "0 4px 12px" }}>
        <dl style={{ display: "grid", gridTemplateColumns: "68px minmax(0, 1fr)", gap: "5px 9px", margin: 0, fontSize: TYPE.caption }}>
          {evidence.query && <><dt style={{ color: UI.ink2 }}>사용 쿼리</dt><dd style={{ margin: 0, color: UI.ink, fontFamily: MONO, overflowWrap: "anywhere" }}>{evidence.query}</dd></>}
          <dt style={{ color: UI.ink2 }}>수집 시각</dt><dd style={{ margin: 0, color: UI.ink }}>{collectedAt}</dd>
        </dl>
        <EvidenceWindowPreview evidenceKey={evidence.evidence_key} source={evidence.source} open={open} />
      </div>
    </details>
  );
}

function RcaSelectedCause({ report, fallbackCause, references, onEvidenceSelect }: { report: RcaReport | null; fallbackCause: string | null | undefined; references: readonly EvidenceReference[]; onEvidenceSelect?: (item: string) => void }) {
  const candidates = report?.candidates ?? [];
  if (candidates.length === 0) {
    return <p style={{ margin: 0, fontSize: TYPE.label, color: fallbackCause ? UI.ink2 : UI.ink3, lineHeight: 1.55 }}>{fallbackCause || "원인 후보 정보가 아직 없습니다."}</p>;
  }
  const selected = candidates.find((candidate) => candidate.candidate_id === report?.selected_candidate_id) ?? candidates[0]!;
  return (
    <div style={{ display: "grid", gap: 9, borderRadius: 8, background: UI.bg2, padding: 11 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        <span title={selected.title ?? selected.candidate_id} style={{ minWidth: 0, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: TYPE.label, fontWeight: 600, color: UI.ink }}>{selected.title ?? rcaDisplayLabel(selected.candidate_id)}</span>
        <span style={{ flexShrink: 0, borderRadius: 999, border: `1px solid ${TINT.ok.bd}`, background: TINT.ok.bg, color: TINT.ok.fg, padding: "2px 7px", fontSize: TYPE.caption, fontWeight: 600 }}>권장</span>
        <span style={{ flexShrink: 0, fontSize: TYPE.caption, color: selected.score === null ? UI.ink3 : UI.ink2 }}>{selected.score === null ? "미확인" : `${Math.round(selected.score * 100)}%`}</span>
      </div>
      {selected.reason && <p style={{ margin: 0, fontSize: TYPE.caption, color: UI.ink2, lineHeight: 1.5 }}>- {selected.reason}</p>}
      <div style={{ display: "grid", gap: 9, marginTop: 2, paddingTop: 10, borderTop: `1px solid ${UI.line}` }}>
        <CandidateEvidenceTokens label="확인된 근거" items={selected.supporting_evidence} tone="ok" references={references} onEvidenceSelect={onEvidenceSelect} />
        <CandidateEvidenceTokens
          label="추가 확인 필요"
          items={selected.missing_evidence}
          tone="warn"
          missingChecks={report?.missing_evidence_checks}
        />
      </div>
    </div>
  );
}

function RcaAlternativeCandidates({ report, references, onEvidenceSelect }: { report: RcaReport | null; references: readonly EvidenceReference[]; onEvidenceSelect?: (item: string) => void }) {
  const candidates = report?.candidates ?? [];
  const selected = candidates.find((candidate) => candidate.candidate_id === report?.selected_candidate_id) ?? candidates[0];
  const alternatives = selected ? candidates.filter((candidate) => candidate.candidate_id !== selected.candidate_id) : [];
  if (alternatives.length === 0) {
    return <p style={{ margin: 0, fontSize: TYPE.label, color: UI.ink3, lineHeight: 1.55 }}>추가 원인 후보가 없습니다.</p>;
  }
  return (
    <div style={{ display: "grid", borderBottom: `1px dashed ${UI.line}` }}>
      {alternatives.map((candidate, index) => (
        <RcaAlternativeCandidate
          key={candidate.candidate_id}
          candidate={candidate}
          first={index === 0}
          references={references}
          onEvidenceSelect={onEvidenceSelect}
        />
      ))}
    </div>
  );
}

function RcaAlternativeCandidate({
  candidate,
  first,
  references,
  onEvidenceSelect,
}: {
  candidate: RcaReport["candidates"][number];
  first: boolean;
  references: readonly EvidenceReference[];
  onEvidenceSelect?: (item: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)} style={{ borderTop: first ? "none" : `1px dashed ${UI.line}`, background: open ? UI.bg2 : "transparent" }}>
      <summary style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto auto", alignItems: "center", gap: 9, padding: "11px 8px", cursor: "pointer", listStyle: "none", fontSize: TYPE.label, color: UI.ink2 }}>
        <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: 600 }}>{candidate.title ?? rcaDisplayLabel(candidate.candidate_id)}</span>
        <span>{candidate.score === null ? "미확인" : `${Math.round(candidate.score * 100)}%`}</span>
        <ChevronRight size={15} style={{ color: UI.ink2, transform: open ? "rotate(90deg)" : "none", transition: "transform 150ms ease" }} />
      </summary>
      <div style={{ display: "grid", gap: 8, padding: "9px 8px 12px", borderTop: `1px solid ${UI.line2}` }}>
        {candidate.reason && <p style={{ margin: 0, fontSize: TYPE.caption, color: UI.ink2, lineHeight: 1.5 }}>- {candidate.reason}</p>}
        <div style={{ display: "grid", gap: 9, marginTop: 2, paddingTop: 10, borderTop: `1px solid ${UI.line}` }}>
          <CandidateEvidenceTokens label="확인된 근거" items={candidate.supporting_evidence} tone="ok" references={references} onEvidenceSelect={onEvidenceSelect} />
          <CandidateEvidenceTokens label="추가 확인 필요" items={candidate.missing_evidence} tone="warn" />
        </div>
      </div>
    </details>
  );
}
export function IssueDetail({ name, symptom, rawSymptom, cluster, svc, ns, resourceKind, incidentId, currentSubject, updatedAt, prUrl: _prUrl, onClose, onOpenRef, onAskAi, onRecoverySelected, correlationId, status, severity, rootCause, confidence, supportingEvidence, missingEvidence, situationSummary, recommendedActionSummary, evidenceSummary, evidenceBundleSummary, recoveryReasonCode, topInset = 0, leftInset = 0, rightInset = 0 }: {
  name: string; symptom: string; cluster: string; svc: string; ns: string; onClose: () => void; onOpenRef: (kind: string, n: string) => void; onAskAi: (request?: AiRecoveryHandoff) => void; onRecoverySelected?: (correlationId: string, update: RecoveryProgressOverride, source: "direct" | "ai") => void;
  rawSymptom?: string | null; resourceKind?: string | null; incidentId?: string | null; currentSubject?: string | null; updatedAt?: string | null; prUrl?: string | null;
  correlationId?: string; status?: string; severity?: "critical" | "warning" | null;
  rootCause?: string | null; confidence?: number | null; supportingEvidence?: string[]; missingEvidence?: string[];
  situationSummary?: string | null; recommendedActionSummary?: string | null; evidenceSummary?: string | null; evidenceBundleSummary?: string | null;
  recoveryReasonCode?: string | null;
  topInset?: number; leftInset?: number; rightInset?: number;
}) {
  const [activeTab, setActiveTab] = useState<"detail" | "recovery">("detail");
  const [evidenceChipActive, setEvidenceChipActive] = useState(false);
  // 셸 우측 패널 공통 규약(DetailOverlay·AI 패널과 동일) — 전체 화면 토글 + 좌측 엣지 리사이즈.
  // 전체 화면은 상단바·좌측 내비를 침범하지 않는 콘텐츠 영역 최대치다.
  const [drawerFull, setDrawerFull] = useState(false);
  const [deploymentLinkActive, setDeploymentLinkActive] = useState<string | null>(null);
  const [highlightedEvidenceIndex, setHighlightedEvidenceIndex] = useState<number | null>(null);
  const [selectedRecoveryActionId, setSelectedRecoveryActionId] = useState<string | null>(null);
  const [recoverySelectionPendingId, setRecoverySelectionPendingId] = useState<string | null>(null);
  const [recoverySelectionAccepted, setRecoverySelectionAccepted] = useState(false);
  const [recoverySelectionError, setRecoverySelectionError] = useState<string | null>(null);
  const [recoverySelectionErrorCode, setRecoverySelectionErrorCode] = useState<string | null>(null);
  const [recoveryRetryPending, setRecoveryRetryPending] = useState(false);
  const [recoveryRetryError, setRecoveryRetryError] = useState<string | null>(null);
  const [recoveryReviewActionId, setRecoveryReviewActionId] = useState<string | null>(null);
  const evidenceSummaryRef = useRef<HTMLElement | null>(null);
  const detailScrollRef = useRef<HTMLDivElement | null>(null);
  const recoveryListScrollTopRef = useRef(0);
  // 복구 후보는 실 계약(GET /api/rca/recovery-plans/by-correlation)에서만. 상관관계
  // id가 없으면(예: 지도 파생 진입) idle로 두고 관측 안 됨을 정직하게 표시한다.
  const recovery = useRecoveryPlan(
    correlationId ?? null,
    recoverySelectionAccepted ? 4000 : 0,
  );
  const remediationBundle = useRemediationBundle(correlationId ?? null);
  const recoveryAudit = useRecoveryAudit(
    correlationId ?? null,
    recoverySelectionAccepted || recovery.plan?.selected_action_id ? 4000 : 0,
  );
  const recentChanges = useIncidentRecentChanges(incidentId ?? null);
  const latestReport = useLatestRcaReport(correlationId ?? null);
  const report = latestReport.report;
  const recoveryAvailable = canOpenRecoveryPlan(rootCause, report, recovery.plan);
  const conf = typeof confidence === "number" && Number.isFinite(confidence) ? Math.round(confidence * 100) : null;
  const analysisState = issueAnalysisState({
    status,
    rootCause,
    analysisStatus: report?.analysis_status,
  });
  const headerTone = analysisState.label === "해결됨" ? TINT.ok
    : severity === "warning" ? TINT.warn
      : severity === "critical" ? TINT.crit
        : TINT.blue;
  const support = supportingEvidence ?? [];
  const missing = missingEvidence ?? [];
  const missingPresentations = missingEvidencePresentations(
    missing,
    report?.missing_evidence_checks,
  );
  const resolvedObjectEvidence = useEvidenceObjectReferences(support);
  const evidenceReferences = useMemo(() => {
    return mergeEvidenceReferences([
      ...(report?.supporting_evidence_refs ?? []),
      ...resolvedObjectEvidence.references,
    ]);
  }, [report?.supporting_evidence_refs, resolvedObjectEvidence.references]);
  const observedStatus = status?.trim() ? koLabel(status) : currentSubject?.trim() ? koLabel(currentSubject) : "상태 미확인";
  const reportConfidence = report?.confidence ?? confidence ?? null;
  const reportConfidencePercent = typeof reportConfidence === "number" && Number.isFinite(reportConfidence) ? Math.round(reportConfidence * 100) : null;
  const reportSymptom = report?.symptom ?? rawSymptom ?? symptom;
  const reportScope = [report?.namespace ?? ns, report?.resource_kind ?? resourceKind, report?.resource_name ?? svc].filter(Boolean).join(" | ");
  const reportFirstSeenAt = report?.first_seen_at ?? null;
  const reportAnalysisAt = report?.created_at ?? updatedAt;
  const reportElapsed = reportElapsedLabel(reportFirstSeenAt, reportAnalysisAt);
  const reportImpact = report?.narrative?.impact?.trim() || null;
  const effectiveRootCause = report?.root_cause?.trim() || rootCause?.trim() || null;
  const recoverySummaryCandidate = recovery.plan?.selected_action
    ?? recovery.plan?.candidates[0]
    ?? null;
  const summaryPresentation = rcaSummaryPresentation({
    report: {
      narrativeExecutiveSummary: report?.narrative?.executive_summary,
      narrativeRecommendedAction: report?.narrative?.recommended_action,
      narrativeReasoning: report?.narrative?.reasoning,
      reason: report?.reason,
      evidenceSummary: report?.evidence_summary,
      evidenceBundleSummary: report?.evidence_bundle_summary,
      rawAction: report?.action,
    },
    issue: {
      situationSummary,
      recommendedActionSummary,
      evidenceSummary,
      evidenceBundleSummary,
    },
    recovery: {
      summary: recovery.plan?.summary,
      candidateDescription: recoverySummaryCandidate?.description,
      candidateTitle: recoverySummaryCandidate?.title,
    },
    fallbackSituation: effectiveRootCause
      ? `${rcaDisplayLabel(effectiveRootCause)}로 ${reportSymptom} 증상이 발생한 것으로 분석했습니다.`
      : reportSymptom?.trim() || null,
  });
  const effectiveSituationSummary = summaryPresentation.situation;
  const effectiveFinalJudgment = report?.narrative?.executive_summary?.trim()
    || effectiveSituationSummary;
  const effectiveRecommendedAction = summaryPresentation.recommendedAction;
  const effectiveEvidenceSummary = summaryPresentation.evidence;
  const effectiveEvidenceBundleSummary = summaryPresentation.evidenceBundle;
  const recoveryTarget = recovery.plan?.target;
  const recoveryTargetKind = typeof recoveryTarget?.resource_kind === "string" && recoveryTarget.resource_kind.trim()
    ? recoveryTarget.resource_kind
    : resourceKind;
  const recoveryTargetName = typeof recoveryTarget?.resource_name === "string" && recoveryTarget.resource_name.trim()
    ? recoveryTarget.resource_name
    : svc;
  const selectedRecoveryCandidate = recovery.plan?.candidates.find(
    (candidate) => candidate.action_id === (selectedRecoveryActionId ?? recovery.plan?.selected_action_id),
  ) ?? recovery.plan?.selected_action ?? null;
  const recoveryProgress = recoveryProgressState({
    status,
    currentSubject,
    plan: recovery.plan,
    audit: recoveryAudit.items,
    actionRoute: selectedRecoveryCandidate?.route ?? recovery.plan?.execution_route ?? null,
    selectionPending: recoverySelectionPendingId !== null,
    selectionAccepted: recoverySelectionAccepted,
    selectionFailed: recoverySelectionError !== null,
    reasonCode: recoverySelectionErrorCode ?? recoveryReasonCode,
  });
  useEffect(() => {
    if (
      recovery.plan?.status !== "selection_requested"
      || recovery.plan.selected_action_id !== null
      || !["blocked", "failed"].includes(recoveryProgress.phase)
    ) {
      return;
    }
    const reason = recoveryProgress.latestEvent?.payload_summary.reason;
    const timer = window.setTimeout(() => {
      setSelectedRecoveryActionId(null);
      setRecoverySelectionAccepted(false);
      if (typeof reason === "string" && reason.trim()) {
        setRecoverySelectionError(reason.trim());
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [
    recovery.plan?.status,
    recovery.plan?.selected_action_id,
    recoveryProgress.phase,
    recoveryProgress.latestEvent?.event_id,
    recoveryProgress.latestEvent?.payload_summary.reason,
  ]);
  useEffect(() => {
    if (recovery.plan?.status === "failed") return;
    const timer = window.setTimeout(() => {
      setRecoveryRetryPending(false);
      setRecoveryRetryError(null);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [recovery.plan?.status]);
  const effectiveRecoveryPrUrl = currentRecoveryAttemptPrUrl(recovery.plan);
  const displayedRecoveryProgress = withCreatedPullRequest(
    recoveryProgress,
    effectiveRecoveryPrUrl,
    "PR 생성됨",
  );
  const effectiveSelectedActionId = selectedRecoveryActionId ?? recovery.plan?.selected_action_id ?? null;
  const reviewedRecoveryCandidate = recovery.plan?.candidates.find((candidate) => candidate.action_id === recoveryReviewActionId) ?? null;
  const reviewedRecoveryDraft = remediationBundle.bundle?.remediation?.candidates.find(
    (candidate) => candidate.action_id === recoveryReviewActionId,
  )?.draft ?? null;
  const beginRecoveryReview = (actionId: string) => {
    recoveryListScrollTopRef.current = detailScrollRef.current?.scrollTop ?? 0;
    setRecoveryReviewActionId(actionId);
    requestAnimationFrame(() => detailScrollRef.current?.scrollTo({ top: 0 }));
  };
  const closeRecoveryReview = () => {
    setRecoveryReviewActionId(null);
    requestAnimationFrame(() => detailScrollRef.current?.scrollTo({ top: recoveryListScrollTopRef.current }));
  };
  const handleRecoverySelection = async (
    actionId: string,
    source: "direct" | "ai" = "direct",
  ): Promise<RecoveryActionAccepted | null> => {
    if (!correlationId || !recovery.plan || recoverySelectionPendingId !== null) return null;
    const selectedCandidate = recovery.plan.candidates.find((candidate) => candidate.action_id === actionId);
    const selectedRoute = selectedCandidate?.route ?? recovery.plan.execution_route;
    setRecoverySelectionPendingId(actionId);
    setRecoverySelectionError(null);
    setRecoverySelectionErrorCode(null);
    onRecoverySelected?.(correlationId, {
      actionRoute: selectedRoute,
      selectionPending: true,
      selectionAccepted: false,
      selectionFailed: false,
      reasonCode: null,
    }, source);
    try {
      const receipt = await selectRecoveryAction(correlationId, recovery.plan.plan_id, actionId);
      if (!receipt.accepted) throw new Error("recovery selection was not accepted");
      setSelectedRecoveryActionId(actionId);
      setRecoverySelectionAccepted(true);
      onRecoverySelected?.(correlationId, {
        actionRoute: selectedRoute,
        selectionPending: false,
        selectionAccepted: true,
        selectionFailed: false,
        reasonCode: null,
      }, source);
      return receipt;
    } catch (cause: unknown) {
      const errorCode = isApiError(cause) ? cause.code : null;
      setRecoverySelectionErrorCode(errorCode);
      setRecoverySelectionError(
        isApiError(cause)
          ? cause.detail ?? cause.message
          : "복구 조치를 선택하지 못했습니다. 권한과 현재 플랜 상태를 확인해 주세요.",
      );
      onRecoverySelected?.(correlationId, {
        actionRoute: selectedRoute,
        selectionPending: false,
        selectionAccepted: false,
        selectionFailed: true,
        reasonCode: errorCode,
      }, source);
      if (source === "ai") throw cause;
      return null;
    } finally {
      setRecoverySelectionPendingId(null);
    }
  };
  const handleRecoveryRetry = async (): Promise<void> => {
    if (
      !correlationId
      || !recovery.plan
      || recovery.plan.status !== "failed"
      || recoveryRetryPending
    ) return;
    setRecoveryRetryPending(true);
    setRecoveryRetryError(null);
    try {
      const receipt = await retryRecovery(
        correlationId,
        recovery.plan.plan_id,
        { reason: "operator requested failed recovery stage retry" },
      );
      if (!receipt.accepted) throw new Error("recovery retry was not accepted");
      setRecoverySelectionAccepted(true);
    } catch (cause: unknown) {
      setRecoveryRetryPending(false);
      setRecoveryRetryError(
        isApiError(cause)
          ? cause.detail ?? cause.message
          : "실패한 복구 단계를 다시 시작하지 못했습니다. 저장된 배포 identity와 현재 상태를 확인해 주세요.",
      );
    }
  };
  const openEvidenceDetail = (item: string) => {
    const index = evidenceReferences.findIndex((evidence) => evidenceReferenceMatches(item, evidence));
    if (index < 0) return;
    setHighlightedEvidenceIndex(index);
    const target = document.getElementById(evidenceDetailAnchor(index)) as HTMLDetailsElement | null;
    if (!target) return;
    target.open = true;
    requestAnimationFrame(() => target.scrollIntoView({ behavior: "smooth", block: "center" }));
  };
  return (
    <DetailDrawer
      ariaLabel={`${name} RCA 상세`}
      bodyRef={detailScrollRef}
      bodyStyle={{
        display: "flex",
        flexDirection: "column",
        gap: SPACE.section,
        padding: `${SPACE.section}px ${SPACE.section}px 104px`,
        background: activeTab === "recovery" && reviewedRecoveryCandidate ? INSET : UI.card,
        transition: `background ${DUR.fade}s ease`,
      }}
      expanded={drawerFull}
      header={(
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
            <span style={{ width: 38, height: 38, borderRadius: 11, background: headerTone.bg, display: "grid", placeItems: "center", flexShrink: 0 }}>
              {analysisState.label === "해결됨"
                ? <Check size={19} strokeWidth={2.4} style={{ color: headerTone.fg }} />
                : <AlertTriangle size={19} style={{ color: headerTone.fg }} />}
            </span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <Mono>{name}</Mono>
                <Pill tone={analysisState.tone} label={analysisState.label} />
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: "5px 16px", marginTop: 9 }}>
                {[
                  ["클러스터", cluster],
                  ["네임스페이스", ns],
                  ["종류", resourceKind ?? "미확인"],
                  ["대상", svc],
                ].map(([label, value]) => (
                  <span key={label} style={{ minWidth: 0, display: "flex", alignItems: "baseline", gap: 6, fontSize: TYPE.caption }}>
                    <span style={{ flexShrink: 0, color: UI.ink3 }}>{label}</span>
                    <span style={{ minWidth: 0, color: UI.ink2, fontFamily: MONO, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{value || "미확인"}</span>
                  </span>
                ))}
              </div>
            </div>
        </div>
      )}
      leftInset={leftInset}
      navigation={(
        <DetailDrawerTabs
          active={activeTab}
          indicatorId="rca-detail-tab"
          items={[
            { id: "detail", label: "이슈 상세" },
            {
              id: "recovery",
              label: "복구 플랜",
              disabled: !recoveryAvailable,
              title: !recoveryAvailable
                ? "원인 후보와 복구 플랜이 확인되면 열 수 있습니다."
                : undefined,
            },
          ]}
          onChange={setActiveTab}
        />
      )}
      onClose={onClose}
      onExpandedChange={setDrawerFull}
      rightInset={rightInset}
      topInset={topInset}
    >
            {activeTab === "detail" ? <>
            <section aria-labelledby="issue-summary-heading" style={{ flexShrink: 0, display: "grid", gap: SPACE.stack, border: `1px solid ${UI.line}`, borderRadius: RADIUS.card, background: UI.card, padding: SPACE.card, boxShadow: `0 6px 16px -10px ${inkA(0.26)}, 0 1px 3px ${inkA(0.06)}` }}>
              <h2 id="issue-summary-heading" style={{ margin: 0, fontSize: ISSUE_DETAIL_TYPE.sectionTitle, fontWeight: 700, color: UI.heading }}>상황 요약</h2>
              <p style={{ margin: 0, fontSize: TYPE.body, fontWeight: 600, color: effectiveSituationSummary ? UI.ink : UI.ink3, lineHeight: 1.65 }}>{effectiveSituationSummary || "상황 요약 정보가 아직 없습니다."}</p>
              {(reportFirstSeenAt || reportElapsed || reportImpact) && <dl style={{ display: "grid", gridTemplateColumns: "86px minmax(0, 1fr)", gap: "7px 10px", margin: 0, paddingTop: 12, borderTop: `1px solid ${UI.line2}`, fontSize: TYPE.caption, lineHeight: 1.5 }}>
                {reportFirstSeenAt && <><dt style={{ color: UI.ink3 }}>장애 시작</dt><dd style={{ margin: 0, color: UI.ink2 }}>{reportTimeLabel(reportFirstSeenAt)}</dd></>}
                {reportElapsed && <><dt style={{ color: UI.ink3 }}>분석 시점까지</dt><dd style={{ margin: 0, color: UI.ink2 }}>{reportElapsed}</dd></>}
                {reportImpact && <><dt style={{ color: UI.ink3 }}>관측 영향</dt><dd style={{ margin: 0, color: UI.ink2 }}>{reportImpact}</dd></>}
              </dl>}
              <div style={{ display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap" }}>
                <span style={{ border: `1px solid ${UI.line}`, borderRadius: 999, padding: "3px 9px", fontSize: TYPE.caption, color: UI.ink2, background: UI.card }}>{observedStatus}</span>
                {conf !== null && <span style={{ border: `1px solid ${UI.line}`, borderRadius: 999, padding: "3px 9px", fontSize: TYPE.caption, color: UI.ink2, background: UI.card }}>신뢰도 {conf}%</span>}
                <button type="button" className="product-focusable product-control" onClick={() => evidenceSummaryRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}
                  onMouseEnter={() => setEvidenceChipActive(true)} onMouseLeave={() => setEvidenceChipActive(false)} onFocus={() => setEvidenceChipActive(true)} onBlur={() => setEvidenceChipActive(false)}
                  style={{ border: `1px solid ${evidenceChipActive ? UI.ink3 : UI.line}`, borderRadius: 999, padding: "3px 9px", fontSize: TYPE.caption, color: evidenceChipActive ? UI.ink : UI.ink2, background: evidenceChipActive ? inkA(0.055) : UI.card, cursor: "pointer", transition: `background ${DUR.micro}s ease, color ${DUR.micro}s ease, border-color ${DUR.micro}s ease` }}>확인된 근거 {Math.max(support.length, evidenceReferences.length)}</button>
              </div>
              {missingPresentations.length > 0 && (
                <div style={{ display: "grid", gap: 8, border: `1px solid ${UI.line}`, borderRadius: 8, background: UI.bg2, padding: 11 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: TYPE.caption, fontWeight: 600, color: UI.ink2 }}><ShieldAlert size={14} style={{ color: TINT.warn.fg }} />추가 확인 필요</div>
                  <ul style={{ display: "grid", gap: 5, margin: 0, padding: 0, listStyle: "none" }}>
                    {missingPresentations.map((presentation, index) => (
                      <li key={`${presentation.message}-${presentation.metadata ?? ""}-${index}`} style={{ display: "grid", gridTemplateColumns: "10px minmax(0, 1fr)", gap: 4, fontSize: TYPE.caption, color: UI.ink2, lineHeight: 1.45 }}>
                        <span aria-hidden="true">-</span>
                        <span style={{ display: "grid", gap: 1 }}>
                          <span>{presentation.message}</span>
                          {presentation.metadata && (
                            <span style={{ fontSize: 10, color: UI.ink3 }}>{presentation.metadata}</span>
                          )}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </section>

            <RcaCardSection title="최근 변경">
              <div>
                {recentChanges.status === "loading" ? (
                  <div style={{ padding: 14, fontSize: TYPE.label, color: UI.ink2 }}>최근 변경을 불러오는 중…</div>
                ) : recentChanges.status === "unavailable" ? (
                  <div role="alert" style={{ display: "grid", gridTemplateColumns: "auto minmax(0, 1fr)", gap: 9, padding: 15, color: TINT.crit.fg }}>
                    <CircleAlert size={15} style={{ marginTop: 1 }} />
                    <div style={{ display: "grid", gap: 4 }}>
                      <span style={{ fontSize: TYPE.label, fontWeight: 600 }}>최근 변경을 불러올 수 없습니다.</span>
                      <span style={{ fontSize: TYPE.caption, lineHeight: 1.45 }}>요청한 최근 변경 기록을 확인할 수 없습니다.</span>
                    </div>
                  </div>
                ) : recentChanges.status === "idle" || recentChanges.items.length === 0 ? (
                  <div style={{ padding: 14, fontSize: TYPE.label, color: UI.ink3 }}>장애 이전에 확인된 변경이 없습니다.</div>
                ) : recentChanges.items.map((change, index) => (
                  <div key={change.event_id} style={{ display: "grid", gap: 8, padding: 13, borderTop: index > 0 ? `1px solid ${UI.line2}` : "none" }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                      <span style={{ minWidth: 0, fontSize: TYPE.label, fontWeight: 600, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{change.resource_kind} · {change.resource_name}</span>
                      <time dateTime={change.changed_at} style={{ flexShrink: 0, fontSize: TYPE.caption, color: UI.ink2 }}>{fromNow(change.changed_at)}</time>
                    </div>
                    {(change.image_before || change.image_after) && (
                      <div style={{ display: "grid", gap: 8, borderRadius: 8, background: UI.bg2, padding: 10, fontSize: TYPE.caption, color: UI.ink2 }}>
                        {change.image_before && <div style={{ display: "grid", gridTemplateColumns: "auto 88px minmax(0, 1fr)", alignItems: "center", gap: 8 }}>
                          <ArrowLeft size={13} style={{ color: UI.ink2 }} />
                          <span style={{ color: UI.ink2 }}>이전 배포 버전</span>
                          <button type="button" className="product-focusable product-control" title={`${change.resource_kind} ${change.resource_name} 상세 열기`} onClick={() => onOpenRef(change.resource_kind, change.resource_name)}
                            onMouseEnter={() => setDeploymentLinkActive(`${change.event_id}-before`)} onMouseLeave={() => setDeploymentLinkActive(null)} onFocus={() => setDeploymentLinkActive(`${change.event_id}-before`)} onBlur={() => setDeploymentLinkActive(null)}
                            style={{ minWidth: 0, justifySelf: "start", maxWidth: "100%", border: "none", borderRadius: 5, background: deploymentLinkActive === `${change.event_id}-before` ? inkA(0.06) : "transparent", color: deploymentLinkActive === `${change.event_id}-before` ? UI.ink : UI.ink2, padding: "2px 4px", fontFamily: MONO, fontSize: TYPE.caption, cursor: "pointer", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{change.image_before}</button>
                        </div>}
                        {change.image_after && <div style={{ display: "grid", gridTemplateColumns: "auto 88px minmax(0, 1fr)", alignItems: "center", gap: 8 }}>
                          <ArrowRight size={13} style={{ color: UI.ink2 }} />
                          <span style={{ color: UI.ink2 }}>현재 배포 버전</span>
                          <button type="button" className="product-focusable product-control" title={`${change.resource_kind} ${change.resource_name} 상세 열기`} onClick={() => onOpenRef(change.resource_kind, change.resource_name)}
                            onMouseEnter={() => setDeploymentLinkActive(`${change.event_id}-after`)} onMouseLeave={() => setDeploymentLinkActive(null)} onFocus={() => setDeploymentLinkActive(`${change.event_id}-after`)} onBlur={() => setDeploymentLinkActive(null)}
                            style={{ minWidth: 0, justifySelf: "start", maxWidth: "100%", border: "none", borderRadius: 5, background: deploymentLinkActive === `${change.event_id}-after` ? inkA(0.06) : "transparent", color: deploymentLinkActive === `${change.event_id}-after` ? UI.ink : UI.ink2, padding: "2px 4px", fontFamily: MONO, fontSize: TYPE.caption, cursor: "pointer", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{change.image_after}</button>
                        </div>}
                      </div>
                    )}
                    <div style={{ display: "grid", gridTemplateColumns: "76px minmax(0, 1fr)", gap: "6px 10px", paddingTop: 2, fontSize: TYPE.caption, color: UI.ink2 }}>
                      {change.commit_sha && <><span>커밋</span><b title={change.commit_sha} style={{ minWidth: 0, color: UI.ink2, fontFamily: MONO, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{change.commit_sha.slice(0, 7)}</b></>}
                      {(change.repository_id || change.repo_ref) && <><span>저장소</span><span style={{ minWidth: 0, display: "flex", alignItems: "center", gap: 7, color: UI.ink2, overflow: "hidden" }}>{change.repo_ref && <span style={{ flexShrink: 0, borderRadius: 6, background: inkA(0.06), padding: "2px 7px", fontSize: TYPE.caption, color: UI.ink2 }}>{change.repo_ref}</span>}<span title={change.repository_id} style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{change.repository_id || "저장소 미확인"}</span></span></>}
                      {change.workflow_run_id && <><span>배포 실행</span><span title={`CI/CD 배포 실행 ID: ${change.workflow_run_id}`} style={{ minWidth: 0, color: UI.ink2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{change.workflow_run_id}</span></>}
                    </div>
                    {change.pr_url && <a href={change.pr_url} target="_blank" rel="noopener noreferrer" style={{ justifySelf: "end", display: "inline-flex", alignItems: "center", gap: 5, color: BLUE, fontSize: TYPE.caption, fontWeight: 600, textDecoration: "none" }}><ExternalLink size={13} />Pull request 열기</a>}
                  </div>
                ))}
              </div>
            </RcaCardSection>

            <RcaCardSection title="RCA 보고서">
              <div style={{ display: "grid", padding: "15px 15px 2px" }}>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 10, paddingBottom: 14, borderBottom: `1px dashed ${UI.line}` }}>
                  {[
                    ["장애 심각도", reportRiskLabel(report?.severity ?? severity)],
                    ["신뢰도", reportConfidencePercent === null ? "미확인" : `${reportConfidencePercent}%`],
                    ["시간", reportTimeLabel(report?.created_at ?? updatedAt)],
                  ].map(([label, value]) => <div key={label} style={{ minWidth: 0, display: "grid", gap: 3 }}><span style={{ fontSize: TYPE.caption, color: UI.ink2 }}>{label}</span><strong title={value} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: TYPE.caption, color: value === "미확인" ? UI.ink3 : UI.ink }}>{value}</strong></div>)}
                </div>
                <dl style={{ display: "grid", gridTemplateColumns: "52px minmax(0, 1fr)", gap: "7px 8px", margin: 0, padding: "14px 0" }}>
                  <dt style={{ fontSize: TYPE.caption, color: UI.ink2 }}>증상</dt><dd title={reportSymptom} style={{ margin: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: TYPE.caption, color: UI.ink }}>{reportSymptom}</dd>
                  <dt style={{ fontSize: TYPE.caption, color: UI.ink2 }}>영향 범위</dt><dd title={reportScope} style={{ margin: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: TYPE.caption, color: reportScope ? UI.ink : UI.ink3 }}>{reportScope || "미확인"}</dd>
                </dl>
                <ReportNumberedSection number="01" title="최종 판단">
                  <p style={{ margin: 0, fontSize: TYPE.label, color: effectiveFinalJudgment ? UI.ink2 : UI.ink3, lineHeight: 1.6 }}>{effectiveFinalJudgment || "최종 판단 정보가 아직 없습니다."}</p>
                </ReportNumberedSection>
                <ReportNumberedSection number="02" title="최종 원인">
                  <RcaSelectedCause report={report} fallbackCause={effectiveRootCause} references={evidenceReferences} onEvidenceSelect={openEvidenceDetail} />
                </ReportNumberedSection>
                <ReportNumberedSection number="03" title="원인 후보">
                  {latestReport.status === "loading" && <span style={{ fontSize: TYPE.caption, color: UI.ink2 }}>원인 후보를 불러오는 중…</span>}
                  {latestReport.status === "unavailable" && <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: TYPE.caption, color: TINT.crit.fg }}><CircleAlert size={14} />원인 후보를 불러오지 못했습니다.</span>}
                  <RcaAlternativeCandidates report={report} references={evidenceReferences} onEvidenceSelect={openEvidenceDetail} />
                </ReportNumberedSection>
                <ReportNumberedSection number="04" title="근거 요약" sectionRef={evidenceSummaryRef}>
                  {effectiveEvidenceSummary || effectiveEvidenceBundleSummary ? <div style={{ display: "grid", gap: 7 }}>
                    {effectiveEvidenceSummary && <div style={{ display: "grid", gridTemplateColumns: "16px minmax(0, 1fr)", gap: 7, alignItems: "start" }}><span aria-hidden="true" style={{ width: 16, height: 20, display: "grid", placeItems: "center", color: TINT.ok.fg }}><CircleCheck size={14} /></span><p style={{ margin: 0, fontSize: TYPE.label, color: UI.ink2, lineHeight: 1.55 }}>{effectiveEvidenceSummary}</p></div>}
                    {effectiveEvidenceBundleSummary && <p style={{ margin: "0 0 0 23px", fontSize: TYPE.caption, color: UI.ink2, lineHeight: 1.5 }}>{effectiveEvidenceBundleSummary}</p>}
                  </div> : <p style={{ margin: 0, fontSize: TYPE.label, color: UI.ink3 }}>근거 요약이 아직 없습니다.</p>}
                </ReportNumberedSection>
                <ReportNumberedSection number="05" title="근거 상세">
                  {resolvedObjectEvidence.status === "loading" && evidenceReferences.length === 0 && <span style={{ fontSize: TYPE.caption, color: UI.ink2 }}>근거 상세를 연결하는 중…</span>}
                  {evidenceReferences.length > 0 ? <div style={{ display: "grid", borderBottom: `1px dashed ${UI.line}` }}>
                    {evidenceReferences.map((evidence, index) => <EvidenceDetailItem key={`${evidence.source}-${evidence.name}-${index}`} id={evidenceDetailAnchor(index)} evidence={evidence} highlighted={highlightedEvidenceIndex === index} onOpenChange={(open) => { if (!open) setHighlightedEvidenceIndex((current) => current === index ? null : current); }} />)}
                  </div> : support.length > 0 && resolvedObjectEvidence.status !== "loading" ? <ul style={{ display: "grid", gap: 7, margin: 0, padding: 0, listStyle: "none" }}>{support.map((item, index) => <li key={`${item}-${index}`} style={{ fontSize: TYPE.label, color: UI.ink2, lineHeight: 1.5 }}>{evidenceReferenceLabel(item, [])}</li>)}</ul> : resolvedObjectEvidence.status !== "loading" ? <p style={{ margin: 0, fontSize: TYPE.label, color: UI.ink3 }}>근거 상세가 아직 없습니다.</p> : null}
                </ReportNumberedSection>
                <ReportNumberedSection number="06" title="권장 조치">
                  <p style={{ margin: 0, fontSize: TYPE.label, color: effectiveRecommendedAction ? UI.ink2 : UI.ink3, lineHeight: 1.55 }}>{effectiveRecommendedAction || "권장 조치가 아직 없습니다."}</p>
                  <button type="button" className="product-focusable product-control" disabled={!recoveryAvailable}
                    title={!recoveryAvailable ? "원인 후보와 복구 플랜이 확인되면 열 수 있습니다." : undefined}
                    onClick={() => {
                    if (!recoveryAvailable) return;
                    setActiveTab("recovery");
                    requestAnimationFrame(() => detailScrollRef.current?.scrollTo({ top: 0 }));
                  }} style={{ justifySelf: "end", border: `1px solid ${recoveryAvailable ? blueA(0.32) : UI.line}`, borderRadius: 8, background: recoveryAvailable ? blueA(0.07) : UI.bg2, color: recoveryAvailable ? BLUE : UI.ink3, padding: "7px 12px", fontSize: TYPE.caption, fontWeight: 600, cursor: recoveryAvailable ? "pointer" : "not-allowed" }}>복구 플랜 보기</button>
                </ReportNumberedSection>
              </div>
            </RcaCardSection>
            </> : <>
            {/* 복구 플랜 — 실 후보 조회, 선택 API, 감사 이벤트를 동일 진행 상태에 연결한다. */}
            <AnimatePresence mode="wait" initial={false}>
            {reviewedRecoveryCandidate ? (
              <RecoveryConfirmation
                candidate={reviewedRecoveryCandidate}
                draft={reviewedRecoveryDraft}
                bundleStatus={remediationBundle.status}
                progress={displayedRecoveryProgress}
                lifecycle={recovery.plan?.lifecycle}
                prUrl={effectiveRecoveryPrUrl}
                retryPending={recoveryRetryPending}
                retryError={recoveryRetryError}
                pending={recoverySelectionPendingId === reviewedRecoveryCandidate.action_id}
                selected={effectiveSelectedActionId === reviewedRecoveryCandidate.action_id}
                onBack={closeRecoveryReview}
                onConfirm={() => void handleRecoverySelection(reviewedRecoveryCandidate.action_id)}
                onRetry={() => void handleRecoveryRetry()}
                onAskAi={() => {
                  if (!correlationId || !recovery.plan) return;
                  const actionId = reviewedRecoveryCandidate.action_id;
                  onAskAi({
                    id: `recovery:${correlationId}:${actionId}:${Date.now()}`,
                    correlationId,
                    prompt: recoveryAiPrompt({
                      candidate: reviewedRecoveryCandidate,
                      cluster,
                      namespace: ns,
                      resourceKind: resourceKind || "리소스",
                      resourceName: svc,
                      symptom: rawSymptom || symptom,
                      rootCause,
                    }),
                    displayPrompt: recoveryAiDisplayPrompt({
                      candidate: reviewedRecoveryCandidate,
                      resourceKind: resourceKind || "리소스",
                      resourceName: svc,
                      symptom: rawSymptom || symptom,
                      rootCause,
                    }),
                    actionTitle: reviewedRecoveryCandidate.title,
                    actionRoute: reviewedRecoveryCandidate.route,
                    validationChecks: reviewedRecoveryCandidate.validation_checks,
                    contextView: "복구 플랜",
                    contextScope: cluster,
                    preview: recoveryAiPreview(reviewedRecoveryCandidate, reviewedRecoveryDraft),
                    execute: async () => {
                      const receipt = await handleRecoverySelection(actionId, "ai");
                      return receipt ? {
                        accepted: receipt.accepted,
                        eventId: receipt.event_id,
                        correlationId: receipt.correlation_id,
                        commandId: receipt.command_id,
                      } : null;
                    },
                  });
                }}
                onOpenTarget={recoveryTargetKind && recoveryTargetName ? () => onOpenRef(recoveryTargetKind, recoveryTargetName) : null}
              />
            ) : (
            <motion.div key="recovery-list" initial={{ opacity: 0, x: -20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} transition={{ duration: DUR.fade }} style={{ display: "grid", gap: 16 }}>
              {recovery.status === "idle" ? (
                <div style={{ fontSize: TYPE.label, color: UI.ink3 }}>복구 플랜 없음</div>
              ) : recovery.status === "loading" ? (
                <div style={{ fontSize: TYPE.label, color: UI.ink3 }}>복구 플랜 불러오는 중…</div>
              ) : recovery.status === "pending" ? (
                <div style={{ display: "grid", gap: 6 }}>
                  <strong style={{ fontSize: TYPE.label, color: UI.heading }}>복구 플랜 생성 중</strong>
                  <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>
                    원인 분석 결과를 바탕으로 복구 후보를 만들고 있습니다. 잠시 후 자동으로 다시 확인합니다.
                  </span>
                </div>
              ) : recovery.status === "unavailable" || recovery.plan === null ? (
                <div style={{ fontSize: TYPE.label, color: UI.ink3 }}>복구 플랜을 불러오지 못했습니다.</div>
              ) : recovery.plan.candidates.length === 0 ? (
                <div style={{ fontSize: TYPE.label, color: UI.ink3 }}>관측된 복구 후보 없음</div>
              ) : (
                <RecoveryPlanPanel
                  plan={recovery.plan}
                  selectedActionId={effectiveSelectedActionId}
                  pendingActionId={recoverySelectionPendingId}
                  selectionError={recoverySelectionError}
                  onSelect={beginRecoveryReview}
                  onOpenTarget={recoveryTargetKind && recoveryTargetName ? () => onOpenRef(recoveryTargetKind, recoveryTargetName) : null}
                />
              )}
            </motion.div>
            )}
            </AnimatePresence>
            </>}
    </DetailDrawer>
  );
}

// ── 이슈 /issues — 진행 중 | RCA | 예방 점검 (5.8 + 5.10 통합) ──
// RcaIncident: 상세 드로어로 넘기는 이슈 식별자 + 서버가 준 관측 RCA 필드(전부 선택적).
// 지도 등 상관관계 없는 진입점은 기본 5필드만 채우고, 상세는 정직한 "관측 안 됨"으로.
export type RcaIncident = {
  name: string; symptom: string; cluster: string; svc: string; ns: string;
  rawSymptom?: string | null;
  resourceKind?: string | null;
  incidentId?: string | null;
  currentSubject?: string | null;
  updatedAt?: string | null;
  correlationId?: string;
  status?: string;
  severity?: "critical" | "warning" | null;
  rootCause?: string | null;
  confidence?: number | null;
  supportingEvidence?: string[];
  missingEvidence?: string[];
  situationSummary?: string | null;
  recommendedActionSummary?: string | null;
  evidenceSummary?: string | null;
  evidenceBundleSummary?: string | null;
  recoveryReasonCode?: string | null;
  prUrl?: string | null;
};

type IssueSeverityFilter = "all" | "critical" | "warning";

function IssueSeverityFilters({ active, criticalCount, warningCount, onChange, totalCount }: {
  active: IssueSeverityFilter;
  criticalCount: number;
  warningCount: number;
  onChange: (filter: IssueSeverityFilter) => void;
  totalCount: number;
}) {
  const filters: { id: IssueSeverityFilter; label: string; value: number }[] = [
    { id: "all", label: "전체", value: totalCount },
    { id: "critical", label: "장애", value: criticalCount },
    { id: "warning", label: "주의", value: warningCount },
  ];
  return (
    <div aria-label="진행 중 이슈 심각도 필터" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      {filters.map((filter) => {
        const selected = active === filter.id;
        const selectedTone = filter.id === "critical" ? TINT.crit : filter.id === "warning" ? TINT.warn : TINT.blue;
        return (
          <button
            key={filter.id}
            className="product-focusable"
            type="button"
            aria-pressed={selected}
            onClick={() => onChange(filter.id)}
            style={{ ...segStyle, borderColor: selected ? selectedTone.bd : UI.line, background: selected ? selectedTone.bg : UI.card, color: selected ? selectedTone.fg : UI.ink2, cursor: "pointer" }}
          >
            {filter.label} <b style={numStyle}>{filter.value}</b>
          </button>
        );
      })}
    </div>
  );
}

function RecoveryProgress({ progress }: { progress: RecoveryProgressState }) {
  const activeColor = progress.tone === "failed" ? HP.crit
    : progress.tone === "completed" ? HP.ok
      : progress.tone === "approval" ? HP.warn
        : BLUE;
  const displayedStep = recoveryDisplayedStep(progress);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 7, color: UI.ink2 }}>
      <span>{progress.label}</span>
      <span aria-hidden="true" style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
        {Array.from({ length: 5 }, (_, index) => (
          <span key={index} style={{ width: 11, height: 5, borderRadius: 3, background: index < displayedStep ? activeColor : HP.pending, opacity: index < displayedStep ? 1 : 0.7 }} />
        ))}
      </span>
      <span style={{ fontVariantNumeric: "tabular-nums" }}>{displayedStep}/5</span>
    </span>
  );
}

const ISSUE_ATTEMPT_COPY = {
  newerDetected: "새 에러 감지됨",
  additionalAttempts: (count: number) => `추가 ${count}개`,
  latestCorrelation: "최신 correlation 보기",
};

function IssueCard({ issue, recoveryProgressOverride, recoveryCompleted, onOpen, onOpenLatest, onOpenTarget }: { issue: RcaIssueDetailView; recoveryProgressOverride: RecoveryProgressOverride | null; recoveryCompleted: boolean; onOpen: () => void; onOpenLatest: (() => void) | null; onOpenTarget: (() => void) | null }) {
  const [targetActive, setTargetActive] = useState(false);
  const state = issueAnalysisState({ ...issue, status: recoveryCompleted ? "resolved" : issue.status });
  const recovery = recoveryProgressState({
    status: recoveryCompleted ? "resolved" : issue.status,
    currentSubject: issue.currentSubject,
    actionRoute: recoveryProgressOverride?.actionRoute ?? issue.actionRoute,
    selectionPending: recoveryProgressOverride?.selectionPending ?? false,
    selectionAccepted: recoveryProgressOverride?.selectionAccepted ?? false,
    selectionFailed: recoveryProgressOverride?.selectionFailed ?? false,
    reasonCode: recoveryProgressOverride?.reasonCode ?? issue.recoveryReasonCode,
  });
  const displayedRecovery = withCreatedPullRequest(recovery, issue.prUrl, "PR 검토 필요");
  const prReference = issue.prUrl ? pullRequestReference(issue.prUrl) : null;
  const title = issue.resourceName ?? "대상 미확인";
  const symptom = issue.symptom ?? "증상 미확인";
  const rootCause = issue.rootCause ?? "원인 미확인";
  const severityLabel = issue.severity === "critical" ? "장애" : issue.severity === "warning" ? "주의" : "정보";
  const indicatorLabel = state.label === "해결됨" ? "해결됨" : severityLabel;
  const indicatorColor = state.label === "해결됨"
    ? HP.ok
    : issue.severity === "critical" ? HP.crit : issue.severity === "warning" ? HP.warn : BLUE;
  const symptomWithCode = issue.rawSymptom && issue.rawSymptom !== symptom
    ? `${symptom} (${issue.rawSymptom})`
    : symptom;
  const evidenceCount = issue.supportingEvidence.length;
  const target = [issue.resourceKind, issue.resourceName].filter(Boolean).join(" · ") || "대상 미확인";
  const scope = [issue.clusterId, issue.namespace].filter(Boolean).join(" · ") || "범위 미확인";
  const newerAttemptCount = issue.newerAttemptCount ?? 0;
  const hasNewerAttempt = newerAttemptCount > 0 && issue.latestAttempt !== null;
  const attemptTitle = issue.recentAttempts.length
    ? issue.recentAttempts.map((attempt) => `${fromNow(attempt.updatedAt)} · ${koLabel(attempt.status)} · ${attempt.correlationId}`).join("\n")
    : undefined;

  return (
    <motion.article
      whileHover={{ y: -1 }}
      transition={{ duration: DUR.micro }}
      style={{
        position: "relative", width: "100%", minWidth: 0, display: "block",
        padding: 0, overflow: "hidden", textAlign: "left",
        border: `1px solid ${UI.line}`, borderRadius: RADIUS.card, background: UI.card,
        boxShadow: `0 1px 2px ${inkA(0.04)}`, color: UI.ink,
      }}
    >
      {/* 카드의 목적지는 이슈 상세 하나다. 전체 카드 오버레이 버튼으로 마우스·키보드
          진입을 통일하고, 별도 목적지인 대상 리소스/PR 링크만 위 레이어에서 유지한다. */}
      <button
        type="button"
        aria-label={`${title} 이슈 상세 열기`}
        className="product-focusable product-control"
        onClick={onOpen}
        style={{ position: "absolute", inset: 0, zIndex: 1, width: "100%", border: "none", borderRadius: RADIUS.card, background: "transparent", cursor: "pointer" }}
      />
      <span style={{ position: "relative", zIndex: 2, pointerEvents: "none", minWidth: 0, display: "grid", gap: SPACE.stack, padding: `${SPACE.stack}px ${SPACE.card}px` }}>
        <span style={{ minWidth: 0, display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ minWidth: 0, flex: 1, display: "flex", alignItems: "center", gap: 8 }}>
            <span role="img" aria-label={`상태: ${indicatorLabel}`} title={`상태: ${indicatorLabel}`} style={{ width: 12, height: 12, flexShrink: 0, alignSelf: "center", cursor: "help", borderRadius: 999, background: indicatorColor }} />
            <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: TYPE.body, lineHeight: 1.35, fontWeight: 600 }}>{title}</span>
          </span>
          <time dateTime={issue.updatedAt ?? undefined} style={{ flexShrink: 0, display: "inline-flex", alignItems: "center", gap: 4, fontSize: TYPE.caption, color: UI.ink3 }}>
            <Clock size={12} />{fromNow(issue.updatedAt)}
          </time>
        </span>

        <span style={{ minWidth: 0, display: "grid", gap: 5, fontSize: TYPE.label, lineHeight: 1.45 }}>
          <span style={{ color: UI.ink2 }}><strong style={{ color: UI.ink, fontWeight: 600 }}>증상</strong><span style={{ margin: "0 7px", color: UI.line }}>|</span>{symptomWithCode}</span>
          <span style={{ color: UI.ink2 }}><strong style={{ color: UI.ink, fontWeight: 600 }}>원인</strong><span style={{ margin: "0 7px", color: UI.line }}>|</span>{rootCause}</span>
          <span style={{ color: UI.ink2 }}>
            <strong style={{ color: UI.ink, fontWeight: 600 }}>대상</strong><span style={{ margin: "0 7px", color: UI.line }}>|</span>
            {onOpenTarget ? (
              <button
                type="button"
                className="product-focusable product-control"
                onClick={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  onOpenTarget();
                }}
                onMouseEnter={() => setTargetActive(true)}
                onMouseLeave={() => setTargetActive(false)}
                onFocus={() => setTargetActive(true)}
                onBlur={() => setTargetActive(false)}
                style={{ position: "relative", zIndex: 3, pointerEvents: "auto", maxWidth: "100%", border: "none", borderRadius: 7, background: targetActive ? TINT.gray.bd : TINT.gray.bg, color: targetActive ? UI.ink : UI.ink2, padding: "2px 7px", fontSize: TYPE.caption, fontWeight: 400, lineHeight: 1.35, cursor: "pointer", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", verticalAlign: "middle", transition: `background ${DUR.micro}s ease, color ${DUR.micro}s ease` }}
                title={`${target} 리소스 상세 열기`}
              >
                {target}
              </button>
            ) : target}
          </span>
          <span style={{ color: UI.ink2 }}><strong style={{ color: UI.ink, fontWeight: 600 }}>복구</strong><span style={{ margin: "0 7px", color: UI.line }}>|</span><RecoveryProgress progress={displayedRecovery} /></span>
          {issue.prUrl && prReference && (
            <span style={{ color: UI.ink2 }}>
              <strong style={{ color: UI.ink, fontWeight: 600 }}>복구 PR</strong><span style={{ margin: "0 7px", color: UI.line }}>|</span>
              <a
                className="product-focusable"
                href={issue.prUrl}
                onClick={(event) => event.stopPropagation()}
                rel="noopener noreferrer"
                target="_blank"
                style={{ position: "relative", zIndex: 3, pointerEvents: "auto", display: "inline-flex", alignItems: "center", gap: 4, color: BLUE, fontSize: TYPE.label, fontWeight: 600, textDecoration: "none" }}
              >
                {prReference.label} <ExternalLink size={12} />
              </a>
            </span>
          )}
        </span>

        <span style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", paddingTop: 9, borderTop: `1px dashed ${UI.line}`, fontSize: TYPE.caption, color: UI.ink3 }}>
          <span style={{ minWidth: 0, display: "inline-flex", alignItems: "center", gap: 4 }}><MapPin size={12} /><span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{scope}</span></span>
          <span>판단 근거 {evidenceCount}개</span>
          {hasNewerAttempt && (
            <>
              <span title={attemptTitle} style={{ display: "inline-flex", alignItems: "center", gap: 5, color: TINT.warn.fg, fontWeight: 600 }}>
                <CircleAlert size={13} />{ISSUE_ATTEMPT_COPY.newerDetected}
              </span>
              <span style={{ color: TINT.warn.fg, fontWeight: 600 }}>{ISSUE_ATTEMPT_COPY.additionalAttempts(newerAttemptCount)}</span>
              {onOpenLatest && (
                <button
                  type="button"
                  className="product-focusable product-control"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onOpenLatest();
                  }}
                  style={{ position: "relative", zIndex: 3, pointerEvents: "auto", border: "none", borderRadius: 7, background: TINT.warn.bg, color: TINT.warn.fg, padding: "3px 8px", fontSize: TYPE.caption, fontWeight: 600, cursor: "pointer" }}
                >
                  {ISSUE_ATTEMPT_COPY.latestCorrelation}
                </button>
              )}
            </>
          )}
        </span>

        <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Pill tone={state.tone} label={state.label} />
        </span>
      </span>
    </motion.article>
  );
}

const EMPTY_PINNED_CORRELATION_IDS: readonly string[] = [];

export function IssuesSurface({ incidentClusterIds, recoveryProgressOverrides = new Map<string, RecoveryProgressOverride>(), pinnedIssueCorrelationIds = EMPTY_PINNED_CORRELATION_IDS, sessionRules: _sessionRules = [], onOpenRef, onOpenRca }: {
  incidentClusterIds: readonly string[]; recoveryProgressOverrides?: ReadonlyMap<string, RecoveryProgressOverride>; pinnedIssueCorrelationIds?: readonly string[]; sessionRules?: string[]; onOpenRef: (kind: string, name: string) => void; onOpenRca?: (i: RcaIncident) => void;
}) {
  const [tab, setTab] = useState("진행 중");
  const [severityFilter, setSeverityFilter] = useState<IssueSeverityFilter>("all");
  // 이슈 탭 — 실 RCA 이슈 큐(GET /api/dashboard/rca/issues, 홈 W2와 동일 소스).
  // 큐 항목이 관측 RCA 필드(원인/확신도/증거/AI 요약)를 이미 실어주므로 상세 드로어로 그대로 전달한다.
  const issues = useRcaIssueDetails(incidentClusterIds, RCA_DETAIL_REFRESH_MS, undefined, pinnedIssueCorrelationIds);
  const issueItems = issues.items;
  const activeIssues = issueItems.filter(isActiveRcaIssue);
  const resolvedIssues = issueItems.filter((issue) => !isActiveRcaIssue(issue));
  const critCount = activeIssues.filter((issue) => issue.severity === "critical").length;
  const warnCount = activeIssues.filter((issue) => issue.severity === "warning").length;
  const visibleIssues = tab === "해결됨"
    ? resolvedIssues
    : activeIssues.filter((issue) => severityFilter === "all" || issue.severity === severityFilter);
  const setRca = (iss: RcaIssueAttemptDetail) => onOpenRca?.({
    name: iss.resourceName ?? iss.correlationId.slice(0, 12),
    symptom: iss.symptom ?? iss.status,
    rawSymptom: iss.rawSymptom,
    cluster: iss.clusterId ?? "-",
    svc: iss.resourceName ?? (iss.resourceName ?? iss.correlationId.slice(0, 12)),
    ns: iss.namespace ?? "-",
    resourceKind: iss.resourceKind,
    correlationId: iss.correlationId,
    incidentId: iss.incidentId,
    currentSubject: iss.currentSubject,
    updatedAt: iss.updatedAt,
    status: iss.status,
    severity: iss.severity,
    rootCause: iss.rootCause,
    confidence: iss.confidence,
    supportingEvidence: iss.supportingEvidence,
    missingEvidence: iss.missingEvidence,
    situationSummary: iss.situationSummary,
    recommendedActionSummary: iss.recommendedActionSummary,
    evidenceSummary: iss.evidenceSummary,
    evidenceBundleSummary: iss.evidenceBundleSummary,
    recoveryReasonCode: iss.recoveryReasonCode,
    prUrl: iss.prUrl,
  });
  return (
    <Page
      title="이슈"
      icon={AlertTriangle}
      navigation={
        <SegmentedControl
          active={tab}
          ariaLabel="이슈 보기"
          indicatorId="ptab-이슈"
          items={[
            { value: "진행 중", label: "진행 중" },
            { value: "해결됨", label: "해결됨" },
            { value: "예방 점검", label: "예방 점검" },
          ]}
          onChange={setTab}
        />
      }
    >
      {tab === "진행 중" && (
        <IssueSeverityFilters active={severityFilter} criticalCount={critCount} warningCount={warnCount} onChange={setSeverityFilter} totalCount={activeIssues.length} />
      )}
      {issues.status === "stale" && (
        <span style={{ fontSize: TYPE.caption, color: TINT.warn.fg }}>
          최근 관측된 이슈를 표시하고 있습니다 · 재조회 대기
        </span>
      )}
      {tab !== "예방 점검" && (
        <div style={{ display: "grid", gap: 8 }}>
          {issues.status === "loading" && visibleIssues.length === 0 ? (
            <Card><div style={{ fontSize: TYPE.label, color: UI.ink3 }}>불러오는 중…</div></Card>
          ) : issues.status === "unavailable" && visibleIssues.length === 0 ? (
            <Card><div style={{ fontSize: TYPE.label, color: UI.ink3 }}>이슈를 불러오지 못했습니다.</div></Card>
          ) : visibleIssues.length === 0 ? (
            <Card><div style={{ fontSize: TYPE.label, color: UI.ink3 }}>{tab === "해결됨" ? "해결된 이슈가 없습니다." : severityFilter === "all" ? "진행 중인 이슈가 없습니다." : `${severityFilter === "critical" ? "장애" : "주의"} 이슈가 없습니다.`}</div></Card>
          ) : visibleIssues.map((iss) => {
            const resourceKind = iss.resourceKind;
            const resourceName = iss.resourceName;
            return (
              <IssueCard
                key={iss.correlationId}
                issue={iss}
                recoveryProgressOverride={recoveryProgressOverrides.get(iss.correlationId) ?? null}
                recoveryCompleted={!isActiveRcaIssue(iss)}
                onOpen={() => setRca(iss)}
                onOpenLatest={iss.latestAttempt ? () => setRca(iss.latestAttempt!) : null}
                onOpenTarget={resourceKind && resourceName ? () => onOpenRef(resourceKind, resourceName) : null}
              />
            );
          })}
        </div>
      )}
      {tab === "예방 점검" && <ChecksContent />}
    </Page>
  );
}

// ── 타임라인 /timeline (5.9 — P-21 문법 + 유형 필터 칩 P-22) ──
// UI-PHASE2-001 §2: 실 timeline API로 재배선. 활동 개요·문제 수·커버리지 공백은
// GET /api/timeline/capabilities + NDJSON snapshot → cursor SSE가 전체 권한
// 클러스터의 변경을 직접 전달하고, 고정 항목은 GET /api/timeline/pins에서 조회한다.
// 예전의 단발성 /api/changes 조회와 "라이브 스트림 미지원" 오판을 제거했다.
type TlCat = "전체" | "이슈" | "배포" | "구성";
function changeCat(kind: string): Exclude<TlCat, "전체"> {
  if (kind === "incident") return "이슈";
  if (kind === "deployment") return "배포";
  return "구성"; // inventory_event · gitops_change
}
function changeTone(severity: string): "ok" | "warn" | "crit" {
  if (severity === "critical") return "crit";
  if (severity === "warning") return "warn";
  return "ok";
}
export function TimelineSurface({
  workspaceId,
  clusterIds,
  onOpenRef: _onOpenRef,
}: {
  workspaceId: string | null;
  clusterIds: readonly string[];
  onOpenRef: (kind: string, name: string) => void;
}) {
  const [cat, setCat] = useState<TlCat>("전체");
  const [page, setPage] = useState(0);
  const feed = useChangeTimeline(workspaceId, clusterIds);
  const board = useTimelineBoard();
  const items = feed.events.map((e) => ({
    id: e.id,
    time: fromNow(e.occurredMs),
    tone: changeTone(e.severity),
    cat: changeCat(e.kind),
    title: e.title,
  }));
  const shown = cat === "전체" ? items : items.filter((i) => i.cat === cat);
  // 수백 개 변경을 한 프레임에 motion 노드로 만들면 진입 시 긴 작업과 레이아웃
  // 이동이 발생한다. 모든 항목은 보존하되 화면 DOM은 페이지당 60개로 제한한다.
  const pageSize = 60;
  const pageCount = Math.max(1, Math.ceil(shown.length / pageSize));
  const safePage = Math.min(page, pageCount - 1);
  const visibleItems = shown.slice(safePage * pageSize, (safePage + 1) * pageSize);
  const timelineObserved = feed.status === "ready"
    || feed.status === "partial"
    || feed.status === "stale";
  const problemCount = feed.events.filter(
    (event) => event.severity === "warning" || event.severity === "critical",
  ).length;
  const activityChips = Object.entries(
    feed.events.reduce<Record<string, number>>((counts, event) => {
      counts[event.activity] = (counts[event.activity] ?? 0) + 1;
      return counts;
    }, {}),
  ).map(([activity, count]) => ({ activity, count }));
  return (
    <Page title="타임라인" icon={Clock}>
      {/* 실 timeline/overview 파생 요약(대표 클러스터 스코프) + 실 timeline/pins 고정 수 */}
      <ChipRow chips={[
        { label: "이벤트", value: timelineObserved ? feed.events.length : "—" },
        { label: "문제", value: timelineObserved ? problemCount : "—", warn: timelineObserved && problemCount > 0 },
        { label: "커버리지 공백", value: timelineObserved ? feed.gaps.length : "—", warn: timelineObserved && feed.gaps.length > 0 },
        { label: "고정", value: board.pins.status === "ready" ? board.pins.items.length : "—" },
      ]} />
      {activityChips.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {activityChips.map((f) => (
            <span key={f.activity} style={segStyle}>{koLabel(f.activity)} <b style={numStyle}>{f.count}</b></span>
          ))}
        </div>
      )}
      <div style={{ display: "flex", gap: 6 }}>
        {(["전체", "배포", "이슈", "구성"] as const).map((c) => (
          <button key={c} className="product-focusable product-control" aria-selected={cat === c} onClick={() => { setCat(c); setPage(0); }}
            style={{ display: "flex", alignItems: "center", gap: 5, border: `1px solid ${cat === c ? blueA(0.45) : UI.line}`, background: cat === c ? blueA(0.07) : UI.card, color: cat === c ? BLUE : UI.ink2, borderRadius: 999, padding: "4px 13px", fontSize: TYPE.label, fontWeight: 600, cursor: "pointer" }}>{c}
            <span style={{ fontVariantNumeric: "tabular-nums", fontSize: TYPE.caption, color: cat === c ? BLUE : UI.ink3 }}>{c === "전체" ? items.length : items.filter((i) => i.cat === c).length}</span>
          </button>
        ))}
      </div>
      <Card pad={0}>
        <div style={{ height: 440, overflowY: "auto", padding: 15, scrollbarGutter: "stable" }}>
          {feed.status === "loading" ? (
            <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>불러오는 중…</span>
          ) : feed.status === "unavailable" ? (
            <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>타임라인을 불러오지 못했습니다.</span>
          ) : shown.length === 0 ? (
            <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>최근 24시간 내 관측된 변경 없음</span>
          ) : (
            <MiniTimeline items={visibleItems.map(({ cat: _c, ...it }) => it)} />
          )}
        </div>
        {shown.length > pageSize && (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 8, minHeight: 38, padding: "5px 12px", borderTop: `1px solid ${UI.line2}` }}>
            <span style={{ marginRight: "auto", fontSize: TYPE.caption, color: UI.ink3 }}>
              {safePage * pageSize + 1}–{Math.min((safePage + 1) * pageSize, shown.length)} / {shown.length}
            </span>
            <button type="button" className="product-focusable product-control" disabled={safePage === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}
              style={{ border: `1px solid ${UI.line}`, background: UI.card, color: safePage === 0 ? UI.ink3 : UI.ink2, borderRadius: 7, padding: "4px 9px", fontSize: TYPE.caption }}>이전</button>
            <button type="button" className="product-focusable product-control" disabled={safePage >= pageCount - 1} onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}
              style={{ border: `1px solid ${UI.line}`, background: UI.card, color: safePage >= pageCount - 1 ? UI.ink3 : UI.ink2, borderRadius: 7, padding: "4px 9px", fontSize: TYPE.caption }}>다음</button>
          </div>
        )}
      </Card>
      {/* 고정한 항목 — 실 GET /api/timeline/pins(서버 진실). 비면 정직한 빈 상태 */}
      <Card pad={0}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "11px 15px", borderBottom: `1px solid ${UI.line2}` }}>
          <Pin size={13} style={{ color: BLUE }} />
          <span style={{ fontSize: TYPE.label, fontWeight: 600, color: UI.ink3 }}>고정한 항목</span>
        </div>
        {board.pins.status === "loading" ? emptyRow("불러오는 중…")
          : board.pins.status === "unavailable" ? emptyRow("고정 항목을 불러오지 못했습니다.")
          : board.pins.status === "unsupported" ? emptyRow("고정 기능 없음")
          : board.pins.items.length === 0 ? emptyRow("고정한 항목 없음")
          : board.pins.items.map((p) => (
            <div key={p.pinId} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 15px", borderTop: `1px solid ${UI.line2}` }}>
              <Pill tone="info" label={p.kind === "resource" ? "리소스" : "애플리케이션"} />
              <span style={{ minWidth: 0, flex: 1 }}>
                <span style={{ display: "block", fontSize: TYPE.label, fontWeight: 600, fontFamily: MONO, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.label}</span>
                {p.sublabel && <span style={{ display: "block", fontSize: TYPE.caption, color: UI.ink3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.sublabel}</span>}
              </span>
            </div>
          ))}
      </Card>
      {/* 정직한 표기 — 변경/문제/커버리지는 보존 snapshot+cursor SSE, 고정은 서버 pin 원장이다. */}
      <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>
        {board.status === "unavailable"
          ? "타임라인 제어 정보를 불러오지 못했습니다"
          : `보존 snapshot ${feed.observedScopes}/${feed.totalScopes} · SSE ${feed.streamingScopes}/${feed.totalScopes}${feed.status === "stale" ? " · 재연결 중" : ""} · 관측 소스 ${board.selectedSourceMode ? koLabel(board.selectedSourceMode) : "—"}`}
      </span>
    </Page>
  );
}

// ── 점검 /checks (5.10 — 정책 결과, 대상 클릭=상세 시트) ──
// UI-PHASE2-001: 실 GET /api/checks/overview. 현 dev 계약은 결과/카탈로그 관측
// unavailable(collector 미통합)이며 실 스코프 커버리지만 제공. 미지원 점검을
// "통과"로 위조하지 않고 정직한 unavailable + 스코프 커버리지를 렌더한다.
function ChecksContent() {
  const checks = useChecksOverview();
  if (checks.status === "loading") {
    return <Card><span style={{ fontSize: TYPE.label, color: UI.ink3 }}>불러오는 중…</span></Card>;
  }
  if (checks.status === "error") {
    return <Card><span style={{ fontSize: TYPE.label, color: UI.ink3 }}>점검 정보를 불러오지 못했습니다.</span></Card>;
  }
  const availabilityLabel = (a: string | null) => a === "available" ? "관측됨" : a === "partial" ? "부분 관측" : "관측 안 됨";
  return (
    <>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <Pill tone={checks.resultAvailability === "available" ? "ok" : "warn"} label={`점검 결과 ${availabilityLabel(checks.resultAvailability)}`} />
        <Pill tone={checks.catalogAvailability === "available" ? "ok" : "warn"} label={`카탈로그 ${availabilityLabel(checks.catalogAvailability)}`} />
        <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>스코프 클러스터 {checks.scopes.length}개</span>
      </div>
      <Card>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "4px 2px" }}>
          <span style={{ fontSize: TYPE.section, fontWeight: 700, color: UI.ink2 }}>점검 결과 관측 안 됨</span>
          <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>점검 결과 없음</span>
          <ReasonNotes codes={checks.reasonCodes} />
        </div>
      </Card>
      <Card pad={0}>
        <div style={{ padding: "11px 15px", borderBottom: `1px solid ${UI.line2}`, fontSize: TYPE.label, fontWeight: 600, color: UI.ink3 }}>스코프 커버리지 · {availabilityLabel(checks.scopeAvailability)}</div>
        {checks.scopes.length === 0
          ? <div style={{ padding: "12px 15px", fontSize: TYPE.label, color: UI.ink3 }}>스코프에 포함된 클러스터가 없습니다.</div>
          : checks.scopes.map((s) => (
            <div key={s.clusterId} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, padding: "10px 15px", borderTop: `1px solid ${UI.line2}` }}>
              <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                <span style={{ fontSize: TYPE.label, fontWeight: 600, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.clusterId}</span>
                <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>{s.namespaces.length ? `${s.namespaces.length}개 네임스페이스` : "전체 네임스페이스"}</span>
              </span>
              <Pill tone={s.freshness === "live" ? "ok" : s.freshness === "disconnected" ? "crit" : "warn"} label={koLabel(s.freshness)} />
            </div>
          ))}
      </Card>
    </>
  );
}

// 이전 /checks 진입점은 호환성을 위해 남겨 두되, 주 내비게이션에서는 이슈의
// '예방 점검' 탭을 사용한다. 두 진입점은 동일한 실 계약과 콘텐츠를 공유한다.
export function ChecksSurface({ onOpenRef: _onOpenRef }: { onOpenRef: (kind: string, name: string) => void }) {
  return (
    <Page title="예방 점검" icon={ShieldCheck}>
      <ChecksContent />
    </Page>
  );
}

// ── 비용 /cost (UI-PHASE2-001: 홈 W7과 같은 useCostOverview 단일 소스) ──
// 현 dev 계약은 비용 관측 unavailable. 가짜 총액/노드 가격을 backfill하지 않고 정직 상태를 렌더한다.
export function CostSurface({ onOpenRef: _onOpenRef }: { onOpenRef: (kind: string, name: string) => void }) {
  const cost = useCostOverview();
  return (
    <Page title="비용" icon={Coins}>
      <Card>
        {cost.status === "loading" ? (
          <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>불러오는 중…</span>
        ) : cost.status === "error" ? (
          <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>비용을 불러오지 못했습니다.</span>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "6px 4px" }}>
            <span style={{ fontSize: TYPE.section, fontWeight: 700, color: UI.ink2 }}>비용 관측 데이터가 아직 없습니다</span>
            <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>현재 스코프에 대해 비용 관측이 아직 연동되지 않았습니다.</span>
            <ReasonNotes codes={cost.reasonCodes.length ? cost.reasonCodes : ["cost_observation_unavailable"]} />
          </div>
        )}
      </Card>
    </Page>
  );
}

// ── 설정 /settings (D20 — 전역 앱 설정만. 연결·클러스터 관리는 각자의 문맥에) ──
// UI-PHASE2-001 §3: 워크스페이스·계정은 실 GET /api/auth/session. 테마·언어는 실
// GET/PUT /api/settings(UiPreferences)로 저장한다(낙관적 UI, 실패 시 서버 진실로
// 롤백, CSRF는 api 레이어). 접근 프로필은 GET /api/settings/access, 자동 갱신
// 정책은 GET /api/refresh-policies 실 조회. 예전의 "미지원/변경할 수 없습니다/연결
// 상태 확인 불가" 오판 표기를 제거했다. 토스트 토글만 이 브라우저 로컬 설정으로 남긴다.
function Segmented<T extends string>({ value, options, onPick, disabled }: {
  value: T | null; options: { id: T; label: string }[]; onPick: (id: T) => void; disabled?: boolean;
}) {
  return (
    <div style={{ display: "flex", gap: 2, background: inkA(0.05), borderRadius: 9, padding: 2 }}>
      {options.map((o) => {
        const active = o.id === value;
        return (
          <button key={o.id} className="product-focusable product-control" aria-selected={active} onClick={() => { if (!disabled && !active) onPick(o.id); }} disabled={disabled}
            style={{ border: "none", background: active ? UI.card : "transparent", color: active ? UI.ink : UI.ink3, borderRadius: 7, padding: "4px 12px", fontSize: TYPE.label, fontWeight: 600, cursor: disabled ? "not-allowed" : active ? "default" : "pointer", boxShadow: active ? `0 1px 3px ${inkA(0.14)}` : "none", opacity: disabled ? 0.55 : 1 }}>
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
export function SettingsSurface({
  session,
  clusterId,
}: {
  session: SessionView;
  clusterId: string | null;
}) {
  const prefs = useUiPreferences();
  const refresh = useRefreshPolicies();
  const access = useSettingsAccess(clusterId);
  const [noise, setNoise] = useState(() => { try { return sessionStorage.getItem("opsia-demo-toast-crit-only") === "1"; } catch { return false; } });
  const toggleNoise = () => setNoise((v) => { const n = !v; try { sessionStorage.setItem("opsia-demo-toast-crit-only", n ? "1" : "0"); } catch { /* 데모 */ } return n; });
  const workspaceSub = session.status === "loading" ? "세션 확인 중…"
    : session.status === "error" ? "세션을 불러오지 못했습니다"
    : `${session.workspaceId ?? "—"}${session.authMode ? ` · ${koLabel(session.authMode)}` : ""}`;
  const accountName = session.displayName ?? session.email ?? session.userId ?? "—";
  const accountSub = session.status !== "ready" ? "—"
    : session.roles.length ? session.roles.map(koLabel).join(", ") : "역할 없음";
  const prefsReady = prefs.status === "ready";
  const prefsDisabled = !prefsReady || prefs.saving;
  const prefsSub = prefs.status === "loading" ? "환경설정 불러오는 중…"
    : prefs.status === "unavailable" ? "환경설정을 불러오지 못했습니다"
    : prefs.saveError ? "저장 실패 · 이전 값으로 되돌렸습니다"
    : prefs.saving ? "저장 중…"
    : "이 계정의 서버 저장 환경설정입니다";
  return (
    <Page title="설정" icon={Building2}>
      <Card pad={0}>
        <SettingsRow icon={Building2} title="워크스페이스" sub={workspaceSub}
          right={session.status === "ready" && session.roles.length ? <Mono dim>{koLabel(session.roles[0])}</Mono> : <Mono dim>—</Mono>} />
        <SettingsRow icon={Building2} title="계정" sub={accountSub}
          right={<span style={{ display: "flex", alignItems: "center", gap: 7 }}>
            <span style={{ fontSize: TYPE.label, color: UI.ink2, maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{accountName}</span>
            <span style={{ width: 26, height: 26, borderRadius: 999, background: inkA(0.08), display: "grid", placeItems: "center", fontSize: TYPE.label, fontWeight: 600, color: UI.ink2 }}>{session.status === "ready" ? sessionInitial(session) : "?"}</span>
          </span>} />
        <SettingsRow icon={Palette} title="테마" sub={prefsSub} right={
          <Segmented value={prefsReady ? prefs.theme : null} disabled={prefsDisabled}
            onPick={(theme) => prefs.save({ theme })}
            options={[{ id: "system", label: "시스템" }, { id: "light", label: "라이트" }, { id: "dark", label: "다크" }]} />} />
        <SettingsRow icon={Globe} title="언어" sub="인터페이스 표시 언어 · 서버에 저장됩니다" right={
          <Segmented value={prefsReady ? prefs.locale : null} disabled={prefsDisabled}
            onPick={(locale) => prefs.save({ locale })}
            options={[{ id: "en", label: "English" }, { id: "ko", label: "한국어" }]} />} />
        <SettingsRow icon={Bell} title="토스트 알림" sub="장애 사건만 토스트로 알림 · 벨에는 전부 기록 · 이 브라우저에만 저장됩니다" right={
          <button type="button" className="product-focusable" role="switch" aria-label="장애 사건 토스트 알림" aria-checked={noise} onClick={toggleNoise} style={{ width: 34, height: 20, borderRadius: 999, border: "none", cursor: "pointer", background: noise ? HP.ok : inkA(0.15), position: "relative", transition: "background .2s" }}>
            <span style={{ position: "absolute", top: 2, left: noise ? 16 : 2, width: 16, height: 16, borderRadius: 999, background: UI.card, boxShadow: `0 1px 3px ${inkA(0.3)}`, transition: "left .2s" }} />
          </button>} />
      </Card>
      {/* 접근 권한 — 실 GET /api/settings/access(대표 클러스터 스코프) */}
      <Card pad={0}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "11px 15px", borderBottom: `1px solid ${UI.line2}` }}>
          <Lock size={13} style={{ color: BLUE }} />
          <span style={{ fontSize: TYPE.label, fontWeight: 600, color: UI.ink3 }}>접근 권한{access.clusterId ? ` · ${access.clusterId}` : ""}</span>
        </div>
        {access.status === "loading" ? emptyRow("불러오는 중…")
          : access.status === "unavailable" ? emptyRow("접근 프로필을 불러오지 못했습니다.")
          : access.clusterId === null ? emptyRow("등록된 클러스터가 없어 접근 프로필을 조회할 수 없습니다.")
          : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: "12px 15px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                {access.roles.length ? access.roles.map((r) => <Pill key={r} tone="info" label={koLabel(r)} />) : <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>역할 없음</span>}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <span style={segStyle}>허용 권한 <b style={numStyle}>{access.allowedCount}/{access.permissionCount}</b></span>
                <Pill tone={access.kubernetesRulesObserved ? "ok" : "warn"} label={access.kubernetesRulesObserved ? "K8s 권한 관측됨" : "K8s 권한 미관측"} />
                {access.restrictedResourceCount !== null && access.restrictedResourceCount > 0 && (
                  <span style={segStyle}>제한 리소스 <b style={numStyle}>{access.restrictedResourceCount}</b></span>
                )}
              </div>
            </div>
          )}
      </Card>
      {/* 자동 갱신 정책 — 실 GET /api/refresh-policies(서버 소유 캐던스) */}
      <Card pad={0}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "11px 15px", borderBottom: `1px solid ${UI.line2}` }}>
          <RefreshCw size={13} style={{ color: BLUE }} />
          <span style={{ fontSize: TYPE.label, fontWeight: 600, color: UI.ink3 }}>자동 갱신 정책</span>
        </div>
        {refresh.status === "loading" ? emptyRow("불러오는 중…")
          : refresh.status === "unavailable" ? emptyRow("자동 갱신 정책을 불러오지 못했습니다.")
          : refresh.items.length === 0 ? emptyRow("등록된 정책 없음")
          : (
            <div style={{ maxHeight: 260, overflowY: "auto" }}>
              {refresh.items.map((p) => (
                <div key={p.key} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, padding: "9px 15px", borderTop: `1px solid ${UI.line2}` }}>
                  <Mono>{koLabel(p.key)}</Mono>
                  <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    {p.eventInvalidation && <Pill tone="info" label="이벤트 무효화" />}
                    <span style={{ fontSize: TYPE.caption, color: UI.ink3, fontVariantNumeric: "tabular-nums" }}>{p.staleAfterSeconds !== null ? `오래됨 ${p.staleAfterSeconds}초 · ` : ""}갱신 {p.refreshAfterSeconds}초</span>
                  </span>
                </div>
              ))}
            </div>
          )}
      </Card>
      {/* 정직한 표기 — 테마·언어는 실 PUT /api/settings 저장(낙관적, 실패 시 롤백).
          접근·자동 갱신 정책은 실 조회. 토스트 토글만 이 브라우저 로컬 데모 설정. */}
      <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>테마·언어는 서버에 저장됩니다(실패 시 이전 값으로 되돌림) · 접근·자동 갱신 정책은 실시간 조회</span>
      <span style={{ fontSize: TYPE.caption, fontFamily: MONO, color: UI.ink3 }}>Kyro Console 0.1.0{prefs.revision !== null ? ` · prefs r${prefs.revision}` : ""}</span>
    </Page>
  );
}

// ── 알림 /alerts — 벨 팝오버의 "전체 보기" 목적지: 발생 이벤트 + 규칙 + 채널 ──
// UI-PHASE2-001: 실 GET /api/alert-events(발생 이벤트) · /api/alert-rules(규칙) ·
// /api/alert-channels(채널). 이벤트 목록은 현재 비어 있어 정직한 "관측된 알림
// 없음"으로 렌더한다. 규칙 활성/비활성·생성은 CSRF가 필요한 mutation이므로 여기
// 서는 읽기 전용(상태 pill)으로만 표시하고 자동 변형은 하지 않는다.
function AlertEventPill({ tone, label, icon: Icon, iconColor }: { tone: AlertPresentationTone; label: string; icon: AlertEventIcon; iconColor: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: TYPE.caption, fontWeight: 600, borderRadius: 999, padding: "3px 9px", whiteSpace: "nowrap",
      color: tone === "ok" ? TINT.ok.fg : tone === "warn" ? TINT.warn.fg : tone === "crit" ? TINT.crit.fg : TINT.blue.fg,
      background: tone === "ok" ? TINT.ok.bg : tone === "warn" ? TINT.warn.bg : tone === "crit" ? critA(0.09) : blueA(0.08),
      border: `1px solid ${tone === "ok" ? TINT.ok.bd : tone === "warn" ? TINT.warn.bd : tone === "crit" ? critA(0.3) : blueA(0.25)}` }}>
      <Icon size={11} style={{ color: iconColor, flexShrink: 0 }} />
      {label}
    </span>
  );
}

function AlertEventDetail({ event, onClose, onOpenRef, topInset, leftInset, rightInset }: {
  event: AlertEventView;
  onClose: () => void;
  onOpenRef: (kind: string, name: string) => void;
  topInset: number;
  leftInset: number;
  rightInset: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const presentation = alertEventPresentation(event);
  const detailSection = (title: string, children: ReactNode) => (
    <section style={{ display: "grid", gap: 10, border: `1px solid ${UI.line}`, borderRadius: RADIUS.card, background: UI.card, padding: SPACE.card }}>
      <h2 style={{ margin: 0, fontSize: TYPE.section, fontWeight: 700, color: UI.heading }}>{title}</h2>
      {children}
    </section>
  );
  const detailRows = (rows: Array<[string, ReactNode]>) => (
    <dl style={{ display: "grid", gridTemplateColumns: "108px minmax(0, 1fr)", gap: "8px 12px", margin: 0, fontSize: TYPE.label, lineHeight: 1.5 }}>
      {rows.map(([label, value]) => (
        <Fragment key={label}>
          <dt style={{ color: UI.ink3 }}>{label}</dt>
          <dd style={{ minWidth: 0, margin: 0, color: UI.ink, overflowWrap: "anywhere" }}>{value}</dd>
        </Fragment>
      ))}
    </dl>
  );
  return (
    <DetailDrawer
      ariaLabel={`${event.kind} ${event.name} 경보 상세`}
      bodyStyle={{ display: "flex", flexDirection: "column", gap: SPACE.stack, padding: SPACE.card }}
      expanded={expanded}
      header={(
        <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
          <span style={{ width: 34, height: 34, borderRadius: 10, background: presentation.tone === "crit" ? critA(0.09) : presentation.tone === "warn" ? TINT.warn.bg : blueA(0.08), display: "grid", placeItems: "center", flexShrink: 0 }}>
            <presentation.Icon size={17} style={{ color: presentation.color }} />
          </span>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap" }}>
              <span style={{ fontSize: TYPE.section, fontWeight: 700, color: UI.ink }}>{event.kind} · {event.name}</span>
              <Pill tone={alertSeverityTone(event.severity)} label={koLabel(event.severity)} />
            </div>
            <div style={{ marginTop: 3, fontFamily: MONO, fontSize: TYPE.caption, color: UI.ink3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {event.cluster}{event.namespace ? ` · ${event.namespace}` : ""}
            </div>
          </div>
        </div>
      )}
      leftInset={leftInset}
      onClose={onClose}
      onExpandedChange={setExpanded}
      rightInset={rightInset}
      topInset={topInset}
    >
      {detailSection("경보 개요", detailRows([
        ["상태", koLabel(event.status)],
        ["소스", event.source],
        ["규칙", event.ruleName ?? "관측 안 됨"],
        ["규칙 ID", <span style={{ fontFamily: MONO }}>{event.ruleId ?? "—"}</span>],
        ["발생", fromNow(event.firedAt)],
        ["해결", fromNow(event.resolvedAt)],
        ["인시던트", <span style={{ fontFamily: MONO }}>{event.incidentId ?? "연결 안 됨"}</span>],
      ]))}
      {detailSection("측정값", detailRows([
        ["관측값", event.observedValue !== null ? String(event.observedValue) : "관측 안 됨"],
        ["임계치", event.threshold !== null ? String(event.threshold) : "관측 안 됨"],
      ]))}
      {detailSection("처리 이력", detailRows([
        ["확인", event.acknowledgedAt ? `${fromNow(event.acknowledgedAt)}${event.acknowledgedBy ? ` · ${event.acknowledgedBy}` : ""}` : "확인 기록 없음"],
        ["승격", event.promotedAt ? `${fromNow(event.promotedAt)}${event.promotedBy ? ` · ${event.promotedBy}` : ""}` : "승격 기록 없음"],
      ]))}
      {detailSection("증거", event.evidence.length === 0 ? (
        <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>관측된 증거 없음</span>
      ) : (
        <div style={{ display: "grid", gap: 8 }}>
          {event.evidence.map((evidence, index) => (
            <div key={`${evidence.type}-${index}`} style={{ display: "grid", gap: 5, border: `1px solid ${UI.line2}`, borderRadius: RADIUS.control, background: UI.bg2, padding: "9px 11px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ minWidth: 0, flex: 1, fontSize: TYPE.label, fontWeight: 600, color: UI.ink }}>{evidence.type}</span>
                {evidence.observed_at && <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>{fromNow(evidence.observed_at)}</span>}
              </div>
              {(evidence.metric || evidence.value !== null) && (
                <span style={{ fontFamily: MONO, fontSize: TYPE.caption, color: UI.ink2 }}>
                  {[evidence.metric, evidence.value !== null ? String(evidence.value) : null].filter(Boolean).join(" · ")}
                </span>
              )}
              {evidence.summary && <span style={{ fontSize: TYPE.label, color: UI.ink2, lineHeight: 1.5 }}>{evidence.summary}</span>}
              {evidence.subject && (
                <button type="button" className="product-focusable product-control" onClick={() => onOpenRef(evidence.subject!.kind, evidence.subject!.name)}
                  style={{ justifySelf: "start", border: "none", background: "transparent", padding: 0, fontFamily: MONO, fontSize: TYPE.caption, color: BLUE, cursor: "pointer" }}>
                  {evidence.subject.kind} · {evidence.subject.name} 열기
                </button>
              )}
              {evidence.link && <span style={{ fontFamily: MONO, fontSize: TYPE.caption, color: UI.ink3, overflowWrap: "anywhere" }}>{evidence.link}</span>}
            </div>
          ))}
        </div>
      ))}
      <button type="button" className="product-focusable product-action" onClick={() => onOpenRef(event.kind, event.name)}
        style={{ alignSelf: "flex-start", border: "none", borderRadius: RADIUS.control, background: BLUE, color: UI.card, padding: "7px 12px", fontSize: TYPE.label, fontWeight: 600, cursor: "pointer" }}>
        대상 리소스 상세 열기
      </button>
    </DetailDrawer>
  );
}

export function AlertsSurface({ events, selectedEventId = null, onSelectedEventIdChange, onOpenRef, topInset = 57, leftInset = 208, rightInset = 0 }: {
  events: AlertEventsFeed;
  selectedEventId?: string | null;
  onSelectedEventIdChange?: (eventId: string | null) => void;
  onOpenRef: (kind: string, name: string) => void;
  topInset?: number;
  leftInset?: number;
  rightInset?: number;
}) {
  const rules = useAlertRules();
  const channels = useAlertChannels();
  const selectedEvent = events.items.find((event) => event.eventId === selectedEventId) ?? null;
  const evCols: [string, string][] = [["심각도", "88px"], ["대상", "minmax(220px,1.8fr)"], ["규칙", "minmax(90px,0.8fr)"], ["상태", "80px"], ["발생", "minmax(70px,0.5fr)"]];
  const ruleCols: [string, string][] = [["규칙", "minmax(160px,1.5fr)"], ["조건", "minmax(140px,1.2fr)"], ["심각도", "minmax(80px,0.6fr)"], ["채널", "56px"], ["활성", "72px"]];
  const chCols: [string, string][] = [["채널", "minmax(160px,1.5fr)"], ["종류", "minmax(90px,0.8fr)"], ["최소 심각도", "minmax(90px,0.8fr)"], ["활성", "72px"]];
  const firing = events.status === "ready" ? events.items.filter((e) => e.status === "firing").length : "—";
  return (
    <Page title="알림" icon={Bell}>
      <ChipRow chips={[
        { label: "발생 중", value: firing, crit: typeof firing === "number" && firing > 0 },
        { label: "이벤트", value: events.status === "ready" ? events.items.length : "—" },
        { label: "규칙", value: rules.status === "ready" ? rules.items.length : "—" },
        { label: "채널", value: channels.status === "ready" ? channels.items.length : "—" },
      ]} />
      <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>
        이벤트 채널 · {events.transport === "sse"
          ? "실시간 연결"
          : events.transport === "http"
            ? "스냅샷"
            : events.transport === "stale"
              ? "최근 관측값 · 재연결 중"
              : "연결 중"}
      </span>
      <Card pad={0}>
        <div style={{ padding: "11px 15px", borderBottom: `1px solid ${UI.line2}`, fontSize: TYPE.label, fontWeight: 600, color: UI.ink3 }}>발생 이벤트</div>
        <THead cols={evCols} />
        {events.status === "loading" ? emptyRow("불러오는 중…")
          : events.status === "unavailable" ? emptyRow("알림 이벤트를 불러오지 못했습니다.")
          : events.items.length === 0 ? emptyRow("관측된 알림 없음")
          : events.items.map((n, i) => {
            const presentation = alertEventPresentation(n);
            return (
              <TRow key={n.eventId} cols={evCols} i={i} onClick={() => onSelectedEventIdChange?.(n.eventId)} cells={[
                <AlertEventPill key="s" tone={presentation.tone} label={koLabel(n.severity)} icon={presentation.Icon} iconColor={presentation.color} />,
                <span key="t" style={{ fontSize: TYPE.label, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{n.kind} · {n.name}{n.namespace ? ` · ${n.namespace}` : ""}</span>,
                <Mono key="r" dim>{n.ruleName ?? "—"}</Mono>,
                <span key="st" style={{ fontSize: TYPE.label, color: UI.ink3 }}>{koLabel(n.status)}</span>,
                <Mono key="w" dim>{fromNow(n.firedAt)}</Mono>,
              ]} />
            );
          })}
      </Card>
      <Card pad={0}>
        <div style={{ padding: "11px 15px", borderBottom: `1px solid ${UI.line2}`, fontSize: TYPE.label, fontWeight: 600, color: UI.ink3 }}>알림 규칙</div>
        <THead cols={ruleCols} />
        {rules.status === "loading" ? emptyRow("불러오는 중…")
          : rules.status === "unavailable" ? emptyRow("알림 규칙을 불러오지 못했습니다.")
          : rules.items.length === 0 ? emptyRow("등록된 규칙 없음")
          : rules.items.map((r, i) => (
            <TRow key={r.ruleId} cols={ruleCols} i={i} cells={[
              <Mono key="n">{r.name}</Mono>,
              <span key="c" style={{ fontSize: TYPE.label, color: UI.ink2, fontFamily: MONO }}>{r.metric} {r.comparator} {r.threshold}</span>,
              <Pill key="s" tone={alertSeverityTone(r.severity)} label={koLabel(r.severity)} />,
              <Mono key="ch">{r.channels.length}</Mono>,
              <Pill key="e" tone={r.enabled ? "ok" : "info"} label={r.enabled ? "활성" : "중지"} />,
            ]} />
          ))}
      </Card>
      <Card pad={0}>
        <div style={{ padding: "11px 15px", borderBottom: `1px solid ${UI.line2}`, fontSize: TYPE.label, fontWeight: 600, color: UI.ink3 }}>알림 채널</div>
        <THead cols={chCols} />
        {channels.status === "loading" ? emptyRow("불러오는 중…")
          : channels.status === "unavailable" ? emptyRow("알림 채널을 불러오지 못했습니다.")
          : channels.items.length === 0 ? emptyRow("등록된 채널 없음")
          : channels.items.map((c, i) => (
            <TRow key={c.channelId} cols={chCols} i={i} cells={[
              <Mono key="n">{c.name}</Mono>,
              <span key="k" style={{ fontSize: TYPE.label, color: UI.ink2 }}>{c.kind}</span>,
              <span key="m" style={{ fontSize: TYPE.label, color: UI.ink3 }}>{koLabel(c.minSeverity)}</span>,
              <Pill key="e" tone={c.enabled ? "ok" : "info"} label={c.enabled ? "활성" : "중지"} />,
            ]} />
          ))}
      </Card>
      {selectedEvent && (
        <AlertEventDetail
          event={selectedEvent}
          onClose={() => onSelectedEventIdChange?.(null)}
          onOpenRef={onOpenRef}
          topInset={topInset}
          leftInset={leftInset}
          rightInset={rightInset}
        />
      )}
    </Page>
  );
}

// ── AI 대화 /ai — AI 패널 대화 내역 모아보기(행 클릭·새 대화 = 패널 열기, 죽은 컨트롤 없음) ──
// UI-PHASE2-001: 실 GET /api/ai/conversations(useAiConversations 재사용). 서버가
// 돌려준 대화만 렌더하고, 없으면 정직한 빈 상태, 실패는 정직한 unavailable로 둔다.
export function AiHistorySurface({ onOpenPanel }: { onOpenPanel: () => void }) {
  const feed = useAiConversations();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [deletePendingId, setDeletePendingId] = useState<string | null>(null);
  const [deleteAllConfirm, setDeleteAllConfirm] = useState(false);
  const [deleteAllPending, setDeleteAllPending] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  // 행 클릭 시 선택한 대화 id로 상세(GET /api/ai/conversations/{id})를 조회해 실 Q&A를 렌더한다.
  const detail = useConversationDetail(selectedId);
  const cols: [string, string][] = [
    ["대화", "minmax(260px,2fr)"],
    ["시간", "minmax(80px,0.6fr)"],
    ["관리", "minmax(104px,auto)"],
  ];
  const deleteConversation = async (conversationId: string) => {
    setDeletePendingId(conversationId);
    setDeleteError(null);
    try {
      await deleteStoredAiConversation(conversationId);
      if (selectedId === conversationId) setSelectedId(null);
      setDeleteConfirmId(null);
    } catch {
      setDeleteError("대화를 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setDeletePendingId(null);
    }
  };
  const deleteAllConversations = async () => {
    setDeleteAllPending(true);
    setDeleteError(null);
    try {
      await deleteAllStoredAiConversations();
      setSelectedId(null);
      setDeleteAllConfirm(false);
    } catch {
      setDeleteError("전체 대화를 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setDeleteAllPending(false);
    }
  };
  const neutralButton: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    border: `1px solid ${UI.line}`,
    borderRadius: 8,
    background: UI.card,
    color: UI.ink2,
    padding: "6px 11px",
    fontSize: TYPE.caption,
    fontWeight: 600,
    cursor: "pointer",
  };

  if (selectedId !== null) {
    const selected = feed.items.find((c) => c.id === selectedId);
    // user turn은 question 필드에, assistant turn은 parts(text)에 실 내용이 담긴다.
    const turnText = (turn: (typeof detail.turns)[number]) =>
      turn.role === "user"
        ? (turn.question ?? "")
        : (turn.parts ?? [])
            .filter((part): part is Extract<typeof part, { kind: "text" }> => part.kind === "text")
            .map((part) => part.markdown)
            .join("\n");
    return (
      <Page title={selected?.title ?? "AI 대화"} icon={Sparkles}
        action={(
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <button className="product-focusable product-control" onClick={() => setSelectedId(null)} style={{ ...neutralButton, fontSize: TYPE.label, padding: "6px 13px" }}>← 목록</button>
            {deleteConfirmId === selectedId ? (
              <>
                <button className="product-focusable product-control" disabled={deletePendingId === selectedId} onClick={() => void deleteConversation(selectedId)} style={{ ...neutralButton, borderColor: TINT.crit.bd, color: TINT.crit.fg, background: TINT.crit.bg }}>
                  {deletePendingId === selectedId ? "삭제 중…" : "삭제 확인"}
                </button>
                <button className="product-focusable product-control" disabled={deletePendingId === selectedId} onClick={() => setDeleteConfirmId(null)} style={neutralButton}>취소</button>
              </>
            ) : (
              <button aria-label="대화 삭제" className="product-focusable product-control" onClick={() => setDeleteConfirmId(selectedId)} style={{ ...neutralButton, padding: 7 }} title="대화 삭제"><Trash2 size={15} /></button>
            )}
          </div>
        )}>
        {deleteError && <div role="alert" style={{ marginBottom: 10, color: TINT.crit.fg, fontSize: TYPE.caption }}>{deleteError}</div>}
        <Card>
          {detail.status === "loading" ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {[0, 1, 2].map((n) => (
                <div key={n} style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                  <span style={{ width: 36, height: 10, borderRadius: 4, background: inkA(0.07) }} />
                  <span style={{ width: n % 2 ? "62%" : "88%", height: 13, borderRadius: 5, background: inkA(0.05) }} />
                  <span style={{ width: "46%", height: 13, borderRadius: 5, background: inkA(0.05) }} />
                </div>
              ))}
            </div>
          )
            : detail.status === "unavailable" ? <div style={{ fontSize: TYPE.label, color: UI.ink3, padding: "6px 2px" }}>이 대화의 상세 이력은 관측되지 않습니다.</div>
            : detail.turns.length === 0 ? <div style={{ fontSize: TYPE.label, color: UI.ink3, padding: "6px 2px" }}>대화 메시지가 없습니다.</div>
            : <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                {detail.turns.map((turn, i) => (
                  <div key={i} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    <span style={{ fontSize: TYPE.caption, fontWeight: 600, color: turn.role === "user" ? BLUE : UI.ink2 }}>{turn.role === "user" ? "질문" : "응답"}</span>
                    <div style={{ fontSize: TYPE.label, color: turnText(turn) ? UI.ink : UI.ink3, whiteSpace: "pre-wrap", lineHeight: 1.65 }}>{turnText(turn) || "(내용 없음)"}</div>
                  </div>
                ))}
              </div>}
        </Card>
      </Page>
    );
  }

  return (
    <Page title="AI 대화" icon={Sparkles}
      action={(
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {feed.items.length > 0 && (deleteAllConfirm ? (
            <>
              <button className="product-focusable product-control" disabled={deleteAllPending} onClick={() => void deleteAllConversations()} style={{ ...neutralButton, borderColor: TINT.crit.bd, color: TINT.crit.fg, background: TINT.crit.bg }}>
                {deleteAllPending ? "삭제 중…" : `전체 ${feed.items.length}개 삭제`}
              </button>
              <button className="product-focusable product-control" disabled={deleteAllPending} onClick={() => setDeleteAllConfirm(false)} style={neutralButton}>취소</button>
            </>
          ) : (
            <button className="product-focusable product-control" onClick={() => setDeleteAllConfirm(true)} style={neutralButton}><Trash2 size={14} />전체 삭제</button>
          ))}
          <button className="product-focusable product-action" onClick={onOpenPanel} style={{ display: "flex", alignItems: "center", gap: 6, border: "none", background: BLUE, color: UI.card, borderRadius: 9, padding: "6px 13px", fontSize: TYPE.label, fontWeight: 600, cursor: "pointer" }}>새 대화</button>
        </div>
      )}>
      {deleteError && <div role="alert" style={{ color: TINT.crit.fg, fontSize: TYPE.caption }}>{deleteError}</div>}
      <Card pad={0}>
        <THead cols={cols} />
        {feed.status === "loading" ? emptyRow("불러오는 중…")
          : feed.status === "unavailable" ? emptyRow("대화 내역을 불러오지 못했습니다.")
          : feed.items.length === 0 ? emptyRow("저장된 AI 대화 없음")
          : feed.items.map((c, i) => (
            <motion.div key={c.id} initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SOFT, delay: Math.min(i, 8) * 0.04 }}
              style={{ display: "grid", gridTemplateColumns: cols.map(([, width]) => width).join(" "), gap: 12, alignItems: "center", borderBottom: `1px solid ${UI.line2}`, padding: "8px 14px" }}>
              <button type="button" className="product-focusable rrow" onClick={() => setSelectedId(c.id)}
                style={{ minWidth: 0, border: "none", background: "transparent", padding: "2px 0", textAlign: "left", cursor: "pointer" }}>
                <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                  <span style={{ fontSize: TYPE.label, fontWeight: 600, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.title}</span>
                  <span style={{ fontSize: TYPE.caption, fontFamily: MONO, color: UI.ink3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.id}</span>
                </span>
              </button>
              <Mono dim>{fromNow(c.updatedAt)}</Mono>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 6 }}>
                {deleteConfirmId === c.id ? (
                  <>
                    <button type="button" className="product-focusable product-control" disabled={deletePendingId === c.id} onClick={() => void deleteConversation(c.id)}
                      style={{ ...neutralButton, borderColor: TINT.crit.bd, color: TINT.crit.fg, background: TINT.crit.bg }}>
                      {deletePendingId === c.id ? "삭제 중…" : "삭제"}
                    </button>
                    <button type="button" className="product-focusable product-control" disabled={deletePendingId === c.id} onClick={() => setDeleteConfirmId(null)} style={neutralButton}>취소</button>
                  </>
                ) : (
                  <button type="button" aria-label={`${c.title} 삭제`} className="product-focusable product-control" onClick={() => setDeleteConfirmId(c.id)}
                    style={{ ...neutralButton, padding: 7 }} title="대화 삭제"><Trash2 size={14} /></button>
                )}
              </div>
            </motion.div>
          ))}
      </Card>
    </Page>
  );
}
