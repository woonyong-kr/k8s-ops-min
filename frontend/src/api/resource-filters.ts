import { apiRequest, type ApiPath } from "./client";
import {
  bindFacetPageToRequest,
  bindLabelPageToRequest,
} from "./resource-filter-bindings";
import {
  filteredInventoryResourceListSchema,
  type FilteredInventoryResourceList,
  type LabelFacetPage,
  type ResourceFilterFacetAxis,
  type ResourceFilterFacetPage,
} from "./resource-filter-schemas";
import {
  boundedQuery,
  canonicalFacetSelections,
  canonicalLabelSelections,
  opaqueCursor,
  pageLimit,
  resourceFilterEntries,
  type ResourceFilterQuery,
} from "./resource-filter-query";
import { withQuery } from "./url";

export const RESOURCES_FILTER_FACETS_PATH: ApiPath = "/api/resources/filter-facets";
export const FILTERED_RESOURCES_PATH: ApiPath = "/api/resources";
export const RESOURCE_LABEL_FACETS_PATH: ApiPath = "/api/resources/label-facets";

export interface ListResourceFilterFacetsOptions {
  axis: ResourceFilterFacetAxis;
  selected?: readonly string[];
  cursor?: string;
  limit?: number;
}

export interface ListResourceLabelFacetsOptions extends ResourceFilterQuery {
  facetQuery?: string;
}

/**
 * Loads one workspace-scoped structural facet page.
 * Backend contract: `RESOURCES_FILTER_FACETS_PATH` / `87c0606e0`.
 */
export function listResourceFilterFacets(
  options: ListResourceFilterFacetsOptions,
  signal?: AbortSignal,
): Promise<ResourceFilterFacetPage> {
  const limit = pageLimit(options.limit);
  const cursor = opaqueCursor(options.cursor);
  const selectedValues = canonicalFacetSelections(options.axis, options.selected);
  const path = withQuery(RESOURCES_FILTER_FACETS_PATH, [
    ["axis", options.axis],
    ["selected", selectedValues.length === 0 ? undefined : selectedValues.join(",")],
    ["cursor", cursor],
    ["limit", limit],
  ]);
  return apiRequest(
    path,
    bindFacetPageToRequest(options.axis, selectedValues),
    { signal },
  );
}

/**
 * Lists one server-filtered, cursor-bound Resources page.
 * Backend contract: `FILTERED_RESOURCES_PATH` / `87c0606e0`.
 */
export function listFilteredResources(
  query: ResourceFilterQuery = {},
  signal?: AbortSignal,
): Promise<FilteredInventoryResourceList> {
  const path = withQuery(FILTERED_RESOURCES_PATH, [
    ...resourceFilterEntries(query),
    ["cursor", opaqueCursor(query.cursor)],
    ["limit", pageLimit(query.limit)],
  ]);
  return apiRequest(path, filteredInventoryResourceListSchema, { signal });
}

/**
 * Loads server-counted Label facets for the Resources surface.
 * Backend contract: `RESOURCE_LABEL_FACETS_PATH` / `87c0606e0`.
 */
export function listResourceLabelFacets(
  query: ListResourceLabelFacetsOptions = {},
  signal?: AbortSignal,
): Promise<LabelFacetPage> {
  const selectedLabels = canonicalLabelSelections(query.labels);
  const path = withQuery(RESOURCE_LABEL_FACETS_PATH, [
    ["surface", "resources"],
    ...resourceFilterEntries(query, selectedLabels),
    ["facet_q", boundedQuery("facetQuery", query.facetQuery)],
    ["cursor", opaqueCursor(query.cursor)],
    ["limit", pageLimit(query.limit)],
  ]);
  return apiRequest(path, bindLabelPageToRequest(selectedLabels), { signal });
}

export type { ResourceFilterQuery } from "./resource-filter-query";
