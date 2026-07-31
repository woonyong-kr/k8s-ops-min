import { useEffect, useMemo, useState } from "react";

import { connectRealtime, type RealtimeClient, type RealtimeClientOptions } from "../api/live";
import { useDevpreviewContracts } from "./contracts";
import {
  liveSummarySchema,
  type LiveSubscription,
  type LiveSummary,
  type RealtimeConnectionStatus,
  type RealtimeMessage,
} from "../api/live-schemas";
import {
  createRafStreamCoalescer,
  type RafStreamCoalescer,
  type RafStreamCoalescerRuntime,
} from "../shared/streaming/rafStreamCoalescer";

// UI-PHASE2-METRICS-001 · CADENCE-STREAM-002 · canonical WS(/api/live/browser) +
// rafStreamCoalescer 재사용 store. devpreview 활성 셸이 60Hz REST·가짜 데이터 없이
// 실시간 델타를 받도록 최소 공통 어댑터를 제공한다.
//
// 보장:
//  - burst → animation frame당 최대 1 visible commit(rafStreamCoalescer.onFlush).
//  - 순서·dedupe: seq를 cursorOf로 사용(transport도 reduceRealtimeSequence로 1차 보증).
//  - snapshot+stream gap 없음: hello→snapshot(baseline)→delta. snapshot 도착 시 pending을
//    비우고 baseline을 재적용(stale-while-revalidate, 이전 표시는 유지되다 baseline로 교체).
//  - hidden/background backpressure: 화면이 숨겨지면 WS를 닫아 네트워크 요청 0, 복귀 시
//    재연결(hello/snapshot으로 resync). 렌더측 coalesce는 coalescer(hiddenTab:"coalesce")가 담당.
//  - terminal/resync: 연결 종료·스코프 종료 전 coalescer.flush()로 durable 델타를 게시.
//
// 순수 도메인 store다(React 아님). 콜백으로 snapshot/batch/status를 노출하며, connect·
// runtime을 주입 가능하게 해 테스트가 실제 WS·rAF 없이 burst/order/dedupe/terminal을 검증한다.

// api 경계 재노출 — 이 스트림 어댑터의 소비자·테스트가 api/*를 직접 import 하지 않도록
// (apiBoundary는 devpreview/*Feed.ts에서만 api 참조를 허용한다).
export type { RealtimeMessage } from "../api/live-schemas";
export type { RealtimeClient, RealtimeClientOptions, RealtimeConnectionState } from "../api/live";

export type LiveDeltaMessage = Extract<
  RealtimeMessage,
  { type: "live.summary" | "resource.delta" }
>;

export interface LiveStreamConfig {
  subscription: LiveSubscription;
  /** snapshot(baseline) 도착 — 이전 델타 누적을 대체한다. */
  onSnapshot: (state: Record<string, unknown>, seq: number) => void;
  /** animation frame당 최대 1회, 순서 보존된 델타 배치(단일 visible commit). */
  onBatch: (deltas: readonly LiveDeltaMessage[]) => void;
  onStatus?: (status: RealtimeConnectionStatus) => void;
  /** 테스트 주입점: 기본은 실제 WS(connectRealtime). */
  connect?: (options: RealtimeClientOptions) => RealtimeClient;
  /** 테스트 주입점: 기본은 브라우저 rAF/visibility. */
  runtime?: Partial<RafStreamCoalescerRuntime>;
  /** 테스트 주입점: 기본은 document.visibilityState. */
  isHidden?: () => boolean;
  /** 테스트 주입점: 기본은 document visibilitychange. */
  subscribeVisibility?: (listener: () => void) => () => void;
}

export interface LiveStreamHandle {
  start: () => void;
  stop: () => void;
}

function defaultIsHidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

function defaultSubscribeVisibility(listener: () => void): () => void {
  if (typeof document === "undefined") return () => undefined;
  document.addEventListener("visibilitychange", listener);
  return () => document.removeEventListener("visibilitychange", listener);
}

