import { chromium } from "playwright";

const targetUrl = process.env.OPSIA_LIVE_PERF_URL
  ?? "http://127.0.0.1:5185/devpreview-unified.html";
const sampleDurationMs = Number(process.env.OPSIA_LIVE_SAMPLE_MS ?? 10_000);
const minimumFps = Number(process.env.OPSIA_MIN_FPS ?? 55);
const maximumP95FrameMs = Number(process.env.OPSIA_MAX_P95_FRAME_MS ?? 33.4);
const maximumLayoutShift = Number(process.env.OPSIA_MAX_LAYOUT_SHIFT ?? 0.1);

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1_440, height: 900 } });
const consoleErrors = [];
const failedResponses = [];
const topologyRequests = [];
let websocketCount = 0;
let websocketFrames = 0;
let activeWebsockets = 0;
let maximumActiveWebsockets = 0;

page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("pageerror", (error) => consoleErrors.push(error.message));
page.on("response", (response) => {
  const url = response.url();
  if (response.status() >= 500) failedResponses.push({ status: response.status(), url });
  if (new URL(url).pathname === "/api/topology") {
    topologyRequests.push({ at: performance.now(), status: response.status(), url });
  }
});
page.on("websocket", (socket) => {
  if (!new URL(socket.url()).pathname.endsWith("/api/live/browser")) return;
  websocketCount += 1;
  activeWebsockets += 1;
  maximumActiveWebsockets = Math.max(maximumActiveWebsockets, activeWebsockets);
  socket.on("framereceived", () => { websocketFrames += 1; });
  socket.on("close", () => { activeWebsockets = Math.max(0, activeWebsockets - 1); });
});

try {
  await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 120_000 });
  await page.getByRole("button", { name: "리소스 화면으로 이동" }).click();
  const clusterPicker = page.getByRole("combobox", { name: "클러스터 범위" });
  await clusterPicker.waitFor({ timeout: 120_000 });
  await page.waitForFunction(() => {
    const picker = document.querySelector('select[aria-label="클러스터 범위"]');
    return picker instanceof HTMLSelectElement && picker.options.length > 1;
  }, undefined, { timeout: 120_000 });
  const clusterId = await clusterPicker.locator("option").nth(1).getAttribute("value");
  if (!clusterId) throw new Error("실시간 검증에 사용할 클러스터를 찾지 못했습니다.");

  const topologyStart = topologyRequests.length;
  const websocketStart = websocketFrames;
  await clusterPicker.selectOption(clusterId);
  const frame = await sampleLiveFrameHealth(page, sampleDurationMs);
  const liveTopologyRequests = topologyRequests.slice(topologyStart);
  const liveWebsocketFrames = websocketFrames - websocketStart;
  // 5초 leading+trailing gate: 최초 조회를 포함해 10초 창에서 4회를 넘으면 bounded가 아니다.
  const maximumTopologyRequests = Math.ceil(sampleDurationMs / 5_000) + 2;

  const violations = [
    frame.averageFps < minimumFps
      ? `실시간 갱신 평균 ${frame.averageFps}fps < ${minimumFps}fps`
      : null,
    frame.p95FrameMs > maximumP95FrameMs
      ? `실시간 갱신 p95 프레임 ${frame.p95FrameMs}ms > ${maximumP95FrameMs}ms`
      : null,
    frame.longTasks > 0 ? `실시간 갱신 long task ${frame.longTasks}건` : null,
    frame.layoutShift > maximumLayoutShift
      ? `실시간 갱신 CLS ${frame.layoutShift} > ${maximumLayoutShift}`
      : null,
    websocketCount === 0 ? "canonical live WebSocket 연결 0건" : null,
    maximumActiveWebsockets > 1 ? `동시 live WebSocket ${maximumActiveWebsockets}개` : null,
    liveWebsocketFrames === 0 ? "관측 창의 live WebSocket frame 0건" : null,
    liveTopologyRequests.length > maximumTopologyRequests
      ? `topology REST ${liveTopologyRequests.length}회 > bounded 상한 ${maximumTopologyRequests}회`
      : null,
    consoleErrors.length > 0 ? `콘솔 오류 ${consoleErrors.length}건` : null,
    failedResponses.length > 0 ? `HTTP 5xx ${failedResponses.length}건` : null,
  ].filter(Boolean);

  console.log(JSON.stringify({
    targetUrl,
    clusterId,
    sampleDurationMs,
    budgets: { minimumFps, maximumP95FrameMs, maximumLayoutShift, maximumTopologyRequests },
    result: {
      averageFps: round(frame.averageFps),
      p95FrameMs: round(frame.p95FrameMs),
      longTasks: frame.longTasks,
      layoutShift: round(frame.layoutShift),
      mutationBatches: frame.mutationBatches,
      websocketCount,
      maximumActiveWebsockets,
      websocketFrames: liveWebsocketFrames,
      topologyRequests: liveTopologyRequests.length,
    },
    consoleErrors,
    failedResponses,
    pass: violations.length === 0,
    violations,
  }, null, 2));
  if (violations.length > 0) process.exitCode = 1;
} finally {
  await browser.close();
}

async function sampleLiveFrameHealth(targetPage, durationMs) {
  return targetPage.evaluate((sampleMs) => new Promise((resolve) => {
    const deltas = [];
    let firstFrameAt = 0;
    let lastFrameAt = 0;
    let longTasks = 0;
    let layoutShift = 0;
    let mutationBatches = 0;
    const performanceObservers = [];
    const mutationObserver = new MutationObserver(() => { mutationBatches += 1; });
    mutationObserver.observe(document.body, { attributes: true, childList: true, subtree: true, characterData: true });

    if (PerformanceObserver.supportedEntryTypes.includes("longtask")) {
      const observer = new PerformanceObserver((list) => { longTasks += list.getEntries().length; });
      observer.observe({ type: "longtask" });
      performanceObservers.push(observer);
    }
    if (PerformanceObserver.supportedEntryTypes.includes("layout-shift")) {
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (!("hadRecentInput" in entry) || !entry.hadRecentInput) layoutShift += entry.value;
        }
      });
      observer.observe({ type: "layout-shift" });
      performanceObservers.push(observer);
    }

    const finish = () => {
      mutationObserver.disconnect();
      performanceObservers.forEach((observer) => observer.disconnect());
      const sorted = [...deltas].sort((left, right) => left - right);
      const elapsed = Math.max(1, lastFrameAt - firstFrameAt);
      resolve({
        averageFps: deltas.length * 1_000 / elapsed,
        p95FrameMs: sorted[Math.max(0, Math.ceil(sorted.length * 0.95) - 1)] ?? 0,
        longTasks,
        layoutShift,
        mutationBatches,
      });
    };
    const paint = (now) => {
      if (firstFrameAt === 0) firstFrameAt = now;
      else deltas.push(now - lastFrameAt);
      lastFrameAt = now;
      if (now - firstFrameAt >= sampleMs) finish();
      else requestAnimationFrame(paint);
    };
    requestAnimationFrame(paint);
  }), durationMs);
}

function round(value) {
  return Math.round(value * 100) / 100;
}
