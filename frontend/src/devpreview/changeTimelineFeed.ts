import { useCallback, useEffect, useMemo, useState } from "react";

import {
  getTimelineSnapshot,
  subscribeTimelineEvents,
} from "../api/timeline";
import type {
  TimelineEndpointCapabilityDescriptor,
  TimelineEndpointCoverage,
  TimelineEndpointEvent,
  TimelineEndpointQuery,
} from "../api/timeline-schemas";
import { loadSharedTimelineCapabilities } from "./timelineCapabilitiesFeed";
import { operationalMessageLabel } from "./statusLabel";

// Timeline은 보존 스냅샷과 opaque cursor SSE를 하나의 계약으로 소비한다.
// 브라우저 폴링이나 이름 기반 합성 없이, 권한이 있는 모든 클러스터를 서버의
// query 한도(100 scopes) 단위로 분할해 snapshot → live delta → resync를 반복한다.

export type ChangeTimelineStatus =
  | "loading"
  | "ready"
  | "partial"
  | "stale"
  | "unavailable";

export interface ChangeEventView {
  id: string;
  kind: "inventory_event" | "incident" | "deployment" | "gitops_change";
  activity: TimelineEndpointEvent["activity"];
  occurredMs: number;
  rawTitle: string;
  title: string;
  severity: "info" | "warning" | "critical" | "unknown";
}

export interface ChangeBucketView {
  startMs: number;
  endMs: number;
  total: number;
  warnings: number;
}

export interface ChangeTimelineGapView {
  from: number;
  to: number;
}

export interface ChangeTimelineFeed {
  status: ChangeTimelineStatus;
  events: ChangeEventView[];
  buckets: ChangeBucketView[];
  gaps: ChangeTimelineGapView[];
  windowFromMs: number;
  windowToMs: number;
  transport: "timeline-sse";
  observedScopes: number;
  streamingScopes: number;
  totalScopes: number;
}

interface TimelineChunkState {
  events: Map<string, TimelineEndpointEvent>;
  coverage: TimelineEndpointCoverage[];
  connected: boolean;
  initialized: boolean;
  scopeCount: number;
}

const WINDOW_MS = 24 * 60 * 60 * 1000;
const BUCKET_MS = 60 * 60 * 1000;
const MAX_QUERY_SCOPES = 100;
const MAX_VISIBLE_EVENTS = 10_000;
const DEFAULT_RECONNECT_MS = 1_000;

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

function timelineKind(event: TimelineEndpointEvent): ChangeEventView["kind"] {
  if (event.event_type === "incident") return "incident";
  if (event.event_type === "deployment") return "deployment";
  if (event.event_type === "gitops_change") return "gitops_change";
  return "inventory_event";
}

export function toChangeEvent(event: TimelineEndpointEvent): ChangeEventView {
  return {
    id: event.event_id,
    kind: timelineKind(event),
    activity: event.activity,
    occurredMs: Date.parse(event.occurred_at),
    rawTitle: event.title,
    title: operationalMessageLabel(event.title),
    severity: event.severity,
  };
}

export function buildTimelineBuckets(
  events: readonly ChangeEventView[],
  fromMs: number,
  toMs: number,
): ChangeBucketView[] {
  const buckets: ChangeBucketView[] = [];
  for (let startMs = fromMs; startMs < toMs; startMs += BUCKET_MS) {
    buckets.push({
      startMs,
      endMs: Math.min(startMs + BUCKET_MS, toMs),
      total: 0,
      warnings: 0,
    });
  }
  for (const event of events) {
    if (event.occurredMs < fromMs || event.occurredMs >= toMs) continue;
    const index = Math.floor((event.occurredMs - fromMs) / BUCKET_MS);
    const bucket = buckets[index];
    if (!bucket) continue;
    bucket.total += 1;
    if (event.severity === "warning" || event.severity === "critical") {
      bucket.warnings += 1;
    }
  }
  return buckets;
}

