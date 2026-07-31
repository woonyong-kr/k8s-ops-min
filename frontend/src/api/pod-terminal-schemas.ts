import { z } from "zod";

const sessionId = z.string().min(1).max(64).regex(/^[A-Za-z0-9_-]+$/u);

export const podTerminalEventSchema = z.discriminatedUnion("type", [
  z.strictObject({
    type: z.literal("terminal.connected"),
    session_id: sessionId,
  }),
  z.strictObject({
    type: z.literal("terminal.output"),
    session_id: sessionId,
    stream: z.enum(["stdout", "stderr"]),
    data: z.string().min(1).max(8_192),
  }),
  z.strictObject({
    type: z.literal("terminal.end"),
    session_id: sessionId,
    exit_code: z.number().int().nullable(),
    reason: z.enum(["completed", "closed", "timeout", "output_limit"]),
  }),
  z.strictObject({
    type: z.literal("terminal.error"),
    session_id: sessionId.nullable(),
    code: z.enum([
      "agent_unavailable",
      "audit_unavailable",
      "exec_failed",
      "invalid_target",
      "output_limit",
      "session_limit",
      "timeout",
    ]),
    message: z.string().min(1).max(240),
    retryable: z.boolean(),
  }),
]);

export type PodTerminalEndpointEvent = z.infer<typeof podTerminalEventSchema>;
