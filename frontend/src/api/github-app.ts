import { z } from "zod";

import { apiRequest, type ApiPath } from "./client";
import { withQuery } from "./url";

// GitHub App 연동 API.
// - config       : App 구성 여부(PAT/App 분기 판단)
// - install-url  : 사용자 원클릭 설치 리다이렉트 URL
// - manifest     : 운영자 1회 자동등록용 manifest + GitHub 생성 URL
// - verify       : 설치가 그 레포에 실제로 걸렸는지 + PR 쓰기 권한 확인

export const githubAppConfigSchema = z.strictObject({
  configured: z.boolean(),
  slug: z.string().nullable(),
  install_available: z.boolean(),
});
export type GithubAppConfig = z.infer<typeof githubAppConfigSchema>;

const installUrlSchema = z.strictObject({ url: z.string().url() });

const manifestSchema = z.strictObject({
  action_url: z.string().url(),
  manifest: z.record(z.string(), z.unknown()),
});
export type GithubAppManifest = z.infer<typeof manifestSchema>;

const verifySchema = z.strictObject({
  installation_id: z.string(),
  matches: z.boolean(),
  write_capable: z.boolean(),
  repositories: z.array(z.string()),
  permissions: z.record(z.string(), z.string()),
  repository_selection: z.string().nullable(),
});
export type GithubAppVerify = z.infer<typeof verifySchema>;

export function getGithubAppConfig(signal?: AbortSignal): Promise<GithubAppConfig> {
  return apiRequest("/api/integrations/github/app/config", githubAppConfigSchema, { signal });
}

const uninstallSchema = z.strictObject({
  removed: z.boolean(),
  env_fallback_active: z.boolean(),
});
export type GithubAppUninstall = z.infer<typeof uninstallSchema>;

// 서버에 저장된 App 구성(개인키·웹훅시크릿) 제거(운영자 오프보딩, 관리자 전용).
export function uninstallGithubApp(signal?: AbortSignal): Promise<GithubAppUninstall> {
  return apiRequest("/api/integrations/github/app/config", uninstallSchema, {
    method: "DELETE",
    signal,
  });
}

export function getGithubAppInstallUrl(
  state: string,
  signal?: AbortSignal,
): Promise<{ url: string }> {
  return apiRequest(
    withQuery("/api/integrations/github/app/install-url", [["state", state]]),
    installUrlSchema,
    { signal },
  );
}

export function getGithubAppManifest(
  params: { baseUrl?: string; state: string; org?: string; name?: string },
  signal?: AbortSignal,
): Promise<GithubAppManifest> {
  // base_url 은 서버가 자기 공개 주소로 자동 채우므로 보통 생략한다.
  const entries: [string, string][] = [["state", params.state]];
  if (params.baseUrl) entries.push(["base_url", params.baseUrl]);
  if (params.org) entries.push(["org", params.org]);
  if (params.name) entries.push(["name", params.name]);
  return apiRequest(
    withQuery("/api/integrations/github/app/manifest", entries),
    manifestSchema,
    { signal },
  );
}

export function verifyGithubAppInstallation(
  installationId: string,
  repoRef: string,
  signal?: AbortSignal,
): Promise<GithubAppVerify> {
  const path =
    `/api/integrations/github/app/installations/${encodeURIComponent(installationId)}/verify` as ApiPath;
  return apiRequest(path, verifySchema, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ repo_ref: repoRef }),
    signal,
  });
}
