import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useRef,
  type ReactNode,
} from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type {
  DetailMutationIntent,
  FilterMutationIntent,
  ProductDetailQuery,
  ProductDetailUpdater,
  UnifiedFilterController,
  UnifiedFilterUpdater,
} from "./filterContract";
import {
  detailHistoryMode,
  filterHistoryMode,
  legacyResourceTypeFromPath,
  parseProductFilterUrl,
  serializeProductFilterUrl,
} from "./filterUrl";

const UnifiedFilterContext = createContext<UnifiedFilterController | null>(null);

export function UnifiedFilterProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const parsed = useMemo(() => {
    const result = parseProductFilterUrl(location.search);
    const legacyType = result.state.resources.types.length === 0
      ? legacyResourceTypeFromPath(location.pathname)
      : null;
    if (legacyType === null) return result;
    return {
      ...result,
      state: {
        ...result.state,
        resources: { ...result.state.resources, types: [legacyType] },
      },
    };
  }, [location.pathname, location.search]);
  const latest = useRef({ detail: parsed.detail, state: parsed.state });
  useLayoutEffect(() => {
    latest.current = { detail: parsed.detail, state: parsed.state };
  }, [parsed.detail, parsed.state]);

  const canonicalize = useCallback(() => {
    const search = serializeProductFilterUrl(parsed.state, parsed.detail);
    if (search === location.search) return;
    navigate({
      hash: location.hash,
      pathname: location.pathname,
      search,
    }, { replace: true });
  }, [location.hash, location.pathname, location.search, navigate, parsed]);

  const navigationHref = useCallback((
    path: `/${string}`,
    detail?: ProductDetailQuery,
  ) => (
    `${path}${serializeProductFilterUrl(latest.current.state, detail)}`
  ), []);

  const updateFilters = useCallback((
    update: UnifiedFilterUpdater,
    intent: FilterMutationIntent,
  ) => {
    const current = latest.current;
    const currentSearch = serializeProductFilterUrl(current.state, current.detail);
    const next = typeof update === "function" ? update(current.state) : update;
    const search = serializeProductFilterUrl(next, current.detail);
    if (search === currentSearch) return;
    latest.current = { detail: current.detail, state: next };
    navigate({
      hash: location.hash,
      pathname: location.pathname,
      search,
    }, { replace: filterHistoryMode(intent) === "replace" });
  }, [location.hash, location.pathname, navigate]);

  const updateDetail = useCallback((
    update: ProductDetailUpdater,
    intent: DetailMutationIntent,
  ) => {
    const current = latest.current;
    const currentSearch = serializeProductFilterUrl(current.state, current.detail);
    const next = typeof update === "function" ? update(current.detail) : update;
    const search = serializeProductFilterUrl(current.state, next);
    if (search === currentSearch) return;
    latest.current = { detail: next, state: current.state };
    navigate({
      hash: location.hash,
      pathname: location.pathname,
      search,
    }, { replace: detailHistoryMode(intent) === "replace" });
  }, [location.hash, location.pathname, navigate]);

  const value = useMemo<UnifiedFilterController>(() => ({
    ...parsed,
    canonicalize,
    navigationHref,
    updateDetail,
    updateFilters,
  }), [canonicalize, navigationHref, parsed, updateDetail, updateFilters]);

  return <UnifiedFilterContext.Provider value={value}>{children}</UnifiedFilterContext.Provider>;
}

export function useUnifiedFilter(): UnifiedFilterController {
  const filter = useContext(UnifiedFilterContext);
  if (!filter) {
    throw new Error("useUnifiedFilter must be used within UnifiedFilterProvider");
  }
  return filter;
}
