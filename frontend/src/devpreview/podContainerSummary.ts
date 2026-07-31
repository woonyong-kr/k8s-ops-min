export type PodContainerPortProtocol = "TCP" | "UDP" | "SCTP";

export interface PodContainerPortView {
  port: number;
  name: string | null;
  protocol: PodContainerPortProtocol;
}

export interface PodContainerView {
  name: string;
  image: string | null;
  ports: PodContainerPortView[];
}

export interface PodContainerSummaryView {
  containers: PodContainerView[];
  containerPortsComplete: boolean | null;
}

export const DEFAULT_POD_CONTAINER_PORT_PROTOCOL: PodContainerPortProtocol = "TCP";

const KUBERNETES_CONTAINER_PORT_MIN = 1;
const KUBERNETES_CONTAINER_PORT_MAX = 65_535;
const FIELD_KEY_SEPARATOR = "\u0000";
const POD_CONTAINER_FIELD = {
  containers: "containers",
  image: "image",
  name: "name",
  ports: "ports",
  portsComplete: "container_ports_complete",
} as const;
const POD_CONTAINER_PORT_FIELD = {
  name: "name",
  port: "container_port",
  protocol: "protocol",
} as const;
const CONTAINER_PORT_PROTOCOLS = new Set<PodContainerPortProtocol>([
  DEFAULT_POD_CONTAINER_PORT_PROTOCOL,
  "UDP",
  "SCTP",
]);

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}

function portNumber(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isInteger(value) || Object.is(value, -0)) return null;
  return value >= KUBERNETES_CONTAINER_PORT_MIN && value <= KUBERNETES_CONTAINER_PORT_MAX ? value : null;
}

function protocol(value: unknown): PodContainerPortProtocol | null {
  if (value == null) return DEFAULT_POD_CONTAINER_PORT_PROTOCOL;
  if (typeof value !== "string") return null;
  const normalized = text(value);
  if (normalized == null) return null;
  const upper = normalized.toUpperCase();
  return CONTAINER_PORT_PROTOCOLS.has(upper as PodContainerPortProtocol)
    ? upper as PodContainerPortProtocol
    : null;
}

function parsePorts(value: unknown): PodContainerPortView[] {
  if (!Array.isArray(value)) return [];
  const ports: PodContainerPortView[] = [];
  const seen = new Set<string>();
  for (const item of value) {
    if (typeof item !== "object" || item === null) continue;
    const record = item as Record<string, unknown>;
    const port = portNumber(record[POD_CONTAINER_PORT_FIELD.port]);
    const portProtocol = protocol(record[POD_CONTAINER_PORT_FIELD.protocol]);
    if (port == null || portProtocol == null) continue;
    const name = text(record[POD_CONTAINER_PORT_FIELD.name]);
    const key = `${portProtocol}${FIELD_KEY_SEPARATOR}${name ?? ""}${FIELD_KEY_SEPARATOR}${port}`;
    if (seen.has(key)) continue;
    seen.add(key);
    ports.push({ port, name, protocol: portProtocol });
  }
  return ports;
}

export function podContainerSummary(summary: Record<string, unknown>): PodContainerSummaryView {
  const containers: PodContainerView[] = [];
  const rawContainers = summary[POD_CONTAINER_FIELD.containers];
  const rawPortsComplete = summary[POD_CONTAINER_FIELD.portsComplete];
  if (Array.isArray(rawContainers)) {
    const seen = new Set<string>();
    for (const item of rawContainers) {
      if (typeof item !== "object" || item === null) continue;
      const record = item as Record<string, unknown>;
      const name = text(record[POD_CONTAINER_FIELD.name]);
      if (name == null || seen.has(name)) continue;
      seen.add(name);
      containers.push({
        name,
        image: text(record[POD_CONTAINER_FIELD.image]),
        ports: parsePorts(record[POD_CONTAINER_FIELD.ports]),
      });
    }
  }

  return {
    containers,
    containerPortsComplete: typeof rawPortsComplete === "boolean"
      ? rawPortsComplete
      : null,
  };
}
