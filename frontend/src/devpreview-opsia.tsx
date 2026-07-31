/* eslint-disable react-hooks/exhaustive-deps */
// ⚠ 데모 · Kyro 통합 맵 v5 — 실 인벤토리 계약 배선.
// 원칙: 뉴트럴 표면 + 헤어라인, 색은 데이터에만, 모노 숫자, 4pt 그리드.
// 구조: 클러스터(리스트) → 노드(관측 목록) → 파드(관측 목록).
// no backfill: 계약이 노출하지 않는 값(CPU/MEM/용량/파드→노드 귀속 등)은 절대
// 지어내지 않는다. 관측이 없으면 "관측 안 됨"/"관측된 리소스가 없습니다"를 렌더한다.
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Activity, AlertTriangle, Box, Check, ChevronLeft, ChevronRight, Clock3, Cpu, EllipsisVertical, ExternalLink, FileCog, Monitor, Plug, RotateCcw, Server, Settings, Unplug } from "lucide-react";
import { UI, BLUE, HP, TINT, MONO, TYPE, SOFT, SPRING, PAGE, PRESENT_SCALE, DUR, RESOURCE_LAYOUT, inkA, blueA, LINE3, INK4, BRAND, cardA } from "./devpreview/theme";
import { AwsIcon, GithubIcon } from "./devpreview/brandIcons";
import { statusLabel, reasonLabel } from "./devpreview/statusLabel";
import { useNarrowViewport } from "./devpreview/useNarrowViewport";
import { useDevpreviewContracts, type DevpreviewCluster } from "./devpreview/contracts";
import { isActiveIncidentCluster } from "./devpreview/rcaIssuesFeed";
import { useClusterSummaries, type ClusterSummaryView } from "./devpreview/clusterSummaryFeed";
import { NodeAliasTitle } from "./devpreview/NodeAliasTitle";
import { useNodeAliases, type NodeAliasView } from "./devpreview/nodeAliasesFeed";
import { HOME_CARD_GRID_CLASS, HOME_CARD_GRID_ITEM_CLASS, homeCardGridItemStyle, homeCardGridStyle } from "./devpreview/widgets";
import {
  podsForNode,
  useClusterTopology,
  type ClusterTopologyView,
  type InvNode,
  type InvPod,
} from "./devpreview/inventoryTopologyFeed";
import { OpsiaConfigPanel } from "./devpreview/OpsiaConfigPanel";
import { OpsiaServicePanel } from "./devpreview/OpsiaServicePanel";
import { RepositoryConnections } from "./devpreview/RepositoryConnections";
import {
  ResourceAuxiliaryPanel,
  ResourceAuxiliaryRow,
  resourceAuxiliaryFooterButtonStyle,
  resourceAuxiliaryViewportHeight,
} from "./devpreview/ResourceAuxiliaryPanel";
import type { RepositoryGroup } from "./devpreview/repositoryRegistry";
import { useRelationTopology } from "./devpreview/relationTopologyFeed";
import {
  podHighlightIdentity,
  resolveHighlightedPodIdentities,
  type PodHighlightTarget,
} from "./devpreview/podHighlight";
import { podResourceSelection } from "./devpreview/podSelection";
import "./styles/tokens.css";
import "./styles/foundation.css";

// ── 도메인 ─────────────────────────────
// 관측된 리소스의 health/status 문자열을 정직하게 심각도로 매핑한다(계약 값 그대로,
// 지어내지 않는다). devpreview-surfaces 의 healthPill 과 동일한 규약.
type Sev = "ok" | "warn" | "crit" | "unknown";
function healthSev(health: string): Sev {
  const h = health.toLowerCase();
  if (h === "healthy" || h === "ready") return "ok";
  if (h === "degraded" || h === "warning") return "warn";
  if (h === "critical" || h === "failed" || h === "unhealthy") return "crit";
  return "unknown";
}
const sevColor = (s: Sev) => (s === "crit" ? HP.crit : s === "warn" ? HP.warn : s === "ok" ? HP.ok : UI.ink3);
const isBadHealth = (health: string) => healthSev(health) === "crit";
type TipMetric = {
  label: string;
  usage: string;
  request?: string;
  limit?: string;
  limitSeverity?: "warn" | "crit";
};
const usageMetric = (
  label: string,
  used: number | null,
  requested: number | null,
  limit: number | null,
  unit: string,
): TipMetric => {
  const limitPercent = used != null && limit != null
    ? Math.round((used / limit) * 100)
    : null;
  return {
    label,
    usage: used == null
      ? "사용량 관측 안 됨"
      : `사용 ${Math.round(used)}${unit}${limitPercent === null ? "" : `(${limitPercent}%)`}`,
    request: requested == null ? "요청 없음" : `요청 ${Math.round(requested)}${unit}`,
    limit: limit == null ? "제한 없음" : `제한 ${Math.round(limit)}${unit}`,
    limitSeverity: limitPercent !== null && limitPercent >= 90
      ? "crit"
      : limitPercent !== null && limitPercent >= 80
        ? "warn"
        : undefined,
  };
};
const podTipMetrics = (pod: InvPod): TipMetric[] => [
  usageMetric("CPU", pod.cpuMillicores, pod.cpuRequestMillicores, pod.cpuLimitMillicores, "m"),
  usageMetric("메모리", pod.memoryMebibytes, pod.memoryRequestMebibytes, pod.memoryLimitMebibytes, "Mi"),
  { label: "재시작", usage: `${pod.restartCount}회` },
];

type View = { level: "clusters" } | { level: "nodes"; cluster: string } | { level: "pods"; cluster: string; node: string };
export type MapScope = View;

type TipData = { x: number; y: number; label: string; status: string; health: string; metrics?: TipMetric[] } | null;
const NODE_METRIC_ANIMATION_SECONDS = 0.28;
const NODE_METRIC_MAX_FRAMES_PER_SECOND = 60;

// ── 커서 추적 툴팁 정보(노드/파드 공용) ─────────────────────────────
function HealthChip({ health }: { health: string }) {
  const sev = healthSev(health);
  const label = statusLabel(health); // 매핑에 없는 원시값은 원문 유지(honest)
  if (sev === "unknown") {
    return <span style={{ fontSize: TYPE.caption, fontWeight: 600, color: UI.ink3, border: `1px solid ${UI.line}`, borderRadius: 5, padding: "1px 6px", whiteSpace: "nowrap" }}>{label}</span>;
  }
  const c = sevColor(sev);
  return <span style={{ fontSize: TYPE.caption, fontWeight: 600, color: c, background: `${c}14`, border: `1px solid ${c}33`, borderRadius: 5, padding: "1px 6px", whiteSpace: "nowrap" }}>{label}</span>;
}

// ── 관측 안 됨 표기(사용률 계약이 없을 때) ─────────────────────────────
function ClusterMiniUsage({ label, value }: { label: string; value: number | null }) {
  // A null usage percentage is an honest "not observed" contract state — the
  // backend returned no `cpu_pct`/`mem_pct` sample. It must never be backfilled.
  // Keep the DOM geometry stable when a summary-backed shell is upgraded with
  // its CPU/MEM sample. Only the compositor-driven scale changes; no bar width
  // reflow or loading-to-ready element replacement is required.
  const reducedMotion = useReducedMotion();
  const observed = value !== null;
  const normalized = Math.min(100, Math.max(0, value ?? 0));
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
      <span style={{ width: 34, fontSize: TYPE.caption, fontWeight: 600, letterSpacing: "0.05em", color: UI.ink3, flexShrink: 0 }}>{label}</span>
      <span style={{ flex: 1, height: 5, borderRadius: 999, background: inkA(0.07), overflow: "hidden" }}>
        <motion.span
          initial={false}
          animate={{ scaleX: normalized / 100, opacity: observed ? 1 : 0 }}
          transition={reducedMotion ? { duration: 0 } : { duration: NODE_METRIC_ANIMATION_SECONDS, ease: "easeOut" }}
          style={{
            display: "block", width: "100%", height: "100%", borderRadius: 999, transformOrigin: "left center",
            background: normalized >= 90 ? HP.crit : normalized >= 75 ? HP.warn : HP.ok,
          }}
        />
      </span>
      <span style={{ width: 66, textAlign: "right", fontSize: observed ? TYPE.label : TYPE.caption, fontWeight: observed ? 600 : 500, color: observed ? UI.ink : UI.ink3, fontVariantNumeric: "tabular-nums", flexShrink: 0, whiteSpace: "nowrap" }}>
        <AnimatedPercentageValue value={value} reducedMotion={Boolean(reducedMotion)} />
      </span>
    </div>
  );
}

export function AnimatedPercentageValue({ value, reducedMotion }: { value: number | null; reducedMotion: boolean }) {
  const previousObserved = useRef<number | null>(null);
  const [presentation, setPresentation] = useState(() => ({
    observed: value,
    value: value ?? 0,
  }));

  useEffect(() => {
    if (value === null) {
      previousObserved.current = null;
      const timer = window.setTimeout(() => {
        setPresentation({ observed: null, value: 0 });
      }, 0);
      return () => window.clearTimeout(timer);
    }

    const target = Math.min(100, Math.max(0, value));
    const startValue = previousObserved.current;
    previousObserved.current = target;
    // The first endpoint is painted exactly as observed. Only a transition
    // between two real observations is interpolated; zero is never invented as
    // a synthetic starting measurement.
    if (startValue === null || reducedMotion || typeof window.requestAnimationFrame !== "function") {
      const timer = window.setTimeout(() => {
        setPresentation({ observed: target, value: target });
      }, 0);
      return () => window.clearTimeout(timer);
    }

    let frame = 0;
    const startedAt = performance.now();
    const durationMs = NODE_METRIC_ANIMATION_SECONDS * 1_000;
    const minimumFrameIntervalMs = 1_000 / NODE_METRIC_MAX_FRAMES_PER_SECOND;
    let lastPaintedAt = startedAt - minimumFrameIntervalMs;
    const update = (now: number) => {
      const progress = Math.min(1, (now - startedAt) / durationMs);
      if (progress < 1 && now - lastPaintedAt < minimumFrameIntervalMs) {
        frame = window.requestAnimationFrame(update);
        return;
      }
      lastPaintedAt = now;
      const eased = 1 - (1 - progress) ** 3;
      setPresentation({
        observed: target,
        value: startValue + (target - startValue) * eased,
      });
      if (progress < 1) frame = window.requestAnimationFrame(update);
    };
    frame = window.requestAnimationFrame(update);
    return () => window.cancelAnimationFrame(frame);
  }, [reducedMotion, value]);

  if (value === null) return <>관측 안 됨</>;
  const presented = presentation.observed === null || reducedMotion ? value : presentation.value;
  const rounded = Math.round(presented * 10) / 10;
  return <>{Number.isInteger(rounded) ? rounded.toFixed(0) : rounded.toFixed(1)}%</>;
}

