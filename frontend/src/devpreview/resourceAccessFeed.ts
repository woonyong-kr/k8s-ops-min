import { useCallback, useEffect, useMemo, useState } from "react";

import {
  getKubernetesNamespaceAccess,
  getKubernetesRoleAccess,
  getKubernetesSubjectAccess,
} from "../api/resource-access";
import type { KubernetesResourceAccessResponse } from "../api/resource-access-schemas";

export type ResourceAccessStatus = "idle" | "loading" | "ready" | "unavailable" | "error";

export interface ResourceAccessView {
  status: ResourceAccessStatus;
  data: KubernetesResourceAccessResponse | null;
  retry: () => void;
}

const IDLE = { status: "idle" as const, data: null };

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

export function useResourceAccess(
  clusterId: string | null,
  kind: string,
  namespace: string | null,
  name: string,
): ResourceAccessView {
  const cid = clusterId?.trim() ?? "";
  const ns = namespace?.trim() ?? "";
  const resourceName = name.trim();
  const mode = kind === "ServiceAccount" ? "subject"
    : kind === "Role" || kind === "ClusterRole" ? "role"
    : kind === "Namespace" || ns !== "" ? "namespace"
    : "idle";
  const key = [cid, mode, kind, ns, resourceName].join("\u0000");
  const [nonce, setNonce] = useState(0);
  const [state, setState] = useState<{ key: string; status: ResourceAccessStatus; data: KubernetesResourceAccessResponse | null }>({ key: "", ...IDLE });

  useEffect(() => {
    if (cid === "" || resourceName === "" || mode === "idle") return;
    if ((mode === "subject" || kind === "Role") && ns === "") return;
    const controller = new AbortController();
    const request = mode === "subject"
      ? getKubernetesSubjectAccess({ clusterId: cid, kind: "ServiceAccount", namespace: ns, name: resourceName }, controller.signal)
      : mode === "role"
        ? getKubernetesRoleAccess({ clusterId: cid, kind: kind as "Role" | "ClusterRole", namespace: ns, name: resourceName }, controller.signal)
        : getKubernetesNamespaceAccess({ clusterId: cid, namespace: kind === "Namespace" ? resourceName : ns }, controller.signal);
    request.then((data) => {
      if (!controller.signal.aborted) setState({ key, status: "ready", data });
    }).catch((error: unknown) => {
      if (controller.signal.aborted || isAbortError(error)) return;
      const status = typeof error === "object" && error !== null && "status" in error
        && (error as { status?: unknown }).status === 503 ? "unavailable" : "error";
      setState({ key, status, data: null });
    });
    return () => controller.abort();
  }, [cid, key, kind, mode, nonce, ns, resourceName]);

  const retry = useCallback(() => {
    setState((previous) => ({ key: previous.key, status: "loading", data: null }));
    setNonce((value) => value + 1);
  }, []);
  const result = useMemo(() => {
    if (cid === "" || resourceName === "" || mode === "idle") return IDLE;
    if ((mode === "subject" || kind === "Role") && ns === "") return IDLE;
    return state.key === key ? { status: state.status, data: state.data } : { status: "loading" as const, data: null };
  }, [cid, key, kind, mode, ns, resourceName, state]);
  return { ...result, retry };
}
