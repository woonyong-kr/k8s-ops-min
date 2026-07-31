/**
 * Batches realtime records at most once per animation frame without changing
 * their transport order. Rendering preferences deliberately never influence
 * this semantic boundary: reduced-motion consumers receive the same records in
 * the same order and decide only how to paint them.
 */

export interface RafStreamPolicy {
  /** Server-negotiated upper bound; clients must not invent a faster budget. */
  maxFramesPerSecond: number;
  /** Hidden documents retain records and publish one coalesced batch on return. */
  hiddenTab: "coalesce";
}

export interface RafStreamCoalescerRuntime {
  cancelFrame(frame: number): void;
  clearTimer(timer: number): void;
  isVisible(): boolean;
  now(): number;
  requestFrame(callback: FrameRequestCallback): number;
  setTimer(callback: () => void, delayMs: number): number;
  subscribeVisibilityChange(listener: () => void): () => void;
}

/**
 * A public resume cursor is an authorization-bound opaque key, not a sequence.
 * `keyOf` must identify one replay frame (not merely a transport position when
 * multiple frame kinds can share it). The caller owns key extraction and
 * equality; this scheduler never parses, sorts, or otherwise infers an order.
 */
export interface RafStreamOpaqueCursor<T> {
  equals?: (left: string, right: string) => boolean;
  keyOf: (event: T) => string | null | undefined;
}

export interface RafStreamCoalescerOptions<T> {
  /** Strictly monotonic numeric stream position for protocols that expose one. */
  cursorOf?: (event: T) => number | null | undefined;
  /** Equality-only resume cursor for public opaque cursor protocols. */
  opaqueCursor?: RafStreamOpaqueCursor<T>;
  onFlush(events: readonly T[]): void;
  policy: RafStreamPolicy;
  /**
   * Exposed for diagnostics only. It must not affect scheduling or batch order;
   * visual consumers use it after receiving the batch.
   */
  reducedMotion?: boolean;
  runtime?: Partial<RafStreamCoalescerRuntime>;
  signal?: AbortSignal;
}

export type RafStreamEnqueueResult = "queued" | "duplicate" | "disposed";
export type RafStreamMotionPreference = "full" | "reduced";

export interface RafStreamCoalescer<T> {
  readonly motionPreference: RafStreamMotionPreference;
  discard(predicate: (event: T) => boolean): number;
  dispose(): void;
  enqueue(event: T): RafStreamEnqueueResult;
  /** Deliver pending durable records before a terminal protocol boundary. */
  flush(): void;
  isDisposed(): boolean;
  pendingCount(): number;
}

export class RafStreamCursorOrderError extends Error {
  constructor() {
    super("stream cursor regressed before a frame was flushed");
    this.name = "RafStreamCursorOrderError";
  }
}

const browserRuntime: RafStreamCoalescerRuntime = {
  cancelFrame(frame) {
    if (typeof cancelAnimationFrame === "function") {
      cancelAnimationFrame(frame);
      return;
    }
    clearTimeout(frame);
  },
  clearTimer(timer) {
    clearTimeout(timer);
  },
  isVisible() {
    return typeof document === "undefined" || document.visibilityState !== "hidden";
  },
  now: () => Date.now(),
  requestFrame(callback) {
    if (typeof requestAnimationFrame === "function") return requestAnimationFrame(callback);
    return setTimeout(() => callback(Date.now()), 16) as unknown as number;
  },
  setTimer(callback, delayMs) {
    return setTimeout(callback, delayMs) as unknown as number;
  },
  subscribeVisibilityChange(listener) {
    if (typeof document === "undefined") return () => undefined;
    document.addEventListener("visibilitychange", listener);
    return () => document.removeEventListener("visibilitychange", listener);
  },
};

/**
 * Returns a transport-neutral scheduler. Supplying `cursorOf` turns duplicate
 * replay suppression and monotonic in-frame ordering on. `opaqueCursor` keeps
 * server arrival order and only removes equal resume frames; opaque tokens are
 * never ordered client-side. Omitting both keeps FIFO/rAF batching.
 */
