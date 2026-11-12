/** Synthetic LeetCode payloads for frontend tests (fictional account, self-reported). */

import { CID } from "@/test/githubFixtures";

export { CID };

const T = "2026-11-12T10:00:00Z";
const prov = {
  source_platform: "leetcode",
  source_type: "self_reported",
  verification_status: "not_verified",
  method: "manual_entry",
  reported_by: "candidate",
  claimed_profile_url: "https://leetcode.com/u/jane_doe/",
  submitted_at: T,
};

export const EVIDENCE = {
  schema_version: 1,
  source_platform: "leetcode",
  source_type: "self_reported",
  verification_status: "not_verified",
  reported_by: "candidate",
  submitted_at: T,
  as_of: "2026-11-01",
  revision: 2,
  solved: { status: "available", note: null, provenance: prov, total: 300, easy: 150, medium: 120, hard: 30 },
  contest: { status: "not_collected", note: "Not provided (unknown, not zero)", provenance: prov, rating: null, attended: null, top_percentage: null },
  contest_history: {
    status: "available",
    note: null,
    provenance: prov,
    items: [{ contest_name: "Weekly Contest 400", rank: 1234, rating_after: 1750, contest_date: "2026-10-20" }],
  },
  languages: { status: "available", note: null, provenance: prov, items: [{ name: "Python3", problems_solved: 250 }, { name: "C++", problems_solved: null }] },
  topics: { status: "not_collected", note: "Not provided (unknown, not zero)", provenance: prov, items: [] },
  notes: "Copied from my profile page.",
  warnings: ["Self-reported and not verified: LeetCode offers no permitted API, so these figures could not be checked against LeetCode"],
};

const WARNINGS = [
  "The username was not checked against LeetCode (no permitted API)",
  "Ownership not verified: a submitted profile is not proof that it belongs to the candidate",
];

export function lcProfile(overrides: Record<string, unknown> = {}) {
  return {
    id: "p0000000-0000-4000-8000-00000000001c",
    candidate_id: CID,
    platform: "leetcode",
    username: "jane_doe",
    profile_url: "https://leetcode.com/u/jane_doe/",
    ownership_status: "unverified",
    collection_status: "unavailable",
    sync_status: "not_applicable",
    integration_mode: "manual_self_reported",
    platform_data: {
      status: "unavailable",
      reason: "LeetCode offers no official or permitted data API.",
      sources: ["https://leetcode.com/terms/", "https://leetcode.com/robots.txt"],
    },
    evidence: EVIDENCE,
    freshness: { submitted_at: T, as_of: "2026-11-01", age_days: 12, stale: false, stale_after_days: 30 },
    warnings: [...WARNINGS, ...EVIDENCE.warnings],
    created_at: T,
    updated_at: T,
    ...overrides,
  };
}

export const LC_STATUS = {
  enabled: true,
  integration_mode: "manual_self_reported",
  platform_api: "unavailable",
  sync_supported: false,
  contacts_leetcode: false,
  investigated_on: "2026-11-13",
  findings: ["LeetCode publishes no official, documented public API.", "robots.txt disallows /graphql."],
  sources: ["https://leetcode.com/terms/", "https://leetcode.com/robots.txt"],
  evidence_stale_after_days: 30,
};

export const LC_STATUS_DISABLED = { ...LC_STATUS, enabled: false };
export const LC_NOT_FOUND = { error: { code: "not_found", message: "This candidate has no LeetCode profile" } };
