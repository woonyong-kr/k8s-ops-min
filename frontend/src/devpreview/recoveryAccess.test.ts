import { describe, expect, it } from "vitest";

import type { RcaReport } from "../api/evidence-schemas";
import type { RecoveryPlan } from "../api/recovery-schemas";
import {
  canOpenRecoveryPlan,
  canStartRecoveryReview,
  hasRcaCauseOrCandidate,
} from "./recoveryAccess";

const reportWithCandidate = {
  root_cause: "insufficient_evidence",
  candidates: [{ candidate_id: "probe_path_wrong" }],
} as RcaReport;

const planWithCandidate = {
  candidates: [{ action_id: "fix-probe" }],
} as RecoveryPlan;

describe("recovery access", () => {
  it("blocks recovery when RCA has neither a cause nor a candidate", () => {
    expect(hasRcaCauseOrCandidate(null, null)).toBe(false);
    expect(canOpenRecoveryPlan(null, null, planWithCandidate)).toBe(false);
  });

  it("opens the recovery tab for a confirmed cause before candidates arrive", () => {
    expect(canOpenRecoveryPlan("Probe path 설정 오류", null, null)).toBe(true);
  });

  it("allows recovery for a final cause with a real recovery candidate", () => {
    expect(canOpenRecoveryPlan("Probe path 설정 오류", null, planWithCandidate)).toBe(true);
  });

  it("allows recovery for an observed RCA candidate with a real recovery candidate", () => {
    expect(canOpenRecoveryPlan(null, reportWithCandidate, planWithCandidate)).toBe(true);
  });

  it("does not start another AI review after the recovery was selected", () => {
    expect(canStartRecoveryReview({ selected: true, pending: false })).toBe(false);
    expect(canStartRecoveryReview({ selected: false, pending: true })).toBe(false);
    expect(canStartRecoveryReview({ selected: false, pending: false })).toBe(true);
  });
});
