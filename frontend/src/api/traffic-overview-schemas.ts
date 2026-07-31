import { z } from "zod";

// --- Literal aliases (packages.contracts.traffic.observations) ---
export const trafficAvailabilitySchema = z.enum(["available", "partial", "unavailable"]);
export const trafficSinceSchema = z.enum(["1m", "5m", "15m", "1h"]);
export const trafficSortSchema = z.enum(["connections", "last_seen", "source", "destination"]);
export const trafficSortOrderSchema = z.enum(["asc", "desc"]);
export const trafficProtocolSchema = z.enum(["tcp", "udp", "http", "grpc", "dns", "unknown"]);
export const trafficVerdictSchema = z.enum(["forwarded", "dropped", "error", "unknown"]);

export type TrafficAvailability = z.infer<typeof trafficAvailabilitySchema>;
export type TrafficSince = z.infer<typeof trafficSinceSchema>;
export type TrafficSort = z.infer<typeof trafficSortSchema>;
export type TrafficSortOrder = z.infer<typeof trafficSortOrderSchema>;
export type TrafficProtocol = z.infer<typeof trafficProtocolSchema>;
export type TrafficVerdict = z.infer<typeof trafficVerdictSchema>;

// Freshness (packages.contracts.parity)
const freshnessSchema = z.enum(["live", "stale", "partial", "disconnected"]);
// tuple[str, ...] with a `()` default: the field is always emitted, so REQUIRED but may be empty.
const reasonCodesSchema = z.array(z.string());

// --- ClusterScope (packages.contracts.parity) ---
export const trafficClusterScopeSchema = z.strictObject({
  workspace_id: z.string().min(1),
  cluster_id: z.string().min(1),
  namespaces: z.array(z.string().min(1)),
  freshness: freshnessSchema,
});

// --- TrafficScopeCoverage ---
export const trafficScopeCoverageSchema = z.strictObject({
  availability: trafficAvailabilitySchema,
  scopes: z.array(trafficClusterScopeSchema),
  observed_at: z.string().min(1).nullable(),
  reason_codes: reasonCodesSchema,
}).superRefine((value, context) => {
  if (value.availability !== "available" && value.reason_codes.length === 0) {
    context.addIssue({ code: "custom", message: "incomplete traffic scope requires reasons" });
  }
});

// --- TrafficEndpoint ---
export const trafficEndpointSchema = z.strictObject({
  cluster_id: z.string().min(1),
  name: z.string().min(1),
  namespace: z.string().min(1).nullable(),
  kind: z.string().min(1),
  workload: z.string().min(1).nullable(),
  service: z.string().min(1).nullable(),
  ip: z.string().min(1).nullable(),
  identity_stability: z.literal("provider_observed"),
});

// --- TrafficRelationship (flow item) ---
export const trafficRelationshipSchema = z.strictObject({
  flow_id: z.string().min(1),
  source_key: z.string().min(1),
  source: trafficEndpointSchema,
  target: trafficEndpointSchema,
  protocol: trafficProtocolSchema,
  port: z.number().int().nullable(),
  verdict: trafficVerdictSchema,
  connections: z.number().int(),
  bytes_sent: z.number().int().nullable(),
  bytes_received: z.number().int().nullable(),
  observed_at: z.string().min(1),
});

// --- Facets ---
export const trafficProtocolFacetSchema = z.strictObject({
  value: trafficProtocolSchema,
  count: z.number().int(),
});

export const trafficVerdictFacetSchema = z.strictObject({
  value: trafficVerdictSchema,
  count: z.number().int(),
});

export const trafficFlowFacetsSchema = z.strictObject({
  protocols: z.array(trafficProtocolFacetSchema),
  verdicts: z.array(trafficVerdictFacetSchema),
});

// --- Observation: unavailable branch (TrafficObservationStatus) ---
export const trafficObservationStatusSchema = z.strictObject({
  availability: z.literal("unavailable"),
  observed_at: z.null(),
  reason_codes: reasonCodesSchema,
});

// --- Observation: observed branch (TrafficObservedObservationStatus) ---
export const trafficObservedObservationStatusSchema = z.strictObject({
  availability: z.enum(["available", "partial"]),
  observed_at: z.string().min(1),
  since: trafficSinceSchema,
  source_keys: z.array(z.string()),
  reason_codes: reasonCodesSchema,
});

// --- Summary: unavailable branch (TrafficObservationSummary) ---
export const trafficObservationSummarySchema = z.strictObject({
  availability: z.literal("unavailable"),
  total_flow_count: z.null(),
  denied_flow_count: z.null(),
  external_flow_count: z.null(),
  reason_codes: reasonCodesSchema,
});

// --- Summary: observed branch (TrafficObservedObservationSummary) ---
export const trafficObservedObservationSummarySchema = z.strictObject({
  availability: z.enum(["available", "partial"]),
  total_flow_count: z.number().int(),
  denied_flow_count: z.number().int(),
  external_flow_count: z.number().int(),
  reason_codes: reasonCodesSchema,
});

// --- Relationships: unavailable branch (TrafficRelationships) ---
export const trafficRelationshipsSchema = z.strictObject({
  availability: z.literal("unavailable"),
  edges: z.null(),
  reason_codes: reasonCodesSchema,
});

// --- Relationships: observed branch (TrafficObservedRelationships) ---
export const trafficObservedRelationshipsSchema = z.strictObject({
  availability: z.enum(["available", "partial"]),
  edges: z.array(trafficRelationshipSchema),
  total_count: z.number().int(),
  has_more: z.boolean(),
  next_cursor: z.string().min(1).nullable(),
  facets: trafficFlowFacetsSchema,
  reason_codes: reasonCodesSchema,
});

// --- TrafficOverviewResponse ---
export const trafficOverviewSchema = z.strictObject({
  scope_coverage: trafficScopeCoverageSchema,
  observation: z.union([
    trafficObservedObservationStatusSchema,
    trafficObservationStatusSchema,
  ]),
  summary: z.union([
    trafficObservedObservationSummarySchema,
    trafficObservationSummarySchema,
  ]),
  relationships: z.union([
    trafficObservedRelationshipsSchema,
    trafficRelationshipsSchema,
  ]),
  refresh_after_seconds: z.number().int(),
});

export type TrafficEndpoint = z.infer<typeof trafficEndpointSchema>;
export type TrafficRelationship = z.infer<typeof trafficRelationshipSchema>;
export type TrafficOverviewEndpoint = z.infer<typeof trafficOverviewSchema>;
