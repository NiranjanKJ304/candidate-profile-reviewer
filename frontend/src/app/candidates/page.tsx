import type { Metadata } from "next";

import CandidatesWorkspace from "@/components/candidate/CandidatesWorkspace";
import { API_BASE_URL } from "@/lib/config";

export const metadata: Metadata = { title: "Candidates" };

export default function CandidatesPage() {
  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-10 sm:px-6">
      <header className="max-w-3xl">
        <h1 className="text-2xl font-bold tracking-tight">Candidates</h1>
        <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
          Link a candidate&apos;s developer profiles to collect public evidence through official APIs: GitHub (profile,
          repositories, languages, topics, recent public activity) and Codeforces (rating, contest history and recent
          submissions). LeetCode has no permitted API, so only manually entered evidence is stored for it, labelled
          self-reported and not verified. Missing or unavailable data is shown as such and is never treated as missing
          skills.
        </p>
      </header>
      <CandidatesWorkspace apiBaseUrl={API_BASE_URL} />
    </main>
  );
}
