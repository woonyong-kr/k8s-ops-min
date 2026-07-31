import { useEffect, useState } from "react";

import { getTrafficOverview } from "../api/traffic-overview";
import type {
  TrafficOverviewEndpoint,
  TrafficVerdict,
} from "../api/traffic-overview-schemas";
import { serviceKeyOf } from "./relationTopologyFeed";
import { useVisibleRefreshClock } from "../shared/data/useVisibleRefreshClock";

// UI-PHASE2-001 TOP-03 · DEMO_WIRING_PLAN §3.4/§6 (traffic route conflict):
// telemetry for the service-topology surface comes ONLY from
// `GET /api/traffic/flows`. The flows contract exposes observed `connections`
// and a `verdict` per directed pair — it does NOT carry RPS, p99 or an error
// percentage, so those are always honest "관측 안 됨" (never fabricated).
// A legitimately `unavailable` observation is a successful contract state and
// yields no per-edge telemetry, not substituted fixture numbers.

export type TrafficTelemetryStatus = "loading" | "ready" | "unavailable" | "error";

export interface EdgeTelemetry {
  /** Observed connection count over the window, summed across matched flows. */
  connections: number;
  /** Worst verdict observed on the pair (error > dropped > unknown > forwarded). */
  verdict: TrafficVerdict;
  protocol: string;
  observedAt: string;
}

export interface TrafficTelemetryView {
  status: TrafficTelemetryStatus;
  /** Keyed by `${sourceServiceKey}>>${targetServiceKey}`. */
  byPair: Record<string, EdgeTelemetry>;
  reasonCodes: string[];
  observedAt: string | null;
}

const LOADING: TrafficTelemetryView = {
  status: "loading",
  byPair: {},
  reasonCodes: [],
  observedAt: null,
};
export const TRAFFIC_REFRESH_MS = 10_000;

export function pairKey(sourceKey: string, targetKey: string): string {
  return `${sourceKey}>>${targetKey}`;
}

const VERDICT_SEVERITY: Record<TrafficVerdict, number> = {
  forwarded: 0,
  unknown: 1,
  dropped: 2,
  error: 3,
};

function worseVerdict(a: TrafficVerdict, b: TrafficVerdict): TrafficVerdict {
  return VERDICT_SEVERITY[b] > VERDICT_SEVERITY[a] ? b : a;
}

export function toTrafficTelemetryView(
  endpoint: TrafficOverviewEndpoint,
): TrafficTelemetryView {
  const rel = endpoint.relationships;
  if (rel.availability === "unavailable" || rel.edges === null) {
    return {
      status: "unavailable",
      byPair: {},
      reasonCodes: [...rel.reason_codes],
      observedAt: null,
    };
  }
  const byPair: Record<string, EdgeTelemetry> = {};
  for (const edge of rel.edges) {
    const source = serviceKeyOf(edge.source.cluster_id, edge.source.namespace, edge.source.name);
    const target = serviceKeyOf(edge.target.cluster_id, edge.target.namespace, edge.target.name);
    const key = pairKey(source, target);
    const existing = byPair[key];
    if (existing === undefined) {
      byPair[key] = {
        connections: edge.connections,
        verdict: edge.verdict,
        protocol: edge.protocol,
        observedAt: edge.observed_at,
      };
    } else {
      byPair[key] = {
        connections: existing.connections + edge.connections,
        verdict: worseVerdict(existing.verdict, edge.verdict),
        protocol: existing.protocol,
        observedAt: existing.observedAt,
      };
    }
  }
  const observedAt = endpoint.observation.availability === "unavailable"
    ? null
    : endpoint.observation.observed_at;
  return { status: "ready", byPair, reasonCodes: [...rel.reason_codes], observedAt };
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

/**
 * Reads observed traffic flows for the scoped clusters and indexes them by
 * directed service identity for a topology join. A scope change aborts the
 * obsolete request. Never seeds state synchronously — only `.then/.catch`.
 */
export function useTrafficTelemetry(
  clusterIds: readonly string[],
): TrafficTelemetryView {
  const [view, setView] = useState<TrafficTelemetryView>(LOADING);
  const key = clusterIds.join(",");
  const { revision } = useVisibleRefreshClock(key !== "", TRAFFIC_REFRESH_MS);
  useEffect(() => {
    const ids = key ? key.split(",") : [];
    const controller = new AbortController();
    void getTrafficOverview(
      { clusterIds: ids, since: "1h", limit: 200 },
      controller.signal,
    )
      .then((endpoint) => {
        if (controller.signal.aborted) return;
        setView(toTrafficTelemetryView(endpoint));
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setView((prev) => ({ ...prev, status: "error" }));
      });
    return () => controller.abort();
  }, [key, revision]);
  return view;
}

// 트래픽 판정 타입도 이 어댑터 경계를 통해 노출한다(shell이 api 스키마를 직접 import하지 않도록).
export type { TrafficVerdict } from "../api/traffic-overview-schemas";
