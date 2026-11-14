import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CANDIDATE, EID, JD, RESULT, evaluation } from "@/test/evaluationFixtures";
import { jsonResponse, mockFetch } from "@/test/fetchMock";

import EvaluationWorkspace from "./EvaluationWorkspace";

const BASE = "http://api.test";

function sequence(...items: (() => Response)[]) {
  let i = 0;
  return () => items[Math.min(i++, items.length - 1)]();
}

const LISTS = {
  "/candidates?limit=100&offset=0": jsonResponse({ items: [CANDIDATE], total: 1, limit: 100, offset: 0 }),
  "/job-descriptions?limit=100&offset=0": jsonResponse({
    items: [JD, { ...JD, id: "d-pending", title: "Pending JD", parse_status: "processing" }],
    total: 2,
  }),
};

async function choose() {
  fireEvent.change(await screen.findByLabelText("Candidate"), { target: { value: CANDIDATE.id } });
  await screen.findByRole("option", { name: /Senior Backend Engineer/ });
  fireEvent.change(screen.getByLabelText("Job description (parsed)"), { target: { value: JD.id } });
}

describe("EvaluationWorkspace", () => {
  it("starts an evaluation, polls and shows the score, verdicts and evidence", async () => {
    const fetchFn = mockFetch({
      ...LISTS,
      [`/evaluations?candidate_id=${CANDIDATE.id}&limit=50&offset=0`]: jsonResponse({ items: [], total: 0, limit: 50, offset: 0 }),
      [`/evaluations/${EID}`]: sequence(
        () => jsonResponse(evaluation({ status: "running", result: null, overall_score: null, fit_label: null })),
        () => jsonResponse(evaluation()),
      ),
      "/api/v1/evaluations": jsonResponse(evaluation({ status: "pending", result: null, overall_score: null, fit_label: null }), 202),
    });
    render(<EvaluationWorkspace apiBaseUrl={BASE} pollIntervalMs={5} />);
    await choose();
    expect(screen.queryByRole("option", { name: /Pending JD/ })).not.toBeInTheDocument(); // only parsed JDs
    fireEvent.click(screen.getByRole("button", { name: "Evaluate" }));

    const view = await screen.findByTestId("evaluation-result");
    expect(screen.getByTestId("evaluation-status")).toHaveTextContent("Completed");
    expect(within(view).getByTestId("evaluation-score")).toHaveTextContent("81");
    expect(within(view).getByText("Strong fit")).toBeInTheDocument();
    expect(within(view).getByText("Decision support only: this is not a hiring decision.")).toBeInTheDocument();
    expect(within(view).getByTestId("unevidenced")).toHaveTextContent("Rust");
    const table = within(view).getByRole("table", { name: "Requirement verdicts" });
    expect(within(table).getByText("Met")).toBeInTheDocument();
    expect(within(table).getByText("Not enough evidence")).toBeInTheDocument();
    expect(within(table).getByText(/Adjusted by the evaluation rules/)).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "G1" })).toHaveAttribute("href", "#evidence-G1");
    expect(within(view).getByText(/may contain mistakes; the requirement table below is authoritative/)).toBeInTheDocument();
    expect(within(view).getByText(/not evidence of missing skills/)).toBeInTheDocument();
    expect(within(view).getByText("not sent to the model")).toBeInTheDocument(); // R9 only
    const posted = fetchFn.mock.calls.find((c) => ((c as unknown[])[1] as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String(((posted as unknown[])[1] as RequestInit).body))).toEqual({
      candidate_id: CANDIDATE.id,
      job_description_id: JD.id,
    });
  });

  it("shows no number when there is not enough evidence", async () => {
    mockFetch({
      ...LISTS,
      [`/evaluations?candidate_id=${CANDIDATE.id}&limit=50&offset=0`]: jsonResponse({ items: [], total: 0, limit: 50, offset: 0 }),
      "/api/v1/evaluations": jsonResponse(
        evaluation({
          overall_score: null,
          fit_label: "insufficient_evidence",
          result: { ...RESULT, score: { ...RESULT.score, overall: null, fit_label: "insufficient_evidence" } },
        }),
        202,
      ),
    });
    render(<EvaluationWorkspace apiBaseUrl={BASE} />);
    await choose();
    fireEvent.click(screen.getByRole("button", { name: "Evaluate" }));
    const view = await screen.findByTestId("evaluation-result");
    expect(within(view).getByTestId("evaluation-score")).toHaveTextContent("—");
    expect(within(view).getByText("Not enough evidence to score")).toBeInTheDocument();
  });

  it("explains a failure and re-runs", async () => {
    const history = { items: [{ ...evaluation({ status: "failed", error_code: "llm_rate_limited", result: null }) }], total: 1, limit: 50, offset: 0 };
    mockFetch({
      ...LISTS,
      [`/evaluations?candidate_id=${CANDIDATE.id}&limit=50&offset=0`]: jsonResponse(history),
      [`/evaluations/${EID}/rerun`]: jsonResponse(evaluation({ status: "pending", result: null }), 202),
      [`/evaluations/${EID}`]: sequence(
        () => jsonResponse(evaluation({ status: "failed", error_code: "llm_rate_limited", error_message: "limit", result: null })),
        () => jsonResponse(evaluation()),
      ),
    });
    render(<EvaluationWorkspace apiBaseUrl={BASE} pollIntervalMs={5} />);
    fireEvent.change(await screen.findByLabelText("Candidate"), { target: { value: CANDIDATE.id } });
    fireEvent.click(await screen.findByRole("button", { name: /Senior Backend Engineer · failed/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("rate limit was reached");
    fireEvent.click(screen.getByRole("button", { name: "Re-run" }));
    expect(await screen.findByTestId("evaluation-result")).toBeInTheDocument();
  });

  it("validates the selection and surfaces server errors", async () => {
    mockFetch({
      ...LISTS,
      [`/evaluations?candidate_id=${CANDIDATE.id}&limit=50&offset=0`]: jsonResponse({ items: [], total: 0, limit: 50, offset: 0 }),
      "/api/v1/evaluations": jsonResponse({ error: { code: "llm_not_configured", message: "No LLM API key is configured" } }, 503),
    });
    render(<EvaluationWorkspace apiBaseUrl={BASE} />);
    fireEvent.click(await screen.findByRole("button", { name: "Evaluate" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a candidate");
    await choose();
    fireEvent.click(screen.getByRole("button", { name: "Evaluate" }));
    expect(await screen.findByText("No LLM API key is configured")).toBeInTheDocument();
  });

  it("reports a configuration error", () => {
    render(<EvaluationWorkspace apiBaseUrl={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent("NEXT_PUBLIC_API_BASE_URL");
  });
});
