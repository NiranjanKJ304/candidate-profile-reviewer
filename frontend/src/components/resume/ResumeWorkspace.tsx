"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { createApiClient } from "@/lib/apiClient";
import {
  type Capabilities,
  type ResumeDetail,
  checkFile,
  createCandidate,
  deleteResume,
  getCapabilities,
  getResume,
  reparseResume,
  resumeFileUrl,
  uploadResume,
} from "@/lib/resumes";

import CodeforcesSection from "@/components/codeforces/CodeforcesSection";
import GitHubSection from "@/components/github/GitHubSection";
import LeetCodeSection from "@/components/leetcode/LeetCodeSection";

import ParsedResumeView from "./ParsedResumeView";

const DEFAULT_MAX_BYTES = 10 * 1024 * 1024;
const MAX_POLLS = 200;

const STATUS_TEXT: Record<ResumeDetail["parse_status"], string> = {
  pending: "Queued for parsing…",
  processing: "Extracting text and structuring the resume…",
  completed: "Parsing complete",
  failed: "Parsing failed",
};

const METHOD_TEXT: Record<string, string> = {
  pdf_text: "PDF text layer",
  pdf_ocr: "PDF with OCR (scanned pages)",
  docx: "Word document",
};

interface Props {
  /** Validated backend base URL (a plain string, so it can cross the server/client boundary). */
  apiBaseUrl: string | null;
  pollIntervalMs?: number;
}

function CapabilityNotice({ caps }: { caps: Capabilities }) {
  const notes: string[] = [];
  if (!caps.llm.configured) {
    notes.push(
      "The LLM is not configured on the server (GROQ_API_KEY), so text will be extracted but not structured. " +
        "Add the key, restart the backend, then use “Retry parsing”.",
    );
  }
  if (!caps.ocr.available) {
    notes.push("OCR is unavailable on the server, so scanned (image-only) PDFs cannot be read.");
  }
  if (notes.length === 0) return null;
  return (
    <div role="note" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
      {notes.map((n) => (
        <p key={n}>{n}</p>
      ))}
    </div>
  );
}

