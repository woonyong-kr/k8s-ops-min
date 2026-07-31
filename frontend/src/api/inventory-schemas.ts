import { z } from "zod";

import { kubernetesResourceAccessResponseSchema } from "./resource-access-schemas";

const nullableStringSchema = z.string().nullable();
const unknownRecordSchema = z.record(z.string(), z.unknown());

const resourceProviderTypeSchema = z.enum([
  "aws-machine", "aws-managed-cluster", "aws-managed-control-plane", "aws-managed-machine-pool",
  "azure-machine", "azure-managed-control-plane", "azure-managed-machine-pool",
  "capi-cluster", "capi-kubeadm-control-plane", "capi-machine", "capi-machine-deployment",
  "capi-machine-health-check", "capi-machine-pool", "capi-machine-set", "certificate",
  "certificate-request", "cluster-compliance-report", "cron-workflow", "crossplane-composite",
  "crossplane-managed-resource", "external-secret", "gateway-class", "gcp-machine",
  "gcp-managed-control-plane", "gcp-managed-machine-pool", "grpc-route", "http-route", "job",
  "karpenter-ec2-node-class", "karpenter-node-claim", "karpenter-node-pool", "keda-scaled-job",
  "keda-scaled-object", "persistent-volume-claim", "prometheus-rule", "sbom-report",
  "sealed-secret", "secret", "secret-store", "tcp-route", "tls-route", "vulnerability-report",
  "workflow",
]);

// Provider별 중첩 projection은 매우 넓지만 최상위 discriminator는 서버 union과 정확히
// 일치시킨다. 알 수 없는 provider type이나 비객체 payload는 런타임에서 즉시 거부한다.
const resourceProviderDetailSchema = z.object({
  type: resourceProviderTypeSchema,
}).catchall(z.unknown());

/**
 * Runtime contract for `InventoryResourceResponse` from
 * `src/packages/contracts/gateway/responses.py`. Nested Kubernetes projections
 * remain unknown until a feature-specific schema narrows them.
 */
export const inventoryResourceSchema = z.strictObject({
  inventory_key: z.string(),
  snapshot_id: z.string(),
  workspace_id: z.string(),
  cluster_id: z.string(),
  resource_type: z.string(),
  api_version: z.string(),
  kind: z.string(),
  namespace: nullableStringSchema,
  name: z.string(),
  uid: nullableStringSchema,
  resource_version: nullableStringSchema,
  status: z.string(),
  health: z.string(),
  labels: unknownRecordSchema,
  annotations: unknownRecordSchema,
  summary: unknownRecordSchema,
  observed_at: nullableStringSchema,
  first_seen_at: nullableStringSchema,
  last_seen_at: nullableStringSchema,
  deleted_at: nullableStringSchema,
  created_at: nullableStringSchema,
  updated_at: nullableStringSchema,
});

/** Runtime contract shared by Home and Resources inventory lists. */
export const inventoryResourceListSchema = z.strictObject({
  cluster_id: z.string(),
  resource_type: nullableStringSchema,
  resources: z.array(inventoryResourceSchema),
});

/** Runtime contract consumed by a Resources inline detail expansion. */
export const inventoryResourceDetailSchema = z.strictObject({
  cluster_id: z.string(),
  identity: unknownRecordSchema,
  resource: inventoryResourceSchema,
  // 순차 배포 중 구버전 gateway는 nullable projection 자체를 생략할 수 있다. 생략은
  // "관측 안 됨"(null)으로 정규화하되, 값이 존재하면 discriminator를 엄격히 검증한다.
  // 이 경계가 partial 200 응답을 raw invalid-payload로 오인하지 않게 한다.
  provider_detail: resourceProviderDetailSchema.nullable().optional().default(null),
  access: kubernetesResourceAccessResponseSchema.nullable().optional().default(null),
  related: z.record(z.string(), z.array(inventoryResourceSchema)),
  events: z.array(inventoryResourceSchema),
});

export type InventoryResource = z.infer<typeof inventoryResourceSchema>;
export type InventoryResourceList = z.infer<typeof inventoryResourceListSchema>;
export type InventoryResourceDetail = z.infer<typeof inventoryResourceDetailSchema>;
