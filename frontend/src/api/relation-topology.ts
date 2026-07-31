import { apiRequest, type ApiPath } from "./client";
import {
  relationTopologySchema,
  type RelationTopologyEndpoint,
} from "./relation-topology-schemas";
import {
  resourceFilterEntries,
  type ResourceFilterQuery,
} from "./resource-filter-query";
import { withQuery } from "./url";

export const RELATION_TOPOLOGY_PATH: ApiPath = "/api/topology";

export interface RelationTopologyQuery extends ResourceFilterQuery {
  snapshotRevision?: number;
}

export function getRelationTopology(
  query: RelationTopologyQuery,
  signal?: AbortSignal,
): Promise<RelationTopologyEndpoint> {
  const path = withQuery(RELATION_TOPOLOGY_PATH, [
    ["view", "relations"],
    ...resourceFilterEntries(query),
    ["snapshot_revision", snapshotRevision(query.snapshotRevision)],
  ]);
  return apiRequest(path, relationTopologySchema, { signal });
}

function snapshotRevision(value: number | undefined): number | undefined {
  if (value === undefined) return undefined;
  if (!Number.isInteger(value) || value < 1) {
    throw new RangeError("snapshotRevision must be a positive integer");
  }
  return value;
}
