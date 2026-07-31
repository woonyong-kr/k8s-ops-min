import { useEffect, useState } from "react";
import { GitBranch, ListChecks, Package, Rocket } from "lucide-react";

import type {
  ApplicationDetailEndpointItem,
  ApplicationDriftEndpoint,
} from "../api/application-catalog-schemas";
import type { GitOpsApplicationDetailEndpoint } from "../api/gitops-application-detail-schemas";
import { BLUE, ELEV, HP, MONO, RADIUS, SPACE, TINT, TYPE, UI, blueA, critA, inkA } from "./theme";
import { DetailDrawer } from "./DetailDrawer";
import { statusLabel } from "./statusLabel";
import {
  useApplicationChangeEvents,
  useApplicationDetail,
  useHelmReleaseDetail,
  type ApplicationChangeEventView,
  type DetailSection,
  type HelmReleaseIdentity,
} from "./deployDetailFeed";
import type { ApplicationRunView, DeployFeedStatus, WorkflowStepView } from "./deployFeed";

// ── 배포 상세 패널 — 전역 레이어 계약: scrim 70 / panel 71 (unified DetailOverlay와 동일층)
// 원칙: 모든 표시는 실제 계약 응답에서만 파생한다. 실패/미연동 섹션은 정직한
// 문구와 함께 그대로 노출하고, 동작하지 않는 컨트롤은 그리지 않는다(가짜 컨트롤 금지).

export type DeployDetailTarget =
  | { kind: "application"; applicationId: string; name: string }
  | { kind: "helm"; identity: HelmReleaseIdentity; displayNamespace: string }
  | { kind: "run"; workflowRunId: string }
  | { kind: "releaseRun"; runId: string }
  | { kind: "releasePlan"; planKey: string };

interface PanelInsets {
  topInset: number;
  leftInset: number;
  rightInset: number;
}

// ── 소형 로컬 부품 — devpreview-surfaces와 같은 토큰 문법(순환 import 회피용 사본,
//    Phase 5에서 공용 모듈로 수렴 예정) ──────────────────────────────────────

