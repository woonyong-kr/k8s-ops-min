export interface ParsedKubernetesLabelSelector {
  key: string;
  value: string;
}

export function parseKubernetesLabelSelector(
  selector: string,
): ParsedKubernetesLabelSelector | null {
  const equals = selector.indexOf("=");
  if (equals <= 0) return null;
  const key = selector.slice(0, equals);
  const value = selector.slice(equals + 1);
  if (!isKubernetesLabelKey(key) || !isKubernetesLabelValue(value)) return null;
  return { key, value };
}

function isKubernetesLabelKey(key: string): boolean {
  if (key.includes(",") || key.includes("=")) return false;
  const slash = key.indexOf("/");
  if (slash !== key.lastIndexOf("/")) return false;
  if (slash < 0) return isKubernetesLabelName(key);
  const prefix = key.slice(0, slash);
  const name = key.slice(slash + 1);
  return isDnsSubdomain(prefix) && isKubernetesLabelName(name);
}

function isKubernetesLabelValue(value: string): boolean {
  return value.length === 0 || isKubernetesLabelName(value);
}

function isKubernetesLabelName(value: string): boolean {
  return value.length > 0 && value.length <= 63 &&
    /^[A-Za-z0-9](?:[-_.A-Za-z0-9]*[A-Za-z0-9])?$/.test(value);
}

function isDnsSubdomain(value: string): boolean {
  if (value.length === 0 || value.length > 253) return false;
  return value.split(".").every((part) =>
    part.length > 0 && part.length <= 63 &&
    /^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$/.test(part));
}
