/** Synthetic GitHub API payloads for frontend tests (fictional account). */

export const CID = "c0000000-0000-4000-8000-0000000000aa";
const T = "2026-11-12T10:00:00Z";
const prov = (api: string, url: string | null = "https://github.com/octo-dev") => ({
  source_platform: "github",
  source_url: url,
  api_reference: api,
  collected_at: T,
});

export const SNAPSHOT = {
  schema_version: 1,
  source_platform: "github",
  collected_at: T,
  authenticated: false,
  profile: {
    status: "available",
    note: null,
    provenance: prov("GET /users/{username}"),
    data: {
      login: "octo-dev",
      id: 4242,
      account_type: "User",
      name: "Octo Dev",
      bio: "Backend developer",
      company: "Example Corp",
      location: "Springfield",
      blog: null,
      public_repos: 2,
      followers: 10,
      following: 2,
      created_at: "2018-04-01T10:00:00Z",
      updated_at: T,
      html_url: "https://github.com/octo-dev",
    },
  },
  repositories: {
    status: "available",
    note: null,
    provenance: prov("GET /users/{username}/repos"),
    items: [
      {
        name: "data-api",
        full_name: "octo-dev/data-api",
        description: "A FastAPI service",
        primary_language: "Python",
        html_url: "https://github.com/octo-dev/data-api",
        created_at: T,
        updated_at: T,
        pushed_at: T,
        stargazers_count: 3,
        forks_count: 1,
        is_fork: false,
        is_archived: false,
        topics: ["fastapi"],
        readme: { status: "available", excerpt: "# Data API", size_bytes: 10, truncated: false, source_url: null, note: null },
      },
      {
        name: "forked-lib",
        full_name: "octo-dev/forked-lib",
        description: null,
        primary_language: "Go",
        html_url: "https://github.com/octo-dev/forked-lib",
        created_at: T,
        updated_at: T,
        pushed_at: T,
        stargazers_count: 0,
        forks_count: 0,
        is_fork: true,
        is_archived: false,
        topics: [],
        readme: null,
      },
    ],
    reported_public_repos: 2,
    truncated: false,
    skipped_malformed: 0,
  },
  languages: { status: "available", note: "Primary language", provenance: prov("derived"), items: [{ name: "Python", repositories: 1 }] },
  topics: { status: "empty", note: "No topics set.", provenance: prov("derived"), items: [] },
  activity: {
    status: "available",
    note: "Based on GitHub's public events feed (90 days).",
    provenance: prov("GET /users/{username}/events/public"),
    events_analyzed: 3,
    event_counts: { PushEvent: 1 },
    push_events: 1,
    commits_reported: 2,
    active_repositories: ["octo-dev/data-api"],
    first_event_at: T,
    last_event_at: T,
    truncated: false,
  },
  pull_requests: { status: "available", note: null, provenance: prov("search"), total_count: 4, incomplete_results: false, items: [] },
  issues: { status: "rate_limited", note: "Rate limited", provenance: prov("search"), total_count: null, incomplete_results: false, items: [] },
  contribution_calendar: { status: "unavailable", note: "Not offered by the REST API.", provenance: prov("not requested") },
  warnings: [],
  api_requests: 7,
};

export function profile(overrides: Record<string, unknown> = {}) {
  return {
    id: "p0000000-0000-4000-8000-0000000000aa",
    candidate_id: CID,
    platform: "github",
    username: "octo-dev",
    profile_url: "https://github.com/octo-dev",
    ownership_status: "unverified",
    collection_status: "collected",
    sync_status: "completed",
    sync_error_code: null,
    sync_error: null,
    sync_warnings: ["Collected without GITHUB_TOKEN (60 requests/hour); set a token for higher limits"],
    last_sync_attempt_at: T,
    collected_at: T,
    created_at: T,
    data: SNAPSHOT,
    ...overrides,
  };
}

export const STATUS_ENABLED = {
  enabled: true,
  authenticated: false,
  reachable: true,
  rate_limits: [
    { resource: "core", limit: 60, remaining: 52, reset_at: T },
    { resource: "search", limit: 10, remaining: 10, reset_at: T },
  ],
  error: null,
  limits: { max_repositories: 300 },
};

export const STATUS_DISABLED = { ...STATUS_ENABLED, enabled: false, reachable: null, rate_limits: [], error: "Disabled" };

export const NOT_FOUND = { error: { code: "not_found", message: "This candidate has no GitHub profile" } };
