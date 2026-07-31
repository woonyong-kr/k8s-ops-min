// ── 홈 위젯 보드 부품 (D21 · Surface Spec §2) — 데모 구현.
// WidgetFrame 하나 + 시각 부품(KpiCard/RatioBar/MiniBars/Donut/RankList)만 존재한다.
// 위젯별 자체 시각 신설 금지 — 제품 이식 시 shared/ui/charts/로 재구현되는 사양 원본.
import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { ChevronRight, EllipsisVertical, Info, LayoutGrid, Pencil, Trash2 } from "lucide-react";
import { UI, BLUE, HP, TINT, MONO, TYPE, SOFT, DUR, inkA, blueA, critA, okA, warnA, IDENT } from "./theme";

export const HOME_CARD_GRID_CLASS = "home-card-grid";
export const HOME_CARD_GRID_ITEM_CLASS = "home-card-grid-item";
export const DASHBOARD_WIDGET_GRID_CLASS = "dashboard-widget-grid";
export const DASHBOARD_WIDGET_GRID_ITEM_CLASS = "dashboard-widget-grid-item";
export type DashboardWidgetSpan = 1 | 2 | 3 | 4;

export function homeCardGridStyle(narrow: boolean): React.CSSProperties {
  return {
    display: "grid",
    gridTemplateColumns: narrow ? "minmax(0, 1fr)" : "repeat(3, minmax(0, 1fr))",
    gridAutoFlow: "row",
    alignItems: "stretch",
    gap: 14,
  };
}

export function homeCardGridItemStyle(narrow: boolean): React.CSSProperties {
  return {
    gridColumn: "span 1",
    minWidth: 0,
    minHeight: 220,
    aspectRatio: narrow ? "auto" : "3 / 2",
    height: "100%",
  };
}

export function dashboardWidgetGridStyle(): React.CSSProperties {
  return {
    display: "grid",
    gridAutoRows: 220,
    gridAutoFlow: "row dense",
    alignItems: "stretch",
    gap: 12,
  };
}

export function dashboardWidgetItemStyle(span: DashboardWidgetSpan): React.CSSProperties {
  return {
    "--dashboard-widget-span": span,
    "--dashboard-widget-span-medium": Math.min(span, 2),
    minWidth: 0,
    minHeight: 220,
    height: 220,
  } as React.CSSProperties;
}