function ClusterRow({ cl, summary, topology, onOpen }: {
  cl: DevpreviewCluster;
  summary?: ClusterSummaryView;
  topology?: ClusterTopologyView;
  onOpen: () => void;
}) {
  // Live: identity/version from `GET /api/clusters`; node readiness and usage
  // from the bounded node-summary feed. A drill topology may refine those counts,
  // but Home does not start heavy topology projections just to paint cards.
  const incidentsObserved = isActiveIncidentCluster(cl);
  const incidents = incidentsObserved ? cl.incidentCount ?? summary?.openIncidents ?? 0 : 0;
  const healthy = incidentsObserved && incidents === 0;
  const summaryLoading = summary === undefined || summary.status === "loading";
  const topologyLoading = topology?.status === "loading";
  const nodesReady = topology?.nodesReady ?? summary?.nodesReady ?? null;
  const nodesTotal = topology?.nodesTotal ?? summary?.nodesTotal ?? null;
  const podCount = topology?.podsTotal ?? summary?.podsTotal ?? summary?.podsRunning ?? null;
  const countsLoading = topologyLoading || (topology === undefined && summaryLoading);
  const fmt = (n: number | null) => (countsLoading ? "…" : n ?? "—");
  const cpuPct = summaryLoading ? null : summary?.cpuPct ?? null;
  const memPct = summaryLoading ? null : summary?.memPct ?? null;
  return (
    <motion.button transition={SPRING} onClick={onOpen}
      whileHover={{ boxShadow: `0 10px 26px -20px ${inkA(0.16)}`, borderColor: LINE3 }}
      style={{
        display: "flex", flexDirection: "column", gap: 12, width: "100%", height: "100%", textAlign: "left", cursor: "pointer",
        background: UI.card, border: `1px solid ${UI.line}`, borderRadius: 16, padding: 16, boxShadow: "none", boxSizing: "border-box",
      }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
        <span style={{ width: 30, height: 30, borderRadius: 9, background: `linear-gradient(135deg, ${BRAND.awsA}, ${BRAND.awsB})`, display: "grid", placeItems: "center", flexShrink: 0 }}>
          <AwsIcon size={17} style={{ color: UI.card }} />
        </span>
        <span style={{ minWidth: 0, flex: 1 }}>
          <span style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
            <span title={cl.displayName} style={{ fontSize: TYPE.section, fontWeight: 700, letterSpacing: "-0.02em", color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{cl.displayName}</span>
            {cl.environment === "production" && <span style={{ fontSize: TYPE.caption, fontWeight: 600, color: TINT.warn.fg, border: `1px solid ${TINT.warn.bd}`, background: TINT.warn.bg, borderRadius: 5, padding: "1px 6px", flexShrink: 0 }}>prod</span>}
            {cl.readOnly && <span style={{ fontSize: TYPE.caption, fontWeight: 600, color: UI.ink2, border: `1px solid ${UI.line}`, background: UI.bg2, borderRadius: 5, padding: "1px 6px", flexShrink: 0 }}>읽기 전용</span>}
          </span>
          <span style={{ display: "block", fontSize: TYPE.caption, color: UI.ink3, marginTop: 2 }}>
            {cl.provider.toUpperCase()} · {cl.kubernetesVersion ?? "—"}
            {summary?.stale === true && <span aria-label="실시간 관측 지연" style={{ color: TINT.warn.fg, fontWeight: 600 }}> · 관측 지연</span>}
          </span>
        </span>
        {!healthy && (incidentsObserved
            ? <span style={{ fontSize: TYPE.caption, fontWeight: 600, color: UI.card, background: HP.crit, borderRadius: 999, padding: "3px 9px", fontVariantNumeric: "tabular-nums", flexShrink: 0 }}>장애 {incidents}</span>
            : !isHealthyConnection(cl)
              ? <span style={{ fontSize: TYPE.caption, fontWeight: 600, color: TINT.warn.fg, background: TINT.warn.bg, border: `1px solid ${TINT.warn.bd}`, borderRadius: 999, padding: "3px 9px", flexShrink: 0 }}>{statusLabel(cl.connectionStage ?? cl.connectionStatus)}</span>
              : null)}
      </div>

      <div style={{ display: "flex", gap: 14, fontSize: TYPE.label, color: UI.ink2, fontVariantNumeric: "tabular-nums", flexWrap: "wrap" }}>
        <span>노드 <b style={{ color: UI.ink, fontVariantNumeric: "tabular-nums" }}>{fmt(nodesReady)}/{fmt(nodesTotal)}</b> 준비</span>
        <span>파드 <b style={{ color: UI.ink, fontVariantNumeric: "tabular-nums" }}>{fmt(podCount)}</b>{incidents > 0 && <b style={{ color: TINT.crit.fg, fontVariantNumeric: "tabular-nums" }}> · 장애 {incidents}</b>}</span>
        <span>네임스페이스 <b style={{ color: UI.ink, fontVariantNumeric: "tabular-nums" }}>{cl.namespaceCount ?? "—"}</b></span>
        {topology?.status === "ready" && topology.partial && (
          <span title={topology.partialReasonCodes.map(reasonLabel).join(" · ") || "부분 관측"}
            style={{ color: TINT.warn.fg, fontWeight: 600 }}>
            일부 관측{topology.truncatedPodCount > 0 ? ` · ${topology.truncatedPodCount}개 미표시` : ""}
          </span>
        )}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 7, marginTop: "auto" }}>
        <ClusterMiniUsage label="CPU" value={cpuPct} />
        <ClusterMiniUsage label="MEM" value={memPct} />
      </div>
    </motion.button>
  );
}

function isHealthyConnection(cluster: Pick<DevpreviewCluster, "connectionStatus" | "connectionStage">): boolean {
  const state = String(cluster.connectionStage ?? cluster.connectionStatus).toLowerCase();
  return cluster.connectionStatus === "online"
    && ["agent_connected", "connected", "online", "ready", "snapshot_received"].includes(state);
}

/**
 * 수치 보간 — 새 관측이 도착하면 0.5초 동안 ease-out 으로 이전 값에서 목표값까지
 * 이동시켜 스텝 점프 대신 자연스러운 움직임을 만든다.
 * no backfill 원칙 유지: null(관측 없음)은 보간 대상이 아니라 즉시 null 로 표시하고,
 * null → 값 첫 등장도 지어낸 시작점 없이 즉시 표시한다.
 */
export function useSmoothedValue(target: number | null, durationMs = 500): number | null {
  const [display, setDisplay] = useState<number | null>(target);
  const displayRef = useRef<number | null>(target);
  useEffect(() => {
    const from = displayRef.current;
    if (target === null || from === null) {
      displayRef.current = target;
      setDisplay(target);
      return;
    }
    if (from === target) return;
    let raf = 0;
    const started = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - started) / durationMs);
      const eased = 1 - (1 - t) * (1 - t);
      const value = Math.round((from + (target - from) * eased) * 10) / 10;
      displayRef.current = value;
      setDisplay(value);
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, durationMs]);
  return display;
}

function CompactUsage({ label, value }: { label: string; value: number | null }) {
  const shown = useSmoothedValue(value);
  return (
    <span style={{ display: "grid", gridTemplateColumns: "30px minmax(48px, 1fr) 38px", alignItems: "center", gap: 6, minWidth: 0 }}>
      <span style={{ fontSize: TYPE.caption, fontWeight: 600, color: UI.ink3 }}>{label}</span>
      <span style={{ height: 4, borderRadius: 999, background: inkA(0.06), overflow: "hidden" }}>
        {shown !== null && <span style={{ display: "block", width: `${Math.min(100, shown)}%`, height: "100%", borderRadius: 999, background: shown >= 90 ? HP.crit : shown >= 75 ? HP.warn : HP.ok }} />}
      </span>
      <span style={{ textAlign: "right", fontSize: TYPE.caption, fontWeight: 600, fontVariantNumeric: "tabular-nums", color: shown === null ? UI.ink3 : UI.ink }}>{shown === null ? "—" : `${Math.round(shown)}%`}</span>
    </span>
  );
}

