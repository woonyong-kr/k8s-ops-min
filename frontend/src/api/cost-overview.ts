import { apiRequest } from "./client";
import { canonicalFacetSelections } from "./resource-filter-query";
import { costOverviewSchema, type CostOverviewEndpoint } from "./cost-overview-schemas";
import { withQuery } from "./url";

export const COST_OVERVIEW_PATH = "/api/cost/overview" as const;

export interface CostOverviewQuery {
  clusterIds?: readonly string[];
}

export function getCostOverview(
  query: CostOverviewQuery = {},
  signal?: AbortSignal,
): Promise<CostOverviewEndpoint> {
  const clusterIds = canonicalFacetSelections("clusters", query.clusterIds);
  return apiRequest(withQuery(COST_OVERVIEW_PATH, [
    ["clusters", clusterIds.length === 0 ? undefined : clusterIds.join(",")],
  ]), costOverviewSchema, { signal });
}
