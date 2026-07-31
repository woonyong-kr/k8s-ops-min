import { afterEach, describe, expect, it, vi } from "vitest";

import type { TimelineEndpointCapabilityDescriptor } from "../api/timeline-schemas";

const getTimelineCapabilities = vi.fn();

vi.mock("../api/timeline", () => ({
  getTimelineCapabilities: (...args: unknown[]) => getTimelineCapabilities(...args),
}));

import {
  loadSharedTimelineCapabilities,
  resetSharedTimelineCapabilitiesForTests,
} from "./timelineCapabilitiesFeed";

const capabilities = {
  selected_source_mode: "retained",
} as TimelineEndpointCapabilityDescriptor;

afterEach(() => {
  resetSharedTimelineCapabilitiesForTests();
  getTimelineCapabilities.mockReset();
});

describe("timeline capabilities request coalescing", () => {
  it("shares simultaneous reads and releases the response after settlement", async () => {
    let resolveRequest!: (value: TimelineEndpointCapabilityDescriptor) => void;
    getTimelineCapabilities.mockImplementation(() => new Promise((resolve) => {
      resolveRequest = resolve;
    }));

    const first = loadSharedTimelineCapabilities();
    const second = loadSharedTimelineCapabilities();
    expect(first).toBe(second);
    expect(getTimelineCapabilities).toHaveBeenCalledTimes(1);

    resolveRequest(capabilities);
    await expect(first).resolves.toBe(capabilities);
    await Promise.resolve();

    getTimelineCapabilities.mockResolvedValue(capabilities);
    await loadSharedTimelineCapabilities();
    expect(getTimelineCapabilities).toHaveBeenCalledTimes(2);
  });
});
