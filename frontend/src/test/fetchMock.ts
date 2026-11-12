import { vi } from "vitest";

export const HEALTH_OK = {
  status: "ok",
  service: "Candidate Intelligence API",
  version: "0.1.0",
  environment: "test",
  timestamp: "2026-11-11T10:00:00Z",
};

export const READY_OK = {
  status: "ready",
  checks: {
    postgres: { status: "ok", latency_ms: 1.25, error: null },
    redis: { status: "ok", latency_ms: 0.5, error: null },
  },
  timestamp: "2026-11-11T10:00:00Z",
};

export const READY_PG_DOWN = {
  status: "not_ready",
  checks: {
    postgres: { status: "error", latency_ms: null, error: "ConnectionRefusedError" },
    redis: { status: "ok", latency_ms: 0.5, error: null },
  },
  timestamp: "2026-11-11T10:00:00Z",
};

export function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

type Handler = Response | Error | (() => Response | Promise<Response>);

/** Stub global fetch, routing by URL suffix. */
export function mockFetch(routes: Record<string, Handler>) {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const key = Object.keys(routes).find((suffix) => url.endsWith(suffix));
    if (!key) throw new Error(`Unexpected fetch: ${url}`);
    const handler = routes[key];
    if (handler instanceof Error) throw handler;
    if (typeof handler === "function") return handler();
    return handler.clone();
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}
