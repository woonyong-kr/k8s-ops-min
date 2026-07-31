import { apiRequest, type ApiPath } from "./client";
import {
  remediationBundleResponseSchema,
  type RemediationBundleResponse,
} from "./rca-bundle-schemas";
import { encodePathSegment } from "./url";

export interface GetRemediationBundleOptions {
  signal?: AbortSignal;
}

/** Loads the strict diagnosis and optional recovery-plan bundle for one correlation. */
export function getRemediationBundle(
  correlationId: string,
  options: GetRemediationBundleOptions = {},
): Promise<RemediationBundleResponse> {
  const path =
    `/api/rca/bundles/${encodePathSegment(correlationId)}` as ApiPath;
  return apiRequest(path, remediationBundleResponseSchema, {
    signal: options.signal,
  });
}
