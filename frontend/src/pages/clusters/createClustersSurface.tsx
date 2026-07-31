import type { ComponentType } from "react";
import type {
  ClusterDisconnectPort,
  ClustersPort,
} from "../../features/clusters/clustersContract";
import { ClustersPage } from "./ClustersPage";

export function createClustersSurface(port: ClustersPort & ClusterDisconnectPort): ComponentType {
  function ClustersSurface() {
    return <ClustersPage port={port} />;
  }

  ClustersSurface.displayName = "ClustersSurface";
  return ClustersSurface;
}
