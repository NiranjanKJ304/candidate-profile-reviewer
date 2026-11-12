import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { jsonResponse, mockFetch } from "@/test/fetchMock";
import {
  DETAIL_COMPLETED,
  DETAIL_FAILED,
  JD_ID,
  PARSED_JD,
  SUMMARY_PENDING,
  TECH,
  jdDetail,
  page,
  parsedResponse,
} from "@/test/jdFixtures";
import { CAPS } from "@/test/resumeFixtures";

import JDView from "./JDView";
import JobDescriptionWorkspace from "./JobDescriptionWorkspace";

const BASE = "http://api.test";
const JD_CAPS = { ...CAPS, job_description: { max_bytes: 5 * 1024 * 1024, max_pages: 20, accepted_extensions: [".pdf", ".docx", ".txt"] } };

function sequence(...bodies: object[]) {
  let i = 0;
  return () => jsonResponse(bodies[Math.min(i++, bodies.length - 1)]);
}

function upload(file = new File(["Senior Backend Engineer\nRequirements: Python"], "role.txt")) {
  fireEvent.change(screen.getByLabelText("Job description file"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Upload and parse" }));
}

describe("JobDescriptionWorkspace", () => {
  it("uploads, polls and shows required vs preferred skills, experience and warnings", async () => {
    mockFetch({
      "/system/capabilities": jsonResponse(JD_CAPS),
      "/job-descriptions?limit=50&offset=0": sequence(page([]), page([SUMMARY_PENDING]), page([DETAIL_COMPLETED])),
      "/job-descriptions/upload": jsonResponse(SUMMARY_PENDING, 202),
      [`/job-descriptions/${JD_ID}`]: sequence(jdDetail(), jdDetail({ parse_status: "processing" }), DETAIL_COMPLETED),
      [`/job-descriptions/${JD_ID}/parsed`]: jsonResponse(parsedResponse()),
    });
    render(<JobDescriptionWorkspace apiBaseUrl={BASE} pollIntervalMs={5} />);
    upload();

    expect(await screen.findByText("Parsing complete")).toBeInTheDocument();
    const view = screen.getByTestId("parsed-jd");
    const required = within(view).getByRole("region", { name: /Required skills \(2\)/ });
    expect(within(required).getByText("Python")).toBeInTheDocument();
    const preferred = within(view).getByRole("region", { name: /Preferred skills \(1\)/ });
    expect(within(preferred).getByText("Docker")).toBeInTheDocument();
    expect(within(view).getByText("5+ years")).toBeInTheDocument();
    expect(within(view).getByText("5+ years of professional software development experience")).toBeInTheDocument();
    expect(screen.getByText(/Removed required skill\(s\)/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute(
      "href",
      `${BASE}/api/v1/job-descriptions/${JD_ID}/file`,
    );
  });

  it("validates the file on the client", async () => {
    const fetchFn = mockFetch({
      "/system/capabilities": jsonResponse(JD_CAPS),
      "/job-descriptions?limit=50&offset=0": jsonResponse(page([])),
    });
    render(<JobDescriptionWorkspace apiBaseUrl={BASE} />);
    upload(new File(["x"], "role.rtf"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Only PDF (.pdf), Word (.docx) and text (.txt)");
    expect(fetchFn.mock.calls.some(([u]) => String(u).endsWith("/upload"))).toBe(false);
  });

  it("shows the server's rejection reason", async () => {
    mockFetch({
      "/system/capabilities": jsonResponse(JD_CAPS),
      "/job-descriptions?limit=50&offset=0": jsonResponse(page([])),
      "/job-descriptions/upload": jsonResponse(
        { error: { code: "duplicate_job_description", message: "This file has already been uploaded" } },
        409,
      ),
    });
    render(<JobDescriptionWorkspace apiBaseUrl={BASE} />);
    upload();
    expect(await screen.findByRole("alert")).toHaveTextContent("Upload rejected: This file has already been uploaded");
  });

  it("shows a failure and retries parsing", async () => {
    mockFetch({
      "/system/capabilities": jsonResponse({ ...JD_CAPS, llm: { ...JD_CAPS.llm, configured: false } }),
      "/job-descriptions?limit=50&offset=0": jsonResponse(page([DETAIL_FAILED])),
      [`/job-descriptions/${JD_ID}/parse`]: jsonResponse(SUMMARY_PENDING, 202),
      [`/job-descriptions/${JD_ID}`]: sequence(DETAIL_FAILED, DETAIL_COMPLETED),
      [`/job-descriptions/${JD_ID}/parsed`]: jsonResponse(parsedResponse()),
    });
    render(<JobDescriptionWorkspace apiBaseUrl={BASE} pollIntervalMs={5} />);
    expect(await screen.findByRole("note")).toHaveTextContent("GROQ_API_KEY");
    fireEvent.click(await screen.findByRole("button", { name: /role\.txt/ }));
    expect(await screen.findByText("Parsing failed")).toBeInTheDocument();
    expect(screen.getByText("llm_timeout")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry parsing" }));
    expect(await screen.findByText("Parsing complete")).toBeInTheDocument();
  });

  it("lets the user correct the extraction and saves it as a review", async () => {
    const fetchFn = mockFetch({
      "/system/capabilities": jsonResponse(JD_CAPS),
      "/job-descriptions?limit=50&offset=0": jsonResponse(page([DETAIL_COMPLETED])),
      [`/job-descriptions/${JD_ID}`]: jsonResponse(DETAIL_COMPLETED),
      [`/job-descriptions/${JD_ID}/parsed`]: () => {
        const call = fetchFn.mock.calls.at(-1) as unknown as [string, RequestInit];
        if (call[1]?.method === "PUT") {
          const sent = JSON.parse(String(call[1].body));
          return jsonResponse(parsedResponse({ source: "reviewed", data: sent }));
        }
        return jsonResponse(parsedResponse());
      },
    });
    render(<JobDescriptionWorkspace apiBaseUrl={BASE} />);
    fireEvent.click(await screen.findByRole("button", { name: /Senior Backend Engineer/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Review & correct" }));

    fireEvent.change(screen.getByLabelText(/Minimum years/), { target: { value: "6" } });
    fireEvent.change(screen.getByLabelText(/^Preferred skills/), { target: { value: "Docker | devops\nTerraform | devops" } });
    fireEvent.click(screen.getByRole("button", { name: "Save corrections" }));

    await waitFor(() => {
      const put = fetchFn.mock.calls.find((c) => ((c as unknown[])[1] as RequestInit | undefined)?.method === "PUT");
      expect(put).toBeDefined();
    });
    const put = fetchFn.mock.calls.find((c) => ((c as unknown[])[1] as RequestInit | undefined)?.method === "PUT");
    const sent = JSON.parse(String(((put as unknown[])[1] as RequestInit).body));
    expect(sent.minimum_experience.years_min).toBe(6);
    expect(sent.minimum_experience.evidence).toBeNull(); // edited value no longer backed by the quote
    expect(sent.preferred_skills.map((s: { name: string }) => s.name)).toEqual(["Docker", "Terraform"]);
    expect(sent.preferred_skills[0].evidence).toBe("Experience with Docker and Kubernetes");
  });

  it("rejects invalid years in the editor without saving", async () => {
    const fetchFn = mockFetch({
      "/system/capabilities": jsonResponse(JD_CAPS),
      "/job-descriptions?limit=50&offset=0": jsonResponse(page([DETAIL_COMPLETED])),
      [`/job-descriptions/${JD_ID}`]: jsonResponse(DETAIL_COMPLETED),
      [`/job-descriptions/${JD_ID}/parsed`]: jsonResponse(parsedResponse()),
    });
    render(<JobDescriptionWorkspace apiBaseUrl={BASE} />);
    fireEvent.click(await screen.findByRole("button", { name: /Senior Backend Engineer/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Review & correct" }));
    fireEvent.change(screen.getByLabelText(/Minimum years/), { target: { value: "9" } });
    fireEvent.change(screen.getByLabelText(/Maximum years/), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Save corrections" }));
    expect(await screen.findByText("Maximum years cannot be below minimum years.")).toBeInTheDocument();
    expect(fetchFn.mock.calls.some((c) => ((c as unknown[])[1] as RequestInit | undefined)?.method === "PUT")).toBe(false);
  });

  it("deletes a job description", async () => {
    const fetchFn = mockFetch({
      "/system/capabilities": jsonResponse(JD_CAPS),
      // List is fetched on mount, then once more after the delete.
      "/job-descriptions?limit=50&offset=0": sequence(page([DETAIL_COMPLETED]), page([])),
      [`/job-descriptions/${JD_ID}/parsed`]: jsonResponse(parsedResponse()),
      [`/job-descriptions/${JD_ID}`]: () => {
        const call = fetchFn.mock.calls.at(-1) as unknown as [string, RequestInit];
        return call[1]?.method === "DELETE" ? new Response(null, { status: 204 }) : jsonResponse(DETAIL_COMPLETED);
      },
    });
    render(<JobDescriptionWorkspace apiBaseUrl={BASE} />);
    fireEvent.click(await screen.findByRole("button", { name: /Senior Backend Engineer/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(await screen.findByText("None uploaded yet.")).toBeInTheDocument();
  });

  it("reports a configuration error without a base URL", () => {
    render(<JobDescriptionWorkspace apiBaseUrl={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent("NEXT_PUBLIC_API_BASE_URL");
  });
});

describe("JDView", () => {
  it("shows 'not stated' for every missing requirement instead of inventing", () => {
    render(
      <JDView
        data={{
          ...PARSED_JD,
          company_name: null,
          required_skills: [],
          preferred_skills: [],
          minimum_experience: null,
          education_requirements: [],
          responsibilities: [],
          soft_skills: [],
          seniority: null,
        } as never}
        tech={{ ...TECH, programming_languages: [], databases: [], cloud_and_devops: [] }}
      />,
    );
    for (const what of [
      "required skills",
      "preferred skills",
      "technologies",
      "education requirements",
      "responsibilities",
      "qualifications",
      "soft skills",
    ]) {
      expect(screen.getByText(`No ${what} stated in the job description.`)).toBeInTheDocument();
    }
    expect(screen.getAllByText("Not stated").length).toBeGreaterThanOrEqual(3);
  });
});
