import { apiRequest } from "./client";
import { rcaTimelineSchema, type RcaTimeline } from "./schemas";

const ROOT_TIMELINE_LIMIT = 6;

export function getRcaTimeline(signal?: AbortSignal): Promise<RcaTimeline> {
  return apiRequest(
    `/api/dashboard/rca/timeline?limit=${ROOT_TIMELINE_LIMIT}`,
    rcaTimelineSchema,
    { signal },
  );
}
