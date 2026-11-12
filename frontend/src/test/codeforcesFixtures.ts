/** Synthetic Codeforces API payloads for frontend tests (fictional account). */

export { CID } from "@/test/githubFixtures";
import { CID } from "@/test/githubFixtures";

const T = "2026-11-12T10:00:00Z";
const prov = (method: string, url = "https://codeforces.com/profile/algo_dev") => ({
  source_platform: "codeforces",
  source_url: url,
  api_method: method,
  collected_at: T,
});

export const STATS = {
  scope: "collected_window",
  total_submissions: 6,
  accepted_submissions: 4,
  rejected_submissions: 2,
  pending_submissions: 0,
  attempted_unique_problems: 4,
  accepted_unique_problems: 3,
  accepted_by_rating: [
    { rating: 1200, problems: 1 },
    { rating: 1600, problems: 1 },
    { rating: null, problems: 1 },
  ],
  accepted_by_tag: [
    { name: "dp", count: 1 },
    { name: "math", count: 1 },
  ],
  verdicts: [
    { name: "OK", count: 4 },
    { name: "WRONG_ANSWER", count: 2 },
  ],
  languages: [
    { name: "Python 3", count: 4 },
    { name: "C++17", count: 2 },
  ],
  monthly_activity: [{ month: "2026-10", submissions: 6, accepted: 4 }],
  contests_in_window: 1,
  window_start: "2026-10-01T00:00:00Z",
  window_end: "2026-10-30T00:00:00Z",
  truncated: true,
};

export const SNAPSHOT = {
  schema_version: 1,
  source_platform: "codeforces",
  collected_at: T,
  profile: {
    status: "available",
    note: null,
    provenance: prov("user.info"),
    data: {
      handle: "algo_dev",
      first_name: "Algo",
      last_name: "Dev",
      country: "Exampleland",
      city: "Springfield",
      organization: "Example University",
      rating: 1650,
      max_rating: 1920,
      rank: "expert",
      max_rank: "candidate master",
      contribution: 5,
      registered_at: "2017-07-14T02:40:00Z",
      last_online_at: T,
      profile_url: "https://codeforces.com/profile/algo_dev",
    },
  },
  rating_history: {
    status: "available",
    note: null,
    provenance: prov("user.rating", "https://codeforces.com/contests/with/algo_dev"),
    items: [
      {
        contest_id: 1000,
        contest_name: "Codeforces Round 1 (Div. 2)",
        rank: 900,
        old_rating: 1500,
        new_rating: 1600,
        delta: 100,
        rating_updated_at: "2026-08-01T00:00:00Z",
        contest_url: "https://codeforces.com/contest/1000",
      },
      {
        contest_id: 1010,
        contest_name: "Codeforces Round 2 (Div. 2)",
        rank: 1200,
        old_rating: 1700,
        new_rating: 1650,
        delta: -50,
        rating_updated_at: "2026-09-01T00:00:00Z",
        contest_url: "https://codeforces.com/contest/1010",
      },
    ],
    skipped_malformed: 0,
  },
  submissions: {
    status: "available",
    note: null,
    provenance: prov("user.status", "https://codeforces.com/submissions/algo_dev"),
    requested_limit: 6,
    truncated: true,
    skipped_malformed: 0,
    window_start: "2026-10-01T00:00:00Z",
    window_end: "2026-10-30T00:00:00Z",
    items: [
      {
        id: 1,
        contest_id: 1010,
        problem_index: "C",
        problem_name: "Coins",
        problem_rating: 1600,
        tags: ["dp"],
        programming_language: "C++17",
        verdict: "OK",
        passed_test_count: 12,
        submitted_at: "2026-10-30T00:00:00Z",
        relative_time_seconds: 3600,
        participant_type: "CONTESTANT",
        problem_key: "1010/C",
        problem_url: "https://codeforces.com/contest/1010/problem/C",
      },
      {
        id: 2,
        contest_id: 1010,
        problem_index: "C",
        problem_name: "Coins",
        problem_rating: 1600,
        tags: ["dp"],
        programming_language: "C++17",
        verdict: "WRONG_ANSWER",
        passed_test_count: 3,
        submitted_at: "2026-10-29T00:00:00Z",
        relative_time_seconds: null,
        participant_type: "PRACTICE",
        problem_key: "1010/C",
        problem_url: "https://codeforces.com/contest/1010/problem/C",
      },
    ],
  },
  analytics: {
    status: "available",
    note: "Derived from the 6 most recent submissions only",
    provenance: prov("derived: user.info, user.rating, user.status"),
    lifetime: { scope: "lifetime", current_rating: 1650, max_rating: 1920, rank: "expert", max_rank: "candidate master", rated_contests: 2 },
    submissions: STATS,
  },
  warnings: [],
  api_requests: 3,
};

export function cfProfile(overrides: Record<string, unknown> = {}) {
  return {
    id: "p0000000-0000-4000-8000-0000000000cf",
    candidate_id: CID,
    platform: "codeforces",
    handle: "algo_dev",
    profile_url: "https://codeforces.com/profile/algo_dev",
    ownership_status: "unverified",
    collection_status: "collected",
    sync_status: "completed",
    sync_error_code: null,
    sync_error: null,
    sync_warnings: ["Only the most recent 6 submissions were collected (limit 6)"],
    last_sync_attempt_at: T,
    collected_at: T,
    freshness: { collected_at: T, age_seconds: 60, stale: false, stale_after_hours: 168 },
    created_at: T,
    data: SNAPSHOT,
    ...overrides,
  };
}

export const CF_STATUS_ENABLED = {
  enabled: true,
  api_base_url: "https://codeforces.com/api/",
  api_methods: ["user.info", "user.rating", "user.status"],
  min_request_interval_seconds: 2,
  submission_limit: 500,
  submission_hard_max: 1000,
  requests_made: 0,
  last_success_at: null,
  last_error: null,
  last_error_at: null,
  note: "Public API methods only",
};

export const CF_STATUS_DISABLED = { ...CF_STATUS_ENABLED, enabled: false, note: "Disabled" };

export const CF_NOT_FOUND = { error: { code: "not_found", message: "This candidate has no Codeforces profile" } };
