import { z } from "zod";

import {
  canonicalFacetSelections,
  canonicalLabelSelections,
} from "./resource-filter-query";
import { rfc3339TimestampSchema } from "./resource-filter-schemas";

const MAX_SCOPE_VALUE_LENGTH = 512;

const inputScopeValueSchema = z.string().min(1).max(MAX_SCOPE_VALUE_LENGTH)
  .transform((value) => value.trim())
  .pipe(z.string().min(1).max(MAX_SCOPE_VALUE_LENGTH).refine(
    (value) => !value.includes(","),
    "scope values cannot contain commas",
  ));

const responseScopeValueSchema = z.string().min(1).max(MAX_SCOPE_VALUE_LENGTH).refine(
  (value) => value === value.trim() && !value.includes(","),
  "scope values must be stable comma-free strings",
);

const inputScopeShape = {
  clusters: z.array(inputScopeValueSchema).max(50).default([]),
  namespaces: z.array(inputScopeValueSchema).max(100).default([]),
  applications: z.array(inputScopeValueSchema).max(100).default([]),
  labels: z.array(inputScopeValueSchema).max(24).default([]),
};

const responseScopeShape = {
  clusters: z.array(responseScopeValueSchema).max(50),
  namespaces: z.array(responseScopeValueSchema).max(100),
  applications: z.array(responseScopeValueSchema).max(100),
  labels: z.array(responseScopeValueSchema).max(24),
};

export const alertRuleScopeInputSchema = z.strictObject(inputScopeShape)
  .transform((scope, context) => canonicalScope(scope, context));

export const alertRuleScopeSchema = z.strictObject(responseScopeShape)
  .superRefine((scope, context) => {
    const canonical = canonicalScope(scope, context);
    if (canonical === z.NEVER) return;
    for (const axis of ["clusters", "namespaces", "applications", "labels"] as const) {
      if (sameValues(scope[axis], canonical[axis])) continue;
      context.addIssue({
        code: "custom",
        message: `${axis} must be unique and canonically sorted`,
        path: [axis],
      });
    }
  });

export const alertMetricSchema = z.enum([
  "cpu_pct",
  "mem_pct",
  "restart_count",
  "pod_not_ready",
]);
export const alertComparatorSchema = z.enum([">", ">=", "<", "<="]);
export const alertSeveritySchema = z.enum(["critical", "high", "medium", "low"]);

const ruleNameInputSchema = z.string().min(1).max(120)
  .transform((value) => normalizeWords(value))
  .pipe(z.string().min(1).max(120));
const ruleNameResponseSchema = z.string().min(1).max(120).refine(
  (value) => value === normalizeWords(value),
  "rule name must be normalized",
);
const channelIdInputSchema = z.string().min(1).max(120)
  .transform((value) => value.trim())
  .pipe(z.string().min(1).max(120));
const channelIdResponseSchema = z.string().min(1).max(120).refine(
  (value) => value === value.trim(),
  "channel id must be normalized",
);
const inputChannelsSchema = z.array(channelIdInputSchema).max(20)
  .transform((values) => [...new Set(values)].sort(compareCodePoints));
const responseChannelsSchema = z.array(channelIdResponseSchema).max(20).superRefine(
  (values, context) => {
    const canonical = [...new Set(values)].sort(compareCodePoints);
    if (sameValues(values, canonical)) return;
    context.addIssue({ code: "custom", message: "channels must be unique and sorted" });
  },
);

const alertRuleMutableInputShape = {
  name: ruleNameInputSchema,
  scope: alertRuleScopeInputSchema,
  metric: alertMetricSchema,
  comparator: alertComparatorSchema,
  threshold: z.number().finite().nonnegative(),
  for_seconds: z.number().int().min(1).max(86_400),
  severity: alertSeveritySchema,
  channels: inputChannelsSchema,
  enabled: z.boolean(),
};

export const alertRuleCreateRequestSchema = z.strictObject(alertRuleMutableInputShape);

