import { z } from "zod";

import { parseKubernetesLabelSelector } from "../shared/data/kubernetesLabel";
import { inventoryResourceSchema } from "./inventory-schemas";

export const rfc3339TimestampSchema = z.iso.datetime({ offset: true });
const nullableRfc3339TimestampSchema = rfc3339TimestampSchema.nullable();
export const filterCountCompletenessSchema = z.enum([
  "exact",
  "partial",
  "unavailable",
]);

export const filterFacetAvailabilitySchema = z.enum([
  "available",
  "restricted",
  "unresolved",
]);

export const resourceFilterFacetAxisSchema = z.enum([
  "clusters",
  "namespaces",
  "applications",
]);

export const filterResultCountsSchema = z.strictObject({
  filtered_count: z.number().int().nonnegative().nullable(),
  unfiltered_count: z.number().int().nonnegative().nullable(),
  filtered_count_completeness: filterCountCompletenessSchema,
  unfiltered_count_completeness: filterCountCompletenessSchema,
}).superRefine((counts, context) => {
  validateCountCompleteness(
    counts.filtered_count,
    counts.filtered_count_completeness,
    "filtered_count",
    context,
  );
  validateCountCompleteness(
    counts.unfiltered_count,
    counts.unfiltered_count_completeness,
    "unfiltered_count",
    context,
  );
});

export const filterSnapshotMetaSchema = z.strictObject({
  snapshot_revision: z.number().int().nonnegative(),
  authorization_revision: z.string().min(1),
  filter_fingerprint: z.string().min(1),
  observed_at: nullableRfc3339TimestampSchema,
  stale: z.boolean(),
  partial_reason_codes: z.array(z.string()),
});

export const clusterFilterFacetItemSchema = z.strictObject({
  axis: z.literal("cluster"),
  value: z.string().min(1),
  cluster_id: z.string().min(1),
  name: z.string().nullable(),
  provider: z.string().nullable(),
  availability: filterFacetAvailabilitySchema,
}).superRefine((item, context) => {
  if (item.value === item.cluster_id) return;
  context.addIssue({
    code: "custom",
    message: "cluster facet value must equal cluster_id",
    path: ["value"],
  });
});

export const namespaceFilterFacetItemSchema = z.strictObject({
  axis: z.literal("namespace"),
  value: z.string().min(1),
  cluster_id: z.string().min(1),
  namespace: z.string().min(1),
  availability: filterFacetAvailabilitySchema,
}).superRefine((item, context) => {
  if (item.value === `${item.cluster_id}/${item.namespace}`) return;
  context.addIssue({
    code: "custom",
    message: "namespace facet value must equal <cluster_id>/<namespace>",
    path: ["value"],
  });
});

export const applicationFilterFacetItemSchema = z.strictObject({
  axis: z.literal("application"),
  value: z.string().min(1),
  application_id: z.string().min(1),
  name: z.string().nullable(),
  environment: z.string().nullable(),
  availability: filterFacetAvailabilitySchema,
}).superRefine((item, context) => {
  if (item.value === item.application_id) return;
  context.addIssue({
    code: "custom",
    message: "application facet value must equal application_id",
    path: ["value"],
  });
});

export const resourceFilterFacetItemSchema = z.discriminatedUnion("axis", [
  clusterFilterFacetItemSchema,
  namespaceFilterFacetItemSchema,
  applicationFilterFacetItemSchema,
]);

export const selectedFilterFacetResolutionSchema = z.strictObject({
  axis: z.enum(["cluster", "namespace", "application"]),
  value: z.string().min(1),
  status: z.enum(["resolved", "restricted", "unresolved", "unavailable"]),
  display_label: z.string().nullable(),
});

const facetItemAxisByPageAxis = {
  clusters: "cluster",
  namespaces: "namespace",
  applications: "application",
} as const;

