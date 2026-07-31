import { z } from "zod";

// UI-PHASE2-001 §3 Settings: strict runtime contracts for the authenticated
// shell-state settings endpoints. Shapes mirror
// `src/packages/contracts/shell_state.py` and `packages.contracts.freshness`
// exactly (StrictModel → strictObject). No passthrough relaxation: an
// unexpected field fails the parse closed, never silently backfilled.

const nonEmptyString = z.string().min(1);
const nonNegativeInteger = z.number().int().nonnegative();
const revisionHashSchema = z.string().regex(/^[0-9a-f]{64}$/u);
const permissionKeySchema = z.string().regex(/^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$/u);

// ── UI preferences (GET/PUT /api/settings) ──
const productThemeSelectionSchema = z.enum(["system", "light", "dark"]);
const productLocaleSchema = z.enum(["en", "ko"]);

export const uiPreferencesSchema = z.strictObject({
  theme: productThemeSelectionSchema,
  locale: productLocaleSchema,
});

export const uiPreferencesResponseSchema = z.strictObject({
  workspace_id: nonEmptyString,
  user_id: nonEmptyString,
  preferences: uiPreferencesSchema,
  revision: nonNegativeInteger,
  updated_at: z.string().nullable().default(null),
});

export const uiPreferencesUpdateRequestSchema = z.strictObject({
  preferences: uiPreferencesSchema,
  expected_revision: nonNegativeInteger,
});

export const uiPreferencesUpdateResponseSchema = uiPreferencesResponseSchema.extend({
  event_id: nonEmptyString,
  audit_event_id: nonEmptyString,
});

// ── Access profile (GET /api/settings/access) ──
const settingsAccessDecisionSchema = z.strictObject({
  permission: permissionKeySchema,
  category: nonEmptyString,
  allowed: z.boolean(),
});

const kubernetesSubjectSchema = z.strictObject({
  kind: z.enum(["ServiceAccount", "User", "Group"]),
  namespace: z.string().default(""),
  name: nonEmptyString,
});

const kubernetesPolicyRuleSchema = z.strictObject({
  verbs: z.array(z.string()).default([]),
  api_groups: z.array(z.string()).default([]),
  resources: z.array(z.string()).default([]),
  resource_names: z.array(z.string()).default([]),
  non_resource_urls: z.array(z.string()).default([]),
});

const kubernetesRestrictedResourceTypeSchema = z.strictObject({
  api_group: z.string().default(""),
  version: nonEmptyString,
  resource: nonEmptyString,
  kind: nonEmptyString,
  namespaced: z.boolean(),
  reason_code: z.literal("list_permission_not_observed"),
});

const settingsUnavailableEvidenceSchema = z.strictObject({
  status: z.literal("unavailable"),
  reason_code: nonEmptyString,
  detail: nonEmptyString,
});

const settingsObservedKubernetesRulesSchema = z.strictObject({
  status: z.literal("observed"),
  authority: z.literal("cluster_agent_service_account"),
  namespace: nonEmptyString,
  observed_at: nonEmptyString,
  subject: kubernetesSubjectSchema,
  resource_rules: z.array(kubernetesPolicyRuleSchema).default([]),
  non_resource_rules: z.array(kubernetesPolicyRuleSchema).default([]),
  truncated: z.boolean().default(false),
});

const settingsObservedRestrictedResourceTypesSchema = z.strictObject({
  status: z.literal("observed"),
  authority: z.literal("cluster_agent_service_account"),
  namespace: nonEmptyString,
  observed_at: nonEmptyString,
  completeness: z.enum(["exact", "partial"]),
  reason_codes: z.array(z.string()).default([]),
  items: z.array(kubernetesRestrictedResourceTypeSchema).default([]),
});

const settingsKubernetesRulesEvidenceSchema = z.discriminatedUnion("status", [
  settingsObservedKubernetesRulesSchema,
  settingsUnavailableEvidenceSchema,
]);

const settingsRestrictedResourceTypesEvidenceSchema = z.discriminatedUnion("status", [
  settingsObservedRestrictedResourceTypesSchema,
  settingsUnavailableEvidenceSchema,
]);

export const settingsAccessProfileResponseSchema = z.strictObject({
  workspace_id: nonEmptyString,
  user_id: nonEmptyString,
  cluster_id: nonEmptyString,
  roles: z.array(z.string()),
  authority: z.literal("opsia_rbac"),
  permissions: z.array(settingsAccessDecisionSchema),
  kubernetes_rules: settingsKubernetesRulesEvidenceSchema,
  restricted_resource_types: settingsRestrictedResourceTypesEvidenceSchema,
  revision: revisionHashSchema,
});

// ── Browser refresh policies (GET /api/refresh-policies) ──
const refreshPolicyKeySchema = z.enum([
  "dashboard",
  "issues_audit",
  "applications",
  "resource_list",
  "resource_list_slow",
  "changes",
  "metrics_kubernetes",
  "metrics_prometheus",
  "metrics_pvc",
  "metrics_rightsizing",
  "gitops_rows",
  "gitops_counts",
  "helm_list",
  "helm_detail",
  "cost_summary",
  "cost_trend",
  "cost_nodes",
  "port_sessions",
]);

export const browserRefreshPolicySchema = z.strictObject({
  stale_after_seconds: z.number().nullable().default(null),
  refresh_after_seconds: z.number(),
  keep_last_success: z.literal(true),
  pause_when_hidden: z.literal(true),
  event_invalidation: z.boolean(),
  retry_after_seconds: z.number().nullable().default(null),
  retry_limit: z.number().int().nullable().default(null),
  post_mutation_refresh_after_seconds: z.number().nullable().default(null),
});

export const browserRefreshPoliciesResponseSchema = z.strictObject({
  revision: revisionHashSchema,
  policies: z.record(refreshPolicyKeySchema, browserRefreshPolicySchema),
});

export type UiPreferences = z.infer<typeof uiPreferencesSchema>;
export type UiPreferencesResponse = z.output<typeof uiPreferencesResponseSchema>;
export type UiPreferencesUpdateRequest = z.output<typeof uiPreferencesUpdateRequestSchema>;
export type UiPreferencesUpdateResponse = z.output<typeof uiPreferencesUpdateResponseSchema>;
export type SettingsAccessProfileResponse = z.output<typeof settingsAccessProfileResponseSchema>;
export type SettingsAccessDecision = z.output<typeof settingsAccessDecisionSchema>;
export type BrowserRefreshPolicy = z.output<typeof browserRefreshPolicySchema>;
export type BrowserRefreshPoliciesResponse = z.output<typeof browserRefreshPoliciesResponseSchema>;
export type RefreshPolicyKey = z.infer<typeof refreshPolicyKeySchema>;
