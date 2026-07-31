import { apiRequest, type ApiPath } from "./client";
import {
  recentChangeListResponseSchema,
  type RecentChangeListResponse,
} from "./recent-changes-schemas";
import { encodePathSegment, withQuery } from "./url";

export const RCA_RECENT_CHANGES_DEFAULT_LIMIT = 5;
export const RCA_RECENT_CHANGES_MAX_LIMIT = 50;
export const RCA_RECENT_CHANGES_PATH =
  "/api/rca/incidents/{incident_id}/recent-changes" as const;

export interface GetIncidentRecentChangesOptions {
  limit?: number;
  signal?: AbortSignal;
}

/** Loads server-correlated successful changes that precede one Incident. */
export async function getIncidentRecentChanges(
  incidentId: string,
  options: GetIncidentRecentChangesOptions = {},
): Promise<RecentChangeListResponse> {
  const identity = requiredIdentity(incidentId, "incident_id");
  const limit = options.limit ?? RCA_RECENT_CHANGES_DEFAULT_LIMIT;
  assertLimit(limit);
  const basePath = RCA_RECENT_CHANGES_PATH.replace(
    "{incident_id}",
    encodePathSegment(identity),
  ) as ApiPath;
  const path = withQuery(basePath, [["limit", limit]]);
  return apiRequest(path, recentChangeListResponseSchema, {
    signal: options.signal,
  });
}

function requiredIdentity(value: string, field: string): string {
  const normalized = value.trim();
  if (normalized === "") throw new TypeError(`${field} must not be blank`);
  return normalized;
}

function assertLimit(limit: number): void {
  if (
    !Number.isInteger(limit)
    || limit < 1
    || limit > RCA_RECENT_CHANGES_MAX_LIMIT
  ) {
    throw new RangeError(
      `Recent changes limit must be an integer from 1 to ${RCA_RECENT_CHANGES_MAX_LIMIT}`,
    );
  }
}
