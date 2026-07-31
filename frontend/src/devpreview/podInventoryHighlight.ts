import type { InventoryResource } from "../api/inventory-schemas";
import type {
  PodHighlightIdentity,
  WorkloadHighlightIdentity,
} from "./podHighlight";

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function stringRecord(value: unknown): Record<string, string> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const entries = Object.entries(value);
  if (entries.some(([, entry]) => typeof entry !== "string")) return null;
  return Object.fromEntries(entries) as Record<string, string>;
}

function ownerOf(resource: InventoryResource): {
  kind: string;
  name: string;
} | null {
  const kind = text(resource.summary.owner_kind);
  const name = text(resource.summary.owner_name);
  return kind && name ? { kind, name } : null;
}

function podIdentity(
  clusterId: string,
  resource: InventoryResource,
): PodHighlightIdentity {
  return {
    clusterId,
    namespace: resource.namespace,
    name: resource.name,
  };
}

/** Resolves Pods only from an observed Service selector and observed Pod labels. */
export function podsSelectedByService(
  clusterId: string,
  service: InventoryResource,
  pods: readonly InventoryResource[],
): PodHighlightIdentity[] {
  const selector = stringRecord(service.summary.selector);
  if (selector === null || Object.keys(selector).length === 0) return [];

  return pods
    .filter((pod) => {
      if (pod.kind.toLowerCase() !== "pod" || pod.namespace !== service.namespace) return false;
      const labels = stringRecord(pod.labels);
      return labels !== null
        && Object.entries(selector).every(([key, value]) => labels[key] === value);
    })
    .map((pod) => podIdentity(clusterId, pod));
}

/**
 * Resolves Deployment Pods through observed owner references:
 * Deployment <- ReplicaSet <- Pod.
 */
export function podsOwnedByWorkloads(
  clusterId: string,
  workloads: readonly WorkloadHighlightIdentity[],
  replicaSets: readonly InventoryResource[],
  pods: readonly InventoryResource[],
): PodHighlightIdentity[] {
  const deploymentKeys = new Set(
    workloads
      .filter((workload) => workload.kind.toLowerCase() === "deployment")
      .map((workload) => `${workload.namespace}\u0000${workload.name}`),
  );
  if (deploymentKeys.size === 0) return [];

  const replicaSetKeys = new Set(
    replicaSets
      .filter((replicaSet) => {
        if (replicaSet.kind.toLowerCase() !== "replicaset") return false;
        const owner = ownerOf(replicaSet);
        return owner?.kind.toLowerCase() === "deployment"
          && deploymentKeys.has(`${replicaSet.namespace ?? ""}\u0000${owner.name}`);
      })
      .map((replicaSet) => `${replicaSet.namespace ?? ""}\u0000${replicaSet.name}`),
  );

  return pods
    .filter((pod) => {
      if (pod.kind.toLowerCase() !== "pod") return false;
      const owner = ownerOf(pod);
      return owner?.kind.toLowerCase() === "replicaset"
        && replicaSetKeys.has(`${pod.namespace ?? ""}\u0000${owner.name}`);
    })
    .map((pod) => podIdentity(clusterId, pod));
}
