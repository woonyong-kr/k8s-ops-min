import {
  Check,
  CircleCheck,
  TriangleAlert,
  Unplug,
  X,
} from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties, type FormEvent } from "react";

import { useAuthSessionGate } from "../../features/auth/AuthSessionGate";
import {
  ClustersPortFailure,
  type ClusterDisconnectPort,
  type ClusterDisconnectReceipt,
} from "../../features/clusters/clustersContract";
import type { HomeClusterChoice } from "../../features/home/homeContract";
import { useI18n } from "../../shared/i18n";
import type { TranslationFunction } from "../../shared/i18n/types";
import { Alert, AlertDescription, AlertTitle } from "../../shared/ui/primitives/alert";
import { Button } from "../../shared/ui/primitives/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from "../../shared/ui/primitives/dialog";
import { Input } from "../../shared/ui/primitives/input";
import { Label } from "../../shared/ui/primitives/label";
import { Spinner } from "../../shared/ui/primitives/spinner";

export type DisconnectPhase =
  | "confirm"
  | "submitting"
  | "uninstalling"
  | "cleanup-required"
  | "succeeded"
  | "failed";

// 클러스터 연결 위저드(라이트 셸)에서 열리는 다이얼로그가 OS 다크 테마(.dark)의
// 토큰을 상속해 톤이 어긋나지 않도록, 이 서브트리는 라이트 토큰으로 고정한다.
// 기본값은 styles/tokens.css 의 :root 라이트 정의이며, primary/ring/radius 는
// 연결 위저드의 파란 포인트·둥근 모서리 톤에 맞춘다.
const LIGHT_SURFACE_TOKENS = {
  colorScheme: "light",
  "--background": "oklch(1 0 0)",
  "--foreground": "oklch(0.145 0 0)",
  "--card": "oklch(1 0 0)",
  "--card-foreground": "oklch(0.145 0 0)",
  "--popover": "oklch(1 0 0)",
  "--popover-foreground": "oklch(0.145 0 0)",
  "--primary": "#0a84ff",
  "--primary-foreground": "#ffffff",
  "--secondary": "oklch(0.97 0 0)",
  "--secondary-foreground": "oklch(0.205 0 0)",
  "--muted": "oklch(0.97 0 0)",
  "--muted-foreground": "oklch(0.556 0 0)",
  "--accent": "oklch(0.97 0 0)",
  "--accent-foreground": "oklch(0.205 0 0)",
  "--destructive": "oklch(0.577 0.245 27.325)",
  "--border": "oklch(0.922 0 0)",
  "--input": "oklch(0.922 0 0)",
  "--ring": "rgba(10, 132, 255, 0.45)",
  "--radius": "0.875rem",
} as CSSProperties;

const COMMAND_POLL_MS = 1_000;
// Agent removal includes Kubernetes API propagation and the final cleanup
// evidence callback. Eight seconds made a healthy uninstall look stalled in
// the demo; keep polling for a realistic bounded minute before backgrounding.
const COMMAND_ACK_TIMEOUT_MS = 60_000;

