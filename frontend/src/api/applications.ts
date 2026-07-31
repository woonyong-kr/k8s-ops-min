import { apiRequest, type ApiPath } from "./client";
import {
  applicationListSchema,
  applicationResponseSchema,
  connectionPreviewSchema,
  deploymentBindingListSchema,
  workflowRunListSchema,
  type ApplicationList,
  type ApplicationResponse,
  type ConnectionPreview,
  type DeploymentBindingList,
  type WorkflowRunList,
} from "./applications-schemas";
import { encodePathSegment, withQuery } from "./url";

export const APPLICATIONS_DEFAULT_LIMIT = 100;
export const APPLICATIONS_MAX_LIMIT = 500;
export const APPLICATION_RUNS_PATH =
  "/api/applications/{application_id}/runs" as const;

export interface ApplicationListOptions {
  limit?: number;
  signal?: AbortSignal;
}

export interface ApplicationHistoryOptions {
  limit?: number;
  signal?: AbortSignal;
}

export interface ApplicationConnectInput {
  name: string;
  repository: string;
  branch: string;
  manifestPath: string;
  sourceType?: string;
  clusterId: string;
  namespace: string;
  environment: string;
  token?: string;
  /** GitHub App 원클릭 연결 완료 시의 설치 id(있으면 App 설치 참조로 저장). */
  installationId?: string;
  /** 리소스 소유권 겹침을 사용자가 확인하고 그래도 진행할 때 true. */
  allowConflicts?: boolean;
}

/** Lists Applications visible to the signed-in user. */
export async function listApplications(
  options: ApplicationListOptions = {},
): Promise<ApplicationList> {
  const limit = options.limit ?? APPLICATIONS_DEFAULT_LIMIT;
  assertLimit(limit);
  const path = withQuery("/api/applications" as ApiPath, [["limit", limit]]);
  return apiRequest(path, applicationListSchema, { signal: options.signal });
}

export interface ApplicationConnectPreviewInput {
  repository: string;
  branch: string;
  manifestPath: string;
  sourceType?: string;
  valuesPath?: string;
  clusterId: string;
  namespace: string;
  installationId?: string;
}

/**
 * 연결 직전, 선택 매니페스트를 대상 클러스터에 반영하면 무엇이 생성·변경·유지·겹침
 * 되는지 미리 계산한다(읽기 전용, 상태 변화 없음).
 */
export function previewApplicationConnection(
  input: ApplicationConnectPreviewInput,
  signal?: AbortSignal,
): Promise<ConnectionPreview> {
  return apiRequest(
    "/api/applications/connect/preview",
    connectionPreviewSchema,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        repo_ref: input.repository,
        branch: input.branch,
        manifest_path: input.manifestPath,
        ...(input.sourceType ? { source_type: input.sourceType } : {}),
        ...(input.valuesPath ? { values_path: input.valuesPath } : {}),
        cluster_id: input.clusterId,
        namespace: input.namespace,
        ...(input.installationId ? { installation_id: input.installationId } : {}),
      }),
      signal,
    },
  );
}

/** Validates a Git source and registers a deployable Application target. */
export function connectApplication(
  input: ApplicationConnectInput,
  signal?: AbortSignal,
): Promise<ApplicationResponse> {
  return apiRequest(
    "/api/applications/connect",
    applicationResponseSchema,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        name: input.name,
        repo_ref: input.repository,
        branch: input.branch,
        manifest_path: input.manifestPath,
        ...(input.sourceType ? { source_type: input.sourceType } : {}),
        cluster_id: input.clusterId,
        namespace: input.namespace,
        environment: input.environment,
        ...(input.token ? { token: input.token } : {}),
        ...(input.installationId ? { installation_id: input.installationId } : {}),
        ...(input.allowConflicts ? { allow_conflicts: true } : {}),
      }),
      signal,
    },
  );
}

/** Loads one Application and its repository metadata. */
export function getApplication(
  applicationId: string,
  signal?: AbortSignal,
): Promise<ApplicationResponse> {
  assertApplicationId(applicationId);
  const path = `/api/applications/${encodePathSegment(applicationId)}` as ApiPath;
  return apiRequest(path, applicationResponseSchema, { signal });
}

/** Lists the deployment bindings/environments configured for an Application. */
export async function listApplicationDeployments(
  applicationId: string,
  options: ApplicationHistoryOptions = {},
): Promise<DeploymentBindingList> {
  assertApplicationId(applicationId);
  const limit = options.limit ?? APPLICATIONS_DEFAULT_LIMIT;
  assertLimit(limit);
  const basePath =
    `/api/applications/${encodePathSegment(applicationId)}/deployments` as ApiPath;
  const path = withQuery(basePath, [["limit", limit]]);
  return apiRequest(path, deploymentBindingListSchema, { signal: options.signal });
}

/** Lists GitOps workflow runs for an Application. */
export async function listApplicationRuns(
  applicationId: string,
  options: ApplicationHistoryOptions = {},
): Promise<WorkflowRunList> {
  assertApplicationId(applicationId);
  const limit = options.limit ?? APPLICATIONS_DEFAULT_LIMIT;
  assertLimit(limit);
  const basePath = APPLICATION_RUNS_PATH.replace(
    "{application_id}",
    encodePathSegment(applicationId),
  ) as ApiPath;
  const path = withQuery(basePath, [["limit", limit]]);
  return apiRequest(path, workflowRunListSchema, { signal: options.signal });
}

function assertLimit(limit: number): void {
  if (
    !Number.isInteger(limit) ||
    limit < 1 ||
    limit > APPLICATIONS_MAX_LIMIT
  ) {
    throw new RangeError(
      `application history limit must be an integer from 1 to ${APPLICATIONS_MAX_LIMIT}`,
    );
  }
}

function assertApplicationId(applicationId: string): void {
  if (applicationId.trim() === "") {
    throw new RangeError("applicationId must not be empty");
  }
}
