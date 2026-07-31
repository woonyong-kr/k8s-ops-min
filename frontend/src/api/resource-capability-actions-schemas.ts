import { z } from "zod";

export const resourceActionAcceptedSchema = z
  .strictObject({
    accepted: z.literal(true),
    event_id: z.string().min(1),
    audit_event_id: z.string().min(1),
    correlation_id: z.string().min(1),
    command_id: z.string().min(1),
    status: z.enum([
      "queued",
      "leased",
      "running",
      "cancel_requested",
      "cancelling",
      "completed",
      "failed",
      "cancelled",
    ]),
  })
  .refine((receipt) => receipt.audit_event_id === receipt.event_id, {
    message: "audit event must be the accepted command event",
  });

export type ResourceActionAccepted = z.infer<typeof resourceActionAcceptedSchema>;