export function ClusterDisconnectDialog({
  cluster,
  onDisconnected,
  onOpenChange,
  onPhaseChange,
  open,
  port,
}: {
  cluster: HomeClusterChoice | null;
  onDisconnected: (clusterId: string) => void;
  onOpenChange: (open: boolean) => void;
  onPhaseChange?: (clusterId: string, phase: DisconnectPhase) => void;
  open: boolean;
  port: ClusterDisconnectPort;
}) {
  const { reportUnauthorized } = useAuthSessionGate();
  const { t } = useI18n();
  const abort = useRef<AbortController | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [phase, setPhase] = useState<DisconnectPhase>("confirm");
  const [receipt, setReceipt] = useState<ClusterDisconnectReceipt | null>(null);

  useEffect(() => () => abort.current?.abort(), []);

  useEffect(() => {
    if (cluster) onPhaseChange?.(cluster.id, phase);
  }, [cluster, onPhaseChange, phase]);

  if (cluster === null) return null;
  const confirmed = confirmation === cluster.name;
  const pending = phase === "submitting" || phase === "uninstalling";
  const terminal = phase === "succeeded";

  const changeOpen = (nextOpen: boolean) => {
    if (!nextOpen && (pending || phase === "cleanup-required")) {
      onOpenChange(false);
      return;
    }
    if (!nextOpen) {
      abort.current?.abort();
      abort.current = null;
      setConfirmation("");
      setPhase("confirm");
      setReceipt(null);
    }
    onOpenChange(nextOpen);
  };

  const finishDisconnect = (nextPhase: DisconnectPhase) => {
    setPhase(nextPhase);
    if (nextPhase === "succeeded") onDisconnected(cluster.id);
  };

  const followCommand = async (
    commandId: string,
    uninstallCommand: string | null,
    controller: AbortController,
  ): Promise<void> => {
    setPhase("uninstalling");
    const deadline = Date.now() + COMMAND_ACK_TIMEOUT_MS;
    while (!controller.signal.aborted) {
      const progress = await port.loadDisconnect(commandId, controller.signal);
      if (progress.status === "completed" && progress.cleanupCompleted) {
        const cleanupVerified = progress.residualResources.length === 0;
        setReceipt((current) => current === null ? current : {
          ...current,
          stage: cleanupVerified ? "registration_revoked" : current.stage,
          cleanupVerified,
          cleanupResources: progress.cleanupResources,
          residualResources: progress.residualResources,
        });
        if (cleanupVerified) finishDisconnect("succeeded");
        else setPhase("cleanup-required");
        return;
      }
      if (progress.status === "completed" || progress.status === "failed") {
        setPhase(uninstallCommand ? "cleanup-required" : "failed");
        return;
      }
      if (Date.now() >= deadline) {
        setPhase(uninstallCommand ? "cleanup-required" : "failed");
        return;
      }
      await wait(COMMAND_POLL_MS, controller.signal);
    }
  };

  const applyReceipt = async (
    nextReceipt: ClusterDisconnectReceipt,
    controller: AbortController,
  ) => {
    setReceipt(nextReceipt);
    if (nextReceipt.status === "disconnected") {
      finishDisconnect("succeeded");
      return;
    }
    if (nextReceipt.status === "cleanup-required" || nextReceipt.commandId === null) {
      setPhase("cleanup-required");
      return;
    }
    await followCommand(nextReceipt.commandId, nextReceipt.uninstallCommand, controller);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!confirmed || pending) return;
    const controller = new AbortController();
    abort.current?.abort();
    abort.current = controller;
    setPhase("submitting");
    try {
      await applyReceipt(await port.disconnect(cluster.id, controller.signal), controller);
    } catch (error) {
      if (isAbortError(error)) return;
      handleFailure(error, reportUnauthorized);
      setPhase("failed");
    } finally {
      if (abort.current === controller) abort.current = null;
    }
  };

  return (
    <Dialog
      onOpenChange={changeOpen}
      open={open}
    >
      <DialogContent
        className="gap-0 overflow-hidden rounded-[26px] p-0 sm:max-w-[580px]"
        closeLabel={t("common.action.close")}
        showCloseButton={false}
        style={LIGHT_SURFACE_TOKENS}
      >
        <form className="min-w-0" onSubmit={(event) => void submit(event)}>
          {/* 연결 위저드 ShellHeader와 동일 골격 — 헤더(30/36/24) → 헤어라인(좌우 36)
              → 본문(28/36/34). 48px 아이콘 슬롯·19px 제목·13px 부제·원형 X까지
              같은 수치, 해제는 파괴적 동작이라 아이콘만 빨강 계열. */}
          <div className="flex items-center gap-4" style={{ padding: "30px 36px 24px" }}>
            <span
              aria-hidden="true"
              className="grid size-12 shrink-0 place-items-center text-white"
              style={{ borderRadius: 15, background: "linear-gradient(135deg, #ff5f57, #e0362c)", boxShadow: "0 8px 18px -6px rgba(224, 54, 44, 0.45)" }}
            >
              <Unplug className="size-[22px]" />
            </span>
            <div className="min-w-0 flex-1">
              <DialogTitle className="text-section font-semibold tracking-[-0.02em]">{phaseTitle(phase, t)}</DialogTitle>
              <DialogDescription className="mt-1 text-body">
                {phase === "confirm"
                  ? t("clusters.disconnect.description", { name: cluster.name })
                  : t("clusters.disconnect.progress.description", { name: cluster.name })}
              </DialogDescription>
            </div>
            {!terminal ? (
              <button
                aria-label={t("common.action.close")}
                className="grid size-9 shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted"
                onClick={() => changeOpen(false)}
                style={{ marginTop: -4 }}
                type="button"
              >
                <X className="size-5" />
              </button>
            ) : null}
          </div>
          <div style={{ padding: "0 36px" }}><div className="h-px w-full bg-border" /></div>
          <div className="grid min-w-0 gap-5" style={{ padding: "28px 36px 34px" }}>
          {phase === "confirm" || phase === "failed" ? (
            <div className="grid gap-2">
              <Label htmlFor="cluster-disconnect-confirmation">
                {t("clusters.disconnect.confirm.label")}
              </Label>
              <Input
                autoComplete="off"
                autoFocus
                className="h-auto rounded-[14px] px-4 py-3.5 font-mono text-body"
                disabled={pending}
                id="cluster-disconnect-confirmation"
                onChange={(event) => setConfirmation(event.currentTarget.value)}
                spellCheck={false}
                value={confirmation}
              />
              <p className="text-xs text-muted-foreground">
                {t("clusters.disconnect.confirm.hint", { name: cluster.name })}
              </p>
            </div>
          ) : null}

          {pending ? <DisconnectProgress phase={phase} t={t} /> : null}

          {receipt ? <DisconnectEvidence phase={phase} receipt={receipt} t={t} /> : null}

          {phase === "cleanup-required" ? (
            <CleanupPending
              description={t("clusters.disconnect.manual.description")}
              residualResources={receipt?.residualResources ?? []}
              resourcesLabel={(count, resources) => t("clusters.disconnect.manual.resources", {
                count,
                resources,
              })}
              title={t("clusters.disconnect.manual.title")}
            />
          ) : null}

          {phase === "succeeded" ? (
            <Alert>
              <CircleCheck aria-hidden="true" />
              <AlertDescription>{t("clusters.disconnect.success.description")}</AlertDescription>
            </Alert>
          ) : null}

          {phase === "failed" ? (
            <Alert variant="destructive">
              <TriangleAlert aria-hidden="true" />
              <AlertTitle>{t("clusters.disconnect.failure.title")}</AlertTitle>
              <AlertDescription>{t("clusters.disconnect.failure.description")}</AlertDescription>
            </Alert>
          ) : null}

          {/* 위저드에는 푸터 밴드가 없다 — 기본 회색 배경·border·음수 마진을 제거해
              본문과 같은 면 위에서 행동 라인만 남긴다. */}
          <DialogFooter className="mx-0 mb-0 rounded-none border-0 bg-transparent p-0">
            {terminal ? (
              <Button onClick={() => changeOpen(false)} type="button">
                {t("common.action.close")}
              </Button>
            ) : phase === "cleanup-required" ? (
              <Button onClick={() => changeOpen(false)} type="button" variant="outline">
                {t("clusters.disconnect.background")}
              </Button>
            ) : pending ? (
              <div className="flex w-full items-center justify-between gap-3">
                <p className="inline-flex min-w-0 items-center gap-2 text-sm text-muted-foreground" role="status">
                  <Spinner className="size-4 shrink-0" decorative />
                  <span className="truncate">{phase === "submitting"
                    ? t("clusters.disconnect.submitting")
                    : t("clusters.disconnect.uninstalling")}</span>
                </p>
                <Button onClick={() => changeOpen(false)} type="button" variant="outline">
                  {t("clusters.disconnect.background")}
                </Button>
              </div>
            ) : (
              /* 위저드 하단 행동 라인과 동일한 [보조 ⅓ · 주행동 ⅔] 구성 —
                 취소는 위저드의 '뒤로'와 같은 회색 채움, 주행동은 파괴적이라 빨강. */
              <div className="flex w-full items-stretch gap-3">
                <Button
                  className="h-auto flex-1 rounded-[14px] bg-secondary py-3.5 text-body font-semibold text-secondary-foreground shadow-none hover:bg-muted"
                  onClick={() => changeOpen(false)}
                  type="button"
                  variant="ghost"
                >
                  {t("common.action.cancel")}
                </Button>
                <Button
                  className="h-auto flex-[2] rounded-[14px] py-3.5 text-section font-semibold"
                  disabled={!confirmed}
                  type="submit"
                  variant="destructive"
                >
                  <Unplug aria-hidden="true" />
                  {t("clusters.action.disconnect")}
                </Button>
              </div>
            )}
          </DialogFooter>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DisconnectProgress({ phase, t }: { phase: DisconnectPhase; t: TranslationFunction }) {
  const steps = [
    {
      label: t("clusters.disconnect.progress.request"),
      state: phase === "submitting" ? "active" : "complete",
    },
    {
      label: t("clusters.disconnect.progress.agent"),
      state: phase === "uninstalling" ? "active" : "pending",
    },
    { label: t("clusters.disconnect.progress.registration"), state: "pending" },
  ];
  return (
    <ol className="grid gap-2 rounded-xl border bg-muted/30 p-3" aria-label="클러스터 연결 해제 진행">
      {steps.map((step) => (
        <li className="flex min-w-0 items-center gap-2 text-sm" key={step.label}>
          {step.state === "complete" ? (
            <Check aria-hidden="true" className="size-4 shrink-0 text-emerald-600" />
          ) : step.state === "active" ? (
            <Spinner className="size-4 shrink-0" decorative />
          ) : (
            <span aria-hidden="true" className="size-4 shrink-0 rounded-full border" />
          )}
          <span className="truncate" data-step-state={step.state}>{step.label}</span>
        </li>
      ))}
    </ol>
  );
}

function DisconnectEvidence({
  phase,
  receipt,
  t,
}: {
  phase: DisconnectPhase;
  receipt: ClusterDisconnectReceipt;
  t: TranslationFunction;
}) {
  const cleanupFinished = receipt.cleanupVerified || phase === "succeeded";
  const registrationRevoked = receipt.stage === "registration_revoked" || cleanupFinished;
  const noInstalledAgentResources = receipt.status === "disconnected"
    && receipt.commandId === null
    && receipt.cleanupResources.length === 0;
  const rows = [
    {
      label: t("clusters.disconnect.evidence.cleanup.label"),
      value: noInstalledAgentResources
        ? t("clusters.disconnect.evidence.cleanup.none")
        : cleanupFinished
        ? t("clusters.disconnect.evidence.cleanup.complete")
        : receipt.stage === "agent_cleanup_queued"
          ? t("clusters.disconnect.evidence.cleanup.queued")
          : t("clusters.disconnect.evidence.pending"),
      complete: cleanupFinished || noInstalledAgentResources,
    },
    {
      label: t("clusters.disconnect.evidence.credentials.label"),
      value: registrationRevoked
        ? t("clusters.disconnect.evidence.credentials.revoked")
        : t("clusters.disconnect.evidence.pending"),
      complete: registrationRevoked,
    },
    {
      label: t("clusters.disconnect.evidence.registration.label"),
      value: registrationRevoked
        ? t("clusters.disconnect.evidence.registration.revoked")
        : t("clusters.disconnect.evidence.pending"),
      complete: registrationRevoked,
    },
    {
      label: t("clusters.disconnect.evidence.residual.label"),
      value: t("clusters.disconnect.evidence.residual.count", {
        count: receipt.residualResources.length,
      }),
      complete: cleanupFinished && receipt.residualResources.length === 0,
    },
  ];

  return (
    <section
      className="grid gap-2 rounded-xl border bg-muted/20 p-3"
      aria-label={t("clusters.disconnect.evidence.aria")}
    >
      {rows.map((row) => (
        <div className="flex min-w-0 items-center gap-2 text-xs" key={row.label}>
          {row.complete ? (
            <Check aria-hidden="true" className="size-4 shrink-0 text-emerald-600" />
          ) : (
            <span aria-hidden="true" className="size-4 shrink-0 rounded-full border" />
          )}
          <span className="min-w-0 flex-1 truncate text-muted-foreground">{row.label}</span>
          <span className="shrink-0 font-medium">{row.value}</span>
        </div>
      ))}
      {receipt.cleanupResources.length > 0 ? (
        <p className="break-words text-xs text-muted-foreground">
          {t("clusters.disconnect.evidence.targets", {
            count: receipt.cleanupResources.length,
            resources: receipt.cleanupResources.join(", "),
          })}
        </p>
      ) : null}
    </section>
  );
}

function CleanupPending({
  description,
  residualResources,
  resourcesLabel,
  title,
}: {
  description: string;
  residualResources: string[];
  resourcesLabel: (count: number, resources: string) => string;
  title: string;
}) {
  return (
    <section className="grid min-w-0 gap-3 rounded-xl border border-amber-500/35 bg-amber-500/5 p-4">
      <div className="flex items-start gap-2">
        <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-amber-600" />
        <div className="min-w-0">
          <h3 className="font-medium">{title}</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            {description}
          </p>
        </div>
      </div>
      {residualResources.length > 0 ? (
        <p className="break-words text-xs text-muted-foreground">
          {resourcesLabel(residualResources.length, residualResources.join(", "))}
        </p>
      ) : null}
    </section>
  );
}

function phaseTitle(
  phase: DisconnectPhase,
  t: TranslationFunction,
): string {
  if (phase === "cleanup-required") return t("clusters.disconnect.manual.heading");
  if (phase === "succeeded") return t("clusters.disconnect.success.title");
  return t("clusters.disconnect.title");
}

function handleFailure(error: unknown, reportUnauthorized: () => void): void {
  if (error instanceof ClustersPortFailure && error.code === "unauthorized") reportUnauthorized();
}

function wait(durationMs: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(resolve, durationMs);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timeout);
      reject(new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error && error.name === "AbortError";
}
