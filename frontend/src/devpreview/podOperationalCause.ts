import type { PodResourceSummaryView } from "./podResourceDetailFeed";
import type { ResourceConditionItem } from "./resourceConditions";
import type { ResourceEventView } from "./resourceEventsFeed";

export type PodOperationalCauseSource = "condition" | "warning-event";

/**
 * A presentation-ready projection of evidence already observed for a Pod.
 *
 * `condition` is the primary health evidence when Kubernetes reports an active
 * degraded condition. `supportingEvent` is the newest observed Warning event,
 * kept separate so consumers do not have to imply that the event caused the
 * condition.
 */
export interface PodOperationalCause {
  source: PodOperationalCauseSource;
  reason: string;
  message: string | null;
  condition: ResourceConditionItem | null;
  supportingEvent: ResourceEventView | null;
}

type PodOperationalSummary = Pick<PodResourceSummaryView, "conditions">;

function text(value: string | null): string | null {
  const normalized = value?.trim() ?? "";
  return normalized === "" ? null : normalized;
}

function normalized(value: string | null): string {
  return text(value)?.toLowerCase() ?? "";
}

function isActiveCondition(condition: ResourceConditionItem): boolean {
  return condition.tone === "crit" || condition.tone === "warn";
}

function conditionPriority(condition: ResourceConditionItem): number {
  const type = normalized(condition.type);
  const status = normalized(condition.status);

  // These are the Pod's direct availability conditions. Prefer an explicitly
  // observed false value over broader symptoms without inferring why it is false.
  if (status === "false" && type === "ready") return 0;
  if (status === "false" && type === "containersready") return 1;
  if (condition.tone === "crit") return 10;
  if (type === "ready") return 20;
  if (type === "containersready") return 21;
  return 30;
}

function conditionObservedAt(condition: ResourceConditionItem): number {
  const value = text(condition.lastTransitionAt);
  if (value === null) return Number.NEGATIVE_INFINITY;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : Number.NEGATIVE_INFINITY;
}

function strongestActiveCondition(
  conditions: readonly ResourceConditionItem[],
): ResourceConditionItem | null {
  let selected: ResourceConditionItem | null = null;
  let selectedPriority = Number.POSITIVE_INFINITY;
  let selectedObservedAt = Number.NEGATIVE_INFINITY;

  for (const condition of conditions) {
    if (!isActiveCondition(condition)) continue;
    const priority = conditionPriority(condition);
    const observedAt = conditionObservedAt(condition);
    if (
      selected === null
      || priority < selectedPriority
      || (priority === selectedPriority && observedAt > selectedObservedAt)
    ) {
      selected = condition;
      selectedPriority = priority;
      selectedObservedAt = observedAt;
    }
  }

  return selected;
}

function eventObservedAt(event: ResourceEventView): number {
  const value = text(event.lastAt);
  if (value === null) return Number.NEGATIVE_INFINITY;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : Number.NEGATIVE_INFINITY;
}

function latestWarningEvent(
  events: readonly ResourceEventView[],
): ResourceEventView | null {
  let selected: ResourceEventView | null = null;
  let selectedObservedAt = Number.NEGATIVE_INFINITY;

  for (const event of events) {
    if (normalized(event.type) !== "warning") continue;
    const observedAt = eventObservedAt(event);
    if (selected === null || observedAt > selectedObservedAt) {
      selected = event;
      selectedObservedAt = observedAt;
    }
  }

  return selected;
}

/**
 * Selects only observed Kubernetes condition/event evidence.
 *
 * It deliberately does not inspect Pod names, namespaces, application labels,
 * or scenario-specific strings, and returns `null` when no degraded condition
 * or Warning event has been observed.
 */
export function projectPodOperationalCause(
  summary: PodOperationalSummary,
  events: readonly ResourceEventView[],
): PodOperationalCause | null {
  const condition = strongestActiveCondition(summary.conditions);
  const warning = latestWarningEvent(events);

  if (condition !== null) {
    return {
      source: "condition",
      reason: text(condition.reason) ?? text(condition.type) ?? "Condition",
      message: text(condition.message),
      condition,
      supportingEvent: warning,
    };
  }

  if (warning === null) return null;
  const warningReason = text(warning.reason);
  const warningMessage = text(warning.message);
  if (warningReason === null && warningMessage === null) return null;

  return {
    source: "warning-event",
    reason: warningReason ?? text(warning.type) ?? "Warning",
    message: warningMessage,
    condition: null,
    supportingEvent: warning,
  };
}
