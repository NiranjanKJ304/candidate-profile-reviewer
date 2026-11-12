/**
 * Codeforces integration API: types, runtime parsers and calls.
 * Mirrors app/schemas/codeforces.py (only the fields the UI uses are typed strictly).
 */

import type { ApiClient, FetchResult } from "@/lib/apiClient";
import { AVAILABILITY, type Availability } from "@/lib/github";
import { bool, list, num, oneOf, optNum, optStr, rec, safe, str } from "@/lib/parse";

const SYNC = ["pending", "processing", "completed", "failed"] as const;
export type SyncStatus = (typeof SYNC)[number];
const COLLECTION = ["not_collected", "collected", "unavailable", "failed"] as const;
const OWNERSHIP = ["unverified", "verified", "mismatch"] as const;

export interface Section {
  status: Availability;
  note: string | null;
  source_url: string | null;
  api_method: string | null;
}
export interface NamedCount {
  name: string;
  count: number;
}
export interface RatingChange {
  contest_id: number;
  contest_name: string | null;
  rank: number | null;
  old_rating: number;
  new_rating: number;
  delta: number;
  rating_updated_at: string | null;
  contest_url: string;
}
export interface Submission {
  id: number;
  problem_index: string | null;
  problem_name: string | null;
  problem_rating: number | null;
  tags: string[];
  programming_language: string | null;
  verdict: string | null;
  submitted_at: string;
  problem_url: string | null;
}
export interface SubmissionStats {
  total_submissions: number;
  accepted_submissions: number;
  rejected_submissions: number;
  pending_submissions: number;
  attempted_unique_problems: number;
  accepted_unique_problems: number;
  accepted_by_rating: { rating: number | null; problems: number }[];
  accepted_by_tag: NamedCount[];
  verdicts: NamedCount[];
  languages: NamedCount[];
  monthly_activity: { month: string; submissions: number; accepted: number }[];
  contests_in_window: number;
  window_start: string | null;
  window_end: string | null;
  truncated: boolean;
}
export interface Snapshot {
  collected_at: string;
  profile: Section & {
    data: {
      handle: string;
      first_name: string | null;
      last_name: string | null;
      country: string | null;
      city: string | null;
      organization: string | null;
      rating: number | null;
      max_rating: number | null;
      rank: string | null;
      max_rank: string | null;
      contribution: number | null;
      registered_at: string | null;
      last_online_at: string | null;
      profile_url: string;
    } | null;
  };
  rating_history: Section & { items: RatingChange[] };
  submissions: Section & { items: Submission[]; requested_limit: number; truncated: boolean };
  analytics: Section & {
    lifetime: { rated_contests: number | null };
    submissions: SubmissionStats | null;
  };
}
export interface CodeforcesProfile {
  id: string;
  candidate_id: string;
  handle: string;
  profile_url: string | null;
  ownership_status: (typeof OWNERSHIP)[number];
  collection_status: (typeof COLLECTION)[number];
  sync_status: SyncStatus;
  sync_error_code: string | null;
  sync_error: string | null;
  sync_warnings: string[];
  last_sync_attempt_at: string | null;
  collected_at: string | null;
  freshness: { stale: boolean; age_seconds: number | null; stale_after_hours: number };
  data: Snapshot | null;
}
export interface CodeforcesStatus {
  enabled: boolean;
  min_request_interval_seconds: number;
  submission_limit: number;
  last_error: string | null;
}

// --------------------------------------------------------------------------- parsers
function section(v: unknown): Section {
  const r = rec(v);
  const prov = r.provenance == null ? {} : rec(r.provenance);
  return {
    status: oneOf(r.status, AVAILABILITY),
    note: optStr(r.note),
    source_url: optStr(prov.source_url),
    api_method: optStr(prov.api_method),
  };
}
const counts = (v: unknown): NamedCount[] =>
  list(v, (x) => {
    const r = rec(x);
    return { name: str(r.name), count: num(r.count) };
  });

function ratingChange(v: unknown): RatingChange {
  const r = rec(v);
  return {
    contest_id: num(r.contest_id),
    contest_name: optStr(r.contest_name),
    rank: optNum(r.rank),
    old_rating: num(r.old_rating),
    new_rating: num(r.new_rating),
    delta: num(r.delta),
    rating_updated_at: optStr(r.rating_updated_at),
    contest_url: str(r.contest_url),
  };
}

function submission(v: unknown): Submission {
  const r = rec(v);
  return {
    id: num(r.id),
    problem_index: optStr(r.problem_index),
    problem_name: optStr(r.problem_name),
    problem_rating: optNum(r.problem_rating),
    tags: list(r.tags, str),
    programming_language: optStr(r.programming_language),
    verdict: optStr(r.verdict),
    submitted_at: str(r.submitted_at),
    problem_url: optStr(r.problem_url),
  };
}

