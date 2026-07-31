import { z } from "zod";

export const resourceActionCapabilityIdSchema = z.string()
  .regex(/^[a-z][a-z0-9._-]*$/u)
  .max(160);

export const resourceCapabilityInputTypeSchema = z.enum(["boolean", "integer", "string"]);

export const resourceCapabilityExecutionSchema = z.enum([
  "command",
  "terminal",
  "resource-files",
]);

export const resourceCapabilityRequestContextSchema = z.enum([
  "simple",
  "exact-resource",
  "rollback",
]);

export const resourceCapabilityResultIntentSchema = z.enum([
  "refresh-resource",
  "resource-summary",
  "terminal-session",
  "resource-files",
]);

export const resourceCapabilityInputSchema = z.strictObject({
  key: z.string().regex(/^[a-z][a-z0-9_]*$/u).max(120),
  label: z.string().min(1).max(120),
  type: resourceCapabilityInputTypeSchema,
  required: z.boolean(),
  minimum: z.number().int().nullable(),
  maximum: z.number().int().nullable(),
  default: z.union([z.boolean(), z.number().int(), z.string(), z.null()]),
  prefill_result_key: z.string().min(1).max(120).regex(/^[a-z][a-z0-9_]*$/u).nullable(),
}).superRefine((input, context) => {
  if (input.minimum !== null && input.maximum !== null && input.minimum > input.maximum) {
    context.addIssue({ code: "custom", message: "minimum must not exceed maximum" });
  }
  if (input.type === "boolean" && input.default !== null && typeof input.default !== "boolean") {
    context.addIssue({ code: "custom", message: "boolean default must be a boolean" });
  }
  if (input.type === "integer" && input.default !== null && typeof input.default !== "number") {
    context.addIssue({ code: "custom", message: "integer default must be an integer" });
  }
  if (input.type === "string" && input.default !== null && typeof input.default !== "string") {
    context.addIssue({ code: "custom", message: "string default must be a string" });
  }
  if (typeof input.default === "number") {
    if (input.minimum !== null && input.default < input.minimum) {
      context.addIssue({ code: "custom", message: "default must not be below minimum" });
    }
    if (input.maximum !== null && input.default > input.maximum) {
      context.addIssue({ code: "custom", message: "default must not exceed maximum" });
    }
  }
});

export const resourceCapabilitySubjectSchema = z.strictObject({
  resource_id: z.string().min(1),
  snapshot_id: z.string().min(1),
  cluster_id: z.string().min(1),
  resource_type: z.string().min(1),
  kind: z.string().min(1),
  namespace: z.string().min(1).nullable(),
  name: z.string().min(1),
});

export const resourceActionCapabilitySchema = z.strictObject({
  capability_id: resourceActionCapabilityIdSchema,
  label: z.string().min(1).max(120),
  description: z.string().min(1).max(500),
  execution: resourceCapabilityExecutionSchema,
  confirmation_required: z.boolean(),
  realtime: z.boolean(),
  input_schema: z.array(resourceCapabilityInputSchema),
  method: z.enum(["POST", "WEBSOCKET"]),
  path: z.string().min(1).regex(/^\//u),
  request_context: resourceCapabilityRequestContextSchema,
  result_intent: resourceCapabilityResultIntentSchema,
});

export const resourceCapabilitiesSchema = z.strictObject({
  subject: resourceCapabilitySubjectSchema,
  revision: z.string().regex(/^[0-9a-f]{64}$/u),
  capabilities: z.array(resourceActionCapabilitySchema),
}).superRefine((response, context) => {
  const ids = response.capabilities.map((item) => item.capability_id);
  if (new Set(ids).size !== ids.length) {
    context.addIssue({
      code: "custom",
      message: "resource capabilities must be unique",
      path: ["capabilities"],
    });
  }
  if (ids.some((id, index) => index > 0 && id <= ids[index - 1]!)) {
    context.addIssue({
      code: "custom",
      message: "resource capabilities must be sorted",
      path: ["capabilities"],
    });
  }
});

export type ResourceCapabilitiesEndpoint = z.infer<typeof resourceCapabilitiesSchema>;
export type ResourceActionCapabilityId = z.infer<typeof resourceActionCapabilityIdSchema>;
export type ResourceActionCapabilityEndpoint = z.infer<typeof resourceActionCapabilitySchema>;
export type ResourceCapabilityInputEndpoint = z.infer<typeof resourceCapabilityInputSchema>;
export type ResourceCapabilityInputType = z.infer<typeof resourceCapabilityInputTypeSchema>;
export type ResourceCapabilityExecution = z.infer<typeof resourceCapabilityExecutionSchema>;
export type ResourceCapabilityRequestContext = z.infer<typeof resourceCapabilityRequestContextSchema>;
export type ResourceCapabilityResultIntent = z.infer<typeof resourceCapabilityResultIntentSchema>;
