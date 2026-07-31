import { useEffect, useState } from "react";

import { getRecoveryPlanByCorrelation } from "../api/recovery";
import { isApiError } from "../api/client";
import type { RecoveryPlan } from "../api/recovery-schemas";
import { getRemediationBundle } from "../api/rca-bundle";
import type { RemediationBundleResponse } from "../api/rca-bundle-schemas";
import { getAuditTimeline } from "../api/audit-timeline";
import type { AuditTimelineItem } from "../api/audit-timeline-schemas";
import { getIncidentRecentChanges } from "../api/recent-changes";
import type { RecentChangeItem } from "../api/recent-changes-schemas";
import { getEvidenceWindowPayload, listEvidence, listRcaReports } from "../api/evidence";
import type { EvidenceRecord, EvidenceWindowPayload, RcaReport } from "../api/evidence-schemas";
import type { RcaIssueList } from "../api/schemas";
import { loadRcaIssueRepresentativeItems, type RcaIssueAttemptSummary, type RcaIssueRepresentativeItem } from "./rcaIssuesFeed";
import { operationalMessageLabel } from "./statusLabel";

// UI-PHASE2-001: typed live adapters for the RCA Issue *detail* drawer. Unlike
// the reduced `rcaIssuesFeed` view (list/bell), this exposes the full observed
// RCA fields the Issue-queue contract already carries — root cause, confidence,
// supporting/missing evidence and the AI-authored summaries — plus the recovery
// candidate plan (`GET /api/rca/recovery-plans/by-correlation/{id}`). Nothing is
// fabricated: absent fields stay null/empty and a load failure is an honest
// `unavailable`, never a synthesised cause/evidence/recovery.

export type RcaDetailStatus = "loading" | "ready" | "stale" | "unavailable";
export const RCA_DETAIL_REFRESH_MS = 4_000;

export interface RcaIssueAttemptDetail {
  correlationId: string;
  incidentId: string | null;
  currentSubject: string;
  clusterId: string | null;
  namespace: string | null;
  resourceName: string | null;
  resourceKind: string | null;
  rawSymptom: string | null;
  symptom: string | null;
  status: string;
  severity: "critical" | "warning" | null;
  rootCause: string | null;
  confidence: number | null;
  supportingEvidence: string[];
  missingEvidence: string[];
  situationSummary: string | null;
  recommendedActionSummary: string | null;
  evidenceSummary: string | null;
  evidenceBundleSummary: string | null;
  actionRoute: string | null;
  prUrl: string | null;
  errorReason: string | null;
  recoveryReasonCode?: string | null;
  updatedAt: string | null;
}

export interface RcaIssueDetailView extends RcaIssueAttemptDetail {
  attemptCount: number;
  newerAttemptCount: number;
  latestAttempt: RcaIssueAttemptDetail | null;
  recentAttempts: RcaIssueAttemptSummary[];
}

export interface RcaIssueDetailsFeed {
  status: RcaDetailStatus;
  items: RcaIssueDetailView[];
}

type RcaIssueItem = RcaIssueList["items"][number];

export function toRcaIssueAttemptDetail(item: RcaIssueItem): RcaIssueAttemptDetail {
  return {
    correlationId: item.correlation_id,
    incidentId: item.incident_id,
    currentSubject: item.current_subject,
    clusterId: item.cluster_id,
    namespace: item.incident_namespace,
    resourceName: item.incident_resource_name,
    resourceKind: item.incident_resource_kind,
    rawSymptom: item.incident_symptom,
    symptom: item.incident_symptom === null ? null : operationalMessageLabel(item.incident_symptom),
    status: item.status,
    severity: item.issue_severity,
    rootCause: item.root_cause,
    confidence: item.confidence,
    supportingEvidence: [...item.supporting_evidence],
    missingEvidence: [...item.missing_evidence],
    situationSummary: item.situation_summary,
    recommendedActionSummary: item.recommended_action_summary,
    evidenceSummary: item.evidence_summary,
    evidenceBundleSummary: item.evidence_bundle_summary,
    actionRoute: item.action_route,
    prUrl: item.pr_url,
    errorReason: item.error_reason,
    recoveryReasonCode: item.recovery_reason_code,
    updatedAt: item.updated_at,
  };
}

