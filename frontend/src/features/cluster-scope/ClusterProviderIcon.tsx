import type { HomeClusterProvider } from "../home/homeContract";
import { useI18n, type MessageKey } from "../../shared/i18n";
import { ProviderLogo } from "../../shared/brand/ProviderLogo";
import { cn } from "../../shared/lib/cn";

const providerLabelKeys: Record<HomeClusterProvider, MessageKey> = {
  aks: "clusterScope.provider.aks",
  eks: "clusterScope.provider.eks",
  gke: "clusterScope.provider.gke",
  kind: "clusterScope.provider.kind",
  onprem: "clusterScope.provider.onprem",
  unknown: "clusterScope.provider.unknown",
};

export function ClusterProviderIcon({
  appearance = "compact",
  className,
  provider,
}: {
  appearance?: "card" | "compact";
  className?: string;
  provider: HomeClusterProvider;
}) {
  const { t } = useI18n();
  const label = t(providerLabelKeys[provider]);

  return (
    <span
      aria-label={label}
      className={cn(
        "inline-grid shrink-0 place-items-center",
        appearance === "card"
          ? "size-9 rounded-lg border bg-muted/45 text-foreground shadow-xs"
          : "size-4",
        className,
      )}
      data-provider={provider}
      data-slot="cluster-provider-icon"
      role="img"
      title={label}
    >
      <ProviderLogo
        className={appearance === "card"
          ? provider === "eks" ? "h-4 w-7" : "size-5"
          : provider === "eks" ? "h-2.5 w-4" : "size-3.5"}
        provider={provider}
      />
    </span>
  );
}
