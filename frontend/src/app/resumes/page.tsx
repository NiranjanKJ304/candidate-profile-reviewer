import type { Metadata } from "next";

import ResumeWorkspace from "@/components/resume/ResumeWorkspace";
import { API_BASE_URL } from "@/lib/config";

export const metadata: Metadata = { title: "Resume parsing" };

export default function ResumesPage() {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-4 py-10 sm:px-6">
      <header className="max-w-3xl">
        <h1 className="text-2xl font-bold tracking-tight">Resume parsing</h1>
        <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
          Upload a candidate&apos;s resume. The text is extracted (with OCR for scanned PDFs) and structured by the
          configured language model. Only information present in the resume is kept; anything not stated is shown
          as &ldquo;not found&rdquo;.
        </p>
      </header>
      <ResumeWorkspace apiBaseUrl={API_BASE_URL} />
    </main>
  );
}
