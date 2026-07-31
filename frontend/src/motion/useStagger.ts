import { usePrefersReducedMotion } from "./usePrefersReducedMotion";

export const MOTION_DURATION_MS = Object.freeze({
  instant: 120,
  quick: 180,
  pop: 340,
  layout: 320,
  camera: 420,
  value: 500,
});

export const STAGGER_MS = Object.freeze({
  node: 70,
  pod: 32,
  row: 18,
  max: 520,
});

function finiteNonNegative(value: number, name: string): number {
  if (!Number.isFinite(value) || value < 0) {
    throw new TypeError(`${name} must be a finite non-negative number`);
  }
  return value;
}

export function staggerDelay(
  index: number,
  stepMs: number,
  maxMs = STAGGER_MS.max,
): number {
  const safeIndex = Math.floor(finiteNonNegative(index, "index"));
  const safeStep = finiteNonNegative(stepMs, "stepMs");
  const safeMax = finiteNonNegative(maxMs, "maxMs");
  return Math.min(safeIndex * safeStep, safeMax);
}

export function podWaveDelay(nodeIndex: number, podIndex: number): number {
  const nodeDelay = staggerDelay(nodeIndex, STAGGER_MS.node);
  const podDelay = staggerDelay(podIndex, STAGGER_MS.pod);
  return Math.min(nodeDelay + podDelay, STAGGER_MS.max);
}

export function useStagger(index: number, stepMs: number): number {
  const reducedMotion = usePrefersReducedMotion();
  return reducedMotion ? 0 : staggerDelay(index, stepMs);
}
