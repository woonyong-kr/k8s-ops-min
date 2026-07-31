import { z } from "zod";

const nonEmptyString = z.string().min(1);
const nonNegativeInteger = z.number().int().nonnegative();
const timestampMilliseconds = nonNegativeInteger.max(8_640_000_000_000_000);
const timelineSourceModeSchema = z.enum(["retained", "local"]);
const timelineActivitySchema = z.enum(["change", "k8s_event", "warning", "unhealthy"]);
const timelineSourceSchema = z.enum([
  "inventory",
  "incident",
  "application_workflow",
  "kubernetes_event",
  "gitops",
]);

const timelineControlOptionSchema = z.strictObject({
  id: nonEmptyString.max(100),
  label: nonEmptyString.max(200),
  description: z.string().min(1).max(500).nullable(),
});

const timelineActivityControlOptionSchema = timelineControlOptionSchema.extend({
  activity: z.array(timelineActivitySchema),
  problems_activity: z.array(timelineActivitySchema),
});

const timelineTimeRangeOptionSchema = timelineControlOptionSchema.extend({
  duration_ms: z.number().int().min(1_000).max(Number.MAX_SAFE_INTEGER),
});

const timelineLensZoomRungSchema = timelineControlOptionSchema.extend({
  duration_ms: z.number().int().min(1_000).max(Number.MAX_SAFE_INTEGER),
});

/** Exact query limits are computed by the server for the authorized scope. */
const timelineQueryBoundsSchema = z.strictObject({
  server_now_ms: timestampMilliseconds,
  earliest_queryable_ms: timestampMilliseconds,
  max_window_ms: z.number().int().min(1_000).max(Number.MAX_SAFE_INTEGER),
}).refine((bounds) => bounds.earliest_queryable_ms <= bounds.server_now_ms, {
  message: "timeline query bounds must not begin after server time",
});

function uniqueControlIds(
  options: readonly { id: string }[],
  path: (string | number)[],
  context: z.RefinementCtx,
): void {
  if (new Set(options.map((option) => option.id)).size !== options.length) {
    context.addIssue({
      code: "custom",
      message: "timeline control IDs must be unique",
      path,
    });
  }
}

const timelineControlSurfaceSchema = z.strictObject({
  views: z.array(timelineControlOptionSchema).min(1),
  groupings: z.array(timelineControlOptionSchema).min(1),
  sorts: z.array(timelineControlOptionSchema).min(1),
  activity: z.array(timelineActivityControlOptionSchema).min(1),
  deleted: z.strictObject({
    key: nonEmptyString.max(100),
    label: nonEmptyString.max(200),
    default: z.boolean(),
  }),
  kinds: z.strictObject({
    key: nonEmptyString.max(100),
    label: nonEmptyString.max(200),
    selection: z.literal("multi"),
    empty_selection: z.literal("all"),
  }),
  time_ranges: z.array(timelineTimeRangeOptionSchema).min(1),
  default_time_range_id: nonEmptyString.max(100),
  custom_time_range_id: z.literal("custom"),
  lens_zoom_rungs: z.array(timelineLensZoomRungSchema).min(1),
  default_lens_zoom_rung: nonEmptyString.max(100),
  legend: z.strictObject({
    key: z.literal("legend"),
    label: nonEmptyString.max(200),
    availability: z.literal("available"),
    items: z.array(timelineControlOptionSchema).min(1),
  }),
  pins: z.discriminatedUnion("availability", [
    z.strictObject({
      key: z.literal("pins"),
      label: nonEmptyString.max(200),
      availability: z.literal("available"),
      storage: z.literal("server"),
      revision: z.literal("pin_set"),
      subject_kinds: z.tuple([z.literal("resource"), z.literal("application")]),
    }),
    z.strictObject({
      key: z.literal("pins"),
      label: nonEmptyString.max(200),
      availability: z.literal("unavailable"),
      storage: z.null(),
      revision: z.null(),
      subject_kinds: z.tuple([]),
    }),
  ]),
}).superRefine((surface, context) => {
  uniqueControlIds(surface.views, ["views"], context);
  uniqueControlIds(surface.groupings, ["groupings"], context);
  uniqueControlIds(surface.sorts, ["sorts"], context);
  uniqueControlIds(surface.activity, ["activity"], context);
  uniqueControlIds(surface.time_ranges, ["time_ranges"], context);
  uniqueControlIds(surface.lens_zoom_rungs, ["lens_zoom_rungs"], context);
  if (!surface.time_ranges.some((range) => range.id === surface.default_time_range_id)) {
    context.addIssue({
      code: "custom",
      message: "timeline default time range must be available",
      path: ["default_time_range_id"],
    });
  }
  if (!surface.lens_zoom_rungs.some((rung) => rung.id === surface.default_lens_zoom_rung)) {
    context.addIssue({
      code: "custom",
      message: "timeline default lens zoom rung must be available",
      path: ["default_lens_zoom_rung"],
    });
  }
});

