import { describe, expect, it } from "vitest";

import { createApiClient } from "@/lib/apiClient";
import { jsonResponse, mockFetch } from "@/test/fetchMock";
import { DETAIL_COMPLETED, JD_ID, PARSED_JD, SUMMARY_PENDING, page, parsedResponse } from "@/test/jdFixtures";

import {
  checkJDFile,
  deleteJobDescription,
  jobDescriptionFileUrl,
  linesToSkills,
  linesToTexts,
  parseJDDetail,
  parseJDPage,
  parseParsedResponse,
  saveReview,
  skillsToLines,
  uploadJobDescription,
} from "./jobDescriptions";

const api = createApiClient("http://api.test");

describe("parsers", () => {
  it("parses detail, page and parsed response", () => {
    expect(parseJDDetail(DETAIL_COMPLETED)?.title).toBe("Senior Backend Engineer");
    expect(parseJDPage(page([SUMMARY_PENDING]))?.items[0].parse_status).toBe("pending");
    const parsed = parseParsedResponse(parsedResponse());
    expect(parsed?.data.required_skills.map((s) => s.name)).toEqual(["Python", "PostgreSQL"]);
    expect(parsed?.data.minimum_experience?.years_min).toBe(5);
    expect(parsed?.technology_summary.databases).toEqual(["PostgreSQL"]);
  });

  it("treats missing sections as empty", () => {
    const parsed = parseParsedResponse(parsedResponse({ data: { job_title: "X" }, extracted: { job_title: "X" } }));
    expect(parsed?.data.required_skills).toEqual([]);
    expect(parsed?.data.minimum_experience).toBeNull();
    expect(parsed?.data.seniority).toBeNull();
  });

  it.each([
    ["bad skill category", parsedResponse({ data: { ...PARSED_JD, required_skills: [{ name: "x", category: "spoken" }] } })],
    ["bad seniority", parsedResponse({ data: { ...PARSED_JD, seniority: { level: "wizard" } } })],
    ["bad source", parsedResponse({ source: "guessed" })],
    ["years as text", parsedResponse({ data: { ...PARSED_JD, minimum_experience: { years_min: "five" } } })],
    ["missing technology summary", { ...parsedResponse(), technology_summary: undefined }],
  ])("rejects %s", (_label, payload) => {
    expect(parseParsedResponse(payload)).toBeNull();
  });

  it("rejects an unknown extraction method", () => {
    expect(parseJDDetail({ ...DETAIL_COMPLETED, extraction_method: "magic" })).toBeNull();
  });
});

describe("checkJDFile", () => {
  const f = (name: string, size: number) => new File([new Uint8Array(size)], name);
  it.each([
    ["jd.pdf", 10, null],
    ["jd.DOCX", 10, null],
    ["jd.txt", 10, null],
    ["jd.rtf", 10, "Only PDF"],
    ["jd.txt", 0, "empty"],
    ["jd.txt", 5000, "larger than"],
  ])("%s", (name, size, expected) => {
    const result = checkJDFile(f(name, size), 1024);
    if (expected === null) expect(result).toBeNull();
    else expect(result).toContain(expected);
  });
});

describe("review line editing", () => {
  it("round-trips skills and keeps evidence for unchanged names", () => {
    const lines = skillsToLines(PARSED_JD.required_skills as never);
    expect(lines).toBe("Python | language\nPostgreSQL | database");
    const edited = linesToSkills(`${lines}\nRedis | database\npython`, PARSED_JD.required_skills as never);
    expect(edited.map((s) => s.name)).toEqual(["Python", "PostgreSQL", "Redis"]);
    expect(edited[0].evidence).toBe("Strong experience with Python and FastAPI");
    expect(edited[2]).toEqual({ name: "Redis", category: "database", evidence: null });
  });

  it("ignores unknown categories and blank lines", () => {
    expect(linesToSkills("Go | spoken\n\n  \n", [])).toEqual([{ name: "Go", category: null, evidence: null }]);
  });

  it("keeps evidence only for unchanged text items", () => {
    const prev = PARSED_JD.responsibilities;
    const out = linesToTexts("Design and build REST APIs in Python\nMentor engineers", prev);
    expect(out[0].evidence).toBe("Design and build REST APIs in Python");
    expect(out[1]).toEqual({ text: "Mentor engineers", evidence: null });
  });
});

describe("calls", () => {
  it("uploads multipart to /upload", async () => {
    const fetchFn = mockFetch({ "/job-descriptions/upload": jsonResponse(SUMMARY_PENDING, 202) });
    const r = await uploadJobDescription(api, new File(["Senior Backend Engineer"], "role.txt"));
    expect(r.kind).toBe("ok");
    const [url, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://api.test/api/v1/job-descriptions/upload");
    expect(init.body).toBeInstanceOf(FormData);
  });

  it("saves a review with PUT", async () => {
    const fetchFn = mockFetch({ [`/job-descriptions/${JD_ID}/parsed`]: jsonResponse(parsedResponse({ source: "reviewed" })) });
    const r = await saveReview(api, JD_ID, PARSED_JD as never);
    expect(r.kind === "ok" && r.data.source).toBe("reviewed");
    const [, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body)).job_title).toBe("Senior Backend Engineer");
  });

  it("surfaces conflicts such as a JD used by evaluations", async () => {
    mockFetch({
      [`/job-descriptions/${JD_ID}`]: jsonResponse(
        { error: { code: "job_description_in_use", message: "Referenced by evaluations" } },
        409,
      ),
    });
    expect(await deleteJobDescription(api, JD_ID)).toEqual({
      kind: "invalid",
      code: "job_description_in_use",
      message: "Referenced by evaluations",
      httpStatus: 409,
    });
  });

  it("builds the download URL", () => {
    expect(jobDescriptionFileUrl(api, JD_ID)).toBe(`http://api.test/api/v1/job-descriptions/${JD_ID}/file`);
  });
});
