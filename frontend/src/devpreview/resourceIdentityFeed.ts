import { useEffect, useMemo, useState } from "react";

import { loadSharedInventoryResourceDetail } from "./resourceDetailFeed";

export interface ResourceIdentityView {
  status: "idle" | "loading" | "ready" | "unavailable" | "error";
  resourceId: string;
}

const IDLE: ResourceIdentityView = { status: "idle", resourceId: "" };

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

/**
 * Resolves the server-issued inventory key from an exact topology identity.
 * Topology `node_id` is intentionally never reused as a manifest resource id.
 */
export function useResourceIdentity(
  enabled: boolean,
  clusterId: string | null,
  resourceType: string,
  kind: string,
  namespace: string | null,
  name: string,
): ResourceIdentityView {
  const cid = clusterId?.trim() ?? "";
  const rt = resourceType.trim();
  const resourceName = name.trim();
  const ns = namespace?.trim() || null;
  const key = enabled && cid && rt && kind && resourceName
    ? [cid, rt, kind, ns ?? "", resourceName].join("\u0000")
    : "";
  const [state, setState] = useState<ResourceIdentityView & { key: string }>({ ...IDLE, key: "" });

  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    void loadSharedInventoryResourceDetail(cid, {
      resourceType: rt,
      kind,
      namespace: ns,
      name: resourceName,
    })
      .then((detail) => {
        if (controller.signal.aborted) return;
        setState({ key, status: "ready", resourceId: detail.resource.inventory_key });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        const unavailable = typeof error === "object" && error !== null && "status" in error
          && (error as { status?: unknown }).status === 404;
        setState({ key, status: unavailable ? "unavailable" : "error", resourceId: "" });
      });
    return () => controller.abort();
  }, [cid, key, kind, ns, resourceName, rt]);

  return useMemo(() => {
    if (!key) return IDLE;
    if (state.key !== key) return { status: "loading", resourceId: "" };
    return { status: state.status, resourceId: state.resourceId };
  }, [key, state]);
}
