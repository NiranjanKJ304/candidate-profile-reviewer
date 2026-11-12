import { describe, expect, it } from "vitest";

import { HEALTH_OK, jsonResponse, mockFetch } from "@/test/fetchMock";

import { apiV1Url, buildApiUrl, createApiClient, errorEnvelopeMessage } from "./apiClient";
import { parseHealth } from "./status";

describe("buildApiUrl", () => {
  it.each([
    ["http://api.test", "/x", "http://api.test/x"],
    ["http://api.test/", "/x", "http://api.test/x"],
    ["http://api.test//", "x", "http://api.test/x"],
    ["https://example.com/backend", "/api/v1/health", "https://example.com/backend/api/v1/health"],
  ])("joins %s + %s", (base, path, expected) => {
    expect(buildApiUrl(base, path)).toBe(expected);
  });
});

describe("apiV1Url", () => {
  it("adds the version prefix", () => {
    expect(apiV1Url("http://api.test", "/health")).toBe("http://api.test/api/v1/health");
    expect(apiV1Url("http://api.test/", "ready")).toBe("http://api.test/api/v1/ready");
  });
});

describe("errorEnvelopeMessage", () => {
  it("extracts the message from the standard envelope", () => {
    expect(errorEnvelopeMessage({ error: { code: "x", message: "Boom" } })).toBe("Boom");
  });

  it.each([null, "text", [], {}, { error: "flat" }, { error: { message: 42 } }])(
    "returns null for %j",
    (value) => {
      expect(errorEnvelopeMessage(value)).toBeNull();
    },
  );
});

describe("createApiClient", () => {
  it("issues versioned GET requests against its base URL", async () => {
    const fetchFn = mockFetch({ "/api/v1/health": jsonResponse(HEALTH_OK) });
    const client = createApiClient("http://api.test/");
    const result = await client.get("/health", parseHealth);

    expect(client.baseUrl).toBe("http://api.test/");
    expect(result.kind).toBe("ok");
    expect(fetchFn).toHaveBeenCalledWith(
      "http://api.test/api/v1/health",
      expect.objectContaining({ method: "GET", headers: { Accept: "application/json" } }),
    );
  });
});
