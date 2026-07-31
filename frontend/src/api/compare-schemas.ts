import { z } from "zod";

const availabilitySchema = z.enum(["available", "partial", "unavailable"]);
const freshnessSchema = z.enum(["live", "stale", "partial", "disconnected"]);
const nullableTextSchema = z.string().min(1).nullable();
const reasonCodesSchema = z.array(z.string().min(1));

export const compareResourceRefSchema = z.strictObject({
  api_group: z.string(),
  version: z.string().min(1),
  kind: z.string().min(1),
  namespace: nullableTextSchema,
  name: z.string().min(1),
  uid: z.string().min(1),
});

export const compareDescriptorSchema = z.strictObject({
  route_kind: z.string().min(1),
  api_group: z.string(),
  api_version: z.string().min(1),
  kubernetes_kind: z.string().min(1),
  resource_type: z.string().min(1),
  projection_kind: z.enum(["workload_replicas", "service_ports"]),
});

const compareScopeSchema = z.strictObject({
  workspace_id: z.string().min(1),
  cluster_id: z.string().min(1),
  namespaces: z.array(z.string().min(1)),
  freshness: freshnessSchema,
});

const compareProvenanceSchema = z.strictObject({
  source_kind: z.literal("inventory_snapshot"),
  observation_snapshot_id: z.string().min(1),
  latest_snapshot_id: z.string().min(1),
  observed_at: nullableTextSchema,
  availability: availabilitySchema,
  reason_codes: reasonCodesSchema,
});

const workloadReplicaProjectionSchema = z.strictObject({
  projection_kind: z.literal("workload_replicas"),
  replicas: z.number().int().nonnegative().nullable(),
});

const servicePortProjectionSchema = z.strictObject({
  name: nullableTextSchema,
  port: z.number().int().min(1).max(65535),
  protocol: z.enum(["TCP", "UDP", "SCTP"]).nullable(),
  target_port_name: nullableTextSchema,
  target_port_number: z.number().int().min(1).max(65535).nullable(),
  node_port: z.number().int().min(1).max(65535).nullable(),
});

const servicePortsProjectionSchema = z.strictObject({
  projection_kind: z.literal("service_ports"),
  service_type: z.enum(["ClusterIP", "NodePort", "LoadBalancer", "ExternalName"]).nullable(),
  ports: z.array(servicePortProjectionSchema),
  excluded_port_count: z.number().int().nonnegative(),
});

const comparableManifestSchema = z.strictObject({
  projection_version: z.literal("safe-manifest-v1"),
  resource: compareResourceRefSchema,
  metadata: z.strictObject({
    name: z.string().min(1),
    namespace: nullableTextSchema,
  }),
  projection: z.discriminatedUnion("projection_kind", [
    workloadReplicaProjectionSchema,
    servicePortsProjectionSchema,
  ]),
  provenance: compareProvenanceSchema,
  omitted_paths: z.array(z.string().min(1)),
});

const compareCoverageSchema = z.strictObject({
  availability: availabilitySchema,
  latest_snapshot_id: z.string().min(1),
  reason_codes: reasonCodesSchema,
});

const comparePresentationSchema = z.strictObject({
  modes: z.array(z.enum(["side-by-side", "unified"])),
  swap: z.literal(true),
  diff_only: z.literal(true),
});

export const compareResourcePairSchema = z.strictObject({
  comparison: z.strictObject({
    scope: compareScopeSchema,
    descriptor: compareDescriptorSchema,
    coverage: compareCoverageSchema,
    presentation: comparePresentationSchema,
    a: comparableManifestSchema,
    b: comparableManifestSchema,
  }),
});

export const compareCandidateListSchema = z.strictObject({
  result: z.strictObject({
    scope: compareScopeSchema,
    descriptor: compareDescriptorSchema,
    coverage: compareCoverageSchema,
    candidates: z.array(z.strictObject({
      resource: compareResourceRefSchema,
      provenance: compareProvenanceSchema,
    })),
    excluded_count: z.number().int().nonnegative(),
  }),
});

export const compareDescriptorListSchema = z.strictObject({
  descriptors: z.array(compareDescriptorSchema),
});

export type CompareResourcePairEndpoint = z.infer<typeof compareResourcePairSchema>;
export type CompareCandidateListEndpoint = z.infer<typeof compareCandidateListSchema>;
export type CompareDescriptorListEndpoint = z.infer<typeof compareDescriptorListSchema>;
