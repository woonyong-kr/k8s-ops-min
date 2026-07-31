import { useEffect, useState } from "react";

import {
  getTimelinePins,
} from "../api/timeline";
import type { TimelineEndpointPinSet } from "../api/timeline-schemas";
import { loadSharedTimelineCapabilities } from "./timelineCapabilitiesFeed";

// Timeline의 동적 사건·커버리지는 changeTimelineFeed의 snapshot+cursor SSE가
// 소유한다. 이 보조 피드는 변경 빈도가 낮은 서버 제어 계약과 고정 원장만 읽어,
// 같은 화면에서 overview를 중복 조회하거나 별도 폴링하지 않는다.

export type TimelineBoardStatus = "loading" | "ready" | "unavailable";

export interface TimelinePinView {
  pinId: string;
  kind: "resource" | "application";
  label: string;
  sublabel: string | null;
}

export interface TimelinePinsView {
  status: "loading" | "ready" | "unavailable" | "unsupported";
  revision: number | null;
  items: TimelinePinView[];
}

export interface TimelineBoardView {
  status: TimelineBoardStatus;
  selectedSourceMode: string | null;
  availableSourceModes: string[];
  pins: TimelinePinsView;
}

const INITIAL: TimelineBoardView = {
  status: "loading",
  selectedSourceMode: null,
  availableSourceModes: [],
  pins: { status: "loading", revision: null, items: [] },
};

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

function toPinView(pin: TimelineEndpointPinSet["pins"][number]): TimelinePinView {
  if (pin.subject.kind === "resource") {
    const resource = pin.subject.resource;
    return {
      pinId: pin.pin_id,
      kind: "resource",
      label: `${resource.kind} · ${resource.name}`,
      sublabel: resource.namespace ?? pin.subject.scope.cluster_id,
    };
  }
  return {
    pinId: pin.pin_id,
    kind: "application",
    label: pin.subject.snapshot.name,
    sublabel: pin.subject.snapshot.repository_id,
  };
}

export function useTimelineBoard(): TimelineBoardView {
  const [board, setBoard] = useState<TimelineBoardView>(INITIAL);
  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    void (async () => {
      const capabilities = await loadSharedTimelineCapabilities();
      if (signal.aborted) return;
      const pinsAvailable = capabilities.control_surface.pins.availability === "available";
      let pins: TimelinePinsView;
      if (!pinsAvailable) {
        pins = { status: "unsupported", revision: null, items: [] };
      } else {
        try {
          const pinSet = await getTimelinePins(signal);
          if (signal.aborted) return;
          pins = {
            status: "ready",
            revision: pinSet.revision,
            items: pinSet.pins.map(toPinView),
          };
        } catch (cause: unknown) {
          if (signal.aborted || isAbortError(cause)) return;
          pins = { status: "unavailable", revision: null, items: [] };
        }
      }
      setBoard({
        status: "ready",
        selectedSourceMode: capabilities.selected_source_mode,
        availableSourceModes: [...capabilities.available_source_modes],
        pins,
      });
    })().catch((cause: unknown) => {
      if (signal.aborted || isAbortError(cause)) return;
      setBoard({
        status: "unavailable",
        selectedSourceMode: null,
        availableSourceModes: [],
        pins: { status: "unavailable", revision: null, items: [] },
      });
    });
    return () => controller.abort();
  }, []);
  return board;
}
