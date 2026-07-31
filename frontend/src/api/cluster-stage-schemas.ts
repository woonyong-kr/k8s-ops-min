import { z } from "zod";

export const connectionStageSchema = z.enum([
  "token_issued",
  "awaiting_install",
  "agent_connected",
  "snapshot_received",
  "ready",
  "expired",
  "error",
]);

export type ConnectionStage = z.infer<typeof connectionStageSchema>;