function buildTimelineQuery(
  capabilities: TimelineEndpointCapabilityDescriptor,
  workspaceId: string,
  clusterIds: readonly string[],
): TimelineEndpointQuery | null {
  const surface = capabilities.control_surface;
  const view = surface.views[0]?.id;
  const grouping = surface.groupings[0]?.id;
  const sort = surface.sorts[0]?.id;
  const activity = surface.activity[0]?.activity;
  if (!view || !grouping || !sort || !activity || clusterIds.length === 0) return null;

  const bounds = capabilities.query_bounds;
  const range = surface.time_ranges.find(
    (option) => option.id === surface.default_time_range_id,
  );
  const span = Math.min(
    WINDOW_MS,
    range?.duration_ms ?? capabilities.max_retained_range_ms,
    bounds.max_window_ms,
  );
  const toMs = bounds.server_now_ms;
  const fromMs = Math.max(bounds.earliest_queryable_ms, toMs - span);
  if (fromMs >= toMs) return null;

  return {
    scopes: clusterIds.map((clusterId) => ({
      workspace_id: workspaceId,
      cluster_id: clusterId,
      namespaces: [],
      freshness: "live" as const,
    })),
    window: { from_ms: fromMs, to_ms: toMs },
    filters: {
      // Each server control option is an exact allowed selection. Combining
      // several options creates a selection the server intentionally rejects.
      activity: [...activity],
      kinds: [],
      include_deleted: surface.deleted.default,
      pinned_only: false,
      query: "",
    },
    mode: "live",
    grouping,
    sort,
    view,
    range_id: surface.default_time_range_id,
    lens_zoom_rung: surface.default_lens_zoom_rung,
  };
}

function chunksOf<T>(values: readonly T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let index = 0; index < values.length; index += size) {
    chunks.push(values.slice(index, index + size));
  }
  return chunks;
}

function waitForReconnect(signal: AbortSignal, delayMs: number): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const timer = window.setTimeout(resolve, delayMs);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      resolve();
    }, { once: true });
  });
}

/**
 * Full Timeline push projection. Every chunk starts with a complete retained
 * snapshot and resumes from its opaque cursor. A terminal frame or transient
 * disconnect obtains a new snapshot before reopening, so no stale cursor is
 * reused across an authorization/capability change.
 */
