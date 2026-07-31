import { z } from "zod";

import { apiRequest, type ApiPath } from "./client";
import { clusterListSchema, type ClusterList } from "./cluster-schemas";
import { encodePathSegment, withQuery } from "./url";

const DEFAULT_CLUSTER_LIMIT = 100;

export interface ListClustersOptions {
  limit?: number;
}

export interface UnregisterClusterOptions {
  manualCleanupAttested?: boolean;
}

const clusterUnregisterResponseSchema = z.strictObject({
  cluster_id: z.string().min(1),
  status: z.enum(["uninstalling", "cleanup_required", "disconnected", "purged"]),
  stage: z.enum([
    "agent_cleanup_queued",
    "agent_cleanup_pending",
    "registration_revoked",
    "purged",
  ]),
  command_id: z.string().min(1).nullable(),
  command_status_path: z.string().min(1).nullable(),
  // Older management planes do not expose a manual command or expanded
  // resource list. Defaults preserve that absence instead of failing the
  // entire unregister response or inventing browser-side cleanup evidence.
  uninstall_command: z.string().min(1).nullable().optional().default(null),
  cleanup_verified: z.boolean(),
  resources: z.array(z.string()).optional().default([]),
  residual_resources: z.array(z.string()).optional().default([]),
  failure_reason: z.string().nullable(),
});

export type ClusterUnregisterResponse = z.output<typeof clusterUnregisterResponseSchema>;

/**
 * Lists the session-visible clusters used by the Home cluster selector.
 * Backend source of truth: `src/domains/target/router.py::list_clusters`.
 */
export function listClusters(
  options: ListClustersOptions = {},
  signal?: AbortSignal,
): Promise<ClusterList> {
  const path = withQuery("/api/clusters" satisfies ApiPath, [
    ["limit", options.limit ?? DEFAULT_CLUSTER_LIMIT],
  ]);
  return apiRequest(path, clusterListSchema, { signal });
}

/**
 * Stops an installed target agent without ever exposing physical fixture purge.
 * Manual cleanup is an explicit operator attestation, never an inferred retry.
 */
export function unregisterCluster(
  clusterId: string,
  options: UnregisterClusterOptions = {},
  signal?: AbortSignal,
): Promise<ClusterUnregisterResponse> {
  const normalized = clusterId.trim();
  if (!normalized) throw new TypeError("clusterId must not be empty");
  const path = withQuery(
    `/api/clusters/${encodePathSegment(normalized)}` as ApiPath,
    [
      ["purge", false],
      ["manual_cleanup_attested", options.manualCleanupAttested ? true : null],
    ],
  );
  return apiRequest(path, clusterUnregisterResponseSchema, { method: "DELETE", signal });
}
