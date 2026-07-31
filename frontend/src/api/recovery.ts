import { apiRequest, type ApiPath } from "./client";
import {
  recoveryActionAcceptedSchema,
  recoveryPlanSchema,
  type RecoveryActionAccepted,
  type RecoveryPlan,
} from "./recovery-schemas";
import { encodePathSegment } from "./url";

export interface SelectRecoveryActionInput {
  reason?: string | null;
}

export interface RetryRecoveryInput {
  reason?: string | null;
}

export interface RecoveryRequestOptions {
  signal?: AbortSignal;
}

/** Loads the recovery candidates generated for one Incident correlation. */
export function getRecoveryPlanByCorrelation(
  correlationId: string,
  options: RecoveryRequestOptions = {},
): Promise<RecoveryPlan> {
  const path =
    `/api/rca/recovery-plans/by-correlation/${encodePathSegment(correlationId)}?include_lifecycle=true` as ApiPath;
  return apiRequest(path, recoveryPlanSchema, { signal: options.signal });
}

/** Selects one recovery candidate without executing it directly. */
export function selectRecoveryAction(
  correlationId: string,
  planId: string,
  actionId: string,
  input: SelectRecoveryActionInput = {},
  options: RecoveryRequestOptions = {},
): Promise<RecoveryActionAccepted> {
  const path =
    `/api/rca/recovery-plans/by-correlation/${encodePathSegment(correlationId)}/actions/select` as ApiPath;
  const body = {
    expected_plan_id: planId,
    action_id: actionId,
    ...(input.reason === undefined ? {} : { reason: input.reason }),
  };
  return apiRequest(path, recoveryActionAcceptedSchema, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

/** Explicitly retries only the failed backend-owned deploy or verification stage. */
export function retryRecovery(
  correlationId: string,
  planId: string,
  input: RetryRecoveryInput = {},
  options: RecoveryRequestOptions = {},
): Promise<RecoveryActionAccepted> {
  const path =
    `/api/rca/recovery-plans/by-correlation/${encodePathSegment(correlationId)}/retry` as ApiPath;
  const body = {
    expected_plan_id: planId,
    ...(input.reason === undefined ? {} : { reason: input.reason }),
  };
  return apiRequest(path, recoveryActionAcceptedSchema, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal: options.signal,
  });
}
