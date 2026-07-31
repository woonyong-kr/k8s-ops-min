import { apiRequest, type ApiPath } from "./client";
import { changeTimelineSchema, type ChangeTimelineEndpoint } from "./change-timeline-schemas";
import { resourceFilterEntries, type ResourceFilterQuery } from "./resource-filter-query";
import { withQuery } from "./url";

export const CHANGE_TIMELINE_PATH: ApiPath = "/api/changes";

export interface ChangeTimelineQuery extends Omit<ResourceFilterQuery, "includeDeleted" | "cursor" | "limit"> {
  fromMs: number;
  toMs: number;
  bucketMs: number;
}

export function getChangeTimeline(
  query: ChangeTimelineQuery,
  signal?: AbortSignal,
): Promise<ChangeTimelineEndpoint> {
  assertWindow(query.fromMs, query.toMs, query.bucketMs);
  const filters = resourceFilterEntries(query).filter(
    ([name]) => name !== "resources.includeDeleted",
  );
  return apiRequest(withQuery(CHANGE_TIMELINE_PATH, [
    ["from", query.fromMs],
    ["to", query.toMs],
    ["bucket", query.bucketMs],
    ...filters,
  ]), changeTimelineSchema, { signal });
}

function assertWindow(fromMs: number, toMs: number, bucketMs: number) {
  if (![fromMs, toMs, bucketMs].every(Number.isSafeInteger)) {
    throw new TypeError("change timeline bounds must be safe integers");
  }
  if (fromMs < 0 || toMs <= fromMs || bucketMs <= 0 || bucketMs > toMs - fromMs) {
    throw new RangeError("change timeline requires a bounded positive window and bucket");
  }
}
