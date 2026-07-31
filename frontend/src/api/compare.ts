import { apiRequest, type ApiPath } from "./client";
import {
  compareCandidateListSchema,
  compareDescriptorListSchema,
  compareResourcePairSchema,
  type CompareCandidateListEndpoint,
  type CompareDescriptorListEndpoint,
  type CompareResourcePairEndpoint,
} from "./compare-schemas";
import { withQuery } from "./url";

export const COMPARE_DESCRIPTORS_PATH = "/api/compare/descriptors" as const;
export const COMPARE_CANDIDATES_PATH = "/api/compare/candidates" as const;
export const COMPARE_RESOURCES_PATH = "/api/compare/resources" as const;

export interface CompareIdentityQuery {
  clusterId: string;
  kind: string;
  apiGroup: string;
  apiVersion: string | null;
}

export interface ComparePairQuery extends CompareIdentityQuery {
  a: string;
  b: string;
}

export function getCompareDescriptors(signal?: AbortSignal): Promise<CompareDescriptorListEndpoint> {
  return apiRequest(COMPARE_DESCRIPTORS_PATH as ApiPath, compareDescriptorListSchema, { signal });
}

export function getCompareCandidates(
  query: CompareIdentityQuery,
  signal?: AbortSignal,
): Promise<CompareCandidateListEndpoint> {
  return apiRequest(compareQuery(COMPARE_CANDIDATES_PATH, query), compareCandidateListSchema, { signal });
}

export function getCompareResourcePair(
  query: ComparePairQuery,
  signal?: AbortSignal,
): Promise<CompareResourcePairEndpoint> {
  return apiRequest(compareQuery(COMPARE_RESOURCES_PATH, query, [
    ["a", required(query.a, "comparison side A")],
    ["b", required(query.b, "comparison side B")],
  ]), compareResourcePairSchema, { signal });
}

function compareQuery(
  path: string,
  query: CompareIdentityQuery,
  extra: readonly (readonly [string, string])[] = [],
): ApiPath {
  return withQuery(path as ApiPath, [
    ["cluster_id", required(query.clusterId, "cluster ID")],
    ["kind", required(query.kind, "comparison kind")],
    ["apiGroup", canonicalGroup(query.apiGroup) || undefined],
    ["apiVersion", query.apiVersion === null ? undefined : requiredVersion(query.apiVersion)],
    ...extra,
  ]);
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
