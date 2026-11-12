/**
 * GitHub integration API: types, runtime parsers and calls.
 * Mirrors app/schemas/github.py (only the fields the UI uses are typed strictly).
 */

import type { ApiClient, FetchResult } from "@/lib/apiClient";
import { bool, list, num, oneOf, optBool, optNum, optStr, rec, safe, str } from "@/lib/parse";

export const AVAILABILITY = [
  "available",
  "empty",
  "unavailable",
  "permission_denied",
  "rate_limited",
  "network_error",
  "error",
  "not_collected",
] as const;
export type Availability = (typeof AVAILABILITY)[number];
const SYNC = ["pending", "processing", "completed", "failed"] as const;
export type SyncStatus = (typeof SYNC)[number];
const COLLECTION = ["not_collected", "collected", "unavailable", "failed"] as const;
const OWNERSHIP = ["unverified", "verified", "mismatch"] as const;

export interface Section {
  status: Availability;
  note: string | null;
  source_url: string | null;
}
export interface CountStat {
  name: string;
  repositories: number;
}
export interface Repository {
  name: string;
  full_name: string;
  description: string | null;
  primary_language: string | null;
  html_url: string;
  pushed_at: string | null;
  stargazers_count: number | null;
  forks_count: number | null;
  is_fork: boolean;
  is_archived: boolean;
  topics: string[];
  readme: { status: Availability; excerpt: string | null; note: string | null } | null;
}
export interface Contribution {
  title: string;
  repository: string | null;
  state: string | null;
  html_url: string;
  merged: boolean | null;
}
export interface Snapshot {
  collected_at: string;
  authenticated: boolean;
  profile: Section & {
    data: {
      login: string;
      name: string | null;
      bio: string | null;
      company: string | null;
      location: string | null;
      public_repos: number | null;
      followers: number | null;
      following: number | null;
      created_at: string | null;
      html_url: string;
    } | null;
  };
  repositories: Section & { items: Repository[]; truncated: boolean };
  languages: Section & { items: CountStat[] };
  topics: Section & { items: CountStat[] };
  activity: Section & {
    events_analyzed: number;
    push_events: number;
    commits_reported: number | null;
    last_event_at: string | null;
  };
  pull_requests: Section & { total_count: number | null; items: Contribution[] };
  issues: Section & { total_count: number | null; items: Contribution[] };
  contribution_calendar: Section;
}
export interface GitHubProfile {
  id: string;
  candidate_id: string;
  username: string;
  profile_url: string | null;
  ownership_status: (typeof OWNERSHIP)[number];
  collection_status: (typeof COLLECTION)[number];
  sync_status: SyncStatus;
  sync_error_code: string | null;
  sync_error: string | null;
  sync_warnings: string[];
  last_sync_attempt_at: string | null;
  collected_at: string | null;
  data: Snapshot | null;
}
export interface GitHubStatus {
  enabled: boolean;
  authenticated: boolean;
  reachable: boolean | null;
  error: string | null;
  rate_limits: { resource: string; limit: number; remaining: number; reset_at: string | null }[];
}
export interface CandidateSummary {
  id: string;
  full_name: string;
  email: string | null;
}

// --------------------------------------------------------------------------- parsers
function section(v: unknown): Section {
  const r = rec(v);
  const prov = r.provenance == null ? {} : rec(r.provenance);
  return { status: oneOf(r.status, AVAILABILITY), note: optStr(r.note), source_url: optStr(prov.source_url) };
}
const stats = (v: unknown): CountStat[] =>
  list(v, (x) => {
    const r = rec(x);
    return { name: str(r.name), repositories: num(r.repositories) };
  });
const contributions = (v: unknown): Contribution[] =>
  list(v, (x) => {
    const r = rec(x);
    return {
      title: str(r.title),
      repository: optStr(r.repository),
      state: optStr(r.state),
      html_url: str(r.html_url),
      merged: optBool(r.merged),
    };
  });

function repository(v: unknown): Repository {
  const r = rec(v);
  const readme = r.readme == null ? null : rec(r.readme);
  return {
    name: str(r.name),
    full_name: str(r.full_name),
    description: optStr(r.description),
    primary_language: optStr(r.primary_language),
    html_url: str(r.html_url),
    pushed_at: optStr(r.pushed_at),
    stargazers_count: optNum(r.stargazers_count),
    forks_count: optNum(r.forks_count),
    is_fork: bool(r.is_fork),
    is_archived: bool(r.is_archived),
    topics: list(r.topics, str),
    readme: readme
      ? { status: oneOf(readme.status, AVAILABILITY), excerpt: optStr(readme.excerpt), note: optStr(readme.note) }
      : null,
  };
}

