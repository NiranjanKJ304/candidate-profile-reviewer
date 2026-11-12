"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { createApiClient } from "@/lib/apiClient";
import {
  type JobDescriptionDetail,
  type JobDescriptionSummary,
  type ParsedJDResponse,
  type ParsedJobDescription,
  checkJDFile,
  deleteJobDescription,
  discardReview,
  getJobDescription,
  getParsedJobDescription,
  jobDescriptionFileUrl,
  listJobDescriptions,
  reparseJobDescription,
  saveReview,
  uploadJobDescription,
} from "@/lib/jobDescriptions";
import { getCapabilities } from "@/lib/resumes";

import JDReviewEditor from "./JDReviewEditor";
import JDView from "./JDView";

const DEFAULT_MAX_BYTES = 5 * 1024 * 1024;
const MAX_POLLS = 200;

const STATUS_TEXT: Record<JobDescriptionSummary["parse_status"], string> = {
  pending: "Queued for parsing…",
  processing: "Extracting text and requirements…",
  completed: "Parsing complete",
  failed: "Parsing failed",
};

interface Props {
  apiBaseUrl: string | null;
  pollIntervalMs?: number;
}

export default function JobDescriptionWorkspace({ apiBaseUrl, pollIntervalMs = 1500 }: Props) {
  const api = useMemo(() => (apiBaseUrl ? createApiClient(apiBaseUrl) : null), [apiBaseUrl]);
  const [maxBytes, setMaxBytes] = useState(DEFAULT_MAX_BYTES);
  const [llmMissing, setLlmMissing] = useState(false);
  const [items, setItems] = useState<JobDescriptionSummary[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<JobDescriptionDetail | null>(null);
  const [parsed, setParsed] = useState<ParsedJDResponse | null>(null);
  const [editing, setEditing] = useState(false);
  const [pollKey, setPollKey] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);

  const refreshList = useCallback(async () => {
    if (!api) return;
    const r = await listJobDescriptions(api);
    if (r.kind === "ok") setItems(r.data.items);
  }, [api]);

  useEffect(() => {
    if (!api) return;
    let cancelled = false;
    void getCapabilities(api).then((r) => {
      if (cancelled || r.kind !== "ok") return;
      setLlmMissing(!r.data.llm.configured);
      if (r.data.job_description) setMaxBytes(r.data.job_description.max_bytes);
    });
    void listJobDescriptions(api).then((r) => {
      if (!cancelled && r.kind === "ok") setItems(r.data.items);
    });
    return () => {
      cancelled = true;
    };
  }, [api]);

  const select = useCallback(
    async (jdId: string) => {
      if (!api) return;
      setError(null);
      setEditing(false);
      setParsed(null);
      const r = await getJobDescription(api, jdId);
      if (r.kind !== "ok") return setError(`Could not load the job description: ${r.message}`);
      setDetail(r.data);
      if (r.data.parse_status === "completed") {
        const p = await getParsedJobDescription(api, jdId);
        if (p.kind === "ok") setParsed(p.data);
      }
    },
    [api],
  );

  const detailId = detail?.id ?? null;
  const status = detail?.parse_status ?? null;

  useEffect(() => {
    if (!api || !detailId || (status !== "pending" && status !== "processing")) return;
    let cancelled = false;
    let polls = 0;
    const tick = async () => {
      if (cancelled) return;
      polls += 1;
      const r = await getJobDescription(api, detailId);
      if (cancelled) return;
      if (r.kind === "ok") {
        setDetail(r.data);
        if (r.data.parse_status === "completed") {
          const p = await getParsedJobDescription(api, detailId);
          if (!cancelled && p.kind === "ok") setParsed(p.data);
          void refreshList();
          return;
        }
        if (r.data.parse_status === "failed") {
          void refreshList();
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
  }, [api, detailId, status, pollKey, pollIntervalMs, refreshList]);

  if (!api) {
    return (
      <p role="alert" className="text-sm text-red-700 dark:text-red-400">
        Configuration error: NEXT_PUBLIC_API_BASE_URL is not a valid http(s) URL.
      </p>
    );
  }

  async function onUpload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!api) return;
    setError(null);
    if (!file) return setError("Choose a job description file to upload.");
    const problem = checkJDFile(file, maxBytes);
    if (problem) return setError(problem);
    setBusy(true);
    try {
      const r = await uploadJobDescription(api, file);
      if (r.kind !== "ok") return setError(`Upload rejected: ${r.message}`);
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      await refreshList();
      await select(r.data.id);
    } finally {
      setBusy(false);
    }
  }

  async function onRetry() {
    if (!api || !detail) return;
    const r = await reparseJobDescription(api, detail.id);
    if (r.kind !== "ok") return setError(`Could not restart parsing: ${r.message}`);
    setParsed(null);
    setDetail({ ...detail, parse_status: "pending", parse_error_code: null, parse_error: null });
    setPollKey((k) => k + 1);
  }

  async function onDelete() {
    if (!api || !detail) return;
    const r = await deleteJobDescription(api, detail.id);
    if (r.kind !== "ok") return setError(`Could not delete: ${r.message}`);
    setDetail(null);
    setParsed(null);
    await refreshList();
  }

  async function onSave(data: ParsedJobDescription) {
    if (!api || !detail) return;
    setBusy(true);
    try {
      const r = await saveReview(api, detail.id, data);
      if (r.kind !== "ok") return setError(`Could not save corrections: ${r.message}`);
      setParsed(r.data);
      setEditing(false);
      await select(detail.id);
      await refreshList();
    } finally {
      setBusy(false);
    }
  }

  async function onDiscard() {
    if (!api || !detail) return;
    const r = await discardReview(api, detail.id);
    if (r.kind !== "ok") return setError(`Could not restore the extraction: ${r.message}`);
    await select(detail.id);
    await refreshList();
  }

  const btn = "rounded-md border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800";

  return (
    <div className="grid gap-6">
      {llmMissing && (
        <div role="note" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          The LLM is not configured on the server (GROQ_API_KEY), so job descriptions will be extracted but not
          structured. Add the key, restart the backend, then use “Retry parsing”.
        </div>
      )}

      <form onSubmit={onUpload} noValidate aria-label="Upload job description" className="flex flex-wrap items-end gap-3 rounded-xl border border-zinc-200 bg-white p-5 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
        <div className="grid flex-1 gap-1 text-sm">
          <label htmlFor="jd-file" className="font-medium">
            Job description file
          </label>
          <input
            id="jd-file"
            ref={fileInput}
            type="file"
            aria-describedby="jd-file-hint"
            accept=".pdf,.docx,.txt,application/pdf,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="text-sm file:mr-3 file:rounded-md file:border-0 file:bg-zinc-100 file:px-3 file:py-2 dark:file:bg-zinc-800"
          />
          <span id="jd-file-hint" className="text-zinc-500">
            PDF, DOCX or TXT, up to {Math.floor(maxBytes / (1024 * 1024))} MB. Scanned PDFs are read with OCR.
          </span>
        </div>
        <button type="submit" disabled={busy} className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-60">
          {busy ? "Uploading…" : "Upload and parse"}
        </button>
      </form>

      {error && (
        <p role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
        <section aria-labelledby="jd-list-heading">
          <h2 id="jd-list-heading" className="text-sm font-semibold">
            Job descriptions ({items.length})
          </h2>
          {items.length === 0 ? (
            <p className="mt-2 text-sm text-zinc-500">None uploaded yet.</p>
          ) : (
            <ul className="mt-2 grid gap-1">
              {items.map((jd) => (
                <li key={jd.id}>
                  <button
                    type="button"
                    onClick={() => void select(jd.id)}
                    aria-current={detail?.id === jd.id ? "true" : undefined}
                    className="w-full rounded-md px-3 py-2 text-left text-sm hover:bg-zinc-100 aria-[current=true]:bg-indigo-50 dark:hover:bg-zinc-800 dark:aria-[current=true]:bg-indigo-950"
                  >
                    <span className="block font-medium break-all">{jd.title ?? jd.original_filename ?? "Untitled"}</span>
                    <span className="block text-xs text-zinc-500">
                      {jd.parse_status}
                      {jd.has_review ? " · reviewed" : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {detail ? (
          <section aria-labelledby="jd-detail-heading" className="grid content-start gap-4">
            <div className="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
              <div>
                <h2 id="jd-detail-heading" className="font-semibold break-all">
                  {detail.title ?? detail.original_filename ?? "Job description"}
                </h2>
                <p data-testid="jd-status" aria-live="polite" className="mt-1 text-sm">
                  {STATUS_TEXT[detail.parse_status]}
                  {parsed?.source === "reviewed" && " · using your corrections"}
                </p>
                <p className="mt-1 text-xs text-zinc-500">
                  {[
                    detail.original_filename,
                    detail.extraction_method,
                    detail.text_char_count !== null ? `${detail.text_char_count.toLocaleString()} characters` : null,
                    detail.parser_model ? `model: ${detail.parser_model}` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {detail.original_filename && (
                  <a href={jobDescriptionFileUrl(api, detail.id)} className={btn}>
                    Download
                  </a>
                )}
                {parsed && !editing && (
                  <button type="button" onClick={() => setEditing(true)} className={btn}>
                    Review & correct
                  </button>
                )}
                {parsed?.source === "reviewed" && !editing && (
                  <button type="button" onClick={() => void onDiscard()} className={btn}>
                    Restore extraction
                  </button>
                )}
                {(detail.parse_status === "failed" || detail.parse_status === "completed") && (
                  <button type="button" onClick={() => void onRetry()} className={btn}>
                    Retry parsing
                  </button>
                )}
                <button type="button" onClick={() => void onDelete()} className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-950">
                  Delete
                </button>
              </div>
            </div>

            {detail.parse_status === "failed" && (
              <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
                <p className="font-medium">{detail.parse_error_code ?? "error"}</p>
                <p>{detail.parse_error ?? "Parsing failed."}</p>
              </div>
            )}

            {(parsed?.warnings ?? detail.parse_warnings).length > 0 && (
              <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
                <p className="font-medium">Extraction warnings</p>
                <ul className="list-disc pl-5">
                  {(parsed?.warnings ?? detail.parse_warnings).map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </div>
            )}

            {parsed && editing && (
              <JDReviewEditor initial={parsed.data} saving={busy} onSave={(d) => void onSave(d)} onCancel={() => setEditing(false)} />
            )}
            {parsed && !editing && <JDView data={parsed.data} tech={parsed.technology_summary} />}
          </section>
        ) : (
          <p className="text-sm text-zinc-500">Upload a job description or select one from the list.</p>
        )}
      </div>
    </div>
  );
}
