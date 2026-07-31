import { useCallback, useEffect, useMemo, useState } from "react";

import type { InventoryResource } from "../api/inventory-schemas";
import { kindToResourceType } from "./inventoryResourcesFeed";
import {
  loadSharedInventoryResourceDetail,
  SHARED_RESOURCE_DETAIL_EVENT_LIMIT,
  SHARED_RESOURCE_DETAIL_RELATED_LIMIT,
} from "./resourceDetailFeed";

export interface ResourceEventView {
  id: string;
  reason: string | null;
  message: string | null;
  type: string | null;
  count: number | null;
  lastAt: string | null;
}

export interface ResourceEventsView {
  status: "idle" | "loading" | "ready" | "unavailable" | "error";
  items: ResourceEventView[];
  retry: () => void;
}

const IDLE = { status: "idle" as const, items: [] as ResourceEventView[] };
export const RESOURCE_DETAIL_RELATED_LIMIT = SHARED_RESOURCE_DETAIL_RELATED_LIMIT;
export const RESOURCE_DETAIL_EVENT_LIMIT = SHARED_RESOURCE_DETAIL_EVENT_LIMIT;

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function toResourceEvent(resource: InventoryResource): ResourceEventView {
  const summary = resource.summary;
  return {
    id: resource.inventory_key,
    reason: text(summary.reason) ?? text(resource.name),
    message: text(summary.message),
    type: text(summary.type) ?? text(resource.status),
    count: number(summary.count),
    lastAt: text(summary.last_timestamp) ?? resource.observed_at ?? resource.updated_at,
  };
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

export function useResourceEvents(
  clusterId: string | null,
  kind: string,
  namespace: string | null,
  name: string,
): ResourceEventsView {
  const cid = clusterId?.trim() ?? "";
  const resourceName = name.trim();
  const ns = namespace?.trim() || null;
  const key = cid && kind && resourceName ? [cid, kind, ns ?? "", resourceName].join("\u0000") : "";
  const [nonce, setNonce] = useState(0);
  const [state, setState] = useState<{ key: string; status: ResourceEventsView["status"]; items: ResourceEventView[] }>({ key: "", ...IDLE });

  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    loadSharedInventoryResourceDetail(cid, {
      resourceType: kindToResourceType(kind), kind, name: resourceName, namespace: ns,
    }).then((detail) => {
      if (!controller.signal.aborted) setState({ key, status: "ready", items: detail.events.map(toResourceEvent) });
    }).catch((error: unknown) => {
      if (controller.signal.aborted || isAbortError(error)) return;
      const unavailable = typeof error === "object" && error !== null && "status" in error
        && (error as { status?: unknown }).status === 404;
      setState({ key, status: unavailable ? "unavailable" : "error", items: [] });
    });
    return () => controller.abort();
  }, [cid, key, kind, nonce, ns, resourceName]);

  const retry = useCallback(() => {
    setState((previous) => ({ key: previous.key, status: "loading", items: [] }));
    setNonce((value) => value + 1);
  }, []);
  const result = useMemo(() => !key ? IDLE : state.key === key
    ? { status: state.status, items: state.items }
    : { status: "loading" as const, items: [] }, [key, state]);
  return { ...result, retry };
}
