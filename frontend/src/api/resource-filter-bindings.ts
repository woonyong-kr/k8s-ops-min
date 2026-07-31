import type { RefinementCtx } from "zod";

import {
  labelFacetPageSchema,
  resourceFilterFacetPageSchema,
  type ResourceFilterFacetAxis,
} from "./resource-filter-schemas";

export function bindFacetPageToRequest(
  axis: ResourceFilterFacetAxis,
  selectedValues: readonly string[],
) {
  return resourceFilterFacetPageSchema.superRefine((page, context) => {
    if (page.axis !== axis) {
      context.addIssue({
        code: "custom",
        message: "facet response axis must equal the requested axis",
        path: ["axis"],
      });
    }
    validateResolvedSet(
      page.selected_resolutions.map((resolution) => resolution.value),
      selectedValues,
      context,
    );
  });
}

export function bindLabelPageToRequest(selectedLabels: readonly string[]) {
  return labelFacetPageSchema.superRefine((page, context) => {
    validateResolvedSet(
      page.selected_resolutions.map((resolution) => resolution.selector),
      selectedLabels,
      context,
    );
  });
}

function validateResolvedSet(
  actual: readonly string[],
  expected: readonly string[],
  context: RefinementCtx,
): void {
  const canonicalActual = [...actual].sort(compareUnicodeCodePoints);
  const matches = canonicalActual.length === expected.length &&
    canonicalActual.every((value, index) => value === expected[index]);
  if (matches) return;
  context.addIssue({
    code: "custom",
    message: "selected resolutions must exactly cover the requested selection",
    path: ["selected_resolutions"],
  });
}

function compareUnicodeCodePoints(left: string, right: string): number {
  const leftPoints = Array.from(left, (character) => character.codePointAt(0) ?? 0);
  const rightPoints = Array.from(right, (character) => character.codePointAt(0) ?? 0);
  const length = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < length; index += 1) {
    const difference = leftPoints[index] - rightPoints[index];
    if (difference !== 0) return difference;
  }
  return leftPoints.length - rightPoints.length;
}