// ── WidgetFrame — 제목/카드 직접 이동 + ⓘ 툴팁. 중복 CTA·접기 컨트롤 없음. ──
export function WidgetFrame({ title, info, onDeepLink, editing, span, widgetType, widgetTypes, onSpanChange, onTypeChange, onEdit, onRemove, children }: {
  title: string; info?: string; onDeepLink?: () => void;
  editing?: boolean;
  span?: DashboardWidgetSpan;
  widgetType?: string;
  widgetTypes?: { id: string; title: string }[];
  onSpanChange?: (span: DashboardWidgetSpan) => void;
  onTypeChange?: (type: string) => void;
  onEdit?: () => void;
  onRemove?: () => void;
  children: React.ReactNode;
}) {
  const [tip, setTip] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLSpanElement>(null);
  const hasMenu = !!(onSpanChange || onTypeChange || onEdit || onRemove);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setMenuOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [menuOpen]);
  const navigable = onDeepLink !== undefined && !editing;
  const onCardClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!navigable) return;
    const target = event.target;
    if (target instanceof Element && target.closest("button, a, input, select, textarea, [role='button'], [role='link']")) return;
    onDeepLink();
  };
  return (
    // 편집 = 카드 전체가 드래그 핸들(버튼식 이동 없음) — 파란 점선 보더가 편집 상태 신호
    <div onClick={onCardClick} style={{ background: UI.card, border: editing ? `1.5px dashed ${blueA(0.5)}` : `1px solid ${UI.line}`, borderRadius: 14, padding: "13px 15px", display: "flex", flexDirection: "column", gap: 11, minWidth: 0, position: "relative", transition: "border-color .2s", height: "100%", boxSizing: "border-box", cursor: navigable ? "pointer" : editing ? "grab" : undefined }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        {/* 제목은 좁은 폭에서 한글이 글자 단위(세로줄)로 붕괴하지 않도록 nowrap+말줄임으로 잘라낸다.
            (CSS word-break: normal은 CJK를 임의 글자에서 끊으므로 nowrap이 필요하다.) */}
        {navigable ? (
          <button type="button" onClick={onDeepLink} aria-label={`${title} 화면으로 이동`}
            style={{ border: "none", background: "transparent", padding: 0, textAlign: "left", cursor: "pointer", fontSize: TYPE.body, fontWeight: 600, letterSpacing: "-0.01em", color: UI.heading, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {title}
          </button>
        ) : (
          <span style={{ fontSize: TYPE.body, fontWeight: 600, letterSpacing: "-0.01em", color: UI.heading, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</span>
        )}
        {info && (
          <span style={{ position: "relative", display: "grid", flexShrink: 0 }} onClick={(event) => event.stopPropagation()} onMouseEnter={() => setTip(true)} onMouseLeave={() => setTip(false)}>
            <Info size={12.5} style={{ color: UI.ink3, cursor: "help" }} />
            {tip && (
              <span style={{ position: "absolute", top: 20, left: -8, zIndex: 30, width: 210, background: inkA(0.92), color: UI.card, fontSize: TYPE.caption, lineHeight: 1.5, borderRadius: 8, padding: "7px 10px", backdropFilter: "blur(8px)" }}>{info}</span>
            )}
          </span>
        )}
        <span ref={menuRef} style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 4, flexShrink: 0, position: "relative" }} onClick={(event) => event.stopPropagation()}>
          {hasMenu && (
            <button type="button" aria-label={`${title} 위젯 메뉴`} aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}
              style={{ width: 26, height: 26, display: "grid", placeItems: "center", borderRadius: 7, border: "none", background: menuOpen ? inkA(0.06) : "transparent", color: UI.ink3, cursor: "pointer" }}>
              <EllipsisVertical size={15} />
            </button>
          )}
          {menuOpen && (
            <span role="menu" aria-label={`${title} 위젯 설정`} style={{ position: "absolute", top: 30, right: 0, zIndex: 45, width: 246, display: "flex", flexDirection: "column", gap: 9, padding: 10, borderRadius: 12, border: `1px solid ${UI.line}`, background: UI.card, boxShadow: `0 16px 40px -18px ${inkA(0.35)}` }}>
              {onSpanChange && (
                <span style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                  <span style={{ fontSize: TYPE.caption, fontWeight: 600, color: UI.ink3 }}>너비</span>
                  <span style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 4 }}>
                    {([1, 2, 3, 4] as const).map((nextSpan) => (
                      <button key={nextSpan} type="button" aria-label={`${title} 너비 ${nextSpan}/4`} aria-pressed={span === nextSpan}
                        onClick={() => { onSpanChange(nextSpan); setMenuOpen(false); }}
                        style={{ position: "relative", display: "flex", flexDirection: "column", alignItems: "center", gap: 4, border: `1px solid ${span === nextSpan ? blueA(0.55) : UI.line}`, borderRadius: 8, background: span === nextSpan ? blueA(0.09) : UI.card, color: span === nextSpan ? BLUE : UI.ink2, padding: "6px 3px 5px", fontSize: TYPE.caption, fontWeight: 600, cursor: "pointer", boxShadow: span === nextSpan ? `inset 0 0 0 1px ${blueA(0.12)}` : "none" }}>
                        <span aria-hidden="true" style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 2, width: 28, height: 10 }}>
                          {[1, 2, 3, 4].map((unit) => <span key={unit} style={{ borderRadius: 2, background: unit <= nextSpan ? (span === nextSpan ? BLUE : UI.ink3) : inkA(0.08) }} />)}
                        </span>
                        <span>{nextSpan}/4</span>
                      </button>
                    ))}
                  </span>
                </span>
              )}
              {onTypeChange && widgetTypes && widgetTypes.length > 0 && (
                <label style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: TYPE.caption, fontWeight: 600, color: UI.ink3 }}>
                  <span style={{ display: "flex", alignItems: "center", gap: 5 }}><LayoutGrid size={12} />위젯 유형</span>
                  <span style={{ position: "relative", display: "flex", alignItems: "center" }}>
                    <LayoutGrid size={13} aria-hidden="true" style={{ position: "absolute", left: 8, color: UI.ink3, pointerEvents: "none" }} />
                    <select aria-label={`${title} 위젯 유형`} value={widgetType} onChange={(event) => { onTypeChange(event.target.value); setMenuOpen(false); }}
                      style={{ minWidth: 0, width: "100%", border: `1px solid ${UI.line}`, borderRadius: 8, background: UI.card, color: UI.ink, padding: "7px 28px", fontSize: TYPE.caption, fontWeight: 600, cursor: "pointer" }}>
                      {widgetTypes.map((type) => <option key={type.id} value={type.id}>{type.title}</option>)}
                    </select>
                  </span>
                </label>
              )}
              {(onEdit || onRemove) && <span style={{ height: 1, background: UI.line2 }} />}
              <span style={{ display: "grid", gridTemplateColumns: onEdit && onRemove ? "1fr 1fr" : "1fr", gap: 5 }}>
                {onEdit && (
                  <button type="button" role="menuitem" onClick={() => { onEdit(); setMenuOpen(false); }}
                    style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 6, border: `1px solid ${UI.line}`, borderRadius: 8, background: UI.bg2, color: UI.ink2, padding: "7px 6px", fontSize: TYPE.caption, fontWeight: 600, cursor: "pointer" }}><Pencil size={13} />레이아웃 편집</button>
                )}
                {onRemove && (
                  <button type="button" role="menuitem" onClick={() => { onRemove(); setMenuOpen(false); }}
                    style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 6, border: `1px solid ${critA(0.2)}`, borderRadius: 8, background: critA(0.05), color: HP.crit, padding: "7px 6px", fontSize: TYPE.caption, fontWeight: 600, cursor: "pointer" }}><Trash2 size={13} />위젯 삭제</button>
                )}
              </span>
            </span>
          )}
        </span>
      </div>
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", minHeight: 0 }}>{children}</div>
    </div>
  );
}

