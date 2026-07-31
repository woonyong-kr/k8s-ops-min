import type {
  ResourceConditionEventItem,
  ResourceConditionItem,
  ResourceConditionTone,
} from "./resourceConditions";
import type { ResourceConditionsView } from "./resourceConditionsFeed";
import { KUBERNETES_KIND } from "./kubernetesKinds";
import { BLUE, MONO, TINT, TYPE, UI, blueA, inkA } from "./theme";

interface ResourceConditionsPanelProps {
  kind: string;
  view: ResourceConditionsView;
}

const COPY = {
  conditionUnavailable: "컨디션 정보 없음",
  eventFallbackTitle: "관련 이벤트",
  eventFallbackNote: "관련 Pod 컨디션이 관측되지 않아 최근 이벤트를 표시합니다.",
  loading: "컨디션을 불러오는 중...",
  noRelatedPods: "현재 관측되는 관련 Pod 없음",
  relatedPodFallbackNote: "ReplicaSet 자체 컨디션이 관측되지 않아 관련 Pod 컨디션을 표시합니다.",
  relatedPodTitle: "관련 Pod 컨디션",
  resourceGone: "리소스가 더 이상 관측되지 않음",
  resourceTitle: "리소스 컨디션",
  retry: "다시 시도",
  unavailable: "컨디션을 불러오지 못했습니다.",
} as const;

function toneStyle(tone: ResourceConditionTone): { color: string; background: string; border: string } {
  if (tone === "ok") return { color: TINT.ok.fg, background: TINT.ok.bg, border: TINT.ok.bd };
  if (tone === "crit") return { color: TINT.crit.fg, background: TINT.crit.bg, border: TINT.crit.bd };
  if (tone === "warn") return { color: TINT.warn.fg, background: TINT.warn.bg, border: TINT.warn.bd };
  return { color: BLUE, background: blueA(0.08), border: blueA(0.24) };
}

function EmptyLine({ children }: { children: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, border: `1px dashed ${UI.line}`, background: UI.bg2, borderRadius: 10, padding: "10px 12px", fontSize: TYPE.label, color: UI.ink3, lineHeight: 1.5 }}>
      <span>{children}</span>
    </div>
  );
}

function RetryLine({ onRetry }: { onRetry: () => void }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0", flexWrap: "wrap" }}>
      <span style={{ fontSize: TYPE.label, color: UI.ink3 }}>{COPY.unavailable}</span>
      <button
        type="button"
        className="product-focusable product-control"
        onClick={onRetry}
        style={{ border: `1px solid ${UI.line}`, background: UI.card, color: BLUE, borderRadius: 8, padding: "5px 11px", fontSize: TYPE.label, fontWeight: 600, cursor: "pointer" }}
      >
        {COPY.retry}
      </button>
    </div>
  );
}

