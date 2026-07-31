import type { FleetTotals } from "../api/schemas";

export interface FleetHeaderMetric {
  key: keyof FleetTotals;
  label: string;
  value: number;
}

export interface FleetHeaderGroups {
  health: FleetHeaderMetric[];
  operations: FleetHeaderMetric[];
}

/**
 * Exhaustive presentation projection for the fleet totals contract.
 *
 * Zero health exceptions are omitted to keep the compact header readable.
 * Operational zeros remain visible because “none pending/running” is useful
 * state. A null global dead-letter count means the caller is not authorized to
 * observe it, so it is omitted rather than rendered as a synthetic zero.
 */
export function fleetHeaderGroups(totals: FleetTotals): FleetHeaderGroups {
  const health: FleetHeaderMetric[] = [
    { key: "clusters", label: "클러스터", value: totals.clusters },
    { key: "healthy", label: "정상", value: totals.healthy },
  ];
  const healthExceptions: FleetHeaderMetric[] = [
    { key: "warning", label: "주의", value: totals.warning },
    { key: "critical", label: "장애", value: totals.critical },
    { key: "stale", label: "관측 지연", value: totals.stale },
    { key: "unknown", label: "미관측", value: totals.unknown },
  ];
  health.push(...healthExceptions.filter((metric) => metric.value > 0));

  const operations: FleetHeaderMetric[] = [
    { key: "open_incidents", label: "이슈", value: totals.open_incidents },
    { key: "pending_approvals", label: "승인 대기", value: totals.pending_approvals },
    { key: "running_workflows", label: "실행 중", value: totals.running_workflows },
  ];
  if (totals.dead_letters !== null) {
    operations.push({
      key: "dead_letters",
      label: "처리 실패",
      value: totals.dead_letters,
    });
  }
  return { health, operations };
}