export const alertRulePatchRequestSchema = z.strictObject({
  name: ruleNameInputSchema.optional(),
  scope: alertRuleScopeInputSchema.optional(),
  metric: alertMetricSchema.optional(),
  comparator: alertComparatorSchema.optional(),
  threshold: z.number().finite().nonnegative().optional(),
  for_seconds: z.number().int().min(1).max(86_400).optional(),
  severity: alertSeveritySchema.optional(),
  channels: inputChannelsSchema.optional(),
  enabled: z.boolean().optional(),
}).refine((changes) => Object.keys(changes).length > 0, {
  message: "alert rule patch cannot be empty",
});

export const alertRuleCreatedSchema = z.strictObject({
  rule_id: z.string().min(1).max(120),
});

export const alertRuleSchema = z.strictObject({
  rule_id: z.string().min(1).max(120),
  name: ruleNameResponseSchema,
  scope: alertRuleScopeSchema,
  metric: alertMetricSchema,
  comparator: alertComparatorSchema,
  threshold: z.number().finite().nonnegative(),
  for_seconds: z.number().int().min(1).max(86_400),
  severity: alertSeveritySchema,
  channels: responseChannelsSchema,
  enabled: z.boolean(),
  last_fired_at: rfc3339TimestampSchema.nullable(),
  occurrence_count: z.number().int().nonnegative(),
  created_by: z.string().min(1),
  created_at: rfc3339TimestampSchema,
  updated_at: rfc3339TimestampSchema,
});

export const alertRuleListSchema = z.strictObject({
  rules: z.array(alertRuleSchema),
});

export interface AlertRuleScopeInput {
  clusters?: readonly string[];
  namespaces?: readonly string[];
  applications?: readonly string[];
  labels?: readonly string[];
}

export interface AlertRuleCreateInput {
  name: string;
  scope: AlertRuleScopeInput;
  metric: AlertMetric;
  comparator: AlertComparator;
  threshold: number;
  for_seconds: number;
  severity: AlertSeverity;
  channels: readonly string[];
  enabled: boolean;
}

export interface AlertRulePatchInput {
  name?: string;
  scope?: AlertRuleScopeInput;
  metric?: AlertMetric;
  comparator?: AlertComparator;
  threshold?: number;
  for_seconds?: number;
  severity?: AlertSeverity;
  channels?: readonly string[];
  enabled?: boolean;
}

export type AlertRuleScope = z.output<typeof alertRuleScopeSchema>;
export type AlertRuleCreated = z.output<typeof alertRuleCreatedSchema>;
export type AlertRule = z.output<typeof alertRuleSchema>;
export type AlertRuleList = z.output<typeof alertRuleListSchema>;
export type AlertMetric = z.output<typeof alertMetricSchema>;
export type AlertComparator = z.output<typeof alertComparatorSchema>;
export type AlertSeverity = z.output<typeof alertSeveritySchema>;

function canonicalScope(
  scope: z.output<z.ZodObject<typeof inputScopeShape>>,
  context: z.RefinementCtx,
) {
  try {
    scope.namespaces.forEach(requireCanonicalNamespaceReference);
    return {
      clusters: [...canonicalFacetSelections("clusters", scope.clusters)],
      namespaces: [...canonicalFacetSelections("namespaces", scope.namespaces)],
      applications: [...canonicalFacetSelections("applications", scope.applications)],
      labels: [...canonicalLabelSelections(scope.labels)],
    };
  } catch (error) {
    context.addIssue({
      code: "custom",
      message: error instanceof Error ? error.message : "scope is not canonical",
    });
    return z.NEVER;
  }
}

function requireCanonicalNamespaceReference(value: string): void {
  const separator = value.lastIndexOf("/");
  if (
    separator <= 0 ||
    separator === value.length - 1 ||
    separator > 253 ||
    value.length - separator - 1 > 253
  ) {
    throw new TypeError("namespaces must use <cluster-id>/<namespace>");
  }
}

function normalizeWords(value: string): string {
  return value.trim().split(/\s+/u).join(" ");
}

function sameValues(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function compareCodePoints(left: string, right: string): number {
  const leftPoints = Array.from(left, (character) => character.codePointAt(0) ?? 0);
  const rightPoints = Array.from(right, (character) => character.codePointAt(0) ?? 0);
  const length = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < length; index += 1) {
    const difference = leftPoints[index] - rightPoints[index];
    if (difference !== 0) return difference;
  }
  return leftPoints.length - rightPoints.length;
}
