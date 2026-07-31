// @vitest-environment jsdom

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  TimelineEndpointCapabilityDescriptor,
  TimelineEndpointEvent,
  TimelineEndpointStreamFrame,
} from "../api/timeline-schemas";

const getTimelineCapabilities = vi.fn();
const getTimelineSnapshot = vi.fn();
const subscribeTimelineEvents = vi.fn();

vi.mock("../api/timeline", () => ({
  getTimelineCapabilities: (...args: unknown[]) => getTimelineCapabilities(...args),
  getTimelineSnapshot: (...args: unknown[]) => getTimelineSnapshot(...args),
  subscribeTimelineEvents: (...args: unknown[]) => subscribeTimelineEvents(...args),
}));

import { buildTimelineBuckets, toChangeEvent, useChangeTimeline } from "./changeTimelineFeed";

const capabilities = {
  selected_source_mode: "retained",
  available_source_modes: ["retained"],
  max_retained_range_ms: 86_400_000,
  query_bounds: {
    server_now_ms: Date.parse("2026-07-24T05:00:00Z"),
    earliest_queryable_ms: Date.parse("2026-07-23T05:00:00Z"),
    max_window_ms: 86_400_000,
  },
  control_surface: {
    views: [{ id: "list", label: "List" }],
    groupings: [{ id: "none", label: "None" }],
    sorts: [{ id: "occurred_at", label: "Time" }],
    activity: [
      {
        id: "all",
        label: "All",
        activity: [],
        problems_activity: ["warning", "unhealthy"],
      },
      {
        id: "changes",
        label: "Changes",
        activity: ["change"],
        problems_activity: ["unhealthy"],
      },
      {
        id: "k8s_events",
        label: "K8s Events",
        activity: ["k8s_event"],
        problems_activity: ["warning"],
      },
    ],
    deleted: { default: true },
    time_ranges: [{ id: "24h", label: "24h", duration_ms: 86_400_000 }],
    default_time_range_id: "24h",
    lens_zoom_rungs: [{ id: "hour", label: "Hour" }],
    default_lens_zoom_rung: "hour",
    pins: { availability: "available" },
  },
} as unknown as TimelineEndpointCapabilityDescriptor;

function event(eventId: string, occurredAt: string): TimelineEndpointEvent {
  return {
    event_id: eventId,
    source: "inventory",
    source_key: eventId,
    native_id: eventId,
    activity: "change",
    occurred_at: occurredAt,
    scope: {
      workspace_id: "workspace-a",
      cluster_id: "cluster-a",
      namespaces: [],
      freshness: "live",
    },
    subject: {
      kind: "inventory_locator",
      inventory_key: eventId,
      api_group: "apps",
      version: "v1",
      resource_kind: "Deployment",
      namespace: "sandbox",
      name: "api",
    },
    resource: null,
    event_type: "update",
    severity: "warning",
    title: `Changed ${eventId}`,
    owner: null,
    metadata: {},
  };
}

function snapshot(events: TimelineEndpointEvent[]) {
  return {
    snapshot: {
      kind: "snapshot" as const,
      cursor: { token: `cursor-${events[0]?.event_id ?? "empty"}` },
      scopes: [{
        workspace_id: "workspace-a",
        cluster_id: "cluster-a",
        namespaces: [],
        freshness: "live" as const,
      }],
      policy: {
        max_batch_events: 1_000,
        max_frames_per_second: 10,
        retention_seconds: 86_400,
        resume: "cursor" as const,
        hidden_tab: "coalesce" as const,
        reconnect: {
          min_delay_ms: 100,
          max_delay_ms: 1_000,
          strategy: "full_jitter_exponential" as const,
        },
        live_session: {
          max_age_ms: 60_000,
          strategy: "replace_with_snapshot" as const,
        },
      },
      capabilities,
      events,
      coverage: [],
      pin_set_revision: null,
    },
    end: {
      kind: "end" as const,
      cursor: { token: `cursor-${events[0]?.event_id ?? "empty"}` },
      pin_set_revision: null,
    },
  };
}

async function* oneFrameThenWait(
  frame: TimelineEndpointStreamFrame,
  signal?: AbortSignal,
) {
  yield frame;
  await new Promise<void>((resolve) => {
    if (signal?.aborted) {
      resolve();
      return;
    }
    signal?.addEventListener("abort", () => resolve(), { once: true });
  });
}

async function* waitUntilAbort(signal?: AbortSignal) {
  yield* [] as TimelineEndpointStreamFrame[];
  await new Promise<void>((resolve) => {
    if (signal?.aborted) {
      resolve();
      return;
    }
    signal?.addEventListener("abort", () => resolve(), { once: true });
  });
}

afterEach(() => {
  getTimelineCapabilities.mockReset();
  getTimelineSnapshot.mockReset();
  subscribeTimelineEvents.mockReset();
});

