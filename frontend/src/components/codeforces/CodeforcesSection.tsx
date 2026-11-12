"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge, day, when } from "@/components/platform/StatusBadge";
import type { ApiClient } from "@/lib/apiClient";
import {
  type CodeforcesProfile,
  type CodeforcesStatus,
  ERROR_EXPLANATIONS,
  type NamedCount,
  addCodeforcesProfile,
  checkCodeforcesInput,
  getCodeforcesProfile,
  getCodeforcesStatus,
  removeCodeforcesProfile,
  syncCodeforcesProfile,
} from "@/lib/codeforces";

const MAX_POLLS = 200;
const RECENT = 10;

interface Props {
  api: ApiClient;
  candidateId: string;
  pollIntervalMs?: number;
}

function Chips({ items, label }: { items: NamedCount[]; label: string }) {
  if (items.length === 0) return <p className="mt-1 text-xs italic text-zinc-500">None in the collected window.</p>;
  return (
    <ul aria-label={label} className="mt-2 flex flex-wrap gap-1.5">
      {items.slice(0, 20).map((s) => (
        <li key={s.name} className="rounded-full border border-zinc-300 px-2 py-0.5 text-xs dark:border-zinc-700">
          {s.name} <span className="text-zinc-500">({s.count})</span>
        </li>
      ))}
    </ul>
  );
}

function Stat({ label, value, hint }: { label: string; value: string | number | null; hint?: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-zinc-500">{label}</dt>
      <dd>
        {value ?? <span className="italic text-zinc-400">Not available</span>}
        {hint && <span className="block text-xs text-zinc-500">{hint}</span>}
      </dd>
    </div>
  );
}

