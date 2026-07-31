import { apiRequest, apiRequestNoContent, type ApiPath } from "./client";
import {
  alertRuleCreateRequestSchema,
  alertRuleCreatedSchema,
  alertRuleListSchema,
  alertRulePatchRequestSchema,
  alertRuleSchema,
  type AlertRule,
  type AlertRuleCreateInput,
  type AlertRuleCreated,
  type AlertRuleList,
  type AlertRulePatchInput,
} from "./alert-rules-schemas";
import { encodePathSegment } from "./url";

export const ALERT_RULES_PATH: ApiPath = "/api/alert-rules";

export function listAlertRules(signal?: AbortSignal): Promise<AlertRuleList> {
  return apiRequest(ALERT_RULES_PATH, alertRuleListSchema, { signal });
}

export function createAlertRule(
  input: AlertRuleCreateInput,
  signal?: AbortSignal,
): Promise<AlertRuleCreated> {
  const payload = alertRuleCreateRequestSchema.parse(input);
  return apiRequest(ALERT_RULES_PATH, alertRuleCreatedSchema, jsonMutation("POST", payload, signal));
}

export function updateAlertRule(
  ruleId: string,
  input: AlertRulePatchInput,
  signal?: AbortSignal,
): Promise<AlertRule> {
  const payload = alertRulePatchRequestSchema.parse(input);
  return apiRequest(alertRulePath(ruleId), alertRuleSchema, jsonMutation("PATCH", payload, signal));
}

export function deleteAlertRule(ruleId: string, signal?: AbortSignal): Promise<void> {
  return apiRequestNoContent(alertRulePath(ruleId), { method: "DELETE", signal });
}

function alertRulePath(ruleId: string): ApiPath {
  if (ruleId.trim() === "") throw new TypeError("ruleId must not be empty");
  return `${ALERT_RULES_PATH}/${encodePathSegment(ruleId)}` as ApiPath;
}

function jsonMutation(method: "POST" | "PATCH", payload: unknown, signal?: AbortSignal) {
  return {
    method,
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  } satisfies RequestInit;
}
