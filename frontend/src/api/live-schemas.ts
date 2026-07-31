import { z } from "zod";

import { liveMetricSourceSchema } from "./live-resource-schemas";

export {
  liveClusterResourceObservationSchema,
  liveNodeResourceObservationSchema,
} from "./live-resource-schemas";
export type {
  LiveClusterResourceObservation,
  LiveNodeResourceObservation,
} from "./live-resource-schemas";

const nonNegativeIntegerSchema = z.number().int().nonnegative();
const openObjectSchema = z.record(z.string(), z.unknown());

export const realtimeProtocolSchema = z.literal("realtime.v1");

export const liveSubscriptionSchema = z.strictObject({
  workspaceId: z.string().trim().min(1),
  clusterId: z.string().trim().optional(),
  namespace: z.string().trim().optional(),
  app: z.string().trim().optional(),
});

export const hotPodSchema = z.strictObject({
  namespace: z.string().min(1),
  pod: z.string().min(1),
  cpu_ratio: z.number().nonnegative().nullable(),
  restart_count: nonNegativeIntegerSchema,
  ready: z.boolean(),
});

export const liveMetricsMetadataSchema = z.strictObject({
  source: liveMetricSourceSchema,
  actual_interval_seconds: z.number().finite().nonnegative().nullable(),
  degraded_reason: z.string().min(1).nullable(),
});

export const liveSummarySchema = z.strictObject({
  cluster_id: z.string().min(1),
  window_ms: nonNegativeIntegerSchema.max(60_000),
  pods_ready: nonNegativeIntegerSchema,
  pods_total: nonNegativeIntegerSchema,
  restart_delta: nonNegativeIntegerSchema,
  rollout_phase: z.enum(["idle", "progressing", "degraded"]),
  hot_pods: z.array(hotPodSchema).max(20),
  metrics_metadata: liveMetricsMetadataSchema.nullable().optional(),
});

export const realtimeStreamPolicySchema = z.strictObject({
  revision: z.number().int().positive(),
  max_frames_per_second: z.number().int().min(1).max(60),
  hidden_tab: z.literal("coalesce"),
  max_pending_messages: z.number().int().positive(),
});

export const helloMessageSchema = z.strictObject({
  type: z.literal("hello"),
  protocol: realtimeProtocolSchema,
  stream_policy: realtimeStreamPolicySchema,
});

export const snapshotMessageSchema = z.strictObject({
  type: z.literal("snapshot"),
  seq: nonNegativeIntegerSchema,
  // Snapshot state is intentionally the only open top-level read-model map.
  state: openObjectSchema,
});

export const liveSummaryMessageSchema = z.strictObject({
  type: z.literal("live.summary"),
  seq: nonNegativeIntegerSchema,
  cluster_id: z.string().min(1),
  summary: liveSummarySchema,
});

export const resourceDeltaMessageSchema = z.strictObject({
  type: z.literal("resource.delta"),
  seq: nonNegativeIntegerSchema,
  op: z.enum(["replace", "remove"]),
  key: z.string().min(1),
  // Resource payloads are provider-neutral open maps at this wire boundary.
  value: openObjectSchema.nullable(),
  // Optional during rolling upgrades; new agents stamp the real collection time.
  observed_at: z.string().datetime({ offset: true }).nullable().optional(),
});

export const pingMessageSchema = z.strictObject({
  type: z.literal("ping"),
  ts: z.number(),
});

export const realtimeMessageSchema = z.discriminatedUnion("type", [
  helloMessageSchema,
  snapshotMessageSchema,
  liveSummaryMessageSchema,
  resourceDeltaMessageSchema,
  pingMessageSchema,
]);

export type LiveSubscription = z.infer<typeof liveSubscriptionSchema>;
export type HotPod = z.infer<typeof hotPodSchema>;
export type LiveMetricsMetadata = z.infer<typeof liveMetricsMetadataSchema>;
export type RealtimeStreamPolicy = z.infer<typeof realtimeStreamPolicySchema>;
export type LiveSummary = z.infer<typeof liveSummarySchema>;
export type HelloMessage = z.infer<typeof helloMessageSchema>;
export type SnapshotMessage = z.infer<typeof snapshotMessageSchema>;
export type LiveSummaryMessage = z.infer<typeof liveSummaryMessageSchema>;
export type ResourceDeltaMessage = z.infer<typeof resourceDeltaMessageSchema>;
export type PingMessage = z.infer<typeof pingMessageSchema>;
export type RealtimeMessage = z.infer<typeof realtimeMessageSchema>;
export type RealtimeConnectionStatus =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected"
  | "closed";

export interface RealtimeSequenceState {
  readonly helloReceived: boolean;
  readonly snapshotReceived: boolean;
  readonly connected: boolean;
  readonly lastSequence: number | null;
}

export type RealtimeSequenceReduction =
  | { readonly accepted: true; readonly state: RealtimeSequenceState }
  | {
      readonly accepted: false;
      readonly reason: "stale-or-duplicate-sequence";
      readonly state: RealtimeSequenceState;
    };

export function parseRealtimeMessage(value: unknown): RealtimeMessage {
  return realtimeMessageSchema.parse(value);
}

export function parseRealtimeTextFrame(rawData: unknown): RealtimeMessage {
  if (typeof rawData !== "string") {
    throw new TypeError("Realtime payload must be a JSON text frame.");
  }
  return parseRealtimeMessage(JSON.parse(rawData) as unknown);
}

export function createRealtimeSequenceState(): RealtimeSequenceState {
  return {
    helloReceived: false,
    snapshotReceived: false,
    connected: false,
    lastSequence: null,
  };
}

/**
 * Applies the hub-global sequence contract. Filtered subscriptions legitimately
 * have gaps, while snapshots are authoritative recovery cuts and always reset.
 */
export function reduceRealtimeSequence(
  state: RealtimeSequenceState,
  message: RealtimeMessage,
): RealtimeSequenceReduction {
  if (message.type === "hello") {
    return {
      accepted: true,
      state: { ...state, helloReceived: true, connected: state.snapshotReceived },
    };
  }
  if (message.type === "snapshot") {
    return {
      accepted: true,
      state: {
        ...state,
        snapshotReceived: true,
        connected: state.helloReceived,
        lastSequence: message.seq,
      },
    };
  }
  if (message.type === "ping") return { accepted: true, state };
  if (state.lastSequence !== null && message.seq <= state.lastSequence) {
    return { accepted: false, reason: "stale-or-duplicate-sequence", state };
  }
  return {
    accepted: true,
    state: { ...state, lastSequence: message.seq },
  };
}
