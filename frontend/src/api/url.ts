import type { ApiPath } from "./client";

type QueryValue = string | number | boolean | null | undefined;
type QueryEntry = readonly [name: string, value: QueryValue];

export function encodePathSegment(value: string): string {
  return encodeURIComponent(value);
}

export function withQuery(path: ApiPath, entries: readonly QueryEntry[]): ApiPath {
  const query = new URLSearchParams();

  for (const [name, value] of entries) {
    if (value === undefined || value === null) continue;
    query.set(name, String(value));
  }

  const serialized = query.toString();
  return serialized === "" ? path : `${path}?${serialized}`;
}

export function optionalQueryString(value: string | null | undefined): string | undefined {
  return value === undefined || value === null || value.trim() === "" ? undefined : value;
}
