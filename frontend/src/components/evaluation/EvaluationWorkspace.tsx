"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import EvaluationResultView from "@/components/evaluation/EvaluationResultView";
import { when } from "@/components/platform/StatusBadge";
import { createApiClient } from "@/lib/apiClient";
import {
  EVALUATION_ERRORS,
  type Evaluation,
  type EvaluationSummary,
  FIT_TEXT,
  createEvaluation,
  deleteEvaluation,
  getEvaluation,
  listEvaluations,
  rerunEvaluation,
} from "@/lib/evaluations";
import { type CandidateSummary, listCandidates } from "@/lib/github";
import { type JobDescriptionSummary, listJobDescriptions } from "@/lib/jobDescriptions";

const MAX_POLLS = 200;

interface Props {
  apiBaseUrl: string | null;
  pollIntervalMs?: number;
}

const select = "rounded-md border border-zinc-300 px-2 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-950";

export default function EvaluationWorkspace({ apiBaseUrl, pollIntervalMs = 1500 }: Props) {
  const api = useMemo(() => (apiBaseUrl ? createApiClient(apiBaseUrl) : null), [apiBaseUrl]);
  const [candidates, setCandidates] = useState<CandidateSummary[]>([]);
  const [jds, setJds] = useState<JobDescriptionSummary[]>([]);
  const [candidateId, setCandidateId] = useState("");
  const [jdId, setJdId] = useState("");
  const [history, setHistory] = useState<EvaluationSummary[]>([]);
  const [current, setCurrent] = useState<Evaluation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pollKey, setPollKey] = useState(0);

  useEffect(() => {
    if (!api) return;
    let cancelled = false;
    void listCandidates(api).then((r) => {
      if (!cancelled && r.kind === "ok") setCandidates(r.data.items);
    });
    void listJobDescriptions(api, 100).then((r) => {
      if (!cancelled && r.kind === "ok") setJds(r.data.items.filter((j) => j.parse_status === "completed"));
    });
    return () => {
      cancelled = true;
    };
  }, [api]);

  const refreshHistory = useCallback(
    async (cid: string) => {
      if (!api || !cid) return setHistory([]);
      const r = await listEvaluations(api, cid);
      if (r.kind === "ok") setHistory(r.data.items);
    },
    [api],
  );

  useEffect(() => {
    if (!api || !candidateId) return;
    let cancelled = false;
    void listEvaluations(api, candidateId).then((r) => {
      if (!cancelled && r.kind === "ok") setHistory(r.data.items);
    });
    return () => {
      cancelled = true;
    };
  }, [api, candidateId]);

  const running = current?.status === "pending" || current?.status === "running";

  useEffect(() => {
    if (!api || !current || !running) return;
    let cancelled = false;
    let polls = 0;
    const id = current.id;
    const tick = async () => {
      if (cancelled) return;
      polls += 1;
      const r = await getEvaluation(api, id);
      if (cancelled) return;
      if (r.kind === "ok") {
        setCurrent(r.data);
        if (r.data.status === "completed" || r.data.status === "failed") {
          void refreshHistory(r.data.candidate_id);
          return;
        }
      }
      if (polls < MAX_POLLS) timer = setTimeout(tick, pollIntervalMs);
    };
    let timer = setTimeout(tick, pollIntervalMs);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- poll per evaluation id / restart key
  }, [api, current?.id, running, pollKey, pollIntervalMs, refreshHistory]);

  if (!api) {
    return (
      <p role="alert" className="text-sm text-red-700 dark:text-red-400">
        Configuration error: NEXT_PUBLIC_API_BASE_URL is not a valid http(s) URL.
      </p>
    );
  }

  async function onEvaluate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!api) return;
    setError(null);
    if (!candidateId || !jdId) return setError("Choose a candidate and a parsed job description.");
    setBusy(true);
    try {
      const r = await createEvaluation(api, candidateId, jdId);
      if (r.kind !== "ok") return setError(r.message);
      setCurrent(r.data);
      setPollKey((k) => k + 1);
      void refreshHistory(candidateId);
    } finally {
      setBusy(false);
    }
  }

  async function open(id: string) {
    if (!api) return;
    setError(null);
    const r = await getEvaluation(api, id);
    if (r.kind === "ok") setCurrent(r.data);
    else setError(`Could not load the evaluation: ${r.message}`);
  }

  async function onRerun() {
    if (!api || !current) return;
    setError(null);
    const r = await rerunEvaluation(api, current.id);
    if (r.kind !== "ok") return setError(`Could not re-run: ${r.message}`);
    setCurrent(r.data);
    setPollKey((k) => k + 1);
  }

  async function onDelete() {
    if (!api || !current) return;
    const r = await deleteEvaluation(api, current.id);
    if (r.kind !== "ok") return setError(`Could not delete: ${r.message}`);
    setCurrent(null);
    void refreshHistory(candidateId);
  }

  const jdTitle = (id: string) => jds.find((j) => j.id === id)?.title ?? "Job description";

  return (
    <div className="grid gap-6">
      <form onSubmit={onEvaluate} noValidate aria-label="Start evaluation" className="flex flex-wrap items-end gap-3">
        <label className="grid gap-1 text-sm font-medium">
          Candidate
          <select value={candidateId} onChange={(e) => setCandidateId(e.target.value)} className={select}>
            <option value="">Choose…</option>
            {candidates.map((c) => (
              <option key={c.id} value={c.id}>
                {c.full_name}
              </option>
            ))}
          </select>
        </label>
        <label className="grid gap-1 text-sm font-medium">
          Job description (parsed)
          <select value={jdId} onChange={(e) => setJdId(e.target.value)} className={select}>
            <option value="">Choose…</option>
            {jds.map((j) => (
              <option key={j.id} value={j.id}>
                {j.title ?? j.original_filename ?? j.id}
                {j.has_review ? " (reviewed)" : ""}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={busy} className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-60">
          {busy ? "Starting…" : "Evaluate"}
        </button>
        <p className="w-full text-xs text-zinc-500">
          Uses the candidate&apos;s most recent parsed resume plus any linked GitHub, Codeforces and LeetCode evidence. Personal details
          are removed before anything is sent to the LLM.
        </p>
      </form>

      {error && (
        <p role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </p>
      )}

      {candidateId && history.length > 0 && (
        <section aria-labelledby="history-heading">
          <h2 id="history-heading" className="text-sm font-semibold">
            Previous evaluations
          </h2>
          <ul className="mt-2 grid gap-1 text-sm">
            {history.map((h) => (
              <li key={h.id}>
                <button type="button" onClick={() => void open(h.id)} className="w-full rounded-md px-3 py-1.5 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800">
                  {h.job_title ?? jdTitle(h.job_description_id)} · {h.status}
                  {h.overall_score !== null ? ` · ${Math.round(h.overall_score)}/100` : ""}
                  {h.fit_label ? ` · ${FIT_TEXT[h.fit_label]}` : ""} · {when(h.created_at)}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {current && (
        <section aria-labelledby="evaluation-heading" className="grid gap-4 rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 id="evaluation-heading" className="text-lg font-semibold">
                {current.result?.job_title ?? jdTitle(current.job_description_id)}
              </h2>
              <p className="text-sm text-zinc-600 dark:text-zinc-400" data-testid="evaluation-status" aria-live="polite">
                {running ? "Evaluating… (collecting evidence and asking the LLM)" : current.status === "failed" ? "Evaluation failed" : "Completed"}
                {current.completed_at ? ` · ${when(current.completed_at)}` : ""}
                {current.model_name ? ` · ${current.model_name}` : ""}
              </p>
            </div>
            <div className="flex gap-2">
              <button type="button" onClick={() => void onRerun()} disabled={running} className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-50 disabled:opacity-60 dark:border-zinc-700 dark:hover:bg-zinc-800">
                Re-run
              </button>
              <button type="button" onClick={() => void onDelete()} disabled={running} className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 disabled:opacity-60 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-950">
                Delete
              </button>
            </div>
          </div>

          {current.status === "failed" && (
            <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
              <p className="font-medium">{(current.error_code && EVALUATION_ERRORS[current.error_code]) ?? "The evaluation failed."}</p>
              {current.error_message && <p className="text-xs">{current.error_message}</p>}
            </div>
          )}

          {current.warnings.length > 0 && (
            <ul className="list-disc rounded-md border border-amber-300 bg-amber-50 p-3 pl-7 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
              {current.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}

          {current.result && <EvaluationResultView result={current.result} />}
        </section>
      )}
    </div>
  );
}