export const resourceFilterFacetPageSchema = z.strictObject({
  axis: resourceFilterFacetAxisSchema,
  items: z.array(resourceFilterFacetItemSchema),
  selected_resolutions: z.array(selectedFilterFacetResolutionSchema),
  next_cursor: z.string().min(1).nullable(),
  has_more: z.boolean(),
  snapshot: filterSnapshotMetaSchema,
}).superRefine((page, context) => {
  validatePagination(page.next_cursor, page.has_more, context);
  const expectedAxis = facetItemAxisByPageAxis[page.axis];
  page.items.forEach((item, index) => {
    if (item.axis === expectedAxis) return;
    context.addIssue({
      code: "custom",
      message: `facet item axis must be ${expectedAxis}`,
      path: ["items", index, "axis"],
    });
  });
  page.selected_resolutions.forEach((resolution, index) => {
    if (resolution.axis === expectedAxis) return;
    context.addIssue({
      code: "custom",
      message: `selected facet axis must be ${expectedAxis}`,
      path: ["selected_resolutions", index, "axis"],
    });
  });
});

export const inventoryResourceClusterIdentitySchema = z.strictObject({
  cluster_id: z.string().min(1),
  name: z.string().nullable(),
  provider: z.string().nullable(),
});

const filteredInventoryResourceSchema = inventoryResourceSchema.extend({
  observed_at: nullableRfc3339TimestampSchema,
  first_seen_at: nullableRfc3339TimestampSchema,
  last_seen_at: nullableRfc3339TimestampSchema,
  deleted_at: nullableRfc3339TimestampSchema,
  created_at: nullableRfc3339TimestampSchema,
  updated_at: nullableRfc3339TimestampSchema,
});

// ResourceTableMetricEvidence base (packages.contracts.gateway.responses).
const resourceTableMetricEvidenceShape = {
  resource_uid: z.string().min(1).nullable(),
  source_snapshot_id: z.string().min(1),
  observed_at: z.string().min(1).nullable(),
  measurement_window: z.string().min(1).max(64).nullable(),
  cpu_mcores: z.number().min(0).nullable(),
  memory_mib: z.number().min(0).nullable(),
  completeness: filterCountCompletenessSchema,
  reason_codes: z.array(z.string()).max(16),
};

// ResourceTablePodMetrics — evidence base + pod request/limit fields.
export const resourceTablePodMetricsSchema = z.strictObject({
  ...resourceTableMetricEvidenceShape,
  kind: z.literal("pod"),
  cpu_request_mcores: z.number().nullable(),
  cpu_limit_mcores: z.number().nullable(),
  memory_request_mib: z.number().nullable(),
  memory_limit_mib: z.number().nullable(),
});

// ResourceTableNodeMetrics — evidence base + node allocatable/pod counts.
export const resourceTableNodeMetricsSchema = z.strictObject({
  ...resourceTableMetricEvidenceShape,
  kind: z.literal("node"),
  cpu_allocatable_mcores: z.number().nullable(),
  memory_allocatable_mib: z.number().nullable(),
  pod_count: z.number().int().nullable(),
  pod_allocatable: z.number().int().nullable(),
});

export const resourceTableMetricsSchema = z.discriminatedUnion("kind", [
  resourceTablePodMetricsSchema,
  resourceTableNodeMetricsSchema,
]);

export const filteredInventoryResourceItemSchema = z.strictObject({
  resource: filteredInventoryResourceSchema,
  cluster: inventoryResourceClusterIdentitySchema,
  application_ids: z.array(z.string().min(1)),
  application_binding_completeness: filterCountCompletenessSchema,
  // ResourceTablePodMetrics | ResourceTableNodeMetrics | None
  metrics: resourceTableMetricsSchema.nullable(),
}).superRefine((item, context) => {
  if (item.resource.cluster_id !== item.cluster.cluster_id) {
    context.addIssue({
      code: "custom",
      message: "resource cluster_id must equal cluster identity",
      path: ["cluster", "cluster_id"],
    });
  }
  if (new Set(item.application_ids).size !== item.application_ids.length) {
    context.addIssue({
      code: "custom",
      message: "application_ids must not contain duplicates",
      path: ["application_ids"],
    });
  }
});