function snapshot(v: unknown): Snapshot {
  const r = rec(v);
  const p = rec(r.profile);
  const d = p.data == null ? null : rec(p.data);
  const repos = rec(r.repositories);
  const act = rec(r.activity);
  const prs = rec(r.pull_requests);
  const iss = rec(r.issues);
  return {
    collected_at: str(r.collected_at),
    authenticated: bool(r.authenticated),
    profile: {
      ...section(p),
      data: d
        ? {
            login: str(d.login),
            name: optStr(d.name),
            bio: optStr(d.bio),
            company: optStr(d.company),
            location: optStr(d.location),
            public_repos: optNum(d.public_repos),
            followers: optNum(d.followers),
            following: optNum(d.following),
            created_at: optStr(d.created_at),
            html_url: str(d.html_url),
          }
        : null,
    },
    repositories: { ...section(repos), items: list(repos.items, repository), truncated: bool(repos.truncated) },
    languages: { ...section(r.languages), items: stats(rec(r.languages).items) },
    topics: { ...section(r.topics), items: stats(rec(r.topics).items) },
    activity: {
      ...section(act),
      events_analyzed: num(act.events_analyzed),
      push_events: num(act.push_events),
      commits_reported: optNum(act.commits_reported),
      last_event_at: optStr(act.last_event_at),
    },
    pull_requests: { ...section(prs), total_count: optNum(prs.total_count), items: contributions(prs.items) },
    issues: { ...section(iss), total_count: optNum(iss.total_count), items: contributions(iss.items) },
    contribution_calendar: section(r.contribution_calendar),
  };
}

export const parseGitHubProfile = (v: unknown): GitHubProfile | null =>
  safe(() => {
    const r = rec(v);
    return {
      id: str(r.id),
      candidate_id: str(r.candidate_id),
      username: str(r.username),
      profile_url: optStr(r.profile_url),
      ownership_status: oneOf(r.ownership_status, OWNERSHIP),
      collection_status: oneOf(r.collection_status, COLLECTION),
      sync_status: oneOf(r.sync_status, SYNC),
      sync_error_code: optStr(r.sync_error_code),
      sync_error: optStr(r.sync_error),
      sync_warnings: list(r.sync_warnings, str),
      last_sync_attempt_at: optStr(r.last_sync_attempt_at),
      collected_at: optStr(r.collected_at),
      data: r.data == null ? null : snapshot(r.data),
    };
  });

export const parseGitHubStatus = (v: unknown): GitHubStatus | null =>
  safe(() => {
    const r = rec(v);
    return {
      enabled: bool(r.enabled),
      authenticated: bool(r.authenticated),
      reachable: optBool(r.reachable),
      error: optStr(r.error),
      rate_limits: list(r.rate_limits, (x) => {
        const l = rec(x);
        return { resource: str(l.resource), limit: num(l.limit), remaining: num(l.remaining), reset_at: optStr(l.reset_at) };
      }),
    };
  });

export const parseCandidatePage = (v: unknown): { items: CandidateSummary[]; total: number } | null =>
  safe(() => {
    const r = rec(v);
    return {
      items: list(r.items, (x) => {
        const c = rec(x);
        return { id: str(c.id), full_name: str(c.full_name), email: optStr(c.email) };
      }),
      total: num(r.total),
    };
  });

// --------------------------------------------------------------------------- calls
const gh = (candidateId: string) => `/candidates/${encodeURIComponent(candidateId)}/github`;

export const getGitHubStatus = (api: ApiClient) => api.get("/integrations/github/status", parseGitHubStatus);
export const getGitHubProfile = (api: ApiClient, candidateId: string) => api.get(gh(candidateId), parseGitHubProfile);
export const addGitHubProfile = (api: ApiClient, candidateId: string, profile: string): Promise<FetchResult<GitHubProfile>> =>
  api.post(gh(candidateId), { profile }, parseGitHubProfile);
export const syncGitHubProfile = (api: ApiClient, candidateId: string) =>
  api.post(`${gh(candidateId)}/sync`, undefined, parseGitHubProfile);
export const removeGitHubProfile = (api: ApiClient, candidateId: string) => api.delete(gh(candidateId));
export const listCandidates = (api: ApiClient, limit = 100) =>
  api.get(`/candidates?limit=${limit}&offset=0`, parseCandidatePage);

/** Light client-side check; the server performs the authoritative validation. */
export function checkGitHubInput(raw: string): string | null {
  const value = raw.trim();
  if (!value) return "Enter a GitHub username or profile URL.";
  if (value.length > 300) return "The value is too long.";
  if (/\s/.test(value)) return "The value must not contain spaces.";
  return null;
}

export const ERROR_EXPLANATIONS: Record<string, string> = {
  invalid_profile: "GitHub reports that this account does not exist or is not public.",
  not_a_user: "This is a GitHub organisation, not a personal account.",
  rate_limited: "GitHub's API rate limit was reached. Retry later or configure GITHUB_TOKEN on the server.",
  permission_denied: "GitHub denied access to this data.",
  auth_failed: "GitHub rejected the server's GITHUB_TOKEN.",
  network_error: "GitHub could not be reached from the server.",
  timeout: "GitHub did not respond in time.",
  api_error: "GitHub returned an unexpected error.",
  integration_disabled: "The GitHub integration is disabled on the server (ENABLED_PLATFORMS).",
  internal_error: "An unexpected error occurred while collecting the profile.",
};

export const AVAILABILITY_TEXT: Record<Availability, string> = {
  available: "Available",
  empty: "None found",
  unavailable: "Not available",
  permission_denied: "Access denied",
  rate_limited: "Rate limited",
  network_error: "Network error",
  error: "Error",
  not_collected: "Not collected",
};
