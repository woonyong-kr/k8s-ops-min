import { useEffect, useState } from "react";

import { listGlobalFilterFacets } from "../api/global-filter";

// UI-PHASE2-001 §5.2: typed live adapter for the Home W5 namespace distribution.
// Uses one authorized global facet aggregation instead of starting one heavy
// inventory projection per cluster. Restricting the facet query to Pod keeps
// the widget's meaning while avoiding a database request fan-out.

export type NamespacesFeedStatus = "loading" | "ready" | "unavailable";

export interface NamespacePodCount {
  namespace: string;
  podCount: number;
}

export interface InventoryNamespacesView {
  status: NamespacesFeedStatus;
  items: NamespacePodCount[];
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

export function useInventoryNamespaces(
  clusterIds: readonly string[],
): InventoryNamespacesView {
  const [view, setView] = useState<InventoryNamespacesView>({ status: "loading", items: [] });
  const key = clusterIds.join(" ");
  useEffect(() => {
    const ids = key ? key.split(" ") : [];
    if (ids.length === 0) return undefined;
    const controller = new AbortController();
    void listGlobalFilterFacets({ clusters: ids, resourceTypes: ["pod"] }, controller.signal)
      .then((facets) => {
        if (controller.signal.aborted) return;
        const totals = new Map<string, number>();
        for (const item of facets.namespaces) {
          if (item.count === null) continue;
          totals.set(item.label, (totals.get(item.label) ?? 0) + item.count);
        }
        const items = [...totals.entries()]
          .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
          .map(([namespace, podCount]) => ({ namespace, podCount }));
        setView({ status: "ready", items });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setView({ status: "unavailable", items: [] });
      });
    return () => controller.abort();
  }, [key]);
  return key === "" ? { status: "ready", items: [] } : view;
}
