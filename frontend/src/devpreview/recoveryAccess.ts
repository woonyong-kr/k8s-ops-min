import type { RcaReport } from "../api/evidence-schemas";
import type { RecoveryPlan } from "../api/recovery-schemas";
import { hasConfirmedRootCause } from "./issueAnalysisState";

export function hasRcaCauseOrCandidate(
  rootCause: string | null | undefined,
  report: RcaReport | null,
): boolean {
  if (hasConfirmedRootCause(rootCause) || hasConfirmedRootCause(report?.root_cause)) return true;
  return (report?.candidates.length ?? 0) > 0;
}

export function canOpenRecoveryPlan(
  rootCause: string | null | undefined,
  report: RcaReport | null,
  _plan: RecoveryPlan | null,
): boolean {
  return hasRcaCauseOrCandidate(rootCause, report);
}

export function canStartRecoveryReview({
  selected,
  pending,
}: {
  selected: boolean;
  pending: boolean;
}): boolean {
  return !selected && !pending;
}
