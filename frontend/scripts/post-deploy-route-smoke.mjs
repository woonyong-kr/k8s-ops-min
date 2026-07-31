import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

import { chromium } from "playwright";

const SIDEBAR_SELECTOR = 'aside[data-slot="sidebar"]';
const NAVIGATION_LINK_SELECTOR = `${SIDEBAR_SELECTOR} nav a[href]`;
const UNIFIED_SHELL_SELECTOR = "#root .uni";
export const UNIFIED_NAVIGATION_SELECTOR = '[data-slot="global-navigation"] button[aria-label$=" 화면으로 이동"]';
const WORKSPACE_HEADER_SELECTOR =
  'header[data-slot="product-header"] [data-slot="product-header-workspace"]';
const AUTH_BOOTSTRAP_TIMEOUT_MS = 60_000;
const ROUTE_SETTLE_TIMEOUT_MS = 20_000;
const NETWORK_QUIET_WINDOW_MS = 500;
const NETWORK_SAMPLE_INTERVAL_MS = 100;
const ROUTE_STABLE_SAMPLE_COUNT = 3;
const SLOW_API_LIMIT = 5;
const DIAGNOSTIC_ITEM_LIMIT = 50;
const LONG_LIVED_API_MEDIA_TYPES = new Set([
  "application/x-ndjson",
  "text/event-stream",
]);
const SENSITIVE_ASSIGNMENT_PATTERN = /\b(authorization|bearer|credential|password|passwd|private[_ -]?key|secret|token|api[_ -]?key|apikey|cookie|set[_ -]?cookie)\s*([:=])\s*(?:Bearer\s+)?[^\s,"']+/giu;
const BEARER_PATTERN = /\bBearer\s+[A-Za-z0-9._~+\/=-]+/giu;
const FAILURE_PRODUCT_STATES = new Set([
  "error",
  "forbidden",
  "not-found",
  "offline",
  "release",
]);
const LEGACY_DOCUMENT_ALIASES = Object.freeze([
  "/devpreview-unified.html",
  "/devpreview-index.html",
]);
const RETIRED_DOCUMENT_CANARY = "/retired-product-entrypoint-canary.html";

/**
 * Route-owned read boundaries used by the deployment smoke. Prefix matching
 * deliberately includes nested resource URLs while keeping shell bootstrap
 * and global polling outside the route completion gate.
 */
export const ROUTE_CRITICAL_API_CONTRACTS = Object.freeze({
  "/home": Object.freeze(["/api/clusters"]),
  "/resources": Object.freeze([
    "/api/resources",
    "/api/filter-facets",
    "/api/clusters",
  ]),
  "/deploy": Object.freeze(["/api/applications"]),
  "/issues": Object.freeze(["/api/dashboard/rca"]),
  "/timeline": Object.freeze(["/api/timeline"]),
  "/checks": Object.freeze(["/api/checks/overview"]),
  "/cost": Object.freeze([
    "/api/cost",
    "/api/rightsizing",
  ]),
  "/alerts": Object.freeze(["/api/alert-events"]),
  "/ai": Object.freeze(["/api/ai/conversations"]),
  "/settings": Object.freeze([
    "/api/settings",
    "/api/integrations",
  ]),
});

export const COMMON_BACKGROUND_API_CONTRACTS = Object.freeze([
  "/api/alert-events",
  "/api/auth/session",
  "/api/refresh-policies",
  "/api/settings",
  "/api/version-check",
]);

export function normalizeSurfaceText(value) {
  return value
    .normalize("NFKC")
    .replace(/\p{Number}+/gu, "#")
    .replace(/\s+/g, " ")
    .trim();
}

export function orderRoutesForTraversal(routes, currentPathname) {
  const otherRoutes = routes.filter(({ pathname }) => pathname !== currentPathname);
  const currentRoutes = routes.filter(({ pathname }) => pathname === currentPathname);
  return [...otherRoutes, ...currentRoutes];
}

export function isStableRouteSurfaceSample(previous, current) {
  return previous !== null
    && current.routeTitle === previous.routeTitle
    && current.bodyFingerprint === previous.bodyFingerprint;
}

export function isChangeTimelineLimitResponse(status, rawUrl) {
  if (status !== 422) return false;
  try {
    return new URL(rawUrl).pathname.endsWith("/api/changes");
  } catch {
    return false;
  }
}

export function isBenignNavigationAbort(errorText) {
  const normalized = errorText.trim().toUpperCase();
  return normalized === "NET::ERR_ABORTED" || normalized === "NS_BINDING_ABORTED";
}

export function isApiErrorResponse(status, rawUrl, baseUrl) {
  if (status < 400) return false;
  try {
    const responseUrl = new URL(rawUrl);
    const applicationUrl = new URL(baseUrl);
    return (
      responseUrl.origin === applicationUrl.origin
      && (
        responseUrl.pathname === "/api"
        || responseUrl.pathname.startsWith("/api/")
      )
    );
  } catch {
    return false;
  }
}

export function isFailureProductState(state) {
  return FAILURE_PRODUCT_STATES.has(state);
}

export function isObservedRouteApiRequest(rawUrl, baseUrl) {
  try {
    const requestUrl = new URL(rawUrl);
    const applicationUrl = new URL(baseUrl);
    return requestUrl.origin === applicationUrl.origin && (
      requestUrl.pathname === "/api"
      || requestUrl.pathname.startsWith("/api/")
    );
  } catch {
    return false;
  }
}

export function classifyRouteApiRequest(routePathname, rawUrl, baseUrl) {
  if (!isObservedRouteApiRequest(rawUrl, baseUrl)) return "ignored";
  return classifyObservedApiPath(routePathname, new URL(rawUrl).pathname);
}

function classifyObservedApiPath(routePathname, apiPathname) {
  const contracts = ROUTE_CRITICAL_API_CONTRACTS[routePathname] ?? [];
  if (contracts.some((contract) => matchesApiContract(apiPathname, contract))) {
    return "critical";
  }
  if (COMMON_BACKGROUND_API_CONTRACTS.some(
    (contract) => matchesApiContract(apiPathname, contract),
  )) {
    return "background-common";
  }
  return "background-other";
}

function matchesApiContract(apiPathname, contract) {
  return apiPathname === contract || apiPathname.startsWith(`${contract}/`);
}

export function isRouteNetworkSettled({
  lastActivityAt,
  now,
  pendingRequestCount,
  quietWindowMs,
}) {
  return pendingRequestCount === 0 && now - lastActivityAt >= quietWindowMs;
}

export function isSuccessfulLongLivedApiResponse(status, headers = {}) {
  // Streaming handshakes must carry a body. A generic 2xx such as 204 is not
  // enough to release the request from the route completion gate.
  if (status !== 200) return false;
  const contentType = Object.entries(headers)
    .find(([name]) => name.toLowerCase() === "content-type")?.[1]
    ?.split(";", 1)[0]
    ?.trim()
    ?.toLowerCase();
  return contentType !== undefined && LONG_LIVED_API_MEDIA_TYPES.has(contentType);
}

export function createRouteSmokeDiagnostics() {
  return {
    apiErrors: [],
    changeTimelineLimits: [],
    failingRoute: "<bootstrap>",
    pageErrors: [],
    requestFailures: [],
  };
}

export function formatRouteSmokeFailureDiagnostics(error, diagnostics) {
  const bounded = (items) => items.slice(-DIAGNOSTIC_ITEM_LIMIT);
  return JSON.stringify({
    event: "authenticated_route_smoke_failure",
    failingRoute: diagnostics.failingRoute,
    error: redactDiagnosticText(error instanceof Error ? error.message : error),
    requestFailures: bounded(diagnostics.requestFailures).map((failure) => ({
      ...failure,
      error: redactDiagnosticText(failure.error),
    })),
    apiErrors: bounded(diagnostics.apiErrors),
    pageErrors: bounded(diagnostics.pageErrors).map(redactDiagnosticText),
    changeTimelineLimits: bounded(diagnostics.changeTimelineLimits),
  });
}

export async function withRouteSmokeDiagnostics(
  action,
  diagnostics,
  emit = (message) => process.stderr.write(`${message}\n`),
) {
  try {
    return await action();
  } catch (error) {
    emit(formatRouteSmokeFailureDiagnostics(error, diagnostics));
    throw error;
  }
}

function redactDiagnosticText(value) {
  return String(value)
    .replace(BEARER_PATTERN, "Bearer <redacted>")
    .replace(
      SENSITIVE_ASSIGNMENT_PATTERN,
      (_match, key, separator) => `${key}${separator}<redacted>`,
    );
}

export function parseNetscapeSessionCookie(rawCookieJar) {
  const candidates = [];
  for (const rawLine of rawCookieJar.split(/\r?\n/u)) {
    const httpOnly = rawLine.startsWith("#HttpOnly_");
    if (!rawLine || (rawLine.startsWith("#") && !httpOnly)) continue;
    const line = httpOnly ? rawLine.slice("#HttpOnly_".length) : rawLine;
    const fields = line.split("\t");
    if (fields.length < 7) continue;
    const [, , path, , , name, value] = fields;
    if (
      httpOnly
      && path === "/"
      && name
      && value
    ) {
      candidates.push({ name, value });
    }
  }
  assert.equal(
    candidates.length,
    1,
    "authentication handoff must contain exactly one HttpOnly root cookie",
  );
  return candidates[0];
}

async function run() {
  const diagnostics = createRouteSmokeDiagnostics();
  return withRouteSmokeDiagnostics(
    () => runWithDiagnostics(diagnostics),
    diagnostics,
  );
}

async function runWithDiagnostics(diagnostics) {
  const baseUrl = requiredEnvironment("BASE_URL");
  const email = requiredEnvironment("AUTH_EMAIL");
  const password = requiredEnvironment("AUTH_PASSWORD", { trim: false });
  const releaseBudget = readRouteReleaseBudget(process.env);
  diagnostics.failingRoute = new URL(baseUrl).pathname;
  await verifyDeliveryRoutePolicy(baseUrl);
  const browser = await chromium.launch({
    headless: true,
    args: ["--disable-dev-shm-usage", "--no-sandbox"],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const routeNetwork = createRouteNetworkObserver(page, baseUrl);

  page.on("pageerror", (error) => {
    diagnostics.pageErrors.push(error.message);
  });
  page.on("requestfailed", (request) => {
    const error = request.failure()?.errorText ?? "unknown request failure";
    if (isBenignNavigationAbort(error)) return;
    diagnostics.requestFailures.push({
      error,
      method: request.method(),
      url: safeUrl(request.url()),
    });
  });
  page.on("response", (response) => {
    if (isApiErrorResponse(response.status(), response.url(), baseUrl)) {
      diagnostics.apiErrors.push({
        status: response.status(),
        url: safeUrl(response.url()),
      });
    }
    if (isChangeTimelineLimitResponse(response.status(), response.url())) {
      diagnostics.changeTimelineLimits.push({
        status: response.status(),
        url: safeUrl(response.url()),
      });
    }
  });

  try {
    await authenticate(page, baseUrl, email, password);
    if (await isUnifiedShell(page)) {
      await runUnifiedShellSmoke(page, baseUrl, diagnostics);
      assertDiagnostics(diagnostics);
      return;
    }
    const initial = await waitForRouteSurface(page, null, new URL(page.url()).pathname);
    const currentWorkspace = await verifyCurrentWorkspaceEvidence({
      loadCatalog: () => requestBrowserJson(page, baseUrl, "/api/auth/workspaces"),
      loadSession: () => requestBrowserJson(page, baseUrl, "/api/auth/session"),
    });
    await verifyWorkspaceSwitcherPlacement(page, currentWorkspace.workspace_id);
    process.stdout.write("authenticated real workspace evidence passed\n");
    const routes = await collectReleasedRoutes(page);
    const traversal = orderRoutesForTraversal(routes, new URL(page.url()).pathname);

    assert.ok(routes.length > 1, "released navigation must expose multiple DOM routes");

    let previous = initial;
    for (const route of traversal) {
      const routeStartedAt = Date.now();
      diagnostics.failingRoute = route.pathname;
      const link = await releasedRouteLink(page, route.pathname);
      const observedHref = await link.getAttribute("href");
      assert.ok(observedHref, `released route ${route.pathname} lost its href`);
      assert.equal(
        new URL(observedHref, page.url()).pathname,
        route.pathname,
        `released route DOM order changed before ${route.pathname}`,
      );

      const spaStartedAt = Date.now();
      const spaNetworkPhase = routeNetwork.beginPhase(route.pathname);
      await Promise.all([
        page.waitForURL(
          (url) => url.pathname === route.pathname,
          { timeout: ROUTE_SETTLE_TIMEOUT_MS },
        ),
        link.click(),
      ]);
      const spaFrame = await waitForObservedRouteSurface(
        page,
        routeNetwork,
        spaNetworkPhase,
        previous,
        route.pathname,
      );
      const spaDurationMs = Date.now() - spaStartedAt;
      const spaSummary = routeNetwork.summarize(spaNetworkPhase, spaDurationMs);
      assertDiagnostics(diagnostics);

      const directUrl = new URL(observedHref, baseUrl);
      const directStartedAt = Date.now();
      const directNetworkPhase = routeNetwork.beginPhase(route.pathname);
      await page.goto(directUrl.href, { waitUntil: "domcontentloaded" });
      previous = await waitForObservedRouteSurface(
        page,
        routeNetwork,
        directNetworkPhase,
        null,
        route.pathname,
        AUTH_BOOTSTRAP_TIMEOUT_MS,
      );
      const directDurationMs = Date.now() - directStartedAt;
      const directSummary = routeNetwork.summarize(
        directNetworkPhase,
        directDurationMs,
      );
      assert.equal(
        previous.routeTitle,
        spaFrame.routeTitle,
        `direct route title changed for ${route.pathname}`,
      );
      assertDiagnostics(diagnostics);
      assertRouteReleaseBudget({
        budget: releaseBudget,
        direct: directSummary,
        pathname: route.pathname,
        spa: spaSummary,
        totalDurationMs: Date.now() - routeStartedAt,
      });
      process.stdout.write(`${formatRouteTiming({
        direct: directSummary,
        pathname: route.pathname,
        spa: spaSummary,
        totalDurationMs: Date.now() - routeStartedAt,
      })}\n`);
    }

    assertDiagnostics(diagnostics);
    process.stdout.write(`authenticated route smoke passed: ${routes.length} routes\n`);
  } finally {
    routeNetwork.dispose();
    await browser.close();
  }
}

export async function verifyDeliveryRoutePolicy(baseUrl, request = fetch) {
  const canonicalUrl = new URL("/", baseUrl);
  const canonicalResponse = await request(canonicalUrl, { redirect: "manual" });
  assert.equal(
    canonicalResponse.status,
    200,
    `canonical product URL must return HTTP 200: ${canonicalUrl.href}`,
  );

  for (const alias of LEGACY_DOCUMENT_ALIASES) {
    const aliasUrl = new URL(alias, canonicalUrl);
    const response = await request(aliasUrl, { redirect: "manual" });
    assert.equal(response.status, 308, `${alias} must permanently redirect`);
    const location = response.headers.get("location");
    assert.ok(location, `${alias} redirect must include Location`);
    assert.equal(
      new URL(location, aliasUrl).href,
      canonicalUrl.href,
      `${alias} must redirect to the canonical product URL`,
    );
  }

  const retiredUrl = new URL(RETIRED_DOCUMENT_CANARY, canonicalUrl);
  const retiredResponse = await request(retiredUrl, { redirect: "manual" });
  assert.equal(
    retiredResponse.status,
    404,
    "unknown retired HTML entry points must not render the product home page",
  );
  process.stdout.write("canonical delivery route policy passed\n");
}

async function isUnifiedShell(page) {
  return (await page.locator(UNIFIED_SHELL_SELECTOR).count()) === 1;
}

async function runUnifiedShellSmoke(page, baseUrl, diagnostics) {
  const root = page.locator("#root");
  await root.waitFor({ state: "visible", timeout: AUTH_BOOTSTRAP_TIMEOUT_MS });
  const currentWorkspace = await verifyCurrentWorkspaceEvidence({
    loadCatalog: () => requestBrowserJson(page, baseUrl, "/api/auth/workspaces"),
    loadSession: () => requestBrowserJson(page, baseUrl, "/api/auth/session"),
  });
  const workspaceIdentity = root.locator('[data-slot="workspace-identity"]').first();
  await workspaceIdentity.waitFor({ state: "visible", timeout: AUTH_BOOTSTRAP_TIMEOUT_MS });
  assert.equal(
    await workspaceIdentity.getAttribute("data-workspace-id"),
    currentWorkspace.workspace_id,
    "unified workspace identity must preserve the authenticated workspace id",
  );
  assert.ok(
    (await workspaceIdentity.innerText()).trim().length > 0,
    "unified workspace identity must expose a non-empty product label",
  );

  const navigation = page.locator(UNIFIED_NAVIGATION_SELECTOR);
  const labels = await navigation.evaluateAll((items) => items
    .map((item) => item.getAttribute("aria-label"))
  );
  assert.equal(labels.length, 9, "GlobalNav must expose exactly eight primary surfaces plus settings");
  assert.ok(labels.every((label) => typeof label === "string" && label.length > 0), "GlobalNav labels must be non-empty");
  assert.equal(new Set(labels).size, labels.length, "GlobalNav labels must be unique");
  for (const label of labels) {
    const item = page.locator('[data-slot="global-navigation"]')
      .getByRole("button", { name: label, exact: true });
    diagnostics.failingRoute = label;
    await item.click();
    await page.waitForTimeout(250);
    assert.equal(
      await item.getAttribute("aria-current"),
      "page",
      `unified surface did not activate: ${diagnostics.failingRoute}`,
    );
    assert.ok(
      (await root.innerText()).trim().length > 0,
      `unified surface rendered blank: ${diagnostics.failingRoute}`,
    );
    assertDiagnostics(diagnostics);
  }
  process.stdout.write(`authenticated unified shell smoke passed: ${labels.length} surfaces\n`);
}

export async function verifyCurrentWorkspaceEvidence({
  loadCatalog,
  loadSession,
}) {
  const catalog = await loadCatalog();
  assertWorkspaceCatalog(catalog);
  const current = catalog.items.find(
    ({ workspace_id: workspaceId }) => workspaceId === catalog.current_workspace_id,
  );
  assert.ok(current, "current workspace must exist in the authenticated catalog");
  assertWorkspaceSession(
    await loadSession(),
    catalog.current_workspace_id,
    "current workspace session",
  );
  return current;
}

function assertWorkspaceCatalog(value) {
  assert.ok(value && typeof value === "object", "workspace catalog must be an object");
  assert.ok(
    typeof value.current_workspace_id === "string"
      && value.current_workspace_id.trim().length > 0,
    "workspace catalog current_workspace_id must be a non-empty string",
  );
  assert.ok(Array.isArray(value.items), "workspace catalog items must be an array");
  for (const item of value.items) {
    assert.ok(
      item
        && typeof item === "object"
        && typeof item.workspace_id === "string"
        && item.workspace_id.trim().length > 0,
      "workspace catalog item workspace_id must be a non-empty string",
    );
    assert.ok(
      typeof item.name === "string" && item.name.trim().length > 0,
      "workspace catalog item name must be a non-empty string",
    );
  }
}

function assertWorkspaceSession(value, expectedWorkspaceId, label) {
  assert.ok(value && typeof value === "object", `${label} must be an object`);
  assert.equal(value.workspace_id, expectedWorkspaceId, `${label} workspace mismatch`);
}

async function requestBrowserJson(page, baseUrl, pathname, options = {}) {
  const method = options.method ?? "GET";
  const response = await page.request.fetch(new URL(pathname, baseUrl).href, {
    data: options.data,
    failOnStatusCode: false,
    headers: method === "GET"
      ? undefined
      : {
          "content-type": "application/json",
          "x-service-csrf": "same-origin",
        },
    method,
  });
  assert.ok(
    response.ok(),
    `${method} ${pathname} failed with status ${response.status()}`,
  );
  try {
    return await response.json();
  } catch {
    throw new Error(`${method} ${pathname} returned invalid JSON`);
  }
}

export async function verifyWorkspaceSwitcherPlacement(
  page,
  workspaceId,
  timeoutMs = AUTH_BOOTSTRAP_TIMEOUT_MS,
) {
  await page
    .locator(WORKSPACE_HEADER_SELECTOR)
    .getByText(workspaceId, { exact: true })
    .waitFor({ state: "visible", timeout: timeoutMs });
  const sidebarMatches = await page
    .locator(SIDEBAR_SELECTOR)
    .getByText(workspaceId, { exact: true })
    .count();
  assert.equal(
    sidebarMatches,
    0,
    "workspace switcher must not remain in the product sidebar",
  );
}

async function waitForObservedRouteSurface(
  page,
  routeNetwork,
  networkPhase,
  previous,
  expectedPathname,
  timeoutMs = ROUTE_SETTLE_TIMEOUT_MS,
) {
  await waitForRouteSurface(page, previous, expectedPathname, timeoutMs);
  await routeNetwork.waitForSettled(networkPhase, timeoutMs);
  return waitForRouteSurface(page, previous, expectedPathname, timeoutMs);
}

export function createRouteNetworkObserver(page, baseUrl) {
  let sequence = 0;
  const pendingRequests = new Map();
  const completedRequests = [];
  const activity = [];
  const recordActivity = (pending) => {
    activity.push({
      at: Date.now(),
      path: pending.path,
      requestSequence: pending.requestSequence,
      sequence,
    });
  };
  const onRequest = (request) => {
    if (!isObservedRouteApiRequest(request.url(), baseUrl)) return;
    sequence += 1;
    const pending = {
      path: safeUrl(request.url()),
      requestSequence: sequence,
      status: null,
      startedAt: Date.now(),
    };
    pendingRequests.set(request, pending);
    recordActivity(pending);
  };
  const onResponse = (response) => {
    const pending = pendingRequests.get(response.request());
    if (pending === undefined) return;
    pending.status = response.status();
    pending.longLived = isSuccessfulLongLivedApiResponse(
      pending.status,
      response.headers?.() ?? {},
    );
    sequence += 1;
    recordActivity(pending);
  };
  const onRequestFinished = (request) => {
    const pending = pendingRequests.get(request);
    completeRequest(request, pending?.status ?? null);
  };
  const onRequestFailed = (request) => {
    completeRequest(request, null);
  };
  const completeRequest = (request, status) => {
    const pending = pendingRequests.get(request);
    if (pending === undefined) return;
    pendingRequests.delete(request);
    sequence += 1;
    recordActivity(pending);
    completedRequests.push({
      durationMs: Date.now() - pending.startedAt,
      path: pending.path,
      requestSequence: pending.requestSequence,
      status,
    });
  };

  page.on("request", onRequest);
  page.on("response", onResponse);
  page.on("requestfinished", onRequestFinished);
  page.on("requestfailed", onRequestFailed);

  return {
    beginPhase(routePathname) {
      assert.ok(
        Object.hasOwn(ROUTE_CRITICAL_API_CONTRACTS, routePathname),
        `missing route critical API contract: ${routePathname}`,
      );
      return {
        completedOffset: completedRequests.length,
        routePathname,
        sequence,
        startedAt: Date.now(),
      };
    },
    dispose() {
      page.off("request", onRequest);
      page.off("response", onResponse);
      page.off("requestfinished", onRequestFinished);
      page.off("requestfailed", onRequestFailed);
    },
    summarize(phase, durationMs) {
      const requests = completedRequests
        .slice(phase.completedOffset)
        .filter((request) => request.requestSequence > phase.sequence);
      const criticalRequests = requests.filter(
        (request) => classifyObservedApiPath(phase.routePathname, request.path) === "critical",
      );
      const backgroundRequests = requests.filter(
        (request) => classifyObservedApiPath(phase.routePathname, request.path) !== "critical",
      );
      const pendingBackgroundRequests = [...pendingRequests.values()]
        .filter((request) => (
          request.requestSequence > phase.sequence
          && classifyObservedApiPath(phase.routePathname, request.path) !== "critical"
        ))
        .map((request) => ({
          durationMs: Date.now() - request.startedAt,
          inFlight: true,
          path: request.path,
          status: request.status,
        }));
      const pendingCriticalRequests = [...pendingRequests.values()]
        .filter((request) => (
          request.requestSequence > phase.sequence
          && classifyObservedApiPath(phase.routePathname, request.path) === "critical"
        ))
        .map((request) => ({
          durationMs: Date.now() - request.startedAt,
          inFlight: true,
          path: request.path,
          status: request.status,
        }));
      const measuredBackgroundRequests = [
        ...backgroundRequests.map((request) => ({ ...request, inFlight: false })),
        ...pendingBackgroundRequests,
      ];
      const measuredCriticalRequests = [
        ...criticalRequests.map((request) => ({ ...request, inFlight: false })),
        ...pendingCriticalRequests,
      ];
      const successfulCriticalApiRequestCount = measuredCriticalRequests.filter(
        ({ status }) => status !== null && status >= 200 && status < 400,
      ).length;
      return {
        backgroundApiRequestCount: measuredBackgroundRequests.length,
        backgroundInFlightRequestCount: pendingBackgroundRequests.length,
        criticalApiRequestCount: measuredCriticalRequests.length,
        criticalInFlightRequestCount: pendingCriticalRequests.length,
        durationMs,
        successfulCriticalApiRequestCount,
        slowBackgroundApi: slowRequestSummary(measuredBackgroundRequests),
        slowCriticalApi: slowRequestSummary(measuredCriticalRequests),
      };
    },
    async waitForSettled(phase, timeoutMs) {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        const criticalActivity = activity.filter((item) => (
          item.requestSequence > phase.sequence
          && classifyObservedApiPath(phase.routePathname, item.path) === "critical"
        ));
        const lastActivityAt = criticalActivity.reduce(
          (latest, item) => Math.max(latest, item.at),
          phase.startedAt,
        );
        const pendingCriticalRequests = [...pendingRequests.values()]
          .filter((request) => (
            request.requestSequence > phase.sequence
            && classifyObservedApiPath(phase.routePathname, request.path) === "critical"
            && !request.longLived
          ));
        const pendingRequestCount = pendingCriticalRequests
          .length;
        if (isRouteNetworkSettled({
          lastActivityAt,
          now: Date.now(),
          pendingRequestCount,
          quietWindowMs: NETWORK_QUIET_WINDOW_MS,
        })) return;
        await page.waitForTimeout(NETWORK_SAMPLE_INTERVAL_MS);
      }
      throw new Error(
        `route critical API requests did not settle: ${JSON.stringify({
          pending: [...pendingRequests.values()]
            .filter((request) => (
              request.requestSequence > phase.sequence
              && classifyObservedApiPath(phase.routePathname, request.path) === "critical"
              && !request.longLived
            ))
            .map((request) => request.path),
          route: phase.routePathname,
        })}`,
      );
    },
  };
}

function slowRequestSummary(requests) {
  return [...requests]
    .sort((left, right) => right.durationMs - left.durationMs)
    .slice(0, SLOW_API_LIMIT)
    .map(({ durationMs, inFlight = false, path, status }) => ({
      durationMs,
      in_flight: inFlight,
      path,
      status,
    }));
}

export function readRouteReleaseBudget(environment) {
  return {
    minCriticalApiRequests: positiveIntegerEnvironment(
      environment,
      "ROUTE_SMOKE_MIN_CRITICAL_API_REQUESTS",
      1,
    ),
  };
}

export function assertRouteReleaseBudget({
  budget,
  direct,
  pathname,
  spa,
}) {
  const criticalEvidence = (
    spa.successfulCriticalApiRequestCount
    + direct.successfulCriticalApiRequestCount
  );
  assert.ok(
    criticalEvidence >= budget.minCriticalApiRequests,
    `route ${pathname} exposed ${criticalEvidence} successful critical API request(s); `
      + `minimum is ${budget.minCriticalApiRequests}`,
  );
}

function formatRouteTiming({ direct, pathname, spa, totalDurationMs }) {
  return `route smoke passed: ${pathname} (spa+direct) ${JSON.stringify({
    direct_background_api_requests: direct.backgroundApiRequestCount,
    direct_background_in_flight: direct.backgroundInFlightRequestCount,
    direct_critical_api_requests: direct.criticalApiRequestCount,
    direct_critical_in_flight: direct.criticalInFlightRequestCount,
    direct_critical_api_successes: direct.successfulCriticalApiRequestCount,
    direct_ms: direct.durationMs,
    direct_slow_background_api: direct.slowBackgroundApi,
    direct_slow_critical_api: direct.slowCriticalApi,
    spa_background_api_requests: spa.backgroundApiRequestCount,
    spa_background_in_flight: spa.backgroundInFlightRequestCount,
    spa_critical_api_requests: spa.criticalApiRequestCount,
    spa_critical_in_flight: spa.criticalInFlightRequestCount,
    spa_critical_api_successes: spa.successfulCriticalApiRequestCount,
    spa_ms: spa.durationMs,
    spa_slow_background_api: spa.slowBackgroundApi,
    spa_slow_critical_api: spa.slowCriticalApi,
    total_ms: totalDurationMs,
  })}`;
}

async function authenticate(page, baseUrl, email, password) {
  const handoffPath = process.env.AUTH_COOKIE_JAR?.trim() ?? "";
  const handoff = handoffPath ? await readCookieHandoff(handoffPath) : null;
  if (handoff === null) {
    const loginResponse = await page.request.post(
      new URL("/api/auth/login", baseUrl).href,
      {
        data: { email, password },
        failOnStatusCode: false,
        headers: { "x-service-csrf": "same-origin" },
      },
    );
    assert.ok(
      loginResponse.ok(),
      `browser authentication failed with status ${loginResponse.status()}`,
    );
  } else {
    const cookie = parseNetscapeSessionCookie(handoff);
    await page.context().addCookies([{
      httpOnly: true,
      name: cookie.name,
      sameSite: "Lax",
      secure: new URL(baseUrl).protocol === "https:",
      url: new URL("/", baseUrl).href,
      value: cookie.value,
    }]);
  }

  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  const sidebar = page.locator(SIDEBAR_SELECTOR);
  const unifiedShell = page.locator(UNIFIED_SHELL_SELECTOR);
  await Promise.race([
    sidebar.waitFor({ state: "visible", timeout: AUTH_BOOTSTRAP_TIMEOUT_MS }),
    unifiedShell.waitFor({ state: "visible", timeout: AUTH_BOOTSTRAP_TIMEOUT_MS }),
  ]);
}

async function readCookieHandoff(path) {
  try {
    return await readFile(path, "utf8");
  } catch (error) {
    if (error && typeof error === "object" && error.code === "ENOENT") return null;
    throw error;
  }
}

async function collectReleasedRoutes(page) {
  const rawRoutes = await page.locator(NAVIGATION_LINK_SELECTOR).evaluateAll((links) =>
    links.map((link) => ({
      href: link.getAttribute("href") ?? "",
    })),
  );
  const routes = [];
  const seen = new Set();

  for (const { href } of rawRoutes) {
    if (!href) continue;
    const url = new URL(href, page.url());
    if (url.origin !== new URL(page.url()).origin || seen.has(url.pathname)) continue;
    seen.add(url.pathname);
    routes.push({ pathname: url.pathname });
  }

  return routes;
}

async function releasedRouteLink(page, pathname) {
  const links = page.locator(NAVIGATION_LINK_SELECTOR);
  const hrefs = await links.evaluateAll((items) => items
    .map((item) => item.getAttribute("href"))
    .filter((href) => href));
  assert.ok(hrefs.some((href) => new URL(href, page.url()).pathname === pathname), `released route link disappeared: ${pathname}`);
  return page.locator(`${SIDEBAR_SELECTOR} nav a[href="${pathname}"]`).first();
}

async function waitForRouteSurface(
  page,
  previous,
  expectedPathname,
  timeoutMs = ROUTE_SETTLE_TIMEOUT_MS,
) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  let stable = null;
  let stableSamples = 0;

  while (Date.now() < deadline) {
    last = await readRouteSurface(page);
    const failureState = last.productStates.find(isFailureProductState);
    if (last.pathname === expectedPathname && failureState) {
      throw new Error(
        `route ${expectedPathname} rendered product state ${failureState}`,
      );
    }
    const transitioned = previous === null
      || (
        last.routeTitle !== previous.routeTitle
        && last.bodyFingerprint !== previous.bodyFingerprint
      );
    const ready = (
      last.pathname === expectedPathname
      && last.documentTitle
      && last.routeTitle
      && last.mainText
      && !last.productStates.includes("loading")
      && transitioned
    );
    if (ready) {
      stableSamples = isStableRouteSurfaceSample(stable, last)
        ? stableSamples + 1
        : 1;
      stable = last;
      if (stableSamples >= ROUTE_STABLE_SAMPLE_COUNT) return last;
    } else {
      stable = null;
      stableSamples = 0;
    }
    await page.waitForTimeout(200);
  }

  throw new Error(
    `route surface did not transition to ${expectedPathname}: ${JSON.stringify({
      bodyChanged: previous ? last?.bodyFingerprint !== previous.bodyFingerprint : null,
      observedPathname: last?.pathname ?? null,
      productStates: last?.productStates ?? [],
      routeTitle: last?.routeTitle ?? null,
      titleChanged: previous ? last?.routeTitle !== previous.routeTitle : null,
    })}`,
  );
}

async function readRouteSurface(page) {
  const main = page.locator("#product-main");
  const [documentTitle, routeTitle, mainText, productStates] = await Promise.all([
    page.title(),
    page.locator("header h1").first().innerText().catch(() => ""),
    main.innerText().catch(() => ""),
    page
      .locator("#product-main[data-product-state], #product-main [data-product-state]")
      .evaluateAll((states) => (
        states
          .map((state) => state.getAttribute("data-product-state") ?? "")
          .filter(Boolean)
      ))
      .catch(() => []),
  ]);
  return {
    bodyFingerprint: normalizeSurfaceText(mainText),
    documentTitle: documentTitle.trim(),
    mainText: mainText.trim(),
    pathname: new URL(page.url()).pathname,
    productStates,
    routeTitle: routeTitle.trim(),
  };
}

function assertDiagnostics(diagnostics) {
  const failures = [];
  if (diagnostics.apiErrors.length > 0) {
    failures.push(`api_error=${JSON.stringify(diagnostics.apiErrors)}`);
  }
  if (diagnostics.pageErrors.length > 0) {
    failures.push(`pageerror=${JSON.stringify(diagnostics.pageErrors)}`);
  }
  if (diagnostics.requestFailures.length > 0) {
    failures.push(`requestfailed=${JSON.stringify(diagnostics.requestFailures)}`);
  }
  if (diagnostics.changeTimelineLimits.length > 0) {
    failures.push(
      `change_timeline_422=${JSON.stringify(diagnostics.changeTimelineLimits)}`,
    );
  }
  assert.equal(failures.length, 0, failures.join("; "));
}

function requiredEnvironment(name, { trim = true } = {}) {
  const rawValue = process.env[name];
  const value = trim ? rawValue?.trim() : rawValue;
  if (value === undefined || value.length === 0) {
    throw new Error(`missing required environment variable: ${name}`);
  }
  return value;
}

function positiveIntegerEnvironment(environment, name, fallback) {
  const rawValue = environment[name]?.trim();
  if (rawValue === undefined || rawValue.length === 0) return fallback;
  if (!/^[1-9][0-9]*$/u.test(rawValue)) {
    throw new Error(`${name} must be a positive integer`);
  }
  return Number.parseInt(rawValue, 10);
}

function safeUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    return url.pathname;
  } catch {
    return "<invalid-url>";
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  run().catch((error) => {
    process.stderr.write(`authenticated route smoke failed: ${error.message}\n`);
    process.exitCode = 1;
  });
}