function fromNow(input: string | null): string {
  if (input === null) return "—";
  const ms = Date.parse(input);
  if (!Number.isFinite(ms)) return "—";
  const diff = Date.now() - ms;
  if (diff < 0) return "방금";
  const min = Math.floor(diff / 60000);
  if (min < 1) return "방금";
  if (min < 60) return `${min}분 전`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}시간 전`;
  return `${Math.floor(hr / 24)}일 전`;
}

type PillTone = "ok" | "warn" | "crit" | "info" | "gray";

function StatusPill({ tone, label }: { tone: PillTone; label: string }) {
  const palette = tone === "ok" ? TINT.ok
    : tone === "warn" ? TINT.warn
    : tone === "crit" ? { fg: TINT.crit.fg, bg: critA(0.09), bd: critA(0.3) }
    : tone === "info" ? { fg: TINT.blue.fg, bg: blueA(0.08), bd: blueA(0.25) }
    : TINT.gray;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: TYPE.caption, fontWeight: 600, borderRadius: 999, padding: "3px 9px", whiteSpace: "nowrap", color: palette.fg, background: palette.bg, border: `1px solid ${palette.bd}` }}>
      <span style={{ width: 5, height: 5, borderRadius: 999, background: tone === "info" ? BLUE : tone === "gray" ? UI.ink3 : HP[tone] }} />
      {label}
    </span>
  );
}

function statusTone(raw: string | null): PillTone {
  const value = raw?.trim().toLowerCase() ?? "";
  if (["succeeded", "synced", "healthy", "ready", "deployed", "in_sync"].includes(value)) return "ok";
  if (["failed", "degraded", "error", "critical", "unhealthy", "drifted"].includes(value)) return "crit";
  if (["pending", "progressing", "running", "waiting_for_approval", "superseded"].includes(value)) return "info";
  if (value === "") return "gray";
  return "warn";
}

function statusView(raw: string | null): React.ReactNode {
  if (raw === null || raw.trim() === "") return <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>관측 안 됨</span>;
  return <StatusPill tone={statusTone(raw)} label={statusLabel(raw)} />;
}

function Section({ title, aside, children }: { title: string; aside?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section style={{ border: `1px solid ${UI.line}`, borderRadius: RADIUS.card, background: UI.card, overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 14px", borderBottom: `1px solid ${UI.line2}`, background: UI.bg2 }}>
        <span style={{ fontSize: TYPE.caption, fontWeight: 700, letterSpacing: "0.04em", color: UI.ink2 }}>{title}</span>
        <span style={{ marginLeft: "auto" }}>{aside}</span>
      </div>
      <div style={{ padding: SPACE.stack, display: "flex", flexDirection: "column", gap: 8 }}>{children}</div>
    </section>
  );
}

function KV({ label, children, mono = false }: { label: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "112px minmax(0, 1fr)", gap: 10, alignItems: "baseline" }}>
      <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>{label}</span>
      <span style={{ minWidth: 0, fontSize: TYPE.label, color: UI.ink, fontFamily: mono ? MONO : undefined, overflowWrap: "anywhere" }}>{children}</span>
    </div>
  );
}

function gapText(value: string | null): React.ReactNode {
  return value !== null && value.trim() !== ""
    ? value
    : <span style={{ color: UI.ink3 }}>—</span>;
}

function SectionState({ status, emptyLabel }: { status: DeployFeedStatus; emptyLabel: string }) {
  return (
    <span style={{ fontSize: TYPE.label, color: UI.ink3, padding: "2px 0" }}>
      {status === "loading" ? "불러오는 중…" : status === "unavailable" ? emptyLabel : "관측된 항목 없음"}
    </span>
  );
}

// ── 패널 셸 ──────────────────────────────────────────────────────────────────

function PanelShell({ icon: Icon, title, subtitle, onClose, insets, children }: {
  icon: React.ComponentType<{ size?: number; style?: React.CSSProperties }>;
  title: string;
  subtitle: string | null;
  onClose: () => void;
  insets: PanelInsets;
  children: React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <DetailDrawer
      ariaLabel={title}
      bodyStyle={{
        display: "flex",
        flexDirection: "column",
        gap: SPACE.stack,
        padding: SPACE.card,
      }}
      expanded={expanded}
      header={(
        <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
          <span style={{ width: 30, height: 30, borderRadius: 9, background: blueA(0.09), display: "grid", placeItems: "center", flexShrink: 0 }}>
            <Icon size={16} style={{ color: BLUE }} />
          </span>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontSize: TYPE.section, fontWeight: 700, letterSpacing: "-0.01em", color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</div>
            {subtitle && <div title={subtitle} style={{ marginTop: 1, fontFamily: MONO, fontSize: TYPE.caption, color: UI.ink3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{subtitle}</div>}
          </div>
        </div>
      )}
      leftInset={insets.leftInset}
      onClose={onClose}
      onExpandedChange={setExpanded}
      rightInset={insets.rightInset}
      topInset={insets.topInset}
    >
      {children}
    </DetailDrawer>
  );
}

// ── 워크플로우 스텝 타임라인 — 서버가 기록한 스텝 evidence만 그린다 ─────────

function stepDotColor(status: string | null): string {
  const tone = statusTone(status);
  if (tone === "ok") return HP.ok;
  if (tone === "crit") return HP.crit;
  if (tone === "info") return BLUE;
  if (tone === "warn") return HP.warn;
  return HP.pending;
}

function StepTimeline({ steps }: { steps: WorkflowStepView[] }) {
  if (steps.length === 0) return <SectionState status="ready" emptyLabel="" />;
  return (
    <ol style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column" }}>
      {steps.map((step, index) => (
        <li key={`${step.name}-${index}`} style={{ display: "flex", gap: 10, minWidth: 0 }}>
          <span aria-hidden="true" style={{ display: "flex", flexDirection: "column", alignItems: "center", flexShrink: 0, width: 10 }}>
            <span style={{ width: 8, height: 8, borderRadius: 999, background: stepDotColor(step.status), marginTop: 5, flexShrink: 0 }} />
            {index < steps.length - 1 && <span style={{ flex: 1, width: 1, background: UI.line2, marginTop: 3, marginBottom: 3, minHeight: 10 }} />}
          </span>
          <div style={{ minWidth: 0, flex: 1, paddingBottom: index < steps.length - 1 ? 10 : 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
              <span style={{ fontSize: TYPE.label, fontWeight: 600, fontFamily: MONO, color: UI.ink }}>{step.name}</span>
              {statusView(step.status)}
              <span style={{ marginLeft: "auto", flexShrink: 0, fontSize: TYPE.caption, color: UI.ink3 }}>{fromNow(step.updatedAt)}</span>
            </div>
            {step.message && (
              <div title={step.message} style={{ marginTop: 2, fontSize: TYPE.caption, color: UI.ink2, lineHeight: 1.5, overflowWrap: "anywhere" }}>{step.message}</div>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

// ── 애플리케이션 상세 ────────────────────────────────────────────────────────

function readText(record: Record<string, unknown> | null, key: string): string | null {
  const value = record?.[key];
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

const GITOPS_REASON_KO: Record<string, string> = {
  binding_scope_unavailable: "배포 바인딩 범위가 아직 확인되지 않았습니다",
  multiple_target_scopes: "여러 대상 범위가 감지되어 단일 범위를 확정할 수 없습니다",
  live_observation_not_integrated: "라이브 관측이 아직 연동되지 않았습니다",
  source_revision_unavailable: "소스 리비전 관측이 아직 없습니다",
  workflow_operation_unobserved: "워크플로우 작업 관측이 아직 없습니다",
  provider_operation_not_integrated: "공급자 작업 연동이 아직 없습니다",
  not_authorized: "권한이 없습니다",
  operation_in_progress: "작업이 진행 중입니다",
  provider_refresh_not_integrated: "공급자 refresh 연동이 아직 없습니다",
  provider_sync_not_integrated: "공급자 sync 연동이 아직 없습니다",
};

function gitOpsReason(code: string | null): string {
  return code === null ? "사유가 보고되지 않았습니다" : GITOPS_REASON_KO[code] ?? "관측 데이터가 아직 없습니다";
}

function GitOpsSection({ section }: { section: DetailSection<GitOpsApplicationDetailEndpoint["application"]> }) {
  const detail = section.data;
  if (detail === null) {
    return (
      <Section title="GitOps">
        <SectionState status={section.status} emptyLabel="GitOps 상세를 불러오지 못했습니다." />
      </Section>
    );
  }
  const scope = detail.scope.scope;
  return (
    <Section title="GitOps" aside={scope && <StatusPill tone={scope.freshness === "live" ? "ok" : scope.freshness === "disconnected" ? "crit" : "warn"} label={statusLabel(scope.freshness)} />}>
      <KV label="리소스" mono>{detail.resource.kind} · {gapText(detail.resource.namespace)} / {detail.resource.name}</KV>
      <KV label="클러스터" mono>{scope ? scope.cluster_id : gitOpsReason(detail.scope.reason_code)}</KV>
      <KV label="저장소" mono>{gapText(detail.source.repository_ref)}</KV>
      <KV label="브랜치" mono>{gapText(detail.source.default_branch)}</KV>
      <KV label="매니페스트" mono>{gapText(detail.source.manifest_path)}</KV>
      <KV label="선언/라이브 비교">
        {detail.desired_live_diff.availability === "available"
          ? gapText(detail.desired_live_diff.source_revision)
          : <span style={{ color: UI.ink3 }}>{gitOpsReason(detail.desired_live_diff.reason_code)}</span>}
      </KV>
      <KV label="진행 중 작업">
        {detail.operation.in_progress
          ? <span style={{ fontFamily: MONO }}>{detail.operation.workflow_run_id} · {statusLabel(detail.operation.status)}</span>
          : <span style={{ color: UI.ink3 }}>{detail.operation.availability === "available" ? "진행 중 작업 없음" : gitOpsReason(detail.operation.reason_code)}</span>}
      </KV>
      {/* refresh/sync capability — 서버 계약상 아직 enabled=false만 온다. 동작하지 않는
          버튼을 그리는 대신 비활성 사유를 정직하게 표기한다(가짜 컨트롤 금지). */}
      {detail.capabilities.map((capability) => (
        <KV key={capability.action} label={capability.action === "refresh" ? "Refresh" : "Sync"}>
          <span style={{ color: UI.ink3 }}>{gitOpsReason(capability.reason_code)}</span>
        </KV>
      ))}
    </Section>
  );
}

function DriftSection({ section }: { section: DetailSection<ApplicationDriftEndpoint> }) {
  const drift = section.data;
  if (drift === null) {
    return (
      <Section title="드리프트">
        <SectionState status={section.status} emptyLabel="드리프트 관측을 불러오지 못했습니다." />
      </Section>
    );
  }
  return (
    <Section title="드리프트" aside={statusView(drift.status)}>
      {drift.summary && <span style={{ fontSize: TYPE.label, color: UI.ink, lineHeight: 1.55 }}>{drift.summary}</span>}
      {drift.observed_at && <KV label="관측 시각">{fromNow(drift.observed_at)}</KV>}
      {drift.status !== "drifted" && !drift.summary && (
        <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>
          {drift.status === "in_sync" ? "선언 상태와 라이브 상태가 일치합니다." : "드리프트 여부를 판정할 관측이 아직 없습니다."}
        </span>
      )}
      {drift.differences.map((difference, index) => (
        <div key={`${difference.resource}-${difference.field_path}-${index}`} style={{ border: `1px solid ${UI.line2}`, borderRadius: RADIUS.control, padding: "8px 10px", display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontFamily: MONO, fontSize: TYPE.caption, color: UI.ink }}>{difference.resource} · {difference.field_path}</span>
          <span style={{ fontFamily: MONO, fontSize: TYPE.caption, color: UI.ink2, overflowWrap: "anywhere" }}>
            {difference.value_redacted
              ? "값이 마스킹되었습니다"
              : `${String(difference.old_value ?? "—")} → ${String(difference.new_value ?? "—")}`}
          </span>
          {(difference.changed_by || difference.changed_at) && (
            <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>
              {[difference.changed_by, difference.changed_at ? fromNow(difference.changed_at) : null].filter(Boolean).join(" · ")}
            </span>
          )}
        </div>
      ))}
    </Section>
  );
}

const EVENT_KIND_KO: Record<ApplicationChangeEventView["kind"], string> = {
  inventory_event: "인벤토리",
  incident: "장애",
  deployment: "배포",
  gitops_change: "GitOps",
};

function eventSeverityColor(severity: ApplicationChangeEventView["severity"]): string {
  if (severity === "critical") return HP.crit;
  if (severity === "warning") return HP.warn;
  if (severity === "info") return BLUE;
  return UI.ink3;
}

/** 직전 24시간의 앱 스코프 실제 변경 이벤트 — 서버가 반환한 것만 그린다. */
function ChangeEventsSection({ applicationId }: { applicationId: string }) {
  const feed = useApplicationChangeEvents(applicationId);
  const visible = feed.events.slice(0, 20);
  return (
    <Section title="변경 이벤트 · 24시간"
      aside={feed.status === "ready" ? <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>{feed.events.length}건</span> : undefined}>
      {feed.status !== "ready" ? (
        <SectionState status={feed.status} emptyLabel="변경 이벤트를 불러오지 못했습니다." />
      ) : visible.length === 0 ? (
        <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>관측된 변경 이벤트 없음</span>
      ) : (
        <>
          {visible.map((event) => (
            <div key={event.id} style={{ display: "flex", alignItems: "flex-start", gap: 8, minWidth: 0 }}>
              <span aria-hidden="true" style={{ width: 7, height: 7, borderRadius: 999, background: eventSeverityColor(event.severity), flexShrink: 0, marginTop: 5 }} />
              <span style={{ minWidth: 0, flex: 1 }}>
                <span title={event.rawTitle} style={{ display: "block", fontSize: TYPE.label, color: UI.ink, lineHeight: 1.45, overflowWrap: "anywhere" }}>{event.title}</span>
                <span style={{ display: "block", marginTop: 1, fontSize: TYPE.caption, color: UI.ink3 }}>{EVENT_KIND_KO[event.kind]} · {fromNow(new Date(event.occurredMs).toISOString())}</span>
              </span>
            </div>
          ))}
          {feed.events.length > visible.length && (
            <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>최근 20건 표시 · {feed.events.length - visible.length}건 더 있음</span>
          )}
        </>
      )}
    </Section>
  );
}

function completenessLabel(value: "exact" | "partial" | "unavailable"): string {
  if (value === "exact") return "전체 관측";
  if (value === "partial") return "부분 관측";
  return "관측 안 됨";
}

const APPLICATION_DETAIL_LABELS: Record<string, string> = {
  aligned: "일치",
  conflict: "불일치",
  unknown: "관측 안 됨",
  paused: "일시정지",
  deployment: "배포",
  delivery: "배포",
  incident: "인시던트",
  change: "변경",
};

function applicationDetailLabel(value: string): string {
  return APPLICATION_DETAIL_LABELS[value] ?? statusLabel(value);
}

function RuntimeEvidenceSection({ record }: { record: ApplicationDetailEndpointItem }) {
  const runtime = record.runtime_readiness;
  const deployment = record.current_deployment;
  const batch = record.batch_runtime;
  return (
    <Section
      title="런타임·현재 배포"
      aside={<StatusPill tone={runtime.completeness === "exact" ? "ok" : runtime.completeness === "partial" ? "warn" : "gray"} label={completenessLabel(runtime.completeness)} />}
    >
      <KV label="런타임 상태">{statusView(runtime.status)}</KV>
      <KV label="파드 준비">
        {runtime.ready_pods !== null && runtime.total_pods !== null
          ? `${runtime.ready_pods} / ${runtime.total_pods}`
          : <span style={{ color: UI.ink3 }}>관측 안 됨</span>}
      </KV>
      <KV label="재시작">{runtime.restarts !== null ? `${runtime.restarts}회` : <span style={{ color: UI.ink3 }}>관측 안 됨</span>}</KV>
      <KV label="버전" mono>{gapText(deployment?.version ?? null)}</KV>
      <KV label="이미지" mono>{gapText(deployment?.image ?? null)}</KV>
      <KV label="이미지 digest" mono>{gapText(deployment?.image_digest ?? null)}</KV>
      <KV label="배포 커밋" mono>{gapText(deployment?.git_sha ?? null)}</KV>
      <KV label="배포 시각">{fromNow(deployment?.deployed_at ?? null)}</KV>
      <KV label="배포 실행자">{gapText(deployment?.deployed_by ?? null)}</KV>
      <KV label="배치 런타임">
        {batch.availability === "available"
          ? (
            <span>
              {statusView(batch.status)}
              <span style={{ marginLeft: 8, color: UI.ink3 }}>
                실행 {batch.active_runs ?? "—"} · 실패 {batch.failed_runs ?? "—"} · 성공 {batch.succeeded_runs ?? "—"}
              </span>
            </span>
          )
          : <span style={{ color: UI.ink3 }}>관측 안 됨</span>}
      </KV>
    </Section>
  );
}

function ResourceEvidenceSection({ record }: { record: ApplicationDetailEndpointItem }) {
  const counts = record.resource_counts;
  return (
    <Section
      title="리소스·운영 상태"
      aside={<StatusPill tone={record.resource_counts_completeness === "exact" ? "ok" : record.resource_counts_completeness === "partial" ? "warn" : "gray"} label={completenessLabel(record.resource_counts_completeness)} />}
    >
      <KV label="리소스">
        {counts === null
          ? <span style={{ color: UI.ink3 }}>관측 안 됨</span>
          : counts.length === 0
            ? <span style={{ color: UI.ink3 }}>관측된 리소스 없음</span>
            : counts.map((item) => `${item.kind} ${item.count}`).join(" · ")}
      </KV>
      <KV label="열린 인시던트">{record.open_incidents !== null ? `${record.open_incidents}건` : <span style={{ color: UI.ink3 }}>관측 안 됨</span>}</KV>
      <KV label="드리프트">
        {record.has_drift === null
          ? <span style={{ color: UI.ink3 }}>판정 안 됨</span>
          : record.has_drift
            ? <span style={{ color: TINT.crit.fg }}>{record.drift_summary}</span>
            : <span style={{ color: TINT.ok.fg }}>감지되지 않음</span>}
      </KV>
      <KV label="소스 정합성">
        {record.source.conflict === null
          ? <span style={{ color: UI.ink3 }}>관측 안 됨</span>
          : applicationDetailLabel(record.source.conflict)}
      </KV>
    </Section>
  );
}

function ApplicationScopeSection({ record }: { record: ApplicationDetailEndpointItem }) {
  const scope = record.scope;
  return (
    <Section
      title="배포 범위"
      aside={<StatusPill tone={scope.completeness === "exact" ? "ok" : scope.completeness === "partial" ? "warn" : "gray"} label={completenessLabel(scope.completeness)} />}
    >
      {scope.instances.length === 0 ? (
        <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>관측된 배포 인스턴스 없음</span>
      ) : scope.instances.map((instance) => (
        <div key={instance.id} style={{ border: `1px solid ${UI.line2}`, borderRadius: RADIUS.control, padding: "8px 10px", display: "flex", flexDirection: "column", gap: 5 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ minWidth: 0, flex: 1, fontSize: TYPE.label, fontWeight: 600, color: UI.ink }}>
              {instance.environment} · {instance.scope.cluster_id}
            </span>
            {scope.selected_instance_id === instance.id && <StatusPill tone="info" label="선택됨" />}
            {statusView(applicationDetailLabel(instance.status))}
          </div>
          <span style={{ fontFamily: MONO, fontSize: TYPE.caption, color: UI.ink3, overflowWrap: "anywhere" }}>
            {instance.scope.namespaces.length > 0 ? instance.scope.namespaces.join(", ") : "네임스페이스 관측 안 됨"} · {statusLabel(instance.scope.freshness)}
          </span>
        </div>
      ))}
    </Section>
  );
}

function ApplicationActivitySection({ record }: { record: ApplicationDetailEndpointItem }) {
  const history = record.history.entries ?? [];
  return (
    <>
      <Section title="최근 활동" aside={<span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>{record.recent_activity.length}건</span>}>
        {record.recent_activity.length === 0 ? (
          <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>관측된 최근 활동 없음</span>
        ) : record.recent_activity.map((activity) => (
          <div key={activity.id} style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
            <span aria-hidden="true" style={{ width: 7, height: 7, borderRadius: 999, background: activity.type === "incident" ? HP.crit : activity.type === "deployment" ? BLUE : HP.warn, marginTop: 5, flexShrink: 0 }} />
            <span style={{ minWidth: 0, flex: 1, fontSize: TYPE.label, color: UI.ink, lineHeight: 1.45 }}>
              {gapText(activity.summary)}
              <span style={{ display: "block", marginTop: 1, fontSize: TYPE.caption, color: UI.ink3 }}>{applicationDetailLabel(activity.type)} · {fromNow(activity.occurred_at)}</span>
            </span>
          </div>
        ))}
      </Section>
      <Section
        title="서버 배포·인시던트 이력"
        aside={<StatusPill tone={record.history.completeness === "exact" ? "ok" : record.history.completeness === "partial" ? "warn" : "gray"} label={completenessLabel(record.history.completeness)} />}
      >
        {history.length === 0 ? (
          <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>관측된 이력 없음</span>
        ) : history.map((entry) => (
          <div key={entry.id} style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
            <span style={{ minWidth: 0, flex: 1 }}>
              <span style={{ display: "block", fontSize: TYPE.label, color: UI.ink }}>{gapText(entry.summary)}</span>
              <span style={{ display: "block", marginTop: 1, fontSize: TYPE.caption, color: UI.ink3 }}>
                {applicationDetailLabel(entry.type)} · {fromNow(entry.occurred_at)}
              </span>
            </span>
            {statusView(entry.status)}
          </div>
        ))}
      </Section>
    </>
  );
}

function ApplicationTopologySection({ record }: { record: ApplicationDetailEndpointItem }) {
  const topology = record.topology;
  const nodes = topology.nodes ?? [];
  const edges = topology.edges ?? [];
  return (
    <Section
      title="토폴로지·엔드포인트"
      aside={<StatusPill tone={topology.completeness === "exact" ? "ok" : topology.completeness === "partial" ? "warn" : "gray"} label={completenessLabel(topology.completeness)} />}
    >
      <KV label="구성">{topology.availability === "available" ? `노드 ${nodes.length}개 · 연결 ${edges.length}개` : <span style={{ color: UI.ink3 }}>관측 안 됨</span>}</KV>
      <KV label="엔드포인트">
        {record.endpoints === null
          ? <span style={{ color: UI.ink3 }}>관측 안 됨</span>
          : record.endpoints.length === 0
            ? <span style={{ color: UI.ink3 }}>관측된 엔드포인트 없음</span>
            : record.endpoints.map((endpoint) => (
              <a key={endpoint.id} href={endpoint.url} target="_blank" rel="noreferrer" style={{ display: "block", color: BLUE, textDecoration: "none", overflowWrap: "anywhere" }}>
                {endpoint.kind} · {endpoint.name}
              </a>
            ))}
      </KV>
      {nodes.slice(0, 8).map((node) => (
        <div key={node.id} style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <span style={{ minWidth: 0, flex: 1, fontFamily: MONO, fontSize: TYPE.caption, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {node.kind} · {[node.namespace, node.name].filter(Boolean).join("/")}
          </span>
          {statusView(node.health)}
        </div>
      ))}
      {nodes.length > 8 && <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>최근 8개 표시 · {nodes.length - 8}개 더 있음</span>}
    </Section>
  );
}

export function ApplicationDetailPanel({ target, runs, onClose, insets }: {
  target: Extract<DeployDetailTarget, { kind: "application" }>;
  runs: ApplicationRunView[];
  onClose: () => void;
  insets: PanelInsets;
}) {
  const detail = useApplicationDetail(target.applicationId);
  const record = detail.record.data;
  const applicationRuns = runs.filter((run) => run.applicationId === target.applicationId);
  const deploymentBindings = detail.deployments.data ?? [];
  const visibleDeploymentBindings = deploymentBindings.slice(0, 20);

  return (
    <PanelShell icon={Package} title={record?.name ?? target.name} subtitle={target.applicationId} onClose={onClose} insets={insets}>
      <Section title="개요" aside={statusView(record?.health.status ?? null)}>
        {detail.record.status !== "ready" ? (
          <SectionState status={detail.record.status} emptyLabel="애플리케이션을 불러오지 못했습니다." />
        ) : record !== null && (
          <>
            <KV label="저장소" mono>{gapText(record.repository_ref)}</KV>
            <KV label="브랜치" mono>{gapText(record.default_branch)}</KV>
            <KV label="매니페스트" mono>{gapText(record.manifest_path)}</KV>
            <KV label="환경">{record.environments.length > 0 ? record.environments.join(", ") : <span style={{ color: UI.ink3 }}>—</span>}</KV>
            <KV label="라이프사이클">{statusView(record.lifecycle_status)}</KV>
            <KV label="배포 상태">{statusView(record.delivery.status)}</KV>
            <KV label="워크플로우" mono>{gapText(record.delivery.workflow_run_id)}</KV>
            <KV label="배포 관측 시각">{fromNow(record.delivery.observed_at)}</KV>
          </>
        )}
      </Section>
      {record !== null && <RuntimeEvidenceSection record={record} />}
      {record !== null && <ResourceEvidenceSection record={record} />}
      {record !== null && <ApplicationScopeSection record={record} />}
      {record !== null && <ApplicationTopologySection record={record} />}
      {record !== null && <ApplicationActivitySection record={record} />}
      <ChangeEventsSection applicationId={target.applicationId} />
      <Section title="배포 바인딩" aside={detail.deployments.data && <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>{detail.deployments.data.length}개</span>}>
        {deploymentBindings.length === 0 ? (
          <SectionState status={detail.deployments.status} emptyLabel="배포 바인딩을 불러오지 못했습니다." />
        ) : visibleDeploymentBindings.map((binding, index) => (
          <div key={index} style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0, borderBottom: index < visibleDeploymentBindings.length - 1 ? `1px solid ${UI.line2}` : "none", paddingBottom: 6 }}>
            <span style={{ minWidth: 0, flex: 1, fontFamily: MONO, fontSize: TYPE.label, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {[readText(binding, "environment"), readText(binding, "cluster_id"), readText(binding, "namespace")].filter(Boolean).join(" · ") || `바인딩 ${index + 1}`}
            </span>
            {statusView(readText(binding, "status"))}
          </div>
        ))}
        {deploymentBindings.length > visibleDeploymentBindings.length && (
          <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>
            최근 20개 표시 · {deploymentBindings.length - visibleDeploymentBindings.length}개 더 있음
          </span>
        )}
      </Section>
      <Section title="워크플로우 실행" aside={<span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>{applicationRuns.length}개</span>}>
        {applicationRuns.length === 0 ? (
          <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>관측된 배포 실행 없음</span>
        ) : applicationRuns.slice(0, 5).map((run) => (
          <div key={run.workflowRunId} style={{ border: `1px solid ${UI.line2}`, borderRadius: RADIUS.control, padding: "9px 11px", display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
              <span title={run.workflowRunId} style={{ minWidth: 0, flex: 1, fontFamily: MONO, fontSize: TYPE.caption, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{run.workflowRunId}</span>
              {statusView(run.status)}
              <span style={{ flexShrink: 0, fontSize: TYPE.caption, color: UI.ink3 }}>{fromNow(run.updatedAt ?? run.createdAt)}</span>
            </div>
            <StepTimeline steps={run.steps} />
          </div>
        ))}
        {applicationRuns.length > 5 && (
          <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>최근 5개 표시 · {applicationRuns.length - 5}개 더 있음</span>
        )}
      </Section>
      <GitOpsSection section={detail.gitops} />
      <DriftSection section={detail.drift} />
    </PanelShell>
  );
}

// ── Helm 릴리스 상세 ─────────────────────────────────────────────────────────

function helmAvailabilityNote(reasonCode: string): string {
  const HELM_REASON_KO: Record<string, string> = {
    helm_manifest_not_integrated: "매니페스트 조회가 아직 연동되지 않았습니다",
    helm_values_not_integrated: "values 조회가 아직 연동되지 않았습니다",
    helm_owned_resources_not_integrated: "소유 리소스 관측이 아직 연동되지 않았습니다",
    helm_commands_not_integrated: "릴리스 명령이 아직 연동되지 않았습니다",
  };
  return HELM_REASON_KO[reasonCode] ?? "아직 연동되지 않았습니다";
}

export function HelmDetailPanel({ target, onClose, insets }: {
  target: Extract<DeployDetailTarget, { kind: "helm" }>;
  onClose: () => void;
  insets: PanelInsets;
}) {
  const feed = useHelmReleaseDetail(target.identity);
  const detail = feed.detail;
  const release = detail?.release ?? null;
  const ownedResources = detail !== null && "items" in detail.owned_resources ? detail.owned_resources : null;

  return (
    <PanelShell icon={Rocket} title={target.identity.name} subtitle={`${target.identity.clusterId} · ${target.displayNamespace}`} onClose={onClose} insets={insets}>
      {detail === null ? (
        <SectionState status={feed.status} emptyLabel="Helm 릴리스 상세를 불러오지 못했습니다." />
      ) : (
        <>
          <Section title="개요" aside={statusView(release?.status ?? null)}>
            <KV label="차트" mono>{gapText(release?.chart ?? null)}</KV>
            <KV label="차트 버전" mono>{gapText(release?.chart_version ?? null)}</KV>
            <KV label="리비전" mono>{release !== null && release.revision !== null ? String(release.revision) : "—"}</KV>
            <KV label="스토리지" mono>{release ? `${release.storage.kind} · ${release.storage_namespace}/${release.storage.name}` : "—"}</KV>
            <KV label="리소스 헬스">
              {release === null ? "—"
                : "resource_count" in release.resource_health
                  ? <span>{statusView(release.resource_health.health)} <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>· 리소스 {release.resource_health.resource_count}개</span></span>
                  : <span style={{ color: UI.ink3 }}>{helmAvailabilityNote(release.resource_health.reason_code)}</span>}
            </KV>
            <KV label="관측 시각">{fromNow(release?.observed_at ?? null)}</KV>
            <KV label="매니페스트"><span style={{ color: UI.ink3 }}>{helmAvailabilityNote(detail.manifest.reason_code)}</span></KV>
            <KV label="Values"><span style={{ color: UI.ink3 }}>{helmAvailabilityNote(detail.values.reason_code)}</span></KV>
          </Section>
          <Section title="리비전 히스토리" aside={<span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>{detail.history.length}개</span>}>
            {detail.history.length === 0 ? (
              <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>보존된 히스토리 관측 없음</span>
            ) : detail.history.map((entry, index) => (
              <div key={`${entry.storage.uid}-${index}`} style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                <span style={{ flexShrink: 0, fontFamily: MONO, fontSize: TYPE.label, fontVariantNumeric: "tabular-nums", color: UI.ink, width: 44 }}>
                  {entry.revision !== null ? `r${entry.revision}` : "—"}
                </span>
                <span style={{ minWidth: 0, flex: 1 }}>{statusView(entry.status)}</span>
                <span style={{ flexShrink: 0, fontSize: TYPE.caption, color: UI.ink3 }}>{fromNow(entry.observed_at)}</span>
              </div>
            ))}
          </Section>
          <Section title="소유 리소스" aside={ownedResources?.truncated ? <StatusPill tone="warn" label="일부 생략" /> : undefined}>
            {ownedResources === null ? (
              <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>
                {"reason_code" in detail.owned_resources ? helmAvailabilityNote(detail.owned_resources.reason_code) : "관측된 소유 리소스 없음"}
              </span>
            ) : ownedResources.items.length === 0 ? (
              <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>관측된 소유 리소스 없음</span>
            ) : ownedResources.items.map((owned, index) => (
              <div key={`${owned.resource.uid}-${index}`} style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                <span style={{ minWidth: 0, flex: 1, fontFamily: MONO, fontSize: TYPE.caption, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {owned.resource.kind} · {[owned.resource.namespace, owned.resource.name].filter(Boolean).join("/")}
                </span>
                {statusView(owned.health)}
              </div>
            ))}
          </Section>
        </>
      )}
    </PanelShell>
  );
}

// ── 워크플로우 실행 상세 ─────────────────────────────────────────────────────

export function RunDetailPanel({ target, runs, onClose, insets }: {
  target: Extract<DeployDetailTarget, { kind: "run" }>;
  runs: ApplicationRunView[];
  onClose: () => void;
  insets: PanelInsets;
}) {
  const run = runs.find((candidate) => candidate.workflowRunId === target.workflowRunId) ?? null;
  return (
    <PanelShell icon={GitBranch} title={run?.applicationName ?? "워크플로우 실행"} subtitle={target.workflowRunId} onClose={onClose} insets={insets}>
      {run === null ? (
        <span style={{ fontSize: TYPE.label, color: UI.ink3, padding: "2px 0" }}>이 실행 기록이 현재 관측 범위에 없습니다.</span>
      ) : (
        <>
          <Section title="개요" aside={statusView(run.status)}>
            <KV label="앱" mono>{run.applicationName}</KV>
            <KV label="저장소" mono>{gapText(run.repositoryRef)}</KV>
            <KV label="커밋" mono>
              {run.commitSha && run.repositoryRef
                ? <a href={`https://github.com/${run.repositoryRef}/commit/${run.commitSha}`} target="_blank" rel="noreferrer" style={{ color: BLUE, textDecoration: "none" }}>{run.commitSha.slice(0, 12)}</a>
                : gapText(run.commitSha)}
            </KV>
            <KV label="클러스터" mono>{gapText(run.clusterId)}</KV>
            <KV label="현재 단계" mono>{gapText(run.currentStep)}</KV>
            <KV label="시작">{fromNow(run.createdAt)}</KV>
            <KV label="마지막 갱신">{fromNow(run.updatedAt)}</KV>
          </Section>
          <Section title="단계 타임라인" aside={<span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>{run.steps.length}단계</span>}>
            {run.steps.length === 0
              ? <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>기록된 단계 없음</span>
              : <StepTimeline steps={run.steps} />}
          </Section>
          {run.promotionGate && <PromotionGateSection gate={run.promotionGate} />}
        </>
      )}
    </PanelShell>
  );
}

