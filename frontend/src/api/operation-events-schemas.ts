import { z } from "zod";

export const commandOperationEventSchema = z.strictObject({
  command_id: z.string().min(1),
  sequence: z.number().int().nonnegative(),
  kind: z.enum(["progress", "log", "completed", "failed", "cancelled"]),
  payload: z.record(z.string(), z.unknown()),
  occurred_at: z.string().datetime({ offset: true }),
});

export type CommandOperationEventEndpoint = z.infer<typeof commandOperationEventSchema>;
