import type { ResourceView } from "./filterContract";
import { readMultiValues, type StrictQuery } from "./filterUrlSyntax";

export function parseBooleanQuery(
  params: StrictQuery,
  key: string,
  invalid: string[],
  trueAliases: readonly string[] = [],
  falseAliases: readonly string[] = [],
): boolean {
  const value = readScalarValue(params, key, invalid);
  if (value === null || value === "false" || falseAliases.includes(value)) return false;
  if (value === "true" || trueAliases.includes(value)) return true;
  invalid.push(value);
  return false;
}

export function parseResourceView(
  params: StrictQuery,
  invalid: string[],
): ResourceView {
  const value = readScalarValue(params, "resources.view", invalid);
  if (value === null || value === "table") return "table";
  if (value === "graph") return "graph";
  invalid.push(value);
  return "table";
}

function readScalarValue(
  params: StrictQuery,
  key: string,
  invalid: string[],
): string | null {
  const [value, ...extra] = readMultiValues(params, key, invalid);
  invalid.push(...extra);
  return value ?? null;
}