function CompactClusterRow({ cl, summary, onOpen, onSettings, onDisconnect }: {
  cl: DevpreviewCluster;
  summary?: ClusterSummaryView;
  onOpen: () => void;
  onSettings?: () => void;
  onDisconnect?: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<{ top: number; right: number } | null>(null);
  const menuRef = useRef<HTMLSpanElement>(null);
  const portalMenuRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!menuRef.current?.contains(target) && !portalMenuRef.current?.contains(target)) {
        setMenuOpen(false);
      }
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setMenuOpen(false); };
    // 메뉴는 body portal(fixed)이라 스크롤·리사이즈 시 기준 좌표가 어긋난다 — 즉시 닫는다.
    const dismiss = () => setMenuOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("resize", dismiss);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("resize", dismiss);
    };
  }, [menuOpen]);
  const summaryLoading = summary === undefined || summary.status === "loading";
  const ready = summary?.nodesReady ?? null;
  const nodes = summary?.nodesTotal ?? null;
  const pods = summary?.podsTotal ?? summary?.podsRunning ?? null;
  const summaryNodes = summary?.nodes ?? [];
  const slots = summaryNodes.length > 0 ? summaryNodes.reduce((total, node) => total + node.podsCapacity, 0) : null;
  const incidentsObserved = isActiveIncidentCluster(cl);
  const incidents = incidentsObserved ? cl.incidentCount ?? summary?.openIncidents ?? 0 : 0;
  const unavailable = summary?.status === "unavailable" || (!summaryLoading && summary?.cpuPct === null && summary?.memPct === null);
  const connectionWarning = !isHealthyConnection(cl);
  const fmt = (value: number | null) => summaryLoading ? "…" : value ?? "—";
  const activate = () => { setMenuOpen(false); onOpen(); };
  return (
    <motion.div onClick={activate}
      whileHover={{ backgroundColor: inkA(0.025) }}
      className="home-cluster-compact-row"
      style={{ position: "relative", display: "grid", alignItems: "center", minHeight: 54, padding: "8px 4px", borderTop: `1px solid ${UI.line2}`, cursor: "pointer" }}>
      <span style={{ display: "flex", alignItems: "center", gap: 9, minWidth: 0 }}>
        <span style={{ width: 27, height: 27, borderRadius: 8, background: `linear-gradient(135deg, ${BRAND.awsA}, ${BRAND.awsB})`, display: "grid", placeItems: "center", flexShrink: 0 }}><AwsIcon size={15} style={{ color: UI.card }} /></span>
        <span style={{ minWidth: 0 }}>
          <span style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
            <button
              type="button"
              aria-label={`${cl.displayName} 클러스터 상세`}
              className="product-focusable"
              onClick={(event) => {
                event.stopPropagation();
                activate();
              }}
              title={cl.displayName}
              style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", border: "none", borderRadius: 5, padding: 0, background: "transparent", fontSize: TYPE.label, fontWeight: 600, color: UI.ink, cursor: "pointer", textAlign: "left" }}
            >
              {cl.displayName}
            </button>
            {cl.readOnly && <span style={{ fontSize: TYPE.caption, fontWeight: 600, color: UI.ink2, border: `1px solid ${UI.line}`, background: UI.bg2, borderRadius: 5, padding: "1px 6px", flexShrink: 0 }}>읽기 전용</span>}
          </span>
          <span style={{ display: "block", marginTop: 1, fontSize: TYPE.caption, color: UI.ink3 }}>{cl.environment ?? cl.provider.toUpperCase()}</span>
        </span>
        {incidents > 0 ? (
          <span style={{ marginLeft: "auto", flexShrink: 0, fontSize: TYPE.caption, fontWeight: 600, color: HP.crit, background: TINT.crit.bg, borderRadius: 999, padding: "2px 6px" }}>장애 {incidents}</span>
        ) : connectionWarning ? (
          <span style={{ marginLeft: "auto", flexShrink: 0, fontSize: TYPE.caption, fontWeight: 600, color: TINT.warn.fg, background: TINT.warn.bg, borderRadius: 999, padding: "2px 6px" }}>{statusLabel(cl.connectionStage ?? cl.connectionStatus)}</span>
        ) : summary?.stale === true ? (
          <span
            aria-label="실시간 관측 지연"
            style={{ marginLeft: "auto", flexShrink: 0, fontSize: TYPE.caption, fontWeight: 600, color: TINT.warn.fg }}
          >
            <Clock3 size={11} aria-hidden="true" /> 관측 지연
          </span>
        ) : unavailable ? (
          <span
            aria-label="메트릭 수집 대기: CPU·메모리 최신 샘플 미수신"
            title="CPU·메모리 최신 샘플을 아직 수신하지 못했습니다."
            style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 4, flexShrink: 0, fontSize: TYPE.caption, fontWeight: 600, lineHeight: 1, color: TINT.warn.fg }}
          >
            <Clock3 size={11} aria-hidden="true" />메트릭 수집 대기
          </span>
        ) : null}
      </span>
      <span className="home-cluster-compact-facts" style={{ display: "grid", gridTemplateColumns: "var(--home-cluster-facts-columns, repeat(4, minmax(0, auto)))", alignItems: "baseline", gap: "var(--home-cluster-facts-gap, 12px)", minWidth: 0, fontSize: TYPE.caption, lineHeight: 1.2, color: UI.ink3, fontVariantNumeric: "tabular-nums" }}>
        <span style={{ display: "inline-flex", alignItems: "baseline", gap: 4, whiteSpace: "nowrap" }}>노드 <b style={{ color: UI.ink, fontVariantNumeric: "tabular-nums" }}>{fmt(ready)}/{fmt(nodes)}</b></span>
        <span style={{ display: "inline-flex", alignItems: "baseline", gap: 4, whiteSpace: "nowrap" }}>파드 <b style={{ color: UI.ink, fontVariantNumeric: "tabular-nums" }}>{fmt(pods)}</b></span>
        <span style={{ display: "inline-flex", alignItems: "baseline", gap: 4, whiteSpace: "nowrap" }}>NS <b style={{ color: UI.ink, fontVariantNumeric: "tabular-nums" }}>{cl.namespaceCount ?? "—"}</b></span>
        <span style={{ display: "inline-flex", alignItems: "baseline", gap: 4, whiteSpace: "nowrap" }}>슬롯 <b style={{ color: UI.ink, fontVariantNumeric: "tabular-nums" }}>{fmt(pods)}/{fmt(slots)}</b></span>
      </span>
      <span className="home-cluster-compact-usage" style={{ display: "grid", gap: 4, minWidth: 0 }}>
        <CompactUsage label="CPU" value={summaryLoading ? null : summary?.cpuPct ?? null} />
        <CompactUsage label="MEM" value={summaryLoading ? null : summary?.memPct ?? null} />
      </span>
      <span ref={menuRef} style={{ position: "relative", display: "grid", placeItems: "center", alignSelf: "center" }} onClick={(event) => event.stopPropagation()}>
        <button type="button" aria-label={`${cl.displayName} 클러스터 메뉴`} aria-expanded={menuOpen}
          onClick={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            setMenuPos({ top: rect.bottom + 4, right: Math.max(8, window.innerWidth - rect.right) });
            setMenuOpen((open) => !open);
          }}
          style={{ width: 28, height: 28, display: "grid", placeItems: "center", border: "none", borderRadius: 7, background: menuOpen ? inkA(0.06) : "transparent", color: UI.ink3, cursor: "pointer" }}><EllipsisVertical size={15} /></button>
        {/* 카드 스택/오버플로 컨텍스트 안에서는 다음 카드가 메뉴 위에 그려져 클릭을
            가로챈다(가림+미동작의 공통 원인). body portal + fixed 좌표로 최상위에 띄운다. */}
        {menuOpen && menuPos && createPortal(
          // body portal 은 셸(.uni) 스코프 밖이라 폰트·hover 스타일이 빠져 엉성해 보였다.
          // uni 클래스로 셸 타이포·토큰을 상속하고, rrow 로 항목 hover 피드백을 붙인다.
          <span ref={portalMenuRef} role="menu" aria-label={`${cl.displayName} 클러스터 작업`}
            className="uni"
            onClick={(event) => event.stopPropagation()}
            style={{ position: "fixed", top: menuPos.top, right: menuPos.right, zIndex: 1200, width: 184, display: "flex", flexDirection: "column", gap: 2, padding: 6, border: `1px solid ${UI.line}`, borderRadius: 12, background: UI.card, boxShadow: `0 16px 40px -18px ${inkA(0.35)}` }}>
            <button role="menuitem" type="button" className="rrow" onClick={activate} style={compactMenuStyle}><ExternalLink size={15} style={{ flexShrink: 0, color: UI.ink3 }} />상세 보기</button>
            {onSettings && <button role="menuitem" type="button" className="rrow" onClick={() => { setMenuOpen(false); onSettings(); }} style={compactMenuStyle}><Settings size={15} style={{ flexShrink: 0, color: UI.ink3 }} />설정</button>}
            {onDisconnect && <button role="menuitem" type="button" className="rrow" onClick={() => { setMenuOpen(false); onDisconnect(); }} style={{ ...compactMenuStyle, color: HP.crit }}><Unplug size={15} style={{ flexShrink: 0 }} />연결 해제…</button>}
          </span>,
          document.body,
        )}
      </span>
    </motion.div>
  );
}

const compactMenuStyle: React.CSSProperties = {
  display: "flex", alignItems: "center", gap: 9, width: "100%", border: "none", borderRadius: 8,
  background: "transparent", color: UI.ink, padding: "9px 10px", textAlign: "left", fontSize: TYPE.label,
  fontWeight: 600, cursor: "pointer",
};

export function HomeClustersWidget({ summaries, onOpen, onSettings, onDisconnect, pending = [] }: {
  summaries: Readonly<Record<string, ClusterSummaryView>>;
  onOpen: (clusterId: string) => void;
  onSettings?: (clusterId: string) => void;
  onDisconnect?: (clusterId: string) => void;
  pending?: string[];
}) {
  const { clusters } = useDevpreviewContracts();
  return (
    <div data-home-clusters="compact" style={{ flex: 1, minHeight: 0, overflowY: "auto", overscrollBehavior: "contain", scrollbarGutter: "stable" }}>
      {clusters.map((cluster) => (
        <CompactClusterRow key={cluster.id} cl={cluster} summary={summaries[cluster.id]}
          onOpen={() => onOpen(cluster.id)}
          onSettings={onSettings ? () => onSettings(cluster.id) : undefined}
          onDisconnect={onDisconnect ? () => onDisconnect(cluster.id) : undefined} />
      ))}
      {pending.map((name) => (
        <div key={name} className="home-cluster-pending-row" style={{ display: "grid", gridTemplateColumns: "var(--home-cluster-pending-columns, minmax(170px, 1.35fr) minmax(210px, 1.25fr) minmax(180px, 1fr) 28px)", alignItems: "center", gap: "var(--home-cluster-pending-gap, 14px)", minHeight: 54, padding: "8px 4px", borderTop: `1px solid ${UI.line2}` }}>
          <span style={{ minWidth: 0, fontSize: TYPE.label, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</span>
          <span style={{ fontSize: TYPE.caption, color: TINT.blue.fg }}>부트스트랩 중 · 첫 인벤토리 대기</span>
          <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>CPU — · MEM —</span><span />
        </div>
      ))}
      {clusters.length === 0 && pending.length === 0 && <span style={{ display: "block", padding: "18px 4px", color: UI.ink3, fontSize: TYPE.label }}>연결된 클러스터가 없습니다.</span>}
    </div>
  );
}

// '+ 클러스터 연결' 점선 카드 — 지도 클러스터 뷰와 홈 클러스터 섹션이 하나를 공유(두 번째 구현 금지)
// compact = 홈 섹션용 가로형(카드 높이를 잡아먹지 않는다) · 기본 = 지도 클러스터 뷰의 카드형
function AddClusterCard({ onClick, delay = 0, compact = false }: { onClick: () => void; delay?: number; compact?: boolean }) {
  return (
    <motion.button initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SOFT, delay }}
      onClick={onClick} whileHover={{ borderColor: TINT.blue.bd, background: blueA(0.03) }}
      style={compact
        ? { display: "flex", alignItems: "center", justifyContent: "center", gap: 10, minHeight: 60,
            border: `1.5px dashed ${LINE3}`, borderRadius: 14, background: "transparent", cursor: "pointer" }
        : { display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8, minHeight: 200,
            border: `1.5px dashed ${LINE3}`, borderRadius: 16, background: "transparent", cursor: "pointer" }}>
      <span style={{ width: compact ? 26 : 34, height: compact ? 26 : 34, borderRadius: 999, background: blueA(0.09), display: "grid", placeItems: "center", color: BLUE, fontSize: compact ? TYPE.body : TYPE.section, fontWeight: 600, lineHeight: 1 }}>+</span>
      <span style={{ fontSize: TYPE.body, fontWeight: 600, color: UI.heading }}>클러스터 연결</span>
      <span style={{ fontSize: TYPE.caption, color: UI.ink3 }}>에이전트 설치로 등록</span>
    </motion.button>
  );
}

// 방금 등록한 클러스터 — 에이전트 부트스트랩 후 첫 인벤토리 수집을 기다리는 정직한 상태.
// 이름 파생 CPU/메모리/노드 수·타이머 Active 승격은 제거했다. 서버가 상태를 주기
// 전까지 관측값은 "관측 안 됨"으로 남는다(가짜로 채우지 않는다).
export function PendingClusterCard({ name, delay = 0 }: { name: string; delay?: number }) {
  return (
    <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SOFT, delay }}
      style={{ display: "flex", flexDirection: "column", gap: 12, minHeight: 0, height: "100%", background: UI.card, border: `1px solid ${blueA(0.3)}`, borderRadius: 16, padding: 16, boxSizing: "border-box" }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 10, minWidth: 0 }}>
        <span style={{ width: 30, height: 30, borderRadius: 9, background: inkA(0.06), display: "grid", placeItems: "center", flexShrink: 0 }}>
          <AwsIcon size={17} style={{ color: UI.ink3 }} />
        </span>
        <span style={{ minWidth: 0, flex: 1 }}>
          <span style={{ fontSize: TYPE.section, fontWeight: 700, letterSpacing: "-0.02em", color: UI.ink, display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</span>
          <span style={{ display: "block", fontSize: TYPE.caption, color: UI.ink3, marginTop: 2 }}>Amazon EKS · 버전 관측 대기</span>
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 5, fontSize: TYPE.caption, fontWeight: 600, color: TINT.blue.fg, background: blueA(0.08), border: `1px solid ${blueA(0.25)}`, borderRadius: 999, padding: "3px 9px", flexShrink: 0 }}><span className="pulsedot" style={{ width: 6, height: 6, borderRadius: 999, background: BLUE }} />부트스트랩 중</span>
      </div>
      <div style={{ fontSize: TYPE.label, color: UI.ink2 }}>부트스트랩 중 · 첫 인벤토리 수집 대기</div>
      <div style={{ marginTop: "auto", display: "flex", flexDirection: "column", gap: 7 }}>
        <ClusterMiniUsage label="CPU" value={null} />
        <ClusterMiniUsage label="MEM" value={null} />
      </div>
    </motion.div>
  );
}

