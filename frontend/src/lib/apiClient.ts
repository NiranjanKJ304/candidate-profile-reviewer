/**
 * Minimal typed HTTP client for the FastAPI backend.
 *
 * - The base URL comes from validated configuration (see config.ts).
 * - Every response body is untrusted: callers pass a parser that returns the typed
 *   value or `null` when the payload does not match the expected schema.
 * - Outcomes are explicit: "ok", "unreachable" (network error / timeout) and
 *   "invalid" (non-JSON, unexpected HTTP status, schema mismatch). Unknown state is
 *   never reported as a known failure.
 */

import { API_BASE_URL } from "@/lib/config";

export const API_V1_PREFIX = "/api/v1";
export const DEFAULT_TIMEOUT_MS = 5000;
export const UPLOAD_TIMEOUT_MS = 120_000;

export type FetchResult<T> =
  | { kind: "ok"; data: T; httpStatus: number; requestId: string | null }
  | { kind: "unreachable"; message: string }
  | { kind: "invalid"; message: string; httpStatus: number | null; code?: string };

export type Parser<T> = (json: unknown) => T | null;

export interface RequestOptions {
  timeoutMs?: number;
  /** Extra HTTP statuses whose body is still a valid payload (e.g. 503 for /ready). */
  acceptStatuses?: number[];
}

export interface SendOptions<T> extends RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  /** JSON-serialisable object, or FormData for file uploads. */
  body?: unknown;
  /** Parser for the response body; omit for endpoints that return 204 No Content. */
  parse?: Parser<T>;
}

/** Join a base URL and an API path without doubling or dropping slashes. */
export function buildApiUrl(baseUrl: string, path: string): string {
  const base = baseUrl.replace(/\/+$/, "");
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${base}${suffix}`;
}

/** Versioned endpoint URL, e.g. apiV1Url(base, "/health") -> base/api/v1/health. */
export function apiV1Url(baseUrl: string, path: string): string {
  return buildApiUrl(baseUrl, `${API_V1_PREFIX}${path.startsWith("/") ? path : `/${path}`}`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Message from the backend's standard error envelope, if present. */
export function errorEnvelopeMessage(json: unknown): string | null {
  if (isRecord(json) && isRecord(json.error) && typeof json.error.message === "string") {
    return json.error.message;
  }
  return null;
}

/** Machine-readable code from the backend's error envelope, if present. */
export function errorEnvelopeCode(json: unknown): string | null {
  if (isRecord(json) && isRecord(json.error) && typeof json.error.code === "string") {
    return json.error.code;
  }
  return null;
}

export async function send<T>(
  url: string,
  { method = "GET", body, parse, timeoutMs = DEFAULT_TIMEOUT_MS, acceptStatuses = [] }: SendOptions<T> = {},
): Promise<FetchResult<T>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const headers: Record<string, string> = { Accept: "application/json" };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body; // the browser sets the multipart boundary header
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(url, { method, headers, body: payload, cache: "no-store", signal: controller.signal });
  } catch (err) {
    const aborted = err instanceof Error && err.name === "AbortError";
    return {
      kind: "unreachable",
      message: aborted ? `No response within ${timeoutMs} ms` : "Backend is not reachable",
    };
  } finally {
    clearTimeout(timer);
  }

  const requestId = response.headers.get("x-request-id");
  if (response.status === 204 && !parse) {
    return { kind: "ok", data: null as T, httpStatus: 204, requestId };
  }

  let json: unknown;
  try {
    json = await response.json();
  } catch {
    return { kind: "invalid", message: "Response was not valid JSON", httpStatus: response.status };
  }

  if (!response.ok && !acceptStatuses.includes(response.status)) {
    const code = errorEnvelopeCode(json);
    return {
      kind: "invalid",
      message: errorEnvelopeMessage(json) ?? `Unexpected HTTP ${response.status}`,
      httpStatus: response.status,
      ...(code ? { code } : {}),
    };
  }

  if (!parse) {
    return { kind: "ok", data: null as T, httpStatus: response.status, requestId };
  }
  const data = parse(json);
  if (data === null) {
    return { kind: "invalid", message: "Response did not match the expected schema", httpStatus: response.status };
  }
  return { kind: "ok", data, httpStatus: response.status, requestId };
}

export function getJson<T>(url: string, parse: Parser<T>, options: RequestOptions = {}): Promise<FetchResult<T>> {
  return send(url, { ...options, parse });
}

/** API client bound to one base URL. */
export interface ApiClient {
  readonly baseUrl: string;
  get<T>(path: string, parse: Parser<T>, options?: RequestOptions): Promise<FetchResult<T>>;
  post<T>(path: string, body: unknown, parse: Parser<T>, options?: RequestOptions): Promise<FetchResult<T>>;
  put<T>(path: string, body: unknown, parse: Parser<T>, options?: RequestOptions): Promise<FetchResult<T>>;
  delete(path: string, options?: RequestOptions): Promise<FetchResult<null>>;
}

export function createApiClient(baseUrl: string): ApiClient {
  return {
    baseUrl,
    get: (path, parse, options) => getJson(apiV1Url(baseUrl, path), parse, options),
    post: (path, body, parse, options) => send(apiV1Url(baseUrl, path), { ...options, method: "POST", body, parse }),
    put: (path, body, parse, options) => send(apiV1Url(baseUrl, path), { ...options, method: "PUT", body, parse }),
    delete: (path, options) => send<null>(apiV1Url(baseUrl, path), { ...options, method: "DELETE" }),
  };
}

/** Client for the configured backend, or `null` when the base URL is invalid. */
export const apiClient: ApiClient | null = API_BASE_URL ? createApiClient(API_BASE_URL) : null;
