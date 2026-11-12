import type { Metadata } from "next";

import JobDescriptionWorkspace from "@/components/job-description/JobDescriptionWorkspace";
import { API_BASE_URL } from "@/lib/config";

export const metadata: Metadata = { title: "Job descriptions" };

export default function JobDescriptionsPage() {
  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-10 sm:px-6">
      <header className="max-w-3xl">
        <h1 className="text-2xl font-bold tracking-tight">Job descriptions</h1>
        <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
          Upload a job description to extract its requirements. Required and preferred skills are kept separate, every
          requirement is backed by a quote from the original text, and nothing that is not stated is added. Review and
          correct the result before it is used for evaluation.
        </p>
      </header>
      <JobDescriptionWorkspace apiBaseUrl={API_BASE_URL} />
    </main>
  );
}
