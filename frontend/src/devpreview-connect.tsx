/* eslint-disable react-hooks/exhaustive-deps, react-hooks/set-state-in-effect */
// ⚠ 데모 · 환경 연결 마법사. 런처 → (A)Git 저장소 등록 / (B)클러스터 연결(에이전트 설치).
// UI-PHASE2-001 wiring: 클러스터 연결은 라이브 백엔드(providers 카탈로그/디스커버리,
// preflight/register, connection 상태)에 연결됨. 저장소 흐름은 주소 형식 사전검사 뒤
// POST /api/applications/connect에서 리비전·브랜치·매니페스트를 다시 검증하고 등록한다.
// SAFETY(plan §5): 공유 라이브 백엔드. GET 읽기만 마운트 시 자동 실행. 타깃 등록(POST)은
// 사용자의 명시적 클릭에서만 호출하며 타이머/마운트 자동 제출은 없다. 토큰은 저장·로그 금지.
import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  AlertCircle, ArrowLeft, ArrowRight, Check, ChevronRight, Copy, Folder, GitBranch, Globe, RotateCw,
  Info, Lock, Search, Server, Sparkles, X,
} from "lucide-react";
// 브랜드 로고 — 인라인 SVG (Simple Icons). tabler 의존 제거로 데모 안정성 확보.
type BrandIconProps = { size?: number; stroke?: number; style?: React.CSSProperties };
const IconBrandAws = ({ size = 21, style }: BrandIconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" style={style} aria-hidden>
    <path d="M6.763 10.036c0 .296.032.535.088.71.064.176.144.368.256.576.04.063.056.127.056.183 0 .08-.048.16-.152.24l-.503.335a.383.383 0 0 1-.208.072c-.08 0-.16-.04-.239-.112a2.47 2.47 0 0 1-.287-.375 6.18 6.18 0 0 1-.248-.471c-.622.734-1.405 1.101-2.347 1.101-.67 0-1.205-.191-1.596-.574-.391-.384-.59-.894-.59-1.533 0-.678.239-1.23.726-1.644.487-.415 1.133-.623 1.955-.623.272 0 .551.024.846.064.296.04.6.104.918.176v-.583c0-.607-.127-1.03-.375-1.277-.255-.248-.686-.367-1.3-.367-.28 0-.568.031-.863.103-.295.072-.583.16-.862.272a2.287 2.287 0 0 1-.28.104.488.488 0 0 1-.127.023c-.112 0-.168-.08-.168-.247v-.391c0-.128.016-.224.056-.28a.597.597 0 0 1 .224-.167c.279-.144.614-.264 1.005-.36a4.84 4.84 0 0 1 1.246-.151c.95 0 1.644.216 2.091.647.439.43.662 1.085.662 1.963v2.586zm-3.24 1.214c.263 0 .534-.048.822-.144.287-.096.543-.271.758-.51.128-.152.224-.32.272-.512.047-.191.08-.423.08-.694v-.335a6.66 6.66 0 0 0-.735-.136 6.02 6.02 0 0 0-.75-.048c-.535 0-.926.104-1.19.32-.263.215-.39.518-.39.917 0 .375.095.655.295.846.191.2.47.296.838.296zm6.41.862c-.144 0-.24-.024-.304-.08-.064-.048-.12-.16-.168-.311L7.586 5.55a1.398 1.398 0 0 1-.072-.32c0-.128.064-.2.191-.2h.783c.151 0 .255.025.31.08.065.048.113.16.16.312l1.342 5.284 1.245-5.284c.04-.16.088-.264.151-.312a.549.549 0 0 1 .32-.08h.638c.152 0 .256.025.32.08.063.048.12.16.151.312l1.261 5.348 1.381-5.348c.048-.16.104-.264.16-.312a.52.52 0 0 1 .311-.08h.743c.127 0 .2.065.2.2 0 .04-.009.08-.017.128a1.137 1.137 0 0 1-.056.2l-1.923 6.17c-.048.16-.104.263-.168.311a.51.51 0 0 1-.303.08h-.687c-.151 0-.255-.024-.32-.08-.063-.056-.119-.16-.15-.32l-1.238-5.148-1.23 5.14c-.04.16-.087.264-.15.32-.065.056-.177.08-.32.08zm10.256.215c-.415 0-.83-.048-1.229-.143-.399-.096-.71-.2-.918-.32-.128-.071-.215-.151-.247-.223a.563.563 0 0 1-.048-.224v-.407c0-.167.064-.247.183-.247.048 0 .096.008.144.024.048.016.12.048.2.08.271.12.566.215.878.279.319.064.63.096.95.096.502 0 .894-.088 1.165-.264a.86.86 0 0 0 .415-.758.777.777 0 0 0-.215-.559c-.144-.151-.416-.287-.807-.415l-1.157-.36c-.583-.183-1.014-.454-1.277-.813a1.902 1.902 0 0 1-.4-1.158c0-.335.073-.63.216-.886.144-.255.335-.479.575-.654.24-.184.51-.32.83-.415.32-.096.655-.136 1.006-.136.175 0 .359.008.535.032.183.024.35.056.518.088.16.04.312.08.455.127.144.048.256.096.336.144a.69.69 0 0 1 .24.2.43.43 0 0 1 .071.263v.375c0 .168-.064.256-.184.256a.83.83 0 0 1-.303-.096 3.652 3.652 0 0 0-1.532-.311c-.455 0-.815.071-1.062.223-.248.152-.375.383-.375.71 0 .224.08.416.24.567.159.152.454.304.877.44l1.134.358c.574.184.99.44 1.237.767.247.327.367.702.367 1.117 0 .343-.072.655-.207.926-.144.272-.336.511-.583.703-.248.2-.543.343-.886.447-.36.111-.734.167-1.142.167zM21.698 16.207c-2.626 1.94-6.442 2.969-9.722 2.969-4.598 0-8.74-1.7-11.87-4.526-.247-.223-.024-.527.272-.351 3.384 1.963 7.559 3.153 11.877 3.153 2.914 0 6.114-.607 9.06-1.852.439-.2.814.287.383.607zM22.792 14.961c-.336-.43-2.22-.207-3.074-.103-.255.032-.295-.192-.063-.36 1.5-1.053 3.967-.75 4.254-.399.287.36-.08 2.826-1.485 4.007-.215.184-.423.088-.327-.151.32-.79 1.03-2.57.695-2.994z"/>
  </svg>
);
const IconBrandGoogle = ({ size = 21, style }: BrandIconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" style={style} aria-hidden>
    <path d="M12.19 2.38a9.344 9.344 0 0 0-9.234 6.893c.053-.02-.055.013 0 0-3.875 2.551-3.922 8.11-.247 10.941l.006-.007-.007.03a6.717 6.717 0 0 0 4.077 1.356h5.173l.03.03h5.192c6.687.053 9.376-8.605 3.835-12.35a9.365 9.365 0 0 0-2.821-4.552l-.043.043.006-.05A9.344 9.344 0 0 0 12.19 2.38zm-.358 4.146c1.244-.04 2.518.368 3.486 1.15a5.186 5.186 0 0 1 1.862 4.078v.518c3.53-.07 3.53 5.262 0 5.193h-5.193l-.008.009v-.04H6.785a2.59 2.59 0 0 1-1.067-.23h.001a2.597 2.597 0 1 1 3.437-3.437l3.013-3.012A6.747 6.747 0 0 0 8.11 8.24c.018-.01.04-.026.054-.023a5.186 5.186 0 0 1 3.67-1.69z"/>
  </svg>
);
const IconBrandAzure = ({ size = 21, style }: BrandIconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" style={style} aria-hidden>
    <path d="M22.379 23.343a1.62 1.62 0 0 0 1.536-2.14v.002L17.35 1.76A1.62 1.62 0 0 0 15.816.657H8.184A1.62 1.62 0 0 0 6.65 1.76L.086 21.204a1.62 1.62 0 0 0 1.536 2.139h4.741a1.62 1.62 0 0 0 1.535-1.103l.977-2.892 4.947 3.675c.28.208.618.32.966.32m-3.084-12.531 3.624 10.739a.54.54 0 0 1-.51.713v-.001h-.03a.54.54 0 0 1-.322-.106l-9.287-6.9h4.853m6.313 7.006c.116-.326.13-.694.007-1.058L9.79 1.76a1.722 1.722 0 0 0-.007-.02h6.034a.54.54 0 0 1 .512.366l6.562 19.445a.54.54 0 0 1-.338.684"/>
  </svg>
);
const IconBrandDocker = ({ size = 21, style }: BrandIconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" style={style} aria-hidden>
    <path d="M13.983 11.078h2.119a.186.186 0 00.186-.185V9.006a.186.186 0 00-.186-.186h-2.119a.185.185 0 00-.185.185v1.888c0 .102.083.185.185.185m-2.954-5.43h2.118a.186.186 0 00.186-.186V3.574a.186.186 0 00-.186-.185h-2.118a.185.185 0 00-.185.185v1.888c0 .102.082.185.185.185m0 2.716h2.118a.187.187 0 00.186-.186V6.29a.186.186 0 00-.186-.185h-2.118a.185.185 0 00-.185.185v1.887c0 .102.082.185.185.186m-2.93 0h2.12a.186.186 0 00.184-.186V6.29a.185.185 0 00-.185-.185H8.1a.185.185 0 00-.185.185v1.887c0 .102.083.185.185.186m-2.964 0h2.119a.186.186 0 00.185-.186V6.29a.185.185 0 00-.185-.185H5.136a.186.186 0 00-.186.185v1.887c0 .102.084.185.186.186m5.893 2.715h2.118a.186.186 0 00.186-.185V9.006a.186.186 0 00-.186-.186h-2.118a.185.185 0 00-.185.185v1.888c0 .102.082.185.185.185m-2.93 0h2.12a.185.185 0 00.184-.185V9.006a.185.185 0 00-.184-.186h-2.12a.185.185 0 00-.184.185v1.888c0 .102.083.185.185.185m-2.964 0h2.119a.185.185 0 00.185-.185V9.006a.185.185 0 00-.184-.186h-2.12a.186.186 0 00-.186.186v1.887c0 .102.084.185.186.185m-2.92 0h2.12a.185.185 0 00.184-.185V9.006a.185.185 0 00-.184-.186h-2.12a.185.185 0 00-.184.185v1.888c0 .102.082.185.185.185M23.763 9.89c-.065-.051-.672-.51-1.954-.51-.338.001-.676.03-1.01.087-.248-1.7-1.653-2.53-1.716-2.566l-.344-.199-.226.327c-.284.438-.49.922-.612 1.43-.23.97-.09 1.882.403 2.661-.595.332-1.55.413-1.744.42H.751a.751.751 0 00-.75.748 11.376 11.376 0 00.692 4.062c.545 1.428 1.355 2.48 2.41 3.124 1.18.723 3.1 1.137 5.275 1.137.983.003 1.963-.086 2.93-.266a12.248 12.248 0 003.823-1.389c.98-.567 1.86-1.288 2.61-2.136 1.252-1.418 1.998-2.997 2.553-4.4h.221c1.372 0 2.215-.549 2.68-1.009.309-.293.55-.65.707-1.046l.098-.288Z"/>
  </svg>
);
import { Spinner } from "./shared/ui/primitives/spinner";
import { emitAction } from "./devpreview/bus";
import {
  connectCluster,
  reissueClusterConnectCommand,
  connectApplication,
  previewApplicationConnection,
  isApiError,
  listClusters,
  listRepositoryBranches,
  listRepositoryManifestCandidates,
  probeRepository,
  validateRepositoryManifest,
  type ClusterConnectResponseView,
  type ConnectionPreviewView,
  type RepositoryBranchView,
  type RepositoryManifestCandidateView,
} from "./devpreview/connectFeed";
import {
  PLATFORM_CLOUD_PROVIDER,
  useClusterConnectionStatus,
  useClusterActivationReadiness,
  useClusterProviders,
  type ClusterProvidersView,
  type ConnectionStatusView,
  type ClusterActivationReadinessView,
  type ProviderAvailability,
} from "./devpreview/connectFeed";
import { reasonLabel } from "./devpreview/statusLabel";
import { BLUE, HP, INSET, TINT, UI, blueA, critA, inkA, okA, warnA } from "./devpreview/theme";
import {
  getGithubAppConfig,
  getGithubAppInstallUrl,
  getGithubAppManifest,
  verifyGithubAppInstallation,
  type GithubAppConfig,
} from "./api/github-app";
import { listApplications } from "./api/applications";
import {
  connectionFailurePresentation,
  type ConnectionFailurePresentation,
} from "./devpreview/connectErrors";
import { DEFAULT_CLUSTER_DISPLAY_NAME } from "./devpreview/clusterConnectionDefaults";
import { useSession } from "./devpreview/sessionFeed";
import "./styles/tokens.css";
import "./styles/foundation.css";

