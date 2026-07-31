import { z } from "zod";

const nonEmpty = z.string().min(1);

export const kubernetesSubjectSchema = z.strictObject({
  kind: z.enum(["ServiceAccount", "User", "Group"]),
  namespace: z.string(),
  name: nonEmpty,
});

export const kubernetesRoleRefSchema = z.strictObject({
  kind: z.enum(["Role", "ClusterRole"]),
  namespace: z.string(),
  name: nonEmpty,
});

export const kubernetesBindingRefSchema = z.strictObject({
  kind: z.enum(["RoleBinding", "ClusterRoleBinding"]),
  namespace: z.string(),
  name: nonEmpty,
  role: kubernetesRoleRefSchema,
});

export const kubernetesPolicyRuleSchema = z.strictObject({
  verbs: z.array(z.string()),
  api_groups: z.array(z.string()),
  resources: z.array(z.string()),
  resource_names: z.array(z.string()),
  non_resource_urls: z.array(z.string()),
});

export const kubernetesBindingRulesSchema = z.strictObject({
  binding: kubernetesBindingRefSchema,
  role: kubernetesRoleRefSchema,
  rules: z.array(kubernetesPolicyRuleSchema),
  scope_namespace: z.string(),
});

const inheritedGroupSchema = z.strictObject({
  group_name: nonEmpty,
  bindings: z.array(kubernetesBindingRulesSchema),
});

const podRefSchema = z.strictObject({ namespace: nonEmpty, name: nonEmpty });

export const kubernetesSubjectAccessResponseSchema = z.strictObject({
  type: z.literal("subject"),
  observed_at: z.string(),
  subject: kubernetesSubjectSchema,
  direct: z.array(kubernetesBindingRulesSchema),
  inherited_from_groups: z.array(inheritedGroupSchema),
  flat: z.array(kubernetesPolicyRuleSchema),
  truncated: z.boolean(),
  used_by_pods: z.array(podRefSchema),
});

const bindingWithSubjectsSchema = z.strictObject({
  binding: kubernetesBindingRefSchema,
  subjects: z.array(kubernetesSubjectSchema),
});

export const kubernetesRoleAccessResponseSchema = z.strictObject({
  type: z.literal("role"),
  observed_at: z.string(),
  role: kubernetesRoleRefSchema,
  bindings: z.array(bindingWithSubjectsSchema),
});

export const kubernetesNamespaceAccessResponseSchema = z.strictObject({
  type: z.literal("namespace"),
  observed_at: z.string(),
  namespace: nonEmpty,
  role_bindings: z.array(bindingWithSubjectsSchema),
  cluster_role_bindings_with_local_subject: z.array(bindingWithSubjectsSchema),
  service_account_count: z.number().int().nonnegative(),
});

export const kubernetesAccessUnavailableResponseSchema = z.strictObject({
  type: z.literal("unavailable"),
  reason_codes: z.array(nonEmpty).min(1),
});

/** Exact runtime discriminator for `ResourceAccessDetail`. */
export const kubernetesResourceAccessResponseSchema = z.discriminatedUnion("type", [
  kubernetesSubjectAccessResponseSchema,
  kubernetesRoleAccessResponseSchema,
  kubernetesNamespaceAccessResponseSchema,
  kubernetesAccessUnavailableResponseSchema,
]);

export type KubernetesSubjectAccessResponse = z.infer<typeof kubernetesSubjectAccessResponseSchema>;
export type KubernetesRoleAccessResponse = z.infer<typeof kubernetesRoleAccessResponseSchema>;
export type KubernetesNamespaceAccessResponse = z.infer<typeof kubernetesNamespaceAccessResponseSchema>;
export type KubernetesAccessUnavailableResponse = z.infer<typeof kubernetesAccessUnavailableResponseSchema>;
export type KubernetesResourceAccessResponse = z.infer<typeof kubernetesResourceAccessResponseSchema>;
