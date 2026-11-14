import { describe, expect, it } from "vitest";

import { createApiClient } from "@/lib/apiClient";
import { createEvaluation, parseEvaluation, parseEvaluationPage } from "@/lib/evaluations";
import { EID, RESULT, evaluation } from "@/test/evaluationFixtures";
import { jsonResponse, mockFetch } from "@/test/fetchMock";

describe("parseEvaluation", () => {
  it("parses a completed evaluation", () => {
    const e = parseEvaluation(evaluation());
    expect(e?.overall_score).toBe(80.8);
    expect(e?.result?.score.unevidenced_required).toEqual(["Q2"]);
    expect(e?.result?.requirements[1].verdict).toBe("insufficient_evidence");
    expect(e?.result?.evidence[1].trust).toBe("platform_collected");
    expect(e?.result?.evidence[0].sent_to_llm).toBe(true); // missing in older results -> true
    expect(e?.result?.evidence[2].sent_to_llm).toBe(false);
  });

  it("keeps a missing score as null, never zero", () => {
    const e = parseEvaluation(
      evaluation({ overall_score: null, fit_label: "insufficient_evidence", result: { ...RESULT, score: { ...RESULT.score, overall: null } } }),
    );
    expect(e?.overall_score).toBeNull();
    expect(e?.result?.score.overall).toBeNull();
  });

  it("parses pending and failed evaluations", () => {
    expect(parseEvaluation(evaluation({ status: "pending", result: null, overall_score: null, fit_label: null }))?.result).toBeNull();
    expect(parseEvaluation(evaluation({ status: "failed", error_code: "llm_timeout", result: null }))?.error_code).toBe("llm_timeout");
  });

  it.each([
    ["unknown verdict", evaluation({ result: { ...RESULT, requirements: [{ ...RESULT.requirements[0], verdict: "maybe" }] } })],
    ["unknown trust", evaluation({ result: { ...RESULT, evidence: [{ ...RESULT.evidence[0], trust: "verified" }] } })],
    ["string score", evaluation({ overall_score: "80.8" })],
    ["unknown status", evaluation({ status: "done" })],
  ])("rejects %s", (_name, payload) => {
    expect(parseEvaluation(payload)).toBeNull();
  });

  it("parses a page", () => {
    const summary: Record<string, unknown> = { ...evaluation() };
    delete summary.result;
    delete summary.warnings;
    expect(parseEvaluationPage({ items: [summary], total: 1, limit: 50, offset: 0 })?.total).toBe(1);
  });
});

describe("calls", () => {
  it("posts the candidate and job description ids", async () => {
    const fetchFn = mockFetch({ "/api/v1/evaluations": jsonResponse(evaluation({ status: "pending", result: null }), 202) });
    const r = await createEvaluation(createApiClient("http://api.test"), "c1", "j1");
    expect(r.kind).toBe("ok");
    const init = (fetchFn.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(JSON.parse(String(init.body))).toEqual({ candidate_id: "c1", job_description_id: "j1" });
    if (r.kind === "ok") expect(r.data.id).toBe(EID);
  });
});
