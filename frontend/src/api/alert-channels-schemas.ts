import { z } from "zod";

import { rfc3339TimestampSchema } from "./resource-filter-schemas";

export const alertChannelSchema = z.strictObject({
  channel_id: z.string().min(1),
  workspace_id: z.string().min(1),
  name: z.string().min(1),
  kind: z.string().min(1),
  url: z.string().min(1).max(2000),
  min_severity: z.string().min(1),
  enabled: z.boolean(),
  last_tested_at: rfc3339TimestampSchema.nullable(),
  last_test_status: z.string().min(1).nullable(),
  last_test_detail: z.string().min(1).nullable(),
  last_test_status_code: z.number().int().nullable(),
  created_at: rfc3339TimestampSchema.nullable(),
  updated_at: rfc3339TimestampSchema.nullable(),
});

export const alertChannelListSchema = z.strictObject({
  channels: z.array(alertChannelSchema),
});

export const alertChannelTestRequestSchema = z.strictObject({
  channel_id: z.string().default(""),
  name: z.string().min(1).max(120).default("test"),
  kind: z.literal("webhook").default("webhook"),
  url: z.string().min(1).max(2000),
  min_severity: z.enum(["info", "warning", "critical"]).default("warning"),
  severity: z.enum(["info", "warning", "critical"]).default("warning"),
  message: z.string().max(500).default("알림 채널 테스트"),
});

export const alertChannelTestResponseSchema = z.strictObject({
  valid: z.boolean(),
  delivered: z.boolean(),
  code: z.string().min(1).nullable(),
  detail: z.string(),
  status_code: z.number().int().nullable(),
  channel: alertChannelSchema.nullable(),
});

export type AlertChannel = z.output<typeof alertChannelSchema>;
export type AlertChannelList = z.output<typeof alertChannelListSchema>;
export type AlertChannelTestInput = z.input<typeof alertChannelTestRequestSchema>;
export type AlertChannelTestResponse = z.output<typeof alertChannelTestResponseSchema>;
