import { AlertTriangle, Check, Clock3, ExternalLink, LoaderCircle } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";

import {
  BLUE,
  DUR,
  HP,
  MONO,
  RADIUS,
  SOFT,
  SPACE,
  TINT,
  TYPE,
  UI,
  blueA,
  inkA,
} from "./theme";
import { useNarrowViewport } from "./useNarrowViewport";

export type ProgressNodeState = "complete" | "active" | "failed" | "pending";

export interface ProgressNode {
  id: string;
  label: string;
  state: ProgressNodeState;
  statusLabel: string;
  description?: string | null;
  observedAt?: string | null;
  activity?: "running" | "waiting";
  tone?: "info" | "warning";
  href?: string | null;
  actionLabel?: string | null;
  onAction?: (() => void) | null;
}

interface ProgressNodeRailProps {
  steps: ProgressNode[];
  ariaLabel: string;
}

export interface ProgressRailMetrics {
  completed: number;
  failed: number;
  total: number;
}

/**
 * Progress is derived only from terminal backend steps. An active animation is
 * intentionally not converted into a made-up percentage.
 */
export function progressRailMetrics(steps: readonly ProgressNode[]): ProgressRailMetrics {
  return {
    completed: steps.filter((step) => step.state === "complete").length,
    failed: steps.filter((step) => step.state === "failed").length,
    total: steps.length,
  };
}

function nodePalette(step: ProgressNode) {
  if (step.state === "complete") return { fg: TINT.ok.fg, bg: TINT.ok.bg, border: TINT.ok.bd };
  if (step.state === "failed") return { fg: TINT.crit.fg, bg: TINT.crit.bg, border: TINT.crit.bd };
  if (step.state === "active" && step.tone === "warning") {
    return { fg: TINT.warn.fg, bg: TINT.warn.bg, border: TINT.warn.bd };
  }
  if (step.state === "active") return { fg: BLUE, bg: TINT.blue.bg, border: TINT.blue.bd };
  return { fg: UI.ink3, bg: UI.card, border: UI.line };
}

function ActivityGauge({ step }: { step: ProgressNode }) {
  const reduceMotion = useReducedMotion();
  const palette = nodePalette(step);
  const active = step.state === "active";
  const label = step.activity === "waiting" ? "외부 승인 대기 중" : "현재 단계 실행 중";

  if (!active) {
    return (
      <span
        aria-hidden="true"
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(14, minmax(0, 1fr))",
          gap: 2,
          width: "100%",
          height: 4,
        }}
      >
        {Array.from({ length: 14 }, (_, index) => (
          <span
            key={index}
            style={{
              borderRadius: 999,
              background: step.state === "complete"
                ? TINT.ok.bd
                : step.state === "failed"
                  ? TINT.crit.bd
                  : UI.line2,
            }}
          />
        ))}
      </span>
    );
  }

  return (
    <span
      aria-label={label}
      role="status"
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(14, minmax(0, 1fr))",
        gap: 2,
        width: "100%",
        height: 4,
      }}
    >
      {Array.from({ length: 14 }, (_, index) => (
        <motion.span
          aria-hidden="true"
          key={index}
          animate={reduceMotion ? { opacity: index < 5 ? 0.9 : 0.2 } : { opacity: [0.16, 1, 0.16] }}
          transition={reduceMotion ? { duration: 0 } : {
            duration: DUR.meter,
            ease: "easeInOut",
            repeat: Infinity,
            delay: index * 0.055,
          }}
          style={{ borderRadius: 999, background: palette.fg }}
        />
      ))}
    </span>
  );
}

