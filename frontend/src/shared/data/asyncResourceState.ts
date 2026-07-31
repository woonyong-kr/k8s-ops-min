export type AsyncResourceState<T, F> =
  | { phase: "idle"; data: null; failure: null }
  | { phase: "loading"; data: null; failure: null }
  | {
    phase: "ready";
    data: T;
    failure: null;
    refreshing: boolean;
    refreshFailure: F | null;
  }
  | { phase: "failed"; data: null; failure: F };

export const ASYNC_IDLE = { phase: "idle", data: null, failure: null } as const;
export const ASYNC_LOADING = { phase: "loading", data: null, failure: null } as const;

export function startAsyncResource<T, F>(
  state: AsyncResourceState<T, F>,
): AsyncResourceState<T, F> {
  if (state.phase !== "ready") return ASYNC_LOADING;
  return { ...state, refreshing: true, refreshFailure: null };
}

export function asyncResourceSuccess<T, F>(data: T): AsyncResourceState<T, F> {
  return {
    phase: "ready",
    data,
    failure: null,
    refreshing: false,
    refreshFailure: null,
  };
}

export function asyncResourceFailure<T, F>(
  current: AsyncResourceState<T, F>,
  failure: F,
): AsyncResourceState<T, F> {
  if (current.phase !== "ready") return { phase: "failed", data: null, failure };
  return { ...current, refreshing: false, refreshFailure: failure };
}

export function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error &&
    error.name === "AbortError";
}
