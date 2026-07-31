import { apiRequest, type ApiPath } from "./client";
import {
  applicationCatalogSchema,
  applicationDeploymentHistorySchema,
  applicationDetailSchema,
  applicationDriftSchema,
  type ApplicationCatalogEndpoint,
  type ApplicationDeploymentHistoryEndpoint,
  type ApplicationDetailEndpoint,
  type ApplicationDriftEndpoint,
} from "./application-catalog-schemas";
import {
  boundedQuery,
  canonicalFacetSelections,
  canonicalLabelSelections,
} from "./resource-filter-query";
import { encodePathSegment, withQuery } from "./url";

export const APPLICATION_CATALOG_PATH: ApiPath = "/api/applications";

export interface ApplicationCatalogQuery {
  clusters?: readonly string[];
  namespaces?: readonly string[];
  applications?: readonly string[];
  labels?: readonly string[];
  environments?: readonly string[];
  statuses?: readonly string[];
  pendingPromotion?: boolean;
  query?: string;
}

export function listApplicationCatalog(
  query: ApplicationCatalogQuery = {},
  signal?: AbortSignal,
): Promise<ApplicationCatalogEndpoint> {
  const path = withQuery(APPLICATION_CATALOG_PATH, [
    ["clusters", joined("clusters", query.clusters)],
    ["namespaces", joined("namespaces", query.namespaces)],
    ["applications", joined("applications", query.applications)],
    ["labels", joinedLabels(query.labels)],
    ["applications.environment", joined("applications", query.environments)],
    ["applications.status", joined("applications", query.statuses)],
    ["applications.pendingPromotion", query.pendingPromotion || undefined],
    ["applications.q", boundedQuery("query", query.query)],
  ]);
  return apiRequest(path, applicationCatalogSchema, { signal });
}

export function getApplicationOverview(
  applicationId: string,
  signal?: AbortSignal,
  instanceId?: string | null,
  workloadKey?: string | null,
): Promise<ApplicationDetailEndpoint> {
  return apiRequest(
    applicationScopedPath(applicationId, instanceId, workloadKey),
    applicationDetailSchema,
    { signal },
  );
}

export function listApplicationDeploymentHistory(
  applicationId: string,
  signal?: AbortSignal,
  instanceId?: string | null,
): Promise<ApplicationDeploymentHistoryEndpoint> {
  return apiRequest(
    withQuery(`${applicationPath(applicationId)}/deployments` as ApiPath, [
      ["instance", normalizedInstanceId(instanceId)],
    ]),
    applicationDeploymentHistorySchema,
    { signal },
  );
}

export function getApplicationDrift(
  applicationId: string,
  signal?: AbortSignal,
  instanceId?: string | null,
): Promise<ApplicationDriftEndpoint> {
  return apiRequest(
    withQuery(`${applicationPath(applicationId)}/drift` as ApiPath, [
      ["instance", normalizedInstanceId(instanceId)],
    ]),
    applicationDriftSchema,
    { signal },
  );
}

function applicationPath(applicationId: string): ApiPath {
  if (applicationId.trim() === "") {
    throw new RangeError("applicationId must not be empty");
  }
  return `/api/applications/${encodePathSegment(applicationId)}` as ApiPath;
}

function applicationScopedPath(
  applicationId: string,
  instanceId?: string | null,
  workloadKey?: string | null,
): ApiPath {
  return withQuery(applicationPath(applicationId), [
    ["instance", normalizedInstanceId(instanceId)],
    ["workload", normalizedWorkloadKey(workloadKey)],
  ]);
}

function normalizedInstanceId(instanceId: string | null | undefined): string | undefined {
  if (instanceId === null || instanceId === undefined) return undefined;
  const normalized = instanceId.trim();
  if (normalized === "") throw new RangeError("instanceId must not be empty");
  return normalized;
}

function normalizedWorkloadKey(workloadKey: string | null | undefined): string | undefined {
  if (workloadKey === null || workloadKey === undefined) return undefined;
  const normalized = workloadKey.trim();
  if (normalized === "") throw new RangeError("workloadKey must not be empty");
  if (normalized.length > 128) throw new RangeError("workloadKey is too long");
  return normalized;
}

function joined(
  axis: "clusters" | "namespaces" | "applications",
  values: readonly string[] | undefined,
): string | undefined {
  const canonical = canonicalFacetSelections(axis, values);
  return canonical.length === 0 ? undefined : canonical.join(",");
}

function joinedLabels(values: readonly string[] | undefined): string | undefined {
  const canonical = canonicalLabelSelections(values);
  return canonical.length === 0 ? undefined : canonical.join(",");
}
