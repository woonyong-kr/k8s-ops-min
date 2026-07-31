import type { InventoryResource } from "../api/inventory-schemas";

export type InventoryTableRow = Record<string, unknown>;

type UnknownRecord = Record<string, unknown>;

function record(value: unknown): UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as UnknownRecord
    : {};
}

function records(value: unknown): UnknownRecord[] {
  return Array.isArray(value) ? value.map(record).filter((item) => Object.keys(item).length > 0) : [];
}

function stringValue(source: UnknownRecord, key: string): string | undefined {
  const value = source[key];
  return typeof value === "string" && value.trim() !== "" ? value : undefined;
}

function numberValue(source: UnknownRecord, key: string): number | undefined {
  const value = source[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function booleanValue(source: UnknownRecord, key: string): boolean | undefined {
  const value = source[key];
  return typeof value === "boolean" ? value : undefined;
}

function displayNumber(value: number): string {
  return value >= 100 ? Math.round(value).toString() : value.toFixed(value >= 10 ? 1 : 2).replace(/\.?0+$/, "");
}

function formatAge(value: string | null | undefined, now: number): string | undefined {
  if (!value) return undefined;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return undefined;
  const seconds = Math.max(0, Math.floor((now - timestamp) / 1000));
  if (seconds < 60) return `${seconds}초`;
  if (seconds < 3_600) return `${Math.floor(seconds / 60)}분`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3_600)}시간`;
  return `${Math.floor(seconds / 86_400)}일`;
}

function formatDuration(start: string | undefined, end: string | undefined, now: number): string | undefined {
  if (!start) return undefined;
  const startedAt = Date.parse(start);
  const endedAt = end ? Date.parse(end) : now;
  if (!Number.isFinite(startedAt) || !Number.isFinite(endedAt)) return undefined;
  const seconds = Math.max(0, Math.floor((endedAt - startedAt) / 1000));
  if (seconds < 60) return `${seconds}초`;
  if (seconds < 3_600) return `${Math.floor(seconds / 60)}분`;
  return `${Math.floor(seconds / 3_600)}시간`;
}

function ratioParts(status: string): { ready?: number; desired?: number } {
  const match = status.match(/^(\d+)\/(\d+)$/);
  return match ? { ready: Number(match[1]), desired: Number(match[2]) } : {};
}

function imageList(summary: UnknownRecord): string | undefined {
  const containers = records(record(record(summary.pod_template).spec).containers);
  const images = containers.map((container) => stringValue(container, "image")).filter(Boolean);
  return images.length > 0 ? images.join(", ") : stringValue(summary, "image");
}

function keyValueList(value: unknown): string | undefined {
  const entries = Object.entries(record(value));
  return entries.length > 0
    ? entries.map(([key, item]) => `${key}=${String(item)}`).sort().join(", ")
    : undefined;
}

function portList(value: unknown): string | undefined {
  const ports = records(value).map((port) => {
    const name = stringValue(port, "name");
    const protocol = stringValue(port, "protocol");
    const portNumber = numberValue(port, "port");
    const target = port.targetPort;
    if (portNumber === undefined) return undefined;
    const endpoint = target !== undefined && target !== null ? `${portNumber}→${String(target)}` : String(portNumber);
    return [name, endpoint, protocol].filter(Boolean).join(" · ");
  }).filter(Boolean);
  return ports.length > 0 ? ports.join(", ") : undefined;
}

function stringList(value: unknown): string | undefined {
  if (!Array.isArray(value)) return undefined;
  const items = value.filter((item): item is string => typeof item === "string" && item.trim() !== "");
  return items.length > 0 ? items.join(", ") : undefined;
}

function meter(used: number | undefined, limit: number | undefined, unit: string) {
  if (used === undefined || limit === undefined || limit <= 0) return undefined;
  return {
    used: `${displayNumber(used)}${unit}`,
    lim: `${displayNumber(limit)}${unit}`,
    pct: Math.min(100, Math.max(0, Math.round((used / limit) * 100))),
  };
}

function workloadState(summary: UnknownRecord, fallback: string): string {
  const failed = numberValue(summary, "failed") ?? 0;
  const active = numberValue(summary, "active") ?? 0;
  const succeeded = numberValue(summary, "succeeded") ?? 0;
  const completions = numberValue(summary, "completions") ?? 1;
  if (failed > 0) return "Failed";
  if (active > 0) return "Running";
  if (succeeded >= completions) return "Complete";
  return fallback;
}

function isBad(resource: InventoryResource): boolean {
  const health = resource.health.toLowerCase();
  return ["critical", "degraded", "unhealthy", "failed", "error"].includes(health)
    || /(crash|oom|fail|error|evict)/i.test(resource.status);
}

/**
 * Converts the bounded, server-observed summary into the shared table keys.
 * Missing evidence stays undefined so the UI renders an honest dash.
 */
export function projectInventoryResourceRow(
  resource: InventoryResource,
  now = Date.now(),
): InventoryTableRow {
  const summary = record(resource.summary);
  const ratio = ratioParts(resource.status);
  const desired = numberValue(summary, "desired_replicas") ?? ratio.desired;
  const readyCount = numberValue(summary, "ready_replicas") ?? ratio.ready;
  const createdAt = stringValue(summary, "creation_timestamp") ?? resource.created_at ?? resource.first_seen_at;
  const kind = resource.kind;
  const row: InventoryTableRow = {
    name: kind === "Event" ? stringValue(summary, "name") ?? resource.name : resource.name,
    ns: resource.namespace ?? undefined,
    kind,
    status: resource.status,
    health: resource.health,
    resource_type: resource.resource_type,
    uid: resource.uid ?? undefined,
    created: createdAt ?? undefined,
    cluster: resource.cluster_id,
    _key: resource.inventory_key,
    bad: isBad(resource),
    age: formatAge(createdAt, now),
  };

  if (["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"].includes(kind)) {
    row.desired = desired;
    row.ready = desired !== undefined && readyCount !== undefined ? `${readyCount}/${desired}` : resource.status;
    row.utd = numberValue(summary, "updated_replicas");
    row.avail = numberValue(summary, "available_replicas");
    row.img = imageList(summary);
    const ownerKind = stringValue(summary, "owner_kind");
    const ownerName = stringValue(summary, "owner_name");
    row.owner = ownerKind && ownerName ? `${ownerKind}/${ownerName}` : undefined;
    row.st = row.bad ? resource.status : "Active";
  }

  if (kind === "Job") {
    row.st = workloadState(summary, resource.status);
    row.comp = `${numberValue(summary, "succeeded") ?? 0}/${numberValue(summary, "completions") ?? 1}`;
    row.dur = formatDuration(
      stringValue(summary, "start_time"),
      stringValue(summary, "completion_time"),
      now,
    );
  }

  if (kind === "CronJob") {
    row.sched = stringValue(summary, "schedule");
    row.tz = stringValue(summary, "timezone") ?? stringValue(summary, "time_zone");
    row.susp = booleanValue(summary, "suspend");
    row.active = numberValue(summary, "active");
    row.last = formatAge(
      stringValue(summary, "last_schedule_time") ?? stringValue(summary, "last_scheduled_time"),
      now,
    );
  }

  if (kind === "Pod") {
    const containers = records(summary.containers);
    const stateReason = containers
      .map((container) => stringValue(container, "state_reason") ?? stringValue(container, "last_state_reason"))
      .find(Boolean);
    row.ctr = containers.length || undefined;
    row.status = stateReason ?? stringValue(summary, "reason") ?? stringValue(summary, "phase") ?? resource.status;
    row.cpu = meter(
      numberValue(summary, "cpu_mcores"),
      numberValue(summary, "cpu_limit_mcores"),
      "m",
    );
    row.mem = meter(
      numberValue(summary, "mem_mib"),
      numberValue(summary, "mem_limit_mib"),
      "Mi",
    );
    row.img = imageList(summary);
  }

  if (kind === "Service") {
    row.type = stringValue(summary, "type") ?? resource.status;
    row.sel = keyValueList(summary.selector);
    row.ports = portList(summary.ports);
    row.ext = stringList(summary.external_hosts) ?? stringValue(summary, "external_url");
  }

  if (kind === "Ingress") {
    row.class = stringValue(summary, "ingress_class_name");
    row.hosts = stringList(summary.hosts);
    row.addr = stringList(summary.external_hosts);
  }

  if (kind === "EndpointSlice") {
    row.at = stringValue(summary, "address_type") ?? resource.status;
    row.ports = portList(summary.ports);
    row.ep = numberValue(summary, "endpoint_count");
  }

  if (kind === "NetworkPolicy") {
    row.types = stringList(summary.policy_types);
    row.sel = keyValueList(summary.pod_selector);
    row.rules = numberValue(summary, "rule_count");
  }

  if (kind === "HorizontalPodAutoscaler") {
    const reference = record(summary.scale_target_ref);
    const referenceKind = stringValue(reference, "kind");
    const referenceName = stringValue(reference, "name");
    row.ref = referenceKind && referenceName ? `${referenceKind}/${referenceName}` : undefined;
    row.targets = stringList(summary.targets);
    row.min = numberValue(summary, "min_replicas");
    row.max = numberValue(summary, "max_replicas");
    row.reps = numberValue(summary, "current_replicas");
  }

  if (kind === "PersistentVolumeClaim") {
    row.st = stringValue(summary, "phase") ?? resource.status;
    row.vol = stringValue(summary, "volume_name");
    row.cap = stringValue(summary, "capacity");
    row.am = stringList(summary.access_modes);
    row.sc = stringValue(summary, "storage_class");
  }

  if (kind === "StorageClass") {
    row.prov = stringValue(summary, "provisioner");
    row.rec = stringValue(summary, "reclaim_policy");
    row.vbm = stringValue(summary, "volume_binding_mode");
  }

  if (kind === "ConfigMap") {
    row.keys = stringList(summary.keys);
    row.size = stringValue(summary, "size");
  }

  if (kind === "Secret") {
    row.type = stringValue(summary, "type") ?? resource.status;
    row.keys = numberValue(summary, "key_count");
    row.exp = formatAge(stringValue(summary, "expires_at"), now);
  }

  if (kind === "Node") {
    const labels = record(summary.labels);
    const allocatable = record(summary.allocatable);
    const cpuUsed = numberValue(summary, "cpu_mcores");
    const cpuRatio = numberValue(summary, "cpu_ratio");
    const memUsed = numberValue(summary, "mem_mib");
    const memRatio = numberValue(summary, "mem_ratio");
    const podCount = numberValue(summary, "pod_count");
    const podLimit = Number(allocatable.pods);
    row.st = booleanValue(summary, "ready") === false ? "NotReady" : resource.status;
    row.inst = stringValue(labels, "node.kubernetes.io/instance-type")
      ?? stringValue(labels, "beta.kubernetes.io/instance-type");
    row.zone = stringValue(labels, "topology.kubernetes.io/zone");
    row.cpu = meter(cpuUsed, cpuRatio && cpuUsed !== undefined ? cpuUsed / cpuRatio : undefined, "m");
    row.mem = meter(memUsed, memRatio && memUsed !== undefined ? memUsed / memRatio : undefined, "Mi");
    row.pods = meter(podCount, Number.isFinite(podLimit) ? podLimit : undefined, "");
  }

  if (kind === "Event") {
    row.type = stringValue(summary, "type") ?? resource.status;
    row.reason = stringValue(summary, "reason");
    row.msg = stringValue(summary, "message");
    const involvedKind = stringValue(summary, "involved_kind");
    const involvedName = stringValue(summary, "involved_name");
    row.obj = involvedKind && involvedName ? `${involvedKind}/${involvedName}` : involvedName;
    row.cnt = numberValue(summary, "count");
  }

  return row;
}
