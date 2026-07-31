import { useCallback, useEffect, useMemo, useState } from "react";

import {
  deleteNodeAlias,
  listNodeAliases,
  updateNodeAlias,
} from "../api/node-aliases";
import type { NodeAliasItem } from "../api/node-aliases-schemas";

export type NodeAliasStatus = "idle" | "loading" | "ready" | "unavailable";

export interface NodeAliasView {
  alias: string;
  revision: number;
  updatedAt: string | null;
}

interface NodeAliasState {
  status: NodeAliasStatus;
  aliasesByNodeName: Map<string, NodeAliasView>;
}

interface InternalNodeAliasState extends NodeAliasState {
  clusterId: string | null;
}

const EMPTY_STATE: NodeAliasState = {
  status: "idle",
  aliasesByNodeName: new Map(),
};

const EMPTY_INTERNAL_STATE: InternalNodeAliasState = {
  ...EMPTY_STATE,
  clusterId: null,
};

export function useNodeAliases(clusterId: string | null): NodeAliasState & {
  saveAlias: (nodeName: string, alias: string) => Promise<NodeAliasView | null>;
  deleteAlias: (nodeName: string) => Promise<void>;
} {
  const [state, setState] = useState<InternalNodeAliasState>(EMPTY_INTERNAL_STATE);

  useEffect(() => {
    if (!clusterId) return;
    const controller = new AbortController();
    void listNodeAliases(clusterId, controller.signal)
      .then((response) => {
        setState((previous) => {
          if (previous.clusterId === clusterId && previous.status === "ready") {
            return previous;
          }
          return {
            clusterId,
            status: "ready",
            aliasesByNodeName: aliasesToMap(response.aliases),
          };
        });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setState((previous) => {
          if (previous.clusterId === clusterId && previous.status === "ready") {
            return previous;
          }
          return { clusterId, status: "unavailable", aliasesByNodeName: new Map() };
        });
      });
    return () => controller.abort();
  }, [clusterId]);

  const saveAlias = useCallback(async (nodeName: string, alias: string) => {
    if (!clusterId) return null;
    const normalized = normalizeAlias(alias);
    if (!normalized) {
      await deleteNodeAlias(clusterId, nodeName);
      setState((previous) => withoutAlias(visibleInternalState(previous, clusterId), nodeName));
      return null;
    }
    const saved = await updateNodeAlias(clusterId, nodeName, normalized);
    const view = toAliasView(saved);
    setState((previous) => withAlias(visibleInternalState(previous, clusterId), saved.node_name, view));
    return view;
  }, [clusterId]);

  const deleteAlias = useCallback(async (nodeName: string) => {
    if (!clusterId) return;
    await deleteNodeAlias(clusterId, nodeName);
    setState((previous) => withoutAlias(visibleInternalState(previous, clusterId), nodeName));
  }, [clusterId]);

  const visibleState = useMemo<NodeAliasState>(() => {
    if (!clusterId) return EMPTY_STATE;
    if (state.clusterId === clusterId) {
      return {
        status: state.status,
        aliasesByNodeName: state.aliasesByNodeName,
      };
    }
    return { status: "loading", aliasesByNodeName: new Map() };
  }, [clusterId, state]);

  return useMemo(() => ({
    ...visibleState,
    saveAlias,
    deleteAlias,
  }), [deleteAlias, saveAlias, visibleState]);
}

function aliasesToMap(items: readonly NodeAliasItem[]): Map<string, NodeAliasView> {
  return new Map(items.map((item) => [item.node_name, toAliasView(item)]));
}

function toAliasView(item: NodeAliasItem): NodeAliasView {
  return {
    alias: item.alias,
    revision: item.revision,
    updatedAt: item.updated_at,
  };
}

function withAlias(
  previous: InternalNodeAliasState,
  nodeName: string,
  alias: NodeAliasView,
): InternalNodeAliasState {
  const next = new Map(previous.aliasesByNodeName);
  next.set(nodeName, alias);
  return { clusterId: previous.clusterId, status: "ready", aliasesByNodeName: next };
}

function withoutAlias(
  previous: InternalNodeAliasState,
  nodeName: string,
): InternalNodeAliasState {
  const next = new Map(previous.aliasesByNodeName);
  next.delete(nodeName);
  return { clusterId: previous.clusterId, status: "ready", aliasesByNodeName: next };
}

function visibleInternalState(
  previous: InternalNodeAliasState,
  clusterId: string,
): InternalNodeAliasState {
  if (previous.clusterId === clusterId) return previous;
  return { clusterId, status: "ready", aliasesByNodeName: new Map() };
}

function normalizeAlias(value: string): string {
  return value.trim().replace(/\s+/gu, " ");
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