/** Server-owned source and query constraints shared by bootstrap and snapshots. */
export const timelineCapabilityDescriptorSchema = z.strictObject({
  selected_source_mode: timelineSourceModeSchema,
  available_source_modes: z.array(timelineSourceModeSchema).min(1),
  max_retained_range_ms: z.number().int().min(1_000).max(Number.MAX_SAFE_INTEGER),
  query_bounds: timelineQueryBoundsSchema,
  namespace_filter_policy: z.enum(["not_required", "required"]),
  control_surface: timelineControlSurfaceSchema,
}).superRefine((descriptor, context) => {
  const modes = new Set(descriptor.available_source_modes);
  if (modes.size !== descriptor.available_source_modes.length) {
    context.addIssue({
      code: "custom",
      message: "timeline capability source modes must be unique",
      path: ["available_source_modes"],
    });
  }
  if (!modes.has(descriptor.selected_source_mode)) {
    context.addIssue({
      code: "custom",
      message: "timeline capability selected source mode must be available",
      path: ["selected_source_mode"],
    });
  }
});

export const timelineScopeSchema = z.strictObject({
  workspace_id: nonEmptyString,
  cluster_id: nonEmptyString,
  namespaces: z.array(nonEmptyString).default([]),
  freshness: z.enum(["live", "stale", "partial", "disconnected"]).default("live"),
});

export const timelineResourceRefSchema = z.strictObject({
  api_group: z.string().default(""),
  version: z.string().default(""),
  kind: nonEmptyString,
  namespace: z.string().nullable().default(null),
  name: nonEmptyString,
  uid: nonEmptyString,
});

const timelineResourceSubjectSchema = z.strictObject({
  kind: z.literal("resource"),
  resource: timelineResourceRefSchema,
});

const timelineInventoryLocatorSubjectSchema = z.strictObject({
  kind: z.literal("inventory_locator"),
  inventory_key: nonEmptyString,
  api_group: z.string().default(""),
  version: z.string().default(""),
  resource_kind: nonEmptyString,
  namespace: z.string().nullable().default(null),
  name: nonEmptyString,
});

const timelineIncidentSubjectSchema = z.strictObject({
  kind: z.literal("incident"),
  incident_id: nonEmptyString,
  correlation_id: nonEmptyString.nullable().default(null),
});

const timelineApplicationWorkflowSubjectSchema = z.strictObject({
  kind: z.literal("application_workflow"),
  application_id: nonEmptyString,
  binding_id: nonEmptyString,
  workflow_run_id: nonEmptyString,
});

export const timelineSubjectSchema = z.discriminatedUnion("kind", [
  timelineResourceSubjectSchema,
  timelineInventoryLocatorSubjectSchema,
  timelineIncidentSubjectSchema,
  timelineApplicationWorkflowSubjectSchema,
]);

