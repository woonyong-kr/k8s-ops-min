import { Unplug } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { createClusterDisconnectPort } from "../app/composition/surfaces/clusters";
import type { ClusterDisconnectPort } from "../features/clusters/clustersContract";
import type { HomeClusterChoice } from "../features/home/homeContract";
import {
  ClusterDisconnectDialog,
  type DisconnectPhase,
} from "../pages/clusters/ClusterDisconnectDialog";
import { BLUE, TYPE, UI, inkA } from "./theme";
import type { DevpreviewCluster } from "./contracts";

const LIVE_CLUSTER_LIFECYCLE_PORT = createClusterDisconnectPort();

export interface ClusterLifecycleControlProps {
  cluster: DevpreviewCluster | null;
  onDisconnected: (clusterId: string) => void;
  onPhaseChange?: (clusterId: string, phase: DisconnectPhase) => void;
  port?: ClusterDisconnectPort;
  roles: readonly string[];
}

/**
 * Active UnifiedApp bridge for the canonical cluster lifecycle contract.
 *
 * This deliberately reuses ClusterDisconnectDialog/createClustersAdapter so
 * the live shell cannot drift into a second unregister implementation. The
 * control never calls DELETE by itself: the operator must open the dialog,
 * enter the exact cluster name and explicitly submit.
 */
export function ClusterLifecycleControl({
  cluster,
  onDisconnected,
  onPhaseChange,
  port = LIVE_CLUSTER_LIFECYCLE_PORT,
  roles,
}: ClusterLifecycleControlProps) {
  const [open, setOpen] = useState(false);
  const lastPhase = useRef<DisconnectPhase | null>(null);
  const choice = useMemo(() => cluster ? toHomeClusterChoice(cluster) : null, [cluster]);
  const allowed = cluster !== null
    && cluster.role === "target"
    && !cluster.readOnly
    && roles.includes("service_admin");

  if (!allowed || cluster === null || choice === null) return null;

  return (
    <>
      <button
        className="product-focusable product-control"
        aria-label={`${cluster.displayName} 연결 해제`}
        aria-pressed={open}
        onClick={() => setOpen(true)}
        title="클러스터 연결 해제"
        type="button"
        style={{
          alignItems: "center",
          background: open ? inkA(0.07) : UI.card,
          border: `1px solid ${UI.line}`,
          borderRadius: 9,
          color: open ? BLUE : UI.ink2,
          cursor: "pointer",
          display: "inline-flex",
          flexShrink: 0,
          fontSize: TYPE.label,
          fontWeight: 600,
          gap: 6,
          padding: "6px 9px",
        }}
      >
        <Unplug aria-hidden="true" size={13} />
        <span className="hide-narrow">연결 해제</span>
      </button>
      <ClusterDisconnectDialog
        cluster={choice}
        key={choice.id}
        onDisconnected={onDisconnected}
        onOpenChange={(nextOpen) => {
          setOpen(nextOpen);
          if (!nextOpen) lastPhase.current = null;
        }}
        onPhaseChange={(clusterId, phase) => {
          if (lastPhase.current === phase) return;
          lastPhase.current = phase;
          onPhaseChange?.(clusterId, phase);
        }}
        open={open}
        port={port}
      />
    </>
  );
}

export function toHomeClusterChoice(cluster: DevpreviewCluster): HomeClusterChoice {
  return {
    id: cluster.id,
    workspaceId: cluster.workspaceId,
    name: cluster.displayName,
    environment: cluster.environment,
    provider: cluster.provider,
    connectionStage: cluster.connectionStage ?? null,
    registrationState: registrationState(cluster),
    connectionState: connectionState(cluster.connectionStatus),
    lastObservedAt: cluster.lastObservedAt,
    nodeCount: cluster.nodeCount,
    podCount: cluster.podCount,
    incidentCount: cluster.incidentCount,
  };
}

function registrationState(cluster: DevpreviewCluster): HomeClusterChoice["registrationState"] {
  if (cluster.connectionStage === "expired") return "expired";
  if (cluster.connectionStage === "token_issued" || cluster.connectionStage === "awaiting_install") {
    return "pending";
  }
  return "active";
}

function connectionState(status: string): HomeClusterChoice["connectionState"] {
  const normalized = status.trim().toLocaleLowerCase();
  if (normalized === "online" || normalized === "connected") return "online";
  if (normalized === "stale") return "stale";
  if (normalized.includes("pending") || normalized.includes("install")) return "pending";
  if (normalized === "offline" || normalized === "disconnected") return "offline";
  return "unknown";
}