function ConditionGroup({ title, items }: { title: string; items: ResourceConditionItem[] }) {
  if (items.length === 0) return null;
  return (
    <div style={{ display: "grid", gap: 7 }}>
      <div style={{ fontSize: TYPE.caption, color: UI.ink3, fontWeight: 700 }}>{title}</div>
      {items.map((item) => {
        const tone = toneStyle(item.tone);
        return (
          <div key={item.id} style={{ border: `1px solid ${UI.line2}`, background: UI.bg2, borderRadius: 10, padding: "9px 11px", minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
              <span style={{ minWidth: 0, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: TYPE.label, fontWeight: 700, color: UI.ink, fontFamily: MONO }}>
                {item.sourceLabel ? `${item.sourceLabel} · ${item.type}` : item.type}
              </span>
              {item.status && (
                <span style={{ flexShrink: 0, color: tone.color, background: tone.background, border: `1px solid ${tone.border}`, borderRadius: 6, padding: "1px 7px", fontSize: TYPE.caption, fontWeight: 700 }}>
                  {item.status}
                </span>
              )}
            </div>
            {(item.reason || item.message || item.lastTransitionAt) && (
              <div style={{ marginTop: 5, display: "grid", gap: 2, fontSize: TYPE.caption, color: UI.ink3, lineHeight: 1.45 }}>
                {item.reason && <span>{item.reason}</span>}
                {item.message && <span style={{ overflowWrap: "anywhere" }}>{item.message}</span>}
                {item.lastTransitionAt && <span style={{ color: inkA(0.45) }}>{item.lastTransitionAt}</span>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function EventGroup({ items }: { items: ResourceConditionEventItem[] }) {
  if (items.length === 0) return null;
  return (
    <div style={{ display: "grid", gap: 7 }}>
      <div style={{ fontSize: TYPE.caption, color: UI.ink3, fontWeight: 700 }}>{COPY.eventFallbackTitle}</div>
      {items.map((item) => {
        const tone = toneStyle(item.tone);
        return (
          <div key={item.id} style={{ border: `1px solid ${UI.line2}`, background: UI.bg2, borderRadius: 10, padding: "9px 11px", minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
              <span style={{ minWidth: 0, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: TYPE.label, fontWeight: 700, color: UI.ink }}>
                {item.reason ?? item.type ?? "Event"}
              </span>
              {item.count !== null && (
                <span style={{ flexShrink: 0, color: tone.color, background: tone.background, border: `1px solid ${tone.border}`, borderRadius: 6, padding: "1px 7px", fontSize: TYPE.caption, fontWeight: 700 }}>
                  {item.count}
                </span>
              )}
            </div>
            {(item.message || item.lastAt) && (
              <div style={{ marginTop: 5, display: "grid", gap: 2, fontSize: TYPE.caption, color: UI.ink3, lineHeight: 1.45 }}>
                {item.message && <span style={{ overflowWrap: "anywhere" }}>{item.message}</span>}
                {item.lastAt && <span style={{ color: inkA(0.45) }}>{item.lastAt}</span>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Note({ children }: { children: string }) {
  return (
    <div style={{ border: `1px solid ${blueA(0.2)}`, background: blueA(0.06), borderRadius: 9, padding: "8px 10px", color: UI.ink2, fontSize: TYPE.caption, lineHeight: 1.45 }}>
      {children}
    </div>
  );
}

export function ResourceConditionsPanel({ kind, view }: ResourceConditionsPanelProps) {
  if (view.status === "idle") return <EmptyLine>{COPY.conditionUnavailable}</EmptyLine>;
  if (view.status === "loading") return <EmptyLine>{COPY.loading}</EmptyLine>;
  if (view.status === "unavailable") return <EmptyLine>{COPY.resourceGone}</EmptyLine>;
  if (view.status === "error") return <RetryLine onRetry={view.retry} />;

  const isReplicaSet = kind === KUBERNETES_KIND.replicaSet;
  const hasPrimary = view.primary.length > 0;
  const hasRelated = view.relatedPods.length > 0;
  const useReplicaSetFallback = isReplicaSet && !hasPrimary;
  const showRelatedPods = useReplicaSetFallback && hasRelated;
  const showEvents = useReplicaSetFallback && !hasRelated && view.events.length > 0;
  if (!hasPrimary && !showRelatedPods && !showEvents) {
    return <EmptyLine>{isReplicaSet && view.relatedPodCount === 0 ? COPY.noRelatedPods : COPY.conditionUnavailable}</EmptyLine>;
  }

  return (
    <div style={{ display: "grid", gap: 10 }}>
      {hasPrimary && <ConditionGroup title={COPY.resourceTitle} items={view.primary} />}
      {showRelatedPods && <Note>{COPY.relatedPodFallbackNote}</Note>}
      {showRelatedPods && <ConditionGroup title={COPY.relatedPodTitle} items={view.relatedPods} />}
      {showEvents && <Note>{COPY.eventFallbackNote}</Note>}
      {showEvents && <EventGroup items={view.events} />}
    </div>
  );
}
