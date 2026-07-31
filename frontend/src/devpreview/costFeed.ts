import { useEffect, useState } from "react";

import { getCostOverview } from "../api/cost-overview";

// UI-PHASE2-001 §5.2: typed live adapter for the cost widget/page. Reads
// `GET /api/cost/overview`. The current dev contract reports cost observation
// as `unavailable` with reason codes; that is an honest successful contract
// state. Catalogue prices are never backfilled to fabricate a dollar total.

export type CostFeedStatus = "loading" | "ready" | "error";

export interface CostOverviewView {
  status: CostFeedStatus;
  /** Backend observation availability, e.g. "unavailable". */
  observationAvailability: string | null;
  reasonCodes: string[];
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

export function useCostOverview(clusterIds?: readonly string[]): CostOverviewView {
  const [view, setView] = useState<CostOverviewView>({
    status: "loading",
    observationAvailability: null,
    reasonCodes: [],
  });
  const key = (clusterIds ?? []).join(" ");
  useEffect(() => {
    const controller = new AbortController();
    const ids = key ? key.split(" ") : undefined;
    void getCostOverview({ clusterIds: ids }, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
        setView({
          status: "ready",
          observationAvailability: response.observation.availability,
          reasonCodes: [...response.summary.reason_codes],
        });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setView({ status: "error", observationAvailability: null, reasonCodes: [] });
      });
    return () => controller.abort();
  }, [key]);
  return view;
}
