import { z } from "zod";

export const changeTimelineBucketSchema = z.strictObject({
  startMs: z.number().int().nonnegative(),
  endMs: z.number().int().positive(),
  total: z.number().int().nonnegative(),
  warnings: z.number().int().nonnegative(),
}).refine((bucket) => bucket.startMs < bucket.endMs && bucket.warnings <= bucket.total, {
  message: "timeline bucket bounds and warning totals must be valid",
});

export const changeTimelineEventSchema = z.strictObject({
  id: z.string().min(1).max(512),
  kind: z.enum(["inventory_event", "incident", "deployment", "gitops_change"]),
  occurredMs: z.number().int().nonnegative(),
  title: z.string().min(1).max(240),
  severity: z.enum(["info", "warning", "critical", "unknown"]),
});

export const changeTimelineGapSchema = z.strictObject({
  from: z.number().int().nonnegative(),
  to: z.number().int().positive(),
}).refine((gap) => gap.from < gap.to, { message: "timeline gap must have positive width" });

export const changeTimelineSchema = z.strictObject({
  buckets: z.array(changeTimelineBucketSchema).max(2_000),
  events: z.array(changeTimelineEventSchema).max(10_000),
  gaps: z.array(changeTimelineGapSchema).max(2_000),
}).superRefine((timeline, context) => {
  validateOrderedRanges(timeline.buckets, "startMs", "endMs", ["buckets"], context);
  validateOrderedRanges(timeline.gaps, "from", "to", ["gaps"], context);
  for (let index = 1; index < timeline.events.length; index += 1) {
    const previous = timeline.events[index - 1]!;
    const current = timeline.events[index]!;
    const previousKey = [previous.occurredMs, previous.kind, previous.id] as const;
    const currentKey = [current.occurredMs, current.kind, current.id] as const;
    if (compareEventKey(previousKey, currentKey) > 0) {
      context.addIssue({
        code: "custom",
        message: "timeline events must use canonical time, kind, and id order",
        path: ["events", index],
      });
    }
  }
});

function validateOrderedRanges(
  values: Array<Record<string, number>>,
  startKey: string,
  endKey: string,
  path: [string],
  context: z.RefinementCtx,
) {
  for (let index = 1; index < values.length; index += 1) {
    if (values[index]![startKey]! < values[index - 1]![endKey]!) {
      context.addIssue({
        code: "custom",
        message: "timeline ranges must be ordered and non-overlapping",
        path: [...path, index],
      });
    }
  }
}

function compareEventKey(
  left: readonly [number, string, string],
  right: readonly [number, string, string],
): number {
  return left[0] - right[0] || left[1].localeCompare(right[1]) || left[2].localeCompare(right[2]);
}

export type ChangeTimelineEndpoint = z.infer<typeof changeTimelineSchema>;
