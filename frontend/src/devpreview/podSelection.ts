import type { InvPod } from "./inventoryTopologyFeed";

export interface PodResourceSelection {
  destination: "resource-detail";
  kind: "Pod";
  data: Record<string, unknown>;
}

/**
 * A row in the infrastructure Pod list always represents the Pod resource.
 * Health changes the row's presentation, not its navigation destination.
 * Incident navigation requires an incident identity and belongs to the issue
 * list or alert entry point instead of being inferred from Pod health alone.
 */
export function podResourceSelection(pod: InvPod): PodResourceSelection {
  return {
    destination: "resource-detail",
    kind: "Pod",
    data: {
      name: pod.name,
      ns: pod.namespace ?? undefined,
      kind: "Pod",
      status: pod.status,
      health: pod.health,
      cluster: pod.cluster,
      bad: pod.health.toLowerCase() === "critical"
        || pod.health.toLowerCase() === "failed"
        || pod.health.toLowerCase() === "unhealthy",
      _key: pod.key,
    },
  };
}
