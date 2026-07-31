import { apiRequest } from "./client";
import { fleetSummarySchema, type FleetSummary } from "./schemas";

export const FLEET_SUMMARY_EVENTS_PATH = "/api/fleet/events";

export function getFleetSummary(signal?: AbortSignal): Promise<FleetSummary> {
  return apiRequest("/api/fleet/summary", fleetSummarySchema, { signal });
}
