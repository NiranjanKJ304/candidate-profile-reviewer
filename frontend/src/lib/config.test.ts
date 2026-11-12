import { describe, expect, it } from "vitest";

import { DEFAULT_API_BASE_URL, resolveApiBaseUrl } from "./config";

describe("resolveApiBaseUrl", () => {
  it("defaults when unset or blank", () => {
    expect(resolveApiBaseUrl(undefined)).toBe(DEFAULT_API_BASE_URL);
    expect(resolveApiBaseUrl("   ")).toBe(DEFAULT_API_BASE_URL);
  });

  it("accepts http and https URLs", () => {
    expect(resolveApiBaseUrl("http://localhost:8000")).toBe("http://localhost:8000");
    expect(resolveApiBaseUrl("https://api.example.com")).toBe("https://api.example.com");
  });

  it("strips trailing slashes and keeps a path prefix", () => {
    expect(resolveApiBaseUrl("http://localhost:8000/")).toBe("http://localhost:8000");
    expect(resolveApiBaseUrl("https://example.com/backend//")).toBe("https://example.com/backend");
  });

  it("drops query strings and fragments", () => {
    expect(resolveApiBaseUrl("http://localhost:8000/?x=1#y")).toBe("http://localhost:8000");
  });

  it.each(["localhost:8000", "not a url", "ftp://example.com", "javascript:alert(1)", "file:///etc/passwd"])(
    "rejects invalid value %s",
    (value) => {
      expect(resolveApiBaseUrl(value)).toBeNull();
    },
  );

  it("rejects URLs with embedded credentials", () => {
    expect(resolveApiBaseUrl("https://user:pass@api.example.com")).toBeNull();
  });
});
