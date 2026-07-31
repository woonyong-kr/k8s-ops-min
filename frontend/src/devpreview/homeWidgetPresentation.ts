import type { AlertEventView } from "./alertsFeed";
import type { ApplicationView } from "./deployFeed";
import { alertEventPresentation } from "./alertEventPresentation";

export interface HomeRankRow {
  id: string;
  tone: "ok" | "warn" | "crit";
  title: string;
  sub: string;
  right: string;
}

export interface HomeDonutItem {
  label: string;
  value: number;
  color: string;
}

const ALERT_SEVERITY_LABELS: Record<AlertEventView["severity"], string> = {
  critical: "심각",
  high: "높음",
  medium: "중간",
  warning: "주의",
  low: "낮음",
  info: "정보",
};

export function activeAlertRows(
  items: readonly AlertEventView[],
  limit = 5,
): HomeRankRow[] {
  return items
    .filter((event) => event.status === "firing")
    .slice()
    .sort((left, right) => Date.parse(right.firedAt) - Date.parse(left.firedAt))
    .slice(0, limit)
    .map((event) => {
      const presentation = alertEventPresentation(event);
      return {
        id: event.eventId,
        tone: presentation.tone === "crit"
          ? "crit"
          : presentation.tone === "warn"
            ? "warn"
            : "ok",
        title: `${event.kind}/${event.name}`,
        sub: [event.ruleName, event.namespace, event.cluster].filter(Boolean).join(" · "),
        right: ALERT_SEVERITY_LABELS[event.severity],
      };
    });
}

export function applicationHealthItems(
  items: readonly ApplicationView[],
  colors: { ok: string; warn: string; unknown: string },
): HomeDonutItem[] {
  const counts = { ok: 0, warn: 0, unknown: 0 };
  for (const item of items) {
    const status = item.healthStatus?.trim().toLowerCase() ?? "";
    if (/^(healthy|ok|ready|synced)$/u.test(status)) {
      counts.ok += 1;
    } else if (/^(critical|degraded|error|failed|unhealthy|warning)$/u.test(status)) {
      counts.warn += 1;
    } else {
      counts.unknown += 1;
    }
  }
  return [
    { label: "정상", value: counts.ok, color: colors.ok },
    { label: "주의", value: counts.warn, color: colors.warn },
    { label: "관측 안 됨", value: counts.unknown, color: colors.unknown },
  ];
}

export function applicationAttentionRows(
  items: readonly ApplicationView[],
  limit = 5,
): HomeRankRow[] {
  const classified = items.map((item) => {
    const health = item.healthStatus?.trim().toLowerCase() ?? "";
    const delivery = item.deliveryStatus?.trim().toLowerCase() ?? "";
    const critical = /^(critical|degraded|error|failed|unhealthy)$/u.test(health)
      || /^(failed|error|drifted)$/u.test(delivery);
    const healthy = /^(healthy|ok|ready|synced)$/u.test(health);
    const tone: HomeRankRow["tone"] = critical ? "crit" : healthy ? "ok" : "warn";
    const priority = critical ? 0 : healthy ? 2 : 1;
    return {
      priority,
      row: {
        id: item.id,
        tone,
        title: item.name,
        sub: [
          item.repositoryRef,
          item.environments.join(", "),
          item.deliveryStatus,
        ].filter(Boolean).join(" · "),
        right: critical ? "주의" : healthy ? "정상" : "미관측",
      },
    };
  });
  return classified
    .sort((left, right) =>
      left.priority - right.priority || left.row.title.localeCompare(right.row.title))
    .slice(0, limit)
    .map(({ row }) => row);
}
