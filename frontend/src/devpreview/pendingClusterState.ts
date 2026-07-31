import type { DevpreviewCluster } from "./contracts";

export const PENDING_CLUSTER_STORAGE_KEY = "opsia-demo-pending-cl";

function identity(value: unknown): string {
  return typeof value === "string" ? value.trim().toLocaleLowerCase() : "";
}

export function reconcilePendingClusters(
  pending: readonly string[],
  clusters: readonly DevpreviewCluster[],
): string[] {
  const observed = new Set(
    clusters
      .flatMap((cluster) => [identity(cluster.id), identity(cluster.name), identity(cluster.displayName)])
      .filter(Boolean),
  );
  const next = pending.filter((reference) => !observed.has(identity(reference)));
  return next.length === pending.length ? pending as string[] : next;
}

export function removePendingClusterReferences(
  pending: readonly string[],
  references: readonly unknown[],
): string[] {
  const removed = new Set(references.map(identity).filter(Boolean));
  if (removed.size === 0) return pending as string[];
  const next = pending.filter((reference) => !removed.has(identity(reference)));
  return next.length === pending.length ? pending as string[] : next;
}
