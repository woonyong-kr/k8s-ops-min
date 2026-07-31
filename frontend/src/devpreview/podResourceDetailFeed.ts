import { useCallback, useEffect, useMemo, useState } from "react";

import type { InventoryResourceDetail } from "../api/inventory-schemas";
import { podContainerSummary, type PodContainerView } from "./podContainerSummary";
import { kindToResourceType } from "./inventoryResourcesFeed";
import { KUBERNETES_KIND } from "./kubernetesKinds";
import { conditionItemsFromSummary, type ResourceConditionItem } from "./resourceConditions";
import {
  toResourceEvent,
  type ResourceEventView,
  type ResourceEventsView,
} from "./resourceEventsFeed";
import { loadSharedInventoryResourceDetail } from "./resourceDetailFeed";

type PodResourceDetailStatus = ResourceEventsView["status"];

export interface PodResourceSummaryView {
  status: PodResourceDetailStatus;
  nodeName: string | null;
  podIp: string | null;
  hostIp: string | null;
  serviceAccountName: string | null;
  containers: PodContainerView[];
  containerPortsComplete: boolean | null;
  conditions: ResourceConditionItem[];
}

export interface PodResourceDetailView {
  status: PodResourceDetailStatus;
  summary: PodResourceSummaryView;
  events: ResourceEventsView;
  retry: () => void;
}

interface PodResourceDetailState {
  key: string;
  status: PodResourceDetailStatus;
  summary: Omit<PodResourceSummaryView, "status">;
  events: ResourceEventView[];
}

const POD_KIND = KUBERNETES_KIND.pod;
const POD_RESOURCE_TYPE = kindToResourceType(POD_KIND);
const DETAIL_KEY_SEPARATOR = "\u0000";
const POD_SUMMARY_KEYS = {
  nodeName: "node_name",
  podIp: "pod_ip",
  hostIp: "host_ip",
  serviceAccountName: "service_account_name",
} as const;
const EMPTY_CONTAINERS: PodContainerView[] = [];
const EMPTY_CONDITIONS: ResourceConditionItem[] = [];
const EMPTY_SUMMARY = {
  nodeName: null,
  podIp: null,
  hostIp: null,
  serviceAccountName: null,
  containers: EMPTY_CONTAINERS,
  containerPortsComplete: null,
  conditions: EMPTY_CONDITIONS,
};
const EMPTY_EVENTS: ResourceEventView[] = [];
const NOOP = () => undefined;

const IDLE_SUMMARY: PodResourceSummaryView = { status: "idle", ...EMPTY_SUMMARY };
const LOADING_SUMMARY: PodResourceSummaryView = { status: "loading", ...EMPTY_SUMMARY };
const IDLE_EVENTS: ResourceEventsView = { status: "idle", items: EMPTY_EVENTS, retry: NOOP };
const LOADING_EVENTS: ResourceEventsView = { status: "loading", items: EMPTY_EVENTS, retry: NOOP };

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

function isUnavailable(error: unknown): boolean {
  return typeof error === "object" && error !== null && "status" in error
    && (error as { status?: unknown }).status === 404;
}

function toSummary(detail: InventoryResourceDetail): Omit<PodResourceSummaryView, "status"> {
  const summary = detail.resource.summary;
  const containers = podContainerSummary(summary);
  return {
    nodeName: text(summary[POD_SUMMARY_KEYS.nodeName]),
    podIp: text(summary[POD_SUMMARY_KEYS.podIp]),
    hostIp: text(summary[POD_SUMMARY_KEYS.hostIp]),
    serviceAccountName: text(summary[POD_SUMMARY_KEYS.serviceAccountName]),
    containers: containers.containers,
    containerPortsComplete: containers.containerPortsComplete,
    conditions: conditionItemsFromSummary(summary),
  };
}

export function usePodResourceDetail(
  enabled: boolean,
  clusterId: string | null,
  namespace: string | null,
  name: string,
): PodResourceDetailView {
  const cid = clusterId?.trim() ?? "";
  const ns = namespace?.trim() || null;
  const resourceName = name.trim();
  const key = enabled && cid && ns && resourceName
    ? [cid, ns, resourceName].join(DETAIL_KEY_SEPARATOR)
    : "";
  const [nonce, setNonce] = useState(0);
  const [state, setState] = useState<PodResourceDetailState>({
    key: "",
    status: "idle",
    summary: EMPTY_SUMMARY,
    events: EMPTY_EVENTS,
  });

  useEffect(() => {
    if (!key || ns === null) return;
    const controller = new AbortController();
    void loadSharedInventoryResourceDetail(cid, {
      resourceType: POD_RESOURCE_TYPE,
      kind: POD_KIND,
      namespace: ns,
      name: resourceName,
    })
      .then((detail) => {
        if (controller.signal.aborted) return;
        setState({
          key,
          status: "ready",
          summary: toSummary(detail),
          events: detail.events.map(toResourceEvent),
        });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        setState({
          key,
          status: isUnavailable(error) ? "unavailable" : "error",
          summary: EMPTY_SUMMARY,
          events: EMPTY_EVENTS,
        });
      });
    return () => controller.abort();
  }, [cid, key, nonce, ns, resourceName]);

  const retry = useCallback(() => {
    setState((previous) => ({
      key: previous.key,
      status: "loading",
      summary: EMPTY_SUMMARY,
      events: EMPTY_EVENTS,
    }));
    setNonce((value) => value + 1);
  }, []);

  return useMemo(() => {
    if (!key) return {
      status: "idle",
      summary: IDLE_SUMMARY,
      events: IDLE_EVENTS,
      retry,
    };
    if (state.key !== key) return {
      status: "loading",
      summary: LOADING_SUMMARY,
      events: LOADING_EVENTS,
      retry,
    };
    const items = state.status === "ready" ? state.events : EMPTY_EVENTS;
    return {
      status: state.status,
      summary: { status: state.status, ...state.summary },
      events: { status: state.status, items, retry },
      retry,
    };
  }, [key, retry, state]);
}
