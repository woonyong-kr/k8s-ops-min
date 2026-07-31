import type { RcaReport } from "../api/evidence-schemas";

type EvidenceReference = RcaReport["supporting_evidence_refs"][number];
type MissingEvidenceCheck = RcaReport["missing_evidence_checks"][number];

export interface MissingEvidencePresentation {
  message: string;
  metadata: string | null;
}

export interface MissingEvidenceListPresentation extends MissingEvidencePresentation {
  item: string;
}

export interface RcaSummaryPresentationInput {
  report?: {
    narrativeExecutiveSummary?: string | null;
    narrativeRecommendedAction?: string | null;
    narrativeReasoning?: string | null;
    reason?: string | null;
    evidenceSummary?: string | null;
    evidenceBundleSummary?: string | null;
    /** Accepted for contract regression tests but intentionally never rendered. */
    rawAction?: string | null;
  };
  issue?: {
    situationSummary?: string | null;
    recommendedActionSummary?: string | null;
    evidenceSummary?: string | null;
    evidenceBundleSummary?: string | null;
  };
  recovery?: {
    summary?: string | null;
    candidateDescription?: string | null;
    candidateTitle?: string | null;
  };
  fallbackSituation?: string | null;
}

export interface RcaSummaryPresentation {
  situation: string | null;
  recommendedAction: string | null;
  evidence: string | null;
  evidenceBundle: string | null;
}

function firstText(...values: Array<string | null | undefined>): string | null {
  for (const value of values) {
    const normalized = value?.trim();
    if (normalized) return normalized;
  }
  return null;
}

function normalizedEvidenceSource(source: string): string {
  const normalized = source.trim().toLowerCase();
  if (normalized === "k8s") return "kubernetes";
  if (normalized === "prometheus") return "metrics";
  if (normalized === "loki") return "logs";
  if (normalized === "tempo") return "traces";
  return normalized;
}

function normalizedEvidenceToken(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/gu, "");
}

function evidenceSourceMetadataLabel(source: string): string {
  const normalized = normalizedEvidenceSource(source);
  if (normalized === "kubernetes") return "Kubernetes";
  if (normalized === "metrics") return "메트릭";
  if (normalized === "logs") return "로그";
  if (normalized === "traces") return "트레이스";
  if (normalized === "metadata") return "메타데이터";
  if (normalized === "signals") return "판별 신호";
  return "추가 근거";
}

function evidenceStatusLabel(status: string): string {
  const normalized = status.trim().toLowerCase();
  if (normalized === "missing") return "누락";
  if (normalized === "unavailable") return "수집 불가";
  if (normalized === "partial") return "부분 수집";
  if (normalized === "pending") return "수집 대기";
  return "확인 필요";
}

function matchingMissingCheck(
  item: string,
  checks: readonly MissingEvidenceCheck[],
): MissingEvidenceCheck | null {
  const normalizedItem = item.trim().toLowerCase();
  const exact = checks.find(
    (check) => check.check_id.trim().toLowerCase() === normalizedItem,
  );
  if (exact) return exact;
  if (normalizedItem.startsWith("signal:")) return null;
  const source = normalizedEvidenceSource(normalizedItem.split(":", 1)[0] ?? normalizedItem);
  return checks.find((check) => {
    const checkSource = check.source ? normalizedEvidenceSource(check.source) : "";
    const checkId = check.check_id.trim().toLowerCase();
    return checkSource === source || checkId === `evidence:${source}:required`;
  }) ?? null;
}

/**
 * Prefer backend-authored missing-check context. Legacy reports do not carry
 * that structure, so their internal signal IDs intentionally collapse to one
 * safe operator instruction instead of accumulating check-specific UI rules.
 */
