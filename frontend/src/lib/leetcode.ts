/**
 * LeetCode (Phase 4): association plus manual, self-reported evidence.
 * LeetCode offers no permitted API, so nothing here is collected from LeetCode.
 * Mirrors app/schemas/leetcode.py.
 */

import type { ApiClient, FetchResult } from "@/lib/apiClient";
import { AVAILABILITY, type Availability } from "@/lib/github";
import { bool, list, num, oneOf, optNum, optStr, rec, safe, str } from "@/lib/parse";

const REPORTED_BY = ["candidate", "recruiter"] as const;
export type ReportedBy = (typeof REPORTED_BY)[number];

export interface SectionInfo {
  status: Availability;
  note: string | null;
  reported_by: ReportedBy;
  verification_status: "not_verified";
}
export interface NamedCount {
  name: string;
  problems_solved: number | null;
}
export interface ContestEntry {
  contest_name: string;
  rank: number | null;
  rating_after: number | null;
  contest_date: string | null;
}
export interface Evidence {
  source_type: "self_reported";
  verification_status: "not_verified";
  reported_by: ReportedBy;
  submitted_at: string;
  as_of: string | null;
  revision: number;
  solved: SectionInfo & { total: number | null; easy: number | null; medium: number | null; hard: number | null };
  contest: SectionInfo & { rating: number | null; attended: number | null; top_percentage: number | null };
  contest_history: SectionInfo & { items: ContestEntry[] };
  languages: SectionInfo & { items: NamedCount[] };
  topics: SectionInfo & { items: NamedCount[] };
  notes: string | null;
}
export interface LeetCodeProfile {
  id: string;
  candidate_id: string;
  username: string;
  profile_url: string | null;
  ownership_status: string;
  platform_data: { status: "unavailable"; reason: string; sources: string[] };
  evidence: Evidence | null;
  freshness: { submitted_at: string | null; as_of: string | null; age_days: number | null; stale: boolean; stale_after_days: number };
  warnings: string[];
}
export interface LeetCodeStatus {
  enabled: boolean;
  findings: string[];
  sources: string[];
  investigated_on: string;
}

// --------------------------------------------------------------------------- parsers
const literal = <T extends string>(v: unknown, value: T): T => oneOf(v, [value] as const);

function section(v: unknown): SectionInfo {
  const r = rec(v);
  const p = rec(r.provenance);
  literal(p.source_type, "self_reported");
  return {
    status: oneOf(r.status, AVAILABILITY),
    note: optStr(r.note),
    reported_by: oneOf(p.reported_by, REPORTED_BY),
    verification_status: literal(p.verification_status, "not_verified"),
  };
}
const counts = (v: unknown): NamedCount[] =>
  list(v, (x) => {
    const r = rec(x);
    return { name: str(r.name), problems_solved: optNum(r.problems_solved) };
  });

function evidence(v: unknown): Evidence {
  const r = rec(v);
  const solved = rec(r.solved);
  const contest = rec(r.contest);
  const history = rec(r.contest_history);
  return {
    source_type: literal(r.source_type, "self_reported"),
    verification_status: literal(r.verification_status, "not_verified"),
    reported_by: oneOf(r.reported_by, REPORTED_BY),
    submitted_at: str(r.submitted_at),
    as_of: optStr(r.as_of),
    revision: num(r.revision),
    solved: { ...section(solved), total: optNum(solved.total), easy: optNum(solved.easy), medium: optNum(solved.medium), hard: optNum(solved.hard) },
    contest: { ...section(contest), rating: optNum(contest.rating), attended: optNum(contest.attended), top_percentage: optNum(contest.top_percentage) },
    contest_history: {
      ...section(history),
      items: list(history.items, (x) => {
        const c = rec(x);
        return { contest_name: str(c.contest_name), rank: optNum(c.rank), rating_after: optNum(c.rating_after), contest_date: optStr(c.contest_date) };
      }),
    },
    languages: { ...section(r.languages), items: counts(rec(r.languages).items) },
    topics: { ...section(r.topics), items: counts(rec(r.topics).items) },
    notes: optStr(r.notes),
  };
}

