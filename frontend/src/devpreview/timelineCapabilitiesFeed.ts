import { getTimelineCapabilities } from "../api/timeline";
import type { TimelineEndpointCapabilityDescriptor } from "../api/timeline-schemas";

let inFlightTimelineCapabilities: Promise<TimelineEndpointCapabilityDescriptor> | null = null;

/**
 * Home activity and the Timeline control panel mount together with the same
 * session scope. Coalesce their simultaneous capability read, but never retain
 * a settled authorization-bearing response across a session switch.
 */
export function loadSharedTimelineCapabilities(): Promise<TimelineEndpointCapabilityDescriptor> {
  if (inFlightTimelineCapabilities !== null) return inFlightTimelineCapabilities;
  const request = getTimelineCapabilities();
  inFlightTimelineCapabilities = request;
  const release = () => {
    if (inFlightTimelineCapabilities === request) {
      inFlightTimelineCapabilities = null;
    }
  };
  void request.then(release, release);
  return request;
}

/** @internal Test isolation for the in-flight request coalescer. */
export function resetSharedTimelineCapabilitiesForTests(): void {
  inFlightTimelineCapabilities = null;
}
