import { apiRequest, type ApiPath } from "./client";
import {
  evidenceListSchema,
  evidenceWindowPayloadSchema,
  rcaReportListSchema,
  type EvidenceList,
  type EvidenceWindowPayload,
  type RcaReportList,
} from "./evidence-schemas";
import { optionalQueryString, withQuery } from "./url";

export const EVIDENCE_DEFAULT_LIMIT = 50;
export const EVIDENCE_MAX_LIMIT = 200;
export const RCA_REPORT_DEFAULT_LIMIT = 50;
export const RCA_REPORT_MAX_LIMIT = 200;

export interface EvidenceListOptions {
  correlationId?: string;
  kind?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
  cursor?: string;
  signal?: AbortSignal;
}

export interface RcaReportListOptions {
  correlationId?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
  cursor?: string;
  signal?: AbortSignal;
}

/** Lists safe, stored Evidence summaries for an Incident or workspace. */
export async function listEvidence(
  options: EvidenceListOptions = {},
): Promise<EvidenceList> {
  const limit = options.limit ?? EVIDENCE_DEFAULT_LIMIT;
  assertLimit(limit, EVIDENCE_MAX_LIMIT, "Evidence");
  const offset = options.offset ?? 0;
  assertOffset(offset);
  const path = withQuery("/api/evidence" as ApiPath, [
    ["correlation_id", optionalQueryString(options.correlationId)],
    ["kind", optionalQueryString(options.kind)],
    ["since", optionalQueryString(options.since)],
    ["until", optionalQueryString(options.until)],
    ["limit", limit],
    ["offset", offset === 0 ? undefined : offset],
    ["cursor", optionalOpaqueCursor(options.cursor)],
  ]);
  return apiRequest(path, evidenceListSchema, { signal: options.signal });
}

/** Reads one stored evidence window, optionally narrowed to one source. */
export async function getEvidenceWindowPayload(
  evidenceKey: string,
  options: { source?: string; signal?: AbortSignal } = {},
): Promise<EvidenceWindowPayload> {
  const path = withQuery(
    `/api/evidence/windows/${encodeURIComponent(evidenceKey)}` as ApiPath,
    [["source", optionalQueryString(options.source)]],
  );
  return apiRequest(path, evidenceWindowPayloadSchema, { signal: options.signal });
}

/** Lists stored RCA report summaries, optionally scoped to one Incident. */
export async function listRcaReports(
  options: RcaReportListOptions = {},
): Promise<RcaReportList> {
  const limit = options.limit ?? RCA_REPORT_DEFAULT_LIMIT;
  assertLimit(limit, RCA_REPORT_MAX_LIMIT, "RCA report");
  const offset = options.offset ?? 0;
  assertOffset(offset);
  const path = withQuery("/api/rca-reports" as ApiPath, [
    ["correlation_id", optionalQueryString(options.correlationId)],
    ["since", optionalQueryString(options.since)],
    ["until", optionalQueryString(options.until)],
    ["limit", limit],
    ["offset", offset === 0 ? undefined : offset],
    ["cursor", optionalOpaqueCursor(options.cursor)],
  ]);
  return apiRequest(path, rcaReportListSchema, { signal: options.signal });
}

function assertLimit(limit: number, max: number, label: string): void {
  if (!Number.isInteger(limit) || limit < 1 || limit > max) {
    throw new RangeError(`${label} list limit must be an integer from 1 to ${max}`);
  }
}

function assertOffset(offset: number): void {
  if (!Number.isInteger(offset) || offset < 0) {
    throw new RangeError("Evidence/RCA report offset must be a non-negative integer");
  }
}

function optionalOpaqueCursor(value: string | undefined): string | undefined {
  if (value === undefined) return undefined;
  if (value.trim() === "") throw new TypeError("cursor must not be blank");
  return value;
}