// ── KpiCard — 큰 값 + 증감 틴트 + 요약 1줄 (P-12·P-17) ──
export function KpiValue({ value, unit, delta, deltaTone, summary }: {
  value: string; unit?: string; delta?: string; deltaTone?: "ok" | "warn"; summary?: React.ReactNode;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span style={{ display: "flex", alignItems: "baseline", gap: 7 }}>
        <span style={{ fontSize: TYPE.kpi, fontWeight: 700, letterSpacing: "-0.02em", color: UI.ink, fontVariantNumeric: "tabular-nums" }}>{value}</span>
        {unit && <span style={{ fontSize: TYPE.label, fontWeight: 600, color: UI.ink3 }}>{unit}</span>}
        {delta && (
          <span style={{ fontSize: TYPE.caption, fontWeight: 600, fontVariantNumeric: "tabular-nums", color: deltaTone === "warn" ? TINT.warn.fg : TINT.ok.fg, background: deltaTone === "warn" ? warnA(0.14) : okA(0.12), borderRadius: 999, padding: "2px 8px" }}>{delta}</span>
        )}
      </span>
      {summary && <span style={{ fontSize: TYPE.label, color: UI.ink2, lineHeight: 1.5 }}>{summary}</span>}
    </div>
  );
}

// ── RatioBar — 이중 비율 바 + 범례 (P-19) ──
export function RatioBar({ a, b, aLabel, bLabel, aColor = HP.ok, bColor = HP.warn }: {
  a: number; b: number; aLabel: string; bLabel: string; aColor?: string; bColor?: string;
}) {
  const total = a + b || 1;
  const ap = Math.round((a / total) * 100);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
      <div style={{ display: "flex", gap: 3, height: 8, borderRadius: 999, overflow: "hidden" }}>
        <motion.span initial={false} animate={{ width: `${ap}%` }} transition={{ duration: DUR.meter, ease: "easeInOut" }} style={{ background: aColor, borderRadius: 999 }} />
        <span style={{ flex: 1, background: bColor, borderRadius: 999, opacity: b === 0 ? 0.18 : 1 }} />
      </div>
      {([[aColor, aLabel, a, ap], [bColor, bLabel, b, 100 - ap]] as const).map(([c, l, v, p]) => (
        <span key={l} style={{ display: "flex", alignItems: "center", gap: 7, fontSize: TYPE.label, color: UI.ink2 }}>
          <span style={{ width: 4, height: 13, borderRadius: 2, background: c }} />
          <b style={{ color: UI.ink, fontVariantNumeric: "tabular-nums" }}>{v}</b>{l}
          <span style={{ marginLeft: "auto", fontVariantNumeric: "tabular-nums", fontSize: TYPE.caption, color: UI.ink3 }}>{p}%</span>
        </span>
      ))}
    </div>
  );
}