export const timelineEventSchema = z.strictObject({
  event_id: nonEmptyString,
  source: timelineSourceSchema,
  source_key: nonEmptyString,
  native_id: nonEmptyString,
  activity: timelineActivitySchema,
  occurred_at: z.string().datetime({ offset: true }),
  scope: timelineScopeSchema,
  subject: timelineSubjectSchema,
  resource: timelineResourceRefSchema.nullable().default(null),
  event_type: z.enum([
    "add",
    "update",
    "delete",
    "k8s_event",
    "incident",
    "deployment",
    "gitops_change",
  ]),
  severity: z.enum(["info", "warning", "critical", "unknown"]),
  title: nonEmptyString.max(1_000),
  owner: timelineResourceRefSchema.nullable().default(null),
  metadata: z.record(z.string(), z.unknown()).default({}),
}).superRefine((event, context) => {
  if (event.subject.kind === "resource") {
    if (event.resource === null) {
      context.addIssue({
        code: "custom",
        message: "resource subject requires an exact resource",
        path: ["resource"],
      });
    } else if (JSON.stringify(event.resource) !== JSON.stringify(event.subject.resource)) {
      context.addIssue({
        code: "custom",
        message: "resource subject must match its resource relation",
        path: ["resource"],
      });
    }
  }
  if (event.subject.kind === "inventory_locator" && event.resource !== null) {
    context.addIssue({
      code: "custom",
      message: "inventory locator must not impersonate a resource",
      path: ["resource"],
    });
  }
  if (
    (event.source === "application_workflow" || event.source === "gitops")
    && event.subject.kind !== "application_workflow"
  ) {
    context.addIssue({
      code: "custom",
      message: "application source requires an application workflow subject",
      path: ["subject"],
    });
  }
});

export const timelineCursorSchema = z.strictObject({
  token: nonEmptyString.refine(
    (value) => value === value.trim() && !/\s/u.test(value),
    "timeline cursor must be opaque",
  ),
});

export const timelineRealtimePolicySchema = z.strictObject({
  max_batch_events: z.number().int().min(1).max(10_000),
  max_frames_per_second: z.number().int().min(1).max(60),
  retention_seconds: z.number().int().min(1).max(31_536_000),
  resume: z.literal("cursor"),
  hidden_tab: z.literal("coalesce"),
  reconnect: z.strictObject({
    min_delay_ms: z.number().int().min(100).max(60_000),
    max_delay_ms: z.number().int().min(100).max(300_000),
    strategy: z.literal("full_jitter_exponential"),
  }).refine((policy) => policy.min_delay_ms <= policy.max_delay_ms, {
    message: "timeline reconnect minimum must not exceed maximum",
  }),
  live_session: z.strictObject({
    max_age_ms: z.number().int().min(1_000).max(300_000),
    strategy: z.literal("replace_with_snapshot"),
  }),
});

export const timelineWindowSchema = z.strictObject({
  from_ms: nonNegativeInteger,
  to_ms: z.number().int().positive(),
}).refine((window) => window.from_ms < window.to_ms, {
  message: "timeline window must have positive width",
});

export const timelineFiltersSchema = z.strictObject({
  activity: z.array(timelineActivitySchema).default([]),
  kinds: z.array(z.string()).default([]),
  include_deleted: z.boolean().default(true),
  pinned_only: z.boolean().default(false),
  query: z.string().max(1_000).default(""),
});

export const timelineQuerySchema = z.strictObject({
  scopes: z.array(timelineScopeSchema).min(1).max(100),
  window: timelineWindowSchema,
  filters: timelineFiltersSchema,
  mode: z.enum(["live", "frozen"]),
  grouping: nonEmptyString.max(100),
  sort: nonEmptyString.max(100),
  view: nonEmptyString.max(100),
  range_id: nonEmptyString.max(100),
  lens_zoom_rung: nonEmptyString.max(100),
});

export const timelineSnapshotRequestSchema = z.strictObject({
  query: timelineQuerySchema,
});

export const timelineStreamRequestSchema = z.strictObject({
  query: timelineQuerySchema,
  after: timelineCursorSchema,
});

export const timelineCoverageSchema = z.strictObject({
  scope: timelineScopeSchema,
  source: timelineSourceSchema,
  from_ms: timestampMilliseconds,
  to_ms: timestampMilliseconds.positive(),
  reason: z.enum(["collection_gap", "retention_boundary", "partial_scope"]),
}).refine((coverage) => coverage.from_ms < coverage.to_ms, {
  message: "timeline coverage must have positive width",
});

