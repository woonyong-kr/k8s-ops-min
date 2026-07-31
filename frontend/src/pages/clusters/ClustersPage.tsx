import { CircleAlert, Plus, RefreshCw } from "lucide-react";
import { useState } from "react";
import { useClusterScope } from "../../features/cluster-scope/ClusterScopeProvider";
import { useOptionalProductSession } from "../../features/auth/ProductSessionContext";
import type {
  ClusterDisconnectPort,
  ClustersPort,
} from "../../features/clusters/clustersContract";
import {
  activeClusterChoices,
  canOfferClusterDisconnect,
  refreshAfterClusterDisconnect,
} from "../../features/clusters/clusterDisconnectPolicy";
import type { HomeClusterChoice } from "../../features/home/homeContract";
import { useUnifiedFilter } from "../../features/filters/UnifiedFilterProvider";
import { useI18n } from "../../shared/i18n";
import { ProductPageFrame } from "../../shared/ui/ProductPageFrame";
import { ProductStateScreen } from "../../shared/ui/ProductStateScreen";
import { Alert, AlertDescription } from "../../shared/ui/primitives/alert";
import { Button } from "../../shared/ui/primitives/button";
import { ClusterCard } from "./ClusterCard";
import { ClusterConnectDialog } from "./ClusterConnectDialog";
import {
  ClusterDisconnectDialog,
  type DisconnectPhase,
} from "./ClusterDisconnectDialog";
import { clusterResourcesHref } from "./clusterNavigation";

export function ClustersPage({ port }: { port: ClustersPort & ClusterDisconnectPort }) {
  const { formatNumber, t } = useI18n();
  const filter = useUnifiedFilter();
  const scope = useClusterScope();
  const session = useOptionalProductSession();
  const [connectOpen, setConnectOpen] = useState(false);
  const [disconnectCluster, setDisconnectCluster] = useState<HomeClusterChoice | null>(null);
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const [disconnectPhase, setDisconnectPhase] = useState<DisconnectPhase>("confirm");
  const canManageClusters = session?.roles.includes("service_admin") ?? false;
  const clusters = scope.collection.phase === "ready"
    ? activeClusterChoices(scope.collection.data.clusters)
    : [];

  if (scope.collection.phase === "loading" || scope.collection.phase === "idle") {
    return <ProductStateScreen kind="loading" placement="content" />;
  }
  if (scope.collection.phase === "failed") {
    if (scope.collection.failure.code === "forbidden") {
      return <ProductStateScreen issue={{ code: "forbidden" }} kind="forbidden" placement="content" />;
    }
    if (scope.collection.failure.code === "offline") {
      return (
        <ProductStateScreen
          issue={{ code: "network" }}
          kind="offline"
          placement="content"
          retry={{ onRetry: scope.refresh, pending: false }}
        />
      );
    }
    return (
      <ProductStateScreen
        issue={{ code: "server" }}
        kind="error"
        placement="content"
        retry={{ onRetry: scope.refresh, pending: false }}
      />
    );
  }
  return (
    <ProductPageFrame className="gap-6">
      <header className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,28rem)] lg:items-end">
        <div className="min-w-0">
          <h2 className="text-2xl font-semibold tracking-tight">{t("clusters.title")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("clusters.description")}</p>
        </div>
        <div className="flex min-w-0 items-center gap-2">
          <div className="min-w-0 flex-1" />
          {canManageClusters ? (
            <Button
              aria-label={t("clusters.action.add")}
              onClick={() => setConnectOpen(true)}
              type="button"
            >
              <Plus aria-hidden="true" />
              <span className="hidden sm:inline">{t("clusters.action.add")}</span>
            </Button>
          ) : null}
          <Button
            aria-label={t("common.action.refresh")}
            disabled={scope.collection.refreshing}
            onClick={scope.refresh}
            size="icon"
            type="button"
            variant="outline"
          >
            <RefreshCw aria-hidden="true" className={scope.collection.refreshing ? "motion-safe:animate-spin" : undefined} />
          </Button>
        </div>
      </header>

      {scope.collection.refreshFailure ? (
        <Alert>
          <CircleAlert aria-hidden="true" />
          <AlertDescription>{t("clusters.refresh.failed")}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>{t("clusters.list.shown", { count: formatNumber(clusters.length) })}</span>
        <span aria-hidden="true">·</span>
        <span>{t("clusters.list.totalUnknown")}</span>
      </div>

      {clusters.length === 0 ? (
        <div className="grid min-h-52 place-items-center rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
          {t("clusters.list.empty")}
        </div>
      ) : (
        <section
          aria-label={t("clusters.list.aria")}
          className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3"
        >
          {clusters.map((cluster, index) => (
            <ClusterCard
              cluster={cluster}
              href={clusterResourcesHref(filter.state, cluster.id)}
              index={index}
              key={cluster.id}
              disconnectPhase={disconnectCluster?.id === cluster.id ? disconnectPhase : undefined}
              onDisconnect={canOfferClusterDisconnect(session?.roles, cluster)
                ? () => {
                    setDisconnectCluster(cluster);
                    setDisconnectOpen(true);
                  }
                : undefined}
            />
          ))}
        </section>
      )}

      {canManageClusters ? (
        <>
          <ClusterConnectDialog
            existingNames={clusters.map((cluster) => cluster.name)}
            onConnected={scope.refresh}
            onOpenChange={setConnectOpen}
            open={connectOpen}
            port={port}
          />
          <ClusterDisconnectDialog
            cluster={disconnectCluster}
            key={disconnectCluster?.id ?? "closed"}
            onDisconnected={(clusterId) => {
              refreshAfterClusterDisconnect(scope, clusterId);
            }}
            onOpenChange={(open) => {
              setDisconnectOpen(open);
              if (!open && !isResumableDisconnectPhase(disconnectPhase)) {
                setDisconnectCluster(null);
              }
            }}
            onPhaseChange={(clusterId, phase) => {
              if (disconnectCluster?.id === clusterId) setDisconnectPhase(phase);
            }}
            open={disconnectOpen && disconnectCluster !== null}
            port={port}
          />
        </>
      ) : null}
    </ProductPageFrame>
  );
}

function isResumableDisconnectPhase(phase: DisconnectPhase): boolean {
  return phase === "submitting" || phase === "uninstalling" || phase === "cleanup-required";
}
