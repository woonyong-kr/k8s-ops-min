import { z } from "zod";

const commandLifecycleStatusSchema = z.enum([
  "queued",
  "leased",
  "running",
  "cancel_requested",
  "cancelling",
  "completed",
  "failed",
  "cancelled",
]);

export const commandAcceptedSchema = z
  .strictObject({
    accepted: z.literal(true),
    event_id: z.string().min(1),
    // Immutable command.requested event. The asynchronous audit projection stores
    // this same value as audit_log.event_id; it is deliberately not audit_log.id.
    audit_event_id: z.string().min(1),
    correlation_id: z.string().min(1),
    command_id: z.string().min(1),
    status: commandLifecycleStatusSchema,
  })
  .refine((receipt) => receipt.audit_event_id === receipt.event_id, {
    message: "audit event must be the accepted command event",
  });

export type CommandAccepted = z.infer<typeof commandAcceptedSchema>;

export const commandControlAcceptedSchema = z
  .strictObject({
    accepted: z.literal(true),
    command_id: z.string().min(1),
    action: z.enum(["cancel", "retry"]),
    event_id: z.string().min(1),
    audit_event_id: z.string().min(1),
    correlation_id: z.string().min(1),
    status: commandLifecycleStatusSchema,
    idempotent: z.boolean(),
    attempt_id: z.string().min(1).optional(),
  })
  .refine((receipt) => receipt.audit_event_id === receipt.event_id, {
    message: "audit event must be the accepted command control event",
  });

export type CommandControlAccepted = z.infer<typeof commandControlAcceptedSchema>;
