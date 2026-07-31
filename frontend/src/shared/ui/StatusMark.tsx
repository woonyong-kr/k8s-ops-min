import { cn } from "@/shared/lib/cn";
import { useI18n, type MessageKey } from "../i18n";

export type StatusTone = "healthy" | "warning" | "critical" | "stale" | "unknown";

const defaultStatusLabelKeys: Record<StatusTone, MessageKey> = {
  healthy: "status.tone.healthy",
  warning: "status.tone.warning",
  critical: "status.tone.critical",
  stale: "status.tone.stale",
  unknown: "status.tone.unknown",
};

export interface StatusMarkProps {
  label?: string;
  labelMode?: "visible" | "sr-only";
  live?: boolean;
  tone: StatusTone;
}

export function StatusMark({
  tone,
  label,
  labelMode = "visible",
  live = false,
}: StatusMarkProps) {
  const { t } = useI18n();
  const visibleLabel = label?.trim() || t(defaultStatusLabelKeys[tone]);

  return (
    <span
      aria-atomic={live || undefined}
      aria-live={live ? "polite" : undefined}
      className="inline-flex min-w-0 items-center gap-2 text-xs font-medium text-muted-foreground"
      data-slot="status-mark"
      data-status={tone}
      role={live ? "status" : undefined}
    >
      <span
        aria-hidden="true"
        className={cn(
          "size-2 rounded-full bg-status-unknown forced-colors:border forced-colors:border-current forced-colors:bg-transparent",
          tone === "healthy" && "bg-status-healthy",
          tone === "warning" && "bg-status-warning",
          tone === "critical" && "bg-destructive",
          tone === "stale" && "bg-status-stale",
        )}
      />
      <span className={cn("truncate", labelMode === "sr-only" && "sr-only")}>
        {visibleLabel}
      </span>
    </span>
  );
}
