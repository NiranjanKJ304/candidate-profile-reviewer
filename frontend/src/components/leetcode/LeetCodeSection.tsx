"use client";

import { useEffect, useState } from "react";

import { day, when } from "@/components/platform/StatusBadge";
import type { ApiClient } from "@/lib/apiClient";
import {
  EMPTY_FORM,
  type Evidence,
  type EvidenceForm,
  type LeetCodeProfile,
  type LeetCodeStatus,
  type NamedCount,
  addLeetCodeProfile,
  buildEvidencePayload,
  checkLeetCodeInput,
  clearLeetCodeEvidence,
  formFromEvidence,
  getLeetCodeProfile,
  getLeetCodeStatus,
  removeLeetCodeProfile,
  saveLeetCodeEvidence,
} from "@/lib/leetcode";

interface Props {
  api: ApiClient;
  candidateId: string;
}

const input = "rounded-md border border-zinc-300 px-2 py-1.5 font-normal dark:border-zinc-700 dark:bg-zinc-950";
const btn = "rounded-md border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800";

function SelfReported() {
  return (
    <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">
      Self-reported · not verified
    </span>
  );
}

function Provided({ status }: { status: string }) {
  return status === "available" ? (
    <span className="text-xs text-zinc-500">provided</span>
  ) : (
    <span className="text-xs italic text-zinc-500">not provided (unknown, not zero)</span>
  );
}

function Value({ label, value }: { label: string; value: string | number | null }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-zinc-500">{label}</dt>
      <dd>{value ?? <span className="italic text-zinc-400">Not provided</span>}</dd>
    </div>
  );
}

function Counts({ items }: { items: NamedCount[] }) {
  return (
    <ul className="mt-1 flex flex-wrap gap-1.5">
      {items.map((i) => (
        <li key={i.name} className="rounded-full border border-zinc-300 px-2 py-0.5 text-xs dark:border-zinc-700">
          {i.name}
          {i.problems_solved !== null && <span className="text-zinc-500"> ({i.problems_solved})</span>}
        </li>
      ))}
    </ul>
  );
}

