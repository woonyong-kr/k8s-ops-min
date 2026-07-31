import { z } from "zod";

const safeText = z.string().min(1);

export const logStreamConnectedSchema = z.strictObject({
  type: z.literal("connected"),
  stream_id: safeText,
});

export const logStreamLineSchema = z.strictObject({
  type: z.literal("log"),
  id: safeText,
  observed_at: z.string().datetime({ offset: true }),
  pod: safeText,
  container: z.string().min(1),
  line: z.string().max(4096),
  line_truncated: z.boolean(),
});

export const logStreamPodMembershipSchema = z.discriminatedUnion("type", [
  z.strictObject({ type: z.literal("pod_added"), pod: safeText }),
  z.strictObject({ type: z.literal("pod_removed"), pod: safeText }),
]);

export const logStreamEndSchema = z.strictObject({
  type: z.literal("end"),
  reason: safeText,
});

export const logStreamErrorSchema = z.strictObject({
  type: z.literal("error"),
  code: safeText,
  retryable: z.boolean(),
});

export const logStreamEventSchema = z.discriminatedUnion("type", [
  logStreamConnectedSchema,
  logStreamLineSchema,
  ...logStreamPodMembershipSchema.options,
  logStreamEndSchema,
  logStreamErrorSchema,
]);

export type LogStreamEventEndpoint = z.infer<typeof logStreamEventSchema>;
