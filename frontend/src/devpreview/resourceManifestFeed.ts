import { isApiError } from "../api/client";
import { getCommandStatus } from "../api/metrics";
import type { CommandStatus } from "../api/metrics-schemas";
import {
  applyResourceManifestEdit,
  approveResourceManifestEdit,
  getResourceManifestSource,
  previewResourceManifestEdit,
} from "../api/resource-manifests";
import type {
  ResourceManifestApplyEndpoint,
  ResourceManifestApproveEndpoint,
  ResourceManifestPreviewEndpoint,
  ResourceManifestSourceEndpoint,
} from "../api/resource-manifests-schemas";

export {
  applyResourceManifestEdit,
  approveResourceManifestEdit,
  getCommandStatus,
  getResourceManifestSource,
  previewResourceManifestEdit,
};
export type {
  CommandStatus,
  ResourceManifestApplyEndpoint,
  ResourceManifestApproveEndpoint,
  ResourceManifestPreviewEndpoint,
  ResourceManifestSourceEndpoint,
};

const STALE_SOURCE_CODES = new Set([
  "manifest_source_stale",
  "manifest_source_revision_invalid",
]);

export function manifestIdempotencyKey(
  resourceId: string,
  desiredSha256: string,
  baseSha: string,
  sourceSha256: string,
): string {
  const safeResource = resourceId.replace(/[^A-Za-z0-9._:-]/g, "-").slice(-40);
  const revision = [baseSha, sourceSha256, desiredSha256]
    .map((value) => value.replace(/[^A-Za-z0-9]/g, "").slice(-16))
    .join("-");
  return `manifest-${safeResource}-${revision}`;
}

export function resourceManifestFailureText(cause: unknown): string {
  if (isApiError(cause)) {
    if (isResourceManifestSourceStale(cause)) {
      return "Git의 YAML 원본이 변경되었습니다. 편집 내용은 유지한 채 최신 원본을 다시 확인하세요.";
    }
    return cause.detail ?? cause.code ?? `${cause.kind}${cause.status ? ` (${cause.status})` : ""}`;
  }
  return cause instanceof Error ? cause.message : "요청을 완료하지 못했습니다.";
}

export function isResourceManifestSourceStale(cause: unknown): boolean {
  return isApiError(cause)
    && cause.status === 409
    && cause.code !== null
    && STALE_SOURCE_CODES.has(cause.code);
}

export type ResourceManifestRemediation =
  | "connect-repository"
  | "reauthenticate"
  | "request-access"
  | "retry"
  | "none";

const SOURCE_NOT_FOUND = "No exact GitOps source binding was found for this live resource.";
const SOURCE_PERMISSION_REQUIRED = "manifest_source_permission_required";

export function isResourceManifestSourceConflict(cause: unknown): boolean {
  return isResourceManifestSourceStale(cause);
}

export function resourceManifestFailureRemediation(cause: unknown): ResourceManifestRemediation {
  if (!isApiError(cause)) return "retry";
  if (cause.kind === "unauthorized") return "reauthenticate";
  if (cause.kind === "forbidden") return "request-access";
  return "retry";
}

export function resourceManifestSourceRemediation(reason: string | null): ResourceManifestRemediation {
  if (reason === SOURCE_NOT_FOUND) return "connect-repository";
  if (reason === SOURCE_PERMISSION_REQUIRED) return "request-access";
  return "none";
}