export default function ResumeWorkspace({ apiBaseUrl, pollIntervalMs = 1500 }: Props) {
  const api = useMemo(() => (apiBaseUrl ? createApiClient(apiBaseUrl) : null), [apiBaseUrl]);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resume, setResume] = useState<ResumeDetail | null>(null);
  const [pollKey, setPollKey] = useState(0);
  const [pollTimedOut, setPollTimedOut] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!api) return;
    let cancelled = false;
    void getCapabilities(api).then((r) => {
      if (!cancelled && r.kind === "ok") setCaps(r.data);
    });
    return () => {
      cancelled = true;
    };
  }, [api]);

  const resumeId = resume?.id ?? null;
  const status = resume?.parse_status ?? null;

  // Poll while the resume is queued or processing.
  useEffect(() => {
    if (!api || !resumeId || (status !== "pending" && status !== "processing")) return;
    let cancelled = false;
    let polls = 0;
    const tick = async () => {
      if (cancelled) return;
      polls += 1;
      const r = await getResume(api, resumeId);
      if (cancelled) return;
      if (r.kind === "ok") {
        setResume(r.data);
        if (r.data.parse_status === "completed" || r.data.parse_status === "failed") return;
      }
      if (polls >= MAX_POLLS) {
        setPollTimedOut(true);
        return;
      }
      timer = setTimeout(tick, pollIntervalMs);
    };
    let timer = setTimeout(tick, pollIntervalMs);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [api, resumeId, status, pollKey, pollIntervalMs]);

  const reset = useCallback(() => {
    setResume(null);
    setFile(null);
    setError(null);
    setPollTimedOut(false);
    if (fileInput.current) fileInput.current.value = "";
  }, []);

  if (!api) {
    return (
      <p role="alert" className="text-sm text-red-700 dark:text-red-400">
        Configuration error: NEXT_PUBLIC_API_BASE_URL is not a valid http(s) URL.
      </p>
    );
  }

  const maxBytes = caps?.resume.max_bytes ?? DEFAULT_MAX_BYTES;

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!api) return;
    setError(null);
    if (!fullName.trim()) return setError("Enter the candidate's full name.");
    if (!file) return setError("Choose a resume file to upload.");
    const fileProblem = checkFile(file, maxBytes);
    if (fileProblem) return setError(fileProblem);

    setBusy(true);
    try {
      const candidate = await createCandidate(api, { full_name: fullName.trim(), email: email.trim() || null });
      if (candidate.kind !== "ok") return setError(`Could not create the candidate: ${candidate.message}`);
      const uploaded = await uploadResume(api, candidate.data.id, file);
      if (uploaded.kind !== "ok") return setError(`Upload rejected: ${uploaded.message}`);
      const detail = await getResume(api, uploaded.data.id);
      setPollTimedOut(false);
      setResume(
        detail.kind === "ok"
          ? detail.data
          : { ...uploaded.data, parsed_data: null, parse_warnings: [], parser_model: null, text_char_count: null },
      );
    } finally {
      setBusy(false);
    }
  }

  async function onRetry() {
    if (!api || !resume) return;
    setError(null);
    const r = await reparseResume(api, resume.id);
    if (r.kind !== "ok") return setError(`Could not restart parsing: ${r.message}`);
    setPollTimedOut(false);
    setResume({ ...resume, ...r.data });
    setPollKey((k) => k + 1);
  }

  async function onDelete() {
    if (!api || !resume) return;
    setError(null);
    const r = await deleteResume(api, resume.id);
    if (r.kind !== "ok") return setError(`Could not delete the resume: ${r.message}`);
    reset();
  }

  return (
    <div className="grid gap-6">
      {caps && <CapabilityNotice caps={caps} />}

      {!resume && (
        <form onSubmit={onSubmit} noValidate className="grid gap-4 rounded-xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900" aria-label="Upload resume">
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="grid gap-1 text-sm font-medium">
              Candidate full name
              <input
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                required
                maxLength={200}
                autoComplete="off"
                className="rounded-md border border-zinc-300 px-3 py-2 font-normal dark:border-zinc-700 dark:bg-zinc-950"
              />
            </label>
            <label className="grid gap-1 text-sm font-medium">
              Email <span className="font-normal text-zinc-500">(optional)</span>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                maxLength={320}
                autoComplete="off"
                className="rounded-md border border-zinc-300 px-3 py-2 font-normal dark:border-zinc-700 dark:bg-zinc-950"
              />
            </label>
          </div>
          <div className="grid gap-1 text-sm">
            <label htmlFor="resume-file" className="font-medium">
              Resume file
            </label>
            <input
              id="resume-file"
              ref={fileInput}
              type="file"
              aria-describedby="resume-file-hint"
              accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="text-sm file:mr-3 file:rounded-md file:border-0 file:bg-zinc-100 file:px-3 file:py-2 dark:file:bg-zinc-800"
            />
            <span id="resume-file-hint" className="text-zinc-500">
              PDF or DOCX, up to {Math.floor(maxBytes / (1024 * 1024))} MB. Scanned PDFs are read with OCR.
            </span>
          </div>
          <div>
            <button
              type="submit"
              disabled={busy}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busy ? "Uploading…" : "Upload and parse"}
            </button>
          </div>
        </form>
      )}

      {error && (
        <p role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </p>
      )}

      {resume && (
        <section aria-labelledby="result-heading" className="grid gap-4">
          <div className="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
            <div>
              <h2 id="result-heading" className="font-semibold break-all">
                {resume.original_filename}
              </h2>
              <p data-testid="parse-status" aria-live="polite" className="mt-1 text-sm">
                <span
                  className={
                    resume.parse_status === "completed"
                      ? "text-emerald-700 dark:text-emerald-400"
                      : resume.parse_status === "failed"
                        ? "text-red-700 dark:text-red-400"
                        : "text-zinc-600 dark:text-zinc-400"
                  }
                >
                  {STATUS_TEXT[resume.parse_status]}
                </span>
              </p>
              <p className="mt-1 text-xs text-zinc-500">
                {[
                  resume.extraction_method ? METHOD_TEXT[resume.extraction_method] : null,
                  resume.page_count ? `${resume.page_count} page(s)` : null,
                  resume.text_char_count !== null ? `${resume.text_char_count.toLocaleString()} characters extracted` : null,
                  resume.parser_model ? `model: ${resume.parser_model}` : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              {pollTimedOut && (
                <p className="mt-1 text-xs text-amber-700">Still processing; refresh the page later to check again.</p>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              <a
                href={resumeFileUrl(api, resume.id)}
                className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
              >
                Download original
              </a>
              {resume.parse_status === "failed" && (
                <button type="button" onClick={() => void onRetry()} className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800">
                  Retry parsing
                </button>
              )}
              <button type="button" onClick={() => void onDelete()} className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-950">
                Delete
              </button>
              <button type="button" onClick={reset} className="rounded-md px-3 py-1.5 text-sm text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800">
                Upload another
              </button>
            </div>
          </div>

          {resume.parse_status === "failed" && (
            <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
              <p className="font-medium">{resume.parse_error_code ?? "error"}</p>
              <p>{resume.parse_error ?? "Parsing failed."}</p>
            </div>
          )}

          {resume.parse_warnings.length > 0 && (
            <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
              <p className="font-medium">Warnings</p>
              <ul className="list-disc pl-5">
                {resume.parse_warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </div>
          )}

          {resume.parsed_data && <ParsedResumeView data={resume.parsed_data} />}

          <GitHubSection api={api} candidateId={resume.candidate_id} pollIntervalMs={pollIntervalMs} />
          <CodeforcesSection api={api} candidateId={resume.candidate_id} pollIntervalMs={pollIntervalMs} />
          <LeetCodeSection api={api} candidateId={resume.candidate_id} />
        </section>
      )}
    </div>
  );
}
