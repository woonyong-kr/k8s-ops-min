import { apiRequest, type ApiPath } from "./client";
import {
  commandAcceptedSchema,
  commandControlAcceptedSchema,
  type CommandAccepted,
  type CommandControlAccepted,
} from "./commands-schemas";
import { encodePathSegment } from "./url";

export interface SubmitCommandInput {
  clusterId: string;
  action: string;
  namespace: string;
  reason?: string | null;
  diff?: Record<string, unknown> | null;
  approvalRef?: string | null;
  policyDecisionRef?: string | null;
  /** One-time acknowledgement of the inspected target and computed impact. */
  confirmation: true;
}

export interface SubmitCommandOptions {
  signal?: AbortSignal;
}

export interface CommandControlInput {
  commandId: string;
  /** Caller-owned key keeps retried HTTP delivery idempotent at the gateway. */
  idempotencyKey: string;
  reason?: string | null;
}

export interface CommandControlOptions {
  signal?: AbortSignal;
}

/** Queues one allowlisted operational command for the target Cluster Agent. */
export function submitCommand(
  input: SubmitCommandInput,
  options: SubmitCommandOptions = {},
): Promise<CommandAccepted> {
  assertRequiredIdentifier(input.clusterId, "clusterId");
  assertRequiredIdentifier(input.action, "action");
  assertRequiredIdentifier(input.namespace, "namespace");

  const path = "/api/commands" as ApiPath;
  return apiRequest(path, commandAcceptedSchema, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      cluster_id: input.clusterId,
      action: input.action,
      namespace: input.namespace,
      reason: input.reason,
      diff: input.diff,
      approval_ref: input.approvalRef,
      policy_decision_ref: input.policyDecisionRef,
      confirmation: input.confirmation,
    }),
    signal: options.signal,
  });
}

/** Requests a cooperative cancellation; terminal state still arrives through SSE. */
export function cancelCommand(
  input: CommandControlInput,
  options: CommandControlOptions = {},
): Promise<CommandControlAccepted> {
  return submitCommandControl("cancel", input, options);
}

/** Queues a policy-allowed new attempt for a failed logical command. */
export function retryCommand(
  input: CommandControlInput,
  options: CommandControlOptions = {},
): Promise<CommandControlAccepted> {
  return submitCommandControl("retry", input, options);
}

function submitCommandControl(
  action: "cancel" | "retry",
  input: CommandControlInput,
  options: CommandControlOptions,
): Promise<CommandControlAccepted> {
  assertRequiredIdentifier(input.commandId, "commandId");
  if (input.idempotencyKey.trim().length < 8) {
    throw new TypeError("command idempotencyKey must contain at least 8 characters");
  }
  const path = `/api/commands/${encodePathSegment(input.commandId.trim())}/${action}` as ApiPath;
  return apiRequest(path, commandControlAcceptedSchema, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "idempotency-key": input.idempotencyKey,
    },
    body: JSON.stringify({ reason: input.reason ?? undefined }),
    signal: options.signal,
  });
}

function assertRequiredIdentifier(value: string, field: string): void {
  if (!value.trim()) {
    throw new TypeError(`command ${field} is required`);
  }
}
