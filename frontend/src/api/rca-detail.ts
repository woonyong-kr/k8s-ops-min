import { apiRequest, type ApiPath } from "./client";
import { rcaIncidentSchema, type RcaIncident } from "./rca-detail-schemas";
import { encodePathSegment, optionalQueryString, withQuery } from "./url";

export interface GetRcaIncidentOptions {
  clusterId?: string;
  signal?: AbortSignal;
}

/** Loads one Incident/RCA detail scoped to the current user's access. */
export function getRcaIncident(
  incidentId: string,
  options: GetRcaIncidentOptions = {},
): Promise<RcaIncident> {
  const basePath =
    `/api/dashboard/rca/incidents/${encodePathSegment(incidentId)}` as ApiPath;
  const path = withQuery(basePath, [
    ["cluster_id", optionalQueryString(options.clusterId)],
  ]);
  return apiRequest(path, rcaIncidentSchema, { signal: options.signal });
}