// ── 홈 서피스용 클러스터 섹션 (D21 2층 — 보드 밖 고정) — 카드는 지도와 같은 ClusterRow 하나 ──
export function HomeClusterSection({ meta: _meta, onOpen, pending = [] }: {
  meta?: Record<string, Record<string, number>>; onOpen: (clId: string) => void; pending?: string[];
}) {
  const { clusters } = useDevpreviewContracts();
  const clusterIds = useMemo(() => clusters.map((cl) => cl.id), [clusters]);
  const summaries = useClusterSummaries(clusterIds);
  // priority 14: 홈은 OpsiaMap의 `.op` 스타일 블록(반응형 media query 포함) 밖에서
  // 렌더되므로 그 media query가 적용되지 않는다. 좁은 화면 1열 전환을 인라인으로 보장한다.
  const narrow = useNarrowViewport();
  return (
    <div className={HOME_CARD_GRID_CLASS} data-home-card-grid="uniform" style={homeCardGridStyle(narrow)}>
      {clusters.map((cl) => (
        <div key={cl.id} className={HOME_CARD_GRID_ITEM_CLASS} data-home-card-layout="unit" style={homeCardGridItemStyle(narrow)}>
          <ClusterRow cl={cl} summary={summaries[cl.id]} onOpen={() => onOpen(cl.id)} />
        </div>
      ))}
      {pending.map((n, i) => (
        <div key={n} className={HOME_CARD_GRID_ITEM_CLASS} data-home-card-layout="unit" style={homeCardGridItemStyle(narrow)}>
          <PendingClusterCard name={n} delay={(clusters.length + i) * 0.05} />
        </div>
      ))}
      {/* 홈에는 연결 카드 없음 — 고정 헤더의 "+ 클러스터 연결" 버튼이 유일한 진입(중복 금지). 카드는 지도 클러스터 뷰 전용 */}
    </div>
  );
}

// ── 노드 카드 — 정본 physical topology 계약의 서버·측정값·pod count만 렌더한다.
export function NodeCard({
  node,
  pods,
  highlightedPodIdentities,
  podHighlightActive,
  problemPodCount,
  nodeAlias,
  onOpen,
  onTip,
  onSaveNodeAlias,
  onDeleteNodeAlias,
}: {
  node: InvNode;
  pods: readonly InvPod[];
  highlightedPodIdentities?: ReadonlySet<string>;
  podHighlightActive?: boolean;
  problemPodCount: number | null;
  nodeAlias?: NodeAliasView | null;
  onOpen: () => void;
  onTip: (t: TipData) => void;
  onSaveNodeAlias?: (nodeName: string, alias: string) => Promise<NodeAliasView | null>;
  onDeleteNodeAlias?: (nodeName: string) => Promise<void>;
}) {
  const sev = healthSev(node.health);
  // "준비됨" 상태 서브라인은 제거 — 정상 상태는 표시하지 않고, 이상 신호만
  // 헤더 점·HealthChip 으로 드러낸다(레퍼런스 톤).
  const problemConditionCount = (node.conditions ?? []).filter((condition) => !/^ready$/i.test(condition)).length;
  const restartsRecent = node.restartsRecent ?? 0;
  return (
    <motion.div transition={SPRING} onClick={onOpen}
      whileHover={{ boxShadow: `0 10px 26px -20px ${inkA(0.16)}`, borderColor: LINE3 }}
      onMouseMove={(event) => {
        if (event.target instanceof Element && event.target.closest("[data-slot-state]")) return;
        onTip(null);
      }}
      onMouseLeave={() => onTip(null)}
      style={{
        display: "flex", flexDirection: "column", gap: 10, width: "100%", height: "100%", textAlign: "left", cursor: "pointer",
        background: UI.card, border: `1px solid ${UI.line}`, borderRadius: 16, padding: 16, boxShadow: "none", boxSizing: "border-box", overflow: "hidden", minHeight: 0,
      }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8, minWidth: 0 }}>
        {/* 아이콘 박스는 제목+호스트명 두 줄 높이(≈40px)에 맞춘다 */}
        <span style={{ width: 40, height: 40, borderRadius: 10, background: UI.bg2, display: "grid", placeItems: "center", flexShrink: 0 }}>
          <Monitor size={22} strokeWidth={2} style={{ color: UI.ink2 }} />
        </span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <NodeAliasTitle
            alias={nodeAlias ?? null}
            nodeName={node.name}
            onOpen={onOpen}
            onDelete={onDeleteNodeAlias}
            onSave={onSaveNodeAlias}
          />
        </div>
        {/* 레퍼런스 톤: 문제 배지·슬롯 카운트를 헤더 우측 상단에 — 별도 "파드 슬롯" 라벨 행 제거 */}
        <span style={{ display: "inline-flex", alignItems: "center", gap: 7, flexShrink: 0, marginTop: 2 }}>
          {problemPodCount !== null && problemPodCount > 0 && (
            <span aria-label={`문제 파드 ${problemPodCount}`} style={{ display: "inline-flex", alignItems: "center", gap: 3, fontSize: TYPE.caption, fontWeight: 700, color: TINT.crit.fg, background: TINT.crit.bg, border: `1px solid ${TINT.crit.bd}`, borderRadius: 7, padding: "1px 6px" }}>
              <b style={{ fontVariantNumeric: "tabular-nums" }}>{problemPodCount}</b><AlertTriangle size={11} aria-hidden="true" />
            </span>
          )}
          <span aria-label={`파드 슬롯 ${node.matchedPodCount ?? "미관측"}/${node.totalPodCount ?? "미관측"}`} style={{ fontSize: TYPE.label, fontFamily: MONO, fontWeight: 700, color: UI.ink2, fontVariantNumeric: "tabular-nums" }}>
            {node.matchedPodCount ?? "—"}/{node.totalPodCount ?? "—"}
          </span>
          {sev !== "ok" && <span style={{ width: 8, height: 8, borderRadius: 999, background: sevColor(sev), flexShrink: 0 }} />}
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        {sev !== "ok" && <HealthChip health={node.health} />}
        {restartsRecent > 0 && (
          <span aria-label={`최근 재시작 ${restartsRecent}`} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: TYPE.caption, fontWeight: 600, color: TINT.warn.fg }}>
            <RotateCcw size={12} aria-hidden="true" /><b style={{ fontVariantNumeric: "tabular-nums" }}>{restartsRecent}</b>최근 재시작
          </span>
        )}
        {problemConditionCount > 0 && (
          <span aria-label={`문제 조건 ${problemConditionCount}`} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: TYPE.caption, fontWeight: 600, color: TINT.warn.fg }}>
            <AlertTriangle size={12} aria-hidden="true" /><b style={{ fontVariantNumeric: "tabular-nums" }}>{problemConditionCount}</b>조건
          </span>
        )}
      </div>
      <div style={{ display: "grid", gap: 6, marginTop: "auto" }}>
        <ClusterMiniUsage label="CPU" value={node.cpuPercent} />
        <ClusterMiniUsage label="MEM" value={node.memoryPercent} />
      </div>
      <NodePodSlotGrid
        node={node}
        pods={pods}
        highlightedPodIdentities={highlightedPodIdentities}
        podHighlightActive={podHighlightActive}
        onTip={onTip}
      />
    </motion.div>
  );
}

type NodePodSlotState = "occupied" | "warning" | "critical" | "pending" | "empty";

/**
 * Compact capacity map restored from the original node drill. Every square is
 * backed by the observed node-summary running/capacity counts. When physical
 * topology contains the corresponding Pod, its actual health/phase refines the
 * occupied square; unreturned occupied Pods stay green because `pods_running`
 * is itself observed evidence. Empty capacity is neutral and never fabricated.
 */
export function NodePodSlotGrid({ node, pods, highlightedPodIdentities, podHighlightActive = false, onTip }: {
  node: InvNode;
  pods: readonly InvPod[];
  highlightedPodIdentities?: ReadonlySet<string>;
  podHighlightActive?: boolean;
  onTip?: (tip: TipData) => void;
}) {
  const reducedMotion = useReducedMotion();
  const capacity = node.totalPodCount;
  const occupied = node.matchedPodCount;
  if (capacity === null || occupied === null || capacity <= 0) return null;

  const safeCapacity = Math.max(0, Math.floor(capacity));
  const safeOccupied = Math.min(safeCapacity, Math.max(0, Math.floor(occupied)));
  const span = nodeCardColumnSpan(node);
  const visibleCapacity = Math.min(safeCapacity, span * NODE_SLOT_COUNT_PER_COLUMN, NODE_SLOT_RENDER_LIMIT);
  const hiddenPodCount = Math.max(0, safeOccupied - visibleCapacity);
  const observedPods = [...pods]
    .sort((left, right) => slotRank(left) - slotRank(right)
      || left.namespace?.localeCompare(right.namespace ?? "")
      || left.name.localeCompare(right.name))
    .slice(0, Math.min(safeOccupied, visibleCapacity));
  const columns = span * NODE_SLOT_COLUMNS_PER_UNIT;
  const slots = Array.from({ length: visibleCapacity }, (_, index): { state: NodePodSlotState; pod: InvPod | null } => {
    if (index >= safeOccupied) return { state: "empty", pod: null };
    const pod = observedPods[index] ?? null;
    return { state: pod ? podSlotState(pod) : "occupied", pod };
  });

  return (
    <div
      role="img"
      aria-label={`파드 슬롯 ${safeOccupied}/${safeCapacity}${hiddenPodCount > 0 ? `, ${visibleCapacity}개 표시, 파드 ${hiddenPodCount}개 더 있음` : ""}`}
      title={`파드 슬롯 ${safeOccupied}/${safeCapacity} · 실제 관측 상태${hiddenPodCount > 0 ? ` · 파드 ${visibleCapacity}개 표시, ${hiddenPodCount}개 생략` : ""}`}
      data-slot-columns={columns}
      data-visible-slot-count={visibleCapacity}
      data-hidden-pod-count={hiddenPodCount}
      style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}
    >
      <span className="node-slot-grid" aria-hidden="true" style={{ display: "grid", gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gap: 4, flex: 1, minWidth: 0 }}>
        {slots.map(({ state, pod }, index) => {
          const highlighted = pod !== null && highlightedPodIdentities?.has(
            podHighlightIdentity(pod.cluster, pod.namespace, pod.name),
          ) === true;
          const dimmed = podHighlightActive && state !== "empty" && !highlighted;
          return (
          <motion.span
            data-slot-state={state}
            data-pod-name={pod?.name}
            data-pod-highlighted={highlighted ? "true" : undefined}
            data-pod-dimmed={dimmed ? "true" : undefined}
            data-pod-cpu-millicores={pod?.cpuMillicores ?? undefined}
            data-pod-memory-mebibytes={pod?.memoryMebibytes ?? undefined}
            data-pod-limit-utilization={pod ? podLimitUtilizationPercent(pod) ?? undefined : undefined}
            key={pod?.key ?? index}
            initial={reducedMotion ? false : { opacity: 0, scale: 0.76 }}
            animate={{ opacity: dimmed ? 0.22 : 1, scale: 1 }}
            whileHover={state === "empty" || reducedMotion ? undefined : { scale: 1.08 }}
            transition={reducedMotion ? { duration: 0 } : { duration: 0.12, ease: "easeOut" }}
            onMouseEnter={(event) => {
              if (state === "empty" || !onTip) return;
              event.stopPropagation();
              onTip(pod
                ? { x: event.clientX, y: event.clientY, label: pod.name, status: pod.status, health: pod.health, metrics: podTipMetrics(pod) }
                : { x: event.clientX, y: event.clientY, label: "파드 정보 관측 안 됨", status: "슬롯 사용 중", health: "" });
            }}
            onMouseMove={(event) => {
              if (state === "empty" || !onTip) return;
              event.stopPropagation();
              onTip(pod
                ? { x: event.clientX, y: event.clientY, label: pod.name, status: pod.status, health: pod.health, metrics: podTipMetrics(pod) }
                : { x: event.clientX, y: event.clientY, label: "파드 정보 관측 안 됨", status: "슬롯 사용 중", health: "" });
            }}
            onMouseLeave={(event) => {
              if (state === "empty" || !onTip) return;
              event.stopPropagation();
              onTip(null);
            }}
            style={{
              position: "relative",
              aspectRatio: "1",
              minWidth: 0,
              borderRadius: 3,
              border: `1px solid ${state === "empty" ? UI.line2 : slotColor(state, pod)}`,
              background: state === "empty" ? HP.ghost : slotColor(state, pod),
              outline: highlighted ? `2px solid ${BLUE}` : "none",
              outlineOffset: -2,
              boxShadow: highlighted ? `0 0 0 3px ${blueA(0.22)}` : "none",
              filter: dimmed ? "grayscale(0.5) saturate(0.45)" : highlighted ? "saturate(1.18)" : "none",
              zIndex: highlighted ? 1 : 0,
              transition: reducedMotion ? "none" : "background-color .3s ease, border-color .3s ease, filter .12s ease, box-shadow .12s ease",
            }}
          >
            {state === "pending" && (
              <span className="pulsedot" style={{ position: "absolute", inset: "35%", borderRadius: 999, background: UI.ink3 }} />
            )}
          </motion.span>
          );
        })}
      </span>
      {hiddenPodCount > 0 && (
        <span
          aria-hidden="true"
          data-slot-overflow={hiddenPodCount}
          style={{ flexShrink: 0, fontVariantNumeric: "tabular-nums", fontSize: TYPE.caption, fontWeight: 600, color: UI.ink3, whiteSpace: "nowrap" }}
        >
          +{hiddenPodCount}
        </span>
      )}
    </div>
  );
}

