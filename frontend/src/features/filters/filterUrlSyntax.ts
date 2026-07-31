import type {
  KubernetesLabelFilter,
  NamespaceFilterRef,
} from "./filterContract";
import { parseKubernetesLabelSelector } from "../../shared/data/kubernetesLabel";

interface StrictQueryEntry {
  key: string;
  rawValue: string;
}

export interface StrictQuery {
  entries: readonly StrictQueryEntry[];
}

export function parseStrictQuery(search: string): StrictQuery {
  const query = search.startsWith("?") ? search.slice(1) : search;
  const entries: StrictQueryEntry[] = [];
  for (const component of query.split("&")) {
    if (component.length === 0) continue;
    const equals = component.indexOf("=");
    const rawKey = equals < 0 ? component : component.slice(0, equals);
    const key = strictDecodeQueryComponent(rawKey);
    if (key === null) continue;
    entries.push({
      key,
      rawValue: equals < 0 ? "" : component.slice(equals + 1),
    });
  }
  return { entries };
}

export function hasQueryKey(params: StrictQuery, key: string): boolean {
  return params.entries.some((entry) => entry.key === key);
}

export function readQueryValue(params: StrictQuery, key: string): string | null {
  const entry = params.entries.find((candidate) => candidate.key === key);
  return entry ? strictDecodeQueryComponent(entry.rawValue) : null;
}

export function readMultiValues(
  params: StrictQuery,
  key: string,
  invalid: string[],
): readonly string[] {
  const values: string[] = [];
  for (const entry of params.entries) {
    if (entry.key !== key) continue;
    for (const rawValue of entry.rawValue.split(",")) {
      const value = strictDecodeQueryComponent(rawValue);
      if (value !== null) values.push(value);
      else if (rawValue.length > 0) invalid.push(rawValue);
    }
  }
  return values;
}

export function readStableText(params: StrictQuery, key: string): string | null {
  const value = readQueryValue(params, key);
  return value !== null && isStableFilterValue(value) ? value : null;
}

export function normalizeStableList(values: readonly string[]): readonly string[] {
  return [...new Set(values.filter(isStableFilterValue))].sort(compareUnicodeCodePoints);
}

export function normalizeNamespaceRefs(
  values: readonly NamespaceFilterRef[],
): readonly NamespaceFilterRef[] {
  const unique = new Map<string, NamespaceFilterRef>();
  for (const value of values) {
    if (
      !isStableFilterValue(value.clusterId) ||
      !isKubernetesNamespace(value.namespace)
    ) continue;
    unique.set(namespaceSelector(value), value);
  }
  return [...unique.entries()]
    .sort(([left], [right]) => compareUnicodeCodePoints(left, right))
    .map(([, value]) => value);
}

export function normalizeLabels(
  values: readonly KubernetesLabelFilter[],
): readonly KubernetesLabelFilter[] {
  const unique = new Map<string, KubernetesLabelFilter>();
  for (const value of values) {
    const parsed = parseLabelSelector(labelSelector(value));
    if (parsed) unique.set(labelSelector(parsed), parsed);
  }
  return [...unique.entries()]
    .sort(([left], [right]) => compareUnicodeCodePoints(left, right))
    .map(([, value]) => value);
}

export function parseLabelSelector(selector: string): KubernetesLabelFilter | null {
  return parseKubernetesLabelSelector(selector);
}

export function isKubernetesNamespace(value: string): boolean {
  return value.length > 0 && value.length <= 63 &&
    /^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$/.test(value);
}

export function isStableFilterValue(value: string): boolean {
  return value.length > 0 && value === value.trim() && !value.includes(",") &&
    !containsControlCharacter(value);
}

export function namespaceSelector(value: NamespaceFilterRef): string {
  return `${value.clusterId}/${value.namespace}`;
}

export function labelSelector(value: KubernetesLabelFilter): string {
  return `${value.key}=${value.value}`;
}

export function appendList(
  pairs: string[],
  key: string,
  values: readonly string[],
) {
  if (values.length === 0) return;
  pairs.push(`${encodeURIComponent(key)}=${values.map(encodeURIComponent).join(",")}`);
}

export function appendText(pairs: string[], key: string, value: string) {
  if (value.length === 0) return;
  pairs.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`);
}

export function appendNullableStableText(
  pairs: string[],
  key: string,
  value: string | null,
) {
  if (value === null || !isStableFilterValue(value)) return;
  appendText(pairs, key, value);
}

export function appendBoolean(pairs: string[], key: string, value: boolean) {
  if (value) appendText(pairs, key, "true");
}

export function normalizeSearch(search: string): string {
  if (search.length === 0 || search === "?") return "";
  return search.startsWith("?") ? search : `?${search}`;
}

function strictDecodeQueryComponent(value: string): string | null {
  try {
    return decodeURIComponent(value.replace(/\+/g, " "));
  } catch {
    return null;
  }
}

function containsControlCharacter(value: string): boolean {
  return Array.from(value).some((character) => {
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