export function createLiveStreamCoalescer(config: LiveStreamConfig): LiveStreamHandle {
  const connect = config.connect ?? connectRealtime;
  const isHidden = config.isHidden ?? defaultIsHidden;
  const subscribeVisibility = config.subscribeVisibility ?? defaultSubscribeVisibility;

  let client: RealtimeClient | null = null;
  let coalescer: RafStreamCoalescer<LiveDeltaMessage> | null = null;
  let disposed = false;
  let unsubscribeVisibility: (() => void) | null = null;

  const discardCoalescer = () => {
    coalescer?.dispose();
    coalescer = null;
  };

  const handleMessage = (message: RealtimeMessage) => {
    if (disposed) return;
    switch (message.type) {
      case "hello": {
        // 서버가 협상한 정책으로 coalescer를 (재)구성한다 — 클라이언트가 더 빠른 예산을 지어내지 않는다.
        discardCoalescer();
        const policy = message.stream_policy;
        coalescer = createRafStreamCoalescer<LiveDeltaMessage>({
          onFlush: (events) => { if (!disposed) config.onBatch(events); },
          policy: { maxFramesPerSecond: policy.max_frames_per_second, hiddenTab: "coalesce" },
          cursorOf: (event) => event.seq, // seq = strictly monotonic → dedupe + in-frame 순서
          runtime: config.runtime,
        });
        return;
      }
      case "snapshot": {
        // 새 baseline — baseline이 pending 델타를 대체하므로 pending만 비우고 coalescer는
        // 유지한다(이후 델타를 계속 받는다). gap 없이 resync(stale-while-revalidate).
        coalescer?.discard(() => true);
        config.onSnapshot(message.state, message.seq);
        return;
      }
      case "live.summary":
      case "resource.delta": {
        // 델타는 rAF coalescer로 배치(≤1 commit/frame). hello 전이면 무시(순서상 도착 안 함).
        coalescer?.enqueue(message);
        return;
      }
      case "ping":
        return;
    }
  };

  const openClient = () => {
    if (disposed || client !== null || isHidden()) return;
    client = connect({
      subscription: config.subscription,
      onMessage: handleMessage,
      onStateChange: (state) => { if (!disposed) config.onStatus?.(state.status); },
    });
  };

  const closeClient = () => {
    // 종료 전 durable 델타를 게시(terminal flush).
    coalescer?.flush();
    const active = client;
    client = null;
    active?.close();
  };

  const onVisibilityChange = () => {
    if (disposed) return;
    if (isHidden()) {
      // 백그라운드 backpressure: WS를 닫아 네트워크 요청 0.
      closeClient();
      discardCoalescer();
    } else {
      // 복귀: 재연결 → hello/snapshot으로 resync.
      openClient();
    }
  };

  return {
    start() {
      if (disposed || unsubscribeVisibility !== null) return;
      unsubscribeVisibility = subscribeVisibility(onVisibilityChange);
      openClient();
    },
    stop() {
      unsubscribeVisibility?.();
      unsubscribeVisibility = null;
      // disposed=true 이전에 closeClient()가 coalescer.flush()로 pending 델타를 게시하게 한다
      // (terminal flush). 이후 dispose하고 disposed 플래그를 세운다.
      closeClient();
      discardCoalescer();
      disposed = true;
    },
  };
}

export interface LiveStreamViewState {
  /** Last transport state. Observed values remain present across reconnects. */
  status: RealtimeConnectionStatus;
  /** True after an authoritative snapshot or admitted live frame has arrived. */
  observed: boolean;
  /** Transport freshness only; it never replaces or clears last-known-good data. */
  stale: boolean;
  resources: Readonly<Record<string, unknown>>;
  summaries: Readonly<Record<string, LiveSummary>>;
  updatedAt: number;
}

const EMPTY_LIVE_STREAM_VIEW: LiveStreamViewState = {
  status: "idle",
  observed: false,
  stale: false,
  resources: {},
  summaries: {},
  updatedAt: 0,
};

type LiveViewListener = (state: LiveStreamViewState) => void;

interface SharedLiveViewChannel {
  state: LiveStreamViewState;
  listeners: Set<LiveViewListener>;
  handle: LiveStreamHandle;
  disposeTimer: ReturnType<typeof setTimeout> | null;
}

const sharedLiveViewChannels = new Map<string, SharedLiveViewChannel>();

/**
 * One retained read model per canonical live subscription. Dashboard and
 * topology consumers therefore share one socket and receive one immutable
 * commit per admitted animation frame. A disconnect changes freshness only:
 * the last observed resources and summaries stay available as the immediate
 * shell until the reconnect snapshot replaces them.
 */
