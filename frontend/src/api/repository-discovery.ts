import { apiRequest, type ApiPath } from "./client";
import {
  repositoryBranchListSchema,
  repositoryManifestCandidateListSchema,
  repositoryManifestValidationSchema,
  repositoryProbeSchema,
  type RepositoryBranchList,
  type RepositoryManifestCandidateList,
  type RepositoryManifestValidation,
  type RepositoryProbe,
} from "./repository-discovery-schemas";
import { withQuery } from "./url";

export const REPOSITORY_DISCOVERY_PROBE_PATH = "/api/repositories/discovery/probe" as const;
export const REPOSITORY_DISCOVERY_BRANCHES_PATH = "/api/repositories/discovery/branches" as const;
export const REPOSITORY_DISCOVERY_MANIFESTS_PATH = "/api/repositories/discovery/manifests" as const;
export const REPOSITORY_DISCOVERY_VALIDATE_PATH = "/api/repositories/discovery/validate" as const;

function jsonRequest(body: unknown, signal?: AbortSignal): RequestInit {
  return {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  };
}

export function probeRepository(
  repoRef: string,
  token?: string,
  signal?: AbortSignal,
  installationId?: string,
): Promise<RepositoryProbe> {
  return apiRequest(
    REPOSITORY_DISCOVERY_PROBE_PATH,
    repositoryProbeSchema,
    jsonRequest(
      {
        repo_ref: repoRef,
        ...(token ? { token } : {}),
        ...(installationId ? { installation_id: installationId } : {}),
      },
      signal,
    ),
  );
}

export function listRepositoryBranches(
  repoRef: string,
  signal?: AbortSignal,
  installationId?: string,
): Promise<RepositoryBranchList> {
  const query: Array<[string, string]> = [["repo_ref", repoRef]];
  if (installationId) {
    query.push(["installation_id", installationId]);
  }
  const path = withQuery(REPOSITORY_DISCOVERY_BRANCHES_PATH as ApiPath, query);
  return apiRequest(path, repositoryBranchListSchema, { signal });
}

export function listRepositoryManifestCandidates(
  repoRef: string,
  branch: string,
  signal?: AbortSignal,
  installationId?: string,
): Promise<RepositoryManifestCandidateList> {
  return apiRequest(
    REPOSITORY_DISCOVERY_MANIFESTS_PATH,
    repositoryManifestCandidateListSchema,
    jsonRequest(
      {
        repo_ref: repoRef,
        branch,
        ...(installationId ? { installation_id: installationId } : {}),
      },
      signal,
    ),
  );
}

export function validateRepositoryManifest(
  repoRef: string,
  branch: string,
  manifestPath: string,
  sourceType = "",
  signal?: AbortSignal,
  installationId?: string,
): Promise<RepositoryManifestValidation> {
  return apiRequest(
    REPOSITORY_DISCOVERY_VALIDATE_PATH,
    repositoryManifestValidationSchema,
    jsonRequest({
      repo_ref: repoRef,
      branch,
      manifest_path: manifestPath,
      source_type: sourceType,
      ...(installationId ? { installation_id: installationId } : {}),
    }, signal),
  );
}