const timelineOverviewBucketSchema = z.strictObject({
  from_ms: timestampMilliseconds,
  to_ms: timestampMilliseconds.positive(),
  event_count: nonNegativeInteger,
  problem_count: nonNegativeInteger,
}).superRefine((bucket, context) => {
  if (bucket.from_ms >= bucket.to_ms) {
    context.addIssue({ code: "custom", message: "timeline overview bucket must have positive width", path: ["to_ms"] });
  }
  if (bucket.problem_count > bucket.event_count) {
    context.addIssue({ code: "custom", message: "timeline overview problem count cannot exceed event count", path: ["problem_count"] });
  }
});

const timelineOverviewFacetsSchema = z.strictObject({
  activity: z.array(z.strictObject({ activity: timelineActivitySchema, count: nonNegativeInteger })),
  kinds: z.array(z.strictObject({ kind: nonEmptyString.max(253), count: nonNegativeInteger })),
});

export const timelineOverviewSchema = z.strictObject({
  window: timelineWindowSchema,
  query_bounds: timelineQueryBoundsSchema,
  bucket_width_ms: z.number().int().min(1_000).max(Number.MAX_SAFE_INTEGER),
  buckets: z.array(timelineOverviewBucketSchema).min(1).max(256),
  coverage: z.array(timelineCoverageSchema),
  coverage_sources: z.array(z.strictObject({
    source: timelineSourceSchema,
    availability: z.enum(["observed", "unavailable"]),
  })).min(1),
  facets: timelineOverviewFacetsSchema,
  new_evidence_count: nonNegativeInteger.nullable(),
  pin_set_revision: nonNegativeInteger.nullable(),
}).superRefine((overview, context) => {
  const [first] = overview.buckets;
  const last = overview.buckets[overview.buckets.length - 1];
  if (first?.from_ms !== overview.window.from_ms) {
    context.addIssue({ code: "custom", message: "timeline overview buckets must start at the requested window", path: ["buckets", 0, "from_ms"] });
  }
  if (last?.to_ms !== overview.window.to_ms) {
    context.addIssue({ code: "custom", message: "timeline overview buckets must end at the requested window", path: ["buckets", overview.buckets.length - 1, "to_ms"] });
  }
  for (let index = 1; index < overview.buckets.length; index += 1) {
    if (overview.buckets[index - 1]?.to_ms !== overview.buckets[index]?.from_ms) {
      context.addIssue({ code: "custom", message: "timeline overview buckets must be contiguous", path: ["buckets", index] });
    }
  }
  const sources = overview.coverage_sources.map((source) => source.source);
  if (new Set(sources).size !== sources.length) {
    context.addIssue({ code: "custom", message: "timeline overview coverage sources must be unique", path: ["coverage_sources"] });
  }
});

export const timelineOverviewRequestSchema = z.strictObject({
  query: timelineQuerySchema,
});

const timelinePinResourceTargetSchema = z.strictObject({
  kind: z.literal("resource"),
  scope: timelineScopeSchema,
  resource: timelineResourceRefSchema,
});

const timelinePinApplicationTargetSchema = z.strictObject({
  kind: z.literal("application"),
  application_id: nonEmptyString.max(512),
});

export const timelinePinTargetSchema = z.discriminatedUnion("kind", [
  timelinePinResourceTargetSchema,
  timelinePinApplicationTargetSchema,
]);

const timelineApplicationPinSnapshotSchema = z.strictObject({
  name: nonEmptyString.max(512),
  repository_id: nonEmptyString.max(512),
  manifest_path: nonEmptyString.max(2_048),
});

const timelinePinnedResourceSubjectSchema = timelinePinResourceTargetSchema;
const timelinePinnedApplicationSubjectSchema = timelinePinApplicationTargetSchema.extend({
  snapshot: timelineApplicationPinSnapshotSchema,
});

const timelinePinSubjectSchema = z.discriminatedUnion("kind", [
  timelinePinnedResourceSubjectSchema,
  timelinePinnedApplicationSubjectSchema,
]);

export const timelinePinIdSchema = nonEmptyString.max(128);

