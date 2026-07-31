// ⚠ 데모 · 서비스 토폴로지 v5 — 라이브 배선(UI-PHASE2-001 TOP-01·03·04·07).
// 구조(노드·엣지·관계)= GET /api/topology?view=relations (관계 근거 전용, 텔레메트리 아님).
// 텔레메트리(연결수·verdict)= GET /api/traffic/flows 관측만. RPS/p99/5xx는 계약에 없어 항상 "관측 안 됨"(조작 금지).
// 드래그 재배치 = 로컬 표현 상태. 엣지 클릭 = 엣지 상세 고정 · 노드 클릭 = 리소스 상세.
import { useMemo, useRef, useState } from "react";
import { motion } from "motion/react";
import { readDevpreviewTopologyFocus } from "./features/filters/devpreviewDeepLinks";
import { UI, BLUE, ST, TINT, MONO, TYPE, PRESENT_SCALE, DUR, inkA, blueA, INK4, cardA } from "./devpreview/theme";
import {
  useRelationTopology,
  type RelationEdgeView,
  type RelationNodeHealth,
  type RelationNodeView,
} from "./devpreview/relationTopologyFeed";
import {
  pairKey,
  useTrafficTelemetry,
  type EdgeTelemetry,
} from "./devpreview/trafficTelemetryFeed";
import type { TrafficVerdict } from "./devpreview/trafficTelemetryFeed";
import { reasonLabel, statusLabel } from "./devpreview/statusLabel";
import "./styles/tokens.css";
import "./styles/foundation.css";


// ── 노드 카드 치수 · 레이아웃 격자 ─────────────────────────────
const NW = 128, NH = 54, CX = 184, RY = 66, PAD = 26;

const HEALTH_COLOR: Record<RelationNodeHealth, string> = {
  ok: ST.ok,
  warn: ST.warn,
  unknown: INK4,
};

const VERDICT_LABEL: Record<TrafficVerdict, string> = {
  forwarded: "정상",
  dropped: "드롭",
  error: "오류",
  unknown: "불명",
};

const KIND_LABEL: Record<RelationEdgeView["kind"], string> = {
  owns: "소유",
  runs_on: "배치",
  selects: "선택",
  routes_to: "라우팅",
};

// 관계 그래프 → 방향 레이어드 배치. root에서 BFS 깊이=열, 열 내 순번=행.
// 좌표는 초기 배치일 뿐이며 드래그(override)로 자유 이동한다.
function layoutGraph(
  nodes: readonly RelationNodeView[],
  edges: readonly RelationEdgeView[],
  rootIds: readonly string[],
): { pos: Record<string, { x: number; y: number }>; vw: number; vh: number } {
  const out = new Map<string, string[]>();
  for (const e of edges) {
    const list = out.get(e.from);
    if (list) list.push(e.to);
    else out.set(e.from, [e.to]);
  }
  const depth = new Map<string, number>();
  const queue: string[] = [];
  const seeds = rootIds.length > 0 ? rootIds : nodes.slice(0, 1).map((n) => n.id);
  for (const id of seeds) {
    if (!depth.has(id)) { depth.set(id, 0); queue.push(id); }
  }
  while (queue.length > 0) {
    const id = queue.shift() as string;
    const d = depth.get(id) as number;
    for (const nb of out.get(id) ?? []) {
      if (!depth.has(nb)) { depth.set(nb, d + 1); queue.push(nb); }
    }
  }
  let disconnectedDepth = 0;
  depth.forEach((d) => { if (d > disconnectedDepth) disconnectedDepth = d; });
  disconnectedDepth += 1;
  for (const n of nodes) {
    if (!depth.has(n.id)) depth.set(n.id, disconnectedDepth);
  }
  const buckets = new Map<number, string[]>();
  for (const n of nodes) {
    const d = depth.get(n.id) as number;
    const list = buckets.get(d);
    if (list) list.push(n.id);
    else buckets.set(d, [n.id]);
  }
  const pos: Record<string, { x: number; y: number }> = {};
  let maxDepth = 0;
  let maxRows = 1;
  buckets.forEach((ids, d) => {
    if (d > maxDepth) maxDepth = d;
    if (ids.length > maxRows) maxRows = ids.length;
    ids.forEach((id, i) => { pos[id] = { x: PAD + d * CX, y: PAD + i * RY }; });
  });
  return {
    pos,
    vw: PAD * 2 + maxDepth * CX + NW,
    vh: PAD * 2 + (maxRows - 1) * RY + NH,
  };
}

