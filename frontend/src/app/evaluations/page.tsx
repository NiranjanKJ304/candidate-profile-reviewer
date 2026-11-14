import type { Metadata } from "next";

import EvaluationWorkspace from "@/components/evaluation/EvaluationWorkspace";
import { API_BASE_URL } from "@/lib/config";

export const metadata: Metadata = { title: "Evaluations" };

export default function EvaluationsPage() {
  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-10 sm:px-6">
      <header className="max-w-3xl">
        <h1 className="text-2xl font-bold tracking-tight">Evaluate a candidate</h1>
        <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
          Compare a candidate&apos;s evidence (resume, GitHub, Codeforces, self-reported LeetCode) with a job description.
          Each requirement gets a verdict that cites its evidence; the score is calculated from those verdicts. Missing
          evidence is shown as such and is never treated as a failed requirement. This supports a recruiter&apos;s decision;
          it does not make it.
        </p>
      </header>
      <EvaluationWorkspace apiBaseUrl={API_BASE_URL} />
    </main>
  );
}