// ── MiniBars — 미니 막대 + 현재 구간 강조 (P-20) ──
export function MiniBars({ values, labels, currentIndex, tone = BLUE }: {
  values: number[]; labels?: string[]; currentIndex?: number; tone?: string;
}) {
  const max = Math.max(...values, 1);
  const barArea = labels ? 44 : 60; // 라벨 행을 제외한 막대 영역(px) — %는 자동 높이 컬럼에서 붕괴한다
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 5, height: 64 }}>
      {values.map((v, i) => (
        <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "flex-end", gap: 4, minWidth: 0 }}>
          <motion.span initial={{ height: 0 }} animate={{ height: Math.max((v / max) * barArea, 3) }} transition={{ ...SOFT, delay: i * 0.04 }}
            style={{ width: "100%", maxWidth: 22, borderRadius: 5, background: i === currentIndex ? tone : inkA(0.09), minHeight: 3 }} />
          {labels && <span style={{ fontSize: TYPE.caption, fontVariantNumeric: "tabular-nums", color: i === currentIndex ? UI.ink : UI.ink3, fontWeight: i === currentIndex ? 600 : 500 }}>{labels[i]}</span>}
        </div>
      ))}
    </div>
  );
}

// ── Donut — 도넛 + 값 범례 (P-14) ──
const DONUT_COLORS = [BLUE, HP.ok, HP.warn, TINT.purple.fg, IDENT.teal, UI.ink3];
export function Donut({ items, onPick }: { items: { label: string; value: number; pick?: boolean; color?: string }[]; onPick?: (label: string) => void }) {
  const total = items.reduce((s, x) => s + x.value, 0) || 1;
  const R = 34, C = 2 * Math.PI * R;
  const segments = items.map((it, i) => {
    const frac = it.value / total;
    const start = items.slice(0, i).reduce((s, x) => s + x.value / total, 0);
    return {
      it,
      i,
      dash: `${Math.max(frac * C - 2.5, 0)} ${C}`,
      off: -start * C,
    };
  });
  return (
    <div data-donut-layout="responsive" style={{ display: "grid", gridTemplateColumns: "var(--donut-layout-columns, minmax(64px, 80px) minmax(0, 1fr))", alignItems: "center", gap: "clamp(8px, 3vw, 14px)", width: "100%", minWidth: 0 }}>
      <svg width={80} height={80} viewBox="0 0 88 88" style={{ display: "block", width: "100%", maxWidth: 80, height: "auto", minWidth: 0, transform: "rotate(-90deg)" }}>
        {segments.map(({ it, i, dash, off }) => {
          return <motion.circle key={it.label} cx={44} cy={44} r={R} fill="none" strokeWidth={11} strokeLinecap="round"
            stroke={it.color ?? DONUT_COLORS[i % DONUT_COLORS.length]} initial={{ strokeDasharray: `0 ${C}` }} animate={{ strokeDasharray: dash }} transition={{ duration: DUR.draw, ease: "easeInOut", delay: i * 0.08 }} strokeDashoffset={off} />;
        })}
      </svg>
      <div style={{ display: "flex", flexDirection: "column", gap: 5, minWidth: 0, width: "100%" }}>
        {items.map((it, i) => {
          const pickable = !!onPick && it.pick !== false;
          return (
          <button key={it.label} onClick={pickable ? () => onPick!(it.label) : undefined} disabled={!pickable} className={pickable ? "rrow" : undefined}
            style={{ display: "flex", alignItems: "center", gap: 7, width: "100%", maxWidth: "100%", fontSize: TYPE.label, color: UI.ink2, border: "none", background: "transparent", padding: "1px 4px", borderRadius: 6, cursor: pickable ? "pointer" : "default", textAlign: "left", minWidth: 0 }}>
            <span style={{ width: 8, height: 8, borderRadius: 999, background: it.color ?? DONUT_COLORS[i % DONUT_COLORS.length], flexShrink: 0 }} />
            <span title={it.label} style={{ flex: "1 1 auto", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{it.label}</span>
            <b style={{ width: 44, marginLeft: "auto", textAlign: "right", color: UI.ink, fontVariantNumeric: "tabular-nums", flexShrink: 0 }}>{it.value}</b>
            <span style={{ fontVariantNumeric: "tabular-nums", fontSize: TYPE.caption, color: UI.ink3, width: 38, textAlign: "right", flexShrink: 0 }}>{Math.round((it.value / total) * 100)}%</span>
          </button>
          );
        })}
      </div>
    </div>
  );
}

// ── RankList — 색점 순위 리스트 (P-30) · 임계 행 틴트(P-01) ──
export function RankList({ rows, onPick }: {
  rows: { id: string; tone: "ok" | "warn" | "crit"; title: string; sub?: string; right?: string }[];
  onPick?: (id: string) => void;
}) {
  return (
    <div data-rank-list-layout="contained" style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minHeight: 0, maxHeight: "100%", overflowY: "auto", overscrollBehavior: "contain", scrollbarGutter: "stable" }}>
      {rows.map((r, i) => (
        <motion.button key={r.id} initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SOFT, delay: i * 0.05 }}
          onClick={onPick ? () => onPick(r.id) : undefined} disabled={!onPick} className={onPick ? "rrow" : undefined}
          style={{ display: "flex", alignItems: "center", gap: 9, width: "100%", textAlign: "left", border: "none", borderRadius: 9, padding: "7px 10px", cursor: onPick ? "pointer" : "default",
            background: r.tone === "crit" ? critA(0.07) : "transparent" }}>
          <span style={{ width: 7, height: 7, borderRadius: 999, background: HP[r.tone], flexShrink: 0 }} className={r.tone === "crit" ? "pulsedot" : undefined} />
          <span style={{ minWidth: 0, flex: 1 }}>
            <span style={{ display: "block", fontSize: TYPE.label, fontWeight: 600, color: UI.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.title}</span>
            {r.sub && <span style={{ display: "block", fontSize: TYPE.caption, fontFamily: MONO, color: UI.ink3, marginTop: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.sub}</span>}
          </span>
          {r.right && <span style={{ fontSize: TYPE.caption, fontVariantNumeric: "tabular-nums", color: UI.ink3, flexShrink: 0 }}>{r.right}</span>}
        </motion.button>
      ))}
    </div>
  );
}

