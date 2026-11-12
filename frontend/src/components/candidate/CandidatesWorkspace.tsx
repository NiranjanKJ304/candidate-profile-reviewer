"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import CodeforcesSection from "@/components/codeforces/CodeforcesSection";
import GitHubSection from "@/components/github/GitHubSection";
import LeetCodeSection from "@/components/leetcode/LeetCodeSection";
import { createApiClient } from "@/lib/apiClient";
import { type CandidateSummary, listCandidates } from "@/lib/github";
import { createCandidate } from "@/lib/resumes";

interface Props {
  apiBaseUrl: string | null;
  pollIntervalMs?: number;
}

export default function CandidatesWorkspace({ apiBaseUrl, pollIntervalMs }: Props) {
  const api = useMemo(() => (apiBaseUrl ? createApiClient(apiBaseUrl) : null), [apiBaseUrl]);
  const [items, setItems] = useState<CandidateSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!api) return;
    const r = await listCandidates(api);
    if (r.kind === "ok") setItems(r.data.items);
  }, [api]);

  useEffect(() => {
    if (!api) return;
    let cancelled = false;
    void listCandidates(api).then((r) => {
      if (!cancelled && r.kind === "ok") setItems(r.data.items);
    });
    return () => {
      cancelled = true;
    };
  }, [api]);

  if (!api) {
    return (
      <p role="alert" className="text-sm text-red-700 dark:text-red-400">
        Configuration error: NEXT_PUBLIC_API_BASE_URL is not a valid http(s) URL.
      </p>
    );
  }

  async function onCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!api) return;
    setError(null);
    if (!name.trim()) return setError("Enter the candidate's full name.");
    const r = await createCandidate(api, { full_name: name.trim() });
    if (r.kind !== "ok") return setError(`Could not create the candidate: ${r.message}`);
    setName("");
    await refresh();
    setSelected(r.data.id);
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
      <section aria-labelledby="candidates-heading" className="grid content-start gap-3">
        <h2 id="candidates-heading" className="text-sm font-semibold">
          Candidates ({items.length})
        </h2>
        <form onSubmit={onCreate} noValidate aria-label="Add candidate" className="flex gap-2">
          <label className="sr-only" htmlFor="new-candidate">
            New candidate name
          </label>
          <input
            id="new-candidate"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Full name"
            maxLength={200}
            className="min-w-0 flex-1 rounded-md border border-zinc-300 px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950"
          />
          <button type="submit" className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700">
            Add
          </button>
        </form>
        {error && (
          <p role="alert" className="text-sm text-red-700 dark:text-red-400">
            {error}
          </p>
        )}
        {items.length === 0 ? (
          <p className="text-sm text-zinc-500">No candidates yet.</p>
        ) : (
          <ul className="grid gap-1">
            {items.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => setSelected(c.id)}
                  aria-current={selected === c.id ? "true" : undefined}
                  className="w-full rounded-md px-3 py-2 text-left text-sm hover:bg-zinc-100 aria-[current=true]:bg-indigo-50 dark:hover:bg-zinc-800 dark:aria-[current=true]:bg-indigo-950"
                >
                  {c.full_name}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      {selected ? (
        <div key={selected} className="grid content-start gap-6">
          <GitHubSection api={api} candidateId={selected} pollIntervalMs={pollIntervalMs} />
          <CodeforcesSection api={api} candidateId={selected} pollIntervalMs={pollIntervalMs} />
          <LeetCodeSection api={api} candidateId={selected} />
        </div>
      ) : (
        <p className="text-sm text-zinc-500">Select or add a candidate to manage their developer profiles.</p>
      )}
    </div>
  );
}