export function useLiveStreamView(
  subscription: LiveSubscription | null,
): LiveStreamViewState {
  const workspaceId = subscription?.workspaceId ?? null;
  const clusterId = subscription?.clusterId;
  const namespace = subscription?.namespace;
  const app = subscription?.app;
  const stableSubscription = useMemo<LiveSubscription | null>(() => {
    if (workspaceId === null) return null;
    return {
      workspaceId,
      ...(clusterId === undefined ? {} : { clusterId }),
      ...(namespace === undefined ? {} : { namespace }),
      ...(app === undefined ? {} : { app }),
    };
  }, [app, clusterId, namespace, workspaceId]);
  const subscriptionKey = liveSubscriptionKey(stableSubscription);
  const [entry, setEntry] = useState<{
    key: string;
    state: LiveStreamViewState;
  }>({ key: "", state: EMPTY_LIVE_STREAM_VIEW });

  useEffect(() => {
    if (stableSubscription === null) return undefined;
    return subscribeLiveStreamView(stableSubscription, (state) => {
      setEntry({ key: subscriptionKey, state });
    });
  }, [stableSubscription, subscriptionKey]);

  return entry.key === subscriptionKey ? entry.state : EMPTY_LIVE_STREAM_VIEW;
}

function subscribeLiveStreamView(
  subscription: LiveSubscription,
  listener: LiveViewListener,
): () => void {
  const channelKey = liveSubscriptionKey(subscription);
  let channel = sharedLiveViewChannels.get(channelKey);
  if (channel === undefined) {
    const created = {} as SharedLiveViewChannel;
    const publish = (next: LiveStreamViewState) => {
      created.state = next;
      created.listeners.forEach((notify) => notify(next));
    };
    const handle = createLiveStreamCoalescer({
      subscription,
      onSnapshot: (snapshot) => publish(reduceLiveSnapshot(created.state, snapshot)),
      onBatch: (messages) => publish(reduceLiveBatch(created.state, messages)),
      onStatus: (status) => {
        if (status === created.state.status) return;
        publish({
          ...created.state,
          status,
          stale: liveStateIsStale(status, created.state.observed),
        });
      },
    });
    Object.assign(created, {
      state: { ...EMPTY_LIVE_STREAM_VIEW, status: "connecting" },
      listeners: new Set<LiveViewListener>(),
      handle,
      disposeTimer: null,
    });
    channel = created;
    sharedLiveViewChannels.set(channelKey, channel);
  }

  if (channel.disposeTimer !== null) {
    clearTimeout(channel.disposeTimer);
    channel.disposeTimer = null;
  }
  channel.listeners.add(listener);
  listener(channel.state);
  if (channel.listeners.size === 1 && channel.state.status === "connecting") {
    channel.handle.start();
  }

  const active = channel;
  return () => {
    active.listeners.delete(listener);
    if (active.listeners.size > 0 || active.disposeTimer !== null) return;
    active.disposeTimer = setTimeout(() => {
      active.disposeTimer = null;
      if (
        active.listeners.size > 0
        || sharedLiveViewChannels.get(channelKey) !== active
      ) return;
      active.handle.stop();
      sharedLiveViewChannels.delete(channelKey);
    }, LIVE_STRICT_MODE_GRACE_MS);
  };
}

function reduceLiveSnapshot(
  current: LiveStreamViewState,
  state: Record<string, unknown>,
): LiveStreamViewState {
  const resources = openRecord(state.resources);
  const clusters = openRecord(state.clusters);
  const summaries: Record<string, LiveSummary> = {};
  for (const [clusterId, value] of Object.entries(clusters)) {
    const parsed = liveSummarySchema.safeParse(value);
    if (parsed.success) summaries[clusterId] = parsed.data;
  }
  return {
    ...current,
    observed: true,
    stale: liveStateIsStale(current.status, true),
    resources: { ...resources },
    summaries,
    updatedAt: Date.now(),
  };
}

function reduceLiveBatch(
  current: LiveStreamViewState,
  messages: readonly LiveDeltaMessage[],
): LiveStreamViewState {
  if (messages.length === 0) return current;
  let resources: Record<string, unknown> | null = null;
  let summaries: Record<string, LiveSummary> | null = null;
  for (const message of messages) {
    if (message.type === "live.summary") {
      summaries ??= { ...current.summaries };
      summaries[message.cluster_id] = message.summary;
      continue;
    }
    resources ??= { ...current.resources };
    if (message.op === "remove") delete resources[message.key];
    else resources[message.key] = message.value;
  }
  return {
    ...current,
    observed: true,
    stale: liveStateIsStale(current.status, true),
    resources: resources ?? current.resources,
    summaries: summaries ?? current.summaries,
    updatedAt: Date.now(),
  };
}

