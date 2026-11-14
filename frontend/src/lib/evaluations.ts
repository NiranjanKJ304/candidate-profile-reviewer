/**
 * Candidate-JD evaluations (Phase 5): types, runtime parsers and calls.
 * Mirrors app/schemas/evaluation.py.
 */

import type { ApiClient } from "@/lib/apiClient";
import { bool, list, num, oneOf, optNum, optStr, rec, safe, str } from "@/lib/parse";

const STATUS = ["pending", "running", "completed", "failed"] as const;
export type EvaluationStatus = (typeof STATUS)[number];
const VERDICTS = ["met", "partially_met", "not_met", "insufficient_evidence"] as const;
export type Verdict = (typeof VERDICTS)[number];
const FIT = ["strong", "moderate", "weak", "insufficient_evidence"] as const;
export type FitLabel = (typeof FIT)[number];
const TRUST = ["platform_collected", "resume_claim", "self_reported"] as const;
export type Trust = (typeof TRUST)[number];
const SOURCES = ["resume", "github", "codeforces", "leetcode"] as const;
const SOURCE_STATES = ["used", "not_linked", "not_collected", "unavailable"] as const;

export interface EvidenceItem {
  id: string;
  source: (typeof SOURCES)[number];
  trust: Trust;
  kind: string;
  text: string;
  source_url: string | null;
  /** False when left out to fit the LLM request limit (it could not back a verdict). */
  sent_to_llm: boolean;
}
export interface AssessedRequirement {
  id: string;
  kind: "required" | "preferred";
  type: string;
  text: string;
  baseline_status: string;
  verdict: Verdict;
  evidence_ids: string[];
  rationale: string;
  weight: number;
  credit: number;
  adjusted: string | null;
}
export interface Point {
  text: string;
  evidence_ids: string[];
}
export interface Gap {
  text: string;
  requirement_ids: string[];
}
export interface Score {
  overall: number | null;
  fit_label: FitLabel;
  coverage: number;
  required_met: number;
  required_total: number;
  preferred_met: number;
  preferred_total: number;
  unmet_required: string[];
  unevidenced_required: string[];
  method: string;
}
export interface EvaluationResult {
  jd_version: "reviewed" | "extracted";
  job_title: string | null;
  sources: { source: string; status: (typeof SOURCE_STATES)[number]; note: string | null }[];
  evidence: EvidenceItem[];
  experience: { years: number | null; method: string };
  requirements: AssessedRequirement[];
  score: Score;
  strengths: Point[];
  gaps: Gap[];
  discrepancies: Point[];
  summary: string;
  privacy: { identity_removed: boolean };
  decision_support_notice: string;
}
export interface EvaluationSummary {
  id: string;
  candidate_id: string;
  job_description_id: string;
  resume_id: string | null;
  status: EvaluationStatus;
  overall_score: number | null;
  fit_label: FitLabel | null;
  job_title: string | null;
  model_name: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}
export interface Evaluation extends EvaluationSummary {
  warnings: string[];
  result: EvaluationResult | null;
}

// --------------------------------------------------------------------------- parsers
const ids = (v: unknown) => list(v, str);
const point = (v: unknown): Point => {
  const r = rec(v);
  return { text: str(r.text), evidence_ids: ids(r.evidence_ids) };
};

function result(v: unknown): EvaluationResult {
  const r = rec(v);
  const s = rec(r.score);
  const exp = rec(r.experience);
  const privacy = rec(r.privacy);
  return {
    jd_version: oneOf(r.jd_version, ["reviewed", "extracted"] as const),
    job_title: optStr(r.job_title),
    sources: list(r.sources, (x) => {
      const o = rec(x);
      return { source: str(o.source), status: oneOf(o.status, SOURCE_STATES), note: optStr(o.note) };
    }),
    evidence: list(r.evidence, (x) => {
      const e = rec(x);
      return {
        id: str(e.id),
        source: oneOf(e.source, SOURCES),
        trust: oneOf(e.trust, TRUST),
        kind: str(e.kind),
        text: str(e.text),
        source_url: optStr(e.source_url),
        sent_to_llm: e.sent_to_llm === undefined ? true : bool(e.sent_to_llm),
      };
    }),
    experience: { years: optNum(exp.years), method: str(exp.method) },
    requirements: list(r.requirements, (x) => {
      const q = rec(x);
      return {
        id: str(q.id),
        kind: oneOf(q.kind, ["required", "preferred"] as const),
        type: str(q.type),
        text: str(q.text),
        baseline_status: str(q.baseline_status),
        verdict: oneOf(q.verdict, VERDICTS),
        evidence_ids: ids(q.evidence_ids),
        rationale: str(q.rationale),
        weight: num(q.weight),
        credit: num(q.credit),
        adjusted: optStr(q.adjusted),
      };
    }),
    score: {
      overall: optNum(s.overall),
      fit_label: oneOf(s.fit_label, FIT),
      coverage: num(s.coverage),
      required_met: num(s.required_met),
      required_total: num(s.required_total),
      preferred_met: num(s.preferred_met),
      preferred_total: num(s.preferred_total),
      unmet_required: ids(s.unmet_required),
      unevidenced_required: ids(s.unevidenced_required),
      method: str(s.method),
    },
    strengths: list(r.strengths, point),
    gaps: list(r.gaps, (x) => {
      const g = rec(x);
      return { text: str(g.text), requirement_ids: ids(g.requirement_ids) };
    }),
    discrepancies: list(r.discrepancies, point),
    summary: str(r.summary),
    privacy: { identity_removed: bool(privacy.identity_removed) },
    decision_support_notice: str(r.decision_support_notice),
  };
}

