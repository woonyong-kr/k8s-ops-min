interface SharedRequest<T> {
  controller: AbortController;
  consumers: number;
  promise: Promise<T>;
  settled: boolean;
}

export interface AcquiredRequest<T> {
  promise: Promise<T>;
  release: () => void;
}

const requestRegistry = new WeakMap<object, Map<string, SharedRequest<unknown>>>();

/**
 * Shares one idempotent read across StrictMode consumers and aborts it only
 * after the final consumer releases the scope.
 */
export function acquireSharedRequest<T>(
  owner: object,
  key: string,
  load: (signal: AbortSignal) => Promise<T>,
): AcquiredRequest<T> {
  let ownerRequests = requestRegistry.get(owner);
  if (!ownerRequests) {
    ownerRequests = new Map();
    requestRegistry.set(owner, ownerRequests);
  }

  let request = ownerRequests.get(key) as SharedRequest<T> | undefined;
  if (!request) {
    const controller = new AbortController();
    request = {
      controller,
      consumers: 0,
      promise: load(controller.signal),
      settled: false,
    };
    ownerRequests.set(key, request as SharedRequest<unknown>);
    void request.promise.then(
      () => { request!.settled = true; },
      () => { request!.settled = true; },
    );
  }

  request.consumers += 1;
  let released = false;
  return {
    promise: request.promise,
    release() {
      if (released) return;
      released = true;
      request!.consumers -= 1;
      queueMicrotask(() => {
        if (request!.consumers !== 0) return;
        if (!request!.settled) request!.controller.abort();
        if (ownerRequests!.get(key) === request) ownerRequests!.delete(key);
        if (ownerRequests!.size === 0) requestRegistry.delete(owner);
      });
    },
  };
}
