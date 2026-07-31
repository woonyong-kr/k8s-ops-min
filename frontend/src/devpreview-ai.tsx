/* eslint-disable react-hooks/exhaustive-deps, react-hooks/set-state-in-effect */
// AI 대화와 복구 검토를 실제 대화·복구 계약에 연결한 제품 패널.
import {
  Activity, ArrowUpRight, BellPlus, Boxes, Check, ChevronDown, CircleAlert,
  CircleStop, FileText, GitBranch, Play, Send, Server, Sparkles, SquarePen, Trash2, X,
} from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import "./styles/tokens.css";
import "./styles/foundation.css";
import { Spinner } from "./shared/ui/primitives/spinner";
import {
  SidePanelIconButton,
  SidePanelWindowControls,
} from "./devpreview/SidePanelShell";
import { emitAction } from "./devpreview/bus";
import {
  appendRecoveryConversationMessage, buildAiContext, createAiAlertRule,
  createRecoveryConversation, isAiProviderFailureTurn, sendAiChatTurn,
  deleteAllStoredAiConversations, deleteStoredAiConversation,
  useAiConversations, useConversationDetail, useAiSuggestions,
} from "./devpreview/aiFeed";
import { getAuditTimeline } from "./api/audit-timeline";
import { isApiError } from "./api/client";
import { listRcaIssues } from "./api/rca-issues";
import type {
  AiMessagePart, AiPageLink, AiResultPart, AiStepsPart, AiTextPart, AiTone, AiTurn,
} from "./features/ai-assistant/aiConversationContract";
import type {
  AiRecoveryExecutionReceipt,
  AiRecoveryHandoff,
} from "./features/ai-assistant/aiRecoveryHandoff";
import { isSafePrRoute, recoveryRouteLabel } from "./devpreview/recoveryRoute";
import {
  recoveryOutcomeNotices,
  type RecoveryOutcomeNotice,
} from "./devpreview/recoveryOutcome";
import { pullRequestReference } from "./devpreview/pullRequestReference";

const SPRING = "cubic-bezier(0.22, 1, 0.36, 1)"; // 진입 등장 이징

// ── 타이밍 상수 (한 곳에서 관리) ─────────────────────────────
// collapse* 는 CSS 트랜지션과 공유(스타일 블록에 주입). 재생/타이핑 계열은 프리뷰 전용(배선 시 제거).
const TIMING = {
  collapseSlideMs: 300,    // 접힘/펼침 높이 트랜지션
  collapseFadeMs: 150,     // 내용↔요약 크로스페이드
  typewriterStepMs: 11,    // 타이핑 간격
  stepRevealMs: 560,       // 단계 노출 간격
  stepCollapseMs: 900,     // 단계 완료 후 한 줄 접힘
  partReadyMs: 440,        // 비텍스트 파트 준비 지연
  revealGapMs: 520,        // 유저 발화 후 다음 노출 간격
  thinkMs: 650,            // 답변 전 "생각 중"
  sendThinkMs: 800,        // 전송 후 응답까지
  actionCreateMs: 900,     // 알림 생성 처리
  actionCollapseMs: 1100,  // 생성 후 한 줄 접힘
  userShownMs: 480,        // 유저 말풍선 노출
  collapsedShownMs: 260,   // 접힌 요약 노출
  replayStep1Ms: 450, replayStep2Ms: 980, // ▶ 재생 시 초기 노출
} as const;

// ── 색상: 애플 팔레트는 CSS 변수(.opsia-ai)로 정의, 여기선 토큰만 참조 ──
const toneHex: Record<AiTone, string> = {
  healthy: "var(--ap-green)", warning: "var(--ap-orange)", critical: "var(--ap-red)", neutral: "var(--ap-gray)",
};
const ICON = 1.75; // SF Symbols 느낌의 일관된 스트로크
const linkIcon = (i?: AiPageLink["icon"]) => i === "resources" ? Boxes : i === "incident" ? CircleAlert : i === "gitops" ? GitBranch : i === "cluster" ? Server : i === "alert" ? BellPlus : ArrowUpRight;
const evIcon = (t: string) => t === "event" ? CircleAlert : t === "metric" ? Activity : FileText;
const LINK = "ap-link"; // 스타일은 .ap-link (스타일 블록)
const inlineMd = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
  .replace(/\[([^\]]+)\]\(([^)]+)\)/g, `<a href="$2" class="${LINK}">$1</a>`);

