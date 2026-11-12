import { describe, expect, it } from "vitest";

import { createApiClient } from "@/lib/apiClient";
import {
  addCodeforcesProfile,
  checkCodeforcesInput,
  parseCodeforcesProfile,
  parseCodeforcesStatus,
  removeCodeforcesProfile,
  syncCodeforcesProfile,
} from "@/lib/codeforces";
import { CF_STATUS_ENABLED, CID, SNAPSHOT, cfProfile } from "@/test/codeforcesFixtures";
import { jsonResponse, mockFetch } from "@/test/fetchMock";

const api = createApiClient("http://api.test");
const CF = `/candidates/${CID}/codeforces`;

describe("parseCodeforcesProfile", () => {
  it("parses a collected profile with analytics", () => {
    const p = parseCodeforcesProfile(cfProfile());
    expect(p?.handle).toBe("algo_dev");
    expect(p?.freshness.stale).toBe(false);
    expect(p?.data?.profile.data?.rating).toBe(1650);
    expect(p?.data?.profile.api_method).toBe("user.info");
    expect(p?.data?.analytics.lifetime.rated_contests).toBe(2);
    expect(p?.data?.analytics.submissions?.accepted_unique_problems).toBe(3);
    expect(p?.data?.analytics.submissions?.accepted_by_rating.at(-1)?.rating).toBeNull();
    expect(p?.data?.rating_history.items[1].delta).toBe(-50);
    expect(p?.data?.submissions.items[0].problem_url).toContain("/contest/1010/problem/C");
  });

  it("keeps unavailable statistics as null (not zero)", () => {
    const p = parseCodeforcesProfile(
      cfProfile({
        data: {
          ...SNAPSHOT,
          submissions: { ...SNAPSHOT.submissions, status: "network_error", items: [] },
          analytics: { ...SNAPSHOT.analytics, status: "network_error", submissions: null },
        },
      }),
    );
    expect(p?.data?.analytics.submissions).toBeNull();
    expect(p?.data?.submissions.status).toBe("network_error");
  });

  it("parses a profile that has not been collected", () => {
    const p = parseCodeforcesProfile(cfProfile({ data: null, collected_at: null, sync_status: "pending" }));
    expect(p?.data).toBeNull();
    expect(p?.sync_status).toBe("pending");
  });

  it.each([
    ["missing handle", { ...cfProfile(), handle: undefined }],
    ["unknown sync status", cfProfile({ sync_status: "exploded" })],
    ["unknown availability", cfProfile({ data: { ...SNAPSHOT, rating_history: { ...SNAPSHOT.rating_history, status: "maybe" } } })],
    ["non-numeric stat", cfProfile({ data: { ...SNAPSHOT, analytics: { ...SNAPSHOT.analytics, submissions: { ...SNAPSHOT.analytics.submissions, total_submissions: "6" } } } })],
    ["missing freshness", cfProfile({ freshness: undefined })],
  ])("rejects %s", (_name, payload) => {
    expect(parseCodeforcesProfile(payload)).toBeNull();
  });

  it("parses the integration status", () => {
    expect(parseCodeforcesStatus(CF_STATUS_ENABLED)).toEqual({
      enabled: true,
      min_request_interval_seconds: 2,
      submission_limit: 500,
      last_error: null,
    });
    expect(parseCodeforcesStatus({ enabled: "yes" })).toBeNull();
  });
});

describe("checkCodeforcesInput", () => {
  it.each([
    ["", "Enter a Codeforces handle"],
    ["tour ist", "spaces"],
    ["a".repeat(301), "too long"],
  ])("rejects %j", (value, message) => {
    expect(checkCodeforcesInput(value)).toContain(message);
  });
  it("accepts handles and URLs (server validates)", () => {
    expect(checkCodeforcesInput(" tourist ")).toBeNull();
    expect(checkCodeforcesInput("https://codeforces.com/profile/tourist")).toBeNull();
  });
});

describe("calls", () => {
  it("posts the raw value and surfaces validation codes", async () => {
    const fetchFn = mockFetch({
      [CF]: jsonResponse({ error: { code: "not_a_profile", message: "This is not a profile URL" } }, 422),
    });
    const r = await addCodeforcesProfile(api, CID, "https://codeforces.com/contest/1");
    expect(r.kind).toBe("invalid");
    if (r.kind === "invalid") expect(r.code).toBe("not_a_profile");
    const init = (fetchFn.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(JSON.parse(String(init.body))).toEqual({ profile: "https://codeforces.com/contest/1" });
  });

  it("uses /retry after a failure and /sync otherwise", async () => {
    const fetchFn = mockFetch({
      [`${CF}/retry`]: jsonResponse(cfProfile({ sync_status: "pending" }), 202),
      [`${CF}/sync`]: jsonResponse(cfProfile({ sync_status: "pending" }), 202),
    });
    expect((await syncCodeforcesProfile(api, CID, true)).kind).toBe("ok");
    expect((await syncCodeforcesProfile(api, CID, false)).kind).toBe("ok");
    expect(fetchFn.mock.calls.map(([u]) => String(u).split("/").at(-1))).toEqual(["retry", "sync"]);
  });

  it("deletes with 204", async () => {
    mockFetch({ [CF]: () => new Response(null, { status: 204 }) });
    expect((await removeCodeforcesProfile(api, CID)).kind).toBe("ok");
  });
});