const curve = (x1: number, y1: number, x2: number, y2: number, horiz = true) => {
  if (horiz) { const mx = (x1 + x2) / 2; return `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`; }
  const my = (y1 + y2) / 2; return `M ${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`;
};

// 상대 위치에 따라 좌/우/상/하 접점 자동 선택
function anchors(a: { x: number; y: number }, b: { x: number; y: number }) {
  if (b.x >= a.x + NW + 10) return { x1: a.x + NW, y1: a.y + NH / 2, x2: b.x, y2: b.y + NH / 2, horiz: true };
  if (b.x + NW <= a.x - 10) return { x1: a.x, y1: a.y + NH / 2, x2: b.x + NW, y2: b.y + NH / 2, horiz: true };
  if (b.y >= a.y + NH) return { x1: a.x + NW / 2, y1: a.y + NH, x2: b.x + NW / 2, y2: b.y, horiz: false };
  return { x1: a.x + NW / 2, y1: a.y, x2: b.x + NW / 2, y2: b.y + NH, horiz: false };
}

function edgeVerdictColor(tel: EdgeTelemetry | null, toHealth: RelationNodeHealth): string {
  if (tel) {
    if (tel.verdict === "error" || tel.verdict === "dropped") return ST.crit;
    if (tel.verdict === "forwarded") return BLUE;
    return INK4;
  }
  // 텔레메트리가 없을 때 대상 리소스의 health 색을 호출 상태처럼 사용하지 않는다.
  // 구조 관계는 중립 회색, 실제 traffic verdict가 있을 때만 색과 흐름을 부여한다.
  void toHealth;
  return INK4;
}