function EvidenceView({ ev }: { ev: Evidence }) {
  return (
    <div className="grid gap-4" data-testid="leetcode-evidence">
      <p className="flex flex-wrap items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400">
        <SelfReported />
        Reported by the {ev.reported_by === "candidate" ? "candidate" : "recruiter (from the candidate's statement)"} · as of{" "}
        {day(ev.as_of) ?? "unknown date"} · submitted {when(ev.submitted_at)} · revision {ev.revision}
      </p>
      <div>
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          Solved problems <Provided status={ev.solved.status} />
        </h3>
        <dl className="mt-1 grid gap-3 text-sm sm:grid-cols-4">
          <Value label="Total" value={ev.solved.total} />
          <Value label="Easy" value={ev.solved.easy} />
          <Value label="Medium" value={ev.solved.medium} />
          <Value label="Hard" value={ev.solved.hard} />
        </dl>
      </div>
      <div>
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          Contests <Provided status={ev.contest.status} />
        </h3>
        <dl className="mt-1 grid gap-3 text-sm sm:grid-cols-3">
          <Value label="Rating" value={ev.contest.rating} />
          <Value label="Attended" value={ev.contest.attended} />
          <Value label="Top %" value={ev.contest.top_percentage} />
        </dl>
        {ev.contest_history.items.length > 0 && (
          <ul aria-label="Contest history" className="mt-2 grid gap-1 text-xs">
            {ev.contest_history.items.map((c) => (
              <li key={`${c.contest_name}-${c.contest_date ?? ""}`}>
                {c.contest_name}
                {c.rank !== null && ` · rank ${c.rank}`}
                {c.rating_after !== null && ` · rating ${c.rating_after}`}
                {c.contest_date && ` · ${day(c.contest_date)}`}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            Languages <Provided status={ev.languages.status} />
          </h3>
          <Counts items={ev.languages.items} />
        </div>
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            Topics <Provided status={ev.topics.status} />
          </h3>
          <Counts items={ev.topics.items} />
        </div>
      </div>
      {ev.notes && (
        <p className="whitespace-pre-line text-sm text-zinc-700 dark:text-zinc-300">
          <span className="font-semibold">Notes: </span>
          {ev.notes}
        </p>
      )}
    </div>
  );
}

export default function LeetCodeSection({ api, candidateId }: Props) {
  const [status, setStatus] = useState<LeetCodeStatus | null>(null);
  const [profile, setProfile] = useState<LeetCodeProfile | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [handle, setHandle] = useState("");
  const [form, setForm] = useState<EvidenceForm>(EMPTY_FORM);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    void getLeetCodeStatus(api).then((r) => {
      if (!cancelled && r.kind === "ok") setStatus(r.data);
    });
    void getLeetCodeProfile(api, candidateId).then((r) => {
      if (cancelled) return;
      if (r.kind === "ok") setProfile(r.data);
      else if (!(r.kind === "invalid" && r.httpStatus === 404)) setErrors([`Could not load the LeetCode profile: ${r.message}`]);
      setLoaded(true);
    });
    return () => {
      cancelled = true;
    };
  }, [api, candidateId]);

  async function onLink(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setErrors([]);
    const problem = checkLeetCodeInput(handle);
    if (problem) return setErrors([problem]);
    setBusy(true);
    try {
      const r = await addLeetCodeProfile(api, candidateId, handle.trim());
      if (r.kind !== "ok") return setErrors([r.message]);
      setProfile(r.data);
      setHandle("");
    } finally {
      setBusy(false);
    }
  }

  function startEditing() {
    setErrors([]);
    setForm(profile?.evidence ? formFromEvidence(profile.evidence) : EMPTY_FORM);
    setEditing(true);
  }

  async function onSave(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const { payload, errors: problems } = buildEvidencePayload(form);
    if (!payload) return setErrors(problems);
    setBusy(true);
    setErrors([]);
    try {
      const r = await saveLeetCodeEvidence(api, candidateId, payload);
      if (r.kind !== "ok") return setErrors(r.kind === "invalid" && r.details?.length ? r.details : [r.message]);
      setProfile(r.data);
      setEditing(false);
    } finally {
      setBusy(false);
    }
  }

  async function onClear() {
    setErrors([]);
    const r = await clearLeetCodeEvidence(api, candidateId);
    if (r.kind !== "ok") return setErrors([`Could not clear the evidence: ${r.message}`]);
    setProfile((p) => (p ? { ...p, evidence: null } : p));
  }

  async function onRemove() {
    setErrors([]);
    const r = await removeLeetCodeProfile(api, candidateId);
    if (r.kind !== "ok") return setErrors([`Could not remove the profile: ${r.message}`]);
    setProfile(null);
    setEditing(false);
  }

  const set = (key: keyof EvidenceForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));
  const disabled = status?.enabled === false;

  return (
    <section aria-labelledby={`leetcode-${candidateId}`} className="grid gap-4 rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id={`leetcode-${candidateId}`} className="text-lg font-semibold">
          LeetCode profile
        </h2>
        <p className="text-xs text-zinc-500" data-testid="leetcode-mode">
          Manual, self-reported evidence only · LeetCode is never contacted
        </p>
      </div>

      <div role="note" className="rounded-md border border-zinc-300 bg-zinc-50 p-3 text-sm text-zinc-700 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-300">
        <p>
          LeetCode offers no official or permitted data API, and its Terms of Service forbid scraping. This system therefore
          collects nothing from LeetCode. Anything shown below was entered manually and is{" "}
          <strong>self-reported and not verified</strong>.
        </p>
        {status && (
          <details className="mt-1 text-xs">
            <summary className="cursor-pointer">Feasibility findings ({status.investigated_on})</summary>
            <ul className="mt-1 list-disc pl-5">
              {status.findings.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
            <p className="mt-1">
              Sources:{" "}
              {status.sources.map((s, i) => (
                <span key={s}>
                  {i > 0 && ", "}
                  <a href={s} target="_blank" rel="noopener noreferrer" className="underline">
                    {s}
                  </a>
                </span>
              ))}
            </p>
          </details>
        )}
      </div>

      {disabled && (
        <p role="note" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          The LeetCode integration is disabled. Add leetcode to ENABLED_PLATFORMS in the backend configuration and restart it.
        </p>
      )}

      {errors.length > 0 && (
        <ul role="alert" className="list-disc rounded-md border border-red-300 bg-red-50 p-3 pl-7 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
          {errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}

      {loaded && !profile && (
        <form onSubmit={onLink} noValidate aria-label="Link LeetCode profile" className="flex flex-wrap items-end gap-3">
          <label className="grid flex-1 gap-1 text-sm font-medium">
            LeetCode username or profile URL
            <input
              value={handle}
              onChange={(e) => setHandle(e.target.value)}
              placeholder="jane_doe or https://leetcode.com/u/jane_doe/"
              maxLength={300}
              autoComplete="off"
              className={`${input} py-2`}
            />
          </label>
          <button type="submit" disabled={busy || disabled} className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-60">
            {busy ? "Linking…" : "Link"}
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
              <p className="text-sm text-zinc-600 dark:text-zinc-400" data-testid="leetcode-platform-data">
                Platform data: not available (no permitted API) · no synchronisation
              </p>
              {profile.evidence && profile.freshness.stale && (
                <p className="text-xs text-amber-700 dark:text-amber-400">
                  This self-reported evidence is older than {profile.freshness.stale_after_days} days; ask for an update.
                </p>
              )}
            </div>
            <div className="flex gap-2">
              {!editing && (
                <button type="button" onClick={startEditing} disabled={disabled} className={btn}>
                  {profile.evidence ? "Edit evidence" : "Add self-reported evidence"}
                </button>
              )}
              {profile.evidence && !editing && (
                <button type="button" onClick={() => void onClear()} className={btn}>
                  Clear evidence
                </button>
              )}
              <button type="button" onClick={() => void onRemove()} className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-950">
                Remove
              </button>
            </div>
          </div>

          <ul className="list-disc rounded-md border border-amber-300 bg-amber-50 p-3 pl-7 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
            {profile.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>

          {editing && (
            <form onSubmit={onSave} noValidate aria-label="Self-reported LeetCode evidence" className="grid gap-3 rounded-md border border-zinc-200 p-4 text-sm dark:border-zinc-800">
              <p className="text-xs text-zinc-500">
                Leave a field empty if it is not known. Empty means &quot;not provided&quot;, never zero. Everything entered is stored as
                self-reported and not verified.
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="grid gap-1 font-medium">
                  Reported by
                  <select value={form.reported_by} onChange={set("reported_by")} className={input}>
                    <option value="">Choose…</option>
                    <option value="candidate">The candidate</option>
                    <option value="recruiter">A recruiter, from the candidate&apos;s statement</option>
                  </select>
                </label>
                <label className="grid gap-1 font-medium">
                  Figures as of
                  <input type="date" value={form.as_of} onChange={set("as_of")} className={input} />
                </label>
              </div>
              <div className="grid gap-3 sm:grid-cols-4">
                {(
                  [
                    ["total_solved", "Total solved"],
                    ["easy_solved", "Easy solved"],
                    ["medium_solved", "Medium solved"],
                    ["hard_solved", "Hard solved"],
                    ["contest_rating", "Contest rating"],
                    ["contests_attended", "Contests attended"],
                    ["contest_top_percentage", "Top %"],
                  ] as const
                ).map(([key, label]) => (
                  <label key={key} className="grid gap-1 font-medium">
                    {label}
                    <input inputMode="decimal" value={form[key]} onChange={set(key)} className={input} />
                  </label>
                ))}
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="grid gap-1 font-medium">
                  Languages (one per line, optional count: &quot;Python3: 250&quot;)
                  <textarea rows={3} value={form.languages} onChange={set("languages")} className={input} />
                </label>
                <label className="grid gap-1 font-medium">
                  Topics (one per line, optional count: &quot;Dynamic Programming: 60&quot;)
                  <textarea rows={3} value={form.topics} onChange={set("topics")} className={input} />
                </label>
              </div>
              <label className="grid gap-1 font-medium">
                Contest history (one per line: &quot;Contest name | rank | rating after | YYYY-MM-DD&quot;)
                <textarea rows={3} value={form.contest_history} onChange={set("contest_history")} className={input} />
              </label>
              <label className="grid gap-1 font-medium">
                Notes
                <textarea rows={2} maxLength={1000} value={form.notes} onChange={set("notes")} className={input} />
              </label>
              <div className="flex gap-2">
                <button type="submit" disabled={busy} className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-60">
                  {busy ? "Saving…" : "Save self-reported evidence"}
                </button>
                <button type="button" onClick={() => setEditing(false)} className={btn}>
                  Cancel
                </button>
              </div>
            </form>
          )}

          {profile.evidence && !editing && <EvidenceView ev={profile.evidence} />}
          {!profile.evidence && !editing && (
            <p className="text-sm text-zinc-500">No self-reported evidence yet. Missing evidence is not evidence of missing skills.</p>
          )}
        </>
      )}
    </section>
  );
}
