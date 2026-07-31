import { z } from "zod";

const availabilitySchema = z.enum(["available", "partial", "unavailable"]);
const freshnessSchema = z.enum(["live", "stale", "partial", "disconnected"]);
const nullableTextSchema = z.string().min(1).nullable();
const featureNameSchema = z.enum([
  "overview",
  "pods",
  "events",
  "logs",
  "metrics",
  "topology",
  "timeline",
  "rbac",
  "gitops",
  "helm",
  "operations",
  "yaml",
  "compare",
]);

export const workloadDetailResourceRefSchema = z.strictObject({
  api_group: z.string(),
  version: z.string().min(1),
  kind: z.string().min(1),
  namespace: nullableTextSchema,
  name: z.string().min(1),
  uid: z.string().min(1),
});

const workloadDetailScopeSchema = z.strictObject({
  workspace_id: z.string().min(1),
  cluster_id: z.string().min(1),
  namespaces: z.array(z.string().min(1)),
  freshness: freshnessSchema,
});

const reasonCodesSchema = z.array(z.string().min(1));

const workloadDetailCoverageSchema = z.strictObject({
  availability: availabilitySchema,
  observation_snapshot_id: z.string().min(1),
  latest_snapshot_id: z.string().min(1),
  observed_at: nullableTextSchema,
  reason_codes: reasonCodesSchema,
});

const workloadDetailFeatureSchema = z.strictObject({
  name: featureNameSchema,
  availability: availabilitySchema,
  reason_codes: reasonCodesSchema,
});

const workloadLogStreamSchema = z.strictObject({
  availability: availabilitySchema,
  stream_kind: z.enum(["deployments", "statefulsets", "daemonsets"]).nullable(),
  reason_codes: reasonCodesSchema,
});

const workloadReplicaSchema = z.strictObject({
  desired: z.number().int().nonnegative().nullable(),
  ready: z.number().int().nonnegative().nullable(),
  available: z.number().int().nonnegative().nullable(),
  updated: z.number().int().nonnegative().nullable(),
  unavailable: z.number().int().nonnegative().nullable(),
});

const workloadLabelSchema = z.strictObject({
  key: z.string().min(1),
  value: z.string(),
});

const workloadObservationSchema = z.strictObject({
  resource: workloadDetailResourceRefSchema,
  health: z.string().min(1),
  replicas: workloadReplicaSchema,
  labels: z.array(workloadLabelSchema),
  observed_at: nullableTextSchema,
});

const workloadPodObservationSchema = z.strictObject({
  resource: workloadDetailResourceRefSchema,
  health: z.string().min(1),
  observed_at: nullableTextSchema,
});

const workloadPodCollectionSchema = z.strictObject({
  availability: availabilitySchema,
  items: z.array(workloadPodObservationSchema),
  excluded_count: z.number().int().nonnegative(),
  reason_codes: reasonCodesSchema,
});

const workloadEventObservationSchema = z.strictObject({
  resource: workloadDetailResourceRefSchema,
  event_type: nullableTextSchema,
  reason: nullableTextSchema,
  occurrence_count: z.number().int().nonnegative().nullable(),
  last_occurred_at: nullableTextSchema,
});

const workloadEventCollectionSchema = z.strictObject({
  availability: availabilitySchema,
  items: z.array(workloadEventObservationSchema),
  excluded_count: z.number().int().nonnegative(),
  reason_codes: reasonCodesSchema,
});

const capabilitySetSchema = z.strictObject({
  scope: workloadDetailScopeSchema,
  resource: workloadDetailResourceRefSchema,
  revision: z.string().min(1),
  actions: z.array(z.string().min(1)),
});

export const workloadDetailSchema = z.strictObject({
  detail: z.strictObject({
    scope: workloadDetailScopeSchema,
    observation: workloadObservationSchema,
    coverage: workloadDetailCoverageSchema,
    pods: workloadPodCollectionSchema,
    events: workloadEventCollectionSchema,
    log_stream: workloadLogStreamSchema,
    capabilities: capabilitySetSchema,
    features: z.array(workloadDetailFeatureSchema),
  }),
});

export type WorkloadDetailEndpoint = z.infer<typeof workloadDetailSchema>;
