import { Check } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAuthSessionGate } from "../../features/auth/AuthSessionGate";
import {
  ClustersPortFailure,
  type ClusterConnectReceipt,
  type ClusterConnectStage,
  type ClustersPort,
} from "../../features/clusters/clustersContract";
import { useUnifiedFilter } from "../../features/filters/UnifiedFilterProvider";
import { useI18n } from "../../shared/i18n";
import { Button, buttonVariants } from "../../shared/ui/primitives/button";
import { cn } from "@/shared/lib/cn";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../shared/ui/primitives/dialog";
import { Input } from "../../shared/ui/primitives/input";
import { Spinner } from "../../shared/ui/primitives/spinner";
import { ConnectionCommandStep } from "./ClusterConnectDialogParts";
import { clusterResourcesHref } from "./clusterNavigation";

const POLL_INTERVAL_MS = 2_000;
type WizardStep = 1 | 2 | 3;
export type ConnectPhase =
  | "idle"
  | "submitting"
  | "waiting"
  | "reissuing"
  | "finishing"
  | "connected"
  | "expired"
  | "failed";

const STEP_MOTION = "motion-wizard-stage";

export function ClusterConnectDialog({
  existingNames,
  onConnected,
  onOpenChange,
  open,
  port,
}: {
  existingNames: readonly string[];
  onConnected: () => void;
  onOpenChange: (open: boolean) => void;
  open: boolean;
  port: ClustersPort;
}) {
  const { reportUnauthorized } = useAuthSessionGate();
  const filter = useUnifiedFilter();
  const { formatDate, t } = useI18n();
  const [step, setStep] = useState<WizardStep>(1);
  const [name, setName] = useState("");
  const [installShell, setInstallShell] = useState<"posix" | "powershell">(detectInstallShell);
  const [phase, setPhase] = useState<ConnectPhase>("idle");
  const [receipt, setReceipt] = useState<ClusterConnectReceipt | null>(null);
  const [connectionStage, setConnectionStage] = useState<ClusterConnectStage>("awaiting_install");
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const [serverNameConflict, setServerNameConflict] = useState(false);
  const connectAbort = useRef<AbortController | null>(null);
  const waitingStartedAt = useRef<number | null>(null);
  const normalizedName = normalizeDisplayName(name);
  const duplicateName = normalizedName.length > 0 && existingNames.some(
    (existingName) => normalizeDisplayName(existingName) === normalizedName,
  );
  const nameConflict = duplicateName || serverNameConflict;

  useEffect(() => {
    if (!open || step !== 2 || !receipt || (phase !== "waiting" && phase !== "finishing")) return;
    const controller = new AbortController();
    let active = true;
    let timeout: number | undefined;
    const poll = async () => {
      try {
        const connection = await port.loadConnection(receipt.clusterId, controller.signal);
        if (!active) return;
        setConnectionStage(connection.stage);
        if (connection.status === "connected") {
          if (phase === "finishing") {
            setPhase("connected");
            setStep(3);
            onConnected();
            return;
          }
          setPhase("finishing");
        } else if (connection.status === "expired") {
          setPhase("expired");
        } else if (connection.stage === "error") {
          setPhase("failed");
        } else if (phase === "finishing") {
          setPhase("waiting");
        }
      } catch (error) {
        if (!active || isAbortError(error)) return;
        if (error instanceof ClustersPortFailure && error.code === "unauthorized") {
          reportUnauthorized();
          return;
        }
        setPhase("failed");
      } finally {
        if (active && phase === "waiting") {
          timeout = window.setTimeout(() => void poll(), POLL_INTERVAL_MS);
        }
      }
    };
    if (phase === "waiting") {
      void poll();
    } else {
      timeout = window.setTimeout(() => void poll(), POLL_INTERVAL_MS);
    }
    return () => {
      active = false;
      controller.abort();
      if (timeout !== undefined) window.clearTimeout(timeout);
    };
  }, [onConnected, open, phase, port, receipt, reportUnauthorized, step]);

  useEffect(() => {
    if (!open || step !== 2 || phase !== "waiting") return;
    waitingStartedAt.current ??= Date.now();
    const updateElapsed = () => {
      const startedAt = waitingStartedAt.current;
      if (startedAt === null) return;
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1_000));
    };
    updateElapsed();
    const interval = window.setInterval(updateElapsed, 1_000);
    return () => window.clearInterval(interval);
  }, [open, phase, step]);

  const changeOpen = (nextOpen: boolean) => {
    if (!nextOpen && (phase === "submitting" || phase === "reissuing")) return;
    if (!nextOpen) {
      connectAbort.current?.abort();
      connectAbort.current = null;
      if (step === 3) reset();
    }
    onOpenChange(nextOpen);
  };
  const reset = () => {
    setStep(1);
    setName("");
    setInstallShell(detectInstallShell());
    setPhase("idle");
    setReceipt(null);
    setConnectionStage("awaiting_install");
    setElapsedSeconds(0);
    waitingStartedAt.current = null;
    setCopyState("idle");
    setServerNameConflict(false);
  };
  const register = async () => {
    if (!name.trim() || nameConflict || phase === "submitting") return;
    const controller = new AbortController();
    connectAbort.current = controller;
    setElapsedSeconds(0);
    setServerNameConflict(false);
    setPhase("submitting");
    try {
      const nextReceipt = await port.connect({ name: name.trim() }, controller.signal);
      setReceipt(nextReceipt);
      setConnectionStage("awaiting_install");
      waitingStartedAt.current = Date.now();
      setPhase("waiting");
      setStep(2);
    } catch (error) {
      if (isAbortError(error)) return;
      if (error instanceof ClustersPortFailure && error.code === "unauthorized") {
        reportUnauthorized();
        return;
      }
      if (error instanceof ClustersPortFailure && error.code === "conflict") {
        setServerNameConflict(true);
        setPhase("idle");
        setStep(1);
        return;
      }
      setPhase("failed");
      setStep(2);
    } finally {
      if (connectAbort.current === controller) connectAbort.current = null;
    }
  };
  const reissue = async () => {
    if (!receipt || phase === "reissuing") return;
    const controller = new AbortController();
    connectAbort.current?.abort();
    connectAbort.current = controller;
    setPhase("reissuing");
    setCopyState("idle");
    try {
      const nextReceipt = await port.reissue(receipt.clusterId, controller.signal);
      if (controller.signal.aborted) return;
      setReceipt(nextReceipt);
      setConnectionStage("awaiting_install");
      setElapsedSeconds(0);
      waitingStartedAt.current = Date.now();
      setPhase("waiting");
    } catch (error) {
      if (isAbortError(error)) return;
      if (error instanceof ClustersPortFailure && error.code === "unauthorized") {
        reportUnauthorized();
        return;
      }
      setPhase("failed");
    } finally {
      if (connectAbort.current === controller) connectAbort.current = null;
    }
  };
  const copyCommand = async () => {
    if (!receipt) return;
    // Clipboard writes do not need a loading state. Acknowledge the click immediately,
    // then surface the uncommon permission failure if the browser rejects the write.
    setCopyState("copied");
    try {
      await navigator.clipboard.writeText(
        installShell === "powershell"
          ? receipt.powershellInstallCommand
          : receipt.installCommand,
      );
    } catch {
      setCopyState("failed");
    }
  };

  return (
    <Dialog onOpenChange={changeOpen} open={open}>
      <DialogContent
        className="sm:max-w-2xl"
        closeLabel={t("common.action.close")}
        showCloseButton={phase !== "submitting" && phase !== "reissuing"}
      >
        <DialogHeader>
          <p className="text-xs font-medium text-muted-foreground">
            {t("clusters.connect.step", { current: step, total: 3 })}
          </p>
          <DialogTitle>{t("clusters.connect.title")}</DialogTitle>
          <DialogDescription>{t("clusters.connect.description")}</DialogDescription>
        </DialogHeader>

        {step === 1 ? (
          <div className={cn("grid gap-5", STEP_MOTION)}>
            <label className="grid gap-2 text-sm font-medium">
              {t("clusters.connect.name.label")}
              <Input
                aria-describedby={nameConflict ? "cluster-connect-name-error" : undefined}
                aria-invalid={nameConflict || undefined}
                autoFocus
                onChange={(event) => {
                  setName(event.currentTarget.value);
                  setServerNameConflict(false);
                }}
                placeholder={t("clusters.connect.name.placeholder")}
                value={name}
              />
              {nameConflict ? (
                <span className="text-xs text-destructive" id="cluster-connect-name-error" role="alert">
                  {t("clusters.connect.name.conflict")}
                </span>
              ) : null}
            </label>
            <DialogFooter className="mt-1">
              <Button
                aria-busy={phase === "submitting"}
                disabled={!name.trim() || nameConflict || phase === "submitting"}
                onClick={() => void register()}
              >
                {phase === "submitting" ? <Spinner decorative /> : null}
                {t("clusters.connect.action.register")}
              </Button>
            </DialogFooter>
          </div>
        ) : null}

        {step === 2 ? (
          <div className={STEP_MOTION}>
            <ConnectionCommandStep
              copyState={copyState}
              connectionStage={connectionStage}
              elapsedSeconds={elapsedSeconds}
              expiresAt={receipt?.expiresAt ?? null}
              formatDate={formatDate}
              installCommand={receipt?.installCommand ?? null}
              installShell={installShell}
              onCopy={() => void copyCommand()}
              onReissue={() => void reissue()}
              onShellChange={setInstallShell}
              phase={phase}
              powershellInstallCommand={receipt?.powershellInstallCommand ?? null}
              t={t}
            />
          </div>
        ) : null}

        {step === 3 && receipt ? (
          <div className={cn("grid justify-items-center gap-4 py-6 text-center", STEP_MOTION)}>
            <span className="grid size-12 place-items-center rounded-full bg-status-healthy/15 text-status-healthy">
              <Check aria-hidden="true" className="size-6" />
            </span>
            <div className="grid gap-1">
              <h3 className="text-lg font-semibold">{t("clusters.connect.connected.title")}</h3>
              <p className="text-sm text-muted-foreground">{t("clusters.connect.connected.description")}</p>
            </div>
            <Link
              className={buttonVariants()}
              to={clusterResourcesHref(filter.state, receipt.clusterId)}
            >
              {t("clusters.connect.action.view")}
            </Link>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function normalizeDisplayName(value: string): string {
  return value.trim().toLocaleLowerCase();
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error &&
    error.name === "AbortError";
}

function detectInstallShell(): "posix" | "powershell" {
  if (typeof navigator === "undefined") return "posix";
  const platform = `${navigator.platform ?? ""} ${navigator.userAgent ?? ""}`.toLowerCase();
  return platform.includes("win") ? "powershell" : "posix";
}
