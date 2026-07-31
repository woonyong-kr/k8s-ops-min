import { apiRequest, type ApiPath } from "./client";
import {
  resourceManifestApproveSchema,
  resourceManifestApplySchema,
  resourceManifestPreviewSchema,
  resourceManifestSourceSchema,
  type ResourceManifestApproveEndpoint,
  type ResourceManifestApplyEndpoint,
  type ResourceManifestPreviewEndpoint,
  type ResourceManifestSourceEndpoint,
} from "./resource-manifests-schemas";
import { encodePathSegment, withQuery } from "./url";

export interface ResourceManifestEditInput {
  applicationId: string;
  baseSha: string;
  sourceSha256: string;
  sourceRevisionToken?: string | null;
  editedYaml: string;
}

export interface ResourceManifestApprovalInput extends ResourceManifestEditInput {
  confirmed: true;
  reason: string;
}

export interface ResourceManifestDirectApplyInput extends ResourceManifestEditInput {
  expectedDesiredSha256: string;
  confirmation: true;
  reason: string;
  idempotencyKey: string;
}

export function getResourceManifestSource(
  resourceId: string,
  applicationId?: string | null,
  signal?: AbortSignal,
): Promise<ResourceManifestSourceEndpoint> {
  const base = `/api/resource-manifests/${encodePathSegment(resourceId)}` as ApiPath;
  const path = withQuery(base, [["application_id", applicationId ?? undefined]]);
  return apiRequest(path, resourceManifestSourceSchema, { signal });
}

export function previewResourceManifestEdit(
  resourceId: string,
  input: ResourceManifestEditInput,
  signal?: AbortSignal,
): Promise<ResourceManifestPreviewEndpoint> {
  return apiRequest(resourceManifestPath(resourceId, "preview"), resourceManifestPreviewSchema, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(requestBody(input)),
    signal,
  });
}

export function approveResourceManifestEdit(
  resourceId: string,
  input: ResourceManifestApprovalInput,
  signal?: AbortSignal,
): Promise<ResourceManifestApproveEndpoint> {
  return apiRequest(resourceManifestPath(resourceId, "approve"), resourceManifestApproveSchema, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      ...requestBody(input),
      confirmed: input.confirmed,
      reason: input.reason,
    }),
    signal,
  });
}

export function applyResourceManifestEdit(
  resourceId: string,
  input: ResourceManifestDirectApplyInput,
  signal?: AbortSignal,
): Promise<ResourceManifestApplyEndpoint> {
  if (input.idempotencyKey.trim().length < 8) {
    throw new TypeError("manifest idempotencyKey must contain at least 8 characters");
  }
  return apiRequest(resourceManifestPath(resourceId, "apply"), resourceManifestApplySchema, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "idempotency-key": input.idempotencyKey,
    },
    body: JSON.stringify({
      ...requestBody(input),
      expected_desired_sha256: input.expectedDesiredSha256,
      confirmation: input.confirmation,
      reason: input.reason,
    }),
    signal,
  });
}

function resourceManifestPath(resourceId: string, action: "preview" | "approve" | "apply"): ApiPath {
  return `/api/resource-manifests/${encodePathSegment(resourceId)}/${action}` as ApiPath;
}

function requestBody(input: ResourceManifestEditInput) {
  return {
    application_id: input.applicationId,
    base_sha: input.baseSha,
    source_sha256: input.sourceSha256,
    source_revision_token: input.sourceRevisionToken ?? null,
    edited_yaml: input.editedYaml,
  };
}
