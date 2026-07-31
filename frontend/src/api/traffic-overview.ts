import { apiRequest } from "./client";
import { canonicalFacetSelections } from "./resource-filter-query";
import {
  trafficOverviewSchema,
  type TrafficOverviewEndpoint,
  type TrafficProtocol,
  type TrafficSince,
  type TrafficSort,
  type TrafficSortOrder,
  type TrafficVerdict,
} from "./traffic-overview-schemas";
import { withQuery } from "./url";

// Dev backend serves the traffic surface at `GET /traffic/flows` (front prefix `/api/traffic/flows`).
export const TRAFFIC_OVERVIEW_PATH = "/api/traffic/flows" as const;

export interface TrafficOverviewQuery {
  clusterIds?: readonly string[];
  namespaces?: readonly string[];
  since?: TrafficSince;
  protocols?: readonly TrafficProtocol[];
  verdicts?: readonly TrafficVerdict[];
  sort?: TrafficSort;
  order?: TrafficSortOrder;
  cursor?: string;
  limit?: number;
}

export function getTrafficOverview(
  query: TrafficOverviewQuery = {},
  signal?: AbortSignal,
): Promise<TrafficOverviewEndpoint> {
  return apiRequest(withQuery(TRAFFIC_OVERVIEW_PATH, [
    ["clusters", joined("clusters", query.clusterIds)],
    ["namespaces", joined("namespaces", query.namespaces)],
    ["since", query.since],
    ["protocols", csv(query.protocols)],
    ["verdicts", csv(query.verdicts)],
    ["sort", query.sort],
    ["order", query.order],
    ["cursor", query.cursor],
    ["limit", query.limit === undefined ? undefined : String(query.limit)],
  ]), trafficOverviewSchema, { signal });
}

function joined(
  axis: "clusters" | "namespaces",
  values: readonly string[] | undefined,
): string | undefined {
  const canonical = canonicalFacetSelections(axis, values);
  return canonical.length === 0 ? undefined : canonical.join(",");
}

function csv(values: readonly string[] | undefined): string | undefined {
  if (values === undefined) return undefined;
  const cleaned = values.map((value) => value.trim()).filter((value) => value.length > 0);
  return cleaned.length === 0 ? undefined : cleaned.join(",");
}
