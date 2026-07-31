import { z } from "zod";

import { rcaTimelineItemSchema } from "./schemas";

/** Runtime contract for the paginated Issues/RCA timeline response. */
export const rcaListSchema = z.strictObject({
  items: z.array(rcaTimelineItemSchema),
});

export type RcaList = z.infer<typeof rcaListSchema>;
export type RcaListItem = RcaList["items"][number];