// promotion gate는 서버 계약상 jsonMap으로 오므로 각 필드를 방어적으로 읽는다.
function PromotionGateSection({ gate }: { gate: Record<string, unknown> }) {
  const readBool = (key: string): boolean | null => (typeof gate[key] === "boolean" ? gate[key] as boolean : null);
  const readCount = (key: string): number | null => {
    const value = gate[key];
    return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
  };
  const eligible = readBool("eligible");
  const applied = readBool("applied");
  const rolloutReady = readBool("rollout_ready");
  const failedCount = readCount("failed_resource_count");
  const commandStatus = typeof gate.command_status === "string" ? gate.command_status : null;
  const unobserved = <span style={{ color: UI.ink3 }}>관측 안 됨</span>;
  return (
    <Section title="프로모션 게이트"
      aside={eligible === null ? undefined : <StatusPill tone={eligible ? "ok" : "warn"} label={eligible ? "충족" : "대기"} />}>
      <KV label="커맨드">{statusView(commandStatus)}</KV>
      <KV label="적용">{applied === null ? unobserved : applied ? "적용됨" : "적용 실패"}</KV>
      <KV label="Rollout">{rolloutReady === null ? unobserved : rolloutReady ? "Ready" : "Not ready"}</KV>
      {failedCount !== null && failedCount > 0 && (
        <KV label="실패 리소스"><span style={{ color: TINT.crit.fg }}>{failedCount}개</span></KV>
      )}
    </Section>
  );
}

