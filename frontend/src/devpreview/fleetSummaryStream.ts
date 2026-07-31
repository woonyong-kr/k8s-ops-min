import { useEffect, useRef, useState } from "react";

import { FLEET_SUMMARY_EVENTS_PATH } from "../api/fleet";
import {
  fleetSummaryStreamFrameSchema,
  type FleetSummary,
} from "../api/schemas";

const LIVE_CHECK_MS = 1_000;
const MIN_LIVE_WINDOW_MS = 12_000;
const LIVE_WINDOW_MULTIPLIER = 2.5;

export interface FleetSummaryStreamView {
  live: boolean;
}

/**
 * Consume complete, authorized fleet payloads from one workspace SSE.
 *
 * EventSource owns reconnect/Last-Event-ID. Invalid frames never replace the
 * last-known-good summary; a missing/failed stream only marks the channel
 * unavailable so the caller can enable its bounded HTTP fallback.
 */
export function useFleetSummaryStream(
  scopeKey: string,
  onSummary: (summary: FleetSummary) => void,
): FleetSummaryStreamView {
  const onSummaryRef = useRef(onSummary);
  const [liveScope, setLiveScope] = useState<string | null>(null);

  useEffect(() => {
    onSummaryRef.current = onSummary;
  });

  useEffect(() => {
    if (!scopeKey || typeof EventSource === "undefined") return undefined;
    let disposed = false;
    let lastFrameAt = 0;
    let liveWindowMs = MIN_LIVE_WINDOW_MS;
    let source: EventSource;

    const markUnavailable = () => {
      if (!disposed) setLiveScope((current) => current === scopeKey ? null : current);
    };

    try {
      source = new EventSource(FLEET_SUMMARY_EVENTS_PATH);
    } catch {
      markUnavailable();
      return undefined;
    }

    const onFleetSummary = (event: Event) => {
      if (disposed || !(event instanceof MessageEvent)) return;
      let raw: unknown;
      try {
        raw = JSON.parse(String(event.data));
      } catch {
        markUnavailable();
        return;
      }
      const parsed = fleetSummaryStreamFrameSchema.safeParse(raw);
      if (!parsed.success) {
        markUnavailable();
        return;
      }
      lastFrameAt = Date.now();
      liveWindowMs = Math.max(
        MIN_LIVE_WINDOW_MS,
        parsed.data.refresh_after_ms * LIVE_WINDOW_MULTIPLIER,
      );
      setLiveScope(scopeKey);
      onSummaryRef.current(parsed.data.summary);
    };

    source.addEventListener("fleet_summary", onFleetSummary);
    source.onerror = markUnavailable;
    const healthTimer = window.setInterval(() => {
      if (lastFrameAt > 0 && Date.now() - lastFrameAt > liveWindowMs) {
        markUnavailable();
      }
    }, LIVE_CHECK_MS);

    return () => {
      disposed = true;
      window.clearInterval(healthTimer);
      source.removeEventListener("fleet_summary", onFleetSummary);
      source.close();
    };
  }, [scopeKey]);

  return { live: Boolean(scopeKey) && liveScope === scopeKey };
}
