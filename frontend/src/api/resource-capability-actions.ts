import { apiRequest, type ApiPath } from "./client";
import {
  resourceActionAcceptedSchema,
  type ResourceActionAccepted,
} from "./resource-capability-actions-schemas";

/** Submit one server-discovered resource command after the UI confirmation. */
export function executeResourceCapability(
  path: string,
  values: Readonly<Record<string, unknown>>,
  signal?: AbortSignal,
): Promise<ResourceActionAccepted> {
  return apiRequest(toApiPath(path), resourceActionAcceptedSchema, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      ...values,
      // The server derives immediate execution from this acknowledgement. Client
      // mode flags are not an execution authority and are never sent.
      confirmation: true,
    }),
    signal,
  });
}

function toApiPath(path: string): ApiPath {
  if (!/^\/(?!\/)[^?\s]+$/u.test(path)) {
    throw new TypeError("resource capability path must be an absolute API path");
  }
  return `/api${path}` as ApiPath;
}