function podSlotState(pod: InvPod): NodePodSlotState {
  if (isBadHealth(pod.health) || /crash|error|fail|evict/i.test(`${pod.status} ${pod.health}`)) return "critical";
  if (!/running/i.test(pod.status) || /pending|unknown|progress|creating/i.test(pod.health)) return "pending";
  if (healthSev(pod.health) === "warn" || (podLimitUtilizationPercent(pod) ?? 0) >= POD_SLOT_WARNING_PERCENT) return "warning";
  return "occupied";
}

function slotRank(pod: InvPod): number {
  const state = podSlotState(pod);
  return state === "critical" ? 0 : state === "warning" ? 1 : state === "pending" ? 2 : 3;
}

function podLimitUtilizationPercent(pod: InvPod): number | null {
  const ratios = [
    pod.cpuMillicores != null && pod.cpuLimitMillicores != null
      ? pod.cpuMillicores / pod.cpuLimitMillicores * 100
      : null,
    pod.memoryMebibytes != null && pod.memoryLimitMebibytes != null
      ? pod.memoryMebibytes / pod.memoryLimitMebibytes * 100
      : null,
  ].filter((value): value is number => value !== null && Number.isFinite(value));
  return ratios.length > 0 ? Math.max(...ratios) : null;
}

function slotColor(state: Exclude<NodePodSlotState, "empty">, pod: InvPod | null): string {
  if (state === "pending") return HP.pending;
  const base = state === "critical" ? HP.crit : state === "warning" ? HP.warn : HP.ok;
  const utilization = pod ? podLimitUtilizationPercent(pod) : null;
  const mix = state === "critical"
    ? 94
    : state === "warning"
      ? utilization === null ? 70 : Math.min(92, 66 + Math.max(0, utilization - POD_SLOT_WARNING_PERCENT) * 1.3)
      : utilization === null ? 58 : Math.min(82, 36 + Math.max(0, utilization) * 0.52);
  return `color-mix(in srgb, ${base} ${Math.round(mix)}%, ${UI.card})`;
}

const POD_SLOT_WARNING_PERCENT = 80;
const NODE_SLOT_COUNT_PER_COLUMN = 10;
const NODE_SLOT_COLUMNS_PER_UNIT = 5;
const NODE_SLOT_RENDER_LIMIT = 40;

// PodSlotLegend(정상/주의/장애/대기 범례)는 제거 — 슬롯 색·호버 툴팁이 자체 설명적이라
// 상단 설명 줄 없이 노드 그리드를 바로 보여준다.

/**
 * Node cards share the same four-column geometry as dashboard widgets. One
 * unit represents up to ten observed Pod slots (five squares by two rows).
 * Larger nodes consume more horizontal units, capped at the full-width four
 * unit card; capacity beyond forty is summarized instead of expanding the DOM.
 */
export function nodeCardColumnSpan(node: Pick<InvNode, "matchedPodCount" | "totalPodCount">): 1 | 2 | 3 | 4 {
  const observedCount = Math.max(node.totalPodCount ?? 0, node.matchedPodCount ?? 0, 1);
  return Math.min(4, Math.max(1, Math.ceil(observedCount / NODE_SLOT_COUNT_PER_COLUMN))) as 1 | 2 | 3 | 4;
}

/**
 * The node-summary endpoint is the preferred CPU/MEM source used by the Home
 * cards. The physical topology endpoint also carries observed CPU/MEM evidence;
 * retain it when the summary is unavailable or one summary metric is absent.
 * This is not a generated fallback: both values come from typed live APIs.
 * Summary-only nodes remain visible while the heavier topology projection is
 * partial; duplicate summary names collapse to one honest card.
 */
export function nodesWithSummaryMetrics(
  topologyNodes: readonly InvNode[],
  summary: ClusterSummaryView | undefined,
  clusterId: string,
): InvNode[] {
  const topologyByName = new Map(topologyNodes.map((node) => [node.name, node]));
  if (summary?.status !== "ready") {
    return [...topologyNodes];
  }

  const summaryByName = new Map(summary.nodes.map((node) => [node.name, node]));
  const names = new Set([...topologyByName.keys(), ...summaryByName.keys()]);
  return Array.from(names, (name) => {
    const node = summaryByName.get(name);
    if (node === undefined) return topologyByName.get(name)!;
    const topologyNode = topologyByName.get(node.name);
    return {
      name: node.name,
      status: node.ready ? "ready" : "not_ready",
      health: node.health,
      cluster: clusterId,
      key: topologyNode?.key ?? node.name,
      cpuPercent: node.cpuPct ?? topologyNode?.cpuPercent ?? null,
      memoryPercent: node.memPct ?? topologyNode?.memoryPercent ?? null,
      matchedPodCount: node.podsRunning,
      totalPodCount: node.podsCapacity,
      restartsRecent: node.restartsRecent,
      conditions: node.conditions,
    };
  });
}

function observedPodsForNode(
  node: InvNode,
  topologyNodes: readonly InvNode[],
  pods: readonly InvPod[],
): InvPod[] | null {
  const topologyNode = topologyNodes.find((candidate) => candidate.name === node.name);
  if (topologyNode === undefined) return null;
  return podsForNode(pods, topologyNode.key);
}

