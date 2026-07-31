import { apiRequest, type ApiPath } from "./client";
import {
  configReferenceListSchema,
  type ConfigReferenceList,
} from "./config-references-schemas";
import { encodePathSegment, optionalQueryString, withQuery } from "./url";

export interface ConfigReferenceQuery {
  namespace?: string | null;
}

export function listConfigReferences(
  clusterId: string,
  query: ConfigReferenceQuery = {},
  signal?: AbortSignal,
): Promise<ConfigReferenceList> {
  const normalizedClusterId = clusterId.trim();
  if (normalizedClusterId === "") {
    throw new TypeError("clusterId must not be empty");
  }

  const path = withQuery(
    `/api/clusters/${encodePathSegment(normalizedClusterId)}/config-references` as ApiPath,
    [
      ["namespace", optionalQueryString(query.namespace)],
    ],
  );
  return apiRequest(path, configReferenceListSchema, { signal });
}