export function missingEvidencePresentation(
  item: string,
  checks: readonly MissingEvidenceCheck[] = [],
): MissingEvidencePresentation {
  const check = matchingMissingCheck(item, checks);
  if (check) {
    const source = check.source?.trim()
      ? evidenceSourceMetadataLabel(check.source)
      : null;
    const status = check.status?.trim()
      ? evidenceStatusLabel(check.status)
      : null;
    return {
      message: firstText(check.reason)
        ?? "원인 확정에 필요한 추가 진단 근거를 수집해 확인하세요.",
      metadata: [source, status].filter((value): value is string => Boolean(value)).join(" · ")
        || null,
    };
  }

  const normalized = item.trim().toLowerCase();
  if (normalized.startsWith("signal:")) {
    return {
      message: "원인 확정에 필요한 추가 진단 신호를 수집해 확인하세요.",
      metadata: null,
    };
  }
  const legacySource = normalizedEvidenceSource(normalized.split(":", 1)[0] ?? normalized);
  if (
    normalized.includes(":")
    && ["kubernetes", "metrics", "logs", "traces", "metadata"].includes(legacySource)
  ) {
    return {
      message: `${evidenceSourceMetadataLabel(legacySource)} 근거를 추가로 수집해 확인하세요.`,
      metadata: null,
    };
  }
  const readable = item.trim().replace(/[_:.]+/gu, " ").replace(/\s+/gu, " ");
  return {
    message: readable ? `${readable}을 확인하세요.` : "추가 진단 근거를 확인하세요.",
    metadata: null,
  };
}

/**
 * Collapse equivalent legacy gaps without hiding distinct backend-authored
 * reasons or source/status metadata.
 */