const EASE = [0.32, 0.72, 0, 1] as const;
// 로컬 주소 파서 디바운스만 남긴다(서버 호출 아님). 성공을 흉내내는 타이머는 제거됨.
const T = { detectMs: 1000 } as const;
const SPRING = { type: "spring", visualDuration: 0.34, bounce: 0.28 } as const;
// 동적으로 나타나는 섹션(토큰창·App버튼 등)은 전부 같은 스프링을 써서 일관된
// 속도로 부드럽게 등장하게 한다. layout 트랜지션도 같은 값으로 통일해 형제
// 요소가 툭 튀지 않고 자연스럽게 밀려나도록 한다("툭툭" 방지).
const REVEAL_T = { type: "spring", visualDuration: 0.36, bounce: 0.16 } as const;
const REVEAL = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -6 },
  transition: { ...REVEAL_T, layout: REVEAL_T },
} as const;
const swap = {
  initial: { opacity: 0, x: 24 },
  animate: { opacity: 1, x: 0 },
  exit: { opacity: 0, x: -24 },
  transition: { duration: 0.3, ease: EASE },
};

const REPO_STEPS = ["저장소", "배포 대상", "완료"];
const CLUSTER_STEPS = ["정보", "설치", "연결"];

// 설치 플랫폼 · 라이브 providers 디스커버리의 cloud_provider 로 매핑되어 가용성이 결정된다.
const PLATFORMS = [
  { id: "aws", name: "Amazon EKS", sub: "AWS", color: "#FF9900", icon: IconBrandAws },
  { id: "gcp", name: "Google GKE", sub: "GCP", color: "#4285F4", icon: IconBrandGoogle },
  { id: "azure", name: "Azure AKS", sub: "Azure", color: "#0078D4", icon: IconBrandAzure },
  { id: "docker", name: "Docker / 기존 K8s", sub: "로컬", color: "#2496ED", icon: IconBrandDocker },
] as const;
type PlatformId = (typeof PLATFORMS)[number]["id"];

export interface ResumeClusterConnection {
  clusterId: string;
  name: string;
  provider: string;
}

export function connectionPlatform(provider: string): PlatformId {
  const normalized = provider.trim().toLowerCase();
  if (normalized === "eks" || normalized === "aws") return "aws";
  if (normalized === "gke" || normalized === "gcp") return "gcp";
  if (normalized === "aks" || normalized === "azure") return "azure";
  return "docker";
}

// Git 저장소 주소 검증 · 아니면 null (로컬 형식 확인 · 서버 확인 아님)
type Repo = { full: string; visibility: "public" | "private"; branch: string };
function parseRepo(v: string): Repo | null {
  const raw = v.trim();
  if (!raw) return null;
  let s = raw.replace(/^git@([^:]+):/i, "$1/");
  s = s.replace(/^[a-z]+:\/\//i, "").replace(/\.git$/i, "").replace(/\/+$/, "");
  const segs = s.split("/").filter(Boolean);
  const hostLike = (segs[0] || "").includes(".");
  const gitHost = /(github\.com|gitlab\.com|bitbucket\.org|codeberg\.org|gitea|git)/i.test(segs[0] || "");
  let owner: string | undefined, repo: string | undefined;
  if (hostLike) {
    if (!gitHost) return null;
    owner = segs[1]; repo = segs[2];
  } else {
    if (segs.length < 2) return null;
    owner = segs[0]; repo = segs[1];
  }
  if (!owner || !repo) return null;
  if (/\.[a-z0-9]{2,5}$/i.test(repo)) return null;
  return { full: `${owner}/${repo}`, visibility: /public|open/i.test(raw) ? "public" : "private", branch: "main" };
}

// ── 오류/취소 헬퍼 ─────────────────────────────
function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}
function errorText(cause: unknown): string {
  if (isApiError(cause)) return cause.detail ?? cause.message;
  return cause instanceof Error ? cause.message : "요청을 처리하지 못했습니다.";
}

// ── 공용 프리미티브 ─────────────────────────────
const Spin = ({ c = "size-4" }: { c?: string }) => <Spinner className={c} decorative />;

function GapBanner({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3 orange-bg" style={{ borderRadius: 14, padding: "13px 15px" }}>
      <Info className="mt-0.5 size-[17px] shrink-0 c-orange" />
      <div className="text-label leading-[1.55] c-2">{children}</div>
    </div>
  );
}

function ProviderChips({ providers }: { providers: ProviderAvailability[] }) {
  if (providers.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {providers.map((p) => (
        <span key={p.key} title={p.unavailableReason ? reasonLabel(p.unavailableReason) : undefined}
          className={`inline-flex items-center gap-1 rounded-full px-2 py-[3px] text-caption font-semibold ${p.available ? "green-bg c-green" : "orange-bg c-orange"}`}>
          {p.available ? <Check className="size-3" strokeWidth={2.5} /> : <X className="size-3" strokeWidth={2.5} />}
          {p.label}
        </span>
      ))}
    </div>
  );
}

