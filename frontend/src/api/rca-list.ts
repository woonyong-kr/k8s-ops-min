import { apiRequest, type ApiPath } from "./client";
import { rcaListSchema, type RcaList } from "./rca-list-schemas";
import { optionalQueryString, withQuery } from "./url";

export const RCA_LIST_DEFAULT_LIMIT = 50;
export const RCA_LIST_MAX_LIMIT = 100;

export interface ListRcaTimelineOptions {
  clusterId?: string;
  limit?: number;
  signal?: AbortSignal;
}

/** Loads the full Issues/RCA list, optionally scoped to one cluster. */
export async function listRcaTimeline(
  options: ListRcaTimelineOptions = {},
): Promise<RcaList> {
  const limit = options.limit ?? RCA_LIST_DEFAULT_LIMIT;
  assertRcaListLimit(limit);
  const path = withQuery("/api/dashboard/rca/timeline" as ApiPath, [
    ["cluster_id", optionalQueryString(options.clusterId)],
    ["limit", limit],
  ]);
  return apiRequest(path, rcaListSchema, { signal: options.signal });
}

function assertRcaListLimit(limit: number): void {
  if (!Number.isInteger(limit) || limit < 1 || limit > RCA_LIST_MAX_LIMIT) {
    throw new RangeError(
      `RCA list limit must be an integer from 1 to ${RCA_LIST_MAX_LIMIT}`,
    );
  }
}