function liveSubscriptionKey(subscription: LiveSubscription | null): string {
  if (subscription === null) return "";
  return [
    subscription.workspaceId,
    subscription.clusterId ?? "",
    subscription.namespace ?? "",
    subscription.app ?? "",
  ].join("\u0000");
}

function openRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function liveStateIsStale(
  status: RealtimeConnectionStatus,
  observed: boolean,
): boolean {
  if (observed) return status !== "connected";
  return status === "reconnecting" || status === "disconnected" || status === "closed";
}

/** @internal Test isolation for the shared retained live channels. */
export function resetLiveStreamViewChannelsForTests(): void {
  sharedLiveViewChannels.forEach((channel) => {
    if (channel.disposeTimer !== null) clearTimeout(channel.disposeTimer);
    channel.handle.stop();
  });
  sharedLiveViewChannels.clear();
}

// 실시간 delta → REST 재검증 사이의 bounded gate 기본 최소 간격(cluster당).
const DEFAULT_REVALIDATION_MIN_MS = 5_000;
const LIVE_STRICT_MODE_GRACE_MS = 50;

export interface BoundedRevalidatorRuntime {
  now: () => number;
  setTimer: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>;
  clearTimer: (timer: ReturnType<typeof setTimeout>) => void;
  /** 화면이 숨겨졌는지 — hidden 동안에는 재검증(→REST)을 발생시키지 않는다(hidden 0 보장). */
  isHidden: () => boolean;
}

const defaultRevalidatorRuntime: BoundedRevalidatorRuntime = {
  now: () => Date.now(),
  setTimer: (callback, delayMs) => setTimeout(callback, delayMs),
  clearTimer: (timer) => clearTimeout(timer),
  isHidden: () => typeof document !== "undefined" && document.visibilityState === "hidden",
};

/**
 * leading+trailing throttle. 아무리 자주 request() 해도 emit()은 **minIntervalMs에 최대 1회**
 * 발생한다(첫 신호 즉시 leading, 폭주는 하나의 trailing으로 합침). 이것이 실시간 delta 폭주와
 * REST 재조회 사이의 상한 gate다 — 화면 프레임 cadence와 데이터 재검증 cadence를 분리한다.
 */
export function createBoundedRevalidator(
  minIntervalMs: number,
  emit: () => void,
  runtime: BoundedRevalidatorRuntime = defaultRevalidatorRuntime,
): { request: () => void; dispose: () => void } {
  let lastEmitAt = Number.NEGATIVE_INFINITY;
  let trailingTimer: ReturnType<typeof setTimeout> | null = null;
  return {
    request() {
      // hidden 동안에는 재검증(→REST 재조회)을 전혀 발생시키지 않는다(hidden-tab 0 보장).
      if (runtime.isHidden()) return;
      const now = runtime.now();
      const elapsed = now - lastEmitAt;
      if (elapsed >= minIntervalMs) {
        lastEmitAt = now;
        emit();
        return;
      }
      if (trailingTimer === null) {
        trailingTimer = runtime.setTimer(() => {
          trailingTimer = null;
          lastEmitAt = runtime.now();
          // trailing 발화 시에도 화면이 숨겨졌으면 emit하지 않는다.
          if (!runtime.isHidden()) emit();
        }, minIntervalMs - elapsed);
      }
    },
    dispose() {
      if (trailingTimer !== null) { runtime.clearTimer(trailingTimer); trailingTimer = null; }
    },
  };
}