export const parseLeetCodeProfile = (v: unknown): LeetCodeProfile | null =>
  safe(() => {
    const r = rec(v);
    const pd = rec(r.platform_data);
    const f = rec(r.freshness);
    return {
      id: str(r.id),
      candidate_id: str(r.candidate_id),
      username: str(r.username),
      profile_url: optStr(r.profile_url),
      ownership_status: str(r.ownership_status),
      platform_data: { status: literal(pd.status, "unavailable"), reason: str(pd.reason), sources: list(pd.sources, str) },
      evidence: r.evidence == null ? null : evidence(r.evidence),
      freshness: {
        submitted_at: optStr(f.submitted_at),
        as_of: optStr(f.as_of),
        age_days: optNum(f.age_days),
        stale: bool(f.stale),
        stale_after_days: num(f.stale_after_days),
      },
      warnings: list(r.warnings, str),
    };
  });

export const parseLeetCodeStatus = (v: unknown): LeetCodeStatus | null =>
  safe(() => {
    const r = rec(v);
    return { enabled: bool(r.enabled), findings: list(r.findings, str), sources: list(r.sources, str), investigated_on: str(r.investigated_on) };
  });

// --------------------------------------------------------------------------- evidence form
export interface EvidenceForm {
  reported_by: ReportedBy | "";
  as_of: string;
  total_solved: string;
  easy_solved: string;
  medium_solved: string;
  hard_solved: string;
  contest_rating: string;
  contests_attended: string;
  contest_top_percentage: string;
  languages: string;
  topics: string;
  contest_history: string;
  notes: string;
}

export const EMPTY_FORM: EvidenceForm = {
  reported_by: "",
  as_of: "",
  total_solved: "",
  easy_solved: "",
  medium_solved: "",
  hard_solved: "",
  contest_rating: "",
  contests_attended: "",
  contest_top_percentage: "",
  languages: "",
  topics: "",
  contest_history: "",
  notes: "",
};

/** Pre-fill the form from stored evidence (for editing). */
export function formFromEvidence(ev: Evidence): EvidenceForm {
  const n = (v: number | null) => (v === null ? "" : String(v));
  const lines = (items: NamedCount[]) => items.map((i) => (i.problems_solved === null ? i.name : `${i.name}: ${i.problems_solved}`)).join("\n");
  return {
    reported_by: ev.reported_by,
    as_of: ev.as_of ?? "",
    total_solved: n(ev.solved.total),
    easy_solved: n(ev.solved.easy),
    medium_solved: n(ev.solved.medium),
    hard_solved: n(ev.solved.hard),
    contest_rating: n(ev.contest.rating),
    contests_attended: n(ev.contest.attended),
    contest_top_percentage: n(ev.contest.top_percentage),
    languages: lines(ev.languages.items),
    topics: lines(ev.topics.items),
    contest_history: ev.contest_history.items
      .map((c) => [c.contest_name, n(c.rank), n(c.rating_after), c.contest_date ?? ""].join(" | ").replace(/( \| )+$/, ""))
      .join("\n"),
    notes: ev.notes ?? "",
  };
}

type Payload = Record<string, unknown>;

function intField(label: string, raw: string, errors: string[]): number | undefined {
  const value = raw.trim();
  if (!value) return undefined; // not provided: unknown, never zero
  if (!/^\d+$/.test(value)) {
    errors.push(`${label} must be a whole number.`);
    return undefined;
  }
  return Number(value);
}

function namedLines(label: string, raw: string, errors: string[]): NamedCount[] {
  return raw
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((line) => {
      const m = /^(.*?)(?::\s*(\d+))?$/.exec(line);
      const name = (m?.[1] ?? line).trim();
      if (!name) errors.push(`${label}: each line needs a name.`);
      return { name, problems_solved: m?.[2] !== undefined ? Number(m[2]) : null };
    });
}

/**
 * Convert the form into the API payload. Empty fields are omitted (not provided),
 * never sent as zero. The server performs the authoritative validation.
 */
