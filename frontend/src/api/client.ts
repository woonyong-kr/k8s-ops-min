import { z } from "zod";

export type ApiErrorKind =
  | "unauthorized"
  | "forbidden"
  | "not-found"
  | "invalid-request"
  | "rate-limited"
  | "network"
  | "invalid-payload"
  | "http";

interface ApiErrorOptions {
  status?: number;
  code?: string;
  detail?: string;
  retryAfter?: number;
  cause?: unknown;
}

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly code: string | null;
  readonly detail: string | null;
  readonly retryAfter: number | null;
  readonly cause: unknown;

  constructor(kind: ApiErrorKind, message: string, options: ApiErrorOptions = {}) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = options.status ?? null;
    this.code = options.code ?? null;
    this.detail = options.detail ?? null;
    this.retryAfter = options.retryAfter ?? null;
    this.cause = options.cause;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export type ApiPath = `/api/${string}`;

const STATE_CHANGING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const CSRF_HEADER = "x-service-csrf";
const CSRF_VALUE = "same-origin";

const errorEnvelopeSchema = z.object({
  detail: z.unknown().optional(),
  error: z.string().optional(),
});

const structuredDetailSchema = z.object({
  code: z.string().optional(),
  detail: z.string().optional(),
  retry_after: z.number().optional(),
});

interface ResponseBody {
  value: unknown;
  parseError: unknown | null;
  empty: boolean;
  rawEmpty: boolean;
}

interface ErrorMetadata {
  code: string | null;
  detail: string | null;
  retryAfter: number | null;
}

export async function apiRequest<TSchema extends z.ZodType>(
  path: ApiPath,
  schema: TSchema,
  init: RequestInit = {},
): Promise<z.output<TSchema>> {
  const response = await request(path, init);

  const body = await readResponseBody(response);

  if (!response.ok) {
    throw httpError(response, body.value);
  }

  if (body.parseError !== null) {
    throw new ApiError("invalid-payload", "API response was not valid JSON.", {
      status: response.status,
      cause: body.parseError,
    });
  }

  const result = schema.safeParse(body.value);
  if (!result.success) {
    throw new ApiError("invalid-payload", "API response did not match its contract.", {
      status: response.status,
      cause: result.error,
    });
  }

  return result.data;
}

export async function apiRequestNoContent(
  path: ApiPath,
  init: RequestInit = {},
): Promise<void> {
  const response = await request(path, init);
  const body = await readResponseBody(response);

  if (!response.ok) {
    throw httpError(response, body.value);
  }

  if ((response.status !== 204 && response.status !== 205) || !body.rawEmpty) {
    throw new ApiError(
      "invalid-payload",
      "API response did not match the 204/205 no-content contract.",
      { status: response.status, cause: body.parseError ?? body.value },
    );
  }
}

export async function apiStreamResponse(
  path: ApiPath,
  mediaType: string,
  signal?: AbortSignal,
  extraHeaders?: HeadersInit,
): Promise<Response> {
  return apiStreamRequest(path, mediaType, { headers: extraHeaders, signal });
}

/**
 * Read a same-origin streaming response with the shared auth/CSRF boundary.
 *
 * Unlike `apiStreamResponse`, this accepts the complete request init so a
 * stream whose query is too complex for a URL can use a POST body.  It is not
 * a mutation helper: the normal request policy still decides whether a method
 * receives the CSRF header.
 */
export async function apiStreamRequest(
  path: ApiPath,
  mediaType: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("accept", mediaType);
  const response = await request(path, {
    ...init,
    headers,
  });
  if (response.ok) return response;
  const body = await readResponseBody(response);
  throw httpError(response, body.value);
}

async function request(path: ApiPath, init: RequestInit): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (!headers.has("accept")) headers.set("accept", "application/json");

  if (STATE_CHANGING_METHODS.has(method)) {
    headers.set(CSRF_HEADER, CSRF_VALUE);
  }

  try {
    return await fetch(path, {
      ...init,
      method,
      headers,
      credentials: "include",
    });
  } catch (cause) {
    if (isAbortError(cause)) throw cause;
    throw new ApiError("network", "API request could not be completed.", { cause });
  }
}

async function readResponseBody(response: Response): Promise<ResponseBody> {
  let text: string;
  try {
    text = await response.text();
  } catch (cause) {
    if (isAbortError(cause)) throw cause;
    throw new ApiError("network", "API response could not be read.", { cause });
  }

  if (text.trim() === "") {
    return {
      value: undefined,
      parseError: new Error("Response body was empty."),
      empty: true,
      rawEmpty: text.length === 0,
    };
  }

  try {
    const value: unknown = JSON.parse(text);
    return { value, parseError: null, empty: false, rawEmpty: false };
  } catch (parseError) {
    return { value: undefined, parseError, empty: false, rawEmpty: false };
  }
}

function httpError(response: Response, payload: unknown): ApiError {
  const metadata = errorMetadata(payload, response.headers.get("retry-after"));
  const kind = errorKind(response.status);
  const message =
    metadata.detail ??
    (response.statusText || `API request failed with status ${response.status}.`);

  return new ApiError(kind, message, {
    status: response.status,
    code: metadata.code ?? undefined,
    detail: metadata.detail ?? undefined,
    retryAfter: metadata.retryAfter ?? undefined,
  });
}

function errorKind(status: number): ApiErrorKind {
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "not-found";
  if (status === 422) return "invalid-request";
  if (status === 429) return "rate-limited";
  return "http";
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}

function errorMetadata(payload: unknown, retryAfterHeader: string | null): ErrorMetadata {
  const envelope = errorEnvelopeSchema.safeParse(payload);
  if (!envelope.success) {
    return {
      code: null,
      detail: null,
      retryAfter: parseRetryAfter(retryAfterHeader),
    };
  }

  if (typeof envelope.data.detail === "string") {
    return {
      code: null,
      detail: envelope.data.detail,
      retryAfter: parseRetryAfter(retryAfterHeader),
    };
  }

  const structured = structuredDetailSchema.safeParse(envelope.data.detail);
  if (structured.success) {
    return {
      code: structured.data.code ?? null,
      detail: structured.data.detail ?? null,
      retryAfter:
        structured.data.retry_after ?? parseRetryAfter(retryAfterHeader),
    };
  }

  return {
    code: null,
    detail: envelope.data.error ?? null,
    retryAfter: parseRetryAfter(retryAfterHeader),
  };
}

function parseRetryAfter(value: string | null): number | null {
  if (value === null || value.trim() === "") return null;
  const seconds = Number(value);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}
