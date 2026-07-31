import { apiRequest, type ApiPath } from "./client";
import {
  helmReleaseDetailSchema,
  helmReleaseListSchema,
  type HelmReleaseDetailEndpoint,
  type HelmReleaseListEndpoint,
} from "./helm-releases-schemas";
import { canonicalFacetSelections } from "./resource-filter-query";
import { encodePathSegment, withQuery } from "./url";

export const HELM_RELEASES_PATH = "/api/helm/releases" as const;
export const HELM_RELEASE_PATH = "/api/helm/releases/{namespace}/{release_name}" as const;

export interface HelmReleaseListQuery {
  clusterIds?: readonly string[];
  namespaces?: readonly string[];
}

export function listHelmReleases(
  query: HelmReleaseListQuery = {},
  signal?: AbortSignal,
): Promise<HelmReleaseListEndpoint> {
  return apiRequest(withQuery(HELM_RELEASES_PATH, [
    ["clusters", joined("clusters", query.clusterIds)],
    ["namespaces", joined("namespaces", query.namespaces)],
  ]), helmReleaseListSchema, { signal });
}

export function getHelmRelease(
  input: { clusterId: string; namespace: string; releaseName: string },
  signal?: AbortSignal,
): Promise<HelmReleaseDetailEndpoint> {
  const clusterId = requiredIdentity(input.clusterId, "clusterId");
  const namespace = requiredIdentity(input.namespace, "namespace");
  const releaseName = requiredIdentity(input.releaseName, "releaseName");
  const path = HELM_RELEASE_PATH
    .replace("{namespace}", encodePathSegment(namespace))
    .replace("{release_name}", encodePathSegment(releaseName)) as ApiPath;
  return apiRequest(withQuery(path, [["cluster_id", clusterId]]), helmReleaseDetailSchema, { signal });
}

function joined(
  axis: "clusters" | "namespaces",
  values: readonly string[] | undefined,
): string | undefined {
  const canonical = canonicalFacetSelections(axis, values);
  return canonical.length === 0 ? undefined : canonical.join(",");
}

function requiredIdentity(value: string, name: string): string {
  const normalized = value.trim();
  if (normalized === "") throw new RangeError(`${name} must not be empty`);
  return normalized;
}
