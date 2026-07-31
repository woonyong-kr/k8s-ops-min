export type ScopeFreshness = "live" | "stale" | "partial" | "disconnected";

export interface ClusterScope {
  workspaceId: string;
  clusterId: string;
  namespaces?: readonly string[];
  freshness: ScopeFreshness;
}

export interface ResourceRef {
  apiGroup?: string;
  version?: string;
  kind: string;
  namespace: string | null;
  name: string;
  uid: string;
}

export interface CapabilitySet {
  scope: ClusterScope;
  resource: ResourceRef;
  revision: string;
  actions: readonly string[];
}

export interface DirectCommandRequest {
  scope: ClusterScope;
  resource: ResourceRef;
  action: string;
  diff: Record<string, unknown>;
  confirmation: boolean;
  reason: string;
}

export interface CommandReceipt {
  accepted: true;
  commandId: string;
  eventId: string;
  /** Immutable request event, later copied to the async audit projection. */
  auditEventId: string;
  correlationId: string;
  status:
    | "queued"
    | "leased"
    | "running"
    | "cancel_requested"
    | "cancelling"
    | "completed"
    | "failed"
    | "cancelled";
}

export interface OperationEvent {
  commandId: string;
  sequence: number;
  kind: "progress" | "log" | "completed" | "failed" | "cancelled";
  payload: Record<string, unknown>;
  occurredAt: string;
}

export type OperationStreamFailure = "forbidden" | "invalid" | "unavailable";

export type OperationStreamLifecycle =
  | { state: "connecting" }
  | { state: "connected" }
  | { state: "reconnecting"; attempt: number; retryAfterMs: number }
  | { state: "closed" }
  | { state: "failed"; failure: OperationStreamFailure };

export interface OperationEventsSubscription {
  afterSequence?: number;
  onLifecycle?: (lifecycle: OperationStreamLifecycle) => void;
  signal?: AbortSignal;
}

export interface OperationEventsPort {
  subscribeOperationEvents(
    commandId: string,
    subscription?: OperationEventsSubscription,
  ): AsyncIterable<OperationEvent>;
}

export function buildScopeKey(scope: ClusterScope): string {
  const namespaces = [...new Set(scope.namespaces ?? [])]
    .map((namespace) => namespace.trim())
    .filter(Boolean)
    .sort()
    .join(",");
  return `${scope.workspaceId}:${scope.clusterId}:${namespaces}`;
}

export function canDispatchDirectCommand(request: DirectCommandRequest): boolean {
  return request.confirmation
    && request.action.trim().length > 0
    && request.reason.trim().length > 0
    && request.resource.uid.trim().length > 0;
}