export function useChangeTimeline(
  workspaceId: string | null,
  clusterIds: readonly string[],
): ChangeTimelineFeed {
  const clusterKey = [...new Set(clusterIds.filter(Boolean))].sort().join("\u0000");
  const scopeKey = `${workspaceId ?? ""}\u0000${clusterKey}`;
  const [windowToMs] = useState(() => Date.now());
  const windowFromMs = windowToMs - WINDOW_MS;
  const emptyFeed = useMemo<ChangeTimelineFeed>(() => ({
    status: workspaceId === null
      ? "unavailable"
      : clusterKey === ""
        ? "ready"
        : "loading",
    events: [],
    buckets: buildTimelineBuckets([], windowFromMs, windowToMs),
    gaps: [],
    windowFromMs,
    windowToMs,
    transport: "timeline-sse",
    observedScopes: 0,
    streamingScopes: 0,
    totalScopes: 0,
  }), [clusterKey, windowFromMs, windowToMs, workspaceId]);
  const [scopedFeed, setScopedFeed] = useState<{
    scopeKey: string;
    feed: ChangeTimelineFeed;
  }>({ scopeKey, feed: emptyFeed });
  const feed = scopedFeed.scopeKey === scopeKey ? scopedFeed.feed : emptyFeed;
  const setFeed = useCallback((
    update: ChangeTimelineFeed
      | ((current: ChangeTimelineFeed) => ChangeTimelineFeed),
  ) => {
    setScopedFeed((current) => {
      const currentFeed = current.scopeKey === scopeKey ? current.feed : emptyFeed;
      return {
        scopeKey,
        feed: typeof update === "function" ? update(currentFeed) : update,
      };
    });
  }, [emptyFeed, scopeKey]);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    const states = new Map<number, TimelineChunkState>();

    const publish = (totalScopes: number) => {
      if (signal.aborted) return;
      const allStates = [...states.values()];
      const combined = new Map<string, TimelineEndpointEvent>();
      const gaps = new Map<string, ChangeTimelineGapView>();
      let connectedScopes = 0;
      let observedScopes = 0;
      let initializedChunks = 0;
      for (const state of allStates) {
        if (state.connected) connectedScopes += state.scopeCount;
        if (state.initialized) {
          initializedChunks += 1;
          observedScopes += state.scopeCount;
        }
        for (const [eventId, event] of state.events) combined.set(eventId, event);
        for (const gap of state.coverage) {
          gaps.set(`${gap.from_ms}:${gap.to_ms}`, { from: gap.from_ms, to: gap.to_ms });
        }
      }
      const events = [...combined.values()]
        .map(toChangeEvent)
        .filter((event) => Number.isFinite(event.occurredMs))
        .sort((left, right) => left.occurredMs - right.occurredMs
          || left.kind.localeCompare(right.kind)
          || left.id.localeCompare(right.id))
        .slice(-MAX_VISIBLE_EVENTS);
      const scopeChunks = states.size;
      setFeed({
        status: scopeChunks === 0
          ? "ready"
          : initializedChunks === 0
            ? "loading"
            : connectedScopes === totalScopes
            ? "ready"
            : connectedScopes > 0
              ? "partial"
              : "stale",
        events,
        buckets: buildTimelineBuckets(events, windowFromMs, windowToMs),
        gaps: [...gaps.values()].sort((left, right) => left.from - right.from),
        windowFromMs,
        windowToMs,
        transport: "timeline-sse",
        observedScopes,
        streamingScopes: connectedScopes,
        totalScopes,
      });
    };

    const runChunk = async (
      index: number,
      query: TimelineEndpointQuery,
      totalScopes: number,
    ) => {
      while (!signal.aborted) {
        let reconnectMs = DEFAULT_RECONNECT_MS;
        try {
          const snapshot = await getTimelineSnapshot({ query }, signal);
          if (signal.aborted) return;
          reconnectMs = snapshot.snapshot.policy.reconnect.min_delay_ms;
          states.set(index, {
            events: new Map(snapshot.snapshot.events.map((event) => [event.event_id, event])),
            coverage: snapshot.snapshot.coverage,
            connected: false,
            initialized: true,
            scopeCount: query.scopes.length,
          });
          publish(totalScopes);

          for await (const frame of subscribeTimelineEvents(
            { query, after: snapshot.snapshot.cursor },
            {
              signal,
              onLifecycle: (lifecycle) => {
                const current = states.get(index);
                if (!current || signal.aborted) return;
                current.connected = lifecycle.state === "connected";
                publish(totalScopes);
              },
            },
          )) {
            if (signal.aborted) return;
            const current = states.get(index);
            if (!current) continue;
            if (frame.kind === "event") {
              current.events.set(frame.event.event_id, frame.event);
            } else if (frame.kind === "coverage") {
              current.coverage = frame.coverage;
            } else {
              current.connected = false;
            }
            publish(totalScopes);
          }
        } catch (cause: unknown) {
          if (signal.aborted || isAbortError(cause)) return;
          const current = states.get(index);
          if (current) current.connected = false;
          publish(totalScopes);
        }
        await waitForReconnect(signal, reconnectMs);
      }
    };

    void (async () => {
      try {
        if (workspaceId === null) {
          setFeed((current) => ({ ...current, status: "unavailable" }));
          return;
        }
        if (clusterKey === "") {
          publish(0);
          return;
        }
        const capabilities = await loadSharedTimelineCapabilities();
        if (signal.aborted) return;
        const scopedClusterIds = clusterKey.split("\u0000");
        const chunks = chunksOf(scopedClusterIds, MAX_QUERY_SCOPES);
        if (chunks.length === 0) {
          publish(0);
          return;
        }
        const tasks = chunks.map((clusterChunk, index) => {
          const query = buildTimelineQuery(
            capabilities,
            workspaceId,
            clusterChunk,
          );
          if (!query) return Promise.resolve();
          states.set(index, {
            events: new Map(),
            coverage: [],
            connected: false,
            initialized: false,
            scopeCount: query.scopes.length,
          });
          return runChunk(index, query, scopedClusterIds.length);
        });
        publish(scopedClusterIds.length);
        await Promise.all(tasks);
      } catch (cause: unknown) {
        if (signal.aborted || isAbortError(cause)) return;
        setFeed((current) => ({ ...current, status: "unavailable" }));
      }
    })();

    return () => controller.abort();
  }, [clusterKey, setFeed, windowFromMs, windowToMs, workspaceId]);

  return feed;
}
