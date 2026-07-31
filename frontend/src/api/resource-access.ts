import { apiRequest, type ApiPath } from "./client";
import {
  kubernetesNamespaceAccessResponseSchema,
  kubernetesRoleAccessResponseSchema,
  kubernetesSubjectAccessResponseSchema,
  type KubernetesNamespaceAccessResponse,
  type KubernetesRoleAccessResponse,
  type KubernetesSubjectAccessResponse,
} from "./resource-access-schemas";
import { encodePathSegment, withQuery } from "./url";

export function getKubernetesSubjectAccess(
  input: { clusterId: string; kind: "ServiceAccount" | "User" | "Group"; namespace?: string | null; name: string },
  signal?: AbortSignal,
): Promise<KubernetesSubjectAccessResponse> {
  const subjectPath = input.kind === "ServiceAccount"
    ? `/api/rbac/subject/${encodePathSegment(input.kind)}/${encodePathSegment(input.namespace ?? "")}/${encodePathSegment(input.name)}`
    : `/api/rbac/subject/${encodePathSegment(input.kind)}/${encodePathSegment(input.name)}`;
  return apiRequest(withQuery(subjectPath as ApiPath, [["cluster_id", input.clusterId]]), kubernetesSubjectAccessResponseSchema, { signal });
}

export function getKubernetesRoleAccess(
  input: { clusterId: string; kind: "Role" | "ClusterRole"; namespace?: string | null; name: string },
  signal?: AbortSignal,
): Promise<KubernetesRoleAccessResponse> {
  const namespace = input.kind === "ClusterRole" ? "_" : input.namespace ?? "";
  const rolePath = `/api/rbac/role/${encodePathSegment(input.kind)}/${encodePathSegment(namespace)}/${encodePathSegment(input.name)}` as ApiPath;
  return apiRequest(withQuery(rolePath, [["cluster_id", input.clusterId]]), kubernetesRoleAccessResponseSchema, { signal });
}

export function getKubernetesNamespaceAccess(
  input: { clusterId: string; namespace: string },
  signal?: AbortSignal,
): Promise<KubernetesNamespaceAccessResponse> {
  const namespacePath = `/api/rbac/namespace/${encodePathSegment(input.namespace)}` as ApiPath;
  return apiRequest(withQuery(namespacePath, [["cluster_id", input.clusterId]]), kubernetesNamespaceAccessResponseSchema, { signal });
}