// ── 확인 다이얼로그 — 모든 릴리스 쓰기 액션의 관문 (z 73: 패널 위, 헤더 아래) ──

export function ConfirmDialog({ title, body, confirmLabel, tone, requireText, busy, onCancel, onConfirm }: {
  title: string;
  body: React.ReactNode;
  confirmLabel: string;
  tone: "primary" | "danger";
  /** 파괴적 액션: 이 문자열을 정확히 입력해야 실행 버튼이 활성화된다. */
  requireText?: string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [typed, setTyped] = useState("");
  const locked = requireText !== undefined && typed.trim() !== requireText;
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.stopPropagation(); onCancel(); }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [onCancel]);
  return (
    <>
      <div aria-hidden="true" onClick={busy ? undefined : onCancel}
        style={{ position: "fixed", inset: 0, background: inkA(0.3), zIndex: 73 }} />
      <div role="alertdialog" aria-modal="true" aria-label={title}
        style={{ position: "fixed", top: "50%", left: "50%", transform: "translate(-50%, -50%)", width: 420, maxWidth: "calc(100vw - 48px)", background: UI.card, border: `1px solid ${UI.line}`, borderRadius: RADIUS.sheet, boxShadow: ELEV.overlay, zIndex: 74, overflow: "hidden" }}>
        <div style={{ padding: `${SPACE.card}px ${SPACE.card}px 0`, fontSize: TYPE.body, fontWeight: 700, color: tone === "danger" ? TINT.crit.fg : UI.ink }}>{title}</div>
        <div style={{ padding: `10px ${SPACE.card}px ${SPACE.card}px`, display: "flex", flexDirection: "column", gap: 10, fontSize: TYPE.label, color: UI.ink2, lineHeight: 1.55 }}>
          {body}
          {requireText !== undefined && (
            <>
              <span style={{ fontSize: TYPE.caption }}>확인을 위해 <span style={{ fontFamily: MONO, color: UI.ink }}>{requireText}</span> 를 입력하세요:</span>
              <input value={typed} onChange={(event) => setTyped(event.target.value)} placeholder={requireText} disabled={busy}
                style={{ border: `1px solid ${UI.line}`, borderRadius: RADIUS.control, padding: "7px 10px", fontSize: TYPE.caption, fontFamily: MONO, color: UI.ink, outline: "none" }} />
            </>
          )}
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, padding: `0 ${SPACE.card}px ${SPACE.card}px` }}>
          <button type="button" className="product-focusable product-control" onClick={onCancel} disabled={busy}
            style={{ border: `1px solid ${UI.line}`, background: UI.card, color: UI.ink, borderRadius: RADIUS.control, padding: "6px 13px", fontSize: TYPE.label, fontWeight: 600, cursor: busy ? "default" : "pointer" }}>취소</button>
          <button type="button" className="product-focusable product-action" onClick={onConfirm} disabled={busy || locked}
            style={{ border: "none", borderRadius: RADIUS.control, padding: "6px 13px", fontSize: TYPE.label, fontWeight: 600,
              cursor: busy || locked ? "default" : "pointer",
              background: busy || locked ? "#F3F4F6" : tone === "danger" ? HP.crit : BLUE,
              color: busy || locked ? "#9AA0AA" : UI.card }}>
            {busy ? "실행 중…" : confirmLabel}
          </button>
        </div>
      </div>
    </>
  );
}

