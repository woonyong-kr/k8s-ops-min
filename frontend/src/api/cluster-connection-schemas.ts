import { z } from "zod";

import { clusterAgentStatusSchema } from "./cluster-schemas";
import { connectionStageSchema } from "./cluster-stage-schemas";

/** Runtime contract for `GET /clusters/{cluster_id}/connection-status`. */
export const clusterConnectionStatusSchema = z.strictObject({
  cluster_id: z.string(),
  connection_status: z.string(),
  connection_stage: connectionStageSchema.optional(),
  last_agent_id: z.string().nullable(),
  last_seen_at: z.string().nullable(),
  agents: z.array(clusterAgentStatusSchema),
  connect_timeout_seconds: z.number().int().nullable(),
  connect_expires_at: z.string().nullable(),
});

export type ClusterConnectionStatus = z.infer<
  typeof clusterConnectionStatusSchema
>;
