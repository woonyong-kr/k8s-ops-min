import { apiRequest, type ApiPath } from "./client";
import {
  physicalTopologySchema,
  type PhysicalTopologyEndpoint,
} from "./physical-topology-schemas";
import {
  resourceFilterEntries,
  type ResourceFilterQuery,
} from "./resource-filter-query";
import { withQuery } from "./url";

export const PHYSICAL_TOPOLOGY_PATH: ApiPath = "/api/topology";

export interface PhysicalTopologyQuery extends ResourceFilterQuery {
  snapshotRevision?: number;
}

export function getPhysicalTopology(
  query: PhysicalTopologyQuery,
  signal?: AbortSignal,
): Promise<PhysicalTopologyEndpoint> {
  const path = withQuery(PHYSICAL_TOPOLOGY_PATH, [
    ["view", "physical"],
    ...resourceFilterEntries(query),
    ["snapshot_revision", snapshotRevision(query.snapshotRevision)],
  ]);
  return apiRequest(path, physicalTopologySchema, { signal });
}

function snapshotRevision(value: number | undefined): number | undefined {
  if (value === undefined) return undefined;
  if (!Number.isInteger(value) || value < 1) {
    throw new RangeError("snapshotRevision must be a positive integer");
  }
  return value;
}