function NextButton({ show, label, onClick }: { show: boolean; label: string; onClick: () => void }) {
  return (
    <AnimatePresence initial={false}>
      {show && (
        <motion.div key="next" initial={{ opacity: 0, height: 0, marginTop: 0 }} animate={{ opacity: 1, height: "auto", marginTop: 4 }} exit={{ opacity: 0, height: 0, marginTop: 0 }} transition={{ duration: 0.28, ease: EASE }} className="overflow-hidden">
          <button onClick={onClick} className="btn-primary flex w-full items-center justify-center gap-1.5 text-section font-semibold tracking-[-0.01em]" style={{ borderRadius: 14, paddingTop: 14, paddingBottom: 14 }}>
            {label} <ArrowRight className="size-[17px]" />
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

// 에러는 흐름에 끼워넣지 않고 최상위 레이어에 플로팅으로 띄운 뒤 자동 소멸시킨다.
// (레이아웃 시프트 0 · "사념파처럼" 나타났다 사라짐)
function FloatingToast({ message, onDismiss }: { message: string | null; onDismiss: () => void }) {
  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(onDismiss, 4200);
    return () => window.clearTimeout(timer);
  }, [message, onDismiss]);
  return (
    <div className="pointer-events-none fixed inset-x-0 z-[2000] flex justify-center px-4" style={{ top: 22 }}>
      <AnimatePresence>
        {message && (
          <motion.div key={message} role="alert"
            initial={{ opacity: 0, y: -16, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -16, scale: 0.96 }}
            transition={{ type: "spring", visualDuration: 0.34, bounce: 0.3 }}
            className="pointer-events-auto flex max-w-[460px] items-center gap-3"
            style={{ background: "var(--surface)", border: `1px solid ${critA(0.28)}`, borderRadius: 14, padding: "13px 18px", boxShadow: "0 24px 60px -16px rgba(0,0,0,0.4), 0 6px 16px -6px rgba(0,0,0,0.16)" }}>
            <span className="grid size-6 shrink-0 place-items-center rounded-full" style={{ background: critA(0.12) }}><AlertCircle className="size-[15px] c-red" strokeWidth={2.4} /></span>
            <span className="text-body font-medium c-ink">{message}</span>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function ShellHeader({ icon: Icon, title, sub, onClose, onBack }: { icon: typeof Server; title: string; sub: string; onClose: () => void; onBack?: () => void }) {
  return (
    <>
      <div className="flex items-center gap-4" style={{ padding: "30px 36px 24px" }}>
        {/* 아이콘 슬롯: 하위 스텝에서는 같은 48×48 자리를 뒤로가기 버튼이 대체한다.
            슬롯 크기가 동일해 제목/부제 위치는 스텝이 바뀌어도 움직이지 않는다. */}
        {onBack ? (
          <button onClick={onBack} aria-label="이전 단계" className="grid size-12 shrink-0 place-items-center rounded-[15px] c-2 transition-colors hover:bg-soft" style={{ background: "var(--fill)" }}><ArrowLeft className="size-[22px]" /></button>
        ) : (
          <span className="grid size-12 shrink-0 place-items-center hdr-grad text-white" style={{ borderRadius: 15, boxShadow: "0 8px 18px -6px rgba(47,91,255,0.5)" }}><Icon className="size-[22px]" /></span>
        )}
        <div className="min-w-0 flex-1">
          <h1 className="text-section font-semibold tracking-[-0.02em] c-ink">{title}</h1>
          <p className="mt-1 text-body c-2">{sub}</p>
        </div>
        <button onClick={onClose} aria-label="닫기" className="grid size-9 place-items-center rounded-full c-3 transition-colors hover:bg-soft" style={{ marginTop: -4 }}><X className="size-5" /></button>
      </div>
      <div style={{ padding: "0 36px" }}><div className="hairline" /></div>
    </>
  );
}

function Steps({ steps, active }: { steps: string[]; active: number }) {
  return (
    <div className="flex items-center" style={{ padding: "26px 36px 0" }}>
      {steps.map((s, i) => {
        const done = i < active, now = i === active;
        return (
          <div key={s} className="flex items-center" style={{ flex: i < steps.length - 1 ? "1 1 0%" : "0 0 auto" }}>
            <div className="flex items-center gap-3">
              <motion.span layout className="grid shrink-0 place-items-center rounded-full font-bold" style={{ width: 34, height: 34, fontSize: "var(--type-section)" }}
                animate={{ backgroundColor: done || now ? "var(--accent)" : inkA(0.07), color: done || now ? UI.card : "var(--ink-3)", boxShadow: now ? `0 0 0 5px ${blueA(0.15)}` : `0 0 0 0px ${blueA(0)}` }} transition={{ duration: 0.3 }}>
                {done ? <Check className="size-[18px]" strokeWidth={3} /> : i + 1}
              </motion.span>
              <span className="text-body font-semibold tracking-[-0.01em]" style={{ color: done || now ? "var(--ink)" : "var(--ink-3)" }}>{s}</span>
            </div>
            {i < steps.length - 1 && (
              <div className="mx-3 h-[3px] flex-1 overflow-hidden rounded-full" style={{ background: inkA(0.08) }}>
                <motion.div className="h-full bg-accent" initial={false} animate={{ width: done ? "100%" : "0%" }} transition={{ duration: 0.4, ease: EASE }} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

const Body = ({ children }: { children: React.ReactNode }) => <div style={{ padding: "28px 36px 34px", flex: "1 1 auto", minHeight: 0, overflowY: "auto" }}><AnimatePresence mode="wait">{children}</AnimatePresence></div>;

// ── A. Git 저장소 등록 (로컬 형식 사전검사 + 서버 리비전/매니페스트 검증) ─────────────
type RepoSource = {
  repo: Repo;
  normalizedRepo: string;
  token: string;
  branches: RepositoryBranchView[];
  defaultBranch: string;
  // GitHub App 원클릭 연결로 돌아온 경우의 설치 id(연결 시 자격증명으로 저장).
  installationId?: string;
};

// GitHub App 섹션 — 구성돼 있으면 "App으로 연결"(사용자 원클릭), 아니면
// "자동 등록"(운영자 1회 · manifest 폼 POST). 토큰 붙여넣기를 대체한다.
function GithubAppConnect({
  repoRef,
  config,
  registerNote,
}: {
  repoRef: string;
  config: GithubAppConfig | null;
  registerNote: "created" | "error" | null;
}) {
  const session = useSession();
  const isAdmin = session.roles.includes("service_admin");
  const sessionReady = session.status !== "loading";
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const org = repoRef.split("/")[0] ?? "";

  const connectWithApp = async () => {
    setBusy(true); setNote(null);
    try {
      const state = crypto.randomUUID();
      sessionStorage.setItem("kyro_gh_app", JSON.stringify({ state, repoRef }));
      const { url } = await getGithubAppInstallUrl(state);
      window.location.href = url;
    } catch {
      setNote("설치 URL을 가져오지 못했습니다.");
      setBusy(false);
    }
  };

  const registerApp = async () => {
    setBusy(true); setNote(null);
    try {
      const state = crypto.randomUUID();
      // base_url 은 서버가 자기 공개 주소로 자동 채운다(입력 불필요).
      const { action_url, manifest } = await getGithubAppManifest({ state, org: org || undefined });
      const form = document.createElement("form");
      form.method = "post";
      form.action = action_url;
      const field = document.createElement("input");
      field.type = "hidden";
      field.name = "manifest";
      field.value = JSON.stringify(manifest);
      form.appendChild(field);
      document.body.appendChild(form);
      form.submit();
    } catch {
      setNote("등록 준비에 실패했습니다.");
      setBusy(false);
    }
  };

  // 구성·세션 로딩 중엔 조용히(고아 상태 방지).
  if (config === null || !sessionReady) return null;

  const banner =
    registerNote === "created" ? (
      <span className="px-0.5 text-caption c-green">GitHub App 등록 완료 — 이제 원클릭으로 연결됩니다.</span>
    ) : registerNote === "error" ? (
      <span className="px-0.5 text-caption c-red">GitHub App 등록에 실패했습니다. 다시 시도하세요.</span>
    ) : null;
  const noteEl = note ? <span className="px-0.5 text-caption c-red">{note}</span> : null;

  // ① 구성됨 → 누구나 원클릭 연결
  if (config.install_available) {
    return (
      <motion.div layout {...REVEAL} className="grid gap-2">
        <button
          onClick={() => void connectWithApp()}
          disabled={busy}
          className="btn-primary flex w-full items-center justify-center gap-2 rounded-[14px] py-3.5 text-section font-semibold disabled:cursor-not-allowed disabled:opacity-60"
        >
          GitHub App으로 연결 <ArrowRight className="size-[17px]" />
        </button>
        {banner}
        {noteEl}
      </motion.div>
    );
  }

  // ② 미구성 + 비어드민 → 관리자 설정 필요(등록 카드 숨김)
  if (!isAdmin) {
    return (
      <motion.div layout {...REVEAL} className="grid gap-1.5 rounded-[14px] p-3.5" style={{ background: "var(--fill)" }}>
        <div className="text-label font-medium c-2">GitHub App이 아직 설정되지 않았어요</div>
        <span className="text-caption c-3">
          관리자가 GitHub App을 등록하면 토큰 없이 연결됩니다. 지금은 아래 액세스 토큰으로 연결하세요.
        </span>
        {banner}
      </motion.div>
    );
  }

  // ③ 미구성 + 어드민 → 원클릭 자동 등록(주소 입력 불필요)
  return (
    <motion.div layout {...REVEAL} className="grid gap-2.5 rounded-[14px] p-3.5" style={{ background: "var(--fill)" }}>
      <div className="text-label font-medium c-2">GitHub App 미설정 · 운영자 1회 자동 등록</div>
      <button
        onClick={() => void registerApp()}
        disabled={busy}
        className="btn-primary flex items-center justify-center gap-2 rounded-[12px] py-3 text-body font-semibold disabled:opacity-60"
      >
        GitHub에서 자동 등록 <ArrowRight className="size-4" />
      </button>
      {banner}
      {noteEl}
    </motion.div>
  );
}

function RepoStep({ providers, onNext }: { providers: ClusterProvidersView; onNext: (v: RepoSource) => void }) {
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<"idle" | "detecting" | "found" | "error">("idle");
  const [repo, setRepo] = useState<Repo | null>(null);
  const [token, setToken] = useState(""); // 로컬 상태만 · 저장/로그 금지
  // access: 주소 인식 직후 토큰 없이 probe 해서 공개/비공개를 판정한다.
  //  - "public": 토큰 불필요 → 토큰 입력창을 숨기고 바로 진행
  //  - "auth":   비공개이거나 접근 불가 → 토큰 입력창을 표시하고 필수로 강제
  const [access, setAccess] = useState<"idle" | "probing" | "public" | "auth">("idle");
  const [accessProbe, setAccessProbe] = useState<Awaited<ReturnType<typeof probeRepository>> | null>(null);
  const [probeStatus, setProbeStatus] = useState<"idle" | "submitting" | "error">("idle");
  const [failure, setFailure] = useState("");
  // GitHub App 구성 상태(토큰 숨김·App 우선 판정) + 운영자 자동등록 복귀 배너.
  const [appConfig, setAppConfig] = useState<GithubAppConfig | null>(null);
  const [appRegisterNote, setAppRegisterNote] = useState<"created" | "error" | null>(null);
  // App 원클릭 연결 복귀 상태 — 설치 id 를 확보하고 그 저장소에 실제로 설치됐는지 확인.
  const [appInstallationId, setAppInstallationId] = useState<string | null>(null);
  const [appReturn, setAppReturn] = useState<
    { kind: "verifying" | "ready" | "mismatch" | "error"; text: string } | null
  >(null);
  const appAvailable = appConfig?.install_available === true;
  // 설치 확인이 끝났으면 "GitHub App으로 연결" CTA 를 다시 보여주지 않는다(중복 유도 방지).
  const installationVerified = appReturn?.kind === "ready";
  // 이미 이 워크스페이스에 연결된 저장소면 처음부터 그렇게 알려준다(재연결 혼란 방지).
  // key 로 저장해 저장소가 바뀌면 파생값이 자연히 null 이 된다(effect 내 동기 setState 금지).
  const [connectedApp, setConnectedApp] = useState<{ key: string; name: string } | null>(null);
  const resolvedRepoFull = repo?.full ?? "";
  const connectedAppName =
    connectedApp && connectedApp.key === resolvedRepoFull.toLowerCase() ? connectedApp.name : null;
  useEffect(() => {
    if (!resolvedRepoFull) return;
    const key = resolvedRepoFull.toLowerCase();
    let cancelled = false;
    listApplications()
      .then((list) => {
        if (cancelled) return;
        const match = (list.applications ?? []).find((app) => {
          const ref = String((app as Record<string, unknown>).repository_ref ?? "");
          return ref.toLowerCase() === key;
        });
        if (match) {
          const name = String((match as Record<string, unknown>).name ?? "") || "연결된 앱";
          setConnectedApp({ key, name });
        }
      })
      .catch(() => { /* 조회 실패는 안내 생략(연결 흐름 무영향) */ });
    return () => { cancelled = true; };
  }, [resolvedRepoFull]);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      getGithubAppConfig()
        .then((c) => { if (!cancelled) setAppConfig(c); })
        .catch(() => { if (!cancelled) setAppConfig({ configured: false, slug: null, install_available: false }); });
    void load();
    const params = new URLSearchParams(window.location.search);
    // 운영자 자동등록 복귀(?github_app_manifest=created|error).
    const outcome = params.get("github_app_manifest");
    if (outcome === "created" || outcome === "error") {
      setAppRegisterNote(outcome);
      if (outcome === "created") void load();
      params.delete("github_app_manifest");
      params.delete("github_app_state");
    }
    // 사용자 원클릭 설치 복귀(?github_app_installation_id&github_app_state).
    const installationId = params.get("github_app_installation_id");
    const returnedState = params.get("github_app_state");
    if (installationId) {
      let saved: { state?: string; repoRef?: string };
      try { saved = JSON.parse(sessionStorage.getItem("kyro_gh_app") || "{}") ?? {}; } catch { saved = {}; }
      sessionStorage.removeItem("kyro_gh_app");
      const stateOk = Boolean(saved.state) && saved.state === returnedState; // CSRF 대조
      const repoRef = String(saved.repoRef || "");
      if (stateOk && repoRef) {
        setAppInstallationId(installationId);
        setInput(repoRef);        // 저장소 자동 복원 → 아래 detect/probe 흐름이 이어짐
        setStatus("detecting");
        setAppReturn({ kind: "verifying", text: "GitHub App 설치를 확인하는 중…" });
        // 설치가 그 저장소를 실제로 포함하고 PR 쓰기 권한이 있는지 서버로 검증.
        void verifyGithubAppInstallation(installationId, repoRef)
          .then((v) => {
            if (cancelled) return;
            if (!v.matches) {
              setAppReturn({ kind: "mismatch", text: "이 설치는 해당 저장소를 포함하지 않습니다. GitHub에서 이 저장소에 App을 설치했는지 확인하세요." });
            } else if (!v.write_capable) {
              setAppReturn({ kind: "ready", text: "설치 확인됨 · 연결을 마칠 수 있습니다(PR 쓰기 권한은 GitHub에서 부여 필요)." });
            } else {
              setAppReturn({ kind: "ready", text: "GitHub App 설치 확인됨 · 이 저장소로 연결을 마칩니다." });
            }
          })
          .catch(() => {
            if (!cancelled) setAppReturn({ kind: "error", text: "설치 확인에 실패했습니다. 잠시 후 다시 시도하세요." });
          });
      } else {
        setAppReturn({ kind: "error", text: "설치 복귀 상태가 유효하지 않습니다(보안 검증 실패). 다시 시도하세요." });
      }
      params.delete("github_app_installation_id");
      params.delete("github_app_setup_action");
      params.delete("github_app_state");
    }
    const rest = params.toString();
    if (window.location.search) {
      window.history.replaceState({}, "", window.location.pathname + (rest ? `?${rest}` : ""));
    }
    return () => { cancelled = true; };
  }, []);

  const handleInputChange = (v: string) => {
    setInput(v);
    setRepo(null);
    setToken("");
    setAccess("idle");
    setAccessProbe(null);
    setProbeStatus("idle");
    setFailure("");
    // 사용자가 주소를 직접 바꾸면 App 복귀 컨텍스트는 무효화(다른 저장소에 오적용 방지).
    setAppInstallationId(null);
    setAppReturn(null);
    setStatus(v.trim() ? "detecting" : "idle");
  };

  useEffect(() => {
    const v = input.trim(); if (!v) return;
    const id = window.setTimeout(() => {
      const parsed = parseRepo(v);
      if (!parsed) { setStatus("error"); return; }
      setRepo(parsed); setStatus("found");
    }, T.detectMs);
    return () => window.clearTimeout(id);
  }, [input]);

  // 주소가 인식되면 토큰 없이 한 번 probe 해서 공개/비공개를 판정한다.
  // 공개면 토큰 입력창을 아예 띄우지 않고, 비공개/접근 불가일 때만 토큰을 요구한다.
  useEffect(() => {
    if (status !== "found" || !repo) return;
    let cancelled = false;
    const controller = new AbortController();
    setAccess("probing");
    setAccessProbe(null);
    setFailure("");
    void (async () => {
      try {
        const probe = await probeRepository(
          repo.full,
          undefined,
          controller.signal,
          appInstallationId ?? undefined,
        );
        if (cancelled) return;
        setAccessProbe(probe);
        setAccess(probe.valid && probe.reachable && probe.private === false ? "public" : "auth");
      } catch {
        if (cancelled || controller.signal.aborted) return;
        // 무인증 접근 실패 → 비공개이거나 권한이 필요하므로 토큰 입력을 요구한다.
        setAccess("auth");
      }
    })();
    return () => { cancelled = true; controller.abort(); };
  }, [status, repo, appInstallationId]);

  const needsToken = access === "auth";
  const resolved = access === "public" || access === "auth";
  const ready = access === "public" || (access === "auth" && token.trim().length > 0);
  const verify = async () => {
    if (!repo || probeStatus === "submitting" || !ready) return;
    if (appReturn?.kind === "mismatch") return; // 설치가 저장소를 포함하지 않으면 진행 차단
    setProbeStatus("submitting");
    setFailure("");
    try {
      // 공개면 이미 받은 무인증 probe 를 재사용하고, 비공개면 토큰으로 다시 검증한다.
      const probe = access === "public" && accessProbe
        ? accessProbe
        : await probeRepository(
            repo.full,
            token.trim() || undefined,
            undefined,
            appInstallationId ?? undefined,
          );
      if (!probe.valid || !probe.reachable) {
        throw new Error(
          probe.errors[0] ||
            (needsToken
              ? "토큰이 유효하지 않거나 저장소 접근 권한이 없습니다."
              : "저장소에 연결할 수 없습니다."),
        );
      }
      const branchList = await listRepositoryBranches(
        probe.normalized_repo_ref,
        undefined,
        appInstallationId ?? undefined,
      );
      const defaultBranch = branchList.default_branch || probe.default_branch || branchList.branches[0]?.name || "main";
      onNext({
        repo: { ...repo, full: probe.normalized_repo_ref, visibility: probe.private ? "private" : "public", branch: defaultBranch },
        normalizedRepo: probe.normalized_repo_ref,
        token,
        branches: branchList.branches,
        defaultBranch,
        // App 원클릭 복귀면 설치 id 를 함께 넘겨 연결 시 자격증명으로 저장한다.
        ...(appInstallationId ? { installationId: appInstallationId } : {}),
      });
    } catch (cause: unknown) {
      setFailure(errorText(cause));
      setProbeStatus("error");
    }
  };

  return (
    <motion.div key="repo" {...swap} style={{ wordBreak: "keep-all", textWrap: "pretty" }} className="grid gap-5">
      <p className="text-body leading-[1.55] c-2">Git 저장소 주소를 확인한 뒤 서버가 선택한 브랜치와 매니페스트를 실제 리비전에서 검증합니다.</p>

      {providers.status === "ready" && providers.sourceProviders.length > 0 && (
        <div className="grid gap-2">
          <span className="px-0.5 text-label font-medium c-3">서버 지원 소스 제공자(라이브)</span>
          <ProviderChips providers={providers.sourceProviders} />
        </div>
      )}

      <div className="field flex items-center gap-3 bg-surface" style={{ borderRadius: 14, padding: "15px 16px" }}>
        {status === "detecting" ? <Spin c="size-[18px] c-accent" /> : <Search className="size-[18px] c-3" />}
        <input value={input} onChange={(e) => handleInputChange(e.currentTarget.value)} placeholder="https://github.com/org/repo" className="w-full bg-transparent font-mono text-body c-ink outline-none placeholder:font-sans placeholder:c-3" />
      </div>

      {/* GitHub App 원클릭 복귀 배너 — 설치 검증 결과를 그대로 노출. */}
      <AnimatePresence mode="popLayout">
        {appReturn && (
          <motion.div
            key="appreturn"
            layout
            {...REVEAL}
            role="status"
            className="flex items-start gap-2.5"
            style={{
              borderRadius: 14,
              padding: "13px 16px",
              background:
                appReturn.kind === "mismatch" || appReturn.kind === "error"
                  ? `var(--err-bg, ${critA(0.08)})`
                  : okA(0.10),
            }}
          >
            {appReturn.kind === "verifying" ? (
              <Spin c="size-4 c-accent" />
            ) : appReturn.kind === "ready" ? (
              <Check className="mt-0.5 size-4 c-green" strokeWidth={3} />
            ) : (
              <AlertCircle className="mt-0.5 size-4 c-red" />
            )}
            <span className="text-label leading-[1.5] c-2">{appReturn.text}</span>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence mode="popLayout">
        {status === "detecting" && (
          <motion.p key="det" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="flex items-center gap-2 px-0.5 text-body c-2"><Spin c="size-3.5 c-accent" /> 주소 형식 확인 중…</motion.p>
        )}
        {status === "error" && (
          <motion.div key="err" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={SPRING} className="flex items-center gap-3.5 err-bg" style={{ borderRadius: 16, padding: "15px 18px" }}>
            <span className="grid size-9 shrink-0 place-items-center rounded-full err-ic-bg"><AlertCircle className="size-5 c-red" /></span>
            <div className="min-w-0 flex-1">
              <div className="text-body font-semibold c-ink">주소 형식을 확인할 수 없어요</div>
              <div className="mt-0.5 text-label c-2">Git 저장소 주소가 맞는지 확인해주세요 · 예: github.com/org/repo</div>
            </div>
          </motion.div>
        )}
        {status === "found" && repo && (
          <motion.div key="found" layout initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, layout: REVEAL_T }} className="grid gap-4">
            <div className="flex items-center gap-4 bg-soft" style={{ borderRadius: 16, padding: "16px 18px" }}>
              <span className="grid size-11 shrink-0 place-items-center bg-surface" style={{ borderRadius: 13, boxShadow: "0 1px 3px rgba(0,0,0,0.08)" }}><Folder className="size-[22px] c-2" /></span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
                  <span className="truncate text-section font-semibold tracking-[-0.015em] c-ink">{repo.full}</span>
                  {/* 뱃지는 클라이언트 추측(parseRepo)이 아니라 무인증 probe 실측(access)으로 표시한다. */}
                  {access === "probing" && (
                    <span className="inline-flex items-center gap-1 rounded-full px-2 py-[3px] text-caption font-semibold c-2" style={{ background: "var(--fill)" }}><Spin c="size-3 c-accent" />접근 확인 중</span>
                  )}
                  {access === "public" && (
                    <span className="inline-flex items-center gap-1 rounded-full green-bg px-2 py-[3px] text-caption font-semibold c-green"><Globe className="size-3" strokeWidth={2.5} />공개 저장소</span>
                  )}
                  {access === "auth" && (
                    <span className="inline-flex items-center gap-1 rounded-full orange-bg px-2 py-[3px] text-caption font-semibold c-orange"><Lock className="size-3" strokeWidth={2.5} />비공개 · 인증 필요</span>
                  )}
                </div>
                <div className="mt-1.5 flex items-center gap-1.5 text-label c-2">
                  <GitBranch className="size-3.5 c-3" /><span className="font-mono">{repo.full}</span><span className="c-3">·</span><span>{access === "public" ? "토큰 없이 연결 가능" : access === "auth" ? "액세스 토큰으로 인증" : "접근 확인 중"}</span>
                </div>
              </div>
            </div>

            {/* 이미 연결된 저장소 — 재연결을 유도하지 않고 사실을 먼저 알려준다. */}
            {connectedAppName && (
              <motion.div key="already" layout {...REVEAL} className="flex items-start gap-2.5"
                style={{ borderRadius: 14, padding: "13px 16px", background: okA(0.10) }}>
                <Check className="mt-0.5 size-4 c-green" strokeWidth={3} />
                <span className="text-label leading-[1.5] c-2">
                  이미 연결된 저장소입니다 — <b>{connectedAppName}</b> 앱이 이 저장소를 사용 중입니다.
                  다른 클러스터·네임스페이스에 추가 배포 대상을 등록할 때만 계속 진행하세요.
                </span>
              </motion.div>
            )}
            {/* 비공개/접근불가일 때만 토큰창을 띄우고 필수로 강제한다.
                probing→public/auth 전환이 툭 튀지 않게 같은 스프링으로 등장/퇴장. */}
            <AnimatePresence mode="popLayout" initial={false}>
            {needsToken && (
              <motion.div key="auth" layout {...REVEAL} className="grid gap-3 pt-1">
                {/* 권장: GitHub App(원클릭) — 미설정이면 어드민 자동등록 / 비어드민 안내.
                    설치 확인이 이미 끝났으면 다시 유도하지 않는다. */}
                {!installationVerified && (
                  <GithubAppConnect repoRef={repo.full} config={appConfig} registerNote={appRegisterNote} />
                )}
                {/* App이 구성되면 토큰 칸을 숨긴다(App 기본). 미구성 시에만 토큰 폴백 노출. */}
                {access === "auth" && (
                  <>
                    <div className="flex items-center gap-2 px-0.5 text-caption c-3">
                      <span className="h-px flex-1" style={{ background: UI.line }} />또는 액세스 토큰<span className="h-px flex-1" style={{ background: UI.line }} />
                    </div>
                    <div className="grid gap-2.5">
                      <span className="px-0.5 text-label font-medium c-2">비공개 저장소 · 액세스 토큰 <span className="c-red">*</span></span>
                      <div className="field flex items-center gap-3 bg-surface" style={{ borderRadius: 14, padding: "15px 16px" }}>
                        <Lock className="size-[18px] c-3" />
                        <input value={token} onChange={(e) => setToken(e.currentTarget.value)} placeholder="필수 · ghp_••••••••••••••••" type="password" autoComplete="new-password" className="w-full bg-transparent font-mono text-body c-ink outline-none placeholder:c-3" />
                      </div>
                      <span className="px-0.5 text-caption c-3">토큰은 브라우저 저장소에 남기지 않으며, 연결 성공 시 서버의 암호화된 저장소 자격증명으로 보관됩니다.</span>
                    </div>
                  </>
                )}
              </motion.div>
            )}
            {/* 공개는 토큰 없이 진행하되, 쓰기(PR)엔 이후 자격증명이 필요함을 정직하게 안내한다. */}
            {access === "public" && !installationVerified && (
              <motion.div key="pub" layout {...REVEAL} className="grid gap-3">
                <div className="flex items-start gap-2 px-0.5 text-label leading-[1.5] c-2">
                  <Globe className="mt-0.5 size-3.5 shrink-0 c-green" />
                  <span>공개 저장소는 토큰 없이 연결·동기화됩니다. 화면에서 YAML을 수정해 PR을 만들려면(쓰기) GitHub App 연결이 필요합니다.</span>
                </div>
                <GithubAppConnect repoRef={repo.full} config={appConfig} registerNote={appRegisterNote} />
              </motion.div>
            )}
            </AnimatePresence>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence mode="popLayout" initial={false}>
      {failure && (
        <motion.div key="fail" layout {...REVEAL} role="alert" className="flex items-start gap-3.5 err-bg" style={{ borderRadius: 16, padding: "14px 16px" }}>
          <AlertCircle className="mt-0.5 size-5 shrink-0 c-red" />
          <div><div className="text-body font-semibold c-ink">저장소 연결 확인 실패</div><div className="mt-1 break-words text-label leading-[1.5] c-2">{failure}</div></div>
        </motion.div>
      )}
      {/* 토큰 경로 버튼: 공개는 항상, 비공개는 App 미가용일 때만(App 가용 시 App 버튼이 경로). */}
      {resolved && (access === "public" || !appAvailable || token.trim().length > 0) && (
        <motion.button key="confirm" layout {...REVEAL} disabled={!ready || probeStatus === "submitting"} onClick={() => void verify()} className="btn-primary flex w-full items-center justify-center gap-2 rounded-[14px] py-3.5 text-section font-semibold disabled:cursor-not-allowed disabled:opacity-60">
          {probeStatus === "submitting" ? <><Spin c="size-4 text-white" /> 저장소·브랜치 확인 중…</> : <>저장소 확인 · 배포 대상 선택 <ArrowRight className="size-[17px]" /></>}
        </motion.button>
      )}
      </AnimatePresence>
    </motion.div>
  );
}

type RepoTargetInput = {
  name: string;
  branch: string;
  manifestPath: string;
  clusterId: string;
  namespace: string;
  environment: string;
};

export interface RepositoryConnectionContext {
  clusterId?: string;
  namespace?: string;
}

// 연결 프리뷰 변경 분류별 표기(클러스터 연결뷰 톤과 통일: 은은한 배경 + 진한 글자).
const CHANGE_META: Record<string, { label: string; tint: { fg: string; bg: string; bd: string } }> = {
  create: { label: "생성", tint: TINT.ok },
  update: { label: "변경", tint: TINT.warn },
  in_sync: { label: "유지", tint: TINT.gray },
  conflict: { label: "겹침", tint: TINT.crit },
};

function ChangeBadge({ change }: { change: string }) {
  const meta = CHANGE_META[change] ?? CHANGE_META.in_sync;
  return (
    <span className="shrink-0 rounded-full px-2 py-0.5 text-caption font-bold" style={{ color: meta.tint.fg, background: meta.tint.bg }}>
      {meta.label}
    </span>
  );
}

function PreviewChip({ change, count }: { change: string; count: number }) {
  const meta = CHANGE_META[change] ?? CHANGE_META.in_sync;
  return (
    <span className="rounded-full px-2.5 py-1 text-caption font-semibold" style={{ color: meta.tint.fg, background: meta.tint.bg }}>
      {meta.label} {count}
    </span>
  );
}

export interface RepositoryTargetCluster {
  id: string;
  name: string;
  environment: string;
  connectionStatus: string;
}

function RepoTargetStep({ source, context, repositoryClusters, onComplete, onReconnectCredential }: {
  source: RepoSource;
  context?: RepositoryConnectionContext;
  repositoryClusters?: readonly RepositoryTargetCluster[];
  onComplete: (repo: string) => void;
  onReconnectCredential: () => void;
}) {
  const repoRef = source.normalizedRepo || source.repo.full;
  const repoSegments = repoRef.split("/");
  const defaultName = repoSegments[repoSegments.length - 1] || "application";
  const [input, setInput] = useState<RepoTargetInput>({
    name: defaultName,
    branch: source.defaultBranch,
    manifestPath: "",
    clusterId: context?.clusterId ?? "",
    namespace: context?.namespace?.trim() || "sandbox",
    environment: "development",
  });
  const [clusters, setClusters] = useState<RepositoryTargetCluster[]>([]);
  const [clusterStatus, setClusterStatus] = useState<"loading" | "ready" | "error">("loading");
  const [submitStatus, setSubmitStatus] = useState<"idle" | "submitting" | "error">("idle");
  const [failure, setFailure] = useState<ConnectionFailurePresentation | null>(null);
  // 리소스 소유권 겹침(다른 앱이 이미 관리 중) 감지 시 사용자 확인을 요구.
  const [conflict, setConflict] = useState<string | null>(null);
  const [manifests, setManifests] = useState<RepositoryManifestCandidateView[]>([]);
  const [manifestStatus, setManifestStatus] = useState<"loading" | "ready" | "error">("loading");
  // 연결 직전 desired vs live 프리뷰(입력이 바뀌면 무효화하고 다시 계산).
  const [preview, setPreview] = useState<ConnectionPreviewView | null>(null);
  const [previewStatus, setPreviewStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");

  useEffect(() => {
    if (repositoryClusters) {
      const connected = repositoryClusters.filter((cluster) =>
        ["online", "connected"].includes(cluster.connectionStatus.toLowerCase())
      );
      setClusters(connected);
      setInput((current) => ({
        ...current,
        clusterId: connected.some((cluster) => cluster.id === current.clusterId)
          ? current.clusterId
          : connected[0]?.id || "",
      }));
      setClusterStatus("ready");
      return undefined;
    }
    const controller = new AbortController();
    void listClusters({}, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
        const connected = response.clusters
          .filter((cluster) => ["online", "connected"].includes(cluster.connection_status.toLowerCase()))
          .map((cluster) => ({
            id: cluster.cluster_id,
            name: cluster.name,
            environment: cluster.environment,
            connectionStatus: cluster.connection_status,
          }));
        setClusters(connected);
        setInput((current) => ({
          ...current,
          clusterId: connected.some((cluster) => cluster.id === current.clusterId)
            ? current.clusterId
            : connected[0]?.id || "",
        }));
        setClusterStatus("ready");
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setClusterStatus("error");
      });
    return () => controller.abort();
  }, [repositoryClusters]);

  useEffect(() => {
    const controller = new AbortController();
    void listRepositoryManifestCandidates(
      repoRef,
      input.branch,
      controller.signal,
      source.installationId ?? undefined,
    )
      .then((response) => {
        if (controller.signal.aborted) return;
        setManifests(response.candidates);
        setInput((current) => ({
          ...current,
          manifestPath: response.candidates.some((candidate) => candidate.path === current.manifestPath)
            ? current.manifestPath
            : response.candidates[0]?.path || "",
        }));
        setManifestStatus("ready");
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setManifests([]);
        setFailure(connectionFailurePresentation(errorText(cause)));
        setManifestStatus("error");
      });
    return () => controller.abort();
  }, [input.branch, repoRef]);

  const update = (key: keyof RepoTargetInput, value: string) => {
    setInput((current) => ({ ...current, [key]: value }));
    if (key === "branch") {
      setManifestStatus("loading");
      setManifests([]);
    }
    setFailure(null);
    setSubmitStatus("idle");
    // 대상·매니페스트가 바뀌면 이전 프리뷰는 무효 — 다시 계산하게 초기화.
    setPreview(null);
    setPreviewStatus("idle");
  };
  const complete = Object.values(input).every((value) => value.trim() !== "") && clusterStatus === "ready" && manifestStatus === "ready";
  const loadPreview = async () => {
    if (!complete || previewStatus === "loading") return;
    setPreviewStatus("loading");
    setFailure(null);
    try {
      const candidate = manifests.find((item) => item.path === input.manifestPath);
      const result = await previewApplicationConnection({
        repository: repoRef,
        branch: input.branch.trim(),
        manifestPath: input.manifestPath.trim(),
        sourceType: candidate?.source_type ?? "",
        clusterId: input.clusterId,
        namespace: input.namespace.trim(),
        ...(source.installationId ? { installationId: source.installationId } : {}),
      });
      setPreview(result);
      setPreviewStatus("ready");
    } catch (cause: unknown) {
      setFailure(connectionFailurePresentation(errorText(cause)));
      setPreviewStatus("error");
    }
  };
  const submit = async (allowConflicts = false) => {
    if (!complete || submitStatus === "submitting") return;
    setSubmitStatus("submitting");
    setFailure(null);
    if (!allowConflicts) setConflict(null);
    try {
      const candidate = manifests.find((item) => item.path === input.manifestPath);
      const validation = await validateRepositoryManifest(
        repoRef,
        input.branch.trim(),
        input.manifestPath.trim(),
        candidate?.source_type ?? "",
        undefined,
        source.installationId ?? undefined,
      );
      if (!validation.valid) throw new Error(validation.errors[0] || "매니페스트 검증에 실패했습니다.");
      await connectApplication({
        name: input.name.trim(),
        repository: repoRef,
        branch: input.branch.trim(),
        manifestPath: input.manifestPath.trim(),
        sourceType: candidate?.source_type ?? "",
        clusterId: input.clusterId,
        namespace: input.namespace.trim(),
        environment: input.environment,
        ...(source.token.trim() ? { token: source.token.trim() } : {}),
        ...(source.installationId ? { installationId: source.installationId } : {}),
        ...(allowConflicts ? { allowConflicts: true } : {}),
      });
      onComplete(repoRef);
    } catch (cause: unknown) {
      // 소유권 겹침(409)이면 실패가 아니라 '확인 후 진행' 흐름으로 전환한다.
      if (isApiError(cause) && cause.status === 409) {
        setConflict(
          "이 저장소가 만들 리소스 중 일부를 이미 다른 앱이 관리하고 있습니다. " +
            "그대로 연결하면 두 소스가 같은 리소스를 서로 덮어써(무한 드리프트) 위험합니다.",
        );
        setSubmitStatus("error");
        return;
      }
      setFailure(connectionFailurePresentation(errorText(cause)));
      setSubmitStatus("error");
    }
  };

  return (
    <motion.div key="repotarget" {...swap} className="grid gap-5">
      <div className="flex items-center gap-4 bg-soft" style={{ borderRadius: 16, padding: "16px 18px" }}>
        <span className="grid size-11 shrink-0 place-items-center bg-surface" style={{ borderRadius: 13, boxShadow: "0 1px 3px rgba(0,0,0,0.08)" }}><Folder className="size-[22px] c-2" /></span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-section font-semibold tracking-[-0.015em] c-ink">{repoRef}</div>
          <div className="mt-1 text-label c-2">서버가 Git 리비전과 매니페스트를 검증한 뒤 배포 대상을 등록합니다.</div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        {([
          ["애플리케이션 이름", "name", input.name],
          ["네임스페이스", "namespace", input.namespace],
        ] as const).map(([label, key, value]) => (
          <label key={key} className="grid gap-1.5 text-label font-semibold c-2">
            {label}
            <input value={value} onChange={(event) => update(key, event.currentTarget.value)} className="field min-w-0 rounded-xl px-3.5 py-3 font-mono text-body font-normal c-ink outline-none" />
          </label>
        ))}
        <label className="grid gap-1.5 text-label font-semibold c-2">
          브랜치
          <select aria-label="브랜치" value={input.branch} onChange={(event) => update("branch", event.currentTarget.value)} className="field min-w-0 rounded-xl px-3.5 py-3 font-mono text-body font-normal c-ink outline-none">
            {(source.branches.length ? source.branches : [{ name: source.defaultBranch, protected: false, default: true }]).map((branch) => <option key={branch.name} value={branch.name}>{branch.name}{branch.default ? " · 기본" : ""}{branch.protected ? " · 보호" : ""}</option>)}
          </select>
        </label>
        <label className="grid gap-1.5 text-label font-semibold c-2">
          매니페스트
          <select aria-label="매니페스트" value={input.manifestPath} disabled={manifestStatus !== "ready" || manifests.length === 0} onChange={(event) => update("manifestPath", event.currentTarget.value)} className="field min-w-0 rounded-xl px-3.5 py-3 font-mono text-body font-normal c-ink outline-none">
            {manifestStatus === "loading" && <option value="">매니페스트 탐색 중…</option>}
            {manifestStatus === "error" && <option value="">매니페스트를 불러오지 못함</option>}
            {manifestStatus === "ready" && manifests.length === 0 && <option value="">발견된 매니페스트 없음</option>}
            {manifests.map((candidate) => <option key={candidate.path} value={candidate.path}>{candidate.display_name || candidate.path} · {candidate.path}</option>)}
          </select>
          {/* 선택한 후보의 선택 근거(왜 이게 추천됐는지)를 그대로 노출. */}
          {(() => {
            const selected = manifests.find((candidate) => candidate.path === input.manifestPath);
            return selected?.reason ? (
              <span className="px-0.5 text-[11px] font-normal c-3">{selected.reason}</span>
            ) : null;
          })()}
        </label>
        <label className="grid gap-1.5 text-label font-semibold c-2">
          연결된 클러스터
          <select aria-label="연결된 클러스터" value={input.clusterId} disabled={clusterStatus !== "ready" || clusters.length === 0} onChange={(event) => update("clusterId", event.currentTarget.value)} className="field min-w-0 rounded-xl px-3.5 py-3 text-body font-normal c-ink outline-none">
            {clusterStatus === "loading" && <option value="">클러스터 확인 중…</option>}
            {clusterStatus === "error" && <option value="">클러스터를 불러오지 못함</option>}
            {clusterStatus === "ready" && clusters.length === 0 && <option value="">연결된 클러스터 없음</option>}
            {clusters.map((cluster) => <option key={cluster.id} value={cluster.id}>{cluster.name} · {cluster.environment}</option>)}
          </select>
        </label>
        <label className="grid gap-1.5 text-label font-semibold c-2">
          환경
          <select aria-label="환경" value={input.environment} onChange={(event) => update("environment", event.currentTarget.value)} className="field min-w-0 rounded-xl px-3.5 py-3 text-body font-normal c-ink outline-none">
            <option value="development">개발</option><option value="staging">스테이징</option><option value="production">운영</option>
          </select>
        </label>
      </div>

      {clusterStatus === "error" && <GapBanner>연결된 클러스터를 불러오지 못했습니다. 서버 연결을 확인한 뒤 다시 열어주세요.</GapBanner>}
      {clusterStatus === "ready" && clusters.length === 0 && <GapBanner>먼저 클러스터를 연결해야 저장소 배포 대상을 등록할 수 있습니다.</GapBanner>}
      {manifestStatus === "ready" && manifests.length === 0 && <GapBanner>선택한 브랜치에서 배포 가능한 Kubernetes 매니페스트를 찾지 못했습니다.</GapBanner>}
      <AnimatePresence mode="popLayout">
        {failure?.kind === "repository_credential" && (
          <motion.div
            key="credential-recovery"
            layout
            {...REVEAL}
            role="alert"
            className="grid gap-3 err-bg"
            style={{ borderRadius: 16, padding: "15px 18px" }}
          >
            <div className="flex items-start gap-3">
              <AlertCircle className="mt-0.5 size-[18px] shrink-0 c-red" />
              <div className="min-w-0">
                <div className="text-body font-semibold c-ink">{failure.title}</div>
                <div className="mt-1 text-label leading-[1.5] c-2">{failure.message}</div>
              </div>
            </div>
            <button
              type="button"
              onClick={onReconnectCredential}
              className="product-focusable product-control flex w-full items-center justify-center gap-2 rounded-[12px] border py-2.5 text-label font-semibold c-2"
              style={{ borderColor: UI.line }}
            >
              <ArrowLeft className="size-4" />
              {failure.actionLabel}
            </button>
          </motion.div>
        )}
      </AnimatePresence>
      <FloatingToast
        message={failure?.kind === "generic" ? failure.message : null}
        onDismiss={() => setFailure(null)}
      />
      <AnimatePresence mode="popLayout">
        {conflict && (
          <motion.div key="conflict" layout {...REVEAL} role="alert" className="grid gap-3 err-bg" style={{ borderRadius: 16, padding: "15px 18px" }}>
            <div className="flex items-start gap-2.5">
              <AlertCircle className="mt-0.5 size-[18px] shrink-0 c-red" />
              <div>
                <div className="text-body font-semibold c-ink">리소스 소유권이 겹칩니다</div>
                <div className="mt-1 break-words text-label leading-[1.5] c-2">{conflict}</div>
              </div>
            </div>
            <div className="flex gap-2.5">
              <button onClick={() => void submit(true)} disabled={submitStatus === "submitting"} className="product-focusable product-destructive flex-1 rounded-[12px] py-2.5 text-label font-bold disabled:opacity-60" style={{ background: HP.crit, color: UI.card }}>
                {submitStatus === "submitting" ? "진행 중…" : "위험 감수하고 그대로 연결"}
              </button>
              <button onClick={() => { setConflict(null); setSubmitStatus("idle"); }} disabled={submitStatus === "submitting"} className="product-focusable product-control flex-1 rounded-[12px] border py-2.5 text-label font-semibold c-2" style={{ borderColor: UI.line }}>
                취소
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <AnimatePresence mode="popLayout">
        {previewStatus === "ready" && preview && (
          <motion.div key="preview" layout {...REVEAL} className="grid gap-3 bg-soft" style={{ borderRadius: 16, padding: "16px 18px" }}>
            <div className="flex items-center justify-between gap-2">
              <div className="text-body font-semibold c-ink">연결 시 클러스터 변경 미리보기</div>
              {preview.revision && <div className="font-mono text-caption c-3">{preview.revision.slice(0, 7)}</div>}
            </div>
            <div className="flex flex-wrap gap-2">
              <PreviewChip change="create" count={preview.create_count} />
              <PreviewChip change="update" count={preview.update_count} />
              <PreviewChip change="in_sync" count={preview.in_sync_count} />
              {preview.conflict_count > 0 && <PreviewChip change="conflict" count={preview.conflict_count} />}
            </div>
            {!preview.live_observed && (
              <div className="text-caption leading-[1.5] c-3">아직 이 클러스터의 관측 데이터가 없어 모두 신규 생성으로 표시됩니다. 연결·관측 후 다시 보면 변경·유지가 구분됩니다.</div>
            )}
            {preview.resources.length > 0 && (
              <div className="grid gap-1.5">
                {preview.resources.map((resource) => (
                  <div key={`${resource.kind}/${resource.namespace ?? "-"}/${resource.name}`} className="flex items-center gap-2.5 bg-surface" style={{ borderRadius: 11, padding: "9px 12px" }}>
                    <ChangeBadge change={resource.change} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-label font-medium c-ink">{resource.kind} · {resource.name}</div>
                      <div className="truncate text-caption c-3">
                        {resource.namespace || "cluster"}
                        {resource.owned_by ? ` · 이미 ${resource.owned_by} 관리` : ""}
                        {resource.field_changes.length > 0 ? ` · ${resource.field_changes.length}개 필드 변경 예정` : ""}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {preview.conflict_count > 0 && (
              <div className="text-caption leading-[1.5] c-red">겹치는 리소스가 있습니다. 그대로 연결하면 다른 앱과 같은 리소스를 서로 덮어써(무한 드리프트) 위험합니다.</div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
      {!conflict && previewStatus !== "ready" && (
        <button disabled={!complete || previewStatus === "loading"} onClick={() => void loadPreview()} className="btn-primary flex w-full items-center justify-center gap-2 rounded-[14px] py-3.5 text-section font-semibold disabled:cursor-not-allowed disabled:opacity-45">
          {previewStatus === "loading" ? <><Spin c="size-4 text-white" /> 변경 미리보기 계산 중…</> : <>연결 시 변경 미리보기 <ArrowRight className="size-[17px]" /></>}
        </button>
      )}
      {!conflict && previewStatus === "ready" && (
        <button disabled={submitStatus === "submitting"} onClick={() => void submit()} className="btn-primary flex w-full items-center justify-center gap-2 rounded-[14px] py-3.5 text-section font-semibold disabled:cursor-not-allowed disabled:opacity-45">
          {submitStatus === "submitting" ? <><Spin c="size-4 text-white" /> 서버 검증·등록 중…</> : <>이대로 연결 <ArrowRight className="size-[17px]" /></>}
        </button>
      )}
    </motion.div>
  );
}

function RepoDoneStep({ repo, onDone }: { repo: string; onDone: () => void }) {
  return (
    <motion.div key="repodone" {...swap} className="grid gap-5 text-center">
      <span className="mx-auto grid size-16 place-items-center rounded-full green-bg"><Check className="size-8 c-green" strokeWidth={2.6} /></span>
      <div><h2 className="text-section font-semibold c-ink">저장소 연결 완료</h2><p className="mt-2 text-body c-2"><span className="font-mono c-ink">{repo}</span>의 검증된 배포 대상이 등록되었습니다.</p></div>
      <button onClick={onDone} className="btn-primary rounded-[14px] py-3.5 text-section font-semibold">GitOps에서 확인</button>
    </motion.div>
  );
}

function RepoWizard({ providers, context, repositoryClusters, onClose, onComplete }: { providers: ClusterProvidersView; context?: RepositoryConnectionContext; repositoryClusters?: readonly RepositoryTargetCluster[]; onClose: () => void; onComplete: (repo: string) => void }) {
  const [step, setStep] = useState(0);
  const [source, setSource] = useState<RepoSource | null>(null);
  const el = {
    0: <RepoStep key="s0" providers={providers} onNext={(value) => { setSource(value); setStep(1); }} />,
    1: source ? (
      <RepoTargetStep
        key="s1"
        source={source}
        context={context}
        repositoryClusters={repositoryClusters}
        onComplete={() => setStep(2)}
        onReconnectCredential={() => setStep(0)}
      />
    ) : null,
    2: source ? <RepoDoneStep key="s2" repo={source.normalizedRepo} onDone={() => onComplete(source.normalizedRepo)} /> : null,
  }[step];
  return (<><ShellHeader icon={GitBranch} title="Git 저장소 연결" sub="Git 원문 검증 · 배포 대상 등록 · Safe PR 준비" onClose={onClose} onBack={step === 1 ? () => setStep(0) : undefined} /><Steps steps={REPO_STEPS} active={step} /><Body>{el}</Body></>);
}

// ── B. 클러스터 연결 (에이전트 설치 · 라이브) ─────────────────────────────
function ClusterInfoStep({
  providers, name, setName, platform, setPlatform, onNext,
}: {
  providers: ClusterProvidersView;
  name: string;
  setName: (v: string) => void;
  platform: PlatformId;
  setPlatform: (v: PlatformId) => void;
  onNext: () => void;
}) {
  const cloudFor = (id: PlatformId) => PLATFORM_CLOUD_PROVIDER[id] ?? "";
  const isDisabled = (id: PlatformId) => {
    if (providers.status !== "ready") return false;
    const info = providers.cloudProviders.get(cloudFor(id));
    return info ? !info.available : false;
  };
  const selectedDisabled = isDisabled(platform);
  return (
    <motion.div key="cinfo" {...swap} className="grid gap-5">
      {providers.status === "unavailable" && (
        <GapBanner>서버가 등록 가능한 클러스터 제공자를 보고하지 않았습니다.</GapBanner>
      )}
      <div className="grid gap-2.5">
        {/* 로딩 상태는 라벨 행 안에서만 표시한다. 별도 줄로 띄우면 로드 완료 시
            사라지면서 아래 전체가 위로 밀려(reflow) 줄바꿈처럼 보이는 버그가 된다. */}
        <div className="flex items-center justify-between px-0.5" style={{ minHeight: 18 }}>
          <span className="text-label font-semibold c-2">플랫폼</span>
          {providers.status === "loading" && (
            <span className="flex items-center gap-1.5 text-caption c-3"><Spin c="size-3 c-accent" /> 확인 중</span>
          )}
        </div>
        <div className="grid grid-cols-2 gap-2.5">
          {PLATFORMS.map((p) => {
            const on = platform === p.id;
            const Icon = p.icon;
            const disabled = isDisabled(p.id);
            return (
              <button key={p.id} disabled={disabled} onClick={() => setPlatform(p.id)} className={`card flex items-center gap-3 ${on ? "card-on" : ""} ${disabled ? "opacity-45" : ""}`} style={{ borderRadius: 14, padding: "12px 13px" }}>
                <span className="grid shrink-0 place-items-center" style={{ width: 34, height: 34, borderRadius: 10, background: `${p.color}1A` }}><Icon size={21} stroke={2} style={{ color: p.color }} /></span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-body font-semibold tracking-[-0.01em] c-ink">{p.name}</div>
                  <div className="text-caption c-3">{p.sub}</div>
                </div>
                <span className="grid shrink-0 place-items-center" style={{ width: 18, height: 18 }}>
                  <motion.span animate={{ scale: on ? 1 : 0, opacity: on ? 1 : 0 }} initial={false} transition={{ type: "spring", visualDuration: 0.26, bounce: 0.3 }} style={{ display: "grid" }}>
                    <Check className="size-[17px] c-accent" strokeWidth={3} />
                  </motion.span>
                </span>
              </button>
            );
          })}
        </div>
      </div>
      <div className="grid gap-2.5">
        {/* P0 이름-only 연결: 플랫폼 선택 후 입력은 표시 이름 하나뿐이다. region/EKS 이름/
            context alias/환경은 UI 에서 받지 않는다 — 실제 클러스터 식별은 사용자가 자기
            터미널(이미 로그인된 컨텍스트)에서 설치 명령을 실행할 때 결정된다. */}
        <span className="px-0.5 text-label font-semibold c-2">클러스터 표시 이름</span>
        <div className="field flex items-center gap-3 bg-surface" style={{ borderRadius: 14, padding: "15px 16px" }}>
          <Server className="size-[18px] c-3" />
          <input aria-label="클러스터 표시 이름" value={name} onChange={(e) => setName(e.currentTarget.value)} placeholder={DEFAULT_CLUSTER_DISPLAY_NAME} className="w-full bg-transparent font-mono text-body c-ink outline-none placeholder:font-sans placeholder:c-3" />
        </div>
      </div>
      <NextButton show={name.trim().length > 1 && !selectedDisabled}
        label="등록 단계로" onClick={onNext} />
    </motion.div>
  );
}

// P0 이름-only 연결 · 설치 명령 OS 탭 — 서버가 생성한 실제 명령을 셸별로 충실 변환만
// 한다(내용/토큰/매니페스트 발명 0). POSIX(macOS/Linux)는 원문 그대로, PowerShell 은
// heredoc(<<EOF … EOF)을 here-string(@'…'@ | …)으로 옮기는 구문 변환만 수행한다.
export type InstallShell = "posix" | "powershell";

export function detectLocalShell(platformText: string): InstallShell {
  return /win/i.test(platformText) ? "powershell" : "posix";
}

export function toPowerShellCommand(posixCommand: string): string {
  // `<명령> <<'?EOF'? … EOF` 패턴 → PowerShell here-string 파이프.
  const heredoc = posixCommand.match(/^([\s\S]*?)<<-?\s*'?"?([A-Za-z_][A-Za-z0-9_]*)'?"?\n([\s\S]*?)\n\2\s*$/);
  if (heredoc) {
    const head = heredoc[1].replace(/-f\s*-\s*$/, "-f -").trimEnd();
    const body = heredoc[3];
    return `@'\n${body}\n'@ | ${head}`;
  }
  return posixCommand; // heredoc 없는 단일 명령은 PowerShell 에서도 동일하게 유효
}

export function toInteractiveSafePosixCommand(command: string): string {
  const trimmed = command.trim();
  if (!trimmed) return "";
  // Rolling deployments can still serve the older ownership guard containing
  // `exit 1`. Preserve the rejection while keeping the user's terminal open.
  if (/^\([\s\S]*\)$/.test(trimmed)) return trimmed;
  return `(${trimmed})`;
}

export function toInteractiveSafePowerShellCommand(command: string): string {
  const trimmed = command.trim();
  if (!trimmed) return "";
  // Older receipts queried the ConfigMap before its namespace existed. Under
  // Windows PowerShell that expected first-run NotFound can be terminating.
  const compatible = trimmed.replace(
    /get configmap target-runtime-config(?! --ignore-not-found)/,
    "get configmap target-runtime-config --ignore-not-found",
  );
  if (compatible.includes("$targetNamespace=")) return compatible;
  return compatible.replace(
    /\$existing=\(& kubectl -n 'target' get configmap target-runtime-config --ignore-not-found -o 'jsonpath=\{\.data\.TARGET_CLUSTER_ID\}' 2>\$null\);/,
    "$existing=''; $targetNamespace=(& kubectl get namespace 'target' --ignore-not-found -o name 2>$null); if ($targetNamespace) { $existing=(& kubectl -n 'target' get configmap target-runtime-config --ignore-not-found -o 'jsonpath={.data.TARGET_CLUSTER_ID}' 2>$null) };",
  );
}

// 설치 진행 표시: "설치 대기" 정적 배지 대신 단계 프로그레스로 진행을 보여주고,
// 타임아웃(expired)·실패(failed) 시 재설치(토큰 재발급) 버튼을 노출한다.
function InstallProgress({ conn, activation, reinstalling, onReinstall }: {
  conn: ConnectionStatusView;
  activation: ClusterActivationReadinessView;
  reinstalling: boolean;
  onReinstall: () => void;
}) {
  const failed = conn.connection === "failed";
  const expired = conn.connection === "expired";
  const terminal = failed || expired;
  const ready = activation.status === "ready";
  const online = conn.connection === "connected";
  // 1 에이전트 연결 대기 → 2 인벤토리 수집 → 3 준비 완료
  const step = ready ? 3 : online ? 2 : 1;
  const pct = terminal ? 100 : (step / 3) * 100;
  const color = failed ? "var(--red)" : expired ? "var(--orange)" : "var(--blue)";
  const label = failed ? "설치 실패" : expired ? "설치 시간 초과" : ready ? "준비 완료" : online ? "인벤토리 수집 중" : "에이전트 연결 대기";
  const sub = failed ? (conn.failureReason === "install_failed" ? "설치가 완료되지 않았어요. 명령을 다시 발급해 재시도하세요." : "에이전트 오류가 감지됐어요. 재설치로 다시 시도하세요.")
    : expired ? "제한 시간 안에 에이전트가 연결되지 않았어요. 재설치로 새 명령을 발급하세요."
    : ready ? "곧 완료 화면으로 이동합니다."
    : online ? "에이전트가 클러스터 상태를 수집하고 있어요."
    : "터미널에서 위 명령을 실행하면 자동으로 진행됩니다.";
  return (
    <div className="inset grid gap-2.5" style={{ padding: "15px 16px" }}>
      <div className="flex items-center gap-2.5">
        {terminal ? (
          <AlertCircle className="size-[18px] shrink-0" style={{ color }} />
        ) : ready ? (
          <span className="grid size-[22px] shrink-0 place-items-center rounded-full green-bg"><Check className="size-[14px] c-green" strokeWidth={3} /></span>
        ) : (
          <Spin c="size-[18px] c-accent" />
        )}
        <span className="flex-1 text-body font-semibold c-ink">{label}</span>
        {!terminal && <span className="text-label font-medium c-3" style={{ fontVariantNumeric: "tabular-nums" }}>{step}/3</span>}
      </div>
      <div className="overflow-hidden rounded-full" style={{ height: 6, background: inkA(0.08) }}>
        <motion.div initial={false} animate={{ width: `${pct}%` }} transition={{ type: "spring", visualDuration: 0.5, bounce: 0 }} style={{ height: "100%", borderRadius: 999, background: color }} />
      </div>
      <span className="text-label leading-[1.5] c-2">{sub}</span>
      {terminal && (
        <button onClick={onReinstall} disabled={reinstalling} className="btn-primary flex w-full items-center justify-center gap-1.5 text-body font-semibold disabled:opacity-50" style={{ borderRadius: 12, paddingTop: 11, paddingBottom: 11, marginTop: 2 }}>
          {reinstalling ? <><Spin c="size-4 text-white" /> 명령 재발급 중…</> : <><RotateCw className="size-4" /> 재설치</>}
        </button>
      )}
    </div>
  );
}

function ClusterInstallStep({
  platform,
  name,
  receipt,
  resumeClusterId,
  onReceiptChange,
  onConnected,
}: {
  platform: PlatformId;
  name: string;
  receipt: ClusterConnectResponseView | null;
  resumeClusterId?: string;
  onReceiptChange: (receipt: ClusterConnectResponseView) => void;
  onConnected: (info: ConnectionStatusView) => void;
}) {
  const pf = PLATFORMS.find((p) => p.id === platform)!;
  const Icon = pf.icon;

  const [phase, setPhase] = useState<"idle" | "registering" | "registered" | "error">(
    receipt ? "registered" : "idle",
  );
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [reinstalling, setReinstalling] = useState(false);
  // 설치 명령 OS 탭 — 로컬 OS 자동 선택(윈도우 → PowerShell), 사용자가 전환 가능.
  const [shell, setShell] = useState<InstallShell>(() => detectLocalShell(typeof navigator === "undefined" ? "" : navigator.platform || navigator.userAgent));
  const ctrlRef = useRef<AbortController | null>(null);

  useEffect(() => () => ctrlRef.current?.abort(), []);

  // 등록이 실제로 완료된 뒤에만(=cluster_id 존재) 연결 상태를 폴링한다(타이머 성공 흉내 없음).
  const conn = useClusterConnectionStatus(receipt?.cluster_id ?? null);
  const activation = useClusterActivationReadiness(
    receipt?.cluster_id ?? null,
    conn.connection,
  );
  useEffect(() => {
    if (activation.status === "ready") onConnected(conn);
  }, [activation.status, conn, onConnected]);

  // 재설치: 기존 등록에 설치 명령을 재발급(토큰 회전)한다. 새 등록을 만들지 않아
  // 고아 등록이 남지 않고, connect_expires_at(타임아웃)도 서버에서 초기화된다.
  const reinstall = () => {
    if (!receipt || reinstalling) return;
    setReinstalling(true); setErrMsg(null);
    void reissueClusterConnectCommand(receipt.cluster_id)
      .then(onReceiptChange)
      .catch((cause: unknown) => { if (!isAbortError(cause)) setErrMsg(errorText(cause)); })
      .finally(() => setReinstalling(false));
  };

  // 닫힌 설치 작업을 다시 열었을 때는 새 등록을 만들지 않는다. 서버가 이미 가진
  // cluster_id에 대해 토큰을 회전하고 설치 명령만 재발급해 중복/덮어쓰기를 막는다.
  // 새 작업일 때만 이름 하나로 등록하고 OS별 설치 명령을 발급한다.
  const runRegister = () => {
    ctrlRef.current?.abort();
    const controller = new AbortController();
    ctrlRef.current = controller;
    setPhase("registering"); setErrMsg(null);
    const request = resumeClusterId
      ? reissueClusterConnectCommand(resumeClusterId, controller.signal)
      : connectCluster({
          name: name.trim(),
          provider: platform === "docker" ? "onprem" : platform,
        }, controller.signal);
    void request
      .then((res) => { if (controller.signal.aborted) return; onReceiptChange(res); setPhase("registered"); })
      .catch((cause: unknown) => { if (controller.signal.aborted || isAbortError(cause)) return; setErrMsg(errorText(cause)); setPhase("error"); });
  };

  const posixCmd = toInteractiveSafePosixCommand(receipt?.install_command ?? "");
  const cmd = shell === "powershell"
    ? toInteractiveSafePowerShellCommand(
        receipt?.powershell_install_command ?? toPowerShellCommand(posixCmd),
      )
    : posixCmd;
  const copy = () => { if (!cmd) return; navigator.clipboard?.writeText(cmd).catch(() => {}); setCopied(true); window.setTimeout(() => setCopied(false), 1600); };
  return (
    <motion.div key="cinstall" {...swap} className="grid gap-5">
      {!receipt && (
        <button onClick={runRegister} disabled={phase === "registering"} className="btn-primary flex w-full items-center justify-center gap-1.5 text-section font-semibold disabled:opacity-50" style={{ borderRadius: 14, paddingTop: 14, paddingBottom: 14 }}>
          {phase === "registering"
            ? <><Spin c="size-[17px]" /> 명령 생성 중…</>
            : resumeClusterId
              ? <><RotateCw className="size-[17px]" /> 설치 명령 다시 발급</>
              : <>설치 명령 생성</>}
        </button>
      )}

      <FloatingToast message={errMsg} onDismiss={() => setErrMsg(null)} />

      {/* 서버 생성 설치 명령 + 부트스트랩 단계 (토큰 포함 · 저장/로그 안 함) */}
      {receipt && (
        <>
          <div className="cmd overflow-hidden" style={{ borderRadius: 16 }}>
            <div className="grid gap-2.5" style={{ padding: "10px 14px", borderBottom: "1px solid var(--line)" }}>
              <span className="flex min-w-0 items-center gap-2 text-label font-semibold c-2"><Icon size={15} stroke={2} style={{ color: pf.color }} />Kyro Agent · {pf.name}</span>
              <span className="flex min-w-0 items-center justify-between gap-2">
                <span className="flex items-center gap-1" role="tablist" aria-label="설치 명령 셸 선택">
                  {([["posix", "macOS/Linux"], ["powershell", "Windows PowerShell"]] as const).map(([id, label]) => (
                    <button key={id} role="tab" aria-selected={shell === id} onClick={() => setShell(id)}
                      className="whitespace-nowrap rounded-full px-2.5 py-1 text-caption font-semibold"
                      style={{ color: shell === id ? "var(--ink)" : "var(--ink-3)", background: shell === id ? inkA(0.07) : "transparent" }}>{label}</button>
                  ))}
                </span>
                <button onClick={copy} className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-label font-semibold transition-colors" style={{ color: copied ? "var(--green)" : "var(--ink-2)", background: copied ? okA(0.12) : inkA(0.05) }}>
                  {copied ? <><Check className="size-3.5" strokeWidth={3} />복사됨</> : <><Copy className="size-3.5" />복사</>}
                </button>
              </span>
            </div>
            <pre className="max-w-full whitespace-pre-wrap break-words font-mono text-label leading-[1.7] c-ink [overflow-wrap:anywhere]" style={{ padding: "14px 16px" }}><code>{cmd}</code></pre>
          </div>

          <InstallProgress conn={conn} activation={activation} reinstalling={reinstalling} onReinstall={reinstall} />
        </>
      )}

    </motion.div>
  );
}

function ClusterDoneStep({ name, connection, onDone }: { name: string; connection: ConnectionStatusView; onDone: () => void }) {
  return (
    <motion.div key="cdone" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.3 }} className="grid gap-5">
      <div className="flex flex-col items-center gap-3 pt-1 text-center">
        <motion.span initial={{ scale: 0, rotate: -18 }} animate={{ scale: 1, rotate: 0 }} transition={{ type: "spring", visualDuration: 0.45, bounce: 0.5 }} className="grid size-16 place-items-center rounded-full lime-bg"><Check className="size-8 c-ink" strokeWidth={3} /></motion.span>
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.14 }}>
          <div className="text-section font-bold tracking-[-0.02em] c-ink">클러스터가 연결됐어요</div>
          <div className="mt-1 text-body c-2"><span className="font-mono c-ink">{name}</span> · Kyro Agent 실행 중</div>
        </motion.div>
      </div>
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.22 }} className="stat">
        <div className="flex-1 text-center" style={{ padding: "15px 0" }}>
          <div className="text-section font-bold tracking-[-0.01em] c-ink">{connection.agentVersion ?? "—"}</div>
          <div className="mt-0.5 text-caption font-medium c-3">에이전트 버전</div>
        </div>
        <div className="flex-1 text-center" style={{ padding: "15px 0", borderLeft: `1px solid ${inkA(0.06)}` }}>
          <div className="text-section font-bold tracking-[-0.01em] c-ink">{connection.connectedAt ? new Date(connection.connectedAt).toLocaleTimeString() : "—"}</div>
          <div className="mt-0.5 text-caption font-medium c-3">연결 시각</div>
        </div>
      </motion.div>
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.3 }} className="flex items-center gap-2.5 bg-soft" style={{ borderRadius: 14, padding: "13px 16px" }}>
        <span className="relative flex size-2.5"><span className="absolute inline-flex size-full animate-ping rounded-full ping-g" /><span className="relative inline-flex size-2.5 rounded-full dot-g" /></span>
        <span className="text-body font-medium c-ink">메트릭·이벤트 수집 중</span>
        <span className="ml-auto text-label c-3">실시간</span>
      </motion.div>
      <button onClick={onDone} className="btn-primary flex w-full items-center justify-center text-section font-semibold" style={{ borderRadius: 14, paddingTop: 14, paddingBottom: 14 }}>완료</button>
    </motion.div>
  );
}

function ClusterWizard({ providers, resumeCluster, onClose, onComplete }: {
  providers: ClusterProvidersView;
  resumeCluster?: ResumeClusterConnection;
  onClose: () => void;
  onComplete: (name: string) => void;
}) {
  const [step, setStep] = useState(resumeCluster ? 1 : 0);
  const [name, setName] = useState(
    resumeCluster?.name ?? DEFAULT_CLUSTER_DISPLAY_NAME,
  );
  const [platform, setPlatform] = useState<PlatformId>(() =>
    resumeCluster ? connectionPlatform(resumeCluster.provider) : "aws");
  const [connection, setConnection] = useState<ConnectionStatusView | null>(null);
  const [installSession, setInstallSession] = useState<{
    key: string;
    receipt: ClusterConnectResponseView;
  } | null>(null);
  const installKey = `${platform}\u0000${name.trim()}`;
  const receipt = installSession?.key === installKey ? installSession.receipt : null;
  const openInstallStep = () => {
    setStep(1);
  };
  const el = {
    0: <ClusterInfoStep key="c0" providers={providers} name={name} setName={setName} platform={platform} setPlatform={setPlatform}
      onNext={openInstallStep} />,
    1: <ClusterInstallStep key="c1" platform={platform} name={name} receipt={receipt}
      resumeClusterId={resumeCluster?.clusterId}
      onReceiptChange={(nextReceipt) => setInstallSession({ key: installKey, receipt: nextReceipt })}
      onConnected={(info) => { setConnection(info); setStep(2); }} />,
    2: connection ? <ClusterDoneStep key="c2" name={name} connection={connection} onDone={() => onComplete(name)} /> : null,
  }[step];
  return (<><ShellHeader icon={Server} title={resumeCluster ? "클러스터 연결 재개" : "클러스터 연결"} sub={resumeCluster ? `${resumeCluster.name} · 기존 등록에 새 설치 명령을 발급합니다` : "에이전트를 설치하면 클러스터가 안전하게 등록·관측됩니다"} onClose={onClose} onBack={!resumeCluster && step === 1 ? () => setStep(0) : undefined} /><Steps steps={CLUSTER_STEPS} active={step} /><Body>{el}</Body></>);
}

// ── 런처 ─────────────────────────────
function Launcher({ onPick }: { onPick: (v: "repo" | "cluster") => void }) {
  const items = [
    { id: "repo" as const, icon: GitBranch, title: "Git 저장소 연결", sub: "브랜치·매니페스트 검증 후 배포 대상 등록" },
    { id: "cluster" as const, icon: Server, title: "클러스터 연결", sub: "에이전트를 설치해 클러스터를 등록·관측 (라이브)" },
  ];
  return (
    <div className="absolute inset-0 grid place-items-center px-6">
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ type: "spring", visualDuration: 0.4, bounce: 0.2 }} className="modal-surface" style={{ width: 460, maxWidth: "100%", borderRadius: 24, boxShadow: "0 30px 70px -26px rgba(0,0,0,0.35)", padding: 28 }}>
        <div className="flex items-center gap-2.5">
          <span className="grid size-8 place-items-center hdr-grad text-white" style={{ borderRadius: 10 }}><Sparkles className="size-[17px]" /></span>
          <div><h1 className="text-section font-semibold tracking-[-0.02em] c-ink">환경 연결</h1></div>
        </div>
        <p className="mt-2 text-body c-2">무엇을 연결할까요?</p>
        <div className="mt-4 inset">
          {items.map(({ id, icon: Icon, title, sub }) => (
            <button key={id} onClick={() => onPick(id)} className="inset-row">
              <span className="grid size-10 shrink-0 place-items-center bg-soft" style={{ borderRadius: 12 }}><Icon className="size-[19px] c-accent" /></span>
              <div className="min-w-0 flex-1"><div className="text-body font-semibold tracking-[-0.01em] c-ink">{title}</div><div className="mt-0.5 text-label c-3">{sub}</div></div>
              <ChevronRight className="size-[18px] shrink-0 c-3" />
            </button>
          ))}
        </div>
      </motion.div>
    </div>
  );
}

// ── 루트 ─────────────────────────────
interface ConnectWizardProps {
  embedded?: boolean;
  initialView?: null | "repo" | "cluster";
  onDismiss?: () => void;
  repositoryContext?: RepositoryConnectionContext;
  repositoryClusters?: readonly RepositoryTargetCluster[];
  resumeCluster?: ResumeClusterConnection;
  onRepositoryComplete?: (repo: string) => void;
}

export function ConnectWizard({
  embedded = false,
  initialView = null,
  onDismiss,
  repositoryContext,
  repositoryClusters,
  resumeCluster,
  onRepositoryComplete,
}: ConnectWizardProps = {}) {
  const [view, setView] = useState<null | "repo" | "cluster">(initialView);
  const providers = useClusterProviders();
  // 컨텍스트 모달 모드: 뒤로가기가 없는 단일 위저드 진입이므로 닫기는 모달을 닫는다(런처로 돌아가지 않음)
  const closeView = () => { if (onDismiss) onDismiss(); else setView(null); };

  // Esc 키로 마법사 닫기(브라우저 confirm/alert 없이 상태 토글/콜백만).
  useEffect(() => {
    if (!view) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") closeView(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view]);

  // 실제 연결 완료 시점에만 셸 알림으로 연결(관측된 결과 기반 · 타이머 흉내 없음).
  const completeCluster = (name: string) => {
    emitAction({ kind: "connect", title: "클러스터 연결됨", body: `${name} · 메트릭 수집 시작`, scope: "cluster", ref: name });
    closeView();
  };
  const completeRepo = (repo: string) => {
    emitAction({ kind: "connect", title: "저장소 연결됨", body: `${repo} · GitOps 대상 등록 완료`, scope: "repo", ref: repo });
    onRepositoryComplete?.(repo);
    closeView();
  };

  return (
    <div className={`opsia-connect ${embedded ? "absolute" : "fixed"} inset-0`}>
      <div className="absolute inset-0 overflow-hidden">
        <div className="absolute -left-28 -top-28 size-[440px] rounded-full" style={{ background: "radial-gradient(circle, rgba(10,132,255,0.13), transparent 70%)", filter: "blur(46px)" }} />
        <div className="absolute -right-20 bottom-0 size-[400px] rounded-full" style={{ background: "radial-gradient(circle, rgba(48,209,88,0.18), transparent 70%)", filter: "blur(46px)" }} />
      </div>

      {view === null && <Launcher onPick={setView} />}

      <AnimatePresence>
        {view && (
          <>
            <motion.div key="backdrop" className="absolute inset-0" style={{ background: "rgba(0,0,0,0.28)", backdropFilter: "blur(5px)" }} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={closeView} />
            {/* 바깥(배경) 클릭 시 닫기 · 모달 컨텐츠 클릭은 stopPropagation으로 전파 차단 */}
            <div className="absolute inset-0 overflow-y-auto" onClick={closeView}>
              <div className="flex min-h-full justify-center px-6" style={{ paddingTop: "6vh", paddingBottom: "6vh" }}>
                <motion.div key={view} role="dialog" aria-modal="true" aria-label={view === "repo" ? "Git 저장소 연결" : "클러스터 연결"}
                  tabIndex={-1} autoFocus onClick={(e) => e.stopPropagation()} initial={{ opacity: 0, y: 22, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 16, scale: 0.98 }} transition={{ type: "spring", visualDuration: 0.42, bounce: 0.2 }}
                  style={{ width: 580, maxWidth: "100%", maxHeight: "88vh", display: "flex", flexDirection: "column", borderRadius: 26, alignSelf: "flex-start", boxShadow: "0 44px 100px -30px rgba(0,0,0,0.4), 0 8px 24px -12px rgba(0,0,0,0.15)" }} className="modal-surface overflow-hidden">
                  {view === "repo"
                    ? <RepoWizard providers={providers} context={repositoryContext} repositoryClusters={repositoryClusters} onClose={closeView} onComplete={completeRepo} />
                    : <ClusterWizard providers={providers} resumeCluster={resumeCluster} onClose={closeView} onComplete={completeCluster} />}
                </motion.div>
              </div>
            </div>
          </>
        )}
      </AnimatePresence>

      <style>{`
        .opsia-connect {
          --surface: ${UI.card};
          --ink: ${UI.ink}; --ink-2: ${UI.ink2}; --ink-3: ${UI.ink3};
          --line: ${UI.line};
          /* 셸 팔레트와 통일: BLUE #0A84FF · HP.ok #30D158 · HP.warn #FFB340 · HP.crit #FF5F55 */
          --blue: ${BLUE}; --accent: ${BLUE}; --lime: ${HP.ok};
          --green: ${HP.ok}; --orange: ${HP.warn}; --red: ${HP.crit};
          --soft: ${blueA(0.06)}; --soft-b: ${blueA(0.32)};
          --fill: ${INSET}; --fill-2: ${UI.bg};
          font-family: var(--font-sans);
          font-weight: var(--font-weight-body);
          color: var(--ink);
        }
        .c-ink { color: var(--ink); } .c-2 { color: var(--ink-2); } .c-3 { color: var(--ink-3); }
        .c-accent { color: var(--blue); } .c-green { color: var(--green); } .c-orange { color: var(--orange); } .c-red { color: var(--red); }
        .bg-surface { background: var(--surface); } .bg-soft { background: var(--soft); }
        .bg-accent { background: var(--blue); } .bg-green { background: var(--green); } .bg-lime { background: var(--lime); }
        .green-bg { background: ${okA(0.14)}; } .orange-bg { background: ${warnA(0.16)}; } .lime-bg { background: var(--lime); }
        .err-bg { background: ${critA(0.06)}; border: 1px solid ${critA(0.18)}; }
        .err-ic-bg { background: ${critA(0.14)}; }
        .dot-r { background: var(--red); } .dot-o { background: var(--orange); } .dot-g { background: var(--green); }
        .ping-g { background: ${okA(0.5)}; }
        .toggle-off { background: ${inkA(0.14)}; }
        .hairline { height: 1px; background: var(--line); }
        .hdr-grad { background: var(--blue); }
        /* 머티리얼: 테두리 대신 부드러운 그림자로 깊이 */
        .modal-surface { background: var(--surface); }
        .notif { background: var(--surface); box-shadow: 0 14px 34px -10px rgba(17,19,24,0.24), 0 2px 8px rgba(17,19,24,0.06); }
        .field { background: var(--fill); border: 1px solid transparent; transition: background .18s, border-color .18s, box-shadow .18s; }
        .field:hover { background: var(--fill-2); }
        .field:focus-within { background: ${UI.card}; border-color: var(--soft-b); box-shadow: 0 0 0 4px rgba(10,132,255,0.12); }
        /* 트레이형 리스트: 회색 트레이 + 선택 시 흰 카드가 떠오름 */
        .inset { background: var(--fill); border-radius: 18px; padding: 6px; display: flex; flex-direction: column; gap: 4px; }
        .inset-row { display: flex; align-items: center; gap: 13px; width: 100%; text-align: left; padding: 11px 13px; border-radius: 13px; transition: background .15s, box-shadow .15s; }
        .inset-row:hover { background: rgba(17,19,24,0.035); }
        .inset-row-on, .inset-row-on:hover { background: ${UI.card}; box-shadow: 0 1px 2px rgba(17,19,24,0.06), 0 6px 16px -8px rgba(17,19,24,0.14); }
        .badge { font-size: var(--type-caption); font-weight: 600; color: var(--ink-2); background: ${UI.card}; padding: 3px 9px; border-radius: 999px; box-shadow: 0 1px 2px rgba(17,19,24,0.06); }
        .check-off { border-color: rgba(17,19,24,0.2); background: ${UI.card}; }
        .inset-row:hover .check-off { border-color: rgba(17,19,24,0.3); }
        .seg { background: var(--fill); }
        .card { background: var(--fill); border: 1px solid transparent; transition: background .16s, border-color .16s, box-shadow .16s; }
        .card:hover { background: var(--fill-2); }
        .card-on, .card-on:hover { background: ${UI.card}; border-color: var(--soft-b); box-shadow: 0 1px 2px rgba(17,19,24,0.06), 0 6px 16px -8px rgba(17,19,24,0.14); }
        .cmd { background: var(--fill); border: none; }
        .stat { display: flex; background: var(--fill); border-radius: 16px; overflow: hidden; }
        .btn-primary { background: var(--blue); color: ${UI.card}; box-shadow: 0 8px 18px -8px rgba(10,132,255,0.55); cursor: pointer; transition: background .16s, transform .12s; }
        .btn-primary:not(:disabled):hover { background: var(--action-hover); }
        .btn-primary:not(:disabled):active { transform: scale(0.99); }
        .btn-ghost { background: var(--fill); border: none; cursor: pointer; transition: background .16s, transform .12s; }
        .btn-ghost:not(:disabled):hover { background: var(--fill-2); }
        .btn-ghost:not(:disabled):active { transform: translateY(1px); }
        .opsia-connect button:focus-visible {
          outline: none;
          box-shadow: 0 0 0 3px var(--focus-ring) !important;
        }
        .opsia-connect button:disabled {
          color: var(--disabled-foreground) !important;
          background: var(--disabled-background) !important;
          border-color: var(--border) !important;
          box-shadow: none !important;
          cursor: not-allowed !important;
          opacity: 1 !important;
        }
        @media (prefers-reduced-motion: reduce) { *,*::before,*::after { animation-duration:.01ms !important; transition-duration:.01ms !important; } }
      `}</style>
    </div>
  );
}