describe("Timeline push feed", () => {
  it("fails closed without a workspace and performs no timeline reads", () => {
    const { result } = renderHook(() =>
      useChangeTimeline(null, ["cluster-a"]));

    expect(result.current).toEqual(expect.objectContaining({
      status: "unavailable",
      events: [],
      observedScopes: 0,
      streamingScopes: 0,
      totalScopes: 0,
    }));
    expect(getTimelineCapabilities).not.toHaveBeenCalled();
    expect(getTimelineSnapshot).not.toHaveBeenCalled();
    expect(subscribeTimelineEvents).not.toHaveBeenCalled();
  });

  it("represents an authorized zero-cluster workspace without opening a stream", () => {
    const { result } = renderHook(() =>
      useChangeTimeline("workspace-empty", []));

    expect(result.current).toEqual(expect.objectContaining({
      status: "ready",
      events: [],
      observedScopes: 0,
      streamingScopes: 0,
      totalScopes: 0,
    }));
    expect(getTimelineCapabilities).not.toHaveBeenCalled();
    expect(getTimelineSnapshot).not.toHaveBeenCalled();
    expect(subscribeTimelineEvents).not.toHaveBeenCalled();
  });

  it("publishes the retained snapshot and direct cursor SSE delta without polling", async () => {
    const baseline = event("event-1", "2026-07-24T04:00:00Z");
    const pushed = event("event-2", "2026-07-24T04:01:00Z");
    getTimelineCapabilities.mockResolvedValue(capabilities);
    getTimelineSnapshot.mockResolvedValue(snapshot([baseline]));
    subscribeTimelineEvents.mockImplementation(
      (_input: unknown, subscription: {
        signal?: AbortSignal;
        onLifecycle?: (value: { state: "connected" }) => void;
      }) => {
        subscription.onLifecycle?.({ state: "connected" });
        return oneFrameThenWait({
          kind: "event",
          cursor: { token: "cursor-2" },
          event: pushed,
          pin_set_revision: null,
        }, subscription.signal);
      },
    );

    const { result, unmount } = renderHook(() =>
      useChangeTimeline("workspace-a", ["cluster-a"]));

    await waitFor(() => expect(result.current.events.map(({ id }) => id)).toEqual([
      "event-1",
      "event-2",
    ]));
    expect(result.current.status).toBe("ready");
    expect(result.current.transport).toBe("timeline-sse");
    expect(result.current.observedScopes).toBe(1);
    expect(result.current.streamingScopes).toBe(1);
    expect(getTimelineSnapshot).toHaveBeenCalledTimes(1);
    expect(getTimelineSnapshot.mock.calls[0]?.[0].query.filters.activity).toEqual([]);
    expect(subscribeTimelineEvents).toHaveBeenCalledTimes(1);
    unmount();
  });

  it("projects event identity and deterministic hourly buckets", () => {
    const projected = toChangeEvent(event("event-1", "2026-07-24T04:10:00Z"));
    const from = Date.parse("2026-07-24T04:00:00Z");
    const to = Date.parse("2026-07-24T06:00:00Z");

    expect(projected).toEqual(expect.objectContaining({
      id: "event-1",
      kind: "inventory_event",
      severity: "warning",
    }));
    expect(buildTimelineBuckets([projected], from, to)).toEqual([
      { startMs: from, endMs: from + 3_600_000, total: 1, warnings: 1 },
      { startMs: from + 3_600_000, endMs: to, total: 0, warnings: 0 },
    ]);
  });

  it("covers more than one server scope page without dropping clusters", async () => {
    const clusterIds = Array.from({ length: 101 }, (_, index) =>
      `cluster-${String(index).padStart(3, "0")}`);
    getTimelineCapabilities.mockResolvedValue(capabilities);
    getTimelineSnapshot.mockImplementation(
      ({ query }: { query: { scopes: unknown[] } }) => {
        const result = snapshot([]);
        result.snapshot.scopes = query.scopes as typeof result.snapshot.scopes;
        result.snapshot.cursor = { token: `cursor-${query.scopes.length}` };
        result.end.cursor = result.snapshot.cursor;
        return Promise.resolve(result);
      },
    );
    subscribeTimelineEvents.mockImplementation(
      (_input: unknown, subscription: {
        signal?: AbortSignal;
        onLifecycle?: (value: { state: "connected" }) => void;
      }) => {
        subscription.onLifecycle?.({ state: "connected" });
        return waitUntilAbort(subscription.signal);
      },
    );

    const { result, unmount } = renderHook(() =>
      useChangeTimeline("workspace-a", clusterIds));

    await waitFor(() => expect(result.current.streamingScopes).toBe(101));
    expect(result.current.observedScopes).toBe(101);
    expect(result.current.status).toBe("ready");
    expect(getTimelineSnapshot).toHaveBeenCalledTimes(2);
    expect(getTimelineSnapshot.mock.calls.map(
      ([input]) => input.query.scopes.length,
    ).sort((left, right) => left - right)).toEqual([1, 100]);
    unmount();
  });

  it("clears retained events immediately when the workspace scope changes", async () => {
    getTimelineCapabilities.mockResolvedValue(capabilities);
    getTimelineSnapshot.mockResolvedValue(snapshot([
      event("workspace-a-event", "2026-07-24T04:00:00Z"),
    ]));
    subscribeTimelineEvents.mockImplementation(
      (_input: unknown, subscription: { signal?: AbortSignal }) =>
        waitUntilAbort(subscription.signal),
    );
    const { result, rerender, unmount } = renderHook(
      ({ workspaceId }) => useChangeTimeline(workspaceId, ["cluster-a"]),
      { initialProps: { workspaceId: "workspace-a" as string | null } },
    );
    await waitFor(() => expect(result.current.events).toHaveLength(1));

    rerender({ workspaceId: null });

    expect(result.current.status).toBe("unavailable");
    expect(result.current.events).toEqual([]);
    expect(result.current.observedScopes).toBe(0);
    unmount();
  });
});
