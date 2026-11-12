import { describe, expect, it } from "vitest";

import { createApiClient } from "@/lib/apiClient";
import { jsonResponse, mockFetch } from "@/test/fetchMock";
import { CID, SNAPSHOT, STATUS_ENABLED, profile } from "@/test/githubFixtures";

import {
  addGitHubProfile,
  checkGitHubInput,
  parseCandidatePage,
  parseGitHubProfile,
  parseGitHubStatus,
  removeGitHubProfile,
} from "./github";

const api = createApiClient("http://api.test");

describe("parsers", () => {
  it("parses a collected profile with sections and provenance", () => {
    const p = parseGitHubProfile(profile());
    expect(p?.data?.profile.data?.login).toBe("octo-dev");
    expect(p?.data?.repositories.items).toHaveLength(2);
    expect(p?.data?.repositories.items[1].is_fork).toBe(true);
    expect(p?.data?.issues.status).toBe("rate_limited");
    expect(p?.data?.profile.source_url).toBe("https://github.com/octo-dev");
  });

  it("accepts a profile that has not been collected yet", () => {
    const p = parseGitHubProfile(profile({ data: null, collection_status: "not_collected", sync_status: "pending" }));
    expect(p?.data).toBeNull();
    expect(p?.sync_status).toBe("pending");
  });

  it.each([
    ["unknown sync status", profile({ sync_status: "done" })],
    ["unknown availability", profile({ data: { ...SNAPSHOT, topics: { ...SNAPSHOT.topics, status: "maybe" } } })],
    ["repository without url", profile({ data: { ...SNAPSHOT, repositories: { ...SNAPSHOT.repositories, items: [{ name: "x" }] } } })],
    ["unknown ownership", profile({ ownership_status: "assumed" })],
  ])("rejects %s", (_label, payload) => {
    expect(parseGitHubProfile(payload)).toBeNull();
  });

  it("parses status and candidate pages", () => {
    expect(parseGitHubStatus(STATUS_ENABLED)?.rate_limits[0].remaining).toBe(52);
    expect(parseCandidatePage({ items: [{ id: CID, full_name: "A", email: null }], total: 1 })?.total).toBe(1);
  });
});

describe("checkGitHubInput", () => {
  it.each([
    ["octocat", null],
    ["https://github.com/octocat", null],
    ["", "Enter"],
    ["octo cat", "spaces"],
    ["x".repeat(301), "too long"],
  ])("%s", (value, expected) => {
    const result = checkGitHubInput(value);
    if (expected === null) expect(result).toBeNull();
    else expect(result).toContain(expected);
  });
});

describe("calls", () => {
  it("posts the raw profile value for server-side validation", async () => {
    const fetchFn = mockFetch({ [`/candidates/${CID}/github`]: jsonResponse(profile({ sync_status: "pending", data: null }), 202) });
    const r = await addGitHubProfile(api, CID, "https://github.com/octo-dev");
    expect(r.kind).toBe("ok");
    const [, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ profile: "https://github.com/octo-dev" });
  });

  it("surfaces validation codes", async () => {
    mockFetch({
      [`/candidates/${CID}/github`]: jsonResponse(
        { error: { code: "unsupported_domain", message: "Only github.com profile URLs are accepted" } },
        422,
      ),
    });
    const r = await addGitHubProfile(api, CID, "https://gitlab.com/x");
    expect(r).toMatchObject({ kind: "invalid", code: "unsupported_domain", httpStatus: 422 });
  });

  it("removes with DELETE", async () => {
    mockFetch({ [`/candidates/${CID}/github`]: new Response(null, { status: 204 }) });
    expect(await removeGitHubProfile(api, CID)).toMatchObject({ kind: "ok", httpStatus: 204 });
  });
});