function stats(v: unknown): SubmissionStats {
  const r = rec(v);
  return {
    total_submissions: num(r.total_submissions),
    accepted_submissions: num(r.accepted_submissions),
    rejected_submissions: num(r.rejected_submissions),
    pending_submissions: num(r.pending_submissions),
    attempted_unique_problems: num(r.attempted_unique_problems),
    accepted_unique_problems: num(r.accepted_unique_problems),
    accepted_by_rating: list(r.accepted_by_rating, (x) => {
      const b = rec(x);
      return { rating: optNum(b.rating), problems: num(b.problems) };
    }),
    accepted_by_tag: counts(r.accepted_by_tag),
    verdicts: counts(r.verdicts),
    languages: counts(r.languages),
    monthly_activity: list(r.monthly_activity, (x) => {
      const m = rec(x);
      return { month: str(m.month), submissions: num(m.submissions), accepted: num(m.accepted) };
    }),
    contests_in_window: num(r.contests_in_window),
    window_start: optStr(r.window_start),
    window_end: optStr(r.window_end),
    truncated: bool(r.truncated),
  };
}

function snapshot(v: unknown): Snapshot {
  const r = rec(v);
  const p = rec(r.profile);
  const d = p.data == null ? null : rec(p.data);
  const rh = rec(r.rating_history);
  const subs = rec(r.submissions);
  const an = rec(r.analytics);
  const life = rec(an.lifetime);
  return {
    collected_at: str(r.collected_at),
    profile: {
      ...section(p),
      data: d
        ? {
            handle: str(d.handle),
            first_name: optStr(d.first_name),
            last_name: optStr(d.last_name),
            country: optStr(d.country),
            city: optStr(d.city),
            organization: optStr(d.organization),
            rating: optNum(d.rating),
            max_rating: optNum(d.max_rating),
            rank: optStr(d.rank),
            max_rank: optStr(d.max_rank),
            contribution: optNum(d.contribution),
            registered_at: optStr(d.registered_at),
            last_online_at: optStr(d.last_online_at),
            profile_url: str(d.profile_url),
          }
        : null,
    },
    rating_history: { ...section(rh), items: list(rh.items, ratingChange) },
    submissions: {
      ...section(subs),
      items: list(subs.items, submission),
      requested_limit: num(subs.requested_limit),
      truncated: bool(subs.truncated),
    },
    analytics: {
      ...section(an),
      lifetime: { rated_contests: optNum(life.rated_contests) },
      submissions: an.submissions == null ? null : stats(an.submissions),
    },
  };
}

export const parseCodeforcesProfile = (v: unknown): CodeforcesProfile | null =>
  safe(() => {
    const r = rec(v);
    const f = rec(r.freshness);
    return {
      id: str(r.id),
      candidate_id: str(r.candidate_id),
      handle: str(r.handle),
      profile_url: optStr(r.profile_url),
      ownership_status: oneOf(r.ownership_status, OWNERSHIP),
      collection_status: oneOf(r.collection_status, COLLECTION),
      sync_status: oneOf(r.sync_status, SYNC),
      sync_error_code: optStr(r.sync_error_code),
      sync_error: optStr(r.sync_error),
      sync_warnings: list(r.sync_warnings, str),
      last_sync_attempt_at: optStr(r.last_sync_attempt_at),
      collected_at: optStr(r.collected_at),
      freshness: { stale: bool(f.stale), age_seconds: optNum(f.age_seconds), stale_after_hours: num(f.stale_after_hours) },
      data: r.data == null ? null : snapshot(r.data),
    };
  });

export const parseCodeforcesStatus = (v: unknown): CodeforcesStatus | null =>
  safe(() => {
    const r = rec(v);
    return {
      enabled: bool(r.enabled),
      min_request_interval_seconds: num(r.min_request_interval_seconds),
      submission_limit: num(r.submission_limit),
      last_error: optStr(r.last_error),
    };
  });

// --------------------------------------------------------------------------- calls
const cfPath = (candidateId: string) => `/candidates/${encodeURIComponent(candidateId)}/codeforces`;

export const getCodeforcesStatus = (api: ApiClient) => api.get("/integrations/codeforces/status", parseCodeforcesStatus);
export const getCodeforcesProfile = (api: ApiClient, candidateId: string) => api.get(cfPath(candidateId), parseCodeforcesProfile);
export const addCodeforcesProfile = (
  api: ApiClient,
  candidateId: string,
  profile: string,
): Promise<FetchResult<CodeforcesProfile>> => api.post(cfPath(candidateId), { profile }, parseCodeforcesProfile);
/** Refresh; uses /retry after a failure (the server only accepts /retry then). */
export const syncCodeforcesProfile = (api: ApiClient, candidateId: string, failed: boolean) =>
  api.post(`${cfPath(candidateId)}/${failed ? "retry" : "sync"}`, undefined, parseCodeforcesProfile);
export const removeCodeforcesProfile = (api: ApiClient, candidateId: string) => api.delete(cfPath(candidateId));

/** Light client-side check; the server performs the authoritative validation. */
export function checkCodeforcesInput(raw: string): string | null {
  const value = raw.trim();
  if (!value) return "Enter a Codeforces handle or profile URL.";
  if (value.length > 300) return "The value is too long.";
  if (/\s/.test(value)) return "The value must not contain spaces.";
  return null;
}

export const ERROR_EXPLANATIONS: Record<string, string> = {
  invalid_profile: "Codeforces reports that no user has this handle.",
  rate_limited: "The Codeforces API call limit was reached. Retry in a minute.",
  network_error: "Codeforces could not be reached from the server, or is temporarily unavailable.",
  timeout: "Codeforces did not respond in time.",
  api_error: "Codeforces returned an unexpected error.",
  integration_disabled: "The Codeforces integration is disabled on the server (ENABLED_PLATFORMS).",
  internal_error: "An unexpected error occurred while collecting the profile.",
};