// ── MultiLine — 활동 추이 멀티라인 (P-36, EASE_DRAW 드로잉 + 범례) ──
export function MultiLine({ series, height = 92 }: {
  series: { label: string; color: string; values: number[] }[]; height?: number;
}) {
  const W = 100, H = 40;
  const max = Math.max(...series.flatMap((s) => s.values), 1);
  const path = (vs: number[]) => vs.map((v, i) => `${i === 0 ? "M" : "L"}${(i / (vs.length - 1)) * W},${H - (v / max) * (H - 4) - 2}`).join(" ");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, flex: 1, minHeight: 0 }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", flex: 1, minHeight: height, display: "block" }} preserveAspectRatio="none">
        {[0.25, 0.5, 0.75].map((f) => <line key={f} x1={0} x2={W} y1={H * f} y2={H * f} stroke={UI.line2} strokeWidth={0.4} strokeDasharray="1.5 2.5" />)}
        {/* preserveAspectRatio=none + non-scaling-stroke에서는 pathLength 대시가 왜곡된다 — 페이드 등장으로 대체 */}
        {series.map((s, i) => (
          <motion.path key={s.label} d={path(s.values)} fill="none" stroke={s.color} strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ ...SOFT, delay: i * 0.12 }} vectorEffect="non-scaling-stroke" />
        ))}
      </svg>
      <div style={{ display: "flex", gap: 13, flexWrap: "wrap" }}>
        {series.map((s) => (
          <span key={s.label} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: TYPE.caption, color: UI.ink2 }}>
            <span style={{ width: 8, height: 2.5, borderRadius: 2, background: s.color }} />{s.label}
            <b style={{ color: UI.ink, fontVariantNumeric: "tabular-nums" }}>{s.values[s.values.length - 1]}</b>
          </span>
        ))}
      </div>
    </div>
  );
}

