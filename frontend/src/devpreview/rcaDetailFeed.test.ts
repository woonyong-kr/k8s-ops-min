import { describe, expect, it } from "vitest";

import { evidenceRecordSchema, rcaReportSchema } from "../api/evidence-schemas";
import {
  parseEvidenceObjectReference,
  resolveEvidenceObjectReference,
} from "./rcaDetailFeed";

describe("parseEvidenceObjectReference", () => {
  it("extracts an RCA correlation and provider fragment", () => {
    expect(parseEvidenceObjectReference(
      "object://evidence/8e7f72bf-9ce5-4fb4-a60d-253f55a299a4.json#traces:cluster_recent_traces",
    )).toEqual({
      value: "object://evidence/8e7f72bf-9ce5-4fb4-a60d-253f55a299a4.json#traces:cluster_recent_traces",
      correlationId: "8e7f72bf-9ce5-4fb4-a60d-253f55a299a4",
      source: "traces",
      name: "cluster_recent_traces",
    });
  });

  it("rejects unrelated and source-less references", () => {
    expect(parseEvidenceObjectReference("https://example.test/evidence")).toBeNull();
    expect(parseEvidenceObjectReference("object://evidence/correlation.json")).toBeNull();
  });
});

describe("resolveEvidenceObjectReference", () => {
  it("skips source summaries without an evidence key when resolving old metadata refs", () => {
    const pointer = parseEvidenceObjectReference(
      "object://evidence/correlation-1.json#metadata:current_workload_snapshots",
    );
    const records = [
      evidenceRecordSchema.parse({
        id: 3,
        workspace_id: "default",
        correlation_id: "correlation-2",
        kind: "rca_bundle",
        cluster_id: "target-1",
        evidence_ref: "object://evidence/correlation-2.json",
        summary: "another incident window",
        sources: [{
          source: "metadata",
          summary: "wrong incident metadata",
          schema_version: 1,
          collector: "cluster-agent",
          collector_version: "2",
          source_version: "2",
          query_version: "1",
          collected_at: "2026-07-24T00:03:00Z",
          evidence_key: "workspace:target:metadata:wrong-window",
          source_id: "metadata",
          agent_id: "agent-1",
          window_start: "2026-07-24T00:03:00Z",
        }],
        created_at: "2026-07-24T00:03:01Z",
      }),
      evidenceRecordSchema.parse({
        id: 2,
        workspace_id: "default",
        correlation_id: "correlation-1",
        kind: "rca_bundle",
        cluster_id: "target-1",
        evidence_ref: "object://evidence/correlation-1.json",
        summary: "newer record without a complete window",
        sources: [{
          source: "metadata",
          summary: "metadata pending",
          schema_version: 1,
          collector: "cluster-agent",
          collector_version: "2",
          source_version: "2",
          query_version: "1",
          collected_at: "2026-07-24T00:02:00Z",
          evidence_key: null,
          source_id: "metadata",
          agent_id: "agent-1",
          window_start: "2026-07-24T00:02:00Z",
        }],
        created_at: "2026-07-24T00:02:01Z",
      }),
      evidenceRecordSchema.parse({
        id: 1,
        workspace_id: "default",
        correlation_id: "correlation-1",
        kind: "rca_bundle",
        cluster_id: "target-1",
        evidence_ref: "object://evidence/correlation-1.json",
        summary: "complete metadata window",
        sources: [{
          source: "metadata",
          summary: "one workload snapshot",
          schema_version: 1,
          collector: "cluster-agent",
          collector_version: "2",
          source_version: "2",
          query_version: "1",
          collected_at: "2026-07-24T00:01:00Z",
          evidence_key: "workspace:target:metadata:window-1",
          source_id: "metadata",
          agent_id: "agent-1",
          window_start: "2026-07-24T00:01:00Z",
        }],
        created_at: "2026-07-24T00:01:01Z",
      }),
    ];

    expect(pointer).not.toBeNull();
    expect(resolveEvidenceObjectReference(pointer!, records)).toMatchObject({
      source: "metadata",
      name: "current_workload_snapshots",
      evidence_key: "workspace:target:metadata:window-1",
      window_start: "2026-07-24T00:01:00Z",
    });
  });
});

describe("RCA report detail contract", () => {
  it("accepts the evidence summaries emitted by the backend", () => {
    const parsed = rcaReportSchema.parse({
      id: 1,
      workspace_id: "default",
      correlation_id: "correlation-1",
      analysis_status: "completed",
      root_cause: "upstream_latency",
      action: "rollback",
      incident_id: "incident-1",
      cluster_id: "target-1",
      symptom: "request latency",
      severity: "warning",
      first_seen_at: "2026-07-24T00:00:00Z",
      confidence: 0.91,
      reason: "Tempo에서 오류 span이 확인됨",
      evidence_ref: "object://evidence/correlation-1.json",
      supporting_evidence: [],
      missing_evidence: [],
      evidence_summary: "trace 오류율이 증가했습니다.",
      evidence_bundle_summary: "traces, metrics",
      created_at: "2026-07-24T00:01:00Z",
      resource_kind: "Deployment",
      resource_name: "checkout",
      namespace: "sandbox",
      secondary_symptoms: [],
      selected_candidate_id: null,
      candidates: [],
      supporting_evidence_refs: [],
      missing_evidence_checks: [],
      narrative: null,
      narrative_status: "unavailable",
    });

    expect(parsed.evidence_summary).toBe("trace 오류율이 증가했습니다.");
    expect(parsed.evidence_bundle_summary).toBe("traces, metrics");
  });
});