export default function CodeforcesSection({ api, candidateId, pollIntervalMs = 1500 }: Props) {
  const [status, setStatus] = useState<CodeforcesStatus | null>(null);
  const [profile, setProfile] = useState<CodeforcesProfile | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollKey, setPollKey] = useState(0);

  const load = useCallback(async () => {
    const r = await getCodeforcesProfile(api, candidateId);
    if (r.kind === "ok") setProfile(r.data);
    else if (r.kind === "invalid" && r.httpStatus === 404) setProfile(null);
    else setError(`Could not load the Codeforces profile: ${r.message}`);
    setLoaded(true);
  }, [api, candidateId]);

  useEffect(() => {
    let cancelled = false;
    void getCodeforcesStatus(api).then((r) => {
      if (!cancelled && r.kind === "ok") setStatus(r.data);
    });
    void getCodeforcesProfile(api, candidateId).then((r) => {
      if (cancelled) return;
      if (r.kind === "ok") setProfile(r.data);
      else if (!(r.kind === "invalid" && r.httpStatus === 404)) setError(`Could not load the Codeforces profile: ${r.message}`);
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
      const r = await getCodeforcesProfile(api, candidateId);
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
    const problem = checkCodeforcesInput(input);
    if (problem) return setError(problem);
    setBusy(true);
    try {
      const r = await addCodeforcesProfile(api, candidateId, input.trim());
      if (r.kind !== "ok") return setError(r.message);
      setProfile(r.data);
      setInput("");
      setPollKey((k) => k + 1);
    } finally {
      setBusy(false);
    }
  }

  async function onSync() {
    if (!profile) return;
    setError(null);
    const r = await syncCodeforcesProfile(api, candidateId, profile.sync_status === "failed");
    if (r.kind !== "ok") return setError(`Could not start synchronisation: ${r.message}`);
    setProfile(r.data);
    setPollKey((k) => k + 1);
  }

  async function onRemove() {
    setError(null);
    const r = await removeCodeforcesProfile(api, candidateId);
    if (r.kind !== "ok") return setError(`Could not remove the profile: ${r.message}`);
    await load();
  }

  const snap = profile?.data ?? null;
  const user = snap?.profile.data ?? null;
  const stats = snap?.analytics.submissions ?? null;
  const btn = "rounded-md border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800";
  const maxBucket = stats ? Math.max(1, ...stats.accepted_by_rating.map((b) => b.problems)) : 1;

  return (
    <section aria-labelledby={`codeforces-${candidateId}`} className="grid gap-4 rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id={`codeforces-${candidateId}`} className="text-lg font-semibold">
          Codeforces profile
        </h2>
        {status && (
          <p className="text-xs text-zinc-500" data-testid="codeforces-integration">
            {status.enabled
              ? `Public API · at most 1 request per ${status.min_request_interval_seconds} s · last ${status.submission_limit} submissions`
              : "Integration disabled on the server"}
          </p>
        )}
      </div>

      {status && !status.enabled && (
        <p role="note" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          The Codeforces integration is disabled. Add codeforces to ENABLED_PLATFORMS in the backend configuration and restart it.
        </p>
      )}

      {error && (
        <p role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </p>
      )}

      {loaded && !profile && (
        <form onSubmit={onConnect} noValidate aria-label="Connect Codeforces profile" className="flex flex-wrap items-end gap-3">
          <label className="grid flex-1 gap-1 text-sm font-medium">
            Codeforces handle or profile URL
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="tourist or https://codeforces.com/profile/tourist"
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
                    {profile.handle}
                  </a>
                ) : (
                  profile.handle
                )}
              </p>
              <p data-testid="codeforces-sync-status" aria-live="polite" className="text-sm text-zinc-600 dark:text-zinc-400">
                {syncing
                  ? "Collecting public Codeforces data…"
                  : profile.sync_status === "failed"
                    ? "Last synchronisation failed"
                    : "Synchronised"}
                {" · "}last successful sync: {when(profile.collected_at)}
              </p>
              {profile.collected_at && profile.freshness.stale && (
                <p className="text-xs text-amber-700 dark:text-amber-400">
                  This data is older than {Math.round(profile.freshness.stale_after_hours / 24)} days; refresh it before relying on it.
                </p>
              )}
              <p className="text-xs text-zinc-500">
                Ownership not verified: a submitted handle is not proof that it belongs to the candidate.
              </p>
            </div>
            <div className="flex gap-2">
              <button type="button" onClick={() => void onSync()} disabled={syncing} className={btn}>
                {profile.sync_status === "failed" ? "Retry sync" : "Refresh"}
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
            <div className="grid gap-5" data-testid="codeforces-data">
              <dl className="grid gap-3 text-sm sm:grid-cols-4">
                <Stat label="Name" value={[user.first_name, user.last_name].filter(Boolean).join(" ") || null} />
                <Stat label="Location" value={[user.city, user.country].filter(Boolean).join(", ") || null} />
                <Stat label="Organization" value={user.organization} />
                <Stat label="Contribution" value={user.contribution} />
                <Stat label="Rating (current / max)" value={user.rating !== null ? `${user.rating} / ${user.max_rating ?? "?"}` : "Unrated"} />
                <Stat label="Rank (current / max)" value={user.rank ? `${user.rank} / ${user.max_rank ?? "?"}` : null} />
                <Stat label="Registered" value={day(user.registered_at)} />
                <Stat label="Last online" value={day(user.last_online_at)} />
              </dl>
              <p className="text-xs text-zinc-500">
                Rating, rank and submission counts are partial signals, not a complete measure of programming ability.
              </p>

              <div>
                <h3 className="flex items-center gap-2 text-sm font-semibold">
                  Lifetime (reported by Codeforces)
                </h3>
                <dl className="mt-2 grid gap-3 text-sm sm:grid-cols-4">
                  <Stat label="Rated contests" value={snap.analytics.lifetime.rated_contests} />
                  <Stat label="Max rating" value={user.max_rating} />
                </dl>
              </div>

              <div>
                <h3 className="flex flex-wrap items-center gap-2 text-sm font-semibold">
                  Collected submission window <Badge status={snap.analytics.status} />
                </h3>
                <p className="text-xs text-zinc-500" data-testid="codeforces-window">
                  {stats
                    ? `${stats.total_submissions} most recent submissions${
                        stats.window_start ? ` (${day(stats.window_start)} – ${day(stats.window_end)})` : ""
                      }. ${stats.truncated ? "Older submissions were not collected, so these numbers do not cover the whole history." : "All submissions returned by Codeforces."}`
                    : (snap.analytics.note ?? "Submission statistics are unavailable.")}
                </p>
                {stats && (
                  <dl className="mt-2 grid gap-3 text-sm sm:grid-cols-5">
                    <Stat label="Submissions" value={stats.total_submissions} />
                    <Stat label="Accepted submissions" value={stats.accepted_submissions} />
                    <Stat label="Solved unique problems" value={stats.accepted_unique_problems} hint={`of ${stats.attempted_unique_problems} attempted`} />
                    <Stat label="Rejected submissions" value={stats.rejected_submissions} />
                    <Stat label="Contests in window" value={stats.contests_in_window} />
                  </dl>
                )}
              </div>

              {stats && (
                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <h3 className="text-sm font-semibold">Solved problems by difficulty</h3>
                    {stats.accepted_by_rating.length === 0 ? (
                      <p className="mt-1 text-xs italic text-zinc-500">None in the collected window.</p>
                    ) : (
                      <ul aria-label="Difficulty distribution" className="mt-2 grid gap-1 text-xs">
                        {stats.accepted_by_rating.map((b) => (
                          <li key={b.rating ?? "unrated"} className="flex items-center gap-2">
                            <span className="w-16 shrink-0 text-zinc-600 dark:text-zinc-400">{b.rating ?? "Unrated"}</span>
                            <span className="h-2 rounded bg-indigo-500" style={{ width: `${(b.problems / maxBucket) * 100}%`, minWidth: "0.25rem" }} />
                            <span>{b.problems}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <div className="grid gap-3">
                    <div>
                      <h3 className="text-sm font-semibold">Solved problems by topic</h3>
                      <Chips items={stats.accepted_by_tag} label="Topic distribution" />
                    </div>
                    <div>
                      <h3 className="text-sm font-semibold">Languages (submissions)</h3>
                      <Chips items={stats.languages} label="Language distribution" />
                    </div>
                    <div>
                      <h3 className="text-sm font-semibold">Verdicts</h3>
                      <Chips items={stats.verdicts} label="Verdict distribution" />
                    </div>
                  </div>
                </div>
              )}

              {stats && stats.monthly_activity.length > 0 && (
                <div>
                  <h3 className="text-sm font-semibold">Monthly activity</h3>
                  <ul className="mt-2 flex flex-wrap gap-1.5 text-xs">
                    {stats.monthly_activity.slice(-12).map((m) => (
                      <li key={m.month} className="rounded border border-zinc-200 px-2 py-1 dark:border-zinc-800">
                        {m.month}: {m.submissions} <span className="text-zinc-500">({m.accepted} accepted)</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div>
                <h3 className="flex items-center gap-2 text-sm font-semibold">
                  Contest history ({snap.rating_history.items.length} rated) <Badge status={snap.rating_history.status} />
                </h3>
                {snap.rating_history.note && <p className="text-xs italic text-zinc-500">{snap.rating_history.note}</p>}
                {snap.rating_history.items.length > 0 && (
                  <table className="mt-2 w-full text-left text-xs">
                    <thead className="text-zinc-500">
                      <tr>
                        <th className="py-1 font-normal">Contest</th>
                        <th className="py-1 font-normal">Rank</th>
                        <th className="py-1 font-normal">Rating</th>
                        <th className="py-1 font-normal">Date</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...snap.rating_history.items].reverse().slice(0, RECENT).map((c) => (
                        <tr key={c.contest_id} className="border-t border-zinc-100 dark:border-zinc-800">
                          <td className="py-1">
                            <a href={c.contest_url} target="_blank" rel="noopener noreferrer" className="underline-offset-2 hover:underline">
                              {c.contest_name ?? `Contest ${c.contest_id}`}
                            </a>
                          </td>
                          <td className="py-1">{c.rank ?? "?"}</td>
                          <td className="py-1">
                            {c.old_rating} → {c.new_rating}{" "}
                            <span className={c.delta >= 0 ? "text-emerald-700 dark:text-emerald-400" : "text-red-700 dark:text-red-400"}>
                              ({c.delta >= 0 ? "+" : ""}
                              {c.delta})
                            </span>
                          </td>
                          <td className="py-1">{day(c.rating_updated_at) ?? "?"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              <div>
                <h3 className="flex items-center gap-2 text-sm font-semibold">
                  Recent submissions <Badge status={snap.submissions.status} />
                </h3>
                {snap.submissions.note && <p className="text-xs italic text-zinc-500">{snap.submissions.note}</p>}
                <ul className="mt-2 grid gap-1 text-xs">
                  {snap.submissions.items.slice(0, RECENT).map((s) => (
                    <li key={s.id} className="flex flex-wrap gap-2 border-t border-zinc-100 py-1 dark:border-zinc-800">
                      {s.problem_url ? (
                        <a href={s.problem_url} target="_blank" rel="noopener noreferrer" className="font-medium underline-offset-2 hover:underline">
                          {s.problem_index ? `${s.problem_index}. ` : ""}
                          {s.problem_name ?? "Problem"}
                        </a>
                      ) : (
                        <span className="font-medium">{s.problem_name ?? "Problem"}</span>
                      )}
                      <span className={s.verdict === "OK" ? "text-emerald-700 dark:text-emerald-400" : "text-zinc-600 dark:text-zinc-400"}>
                        {s.verdict === "OK" ? "Accepted" : (s.verdict ?? "Judging")}
                      </span>
                      {s.problem_rating !== null && <span className="text-zinc-500">rating {s.problem_rating}</span>}
                      {s.programming_language && <span className="text-zinc-500">{s.programming_language}</span>}
                      <span className="text-zinc-500">{day(s.submitted_at)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