export function createRafStreamCoalescer<T>(
  options: RafStreamCoalescerOptions<T>,
): RafStreamCoalescer<T> {
  validatePolicy(options.policy);
  validateCursorConfiguration(options);
  const runtimeOverrides = options.runtime;
  const runtime: RafStreamCoalescerRuntime = {
    cancelFrame: (frame) => (runtimeOverrides?.cancelFrame ?? browserRuntime.cancelFrame)
      .call(runtimeOverrides, frame),
    clearTimer: (timer) => (runtimeOverrides?.clearTimer ?? browserRuntime.clearTimer)
      .call(runtimeOverrides, timer),
    isVisible: () => (runtimeOverrides?.isVisible ?? browserRuntime.isVisible)
      .call(runtimeOverrides),
    now: () => (runtimeOverrides?.now ?? browserRuntime.now).call(runtimeOverrides),
    requestFrame: (callback) => (runtimeOverrides?.requestFrame ?? browserRuntime.requestFrame)
      .call(runtimeOverrides, callback),
    setTimer: (callback, delayMs) => (runtimeOverrides?.setTimer ?? browserRuntime.setTimer)
      .call(runtimeOverrides, callback, delayMs),
    subscribeVisibilityChange: (listener) => (
      runtimeOverrides?.subscribeVisibilityChange ?? browserRuntime.subscribeVisibilityChange
    ).call(runtimeOverrides, listener),
  };
  const minimumFrameIntervalMs = 1_000 / options.policy.maxFramesPerSecond;
  const pending: T[] = [];
  const motionPreference: RafStreamMotionPreference = options.reducedMotion
    ? "reduced"
    : "full";
  let disposed = false;
  let frame: number | null = null;
  let timer: number | null = null;
  let lastFlushedAt = Number.NEGATIVE_INFINITY;
  let lastFlushedCursor: number | null = null;
  let lastQueuedCursor: number | null = null;
  let lastFlushedOpaqueKey: string | null = null;
  let queuedOpaqueKeys: string[] = [];

  const unsubscribeVisibility = runtime.subscribeVisibilityChange(() => {
    if (!runtime.isVisible()) {
      cancelPendingSchedule();
      return;
    }
    schedule();
  });
  const onAbort = () => dispose();
  options.signal?.addEventListener("abort", onAbort, { once: true });
  if (options.signal?.aborted) dispose();

  return {
    discard,
    motionPreference,
    dispose,
    enqueue,
    flush: flushNow,
    isDisposed: () => disposed,
    pendingCount: () => pending.length,
  };

  function enqueue(event: T): RafStreamEnqueueResult {
    if (disposed) return "disposed";
    const cursor = cursorFor(event);
    if (cursor !== null) {
      if (lastFlushedCursor !== null && cursor <= lastFlushedCursor) {
        return "duplicate";
      }
      if (lastQueuedCursor !== null) {
        if (cursor === lastQueuedCursor) return "duplicate";
        if (cursor < lastQueuedCursor) throw new RafStreamCursorOrderError();
      }
      lastQueuedCursor = cursor;
    }
    const opaqueKey = opaqueKeyFor(event);
    if (opaqueKey !== null) {
      if (
        lastFlushedOpaqueKey !== null
        && opaqueKeysEqual(lastFlushedOpaqueKey, opaqueKey)
      ) {
        return "duplicate";
      }
      if (queuedOpaqueKeys.some((queuedKey) => opaqueKeysEqual(queuedKey, opaqueKey))) {
        return "duplicate";
      }
      queuedOpaqueKeys.push(opaqueKey);
    }
    pending.push(event);
    schedule();
    return "queued";
  }

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    cancelPendingSchedule();
    pending.length = 0;
    options.signal?.removeEventListener("abort", onAbort);
    unsubscribeVisibility();
  }

  function flushNow(): void {
    if (disposed || pending.length === 0) return;
    cancelPendingSchedule();
    flush();
  }

  function discard(predicate: (event: T) => boolean): number {
    if (disposed) return 0;
    let discarded = 0;
    for (let index = pending.length - 1; index >= 0; index -= 1) {
      if (!predicate(pending[index])) continue;
      pending.splice(index, 1);
      discarded += 1;
    }
    if (discarded === 0) return 0;
    lastQueuedCursor = lastCursor(pending) ?? lastFlushedCursor;
    queuedOpaqueKeys = opaqueKeys(pending);
    if (pending.length === 0) cancelPendingSchedule();
    return discarded;
  }

  function schedule(): void {
    if (disposed || pending.length === 0 || !runtime.isVisible()) return;
    if (frame !== null || timer !== null) return;
    const delay = Math.max(0, lastFlushedAt + minimumFrameIntervalMs - runtime.now());
    if (delay > 0) {
      timer = runtime.setTimer(() => {
        timer = null;
        requestFrame();
      }, delay);
      return;
    }
    requestFrame();
  }

  function requestFrame(): void {
    if (disposed || pending.length === 0 || !runtime.isVisible() || frame !== null) return;
    frame = runtime.requestFrame(() => {
      frame = null;
      if (disposed || pending.length === 0 || !runtime.isVisible()) return;
      if (runtime.now() < lastFlushedAt + minimumFrameIntervalMs) {
        schedule();
        return;
      }
      flush();
    });
  }

  function flush(): void {
    const batch = pending.splice(0, pending.length);
    try {
      options.onFlush(batch);
    } catch (error) {
      pending.unshift(...batch);
      throw error;
    }
    lastFlushedAt = runtime.now();
    const cursor = lastCursor(batch);
    if (cursor !== null) lastFlushedCursor = cursor;
    const opaqueKey = lastOpaqueKey(batch);
    if (opaqueKey !== null) lastFlushedOpaqueKey = opaqueKey;
    lastQueuedCursor = lastCursor(pending) ?? lastFlushedCursor;
    queuedOpaqueKeys = opaqueKeys(pending);
    schedule();
  }

  function cancelPendingSchedule(): void {
    if (frame !== null) runtime.cancelFrame(frame);
    if (timer !== null) runtime.clearTimer(timer);
    frame = null;
    timer = null;
  }

  function cursorFor(event: T): number | null {
    const cursor = options.cursorOf?.(event);
    if (cursor === null || cursor === undefined) return null;
    if (!Number.isSafeInteger(cursor) || cursor < 0) {
      throw new TypeError("stream cursor must be a non-negative safe integer");
    }
    return cursor;
  }

  function lastCursor(events: readonly T[]): number | null {
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const cursor = cursorFor(events[index]);
      if (cursor !== null) return cursor;
    }
    return null;
  }

  function opaqueKeyFor(event: T): string | null {
    const key = options.opaqueCursor?.keyOf(event);
    return key === null || key === undefined ? null : key;
  }

  function opaqueKeysEqual(left: string, right: string): boolean {
    return options.opaqueCursor?.equals?.(left, right) ?? Object.is(left, right);
  }

  function opaqueKeys(events: readonly T[]): string[] {
    const keys: string[] = [];
    for (const event of events) {
      const key = opaqueKeyFor(event);
      if (key !== null) keys.push(key);
    }
    return keys;
  }

  function lastOpaqueKey(events: readonly T[]): string | null {
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const key = opaqueKeyFor(events[index]);
      if (key !== null) return key;
    }
    return null;
  }
}

function validatePolicy(policy: RafStreamPolicy): void {
  if (policy.hiddenTab !== "coalesce") {
    throw new TypeError("stream hidden-tab policy must be coalesce");
  }
  if (
    !Number.isSafeInteger(policy.maxFramesPerSecond)
    || policy.maxFramesPerSecond < 1
    || policy.maxFramesPerSecond > 60
  ) {
    throw new RangeError("stream maxFramesPerSecond must be an integer between 1 and 60");
  }
}

function validateCursorConfiguration<T>(options: RafStreamCoalescerOptions<T>): void {
  if (options.cursorOf !== undefined && options.opaqueCursor !== undefined) {
    throw new TypeError("stream cursor policy must be numeric or opaque, not both");
  }
}
