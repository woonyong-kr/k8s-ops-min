import { apiRequest, type ApiPath } from "./client";
import {
  auditTimelineResponseSchema,
  type AuditTimelineResponse,
} from "./audit-timeline-schemas";
import { withQuery } from "./url";

export const AUDIT_TIMELINE_DEFAULT_LIMIT = 50;
export const AUDIT_TIMELINE_MAX_LIMIT = 200;
export const AUDIT_TIMELINE_PATH = "/api/audit/timeline" as ApiPath;

export interface GetAuditTimelineOptions {
  cursor?: string;
  limit?: number;
  signal?: AbortSignal;
}

/** Loads one server-ordered page of the audit chain for a correlation. */
export async function getAuditTimeline(
  correlationId: string,
  options: GetAuditTimelineOptions = {},
): Promise<AuditTimelineResponse> {
  assertOpaqueValue(correlationId, "correlation_id");
  if (options.cursor !== undefined) {
    assertOpaqueValue(options.cursor, "cursor");
  }

  const limit = options.limit ?? AUDIT_TIMELINE_DEFAULT_LIMIT;
  assertAuditTimelineLimit(limit);
  const path = withQuery(AUDIT_TIMELINE_PATH, [
    ["correlation_id", correlationId],
    ["cursor", options.cursor],
    ["limit", limit],
  ]);

  return apiRequest(path, auditTimelineResponseSchema, {
    signal: options.signal,
  });
}

function assertOpaqueValue(value: string, field: string): void {
  if (value.trim() === "") {
    throw new TypeError(`${field} must not be blank`);
  }
}

function assertAuditTimelineLimit(limit: number): void {
  if (
    !Number.isInteger(limit)
    || limit < 1
    || limit > AUDIT_TIMELINE_MAX_LIMIT
  ) {
    throw new RangeError(
      `Audit timeline limit must be an integer from 1 to ${AUDIT_TIMELINE_MAX_LIMIT}`,
    );
  }
}
