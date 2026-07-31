import type {
  DetailMutationIntent,
  FilterHistoryMode,
  FilterMutationIntent,
} from "./filterContract";
import { isStableFilterValue } from "./filterUrlSyntax";
import {
  parseProductFilterUrl,
  serializeProductFilterUrl,
} from "./filterUrlCodec";

export {
  parseProductFilterUrl,
  serializeProductFilterUrl,
} from "./filterUrlCodec";

export function canonicalizeProductFilterUrl(search: string): string {
  const parsed = parseProductFilterUrl(search);
  return serializeProductFilterUrl(parsed.state, parsed.detail);
}

export function legacyResourceTypeFromPath(pathname: string): string | null {
  const prefix = "/resources/";
  if (!pathname.startsWith(prefix)) return null;
  const encoded = pathname.slice(prefix.length);
  if (encoded.length === 0 || encoded.includes("/")) return null;
  try {
    const value = decodeURIComponent(encoded);
    return value.length <= 80 && !value.includes("/") && isStableFilterValue(value)
      ? value
      : null;
  } catch {
    return null;
  }
}

export function productFilterNavigationHref(
  path: `/${string}`,
  currentSearch: string,
): string {
  const parsed = parseProductFilterUrl(currentSearch);
  return `${path}${serializeProductFilterUrl(parsed.state)}`;
}

export function filterHistoryMode(intent: FilterMutationIntent): FilterHistoryMode {
  switch (intent) {
    case "chip-add":
    case "chip-remove":
    case "clear-labels":
    case "clear-filters":
    case "view-change":
      return "push";
    case "canonicalize":
    case "legacy-migration":
    case "typing":
      return "replace";
  }
}

export function detailHistoryMode(intent: DetailMutationIntent): FilterHistoryMode {
  switch (intent) {
    case "detail-open":
    case "drill-in":
    case "detail-instance":
    case "detail-workload":
      return "push";
    case "detail-close":
    case "detail-expand":
    case "detail-tab":
    case "detail-instance-default":
    case "detail-workload-default":
    case "detail-workload-recovery":
    case "topology-view-reset":
      return "replace";
    case "topology-view":
    case "time-range":
    case "graph-visibility":
      return "push";
    case "time-at":
      return "replace";
  }
}
