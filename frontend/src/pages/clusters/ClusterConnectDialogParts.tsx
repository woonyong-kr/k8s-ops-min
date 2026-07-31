import { Check, Clipboard, TriangleAlert } from "lucide-react";
import type { ClusterConnectStage } from "../../features/clusters/clustersContract";
import type { I18nController } from "../../shared/i18n";
import { Alert, AlertDescription, AlertTitle } from "../../shared/ui/primitives/alert";
import { Button } from "../../shared/ui/primitives/button";
import { Spinner } from "../../shared/ui/primitives/spinner";
import type { ConnectPhase } from "./ClusterConnectDialog";

export function ConnectionCommandStep({
  copyState,
  connectionStage,
  elapsedSeconds,
  expiresAt,
  formatDate,
  installCommand,
  installShell,
  onCopy,
  onReissue,
  onShellChange,
  phase,
  powershellInstallCommand,
  t,
}: {
  copyState: "idle" | "copied" | "failed";
  connectionStage: ClusterConnectStage;
  elapsedSeconds: number;
  expiresAt: string | null;
  formatDate: I18nController["formatDate"];
  installCommand: string | null;
  installShell: "posix" | "powershell";
  onCopy: () => void;
  onReissue: () => void;
  onShellChange: (shell: "posix" | "powershell") => void;
  phase: ConnectPhase;
  powershellInstallCommand: string | null;
  t: I18nController["t"];
}) {
  if (phase === "reissuing") {
    return (
      <div className="flex min-h-36 items-center justify-center rounded-xl border bg-card" role="status">
        <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner className="size-4" decorative />
          {t("clusters.connect.reissue.pending")}
        </span>
      </div>
    );
  }
  if (phase === "failed" || phase === "expired") {
    const expired = phase === "expired";
    return (
      <Alert variant="destructive">
        <TriangleAlert aria-hidden="true" />
        <AlertTitle>{t(expired ? "clusters.connect.expired.title" : "clusters.connect.failure.title")}</AlertTitle>
        <AlertDescription>
          <span className="block">
            {t(expired ? "clusters.connect.expired.description" : "clusters.connect.failure.description")}
          </span>
          {expired ? (
            <Button className="mt-3" onClick={onReissue} size="sm" type="button" variant="outline">
              {t("clusters.connect.reissue.action")}
            </Button>
          ) : null}
        </AlertDescription>
      </Alert>
    );
  }
  if (!installCommand || !powershellInstallCommand) return null;
  const activeCommand = installShell === "powershell"
    ? powershellInstallCommand
    : installCommand;
  return (
    <div className="grid gap-4">
      <div className="grid gap-1">
        <h3 className="font-semibold">{t("clusters.connect.command.title")}</h3>
        <p className="text-sm text-muted-foreground">{t("clusters.connect.command.description")}</p>
      </div>
      <div className="inline-flex w-fit rounded-lg border bg-muted p-1" role="tablist" aria-label={t("clusters.connect.command.shellAria")}>
        <Button
          aria-selected={installShell === "posix"}
          onClick={() => onShellChange("posix")}
          role="tab"
          size="sm"
          type="button"
          variant={installShell === "posix" ? "secondary" : "ghost"}
        >
          {t("clusters.connect.command.shellPosix")}
        </Button>
        <Button
          aria-selected={installShell === "powershell"}
          onClick={() => onShellChange("powershell")}
          role="tab"
          size="sm"
          type="button"
          variant={installShell === "powershell" ? "secondary" : "ghost"}
        >
          {t("clusters.connect.command.shellPowerShell")}
        </Button>
      </div>
      <div
        className="flex min-w-0 max-w-full items-start gap-2 overflow-hidden rounded-xl border bg-muted p-2"
        data-command-surface="true"
      >
        <div
          aria-label={t("clusters.connect.command.title")}
          className="min-w-0 flex-1 overflow-hidden rounded-lg bg-background/70"
          data-command-block="true"
          role="region"
          tabIndex={0}
        >
          <pre className="select-text whitespace-pre-wrap break-all px-3 py-2.5 text-xs leading-5 [overflow-wrap:anywhere]"><code>{activeCommand}</code></pre>
        </div>
        <Button
          aria-label={t(copyState === "copied" ? "clusters.connect.action.copied" : "clusters.connect.action.copy")}
          className="shrink-0 bg-background shadow-xs"
          onClick={onCopy}
          size="sm"
          type="button"
          variant="outline"
        >
          {copyState === "copied" ? <Check aria-hidden="true" /> : <Clipboard aria-hidden="true" />}
          <span className="hidden sm:inline">
            {t(copyState === "copied" ? "clusters.connect.action.copied" : "clusters.connect.action.copy")}
          </span>
        </Button>
      </div>
      <ConnectionProgress elapsedSeconds={elapsedSeconds} stage={connectionStage} t={t} />
      <div className="flex min-w-0 items-center gap-3">
        <span className="inline-flex items-center gap-2 text-sm text-muted-foreground" role="status">
          <Spinner className="size-4" decorative />
          {t(phase === "finishing" ? "clusters.connect.progress.finalizing" : "clusters.connect.waiting")}
        </span>
      </div>
      <p aria-live="polite" className="sr-only">
        {copyState === "copied" ? t("clusters.connect.action.copied") : ""}
      </p>
      {copyState === "failed" ? (
        <p className="text-sm text-destructive" role="alert">{t("clusters.connect.copyFailure")}</p>
      ) : null}
      {expiresAt ? (
        <p className="text-xs text-muted-foreground">
          {t("clusters.connect.expires", { time: formatDate(new Date(expiresAt)) })}
        </p>
      ) : null}
    </div>
  );
}