export function toRcaIssueDetailView(item: RcaIssueItem | RcaIssueRepresentativeItem): RcaIssueDetailView {
  if (!isRcaIssueRepresentativeItem(item)) {
    return {
      ...toRcaIssueAttemptDetail(item),
      attemptCount: 1,
      newerAttemptCount: 0,
      latestAttempt: null,
      recentAttempts: [],
    };
  }
  const base = toRcaIssueAttemptDetail(item.item);
  const latestAttempt = item.latestItem.correlation_id !== item.item.correlation_id
    ? toRcaIssueAttemptDetail(item.latestItem)
    : null;
  return {
    ...base,
    attemptCount: item.attemptCount,
    newerAttemptCount: item.newerAttemptCount,
    latestAttempt,
    recentAttempts: item.recentAttempts,
  };
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

/**
 * Reads the full Issue-queue projection for the detail drawer. A load failure is
 * an honest `unavailable`; the queue already carries the observed RCA fields, so
 * no per-incident refetch is needed for cause/confidence/evidence.
 */
export function useRcaIssueDetails(
  clusterIds?: readonly string[],
  pollMs = 0,
  onItems?: (items: RcaIssueDetailView[]) => void,
  pinnedCorrelationIds: readonly string[] = [],
): RcaIssueDetailsFeed {
  const scopeKey = clusterIds === undefined
    ? null
    : [...new Set(clusterIds.filter((clusterId) => clusterId.trim() !== ""))].sort().join("\u0000");
  const pinnedKey = [...new Set(pinnedCorrelationIds.map((correlationId) => correlationId.trim()).filter(Boolean))].join("\u0000");
  const [snapshot, setSnapshot] = useState<{ scopeKey: string | null; feed: RcaIssueDetailsFeed }>({
    scopeKey,
    feed: { status: "loading", items: [] },
  });
  useEffect(() => {
    const controller = new AbortController();
    const scopedClusterIds = scopeKey === null ? undefined : scopeKey === "" ? [] : scopeKey.split("\u0000");
    let timer: number | undefined;
    const load = () => {
      const pinnedIds = pinnedKey === "" ? [] : pinnedKey.split("\u0000");
      void loadRcaIssueRepresentativeItems(scopedClusterIds, controller.signal, pinnedIds)
        .then((items) => {
          if (controller.signal.aborted) return;
          const views = items.map(toRcaIssueDetailView);
          setSnapshot({ scopeKey, feed: { status: "ready", items: views } });
          onItems?.(views);
        })
        .catch((cause: unknown) => {
          if (controller.signal.aborted || isAbortError(cause)) return;
          setSnapshot((previous) => (
            previous.scopeKey === scopeKey
            && (previous.feed.status === "ready" || previous.feed.status === "stale")
              ? {
                  scopeKey,
                  feed: { status: "stale", items: previous.feed.items },
                }
              : {
                  scopeKey,
                  feed: { status: "unavailable", items: [] },
                }
          ));
        })
        .finally(() => {
          if (!controller.signal.aborted && pollMs > 0) timer = window.setTimeout(load, pollMs);
        });
    };
    load();
    return () => {
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [scopeKey, pollMs, onItems, pinnedKey]);
  return snapshot.scopeKey === scopeKey
    ? snapshot.feed
    : { status: "loading", items: [] };
}

function isRcaIssueRepresentativeItem(item: RcaIssueItem | RcaIssueRepresentativeItem): item is RcaIssueRepresentativeItem {
  return "item" in item && "latestItem" in item;
}

export type RecoveryPlanStatus = "idle" | "loading" | "pending" | "ready" | "unavailable";

export interface RecoveryPlanFeed {
  status: RecoveryPlanStatus;
  plan: RecoveryPlan | null;
}

export interface RemediationBundleFeed {
  status: Exclude<RecoveryPlanStatus, "pending">;
  bundle: RemediationBundleResponse | null;
}

export type RecoveryAuditStatus = "idle" | "loading" | "ready" | "unavailable";

export interface RecoveryAuditFeed {
  status: RecoveryAuditStatus;
  items: AuditTimelineItem[];
}

export type IncidentRecentChangesStatus = "idle" | "loading" | "ready" | "unavailable";

export interface IncidentRecentChangesFeed {
  status: IncidentRecentChangesStatus;
  items: RecentChangeItem[];
}

export interface RcaReportFeed {
  status: RcaDetailStatus | "idle";
  report: RcaReport | null;
}

export interface EvidenceWindowFeed {
  status: RcaDetailStatus | "idle";
  evidence: EvidenceWindowPayload | null;
}

export type RcaEvidenceReference = RcaReport["supporting_evidence_refs"][number];

export interface EvidenceReferenceFeed {
  status: RcaDetailStatus | "idle";
  references: RcaEvidenceReference[];
}

export interface EvidenceObjectReference {
  value: string;
  correlationId: string;
  source: string;
  name: string;
}

/** Parse the durable RCA evidence URI without treating it as a browser URL. */
export function parseEvidenceObjectReference(value: string): EvidenceObjectReference | null {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "object:" || parsed.hostname !== "evidence") return null;
    const path = decodeURIComponent(parsed.pathname.replace(/^\/+/u, ""));
    const correlationId = path.endsWith(".json") ? path.slice(0, -5) : path;
    const fragment = decodeURIComponent(parsed.hash.replace(/^#/u, ""));
    const separator = fragment.indexOf(":");
    if (!correlationId || separator < 1 || separator === fragment.length - 1) return null;
    return {
      value,
      correlationId,
      source: fragment.slice(0, separator),
      name: fragment.slice(separator + 1),
    };
  } catch {
    return null;
  }
}

function normalizedEvidenceSource(source: string): string {
  const normalized = source.trim().toLowerCase();
  if (normalized === "k8s") return "kubernetes";
  if (normalized === "prometheus") return "metrics";
  if (normalized === "loki") return "logs";
  if (normalized === "tempo") return "traces";
  return normalized;
}

/**
 * Resolve one legacy pointer only against a source summary that identifies a
 * durable EvidenceWindow. A correlation may contain multiple records for the
 * same provider while a window is still assembling, so a source-only match can
 * accidentally select a newer, unkeyed partial record and make the old link
 * unopenable.
 */
export function resolveEvidenceObjectReference(
  pointer: EvidenceObjectReference,
  records: readonly EvidenceRecord[],
): RcaEvidenceReference | null {
  const source = normalizedEvidenceSource(pointer.source);
  const sourceSummary = records
    .filter((record) => record.correlation_id === pointer.correlationId)
    .flatMap((record) => record.sources)
    .find((candidate) => (
      normalizedEvidenceSource(candidate.source) === source
      && Boolean(candidate.evidence_key?.trim())
    ));
  if (!sourceSummary?.evidence_key) return null;
  return {
    source,
    name: pointer.name,
    check_id: null,
    summary: sourceSummary.summary || null,
    query: null,
    evidence_ref: pointer.value,
    schema_version: sourceSummary.schema_version,
    source_version: sourceSummary.source_version,
    collector: sourceSummary.collector,
    collector_version: sourceSummary.collector_version,
    query_version: sourceSummary.query_version,
    collected_at: sourceSummary.collected_at,
    evidence_key: sourceSummary.evidence_key,
    source_id: sourceSummary.source_id,
    agent_id: sourceSummary.agent_id,
    window_start: sourceSummary.window_start,
  };
}

/**
 * Resolve legacy object:// evidence strings through the safe Evidence summary
 * endpoint. This recovers the window key needed by the existing detail panel
 * without exposing or guessing an object-store URL.
 */
export function useEvidenceObjectReferences(values: readonly string[]): EvidenceReferenceFeed {
  const requestKey = [...new Set(values.filter((value) => parseEvidenceObjectReference(value)))]
    .sort()
    .join("\u0000");
  const [snapshot, setSnapshot] = useState<{
    requestKey: string;
    feed: EvidenceReferenceFeed;
  }>({
    requestKey: "",
    feed: { status: "idle", references: [] },
  });
  useEffect(() => {
    if (!requestKey) return;
    const controller = new AbortController();
    const pointers = requestKey
      .split("\u0000")
      .map(parseEvidenceObjectReference)
      .filter((pointer): pointer is EvidenceObjectReference => pointer !== null);
    const correlationIds = [...new Set(pointers.map((pointer) => pointer.correlationId))];
    void Promise.all(
      correlationIds.map(async (correlationId) => [
        correlationId,
        await listEvidence({ correlationId, limit: 50, signal: controller.signal }),
      ] as const),
    )
      .then((responses) => {
        if (controller.signal.aborted) return;
        const recordsByCorrelation = new Map(responses);
        const references = pointers.flatMap((pointer): RcaEvidenceReference[] => {
          const records = recordsByCorrelation.get(pointer.correlationId)?.items ?? [];
          const reference = resolveEvidenceObjectReference(pointer, records);
          return reference ? [reference] : [];
        });
        setSnapshot({ requestKey, feed: { status: "ready", references } });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setSnapshot({ requestKey, feed: { status: "unavailable", references: [] } });
      });
    return () => controller.abort();
  }, [requestKey]);
  if (!requestKey) return { status: "idle", references: [] };
  return snapshot.requestKey === requestKey
    ? snapshot.feed
    : { status: "loading", references: [] };
}

export function useEvidenceWindowPayload(
  evidenceKey: string | null,
  source: string | null,
  enabled: boolean,
): EvidenceWindowFeed {
  const requestKey = enabled && evidenceKey ? `${evidenceKey}\u0000${source ?? ""}` : null;
  const [snapshot, setSnapshot] = useState<{ requestKey: string | null; feed: EvidenceWindowFeed }>({
    requestKey: null,
    feed: { status: "idle", evidence: null },
  });
  useEffect(() => {
    if (!requestKey || !evidenceKey) return;
    const controller = new AbortController();
    void getEvidenceWindowPayload(evidenceKey, { source: source ?? undefined, signal: controller.signal })
      .then((evidence) => {
        if (controller.signal.aborted) return;
        setSnapshot({ requestKey, feed: { status: "ready", evidence } });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setSnapshot({ requestKey, feed: { status: "unavailable", evidence: null } });
      });
    return () => controller.abort();
  }, [requestKey, evidenceKey, source]);
  if (requestKey === null) return { status: "idle", evidence: null };
  return snapshot.requestKey === requestKey
    ? snapshot.feed
    : { status: "loading", evidence: null };
}

export function useLatestRcaReport(correlationId: string | null): RcaReportFeed {
  const [snapshot, setSnapshot] = useState<{ correlationId: string | null; feed: RcaReportFeed }>({
    correlationId: null,
    feed: { status: "idle", report: null },
  });
  useEffect(() => {
    if (!correlationId) return;
    const controller = new AbortController();
    void listRcaReports({ correlationId, limit: 1, signal: controller.signal })
      .then((response) => {
        if (controller.signal.aborted) return;
        setSnapshot({ correlationId, feed: { status: "ready", report: response.items[0] ?? null } });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setSnapshot({ correlationId, feed: { status: "unavailable", report: null } });
      });
    return () => controller.abort();
  }, [correlationId]);
  if (!correlationId) return { status: "idle", report: null };
  return snapshot.correlationId === correlationId
    ? snapshot.feed
    : { status: "loading", report: null };
}

export function useIncidentRecentChanges(incidentId: string | null): IncidentRecentChangesFeed {
  const [snapshot, setSnapshot] = useState<{ incidentId: string | null; feed: IncidentRecentChangesFeed }>({
    incidentId: null,
    feed: { status: "idle", items: [] },
  });
  useEffect(() => {
    if (!incidentId) return;
    const controller = new AbortController();
    void getIncidentRecentChanges(incidentId, { signal: controller.signal })
      .then((response) => {
        if (controller.signal.aborted) return;
        setSnapshot({ incidentId, feed: { status: "ready", items: response.items } });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setSnapshot({ incidentId, feed: { status: "unavailable", items: [] } });
      });
    return () => controller.abort();
  }, [incidentId]);
  if (!incidentId) return { status: "idle", items: [] };
  return snapshot.incidentId === incidentId
    ? snapshot.feed
    : { status: "loading", items: [] };
}

/**
 * Loads the recovery candidate plan for one Incident correlation. This is a
 * read-only observation of the server-generated candidates; it never executes a
 * recovery. Without a correlation id the feed stays `idle` (nothing to observe).
 */
export function useRecoveryPlan(
  correlationId?: string | null,
  pollMs = 0,
): RecoveryPlanFeed {
  const [feed, setFeed] = useState<RecoveryPlanFeed>(
    () => ({ status: correlationId ? "loading" : "idle", plan: null }),
  );
  useEffect(() => {
    if (!correlationId) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const schedule = (delay: number) => {
      timer = window.setTimeout(load, delay);
    };
    const load = () => {
      void getRecoveryPlanByCorrelation(correlationId, { signal: controller.signal })
        .then((plan) => {
          if (controller.signal.aborted) return;
          setFeed({ status: "ready", plan });
          if (pollMs > 0) schedule(pollMs);
        })
        .catch((cause: unknown) => {
          if (controller.signal.aborted || isAbortError(cause)) return;
          if (isApiError(cause) && cause.status === 404) {
            setFeed({ status: "pending", plan: null });
            schedule(2000);
            return;
          }
          setFeed({ status: "unavailable", plan: null });
          if (pollMs > 0) schedule(pollMs);
        });
    };
    load();
    return () => {
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [correlationId, pollMs]);
  return feed;
}

export function useRemediationBundle(correlationId?: string | null): RemediationBundleFeed {
  const [snapshot, setSnapshot] = useState<{
    correlationId: string | null;
    feed: RemediationBundleFeed;
  }>({
    correlationId: null,
    feed: { status: "idle", bundle: null },
  });
  useEffect(() => {
    if (!correlationId) return;
    const controller = new AbortController();
    void getRemediationBundle(correlationId, { signal: controller.signal })
      .then((bundle) => {
        if (controller.signal.aborted) return;
        setSnapshot({ correlationId, feed: { status: "ready", bundle } });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setSnapshot({ correlationId, feed: { status: "unavailable", bundle: null } });
      });
    return () => controller.abort();
  }, [correlationId]);
  if (!correlationId) return { status: "idle", bundle: null };
  return snapshot.correlationId === correlationId
    ? snapshot.feed
    : { status: "loading", bundle: null };
}

export function useRecoveryAudit(correlationId?: string | null, pollMs = 0): RecoveryAuditFeed {
  const [feed, setFeed] = useState<RecoveryAuditFeed>(
    () => ({ status: correlationId ? "loading" : "idle", items: [] }),
  );
  useEffect(() => {
    if (!correlationId) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const load = () => {
      void getAuditTimeline(correlationId, { limit: 50, signal: controller.signal })
        .then((response) => {
          if (controller.signal.aborted) return;
          setFeed({ status: "ready", items: response.items });
        })
        .catch((cause: unknown) => {
          if (controller.signal.aborted || isAbortError(cause)) return;
          setFeed({ status: "unavailable", items: [] });
        })
        .finally(() => {
          if (!controller.signal.aborted && pollMs > 0) timer = window.setTimeout(load, pollMs);
        });
    };
    load();
    return () => {
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [correlationId, pollMs]);
  return feed;
}
