// @vitest-environment jsdom

import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FleetSummary } from "../api/schemas";
import { useFleetSummaryStream } from "./fleetSummaryStream";

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly url: string;
  readonly listeners = new Map<string, Set<EventListener>>();
  onerror: ((event: Event) => void) | null = null;
  closed = false;

  constructor(url: string | URL) {
    this.url = String(url);
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    const callback = typeof listener === "function"
      ? listener
      : (event: Event) => listener.handleEvent(event);
    const callbacks = this.listeners.get(type) ?? new Set<EventListener>();
    callbacks.add(callback);
    this.listeners.set(type, callbacks);
  }

  removeEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    if (typeof listener === "function") this.listeners.get(type)?.delete(listener);
  }

  close(): void {
    this.closed = true;
  }

  emitSummary(data: unknown): void {
    const event = new MessageEvent("fleet_summary", { data: JSON.stringify(data) });
    for (const listener of this.listeners.get("fleet_summary") ?? []) listener(event);
  }

  fail(): void {
    this.onerror?.(new Event("error"));
  }
}

const summary: FleetSummary = {
  clusters: [],
  totals: {
    clusters: 0,
    healthy: 0,
    warning: 0,
    critical: 0,
    stale: 0,
    unknown: 0,
    open_incidents: 0,
    pending_approvals: 0,
    running_workflows: 0,
    dead_letters: 0,
  },
};

function frame(refreshAfterMs = 5_000) {
  return {
    cursor: "cursor-a",
    revision: "a".repeat(64),
    generated_at: "2026-07-24T04:00:00Z",
    refresh_after_ms: refreshAfterMs,
    summary,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useFleetSummaryStream", () => {
  it("opens one workspace stream and delivers a validated complete payload", () => {
    const onSummary = vi.fn();
    const { result } = renderHook(() => useFleetSummaryStream("cluster-a", onSummary));

    expect(FakeEventSource.instances.map((source) => source.url)).toEqual([
      "/api/fleet/events",
    ]);
    expect(result.current.live).toBe(false);

    act(() => FakeEventSource.instances[0].emitSummary(frame()));

    expect(result.current.live).toBe(true);
    expect(onSummary).toHaveBeenCalledWith(summary);
  });

  it("does not replace data or report live for an invalid frame", () => {
    const onSummary = vi.fn();
    const { result } = renderHook(() => useFleetSummaryStream("cluster-a", onSummary));

    act(() => FakeEventSource.instances[0].emitSummary({ summary }));

    expect(result.current.live).toBe(false);
    expect(onSummary).not.toHaveBeenCalled();
  });

  it("falls back after an error while EventSource remains responsible for reconnect", () => {
    const onSummary = vi.fn();
    const { result } = renderHook(() => useFleetSummaryStream("cluster-a", onSummary));
    const source = FakeEventSource.instances[0];

    act(() => source.emitSummary(frame()));
    expect(result.current.live).toBe(true);

    act(() => source.fail());
    expect(result.current.live).toBe(false);
    expect(source.closed).toBe(false);
  });

  it("marks the channel unavailable when complete frames stop arriving", async () => {
    const { result } = renderHook(() => useFleetSummaryStream("cluster-a", vi.fn()));

    act(() => FakeEventSource.instances[0].emitSummary(frame()));
    expect(result.current.live).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(13_000);
    });
    expect(result.current.live).toBe(false);
  });

  it("closes and resets liveness when the authorization scope changes", () => {
    const { result, rerender } = renderHook(
      ({ scopeKey }) => useFleetSummaryStream(scopeKey, vi.fn()),
      { initialProps: { scopeKey: "cluster-a" } },
    );

    act(() => FakeEventSource.instances[0].emitSummary(frame()));
    expect(result.current.live).toBe(true);

    rerender({ scopeKey: "cluster-b" });
    expect(FakeEventSource.instances[0].closed).toBe(true);
    expect(result.current.live).toBe(false);

    act(() => FakeEventSource.instances[1].emitSummary(frame()));
    expect(result.current.live).toBe(true);
  });
});
