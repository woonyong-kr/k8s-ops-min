import { apiRequest, type ApiPath } from "./client";
import { workloadDetailSchema, type WorkloadDetailEndpoint } from "./workload-detail-schemas";
import { encodePathSegment, withQuery } from "./url";

export const WORKLOAD_DETAIL_PATH = "/api/workloads/{kind}/{namespace}/{name}" as const;

export interface WorkloadDetailQuery {
  clusterId: string;
  apiGroup: string;
  apiVersion: string;
  kind: string;
  namespace: string | null;
  name: string;
}

export function getWorkloadDetail(
  query: WorkloadDetailQuery,
  signal?: AbortSignal,
): Promise<WorkloadDetailEndpoint> {
  const kind = required(query.kind, "kind");
  const namespace = query.namespace === null ? "_" : required(query.namespace, "namespace");
  const name = required(query.name, "name");
  const path = WORKLOAD_DETAIL_PATH
    .replace("{kind}", encodePathSegment(kind))
    .replace("{namespace}", encodePathSegment(namespace))
    .replace("{name}", encodePathSegment(name)) as ApiPath;
  return apiRequest(withQuery(path, [
    ["cluster_id", required(query.clusterId, "cluster ID")],
    ["apiGroup", canonicalGroup(query.apiGroup)],
    ["apiVersion", requiredVersion(query.apiVersion)],
  ]), workloadDetailSchema, { signal });
}

function required(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new TypeError(`${label} must not be empty`);
  return normalized;
}

function canonicalGroup(value: string): string {
  const normalized = value.trim();
  if (normalized !== value || normalized.includes("/") || /\s/.test(normalized)) {
    throw new TypeError("API group is invalid");
  }
  return normalized;
}

function requiredVersion(value: string): string {
  const normalized = required(value, "API version");
  if (normalized !== value || normalized.includes("/") || /\s/.test(normalized)) {
    throw new TypeError("API version is invalid");
  }
  return normalized;
}
