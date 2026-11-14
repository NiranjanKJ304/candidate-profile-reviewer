import Link from "next/link";

import { API_BASE_URL } from "@/lib/config";

export default function SiteHeader() {
  return (
    <header className="border-b border-zinc-200 bg-white/80 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/80">
      <div className="mx-auto flex h-14 w-full max-w-5xl items-center justify-between gap-4 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <span
            aria-hidden
            className="grid h-7 w-7 place-items-center rounded-md bg-indigo-600 text-xs font-bold text-white"
          >
            CI
          </span>
          <span>Candidate Intelligence</span>
        </Link>
        <nav aria-label="Main" className="flex items-center gap-4 text-sm text-zinc-600 dark:text-zinc-400">
          <Link href="/" className="hover:text-zinc-900 dark:hover:text-zinc-100">
            Home
          </Link>
          <Link href="/resumes" className="hover:text-zinc-900 dark:hover:text-zinc-100">
            Resumes
          </Link>
          <Link href="/job-descriptions" className="hover:text-zinc-900 dark:hover:text-zinc-100">
            Job descriptions
          </Link>
          <Link href="/candidates" className="hover:text-zinc-900 dark:hover:text-zinc-100">
            Candidates
          </Link>
          <Link href="/evaluations" className="hover:text-zinc-900 dark:hover:text-zinc-100">
            Evaluate
          </Link>
          {API_BASE_URL && (
            <a
              href={`${API_BASE_URL}/docs`}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-zinc-900 dark:hover:text-zinc-100"
            >
              API docs
            </a>
          )}
        </nav>
      </div>
    </header>
  );
}
