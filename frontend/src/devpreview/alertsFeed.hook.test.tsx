// @vitest-environment jsdom

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const listAlertEvents = vi.fn();
const subscribeAlertEvents = vi.fn();

vi.mock("../api/alert-events", () => ({
  listAlertEvents: (...args: unknown[]) => listAlertEvents(...args),
  subscribeAlertEvents: (...args: unknown[]) => subscribeAlertEvents(...args),
}));

vi.mock("../api/alert-rules", () => ({
  listAlertRules: vi.fn(),
}));

vi.mock("../api/alert-channels", () => ({
  listAlertChannels: vi.fn(),
}));

import { useAlertEvents } from "./alertsFeed";

afterEach(() => {
  listAlertEvents.mockReset();
  subscribeAlertEvents.mockReset();
});

describe("alert event transport state", () => {
  it("reports the SSE channel only after the stream confirms connection", async () => {
    listAlertEvents.mockResolvedValue([]);
    subscribeAlertEvents.mockImplementation(async function* (
      subscription: {
        signal?: AbortSignal;
        onLifecycle?: (state: string) => void;
      },
    ) {
      yield* [] as never[];
      subscription.onLifecycle?.("connecting");
      subscription.onLifecycle?.("connected");
      await new Promise<void>((resolve) => {
        subscription.signal?.addEventListener("abort", () => resolve(), { once: true });
      });
    });

    const { result, unmount } = renderHook(() => useAlertEvents());

    await waitFor(() => expect(result.current.transport).toBe("sse"));
    expect(result.current.status).toBe("ready");
    expect(result.current.items).toEqual([]);
    expect(listAlertEvents).toHaveBeenCalledTimes(1);
    expect(subscribeAlertEvents).toHaveBeenCalledTimes(1);
    unmount();
  });

  it("retains the HTTP snapshot and marks it stale when reconnect refresh fails", async () => {
    listAlertEvents
      .mockResolvedValueOnce([{
        event_id: "alert-a",
        rule_id: "rule-a",
        rule_name: null,
        source: "opsia",
        severity: "warning",
        status: "firing",
        subject: {
          cluster: "cluster-a",
          namespace: "sandbox",
          kind: "Pod",
          name: "pod-a",
        },
        fired_at: "2026-07-24T00:00:00Z",
        resolved_at: null,
        observed_value: 91,
        threshold: 90,
        evidence: [{
          type: "metric",
          metric: "cpu_usage",
          observed_at: "2026-07-24T00:00:00Z",
          subject: null,
          value: 91,
          summary: "CPU usage crossed the configured threshold",
          link: null,
        }],
        incident_id: null,
        acknowledged_at: null,
        acknowledged_by: null,
        promoted_at: null,
        promoted_by: null,
      }])
      .mockRejectedValueOnce(new Error("temporary refresh failure"));
    subscribeAlertEvents.mockImplementation(async function* (
      subscription: {
        onLifecycle?: (state: string) => void;
      },
    ) {
      yield* [] as never[];
      subscription.onLifecycle?.("connected");
      subscription.onLifecycle?.("failed");
      throw new Error("stream interrupted");
    });

    const { result, unmount } = renderHook(() => useAlertEvents());

    await waitFor(() => expect(result.current.transport).toBe("stale"));
    expect(result.current.status).toBe("ready");
    expect(result.current.items.map((item) => item.eventId)).toEqual(["alert-a"]);
    expect(result.current.items[0]).toMatchObject({
      ruleId: "rule-a",
      observedValue: 91,
      threshold: 90,
      evidence: [expect.objectContaining({ metric: "cpu_usage", value: 91 })],
    });
    unmount();
  });
});
