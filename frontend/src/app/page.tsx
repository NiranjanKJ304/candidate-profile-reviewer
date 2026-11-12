import Link from "next/link";

import StatusPanel from "@/components/StatusPanel";
import { API_BASE_URL } from "@/lib/config";

const CAPABILITIES: { title: string; description: string; href?: string }[] = [
  {
    title: "Resume analysis",
    description: "Structured extraction from PDF and DOCX resumes, with OCR for scanned PDFs.",
    href: "/resumes",
  },
  {
    title: "Developer profile evidence",
    description: "GitHub, LeetCode, HackerRank, Codeforces, CodeChef and Kaggle, via permitted sources only.",
  },
  {
    title: "Job description analysis",
    description: "Required vs preferred skills, experience, education and responsibilities, each backed by a quote.",
    href: "/job-descriptions",
  },
  {
    title: "Evidence-based evaluation",
    description: "Supported claims, discrepancies, missing evidence and skill gaps, with provenance.",
  },
  { title: "Interview questions", description: "Technical questions targeted at gaps and unverified claims." },
];

const EVALUATION_STEPS: { title: string; description: string }[] = [
  { title: "Collect inputs", description: "Resume, developer profile links and the job description." },
  {
    title: "Gather evidence",
    description: "Public, permitted platform data, each fact stored with its source and time.",
  },
  { title: "Compare", description: "Candidate evidence is matched against the job's requirements." },
  {
    title: "Report",
    description: "Supported claims, discrepancies, missing evidence, gaps and interview questions.",
  },
];

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-12 px-4 py-12 sm:px-6 lg:py-16">
      <header className="max-w-3xl">
        <p className="text-sm font-semibold uppercase tracking-wide text-indigo-600 dark:text-indigo-400">
          Candidate evaluation · Phase 0 foundation
        </p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight sm:text-4xl">Candidate Intelligence</h1>
        <p className="mt-4 text-base leading-7 text-zinc-600 dark:text-zinc-400">
          An AI-assisted recruitment evaluation system that compares a candidate&apos;s resume and public developer
          profiles against a job description, and reports only what the evidence supports.
        </p>
      </header>

      <section aria-labelledby="how-heading">
        <h2 id="how-heading" className="text-lg font-semibold">
          How an evaluation works
        </h2>
        <ol className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {EVALUATION_STEPS.map((step, index) => (
            <li
              key={step.title}
              className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
            >
              <span className="text-xs font-semibold text-indigo-600 dark:text-indigo-400">Step {index + 1}</span>
              <h3 className="mt-1 text-sm font-semibold">{step.title}</h3>
              <p className="mt-1.5 text-sm text-zinc-600 dark:text-zinc-400">{step.description}</p>
            </li>
          ))}
        </ol>
      </section>

      <div className="grid gap-8 lg:grid-cols-[1fr_22rem]">
        <section aria-labelledby="capabilities-heading">
          <h2 id="capabilities-heading" className="text-lg font-semibold">
            Capabilities
          </h2>
          <p className="mt-1 text-sm text-zinc-500">
            Available features are linked. Items marked &ldquo;Planned&rdquo; arrive in later phases and are not
            active yet.
          </p>
          <ul className="mt-4 grid gap-3 sm:grid-cols-2">
            {CAPABILITIES.map((item) => (
              <li
                key={item.title}
                className={
                  item.href
                    ? "rounded-lg border border-emerald-300 p-4 dark:border-emerald-800"
                    : "rounded-lg border border-dashed border-zinc-300 p-4 dark:border-zinc-700"
                }
              >
                <div className="flex items-start justify-between gap-2">
                  <h3 className="text-sm font-semibold">
                    {item.href ? (
                      <Link href={item.href} className="underline-offset-2 hover:underline">
                        {item.title}
                      </Link>
                    ) : (
                      item.title
                    )}
                  </h3>
                  {item.href ? (
                    <span className="shrink-0 rounded-full bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
                      Available
                    </span>
                  ) : (
                    <span className="shrink-0 rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
                      Planned
                    </span>
                  )}
                </div>
                <p className="mt-1.5 text-sm text-zinc-600 dark:text-zinc-400">{item.description}</p>
              </li>
            ))}
          </ul>
        </section>

        <StatusPanel apiBaseUrl={API_BASE_URL} />
      </div>
    </main>
  );
}
