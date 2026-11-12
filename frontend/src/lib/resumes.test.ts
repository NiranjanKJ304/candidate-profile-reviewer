import { describe, expect, it } from "vitest";

import { createApiClient } from "@/lib/apiClient";
import { jsonResponse, mockFetch } from "@/test/fetchMock";
import { CANDIDATE, CAPS, DETAIL_COMPLETED, PARSED, RID, SUMMARY_PENDING, detail } from "@/test/resumeFixtures";

import {
  checkFile,
  createCandidate,
  deleteResume,
  parseCapabilities,
  parseResumeDetail,
  parseResumeSummary,
  resumeFileUrl,
  uploadResume,
} from "./resumes";

const api = createApiClient("http://api.test");

describe("parsers", () => {
  it("parses a completed resume detail", () => {
    const d = parseResumeDetail(DETAIL_COMPLETED);
    expect(d?.parse_status).toBe("completed");
    expect(d?.parsed_data?.technical_skills.map((s) => s.name)).toEqual(["Python", "FastAPI"]);
    expect(d?.parse_warnings).toHaveLength(1);
  });

  it("treats missing sections as empty and missing values as null", () => {
    const d = parseResumeDetail(detail({ parse_status: "completed", parsed_data: { personal_details: {} } }));
    expect(d?.parsed_data?.education).toEqual([]);
    expect(d?.parsed_data?.personal_details.email).toBeNull();
  });

  it.each([
    ["unknown status", detail({ parse_status: "done" })],
    ["unknown extraction method", detail({ extraction_method: "magic" })],
    ["skill without name", detail({ parsed_data: { ...PARSED, technical_skills: [{ category: "x" }] } })],
    ["section not a list", detail({ parsed_data: { ...PARSED, education: "x" } })],
    ["missing id", { ...SUMMARY_PENDING, id: undefined }],
    ["size as string", { ...SUMMARY_PENDING, file_size_bytes: "2048" }],
    ["not an object", ["a"]],
  ])("rejects %s", (_label, payload) => {
    expect(parseResumeDetail(payload)).toBeNull();
  });

  it("parses summaries and capabilities", () => {
    expect(parseResumeSummary(SUMMARY_PENDING)?.parse_status).toBe("pending");
    expect(parseCapabilities(CAPS)?.llm.configured).toBe(true);
    expect(parseCapabilities({ ...CAPS, llm: { ...CAPS.llm, configured: "yes" } })).toBeNull();
  });
});

describe("checkFile", () => {
  const file = (name: string, size: number) => new File([new Uint8Array(size)], name);
  it.each([
    ["cv.pdf", 100, null],
    ["CV.DOCX", 100, null],
    ["cv.txt", 100, "Only PDF"],
    ["cv.doc", 100, "Only PDF"],
    ["cv.pdf", 0, "empty"],
    ["cv.pdf", 2048, "larger than"],
  ])("%s (%i bytes)", (name, size, expected) => {
    const result = checkFile(file(name, size), 1024);
    if (expected === null) expect(result).toBeNull();
    else expect(result).toContain(expected);
  });
});

describe("calls", () => {
  it("creates a candidate with only provided fields", async () => {
    const fetchFn = mockFetch({ "/api/v1/candidates": jsonResponse(CANDIDATE, 201) });
    const r = await createCandidate(api, { full_name: "Jordan Sample", email: null });
    expect(r.kind).toBe("ok");
    const [, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ full_name: "Jordan Sample" });
  });

  it("uploads as multipart form data with a long timeout", async () => {
    const fetchFn = mockFetch({ [`/candidates/${CANDIDATE.id}/resumes`]: jsonResponse(SUMMARY_PENDING, 202) });
    const r = await uploadResume(api, CANDIDATE.id, new File(["%PDF-1.7"], "cv.pdf"));
    expect(r.kind).toBe("ok");
    const [, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("file")).toBeInstanceOf(File);
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("surfaces the server's error code and message on rejection", async () => {
    mockFetch({
      [`/candidates/${CANDIDATE.id}/resumes`]: jsonResponse(
        { error: { code: "encrypted_file", message: "Password-protected PDFs are not supported" } },
        422,
      ),
    });
    const r = await uploadResume(api, CANDIDATE.id, new File(["x"], "cv.pdf"));
    expect(r).toEqual({
      kind: "invalid",
      code: "encrypted_file",
      message: "Password-protected PDFs are not supported",
      httpStatus: 422,
    });
  });

  it("deletes and accepts 204 No Content", async () => {
    mockFetch({ [`/resumes/${RID}`]: new Response(null, { status: 204 }) });
    const r = await deleteResume(api, RID);
    expect(r).toMatchObject({ kind: "ok", httpStatus: 204, data: null });
  });

  it("builds the download URL", () => {
    expect(resumeFileUrl(api, RID)).toBe(`http://api.test/api/v1/resumes/${RID}/file`);
  });
});
