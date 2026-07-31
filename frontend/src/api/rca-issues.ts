import { apiRequest, type ApiPath } from "./client";
import { rcaIssueListSchema, type RcaIssueList } from "./schemas";
import { optionalQueryString, withQuery } from "./url";

export const RCA_ISSUES_DEFAULT_LIMIT = 50;
export const RCA_ISSUES_MAX_LIMIT = 100;

export interface ListRcaIssuesOptions {
  clusterId?: string;
  limit?: number;
  signal?: AbortSignal;
}

/**
 * Read the additive Issue queue projection.  It is purposefully a separate
 * route from the legacy timeline so strict older web bundles remain valid
 * during a rolling server/frontend deployment.
 */
export async function listRcaIssues(
  options: ListRcaIssuesOptions = {},
): Promise<RcaIssueList> {
  const limit = options.limit ?? RCA_ISSUES_DEFAULT_LIMIT;
  assertRcaIssuesLimit(limit);
  const path = withQuery("/api/dashboard/rca/issues" as ApiPath, [
    ["cluster_id", optionalQueryString(options.clusterId)],
    ["limit", limit],
  ]);
  return apiRequest(path, rcaIssueListSchema, { signal: options.signal });
}

function assertRcaIssuesLimit(limit: number): void {
  if (!Number.isInteger(limit) || limit < 1 || limit > RCA_ISSUES_MAX_LIMIT) {
    throw new RangeError(
      `RCA issues limit must be an integer from 1 to ${RCA_ISSUES_MAX_LIMIT}`,
    );
  }
}
