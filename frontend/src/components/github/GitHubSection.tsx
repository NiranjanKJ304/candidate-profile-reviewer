"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge, when } from "@/components/platform/StatusBadge";
import type { ApiClient } from "@/lib/apiClient";
import {
  AVAILABILITY_TEXT,
  ERROR_EXPLANATIONS,
  type GitHubProfile,
  type GitHubStatus,
  addGitHubProfile,
  checkGitHubInput,
  getGitHubProfile,
  getGitHubStatus,
  removeGitHubProfile,
  syncGitHubProfile,
} from "@/lib/github";

const MAX_POLLS = 200;

interface Props {
  api: ApiClient;
  candidateId: string;
  pollIntervalMs?: number;
}

export default function GitHubSection({ api, candidateId, pollIntervalMs = 1500 }: Props) {
  const [status, setStatus] = useState<GitHubStatus | null>(null);
  const [profile, setProfile] = useState<GitHubProfile | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollKey, setPollKey] = useState(0);

  const load = useCallback(async () => {
    const r = await getGitHubProfile(api, candidateId);
    if (r.kind === "ok") setProfile(r.data);
    else if (r.kind === "invalid" && r.httpStatus === 404) setProfile(null);
    else setError(`Could not load the GitHub profile: ${r.message}`);
    setLoaded(true);
  }, [api, candidateId]);

  useEffect(() => {
    let cancelled = false;
    void getGitHubStatus(api).then((r) => {
      if (!cancelled && r.kind === "ok") setStatus(r.data);
    });
    void getGitHubProfile(api, candidateId).then((r) => {
      if (cancelled) return;
      if (r.kind === "ok") setProfile(r.data);
      else if (!(r.kind === "invalid" && r.httpStatus === 404)) setError(`Could not load the GitHub profile: ${r.message}`);
      setLoaded(true);
    });
    return () => {
      cancelled = true;
    };
  }, [api, candidateId]);

  const syncing = profile?.sync_status === "pending" || profile?.sync_status === "processing";

  useEffect(() => {
    if (!syncing) return;
    let cancelled = false;
    let polls = 0;
    const tick = async () => {
      if (cancelled) return;
      polls += 1;
      const r = await getGitHubProfile(api, candidateId);
      if (cancelled) return;
      if (r.kind === "ok") {
        setProfile(r.data);
        if (r.data.sync_status === "completed" || r.data.sync_status === "failed") return;
      }
      if (polls < MAX_POLLS) timer = setTimeout(tick, pollIntervalMs);
    };
    let timer = setTimeout(tick, pollIntervalMs);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [api, candidateId, syncing, pollKey, pollIntervalMs]);

  async function onConnect(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const problem = checkGitHubInput(input);
    if (problem) return setError(problem);
    setBusy(true);
    try {
      const r = await addGitHubProfile(api, candidateId, input.trim());
      if (r.kind !== "ok") return setError(r.message);
      setProfile(r.data);
      setInput("");
      setPollKey((k) => k + 1);
    } finally {
      setBusy(false);
    }
  }

  async function onRetry() {
    setError(null);
    const r = await syncGitHubProfile(api, candidateId);
    if (r.kind !== "ok") return setError(`Could not start synchronisation: ${r.message}`);
    setProfile(r.data);
    setPollKey((k) => k + 1);
  }

  async function onRemove() {
    setError(null);
    const r = await removeGitHubProfile(api, candidateId);
    if (r.kind !== "ok") return setError(`Could not remove the profile: ${r.message}`);
    await load();
  }

  const snap = profile?.data ?? null;
  const user = snap?.profile.data ?? null;
  const btn = "rounded-md border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800";

  return (
    <section aria-labelledby={`github-${candidateId}`} className="grid gap-4 rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id={`github-${candidateId}`} className="text-lg font-semibold">
          GitHub profile
        </h2>
        {status && (
          <p className="text-xs text-zinc-500" data-testid="github-integration">
            {status.enabled
              ? `${status.authenticated ? "Authenticated" : "Unauthenticated (60 requests/hour)"}${
                  status.rate_limits.find((l) => l.resource === "core")
                    ? ` · ${status.rate_limits.find((l) => l.resource === "core")?.remaining} requests left`
                    : ""
                }`
              : "Integration disabled on the server"}
          </p>
        )}
      </div>

      {status && !status.enabled && (
        <p role="note" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          The GitHub integration is disabled. Set ENABLED_PLATFORMS=github in the backend configuration and restart it.
        </p>
      )}

      {error && (
        <p role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </p>
      )}

      {loaded && !profile && (
        <form onSubmit={onConnect} noValidate aria-label="Connect GitHub profile" className="flex flex-wrap items-end gap-3">
          <label className="grid flex-1 gap-1 text-sm font-medium">
            GitHub username or profile URL
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="octocat or https://github.com/octocat"
              maxLength={300}
              autoComplete="off"
              className="rounded-md border border-zinc-300 px-3 py-2 font-normal dark:border-zinc-700 dark:bg-zinc-950"
            />
          </label>
          <button type="submit" disabled={busy || status?.enabled === false} className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-60">
            {busy ? "Connecting…" : "Connect"}
          </button>
        </form>
      )}

      {profile && (
        <>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="font-medium">
                {profile.profile_url ? (
                  <a href={profile.profile_url} target="_blank" rel="noopener noreferrer" className="underline-offset-2 hover:underline">
                    {profile.username}
                  </a>
                ) : (
                  profile.username
                )}
              </p>
              <p data-testid="github-sync-status" aria-live="polite" className="text-sm text-zinc-600 dark:text-zinc-400">
                {syncing
                  ? "Collecting public GitHub data…"
                  : profile.sync_status === "failed"
                    ? "Last synchronisation failed"
                    : "Synchronised"}
                {" · "}last successful sync: {when(profile.collected_at)}
              </p>
              <p className="text-xs text-zinc-500">
                Ownership not verified: a submitted profile is not proof that it belongs to the candidate.
              </p>
            </div>
            <div className="flex gap-2">
              <button type="button" onClick={() => void onRetry()} disabled={syncing} className={btn}>
                Retry sync
              </button>
              <button type="button" onClick={() => void onRemove()} className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-950">
                Remove
              </button>
            </div>
          </div>

          {profile.sync_status === "failed" && profile.sync_error_code && (
            <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
              <p className="font-medium">{ERROR_EXPLANATIONS[profile.sync_error_code] ?? profile.sync_error_code}</p>
              {profile.sync_error && <p className="text-xs">{profile.sync_error}</p>}
              {snap && <p className="mt-1 text-xs">Showing data from the last successful synchronisation.</p>}
            </div>
          )}

          {profile.sync_warnings.length > 0 && (
            <ul className="list-disc rounded-md border border-amber-300 bg-amber-50 p-3 pl-7 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
              {profile.sync_warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}

          {snap && user && (
            <div className="grid gap-4" data-testid="github-data">
              <dl className="grid gap-3 text-sm sm:grid-cols-3">
                {(
                  [
                    ["Name", user.name],
                    ["Company", user.company],
                    ["Location", user.location],
                    ["Public repositories", user.public_repos?.toString() ?? null],
                    ["Followers / following", user.followers !== null ? `${user.followers} / ${user.following ?? "?"}` : null],
                    ["Account created", user.created_at ? new Date(user.created_at).toLocaleDateString() : null],
                  ] as const
                ).map(([label, value]) => (
                  <div key={label}>
                    <dt className="text-xs uppercase tracking-wide text-zinc-500">{label}</dt>
                    <dd>{value ?? <span className="italic text-zinc-400">Not provided</span>}</dd>
                  </div>
                ))}
              </dl>
              {user.bio && <p className="text-sm text-zinc-700 dark:text-zinc-300">{user.bio}</p>}
              <p className="text-xs text-zinc-500">
                Follower and repository counts describe visibility, not coding ability.
              </p>

              <div className="grid gap-3 sm:grid-cols-2">
                {(["languages", "topics"] as const).map((key) => (
                  <div key={key}>
                    <p className="flex items-center gap-2 text-sm font-semibold">
                      {key === "languages" ? "Languages" : "Topics"} <Badge status={snap[key].status} />
                    </p>
                    {snap[key].items.length > 0 ? (
                      <ul className="mt-2 flex flex-wrap gap-1.5">
                        {snap[key].items.map((s) => (
                          <li key={s.name} className="rounded-full border border-zinc-300 px-2 py-0.5 text-xs dark:border-zinc-700">
                            {s.name} <span className="text-zinc-500">({s.repositories})</span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="mt-1 text-xs italic text-zinc-500">{snap[key].note ?? "None found."}</p>
                    )}
                  </div>
                ))}
              </div>

              <div>
                <p className="flex items-center gap-2 text-sm font-semibold">
                  Repositories ({snap.repositories.items.length}) <Badge status={snap.repositories.status} />
                </p>
                {snap.repositories.note && <p className="text-xs italic text-zinc-500">{snap.repositories.note}</p>}
                <ul className="mt-2 grid gap-2">
                  {snap.repositories.items.map((r) => (
                    <li key={r.full_name} className="rounded-md border border-zinc-200 p-3 text-sm dark:border-zinc-800">
                      <div className="flex flex-wrap items-center gap-2">
                        <a href={r.html_url} target="_blank" rel="noopener noreferrer" className="font-medium underline-offset-2 hover:underline">
                          {r.name}
                        </a>
                        {r.is_fork && <span className="text-xs text-zinc-500">fork</span>}
                        {r.is_archived && <span className="text-xs text-zinc-500">archived</span>}
                        {r.primary_language && <span className="text-xs text-indigo-700 dark:text-indigo-300">{r.primary_language}</span>}
                        <span className="text-xs text-zinc-500">
                          ★ {r.stargazers_count ?? "?"} · forks {r.forks_count ?? "?"}
                        </span>
                      </div>
                      {r.description && <p className="mt-1 text-zinc-600 dark:text-zinc-400">{r.description}</p>}
                      {r.topics.length > 0 && <p className="mt-1 text-xs text-zinc-500">Topics: {r.topics.join(", ")}</p>}
                      {r.readme && (
                        <p className="mt-1 text-xs text-zinc-500">
                          README: {AVAILABILITY_TEXT[r.readme.status]}
                          {r.readme.note ? ` (${r.readme.note})` : ""}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              </div>

              <dl className="grid gap-3 text-sm sm:grid-cols-3">
                <div>
                  <dt className="flex items-center gap-2 text-xs uppercase tracking-wide text-zinc-500">
                    Recent activity <Badge status={snap.activity.status} />
                  </dt>
                  <dd>
                    {snap.activity.events_analyzed} public events · {snap.activity.push_events} pushes
                    {snap.activity.commits_reported !== null ? ` · ${snap.activity.commits_reported} commits` : ""}
                  </dd>
                </div>
                {(["pull_requests", "issues"] as const).map((key) => (
                  <div key={key}>
                    <dt className="flex items-center gap-2 text-xs uppercase tracking-wide text-zinc-500">
                      {key === "pull_requests" ? "Pull requests" : "Issues"} <Badge status={snap[key].status} />
                    </dt>
                    <dd>{snap[key].total_count !== null ? `${snap[key].total_count} authored (public)` : "Unknown"}</dd>
                  </div>
                ))}
              </dl>
              <p className="text-xs text-zinc-500">
                {snap.activity.note} {snap.contribution_calendar.note}
              </p>
            </div>
          )}
        </>
      )}
    </section>
  );
}