const timelinePinSchema = z.strictObject({
  pin_id: timelinePinIdSchema,
  subject: timelinePinSubjectSchema,
  created_at: z.string().datetime({ offset: true }),
});

export const timelinePinSetSchema = z.strictObject({
  revision: nonNegativeInteger,
  pins: z.array(timelinePinSchema),
});

export const timelinePinMutationSchema = z.strictObject({
  action: z.enum(["added", "unchanged", "deleted", "absent"]),
  pin_set: timelinePinSetSchema,
});

export const timelinePinUpsertRequestSchema = z.strictObject({
  expected_revision: nonNegativeInteger,
  target: timelinePinTargetSchema,
});

export const timelinePinDeleteRequestSchema = z.strictObject({
  pin_id: timelinePinIdSchema,
  expected_revision: nonNegativeInteger,
});

const snapshotFrameSchema = z.strictObject({
  kind: z.literal("snapshot"),
  cursor: timelineCursorSchema,
  scopes: z.array(timelineScopeSchema).min(1),
  policy: timelineRealtimePolicySchema,
  capabilities: timelineCapabilityDescriptorSchema,
  events: z.array(timelineEventSchema).default([]),
  coverage: z.array(timelineCoverageSchema).default([]),
  pin_set_revision: nonNegativeInteger.nullable(),
});

const eventFrameSchema = z.strictObject({
  kind: z.literal("event"),
  cursor: timelineCursorSchema,
  event: timelineEventSchema,
  pin_set_revision: z.null(),
});

const coverageFrameSchema = z.strictObject({
  kind: z.literal("coverage"),
  cursor: timelineCursorSchema,
  coverage: z.array(timelineCoverageSchema).min(1),
  pin_set_revision: z.null(),
});

const resyncFrameSchema = z.strictObject({
  kind: z.literal("resync_required"),
  cursor: timelineCursorSchema,
  reason: nonEmptyString.max(500),
  pin_set_revision: z.null(),
});

const endFrameSchema = z.strictObject({
  kind: z.literal("end"),
  cursor: timelineCursorSchema,
  pin_set_revision: z.null(),
});

const errorFrameSchema = z.strictObject({
  kind: z.literal("error"),
  cursor: timelineCursorSchema,
  reason: nonEmptyString.max(500),
  pin_set_revision: z.null(),
});

export const timelineStreamFrameSchema = z.discriminatedUnion("kind", [
  snapshotFrameSchema,
  eventFrameSchema,
  coverageFrameSchema,
  resyncFrameSchema,
  endFrameSchema,
  errorFrameSchema,
]);

export type TimelineEndpointScope = z.infer<typeof timelineScopeSchema>;
export type TimelineEndpointCapabilityDescriptor = z.infer<typeof timelineCapabilityDescriptorSchema>;
export type TimelineEndpointCursor = z.infer<typeof timelineCursorSchema>;
export type TimelineEndpointEvent = z.infer<typeof timelineEventSchema>;
export type TimelineEndpointCoverage = z.infer<typeof timelineCoverageSchema>;
export type TimelineEndpointOverview = z.infer<typeof timelineOverviewSchema>;
export type TimelineEndpointPinMutation = z.infer<typeof timelinePinMutationSchema>;
export type TimelineEndpointPinSet = z.infer<typeof timelinePinSetSchema>;
export type TimelineEndpointPinUpsert = z.output<typeof timelinePinUpsertRequestSchema>;
export type TimelinePinDeleteRequest = z.output<typeof timelinePinDeleteRequestSchema>;
export type TimelineEndpointRealtimePolicy = z.infer<typeof timelineRealtimePolicySchema>;
export type TimelineEndpointStreamFrame = z.infer<typeof timelineStreamFrameSchema>;
export type TimelineEndpointQuery = z.infer<typeof timelineQuerySchema>;
export type TimelineSnapshotRequest = z.output<typeof timelineSnapshotRequestSchema>;
export type TimelineStreamRequest = z.output<typeof timelineStreamRequestSchema>;
export type TimelineOverviewRequest = z.output<typeof timelineOverviewRequestSchema>;