export function buildEvidencePayload(form: EvidenceForm): { payload: Payload | null; errors: string[] } {
  const errors: string[] = [];
  if (!form.reported_by) errors.push("Choose who is reporting this evidence.");
  const payload: Payload = { reported_by: form.reported_by };
  if (form.as_of.trim()) payload.as_of = form.as_of.trim();
  const ints: [keyof EvidenceForm, string][] = [
    ["total_solved", "Total solved"],
    ["easy_solved", "Easy solved"],
    ["medium_solved", "Medium solved"],
    ["hard_solved", "Hard solved"],
    ["contest_rating", "Contest rating"],
    ["contests_attended", "Contests attended"],
  ];
  for (const [key, label] of ints) {
    const value = intField(label, form[key], errors);
    if (value !== undefined) payload[key] = value;
  }
  const top = form.contest_top_percentage.trim();
  if (top) {
    const value = Number(top);
    if (!Number.isFinite(value) || value <= 0 || value > 100) errors.push("Top percentage must be between 0 and 100.");
    else payload.contest_top_percentage = value;
  }
  const { total_solved: total, easy_solved: e, medium_solved: m, hard_solved: h } = payload as Record<string, number | undefined>;
  const parts = [e, m, h].filter((x): x is number => x !== undefined);
  if (total !== undefined && parts.reduce((a, b) => a + b, 0) > total) errors.push("Easy + medium + hard cannot exceed total solved.");
  if (total !== undefined && parts.length === 3 && parts.reduce((a, b) => a + b, 0) !== total)
    errors.push("Easy + medium + hard must equal total solved.");

  const languages = namedLines("Languages", form.languages, errors);
  const topics = namedLines("Topics", form.topics, errors);
  if (languages.length) payload.languages = languages;
  if (topics.length) payload.topics = topics;
  const history = form.contest_history
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((line) => {
      const [name = "", rank = "", rating = "", day = ""] = line.split("|").map((x) => x.trim());
      const entry: Payload = { contest_name: name };
      const r = intField(`Rank for "${name}"`, rank, errors);
      const ra = intField(`Rating for "${name}"`, rating, errors);
      if (r !== undefined) entry.rank = r;
      if (ra !== undefined) entry.rating_after = ra;
      if (day) entry.contest_date = day;
      if (!name) errors.push("Contest history: each line needs a contest name.");
      return entry;
    });
  if (history.length) payload.contest_history = history;
  if (form.notes.trim()) payload.notes = form.notes.trim();

  const provided = Object.keys(payload).filter((k) => !["reported_by", "as_of", "notes"].includes(k));
  if (provided.length === 0) errors.push("Provide at least one evidence value.");
  return errors.length ? { payload: null, errors } : { payload, errors };
}

// --------------------------------------------------------------------------- calls
const lcPath = (candidateId: string) => `/candidates/${encodeURIComponent(candidateId)}/leetcode`;

export const getLeetCodeStatus = (api: ApiClient) => api.get("/integrations/leetcode/status", parseLeetCodeStatus);
export const getLeetCodeProfile = (api: ApiClient, candidateId: string) => api.get(lcPath(candidateId), parseLeetCodeProfile);
export const addLeetCodeProfile = (api: ApiClient, candidateId: string, profile: string): Promise<FetchResult<LeetCodeProfile>> =>
  api.post(lcPath(candidateId), { profile }, parseLeetCodeProfile);
export const saveLeetCodeEvidence = (api: ApiClient, candidateId: string, payload: Payload) =>
  api.put(`${lcPath(candidateId)}/evidence`, payload, parseLeetCodeProfile);
export const clearLeetCodeEvidence = (api: ApiClient, candidateId: string) => api.delete(`${lcPath(candidateId)}/evidence`);
export const removeLeetCodeProfile = (api: ApiClient, candidateId: string) => api.delete(lcPath(candidateId));

export function checkLeetCodeInput(raw: string): string | null {
  const value = raw.trim();
  if (!value) return "Enter a LeetCode username or profile URL.";
  if (value.length > 300) return "The value is too long.";
  if (/\s/.test(value)) return "The value must not contain spaces.";
  return null;
}