function summary(v: unknown): EvaluationSummary {
  const r = rec(v);
  return {
    id: str(r.id),
    candidate_id: str(r.candidate_id),
    job_description_id: str(r.job_description_id),
    resume_id: optStr(r.resume_id),
    status: oneOf(r.status, STATUS),
    overall_score: optNum(r.overall_score),
    fit_label: r.fit_label == null ? null : oneOf(r.fit_label, FIT),
    job_title: optStr(r.job_title),
    model_name: optStr(r.model_name),
    error_code: optStr(r.error_code),
    error_message: optStr(r.error_message),
    created_at: str(r.created_at),
    completed_at: optStr(r.completed_at),
  };
}

export const parseEvaluation = (v: unknown): Evaluation | null =>
  safe(() => {
    const r = rec(v);
    return { ...summary(r), warnings: list(r.warnings, str), result: r.result == null ? null : result(r.result) };
  });

export const parseEvaluationPage = (v: unknown): { items: EvaluationSummary[]; total: number } | null =>
  safe(() => {
    const r = rec(v);
    return { items: list(r.items, summary), total: num(r.total) };
  });

// --------------------------------------------------------------------------- calls
const base = "/evaluations";

export const createEvaluation = (api: ApiClient, candidateId: string, jobDescriptionId: string) =>
  api.post(base, { candidate_id: candidateId, job_description_id: jobDescriptionId }, parseEvaluation);
export const getEvaluation = (api: ApiClient, id: string) => api.get(`${base}/${encodeURIComponent(id)}`, parseEvaluation);
export const listEvaluations = (api: ApiClient, candidateId: string) =>
  api.get(`${base}?candidate_id=${encodeURIComponent(candidateId)}&limit=50&offset=0`, parseEvaluationPage);
export const rerunEvaluation = (api: ApiClient, id: string) =>
  api.post(`${base}/${encodeURIComponent(id)}/rerun`, undefined, parseEvaluation);
export const deleteEvaluation = (api: ApiClient, id: string) => api.delete(`${base}/${encodeURIComponent(id)}`);

// --------------------------------------------------------------------------- labels
export const VERDICT_TEXT: Record<Verdict, string> = {
  met: "Met",
  partially_met: "Partially met",
  not_met: "Not met",
  insufficient_evidence: "Not enough evidence",
};
export const FIT_TEXT: Record<FitLabel, string> = {
  strong: "Strong fit",
  moderate: "Moderate fit",
  weak: "Weak fit",
  insufficient_evidence: "Not enough evidence to score",
};
export const TRUST_TEXT: Record<Trust, string> = {
  platform_collected: "Platform data",
  resume_claim: "Resume claim",
  self_reported: "Self-reported",
};
export const EVALUATION_ERRORS: Record<string, string> = {
  no_evidence: "There is no evidence for this candidate yet. Parse a resume or collect a developer profile first.",
  jd_not_parsed: "The job description has not been parsed successfully.",
  jd_has_no_requirements: "The job description has no skills or requirements to evaluate.",
  source_missing: "The candidate or job description was deleted.",
  llm_not_configured: "No LLM API key is configured on the server (GROQ_API_KEY).",
  llm_auth_failed: "The LLM provider rejected the API key.",
  llm_timeout: "The LLM did not respond in time. Re-run the evaluation.",
  llm_rate_limited: "The LLM provider's rate limit was reached. Wait a minute and re-run.",
  llm_error: "The LLM provider returned an error.",
  llm_invalid_response: "The LLM did not return a valid assessment. Re-run the evaluation.",
  internal_error: "An unexpected error occurred.",
};
