import {
  getInventoryResourceDetail,
  type InventoryResourceIdentity,
} from "../api/inventory";
import type { InventoryResourceDetail } from "../api/inventory-schemas";

// One detail payload is shared by overview, events, Pod summary, and manifest
// identity consumers. The limits are the bounded superset required by those
// projections, so opening one drawer never fans out four equivalent requests.
export const SHARED_RESOURCE_DETAIL_RELATED_LIMIT = 20;
export const SHARED_RESOURCE_DETAIL_EVENT_LIMIT = 100;

const inFlightResourceDetail = new Map<string, Promise<InventoryResourceDetail>>();

function resourceDetailRequestKey(
  clusterId: string,
  identity: InventoryResourceIdentity,
): string {
  return JSON.stringify({
    clusterId,
    resourceType: identity.resourceType,
    kind: identity.kind,
    namespace: identity.namespace ?? null,
    name: identity.name,
  });
}

/**
 * Coalesces only simultaneous identical reads. Settled data is never retained,
 * preventing a previous session/workspace payload from becoming a cache.
 * Consumers retain their own AbortController and ignore settlement after
 * unmount; the shared underlying request is not owned by any one consumer.
 */
export function loadSharedInventoryResourceDetail(
  clusterId: string,
  identity: InventoryResourceIdentity,
): Promise<InventoryResourceDetail> {
  const key = resourceDetailRequestKey(clusterId, identity);
  const existing = inFlightResourceDetail.get(key);
  if (existing) return existing;
  const request = getInventoryResourceDetail(
    clusterId,
    identity,
    {
      relatedLimit: SHARED_RESOURCE_DETAIL_RELATED_LIMIT,
      eventLimit: SHARED_RESOURCE_DETAIL_EVENT_LIMIT,
    },
  );
  inFlightResourceDetail.set(key, request);
  const release = () => {
    if (inFlightResourceDetail.get(key) === request) {
      inFlightResourceDetail.delete(key);
    }
  };
  void request.then(release, release);
  return request;
}

/** @internal Test isolation for the in-flight request coalescer. */
export function resetSharedInventoryResourceDetailForTests(): void {
  inFlightResourceDetail.clear();
}
