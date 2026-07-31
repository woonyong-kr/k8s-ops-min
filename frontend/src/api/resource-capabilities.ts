import { apiRequest, type ApiPath } from "./client";
import {
  resourceCapabilitiesSchema,
  type ResourceCapabilitiesEndpoint,
} from "./resource-capabilities-schemas";
import { withQuery } from "./url";

export const RESOURCE_CAPABILITIES_PATH: ApiPath = "/api/capabilities";

export function getResourceCapabilities(
  resource: string,
  signal?: AbortSignal,
): Promise<ResourceCapabilitiesEndpoint> {
  const canonicalResource = resource.trim();
  if (canonicalResource.length === 0) {
    throw new TypeError("resource capability inventory key must not be empty");
  }
  return apiRequest(
    withQuery(RESOURCE_CAPABILITIES_PATH, [["resource", canonicalResource]]),
    resourceCapabilitiesSchema,
    { signal },
  );
}
