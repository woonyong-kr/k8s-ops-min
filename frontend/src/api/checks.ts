import { apiRequest, type ApiPath } from "./client";
import { canonicalFacetSelections } from "./resource-filter-query";
import {
  checksDetailResponseSchema,
  checksOverviewSchema,
  type ChecksDetailEndpoint,
  type ChecksOverviewEndpoint,
} from "./checks-schemas";
import { withQuery } from "./url";

export const CHECKS_OVERVIEW_PATH = "/api/checks/overview" as const;
export const checksDetailPath = (checkId: string): ApiPath => `/api/checks/${encodeURIComponent(checkId)}`;

export interface ChecksQuery {
  clusterIds?: readonly string[];
  namespaces?: readonly string[];
}

export function getChecksOverview(
  query: ChecksQuery = {},
  signal?: AbortSignal,
): Promise<ChecksOverviewEndpoint> {
  return apiRequest(withScope(CHECKS_OVERVIEW_PATH, query), checksOverviewSchema, { signal });
}

export function getChecksDetail(
  checkId: string,
  query: ChecksQuery = {},
  signal?: AbortSignal,
): Promise<ChecksDetailEndpoint> {
  return apiRequest(withScope(checksDetailPath(checkId), query), checksDetailResponseSchema, { signal });
}

function withScope(path: ApiPath, query: ChecksQuery): ApiPath {
  return withQuery(path, [
    ["clusters", joined("clusters", query.clusterIds)],
    ["namespaces", joined("namespaces", query.namespaces)],
  ]);
}

function joined(
  axis: "clusters" | "namespaces",
  values: readonly string[] | undefined,
): string | undefined {
  const canonical = canonicalFacetSelections(axis, values);
  return canonical.length === 0 ? undefined : canonical.join(",");
}
