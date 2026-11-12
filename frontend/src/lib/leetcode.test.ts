import { describe, expect, it } from "vitest";

import { createApiClient } from "@/lib/apiClient";
import {
  EMPTY_FORM,
  addLeetCodeProfile,
  buildEvidencePayload,
  checkLeetCodeInput,
  formFromEvidence,
  parseLeetCodeProfile,
  parseLeetCodeStatus,
  saveLeetCodeEvidence,
} from "@/lib/leetcode";
import { jsonResponse, mockFetch } from "@/test/fetchMock";
import { CID, EVIDENCE, LC_STATUS, lcProfile } from "@/test/leetcodeFixtures";

const api = createApiClient("http://api.test");
const LC = `/candidates/${CID}/leetcode`;

describe("parseLeetCodeProfile", () => {
  it("parses self-reported evidence and keeps unknowns as null", () => {
    const p = parseLeetCodeProfile(lcProfile());
    expect(p?.platform_data.status).toBe("unavailable");
    expect(p?.evidence?.verification_status).toBe("not_verified");
    expect(p?.evidence?.solved.total).toBe(300);
    expect(p?.evidence?.contest.rating).toBeNull();
    expect(p?.evidence?.contest.status).toBe("not_collected");
    expect(p?.evidence?.languages.items[1].problems_solved).toBeNull();
    expect(p?.evidence?.solved.reported_by).toBe("candidate");
  });

  it("parses a profile without evidence", () => {
    expect(parseLeetCodeProfile(lcProfile({ evidence: null }))?.evidence).toBeNull();
  });

  it.each([
    ["evidence claiming to be verified", lcProfile({ evidence: { ...EVIDENCE, verification_status: "verified" } })],
    ["evidence claiming a platform source", lcProfile({ evidence: { ...EVIDENCE, source_type: "platform_api" } })],
    [
      "a section claiming verification",
      lcProfile({ evidence: { ...EVIDENCE, solved: { ...EVIDENCE.solved, provenance: { ...EVIDENCE.solved.provenance, verification_status: "verified" } } } }),
    ],
    ["platform data claiming availability", lcProfile({ platform_data: { status: "available", reason: "x", sources: [] } })],
    ["missing freshness", lcProfile({ freshness: undefined })],
  ])("rejects %s", (_name, payload) => {
    expect(parseLeetCodeProfile(payload)).toBeNull();
  });

  it("parses the status", () => {
    expect(parseLeetCodeStatus(LC_STATUS)?.sources).toEqual(["https://leetcode.com/terms/", "https://leetcode.com/robots.txt"]);
    expect(parseLeetCodeStatus({})).toBeNull();
  });
});

describe("buildEvidencePayload", () => {
  it("omits empty fields (not provided is never zero)", () => {
    const { payload, errors } = buildEvidencePayload({ ...EMPTY_FORM, reported_by: "candidate", total_solved: "42", hard_solved: "0" });
    expect(errors).toEqual([]);
    expect(payload).toEqual({ reported_by: "candidate", total_solved: 42, hard_solved: 0 });
  });

  it("parses lists and contest history", () => {
    const { payload } = buildEvidencePayload({
      ...EMPTY_FORM,
      reported_by: "recruiter",
      as_of: "2026-11-01",
      languages: "Python3: 250\nC++\n",
      topics: "Dynamic Programming: 60",
      contest_history: "Weekly Contest 400 | 1234 | 1750 | 2026-10-20\nBiweekly 120",
      contest_top_percentage: "12.5",
      notes: "  hello ",
    });
    expect(payload).toEqual({
      reported_by: "recruiter",
      as_of: "2026-11-01",
      contest_top_percentage: 12.5,
      languages: [
        { name: "Python3", problems_solved: 250 },
        { name: "C++", problems_solved: null },
      ],
      topics: [{ name: "Dynamic Programming", problems_solved: 60 }],
      contest_history: [
        { contest_name: "Weekly Contest 400", rank: 1234, rating_after: 1750, contest_date: "2026-10-20" },
        { contest_name: "Biweekly 120" },
      ],
      notes: "hello",
    });
  });

  it.each([
    [{ total_solved: "5" }, "Choose who is reporting"],
    [{ reported_by: "candidate" }, "at least one evidence value"],
    [{ reported_by: "candidate", total_solved: "-3" }, "whole number"],
    [{ reported_by: "candidate", total_solved: "1.5" }, "whole number"],
    [{ reported_by: "candidate", total_solved: "10", easy_solved: "8", medium_solved: "8" }, "cannot exceed"],
    [{ reported_by: "candidate", total_solved: "10", easy_solved: "1", medium_solved: "1", hard_solved: "1" }, "must equal"],
    [{ reported_by: "candidate", contest_top_percentage: "150" }, "between 0 and 100"],
    [{ reported_by: "candidate", contest_history: " | 5" }, "needs a contest name"],
  ] as const)("rejects %j", (fields, message) => {
    const { payload, errors } = buildEvidencePayload({ ...EMPTY_FORM, ...fields });
    expect(payload).toBeNull();
    expect(errors.join(" ")).toContain(message);
  });

  it("round-trips stored evidence through the edit form", () => {
    const ev = parseLeetCodeProfile(lcProfile())!.evidence!;
    const form = formFromEvidence(ev);
    expect(form.languages).toBe("Python3: 250\nC++");
    expect(form.contest_history).toBe("Weekly Contest 400 | 1234 | 1750 | 2026-10-20");
    expect(form.contest_rating).toBe("");
    const { payload } = buildEvidencePayload(form);
    expect(payload?.total_solved).toBe(300);
    expect(payload).not.toHaveProperty("contest_rating");
  });
});

describe("calls", () => {
  it("checks input", () => {
    expect(checkLeetCodeInput("")).toContain("Enter");
    expect(checkLeetCodeInput("a b")).toContain("spaces");
    expect(checkLeetCodeInput("jane_doe")).toBeNull();
  });

  it("surfaces server validation details", async () => {
    mockFetch({
      [`${LC}/evidence`]: jsonResponse(
        { error: { code: "validation_error", message: "Request validation failed", details: [{ loc: ["body"], msg: "Value error, easy + medium + hard solved must equal total solved", type: "value_error" }] } },
        422,
      ),
    });
    const r = await saveLeetCodeEvidence(api, CID, { reported_by: "candidate", total_solved: 1 });
    expect(r.kind).toBe("invalid");
    if (r.kind === "invalid") expect(r.details).toEqual(["Value error, easy + medium + hard solved must equal total solved"]);
  });

  it("posts the raw profile value", async () => {
    const fetchFn = mockFetch({ [LC]: jsonResponse(lcProfile({ evidence: null }), 201) });
    expect((await addLeetCodeProfile(api, CID, "https://leetcode.com/u/jane_doe/")).kind).toBe("ok");
    const init = (fetchFn.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(JSON.parse(String(init.body))).toEqual({ profile: "https://leetcode.com/u/jane_doe/" });
  });
});