export function missingEvidencePresentations(
  items: readonly string[],
  checks: readonly MissingEvidenceCheck[] = [],
): MissingEvidenceListPresentation[] {
  const seen = new Set<string>();
  const presentations: MissingEvidenceListPresentation[] = [];
  for (const item of items) {
    const presentation = missingEvidencePresentation(item, checks);
    const key = `${presentation.message}\u0000${presentation.metadata ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    presentations.push({ item, ...presentation });
  }
  return presentations;
}

/** Backward-compatible string adapter for the few legacy call sites. */
export function missingEvidenceAction(
  item: string,
  checks: readonly MissingEvidenceCheck[] = [],
): string {
  return missingEvidencePresentation(item, checks).message;
}

export function evidenceReferenceMatches(
  value: string,
  evidence: EvidenceReference,
): boolean {
  const source = normalizedEvidenceSource(evidence.source);
  const token = normalizedEvidenceToken(value);
  return [
    source,
    evidence.name,
    `${source}:${evidence.name}`,
    evidence.check_id ?? "",
    evidence.evidence_ref ?? "",
  ].some((candidate) => normalizedEvidenceToken(candidate) === token);
}

function sameEvidenceReference(
  left: EvidenceReference,
  right: EvidenceReference,
): boolean {
  if (
    left.evidence_ref
    && right.evidence_ref
    && normalizedEvidenceToken(left.evidence_ref) === normalizedEvidenceToken(right.evidence_ref)
  ) {
    return true;
  }
  if (
    normalizedEvidenceSource(left.source) !== normalizedEvidenceSource(right.source)
    || normalizedEvidenceToken(left.name) !== normalizedEvidenceToken(right.name)
    || normalizedEvidenceToken(left.check_id ?? "") !== normalizedEvidenceToken(right.check_id ?? "")
  ) {
    return false;
  }
  return !left.evidence_key
    || !right.evidence_key
    || left.evidence_key === right.evidence_key;
}

function referenceRichness(reference: EvidenceReference): number {
  return (reference.evidence_key ? 20 : 0)
    + (reference.evidence_ref ? 4 : 0)
    + [
      reference.schema_version,
      reference.source_version,
      reference.collector,
      reference.collector_version,
      reference.query_version,
      reference.collected_at,
      reference.source_id,
      reference.agent_id,
      reference.window_start,
    ].filter((value) => value !== null && value !== "").length;
}

function mergeEvidenceReference(
  current: EvidenceReference,
  incoming: EvidenceReference,
): EvidenceReference {
  const preferred = referenceRichness(incoming) > referenceRichness(current)
    ? incoming
    : current;
  const fallback = preferred === current ? incoming : current;
  return {
    source: firstText(preferred.source, fallback.source) ?? current.source,
    name: firstText(preferred.name, fallback.name) ?? current.name,
    check_id: firstText(preferred.check_id, fallback.check_id),
    summary: firstText(preferred.summary, fallback.summary),
    query: firstText(preferred.query, fallback.query),
    evidence_ref: firstText(preferred.evidence_ref, fallback.evidence_ref),
    schema_version: preferred.schema_version ?? fallback.schema_version,
    source_version: firstText(preferred.source_version, fallback.source_version),
    collector: firstText(preferred.collector, fallback.collector),
    collector_version: firstText(preferred.collector_version, fallback.collector_version),
    query_version: firstText(preferred.query_version, fallback.query_version),
    collected_at: firstText(preferred.collected_at, fallback.collected_at),
    evidence_key: firstText(preferred.evidence_key, fallback.evidence_key),
    source_id: firstText(preferred.source_id, fallback.source_id),
    agent_id: firstText(preferred.agent_id, fallback.agent_id),
    window_start: firstText(preferred.window_start, fallback.window_start),
  };
}

/**
 * Dedupe logical references without discarding a later window key/lineage.
 * Distinct keyed windows remain separate even when source/name are identical.
 */
export function mergeEvidenceReferences(
  references: readonly EvidenceReference[],
): EvidenceReference[] {
  const merged: EvidenceReference[] = [];
  for (const reference of references) {
    const index = merged.findIndex((candidate) => sameEvidenceReference(candidate, reference));
    if (index < 0) {
      merged.push(reference);
      continue;
    }
    merged[index] = mergeEvidenceReference(merged[index]!, reference);
  }
  return merged;
}

function stringValue(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return null;
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function metricNameLabel(name: string): string {
  const normalized = name.replace(/_/gu, " ");
  const labels: Record<string, string> = {
    container_restart_rate: "컨테이너 재시작률",
    pod_ready_ratio: "Pod 준비 비율",
    startup_failure_total: "시작 실패 누계",
  };
  return labels[name] ? `${labels[name]}(${normalized})` : normalized;
}

function traceStatusLabel(value: string | null): string | null {
  if (!value) return null;
  const normalized = value.trim().toLowerCase();
  if (["error", "err", "failed", "failure", "2"].includes(normalized)) return "오류";
  if (["ok", "success", "1"].includes(normalized)) return "정상";
  if (["unset", "unknown", "0"].includes(normalized)) return "상태 미확인";
  return value;
}

function tracePreviewLine(value: unknown): string | null {
  const trace = recordValue(value);
  if (!trace) return null;
  const service = firstText(
    stringValue(trace.service),
    stringValue(trace.service_name),
    stringValue(trace.serviceName),
    stringValue(trace.rootServiceName),
  );
  const operation = firstText(
    stringValue(trace.operation),
    stringValue(trace.operation_name),
    stringValue(trace.operationName),
    stringValue(trace.rootTraceName),
    stringValue(trace.name),
  );
  const status = traceStatusLabel(firstText(
    stringValue(trace.status),
    stringValue(trace.status_code),
    stringValue(trace.statusCode),
  ));
  const duration = firstText(
    stringValue(trace.duration_ms),
    stringValue(trace.durationMs),
  );
  const traceId = firstText(
    stringValue(trace.trace_id),
    stringValue(trace.traceID),
    stringValue(trace.traceId),
  );
  const parts = [
    service,
    operation,
    status,
    duration ? `${duration}ms` : null,
    traceId ? `trace ${traceId.slice(0, 12)}` : null,
  ].filter((item): item is string => Boolean(item));
  return parts.length > 0 ? parts.join(" · ") : null;
}

function metadataSnapshotLine(value: unknown): string | null {
  const snapshot = recordValue(value);
  const workload = recordValue(snapshot?.workload);
  if (!snapshot || !workload) return null;
  const kind = stringValue(workload.kind);
  const namespace = stringValue(workload.namespace);
  const name = stringValue(workload.name);
  const identity = [
    kind,
    namespace && name ? `${namespace}/${name}` : name ?? namespace,
  ].filter((item): item is string => Boolean(item)).join(" ");
  if (!identity) return null;
  const status = recordValue(snapshot.deployment_status);
  const ready = stringValue(status?.ready_replicas);
  const desired = firstText(
    stringValue(status?.desired_replicas),
    stringValue(status?.replicas),
  );
  return ready !== null && desired !== null
    ? `${identity} · Ready ${ready}/${desired}`
    : identity;
}

/** Build a bounded, operator-readable preview from the actual provider payload. */
export function evidencePreviewLines(
  source: string,
  payload: Record<string, unknown>,
): string[] {
  const normalized = normalizedEvidenceSource(source);
  const value = payload[source] ?? payload[normalized];
  if (normalized === "logs" && Array.isArray(value)) {
    const lines: string[] = [];
    for (const rawEntry of value) {
      const entry = recordValue(rawEntry);
      if (!entry) continue;
      const matchedEntries = Array.isArray(entry.matched_entries) ? entry.matched_entries : [];
      for (const rawMatch of matchedEntries) {
        const match = recordValue(rawMatch);
        const message = match ? stringValue(match.message) : null;
        if (message) lines.push(message);
        if (lines.length >= 5) return lines;
      }
      const streams = Array.isArray(entry.streams) ? entry.streams : [];
      for (const rawStream of streams) {
        const stream = recordValue(rawStream);
        const values = stream && Array.isArray(stream.values) ? stream.values : [];
        for (const rawLine of values) {
          const line = recordValue(rawLine);
          const message = line ? stringValue(line.line) : null;
          if (message) lines.push(message);
          if (lines.length >= 5) return lines;
        }
      }
    }
    return lines;
  }
  if (normalized === "metrics" && recordValue(value)) {
    const results = recordValue(recordValue(value)?.results);
    if (!results) return [];
    return Object.entries(results).slice(0, 5).map(([name, result]) => {
      const samples = recordValue(result)?.samples;
      const sampleItems = Array.isArray(samples) ? samples : [];
      const latest = sampleItems.length > 0 ? recordValue(sampleItems[sampleItems.length - 1]) : null;
      const latestValue = latest ? stringValue(latest.value) : null;
      return latestValue === null ? metricNameLabel(name) : `${metricNameLabel(name)} · 최근 값 ${latestValue}`;
    });
  }
  if (normalized === "kubernetes" && recordValue(value)) {
    const body = recordValue(value)!;
    const groups: Array<[string, unknown]> = [
      ["이벤트", body.events], ["Pod", body.pods], ["노드", body.nodes], ["워크로드", body.workloads],
    ];
    return groups.flatMap(([label, items]) => (
      Array.isArray(items) && items.length > 0 ? [`${label} ${items.length}건`] : []
    )).slice(0, 5);
  }
  if (normalized === "traces" && recordValue(value)) {
    const results = recordValue(recordValue(value)?.results);
    if (!results) return [];
    const lines: string[] = [];
    for (const result of Object.values(results)) {
      const resultRecord = recordValue(result);
      const analysis = recordValue(resultRecord?.analysis);
      const summaries = Array.isArray(analysis?.trace_summaries)
        ? analysis.trace_summaries
        : Array.isArray(resultRecord?.traces)
          ? resultRecord.traces
          : [];
      for (const summary of summaries) {
        const line = tracePreviewLine(summary);
        if (line) lines.push(line);
        if (lines.length >= 5) return lines;
      }
    }
    return lines;
  }
  if (normalized === "metadata" && recordValue(value)) {
    const metadata = recordValue(value)!;
    const context = recordValue(metadata.change_context) ?? metadata;
    const snapshots = [
      ...(recordValue(context.current_workload_snapshot)
        ? [context.current_workload_snapshot]
        : []),
      ...(Array.isArray(context.current_workload_snapshots)
        ? context.current_workload_snapshots
        : []),
    ];
    const lines = snapshots
      .map(metadataSnapshotLine)
      .filter((line): line is string => Boolean(line))
      .slice(0, 3);
    const groups: Array<[string, unknown]> = [
      ["Service selector 일치", context.service_selector_matches],
      ["준비 EndpointSlice", context.endpoint_slice_ready_endpoints],
      ["참조 ConfigMap·Secret", context.referenced_config_objects],
      ["ResourceQuota", context.resource_quotas],
    ];
    for (const [label, items] of groups) {
      if (Array.isArray(items) && items.length > 0) lines.push(`${label} ${items.length}건`);
      if (lines.length >= 5) break;
    }
    return lines.slice(0, 5);
  }
  return [];
}

/**
 * Keep live report copy authoritative over the issue projection, which can lag
 * by one projection cycle. Recovery copy is a final semantic fallback. The raw
 * machine action (`plan_recovery`, etc.) is intentionally absent.
 */
export function rcaSummaryPresentation(
  input: RcaSummaryPresentationInput,
): RcaSummaryPresentation {
  const { report = {}, issue = {}, recovery = {} } = input;
  return {
    situation: firstText(
      report.narrativeExecutiveSummary,
      report.reason,
      issue.situationSummary,
      recovery.summary,
      input.fallbackSituation,
    ),
    recommendedAction: firstText(
      report.narrativeRecommendedAction,
      issue.recommendedActionSummary,
      recovery.candidateDescription,
      recovery.candidateTitle,
    ),
    evidence: firstText(
      report.narrativeReasoning,
      report.evidenceSummary,
      issue.evidenceSummary,
      recovery.summary,
    ),
    evidenceBundle: firstText(
      report.evidenceBundleSummary,
      issue.evidenceBundleSummary,
    ),
  };
}
