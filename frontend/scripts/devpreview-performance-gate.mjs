import { chromium } from "playwright";

const targetUrl = process.env.OPSIA_PERF_URL
  ?? "http://127.0.0.1:5185/devpreview-unified.html";
const minimumFps = Number(process.env.OPSIA_MIN_FPS ?? 55);
const maximumP95FrameMs = Number(process.env.OPSIA_MAX_P95_FRAME_MS ?? 33.4);
const maximumTransitionMs = Number(process.env.OPSIA_MAX_TRANSITION_MS ?? 1_500);
const maximumLayoutShift = Number(process.env.OPSIA_MAX_LAYOUT_SHIFT ?? 0.1);

const surfaces = [
  "홈",
  "리소스",
  "배포",
  "이슈",
  "타임라인",
  "점검",
  "비용",
  "알림",
  "AI 대화",
  "설정",
];

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1_440, height: 900 } });
const consoleErrors = [];
const failedResponses = [];
const requests = [];

page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("response", (response) => {
  requests.push({ at: performance.now(), status: response.status(), url: response.url() });
  if (response.status() >= 500) {
    failedResponses.push({ status: response.status(), url: response.url() });
  }
});

try {
  await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 120_000 });
  await page.getByRole("button", { name: "홈 화면으로 이동" }).waitFor();

  const results = [];
  for (const surface of surfaces) {
    const requestStart = requests.length;
    const startedAt = performance.now();
    await page.getByRole("button", { name: `${surface} 화면으로 이동` }).click();
    await page.evaluate(() => new Promise((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(resolve));
    }));
    const transitionMs = performance.now() - startedAt;
    const frame = await sampleFrameHealth(page, 1_200);
    const surfaceRequests = requests.slice(requestStart);
    results.push({
      surface,
      transitionMs: round(transitionMs),
      averageFps: round(frame.averageFps),
      p95FrameMs: round(frame.p95FrameMs),
      longTasks: frame.longTasks,
      layoutShift: round(frame.layoutShift),
      requests: surfaceRequests.length,
      serverErrors: surfaceRequests.filter(({ status }) => status >= 500).length,
    });
  }

  const violations = results.flatMap((result) => [
    result.transitionMs > maximumTransitionMs
      ? `${result.surface}: 화면 전환 ${result.transitionMs}ms > ${maximumTransitionMs}ms`
      : null,
    result.averageFps < minimumFps
      ? `${result.surface}: 평균 ${result.averageFps}fps < ${minimumFps}fps`
      : null,
    result.p95FrameMs > maximumP95FrameMs
      ? `${result.surface}: p95 프레임 ${result.p95FrameMs}ms > ${maximumP95FrameMs}ms`
      : null,
    result.layoutShift > maximumLayoutShift
      ? `${result.surface}: CLS ${result.layoutShift} > ${maximumLayoutShift}`
      : null,
  ].filter(Boolean));

  if (consoleErrors.length > 0) violations.push(`콘솔 오류 ${consoleErrors.length}건`);
  if (failedResponses.length > 0) violations.push(`HTTP 5xx ${failedResponses.length}건`);

  console.log(JSON.stringify({
    targetUrl,
    budgets: {
      minimumFps,
      maximumP95FrameMs,
      maximumTransitionMs,
      maximumLayoutShift,
    },
    results,
    consoleErrors,
    failedResponses,
    pass: violations.length === 0,
    violations,
  }, null, 2));

  if (violations.length > 0) process.exitCode = 1;
} finally {
  await browser.close();
}

async function sampleFrameHealth(targetPage, durationMs) {
  return targetPage.evaluate((sampleDurationMs) => new Promise((resolve) => {
    const deltas = [];
    let lastFrameAt = 0;
    let firstFrameAt = 0;
    let longTasks = 0;
    let layoutShift = 0;
    const observers = [];

    if (PerformanceObserver.supportedEntryTypes.includes("longtask")) {
      const observer = new PerformanceObserver((list) => {
        longTasks += list.getEntries().length;
      });
      observer.observe({ type: "longtask" });
      observers.push(observer);
    }
    if (PerformanceObserver.supportedEntryTypes.includes("layout-shift")) {
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (!("hadRecentInput" in entry) || !entry.hadRecentInput) layoutShift += entry.value;
        }
      });
      observer.observe({ type: "layout-shift" });
      observers.push(observer);
    }

    const finish = () => {
      observers.forEach((observer) => observer.disconnect());
      const sorted = [...deltas].sort((left, right) => left - right);
      const p95Index = Math.max(0, Math.ceil(sorted.length * 0.95) - 1);
      const elapsed = Math.max(1, lastFrameAt - firstFrameAt);
      resolve({
        averageFps: deltas.length * 1_000 / elapsed,
        p95FrameMs: sorted[p95Index] ?? 0,
        longTasks,
        layoutShift,
      });
    };

    const paint = (now) => {
      if (firstFrameAt === 0) {
        firstFrameAt = now;
      } else {
        deltas.push(now - lastFrameAt);
      }
      lastFrameAt = now;
      if (now - firstFrameAt >= sampleDurationMs) finish();
      else requestAnimationFrame(paint);
    };
    requestAnimationFrame(paint);
  }), durationMs);
}

function round(value) {
  return Math.round(value * 100) / 100;
}