export const filteredInventoryResourceListSchema = z.strictObject({
  items: z.array(filteredInventoryResourceItemSchema),
  next_cursor: z.string().min(1).nullable(),
  has_more: z.boolean(),
  counts: filterResultCountsSchema,
  snapshot: filterSnapshotMetaSchema,
}).superRefine((page, context) => {
  validatePagination(page.next_cursor, page.has_more, context);
});

const labelSelectorShape = {
  key: z.string().min(1),
  value: z.string(),
  selector: z.string().min(2),
};

export const labelSelectorSchema = z.strictObject(labelSelectorShape).superRefine(
  validateLabelSelector,
);

export const labelFacetItemSchema = z.strictObject({
  ...labelSelectorShape,
  match_count: z.number().int().nonnegative().nullable(),
  count_completeness: filterCountCompletenessSchema,
}).superRefine((item, context) => {
  validateLabelSelector(item, context);
  validateCountCompleteness(
    item.match_count,
    item.count_completeness,
    "match_count",
    context,
  );
});

export const selectedLabelResolutionSchema = z.strictObject({
  ...labelSelectorShape,
  status: z.enum(["resolved", "zero", "restricted", "unavailable"]),
}).superRefine((resolution, context) => {
  validateLabelSelector(resolution, context);
});

export const labelFacetPageSchema = z.strictObject({
  surface: z.literal("resources"),
  items: z.array(labelFacetItemSchema),
  selected_resolutions: z.array(selectedLabelResolutionSchema),
  next_cursor: z.string().min(1).nullable(),
  has_more: z.boolean(),
  counts: filterResultCountsSchema,
  snapshot: filterSnapshotMetaSchema,
}).superRefine((page, context) => {
  validatePagination(page.next_cursor, page.has_more, context);
});

function validatePagination(
  nextCursor: string | null,
  hasMore: boolean,
  context: z.RefinementCtx,
): void {
  if (hasMore === (nextCursor !== null)) return;
  context.addIssue({
    code: "custom",
    message: "has_more and next_cursor must describe the same page boundary",
    path: ["next_cursor"],
  });
}

function validateCountCompleteness(
  count: number | null,
  completeness: z.infer<typeof filterCountCompletenessSchema>,
  path: string,
  context: z.RefinementCtx,
): void {
  const isAvailable = completeness !== "unavailable";
  if (isAvailable === (count !== null)) return;
  context.addIssue({
    code: "custom",
    message: "count availability must match its completeness",
    path: [path],
  });
}

function validateLabelSelector(
  selector: { key: string; value: string; selector: string },
  context: z.RefinementCtx,
): void {
  const canonical = `${selector.key}=${selector.value}`;
  if (
    selector.selector === canonical &&
    parseKubernetesLabelSelector(canonical) !== null
  ) return;
  context.addIssue({
    code: "custom",
    message: "selector must equal a canonical Kubernetes key=value selector",
    path: ["selector"],
  });
}

export type FilterCountCompleteness = z.infer<typeof filterCountCompletenessSchema>;
export type FilterResultCounts = z.infer<typeof filterResultCountsSchema>;
export type FilterSnapshotMeta = z.infer<typeof filterSnapshotMetaSchema>;
export type ResourceFilterFacetAxis = z.infer<typeof resourceFilterFacetAxisSchema>;
export type ResourceFilterFacetItem = z.infer<typeof resourceFilterFacetItemSchema>;
export type SelectedFilterFacetResolution = z.infer<
  typeof selectedFilterFacetResolutionSchema
>;
export type ResourceFilterFacetPage = z.infer<typeof resourceFilterFacetPageSchema>;
export type FilteredInventoryResourceItem = z.infer<
  typeof filteredInventoryResourceItemSchema
>;
export type FilteredInventoryResourceList = z.infer<
  typeof filteredInventoryResourceListSchema
>;
export type LabelFacetItem = z.infer<typeof labelFacetItemSchema>;
export type SelectedLabelResolution = z.infer<typeof selectedLabelResolutionSchema>;
export type LabelFacetPage = z.infer<typeof labelFacetPageSchema>;
