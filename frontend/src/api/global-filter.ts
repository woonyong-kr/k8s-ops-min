import { apiRequest, type ApiPath } from "./client";
import {
  globalFilterFacetsSchema,
  type GlobalFilterFacets,
} from "./global-filter-schemas";
import {
  boundedQuery,
  canonicalFacetSelections,
  canonicalLabelSelections,
  canonicalResourceTypeSelections,
} from "./resource-filter-query";
import { withQuery } from "./url";

export const GLOBAL_FILTER_FACETS_PATH: ApiPath = "/api/filter-facets";

export interface GlobalFilterFacetQuery {
  q?: string;
  clusters?: readonly string[];
  namespaces?: readonly string[];
  applications?: readonly string[];
  resourceTypes?: readonly string[];
  labels?: readonly string[];
}

export function listGlobalFilterFacets(
  query: GlobalFilterFacetQuery = {},
  signal?: AbortSignal,
): Promise<GlobalFilterFacets> {
  const path = withQuery(GLOBAL_FILTER_FACETS_PATH, [
    ["q", boundedQuery("q", query.q)],
    ["clusters", join(canonicalFacetSelections("clusters", query.clusters))],
    ["namespaces", join(canonicalFacetSelections("namespaces", query.namespaces))],
    ["applications", join(canonicalFacetSelections("applications", query.applications))],
    ["resources.types", join(canonicalResourceTypeSelections(query.resourceTypes))],
    ["labels", join(canonicalLabelSelections(query.labels))],
  ]);
  return apiRequest(path, globalFilterFacetsSchema, { signal });
}

function join(values: readonly string[]): string | undefined {
  return values.length === 0 ? undefined : values.join(",");
}