// ── 릴리스 런/플랜 상세 — releaseFlowFeed의 서버 응답만 렌더 ────────────────

type ReleaseRunRecord = import("../api/release-flow-schemas").ReleaseRunApi;
type ReleasePlanRecord = import("../api/release-flow-schemas").ReleasePlanApi;
type ReleaseActionsApi = import("./releaseFlowFeed").ReleaseActions;
type RunActionId = import("../api/release-flow").ReleaseRunAction;

interface PendingRunAction {
  action: RunActionId;
  label: string;
  destructive: boolean;
}

const RUN_ACTION_LABEL: Record<string, string> = {
  pause: "일시정지", resume: "재개", retry: "재시도", rollback: "롤백", cancel: "취소",
};

function runStatusView(run: ReleaseRunRecord): React.ReactNode {
  const derived = typeof run.derived_status === "string" && run.derived_status.trim() !== ""
    ? run.derived_status
    : run.status;
  return statusView(derived);
}

export function ReleaseRunDetailPanel({ runId, runs, actions, onClose, insets }: {
  runId: string;
  runs: ReleaseRunRecord[];
  actions: ReleaseActionsApi;
  onClose: () => void;
  insets: PanelInsets;
}) {
  const run = runs.find((candidate) => candidate.run_id === runId) ?? null;
  const [confirm, setConfirm] = useState<PendingRunAction | null>(null);
  const busy = actions.state.pendingKey !== null;
  const lastResult = actions.state.lastResult;
  const effective = run === null ? "" : (typeof run.derived_status === "string" && run.derived_status.trim() !== "" ? run.derived_status : run.status).toLowerCase();
  const isActive = ["running", "in_progress", "pending", "starting", "progressing", "waiting_for_approval"].includes(effective);
  const isPaused = effective === "paused";
  const hasFailedStep = run !== null && run.steps.some((step) => /fail|error/i.test(step.status));

  // 서버가 허용하지 않을 상태의 버튼은 비활성 + 사유 — 동작 없는 컨트롤을 그리지 않는다.
  const runActions: { action: RunActionId; enabled: boolean; reason: string; destructive: boolean }[] = [
    { action: "pause", enabled: isActive, reason: "진행 중인 런에서만 가능", destructive: false },
    { action: "resume", enabled: isPaused, reason: "일시정지된 런에서만 가능", destructive: false },
    { action: "retry", enabled: isPaused || hasFailedStep || effective === "failed", reason: "실패가 관측된 런에서만 가능", destructive: false },
    { action: "rollback", enabled: run !== null, reason: "런 관측 필요", destructive: true },
    { action: "cancel", enabled: isActive || isPaused, reason: "진행·일시정지 상태에서만 가능", destructive: true },
  ];

  return (
    <PanelShell icon={ListChecks} title={run?.plan_name ?? "릴리스 런"} subtitle={runId} onClose={onClose} insets={insets}>
      {run === null ? (
        <span style={{ fontSize: TYPE.label, color: UI.ink3, padding: "2px 0" }}>이 런이 현재 관측 범위에 없습니다.</span>
      ) : (
        <>
          <Section title="개요" aside={runStatusView(run)}>
            <KV label="웨이브 진행" mono>{run.current_wave} / {run.total_waves}</KV>
            <KV label="시작자">{gapText(typeof run.started_by === "string" ? run.started_by : null)}</KV>
            <KV label="시작">{fromNow(run.created_at ?? null)}</KV>
            <KV label="마지막 갱신">{fromNow(run.updated_at ?? null)}</KV>
          </Section>
          <Section title="단계" aside={<span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>{run.steps.length}단계</span>}>
            {run.steps.length === 0 ? (
              <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>기록된 단계 없음</span>
            ) : run.steps.map((step) => (
              <div key={step.run_step_id} style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                <span style={{ flexShrink: 0, fontFamily: MONO, fontSize: TYPE.caption, color: UI.ink3, width: 28 }}>w{step.wave}</span>
                <span style={{ minWidth: 0, flex: 1, fontFamily: MONO, fontSize: TYPE.label, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{step.name}</span>
                {statusView(step.status)}
              </div>
            ))}
          </Section>
          <Section title="감사 로그" aside={<span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>{run.events.length}건</span>}>
            {run.events.length === 0 ? (
              <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>기록된 감사 이벤트 없음</span>
            ) : run.events.slice(0, 20).map((event) => (
              <div key={event.audit_id} style={{ display: "flex", alignItems: "baseline", gap: 8, minWidth: 0, fontSize: TYPE.caption }}>
                <span style={{ fontFamily: MONO, color: UI.ink, flexShrink: 0 }}>{event.event_type}</span>
                <span style={{ minWidth: 0, flex: 1, color: UI.ink2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={event.message}>{event.message}</span>
                <span style={{ color: UI.ink3, flexShrink: 0 }}>{[typeof event.actor === "string" ? event.actor : null, fromNow(event.created_at ?? null)].filter(Boolean).join(" · ")}</span>
              </div>
            ))}
          </Section>
          <Section title="런 제어">
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {runActions.map(({ action, enabled, reason, destructive }) => (
                <button key={action} type="button" className="product-focusable product-control"
                  disabled={!enabled || busy}
                  title={enabled ? undefined : reason}
                  onClick={() => setConfirm({ action, label: RUN_ACTION_LABEL[action] ?? action, destructive })}
                  style={{ border: `1px solid ${destructive && enabled ? TINT.crit.bd : UI.line}`, borderRadius: RADIUS.control, padding: "6px 13px", fontSize: TYPE.label, fontWeight: 600,
                    background: !enabled || busy ? "#F3F4F6" : destructive ? TINT.crit.bg : UI.card,
                    color: !enabled || busy ? "#9AA0AA" : destructive ? TINT.crit.fg : UI.ink,
                    cursor: !enabled || busy ? "not-allowed" : "pointer" }}>
                  {RUN_ACTION_LABEL[action] ?? action}
                </button>
              ))}
            </div>
            {lastResult !== null && (
              <span style={{ fontSize: TYPE.caption, color: lastResult.ok ? TINT.ok.fg : TINT.crit.fg }}>
                {lastResult.ok ? "✓" : "✕"} {lastResult.message}
              </span>
            )}
            <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>비활성 버튼은 서버가 허용하지 않는 상태입니다(사유는 툴팁).</span>
          </Section>
        </>
      )}
      {confirm !== null && run !== null && (
        <ConfirmDialog
          title={confirm.destructive ? `${confirm.label} — 파괴적 액션` : `${confirm.label} 확인`}
          tone={confirm.destructive ? "danger" : "primary"}
          confirmLabel={`${confirm.label} 실행`}
          requireText={confirm.destructive ? run.run_id : undefined}
          busy={busy}
          body={<span><span style={{ fontFamily: MONO, color: UI.ink }}>{run.run_id}</span> ({run.plan_name}) 런에 <b>{confirm.label}</b> 을(를) 요청합니다. 결과는 서버 응답과 감사 로그로만 반영됩니다.</span>}
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            void actions.executeRunAction(run.run_id, confirm.action).then(() => setConfirm(null));
          }}
        />
      )}
    </PanelShell>
  );
}

export function ReleasePlanDetailPanel({ planKey, plans, runs, actions, onClose, insets }: {
  planKey: string;
  plans: ReleasePlanRecord[];
  runs: ReleaseRunRecord[];
  actions: ReleaseActionsApi;
  onClose: () => void;
  insets: PanelInsets;
}) {
  const plan = plans.find((candidate) => (candidate.plan_id ?? candidate.name) === planKey) ?? null;
  const planRuns = plan === null ? [] : runs.filter((run) => run.plan_id === (plan.plan_id ?? ""));
  const [startOpen, setStartOpen] = useState(false);
  const [readinessState, setReadinessState] = useState<{
    key: string;
    status: DeployFeedStatus;
    result: import("../api/release-flow-schemas").ReleaseReadinessApi | null;
  }>({ key: "", status: "loading", result: null });
  const busy = actions.state.pendingKey !== null;
  const lastResult = actions.state.lastResult;

  // 시작 다이얼로그를 여는 순간 준비 검사(POST /api/release-readiness)를 실행한다.
  // 로딩 표시는 key 비교로 파생한다 — effect 안 동기 setState 금지(OpsiaConfigPanel 패턴).
  const readinessKey = startOpen && plan !== null ? (plan.plan_id ?? plan.name) : "";
  useEffect(() => {
    if (readinessKey === "" || plan === null) return;
    const controller = new AbortController();
    actions.checkReadiness(plan, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setReadinessState({ key: readinessKey, status: "ready", result });
      })
      .catch(() => {
        if (!controller.signal.aborted) setReadinessState({ key: readinessKey, status: "unavailable", result: null });
      });
    return () => controller.abort();
    // plan 객체는 폴링마다 참조가 바뀐다 — readinessKey가 정체성의 정본이다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readinessKey, actions]);
  const readiness = readinessState.key === readinessKey ? readinessState.result : null;
  const readinessStatus: DeployFeedStatus = readinessState.key === readinessKey ? readinessState.status : "loading";

  return (
    <PanelShell icon={ListChecks} title={plan?.name ?? "릴리스 플랜"} subtitle={plan?.plan_id ?? planKey} onClose={onClose} insets={insets}>
      {plan === null ? (
        <span style={{ fontSize: TYPE.label, color: UI.ink3, padding: "2px 0" }}>이 플랜이 현재 관측 범위에 없습니다.</span>
      ) : (
        <>
          <Section title="개요" aside={statusView(plan.status)}>
            {plan.description.trim() !== "" && <span style={{ fontSize: TYPE.label, color: UI.ink, lineHeight: 1.55 }}>{plan.description}</span>}
            <KV label="단계 수" mono>{String(plan.steps.length)}</KV>
            <KV label="수정">{fromNow(plan.updated_at ?? null)}</KV>
          </Section>
          <Section title="단계 구성">
            {plan.steps.length === 0 ? (
              <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>구성된 단계 없음</span>
            ) : plan.steps.map((step, index) => (
              <div key={step.step_id ?? index} style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                <span style={{ flexShrink: 0, fontFamily: MONO, fontSize: TYPE.caption, color: UI.ink3, width: 20 }}>{index + 1}</span>
                <span style={{ minWidth: 0, flex: 1, fontFamily: MONO, fontSize: TYPE.label, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{step.name}</span>
                {step.depends_on.length > 0 && <span style={{ fontSize: TYPE.caption, color: UI.ink3, flexShrink: 0 }}>의존 {step.depends_on.length}</span>}
              </div>
            ))}
          </Section>
          <Section title="최근 런" aside={<span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>{planRuns.length}개</span>}>
            {planRuns.length === 0 ? (
              <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>기록된 런 없음</span>
            ) : planRuns.slice(0, 5).map((run) => (
              <div key={run.run_id} style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                <span style={{ minWidth: 0, flex: 1, fontFamily: MONO, fontSize: TYPE.caption, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{run.run_id}</span>
                {runStatusView(run)}
                <span style={{ flexShrink: 0, fontSize: TYPE.caption, color: UI.ink3 }}>{fromNow(run.created_at ?? null)}</span>
              </div>
            ))}
          </Section>
          <Section title="플랜 제어">
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button type="button" className="product-focusable product-action"
                disabled={busy || plan.status === "archived"}
                title={plan.status === "archived" ? "보관된 플랜은 시작할 수 없습니다" : undefined}
                onClick={() => setStartOpen(true)}
                style={{ border: "none", borderRadius: RADIUS.control, padding: "6px 13px", fontSize: TYPE.label, fontWeight: 600,
                  background: busy || plan.status === "archived" ? "#F3F4F6" : BLUE,
                  color: busy || plan.status === "archived" ? "#9AA0AA" : UI.card,
                  cursor: busy || plan.status === "archived" ? "not-allowed" : "pointer" }}>
                런 시작…
              </button>
              <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>시작 전 서버 준비 검사 결과를 먼저 확인합니다.</span>
            </div>
            {lastResult !== null && (
              <span style={{ fontSize: TYPE.caption, color: lastResult.ok ? TINT.ok.fg : TINT.crit.fg }}>
                {lastResult.ok ? "✓" : "✕"} {lastResult.message}
              </span>
            )}
          </Section>
        </>
      )}
      {startOpen && plan !== null && (
        <ConfirmDialog
          title="런 시작 — 준비 검사"
          tone="primary"
          confirmLabel="런 시작"
          busy={busy}
          requireText={readiness !== null && readiness.impact !== undefined && readiness.impact.production_target_count > 0 ? (plan.plan_id ?? plan.name) : undefined}
          body={
            readinessStatus === "loading" ? <span>준비 검사 실행 중… (POST /api/release-readiness)</span>
            : readinessStatus === "unavailable" ? <span style={{ color: TINT.crit.fg }}>준비 검사를 수행하지 못했습니다 — 시작할 수 없습니다.</span>
            : readiness === null ? <span>준비 검사 결과 없음</span>
            : (
              <span style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <span style={{ color: readiness.ready ? TINT.ok.fg : TINT.crit.fg, fontWeight: 600 }}>
                  {readiness.ready ? "✓ 준비됨" : "✕ 준비 안 됨"} · {readiness.summary}
                </span>
                {readiness.impact !== undefined && readiness.impact.production_target_count > 0 && (
                  <span style={{ color: TINT.crit.fg, background: TINT.crit.bg, border: `1px solid ${TINT.crit.bd}`, borderRadius: RADIUS.control, padding: "7px 10px", fontSize: TYPE.caption }}>
                    ⚠ 프로덕션 대상 {readiness.impact.production_target_count}개 포함 — 플랜 식별자 입력이 필요합니다.
                  </span>
                )}
                {readiness.checks.filter((check) => check.status !== "passed").slice(0, 5).map((check) => (
                  <span key={check.check_id} style={{ fontSize: TYPE.caption, color: UI.ink2 }}>▪ {check.name}: {check.message}</span>
                ))}
              </span>
            )
          }
          onCancel={() => setStartOpen(false)}
          onConfirm={() => {
            if (readinessStatus !== "ready" || readiness === null || !readiness.ready) return;
            void actions.startPlan(plan).then((ok) => { if (ok) setStartOpen(false); });
          }}
        />
      )}
    </PanelShell>
  );
}

// ── 진입점 — DeploySurface가 detail 상태 하나로 패널을 라우팅한다 ────────────

export function DeployDetailHost({ target, runs, releasePlans = [], releaseRuns = [], releaseActions = null, onClose, topInset, leftInset, rightInset }: {
  target: DeployDetailTarget;
  runs: ApplicationRunView[];
  releasePlans?: ReleasePlanRecord[];
  releaseRuns?: ReleaseRunRecord[];
  releaseActions?: ReleaseActionsApi | null;
  onClose: () => void;
} & PanelInsets) {
  const insets: PanelInsets = { topInset, leftInset, rightInset };
  if (target.kind === "application") return <ApplicationDetailPanel target={target} runs={runs} onClose={onClose} insets={insets} />;
  if (target.kind === "helm") return <HelmDetailPanel target={target} onClose={onClose} insets={insets} />;
  if (target.kind === "releaseRun") {
    return releaseActions === null ? null : (
      <ReleaseRunDetailPanel runId={target.runId} runs={releaseRuns} actions={releaseActions} onClose={onClose} insets={insets} />
    );
  }
  if (target.kind === "releasePlan") {
    return releaseActions === null ? null : (
      <ReleasePlanDetailPanel planKey={target.planKey} plans={releasePlans} runs={releaseRuns} actions={releaseActions} onClose={onClose} insets={insets} />
    );
  }
  return <RunDetailPanel target={target} runs={runs} onClose={onClose} insets={insets} />;
}