// ── 파드 행 — 목록은 name/ns/status/health에 집중하고,
// hover에서 계약이 제공하는 CPU/MEM/재시작 관측값을 보완한다.
function PodRow({ pod, onClick, onTip }: {
  pod: InvPod; onClick: () => void; onTip: (t: TipData) => void;
}) {
  const sev = healthSev(pod.health);
  return (
    <motion.button data-pod={pod.key} onClick={(e) => { e.stopPropagation(); onClick(); }}
      onMouseEnter={(e) => onTip({ x: e.clientX, y: e.clientY, label: pod.name, status: pod.status, health: pod.health, metrics: podTipMetrics(pod) })}
      onMouseMove={(e) => onTip({ x: e.clientX, y: e.clientY, label: pod.name, status: pod.status, health: pod.health, metrics: podTipMetrics(pod) })}
      onMouseLeave={() => onTip(null)}
      whileTap={{ scale: 0.995 }}
      className="podrow"
      style={{
        display: "grid", gridTemplateColumns: "12px minmax(160px,1.8fr) minmax(90px,1fr) 92px 96px", alignItems: "center", gap: 12, width: "100%", textAlign: "left",
        border: "none", background: "transparent", borderRadius: 9, padding: "8px 10px", cursor: "pointer",
      }}>
      <span style={{ width: 10, height: 10, borderRadius: 3, background: sevColor(sev) }} />
      <span style={{ fontSize: TYPE.body, fontWeight: 600, fontFamily: MONO, color: UI.ink, letterSpacing: "-0.01em", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{pod.name}</span>
      <span style={{ fontSize: TYPE.caption, fontFamily: MONO, color: UI.ink3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{pod.namespace ?? "—"}</span>
      <span style={{ fontSize: TYPE.caption, color: UI.ink2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{pod.status ? statusLabel(pod.status) : "—"}</span>
      <span style={{ justifySelf: "start" }}><HealthChip health={pod.health} /></span>
    </motion.button>
  );
}

// ── 앱 ─────────────────────────────
// embedded: 셸(통합 리소스)에 내장될 때 자체 헤더·내비를 숨기고 스코프 변화를 알림
export function OpsiaMap({ embedded = false, onScopeChange, onOpenResource, lensTab, onAddCluster, onAddRepo, onOpenRepository, stickyTop, viewportTopInset = 0, initialCluster, selectedNamespace = null, pendingClusters, pendingRepos, connectedRepos, repositoryGroups, onRepositoryDisconnected }: {
  embedded?: boolean;
  onScopeChange?: (v: View) => void;
  /** 임베드 모드: 파드 클릭 시 셸의 통합 상세 오버레이를 연다 (내부 패널 대신) */
  onOpenResource?: (kind: "Pod", data: Record<string, unknown>) => void;
  /** 셸의 종류 선택과 연결 보기 탭 동기화 */
  lensTab?: "svc" | "cfg" | "git" | null;
  /** 실서비스 배치: 클러스터 뷰의 '+ 연결' 카드 / 배포 탭의 '+ 저장소 연결' */
  onAddCluster?: () => void;
  onAddRepo?: () => void;
  /** 연결 패널의 저장소 카드를 배포/GitOps 저장소 필터로 연다. */
  onOpenRepository?: (repositoryRef: string) => void;
  /** 탐색 패널 고정 오프셋(CSS px) — 셸 sticky 헤더 바로 아래 */
  stickyTop?: number;
  /** 임베드된 셸의 고정 헤더 높이. 보조 패널의 가용 높이 계산에 사용한다. */
  viewportTopInset?: number;
  /** 홈 카드 클릭 등 외부 진입 시 해당 클러스터 노드 뷰로 시작 (스코프 전달 — D21) */
  initialCluster?: string;
  /** 셸 네임스페이스 선택값. null이면 클러스터의 모든 네임스페이스를 본다. */
  selectedNamespace?: string | null;
  /** 세션 중 등록된 연결 대기 항목 — 목록에 실반영(등록의 결과가 보여야 한다) */
  pendingClusters?: string[];
  pendingRepos?: string[];
  /** 서버가 active로 확정한 GitOps 저장소. 세션 대기값과 섞지 않는다. */
  connectedRepos?: string[];
  repositoryGroups?: RepositoryGroup[];
  onRepositoryDisconnected?: (repositoryRef: string) => void;
} = {}) {
  const { clusters } = useDevpreviewContracts();
  const clusterIds = useMemo(() => clusters.map((cl) => cl.id), [clusters]);
  const reducedMotion = useReducedMotion();

  const [view, setView] = useState<View>(initialCluster ? { level: "nodes", cluster: initialCluster } : { level: "clusters" });
  useEffect(() => { onScopeChange?.(view); }, [view]);

  // 드릴된 클러스터의 관측된 노드·파드(실 인벤토리 계약). 클러스터 뷰에선 null → 무요청.
  const activeCluster = view.level === "clusters" ? null : view.cluster;
  // 클러스터 목록에서는 모든 카드 요약을 병렬 조회하지만, 한 클러스터를 드릴한
  // 뒤에는 그 클러스터만 조회한다. 노드 화면 진입 때 보이지 않는 다른 클러스터의
  // node-summary 요청을 반복하지 않아 첫 유효 화면을 방해하지 않는다.
  const summaryClusterIds = useMemo(
    () => activeCluster === null ? clusterIds : [activeCluster],
    [activeCluster, clusterIds],
  );
  const [podHighlightTarget, setPodHighlightTarget] = useState<PodHighlightTarget | null>(null);
  const highlightedApplicationIds = podHighlightTarget?.type === "applications"
    ? podHighlightTarget.applicationIds
    : [];
  const clusterSummaries = useClusterSummaries(summaryClusterIds);
  const topology = useClusterTopology(activeCluster);
  const relationTopology = useRelationTopology(
    activeCluster ? [activeCluster] : [],
    highlightedApplicationIds,
  );
  const nodeAliasCluster = view.level === "clusters" ? null : activeCluster;
  const nodeAliases = useNodeAliases(nodeAliasCluster);
  const nodeAliasEditingAvailable = nodeAliasCluster !== null;
  const { nodes: topologyNodes, pods } = topology;
  const activeSummary = activeCluster ? clusterSummaries[activeCluster] : undefined;
  const nodes = useMemo(
    () => nodesWithSummaryMetrics(topologyNodes, activeSummary, activeCluster ?? ""),
    [activeCluster, activeSummary, topologyNodes],
  );
  const activeNode = view.level === "pods" ? nodes.find((node) => node.name === view.node) : undefined;
  const nodePods = view.level === "pods" && activeNode
    ? podsForNode(pods, activeNode.key)
    : [];
  const crit = pods.filter((p) => isBadHealth(p.health)).length;

  const [dir, setDir] = useState(1);
  const [mode, setMode] = useState<"push" | "hero">("push");
  // 노드 ↔ 파드 = 같은 대상에 더 가까이(히어로 확장) · 그 외 = 레벨 이동(푸시 슬라이드)
  const go = (v: View, d: number) => {
    const hero = (view.level === "nodes" && v.level === "pods") || (view.level === "pods" && v.level === "nodes");
    setMode(hero ? "hero" : "push");
    setDir(d); setView(v);
    window.scrollTo({ top: 0, behavior: "auto" });
  };
  const [tip, setTip] = useState<TipData>(null);
  const highlightedPodIdentities = useMemo(
    () => resolveHighlightedPodIdentities(
      podHighlightTarget,
      relationTopology.evidenceNodes,
      relationTopology.evidenceEdges,
    ),
    [podHighlightTarget, relationTopology.evidenceEdges, relationTopology.evidenceNodes],
  );
  const podHighlightActive = useMemo(
    () => podHighlightTarget !== null && pods.some((pod) => highlightedPodIdentities.has(
      podHighlightIdentity(pod.cluster, pod.namespace, pod.name),
    )),
    [highlightedPodIdentities, podHighlightTarget, pods],
  );
  // 툴팁 좌표는 CSS px로 — zoom(PRESENT_SCALE) 컨테이너 안 fixed는 시각 px 그대로 쓰면 스케일만큼 어긋난다
  const tipScale = embedded ? PRESENT_SCALE : 1;
  const onTip = (t: TipData) => setTip(t ? { ...t, x: t.x / tipScale, y: t.y / tipScale } : null);

  const selectPod = (p: InvPod) => {
    // 임베드 모드: 상세는 셸의 최상위 오버레이 하나로 일원화(내부 패널과 이원화 금지).
    // health는 행의 상태 표시만 바꾸며, incident identity가 없는 파드 행에서
    // 임의의 RCA 객체를 만들지 않는다. 이슈 진입은 이슈/알림 화면이 소유한다.
    if (embedded && onOpenResource) {
      const selection = podResourceSelection(p);
      onOpenResource(selection.kind, selection.data);
    }
  };

  const viewKey = view.level === "clusters" ? "clusters" : view.level === "nodes" ? `nodes-${view.cluster}` : `pods-${view.node}`;
  const crumbs: { label: string; onClick?: () => void }[] = [{ label: "클러스터", onClick: view.level !== "clusters" ? () => go({ level: "clusters" }, -1) : undefined }];
  if (view.level !== "clusters") crumbs.push({ label: view.cluster, onClick: view.level === "pods" ? () => go({ level: "nodes", cluster: view.cluster }, -1) : undefined });
  if (view.level === "pods") crumbs.push({ label: view.node });
  const tipWidth = tip?.metrics ? 468 : 280;
  const tipHeight = tip?.metrics ? 176 : 88;
  const tipViewportWidth = typeof window === "undefined" ? tipWidth : Math.min(tipWidth, window.innerWidth - 16);
  const tipLeft = tip && typeof window !== "undefined"
    ? Math.max(8, Math.min(tip.x + 14, window.innerWidth - tipViewportWidth - 8))
    : 8;
  const tipTop = tip && typeof window !== "undefined"
    ? Math.max(8, Math.min(tip.y + 16, window.innerHeight - tipHeight - 8))
    : 8;

  return (
    <div className="op" data-embedded={embedded ? "true" : "false"}>
      <div style={{ width: embedded ? "100%" : 1220, maxWidth: "100%", margin: "0 auto", padding: embedded ? 0 : "32px 24px 48px" }}>
        {!embedded && (
        <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 14 }}>
            <h1 style={{ margin: 0, fontSize: TYPE.page, fontWeight: 700, letterSpacing: "-0.03em", color: UI.ink }}>통합 맵</h1>
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: TYPE.label, fontWeight: 600, color: UI.ink2 }}>
              <span className="pulsedot" style={{ width: 6, height: 6, borderRadius: 999, background: HP.ok }} />
              실시간 · {clusters.length} 클러스터
            </div>
          </div>
        </header>
        )}

        {/* 세 관점 공통 2열 프레임: 요약·경로는 좌측 본문에 포함하고,
            우측 보조 패널은 다른 관점과 같은 상단선에서 시작한다. */}
        <div className="opsia-content-layout" style={{ display: "flex", gap: RESOURCE_LAYOUT.columnGap, alignItems: "flex-start", flexWrap: "wrap" }}>
          <div className="opsia-main-pane" style={{ flex: "1 1 440px", minWidth: 0, position: "relative" }}>
        {/* 상태 요약 줄 — 클러스터 수(실). 드릴 시 관측된 노드·파드 수를 정직하게 표기. */}
        {(() => {
          const seg: React.CSSProperties = { display: "flex", alignItems: "center", gap: 5, fontSize: TYPE.label, fontWeight: 600, color: UI.ink2, background: UI.card, border: `1px solid ${UI.line}`, borderRadius: 999, padding: "5px 11px", whiteSpace: "nowrap" };
          const num: React.CSSProperties = { fontWeight: 700, color: UI.ink, fontVariantNumeric: "tabular-nums" };
          const drilled = view.level !== "clusters";
          const observing = drilled && (topology.status === "ready" || activeSummary?.status === "ready");
          const nodesReady = activeSummary?.status === "ready" ? activeSummary.nodesReady : topology.nodesReady;
          const nodesTotal = activeSummary?.status === "ready" ? activeSummary.nodesTotal : topology.nodesTotal;
          return (
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
              <span style={seg}><Server size={11} style={{ color: UI.ink3 }} />클러스터 <b style={num}>{clusters.length}</b>
                {(pendingClusters?.length ?? 0) > 0 && <span style={{ color: TINT.blue.fg }}>· 연결 중 {pendingClusters!.length}</span>}
              </span>
              {drilled && (
                <span style={seg}><Monitor size={12} style={{ color: UI.ink3 }} />노드 <b style={num}>{observing ? `${nodesReady ?? "—"}/${nodesTotal ?? "—"}` : topology.status === "loading" || activeSummary?.status === "loading" ? "…" : "—"}</b></span>
              )}
              {drilled && (
                <span style={seg}><Box size={11} style={{ color: UI.ink3 }} />파드 <b style={num}>{observing ? activeSummary?.podsTotal ?? topology.podsTotal ?? "—" : topology.status === "loading" ? "…" : "—"}</b></span>
              )}
              {drilled && (topology.stale || activeSummary?.stale === true) && (
                <span aria-label="실시간 관측 지연" style={{ ...seg, color: TINT.warn.fg, fontWeight: 600 }}>
                  <Clock3 size={11} />관측 지연
                </span>
              )}
              {observing && topology.partial && (
                <span title={topology.partialReasonCodes.map(reasonLabel).join(" · ") || "부분 관측"}
                  style={{ ...seg, color: TINT.warn.fg, fontWeight: 600 }}>
                  일부 관측{topology.truncatedPodCount > 0 ? ` · ${topology.truncatedPodCount}개 미표시` : ""}
                </span>
              )}
              {crit > 0 && <span style={{ width: 1, height: 16, background: UI.line, margin: "0 2px" }} />}
              {crit > 0 && (
                <span style={{ fontSize: TYPE.label, fontWeight: 600, color: HP.crit, display: "flex", alignItems: "center", gap: 5, border: `1px solid ${TINT.crit.bd}`, background: TINT.crit.bg, borderRadius: 999, padding: "5px 12px" }}>
                  <Activity size={12} />장애 {crit}
                </span>
              )}
            </div>
          );
        })()}

        {/* 브레드크럼 */}
        <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 16, minHeight: 28 }}>
          {view.level !== "clusters" && (
            <motion.button type="button" aria-label={view.level === "pods" ? "노드 목록으로 돌아가기" : "클러스터 목록으로 돌아가기"} title={view.level === "pods" ? "노드 목록으로" : "클러스터 목록으로"} whileTap={{ scale: 0.92 }} whileHover={{ borderColor: LINE3 }}
              onClick={() => go(view.level === "pods" ? { level: "nodes", cluster: view.cluster } : { level: "clusters" }, -1)}
              style={{ width: 26, height: 26, borderRadius: 999, border: `1px solid ${UI.line}`, background: UI.card, cursor: "pointer", display: "grid", placeItems: "center", marginRight: 6 }}>
              <ChevronLeft size={14} style={{ color: UI.ink2 }} />
            </motion.button>
          )}
          {crumbs.map((c, i) => (
            <span key={c.label} style={{ display: "flex", alignItems: "center", gap: 4 }}>
              {i > 0 && <ChevronRight size={11} style={{ color: INK4 }} />}
              <button onClick={c.onClick} disabled={!c.onClick}
                style={{ border: "none", background: "transparent", cursor: c.onClick ? "pointer" : "default", fontSize: TYPE.body, fontWeight: 600, color: i === crumbs.length - 1 ? UI.ink : UI.ink3, padding: "2px 4px", fontFamily: i > 0 ? MONO : undefined, letterSpacing: "-0.01em" }}>
                {c.label}
              </button>
            </span>
          ))}
        </div>

            {/* 좁아지면(AI 도킹 등) 어사이드가 아래로 내려간다 — 겹침 방지 */}
            <AnimatePresence mode="popLayout" custom={{ dir, mode }} initial={false}>
              <motion.div key={viewKey} custom={{ dir, mode }}
                variants={{
                  initial: (c: { dir: number; mode: string }) => (c.mode === "hero" ? { opacity: 0, scale: c.dir === 1 ? 0.96 : 1.02, y: c.dir === 1 ? 10 : -6 } : { opacity: 0, x: 46 * c.dir, scale: 0.985, filter: "blur(7px)" }),
                  animate: { opacity: 1, x: 0, y: 0, scale: 1, filter: "blur(0px)" },
                  exit: (c: { dir: number; mode: string }) => (c.mode === "hero" ? { opacity: 0, scale: c.dir === 1 ? 1.02 : 0.97, transition: { duration: DUR.fade } } : { opacity: 0, x: -42 * c.dir, scale: 0.99, filter: "blur(7px)" }),
                }}
                initial={reducedMotion ? false : "initial"}
                animate="animate"
                exit={reducedMotion ? undefined : "exit"}
                transition={reducedMotion ? { duration: 0 } : PAGE}>

                {view.level === "clusters" && (
                  /* 클러스터: 가로 최대 2개 · 카드 폭을 제한해 정사각에 가깝게 */
                  <div className="cluster-grid" style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 560px))", gap: 14, alignItems: "stretch" }}>
                    {clusters.map((cl, i) => (
                      <motion.div key={cl.id} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SOFT, delay: i * 0.05 }} style={{ display: "flex" }}>
                        <ClusterRow cl={cl} summary={clusterSummaries[cl.id]}
                          onOpen={() => go({ level: "nodes", cluster: cl.id }, 1)} />
                      </motion.div>
                    ))}
                    {(pendingClusters ?? []).map((n, i) => <PendingClusterCard key={n} name={n} delay={(clusters.length + i) * 0.05} />)}
                    {onAddCluster && <AddClusterCard onClick={onAddCluster} delay={(clusters.length + (pendingClusters?.length ?? 0)) * 0.05} />}
                  </div>
                )}

                {view.level === "nodes" && (<>
                  {nodes.length === 0 && topology.status === "loading" && activeSummary?.status !== "ready" ? (
                    <NodeSkeleton />
                  ) : nodes.length === 0 && topology.status === "unavailable" && activeSummary?.status !== "ready" ? (
                    <EmptyState icon={<Server size={18} strokeWidth={1.75} />} label="인벤토리 관측 안 됨"
                      hint="에이전트가 아직 이 클러스터의 노드 인벤토리를 보고하지 않았습니다." />
                  ) : nodes.length === 0 ? (
                    <EmptyState icon={<Cpu size={18} strokeWidth={1.75} />}
                      label={topology.partial ? "노드 관측 갱신 중" : "관측된 노드가 없습니다"}
                      hint={topology.partial
                        ? "불완전한 스냅샷을 수신했습니다. 다음 라이브 관측을 기다립니다."
                        : "이 클러스터에서 준비된 노드가 아직 관측되지 않았습니다."} />
                  ) : (
                    /* 노드: 대시보드와 같은 4단위 격자. 슬롯 10개마다 카드가 한 칸씩 확장된다. */
                    <div style={{ display: "grid", gap: 8 }}>
                      <div className="node-grid" style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gridAutoFlow: "row dense", gap: 12 }}>
                        {nodes.map((node, i) => {
                          const observedPods = observedPodsForNode(node, topologyNodes, pods);
                          const span = nodeCardColumnSpan(node);
                          return (
                            <motion.div
                              className="node-card-shell"
                              data-node-card-span={span}
                              key={node.key}
                              style={{ minWidth: 0, maxWidth: "100%", gridColumn: `span ${span}`, contain: "layout" }}
                              layout={reducedMotion ? false : "position"}
                              initial={reducedMotion ? false : { opacity: 0, y: 8 }}
                              animate={{ opacity: 1, y: 0 }}
                              transition={reducedMotion ? { duration: 0 } : { ...SOFT, delay: Math.min(i, 4) * 0.015 }}
                            >
                              <NodeCard node={node} pods={observedPods ?? []}
                                highlightedPodIdentities={highlightedPodIdentities}
                                podHighlightActive={podHighlightActive}
                                nodeAlias={nodeAliases.aliasesByNodeName.get(node.name) ?? null}
                                problemPodCount={observedPods === null ? null : observedPods.filter((pod) => isBadHealth(pod.health)).length}
                                onOpen={() => go({ level: "pods", cluster: view.cluster, node: node.name }, 1)}
                                onTip={onTip}
                                onSaveNodeAlias={nodeAliasEditingAvailable ? nodeAliases.saveAlias : undefined}
                                onDeleteNodeAlias={nodeAliasEditingAvailable ? nodeAliases.deleteAlias : undefined} />
                            </motion.div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </>)}

                {view.level === "pods" && (
                  <div style={{ background: UI.card, border: `1px solid ${UI.line}`, borderRadius: 16, padding: 20 }}>
                    {/* 노드 귀속 판정(physical topology server_id)은 유지하되,
                        설명 카드는 UI에서 제거 — 파드 목록만 바로 보여준다. */}
                    <div style={{ display: "flex", alignItems: "flex-start", gap: 8, marginBottom: 14, minWidth: 0 }}>
                      <Server size={14} style={{ color: UI.ink3 }} />
                      <span style={{ minWidth: 0, flex: 1 }}>
                        <NodeAliasTitle
                          alias={nodeAliases.aliasesByNodeName.get(view.node) ?? null}
                          nodeName={view.node}
                          onDelete={nodeAliasEditingAvailable ? nodeAliases.deleteAlias : undefined}
                          onSave={nodeAliasEditingAvailable ? nodeAliases.saveAlias : undefined}
                        />
                      </span>
                    </div>
                    {topology.status === "loading" ? (
                      <PodSkeleton />
                    ) : topology.status === "unavailable" ? (
                      <EmptyState icon={<Box size={18} strokeWidth={1.75} />} label="인벤토리 관측 안 됨"
                        hint="에이전트가 아직 이 클러스터의 파드 인벤토리를 보고하지 않았습니다." flush />
                    ) : nodePods.length === 0 ? (
                      <EmptyState icon={<Box size={18} strokeWidth={1.75} />} label="관측된 파드가 없습니다"
                        hint="이 노드에 귀속된 파드가 아직 관측되지 않았습니다." flush />
                    ) : (<div style={embedded ? undefined : { maxHeight: 480, overflowY: "auto", scrollbarGutter: "stable", overscrollBehavior: "contain" }}>
                      <div style={{ display: "grid", gridTemplateColumns: "12px minmax(160px,1.8fr) minmax(90px,1fr) 92px 96px", alignItems: "center", gap: 12, padding: "0 10px 7px", borderBottom: `1px solid ${UI.line}`, fontSize: TYPE.caption, fontWeight: 600, letterSpacing: "0.07em", color: UI.ink3 }}>
                        <span /><span>파드</span><span>네임스페이스</span><span>상태</span><span>헬스</span>
                      </div>
                      {nodePods.map((p) => (
                        <PodRow key={p.key} pod={p} onClick={() => selectPod(p)} onTip={onTip} />
                      ))}
                    </div>)}
                  </div>
                )}
              </motion.div>
            </AnimatePresence>
          </div>

          <SidePanel key={lensTab ?? "default"} forcedTab={lensTab ?? null} scaled={embedded}
            onAddRepo={onAddRepo} onOpenRepository={onOpenRepository} stickyTop={stickyTop}
            viewportTopInset={viewportTopInset}
            activeCluster={activeCluster} selectedNamespace={selectedNamespace}
            onHighlightTarget={setPodHighlightTarget}
            pendingRepos={pendingRepos} connectedRepos={connectedRepos}
            repositoryGroups={repositoryGroups} onRepositoryDisconnected={onRepositoryDisconnected} />
        </div>
      </div>

      {/* 커서 추적 툴팁 — 관측된 리소스의 이름·상태·헬스 */}
      <AnimatePresence>
        {tip && (
          <motion.div key="tip" initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.98 }} transition={{ duration: DUR.micro }}
            style={{
              position: "fixed", left: tipLeft, top: tipTop, zIndex: 60, pointerEvents: "none",
              // 카드 폭은 내용(max-content)에 맞춰 늘어난다 — 고정 368px + 고정 컬럼 트랙에서
              // "제한 1000m"/"사용량 관측 안 됨" 같은 값이 카드 밖으로 넘치던 문제의 교정.
              background: cardA(0.96), backdropFilter: "blur(10px)", border: `1px solid ${UI.line}`, borderRadius: 11, padding: "9px 12px",
              boxShadow: `0 10px 30px -12px ${inkA(0.22)}`, width: "max-content", minWidth: tip.metrics ? 420 : 260,
              maxWidth: "min(468px, calc(100vw - 16px))", boxSizing: "border-box",
            }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
              <span style={{ width: 7, height: 7, borderRadius: 999, background: sevColor(healthSev(tip.health)), flexShrink: 0 }} />
              <span style={{ flex: 1, minWidth: 0, fontSize: TYPE.label, fontWeight: 600, fontFamily: MONO, color: UI.ink, letterSpacing: "-0.01em", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{tip.label}</span>
            </div>
            <div style={{ fontSize: TYPE.caption, color: UI.ink3, marginTop: 3, marginLeft: 13 }}>
              <span style={{ color: sevColor(healthSev(tip.health)), fontWeight: 600 }}>{tip.health ? statusLabel(tip.health) : "헬스 관측 안 됨"}</span>
              <span> · {tip.status ? statusLabel(tip.status) : "상태 관측 안 됨"}</span>
            </div>
            {/* 모든 행이 같은 그리드 트랙(max-content)을 공유 — 열은 내용만큼 넓어지고
                행 간 정렬은 유지된다. 셀은 nowrap, 카드가 내용에 맞춰 커진다. */}
            {tip.metrics && (
              <div style={{ display: "grid", gridTemplateColumns: "52px max-content 8px max-content 8px max-content", columnGap: 4, rowGap: 3, alignItems: "baseline", fontSize: TYPE.caption, marginTop: 7, marginLeft: 13, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
                {tip.metrics.map((metric) => (
                  <div key={metric.label} style={{ display: "contents" }}>
                    <span style={{ color: UI.ink3 }}>{metric.label}</span>
                    <span style={{ color: metric.limitSeverity === "crit" ? HP.crit : metric.limitSeverity === "warn" ? TINT.warn.fg : UI.ink, fontWeight: 600 }}>
                      {metric.usage}
                    </span>
                    {metric.request ? (
                      <>
                        <span style={{ color: UI.ink3, textAlign: "center" }}>·</span>
                        <span style={{ color: UI.ink3 }}>{metric.request}</span>
                      </>
                    ) : <><span /><span /></>}
                    {metric.limit ? (
                      <>
                        <span style={{ color: UI.ink3, textAlign: "center" }}>·</span>
                        <span style={{ color: UI.ink2 }}>{metric.limit}</span>
                      </>
                    ) : <><span /><span /></>}
                  </div>
                ))}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      <style>{`
        html, body { background: ${UI.bg}; }
        .op { background: ${UI.bg}; font-family: var(--font-sans); font-weight: var(--font-weight-body); -webkit-font-smoothing: antialiased; }
        .op:not([data-embedded="true"]) { min-height: 100vh; }
        .op .opsia-content-layout { container: opsia-content / inline-size; }
        .op .opsia-main-pane { container: opsia-main / inline-size; }
        .op .node-slot-grid { justify-content: start; }
        .op [data-slot-state]:not([data-slot-state="empty"]):hover {
          z-index: 2;
          filter: brightness(1.04);
          box-shadow: 0 5px 12px ${inkA(0.2)};
        }
        .op [data-pod-highlight-source] {
          transition: background-color .14s ease, border-color .14s ease, box-shadow .14s ease;
          outline: none;
        }
        .op [data-pod-highlight-source]:hover,
        .op [data-pod-highlight-source]:focus {
          background: ${blueA(0.1)} !important;
          border-color: ${blueA(0.34)} !important;
          box-shadow: 0 0 0 2px ${blueA(0.08)};
        }
        .op [data-pod-highlight-source]:hover > svg,
        .op [data-pod-highlight-source]:focus > svg,
        .op [data-pod-highlight-source]:hover [data-pod-highlight-primary],
        .op [data-pod-highlight-source]:focus [data-pod-highlight-primary] {
          color: ${BLUE} !important;
        }
        .op .podrow { position: relative; transition: background .15s ease; }
        .op .podrow:hover { background: ${inkA(0.035)} !important; }
        @container opsia-content (max-width: 1000px) {
          .op .opsia-main-pane { flex-basis: 100% !important; }
          .op .opsia-side-panel { position: static !important; top: auto !important; width: 100% !important; height: auto !important; max-height: none !important; }
        }
        @container opsia-main (max-width: 720px) {
          .op .node-grid { grid-template-columns: minmax(0, 1fr) !important; }
          .op .node-card-shell { grid-column: auto !important; }
        }
        @media (max-width: 760px) {
          .op .cluster-grid,
          .op .node-grid { grid-template-columns: minmax(0, 1fr) !important; }
          .op .node-card-shell { grid-column: auto !important; }
          .home-cluster-grid { grid-template-columns: minmax(0, 1fr) !important; }
          .home-cluster-grid > * { grid-column: auto !important; }
        }
        .op .kindlink:hover { color: ${BLUE} !important; } .op .kindlink:hover b { color: ${BLUE}; }
        .pulsedot { animation: pd 1.5s ease-in-out infinite; }
        @keyframes pd { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
        .op .op-skel { display: block; background: linear-gradient(90deg, ${inkA(0.05)} 25%, ${inkA(0.09)} 37%, ${inkA(0.05)} 63%); background-size: 400% 100%; animation: skel 1.4s ease-in-out infinite; }
        @keyframes skel { 0% { background-position: 100% 0; } 100% { background-position: 0 0; } }
        .op ::-webkit-scrollbar { width: 8px; } .op ::-webkit-scrollbar-thumb { background: ${inkA(0.12)}; border-radius: 99px; }
        @media (prefers-reduced-motion: reduce) { .pulsedot, .op .op-skel { animation: none !important; } }
      `}</style>
    </div>
  );
}

// ── 빈 상태 — 아이콘 + 제목 + 부연으로 정돈(투박한 한 줄 텍스트 대신). 정직한 "관측 안 됨" 문구 유지.
function EmptyState({ icon, label, hint, flush = false }: { icon?: React.ReactNode; label: string; hint?: string; flush?: boolean }) {
  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "center", gap: 8,
      background: flush ? "transparent" : UI.card, border: flush ? "none" : `1px solid ${UI.line}`, borderRadius: 14,
      padding: "44px 20px", textAlign: "center",
    }}>
      {icon && <span style={{ width: 40, height: 40, borderRadius: 12, background: inkA(0.04), display: "grid", placeItems: "center", color: UI.ink3, flexShrink: 0 }}>{icon}</span>}
      <span style={{ fontSize: TYPE.body, fontWeight: 600, color: UI.ink2 }}>{label}</span>
      {hint && <span style={{ fontSize: TYPE.caption, color: UI.ink3, maxWidth: 320, lineHeight: 1.5 }}>{hint}</span>}
    </div>
  );
}

// ── 로딩 스켈레톤 — cold start에서도 최종 4단위 노드 격자와 동일한 자리를
// 먼저 점유한다. 요약 응답이 도착하면 summary-backed NodeCard가 이 셸을 대체하고,
// 무거운 토폴로지는 같은 카드 DOM의 세부 상태만 보강한다. shimmer는 .op-skel.
export function NodeSkeleton() {
  return (
    <div
      className="node-grid"
      data-testid="node-skeleton-grid"
      data-grid-columns="4"
      style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gridAutoFlow: "row dense", gap: 12 }}
    >
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          aria-hidden="true"
          className="node-card-shell"
          data-node-skeleton-card="unit"
          data-node-card-span="1"
          key={i}
          style={{ display: "flex", flexDirection: "column", gap: 10, minWidth: 0, gridColumn: "span 1", aspectRatio: "1 / 1", contain: "layout paint", background: UI.card, border: `1px solid ${UI.line}`, borderRadius: 16, padding: 16, boxSizing: "border-box", overflow: "hidden" }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="op-skel" style={{ width: 13, height: 13, borderRadius: 4 }} />
            <span className="op-skel" style={{ flex: 1, height: 11, borderRadius: 5 }} />
          </div>
          <span className="op-skel" style={{ width: "52%", height: 12, borderRadius: 5 }} />
          <div style={{ display: "grid", gap: 6, marginTop: "auto" }}>
            {[0, 1].map((metric) => (
              <span key={metric} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span className="op-skel" style={{ width: 34, height: 8, borderRadius: 4, flexShrink: 0 }} />
                <span className="op-skel" style={{ flex: 1, height: 5, borderRadius: 999 }} />
                <span className="op-skel" style={{ width: 66, height: 9, borderRadius: 4, flexShrink: 0 }} />
              </span>
            ))}
          </div>
          <span
            data-node-skeleton-slots="10"
            style={{ display: "grid", gridTemplateColumns: `repeat(${NODE_SLOT_COLUMNS_PER_UNIT}, minmax(0, 1fr))`, gap: 4 }}
          >
            {Array.from({ length: NODE_SLOT_COUNT_PER_COLUMN }).map((_, slot) => (
              <span className="op-skel" key={slot} style={{ aspectRatio: "1", minWidth: 0, borderRadius: 3 }} />
            ))}
          </span>
        </div>
      ))}
    </div>
  );
}

function PodSkeleton() {
  const cols = "12px minmax(160px,1.8fr) minmax(90px,1fr) 92px 96px";
  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: cols, alignItems: "center", gap: 12, padding: "0 10px 7px", borderBottom: `1px solid ${UI.line}`, fontSize: TYPE.caption, fontWeight: 600, letterSpacing: "0.07em", color: UI.ink3 }}>
        <span /><span>파드</span><span>네임스페이스</span><span>상태</span><span>헬스</span>
      </div>
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} style={{ display: "grid", gridTemplateColumns: cols, alignItems: "center", gap: 12, padding: "9px 10px" }}>
          <span className="op-skel" style={{ width: 10, height: 10, borderRadius: 3 }} />
          <span className="op-skel" style={{ width: `${68 - i * 4}%`, height: 10, borderRadius: 5 }} />
          <span className="op-skel" style={{ width: "62%", height: 9, borderRadius: 5 }} />
          <span className="op-skel" style={{ width: 46, height: 9, borderRadius: 5 }} />
          <span className="op-skel" style={{ width: 42, height: 17, borderRadius: 6 }} />
        </div>
      ))}
    </div>
  );
}