function OverallProgressGauge({ steps, ariaLabel }: { steps: ProgressNode[]; ariaLabel: string }) {
  const reduceMotion = useReducedMotion();
  const metrics = progressRailMetrics(steps);
  return (
    <div
      aria-label={`${ariaLabel} · ${metrics.completed}/${metrics.total} 완료`}
      aria-valuemax={Math.max(metrics.total, 1)}
      aria-valuemin={0}
      aria-valuenow={metrics.completed}
      aria-valuetext={`${metrics.total}개 관측 단계 중 ${metrics.completed}개 완료${metrics.failed > 0 ? `, ${metrics.failed}개 실패` : ""}`}
      role="progressbar"
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${Math.max(metrics.total, 1)}, minmax(0, 1fr))`,
        gap: 4,
        height: 5,
      }}
    >
      {(steps.length > 0 ? steps : [{ id: "empty", label: "", statusLabel: "", state: "pending" as const }]).map((step) => {
        const palette = nodePalette(step);
        return (
          <span
            aria-hidden="true"
            key={step.id}
            style={{
              position: "relative",
              overflow: "hidden",
              borderRadius: 999,
              background: step.state === "complete" ? HP.ok
                : step.state === "failed" ? HP.crit
                  : step.state === "active" ? palette.bg
                    : UI.line2,
            }}
          >
            {step.state === "active" ? (
              <motion.span
                initial={false}
                animate={reduceMotion ? { x: "0%" } : { x: ["-100%", "280%"] }}
                transition={reduceMotion ? { duration: 0 } : {
                  duration: 1.55,
                  ease: "easeInOut",
                  repeat: Infinity,
                }}
                style={{
                  position: "absolute",
                  inset: 0,
                  width: "38%",
                  borderRadius: 999,
                  background: palette.fg,
                  opacity: reduceMotion ? 0.72 : 1,
                }}
              />
            ) : null}
          </span>
        );
      })}
    </div>
  );
}

function ProgressMarker({ step, index }: { step: ProgressNode; index: number }) {
  const reduceMotion = useReducedMotion();
  const palette = nodePalette(step);
  const active = step.state === "active";
  return (
    <span style={{ width: 32, height: 32, position: "relative", zIndex: 2, display: "grid", placeItems: "center" }}>
      {active && !reduceMotion ? (
        <motion.span
          aria-hidden="true"
          animate={{ opacity: [0.42, 0], scale: [0.9, 1.38] }}
          transition={{ duration: 1.8, ease: "easeOut", repeat: Infinity }}
          style={{ position: "absolute", inset: 1, border: `1px solid ${palette.fg}`, borderRadius: 999 }}
        />
      ) : null}
      <motion.span
        aria-hidden="true"
        initial={false}
        animate={{ scale: active ? 1.04 : 1 }}
        transition={reduceMotion ? { duration: 0 } : SOFT}
        style={{
          width: 28,
          height: 28,
          display: "grid",
          placeItems: "center",
          borderRadius: 999,
          border: `1px solid ${palette.border}`,
          background: palette.bg,
          color: palette.fg,
          boxShadow: active ? `0 0 0 4px ${step.tone === "warning" ? TINT.warn.bg : blueA(0.08)}` : "none",
          fontFamily: MONO,
          fontSize: TYPE.caption,
          fontWeight: 700,
        }}
      >
        {step.state === "complete" ? <Check size={14} strokeWidth={3} />
          : step.state === "failed" ? <AlertTriangle size={13} />
            : active && step.activity === "running" ? (
              <motion.span
                animate={reduceMotion ? undefined : { rotate: 360 }}
                transition={reduceMotion ? undefined : { duration: 1.2, ease: "linear", repeat: Infinity }}
                style={{ display: "grid", placeItems: "center" }}
              >
                <LoaderCircle size={14} />
              </motion.span>
            )
              : active ? <Clock3 size={13} />
                : index + 1}
      </motion.span>
    </span>
  );
}

function NodeCopy({ step, centered }: { step: ProgressNode; centered: boolean }) {
  const palette = nodePalette(step);
  return (
    <div style={{ minWidth: 0, display: "grid", gap: 2, textAlign: centered ? "center" : "left" }}>
      <span
        title={step.label}
        style={{
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          color: step.state === "active" || step.state === "failed" ? UI.ink : UI.ink2,
          fontSize: TYPE.label,
          fontWeight: step.state === "active" || step.state === "failed" ? 700 : 600,
        }}
      >
        {step.label}
      </span>
      <span
        title={step.statusLabel}
        style={{
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          color: step.state === "active" || step.state === "failed" ? palette.fg : UI.ink2,
          fontSize: TYPE.caption,
          fontWeight: step.state === "active" || step.state === "failed" ? 600 : 500,
        }}
      >
        {step.statusLabel}
      </span>
    </div>
  );
}

export function ProgressNodeRail({ steps, ariaLabel }: ProgressNodeRailProps) {
  const narrow = useNarrowViewport(860);
  const reduceMotion = useReducedMotion();
  const metrics = progressRailMetrics(steps);
  const current = steps.find((step) => step.state === "active" || step.state === "failed") ?? null;

  return (
    <section aria-label={ariaLabel} style={{ display: "grid", gap: SPACE.stack }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <span style={{ color: UI.ink2, fontSize: TYPE.label, fontWeight: 600 }}>단계 진행</span>
        <span style={{ color: UI.ink3, fontFamily: MONO, fontSize: TYPE.caption, fontVariantNumeric: "tabular-nums" }}>
          {metrics.completed}/{metrics.total} 완료
        </span>
      </div>

      <OverallProgressGauge ariaLabel={ariaLabel} steps={steps} />

      <div
        style={{
          border: `1px solid ${UI.line}`,
          borderRadius: RADIUS.control,
          background: UI.bg2,
          padding: narrow ? 10 : 12,
          overflow: "visible",
        }}
      >
        <ol
          aria-label={ariaLabel}
          style={{
            display: "grid",
            gridTemplateColumns: narrow ? "minmax(0, 1fr)" : `repeat(${Math.max(steps.length, 1)}, minmax(0, 1fr))`,
            gap: narrow ? 10 : 0,
            margin: 0,
            padding: 0,
            listStyle: "none",
          }}
        >
          {steps.map((step, index) => {
            const next = steps[index + 1] ?? null;
            const connectorReached = next !== null
              && (next.state === "complete" || next.state === "active" || next.state === "failed");
            const palette = nodePalette(step);
            const copy = <NodeCopy step={step} centered={!narrow} />;
            const body = (
              <>
                <ProgressMarker step={step} index={index} />
                {copy}
                <span style={{ gridColumn: narrow ? "2" : "1", width: "100%", alignSelf: "end" }}>
                  <ActivityGauge step={step} />
                </span>
              </>
            );
            return (
              <motion.li
                aria-current={step.state === "active" || step.state === "failed" ? "step" : undefined}
                key={step.id}
                initial={reduceMotion ? false : { opacity: 0, y: 5 }}
                animate={{ opacity: 1, y: 0 }}
                transition={reduceMotion ? { duration: 0 } : { ...SOFT, delay: Math.min(index, 8) * 0.045 }}
                style={{
                  minWidth: 0,
                  minHeight: narrow ? 82 : 132,
                  position: "relative",
                  display: "grid",
                  gridTemplateColumns: narrow ? "32px minmax(0, 1fr)" : "minmax(0, 1fr)",
                  justifyItems: narrow ? "stretch" : "center",
                  alignItems: "center",
                  alignContent: "center",
                  gap: narrow ? "8px 12px" : 8,
                  padding: narrow ? "12px" : "14px 10px 12px",
                  border: `1px solid ${palette.border}`,
                  borderRadius: RADIUS.control,
                  background: step.state === "active" || step.state === "failed" ? palette.bg : UI.card,
                  boxShadow: step.state === "active" ? `0 8px 24px -20px ${palette.fg}` : "none",
                }}
              >
                {next ? (
                  <span
                    aria-hidden="true"
                    style={narrow
                      ? { position: "absolute", zIndex: 0, left: 27, top: 43, bottom: -12, width: 2, overflow: "hidden", background: UI.line }
                      : { position: "absolute", zIndex: 0, left: "50%", top: 28, width: "100%", height: 2, overflow: "hidden", background: UI.line }}
                  >
                    <motion.span
                      initial={false}
                      animate={narrow
                        ? { scaleY: connectorReached ? 1 : 0 }
                        : { scaleX: connectorReached ? 1 : 0 }}
                      transition={reduceMotion ? { duration: 0 } : { duration: DUR.fade, ease: [0.22, 1, 0.36, 1] }}
                      style={{
                        position: "absolute",
                        inset: 0,
                        background: connectorReached
                          ? next.state === "failed" ? HP.crit
                            : next.state === "active" && next.tone === "warning" ? HP.warn
                              : HP.ok
                          : UI.line,
                        transformOrigin: narrow ? "top" : "left",
                      }}
                    />
                  </span>
                ) : null}
                {step.href ? (
                  <a
                    className="product-focusable"
                    href={step.href}
                    rel="noreferrer"
                    target="_blank"
                    style={{
                      zIndex: 1,
                      minWidth: 0,
                      display: "contents",
                      color: "inherit",
                      textDecoration: "none",
                    }}
                  >
                    {body}
                  </a>
                ) : body}
              </motion.li>
            );
          })}
        </ol>
      </div>

      {current ? (
        <motion.div
          aria-live="polite"
          initial={reduceMotion ? false : { opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={reduceMotion ? { duration: 0 } : SOFT}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            minWidth: 0,
            border: `1px solid ${nodePalette(current).border}`,
            borderRadius: RADIUS.control,
            background: nodePalette(current).bg,
            padding: "10px 12px",
          }}
        >
          <span style={{ flexShrink: 0, color: nodePalette(current).fg, fontSize: TYPE.caption, fontWeight: 700 }}>
            {current.state === "failed" ? "중단 단계" : "현재 단계"}
          </span>
          <span aria-hidden="true" style={{ width: 1, height: 18, background: inkA(0.08), flexShrink: 0 }} />
          <div style={{ minWidth: 0, flex: 1, display: "grid", gridTemplateColumns: "minmax(90px, auto) minmax(0, 1fr)", alignItems: "center", gap: 10 }}>
            <strong style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: UI.ink, fontSize: TYPE.label }}>
              {current.label}
            </strong>
            <span title={current.description ?? current.statusLabel} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: UI.ink2, fontSize: TYPE.caption }}>
              {current.description || current.statusLabel}
              {current.observedAt ? ` · ${current.observedAt}` : ""}
            </span>
          </div>
          {current.actionLabel && current.onAction ? (
            <button
              type="button"
              className="product-focusable product-control"
              onClick={current.onAction}
              style={{
                flexShrink: 0,
                border: `1px solid ${nodePalette(current).border}`,
                borderRadius: RADIUS.chip,
                background: UI.card,
                color: nodePalette(current).fg,
                padding: "5px 9px",
                fontSize: TYPE.caption,
                fontWeight: 700,
                cursor: "pointer",
              }}
            >
              {current.actionLabel}
            </button>
          ) : current.href ? (
            <a
              className="product-focusable"
              href={current.href}
              rel="noreferrer"
              target="_blank"
              style={{ flexShrink: 0, display: "inline-flex", alignItems: "center", gap: 4, color: nodePalette(current).fg, fontSize: TYPE.caption, fontWeight: 700, textDecoration: "none" }}
            >
              열기 <ExternalLink size={12} />
            </a>
          ) : null}
        </motion.div>
      ) : null}
    </section>
  );
}
