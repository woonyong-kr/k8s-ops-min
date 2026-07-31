import { chromium } from "playwright";

const targetUrl = process.env.OPSIA_LOAD_URL
  ?? "http://127.0.0.1:5185/devpreview-unified.html";
const concurrentUsers = Number(process.env.OPSIA_CONCURRENT_USERS ?? 3);
const measuredCycles = Number(process.env.OPSIA_LOAD_CYCLES ?? 1);
const maximumApiP95Ms = Number(process.env.OPSIA_MAX_API_P95_MS ?? 2_000);
const maximumInteractionP95Ms = Number(process.env.OPSIA_MAX_INTERACTION_P95_MS ?? 150);
const maximumFirstUsefulDataMs = Number(process.env.OPSIA_MAX_TTFUD_MS ?? 2_000);
const maximumHeapGrowthBytes = Number(process.env.OPSIA_MAX_HEAP_GROWTH_BYTES ?? 25 * 1024 * 1024);

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

if (!Number.isInteger(concurrentUsers) || concurrentUsers < 1 || concurrentUsers > 10) {
  throw new RangeError("OPSIA_CONCURRENT_USERS must be an integer between 1 and 10");
}
if (!Number.isInteger(measuredCycles) || measuredCycles < 1 || measuredCycles > 10) {
  throw new RangeError("OPSIA_LOAD_CYCLES must be an integer between 1 and 10");
}

const browser = await chromium.launch({ headless: true });
try {
  const users = await Promise.all(Array.from(
    { length: concurrentUsers },
    (_, index) => runUser(browser, index + 1),
  ));
  const apiMeasurements = users.flatMap((user) => user.apiMeasurements);
  const apiLatencies = apiMeasurements.map(({ latencyMs }) => latencyMs);
  const interactionLatencies = users.flatMap((user) => user.interactions.map(({ latencyMs }) => latencyMs));
  const failures = users.flatMap((user) => user.failures);
  const consoleErrors = users.flatMap((user) => user.consoleErrors);
  const firstUsefulDataP95Ms = percentile(users.map((user) => user.firstUsefulDataMs), 0.95);
  const apiP50Ms = percentile(apiLatencies, 0.5);
  const apiP95Ms = percentile(apiLatencies, 0.95);
  const interactionP95Ms = percentile(interactionLatencies, 0.95);
  const maximumObservedHeapGrowth = Math.max(0, ...users.map((user) => user.heapGrowthBytes ?? 0));
  const surfaceSummaries = surfaces.map((surface) => {
    const surfaceApi = apiMeasurements.filter((measurement) => measurement.surface === surface);
    const surfaceInteractions = users.flatMap((user) => user.interactions)
      .filter((interaction) => interaction.surface === surface)
      .map((interaction) => interaction.latencyMs);
    return {
      surface,
      apiRequests: surfaceApi.length,
      apiP95Ms: percentile(surfaceApi.map(({ latencyMs }) => latencyMs), 0.95),
      interactionP95Ms: percentile(surfaceInteractions, 0.95),
    };
  });

  const violations = [
    apiP95Ms > maximumApiP95Ms ? `API p95 ${apiP95Ms}ms > ${maximumApiP95Ms}ms` : null,
    interactionP95Ms > maximumInteractionP95Ms
      ? `상호작용 p95 ${interactionP95Ms}ms > ${maximumInteractionP95Ms}ms`
      : null,
    firstUsefulDataP95Ms > maximumFirstUsefulDataMs
      ? `최초 유효 화면 p95 ${firstUsefulDataP95Ms}ms > ${maximumFirstUsefulDataMs}ms`
      : null,
    maximumObservedHeapGrowth > maximumHeapGrowthBytes
      ? `JS 힙 증가 ${maximumObservedHeapGrowth}B > ${maximumHeapGrowthBytes}B`
      : null,
    failures.length > 0 ? `네트워크/HTTP 실패 ${failures.length}건` : null,
    consoleErrors.length > 0 ? `콘솔 오류 ${consoleErrors.length}건` : null,
  ].filter(Boolean);

  console.log(JSON.stringify({
    targetUrl,
    concurrentUsers,
    measuredCycles,
    budgets: {
      maximumApiP95Ms,
      maximumInteractionP95Ms,
      maximumFirstUsefulDataMs,
      maximumHeapGrowthBytes,
    },
    summary: {
      apiRequests: apiLatencies.length,
      apiP50Ms,
      apiP95Ms,
      interactionP95Ms,
      firstUsefulDataP95Ms,
      maximumObservedHeapGrowth,
      networkOrHttpFailures: failures.length,
      consoleErrors: consoleErrors.length,
    },
    surfaces: surfaceSummaries,
    users: users.map(({ apiMeasurements: measurements, ...user }) => ({
      ...user,
      apiRequests: measurements.length,
      apiP95Ms: percentile(measurements.map(({ latencyMs }) => latencyMs), 0.95),
    })),
    pass: violations.length === 0,
    violations,
  }, null, 2));
  if (violations.length > 0) process.exitCode = 1;
} finally {
  await browser.close();
}

