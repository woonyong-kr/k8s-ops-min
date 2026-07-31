export interface RcaIssuePinSubject {
  clusterId?: string | null;
  cluster?: string | null;
  namespace?: string | null;
  ns?: string | null;
  resourceKind?: string | null;
  resourceName?: string | null;
  svc?: string | null;
  rawSymptom?: string | null;
  symptom?: string | null;
  incidentId?: string | null;
  correlationId?: string | null;
}

export interface StoredRcaIssuePin {
  groupKey: string;
  correlationId: string;
  touchedAt: string;
}

const RCA_PIN_STORAGE_PREFIX = "opsia.devpreview.rcaIssuePins.v1";
const RCA_PIN_LIMIT = 50;

export function rcaIssuePinStorageKey(workspaceId: string | null | undefined): string {
  const workspaceKey = normalizeStoragePart(workspaceId) || "workspace";
  return `${RCA_PIN_STORAGE_PREFIX}:${workspaceKey}`;
}

export function rcaIssuePinGroupKey(subject: RcaIssuePinSubject): string {
  const resourceName = normalizeIdentityPart(subject.resourceName ?? subject.svc ?? null);
  const symptom = normalizeIdentityPart(subject.rawSymptom ?? subject.symptom ?? null);
  if (resourceName === "" || symptom === "") {
    return `event:${subject.incidentId ?? subject.correlationId ?? ""}`;
  }
  return [
    normalizeIdentityPart(subject.clusterId ?? subject.cluster ?? null),
    normalizeIdentityPart(subject.namespace ?? subject.ns ?? null),
    normalizeIdentityPart(subject.resourceKind ?? null),
    resourceName,
    symptom,
  ].join("\u0000");
}

export function readStoredRcaIssuePins(storageKey: string): StoredRcaIssuePin[] {
  const storage = browserStorage();
  if (storage === null) return [];
  try {
    return parseStoredRcaIssuePins(storage.getItem(storageKey));
  } catch {
    return [];
  }
}

export function writeStoredRcaIssuePins(storageKey: string, pins: readonly StoredRcaIssuePin[]): void {
  const storage = browserStorage();
  if (storage === null) return;
  try {
    storage.setItem(storageKey, JSON.stringify(pins.slice(0, RCA_PIN_LIMIT)));
  } catch {
    // localStorage is best-effort state for demo continuity.
  }
}

export function upsertStoredRcaIssuePin(
  pins: readonly StoredRcaIssuePin[],
  pin: StoredRcaIssuePin,
): StoredRcaIssuePin[] {
  const deduped = pins.filter((candidate) => (
    candidate.groupKey !== pin.groupKey
    && candidate.correlationId !== pin.correlationId
  ));
  return [pin, ...deduped]
    .sort((a, b) => Date.parse(b.touchedAt) - Date.parse(a.touchedAt))
    .slice(0, RCA_PIN_LIMIT);
}

function parseStoredRcaIssuePins(raw: string | null): StoredRcaIssuePin[] {
  if (raw === null) return [];
  const parsed: unknown = JSON.parse(raw);
  if (!Array.isArray(parsed)) return [];
  return parsed
    .map((value) => {
      if (value === null || typeof value !== "object") return null;
      const record = value as Record<string, unknown>;
      const groupKey = typeof record.groupKey === "string" ? record.groupKey : "";
      const correlationId = typeof record.correlationId === "string" ? record.correlationId.trim() : "";
      const touchedAt = typeof record.touchedAt === "string" ? record.touchedAt : "";
      if (!groupKey || !correlationId || Number.isNaN(Date.parse(touchedAt))) return null;
      return { groupKey, correlationId, touchedAt };
    })
    .filter((pin): pin is StoredRcaIssuePin => pin !== null)
    .slice(0, RCA_PIN_LIMIT);
}

function browserStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function normalizeStoragePart(value: string | null | undefined): string {
  return (value ?? "").trim().toLocaleLowerCase().replace(/[^\p{L}\p{N}_.:-]+/gu, "_");
}

function normalizeIdentityPart(value: string | null): string {
  return (value ?? "").trim().toLocaleLowerCase().replace(/[\s_-]+/gu, " ");
}