/**
 * 활성 devpreview 셸용 **단일 cluster** 실시간 재검증 gate. `{workspaceId, clusterId}`로
 * canonical WS(/api/live/browser)를 열고, snapshot/coalesced delta를 **bounded revalidation
 * key**로 변환한다. 화면 60fps(transport)와 데이터 재검증 cadence를 분리한다:
 *
 *  - 전송 cadence: WS delta는 coalescer가 프레임당 ≤1 배치로 합친다(store).
 *  - 재검증 cadence: leading+trailing throttle로 **cluster당 최소 minIntervalMs에 1회**만 key를
 *    올린다 → feed의 REST 재조회 상한이 증명된다(1000-frame burst여도 GET은 bounded).
 *  - snapshot은 즉시 1회(leading) 허용.
 *  - WS 미가용/미구독(clusterId null)이면 key는 0으로 유지 → feed는 bounded-poll fallback(가짜 데이터 없음).
 *
 * cluster 구독이므로 delta의 relevance가 내재된다(그 WS의 delta는 그 cluster만 stale로 만든다).
 * unmount·scope 변경 시 start/stop lifecycle로 정리되고 background/reconnect/resync는 store가 처리.
 */
export function useLiveClusterRevalidation(
  clusterId: string | null,
  minIntervalMs: number = DEFAULT_REVALIDATION_MIN_MS,
  revalidateOnSnapshot: boolean = true,
): number {
  const { workspaceId } = useDevpreviewContracts();
  const [key, setKey] = useState(0);
  const workspaceKey = workspaceId ?? "";
  const clusterKey = clusterId ?? "";
  useEffect(() => {
    if (workspaceKey === "" || clusterKey === "") return undefined;
    return subscribeLiveClusterRevalidation(
      workspaceKey,
      clusterKey,
      minIntervalMs,
      revalidateOnSnapshot,
      setKey,
    );
  }, [workspaceKey, clusterKey, minIntervalMs, revalidateOnSnapshot]);
  return key;
}

interface SharedLiveRevalidationChannel {
  key: number;
  listeners: Set<(key: number) => void>;
  handle: LiveStreamHandle;
  revalidator: { request: () => void; dispose: () => void };
  disposeTimer: ReturnType<typeof setTimeout> | null;
}

const liveRevalidationChannels = new Map<string, SharedLiveRevalidationChannel>();

/**
 * React StrictMode의 effect 재마운트에서도 같은 workspace/cluster 소켓을 공유한다.
 * 마지막 구독자가 사라진 뒤 50ms 동안 채널을 보존해 개발 모드의 open→close→open 중복을
 * 없애고, 실제 화면 전환에서는 유예 뒤 소켓과 throttle timer를 모두 정리한다.
 */
function subscribeLiveClusterRevalidation(
  workspaceId: string,
  clusterId: string,
  minIntervalMs: number,
  revalidateOnSnapshot: boolean,
  listener: (key: number) => void,
): () => void {
  const channelKey = `${workspaceId}\u0000${clusterId}\u0000${minIntervalMs}\u0000${revalidateOnSnapshot ? "snapshot" : "delta"}`;
  let channel = liveRevalidationChannels.get(channelKey);
  if (channel === undefined) {
    const listeners = new Set<(key: number) => void>();
    const created = {} as SharedLiveRevalidationChannel;
    const revalidator = createBoundedRevalidator(minIntervalMs, () => {
      created.key += 1;
      created.listeners.forEach((notify) => notify(created.key));
    });
    const handle = createLiveStreamCoalescer({
      subscription: { workspaceId, clusterId },
      // Some consumers already issue a canonical initial snapshot request on
      // mount. They can ignore the WS baseline and reserve REST revalidation
      // for subsequent deltas, avoiding a guaranteed duplicate cold read.
      onSnapshot: () => { if (revalidateOnSnapshot) revalidator.request(); },
      onBatch: () => revalidator.request(),
    });
    Object.assign(created, {
      key: 0,
      listeners,
      handle,
      revalidator,
      disposeTimer: null,
    });
    channel = created;
    liveRevalidationChannels.set(channelKey, channel);
    handle.start();
  }

  if (channel.disposeTimer !== null) {
    clearTimeout(channel.disposeTimer);
    channel.disposeTimer = null;
  }
  channel.listeners.add(listener);
  if (channel.key > 0) listener(channel.key);

  const active = channel;
  return () => {
    active.listeners.delete(listener);
    if (active.listeners.size > 0 || active.disposeTimer !== null) return;
    active.disposeTimer = setTimeout(() => {
      active.disposeTimer = null;
      if (active.listeners.size > 0 || liveRevalidationChannels.get(channelKey) !== active) return;
      active.handle.stop();
      active.revalidator.dispose();
      liveRevalidationChannels.delete(channelKey);
    }, LIVE_STRICT_MODE_GRACE_MS);
  };
}