async function runUser(activeBrowser, userId) {
  const context = await activeBrowser.newContext({ viewport: { width: 1_440, height: 900 } });
  const page = await context.newPage();
  const requestStartedAt = new WeakMap();
  const apiMeasurements = [];
  const failures = [];
  const consoleErrors = [];
  let activeSurface = "초기";

  page.on("request", (request) => requestStartedAt.set(request, {
    at: performance.now(),
    surface: activeSurface,
  }));
  page.on("response", (response) => {
    const url = response.url();
    if (!isApiUrl(url)) return;
    const started = requestStartedAt.get(response.request());
    if (started !== undefined) {
      apiMeasurements.push({
        latencyMs: round(performance.now() - started.at),
        surface: started.surface,
      });
    }
    if (response.status() >= 500) failures.push({ status: response.status(), url });
  });
  page.on("requestfailed", (request) => {
    if (isApiUrl(request.url()) && request.failure()?.errorText !== "net::ERR_ABORTED") {
      failures.push({ error: request.failure()?.errorText ?? "request failed", url: request.url() });
    }
  });
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));

  activeSurface = "홈";
  const navigationStartedAt = performance.now();
  await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 120_000 });
  await page.getByRole("button", { name: "홈 화면으로 이동" }).waitFor();
  await page.waitForFunction(() => {
    const main = document.querySelector("main");
    return main !== null && main.textContent !== null && main.textContent.trim().length > 20;
  });
  const firstUsefulDataMs = round(performance.now() - navigationStartedAt);

  // 첫 순회에서 번들·폰트·데이터 캐시를 예열한 뒤 힙 증가와 상호작용을 측정한다.
  await navigateSurfaces(page, false, (surface) => { activeSurface = surface; });
  const heapBefore = await readHeap(page);
  const interactions = [];
  for (let cycle = 0; cycle < measuredCycles; cycle += 1) {
    interactions.push(...await navigateSurfaces(page, true, (surface) => { activeSurface = surface; }));
  }
  await page.waitForTimeout(500);
  const heapAfter = await readHeap(page);
  await context.close();

  return {
    userId,
    firstUsefulDataMs,
    apiMeasurements,
    interactions,
    heapBeforeBytes: heapBefore,
    heapAfterBytes: heapAfter,
    heapGrowthBytes: heapBefore === null || heapAfter === null ? null : heapAfter - heapBefore,
    failures,
    consoleErrors,
  };
}

async function navigateSurfaces(page, record, onSurface) {
  const interactions = [];
  for (const surface of surfaces) {
    onSurface(surface);
    const startedAt = performance.now();
    await page.getByRole("button", { name: `${surface} 화면으로 이동` }).click();
    await page.evaluate(() => new Promise((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(resolve));
    }));
    const latencyMs = round(performance.now() - startedAt);
    if (record) interactions.push({ surface, latencyMs });
    // 짧은 관측 창을 유지해 이 화면이 시작한 API 응답을 다음 화면으로 잘못 귀속하지 않는다.
    await page.waitForTimeout(200);
  }
  return interactions;
}

async function readHeap(page) {
  return page.evaluate(() => {
    const memory = performance.memory;
    return memory && Number.isFinite(memory.usedJSHeapSize) ? memory.usedJSHeapSize : null;
  });
}

function percentile(values, fraction) {
  if (values.length === 0) return 0;
  const ordered = [...values].sort((left, right) => left - right);
  return round(ordered[Math.max(0, Math.ceil(ordered.length * fraction) - 1)]);
}

function isApiUrl(value) {
  try {
    return new URL(value).pathname.startsWith("/api/");
  } catch {
    return false;
  }
}

function round(value) {
  return Math.round(value * 100) / 100;
}
