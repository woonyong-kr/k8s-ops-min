import { parseKubernetesLabelSelector } from "../shared/data/kubernetesLabel";
import { withQuery } from "./url";
import type { ResourceFilterFacetAxis } from "./resource-filter-schemas";

const DEFAULT_PAGE_LIMIT = 50;
const MIN_PAGE_LIMIT = 1;
const MAX_PAGE_LIMIT = 200;
const MAX_AXIS_VALUES = 100;
const MAX_LABEL_SELECTORS = 24;
const MAX_AXIS_TOKEN_LENGTH = 253;
const MAX_NAMESPACE_REFERENCE_LENGTH = 507;
const MAX_LABEL_SELECTOR_LENGTH = 1_024;
const MAX_QUERY_LENGTH = 200;
const MAX_CURSOR_LENGTH = 8_192;

export interface ResourceFilterQuery {
  clusters?: readonly string[];
  namespaces?: readonly string[];
  applications?: readonly string[];
  resourceTypes?: readonly string[];
  health?: readonly string[];
  labels?: readonly string[];
  query?: string;
  includeDeleted?: boolean;
  cursor?: string;
  limit?: number;
}

export function canonicalFacetSelections(
  axis: ResourceFilterFacetAxis,
  values: readonly string[] | undefined,
): readonly string[] {
  return canonicalValues(
    "selected",
    values,
    MAX_AXIS_VALUES,
    axis === "namespaces" ? MAX_NAMESPACE_REFERENCE_LENGTH : MAX_AXIS_TOKEN_LENGTH,
  );
}

export function canonicalLabelSelections(
  values: readonly string[] | undefined,
): readonly string[] {
  return canonicalValues(
    "labels",
    values,
    MAX_LABEL_SELECTORS,
    MAX_LABEL_SELECTOR_LENGTH,
    (value) => parseKubernetesLabelSelector(value) !== null,
  );
}

export function canonicalResourceTypeSelections(
  values: readonly string[] | undefined,
): readonly string[] {
  return canonicalAxisValues("resourceTypes", values);
}

export function resourceFilterEntries(
  query: ResourceFilterQuery,
  selectedLabels = canonicalLabelSelections(query.labels),
): Parameters<typeof withQuery>[1] {
  return [
    ["clusters", joinValues(canonicalAxisValues("clusters", query.clusters))],
    ["namespaces", joinValues(canonicalValues(
      "namespaces",
      query.namespaces,
      MAX_AXIS_VALUES,
      MAX_NAMESPACE_REFERENCE_LENGTH,
    ))],
    ["applications", joinValues(canonicalAxisValues("applications", query.applications))],
    ["resources.types", joinValues(canonicalAxisValues("resourceTypes", query.resourceTypes))],
    ["resources.health", joinValues(canonicalAxisValues("health", query.health))],
    ["labels", joinValues(selectedLabels)],
    ["resources.q", boundedQuery("query", query.query)],
    ["resources.includeDeleted", query.includeDeleted ?? false],
  ];
}

export function boundedQuery(name: string, value: string | undefined): string | undefined {
  if (value === undefined) return undefined;
  const normalized = value.trim();
  if (normalized === "") return undefined;
  if (normalized.length > MAX_QUERY_LENGTH) {
    throw new RangeError(`${name} must not exceed ${MAX_QUERY_LENGTH} characters`);
  }
  return normalized;
}

export function opaqueCursor(value: string | undefined): string | undefined {
  if (value === undefined) return undefined;
  if (!isStableValue(value)) throw new TypeError("cursor must be a stable opaque value");
  if (value.length > MAX_CURSOR_LENGTH) {
    throw new RangeError(`cursor must not exceed ${MAX_CURSOR_LENGTH} characters`);
  }
  return value;
}

export function pageLimit(value: number | undefined): number {
  const limit = value ?? DEFAULT_PAGE_LIMIT;
  if (!Number.isInteger(limit) || limit < MIN_PAGE_LIMIT || limit > MAX_PAGE_LIMIT) {
    throw new RangeError("limit must be an integer from 1 to 200");
  }
  return limit;
}

function canonicalAxisValues(
  name: string,
  values: readonly string[] | undefined,
): readonly string[] {
  return canonicalValues(name, values, MAX_AXIS_VALUES, MAX_AXIS_TOKEN_LENGTH);
}

function canonicalValues(
  name: string,
  values: readonly string[] | undefined,
  maxItems: number,
  maxTokenLength: number,
  validator?: (value: string) => boolean,
): readonly string[] {
  if (values === undefined || values.length === 0) return [];
  if (values.length > maxItems) {
    throw new RangeError(`${name} accepts at most ${maxItems} values`);
  }
  for (const value of values) {
    if (!isStableValue(value)) {
      throw new TypeError(`${name} values must be stable comma-free strings`);
    }
    if (value.length > maxTokenLength) {
      throw new RangeError(`${name} contains a value that exceeds its contract limit`);
    }
    if (validator !== undefined && !validator(value)) {
      throw new TypeError(`${name} contains a value outside its canonical grammar`);
    }
  }
  return [...new Set(values)].sort(compareUnicodeCodePoints);
}

function joinValues(values: readonly string[]): string | undefined {
  return values.length === 0 ? undefined : values.join(",");
}

function isStableValue(value: string): boolean {
  return value.length > 0 && value === value.trim() && !value.includes(",") &&
    !Array.from(value).some((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return codePoint <= 0x1f || codePoint === 0x7f;
    });
}

function compareUnicodeCodePoints(left: string, right: string): number {
  const leftPoints = Array.from(left, (character) => character.codePointAt(0) ?? 0);
  const rightPoints = Array.from(right, (character) => character.codePointAt(0) ?? 0);
  const length = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < length; index += 1) {
    const difference = leftPoints[index] - rightPoints[index];
    if (difference !== 0) return difference;
  }
  return leftPoints.length - rightPoints.length;
}
