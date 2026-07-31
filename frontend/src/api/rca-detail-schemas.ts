import { z } from "zod";

import { rcaTimelineItemSchema } from "./schemas";

/** Runtime contract for one Incident/RCA detail response. */
export const rcaIncidentSchema = z.strictObject({
  item: rcaTimelineItemSchema,
});

export type RcaIncident = z.infer<typeof rcaIncidentSchema>;
export type RcaIncidentItem = RcaIncident["item"];
