import type { InventoryResource, InventoryResourceDetail } from "../api/inventory-schemas";
import { KUBERNETES_KIND } from "./kubernetesKinds";
import { toResourceEvent, type ResourceEventView } from "./resourceEventsFeed";

export type ResourceConditionTone = "ok" | "warn" | "crit" | "info";

export interface ResourceConditionItem {
  id: string;
  sourceLabel: string | null;
  type: string;
  status: string | null;
  reason: string | null;
  message: string | null;
  lastTransitionAt: string | null;
  tone: ResourceConditionTone;
}

export interface ResourceConditionEventItem extends ResourceEventView {
  tone: ResourceConditionTone;
}

export interface ResourceConditionSnapshot {
  primary: ResourceConditionItem[];
  relatedPods: ResourceConditionItem[];
  events: ResourceConditionEventItem[];
  relatedPodCount: number;
}

export interface ResourceConditionSnapshotOptions {
  includeFallbackEvidence?: boolean;
}

const CONDITION_FIELD = {
  conditions: "conditions",
  lastTransitionTime: "lastTransitionTime",
  lastTransitionTimeSnake: "last_transition_time",
  message: "message",
  reason: "reason",
  status: "status",
  type: "type",
  value: "value",
} as const;
const CONDITION_EVENT_WORDS = [
  "condition",
  "containersnotready",
  "failed",
  "not ready",
  "probe",
  "readiness",
  "unhealthy",
] as const;
const TRUE_IS_PROBLEM_CONDITION_TYPES = new Set([
  "disruptiontarget",
  "failed",
  "failure",
  "replicafailure",
]);

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}

function conditionRows(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) return value.flatMap((item) => {
    const row = record(item);
    return row === null ? [] : [row];
  });
  const wrapped = record(value);
  if (wrapped !== null && Array.isArray(wrapped[CONDITION_FIELD.value])) {
    return conditionRows(wrapped[CONDITION_FIELD.value]);
  }
  return [];
}

function conditionTone(type: string, status: string | null): ResourceConditionTone {
  const normalized = status?.trim().toLowerCase();
  if (normalized === "unknown") return "warn";
  const conditionType = type.trim().toLowerCase();
  if (TRUE_IS_PROBLEM_CONDITION_TYPES.has(conditionType)) {
    if (normalized === "true") return conditionType === "disruptiontarget" ? "warn" : "crit";
    if (normalized === "false") return "ok";
  }
  if (normalized === "true") return "ok";
  if (normalized === "false") return "crit";
  return "info";
}

export function conditionItemsFromSummary(
  summary: Record<string, unknown>,
  sourceLabel: string | null = null,
): ResourceConditionItem[] {
  return conditionRows(summary[CONDITION_FIELD.conditions]).map((condition, index) => {
    const type = text(condition[CONDITION_FIELD.type]) ?? "Condition";
    const status = text(condition[CONDITION_FIELD.status]);
    return {
      id: [
        sourceLabel ?? "resource",
        type,
        status ?? "",
        text(condition[CONDITION_FIELD.reason]) ?? "",
        index,
      ].join("\u0000"),
      sourceLabel,
      type,
      status,
      reason: text(condition[CONDITION_FIELD.reason]),
      message: text(condition[CONDITION_FIELD.message]),
      lastTransitionAt: text(condition[CONDITION_FIELD.lastTransitionTime])
        ?? text(condition[CONDITION_FIELD.lastTransitionTimeSnake]),
      tone: conditionTone(type, status),
    };
  });
}

function relatedPodResources(detail: InventoryResourceDetail): InventoryResource[] {
  return Object.values(detail.related)
    .flat()
    .filter((resource) => resource.kind === KUBERNETES_KIND.pod);
}

function conditionEventTone(event: ResourceEventView): ResourceConditionTone {
  const type = event.type?.trim().toLowerCase();
  if (type === "warning") return "warn";
  return "info";
}

function isConditionEvent(event: ResourceEventView): boolean {
  const haystack = [
    event.type,
    event.reason,
    event.message,
  ].filter(Boolean).join(" ").toLowerCase();
  return CONDITION_EVENT_WORDS.some((word) => haystack.includes(word));
}

export function conditionEventsFromResources(
  events: readonly InventoryResource[],
): ResourceConditionEventItem[] {
  return events
    .map(toResourceEvent)
    .filter(isConditionEvent)
    .map((event) => ({
      ...event,
      tone: conditionEventTone(event),
    }));
}

export function resourceConditionSnapshot(
  detail: InventoryResourceDetail,
  options: ResourceConditionSnapshotOptions = {},
): ResourceConditionSnapshot {
  const includeFallbackEvidence = options.includeFallbackEvidence === true;
  const relatedPods = includeFallbackEvidence ? relatedPodResources(detail) : [];
  return {
    primary: conditionItemsFromSummary(detail.resource.summary),
    relatedPods: relatedPods.flatMap((pod) =>
      conditionItemsFromSummary(pod.summary, pod.name)
    ),
    events: includeFallbackEvidence ? conditionEventsFromResources(detail.events) : [],
    relatedPodCount: relatedPods.length,
  };
}