const progressStages = [
  ["awaiting_install", "clusters.connect.progress.command"],
  ["agent_connected", "clusters.connect.progress.agent"],
  ["snapshot_received", "clusters.connect.progress.inventory"],
  ["ready", "clusters.connect.progress.ready"],
] as const;

function ConnectionProgress({
  elapsedSeconds,
  stage,
  t,
}: {
  elapsedSeconds: number;
  stage: ClusterConnectStage;
  t: I18nController["t"];
}) {
  const effectiveStage = stage === "token_issued" ? "awaiting_install" : stage;
  const currentIndex = Math.max(0, progressStages.findIndex(([value]) => value === effectiveStage));
  const statusKey = effectiveStage === "ready"
    ? "clusters.connect.progress.finalizing"
    : effectiveStage === "agent_connected"
      ? "clusters.connect.progress.waitInventory"
      : effectiveStage === "snapshot_received"
        ? "clusters.connect.progress.prepare"
        : "clusters.connect.progress.waitAgent";
  return (
    <div className="grid min-h-28 gap-3 rounded-xl border bg-card p-3" role="status">
      <ol aria-label={t("clusters.connect.progress.aria")} className="grid grid-cols-4 gap-2">
        {progressStages.map(([value, labelKey], index) => {
          const complete = index < currentIndex || effectiveStage === "ready";
          const active = index === currentIndex && effectiveStage !== "ready";
          return (
            <li
              className="motion-live-preview grid min-w-0 justify-items-center gap-1 rounded-lg py-1 text-center"
              data-complete={complete}
              key={value}
            >
              <span className={complete
                ? "grid size-6 place-items-center rounded-full bg-status-healthy text-white transition-colors duration-(--motion-quick) ease-(--ease-out) motion-reduce:transition-none"
                : active
                  ? "grid size-6 place-items-center rounded-full bg-primary/15 text-primary transition-colors duration-(--motion-quick) ease-(--ease-out) motion-reduce:transition-none"
                  : "grid size-6 place-items-center rounded-full bg-muted text-muted-foreground transition-colors duration-(--motion-quick) ease-(--ease-out) motion-reduce:transition-none"
              }>
                {complete ? <Check aria-hidden="true" className="size-3.5" /> : active
                  ? <Spinner className="size-3.5" decorative />
                  : <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />}
              </span>
              <span className="w-full truncate text-caption text-muted-foreground" title={t(labelKey)}>
                {t(labelKey)}
              </span>
            </li>
          );
        })}
      </ol>
      <div className="flex min-w-0 items-center justify-between gap-3 text-xs text-muted-foreground">
        <span className="truncate">{t(statusKey)}</span>
        <span className="shrink-0 tabular-nums">{t("clusters.connect.progress.elapsed", { seconds: elapsedSeconds })}</span>
      </div>
      {elapsedSeconds >= 10 ? (
        <p className="text-xs text-status-warning">{t("clusters.connect.progress.slow")}</p>
      ) : null}
    </div>
  );
}
