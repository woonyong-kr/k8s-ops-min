import { apiRequest, type ApiPath } from "./client";
import {
  repositoryConnectionStatusSchema,
  repositoryListSchema,
  type RepositoryConnectionStatus,
  type RepositoryList,
} from "./repository-connection-schemas";
import { withQuery } from "./url";

/** 워크스페이스의 모든 연결 저장소를 상태와 함께 나열한다(관리 화면용). */
export function listRepositories(signal?: AbortSignal): Promise<RepositoryList> {
  return apiRequest("/api/repositories" as ApiPath, repositoryListSchema, { signal });
}

/** Reads the server-owned terminal state for a repository registration. */
export function getRepositoryConnectionStatus(
  repoRef: string,
  signal?: AbortSignal,
): Promise<RepositoryConnectionStatus> {
  const path = withQuery(
    "/api/repositories/connection-status" as ApiPath,
    [["repo_ref", repoRef]],
  );
  return apiRequest(path, repositoryConnectionStatusSchema, { signal });
}

/**
 * 저장소 연결을 명시적으로 해제한다(고아 없이 종단 상태로 수렴).
 * 서버가 repo·watch·binding·application 을 한 트랜잭션에서 비활성으로 내리고
 * 저장된 자격증명을 삭제한 뒤, disconnected 상태를 돌려준다.
 */
export function disconnectRepository(
  repoRef: string,
  signal?: AbortSignal,
): Promise<RepositoryConnectionStatus> {
  return apiRequest("/api/repositories/disconnect" as ApiPath, repositoryConnectionStatusSchema, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ repo_ref: repoRef }),
    signal,
  });
}
