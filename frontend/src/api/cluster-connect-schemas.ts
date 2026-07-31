import { z } from "zod";

export const clusterConnectProviderSchema = z.enum(["aws", "gcp", "azure", "onprem"]);

const clusterConnectResponseWireSchema = z.strictObject({
  cluster_id: z.string(),
  install_command: z.string().min(1),
  // During a rolling backend deployment an older healthy replica can still
  // return the POSIX-only receipt. Keep the UI usable and derive the
  // PowerShell fallback at the presentation boundary instead of rejecting the
  // entire one-time command as an invalid payload.
  powershell_install_command: z.string().min(1).optional(),
  expires_at: z.string(),
});

export const clusterConnectResponseSchema = clusterConnectResponseWireSchema.transform(
  (response) => ({
    ...response,
    powershell_install_command:
      response.powershell_install_command ?? response.install_command,
  }),
);

export const clusterConnectStatusResponseSchema = z.strictObject({
  status: z.enum(["waiting", "connected", "expired", "failed"]),
  stage: z.string().nullable().optional(),
  agent_version: z.string().nullable(),
  connected_at: z.string().nullable(),
  failure_reason: z.string().nullable().optional(),
});

export type ClusterConnectProvider = z.infer<typeof clusterConnectProviderSchema>;
export type ClusterConnectResponse = z.infer<typeof clusterConnectResponseSchema>;
export type ClusterConnectStatusResponse = z.infer<typeof clusterConnectStatusResponseSchema>;
