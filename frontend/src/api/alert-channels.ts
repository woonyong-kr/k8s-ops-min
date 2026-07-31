import { apiRequest, type ApiPath } from "./client";
import {
  alertChannelListSchema,
  alertChannelTestRequestSchema,
  alertChannelTestResponseSchema,
  type AlertChannelList,
  type AlertChannelTestInput,
  type AlertChannelTestResponse,
} from "./alert-channels-schemas";

export const ALERT_CHANNELS_PATH: ApiPath = "/api/alert-channels";
export const ALERT_CHANNEL_TEST_PATH: ApiPath = "/api/alert-channels/test";

export function listAlertChannels(signal?: AbortSignal): Promise<AlertChannelList> {
  return apiRequest(ALERT_CHANNELS_PATH, alertChannelListSchema, { signal });
}

export function testAlertChannel(
  input: AlertChannelTestInput,
  signal?: AbortSignal,
): Promise<AlertChannelTestResponse> {
  const payload = alertChannelTestRequestSchema.parse(input);
  return apiRequest(ALERT_CHANNEL_TEST_PATH, alertChannelTestResponseSchema, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
}