// ── 우측 패널 ─────────────────────────────
// 서비스/구성 탭은 현재 드릴된 클러스터/네임스페이스 범위의 read-only projection을 보여준다.
function SidePanel({ forcedTab, scaled, onAddRepo, onOpenRepository, stickyTop, viewportTopInset = 0, activeCluster, selectedNamespace, onHighlightTarget, pendingRepos, connectedRepos, repositoryGroups, onRepositoryDisconnected }: {
  forcedTab?: "svc" | "cfg" | "git" | null;
  scaled?: boolean;
  onAddRepo?: () => void;
  onOpenRepository?: (repositoryRef: string) => void;
  stickyTop?: number;
  viewportTopInset?: number;
  activeCluster?: string | null;
  selectedNamespace?: string | null;
  onHighlightTarget?: (target: PodHighlightTarget | null) => void;
  pendingRepos?: string[];
  connectedRepos?: string[];
  repositoryGroups?: RepositoryGroup[];
  onRepositoryDisconnected?: (repositoryRef: string) => void;
}) {
  const [tab, setTab] = useState<"svc" | "cfg" | "git">(forcedTab ?? "svc");
  const panelHeight = scaled
    ? resourceAuxiliaryViewportHeight(viewportTopInset, stickyTop ?? 24)
    : "calc(100vh - 60px)";
  return (
    <ResourceAuxiliaryPanel
      className="opsia-side-panel"
      aria-label="인프라 보조 정보"
      style={{ position: "sticky", top: stickyTop ?? 24, height: panelHeight, maxHeight: panelHeight }}
      header={(
      <div role="tablist" aria-label="인프라 보조 정보" style={{ width: "100%", height: 36, display: "flex", gap: 3, flexShrink: 0, background: UI.bg2, borderRadius: 10, padding: 3 }}>
        {([["svc", "서비스", Plug], ["cfg", "구성", FileCog], ["git", "저장소", GithubIcon]] as const).map(([id, label, I]) => {
          const on = tab === id;
          return (
            <button type="button" role="tab" aria-selected={on} key={id} onClick={() => { onHighlightTarget?.(null); setTab(id); }} style={{ position: "relative", flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 5, padding: "6px 0", borderRadius: 8, border: "none", background: "transparent", cursor: "pointer", fontSize: TYPE.label, fontWeight: 600, color: on ? UI.ink : UI.ink3 }}>
              {on && <motion.span layoutId="ptab" transition={SOFT} style={{ position: "absolute", inset: 0, borderRadius: 8, background: UI.card, boxShadow: `0 1px 3px ${inkA(0.12)}` }} />}
              <span style={{ position: "relative", display: "flex", alignItems: "center", gap: 5 }}><I size={12} />{label}</span>
            </button>
          );
        })}
      </div>
      )}
    >
        {tab === "svc" && (
          <OpsiaServicePanel
            activeCluster={activeCluster ?? null}
            selectedNamespace={selectedNamespace ?? null}
            onHighlightTarget={onHighlightTarget}
          />
        )}

        {tab === "cfg" && (
          <OpsiaConfigPanel
            activeCluster={activeCluster ?? null}
            selectedNamespace={selectedNamespace ?? null}
            onHighlightTarget={onHighlightTarget}
          />
        )}

        {tab === "git" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {repositoryGroups ? <RepositoryConnections
            groups={repositoryGroups}
            onOpenRepository={onOpenRepository}
            onDisconnected={onRepositoryDisconnected}
            onHoverRepository={(group) => onHighlightTarget?.(
              group && activeCluster
                ? {
                    type: "applications",
                    clusterId: activeCluster,
                    applicationIds: group.applications.map((application) => application.id),
                  }
                : null,
            )}
          /> : (connectedRepos ?? []).map((r) => (
            <ResourceAuxiliaryRow
              key={`connected-${r}`}
              icon={<GithubIcon size={15} />}
              title={r}
              tooltip={r}
              titleFontFamily={MONO}
              meta={<span style={{ color: TINT.ok.fg }}>연결됨 · GitOps 관리</span>}
              trailing={<Check size={13} style={{ color: TINT.ok.fg }} />}
            />
          ))}
          {(pendingRepos ?? []).map((r) => (
            <ResourceAuxiliaryRow
              key={r}
              icon={<GithubIcon size={15} />}
              title={r}
              tooltip={r}
              titleFontFamily={MONO}
              meta={<span style={{ color: TINT.blue.fg }}>연결 중 · 초기 동기화 대기</span>}
              trailing={<span className="pulsedot" style={{ width: 6, height: 6, borderRadius: 999, background: BLUE }} />}
              style={{ background: blueA(0.05) }}
            />
          ))}
          {(pendingRepos ?? []).length === 0
            && (repositoryGroups ? repositoryGroups.length === 0 : (connectedRepos ?? []).length === 0) && (
            <div style={{ fontSize: TYPE.caption, color: UI.ink3, padding: "16px 10px 6px" }}>연결된 저장소는 배포 관점에서 관리됩니다.</div>
          )}
          {onAddRepo && (
            <button onClick={onAddRepo}
              style={{ ...resourceAuxiliaryFooterButtonStyle, border: `1.5px dashed ${LINE3}`, color: BLUE }}>
              + 저장소 연결
            </button>
          )}
          </div>
        )}
    </ResourceAuxiliaryPanel>
  );
}