function RichText({ markdown }: { markdown: string }) {
  return (
    <span className="ai-rich-text">
      {markdown.split("\n").map((rawLine, index) => {
        const line = rawLine.trim();
        if (/^#{1,3}$/.test(line) || line === "-" || line === "--") {
          return <span aria-hidden="true" className="ai-rich-gap" key={`partial-${index}`} />;
        }
        if (/^-{3,}$/.test(line)) return <span aria-hidden="true" className="ai-rich-divider" key={`divider-${index}`} />;
        if (/^#{1,3}\s+/.test(line)) {
          return <span className="ai-rich-heading" dangerouslySetInnerHTML={{ __html: inlineMd(line.replace(/^#{1,3}\s+/, "")) }} key={`heading-${index}`} />;
        }
        if (line.startsWith("- ")) {
          return (
            <span className="ai-rich-list-item" key={`list-${index}`}>
              <span aria-hidden="true" className="ai-rich-bullet">•</span>
              <span dangerouslySetInnerHTML={{ __html: inlineMd(line.slice(2)) }} />
            </span>
          );
        }
        if (!line) return <span aria-hidden="true" className="ai-rich-gap" key={`gap-${index}`} />;
        return <span className="ai-rich-line" dangerouslySetInnerHTML={{ __html: inlineMd(rawLine) }} key={`line-${index}`} />;
      })}
    </span>
  );
}

// ── 파트 렌더러 (단일 표면 안에서 flat) ─────────────────────────────

function TextPart({ part, active, onReady }: { part: AiTextPart; active: boolean; onReady: () => void }) {
  const [n, setN] = useState(active ? 0 : part.markdown.length);
  useEffect(() => {
    if (!active) { setN(part.markdown.length); return; }
    if (!part.markdown) { onReady(); return; }
    setN(0); let i = 0;
    const id = window.setInterval(() => { i += 2; setN(i); if (i >= part.markdown.length) { window.clearInterval(id); onReady(); } }, TIMING.typewriterStepMs);
    return () => window.clearInterval(id);
  }, [active]);
  if (!part.markdown) return null;
  // 타이핑 중 미완성 마크다운 토큰(링크/코드/볼드)을 숨겨 원문 기호 노출 방지
  let safe = part.markdown.slice(0, n).replace(/\[[^\]]*(\]\([^)]*)?$/, "");
  if (((safe.match(/`/g) || []).length) % 2) safe = safe.replace(/`([^`]*)$/, "$1");
  if (((safe.match(/\*\*/g) || []).length) % 2) safe = safe.replace(/\*\*([^*]*)$/, "$1");
  return (
    <div className="text-body leading-[1.7] tracking-[-0.006em] text-foreground/90 [&_code]:rounded-md [&_code]:bg-muted/70 [&_code]:px-1.5 [&_code]:py-px [&_code]:font-mono [&_code]:text-[0.82em] [&_strong]:font-semibold [&_strong]:text-foreground">
      <RichText markdown={safe} />
      {active && n < part.markdown.length ? <span className="ml-0.5 inline-block h-3.5 w-[2px] animate-pulse rounded-full bg-primary align-text-bottom" /> : null}
    </div>
  );
}

function StepsPart({ part, active, onReady, evidenceCount }: { part: AiStepsPart; active: boolean; onReady: () => void; evidenceCount: number }) {
  const total = part.steps.length;
  const [done, setDone] = useState(active ? 0 : total);
  const [collapsed, setCollapsed] = useState(!active && !part.running);
  useEffect(() => {
    if (!active) { setDone(total); return; }
    setDone(0); let d = 0;
    const id = window.setInterval(() => {
      d += 1; setDone(d);
      const finished = part.running ? d >= total - 1 : d >= total;
      if (finished) { window.clearInterval(id); if (!part.running) { onReady(); window.setTimeout(() => setCollapsed(true), TIMING.stepCollapseMs); } }
    }, TIMING.stepRevealMs);
    return () => window.clearInterval(id);
  }, [active]);
  const complete = !part.running && done >= total;

  if (collapsed && complete) {
    return (
      <button onClick={() => setCollapsed(false)} type="button"
        className="group/s flex w-fit items-center gap-1.5 rounded-full text-caption font-medium text-muted-foreground/80 transition-colors hover:text-foreground"
        style={{ animation: `fadeUp 0.4s ${SPRING}` }}>
        <span className="grid size-4 place-items-center rounded-full ap-ok-bg"><Check className="size-2.5 ap-ok" /></span>
        <span>근거 {total}단계 확인{evidenceCount ? ` · ${evidenceCount}건` : ""}</span>
        <ChevronDown className="size-3 opacity-0 transition-opacity group-hover/s:opacity-50" />
      </button>
    );
  }
  return (
    <div className="grid gap-2">
      <div className="flex items-center gap-1.5 text-caption font-medium text-muted-foreground">
        {complete ? <Check className="size-3.5 ap-ok" /> : <Spinner className="size-3.5 ap-accent" decorative />}
        <span className="tracking-[-0.01em]">{complete ? "근거 확인 완료" : "확인하는 중"}</span>
        <span className="tabular-nums opacity-60">{Math.min(done + (part.running ? 1 : 0), total)}/{total}</span>
      </div>
      <ol className="ml-[6px] grid gap-2 border-l border-border/70 pl-4">
        {part.steps.map((s, i) => {
          const isDone = i < done;
          const isRunning = i === done && (part.running || done < total);
          if (!isDone && !isRunning) return null;
          return (
            <li key={s.id} className="relative flex items-center gap-2 text-label" style={{ animation: `stepIn 0.42s ${SPRING}` }}>
              <span className="absolute -left-[21px] grid size-4 place-items-center rounded-full bg-card ring-4 ring-card">
                {isDone ? <span className="grid size-4 place-items-center rounded-full ap-ok-bg"><Check className="size-2.5 ap-ok" /></span> : <Spinner className="size-3 ap-accent" decorative />}
              </span>
              <span className="font-medium text-foreground/90">{s.label}</span>
              {isDone && s.detail ? <span className="truncate text-muted-foreground/80">· {s.detail}</span> : isRunning ? <span className="text-muted-foreground/70">…</span> : null}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/** 애플 헬스풍 지표 행 — hairline 위에 큰 숫자. (중첩 카드 아님) */
function ResultPart({ part, first }: { part: AiResultPart; first?: boolean }) {
  return (
    <div className={`grid gap-2.5 ${first ? "" : "border-t border-black/[0.05] pt-3"}`} style={{ animation: `fadeUp 0.5s ${SPRING}` }}>
      <div className="flex items-center gap-2">
        <span className="size-1.5 rounded-full" style={{ background: toneHex[part.tone] }} />
        <span className="text-body font-semibold tracking-[-0.01em] text-heading">{part.title}</span>
        <span className="text-label text-muted-foreground">· {part.summary}</span>
      </div>
      {part.metrics ? (
        <div className="flex flex-wrap gap-x-8 gap-y-2">
          {part.metrics.map((m) => (
            <div className="grid gap-0.5" key={m.label}>
              <span className="text-kpi font-semibold leading-none tracking-[-0.02em] tabular-nums" style={{ color: toneHex[m.tone] }}>{m.value}</span>
              <span className="text-caption font-medium uppercase tracking-wide text-muted-foreground/70">{m.label}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// AI 안의 링크는 전부 실 목적지 — 셸 버스로 상세 시트/장애 목록을 연다. href="#" 같은 죽은 링크 금지.
const followAiLink = (href: string) => {
  const pod = href.match(/detail=pod\/[^/]+\/([^&]+)/);
  if (pod) { emitAction({ kind: "open_ref", title: "Pod", body: pod[1] }); return; }
  if (href.includes("health=critical")) { emitAction({ kind: "open_crit", title: "", body: "" }); return; }
};

function EvidencePart({ part }: { part: Extract<AiMessagePart, { kind: "evidence" }> }) {
  return (
    <p className="flex flex-wrap items-center gap-x-3.5 gap-y-1.5 text-label" style={{ animation: `fadeUp 0.45s ${SPRING}` }}>
      <span className="font-medium text-muted-foreground/70">근거</span>
      {part.items.map((e) => { const Icon = evIcon(e.type); return (
        <button type="button" key={e.id} onClick={() => followAiLink(e.link ?? "")} className={`inline-flex items-center gap-1 ${LINK}`}><Icon className="size-3 opacity-60" />{e.label}</button>
      ); })}
    </p>
  );
}

function LinksPart({ part }: { part: Extract<AiMessagePart, { kind: "links" }> }) {
  return (
    <p className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-label" style={{ animation: `fadeUp 0.5s ${SPRING}` }}>
      {part.items.map((l) => { const Icon = linkIcon(l.icon); return (
        <button type="button" key={l.href} onClick={() => followAiLink(l.href)} className={`inline-flex items-center gap-1 ${LINK}`}><Icon className="size-3.5 opacity-60" />{l.label}<ArrowUpRight className="size-3 opacity-50" /></button>
      ); })}
    </p>
  );
}

/** 표면 안에서 flat한 대화형 폼(자체 카드 없음 → 카드 중첩 제거). 완료 시 한 줄로 접힘. */
const METRIC_LABEL: Record<string, string> = {
  cpu_pct: "CPU", mem_pct: "메모리", restart_count: "재시작", pod_not_ready: "파드 미준비",
};

function ActionPart({ part, onIdleChange, first }: { part: Extract<AiMessagePart, { kind: "action" }>; onIdleChange?: (idle: boolean) => void; first?: boolean }) {
  const p = part.proposal.payload;
  const metricLabel = METRIC_LABEL[p.metric] ?? p.metric;
  const isPct = p.metric === "cpu_pct" || p.metric === "mem_pct";
  const [state, setState] = useState<"idle" | "creating" | "created" | "error">("idle");
  const [ruleId, setRuleId] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const created = state === "created";
  // 성공 전까지는 이 턴이 자동으로 접히지 않도록 유지 (제안/진행/오류 상태 모두 펼침)
  useEffect(() => { onIdleChange?.(state !== "created"); }, [state]);
  useEffect(() => { if (created) { const id = window.setTimeout(() => setCollapsed(true), TIMING.actionCollapseMs); return () => window.clearTimeout(id); } }, [created]);
  useEffect(() => () => abortRef.current?.abort(), []);

  // AI-06: 실제 알림 규칙 뮤테이션에 위임한다. 타이머로 성공을 가장하지 않고,
  // 서버가 발급한 rule_id만 결과로 노출하며 셸 버스에도 실제 결과를 알린다.
  const create = () => {
    if (state === "creating") return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState("creating");
    void createAiAlertRule(p, controller.signal)
      .then(({ ruleId: id }) => {
        if (controller.signal.aborted) return;
        setRuleId(id); setState("created");
        emitAction({ kind: "alert_rule", title: "알림 규칙 생성됨", body: `${p.name} · ${id}` });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || (typeof cause === "object" && cause !== null && (cause as { name?: string }).name === "AbortError")) return;
        setState("error");
      });
  };

  if (created && collapsed) {
    return (
      <button onClick={() => setCollapsed(false)} type="button"
        className="flex w-fit items-center gap-1.5 text-label text-muted-foreground transition-colors hover:text-foreground"
        style={{ animation: `fadeUp 0.4s ${SPRING}` }}>
        <span className="grid size-4 place-items-center rounded-full ap-ok-bg"><Check className="size-2.5 ap-ok" /></span>
        <span className="font-medium text-foreground/90">{p.name}</span><span>생성됨</span>
      </button>
    );
  }
  return (
    <div className={`grid gap-2.5 ${first ? "" : "border-t border-black/[0.05] pt-3"}`} style={{ animation: `fadeUp 0.5s ${SPRING}` }}>
      <div className="flex items-center gap-2 text-body font-semibold tracking-[-0.01em]">
        <span className={`grid size-5 place-items-center rounded-md ${created ? "ap-ok-bg ap-ok" : "bg-black/[0.05] text-foreground/70"}`}>
          {created ? <Check className="size-3" strokeWidth={ICON} /> : <BellPlus className="size-3" strokeWidth={ICON} />}
        </span>
        {created ? "알림 규칙 생성됨" : "알림 규칙 만들기"}
      </div>
      <dl className={`grid grid-cols-[auto_1fr] gap-x-5 gap-y-1.5 text-label transition-opacity ${created ? "opacity-55" : ""}`}>
        <dt className="text-muted-foreground/80">이름</dt><dd className="font-medium">{p.name}</dd>
        <dt className="text-muted-foreground/80">조건</dt><dd className="font-medium tabular-nums">{metricLabel} {p.comparator} {p.threshold}{isPct ? "%" : ""}</dd>
        <dt className="text-muted-foreground/80">지속</dt><dd className="font-medium tabular-nums">{p.forSeconds}초 이상</dd>
        <dt className="text-muted-foreground/80">범위</dt><dd className="font-medium">{p.scope.clusters.join(", ") || "현재 화면"}</dd>
        {created && ruleId ? (<><dt className="text-muted-foreground/80">규칙 ID</dt><dd className="font-mono text-caption">{ruleId}</dd></>) : null}
      </dl>
      {!created ? (
        <div className="flex flex-col gap-2 pt-0.5">
          <div className="flex items-center gap-2">
            <button type="button" disabled={state === "creating"} onClick={create}
              className="inline-flex items-center gap-1.5 rounded-xl bg-primary px-3.5 py-2 text-body font-medium text-primary-foreground shadow-[0_1px_2px_rgba(0,0,0,0.12)] transition-all hover:brightness-105 active:scale-[0.97] disabled:opacity-60">
              {state === "creating" ? <Spinner className="size-4" decorative /> : <BellPlus className="size-4" />}
              {state === "creating" ? "만드는 중" : state === "error" ? "다시 시도" : "만들기"}
            </button>
          </div>
          {state === "error" ? (
            <p className="flex items-center gap-1.5 text-label" style={{ color: "var(--ap-red)" }}>
              <CircleAlert className="size-3.5" /> 알림 규칙을 생성하지 못했습니다.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function PartView({ part, active, onReady, evidenceCount, onIdleChange, first }: { part: AiMessagePart; active: boolean; onReady: () => void; evidenceCount: number; onIdleChange?: (idle: boolean) => void; first?: boolean }) {
  const fired = useRef(false);
  const ready = () => { if (!fired.current) { fired.current = true; onReady(); } };
  useEffect(() => { fired.current = false; }, [active]);
  useEffect(() => {
    if (active && !(part.kind === "text" || part.kind === "steps")) {
      const id = window.setTimeout(ready, TIMING.partReadyMs); return () => window.clearTimeout(id);
    }
  }, [active]);
  if (part.kind === "text") return <TextPart active={active} onReady={ready} part={part} />;
  if (part.kind === "steps") return <StepsPart active={active} evidenceCount={evidenceCount} onReady={ready} part={part} />;
  if (part.kind === "result") return <ResultPart first={first} part={part} />;
  if (part.kind === "evidence") return <EvidencePart part={part} />;
  if (part.kind === "links") return <LinksPart part={part} />;
  if (part.kind === "action") return <ActionPart first={first} onIdleChange={onIdleChange} part={part} />;
  if (part.kind === "status" && part.state === "pending")
    return <span className="inline-flex w-fit items-center gap-1.5 text-label text-muted-foreground"><Spinner className="size-3.5 ap-accent" decorative /> 확인하고 있습니다…</span>;
  return null;
}

function deriveSummary(turn: AiTurn): { text: string; tone: AiTone } {
  const parts = turn.parts ?? [];
  const r = parts.find((p) => p.kind === "result") as AiResultPart | undefined;
  if (r) return { text: `${r.title} · ${r.summary}`, tone: r.tone };
  const a = parts.find((p) => p.kind === "action") as Extract<AiMessagePart, { kind: "action" }> | undefined;
  if (a) return { text: `알림 규칙 · ${a.proposal.payload.name}`, tone: "warning" };
  const t = parts.find((p) => p.kind === "text" && (p as AiTextPart).markdown) as AiTextPart | undefined;
  if (t) {
    const plain = t.markdown
      .replace(/^#{1,6}\s*/gm, "")
      .replace(/^-{3,}\s*$/gm, "")
      .replace(/^-\s+/gm, "")
      .replace(/[`*]/g, "")
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      .replace(/\s+/g, " ")
      .trim();
    return { text: plain.length > 44 ? plain.slice(0, 44) + "…" : plain, tone: "neutral" };
  }
  return { text: turn.summary ?? "대화", tone: "neutral" };
}

export function isStructuredAssistantTurn(turn: AiTurn): boolean {
  return (turn.parts ?? []).some((part) => part.kind !== "text");
}

function PlainAssistantTurn({ turn }: { turn: AiTurn }) {
  const textParts = (turn.parts ?? []).filter(
    (part): part is AiTextPart => part.kind === "text",
  );
  if (textParts.length === 0) return null;
  return (
    <div
      className="mr-auto grid w-full max-w-[94%] gap-2 px-1 py-1 animate-in fade-in-0 slide-in-from-bottom-1 duration-300"
      data-ai-turn-presentation="chat"
    >
      {textParts.map((part, index) => (
        <TextPart
          active={false}
          key={`${turn.id}-text-${index}`}
          onReady={noop}
          part={part}
        />
      ))}
    </div>
  );
}

/** 접힘/펼침 — CSS grid-template-rows(0fr↔1fr)로 height:auto를 트랜지션. React는 is-collapsed 클래스만 토글(측정·JS조작 없음). */
function AssistantTurn({ turn, onComplete }: { turn: AiTurn; onComplete: () => void }) {
  const parts = turn.parts ?? [];
  const evidenceCount = (parts.find((p) => p.kind === "evidence") as { items?: unknown[] } | undefined)?.items?.length ?? 0;
  const hasRunning = parts.some((p) => (p.kind === "steps" && p.running) || (p.kind === "status" && p.state === "pending"));
  const [shown, setShown] = useState(1);
  const [phase, setPhase] = useState<"play" | "review">("play");
  const [collapsed, setCollapsed] = useState(false);
  const [actionIdle, setActionIdle] = useState(parts.some((p) => p.kind === "action"));
  const summary = deriveSummary(turn);

  const advance = () => setShown((s) => {
    if (s >= parts.length) { setPhase("review"); onComplete(); return s; }
    return s + 1;
  });
  useEffect(() => { if (parts.length === 0) { setPhase("review"); onComplete(); } }, []);

  const instant = phase === "review";
  const canCollapse = instant && !hasRunning && !actionIdle;
  const clickable = collapsed || canCollapse;

  const onSurfaceClick = (e: ReactMouseEvent) => {
    if ((e.target as HTMLElement).closest("a,button,input,textarea,select,label")) return;
    if (collapsed) setCollapsed(false);
    else if (canCollapse) setCollapsed(true);
  };

  return (
    <div onClick={onSurfaceClick}
      className={`group/msg mr-auto w-full max-w-[97%] overflow-hidden border border-border bg-card shadow-[0_2px_10px_-4px_rgba(0,0,0,0.06),0_18px_44px_-22px_rgba(0,0,0,0.2)] animate-in fade-in-0 slide-in-from-bottom-2 duration-300 ${collapsed ? "is-collapsed" : ""} ${clickable ? "cursor-pointer" : ""}`}
      style={{ borderRadius: 20 }}>
      {/* 접힌 요약 */}
      <div className="ac-cap">
        <div className="min-h-0 overflow-hidden">
          <div className="flex items-center gap-2.5 px-4 py-3">
            <span className={`size-2 shrink-0 rounded-full ${summary.tone === "critical" ? "island-pulse" : ""}`} style={{ background: toneHex[summary.tone] }} />
            <span className="min-w-0 flex-1 truncate text-label font-medium text-muted-foreground group-hover/msg:text-foreground/80">{summary.text}</span>
            <ChevronDown className="size-4 shrink-0 -rotate-90 text-muted-foreground/40" />
          </div>
        </div>
      </div>
      {/* 펼친 내용 */}
      <div className="ac-full">
        <div className="min-h-0 overflow-hidden">
          <div className="grid gap-3.5 px-4 py-4">
            {(instant ? parts : parts.slice(0, shown)).map((part, i) => (
              <PartView active={!instant && i === shown - 1} evidenceCount={evidenceCount} first={i === 0} key={i}
                onIdleChange={setActionIdle} onReady={!instant && i === shown - 1 ? advance : () => {}} part={part} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function Thinking() {
  return (
    <div className="mr-auto flex w-fit items-center gap-1.5 rounded-full bg-card/70 px-3.5 py-2.5 shadow-[0_1px_2px_rgba(0,0,0,0.04)] backdrop-blur" style={{ animation: `surfaceIn 0.35s ${SPRING}` }}>
      {[0, 1, 2].map((i) => <span className="size-[7px] rounded-full bg-primary/50" key={i} style={{ animation: `bob 1.1s ${i * 0.15}s infinite ${SPRING}` }} />)}
    </div>
  );
}

function UserTurn({ turn, onShown }: { turn: AiTurn; onShown: () => void }) {
  useEffect(() => { const id = window.setTimeout(onShown, TIMING.userShownMs); return () => window.clearTimeout(id); }, []);
  const question = turn.question ?? "";
  const recoveryPrompt = question.startsWith("🔎 복구 플랜 검토");
  if (recoveryPrompt) {
    const [heading, ...body] = question.split("\n");
    const bodyLines = body.join("\n").trimStart().split("\n");
    return (
      <div
        className="ml-auto w-full max-w-[94%] rounded-[18px] rounded-br-md border border-primary/20 bg-primary/[0.07] px-4 py-3 text-body font-normal leading-relaxed text-foreground shadow-[0_2px_8px_-4px_color-mix(in_oklch,var(--primary)_35%,transparent)]"
        style={{ animation: `userIn 0.42s ${SPRING}` }}
      >
        <div className="font-semibold text-heading">{heading}</div>
        <div className="my-3 border-t border-primary/15" />
        <div>
          {bodyLines.map((line, index) => {
            if (!line) return <div className="h-3" key={`gap-${index}`} />;
            if (line.startsWith("🛠️") || line.startsWith("✅")) {
              return <div className="font-semibold text-heading" key={`${line}-${index}`}>{line}</div>;
            }
            return <div className="whitespace-pre-wrap" key={`${line}-${index}`}>{line}</div>;
          })}
        </div>
      </div>
    );
  }
  return (
    <p
      className="ml-auto w-fit max-w-[80%] whitespace-pre-wrap rounded-[18px] rounded-br-md bg-primary px-3.5 py-2 text-body font-medium leading-relaxed text-primary-foreground shadow-[0_2px_8px_-2px_color-mix(in_oklch,var(--primary)_50%,transparent)]"
      style={{ animation: `userIn 0.42s ${SPRING}` }}
    >
      {question}
    </p>
  );
}

function CollapsedTurn({ turn, onShown }: { turn: AiTurn; onShown: () => void }) {
  useEffect(() => { const id = window.setTimeout(onShown, TIMING.collapsedShownMs); return () => window.clearTimeout(id); }, []);
  const summary = deriveSummary(turn);
  return (
    <div className="group mr-auto flex w-full items-center gap-2.5 rounded-full border border-border bg-card/85 px-3.5 py-2 shadow-[0_1px_2px_rgba(0,0,0,0.05),0_10px_24px_-16px_rgba(0,0,0,0.3)] backdrop-blur-xl transition-[transform,box-shadow] duration-300 hover:-translate-y-px" style={{ animation: `islandIn 0.5s ${SPRING}` }}>
      <span className="size-2 shrink-0 rounded-full" style={{ background: toneHex[summary.tone] }} />
      <span className="min-w-0 flex-1 truncate text-label font-medium text-muted-foreground">{summary.text}</span>
      <ChevronDown className="size-3.5 shrink-0 -rotate-90 text-muted-foreground/40" />
    </div>
  );
}

function recoverySubmittedLabel(route: string): string {
  if (route === "auto") return "자동 복구 요청됨";
  if (isSafePrRoute(route)) return "PR 생성 요청됨";
  return "복구 요청됨";
}

function recoveryAcceptedMessage(route: string): string {
  if (route === "auto") {
    return [
      "✅ **자동 복구 요청을 접수했습니다.**",
      "선택한 복구 조치를 실행 워커에 전달했습니다.",
      "---",
      "실행이 끝나면 성공 조건을 자동으로 검증하고, 이슈 카드와 복구 플랜에 완료 결과를 반영합니다.",
    ].join("\n");
  }
  if (isSafePrRoute(route)) {
    return [
      "✅ **복구 PR 요청을 전달했습니다.**",
      "저장소·배포 바인딩·승인 스냅샷을 검증하고 실제 PR 생성 결과를 확인하고 있습니다.",
      "---",
      "검증에 실패하면 필요한 설정과 실제 사유를 표시합니다. PR이 생성된 경우에만 **Pull Request 열기** 링크가 나타납니다.",
    ].join("\n");
  }
  return [
    "✅ **복구 요청을 접수했습니다.**",
    "선택한 복구 조치를 처리할 준비가 완료되었습니다.",
    "---",
    "복구 플랜에서 처리 결과를 확인할 수 있습니다.",
  ].join("\n");
}

function recoveryRejectedMessage(cause: unknown): string {
  const detail = isApiError(cause)
    ? cause.detail ?? cause.message
    : cause instanceof Error && cause.message.trim()
      ? cause.message
      : "복구 요청을 처리하지 못했습니다.";
  return [
    "**복구 요청이 차단되었습니다.**",
    detail,
    "---",
    "복구 플랜은 다시 선택할 수 있는 상태로 유지됩니다. 표시된 연결이나 권한을 보완한 뒤 재시도해 주세요.",
  ].join("\n");
}

function recoveryOutcomeTurn(
  notice: RecoveryOutcomeNotice,
  request: AiRecoveryHandoff,
): AiTurn {
  const lines = [
    notice.detail,
    notice.prUrl ? `🔗 [${pullRequestReference(notice.prUrl).label}](${notice.prUrl})` : null,
    "---",
    "⭐ **확인 결과**",
    notice.kind === "recovery_completed"
      ? "- 이슈 상태가 **해결됨**으로 변경되었습니다."
      : notice.kind === "pull_request_created"
        ? "- PR 생성은 완료됐지만 아직 장애 해결이 확정된 것은 아닙니다."
      : notice.kind === "execution_completed"
          ? "- 복구 명령 실행은 완료됐으며 운영 상태 정상화를 계속 확인합니다."
          : notice.kind === "recovery_blocked"
            ? "- PR 생성 단계는 시작되지 않았습니다. 안내된 GitOps 연결을 구성한 뒤 새 복구 플랜에서 다시 요청해 주세요."
            : "- 복구 플랜에서 실패 원인을 확인한 뒤 다시 시도해 주세요.",
    request.validationChecks.length > 0 ? "" : null,
    request.validationChecks.length > 0 ? "**복구 플랜의 성공 조건**" : null,
    ...request.validationChecks.map((check) => `- ${check}`),
    request.validationChecks.length > 0
      ? "현재 백엔드는 성공 조건별 판정값을 따로 제공하지 않으므로, 위 항목은 검증 기준으로 표시합니다."
      : null,
  ].filter((line): line is string => line !== null);
  return {
    id: `recovery-result:${notice.key}`,
    role: "assistant",
    collapsed: false,
    createdAt: now(),
    parts: [
      {
        kind: "result",
        title: notice.title,
        tone: notice.tone,
        summary: notice.summary,
      },
      {
        kind: "text",
        markdown: lines.join("\n"),
      },
    ],
  };
}

function RecoveryChangePreview({
  request,
  state,
}: {
  request: AiRecoveryHandoff;
  state: RecoveryReviewState;
}) {
  if (!request.preview || state === "idle" || state === "reviewing" || state === "error") return null;
  const submitted = state === "executed";
  const submitting = state === "executing";
  return (
    <div className="mr-auto grid w-full max-w-[97%] gap-3 rounded-2xl border border-border bg-card p-3.5 shadow-[0_8px_22px_-18px_rgba(0,0,0,0.32)]" style={{ animation: `fadeUp 0.45s ${SPRING}` }}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-label font-semibold text-heading">변경사항 미리보기</p>
          <p className="mt-0.5 truncate font-mono text-caption text-muted-foreground">{request.preview.fileName}</p>
        </div>
        <span className="shrink-0 rounded-full bg-primary/[0.08] px-2 py-1 text-caption font-semibold text-primary">
          {request.preview.title}
        </span>
      </div>
      <div className="overflow-hidden rounded-xl border border-white/10 bg-[#15171C] py-2 font-mono text-caption leading-relaxed text-[#D8DDE7]">
        {request.preview.lines.map((line, index) => (
          <div
            className={line.kind === "add" ? "bg-emerald-400/[0.12]" : line.kind === "remove" ? "bg-red-400/[0.12]" : ""}
            key={`${line.kind}-${index}`}
            style={{ display: "grid", gridTemplateColumns: "18px minmax(0, 1fr)", gap: 6, padding: "2px 10px", animation: `fadeUp 0.28s ${SPRING} ${index * 55}ms both` }}
          >
            <span className={line.kind === "add" ? "text-emerald-400" : line.kind === "remove" ? "text-red-400" : "text-white/25"}>
              {line.kind === "add" ? "+" : line.kind === "remove" ? "−" : ""}
            </span>
            <span className="whitespace-pre-wrap break-all">{line.content}</span>
          </div>
        ))}
      </div>
      {request.preview.note ? <p className="text-caption leading-relaxed text-muted-foreground">{request.preview.note}</p> : null}
      {submitting || submitted ? (
        <div className="grid gap-2 border-t border-border pt-3" role="status">
          {[
            ["검토 결과 확인", true],
            [recoveryRouteLabel(request.actionRoute), submitted],
            ["복구 요청 접수", submitted],
          ].map(([label, complete], index) => (
            <div className="flex items-center gap-2 text-label" key={String(label)} style={{ animation: `fadeUp 0.32s ${SPRING} ${index * 90}ms both` }}>
              {complete ? (
                <span className="grid size-4 place-items-center rounded-full bg-emerald-500/12 text-emerald-600"><Check className="size-2.5" strokeWidth={3} /></span>
              ) : (
                <Spinner className="size-4 text-primary" decorative />
              )}
              <span className={complete ? "font-medium text-foreground" : "text-muted-foreground"}>{label}</span>
            </div>
          ))}
          {submitted ? (
            <div className="mt-1 rounded-xl bg-primary/[0.06] px-3 py-2 text-label font-semibold text-primary">
              {recoverySubmittedLabel(request.actionRoute)}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

const now = () => new Date().toISOString();
const noop = () => {};
export type RecoveryReviewState = "idle" | "reviewing" | "ready" | "executing" | "executed" | "error";

export function AiPanel({ onClose, onCancelRecovery, onRecoveryReviewStateChange, embedded = false, contextView = "resources", contextScope = "", contextScopeLabel, full = false, onToggleFull, recoveryRequest = null }: {
  /** 셸 임베드: 닫기 버튼 동작 */
  onClose?: () => void;
  /** 제출 전 복구 AI 검토를 명시적으로 중단한다. 패널 숨기기와 구분한다. */
  onCancelRecovery?: () => void;
  /** 패널을 숨겨도 셸에서 현재 복구 검토 상태를 표시한다. */
  onRecoveryReviewStateChange?: (state: RecoveryReviewState) => void;
  /** 셸 임베드: 고정 460px 대신 컨테이너 폭을 따른다 (리사이즈 핸들 대응) */
  embedded?: boolean;
  /** 현재 화면 맥락 칩 — 셸이 실제 화면·범위를 알려준다 */
  contextView?: string;
  /** API에 전달할 실제 cluster id. 전체 범위는 빈 문자열이다. */
  contextScope?: string;
  /** 맥락 칩의 표시 문자열. cluster id와 분리해 sentinel 전송을 막는다. */
  contextScopeLabel?: string;
  /** 셸 임베드: 전체 화면 상태와 토글 (미전달 시 버튼 미노출) */
  full?: boolean;
  onToggleFull?: () => void;
  /** 선택한 복구 조치를 AI가 검토한 뒤 기존 실행 흐름으로 넘기는 요청 */
  recoveryRequest?: AiRecoveryHandoff | null;
} = {}) {
  const [turns, setTurns] = useState<AiTurn[]>([]);
  const [thinking, setThinking] = useState(false);
  const [listOpen, setListOpen] = useState(false);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [recoveryReviewState, setRecoveryReviewState] = useState<RecoveryReviewState>("idle");
  const [recoveryExecution, setRecoveryExecution] = useState<{
    receipt: AiRecoveryExecutionReceipt;
    submittedAt: string;
  } | null>(null);
  // AI history: 목록에서 고른 대화 id. null이면 라이브 대화 화면.
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [historyDeleteConfirmId, setHistoryDeleteConfirmId] = useState<string | null>(null);
  const [historyDeletePendingId, setHistoryDeletePendingId] = useState<string | null>(null);
  const [historyDeleteAllConfirm, setHistoryDeleteAllConfirm] = useState(false);
  const [historyDeleteAllPending, setHistoryDeleteAllPending] = useState(false);
  const [historyDeleteError, setHistoryDeleteError] = useState<string | null>(null);
  const suggestions = useAiSuggestions(contextView, contextScope);
  const conversations = useAiConversations();
  const detail = useConversationDetail(selectedConversationId);
  const viewingHistory = selectedConversationId !== null;
  const idSeq = useRef(0);
  const chatAbort = useRef<AbortController | null>(null);
  const lastRecoveryRequestId = useRef<string | null>(null);
  const recoveryConversationId = useRef<string | null>(null);
  const recoveryConversationTurnCount = useRef(0);
  const recoveryOutcomeKeys = useRef<Set<string>>(new Set());
  // 알림 액션 되묻기 누적 — /api/ai/chat 은 무상태라 "알람 만들어 줘" → "CPU" →
  // "80%"처럼 나눠 답하면 매 턴이 따로 파싱된다. 서버가 clarification 을 표시한
  // 동안 보류 문장을 여기 누적해, 다음 전송을 "누적 + 새 입력"으로 합쳐 보낸다
  // (말풍선에는 새 입력만 표시). 액션/일반 답변이 오면 초기화.
  const alertDraft = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const scrollToLatest = () => {
    window.requestAnimationFrame(() => {
      const element = scrollRef.current;
      if (element) element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
    });
  };
  useLayoutEffect(() => {
    const el = scrollRef.current; if (!el) return;
    // 사용자가 이미 바닥 근처일 때만 따라감 (위로 스크롤해 읽는 중엔 끌어내리지 않음)
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 140) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  });
  // AI-07: 맥락/범위가 바뀌거나 언마운트되면 진행 중 요청을 취소한다 (stale 응답 차단)
  useEffect(() => () => chatAbort.current?.abort(), []);

  const isAbort = (cause: unknown) =>
    typeof cause === "object" && cause !== null && (cause as { name?: string }).name === "AbortError";

  // AI-03: /api/ai/chat 은 뮤테이션 — 사용자가 명시적으로 보낼 때만 호출한다
  // (마운트·타이머 자동 호출 없음). 응답의 answer/evidence/action 필드만 렌더링하고,
  // 서버가 주지 않는 reasoning step·related link 는 만들지 않는다.
  const send = (text: string, recoveryRequestId?: string, displayText?: string) => {
    const trimmed = text.trim(); if (!trimmed || (thinking && recoveryRequestId === undefined)) return;
    const isRecoveryTurn = recoveryRequest !== null;
    setInput(""); setError(null); setSelectedConversationId(null); // 질문 전송 시 라이브 화면으로
    if (isRecoveryTurn) setRecoveryReviewState("reviewing");
    idSeq.current += 1;
    const userTurn: AiTurn = { id: `u${idSeq.current}`, role: "user", question: displayText?.trim() || trimmed, collapsed: false, createdAt: now() };
    setTurns((prev) => [
      ...prev.map((turn) => (
        turn.role === "assistant" && isStructuredAssistantTurn(turn)
          ? { ...turn, collapsed: true }
          : turn
      )),
      userTurn,
    ]);
    setThinking(true);
    chatAbort.current?.abort();
    const controller = new AbortController();
    chatAbort.current = controller;
    idSeq.current += 1;
    const replyId = `a${idSeq.current}`;
    const outgoing = alertDraft.current === null ? trimmed : `${alertDraft.current} ${trimmed}`;
    const context = buildAiContext(contextView, contextScope);
    const liveReply = isRecoveryTurn
      ? (async () => {
          const previousTurnCount = recoveryConversationTurnCount.current;
          let conversationTurns: AiTurn[];
          if (recoveryConversationId.current === null) {
            const created = await createRecoveryConversation(
              outgoing,
              `복구 플랜 검토 · ${recoveryRequest.actionTitle}`,
              {
                ...context,
                recovery: {
                  request_id: recoveryRequest.id,
                  action_title: recoveryRequest.actionTitle,
                  action_route: recoveryRequest.actionRoute,
                },
              },
              (conversationId) => {
                recoveryConversationId.current = conversationId;
                recoveryConversationTurnCount.current = 1;
              },
              controller.signal,
            );
            recoveryConversationId.current = created.conversationId;
            conversationTurns = created.turns;
          } else {
            conversationTurns = await appendRecoveryConversationMessage(
              recoveryConversationId.current,
              previousTurnCount,
              outgoing,
              context,
              controller.signal,
            );
          }
          recoveryConversationTurnCount.current = conversationTurns.length;
          const assistantTurns = conversationTurns
            .slice(previousTurnCount === 0 ? 1 : previousTurnCount)
            .filter((turn) => turn.role === "assistant");
          return assistantTurns[assistantTurns.length - 1]
            ?? sendAiChatTurn(context, outgoing, replyId, controller.signal);
        })()
      : sendAiChatTurn(context, outgoing, replyId, controller.signal);
    void liveReply
      .then((turn) => {
        if (controller.signal.aborted) return;
        const providerFailed = isRecoveryTurn && isAiProviderFailureTurn(turn);
        alertDraft.current = turn.clarification === true ? outgoing : null;
        setThinking(false); setTurns((prev) => [...prev, turn]);
        if (isRecoveryTurn) {
          setRecoveryReviewState(providerFailed ? "error" : "ready");
        }
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbort(cause)) return;
        if (isRecoveryTurn) {
          void sendAiChatTurn(context, outgoing, replyId, controller.signal)
            .then((turn) => {
              if (controller.signal.aborted) return;
              setThinking(false);
              setTurns((prev) => [...prev, turn]);
              setRecoveryReviewState(isAiProviderFailureTurn(turn) ? "error" : "ready");
            })
            .catch((fallbackCause: unknown) => {
              if (controller.signal.aborted || isAbort(fallbackCause)) return;
              setThinking(false); setError(trimmed); setRecoveryReviewState("error");
            });
          return;
        }
        setThinking(false); setError(trimmed);
      });
  };

  useEffect(() => {
    if (recoveryRequest === null || lastRecoveryRequestId.current === recoveryRequest.id) return;
    chatAbort.current?.abort();
    lastRecoveryRequestId.current = recoveryRequest.id;
    recoveryConversationId.current = null;
    recoveryConversationTurnCount.current = 0;
    recoveryOutcomeKeys.current.clear();
    setRecoveryExecution(null);
    setTurns([]);
    setError(null);
    setInput("");
    setListOpen(false);
    send(recoveryRequest.prompt, recoveryRequest.id, recoveryRequest.displayPrompt);
  }, [recoveryRequest?.id]);

  useEffect(() => {
    if (recoveryRequest === null || recoveryExecution === null) return;
    const controller = new AbortController();
    let timer: number | undefined;
    let stopped = false;
    const poll = async () => {
      try {
        const [auditResponse, issuesResponse] = await Promise.all([
          getAuditTimeline(recoveryExecution.receipt.correlationId, {
            limit: 100,
            signal: controller.signal,
          }),
          listRcaIssues({ limit: 100, signal: controller.signal }),
        ]);
        if (controller.signal.aborted || stopped) return;
        const issueStatus = issuesResponse.items.find(
          (item) => item.correlation_id === recoveryExecution.receipt.correlationId,
        )?.status ?? null;
        const notices = recoveryOutcomeNotices({
          actionRoute: recoveryRequest.actionRoute,
          audit: auditResponse.items,
          issueStatus,
          selectionEventId: recoveryExecution.receipt.eventId,
          submittedAt: recoveryExecution.submittedAt,
        });
        const unseen = notices.filter((notice) => !recoveryOutcomeKeys.current.has(notice.key));
        if (unseen.length > 0) {
          unseen.forEach((notice) => recoveryOutcomeKeys.current.add(notice.key));
          setTurns((previous) => [
            ...previous,
            ...unseen.map((notice) => recoveryOutcomeTurn(notice, recoveryRequest)),
          ]);
          scrollToLatest();
        }
        if (notices.some((notice) => notice.terminal)) {
          stopped = true;
          return;
        }
      } catch (cause: unknown) {
        if (controller.signal.aborted || isAbort(cause)) return;
        if (isApiError(cause) && cause.status === 404) {
          stopped = true;
          cancelRecoveryReview();
          return;
        }
      }
      if (!stopped && !controller.signal.aborted) {
        timer = window.setTimeout(poll, 4_000);
      }
    };
    void poll();
    return () => {
      stopped = true;
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [recoveryExecution, recoveryRequest?.id]);
  useEffect(() => {
    onRecoveryReviewStateChange?.(recoveryReviewState);
  }, [onRecoveryReviewStateChange, recoveryReviewState]);

  // AI history: 목록에서 대화를 고르면 선택 id만 바꾼다. 실제 이력 조회는
  // useConversationDetail(selectedConversationId)가 담당(서버 role/content만 투영).
  const openConversation = (id: string) => {
    chatAbort.current?.abort(); setThinking(false); setError(null);
    alertDraft.current = null;
    setHistoryDeleteConfirmId(null);
    setListOpen(false); setSelectedConversationId(id);
  };

  const newChat = () => { chatAbort.current?.abort(); alertDraft.current = null; setSelectedConversationId(null); setTurns([]); setError(null); setInput(""); setThinking(false); };
  const deleteHistoryConversation = async (conversationId: string) => {
    setHistoryDeletePendingId(conversationId);
    setHistoryDeleteError(null);
    try {
      await deleteStoredAiConversation(conversationId);
      if (selectedConversationId === conversationId) setSelectedConversationId(null);
      setHistoryDeleteConfirmId(null);
    } catch {
      setHistoryDeleteError("대화를 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setHistoryDeletePendingId(null);
    }
  };
  const deleteAllHistoryConversations = async () => {
    setHistoryDeleteAllPending(true);
    setHistoryDeleteError(null);
    try {
      await deleteAllStoredAiConversations();
      setSelectedConversationId(null);
      setTurns([]);
      setHistoryDeleteAllConfirm(false);
    } catch {
      setHistoryDeleteError("전체 대화를 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setHistoryDeleteAllPending(false);
    }
  };
  function cancelRecoveryReview(): void {
    chatAbort.current?.abort();
    alertDraft.current = null;
    lastRecoveryRequestId.current = null;
    recoveryConversationId.current = null;
    recoveryConversationTurnCount.current = 0;
    setSelectedConversationId(null);
    setTurns([]);
    setError(null);
    setInput("");
    setThinking(false);
    setRecoveryReviewState("idle");
    onCancelRecovery?.();
  }

  // 저장된 대화의 이력 턴과 라이브 대화 턴을 같은 표면으로 렌더한다
  const renderTurn = (turn: AiTurn) => {
    if (turn.role === "user") return <UserTurn key={turn.id} onShown={noop} turn={turn} />;
    if (!isStructuredAssistantTurn(turn)) return <PlainAssistantTurn key={turn.id} turn={turn} />;
    if (turn.collapsed) return <CollapsedTurn key={turn.id} onShown={noop} turn={turn} />;
    return <AssistantTurn key={turn.id} onComplete={noop} turn={turn} />;
  };

  return (
    <div
      className={`opsia-ai relative flex min-h-0 max-h-full ${embedded ? "h-full w-full min-w-0" : "h-screen w-[460px]"} flex-col overflow-hidden bg-white`}
      data-ai-panel-root="true"
    >
      <header className="flex shrink-0 items-center gap-2.5 border-b border-black/[0.05] bg-white/60 px-3.5 py-3 backdrop-blur-xl">
        <span className="grid size-9 shrink-0 place-items-center rounded-[13px] bg-[linear-gradient(135deg,#0A84FF,#5AC8FA)] text-primary-foreground shadow-[0_2px_8px_-2px_color-mix(in_oklch,var(--primary)_55%,transparent)]"><Sparkles className="size-4" /></span>
        <div className="min-w-0 flex-1"><h2 className="text-body font-semibold leading-tight tracking-[-0.01em] text-heading">Kyro AI</h2></div>
        <SidePanelWindowControls
          actions={(
            <>
              {recoveryRequest === null ? (
                <SidePanelIconButton
                  ariaLabel="새 대화"
                  onClick={() => newChat()}
                  title="새 대화"
                >
                  <Play size={14} strokeWidth={2.2} />
                </SidePanelIconButton>
              ) : null}
              <SidePanelIconButton
                ariaLabel="대화 목록"
                onClick={() => setListOpen((value) => !value)}
                title="대화 목록"
              >
                <SquarePen size={14} strokeWidth={2.2} />
              </SidePanelIconButton>
              {recoveryRequest !== null && recoveryReviewState !== "executing" && recoveryReviewState !== "executed" ? (
                <SidePanelIconButton
                  ariaLabel="복구 검토 중단"
                  onClick={cancelRecoveryReview}
                  title="복구 검토 중단"
                  tone="danger"
                >
                  <CircleStop size={14} strokeWidth={2.2} />
                </SidePanelIconButton>
              ) : null}
            </>
          )}
          actionsPlacement="after-toggle"
          closeLabel="AI 패널 닫기"
          closeTitle="숨기기"
          expanded={full}
          onClose={onClose ?? noop}
          onExpandedChange={() => onToggleFull?.()}
          panelLabel="AI 패널"
          showExpandedControl={onToggleFull !== undefined}
        />
      </header>
      <div className="flex shrink-0 items-center gap-1.5 border-b border-black/[0.04] bg-white/30 px-3.5 py-2 text-black backdrop-blur">
        <span className="text-caption font-medium text-black">맥락</span>
        <span className="inline-flex items-center gap-1 rounded-full bg-black/[0.05] px-2 py-0.5 text-caption font-medium text-black"><Boxes className="size-3" />{contextView}</span>
        <span className="inline-flex items-center gap-1 rounded-full bg-black/[0.05] px-2 py-0.5 text-caption font-medium text-black"><Server className="size-3" />{contextScopeLabel ?? (contextScope || "전체 클러스터")}</span>
      </div>
      {listOpen ? (
        <div className="max-h-64 shrink-0 overflow-y-auto border-b border-black/[0.06] bg-white/90 shadow-sm backdrop-blur-xl" style={{ animation: `fadeUp 0.2s ${SPRING}` }}>
          <div className="flex items-center justify-between border-b border-black/[0.05] px-3.5 py-2">
            <span className="text-caption font-semibold text-heading">대화 목록</span>
            {conversations.items.length > 0 ? (
              historyDeleteAllConfirm ? (
                <span className="flex items-center gap-1">
                  <button
                    className="rounded-lg bg-red-500/10 px-2 py-1 text-caption font-semibold text-red-600 transition-colors hover:bg-red-500/15 disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={historyDeleteAllPending}
                    onClick={() => void deleteAllHistoryConversations()}
                    type="button"
                  >
                    {historyDeleteAllPending ? "삭제 중…" : "전체 삭제 확인"}
                  </button>
                  <button
                    className="rounded-lg px-2 py-1 text-caption font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={historyDeleteAllPending}
                    onClick={() => setHistoryDeleteAllConfirm(false)}
                    type="button"
                  >
                    취소
                  </button>
                </span>
              ) : (
                <button
                  className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-caption font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  onClick={() => setHistoryDeleteAllConfirm(true)}
                  type="button"
                >
                  <Trash2 className="size-3.5" />
                  전체 삭제
                </button>
              )
            ) : null}
          </div>
          {historyDeleteError ? (
            <p className="px-3.5 py-2 text-caption text-red-600" role="alert">{historyDeleteError}</p>
          ) : null}
          {conversations.status === "loading" ? (
            <p className="flex items-center gap-2 px-3.5 py-3 text-label text-muted-foreground"><Spinner className="size-3.5 ap-accent" decorative /> 대화 목록 불러오는 중…</p>
          ) : conversations.status === "unavailable" ? (
            <p className="flex items-center gap-1.5 px-3.5 py-3 text-label text-inactive-foreground"><CircleAlert className="size-3.5" /> 대화 목록을 불러올 수 없습니다.</p>
          ) : conversations.items.length === 0 ? (
            <p className="px-3.5 py-3 text-label text-inactive-foreground">저장된 대화가 없습니다.</p>
          ) : (
            <ul className="grid gap-0.5 p-2">{conversations.items.map((c) => (
              <li className="flex items-center gap-1 rounded-xl hover:bg-muted" key={c.id}>
                <button className="flex min-w-0 flex-1 items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-body text-foreground" onClick={() => openConversation(c.id)} type="button">
                  <Sparkles className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="flex-1 truncate">{c.title}</span>
                  {c.updatedAt ? <span className="shrink-0 text-caption text-muted-foreground">{c.updatedAt}</span> : null}
                </button>
                {historyDeleteConfirmId === c.id ? (
                  <span className="mr-1 flex shrink-0 items-center gap-1">
                    <button
                      className="rounded-lg bg-red-500/10 px-2 py-1 text-caption font-semibold text-red-600 transition-colors hover:bg-red-500/15 disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={historyDeletePendingId === c.id}
                      onClick={() => void deleteHistoryConversation(c.id)}
                      type="button"
                    >
                      {historyDeletePendingId === c.id ? "삭제 중…" : "삭제"}
                    </button>
                    <button
                      aria-label="삭제 취소"
                      className="grid size-7 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-black/[0.05] hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={historyDeletePendingId === c.id}
                      onClick={() => setHistoryDeleteConfirmId(null)}
                      type="button"
                    >
                      <X className="size-3.5" />
                    </button>
                  </span>
                ) : (
                  <button
                    aria-label={`${c.title} 삭제`}
                    className="mr-1 grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-red-500/10 hover:text-red-600"
                    onClick={() => setHistoryDeleteConfirmId(c.id)}
                    title="대화 삭제"
                    type="button"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                )}
              </li>
            ))}</ul>
          )}
        </div>
      ) : null}

      <div
        className="chatscroll min-h-0 flex-1 space-y-3.5 overflow-y-auto scroll-smooth px-4 py-5 [scrollbar-gutter:stable]"
        data-ai-scroll-region="true"
        ref={scrollRef}
      >
        {viewingHistory ? (
          // 저장된 대화 이력 (읽기 전용). 상세가 없거나 못 불러오면 정직한 빈 상태.
          detail.status === "loading" ? (
            <Thinking />
          ) : detail.status === "unavailable" ? (
            <div className="mr-auto flex w-full max-w-[97%] items-center gap-2.5 rounded-2xl border border-black/[0.06] bg-card px-4 py-3 text-label" style={{ animation: `fadeUp 0.35s ${SPRING}` }}>
              <CircleAlert className="size-4 shrink-0" style={{ color: "var(--ap-red)" }} />
              <span className="flex-1 text-muted-foreground">이 대화의 상세 이력은 관측되지 않습니다.</span>
            </div>
          ) : detail.turns.length === 0 ? (
            <div className="mx-auto mt-8 grid max-w-[85%] place-items-center gap-2 text-center">
              <span className="grid size-11 place-items-center rounded-2xl bg-black/[0.04] text-muted-foreground"><Sparkles className="size-5" /></span>
              <p className="text-body text-muted-foreground">이 대화의 상세 이력은 관측되지 않습니다.</p>
            </div>
          ) : (
            detail.turns.map(renderTurn)
          )
        ) : (
          <>
            {turns.length === 0 && !thinking ? (
              <div className="mx-auto mt-8 grid max-w-[85%] place-items-center gap-2 text-center">
                <span className="grid size-11 place-items-center rounded-2xl bg-black/[0.04] text-muted-foreground"><Sparkles className="size-5" /></span>
                <p className="text-body leading-relaxed text-muted-foreground">
                  <span className="block">현재 화면 맥락으로 질문해 보세요.</span>
                  <span className="block">답변은 관측된 근거에 기반합니다.</span>
                </p>
              </div>
            ) : null}
            {turns.map(renderTurn)}
            {thinking ? <Thinking /> : null}
            {recoveryRequest !== null ? <RecoveryChangePreview request={recoveryRequest} state={recoveryReviewState} /> : null}
            {error !== null ? (
              <div className="mr-auto flex w-full max-w-[97%] items-center gap-2.5 rounded-2xl border border-black/[0.06] bg-card px-4 py-3 text-label" style={{ animation: `fadeUp 0.35s ${SPRING}` }}>
                <CircleAlert className="size-4 shrink-0" style={{ color: "var(--ap-red)" }} />
                <span className="flex-1 text-muted-foreground">답변을 가져오지 못했습니다.</span>
                <button className="shrink-0 rounded-lg px-2.5 py-1 font-medium ap-accent transition-colors hover:bg-black/[0.04]" onClick={() => send(error)} type="button">다시 시도</button>
              </div>
            ) : null}
          </>
        )}
      </div>

      <footer
        className="flex min-h-0 shrink-0 flex-col overflow-hidden border-t border-black/[0.05] bg-white/50 backdrop-blur-xl"
        data-ai-composer-region="true"
        style={{ maxHeight: "min(52%, 28rem)" }}
      >
        <div className="min-h-0 overflow-y-auto px-3.5 pt-3 [scrollbar-gutter:stable]">
          {recoveryRequest !== null ? (
            <div className="mb-3 rounded-2xl border border-primary/20 bg-primary/[0.04] p-3">
            <div className="flex items-start gap-2">
              <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary"><Sparkles className="size-3.5" /></span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-label font-semibold text-heading">{recoveryRequest.actionTitle}</p>
                <p className="mt-0.5 text-caption text-muted-foreground">
                  {recoveryReviewState === "reviewing" ? "AI가 선택한 복구 플랜과 안전 조건을 검토하고 있습니다."
                    : recoveryReviewState === "ready" ? "AI 검토가 완료되었습니다. 답변을 확인한 뒤 복구를 요청할 수 있습니다."
                    : recoveryReviewState === "executing" ? "확인된 복구 조치를 요청하고 있습니다."
                    : recoveryReviewState === "executed" ? "복구 조치가 정상적으로 요청되었습니다."
                    : recoveryReviewState === "error" ? "AI 검토 또는 복구 요청을 완료하지 못했습니다."
                    : "선택한 복구 플랜을 AI와 검토합니다."}
                </p>
              </div>
              <span className="shrink-0 rounded-full bg-black/[0.05] px-2 py-1 text-caption font-medium text-muted-foreground">
                {recoveryRouteLabel(recoveryRequest.actionRoute)}
              </span>
            </div>
            <div className="mt-2.5 flex gap-2">
              {recoveryReviewState === "error" ? (
                <button className="flex-1 rounded-xl border border-border bg-card px-3 py-2 text-label font-semibold text-foreground transition-colors hover:bg-muted"
                  onClick={() => send(recoveryRequest.prompt, recoveryRequest.id, recoveryRequest.displayPrompt)} type="button">
                  다시 검토
                </button>
              ) : null}
              {recoveryReviewState === "executed" ? (
                <div className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-primary/[0.07] px-3 py-2 text-label font-semibold text-primary" role="status">
                  <Check className="size-3.5" strokeWidth={3} />
                  {recoverySubmittedLabel(recoveryRequest.actionRoute)}
                </div>
              ) : (
                <button className="flex-1 rounded-xl bg-primary px-3 py-2 text-label font-semibold text-primary-foreground transition-all hover:brightness-105 active:scale-[0.99] disabled:cursor-not-allowed disabled:bg-primary/15 disabled:text-primary/45"
                  disabled={recoveryReviewState !== "ready" || thinking}
                  onClick={() => {
                    setRecoveryReviewState("executing");
                    const submittedAt = now();
                    const minimumProgressTime = new Promise<void>((resolve) => window.setTimeout(resolve, 900));
                    void Promise.all([recoveryRequest.execute(), minimumProgressTime])
                      .then(([receipt]) => {
                        if (!receipt?.accepted) {
                          throw new Error("복구 요청이 접수되지 않았습니다.");
                        }
                        setRecoveryExecution({ receipt, submittedAt });
                        idSeq.current += 1;
                        setTurns((prev) => [...prev, {
                          id: `a${idSeq.current}`,
                          role: "assistant",
                          collapsed: false,
                          createdAt: now(),
                          parts: [{
                            kind: "text",
                            markdown: recoveryAcceptedMessage(recoveryRequest.actionRoute),
                          }],
                        }]);
                        scrollToLatest();
                        setRecoveryReviewState("executed");
                      })
                      .catch((cause: unknown) => {
                        idSeq.current += 1;
                        setTurns((prev) => [...prev, {
                          id: `a${idSeq.current}`,
                          role: "assistant",
                          collapsed: false,
                          createdAt: now(),
                          parts: [{
                            kind: "text",
                            markdown: recoveryRejectedMessage(cause),
                          }],
                        }]);
                        scrollToLatest();
                        setRecoveryReviewState("error");
                      });
                  }}
                  type="button">
                  {thinking && recoveryReviewState === "ready" ? "답변 확인 중"
                    : recoveryReviewState === "executing" ? "요청 중"
                      : recoveryRequest.actionRoute === "auto" ? "자동 복구 요청"
                        : isSafePrRoute(recoveryRequest.actionRoute) ? "복구 PR 생성"
                          : "복구 요청"}
                </button>
              )}
            </div>
            </div>
          ) : null}
          {suggestions.status === "ready" && suggestions.items.length > 0 ? (
            <div className="mb-2.5 flex flex-wrap gap-1.5">{suggestions.items.map((s) => <button className="rounded-full border border-border bg-card/80 px-3 py-1.5 text-caption font-medium text-muted-foreground shadow-[0_1px_2px_rgba(0,0,0,0.03)] transition-all hover:-translate-y-px hover:border-ring hover:text-foreground hover:shadow-[0_2px_6px_-2px_rgba(0,0,0,0.12)]" key={s.id} onClick={() => send(s.prompt)} type="button">{s.label}</button>)}</div>
          ) : null}
        </div>
        <div className="shrink-0 px-3.5 pb-3.5 pt-3" data-ai-composer="true">
          <div className="relative rounded-[20px] border border-black/[0.12] bg-white shadow-[0_1px_2px_rgba(0,0,0,0.04),inset_0_1px_0_rgba(255,255,255,0.8)] transition-all focus-within:border-primary/40 focus-within:shadow-[0_0_0_4px_color-mix(in_oklch,var(--primary)_12%,transparent)]">
            <textarea
              className="min-h-[60px] w-full resize-none rounded-[20px] bg-white px-3.5 py-3 pr-12 text-body leading-relaxed tracking-[-0.006em] text-black caret-black outline-none placeholder:text-black/40"
              onChange={(e) => setInput(e.currentTarget.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(input); } }}
              placeholder="질문 입력"
              value={input}
            />
            <button className="absolute bottom-2.5 right-2.5 grid size-8 place-items-center rounded-full bg-primary text-primary-foreground shadow-[0_2px_6px_-1px_color-mix(in_oklch,var(--primary)_50%,transparent)] transition-all hover:brightness-105 active:scale-90 disabled:scale-90 disabled:bg-primary/20 disabled:text-primary disabled:opacity-100" disabled={!input.trim()} onClick={() => send(input)} title="보내기" type="button"><Send className="size-4" /></button>
          </div>
        </div>
      </footer>

      <style>{`
        @keyframes surfaceIn { from { opacity: 0; transform: translateY(10px) scale(0.985); } to { opacity: 1; transform: none; } }
        @keyframes userIn { from { opacity: 0; transform: translateY(8px) scale(0.97); } to { opacity: 1; transform: none; } }
        @keyframes fadeUp { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
        @keyframes stepIn { from { opacity: 0; transform: translateX(-6px); } to { opacity: 1; transform: none; } }
        @keyframes collapseIn { from { opacity: 0; transform: translateY(-4px) scale(0.99); } to { opacity: 1; transform: none; } }
        @keyframes bob { 0%, 100% { transform: translateY(0); opacity: 0.5; } 50% { transform: translateY(-4px); opacity: 1; } }
        @keyframes islandIn { from { opacity: 0; transform: translateY(-6px) scale(0.94); } to { opacity: 1; transform: none; } }
        .chatscroll { scrollbar-width: thin; scrollbar-color: rgba(0,0,0,0.16) transparent; }
        .chatscroll::-webkit-scrollbar { width: 10px; }
        .chatscroll::-webkit-scrollbar-track { background: transparent; }
        .chatscroll::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.14); border-radius: 999px; border: 3px solid transparent; background-clip: padding-box; }
        .chatscroll::-webkit-scrollbar-thumb:hover { background: rgba(0,0,0,0.24); background-clip: padding-box; }
        /* 애플 팔레트 토큰 + 유틸 */
        /* 셸 팔레트와 통일 (BLUE·HP.ok·HP.warn·HP.crit)
           — 제품 토큰의 --primary(검정)를 패널 스코프에서 셸 블루로 오버라이드 */
        .opsia-ai {
          color-scheme: light;
          --ap-blue:#0A84FF; --ap-red:#FF5F55; --ap-orange:#FFB340; --ap-green:#30D158; --ap-gray:#8E8E93;
          --background:#E9EBF0; --foreground:#111318; --heading-foreground:#2B2F36; --inactive-foreground:#9AA0AA;
          --card:#FFFFFF; --card-foreground:#111318; --popover:#FFFFFF; --popover-foreground:#111318;
          --primary:#0A84FF; --primary-foreground:#FFFFFF;
          --secondary:#F7F8FA; --secondary-foreground:#2B2F36;
          --muted:#F4F5F7; --muted-foreground:#5F6570;
          --accent:#EDF4FF; --accent-foreground:#0A6CFF;
          --destructive:#FF5F55; --border:#E0E3E8; --input:#E0E3E8; --ring:#0A84FF;
        }
        .opsia-ai button:focus-visible {
          outline: none;
          box-shadow: 0 0 0 3px var(--focus-ring);
        }
        .opsia-ai button:disabled {
          color: var(--disabled-foreground);
          background: var(--disabled-background);
          border-color: var(--border);
          cursor: not-allowed;
          opacity: 1;
        }
        .ap-accent { color: var(--ap-blue); }
        .ap-ok { color: var(--ap-green); }
        .ap-ok-bg { background: color-mix(in srgb, var(--ap-green) 15%, transparent); }
        .ap-link { font-weight: 500; color: var(--ap-blue); text-decoration: underline; text-underline-offset: 3px; text-decoration-color: color-mix(in srgb, var(--ap-blue) 30%, transparent); transition: color .15s, text-decoration-color .15s; }
        .ap-link:hover { color: var(--action-hover); text-decoration-color: var(--action-hover); }
        .ai-rich-text { display: grid; gap: 5px; }
        .ai-rich-line { display: block; }
        .ai-rich-heading { display: block; margin-top: 2px; color: var(--heading-foreground); font-weight: 650; }
        .ai-rich-divider { display: block; height: 1px; margin: 8px 0; background: var(--border); }
        .ai-rich-list-item { display: grid; grid-template-columns: 12px minmax(0,1fr); gap: 5px; color: var(--muted-foreground); }
        .ai-rich-bullet { color: var(--ap-blue); font-weight: 700; }
        .ai-rich-gap { display: block; height: 3px; }
        /* 접힘/펼침 아코디언 (grid-rows 0fr↔1fr, 타이밍은 TIMING 주입) */
        .ac-cap, .ac-full { display: grid; }
        .ac-cap { grid-template-rows: 0fr; opacity: 0; transition: grid-template-rows ${TIMING.collapseSlideMs}ms cubic-bezier(0.4,0,0.2,1), opacity ${TIMING.collapseFadeMs}ms ease; }
        .ac-full { grid-template-rows: 1fr; opacity: 1; transition: grid-template-rows ${TIMING.collapseSlideMs}ms cubic-bezier(0.4,0,0.2,1), opacity ${TIMING.collapseFadeMs}ms ease ${TIMING.collapseFadeMs}ms; }
        .is-collapsed .ac-cap { grid-template-rows: 1fr; opacity: 1; transition: grid-template-rows ${TIMING.collapseSlideMs}ms cubic-bezier(0.4,0,0.2,1), opacity ${TIMING.collapseFadeMs}ms ease ${TIMING.collapseFadeMs}ms; }
        .is-collapsed .ac-full { grid-template-rows: 0fr; opacity: 0; transition: grid-template-rows ${TIMING.collapseSlideMs}ms cubic-bezier(0.4,0,0.2,1), opacity ${TIMING.collapseFadeMs}ms ease; }
        .island-pulse { animation: islandPulse 2s ease-in-out infinite; }
        @keyframes islandPulse { 0%, 100% { box-shadow: 0 0 0 0 color-mix(in oklch, var(--destructive) 45%, transparent); } 50% { box-shadow: 0 0 0 4px color-mix(in oklch, var(--destructive) 0%, transparent); } }
        @media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; } }
      `}</style>
    </div>
  );
}
