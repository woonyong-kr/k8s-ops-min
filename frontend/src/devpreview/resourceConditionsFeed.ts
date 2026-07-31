import { useCallback, useEffect, useMemo, useState } from "react";

import { kindToResourceType } from "./inventoryResourcesFeed";
import { KUBERNETES_KIND } from "./kubernetesKinds";
import { loadSharedInventoryResourceDetail } from "./resourceDetailFeed";
import {
  resourceConditionSnapshot,
  type ResourceConditionEventItem,
  type ResourceConditionItem,
} from "./resourceConditions";

export type ResourceConditionsStatus = "idle" | "loading" | "ready" | "unavailable" | "error";

export interface ResourceConditionsView {
  status: ResourceConditionsStatus;
  primary: ResourceConditionItem[];
  relatedPods: ResourceConditionItem[];
  events: ResourceConditionEventItem[];
  relatedPodCount: number;
  retry: () => void;
}

// ReplicaSet conditions are often empty. The shared detail request uses this
// bounded related-resource limit once for every drawer consumer.
export const REPLICA_SET_CONDITION_RELATED_POD_LIMIT = 20;
const EMPTY_ITEMS: ResourceConditionItem[] = [];
const EMPTY_EVENTS: ResourceConditionEventItem[] = [];
const NOOP = () => undefined;
const DETAIL_KEY_SEPARATOR = "\u0000";

export const EMPTY_RESOURCE_CONDITIONS_VIEW: ResourceConditionsView = {
  status: "idle",
  primary: EMPTY_ITEMS,
  relatedPods: EMPTY_ITEMS,
  events: EMPTY_EVENTS,
  relatedPodCount: 0,
  retry: NOOP,
};

interface ResourceConditionsState {
  key: string;
  status: ResourceConditionsStatus;
  primary: ResourceConditionItem[];
  relatedPods: ResourceConditionItem[];
  events: ResourceConditionEventItem[];
  relatedPodCount: number;
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

function isUnavailable(error: unknown): boolean {
  return typeof error === "object" && error !== null && "status" in error
    && (error as { status?: unknown }).status === 404;
}

export function useResourceConditions(
  enabled: boolean,
  clusterId: string | null,
  resourceType: string | null,
  kind: string,
  namespace: string | null,
  name: string,
): ResourceConditionsView {
  const cid = clusterId?.trim() ?? "";
  const resourceName = name.trim();
  const resourceKind = kind.trim();
  const resolvedResourceType = resourceType?.trim() || kindToResourceType(resourceKind);
  const ns = namespace?.trim() || null;
  const key = enabled && cid && resourceKind && resourceName && resolvedResourceType
    ? [cid, resolvedResourceType, resourceKind, ns ?? "", resourceName].join(DETAIL_KEY_SEPARATOR)
    : "";
  const [nonce, setNonce] = useState(0);
  const [state, setState] = useState<ResourceConditionsState>({
    key: "",
    status: "idle",
    primary: EMPTY_ITEMS,
    relatedPods: EMPTY_ITEMS,
    events: EMPTY_EVENTS,
    relatedPodCount: 0,
  });

  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    const needsReplicaSetFallback = resourceKind === KUBERNETES_KIND.replicaSet;
    void loadSharedInventoryResourceDetail(cid, {
      resourceType: resolvedResourceType,
      kind: resourceKind,
      namespace: ns,
      name: resourceName,
    })
      .then((detail) => {
        if (controller.signal.aborted) return;
        setState({
          key,
          status: "ready",
          ...resourceConditionSnapshot(detail, {
            includeFallbackEvidence: needsReplicaSetFallback,
          }),
        });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        setState({
          key,
          status: isUnavailable(error) ? "unavailable" : "error",
          primary: EMPTY_ITEMS,
          relatedPods: EMPTY_ITEMS,
          events: EMPTY_EVENTS,
          relatedPodCount: 0,
        });
      });
    return () => controller.abort();
  }, [cid, key, nonce, ns, resolvedResourceType, resourceKind, resourceName]);

  const retry = useCallback(() => {
    setState((previous) => ({
      key: previous.key,
      status: "loading",
      primary: EMPTY_ITEMS,
      relatedPods: EMPTY_ITEMS,
      events: EMPTY_EVENTS,
      relatedPodCount: 0,
    }));
    setNonce((value) => value + 1);
  }, []);

  return useMemo(() => {
    if (!key) return { ...EMPTY_RESOURCE_CONDITIONS_VIEW, retry };
    if (state.key !== key) return { ...EMPTY_RESOURCE_CONDITIONS_VIEW, status: "loading", retry };
    return {
      status: state.status,
      primary: state.primary,
      relatedPods: state.relatedPods,
      events: state.events,
      relatedPodCount: state.relatedPodCount,
      retry,
    };
  }, [key, retry, state]);
}
