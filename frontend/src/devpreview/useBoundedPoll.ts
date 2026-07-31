import { useEffect, useRef } from "react";

// UI-PHASE2-METRICS-001 · 실시간 cadence · 공통 bounded-poll primitive.
// stale 허용 read를 visibility-gated bounded 폴링으로 전환하는 재사용 훅.
//
// 보장:
//  - hidden-tab 0 요청: 문서가 숨겨지면 대기 타이머를 취소하고 새 요청을 내지 않는다.
//  - in-flight dedupe/backpressure: 진행 중 요청이 있으면 새 폴링을 시작하지 않고,
//    다음 예약은 요청 완료(finally) 이후에만 한다 → 동시 중복 요청 0, 느린 백엔드에서
//    요청이 쌓이지 않는다(60Hz 방지, 무조건 20s 아님 — 호출자가 endpoint 비용에 맞춰 지정).
//  - AbortController: 스코프(scopeKey) 변경·언마운트 시 진행 중 요청을 abort → stale 스코프
//    응답이 새 스코프 상태를 덮지 않는다(scope stale overwrite 0).
//  - 복귀 즉시 재조회: 화면이 다시 보이면(visibilitychange) 진행 중이 아니면 즉시 1회.
//  - stale-while-refresh + 단일 commit: load는 성공/실패 각 1회만 콜백을 호출하고, 호출자는
//    직전 값을 유지하다 결과가 오면 교체한다(요청 중 DOM 유지 → CLS 0).
//
// load/onResult/onError는 매 렌더 새로 생성돼도 되도록 ref로 고정한다. 이펙트는
// scopeKey·intervalMs 변경 시에만 재구성된다. scopeKey === "" 이면 폴링을 하지 않는다.
export function useBoundedPoll<T>(options: {
  scopeKey: string;
  intervalMs: number;
  load: (signal: AbortSignal) => Promise<T>;
  onResult: (value: T) => void;
  onError?: (error: unknown) => void;
}): void {
  const loadRef = useRef(options.load);
  const onResultRef = useRef(options.onResult);
  const onErrorRef = useRef(options.onError);
  // 최신 콜백을 렌더 중이 아니라 커밋 후 이펙트에서 고정한다. run()은 이펙트/타이머에서만
  // ref를 읽으므로 렌더-시점 ref 쓰기(react-hooks/refs) 없이 항상 최신 콜백을 사용한다.
  useEffect(() => {
    loadRef.current = options.load;
    onResultRef.current = options.onResult;
    onErrorRef.current = options.onError;
  });

  const { scopeKey, intervalMs } = options;

  useEffect(() => {
    if (scopeKey === "" || !Number.isFinite(intervalMs) || intervalMs <= 0) return undefined;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    // 시도 세대(generation): 각 poll attempt 에 단조 증가 id 를 부여한다. 커밋(onResult/
    // onError)은 **자신이 최신 세대일 때만** 허용 — 타임아웃으로 버려진 이전 시도의
    // 늦은 성공/실패가 새 시도의 결과를 덮지 못한다(stale response 무시).
    let attemptSeq = 0;
    // 정착을 기다리는 최신 시도의 세대. null 이면 새 시도를 시작할 수 있다.
    // (행잉 시도는 타임아웃 시 소유권을 잃어 backpressure 를 영구 점유하지 못한다.)
    let pendingGeneration: number | null = null;
    // scope 당 quick-retry burst 상한(연속 실패 metric). 성공 시 리셋.
    let quickRetriesUsed = 0;
    // 스코프 단위 abort — 스코프가 유지되는 동안의 모든 폴링을 함께 취소한다.
    const scopeController = new AbortController();

    const clearTimer = () => {
      if (timer !== null) { clearTimeout(timer); timer = null; }
    };
    const schedule = () => {
      if (timer !== null || disposed || pendingGeneration !== null || document.hidden) return;
      timer = setTimeout(() => { timer = null; run(); }, intervalMs);
    };
    const scheduleQuickRetry = () => {
      // 실패 직후 1초 재시도(상한 QUICK_RETRY_LIMIT/scope). 늦은 200 은 그 시도의
      // 세대가 최신일 때만 상태를 채운다. 상한 초과 시 평시 캐던스로 후퇴.
      if (disposed || document.hidden) return;
      if (quickRetriesUsed >= QUICK_RETRY_LIMIT) { schedule(); return; }
      quickRetriesUsed += 1;
      clearTimer();
      timer = setTimeout(() => { timer = null; run(); }, QUICK_RETRY_MS);
    };
    const run = () => {
      if (disposed || document.hidden || scopeController.signal.aborted) return;
      if (pendingGeneration !== null) return; // 최신 시도가 아직 정착 대기(backpressure)
      const generation = ++attemptSeq;
      pendingGeneration = generation;
      const isCurrent = () =>
        !disposed && !scopeController.signal.aborted && generation === attemptSeq;
      // 시도 단위 abort: 행잉 요청은 타임아웃으로 절단하고 소유권을 회수한다.
      const attempt = new AbortController();
      const onScopeAbort = () => attempt.abort();
      scopeController.signal.addEventListener("abort", onScopeAbort);
      const attemptTimeout = setTimeout(() => {
        attempt.abort();
        // 정착하지 않는 promise 가 소유권을 영구 점유하지 못하게 즉시 회수 후 재시도.
        if (pendingGeneration === generation) pendingGeneration = null;
        if (isCurrent()) scheduleQuickRetry();
      }, ATTEMPT_TIMEOUT_MS);
      loadRef.current(attempt.signal)
        .then((value) => {
          if (!isCurrent()) return; // 이전 세대의 늦은 성공 — 무시(stale overwrite 금지)
          quickRetriesUsed = 0;
          onResultRef.current(value);
        })
        .catch((error: unknown) => {
          if (!isCurrent()) return; // 이전 세대의 늦은 실패 — 무시
          if (isAbortError(error)) { scheduleQuickRetry(); return; } // 시도 절단 → 즉시 재시도
          onErrorRef.current?.(error);
          scheduleQuickRetry();
        })
        .finally(() => {
          clearTimeout(attemptTimeout);
          scopeController.signal.removeEventListener("abort", onScopeAbort);
          // 소유권은 자기 세대가 아직 갖고 있을 때만 반납한다(타임아웃이 이미 회수했으면 no-op).
          if (pendingGeneration === generation) pendingGeneration = null;
          schedule(); // 다음 폴링은 정착 후에만 예약. quick-retry 가 잡았으면 no-op.
        });
    };
    const onVisibility = () => {
      if (document.hidden) { clearTimer(); return; }
      if (pendingGeneration === null) run(); // 복귀 즉시 1회
    };
    document.addEventListener("visibilitychange", onVisibility);
    run(); // 최초 로드

    return () => {
      disposed = true;
      clearTimer();
      document.removeEventListener("visibilitychange", onVisibility);
      scopeController.abort(); // scope 변경/언마운트 — 이전 응답은 isCurrent() 로 전부 무시된다
    };
  }, [scopeKey, intervalMs]);
}

// 시도 타임아웃/즉시 재시도 상수 — 첫 화면 실값 수렴(ellipsis 고착 방지).
const ATTEMPT_TIMEOUT_MS = 8_000;
const QUICK_RETRY_MS = 1_000;
const QUICK_RETRY_LIMIT = 3;

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}
