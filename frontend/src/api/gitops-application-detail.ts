import { apiRequest, type ApiPath } from "./client";
import {
  gitOpsApplicationDetailResponseSchema,
  type GitOpsApplicationDetailEndpoint,
} from "./gitops-application-detail-schemas";
import { encodePathSegment } from "./url";

export const GITOPS_APPLICATION_DETAIL_PATH = "/api/gitops/applications/{application_id}" as const;

export function getGitOpsApplicationDetail(
  applicationId: string,
  signal?: AbortSignal,
): Promise<GitOpsApplicationDetailEndpoint> {
  if (applicationId.trim() === "") {
    throw new RangeError("applicationId must not be empty");
  }
  const path = GITOPS_APPLICATION_DETAIL_PATH.replace(
    "{application_id}",
    encodePathSegment(applicationId),
  ) as ApiPath;
  return apiRequest(path, gitOpsApplicationDetailResponseSchema, { signal });
}
