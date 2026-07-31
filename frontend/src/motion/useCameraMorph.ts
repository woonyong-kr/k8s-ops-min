import { type RefObject, useCallback, useRef } from "react";
import { MOTION_DURATION_MS } from "./useStagger";
import {
  prefersReducedMotion,
  usePrefersReducedMotion,
} from "./usePrefersReducedMotion";

export const CAMERA_MORPH_EASING = "cubic-bezier(0.34, 1.20, 0.50, 1)";
export const MAX_CONCURRENT_MORPHS = 200;

export type MorphRects = ReadonlyMap<string, DOMRect>;

export interface CameraMorphOptions {
  reducedMotion?: boolean;
}

export interface CameraMorphController {
  capture: () => void;
  play: () => Animation[];
}

let pendingRouteMorphRects: Map<string, DOMRect> | null = null;

export function captureRouteMorph(root: ParentNode = document): void {
  pendingRouteMorphRects = collectMorphRects(root);
}

function takeRouteMorphRects(): Map<string, DOMRect> {
  const captured = pendingRouteMorphRects ?? new Map<string, DOMRect>();
  pendingRouteMorphRects = null;
  return captured;
}

export function collectMorphRects(root: ParentNode): Map<string, DOMRect> {
  const rects = new Map<string, DOMRect>();
  root.querySelectorAll<HTMLElement>("[data-morph-id]").forEach((element) => {
    const morphId = element.dataset.morphId?.trim();
    if (morphId && !rects.has(morphId)) {
      rects.set(morphId, element.getBoundingClientRect());
    }
  });
  return rects;
}

export function morphTransform(from: DOMRect, to: DOMRect): string | null {
  if (to.width <= 0 || to.height <= 0) return null;

  const deltaX = from.left - to.left;
  const deltaY = from.top - to.top;
  const scaleX = from.width / to.width;
  const scaleY = from.height / to.height;
  return `translate(${deltaX}px, ${deltaY}px) scale(${scaleX}, ${scaleY})`;
}

export function morph(
  fromRects: MorphRects,
  root: ParentNode,
  options: CameraMorphOptions = {},
): Animation[] {
  const reducedMotion = options.reducedMotion ?? prefersReducedMotion();
  if (reducedMotion) return [];

  const animations: Animation[] = [];
  const matchedIds = new Set<string>();
  root.querySelectorAll<HTMLElement>("[data-morph-id]").forEach((element) => {
    const morphId = element.dataset.morphId?.trim();
    const from = morphId ? fromRects.get(morphId) : undefined;
    if (
      !from
      || !morphId
      || matchedIds.has(morphId)
      || animations.length >= MAX_CONCURRENT_MORPHS
    ) return;

    const transform = morphTransform(from, element.getBoundingClientRect());
    if (!transform || typeof element.animate !== "function") return;

    matchedIds.add(morphId);
    animations.push(element.animate(
      [
        { opacity: 0.6, transform },
        { opacity: 1, transform: "none" },
      ],
      {
        duration: MOTION_DURATION_MS.camera,
        easing: CAMERA_MORPH_EASING,
        fill: "both",
      },
    ));
  });
  return animations;
}

export function useCameraMorph(
  rootRef: RefObject<HTMLElement | null>,
): CameraMorphController {
  const fromRects = useRef<Map<string, DOMRect>>(takeRouteMorphRects());
  const reducedMotion = usePrefersReducedMotion();

  const capture = useCallback(() => {
    fromRects.current = rootRef.current
      ? collectMorphRects(rootRef.current)
      : new Map();
  }, [rootRef]);

  const play = useCallback(() => {
    if (!rootRef.current) return [];
    const captured = fromRects.current;
    fromRects.current = new Map();
    return morph(captured, rootRef.current, { reducedMotion });
  }, [reducedMotion, rootRef]);

  return { capture, play };
}
