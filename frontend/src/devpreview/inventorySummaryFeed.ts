import { getInventorySummary } from "../api/inventory-summary";
import type { InventorySummary } from "../api/inventory-summary-schemas";

// Home consumes the same heavy inventory summary in both kind-count and
// namespace widgets. Share only the in-flight promise so simultaneous widgets
// and React StrictMode mounts cannot multiply the database projection. The
// server remains authoritative on the next intentional refresh/remount.
const pending = new Map<string, Promise<InventorySummary>>();

export function getSharedInventorySummary(clusterId: string): Promise<InventorySummary> {
  const current = pending.get(clusterId);
  if (current !== undefined) return current;
  const request = getInventorySummary(clusterId)
    .finally(() => {
      if (pending.get(clusterId) === request) pending.delete(clusterId);
    });
  pending.set(clusterId, request);
  return request;
}

/** @internal test isolation for an intentionally unresolved request. */
export function resetInventorySummaryRequestsForTests(): void {
  pending.clear();
}
