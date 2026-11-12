import { afterEach, describe, expect, it, vi } from "vitest";

import { HEALTH_OK, READY_OK, READY_PG_DOWN, jsonResponse, mockFetch } from "@/test/fetchMock";

import { fetchEndpoint, fetchHealth, fetchReadiness, parseHealth, parseReadiness } from "./status";

const BASE = "http://api.test";

describe("parseHealth", () => {
  it("accepts a valid payload", () => {
    expect(parseHealth(HEALTH_OK)).toEqual(HEALTH_OK);
  });

  it.each([
    ["null", null],
    ["array", []],
    ["string", "ok"],
    ["wrong status", { ...HEALTH_OK, status: "degraded" }],
    ["missing version", { ...HEALTH_OK, version: undefined }],
    ["numeric version", { ...HEALTH_OK, version: 1 }],
  ])("rejects %s", (_label, payload) => {
    expect(parseHealth(payload)).toBeNull();
  });
});

describe("parseReadiness", () => {
  it("parses a ready payload and maps latency_ms", () => {
    const parsed = parseReadiness(READY_OK);
    expect(parsed?.status).toBe("ready");
    expect(parsed?.checks.postgres).toEqual({ status: "ok", latencyMs: 1.25, error: null });
  });

  it("parses a not_ready payload", () => {
    const parsed = parseReadiness(READY_PG_DOWN);
    expect(parsed?.status).toBe("not_ready");
    expect(parsed?.checks.postgres).toEqual({
      status: "error",
      latencyMs: null,
      error: "ConnectionRefusedError",
    });
  });

  it("treats missing optional fields as null (unavailable, not zero)", () => {
    const parsed = parseReadiness({ ...READY_OK, checks: { redis: { status: "timeout" } } });
    expect(parsed?.checks.redis).toEqual({ status: "timeout", latencyMs: null, error: null });
  });

  it("accepts an empty checks object", () => {
    expect(parseReadiness({ ...READY_OK, checks: {} })?.checks).toEqual({});
  });

  it.each([
    ["unknown overall status", { ...READY_OK, status: "maybe" }],
    ["checks not an object", { ...READY_OK, checks: [] }],
    ["missing timestamp", { ...READY_OK, timestamp: undefined }],
    ["unknown check status", { ...READY_OK, checks: { postgres: { status: "down" } } }],
    ["latency as string", { ...READY_OK, checks: { postgres: { status: "ok", latency_ms: "1" } } }],
    ["error as object", { ...READY_OK, checks: { postgres: { status: "error", error: {} } } }],
  ])("rejects %s", (_label, payload) => {
    expect(parseReadiness(payload)).toBeNull();
  });
});

describe("fetchEndpoint", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns data, status and request id on success", async () => {
    const fetchFn = mockFetch({ "/api/v1/health": jsonResponse(HEALTH_OK, 200, { "x-request-id": "rid-1" }) });
    const result = await fetchHealth(BASE);
    expect(result).toEqual({ kind: "ok", data: HEALTH_OK, httpStatus: 200, requestId: "rid-1" });
    expect(fetchFn).toHaveBeenCalledWith(
      `${BASE}/api/v1/health`,
      expect.objectContaining({ method: "GET", cache: "no-store" }),
    );
  });

  it("accepts a 503 readiness body as a valid not_ready answer", async () => {
    mockFetch({ "/api/v1/ready": jsonResponse(READY_PG_DOWN, 503) });
    const result = await fetchReadiness(BASE);
    expect(result.kind).toBe("ok");
    if (result.kind === "ok") {
      expect(result.httpStatus).toBe(503);
      expect(result.data.status).toBe("not_ready");
    }
  });

  it("reports a network failure as unreachable", async () => {
    mockFetch({ "/api/v1/health": new TypeError("Failed to fetch") });
    expect(await fetchHealth(BASE)).toEqual({ kind: "unreachable", message: "Backend is not reachable" });
  });

  it("aborts and reports unreachable after the timeout", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener("abort", () =>
              reject(Object.assign(new Error("aborted"), { name: "AbortError" })),
            );
          }),
      ),
    );
    const result = await fetchHealth(BASE, 20);
    expect(result).toEqual({ kind: "unreachable", message: "No response within 20 ms" });
  });

  it("reports non-JSON bodies as invalid", async () => {
    mockFetch({ "/api/v1/health": new Response("<html>gateway</html>", { status: 502 }) });
    expect(await fetchHealth(BASE)).toEqual({
      kind: "invalid",
      message: "Response was not valid JSON",
      httpStatus: 502,
    });
  });

  it("uses the backend error envelope message for HTTP errors", async () => {
    mockFetch({
      "/api/v1/health": jsonResponse(
        { error: { code: "internal_error", message: "An unexpected error occurred" } },
        500,
      ),
    });
    expect(await fetchHealth(BASE)).toEqual({
      kind: "invalid",
      message: "An unexpected error occurred",
      httpStatus: 500,
      code: "internal_error",
    });
  });

  it("falls back to a generic message for HTTP errors without an envelope", async () => {
    mockFetch({ "/api/v1/health": jsonResponse({ detail: "nope" }, 404) });
    const result = await fetchHealth(BASE);
    expect(result).toEqual({ kind: "invalid", message: "Unexpected HTTP 404", httpStatus: 404 });
  });

  it("does not accept a 503 for endpoints that did not opt in", async () => {
    mockFetch({ "/api/v1/health": jsonResponse(HEALTH_OK, 503) });
    expect((await fetchHealth(BASE)).kind).toBe("invalid");
  });

  it("reports schema mismatches as invalid", async () => {
    mockFetch({ "/x": jsonResponse({ unexpected: true }) });
    const result = await fetchEndpoint(`${BASE}/x`, parseHealth);
    expect(result).toEqual({
      kind: "invalid",
      message: "Response did not match the expected schema",
      httpStatus: 200,
    });
  });
});