// ── RingGauge — 순간 사용률 (P-31: 상세 시트 개요 한정. 카드·홈 금지 — D21) ──
export function RingGauge({ label, value }: { label: string; value: number }) {
  const R = 20, C = 2 * Math.PI * R;
  const tone = value >= 90 ? HP.crit : value >= 75 ? HP.warn : HP.ok;
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
      <div style={{ position: "relative", width: 52, height: 52 }}>
        <svg width={52} height={52} viewBox="0 0 52 52" style={{ transform: "rotate(-90deg)" }}>
          <circle cx={26} cy={26} r={R} fill="none" stroke={inkA(0.07)} strokeWidth={5} />
          <motion.circle cx={26} cy={26} r={R} fill="none" stroke={tone} strokeWidth={5} strokeLinecap="round"
            strokeDasharray={C} initial={{ strokeDashoffset: C }} animate={{ strokeDashoffset: C * (1 - value / 100) }} transition={{ duration: DUR.meter, ease: "easeInOut" }} />
        </svg>
        <span style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", fontSize: TYPE.caption, fontWeight: 600, color: UI.ink, fontVariantNumeric: "tabular-nums" }}>{value}%</span>
      </div>
      <span style={{ fontSize: TYPE.caption, fontWeight: 600, letterSpacing: "0.05em", color: UI.ink3 }}>{label}</span>
    </div>
  );
}

// ── MiniTimeline — 최근 변경 세로 리스트 (P-21 문법: 시간·노드·점선 연결) ──
export function MiniTimeline({ items, onPick, columns = 1 }: {
  items: { id: string; time: string; tone: "ok" | "warn" | "crit"; title: string; ref?: { kind: string; name: string } }[];
  onPick?: (ref: { kind: string; name: string }) => void;
  columns?: 1 | 2; // 넓은 스팬(4칸)에서는 2열로 채워 어색한 좌측 쏠림을 없앤다
}) {
  const stacks = columns === 2
    ? [items.slice(0, Math.ceil(items.length / 2)), items.slice(Math.ceil(items.length / 2))]
    : [items];
  return (
    <div style={{ display: "grid", gridTemplateColumns: `repeat(${stacks.length}, minmax(0, 1fr))`, columnGap: 28 }}>
      {stacks.map((stack, si) => (
      <div key={si} style={{ display: "flex", flexDirection: "column" }}>
      {stack.map((it, i) => (
        <motion.div key={it.id} initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SOFT, delay: Math.min(i, 8) * 0.04 }}
          style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
          <span style={{ width: 76, flexShrink: 0, whiteSpace: "nowrap", fontSize: TYPE.caption, color: UI.ink3, paddingTop: 2, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{it.time}</span>
          <span style={{ display: "flex", flexDirection: "column", alignItems: "center", alignSelf: "stretch", flexShrink: 0 }}>
            <span style={{ width: 9, height: 9, borderRadius: 999, border: `2px solid ${HP[it.tone]}`, background: it.tone === "crit" ? HP.crit : "transparent", marginTop: 3 }} />
            {i < stack.length - 1 && <span style={{ flex: 1, width: 1, borderLeft: `1.5px dashed ${UI.line}`, minHeight: 14 }} />}
          </span>
          <button onClick={it.ref && onPick ? () => onPick(it.ref!) : undefined} disabled={!it.ref || !onPick} className={it.ref && onPick ? "rrow" : undefined}
            style={{ border: "none", background: "transparent", textAlign: "left", fontSize: TYPE.label, color: UI.ink, fontWeight: 600, padding: "0 4px 12px", borderRadius: 6, cursor: it.ref && onPick ? "pointer" : "default", minWidth: 0 }}>
            {it.title}{it.ref && onPick && <ChevronRight size={11} style={{ color: UI.ink3, verticalAlign: -1, marginLeft: 2 }} />}
          </button>
        </motion.div>
      ))}
      </div>
      ))}
    </div>
  );
}
