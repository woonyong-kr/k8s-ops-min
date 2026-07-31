import { apiRequest, type ApiPath } from "./client";
import { withQuery } from "./url";
import {
  browserRefreshPoliciesResponseSchema,
  settingsAccessProfileResponseSchema,
  uiPreferencesResponseSchema,
  uiPreferencesUpdateRequestSchema,
  uiPreferencesUpdateResponseSchema,
  type BrowserRefreshPoliciesResponse,
  type SettingsAccessProfileResponse,
  type UiPreferences,
  type UiPreferencesResponse,
  type UiPreferencesUpdateResponse,
} from "./settings-schemas";

// UI-PHASE2-001 §3 Settings: authenticated client for the shell-state settings
// endpoints. All requests flow through the shared `apiRequest` boundary, which
// includes credentials and stamps the `x-service-csrf: same-origin` header on
// every state-changing method (PUT here). Backend source of truth:
// `src/domains/shell_state/router.py`.

export const SETTINGS_PATH: ApiPath = "/api/settings";
export const SETTINGS_ACCESS_PATH: ApiPath = "/api/settings/access";
export const REFRESH_POLICIES_PATH: ApiPath = "/api/refresh-policies";

export interface UiPreferencesUpdateInput {
  preferences: UiPreferences;
  expectedRevision: number;
}

export interface SettingsAccessQuery {
  clusterId: string;
  namespace?: string | null;
}

/** Reads the authenticated workspace/user UI preferences (theme, locale). */
export function getUiPreferences(signal?: AbortSignal): Promise<UiPreferencesResponse> {
  return apiRequest(SETTINGS_PATH, uiPreferencesResponseSchema, { signal });
}

/**
 * Persists UI preferences with optimistic-concurrency protection. The server
 * returns the new revision and the mutation/audit event identifiers. CSRF is
 * applied by the request boundary; this is only invoked from an explicit user
 * action.
 */
export function updateUiPreferences(
  input: UiPreferencesUpdateInput,
  signal?: AbortSignal,
): Promise<UiPreferencesUpdateResponse> {
  const request = uiPreferencesUpdateRequestSchema.parse({
    preferences: input.preferences,
    expected_revision: input.expectedRevision,
  });
  return apiRequest(SETTINGS_PATH, uiPreferencesUpdateResponseSchema, {
    body: JSON.stringify(request),
    headers: { "content-type": "application/json" },
    method: "PUT",
    signal,
  });
}

/** Reads the RBAC access/permission profile for one cluster (and namespace). */
export function getSettingsAccessProfile(
  query: SettingsAccessQuery,
  signal?: AbortSignal,
): Promise<SettingsAccessProfileResponse> {
  const path = withQuery(SETTINGS_ACCESS_PATH, [
    ["cluster_id", query.clusterId],
    ["namespace", query.namespace ?? null],
  ]);
  return apiRequest(path, settingsAccessProfileResponseSchema, { signal });
}

/** Reads the server-owned browser auto-refresh cadences. */
export function getBrowserRefreshPolicies(
  signal?: AbortSignal,
): Promise<BrowserRefreshPoliciesResponse> {
  return apiRequest(REFRESH_POLICIES_PATH, browserRefreshPoliciesResponseSchema, { signal });
}