export function TopologyView({ embedded = false, onOpenService, focusId, onFocusService, clusterIds }: {
  embedded?: boolean;
  onOpenService?: (node: RelationNodeView) => void;
  focusId?: string | null;
  onFocusService?: (id: string | null) => void;
  /** 관측 대상 클러스터 목록. 빈/미지정이면 전체 클러스터 관계를 조회한다(demo-server 축소 없음). */
  clusterIds?: readonly string[];
} = {}) {
  const key = (clusterIds ?? []).join(",");
  const scope = useMemo(() => (key ? key.split(",") : []), [key]);
  const topo = useRelationTopology(scope);
  const traffic = useTrafficTelemetry(scope);

  const nodeMap = useMemo(
    () => new Map(topo.nodes.map((n) => [n.id, n])),
    [topo.nodes],
  );
  const layout = useMemo(
    () => layoutGraph(topo.nodes, topo.edges, topo.rootIds),
    [topo.nodes, topo.edges, topo.rootIds],
  );

  // 드래그 override는 계산된 초기 배치 위에 얹히는 순수 표현 상태
  const [override, setOverride] = useState<Record<string, { x: number; y: number }>>({});
  // userSel === undefined → 사용자가 아직 손대지 않음(?focus 딥링크 우선)
  const [userSel, setUserSel] = useState<string | null | undefined>(undefined);
  const deepFocus = useMemo(
    () => readDevpreviewTopologyFocus(topo.nodes.map((n) => n.id)),
    [topo.nodes],
  );
  const localSel = userSel === undefined ? deepFocus : userSel;
  const sel = focusId !== undefined ? focusId : localSel;
  const setGraphSel = (id: string | null) => {
    if (focusId !== undefined) onFocusService?.(id);
    else setUserSel(id);
  };

  const [etip, setEtip] = useState<{ x: number; y: number; e: RelationEdgeView } | null>(null);
  const [pinEdge, setPinEdge] = useState<RelationEdgeView | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragRef = useRef<{ id: string; dx: number; dy: number } | null>(null);
  const movedRef = useRef(false); // 드래그 이동 후 곧바로 오는 click 무시(오클릭 상세 방지)

  const P = (id: string) => override[id] ?? layout.pos[id] ?? { x: PAD, y: PAD };
  const edgeTel = (e: RelationEdgeView): EdgeTelemetry | null => {
    if (traffic.status !== "ready") return null;
    const from = nodeMap.get(e.from);
    const to = nodeMap.get(e.to);
    if (!from || !to) return null;
    return traffic.byPair[pairKey(from.serviceKey, to.serviceKey)] ?? null;
  };

  // 선택 문맥: 인접(이웃) 노드 강조
  const ctx = useMemo(() => {
    if (!sel) return null;
    const svcs = new Set<string>([sel]);
    for (const e of topo.edges) {
      if (e.from === sel) svcs.add(e.to);
      if (e.to === sel) svcs.add(e.from);
    }
    return { svcs, center: sel };
  }, [sel, topo.edges]);

  const svcLit = (id: string) => (pinEdge ? pinEdge.from === id || pinEdge.to === id : !ctx || ctx.svcs.has(id));

  const toVB = (cx: number, cy: number) => {
    const r = svgRef.current!.getBoundingClientRect();
    return { x: ((cx - r.left) * layout.vw) / r.width, y: ((cy - r.top) * layout.vh) / r.height };
  };
  const startDrag = (id: string) => (e: React.PointerEvent) => {
    const v = toVB(e.clientX, e.clientY);
    const p = P(id);
    dragRef.current = { id, dx: v.x - p.x, dy: v.y - p.y };
    movedRef.current = false;
    setDragId(id); setEtip(null);
    (e.target as Element).setPointerCapture?.(e.pointerId);
  };
  const onMove = (e: React.PointerEvent) => {
    const d = dragRef.current; if (!d) return;
    movedRef.current = true;
    const v = toVB(e.clientX, e.clientY);
    const x = Math.max(4, Math.min(layout.vw - NW - 4, v.x - d.dx));
    const y = Math.max(4, Math.min(layout.vh - NH - 4, v.y - d.dy));
    setOverride((o) => ({ ...o, [d.id]: { x, y } }));
  };
  const endDrag = () => { dragRef.current = null; setDragId(null); };

  const openNode = (id: string) => {
    const node = nodeMap.get(id);
    // cluster/namespace/name을 한 덩어리로 넘겨 동일명 서비스의 상세 identity를 보존한다.
    if (onOpenService && node) onOpenService(node);
    else setGraphSel(id);
  };
  // TOP-04: 엣지 상호작용은 언제나 고정 상세 패널을 연다(onOpenService가 있어도 노드로 새지 않는다)
  const pinEdgeToggle = (e: RelationEdgeView) => {
    setPinEdge((prev) => (prev && prev.id === e.id ? null : e));
    setEtip(null);
  };

  const scopeLabel = scope.length === 0 ? "전체 클러스터" : scope.length === 1 ? scope[0]! : `${scope.length}개 클러스터`;

  const banner = (() => {
    if (topo.status === "loading") return { tone: "gray" as const, text: "토폴로지 관측 중…" };
    if (topo.status === "error") return { tone: "crit" as const, text: "토폴로지를 불러오지 못했습니다" };
    if (topo.status === "unavailable") {
      const why = topo.partialReasonCodes.length > 0
        ? ` · ${reasonLabel(topo.partialReasonCodes[0])}${topo.partialReasonCodes.length > 1 ? ` 외 ${topo.partialReasonCodes.length - 1}건` : ""}`
        : "";
      return { tone: "warn" as const, text: `구조 관측 안 됨${why}` };
    }
    if (topo.nodes.length === 0) return { tone: "gray" as const, text: "관측된 서비스가 없습니다" };
    // 노드는 관측됐으나 서비스 호출(edge)이 아직 관측되지 않은 경우, 관계 없는 노드를
    // 나열하지 않고 compact honest 상태로 닫는다(트래픽 텔레메트리 갱신 대기 등).
    if (topo.edges.length === 0) {
      const why = topo.partialReasonCodes.length > 0
        ? ` · ${reasonLabel(topo.partialReasonCodes[0])}${topo.partialReasonCodes.length > 1 ? ` 외 ${topo.partialReasonCodes.length - 1}건` : ""}`
        : "";
      return { tone: "warn" as const, text: `트래픽 관측 안 됨${why}` };
    }
    return null;
  })();

  return (
    <div className="tp" style={{ minHeight: embedded ? undefined : "100vh", padding: embedded ? 0 : "44px 24px", display: "flex", justifyContent: "center" }}>
      <div style={{ width: embedded ? "100%" : 992, maxWidth: "100%" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 18 }}>
          {!embedded && <div style={{ fontSize: TYPE.page, fontWeight: 700, letterSpacing: "-0.03em", color: UI.ink }}>서비스 토폴로지</div>}
          {!embedded && <div style={{ fontSize: TYPE.body, color: UI.ink3 }}>관계 그래프 — {scopeLabel}</div>}
        </div>

        <div style={{ background: UI.card, border: `1px solid ${UI.line}`, borderRadius: 16, padding: 18, position: "relative" }}>
          {/* TOP-04: 고정된 엣지 상세 — 관계 근거 + 관측 텔레메트리(없으면 관측 안 됨) */}
          {pinEdge && (() => {
            const from = nodeMap.get(pinEdge.from);
            const to = nodeMap.get(pinEdge.to);
            const tel = edgeTel(pinEdge);
            const bad = tel !== null && (tel.verdict === "error" || tel.verdict === "dropped");
            return (
              <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ type: "spring", bounce: 0.12, visualDuration: 0.3 }}
                style={{ position: "absolute", top: 14, right: 14, width: 276, background: cardA(0.97), backdropFilter: "blur(10px)", border: `1px solid ${bad ? TINT.crit.bd : UI.line}`, borderRadius: 13, padding: 14, boxShadow: `0 16px 40px -18px ${inkA(0.25)}`, zIndex: 5 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: TYPE.body, fontWeight: 600, fontFamily: MONO, color: UI.ink, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {from?.name ?? pinEdge.from} <span style={{ color: UI.ink3, fontWeight: 600 }}>→</span> {to?.name ?? pinEdge.to}
                  </span>
                  <button onClick={() => setPinEdge(null)} aria-label="엣지 상세 닫기" style={{ width: 20, height: 20, borderRadius: 999, border: "none", background: inkA(0.06), color: UI.ink3, cursor: "pointer", fontSize: TYPE.caption, lineHeight: 1 }}>✕</button>
                </div>
                <div style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <span style={{ fontSize: TYPE.caption, fontWeight: 600, color: TINT.blue.fg, background: TINT.blue.bg, border: `1px solid ${TINT.blue.bd}`, borderRadius: 6, padding: "1px 7px" }}>관계 · {KIND_LABEL[pinEdge.kind]}</span>
                  {to && <span style={{ fontSize: TYPE.caption, color: UI.ink2, background: inkA(0.04), borderRadius: 6, padding: "1px 7px" }}>{to.kind}{to.namespace ? ` · ${to.namespace}` : ""}</span>}
                </div>
                {tel ? (
                  <div style={{ marginTop: 11, borderTop: `1px solid ${UI.line}`, paddingTop: 10 }}>
                    <div style={{ display: "flex", gap: 12, fontSize: TYPE.caption, fontVariantNumeric: "tabular-nums" }}>
                      <span style={{ color: UI.ink2 }}>연결 <b style={{ color: UI.ink }}>{tel.connections.toLocaleString()}</b></span>
                      <span style={{ color: UI.ink2 }}>판정 <b style={{ color: bad ? ST.crit : UI.ink }}>{VERDICT_LABEL[tel.verdict]}</b></span>
                      <span style={{ color: UI.ink2 }}>{tel.protocol}</span>
                    </div>
                    <div style={{ marginTop: 8, fontSize: TYPE.caption, color: UI.ink3 }}>rps · p99 · 5xx 관측 안 됨 (traffic/flows 계약 미제공)</div>
                  </div>
                ) : (
                  <div style={{ marginTop: 11, borderTop: `1px solid ${UI.line}`, paddingTop: 10, fontSize: TYPE.caption, color: UI.ink3 }}>
                    트래픽 관측 안 됨{traffic.status === "unavailable" && traffic.reasonCodes.length > 0 ? ` · ${reasonLabel(traffic.reasonCodes[0])}` : ""}
                  </div>
                )}
              </motion.div>
            );
          })()}

          {banner && (
            <div style={{ position: "absolute", top: 14, left: 18, display: "flex", alignItems: "center", gap: 8, fontSize: TYPE.label, fontWeight: 600, color: TINT[banner.tone].fg, background: TINT[banner.tone].bg, border: `1px solid ${TINT[banner.tone].bd}`, borderRadius: 9, padding: "5px 10px", zIndex: 4 }}>
              <span style={{ width: 7, height: 7, borderRadius: 999, background: TINT[banner.tone].fg }} />{banner.text}
            </div>
          )}

          <div style={{ height: "clamp(420px, calc(100vh - 260px), 720px)", minHeight: 420, overflow: "auto", scrollbarGutter: "stable", paddingTop: banner ? 34 : 0, boxSizing: "border-box" }}>
          {/* P0: 서비스 호출 그래프는 실제 관측된 edge가 있을 때만 렌더한다. edge가 0이면
              관계 없는 노드 구름을 그리지 않고 위 banner의 honest 상태만 보인다. */}
          {topo.status === "ready" && topo.edges.length > 0 && (
          <svg ref={svgRef} viewBox={`0 0 ${layout.vw} ${layout.vh}`} width="100%" style={{ display: "block", touchAction: "none" }}
            onPointerMove={onMove} onPointerUp={endDrag} onPointerLeave={() => { if (!dragRef.current) { setGraphSel(null); setEtip(null); } }}>
            {/* 방향 엣지 — 관계 근거. 관측된 트래픽이 있을 때만 흐름 애니메이션 */}
            {topo.edges.map((e) => {
              const a = P(e.from), b = P(e.to);
              const an = anchors(a, b);
              const pinned = pinEdge !== null && pinEdge.id === e.id;
              const hovered = (etip !== null && etip.e.id === e.id) || pinned;
              const on = pinEdge ? pinned : hovered || (!etip && (!ctx || ctx.center === e.from || ctx.center === e.to));
              const to = nodeMap.get(e.to);
              const tel = edgeTel(e);
              const col = edgeVerdictColor(tel, to?.health ?? "unknown");
              const d = curve(an.x1, an.y1, an.x2, an.y2, an.horiz);
              const flowing = tel !== null && tel.verdict === "forwarded";
              return (
                <g key={e.id} style={{ opacity: on ? 1 : 0.08, transition: "opacity .18s" }}>
                  <path d={d} fill="none" stroke={col} strokeWidth={tel ? 3 : 2} strokeOpacity={hovered ? 0.55 : tel ? 0.32 : 0.42} strokeLinecap="round" strokeDasharray={tel ? undefined : "5 7"} />
                  {flowing && <path d={d} fill="none" stroke={col} strokeWidth={3} strokeLinecap="round" strokeDasharray="3 11" className="flow" />}
                  <path d={d} fill="none" stroke="transparent" strokeWidth={16} strokeLinecap="round"
                    tabIndex={0} role="button" aria-label={`엣지 ${nodeMap.get(e.from)?.name ?? e.from} → ${to?.name ?? e.to} 상세`}
                    style={{ cursor: "pointer", outline: "none" }}
                    onKeyDown={(ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pinEdgeToggle(e); } }}
                    onClick={() => pinEdgeToggle(e)}
                    onMouseEnter={(ev) => { if (dragRef.current) return; const s = embedded ? PRESENT_SCALE : 1; setEtip({ x: ev.clientX / s, y: ev.clientY / s, e }); setGraphSel(null); }}
                    onMouseMove={(ev) => { if (dragRef.current) return; const s = embedded ? PRESENT_SCALE : 1; setEtip({ x: ev.clientX / s, y: ev.clientY / s, e }); }}
                    onMouseLeave={() => setEtip(null)} />
                </g>
              );
            })}

            {/* 노드 — 드래그 가능 카드. 클릭 = 리소스 상세 · 이중클릭/호버 = 포커스 */}
            {topo.nodes.map((s) => {
              const p = P(s.id); const lit = svcLit(s.id); const on = ctx?.center === s.id;
              return (
                <g key={s.id}
                  tabIndex={0} role="button" aria-label={`${s.name} (${s.kind}) 상세 열기`}
                  onMouseEnter={() => { if (!dragRef.current) setGraphSel(s.id); }}
                  onPointerDown={startDrag(s.id)}
                  onKeyDown={(ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openNode(s.id); } }}
                  onClick={() => { if (movedRef.current) { movedRef.current = false; return; } if (!dragRef.current) openNode(s.id); }}
                  onDoubleClick={() => setGraphSel(s.id)}
                  style={{ cursor: dragId === s.id ? "grabbing" : "grab", opacity: lit ? 1 : 0.22, transition: "opacity .18s", outline: "none" }}>
                  <rect x={p.x} y={p.y} width={NW} height={NH} rx={12} fill={UI.card} stroke={dragId === s.id || on ? BLUE : UI.line} strokeWidth={on || dragId === s.id ? 1.5 : 1}
                    style={{ filter: dragId === s.id ? `drop-shadow(0 16px 30px ${blueA(0.22)})` : on ? `drop-shadow(0 8px 18px ${blueA(0.16)})` : `drop-shadow(0 1px 2px ${inkA(0.05)})` }} />
                  <circle cx={p.x + 14} cy={p.y + 16} r={4} fill={HEALTH_COLOR[s.health]} />
                  <text x={p.x + 26} y={p.y + 20} fontSize="11" fontWeight="600" fill={UI.ink} fontFamily={MONO} letterSpacing="-0.01em" style={{ pointerEvents: "none" }}>{s.name.length > 15 ? `${s.name.slice(0, 14)}…` : s.name}</text>
                  <text x={p.x + 13} y={p.y + 36} fontSize="8.5" fill={UI.ink3} style={{ pointerEvents: "none" }}>{s.kind}{s.namespace ? ` · ${s.namespace}` : ""}</text>
                  <text x={p.x + 13} y={p.y + 47} fontSize="8" fill={UI.ink3} style={{ pointerEvents: "none" }}>{s.category} · {statusLabel(s.status)}</text>
                </g>
              );
            })}
          </svg>
          )}
          </div>

          {/* 레전드 */}
          <div style={{ marginTop: 6, paddingTop: 12, borderTop: `1px solid ${UI.line}`, display: "flex", gap: 14, flexWrap: "wrap", fontSize: TYPE.label, color: UI.ink2, alignItems: "center" }}>
            <span style={{ display: "flex", alignItems: "center", gap: 6 }}><svg width="30" height="8"><line x1="0" y1="4" x2="29" y2="4" stroke={INK4} strokeWidth="2" strokeDasharray="5 5" strokeLinecap="round" /></svg>구조 관계 {topo.edges.length}개</span>
            {traffic.status === "ready" && <span style={{ display: "flex", alignItems: "center", gap: 6 }}><span style={{ width: 8, height: 8, borderRadius: 999, background: BLUE }} />정상<span style={{ width: 8, height: 8, borderRadius: 999, background: ST.crit, marginLeft: 6 }} />오류·드롭<span style={{ width: 8, height: 8, borderRadius: 999, background: INK4, marginLeft: 6 }} />불명</span>}
            <span style={{ display: "flex", alignItems: "center", gap: 6, color: UI.ink3 }}>
              호출 텔레메트리 {traffic.status === "ready" ? "관측됨" : traffic.status === "loading" ? "관측 중…" : "미관측"}
            </span>
            {topo.truncated && <span style={{ color: TINT.warn.fg }}>생략: 노드 {topo.omittedNodeCount} · 관계 {topo.omittedEdgeCount}</span>}
          </div>
        </div>
      </div>

      {/* 엣지 툴팁 */}
      {etip && (() => {
        const from = nodeMap.get(etip.e.from);
        const to = nodeMap.get(etip.e.to);
        const tel = edgeTel(etip.e);
        const bad = tel !== null && (tel.verdict === "error" || tel.verdict === "dropped");
        return (
          <motion.div initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }} transition={{ duration: DUR.micro }}
            style={{
              position: "fixed", left: Math.min(etip.x + 14, window.innerWidth - 220), top: Math.min(etip.y + 16, window.innerHeight - 110), zIndex: 60, pointerEvents: "none",
              background: cardA(0.96), backdropFilter: "blur(10px)", border: `1px solid ${bad ? TINT.crit.bd : UI.line}`, borderRadius: 11, padding: "10px 12px",
              boxShadow: `0 10px 30px -12px ${inkA(0.22)}`, minWidth: 184,
            }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: TYPE.label, fontWeight: 600, fontFamily: MONO, color: UI.ink }}>
              {from?.name ?? etip.e.from}<span style={{ color: UI.ink3, fontWeight: 600 }}>→</span>{to?.name ?? etip.e.to}
            </div>
            <div style={{ marginTop: 6, fontSize: TYPE.caption, color: UI.ink3 }}>관계 · {KIND_LABEL[etip.e.kind]}</div>
            {tel ? (
              <div style={{ display: "flex", gap: 12, marginTop: 7, fontSize: TYPE.caption, fontVariantNumeric: "tabular-nums" }}>
                <span style={{ color: UI.ink2 }}>연결 <b style={{ color: UI.ink }}>{tel.connections.toLocaleString()}</b></span>
                <span style={{ color: UI.ink2 }}>판정 <b style={{ color: bad ? ST.crit : UI.ink }}>{VERDICT_LABEL[tel.verdict]}</b></span>
              </div>
            ) : (
              <div style={{ marginTop: 7, fontSize: TYPE.caption, color: UI.ink3 }}>트래픽 관측 안 됨</div>
            )}
          </motion.div>
        );
      })()}

      <style>{`
        .tp { font-family: var(--font-sans); font-weight: var(--font-weight-body); -webkit-font-smoothing: antialiased; }
        .tp .flow { animation: flowmove 1.4s linear infinite; }
        @keyframes flowmove { to { stroke-dashoffset: -28; } }
        .tp svg text { user-select: none; }
        .tp svg [role="button"]:focus-visible { outline: 2px solid ${BLUE}; outline-offset: 2px; }
        @media (prefers-reduced-motion: reduce) { .tp .flow { animation: none !important; } }
      `}</style>
    </div>
  );
}
