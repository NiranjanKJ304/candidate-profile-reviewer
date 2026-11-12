import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { jsonResponse, mockFetch } from "@/test/fetchMock";
import {
  CANDIDATE,
  CAPS,
  DETAIL_COMPLETED,
  DETAIL_FAILED,
  RID,
  SUMMARY_PENDING,
  detail,
} from "@/test/resumeFixtures";

import ParsedResumeView from "./ParsedResumeView";
import ResumeWorkspace from "./ResumeWorkspace";

const BASE = "http://api.test";

function fill(name = "Jordan Sample", file: File | null = new File(["%PDF-1.7 test"], "jordan.pdf")) {
  fireEvent.change(screen.getByLabelText("Candidate full name"), { target: { value: name } });
  if (file) fireEvent.change(screen.getByLabelText("Resume file"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Upload and parse" }));
}

/** Resume GET returns each scripted body in turn (the last one repeats). */
function sequence(...bodies: object[]) {
  let i = 0;
  return () => jsonResponse(bodies[Math.min(i++, bodies.length - 1)]);
}

describe("ResumeWorkspace", () => {
  it("uploads, polls and shows the extracted information", async () => {
    const fetchFn = mockFetch({
      "/system/capabilities": jsonResponse(CAPS),
      "/api/v1/candidates": jsonResponse(CANDIDATE, 201),
      [`/candidates/${CANDIDATE.id}/resumes`]: jsonResponse(SUMMARY_PENDING, 202),
      [`/resumes/${RID}`]: sequence(detail(), detail({ parse_status: "processing" }), DETAIL_COMPLETED),
    });
    render(<ResumeWorkspace apiBaseUrl={BASE} pollIntervalMs={5} />);
    fill();

    expect(await screen.findByText("Parsing complete")).toBeInTheDocument();
    expect(screen.getByTestId("parsed-resume")).toBeInTheDocument();
    expect(screen.getByText("Software Engineer")).toBeInTheDocument();
    expect(screen.getByText("FastAPI")).toBeInTheDocument();
    expect(screen.getByText(/Removed skill\(s\)/)).toBeInTheDocument();
    expect(screen.getByText(/PDF text layer/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download original" })).toHaveAttribute(
      "href",
      `http://api.test/api/v1/resumes/${RID}/file`,
    );
    const polls = fetchFn.mock.calls.filter(([u]) => String(u).endsWith(`/resumes/${RID}`));
    expect(polls.length).toBeGreaterThanOrEqual(3);
  });

  it("validates on the client before contacting the server", async () => {
    const fetchFn = mockFetch({ "/system/capabilities": jsonResponse(CAPS) });
    render(<ResumeWorkspace apiBaseUrl={BASE} />);
    fill("", null);
    expect(await screen.findByRole("alert")).toHaveTextContent("full name");
    fill("Jordan Sample", new File(["text"], "notes.txt"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Only PDF");
    expect(fetchFn.mock.calls.every(([u]) => String(u).endsWith("/system/capabilities"))).toBe(true);
  });

  it("shows the server's rejection message", async () => {
    mockFetch({
      "/system/capabilities": jsonResponse(CAPS),
      "/api/v1/candidates": jsonResponse(CANDIDATE, 201),
      [`/candidates/${CANDIDATE.id}/resumes`]: jsonResponse(
        { error: { code: "corrupted_file", message: "The PDF could not be opened; it may be damaged" } },
        422,
      ),
    });
    render(<ResumeWorkspace apiBaseUrl={BASE} />);
    fill();
    expect(await screen.findByRole("alert")).toHaveTextContent("Upload rejected: The PDF could not be opened");
  });

  it("shows failures and retries parsing", async () => {
    mockFetch({
      "/system/capabilities": jsonResponse({ ...CAPS, llm: { ...CAPS.llm, configured: false } }),
      "/api/v1/candidates": jsonResponse(CANDIDATE, 201),
      [`/candidates/${CANDIDATE.id}/resumes`]: jsonResponse(SUMMARY_PENDING, 202),
      [`/resumes/${RID}/parse`]: jsonResponse({ ...SUMMARY_PENDING, parse_status: "pending" }, 202),
      // 1st GET (after upload) -> failed; polling stops. After "Retry", the next GET -> completed.
      [`/resumes/${RID}`]: sequence(DETAIL_FAILED, DETAIL_COMPLETED),
    });
    render(<ResumeWorkspace apiBaseUrl={BASE} pollIntervalMs={5} />);
    expect(await screen.findByRole("note")).toHaveTextContent("GROQ_API_KEY");
    fill();

    expect(await screen.findByText("Parsing failed")).toBeInTheDocument();
    expect(screen.getByText("llm_not_configured")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry parsing" }));
    expect(await screen.findByText("Parsing complete")).toBeInTheDocument();
  });

  it("deletes the resume and returns to the upload form", async () => {
    const fetchFn = mockFetch({
      "/system/capabilities": jsonResponse(CAPS),
      "/api/v1/candidates": jsonResponse(CANDIDATE, 201),
      [`/candidates/${CANDIDATE.id}/resumes`]: jsonResponse(SUMMARY_PENDING, 202),
      [`/resumes/${RID}`]: (() => {
        let deleted = false;
        return () => {
          const call = fetchFn.mock.calls.at(-1) as unknown as [string, RequestInit];
          if (call[1]?.method === "DELETE") {
            deleted = true;
            return new Response(null, { status: 204 });
          }
          return deleted ? jsonResponse({ error: { code: "not_found", message: "x" } }, 404) : jsonResponse(DETAIL_COMPLETED);
        };
      })(),
    });
    render(<ResumeWorkspace apiBaseUrl={BASE} pollIntervalMs={5} />);
    fill();
    expect(await screen.findByText("Parsing complete")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByRole("button", { name: "Upload and parse" })).toBeInTheDocument();
    const methods = fetchFn.mock.calls.map((call) => ((call as unknown[])[1] as RequestInit | undefined)?.method);
    expect(methods).toContain("DELETE");
  });

  it("reports a configuration error without an API client", () => {
    render(<ResumeWorkspace apiBaseUrl={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent("NEXT_PUBLIC_API_BASE_URL");
  });

  it("shows an OCR notice when OCR is unavailable", async () => {
    mockFetch({ "/system/capabilities": jsonResponse({ ...CAPS, ocr: { ...CAPS.ocr, available: false } }) });
    render(<ResumeWorkspace apiBaseUrl={BASE} />);
    await waitFor(() => expect(screen.getByRole("note")).toHaveTextContent("OCR is unavailable"));
  });
});

describe("ParsedResumeView", () => {
  it("shows 'not found' for every missing section instead of inventing content", () => {
    render(
      <ParsedResumeView
        data={{
          personal_details: {
            full_name: "Jordan Sample",
            email: null,
            phone: null,
            location: null,
            linkedin_url: null,
            github_url: null,
            portfolio_url: null,
            other_links: [],
            summary: null,
          },
          education: [],
          work_experience: [],
          internships: [],
          technical_skills: [],
          projects: [],
          certifications: [],
          achievements: [],
        }}
      />,
    );
    for (const what of [
      "technical skills",
      "work experience",
      "internships",
      "education",
      "projects",
      "certifications",
      "achievements",
    ]) {
      expect(screen.getByText(`No ${what} found in the resume.`)).toBeInTheDocument();
    }
    expect(screen.getAllByText("Not stated")).toHaveLength(3);
  });
});
