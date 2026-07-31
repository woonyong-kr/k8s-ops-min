import { Boxes, Ellipsis, Layers3, LoaderCircle, Server, ShieldCheck, TriangleAlert, Unplug } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ClusterProviderIcon } from "../../features/cluster-scope/ClusterProviderIcon";
import type {
  HomeClusterChoice,
  HomeConnectionState,
} from "../../features/home/homeContract";
import { STAGGER_MS, useStagger } from "../../motion/useStagger";
import { captureRouteMorph } from "../../motion/useCameraMorph";
import { useI18n, type MessageKey } from "../../shared/i18n";
import { StatusMark, type StatusTone } from "../../shared/ui/StatusMark";
import { Button } from "../../shared/ui/primitives/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "../../shared/ui/primitives/card";
import { cn } from "@/shared/lib/cn";
import type { DisconnectPhase } from "./ClusterDisconnectDialog";

const connectionLabelKeys: Record<HomeConnectionState, MessageKey> = {
  online: "clusters.connection.online",
  stale: "clusters.connection.stale",
  pending: "clusters.connection.pending",
  offline: "clusters.connection.offline",
  unknown: "clusters.connection.unknown",
};

const connectionTones: Record<HomeConnectionState, StatusTone> = {
  online: "healthy",
  stale: "stale",
  pending: "warning",
  offline: "unknown",
  unknown: "unknown",
};

export function ClusterCard({
  cluster,
  disconnectPhase,
  href,
  index,
  onDisconnect,
}: {
  cluster: HomeClusterChoice;
  disconnectPhase?: DisconnectPhase;
  href: string;
  index: number;
  onDisconnect?: () => void;
}) {
  const { formatNumber, locale, t } = useI18n();
  const cardRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const delay = useStagger(index, STAGGER_MS.node);
  const disconnected = cluster.connectionState !== "online";
  const disconnectStep = disconnectProgressStep(disconnectPhase);

  useEffect(() => {
    const card = cardRef.current;
    if (!card) return;
    card.style.animationDelay = `${delay}ms`;
    return () => {
      card.style.removeProperty("animation-delay");
    };
  }, [delay]);

  useEffect(() => {
    if (!menuOpen) return;
    const closeOutside = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const closeWithKeyboard = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      menuButtonRef.current?.focus();
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeWithKeyboard);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeWithKeyboard);
    };
  }, [menuOpen]);

  return (
    <Card
      className={cn(
        "motion-node-land relative min-h-52 overflow-visible transition-[border-color,box-shadow,transform] duration-(--motion-quick) ease-(--ease-out) hover:-translate-y-0.5 hover:border-ring/50 hover:shadow-md motion-reduce:transition-none",
        disconnected && "bg-muted/30 text-muted-foreground saturate-0",
      )}
      data-cluster-id={cluster.id}
      ref={cardRef}
    >
      <Link
        aria-label={t("clusters.card.openResources", { name: cluster.name })}
        className="flex flex-1 flex-col gap-(--card-spacing) rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-focus-ring"
        onClick={() => captureRouteMorph(document)}
        to={href}
      >
        <CardHeader className={cn(
          "grid-cols-[auto_minmax(0,1fr)] items-center gap-3",
          onDisconnect && "pr-14",
        )}>
          <ClusterProviderIcon appearance="card" provider={cluster.provider} />
          <div className="min-w-0">
            <CardTitle className="truncate text-lg">{cluster.name}</CardTitle>
            <p className="mt-1 truncate text-xs text-muted-foreground">{cluster.environment}</p>
          </div>
        </CardHeader>

        <CardContent className="grid flex-1 content-between gap-4">
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-2 border-y bg-muted/25 px-3 py-2.5">
            <StatusMark
              label={t(connectionLabelKeys[cluster.connectionState])}
              tone={connectionTones[cluster.connectionState]}
            />
            {disconnected && cluster.lastObservedAt ? (
              <p className="min-w-0 truncate text-xs text-muted-foreground">
                {t("clusters.lastResponse", {
                  time: formatRelativeTime(cluster.lastObservedAt, locale),
                })}
              </p>
            ) : null}
          </div>

          <div className="grid gap-3">
            <div className="flex min-w-0 flex-wrap gap-x-4 gap-y-2 rounded-lg bg-muted/35 px-3 py-2.5 text-xs text-muted-foreground">
              {(cluster.serverCount ?? cluster.nodeCount) == null ? null : (
                <ClusterMetric icon={<Server />} label={t("clusters.metric.servers", {
                  count: formatNumber(cluster.serverCount ?? cluster.nodeCount ?? 0),
                })} />
              )}
              {cluster.podCount === null ? null : (
                <ClusterMetric icon={<Boxes />} label={t("clusters.metric.pods", {
                  count: formatNumber(cluster.podCount),
                })} />
              )}
              {cluster.appCount == null ? null : (
                <ClusterMetric icon={<Layers3 />} label={t("clusters.metric.apps", {
                  count: formatNumber(cluster.appCount),
                })} />
              )}
            </div>

            {cluster.openIncidentCount == null ? null : cluster.openIncidentCount > 0 ? (
              <ClusterMetric
                className="text-destructive"
                icon={<TriangleAlert />}
                label={t("clusters.metric.incidents", {
                  count: formatNumber(cluster.openIncidentCount),
                })}
              />
            ) : disconnected ? null : (
              <ClusterMetric
                className="text-status-healthy"
                icon={<ShieldCheck />}
                label={t("clusters.metric.healthy")}
              />
            )}

          </div>
        </CardContent>
      </Link>

      {onDisconnect ? (
        <div className="absolute top-2 right-2 z-10" ref={menuRef}>
          <Button
            aria-expanded={menuOpen}
            aria-haspopup="menu"
            aria-label={t("clusters.card.actions", { name: cluster.name })}
            onClick={() => setMenuOpen((open) => !open)}
            ref={menuButtonRef}
            size="icon-sm"
            type="button"
            variant="ghost"
          >
            <Ellipsis aria-hidden="true" />
          </Button>
          {menuOpen ? (
            <div
              aria-label={t("clusters.card.actions", { name: cluster.name })}
              className="absolute top-full right-0 mt-1 min-w-36 rounded-lg border bg-popover p-1 text-popover-foreground shadow-lg"
              role="menu"
            >
              <button
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-destructive outline-none hover:bg-destructive/10 focus-visible:bg-destructive/10"
                onClick={() => {
                  setMenuOpen(false);
                  onDisconnect();
                }}
                role="menuitem"
                type="button"
              >
                <Unplug aria-hidden="true" className="size-3.5" />
                {disconnectStep === null
                  ? t("clusters.action.disconnect")
                  : t("clusters.disconnect.resume")}
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      {disconnectStep !== null && onDisconnect ? (
        <button
          className="mx-3 mb-3 flex min-w-0 items-center gap-2 rounded-lg border border-status-warning/30 bg-status-warning/8 px-3 py-2 text-left text-xs font-medium text-warning-foreground transition-colors hover:bg-status-warning/12 focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-focus-ring motion-reduce:transition-none"
          onClick={onDisconnect}
          type="button"
        >
          <LoaderCircle aria-hidden="true" className="size-3.5 shrink-0 motion-safe:animate-spin" />
          <span className="truncate">{t("clusters.disconnect.cardProgress", { step: disconnectStep })}</span>
        </button>
      ) : null}
    </Card>
  );
}

function disconnectProgressStep(phase: DisconnectPhase | undefined): number | null {
  if (phase === "submitting") return 1;
  if (phase === "uninstalling" || phase === "cleanup-required") return 2;
  return null;
}

function ClusterMetric({
  className,
  icon,
  label,
}: {
  className?: string;
  icon: ReactNode;
  label: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs", className)}>
      <span aria-hidden="true" className="[&_svg]:size-3.5">{icon}</span>
      {label}
    </span>
  );
}

function formatRelativeTime(value: string, locale: "en" | "ko"): string {
  const deltaMilliseconds = new Date(value).getTime() - Date.now();
  const absolute = Math.abs(deltaMilliseconds);
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  const [amount, unit] = absolute < hour
    ? [Math.round(deltaMilliseconds / minute), "minute" as const]
    : absolute < day
      ? [Math.round(deltaMilliseconds / hour), "hour" as const]
      : [Math.round(deltaMilliseconds / day), "day" as const];
  const nonZeroAmount = amount === 0 ? -1 : amount;
  return new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(nonZeroAmount, unit);
}
